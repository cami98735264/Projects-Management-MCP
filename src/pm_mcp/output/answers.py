"""Per-question answers and their procedure, written to be read by a student.

Layout of every answer:

* **Respuesta** — short bullet lines with the direct result first (the number that answers the question), then
  the supporting facts. No per-activity dumps run together in one sentence.
* **Procedimiento** — numbered steps. Each step has a title that states the formula in words and symbols, and
  one line per activity / case with the numbers substituted.

Every number quoted here is read from the engine results in the :class:`ProjectSolution`; nothing is recomputed
or rounded differently from the result models (exact values are shown as fractions, irrational ones as
4-decimal decimals).
"""

from __future__ import annotations

from collections.abc import Callable
from fractions import Fraction

from pm_mcp.domain.models import RequestedOutput as O
from pm_mcp.domain.numbers import decimal_text, number_text
from pm_mcp.engine.probability import PercentileResult, ProbabilityResult
from pm_mcp.i18n import t
from pm_mcp.solution import ProcedureStep, ProjectSolution, QuestionAnswer

OUTPUT_LABELS: dict[str, dict[O, str]] = {
    "es": {
        O.GANTT: "Diagrama Gantt",
        O.NETWORK_DIAGRAM: "Red actividad-nodo",
        O.EARLY_LATE_TIMES: "Tiempos de inicio y terminación",
        O.SLACK: "Holguras",
        O.CRITICAL_PATH: "Ruta crítica",
        O.EXPECTED_DURATION: "Duración del proyecto",
        O.VARIANCE: "Varianza del proyecto",
        O.ACTIVITY_ESTIMATES: "Tiempo esperado y varianza por actividad",
        O.PROBABILITY_QUERY: "Probabilidades de terminación",
        O.PERCENTILE_DURATION: "Duración para una probabilidad objetivo",
        O.ACTIVITY_MAX_DELAY: "Retraso máximo de actividades",
        O.CRASHING: "Compresión del proyecto",
        O.FULL_REPORT: "Informe completo",
    },
    "en": {
        O.GANTT: "Gantt chart",
        O.NETWORK_DIAGRAM: "Activity-on-node network",
        O.EARLY_LATE_TIMES: "Start and finish times",
        O.SLACK: "Slacks",
        O.CRITICAL_PATH: "Critical path",
        O.EXPECTED_DURATION: "Project duration",
        O.VARIANCE: "Project variance",
        O.ACTIVITY_ESTIMATES: "Expected time and variance per activity",
        O.PROBABILITY_QUERY: "Completion probabilities",
        O.PERCENTILE_DURATION: "Duration for a target probability",
        O.ACTIVITY_MAX_DELAY: "Maximum activity delay",
        O.CRASHING: "Project crashing",
        O.FULL_REPORT: "Full report",
    },
}

ARROW = " → "
BULLET = "• "
INDENT = "   "

