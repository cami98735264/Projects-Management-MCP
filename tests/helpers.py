"""Test helpers: build activities and drafts compactly. No expected results live here."""

from __future__ import annotations

import json
from pathlib import Path

from pm_mcp.domain.models import Activity, ArrowArc, CrashData, Project, ProjectDraft
from pm_mcp.engine.aoa import convert_arrow_network
from pm_mcp.ingestion.normalize import normalize_project_input

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def act(id: str, preds=(), duration=None, est=None, crash=None, dummy: bool = False, name: str | None = None) -> Activity:
    kwargs = {}
    if est is not None:
        kwargs.update(optimistic_duration=est[0], most_likely_duration=est[1], pessimistic_duration=est[2])
    if crash is not None:
        kwargs["crash"] = CrashData(crash_duration=crash[0], normal_cost=crash[1], crash_cost=crash[2])
    return Activity(id=id, name=name, predecessors=list(preds), duration=duration, is_dummy=dummy, **kwargs)


def normalized(draft: ProjectDraft) -> Project:
    result = normalize_project_input(draft)
    assert result.ok, [i.message for i in result.issues]
    return result.project


def arcs_from(fixture: dict) -> list[ArrowArc]:
    return [ArrowArc(**arc) for arc in fixture["arcs"]]


def draft_from_arrows(fixture: dict, **kwargs) -> ProjectDraft:
    conversion = convert_arrow_network(arcs_from(fixture))
    return ProjectDraft(activities=conversion.activities, arrow_network_trace=conversion.trace, **kwargs)


def crash_activities(fixture: dict) -> list[Activity]:
    return [act(row["id"], row["predecessors"], row["duration"],
                crash=(row["crash_duration"], row["normal_cost"], row["crash_cost"])) for row in fixture["activities"]]
