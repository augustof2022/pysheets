"""Python scripts inside the workbook and custom formula functions."""

import time

import pytest
from conftest import grid, val

from pysheets.engine import io as IO
from pysheets.engine.workbook import EngineError, Workbook


def wait(mgr, run, timeout=20):
    t0 = time.time()
    while mgr.runs[run.id].status == "running":
        if time.time() - t0 > timeout:
            raise AssertionError("script did not finish")
        time.sleep(0.02)
    return mgr.status(run.id)


def test_macro_reads_writes_and_sql(wb, sales):
    mgr = wb.scripting
    code = """
df = ps.sheet("Sales").df()
print("rows", len(df))
by_region = ps.sql('SELECT "Region", sum("Qty") AS units FROM "Sales" GROUP BY 1 ORDER BY 1')
ps.new_sheet("By region", by_region)
ps.sheet("By region").set("D1", "Note")
ps.sheet("By region").set("D2", "=B2*2")
ps.toast("done!")
"""
    st = wait(mgr, mgr.start(code=code, name="t"))
    assert st["status"] == "done", st
    assert any(line["text"] == "rows 1000" for line in st["lines"])
    out = wb.get_sheet("By region")
    assert [c.name for c in out.columns[:2]] == ["Region", "units"]
    rows = grid(wb, out, 1, 5, 0, 2)
    assert [r[0] for r in rows] == ["East", "North", "South", "West"]
    assert val(wb, out, 1, 3) == rows[0][1] * 2
    assert st["toasts"] == ["done!"]


def test_script_error_is_reported(wb):
    st = wait(wb.scripting, wb.scripting.start(code="x = 1\nraise ValueError('boom')"))
    assert st["status"] == "error" and "boom" in st["error"]
    assert any("ValueError" in ln["text"] for ln in st["lines"])


def test_cancel_running_script(wb):
    mgr = wb.scripting
    run = mgr.start(code="while True:\n    pass\n")
    time.sleep(0.2)
    mgr.cancel(run.id)
    st = wait(mgr, run, 10)
    assert st["status"] == "cancelled"


def test_custom_functions_in_formulas(wb, sales):
    mgr = wb.scripting
    lib = """
import re

@ps.function
def BIG_QTY(q: float) -> bool:
    return q is not None and q >= 3

@ps.function
def SHOUT(s: str) -> str:
    return (s or "").upper() + "!"

@ps.function(vectorized=True)
def DOUBLE_IT(x: float) -> float:
    return x * 2
"""
    mgr.save({"id": "lib", "name": "lib", "kind": "functions", "code": lib})
    assert set(wb.custom_functions) == {"BIG_QTY", "SHOUT", "DOUBLE_IT"}
    wb.set_cell(sales.id, 1, 5, "=BIG_QTY(E2)", fill="column")
    wb.set_cell(sales.id, 1, 6, "=SHOUT(B2)", fill="column")
    wb.set_cell(sales.id, 1, 7, "=DOUBLE_IT(D2)", fill="column")
    rows = grid(wb, sales, 1, 4, 3, 8)
    assert [r[2] for r in rows] == [False, False, True]
    assert rows[0][3] == "NORTH!"
    assert rows[1][4] == rows[1][0] * 2
    assert all(f.error is None for f in wb.formulas.values())
    # built-in names are protected
    with pytest.raises(EngineError):
        mgr.save(
            {
                "id": "bad",
                "name": "bad",
                "kind": "functions",
                "code": "@ps.function\ndef SUM(x: float) -> float:\n    return x",
            }
        )


def test_file_script(wb, tmp_path):
    script = tmp_path / "hello.py"
    script.write_text("import sys\nprint('hello', sys.argv[1:])\n", encoding="utf-8")
    mgr = wb.scripting
    mgr.save(
        {"id": "f", "name": "file", "kind": "file", "path": str(script), "args": "a b", "python": "auto"}
    )
    st = wait(mgr, mgr.start("f"))
    assert st["status"] == "done", st
    assert any("hello ['a', 'b']" in ln["text"] for ln in st["lines"])


def test_scripts_saved_and_untrusted_on_open(wb, sales, tmp_path):
    wb.scripting.save(
        {
            "id": "lib",
            "name": "lib",
            "kind": "functions",
            "code": "@ps.function\ndef TWICE(x: float) -> float:\n    return x * 2",
        }
    )
    wb.set_cell(sales.id, 1, 5, "=TWICE(E2)")
    p = str(tmp_path / "s.pysheets")
    IO.save_workbook(wb, p)
    other = Workbook()
    IO.open_workbook(other, p)
    assert [s["name"] for s in other.scripts] == ["lib"]
    assert other.scripting.trusted is False
    with pytest.raises(EngineError):
        other.scripting.start(code="print(1)")
    other.recalc(None)
    f = next(iter(other.formulas.values()))
    assert f.error and f.error.startswith("#NAME?")
    other.scripting.load_all_functions()
    assert f.error is None and val(other, other.sheets[0], 1, 5) == 2


def test_register_after_partial_fetch(wb, sales):
    # a previous query left an unfinished result on the connection (common in the app)
    wb.con.execute(f'SELECT * FROM "{sales.table}"').fetchone()
    wb.scripting.save(
        {
            "id": "x",
            "name": "x",
            "kind": "functions",
            "code": "@ps.function\ndef TRIPLE(x: float) -> float:\n    return x * 3",
        }
    )
    wb.con.execute(f'SELECT * FROM "{sales.table}"').fetchone()
    wb.scripting.save(
        {
            "id": "x",
            "name": "x",
            "kind": "functions",
            "code": "@ps.function\ndef TRIPLE(x: float) -> float:\n    return x * 4",
        }
    )
    wb.set_cell(sales.id, 1, 5, "=TRIPLE(2)")
    assert val(wb, sales, 1, 5) == 8
