"""Smart (format-tolerant) lookup matching and its diagnostics."""

import os

import pytest
from conftest import grid, val

from pysheets.engine import io as IO
from pysheets.engine.workbook import EngineError


@pytest.fixture
def books(wb):
    """B is a lookup table whose keys are text with format problems; A has numeric/clean keys."""
    b = wb.add_sheet("B", record=False)
    keys = ["'00123", "'456 ", "'1,234", "'ABC-1 ", "'x​y", "'789", "'Exact"]
    wb.set_range(b.id, 0, 0, [["Key", "Val"]] + [[k, f"v{i}"] for i, k in enumerate(keys)])
    a = wb.add_sheet("A", record=False)
    # numbers in a numeric column, text in a text column
    wb.set_range(a.id, 0, 0, [["Num"], ["123"], ["456"], ["1234"], ["789"], ["999"]])
    wb.set_range(a.id, 0, 3, [["Txt"], ["abc-1"], ["xy"], ["exact"], ["nope"]])
    return a, b


def formula(wb, sh, row, col):
    cid = sh.columns[col].id
    return next(f for f in wb.formulas.values() if f.sheet_id == sh.id and f.col_id == cid and f.row == row)


def test_normkey_macro(wb):
    cases = {
        "00123": "123",
        " 456 ": "456",
        "1,234.50": "1234.5",
        "ABC-1 ": "abc-1",
        "x​y": "xy",
        "a  b\tc": "a b c",
        "1E3": "1000",
        "$1,000": "1000",
        "2024-01-05 00:00:00": "2024-01-05",
        "   ": None,
        None: None,
    }
    for raw, want in cases.items():
        assert wb.con.execute("SELECT ps_normkey(?)", [raw]).fetchone()[0] == want, raw


def test_smart_lookup_finds_format_mismatches(wb, books):
    a, b = books
    assert a.columns[0].duck == "DOUBLE" and b.columns[0].duck == "VARCHAR"
    wb.set_cell(a.id, 1, 1, "=VLOOKUP(A2,B!A:B,2,FALSE)", fill="column")
    wb.set_cell(a.id, 1, 2, "=MATCH(A2,B!A:A,0)", fill="column")
    wb.set_cell(a.id, 1, 4, "=XLOOKUP(D2,B!A:A,B!B:B)", fill="column")
    cells = grid(wb, a, 1, 6, 0, 5)
    assert [r[1] for r in cells] == ["v0", "v1", "v2", "v5", None]
    assert [r[2] for r in cells] == [2, 3, 4, 7, None]
    assert [r[4] for r in cells][:4] == ["v3", "v4", "v6", None]

    vl = formula(wb, a, 1, 1)
    # rows 1-4 matched only after normalising (number vs text), row 5 (999) is a clean miss;
    # the remaining rows of the column have blank keys and are not counted as misses
    assert vl.match["fuzzy"] == 4 and vl.match["missing"] == 1 and vl.match["exact"] == 0
    xl = formula(wb, a, 1, 4)
    assert xl.match["exact"] == 1 and xl.match["fuzzy"] == 2 and xl.match["missing"] == 1

    vp = wb.viewport(a.id, 0, 8, 0, 5)
    marked = {(vp["rows"][f[0]], f[1]) for f in vp["flags"] if f[2] == "fmt"}
    assert (1, 1) in marked and (4, 1) in marked and (5, 1) not in marked
    assert (3, 4) not in marked  # "exact" matched "Exact" exactly (case-insensitive)
    assert wb.fuzzy_rows(vl.id) == [1, 2, 3, 4]
    summary = wb.match_summary()
    assert {f["id"] for f in summary["formulas"]} >= {vl.id, xl.id}


def test_exact_match_preferred_over_format_match(wb):
    b = wb.add_sheet("T", record=False)
    wb.set_range(b.id, 0, 0, [["k", "v"], ["'5 ", "spaced"], ["5", "exact"]])
    s = wb.add_sheet("S", record=False)
    wb.set_cell(s.id, 1, 0, "=VLOOKUP(5,T!A:B,2,FALSE)")
    # column A of T is text ("5 " forces text), so 5 (a number) is a format match either way;
    # with a text key the exact row should win even though it comes second
    wb.set_cell(s.id, 2, 0, '=VLOOKUP("5",T!A:B,2,FALSE)')
    assert val(wb, s, 2, 0) == "exact"
    assert formula(wb, s, 2, 0).match["exact"] == 1


def test_strict_mode_reports_instead_of_matching(wb, books):
    a, _ = books
    wb.set_cell(a.id, 1, 1, "=VLOOKUP(A2,B!A:B,2,FALSE)", fill="column")
    wb.set_match_mode("strict")
    assert [r[0] for r in grid(wb, a, 1, 6, 1, 2)] == [None] * 5
    f = formula(wb, a, 1, 1)
    assert f.match["mode"] == "strict" and f.match["fuzzy"] == 4 and f.match["missing"] == 1
    vp = wb.viewport(a.id, 0, 8, 0, 3)
    assert sum(1 for x in vp["flags"] if x[2] == "fmt") == 4
    wb.set_match_mode("smart")
    assert [r[0] for r in grid(wb, a, 1, 6, 1, 2)] == ["v0", "v1", "v2", "v5", None]
    with pytest.raises(EngineError):
        wb.set_match_mode("bogus")


