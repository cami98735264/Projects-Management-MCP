"""Activity-on-node drawing in the reference notation (six-cell node, grey critical nodes, double critical arcs).

Layout (layered / Sugiyama style, fully deterministic):

1. **Layers.** Column = longest number of arcs from a start activity.
2. **Virtual nodes.** An arc that spans several columns is split into a chain through invisible "virtual" nodes,
   one per intermediate column, so it is routed through the free space *between* nodes instead of being drawn
   as a long diagonal that cuts across other nodes and arcs.
3. **Ordering (crossing minimisation).** The vertical order inside every column is chosen to minimise the number
   of arc crossings. Small networks (the usual exercise size) are solved *exactly* by enumerating every
   combination of per-column orders; larger ones use barycenter sweeps plus adjacent transpositions, keeping
   the best order seen. Ties are broken by a straightness score that favours keeping critical arcs horizontal.
4. **Vertical coordinates.** Each node is pulled towards the mean height of its neighbours (critical arcs weigh
   more, so the critical path tends to become a straight line) while preserving the order and a minimum gap,
   solved per column with weighted isotonic regression (pool-adjacent-violators).
5. **Ports.** Several arcs leaving / entering the same node use separate, vertically ordered anchor points, so
   arrows do not pile up on one pixel and do not cross next to the node.

Rendering uses matplotlib's object API with the Agg canvas only (no global pyplot state), and the PNG carries no
timestamp, so equal inputs give equal bytes.
"""

from __future__ import annotations

import io
import itertools
import math
import struct
from dataclasses import dataclass, field

from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.patches import FancyArrowPatch, Rectangle
from matplotlib.path import Path

from pm_mcp.domain.models import ReportLanguage
from pm_mcp.domain.numbers import number_text
from pm_mcp.engine.cpm import CpmResult

NODE_W, NODE_H = 1.6, 1.6
GAP_X, GAP_Y = 1.4, 0.6
PITCH = NODE_H + GAP_Y          # vertical distance between two stacked real nodes (layout units)
VIRTUAL_GAP = 0.45              # minimum gap (in pitches) when a virtual node is involved
CRITICAL_FILL = "#BFBFBF"
NORMAL_FILL = "#FFFFFF"
EXHAUSTIVE_LIMIT = 40_000       # max number of order combinations evaluated exactly
CRITICAL_WEIGHT = 12.0
VIRTUAL_WEIGHT = 2.0


@dataclass
class NetworkLayout:
    """Result of :func:`compute_layout`."""

    layer: dict[str, int]                       # every node (real and virtual) -> column
    order: list[list[str]]                      # per column, top-to-bottom node ids (real and virtual)
    y: dict[str, float]                         # node -> vertical position in pitches (0 = top)
    chains: dict[tuple[str, str], list[str]]    # real arc (u, v) -> [u, virtual..., v]
    critical_arcs: set[tuple[str, str]] = field(default_factory=set)
    crossings: int = 0

    def is_virtual(self, node: str) -> bool:
        return node.startswith("\x00")


# ---------------------------------------------------------------------------------------------- layering


def _levels(schedule: CpmResult) -> dict[str, int]:
    levels: dict[str, int] = {}
    for row in schedule.activities:  # topological order
        levels[row.activity_id] = 1 + max((levels[p] for p in row.predecessors), default=-1)
    return levels


def _critical_arcs(schedule: CpmResult) -> set[tuple[str, str]]:
    rows = schedule.by_id()
    arcs: set[tuple[str, str]] = set()
    for r in schedule.activities:
        for s in r.successors:
            succ = rows[s]
            if r.is_critical and succ.is_critical and r.early_finish == succ.early_start:
                arcs.add((r.activity_id, s))
    return arcs


# ---------------------------------------------------------------------------------------------- crossings


def _pair_crossings(upper: list[str], lower: list[str], edges: list[tuple[str, str]]) -> int:
    pu = {n: i for i, n in enumerate(upper)}
    pl = {n: i for i, n in enumerate(lower)}
    seg = sorted((pu[a], pl[b]) for a, b in edges)
    count = 0
    for i in range(len(seg)):
        a1, b1 = seg[i]
        for j in range(i + 1, len(seg)):
            a2, b2 = seg[j]
            if a2 > a1 and b2 < b1:
                count += 1
    return count


