# PySheets

A fast desktop spreadsheet for **millions of rows**, an alternative to Row Zero and
Microsoft Excel for big data. Data lives in an embedded **DuckDB** database, and formulas
compile to vectorised SQL. The UI is a canvas-rendered grid in a native Windows window
(Edge WebView2).

![PySheets light theme](docs/screenshot-light.png)
![PySheets dark theme](docs/screenshot-dark.png)

## Features

| Area | What you get |
| --- | --- |
| **Scale** | Opens multi-million-row CSV / Parquet files in under a second; scrolling fetches only the visible block (about 2–5 ms per block). Custom scrolling has no browser height limit. |
| **Import** | CSV/TSV (auto-detected delimiter and header), Excel (`.xlsx .xlsm .xls .xlsb .ods`, every tab), Parquet, JSON/NDJSON, SQLite, DuckDB, and any SQL database through a SQLAlchemy URL (PostgreSQL, MySQL, SQL Server…), by table or by custom query. Drag and drop files onto the window. |
| **Export** | One tab or the whole workbook to Excel (streams through a Rust writer and splits sheets over 1,048,576 rows), CSV, TSV, Parquet, JSON, SQLite, DuckDB, a portable SQL script, or straight into a database server. |
| **Formulas** | About 150 functions with Google Sheets semantics: `SUM… COUNTIF(S) SUMIF(S) AVERAGEIF(S) MAXIFS MINIFS`, `VLOOKUP HLOOKUP XLOOKUP INDEX MATCH CHOOSE`, `FILTER SORT SORTN UNIQUE QUERY ARRAYFORMULA SEQUENCE SPLIT TRANSPOSE FLATTEN`, text (`TRIM PROPER SUBSTITUTE REGEX* TEXT TEXTJOIN…`), dates, logic, statistics. Results **spill** like Sheets/Excel 365. |
| **Column formulas** | Type a formula and press **Ctrl+Enter** (or double-click the fill handle). It is applied to the whole column as **one** SQL query: `=D2*E2` or `=COUNTIF(B:B,B2)` over 2,000,000 rows takes about 0.1–0.6 s. Running totals (`=SUM($E$2:E2)`) become window functions. |
| **Editing** | Typed columns, fill handle (series and formulas), copy/cut/paste (internal paste keeps formulas and shifts references), multi-range selection, insert/delete rows and columns with reference rewriting, undo/redo for everything. |
| **Formula UX** | Syntax highlighting, colour-matched reference outlines on the grid, click-to-insert references (including from other tabs), function autocomplete with signature help, auto-closing parentheses, error tooltips. |
| **Data tools** | Filter (by values with counts, or by condition), sort, pivot tables with a live preview (they refresh when the source changes), find & replace (case, whole cell, regex, all tabs), duplicate detection (count / highlight / mark / remove), data validation (dropdowns, checkboxes, number/date/text rules, e-mail, URL, regex, unique, custom formula; warn or reject) with an invalid-cell report, text cleanup (trim, case, non-printing), split text to columns, column statistics. |
| **Formatting** | Number formats (currency, percent, dates, scientific, custom), bold/italic/underline/strike, text and fill colours, alignment, conditional formatting (comparisons, text, blanks, duplicates/unique, top/bottom N, above/below average, colour scales, custom formula). |
| **Layout** | Freeze rows/columns, multiple tabs (rename, reorder by drag, duplicate, delete), column resize and autofit, row density, light/dark theme, command palette (**Ctrl+K**). |
| **Files** | Native `.pysheets` workbook (a single DuckDB file that keeps data, formulas, formats and rules). |

## Install (Windows)

Every push builds the Windows app in GitHub Actions (**Actions → Windows app → Artifacts**):

* `PySheets-Setup-<version>.exe`: installer with a Start-menu entry and `.pysheets` file association.
* `PySheets-<version>-windows-x64.zip`: portable folder; run `PySheets.exe`.

