import * as A from "./actions";
import { exportDialog, newWorkbook, openFile, openPySheetsFolder, saveWorkbook, saveWorkbookAs } from "./fileops";
import { enableScripts, openScripts, runScript } from "./scripts";
import { colLetters } from "./format";
import type { Hit } from "./grid/GridController";
import { MOD } from "./shortcuts";
import { call, currentSheet, openDialog, openPanel, primaryRect, setTheme, useApp, type MenuItem } from "./store";

function sheetIdOrNull() {
  return currentSheet()?.id;
}

export function cellMenu(hit: Hit): MenuItem[] {
  const sh = currentSheet();
  const c = hit.c;
  const cell = useApp.getState().cell;
  const isFormula = !!cell?.input?.startsWith("=");
  const rect = primaryRect();
  const nRows = rect.r2 - rect.r1 + 1;
  const nCols = rect.c2 - rect.c1 + 1;
  const items: MenuItem[] = [
    { label: "Cut", icon: "scissors", shortcut: `${MOD}+X`, run: () => A.copySelection(true) },
    { label: "Copy", icon: "copy", shortcut: `${MOD}+C`, run: () => A.copySelection(false) },
    {
      label: "Paste",
      icon: "clipboard",
      shortcut: `${MOD}+V`,
      run: async () => {
        try {
          A.pasteText(await navigator.clipboard.readText());
        } catch {
          const clip = useApp.getState().clip;
          if (clip) A.pasteText(clip.text);
        }
      },
    },
    { divider: true },
    { label: `Insert ${nRows > 1 ? nRows + " rows" : "row"} above`, icon: "rows", run: () => A.insertRows("above"), disabled: hit.r < 1 },
    { label: `Insert ${nRows > 1 ? nRows + " rows" : "row"} below`, icon: "rows", run: () => A.insertRows("below") },
    { label: `Insert ${nCols > 1 ? nCols + " columns" : "column"} left`, icon: "columns", run: () => A.insertCols("left") },
    { label: `Insert ${nCols > 1 ? nCols + " columns" : "column"} right`, icon: "columns", run: () => A.insertCols("right") },
    {
      label: A.isFiltered() ? `Delete ${nRows > 1 ? nRows + " visible rows" : "row"}` : nRows > 1 ? `Delete rows ${A.sheetRow(rect.r1) + 1}–${A.sheetRow(rect.r2) + 1}` : "Delete row",
      icon: "trash",
      danger: true,
      run: () => A.deleteRows(),
      disabled: hit.r < 1,
    },
    { label: nCols > 1 ? "Delete columns" : `Delete column ${colLetters(c)}`, icon: "trash", danger: true, run: () => A.deleteCols() },
    { divider: true },
    { label: "Sort sheet A → Z", icon: "sort-asc", run: () => A.sortBy(c, true) },
    { label: "Sort sheet Z → A", icon: "sort-desc", run: () => A.sortBy(c, false) },
    {
      label: "Filter by this value",
      icon: "filter",
      run: () => {
        const v = A.getGrid()?.value(hit.r, c);
        A.setFilter(c, { include: [v ?? null] });
      },
      disabled: hit.r < 1,
    },
    { divider: true },
  ];
  if (isFormula && (cell?.row ?? 0) >= 1) {
    items.push({ label: "Apply formula to whole column", icon: "function", shortcut: `${MOD}+Enter`, run: () => A.fillColumn() });
  }
  items.push(
    { label: "Clear contents", icon: "eraser", shortcut: "Del", run: () => A.clearSelection("contents") },
    { label: "Clear formatting", icon: "eraser", run: () => A.clearSelection("formats") },
    { divider: true },
    {
      label: "Freeze",
      icon: "snowflake",
      items: [
        { label: "No rows", run: () => A.freezeAt(0, null) },
        { label: "Header row", run: () => A.freezeAt(1, null) },
        { label: `Up to row ${A.sheetRow(hit.r) + 1}`, run: () => A.freezeAt(Math.max(1, hit.r + 1), null), disabled: !!sh?.filtered },
        { divider: true },
        { label: "No columns", run: () => A.freezeAt(null, 0) },
        { label: `Up to column ${colLetters(c)}`, run: () => A.freezeAt(null, c + 1) },
      ],
    },
    { label: "Data validation…", icon: "shield", run: () => openPanel("dv") },
    { label: "Conditional formatting…", icon: "palette", run: () => openPanel("cf") },
  );
  return items;
}

