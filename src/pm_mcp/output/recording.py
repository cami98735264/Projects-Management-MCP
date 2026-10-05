"""Recording proxy around an xlsxwriter worksheet.

The classic writer draws every sheet through :class:`RecordingSheet`: each call is forwarded unchanged to the
real worksheet (so the classic workbook is byte-for-byte what it was) and also kept as a :class:`Rec` with the
format *spec* (not the xlsxwriter object) and the tags active at that moment. The progressive layout
(:mod:`pm_mcp.output.progressive`) replays these records into a new workbook, cell by cell, deciding in which
step each one appears from its tags and coordinates.

All coordinates in records are 1-based (row, column), like the rest of the writer.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from xlsxwriter.utility import xl_cell_to_rowcol


@dataclass
class Rec:
    op: str                      # cell | merge | row | col | cf
    seq: int
    tags: tuple[str, ...]
    r: int = 0
    c: int = 0
    r2: int = 0
    c2: int = 0
    vtype: str = ""              # string | number | formula | blank (op == cell)
    value: Any = None            # text, number or formula text ("=...")
    cached: Any = None           # cached formula result
    spec: dict[str, Any] = field(default_factory=dict)
    size: float | None = None    # row height / column width
    cf: dict[str, Any] | None = None  # conditional format options, "format" replaced by its spec

    def has(self, tag: str) -> bool:
        return tag in self.tags

    def tag_value(self, prefix: str) -> str | None:
        """Value of the innermost tag ``prefix:value`` (e.g. ``state:3`` → ``'3'``)."""
        for tag in reversed(self.tags):
            if tag.startswith(prefix + ":"):
                return tag[len(prefix) + 1:]
        return None


class RecordingSheet:
    """Forward every call to ``target`` (may be None: record only) and record the drawing calls."""

    def __init__(self, target: Any, spec_of: Callable[[Any], dict[str, Any]]):
        self._target = target
        self._spec_of = spec_of
        self.recs: list[Rec] = []
        self.tags: list[str] = []

    def __getattr__(self, name: str) -> Any:  # freeze_panes, … go straight to the worksheet
        if self._target is None:
            return lambda *a, **k: None
        return getattr(self._target, name)

    @contextmanager
    def tagged(self, *tags: str):
        self.tags.extend(tags)
        try:
            yield
        finally:
            del self.tags[len(self.tags) - len(tags):]

    def _add(self, **kw: Any) -> None:
        self.recs.append(Rec(seq=len(self.recs), tags=tuple(self.tags), **kw))

    def _spec(self, fmt: Any) -> dict[str, Any]:
        return dict(self._spec_of(fmt)) if fmt is not None else {}

    # ---------------------------------------------------------------- recorded calls (0-based like xlsxwriter)

    def write_string(self, row: int, col: int, string: str, cell_format: Any = None):
        self._add(op="cell", r=row + 1, c=col + 1, vtype="string", value=string, spec=self._spec(cell_format))
        if self._target is not None:
            return self._target.write_string(row, col, string, cell_format)

    def shadow_string(self, row: int, col: int, string: str, cell_format: Any = None) -> None:
        """Record a string without writing it (an earlier-step variant of a cell the writer draws next)."""
        self._add(op="cell", r=row + 1, c=col + 1, vtype="string", value=string, spec=self._spec(cell_format))

    def shadow_blank(self, row: int, col: int, cell_format: Any = None) -> None:
        """Record a formatted blank without writing it (an earlier-step variant of a cell drawn next)."""
        self._add(op="cell", r=row + 1, c=col + 1, vtype="blank", value=None, spec=self._spec(cell_format))

    def write_number(self, row: int, col: int, number: float, cell_format: Any = None):
        self._add(op="cell", r=row + 1, c=col + 1, vtype="number", value=number, spec=self._spec(cell_format))
        if self._target is not None:
            return self._target.write_number(row, col, number, cell_format)

    def write_formula(self, row: int, col: int, formula: str, cell_format: Any = None, value: Any = 0):
        self._add(op="cell", r=row + 1, c=col + 1, vtype="formula", value=formula, cached=value,
                  spec=self._spec(cell_format))
        if self._target is not None:
            return self._target.write_formula(row, col, formula, cell_format, value)

    def write_blank(self, row: int, col: int, blank: Any = None, cell_format: Any = None):
        self._add(op="cell", r=row + 1, c=col + 1, vtype="blank", value=None, spec=self._spec(cell_format))
        if self._target is not None:
            return self._target.write_blank(row, col, blank, cell_format)

    def merge_range(self, first_row: int, first_col: int, last_row: int, last_col: int, data: Any,
                    cell_format: Any = None):
        spec = self._spec(cell_format)
        self._add(op="merge", r=first_row + 1, c=first_col + 1, r2=last_row + 1, c2=last_col + 1, spec=spec)
        if isinstance(data, (int, float)) and not isinstance(data, bool):
            self._add(op="cell", r=first_row + 1, c=first_col + 1, vtype="number", value=data, spec=spec)
        elif data in (None, ""):
            self._add(op="cell", r=first_row + 1, c=first_col + 1, vtype="blank", spec=spec)
        else:
            self._add(op="cell", r=first_row + 1, c=first_col + 1, vtype="string", value=str(data), spec=spec)
        if self._target is not None:
            return self._target.merge_range(first_row, first_col, last_row, last_col, data, cell_format)

    def set_row(self, row: int, height: float | None = None, cell_format: Any = None, options: Any = None):
        self._add(op="row", r=row + 1, size=height)
        if self._target is not None:
            return self._target.set_row(row, height, cell_format, options)

    def set_column(self, first_col: int, last_col: int, width: float | None = None, cell_format: Any = None,
                   options: Any = None):
        self._add(op="col", c=first_col + 1, c2=last_col + 1, size=width)
        if self._target is not None:
            return self._target.set_column(first_col, last_col, width, cell_format, options)

    def conditional_format(self, cell_range: str, options: dict[str, Any]):
        first, _, last = cell_range.partition(":")
        r1, c1 = xl_cell_to_rowcol(first)
        r2, c2 = xl_cell_to_rowcol(last or first)
        cf = {k: v for k, v in options.items() if k != "format"}
        cf["format"] = self._spec(options.get("format"))
        self._add(op="cf", r=r1 + 1, c=c1 + 1, r2=r2 + 1, c2=c2 + 1, cf=cf)
        if self._target is not None:
            return self._target.conditional_format(cell_range, options)
