"""A small, whitelisted Excel formula evaluator.

It exists so the workbook can be validated *independently of the library that wrote it*: every formula is
parsed, every reference resolved and every value recomputed from the cells read back with openpyxl, then
compared with the cached value and with the engine's value.

Supported grammar (Excel precedence, lowest first): comparison (= <> < > <= >=), concatenation (&),
additive (+ −), multiplicative (* /), exponent (^), unary sign, postfix percent; literals (numbers,
"strings", TRUE/FALSE, error tokens), cell references (optionally sheet-qualified, $-absolute), ranges and
calls to the functions in :data:`FUNCTIONS`.
"""

from __future__ import annotations

import math
import re
import sys
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal
from fractions import Fraction
from typing import Any

from pm_mcp.engine.probability import normal_cdf, normal_ppf

CellKey = tuple[str, int, int]


class ExcelError(Exception):
    """An Excel error value (#DIV/0!, #NAME?, #REF!, #VALUE!, #NUM!, #N/A) or a circular reference."""

    def __init__(self, code: str, detail: str = ""):
        self.code = code
        super().__init__(f"{code} {detail}".strip())


class FormulaSyntaxError(ValueError):
    pass


ERROR_TOKENS = ("#DIV/0!", "#NAME?", "#REF!", "#VALUE!", "#NUM!", "#N/A", "#NULL!")
ERROR_TOKEN_RE = re.compile("|".join(re.escape(t) for t in ERROR_TOKENS))

# ---------------------------------------------------------------------------------------------- tokenizer

_TOKEN_RE = re.compile(
    r"""
    (?P<ws>\s+)
  | (?P<string>"(?:[^"]|"")*")
  | (?P<error>\#DIV/0!|\#NAME\?|\#REF!|\#VALUE!|\#NUM!|\#N/A|\#NULL!)
  | (?P<ref>(?:(?:'(?:[^']|'')+'|[A-Za-z_][A-Za-z0-9_.]*)!)?\$?[A-Za-z]{1,3}\$?[0-9]+(?::\$?[A-Za-z]{1,3}\$?[0-9]+)?(?![A-Za-z0-9_(]))
  | (?P<number>(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][+-]?[0-9]+)?)
  | (?P<func>[A-Za-z_][A-Za-z0-9_.]*(?=\())
  | (?P<bool>TRUE|FALSE)(?![A-Za-z0-9_])
  | (?P<op><=|>=|<>|[-+*/^&=<>%(),:])
  """,
    re.VERBOSE,
)


@dataclass
class Token:
    kind: str
    text: str


def tokenize(formula: str) -> list[Token]:
    text = formula[1:] if formula.startswith("=") else formula
    tokens: list[Token] = []
    pos = 0
    while pos < len(text):
        m = _TOKEN_RE.match(text, pos)
        if not m:
            raise FormulaSyntaxError(f"unexpected character {text[pos]!r} at {pos} in {formula!r}")
        pos = m.end()
        kind = m.lastgroup
        if kind == "ws":
            continue
        tokens.append(Token(kind, m.group(kind)))
    return tokens


# ---------------------------------------------------------------------------------------------- AST

@dataclass
class Node:
    kind: str  # num, str, bool, error, ref, range, func, unary, binary, percent
    value: Any = None
    children: list[Node] = field(default_factory=list)


_A1_RE = re.compile(r"^\$?([A-Za-z]{1,3})\$?([0-9]+)$")


def _col_number(letters: str) -> int:
    n = 0
    for ch in letters.upper():
        n = n * 26 + (ord(ch) - 64)
    return n


def split_reference(text: str) -> tuple[str | None, list[tuple[int, int]]]:
    """'Sheet'!$A$1:B2 → ('Sheet', [(1, 1), (2, 2)]) with (row, column) pairs, 1-based."""
    sheet = None
    if "!" in text:
        sheet_part, text = text.rsplit("!", 1)
        if sheet_part.startswith("'"):
            sheet_part = sheet_part[1:-1].replace("''", "'")
        sheet = sheet_part
    cells = []
    for part in text.split(":"):
        m = _A1_RE.match(part)
        if not m:
            raise FormulaSyntaxError(f"bad reference {text!r}")
        cells.append((int(m.group(2)), _col_number(m.group(1))))
    return sheet, cells


