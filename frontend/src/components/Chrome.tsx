import { Cloud, Command, Moon, Plus, Sun, X, ChevronDown, Sheet as SheetIcon, AlertTriangle, CheckCircle2, Info as InfoIcon, Loader2, FunctionSquare } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import * as A from "../actions";
import { autoNumber } from "../format";
import { TOP_MENUS } from "../menus";
import { call, currentSheet, dismissToast, selectSheet, setTheme, toast, useApp, type MenuItem } from "../store";
import { progress } from "../api";
import FormulaInput from "./FormulaInput";
import { FloatingMenu } from "./Menu";

// ------------------------------------------------------------------ top bar

export function TopBar() {
  const info = useApp((s) => s.info);
  const theme = useApp((s) => s.theme);
  const [open, setOpen] = useState<{ i: number; x: number; y: number } | null>(null);
  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState("");

  return (
    <header className="topbar">
      <div className="brand" title="PySheets">
        <svg viewBox="0 0 32 32" width="28" height="28" aria-hidden>
          <defs>
            <linearGradient id="lg" x1="0" y1="0" x2="1" y2="1">
              <stop offset="0" stopColor="var(--accent)" />
              <stop offset="1" stopColor="var(--accent-2)" />
            </linearGradient>
          </defs>
          <rect width="32" height="32" rx="7.5" fill="url(#lg)" />
          <rect x="6.5" y="7.5" width="19" height="17" rx="2.4" fill="none" stroke="#fff" strokeWidth="1.6" />
          <path d="M6.5 9.9a2.4 2.4 0 0 1 2.4-2.4h14.2a2.4 2.4 0 0 1 2.4 2.4v3.4h-19z" fill="#fff" />
          <path d="M6.5 19h19M14.1 13.3v11.2" stroke="#fff" strokeWidth="1.6" />
        </svg>
      </div>
      <div className="title-col">
        {editing ? (
          <input
            className="title-input"
            autoFocus
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            onBlur={() => {
              setEditing(false);
              if (title.trim() && title !== info?.title) call("workbook.title", { title });
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter") (e.target as HTMLInputElement).blur();
              if (e.key === "Escape") setEditing(false);
            }}
          />
        ) : (
          <button
            className="title"
            onClick={() => {
              setTitle(info?.title || "");
              setEditing(true);
            }}
          >
            {info?.title || "Untitled spreadsheet"}
            {info?.dirty && <span className="dirty-dot" title="Unsaved changes" />}
          </button>
        )}
        <nav className="menubar">
          {TOP_MENUS.map((m, i) => (
            <button
              key={m.label}
              className={`menubar-item ${open?.i === i ? "on" : ""}`}
              onMouseDown={(e) => e.preventDefault()}
              onClick={(e) => {
                const r = (e.currentTarget as HTMLElement).getBoundingClientRect();
                setOpen(open?.i === i ? null : { i, x: r.left, y: r.bottom + 4 });
              }}
              onMouseEnter={(e) => {
                if (open && open.i !== i) {
                  const r = (e.currentTarget as HTMLElement).getBoundingClientRect();
                  setOpen({ i, x: r.left, y: r.bottom + 4 });
                }
              }}
            >
              {m.label}
            </button>
          ))}
        </nav>
      </div>
      <div className="topbar-right">
        <button className="cmdk" onClick={() => useApp.setState({ palette: true })}>
          <Command size={14} />
          <span>Search commands</span>
          <kbd>Ctrl K</kbd>
        </button>
        <button className="icon-btn" data-tip={theme === "dark" ? "Light theme" : "Dark theme"} onClick={() => setTheme(theme === "dark" ? "light" : "dark")}>
          {theme === "dark" ? <Sun size={17} /> : <Moon size={17} />}
        </button>
      </div>
      {open && <FloatingMenu key={open.i} items={TOP_MENUS[open.i].items()} x={open.x} y={open.y} onClose={() => setOpen(null)} />}
    </header>
  );
}

// -------------------------------------------------------------- formula bar

