"""Edits made through a filtered view must only touch the rows the user can see.

The sheet below shows sheet rows 2, 5, 6, 8 and 10 when filtered on Show = "y"; rows 3, 4, 7
and 9 are hidden and must never change.  Visual rows: 1 → row 2, 2 → row 5, 3 → row 6,
4 → row 8, 5 → row 10.
"""

import pytest
from conftest import grid

VISIBLE = [2, 5, 6, 8, 10]
HIDDEN = [1, 3, 4, 7, 9]


@pytest.fixture
def fsheet(wb):
    sh = wb.add_sheet("F", record=False)
    rows = [["Show", "Val", "Note"]]
    for r in range(1, 11):
        rows.append(["y" if r in VISIBLE else "n", str(r * 10), f"n{r}"])
    wb.set_range(sh.id, 0, 0, rows)
    wb.set_filter(sh.id, 0, {"include": ["y"]})
    assert sh.nview == 5
    return sh


def column(wb, sh, c, n=10):
    return [r[0] for r in grid(wb, sh, 1, n + 1, c, c + 1)]


def hidden_untouched(wb, sh, before):
    wb.toggle_filter(sh.id, False)
    after = grid(wb, sh, 1, 11, 0, 3)
    for r in HIDDEN:
        assert after[r - 1] == before[r - 1], f"hidden row {r} changed"
    return after


def snapshot(wb, sh):
    wb.toggle_filter(sh.id, False)
    snap = grid(wb, sh, 1, 11, 0, 3)
    wb.toggle_filter(sh.id, True)
    wb.set_filter(sh.id, 0, {"include": ["y"]})
    return snap


def test_view_rows_mapping(wb, fsheet):
    assert wb._view_rows(fsheet, 1, 5) == VISIBLE
    assert wb._view_rows(fsheet, 0, 2) == [0, 2, 5]
    assert wb._view_rows_from(fsheet, 2, 10) == [5, 6, 8, 10]
    assert wb.view_rects(fsheet.id, [[1, 0, 5, 1]]) == [
        [2, 0, 2, 1],
        [5, 0, 6, 1],
        [8, 0, 8, 1],
        [10, 0, 10, 1],
    ]


def test_clear_visible_only(wb, fsheet):
    before = snapshot(wb, fsheet)
    wb.clear_range(fsheet.id, 1, 1, 5, 1, view=True)
    after = hidden_untouched(wb, fsheet, before)
    assert [after[r - 1][1] for r in VISIBLE] == [None] * 5


def test_delete_visible_rows_only(wb, fsheet):
    wb.set_cell(fsheet.id, 1, 4, "=B10")  # a reference into the surviving data must follow it
    wb.delete_rows(fsheet.id, 2, 4, view=True)  # visual rows 2-4 = sheet rows 5, 6, 8
    assert fsheet.nrows == 997  # 1000 rows - the 3 visible ones
    wb.toggle_filter(fsheet.id, False)
    assert column(wb, fsheet, 2, 7) == ["n1", "n2", "n3", "n4", "n7", "n9", "n10"]
    f = next(f for f in wb.formulas.values() if f.sheet_id == fsheet.id and f.row == 1)
    assert f.text == "=B7"
    assert grid(wb, fsheet, 1, 2, 4, 5)[0][0] == 90  # B10 was sheet row 9 ("90"), now B7


def test_paste_text_into_visible_rows(wb, fsheet):
    before = snapshot(wb, fsheet)
    res = wb.set_range(fsheet.id, 1, 2, [["a"], ["b"], ["c"]], view=True)
    assert res["skipped"] == 0
    after = hidden_untouched(wb, fsheet, before)
    assert [after[r - 1][2] for r in (2, 5, 6)] == ["a", "b", "c"]
    assert [after[r - 1][2] for r in (8, 10)] == ["n8", "n10"]


def test_paste_more_lines_than_visible_rows_reports_skipped(wb, fsheet):
    before = snapshot(wb, fsheet)
    res = wb.set_range(fsheet.id, 4, 2, [["a"], ["b"], ["c"]], view=True)
    assert res["skipped"] == 1
    after = hidden_untouched(wb, fsheet, before)
    assert after[7][2] == "a" and after[9][2] == "b"
    assert after[8][2] == "n9" and fsheet.nrows == 1000  # nothing spilled into hidden/new rows


