"""Formula semantics, evaluated through the real DuckDB engine."""

import datetime as dt

import pytest
from conftest import val


@pytest.fixture
def calc(wb, sales):
    sh = wb.add_sheet("Calc", record=False)

    def run(formula):
        wb.set_cell(sh.id, 1, 0, formula)
        f = next(
            (
                f
                for f in wb.formulas.values()
                if f.sheet_id == sh.id and f.row == 1 and f.col_id == sh.columns[0].id
            ),
            None,
        )
        if f is not None and f.error:
            return f.error
        return val(wb, sh, 1, 0)

    run.sheet = sh
    return run


@pytest.mark.parametrize(
    "formula,expected",
    [
        ("=1+2*3", 7),
        ("=2^3^2", 64),  # left-assoc like Sheets
        ("=-2^2", 4),
        ("=10%", 0.1),
        ('="a"&1&TRUE', "a1TRUE"),
        ('=IF(1>2,"x","y")', "y"),
        ('=IFERROR(1/0,"div")', "div"),
        ("=ROUND(2.5)", 3),
        ("=ROUND(-2.5)", -3),
        ("=ROUNDUP(1.21,1)", 1.3),
        ("=MOD(-7,3)", 2),
        ('=TRIM("  a   b  ")', "a b"),
        ('=PROPER("hello wORLD")', "Hello World"),
        ('=LEFT("spreadsheet",6)&"|"&RIGHT("abc",2)&"|"&MID("abcdef",2,3)', "spread|bc|bcd"),
        ('=SUBSTITUTE("a-b-c","-","+")', "a+b+c"),
        ('=SUBSTITUTE("a-b-c","-","+",2)', "a-b+c"),
        ('=LEN("abc")', 3),
        ('=FIND("c","abcabc")', 3),
        ('=SEARCH("C","abcabc",4)', 6),
        ('=REGEXEXTRACT("order-1234","[0-9]+")', "1234"),
        ('=REGEXREPLACE("a1b2","[0-9]","#")', "a#b#"),
        ('=REGEXMATCH("abc","^a")', True),
        ('=TEXT(1234.5,"#,##0.00")', "1,234.50"),
        ('=TEXT(0.256,"0.0%")', "25.6%"),
        ('=VALUE("1,234.5")', 1234.5),
        ("=DATE(2024,2,30)", "2024-03-01"),
        ("=EOMONTH(DATE(2024,1,15),1)", "2024-02-29"),
        ("=YEAR(DATE(2024,5,6))+MONTH(DATE(2024,5,6))", 2029),
        ('=DATEDIF(DATE(2020,1,15),DATE(2024,1,14),"Y")', 3),
        ("=AND(TRUE,1>0)", True),
        ("=OR(FALSE,0)", False),
        ('=SWITCH(2,1,"one",2,"two","many")', "two"),
        ('=CHOOSE(3,"a","b","c")', "c"),
        ('=IFS(1>2,"a",2>1,"b")', "b"),
        ("=ISBLANK(Z99)", True),
        ("=SUMPRODUCT({1,2,3},{4,5,6})", 32),
        ('=TEXTJOIN(",",TRUE,{"a","","b"})', "a,b"),
    ],
)
def test_scalar(calc, formula, expected):
    got = calc(formula)
    if isinstance(expected, float):
        assert got == pytest.approx(expected)
    else:
        assert got == expected or (isinstance(got, float) and got == pytest.approx(expected))


@pytest.mark.parametrize(
    "formula,expected",
    [
        ("=SUM(Sales!E:E)", 1999),  # qty = range%3+1 over 1000 rows
        ("=COUNT(Sales!A:A)", 1000),
        ("=COUNTA(Sales!A:A)", 1001),  # header counts as text
        ("=AVERAGE(Sales!E2:E4)", 2),
        ("=MAX(Sales!D:D)", 49.5),
        ('=COUNTIF(Sales!B:B,"North")', 250),
        ('=COUNTIF(Sales!B:B,"no*")', 250),
        ('=COUNTIF(Sales!D:D,">40")', 190),
        ('=COUNTIFS(Sales!B:B,"North",Sales!E:E,1)', 84),
        ('=SUMIF(Sales!B:B,"East",Sales!E:E)', 501),
        ('=SUMIFS(Sales!E:E,Sales!B:B,"East",Sales!C:C,"item 2")', 101),
        ('=AVERAGEIF(Sales!B:B,"West",Sales!E:E)', pytest.approx(1.996, abs=1e-3)),
        ("=COUNTUNIQUE(Sales!B:B)", 4),
        ("=VLOOKUP(42,Sales!A:D,4,FALSE)", 43.5),
        ('=VLOOKUP("item 3",Sales!C:E,3,FALSE)', 1),
        ("=INDEX(Sales!D:D,MATCH(42,Sales!A:A,0))", 43.5),
        ('=MATCH("Region",Sales!1:1,0)', 2),
        ('=MATCH("Price",Sales!A1:E1,0)', 4),
        ("=XLOOKUP(10,Sales!A:A,Sales!B:B)", "South"),
        ('=XLOOKUP(99999,Sales!A:A,Sales!B:B,"none")', "none"),
        ("=INDEX(Sales!A:E,3,2)", "South"),
        ("=INDEX(Sales!B:B,1)", "Region"),
        ("=ROWS(Sales!A2:A11)", 10),
        ("=LARGE(Sales!D:D,2)", 49.5),
        ("=MEDIAN(Sales!E2:E1001)", 2),
    ],
)
def test_ranges(calc, formula, expected):
    assert calc(formula) == expected


def test_unknown_function(calc):
    assert calc("=NOPE(1)").startswith("#NAME?")


def test_bad_sheet(calc):
    assert calc("=Missing!A1").startswith("#REF!")


def test_parse_error_is_stored(calc):
    assert calc("=1+").startswith("#ERROR!")


def test_autoclose(wb, calc):
    calc("=SUM(1,2")
    f = next(f for f in wb.formulas.values() if f.sheet_id == calc.sheet.id)
    assert f.text == "=SUM(1,2)"


def test_division_by_zero_is_blank_not_crash(calc):
    assert calc("=1/0") is None


def test_today_is_date(calc):
    assert calc("=TODAY()") == dt.date.today().isoformat()
