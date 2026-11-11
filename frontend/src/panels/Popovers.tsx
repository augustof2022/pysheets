import { ArrowDownAZ, ArrowDownZA, ChevronDown, ChevronUp, Search, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import * as A from "../actions";
import { rpc } from "../api";
import { formatCell } from "../format";
import { TOP_MENUS } from "../menus";
import { call, currentSheet, selectSheet, toast, useApp, type MenuItem } from "../store";
import type { FilterSpec } from "../types";

// ------------------------------------------------------------- filter popover

const CONDITIONS: { op: string; label: string; args: number }[] = [
  { op: "", label: "None", args: 0 },
  { op: "empty", label: "Is empty", args: 0 },
  { op: "not_empty", label: "Is not empty", args: 0 },
  { op: "contains", label: "Text contains", args: 1 },
  { op: "not_contains", label: "Text does not contain", args: 1 },
  { op: "starts", label: "Text starts with", args: 1 },
  { op: "ends", label: "Text ends with", args: 1 },
  { op: "exact", label: "Text is exactly", args: 1 },
  { op: "gt", label: "Greater than", args: 1 },
  { op: "gte", label: "Greater than or equal to", args: 1 },
  { op: "lt", label: "Less than", args: 1 },
  { op: "lte", label: "Less than or equal to", args: 1 },
  { op: "eq", label: "Is equal to", args: 1 },
  { op: "neq", label: "Is not equal to", args: 1 },
  { op: "between", label: "Is between", args: 2 },
  { op: "not_between", label: "Is not between", args: 2 },
  { op: "regex", label: "Matches regex", args: 1 },
];

export function FilterPopover() {
  const fp = useApp((s) => s.filterPopover);
  const sheet = currentSheet();
  const [values, setValues] = useState<[unknown, number][]>([]);
  const [distinct, setDistinct] = useState(0);
  const [truncated, setTruncated] = useState(false);
  const [search, setSearch] = useState("");
  const [checked, setChecked] = useState<Set<string>>(new Set());
  const [allChecked, setAllChecked] = useState(true);
  const [cond, setCond] = useState<{ op: string; value?: string; value2?: string }>({ op: "" });
  const [tab, setTab] = useState<"values" | "condition">("values");
  const [loading, setLoading] = useState(false);
  const col = fp ? sheet?.columns[fp.col] : undefined;
  const existing: FilterSpec | undefined = col ? sheet?.filters[col.id] : undefined;
  const key = (v: unknown) => (v === null ? "\u0000null" : String(v));

  useEffect(() => {
    if (!fp || !sheet) return;
    setSearch("");
    setCond(existing?.cond ? { op: existing.cond.op, value: String(existing.cond.value ?? ""), value2: String(existing.cond.value2 ?? "") } : { op: "" });
    setTab(existing?.cond ? "condition" : "values");
  }, [fp?.col, fp?.x]);

  useEffect(() => {
    if (!fp || !sheet) return;
    let alive = true;
    setLoading(true);
    const t = window.setTimeout(async () => {
      try {
        const { result } = await rpc<{ values: [unknown, number][]; distinct: number; truncated: boolean }>("filter.values", { sheet: sheet.id, col: fp.col, search, limit: 1000 });
        if (!alive) return;
        setValues(result.values);
        setDistinct(result.distinct);
        setTruncated(result.truncated);
        if (!search) {
          if (existing?.include) {
            setAllChecked(false);
            setChecked(new Set(existing.include.map(key)));
          } else if (existing?.exclude) {
            setAllChecked(true);
            const ex = new Set(existing.exclude.map(key));
            setChecked(new Set(result.values.map(([v]) => key(v)).filter((k) => !ex.has(k))));
          } else {
            setAllChecked(true);
            setChecked(new Set(result.values.map(([v]) => key(v))));
          }
        }
      } finally {
        if (alive) setLoading(false);
      }
    }, search ? 120 : 0);
    return () => {
      alive = false;
      window.clearTimeout(t);
    };
  }, [fp?.col, fp?.x, search, sheet?.id]);

  if (!fp || !sheet || !col) return null;
  const close = () => useApp.setState({ filterPopover: null });

  const apply = async () => {
    let spec: FilterSpec | null = {};
    const vals = values.map(([v]) => v);
    const uncheckedVisible = vals.filter((v) => !checked.has(key(v)));
    if (tab === "values") {
      if (search) {
        spec.include = vals.filter((v) => checked.has(key(v)));
      } else if (uncheckedVisible.length === 0 && allChecked) {
        spec = null;
      } else if (!truncated && uncheckedVisible.length <= vals.length / 2) {
        spec.exclude = uncheckedVisible;
      } else {
        spec.include = vals.filter((v) => checked.has(key(v)));
      }
    } else {
      spec = cond.op ? { cond: { op: cond.op, value: cond.value, value2: cond.value2 } } : null;
    }
    close();
    await A.setFilter(fp.col, spec);
  };

  const left = Math.min(fp.x, window.innerWidth - 316);
  const condDef = CONDITIONS.find((c) => c.op === cond.op) || CONDITIONS[0];
  return (
    <>
      <div className="click-catcher" onMouseDown={close} />
      <div className="filter-pop pop-in" style={{ left, top: fp.y }}>
        <div className="fp-head">
          <span className="fp-col">{col.name || `Column ${fp.col + 1}`}</span>
          <span className="fp-sub">{distinct.toLocaleString()} distinct</span>
        </div>
        <div className="fp-sort">
          <button className="btn ghost sm" onClick={() => (close(), A.sortBy(fp.col, true))}>
            <ArrowDownAZ size={15} /> Sort A → Z
          </button>
          <button className="btn ghost sm" onClick={() => (close(), A.sortBy(fp.col, false))}>
            <ArrowDownZA size={15} /> Sort Z → A
          </button>
        </div>
        <div className="seg">
          <button className={tab === "values" ? "on" : ""} onClick={() => setTab("values")}>
            By values
          </button>
          <button className={tab === "condition" ? "on" : ""} onClick={() => setTab("condition")}>
            By condition
          </button>
        </div>
        {tab === "values" ? (
          <>
            <div className="fp-search">
              <Search size={14} />
              <input autoFocus placeholder="Search values" value={search} onChange={(e) => setSearch(e.target.value)} />
              {loading && <span className="mini-spin" />}
            </div>
            <div className="fp-actions">
              <button
                className="link"
                onClick={() => {
                  setAllChecked(true);
                  setChecked(new Set([...checked, ...values.map(([v]) => key(v))]));
                }}
              >
                Select all {values.length < distinct ? values.length.toLocaleString() : ""}
              </button>
              <button
                className="link"
                onClick={() => {
                  setAllChecked(false);
                  const s = new Set(checked);
                  values.forEach(([v]) => s.delete(key(v)));
                  setChecked(s);
                }}
              >
                Clear
              </button>
            </div>
            <div className="fp-list">
              {values.map(([v, n]) => {
                const k = key(v);
                const f = v === null ? "(Blanks)" : formatCell(v, col.type, col.fmt).text || String(v);
                return (
                  <label key={k} className="fp-item">
                    <input
                      type="checkbox"
                      checked={checked.has(k)}
                      onChange={(e) => {
                        const s = new Set(checked);
                        if (e.target.checked) s.add(k);
                        else s.delete(k);
                        setChecked(s);
                      }}
                    />
                    <span className={`fp-val ${v === null ? "muted" : ""}`}>{f}</span>
                    <span className="fp-n">{n.toLocaleString()}</span>
                  </label>
                );
              })}
              {truncated && <div className="fp-more">Showing first {values.length.toLocaleString()} values — search to narrow down</div>}
            </div>
          </>
        ) : (
          <div className="fp-cond">
            <select value={cond.op} onChange={(e) => setCond({ ...cond, op: e.target.value })}>
              {CONDITIONS.map((c) => (
                <option key={c.op} value={c.op}>
                  {c.label}
                </option>
              ))}
            </select>
            {condDef.args >= 1 && <input autoFocus placeholder="Value" value={cond.value || ""} onChange={(e) => setCond({ ...cond, value: e.target.value })} onKeyDown={(e) => e.key === "Enter" && apply()} />}
            {condDef.args >= 2 && <input placeholder="and" value={cond.value2 || ""} onChange={(e) => setCond({ ...cond, value2: e.target.value })} onKeyDown={(e) => e.key === "Enter" && apply()} />}
          </div>
        )}
        <div className="fp-foot">
          {existing && (
            <button className="btn ghost sm" onClick={() => (close(), A.setFilter(fp.col, null))}>
              Clear filter
            </button>
          )}
          <div className="tb-flex" />
          <button className="btn ghost sm" onClick={close}>
            Cancel
          </button>
          <button className="btn primary sm" onClick={apply}>
            OK
          </button>
        </div>
      </div>
    </>
  );
}

// -------------------------------------------------------------- find/replace

export function FindReplace() {
  const open = useApp((s) => s.findOpen);
  const mode = useApp((s) => s.findMode);
  const focusTick = useApp((s) => s.findFocus);
  const [q, setQ] = useState("");
  const [rep, setRep] = useState("");
  const [opts, setOpts] = useState({ match_case: false, whole: false, regex: false, all_sheets: false });
  const [count, setCount] = useState<number | null>(null);
  const [showRep, setShowRep] = useState(mode === "replace");
  const inputRef = useRef<HTMLInputElement>(null);
  const pos = useRef({ row: 0, col: -1, sheet: "" });

  useEffect(() => {
    if (open) {
      setShowRep(mode === "replace");
      // focus + select the search text every time Ctrl+F / Ctrl+H is pressed, even if already open
      requestAnimationFrame(() => {
        inputRef.current?.focus();
        inputRef.current?.select();
      });
    }
  }, [open, mode, focusTick]);

  useEffect(() => {
    setCount(null);
  }, [q, opts]);

  if (!open) return null;

  const go = async (backwards = false) => {
    const sh = currentSheet();
    if (!sh || !q) return;
    const sel = useApp.getState().sel;
    if (pos.current.sheet !== sh.id) pos.current = { row: A.sheetRow(sel.active.r), col: sel.active.c - 1, sheet: sh.id };
    const { result } = await rpc<{ found: boolean; count: number; sheet?: string; row?: number; col?: number; vrow?: number | null }>("find", {
      sheet: sh.id,
      query: q,
      ...opts,
      row: pos.current.row,
      col: pos.current.col,
      backwards,
    });
    setCount(result.count);
    if (!result.found) return;
    if (result.sheet && result.sheet !== sh.id) selectSheet(result.sheet);
    pos.current = { row: result.row!, col: result.col!, sheet: result.sheet || sh.id };
    const vr = result.vrow ?? result.row!;
    window.setTimeout(() => {
      const g = A.getGrid();
      if (!g) return;
      g.findRect = { r1: vr, c1: result.col!, r2: vr, c2: result.col! };
      g.selectCell(vr, result.col!, "api");
    }, result.sheet !== sh.id ? 60 : 0);
  };

  const replaceAll = async () => {
    const sh = currentSheet();
    if (!sh || !q) return;
    const r = await call<{ replaced: number }>("replace_all", { sheet: sh.id, query: q, replacement: rep, ...opts }, { label: "Replacing" });
    if (r) toast(`Replaced ${r.replaced.toLocaleString()} occurrence${r.replaced === 1 ? "" : "s"}`, "success");
    A.invalidate();
    setCount(0);
  };

  const replaceOne = async () => {
    const cell = useApp.getState().cell;
    const sh = currentSheet();
    if (!cell || !sh) return go();
    const flags = opts.match_case ? "g" : "gi";
    const pat = opts.regex ? new RegExp(opts.whole ? `^(?:${q})$` : q, flags) : new RegExp(opts.whole ? `^${escapeRe(q)}$` : escapeRe(q), flags);
    if (pat.test(cell.input)) {
      pat.lastIndex = 0;
      await call("cell.set", { sheet: sh.id, row: cell.row, col: cell.col, value: cell.input.replace(pat, rep) });
      A.invalidate();
    }
    go();
  };

  return (
    <div className="find pop-in" onKeyDown={(e) => e.key === "Escape" && (useApp.setState({ findOpen: false }), A.getGrid()?.focus())}>
      <div className="find-row">
        <button className="icon-btn sm" onClick={() => setShowRep(!showRep)} data-tip="Toggle replace">
          {showRep ? <ChevronUp size={15} /> : <ChevronDown size={15} />}
        </button>
        <div className="find-field">
          <Search size={14} />
          <input
            ref={inputRef}
            placeholder="Find in sheet"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                go(e.shiftKey);
              }
            }}
          />
          {count !== null && <span className="find-count">{count === 0 ? "No results" : `${count.toLocaleString()} found`}</span>}
        </div>
        <button className={`chip ${opts.match_case ? "on" : ""}`} data-tip="Match case" onClick={() => setOpts({ ...opts, match_case: !opts.match_case })}>
          Aa
        </button>
        <button className={`chip ${opts.whole ? "on" : ""}`} data-tip="Match entire cell" onClick={() => setOpts({ ...opts, whole: !opts.whole })}>
          ▭
        </button>
        <button className={`chip ${opts.regex ? "on" : ""}`} data-tip="Regular expression" onClick={() => setOpts({ ...opts, regex: !opts.regex })}>
          .*
        </button>
        <button className={`chip ${opts.all_sheets ? "on" : ""}`} data-tip="Search all sheets" onClick={() => setOpts({ ...opts, all_sheets: !opts.all_sheets })}>
          All
        </button>
        <button className="icon-btn sm" data-tip="Previous (Shift+Enter)" onClick={() => go(true)}>
          <ChevronUp size={15} />
        </button>
        <button className="icon-btn sm" data-tip="Next (Enter)" onClick={() => go(false)}>
          <ChevronDown size={15} />
        </button>
        <button className="icon-btn sm" onClick={() => (useApp.setState({ findOpen: false }), A.getGrid()?.focus())}>
          <X size={15} />
        </button>
      </div>
      {showRep && (
        <div className="find-row rep">
          <div className="find-field">
            <input placeholder="Replace with" value={rep} onChange={(e) => setRep(e.target.value)} onKeyDown={(e) => e.key === "Enter" && replaceOne()} />
          </div>
          <button className="btn ghost sm" onClick={replaceOne}>
            Replace
          </button>
          <button className="btn primary sm" onClick={replaceAll}>
            Replace all
          </button>
        </div>
      )}
    </div>
  );
}