class _Parser:
    def __init__(self, tokens: list[Token], source: str):
        self.tokens = tokens
        self.i = 0
        self.source = source

    def peek(self) -> Token | None:
        return self.tokens[self.i] if self.i < len(self.tokens) else None

    def take(self) -> Token:
        tok = self.peek()
        if tok is None:
            raise FormulaSyntaxError(f"unexpected end of formula {self.source!r}")
        self.i += 1
        return tok

    def expect(self, text: str) -> None:
        tok = self.take()
        if tok.text != text:
            raise FormulaSyntaxError(f"expected {text!r}, got {tok.text!r} in {self.source!r}")

    def parse(self) -> Node:
        node = self.comparison()
        if self.peek() is not None:
            raise FormulaSyntaxError(f"unexpected {self.peek().text!r} in {self.source!r}")
        return node

    def _binary(self, ops: tuple[str, ...], operand: Callable[[], Node]) -> Node:
        node = operand()
        while (tok := self.peek()) is not None and tok.kind == "op" and tok.text in ops:
            self.take()
            node = Node("binary", tok.text, [node, operand()])
        return node

    def comparison(self) -> Node:
        return self._binary(("=", "<>", "<", ">", "<=", ">="), self.concat)

    def concat(self) -> Node:
        return self._binary(("&",), self.additive)

    def additive(self) -> Node:
        return self._binary(("+", "-"), self.multiplicative)

    def multiplicative(self) -> Node:
        return self._binary(("*", "/"), self.power)

    def power(self) -> Node:
        return self._binary(("^",), self.unary)

    def unary(self) -> Node:
        tok = self.peek()
        if tok is not None and tok.kind == "op" and tok.text in ("-", "+"):
            self.take()
            return Node("unary", tok.text, [self.unary()])
        return self.postfix()

    def postfix(self) -> Node:
        node = self.primary()
        while (tok := self.peek()) is not None and tok.kind == "op" and tok.text == "%":
            self.take()
            node = Node("percent", None, [node])
        return node

    def primary(self) -> Node:
        tok = self.take()
        if tok.kind == "number":
            return Node("num", float(tok.text))
        if tok.kind == "string":
            return Node("str", tok.text[1:-1].replace('""', '"'))
        if tok.kind == "bool":
            return Node("bool", tok.text == "TRUE")
        if tok.kind == "error":
            return Node("error", tok.text)
        if tok.kind == "ref":
            sheet, cells = split_reference(tok.text)
            return Node("range" if len(cells) == 2 else "ref", (sheet, cells))
        if tok.kind == "func":
            name = tok.text.upper()
            self.expect("(")
            args: list[Node] = []
            if not (self.peek() is not None and self.peek().text == ")"):
                args.append(self.comparison())
                while self.peek() is not None and self.peek().text == ",":
                    self.take()
                    args.append(self.comparison())
            self.expect(")")
            return Node("func", name, args)
        if tok.text == "(":
            node = self.comparison()
            self.expect(")")
            return node
        raise FormulaSyntaxError(f"unexpected {tok.text!r} in {self.source!r}")


def parse_formula(formula: str) -> Node:
    tokens = tokenize(formula)
    if not tokens:
        raise FormulaSyntaxError(f"empty formula {formula!r}")
    return _Parser(tokens, formula).parse()


def iter_nodes(node: Node) -> Iterator[Node]:
    yield node
    for child in node.children:
        yield from iter_nodes(child)


# ---------------------------------------------------------------------------------------------- values

class _Blank:
    """An empty cell: 0 in arithmetic, "" in text."""

    def __repr__(self) -> str:
        return "BLANK"


BLANK = _Blank()


def _round(value: float, digits: int, mode=ROUND_HALF_UP) -> float:
    dec = Decimal(repr(float(value)))
    quantum = Decimal(1).scaleb(-int(digits))
    if mode == "up":  # away from zero
        mode = ROUND_CEILING if dec >= 0 else ROUND_FLOOR
    elif mode == "down":  # toward zero
        mode = ROUND_FLOOR if dec >= 0 else ROUND_CEILING
    result = float(dec.quantize(quantum, rounding=mode))
    return result + 0.0  # normalise -0.0


def to_number(value: Any) -> float:
    if value is BLANK or value is None:
        return 0.0
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            raise ExcelError("#VALUE!", f"text {value!r} used as a number") from None
    raise ExcelError("#VALUE!", repr(value))