Tagging a release (`git tag v0.2.0 && git push --tags`) publishes both to GitHub Releases.
The app needs the Microsoft Edge WebView2 runtime, which ships with Windows 10 and 11.

## Run from source

```bash
uv sync --all-extras                # Python deps (duckdb, pyarrow, pywebview, sqlalchemy, …)
npm --prefix frontend ci            # UI deps
npm --prefix frontend run build     # builds the UI into src/pysheets/web
uv run pysheets data.csv            # native window
uv run pysheets --browser data.csv  # or in your default browser
```

Build the Windows executable locally:
`powershell -ExecutionPolicy Bypass -File scripts\build_windows.ps1 -Installer`.

### Development

```bash
scripts/dev.sh            # API on :8765 + Vite hot reload on http://localhost:5173
uv run pytest             # engine, formula, import/export and server tests
uv run ruff check src tests
npm --prefix frontend run typecheck
```

## How it works

```
┌────────────── Native window (pywebview → WebView2) ──────────────┐
│  React chrome + canvas grid (frontend/)                           │
│  • block cache (128 rows × 48 cols), stale-while-revalidate       │
│  • custom eased scrolling, frozen panes, animated selection       │
└──────────────── JSON-RPC over http://127.0.0.1 (token) ──────────┘
                                   │
┌──────────────────────────── Python engine ───────────────────────┐
│ formula/   parser → compiler → DuckDB SQL (scalar, row, array)    │
│ engine/    sheets = DuckDB tables (__rid + typed columns),        │
│            spills, dependency-ordered recalc, undo snapshots,     │
│            filters/sort/pivot/validation/formatting, import/export│
└───────────────────────── DuckDB (in-memory, spills to disk) ─────┘
```

* **Row 1 is the header row.** Each tab is a typed DuckDB table, and row 1 holds the
  column names, so `A1` references and ranges like `A:A` / `A2:A` behave like Google Sheets.
* **Formulas compile to SQL.** A cell formula becomes one `SELECT`. Ranges become
  relations, and lookups become correlated sub-queries that DuckDB turns into hash joins.
  A column formula is evaluated by one `UPDATE` (or a window query) over the whole column.
* **Recalculation** tracks which regions each formula reads and writes, then recomputes
  only the affected formulas in topological order. It detects cycles and spill collisions.
* The local server binds to `127.0.0.1` and requires a random per-launch token, so
  other web pages can't call it.

## Differences from Google Sheets / Excel (current limitations)

* **Error values.** Errors inside vectorised formulas become blank cells instead of
  `#N/A`/`#DIV/0!`. `IFERROR`/`IFNA` still work, and errors from a single-cell formula
  are shown with a tooltip.
* **Mixed-type columns.** Columns are typed, so a column that mixes numbers and text is
  stored as text. Numeric formulas still read numeric-looking text.
* **Excel export writes values**, not formulas.
* **QUERY** supports `select, where, group by, pivot, order by, limit, offset, label`.
* **Not yet supported:** charts, merged cells, borders, per-row heights, hidden
  rows/columns (use filters), comments, named ranges, `INDIRECT`/`OFFSET`/`LAMBDA`,
  iterative calculation, real-time collaboration.

## Keyboard shortcuts

`Enter`/`F2` edit · `Ctrl+Enter` fill formula down the column · `Ctrl+D` fill down ·
`Ctrl+Z`/`Ctrl+Y` undo/redo · `Ctrl+C/X/V` clipboard · `Ctrl+B/I/U` styles ·
`Ctrl+F`/`Ctrl+H` find/replace · `Ctrl+K` command palette · `Ctrl+Shift+L` filter ·
`Ctrl+Arrow` jump to data edge · `Ctrl+Space`/`Shift+Space` select column/row ·
`Ctrl+PgUp/PgDn` switch tab · `Ctrl+O` open · `Ctrl+S` save · `Ctrl+Shift+E` export ·
`Ctrl+/` all shortcuts.

## License

MIT
