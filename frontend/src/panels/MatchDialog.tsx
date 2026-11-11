import { useEffect, useRef, useState } from "react";
import * as A from "../actions";
import { call, currentSheet, query, selectSheet, useApp } from "../store";
import { cellRef, colLetters } from "../format";
import { Shell } from "./Dialogs";

interface LookupRow {
  id: number;
  sheet: string;
  sheet_name: string;
  row: number;
  col: number;
  ref: string;
  text: string;
  kind: "cell" | "column";
  mode: "smart" | "strict";
  exact: number;
  fuzzy: number;
  missing: number;
  blank: number;
  filter_fuzzy?: number;
}

interface Health {
  sheet_name: string;
  name: string;
  col: number;
  type: string;
  filled: number;
  numbers_as_text: number;
  all_numeric: boolean;
  spaces: number;
  invisible: number;
}

interface Report {
  a: Health;
  b: Health;
  total: number;
  exact: number;
  fuzzy: number;
  missing: number;
  reasons: Record<string, number>;
  fuzzy_samples: { row: number; value: string; b_row: number; b_value: string; reason: string }[];
  missing_samples: { row: number; value: string }[];
  suggest: "number" | "text";
}

const REASONS: Record<string, string> = {
  type: "Number vs text",
  spaces: "Extra spaces",
  invisible: "Hidden characters",
  zeros: "Leading zeros",
  separators: "Separators / currency",
  decimals: "Decimals or exponent",
  other: "Other formatting",
};

const LOOKUP_RE = /\b(VLOOKUP|HLOOKUP|XLOOKUP|MATCH)\(\s*(\$?[A-Z]{1,3}\$?\d*)\s*,\s*(?:'?([^'!]+)'?!)?\$?([A-Z]{1,3})/i;

/** Guess the two key columns from a lookup formula, e.g. VLOOKUP(A2, Sheet2!C:F, ...) → A ↔ Sheet2!C. */
function guessFromFormula(text: string): { colA: number; sheetB: string | null; colB: number } | null {
  const m = text.match(LOOKUP_RE);
  if (!m) return null;
  const letters = (s: string) => [...s.replace(/\$|\d/g, "").toUpperCase()].reduce((a, ch) => a * 26 + ch.charCodeAt(0) - 64, 0) - 1;
  return { colA: letters(m[2]), sheetB: m[3] ?? null, colB: letters(m[4]) };
}

function Num({ n }: { n: number }) {
  return <>{n.toLocaleString()}</>;
}

