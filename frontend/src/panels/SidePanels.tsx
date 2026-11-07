import { Plus, Trash2, X, ArrowRight, Sparkles, AlertTriangle } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import * as A from "../actions";
import { rpc } from "../api";
import { scaleAt } from "../grid/GridController";
import { autoNumber, colLetters } from "../format";
import { usePresence } from "../components/Menu";
import { call, closePanel, currentSheet, primaryRect, query, selectSheet, toast, useApp } from "../store";
import type { CondFormat, PivotSpec, SheetInfo, Validation } from "../types";

function PanelShell({ title, children, footer }: { title: string; children: React.ReactNode; footer?: React.ReactNode }) {
  return (
    <>
      <div className="panel-head">
        <h3>{title}</h3>
        <button className="icon-btn" onClick={closePanel}>
          <X size={18} />
        </button>
      </div>
      <div className="panel-body">{children}</div>
      {footer && <div className="panel-foot">{footer}</div>}
    </>
  );
}

function selectedColumns(sh: SheetInfo): string[] {
  const out: string[] = [];
  for (const r of useApp.getState().sel.ranges) {
    for (let c = r.c1; c <= Math.min(r.c2, sh.ncols - 1); c++) if (sh.columns[c] && !out.includes(sh.columns[c].id)) out.push(sh.columns[c].id);
  }
  return out;
}

function ColumnChips({ sh, value, onChange }: { sh: SheetInfo; value: string[]; onChange: (v: string[]) => void }) {
  return (
    <div className="chips wrap">
      {sh.columns.map((c, i) => (
        <button
          key={c.id}
          className={`chip ${value.includes(c.id) ? "on" : ""}`}
          onClick={() => onChange(value.includes(c.id) ? value.filter((x) => x !== c.id) : [...value, c.id])}
        >
          <span className="chip-letter">{colLetters(i)}</span>
          {c.name || "(no header)"}
        </button>
      ))}
    </div>
  );
}

// ------------------------------------------------------------------- pivot

const AGGS = [
  ["sum", "SUM"],
  ["count", "COUNT"],
  ["counta", "COUNTA"],
  ["countd", "COUNTUNIQUE"],
  ["avg", "AVERAGE"],
  ["min", "MIN"],
  ["max", "MAX"],
  ["median", "MEDIAN"],
  ["stdev", "STDEV"],
];