def _total_crossings(order: list[list[str]], layer_edges: list[list[tuple[str, str]]]) -> int:
    return sum(_pair_crossings(order[i], order[i + 1], layer_edges[i]) for i in range(len(order) - 1))


def _straightness(order: list[list[str]], layer_edges: list[list[tuple[str, str]]],
                  weights: dict[tuple[str, str], float]) -> float:
    """Σ w·(centred position difference)²: lower means flatter arcs (critical arcs weigh more)."""
    total = 0.0
    for i in range(len(order) - 1):
        cu = (len(order[i]) - 1) / 2
        cl = (len(order[i + 1]) - 1) / 2
        pu = {n: k - cu for k, n in enumerate(order[i])}
        pl = {n: k - cl for k, n in enumerate(order[i + 1])}
        for a, b in layer_edges[i]:
            total += weights.get((a, b), 1.0) * (pu[a] - pl[b]) ** 2
    return total


# ---------------------------------------------------------------------------------------------- ordering


def _barycenter_sweeps(order: list[list[str]], layer_edges: list[list[tuple[str, str]]],
                       weights: dict[tuple[str, str], float], iterations: int = 24) -> list[list[str]]:
    preds: dict[str, list[str]] = {}
    succs: dict[str, list[str]] = {}
    for edges in layer_edges:
        for a, b in edges:
            succs.setdefault(a, []).append(b)
            preds.setdefault(b, []).append(a)

    def key(order_now):
        return (_total_crossings(order_now, layer_edges), _straightness(order_now, layer_edges, weights))

    best = [list(col) for col in order]
    best_key = key(best)
    current = [list(col) for col in order]
    for it in range(iterations):
        downward = it % 2 == 0
        rng = range(1, len(current)) if downward else range(len(current) - 2, -1, -1)
        for i in rng:
            ref = current[i - 1] if downward else current[i + 1]
            pos = {n: k for k, n in enumerate(ref)}
            neigh = preds if downward else succs
            old = {n: k for k, n in enumerate(current[i])}

            def bary(n, pos=pos, neigh=neigh, old=old):
                ns = [pos[m] for m in neigh.get(n, []) if m in pos]
                return sum(ns) / len(ns) if ns else old[n]
            current[i].sort(key=lambda n: (bary(n), old[n]))
        # adjacent transpositions while they reduce crossings
        improved = True
        while improved:
            improved = False
            for i, col in enumerate(current):
                for k in range(len(col) - 1):
                    before = key(current)
                    col[k], col[k + 1] = col[k + 1], col[k]
                    if key(current) < before:
                        improved = True
                    else:
                        col[k], col[k + 1] = col[k + 1], col[k]
        k_now = key(current)
        if k_now < best_key:
            best, best_key = [list(c) for c in current], k_now
    return best


def _exhaustive(order: list[list[str]], layer_edges: list[list[tuple[str, str]]],
                weights: dict[tuple[str, str], float]) -> list[list[str]]:
    # Column 0 permutations are only needed if it has >1 node; fixing nothing keeps the search exact.
    options = [list(itertools.permutations(col)) for col in order]
    best: list[list[str]] | None = None
    best_key: tuple[int, float] | None = None
    for combo in itertools.product(*options):
        cand = [list(c) for c in combo]
        crossings = _total_crossings(cand, layer_edges)
        if best_key is not None and crossings > best_key[0]:
            continue
        k = (crossings, round(_straightness(cand, layer_edges, weights), 9))
        if best_key is None or k < best_key:
            best, best_key = cand, k
    assert best is not None
    return best


# ---------------------------------------------------------------------------------------------- coordinates


def _pav(targets: list[float], weights: list[float]) -> list[float]:
    """Weighted isotonic (non-decreasing) regression by pool-adjacent-violators."""
    blocks: list[list[float]] = []  # [value, weight, count]
    for t, w in zip(targets, weights):
        blocks.append([t, w, 1])
        while len(blocks) > 1 and blocks[-2][0] > blocks[-1][0]:
            v2, w2, c2 = blocks.pop()
            v1, w1, c1 = blocks.pop()
            wt = w1 + w2
            blocks.append([(v1 * w1 + v2 * w2) / wt, wt, c1 + c2])
    out: list[float] = []
    for v, _, c in blocks:
        out.extend([v] * c)
    return out


