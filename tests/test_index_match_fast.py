"""INDEX(…, MATCH(…, 0)) uses a keyed lookup: same answers as the general form, much faster."""

import time

import pytest

from pysheets.engine.workbook import Workbook


@pytest.fixture
def wb():
    w = Workbook()
    yield w
    w.close()


def _cells(wb, sh, col, rows):
    return [wb.get_cell(sh.id, r, col)["value"] for r in rows]


def _both(wb, sh, fast_col, slow_col, formula, rows, fill=None):
    """Write the formula and the same formula with MATCH(...)+0 (general path); compare."""
    slow = formula.replace(", 0),", ", 0)+0,") if ", 0)," in formula else formula.replace(", 0))", ", 0)+0)")
    assert slow != formula
    for col, f in ((fast_col, formula), (slow_col, slow)):
        if fill:
            wb.set_cell(sh.id, rows[0], col, f, fill="column")
        else:
            for r in rows:
                wb.set_cell(sh.id, r, col, f.replace("{r}", str(r + 1)))
    return _cells(wb, sh, fast_col, rows), _cells(wb, sh, slow_col, rows)


def test_same_results_as_general_form(wb):
    data = wb.add_sheet("Data", record=False)
    wb.set_range(
        data.id,
        0,
        0,
        [
            ["code", "name", "qty"],
            ["A1", "alpha", 1],
            ["00123", "padded", 2],
            ["B2", "beta", 3],
            ["A1", "alpha again (duplicate)", 4],
            ["dup ", "trailing space", 5],
            [42, "number key", 6],
        ],
    )
    main = wb.add_sheet("Main", record=False)
    keys = ["A1", "123", "B2", "missing", "dup", "42", "code", "b2"]
    wb.set_range(main.id, 0, 0, [["key"], *[[k] for k in keys]])
    rows = list(range(1, len(keys) + 1))
    for formula in [
        "=INDEX(Data!B:B, MATCH(A{r}, Data!A:A, 0))",
        "=INDEX(Data!A:C, MATCH(A{r}, Data!A:A, 0), 3)",
        '=IFNA(INDEX(Data!B:B, MATCH(A{r}, Data!A:A, 0)), "none")',
        "=INDEX(Data!B2:B7, MATCH(A{r}, Data!A2:A7, 0))",
    ]:
        fast, slow = _both(wb, main, 4, 5, formula, rows)
        assert fast == slow, (formula, fast, slow)
    # spot checks: first duplicate wins, format-tolerant keys, header row = position 1
    fast, _ = _both(wb, main, 4, 5, "=INDEX(Data!B:B, MATCH(A{r}, Data!A:A, 0))", rows)
    assert fast[0] == "alpha" and fast[1] == "padded" and fast[3] is None and fast[6] == "name"


def test_column_formula_matches_and_is_fast(wb):
    data = wb.add_sheet("Data", record=False)
    n = 20_000
    wb.set_range(data.id, 0, 0, [["k", "v"], *[[i * 3, f"v{i}"] for i in range(2000)]])
    main = wb.add_sheet("Main", record=False)
    wb.set_range(main.id, 0, 0, [["key"], *[[i] for i in range(n)]])
    t0 = time.perf_counter()
    wb.set_cell(main.id, 1, 1, "=INDEX(Data!B:B, MATCH(A2, Data!A:A, 0))", fill="column")
    took = time.perf_counter() - t0
    wb.set_cell(main.id, 1, 2, "=INDEX(Data!B:B, MATCH(A2, Data!A:A, 0)+0)", fill="column")
    rows = [1, 2, 4, 7, 301, 5998, 5999, 6001, n]
    assert _cells(wb, main, 1, rows) == _cells(wb, main, 2, rows)
    assert _cells(wb, main, 1, [1, 4, 7]) == ["v0", "v1", "v2"]
    assert took < 5, f"column INDEX/MATCH took {took:.1f}s"


def test_mismatched_spans_keep_general_behaviour(wb):
    data = wb.add_sheet("Data", record=False)
    wb.set_range(data.id, 0, 0, [["k", "v"], ["a", "x"], ["b", "y"], ["c", "z"]])
    main = wb.add_sheet("Main", record=False)
    wb.set_range(main.id, 0, 0, [["key"], ["b"]])
    # B:B includes the header row, A2:A does not: positions are offset by one (as in Sheets)
    wb.set_cell(main.id, 1, 1, "=INDEX(Data!B:B, MATCH(A2, Data!A2:A, 0))")
    assert wb.get_cell(main.id, 1, 1)["value"] == "x"


@pytest.mark.parametrize("mode", ["smart", "strict"])
def test_strict_mode_and_ranges_on_different_tabs(wb, mode):
    wb.match_mode = mode
    keys = wb.add_sheet("Keys", record=False)
    vals = wb.add_sheet("Vals", record=False)
    wb.set_range(keys.id, 0, 0, [["k"], ["A1"], ["00123"], ["x "], [7]])
    wb.set_range(vals.id, 0, 0, [["v"], ["first"], ["second"], ["third"], ["fourth"]])
    main = wb.add_sheet("Main", record=False)
    probe = ["A1", "123", "x", "7", "k", "nope"]
    wb.set_range(main.id, 0, 0, [["key"], *[[p] for p in probe]])
    rows = list(range(1, len(probe) + 1))
    fast, slow = _both(wb, main, 3, 4, "=INDEX(Vals!A:A, MATCH(A{r}, Keys!A:A, 0))", rows)
    assert fast == slow
    assert fast[0] == "first" and fast[4] == "v" and fast[5] is None


def test_approximate_match_types(wb):
    data = wb.add_sheet("Data", record=False)
    wb.set_range(
        data.id, 0, 0, [["min", "band"], [0, "low"], [10, "mid"], [10, "mid-2"], [50, "high"], [100, "top"]]
    )
    main = wb.add_sheet("Main", record=False)
    probe = [-5, 0, 9, 10, 49, 50, 99, 1000]
    wb.set_range(main.id, 0, 0, [["v"], *[[p] for p in probe]])
    rows = list(range(1, len(probe) + 1))
    for f in [
        "=INDEX(Data!B:B, MATCH(A{r}, Data!A:A, 1))",
        "=INDEX(Data!B2:B6, MATCH(A{r}, Data!A2:A6, -1))",
    ]:
        fast = slow = None
        for col, g in ((3, f), (4, f.replace("))", ")+0)"))):
            for r in rows:
                wb.set_cell(main.id, r, col, g.replace("{r}", str(r + 1)))
        fast, slow = _cells(wb, main, 3, rows), _cells(wb, main, 4, rows)
        assert fast == slow, (f, fast, slow)
