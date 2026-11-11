import { Database, FileSpreadsheet, FileText, FileJson, Boxes, Upload, X, Table2, Braces, HardDrive, Server, Sheet } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import * as A from "../actions";
import { DESKTOP, downloadUrl, rpc, upload } from "../api";
import { triggerDownload } from "../fileops";
import { call, closeDialog, currentSheet, query, selectSheet, toast, useApp } from "../store";
import { MOD } from "../shortcuts";
import { AboutDialog } from "./About";
import { DocsDialog } from "./Docs";
import { GoogleDialog } from "./GoogleDialog";
import { LinkDialog } from "./LinkDialog";

export function Shell({ title, sub, children, footer, wide }: { title: string; sub?: string; children: React.ReactNode; footer?: React.ReactNode; wide?: boolean }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && closeDialog();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  return (
    <div className="overlay fade-in" onMouseDown={closeDialog}>
      <div className={`dialog scale-in ${wide ? "wide" : ""}`} onMouseDown={(e) => e.stopPropagation()}>
        <div className="dialog-head">
          <div>
            <h2>{title}</h2>
            {sub && <p>{sub}</p>}
          </div>
          <button className="icon-btn" onClick={closeDialog}>
            <X size={18} />
          </button>
        </div>
        <div className="dialog-body">{children}</div>
        {footer && <div className="dialog-foot">{footer}</div>}
      </div>
    </div>
  );
}

interface Preview {
  kind: string;
  path: string;
  name: string;
  size: number;
  tables: { name: string; columns: string[]; rows: unknown[][]; total?: number | null }[];
  sniff?: { delimiter?: string; header?: boolean };
}

function humanSize(n: number) {
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) {
    n /= 1024;
    i++;
  }
  return `${n.toFixed(n < 10 && i ? 1 : 0)} ${u[i]}`;
}