export function MatchDialog() {
  const info = useApp((s) => s.info);
  const props = useApp((s) => s.dialogProps) as { formula?: number };
  const sheets = info?.sheets ?? [];
  const cur = currentSheet();
  const [mode, setMode] = useState<"smart" | "strict">((info as { match_mode?: "smart" | "strict" } | null)?.match_mode ?? "smart");
  const [lookups, setLookups] = useState<LookupRow[] | null>(null);
  const [sheetA, setSheetA] = useState(cur?.id ?? sheets[0]?.id ?? "");
  const [colA, setColA] = useState(useApp.getState().sel.active.c);
  const [sheetB, setSheetB] = useState(sheets.find((s) => s.id !== cur?.id)?.id ?? cur?.id ?? "");
  const [colB, setColB] = useState(0);
  const [report, setReport] = useState<Report | null>(null);
  const [busy, setBusy] = useState(false);
  const [to, setTo] = useState<"number" | "text">("text");
  const [keep, setKeep] = useState(true);
  const picked = useRef(false);

  async function loadLookups() {
    const r = await query<{ mode: "smart" | "strict"; formulas: LookupRow[] }>("match.summary", {});
    if (r) {
      setLookups(r.formulas);
      setMode(r.mode);
    }
  }

  function pickFormula(f: LookupRow) {
    const g = guessFromFormula(f.text);
    setSheetA(f.sheet);
    if (g) {
      setColA(g.colA);
      const sb = g.sheetB ? sheets.find((s) => s.name.toLowerCase() === g.sheetB!.toLowerCase()) : sheets.find((s) => s.id === f.sheet);
      if (sb) setSheetB(sb.id);
      setColB(g.colB);
    }
    setReport(null);
  }

  useEffect(() => {
    loadLookups();
  }, []);

  useEffect(() => {
    // opened from a formula's "Review": preselect its key columns once
    if (props.formula && lookups && !picked.current) {
      picked.current = true;
      const f = lookups.find((x) => x.id === props.formula);
      if (f) pickFormula(f);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lookups]);

  async function changeMode(m: "smart" | "strict") {
    setMode(m);
    await call("workbook.match_mode", { mode: m }, { label: "Recalculating lookups" });
    A.invalidate();
    loadLookups();
  }

  async function check() {
    setBusy(true);
    const r = await query<Report>("match.report", { sheet_a: sheetA, col_a: colA, sheet_b: sheetB, col_b: colB });
    setBusy(false);
    if (r) {
      setReport(r);
      setTo(r.suggest);
    }
  }

  async function normalise() {
    const targets = [
      { sheet: sheetA, col: colA },
      { sheet: sheetB, col: colB },
    ].filter((t, i, arr) => arr.findIndex((u) => u.sheet === t.sheet && u.col === t.col) === i);
    const r = await call<{ columns: { changed: number }[]; type: string }>(
      "match.normalize",
      { targets, to, keep_original: keep },
      { label: "Normalising key columns", success: "Key columns normalised · Ctrl+Z to undo" },
    );
    if (r) {
      A.invalidate();
      await check();
      loadLookups();
    }
  }

  function goTo(f: LookupRow) {
    selectSheet(f.sheet);
    setTimeout(() => A.gotoRef(cellRef(f.row, f.col)), 30);
  }

  const colOptions = (sid: string) => {
    const sh = sheets.find((s) => s.id === sid);
    return (sh?.columns ?? []).map((c, i) => (
      <option key={c.id} value={i}>
        {colLetters(i)}
        {c.name ? ` · ${c.name}` : ""}
      </option>
    ));
  };
  const sheetOptions = sheets.map((s) => (
    <option key={s.id} value={s.id}>
      {s.name}
    </option>
  ));
  const flagged = (lookups ?? []).filter((f) => f.fuzzy || f.filter_fuzzy || f.missing);

  return (
    <Shell title="Check key matches" sub="Find values that only fail to match because of their format, and fix them" wide>
      <div className="match-dialog">
        <section>
          <h3>Lookup matching</h3>
          <div className="seg full">
            <button className={mode === "smart" ? "on" : ""} onClick={() => changeMode("smart")}>
              Smart — see through formats
            </button>
            <button className={mode === "strict" ? "on" : ""} onClick={() => changeMode("strict")}>
              Strict — exact formats only
            </button>
          </div>
          <p className="muted small">
            {mode === "smart"
              ? 'VLOOKUP, XLOOKUP, MATCH, COUNTIF/SUMIF and FILTER treat 123, "123", "00123", " 123 ", "1,234" vs 1234 and invisible characters as the same key. Cells matched this way get an amber corner so you can still see them.'
              : "Lookups only match values of the same kind and form. Cells that would have matched with a different format get an amber corner instead of a value, so a blank always means the key is really missing."}
          </p>
        </section>

        <section>
          <h3>Lookups in this workbook</h3>
          {lookups === null ? (
            <p className="muted small">Loading…</p>
          ) : lookups.length === 0 ? (
            <p className="muted small">No VLOOKUP / XLOOKUP / MATCH / FILTER formulas yet.</p>
          ) : (
            <div className="preview-scroll match-table">
              <table>
                <thead>
                  <tr>
                    <th>Formula</th>
                    <th>Exact</th>
                    <th>Format only</th>
                    <th>Not found</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {lookups.map((f) => (
                    <tr key={f.id}>
                      <td>
                        <button className="link" onClick={() => goTo(f)} title="Go to the formula">
                          {f.sheet_name}!{f.ref}
                        </button>{" "}
                        <code>{f.text}</code>
                      </td>
                      <td>
                        <Num n={f.exact} />
                      </td>
                      <td className={f.fuzzy || f.filter_fuzzy ? "warn" : ""}>
                        <Num n={f.fuzzy + (f.filter_fuzzy ?? 0)} />
                      </td>
                      <td>
                        <Num n={f.missing} />
                      </td>
                      <td>
                        <button className="btn ghost sm" onClick={() => pickFormula(f)}>
                          Inspect keys
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {flagged.length > 0 && (
            <p className="muted small">"Not found" counts keys that don't exist in any format — those are clean misses.</p>
          )}
        </section>

        <section>
          <h3>Compare two key columns</h3>
          <div className="match-pick">
            <div className="field">
              <label>Values you look up</label>
              <div className="row-gap">
                <select value={sheetA} onChange={(e) => (setSheetA(e.target.value), setReport(null))}>
                  {sheetOptions}
                </select>
                <select value={colA} onChange={(e) => (setColA(+e.target.value), setReport(null))}>
                  {colOptions(sheetA)}
                </select>
              </div>
            </div>
            <div className="field">
              <label>Where you look them up</label>
              <div className="row-gap">
                <select value={sheetB} onChange={(e) => (setSheetB(e.target.value), setReport(null))}>
                  {sheetOptions}
                </select>
                <select value={colB} onChange={(e) => (setColB(+e.target.value), setReport(null))}>
                  {colOptions(sheetB)}
                </select>
              </div>
            </div>
            <button className="btn primary" onClick={check} disabled={busy}>
              {busy ? "Checking…" : "Check"}
            </button>
          </div>

          {report && (
            <div className="match-report">
              <div className="stat-cards">
                <div className="stat-card ok">
                  <span>Exact matches</span>
                  <b>
                    <Num n={report.exact} />
                  </b>
                </div>
                <div className={`stat-card ${report.fuzzy ? "warn" : "ok"}`}>
                  <span>Match, format differs</span>
                  <b>
                    <Num n={report.fuzzy} />
                  </b>
                </div>
                <div className="stat-card">
                  <span>Really missing</span>
                  <b>
                    <Num n={report.missing} />
                  </b>
                </div>
              </div>

              <div className="match-health">
                {[report.a, report.b].map((h, i) => (
                  <div key={i} className="note">
                    <div>
                      <b>
                        {h.sheet_name}!{colLetters(h.col)}
                        {h.name ? ` (${h.name})` : ""}
                      </b>{" "}
                      is a <b>{h.type}</b> column with <Num n={h.filled} /> values
                      {h.numbers_as_text > 0 && (
                        <>
                          {" "}· <b><Num n={h.numbers_as_text} /></b> numbers stored as text
                        </>
                      )}
                      {h.spaces > 0 && (
                        <>
                          {" "}· <Num n={h.spaces} /> with extra spaces
                        </>
                      )}
                      {h.invisible > 0 && (
                        <>
                          {" "}· <Num n={h.invisible} /> with hidden characters
                        </>
                      )}
                    </div>
                  </div>
                ))}
              </div>

              {report.fuzzy > 0 && (
                <>
                  <div className="chips wrap">
                    {Object.entries(report.reasons)
                      .filter(([, n]) => n > 0)
                      .map(([k, n]) => (
                        <span key={k} className="chip on">
                          {REASONS[k] ?? k}: {n.toLocaleString()}
                        </span>
                      ))}
                  </div>
                  <div className="preview-scroll match-table">
                    <table>
                      <thead>
                        <tr>
                          <th>Row</th>
                          <th>Looked-up value</th>
                          <th>Found as</th>
                          <th>Why it differs</th>
                        </tr>
                      </thead>
                      <tbody>
                        {report.fuzzy_samples.map((x, i) => (
                          <tr key={i}>
                            <td>{x.row + 1}</td>
                            <td>
                              <code>{JSON.stringify(x.value)}</code>
                            </td>
                            <td>
                              <code>{JSON.stringify(x.b_value)}</code> <span className="muted">row {x.b_row + 1}</span>
                            </td>
                            <td>{REASONS[x.reason] ?? x.reason}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </>
              )}

              {report.missing > 0 && (
                <p className="muted small">
                  Really missing (first {report.missing_samples.length}):{" "}
                  {report.missing_samples.map((x) => `${JSON.stringify(x.value)} (row ${x.row + 1})`).join(", ")}
                </p>
              )}

              {(report.fuzzy > 0 || report.a.type !== report.b.type) && (
                <div className="match-fix">
                  <h3>Fix the columns</h3>
                  <p className="muted small">
                    Removes hidden characters, trims and collapses spaces, and stores both columns as the same kind so they match exactly. The value
                    is kept — "00123" becomes the number 123 only when you choose numbers. Undo with {navigator.platform.includes("Mac") ? "⌘" : "Ctrl"}+Z.
                  </p>
                  <div className="seg">
                    <button className={to === "number" ? "on" : ""} onClick={() => setTo("number")} disabled={!(report.a.all_numeric && report.b.all_numeric)}>
                      Numbers{report.suggest === "number" ? " (suggested)" : ""}
                    </button>
                    <button className={to === "text" ? "on" : ""} onClick={() => setTo("text")}>
                      Text{report.suggest === "text" ? " (suggested)" : ""}
                    </button>
                  </div>
                  {to === "text" && (report.reasons.zeros ?? 0) > 0 && (
                    <p className="muted small">
                      {report.reasons.zeros.toLocaleString()} key(s) differ only by leading zeros ("00123" vs 123). As text they keep their zeros, so
                      they still match only with Smart matching
                      {report.a.all_numeric && report.b.all_numeric ? " — choose Numbers to make them exact" : " (one column also holds non-numeric codes)"}.
                    </p>
                  )}
                  <label className="switch">
                    <input type="checkbox" checked={keep} onChange={(e) => setKeep(e.target.checked)} />
                    <span /> Keep a copy of the original values in a new column
                  </label>
                  <button className="btn primary" onClick={normalise}>
                    Normalise both columns
                  </button>
                </div>
              )}
            </div>
          )}
        </section>
      </div>
    </Shell>
  );
}
