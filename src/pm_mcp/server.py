"""MCP server: typed tools over the deterministic engine.

Run with ``pm-scheduling-mcp`` (stdio). Every project-taking tool accepts a ProjectDraft and normalizes it first;
no tool accepts numbers "already computed" by the LLM except the two generic normal-distribution calculators.
"""

from __future__ import annotations

import json
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from pm_mcp import service as svc
from pm_mcp.domain.models import (
    ArrowArc,
    DurationSource,
    ProbabilityMethod,
    ProbabilityQuery,
    ProjectDraft,
    ReportLanguage,
    VarianceStrategy,
)
from pm_mcp.engine.aoa import ArrowNetworkConversion
from pm_mcp.engine.cpm import CpmResult
from pm_mcp.engine.crashing import CrashingResult
from pm_mcp.engine.gantt import GanttSchedule
from pm_mcp.engine.network import ActivityNetwork
from pm_mcp.engine.pert import CriticalPathVarianceResult, PertEstimatesResult
from pm_mcp.engine.probability import PercentileResult, ProbabilityResult
from pm_mcp.ingestion.document_extraction import ExtractedDocument, extract_document_content as _extract
from pm_mcp.ingestion.normalize import NormalizationResult
from pm_mcp.ingestion.requirements_inference import RequirementSuggestions, suggest_requirements_from_text as _suggest
from pm_mcp.logging_config import configure_logging
from pm_mcp.output.workbook_validation import WorkbookValidationReport
from pm_mcp.reference import load_methodology, methodology_topic
from pm_mcp.service import Envelope, WorkbookGenerationResult
from pm_mcp.solution import CalculationValidationReport, ProjectSolution

WORKFLOW = """\
Recommended call sequence for a project-scheduling exercise (CPM / PERT / Gantt):

1. extract_document_content(path, image_output_dir) for every file the user supplied. Read text_blocks and tables;
   open EVERY saved image visually — activity tables, formulas and network figures are often images only.
2. suggest_requirements_from_text(joined text) → questions, probability queries, time unit (confirm each one).
3. Transcribe activity data:
   - a table with predecessors → Activity objects;
   - a figure with numbered nodes and lettered arrows (activity-on-arrow, dashed arrows = dummies) → ArrowArc list →
     convert_arrow_network → activities + arrow_network_trace.
   Never invent durations, estimates or precedences; leave unknowns unset so validation asks for them.
4. normalize_project_input(ProjectDraft). If ok=false, ask the user the clarification_questions — do not guess.
5. Optional inspection: build_activity_network, calculate_pert_estimates, calculate_cpm,
   aggregate_critical_path_variance, generate_gantt_schedule, calculate_crashing_schedule.
6. solve_project for all numbers and per-question answers (includes independent validation), then
   generate_solution_workbook(project, 'name.xlsx') — it writes, round-trip validates, and returns answers.
7. Report every number exactly as returned by the tools; never do arithmetic yourself.
"""

mcp = FastMCP("pm-scheduling", instructions=WORKFLOW)


@mcp.tool()
def extract_document_content(path: str, image_output_dir: str | None = None, ocr: bool = False) -> ExtractedDocument:
    """Extract text (document order), native tables, spreadsheet cells and every embedded image (saved with its
    location and sha256; default folder PM_MCP_OUTPUT_DIR/extracted/<file name>) from a .docx/.xlsx/.pdf/.txt/.json/
    image file so the images can be inspected visually. OCR text is a low-confidence hint only."""
    target = image_output_dir or str(svc.resolve_output_path(f"extracted/{Path(path).stem}"))
    return _extract(path, target, ocr)


@mcp.tool()
def get_methodology_reference(topic: str | None = None) -> dict:
    """Methodology rules and classified reference material (formulas, Gantt and AON conventions, probability table
    convention, crashing procedure). Topics: pert, cpm, gantt, network, probability, crashing, or omit for all."""
    return methodology_topic(topic)


@mcp.tool()
def suggest_requirements_from_text(text: str) -> RequirementSuggestions:
    """Heuristically split an exercise into question parts (a), b), …) and suggest requested outputs, probability
    queries (at most / at least / between / percentile), delay queries and time unit. Confirm before use."""
    return _suggest(text)


@mcp.tool()
def convert_arrow_network(arcs: list[ArrowArc], keep_dummies: bool = False,
                          language: ReportLanguage = ReportLanguage.ES) -> Envelope[ArrowNetworkConversion]:
    """Convert an activity-on-arrow network (arcs between numbered nodes, dummy arcs allowed) into activity-on-node
    activities with derived predecessors and a per-activity justification trace."""
    return svc.convert_arrows(arcs, keep_dummies, language)


@mcp.tool()
def normalize_project_input(project: ProjectDraft) -> NormalizationResult:
    """Validate a ProjectDraft and return a Project with only structurally safe inferences (dummy duration 0,
    calculation method from data shape, requested outputs from queries), or typed issues and clarification questions."""
    return svc.normalize(project)


