/*
 * Canvas spreadsheet renderer.
 *
 * - Custom scrolling (browsers cap element height at ~33M px; 10M rows need more), with
 *   eased wheel scrolling and overlay scrollbars.
 * - Data arrives in blocks (BLOCK_R x BLOCK_C) via the `sheet.viewport` RPC and is cached
 *   (stale-while-revalidate: old values stay visible until fresh ones arrive, so edits never flicker).
 * - Four quadrants (corner / frozen rows / frozen cols / main) share one drawing routine.
 * - Selection, fill handle and copy border are animated with critically-damped easing.
 */
import { rpc } from "../api";
import { colLetters, fitNumber, formatCell, isNumericText } from "../format";
import type { CellStyle, ColumnInfo, Flag, Rect, Selection, SheetInfo, Viewport } from "../types";

export const BLOCK_R = 128;
export const BLOCK_C = 48;
const MAX_BLOCKS = 400;
const MAX_INFLIGHT = 4;
const HEADER_H = 28;
const FONT = `13px "Inter Variable", "Segoe UI Variable Text", "Segoe UI", system-ui, sans-serif`;
const FONT_BOLD = `600 13px "Inter Variable", "Segoe UI Variable Text", "Segoe UI", system-ui, sans-serif`;
const FONT_HEADER = `500 11.5px "Inter Variable", "Segoe UI Variable Text", "Segoe UI", system-ui, sans-serif`;
const PAD = 7;

export interface GridTheme {
  bg: string;
  grid: string;
  gridStrong: string;
  text: string;
  muted: string;
  headerBg: string;
  headerText: string;
  headerSel: string;
  headerSelText: string;
  headerFull: string;
  headerFullText: string;
  row0Bg: string;
  row0Text: string;
  accent: string;
  selFill: string;
  error: string;
  negative: string;
  shimmer: string;
  frozenLine: string;
  shadow: string;
  spill: string;
  invalid: string;
  warning: string;
  refColors: string[];
  checkbox: string;
  findFill: string;
}

interface Block {
  key: string;
  br: number;
  bc: number;
  rows: number[];
  cells: unknown[][];
  flags: Map<number, Flag[]>;
  gen: number;
  loading: boolean;
  used: number;
}

export interface Hit {
  area: "corner" | "colHeader" | "rowHeader" | "cell" | "none";
  r: number;
  c: number;
  border?: number; // column index whose right border is under the pointer
  fillHandle?: boolean;
  filterBtn?: boolean;
  dropdown?: boolean;
  checkbox?: boolean;
  x: number;
  y: number;
}

export interface RefHighlight extends Rect {
  color: string;
}

export interface GridCallbacks {
  onSelect(sel: Selection, reason: "click" | "drag" | "key" | "api"): void;
  onEditStart(initial: string | null, caretAtEnd: boolean): void;
  onEditCommit(move?: "down" | "up" | "right" | "left" | null): boolean;
  isEditing(): boolean;
  canPickRef(): boolean;
  onRefPick(rect: Rect, phase: "start" | "move" | "end"): void;
  onContextMenu(hit: Hit, clientX: number, clientY: number): void;
  onFilterButton(col: number, rect: DOMRect): void;
  onDropdown(r: number, c: number, rect: { x: number; y: number; w: number; h: number }): void;
  onCheckbox(r: number, c: number, value: boolean): void;
  onFill(src: Rect, dst: Rect): void;
  onFillColumn(): void;
  onResizeColumn(col: number, width: number): void;
  onAutofit(col: number): void;
  onHover(hit: Hit | null, clientX: number, clientY: number): void;
  onView(): void;
  onKey(e: KeyboardEvent): boolean; // return true if handled by app-level shortcuts
  onPaste(text: string): void;
  onCopy(cut: boolean): void;
}

const easeOut = (t: number) => 1 - Math.pow(1 - t, 3);

export class GridController {
  host!: HTMLElement;
  canvas!: HTMLCanvasElement;
  ctx!: CanvasRenderingContext2D;
  input!: HTMLTextAreaElement;
  vbar!: HTMLDivElement;
  vthumb!: HTMLDivElement;
  hbar!: HTMLDivElement;
  hthumb!: HTMLDivElement;
  cb: GridCallbacks;
  theme: GridTheme;
  sheet: SheetInfo | null = null;
  sel: Selection = { ranges: [{ r1: 1, c1: 0, r2: 1, c2: 0 }], active: { r: 1, c: 0 }, anchor: { r: 1, c: 0 } };

  dpr = 1;
  width = 0;
  height = 0;
  rowH = 26;
  rowHeaderW = 52;
  colX: number[] = [0];
  widthOverride = new Map<number, number>();
  totalRows = 1000;
  totalCols = 26;

  sx = 0;
  sy = 0;
  tx = 0;
  ty = 0;
  tau = 0.07;

  blocks = new Map<string, Block>();
  gen = 1;
  inflight = 0;
  queue: string[] = [];
  useCounter = 0;

  // animation state for the primary selection rectangle (content coordinates)
  anim = { x: 0, y: 0, w: 0, h: 0, init: false };
  lastFrame = 0;
  raf = 0;
  dirty = true;
  destroyed = false;

  drag: null | {
    kind: "select" | "colsel" | "rowsel" | "resize" | "fill" | "ref" | "refcol" | "refrow" | "vthumb" | "hthumb";
    startX: number;
    startY: number;
    col?: number;
    startW?: number;
    origin?: { r: number; c: number };
    add?: boolean;
    fillTo?: Rect;
    grab?: number;
    lastClientX?: number;
    lastClientY?: number;
  } = null;
  autoScroll = { vx: 0, vy: 0 };
  hover: Hit | null = null;
  refHighlights: RefHighlight[] = [];
  copyRect: Rect | null = null;
  findRect: Rect | null = null;
  spillRect: Rect | null = null;
  textWidthCache = new Map<string, number>();
  styleCache: { gen: number; list: SheetInfo["styles"] } = { gen: -1, list: [] };
  checkboxCols = new Set<number>();
  dropdownCols = new Set<number>();
  errorCells = new Map<string, string>();
  formulaCols = new Map<number, { row: number; end: number }[]>();
  reducedMotion = false;
  resizeObs?: ResizeObserver;
  scrollbarTimer = 0;

  constructor(cb: GridCallbacks, theme: GridTheme) {
    this.cb = cb;
    this.theme = theme;
  }

  // ------------------------------------------------------------------ setup

  attach(host: HTMLElement) {
    this.host = host;
    this.canvas = host.querySelector("canvas")!;
    this.ctx = this.canvas.getContext("2d", { alpha: false })!;
    this.input = host.querySelector("textarea.grid-input") as HTMLTextAreaElement;
    this.vbar = host.querySelector(".sb-v") as HTMLDivElement;
    this.vthumb = this.vbar.firstElementChild as HTMLDivElement;
    this.hbar = host.querySelector(".sb-h") as HTMLDivElement;
    this.hthumb = this.hbar.firstElementChild as HTMLDivElement;
    this.reducedMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
    this.resizeObs = new ResizeObserver(() => this.resize());
    this.resizeObs.observe(host);
    this.resize();
    this.bind();
    this.loop(performance.now());
  }

  private winMove = (e: PointerEvent) => this.onPointerMove(e);
  private winUp = (e: PointerEvent) => this.onPointerUp(e);

  destroy() {
    this.destroyed = true;
    cancelAnimationFrame(this.raf);
    this.resizeObs?.disconnect();
    window.removeEventListener("pointermove", this.winMove);
    window.removeEventListener("pointerup", this.winUp);
  }

  setTheme(theme: GridTheme) {
    this.theme = theme;
    this.invalidateDraw();
  }

  resize() {
    const r = this.host.getBoundingClientRect();
    this.dpr = window.devicePixelRatio || 1;
    this.width = Math.max(1, Math.floor(r.width));
    this.height = Math.max(1, Math.floor(r.height));
    this.canvas.width = Math.round(this.width * this.dpr);
    this.canvas.height = Math.round(this.height * this.dpr);
    this.canvas.style.width = `${this.width}px`;
    this.canvas.style.height = `${this.height}px`;
    this.clampScroll(true);
    this.invalidateDraw();
    this.requestData();
  }

  setSheet(sheet: SheetInfo, opts: { resetScroll?: boolean; scroll?: { x: number; y: number } } = {}) {
    const changedSheet = !this.sheet || this.sheet.id !== sheet.id;
    this.sheet = sheet;
    this.rowH = sheet.row_height || 26;
    this.computeGeometry();
    if (changedSheet) {
      this.blocks.clear();
      this.queue = [];
      this.anim.init = false;
      this.copyRect = null;
      const s = opts.scroll || { x: 0, y: 0 };
      this.sx = this.tx = s.x;
      this.sy = this.ty = s.y;
    }
    this.clampScroll(true);
    this.invalidateDraw();
    this.requestData();
  }

  computeGeometry() {
    const sh = this.sheet!;
    const filtered = sh.filtered;
    this.totalRows = filtered ? sh.nview + 1 + 30 : Math.max(sh.nrows + 1 + 100, 200);
    this.totalCols = Math.max(sh.ncols + 8, 26);
    const xs = [0];
    for (let c = 0; c < this.totalCols; c++) xs.push(xs[c] + this.colWidth(c));
    this.colX = xs;
    const digits = String(Math.max(sh.nrows + 101, 1000)).length;
    this.rowHeaderW = Math.max(46, 18 + digits * 8);
    this.checkboxCols.clear();
    this.dropdownCols.clear();
    const idx = new Map(sh.columns.map((c, i) => [c.id, i]));
    for (const v of sh.validations) {
      for (const cid of v.cols) {
        const i = idx.get(cid);
        if (i === undefined) continue;
        if (v.kind === "checkbox") this.checkboxCols.add(i);
        if (v.kind === "list" && v.dropdown !== false) this.dropdownCols.add(i);
      }
    }
    this.errorCells.clear();
    this.formulaCols.clear();
    for (const f of sh.formulas) {
      if (f.error) this.errorCells.set(`${f.row}:${f.col}`, f.error);
      if (f.kind === "column") {
        const arr = this.formulaCols.get(f.col) || [];
        arr.push({ row: f.row, end: f.row_end ?? sh.nrows });
        this.formulaCols.set(f.col, arr);
      }
    }
    this.styleCache.gen = -1;
  }

  colWidth(c: number): number {
    const o = this.widthOverride.get(c);
    if (o !== undefined) return o;
    const col = this.sheet?.columns[c];
    return col ? col.width : 110;
  }

