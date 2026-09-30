"""Independent re-derivation of results (``validate_calculations``).

Every check recomputes a quantity by a *different route* than the engine used (alternate formula, different
traversal order, numerical integration instead of erf, ...) and compares. A single failure is a hard error.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from fractions import Fraction

from pm_mcp.domain.models import CalculationMethod, DurationSource, ProbabilityQueryKind, Project
from pm_mcp.domain.numbers import round_half_up
from pm_mcp.engine.cost_curve import period_overlap
from pm_mcp.engine.cpm import CpmResult
from pm_mcp.engine.gantt import bar_columns
from pm_mcp.solution import CalculationValidationReport, CheckResult, ProjectSolution

_TOL = 1e-9


def _simpson_cdf(z: float, intervals: int = 4000) -> float:
    """Φ(z) = 0.5 ± ∫₀^|z| φ(t) dt by composite Simpson's rule — independent of math.erf."""
    upper = abs(z)
    h = upper / intervals
    total = 0.0
    for i in range(intervals + 1):
        weight = 1 if i in (0, intervals) else (4 if i % 2 else 2)
        x = i * h
        total += weight * math.exp(-x * x / 2)
    integral = total * h / 3 / math.sqrt(2 * math.pi)
    return 0.5 + integral if z >= 0 else 0.5 - integral


def _close(a: float, b: float, tol: float = _TOL) -> bool:
    return math.isclose(a, b, rel_tol=tol, abs_tol=tol)


class _Checker:
    def __init__(self) -> None:
        self.checks: list[CheckResult] = []

    def run(self, name: str, fn: Callable[[], list[str]]) -> None:
        try:
            problems = fn()
        except Exception as exc:  # a crash inside a check is itself a failed check
            problems = [f"check raised {type(exc).__name__}: {exc}"]
        self.checks.append(CheckResult(
            name=name, passed=not problems,
            detail="ok" if not problems else "; ".join(problems[:10]) + (" …" if len(problems) > 10 else "")))


def _schedule_checks(checker: _Checker, label: str, project: Project, cpm: CpmResult,
                     expected: dict[str, Fraction]) -> None:
    acts = project.activity_map()
    rows = cpm.by_id()

    def durations() -> list[str]:
        return [f"{r.activity_id}: duration {r.duration} ≠ {expected[r.activity_id]}"
                for r in cpm.activities if r.duration != expected[r.activity_id]]

    def identities() -> list[str]:
        bad = []
        for r in cpm.activities:
            if r.early_finish - r.early_start != r.duration or r.late_finish - r.late_start != r.duration:
                bad.append(f"{r.activity_id}: EF−ES or LF−LS ≠ duration")
            if not (r.late_start - r.early_start == r.late_finish - r.early_finish == r.total_slack):
                bad.append(f"{r.activity_id}: slack definitions disagree (LS−ES vs LF−EF)")
            if r.total_slack < 0 or r.early_start < 0:
                bad.append(f"{r.activity_id}: negative slack or start")
            if r.is_critical != (r.total_slack == 0):
                bad.append(f"{r.activity_id}: critical flag does not match slack")
        return bad

    def precedence() -> list[str]:
        bad = []
        for act in project.activities:
            for p in act.predecessors:
                if rows[act.id].early_start < rows[p].early_finish:
                    bad.append(f"{act.id} starts at {rows[act.id].early_start} before {p} finishes at {rows[p].early_finish}")
                if rows[p].late_finish > rows[act.id].late_start:
                    bad.append(f"late times violate {p} → {act.id}")
        return bad

    def longest_path() -> list[str]:
        # Label-correcting relaxation in *input* order (not topological) until a fixed point.
        start = {a.id: Fraction(0) for a in project.activities}
        for _ in range(len(project.activities) + 1):
            changed = False
            for act in project.activities:
                for p in act.predecessors:
                    candidate = start[p] + expected[p]
                    if candidate > start[act.id]:
                        start[act.id] = candidate
                        changed = True
            if not changed:
                break
        total = max(start[a] + expected[a] for a in start)
        return [] if total == cpm.project_duration else [f"longest path {total} ≠ project duration {cpm.project_duration}"]

    def critical_paths() -> list[str]:
        if not cpm.critical_paths:
            return ["no critical path found"]
        bad = []
        for path in cpm.critical_paths:
            total = sum((expected[a] for a in path), Fraction(0))
            if total != cpm.project_duration:
                bad.append(f"path {'-'.join(path)} sums to {total}, not {cpm.project_duration}")
            for a, b in zip(path, path[1:]):
                reachable = b in acts and (a in acts[b].predecessors or _reaches_via_dummies(project, a, b))
                if not reachable:
                    bad.append(f"{a} → {b} is not a precedence arc")
        crit = sorted(a.id for a in project.activities if not a.is_dummy and rows[a.id].total_slack == 0)
        if crit != sorted(cpm.critical_activities):
            bad.append(f"critical activities {sorted(cpm.critical_activities)} ≠ zero-slack activities {crit}")
        return bad

    checker.run(f"{label}: durations match their source", durations)
    checker.run(f"{label}: EF−ES = LF−LS = duration and LS−ES = LF−EF", identities)
    checker.run(f"{label}: every precedence respected", precedence)
    checker.run(f"{label}: project duration = longest path (independent relaxation)", longest_path)
    checker.run(f"{label}: each critical path sums to the project duration", critical_paths)


