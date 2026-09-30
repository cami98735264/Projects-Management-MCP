"""Normal-distribution completion probability and percentile (inverse) queries.

Z = (T_p − t_e) / σ  (formula image in the exercise document and the 'PERT Probabilístico' sheet).

Two numeric conventions are always computed side by side:
* exact  — Φ via ``math.erfc`` (double precision) and Φ⁻¹ via Acklam's rational approximation refined with
  Halley steps (|error| < 1e-15 in the central region);
* table  — the convention of the reference material's worked example: Z rounded to 2 decimals, Φ read to
  4 decimals; for a target probability, the smallest 2-decimal Z whose tabulated Φ reaches it (the worked
  example uses Z = 1.29 for 0.90), and required durations rounded *up* to whole time units.
"""

from __future__ import annotations

import math
from fractions import Fraction

from pydantic import BaseModel, Field

from pm_mcp.domain.errors import IssueCode, IssueSeverity, ValidationIssue
from pm_mcp.domain.models import ProbabilityMethod, ProbabilityQuery, ProbabilityQueryKind
from pm_mcp.domain.numbers import ceil_tolerant, decimal_text, round_half_up

_SQRT2 = math.sqrt(2.0)
_SQRT2PI = math.sqrt(2.0 * math.pi)

_A = (-3.969683028665376e01, 2.209460984245205e02, -2.759285104469687e02, 1.383577518672690e02,
      -3.066479806614716e01, 2.506628277459239e00)
_B = (-5.447609879822406e01, 1.615858368580409e02, -1.556989798598866e02, 6.680131188771972e01,
      -1.328068155288572e01)
_C = (-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e00, -2.549732539343734e00,
      4.374664141464968e00, 2.938163982698783e00)
_D = (7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e00, 3.754408661907416e00)


def normal_cdf(z: float) -> float:
    return 0.5 * math.erfc(-z / _SQRT2)


def normal_ppf(p: float) -> float:
    if not 0.0 < p < 1.0:
        raise ValueError(f"probability must be strictly between 0 and 1, got {p}")
    low = 0.02425
    if p < low:
        q = math.sqrt(-2.0 * math.log(p))
        x = (((((_C[0] * q + _C[1]) * q + _C[2]) * q + _C[3]) * q + _C[4]) * q + _C[5]) / \
            ((((_D[0] * q + _D[1]) * q + _D[2]) * q + _D[3]) * q + 1.0)
    elif p <= 1.0 - low:
        q = p - 0.5
        r = q * q
        x = (((((_A[0] * r + _A[1]) * r + _A[2]) * r + _A[3]) * r + _A[4]) * r + _A[5]) * q / \
            (((((_B[0] * r + _B[1]) * r + _B[2]) * r + _B[3]) * r + _B[4]) * r + 1.0)
    else:
        q = math.sqrt(-2.0 * math.log(1.0 - p))
        x = -(((((_C[0] * q + _C[1]) * q + _C[2]) * q + _C[3]) * q + _C[4]) * q + _C[5]) / \
            ((((_D[0] * q + _D[1]) * q + _D[2]) * q + _D[3]) * q + 1.0)
    for _ in range(2):
        error = normal_cdf(x) - p
        u = error * _SQRT2PI * math.exp(x * x / 2.0)
        x = x - u / (1.0 + x * u / 2.0)
    return x


def table_z(z: float) -> float:
    return round_half_up(z, 2)


def table_cdf(z: float) -> float:
    """Φ as printed in a 4-decimal standard normal table, looked up at Z rounded to 2 decimals."""
    return round_half_up(normal_cdf(table_z(z)), 4)


def table_quantile(p: float) -> float:
    hundredths = math.ceil(round(normal_ppf(p) * 100, 6))
    while table_cdf(hundredths / 100) < p:
        hundredths += 1
    while table_cdf((hundredths - 1) / 100) >= p:
        hundredths -= 1
    return hundredths / 100


def _num(value: float | Fraction) -> str:
    return decimal_text(value, 4).replace("-", "−")


