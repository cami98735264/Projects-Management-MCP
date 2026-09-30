"""ProjectDraft → validated Project, applying only structurally safe inferences (§8)."""

from __future__ import annotations

from fractions import Fraction

from pydantic import BaseModel, Field, ValidationError

from pm_mcp.domain.errors import IssueCode, IssueSeverity, ValidationIssue, has_errors
from pm_mcp.domain.models import (
    Assumption,
    Project,
    ProjectDraft,
    ProbabilityQueryKind,
    Provenance,
    RequestedOutput,
)
from pm_mcp.domain.rules import collect_project_issues
from pm_mcp.logging_config import get_logger, log_stage

_log = get_logger(__name__)


class NormalizationResult(BaseModel):
    ok: bool
    project: Project | None = None
    issues: list[ValidationIssue] = Field(default_factory=list)
    inferences: list[Assumption] = Field(default_factory=list, description="Values the system inferred, with justification")
    clarification_questions: list[str] = Field(
        default_factory=list, description="What must be asked to the user before calculating (never guessed)")


def normalize_project_input(draft: ProjectDraft) -> NormalizationResult:
    log_stage(_log, "input_received", activities=len(draft.activities), method=draft.calculation_method)
    inferences: list[Assumption] = []

    activities = []
    for index, act in enumerate(draft.activities):
        if act.is_dummy and act.duration is None:
            note = "Dummy ('ficticia') activity: zero duration by definition."
            act = act.model_copy(update={
                "duration": Fraction(0),
                "field_provenance": {**act.field_provenance, "duration": Provenance.INFERRED},
                "provenance_notes": [*act.provenance_notes, note],
            })
            inferences.append(Assumption(field_path=f"activities[{index}].duration", value="0", justification=note))
        activities.append(act)

    outputs = list(draft.requested_outputs)

    def add_output(output: RequestedOutput, why: str) -> None:
        if output not in outputs and RequestedOutput.FULL_REPORT not in outputs:
            outputs.append(output)
            inferences.append(Assumption(field_path="requested_outputs", value=output.value, justification=why))

    for question in draft.questions:
        for output in question.outputs:
            add_output(output, f"Question '{question.label}' asks for it.")
    kinds = {q.kind for q in draft.probability_queries}
    if kinds - {ProbabilityQueryKind.PERCENTILE_TO_DURATION}:
        add_output(RequestedOutput.PROBABILITY_QUERY, "Probability queries are defined.")
    if ProbabilityQueryKind.PERCENTILE_TO_DURATION in kinds:
        add_output(RequestedOutput.PERCENTILE_DURATION, "A target-probability (percentile) query is defined.")
    if draft.delay_queries:
        add_output(RequestedOutput.ACTIVITY_MAX_DELAY, "Activity delay queries are defined.")
    if draft.crashing is not None:
        add_output(RequestedOutput.CRASHING, "A crashing request is defined.")
    if not outputs:
        outputs = [RequestedOutput.FULL_REPORT]
        inferences.append(Assumption(field_path="requested_outputs", value="FULL_REPORT",
                                     justification="No specific deliverable was requested; every output the data supports is produced."))

    working = draft.model_copy(update={"activities": activities, "requested_outputs": outputs})
    issues, method = collect_project_issues(working)
    if draft.calculation_method is None and method is not None:
        inferences.append(Assumption(
            field_path="calculation_method", value=method.value,
            justification={
                "PERT": "Every activity has optimistic, most likely and pessimistic durations.",
                "CPM": "Every activity has a single deterministic duration and no three-point estimates.",
                "BOTH": "Every activity has both a deterministic duration and three-point estimates.",
            }[method.value]))

    clarifications = [i.message for i in issues if i.needs_user_clarification and i.severity == IssueSeverity.ERROR]
    if has_errors(issues) or method is None:
        log_stage(_log, "normalization_failed", errors=sum(i.severity == IssueSeverity.ERROR for i in issues))
        return NormalizationResult(ok=False, issues=issues, inferences=inferences, clarification_questions=clarifications)

    data = working.model_dump()
    data["calculation_method"] = method
    data["assumptions"] = [*data["assumptions"], *(a.model_dump() for a in inferences)]
    try:
        project = Project.model_validate(data)
    except ValidationError as exc:  # defensive: rules and model must agree
        issue = ValidationIssue(code=IssueCode.SCHEMA, message=str(exc))
        return NormalizationResult(ok=False, issues=[*issues, issue], inferences=inferences)
    log_stage(_log, "normalized", method=method.value, outputs=[o.value for o in outputs],
              warnings=len(issues), inferences=len(inferences))
    return NormalizationResult(ok=True, project=project, issues=issues, inferences=inferences)