export function FormulaBar() {
  const sel = useApp((s) => s.sel);
  const cell = useApp((s) => s.cell);
  const editor = useApp((s) => s.editor);
  const [nameBox, setNameBox] = useState<string | null>(null);
  const label = A.describeSelection(sel);
  const value = editor ? editor.text : cell?.input ?? "";
  const colF = cell?.formula?.kind === "column" ? cell.formula : null;
  const spill = cell?.spill_of;
  const err = cell?.formula?.error;

  return (
    <div className="formula-bar">
      <input
        className="name-box"
        value={nameBox ?? label}
        onFocus={(e) => {
          setNameBox(label);
          e.target.select();
        }}
        onChange={(e) => setNameBox(e.target.value)}
        onBlur={() => setNameBox(null)}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            if (nameBox && A.gotoRef(nameBox)) {
              setNameBox(null);
              (e.target as HTMLInputElement).blur();
              A.getGrid()?.focus();
            } else toast(`“${nameBox}” is not a valid cell or range`, "error");
          }
          if (e.key === "Escape") {
            setNameBox(null);
            A.getGrid()?.focus();
          }
        }}
        spellCheck={false}
      />
      <div className="fx">
        <FunctionSquare size={16} />
      </div>
      <div className={`fbar-input ${editor?.origin === "bar" ? "focused" : ""}`}>
        <FormulaInput
          value={spill && !editor ? spill.text : value}
          caret={editor?.caret}
          placeholder={spill ? "" : ""}
          className={spill && !editor ? "ghost" : ""}
          onFocus={() => {
            if (!useApp.getState().editor) A.startEdit(null, "bar");
            else useApp.setState({ editor: { ...useApp.getState().editor!, origin: "bar" } });
          }}
          onChange={(t, c) => {
            if (!useApp.getState().editor) A.startEdit(t, "bar");
            A.setEditorText(t, c);
          }}
          onKeyDown={(e, acOpen) => {
            if (acOpen) return;
            if (e.key === "Enter" && !e.altKey) {
              e.preventDefault();
              A.commitEdit("down", (e.ctrlKey || e.metaKey) && value.startsWith("=") ? "column" : undefined);
            } else if (e.key === "Escape") {
              e.preventDefault();
              A.cancelEdit();
            } else if (e.key === "Tab") {
              e.preventDefault();
              A.commitEdit("right");
            }
          }}
        />
      </div>
      {(colF || spill || err) && (
        <div className={`fbar-badge ${err ? "err" : ""}`} title={err || ""}>
          {err ? (
            <>
              <AlertTriangle size={13} /> {err.split(" ")[0]}
            </>
          ) : colF ? (
            <>ƒ column formula · rows {(colF.row ?? 0) + 1}–{((colF.row_end ?? 0) + 1).toLocaleString()}</>
          ) : (
            <>spilled from {spill!.anchor}</>
          )}
        </div>
      )}
    </div>
  );
}

// --------------------------------------------------------------- sheet tabs

