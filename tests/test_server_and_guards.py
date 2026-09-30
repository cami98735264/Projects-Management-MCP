import asyncio
import re
from pathlib import Path

from helpers import ROOT, load_fixture
from pm_mcp.server import mcp

EXPECTED_TOOLS = {
    "extract_document_content", "get_methodology_reference", "suggest_requirements_from_text", "convert_arrow_network",
    "normalize_project_input", "build_activity_network", "calculate_pert_estimates", "calculate_cpm",
    "aggregate_critical_path_variance", "calculate_completion_probability", "calculate_percentile_duration",
    "generate_gantt_schedule", "calculate_crashing_schedule", "solve_project", "validate_calculations",
    "generate_solution_workbook", "validate_workbook",
}


def call(name, arguments):
    _, structured = asyncio.run(mcp.call_tool(name, arguments))
    return structured


def test_tools_are_registered_with_typed_input_schemas():
    tools = {t.name: t for t in asyncio.run(mcp.list_tools())}
    assert set(tools) == EXPECTED_TOOLS
    assert "project" in tools["normalize_project_input"].inputSchema["properties"]
    assert all(t.description for t in tools.values())


def test_json_only_round_trip_through_the_server(output_dir):
    conversion = call("convert_arrow_network", {"arcs": load_fixture("taller1_transcription.json")["arcs"]})
    assert conversion["ok"]
    project = {"metadata": {"title": "json", "time_unit": {"code": "week"}},
               "activities": conversion["result"]["activities"],
               "probability_queries": [{"id": "g", "kind": "PERCENTILE_TO_DURATION", "target_probability": 0.98}]}
    normalized = call("normalize_project_input", {"project": project})
    assert normalized["ok"] and normalized["project"]["calculation_method"] == "PERT"
    cpm = call("calculate_cpm", {"project": project})
    assert cpm["result"]["project_duration"] == {"value": 15.0, "fraction": "15"}
    workbook = call("generate_solution_workbook", {"project": project, "output_path": "server.xlsx"})
    assert workbook["ok"] and Path(workbook["path"]).exists()
    checked = call("validate_workbook", {"path": workbook["path"], "project": project})
    assert checked["passed"] and checked["expected_values_checked"] > 0
    solved = call("solve_project", {"project": project})
    verified = call("validate_calculations", {"project": project, "solution": solved["result"]})
    assert verified["ok"] and verified["result"]["passed"]


def test_tampered_solution_is_rejected():
    arcs = load_fixture("taller1_transcription.json")["arcs"]
    project = {"activities": call("convert_arrow_network", {"arcs": arcs})["result"]["activities"]}
    solution = call("solve_project", {"project": project})["result"]
    solution["cpm"]["project_duration"] = {"value": 14.0, "fraction": "14"}
    verified = call("validate_calculations", {"project": project, "solution": solution})
    assert verified["ok"] and not verified["result"]["passed"]


def test_invalid_input_returns_typed_issues():
    bad = call("normalize_project_input", {"project": {"activities": [
        {"id": "A", "predecessors": ["B"], "duration": 1}, {"id": "B", "predecessors": ["A"], "duration": 1}]}})
    assert not bad["ok"] and bad["issues"][0]["code"] == "cycle"
    percentile = call("calculate_percentile_duration", {"expected_duration": 10, "standard_deviation": 1, "target_probability": 98})
    assert not percentile["ok"] and "0.98" in percentile["issues"][0]["message"]


FORBIDDEN = [
    (r"taller", re.IGNORECASE),
    (r"ejemplo\s*4\.2", re.IGNORECASE),
    (r"\b37/9\b|\b14/9\b|26\.6089|19\.16", 0),
    (r"A\W{1,3}C\W{1,3}F\W{1,3}H\W{1,3}J", 0),
    (r"==\s*['\"][A-J]['\"]", 0),
]


def test_no_exercise_specific_logic_in_source():
    for path in (ROOT / "src" / "pm_mcp").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for pattern, flags in FORBIDDEN:
            assert not re.search(pattern, text, flags), f"{pattern!r} found in {path.relative_to(ROOT)}"
