import { useEffect, useLayoutEffect, useRef, useState } from "react";
import * as A from "../actions";
import { rpc } from "../api";
import FormulaInput, { canInsertRef, formulaRefs } from "../components/FormulaInput";
import { colLetters, parseRef } from "../format";
import { cellMenu, colHeaderMenu, rowHeaderMenu } from "../menus";
import { call, currentSheet, rememberScroll, setSelection, useApp } from "../store";
import type { Rect, Selection, Stats } from "../types";
import { GridController, type GridTheme, type Hit } from "./GridController";
import { handleShortcut } from "../shortcuts";

function readTheme(): GridTheme {
  const cs = getComputedStyle(document.documentElement);
  const v = (n: string) => cs.getPropertyValue(n).trim();
  return {
    bg: v("--grid-bg"),
    grid: v("--grid-line"),
    gridStrong: v("--grid-line-strong"),
    text: v("--text"),
    muted: v("--text-muted"),
    headerBg: v("--header-bg"),
    headerText: v("--header-text"),
    headerSel: v("--header-sel"),
    headerSelText: v("--header-sel-text"),
    headerFull: v("--accent"),
    headerFullText: "#ffffff",
    row0Bg: v("--row0-bg"),
    row0Text: v("--row0-text"),
    accent: v("--accent"),
    selFill: v("--sel-fill"),
    error: v("--danger"),
    negative: v("--danger"),
    shimmer: v("--shimmer"),
    frozenLine: v("--frozen-line"),
    shadow: v("--frozen-shadow"),
    spill: v("--spill"),
    invalid: v("--danger"),
    warning: v("--warning"),
    refColors: [],
    checkbox: v("--accent"),
    findFill: v("--find-fill"),
  };
}

// pick session while editing a formula: where the last inserted reference sits
let pick: { start: number; end: number; sheet: string | null } | null = null;