def test_countif_and_filter_are_format_tolerant(wb, books):
    a, b = books
    wb.set_cell(a.id, 1, 1, "=COUNTIF(B!A:A,A2)", fill="column")
    assert [r[0] for r in grid(wb, a, 1, 6, 1, 2)] == [1, 1, 1, 1, 0]
    wb.set_cell(a.id, 1, 6, '=COUNTIF(B!A:A,"abc-1")')
    assert val(wb, a, 1, 6) == 1
    s = wb.add_sheet("F", record=False)
    wb.set_cell(s.id, 1, 0, "=FILTER(B!B2:B8,B!A2:A8=123)")
    assert val(wb, s, 1, 0) == "v0"
    f = formula(wb, s, 1, 0)
    assert f.match and f.match.get("filter_fuzzy") == 1


def test_cleankey_function(wb):
    s = wb.add_sheet("K", record=False)
    wb.set_cell(s.id, 1, 0, '=CLEANKEY(" 00042 ")')
    wb.set_cell(s.id, 2, 0, '=CLEANKEY("Foo  Bar")')
    assert val(wb, s, 1, 0) == "42"
    assert val(wb, s, 2, 0) == "foo bar"


def test_match_mode_and_markers_survive_save(wb, books, tmp_path):
    a, _ = books
    wb.set_cell(a.id, 1, 1, "=VLOOKUP(A2,B!A:B,2,FALSE)", fill="column")
    wb.set_match_mode("strict")
    path = os.path.join(tmp_path, "m.pysheets")
    IO.save_workbook(wb, path)
    IO.new_workbook(wb)
    assert wb.match_mode == "smart"
    IO.open_workbook(wb, path)
    assert wb.match_mode == "strict"
    a2 = wb.get_sheet("A")
    vp = wb.viewport(a2.id, 0, 8, 0, 3)
    assert sum(1 for x in vp["flags"] if x[2] == "fmt") == 4


def test_match_report_explains_mismatches(wb, books):
    from pysheets.engine import features as F

    a, b = books
    rep = F.match_report(wb, a.id, 0, b.id, 0)
    assert rep["total"] == 5 and rep["exact"] == 0 and rep["fuzzy"] == 4 and rep["missing"] == 1
    assert rep["reasons"]["type"] == 4
    assert (
        rep["reasons"]["zeros"] == 1 and rep["reasons"]["spaces"] == 1 and rep["reasons"]["separators"] == 1
    )
    assert rep["missing_samples"] == [{"row": 5, "value": "999"}]
    assert rep["a"]["type"] == "Number" and rep["b"]["type"] == "Text"
    assert rep["b"]["numbers_as_text"] == 4 and rep["suggest"] == "text"
    s0 = rep["fuzzy_samples"][0]
    assert s0["row"] == 1 and s0["b_value"] == "00123" and s0["b_row"] == 1

    rep2 = F.match_report(wb, a.id, 3, b.id, 0)
    assert rep2["exact"] == 1 and rep2["fuzzy"] == 2 and rep2["missing"] == 1
    assert rep2["reasons"]["invisible"] == 2 and rep2["reasons"]["type"] == 0


def test_normalize_columns_makes_matches_exact(wb):
    from pysheets.engine import features as F

    b = wb.add_sheet("B", record=False)
    wb.set_range(b.id, 0, 0, [["Key", "Val"], ["'00123", "x"], ["' 456 ", "y"], ["'1,234", "z"]])
    a = wb.add_sheet("A", record=False)
    wb.set_range(a.id, 0, 0, [["Num"], ["123"], ["456"], ["1234"]])
    wb.set_cell(a.id, 1, 1, "=VLOOKUP(A2,B!A:B,2,FALSE)", fill="column")
    f = formula(wb, a, 1, 1)
    assert f.match["fuzzy"] == 3
    res = F.normalize_columns(
        wb, [{"sheet": b.id, "col": 0}, {"sheet": a.id, "col": 0}], "auto", keep_original=True
    )
    assert res["type"] == "number"
    assert b.columns[0].duck == "DOUBLE" and b.columns[2].name == "Key (original)"
    assert [r[0] for r in grid(wb, b, 1, 4, 2, 3)] == ["00123", " 456 ", "1,234"]
    assert [r[0] for r in grid(wb, b, 1, 4, 0, 1)] == [123, 456, 1234]
    # the lookup now matches exactly; the copy went to a free column so references didn't move
    f = formula(wb, a, 1, 1)
    assert f.text == "=VLOOKUP(A2,B!A:B,2,FALSE)" and f.match["exact"] == 3 and f.match["fuzzy"] == 0
    assert a.columns[1].name == ""  # A was already numeric: nothing changed, no copy
    assert [r[0] for r in grid(wb, a, 1, 4, 1, 2)] == ["x", "y", "z"]
    wb.undo()
    assert wb.get_sheet("B").columns[0].duck == "VARCHAR"


def test_normalize_refuses_lossy_number_conversion(wb):
    from pysheets.engine import features as F

    b = wb.add_sheet("B", record=False)
    wb.set_range(b.id, 0, 0, [["Key"], ["'001"], ["abc"]])
    with pytest.raises(EngineError, match="aren't numbers"):
        F.normalize_columns(wb, [{"sheet": b.id, "col": 0}], "number")
    res = F.normalize_columns(wb, [{"sheet": b.id, "col": 0}], "auto")
    assert res["type"] == "text"


def test_normalize_as_text_keeps_zeros_drops_separators(wb):
    from pysheets.engine import features as F

    b = wb.add_sheet("B", record=False)
    wb.set_range(b.id, 0, 0, [["Key"], ["'00123"], ["'1,234"], ["' ABC-1 "], ["'$1,000"]])
    F.normalize_columns(wb, [{"sheet": b.id, "col": 0}], "text")
    assert [r[0] for r in grid(wb, b, 1, 5, 0, 1)] == ["00123", "1234", "ABC-1", "1000"]
