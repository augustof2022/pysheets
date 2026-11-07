"""Workbook behaviour: typed edits, spills, column formulas, recalc, structure, undo."""

import pytest
from conftest import grid, val

from pysheets.engine import features as F
from pysheets.engine.workbook import EngineError


def test_import_types_and_header(wb, sales):
    assert sales.nrows == 1000
    assert [c.name for c in sales.columns] == ["ID", "Region", "Item", "Price", "Qty"]
    assert [c.typ for c in sales.columns] == ["num", "str", "str", "num", "num"]
    assert grid(wb, sales, 0, 2, 0, 2) == [["ID", "Region"], [1, "North"]]


def test_blank_sheet_retypes_columns(wb):
    sh = wb.add_sheet("S")
    wb.set_cell(sh.id, 1, 0, "12")
    wb.set_cell(sh.id, 2, 0, "3.5")
    assert sh.columns[0].duck == "DOUBLE"
    wb.set_cell(sh.id, 3, 0, "hello")
    assert sh.columns[0].duck == "VARCHAR"
    assert grid(wb, sh, 1, 4, 0, 1) == [["12"], ["3.5"], ["hello"]]


def test_header_row_edit_renames(wb, sales):
    wb.set_cell(sales.id, 0, 1, "Area")
    assert sales.columns[1].name == "Area"


def test_dependency_recalc_across_sheets(wb, sales):
    calc = wb.add_sheet("Calc")
    wb.set_cell(calc.id, 1, 0, "=SUM(Sales!E:E)")
    wb.set_cell(calc.id, 1, 1, "=A2*2")
    assert val(wb, calc, 1, 1) == 3998
    wb.set_cell(sales.id, 1, 4, "1001")  # row 2 qty 1 -> 1001
    assert val(wb, calc, 1, 0) == 2999
    assert val(wb, calc, 1, 1) == 5998


def test_column_formula_vectorised(wb, sales):
    wb.set_cell(sales.id, 1, 5, "=D2*E2", fill="column")
    f = next(iter(wb.formulas.values()))
    assert f.kind == "column" and f.error is None
    rows = grid(wb, sales, 1, 4, 3, 6)
    assert [r[2] for r in rows] == [r[0] * r[1] for r in rows]
    # an edit inside the column becomes an exception, not overwritten on recalc
    wb.set_cell(sales.id, 3, 5, "7")
    wb.set_cell(sales.id, 1, 3, "10")
    assert val(wb, sales, 3, 5) == 7
    assert val(wb, sales, 1, 5) == 10


def test_running_total_and_relative_windows(wb, sales):
    wb.set_cell(sales.id, 1, 5, "=SUM($E$2:E2)", fill="column")
    wb.set_cell(sales.id, 1, 6, "=E2-E1", fill="column")
    rows = grid(wb, sales, 1, 5, 4, 7)
    assert [r[1] for r in rows] == [1, 3, 6, 7]
    assert [r[2] for r in rows] == [1, 1, 1, -2]  # first row: E1 is the header -> blank


def test_circular_column_formula(wb, sales):
    wb.set_cell(sales.id, 2, 5, "=F1+1", fill="column")
    f = next(iter(wb.formulas.values()))
    assert f.error and "Circular" in f.error


def test_spill_filter_sort_unique(wb, sales):
    out = wb.add_sheet("Out")
    wb.set_cell(out.id, 0, 0, "=FILTER(Sales!A:C,Sales!D:D>=49)")
    assert out.columns[0].name not in ("ID",)  # header row did not pass the condition
    n = sum(1 for f in wb.formulas.values() if f.spill)
    assert n == 1
    wb.set_cell(out.id, 0, 5, "=SORT(UNIQUE(Sales!B2:B),1,FALSE)")
    col = [r[0] for r in grid(wb, out, 0, 4, 5, 6)]
    assert col == ["West", "South", "North", "East"]


def test_spill_blocked_and_recovers(wb):
    sh = wb.add_sheet("S")
    wb.set_cell(sh.id, 3, 0, "x")
    wb.set_cell(sh.id, 1, 0, "=SEQUENCE(5)")
    f = next(iter(wb.formulas.values()))
    assert f.error and f.error.startswith("#REF!")
    wb.set_cell(sh.id, 3, 0, "")
    assert f.error is None
    assert [r[0] for r in grid(wb, sh, 1, 6, 0, 1)] == [1, 2, 3, 4, 5]


