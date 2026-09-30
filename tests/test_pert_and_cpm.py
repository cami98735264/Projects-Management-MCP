"""PERT and CPM unit tests. Expected values are derived by hand in the comments, not copied from a key."""

import math
from fractions import Fraction as F

import pytest

from helpers import act, arcs_from, load_fixture, normalized
from pm_mcp.domain.errors import DomainValidationError, IssueCode
from pm_mcp.domain.models import DurationSource, ProjectDraft, VarianceStrategy
from pm_mcp.engine.aoa import convert_arrow_network
from pm_mcp.engine.cpm import calculate_cpm, resolve_durations
from pm_mcp.engine.network import build_activity_network
from pm_mcp.engine.pert import activity_variance, aggregate_critical_path_variance, calculate_pert_estimates, expected_time


def schedule(activities, source=DurationSource.DURATION):
    network = build_activity_network(activities)
    expected = calculate_pert_estimates(activities).expected_durations() if source == DurationSource.EXPECTED_DURATION else None
    return calculate_cpm(network, activities, resolve_durations(activities, source, expected), source)


def table(cpm):
    return {r.activity_id: (r.early_start, r.early_finish, r.late_start, r.late_finish, r.total_slack) for r in cpm.activities}


def test_pert_formulas_exact():
    assert expected_time(F(1), F(2), F(3)) == 2 and activity_variance(F(1), F(3)) == F(1, 9)   # (1+3+8)/6, (2/6)²
    assert expected_time(F(2), F(3), F(10)) == 4 and activity_variance(F(2), F(10)) == F(16, 9)  # (2+10+12)/6, (8/6)²
    assert expected_time(F(1), F(2), F(4)) == F(13, 6)  # (1+4+8)/6 — stays exact


def test_pert_estimates_workings_and_missing_data():
    est = calculate_pert_estimates([act("A", est=(1, 2, 3)), act("d", dummy=True)])
    a = est.estimates[0]
    assert a.expected_duration_working == "(1 + 3 + 4·2) / 6 = 2"
    assert a.variance_working == "[(3 − 1) / 6]² = 1/9"
    assert est.estimates[1].expected_duration == 0
    with pytest.raises(DomainValidationError):
        calculate_pert_estimates([act("A", duration=3)])


def test_exercise_schedule_hand_derived():
    activities = convert_arrow_network(arcs_from(load_fixture("taller1_transcription.json"))).activities
    cpm = schedule(activities, DurationSource.EXPECTED_DURATION)
    # t_e: A2 B3 C4 D5 E2 F1 G1 H3 I4 J5.
    # Forward: A 0–2; B 2–5; C 2–6; D 5–10; F,G start max(EF C=6, EF B=5)=6 → F 6–7, G 6–7; H 7–10;
    #          I starts max(G 7, F 7)=7 → 7–11; J 10–15; E 10–12. Project = max(E 12, I 11, J 15) = 15.
    # Backward: J 10–15; E 13–15; I 11–15; H LF=10 → 7; D LF=13 → 8; F LF=min(H 7, I 11)=7 → 6; G LF=11 → 10;
    #           C LF=min(F 6, G 10)=6 → 2; B LF=min(D 8, F 6, G 10)=6 → 3; A LF=min(B 3, C 2)=2 → 0.
    assert cpm.project_duration == 15
    assert table(cpm) == {
        "A": (0, 2, 0, 2, 0), "B": (2, 5, 3, 6, 1), "C": (2, 6, 2, 6, 0), "D": (5, 10, 8, 13, 3), "E": (10, 12, 13, 15, 3),
        "F": (6, 7, 6, 7, 0), "G": (6, 7, 10, 11, 4), "H": (7, 10, 7, 10, 0), "I": (7, 11, 11, 15, 4), "J": (10, 15, 10, 15, 0),
    }
    assert cpm.critical_paths == [["A", "C", "F", "H", "J"]]
    variance = aggregate_critical_path_variance(cpm, calculate_pert_estimates(activities))
    # σ² = A 1/9 + C 16/9 + F 0 + H 4/9 + J 16/9 = 37/9
    assert variance.total_variance == F(37, 9)
    assert math.isclose(variance.standard_deviation, math.sqrt(37 / 9))
    assert variance.variance_working == "σ² = 1/9 + 16/9 + 0 + 4/9 + 16/9 = 37/9"


def test_reference_gantt_example_deterministic():
    # A5 B1 C2(B) D4(A,C) E6(A,C) F3(D,E): A 0–5, B 0–1, C 1–3, D 5–9, E 5–11, F 11–14.
    # Backward: F 11–14; D LF 11 → 7; E LF 11 → 5; C LF min(7,5)=5 → 3; A LF 5 → 0; B LF 3 → 2.
    cpm = schedule([act("A", [], 5), act("B", [], 1), act("C", ["B"], 2), act("D", ["A", "C"], 4),
                    act("E", ["A", "C"], 6), act("F", ["D", "E"], 3)])
    assert cpm.project_duration == 14
    assert {k: v[4] for k, v in table(cpm).items()} == {"A": 0, "B": 2, "C": 2, "D": 2, "E": 0, "F": 0}
    assert cpm.critical_paths == [["A", "E", "F"]]