  // ---------------------------------------------------------------- geometry

  get fr() {
    return Math.min(this.sheet?.frozen_rows ?? 0, this.totalRows - 1);
  }
  get fc() {
    return Math.min(this.sheet?.frozen_cols ?? 0, this.totalCols - 1);
  }
  get frozenH() {
    return this.fr * this.rowH;
  }
  get frozenW() {
    return this.colX[this.fc];
  }
  get viewW() {
    return this.width - this.rowHeaderW - this.frozenW;
  }
  get viewH() {
    return this.height - HEADER_H - this.frozenH;
  }
  get maxSx() {
    return Math.max(0, this.colX[this.totalCols] - this.frozenW - this.viewW + 40);
  }
  get maxSy() {
    return Math.max(0, (this.totalRows - this.fr) * this.rowH - this.viewH + 40);
  }

  clampScroll(snap = false) {
    this.tx = Math.min(Math.max(0, this.tx), this.maxSx);
    this.ty = Math.min(Math.max(0, this.ty), this.maxSy);
    if (snap) {
      this.sx = Math.min(Math.max(0, this.sx), this.maxSx);
      this.sy = Math.min(Math.max(0, this.sy), this.maxSy);
    }
  }

  /** Screen x of a content x (column offset) in a given column zone. */
  xOf(cx: number, frozen: boolean) {
    return this.rowHeaderW + cx - (frozen ? 0 : this.sx);
  }
  yOf(cy: number, frozen: boolean) {
    return HEADER_H + cy - (frozen ? 0 : this.sy);
  }

