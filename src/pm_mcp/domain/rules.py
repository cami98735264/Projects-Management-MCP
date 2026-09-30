"""Semantic validation rules for projects.

Branching here is keyed only on the *shape* of the data (which fields are present, what was requested),
never on the identity of a particular exercise.
"""

from __future__ import annotations

from collections import Counter

from pm_mcp.domain.errors import IssueCode, IssueSeverity, ValidationIssue
from pm_mcp.domain.models import (
    CalculationMethod,
    ProbabilityQueryKind,
    ProjectDraft,
    RequestedOutput,
    TimeUnitCode,
)

ESTIMATE_OUTPUTS = {
    RequestedOutput.VARIANCE,
    RequestedOutput.ACTIVITY_ESTIMATES,
    RequestedOutput.PROBABILITY_QUERY,
    RequestedOutput.PERCENTILE_DURATION,
}


def _err(code: IssueCode, message: str, **kw) -> ValidationIssue:
    return ValidationIssue(code=code, message=message, **kw)


def _warn(code: IssueCode, message: str, **kw) -> ValidationIssue:
    return ValidationIssue(code=code, severity=IssueSeverity.WARNING, message=message, **kw)


def infer_calculation_method(draft: ProjectDraft) -> tuple[CalculationMethod | None, list[ValidationIssue]]:
    """Resolve CPM/PERT/BOTH from which duration data every real (non-dummy) activity carries."""
    real = [a for a in draft.activities if not a.is_dummy]
    if not real:
        return None, []
    with_estimates = [a.id for a in real if a.has_three_point_estimates]
    with_duration = [a.id for a in real if a.duration is not None]
    all_est = len(with_estimates) == len(real)
    all_dur = len(with_duration) == len(real)
    requested = draft.calculation_method

    if requested is not None:
        issues: list[ValidationIssue] = []
        if requested in (CalculationMethod.PERT, CalculationMethod.BOTH) and not all_est:
            missing = [a.id for a in real if not a.has_three_point_estimates]
            issues.append(
                _err(
                    IssueCode.METHOD_DATA_MISSING,
                    f"Method {requested} needs optimistic/most-likely/pessimistic durations for every activity; "
                    f"missing for: {', '.join(missing)}.",
                    field_path="activities",
                    activity_ids=missing,
                    needs_user_clarification=True,
                    suggestion="Provide a, m and b for these activities, or use calculation_method='CPM' with durations.",
                )
            )
        if requested in (CalculationMethod.CPM, CalculationMethod.BOTH) and not all_dur:
            missing = [a.id for a in real if a.duration is None]
            issues.append(
                _err(
                    IssueCode.METHOD_DATA_MISSING,
                    f"Method {requested} needs a deterministic duration for every activity; missing for: {', '.join(missing)}.",
                    field_path="activities",
                    activity_ids=missing,
                    needs_user_clarification=True,
                    suggestion="Provide `duration`, or use calculation_method='PERT' with three-point estimates.",
                )
            )
        return (requested if not issues else None), issues

    if all_est and all_dur:
        return CalculationMethod.BOTH, []
    if all_est:
        return CalculationMethod.PERT, []
    if all_dur:
        return CalculationMethod.CPM, []
    neither = [a.id for a in real if a.id not in with_estimates and a.id not in with_duration]
    mixed = not neither
    message = (
        "Duration data is mixed: some activities only have three-point estimates and others only a deterministic "
        "duration, so neither CPM nor PERT can be applied consistently."
        if mixed
        else f"No duration data (neither `duration` nor a/m/b) for: {', '.join(neither)}."
    )
    return None, [
        _err(
            IssueCode.AMBIGUOUS_METHOD if mixed else IssueCode.METHOD_DATA_MISSING,
            message,
            field_path="activities",
            activity_ids=neither or [a.id for a in real],
            needs_user_clarification=True,
            suggestion="Ask the user for the missing durations; never invent them.",
        )
    ]


