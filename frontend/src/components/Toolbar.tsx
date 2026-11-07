import {
  AlignCenter,
  AlignLeft,
  AlignRight,
  ArrowDownAZ,
  ArrowDownZA,
  Baseline,
  Bold,
  ChevronDown,
  CopyCheck,
  Download,
  Filter,
  Italic,
  Paintbrush,
  Palette,
  Redo2,
  Search,
  ShieldCheck,
  Sigma,
  Snowflake,
  Strikethrough,
  Table2,
  TableCellsMerge,
  Grid2x2,
  RefreshCw,
  SquareTerminal,
  Underline,
  Undo2,
  Upload,
} from "lucide-react";
import { useState } from "react";
import * as A from "../actions";
import { exportDialog, openFile } from "../fileops";
import { NUMBER_FORMATS } from "../format";
import { currentSheet, openPanel, useApp, type MenuItem } from "../store";
import { MOD } from "../shortcuts";
import { FloatingMenu } from "./Menu";

const SWATCHES = [
  ["#000000", "#434343", "#666666", "#999999", "#b7b7b7", "#d9d9d9", "#efefef", "#ffffff"],
  ["#dc2626", "#ea580c", "#d97706", "#ca8a04", "#16a34a", "#0891b2", "#2563eb", "#7c3aed"],
  ["#fee2e2", "#ffedd5", "#fef3c7", "#fef9c3", "#dcfce7", "#cffafe", "#dbeafe", "#ede9fe"],
  ["#fca5a5", "#fdba74", "#fcd34d", "#fde047", "#86efac", "#67e8f9", "#93c5fd", "#c4b5fd"],
  ["#991b1b", "#9a3412", "#92400e", "#854d0e", "#166534", "#155e75", "#1e40af", "#5b21b6"],
];

function Btn(props: { tip: string; onClick: () => void; active?: boolean; disabled?: boolean; children: React.ReactNode; wide?: boolean }) {
  return (
    <button
      className={`tb-btn ${props.active ? "on" : ""} ${props.wide ? "wide" : ""}`}
      data-tip={props.tip}
      disabled={props.disabled}
      onMouseDown={(e) => e.preventDefault()}
      onClick={props.onClick}
    >
      {props.children}
    </button>
  );
}

const BORDER_MODES: { mode: string; tip: string; d: string }[] = [
  { mode: "all", tip: "All borders", d: "M3 3h14v14H3zM10 3v14M3 10h14" },
  { mode: "inner", tip: "Inner borders", d: "M10 3v14M3 10h14" },
  { mode: "horizontal", tip: "Horizontal borders", d: "M3 10h14" },
  { mode: "vertical", tip: "Vertical borders", d: "M10 3v14" },
  { mode: "outer", tip: "Outer borders", d: "M3 3h14v14H3z" },
  { mode: "left", tip: "Left border", d: "M3 3v14" },
  { mode: "top", tip: "Top border", d: "M3 3h14" },
  { mode: "right", tip: "Right border", d: "M17 3v14" },
  { mode: "bottom", tip: "Bottom border", d: "M3 17h14" },
  { mode: "none", tip: "Clear borders", d: "" },
];

const LINES: { id: string; label: string; dash?: string; w: number }[] = [
  { id: "thin", label: "Thin", w: 1 },
  { id: "medium", label: "Medium", w: 2 },
  { id: "thick", label: "Thick", w: 3 },
  { id: "dashed", label: "Dashed", dash: "4 3", w: 1.2 },
  { id: "dotted", label: "Dotted", dash: "1 2", w: 1.4 },
  { id: "double", label: "Double", w: 1 },
];