class ZComputation(BaseModel):
    bound: str = Field(description="'upper' or 'lower'")
    target_duration: float
    z_exact: float | None
    cdf_exact: float = Field(description="P(T ≤ target) with exact Φ")
    z_table: float | None
    cdf_table: float = Field(description="P(T ≤ target) read from a 4-decimal table")
    working: str


class ProbabilityResult(BaseModel):
    query: ProbabilityQuery
    expected_duration: float
    standard_deviation: float
    expression: str
    z_computations: list[ZComputation]
    probability_exact: float
    probability_table: float
    method: ProbabilityMethod
    probability: float = Field(description="Primary answer according to `method`")
    steps: list[str]
    warnings: list[ValidationIssue] = Field(default_factory=list)


class PercentileResult(BaseModel):
    query: ProbabilityQuery
    target_probability: float
    expected_duration: float
    standard_deviation: float
    z_exact: float | None
    duration_exact: float
    z_table: float | None
    cdf_at_z_table: float | None
    duration_table: float
    duration_whole_units_exact: int
    duration_whole_units_table: int
    method: ProbabilityMethod
    duration: float = Field(description="Primary (unrounded) duration according to `method`")
    duration_whole_units: int = Field(description="Primary duration rounded up to whole time units")
    steps: list[str]
    warnings: list[ValidationIssue] = Field(default_factory=list)


def _z(target: float, te: float, sigma: float, bound: str) -> ZComputation:
    if sigma == 0:
        cdf = 1.0 if target >= te else 0.0
        return ZComputation(bound=bound, target_duration=target, z_exact=None, cdf_exact=cdf, z_table=None,
                            cdf_table=cdf, working=f"σ = 0 → P(T ≤ {_num(target)}) = {cdf:g}")
    z = (target - te) / sigma
    return ZComputation(
        bound=bound, target_duration=target, z_exact=z, cdf_exact=normal_cdf(z), z_table=table_z(z),
        cdf_table=table_cdf(z),
        working=f"Z = ({_num(target)} − {_num(te)}) / {_num(sigma)} = {_num(z)}; "
                f"Φ({_num(z)}) = {normal_cdf(z):.4f}; tabla Φ({table_z(z):.2f}) = {table_cdf(z):.4f}",
    )


def calculate_completion_probability(
    expected_duration: float | Fraction,
    standard_deviation: float,
    query: ProbabilityQuery,
    method: ProbabilityMethod = ProbabilityMethod.EXACT,
) -> ProbabilityResult:
    if query.kind == ProbabilityQueryKind.PERCENTILE_TO_DURATION:
        raise ValueError("use calculate_percentile_duration for PERCENTILE_TO_DURATION queries")
    if standard_deviation < 0:
        raise ValueError("standard deviation cannot be negative")
    te = float(expected_duration)
    sigma = float(standard_deviation)
    warnings: list[ValidationIssue] = []
    if sigma == 0:
        warnings.append(ValidationIssue(code=IssueCode.DEGENERATE_DISTRIBUTION, severity=IssueSeverity.WARNING,
                                        message="σ = 0: the project duration is deterministic, probabilities are 0 or 1."))
    upper = float(query.upper_bound) if query.upper_bound is not None else None
    lower = float(query.lower_bound) if query.lower_bound is not None else None

    if query.kind == ProbabilityQueryKind.AT_MOST:
        zu = _z(upper, te, sigma, "upper")
        comps = [zu]
        p_exact, p_table = zu.cdf_exact, zu.cdf_table
        expression = f"P(T ≤ {_num(upper)})"
        final = f"P(T ≤ {_num(upper)}) = Φ(Z) = {p_exact:.4f} (tabla: {p_table:.4f})"
    elif query.kind == ProbabilityQueryKind.AT_LEAST:
        zl = _z(lower, te, sigma, "lower")
        comps = [zl]
        if sigma == 0:
            p_exact = p_table = 1.0 if te >= lower else 0.0
        else:
            p_exact = 1.0 - zl.cdf_exact
            p_table = round_half_up(1.0 - zl.cdf_table, 4)
        expression = f"P(T ≥ {_num(lower)})"
        final = f"P(T ≥ {_num(lower)}) = 1 − Φ(Z) = {p_exact:.4f} (tabla: 1 − {zl.cdf_table:.4f} = {p_table:.4f})"
    else:
        zu = _z(upper, te, sigma, "upper")
        zl = _z(lower, te, sigma, "lower")
        comps = [zu, zl]
        if sigma == 0:
            p_exact = p_table = 1.0 if lower <= te <= upper else 0.0
        else:
            p_exact = zu.cdf_exact - zl.cdf_exact
            p_table = round_half_up(zu.cdf_table - zl.cdf_table, 4)
        expression = f"P({_num(lower)} ≤ T ≤ {_num(upper)})"
        final = (f"{expression} = Φ(Z₂) − Φ(Z₁) = {p_exact:.4f} "
                 f"(tabla: {zu.cdf_table:.4f} − {zl.cdf_table:.4f} = {p_table:.4f})")

    steps = [f"t_e = {_num(te)}, σ = {_num(sigma)}", *(c.working for c in comps), final]
    return ProbabilityResult(
        query=query, expected_duration=te, standard_deviation=sigma, expression=expression, z_computations=comps,
        probability_exact=p_exact, probability_table=p_table, method=method,
        probability=p_table if method == ProbabilityMethod.TABLE else p_exact, steps=steps, warnings=warnings,
    )


