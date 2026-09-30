"""Methodology knowledge derived once from the course reference material and persisted as JSON.

``inventory.json`` is machine-generated (``scripts/build_reference_inventory.py``): every sheet, non-empty cell and
embedded image of the reference files with location and sha256. ``methodology.json`` is the curated
classification of each of those items plus the methodology *rules* the engine implements, each citing its sources.
Neither file contains exercise numbers used for calculation — the engine only ever computes from user input.
"""

from __future__ import annotations

import json
from functools import lru_cache
from importlib import resources
from typing import Any

TOPICS = ("pert", "cpm", "gantt", "network", "probability", "crashing")


@lru_cache(maxsize=None)
def load_methodology() -> dict[str, Any]:
    return json.loads(resources.files(__package__).joinpath("methodology.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=None)
def load_inventory() -> dict[str, Any]:
    return json.loads(resources.files(__package__).joinpath("inventory.json").read_text(encoding="utf-8"))


def methodology_topic(topic: str | None = None) -> dict[str, Any]:
    data = load_methodology()
    if not topic:
        return data
    topic = topic.lower().strip()
    return {
        "topic": topic,
        "rules": {key: rule for key, rule in data["rules"].items() if topic in rule.get("topics", [])},
        "items": [item for item in data["items"] if topic in item.get("topics", [])],
        "available_topics": list(TOPICS),
    }
