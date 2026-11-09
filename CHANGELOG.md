# Changelog

## Unreleased
- **Filtered views edit only what you see:** with a filter on, clearing, deleting rows, pasting (each line goes to the next visible row), copying, fill down / drag-fill, Ctrl+Enter column fills, formatting and text cleanup affect only the visible rows. Hidden rows are never changed, and the toast says how many visible rows were affected.
- **Format-tolerant lookups:** VLOOKUP, XLOOKUP, MATCH, HLOOKUP, the COUNTIF/SUMIF family and FILTER match keys that differ only in format (123 vs "00123", trailing or non-breaking spaces, "1,234" vs 1234, invisible characters). Exact matches win; format-only matches get an amber corner and a summary after the formula runs (exact / format differs / really missing).
- **Data → Check key matches:** compares two key columns, explains each mismatch, lists keys that are really missing, and normalises both columns to numbers or text (optionally keeping a copy of the originals). Switch lookups between Smart and Strict matching.
- New function `CLEANKEY(value)`.
- Large exception lists in column formulas no longer slow evaluation down.

## 0.4.0
- **Documentation inside the app:** Help → Documentation (F1) covers getting started, importing, the grid, formulas, every function, data tools, formatting, Google Sheets, Python scripts, exporting and shortcuts, with search. "Learn more" in the formula help card opens that function's page.
- **Check for updates:** Help → Check for updates… shows your version, tells you when you're on the latest release, and installs a new version with one click (verified download, PySheets reopens by itself).

## 0.3.4
- Workbooks are saved in Documents\PySheets and exports in Documents\PySheets\Exports. File → Open PySheets folder.

## 0.3.3
- Tooltips with names, shortcuts and descriptions on every toolbar button.
- Google Sheets-style formula help card: syntax, example, summary and every argument explained.
- Fixed the text cursor appearing before the last typed character in cells.

## 0.3.2
- Google Sheets requires signing in first; refreshes resume after signing in.

## 0.3.0
- Borders and merged cells.
- Google Sheets: linked sheets with Refresh, and consolidating a tab from a list of spreadsheets.
- Python scripts (macros, custom formula functions, running existing .py files).