def to_text(value: Any) -> str:
    if value is BLANK or value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        number = float(value)
        if number.is_integer() and abs(number) < 1e15:
            return str(int(number))
        return f"{number:.15g}"
    return str(value)


def to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.upper() in ("TRUE", "FALSE"):
        return value.upper() == "TRUE"
    return to_number(value) != 0


def excel_text(value: float, number_format: str) -> str:
    """TEXT(value, format) for the fraction formats used in the workbook ('#/#', '#/##', '#/###')."""
    m = re.fullmatch(r"#/(#{1,3})", number_format)
    if not m:
        raise ExcelError("#VALUE!", f"unsupported TEXT format {number_format!r}")
    max_den = 10 ** len(m.group(1)) - 1
    frac = Fraction(repr(float(value))).limit_denominator(max_den)
    return f"{frac.numerator}/{frac.denominator}"


def _flatten(values: list[Any]) -> list[Any]:
    flat: list[Any] = []
    for v in values:
        if isinstance(v, list):
            flat.extend(x for x in v if x is not BLANK and x is not None and not isinstance(x, str))
        else:
            flat.append(v)
    return flat


def _numbers(values: list[Any]) -> list[float]:
    return [to_number(v) for v in _flatten(values)]


def _sqrt(x: float) -> float:
    if x < 0:
        raise ExcelError("#NUM!", "square root of a negative number")
    return math.sqrt(x)


def _normsinv(p: float) -> float:
    if not 0 < p < 1:
        raise ExcelError("#NUM!", f"NORMSINV({p})")
    return normal_ppf(p)


FUNCTIONS: dict[str, Callable[..., Any]] = {
    "SUM": lambda *a: math.fsum(_numbers(list(a))),
    "MAX": lambda *a: max(_numbers(list(a)), default=0.0),
    "MIN": lambda *a: min(_numbers(list(a)), default=0.0),
    "ABS": lambda x: abs(to_number(x)),
    "SQRT": lambda x: _sqrt(to_number(x)),
    "ROUND": lambda x, d=0: _round(to_number(x), int(to_number(d))),
    "ROUNDUP": lambda x, d=0: _round(to_number(x), int(to_number(d)), "up"),
    "ROUNDDOWN": lambda x, d=0: _round(to_number(x), int(to_number(d)), "down"),
    "NORMSDIST": lambda z: normal_cdf(to_number(z)),
    "NORMSINV": lambda p: _normsinv(to_number(p)),
    "AND": lambda *a: all(to_bool(v) for v in _flatten(list(a))),
    "OR": lambda *a: any(to_bool(v) for v in _flatten(list(a))),
    "NOT": lambda x: not to_bool(x),
    "TEXT": lambda x, f: excel_text(to_number(x), to_text(f)),
    "COUNT": lambda *a: float(len(_numbers(list(a)))),
}
LAZY_FUNCTIONS = {"IF", "IFERROR"}
SUPPORTED_FUNCTIONS = frozenset(FUNCTIONS) | LAZY_FUNCTIONS


# ---------------------------------------------------------------------------------------------- evaluator