function PivotPanel() {
  const props = useApp((s) => s.panelProps) as { source?: string; target?: string | null };
  const info = useApp((s) => s.info);
  const targetSheet = props.target ? info?.sheets.find((s) => s.id === props.target) : null;
  const [spec, setSpec] = useState<PivotSpec>(() => targetSheet?.pivot ? { ...targetSheet.pivot } : { source: props.source || currentSheet()!.id, rows: [], cols: [], values: [], totals: true });
  const src = info?.sheets.find((s) => s.id === spec.source);
  const [preview, setPreview] = useState<{ labels: string[]; rows: unknown[][] } | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (!spec.rows.length && !spec.values.length && !spec.cols.length) {
      setPreview(null);
      return;
    }
    const t = window.setTimeout(async () => {
      try {
        const { result } = await rpc<{ labels: string[]; rows: unknown[][] }>("pivot.preview", { spec });
        setPreview(result);
        setErr(null);
      } catch (e) {
        setErr(String((e as Error).message || e));
      }
    }, 150);
    return () => window.clearTimeout(t);
  }, [spec]);

  if (!src) return <PanelShell title="Pivot table">Source sheet not found.</PanelShell>;
  const name = (id: string) => src.columns.find((c) => c.id === id)?.name || "?";
  const used = new Set([...spec.rows, ...spec.cols]);
  const addSel = (key: "rows" | "cols" | "values") => (
    <select
      className="add-select"
      value=""
      onChange={(e) => {
        const id = e.target.value;
        if (!id) return;
        if (key === "values") {
          const col = src.columns.find((c) => c.id === id);
          setSpec({ ...spec, values: [...spec.values, { col: id, agg: col?.type === "num" ? "sum" : "counta" }] });
        } else setSpec({ ...spec, [key]: [...spec[key], id] });
      }}
    >
      <option value="">+ Add</option>
      {src.columns.map((c, i) =>
        key !== "values" && used.has(c.id) ? null : (
          <option key={c.id} value={c.id}>
            {c.name || colLetters(i)}
          </option>
        ),
      )}
    </select>
  );

  const create = async () => {
    const r = await call<{ sheet: string }>("pivot.create", { spec, target: props.target || null }, { label: "Building pivot table" });
    if (r?.sheet) {
      selectSheet(r.sheet);
      useApp.setState({ panelProps: { ...props, target: r.sheet } });
      A.invalidate();
    }
  };

  return (
    <PanelShell
      title="Pivot table"
      footer={
        <>
          <span className="foot-note">Source: {src.name}</span>
          <div className="tb-flex" />
          <button className="btn primary" disabled={!spec.rows.length && !spec.values.length} onClick={create}>
            {props.target ? "Update pivot" : "Create pivot"}
          </button>
        </>
      }
    >
      <div className="field">
        <label>Source data</label>
        <select value={spec.source} onChange={(e) => setSpec({ source: e.target.value, rows: [], cols: [], values: [], totals: true })} disabled={!!props.target}>
          {info?.sheets.filter((s) => !s.pivot).map((s) => (
            <option key={s.id} value={s.id}>
              {s.name} ({s.nrows.toLocaleString()} rows)
            </option>
          ))}
        </select>
      </div>
      {(["rows", "cols"] as const).map((key) => (
        <div className="pv-section" key={key}>
          <div className="pv-title">
            <span>{key === "rows" ? "Rows" : "Columns"}</span>
            {addSel(key)}
          </div>
          {spec[key].map((id, i) => (
            <div className="pv-field" key={id}>
              <span className="pv-name">{name(id)}</span>
              <button className="icon-btn sm" onClick={() => setSpec({ ...spec, [key]: spec[key].filter((_, j) => j !== i) })}>
                <X size={14} />
              </button>
            </div>
          ))}
        </div>
      ))}
      <div className="pv-section">
        <div className="pv-title">
          <span>Values</span>
          {addSel("values")}
        </div>
        {spec.values.map((v, i) => (
          <div className="pv-field" key={i}>
            <select
              value={v.agg}
              onChange={(e) => {
                const values = [...spec.values];
                values[i] = { ...v, agg: e.target.value };
                setSpec({ ...spec, values });
              }}
            >
              {AGGS.map(([a, l]) => (
                <option key={a} value={a}>
                  {l}
                </option>
              ))}
            </select>
            <span className="pv-name">of {name(v.col)}</span>
            <button className="icon-btn sm" onClick={() => setSpec({ ...spec, values: spec.values.filter((_, j) => j !== i) })}>
              <X size={14} />
            </button>
          </div>
        ))}
      </div>
      <div className="opt-col">
        <label className="switch">
          <input type="checkbox" checked={spec.totals !== false} onChange={(e) => setSpec({ ...spec, totals: e.target.checked })} />
          <span /> Show grand totals
        </label>
        <label className="switch">
          <input type="checkbox" checked={!!spec.sort_by_value} onChange={(e) => setSpec({ ...spec, sort_by_value: e.target.checked })} />
          <span /> Sort by first value
        </label>
        <label className="switch">
          <input type="checkbox" checked={!!spec.desc} onChange={(e) => setSpec({ ...spec, desc: e.target.checked })} />
          <span /> Descending
        </label>
      </div>
      {err && (
        <div className="note err">
          <AlertTriangle size={14} /> {err}
        </div>
      )}
      {preview && (
        <div className="preview small">
          <div className="label">Live preview</div>
          <div className="preview-scroll">
            <table>
              <thead>
                <tr>
                  {preview.labels.map((l, i) => (
                    <th key={i}>{l}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {preview.rows.slice(0, 30).map((r, i) => (
                  <tr key={i}>
                    {r.map((v, j) => (
                      <td key={j} className={typeof v === "number" ? "num" : ""}>
                        {v === null ? "" : typeof v === "number" ? v.toLocaleString(undefined, { maximumFractionDigits: 2 }) : String(v)}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </PanelShell>
  );
}

// -------------------------------------------------------- conditional format

const CF_KINDS: [string, string, number][] = [
  ["gt", "Greater than", 1],
  ["gte", "Greater than or equal", 1],
  ["lt", "Less than", 1],
  ["lte", "Less than or equal", 1],
  ["eq", "Is equal to", 1],
  ["neq", "Is not equal to", 1],
  ["between", "Is between", 2],
  ["contains", "Text contains", 1],
  ["not_contains", "Text does not contain", 1],
  ["starts", "Text starts with", 1],
  ["ends", "Text ends with", 1],
  ["empty", "Is empty", 0],
  ["not_empty", "Is not empty", 0],
  ["duplicate", "Duplicate values", 0],
  ["unique", "Unique values", 0],
  ["top", "Top N", 1],
  ["bottom", "Bottom N", 1],
  ["above_avg", "Above average", 0],
  ["below_avg", "Below average", 0],
  ["color_scale", "Color scale", 0],
  ["formula", "Custom formula is", 1],
];

const CF_STYLES = [
  { bg: "#fde2e1", fg: "#9f1c1c", label: "Red" },
  { bg: "#fef3c7", fg: "#92400e", label: "Amber" },
  { bg: "#dcfce7", fg: "#166534", label: "Green" },
  { bg: "#dbeafe", fg: "#1e40af", label: "Blue" },
  { bg: "#ede9fe", fg: "#5b21b6", label: "Violet" },
  { bg: null, fg: "#dc2626", label: "Red text", bold: true },
];

const SCALES = [
  ["#f8d0cf", "#fff3c4", "#c7ecd6"],
  ["#c7ecd6", "#fff3c4", "#f8d0cf"],
  ["#ffffff", "#93c5fd"],
  ["#ffffff", "#86efac"],
  ["#ffffff", "#fca5a5"],
];

function CondFormatPanel() {
  const sh = currentSheet()!;
  const [cols, setCols] = useState<string[]>(() => selectedColumns(sh));
  const [kind, setKind] = useState("gt");
  const [value, setValue] = useState("");
  const [value2, setValue2] = useState("");
  const [style, setStyle] = useState(0);
  const [scale, setScale] = useState(0);
  const def = CF_KINDS.find((k) => k[0] === kind)!;

  const add = async () => {
    if (!cols.length) return toast("Choose at least one column", "error");
    const rule: Partial<CondFormat> = { cols, kind, r1: Math.max(1, A.toSheetRect(primaryRect()).r1 > sh.nrows ? 1 : 1) };
    if (kind === "formula") rule.formula = value.startsWith("=") ? value : `=${value}`;
    else if (def[2] >= 1) rule.value = value;
    if (def[2] >= 2) rule.value2 = value2;
    if (kind === "color_scale") rule.colors = SCALES[scale];
    else {
      const s = CF_STYLES[style];
      rule.style = { bg: s.bg, fg: s.fg, bold: s.bold };
    }
    await call("cf.add", { sheet: sh.id, rule });
    A.invalidate();
  };

  return (
    <PanelShell
      title="Conditional formatting"
      footer={
        <>
          <div className="tb-flex" />
          <button className="btn primary" onClick={add}>
            <Plus size={15} /> Add rule
          </button>
        </>
      }
    >
      <div className="field">
        <label>Apply to columns</label>
        <ColumnChips sh={sh} value={cols} onChange={setCols} />
      </div>
      <div className="field">
        <label>Format cells if…</label>
        <select value={kind} onChange={(e) => setKind(e.target.value)}>
          {CF_KINDS.map(([k, l]) => (
            <option key={k} value={k}>
              {l}
            </option>
          ))}
        </select>
      </div>
      {def[2] >= 1 && (
        <div className="row-gap">
          <input placeholder={kind === "formula" ? "=$C2>100" : kind === "top" || kind === "bottom" ? "10" : "Value"} value={value} onChange={(e) => setValue(e.target.value)} />
          {def[2] >= 2 && <input placeholder="and" value={value2} onChange={(e) => setValue2(e.target.value)} />}
        </div>
      )}
      {kind === "color_scale" ? (
        <div className="field">
          <label>Scale</label>
          <div className="scales">
            {SCALES.map((s, i) => (
              <button key={i} className={`scale ${scale === i ? "on" : ""}`} onClick={() => setScale(i)} style={{ background: `linear-gradient(90deg, ${s.map((c, j) => `${scaleAt(s, j / (s.length - 1))}`).join(", ")})` }} />
            ))}
          </div>
        </div>
      ) : (
        <div className="field">
          <label>Style</label>
          <div className="cf-styles">
            {CF_STYLES.map((s, i) => (
              <button key={i} className={`cf-style ${style === i ? "on" : ""}`} style={{ background: s.bg || "var(--surface)", color: s.fg, fontWeight: s.bold ? 700 : 500 }} onClick={() => setStyle(i)}>
                123
              </button>
            ))}
          </div>
        </div>
      )}
      <div className="rules">
        <div className="label">Rules on this sheet</div>
        {sh.cond_formats.length === 0 && <div className="muted small">No rules yet</div>}
        {sh.cond_formats.map((r) => (
          <div key={r.id} className="rule">
            <span className="rule-swatch" style={r.kind === "color_scale" ? { background: `linear-gradient(90deg, ${(r.colors || SCALES[0]).join(",")})` } : { background: r.style?.bg || "transparent", color: r.style?.fg || undefined }}>
              {r.kind === "color_scale" ? "" : "Aa"}
            </span>
            <div className="rule-text">
              <b>{r.name || CF_KINDS.find((k) => k[0] === r.kind)?.[1] || r.kind}</b>
              <span>
                {r.kind === "formula" ? r.formula : r.value !== undefined && r.value !== "" ? String(r.value) : ""}
                {r.value2 ? ` – ${r.value2}` : ""} · {r.cols.map((c) => sh.columns.find((x) => x.id === c)?.name || "?").join(", ")}
              </span>
            </div>
            <button className="icon-btn sm" onClick={() => call("cf.delete", { sheet: sh.id, id: r.id }).then(A.invalidate)}>
              <Trash2 size={14} />
            </button>
          </div>
        ))}
      </div>
    </PanelShell>
  );
}

// --------------------------------------------------------------- validation

const DV_KINDS: [string, string][] = [
  ["list", "Dropdown (list of items)"],
  ["list_range", "Dropdown (from a range)"],
  ["number", "Number"],
  ["text_length", "Text length"],
  ["date", "Date"],
  ["text_contains", "Text contains"],
  ["text_not_contains", "Text does not contain"],
  ["email", "Valid e-mail"],
  ["url", "Valid URL"],
  ["regex", "Matches regular expression"],
  ["unique", "Unique (no duplicates)"],
  ["checkbox", "Checkbox"],
  ["formula", "Custom formula is"],
];
const DV_OPS: [string, string][] = [
  ["between", "between"],
  ["not_between", "not between"],
  ["gt", "greater than"],
  ["gte", "greater than or equal"],
  ["lt", "less than"],
  ["lte", "less than or equal"],
  ["eq", "equal to"],
  ["neq", "not equal to"],
];

function ValidationPanel() {
  const sh = currentSheet()!;
  const props = useApp((s) => s.panelProps) as { kind?: string };
  const [cols, setCols] = useState<string[]>(() => selectedColumns(sh));
  const [kind, setKind] = useState(props.kind || "list");
  const [op, setOp] = useState("between");
  const [v1, setV1] = useState("");
  const [v2, setV2] = useState("");
  const [items, setItems] = useState("");
  const [strict, setStrict] = useState(false);
  const [message, setMessage] = useState("");
  const [report, setReport] = useState<Record<string, { invalid: number; first: [number, number] | null }>>({});

  const loadReport = async () => {
    const r = await query<{ rules: { id: string; invalid: number; first: [number, number] | null }[] }>("dv.report", { sheet: sh.id });
    if (r) setReport(Object.fromEntries(r.rules.map((x) => [x.id, x])));
  };
  useEffect(() => {
    loadReport();
  }, [sh.version]);

  const add = async () => {
    if (!cols.length) return toast("Choose at least one column", "error");
    const rule: Partial<Validation> = { cols, kind: kind === "list_range" ? "list" : kind, strict, message: message || undefined };
    if (kind === "list") rule.values = items.split(/[,\n]/).map((s) => s.trim()).filter(Boolean);
    if (kind === "list_range") rule.source = v1;
    if (["number", "text_length", "date"].includes(kind)) Object.assign(rule, { op, value: v1, value2: v2 });
    if (["text_contains", "text_not_contains"].includes(kind)) rule.value = v1;
    if (kind === "regex") rule.pattern = v1;
    if (kind === "formula") Object.assign(rule, { formula: v1.startsWith("=") ? v1 : `=${v1}` });
    await call("dv.add", { sheet: sh.id, rule });
    A.invalidate();
    loadReport();
  };

  return (
    <PanelShell
      title="Data validation"
      footer={
        <>
          <div className="tb-flex" />
          <button className="btn primary" onClick={add}>
            <Plus size={15} /> Add rule
          </button>
        </>
      }
    >
      <div className="field">
        <label>Apply to columns</label>
        <ColumnChips sh={sh} value={cols} onChange={setCols} />
      </div>
      <div className="field">
        <label>Criteria</label>
        <select value={kind} onChange={(e) => setKind(e.target.value)}>
          {DV_KINDS.map(([k, l]) => (
            <option key={k} value={k}>
              {l}
            </option>
          ))}
        </select>
      </div>
      {kind === "list" && <textarea className="sql-box" placeholder="Option A, Option B, Option C" value={items} onChange={(e) => setItems(e.target.value)} />}
      {kind === "list_range" && <input placeholder="Sheet2!A2:A or Lists!B:B" value={v1} onChange={(e) => setV1(e.target.value)} />}
      {["number", "text_length", "date"].includes(kind) && (
        <div className="row-gap">
          <select value={op} onChange={(e) => setOp(e.target.value)}>
            {DV_OPS.map(([k, l]) => (
              <option key={k} value={k}>
                {l}
              </option>
            ))}
          </select>
          <input placeholder={kind === "date" ? "2024-01-01" : "Value"} value={v1} onChange={(e) => setV1(e.target.value)} />
          {op.includes("between") && <input placeholder="and" value={v2} onChange={(e) => setV2(e.target.value)} />}
        </div>
      )}
      {["text_contains", "text_not_contains", "regex", "formula"].includes(kind) && (
        <input placeholder={kind === "regex" ? "^[A-Z]{3}-\\d+$" : kind === "formula" ? "=AND(C2>0, C2<100)" : "Text"} value={v1} onChange={(e) => setV1(e.target.value)} />
      )}
      {kind !== "checkbox" && (
        <>
          <div className="seg full">
            <button className={!strict ? "on" : ""} onClick={() => setStrict(false)}>
              Show warning
            </button>
            <button className={strict ? "on" : ""} onClick={() => setStrict(true)}>
              Reject input
            </button>
          </div>
          <input placeholder="Help text shown when rejected (optional)" value={message} onChange={(e) => setMessage(e.target.value)} />
        </>
      )}
      <div className="rules">
        <div className="label">Rules on this sheet</div>
        {sh.validations.length === 0 && <div className="muted small">No rules yet</div>}
        {sh.validations.map((r) => {
          const rep = report[r.id];
          return (
            <div key={r.id} className="rule">
              <span className={`rule-badge ${rep?.invalid ? "bad" : "ok"}`}>{rep ? (rep.invalid ? rep.invalid.toLocaleString() : "✓") : "…"}</span>
              <div className="rule-text">
                <b>{DV_KINDS.find((k) => k[0] === r.kind)?.[1] || r.kind}</b>
                <span>
                  {r.values?.slice(0, 5).join(", ") || r.source || (r.op ? `${r.op} ${r.value ?? ""}${r.value2 ? " – " + r.value2 : ""}` : r.pattern || "")} · {r.cols.map((c) => sh.columns.find((x) => x.id === c)?.name || "?").join(", ")}
                  {r.strict ? " · rejects" : ""}
                </span>
              </div>
              {rep?.first && (
                <button className="icon-btn sm" data-tip="Go to first invalid" onClick={() => A.getGrid()?.selectCell(rep.first![0], rep.first![1], "api")}>
                  <ArrowRight size={14} />
                </button>
              )}
              <button className="icon-btn sm" onClick={() => call("dv.delete", { sheet: sh.id, id: r.id }).then(() => (A.invalidate(), loadReport()))}>
                <Trash2 size={14} />
              </button>
            </div>
          );
        })}
      </div>
    </PanelShell>
  );
}

// --------------------------------------------------------------- duplicates

function DuplicatesPanel() {
  const sh = currentSheet()!;
  const props = useApp((s) => s.panelProps) as { cols?: number[] };
  const [cols, setCols] = useState<string[]>(() => (props.cols || []).map((c) => sh.columns[c]?.id).filter(Boolean) as string[]);
  const [ignoreCase, setIgnoreCase] = useState(true);
  const [trim, setTrim] = useState(true);
  const [res, setRes] = useState<{ rows: number; unique: number; duplicates: number; groups: number; top?: [string, number][]; removed?: number } | null>(null);
  const idx = useMemo(() => cols.map((id) => sh.columns.findIndex((c) => c.id === id)).filter((i) => i >= 0), [cols, sh]);

  const runAction = async (action: string) => {
    if (!idx.length) return toast("Choose at least one column", "error");
    if (action === "remove" && !window.confirm("Remove duplicate rows, keeping the first occurrence?")) return;
    const r = await call<typeof res>("dups", { sheet: sh.id, cols: idx, action, ignore_case: ignoreCase, trim }, { label: "Checking duplicates" });
    if (!r) return;
    setRes(r);
    if (action === "remove") toast(`Removed ${(r.removed ?? 0).toLocaleString()} duplicate rows`, "success");
    if (action === "highlight") toast("Duplicates highlighted", "success");
    if (action === "mark") toast("Added an “Is duplicate” column", "success");
    A.invalidate();
  };

  useEffect(() => {
    if (idx.length) runAction("count");
    else setRes(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cols, ignoreCase, trim]);

  return (
    <PanelShell title="Duplicates">
      <div className="field">
        <label>Rows are duplicates when these columns match</label>
        <ColumnChips sh={sh} value={cols} onChange={setCols} />
      </div>
      <div className="opt-col">
        <label className="switch">
          <input type="checkbox" checked={ignoreCase} onChange={(e) => setIgnoreCase(e.target.checked)} />
          <span /> Ignore case
        </label>
        <label className="switch">
          <input type="checkbox" checked={trim} onChange={(e) => setTrim(e.target.checked)} />
          <span /> Ignore leading/trailing spaces
        </label>
      </div>
      {res && (
        <div className="stat-cards">
          <div className="stat-card">
            <span>Rows</span>
            <b>{res.rows.toLocaleString()}</b>
          </div>
          <div className="stat-card">
            <span>Unique</span>
            <b>{res.unique.toLocaleString()}</b>
          </div>
          <div className={`stat-card ${res.duplicates ? "warn" : "ok"}`}>
            <span>Duplicates</span>
            <b>{res.duplicates.toLocaleString()}</b>
          </div>
        </div>
      )}
      {res?.top && res.top.length > 0 && (
        <div className="top-list">
          <div className="label">Most repeated</div>
          {res.top.slice(0, 10).map(([k, n]) => (
            <div key={k} className="top-item">
              <span>{k || "(blank)"}</span>
              <b>×{n.toLocaleString()}</b>
            </div>
          ))}
        </div>
      )}
      <div className="action-list">
        <button className="btn ghost full" onClick={() => runAction("highlight")}>
          Highlight duplicates
        </button>
        <button className="btn ghost full" onClick={() => runAction("mark")}>
          Add “Is duplicate” column
        </button>
        <button className="btn danger full" onClick={() => runAction("remove")}>
          Remove duplicates (keep first)
        </button>
      </div>
    </PanelShell>
  );
}

// ------------------------------------------------------------------ profile

function ProfilePanel() {
  const props = useApp((s) => s.panelProps) as { col?: number };
  const sh = currentSheet()!;
  const col = props.col ?? useApp.getState().sel.active.c;
  const [p, setP] = useState<{ rows: number; filled: number; distinct: number; min: unknown; max: unknown; avg: unknown; top: [string, number][]; type: string } | null>(null);
  useEffect(() => {
    rpc<typeof p>("col.profile", { sheet: sh.id, col }).then(({ result }) => setP(result)).catch(() => setP(null));
  }, [col, sh.version]);
  const c = sh.columns[col];
  const fmt = (v: unknown) => (v === null || v === undefined ? "—" : typeof v === "number" ? autoNumber(Math.round(v * 1000) / 1000) : String(v));
  return (
    <PanelShell title={`Column ${colLetters(col)}${c?.name ? ` · ${c.name}` : ""}`}>
      {!p ? (
        <div className="loading-block">
          <span className="mini-spin" />
        </div>
      ) : (
        <>
          <div className="stat-cards">
            <div className="stat-card">
              <span>Filled</span>
              <b>{p.filled.toLocaleString()}</b>
            </div>
            <div className="stat-card">
              <span>Empty</span>
              <b>{(p.rows - p.filled).toLocaleString()}</b>
            </div>
            <div className="stat-card">
              <span>Distinct</span>
              <b>{p.distinct.toLocaleString()}</b>
            </div>
          </div>
          {p.type === "num" && (
            <div className="stat-cards">
              <div className="stat-card">
                <span>Min</span>
                <b>{fmt(p.min)}</b>
              </div>
              <div className="stat-card">
                <span>Average</span>
                <b>{fmt(p.avg)}</b>
              </div>
              <div className="stat-card">
                <span>Max</span>
                <b>{fmt(p.max)}</b>
              </div>
            </div>
          )}
          <div className="top-list">
            <div className="label">Most common values</div>
            {p.top.map(([k, n]) => (
              <div key={k} className="top-item bar">
                <i style={{ width: `${Math.max(2, (n / Math.max(1, p.filled)) * 100)}%` }} />
                <span>{k}</span>
                <b>{n.toLocaleString()}</b>
              </div>
            ))}
          </div>
          <div className="muted small">Type: {c?.duck}</div>
        </>
      )}
    </PanelShell>
  );
}

// -------------------------------------------------------------------- clean

function CleanPanel() {
  const run = async (op: string, label: string) => {
    const sh = currentSheet();
    if (!sh) return;
    const rects = A.selectionRects().map((r) => [r.r1, r.c1, r.r2, r.c2]);
    const r = await call<{ changed: number }>("clean.text", { sheet: sh.id, rects, op }, { label });
    if (r) toast(`${label}: ${r.changed.toLocaleString()} cells changed`, "success");
    A.invalidate();
  };
  return (
    <PanelShell title="Clean up text">
      <p className="muted small">Applies to the selected cells or columns. Undo is available.</p>
      <div className="action-list">
        <button className="btn ghost full" onClick={() => run("trim", "Trim whitespace")}>
          <Sparkles size={15} /> Trim whitespace
        </button>
        <button className="btn ghost full" onClick={() => run("clean", "Remove non-printing characters")}>
          Remove non-printing characters
        </button>
        <button className="btn ghost full" onClick={() => run("upper", "UPPER CASE")}>
          UPPER CASE
        </button>
        <button className="btn ghost full" onClick={() => run("lower", "lower case")}>
          lower case
        </button>
        <button className="btn ghost full" onClick={() => run("proper", "Proper Case")}>
          Proper Case
        </button>
      </div>
    </PanelShell>
  );
}

export function SidePanel() {
  const panel = useApp((s) => s.panel);
  const { mounted, state } = usePresence(!!panel, 220);
  const [shown, setShown] = useState(panel);
  useEffect(() => {
    if (panel) setShown(panel);
  }, [panel]);
  if (!mounted || !currentSheet()) return null;
  const which = panel || shown;
  return (
    <aside className="side-panel" data-state={state}>
      {which === "pivot" && <PivotPanel key={(useApp.getState().panelProps.target as string) || "new"} />}
      {which === "cf" && <CondFormatPanel />}
      {which === "dv" && <ValidationPanel />}
      {which === "dups" && <DuplicatesPanel />}
      {which === "profile" && <ProfilePanel />}
      {which === "clean" && <CleanPanel />}
    </aside>
  );
}