def test_query_group_by(wb, sales):
    out = wb.add_sheet("Q")
    wb.set_cell(
        out.id,
        0,
        0,
        "=QUERY(Sales!A:E, \"select B, sum(E) where E > 1 group by B order by B label sum(E) 'Units'\")",
    )
    assert [c.name for c in out.columns[:2]] == ["Region", "Units"]
    rows = grid(wb, out, 1, 5, 0, 2)
    assert [r[0] for r in rows] == ["East", "North", "South", "West"]


def test_arrayformula_with_header_idiom(wb, sales):
    wb.set_cell(sales.id, 0, 5, '=ARRAYFORMULA(IF(ROW(A:A)=1,"Upper",UPPER(B:B)))')
    assert sales.columns[5].name == "Upper"
    assert val(wb, sales, 1, 5) == "NORTH"


def test_undo_redo_cell_and_structure(wb, sales):
    wb.set_cell(sales.id, 1, 1, "Changed")
    wb.insert_rows(sales.id, 2, 3)
    assert sales.nrows == 1003
    wb.undo()
    assert wb.sheet(sales.id).nrows == 1000
    wb.undo()
    assert val(wb, wb.sheet(sales.id), 1, 1) == "North"
    wb.redo()
    assert val(wb, wb.sheet(sales.id), 1, 1) == "Changed"


def test_undo_add_sheet_and_redo(wb, sales):
    sh = wb.add_sheet("New")
    wb.set_cell(sh.id, 1, 0, "5")
    wb.undo()
    wb.undo()
    assert wb.get_sheet("New") is None
    wb.redo()
    wb.redo()
    assert val(wb, wb.get_sheet("New"), 1, 0) == 5


def test_insert_delete_rewrites_refs(wb, sales):
    calc = wb.add_sheet("Calc")
    wb.set_cell(calc.id, 1, 0, "=Sales!D10")
    wb.set_cell(calc.id, 1, 1, "=SUM(Sales!D2:D20)")
    wb.insert_rows(sales.id, 5, 2)
    texts = sorted(f.text for f in wb.formulas.values())
    assert texts == ["=SUM(Sales!D2:D22)", "=Sales!D12"]
    wb.delete_rows(sales.id, 11, 11)  # sheet row 11 = "D12" in A1 terms
    texts = sorted(f.text for f in wb.formulas.values())
    assert texts == ["=#REF!", "=SUM(Sales!D2:D21)"]
    wb.insert_cols(sales.id, 0, 1)
    assert any(f.text == "=SUM(Sales!E2:E21)" for f in wb.formulas.values())


def test_sort_and_filter(wb, sales):
    wb.sort(sales.id, [{"col": 3, "asc": False}])
    assert val(wb, sales, 1, 3) == 49.5
    wb.set_filter(sales.id, 1, {"include": ["North", "South"]})
    s = wb.sheet(sales.id)
    assert s.nview == 500
    rows = wb.viewport(s.id, 0, 4, 1, 2)
    assert rows["rows"][0] == 0 and all(c[0] in ("North", "South") for c in rows["cells"][1:])
    wb.set_filter(sales.id, 3, {"cond": {"op": "gt", "value": 40}})
    assert wb.sheet(sales.id).nview < 500
    wb.toggle_filter(sales.id, False)
    assert wb.sheet(sales.id).view_table is None


def test_fill_series_and_formula(wb):
    sh = wb.add_sheet("S")
    wb.set_cell(sh.id, 1, 0, "1")
    wb.set_cell(sh.id, 2, 0, "3")
    wb.fill(sh.id, [1, 0, 2, 0], [1, 0, 6, 0])
    assert [r[0] for r in grid(wb, sh, 1, 7, 0, 1)] == [1, 3, 5, 7, 9, 11]
    wb.set_cell(sh.id, 1, 1, "=A2*10")
    wb.fill(sh.id, [1, 1, 1, 1], [1, 1, 6, 1])
    assert [r[0] for r in grid(wb, sh, 1, 7, 1, 2)] == [10, 30, 50, 70, 90, 110]


