import { rpc } from "./api";
import { bumpDecimals, cellRef, colLetters } from "./format";
import { BLOCK_C, BLOCK_R, type GridController } from "./grid/GridController";
import {
  call,
  currentSheet,
  openDialog,
  openPanel,
  primaryRect,
  query,
  selectSheet,
  toast,
  useApp,
  type EditorState,
} from "./store";
import type { CellInfo, CellStyle, Rect, Selection } from "./types";

let grid: GridController | null = null;
export function setGrid(g: GridController | null) {
  grid = g;
  // handy for debugging and end-to-end tests
  (window as unknown as { __pysheetsGrid?: GridController | null }).__pysheetsGrid = g;
}
export function getGrid() {
  return grid;
}

const get = useApp.getState;
const set = useApp.setState;

// ------------------------------------------------------------------- helpers

export function sheetRow(r: number): number {
  return grid ? grid.sheetRow(r) : r;
}

/** Convert a visual rectangle to sheet rows (identity unless a filter is active). */
export function toSheetRect(rect: Rect): Rect {
  const sh = currentSheet();
  const lastRow = sh ? (sh.filtered ? sh.nview : sh.nrows + 100) : rect.r2;
  const r2 = Math.min(rect.r2, lastRow);
  const c2 = Math.min(rect.c2, (sh?.ncols ?? 26) + 50);
  if (!sh?.filtered) return { r1: rect.r1, c1: rect.c1, r2, c2 };
  const a = sheetRow(rect.r1);
  const b = sheetRow(Math.min(r2, grid ? grid.totalRows - 1 : r2));
  return { r1: a < 0 ? 0 : a, c1: rect.c1, r2: b < 0 ? sh.nrows : b, c2 };
}

export function selectionRects(): Rect[] {
  return get().sel.ranges.map(toSheetRect);
}

function sheetId(): string {
  return currentSheet()!.id;
}

export function invalidate() {
  grid?.invalidateData();
}

export async function refreshCell() {
  const { sel } = get();
  const sh = currentSheet();
  if (!sh) return;
  const r = sheetRow(sel.active.r);
  if (r < 0) {
    set({ cell: null });
    return;
  }
  try {
    const { result } = await rpc<CellInfo>("cell.get", { sheet: sh.id, row: r, col: sel.active.c });
    const now = get().sel;
    if (now.active.r === sel.active.r && now.active.c === sel.active.c) set({ cell: result });
  } catch {
    /* ignore */
  }
}

// ------------------------------------------------------------------- editing

export function startEdit(initial: string | null, origin: "cell" | "bar" = "cell") {
  const { sel, cell } = get();
  const sh = currentSheet();
  if (!sh) return;
  const r = sel.active.r;
  if (sh.filtered && sheetRow(r) < 0) return;
  const row = sheetRow(r);
  const text = initial !== null ? initial : cell && cell.row === row && cell.col === sel.active.c ? cell.input : "";
  set({
    editor: { r, c: sel.active.c, row, sheet: sh.id, text, origin, caret: text.length, replace: initial !== null },
    editorSheet: sh.id,
  });
}

export function setEditorText(text: string, caret?: number) {
  const ed = get().editor;
  if (!ed) return;
  set({ editor: { ...ed, text, caret } });
}

export function cancelEdit() {
  const ed = get().editor;
  set({ editor: null, editorSheet: null });
  if (ed && ed.sheet !== get().sheetId) selectSheet(ed.sheet);
  grid?.focus();
  if (grid) grid.refHighlights = [];
}

