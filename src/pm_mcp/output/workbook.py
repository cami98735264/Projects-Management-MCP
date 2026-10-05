"""Live-formula solution workbook.

Sheets are planned from the requested outputs and the data shape (:func:`plan_sheets`). Every derived number is
an Excel formula chained back to the Datos sheet, written together with the engine's value as the cached
result, and recorded in ``expected_values`` so :func:`validate_workbook` can re-evaluate each formula and compare
it with the engine independently of xlsxwriter.
"""

from __future__ import annotations

import io
import math
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from fractions import Fraction
from pathlib import Path
from typing import Any

import xlsxwriter
from xlsxwriter.utility import xl_rowcol_to_cell

from pm_mcp.domain.models import Project, Provenance, ProbabilityQueryKind, RequestedOutput as O
from pm_mcp.domain.numbers import fraction_text, number_text, round_half_up
from pm_mcp.engine.cost_curve import cost_curve_applies
from pm_mcp.engine.cpm import CpmResult
from pm_mcp.engine.network import enumerate_paths
from pm_mcp.i18n import t
from pm_mcp.output.answers import OUTPUT_LABELS
from pm_mcp.output.formula_eval import excel_text
from pm_mcp.output.network_cells import NODE_COLS, NODE_ROWS, CellNetwork, LineCell, plan_many
from pm_mcp.output.recording import RecordingSheet
from pm_mcp.solution import ProjectSolution, QuestionAnswer


class SheetKind(StrEnum):
    PROBLEM = "problem"
    DATA = "data"
    PERT = "pert"
    CPM = "cpm"
    CPM_DETERMINISTIC = "cpm_det"
    NETWORK = "network"
    GANTT = "gantt"
    PROBABILITY = "probability"
    CRASHING = "crashing"
    STEP_NETWORK = "step_network"
    COST_CURVE = "cost_curve"
    RESULTS = "results"


SCHEDULE_OUTPUTS = {
    O.GANTT, O.NETWORK_DIAGRAM, O.EARLY_LATE_TIMES, O.SLACK, O.CRITICAL_PATH, O.EXPECTED_DURATION, O.VARIANCE,
    O.ACTIVITY_ESTIMATES, O.PROBABILITY_QUERY, O.PERCENTILE_DURATION, O.ACTIVITY_MAX_DELAY,
}


def plan_sheets(project: Project, outputs: list[O]) -> list[SheetKind]:
    """Which sheets the workbook contains, in order, keyed only on the requested outputs and the data shape."""
    wanted = set(outputs)
    schedule = bool(wanted & SCHEDULE_OUTPUTS)
    probability = bool(wanted & {O.PROBABILITY_QUERY, O.PERCENTILE_DURATION}) and project.uses_estimates
    crashing = O.CRASHING in wanted
    plan = [SheetKind.PROBLEM, SheetKind.DATA]
    if project.uses_estimates and (schedule or probability):
        plan.append(SheetKind.PERT)
    if schedule:
        plan.append(SheetKind.CPM)
        if project.uses_estimates and project.uses_deterministic_durations:
            plan.append(SheetKind.CPM_DETERMINISTIC)
    if O.NETWORK_DIAGRAM in wanted:
        plan.append(SheetKind.NETWORK)
    if O.GANTT in wanted:
        plan.append(SheetKind.GANTT)
    if probability:
        plan.append(SheetKind.PROBABILITY)
    if crashing:
        plan.append(SheetKind.CRASHING)
        if O.NETWORK_DIAGRAM in wanted:
            plan.append(SheetKind.STEP_NETWORK)
    if schedule and cost_curve_applies(project.activities):
        plan.append(SheetKind.COST_CURVE)
    plan.append(SheetKind.RESULTS)
    return plan