export function SheetTabs() {
  const info = useApp((s) => s.info);
  const sheetId = useApp((s) => s.sheetId);
  const [renaming, setRenaming] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [menu, setMenu] = useState<{ x: number; y: number; items: MenuItem[] } | null>(null);
  const [drag, setDrag] = useState<{ id: string; over: number } | null>(null);
  const tabsRef = useRef<HTMLDivElement>(null);
  const [indicator, setIndicator] = useState({ left: 0, width: 0 });

  useEffect(() => {
    const el = tabsRef.current?.querySelector(`[data-id="${sheetId}"]`) as HTMLElement | null;
    if (el) {
      setIndicator({ left: el.offsetLeft, width: el.offsetWidth });
      el.scrollIntoView({ block: "nearest", inline: "nearest", behavior: "smooth" });
    }
  }, [sheetId, info]);

  const sheets = info?.sheets || [];
  const tabMenu = (id: string, x: number, y: number) => {
    const s = sheets.find((t) => t.id === id)!;
    setMenu({
      x,
      y,
      items: [
        { label: "Rename", icon: "type", run: () => (setName(s.name), setRenaming(id)) },
        { label: "Duplicate", icon: "copy", run: () => call("sheet.duplicate", { sheet: id }) },
        { label: "Export this tab…", icon: "download", run: () => (selectSheet(id), useApp.setState({ dialog: "export", dialogProps: { scope: "sheet" } })) },
        { divider: true },
        { label: "Move left", icon: "chevron-right", disabled: sheets[0]?.id === id, run: () => call("sheet.move", { sheet: id, index: sheets.findIndex((t) => t.id === id) - 1 }) },
        { label: "Move right", icon: "chevron-right", disabled: sheets[sheets.length - 1]?.id === id, run: () => call("sheet.move", { sheet: id, index: sheets.findIndex((t) => t.id === id) + 1 }) },
        { divider: true },
        { label: "Delete", icon: "trash", danger: true, disabled: sheets.length < 2, run: () => window.confirm(`Delete "${s.name}"?`) && call("sheet.delete", { sheet: id }) },
      ],
    });
  };

  return (
    <div className="tabs-bar">
      <button className="icon-btn" data-tip="Add sheet" onClick={async () => {
        const r = await call<{ sheet: string }>("sheet.add", {});
        if (r) selectSheet(r.sheet);
      }}>
        <Plus size={17} />
      </button>
      <button
        className="icon-btn"
        data-tip="All sheets"
        onClick={(e) => {
          const r = (e.currentTarget as HTMLElement).getBoundingClientRect();
          setMenu({ x: r.left, y: r.top - 8 - Math.min(sheets.length, 12) * 34, items: sheets.map((s) => ({ label: s.name, checked: s.id === sheetId, run: () => selectSheet(s.id) })) });
        }}
      >
        <ChevronDown size={17} />
      </button>
      <div className="tabs" ref={tabsRef}>
        <div className="tab-indicator" style={{ transform: `translateX(${indicator.left}px)`, width: indicator.width }} />
        {sheets.map((s, i) => (
          <div
            key={s.id}
            data-id={s.id}
            className={`tab ${s.id === sheetId ? "on" : ""} ${drag && drag.over === i && drag.id !== s.id ? "drop" : ""}`}
            draggable={renaming !== s.id}
            onDragStart={(e) => {
              e.dataTransfer.effectAllowed = "move";
              setDrag({ id: s.id, over: i });
            }}
            onDragOver={(e) => {
              e.preventDefault();
              if (drag && drag.over !== i) setDrag({ ...drag, over: i });
            }}
            onDragEnd={() => setDrag(null)}
            onDrop={(e) => {
              e.preventDefault();
              if (drag && drag.id !== s.id) call("sheet.move", { sheet: drag.id, index: i });
              setDrag(null);
            }}
            onClick={() => selectSheet(s.id)}
            onDoubleClick={() => (setName(s.name), setRenaming(s.id))}
            onContextMenu={(e) => {
              e.preventDefault();
              tabMenu(s.id, e.clientX, e.clientY - 250);
            }}
          >
            {s.pivot ? <span className="tab-badge">⊞</span> : s.link ? <Cloud size={13} className="tab-icon linked" /> : <SheetIcon size={13} className="tab-icon" />}
            {renaming === s.id ? (
              <input
                className="tab-input"
                autoFocus
                value={name}
                onChange={(e) => setName(e.target.value)}
                onFocus={(e) => e.target.select()}
                onBlur={() => {
                  setRenaming(null);
                  if (name.trim() && name !== s.name) call("sheet.rename", { sheet: s.id, name });
                }}
                onKeyDown={(e) => {
                  if (e.key === "Enter") (e.target as HTMLInputElement).blur();
                  if (e.key === "Escape") setRenaming(null);
                }}
              />
            ) : (
              <span className="tab-name">{s.name}</span>
            )}
            <button
              className="tab-menu"
              onClick={(e) => {
                e.stopPropagation();
                const r = (e.currentTarget as HTMLElement).getBoundingClientRect();
                tabMenu(s.id, r.left, r.top - 250);
              }}
            >
              <ChevronDown size={13} />
            </button>
          </div>
        ))}
      </div>
      {menu && <FloatingMenu items={menu.items} x={menu.x} y={Math.max(8, menu.y)} onClose={() => setMenu(null)} />}
      <StatusBar />
    </div>
  );
}

