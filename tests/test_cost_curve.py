"""Period-by-period cost accumulation (S-curve). Expected values are hand arithmetic, written in the comments."""

from fractions import Fraction as F

import openpyxl
import zipfile
from helpers import act, normalized
from pm_mcp.domain.models import (
    CrashingRequest,
    IndirectCostPoint,
    ProjectDraft,
    ProjectMetadata,
    RequestedOutput,
)
from pm_mcp.engine.cost_curve import calculate_cost_curve, cost_curve_applies, period_overlap
from pm_mcp.engine.crashing import calculate_crashing_schedule
from pm_mcp.engine.network import build_activity_network
from pm_mcp.pipeline import solve_project
from pm_mcp.service import generate_workbook


def distinct_images(path):
    """Distinct PNGs stored in the file — identical pictures are deduplicated into one, so this counts states."""
    with zipfile.ZipFile(path) as book:
        return len([n for n in book.namelist() if n.startswith("xl/media/")])


def test_period_overlap():
    assert period_overlap(F(0), F(2), 1) == 1 and period_overlap(F(0), F(2), 3) == 0
    assert period_overlap(F(1, 2), F(5, 2), 1) == F(1, 2) and period_overlap(F(1, 2), F(5, 2), 3) == F(1, 2)
    assert period_overlap(F(4), F(4), 4) == 0


def normal_project():
    # A 0–2 (100), B 2–5 (300), C 0–4 (400): project duration 5, total direct cost 800
    return [act("A", [], 2, crash=(2, 100, 100)), act("B", ["A"], 3, crash=(3, 300, 300)),
            act("C", [], 4, crash=(4, 400, 400))]


def test_normal_plan_spreads_each_cost_over_its_duration():
    activities = normal_project()
    curve = calculate_cost_curve(build_activity_network(activities), activities)
    assert curve.basis == "normal" and curve.basis_duration == 5 and curve.horizon == 5
    assert curve.total_cost == 800
    # cost per week: A 100/2 = 50, B 300/3 = 100, C 400/4 = 100
    assert {a.activity_id: a.cost_per_period for a in curve.activities} == {"A": 50, "B": 100, "C": 100}
    assert [p.activity_ids for p in curve.periods] == [["A", "C"], ["A", "C"], ["B", "C"], ["B", "C"], ["B"]]
    # weeks 1–2: 50 + 100; weeks 3–4: 100 + 100; week 5: 100 (only B is still running)
    assert [p.period_cost for p in curve.periods] == [150, 150, 200, 200, 100]
    assert [p.cumulative_cost for p in curve.periods] == [150, 300, 500, 700, 800]
    assert curve.periods[-1].fraction_of_total == 1


def crashed_draft():
    # A: Dn 4, Dc 2, Cn 400, Cc 600 → slope 100; B (after A): 3 weeks, 300, not reducible.
    # Direct cost: 7 weeks 700, 6 weeks 800, 5 weeks 900. With the indirect costs below the totals are
    # 1200, 1000, 1200 → the recommended plan is 6 weeks at 800 of direct cost.
    activities = [act("A", [], 4, crash=(2, 400, 600)), act("B", ["A"], 3, crash=(3, 300, 300))]
    crashing = CrashingRequest(indirect_costs=[IndirectCostPoint(duration=7, cost=500),
                                              IndirectCostPoint(duration=6, cost=200),
                                              IndirectCostPoint(duration=5, cost=300)])
    return ProjectDraft(metadata=ProjectMetadata(title="Curva S"), activities=activities, crashing=crashing,
                        requested_outputs=["CRASHING", "CRITICAL_PATH", "GANTT"])


def test_compressed_plan_uses_the_recommended_duration_and_its_costs():
    project = normalized(crashed_draft())
    activities = project.activities
    network = build_activity_network(activities)
    crashing = calculate_crashing_schedule(network, activities, project.crashing)
    assert crashing.optimal.project_duration == 6 and crashing.optimal.total_cost == 1000

    curve = calculate_cost_curve(network, activities, crashing)
    assert curve.basis == "crashed" and curve.basis_duration == 6 and curve.crash_state_step == 1
    # A is shortened by 1: cost 400 + 100 × 1 = 500 over 3 weeks; B keeps 300 over 3 weeks
    a, b = curve.activities
    assert (a.duration, a.reduction, a.total_cost, a.cost_per_period) == (3, 1, 500, F(500, 3))
    assert (b.duration, b.reduction, b.total_cost, b.cost_per_period) == (3, 0, 300, 100)
    assert curve.total_cost == 800 == crashing.optimal.direct_cost
    assert [p.activity_ids for p in curve.periods] == [["A"], ["A"], ["A"], ["B"], ["B"], ["B"]]
    assert [p.cumulative_cost for p in curve.periods] == [F(500, 3), F(1000, 3), 500, 600, 700, 800]


def test_curve_needs_a_deterministic_duration_and_a_cost_for_every_activity():
    assert cost_curve_applies(normal_project())
    assert not cost_curve_applies([act("A", [], 2), act("B", ["A"], 3)])
    partial = [act("A", [], 2, crash=(2, 100, 100)), act("B", ["A"], 3)]
    assert not cost_curve_applies(partial)
    assert calculate_cost_curve(build_activity_network(partial), partial) is None