function escapeRe(s: string) {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

// ----------------------------------------------------------- command palette

interface Cmd {
  label: string;
  group: string;
  shortcut?: string;
  run: () => void;
}

function flatten(items: MenuItem[], group: string, prefix = ""): Cmd[] {
  const out: Cmd[] = [];
  for (const it of items) {
    if (it.divider || it.disabled) continue;
    if (it.items) out.push(...flatten(it.items, group, `${prefix}${it.label} › `));
    else if (it.run && it.label) out.push({ label: prefix + it.label, group, shortcut: it.shortcut, run: it.run });
  }
  return out;
}

export function CommandPalette() {
  const open = useApp((s) => s.palette);
  const [q, setQ] = useState("");
  const [idx, setIdx] = useState(0);
  const listRef = useRef<HTMLDivElement>(null);
  const cmds = useMemo(() => {
    if (!open) return [];
    const out: Cmd[] = [];
    for (const m of TOP_MENUS) out.push(...flatten(m.items(), m.label));
    const info = useApp.getState().info;
    for (const s of info?.sheets || []) out.push({ label: `Go to sheet: ${s.name}`, group: "Sheets", run: () => selectSheet(s.id) });
    const sh = currentSheet();
    sh?.columns.forEach((c, i) => {
      if (c.name) out.push({ label: `Go to column: ${c.name}`, group: "Columns", run: () => A.getGrid()?.selectCell(Math.max(1, A.getGrid()?.sel.active.r ?? 1), i, "api") });
    });
    return out;
  }, [open]);
  const filtered = useMemo(() => {
    const t = q.toLowerCase().trim();
    if (!t) return cmds.slice(0, 60);
    const words = t.split(/\s+/);
    return cmds
      .map((c) => {
        const l = c.label.toLowerCase();
        if (!words.every((w) => l.includes(w) || c.group.toLowerCase().includes(w))) return null;
        return { c, score: (l.startsWith(t) ? 0 : 1) + l.indexOf(words[0]) / 100 };
      })
      .filter(Boolean)
      .sort((a, b) => a!.score - b!.score)
      .slice(0, 60)
      .map((x) => x!.c);
  }, [q, cmds]);

  useEffect(() => {
    setIdx(0);
  }, [q]);
  useEffect(() => {
    if (open) setQ("");
  }, [open]);
  useEffect(() => {
    listRef.current?.querySelector(".on")?.scrollIntoView({ block: "nearest" });
  }, [idx]);

  if (!open) return null;
  const close = () => {
    useApp.setState({ palette: false });
    A.getGrid()?.focus();
  };
  const run = (c: Cmd) => {
    close();
    window.setTimeout(c.run, 10);
  };
  return (
    <div className="overlay fade-in" onMouseDown={close}>
      <div className="palette scale-in" onMouseDown={(e) => e.stopPropagation()}>
        <div className="palette-input">
          <Search size={17} />
          <input
            autoFocus
            placeholder="Type a command, sheet or column…"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Escape") close();
              else if (e.key === "ArrowDown") {
                e.preventDefault();
                setIdx((i) => Math.min(filtered.length - 1, i + 1));
              } else if (e.key === "ArrowUp") {
                e.preventDefault();
                setIdx((i) => Math.max(0, i - 1));
              } else if (e.key === "Enter" && filtered[idx]) run(filtered[idx]);
            }}
          />
        </div>
        <div className="palette-list" ref={listRef}>
          {filtered.length === 0 && <div className="palette-empty">No matching commands</div>}
          {filtered.map((c, i) => (
            <div key={c.group + c.label} className={`palette-item ${i === idx ? "on" : ""}`} onMouseEnter={() => setIdx(i)} onClick={() => run(c)}>
              <span className="palette-group">{c.group}</span>
              <span className="palette-label">{c.label}</span>
              {c.shortcut && <kbd>{c.shortcut}</kbd>}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