def _assign_y(lay: NetworkLayout, weights: dict[tuple[str, str], float], spine: list[str]) -> None:
    """Vertical positions. ``spine`` (one critical path, with its virtual nodes) is pinned to one height so the
    critical path is drawn as a straight horizontal line; every other node settles around it."""
    pinned = set(spine)
    neigh: dict[str, list[tuple[str, float]]] = {}
    for chain in lay.chains.values():
        for a, b in zip(chain, chain[1:]):
            w = weights.get((a, b), 1.0)
            neigh.setdefault(a, []).append((b, w))
            neigh.setdefault(b, []).append((a, w))

    def gap(a: str, b: str) -> float:
        return 1.0 if not (lay.is_virtual(a) or lay.is_virtual(b)) else VIRTUAL_GAP

    offsets: list[list[float]] = []
    for col in lay.order:
        acc, offs = 0.0, []
        for k, n in enumerate(col):
            if k:
                acc += gap(col[k - 1], n)
            offs.append(acc)
        offsets.append(offs)
        for n, o in zip(col, offs):
            lay.y[n] = o - acc / 2  # centred start

    sweep = list(range(len(lay.order))) + list(range(len(lay.order) - 2, 0, -1))
    for _ in range(30):
        for i in sweep:
            col, offs = lay.order[i], offsets[i]
            targets, ws = [], []
            for n, o in zip(col, offs):
                ns = neigh.get(n, [])
                if n in pinned:
                    desired, tw = 0.0, 1e6
                elif ns:
                    tw = sum(w for _, w in ns)
                    desired = sum(lay.y[m] * w for m, w in ns) / tw
                else:
                    desired, tw = lay.y[n], 0.5
                targets.append(desired - o)
                ws.append(tw)
            z = _pav(targets, ws)
            for n, o, zz in zip(col, offs, z):
                lay.y[n] = zz + o
    if pinned:  # the 1e6 pin weight leaves ~1e-6 noise; make the spine exactly level
        level = sum(lay.y[n] for n in pinned) / len(pinned)
        for n in pinned:
            lay.y[n] = level
    low = min(lay.y.values())
    for n in lay.y:
        lay.y[n] = round(lay.y[n] - low, 4)


# ---------------------------------------------------------------------------------------------- public API


def compute_layout(schedule: CpmResult) -> NetworkLayout:
    levels = _levels(schedule)
    critical = _critical_arcs(schedule)
    n_layers = max(levels.values()) + 1
    order: list[list[str]] = [[] for _ in range(n_layers)]
    lay = NetworkLayout(layer=dict(levels), order=order, y={}, chains={}, critical_arcs=critical)
    for r in schedule.activities:
        order[levels[r.activity_id]].append(r.activity_id)
    layer_edges: list[list[tuple[str, str]]] = [[] for _ in range(max(n_layers - 1, 0))]
    weights: dict[tuple[str, str], float] = {}
    for r in schedule.activities:
        for s in r.successors:
            u, v = r.activity_id, s
            chain = [u]
            for L in range(levels[u] + 1, levels[v]):
                vid = f"\x00{u}>{v}#{L}"
                lay.layer[vid] = L
                order[L].append(vid)
                chain.append(vid)
            chain.append(v)
            lay.chains[(u, v)] = chain
            w = CRITICAL_WEIGHT if (u, v) in critical else (VIRTUAL_WEIGHT if len(chain) > 2 else 1.0)
            for a, b in zip(chain, chain[1:]):
                layer_edges[lay.layer[a]].append((a, b))
                weights[(a, b)] = w

    combos = 1
    for col in order:
        combos *= math.factorial(len(col))
        if combos > EXHAUSTIVE_LIMIT:
            break
    if combos <= EXHAUSTIVE_LIMIT:
        best = _exhaustive(order, layer_edges, weights)
    else:
        best = _barycenter_sweeps(order, layer_edges, weights)
    lay.order[:] = best
    lay.crossings = _total_crossings(best, layer_edges)
    _assign_y(lay, weights, _spine(schedule, lay))
    return lay