export function colHeaderMenu(c: number): MenuItem[] {
  const sh = currentSheet();
  const col = sh?.columns[c];
  const rect = primaryRect();
  const n = rect.c2 - rect.c1 + 1;
  return [
    { label: "Cut", icon: "scissors", run: () => A.copySelection(true) },
    { label: "Copy", icon: "copy", run: () => A.copySelection(false) },
    { divider: true },
    { label: `Insert ${n > 1 ? n + " columns" : "column"} left`, icon: "columns", run: () => A.insertCols("left") },
    { label: `Insert ${n > 1 ? n + " columns" : "column"} right`, icon: "columns", run: () => A.insertCols("right") },
    { label: n > 1 ? `Delete ${n} columns` : `Delete column ${colLetters(c)}`, icon: "trash", danger: true, run: () => A.deleteCols() },
    { label: "Clear column", icon: "eraser", run: () => A.clearSelection("contents") },
    { divider: true },
    { label: "Sort sheet A → Z", icon: "sort-asc", run: () => A.sortBy(c, true) },
    { label: "Sort sheet Z → A", icon: "sort-desc", run: () => A.sortBy(c, false) },
    { label: sh?.filter_on ? "Remove filter" : "Create a filter", icon: "filter", run: () => A.toggleFilter() },
    { divider: true },
    {
      label: "Column type",
      icon: "type",
      items: [
        { label: "Number", checked: col?.type === "num", run: () => call("col.type", { sheet: sh?.id, col: c, type: "DOUBLE" }).then(A.invalidate) },
        { label: "Text", checked: col?.type === "str", run: () => call("col.type", { sheet: sh?.id, col: c, type: "VARCHAR" }).then(A.invalidate) },
        { label: "Date", checked: col?.type === "date", run: () => call("col.type", { sheet: sh?.id, col: c, type: "DATE" }).then(A.invalidate) },
        { label: "Date & time", checked: col?.type === "ts", run: () => call("col.type", { sheet: sh?.id, col: c, type: "TIMESTAMP" }).then(A.invalidate) },
        { label: "Checkbox (TRUE/FALSE)", checked: col?.type === "bool", run: () => call("col.type", { sheet: sh?.id, col: c, type: "BOOLEAN" }).then(A.invalidate) },
      ],
    },
    { label: "Column statistics", icon: "chart", run: () => openPanel("profile", { col: c }) },
    { label: "Find duplicates…", icon: "copy-check", run: () => openPanel("dups", { cols: [c] }) },
    { label: "Clean up text…", icon: "sparkles", run: () => openPanel("clean") },
    { label: "Split text to columns…", icon: "split", run: () => openDialog("split", { col: c }) },
    { divider: true },
    { label: "Fit to data", icon: "move-h", run: () => A.autofit(c) },
    { label: `Freeze up to column ${colLetters(c)}`, icon: "snowflake", run: () => A.freezeAt(null, c + 1) },
  ];
}

export function rowHeaderMenu(): MenuItem[] {
  const rect = primaryRect();
  const n = rect.r2 - rect.r1 + 1;
  return [
    { label: "Cut", icon: "scissors", run: () => A.copySelection(true) },
    { label: "Copy", icon: "copy", run: () => A.copySelection(false) },
    { divider: true },
    { label: `Insert ${n > 1 ? n + " rows" : "row"} above`, icon: "rows", run: () => A.insertRows("above"), disabled: rect.r1 < 1 },
    { label: `Insert ${n > 1 ? n + " rows" : "row"} below`, icon: "rows", run: () => A.insertRows("below") },
    { label: n > 1 ? `Delete ${n}${A.isFiltered() ? " visible" : ""} rows` : "Delete row", icon: "trash", danger: true, run: () => A.deleteRows(), disabled: rect.r1 < 1 },
    { label: "Clear row", icon: "eraser", run: () => A.clearSelection("contents") },
    { divider: true },
    { label: `Freeze up to row ${A.sheetRow(rect.r2) + 1}`, icon: "snowflake", run: () => A.freezeAt(rect.r2 + 1, null) },
  ];
}

