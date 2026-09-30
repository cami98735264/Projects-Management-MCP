"""Application services behind the MCP tools. Stateless, typed in / typed out, never raise to the caller.

Every service that takes a project accepts a :class:`ProjectDraft` and normalizes it first, so a tool can never
run on unvalidated data and never trusts numbers computed elsewhere (e.g. by the LLM).
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Generic, TypeVar

from pydantic import BaseModel, Field

from pm_mcp.domain.errors import (
    CalculationConsistencyError,
    DomainValidationError,
    IssueCode,
    ValidationIssue,
)
from pm_mcp.domain.models import (
    ArrowArc,
    Assumption,
    DurationSource,
    ProbabilityMethod,
    ProbabilityQuery,
    ProbabilityQueryKind,
    Project,
    ProjectDraft,
    ReportLanguage,
    VarianceStrategy,
)
from pm_mcp.engine.aoa import ArrowNetworkConversion, convert_arrow_network
from pm_mcp.engine.cpm import CpmResult, calculate_cpm, resolve_durations
from pm_mcp.engine.crashing import CrashingResult, calculate_crashing_schedule
from pm_mcp.engine.gantt import GanttSchedule, generate_gantt_schedule
from pm_mcp.engine.network import ActivityNetwork, build_activity_network
from pm_mcp.engine.pert import (
    CriticalPathVarianceResult,
    PertEstimatesResult,
    aggregate_critical_path_variance,
    calculate_pert_estimates,
)
from pm_mcp.engine.probability import (
    PercentileResult,
    ProbabilityResult,
    calculate_completion_probability,
    calculate_percentile_duration,
)
from pm_mcp.engine.validation import validate_calculations
from pm_mcp.ingestion.normalize import NormalizationResult, normalize_project_input
from pm_mcp.logging_config import get_logger, log_stage
from pm_mcp.output.workbook import write_solution_workbook
from pm_mcp.output.workbook_validation import WorkbookValidationReport, validate_workbook
from pm_mcp.pipeline import solve_project
from pm_mcp.solution import CalculationValidationReport, ProjectSolution, QuestionAnswer

_log = get_logger(__name__)
T = TypeVar("T")


class Envelope(BaseModel, Generic[T]):
    ok: bool
    result: T | None = None
    issues: list[ValidationIssue] = Field(default_factory=list)
    inferences: list[Assumption] = Field(default_factory=list)


def _guard(fn: Callable[[], Envelope]) -> Envelope:
    try:
        return fn()
    except DomainValidationError as exc:
        return Envelope(ok=False, issues=exc.issues)
    except CalculationConsistencyError as exc:
        failures = "; ".join(f"{c.name}: {c.detail}" for c in exc.report.checks if not c.passed)
        return Envelope(ok=False, issues=[ValidationIssue(code=IssueCode.CALCULATION_INCONSISTENCY, message=failures)])
    except (ValueError, TypeError) as exc:
        return Envelope(ok=False, issues=[ValidationIssue(code=IssueCode.SCHEMA, message=str(exc))])


def _with_project(draft: ProjectDraft, fn: Callable[[Project], object]) -> Envelope:
    norm = normalize_project_input(draft)
    if not norm.ok:
        return Envelope(ok=False, issues=norm.issues, inferences=norm.inferences)
    envelope = _guard(lambda: Envelope(ok=True, result=fn(norm.project)))
    envelope.inferences = norm.inferences
    envelope.issues = [*norm.issues, *envelope.issues]
    return envelope


def normalize(draft: ProjectDraft) -> NormalizationResult:
    return normalize_project_input(draft)


def convert_arrows(arcs: list[ArrowArc], keep_dummies: bool, language: ReportLanguage) -> Envelope[ArrowNetworkConversion]:
    return _guard(lambda: Envelope(ok=True, result=convert_arrow_network(arcs, keep_dummies, language)))


def network(draft: ProjectDraft) -> Envelope[ActivityNetwork]:
    return _with_project(draft, lambda p: build_activity_network(p.activities))


def pert_estimates(draft: ProjectDraft) -> Envelope[PertEstimatesResult]:
    return _with_project(draft, lambda p: calculate_pert_estimates(p.activities))


def cpm(draft: ProjectDraft, duration_source: DurationSource | None = None) -> Envelope[CpmResult]:
    def run(p: Project) -> CpmResult:
        source = duration_source or (DurationSource.EXPECTED_DURATION if p.uses_estimates else DurationSource.DURATION)
        expected = calculate_pert_estimates(p.activities).expected_durations() if source == DurationSource.EXPECTED_DURATION else None
        return calculate_cpm(build_activity_network(p.activities), p.activities,
                             resolve_durations(p.activities, source, expected), source)
    return _with_project(draft, run)


def critical_path_variance(draft: ProjectDraft, strategy: VarianceStrategy | None = None) -> Envelope[CriticalPathVarianceResult]:
    def run(p: Project) -> CriticalPathVarianceResult:
        estimates = calculate_pert_estimates(p.activities)
        schedule = calculate_cpm(build_activity_network(p.activities), p.activities,
                                 resolve_durations(p.activities, DurationSource.EXPECTED_DURATION, estimates.expected_durations()),
                                 DurationSource.EXPECTED_DURATION)
        return aggregate_critical_path_variance(schedule, estimates, strategy or p.options.variance_strategy)
    return _with_project(draft, run)


def completion_probability(expected_duration: float, standard_deviation: float, query: ProbabilityQuery,
                           method: ProbabilityMethod) -> Envelope[ProbabilityResult]:
    return _guard(lambda: Envelope(ok=True, result=calculate_completion_probability(expected_duration, standard_deviation, query, method)))


def percentile_duration(expected_duration: float, standard_deviation: float, target_probability: float,
                        method: ProbabilityMethod, query_id: str | None = None) -> Envelope[PercentileResult]:
    def run() -> Envelope:
        query = ProbabilityQuery(id=query_id, kind=ProbabilityQueryKind.PERCENTILE_TO_DURATION, target_probability=target_probability)
        if not 0 < target_probability < 1:
            raise ValueError(f"target_probability must be strictly between 0 and 1 (got {target_probability}); use 0.98 for 98 %")
        return Envelope(ok=True, result=calculate_percentile_duration(expected_duration, standard_deviation, query, method))
    return _guard(run)


def gantt(draft: ProjectDraft) -> Envelope[GanttSchedule]:
    def run(p: Project) -> GanttSchedule:
        schedule = cpm(p).result
        return generate_gantt_schedule(schedule, p.metadata.time_unit, p.metadata.language, p.options.include_dummies_in_gantt)
    return _with_project(draft, run)


def crashing(draft: ProjectDraft) -> Envelope[CrashingResult]:
    return _with_project(draft, lambda p: calculate_crashing_schedule(build_activity_network(p.activities), p.activities,
                                                                       p.crashing, p.metadata.language))


def solve(draft: ProjectDraft) -> Envelope[ProjectSolution]:
    return _with_project(draft, solve_project)


def check_calculations(draft: ProjectDraft, solution: ProjectSolution | None = None) -> Envelope[CalculationValidationReport]:
    def run(p: Project) -> CalculationValidationReport:
        if solution is None:
            return solve_project(p).validation
        return validate_calculations(p, solution)
    return _with_project(draft, run)


def resolve_output_path(output_path: str) -> Path:
    path = Path(output_path)
    if not path.is_absolute():
        path = Path(os.environ.get("PM_MCP_OUTPUT_DIR", Path.cwd() / "output")) / path
    return path.resolve()


class WorkbookGenerationResult(BaseModel):
    ok: bool
    path: str | None = None
    sheets: list[str] = Field(default_factory=list)
    answers: list[QuestionAnswer] = Field(default_factory=list)
    calculation_validation: CalculationValidationReport | None = None
    workbook_validation: WorkbookValidationReport | None = None
    issues: list[ValidationIssue] = Field(default_factory=list)
    inferences: list[Assumption] = Field(default_factory=list)
    invalid_workbook_path: str | None = None
    notes: list[str] = Field(default_factory=list)


def generate_workbook(draft: ProjectDraft, output_path: str, overwrite: bool = False) -> WorkbookGenerationResult:
    norm = normalize_project_input(draft)
    if not norm.ok:
        return WorkbookGenerationResult(ok=False, issues=norm.issues, inferences=norm.inferences)
    path = resolve_output_path(output_path)
    base = WorkbookGenerationResult(ok=False, issues=list(norm.issues), inferences=norm.inferences)
    if path.suffix.lower() != ".xlsx":
        base.issues.append(ValidationIssue(code=IssueCode.OUTPUT_PATH, message=f"output path must end in .xlsx: {path}"))
        return base
    if path.exists() and not overwrite:
        base.issues.append(ValidationIssue(code=IssueCode.OUTPUT_PATH, message=f"{path} already exists; pass overwrite=true to replace it"))
        return base

    envelope = _guard(lambda: Envelope(ok=True, result=solve_project(norm.project)))
    if not envelope.ok:
        base.issues += envelope.issues
        return base
    solution: ProjectSolution = envelope.result

    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.stem}.tmp.xlsx")
    build = write_solution_workbook(solution, temporary)
    log_stage(_log, "workbook_written", path=str(temporary), sheets=build.sheet_names, formulas=len(build.expected_values))
    report = validate_workbook(temporary, build.sheet_names, build.expected_values)
    log_stage(_log, "workbook_validated", passed=report.passed, issues=len(report.issues),
              formulas=report.formula_count, evaluated=report.evaluated_formula_count)
    result = WorkbookGenerationResult(
        ok=report.passed, sheets=build.sheet_names, answers=solution.answers, calculation_validation=solution.validation,
        workbook_validation=report, issues=[*norm.issues, *solution.warnings], inferences=norm.inferences, notes=build.notes)
    if report.passed:
        os.replace(temporary, path)
        report.path = result.path = str(path)
    else:
        invalid = path.with_name(f"{path.stem}.invalid.xlsx")
        os.replace(temporary, invalid)
        result.invalid_workbook_path = str(invalid)
        result.issues.append(ValidationIssue(code=IssueCode.WORKBOOK_INVALID,
                                             message=f"generated workbook failed validation ({len(report.issues)} issue(s)); "
                                                     f"kept for inspection at {invalid}"))
    return result


def check_workbook(path: str, draft: ProjectDraft | None = None) -> WorkbookValidationReport:
    """Validate any workbook; with a project, also re-generate expectations and compare the values."""
    resolved = resolve_output_path(path)
    if draft is None:
        return validate_workbook(resolved)
    norm = normalize_project_input(draft)
    if not norm.ok:
        report = validate_workbook(resolved)
        report.passed = False
        return report
    solution = solve_project(norm.project)
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        reference = write_solution_workbook(solution, Path(tmp) / "reference.xlsx")
    return validate_workbook(resolved, reference.sheet_names, reference.expected_values)