def _reaches_via_dummies(project: Project, a: str, b: str) -> bool:
    acts = project.activity_map()
    frontier = [p for p in acts[b].predecessors if acts[p].is_dummy]
    seen: set[str] = set()
    while frontier:
        d = frontier.pop()
        if d in seen:
            continue
        seen.add(d)
        if a in acts[d].predecessors:
            return True
        frontier.extend(p for p in acts[d].predecessors if acts[p].is_dummy)
    return False


def validate_calculations(project: Project, solution: ProjectSolution) -> CalculationValidationReport:
    checker = _Checker()
    acts = project.activity_map()

    def network_edges() -> list[str]:
        expected = {(p, a.id) for a in project.activities for p in a.predecessors}
        actual = {(e.from_id, e.to_id) for e in solution.network.edges}
        return [] if expected == actual else [f"edges differ: missing {expected - actual}, extra {actual - expected}"]

    checker.run("network arcs match the activity predecessor lists", network_edges)

    expected_te: dict[str, Fraction] = {}
    if solution.pert is not None:
        def pert_alternate() -> list[str]:
            bad = []
            for e in solution.pert.estimates:
                act = acts[e.activity_id]
                if act.is_dummy:
                    ok = e.expected_duration == 0 and e.variance == 0
                else:
                    a, m, b = act.estimates
                    te = a / 6 + Fraction(2, 3) * m + b / 6
                    var = (b - a) * (b - a) / 36
                    ok = e.expected_duration == te and e.variance == var and _close(e.standard_deviation, math.sqrt(var))
                    expected_te[act.id] = te
                if not ok:
                    bad.append(f"{e.activity_id}: t_e/σ² mismatch")
                expected_te.setdefault(act.id, Fraction(0))
            missing = {a.id for a in project.activities} - {e.activity_id for e in solution.pert.estimates}
            return bad + [f"missing estimates for {sorted(missing)}"] * bool(missing)

        checker.run("PERT t_e = a/6 + 2m/3 + b/6 and σ² = (b−a)²/36", pert_alternate)

    deterministic = {a.id: (Fraction(0) if a.is_dummy else (a.duration or Fraction(0))) for a in project.activities}
    primary_expected = expected_te if solution.cpm.duration_source == DurationSource.EXPECTED_DURATION else deterministic
    _schedule_checks(checker, "schedule", project, solution.cpm, primary_expected)
    if solution.cpm_deterministic is not None:
        _schedule_checks(checker, "deterministic schedule", project, solution.cpm_deterministic, deterministic)
    if project.calculation_method == CalculationMethod.BOTH and solution.cpm_deterministic is None:
        checker.checks.append(CheckResult(name="BOTH method has deterministic schedule", passed=False, detail="missing"))

    if solution.variance is not None:
        def variance_resum() -> list[str]:
            bad = []
            v = solution.variance
            resum = Fraction(0)
            for a in v.selected_activities:
                act = acts[a]
                resum += ((act.pessimistic_duration - act.optimistic_duration) / 6) ** 2 if not act.is_dummy else 0
            if resum != v.total_variance:
                bad.append(f"Σσ² recomputed {resum} ≠ {v.total_variance}")
            if not _close(v.standard_deviation ** 2, float(v.total_variance)):
                bad.append("σ² ≠ σ·σ")
            if not set(v.selected_activities) <= set(solution.cpm.critical_activities):
                bad.append("variance uses non-critical activities")
            if v.expected_project_duration != solution.cpm.project_duration:
                bad.append("expected duration differs from schedule")
            return bad

        checker.run("project variance re-summed from the raw estimates", variance_resum)

    if solution.probabilities:
        def probability_recheck() -> list[str]:
            bad = []
            sigma = solution.variance.standard_deviation if solution.variance else None
            for r in solution.probabilities:
                if sigma is not None and not _close(r.standard_deviation, sigma):
                    bad.append(f"{r.expression}: σ differs from project σ")
                if not _close(r.expected_duration, float(solution.cpm.project_duration)):
                    bad.append(f"{r.expression}: t_e differs from schedule")
                for c in r.z_computations:
                    if c.z_exact is None:
                        continue
                    if not _close(c.z_exact, (c.target_duration - r.expected_duration) / r.standard_deviation):
                        bad.append(f"{r.expression}: Z recomputation differs")
                    if not _close(c.cdf_exact, _simpson_cdf(c.z_exact), 1e-7):
                        bad.append(f"{r.expression}: Φ({c.z_exact:.4f}) differs from numerical integration")
                    if c.cdf_table != round_half_up(_simpson_cdf(round_half_up(c.z_exact, 2)), 4):
                        bad.append(f"{r.expression}: table value mismatch")
                cdf = {c.bound: c for c in r.z_computations}
                if r.z_computations[0].z_exact is not None:
                    if r.query.kind == ProbabilityQueryKind.AT_MOST:
                        combo = cdf["upper"].cdf_exact
                    elif r.query.kind == ProbabilityQueryKind.AT_LEAST:
                        combo = 1 - cdf["lower"].cdf_exact
                    else:
                        combo = cdf["upper"].cdf_exact - cdf["lower"].cdf_exact
                    if not _close(combo, r.probability_exact):
                        bad.append(f"{r.expression}: probability combination mismatch")
                if not 0 <= r.probability_exact <= 1:
                    bad.append(f"{r.expression}: probability outside [0, 1]")
            return bad

        checker.run("probabilities re-derived by numerical integration of the normal pdf", probability_recheck)

    if solution.percentiles:
        def percentile_recheck() -> list[str]:
            bad = []
            for r in solution.percentiles:
                if r.z_exact is None:
                    continue
                if not _close(_simpson_cdf(r.z_exact), r.target_probability, 1e-7):
                    bad.append(f"Φ(Z={r.z_exact}) ≠ {r.target_probability}")
                if not _close(r.duration_exact, r.expected_duration + r.z_exact * r.standard_deviation):
                    bad.append("T_p ≠ t_e + Zσ")
                table = lambda z: round_half_up(_simpson_cdf(round_half_up(z, 2)), 4)  # noqa: E731
                if not (table(r.z_table) >= r.target_probability > table(r.z_table - 0.01)):
                    bad.append(f"table Z {r.z_table} is not the smallest tabulated Z reaching {r.target_probability}")
                if r.duration_whole_units_exact < r.duration_exact - 1e-9 or r.duration_whole_units_exact - r.duration_exact >= 1:
                    bad.append("rounded-up duration inconsistent")
            return bad

        checker.run("percentile durations re-derived (Φ(Z) round-trip, table rule)", percentile_recheck)

    if solution.gantt is not None:
        def gantt_recheck() -> list[str]:
            bad = []
            rows = solution.cpm.by_id()
            for g in solution.gantt.rows:
                r = rows[g.activity_id]
                if g.early_bar_columns != bar_columns(r.early_start, r.early_finish):
                    bad.append(f"{g.activity_id}: early bar misplaced")
                if g.late_bar_columns != bar_columns(r.late_start, r.late_finish):
                    bad.append(f"{g.activity_id}: late bar misplaced")
                if g.is_critical != r.is_critical:
                    bad.append(f"{g.activity_id}: critical flag mismatch")
                if max(g.late_bar_columns + g.early_bar_columns, default=0) > solution.gantt.horizon:
                    bad.append(f"{g.activity_id}: bar exceeds horizon")
            return bad

        checker.run("Gantt bars match ES→EF / LS→LF and critical flags", gantt_recheck)

    if solution.crashing is not None:
        def crashing_recheck() -> list[str]:
            from pm_mcp.engine.cpm import calculate_cpm

            bad = []
            slopes = {s.activity_id: s for s in solution.crashing.slopes}
            for state in solution.crashing.states:
                cpm = calculate_cpm(solution.network, project.activities, state.durations, DurationSource.DURATION)
                if cpm.project_duration != state.project_duration:
                    bad.append(f"step {state.step}: duration {state.project_duration} ≠ recomputed {cpm.project_duration}")
                cost = Fraction(0)
                for a in project.activities:
                    if a.is_dummy:
                        continue
                    s = slopes[a.id]
                    d = state.durations[a.id]
                    if d < s.crash_duration or d > s.normal_duration:
                        bad.append(f"step {state.step}: {a.id} duration {d} outside [{s.crash_duration}, {s.normal_duration}]")
                    cost += s.normal_cost + ((s.normal_duration - d) * s.slope if s.slope is not None else 0)
                if cost != state.direct_cost:
                    bad.append(f"step {state.step}: direct cost {state.direct_cost} ≠ Σ(Cn + slope·reduction) = {cost}")
            return bad

        checker.run("crashing states re-scheduled and re-costed", crashing_recheck)

    if solution.cost_curve is not None:
        def cost_curve_recheck() -> list[str]:
            curve = solution.cost_curve
            bad = []
            activities = {a.activity_id: a for a in curve.activities}
            for a in curve.activities:
                if a.total_cost != a.normal_cost + (a.reduction * a.slope if a.slope is not None else 0):
                    bad.append(f"{a.activity_id}: cost {a.total_cost} ≠ Cn + slope·reduction")
                if a.duration > 0 and a.cost_per_period * a.duration != a.total_cost:
                    bad.append(f"{a.activity_id}: cost per period × duration ≠ {a.total_cost}")
                if a.early_finish - a.early_start != a.duration:
                    bad.append(f"{a.activity_id}: EF − ES ≠ duration {a.duration}")
            if sum((a.total_cost for a in curve.activities), Fraction(0)) != curve.total_cost:
                bad.append(f"Σ activity cost ≠ total cost {curve.total_cost}")
            running = Fraction(0)
            for period in curve.periods:
                # re-derive the period's charge from the schedule, independently of the engine's contributions
                expected = Fraction(0)
                for a in curve.activities:
                    expected += (a.cost_per_period * period_overlap(a.early_start, a.early_finish, period.period)
                                 if a.duration > 0 else
                                 (a.total_cost if max(1, math.ceil(a.early_finish)) == period.period else Fraction(0)))
                if expected != period.period_cost:
                    bad.append(f"period {period.period}: cost {period.period_cost} ≠ re-derived {expected}")
                running += period.period_cost
                if running != period.cumulative_cost:
                    bad.append(f"period {period.period}: cumulative {period.cumulative_cost} ≠ running sum {running}")
                for aid in period.activity_ids:
                    a = activities[aid]
                    if a.duration > 0 and period_overlap(a.early_start, a.early_finish, period.period) == 0:
                        bad.append(f"period {period.period}: {aid} listed but not in progress")
            if running != curve.total_cost:
                bad.append(f"Σ period cost {running} ≠ total cost {curve.total_cost}")
            return bad

        checker.run("cost curve: periods sum to the total cost and follow the schedule", cost_curve_recheck)

    if solution.delay_answers:
        def delay_recheck() -> list[str]:
            rows = solution.cpm.by_id()
            return [f"{d.activity_id}: slack mismatch" for d in solution.delay_answers
                    if d.total_slack != rows[d.activity_id].total_slack]

        checker.run("maximum delays equal total slack", delay_recheck)

    return CalculationValidationReport(passed=all(c.passed for c in checker.checks), checks=checker.checks)