@mcp.tool()
def build_activity_network(project: ProjectDraft) -> Envelope[ActivityNetwork]:
    """Build the activity-on-node graph; reports cycles (naming the loop), duplicate ids, unknown predecessors,
    unreachable activities and disconnected groups."""
    return svc.network(project)


@mcp.tool()
def calculate_pert_estimates(project: ProjectDraft) -> Envelope[PertEstimatesResult]:
    """Per-activity t_e = (a + b + 4m)/6 and σ² = [(b − a)/6]², exact fractions with working."""
    return svc.pert_estimates(project)


@mcp.tool()
def calculate_cpm(project: ProjectDraft, duration_source: DurationSource | None = None) -> Envelope[CpmResult]:
    """Forward/backward pass: ES, EF, LS, LF, total and free slack, all critical paths, project duration.
    duration_source defaults to expected_duration for PERT data and duration otherwise."""
    return svc.cpm(project, duration_source)


@mcp.tool()
def aggregate_critical_path_variance(project: ProjectDraft,
                                     strategy: VarianceStrategy | None = None) -> Envelope[CriticalPathVarianceResult]:
    """Project variance = Σ σ² of critical activities and σ = √σ²; handles multiple critical paths explicitly."""
    return svc.critical_path_variance(project, strategy)


@mcp.tool()
def calculate_completion_probability(expected_duration: float, standard_deviation: float, query: ProbabilityQuery,
                                     method: ProbabilityMethod = ProbabilityMethod.EXACT) -> Envelope[ProbabilityResult]:
    """P(T ≤ x), P(T ≥ x) or P(x₁ ≤ T ≤ x₂) with Z = (T_p − t_e)/σ; returns exact and 4-decimal-table values."""
    return svc.completion_probability(expected_duration, standard_deviation, query, method)


@mcp.tool()
def calculate_percentile_duration(expected_duration: float, standard_deviation: float, target_probability: float,
                                  method: ProbabilityMethod = ProbabilityMethod.EXACT,
                                  query_id: str | None = None) -> Envelope[PercentileResult]:
    """Duration with the given completion probability: T_p = t_e + Z·σ with Z = Φ⁻¹(p) (exact) and the table Z;
    also rounded up to whole time units (e.g. a contract duration)."""
    return svc.percentile_duration(expected_duration, standard_deviation, target_probability, method, query_id)


@mcp.tool()
def generate_gantt_schedule(project: ProjectDraft) -> Envelope[GanttSchedule]:
    """Gantt rows: early bar (ES→EF) and late bar (LS→LF) per activity, time-unit columns, and an observation
    ('Actividad Crítica' or 'Holgura de N …')."""
    return svc.gantt(project)


@mcp.tool()
def calculate_crashing_schedule(project: ProjectDraft) -> Envelope[CrashingResult]:
    """Time-cost trade-off: slopes (Cc − Cn)/(Dn − Dc), step-by-step minimum-cost compression over all critical
    paths, direct/indirect/total cost table and optimum. Needs crash data for every activity."""
    return svc.crashing(project)


@mcp.tool()
def solve_project(project: ProjectDraft) -> Envelope[ProjectSolution]:
    """Run every calculation the project calls for, validate them independently, and return per-question answers."""
    return svc.solve(project)


@mcp.tool()
def validate_calculations(project: ProjectDraft, solution: ProjectSolution | None = None) -> Envelope[CalculationValidationReport]:
    """Re-derive results by independent routes (alternate formulas, relaxation longest path, numerical integration)
    and compare. Pass a previously returned solution to check it has not been altered."""
    return svc.check_calculations(project, solution)


@mcp.tool()
def generate_solution_workbook(project: ProjectDraft, output_path: str, overwrite: bool = False) -> WorkbookGenerationResult:
    """Solve, write the .xlsx (sheets chosen from the requested outputs), round-trip validate every formula and
    promised sheet, and return the path, answers and both validation reports. Relative paths go to PM_MCP_OUTPUT_DIR."""
    return svc.generate_workbook(project, output_path, overwrite)


@mcp.tool()
def validate_workbook(path: str, project: ProjectDraft | None = None) -> WorkbookValidationReport:
    """Open a workbook and check sheets, formula syntax, error values, dangling references and cached values;
    with a project, also compare every computed cell against the engine."""
    return svc.check_workbook(path, project)


@mcp.resource("pm://methodology")
def methodology_resource() -> str:
    """Classified reference material and methodology rules (JSON)."""
    return json.dumps(load_methodology(), ensure_ascii=False, indent=2)


@mcp.resource("pm://schema/project-draft")
def project_draft_schema() -> str:
    """JSON schema of the canonical ProjectDraft input."""
    return json.dumps(ProjectDraft.model_json_schema(), ensure_ascii=False, indent=2)


@mcp.prompt()
def solve_exercise_workflow() -> str:
    """How to orchestrate the tools to solve an exercise end to end."""
    return WORKFLOW


def main() -> None:
    configure_logging()
    mcp.run()


if __name__ == "__main__":
    main()
