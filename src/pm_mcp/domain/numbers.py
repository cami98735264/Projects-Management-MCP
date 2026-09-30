"""Exact rational arithmetic helpers.

All schedule arithmetic (t_e, σ², ES/EF/LS/LF, slack, costs) is done with ``fractions.Fraction`` so that
critical-activity detection (slack == 0) never depends on floating-point tolerance and so results can be
shown the way the reference material shows them (e.g. ``σ² = 5/9``). Floats appear only where the maths is
irrational (σ = √σ², the normal CDF).

Display rule: every exact value is rendered as a fraction (``number_text`` / ``exact_display``); only the
irrational floats above are rendered as decimals (``decimal_text``).
"""

from __future__ import annotations

import math
from decimal import ROUND_HALF_UP, Decimal
from fractions import Fraction
from typing import Annotated, Any

from pydantic import PlainSerializer, PlainValidator, WithJsonSchema


def to_fraction(value: Any) -> Fraction:
    """Convert user/LLM supplied numbers to an exact Fraction (0.1 -> 1/10, "13/6" -> 13/6)."""
    if isinstance(value, Fraction):
        return value
    if isinstance(value, bool):
        raise ValueError("a boolean is not a valid number")
    if isinstance(value, int):
        return Fraction(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("number must be finite")
        return Fraction(Decimal(repr(value)))
    if isinstance(value, Decimal):
        return Fraction(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            raise ValueError("empty string is not a number")
        try:
            return Fraction(text)
        except ValueError:
            raise ValueError(f"'{value}' is not a number or fraction (examples: 3, 2.5, '13/6')") from None
    if isinstance(value, dict) and "fraction" in value:
        return to_fraction(value["fraction"])
    raise ValueError(f"unsupported numeric value: {value!r}")


def fraction_text(value: Fraction) -> str:
    """'2' for integers, '5/9' otherwise."""
    if value.denominator == 1:
        return str(value.numerator)
    return f"{value.numerator}/{value.denominator}"


def round_half_up(value: float | Fraction, places: int) -> float:
    """Round like Excel's ROUND and like printed tables (ties away from zero)."""
    if isinstance(value, Fraction):
        dec = Decimal(value.numerator) / Decimal(value.denominator)
    else:
        dec = Decimal(repr(value))
    quantum = Decimal(1).scaleb(-places)
    return float(dec.quantize(quantum, rounding=ROUND_HALF_UP))


def decimal_text(value: float | Fraction, places: int = 4) -> str:
    """Human readable decimal without trailing zeros: 15 -> '15', 5/9 -> '0.5556', (6000, 0) -> '6000'.

    Trailing zeros are only fractional ones: with ``places=0`` there is no decimal point to protect the
    integer's own zeros, so nothing is stripped.
    """
    rounded = round_half_up(value, places)
    text = f"{rounded:.{places}f}"
    if places > 0:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def exact_display(value: Fraction, places: int = 4) -> str:
    """'15' for integers, '5/9' for non-integers.

    Exact (rational) results are always shown as fractions, never as rounded decimals, so the reported
    number is the number the engine computed.
    """
    return fraction_text(value)


def number_text(value: float | Fraction, places: int = 4) -> str:
    """Display any calculated number: exact rationals as fractions ('65/6'), floats as decimals.

    Everything the schedule arithmetic produces is a ``Fraction`` and is therefore rendered exactly.
    Floats reach this function only where the mathematics is genuinely irrational (sigma = sqrt(sigma^2),
    Z, the normal CDF), which has no finite fractional form and stays decimal.
    """
    if isinstance(value, Fraction):
        return fraction_text(value)
    return decimal_text(value, places)


def ceil_tolerant(value: float | Fraction) -> int:
    """Ceiling that ignores floating noise such as 19.000000000001."""
    if isinstance(value, Fraction):
        return math.ceil(value)
    return math.ceil(round(value, 9))


def _serialize_input(value: Fraction) -> int | float:
    return value.numerator if value.denominator == 1 else float(value)


def _serialize_exact(value: Fraction) -> dict[str, Any]:
    return {"value": float(value), "fraction": fraction_text(value)}


NumberIn = Annotated[
    Fraction,
    PlainValidator(to_fraction),
    PlainSerializer(_serialize_input, when_used="json"),
    WithJsonSchema(
        {
            "anyOf": [
                {"type": "number"},
                {"type": "string", "description": "decimal or fraction text, e.g. '2.5' or '13/6'"},
            ]
        }
    ),
]
"""Input number: accepts int/float/decimal-string/fraction-string, stored exactly."""

Exact = Annotated[
    Fraction,
    PlainValidator(to_fraction),
    PlainSerializer(_serialize_exact, when_used="json"),
    WithJsonSchema(
        {
            "type": "object",
            "properties": {"value": {"type": "number"}, "fraction": {"type": "string"}},
            "required": ["value", "fraction"],
        }
    ),
]
"""Calculated exact number, serialized as {"value": 0.5556, "fraction": "5/9"}."""
