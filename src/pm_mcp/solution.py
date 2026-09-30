"""Aggregate result models shared by the pipeline, validation, answers and the workbook writer."""

from __future__ import annotations

from pydantic import BaseModel, Field

from pm_mcp.domain.errors import ValidationIssue
from pm_mcp.domain.models import Project, RequestedOutput
from pm_mcp.domain.numbers import Exact
from pm_mcp.engine.cost_curve import CostCurve
from pm_mcp.engine.cpm import CpmResult
from pm_mcp.engine.crashing import CrashingResult
from pm_mcp.engine.gantt import GanttSchedule
from pm_mcp.engine.network import ActivityNetwork
from pm_mcp.engine.pert import CriticalPathVarianceResult, PertEstimatesResult
from pm_mcp.engine.probability import PercentileResult, ProbabilityResult


class CheckResult(BaseModel):
    name: str
    passed: bool
    detail: str


class CalculationValidationReport(BaseModel):
    passed: bool
    checks: list[CheckResult]

    @property
    def failures(self) -> list[CheckResult]:
        return [c for c in self.checks if not c.passed]


class ActivityDelayAnswer(BaseModel):
    activity_id: str
    total_slack: Exact
    free_slack: Exact
    is_critical: bool
    text: str


class QuestionAnswer(BaseModel):
    label: str
    question: str
    answer: str
    procedure: str = Field(default="", description="Variables, constants and operations (with the numbers used) behind the answer")
    outputs: list[RequestedOutput] = Field(default_factory=list)


class ProjectSolution(BaseModel):
    project: Project
    effective_outputs: list[RequestedOutput]
    network: ActivityNetwork
    pert: PertEstimatesResult | None = None
    cpm: CpmResult = Field(description="Primary schedule: t_e-based for PERT/BOTH, deterministic for CPM")
    cpm_deterministic: CpmResult | None = Field(default=None, description="Deterministic schedule when method is BOTH")
    variance: CriticalPathVarianceResult | None = None
    probabilities: list[ProbabilityResult] = Field(default_factory=list)
    percentiles: list[PercentileResult] = Field(default_factory=list)
    gantt: GanttSchedule | None = None
    crashing: CrashingResult | None = None
    cost_curve: CostCurve | None = Field(default=None, description="Period-by-period cost accumulation (S-curve) of the planned schedule")
    delay_answers: list[ActivityDelayAnswer] = Field(default_factory=list)
    validation: CalculationValidationReport | None = None
    answers: list[QuestionAnswer] = Field(default_factory=list)
    warnings: list[ValidationIssue] = Field(default_factory=list)