def _activity_issues(draft: ProjectDraft) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    for index, act in enumerate(draft.activities):
        path = f"activities[{index}]"
        numeric = {
            "duration": act.duration,
            "optimistic_duration": act.optimistic_duration,
            "most_likely_duration": act.most_likely_duration,
            "pessimistic_duration": act.pessimistic_duration,
        }
        for field, value in numeric.items():
            if value is not None and value < 0:
                issues.append(
                    _err(IssueCode.NEGATIVE_VALUE, f"Activity {act.id}: {field} cannot be negative ({value}).",
                         field_path=f"{path}.{field}", activity_ids=[act.id])
                )
        if act.is_dummy:
            nonzero = [f for f, v in numeric.items() if v not in (None, 0)]
            if nonzero:
                issues.append(
                    _err(IssueCode.INVALID_DUMMY,
                         f"Activity {act.id} is marked as dummy ('ficticia') but has non-zero {', '.join(nonzero)}; "
                         "dummy activities have zero duration by definition.",
                         field_path=path, activity_ids=[act.id],
                         suggestion="Set is_dummy=false if it is a real activity, or remove the durations.")
                )
            if act.crash is not None:
                issues.append(_err(IssueCode.INVALID_DUMMY, f"Dummy activity {act.id} cannot carry crashing data.",
                                   field_path=f"{path}.crash", activity_ids=[act.id]))
            continue
        if act.has_partial_estimates:
            missing = [n for n, v in zip(("optimistic_duration", "most_likely_duration", "pessimistic_duration"),
                                         act.estimates) if v is None]
            issues.append(
                _err(IssueCode.INCOMPLETE_ESTIMATES,
                     f"Activity {act.id} has only some PERT estimates; missing {', '.join(missing)}.",
                     field_path=path, activity_ids=[act.id], needs_user_clarification=True,
                     suggestion="PERT needs all three estimates (a, m, b); ask the user for the missing ones.")
            )
        elif act.has_three_point_estimates:
            a, m, b = act.estimates
            if not (a <= m <= b):
                issues.append(
                    _err(IssueCode.INCONSISTENT_ESTIMATES,
                         f"Activity {act.id}: estimates must satisfy optimistic ≤ most likely ≤ pessimistic "
                         f"(got a={a}, m={m}, b={b}). The columns may have been transcribed in the wrong order.",
                         field_path=path, activity_ids=[act.id], needs_user_clarification=True)
                )
        if act.crash is not None:
            c = act.crash
            if act.duration is None:
                issues.append(_err(IssueCode.INVALID_CRASH_DATA,
                                   f"Activity {act.id} has crashing data but no normal `duration`.",
                                   field_path=f"{path}.duration", activity_ids=[act.id]))
            elif c.crash_duration > act.duration:
                issues.append(_err(IssueCode.INVALID_CRASH_DATA,
                                   f"Activity {act.id}: minimum duration {c.crash_duration} exceeds normal duration {act.duration}.",
                                   field_path=f"{path}.crash.crash_duration", activity_ids=[act.id]))
            if c.crash_duration < 0 or c.normal_cost < 0 or c.crash_cost < 0:
                issues.append(_err(IssueCode.NEGATIVE_VALUE, f"Activity {act.id}: crashing values cannot be negative.",
                                   field_path=f"{path}.crash", activity_ids=[act.id]))
            if c.crash_cost < c.normal_cost:
                issues.append(_err(IssueCode.INVALID_CRASH_DATA,
                                   f"Activity {act.id}: minimum-duration cost {c.crash_cost} is lower than normal cost "
                                   f"{c.normal_cost}; crashing cannot reduce direct cost.",
                                   field_path=f"{path}.crash", activity_ids=[act.id], needs_user_clarification=True))
            if act.duration is not None and c.crash_duration == act.duration and c.crash_cost != c.normal_cost:
                issues.append(_warn(IssueCode.INVALID_CRASH_DATA,
                                    f"Activity {act.id}: normal and minimum durations are equal, so its cost difference is ignored.",
                                    field_path=f"{path}.crash", activity_ids=[act.id]))
    return issues