class WorkbookEvaluator:
    """Evaluate cells of a workbook given as ``{(sheet, row, column): raw value or '=formula'}``."""

    def __init__(self, cells: dict[CellKey, Any], sheets: list[str]):
        self.cells = cells
        self.sheets = list(sheets)
        self._cache: dict[CellKey, Any] = {}
        self._parsed: dict[str, Node] = {}
        self._active: set[CellKey] = set()
        self.blank_references: list[tuple[CellKey, CellKey]] = []

    def parsed(self, formula: str) -> Node:
        node = self._parsed.get(formula)
        if node is None:
            node = self._parsed[formula] = parse_formula(formula)
        return node

    def value(self, key: CellKey) -> Any:
        limit = sys.getrecursionlimit()
        sys.setrecursionlimit(max(limit, 100_000))
        try:
            result = self._value(key)
        finally:
            sys.setrecursionlimit(limit)
        return 0.0 if result is BLANK else result

    def _value(self, key: CellKey) -> Any:
        if key in self._cache:
            cached = self._cache[key]
            if isinstance(cached, ExcelError):
                raise cached
            return cached
        raw = self.cells.get(key)
        if not (isinstance(raw, str) and raw.startswith("=")):
            return BLANK if raw is None or raw == "" else raw
        if key in self._active:
            raise ExcelError("#CIRCULAR", f"circular reference through {key[0]}!R{key[1]}C{key[2]}")
        self._active.add(key)
        try:
            result = self._eval(self.parsed(raw), key)
            if isinstance(result, list):
                raise ExcelError("#VALUE!", "a range cannot be a cell value")
        except ExcelError as exc:
            self._cache[key] = exc
            raise
        finally:
            self._active.discard(key)
        self._cache[key] = result
        return result

    def _sheet(self, sheet: str | None, here: CellKey) -> str:
        if sheet is None:
            return here[0]
        if sheet not in self.sheets:
            raise ExcelError("#REF!", f"unknown sheet {sheet!r}")
        return sheet

    def _eval(self, node: Node, here: CellKey) -> Any:
        kind = node.kind
        if kind in ("num", "str", "bool"):
            return node.value
        if kind == "error":
            raise ExcelError(node.value, "error literal in formula")
        if kind == "ref":
            sheet_name, [(row, col)] = node.value
            target = (self._sheet(sheet_name, here), row, col)
            result = self._value(target)
            if result is BLANK:
                self.blank_references.append((here, target))
            return result
        if kind == "range":
            sheet_name, [(r1, c1), (r2, c2)] = node.value
            sheet = self._sheet(sheet_name, here)
            return [self._value((sheet, r, c)) for r in range(min(r1, r2), max(r1, r2) + 1)
                    for c in range(min(c1, c2), max(c1, c2) + 1)]
        if kind == "unary":
            operand = to_number(self._eval(node.children[0], here))
            return -operand if node.value == "-" else operand
        if kind == "percent":
            return to_number(self._eval(node.children[0], here)) / 100
        if kind == "binary":
            return self._binary(node.value, self._eval(node.children[0], here), self._eval(node.children[1], here))
        if kind == "func":
            return self._call(node, here)
        raise ExcelError("#VALUE!", f"unknown node {kind}")

    def _call(self, node: Node, here: CellKey) -> Any:
        name, args = node.value, node.children
        if name == "IF":
            if not 1 <= len(args) <= 3:
                raise ExcelError("#VALUE!", "IF takes 1 to 3 arguments")
            if to_bool(self._eval(args[0], here)):
                return self._eval(args[1], here) if len(args) > 1 else True
            return self._eval(args[2], here) if len(args) > 2 else False
        if name == "IFERROR":
            try:
                return self._eval(args[0], here)
            except ExcelError as exc:
                if exc.code == "#CIRCULAR":
                    raise
                return self._eval(args[1], here)
        fn = FUNCTIONS.get(name)
        if fn is None:
            raise ExcelError("#NAME?", f"unknown function {name}")
        values = [self._eval(a, here) for a in args]
        try:
            return fn(*values)
        except TypeError as exc:
            raise ExcelError("#VALUE!", f"{name}: {exc}") from None

    @staticmethod
    def _binary(op: str, left: Any, right: Any) -> Any:
        if op == "&":
            return to_text(left) + to_text(right)
        if op in ("+", "-", "*", "/", "^"):
            a, b = to_number(left), to_number(right)
            if op == "+":
                return a + b
            if op == "-":
                return a - b
            if op == "*":
                return a * b
            if op == "/":
                if b == 0:
                    raise ExcelError("#DIV/0!", "division by zero")
                return a / b
            try:
                return float(a ** b)
            except (OverflowError, ZeroDivisionError, ValueError):
                raise ExcelError("#NUM!", f"{a}^{b}") from None
        # comparisons: numbers < text < booleans, text case-insensitive
        def rank(v: Any) -> tuple[int, Any]:
            if v is BLANK or v is None:
                return (0, 0.0)
            if isinstance(v, bool):
                return (2, v)
            if isinstance(v, (int, float)):
                return (0, float(v))
            return (1, str(v).lower())

        if left is BLANK and isinstance(right, str):
            left = ""
        if right is BLANK and isinstance(left, str):
            right = ""
        a, b = rank(left), rank(right)
        return {"=": a == b, "<>": a != b, "<": a < b, ">": a > b, "<=": a <= b, ">=": a >= b}[op]