export async function commitEdit(move: "down" | "up" | "right" | "left" | null = "down", fill?: "column") {
  const ed = get().editor as EditorState | null;
  if (!ed) return false;
  set({ editor: null, editorSheet: null });
  if (ed.sheet !== get().sheetId) selectSheet(ed.sheet);
  const sh = get().info?.sheets.find((s) => s.id === ed.sheet);
  if (!sh) return false;
  if (grid) {
    grid.refHighlights = [];
    grid.invalidateDraw();
  }
  const row = ed.row;
  const prev = get().cell;
  const unchanged = prev && prev.row === row && prev.col === ed.c && prev.input === ed.text && !fill;
  if (move && grid) {
    const dr = move === "down" ? 1 : move === "up" ? -1 : 0;
    const dc = move === "right" ? 1 : move === "left" ? -1 : 0;
    grid.move(dr, dc, false);
  }
  grid?.focus();
  if (unchanged || row < 0) return true;
  // optimistic paint for plain values
  if (grid && !ed.text.startsWith("=")) optimistic(ed.r, ed.c, ed.text);
  const res = await call("cell.set", { sheet: sh.id, row, col: ed.c, value: ed.text, fill });
  invalidate();
  refreshCell();
  if (res !== undefined && ed.text.startsWith("=") && !fill && row >= 1 && suggestFill(ed.text, ed.r, row)) {
    const isCol = sh.formulas.some((f) => f.kind === "column" && f.col === ed.c && f.row <= row);
    if (!isCol) {
      dismissFillToasts();
      toast("Apply this formula to the whole column?", "info", {
        label: "Fill column ⌃↵",
        run: () => call("cell.set", { sheet: sh.id, row, col: ed.c, value: ed.text, fill: "column" }).then(invalidate),
      });
    }
  }
  return true;
}

