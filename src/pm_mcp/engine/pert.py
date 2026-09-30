"""Probabilistic PERT: per-activity expected time and variance, critical-path variance aggregation.

Formulas as given in the reference material (exercise formula images; 'PERT Probabilístico' sheet):
    t_e = (a + b + 4m) / 6
    σ²  = [(b − a) / 6]²
    σ²_project = Σ σ² over the critical activities;  σ = √σ²_project
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from fractions import Fraction

from pydantic import BaseModel, Field

from pm_mcp.domain.errors import DomainValidationError, IssueCode, IssueSeverity, ValidationIssue
from pm_mcp.domain.models import Activity, VarianceStrategy
from pm_mcp.domain.numbers import Exact, decimal_text, fraction_text
from pm_mcp.engine.cpm import CpmResult

EXPECTED_TIME_FORMULA = "t_e = (a + b + 4m) / 6"
VARIANCE_FORMULA = "σ² = [(b − a) / 6]²"


def expected_time(a: Fraction, m: Fraction, b: Fraction) -> Fraction:
    return (a + b + 4 * m) / 6


def activity_variance(a: Fraction, b: Fraction) -> Fraction:
    return ((b - a) / 6) ** 2


class PertActivityEstimate(BaseModel):
    activity_id: str
    name: str | None = None
    is_dummy: bool = False
    optimistic: Exact
    most_likely: Exact
    pessimistic: Exact
    expected_duration: Exact
    variance: Exact
    standard_deviation: float
    expected_duration_working: str
    variance_working: str


class PertEstimatesResult(BaseModel):
    estimates: list[PertActivityEstimate]
    expected_time_formula: str = EXPECTED_TIME_FORMULA
    variance_formula: str = VARIANCE_FORMULA

    def expected_durations(self) -> dict[str, Fraction]:
        return {e.activity_id: e.expected_duration for e in self.estimates}

    def variances(self) -> dict[str, Fraction]:
        return {e.activity_id: e.variance for e in self.estimates}


def calculate_pert_estimates(activities: Sequence[Activity]) -> PertEstimatesResult:
    estimates: list[PertActivityEstimate] = []
    missing: list[str] = []
    for act in activities:
        if act.is_dummy:
            zero = Fraction(0)
            estimates.append(PertActivityEstimate(
                activity_id=act.id, name=act.name, is_dummy=True, optimistic=zero, most_likely=zero, pessimistic=zero,
                expected_duration=zero, variance=zero, standard_deviation=0.0,
                expected_duration_working="dummy: t_e = 0", variance_working="dummy: σ² = 0"))
            continue
        if not act.has_three_point_estimates:
            missing.append(act.id)
            continue
        a, m, b = act.estimates
        te = expected_time(a, m, b)
        var = activity_variance(a, b)
        estimates.append(PertActivityEstimate(
            activity_id=act.id, name=act.name, optimistic=a, most_likely=m, pessimistic=b,
            expected_duration=te, variance=var, standard_deviation=math.sqrt(var),
            expected_duration_working=f"({fraction_text(a)} + {fraction_text(b)} + 4·{fraction_text(m)}) / 6 = {fraction_text(te)}",
            variance_working=f"[({fraction_text(b)} − {fraction_text(a)}) / 6]² = {fraction_text(var)}",
        ))
    if missing:
        raise DomainValidationError([ValidationIssue(
            code=IssueCode.METHOD_DATA_MISSING,
            message=f"PERT estimates (a, m, b) are missing for: {', '.join(missing)}.",
            activity_ids=missing, needs_user_clarification=True)])
    return PertEstimatesResult(estimates=estimates)


class VarianceTerm(BaseModel):
    activity_id: str
    variance: Exact


class PathVariance(BaseModel):
    path: list[str]
    terms: list[VarianceTerm]
    variance: Exact
    standard_deviation: float


class CriticalPathVarianceResult(BaseModel):
    strategy: VarianceStrategy
    expected_project_duration: Exact
    critical_paths: list[PathVariance]
    selected_activities: list[str] = Field(description="Activities whose variances were summed")
    total_variance: Exact
    standard_deviation: float
    variance_working: str
    standard_deviation_working: str
    warnings: list[ValidationIssue] = Field(default_factory=list)


def aggregate_critical_path_variance(
    cpm: CpmResult,
    estimates: PertEstimatesResult,
    strategy: VarianceStrategy = VarianceStrategy.MAX_VARIANCE_PATH,
) -> CriticalPathVarianceResult:
    variances = estimates.variances()
    paths: list[PathVariance] = []
    for path in cpm.critical_paths:
        terms = [VarianceTerm(activity_id=a, variance=variances.get(a, Fraction(0))) for a in path]
        total = sum((t.variance for t in terms), Fraction(0))
        paths.append(PathVariance(path=path, terms=terms, variance=total, standard_deviation=math.sqrt(total)))

    warnings: list[ValidationIssue] = []
    if strategy == VarianceStrategy.MAX_VARIANCE_PATH and paths:
        selected_path = max(paths, key=lambda p: p.variance)
        terms = selected_path.terms
    else:
        crit = [a for a in cpm.critical_activities if a in variances]
        terms = [VarianceTerm(activity_id=a, variance=variances[a]) for a in crit]
    total = sum((t.variance for t in terms), Fraction(0))
    std = math.sqrt(total)

    if len(paths) > 1:
        described = "; ".join(f"{'-'.join(p.path)} (σ² = {fraction_text(p.variance)})" for p in paths)
        warnings.append(ValidationIssue(
            code=IssueCode.MULTIPLE_CRITICAL_PATHS, severity=IssueSeverity.WARNING,
            message=f"There are {len(paths)} critical paths: {described}. Strategy '{strategy}' was applied."))
    if cpm.critical_paths_truncated:
        warnings.append(ValidationIssue(
            code=IssueCode.CRITICAL_PATHS_TRUNCATED, severity=IssueSeverity.WARNING,
            message="Critical path enumeration was truncated; the variance uses the enumerated paths only."))
    return CriticalPathVarianceResult(
        strategy=strategy,
        expected_project_duration=cpm.project_duration,
        critical_paths=paths,
        selected_activities=[t.activity_id for t in terms],
        total_variance=total,
        standard_deviation=std,
        variance_working="σ² = " + " + ".join(fraction_text(t.variance) for t in terms) + f" = {fraction_text(total)}",
        standard_deviation_working=f"σ = √({fraction_text(total)}) = {decimal_text(std)}",
        warnings=warnings,
    )
