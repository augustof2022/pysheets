import {
  ArrowUpCircle,
  BookOpen,
  Cloud,
  Download,
  FolderOpen,
  Grid3x3,
  Keyboard,
  Paintbrush,
  Search,
  Sigma,
  SlidersHorizontal,
  SquareTerminal,
  Upload,
  X,
  type LucideIcon,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { ALL_DOCS, argName, syntaxArgs, type FnDoc } from "../formulaDocs";
import { closeDialog, openDialog, useApp } from "../store";
import { MOD } from "../shortcuts";
import { SHORTCUTS } from "./Dialogs";

/* ------------------------------------------------------------------ helpers */

const K = ({ children }: { children: React.ReactNode }) => <kbd className="dk">{children}</kbd>;
const C = ({ children }: { children: React.ReactNode }) => <code className="dc">{children}</code>;
const Path = ({ children }: { children: React.ReactNode }) => <span className="dpath">{children}</span>;
const Tip = ({ children }: { children: React.ReactNode }) => <div className="dtip">{children}</div>;
const Pre = ({ children }: { children: string }) => <pre className="dpre">{children.trim()}</pre>;

function Steps({ items }: { items: React.ReactNode[] }) {
  return (
    <ol className="dsteps">
      {items.map((x, i) => (
        <li key={i}>{x}</li>
      ))}
    </ol>
  );
}

function Table({ rows, head }: { rows: React.ReactNode[][]; head?: string[] }) {
  return (
    <table className="dtable">
      {head && (
        <thead>
          <tr>
            {head.map((h) => (
              <th key={h}>{h}</th>
            ))}
          </tr>
        </thead>
      )}
      <tbody>
        {rows.map((r, i) => (
          <tr key={i}>
            {r.map((c, j) => (
              <td key={j}>{c}</td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/* ----------------------------------------------------------------- sections */

interface Section {
  id: string;
  title: string;
  icon: LucideIcon;
  keywords: string;
  body: () => React.ReactNode;
}

const SECTIONS: Section[] = [
  {
    id: "start",
    title: "Getting started",
    icon: BookOpen,
    keywords: "welcome interface overview header row tabs toolbar formula bar quick start",
    body: () => (
      <>
        <p>
          PySheets is a desktop spreadsheet built for big data. It opens files with millions of rows in about a second and feels like Google Sheets: formulas, filters, pivots, formatting, multiple tabs. Behind the grid, every sheet is a table in an embedded{" "}
          <b>DuckDB</b> database, and formulas are turned into fast SQL queries.
        </p>
        <h3>The window</h3>
        <Table
          rows={[
            [<b key="m">Menus</b>, "File, Edit, View, Insert, Format, Data, Extensions and Help: every command lives here."],
            [<b key="t">Toolbar</b>, "Undo/redo, number formats, text styles, colours, borders, merge, alignment, freeze, filter, sort, functions, pivot, data tools and Python scripts. Hover any icon for its name and shortcut."],
            [<b key="f">Formula bar</b>, "Shows the cell address (type one, e.g. B12, to jump there) and the cell's value or formula. Click it to edit long formulas."],
            [<b key="g">Grid</b>, "Click a cell to select it, drag to select a range, double-click (or F2) to edit."],
            [<b key="s">Sheet tabs</b>, "At the bottom. + adds a sheet; right-click a tab to rename, duplicate or delete it, drag to reorder."],
            [<b key="c">Command palette</b>, <span key="cp">Press <K>{MOD}+K</K> and type what you want to do, e.g. "pivot" or "freeze".</span>],
          ]}
        />
        <h3>Row 1 is the header row</h3>
        <p>
          Each tab is a typed table: row 1 holds the column names and your data starts on row 2. That's why <C>A:A</C> or <C>A2:A</C> in formulas means "the whole column" and is fast even for millions of rows. Each column has a type (number, text, date, date &amp; time,
          checkbox). PySheets detects it, and you can change it from the column header menu.
        </p>
        <h3>Quick start</h3>
        <Steps
          items={[
            <span key="1">
              <b>Open data:</b> press <K>{MOD}+O</K>, click <b>Import</b>, or drag a file onto the window.
            </span>,
            <span key="2">
              <b>Explore:</b> filter with <K>{MOD}+Shift+L</K>, sort from the column menu, summarise with <b>Pivot</b>.
            </span>,
            <span key="3">
              <b>Calculate:</b> type <C>=</C> in a cell to write a formula. The help card explains each function as you type (<K>F1</K> hides it).
            </span>,
            <span key="4">
              <b>Save:</b> <K>{MOD}+S</K> saves a <C>.pysheets</C> workbook into <Path>Documents\PySheets</Path>.
            </span>,
            <span key="5">
              <b>Share:</b> <K>{MOD}+Shift+E</K> exports to Excel, CSV, Parquet, SQL and more.
            </span>,
          ]}
        />
      </>
    ),
  },
  {
    id: "import",
    title: "Importing data",
    icon: Upload,
    keywords: "open import csv excel xlsx parquet json sqlite duckdb database sql postgres mysql drag drop delimiter header",
    body: () => (
      <>
        <p>
          Open data with <K>{MOD}+O</K>, the <b>Import</b> button, <b>File → Open / import…</b>, or by dragging files onto the window. A preview shows the first rows and detected column types before anything is loaded.
        </p>
        <Table
          head={["Format", "Notes"]}
          rows={[
            ["CSV, TSV, TXT", "Delimiter, header row and encoding are detected automatically; you can change them in the preview. Multi-million-row files load in seconds."],
            ["Excel (.xlsx .xlsm .xls .xlsb .ods)", "Pick which tabs to import; each becomes its own sheet."],
            ["Parquet", "The fastest format for big data. Types are kept exactly."],
            ["JSON / NDJSON", "An array of objects or one object per line."],
            ["SQLite, DuckDB files", "Choose a table, or write a query."],
            ["Database servers", <span key="db">PostgreSQL, MySQL, SQL Server… through <b>File → Import from database…</b> with a connection URL (e.g. <C>postgresql://user:pass@host/db</C>) and a table or SQL query.</span>],
            [".pysheets", "Opens a saved PySheets workbook with all its tabs, formulas, formats, rules, links and scripts."],
            ["Google Sheets", <span key="g">See <a onClick={() => go("google")}>Google Sheets</a>: linked data you can refresh.</span>],
          ]}
        />
        <Tip>
          Importing several files at once puts each into its own tab. If a column mixes numbers and text it is stored as text; formulas still treat numeric-looking text as numbers.
        </Tip>
      </>
    ),
  },
  {
    id: "grid",
    title: "Working with the grid",
    icon: Grid3x3,
    keywords: "select edit enter f2 fill handle copy paste insert delete rows columns freeze resize autofit tabs undo redo checkbox dropdown",
    body: () => (
      <>
        <h3>Selecting and editing</h3>
        <ul className="dlist">
          <li>Type to replace a cell; <K>Enter</K> or <K>F2</K> (or double-click) edits the existing content. <K>Enter</K> confirms and moves down, <K>Tab</K> moves right, <K>Esc</K> cancels.</li>
          <li>
            <K>{MOD}+Arrow</K> jumps to the edge of the data; add <K>Shift</K> to select. <K>{MOD}+Space</K> / <K>Shift+Space</K> select the column / row. <K>{MOD}</K>-click adds more ranges.
          </li>
          <li>
            <K>Delete</K> clears contents; <b>Edit → Clear formatting</b> removes styles.
          </li>
        </ul>
        <h3>Filling and copying</h3>
        <ul className="dlist">
          <li>Drag the small square at the corner of the selection (the <b>fill handle</b>) to continue a series (1, 2, 3…, dates, "Item 1"…) or copy a formula. Double-click it to fill down the whole column.</li>
          <li>
            <K>{MOD}+D</K> fills down. Copy/paste works inside PySheets (formulas keep relative references) and with other apps (as text).
          </li>
        </ul>
        <h3>Rows, columns and sheets</h3>
        <ul className="dlist">
          <li>Right-click a row number or column letter to insert, delete, clear, sort, or change the column type. Drag a column edge to resize; double-click it to fit the data.</li>
          <li>
            <b>View → Freeze</b> (or the snowflake button) keeps header rows/columns visible while scrolling. <b>View → Row height</b> switches between compact, default and comfortable.
          </li>
          <li>
            <b>Insert → Checkbox / Dropdown</b> turns the selection into TRUE/FALSE boxes or a list of choices.
          </li>
          <li>
            Switch tabs with <K>{MOD}+PgUp</K> / <K>{MOD}+PgDn</K>. Every change can be undone with <K>{MOD}+Z</K> (redo <K>{MOD}+Y</K>).
          </li>
        </ul>
      </>
    ),
  },
  {
    id: "formulas",
    title: "Formulas",
    icon: Sigma,
    keywords: "formula reference range absolute relative column formula ctrl enter spill array criteria wildcard query errors iferror help card",
    body: () => (
      <>
        <p>
          Start with <C>=</C>. PySheets follows Google Sheets syntax and supports {Object.keys(ALL_DOCS).length} functions. See the <a onClick={() => go("functions")}>function list</a>. While you type, a help card shows the syntax with the current argument highlighted,
          an example and what every argument means. <K>F1</K> collapses it.
        </p>
        <h3>References</h3>
        <Table
          rows={[
            [<C key="a">A2</C>, "One cell."],
            [<C key="b">A2:C100</C>, "A range."],
            [<C key="c">A:A</C>, "A whole column (data rows, without the header)."],
            [<C key="d">A2:A</C>, "From A2 to the end of the column."],
            [<C key="e">$A$2</C>, "Absolute: stays fixed when copied or filled. $A2 / A$2 lock only the column / row."],
            [<C key="f">Sales!B:B</C>, <span key="f2">Another tab. Use quotes for names with spaces: <C>'Q1 data'!A2</C>. While editing, click cells, column headers or row headers (on this tab or another) to insert references such as <C>Sales!M:M</C>.</span>],
          ]}
        />
        <h3>Column formulas: one formula for millions of rows</h3>
        <p>
          Type a formula in the first data row and press <K>{MOD}+Enter</K> (or double-click the fill handle). It is applied to the whole column as a single database query: <C>=D2*E2</C> or <C>=COUNTIF(B:B, B2)</C> over 2,000,000 rows takes well under a second.
          Running totals like <C>=SUM($E$2:E2)</C> work too.
        </p>
        <h3>Arrays and spilling</h3>
        <p>
          Functions that return several values spill into the cells below/right, as in Sheets and Excel 365: <C>FILTER</C>, <C>SORT</C>, <C>UNIQUE</C>, <C>QUERY</C>, <C>SEQUENCE</C>, <C>SPLIT</C>, <C>TRANSPOSE</C>, and <C>ARRAYFORMULA(B2:B*C2:C)</C>. If something is in the way you get{" "}
          <C>#REF!</C>: clear those cells and it fills in.
        </p>
        <h3>Criteria (COUNTIF, SUMIF, …IFS)</h3>
        <Table
          rows={[
            [<C key="1">"North"</C>, "Equal to (not case-sensitive)."],
            [<C key="2">"&gt;100"</C>, <span key="2b">Comparisons: <C>&gt;</C> <C>&lt;</C> <C>&gt;=</C> <C>&lt;=</C> <C>&lt;&gt;</C>. Combine with a cell: <C>"&gt;"&amp;F1</C>.</span>],
            [<C key="3">"&lt;&gt;"</C>, "Not empty."],
            [<C key="4">"a*"</C>, <span key="4b">Wildcards: <C>*</C> any text, <C>?</C> one character.</span>],
          ]}
        />
        <h3>QUERY</h3>
        <p>
          <C>=QUERY(A1:D, "select B, sum(D) where C &gt; 5 group by B order by sum(D) desc limit 10")</C>. Columns are named by letter. Supported: <C>select</C>, <C>where</C> (with <C>and/or/not</C>, <C>contains</C>, <C>starts with</C>, <C>ends with</C>,{" "}
          <C>matches</C>, <C>like</C>, <C>is null</C>), <C>group by</C>, <C>pivot</C>, <C>order by</C>, <C>limit</C>, <C>offset</C>, <C>label</C>, and the aggregates <C>sum avg count min max</C>.
        </p>
        <h3>Lookups and format mismatches</h3>
        <p>
          A key can fail to match only because of its format: <C>123</C> vs the text <C>"00123"</C>, <C>"ABC-1 "</C> with a trailing or non-breaking space, <C>"1,234"</C> vs <C>1234</C>, or an invisible character pasted from the web. With <b>Smart matching</b> (the default) <C>VLOOKUP</C>,{" "}
          <C>XLOOKUP</C>, <C>MATCH</C>, <C>HLOOKUP</C>, the <C>COUNTIF</C>/<C>SUMIF</C> family and <C>FILTER</C> conditions see through these differences; exact matches always win. Cells that matched only this way get an <b>amber corner</b>, and a message after the formula runs tells you
          how many keys matched exactly, matched despite a format difference, or are really missing, so a blank result can be trusted. With <b>Strict matching</b> (Data → Check key matches…) only same-format values match and the amber corner marks keys that exist in another
          format. <C>CLEANKEY(value)</C> returns the normalised form used for this comparison.
        </p>
        <h3>Errors</h3>
        <p>
          A cell with an error shows a red triangle; hover it for the reason. Wrap formulas in <C>IFERROR(…, fallback)</C> or <C>IFNA(…)</C> to replace errors. In column formulas, errors become blank cells.
        </p>
        <Tip>
          You can add your own functions in Python, e.g. <C>=HHID_OK(C2)</C>. See <a onClick={() => go("python")}>Python scripts</a>.
        </Tip>
      </>
    ),
  },
  {
    id: "functions",
    title: "Function list",
    icon: Sigma,
    keywords: "",
    body: () => <FunctionList />,
  },
  {
    id: "data",
    title: "Data tools",
    icon: SlidersHorizontal,
    keywords: "filter sort pivot find replace duplicates validation dropdown clean trim split statistics profile visible hidden rows key match lookup mismatch normalise",
    body: () => (
      <>
        <Table
          head={["Tool", "How to use it"]}
          rows={[
            [
              <b key="f">Filter</b>,
              <span key="f2">
                <K>{MOD}+Shift+L</K> or the funnel button adds filter buttons to the header row. Filter by values (with counts), or by condition (greater than, contains, is empty…). Filters hide rows without deleting them.{" "}
                <b>While a filter is on, every edit affects only the rows you can see:</b> clearing, deleting rows, pasting (each line goes to the next visible row), copying, fill down / drag-fill, <K>{MOD}+Enter</K> column fills, formatting and text cleanup. Hidden rows are never touched.
              </span>,
            ],
            [<b key="s">Sort</b>, "Sort A → Z / Z → A buttons, or the column header menu. The whole sheet is sorted by the active column."],
            [<b key="p">Pivot table</b>, "Data → Pivot table…: pick rows, columns and values (SUM, COUNT, COUNTA, COUNTUNIQUE, AVERAGE, MIN, MAX, MEDIAN, STDEV). A live preview updates as you build it, and the pivot refreshes when the source data changes."],
            [<b key="r">Find & replace</b>, <span key="r2"><K>{MOD}+F</K> / <K>{MOD}+H</K>. Type, then press <K>Enter</K> to search (<K>Shift+Enter</K> for the previous match); a spinner shows while it runs. Options: match case, entire cell, regular expressions, all sheets.</span>],
            [<b key="d">Duplicates</b>, "Data → Find duplicates…: choose the columns that define a duplicate, then count, highlight, mark, or remove extra copies."],
            [<b key="v">Data validation</b>, "Data → Data validation…: dropdown lists, checkboxes, number/date/text-length rules, e-mail, URL, regex, unique values or a custom formula. Choose to warn or reject; the panel lists invalid cells."],
            [<b key="c">Clean up text</b>, "Data → Clean up text…: trim spaces, fix case, remove non-printing characters, in one step for a whole column."],
            [<b key="t">Split text to columns</b>, "Data → Split text to columns…: split a column by comma, semicolon, space or any custom separator."],
            [
              <b key="km">Check key matches</b>,
              "Data → Check key matches…: compare the column you look up with the column you look it up in. It counts exact matches, keys that match only once formats are ignored (numbers stored as text, extra spaces, leading zeros, separators, hidden characters) with the reason for each, and keys that are really missing. Normalise both columns to the same kind in one step (optionally keeping a copy of the original values), and switch lookup matching between Smart and Strict.",
            ],
            [<b key="st">Column statistics</b>, "Data → Column statistics: count, blanks, distinct values, min/max/mean and the most common values."],
          ]}
        />
      </>
    ),
  },
  {
    id: "format",
    title: "Formatting",
    icon: Paintbrush,
    keywords: "number format currency percent date decimals bold italic colour color fill alignment borders merge conditional formatting",
    body: () => (
      <>
        <ul className="dlist">
          <li>
            <b>Number formats:</b> the format dropdown on the toolbar: number, currency, percent, scientific, dates and times, plain text. <b>.0← / .00→</b> change decimal places. Custom patterns such as <C>#,##0.00</C> or <C>yyyy-mm-dd</C> are supported.
          </li>
          <li>
            <b>Text:</b> bold <K>{MOD}+B</K>, italic <K>{MOD}+I</K>, underline <K>{MOD}+U</K>, strikethrough; text colour and fill colour; left/centre/right alignment.
          </li>
          <li>
            <b>Borders:</b> the borders button: all, inner, outer, horizontal, vertical, each side or none, in thin, medium, thick, dashed, dotted or double lines and any colour.
          </li>
          <li>
            <b>Merge cells:</b> merge all, horizontally, vertically, or unmerge. Merged cells select and edit as one.
          </li>
          <li>
            <b>Conditional formatting:</b> Format → Conditional formatting…: colour cells by value comparisons, text, blanks, duplicates/unique, top/bottom N, above/below average, colour scales or a custom formula. Rules re-apply as data changes.
          </li>
        </ul>
      </>
    ),
  },
  {
    id: "google",
    title: "Google Sheets",
    icon: Cloud,
    keywords: "google sheets sign in login oauth credentials token consolidate directory refresh linked import service account",
    body: () => (
      <>
        <p>
          <b>Data → Google Sheets</b> brings data from Google Sheets into PySheets as <b>linked sheets</b> (cloud icon on the tab) that you refresh whenever you want. PySheets only reads; it never changes your Google Sheets.
        </p>
        <h3>1. Sign in</h3>
        <p>Google features stay locked until you sign in. Click <b>Sign in with Google</b>, choose your account in the browser and allow read-only access. PySheets continues automatically and remembers the sign-in.</p>
        <ul className="dlist">
          <li>
            The first time, PySheets may ask for an <b>OAuth client file</b> (<C>credentials.json</C>, a Google Cloud OAuth client of type <i>Desktop app</i>). You can reuse the one your Python scripts use.
          </li>
          <li>
            <b>Other ways to sign in:</b> reuse the <C>token.json</C> from your scripts' folder, or a service-account key (share each sheet with the service account's e-mail).
          </li>
          <li>Your account appears at the top of the dialog; the arrow button signs out.</li>
        </ul>
        <h3>2. Import one sheet</h3>
        <p>Paste a spreadsheet link, click <b>Load tabs</b>, pick tabs, and import. Each tab becomes a linked sheet.</p>
        <h3>3. Consolidate many sheets</h3>
        <p>Pull the same tab from a list of spreadsheets and stack them into one table, e.g. every cluster's <C>monitoring</C> tab listed in a master directory.</p>
        <Steps
          items={[
            "Choose where the list lives: a Google Sheet (e.g. the app_cluster_directory tab), a tab in this workbook, or pasted IDs/links.",
            "Name the ID column (and a link column as fallback), a label column (e.g. cluster_id), and the tabs to pull.",
            "Click Consolidate. Downloads run in parallel; headers are matched ignoring case, spaces and line breaks.",
            "You get one sheet per tab with source columns (_source_label, _source_spreadsheet_id, _source_row_number…) and a log tab with rows, missing tabs and column differences per spreadsheet.",
          ]}
        />
        <h3>Refreshing</h3>
        <p>
          The <b>Refresh</b> button (top right, on linked sheets) or <b>Data → Google Sheets → Refresh all data</b> pulls the latest values. Consolidations re-read the directory first, so newly listed spreadsheets are included. Columns you add yourself (checks,
          formulas) are kept and recalculated. If your sign-in has expired, PySheets asks you to sign in and then finishes the refresh.
        </p>
      </>
    ),
  },
  {
    id: "python",
    title: "Python scripts",
    icon: SquareTerminal,
    keywords: "python script macro extensions apps script custom function udf ps api pandas duckdb run file console enable trust",
    body: () => (
      <>
        <p>
          <b>Extensions → Python scripts</b> (or the <C>&gt;_</C> toolbar button) opens the script editor, like Apps Script but in Python. Scripts are saved inside the workbook. <K>{MOD}+S</K> saves, <K>{MOD}+Enter</K> runs, and the console shows output, errors and timing.
          Start from a template with <b>New script</b>.
        </p>
        <h3>Three kinds of script</h3>
        <Table
          rows={[
            [<b key="m">Macro</b>, <span key="m2">Python that works with your sheets through the <C>ps</C> object. <C>pd</C> (pandas) and <C>duckdb</C> are ready to use.</span>],
            [<b key="f">Custom functions</b>, <span key="f2">Functions marked with <C>@ps.function</C> become formula functions with autocomplete and a help card. Saving the script registers them.</span>],
            [<b key="r">Run a .py file</b>, "Runs one of your existing scripts (with arguments and a working folder) and streams its output. It uses the Python installed on your computer, so your packages work; otherwise the Python bundled with PySheets."],
          ]}
        />
        <h3>Macro example</h3>
        <Pre>{`
top = ps.sql('SELECT region, count(*) AS orders, sum(qty * unit_price) AS revenue FROM "sales" GROUP BY 1 ORDER BY 3 DESC')
print(top)
ps.new_sheet("Revenue by region", top)
ps.toast(f"Wrote {len(top)} rows")
`}</Pre>
        <h3>Custom function example</h3>
        <Pre>{`
import re

@ps.function
def HHID_OK(hhid: str) -> bool:
    """TRUE when the household ID is 18 digits."""
    return bool(re.fullmatch(r"\\d{18}", (hhid or "").strip()))

@ps.function(vectorized=True)           # receives whole columns: fast on millions of rows
def PERCENT_OF(part: float, whole: float) -> float:
    return part / whole.where(whole != 0) * 100
`}</Pre>
        <p>
          Then use <C>=HHID_OK(C2)</C> in a cell (or as a column formula with <K>{MOD}+Enter</K>). Type hints (<C>str float int bool date</C>) set the argument and result types; the docstring becomes the help text.
        </p>
        <h3>The ps object</h3>
        <Table
          head={["Call", "What it does"]}
          rows={[
            [<C key="1">ps.sheets()</C>, "Names of all tabs."],
            [<C key="2">ps.sql(query)</C>, <span key="2b">Run DuckDB SQL; every tab is a table named after it (<C>"Sales"</C>). Returns a pandas DataFrame.</span>],
            [<C key="3">ps.sheet(name).df()</C>, "A tab as a DataFrame (row 1 = column names)."],
            [<C key="4">ps.new_sheet(name, df)</C>, "Write a DataFrame to a new (or replaced) tab."],
            [<C key="5">sh.get("B2") / sh.set("B2", value)</C>, <span key="5b">Read or write one cell; strings starting with <C>=</C> are formulas.</span>],
            [<C key="6">sh.write(df, "A1") / sh.append(df) / sh.replace(df) / sh.clear()</C>, "Write at a position, append rows (columns matched by header), replace the whole tab, or clear it."],
            [<C key="7">ps.read(path)</C>, "Read a CSV, Parquet, JSON or Excel file into a DataFrame."],
            [<C key="8">ps.gsheet(link, tab)</C>, "Read a Google Sheets tab (needs Google sign-in)."],
            [<C key="9">ps.refresh_all()</C>, "Refresh every linked / consolidated sheet."],
            [<C key="10">ps.run_file(path, *args)</C>, "Run another .py file; its output appears in the console."],
            [<C key="11">ps.progress(i, n, label) / ps.toast(msg)</C>, "Show progress in the top bar / a message."],
          ]}
        />
        <Tip>
          <b>Security:</b> scripts can do anything a Python program can on your computer. Workbooks you receive from others open with scripts <b>off</b>; review the code and click <b>Enable scripts</b> only if you trust it.
        </Tip>
      </>
    ),
  },
  {
    id: "export",
    title: "Exporting & saving",
    icon: Download,
    keywords: "export save excel csv parquet json sqlite duckdb sql database pysheets workbook documents folder exports",
    body: () => (
      <>
        <h3>Saving workbooks</h3>
        <p>
          <K>{MOD}+S</K> saves a <C>.pysheets</C> workbook, a single file with every tab, formula, format, rule, Google link and script. New workbooks are saved in <Path>Documents\PySheets</Path> (created automatically); the Save and Open dialogs start there.{" "}
          <b>File → Open PySheets folder</b> shows it in File Explorer. PySheets doesn't autosave; the title shows a dot (•) when there are unsaved changes.
        </p>
        <h3>Exporting</h3>
        <p>
          <K>{MOD}+Shift+E</K> or <b>Export</b>: choose this tab or the whole workbook, and a format. Exports are saved in <Path>Documents\PySheets\Exports</Path> by default.
        </p>
        <Table
          head={["Format", "Good for"]}
          rows={[
            ["Excel (.xlsx)", "Sharing with Excel users. Sheets over 1,048,576 rows are split across several tabs automatically. Values are exported (not formulas)."],
            ["CSV / TSV", "Plain text for any tool."],
            ["Parquet", "Big data: compact, fast, keeps types."],
            ["JSON", "Web apps and APIs (array of row objects)."],
            ["SQLite / DuckDB", "A database file with one table per sheet."],
            ["SQL script", "CREATE TABLE + INSERT statements."],
            ["Database server", "Straight into PostgreSQL, MySQL, SQL Server… with a connection URL."],
          ]}
        />
      </>
    ),
  },
  {
    id: "files",
    title: "Files & folders",
    icon: FolderOpen,
    keywords: "documents pysheets folder exports location where saved credentials google",
    body: () => (
      <Table
        rows={[
          [<Path key="1">Documents\PySheets</Path>, "Your saved workbooks (.pysheets)."],
          [<Path key="2">Documents\PySheets\Exports</Path>, "Exported files."],
          [<Path key="3">%LOCALAPPDATA%\PySheets\google</Path>, "Your Google sign-in (stays on this computer; Sign out deletes it)."],
        ]}
      />
    ),
  },
  {
    id: "shortcuts",
    title: "Keyboard shortcuts",
    icon: Keyboard,
    keywords: "keyboard shortcuts keys hotkeys",
    body: () => (
      <Table
        rows={[
          ...SHORTCUTS.map(([a, b]) => [a, <K key={a}>{b}</K>]),
          ["Documentation", <K key="f1">F1</K>],
          ["Show / hide formula help while typing", <K key="f1b">F1</K>],
          ["Run script / save script (in the script editor)", <K key="se">{`${MOD}+Enter / ${MOD}+S`}</K>],
        ]}
      />
    ),
  },
  {
    id: "updates",
    title: "Updates",
    icon: ArrowUpCircle,
    keywords: "update version check latest release install upgrade about",
    body: () => (
      <>
        <p>
          PySheets never updates on its own. Open <b>Help → Check for updates…</b> (or <b>About PySheets</b>) to see your version and check GitHub for a newer release.
        </p>
        <ul className="dlist">
          <li>Up to date: you'll see "Your app is on the latest version".</li>
          <li>
            New version: click <b>Update to x.y.z</b>. PySheets downloads the installer, verifies it, offers to save your work, then installs it and reopens itself.
          </li>
          <li>The portable (zip) version shows a download link instead.</li>
        </ul>
        <p>
          <a onClick={() => openDialog("about", { check: true })}>Check for updates now →</a>
        </p>
      </>
    ),
  },
];

/* ----------------------------------------------------------- function list */

const CATEGORY_ORDER = ["Math", "Statistical", "Lookup", "Logical", "Text", "Date", "Array", "Info"];

function FnCard({ name, doc }: { name: string; doc: FnDoc }) {
  const tokens = syntaxArgs(doc.syntax);
  let inBrackets = false;
  const rows = tokens
    .map((t, i) => {
      if (t.startsWith("[")) inBrackets = true;
      const r = { i, name: argName(t), optional: inBrackets, desc: doc.args[i] };
      if (t.endsWith("]")) inBrackets = false;
      return r;
    })
    .filter((r) => r.desc && r.name !== "…");
  return (
    <div className="dfn" id={`fn-${name}`}>
      <div className="dfn-head">
        <code>{doc.syntax}</code>
      </div>
      <p>{doc.summary}</p>
      <div className="dfn-ex">
        <span>Example</span>
        <code>={doc.example}</code>
      </div>
      {rows.length > 0 && (
        <dl>
          {rows.map((r) => (
            <div key={r.i}>
              <dt>
                {r.name}
                {r.optional && <span className="fh-opt">optional</span>}
              </dt>
              <dd>{r.desc}</dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  );
}

function FunctionList() {
  const [q, setQ] = useState(docsState.fnQuery);
  const [cat, setCat] = useState<string | null>(null);
  const custom = useApp((s) => s.info?.custom_function_docs) || {};
  const entries = useMemo(() => {
    const all = Object.keys(ALL_DOCS).map((n) => {
      const [category, syntax, summary, example, args] = ALL_DOCS[n];
      return { name: n, doc: { category, syntax, summary, example, args } as FnDoc };
    });
    for (const [n, c] of Object.entries(custom)) {
      all.push({ name: n, doc: { category: "Custom (Python)", syntax: `${n}(${c.params.join(", ")})`, summary: c.doc || "Custom function defined in a Python script.", example: `${n}(${c.params.map((_, i) => `${String.fromCharCode(65 + i)}2`).join(", ")})`, args: [] } });
    }
    return all;
  }, [custom]);
  const needle = q.trim().toLowerCase();
  const shown = entries.filter((e) => (!cat || e.doc.category === cat) && (!needle || e.name.toLowerCase().includes(needle) || e.doc.summary.toLowerCase().includes(needle)));
  const cats = [...CATEGORY_ORDER, ...(Object.keys(custom).length ? ["Custom (Python)"] : [])];

  useEffect(() => {
    if (!docsState.fn) return;
    const name = docsState.fn;
    // after the dialog's own "scroll to top on section change" has run
    const raf = requestAnimationFrame(() => {
      const el = document.getElementById(`fn-${name}`);
      el?.scrollIntoView({ block: "start" });
      el?.classList.add("flash");
      docsState.fn = null;
    });
    return () => cancelAnimationFrame(raf);
  }, []);

  return (
    <>
      <p>
        {entries.length} functions. Every one also has a help card while you type it in a cell. Names follow Google Sheets, so most formulas you know work as-is.
      </p>
      <div className="dfn-tools">
        <div className="dsearch small">
          <Search size={14} />
          <input value={q} placeholder="Filter functions, e.g. lookup or date" onChange={(e) => setQ(e.target.value)} />
        </div>
        <div className="chips wrap">
          <button className={`chip ${cat === null ? "on" : ""}`} onClick={() => setCat(null)}>
            All
          </button>
          {cats.map((c) => (
            <button key={c} className={`chip ${cat === c ? "on" : ""}`} onClick={() => setCat(cat === c ? null : c)}>
              {c}
            </button>
          ))}
        </div>
      </div>
      {cats
        .filter((c) => shown.some((e) => e.doc.category === c))
        .map((c) => (
          <div key={c}>
            <h3>{c}</h3>
            {shown
              .filter((e) => e.doc.category === c)
              .sort((a, b) => a.name.localeCompare(b.name))
              .map((e) => (
                <FnCard key={e.name} name={e.name} doc={e.doc} />
              ))}
          </div>
        ))}
      {shown.length === 0 && <p className="muted">No function matches "{q}".</p>}
    </>
  );
}

/* ----------------------------------------------------------------- dialog */

// small module state so links inside sections can navigate
const docsState: { go: ((id: string) => void) | null; fn: string | null; fnQuery: string } = { go: null, fn: null, fnQuery: "" };
function go(id: string) {
  docsState.go?.(id);
}

export function DocsDialog() {
  const props = useApp((s) => s.dialogProps) as { section?: string; fn?: string };
  const [section, setSection] = useState(props.fn ? "functions" : props.section || "start");
  const [query, setQuery] = useState("");
  const content = useRef<HTMLDivElement>(null);
  if (props.fn) docsState.fn = props.fn.toUpperCase();

  useEffect(() => {
    docsState.go = (id) => {
      setQuery("");
      setSection(id);
    };
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && closeDialog();
    window.addEventListener("keydown", onKey);
    return () => {
      docsState.go = null;
      window.removeEventListener("keydown", onKey);
    };
  }, []);
  useEffect(() => {
    if (!docsState.fn) content.current?.scrollTo({ top: 0 });
  }, [section]);

  const needle = query.trim().toLowerCase();
  const sectionHits = needle ? SECTIONS.filter((s) => s.id !== "functions" && (s.title + " " + s.keywords).toLowerCase().includes(needle)) : [];
  const fnHits = needle
    ? Object.keys(ALL_DOCS)
        .filter((n) => n.toLowerCase().includes(needle) || ALL_DOCS[n][2].toLowerCase().includes(needle))
        .slice(0, 12)
    : [];
  const current = SECTIONS.find((s) => s.id === section) || SECTIONS[0];

  return (
    <div className="overlay fade-in" onMouseDown={closeDialog}>
      <div className="dialog docs scale-in" onMouseDown={(e) => e.stopPropagation()}>
        <aside className="docs-nav">
          <div className="docs-brand">
            <BookOpen size={18} /> Documentation
          </div>
          <div className="dsearch">
            <Search size={14} />
            <input autoFocus value={query} placeholder="Search the docs" onChange={(e) => setQuery(e.target.value)} />
          </div>
          {needle ? (
            <div className="docs-results">
              {sectionHits.map((s) => (
                <button key={s.id} className="docs-link" onClick={() => go(s.id)}>
                  <s.icon size={15} /> {s.title}
                </button>
              ))}
              {fnHits.length > 0 && <div className="docs-sub">Functions</div>}
              {fnHits.map((n) => (
                <button
                  key={n}
                  className="docs-link fn"
                  onClick={() => {
                    docsState.fn = n;
                    setQuery("");
                    if (section === "functions") {
                      const el = document.getElementById(`fn-${n}`);
                      el?.scrollIntoView({ block: "start", behavior: "smooth" });
                      el?.classList.remove("flash");
                      void el?.offsetWidth;
                      el?.classList.add("flash");
                      docsState.fn = null;
                    } else setSection("functions");
                  }}
                >
                  <code>{n}</code>
                  <span>{ALL_DOCS[n][2]}</span>
                </button>
              ))}
              {sectionHits.length === 0 && fnHits.length === 0 && <div className="muted docs-none">No results for "{query}"</div>}
            </div>
          ) : (
            <nav>
              {SECTIONS.map((s) => (
                <button key={s.id} className={`docs-link ${s.id === section ? "on" : ""}`} onClick={() => setSection(s.id)}>
                  <s.icon size={15} /> {s.title}
                </button>
              ))}
            </nav>
          )}
        </aside>
        <main className="docs-main" ref={content}>
          <button className="icon-btn docs-close" onClick={closeDialog} aria-label="Close">
            <X size={18} />
          </button>
          <article className="docs-article fade-in" key={current.id}>
            <h2>
              <current.icon size={22} /> {current.title}
            </h2>
            {current.body()}
          </article>
        </main>
      </div>
    </div>
  );
}
