# Changelog

## 0.4.1
- **Formulas across tabs:** while typing a formula, click other tabs and keep typing. The keyboard stays in the formula across as many tabs as you like. Clicking a column letter or row number inserts `C:C` / `5:5` instead of ending the formula, and the formula help card no longer blocks clicks on the grid (it fades when you point at it; F1 shows/hides it).
- **Formulas across files:** File → Use another file in formulas… adds the tabs of another .pysheets workbook, Excel, CSV or Parquet file to this workbook as linked tabs, so INDEX/MATCH, FILTER, VLOOKUP… can use both. Opening a second .pysheets file offers the same. When that file changes, PySheets offers Refresh; columns you add are kept.
- **Find:** Ctrl+F (or Ctrl+H) always jumps into the search box, even when the find bar is already open.

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
