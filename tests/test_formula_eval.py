import math

import pytest

from pm_mcp.output.formula_eval import ExcelError, FormulaSyntaxError, WorkbookEvaluator, parse_formula


def value(formula, cells=None, sheets=("S",)):
    cells = {("S", 1, 1): formula, **(cells or {})}
    return WorkbookEvaluator(cells, list(sheets)).value(("S", 1, 1))


def test_operators_follow_excel_precedence():
    assert value("=-2^2") == 4
    assert value("=2+3*4-1") == 13
    assert value('=(1+2)&"x"') == "3x"
    assert value("=10/4") == 2.5
    assert value("=50%") == 0.5
    assert value("=1<2") is True


def test_references_ranges_and_functions():
    cells = {
        ("S", 2, 1): 1, ("S", 3, 1): 2,
        ("S", 4, 1): "=SUM(A2:A3)+MAX(A2,A3)",
        ("Other sheet", 1, 1): "='S'!$A$4*2",
        ("S", 5, 1): '=IF(AND(A2>0,A3>1),"yes","no")',
    }
    ev = WorkbookEvaluator(cells, ["S", "Other sheet"])
    assert ev.value(("S", 4, 1)) == 5
    assert ev.value(("Other sheet", 1, 1)) == 10
    assert ev.value(("S", 5, 1)) == "yes"
    assert value("=ROUND(2.675,2)") == 2.68
    assert value("=ROUND(-2.5,0)") == -3
    assert value("=ROUNDUP(ROUND(19.17683,9),0)") == 20
    assert value("=NORMSDIST(0)") == 0.5
    assert math.isclose(value("=NORMSINV(0.975)"), 1.959963984540054)
    assert value('="Holgura de "&ROUND(2.5,4)&" semanas"') == "Holgura de 2.5 semanas"
    assert value('="n="&3') == "n=3"


def test_errors_are_reported():
    with pytest.raises(ExcelError, match="DIV/0"):
        value("=1/0")
    with pytest.raises(ExcelError, match="NAME"):
        value("=FOO(1)")
    with pytest.raises(ExcelError, match="REF"):
        value("='Missing'!A1")
    with pytest.raises(ExcelError, match="CIRCULAR"):
        value("=A2", {("S", 2, 1): "=A1"})
    with pytest.raises(FormulaSyntaxError):
        parse_formula("=1+")
    ev = WorkbookEvaluator({("S", 1, 1): "=B7+1"}, ["S"])
    assert ev.value(("S", 1, 1)) == 1 and ev.blank_references == [(("S", 1, 1), ("S", 7, 2))]


def test_text_renders_the_exact_fraction_excel_shows():
    from pm_mcp.output.formula_eval import ExcelError, excel_text

    assert excel_text(10.833333333333334, "#/##") == "65/6"
    assert excel_text(0.6666666666666666, "#/#") == "2/3"
    assert excel_text(0.3611111111111111, "#/##") == "13/36"
    with pytest.raises(ExcelError):
        excel_text(1.5, "0.0000")
