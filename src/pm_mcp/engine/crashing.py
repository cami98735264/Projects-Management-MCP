"""Time-cost trade-off ('compresión' / crashing), as taught in the reference workbook's CPM sheets.

slope = (Cc − Cn) / (Dn − Dc). Repeatedly: compute the critical path(s); shorten, by one step, the cheapest
set of reducible critical activities that shortens *every* critical path (a minimum-cost vertex cut of the
critical sub-network — a single activity when there is one critical path, or e.g. a shared activity versus one
activity per path when there are several); stop when a critical path has no reducible activity left or the
target duration is reached.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from fractions import Fraction

import networkx as nx
from pydantic import BaseModel, Field

from pm_mcp.domain.errors import IssueCode, IssueSeverity, ValidationIssue
from pm_mcp.domain.models import Activity, CrashingRequest, DurationSource, ReportLanguage
from pm_mcp.domain.numbers import Exact, fraction_text
from pm_mcp.engine.cpm import CpmResult, calculate_cpm
from pm_mcp.engine.network import ActivityNetwork

MAX_STEPS = 10_000


class ActivitySlope(BaseModel):
    activity_id: str
    normal_duration: Exact
    crash_duration: Exact
    normal_cost: Exact
    crash_cost: Exact
    max_reduction: Exact
    slope: Exact | None = Field(description="None when the activity cannot be shortened")
    working: str


class CrashState(BaseModel):
    step: int
    project_duration: Exact
    direct_cost: Exact
    schedule: CpmResult = Field(description="The network re-scheduled with this step's durations")
    durations: dict[str, Exact]
    critical_paths: list[list[str]]
    next_crash_activities: list[str] = Field(default_factory=list)
    next_reduction: Exact | None = None
    next_cost_increase: Exact | None = None


class CostPoint(BaseModel):
    project_duration: Exact
    direct_cost: Exact
    indirect_cost: Exact | None = None
    total_cost: Exact | None = None


class CrashingResult(BaseModel):
    slopes: list[ActivitySlope]
    normal_direct_cost: Exact
    states: list[CrashState]
    stop_reason: str
    minimum_duration: Exact
    minimum_duration_direct_cost: Exact
    cost_table: list[CostPoint]
    optimal: CostPoint | None
    warnings: list[ValidationIssue] = Field(default_factory=list)


def _min_cost_cut(
    cpm: CpmResult,
    candidates: dict[str, Fraction],
    order: dict[str, int],
) -> list[str] | None:
    """Cheapest set of candidate activities intersecting every critical path, or None if impossible."""
    if not candidates:
        return None
    denominator = math.lcm(*(s.denominator for s in candidates.values()))
    tie_break = len(candidates) + 1
    rows = cpm.by_id()
    graph = nx.DiGraph()
    for r in cpm.activities:
        if not r.is_critical:
            continue
        node_in, node_out = ("in", r.activity_id), ("out", r.activity_id)
        if r.activity_id in candidates:
            capacity = int(candidates[r.activity_id] * denominator) * tie_break + 1
            graph.add_edge(node_in, node_out, capacity=capacity)
        else:
            graph.add_edge(node_in, node_out)
        for s in r.successors:
            succ = rows[s]
            if succ.is_critical and succ.early_start == r.early_finish:
                graph.add_edge(node_out, ("in", s))
        if r.early_start == 0:
            graph.add_edge("source", node_in)
        if r.early_finish == cpm.project_duration:
            graph.add_edge(node_out, "sink")
    try:
        _, (reachable, _) = nx.minimum_cut(graph, "source", "sink")
    except nx.NetworkXUnbounded:
        return None
    cut = [a for a in candidates if ("in", a) in reachable and ("out", a) not in reachable]
    return sorted(cut, key=order.get)


def calculate_crashing_schedule(
    network: ActivityNetwork,
    activities: Sequence[Activity],
    request: CrashingRequest | None = None,
    language: ReportLanguage = ReportLanguage.ES,
) -> CrashingResult:
    request = request or CrashingRequest()
    order = {aid: i for i, aid in enumerate(network.topological_order)}
    real = [a for a in activities if not a.is_dummy]
    slopes: dict[str, Fraction | None] = {}
    slope_rows: list[ActivitySlope] = []
    for act in real:
        c = act.crash
        reduction = act.duration - c.crash_duration
        slope = (c.crash_cost - c.normal_cost) / reduction if reduction > 0 else None
        slopes[act.id] = slope
        working = (
            f"({fraction_text(c.crash_cost)} − {fraction_text(c.normal_cost)}) / "
            f"({fraction_text(act.duration)} − {fraction_text(c.crash_duration)}) = {fraction_text(slope)}"
            if slope is not None else ("no tiene (Dn = Dc)" if language == ReportLanguage.ES else "none (Dn = Dc)")
        )
        slope_rows.append(ActivitySlope(
            activity_id=act.id, normal_duration=act.duration, crash_duration=c.crash_duration,
            normal_cost=c.normal_cost, crash_cost=c.crash_cost, max_reduction=reduction, slope=slope, working=working))

    minimum = {a.id: a.crash.crash_duration for a in real}
    durations: dict[str, Fraction] = {a.id: (Fraction(0) if a.is_dummy else a.duration) for a in activities}
    normal_cost = sum((a.crash.normal_cost for a in real), Fraction(0))
    cost = normal_cost
    states: list[CrashState] = []
    warnings: list[ValidationIssue] = []
    stop_reason = ""
    es = language == ReportLanguage.ES

    for step in range(MAX_STEPS):
        cpm = calculate_cpm(network, activities, durations, DurationSource.DURATION)
        state = CrashState(step=step, project_duration=cpm.project_duration, direct_cost=cost, schedule=cpm,
                           durations=dict(durations), critical_paths=cpm.critical_paths)
        states.append(state)
        target = request.target_duration
        if target is not None and cpm.project_duration <= target:
            stop_reason = (f"Se alcanzó la duración objetivo {fraction_text(target)}." if es
                           else f"Target duration {fraction_text(target)} reached.")
            break
        candidates = {
            a: slopes[a] for a in cpm.critical_activities
            if slopes.get(a) is not None and durations[a] > minimum[a]
        }
        cut = _min_cost_cut(cpm, candidates, order)
        if cut is None:
            blocked = next((p for p in cpm.critical_paths if not any(a in candidates for a in p)), None)
            path_text = "-".join(blocked) if blocked else "?"
            stop_reason = (
                f"La ruta crítica {path_text} ya no tiene actividades reducibles: el proyecto llegó a su mínima reducción."
                if es else f"Critical path {path_text} has no reducible activity left: maximum compression reached.")
            break
        positive_slack = [r.total_slack for r in cpm.activities if r.total_slack > 0]
        limits = [request.step, *(durations[a] - minimum[a] for a in cut)]
        if positive_slack:
            limits.append(min(positive_slack))
        if target is not None:
            limits.append(cpm.project_duration - target)
        delta = min(limits)
        increase = sum((slopes[a] for a in cut), Fraction(0)) * delta
        state.next_crash_activities = cut
        state.next_reduction = delta
        state.next_cost_increase = increase
        for a in cut:
            durations[a] -= delta
        cost += increase
    else:
        warnings.append(ValidationIssue(code=IssueCode.CALCULATION_INCONSISTENCY, severity=IssueSeverity.WARNING,
                                        message=f"Crashing stopped after {MAX_STEPS} steps."))
        stop_reason = "step limit"

    direct_by_duration: dict[Fraction, Fraction] = {}
    for s in states:
        direct_by_duration.setdefault(s.project_duration, s.direct_cost)
    indirect = {p.duration: p.cost for p in request.indirect_costs}
    table = [
        CostPoint(project_duration=d, direct_cost=c, indirect_cost=indirect.get(d),
                  total_cost=(c + indirect[d]) if d in indirect else None)
        for d, c in sorted(direct_by_duration.items())
    ]
    unmatched = [d for d in indirect if d not in direct_by_duration]
    if unmatched:
        warnings.append(ValidationIssue(
            code=IssueCode.INVALID_CRASH_DATA, severity=IssueSeverity.WARNING,
            message=f"Indirect costs given for durations {', '.join(fraction_text(d) for d in unmatched)} that the "
                    "compression procedure never reaches; they are not used."))
    with_total = [p for p in table if p.total_cost is not None]
    optimal = min(with_total, key=lambda p: (p.total_cost, p.project_duration)) if with_total else None
    last = states[-1]
    return CrashingResult(
        slopes=slope_rows, normal_direct_cost=normal_cost, states=states, stop_reason=stop_reason,
        minimum_duration=last.project_duration, minimum_duration_direct_cost=last.direct_cost,
        cost_table=table, optimal=optimal, warnings=warnings,
    )
