export type ColType = "num" | "str" | "bool" | "date" | "ts";

export interface ColumnInfo {
  id: string;
  name: string;
  type: ColType;
  duck: string;
  width: number;
  fmt: string | null;
  linked?: boolean;
}

export interface CellStyle {
  bold?: boolean;
  italic?: boolean;
  underline?: boolean;
  strike?: boolean;
  fg?: string | null;
  bg?: string | null;
  align?: "left" | "center" | "right" | null;
  fmt?: string | null;
  wrap?: boolean;
  bt?: string | null; // borders: "<thin|medium|thick|dashed|dotted|double>:<color>"
  bb?: string | null;
  bl?: string | null;
  br?: string | null;
}

export interface StyleRange extends CellStyle {
  r1: number;
  r2: number | null;
  c1: number;
  c2: number;
}

export interface CondFormat {
  id: string;
  name?: string;
  cols: string[];
  key_cols?: string[];
  kind: string;
  value?: unknown;
  value2?: unknown;
  formula?: string;
  style?: CellStyle;
  colors?: string[];
  r1?: number;
  r2?: number | null;
}

export interface Validation {
  id: string;
  cols: string[];
  kind: string;
  op?: string;
  value?: unknown;
  value2?: unknown;
  values?: string[];
  source?: string;
  pattern?: string;
  strict?: boolean;
  message?: string;
  dropdown?: boolean;
  r1?: number;
  r2?: number | null;
}

export interface FilterSpec {
  include?: unknown[] | null;
  exclude?: unknown[] | null;
  cond?: { op: string; value?: unknown; value2?: unknown } | null;
}

export interface FormulaCell {
  id: number;
  row: number;
  col: number;
  kind: "cell" | "column";
  error: string | null;
  h: number;
  w: number;
  row_end?: number;
  text?: string;
  /** lookup diagnostics from the last evaluation */
  match?: { mode: "smart" | "strict"; exact: number; fuzzy: number; missing: number; blank: number; filter_fuzzy?: number };
}

export interface PivotSpec {
  source: string;
  rows: string[];
  cols: string[];
  values: { col: string; agg: string }[];
  filters?: Record<string, FilterSpec>;
  totals?: boolean;
  desc?: boolean;
  sort_by_value?: boolean;
  error?: string | null;
}

export interface SheetInfo {
  id: string;
  name: string;
  nrows: number;
  ncols: number;
  columns: ColumnInfo[];
  frozen_rows: number;
  frozen_cols: number;
  filter_on: boolean;
  filters: Record<string, FilterSpec>;
  filtered: boolean;
  nview: number;
  styles: StyleRange[];
  merges: Rect[];
  cond_formats: CondFormat[];
  validations: Validation[];
  pivot: PivotSpec | null;
  tab_color: string | null;
  version: number;
  row_height: number;
  source: string | null;
  link: { kind: string; tab?: string; refreshed_at?: string; rows?: number; sources?: number; group?: string; spreadsheet_id?: string } | null;
  formulas: FormulaCell[];
}

export interface WorkbookInfo {
  title: string;
  path: string | null;
  dirty: boolean;
  version: number;
  can_undo: boolean;
  can_redo: boolean;
  undo_label: string | null;
  redo_label: string | null;
  scripts: { id: string; name: string; kind: "macro" | "functions" | "file" }[];
  scripts_trusted: boolean;
  match_mode?: "smart" | "strict";
  custom_functions: string[];
  custom_function_docs?: Record<string, { params: string[]; doc: string }>;
  sheets: SheetInfo[];
}

export type Flag = [number, number, string, unknown, number];

export interface Viewport {
  sheet: string;
  version: number;
  r0: number;
  c0: number;
  rows: number[];
  cells: unknown[][];
  flags: Flag[];
}

export interface CellInfo {
  row: number;
  col: number;
  ref: string;
  value: unknown;
  input: string;
  type: ColType;
  formula?: {
    id: number;
    kind: "cell" | "column";
    error: string | null;
    anchor?: string;
    row?: number;
    row_end?: number;
    skipped?: number; // rows inside the range the formula doesn't cover (e.g. hidden by a filter)
    spill?: [number, number] | null;
  };
  spill_of?: { anchor: string; text: string; row: number; col: number };
  validation?: Validation;
}

export interface Rect {
  r1: number;
  c1: number;
  r2: number;
  c2: number;
}

export interface Selection {
  ranges: Rect[];
  active: { r: number; c: number };
  anchor: { r: number; c: number };
}

export interface Stats {
  sum: number;
  count: number;
  counta: number;
  min: number | null;
  max: number | null;
  avg: number | null;
}