def synthetic_days():
    return [
        act("S1", [], 2), act("S2", [], 2), act("S3", [], 3), act("P", ["S1"], 5), act("Q", ["S2"], 6), act("R", ["S3"], 2),
        act("T", ["P", "Q"], 4), act("U", ["R"], 3), act("V", ["Q"], 1), act("W", ["T"], 4), act("X", ["U", "V"], 7),
        act("Y", ["T"], 1),
    ]


def test_synthetic_two_critical_paths_multi_start_end():
    cpm = schedule(synthetic_days())
    # Forward: S1 0–2 S2 0–2 S3 0–3 P 2–7 Q 2–8 R 3–5 T max(7,8)=8–12 U 5–8 V 8–9 W 12–16 X max(8,9)=9–16 Y 12–13 → 16
    # Backward: W 12–16 X 9–16 Y 15–16 T LF min(12,15)=12→8 U LF 9→6 V LF 9→8 R LF 6→4 P LF 8→3
    #           Q LF min(T 8, V 8)=8→2 S1 LF 3→1 S2 LF 2→0 S3 LF 4→1
    assert cpm.project_duration == 16
    slack = {k: v[4] for k, v in table(cpm).items()}
    assert slack == {"S1": 1, "S2": 0, "S3": 1, "P": 1, "Q": 0, "R": 1, "T": 0, "U": 1, "V": 0, "W": 0, "X": 0, "Y": 3}
    assert cpm.critical_paths == [["S2", "Q", "T", "W"], ["S2", "Q", "V", "X"]]
    free = {r.activity_id: r.free_slack for r in cpm.activities}
    # free slack = min ES(successors) − EF: S1 → P.ES 2 − 2 = 0; P → T.ES 8 − 7 = 1; U → X.ES 9 − 8 = 1; Y → 16 − 13 = 3
    assert (free["S1"], free["P"], free["U"], free["Y"]) == (0, 1, 1, 3)


def test_fractional_durations_detect_critical_exactly():
    cpm = schedule([act("A", [], "1/3"), act("B", [], "1/3"), act("C", ["A"], "1/3"), act("D", ["B"], F(2, 3) - F(1, 3)),
                    act("E", ["C", "D"], 0.1)])
    # A→C and B→D both total 2/3; float arithmetic would risk 1e-16 residual slack
    assert cpm.project_duration == F(2, 3) + F(1, 10)
    assert cpm.critical_paths == [["A", "C", "E"], ["B", "D", "E"]]


def test_dummy_nodes_hidden_in_paths():
    cpm = schedule([act("A", [], 2), act("B", [], 1), act("x", ["B"], dummy=True, duration=0), act("C", ["A", "x"], 3)])
    assert cpm.critical_paths == [["A", "C"]]
    assert "x" not in cpm.critical_activities


def test_multiple_critical_paths_variance_strategies():
    # A (0,2,4): t_e 2, σ² (4/6)² = 4/9; B (1,2,3): t_e 2, σ² 1/9; C (1,1,1): t_e 1, σ² 0 — paths A-C and B-C both 3.
    activities = [act("A", est=(0, 2, 4)), act("B", est=(1, 2, 3)), act("C", ["A", "B"], est=(1, 1, 1))]
    cpm = schedule(activities, DurationSource.EXPECTED_DURATION)
    estimates = calculate_pert_estimates(activities)
    worst = aggregate_critical_path_variance(cpm, estimates, VarianceStrategy.MAX_VARIANCE_PATH)
    total = aggregate_critical_path_variance(cpm, estimates, VarianceStrategy.ALL_CRITICAL_ACTIVITIES)
    assert worst.total_variance == F(4, 9) and worst.selected_activities == ["A", "C"]
    assert total.total_variance == F(5, 9)
    assert worst.warnings[0].code == IssueCode.MULTIPLE_CRITICAL_PATHS
    assert [p.variance for p in worst.critical_paths] == [F(4, 9), F(1, 9)]


def test_reference_pert_example_schedule():
    project = normalized(ProjectDraft(activities=convert_arrow_network(arcs_from(load_fixture("pert_example_transcription.json"))).activities))
    cpm = schedule(project.activities, DurationSource.EXPECTED_DURATION)
    # t_e: A3 B3 C5 D4 E8 F6 G5 H9 I3 (a+b+4m)/6. Forward: A 0–3 B 3–6 E 3–11 C 6–11 D 6–10 F max(10,11)=11–17
    # H 11–20 G max(11,17)=17–22 I max(22,20)=22–25. Backward: I 22; G 17; H 13 (slack 2); F 11; C LF 17 → 12 (slack 6);
    # D LF 11 → 7 (1); E LF min(F 11, H 13)=11 → 3; B LF min(C 12, D 7)=7 → 4 (1); A LF min(B 4, E 3)=3 → 0.
    assert table(cpm) == {
        "A": (0, 3, 0, 3, 0), "B": (3, 6, 4, 7, 1), "C": (6, 11, 12, 17, 6), "D": (6, 10, 7, 11, 1), "E": (3, 11, 3, 11, 0),
        "F": (11, 17, 11, 17, 0), "G": (17, 22, 17, 22, 0), "H": (11, 20, 13, 22, 2), "I": (22, 25, 22, 25, 0),
    }
    variance = aggregate_critical_path_variance(cpm, calculate_pert_estimates(project.activities))
    # A 4/9 + E 1/9 + F 4/9 + G 1/9 + I 4/9 = 14/9
    assert variance.total_variance == F(14, 9)
