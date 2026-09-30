"""Activity-on-Node network construction and structural validation."""

from __future__ import annotations

import heapq
from collections import Counter
from collections.abc import Sequence

from pydantic import BaseModel, Field

from pm_mcp.domain.errors import DomainValidationError, IssueCode, IssueSeverity, ValidationIssue, has_errors
from pm_mcp.domain.models import Activity


class PrecedenceEdge(BaseModel):
    from_id: str
    to_id: str


class ActivityNetwork(BaseModel):
    activity_ids: list[str] = Field(description="Input order")
    topological_order: list[str]
    predecessors: dict[str, list[str]]
    successors: dict[str, list[str]]
    edges: list[PrecedenceEdge]
    start_activities: list[str]
    end_activities: list[str]
    dummy_activities: list[str]
    levels: dict[str, int] = Field(description="Longest number of arcs from any start activity (used for layout)")
    component_count: int
    warnings: list[ValidationIssue] = Field(default_factory=list)


def _find_cycles(remaining: list[str], preds: dict[str, list[str]], index: dict[str, int]) -> list[list[str]]:
    """Every node left after Kahn's algorithm has a predecessor that is also left, so walking predecessors
    backwards from any of them must eventually revisit a node: that loop is a cycle."""
    rem = set(remaining)
    explored: set[str] = set()
    cycles: list[list[str]] = []
    for start in remaining:
        if start in explored:
            continue
        path: list[str] = []
        position: dict[str, int] = {}
        node = start
        while node not in position and node not in explored:
            position[node] = len(path)
            path.append(node)
            node = next(p for p in preds[node] if p in rem)
        explored.update(path)
        if node in position:
            backward = path[position[node]:]
            forward = list(reversed(backward))
            pivot = min(range(len(forward)), key=lambda i: index[forward[i]])
            cycles.append(forward[pivot:] + forward[:pivot])
    return cycles


