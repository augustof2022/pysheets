"""Google Sheets linking and consolidation, with a fake API client (no network)."""

import pytest
from conftest import grid, val

from pysheets.connectors.google import GoogleClient, clean_header, extract_sheet_id, values_to_rows
from pysheets.engine import links as L


class FakeClient(GoogleClient):
    def __init__(self, books):
        super().__init__(session=None)
        self.public = False
        self.books = books
        self.calls = 0

    def get_values(self, spreadsheet_id, tabs, typed=True):
        self.calls += 1
        book = self.books.get(spreadsheet_id)
        if book is None:
            from pysheets.connectors.google import GoogleError

            raise GoogleError("Spreadsheet or tab not found")
        return {t: ([list(r) for r in book[t]] if t in book else None) for t in tabs}


DIRECTORY = [
    ["cluster_id", "Spreadsheet ID", "gsheet_datasource_url"],
    ["KU_APP_001", "id001", ""],
    ["KU_APP_002", "", "https://docs.google.com/spreadsheets/d/id002/edit#gid=0"],
    ["KU_APP_003", "id003", ""],
    ["", "", ""],
]


def books():
    return {
        "dir": {"app_cluster_directory": DIRECTORY},
        "id001": {
            "monitoring": [
                ["REGION", "BARANGAY", "HOUSEHOLD ID NUMBER", "CLIENT STATUS"],
                ["I", "San Juan", "111", "ACTIVE"],
                ["I", "Poblacion", "112", "EXITED"],
                ["", "", "", ""],
            ],
            "ku_report": [["REGION", "NARRATIVE REPORT"], ["I", "ok"]],
        },
        "id002": {
            "monitoring": [
                ["region", "Barangay\n", "household id  number"],  # header variations
                ["II", "Centro", "221"],
            ]
        },
        "id003": {
            "monitoring": [
                ["REGION", "BARANGAY", "HOUSEHOLD ID NUMBER", "EXTRA COL"],
                ["III", "Sto Nino", "331", "x"],
                ["III", "Bagong", "332", "y"],
            ],
            "ku_report": [["REGION", "NARRATIVE REPORT"], ["III", "late"]],
        },
    }


SPEC = {
    "directory": {
        "kind": "gsheet",
        "url": "https://docs.google.com/spreadsheets/d/dir/edit",
        "tab": "app_cluster_directory",
    },
    "id_column": "Spreadsheet ID",
    "url_column": "gsheet_datasource_url",
    "label_column": "cluster_id",
    "tabs": ["monitoring", "ku_report"],
}


def test_helpers():
    assert extract_sheet_id("https://docs.google.com/spreadsheets/d/abc_DEF-1/edit#gid=0") == "abc_DEF-1"
    assert extract_sheet_id("  xyz ") == "xyz"
    assert clean_header("Barangay\n ") == clean_header("BARANGAY")
    names, rows = values_to_rows([["a", "", "a"], [1, 2, 3, 4], [5]])
    assert names == ["a", "Column 2", "a (2)", "Column 4"]
    assert rows == [[1, 2, 3, 4], [5, None, None, None]]


def test_consolidate_and_refresh(wb):
    fake = FakeClient(books())
    res = L.consolidate_gsheets(wb, SPEC, client=fake)
    assert res["sources"] == 3 and res["rows"] == {"monitoring": 5, "ku_report": 2}
    mon = wb.get_sheet("monitoring")
    names = [c.name for c in mon.columns]
    assert names[:5] == ["REGION", "BARANGAY", "HOUSEHOLD ID NUMBER", "CLIENT STATUS", "EXTRA COL"]
    assert "_source_label" in names and "_source_row_number" in names
    rows = grid(wb, mon, 1, 6, 0, 3)
    assert [r[2] for r in rows] == ["111", "112", "221", "331", "332"]  # blank row dropped, headers merged
    label_col = names.index("_source_label")
    assert val(wb, mon, 3, label_col) == "KU_APP_002"
    log = wb.get_sheet("monitoring log")
    statuses = [r[4] for r in grid(wb, log, 1, 7, 0, 9)]
    assert "MissingTab" in statuses  # id002 has no ku_report tab

    # the user adds a check column; it must survive a refresh and recompute
    check = len(mon.columns)
    wb.set_cell(mon.id, 0, check, "Duplicate HHID?")
    wb.set_cell(mon.id, 1, check, "=COUNTIF(C:C,C2)>1", fill="column")
    b = books()
    b["id003"]["monitoring"].append(["III", "Bagong", "111", "z"])  # new row, duplicate HHID
    fake.books = b
    out = L.refresh(wb, mon.id, client=fake)
    mon = wb.get_sheet("monitoring")
    assert out["rows"]["monitoring"] == 6 and mon.nrows == 6
    assert mon.columns[check].name == "Duplicate HHID?"
    assert [r[0] for r in grid(wb, mon, 1, 7, check, check + 1)] == [True, False, False, False, False, True]


def test_directory_in_workbook_and_list(wb):
    fake = FakeClient(books())
    d = wb.add_sheet("Directory")
    wb.set_range(d.id, 0, 0, [["name", "id"], ["one", "id001"], ["three", "id003"]])
    spec = {
        "directory": {"kind": "sheet", "sheet": d.id},
        "id_column": "id",
        "label_column": "name",
        "tabs": ["ku_report"],
        "name": "Reports",
    }
    res = L.consolidate_gsheets(wb, spec, client=fake)
    assert res["rows"] == {"ku_report": 2}
    rep = wb.get_sheet("Reports")
    assert [r[1] for r in grid(wb, rep, 1, 3, 0, 2)] == ["ok", "late"]
    # adding an ID to the directory sheet is picked up by the next refresh
    wb.set_cell(d.id, 3, 0, "two")
    wb.set_cell(d.id, 3, 1, "id002")
    L.refresh(wb, rep.id, client=fake)
    log = wb.get_sheet("Reports log")
    assert log.nrows == 3


def test_import_single_tab_and_refresh(wb):
    fake = FakeClient(books())
    res = L.import_tabs(wb, "id003", ["monitoring"], client=fake)
    sh = wb.sheet(res["sheets"][0])
    assert sh.link["kind"] == "gsheet" and sh.nrows == 2
    b = books()
    b["id003"]["monitoring"][1][3] = "changed"
    fake.books = b
    L.refresh(wb, sh.id, client=fake)
    assert val(wb, wb.sheet(sh.id), 1, 3) == "changed"


def test_missing_directory_column(wb):
    fake = FakeClient(books())
    spec = dict(SPEC, id_column="Nope", url_column="")
    with pytest.raises(Exception, match="not found"):
        L.consolidate_gsheets(wb, spec, client=fake)
