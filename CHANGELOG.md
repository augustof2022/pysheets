# Changelog

## Unreleased
- **Find:** the search starts when you press Enter, the box says "Press Enter" while you type and shows a spinner with "Searching…" while it runs. Jumping to the next match no longer rescans the whole sheet (the match count is reused until the data changes), and the search for it starts near the current cell. An invalid regular expression now shows a message instead of nothing.
- **Formulas:** while editing a formula, clicking a column header inserts a whole-column reference (`Sheet1!M:M`) and a row header a whole-row reference; drag across headers for several. This used to end the formula and jump back to the tab being edited.
- **Window title:** the taskbar shows the workbook's file name (`Budget — PySheets`, with `•` when there are unsaved changes), so several open PySheets windows can be told apart.

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
