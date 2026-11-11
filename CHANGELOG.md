# Changelog

## 0.5.2
- **Formulas across tabs:** while typing a formula you can click other tabs and keep typing. The keyboard stays in the formula across as many tabs as you like, and clicking a tab no longer takes it away. The formula help card no longer blocks clicks on the grid: it fades when you point at it, and F1 shows or hides it.
- **Formulas across files:** File → Use another file in formulas… adds the tabs of another .pysheets workbook, Excel, CSV or Parquet file to this workbook as linked tabs, so INDEX/MATCH, FILTER, VLOOKUP… can use data from both. Opening a second .pysheets file offers the same. When that file changes, PySheets offers Refresh; columns you add yourself are kept.
- **Find:** Ctrl+F (or Ctrl+H) always jumps into the search box with the text selected, even when the find bar is already open.

## 0.5.1
- **Find:** the search starts when you press Enter, the box says "Press Enter" while you type and shows a spinner with "Searching…" while it runs. Jumping to the next match no longer rescans the whole sheet (the match count is reused until the data changes), and the search for it starts near the current cell. An invalid regular expression now shows a message instead of nothing.
- **Formulas:** while editing a formula, clicking a column header inserts a whole-column reference (`Sheet1!M:M`) and a row header a whole-row reference; drag across headers for several. This used to end the formula and jump back to the tab being edited.
- **Window title:** the taskbar shows the workbook's file name (`Budget — PySheets`, with `•` when there are unsaved changes), so several open PySheets windows can be told apart.

## 0.5.0
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