def test_solution_and_workbook_carry_the_curve(output_dir):
    draft = crashed_draft()
    solution = solve_project(normalized(draft))
    assert solution.validation.passed
    assert [c.name for c in solution.validation.checks if "cost curve" in c.name]
    assert solution.cost_curve.total_cost == 800

    result = generate_workbook(draft, "curva.xlsx")
    assert result.ok, result.workbook_validation.issues[:5]
    assert result.sheets == ["Enunciado", "Datos", "CPM", "Gantt", "Compresión", "Curva de costos", "Resultados"]

    sheet = openpyxl.load_workbook(result.path)["Curva de costos"]
    assert len(sheet._charts) == 1, "the cumulative cost must be charted, not only tabulated"
    values = openpyxl.load_workbook(result.path, data_only=True)["Curva de costos"]
    header = next(r for r in range(1, 40) if values.cell(r, 1).value == "Semana")
    table = [[values.cell(r, c).value for c in range(1, 5)] for r in range(header + 1, header + 7)]
    assert [row[0] for row in table] == [1, 2, 3, 4, 5, 6]
    assert [row[1] for row in table] == ["A", "A", "A", "B", "B", "B"]
    assert [round(row[3], 6) for row in table] == [round(v, 6) for v in (500 / 3, 1000 / 3, 500, 600, 700, 800)]
    # the sheet proves itself: final cumulative − total cost = 0
    assert values.cell(header + 7, 4).value == 0


def test_workbook_without_compression_links_the_normal_costs_of_the_data_sheet(output_dir):
    draft = ProjectDraft(metadata=ProjectMetadata(title="Curva S normal"), activities=normal_project(),
                         requested_outputs=["GANTT", "CRITICAL_PATH"])
    result = generate_workbook(draft, "curva_normal.xlsx")
    assert result.ok, result.workbook_validation.issues[:5]
    assert result.sheets == ["Enunciado", "Datos", "CPM", "Gantt", "Curva de costos", "Resultados"]
    sheet = openpyxl.load_workbook(result.path)["Curva de costos"]
    assert sheet["A2"].value.startswith("Programa base: duraciones y costos normales")
    assert sheet["C6"].value == "='Datos'!$G$5" and sheet["B6"].value == "='Datos'!$E$5"
    values = openpyxl.load_workbook(result.path, data_only=True)["Curva de costos"]
    header = next(r for r in range(1, 40) if values.cell(r, 1).value == "Semana")
    assert [values.cell(header + 1 + i, 4).value for i in range(5)] == [150, 300, 500, 700, 800]


def test_network_sheet_also_charts_the_accumulation(output_dir):
    draft = crashed_draft()
    draft = draft.model_copy(update={"requested_outputs": [*draft.requested_outputs, RequestedOutput.NETWORK_DIAGRAM]})
    result = generate_workbook(draft, "curva_red.xlsx")
    assert result.ok, result.workbook_validation.issues[:5]
    wb = openpyxl.load_workbook(result.path)
    # the curve is charted where the reader looks for the network too, over the cost-curve table's own cells
    network = wb["Red AON"]
    assert len(network._charts) == 1
    note = [c.value for row in network.iter_rows() for c in row if isinstance(c.value, str) and "Curva de costos" in c.value]
    assert note and note[0].startswith("Costo acumulado por semana")
    assert len(wb["Curva de costos"]._charts) == 1

    # one network picture per week of the base plan, each row carrying that week's cost from the curve


def test_one_network_picture_per_compression_step(output_dir):
    """The compression process step by step: each picture is the network re-scheduled with that step's durations."""
    draft = crashed_draft()
    draft = draft.model_copy(update={"requested_outputs": [*draft.requested_outputs, RequestedOutput.NETWORK_DIAGRAM]})
    result = generate_workbook(draft, "pasos.xlsx")
    assert result.ok, result.workbook_validation.issues[:5]
    steps = len(solve_project(normalized(draft)).crashing.states)
    assert steps == 3, "7 weeks, then 6 (the recommended duration), then A's minimum at 5"

    wb = openpyxl.load_workbook(result.path)
    sheet = wb["Red AON por paso"]
    assert len(sheet._images) == steps, "one picture per step, not one per calendar period"
    assert distinct_images(result.path) == steps + 1, "every step redraws the network, plus the Red AON diagram"
    assert {"PASO 0", "PASO 1", "PASO 2"} <= {sheet.cell(r, 1).value for r in range(1, sheet.max_row + 1)}

    # the panel sits to the right of the picture, so find it by its labels rather than by a fixed column
    panel = {(c.value, sheet.cell(c.row, c.column + 1).value) for row in sheet.iter_rows() for c in row
             if isinstance(c.value, str) and c.value.startswith(("Costos directos", "Duración del proyecto"))}
    assert any(label == "Costos directos" and str(value).startswith("='Compresión'!") for label, value in panel),         "the panel links to the compression sheet instead of restating it"
    values = openpyxl.load_workbook(result.path, data_only=True)["Red AON por paso"]
    durations = [values.cell(c.row, c.column + 1).value for row in values.iter_rows() for c in row
                 if c.value == "Duración del proyecto (semanas)"]
    assert durations == [7, 6, 5], "one panel per step, from the normal duration down to the shortest"
    assert len(wb["Compresión"]._charts) == 1, "cost against duration is charted on the compression sheet"


def test_network_without_crashing_has_no_step_sheet(output_dir):
    activities = [act("A", [], 2), act("B", ["A"], 3), act("C", [], 4)]
    draft = ProjectDraft(metadata=ProjectMetadata(title="Sin compresión"), activities=activities,
                         requested_outputs=[RequestedOutput.NETWORK_DIAGRAM])
    result = generate_workbook(draft, "sin_pasos.xlsx")
    assert result.ok, result.workbook_validation.issues[:5]
    assert "Red AON por paso" not in result.sheets and "Red AON" in result.sheets
