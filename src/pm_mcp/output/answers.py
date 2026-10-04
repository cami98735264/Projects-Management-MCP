"""Per-question answers and their procedure (variables, constants and every operation with its numbers).

Every number quoted here is read from the engine results in the :class:`ProjectSolution`; nothing is
recomputed or rounded differently from the result models (exact values are shown as fractions, irrational
ones as 4-decimal decimals).
"""

from __future__ import annotations

from collections.abc import Callable
from fractions import Fraction

from pm_mcp.domain.models import RequestedOutput as O
from pm_mcp.domain.numbers import decimal_text, number_text
from pm_mcp.engine.probability import PercentileResult, ProbabilityResult
from pm_mcp.i18n import t
from pm_mcp.solution import ProjectSolution, QuestionAnswer

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

_W: dict[str, dict[str, str]] = {
    "es": {
        "sheet_ref": "(hoja '{sheet}')",
        "gantt": "Diagrama Gantt en la hoja '{sheet}': {n} actividades en {h} {unit}; barras de tiempos más tempranos y "
                 "más tardíos; observaciones con actividades críticas y holguras.",
        "gantt_proc": "Horizonte: ⌈T⌉ = ⌈{T}⌉ = {h} {unit} (columna k = intervalo [k − 1, k]). Barra temprana IC → TC "
                      "y barra tardía IL → TL: {bars}.",
        "times": "{id}: IC {es}, TC {ef}, IL {ls}, TL {lf}, holgura {slack}",
        "sched_intro": "Paso hacia adelante: IC = máx(TC de las predecesoras) (0 sin predecesoras), TC = IC + t. "
                       "Paso hacia atrás: TL = mín(IL de las sucesoras) (T para las finales), IL = TL − t. "
                       "Holgura = IL − IC.",
        "sched_row": "{id}: t = {t}; IC = {fw}; TC = {es} + {t} = {ef}; TL = {bw}; IL = {lf} − {t} = {ls}; "
                     "Holgura = {ls} − {es} = {slack}.",
        "max": "máx", "min": "mín", "EF": "TC", "LS": "IL",
        "path1": "Ruta crítica: {paths}.", "pathN": "Rutas críticas: {paths}.",
        "crit_proc": "Actividades críticas: holgura = IL − IC = 0 → {acts}.",
        "dur_pert": "Tiempo esperado mínimo del proyecto (t_e): {T} {unit}.",
        "dur_cpm": "Duración mínima del proyecto: {T} {unit}.",
        "dur_det": "Con duraciones determinísticas: {T} {unit}.",
        "finish_proc": "T = máx(TC de las actividades finales) = máx({terms}) = {T}.",
        "te_proc": "Variables: a (optimista), m (normal), b (pesimista). Constantes: 4 (peso de m) y 6 (divisor). "
                   "t_e = (a + b + 4m) / 6: {rows}.",
        "var_proc": "σ² = [(b − a) / 6]²: {rows}.",
        "proj_var_proc": "σ² del proyecto = Σ σ² de las actividades críticas ({path}): {var}; {std}.",
        "variance": "Varianza del proyecto: {var} ≈ {dec}; desviación estándar {std}.",
        "estimates": "{id}: t_e = {te}, σ² = {var}",
        "prob": "{expr} = {p} (Z = {z}; {other_label}: {other}).",
        "prob_nz": "{expr} = {p} ({other_label}: {other}).",
        "with_table": "con tabla", "exact": "exacta",
        "prob_proc": "Variables: T_p (duración consultada), t_e = {te}, σ = {sigma}. Fórmula: Z = (T_p − t_e) / σ. {steps}",
        "pct": "Para {pct} %: Z = {z} → T = {d}; con tabla Z = {zt} → T = {dt}; duración a especificar: {w} {unit}.",
        "pct_degenerate": "σ = 0 → T = t_e = {d}; duración a especificar: {w} {unit}.",
        "pct_proc": "Fórmula: T_p = t_e + Z · σ. {steps} Duración a especificar: ⌈{d}⌉ = {w} {unit}.",
        "delay_proc": "Retraso máximo sin retrasar el proyecto = holgura total = IL − IC: {rows}.",
        "network": "Red actividad-nodo en la hoja '{sheet}' con la simbología de nodo de seis casillas.",
        "network_proc": "{n} nodos; arcos (precedencias): {arcs}. Cada nodo muestra t, IC | TC, IL | TL y la holgura de la hoja 'CPM'.",
        "crash": "Duración normal {T0} {unit0} con costo directo {C0}; se puede reducir hasta {T1} {unit1} con costo directo {C1}.",
        "crash_opt": "Duración de costo total mínimo: {T} {unit} (costo total {C}).",
        "crash_slopes": "Pendiente = (Cc − Cn) / (Dn − Dc): {rows}.",
        "crash_step": "Paso {k}: T = {T}, costo directo {C}",
        "crash_next": "; se reduce {acts} en {d} → incremento {inc}",
        "crash_totals": "Costo total = directo + indirecto: {rows}.",
        "no_data": "No aplica: los datos no lo permiten.",
    },
    "en": {
        "sheet_ref": "(sheet '{sheet}')",
        "gantt": "Gantt chart on sheet '{sheet}': {n} activities over {h} {unit}; early and late bars; observations with "
                 "critical activities and slacks.",
        "gantt_proc": "Horizon: ⌈T⌉ = ⌈{T}⌉ = {h} {unit} (column k = interval [k − 1, k]). Early bar ES → EF and late "
                      "bar LS → LF: {bars}.",
        "times": "{id}: ES {es}, EF {ef}, LS {ls}, LF {lf}, slack {slack}",
        "sched_intro": "Forward pass: ES = max(EF of predecessors) (0 without predecessors), EF = ES + t. Backward pass: "
                       "LF = min(LS of successors) (T for end activities), LS = LF − t. Slack = LS − ES.",
        "sched_row": "{id}: t = {t}; ES = {fw}; EF = {es} + {t} = {ef}; LF = {bw}; LS = {lf} − {t} = {ls}; "
                     "Slack = {ls} − {es} = {slack}.",
        "max": "max", "min": "min", "EF": "EF", "LS": "LS",
        "path1": "Critical path: {paths}.", "pathN": "Critical paths: {paths}.",
        "crit_proc": "Critical activities: slack = LS − ES = 0 → {acts}.",
        "dur_pert": "Expected minimum project duration (t_e): {T} {unit}.",
        "dur_cpm": "Minimum project duration: {T} {unit}.",
        "dur_det": "With deterministic durations: {T} {unit}.",
        "finish_proc": "T = max(EF of end activities) = max({terms}) = {T}.",
        "te_proc": "Variables: a (optimistic), m (most likely), b (pessimistic). Constants: 4 (weight of m) and 6 "
                   "(divisor). t_e = (a + b + 4m) / 6: {rows}.",
        "var_proc": "σ² = [(b − a) / 6]²: {rows}.",
        "proj_var_proc": "Project σ² = Σ σ² of the critical activities ({path}): {var}; {std}.",
        "variance": "Project variance: {var} ≈ {dec}; standard deviation {std}.",
        "estimates": "{id}: t_e = {te}, σ² = {var}",
        "prob": "{expr} = {p} (Z = {z}; {other_label}: {other}).",
        "prob_nz": "{expr} = {p} ({other_label}: {other}).",
        "with_table": "with table", "exact": "exact",
        "prob_proc": "Variables: T_p (queried duration), t_e = {te}, σ = {sigma}. Formula: Z = (T_p − t_e) / σ. {steps}",
        "pct": "For {pct} %: Z = {z} → T = {d}; with table Z = {zt} → T = {dt}; duration to specify: {w} {unit}.",
        "pct_degenerate": "σ = 0 → T = t_e = {d}; duration to specify: {w} {unit}.",
        "pct_proc": "Formula: T_p = t_e + Z · σ. {steps} Duration to specify: ⌈{d}⌉ = {w} {unit}.",
        "delay_proc": "Maximum delay without delaying the project = total slack = LS − ES: {rows}.",
        "network": "Activity-on-node network on sheet '{sheet}' with the six-cell node notation.",
        "network_proc": "{n} nodes; arcs (precedences): {arcs}. Every node shows t, ES | EF, LS | LF and the slack from sheet 'CPM'.",
        "crash": "Normal duration {T0} {unit0} at direct cost {C0}; it can be shortened to {T1} {unit1} at direct cost {C1}.",
        "crash_opt": "Duration with minimum total cost: {T} {unit} (total cost {C}).",
        "crash_slopes": "Slope = (Cc − Cn) / (Dn − Dc): {rows}.",
        "crash_step": "Step {k}: T = {T}, direct cost {C}",
        "crash_next": "; shorten {acts} by {d} → increase {inc}",
        "crash_totals": "Total cost = direct + indirect: {rows}.",
        "no_data": "Not applicable: the data does not support it.",
    },
}


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

    # ------------------------------------------------------------------ blocks (answer part, procedure part)

    def gantt(self, _q) -> tuple[str, list[str]]:
        g, cpm = self.s.gantt, self.s.cpm
        bars = "; ".join(
            f"{r.activity_id}: {number_text(r.early_start)} → {number_text(r.early_finish)} / "
            f"{number_text(r.late_start)} → {number_text(r.late_finish)}" for r in g.rows)
        answer = self.w["gantt"].format(sheet=self.sheet("gantt"), n=len(g.rows), h=g.horizon, unit=self.u(g.horizon))
        proc = self.w["gantt_proc"].format(T=number_text(cpm.project_duration), h=g.horizon, unit=self.u(g.horizon), bars=bars)
        return answer, [proc]

    def _schedule_procedure(self, cpm) -> str:
        w = self.w
        rows = cpm.by_id()
        lines = [w["sched_intro"]]
        for r in cpm.activities:
            if r.predecessors:
                terms = ", ".join(f"{w['EF']} {p} = {number_text(rows[p].early_finish)}" for p in r.predecessors)
                fw = f"{w['max']}({terms}) = {number_text(r.early_start)}"
            else:
                fw = "0"
            if r.successors:
                terms = ", ".join(f"{w['LS']} {s} = {number_text(rows[s].late_start)}" for s in r.successors)
                bw = f"{w['min']}({terms}) = {number_text(r.late_finish)}"
            else:
                bw = f"T = {number_text(cpm.project_duration)}"
            lines.append(w["sched_row"].format(
                id=r.activity_id, t=number_text(r.duration), fw=fw, es=number_text(r.early_start),
                ef=number_text(r.early_finish), bw=bw, lf=number_text(r.late_finish), ls=number_text(r.late_start),
                slack=number_text(r.total_slack)))
        return "\n".join(lines)

    def times(self, _q) -> tuple[str, list[str]]:
        cpm = self.s.cpm
        body = "; ".join(self.w["times"].format(
            id=r.activity_id, es=number_text(r.early_start), ef=number_text(r.early_finish), ls=number_text(r.late_start),
            lf=number_text(r.late_finish), slack=number_text(r.total_slack)) for r in cpm.activities if not r.is_dummy)
        return f"{self.w['sheet_ref'].format(sheet=self.sheet('cpm'))} {body}.", [self._schedule_procedure(cpm)]

    def critical_path(self, _q) -> tuple[str, list[str]]:
        cpm = self.s.cpm
        paths = "; ".join(" – ".join(p) for p in cpm.critical_paths)
        key = "path1" if len(cpm.critical_paths) == 1 else "pathN"
        rows = cpm.by_id()
        proc = [self.w["crit_proc"].format(acts=", ".join(cpm.critical_activities))]
        for path in cpm.critical_paths:
            terms = " + ".join(number_text(rows[a].duration) for a in path)
            proc.append(f"{' – '.join(path)}: Σ t = {terms} = {number_text(cpm.project_duration)}.")
        return self.w[key].format(paths=paths), [" ".join(proc)]

    def _te_procedure(self) -> str:
        rows = "; ".join(f"{e.activity_id}: {e.expected_duration_working}" for e in self.s.pert.estimates if not e.is_dummy)
        return self.w["te_proc"].format(rows=rows)

    def _var_procedure(self) -> str:
        rows = "; ".join(f"{e.activity_id}: σ² = {e.variance_working}" for e in self.s.pert.estimates if not e.is_dummy)
        return self.w["var_proc"].format(rows=rows)

    def _finish_procedure(self, cpm) -> str:
        rows = cpm.by_id()
        ends = [r for r in cpm.activities if not r.successors]
        terms = ", ".join(f"{self.w['EF']} {r.activity_id} = {number_text(r.early_finish)}" for r in ends)
        return self.w["finish_proc"].format(terms=terms, T=number_text(cpm.project_duration)) if rows else ""

    def expected_duration(self, _q) -> tuple[str, list[str]]:
        cpm = self.s.cpm
        proc: list[str] = []
        if self.p.uses_estimates:
            answer = self.w["dur_pert"].format(T=number_text(cpm.project_duration), unit=self.u(cpm.project_duration))
            proc.append(self._te_procedure())
        else:
            answer = self.w["dur_cpm"].format(T=number_text(cpm.project_duration), unit=self.u(cpm.project_duration))
        proc.append(self._finish_procedure(cpm))
        det = self.s.cpm_deterministic
        if det is not None:
            answer += " " + self.w["dur_det"].format(T=number_text(det.project_duration), unit=self.u(det.project_duration))
            proc.append(self._finish_procedure(det))
        return answer, proc

    def variance(self, _q) -> tuple[str, list[str]]:
        v = self.s.variance
        if v is None:
            return self.w["no_data"], []
        answer = self.w["variance"].format(var=v.variance_working, dec=decimal_text(v.total_variance),
                                           std=v.standard_deviation_working)
        proc = [self._var_procedure(),
                self.w["proj_var_proc"].format(path=" – ".join(v.selected_activities), var=v.variance_working,
                                               std=v.standard_deviation_working)]
        if len(v.critical_paths) > 1:
            proc.append(t(self.lang, f"pert.strategy.{v.strategy.value}"))
        return answer, proc

    def estimates(self, _q) -> tuple[str, list[str]]:
        if self.s.pert is None:
            return self.w["no_data"], []
        body = "; ".join(self.w["estimates"].format(id=e.activity_id, te=number_text(e.expected_duration),
                                                    var=number_text(e.variance))
                         for e in self.s.pert.estimates if not e.is_dummy)
        return f"{self.w['sheet_ref'].format(sheet=self.sheet('pert'))} {body}.", [self._te_procedure(), self._var_procedure()]

    def _selected(self, results: list, question) -> list:
        ids = set(question.probability_query_ids) if question is not None else set()
        return [r for r in results if r.query.id in ids] if ids else results

    def probability(self, question) -> tuple[str, list[str]]:
        results: list[ProbabilityResult] = self._selected(self.s.probabilities, question)
        if not results:
            return "", []
        parts, proc = [], []
        for r in results:
            table = r.method.value == "table"
            other_label = self.w["exact"] if table else self.w["with_table"]
            other = r.probability_exact if table else r.probability_table
            zs = ", ".join(decimal_text(c.z_exact) for c in r.z_computations if c.z_exact is not None)
            key = "prob" if zs else "prob_nz"
            parts.append(self.w[key].format(expr=r.expression, p=f"{r.probability:.4f}", z=zs, other_label=other_label,
                                            other=f"{other:.4f}"))
            proc.append(self.w["prob_proc"].format(te=_num(r.expected_duration), sigma=_num(r.standard_deviation),
                                                   steps="\n".join(r.steps)))
        return " ".join(parts), proc

    def percentile(self, question) -> tuple[str, list[str]]:
        results: list[PercentileResult] = self._selected(self.s.percentiles, question)
        if not results:
            return "", []
        parts, proc = [], []
        for r in results:
            unit = self.u(r.duration_whole_units)
            if r.z_exact is None:
                parts.append(self.w["pct_degenerate"].format(d=_num(r.duration), w=r.duration_whole_units, unit=unit))
            else:
                parts.append(self.w["pct"].format(
                    pct=f"{r.target_probability * 100:g}", z=decimal_text(r.z_exact), d=decimal_text(r.duration_exact),
                    zt=f"{r.z_table:.2f}", dt=decimal_text(r.duration_table), w=r.duration_whole_units, unit=unit))
            proc.append(self.w["pct_proc"].format(steps="\n".join(r.steps), d=_num(r.duration), w=r.duration_whole_units,
                                                  unit=unit))
        return " ".join(parts), proc

    def delay(self, question) -> tuple[str, list[str]]:
        ids = set(question.delay_activity_ids) if question is not None else set()
        delays = [d for d in self.s.delay_answers if not ids or d.activity_id in ids]
        if not delays:
            return "", []
        rows = self.s.cpm.by_id()
        proc = self.w["delay_proc"].format(rows="; ".join(
            f"{d.activity_id}: {number_text(rows[d.activity_id].late_start)} − "
            f"{number_text(rows[d.activity_id].early_start)} = {number_text(d.total_slack)}" for d in delays))
        return " ".join(d.text for d in delays), [proc]

    def network(self, _q) -> tuple[str, list[str]]:
        arcs = ", ".join(f"{e.from_id}→{e.to_id}" for e in self.s.network.edges)
        return (self.w["network"].format(sheet=self.sheet("network")),
                [self.w["network_proc"].format(n=len(self.s.network.activity_ids), arcs=arcs or "—")])

    def crashing(self, _q) -> tuple[str, list[str]]:
        c = self.s.crashing
        if c is None:
            return self.w["no_data"], []
        first, last = c.states[0], c.states[-1]
        answer = self.w["crash"].format(
            T0=number_text(first.project_duration), unit0=self.u(first.project_duration), C0=number_text(first.direct_cost),
            T1=number_text(last.project_duration), unit1=self.u(last.project_duration), C1=number_text(last.direct_cost))
        if c.optimal is not None:
            answer += " " + self.w["crash_opt"].format(T=number_text(c.optimal.project_duration),
                                                       unit=self.u(c.optimal.project_duration),
                                                       C=number_text(c.optimal.total_cost))
        proc = [self.w["crash_slopes"].format(rows="; ".join(f"{s.activity_id}: {s.working}" for s in c.slopes))]
        steps = []
        for st in c.states:
            text = self.w["crash_step"].format(k=st.step, T=number_text(st.project_duration), C=number_text(st.direct_cost))
            if st.next_crash_activities:
                text += self.w["crash_next"].format(acts=", ".join(st.next_crash_activities),
                                                    d=number_text(st.next_reduction), inc=number_text(st.next_cost_increase))
            steps.append(text + ".")
        proc.append(" ".join(steps) + " " + c.stop_reason)
        totals = [p for p in c.cost_table if p.total_cost is not None]
        if totals:
            proc.append(self.w["crash_totals"].format(rows="; ".join(
                f"T = {number_text(p.project_duration)}: {number_text(p.direct_cost)} + {number_text(p.indirect_cost)} = "
                f"{number_text(p.total_cost)}" for p in totals)))
        return answer, proc

    # ------------------------------------------------------------------ assembly

    def handlers(self) -> dict[O, Callable]:
        return {
            O.GANTT: self.gantt, O.EARLY_LATE_TIMES: self.times, O.SLACK: self.times,
            O.CRITICAL_PATH: self.critical_path, O.EXPECTED_DURATION: self.expected_duration, O.VARIANCE: self.variance,
            O.ACTIVITY_ESTIMATES: self.estimates, O.PROBABILITY_QUERY: self.probability,
            O.PERCENTILE_DURATION: self.percentile, O.ACTIVITY_MAX_DELAY: self.delay, O.NETWORK_DIAGRAM: self.network,
            O.CRASHING: self.crashing,
        }

    def answer(self, outputs: list[O], question=None) -> tuple[str, str]:
        handlers = self.handlers()
        answers: list[str] = []
        procedures: list[str] = []
        for output in outputs:
            handler = handlers.get(output)
            if handler is None:
                continue
            text, proc = handler(question)
            if text and text not in answers:
                answers.append(text)
            for block in proc:
                if block and block not in procedures:
                    procedures.append(block)
        return " ".join(answers), "\n".join(procedures)


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
            text, procedure = builder.answer(outputs, question)
            answers.append(QuestionAnswer(label=question.label, question=question.text, answer=text,
                                          procedure=procedure, outputs=outputs))
        return answers
    for index, output in enumerate(solution.effective_outputs, start=1):
        text, procedure = builder.answer([output])
        answers.append(QuestionAnswer(label=str(index), question=labels[output], answer=text, procedure=procedure,
                                      outputs=[output]))
    return answers
