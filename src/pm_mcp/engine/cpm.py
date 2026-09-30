"""Critical Path Method: forward pass, backward pass, slack, critical path(s)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from fractions import Fraction

from pydantic import BaseModel, Field

from pm_mcp.domain.errors import DomainValidationError, IssueCode, ValidationIssue
from pm_mcp.domain.models import Activity, DurationSource
from pm_mcp.domain.numbers import Exact
from pm_mcp.engine.network import ActivityNetwork

DEFAULT_MAX_PATHS = 1000


class CpmActivityResult(BaseModel):
    activity_id: str
    name: str | None = None
    is_dummy: bool = False
    duration: Exact
    early_start: Exact
    early_finish: Exact
    late_start: Exact
    late_finish: Exact
    total_slack: Exact = Field(description="LS − ES (= LF − EF)")
    free_slack: Exact = Field(description="min(ES of successors) − EF")
    is_critical: bool
    predecessors: list[str]
    successors: list[str]


class CpmResult(BaseModel):
    duration_source: DurationSource
    project_duration: Exact
    activities: list[CpmActivityResult] = Field(description="Topological order")
    critical_activities: list[str] = Field(description="Real (non-dummy) activities with zero slack, topological order")
    critical_paths: list[list[str]] = Field(description="Start-to-end chains of critical activities (dummies omitted)")
    critical_paths_truncated: bool = False

    def by_id(self) -> dict[str, CpmActivityResult]:
        return {r.activity_id: r for r in self.activities}


def resolve_durations(
    activities: Sequence[Activity],
    source: DurationSource,
    expected_durations: Mapping[str, Fraction] | None = None,
) -> dict[str, Fraction]:
    durations: dict[str, Fraction] = {}
    missing: list[str] = []
    for act in activities:
        if act.is_dummy:
            durations[act.id] = Fraction(0)
        elif source == DurationSource.DURATION:
            if act.duration is None:
                missing.append(act.id)
            else:
                durations[act.id] = act.duration
        else:
            if expected_durations is None or act.id not in expected_durations:
                missing.append(act.id)
            else:
                durations[act.id] = expected_durations[act.id]
    if missing:
        raise DomainValidationError([ValidationIssue(
            code=IssueCode.METHOD_DATA_MISSING,
            message=f"No {source.value} available for: {', '.join(missing)}.",
            activity_ids=missing, needs_user_clarification=True)])
    return durations


def calculate_cpm(
    network: ActivityNetwork,
    activities: Sequence[Activity],
    durations: Mapping[str, Fraction],
    source: DurationSource,
    max_paths: int = DEFAULT_MAX_PATHS,
) -> CpmResult:
    info = {a.id: a for a in activities}
    preds, succs, topo = network.predecessors, network.successors, network.topological_order

    es: dict[str, Fraction] = {}
    ef: dict[str, Fraction] = {}
    for node in topo:
        es[node] = max((ef[p] for p in preds[node]), default=Fraction(0))
        ef[node] = es[node] + durations[node]
    project_duration = max(ef.values())

    lf: dict[str, Fraction] = {}
    ls: dict[str, Fraction] = {}
    for node in reversed(topo):
        lf[node] = min((ls[s] for s in succs[node]), default=project_duration)
        ls[node] = lf[node] - durations[node]

    critical = {node: ls[node] == es[node] for node in topo}
    rows = [
        CpmActivityResult(
            activity_id=node,
            name=info[node].name,
            is_dummy=info[node].is_dummy,
            duration=durations[node],
            early_start=es[node],
            early_finish=ef[node],
            late_start=ls[node],
            late_finish=lf[node],
            total_slack=ls[node] - es[node],
            free_slack=min((es[s] for s in succs[node]), default=project_duration) - ef[node],
            is_critical=critical[node],
            predecessors=list(preds[node]),
            successors=list(succs[node]),
        )
        for node in topo
    ]

    tight = {n: [s for s in succs[n] if critical[s] and ef[n] == es[s]] for n in topo if critical[n]}
    has_tight_pred = {s for targets in tight.values() for s in targets}
    sources = [n for n in topo if critical[n] and es[n] == 0 and n not in has_tight_pred]
    raw_paths: list[list[str]] = []
    truncated = False
    stack: list[tuple[str, list[str]]] = [(s, [s]) for s in reversed(sources)]
    while stack:
        node, path = stack.pop()
        nexts = tight[node]
        if not nexts:
            if ef[node] == project_duration:
                raw_paths.append(path)
                if len(raw_paths) >= max_paths:
                    truncated = bool(stack)
                    break
            continue
        for s in reversed(nexts):
            stack.append((s, path + [s]))

    paths: list[list[str]] = []
    seen: set[tuple[str, ...]] = set()
    for path in raw_paths:
        visible = tuple(n for n in path if not info[n].is_dummy)
        if visible and visible not in seen:
            seen.add(visible)
            paths.append(list(visible))

    return CpmResult(
        duration_source=source,
        project_duration=project_duration,
        activities=rows,
        critical_activities=[n for n in topo if critical[n] and not info[n].is_dummy],
        critical_paths=paths,
        critical_paths_truncated=truncated,
    )