function BorderPicker({ x, y, onClose }: { x: number; y: number; onClose: () => void }) {
  const [line, setLine] = useState("thin");
  const [color, setColor] = useState("#1f2937");
  return (
    <div className="border-pop pop-in" style={{ left: x, top: y }} onMouseDown={(e) => e.preventDefault()}>
      <div className="border-grid">
        {BORDER_MODES.map((b) => (
          <button key={b.mode} className="border-btn" data-tip={b.tip} onClick={() => (A.applyBorders(b.mode, line, color), onClose())}>
            <svg viewBox="0 0 20 20" width="20" height="20">
              <path d="M3 3h14v14H3zM10 3v14M3 10h14" fill="none" stroke="currentColor" strokeOpacity=".25" strokeDasharray="1.5 1.5" />
              {b.d ? <path d={b.d} fill="none" stroke="currentColor" strokeWidth="1.8" /> : <path d="M5 5l10 10M15 5L5 15" stroke="currentColor" strokeWidth="1.6" />}
            </svg>
          </button>
        ))}
      </div>
      <div className="border-lines">
        {LINES.map((l) => (
          <button key={l.id} className={`border-line ${line === l.id ? "on" : ""}`} onClick={() => setLine(l.id)} title={l.label}>
            <svg viewBox="0 0 40 10" width="40" height="10">
              {l.id === "double" ? (
                <path d="M2 3.5h36M2 6.5h36" stroke="currentColor" strokeWidth="1" />
              ) : (
                <path d="M2 5h36" stroke="currentColor" strokeWidth={l.w} strokeDasharray={l.dash} />
              )}
            </svg>
          </button>
        ))}
      </div>
      <div className="color-row">
        {["#1f2937", "#6b7280", "#d1d5db", "#dc2626", "#d97706", "#16a34a", "#2563eb", "#7c3aed"].map((c) => (
          <button key={c} className={`swatch ${color === c ? "on" : ""}`} style={{ background: c }} onClick={() => setColor(c)} />
        ))}
      </div>
    </div>
  );
}

function ColorPicker({ x, y, onPick, onClose, title }: { x: number; y: number; onPick: (c: string | null) => void; onClose: () => void; title: string }) {
  return (
    <div className="color-pop pop-in" style={{ left: x, top: y }} onMouseDown={(e) => e.preventDefault()}>
      <div className="color-title">{title}</div>
      <button className="color-reset" onClick={() => (onPick(null), onClose())}>
        Reset
      </button>
      {SWATCHES.map((row, i) => (
        <div className="color-row" key={i}>
          {row.map((c) => (
            <button key={c} className="swatch" style={{ background: c }} title={c} onClick={() => (onPick(c), onClose())} />
          ))}
        </div>
      ))}
    </div>
  );
}

