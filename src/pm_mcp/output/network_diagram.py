"""Activity-on-node drawing in the reference notation (six-cell node, grey critical nodes, double critical arcs).

Layout: column = longest number of arcs from a start activity (the network's level), rows ordered inside each
column by the barycenter of their predecessors to reduce crossings. Rendering uses matplotlib's object API with
the Agg canvas only (no global pyplot state), and the PNG carries no timestamp, so equal inputs give equal bytes.
"""

from __future__ import annotations

import io
import struct

from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.patches import FancyArrowPatch, Rectangle

from pm_mcp.domain.models import ReportLanguage
from pm_mcp.domain.numbers import number_text
from pm_mcp.engine.cpm import CpmResult

NODE_W, NODE_H = 1.6, 1.6
GAP_X, GAP_Y = 1.0, 0.6
CRITICAL_FILL = "#BFBFBF"
NORMAL_FILL = "#FFFFFF"


def _levels(schedule: CpmResult) -> dict[str, int]:
    levels: dict[str, int] = {}
    for row in schedule.activities:  # topological order
        levels[row.activity_id] = 1 + max((levels[p] for p in row.predecessors), default=-1)
    return levels


def layout(schedule: CpmResult) -> dict[str, tuple[int, int]]:
    """activity id → (column, row) grid position."""
    levels = _levels(schedule)
    columns: dict[int, list[str]] = {}
    for row in schedule.activities:
        columns.setdefault(levels[row.activity_id], []).append(row.activity_id)
    preds = {r.activity_id: r.predecessors for r in schedule.activities}
    position: dict[str, tuple[int, int]] = {}
    for col in sorted(columns):
        ids = columns[col]
        if col > 0:
            def barycenter(aid: str) -> float:
                rows = [position[p][1] for p in preds[aid] if p in position]
                return sum(rows) / len(rows) if rows else 0.0
            ids = sorted(ids, key=lambda a: (barycenter(a), ids.index(a)))
        for i, aid in enumerate(ids):
            position[aid] = (col, i)
    return position


def _xy(pos: tuple[int, int], height: int) -> tuple[float, float]:
    col, row = pos
    return col * (NODE_W + GAP_X), (height - 1 - row) * (NODE_H + GAP_Y)


def png_size(data: bytes) -> tuple[int, int]:
    """(width, height) in pixels from a PNG header."""
    width, height = struct.unpack(">II", data[16:24])
    return width, height


def render_network_png(schedule: CpmResult, language: ReportLanguage | str = ReportLanguage.ES,
                       caption: str | None = None, dpi: int = 100) -> bytes:
    """Draw the scheduled network; each node shows Act | t / IC | TC / IL | TL / Holgura."""
    pos = layout(schedule)
    rows = schedule.by_id()
    n_cols = max(c for c, _ in pos.values()) + 1
    n_rows = max(r for _, r in pos.values()) + 1
    width_units = n_cols * NODE_W + (n_cols - 1) * GAP_X
    height_units = n_rows * NODE_H + (n_rows - 1) * GAP_Y + (0.8 if caption else 0)
    scale = min(0.9, 60 / width_units, 40 / height_units)  # inches per layout unit, capped for huge networks
    fig = Figure(figsize=(max(4.0, width_units * scale + 0.6), max(2.5, height_units * scale + 0.6)), dpi=dpi)
    FigureCanvasAgg(fig)
    ax = fig.add_axes((0, 0, 1, 1))
    ax.set_xlim(-0.3, width_units + 0.3)
    ax.set_ylim(-0.3, height_units + 0.3 - (0 if caption else 0))
    ax.set_aspect("equal")
    ax.axis("off")
    slack_label = "Holgura" if str(language) == "es" else "Slack"

    for r in schedule.activities:
        x, y = _xy(pos[r.activity_id], n_rows)
        fill = CRITICAL_FILL if r.is_critical else NORMAL_FILL
        ax.add_patch(Rectangle((x, y), NODE_W, NODE_H, facecolor=fill, edgecolor="black", linewidth=1.2))
        quarter = NODE_H / 4
        for k in (1, 2, 3):
            ax.plot([x, x + NODE_W], [y + k * quarter] * 2, color="black", linewidth=0.8)
        ax.plot([x + NODE_W / 2] * 2, [y + quarter, y + NODE_H], color="black", linewidth=0.8)
        cells = [
            (r.activity_id, number_text(r.duration)),
            (number_text(r.early_start), number_text(r.early_finish)),
            (number_text(r.late_start), number_text(r.late_finish)),
        ]
        for i, (left, right) in enumerate(cells):
            cy = y + NODE_H - (i + 0.5) * quarter
            weight = "bold" if i == 0 else "normal"
            ax.text(x + NODE_W / 4, cy, left, ha="center", va="center", fontsize=9, fontweight=weight)
            ax.text(x + 3 * NODE_W / 4, cy, right, ha="center", va="center", fontsize=9)
        ax.text(x + NODE_W / 2, y + quarter / 2, f"{slack_label} {number_text(r.total_slack)}",
                ha="center", va="center", fontsize=8)

    for r in schedule.activities:
        for s in r.successors:
            succ = rows[s]
            x1, y1 = _xy(pos[r.activity_id], n_rows)
            x2, y2 = _xy(pos[s], n_rows)
            start = (x1 + NODE_W, y1 + NODE_H / 2)
            end = (x2, y2 + NODE_H / 2)
            critical = r.is_critical and succ.is_critical and r.early_finish == succ.early_start
            if critical:  # double arrow: a thick black stroke with a white core
                ax.add_patch(FancyArrowPatch(start, end, arrowstyle="-|>", mutation_scale=14, linewidth=3.2,
                                             color="black", shrinkA=0, shrinkB=0))
                ax.add_patch(FancyArrowPatch(start, end, arrowstyle="-", linewidth=1.0, color="white",
                                             shrinkA=0, shrinkB=6))
            else:
                ax.add_patch(FancyArrowPatch(start, end, arrowstyle="-|>", mutation_scale=12, linewidth=1.0,
                                             color="black", shrinkA=0, shrinkB=0))

    if caption:
        ax.text(0, height_units + 0.1, caption, ha="left", va="top", fontsize=11, fontweight="bold")

    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", dpi=dpi, metadata={"Software": None}, facecolor="white")
    return buffer.getvalue()