// --------------------------------------------------------------- status bar

function fmtStat(n: number | null | undefined) {
  if (n === null || n === undefined) return "—";
  const a = Math.abs(n);
  if (a >= 1e6) return n.toLocaleString(undefined, { maximumFractionDigits: 0 });
  return autoNumber(Math.round(n * 1e6) / 1e6).replace(/\B(?=(\d{3})+(?!\d))(?<!\.\d*)/g, ",");
}

export function StatusBar() {
  const stats = useApp((s) => s.stats);
  const busy = useApp((s) => s.busy);
  const busyLabel = useApp((s) => s.busyLabel);
  const sheet = currentSheet();
  const [mode, setMode] = useState<"sum" | "avg" | "count" | "min" | "max">("sum");
  const [prog, setProg] = useState<{ label?: string; done?: number; total?: number | null; query?: number | null } | null>(null);

  useEffect(() => {
    if (!busy) {
      setProg(null);
      return;
    }
    let alive = true;
    const tick = async () => {
      try {
        const p = await progress();
        if (alive) setProg(p.active ? p : null);
      } catch {
        /* ignore */
      }
      if (alive) window.setTimeout(tick, 350);
    };
    const t = window.setTimeout(tick, 400);
    return () => {
      alive = false;
      window.clearTimeout(t);
    };
  }, [busy]);

  const rows = sheet ? (sheet.filtered ? `${sheet.nview.toLocaleString()} of ${sheet.nrows.toLocaleString()} rows` : `${sheet.nrows.toLocaleString()} rows`) : "";
  const pct = prog?.total ? Math.min(100, Math.round(((prog.done || 0) / prog.total) * 100)) : prog?.query && prog.query > 0 ? Math.round(prog.query) : null;
  return (
    <div className="status">
      {busy > 0 && (
        <span className="status-busy">
          <Loader2 size={13} className="spin" /> {prog?.label || busyLabel || "Working"}
          {pct !== null && <b>{pct}%</b>}
        </span>
      )}
      {stats && stats.counta > 0 && (
        <button className="status-stat" onClick={() => setMode((m) => (m === "sum" ? "avg" : m === "avg" ? "count" : m === "count" ? "min" : m === "min" ? "max" : "sum"))} data-tip="Click to cycle">
          {stats.count > 0 ? (
            <>
              <span>{mode === "sum" ? "Sum" : mode === "avg" ? "Avg" : mode === "count" ? "Count" : mode === "min" ? "Min" : "Max"}</span>
              <b>{fmtStat(mode === "sum" ? stats.sum : mode === "avg" ? stats.avg : mode === "count" ? stats.count : mode === "min" ? stats.min : stats.max)}</b>
            </>
          ) : (
            <>
              <span>Count</span>
              <b>{stats.counta.toLocaleString()}</b>
            </>
          )}
        </button>
      )}
      <span className="status-rows">{rows}</span>
    </div>
  );
}

// ------------------------------------------------------------------- toasts

export function Toasts() {
  const toasts = useApp((s) => s.toasts);
  return (
    <div className="toasts">
      {toasts.map((t) => (
        <div key={t.id} className={`toast ${t.kind}`}>
          {t.kind === "error" ? <AlertTriangle size={16} /> : t.kind === "success" ? <CheckCircle2 size={16} /> : <InfoIcon size={16} />}
          <span>{t.text}</span>
          {t.action && (
            <button
              className="toast-action"
              onClick={() => {
                t.action!.run();
                dismissToast(t.id);
              }}
            >
              {t.action.label}
            </button>
          )}
          <button className="toast-x" onClick={() => dismissToast(t.id)}>
            <X size={14} />
          </button>
        </div>
      ))}
    </div>
  );
}

export function BusyBar() {
  const busy = useApp((s) => s.busy);
  return <div className={`busy-bar ${busy ? "on" : ""}`} />;
}