export default function Toolbar() {
  const info = useApp((s) => s.info);
  const busy = useApp((s) => s.busy > 0);
  useApp((s) => s.sel);
  useApp((s) => s.info?.version);
  const sheet = currentSheet();
  const [menu, setMenu] = useState<{ x: number; y: number; items: MenuItem[] } | null>(null);
  const [color, setColor] = useState<{ x: number; y: number; key: "fg" | "bg" } | null>(null);
  const [borders, setBorders] = useState<{ x: number; y: number } | null>(null);
  const st = A.currentStyle();
  const fmtLabel = NUMBER_FORMATS.find((f) => f.fmt === (st.fmt ?? null))?.label ?? (st.fmt ? "Custom" : "Automatic");

  const openMenu = (e: React.MouseEvent, items: MenuItem[]) => {
    const r = (e.currentTarget as HTMLElement).getBoundingClientRect();
    setMenu({ x: r.left, y: r.bottom + 4, items });
  };
  const openColor = (e: React.MouseEvent, key: "fg" | "bg") => {
    const r = (e.currentTarget as HTMLElement).getBoundingClientRect();
    setColor({ x: r.left, y: r.bottom + 4, key });
  };

  return (
    <div className="toolbar">
      <Btn tip={`Undo (${MOD}+Z)`} onClick={A.undo} disabled={!info?.can_undo}>
        <Undo2 size={17} />
      </Btn>
      <Btn tip={`Redo (${MOD}+Y)`} onClick={A.redo} disabled={!info?.can_redo}>
        <Redo2 size={17} />
      </Btn>
      <div className="tb-sep" />
      <button
        className="tb-select"
        data-tip="Number format"
        onMouseDown={(e) => e.preventDefault()}
        onClick={(e) =>
          openMenu(
            e,
            NUMBER_FORMATS.map((f) => ({ label: f.label, shortcut: f.sample, checked: (st.fmt ?? null) === f.fmt, run: () => A.setNumberFormat(f.fmt) })),
          )
        }
      >
        <span>{fmtLabel}</span>
        <ChevronDown size={14} />
      </button>
      <Btn tip="Fewer decimals" onClick={() => A.changeDecimals(-1)}>
        <span className="tb-text">.0←</span>
      </Btn>
      <Btn tip="More decimals" onClick={() => A.changeDecimals(1)}>
        <span className="tb-text">.00→</span>
      </Btn>
      <div className="tb-sep" />
      <Btn tip={`Bold (${MOD}+B)`} onClick={() => A.toggleStyle("bold")} active={!!st.bold}>
        <Bold size={17} />
      </Btn>
      <Btn tip={`Italic (${MOD}+I)`} onClick={() => A.toggleStyle("italic")} active={!!st.italic}>
        <Italic size={17} />
      </Btn>
      <Btn tip={`Underline (${MOD}+U)`} onClick={() => A.toggleStyle("underline")} active={!!st.underline}>
        <Underline size={17} />
      </Btn>
      <Btn tip="Strikethrough" onClick={() => A.toggleStyle("strike")} active={!!st.strike}>
        <Strikethrough size={17} />
      </Btn>
      <Btn tip="Text color" onClick={() => undefined}>
        <span className="tb-color" onClick={(e) => openColor(e, "fg")}>
          <Baseline size={17} />
          <i style={{ background: st.fg || "var(--text)" }} />
        </span>
      </Btn>
      <Btn tip="Fill color" onClick={() => undefined}>
        <span className="tb-color" onClick={(e) => openColor(e, "bg")}>
          <Paintbrush size={16} />
          <i style={{ background: st.bg || "transparent", boxShadow: st.bg ? "none" : "inset 0 0 0 1px var(--border)" }} />
        </span>
      </Btn>
      <div className="tb-sep" />
      <Btn tip="Borders" onClick={() => undefined}>
        <span
          className="tb-inline"
          onClick={(e) => {
            const r = (e.currentTarget as HTMLElement).getBoundingClientRect();
            setBorders({ x: r.left, y: r.bottom + 4 });
          }}
        >
          <Grid2x2 size={17} />
        </span>
      </Btn>
      <Btn tip="Merge cells" onClick={() => undefined}>
        <span
          className="tb-inline"
          onClick={(e) =>
            openMenu(e, [
              { label: "Merge all", run: () => A.mergeCells("all") },
              { label: "Merge horizontally", run: () => A.mergeCells("horizontal") },
              { label: "Merge vertically", run: () => A.mergeCells("vertical") },
              { divider: true },
              { label: "Unmerge", run: () => A.mergeCells("unmerge") },
            ])
          }
        >
          <TableCellsMerge size={17} />
        </span>
      </Btn>
      <div className="tb-sep" />
      <Btn tip="Align left" onClick={() => A.applyStyle({ align: "left" })} active={st.align === "left"}>
        <AlignLeft size={17} />
      </Btn>
      <Btn tip="Align center" onClick={() => A.applyStyle({ align: "center" })} active={st.align === "center"}>
        <AlignCenter size={17} />
      </Btn>
      <Btn tip="Align right" onClick={() => A.applyStyle({ align: "right" })} active={st.align === "right"}>
        <AlignRight size={17} />
      </Btn>
      <div className="tb-sep" />
      <Btn
        tip="Freeze rows / columns"
        active={!!sheet && (sheet.frozen_rows > 0 || sheet.frozen_cols > 0)}
        onClick={() => undefined}
      >
        <span
          className="tb-inline"
          onClick={(e) =>
            openMenu(e, [
              { label: "No rows", checked: sheet?.frozen_rows === 0, run: () => A.freezeAt(0, null) },
              { label: "Header row", checked: sheet?.frozen_rows === 1, run: () => A.freezeAt(1, null) },
              { label: "Up to current row", run: () => A.freezeAt(useApp.getState().sel.active.r + 1, null) },
              { divider: true },
              { label: "No columns", checked: sheet?.frozen_cols === 0, run: () => A.freezeAt(null, 0) },
              { label: "First column", checked: sheet?.frozen_cols === 1, run: () => A.freezeAt(null, 1) },
              { label: "Up to current column", run: () => A.freezeAt(null, useApp.getState().sel.active.c + 1) },
            ])
          }
        >
          <Snowflake size={17} />
        </span>
      </Btn>
      <Btn tip={`Filter (${MOD}+Shift+L)`} onClick={A.toggleFilter} active={!!sheet?.filter_on}>
        <Filter size={17} />
      </Btn>
      <Btn tip="Sort A → Z by this column" onClick={() => A.sortBy(useApp.getState().sel.active.c, true)}>
        <ArrowDownAZ size={17} />
      </Btn>
      <Btn tip="Sort Z → A by this column" onClick={() => A.sortBy(useApp.getState().sel.active.c, false)}>
        <ArrowDownZA size={17} />
      </Btn>
      <Btn tip="Functions" onClick={() => undefined}>
        <span
          className="tb-inline"
          onClick={(e) =>
            openMenu(
              e,
              ["SUM", "AVERAGE", "COUNT", "MAX", "MIN", "COUNTIF", "SUMIFS", "VLOOKUP", "XLOOKUP", "INDEX", "MATCH", "FILTER", "QUERY", "UNIQUE", "SORT", "ARRAYFORMULA", "IFERROR", "TRIM"].map((f) => ({
                label: f,
                run: () => A.startEdit(`=${f}(`),
              })),
            )
          }
        >
          <Sigma size={17} />
        </span>
      </Btn>
      <div className="tb-sep" />
      <Btn tip="Pivot table" onClick={A.openPivot} wide>
        <Table2 size={16} />
        <span>Pivot</span>
      </Btn>
      <Btn tip="Conditional formatting" onClick={() => openPanel("cf")}>
        <Palette size={17} />
      </Btn>
      <Btn tip="Data validation" onClick={() => openPanel("dv")}>
        <ShieldCheck size={17} />
      </Btn>
      <Btn tip="Duplicates" onClick={() => openPanel("dups", { cols: [useApp.getState().sel.active.c] })}>
        <CopyCheck size={17} />
      </Btn>
      <Btn tip={`Find and replace (${MOD}+F)`} onClick={() => A.openFind("find")}>
        <Search size={17} />
      </Btn>
      <Btn tip="Python scripts" onClick={() => useApp.setState({ scriptsOpen: true })}>
        <SquareTerminal size={17} />
      </Btn>
      <div className="tb-flex" />
      {sheet?.link && (
        <button
          className="btn ghost sm refresh-btn"
          data-tip={`Linked to Google Sheets${sheet.link.refreshed_at ? ` · refreshed ${sheet.link.refreshed_at.replace("T", " ")}` : ""}`}
          onMouseDown={(e) => e.preventDefault()}
          onClick={() => A.refreshLinked()}
        >
          <RefreshCw size={15} className={busy ? "spin" : ""} /> Refresh
        </button>
      )}
      <button className="btn ghost sm" onMouseDown={(e) => e.preventDefault()} onClick={openFile}>
        <Upload size={15} /> Import
      </button>
      <button className="btn primary sm" onMouseDown={(e) => e.preventDefault()} onClick={exportDialog}>
        <Download size={15} /> Export
      </button>
      {menu && <FloatingMenu items={menu.items} x={menu.x} y={menu.y} onClose={() => setMenu(null)} />}
      {borders && (
        <>
          <div className="click-catcher" onMouseDown={() => setBorders(null)} />
          <BorderPicker x={borders.x} y={borders.y} onClose={() => setBorders(null)} />
        </>
      )}
      {color && (
        <>
          <div className="click-catcher" onMouseDown={() => setColor(null)} />
          <ColorPicker
            x={color.x}
            y={color.y}
            title={color.key === "fg" ? "Text color" : "Fill color"}
            onPick={(c) => A.applyStyle({ [color.key]: c })}
            onClose={() => setColor(null)}
          />
        </>
      )}
    </div>
  );
}
