import { create } from "zustand";
import { rpc, RpcError } from "./api";
import type { CellInfo, Rect, Selection, SheetInfo, Stats, WorkbookInfo } from "./types";

export type PanelKind = "pivot" | "cf" | "dv" | "dups" | "profile" | "clean";
export type DialogKind = "import" | "export" | "sql" | "shortcuts" | "about" | "split" | "google";

export interface ScriptLine {
  t: number;
  s: "out" | "err" | "sys";
  text: string;
}

export interface RunState {
  id: string;
  scriptId: string | null;
  name: string;
  status: "running" | "done" | "error" | "cancelled";
  lines: ScriptLine[];
  error: string | null;
  elapsed: number;
}

export interface Toast {
  id: number;
  text: string;
  kind: "info" | "error" | "success";
  action?: { label: string; run: () => void };
}

export interface Clip {
  sheet: string;
  r1: number;
  c1: number;
  r2: number;
  c2: number;
  cut: boolean;
  text: string;
}

export interface MenuItem {
  label?: string;
  icon?: string;
  shortcut?: string;
  disabled?: boolean;
  danger?: boolean;
  checked?: boolean;
  run?: () => void;
  items?: MenuItem[];
  divider?: boolean;
}

export interface ContextMenu {
  x: number;
  y: number;
  items: MenuItem[];
}

export interface EditorState {
  r: number;
  c: number;
  row: number; // sheet row when editing started (stable across tab switches)
  sheet: string;
  text: string;
  origin: "cell" | "bar";
  caret?: number;
  replace: boolean; // started by typing (arrow keys commit) vs. F2/double-click
}

interface PerSheet {
  sel: Selection;
  scrollX: number;
  scrollY: number;
}

const emptySel = (): Selection => ({ ranges: [{ r1: 1, c1: 0, r2: 1, c2: 0 }], active: { r: 1, c: 0 }, anchor: { r: 1, c: 0 } });

export interface AppState {
  info: WorkbookInfo | null;
  sheetId: string | null;
  sel: Selection;
  cell: CellInfo | null;
  editor: EditorState | null;
  editorSheet: string | null;
  stats: Stats | null;
  theme: "light" | "dark";
  panel: PanelKind | null;
  panelProps: Record<string, unknown>;
  dialog: DialogKind | null;
  dialogProps: Record<string, unknown>;
  toasts: Toast[];
  busy: number;
  busyLabel: string | null;
  clip: Clip | null;
  findOpen: boolean;
  findMode: "find" | "replace";
  menu: ContextMenu | null;
  filterPopover: { col: number; x: number; y: number } | null;
  palette: boolean;
  scriptsOpen: boolean;
  run: RunState | null;
  functions: string[];
  builtinFunctions: string[];
  dataVersion: number;
  perSheet: Record<string, PerSheet>;
}

export const useApp = create<AppState>(() => ({
  info: null,
  sheetId: null,
  sel: emptySel(),
  cell: null,
  editor: null,
  editorSheet: null,
  stats: null,
  theme: (localStorageGet("pysheets.theme") as "light" | "dark") ||
    (window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light"),
  panel: null,
  panelProps: {},
  dialog: null,
  dialogProps: {},
  toasts: [],
  busy: 0,
  busyLabel: null,
  clip: null,
  findOpen: false,
  findMode: "find",
  menu: null,
  filterPopover: null,
  palette: false,
  scriptsOpen: false,
  run: null,
  functions: [],
  builtinFunctions: [],
  dataVersion: 0,
  perSheet: {},
}));

function localStorageGet(k: string): string | null {
  try {
    return localStorage.getItem(k);
  } catch {
    return null;
  }
}

export function localStorageSet(k: string, v: string) {
  try {
    localStorage.setItem(k, v);
  } catch {
    /* private mode */
  }
}

const get = useApp.getState;
const set = useApp.setState;

export function currentSheet(): SheetInfo | null {
  const { info, sheetId } = get();
  return info?.sheets.find((s) => s.id === sheetId) || info?.sheets[0] || null;
}

let toastId = 1;
export function toast(text: string, kind: Toast["kind"] = "info", action?: Toast["action"], ms = 4200) {
  const id = toastId++;
  set((s) => ({ toasts: [...s.toasts.slice(-3), { id, text, kind, action }] }));
  window.setTimeout(() => dismissToast(id), kind === "error" ? ms + 2500 : ms);
}

export function dismissToast(id: number) {
  set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) }));
}