def calculate_percentile_duration(
    expected_duration: float | Fraction,
    standard_deviation: float,
    query: ProbabilityQuery,
    method: ProbabilityMethod = ProbabilityMethod.EXACT,
) -> PercentileResult:
    if query.kind != ProbabilityQueryKind.PERCENTILE_TO_DURATION or query.target_probability is None:
        raise ValueError("calculate_percentile_duration needs a PERCENTILE_TO_DURATION query with target_probability")
    p = float(query.target_probability)
    te = float(expected_duration)
    sigma = float(standard_deviation)
    warnings: list[ValidationIssue] = []
    if sigma == 0:
        warnings.append(ValidationIssue(code=IssueCode.DEGENERATE_DISTRIBUTION, severity=IssueSeverity.WARNING,
                                        message="σ = 0: every target probability corresponds to the expected duration."))
        whole = ceil_tolerant(te)
        return PercentileResult(
            query=query, target_probability=p, expected_duration=te, standard_deviation=0.0, z_exact=None,
            duration_exact=te, z_table=None, cdf_at_z_table=None, duration_table=te, duration_whole_units_exact=whole,
            duration_whole_units_table=whole, method=method, duration=te, duration_whole_units=whole,
            steps=[f"σ = 0 → T_p = t_e = {_num(te)}"], warnings=warnings)

    z_exact = normal_ppf(p)
    duration_exact = te + z_exact * sigma
    z_tab = table_quantile(p)
    duration_table = te + z_tab * sigma
    whole_exact = ceil_tolerant(duration_exact)
    whole_table = ceil_tolerant(duration_table)
    table_mode = method == ProbabilityMethod.TABLE
    steps = [
        f"t_e = {_num(te)}, σ = {_num(sigma)}, probabilidad objetivo = {p:g}",
        f"Z exacto = Φ⁻¹({p:g}) = {_num(z_exact)} → T_p = {_num(te)} + {_num(z_exact)} × {_num(sigma)} = {_num(duration_exact)}",
        f"Z tabla = {z_tab:.2f} (Φ = {table_cdf(z_tab):.4f} ≥ {p:g}) → T_p = {_num(te)} + {z_tab:.2f} × {_num(sigma)} = {_num(duration_table)}",
        f"Redondeo al entero superior: {whole_table if table_mode else whole_exact}",
    ]
    return PercentileResult(
        query=query, target_probability=p, expected_duration=te, standard_deviation=sigma, z_exact=z_exact,
        duration_exact=duration_exact, z_table=z_tab, cdf_at_z_table=table_cdf(z_tab), duration_table=duration_table,
        duration_whole_units_exact=whole_exact, duration_whole_units_table=whole_table, method=method,
        duration=duration_table if table_mode else duration_exact,
        duration_whole_units=whole_table if table_mode else whole_exact, steps=steps, warnings=warnings,
    )
