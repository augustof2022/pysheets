import json
import os
import sqlite3
import urllib.request

from conftest import grid, val

from pysheets.engine import io as IO
from pysheets.engine.workbook import Workbook
from pysheets.server import make_server, serve_in_thread


def test_csv_parquet_roundtrip(wb, sales, tmp_path):
    for ext in ("csv", "parquet", "json", "tsv"):
        p = str(tmp_path / f"out.{ext}")
        IO.export(wb, p, ext, "sheet", sales.id)
        res = IO.import_file(wb, p)
        sh = wb.sheet(res["sheets"][0])
        assert sh.nrows == 1000, ext
        assert [c.name for c in sh.columns] == ["ID", "Region", "Item", "Price", "Qty"], ext


def test_excel_roundtrip(wb, sales, tmp_path):
    wb.set_cell(sales.id, 1, 5, "=D2*E2", fill="column")
    wb.set_cell(sales.id, 0, 5, "Total")
    p = str(tmp_path / "out.xlsx")
    IO.export(wb, p, "xlsx", "all")
    prev = IO.preview(wb, p)
    assert prev["tables"][0]["rows"][0][:2] == ["ID", "Region"]
    res = IO.import_file(wb, p)
    sh = wb.sheet(res["sheets"][0])
    assert sh.nrows == 1000 and sh.columns[5].name == "Total"
    assert val(wb, sh, 1, 5) == val(wb, sales, 1, 5)


def test_sqlite_duckdb_sql(wb, sales, tmp_path):
    p = str(tmp_path / "out.sqlite")
    IO.export(wb, p, "sqlite", "sheet", sales.id)
    assert sqlite3.connect(p).execute('SELECT count(*), sum("Qty") FROM "Sales"').fetchone() == (1000, 1999)
    res = IO.import_file(wb, p, {"query": 'SELECT "Region", count(*) AS n FROM "Sales" GROUP BY 1'})
    assert wb.sheet(res["sheets"][0]).nrows == 4
    d = str(tmp_path / "out.duckdb")
    IO.export(wb, d, "duckdb", "sheet", sales.id)
    assert IO.list_db_tables(d, "duckdb") == ["Sales"]
    s = str(tmp_path / "out.sql")
    IO.export(wb, s, "sql", "sheet", sales.id)
    text = open(s).read()
    assert 'CREATE TABLE "Sales"' in text and "INSERT INTO" in text


def test_sqlalchemy_url(wb, sales, tmp_path):
    url = f"sqlite:///{tmp_path / 'sa.db'}"
    IO.export_sql_url(wb, url, "sheet", sales.id)
    res = IO.import_sql_url(wb, url, 'SELECT * FROM "Sales" WHERE "Qty" = 3', "Q")
    assert wb.sheet(res["sheets"][0]).nrows == 333


def test_native_save_open(wb, sales, tmp_path):
    wb.set_cell(sales.id, 1, 5, "=D2*E2", fill="column")
    calc = wb.add_sheet("Calc")
    wb.set_cell(calc.id, 1, 0, "=SUM(Sales!E:E)")
    p = str(tmp_path / "book.pysheets")
    IO.save_workbook(wb, p)
    other = Workbook()
    IO.open_workbook(other, p)
    assert [s.name for s in other.sheets] == ["Sales", "Calc"]
    assert len(other.formulas) == 2
    other.set_cell(other.sheets[0].id, 1, 4, "100")
    assert val(other, other.sheets[1], 1, 0) == 1999 + 99
    assert grid(other, other.sheets[0], 1, 2, 5, 6)[0][0] == val(other, other.sheets[0], 1, 3) * 100


def test_excel_import_mixed_and_header_detect(wb, tmp_path):
    import xlsxwriter

    p = str(tmp_path / "mixed.xlsx")
    book = xlsxwriter.Workbook(p)
    ws = book.add_worksheet("Data")
    ws.write_row(0, 0, ["Name", "Value"])
    ws.write_row(1, 0, ["a", 1])
    ws.write_row(2, 0, ["b", "two"])
    ws2 = book.add_worksheet("NoHeader")
    ws2.write_row(0, 0, [1, 2])
    ws2.write_row(1, 0, [3, 4])
    book.close()
    res = IO.import_file(wb, p)
    a, b = (wb.sheet(s) for s in res["sheets"])
    assert a.columns[1].duck == "VARCHAR" and a.nrows == 2
    assert b.nrows == 2 and b.columns[0].name == ""


def test_server_requires_token(tmp_path):
    w = Workbook()
    w.add_sheet("Sheet1", record=False)
    srv = make_server(w, 0)
    serve_in_thread(srv)
    try:
        body = json.dumps({"method": "workbook.info"}).encode()
        req = urllib.request.Request(srv.url + "api/rpc", data=body, method="POST")
        try:
            urllib.request.urlopen(req)
            raise AssertionError("expected 401")
        except urllib.error.HTTPError as e:
            assert e.code == 401
        req = urllib.request.Request(
            srv.url + "api/rpc", data=body, method="POST", headers={"X-PySheets-Token": srv.token}
        )
        out = json.loads(urllib.request.urlopen(req).read())
        assert out["ok"] and out["result"]["sheets"][0]["name"] == "Sheet1"
        # failed commands roll back cleanly and report the error
        bad = json.dumps(
            {"method": "cell.set", "params": {"sheet": "nope", "row": 1, "col": 0, "value": "x"}}
        ).encode()
        req = urllib.request.Request(
            srv.url + "api/rpc", data=bad, method="POST", headers={"X-PySheets-Token": srv.token}
        )
        out = json.loads(urllib.request.urlopen(req).read())
        assert not out["ok"] and "Unknown sheet" in out["error"]
    finally:
        srv.shutdown()
        w.close()
    assert os.path.isdir(w._tmpdir)


def test_window_title_follows_workbook(tmp_path):
    class FakeWindow:
        confirm_close = False

        def __init__(self):
            self.titles = []

        def set_title(self, t):
            self.titles.append(t)

    w = Workbook()
    w.add_sheet("Sheet1", record=False)
    srv = make_server(w, 0)
    srv.window = win = FakeWindow()
    serve_in_thread(srv)

    def call(method, **params):
        body = json.dumps({"method": method, "params": params}).encode()
        req = urllib.request.Request(
            srv.url + "api/rpc", data=body, method="POST", headers={"X-PySheets-Token": srv.token}
        )
        return json.loads(urllib.request.urlopen(req).read())

    try:
        call("workbook.title", title="Budget 2026")
        assert win.titles[-1] == "• Budget 2026 — PySheets" and win.confirm_close
        n = len(win.titles)
        call("workbook.info")  # nothing changed: the title is not set again
        assert len(win.titles) == n
        call("workbook.save", path=str(tmp_path / "Q3 report.pysheets"))
        assert win.titles[-1] == "Q3 report — PySheets" and not win.confirm_close
    finally:
        srv.shutdown()
        w.close()
