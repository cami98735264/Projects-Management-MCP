"""Typed validation issues and exceptions shared by every layer."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field


class IssueSeverity(StrEnum):
    ERROR = "error"
    WARNING = "warning"


class IssueCode(StrEnum):
    SCHEMA = "schema"
    EMPTY_PROJECT = "empty_project"
    DUPLICATE_ACTIVITY_ID = "duplicate_activity_id"
    UNKNOWN_PREDECESSOR = "unknown_predecessor"
    SELF_DEPENDENCY = "self_dependency"
    DUPLICATE_PREDECESSOR = "duplicate_predecessor"
    CYCLE = "cycle"
    UNREACHABLE_ACTIVITY = "unreachable_activity"
    DISCONNECTED_NETWORK = "disconnected_network"
    NEGATIVE_VALUE = "negative_value"
    INCOMPLETE_ESTIMATES = "incomplete_estimates"
    INCONSISTENT_ESTIMATES = "inconsistent_estimates"
    INVALID_DUMMY = "invalid_dummy"
    METHOD_DATA_MISSING = "method_data_missing"
    AMBIGUOUS_METHOD = "ambiguous_method"
    INVALID_PROBABILITY_QUERY = "invalid_probability_query"
    PROBABILITY_WITHOUT_ESTIMATES = "probability_without_estimates"
    MISSING_QUERY = "missing_query"
    UNKNOWN_ACTIVITY_REFERENCE = "unknown_activity_reference"
    CRASH_DATA_MISSING = "crash_data_missing"
    INVALID_CRASH_DATA = "invalid_crash_data"
    INVALID_TIME_UNIT = "invalid_time_unit"
    AOA_INVALID_ARC = "aoa_invalid_arc"
    AOA_DUPLICATE_ARC = "aoa_duplicate_arc"
    AOA_CYCLE = "aoa_cycle"
    AOA_DISCONNECTED = "aoa_disconnected"
    AMBIGUOUS_QUERY = "ambiguous_query"
    UNREADABLE_DOCUMENT = "unreadable_document"
    UNSUPPORTED_FORMAT = "unsupported_format"
    DEGENERATE_DISTRIBUTION = "degenerate_distribution"
    MULTIPLE_CRITICAL_PATHS = "multiple_critical_paths"
    CRITICAL_PATHS_TRUNCATED = "critical_paths_truncated"
    CALCULATION_INCONSISTENCY = "calculation_inconsistency"
    WORKBOOK_INVALID = "workbook_invalid"
    OUTPUT_PATH = "output_path"


class ValidationIssue(BaseModel):
    """A specific, actionable problem (or warning) found while validating input or results."""

    code: IssueCode
    severity: IssueSeverity = IssueSeverity.ERROR
    message: str
    field_path: str | None = Field(default=None, description="JSON-path-like location, e.g. activities[3].predecessors")
    activity_ids: list[str] = Field(default_factory=list)
    suggestion: str | None = None
    needs_user_clarification: bool = Field(
        default=False,
        description="True when the value cannot be inferred safely and the user must be asked.",
    )


def has_errors(issues: list[ValidationIssue]) -> bool:
    return any(issue.severity == IssueSeverity.ERROR for issue in issues)


def format_issues(issues: list[ValidationIssue]) -> str:
    return "\n".join(f"[{i.severity}:{i.code}] {i.message}" for i in issues)


class PMError(Exception):
    """Base class for all engine errors."""


class DomainValidationError(PMError):
    """Raised when input violates the domain rules. Carries every issue found, not just the first."""

    def __init__(self, issues: list[ValidationIssue]):
        self.issues = issues
        super().__init__(format_issues([i for i in issues if i.severity == IssueSeverity.ERROR]) or "invalid input")


class CalculationConsistencyError(PMError):
    """Raised when the independent re-derivation of results disagrees with the engine."""

    def __init__(self, report):  # report: CalculationValidationReport (avoid import cycle)
        self.report = report
        failed = [c.name for c in report.checks if not c.passed]
        super().__init__(f"calculation validation failed: {', '.join(failed)}")


class WorkbookValidationError(PMError):
    def __init__(self, report):  # report: WorkbookValidationReport
        self.report = report
        super().__init__(f"workbook validation failed with {len(report.issues)} issue(s)")
