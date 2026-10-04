"""Round-trip validation of a generated workbook, independent of the library that wrote it.

The file is re-opened with openpyxl (formulas and cached values separately); every formula is parsed with the
whitelisted grammar of :mod:`formula_eval`, every reference resolved, every formula re-evaluated and compared
with the cached value and, when the writer's expectations are supplied, with the engine's value.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import openpyxl
from pydantic import BaseModel, Field

from pm_mcp.output.formula_eval import (
    ERROR_TOKEN_RE,
    SUPPORTED_FUNCTIONS,
    ExcelError,
    FormulaSyntaxError,
    WorkbookEvaluator,
    iter_nodes,
    parse_formula,
)

CellKey = tuple[str, int, int]
_REL_TOL = 1e-9
_ABS_TOL = 1e-9


class WorkbookIssue(BaseModel):
    kind: str = Field(description="missing_sheet, formula_error_token, formula_syntax, unknown_function, "
                                  "unknown_sheet_reference, dangling_reference, circular_reference, "
                                  "evaluation_error, cached_value_mismatch, expected_value_mismatch, "
                                  "missing_expected_formula, unreadable_workbook")
    sheet: str | None = None
    cell: str | None = None
    message: str


class WorkbookValidationReport(BaseModel):
    path: str
    passed: bool
    sheets: list[str] = Field(default_factory=list)
    formula_count: int = 0
    evaluated_formula_count: int = 0
    expected_values_checked: int = 0
    conditional_format_rules: int = 0
    issues: list[WorkbookIssue] = Field(default_factory=list)


def _coordinate(row: int, col: int) -> str:
    return f"{openpyxl.utils.get_column_letter(col)}{row}"


def values_match(actual: Any, expected: Any) -> bool:
    if actual is None:
        actual = ""
    if expected is None:
        expected = ""
    if isinstance(actual, bool) or isinstance(expected, bool):
        return actual == expected
    if isinstance(actual, (int, float)) and isinstance(expected, (int, float)):
        return math.isclose(float(actual), float(expected), rel_tol=_REL_TOL, abs_tol=_ABS_TOL)
    if isinstance(actual, str) and isinstance(expected, str):
        return actual == expected
    try:  # number stored as text or vice versa
        return math.isclose(float(actual), float(expected), rel_tol=_REL_TOL, abs_tol=_ABS_TOL)
    except (TypeError, ValueError):
        return False


def _check_static(node_formula: str, sheets: set[str], sheet: str, cell: str, issues: list[WorkbookIssue]) -> bool:
    """Syntax, error tokens, function whitelist and sheet references. Returns False when unparseable."""
    if ERROR_TOKEN_RE.search(node_formula.replace('""', "")):
        issues.append(WorkbookIssue(kind="formula_error_token", sheet=sheet, cell=cell,
                                    message=f"formula contains an error token: {node_formula}"))
    try:
        tree = parse_formula(node_formula)
    except FormulaSyntaxError as exc:
        issues.append(WorkbookIssue(kind="formula_syntax", sheet=sheet, cell=cell, message=str(exc)))
        return False
    for node in iter_nodes(tree):
        if node.kind == "func" and node.value not in SUPPORTED_FUNCTIONS:
            issues.append(WorkbookIssue(kind="unknown_function", sheet=sheet, cell=cell,
                                        message=f"function {node.value} is not supported: {node_formula}"))
        if node.kind in ("ref", "range") and node.value[0] is not None and node.value[0] not in sheets:
            issues.append(WorkbookIssue(kind="unknown_sheet_reference", sheet=sheet, cell=cell,
                                        message=f"reference to missing sheet {node.value[0]!r}: {node_formula}"))
    return True


def validate_workbook(
    path: str | Path,
    expected_sheets: list[str] | None = None,
    expected_values: dict[CellKey, Any] | None = None,
) -> WorkbookValidationReport:
    path = Path(path)
    report = WorkbookValidationReport(path=str(path), passed=False)
    try:
        wb = openpyxl.load_workbook(path)
        cached_wb = openpyxl.load_workbook(path, data_only=True)
    except Exception as exc:  # noqa: BLE001 — any failure to open is a validation failure
        report.issues.append(WorkbookIssue(kind="unreadable_workbook", message=f"{type(exc).__name__}: {exc}"))
        return report

    issues = report.issues
    report.sheets = list(wb.sheetnames)
    sheet_set = set(wb.sheetnames)
    for name in expected_sheets or []:
        if name not in sheet_set:
            issues.append(WorkbookIssue(kind="missing_sheet", sheet=name, message=f"promised sheet {name!r} is missing"))
    if expected_sheets is not None:
        for name in wb.sheetnames:
            if name not in expected_sheets:
                issues.append(WorkbookIssue(kind="unexpected_sheet", sheet=name, message=f"sheet {name!r} was not planned"))

    cells: dict[CellKey, Any] = {}
    formulas: list[CellKey] = []
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for c in row:
                value = c.value
                if value is None:
                    continue
                if not isinstance(value, (str, int, float, bool)):
                    value = str(value)
                key = (ws.title, c.row, c.column)
                cells[key] = value
                if isinstance(value, str) and value.startswith("="):
                    formulas.append(key)
                elif isinstance(value, str) and value in ("#DIV/0!", "#NAME?", "#REF!", "#VALUE!", "#NUM!", "#N/A"):
                    issues.append(WorkbookIssue(kind="formula_error_token", sheet=ws.title, cell=c.coordinate,
                                                message=f"cell holds the error value {value}"))

        for cf_range in ws.conditional_formatting:
            for rule in cf_range.rules:
                report.conditional_format_rules += 1
                for formula in rule.formula or []:
                    _check_static("=" + str(formula), sheet_set, ws.title, str(cf_range.sqref), issues)

    report.formula_count = len(formulas)
    evaluator = WorkbookEvaluator(cells, wb.sheetnames)
    parseable: list[CellKey] = []
    for key in formulas:
        if _check_static(cells[key], sheet_set, key[0], _coordinate(key[1], key[2]), issues):
            parseable.append(key)

    evaluated: dict[CellKey, Any] = {}
    for key in parseable:
        cell = _coordinate(key[1], key[2])
        try:
            evaluated[key] = evaluator.value(key)
        except ExcelError as exc:
            kind = "circular_reference" if exc.code == "#CIRCULAR" else "evaluation_error"
            issues.append(WorkbookIssue(kind=kind, sheet=key[0], cell=cell, message=f"{exc} in {cells[key]}"))
    report.evaluated_formula_count = len(evaluated)

    seen_dangling: set[tuple[CellKey, CellKey]] = set()
    for source, target in evaluator.blank_references:
        if (source, target) in seen_dangling:
            continue
        seen_dangling.add((source, target))
        issues.append(WorkbookIssue(kind="dangling_reference", sheet=source[0], cell=_coordinate(source[1], source[2]),
                                    message=f"refers to empty cell {target[0]}!{_coordinate(target[1], target[2])}"))

    for key, value in evaluated.items():
        cached = cached_wb[key[0]].cell(key[1], key[2]).value
        if not values_match(cached, value):
            issues.append(WorkbookIssue(kind="cached_value_mismatch", sheet=key[0], cell=_coordinate(key[1], key[2]),
                                        message=f"cached {cached!r} ≠ recalculated {value!r} for {cells[key]}"))

    if expected_values is not None:
        for key, expected in expected_values.items():
            cell = _coordinate(key[1], key[2])
            if key not in evaluated:
                if key not in cells or not str(cells[key]).startswith("="):
                    issues.append(WorkbookIssue(kind="missing_expected_formula", sheet=key[0], cell=cell,
                                                message=f"expected a formula giving {expected!r}"))
                continue
            report.expected_values_checked += 1
            if not values_match(evaluated[key], expected):
                issues.append(WorkbookIssue(kind="expected_value_mismatch", sheet=key[0], cell=cell,
                                            message=f"recalculated {evaluated[key]!r} ≠ engine {expected!r} for {cells[key]}"))

    report.passed = not issues
    return report