def _query_issues(draft: ProjectDraft, method: CalculationMethod | None) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    uses_estimates = method in (CalculationMethod.PERT, CalculationMethod.BOTH)
    for index, q in enumerate(draft.probability_queries):
        path = f"probability_queries[{index}]"
        label = q.id or str(index)
        if q.kind == ProbabilityQueryKind.AT_MOST and q.upper_bound is None:
            issues.append(_err(IssueCode.INVALID_PROBABILITY_QUERY, f"Query {label}: AT_MOST requires upper_bound.", field_path=path))
        if q.kind == ProbabilityQueryKind.AT_LEAST and q.lower_bound is None:
            issues.append(_err(IssueCode.INVALID_PROBABILITY_QUERY, f"Query {label}: AT_LEAST requires lower_bound.", field_path=path))
        if q.kind == ProbabilityQueryKind.BETWEEN:
            if q.lower_bound is None or q.upper_bound is None:
                issues.append(_err(IssueCode.INVALID_PROBABILITY_QUERY,
                                   f"Query {label}: BETWEEN requires lower_bound and upper_bound.", field_path=path))
            elif q.lower_bound > q.upper_bound:
                issues.append(_err(IssueCode.INVALID_PROBABILITY_QUERY,
                                   f"Query {label}: lower_bound {q.lower_bound} is greater than upper_bound {q.upper_bound}.",
                                   field_path=path))
        if q.kind == ProbabilityQueryKind.PERCENTILE_TO_DURATION:
            p = q.target_probability
            if p is None or not (0 < p < 1):
                issues.append(_err(IssueCode.INVALID_PROBABILITY_QUERY,
                                   f"Query {label}: target_probability must be strictly between 0 and 1 (got {p}). "
                                   "Percentages such as 98 must be given as 0.98.", field_path=f"{path}.target_probability"))
        for bound in (q.lower_bound, q.upper_bound):
            if bound is not None and bound < 0:
                issues.append(_err(IssueCode.NEGATIVE_VALUE, f"Query {label}: durations cannot be negative.", field_path=path))
    if draft.probability_queries and method is not None and not uses_estimates:
        issues.append(
            _err(IssueCode.PROBABILITY_WITHOUT_ESTIMATES,
                 "Probability questions require PERT three-point estimates, but the project has only deterministic durations.",
                 field_path="probability_queries", needs_user_clarification=True,
                 suggestion="Remove the probability questions or provide optimistic/most-likely/pessimistic durations.")
        )
    ids = [q.id for q in draft.probability_queries if q.id]
    for dup, count in Counter(ids).items():
        if count > 1:
            issues.append(_err(IssueCode.INVALID_PROBABILITY_QUERY, f"Probability query id '{dup}' is used {count} times.",
                               field_path="probability_queries"))
    return issues


def _request_issues(draft: ProjectDraft, method: CalculationMethod | None) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    ids = {a.id for a in draft.activities}
    real_ids = {a.id for a in draft.activities if not a.is_dummy}
    outputs = set(draft.requested_outputs)
    uses_estimates = method in (CalculationMethod.PERT, CalculationMethod.BOTH)
    kinds = {q.kind for q in draft.probability_queries}

    if method is not None and not uses_estimates:
        bad = sorted(outputs & ESTIMATE_OUTPUTS)
        if bad:
            issues.append(_err(IssueCode.PROBABILITY_WITHOUT_ESTIMATES,
                               f"Requested outputs {', '.join(bad)} need PERT three-point estimates, which the project lacks.",
                               field_path="requested_outputs", needs_user_clarification=True))
    if RequestedOutput.PROBABILITY_QUERY in outputs and not (kinds - {ProbabilityQueryKind.PERCENTILE_TO_DURATION}):
        issues.append(_err(IssueCode.MISSING_QUERY,
                           "PROBABILITY_QUERY was requested but no AT_MOST / AT_LEAST / BETWEEN query is defined.",
                           field_path="probability_queries", needs_user_clarification=True))
    if RequestedOutput.PERCENTILE_DURATION in outputs and ProbabilityQueryKind.PERCENTILE_TO_DURATION not in kinds:
        issues.append(_err(IssueCode.MISSING_QUERY,
                           "PERCENTILE_DURATION was requested but no PERCENTILE_TO_DURATION query (target probability) is defined.",
                           field_path="probability_queries", needs_user_clarification=True))
    if RequestedOutput.ACTIVITY_MAX_DELAY in outputs and not draft.delay_queries:
        issues.append(_err(IssueCode.MISSING_QUERY, "ACTIVITY_MAX_DELAY was requested but delay_queries is empty.",
                           field_path="delay_queries", needs_user_clarification=True))
    for index, act_id in enumerate(draft.delay_queries):
        if act_id not in real_ids:
            issues.append(_err(IssueCode.UNKNOWN_ACTIVITY_REFERENCE,
                               f"delay_queries references '{act_id}', which is not a real activity of the project.",
                               field_path=f"delay_queries[{index}]", activity_ids=[act_id]))

    wants_crashing = RequestedOutput.CRASHING in outputs or draft.crashing is not None
    if wants_crashing:
        if method == CalculationMethod.PERT:
            issues.append(_err(IssueCode.CRASH_DATA_MISSING,
                               "Crashing (time-cost trade-off) works on deterministic durations; the project only has PERT estimates.",
                               field_path="crashing", needs_user_clarification=True))
        missing = [a.id for a in draft.activities if not a.is_dummy and a.crash is None]
        if missing:
            issues.append(_err(IssueCode.CRASH_DATA_MISSING,
                               f"Crashing needs minimum duration, normal cost and minimum cost for every activity; missing for: "
                               f"{', '.join(missing)}.",
                               field_path="activities", activity_ids=missing, needs_user_clarification=True,
                               suggestion="If an activity cannot be shortened, set crash_duration = duration and crash_cost = normal_cost."))
    if draft.crashing is not None:
        durations = [p.duration for p in draft.crashing.indirect_costs]
        for dup, count in Counter(durations).items():
            if count > 1:
                issues.append(_err(IssueCode.INVALID_CRASH_DATA, f"Indirect cost for duration {dup} is given {count} times.",
                                   field_path="crashing.indirect_costs"))
        if draft.crashing.step <= 0:
            issues.append(_err(IssueCode.INVALID_CRASH_DATA, "crashing.step must be positive.", field_path="crashing.step"))

    query_ids = {q.id for q in draft.probability_queries if q.id}
    for qi, question in enumerate(draft.questions):
        for ref in question.probability_query_ids:
            if ref not in query_ids:
                issues.append(_err(IssueCode.UNKNOWN_ACTIVITY_REFERENCE,
                                   f"Question '{question.label}' references unknown probability query id '{ref}'.",
                                   field_path=f"questions[{qi}].probability_query_ids"))
        for ref in question.delay_activity_ids:
            if ref not in ids:
                issues.append(_err(IssueCode.UNKNOWN_ACTIVITY_REFERENCE,
                                   f"Question '{question.label}' references unknown activity '{ref}'.",
                                   field_path=f"questions[{qi}].delay_activity_ids", activity_ids=[ref]))

    unit = draft.metadata.time_unit
    if unit.code == TimeUnitCode.CUSTOM and not (unit.singular and unit.plural):
        issues.append(_err(IssueCode.INVALID_TIME_UNIT, "A custom time unit needs both `singular` and `plural` labels.",
                           field_path="metadata.time_unit", needs_user_clarification=True))
    return issues