def test_copy_paste_between_filtered_views(wb, fsheet):
    clip = wb.get_range_text(fsheet.id, 1, 1, 3, 1, view=True)
    assert clip["tsv"] == "20\n50\n60"
    before = snapshot(wb, fsheet)
    res = wb.paste_internal(
        fsheet.id,
        {"sheet": fsheet.id, "token": clip["token"], "r1": 1, "c1": 1, "r2": 3, "c2": 1},
        3,
        2,
        view=True,
    )
    assert res["rows"] == 3
    after = hidden_untouched(wb, fsheet, before)
    # visual rows 3-5 = sheet rows 6, 8, 10 receive 20, 50, 60
    assert [after[r - 1][2] for r in (6, 8, 10)] == ["20", "50", "60"]


def test_paste_formulas_shift_to_their_new_rows(wb, fsheet):
    for r in VISIBLE:
        wb.set_cell(fsheet.id, r, 3, f"=B{r + 1}*2")
    clip = wb.get_range_text(fsheet.id, 1, 3, 2, 3, view=True)
    wb.paste_internal(
        fsheet.id,
        {"sheet": fsheet.id, "token": clip["token"], "r1": 1, "c1": 3, "r2": 2, "c2": 3},
        4,
        4,
        view=True,
    )
    texts = {
        f.row: f.text
        for f in wb.formulas.values()
        if f.sheet_id == fsheet.id and f.col_id == fsheet.columns[4].id
    }
    assert texts == {8: "=C9*2", 10: "=C11*2"}


def test_fill_formula_down_visible_rows(wb, fsheet):
    before = snapshot(wb, fsheet)
    wb.set_cell(fsheet.id, 2, 2, "=B3+1")  # visual row 1
    wb.fill(fsheet.id, [1, 2, 1, 2], [1, 2, 5, 2], view=True)
    after = hidden_untouched(wb, fsheet, before)
    # column C still holds text in the hidden rows, so results are stored as text
    assert [after[r - 1][2] for r in VISIBLE] == ["21", "51", "61", "81", "101"]


def test_fill_column_over_large_filter_is_fast(wb):
    import time

    sh = wb.sheet_from_sql(
        "Big",
        "SELECT CASE WHEN range % 3 = 0 THEN 'y' ELSE 'n' END AS show, range AS v FROM range(300000)",
        record=False,
    )
    wb.set_filter(sh.id, 0, {"include": ["y"]})
    wb.set_cell(sh.id, 1, 2, "=B2*2")
    t = time.time()
    wb.fill(sh.id, [1, 2, 1, 2], [1, 2, sh.nview, 2], view=True)
    assert time.time() - t < 20
    f = next(f for f in wb.formulas.values() if f.sheet_id == sh.id and f.kind == "column")
    assert len(f.exceptions) > 150000
    wb.toggle_filter(sh.id, False)
    assert grid(wb, sh, 1, 5, 2, 3) == [[0], [None], [None], [6]]


def test_fill_values_series_visible_rows(wb, fsheet):
    before = snapshot(wb, fsheet)
    wb.fill(fsheet.id, [1, 1, 2, 1], [1, 1, 5, 1], view=True)  # 20, 50 → 80, 110, 140
    after = hidden_untouched(wb, fsheet, before)
    assert [after[r - 1][1] for r in VISIBLE] == [20, 50, 80, 110, 140]


def test_format_and_cleanup_visible_only(wb, fsheet):
    rects = wb.view_rects(fsheet.id, [[1, 2, 5, 2]])
    from pysheets.engine import features as F

    F.set_style(wb, fsheet.id, rects, {"bold": True})
    styled = {r for st in fsheet.styles if st.get("bold") for r in range(st["r1"], st["r2"] + 1)}
    assert styled == set(VISIBLE)
    F.transform_text(wb, fsheet.id, rects, "upper")
    wb.toggle_filter(fsheet.id, False)
    notes = column(wb, fsheet, 2)
    assert [notes[r - 1] for r in VISIBLE] == ["N2", "N5", "N6", "N8", "N10"]
    assert [notes[r - 1] for r in HIDDEN] == ["n1", "n3", "n4", "n7", "n9"]


def test_undo_restores_filtered_edit(wb, fsheet):
    before = snapshot(wb, fsheet)
    wb.set_range(fsheet.id, 1, 1, [["x"]] * 5, view=True)
    wb.undo()
    wb.toggle_filter(fsheet.id, False)
    assert grid(wb, fsheet, 1, 11, 0, 3) == before
