"""Canonical domain model.

This is the *only* contract between the AI/LLM orchestration layer and the deterministic engine. The LLM
produces a :class:`ProjectDraft` (typed, but allowed to leave inferable things unset); ``normalize_project_input``
turns it into a :class:`Project`, whose construction re-runs every semantic rule — so holding a ``Project``
instance guarantees the engine can run on it.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, StringConstraints, model_validator

from pm_mcp.domain.numbers import NumberIn


class Provenance(StrEnum):
    USER_STATED = "user_stated"
    DOCUMENT_EXTRACTED = "document_extracted"
    INFERRED = "inferred"
    CALCULATED = "calculated"
    ASSUMED = "assumed"


class TimeUnitCode(StrEnum):
    HOUR = "hour"
    DAY = "day"
    WEEK = "week"
    MONTH = "month"
    CUSTOM = "custom"


class ReportLanguage(StrEnum):
    ES = "es"
    EN = "en"


class CalculationMethod(StrEnum):
    CPM = "CPM"
    """Deterministic durations (``duration``) feed the forward/backward passes."""
    PERT = "PERT"
    """Three-point estimates: t_e feeds the passes; σ² and probability analysis are available."""
    BOTH = "BOTH"
    """Both data sets are present: a deterministic schedule and a PERT schedule are computed."""


class DurationSource(StrEnum):
    DURATION = "duration"
    EXPECTED_DURATION = "expected_duration"


class RequestedOutput(StrEnum):
    GANTT = "GANTT"
    NETWORK_DIAGRAM = "NETWORK_DIAGRAM"
    EARLY_LATE_TIMES = "EARLY_LATE_TIMES"
    SLACK = "SLACK"
    CRITICAL_PATH = "CRITICAL_PATH"
    EXPECTED_DURATION = "EXPECTED_DURATION"
    VARIANCE = "VARIANCE"
    ACTIVITY_ESTIMATES = "ACTIVITY_ESTIMATES"
    """Per-activity PERT expected time and variance."""
    PROBABILITY_QUERY = "PROBABILITY_QUERY"
    PERCENTILE_DURATION = "PERCENTILE_DURATION"
    ACTIVITY_MAX_DELAY = "ACTIVITY_MAX_DELAY"
    CRASHING = "CRASHING"
    FULL_REPORT = "FULL_REPORT"


class ProbabilityQueryKind(StrEnum):
    AT_MOST = "AT_MOST"
    AT_LEAST = "AT_LEAST"
    BETWEEN = "BETWEEN"
    PERCENTILE_TO_DURATION = "PERCENTILE_TO_DURATION"


class ProbabilityMethod(StrEnum):
    EXACT = "exact"
    """Z and Φ(Z) at full precision."""
    TABLE = "table"
    """Reference-material convention: Z rounded to 2 decimals, Φ read to 4 decimals, percentile Z = smallest
    table Z whose Φ reaches the target, durations rounded up to whole units."""


class VarianceStrategy(StrEnum):
    MAX_VARIANCE_PATH = "max_variance_path"
    ALL_CRITICAL_ACTIVITIES = "all_critical_activities"


ActivityId = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=40, pattern=r"^[^\s,;]+$"),
]


def _node_to_str(value: Any) -> Any:
    return str(value) if isinstance(value, int) and not isinstance(value, bool) else value


NodeId = Annotated[str, BeforeValidator(_node_to_str), StringConstraints(strip_whitespace=True, min_length=1)]

_UNIT_LABELS: dict[str, dict[str, tuple[str, str]]] = {
    "es": {"hour": ("hora", "horas"), "day": ("día", "días"), "week": ("semana", "semanas"), "month": ("mes", "meses")},
    "en": {"hour": ("hour", "hours"), "day": ("day", "days"), "week": ("week", "weeks"), "month": ("month", "months")},
}


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TimeUnit(_Strict):
    code: TimeUnitCode = TimeUnitCode.WEEK
    singular: str | None = Field(default=None, description="Display label, required when code='custom'")
    plural: str | None = None

    def labels(self, language: ReportLanguage | str) -> tuple[str, str]:
        if self.singular and self.plural:
            return self.singular, self.plural
        if self.code == TimeUnitCode.CUSTOM:
            return self.singular or "unit", self.plural or self.singular or "units"
        return _UNIT_LABELS[str(language)][self.code.value]

    def label(self, quantity: Any, language: ReportLanguage | str) -> str:
        singular, plural = self.labels(language)
        return singular if quantity == 1 else plural


class CrashData(_Strict):
    """Time-cost trade-off data (CPM sheet of the reference workbook)."""

    crash_duration: NumberIn = Field(description="Minimum ('crítica') duration Dc")
    normal_cost: NumberIn = Field(description="Direct cost at normal duration Cn")
    crash_cost: NumberIn = Field(description="Direct cost at minimum duration Cc")
    provenance: Provenance = Provenance.USER_STATED


class Activity(_Strict):
    id: ActivityId
    name: str | None = None
    predecessors: list[ActivityId] = Field(default_factory=list)
    is_dummy: bool = Field(default=False, description="'Ficticia' activity: zero duration, only enforces precedence")
    duration: NumberIn | None = Field(default=None, description="Deterministic (CPM) duration in the project time unit")
    optimistic_duration: NumberIn | None = Field(default=None, description="a")
    most_likely_duration: NumberIn | None = Field(default=None, description="m")
    pessimistic_duration: NumberIn | None = Field(default=None, description="b")
    crash: CrashData | None = None
    provenance: Provenance = Provenance.USER_STATED
    field_provenance: dict[str, Provenance] = Field(
        default_factory=dict, description="Per-field provenance overriding `provenance`, e.g. {'predecessors': 'inferred'}"
    )
    provenance_notes: list[str] = Field(default_factory=list, description="Justifications for inferred values")

    @property
    def estimates(self) -> tuple:
        return (self.optimistic_duration, self.most_likely_duration, self.pessimistic_duration)

    @property
    def has_three_point_estimates(self) -> bool:
        return all(v is not None for v in self.estimates)

    @property
    def has_partial_estimates(self) -> bool:
        present = [v is not None for v in self.estimates]
        return any(present) and not all(present)

    def provenance_of(self, field: str) -> Provenance:
        return self.field_provenance.get(field, self.provenance)


class ProbabilityQuery(_Strict):
    id: str | None = Field(default=None, description="Stable label, e.g. the question letter 'd'")
    kind: ProbabilityQueryKind
    lower_bound: NumberIn | None = Field(default=None, description="AT_LEAST / BETWEEN lower duration")
    upper_bound: NumberIn | None = Field(default=None, description="AT_MOST / BETWEEN upper duration")
    target_probability: float | None = Field(default=None, description="PERCENTILE_TO_DURATION probability in (0, 1)")
    provenance: Provenance = Provenance.USER_STATED
    source_text: str | None = None


class Assumption(_Strict):
    field_path: str
    value: str
    justification: str
    provenance: Provenance = Provenance.INFERRED


class QuestionItem(_Strict):
    """One numbered part of the original exercise, answered in order in the Final Results sheet."""

    label: str
    text: str
    outputs: list[RequestedOutput] = Field(default_factory=list)
    probability_query_ids: list[str] = Field(default_factory=list)
    delay_activity_ids: list[ActivityId] = Field(default_factory=list)


class IndirectCostPoint(_Strict):
    duration: NumberIn
    cost: NumberIn


class CrashingRequest(_Strict):
    target_duration: NumberIn | None = Field(default=None, description="Stop once the project reaches this duration")
    indirect_costs: list[IndirectCostPoint] = Field(default_factory=list)
    step: NumberIn = Field(default=1, description="Maximum reduction per iteration (the reference reduces 1 unit at a time)")


class ArrowArc(_Strict):
    """An arc of an Activity-on-Arrow ('actividad-flecha') network, as drawn in many exercise figures."""

    id: ActivityId | None = Field(default=None, description="Activity letter; optional for dummy arcs")
    tail_node: NodeId
    head_node: NodeId
    is_dummy: bool = False
    name: str | None = None
    duration: NumberIn | None = None
    optimistic_duration: NumberIn | None = None
    most_likely_duration: NumberIn | None = None
    pessimistic_duration: NumberIn | None = None
    crash: CrashData | None = None
    provenance: Provenance = Provenance.DOCUMENT_EXTRACTED


class ArrowConversionTrace(_Strict):
    activity_id: str
    tail_node: str
    head_node: str
    predecessors: list[str]
    via_dummies: list[str] = Field(default_factory=list)
    justification: str


class ProjectMetadata(_Strict):
    title: str = "Proyecto"
    time_unit: TimeUnit = Field(default_factory=TimeUnit)
    source_description: str | None = None
    language: ReportLanguage = ReportLanguage.ES


class AnalysisOptions(_Strict):
    probability_method: ProbabilityMethod = Field(
        default=ProbabilityMethod.EXACT,
        description="Which value is reported as the primary answer; both exact and table values are always shown.",
    )
    variance_strategy: VarianceStrategy = VarianceStrategy.MAX_VARIANCE_PATH
    include_dummies_in_gantt: bool = False


class ProjectDraft(_Strict):
    """What the LLM layer produces. Inferable fields may be left unset; nothing may be fabricated."""

    metadata: ProjectMetadata = Field(default_factory=ProjectMetadata)
    activities: list[Activity] = Field(default_factory=list)
    calculation_method: CalculationMethod | None = None
    requested_outputs: list[RequestedOutput] = Field(default_factory=list)
    probability_queries: list[ProbabilityQuery] = Field(default_factory=list)
    delay_queries: list[ActivityId] = Field(default_factory=list)
    crashing: CrashingRequest | None = None
    questions: list[QuestionItem] = Field(default_factory=list)
    assumptions: list[Assumption] = Field(default_factory=list)
    arrow_network_trace: list[ArrowConversionTrace] = Field(default_factory=list)
    options: AnalysisOptions = Field(default_factory=AnalysisOptions)


class Project(ProjectDraft):
    """A fully validated project. Constructing one runs every semantic rule; invalid data cannot exist here."""

    calculation_method: CalculationMethod
    requested_outputs: list[RequestedOutput] = Field(min_length=1)

    @model_validator(mode="after")
    def _enforce_domain_rules(self) -> Project:
        from pm_mcp.domain.errors import format_issues, has_errors
        from pm_mcp.domain.rules import collect_project_issues

        issues, _ = collect_project_issues(self)
        if has_errors(issues):
            raise ValueError(format_issues([i for i in issues if i.severity == "error"]))
        return self

    def activity_map(self) -> dict[str, Activity]:
        return {a.id: a for a in self.activities}

    @property
    def uses_estimates(self) -> bool:
        return self.calculation_method in (CalculationMethod.PERT, CalculationMethod.BOTH)

    @property
    def uses_deterministic_durations(self) -> bool:
        return self.calculation_method in (CalculationMethod.CPM, CalculationMethod.BOTH)
