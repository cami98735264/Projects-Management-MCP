import json
import logging
from fractions import Fraction

import pytest
from pydantic import BaseModel, ValidationError

from helpers import act
from pm_mcp.domain.models import Activity, Project, ProjectDraft, TimeUnit, TimeUnitCode
from pm_mcp.domain.numbers import (
    Exact,
    NumberIn,
    ceil_tolerant,
    decimal_text,
    exact_display,
    number_text,
    round_half_up,
    to_fraction,
)
from pm_mcp.logging_config import JsonFormatter


def test_to_fraction_is_exact():
    assert to_fraction(0.1) == Fraction(1, 10)
    assert to_fraction("13/6") == Fraction(13, 6)
    assert to_fraction("2.5") == Fraction(5, 2)
    assert to_fraction({"value": 4.11, "fraction": "37/9"}) == Fraction(37, 9)
    with pytest.raises(ValueError):
        to_fraction(True)
    with pytest.raises(ValueError):
        to_fraction("abc")


def test_rounding_helpers_follow_table_convention():
    assert round_half_up(2.675, 2) == 2.68
    assert round_half_up(-1.605, 2) == -1.61
    assert decimal_text(Fraction(37, 9)) == "4.1111"
    assert decimal_text(15) == "15"
    # places=0 has no decimal point protecting the integer's own zeros
    assert [decimal_text(v, 0) for v in (6000, 12600, 60, Fraction(200, 3))] == ["6000", "12600", "60", "67"]
    assert exact_display(Fraction(37, 9)) == "37/9"
    assert ceil_tolerant(19.000000000001) == 19
    assert ceil_tolerant(19.1768) == 20


def test_exact_values_are_displayed_as_fractions():
    # every exact (rational) result is shown as a fraction, never as a rounded decimal
    assert number_text(Fraction(65, 6)) == "65/6"
    assert number_text(Fraction(2, 3)) == "2/3"
    assert number_text(Fraction(10)) == "10"
    assert exact_display(Fraction(13, 36)) == "13/36"
    # only genuinely irrational floats (sigma, Z, the normal CDF) stay decimal
    assert number_text(0.6009252125773316) == "0.6009"


def test_exact_serialization():
    class Model(BaseModel):
        x: Exact
        y: NumberIn

    m = Model(x="37/9", y=0.5)
    assert json.loads(m.model_dump_json()) == {"x": {"value": 37 / 9, "fraction": "37/9"}, "y": 0.5}
    assert m.model_dump()["x"] == Fraction(37, 9)


def test_activity_schema_rejects_bad_ids_and_unknown_fields():
    with pytest.raises(ValidationError):
        Activity(id="A B")
    with pytest.raises(ValidationError):
        Activity(id="A", optimistic=1)  # misnamed field must not be silently ignored


def test_project_cannot_be_constructed_with_invalid_graph():
    with pytest.raises(ValidationError, match="Circular dependency"):
        Project(activities=[act("A", ["B"], 1), act("B", ["A"], 2)], calculation_method="CPM", requested_outputs=["CRITICAL_PATH"])


def test_time_unit_labels():
    assert TimeUnit(code=TimeUnitCode.DAY).label(1, "es") == "día"
    assert TimeUnit(code=TimeUnitCode.MONTH).label(3, "en") == "months"
    assert TimeUnit(code=TimeUnitCode.CUSTOM, singular="sprint", plural="sprints").label(2, "es") == "sprints"


def test_draft_json_schema_is_exportable():
    schema = ProjectDraft.model_json_schema()
    assert "activities" in schema["properties"]


def test_structured_log_format():
    record = logging.LogRecord("pm_mcp.x", logging.INFO, __file__, 1, "network_built", None, None)
    record.fields = {"stage": "network_built", "activities": 3}
    payload = json.loads(JsonFormatter().format(record))
    assert payload["stage"] == "network_built" and payload["activities"] == 3