  colAt(screenX: number): number {
    const x = screenX - this.rowHeaderW;
    if (x < 0) return -1;
    const cx = x < this.frozenW ? x : x + this.sx;
    let lo = 0;
    let hi = this.totalCols;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (this.colX[mid + 1] <= cx) lo = mid + 1;
      else hi = mid;
    }
    return Math.min(lo, this.totalCols - 1);
  }

  rowAt(screenY: number): number {
    const y = screenY - HEADER_H;
    if (y < 0) return -1;
    const cy = y < this.frozenH ? y : y + this.sy;
    return Math.min(Math.floor(cy / this.rowH), this.totalRows - 1);
  }

  visibleRows(): [number, number] {
    const first = this.fr + Math.floor(this.sy / this.rowH);
    const last = Math.min(this.totalRows - 1, first + Math.ceil(this.viewH / this.rowH) + 1);
    return [first, last];
  }

  visibleCols(): [number, number] {
    const startX = this.frozenW + this.sx;
    let c = this.fc;
    while (c < this.totalCols - 1 && this.colX[c + 1] <= startX) c++;
    let e = c;
    while (e < this.totalCols - 1 && this.colX[e] < startX + this.viewW) e++;
    return [c, e];
  }

  /** Screen rectangle of a cell (for the editor overlay). */
  cellRect(r: number, c: number) {
    const frozenR = r < this.fr;
    const frozenC = c < this.fc;
    const x = this.xOf(this.colX[c], frozenC);
    const y = this.yOf(r * this.rowH, frozenR);
    const m = this.mergeFor(r, c);
    if (m && m.r1 === r && m.c1 === c) {
      return { x, y, w: this.colX[Math.min(m.c2, this.totalCols - 1) + 1] - this.colX[c], h: (m.r2 - m.r1 + 1) * this.rowH };
    }
    return { x, y, w: this.colX[c + 1] - this.colX[c], h: this.rowH };
  }

  isCellVisible(r: number, c: number) {
    const rc = this.cellRect(r, c);
    return rc.x >= this.rowHeaderW - 1 && rc.y >= HEADER_H - 1 && rc.x < this.width && rc.y < this.height;
  }

  // --------------------------------------------------------------------- data

  invalidateData() {
    this.gen++;
    this.requestData();
    this.invalidateDraw();
  }

  invalidateDraw() {
    this.dirty = true;
  }

  private blockKey(br: number, bc: number) {
    return `${br}:${bc}`;
  }

  requestData() {
    if (!this.sheet || this.width < 2) return;
    const [r0, r1] = this.visibleRows();
    const [c0, c1] = this.visibleCols();
    const wanted: [number, number, number][] = [];
    const brs = new Set<number>();
    const add = (r: number) => brs.add(Math.floor(r / BLOCK_R));
    add(0);
    if (this.fr > 0) add(this.fr - 1);
    for (let r = r0; r <= r1 + BLOCK_R / 2; r += BLOCK_R / 2) add(Math.min(r, this.totalRows - 1));
    add(Math.max(0, r0 - BLOCK_R / 2));
    add(Math.min(this.totalRows - 1, r1));
    const bcs = new Set<number>();
    for (let c = 0; c < this.fc; c += BLOCK_C) bcs.add(Math.floor(c / BLOCK_C));
    for (let c = c0; c <= c1; c += BLOCK_C) bcs.add(Math.floor(c / BLOCK_C));
    bcs.add(Math.floor(c1 / BLOCK_C));
    const centre = (r0 + r1) / 2;
    for (const br of brs) {
      for (const bc of bcs) {
        const b = this.blocks.get(this.blockKey(br, bc));
        if (!b || (b.gen !== this.gen && !b.loading)) {
          wanted.push([br, bc, Math.abs(br * BLOCK_R + BLOCK_R / 2 - centre)]);
        }
      }
    }
    wanted.sort((a, b) => a[2] - b[2]);
    this.queue = wanted.map(([br, bc]) => this.blockKey(br, bc));
    this.pump();
  }

  private pump() {
    while (this.inflight < MAX_INFLIGHT && this.queue.length) {
      const key = this.queue.shift()!;
      const [br, bc] = key.split(":").map(Number);
      const existing = this.blocks.get(key);
      if (existing?.loading) continue;
      this.fetchBlock(br, bc, existing);
    }
  }

  private async fetchBlock(br: number, bc: number, existing?: Block) {
    const sheet = this.sheet!;
    const gen = this.gen;
    const key = this.blockKey(br, bc);
    if (existing) existing.loading = true;
    else
      this.blocks.set(key, { key, br, bc, rows: [], cells: [], flags: new Map(), gen: -1, loading: true, used: ++this.useCounter });
    this.inflight++;
    try {
      const { result } = await rpc<Viewport>("sheet.viewport", {
        sheet: sheet.id,
        r0: br * BLOCK_R,
        r1: (br + 1) * BLOCK_R,
        c0: bc * BLOCK_C,
        c1: (bc + 1) * BLOCK_C,
      });
      if (this.destroyed || this.sheet?.id !== sheet.id) return;
      const flags = new Map<number, Flag[]>();
      for (const f of result.flags) {
        const k = f[0] * 4096 + f[1];
        const arr = flags.get(k);
        if (arr) arr.push(f);
        else flags.set(k, [f]);
      }
      const b = this.blocks.get(key);
      const blk: Block = { key, br, bc, rows: result.rows, cells: result.cells, flags, gen, loading: false, used: b?.used ?? ++this.useCounter };
      this.blocks.set(key, blk);
      this.evict();
      this.invalidateDraw();
      if (gen !== this.gen) this.requestData();
    } catch {
      const b = this.blocks.get(key);
      if (b) b.loading = false;
    } finally {
      this.inflight--;
      this.pump();
    }
  }

  private evict() {
    if (this.blocks.size <= MAX_BLOCKS) return;
    const arr = [...this.blocks.values()].sort((a, b) => a.used - b.used);
    for (const b of arr.slice(0, this.blocks.size - MAX_BLOCKS)) this.blocks.delete(b.key);
  }

  block(r: number, c: number): Block | undefined {
    const b = this.blocks.get(this.blockKey(Math.floor(r / BLOCK_R), Math.floor(c / BLOCK_C)));
    if (b) b.used = ++this.useCounter;
    return b;
  }

  value(r: number, c: number): unknown {
    const b = this.block(r, c);
    if (!b || !b.rows.length) return undefined;
    const row = b.cells[r - b.br * BLOCK_R];
    if (!row) return undefined;
    return row[c - b.bc * BLOCK_C];
  }

  hasValue(r: number, c: number): boolean {
    const b = this.block(r, c);
    return !!b && b.rows.length > 0;
  }

  /** Sheet row (0 = header) shown at visual row r; -1 when unknown/blank. */
  sheetRow(r: number): number {
    if (!this.sheet?.filtered) return r;
    if (r === 0) return 0;
    const b = this.block(r, 0) || this.blocks.get(this.blockKey(Math.floor(r / BLOCK_R), 0));
    if (!b || !b.rows.length) return -1;
    return b.rows[r - b.br * BLOCK_R] ?? -1;
  }

  flagsAt(r: number, c: number): Flag[] | undefined {
    const b = this.block(r, c);
    if (!b) return undefined;
    return b.flags.get((r - b.br * BLOCK_R) * 4096 + (c - b.bc * BLOCK_C));
  }

  // ---------------------------------------------------------------- scrolling

  scrollBy(dx: number, dy: number, smooth = true) {
    this.tx += dx;
    this.ty += dy;
    this.clampScroll();
    if (!smooth || this.reducedMotion) {
      this.sx = this.tx;
      this.sy = this.ty;
    }
    this.showScrollbars();
    this.invalidateDraw();
  }

  scrollTo(x: number | null, y: number | null, smooth = true) {
    if (x !== null) this.tx = x;
    if (y !== null) this.ty = y;
    this.clampScroll();
    if (!smooth || this.reducedMotion) {
      this.sx = this.tx;
      this.sy = this.ty;
    }
    this.invalidateDraw();
  }

  scrollIntoView(r: number, c: number, smooth = true) {
    let x: number | null = null;
    let y: number | null = null;
    if (r >= this.fr) {
      const top = (r - this.fr) * this.rowH;
      if (top < this.ty) y = top;
      else if (top + this.rowH > this.ty + this.viewH - 2) y = top + this.rowH - this.viewH + 4;
    }
    if (c >= this.fc) {
      const left = this.colX[c] - this.frozenW;
      const w = this.colX[c + 1] - this.colX[c];
      if (left < this.tx) x = left;
      else if (left + w > this.tx + this.viewW - 2) x = Math.min(left, left + w - this.viewW + 4);
    }
    if (x !== null || y !== null) {
      const far = y !== null && Math.abs(y - this.sy) > this.viewH * 3;
      this.scrollTo(x, y, smooth && !far);
    }
  }

  showScrollbars() {
    this.host.classList.add("scrolling");
    window.clearTimeout(this.scrollbarTimer);
    this.scrollbarTimer = window.setTimeout(() => this.host.classList.remove("scrolling"), 900);
  }

  private updateScrollbars() {
    const contentH = (this.totalRows - this.fr) * this.rowH + 40;
    const contentW = this.colX[this.totalCols] - this.frozenW + 40;
    const trackH = this.height - HEADER_H - this.frozenH - 14;
    const trackW = this.width - this.rowHeaderW - this.frozenW - 14;
    const thumbH = Math.max(36, (this.viewH / contentH) * trackH);
    const thumbW = Math.max(36, (this.viewW / contentW) * trackW);
    const ty = this.maxSy ? (this.sy / this.maxSy) * (trackH - thumbH) : 0;
    const tx = this.maxSx ? (this.sx / this.maxSx) * (trackW - thumbW) : 0;
    this.vbar.style.top = `${HEADER_H + this.frozenH}px`;
    this.vbar.style.height = `${trackH}px`;
    this.vthumb.style.height = `${thumbH}px`;
    this.vthumb.style.transform = `translateY(${ty}px)`;
    this.vbar.style.display = this.maxSy > 0 ? "" : "none";
    this.hbar.style.left = `${this.rowHeaderW + this.frozenW}px`;
    this.hbar.style.width = `${trackW}px`;
    this.hthumb.style.width = `${thumbW}px`;
    this.hthumb.style.transform = `translateX(${tx}px)`;
    this.hbar.style.display = this.maxSx > 0 ? "" : "none";
  }

  // ------------------------------------------------------------------ events

  private bind() {
    const c = this.canvas;
    c.addEventListener("wheel", (e) => this.onWheel(e), { passive: false });
    c.addEventListener("pointerdown", (e) => this.onPointerDown(e));
    window.addEventListener("pointermove", this.winMove);
    window.addEventListener("pointerup", this.winUp);
    c.addEventListener("dblclick", (e) => this.onDblClick(e));
    c.addEventListener("contextmenu", (e) => this.onContext(e));
    c.addEventListener("pointerleave", () => {
      if (!this.drag) {
        this.hover = null;
        this.cb.onHover(null, 0, 0);
        this.invalidateDraw();
      }
    });
    this.input.addEventListener("keydown", (e) => this.onKeyDown(e));
    this.input.addEventListener("paste", (e) => {
      const text = e.clipboardData?.getData("text/plain");
      if (text !== undefined) {
        e.preventDefault();
        this.cb.onPaste(text);
      }
    });
    this.input.addEventListener("copy", (e) => {
      e.preventDefault();
      this.cb.onCopy(false);
    });
    this.input.addEventListener("cut", (e) => {
      e.preventDefault();
      this.cb.onCopy(true);
    });
    this.input.addEventListener("input", () => {
      // IME / dead keys: start editing with what was typed
      const v = this.input.value;
      if (v && !this.cb.isEditing()) {
        this.input.value = "";
        this.cb.onEditStart(v, true);
      }
    });
    const thumbDown = (kind: "vthumb" | "hthumb") => (e: PointerEvent) => {
      e.preventDefault();
      e.stopPropagation();
      const rect = (kind === "vthumb" ? this.vthumb : this.hthumb).getBoundingClientRect();
      this.drag = { kind, startX: e.clientX, startY: e.clientY, grab: kind === "vthumb" ? e.clientY - rect.top : e.clientX - rect.left };
      this.host.classList.add("dragging-thumb");
    };
    this.vthumb.addEventListener("pointerdown", thumbDown("vthumb"));
    this.hthumb.addEventListener("pointerdown", thumbDown("hthumb"));
    const trackDown = (vertical: boolean) => (e: PointerEvent) => {
      if (e.target !== (vertical ? this.vbar : this.hbar)) return;
      const rect = (vertical ? this.vthumb : this.hthumb).getBoundingClientRect();
      if (vertical) this.scrollBy(0, (e.clientY < rect.top ? -1 : 1) * this.viewH * 0.9);
      else this.scrollBy((e.clientX < rect.left ? -1 : 1) * this.viewW * 0.9, 0);
    };
    this.vbar.addEventListener("pointerdown", trackDown(true));
    this.hbar.addEventListener("pointerdown", trackDown(false));
  }

  focus() {
    this.input.focus({ preventScroll: true });
  }

  private onWheel(e: WheelEvent) {
    e.preventDefault();
    let dx = e.deltaX;
    let dy = e.deltaY;
    if (e.deltaMode === 1) {
      dx *= this.rowH;
      dy *= this.rowH;
    } else if (e.deltaMode === 2) {
      dx *= this.viewW;
      dy *= this.viewH;
    }
    if (e.shiftKey && !dx) {
      dx = dy;
      dy = 0;
    }
    if (e.ctrlKey) return; // pinch zoom: ignore
    // notched wheels send big deltas → ease them; touchpads send small ones → follow directly
    const notched = Math.abs(dy) >= 50 || Math.abs(dx) >= 50;
    this.tau = notched ? 0.085 : 0.018;
    this.scrollBy(dx, dy, true);
  }

  hitTest(clientX: number, clientY: number): Hit {
    const rect = this.canvas.getBoundingClientRect();
    const x = clientX - rect.left;
    const y = clientY - rect.top;
    const base: Hit = { area: "none", r: -1, c: -1, x, y };
    if (x < this.rowHeaderW && y < HEADER_H) return { ...base, area: "corner" };
    if (y < HEADER_H) {
      const c = this.colAt(x);
      // column border (resize)
      for (const cc of [c - 1, c]) {
        if (cc < 0) continue;
        const bx = this.xOf(this.colX[cc + 1], cc < this.fc);
        if (Math.abs(x - bx) <= 4) return { ...base, area: "colHeader", c, border: cc };
      }
      return { ...base, area: "colHeader", c };
    }
    if (x < this.rowHeaderW) return { ...base, area: "rowHeader", r: this.rowAt(y) };
    const r = this.rowAt(y);
    const c = this.colAt(x);
    const hit: Hit = { ...base, area: "cell", r, c };
    // fill handle
    const pr = this.sel.ranges[this.sel.ranges.length - 1];
    const fh = this.cellRect(pr.r2, pr.c2);
    if (Math.abs(x - (fh.x + fh.w)) <= 5 && Math.abs(y - (fh.y + fh.h)) <= 5 && !this.cb.isEditing()) hit.fillHandle = true;
    const cr = this.cellRect(r, c);
    if (r === 0 && this.sheet?.filter_on && c < (this.sheet?.ncols ?? 0) && x > cr.x + cr.w - 24) hit.filterBtn = true;
    if (r > 0 && this.dropdownCols.has(c) && x > cr.x + cr.w - 22 && this.sel.active.r === r && this.sel.active.c === c)
      hit.dropdown = true;
    if (r > 0 && this.checkboxCols.has(c)) {
      const cx = cr.x + cr.w / 2;
      if (Math.abs(x - cx) < 10 && Math.abs(y - (cr.y + cr.h / 2)) < 10) hit.checkbox = true;
    }
    return hit;
  }

  private onPointerDown(e: PointerEvent) {
    if (e.button === 2) return;
    this.focusSoon();
    const hit = this.hitTest(e.clientX, e.clientY);
    const additive = e.ctrlKey || e.metaKey;
    if (hit.area === "corner") {
      this.commitIfEditing();
      this.setSel({ ranges: [{ r1: 0, c1: 0, r2: this.totalRows - 1, c2: this.totalCols - 1 }], active: { r: 0, c: 0 }, anchor: { r: 0, c: 0 } }, "click");
      return;
    }
    if (hit.area === "colHeader") {
      if (hit.border !== undefined && e.button === 0) {
        this.drag = { kind: "resize", startX: e.clientX, startY: e.clientY, col: hit.border, startW: this.colWidth(hit.border) };
        this.host.classList.add("resizing");
        return;
      }
      if (this.cb.isEditing() && this.cb.canPickRef()) {
        // editing a formula: a column header inserts a whole-column reference (e.g. Sheet1!M:M)
        e.preventDefault();
        this.cb.onRefPick({ r1: 0, c1: hit.c, r2: this.totalRows - 1, c2: hit.c }, "start");
        this.drag = { kind: "refcol", startX: e.clientX, startY: e.clientY, origin: { r: 0, c: hit.c } };
        return;
      }
      this.commitIfEditing();
      const c = hit.c;
      if (e.shiftKey) {
        const a = this.sel.anchor.c;
        this.setSel({ ranges: [{ r1: 0, c1: Math.min(a, c), r2: this.totalRows - 1, c2: Math.max(a, c) }], active: this.sel.active, anchor: this.sel.anchor }, "click");
      } else {
        const range = { r1: 0, c1: c, r2: this.totalRows - 1, c2: c };
        this.setSel({ ranges: additive ? [...this.sel.ranges, range] : [range], active: { r: Math.max(1, this.fr), c }, anchor: { r: 0, c } }, "click");
      }
      this.drag = { kind: "colsel", startX: e.clientX, startY: e.clientY, origin: { r: 0, c } };
      return;
    }
    if (hit.area === "rowHeader") {
      if (this.cb.isEditing() && this.cb.canPickRef()) {
        // editing a formula: a row header inserts a whole-row reference (e.g. 5:5)
        e.preventDefault();
        this.cb.onRefPick({ r1: hit.r, c1: 0, r2: hit.r, c2: this.totalCols - 1 }, "start");
        this.drag = { kind: "refrow", startX: e.clientX, startY: e.clientY, origin: { r: hit.r, c: 0 } };
        return;
      }
      this.commitIfEditing();
      const r = hit.r;
      if (e.shiftKey) {
        const a = this.sel.anchor.r;
        this.setSel({ ranges: [{ r1: Math.min(a, r), c1: 0, r2: Math.max(a, r), c2: this.totalCols - 1 }], active: this.sel.active, anchor: this.sel.anchor }, "click");
      } else {
        const range = { r1: r, c1: 0, r2: r, c2: this.totalCols - 1 };
        this.setSel({ ranges: additive ? [...this.sel.ranges, range] : [range], active: { r, c: 0 }, anchor: { r, c: 0 } }, "click");
      }
      this.drag = { kind: "rowsel", startX: e.clientX, startY: e.clientY, origin: { r, c: 0 } };
      return;
    }
    if (hit.area !== "cell") return;
    if (this.cb.isEditing() && this.cb.canPickRef()) {
      e.preventDefault();
      const rect = { r1: hit.r, c1: hit.c, r2: hit.r, c2: hit.c };
      this.cb.onRefPick(rect, "start");
      this.drag = { kind: "ref", startX: e.clientX, startY: e.clientY, origin: { r: hit.r, c: hit.c } };
      return;
    }
    if (hit.filterBtn) {
      const cr = this.cellRect(0, hit.c);
      const br = this.canvas.getBoundingClientRect();
      this.cb.onFilterButton(hit.c, new DOMRect(br.left + cr.x + cr.w - 24, br.top + cr.y, 24, cr.h));
      return;
    }
    if (hit.fillHandle) {
      this.drag = { kind: "fill", startX: e.clientX, startY: e.clientY };
      return;
    }
    if (hit.checkbox) {
      const v = this.value(hit.r, hit.c);
      this.commitIfEditing();
      this.setSel({ ranges: [{ r1: hit.r, c1: hit.c, r2: hit.r, c2: hit.c }], active: { r: hit.r, c: hit.c }, anchor: { r: hit.r, c: hit.c } }, "click");
      this.cb.onCheckbox(hit.r, hit.c, !(v === true || v === "TRUE"));
      return;
    }
    if (hit.dropdown) {
      const cr = this.cellRect(hit.r, hit.c);
      this.cb.onDropdown(hit.r, hit.c, cr);
      return;
    }
    this.commitIfEditing();
    const { r, c } = hit;
    if (e.shiftKey) {
      const a = this.sel.anchor;
      const rect = { r1: Math.min(a.r, r), c1: Math.min(a.c, c), r2: Math.max(a.r, r), c2: Math.max(a.c, c) };
      const ranges = [...this.sel.ranges.slice(0, -1), rect];
      this.setSel({ ranges, active: this.sel.active, anchor: a }, "click");
    } else {
      const rect = { r1: r, c1: c, r2: r, c2: c };
      this.setSel({ ranges: additive ? [...this.sel.ranges, rect] : [rect], active: { r, c }, anchor: { r, c } }, "click");
    }
    this.drag = { kind: "select", startX: e.clientX, startY: e.clientY, origin: e.shiftKey ? this.sel.anchor : { r, c }, add: additive };
  }

  private focusSoon() {
    if (!this.cb.isEditing()) window.setTimeout(() => this.focus(), 0);
  }

  private commitIfEditing() {
    if (this.cb.isEditing()) this.cb.onEditCommit(null);
  }

  private onPointerMove(e: PointerEvent) {
    const d = this.drag;
    if (!d) {
      if (e.target !== this.canvas) return;
      const hit = this.hitTest(e.clientX, e.clientY);
      this.hover = hit;
      let cursor = "cell";
      if (hit.area === "colHeader") cursor = hit.border !== undefined ? "col-resize" : "s-resize";
      else if (hit.area === "rowHeader") cursor = "e-resize";
      else if (hit.area === "corner") cursor = "se-resize";
      else if (hit.fillHandle) cursor = "crosshair";
      else if (hit.filterBtn || hit.dropdown || hit.checkbox) cursor = "pointer";
      this.canvas.dataset.cursor = cursor;
      this.cb.onHover(hit, e.clientX, e.clientY);
      this.invalidateDraw();
      return;
    }
    d.lastClientX = e.clientX;
    d.lastClientY = e.clientY;
    if (d.kind === "vthumb" || d.kind === "hthumb") {
      const vertical = d.kind === "vthumb";
      const bar = (vertical ? this.vbar : this.hbar).getBoundingClientRect();
      const thumb = (vertical ? this.vthumb : this.hthumb).getBoundingClientRect();
      const track = vertical ? bar.height - thumb.height : bar.width - thumb.width;
      const pos = (vertical ? e.clientY - bar.top : e.clientX - bar.left) - (d.grab ?? 0);
      const frac = Math.min(1, Math.max(0, pos / Math.max(1, track)));
      if (vertical) this.scrollTo(null, frac * this.maxSy, false);
      else this.scrollTo(frac * this.maxSx, null, false);
      this.showScrollbars();
      return;
    }
    if (d.kind === "resize") {
      const w = Math.max(28, (d.startW ?? 100) + e.clientX - d.startX);
      this.widthOverride.set(d.col!, w);
      this.computeGeometry();
      this.invalidateDraw();
      this.cb.onView();
      return;
    }
    this.updateAutoScroll(e.clientX, e.clientY);
    this.dragTo(e.clientX, e.clientY);
  }

  private dragTo(clientX: number, clientY: number) {
    const d = this.drag;
    if (!d) return;
    const rect = this.canvas.getBoundingClientRect();
    const x = Math.min(Math.max(clientX - rect.left, this.rowHeaderW + 1), this.width - 1);
    const y = Math.min(Math.max(clientY - rect.top, HEADER_H + 1), this.height - 1);
    const r = Math.max(0, this.rowAt(y));
    const c = Math.max(0, this.colAt(x));
    if (d.kind === "select" && d.origin) {
      const o = d.origin;
      const rr = { r1: Math.min(o.r, r), c1: Math.min(o.c, c), r2: Math.max(o.r, r), c2: Math.max(o.c, c) };
      const cur = this.sel.ranges[this.sel.ranges.length - 1];
      if (cur.r1 !== rr.r1 || cur.r2 !== rr.r2 || cur.c1 !== rr.c1 || cur.c2 !== rr.c2) {
        this.setSel({ ranges: [...this.sel.ranges.slice(0, -1), rr], active: this.sel.active, anchor: o }, "drag");
      }
    } else if (d.kind === "colsel" && d.origin) {
      const o = d.origin.c;
      this.setSel({ ranges: [...this.sel.ranges.slice(0, -1), { r1: 0, c1: Math.min(o, c), r2: this.totalRows - 1, c2: Math.max(o, c) }], active: this.sel.active, anchor: this.sel.anchor }, "drag");
    } else if (d.kind === "rowsel" && d.origin) {
      const o = d.origin.r;
      this.setSel({ ranges: [...this.sel.ranges.slice(0, -1), { r1: Math.min(o, r), c1: 0, r2: Math.max(o, r), c2: this.totalCols - 1 }], active: this.sel.active, anchor: this.sel.anchor }, "drag");
    } else if (d.kind === "ref" && d.origin) {
      const o = d.origin;
      this.cb.onRefPick({ r1: Math.min(o.r, r), c1: Math.min(o.c, c), r2: Math.max(o.r, r), c2: Math.max(o.c, c) }, "move");
    } else if (d.kind === "refcol" && d.origin) {
      const o = d.origin.c;
      this.cb.onRefPick({ r1: 0, c1: Math.min(o, c), r2: this.totalRows - 1, c2: Math.max(o, c) }, "move");
    } else if (d.kind === "refrow" && d.origin) {
      const o = d.origin.r;
      this.cb.onRefPick({ r1: Math.min(o, r), c1: 0, r2: Math.max(o, r), c2: this.totalCols - 1 }, "move");
    } else if (d.kind === "fill") {
      const src = this.sel.ranges[this.sel.ranges.length - 1];
      let to: Rect = { ...src };
      const dr = r > src.r2 ? r - src.r2 : r < src.r1 ? r - src.r1 : 0;
      const dc = c > src.c2 ? c - src.c2 : c < src.c1 ? c - src.c1 : 0;
      if (Math.abs(dr) >= Math.abs(dc) && dr !== 0) to = dr > 0 ? { ...src, r2: r } : { ...src, r1: Math.max(1, r) };
      else if (dc !== 0) to = dc > 0 ? { ...src, c2: c } : { ...src, c1: c };
      d.fillTo = to;
      this.invalidateDraw();
    }
  }

  private updateAutoScroll(clientX: number, clientY: number) {
    const rect = this.canvas.getBoundingClientRect();
    const x = clientX - rect.left;
    const y = clientY - rect.top;
    const edge = 28;
    const speed = (dist: number) => Math.sign(dist) * Math.min(40, Math.abs(dist) * 0.6 + 4);
    this.autoScroll.vx = x > this.width - edge ? speed(x - (this.width - edge)) : x < this.rowHeaderW + this.frozenW + 4 && this.sx > 0 ? -speed(this.rowHeaderW + this.frozenW + 4 - x) : 0;
    this.autoScroll.vy = y > this.height - edge ? speed(y - (this.height - edge)) : y < HEADER_H + this.frozenH + 4 && this.sy > 0 ? -speed(HEADER_H + this.frozenH + 4 - y) : 0;
  }

  private onPointerUp(_e: PointerEvent) {
    const d = this.drag;
    if (!d) return;
    this.drag = null;
    this.autoScroll = { vx: 0, vy: 0 };
    this.host.classList.remove("resizing", "dragging-thumb");
    if (d.kind === "resize" && d.col !== undefined) {
      const w = this.widthOverride.get(d.col);
      if (w !== undefined) this.cb.onResizeColumn(d.col, Math.round(w));
    } else if (d.kind === "fill" && d.fillTo) {
      const src = this.sel.ranges[this.sel.ranges.length - 1];
      const to = d.fillTo;
      if (to.r1 !== src.r1 || to.r2 !== src.r2 || to.c1 !== src.c1 || to.c2 !== src.c2) {
        this.cb.onFill(src, to);
        this.setSel({ ranges: [to], active: this.sel.active, anchor: this.sel.anchor }, "api");
      }
      this.invalidateDraw();
    } else if ((d.kind === "ref" || d.kind === "refcol" || d.kind === "refrow") && d.origin) {
      this.cb.onRefPick({ r1: -1, c1: -1, r2: -1, c2: -1 }, "end");
    }
  }

  private onDblClick(e: MouseEvent) {
    const hit = this.hitTest(e.clientX, e.clientY);
    if (hit.area === "colHeader" && hit.border !== undefined) {
      this.cb.onAutofit(hit.border);
      return;
    }
    if (hit.fillHandle) {
      this.cb.onFillColumn();
      return;
    }
    if (hit.area === "cell" && !hit.filterBtn && !hit.checkbox) {
      this.cb.onEditStart(null, true);
    }
  }

  private onContext(e: MouseEvent) {
    e.preventDefault();
    const hit = this.hitTest(e.clientX, e.clientY);
    if (hit.area === "cell") {
      const inside = this.sel.ranges.some((rg) => hit.r >= rg.r1 && hit.r <= rg.r2 && hit.c >= rg.c1 && hit.c <= rg.c2);
      if (!inside) {
        this.commitIfEditing();
        this.setSel({ ranges: [{ r1: hit.r, c1: hit.c, r2: hit.r, c2: hit.c }], active: { r: hit.r, c: hit.c }, anchor: { r: hit.r, c: hit.c } }, "click");
      }
    } else if (hit.area === "colHeader") {
      const inside = this.sel.ranges.some((rg) => rg.r1 === 0 && rg.r2 >= this.totalRows - 1 && hit.c >= rg.c1 && hit.c <= rg.c2);
      if (!inside) this.setSel({ ranges: [{ r1: 0, c1: hit.c, r2: this.totalRows - 1, c2: hit.c }], active: { r: Math.max(1, this.fr), c: hit.c }, anchor: { r: 0, c: hit.c } }, "click");
    } else if (hit.area === "rowHeader") {
      const inside = this.sel.ranges.some((rg) => rg.c1 === 0 && rg.c2 >= this.totalCols - 1 && hit.r >= rg.r1 && hit.r <= rg.r2);
      if (!inside) this.setSel({ ranges: [{ r1: hit.r, c1: 0, r2: hit.r, c2: this.totalCols - 1 }], active: { r: hit.r, c: 0 }, anchor: { r: hit.r, c: 0 } }, "click");
    }
    this.cb.onContextMenu(hit, e.clientX, e.clientY);
  }

  setSel(sel: Selection, reason: "click" | "drag" | "key" | "api") {
    const last = sel.ranges[sel.ranges.length - 1];
    const m = this.mergeFor(sel.active.r, sel.active.c);
    if (m && last.r1 === last.r2 && last.c1 === last.c2) {
      // clicking any part of a merged cell selects the whole merge
      sel = { ranges: [...sel.ranges.slice(0, -1), { ...m }], active: { r: m.r1, c: m.c1 }, anchor: { r: m.r1, c: m.c1 } };
    } else if (reason === "drag" || reason === "key") {
      // grow a range so it never cuts a merged cell in half
      let g = { ...last };
      for (let pass = 0; pass < 3; pass++) {
        for (const mm of this.sheet?.filtered ? [] : this.sheet?.merges || []) {
          if (mm.r1 <= g.r2 && mm.r2 >= g.r1 && mm.c1 <= g.c2 && mm.c2 >= g.c1) {
            g = { r1: Math.min(g.r1, mm.r1), c1: Math.min(g.c1, mm.c1), r2: Math.max(g.r2, mm.r2), c2: Math.max(g.c2, mm.c2) };
          }
        }
      }
      sel = { ...sel, ranges: [...sel.ranges.slice(0, -1), g] };
    }
    this.sel = sel;
    if (reason !== "drag") this.scrollIntoView(sel.active.r, sel.active.c, true);
    else {
      const pr = sel.ranges[sel.ranges.length - 1];
      const tgtR = pr.r2 === sel.anchor.r ? pr.r1 : pr.r2;
      const tgtC = pr.c2 === sel.anchor.c ? pr.c1 : pr.c2;
      this.scrollIntoView(tgtR, tgtC, false);
    }
    this.cb.onSelect(sel, reason);
    this.invalidateDraw();
  }

  // ----------------------------------------------------------------- keyboard

  private onKeyDown(e: KeyboardEvent) {
    if (this.cb.onKey(e)) {
      e.preventDefault();
      return;
    }
    const mod = e.ctrlKey || e.metaKey;
    const { active } = this.sel;
    const lastRow = this.totalRows - 1;
    const lastCol = this.totalCols - 1;
    const dataRows = (this.sheet?.filtered ? this.sheet.nview : this.sheet?.nrows) ?? 0;
    const dataCols = Math.max(1, this.sheet?.ncols ?? 1);
    let dr = 0;
    let dc = 0;
    let jump: { r?: number; c?: number } | null = null;
    switch (e.key) {
      case "ArrowDown":
        if (mod) jump = { r: active.r < dataRows ? dataRows : lastRow };
        else dr = 1;
        break;
      case "ArrowUp":
        if (mod) jump = { r: active.r > 1 && active.r <= dataRows ? 1 : 0 };
        else dr = -1;
        break;
      case "ArrowRight":
        if (mod) jump = { c: active.c < dataCols - 1 ? dataCols - 1 : lastCol };
        else dc = 1;
        break;
      case "ArrowLeft":
        if (mod) jump = { c: 0 };
        else dc = -1;
        break;
      case "PageDown":
        dr = Math.max(1, Math.floor(this.viewH / this.rowH) - 1);
        break;
      case "PageUp":
        dr = -Math.max(1, Math.floor(this.viewH / this.rowH) - 1);
        break;
      case "Home":
        jump = mod ? { r: 0, c: 0 } : { c: 0 };
        break;
      case "End":
        jump = mod ? { r: dataRows, c: dataCols - 1 } : { c: dataCols - 1 };
        break;
      case "Tab":
        e.preventDefault();
        this.move(0, e.shiftKey ? -1 : 1, false);
        return;
      case "Enter":
        e.preventDefault();
        if (e.shiftKey) this.move(-1, 0, false);
        else this.cb.onEditStart(null, true);
        return;
      case "F2":
        e.preventDefault();
        this.cb.onEditStart(null, true);
        return;
      case "Escape":
        this.copyRect = null;
        this.invalidateDraw();
        return;
      default:
        if (!mod && !e.altKey && e.key.length === 1) {
          e.preventDefault();
          this.cb.onEditStart(e.key, true);
        }
        return;
    }
    e.preventDefault();
    if (jump) {
      const nr = jump.r ?? active.r;
      const nc = jump.c ?? active.c;
      if (e.shiftKey) this.extendTo(nr, nc);
      else this.selectCell(nr, nc);
      return;
    }
    if (e.shiftKey) {
      const pr = this.sel.ranges[this.sel.ranges.length - 1];
      const a = this.sel.anchor;
      // move the edge opposite to the anchor
      const edgeR = pr.r1 === a.r ? pr.r2 : pr.r1;
      const edgeC = pr.c1 === a.c ? pr.c2 : pr.c1;
      this.extendTo(Math.min(lastRow, Math.max(0, edgeR + dr)), Math.min(lastCol, Math.max(0, edgeC + dc)));
    } else this.move(dr, dc, true);
  }

  move(dr: number, dc: number, collapse: boolean) {
    const { active } = this.sel;
    const pr = this.sel.ranges[this.sel.ranges.length - 1];
    const multi = !collapse && (pr.r1 !== pr.r2 || pr.c1 !== pr.c2);
    if (multi) {
      // Tab/Enter cycle inside the selection like Sheets
      let r = active.r + dr;
      let c = active.c + dc;
      if (c > pr.c2) {
        c = pr.c1;
        r++;
      }
      if (c < pr.c1) {
        c = pr.c2;
        r--;
      }
      if (r > pr.r2) r = pr.r1;
      if (r < pr.r1) r = pr.r2;
      this.setSel({ ranges: this.sel.ranges, active: { r, c }, anchor: this.sel.anchor }, "key");
      return;
    }
    const m = this.mergeFor(active.r, active.c);
    const baseR = m && dr > 0 ? m.r2 : active.r;
    const baseC = m && dc > 0 ? m.c2 : active.c;
    const r = Math.min(this.totalRows - 1, Math.max(0, baseR + dr));
    const c = Math.min(this.totalCols - 1, Math.max(0, baseC + dc));
    this.selectCell(r, c);
  }

  selectCell(r: number, c: number, reason: "key" | "api" = "key") {
    this.setSel({ ranges: [{ r1: r, c1: c, r2: r, c2: c }], active: { r, c }, anchor: { r, c } }, reason);
  }

  selectRect(rect: Rect, reason: "key" | "api" = "api") {
    this.setSel({ ranges: [rect], active: { r: rect.r1, c: rect.c1 }, anchor: { r: rect.r1, c: rect.c1 } }, reason);
  }

  extendTo(r: number, c: number) {
    const a = this.sel.anchor;
    const rect = { r1: Math.min(a.r, r), c1: Math.min(a.c, c), r2: Math.max(a.r, r), c2: Math.max(a.c, c) };
    this.setSel({ ranges: [...this.sel.ranges.slice(0, -1), rect], active: this.sel.active, anchor: a }, "key");
    this.scrollIntoView(r, c, true);
  }

  // ------------------------------------------------------------------ drawing

  private loop = (now: number) => {
    if (this.destroyed) return;
    const dt = Math.min(0.05, (now - (this.lastFrame || now)) / 1000);
    this.lastFrame = now;
    let moving = false;
    // auto-scroll during drags
    if (this.drag && (this.autoScroll.vx || this.autoScroll.vy)) {
      this.scrollBy(this.autoScroll.vx, this.autoScroll.vy, false);
      if (this.drag.lastClientX !== undefined) this.dragTo(this.drag.lastClientX, this.drag.lastClientY!);
      moving = true;
    }
    // eased scrolling
    const k = 1 - Math.exp(-dt / this.tau);
    if (Math.abs(this.tx - this.sx) > 0.3 || Math.abs(this.ty - this.sy) > 0.3) {
      this.sx += (this.tx - this.sx) * k;
      this.sy += (this.ty - this.sy) * k;
      if (Math.abs(this.tx - this.sx) < 0.4) this.sx = this.tx;
      if (Math.abs(this.ty - this.sy) < 0.4) this.sy = this.ty;
      moving = true;
      this.requestData();
      this.cb.onView();
    }
    // selection animation
    const pr = this.sel.ranges[this.sel.ranges.length - 1];
    const target = this.rectContent(pr);
    const a = this.anim;
    if (!a.init || this.reducedMotion) {
      Object.assign(a, target, { init: true });
    } else {
      const ks = 1 - Math.exp(-dt / 0.045);
      const dx = target.x - a.x;
      const dy = target.y - a.y;
      const dw = target.w - a.w;
      const dh = target.h - a.h;
      if (Math.abs(dx) + Math.abs(dy) + Math.abs(dw) + Math.abs(dh) > 0.5) {
        if (Math.abs(dy) > this.viewH * 2 || Math.abs(dx) > this.width * 2) Object.assign(a, target);
        else {
          a.x += dx * ks;
          a.y += dy * ks;
          a.w += dw * ks;
          a.h += dh * ks;
        }
        moving = true;
      } else Object.assign(a, target);
    }
    const animatedExtras = !!this.copyRect || this.hasLoadingVisible;
    if (this.dirty || moving || animatedExtras) {
      this.draw(now);
      this.updateScrollbars();
      this.dirty = false;
    }
    this.raf = requestAnimationFrame(this.loop);
  };

  hasLoadingVisible = false;

  rectContent(r: Rect) {
    const r2 = Math.min(r.r2, this.totalRows - 1);
    const c2 = Math.min(r.c2, this.totalCols - 1);
    return {
      x: this.colX[r.c1],
      y: r.r1 * this.rowH,
      w: this.colX[c2 + 1] - this.colX[r.c1],
      h: (r2 - r.r1 + 1) * this.rowH,
    };
  }

  private px(v: number) {
    return (Math.round(v * this.dpr) + 0.5) / this.dpr;
  }

  private measure(text: string, font: string) {
    const key = font.length + font[0] + text;
    let w = this.textWidthCache.get(key);
    if (w === undefined) {
      this.ctx.font = font;
      w = this.ctx.measureText(text).width;
      if (this.textWidthCache.size > 20000) this.textWidthCache.clear();
      this.textWidthCache.set(key, w);
    }
    return w;
  }

  private visibleStyles(r0: number, r1: number, c0: number, c1: number) {
    const styles = this.sheet?.styles || [];
    if (!styles.length) return [];
    return styles.filter((s) => s.c2 >= c0 && s.c1 <= c1 && (s.r2 === null || s.r2 >= r0) && s.r1 <= r1);
  }

  private draw(now: number) {
    const ctx = this.ctx;
    const t = this.theme;
    const W = this.width;
    const H = this.height;
    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    ctx.fillStyle = t.bg;
    ctx.fillRect(0, 0, W, H);
    if (!this.sheet) return;
    this.hasLoadingVisible = false;
    const [vr0, vr1] = this.visibleRows();
    const [vc0, vc1] = this.visibleCols();
    const fr = this.fr;
    const fc = this.fc;
    const fw = this.frozenW;
    const fh = this.frozenH;
    const RH = this.rowHeaderW;
    const quads = [
      { rows: [vr0, vr1], cols: [vc0, vc1], fx: false, fy: false, x: RH + fw, y: HEADER_H + fh, w: W - RH - fw, h: H - HEADER_H - fh },
      { rows: [0, fr - 1], cols: [vc0, vc1], fx: false, fy: true, x: RH + fw, y: HEADER_H, w: W - RH - fw, h: fh },
      { rows: [vr0, vr1], cols: [0, fc - 1], fx: true, fy: false, x: RH, y: HEADER_H + fh, w: fw, h: H - HEADER_H - fh },
      { rows: [0, fr - 1], cols: [0, fc - 1], fx: true, fy: true, x: RH, y: HEADER_H, w: fw, h: fh },
    ];
    for (const q of quads) {
      if (q.w <= 0 || q.h <= 0 || q.rows[1] < q.rows[0] || q.cols[1] < q.cols[0]) continue;
      ctx.save();
      ctx.beginPath();
      ctx.rect(q.x, q.y, q.w, q.h);
      ctx.clip();
      this.drawCells(q.rows[0], q.rows[1], q.cols[0], q.cols[1], q.fx, q.fy, now);
      this.drawOverlays(q.fx, q.fy, now);
      ctx.restore();
    }
    // frozen separators + shadows
    if (fr > 0) {
      const y = HEADER_H + fh;
      ctx.fillStyle = t.frozenLine;
      ctx.fillRect(0, y - 1, W, 2);
      if (this.sy > 1) {
        const g = ctx.createLinearGradient(0, y, 0, y + 8);
        g.addColorStop(0, t.shadow);
        g.addColorStop(1, "rgba(0,0,0,0)");
        ctx.fillStyle = g;
        ctx.fillRect(RH, y + 1, W - RH, 8);
      }
    }
    if (fc > 0) {
      const x = RH + fw;
      ctx.fillStyle = t.frozenLine;
      ctx.fillRect(x - 1, 0, 2, H);
      if (this.sx > 1) {
        const g = ctx.createLinearGradient(x, 0, x + 8, 0);
        g.addColorStop(0, t.shadow);
        g.addColorStop(1, "rgba(0,0,0,0)");
        ctx.fillStyle = g;
        ctx.fillRect(x + 1, HEADER_H, 8, H - HEADER_H);
      }
    }
    this.drawHeaders(vr0, vr1, vc0, vc1);
  }

  private resolveStyle(styles: SheetInfo["styles"], srow: number, c: number): CellStyle | null {
    let st: CellStyle | null = null;
    if (styles.length && srow >= 0) {
      for (const s of styles) {
        if (c >= s.c1 && c <= s.c2 && srow >= s.r1 && (s.r2 === null || srow <= s.r2)) st = Object.assign({}, st ?? {}, s) as CellStyle;
      }
    }
    return st;
  }

  /** Merges visible in a block of visual rows (merges are hidden while a filter is active). */
  private visibleMerges(r0: number, r1: number, c0: number, c1: number): Rect[] {
    const ms = this.sheet?.merges;
    if (!ms || !ms.length || this.sheet?.filtered) return [];
    return ms.filter((m) => m.r1 <= r1 && m.r2 >= r0 && m.c1 <= c1 && m.c2 >= c0);
  }

  mergeFor(r: number, c: number): Rect | null {
    const ms = this.sheet?.merges;
    if (!ms || !ms.length || this.sheet?.filtered) return null;
    for (const m of ms) if (r >= m.r1 && r <= m.r2 && c >= m.c1 && c <= m.c2) return m;
    return null;
  }

  private drawCells(r0: number, r1: number, c0: number, c1: number, fx: boolean, fy: boolean, now: number) {
    const ctx = this.ctx;
    const t = this.theme;
    const sh = this.sheet!;
    const rowH = this.rowH;
    const styles = this.visibleStyles(this.sheetRow(r0), Math.max(this.sheetRow(r1), r1), c0, c1);
    const merges = this.visibleMerges(r0, r1, c0, c1);
    const borders: { x: number; y: number; w: number; h: number; st: CellStyle }[] = [];
    // backgrounds + text
    for (let r = r0; r <= r1; r++) {
      const y = this.yOf(r * rowH, fy);
      const srow = this.sheetRow(r);
      const isHeader = r === 0;
      if (isHeader) {
        ctx.fillStyle = t.row0Bg;
        const x0 = this.xOf(this.colX[c0], fx);
        ctx.fillRect(x0, y, this.xOf(this.colX[c1 + 1], fx) - x0, rowH);
      }
      for (let c = c0; c <= c1; c++) {
        const x = this.xOf(this.colX[c], fx);
        const w = this.colX[c + 1] - this.colX[c];
        if (merges.length && merges.some((m) => r >= m.r1 && r <= m.r2 && c >= m.c1 && c <= m.c2)) continue;
        if (!this.hasValue(r, c)) {
          if (c < sh.ncols && (isHeader || (srow > 0 && srow <= sh.nrows) || sh.filtered)) {
            this.hasLoadingVisible = true;
            const phase = (now / 900 + c * 0.07 + r * 0.03) % 1;
            ctx.globalAlpha = 0.35 + 0.35 * Math.sin(phase * Math.PI * 2) ** 2;
            ctx.fillStyle = t.shimmer;
            const bw = Math.min(w - 2 * PAD, 24 + ((r * 7 + c * 13) % 5) * 12);
            this.roundRect(x + PAD, y + rowH / 2 - 4, Math.max(8, bw), 8, 4);
            ctx.fill();
            ctx.globalAlpha = 1;
          }
          continue;
        }
        const st = this.resolveStyle(styles, srow, c);
        this.paintCell(r, c, srow, x, y, w, rowH, st, c1, false);
        if (st && (st.bt || st.bb || st.bl || st.br)) borders.push({ x, y, w, h: rowH, st });
      }
    }
    // grid lines
    ctx.beginPath();
    ctx.strokeStyle = t.grid;
    ctx.lineWidth = 1 / this.dpr;
    const yTop = this.yOf(r0 * rowH, fy);
    const yBot = this.yOf((r1 + 1) * rowH, fy);
    const xL = this.xOf(this.colX[c0], fx);
    const xR = this.xOf(this.colX[c1 + 1], fx);
    for (let c = c0; c <= c1 + 1; c++) {
      const x = this.px(this.xOf(this.colX[c], fx) - 0.5);
      ctx.moveTo(x, yTop);
      ctx.lineTo(x, yBot);
    }
    for (let r = r0; r <= r1 + 1; r++) {
      const y = this.px(this.yOf(r * rowH, fy) - 0.5);
      ctx.moveTo(xL, y);
      ctx.lineTo(xR, y);
    }
    ctx.stroke();
    if (r0 <= 0 && r1 >= 0) {
      ctx.fillStyle = t.gridStrong;
      ctx.fillRect(xL, this.yOf(rowH, fy) - 1, xR - xL, 1);
    }
    // merged cells cover the grid lines inside them
    for (const m of merges) {
      const x = this.xOf(this.colX[m.c1], fx);
      const y = this.yOf(m.r1 * rowH, fy);
      const w = this.colX[Math.min(m.c2, this.totalCols - 1) + 1] - this.colX[m.c1];
      const h = (m.r2 - m.r1 + 1) * rowH;
      ctx.fillStyle = m.r1 === 0 ? t.row0Bg : t.bg;
      ctx.fillRect(x, y, w - 1, h - 1);
      const st = this.resolveStyle(this.sheet!.styles, m.r1, m.c1);
      if (this.hasValue(m.r1, m.c1)) this.paintCell(m.r1, m.c1, m.r1, x, y, w, h, st, c1, true);
      if (st && (st.bt || st.bb || st.bl || st.br)) borders.push({ x, y, w, h, st });
    }
    for (const b of borders) this.drawBorders(b.x, b.y, b.w, b.h, b.st);
  }

  /** Paint one cell (or a merged block) at the given screen rectangle. */
  private paintCell(r: number, c: number, srow: number, x: number, y: number, w: number, h: number, st: CellStyle | null, cMax: number, merged: boolean) {
    const ctx = this.ctx;
    const t = this.theme;
    const sh = this.sheet!;
    const col: ColumnInfo | undefined = sh.columns[c];
    const cfRules = sh.cond_formats;
    const isHeader = r === 0;
    const v = this.value(r, c);
    let bg = st?.bg || null;
    let fg = st?.fg || null;
    let bold = st?.bold || isHeader;
    let italic = st?.italic;
    let strike = st?.strike;
    let invalid = false;
    let mismatch = false;
    let scaleColor: string | null = null;
    const flags = r > 0 ? this.flagsAt(r, c) : undefined;
    if (flags) {
      for (const f of flags) {
        if (f[2] === "cf") {
          const s = cfRules[f[4]]?.style;
          if (s) {
            if (s.bg) bg = s.bg;
            if (s.fg) fg = s.fg;
            if (s.bold) bold = true;
            if (s.italic) italic = true;
            if (s.strike) strike = true;
          }
        } else if (f[2] === "scale") {
          scaleColor = scaleAt(cfRules[f[4]]?.colors || ["#f8d0cf", "#fff3c4", "#c7ecd6"], Number(f[3]));
        } else if (f[2] === "invalid") invalid = true;
        else if (f[2] === "fmt") mismatch = true;
      }
    }
    if (scaleColor) bg = scaleColor;
    if (bg) {
      ctx.fillStyle = bg;
      ctx.fillRect(x, y, merged ? w - 1 : w, merged ? h - 1 : h);
      // keep text readable on custom fills in both themes
      if (!fg) fg = isLight(bg) ? "#1f2328" : "#f3f4f6";
    }
    const err = this.errorCells.get(`${srow}:${c}`);
    if (this.checkboxCols.has(c) && r > 0 && (v === true || v === false || v === "TRUE" || v === "FALSE" || v === null)) {
      this.drawCheckbox(x + w / 2, y + h / 2, v === true || v === "TRUE");
    } else if (err) {
      ctx.font = FONT_BOLD;
      ctx.fillStyle = t.error;
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillText(err.split(" ")[0], x + w / 2, y + h / 2 + 0.5);
      ctx.beginPath();
      ctx.moveTo(x + w - 7, y + 1);
      ctx.lineTo(x + w - 1, y + 1);
      ctx.lineTo(x + w - 1, y + 7);
      ctx.fill();
    } else if (v !== null && v !== undefined && v !== "") {
      const fmt = st?.fmt ?? (isHeader ? null : col?.fmt);
      const f = formatCell(v, isHeader ? "str" : col?.type, fmt);
      const align = st?.align || (merged && typeof v === "string" ? "center" : f.align);
      const font = bold ? FONT_BOLD : FONT;
      const fontUse = italic ? `italic ${font}` : font;
      ctx.font = fontUse;
      ctx.fillStyle = fg || (isHeader ? t.row0Text : f.negative && fmt?.includes("(") ? t.negative : t.text);
      ctx.textBaseline = "middle";
      let text = f.text;
      let tw = this.measure(text, fontUse);
      const filterGap = isHeader && sh.filter_on && c < sh.ncols && !merged ? 18 : 0;
      const avail = w - 2 * PAD - filterGap;
      // let text overflow into empty neighbours (like Sheets)
      let overflow = 0;
      if (tw > avail && align === "left" && !isHeader && !merged) {
        let cc = c + 1;
        while (cc <= cMax + 3 && cc < this.totalCols && tw > avail + overflow) {
          const nv = this.value(r, cc);
          if (nv !== null && nv !== undefined && nv !== "") break;
          if (this.mergeFor(r, cc)) break;
          overflow += this.colX[cc + 1] - this.colX[cc];
          cc++;
        }
      }
      const numeric = !isHeader && (typeof v === "number" || (f.align === "right" && typeof v === "string" && isNumericText(v)));
      if (tw > avail + overflow && numeric && align !== "left") {
        const auto = !fmt || fmt === "auto" || fmt === "General";
        const fitted = auto ? fitNumber(Number(v), (s) => this.measure(s, fontUse) <= avail) : null;
        text = fitted ?? "#".repeat(Math.max(1, Math.floor(avail / this.measure("#", fontUse))));
        tw = this.measure(text, fontUse);
      }
      const needsClip = tw > avail + overflow;
      if (needsClip) {
        ctx.save();
        ctx.beginPath();
        ctx.rect(x + 1, y, w - 2 + overflow - (filterGap ? 22 : 0), h);
        ctx.clip();
      }
      let tx = x + PAD;
      if (align === "right") tx = x + w - PAD - tw - filterGap;
      else if (align === "center") tx = x + (w - tw) / 2;
      ctx.textAlign = "left";
      const ty = y + h / 2 + 0.5;
      ctx.fillText(text, tx, ty);
      if (strike) ctx.fillRect(tx, ty - 0.5, tw, 1);
      if (st?.underline) ctx.fillRect(tx, ty + 6.5, tw, 1);
      if (needsClip) ctx.restore();
    }
    if (mismatch) {
      // lookup matched only after normalising formats (text vs number, spaces, zeros…)
      ctx.fillStyle = t.warning;
      ctx.beginPath();
      ctx.moveTo(x + 1, y + 1);
      ctx.lineTo(x + 8, y + 1);
      ctx.lineTo(x + 1, y + 8);
      ctx.fill();
    }
    if (invalid) {
      ctx.fillStyle = t.invalid;
      ctx.beginPath();
      ctx.moveTo(x + w - 7, y + 1);
      ctx.lineTo(x + w - 1, y + 1);
      ctx.lineTo(x + w - 1, y + 7);
      ctx.fill();
    }
    if (isHeader && sh.filter_on && c < sh.ncols && !merged) this.drawFilterButton(x + w - 22, y + 4, !!(col && sh.filters[col.id]));
    if (r > 0 && this.dropdownCols.has(c) && this.sel.active.r === r && this.sel.active.c === c) this.drawChevron(x + w - 16, y + h / 2);
  }

  private drawBorders(x: number, y: number, w: number, h: number, st: CellStyle) {
    const ctx = this.ctx;
    const edge = (spec: string | null | undefined, x1: number, y1: number, x2: number, y2: number) => {
      if (!spec) return;
      const [line, color] = spec.split(":");
      const width = line === "thick" ? 3 : line === "medium" ? 2 : 1;
      ctx.strokeStyle = color || this.theme.text;
      ctx.lineWidth = width;
      ctx.setLineDash(line === "dashed" ? [5, 3] : line === "dotted" ? [1.5, 2.5] : []);
      const off = width % 2 ? 0.5 / this.dpr : 0;
      ctx.beginPath();
      if (line === "double") {
        ctx.lineWidth = 1;
        const dx = y1 === y2 ? 0 : 1.5;
        const dy = y1 === y2 ? 1.5 : 0;
        ctx.moveTo(x1 - dx, y1 - dy);
        ctx.lineTo(x2 - dx, y2 - dy);
        ctx.moveTo(x1 + dx, y1 + dy);
        ctx.lineTo(x2 + dx, y2 + dy);
      } else {
        ctx.moveTo(x1 + (x1 === x2 ? off : 0), y1 + (y1 === y2 ? off : 0));
        ctx.lineTo(x2 + (x1 === x2 ? off : 0), y2 + (y1 === y2 ? off : 0));
      }
      ctx.stroke();
      ctx.setLineDash([]);
    };
    const L = Math.round(x) - 1;
    const T = Math.round(y) - 1;
    const R = Math.round(x + w) - 1;
    const B = Math.round(y + h) - 1;
    edge(st.bt, L, T, R + 1, T);
    edge(st.bb, L, B, R + 1, B);
    edge(st.bl, L, T, L, B + 1);
    edge(st.br, R, T, R, B + 1);
  }

  private drawOverlays(fx: boolean, fy: boolean, now: number) {
    const ctx = this.ctx;
    const t = this.theme;
    const ox = (cx: number) => this.xOf(cx, fx);
    const oy = (cy: number) => this.yOf(cy, fy);
    // spill / column-formula outline
    if (this.spillRect) {
      const s = this.rectContent(this.spillRect);
      ctx.strokeStyle = t.spill;
      ctx.lineWidth = 1;
      ctx.setLineDash([]);
      ctx.strokeRect(this.px(ox(s.x)), this.px(oy(s.y)), s.w - 1, s.h - 1);
    }
    // find match
    if (this.findRect) {
      const s = this.rectContent(this.findRect);
      ctx.fillStyle = t.findFill;
      ctx.fillRect(ox(s.x), oy(s.y), s.w, s.h);
    }
    // other selection ranges
    const ranges = this.sel.ranges;
    ctx.fillStyle = t.selFill;
    for (let i = 0; i < ranges.length - 1; i++) {
      const s = this.rectContent(ranges[i]);
      ctx.fillRect(ox(s.x), oy(s.y), s.w, s.h);
    }
    // primary range (animated)
    const a = this.anim;
    const pr = ranges[ranges.length - 1];
    const am = this.mergeFor(this.sel.active.r, this.sel.active.c);
    const single = (pr.r1 === pr.r2 && pr.c1 === pr.c2) || (!!am && am.r1 === pr.r1 && am.c1 === pr.c1 && am.r2 === pr.r2 && am.c2 === pr.c2);
    if (!single) {
      ctx.fillStyle = t.selFill;
      ctx.fillRect(ox(a.x), oy(a.y), a.w, a.h);
      // the active cell stays unshaded
      const ac = this.rectContent({ r1: this.sel.active.r, c1: this.sel.active.c, r2: this.sel.active.r, c2: this.sel.active.c });
      ctx.fillStyle = t.bg;
      ctx.globalAlpha = 0.0;
      ctx.fillRect(ox(ac.x), oy(ac.y), ac.w, ac.h);
      ctx.globalAlpha = 1;
      ctx.strokeStyle = t.accent;
      ctx.lineWidth = 1;
      ctx.strokeRect(this.px(ox(a.x)), this.px(oy(a.y)), Math.max(0, a.w - 1), Math.max(0, a.h - 1));
      const ar = this.rectContent({ r1: this.sel.active.r, c1: this.sel.active.c, r2: this.sel.active.r, c2: this.sel.active.c });
      ctx.lineWidth = 2;
      ctx.strokeRect(ox(ar.x) + 1, oy(ar.y) + 1, ar.w - 2, ar.h - 2);
    } else {
      ctx.strokeStyle = t.accent;
      ctx.lineWidth = 2;
      ctx.strokeRect(ox(a.x) + 0.5, oy(a.y) + 0.5, Math.max(0, a.w - 1.5), Math.max(0, a.h - 1.5));
    }
    // fill handle
    if (!this.cb.isEditing()) {
      const hx = ox(a.x + a.w);
      const hy = oy(a.y + a.h);
      ctx.fillStyle = t.bg;
      ctx.fillRect(hx - 5, hy - 5, 9, 9);
      ctx.fillStyle = t.accent;
      ctx.fillRect(hx - 4, hy - 4, 7, 7);
    }
    // fill preview
    if (this.drag?.kind === "fill" && this.drag.fillTo) {
      const s = this.rectContent(this.drag.fillTo);
      ctx.setLineDash([4, 3]);
      ctx.strokeStyle = t.muted;
      ctx.lineWidth = 1;
      ctx.strokeRect(this.px(ox(s.x)), this.px(oy(s.y)), s.w - 1, s.h - 1);
      ctx.setLineDash([]);
    }
    // copy marching ants
    if (this.copyRect) {
      const s = this.rectContent(this.copyRect);
      ctx.setLineDash([5, 4]);
      ctx.lineDashOffset = -((now / 40) % 9);
      ctx.strokeStyle = t.accent;
      ctx.lineWidth = 1.5;
      ctx.strokeRect(ox(s.x) + 1, oy(s.y) + 1, s.w - 2, s.h - 2);
      ctx.setLineDash([]);
      ctx.lineDashOffset = 0;
    }
    // references while editing a formula
    for (const h of this.refHighlights) {
      const s = this.rectContent(h);
      ctx.fillStyle = h.color + "1f";
      ctx.fillRect(ox(s.x), oy(s.y), s.w, s.h);
      ctx.strokeStyle = h.color;
      ctx.lineWidth = 1.5;
      ctx.setLineDash([4, 2]);
      ctx.strokeRect(ox(s.x) + 1, oy(s.y) + 1, s.w - 2, s.h - 2);
      ctx.setLineDash([]);
    }
  }

  private drawHeaders(vr0: number, vr1: number, vc0: number, vc1: number) {
    const ctx = this.ctx;
    const t = this.theme;
    const W = this.width;
    const H = this.height;
    const RH = this.rowHeaderW;
    const sel = this.sel;
    const fullCols = new Set<number>();
    const fullRows = new Set<number>();
    const selCols = new Set<number>();
    const selRows = new Set<number>();
    for (const rg of sel.ranges) {
      const allRows = rg.r1 === 0 && rg.r2 >= this.totalRows - 1;
      const allCols = rg.c1 === 0 && rg.c2 >= this.totalCols - 1;
      for (let c = Math.max(rg.c1, 0); c <= Math.min(rg.c2, vc1 + 1); c++) {
        selCols.add(c);
        if (allRows) fullCols.add(c);
      }
      for (let r = Math.max(rg.r1, 0); r <= Math.min(rg.r2, vr1 + 1); r++) {
        selRows.add(r);
        if (allCols) fullRows.add(r);
      }
    }
    // column header
    ctx.save();
    ctx.fillStyle = t.headerBg;
    ctx.fillRect(0, 0, W, HEADER_H);
    ctx.font = FONT_HEADER;
    ctx.textBaseline = "middle";
    ctx.textAlign = "center";
    const drawCol = (c: number, fx: boolean) => {
      const x = this.xOf(this.colX[c], fx);
      const w = this.colX[c + 1] - this.colX[c];
      if (fullCols.has(c)) {
        ctx.fillStyle = t.headerFull;
        ctx.fillRect(x, 0, w, HEADER_H);
      } else if (selCols.has(c)) {
        ctx.fillStyle = t.headerSel;
        ctx.fillRect(x, 0, w, HEADER_H);
      }
      ctx.fillStyle = fullCols.has(c) ? t.headerFullText : selCols.has(c) ? t.headerSelText : t.headerText;
      ctx.fillText(colLetters(c), x + w / 2, HEADER_H / 2 + 0.5);
      const fcol = this.formulaCols.get(c);
      if (fcol) {
        ctx.fillStyle = t.spill;
        ctx.fillRect(x + 3, HEADER_H - 3, w - 6, 2);
      }
      ctx.fillStyle = t.grid;
      ctx.fillRect(Math.round(x + w) - 1, 4, 1, HEADER_H - 8);
      if (this.hover?.area === "colHeader" && this.hover.border === c) {
        ctx.fillStyle = t.accent;
        ctx.fillRect(Math.round(x + w) - 2, 0, 3, HEADER_H);
      }
    };
    ctx.beginPath();
    ctx.rect(RH + this.frozenW, 0, W - RH - this.frozenW, HEADER_H);
    ctx.save();
    ctx.clip();
    for (let c = vc0; c <= vc1; c++) drawCol(c, false);
    ctx.restore();
    for (let c = 0; c < this.fc; c++) drawCol(c, true);
    ctx.fillStyle = t.gridStrong;
    ctx.fillRect(0, HEADER_H - 1, W, 1);
    // row header
    ctx.fillStyle = t.headerBg;
    ctx.fillRect(0, HEADER_H, RH, H - HEADER_H);
    ctx.textAlign = "right";
    const drawRow = (r: number, fy: boolean) => {
      const y = this.yOf(r * this.rowH, fy);
      if (fullRows.has(r)) {
        ctx.fillStyle = t.headerFull;
        ctx.fillRect(0, y, RH, this.rowH);
      } else if (selRows.has(r)) {
        ctx.fillStyle = t.headerSel;
        ctx.fillRect(0, y, RH, this.rowH);
      }
      const srow = this.sheetRow(r);
      const label = srow >= 0 ? String(srow + 1) : "";
      ctx.fillStyle = fullRows.has(r) ? t.headerFullText : selRows.has(r) ? t.headerSelText : t.headerText;
      ctx.fillText(label, RH - 9, y + this.rowH / 2 + 0.5);
      ctx.fillStyle = t.grid;
      ctx.fillRect(6, Math.round(y + this.rowH) - 1, RH - 6, 1);
    };
    ctx.save();
    ctx.beginPath();
    ctx.rect(0, HEADER_H + this.frozenH, RH, H - HEADER_H - this.frozenH);
    ctx.clip();
    for (let r = vr0; r <= vr1; r++) drawRow(r, false);
    ctx.restore();
    for (let r = 0; r < this.fr; r++) drawRow(r, true);
    ctx.fillStyle = t.gridStrong;
    ctx.fillRect(RH - 1, 0, 1, H);
    // corner
    ctx.fillStyle = t.headerBg;
    ctx.fillRect(0, 0, RH - 1, HEADER_H - 1);
    ctx.fillStyle = t.muted;
    ctx.beginPath();
    ctx.moveTo(RH - 6, HEADER_H - 6);
    ctx.lineTo(RH - 6, HEADER_H - 14);
    ctx.lineTo(RH - 14, HEADER_H - 6);
    ctx.closePath();
    ctx.globalAlpha = 0.5;
    ctx.fill();
    ctx.globalAlpha = 1;
    ctx.restore();
  }

  private roundRect(x: number, y: number, w: number, h: number, r: number) {
    const ctx = this.ctx;
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.arcTo(x + w, y, x + w, y + h, r);
    ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r);
    ctx.arcTo(x, y, x + w, y, r);
    ctx.closePath();
  }

  private drawFilterButton(x: number, y: number, active: boolean) {
    const ctx = this.ctx;
    const t = this.theme;
    const s = this.rowH - 8;
    if (active) {
      ctx.fillStyle = t.accent;
      this.roundRect(x, y, 18, s, 4);
      ctx.fill();
    }
    ctx.strokeStyle = active ? "#fff" : t.muted;
    ctx.lineWidth = 1.4;
    ctx.beginPath();
    const cx = x + 9;
    const cy = y + s / 2;
    ctx.moveTo(cx - 5, cy - 4);
    ctx.lineTo(cx + 5, cy - 4);
    ctx.lineTo(cx + 1, cy + 1);
    ctx.lineTo(cx + 1, cy + 5);
    ctx.lineTo(cx - 1, cy + 4);
    ctx.lineTo(cx - 1, cy + 1);
    ctx.closePath();
    ctx.stroke();
  }

  private drawChevron(x: number, y: number) {
    const ctx = this.ctx;
    ctx.strokeStyle = this.theme.muted;
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.moveTo(x, y - 2);
    ctx.lineTo(x + 4, y + 2);
    ctx.lineTo(x + 8, y - 2);
    ctx.stroke();
  }

  private drawCheckbox(cx: number, cy: number, checked: boolean) {
    const ctx = this.ctx;
    const t = this.theme;
    const s = 14;
    this.roundRect(cx - s / 2, cy - s / 2, s, s, 3.5);
    if (checked) {
      ctx.fillStyle = t.checkbox;
      ctx.fill();
      ctx.strokeStyle = "#fff";
      ctx.lineWidth = 1.8;
      ctx.beginPath();
      ctx.moveTo(cx - 3.5, cy);
      ctx.lineTo(cx - 1, cy + 2.8);
      ctx.lineTo(cx + 3.8, cy - 2.8);
      ctx.stroke();
    } else {
      ctx.strokeStyle = t.muted;
      ctx.lineWidth = 1.3;
      ctx.stroke();
    }
  }

  /** Approximate text width of a column's visible values (for double-click autofit). */
  measureColumn(c: number): number {
    let max = this.measure(colLetters(c), FONT_HEADER) + 20;
    const col = this.sheet?.columns[c];
    for (const b of this.blocks.values()) {
      if (Math.floor(c / BLOCK_C) !== b.bc) continue;
      for (let i = 0; i < b.cells.length; i++) {
        const v = b.cells[i]?.[c - b.bc * BLOCK_C];
        if (v === null || v === undefined) continue;
        const r = b.br * BLOCK_R + i;
        const f = formatCell(v, r === 0 ? "str" : col?.type, r === 0 ? null : col?.fmt);
        const w = this.measure(f.text, r === 0 ? FONT_BOLD : FONT) + 2 * PAD + (r === 0 && this.sheet?.filter_on ? 20 : 0);
        if (w > max) max = w;
      }
    }
    return Math.min(Math.ceil(max + 4), 600);
  }
}

function isLight(color: string): boolean {
  let r: number, g: number, b: number;
  const m = color.match(/^rgba?\((\d+),\s*(\d+),\s*(\d+)/);
  if (m) [r, g, b] = [+m[1], +m[2], +m[3]];
  else if (color.startsWith("#")) [r, g, b] = hexToRgb(color);
  else return true;
  return 0.2126 * r + 0.7152 * g + 0.0722 * b > 150;
}

function hexToRgb(h: string): [number, number, number] {
  const s = h.replace("#", "");
  const n = parseInt(s.length === 3 ? s.split("").map((x) => x + x).join("") : s, 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

export function scaleAt(colors: string[], v: number): string {
  const x = Math.min(1, Math.max(0, isFinite(v) ? v : 0));
  const segs = colors.length - 1;
  const i = Math.min(segs - 1, Math.floor(x * segs));
  const f = x * segs - i;
  const a = hexToRgb(colors[i]);
  const b = hexToRgb(colors[i + 1]);
  const m = (k: number) => Math.round(a[k] + (b[k] - a[k]) * f);
  return `rgb(${m(0)},${m(1)},${m(2)})`;
}

export const _easeOut = easeOut;