def test_paste_internal_shifts_formulas(wb):
    sh = wb.add_sheet("S")
    wb.set_range(sh.id, 1, 0, [["1", "=A2*2"], ["2", "=A3*2"]])
    wb.paste_internal(sh.id, {"sheet": sh.id, "r1": 1, "c1": 0, "r2": 2, "c2": 1}, 5, 2)
    assert grid(wb, sh, 5, 7, 2, 4) == [[1, 2], [2, 4]]
    assert any(f.text == "=C6*2" for f in wb.formulas.values())


def test_strict_validation_rejects(wb, sales):
    F.add_validation(
        wb,
        sales.id,
        {
            "kind": "list",
            "cols": [sales.columns[1].id],
            "values": ["North", "South", "East", "West"],
            "strict": True,
        },
    )
    with pytest.raises(EngineError):
        wb.set_cell(sales.id, 1, 1, "Nowhere")
    wb.set_cell(sales.id, 1, 1, "south")
    rep = F.validation_report(wb, sales.id)
    assert rep["rules"][0]["invalid"] == 0


def test_validation_flags_and_report(wb, sales):
    F.add_validation(
        wb,
        sales.id,
        {"kind": "number", "op": "between", "value": 0, "value2": 40, "cols": [sales.columns[3].id]},
    )
    rep = F.validation_report(wb, sales.id)["rules"][0]
    assert rep["invalid"] == 190
    vp = wb.viewport(sales.id, 0, 200, 3, 4)
    assert any(f[2] == "invalid" for f in vp["flags"])


def test_conditional_formats(wb, sales):
    F.add_cond_format(wb, sales.id, {"kind": "gt", "value": 45, "cols": [sales.columns[3].id]})
    F.add_cond_format(wb, sales.id, {"kind": "color_scale", "cols": [sales.columns[3].id]})
    F.add_cond_format(wb, sales.id, {"kind": "duplicate", "cols": [sales.columns[1].id]})
    vp = wb.viewport(sales.id, 0, 50, 0, 5)
    kinds = {f[2] for f in vp["flags"]}
    assert {"cf", "scale"} <= kinds


def test_find_and_replace(wb, sales):
    res = F.find(wb, sales.id, "north", row=0, col=-1)
    assert res["found"] and res["count"] == 250 and (res["row"], res["col"]) == (1, 1)
    nxt = F.find(wb, sales.id, "north", row=res["row"], col=res["col"])
    assert nxt["row"] == 5
    out = F.replace_all(wb, sales.id, "North", "N.")
    assert out["replaced"] == 250
    assert val(wb, sales, 1, 1) == "N."


def test_duplicates(wb, sales):
    info = F.duplicates(wb, sales.id, [1, 2], "count")
    assert info["unique"] == 20 and info["duplicates"] == 980
    F.duplicates(wb, sales.id, [1, 2], "remove")
    assert wb.sheet(sales.id).nrows == 20


def test_pivot_and_refresh(wb, sales):
    res = F.create_pivot(
        wb,
        {
            "source": sales.id,
            "rows": [sales.columns[1].id],
            "cols": [],
            "values": [{"col": sales.columns[4].id, "agg": "sum"}],
        },
    )
    pv = wb.sheet(res["sheet"])
    rows = grid(wb, pv, 0, 7, 0, 2)
    assert rows[0] == ["Region", "SUM of Qty"]
    assert rows[5][0] == "Grand Total" and rows[5][1] == 1999
    wb.set_cell(sales.id, 1, 4, "101")  # +100
    assert grid(wb, wb.sheet(res["sheet"]), 5, 6, 0, 2)[0][1] == 2099


def test_selection_stats(wb, sales):
    st = F.selection_stats(wb, sales.id, [[1, 4, 10, 4]])
    assert st["count"] == 10 and st["sum"] == 19


def test_text_cleanup_and_split(wb):
    sh = wb.add_sheet("S")
    wb.set_range(sh.id, 1, 0, [["  a  b "], ["c,d,e"]])
    F.transform_text(wb, sh.id, [[1, 0, 1, 0]], "trim")
    assert val(wb, sh, 1, 0) == "a b"
    F.split_column(wb, sh.id, 0, ",")
    assert grid(wb, sh, 2, 3, 1, 4) == [["c", "d", "e"]]