_W: dict[str, dict[str, str]] = {
    "es": {
        # Gantt
        "gantt": "Diagrama de Gantt en la hoja '{sheet}': {n} actividades en un horizonte de {h} {unit}.",
        "gantt_bars": "Cada actividad tiene dos barras: la superior va de IC a TC (lo más temprano posible) y la "
                      "inferior de IL a TL (lo más tarde posible).",
        "gantt_crit": "Actividades críticas (sin holgura, sombreadas): {acts}.",
        "gantt_h": "Horizonte del diagrama",
        "gantt_h_line": "⌈T⌉ = ⌈{T}⌉ = {h} columnas (la columna k cubre el intervalo [k − 1, k]).",
        "gantt_bars_h": "Barras por actividad (temprana IC → TC  /  tardía IL → TL)",
        # times
        "times_head": "Tiempos por actividad (hoja '{sheet}'):",
        "times_row": "{id}: IC = {es}, TC = {ef}, IL = {ls}, TL = {lf}, holgura = {slack}",
        "critical_tag": " (crítica)",
        "times_legend": "IC / TC = inicio / terminación más temprana; IL / TL = inicio / terminación más tardía.",
        "fw_h": "Paso hacia adelante: IC = máx(TC de las predecesoras), 0 si no tiene; TC = IC + t",
        "fw_row": "{id} (t = {t}): IC = {fw}; TC = {es} + {t} = {ef}",
        "bw_h": "Paso hacia atrás: TL = mín(IL de las sucesoras), T si es final; IL = TL − t",
        "bw_row": "{id} (t = {t}): TL = {bw}; IL = {lf} − {t} = {ls}",
        "slack_h": "Holgura = IL − IC (0 = actividad crítica)",
        "slack_row": "{id}: {ls} − {es} = {slack}",
        "max": "máx", "min": "mín", "EF": "TC", "LS": "IL",
        # critical path
        "path1": "Ruta crítica: {path}", "pathN": "Rutas críticas: {paths}",
        "crit_h": "Actividades críticas (holgura = 0)",
        "crit_len_h": "Duración de la ruta crítica = suma de sus tiempos",
        # duration
        "dur_pert": "Tiempo esperado del proyecto: {T} {unit}.",
        "dur_cpm": "Duración normal del proyecto: {T} {unit}.",
        "dur_det": "Con las duraciones determinísticas: {T} {unit}.",
        "normal_cost": "Costo directo normal: {C} (suma de los costos normales).",
        "finish_h": "Duración del proyecto = mayor TC de las actividades finales",
        "finish_line": "T = {expr} = {T}",
        "cost_h": "Costo directo normal = suma de los costos normales",
        # PERT
        "te_h": "Tiempo esperado por actividad: t_e = (a + b + 4m) / 6  (a optimista, m más probable, b pesimista; "
                "constantes: 4 = peso de m, 6 = divisor)",
        "var_h": "Varianza por actividad: σ² = [(b − a) / 6]²",
        "variance": "Varianza del proyecto: σ² = {var} ≈ {dec}.",
        "std": "Desviación estándar: {std}.",
        "proj_var_h": "Varianza del proyecto = suma de las varianzas de la ruta crítica ({path})",
        "estimates_head": "Tiempo esperado y varianza por actividad (hoja '{sheet}'):",
        "estimates": "{id}: t_e = {te}, σ² = {var}",
        # probability
        "prob": "{expr} = {p} ({pct} %).",
        "prob_detail": "Z = {z}; {other_label}: {other}.",
        "with_table": "con tabla", "exact": "valor exacto",
        "prob_h": "{expr}: Z = (T_p − t_e) / σ, con t_e = {te} y σ = {sigma}",
        "pct": "Duración a especificar: {w} {unit}.",
        "pct_detail": "Z para {pct} % = {z} → T = {d}, se redondea hacia arriba a {w}.",
        "pct_table": "Con tabla: Z = {zt} → T = {dt}.",
        "pct_degenerate": "σ = 0 → T = t_e = {d}; duración a especificar: {w} {unit}.",
        "pct_h": "Duración para una probabilidad objetivo: T_p = t_e + Z · σ",
        "pct_final": "Duración a especificar: ⌈{d}⌉ = {w} {unit}",
        # delay
        "delay_h": "Retraso máximo sin retrasar el proyecto = holgura total = IL − IC",
        # network
        "network": "Red actividad-nodo en la hoja '{sheet}' (cada nodo: actividad y t, IC | TC, IL | TL, holgura).",
        "network_h": "Arcos de la red (precedencias)",
        "network_n": "{n} nodos.",
        # crashing
        "crash_normal": "Duración normal: {T} {unit}, costo directo {C}.",
        "crash_opt": "Duración recomendada: {T} {unit} → costo total mínimo {C} (directo {D} + indirecto {I}).",
        "crash_min": "Duración mínima alcanzable: {T} {unit}, costo directo {C}.",
        "crash_cuts": "Para lograrlo se reduce: {cuts}.",
        "crash_cut": "{act} {d} {unit}",
        "crash_tie": "Con {T} {unit} el costo total también es {C}; se escoge la menor duración.",
        "slopes_h": "Pendiente de costo = (Cc − Cn) / (Dn − Dc)  (cuánto cuesta reducir una unidad de tiempo)",
        "slope_row": "{id}: {working}",
        "slope_none": "{id}: no se puede reducir",
        "steps_h": "Compresión paso a paso (en cada paso se reduce lo más barato que acorte todas las rutas críticas)",
        "step": "Paso {k}: T = {T}, costo directo {C}",
        "step_next": "{sep}se reduce {acts} en {d} (+{inc})",
        "totals_h": "Costo total = costo directo + costo indirecto",
        "totals_row": "T = {T}: {D} + {I} = {C}",
        "totals_min": "Mínimo: T = {T} → {C}",
        "no_data": "No aplica: los datos no lo permiten.",
    },
    "en": {
        "gantt": "Gantt chart on sheet '{sheet}': {n} activities over a {h} {unit} horizon.",
        "gantt_bars": "Each activity has two bars: the upper one goes from ES to EF (as early as possible) and the "
                      "lower one from LS to LF (as late as possible).",
        "gantt_crit": "Critical activities (no slack, shaded): {acts}.",
        "gantt_h": "Chart horizon",
        "gantt_h_line": "⌈T⌉ = ⌈{T}⌉ = {h} columns (column k covers the interval [k − 1, k]).",
        "gantt_bars_h": "Bars per activity (early ES → EF  /  late LS → LF)",
        "times_head": "Times per activity (sheet '{sheet}'):",
        "times_row": "{id}: ES = {es}, EF = {ef}, LS = {ls}, LF = {lf}, slack = {slack}",
        "critical_tag": " (critical)",
        "times_legend": "ES / EF = earliest start / finish; LS / LF = latest start / finish.",
        "fw_h": "Forward pass: ES = max(EF of predecessors), 0 if none; EF = ES + t",
        "fw_row": "{id} (t = {t}): ES = {fw}; EF = {es} + {t} = {ef}",
        "bw_h": "Backward pass: LF = min(LS of successors), T if it is an end activity; LS = LF − t",
        "bw_row": "{id} (t = {t}): LF = {bw}; LS = {lf} − {t} = {ls}",
        "slack_h": "Slack = LS − ES (0 = critical activity)",
        "slack_row": "{id}: {ls} − {es} = {slack}",
        "max": "max", "min": "min", "EF": "EF", "LS": "LS",
        "path1": "Critical path: {path}", "pathN": "Critical paths: {paths}",
        "crit_h": "Critical activities (slack = 0)",
        "crit_len_h": "Critical path length = sum of its times",
        "dur_pert": "Expected project duration: {T} {unit}.",
        "dur_cpm": "Normal project duration: {T} {unit}.",
        "dur_det": "With the deterministic durations: {T} {unit}.",
        "normal_cost": "Normal direct cost: {C} (sum of the normal costs).",
        "finish_h": "Project duration = largest EF of the end activities",
        "finish_line": "T = {expr} = {T}",
        "cost_h": "Normal direct cost = sum of the normal costs",
        "te_h": "Expected time per activity: t_e = (a + b + 4m) / 6  (a optimistic, m most likely, b pessimistic; "
                "constants: 4 = weight of m, 6 = divisor)",
        "var_h": "Variance per activity: σ² = [(b − a) / 6]²",
        "variance": "Project variance: σ² = {var} ≈ {dec}.",
        "std": "Standard deviation: {std}.",
        "proj_var_h": "Project variance = sum of the critical path variances ({path})",
        "estimates_head": "Expected time and variance per activity (sheet '{sheet}'):",
        "estimates": "{id}: t_e = {te}, σ² = {var}",
        "prob": "{expr} = {p} ({pct} %).",
        "prob_detail": "Z = {z}; {other_label}: {other}.",
        "with_table": "with table", "exact": "exact value",
        "prob_h": "{expr}: Z = (T_p − t_e) / σ, with t_e = {te} and σ = {sigma}",
        "pct": "Duration to specify: {w} {unit}.",
        "pct_detail": "Z for {pct} % = {z} → T = {d}, rounded up to {w}.",
        "pct_table": "With table: Z = {zt} → T = {dt}.",
        "pct_degenerate": "σ = 0 → T = t_e = {d}; duration to specify: {w} {unit}.",
        "pct_h": "Duration for a target probability: T_p = t_e + Z · σ",
        "pct_final": "Duration to specify: ⌈{d}⌉ = {w} {unit}",
        "delay_h": "Maximum delay without delaying the project = total slack = LS − ES",
        "network": "Activity-on-node network on sheet '{sheet}' (each node: activity and t, ES | EF, LS | LF, slack).",
        "network_h": "Network arcs (precedences)",
        "network_n": "{n} nodes.",
        "crash_normal": "Normal duration: {T} {unit}, direct cost {C}.",
        "crash_opt": "Recommended duration: {T} {unit} → minimum total cost {C} (direct {D} + indirect {I}).",
        "crash_min": "Shortest achievable duration: {T} {unit}, direct cost {C}.",
        "crash_cuts": "To get there, shorten: {cuts}.",
        "crash_cut": "{act} by {d} {unit}",
        "crash_tie": "With {T} {unit} the total cost is also {C}; the shorter duration is chosen.",
        "slopes_h": "Cost slope = (Cc − Cn) / (Dn − Dc)  (cost of shortening by one time unit)",
        "slope_row": "{id}: {working}",
        "slope_none": "{id}: cannot be shortened",
        "steps_h": "Step-by-step crashing (each step shortens the cheapest set that cuts every critical path)",
        "step": "Step {k}: T = {T}, direct cost {C}",
        "step_next": "{sep}shorten {acts} by {d} (+{inc})",
        "totals_h": "Total cost = direct cost + indirect cost",
        "totals_row": "T = {T}: {D} + {I} = {C}",
        "totals_min": "Minimum: T = {T} → {C}",
        "no_data": "Not applicable: the data does not support it.",
    },
}