def _spine(schedule: CpmResult, lay: NetworkLayout) -> list[str]:
    """Nodes of the first critical path plus the virtual nodes of its arcs, if they can all share one height
    (i.e. the path never visits the same column twice, which holds for any path in a layered DAG)."""
    if not schedule.critical_paths:
        return []
    path = [a for a in schedule.critical_paths[0] if a in lay.layer]
    nodes = list(path)
    for u, v in zip(path, path[1:]):
        nodes.extend(lay.chains.get((u, v), [u, v])[1:-1])
    columns = [lay.layer[n] for n in nodes]
    return nodes if len(columns) == len(set(columns)) else []


def count_crossings(schedule: CpmResult) -> int:
    """Number of arc crossings in the drawn network (virtual nodes included)."""
    return compute_layout(schedule).crossings


def layout(schedule: CpmResult) -> dict[str, tuple[int, int]]:
    """activity id → (column, row) grid position mirroring the drawing (used by the workbook's node cards)."""
    lay = compute_layout(schedule)
    return {r.activity_id: (lay.layer[r.activity_id], math.floor(lay.y[r.activity_id] + 0.5))
            for r in schedule.activities}


def png_size(data: bytes) -> tuple[int, int]:
    """(width, height) in pixels from a PNG header."""
    width, height = struct.unpack(">II", data[16:24])
    return width, height


def _ports(count: int, centre_index: int | None = None) -> list[float]:
    """Vertical offsets (from the node centre, top first) for `count` arcs on one side of a node.

    With ``centre_index`` (the critical arc) that arc keeps the exact centre, so a critical path between
    aligned nodes stays perfectly horizontal; the others spread above / below it in order.
    """
    half = NODE_H * 0.55 / 2
    if count <= 1:
        return [0.0]
    if centre_index is None:
        return [half - k * 2 * half / (count - 1) for k in range(count)]
    above, below = centre_index, count - 1 - centre_index
    offsets = []
    for k in range(count):
        if k < centre_index:
            offsets.append(half * (centre_index - k) / above)
        elif k == centre_index:
            offsets.append(0.0)
        else:
            offsets.append(-half * (k - centre_index) / below)
    return offsets


def _shorten(verts: list[tuple[float, float]], amount: float) -> list[tuple[float, float]]:
    (x1, y1), (x2, y2) = verts[-2], verts[-1]
    length = math.hypot(x2 - x1, y2 - y1)
    if length <= amount:
        return verts
    f = (length - amount) / length
    return [*verts[:-1], (x1 + (x2 - x1) * f, y1 + (y2 - y1) * f)]


