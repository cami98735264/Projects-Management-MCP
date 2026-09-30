"""Deterministic solve pipeline: Project → every requested calculation → independent validation → answers."""

from __future__ import annotations

from pm_mcp.domain.errors import CalculationConsistencyError
from pm_mcp.domain.models import DurationSource, ProbabilityQueryKind, Project, RequestedOutput
from pm_mcp.domain.rules import effective_outputs
from pm_mcp.engine.cost_curve import calculate_cost_curve
from pm_mcp.engine.cpm import calculate_cpm, resolve_durations
from pm_mcp.engine.crashing import calculate_crashing_schedule
from pm_mcp.engine.gantt import generate_gantt_schedule
from pm_mcp.engine.network import build_activity_network
from pm_mcp.engine.pert import aggregate_critical_path_variance, calculate_pert_estimates
from pm_mcp.engine.probability import calculate_completion_probability, calculate_percentile_duration
from pm_mcp.engine.validation import validate_calculations
from pm_mcp.logging_config import get_logger, log_stage
from pm_mcp.output.answers import build_answers, delay_text
from pm_mcp.solution import ActivityDelayAnswer, ProjectSolution

_log = get_logger(__name__)


def solve_project(project: Project) -> ProjectSolution:
    """Run every calculation the project's shape calls for. Raises CalculationConsistencyError on any mismatch."""
    acts = project.activities
    lang = project.metadata.language
    outputs = effective_outputs(project, project.calculation_method)

    network = build_activity_network(acts)
    log_stage(_log, "network_built", activities=len(acts), starts=network.start_activities, ends=network.end_activities)

    pert = calculate_pert_estimates(acts) if project.uses_estimates else None
    if pert is not None:
        cpm = calculate_cpm(network, acts, resolve_durations(acts, DurationSource.EXPECTED_DURATION, pert.expected_durations()),
                            DurationSource.EXPECTED_DURATION)
    else:
        cpm = calculate_cpm(network, acts, resolve_durations(acts, DurationSource.DURATION), DurationSource.DURATION)
    cpm_det = (calculate_cpm(network, acts, resolve_durations(acts, DurationSource.DURATION), DurationSource.DURATION)
               if pert is not None and project.uses_deterministic_durations else None)
    log_stage(_log, "cpm_calculated", duration=str(cpm.project_duration), critical_paths=cpm.critical_paths)

    variance = aggregate_critical_path_variance(cpm, pert, project.options.variance_strategy) if pert else None
    probabilities, percentiles = [], []
    if variance is not None:
        method = project.options.probability_method
        for q in project.probability_queries:
            if q.kind == ProbabilityQueryKind.PERCENTILE_TO_DURATION:
                percentiles.append(calculate_percentile_duration(cpm.project_duration, variance.standard_deviation, q, method))
            else:
                probabilities.append(calculate_completion_probability(cpm.project_duration, variance.standard_deviation, q, method))
        log_stage(_log, "pert_calculated", variance=str(variance.total_variance), queries=len(project.probability_queries))

    gantt = generate_gantt_schedule(cpm, project.metadata.time_unit, lang, project.options.include_dummies_in_gantt)
    crashing = (calculate_crashing_schedule(network, acts, project.crashing, lang)
                if RequestedOutput.CRASHING in outputs else None)
    cost_curve = calculate_cost_curve(network, acts, crashing, lang)
    if cost_curve is not None:
        log_stage(_log, "cost_curve_calculated", basis=cost_curve.basis, periods=cost_curve.horizon,
                  total_cost=str(cost_curve.total_cost))

    rows = cpm.by_id()
    delays = [
        ActivityDelayAnswer(
            activity_id=a, total_slack=rows[a].total_slack, free_slack=rows[a].free_slack, is_critical=rows[a].is_critical,
            text=delay_text(lang, a, rows[a].total_slack, project.metadata.time_unit.label(rows[a].total_slack, lang)))
        for a in project.delay_queries
    ]
    warnings = [*network.warnings]
    if variance:
        warnings += variance.warnings
    for r in [*probabilities, *percentiles]:
        warnings += r.warnings
    if crashing:
        warnings += crashing.warnings

    solution = ProjectSolution(
        project=project, effective_outputs=outputs, network=network, pert=pert, cpm=cpm, cpm_deterministic=cpm_det,
        variance=variance, probabilities=probabilities, percentiles=percentiles, gantt=gantt, crashing=crashing, cost_curve=cost_curve,
        delay_answers=delays, warnings=warnings,
    )
    report = validate_calculations(project, solution)
    log_stage(_log, "calculations_validated", passed=report.passed, checks=len(report.checks))
    if not report.passed:
        raise CalculationConsistencyError(report)
    solution.validation = report
    solution.answers = build_answers(solution)
    return solution