@dataclass
class WorkbookBuild:
    path: str
    sheet_names: list[str]
    expected_values: dict[tuple[str, int, int], Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


_EXTRA: dict[str, dict[str, str]] = {
    "es": {
        "options": "Opciones", "data_note": "Tabla de actividades: hoja '{sheet}'.",
        "cpm_note": "Holgura = IL − IC = TL − TC; actividades con holgura 0 = ruta crítica. IC = máx(TC de predecesoras); "
                    "TL = mín(IL de sucesoras).",
        "id": "id", "p": "p", "card_act": "Act.", "card_t": "t", "card_es": "IC", "card_ef": "TC", "card_ls": "IL",
        "card_lf": "TL", "warnings": "Advertencias", "field": "Campo", "message": "Mensaje",
        "came_from": "Se redujo {acts} en {d} {unit} (incremento de costo {inc})",
        "start": "Programa normal (sin reducciones)",
        "interp_pct": "P = {p} → {w} {unit}", "fractional": "Celdas Gantt parcialmente ocupadas (tiempos fraccionarios) "
                                                             "se sombrean completas: {cells}.",
        "partial": "{id}: columnas {cols}",
    },
    "en": {
        "options": "Options", "data_note": "Activity table: sheet '{sheet}'.",
        "cpm_note": "Slack = LS − ES = LF − EF; activities with slack 0 = critical path. ES = max(EF of predecessors); "
                    "LF = min(LS of successors).",
        "id": "id", "p": "p", "card_act": "Act.", "card_t": "t", "card_es": "ES", "card_ef": "EF", "card_ls": "LS",
        "card_lf": "LF", "warnings": "Warnings", "field": "Field", "message": "Message",
        "came_from": "{acts} shortened by {d} {unit} (cost increase {inc})",
        "start": "Normal plan (no reductions)",
        "interp_pct": "P = {p} → {w} {unit}", "fractional": "Gantt cells only partly occupied (fractional times) are "
                                                             "shaded whole: {cells}.",
        "partial": "{id}: columns {cols}",
    },
}

GREY = "#D9D9D9"
CRITICAL_GREY = "#BFBFBF"
BAR = "#4F81BD"
TOL = "0.000000001"
LINE_CRITICAL = "#C00000"     # critical arcs: red double lines
LINE_ROW_HEIGHT = 15.0        # drawing rows: fixed height so the box-drawing glyphs join vertically
NODE_COL_WIDTH = 6.7
CHANNEL_COL_WIDTH = 2.7


def _q(sheet: str) -> str:
    return "'" + sheet.replace("'", "''") + "'"


def _cell(row: int, col: int, absolute: bool = False) -> str:
    return xl_rowcol_to_cell(row - 1, col - 1, absolute, absolute)


def _xref(sheet: str, row: int, col: int) -> str:
    return f"{_q(sheet)}!{_cell(row, col, True)}"


def _num(value: Any) -> float:
    return float(value)


def _frac_text_formula(cell: str) -> str:
    """Excel text of a value as the engine displays exact numbers: integers plainly, others as a fraction."""
    return f'IF(ROUND({cell},9)=ROUND({cell},0),ROUND({cell},0),TEXT({cell},"#/###"))'


def _frac_text_value(value: Fraction) -> str:
    return str(value.numerator) if value.denominator == 1 else excel_text(float(value), "#/###")


RESULT_WIDTHS = (8.7, 38.7, 62.7, 88.7, 18.7)  # Resultados: Parte, Pregunta, Respuesta, Procedimiento, Valor clave


class _Writer:
    """Classic workbook writer. Every worksheet is wrapped in a :class:`RecordingSheet` (forwards each call, keeps a
    record with the format spec and the active tags) so the progressive layout can replay the same cells."""

    def __init__(self, solution: ProjectSolution, path: Path | io.BytesIO):
        self.s = solution
        self.p: Project = solution.project
        self.lang = str(self.p.metadata.language)
        self.x = _EXTRA[self.lang]
        self.unit = self.p.metadata.time_unit
        self.singular, self.plural = self.unit.labels(self.lang)
        self.plan = plan_sheets(self.p, solution.effective_outputs)
        self.names = {k: t(self.lang, f"sheet.{k.value}") for k in self.plan}
        self.path = path
        self.wb = xlsxwriter.Workbook(path if isinstance(path, io.BytesIO) else str(path), {"in_memory": True})
        self.wb.set_properties({"created": datetime.now(), "title": self.p.metadata.title})
        self.fmt_specs: dict[int, dict[str, Any]] = {}
        self.ws = {k: RecordingSheet(self.wb.add_worksheet(self.names[k]), self.spec_of) for k in self.plan}
        self.marks: dict[str, Any] = {}  # boundary rows the progressive layout needs (see progressive.py)
        self.expected: dict[tuple[str, int, int], Any] = {}
        self.notes: list[str] = []
        f = self._format
        self.f_title = f({"font_size": 13})
        self.f_heading = f({})
        self.f_head = f({"bg_color": GREY, "border": 1, "text_wrap": True, "valign": "top"})
        self.f_int = f({"num_format": "0"})
        self.f_dec = f({"num_format": "0.0000"})
        self.f_pct = f({"num_format": "0.00%"})
        self.f_wrap = f({"text_wrap": True, "valign": "top"})
        self.f_cell = f({"text_wrap": True, "valign": "top", "border": 1})
        self.f_label = f({"align": "center", "valign": "top", "border": 1, "font_size": 12})
        self.f_answer = f({"text_wrap": True, "valign": "top", "border": 1})
        self.f_key = f({"align": "center", "valign": "top", "border": 1, "text_wrap": True})
        self.f_key_int = f({"align": "center", "valign": "top", "border": 1, "num_format": "0"})
        self.f_key_dec = f({"align": "center", "valign": "top", "border": 1, "num_format": "0.0000"})
        self.f_center = f({"align": "center", "border": 1})
        self.f_card_int = f({"num_format": "0", "align": "center", "border": 1})
        self.f_card_dec = f({"num_format": "0.0000", "align": "center", "border": 1})
        self.f_card_name = f({"align": "center", "border": 1})
        self.f_crit = f({"bg_color": CRITICAL_GREY})
        self.f_bar = f({"bg_color": BAR, "font_color": BAR})
        self.f_bar_cell = f({"align": "center"})
        self.cpm_rows: dict[SheetKind, dict[str, int]] = {}
        self.data_row: dict[str, int] = {}
        self.data_col: dict[str, int] = {}
        self.pert_row: dict[str, int] = {}
        self.pert_var_cell: tuple[int, int] | None = None
        self.prob_cells: dict[int, tuple[int, int]] = {}  # index in project queries → key cell
        self.crash: dict[str, Any] = {}
        self.curve: dict[str, Any] = {}
        self._line_formats: dict[tuple[str, bool], Any] = {}
        self._card_formats: dict[tuple, Any] = {}

    # ------------------------------------------------------------------ primitives

    def _format(self, spec: dict[str, Any]):
        """Every cell format goes through here: nothing in the workbook is bold (plain, hand-made look)."""
        full = {**spec, "bold": False}
        fmt = self.wb.add_format(full)
        self.fmt_specs[id(fmt)] = full
        return fmt

    def spec_of(self, fmt: Any) -> dict[str, Any]:
        return self.fmt_specs.get(id(fmt), {})

    @contextmanager
    def tagged(self, kind: SheetKind, *tags: str):
        """Tag every call made inside the block (the progressive layout decides the step of a cell from them)."""
        with self.ws[kind].tagged(*tags):
            yield

    def has(self, kind: SheetKind) -> bool:
        return kind in self.ws

    def name(self, kind: SheetKind) -> str:
        return self.names[kind]

    def numfmt(self, value: Any, card: bool = False):
        if isinstance(value, (int, float, Fraction)) and not isinstance(value, bool):
            integral = float(value).is_integer()
            if card:
                return self.f_card_int if integral else self.f_card_dec
            return self.f_int if integral else self.f_dec
        return None

    def put(self, kind: SheetKind, row: int, col: int, value: Any, fmt=None) -> None:
        if value is None:
            return
        ws = self.ws[kind]
        if isinstance(value, (int, float, Fraction)) and not isinstance(value, bool):
            ws.write_number(row - 1, col - 1, float(value), fmt or self.numfmt(value))
        elif value == "":
            return
        else:
            ws.write_string(row - 1, col - 1, str(value), fmt)

    def formula(self, kind: SheetKind, row: int, col: int, text: str, value: Any, fmt=None) -> None:
        cached: Any = value
        if isinstance(value, (int, Fraction)) and not isinstance(value, bool):
            cached = float(value)
        if fmt is None:
            fmt = self.numfmt(cached)
        self.ws[kind].write_formula(row - 1, col - 1, text if text.startswith("=") else "=" + text, fmt, cached)
        self.expected[(self.names[kind], row, col)] = cached

    def header(self, kind: SheetKind, row: int, col: int, labels: list[str]) -> None:
        for i, label in enumerate(labels):
            self.put(kind, row, col + i, label, self.f_head)

    def grey_rule(self, kind: SheetKind, first: tuple[int, int], last: tuple[int, int], criteria: str) -> None:
        rng = f"{_cell(*first)}:{_cell(*last)}"
        self.ws[kind].conditional_format(rng, {"type": "formula", "criteria": "=" + criteria, "format": self.f_crit})

    def prov_label(self, value: Provenance | str) -> str:
        return t(self.lang, f"prov.{Provenance(value).value}")

    # ------------------------------------------------------------------ Datos

    def write_data(self) -> None:
        k = SheetKind.DATA
        ws = self.ws[k]
        acts = self.p.activities
        method_text = t(self.lang, f"problem.method.{self.p.calculation_method.value}")
        self.put(k, 1, 1, t(self.lang, "title.data"), self.f_title)
        self.put(k, 2, 1, f"{t(self.lang, 'time_unit')}: {self.plural} · {t(self.lang, 'method')}: {method_text}")
        headers = [t(self.lang, "activity"), t(self.lang, "description"), t(self.lang, "predecessors"), t(self.lang, "dummy")]
        cols: list[str] = []
        if self.p.uses_deterministic_durations or any(a.duration is not None and not a.is_dummy for a in acts):
            cols.append("duration")
            headers.append(f"{t(self.lang, 'duration')} ({self.plural})")
        if self.p.uses_estimates:
            cols += ["a", "m", "b"]
            headers += [t(self.lang, "optimistic"), t(self.lang, "most_likely"), t(self.lang, "pessimistic")]
        if any(a.crash is not None for a in acts):
            cols += ["crash_duration", "normal_cost", "crash_cost"]
            headers += [t(self.lang, "crash_duration"), t(self.lang, "normal_cost"), t(self.lang, "crash_cost")]
        self.header(k, 4, 1, headers)
        self.data_col = {c: 5 + i for i, c in enumerate(cols)}
        for i, a in enumerate(acts):
            r = 5 + i
            self.data_row[a.id] = r
            self.put(k, r, 1, a.id)
            self.put(k, r, 2, a.name or "")
            self.put(k, r, 3, ", ".join(a.predecessors) or t(self.lang, "none"))
            self.put(k, r, 4, t(self.lang, "yes" if a.is_dummy else "no"))
            zero = Fraction(0) if a.is_dummy else None
            values = {
                "duration": a.duration if a.duration is not None else zero,
                "a": a.optimistic_duration if a.optimistic_duration is not None else zero,
                "m": a.most_likely_duration if a.most_likely_duration is not None else zero,
                "b": a.pessimistic_duration if a.pessimistic_duration is not None else zero,
                "crash_duration": a.crash.crash_duration if a.crash else None,
                "normal_cost": a.crash.normal_cost if a.crash else None,
                "crash_cost": a.crash.crash_cost if a.crash else None,
            }
            for c in cols:
                self.put(k, r, self.data_col[c], values[c])
        ws.set_column(0, 0, 10.7)
        ws.set_column(1, 1, 18.7)
        ws.set_column(3, 3, 13.7)
        ws.freeze_panes(4, 1)

    def data_ref(self, aid: str, col: str) -> str | None:
        if col not in self.data_col:
            return None
        return _xref(self.name(SheetKind.DATA), self.data_row[aid], self.data_col[col])

    # ------------------------------------------------------------------ CPM

    def cpm_layout(self, kind: SheetKind, schedule: CpmResult) -> None:
        self.cpm_rows[kind] = {r.activity_id: 6 + i for i, r in enumerate(schedule.activities)}

    def write_cpm(self, kind: SheetKind, schedule: CpmResult, pert_based: bool) -> None:
        ws = self.ws[kind]
        rows = self.cpm_rows[kind]
        title = "title.cpm_pert" if pert_based else "title.cpm_det"
        self.put(kind, 1, 1, t(self.lang, title), self.f_title)
        label = "expected_project_duration" if pert_based else "project_duration"
        self.put(kind, 2, 1, f"{t(self.lang, label)} ({self.plural})", self.f_heading)
        ends = [r for r in schedule.activities if not r.successors]
        self.formula(kind, 2, 4, "=MAX(" + ",".join(_cell(rows[r.activity_id], 6) for r in ends) + ")",
                     schedule.project_duration)
        paths = "; ".join(" – ".join(p) for p in schedule.critical_paths)
        self.put(kind, 3, 1, t(self.lang, "critical_path" if len(schedule.critical_paths) == 1 else "critical_paths"),
                 self.f_heading)
        self.put(kind, 3, 4, paths, self.f_heading)
        dur_head = f"{t(self.lang, 'duration')} (t_e)" if pert_based else f"{t(self.lang, 'duration')} ({self.plural})"
        self.header(kind, 5, 1, [t(self.lang, "activity"), t(self.lang, "predecessors"), t(self.lang, "successors"),
                                 dur_head, t(self.lang, "es"), t(self.lang, "ef"), t(self.lang, "ls"), t(self.lang, "lf"),
                                 t(self.lang, "slack_ls_es"), t(self.lang, "slack_lf_ef"), t(self.lang, "free_slack"),
                                 t(self.lang, "critical")])
        yes, no = t(self.lang, "yes"), t(self.lang, "no")
        for r in schedule.activities:
            row = rows[r.activity_id]
            self.put(kind, row, 1, r.activity_id)
            self.put(kind, row, 2, ", ".join(r.predecessors) or "—")
            self.put(kind, row, 3, ", ".join(r.successors) or "—")
            if pert_based and self.has(SheetKind.PERT):
                self.formula(kind, row, 4, "=" + _xref(self.name(SheetKind.PERT), self.pert_row[r.activity_id], 5), r.duration)
            elif not pert_based and self.data_ref(r.activity_id, "duration"):
                self.formula(kind, row, 4, "=" + self.data_ref(r.activity_id, "duration"), r.duration)
            else:
                self.put(kind, row, 4, r.duration)
            if r.predecessors:
                self.formula(kind, row, 5, "=MAX(" + ",".join(_cell(rows[p], 6) for p in r.predecessors) + ")", r.early_start)
            else:
                self.put(kind, row, 5, r.early_start)
            self.formula(kind, row, 6, f"={_cell(row, 5)}+{_cell(row, 4)}", r.early_finish)
            self.formula(kind, row, 7, f"={_cell(row, 8)}-{_cell(row, 4)}", r.late_start)
            if r.successors:
                self.formula(kind, row, 8, "=MIN(" + ",".join(_cell(rows[s], 7) for s in r.successors) + ")", r.late_finish)
                free = "MIN(" + ",".join(_cell(rows[s], 5) for s in r.successors) + ")"
            else:
                self.formula(kind, row, 8, "=$D$2", r.late_finish)
                free = "$D$2"
            self.formula(kind, row, 9, f"=ROUND({_cell(row, 7)}-{_cell(row, 5)},10)", r.total_slack)
            self.formula(kind, row, 10, f"=ROUND({_cell(row, 8)}-{_cell(row, 6)},10)", r.late_finish - r.early_finish)
            self.formula(kind, row, 11, f"=ROUND({free}-{_cell(row, 6)},10)", r.free_slack)
            self.formula(kind, row, 12, f'=IF(ABS({_cell(row, 9)})<{TOL},"{yes}","{no}")', yes if r.is_critical else no)
        last = 5 + len(schedule.activities)
        self.grey_rule(kind, (6, 1), (last, 12), f"ABS($I6)<{TOL}")
        with self.tagged(kind, "note"):
            self.put(kind, last + 2, 1, self.x["cpm_note"])
        ws.set_column(0, 0, 12.7)
        ws.set_column(1, 2, 11)
        ws.set_column(3, 11, 13.7)
        ws.freeze_panes(5, 1)

    # ------------------------------------------------------------------ PERT

    def write_pert(self) -> None:
        k = SheetKind.PERT
        ws = self.ws[k]
        pert, var = self.s.pert, self.s.variance
        self.put(k, 1, 1, t(self.lang, "title.pert"), self.f_title)
        self.put(k, 2, 1, f"{t(self.lang, 'pert.formula_te')}        {t(self.lang, 'pert.formula_var')}", self.f_heading)
        self.header(k, 5, 1, [t(self.lang, "activity"), "a", "m", "b", "t_e = (a + b + 4m)/6", "σ² = [(b − a)/6]²",
                              t(self.lang, "pert.te_fraction"), t(self.lang, "pert.var_fraction"),
                              t(self.lang, "pert.te_calc"), t(self.lang, "pert.var_calc"), t(self.lang, "critical")])
        yes, no = t(self.lang, "yes"), t(self.lang, "no")
        crit = {r.activity_id: r.is_critical for r in self.s.cpm.activities}
        for i, e in enumerate(pert.estimates):
            r = 6 + i
            self.put(k, r, 1, e.activity_id)
            for col, key, value in ((2, "a", e.optimistic), (3, "m", e.most_likely), (4, "b", e.pessimistic)):
                ref = self.data_ref(e.activity_id, key)
                if ref:
                    self.formula(k, r, col, "=" + ref, value)
                else:
                    self.put(k, r, col, value)
            self.formula(k, r, 5, f"=({_cell(r, 2)}+{_cell(r, 4)}+4*{_cell(r, 3)})/6", e.expected_duration)
            self.formula(k, r, 6, f"=(({_cell(r, 4)}-{_cell(r, 2)})/6)^2", e.variance)
            self.put(k, r, 7, fraction_text(e.expected_duration))
            self.put(k, r, 8, fraction_text(e.variance))
            self.put(k, r, 9, e.expected_duration_working)
            self.put(k, r, 10, e.variance_working)
            if self.has(SheetKind.CPM):
                self.formula(k, r, 11, "=" + _xref(self.name(SheetKind.CPM), self.cpm_rows[SheetKind.CPM][e.activity_id], 12),
                             yes if crit[e.activity_id] else no)
            else:
                self.put(k, r, 11, yes if crit[e.activity_id] else no)
        last = 5 + len(pert.estimates)
        self.grey_rule(k, (6, 1), (last, 11), f'$K6="{yes}"')
        if var is not None:
            self.ws[k].tags.append("variance")  # project σ² / σ rows: a later step than the per-activity table
            self.marks["pert_variance_first"] = last + 2
            r = last + 2
            self.put(k, r, 1, t(self.lang, "pert.path_used"), self.f_heading)
            self.put(k, r, 5, " – ".join(var.selected_activities), self.f_heading)
            r += 1
            self.put(k, r, 1, t(self.lang, "pert.project_variance"), self.f_heading)
            terms = [_cell(self.pert_row[a], 6) for a in var.selected_activities]
            self.formula(k, r, 6, "=" + ("+".join(terms) if terms else "0"), var.total_variance)
            self.put(k, r, 7, fraction_text(var.total_variance))
            self.put(k, r, 9, var.variance_working)
            self.pert_var_cell = (r, 6)
            r += 1
            self.put(k, r, 1, t(self.lang, "pert.project_std"), self.f_heading)
            self.formula(k, r, 6, f"=SQRT({_cell(r - 1, 6)})", var.standard_deviation)
            self.put(k, r, 9, var.standard_deviation_working)
            r += 1
            self.put(k, r, 1, t(self.lang, "expected_project_duration"), self.f_heading)
            if self.has(SheetKind.CPM):
                self.formula(k, r, 5, "=" + _xref(self.name(SheetKind.CPM), 2, 4), self.s.cpm.project_duration)
            else:
                self.put(k, r, 5, self.s.cpm.project_duration)
            if len(var.critical_paths) > 1:
                r += 2
                self.put(k, r, 1, t(self.lang, "pert.path_variances"), self.f_heading)
                self.put(k, r, 5, t(self.lang, f"pert.strategy.{var.strategy.value}"))
                for pv in var.critical_paths:
                    r += 1
                    self.put(k, r, 1, " – ".join(pv.path))
                    terms = [_cell(self.pert_row[a], 6) for a in pv.path if a in self.pert_row]
                    self.formula(k, r, 6, "=" + ("+".join(terms) if terms else "0"), pv.variance)
                    self.put(k, r, 7, fraction_text(pv.variance))
            self.ws[k].tags.pop()
        ws.set_column(0, 0, 9.7)
        ws.set_column(4, 5, 14.7)
        ws.set_column(8, 9, 30.7)
        ws.set_column(10, 10, 10.7)

    # ------------------------------------------------------------------ Probabilidad

    def write_probability(self) -> None:
        k = SheetKind.PROBABILITY
        ws = self.ws[k]
        var = self.s.variance
        self.put(k, 1, 1, t(self.lang, "title.probability"), self.f_title)
        self.put(k, 3, 1, t(self.lang, "prob.params"), self.f_heading)
        self.put(k, 4, 1, t(self.lang, "expected_project_duration"))
        self.formula(k, 4, 3, "=" + _xref(self.name(SheetKind.CPM), 2, 4), self.s.cpm.project_duration)
        self.put(k, 5, 1, "σ² (Σ σ²)")
        self.formula(k, 5, 3, "=" + _xref(self.name(SheetKind.PERT), *self.pert_var_cell), var.total_variance)
        self.put(k, 6, 1, "σ = √σ²")
        self.formula(k, 6, 3, "=SQRT(C5)", var.standard_deviation)
        self.put(k, 7, 1, t(self.lang, "prob.z_formula"), self.f_heading)
        self.put(k, 8, 1, t(self.lang, "prob.table_note"))
        degenerate = var.standard_deviation == 0
        index_of = {id(q): i for i, q in enumerate(self.p.probability_queries)}
        r = 10
        if self.s.probabilities:
            self.header(k, r, 1, [t(self.lang, "prob.question"), t(self.lang, "prob.kind"), t(self.lang, "prob.tp"),
                                  t(self.lang, "prob.z_exact"), t(self.lang, "prob.cdf_exact"), t(self.lang, "prob.z_table"),
                                  t(self.lang, "prob.cdf_table"), t(self.lang, "prob.p_exact"), t(self.lang, "prob.p_table"),
                                  t(self.lang, "prob.interpretation")])
            r += 1
            for res in self.s.probabilities:
                q = res.query
                qi = self._query_index(q, index_of)
                kind_text = t(self.lang, f"prob.kind.{q.kind.value}",
                              l=number_text(q.lower_bound) if q.lower_bound is not None else "",
                              u=number_text(q.upper_bound) if q.upper_bound is not None else "")
                first = r
                for comp in res.z_computations:
                    self.put(k, r, 1, q.id or str(qi + 1))
                    self.put(k, r, 2, kind_text)
                    self.put(k, r, 3, comp.target_duration)
                    if degenerate:
                        self.put(k, r, 5, comp.cdf_exact)
                        self.put(k, r, 7, comp.cdf_table)
                    else:
                        self.formula(k, r, 4, f"=(C{r}-$C$4)/$C$6", comp.z_exact)
                        self.formula(k, r, 5, f"=NORMSDIST(D{r})", comp.cdf_exact)
                        self.formula(k, r, 6, f"=ROUND(D{r},2)", comp.z_table)
                        self.formula(k, r, 7, f"=ROUND(NORMSDIST(F{r}),4)", comp.cdf_table)
                    r += 1
                if degenerate:
                    self.put(k, first, 8, res.probability_exact)
                    self.put(k, first, 9, res.probability_table)
                elif q.kind == ProbabilityQueryKind.AT_MOST:
                    self.formula(k, first, 8, f"=E{first}", res.probability_exact)
                    self.formula(k, first, 9, f"=G{first}", res.probability_table)
                elif q.kind == ProbabilityQueryKind.AT_LEAST:
                    self.formula(k, first, 8, f"=1-E{first}", res.probability_exact)
                    self.formula(k, first, 9, f"=ROUND(1-G{first},4)", res.probability_table)
                else:
                    self.formula(k, first, 8, f"=E{first}-E{first + 1}", res.probability_exact)
                    self.formula(k, first, 9, f"=ROUND(G{first}-G{first + 1},4)", res.probability_table)
                self.put(k, first, 10, f"{res.expression} = {res.probability:.4f}")
                self.prob_cells[qi] = (first, 9 if res.method.value == "table" else 8)
            r += 1
        if self.s.percentiles:
            self.ws[k].tags.append("pct")  # percentile section: its own step ("Duración objetivo")
            self.marks["percentile_first"] = r
            self.put(k, r, 1, t(self.lang, "prob.percentiles"), self.f_heading)
            r += 1
            self.header(k, r, 1, [t(self.lang, "prob.question"), t(self.lang, "prob.target"), t(self.lang, "prob.z_exact"),
                                  t(self.lang, "prob.tp_exact"), t(self.lang, "prob.z_table"), t(self.lang, "prob.cdf_table"),
                                  t(self.lang, "prob.tp_table"), t(self.lang, "prob.tp_whole"),
                                  t(self.lang, "prob.interpretation")])
            r += 1
            for res in self.s.percentiles:
                qi = self._query_index(res.query, index_of)
                self.put(k, r, 1, res.query.id or str(qi + 1))
                self.put(k, r, 2, res.target_probability, self.f_dec)
                if degenerate:
                    self.put(k, r, 4, res.duration_exact)
                    self.put(k, r, 7, res.duration_table)
                    self.put(k, r, 8, res.duration_whole_units)
                else:
                    self.formula(k, r, 3, f"=NORMSINV(B{r})", res.z_exact)
                    self.formula(k, r, 4, f"=$C$4+C{r}*$C$6", res.duration_exact)
                    self.put(k, r, 5, res.z_table, self.f_dec)
                    self.formula(k, r, 6, f"=ROUND(NORMSDIST(E{r}),4)", res.cdf_at_z_table)
                    self.formula(k, r, 7, f"=$C$4+E{r}*$C$6", res.duration_table)
                    source = "G" if res.method.value == "table" else "D"
                    self.formula(k, r, 8, f"=ROUNDUP(ROUND({source}{r},9),0)", res.duration_whole_units)
                self.put(k, r, 9, self.x["interp_pct"].format(p=f"{res.target_probability:g}", w=res.duration_whole_units,
                                                              unit=self.unit.label(res.duration_whole_units, self.lang)))
                self.prob_cells[qi] = (r, 8)
                r += 1
            self.put(k, r, 1, t(self.lang, "prob.table_z_note"))
            self.ws[k].tags.pop()
        if degenerate:
            self.put(k, r + 1, 1, t(self.lang, "prob.degenerate"))
        ws.set_column(0, 0, 12.7)
        ws.set_column(1, 1, 14.7)
        ws.set_column(2, 8, 12.7)
        ws.set_column(9, 9, 40.7)

    def _query_index(self, query, index_of: dict[int, int]) -> int:
        for i, q in enumerate(self.p.probability_queries):
            if q == query:
                return i
        return index_of.get(id(query), 0)

    # ------------------------------------------------------------------ Gantt

    def write_gantt(self) -> None:
        k = SheetKind.GANTT
        ws = self.ws[k]
        g = self.s.gantt
        cpm_name, cpm_rows = self.name(SheetKind.CPM), self.cpm_rows[SheetKind.CPM]
        self.put(k, 1, 1, t(self.lang, "title.gantt"), self.f_title)
        self.put(k, 2, 1, t(self.lang, "gantt.note"))
        first_col = 6
        last_col = first_col + g.horizon - 1
        obs_col = last_col + 1
        for col, key in ((1, "activities"), (2, "gantt.row"), (3, "gantt.start"), (4, "gantt.finish"), (5, "slack")):
            ws.merge_range(3, col - 1, 4, col - 1, t(self.lang, key), self.f_head)
        if g.horizon > 1:
            ws.merge_range(3, first_col - 1, 3, last_col - 1, t(self.lang, "gantt.duration_in", unit=self.plural), self.f_head)
        else:
            self.put(k, 4, first_col, t(self.lang, "gantt.duration_in", unit=self.plural), self.f_head)
        ws.merge_range(3, obs_col - 1, 4, obs_col - 1, t(self.lang, "gantt.observations"), self.f_head)
        for c in g.columns:
            self.put(k, 5, first_col + c - 1, c, self.f_head)
        crit_text = t(self.lang, "critical_activity")
        prefix = t(self.lang, "slack_of", value="\0", unit="").split("\0")[0]
        partial: list[str] = []
        for i, row in enumerate(g.rows):
            r = 6 + 2 * i
            src = cpm_rows[row.activity_id]
            ws.merge_range(r - 1, 0, r, 0, row.activity_id, self.f_center)
            self.put(k, r, 2, t(self.lang, "gantt.early"))
            self.put(k, r + 1, 2, t(self.lang, "gantt.late"))
            self.formula(k, r, 3, "=" + _xref(cpm_name, src, 5), row.early_start)
            self.formula(k, r, 4, "=" + _xref(cpm_name, src, 6), row.early_finish)
            self.formula(k, r + 1, 3, "=" + _xref(cpm_name, src, 7), row.late_start)
            self.formula(k, r + 1, 4, "=" + _xref(cpm_name, src, 8), row.late_finish)
            ws.merge_range(r - 1, 4, r, 4, "", self.f_center)
            self.formula(k, r, 5, "=" + _xref(cpm_name, src, 9), row.total_slack, self.numfmt(row.total_slack, card=True))
            for rr, bars in ((r, row.early_bar_columns), (r + 1, row.late_bar_columns)):
                for c in g.columns:
                    col = first_col + c - 1
                    head = xl_rowcol_to_cell(4, col - 1, True, False)  # e.g. F$5: the period number
                    start, finish = f"$C{rr}", f"$D{rr}"
                    text = (f'=IF(AND(ROUND({finish},9)>ROUND({start},9),{head}>ROUND({start},9),'
                            f'{head}-1<ROUND({finish},9)),"█","")')
                    self.formula(k, rr, col, text, "█" if c in bars else "", self.f_bar_cell)
            slack_cell = f"$E${r}"
            sing, plur = self.unit.labels(self.lang)
            obs = (f'=IF(ABS({slack_cell})<{TOL},"{crit_text}","{prefix}"&{_frac_text_formula(slack_cell)}&" "&'
                   f'IF(ROUND({slack_cell},4)=1,"{sing}","{plur}"))')
            if row.total_slack == 0:
                obs_value = crit_text
            else:
                label = sing if round_half_up(row.total_slack, 4) == 1 else plur
                obs_value = f"{prefix}{_frac_text_value(row.total_slack)} {label}"
            ws.merge_range(r - 1, obs_col - 1, r, obs_col - 1, "", self.f_wrap)
            self.formula(k, r, obs_col, obs, obs_value, self.f_wrap)
            for rng_col in (1, obs_col):
                self.grey_rule(k, (r, rng_col), (r + 1, rng_col), f"ABS({slack_cell})<{TOL}")
            if row.partial_columns:
                partial.append(self.x["partial"].format(id=row.activity_id, cols=", ".join(map(str, row.partial_columns))))
        last_row = 5 + 2 * len(g.rows)
        if g.rows:
            ws.conditional_format(f"{_cell(6, first_col)}:{_cell(last_row, last_col)}",
                                  {"type": "cell", "criteria": "==", "value": '"█"', "format": self.f_bar})
        if partial:
            note = self.x["fractional"].format(cells="; ".join(partial))
            self.put(k, last_row + 2, 1, note)
            self.notes.append(note)
        ws.set_column(0, 0, 14.7)
        ws.set_column(1, 1, 9.7)
        ws.set_column(2, 4, 7.7)
        ws.set_column(first_col - 1, last_col - 1, 4.0)
        ws.set_column(obs_col - 1, obs_col - 1, 26.7)
        ws.freeze_panes(5, 5)

    # ------------------------------------------------------------------ network pictures

    def _line_format(self, align: str, critical: bool):
        key = (align, critical)
        if key not in self._line_formats:
            self._line_formats[key] = self._format({
                "font_name": "Consolas", "font_size": 14, "align": align, "valign": "vcenter",
                "font_color": LINE_CRITICAL if critical else "#000000"})
        return self._line_formats[key]

    def _card_format(self, value: Any, critical: bool, text: bool = False):
        integral = isinstance(value, (int, float, Fraction)) and float(value).is_integer()
        key = (critical, "text" if text else ("int" if integral else "dec"))
        if key not in self._card_formats:
            spec: dict[str, Any] = {"align": "center", "valign": "vcenter", "border": 1}
            if not text:
                spec["num_format"] = "0" if integral else "0.0000"
            if critical:
                spec["bg_color"] = CRITICAL_GREY
            self._card_formats[key] = self._format(spec)
        return self._card_formats[key]

    def _network_plans(self) -> list[CellNetwork]:
        """Cell plans of the main network followed by one per crashing step, all sharing one column structure, so
        the progressive layout can stack them in the same columns without breaking any arrow."""
        if not hasattr(self, "_plans"):
            schedules = [self.s.cpm]
            if self.has(SheetKind.STEP_NETWORK) and self.s.crashing is not None:
                schedules += [st.schedule for st in self.s.crashing.states]
            self._plans = plan_many(schedules)
        return self._plans

    def draw_cell_network(self, kind: SheetKind, net: CellNetwork, schedule: CpmResult, r0: int, c0: int,
                          linked: bool) -> None:
        """Draw ``net`` with its top-left corner at (r0, c0) (1-based). Node cells are formulas linked to the CPM
        sheet when ``linked`` (the main network), plain values otherwise (one network per crashing step)."""
        ws = self.ws[kind]
        rows = schedule.by_id()
        cpm_name = self.name(SheetKind.CPM) if linked else None
        cpm_rows = self.cpm_rows.get(SheetKind.CPM, {}) if linked else {}
        for r in range(net.height):
            ws.set_row(r0 - 1 + r, LINE_ROW_HEIGHT)
        for (r, c), line in sorted(net.lines.items()):
            if line.critical:
                # neutral version (single black glyphs), recorded only: the progressive layout shows it until the
                # critical path is known; the classic workbook never contains it
                neutral = LineCell(dirs=set(line.dirs), arrow=line.arrow)
                text, align = neutral.text()
                with self.tagged(kind, "line"):
                    ws.shadow_string(r0 - 1 + r, c0 - 1 + c, text, self._line_format(align, False))
            text, align = line.text()
            with self.tagged(kind, "line_crit" if line.critical else "line"):
                ws.write_string(r0 - 1 + r, c0 - 1 + c, text, self._line_format(align, line.critical))
        for place in net.nodes.values():
            row = rows[place.activity_id]
            r, c = r0 + place.row, c0 + place.col
            static_grey = place.critical and not linked
            cells = [(0, 0, row.activity_id, 0, "id"), (0, 1, row.duration, 4, "t"), (1, 0, row.early_start, 5, "es"),
                     (1, 1, row.early_finish, 6, "ef"), (2, 0, row.late_start, 7, "ls"), (2, 1, row.late_finish, 8, "lf")]
            for dr, dc, value, src_col, tag in cells:
                self.ws[kind].tags.append(tag)
                if isinstance(value, str):
                    self.put(kind, r + dr, c + dc, value, self._card_format(value, static_grey, text=True))
                elif linked:
                    self.formula(kind, r + dr, c + dc, "=" + _xref(cpm_name, cpm_rows[place.activity_id], src_col),
                                 value, self._card_format(value, False))
                else:
                    self.put(kind, r + dr, c + dc, value, self._card_format(value, static_grey))
                self.ws[kind].tags.pop()
            self.ws[kind].tags.append("slack")
            slack_fmt = self._card_format(row.total_slack, static_grey)
            ws.merge_range(r + 2, c - 1, r + 2, c, "", slack_fmt)
            if linked:
                self.formula(kind, r + 3, c, "=" + _xref(cpm_name, cpm_rows[place.activity_id], 9), row.total_slack,
                             slack_fmt)
                self.grey_rule(kind, (r, c), (r + NODE_ROWS - 1, c + NODE_COLS - 1),
                               f"ABS({_cell(r + 3, c, True)})<{TOL}")
            else:
                self.put(kind, r + 3, c, row.total_slack, slack_fmt)
            self.ws[kind].tags.pop()
        node_starts = {c0 + x for x in net.node_columns}
        for c in range(net.width):
            col = c0 + c
            in_node = any(s <= col < s + NODE_COLS for s in node_starts)
            ws.set_column(col - 1, col - 1, NODE_COL_WIDTH if in_node else CHANNEL_COL_WIDTH)

    def _network_legend(self, k: SheetKind, row: int) -> None:
        self.put(k, row, 2, t(self.lang, "network.lines"), self.f_heading)
        with self.tagged(k, "legend_crit"):
            self.ws[k].write_string(row, 1, "═════►", self._line_format("left", True))
            self.put(k, row + 1, 5, t(self.lang, "network.critical_line"))
        self.ws[k].write_string(row + 1, 1, "─────►", self._line_format("left", False))
        self.put(k, row + 2, 5, t(self.lang, "network.normal_line"))

    def write_network(self) -> None:
        k = SheetKind.NETWORK
        ws = self.ws[k]
        x = self.x
        schedule = self.s.cpm
        cpm_name, cpm_rows = self.name(SheetKind.CPM), self.cpm_rows[SheetKind.CPM]
        self.put(k, 1, 1, t(self.lang, "title.network"), self.f_title)
        self.put(k, 3, 2, t(self.lang, "network.legend"), self.f_heading)
        legend = [((x["card_act"], x["card_t"]), ("network.legend.name", "network.legend.duration")),
                  ((x["card_es"], x["card_ef"]), ("network.legend.es", "network.legend.ef")),
                  ((x["card_ls"], x["card_lf"]), ("network.legend.ls", "network.legend.lf"))]
        for i, ((left, right), (kl, kr)) in enumerate(legend):
            with self.tagged(k, ("id", "es", "ls")[i]):
                self.put(k, 4 + i, 2, left, self.f_card_name)
                self.put(k, 4 + i, 3, right, self.f_card_name)
                self.put(k, 4 + i, 5, f"{t(self.lang, kl)}  |  {t(self.lang, kr)}")
        with self.tagged(k, "slack"):
            ws.merge_range(6, 1, 6, 2, t(self.lang, "slack"), self.f_card_name)
            self.put(k, 7, 5, t(self.lang, "network.legend.slack"))
        self._network_legend(k, 8)
        net = self._network_plans()[0]
        r0 = 13
        self.put(k, r0 - 1, 2, t(self.lang, "network.cards"), self.f_heading)
        self.draw_cell_network(k, net, schedule, r0 + 1, 2, linked=True)
        arc_col = 2 + net.width + 2
        self.put(k, r0 - 1, arc_col, t(self.lang, "network.arcs"), self.f_heading)
        self.header(k, r0, arc_col, [t(self.lang, "network.from"), t(self.lang, "network.to")])
        with self.tagged(k, "arc_crit"):
            self.header(k, r0, arc_col + 2, [t(self.lang, "network.critical_arc")])
        rows = schedule.by_id()
        for i, e in enumerate(self.s.network.edges):
            a, b = rows[e.from_id], rows[e.to_id]
            critical = a.is_critical and b.is_critical and a.early_finish == b.early_start
            self.put(k, r0 + 1 + i, arc_col, e.from_id)
            self.put(k, r0 + 1 + i, arc_col + 1, e.to_id)
            with self.tagged(k, "arc_crit"):
                self.put(k, r0 + 1 + i, arc_col + 2, t(self.lang, "yes" if critical else "no"))
        ws.set_column(arc_col - 1, arc_col + 1, 12.7)
        if self.s.cost_curve is not None and self.has(SheetKind.COST_CURVE):
            note_col = arc_col + 4
            with self.tagged(k, "cost_note"):
                self.put(k, 3, note_col, t(self.lang, "network.cost_note", unit=self.singular, sheet=self.name(SheetKind.COST_CURVE)))

    # ------------------------------------------------------------------ Compresión

    def write_crashing(self) -> None:
        k = SheetKind.CRASHING
        ws = self.ws[k]
        c = self.s.crashing
        real = [a for a in self.p.activities if not a.is_dummy]
        tags = self.ws[k].tags
        tags.append("slopes")
        self.put(k, 1, 1, t(self.lang, "title.crashing"), self.f_title)
        self.put(k, 2, 1, t(self.lang, "crash.slopes"), self.f_heading)
        self.header(k, 4, 1, [t(self.lang, "activity"), t(self.lang, "predecessors"), f"{t(self.lang, 'duration')} (Dn)",
                              f"{t(self.lang, 'normal_cost')} (Cn)", f"{t(self.lang, 'crash_duration')} (Dc)",
                              f"{t(self.lang, 'crash_cost')} (Cc)", t(self.lang, "crash.slope"),
                              t(self.lang, "crash.max_reduction")])
        slopes = {s.activity_id: s for s in c.slopes}
        slope_row: dict[str, int] = {}
        no_slope = t(self.lang, "crash.no_slope")
        for i, a in enumerate(real):
            r = 5 + i
            slope_row[a.id] = r
            s = slopes[a.id]
            self.put(k, r, 1, a.id)
            self.put(k, r, 2, ", ".join(a.predecessors) or t(self.lang, "none"))
            for col, key, value in ((3, "duration", s.normal_duration), (4, "normal_cost", s.normal_cost),
                                    (5, "crash_duration", s.crash_duration), (6, "crash_cost", s.crash_cost)):
                ref = self.data_ref(a.id, key)
                if ref:
                    self.formula(k, r, col, "=" + ref, value)
                else:
                    self.put(k, r, col, value)
            self.formula(k, r, 7, f'=IF(C{r}=E{r},"{no_slope}",(F{r}-D{r})/(C{r}-E{r}))',
                         s.slope if s.slope is not None else no_slope)
            self.formula(k, r, 8, f"=C{r}-E{r}", s.max_reduction)
        last = 4 + len(real)
        sum_row = last + 1
        self.put(k, sum_row, 1, t(self.lang, "crash.normal_cost_total"), self.f_heading)
        self.formula(k, sum_row, 4, f"=SUM(D5:D{last})", c.normal_direct_cost)

        tags[-1] = "steps_head"
        r = sum_row + 2
        self.put(k, r, 1, t(self.lang, "crash.steps"), self.f_heading)
        r += 1
        self.header(k, r, 1, [t(self.lang, "crash.step"), f"{t(self.lang, 'duration')} ({self.plural})",
                              t(self.lang, "crash.direct_cost"), t(self.lang, "critical_paths"),
                              t(self.lang, "crash.to_reduce"), t(self.lang, "crash.reduction"),
                              t(self.lang, "crash.increase"), *[a.id for a in real]])
        state_row: dict[int, int] = {}
        for st in c.states:
            r += 1
            state_row[st.step] = r
            tags[-1] = f"state:{st.step}"  # columns E–G (what is reduced next) belong to the next step
            self.put(k, r, 1, st.step)
            self.put(k, r, 2, st.project_duration)
            if st.step == 0:
                self.formula(k, r, 3, f"=D{sum_row}", st.direct_cost)
            else:
                self.formula(k, r, 3, f"=C{r - 1}+G{r - 1}", st.direct_cost)
            self.put(k, r, 4, "; ".join("-".join(p) for p in st.critical_paths))
            self.put(k, r, 5, ", ".join(st.next_crash_activities) or "—")
            if st.next_crash_activities:
                self.put(k, r, 6, st.next_reduction)
                slopes_sum = "+".join(f"G{slope_row[a]}" for a in st.next_crash_activities)
                self.formula(k, r, 7, f"=({slopes_sum})*F{r}", st.next_cost_increase)
            for j, a in enumerate(real):
                self.put(k, r, 8 + j, st.durations[a.id])
        r += 1
        tags[-1] = "stop"
        self.put(k, r, 1, f"{t(self.lang, 'crash.stop_reason')}: {c.stop_reason}")
        r += 2
        tags[-1] = "summary"
        self.marks["crash_summary_first"] = r
        self.put(k, r, 1, t(self.lang, "crash.summary"), self.f_heading)
        r += 1
        self.header(k, r, 1, [f"{t(self.lang, 'duration')} ({self.plural})", t(self.lang, "crash.direct_cost"),
                              t(self.lang, "crash.indirect_cost"), t(self.lang, "crash.total_cost")])
        first_by_duration: dict[Fraction, int] = {}
        for st in c.states:
            first_by_duration.setdefault(st.project_duration, state_row[st.step])
        summary_row: dict[Fraction, int] = {}
        first_summary = r + 1
        for point in c.cost_table:
            r += 1
            summary_row[point.project_duration] = r
            self.put(k, r, 1, point.project_duration)
            self.formula(k, r, 2, f"=C{first_by_duration[point.project_duration]}", point.direct_cost)
            if point.indirect_cost is not None:
                self.put(k, r, 3, point.indirect_cost)
                self.formula(k, r, 4, f"=B{r}+C{r}", point.total_cost)
        last_summary = r
        min_cell = None
        if c.optimal is not None:
            r += 2
            tags[-1] = "optimal"
            self.marks["crash_optimal_first"] = r
            self.put(k, r, 1, t(self.lang, "crash.optimal"), self.f_heading)
            self.put(k, r, 4, c.optimal.project_duration)
            r += 1
            self.put(k, r, 1, t(self.lang, "crash.min_total_check"), self.f_heading)
            self.formula(k, r, 4, f"=MIN(D{first_summary}:D{last_summary})", c.optimal.total_cost)
            min_cell = (r, 4)
        tags.pop()
        self.crash = {"slope_row": slope_row, "state_row": state_row, "summary_row": summary_row,
                      "min_cell": min_cell, "activity_col": {a.id: 8 + j for j, a in enumerate(real)}}
        ws.set_column(0, 0, 14.7)
        ws.set_column(1, 2, 13)
        ws.set_column(3, 3, 26.7)
        ws.set_column(4, 4, 14.7)
        ws.set_column(5, 6, 12)
        ws.set_column(7, 7 + len(real), 7.7)

    # ------------------------------------------------------------------ Red AON por paso

    def write_step_network(self) -> None:
        k = SheetKind.STEP_NETWORK
        ws = self.ws[k]
        c = self.s.crashing
        crash_name = self.name(SheetKind.CRASHING)
        info = self.crash
        self.put(k, 1, 1, t(self.lang, "title.step_network"), self.f_title)
        self.put(k, 2, 1, t(self.lang, "step.note", sheet=crash_name))
        self.put(k, 3, 1, t(self.lang, "step.legend"))
        slopes = {s.activity_id: s.slope for s in c.slopes}
        self._network_legend(k, 4)
        r = 8
        previous = None
        plans = self._network_plans()[1:]
        tags = self.ws[k].tags
        self.marks["step_block_first"] = {}
        self.marks["step_cost_rows"] = {}
        for st, net in zip(c.states, plans):
            tags.append(f"state:{st.step}")
            self.marks["step_block_first"][st.step] = r
            self.put(k, r, 1, t(self.lang, "step.label", step=st.step), self.f_heading)
            caption = t(self.lang, "step.caption", step=st.step, duration=number_text(st.project_duration), plural=self.plural)
            self.put(k, r, 3, caption, self.f_heading)
            self.draw_cell_network(k, net, st.schedule, r + 2, 2, linked=False)
            image_rows = net.height + 2
            pc = 2 + net.width + 2
            pr = r + 1
            srow = info["state_row"][st.step]
            self.put(k, pr, pc, t(self.lang, "step.duration", plural=self.plural), self.f_heading)
            self.formula(k, pr, pc + 1, "=" + _xref(crash_name, srow, 2), st.project_duration)
            pr += 1
            self.put(k, pr, pc, t(self.lang, "step.reduced"), self.f_heading)
            self.put(k, pr, pc + 1, ", ".join(previous.next_crash_activities) if previous else "—")
            pr += 1
            self.put(k, pr, pc, t(self.lang, "step.increase"), self.f_heading)
            if previous is not None:
                self.formula(k, pr, pc + 1, "=" + _xref(crash_name, info["state_row"][previous.step], 7),
                             previous.next_cost_increase)
            else:
                self.put(k, pr, pc + 1, 0)
            pr += 1
            self.put(k, pr, pc, t(self.lang, "crash.direct_cost"), self.f_heading)
            self.formula(k, pr, pc + 1, "=" + _xref(crash_name, srow, 3), st.direct_cost)
            point = next((p for p in c.cost_table if p.project_duration == st.project_duration), None)
            if point is not None and point.indirect_cost is not None:
                srow2 = info["summary_row"][st.project_duration]
                tags.append("costpanel")  # indirect / total rows stay hidden until the total-cost step
                self.marks["step_cost_rows"][st.step] = (pr + 1, pr + 2)
                pr += 1
                self.put(k, pr, pc, t(self.lang, "crash.indirect_cost"), self.f_heading)
                self.formula(k, pr, pc + 1, "=" + _xref(crash_name, srow2, 3), point.indirect_cost)
                pr += 1
                self.put(k, pr, pc, t(self.lang, "crash.total_cost"), self.f_heading)
                self.formula(k, pr, pc + 1, "=" + _xref(crash_name, srow2, 4), point.total_cost)
                tags.pop()
            pr += 1
            self.put(k, pr, pc, t(self.lang, "step.routes"), self.f_heading)
            for path in enumerate_paths(self.s.network):
                pr += 1
                length = sum((st.durations[a] for a in path), Fraction(0))
                visible = [a for a in path if a in slopes]
                self.put(k, pr, pc, f"{'-'.join(visible or path)} = {number_text(length)}")
            pr += 1
            if previous is not None:
                changes = self.x["came_from"].format(acts=", ".join(previous.next_crash_activities),
                                                     d=number_text(previous.next_reduction), unit=self.plural,
                                                     inc=number_text(previous.next_cost_increase))
                self.put(k, pr, pc, t(self.lang, "step.came_from", changes=changes))
            else:
                self.put(k, pr, pc, self.x["start"])
            if st.next_crash_activities:
                pr += 1
                slope = sum((slopes[a] for a in st.next_crash_activities), Fraction(0))
                with self.tagged(k, f"next:{st.step}"):
                    self.put(k, pr, pc, t(self.lang, "step.next", activities=", ".join(st.next_crash_activities),
                                          slope=number_text(slope), unit=self.singular))
            ws.set_column(pc - 2, pc - 2, CHANNEL_COL_WIDTH)
            ws.set_column(pc - 1, pc - 1, 38)
            ws.set_column(pc, pc, 14)
            r = max(r + 1 + image_rows, pr) + 2
            previous = st
            tags.pop()

    # ------------------------------------------------------------------ Curva de costos

    def write_cost_curve(self) -> None:
        k = SheetKind.COST_CURVE
        ws = self.ws[k]
        curve = self.s.cost_curve
        unit = self.singular
        self.put(k, 1, 1, t(self.lang, "title.cost_curve", unit=unit), self.f_title)
        if curve.basis == "crashed":
            basis = t(self.lang, "cost.basis_crashed", duration=number_text(curve.basis_duration),
                      plural=self.unit.label(curve.basis_duration, self.lang), step=curve.crash_state_step,
                      sheet=self.name(SheetKind.CRASHING) if self.has(SheetKind.CRASHING) else "")
        else:
            basis = t(self.lang, "cost.basis_normal", duration=number_text(curve.basis_duration),
                      plural=self.unit.label(curve.basis_duration, self.lang))
        self.put(k, 2, 1, basis)
        self.put(k, 3, 1, t(self.lang, "cost.note", unit=unit))
        self.put(k, 4, 1, t(self.lang, "cost.per_activity", unit=unit), self.f_heading)
        self.header(k, 5, 1, [t(self.lang, "activity"), t(self.lang, "duration"), t(self.lang, "cost.activity_cost"),
                              t(self.lang, "cost.cost_per_unit", unit=unit), t(self.lang, "es"), t(self.lang, "ef")])
        rows: dict[str, int] = {}
        crashed = curve.basis == "crashed" and self.has(SheetKind.CRASHING) and self.crash
        crash_name = self.name(SheetKind.CRASHING) if self.has(SheetKind.CRASHING) else None
        for i, a in enumerate(curve.activities):
            r = 6 + i
            rows[a.activity_id] = r
            self.put(k, r, 1, a.activity_id)
            if crashed:
                srow = self.crash["state_row"][curve.crash_state_step]
                self.formula(k, r, 2, "=" + _xref(crash_name, srow, self.crash["activity_col"][a.activity_id]), a.duration)
                slope_r = self.crash["slope_row"][a.activity_id]
                base = _xref(crash_name, slope_r, 4)
                if a.reduction > 0 and a.slope is not None:
                    text = f"={base}+{_xref(crash_name, slope_r, 7)}*({_xref(crash_name, slope_r, 3)}-B{r})"
                else:
                    text = "=" + base
                self.formula(k, r, 3, text, a.total_cost)
            else:
                dref, cref = self.data_ref(a.activity_id, "duration"), self.data_ref(a.activity_id, "normal_cost")
                self.formula(k, r, 2, "=" + dref, a.duration)
                self.formula(k, r, 3, "=" + cref, a.total_cost)
            self.formula(k, r, 4, f"=IF(B{r}>0,C{r}/B{r},C{r})", a.cost_per_period)
            self.put(k, r, 5, a.early_start)
            self.put(k, r, 6, a.early_finish)
        last = 5 + len(curve.activities)
        total_row = last + 1
        self.put(k, total_row, 1, t(self.lang, "cost.project_total"), self.f_heading)
        self.formula(k, total_row, 3, f"=SUM(C6:C{last})", curve.total_cost)

        r = total_row + 2
        self.put(k, r, 1, t(self.lang, "cost.accumulation", unit=unit), self.f_heading)
        r += 1
        self.header(k, r, 1, [t(self.lang, "cost.period_number", unit=unit[:1].upper() + unit[1:]),
                              t(self.lang, "cost.in_progress"), t(self.lang, "cost.period_cost", unit=unit),
                              t(self.lang, "cost.cumulative"), t(self.lang, "cost.percent")])
        first_period = r + 1
        by_id = {a.activity_id: a for a in curve.activities}
        for period in curve.periods:
            r += 1
            self.put(k, r, 1, period.period)
            self.put(k, r, 2, ", ".join(period.activity_ids) or t(self.lang, "cost.nothing_in_progress"))
            terms = []
            for aid in period.activity_ids:
                ar = rows[aid]
                if by_id[aid].duration > 0:
                    terms.append(f"D{ar}*MAX(0,MIN(F{ar},A{r})-MAX(E{ar},A{r}-1))")
                else:
                    terms.append(f"C{ar}")
            self.formula(k, r, 3, "=" + ("+".join(terms) if terms else "0"), period.period_cost)
            self.formula(k, r, 4, f"=C{r}" if r == first_period else f"=D{r - 1}+C{r}", period.cumulative_cost)
            self.formula(k, r, 5, f"=IF($C${total_row}=0,0,D{r}/$C${total_row})", period.fraction_of_total, self.f_pct)
        last_period = r
        r += 1
        self.put(k, r, 1, t(self.lang, "cost.check"), self.f_heading)
        self.formula(k, r, 4, f"=ROUND(D{last_period}-C{total_row},9)", 0)
        self.curve = {"first": first_period, "last": last_period}
        ws.set_column(0, 0, 14)
        ws.set_column(1, 1, 22)
        ws.set_column(2, 5, 16)

    # ------------------------------------------------------------------ Enunciado

    def write_problem(self) -> None:
        k = SheetKind.PROBLEM
        ws = self.ws[k]
        p, lang = self.p, self.lang
        labels = OUTPUT_LABELS[lang]
        self.put(k, 1, 1, p.metadata.title, self.f_title)
        self.put(k, 2, 1, t(lang, "title.problem"))
        r = 4
        if p.metadata.source_description:
            self.put(k, r, 1, t(lang, "problem.source"), self.f_heading)
            self.put(k, r, 2, p.metadata.source_description)
            r += 1
        self.put(k, r, 1, t(lang, "time_unit"), self.f_heading)
        self.put(k, r, 2, self.plural)
        self.put(k, r + 1, 1, t(lang, "method"), self.f_heading)
        self.put(k, r + 1, 2, t(lang, f"problem.method.{p.calculation_method.value}"))
        self.put(k, r + 2, 1, self.x["options"], self.f_heading)
        self.put(k, r + 2, 2, f"probability_method={p.options.probability_method.value}; "
                              f"variance_strategy={p.options.variance_strategy.value}")
        r += 4
        if p.questions:
            self.put(k, r, 1, t(lang, "problem.questions"), self.f_heading)
            self.header(k, r + 1, 1, [t(lang, "results.label"), t(lang, "results.question"), t(lang, "problem.requested")])
            r += 2
            for q in p.questions:
                self.put(k, r, 1, q.label)
                self.put(k, r, 2, q.text, self.f_wrap)
                self.put(k, r, 3, ", ".join(labels[o] for o in q.outputs))
                r += 1
            r += 1
        self.put(k, r, 1, t(lang, "problem.requested"), self.f_heading)
        self.put(k, r, 2, ", ".join(labels[o] for o in self.s.effective_outputs), self.f_wrap)
        r += 2
        if p.probability_queries:
            self.put(k, r, 1, t(lang, "problem.queries"), self.f_heading)
            self.header(k, r + 1, 1, [self.x["id"], t(lang, "prob.kind"), "≥", "≤", self.x["p"], t(lang, "provenance"),
                                      t(lang, "problem.source")])
            r += 2
            for q in p.probability_queries:
                self.put(k, r, 1, q.id or "")
                self.put(k, r, 2, q.kind.value)
                self.put(k, r, 3, number_text(q.lower_bound) if q.lower_bound is not None else "")
                self.put(k, r, 4, number_text(q.upper_bound) if q.upper_bound is not None else "")
                self.put(k, r, 5, f"{q.target_probability:g}" if q.target_probability is not None else "")
                self.put(k, r, 6, self.prov_label(q.provenance))
                self.put(k, r, 7, q.source_text or "")
                r += 1
            r += 1
        if p.arrow_network_trace:
            self.put(k, r, 1, t(lang, "network.aoa"), self.f_heading)
            self.header(k, r + 1, 1, [t(lang, "activity"), t(lang, "network.tail"), t(lang, "network.head"),
                                      t(lang, "predecessors"), t(lang, "network.via"), t(lang, "problem.justification")])
            r += 2
            for tr in p.arrow_network_trace:
                self.put(k, r, 1, tr.activity_id)
                self.put(k, r, 2, tr.tail_node)
                self.put(k, r, 3, tr.head_node)
                self.put(k, r, 4, ", ".join(tr.predecessors) or t(lang, "none"))
                self.put(k, r, 5, ", ".join(tr.via_dummies))
                self.put(k, r, 6, tr.justification)
                r += 1
            r += 1
        self.put(k, r + 1, 1, self.x["data_note"].format(sheet=self.name(SheetKind.DATA)))
        ws.set_column(0, 0, 22.7)
        ws.set_column(1, 1, 60.7)
        ws.set_column(2, 2, 12.7)
        ws.set_column(5, 5, 40.7)

    def _assumptions(self, k: SheetKind, r: int) -> int:
        if not self.p.assumptions:
            return r
        self.put(k, r, 1, t(self.lang, "results.assumptions"), self.f_heading)
        self.header(k, r + 1, 1, [t(self.lang, "problem.field"), t(self.lang, "problem.value"),
                                  t(self.lang, "problem.justification"), t(self.lang, "provenance")])
        r += 2
        for a in self.p.assumptions:
            self.put(k, r, 1, a.field_path)
            self.put(k, r, 2, a.value)
            self.put(k, r, 3, a.justification)
            self.put(k, r, 4, self.prov_label(a.provenance))
            r += 1
        return r + 1

    # ------------------------------------------------------------------ Resultados

    def key_link(self, answer: QuestionAnswer) -> tuple[str, Any] | None:
        cpm_name = self.name(SheetKind.CPM) if self.has(SheetKind.CPM) else None
        queries = self.p.probability_queries
        question = next((q for q in self.p.questions if q.label == answer.label), None)
        for output in (O.CRITICAL_PATH, O.EXPECTED_DURATION, O.VARIANCE, O.PROBABILITY_QUERY, O.PERCENTILE_DURATION,
                       O.CRASHING, O.ACTIVITY_MAX_DELAY):
            if output not in answer.outputs:
                continue
            if output == O.CRITICAL_PATH and cpm_name:
                return "=" + _xref(cpm_name, 3, 4), "; ".join(" – ".join(p) for p in self.s.cpm.critical_paths)
            if output == O.EXPECTED_DURATION and cpm_name:
                return "=" + _xref(cpm_name, 2, 4), self.s.cpm.project_duration
            if output == O.VARIANCE and self.pert_var_cell and self.has(SheetKind.PERT):
                return "=" + _xref(self.name(SheetKind.PERT), *self.pert_var_cell), self.s.variance.total_variance
            if output in (O.PROBABILITY_QUERY, O.PERCENTILE_DURATION) and self.has(SheetKind.PROBABILITY):
                wanted = O.PROBABILITY_QUERY == output
                ids = set(question.probability_query_ids) if question else set()
                for i, q in enumerate(queries):
                    is_pct = q.kind == ProbabilityQueryKind.PERCENTILE_TO_DURATION
                    if (ids and q.id not in ids) or is_pct == wanted or i not in self.prob_cells:
                        continue
                    row, col = self.prob_cells[i]
                    return "=" + _xref(self.name(SheetKind.PROBABILITY), row, col), self.expected[
                        (self.name(SheetKind.PROBABILITY), row, col)]
            if output == O.CRASHING and self.has(SheetKind.CRASHING):
                c = self.s.crashing
                if self.crash.get("min_cell"):
                    return "=" + _xref(self.name(SheetKind.CRASHING), *self.crash["min_cell"]), c.optimal.total_cost
                last = c.states[-1]
                return "=" + _xref(self.name(SheetKind.CRASHING), self.crash["state_row"][last.step], 3), last.direct_cost
            if output == O.ACTIVITY_MAX_DELAY and cpm_name and self.s.delay_answers:
                ids = set(question.delay_activity_ids) if question else set()
                d = next((d for d in self.s.delay_answers if not ids or d.activity_id in ids), None)
                if d is not None:
                    return "=" + _xref(cpm_name, self.cpm_rows[SheetKind.CPM][d.activity_id], 9), d.total_slack
        return None

    @staticmethod
    def _text_height(text: str, width: float) -> float:
        """Approximate row height (points) for wrapped ``text`` in a column of ``width`` characters."""
        chars = max(10, int(width * 1.05))
        lines = sum(max(1, math.ceil(len(line) / chars)) for line in (text or "").split("\n"))
        return lines * 15.0 + 6

    def _result_block(self, k: SheetKind, r: int, answer: QuestionAnswer) -> int:
        """One question: label, question, answer and key value span the block; each procedure step gets its own
        row (numbered title + one operation per line) so long procedures stay readable and never hit Excel's row cap."""
        ws = self.ws[k]
        steps = answer.procedure_steps or []
        n = max(1, len(steps))
        heights = []
        for i in range(n):
            if steps:
                st = steps[i]
                body = "\n".join("   " + line for line in st.lines)
                title = f"{st.number}. {st.title}"
                ws.write_string(r - 1 + i, 3, title + ("\n" + body if st.lines else ""), self.f_cell)
                heights.append(self._text_height(title + "\n" + body, RESULT_WIDTHS[3]))
            else:
                ws.write_blank(r - 1 + i, 3, None, self.f_cell)
                heights.append(20.0)
        need = max(self._text_height(answer.answer, RESULT_WIDTHS[2]),
                   self._text_height(answer.question, RESULT_WIDTHS[1]))
        if sum(heights) < need:
            heights[-1] += need - sum(heights)
        for i, h in enumerate(heights):
            ws.set_row(r - 1 + i, min(409.0, h))
        link = self.key_link(answer)
        cells = [(0, answer.label, self.f_label), (1, answer.question, self.f_cell), (2, answer.answer, self.f_answer)]
        for col, value, fmt in cells:
            if n > 1:
                ws.merge_range(r - 1, col, r - 2 + n, col, value, fmt)
            else:
                ws.write_string(r - 1, col, value, fmt)
        if n > 1:
            ws.merge_range(r - 1, 4, r - 2 + n, 4, "", self.f_key)
        if link is not None:
            self.formula(k, r, 5, link[0], link[1], self.f_key if not isinstance(link[1], (int, float, Fraction))
                         else self.f_key_int if float(link[1]).is_integer() else self.f_key_dec)
        elif n == 1:
            ws.write_blank(r - 1, 4, None, self.f_key)
        return r + n

    def write_results(self) -> None:
        k = SheetKind.RESULTS
        ws = self.ws[k]
        lang = self.lang
        self.put(k, 1, 1, t(lang, "title.results"), self.f_title)
        self.put(k, 2, 1, self.p.metadata.title)
        self.header(k, 4, 1, [t(lang, "results.label"), t(lang, "results.question"), t(lang, "results.answer"),
                              t(lang, "results.procedure"), t(lang, "results.key_value")])
        r = 5
        for answer in self.s.answers:
            r = self._result_block(k, r, answer)
        # Provenance, assumptions, validation checks and warnings stay in the tool result (solve_project /
        # generate_solution_workbook) only: the workbook must read like a student's own solution.
        for col, width in enumerate(RESULT_WIDTHS):
            ws.set_column(col, col, width)
        ws.freeze_panes(4, 0)

    # ------------------------------------------------------------------ driver

    def write(self) -> WorkbookBuild:
        s = self.s
        if self.has(SheetKind.PERT):
            self.pert_row = {e.activity_id: 6 + i for i, e in enumerate(s.pert.estimates)}
        if self.has(SheetKind.CPM):
            self.cpm_layout(SheetKind.CPM, s.cpm)
        if self.has(SheetKind.CPM_DETERMINISTIC):
            self.cpm_layout(SheetKind.CPM_DETERMINISTIC, s.cpm_deterministic)
        self.write_data()
        if self.has(SheetKind.CPM):
            self.write_cpm(SheetKind.CPM, s.cpm, pert_based=self.p.uses_estimates)
        if self.has(SheetKind.CPM_DETERMINISTIC):
            self.write_cpm(SheetKind.CPM_DETERMINISTIC, s.cpm_deterministic, pert_based=False)
        if self.has(SheetKind.PERT):
            self.write_pert()
        if self.has(SheetKind.PROBABILITY):
            self.write_probability()
        if self.has(SheetKind.GANTT):
            self.write_gantt()
        if self.has(SheetKind.CRASHING):
            self.write_crashing()
        if self.has(SheetKind.COST_CURVE):
            self.write_cost_curve()
        if self.has(SheetKind.NETWORK):
            self.write_network()
        if self.has(SheetKind.STEP_NETWORK):
            self.write_step_network()
        self.write_problem()
        self.write_results()
        self.wb.close()
        return WorkbookBuild(path=str(self.path), sheet_names=[self.names[k] for k in self.plan],
                             expected_values=self.expected, notes=self.notes)


def write_solution_workbook(solution: ProjectSolution, path: str | Path) -> WorkbookBuild:
    """Write the solution workbook to ``path`` (overwriting it) and return what was promised in it."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return _Writer(solution, path).write()