export function applyInfo(info: WorkbookInfo | undefined | null) {
  if (!info) return;
  const st = get();
  let sheetId = st.sheetId;
  if (!sheetId || !info.sheets.some((s) => s.id === sheetId)) sheetId = info.sheets[0]?.id || null;
  const changed = !st.info || st.info.version !== info.version;
  const fns = [...new Set([...st.builtinFunctions, ...(info.custom_functions || [])])].sort();
  set({ info, sheetId, dataVersion: changed ? st.dataVersion + 1 : st.dataVersion, functions: st.builtinFunctions.length ? fns : st.functions });
  document.title = `${info.dirty ? "• " : ""}${info.title} — PySheets`;
}

export async function refreshInfo() {
  const { result } = await rpc<WorkbookInfo>("workbook.info");
  applyInfo(result);
}

interface CallOpts {
  label?: string;
  quiet?: boolean;
  success?: string;
}

/** Run a mutating RPC: shows progress, applies the new workbook info, surfaces errors.
 *  Google calls made while signed out open the Google sign-in first and continue once signed in. */
export async function call<T = unknown>(method: string, params: Record<string, unknown> = {}, opts: CallOpts = {}): Promise<T | undefined> {
  let needsGoogle: string | null = null;
  set((s) => ({ busy: s.busy + 1, busyLabel: opts.label ?? s.busyLabel }));
  try {
    const { result, info } = await rpc<T>(method, params);
    applyInfo(info);
    if (opts.success) toast(opts.success, "success");
    return result;
  } catch (e) {
    if (e instanceof RpcError) {
      applyInfo(e.info);
      if (e.code === "google_auth") needsGoogle = e.message;
      else if (!opts.quiet) toast(e.message, "error");
    } else {
      toast(String(e), "error");
    }
    if (!needsGoogle) return undefined;
  } finally {
    set((s) => ({ busy: Math.max(0, s.busy - 1), busyLabel: s.busy <= 1 ? null : s.busyLabel }));
  }
  return new Promise<T | undefined>((resolve) =>
    openDialog("google", {
      reason: needsGoogle,
      retry: () => call<T>(method, params, opts).then(resolve),
      cancel: () => resolve(undefined),
    }),
  );
}

/** Read-only RPC with error toast. */
export async function query<T = unknown>(method: string, params: Record<string, unknown> = {}): Promise<T | undefined> {
  try {
    const { result } = await rpc<T>(method, params);
    return result;
  } catch (e) {
    toast(e instanceof Error ? e.message : String(e), "error");
    return undefined;
  }
}

export function selectSheet(id: string) {
  const st = get();
  if (st.sheetId === id) return;
  const per = { ...st.perSheet };
  if (st.sheetId) per[st.sheetId] = { ...(per[st.sheetId] || { scrollX: 0, scrollY: 0 }), sel: st.sel };
  const next = per[id];
  // keep a formula being edited alive so the user can click cells on other tabs
  const keep = st.editor && st.editor.text.startsWith("=") ? st.editor : null;
  set({ sheetId: id, perSheet: per, sel: next?.sel || emptySel(), editor: keep, editorSheet: keep ? st.editorSheet : null,
        filterPopover: null, cell: null });
}

export function rememberScroll(sheetId: string, x: number, y: number) {
  const per = get().perSheet;
  const cur = per[sheetId];
  per[sheetId] = { sel: cur?.sel || get().sel, scrollX: x, scrollY: y };
}

export function setSelection(sel: Selection) {
  set({ sel });
}

export function primaryRect(): Rect {
  const { sel } = get();
  return sel.ranges[sel.ranges.length - 1];
}

export function openPanel(panel: PanelKind, props: Record<string, unknown> = {}) {
  set({ panel, panelProps: props });
}

export function closePanel() {
  set({ panel: null, panelProps: {} });
}

export function openDialog(dialog: DialogKind, props: Record<string, unknown> = {}) {
  set({ dialog, dialogProps: props });
}

export function closeDialog() {
  set({ dialog: null, dialogProps: {} });
}

export function setTheme(theme: "light" | "dark") {
  localStorageSet("pysheets.theme", theme);
  set({ theme });
}
