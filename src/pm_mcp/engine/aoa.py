"""Activity-on-Arrow ('actividad-flecha') → Activity-on-Node conversion.

Exercise figures often draw activities on arcs between numbered event nodes, with dashed zero-duration
'ficticia' arcs. The reference material solves such exercises by first reading the precedences off the arrow
network and then drawing the ACTIVIDAD-NODO network; this module performs that step deterministically.

Rule: an activity leaving node *n* has as predecessors every real activity entering *n*, plus — transitively —
the real activities entering the tail node of every dummy arc that enters *n*.
"""

from __future__ import annotations

import heapq
from collections import Counter
from collections.abc import Sequence

from pydantic import BaseModel, Field

from pm_mcp.domain.errors import DomainValidationError, IssueCode, IssueSeverity, ValidationIssue, has_errors
from pm_mcp.domain.models import Activity, ArrowArc, ArrowConversionTrace, Provenance, ReportLanguage


class ArrowNetworkConversion(BaseModel):
    activities: list[Activity]
    trace: list[ArrowConversionTrace]
    start_nodes: list[str]
    end_nodes: list[str]
    dummy_arcs: list[str]
    warnings: list[ValidationIssue] = Field(default_factory=list)


def _dummy_id(arc: ArrowArc) -> str:
    return arc.id or f"ficticia_{arc.tail_node}_{arc.head_node}"


def _justify(language: ReportLanguage, arc_id: str, tail: str, direct: list[str], via: list[str], preds: list[str]) -> str:
    if language == ReportLanguage.EN:
        if not preds:
            return f"Arc {arc_id} leaves node {tail}, which no activity enters: start activity."
        text = f"Arc {arc_id} leaves node {tail}; activities entering node {tail}: {', '.join(direct) or '-'}"
        if via:
            text += f"; through dummy arc(s) {', '.join(via)} it also follows {', '.join(p for p in preds if p not in direct)}"
        return text + "."
    if not preds:
        return f"La flecha {arc_id} sale del nodo {tail}, al que no llega ninguna actividad: actividad inicial."
    text = f"La flecha {arc_id} sale del nodo {tail}; llegan al nodo {tail}: {', '.join(direct) or '-'}"
    if via:
        text += f"; por la(s) ficticia(s) {', '.join(via)} también depende de {', '.join(p for p in preds if p not in direct)}"
    return text + "."