def render_network_png(schedule: CpmResult, language: ReportLanguage | str = ReportLanguage.ES,
                       caption: str | None = None, dpi: int = 100) -> bytes:
    """Draw the scheduled network; each node shows Act | t / IC | TC / IL | TL / Holgura."""
    lay = compute_layout(schedule)
    n_cols = len(lay.order)
    y_max = max(lay.y.values())
    width_units = n_cols * NODE_W + (n_cols - 1) * GAP_X
    graph_h = y_max * PITCH + NODE_H
    cap_h = 0.8 if caption else 0.0
    height_units = graph_h + cap_h
    scale = min(0.9, 60 / width_units, 40 / height_units)  # inches per layout unit, capped for huge networks
    fig = Figure(figsize=(max(4.0, width_units * scale + 0.6), max(2.5, height_units * scale + 0.6)), dpi=dpi)
    FigureCanvasAgg(fig)
    ax = fig.add_axes((0, 0, 1, 1))
    ax.set_xlim(-0.3, width_units + 0.3)
    ax.set_ylim(-0.3, height_units + 0.3)
    ax.set_aspect("equal")
    ax.axis("off")
    slack_label = "Holgura" if str(language) == "es" else "Slack"

    def x_left(node: str) -> float:
        return lay.layer[node] * (NODE_W + GAP_X)

    def y_center(node: str) -> float:
        return (y_max - lay.y[node]) * PITCH + NODE_H / 2

    for r in schedule.activities:
        x = x_left(r.activity_id)
        y = y_center(r.activity_id) - NODE_H / 2
        fill = CRITICAL_FILL if r.is_critical else NORMAL_FILL
        ax.add_patch(Rectangle((x, y), NODE_W, NODE_H, facecolor=fill, edgecolor="black", linewidth=1.2, zorder=3))
        quarter = NODE_H / 4
        for k in (1, 2, 3):
            ax.plot([x, x + NODE_W], [y + k * quarter] * 2, color="black", linewidth=0.8, zorder=4)
        ax.plot([x + NODE_W / 2] * 2, [y + quarter, y + NODE_H], color="black", linewidth=0.8, zorder=4)
        cells = [
            (r.activity_id, number_text(r.duration)),
            (number_text(r.early_start), number_text(r.early_finish)),
            (number_text(r.late_start), number_text(r.late_finish)),
        ]
        for i, (left, right) in enumerate(cells):
            cy = y + NODE_H - (i + 0.5) * quarter
            weight = "bold" if i == 0 else "normal"
            ax.text(x + NODE_W / 4, cy, left, ha="center", va="center", fontsize=9, fontweight=weight, zorder=5)
            ax.text(x + 3 * NODE_W / 4, cy, right, ha="center", va="center", fontsize=9, zorder=5)
        ax.text(x + NODE_W / 2, y + quarter / 2, f"{slack_label} {number_text(r.total_slack)}",
                ha="center", va="center", fontsize=8, zorder=5)

    # anchor points: out-ports ordered by the height of the next hop, in-ports by the previous hop
    out_arcs: dict[str, list[tuple[str, str]]] = {}
    in_arcs: dict[str, list[tuple[str, str]]] = {}
    for (u, v), chain in lay.chains.items():
        out_arcs.setdefault(u, []).append((u, v))
        in_arcs.setdefault(v, []).append((u, v))
    out_port: dict[tuple[str, str], float] = {}
    in_port: dict[tuple[str, str], float] = {}
    for u, arcs in out_arcs.items():
        arcs.sort(key=lambda a: (-y_center(lay.chains[a][1]), a))
        crit = next((i for i, a in enumerate(arcs) if a in lay.critical_arcs), None)
        for arc, off in zip(arcs, _ports(len(arcs), crit)):
            out_port[arc] = y_center(u) + off
    for v, arcs in in_arcs.items():
        arcs.sort(key=lambda a: (-y_center(lay.chains[a][-2]), a))
        crit = next((i for i, a in enumerate(arcs) if a in lay.critical_arcs), None)
        for arc, off in zip(arcs, _ports(len(arcs), crit)):
            in_port[arc] = y_center(v) + off

    for (u, v), chain in sorted(lay.chains.items(), key=lambda kv: ((kv[0] in lay.critical_arcs), kv[0])):
        verts = [(x_left(u) + NODE_W, out_port[(u, v)])]
        for node in chain[1:-1]:
            yc = y_center(node)
            verts.append((x_left(node), yc))
            verts.append((x_left(node) + NODE_W, yc))
        verts.append((x_left(v), in_port[(u, v)]))
        codes = [Path.MOVETO] + [Path.LINETO] * (len(verts) - 1)
        if (u, v) in lay.critical_arcs:  # double arrow: a thick black stroke with a white core
            ax.add_patch(FancyArrowPatch(path=Path(verts, codes), arrowstyle="-|>", mutation_scale=14, linewidth=3.2,
                                         color="black", zorder=2, joinstyle="miter"))
            core = _shorten(verts, 0.32)
            ax.add_patch(FancyArrowPatch(path=Path(core, codes), arrowstyle="-", linewidth=1.0, color="white",
                                         zorder=2.1))
        else:
            ax.add_patch(FancyArrowPatch(path=Path(verts, codes), arrowstyle="-|>", mutation_scale=12, linewidth=1.0,
                                         color="black", zorder=1))

    if caption:
        ax.text(0, height_units + 0.1, caption, ha="left", va="top", fontsize=11, fontweight="bold")

    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", dpi=dpi, metadata={"Software": None}, facecolor="white")
    return buffer.getvalue()