export interface TopMenu {
  label: string;
  items: () => MenuItem[];
}

export const TOP_MENUS: TopMenu[] = [
  {
    label: "File",
    items: () => [
      { label: "New spreadsheet", icon: "file-plus", run: () => newWorkbook() },
      { label: "Open / import…", icon: "folder-open", shortcut: `${MOD}+O`, run: () => openFile() },
      { label: "Import from database…", icon: "database", run: () => openDialog("sql") },
      { divider: true },
      { label: "Save", icon: "save", shortcut: `${MOD}+S`, run: () => saveWorkbook() },
      { label: "Save as…", icon: "save", shortcut: `${MOD}+Shift+S`, run: () => saveWorkbookAs() },
      { divider: true },
      { label: "Export…", icon: "download", shortcut: `${MOD}+Shift+E`, run: () => exportDialog() },
      { divider: true },
      { label: "Open PySheets folder", icon: "folder-open", run: () => openPySheetsFolder() },
    ],
  },
  {
    label: "Edit",
    items: () => {
      const info = useApp.getState().info;
      return [
        { label: info?.undo_label ? `Undo ${info.undo_label.toLowerCase()}` : "Undo", icon: "undo", shortcut: `${MOD}+Z`, run: () => A.undo(), disabled: !info?.can_undo },
        { label: info?.redo_label ? `Redo ${info.redo_label.toLowerCase()}` : "Redo", icon: "redo", shortcut: `${MOD}+Y`, run: () => A.redo(), disabled: !info?.can_redo },
        { divider: true },
        { label: "Cut", icon: "scissors", shortcut: `${MOD}+X`, run: () => A.copySelection(true) },
        { label: "Copy", icon: "copy", shortcut: `${MOD}+C`, run: () => A.copySelection(false) },
        { divider: true },
        { label: "Find", icon: "search", shortcut: `${MOD}+F`, run: () => A.openFind("find") },
        { label: "Find and replace", icon: "replace", shortcut: `${MOD}+H`, run: () => A.openFind("replace") },
        { divider: true },
        { label: "Fill down", icon: "arrow-down", shortcut: `${MOD}+D`, run: () => A.fillDown() },
        { label: "Apply formula to column", icon: "function", shortcut: `${MOD}+Enter`, run: () => A.fillColumn() },
        { label: "Clear contents", icon: "eraser", shortcut: "Del", run: () => A.clearSelection("contents") },
      ];
    },
  },
  {
    label: "View",
    items: () => {
      const sh = currentSheet();
      const theme = useApp.getState().theme;
      return [
        {
          label: "Freeze",
          icon: "snowflake",
          items: [
            { label: "No rows", checked: sh?.frozen_rows === 0, run: () => A.freezeAt(0, null) },
            { label: "1 row", checked: sh?.frozen_rows === 1, run: () => A.freezeAt(1, null) },
            { label: "2 rows", checked: sh?.frozen_rows === 2, run: () => A.freezeAt(2, null) },
            { label: `Up to current row`, run: () => A.freezeAt(useApp.getState().sel.active.r + 1, null) },
            { divider: true },
            { label: "No columns", checked: sh?.frozen_cols === 0, run: () => A.freezeAt(null, 0) },
            { label: "1 column", checked: sh?.frozen_cols === 1, run: () => A.freezeAt(null, 1) },
            { label: "2 columns", checked: sh?.frozen_cols === 2, run: () => A.freezeAt(null, 2) },
            { label: "Up to current column", run: () => A.freezeAt(null, useApp.getState().sel.active.c + 1) },
          ],
        },
        {
          label: "Row height",
          icon: "rows",
          items: [
            { label: "Compact", checked: sh?.row_height === 22, run: () => call("sheet.props", { sheet: sheetIdOrNull(), row_height: 22 }) },
            { label: "Default", checked: sh?.row_height === 26, run: () => call("sheet.props", { sheet: sheetIdOrNull(), row_height: 26 }) },
            { label: "Comfortable", checked: sh?.row_height === 32, run: () => call("sheet.props", { sheet: sheetIdOrNull(), row_height: 32 }) },
          ],
        },
        { divider: true },
        { label: theme === "dark" ? "Light theme" : "Dark theme", icon: theme === "dark" ? "sun" : "moon", run: () => setTheme(theme === "dark" ? "light" : "dark") },
        { label: "Command palette", icon: "command", shortcut: `${MOD}+K`, run: () => useApp.setState({ palette: true }) },
      ];
    },
  },
  {
    label: "Insert",
    items: () => [
      { label: "Rows above", icon: "rows", run: () => A.insertRows("above") },
      { label: "Rows below", icon: "rows", run: () => A.insertRows("below") },
      { label: "Columns left", icon: "columns", run: () => A.insertCols("left") },
      { label: "Columns right", icon: "columns", run: () => A.insertCols("right") },
      { divider: true },
      { label: "New sheet", icon: "plus", run: () => call("sheet.add", {}) },
      { label: "Pivot table…", icon: "table", run: () => A.openPivot() },
      { divider: true },
      {
        label: "Function",
        icon: "function",
        items: ["SUM", "AVERAGE", "COUNT", "MAX", "MIN", "IF", "VLOOKUP", "XLOOKUP", "INDEX", "MATCH", "FILTER", "QUERY", "UNIQUE", "SORT", "ARRAYFORMULA", "TRIM"].map((f) => ({
          label: f,
          run: () => A.startEdit(`=${f}(`),
        })),
      },
      { label: "Checkbox", icon: "check-square", run: () => insertCheckbox() },
      { label: "Dropdown", icon: "list", run: () => openPanel("dv", { kind: "list" }) },
    ],
  },
  {
    label: "Format",
    items: () => [
      { label: "Bold", icon: "bold", shortcut: `${MOD}+B`, run: () => A.toggleStyle("bold") },
      { label: "Italic", icon: "italic", shortcut: `${MOD}+I`, run: () => A.toggleStyle("italic") },
      { label: "Underline", icon: "underline", shortcut: `${MOD}+U`, run: () => A.toggleStyle("underline") },
      { label: "Strikethrough", icon: "strike", shortcut: "Alt+Shift+5", run: () => A.toggleStyle("strike") },
      { divider: true },
      {
        label: "Borders",
        icon: "table",
        items: [
          { label: "All borders", run: () => A.applyBorders("all") },
          { label: "Outer borders", run: () => A.applyBorders("outer") },
          { label: "Inner borders", run: () => A.applyBorders("inner") },
          { label: "Thick outer border", run: () => A.applyBorders("outer", "thick") },
          { label: "Bottom border", run: () => A.applyBorders("bottom") },
          { divider: true },
          { label: "Clear borders", run: () => A.applyBorders("none") },
        ],
      },
      {
        label: "Merge cells",
        icon: "columns",
        items: [
          { label: "Merge all", run: () => A.mergeCells("all") },
          { label: "Merge horizontally", run: () => A.mergeCells("horizontal") },
          { label: "Merge vertically", run: () => A.mergeCells("vertical") },
          { divider: true },
          { label: "Unmerge", run: () => A.mergeCells("unmerge") },
        ],
      },
      { divider: true },
      { label: "Conditional formatting…", icon: "palette", run: () => openPanel("cf") },
      { label: "Clear formatting", icon: "eraser", run: () => A.clearSelection("formats") },
    ],
  },
  {
    label: "Data",
    items: () => {
      const sh = currentSheet();
      const c = useApp.getState().sel.active.c;
      return [
        { label: `Sort sheet by ${colLetters(c)} (A → Z)`, icon: "sort-asc", run: () => A.sortBy(c, true) },
        { label: `Sort sheet by ${colLetters(c)} (Z → A)`, icon: "sort-desc", run: () => A.sortBy(c, false) },
        { divider: true },
        { label: sh?.filter_on ? "Remove filter" : "Create a filter", icon: "filter", shortcut: `${MOD}+Shift+L`, run: () => A.toggleFilter() },
        { label: "Pivot table…", icon: "table", run: () => A.openPivot() },
        { divider: true },
        { label: "Data validation…", icon: "shield", run: () => openPanel("dv") },
        { label: "Find duplicates…", icon: "copy-check", run: () => openPanel("dups", { cols: [c] }) },
        { label: "Clean up text…", icon: "sparkles", run: () => openPanel("clean") },
        { label: "Split text to columns…", icon: "split", run: () => openDialog("split", { col: c }) },
        { label: "Column statistics", icon: "chart", run: () => openPanel("profile", { col: c }) },
        { label: "Check key matches…", icon: "search", run: () => openDialog("match") },
        { divider: true },
        {
          label: "Google Sheets",
          icon: "table",
          items: [
            { label: "Consolidate many sheets…", run: () => openDialog("google", { tab: "consolidate" }) },
            { label: "Import one sheet…", run: () => openDialog("google", { tab: "import" }) },
            { label: "Google account…", run: () => openDialog("google") },
          ],
        },
        { label: "Refresh this sheet", icon: "redo", disabled: !sh?.link, run: () => A.refreshLinked() },
        { label: "Refresh all data", icon: "redo", run: () => A.refreshAll() },
        { label: "Import from database…", icon: "database", run: () => openDialog("sql") },
      ];
    },
  },
  {
    label: "Extensions",
    items: () => {
      const info = useApp.getState().info;
      const macros = (info?.scripts || []).filter((s) => s.kind !== "functions");
      return [
        { label: "Python scripts…", icon: "function", run: () => openScripts() },
        ...(info && !info.scripts_trusted ? [{ label: "Enable scripts in this workbook", icon: "shield", run: () => enableScripts() }] : []),
        ...(macros.length ? [{ divider: true } as MenuItem] : []),
        ...macros.map((m) => ({ label: `Run: ${m.name}`, icon: "sparkles", run: () => runScript(m.id) })),
        ...((info?.custom_functions || []).length
          ? [{ divider: true } as MenuItem, { label: `Custom functions: ${info!.custom_functions.join(", ")}`, disabled: true }]
          : []),
      ];
    },
  },
  {
    label: "Help",
    items: () => [
      { label: "Documentation", icon: "book-open", shortcut: "F1", run: () => openDialog("docs") },
      { label: "Function list", icon: "sigma", run: () => openDialog("docs", { section: "functions" }) },
      { label: "Keyboard shortcuts", icon: "keyboard", shortcut: `${MOD}+/`, run: () => openDialog("shortcuts") },
      { divider: true },
      { label: "Check for updates…", icon: "refresh-cw", run: () => openDialog("about", { check: true }) },
      { label: "About PySheets", icon: "info", run: () => openDialog("about") },
    ],
  },
];

async function insertCheckbox() {
  const sh = currentSheet();
  if (!sh) return;
  const rect = A.toSheetRect(primaryRect());
  const cols: string[] = [];
  for (let c = rect.c1; c <= rect.c2; c++) {
    while (sh.columns.length <= c) break;
    if (sh.columns[c]) cols.push(sh.columns[c].id);
  }
  if (!cols.length) return;
  await call("dv.add", { sheet: sh.id, rule: { kind: "checkbox", cols, r1: Math.max(1, rect.r1), r2: rect.r2 >= sh.nrows ? null : rect.r2 } });
  A.invalidate();
}
