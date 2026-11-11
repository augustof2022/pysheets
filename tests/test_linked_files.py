"""Formulas across files: another file's tabs are added as linked sheets and refresh from disk."""

import os
import time

import pandas as pd

from pysheets.api import dispatch
from pysheets.engine import io as IO
from pysheets.engine.links import file_tabs, link_file, refresh, stale_file_links
from pysheets.engine.workbook import Workbook


def _sheet(wb, name):
    return next(s for s in wb.sheets if s.name == name)


def _val(wb, sheet, a1_row, col):
    return wb.get_cell(sheet.id, a1_row, col)["value"]


def test_link_pysheets_and_excel_and_use_in_formulas(tmp_path):
    # another PySheets workbook with a lookup table
    other = Workbook()
    sh = other.add_sheet("Sheet1", record=False)
    other.set_range(sh.id, 0, 0, [["code", "price"], ["A", 10], ["B", 20], ["C", 30]])
    other_path = str(tmp_path / "prices.pysheets")
    IO.save_workbook(other, other_path)
    other.close()
    # an Excel file with two tabs
    xlsx = tmp_path / "regions.xlsx"
    with pd.ExcelWriter(xlsx) as xw:
        pd.DataFrame({"code": ["A", "B"], "region": ["North", "South"]}).to_excel(
            xw, sheet_name="Map", index=False
        )
        pd.DataFrame({"x": [1]}).to_excel(xw, sheet_name="Other", index=False)

    assert file_tabs(other_path) == ["Sheet1"]
    assert file_tabs(str(xlsx)) == ["Map", "Other"]

    wb = Workbook()
    main = wb.add_sheet("Main", record=False)
    wb.set_range(main.id, 0, 0, [["code"], ["B"], ["C"]])
    r = link_file(wb, other_path)
    assert _sheet(wb, "prices").link["kind"] == "file"
    r2 = link_file(wb, str(xlsx), ["Map"])
    assert len(r["sheets"]) == 1 and len(r2["sheets"]) == 1
    assert "regions" in [s.name for s in wb.sheets]

    wb.set_cell(main.id, 1, 1, "=INDEX(prices!B:B, MATCH(A2, prices!A:A, 0))")
    wb.set_cell(main.id, 1, 2, '=IFNA(VLOOKUP(A2, regions!A:B, 2, FALSE), "none")')
    assert _val(wb, main, 1, 1) == 20
    assert _val(wb, main, 1, 2) == "South"

    # a column the user adds to the linked sheet survives a refresh
    prices = _sheet(wb, "prices")
    wb.set_range(prices.id, 0, 2, [["note"], ["keep me"]])

    # the source file changes on disk -> detected as stale, refresh pulls new values
    time.sleep(0.05)
    other = Workbook()
    IO.open_workbook(other, other_path)
    s0 = other.sheets[0]
    other.set_cell(s0.id, 2, 1, "25")
    IO.save_workbook(other, other_path)
    other.close()
    os.utime(other_path, (time.time() + 5, time.time() + 5))
    assert prices.id in stale_file_links(wb)
    refresh(wb, prices.id)
    assert _val(wb, main, 1, 1) == 25
    assert _val(wb, prices, 1, 2) == "keep me"
    assert prices.id not in stale_file_links(wb)
    wb.close()


def test_link_rpc_and_missing_file(tmp_path):
    csv = tmp_path / "people.csv"
    csv.write_text("name,age\nAna,31\nBo,42\n")
    wb = Workbook()
    out = dispatch(wb, "files.link", {"path": str(csv)})
    assert out["ok"], out
    assert any(s["name"] == "people" and s["link"]["book"] == "people.csv" for s in out["info"]["sheets"])
    bad = dispatch(wb, "files.link", {"path": str(tmp_path / "nope.csv")})
    assert not bad["ok"] and "not found" in bad["error"]
    wb.close()