export default function Grid() {
  const hostRef = useRef<HTMLDivElement>(null);
  const edRef = useRef<HTMLDivElement>(null);
  const ctrl = useRef<GridController | null>(null);
  const sheet = useApp((s) => s.info?.sheets.find((x) => x.id === s.sheetId) || s.info?.sheets[0] || null);
  const theme = useApp((s) => s.theme);
  const editor = useApp((s) => s.editor);
  const dataVersion = useApp((s) => s.dataVersion);
  const findRect = useRef<Rect | null>(null);
  const [tip, setTip] = useState<{ x: number; y: number; text: string } | null>(null);
  const [dd, setDd] = useState<{ x: number; y: number; w: number; r: number; c: number; options: string[] } | null>(null);
  const statsTimer = useRef(0);

  useEffect(() => {
    const host = hostRef.current!;
    const g = new GridController(
      {
        onSelect(sel: Selection, reason) {
          setSelection(sel);
          setDd(null);
          if (reason !== "drag") A.refreshCell();
          else window.setTimeout(A.refreshCell, 0);
          updateSpill(sel);
          window.clearTimeout(statsTimer.current);
          statsTimer.current = window.setTimeout(loadStats, reason === "drag" ? 160 : 60);
        },
        onEditStart(initial) {
          pick = null;
          A.startEdit(initial, "cell");
        },
        onEditCommit(move) {
          A.commitEdit(move ?? null);
          return true;
        },
        isEditing: () => !!useApp.getState().editor,
        canPickRef() {
          const ed = useApp.getState().editor;
          if (!ed || !ed.text.startsWith("=")) return false;
          const caret = ed.caret ?? ed.text.length;
          return (pick !== null && pick.end === caret) || canInsertRef(ed.text, caret);
        },
        onRefPick(rect, phase) {
          const ed = useApp.getState().editor;
          if (!ed) return;
          if (phase === "end") {
            focusEditor();
            return;
          }
          const sh = currentSheet();
          const g2 = ctrl.current!;
          const full = rect.r1 === 0 && rect.r2 >= g2.totalRows - 1;
          const fullRow = !full && rect.c1 === 0 && rect.c2 >= g2.totalCols - 1;
          let label: string;
          const r1 = A.sheetRow(rect.r1);
          const r2 = A.sheetRow(rect.r2);
          if (full) label = `${colLetters(rect.c1)}:${colLetters(rect.c2)}`;
          else if (fullRow) label = `${r1 + 1}:${r2 + 1}`;
          else if (rect.r1 === rect.r2 && rect.c1 === rect.c2) label = `${colLetters(rect.c1)}${r1 + 1}`;
          else label = `${colLetters(rect.c1)}${r1 + 1}:${colLetters(rect.c2)}${r2 + 1}`;
          const editingSheet = useApp.getState().editorSheet;
          if (editingSheet && sh && editingSheet !== sh.id) {
            const nm = sh.name;
            label = (/^[A-Za-z_]\w*$/.test(nm) ? nm : `'${nm.replace(/'/g, "''")}'`) + "!" + label;
          }
          const caret = ed.caret ?? ed.text.length;
          let start = caret;
          let end = caret;
          if (pick && (phase === "move" || pick.end === caret)) {
            start = pick.start;
            end = pick.end;
          }
          const text = ed.text.slice(0, start) + label + ed.text.slice(end);
          pick = { start, end: start + label.length, sheet: sh?.id ?? null };
          A.setEditorText(text, start + label.length);
        },
        onContextMenu(hit: Hit, x, y) {
          const items = hit.area === "colHeader" ? colHeaderMenu(hit.c) : hit.area === "rowHeader" ? rowHeaderMenu() : cellMenu(hit);
          useApp.setState({ menu: { x, y, items } });
        },
        onFilterButton(col, rect) {
          useApp.setState({ filterPopover: { col, x: rect.left, y: rect.bottom + 4 } });
        },
        async onDropdown(r, c, rect) {
          const sh = currentSheet();
          if (!sh) return;
          const col = sh.columns[c];
          const rule = sh.validations.find((v) => v.kind === "list" && col && v.cols.includes(col.id));
          if (!rule) return;
          const { result } = await rpc<{ values: string[] }>("dv.options", { sheet: sh.id, id: rule.id });
          const hr = hostRef.current!.getBoundingClientRect();
          setDd({ x: hr.left + rect.x, y: hr.top + rect.y + rect.h + 2, w: Math.max(rect.w, 160), r, c, options: result.values });
        },
        async onCheckbox(r, c, value) {
          const sh = currentSheet();
          if (!sh) return;
          await call("cell.set", { sheet: sh.id, row: A.sheetRow(r), col: c, value: value ? "TRUE" : "FALSE" });
          A.invalidate();
          A.refreshCell();
        },
        onFill: (src, dst) => A.fillRange(src, dst),
        onFillColumn: () => A.fillColumn(),
        onResizeColumn: (col, w) => A.resizeColumn(col, w),
        onAutofit: (col) => A.autofit(col),
        onHover(hit, x, y) {
          if (!hit || hit.area !== "cell") return setTip(null);
          const sh = currentSheet();
          const row = A.sheetRow(hit.r);
          const err = sh?.formulas.find((f) => f.error && f.row === row && f.col === hit.c);
          const fmt = ctrl.current?.flagsAt(hit.r, hit.c)?.find((f) => f[2] === "fmt");
          if (err) setTip({ x: x + 14, y: y + 16, text: err.error! });
          else if (fmt)
            setTip({
              x: x + 14,
              y: y + 16,
              text:
                fmt[3] === "strict"
                  ? "No exact match — but the value is there in a different format (text vs number, extra spaces, leading zeros, separators or hidden characters). Data → Check key matches shows where; switch Lookup matching to Smart or normalise the columns."
                  : "Matched despite a format difference (text vs number, extra spaces, leading zeros, separators or hidden characters). Data → Check key matches shows the details and can fix both columns.",
            });
          else setTip(null);
        },
        onView() {
          positionEditor();
          const sh = currentSheet();
          const g2 = ctrl.current;
          if (sh && g2) rememberScroll(sh.id, g2.tx, g2.ty);
          setDd(null);
        },
        onKey: (e) => handleShortcut(e, "grid"),
        onPaste: (text) => A.pasteText(text),
        onCopy: (cut) => A.copySelection(cut),
      },
      readTheme(),
    );
    g.attach(host);
    ctrl.current = g;
    A.setGrid(g);
    g.focus();
    return () => {
      g.destroy();
      A.setGrid(null);
    };
  }, []);

  useEffect(() => {
    const g = ctrl.current;
    if (!g || !sheet) return;
    const st = useApp.getState();
    const per = st.perSheet[sheet.id];
    const switching = g.sheet?.id !== sheet.id;
    g.setSheet(sheet, { scroll: per ? { x: per.scrollX, y: per.scrollY } : { x: 0, y: 0 } });
    if (switching) {
      g.sel = st.sel;
      g.anim.init = false;
      if (!st.editor) g.focus();
      A.refreshCell();
      loadStats();
    }
    // widths changed on the server: drop local overrides
    g.widthOverride.clear();
    g.computeGeometry();
    updateSpill(st.sel);
  }, [sheet]);

  useEffect(() => {
    ctrl.current?.invalidateData();
    A.refreshCell();
    window.clearTimeout(statsTimer.current);
    statsTimer.current = window.setTimeout(loadStats, 120);
  }, [dataVersion]);

  useEffect(() => {
    const id = requestAnimationFrame(() => ctrl.current?.setTheme(readTheme()));
    return () => cancelAnimationFrame(id);
  }, [theme]);

  // reference highlights while editing a formula
  useEffect(() => {
    const g = ctrl.current;
    if (!g) return;
    if (!editor || !editor.text.startsWith("=")) {
      g.refHighlights = [];
      g.invalidateDraw();
      if (!editor) pick = null;
      return;
    }
    const sh = currentSheet();
    const hl = [];
    for (const { ref, color } of formulaRefs(editor.text)) {
      const p = parseRef(ref);
      if (!p) continue;
      if (p.sheet && sh && p.sheet.toLowerCase() !== sh.name.toLowerCase()) continue;
      hl.push({ r1: p.r1, c1: p.c1, r2: Math.min(p.r2, g.totalRows - 1), c2: Math.min(p.c2, g.totalCols - 1), color });
    }
    g.refHighlights = hl;
    g.invalidateDraw();
  }, [editor?.text, editor, sheet]);

  // find highlight
  useEffect(
    () =>
      useApp.subscribe((s, prev) => {
        if (s.findOpen !== prev.findOpen && !s.findOpen && ctrl.current) {
          ctrl.current.findRect = null;
          ctrl.current.invalidateDraw();
        }
        if (s.clip !== prev.clip && !s.clip && ctrl.current) {
          ctrl.current.copyRect = null;
          ctrl.current.invalidateDraw();
        }
      }),
    [],
  );
  void findRect;

  useLayoutEffect(() => positionEditor());

  function positionEditor() {
    const el = edRef.current;
    const g = ctrl.current;
    const ed = useApp.getState().editor;
    if (!el || !g || !ed) return;
    const r = g.cellRect(ed.r, ed.c);
    el.style.transform = `translate(${r.x}px, ${r.y}px)`;
    el.style.minWidth = `${r.w + 1}px`;
    el.style.minHeight = `${r.h + 1}px`;
    el.style.maxWidth = `${Math.max(r.w, g.width - r.x - 12)}px`;
    el.style.visibility = g.isCellVisible(ed.r, ed.c) || ed.origin === "bar" ? "visible" : "hidden";
  }

  function focusEditor() {
    const ta = edRef.current?.querySelector("textarea");
    ta?.focus();
  }

  function updateSpill(sel: Selection) {
    const g = ctrl.current;
    const sh = currentSheet();
    if (!g || !sh) return;
    const row = A.sheetRow(sel.active.r);
    const c = sel.active.c;
    let rect: Rect | null = null;
    for (const f of sh.formulas) {
      if (f.kind === "cell" && (f.h > 1 || f.w > 1) && row >= f.row && row < f.row + f.h && c >= f.col && c < f.col + f.w) {
        rect = { r1: f.row, c1: f.col, r2: f.row + f.h - 1, c2: f.col + f.w - 1 };
      } else if (f.kind === "column" && c === f.col && row >= f.row && row <= (f.row_end ?? sh.nrows)) {
        rect = { r1: f.row, c1: f.col, r2: f.row_end ?? sh.nrows, c2: f.col };
      }
    }
    g.spillRect = sh.filtered ? null : rect;
    g.invalidateDraw();
  }

  async function loadStats() {
    const sh = currentSheet();
    const st = useApp.getState();
    if (!sh) return;
    const rects = A.selectionRects();
    const single = rects.length === 1 && rects[0].r1 === rects[0].r2 && rects[0].c1 === rects[0].c2;
    if (single) {
      useApp.setState({ stats: null });
      return;
    }
    try {
      const { result } = await rpc<Stats>("stats", { sheet: sh.id, ...A.rectsParam() });
      if (useApp.getState().sel === st.sel) useApp.setState({ stats: result });
    } catch {
      useApp.setState({ stats: null });
    }
  }

  const onEditorKey = (e: React.KeyboardEvent<HTMLTextAreaElement>, acOpen: boolean) => {
    if (acOpen) return;
    const ed = useApp.getState().editor;
    if (!ed) return;
    const mod = e.ctrlKey || e.metaKey;
    if (e.key === "Enter" && e.altKey) {
      e.preventDefault();
      const ta = e.currentTarget;
      const pos = ta.selectionStart;
      A.setEditorText(ed.text.slice(0, pos) + "\n" + ed.text.slice(ta.selectionEnd), pos + 1);
      return;
    }
    if (e.key === "Enter") {
      e.preventDefault();
      if (mod && ed.text.startsWith("=")) A.commitEdit("down", "column");
      else A.commitEdit(e.shiftKey ? "up" : "down");
      return;
    }
    if (e.key === "Tab") {
      e.preventDefault();
      A.commitEdit(e.shiftKey ? "left" : "right");
      return;
    }
    if (e.key === "Escape") {
      e.preventDefault();
      A.cancelEdit();
      return;
    }
    if (ed.replace && !ed.text.startsWith("=") && ["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight"].includes(e.key)) {
      e.preventDefault();
      A.commitEdit(e.key === "ArrowUp" ? "up" : e.key === "ArrowDown" ? "down" : e.key === "ArrowLeft" ? "left" : "right");
      return;
    }
    if (e.key.length === 1 || e.key === "Backspace") pick = null;
  };

  return (
    <div className="grid-host" ref={hostRef}>
      <canvas />
      <textarea className="grid-input" aria-label="Spreadsheet grid" autoCapitalize="off" autoComplete="off" spellCheck={false} />
      <div className="sb sb-v">
        <div className="sb-thumb" />
      </div>
      <div className="sb sb-h">
        <div className="sb-thumb" />
      </div>
      {editor && (
        <div className="cell-editor" ref={edRef}>
          <FormulaInput
            value={editor.text}
            caret={editor.caret}
            autoFocus={editor.origin === "cell"}
            onChange={(t, c) => A.setEditorText(t, c)}
            onKeyDown={onEditorKey}
          />
        </div>
      )}
      {tip && (
        <div className="cell-tip" style={{ left: tip.x, top: tip.y }}>
          {tip.text}
        </div>
      )}
      {dd && (
        <div className="dv-dropdown pop-in" style={{ left: dd.x - (hostRef.current?.getBoundingClientRect().left ?? 0), top: dd.y - (hostRef.current?.getBoundingClientRect().top ?? 0), minWidth: dd.w }}>
          {dd.options.length === 0 && <div className="dv-empty">No options</div>}
          {dd.options.map((o) => (
            <button
              key={o}
              className="dv-opt"
              onClick={async () => {
                const sh = currentSheet();
                setDd(null);
                if (!sh) return;
                await call("cell.set", { sheet: sh.id, row: A.sheetRow(dd.r), col: dd.c, value: o });
                A.invalidate();
                A.refreshCell();
                ctrl.current?.focus();
              }}
            >
              {o}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
