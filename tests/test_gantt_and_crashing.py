from fractions import Fraction as F

from helpers import arcs_from, crash_activities, load_fixture, normalized
from pm_mcp.domain.errors import IssueCode
from pm_mcp.domain.models import CrashingRequest, DurationSource, IndirectCostPoint, ProjectDraft, ReportLanguage, TimeUnit
from pm_mcp.engine.aoa import convert_arrow_network
from pm_mcp.engine.cpm import calculate_cpm, resolve_durations
from pm_mcp.engine.crashing import calculate_crashing_schedule
from pm_mcp.engine.gantt import bar_columns, generate_gantt_schedule
from pm_mcp.engine.network import build_activity_network
from pm_mcp.engine.pert import calculate_pert_estimates
from pm_mcp.ingestion.normalize import normalize_project_input


def test_bar_columns():
    assert bar_columns(F(0), F(2)) == [1, 2]
    assert bar_columns(F(2), F(5)) == [3, 4, 5]
    assert bar_columns(F(13, 6), F(19, 6)) == [3, 4]
    assert bar_columns(F(4), F(4)) == []


def exercise_gantt(language=ReportLanguage.ES):
    activities = convert_arrow_network(arcs_from(load_fixture("taller1_transcription.json"))).activities
    network = build_activity_network(activities)
    expected = calculate_pert_estimates(activities).expected_durations()
    cpm = calculate_cpm(network, activities, resolve_durations(activities, DurationSource.EXPECTED_DURATION, expected),
                        DurationSource.EXPECTED_DURATION)
    return generate_gantt_schedule(cpm, TimeUnit(code="week"), language)


def test_gantt_rows_follow_reference_convention():
    gantt = exercise_gantt()
    rows = {r.activity_id: r for r in gantt.rows}
    assert gantt.horizon == 15 and gantt.columns[-1] == 15
    # B: ES 2 EF 5 → weeks 3–5; LS 3 LF 6 → weeks 4–6; slack 1 (singular unit)
    assert rows["B"].early_bar_columns == [3, 4, 5] and rows["B"].late_bar_columns == [4, 5, 6]
    assert rows["B"].observation == "Holgura de 1 semana"
    assert rows["D"].observation == "Holgura de 3 semanas"
    assert rows["A"].observation == "Actividad Crítica" and rows["A"].early_bar_columns == rows["A"].late_bar_columns
    assert exercise_gantt(ReportLanguage.EN).rows[6].observation == "Slack of 4 weeks"  # G


def crash_problem(name):
    fixture = load_fixture("crashing_reference_problems.json")[name]
    activities = crash_activities(fixture)
    request = CrashingRequest(indirect_costs=[IndirectCostPoint(**p) for p in fixture.get("indirect_costs", [])])
    return calculate_crashing_schedule(build_activity_network(activities), activities, request)


def test_crashing_single_then_two_independent_critical_paths():
    result = crash_problem("problem_1")
    # Slopes: A 50, B 100, C 40, D 60, E 25, F 10. Normal: A-D = 18 at cost 580.
    # A (50) twice until its minimum 6; then D (60) each week; at 12 weeks B-E-F (4+5+3) is also critical,
    # so D and F (60 + 10) are shortened together; at 11 weeks A and D are both at minimum → stop.
    assert [s.project_duration for s in result.states] == [18, 17, 16, 15, 14, 13, 12, 11]
    assert [s.direct_cost for s in result.states] == [580, 630, 680, 740, 800, 860, 920, 990]
    assert [s.next_crash_activities for s in result.states] == [["A"], ["A"], ["D"], ["D"], ["D"], ["D"], ["D", "F"], []]
    assert "A-D" in result.stop_reason
    assert {s.activity_id: s.slope for s in result.slopes}["F"] == 10


def test_crashing_prefers_shared_activity_and_finds_total_cost_optimum():
    result = crash_problem("problem_2")
    # 13→12: F (100) on C-E-F. At 12: paths C-E-F and C-G share C: C (200) < F+G (250) < E+G (750).
    # 11→10: C at minimum → F+G (250). 10→9: F at minimum → E+G (750). At 9: C-E-F has no reducible activity.
    assert [s.next_crash_activities for s in result.states] == [["F"], ["C"], ["F", "G"], ["E", "G"], []]
    assert [(p.project_duration, p.direct_cost, p.total_cost) for p in result.cost_table] == [
        (9, 6400, 12400), (10, 5650, 11800), (11, 5400, 11600), (12, 5200, 11700), (13, 5100, 12200)]
    assert (result.optimal.project_duration, result.optimal.total_cost) == (11, 11600)


def test_crashing_target_duration():
    fixture = load_fixture("crashing_reference_problems.json")["problem_1"]
    activities = crash_activities(fixture)
    result = calculate_crashing_schedule(build_activity_network(activities), activities, CrashingRequest(target_duration=15))
    assert (result.minimum_duration, result.minimum_duration_direct_cost) == (15, 740)


def test_crashing_input_validation():
    fixture = load_fixture("crashing_reference_problems.json")["problem_1"]
    activities = crash_activities(fixture)
    missing = activities[0].model_copy(update={"crash": None})
    result = normalize_project_input(ProjectDraft(activities=[missing, *activities[1:]], requested_outputs=["CRASHING"]))
    assert not result.ok and result.issues[0].code == IssueCode.CRASH_DATA_MISSING
    cheap = activities[1].model_copy(update={"crash": activities[1].crash.model_copy(update={"crash_cost": F(1)})})
    result = normalize_project_input(ProjectDraft(activities=[activities[0], cheap, *activities[2:]], requested_outputs=["CRASHING"]))
    assert any(i.code == IssueCode.INVALID_CRASH_DATA for i in result.issues)


def test_crashing_through_pipeline_is_validated():
    from pm_mcp.pipeline import solve_project

    fixture = load_fixture("crashing_reference_problems.json")["problem_2"]
    project = normalized(ProjectDraft(activities=crash_activities(fixture), requested_outputs=["CRASHING"],
                                      crashing=CrashingRequest(indirect_costs=[IndirectCostPoint(**p) for p in fixture["indirect_costs"]])))
    solution = solve_project(project)
    assert solution.validation.passed
    assert any("crashing" in c.name for c in solution.validation.checks)
