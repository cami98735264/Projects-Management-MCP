"""Structurally different exercises to prove generalization (no dummies / dummy nodes, CPM-only / BOTH,
different units, languages, thresholds, sizes)."""

import random
from fractions import Fraction as F

from helpers import act, normalized
from pm_mcp.domain.models import ProbabilityQuery, ProjectDraft, ProjectMetadata, TimeUnit
from pm_mcp.pipeline import solve_project
from pm_mcp.service import generate_workbook


def launch_plan():
    return [
        act("S1", [], 2), act("S2", [], 2), act("S3", [], 3), act("P", ["S1"], 5), act("Q", ["S2"], 6), act("R", ["S3"], 2),
        act("T", ["P", "Q"], 4), act("U", ["R"], 3), act("V", ["Q"], 1), act("W", ["T"], 4), act("X", ["U", "V"], 7),
        act("Y", ["T"], 1),
    ]


def test_cpm_only_days_english_two_critical_paths(output_dir):
    draft = ProjectDraft(metadata=ProjectMetadata(title="Launch plan", time_unit=TimeUnit(code="day"), language="en"),
                         activities=launch_plan(), requested_outputs=["CRITICAL_PATH", "SLACK", "GANTT"], delay_queries=["P"])
    solution = solve_project(normalized(draft))
    assert solution.project.calculation_method == "CPM" and solution.pert is None and solution.variance is None
    answers = {a.outputs[0]: a.answer for a in solution.answers}
    assert "Critical paths: S2 → Q → T → W; S2 → Q → V → X" in answers["CRITICAL_PATH"]
    assert "Activity P can be delayed up to 1 day" in answers["ACTIVITY_MAX_DELAY"]
    result = generate_workbook(draft, "launch.xlsx", layout="classic")
    assert result.ok and result.sheets == ["Problem", "Activity Data", "CPM", "Gantt", "Final Results"]


def test_both_methods_months_dummy_node_custom_queries(output_dir):
    activities = [act("A", [], 4, est=(2, 4, 6)), act("B", [], 3, est=(1, 3, 8)), act("x", ["B"], dummy=True),
                  act("C", ["A", "x"], 5, est=(4, 5, 9)), act("D", ["B"], 2, est=(1, 2, 3))]
    queries = [ProbabilityQuery(id="late", kind="AT_LEAST", lower_bound=11),
               ProbabilityQuery(id="window", kind="BETWEEN", lower_bound=9, upper_bound=12),
               ProbabilityQuery(id="p75", kind="PERCENTILE_TO_DURATION", target_probability=0.75)]
    draft = ProjectDraft(metadata=ProjectMetadata(time_unit=TimeUnit(code="month")), activities=activities,
                         probability_queries=queries, requested_outputs=["FULL_REPORT"])
    solution = solve_project(normalized(draft))
    assert solution.project.calculation_method == "BOTH"
    # t_e: A (2+6+16)/6=4, B (1+8+12)/6=7/2, C (4+9+20)/6=11/2, D (1+3+8)/6=2.
    # PERT schedule: A 0–4, B 0–3.5, x 3.5, C max(4, 3.5)=4–9.5, D 3.5–5.5 → 9.5; critical A–C.
    assert solution.cpm.project_duration == F(19, 2) and solution.cpm.critical_paths == [["A", "C"]]
    # σ² = ((6−2)/6)² + ((9−4)/6)² = 16/36 + 25/36 = 41/36
    assert solution.variance.total_variance == F(41, 36)
    # deterministic: A 0–4, C 4–9 → 9
    assert solution.cpm_deterministic.project_duration == 9
    gantt_b = next(r for r in solution.gantt.rows if r.activity_id == "B")
    # B early 0–3.5 (column 4 partial); B late: LF = min(x.LS = C.LS = 9.5 − 5.5 = 4, D.LS = 7.5) = 4 → LS 0.5 (column 1 partial)
    assert gantt_b.early_bar_columns == [1, 2, 3, 4] and gantt_b.late_bar_columns == [1, 2, 3, 4]
    assert gantt_b.total_slack == F(1, 2) and gantt_b.partial_columns == [1, 4]
    result = generate_workbook(draft, "both.xlsx", layout="classic")
    assert result.ok, result.workbook_validation.issues[:5]
    assert {"CPM determinístico", "Red AON", "Gantt", "Probabilidad"} <= set(result.sheets)


def test_large_random_network(output_dir):
    rng = random.Random(2024)
    activities = []
    for i in range(60):
        preds = rng.sample([f"T{j}" for j in range(max(0, i - 8), i)], k=min(i, rng.randint(0, 3)))
        a = rng.randint(1, 5)
        m = a + rng.randint(0, 4)
        activities.append(act(f"T{i}", preds, est=(a, m, m + rng.randint(0, 6))))
    draft = ProjectDraft(activities=activities, requested_outputs=["FULL_REPORT"],
                         probability_queries=[ProbabilityQuery(id="p", kind="PERCENTILE_TO_DURATION", target_probability=0.9)])
    solution = solve_project(normalized(draft))
    assert solution.validation.passed and len(solution.cpm.activities) == 60
    result = generate_workbook(draft, "large.xlsx")
    assert result.ok, result.workbook_validation.issues[:5]