Section = tuple[str, list[str]]


def _num(value: float) -> str:
    """Irrational quantities (σ, Z, probabilities' durations) as 4-decimal text with a true minus sign."""
    return decimal_text(value, 4).replace("-", "−")


def delay_text(language, activity_id: str, slack: Fraction, unit_label: str) -> str:
    es = str(language) == "es"
    if slack == 0:
        return (f"La actividad {activity_id} es crítica: no puede retrasarse sin retrasar el proyecto." if es
                else f"Activity {activity_id} is critical: it cannot be delayed without delaying the project.")
    amount = number_text(slack)
    return (f"La actividad {activity_id} puede retrasarse hasta {amount} {unit_label} sin retrasar el proyecto "
            f"(holgura total)." if es else
            f"Activity {activity_id} can be delayed up to {amount} {unit_label} without delaying the project (total slack).")


class _Builder:
    def __init__(self, solution: ProjectSolution):
        self.s = solution
        self.p = solution.project
        self.lang = str(self.p.metadata.language)
        self.w = _W[self.lang]
        self.unit = self.p.metadata.time_unit

    def u(self, quantity) -> str:
        return self.unit.label(quantity, self.lang)

    def sheet(self, key: str) -> str:
        return t(self.lang, f"sheet.{key}")

    def money(self, value) -> str:
        """Currency-style amount: $69.000 (es) / $69,000 (en); non-integers keep their exact form."""
        if value is None:
            return "—"
        frac = Fraction(value)
        if frac.denominator != 1:
            return "$" + number_text(frac)
        text = f"{abs(frac.numerator):,}"
        if self.lang == "es":
            text = text.replace(",", ".")
        return ("−$" if frac < 0 else "$") + text

    @staticmethod
    def path_text(path: list[str]) -> str:
        return ARROW.join(path)

    # ------------------------------------------------------------------ handlers → (answer lines, sections)

    def gantt(self, _q) -> tuple[list[str], list[Section]]:
        g, cpm, w = self.s.gantt, self.s.cpm, self.w
        crit = ", ".join(cpm.critical_activities)
        answer = [BULLET + w["gantt"].format(sheet=self.sheet("gantt"), n=len(g.rows), h=g.horizon, unit=self.u(g.horizon)),
                  BULLET + w["gantt_bars"], BULLET + w["gantt_crit"].format(acts=crit)]
        bars = [f"{r.activity_id}: {number_text(r.early_start)} → {number_text(r.early_finish)}  /  "
                f"{number_text(r.late_start)} → {number_text(r.late_finish)}" for r in g.rows]
        return answer, [(w["gantt_h"], [w["gantt_h_line"].format(T=number_text(cpm.project_duration), h=g.horizon)]),
                        (w["gantt_bars_h"], bars)]

    def _schedule_sections(self, cpm) -> list[Section]:
        w = self.w
        rows = cpm.by_id()
        forward, backward, slack = [], [], []
        for r in cpm.activities:
            if r.predecessors:
                terms = ", ".join(f"{w['EF']} {p} = {number_text(rows[p].early_finish)}" for p in r.predecessors)
                fw = (f"{terms}" if len(r.predecessors) == 1 else f"{w['max']}({terms}) = {number_text(r.early_start)}")
            else:
                fw = "0"
            forward.append(w["fw_row"].format(id=r.activity_id, t=number_text(r.duration), fw=fw,
                                              es=number_text(r.early_start), ef=number_text(r.early_finish)))
            tag = w["critical_tag"] if r.total_slack == 0 else ""
            slack.append(w["slack_row"].format(id=r.activity_id, ls=number_text(r.late_start),
                                               es=number_text(r.early_start), slack=number_text(r.total_slack)) + tag)
        for r in reversed(cpm.activities):
            if r.successors:
                terms = ", ".join(f"{w['LS']} {s} = {number_text(rows[s].late_start)}" for s in r.successors)
                bw = (f"{terms}" if len(r.successors) == 1 else f"{w['min']}({terms}) = {number_text(r.late_finish)}")
            else:
                bw = f"T = {number_text(cpm.project_duration)}"
            backward.append(w["bw_row"].format(id=r.activity_id, t=number_text(r.duration), bw=bw,
                                               lf=number_text(r.late_finish), ls=number_text(r.late_start)))
        return [(w["fw_h"], forward), (w["bw_h"], backward), (w["slack_h"], slack)]

    def times(self, _q) -> tuple[list[str], list[Section]]:
        cpm, w = self.s.cpm, self.w
        answer = [w["times_head"].format(sheet=self.sheet("cpm"))]
        for r in cpm.activities:
            if r.is_dummy:
                continue
            tag = w["critical_tag"] if r.total_slack == 0 else ""
            answer.append(BULLET + w["times_row"].format(
                id=r.activity_id, es=number_text(r.early_start), ef=number_text(r.early_finish),
                ls=number_text(r.late_start), lf=number_text(r.late_finish), slack=number_text(r.total_slack)) + tag)
        answer.append(INDENT + w["times_legend"])
        return answer, self._schedule_sections(cpm)

    def critical_path(self, _q) -> tuple[list[str], list[Section]]:
        cpm, w = self.s.cpm, self.w
        if len(cpm.critical_paths) == 1:
            answer = [BULLET + w["path1"].format(path=self.path_text(cpm.critical_paths[0]))]
        else:
            answer = [BULLET + w["pathN"].format(paths="; ".join(self.path_text(p) for p in cpm.critical_paths))]
        rows = cpm.by_id()
        lengths = []
        for path in cpm.critical_paths:
            terms = " + ".join(number_text(rows[a].duration) for a in path)
            lengths.append(f"{self.path_text(path)}: Σ t = {terms} = {number_text(cpm.project_duration)}")
        return answer, [(w["crit_h"], [", ".join(cpm.critical_activities)]), (w["crit_len_h"], lengths)]

    def _te_section(self) -> Section:
        rows = [f"{e.activity_id}: {e.expected_duration_working}" for e in self.s.pert.estimates if not e.is_dummy]
        return self.w["te_h"], rows

    def _var_section(self) -> Section:
        rows = [f"{e.activity_id}: σ² = {e.variance_working}" for e in self.s.pert.estimates if not e.is_dummy]
        return self.w["var_h"], rows

    def _finish_section(self, cpm) -> Section:
        ends = [r for r in cpm.activities if not r.successors]
        terms = ", ".join(f"{self.w['EF']} {r.activity_id} = {number_text(r.early_finish)}" for r in ends)
        expr = f"{self.w['max']}({terms})" if len(ends) > 1 else terms
        return self.w["finish_h"], [self.w["finish_line"].format(expr=expr, T=number_text(cpm.project_duration))]

    def expected_duration(self, _q) -> tuple[list[str], list[Section]]:
        cpm, w = self.s.cpm, self.w
        sections: list[Section] = []
        T, unit = number_text(cpm.project_duration), self.u(cpm.project_duration)
        if self.p.uses_estimates:
            answer = [BULLET + w["dur_pert"].format(T=T, unit=unit)]
            sections.append(self._te_section())
        else:
            answer = [BULLET + w["dur_cpm"].format(T=T, unit=unit)]
        sections.append(self._finish_section(cpm))
        det = self.s.cpm_deterministic
        if det is not None:
            answer.append(BULLET + w["dur_det"].format(T=number_text(det.project_duration),
                                                       unit=self.u(det.project_duration)))
            sections.append(self._finish_section(det))
        c = self.s.crashing
        if c is not None:
            answer.append(BULLET + w["normal_cost"].format(C=self.money(c.normal_direct_cost)))
            costs = " + ".join(self.money(s.normal_cost) for s in c.slopes)
            sections.append((w["cost_h"], [f"{costs} = {self.money(c.normal_direct_cost)}"]))
        return answer, sections

    def variance(self, _q) -> tuple[list[str], list[Section]]:
        v, w = self.s.variance, self.w
        if v is None:
            return [w["no_data"]], []
        answer = [BULLET + w["variance"].format(var=number_text(v.total_variance), dec=decimal_text(v.total_variance)),
                  BULLET + w["std"].format(std=v.standard_deviation_working)]
        lines = [v.variance_working, v.standard_deviation_working]
        if len(v.critical_paths) > 1:
            lines.append(t(self.lang, f"pert.strategy.{v.strategy.value}"))
        return answer, [self._var_section(),
                        (w["proj_var_h"].format(path=self.path_text(v.selected_activities)), lines)]

    def estimates(self, _q) -> tuple[list[str], list[Section]]:
        if self.s.pert is None:
            return [self.w["no_data"]], []
        answer = [self.w["estimates_head"].format(sheet=self.sheet("pert"))]
        answer += [BULLET + self.w["estimates"].format(id=e.activity_id, te=number_text(e.expected_duration),
                                                       var=number_text(e.variance))
                   for e in self.s.pert.estimates if not e.is_dummy]
        return answer, [self._te_section(), self._var_section()]

    def _selected(self, results: list, question) -> list:
        ids = set(question.probability_query_ids) if question is not None else set()
        return [r for r in results if r.query.id in ids] if ids else results

    def probability(self, question) -> tuple[list[str], list[Section]]:
        results: list[ProbabilityResult] = self._selected(self.s.probabilities, question)
        if not results:
            return [], []
        w = self.w
        answer, sections = [], []
        for r in results:
            table = r.method.value == "table"
            other_label = w["exact"] if table else w["with_table"]
            other = r.probability_exact if table else r.probability_table
            answer.append(BULLET + w["prob"].format(expr=r.expression, p=f"{r.probability:.4f}",
                                                    pct=f"{r.probability * 100:.2f}"))
            zs = ", ".join(_num(c.z_exact) for c in r.z_computations if c.z_exact is not None)
            if zs:
                answer.append(INDENT + w["prob_detail"].format(z=zs, other_label=other_label, other=f"{other:.4f}"))
            lines: list[str] = []
            for step in r.steps[1:]:
                lines.extend(part.strip() for part in step.split("; "))
            sections.append((w["prob_h"].format(expr=r.expression, te=_num(r.expected_duration),
                                                sigma=_num(r.standard_deviation)), lines))
        return answer, sections

    def percentile(self, question) -> tuple[list[str], list[Section]]:
        results: list[PercentileResult] = self._selected(self.s.percentiles, question)
        if not results:
            return [], []
        w = self.w
        answer, sections = [], []
        for r in results:
            unit = self.u(r.duration_whole_units)
            if r.z_exact is None:
                answer.append(BULLET + w["pct_degenerate"].format(d=_num(r.duration), w=r.duration_whole_units, unit=unit))
            else:
                table = r.method.value == "table"
                z = f"{r.z_table:.2f}" if table else decimal_text(r.z_exact)
                answer.append(BULLET + w["pct"].format(w=r.duration_whole_units, unit=unit))
                answer.append(INDENT + w["pct_detail"].format(pct=f"{r.target_probability * 100:g}", z=z,
                                                               d=_num(r.duration), w=r.duration_whole_units))
                if not table:
                    answer.append(INDENT + w["pct_table"].format(zt=f"{r.z_table:.2f}", dt=decimal_text(r.duration_table)))
            lines = list(r.steps) + [w["pct_final"].format(d=_num(r.duration), w=r.duration_whole_units, unit=unit)]
            sections.append((w["pct_h"], lines))
        return answer, sections

    def delay(self, question) -> tuple[list[str], list[Section]]:
        ids = set(question.delay_activity_ids) if question is not None else set()
        delays = [d for d in self.s.delay_answers if not ids or d.activity_id in ids]
        if not delays:
            return [], []
        rows = self.s.cpm.by_id()
        lines = [f"{d.activity_id}: {number_text(rows[d.activity_id].late_start)} − "
                 f"{number_text(rows[d.activity_id].early_start)} = {number_text(d.total_slack)}" for d in delays]
        return [BULLET + d.text for d in delays], [(self.w["delay_h"], lines)]

    def network(self, _q) -> tuple[list[str], list[Section]]:
        arcs = ", ".join(f"{e.from_id} → {e.to_id}" for e in self.s.network.edges)
        return ([BULLET + self.w["network"].format(sheet=self.sheet("network"))],
                [(self.w["network_h"], [self.w["network_n"].format(n=len(self.s.network.activity_ids)), arcs or "—"])])

    def _crash_cuts(self, target) -> list[tuple[str, Fraction]]:
        """Total reduction per activity applied while going from the normal duration down to ``target``."""
        cuts: dict[str, Fraction] = {}
        for st in self.s.crashing.states:
            if st.project_duration <= target or not st.next_crash_activities or st.next_reduction is None:
                continue
            for act in st.next_crash_activities:
                cuts[act] = cuts.get(act, Fraction(0)) + Fraction(st.next_reduction)
        return list(cuts.items())

    def crashing(self, _q) -> tuple[list[str], list[Section]]:
        c, w = self.s.crashing, self.w
        if c is None:
            return [w["no_data"]], []
        first, last = c.states[0], c.states[-1]
        answer = [BULLET + w["crash_normal"].format(T=number_text(first.project_duration),
                                                    unit=self.u(first.project_duration), C=self.money(first.direct_cost))]
        if c.optimal is not None:
            o = c.optimal
            target = o.project_duration
            answer.append(BULLET + w["crash_opt"].format(T=number_text(target), unit=self.u(target),
                                                         C=self.money(o.total_cost), D=self.money(o.direct_cost),
                                                         I=self.money(o.indirect_cost)))
        else:
            target = last.project_duration
            answer.append(BULLET + w["crash_min"].format(T=number_text(target), unit=self.u(target),
                                                         C=self.money(last.direct_cost)))
        cuts = self._crash_cuts(target)
        if cuts:
            answer.append(BULLET + w["crash_cuts"].format(cuts=", ".join(
                w["crash_cut"].format(act=a, d=number_text(d), unit=self.u(d)) for a, d in cuts)))
        if c.optimal is not None:
            ties = [p for p in c.cost_table if p.total_cost is not None and p.total_cost == c.optimal.total_cost
                    and p.project_duration != c.optimal.project_duration]
            for p in ties:
                answer.append(INDENT + w["crash_tie"].format(T=number_text(p.project_duration),
                                                             unit=self.u(p.project_duration), C=self.money(p.total_cost)))

        slopes = [w["slope_row"].format(id=s.activity_id, working=s.working) if s.slope is not None
                  else w["slope_none"].format(id=s.activity_id) for s in c.slopes]
        steps = []
        for st in c.states:
            text = w["step"].format(k=st.step, T=number_text(st.project_duration), C=self.money(st.direct_cost))
            if st.next_crash_activities:
                text += w["step_next"].format(sep=" → ", acts=", ".join(st.next_crash_activities),
                                              d=number_text(st.next_reduction), inc=self.money(st.next_cost_increase))
            steps.append(text)
        if c.stop_reason:
            steps.append(c.stop_reason)
        sections: list[Section] = [(w["slopes_h"], slopes), (w["steps_h"], steps)]
        totals = [p for p in c.cost_table if p.total_cost is not None]
        if totals:
            rows = [w["totals_row"].format(T=number_text(p.project_duration), D=self.money(p.direct_cost),
                                           I=self.money(p.indirect_cost), C=self.money(p.total_cost)) for p in totals]
            if c.optimal is not None:
                rows.append(w["totals_min"].format(T=number_text(c.optimal.project_duration),
                                                   C=self.money(c.optimal.total_cost)))
            sections.append((w["totals_h"], rows))
        return answer, sections

    # ------------------------------------------------------------------ assembly

    def handlers(self) -> dict[O, Callable]:
        return {
            O.GANTT: self.gantt, O.EARLY_LATE_TIMES: self.times, O.SLACK: self.times,
            O.CRITICAL_PATH: self.critical_path, O.EXPECTED_DURATION: self.expected_duration, O.VARIANCE: self.variance,
            O.ACTIVITY_ESTIMATES: self.estimates, O.PROBABILITY_QUERY: self.probability,
            O.PERCENTILE_DURATION: self.percentile, O.ACTIVITY_MAX_DELAY: self.delay, O.NETWORK_DIAGRAM: self.network,
            O.CRASHING: self.crashing,
        }

    # headline order for the answer (the number the question asks for comes first)
    _ANSWER_ORDER = [O.EXPECTED_DURATION, O.CRITICAL_PATH, O.CRASHING, O.PROBABILITY_QUERY, O.PERCENTILE_DURATION,
                     O.VARIANCE, O.ACTIVITY_MAX_DELAY, O.ACTIVITY_ESTIMATES, O.EARLY_LATE_TIMES, O.SLACK, O.GANTT,
                     O.NETWORK_DIAGRAM]
    # calculation order for the procedure (each step only uses results of earlier steps)
    _PROCEDURE_ORDER = [O.ACTIVITY_ESTIMATES, O.EARLY_LATE_TIMES, O.SLACK, O.EXPECTED_DURATION, O.CRITICAL_PATH,
                        O.VARIANCE, O.PROBABILITY_QUERY, O.PERCENTILE_DURATION, O.ACTIVITY_MAX_DELAY, O.CRASHING,
                        O.GANTT, O.NETWORK_DIAGRAM]

    @staticmethod
    def _sorted(outputs: list[O], order: list[O]) -> list[O]:
        rank = {o: i for i, o in enumerate(order)}
        return sorted(outputs, key=lambda o: rank.get(o, len(order)))

    def answer(self, outputs: list[O], question=None) -> tuple[str, str, list[ProcedureStep]]:
        handlers = self.handlers()
        if O.CRASHING in outputs and self.s.crashing is not None:
            outputs = [o for o in outputs if o != O.EXPECTED_DURATION]  # crashing already states the normal T and cost
        results: dict[O, tuple[list[str], list[Section]]] = {}
        done: dict = {}
        for output in outputs:
            handler = handlers.get(output)
            if handler is None:
                continue
            if handler not in done:
                done[handler] = handler(question)
                results[output] = done[handler]
        lines: list[str] = []
        for output in self._sorted(list(results), self._ANSWER_ORDER):
            for line in results[output][0]:
                if line and line not in lines:
                    lines.append(line)
        sections: list[Section] = []
        for output in self._sorted(list(results), self._PROCEDURE_ORDER):
            for section in results[output][1]:
                if section[1] and section not in sections:
                    sections.append(section)
        steps = [ProcedureStep(number=i, title=title, lines=body) for i, (title, body) in enumerate(sections, start=1)]
        return "\n".join(lines), procedure_text(steps), steps