def convert_arrow_network(
    arcs: Sequence[ArrowArc],
    keep_dummies: bool = False,
    language: ReportLanguage = ReportLanguage.ES,
) -> ArrowNetworkConversion:
    issues: list[ValidationIssue] = []
    if not arcs:
        raise DomainValidationError([ValidationIssue(code=IssueCode.EMPTY_PROJECT, message="The arrow network has no arcs.")])

    ids: list[str] = []
    for pos, arc in enumerate(arcs):
        if arc.tail_node == arc.head_node:
            issues.append(ValidationIssue(code=IssueCode.AOA_INVALID_ARC,
                                          message=f"Arc {arc.id or pos} starts and ends at node {arc.tail_node}.",
                                          field_path=f"arcs[{pos}]"))
        if not arc.is_dummy and not arc.id:
            issues.append(ValidationIssue(code=IssueCode.AOA_INVALID_ARC,
                                          message=f"Arc {arc.tail_node}→{arc.head_node} is not a dummy but has no activity id.",
                                          field_path=f"arcs[{pos}].id", needs_user_clarification=True))
        ids.append(_dummy_id(arc) if arc.is_dummy else (arc.id or f"#{pos}"))
    for dup, count in Counter(ids).items():
        if count > 1:
            issues.append(ValidationIssue(code=IssueCode.DUPLICATE_ACTIVITY_ID,
                                          message=f"Arc id '{dup}' appears {count} times in the arrow network.",
                                          activity_ids=[dup]))
    pairs = Counter((a.tail_node, a.head_node) for a in arcs if not a.is_dummy)
    for (tail, head), count in pairs.items():
        if count > 1:
            same = [ids[i] for i, a in enumerate(arcs) if (a.tail_node, a.head_node) == (tail, head) and not a.is_dummy]
            issues.append(ValidationIssue(
                code=IssueCode.AOA_DUPLICATE_ARC, severity=IssueSeverity.WARNING,
                message=f"Activities {', '.join(same)} share nodes {tail}→{head}; arrow networks normally separate "
                        "them with a dummy arc. Precedences are still well defined.", activity_ids=same))
    if has_errors(issues):
        raise DomainValidationError(issues)

    nodes = list(dict.fromkeys(n for a in arcs for n in (a.tail_node, a.head_node)))
    node_index = {n: i for i, n in enumerate(nodes)}
    incoming: dict[str, list[int]] = {n: [] for n in nodes}
    outgoing: dict[str, list[int]] = {n: [] for n in nodes}
    for i, arc in enumerate(arcs):
        outgoing[arc.tail_node].append(i)
        incoming[arc.head_node].append(i)

    indegree = {n: len(incoming[n]) for n in nodes}
    heap = [(node_index[n], n) for n in nodes if indegree[n] == 0]
    heapq.heapify(heap)
    ordered: list[str] = []
    while heap:
        _, n = heapq.heappop(heap)
        ordered.append(n)
        for i in outgoing[n]:
            head = arcs[i].head_node
            indegree[head] -= 1
            if indegree[head] == 0:
                heapq.heappush(heap, (node_index[head], head))
    if len(ordered) < len(nodes):
        stuck = [n for n in nodes if n not in set(ordered)]
        raise DomainValidationError([ValidationIssue(
            code=IssueCode.AOA_CYCLE,
            message=f"The arrow network contains a cycle through node(s) {', '.join(stuck)}.",
            needs_user_clarification=True,
            suggestion="Check the arrow directions of the arcs touching these nodes.")])

    start_nodes = [n for n in nodes if not incoming[n]]
    end_nodes = [n for n in nodes if not outgoing[n]]
    if len(start_nodes) > 1 or len(end_nodes) > 1:
        issues.append(ValidationIssue(
            code=IssueCode.AOA_DISCONNECTED, severity=IssueSeverity.WARNING,
            message=f"Arrow networks normally have one start and one end node; found start nodes {start_nodes} and "
                    f"end nodes {end_nodes}. Verify the figure transcription."))

    memo: dict[str, tuple[list[str], list[str]]] = {}

    def resolve(node: str) -> tuple[list[str], list[str]]:
        """(real activities finishing at node, dummy arcs traversed) — nodes are processed in topological order."""
        if node in memo:
            return memo[node]
        found: list[str] = []
        via: list[str] = []
        for i in incoming[node]:
            arc = arcs[i]
            if arc.is_dummy:
                sub, sub_via = resolve(arc.tail_node)
                found.extend(sub)
                via.extend([ids[i], *sub_via])
            else:
                found.append(ids[i])
        memo[node] = (list(dict.fromkeys(found)), list(dict.fromkeys(via)))
        return memo[node]

    for n in ordered:
        resolve(n)

    activities: list[Activity] = []
    trace: list[ArrowConversionTrace] = []
    for i, arc in enumerate(arcs):
        if arc.is_dummy and not keep_dummies:
            continue
        if keep_dummies:
            preds = [ids[j] for j in incoming[arc.tail_node]]
            direct, via = preds, []
        else:
            preds, via = resolve(arc.tail_node)
            direct = [ids[j] for j in incoming[arc.tail_node] if not arcs[j].is_dummy]
        justification = _justify(language, ids[i], arc.tail_node, direct, via, preds)
        activities.append(Activity(
            id=ids[i],
            name=arc.name,
            predecessors=preds,
            is_dummy=arc.is_dummy,
            duration=0 if arc.is_dummy else arc.duration,
            optimistic_duration=arc.optimistic_duration,
            most_likely_duration=arc.most_likely_duration,
            pessimistic_duration=arc.pessimistic_duration,
            crash=arc.crash,
            provenance=arc.provenance,
            field_provenance={"predecessors": Provenance.INFERRED},
            provenance_notes=[justification],
        ))
        trace.append(ArrowConversionTrace(activity_id=ids[i], tail_node=arc.tail_node, head_node=arc.head_node,
                                          predecessors=preds, via_dummies=via, justification=justification))

    return ArrowNetworkConversion(
        activities=activities,
        trace=trace,
        start_nodes=start_nodes,
        end_nodes=end_nodes,
        dummy_arcs=[ids[i] for i, a in enumerate(arcs) if a.is_dummy],
        warnings=[i for i in issues if i.severity == IssueSeverity.WARNING],
    )