def collect_project_issues(draft: ProjectDraft) -> tuple[list[ValidationIssue], CalculationMethod | None]:
    """Every semantic problem of a draft/project plus the resolved calculation method."""
    from pm_mcp.engine.network import analyze_network

    if not draft.activities:
        return [_err(IssueCode.EMPTY_PROJECT, "The project has no activities.", field_path="activities",
                     needs_user_clarification=True)], None
    issues = _activity_issues(draft)
    _, network_issues = analyze_network(draft.activities)
    issues.extend(network_issues)
    method, method_issues = infer_calculation_method(draft)
    issues.extend(method_issues)
    issues.extend(_query_issues(draft, method))
    issues.extend(_request_issues(draft, method))
    return issues, method


def effective_outputs(draft: ProjectDraft, method: CalculationMethod) -> list[RequestedOutput]:
    """Expand FULL_REPORT into the concrete outputs the data supports, preserving a stable order."""
    requested = set(draft.requested_outputs)
    if RequestedOutput.FULL_REPORT in requested:
        requested |= {
            RequestedOutput.GANTT,
            RequestedOutput.NETWORK_DIAGRAM,
            RequestedOutput.EARLY_LATE_TIMES,
            RequestedOutput.SLACK,
            RequestedOutput.CRITICAL_PATH,
            RequestedOutput.EXPECTED_DURATION,
        }
        if method in (CalculationMethod.PERT, CalculationMethod.BOTH):
            requested |= {RequestedOutput.VARIANCE, RequestedOutput.ACTIVITY_ESTIMATES}
        kinds = {q.kind for q in draft.probability_queries}
        if kinds - {ProbabilityQueryKind.PERCENTILE_TO_DURATION}:
            requested.add(RequestedOutput.PROBABILITY_QUERY)
        if ProbabilityQueryKind.PERCENTILE_TO_DURATION in kinds:
            requested.add(RequestedOutput.PERCENTILE_DURATION)
        if draft.delay_queries:
            requested.add(RequestedOutput.ACTIVITY_MAX_DELAY)
        if draft.crashing is not None:
            requested.add(RequestedOutput.CRASHING)
    requested.discard(RequestedOutput.FULL_REPORT)
    return [o for o in RequestedOutput if o in requested]