def procedure_text(steps: list[ProcedureStep]) -> str:
    """Numbered steps, one operation per line: '1. Title\\n   line\\n   line\\n\\n2. …'."""
    return "\n\n".join(f"{s.number}. {s.title}\n" + "\n".join(INDENT + line for line in s.lines) for s in steps)


def _question_outputs(question, solution: ProjectSolution) -> list[O]:
    outputs = list(question.outputs)
    if not outputs:
        kinds = {q.id: q.kind.value for q in solution.project.probability_queries}
        for qid in question.probability_query_ids:
            wanted = O.PERCENTILE_DURATION if kinds.get(qid) == "PERCENTILE_TO_DURATION" else O.PROBABILITY_QUERY
            if wanted not in outputs:
                outputs.append(wanted)
        if question.delay_activity_ids:
            outputs.append(O.ACTIVITY_MAX_DELAY)
    return outputs


def build_answers(solution: ProjectSolution) -> list[QuestionAnswer]:
    builder = _Builder(solution)
    labels = OUTPUT_LABELS[builder.lang]
    answers: list[QuestionAnswer] = []
    if solution.project.questions:
        for question in solution.project.questions:
            outputs = _question_outputs(question, solution)
            text, procedure, steps = builder.answer(outputs, question)
            answers.append(QuestionAnswer(label=question.label, question=question.text, answer=text,
                                          procedure=procedure, procedure_steps=steps, outputs=outputs))
        return answers
    for index, output in enumerate(solution.effective_outputs, start=1):
        text, procedure, steps = builder.answer([output])
        answers.append(QuestionAnswer(label=str(index), question=labels[output], answer=text, procedure=procedure,
                                      procedure_steps=steps, outputs=[output]))
    return answers
