"""Activity-on-node network drawn with worksheet cells (no pictures).

Each activity is a 2 × 4 block of bordered cells::

    | A  |  t |
    | IC | TC |
    | IL | TL |
    | Holgura |

Arcs are drawn in the narrow "channel" columns between two layers with box-drawing characters (─ │ ┌ ┐ └ ┘ ├ ┤ ┬
┴ ┼) and end in an arrow head (►) next to the successor's block. Critical arcs use double lines (═ ║ ╔ …) in red,
critical nodes are shaded, so the critical path stands out.

Geometry comes from :func:`pm_mcp.output.network_diagram.compute_layout` (layered layout, exact crossing
minimisation, critical path pinned to one height, long arcs routed through virtual nodes). This module turns it
into an integer cell grid:

* rows: one layout pitch = :data:`PITCH_ROWS` rows (4 for the block + a gap); a virtual node is one row;
* ports: the arcs leaving / entering a block use different rows of the block, ordered to avoid crossings and
  keeping the critical arc on the block's second row so the critical path stays one straight line;
* channels: every arc that changes row gets its own vertical track; the track order is chosen to minimise
  crossings and never lets two different arcs share a horizontal run.

Everything is deterministic (no randomness, stable tie-breaks).
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field

from pm_mcp.engine.cpm import CpmResult
from pm_mcp.output.network_diagram import NetworkLayout, compute_layout

PITCH_ROWS = 7          # rows per layout pitch: 4 block rows + 3 gap rows
NODE_ROWS = 4
NODE_COLS = 2
CENTRE_PORT = 1         # block row used by a single (or the critical) arc: the IC | TC row
MAX_PERMUTATION_HOPS = 7

N, S, E, W = "N", "S", "E", "W"

# glyphs keyed by (directions, horizontal style, vertical style); 's' single, 'd' double
_GLYPHS: dict[tuple[str, str, str], str] = {}
for (_dirs, _ss, _dd, _ds, _sd) in [
    ("EW", "─", "═", "═", "─"),
    ("NS", "│", "║", "│", "║"),
    ("ES", "┌", "╔", "╒", "╓"),
    ("SW", "┐", "╗", "╕", "╖"),
    ("EN", "└", "╚", "╘", "╙"),
    ("NW", "┘", "╝", "╛", "╜"),
    ("ENS", "├", "╠", "╞", "╟"),
    ("NSW", "┤", "╣", "╡", "╢"),
    ("ESW", "┬", "╦", "╤", "╥"),
    ("ENW", "┴", "╩", "╧", "╨"),
    ("ENSW", "┼", "╬", "╪", "╫"),
]:
    _GLYPHS[(_dirs, "s", "s")] = _ss
    _GLYPHS[(_dirs, "d", "d")] = _dd
    _GLYPHS[(_dirs, "d", "s")] = _ds
    _GLYPHS[(_dirs, "s", "d")] = _sd

ARROW = "►"
ARM = 6  # characters of horizontal arm on each side of a junction glyph (clipped by the cell edge)


@dataclass
class LineCell:
    dirs: set[str] = field(default_factory=set)
    critical_dirs: set[str] = field(default_factory=set)
    arrow: bool = False
    arrow_critical: bool = False

    def add(self, direction: str, critical: bool) -> None:
        self.dirs.add(direction)
        if critical:
            self.critical_dirs.add(direction)

    @property
    def critical(self) -> bool:
        return bool(self.critical_dirs) or self.arrow_critical

    def text(self) -> tuple[str, str]:
        """(cell text, alignment): the glyph centred, with horizontal arms long enough to be clipped at the
        cell edges so lines look continuous; arrow cells are right aligned so the head touches the block."""
        h_style = "d" if self.critical_dirs & {E, W} else "s"
        v_style = "d" if self.critical_dirs & {N, S} else "s"
        h_char = "═" if h_style == "d" else "─"
        if self.arrow:
            return h_char * (ARM * 2) + ARROW, "right"
        dirs = "".join(sorted(self.dirs))
        horizontal = self.dirs & {E, W}
        vertical = self.dirs & {N, S}
        if not vertical:
            return h_char * (ARM * 2 + 1), "center"
        if not horizontal:
            return ("║" if v_style == "d" else "│"), "center"
        glyph = _GLYPHS.get((dirs, h_style, v_style)) or _GLYPHS[(dirs, "s", "s")]
        left = h_char * ARM if W in self.dirs else " " * ARM
        right = h_char * ARM if E in self.dirs else " " * ARM
        return left + glyph + right, "center"


@dataclass
class NodePlace:
    activity_id: str
    row: int        # top row (0-based offset inside the drawing)
    col: int        # left column (0-based offset inside the drawing)
    critical: bool


@dataclass
class CellNetwork:
    nodes: dict[str, NodePlace]
    lines: dict[tuple[int, int], LineCell]
    width: int                       # columns used
    height: int                      # rows used
    channel_widths: list[int]        # columns of every channel between consecutive layers
    node_columns: list[int]          # left column of every layer
    crossings: int = 0               # line crossings actually drawn (┼-type cells between different arcs)


@dataclass
class _Hop:
    arc: tuple[str, str]
    source: str
    target: str
    src_row: int
    dst_row: int
    critical: bool
    to_real: bool
    track: int | None = None


def _choose_ports(rows_wanted: list[float], critical_index: int | None, top: int) -> list[int]:
    """Pick distinct block rows (in order) for k arcs, closest to where they want to go; the critical arc is
    pulled hard to the centre port. With more than NODE_ROWS arcs, ports are shared in order."""
    k = len(rows_wanted)
    if k == 0:
        return []
    if k > NODE_ROWS:
        return [top + min(NODE_ROWS - 1, i * NODE_ROWS // k) for i in range(k)]
    best: tuple[float, tuple[int, ...]] | None = None
    for combo in itertools.combinations(range(NODE_ROWS), k):
        cost = 0.0
        for i, (port, wanted) in enumerate(zip(combo, rows_wanted)):
            if i == critical_index:
                cost += 1000 * abs(port - CENTRE_PORT)
            else:
                cost += abs(top + port - wanted)
        if k == 1 and critical_index is None:
            cost += 0.25 * abs(combo[0] - CENTRE_PORT)  # a lone arc prefers the centre row as well
        if best is None or cost < best[0]:
            best = (cost, combo)
    assert best is not None
    return [top + p for p in best[1]]


def _segments(h: _Hop, end: int) -> tuple[list[tuple[int, int, int, frozenset]], tuple[int, int, int] | None]:
    """Horizontal runs (row, col_from, col_to, ends) and the vertical run (col, row_from, row_to) of a routed hop.

    ``ends`` names the block ports a run is attached to; two runs may share cells only when they hang from the
    same port (a fork out of one source, or a merge into one target)."""
    src_end = ("src", h.source, h.src_row)
    dst_end = ("dst", h.target, h.dst_row)
    if h.src_row == h.dst_row:
        return [(h.src_row, 0, end, frozenset({src_end, dst_end}))], None
    t = h.track
    lo, hi = sorted((h.src_row, h.dst_row))
    return [(h.src_row, 0, t, frozenset({src_end})), (h.dst_row, t, end, frozenset({dst_end}))], (t, lo, hi)


def _channel_cost(hops: list[_Hop], end: int) -> int:
    """Crossings between different arcs, plus a heavy penalty when two arcs that do not share a port would share
    cells (they would look like one line)."""
    cost = 0
    segs = {id(h): _segments(h, end) for h in hops}
    for a in hops:
        ha, va = segs[id(a)]
        for b in hops:
            if a is b:
                continue
            hb, vb = segs[id(b)]
            if va is not None:
                col, lo, hi = va
                for row, c1, c2, ends in hb:
                    if not c1 <= col <= c2:
                        continue
                    if lo < row < hi:
                        cost += 1
                    elif row in (lo, hi) and not (ends & {e for *_, es in ha for e in es}):
                        cost += 1000  # b's run touches a's corner
                if vb is not None and vb[0] == col and max(lo, vb[1]) < min(hi, vb[2]):
                    cost += 1000
            for (r1, a1, a2, e1), (r2, b1, b2, e2) in itertools.product(ha, hb):
                if r1 == r2 and max(a1, b1) <= min(a2, b2) and not (e1 & e2):
                    cost += 1000  # overlapping horizontal runs that do not hang from one port
    return cost


def _assign_tracks(hops: list[_Hop]) -> int:
    """Give every row-changing hop its own track column (1..n); returns n. Exact search for small channels."""
    bent = [h for h in hops if h.src_row != h.dst_row]
    n = len(bent)
    if n == 0:
        return 0
    end = n + 1
    if n <= MAX_PERMUTATION_HOPS:
        best = None
        for perm in itertools.permutations(range(1, n + 1)):
            for h, t in zip(bent, perm):
                h.track = t
            cost = _channel_cost(hops, end)
            if best is None or cost < best[0]:
                best = (cost, perm)
                if cost == 0:
                    break
        for h, t in zip(bent, best[1]):
            h.track = t
    else:
        # downward arcs: higher sources further right; upward arcs: lower sources further right
        bent.sort(key=lambda h: (h.dst_row > h.src_row, -h.src_row if h.dst_row > h.src_row else h.src_row,
                                 h.arc))
        for t, h in enumerate(bent, start=1):
            h.track = t
    return n


def plan_cell_network(schedule: CpmResult, min_channel_widths: list[int] | None = None,
                      lay: NetworkLayout | None = None) -> CellNetwork:
    lay = lay or compute_layout(schedule)
    rows = schedule.by_id()
    n_layers = len(lay.order)

    # ---- rows of blocks and virtual nodes
    top: dict[str, int] = {}
    vrow: dict[str, int] = {}
    for col in lay.order:
        for n in col:
            if not lay.is_virtual(n):
                top[n] = int(math.floor(lay.y[n] * PITCH_ROWS + 0.5))
    for li, col in enumerate(lay.order):
        blocked = {r for n in col if not lay.is_virtual(n) for r in range(top[n], top[n] + NODE_ROWS)}
        for n in sorted((n for n in col if lay.is_virtual(n)), key=lambda n: (lay.y[n], n)):
            want = int(math.floor(lay.y[n] * PITCH_ROWS + 0.5)) + CENTRE_PORT  # level with a block's centre port
            for delta in itertools.chain([0], *([d, -d] for d in range(1, 50))):
                if want + delta not in blocked:
                    vrow[n] = want + delta
                    blocked.add(want + delta)
                    break

    def centre(n: str) -> float:
        return vrow[n] if lay.is_virtual(n) else top[n] + (NODE_ROWS - 1) / 2

    # ---- ports
    out_port: dict[tuple[str, str], int] = {}
    in_port: dict[tuple[str, str], int] = {}
    outs: dict[str, list[tuple[str, str]]] = {}
    ins: dict[str, list[tuple[str, str]]] = {}
    for arc, chain in lay.chains.items():
        outs.setdefault(arc[0], []).append(arc)
        ins.setdefault(arc[1], []).append(arc)
    for u, arcs in outs.items():
        arcs.sort(key=lambda a: (centre(lay.chains[a][1]), a))
        crit = next((i for i, a in enumerate(arcs) if a in lay.critical_arcs), None)
        for arc, port in zip(arcs, _choose_ports([centre(lay.chains[a][1]) for a in arcs], crit, top[u])):
            out_port[arc] = port
    for v, arcs in ins.items():
        arcs.sort(key=lambda a: (centre(lay.chains[a][-2]) if len(lay.chains[a]) > 2 else out_port[a], a))
        crit = next((i for i, a in enumerate(arcs) if a in lay.critical_arcs), None)
        wanted = [centre(lay.chains[a][-2]) if len(lay.chains[a]) > 2 else out_port[a] for a in arcs]
        for arc, port in zip(arcs, _choose_ports(wanted, crit, top[v])):
            in_port[arc] = port

    # ---- hops per channel
    channel_hops: list[list[_Hop]] = [[] for _ in range(max(n_layers - 1, 0))]
    for arc, chain in lay.chains.items():
        critical = arc in lay.critical_arcs
        for i, (a, b) in enumerate(zip(chain, chain[1:])):
            src = out_port[arc] if i == 0 else vrow[a]
            dst = in_port[arc] if i == len(chain) - 2 else vrow[b]
            channel_hops[lay.layer[a]].append(_Hop(arc=arc, source=a, target=b, src_row=src, dst_row=dst,
                                                   critical=critical, to_real=not lay.is_virtual(b)))
    widths = []
    for li, hops in enumerate(channel_hops):
        hops.sort(key=lambda h: (h.src_row, h.dst_row, h.arc))
        n_tracks = _assign_tracks(hops)
        width = max(n_tracks + 2, 3, (min_channel_widths or [])[li] if min_channel_widths and li < len(
            min_channel_widths) else 0)
        widths.append(width)

    node_cols = []
    x = 0
    for li in range(n_layers):
        node_cols.append(x)
        x += NODE_COLS + (widths[li] if li < len(widths) else 0)
    total_width = node_cols[-1] + NODE_COLS

    # ---- shift rows so the drawing starts at row 0
    low = min([*top.values(), *vrow.values()])
    for n in top:
        top[n] -= low
    for n in vrow:
        vrow[n] -= low
    for arc in out_port:
        out_port[arc] -= low
    for arc in in_port:
        in_port[arc] -= low
    for hops in channel_hops:
        for h in hops:
            h.src_row -= low
            h.dst_row -= low

    lines: dict[tuple[int, int], LineCell] = {}

    def cell(r: int, c: int) -> LineCell:
        return lines.setdefault((r, c), LineCell())

    def hline(r: int, c1: int, c2: int, critical: bool) -> None:
        for c in range(c1, c2):
            cell(r, c).add(E, critical)
            cell(r, c + 1).add(W, critical)

    def vline(c: int, r1: int, r2: int, critical: bool) -> None:
        lo, hi = sorted((r1, r2))
        for r in range(lo, hi):
            cell(r, c).add(S, critical)
            cell(r + 1, c).add(N, critical)

    for li, hops in enumerate(channel_hops):
        start = node_cols[li] + NODE_COLS
        end = start + widths[li] - 1
        for h in hops:
            cell(h.src_row, start).add(W, h.critical)  # touches the source block / continues a virtual line
            if h.src_row == h.dst_row:
                hline(h.src_row, start, end, h.critical)
            else:
                t = start + h.track
                hline(h.src_row, start, t, h.critical)
                vline(t, h.src_row, h.dst_row, h.critical)
                hline(h.dst_row, t, end, h.critical)
            last = cell(h.dst_row, end)
            last.add(E, h.critical)
            if h.to_real:
                last.arrow = True
                last.arrow_critical = last.arrow_critical or h.critical
    for n, r in vrow.items():  # the line crosses the layer where the virtual node sits
        c = node_cols[lay.layer[n]]
        arc = next(a for a, ch in lay.chains.items() if n in ch)
        hline(r, c - 1, c + NODE_COLS, arc in lay.critical_arcs)

    crossings = sum(1 for lc in lines.values() if lc.dirs == {N, S, E, W})
    nodes = {a: NodePlace(activity_id=a, row=top[a], col=node_cols[lay.layer[a]], critical=rows[a].is_critical)
             for a in top}
    height = max([*(p.row + NODE_ROWS for p in nodes.values()), *(r + 1 for r, _ in lines)])
    return CellNetwork(nodes=nodes, lines=lines, width=total_width, height=height, channel_widths=widths,
                       node_columns=node_cols, crossings=crossings)


def plan_many(schedules: list[CpmResult]) -> list[CellNetwork]:
    """Plans for several schedules of the same network sharing one column structure (equal channel widths), so
    they can be stacked on one sheet."""
    first = [plan_cell_network(s) for s in schedules]
    widths = [max(p.channel_widths[i] for p in first) for i in range(len(first[0].channel_widths))] if first else []
    return [plan_cell_network(s, widths) for s in schedules]