export function ImportDialog() {
  const props = useApp((s) => s.dialogProps) as { paths?: string[] };
  const [paths, setPaths] = useState<string[]>(props.paths || []);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [uploading, setUploading] = useState<number | null>(null);
  const [header, setHeader] = useState<boolean | null>(null);
  const [delim, setDelim] = useState("");
  const [tables, setTables] = useState<Set<string>>(new Set());
  const [sql, setSql] = useState("");
  const [drag, setDrag] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!paths.length) return;
    (async () => {
      const p = await query<Preview>("io.preview", { path: paths[0], options: delim ? { delimiter: delim } : {} });
      if (!p) return;
      setPreview(p);
      if (header === null && p.sniff?.header !== undefined) setHeader(p.sniff.header);
      if (p.kind === "excel") setTables(new Set(p.tables.map((t) => t.name)));
      if (p.kind === "sqlite" || p.kind === "duckdb") setTables(new Set(p.tables.slice(0, 1).map((t) => t.name)));
    })();
  }, [paths, delim]);

  const pickFiles = async (files: FileList | File[]) => {
    const list = Array.from(files);
    if (!list.length) return;
    const out: string[] = [];
    for (const f of list) {
      setUploading(0);
      try {
        out.push(await upload(f, (x) => setUploading(x)));
      } catch (e) {
        toast(String(e), "error");
      }
    }
    setUploading(null);
    setPaths(out);
  };

  const chooseNative = async () => {
    const res = await query<{ paths?: string[]; unsupported?: boolean }>("dialog.open", { multiple: true });
    if (res?.unsupported) fileRef.current?.click();
    else if (res?.paths?.length) setPaths(res.paths);
  };

  const doImport = async () => {
    if (!paths.length) return;
    closeDialog();
    let first: string | undefined;
    for (const [i, path] of paths.entries()) {
      if (path.toLowerCase().endsWith(".pysheets")) {
        await call("workbook.open", { path }, { label: "Opening workbook" });
        useApp.setState({ perSheet: {} });
        A.invalidate();
        return;
      }
      const options: Record<string, unknown> = {};
      if (i === 0 && preview) {
        if (preview.kind === "csv") {
          if (header !== null) options.header = header;
          if (delim) options.delimiter = delim;
        }
        if (preview.kind === "excel") {
          options.sheets = [...tables];
          if (header !== null) options.header = header;
        }
        if (preview.kind === "sqlite" || preview.kind === "duckdb") {
          if (sql.trim()) options.query = sql;
          else options.tables = [...tables];
        }
      }
      const r = await call<{ sheets: string[] }>("io.import", { path, options }, { label: `Importing ${path.split(/[\\/]/).pop()}` });
      if (r?.sheets?.length && !first) first = r.sheets[0];
    }
    if (first) {
      selectSheet(first);
      const sh = useApp.getState().info?.sheets.find((s) => s.id === first);
      if (sh) toast(`Imported ${sh.nrows.toLocaleString()} rows × ${sh.ncols} columns`, "success");
    }
  };

  const t0 = preview?.tables[0];
  const isDb = preview?.kind === "sqlite" || preview?.kind === "duckdb";
  return (
    <Shell
      title="Import data"
      sub="CSV, TSV, Excel, Parquet, JSON, SQLite, DuckDB or a PySheets workbook"
      wide
      footer={
        <>
          <span className="foot-note">{preview ? `${preview.name} · ${humanSize(preview.size)}` : ""}</span>
          <div className="tb-flex" />
          <button className="btn ghost" onClick={closeDialog}>
            Cancel
          </button>
          <button className="btn primary" disabled={!paths.length || uploading !== null} onClick={doImport}>
            Import{paths.length > 1 ? ` ${paths.length} files` : ""}
          </button>
        </>
      }
    >
      <input ref={fileRef} type="file" multiple hidden onChange={(e) => e.target.files && pickFiles(e.target.files)} />
      {!paths.length ? (
        <div
          className={`dropzone ${drag ? "drag" : ""}`}
          onDragOver={(e) => {
            e.preventDefault();
            setDrag(true);
          }}
          onDragLeave={() => setDrag(false)}
          onDrop={(e) => {
            e.preventDefault();
            setDrag(false);
            pickFiles(e.dataTransfer.files);
          }}
          onClick={() => (DESKTOP ? chooseNative() : fileRef.current?.click())}
        >
          {uploading !== null ? (
            <>
              <div className="progress">
                <div style={{ width: `${Math.round(uploading * 100)}%` }} />
              </div>
              <p>Uploading… {Math.round(uploading * 100)}%</p>
            </>
          ) : (
            <>
              <div className="dz-icon">
                <Upload size={26} />
              </div>
              <p className="dz-title">Drop files here or click to browse</p>
              <p className="dz-sub">Millions of rows are fine — data is processed locally with DuckDB</p>
              <div className="dz-formats">
                {["CSV", "TSV", "XLSX", "XLS", "Parquet", "JSON", "SQLite", "DuckDB"].map((f) => (
                  <span key={f}>{f}</span>
                ))}
              </div>
            </>
          )}
        </div>
      ) : !preview ? (
        <div className="loading-block">
          <span className="mini-spin" /> Reading file…
        </div>
      ) : (
        <div className="import-body">
          {paths.length > 1 && <div className="note">{paths.length} files selected — options apply to the first one; others import with defaults.</div>}
          {preview.kind === "csv" && (
            <div className="opt-row">
              <label className="switch">
                <input type="checkbox" checked={header ?? true} onChange={(e) => setHeader(e.target.checked)} />
                <span /> First row is a header
              </label>
              <label className="field-inline">
                Delimiter
                <select value={delim} onChange={(e) => setDelim(e.target.value)}>
                  <option value="">Auto-detect {preview.sniff?.delimiter ? `(${preview.sniff.delimiter === "\t" ? "tab" : preview.sniff.delimiter})` : ""}</option>
                  <option value=",">Comma</option>
                  <option value=";">Semicolon</option>
                  <option value={"\t"}>Tab</option>
                  <option value="|">Pipe</option>
                </select>
              </label>
            </div>
          )}
          {(preview.kind === "excel" || isDb) && (
            <div className="table-pick">
              <div className="label">{isDb ? "Tables" : "Worksheets"}</div>
              <div className="chips">
                {preview.tables.map((t) => (
                  <button
                    key={t.name}
                    className={`chip lg ${tables.has(t.name) ? "on" : ""}`}
                    onClick={() => {
                      const s = new Set(tables);
                      if (s.has(t.name)) s.delete(t.name);
                      else s.add(t.name);
                      setTables(s);
                    }}
                  >
                    {isDb ? <Table2 size={13} /> : <Sheet size={13} />} {t.name}
                  </button>
                ))}
              </div>
              {preview.kind === "excel" && (
                <label className="switch">
                  <input type="checkbox" checked={header ?? true} onChange={(e) => setHeader(e.target.checked)} />
                  <span /> First row is a header
                </label>
              )}
              {isDb && (
                <>
                  <div className="label">…or run a SQL query</div>
                  <textarea className="sql-box" placeholder="SELECT * FROM orders WHERE total > 100" value={sql} onChange={(e) => setSql(e.target.value)} />
                </>
              )}
            </div>
          )}
          {t0 && t0.rows.length > 0 && (
            <div className="preview">
              <div className="label">
                Preview{t0.total ? ` · ${t0.total.toLocaleString()} rows` : ""}
                {preview.kind === "excel" ? ` · ${t0.name}` : ""}
              </div>
              <div className="preview-scroll">
                <table>
                  {t0.columns.length > 0 && (
                    <thead>
                      <tr>
                        {t0.columns.map((c, i) => (
                          <th key={i}>{c}</th>
                        ))}
                      </tr>
                    </thead>
                  )}
                  <tbody>
                    {t0.rows.slice(0, 12).map((r, i) => (
                      <tr key={i} className={i === 0 && preview.kind === "excel" && (header ?? true) ? "hdr" : ""}>
                        {r.map((v, j) => (
                          <td key={j}>{v === null ? "" : String(v)}</td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
          <button className="link" onClick={() => (setPaths([]), setPreview(null))}>
            Choose a different file
          </button>
        </div>
      )}
    </Shell>
  );
}

const FORMATS = [
  { id: "xlsx", label: "Excel", ext: "xlsx", icon: FileSpreadsheet, desc: "Formatted workbook, splits sheets over 1,048,576 rows" },
  { id: "csv", label: "CSV", ext: "csv", icon: FileText, desc: "Comma-separated text" },
  { id: "tsv", label: "TSV", ext: "tsv", icon: FileText, desc: "Tab-separated text" },
  { id: "parquet", label: "Parquet", ext: "parquet", icon: Boxes, desc: "Compressed columnar, best for big data" },
  { id: "json", label: "JSON", ext: "json", icon: FileJson, desc: "Array of row objects" },
  { id: "sqlite", label: "SQLite", ext: "sqlite", icon: Database, desc: "One table per sheet" },
  { id: "duckdb", label: "DuckDB", ext: "duckdb", icon: HardDrive, desc: "One table per sheet" },
  { id: "sql", label: "SQL script", ext: "sql", icon: Braces, desc: "CREATE TABLE + INSERT statements" },
  { id: "db", label: "Database server", ext: "", icon: Server, desc: "PostgreSQL, MySQL, SQL Server… via URL" },
  { id: "pysheets", label: "PySheets", ext: "pysheets", icon: Sheet, desc: "Full workbook with formulas & formats" },
];

export function ExportDialog() {
  const props = useApp((s) => s.dialogProps) as { scope?: "sheet" | "all" };
  const sheet = currentSheet();
  const nSheets = useApp((s) => s.info?.sheets.length ?? 1);
  const [fmt, setFmt] = useState("xlsx");
  const [scope, setScope] = useState<"sheet" | "all">(props.scope || "sheet");
  const [visible, setVisible] = useState(false);
  const [url, setUrl] = useState("");
  const f = FORMATS.find((x) => x.id === fmt)!;

  const run = async () => {
    if (!sheet) return;
    if (fmt === "db") {
      if (!url.trim()) return toast("Enter a database URL", "error");
      closeDialog();
      const r = await call<{ tables: string[] }>("io.sql_export", { url, scope, sheet: sheet.id }, { label: "Writing to database" });
      if (r) toast(`Wrote ${r.tables.join(", ")}`, "success");
      return;
    }
    const base = (scope === "all" ? useApp.getState().info?.title : sheet.name) || "export";
    const name = `${base.replace(/[\\/:*?"<>|]+/g, "_")}.${f.ext}`;
    let path: string | null = null;
    if (DESKTOP) {
      const res = await query<{ path?: string | null }>("dialog.save", { name, types: [`${f.label} (*.${f.ext})`] });
      path = res?.path || null;
      if (!path) return;
    } else {
      const res = await rpc<{ path: string }>("io.temp_path", { name });
      path = res.result.path;
    }
    closeDialog();
    const r = await call<{ files: string[] }>("io.export", { path, format: fmt, scope, sheet: sheet.id, options: { visible_only: visible } }, { label: `Exporting ${f.label}` });
    if (!r) return;
    if (!DESKTOP) for (const file of r.files) triggerDownload(downloadUrl(file));
    toast(r.files.length > 1 ? `Exported ${r.files.length} files` : `Exported ${r.files[0].split(/[\\/]/).pop()}`, "success");
  };

  return (
    <Shell
      title="Export"
      sub={scope === "all" ? `All ${nSheets} tabs` : `Tab “${sheet?.name}” · ${sheet?.nrows.toLocaleString()} rows`}
      wide
      footer={
        <>
          <div className="seg">
            <button className={scope === "sheet" ? "on" : ""} onClick={() => setScope("sheet")}>
              This tab
            </button>
            <button className={scope === "all" ? "on" : ""} onClick={() => setScope("all")}>
              All tabs
            </button>
          </div>
          {sheet?.filtered && scope === "sheet" && (
            <label className="switch">
              <input type="checkbox" checked={visible} onChange={(e) => setVisible(e.target.checked)} />
              <span /> Only filtered rows
            </label>
          )}
          <div className="tb-flex" />
          <button className="btn ghost" onClick={closeDialog}>
            Cancel
          </button>
          <button className="btn primary" onClick={run}>
            Export {f.label}
          </button>
        </>
      }
    >
      <div className="format-grid">
        {FORMATS.map((x) => (
          <button key={x.id} className={`format-card ${fmt === x.id ? "on" : ""}`} onClick={() => setFmt(x.id)}>
            <x.icon size={22} />
            <div>
              <b>{x.label}</b>
              <span>{x.desc}</span>
            </div>
          </button>
        ))}
      </div>
      {fmt === "db" && (
        <div className="field">
          <label>SQLAlchemy URL</label>
          <input placeholder="postgresql+psycopg://user:pass@host:5432/db" value={url} onChange={(e) => setUrl(e.target.value)} />
          <small>Tables are created (or replaced) with one table per tab. Requires the matching database driver.</small>
        </div>
      )}
      {fmt === "csv" && scope === "all" && <div className="note">Each tab is written to its own file next to the one you choose.</div>}
    </Shell>
  );
}

export function SqlDialog() {
  const [url, setUrl] = useState("");
  const [sql, setSql] = useState("");
  const [name, setName] = useState("Query");
  const [tables, setTables] = useState<string[] | null>(null);
  const list = async () => {
    const t = await query<string[]>("io.sql_tables", { url });
    if (t) setTables(t);
  };
  const run = async () => {
    closeDialog();
    const r = await call<{ sheets: string[] }>("io.sql_import", { url, query: sql, name }, { label: "Running query" });
    if (r?.sheets?.[0]) selectSheet(r.sheets[0]);
  };
  return (
    <Shell
      title="Import from a database"
      sub="Any SQLAlchemy URL, or a path to a SQLite / DuckDB file"
      wide
      footer={
        <>
          <div className="tb-flex" />
          <button className="btn ghost" onClick={closeDialog}>
            Cancel
          </button>
          <button className="btn primary" disabled={!url || !sql} onClick={run}>
            Run & import
          </button>
        </>
      }
    >
      <div className="field">
        <label>Connection</label>
        <div className="row-gap">
          <input placeholder="postgresql+psycopg://user:pass@localhost/db   or   C:\data\app.sqlite" value={url} onChange={(e) => setUrl(e.target.value)} />
          <button className="btn ghost" disabled={!url} onClick={list}>
            List tables
          </button>
        </div>
      </div>
      {tables && (
        <div className="chips wrap">
          {tables.map((t) => (
            <button key={t} className="chip lg" onClick={() => (setSql(`SELECT * FROM ${t}`), setName(t))}>
              <Table2 size={13} /> {t}
            </button>
          ))}
        </div>
      )}
      <div className="field">
        <label>SQL query</label>
        <textarea className="sql-box tall" placeholder="SELECT region, SUM(amount) FROM sales GROUP BY 1" value={sql} onChange={(e) => setSql(e.target.value)} />
      </div>
      <div className="field">
        <label>Sheet name</label>
        <input value={name} onChange={(e) => setName(e.target.value)} />
      </div>
    </Shell>
  );
}

export function SplitDialog() {
  const props = useApp((s) => s.dialogProps) as { col?: number };
  const [d, setD] = useState(",");
  const sh = currentSheet();
  const col = props.col ?? 0;
  return (
    <Shell
      title="Split text to columns"
      sub={`Column ${sh?.columns[col]?.name || col + 1}`}
      footer={
        <>
          <div className="tb-flex" />
          <button className="btn ghost" onClick={closeDialog}>
            Cancel
          </button>
          <button
            className="btn primary"
            onClick={async () => {
              closeDialog();
              await call("split.column", { sheet: sh?.id, col, delimiter: d === "space" ? "" : d });
              A.invalidate();
            }}
          >
            Split
          </button>
        </>
      }
    >
      <div className="chips">
        {[
          [",", "Comma"],
          [";", "Semicolon"],
          [".", "Period"],
          ["space", "Whitespace"],
          ["|", "Pipe"],
          ["-", "Dash"],
        ].map(([v, l]) => (
          <button key={v} className={`chip lg ${d === v ? "on" : ""}`} onClick={() => setD(v)}>
            {l}
          </button>
        ))}
      </div>
      <div className="field">
        <label>Custom separator</label>
        <input value={d === "space" ? "" : d} onChange={(e) => setD(e.target.value)} />
      </div>
    </Shell>
  );
}

export const SHORTCUTS: [string, string][] = [
  ["Edit cell", "Enter / F2 / start typing"],
  ["Commit and fill whole column", `${MOD}+Enter`],
  ["New line in cell", "Alt+Enter"],
  ["Fill down", `${MOD}+D`],
  ["Undo / redo", `${MOD}+Z / ${MOD}+Y`],
  ["Copy / cut / paste", `${MOD}+C / ${MOD}+X / ${MOD}+V`],
  ["Bold / italic / underline", `${MOD}+B / ${MOD}+I / ${MOD}+U`],
  ["Find / replace", `${MOD}+F / ${MOD}+H`],
  ["Command palette", `${MOD}+K`],
  ["Toggle filter", `${MOD}+Shift+L`],
  ["Select all / column / row", `${MOD}+A / ${MOD}+Space / Shift+Space`],
  ["Jump to edge of data", `${MOD}+Arrow`],
  ["Extend selection", "Shift+Arrow"],
  ["Next / previous sheet", `${MOD}+PgDn / ${MOD}+PgUp`],
  ["Insert today's date", `${MOD}+;`],
  ["Open / import", `${MOD}+O`],
  ["Save / save as", `${MOD}+S / ${MOD}+Shift+S`],
  ["Export", `${MOD}+Shift+E`],
  ["Clear contents", "Delete / Backspace"],
];

export function ShortcutsDialog() {
  return (
    <Shell title="Keyboard shortcuts">
      <div className="shortcut-list">
        {SHORTCUTS.map(([a, b]) => (
          <div key={a} className="shortcut">
            <span>{a}</span>
            <kbd>{b}</kbd>
          </div>
        ))}
      </div>
    </Shell>
  );
}

export function Dialogs() {
  const d = useApp((s) => s.dialog);
  if (d === "import") return <ImportDialog />;
  if (d === "export") return <ExportDialog />;
  if (d === "sql") return <SqlDialog />;
  if (d === "shortcuts") return <ShortcutsDialog />;
  if (d === "about") return <AboutDialog />;
  if (d === "docs") return <DocsDialog />;
  if (d === "link") return <LinkDialog />;
  if (d === "split") return <SplitDialog />;
  if (d === "google") return <GoogleDialog />;
  return null;
}