def analyze_network(activities: Sequence[Activity]) -> tuple[ActivityNetwork | None, list[ValidationIssue]]:
    """Build the precedence graph and collect *all* structural issues. Returns (None, issues) when unusable."""
    issues: list[ValidationIssue] = []
    counts = Counter(a.id for a in activities)
    for dup, count in counts.items():
        if count > 1:
            issues.append(ValidationIssue(
                code=IssueCode.DUPLICATE_ACTIVITY_ID,
                message=f"Activity id '{dup}' is defined {count} times; activity ids must be unique.",
                field_path="activities", activity_ids=[dup], needs_user_clarification=True))

    order = list(dict.fromkeys(a.id for a in activities))
    index = {aid: i for i, aid in enumerate(order)}
    definitions: dict[str, Activity] = {}
    for act in activities:
        definitions.setdefault(act.id, act)

    preds: dict[str, list[str]] = {aid: [] for aid in order}
    for pos, act in enumerate(activities):
        if definitions[act.id] is not act:
            continue
        for p in act.predecessors:
            if p == act.id:
                issues.append(ValidationIssue(
                    code=IssueCode.SELF_DEPENDENCY,
                    message=f"Activity {act.id} lists itself as a predecessor.",
                    field_path=f"activities[{pos}].predecessors", activity_ids=[act.id]))
            elif p not in index:
                issues.append(ValidationIssue(
                    code=IssueCode.UNKNOWN_PREDECESSOR,
                    message=f"Activity {act.id} lists predecessor '{p}', which is not a defined activity.",
                    field_path=f"activities[{pos}].predecessors", activity_ids=[act.id, p],
                    needs_user_clarification=True,
                    suggestion=f"Defined activity ids: {', '.join(order)}."))
            elif p in preds[act.id]:
                issues.append(ValidationIssue(
                    code=IssueCode.DUPLICATE_PREDECESSOR, severity=IssueSeverity.WARNING,
                    message=f"Activity {act.id} lists predecessor {p} more than once; the duplicate is ignored.",
                    field_path=f"activities[{pos}].predecessors", activity_ids=[act.id, p]))
            else:
                preds[act.id].append(p)

    succs: dict[str, list[str]] = {aid: [] for aid in order}
    for aid in order:
        for p in preds[aid]:
            succs[p].append(aid)

    indegree = {aid: len(preds[aid]) for aid in order}
    heap = [(index[aid], aid) for aid in order if indegree[aid] == 0]
    heapq.heapify(heap)
    topo: list[str] = []
    while heap:
        _, node = heapq.heappop(heap)
        topo.append(node)
        for s in succs[node]:
            indegree[s] -= 1
            if indegree[s] == 0:
                heapq.heappush(heap, (index[s], s))

    if len(topo) < len(order):
        done = set(topo)
        remaining = [aid for aid in order if aid not in done]
        cycles = _find_cycles(remaining, preds, index)
        in_cycle = {n for c in cycles for n in c}
        for cycle in cycles:
            issues.append(ValidationIssue(
                code=IssueCode.CYCLE,
                message=f"Circular dependency: {' → '.join(cycle + [cycle[0]])}. A project network must be acyclic, "
                        "so no schedule can be computed.",
                field_path="activities", activity_ids=cycle, needs_user_clarification=True,
                suggestion="Check the predecessor lists of these activities; one of the dependencies is probably reversed."))
        blocked = [aid for aid in remaining if aid not in in_cycle]
        if blocked:
            issues.append(ValidationIssue(
                code=IssueCode.UNREACHABLE_ACTIVITY,
                message=f"Activities {', '.join(blocked)} can never start because they depend, directly or indirectly, "
                        "on a circular dependency.",
                field_path="activities", activity_ids=blocked))
        return None, issues

    if has_errors(issues):
        return None, issues

    levels: dict[str, int] = {}
    for node in topo:
        levels[node] = 1 + max((levels[p] for p in preds[node]), default=-1)

    parent = {aid: aid for aid in order}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for aid in order:
        for p in preds[aid]:
            ra, rb = find(aid), find(p)
            if ra != rb:
                parent[max(ra, rb, key=index.get)] = min(ra, rb, key=index.get)
    groups: dict[str, list[str]] = {}
    for aid in order:
        groups.setdefault(find(aid), []).append(aid)
    if len(groups) > 1:
        described = "; ".join("{" + ", ".join(g) + "}" for g in groups.values())
        issues.append(ValidationIssue(
            code=IssueCode.DISCONNECTED_NETWORK, severity=IssueSeverity.WARNING,
            message=f"The network splits into {len(groups)} unconnected groups: {described}. The schedule is still "
                    "computable (all groups start at time 0), but check that no precedence was lost during extraction.",
            field_path="activities", activity_ids=[g[0] for g in groups.values()]))

    dummies = [a.id for a in definitions.values() if a.is_dummy]
    for d in dummies:
        if not preds[d] or not succs[d]:
            issues.append(ValidationIssue(
                code=IssueCode.INVALID_DUMMY, severity=IssueSeverity.WARNING,
                message=f"Dummy activity {d} has no {'predecessors' if not preds[d] else 'successors'}, so it enforces no precedence.",
                activity_ids=[d]))

    network = ActivityNetwork(
        activity_ids=order,
        topological_order=topo,
        predecessors=preds,
        successors=succs,
        edges=[PrecedenceEdge(from_id=p, to_id=aid) for aid in order for p in preds[aid]],
        start_activities=[aid for aid in order if not preds[aid]],
        end_activities=[aid for aid in order if not succs[aid]],
        dummy_activities=dummies,
        levels=levels,
        component_count=len(groups),
        warnings=[i for i in issues if i.severity == IssueSeverity.WARNING],
    )
    return network, issues


def build_activity_network(activities: Sequence[Activity]) -> ActivityNetwork:
    """Build the network or raise :class:`DomainValidationError` listing every structural error."""
    network, issues = analyze_network(activities)
    if network is None or has_errors(issues):
        raise DomainValidationError(issues)
    return network


def enumerate_paths(network: ActivityNetwork, limit: int = 24) -> list[list[str]]:
    """Every start-to-end chain of the network, in topological order, or [] when there are more than ``limit``.

    Used to show how long each route is at a given moment (the figure exercises write "A-C-G = 27"); the limit
    keeps a large network from enumerating exponentially many paths.
    """
    paths: list[list[str]] = []
    stack: list[list[str]] = [[a] for a in reversed(network.start_activities)]
    while stack:
        path = stack.pop()
        successors = network.successors[path[-1]]
        if not successors:
            paths.append(path)
            if len(paths) > limit:
                return []
            continue
        for successor in reversed(successors):
            stack.append([*path, successor])
    return paths
