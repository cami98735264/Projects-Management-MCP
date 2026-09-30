import math

import pytest

from pm_mcp.domain.errors import IssueCode
from pm_mcp.domain.models import ProbabilityMethod, ProbabilityQuery
from pm_mcp.engine.probability import (
    calculate_completion_probability,
    calculate_percentile_duration,
    normal_cdf,
    normal_ppf,
    table_cdf,
    table_quantile,
)

SIGMA_37_9 = math.sqrt(37 / 9)
SIGMA_14_9 = math.sqrt(14 / 9)


@pytest.mark.parametrize("z, expected", [(0, 0.5), (1.96, 0.9750021048517795), (2.05, 0.9798177845942956),
                                         (-1.0, 0.15865525393145707), (3.0, 0.9986501019683699)])
def test_normal_cdf_known_values(z, expected):
    assert math.isclose(normal_cdf(z), expected, rel_tol=1e-12)


@pytest.mark.parametrize("p", [1e-9, 0.001, 0.02, 0.1, 0.5, 0.9, 0.975, 0.98, 0.999, 1 - 1e-9])
def test_normal_ppf_round_trip(p):
    assert math.isclose(normal_cdf(normal_ppf(p)), p, rel_tol=1e-12, abs_tol=1e-15)


def test_normal_ppf_known_values():
    assert math.isclose(normal_ppf(0.975), 1.959963984540054, rel_tol=1e-12)
    assert math.isclose(normal_ppf(0.98), 2.053748910631823, rel_tol=1e-12)
    with pytest.raises(ValueError):
        normal_ppf(1.0)


def test_against_scipy_when_available():
    stats = pytest.importorskip("scipy.stats")
    for i in range(-400, 401, 7):
        z = i / 100
        assert math.isclose(normal_cdf(z), stats.norm.cdf(z), rel_tol=1e-12, abs_tol=1e-15)
    for i in range(1, 1000, 13):
        p = i / 1000
        assert math.isclose(normal_ppf(p), stats.norm.ppf(p), rel_tol=1e-10, abs_tol=1e-12)


def test_table_convention_reproduces_reference_values():
    assert table_cdf((27 - 25) / SIGMA_14_9) == 0.9452  # Z 1.6036 → 1.60
    assert table_cdf(-1.6036) == 0.0548
    assert table_quantile(0.90) == 1.29   # Φ(1.28)=0.8997 < 0.90 ≤ Φ(1.29)=0.9015
    assert table_quantile(0.98) == 2.06   # Φ(2.05)=0.9798 < 0.98 ≤ Φ(2.06)=0.9803
    assert table_quantile(0.5) == 0.0
    assert table_quantile(0.10) == -1.28


def q(**kw):
    return ProbabilityQuery(**kw)


def test_exercise_probabilities():
    # t_e = 15, σ = √(37/9). Reference values from scipy.stats.norm (independent implementation):
    #   P(T ≤ 13) = Φ(−0.98639) = 0.161969923;  P(T ≥ 16) = 1 − Φ(0.49320) = 0.310936712
    #   P(16 ≤ T ≤ 20) = Φ(2.46598) − Φ(0.49320) = 0.304104856
    at_most = calculate_completion_probability(15, SIGMA_37_9, q(kind="AT_MOST", upper_bound=13))
    at_least = calculate_completion_probability(15, SIGMA_37_9, q(kind="AT_LEAST", lower_bound=16))
    between = calculate_completion_probability(15, SIGMA_37_9, q(kind="BETWEEN", lower_bound=16, upper_bound=20))
    assert math.isclose(at_most.probability_exact, 0.16196992279926703, rel_tol=1e-9)
    assert math.isclose(at_least.probability_exact, 0.31093671216537044, rel_tol=1e-9)
    assert math.isclose(between.probability_exact, 0.3041048557312799, rel_tol=1e-9)
    # Table: Φ(−0.99)=0.1611; 1 − Φ(0.49)=1 − 0.6879; Φ(2.47) − Φ(0.49) = 0.9932 − 0.6879
    assert (at_most.probability_table, at_least.probability_table, between.probability_table) == (0.1611, 0.3121, 0.3053)
    assert at_most.z_computations[0].z_table == -0.99


def test_exercise_contract_duration_98_percent():
    result = calculate_percentile_duration(15, SIGMA_37_9, q(kind="PERCENTILE_TO_DURATION", target_probability=0.98))
    # Z = Φ⁻¹(0.98) = 2.0537489 → T = 15 + 2.0537489·2.0275875 = 19.1641556 (scipy); table Z 2.06 → 19.1768303
    assert math.isclose(result.z_exact, 2.0537489106318225, rel_tol=1e-10)
    assert math.isclose(result.duration_exact, 19.164155640077347, rel_tol=1e-10)
    assert math.isclose(result.duration_table, 15 + 2.06 * SIGMA_37_9, rel_tol=1e-12)
    assert result.duration_whole_units == 20 and result.duration_whole_units_table == 20


def test_reference_example_probabilities_with_table_method():
    method = ProbabilityMethod.TABLE
    p27 = calculate_completion_probability(25, SIGMA_14_9, q(kind="AT_MOST", upper_bound=27), method)
    p25 = calculate_completion_probability(25, SIGMA_14_9, q(kind="AT_MOST", upper_bound=25), method)
    p23 = calculate_completion_probability(25, SIGMA_14_9, q(kind="AT_MOST", upper_bound=23), method)
    more = calculate_completion_probability(25, SIGMA_14_9, q(kind="AT_LEAST", lower_bound=27), method)
    between = calculate_completion_probability(25, SIGMA_14_9, q(kind="BETWEEN", lower_bound=25, upper_bound=27), method)
    assert [r.probability for r in (p27, p25, p23, more, between)] == [0.9452, 0.5, 0.0548, 0.0548, 0.4452]
    pct = calculate_percentile_duration(25, SIGMA_14_9, q(kind="PERCENTILE_TO_DURATION", target_probability=0.9), method)
    assert pct.z_table == 1.29
    assert round(pct.duration, 4) == 26.6089 and pct.duration_whole_units == 27


def test_degenerate_distribution():
    result = calculate_completion_probability(10, 0.0, q(kind="AT_LEAST", lower_bound=10))
    assert result.probability_exact == 1.0 and result.warnings[0].code == IssueCode.DEGENERATE_DISTRIBUTION
    pct = calculate_percentile_duration(10, 0.0, q(kind="PERCENTILE_TO_DURATION", target_probability=0.95))
    assert pct.duration == 10 and pct.z_exact is None


def test_wrong_query_kinds_raise():
    with pytest.raises(ValueError):
        calculate_completion_probability(10, 1, q(kind="PERCENTILE_TO_DURATION", target_probability=0.5))
    with pytest.raises(ValueError):
        calculate_percentile_duration(10, 1, q(kind="AT_MOST", upper_bound=3))
