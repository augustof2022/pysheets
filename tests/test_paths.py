"""Workbooks are saved in Documents/PySheets by default."""

from pysheets import paths
from pysheets.api import dispatch
from pysheets.engine.workbook import Workbook


def test_folders_are_created(tmp_path, monkeypatch):
    monkeypatch.setenv("PYSHEETS_DOCUMENTS", str(tmp_path / "Docs"))
    assert paths.workbooks_dir() == tmp_path / "Docs" / "PySheets"
    assert paths.exports_dir() == tmp_path / "Docs" / "PySheets" / "Exports"
    assert paths.exports_dir().is_dir()


def test_unique_and_safe_names(tmp_path):
    assert paths.safe_name('Q3: "sales"/report?') == "Q3_ _sales_report_"
    assert paths.safe_name("  ") == "Untitled spreadsheet"
    (tmp_path / "a.pysheets").write_text("x")
    (tmp_path / "a (2).pysheets").write_text("x")
    assert paths.unique_path(tmp_path, "a.pysheets").name == "a (3).pysheets"


def test_save_without_path_goes_to_documents_pysheets(tmp_path, monkeypatch):
    monkeypatch.setenv("PYSHEETS_DOCUMENTS", str(tmp_path))
    wb = Workbook()
    wb.title = "Monthly KU report"
    out = dispatch(wb, "workbook.save", {})
    assert out["ok"], out
    saved = tmp_path / "PySheets" / "Monthly KU report.pysheets"
    assert out["result"]["path"] == str(saved) and saved.exists()
    # saving again (Ctrl+S) keeps the same file
    out = dispatch(wb, "workbook.save", {})
    assert out["result"]["path"] == str(saved)
    assert dispatch(wb, "paths.folders", {})["result"]["workbooks"] == str(tmp_path / "PySheets")
    wb.close()