/** Sheets-style autofill hint: the formula reads a cell on its own row and that column continues below. */
function suggestFill(text: string, r: number, row: number): boolean {
  if (!grid) return false;
  const refs = [...text.matchAll(/(?<![A-Za-z!'$])\$?([A-Za-z]{1,3})(\d+)(?![\d(:])/g)];
  for (const m of refs) {
    if (+m[2] !== row + 1) continue;
    const col = [...m[1].toUpperCase()].reduce((a, ch) => a * 26 + ch.charCodeAt(0) - 64, 0) - 1;
    for (const dr of [1, 2]) {
      const v = grid.value(r + dr, col);
      if (v !== null && v !== undefined && v !== "") return true;
    }
  }
  return false;
}

function dismissFillToasts() {
  set((s) => ({ toasts: s.toasts.filter((t) => !t.text.startsWith("Apply this formula")) }));
}

function optimistic(r: number, c: number, text: string) {
  if (!grid) return;
  const b = grid.block(r, c);
  if (!b || !b.rows.length) return;
  const row = b.cells[r - b.br * BLOCK_R];
  if (!row) return;
  const n = Number(text.replace(/,/g, ""));
  row[c - b.bc * BLOCK_C] = text === "" ? null : text.trim() !== "" && !isNaN(n) ? n : text;
  grid.invalidateDraw();
}

// ----------------------------------------------------------------- clipboard

export async function copySelection(cut = false) {
  const sh = currentSheet();
  if (!sh) return;
  const rect = toSheetRect(primaryRect());
  const res = await query<{ tsv: string }>("range.text", { sheet: sh.id, ...rect });
  if (!res) return;
  set({ clip: { sheet: sh.id, ...rect, cut, text: res.tsv } });
  if (grid) {
    grid.copyRect = primaryRect();
    grid.invalidateDraw();
  }
  try {
    await navigator.clipboard.writeText(res.tsv);
  } catch {
    const ta = document.createElement("textarea");
    ta.value = res.tsv;
    document.body.appendChild(ta);
    ta.select();
    document.execCommand("copy");
    ta.remove();
    grid?.focus();
  }
  const cells = (rect.r2 - rect.r1 + 1) * (rect.c2 - rect.c1 + 1);
  if (cells > 1) toast(`${cut ? "Cut" : "Copied"} ${cells.toLocaleString()} cells`);
}

export async function pasteText(text: string) {
  const sh = currentSheet();
  if (!sh) return;
  const { clip, sel } = get();
  const row = sheetRow(sel.active.r);
  const col = sel.active.c;
  if (clip && normalizeNl(clip.text) === normalizeNl(text)) {
    await call("range.paste", { sheet: sh.id, src: { sheet: clip.sheet, r1: clip.r1, c1: clip.c1, r2: clip.r2, c2: clip.c2 }, row, col });
    if (clip.cut) {
      await call("range.clear", { sheet: clip.sheet, r1: clip.r1, c1: clip.c1, r2: clip.r2, c2: clip.c2, what: "all" });
      set({ clip: null });
      if (grid) grid.copyRect = null;
    }
  } else {
    const rows = parseTsv(text);
    if (!rows.length) return;
    await call("range.set", { sheet: sh.id, row, col, values: rows }, { label: "Pasting" });
    if (rows.length > 1 || rows[0].length > 1) {
      grid?.selectRect({ r1: sel.active.r, c1: col, r2: sel.active.r + rows.length - 1, c2: col + Math.max(...rows.map((r) => r.length)) - 1 });
    }
  }
  invalidate();
  refreshCell();
}

function normalizeNl(s: string) {
  return s.replace(/\r\n/g, "\n").replace(/\n$/, "");
}

export function parseTsv(text: string): string[][] {
  const t = text.replace(/\r\n/g, "\n").replace(/\n$/, "");
  if (!t) return [];
  const delim = t.includes("\t") ? "\t" : t.includes("\n") && t.split("\n")[0].includes(",") && !t.includes("\t") ? "\t" : "\t";
  const rows: string[][] = [];
  let row: string[] = [];
  let cur = "";
  let inQ = false;
  for (let i = 0; i < t.length; i++) {
    const ch = t[i];
    if (inQ) {
      if (ch === '"') {
        if (t[i + 1] === '"') {
          cur += '"';
          i++;
        } else inQ = false;
      } else cur += ch;
    } else if (ch === '"' && cur === "") inQ = true;
    else if (ch === delim) {
      row.push(cur);
      cur = "";
    } else if (ch === "\n") {
      row.push(cur);
      rows.push(row);
      row = [];
      cur = "";
    } else cur += ch;
  }
  row.push(cur);
  rows.push(row);
  return rows;
}

// ------------------------------------------------------------- range actions

export async function clearSelection(what: "contents" | "formats" | "all" = "contents") {
  const sh = currentSheet();
  if (!sh) return;
  for (const r of selectionRects()) {
    await call("range.clear", { sheet: sh.id, ...r, what });
  }
  invalidate();
  refreshCell();
}

export async function fillRange(src: Rect, dst: Rect) {
  const s = toSheetRect(src);
  const d = toSheetRect(dst);
  await call("range.fill", { sheet: sheetId(), src: [s.r1, s.c1, s.r2, s.c2], dst: [d.r1, d.c1, d.r2, d.c2] }, { label: "Filling" });
  invalidate();
  refreshCell();
}

export async function fillColumn() {
  const sh = currentSheet();
  const { cell, sel } = get();
  if (!sh) return;
  const row = sheetRow(sel.active.r);
  if (cell && cell.input.startsWith("=") && row >= 1) {
    await call("cell.set", { sheet: sh.id, row, col: sel.active.c, value: cell.input, fill: "column" }, { label: "Filling column" });
  } else {
    const r = toSheetRect(primaryRect());
    await call("range.fill", { sheet: sh.id, src: [r.r1, r.c1, r.r2, r.c2], dst: [r.r1, r.c1, Math.max(sh.nrows, r.r2), r.c2] });
  }
  invalidate();
  refreshCell();
}

export async function fillDown() {
  const rect = primaryRect();
  if (rect.r2 > rect.r1) {
    await fillRange({ ...rect, r2: rect.r1 }, rect);
  } else {
    await fillColumn();
  }
}

export async function insertRows(where: "above" | "below") {
  const rect = toSheetRect(primaryRect());
  const n = rect.r2 - rect.r1 + 1;
  const row = where === "above" ? Math.max(1, rect.r1) : rect.r2 + 1;
  await call("rows.insert", { sheet: sheetId(), row, count: Math.min(n, 10000) }, { label: "Inserting rows" });
  invalidate();
}

export async function deleteRows() {
  const rect = toSheetRect(primaryRect());
  await call("rows.delete", { sheet: sheetId(), r1: Math.max(1, rect.r1), r2: rect.r2 }, { label: "Deleting rows" });
  invalidate();
}

export async function insertCols(where: "left" | "right") {
  const rect = primaryRect();
  const n = rect.c2 - rect.c1 + 1;
  await call("cols.insert", { sheet: sheetId(), col: where === "left" ? rect.c1 : rect.c2 + 1, count: n });
  invalidate();
}

export async function deleteCols() {
  const rect = primaryRect();
  await call("cols.delete", { sheet: sheetId(), c1: rect.c1, c2: rect.c2 });
  invalidate();
}

export async function sortBy(col: number, asc: boolean) {
  await call("sort", { sheet: sheetId(), keys: [{ col, asc }] }, { label: "Sorting", success: `Sorted by ${colLetters(col)} ${asc ? "A → Z" : "Z → A"}` });
  invalidate();
}

export async function freezeAt(rows: number | null, cols: number | null) {
  const p: Record<string, unknown> = { sheet: sheetId() };
  if (rows !== null) p.frozen_rows = rows;
  if (cols !== null) p.frozen_cols = cols;
  await call("sheet.props", p);
}

export async function toggleFilter() {
  const sh = currentSheet();
  if (!sh) return;
  await call("filter.toggle", { sheet: sh.id, on: !sh.filter_on });
  invalidate();
}

export async function setFilter(col: number, spec: unknown) {
  await call("filter.set", { sheet: sheetId(), col, spec }, { label: "Filtering" });
  invalidate();
  grid?.scrollTo(null, 0, false);
}

export async function applyStyle(patch: CellStyle) {
  const rects = selectionRects().map((r) => [r.r1, r.c1, r.r2, r.c2]);
  await call("style.set", { sheet: sheetId(), rects, patch });
  grid?.invalidateDraw();
}

export function currentStyle(): CellStyle {
  const sh = currentSheet();
  const { sel } = get();
  if (!sh) return {};
  const r = sheetRow(sel.active.r);
  const c = sel.active.c;
  let st: CellStyle = {};
  for (const s of sh.styles) {
    if (c >= s.c1 && c <= s.c2 && r >= s.r1 && (s.r2 === null || r <= s.r2)) st = { ...st, ...s };
  }
  const col = sh.columns[c];
  if (st.fmt === undefined && col?.fmt) st.fmt = col.fmt;
  return st;
}

export async function toggleStyle(key: "bold" | "italic" | "strike" | "underline") {
  const cur = currentStyle();
  await applyStyle({ [key]: !cur[key] });
}

export async function setNumberFormat(fmt: string | null) {
  const rects = selectionRects();
  const sh = currentSheet();
  if (!sh) return;
  const wholeCols = rects.every((r) => r.r1 <= 1 && r.r2 >= sh.nrows);
  if (wholeCols) {
    const cols: number[] = [];
    for (const r of rects) for (let c = r.c1; c <= Math.min(r.c2, sh.ncols - 1); c++) cols.push(c);
    await call("col.props", { sheet: sh.id, cols, fmt });
  } else {
    await applyStyle({ fmt });
  }
  grid?.invalidateDraw();
}

export async function changeDecimals(delta: number) {
  const cur = currentStyle();
  const v = grid?.value(get().sel.active.r, get().sel.active.c);
  await setNumberFormat(bumpDecimals(cur.fmt, delta, typeof v === "number" ? v : undefined));
}

export async function undo() {
  const r = await call<{ ok: boolean; label?: string }>("undo");
  if (r?.ok) toast(`Undid ${r.label?.toLowerCase()}`);
  invalidate();
  refreshCell();
}

export async function redo() {
  const r = await call<{ ok: boolean; label?: string }>("redo");
  if (r?.ok) toast(`Redid ${r.label?.toLowerCase()}`);
  invalidate();
  refreshCell();
}

export function selectAll() {
  if (!grid) return;
  grid.setSel({ ranges: [{ r1: 0, c1: 0, r2: grid.totalRows - 1, c2: grid.totalCols - 1 }], active: get().sel.active, anchor: { r: 0, c: 0 } }, "api");
}

export function selectColumn() {
  if (!grid) return;
  const { active } = get().sel;
  grid.setSel({ ranges: [{ r1: 0, c1: active.c, r2: grid.totalRows - 1, c2: active.c }], active, anchor: { r: 0, c: active.c } }, "api");
}

export function selectRow() {
  if (!grid) return;
  const { active } = get().sel;
  grid.setSel({ ranges: [{ r1: active.r, c1: 0, r2: active.r, c2: grid.totalCols - 1 }], active, anchor: { r: active.r, c: 0 } }, "api");
}

export function gotoRef(text: string) {
  if (!grid) return false;
  const m = text.trim().match(/^\$?([A-Za-z]{1,3})\$?(\d+)(?::\$?([A-Za-z]{1,3})\$?(\d+))?$/);
  if (!m) return false;
  const col = (s: string) => [...s.toUpperCase()].reduce((a, ch) => a * 26 + ch.charCodeAt(0) - 64, 0) - 1;
  const r1 = +m[2] - 1;
  const c1 = col(m[1]);
  const r2 = m[4] ? +m[4] - 1 : r1;
  const c2 = m[3] ? col(m[3]) : c1;
  grid.setSel({ ranges: [{ r1: Math.min(r1, r2), c1: Math.min(c1, c2), r2: Math.max(r1, r2), c2: Math.max(c1, c2) }], active: { r: r1, c: c1 }, anchor: { r: r1, c: c1 } }, "api");
  grid.scrollIntoView(r1, c1, true);
  grid.focus();
  return true;
}

export async function autofit(col: number) {
  if (!grid) return;
  const w = grid.measureColumn(col);
  grid.widthOverride.delete(col);
  await call("col.props", { sheet: sheetId(), col, width: w });
}

export async function resizeColumn(col: number, width: number) {
  const sh = currentSheet();
  if (!sh) return;
  const sel = get().sel;
  const rg = sel.ranges.find((r) => r.r1 === 0 && col >= r.c1 && col <= r.c2 && r.r2 >= (grid?.totalRows ?? 1) - 1);
  const cols = rg ? Array.from({ length: rg.c2 - rg.c1 + 1 }, (_, i) => rg.c1 + i) : [col];
  await call("col.props", { sheet: sh.id, cols, width });
  for (const c of cols) grid?.widthOverride.delete(c);
}

export function describeSelection(sel: Selection): string {
  const r = sel.ranges[sel.ranges.length - 1];
  if (r.r1 === r.r2 && r.c1 === r.c2) return cellRef(sheetRow(r.r1), r.c1);
  return `${cellRef(sheetRow(r.r1), r.c1)}:${cellRef(Math.max(0, sheetRow(Math.min(r.r2, (grid?.totalRows ?? r.r2) - 1))), Math.min(r.c2, (grid?.totalCols ?? r.c2) - 1))}`;
}

export function openFind(mode: "find" | "replace") {
  set({ findOpen: true, findMode: mode });
}

export function openPivot() {
  openPanel("pivot", { source: currentSheet()?.pivot?.source ?? currentSheet()?.id, target: currentSheet()?.pivot ? currentSheet()?.id : null });
}

export function openImport() {
  openDialog("import");
}

export function openExport() {
  openDialog("export");
}
