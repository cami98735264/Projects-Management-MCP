"""Gantt schedule derivation following the reference convention.

Rows = activities, columns = time units numbered 1..H (column k covers the interval [k−1, k]).
Each activity gets two bars: early (ES → EF) and late (LS → LF). The observations column reads
"Actividad Crítica" for zero-slack activities or "Holgura de N <unidades>" otherwise.
"""

from __future__ import annotations

import math
from fractions import Fraction

from pydantic import BaseModel, Field

from pm_mcp.domain.models import ReportLanguage, TimeUnit
from pm_mcp.domain.numbers import Exact, number_text
from pm_mcp.engine.cpm import CpmResult
from pm_mcp.i18n import t


def bar_columns(start: Fraction, finish: Fraction) -> list[int]:
    """1-based time-unit columns touched by the interval (start, finish]."""
    if finish <= start:
        return []
    return list(range(math.floor(start) + 1, math.ceil(finish) + 1))


class GanttRow(BaseModel):
    activity_id: str
    name: str | None = None
    is_dummy: bool = False
    is_critical: bool
    early_start: Exact
    early_finish: Exact
    late_start: Exact
    late_finish: Exact
    total_slack: Exact
    early_bar_columns: list[int]
    late_bar_columns: list[int]
    partial_columns: list[int] = Field(description="Columns only partially covered (fractional times)")
    observation: str


class GanttSchedule(BaseModel):
    time_unit_plural: str
    horizon: int
    columns: list[int]
    rows: list[GanttRow]


def slack_observation(slack: Fraction, time_unit: TimeUnit, language: ReportLanguage) -> str:
    if slack == 0:
        return t(language, "critical_activity")
    return t(language, "slack_of", value=number_text(slack), unit=time_unit.label(slack, language))


def generate_gantt_schedule(
    cpm: CpmResult,
    time_unit: TimeUnit,
    language: ReportLanguage = ReportLanguage.ES,
    include_dummies: bool = False,
) -> GanttSchedule:
    horizon = max(1, math.ceil(cpm.project_duration))
    rows: list[GanttRow] = []
    for r in cpm.activities:
        if r.is_dummy and not include_dummies:
            continue
        partial = sorted({
            c for s, f in ((r.early_start, r.early_finish), (r.late_start, r.late_finish))
            for c in bar_columns(s, f) if s > c - 1 or f < c
        })
        rows.append(GanttRow(
            activity_id=r.activity_id, name=r.name, is_dummy=r.is_dummy, is_critical=r.is_critical,
            early_start=r.early_start, early_finish=r.early_finish, late_start=r.late_start,
            late_finish=r.late_finish, total_slack=r.total_slack,
            early_bar_columns=bar_columns(r.early_start, r.early_finish),
            late_bar_columns=bar_columns(r.late_start, r.late_finish),
            partial_columns=partial,
            observation=slack_observation(r.total_slack, time_unit, language),
        ))
    return GanttSchedule(
        time_unit_plural=time_unit.labels(language)[1],
        horizon=horizon,
        columns=list(range(1, horizon + 1)),
        rows=rows,
    )
