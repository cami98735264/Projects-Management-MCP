"""Period-by-period cost accumulation ('curva S') for a schedule whose activities carry direct costs.

Nothing here invents cost data or a new costing rule: the durations come from the schedule the project is
actually planned with (the compressed plan when the crashing procedure recommends one, otherwise the normal
durations) and each activity's direct cost is the one the crashing sheet already uses,
``C = Cn + slope · (Dn − D)`` — the same expression ``validation.py`` re-derives the crashing states with.

The only modelling convention added is the standard one for drawing an S-curve: an activity spreads its cost
uniformly over the time it is being executed, so the cost charged to period *k* (the interval ``(k−1, k]``) is
``cost_per_period × overlap`` of that interval with ``(ES, EF]``. Summing the periods therefore reproduces the
total direct cost exactly, and the cumulative column is the S-curve.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from fractions import Fraction

from pydantic import BaseModel, Field

from pm_mcp.domain.models import Activity, DurationSource, ReportLanguage
from pm_mcp.domain.numbers import Exact, fraction_text
from pm_mcp.engine.cpm import CpmResult, calculate_cpm
from pm_mcp.engine.crashing import CrashingResult
from pm_mcp.engine.network import ActivityNetwork


def period_overlap(start: Fraction, finish: Fraction, period: int) -> Fraction:
    """Amount of the interval (start, finish] that falls inside period *k*, i.e. inside (k−1, k]."""
    lower, upper = max(start, Fraction(period - 1)), min(finish, Fraction(period))
    return max(Fraction(0), upper - lower)


class CostCurveActivity(BaseModel):
    """One activity's cost and how it is spread over the periods it occupies."""

    activity_id: str
    duration: Exact
    normal_duration: Exact
    normal_cost: Exact = Field(description="Cn, the cost the data sheet states for the normal duration")
    reduction: Exact = Field(description="Dn − D: units the plan shortens this activity by (0 on the normal plan)")
    slope: Exact | None = None
    total_cost: Exact = Field(description="Cn + slope · reduction: the activity's direct cost in this plan")
    cost_per_period: Exact = Field(description="total_cost / duration (the whole cost in one period when D = 0)")
    early_start: Exact
    early_finish: Exact
    working: str


class CostCurvePeriod(BaseModel):
    period: int = Field(description="1-based period; period k covers the interval (k−1, k]")
    activity_ids: list[str] = Field(description="Activities being executed during the period, schedule order")
    contributions: dict[str, Exact] = Field(description="Cost charged to this period per activity")
    period_cost: Exact
    cumulative_cost: Exact
    fraction_of_total: Exact


class CostCurve(BaseModel):
    basis: str = Field(description="'crashed' when the plan comes from the compression procedure, else 'normal'")
    basis_duration: Exact = Field(description="Project duration of the plan the curve is built on")
    crash_state_step: int | None = Field(default=None, description="Crashing step the durations and costs come from")
    horizon: int
    total_cost: Exact
    schedule: CpmResult = Field(description="The base plan's schedule: what each period's network picture shows")
    activities: list[CostCurveActivity]
    periods: list[CostCurvePeriod]


def cost_curve_applies(activities: Sequence[Activity]) -> bool:
    """A cost curve needs a deterministic duration and the normal cost of every real activity."""
    real = [a for a in activities if not a.is_dummy]
    return bool(real) and all(a.crash is not None and a.duration is not None for a in real)


def calculate_cost_curve(
    network: ActivityNetwork,
    activities: Sequence[Activity],
    crashing: CrashingResult | None = None,
    language: ReportLanguage = ReportLanguage.ES,
) -> CostCurve | None:
    """Cost charged to every period of the planned schedule, and the cumulative (S) curve.

    The plan is the compressed one when ``crashing`` recommends a minimum-total-cost duration (the answer the
    exercise asks for); otherwise the normal durations and costs.
    """
    if not cost_curve_applies(activities):
        return None
    es = language == ReportLanguage.ES

    state = None
    if crashing is not None and crashing.optimal is not None:
        state = next((s for s in crashing.states if s.project_duration == crashing.optimal.project_duration), None)
    slopes = {s.activity_id: s for s in crashing.slopes} if crashing is not None else {}

    durations: dict[str, Fraction] = {
        a.id: (Fraction(0) if a.is_dummy else (state.durations[a.id] if state is not None else a.duration))
        for a in activities
    }
    cpm = calculate_cpm(network, activities, durations, DurationSource.DURATION)
    rows = cpm.by_id()

    curve_activities: list[CostCurveActivity] = []
    for act in activities:
        if act.is_dummy:
            continue
        duration, row = durations[act.id], rows[act.id]
        normal_cost, slope = act.crash.normal_cost, slopes.get(act.id).slope if act.id in slopes else None
        reduction = act.duration - duration
        total = normal_cost + (reduction * slope if slope is not None else Fraction(0))
        per_period = total / duration if duration > 0 else total
        if reduction > 0 and slope is not None:
            cost_text = (f"{fraction_text(normal_cost)} + {fraction_text(slope)} × {fraction_text(reduction)} = "
                         f"{fraction_text(total)}")
        else:
            cost_text = fraction_text(total)
        spread = (f"{fraction_text(total)} / {fraction_text(duration)} = {fraction_text(per_period)}"
                  if duration > 0 else (f"{fraction_text(total)} en un solo período (duración 0)" if es
                                        else f"{fraction_text(total)} in a single period (duration 0)"))
        curve_activities.append(CostCurveActivity(
            activity_id=act.id, duration=duration, normal_duration=act.duration, normal_cost=normal_cost,
            reduction=reduction, slope=slope, total_cost=total, cost_per_period=per_period,
            early_start=row.early_start, early_finish=row.early_finish, working=f"{cost_text}; {spread}"))

    total_cost = sum((a.total_cost for a in curve_activities), Fraction(0))
    horizon = max(1, math.ceil(cpm.project_duration))
    periods: list[CostCurvePeriod] = []
    cumulative = Fraction(0)
    for k in range(1, horizon + 1):
        contributions: dict[str, Fraction] = {}
        for a in curve_activities:
            if a.duration > 0:
                charged = a.cost_per_period * period_overlap(a.early_start, a.early_finish, k)
            else:
                charged = a.total_cost if max(1, math.ceil(a.early_finish)) == k else Fraction(0)
            if charged != 0 or (a.duration == 0 and max(1, math.ceil(a.early_finish)) == k):
                contributions[a.activity_id] = charged
        period_cost = sum(contributions.values(), Fraction(0))
        cumulative += period_cost
        periods.append(CostCurvePeriod(
            period=k, activity_ids=list(contributions), contributions=contributions, period_cost=period_cost,
            cumulative_cost=cumulative, fraction_of_total=(cumulative / total_cost if total_cost else Fraction(0))))

    return CostCurve(
        basis="crashed" if state is not None else "normal", basis_duration=cpm.project_duration,
        crash_state_step=state.step if state is not None else None, horizon=horizon, total_cost=total_cost,
        schedule=cpm, activities=curve_activities, periods=periods,
    )
