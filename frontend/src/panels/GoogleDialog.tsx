import { CheckCircle2, Cloud, KeyRound, Link2, LogIn, RefreshCw, X } from "lucide-react";
import { useEffect, useState } from "react";
import { invalidate } from "../actions";
import { DESKTOP, rpc } from "../api";
import { call, closeDialog, currentSheet, query, selectSheet, toast, useApp } from "../store";

interface GStatus {
  folder: string;
  client_secret: boolean;
  token: boolean;
  service_account: boolean;
  mode: "public" | "oauth" | "service_account";
  account?: string;
}

interface Tab {
  title: string;
  rows: number;
  cols: number;
}

function Account({ status, onChange }: { status: GStatus | null; onChange: (s: GStatus) => void }) {
  const [busy, setBusy] = useState<string | null>(null);
  const [path, setPath] = useState("");
  const pick = async (kind: "folder" | "file") => {
    if (!DESKTOP) return path || null;
    const res = await query<{ paths?: string[]; path?: string }>(kind === "folder" ? "dialog.folder" : "dialog.open", kind === "file" ? { types: ["JSON (*.json)"] } : {});
    return res?.paths?.[0] || res?.path || null;
  };
  const setup = async (field: "folder" | "client_secret" | "service_account") => {
    const p = await pick(field === "folder" ? "folder" : "file");
    if (!p) return toast(DESKTOP ? "Nothing selected" : "Type the path first", "info");
    const s = await query<GStatus>("google.setup", { [field]: p });
    if (s) {
      onChange(s);
      toast("Credentials saved", "success");
    }
  };
  const signIn = async () => {
    setBusy("Complete the sign-in in your browser…");
    try {
      const { result } = await rpc<GStatus>("google.connect");
      onChange(result);
      toast("Connected to Google", "success");
    } catch (e) {
      toast(e instanceof Error ? e.message : String(e), "error");
    } finally {
      setBusy(null);
    }
  };
  return (
    <div className="g-account">
      <div className={`g-status ${status?.mode === "public" ? "warn" : "ok"}`}>
        {status?.mode === "public" ? <Cloud size={18} /> : <CheckCircle2 size={18} />}
        <div>
          <b>{status?.mode === "service_account" ? `Service account${status.account ? ` · ${status.account}` : ""}` : status?.mode === "oauth" ? "Signed in with your Google account" : "Not connected"}</b>
          <span>{status?.mode === "public" ? "Only sheets shared as “anyone with the link” can be read." : "Read-only access to Google Sheets (spreadsheets.readonly)."}</span>
        </div>
      </div>
      {!DESKTOP && <input className="g-path" placeholder="Path on this computer, e.g. C:\PythonScripts\ku_ir_consolidator" value={path} onChange={(e) => setPath(e.target.value)} />}
      <div className="g-actions">
        <button className="g-card" onClick={() => setup("folder")}>
          <KeyRound size={18} />
          <b>Use my existing script folder</b>
          <span>Pick the folder with credentials.json + token.json (e.g. your consolidator). No new sign-in needed.</span>
        </button>
        <button className="g-card" onClick={() => setup("client_secret")}>
          <Link2 size={18} />
          <b>Choose an OAuth client file</b>
          <span>credentials.json from Google Cloud (Desktop app), then sign in.</span>
        </button>
        <button className="g-card" onClick={signIn} disabled={!status?.client_secret || !!busy}>
          <LogIn size={18} />
          <b>Sign in with Google</b>
          <span>{busy || (status?.client_secret ? "Opens your browser once; the token is saved." : "Add credentials.json first.")}</span>
        </button>
        <button className="g-card" onClick={() => setup("service_account")}>
          <KeyRound size={18} />
          <b>Use a service account key</b>
          <span>Share each sheet with the service account e-mail.</span>
        </button>
      </div>
      {status && status.mode !== "public" && (
        <button className="link" onClick={async () => onChange((await query<GStatus>("google.sign_out"))!)}>
          Sign out / forget credentials
        </button>
      )}
      <small className="muted">Stored in {status?.folder}</small>
    </div>
  );
}

function ImportTab() {
  const [url, setUrl] = useState("");
  const [info, setInfo] = useState<{ title: string; tabs: Tab[] } | null>(null);
  const [chosen, setChosen] = useState<Set<string>>(new Set());
  const [manual, setManual] = useState("");
  const [typed, setTyped] = useState(true);
  const load = async () => {
    try {
      const { result } = await rpc<{ title: string; tabs: Tab[] }>("gsheets.tabs", { url });
      setInfo(result);
      setChosen(new Set(result.tabs.slice(0, 1).map((t) => t.title)));
    } catch (e) {
      toast(e instanceof Error ? e.message : String(e), "error");
    }
  };
  const go = async () => {
    const tabs = info ? [...chosen] : manual.split(",").map((s) => s.trim()).filter(Boolean);
    closeDialog();
    const r = await call<{ sheets: string[] }>("gsheets.import", { url, tabs, typed }, { label: "Downloading from Google Sheets" });
    if (r?.sheets?.[0]) {
      selectSheet(r.sheets[0]);
      invalidate();
      toast("Imported — use Refresh to pull new data any time", "success");
    }
  };
  return (
    <div className="g-form">
      <div className="field">
        <label>Spreadsheet link or ID</label>
        <div className="row-gap">
          <input value={url} placeholder="https://docs.google.com/spreadsheets/d/…" onChange={(e) => (setUrl(e.target.value), setInfo(null))} />
          <button className="btn ghost" disabled={!url} onClick={load}>
            Load tabs
          </button>
        </div>
      </div>
      {info ? (
        <div className="field">
          <label>{info.title} — tabs</label>
          <div className="chips wrap">
            {info.tabs.map((t) => (
              <button
                key={t.title}
                className={`chip lg ${chosen.has(t.title) ? "on" : ""}`}
                onClick={() => {
                  const s = new Set(chosen);
                  if (s.has(t.title)) s.delete(t.title);
                  else s.add(t.title);
                  setChosen(s);
                }}
              >
                {t.title} <span className="muted">{t.rows?.toLocaleString()} rows</span>
              </button>
            ))}
          </div>
        </div>
      ) : (
        <div className="field">
          <label>Tab names (comma separated)</label>
          <input value={manual} placeholder="monitoring, ku_report" onChange={(e) => setManual(e.target.value)} />
        </div>
      )}
      <label className="switch">
        <input type="checkbox" checked={typed} onChange={(e) => setTyped(e.target.checked)} />
        <span /> Keep numbers and dates typed (off = text exactly as displayed)
      </label>
      <div className="g-foot">
        <button className="btn primary" disabled={!url || (info ? chosen.size === 0 : !manual.trim())} onClick={go}>
          <Cloud size={15} /> Import as linked sheet
        </button>
      </div>
    </div>
  );
}

function ConsolidateTab() {
  const sheets = useApp((s) => s.info?.sheets || []);
  const [kind, setKind] = useState<"gsheet" | "sheet" | "list">("gsheet");
  const [dirUrl, setDirUrl] = useState("");
  const [dirTab, setDirTab] = useState("app_cluster_directory");
  const [dirSheet, setDirSheet] = useState(currentSheet()?.id || "");
  const [ids, setIds] = useState("");
  const [idCol, setIdCol] = useState("Spreadsheet ID");
  const [urlCol, setUrlCol] = useState("gsheet_datasource_url");
  const [labelCol, setLabelCol] = useState("cluster_id");
  const [tabs, setTabs] = useState("monitoring");
  const [name, setName] = useState("{tab}");
  const [meta, setMeta] = useState(true);
  const [dropBlank, setDropBlank] = useState(true);
  const [typed, setTyped] = useState(false);
  const [workers, setWorkers] = useState(4);

  const go = async () => {
    const directory = kind === "gsheet" ? { kind, url: dirUrl, tab: dirTab } : kind === "sheet" ? { kind, sheet: dirSheet } : { kind, ids: ids.split(/[\n,]+/).map((s) => s.trim()).filter(Boolean) };
    const spec = {
      directory,
      id_column: idCol,
      url_column: urlCol,
      label_column: labelCol,
      tabs: tabs.split(",").map((s) => s.trim()).filter(Boolean),
      name: name || "{tab}",
      add_metadata: meta,
      drop_blank: dropBlank,
      typed,
      workers,
    };
    closeDialog();
    const r = await call<{ sheets: string[]; sources: number; rows: Record<string, number>; failed: number; seconds: number }>("gsheets.consolidate", { spec }, { label: "Consolidating Google Sheets" });
    if (r) {
      if (r.sheets[0]) selectSheet(r.sheets[0]);
      invalidate();
      const total = Object.values(r.rows).reduce((a, b) => a + b, 0);
      toast(`Consolidated ${total.toLocaleString()} rows from ${r.sources} spreadsheets in ${r.seconds}s${r.failed ? ` · ${r.failed} issue(s), see the log tab` : ""}`, r.failed ? "info" : "success");
    }
  };

  return (
    <div className="g-form">
      <div className="field">
        <label>Where is the list of spreadsheets?</label>
        <div className="seg">
          <button className={kind === "gsheet" ? "on" : ""} onClick={() => setKind("gsheet")}>
            A Google Sheet (directory)
          </button>
          <button className={kind === "sheet" ? "on" : ""} onClick={() => setKind("sheet")}>
            A tab in this workbook
          </button>
          <button className={kind === "list" ? "on" : ""} onClick={() => setKind("list")}>
            Paste IDs
          </button>
        </div>
      </div>
      {kind === "gsheet" && (
        <div className="row-gap">
          <input value={dirUrl} placeholder="Directory spreadsheet link" onChange={(e) => setDirUrl(e.target.value)} />
          <input style={{ maxWidth: 220 }} value={dirTab} placeholder="Tab, e.g. app_cluster_directory" onChange={(e) => setDirTab(e.target.value)} />
        </div>
      )}
      {kind === "sheet" && (
        <select value={dirSheet} onChange={(e) => setDirSheet(e.target.value)}>
          {sheets.map((s) => (
            <option key={s.id} value={s.id}>
              {s.name}
            </option>
          ))}
        </select>
      )}
      {kind === "list" && <textarea className="sql-box tall" placeholder={"One spreadsheet link or ID per line"} value={ids} onChange={(e) => setIds(e.target.value)} />}
      {kind !== "list" && (
        <div className="g-cols">
          <div className="field">
            <label>ID column</label>
            <input value={idCol} onChange={(e) => setIdCol(e.target.value)} />
          </div>
          <div className="field">
            <label>Link column (fallback)</label>
            <input value={urlCol} onChange={(e) => setUrlCol(e.target.value)} />
          </div>
          <div className="field">
            <label>Label column</label>
            <input value={labelCol} onChange={(e) => setLabelCol(e.target.value)} />
          </div>
        </div>
      )}
      <div className="g-cols">
        <div className="field">
          <label>Tabs to pull from each spreadsheet</label>
          <input value={tabs} placeholder="monitoring, ku_report, pt_tracker" onChange={(e) => setTabs(e.target.value)} />
        </div>
        <div className="field">
          <label>Result sheet name</label>
          <input value={name} onChange={(e) => setName(e.target.value)} />
        </div>
        <div className="field">
          <label>Parallel downloads</label>
          <select value={workers} onChange={(e) => setWorkers(+e.target.value)}>
            {[1, 2, 4, 6, 8].map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </select>
        </div>
      </div>
      <div className="opt-col">
        <label className="switch">
          <input type="checkbox" checked={meta} onChange={(e) => setMeta(e.target.checked)} />
          <span /> Add source columns (_source_label, _source_spreadsheet_id, _source_row_number…)
        </label>
        <label className="switch">
          <input type="checkbox" checked={dropBlank} onChange={(e) => setDropBlank(e.target.checked)} />
          <span /> Skip completely blank rows
        </label>
        <label className="switch">
          <input type="checkbox" checked={typed} onChange={(e) => setTyped(e.target.checked)} />
          <span /> Keep numbers/dates typed (off = text exactly as displayed, like FORMATTED_VALUE)
        </label>
      </div>
      <div className="note">
        <RefreshCw size={14} /> Headers are matched ignoring case, spaces and line breaks. A log tab lists rows, missing tabs and column differences per spreadsheet. Press Refresh later to pull the latest data — new IDs added to the list are included.
      </div>
      <div className="g-foot">
        <button className="btn primary" disabled={!tabs.trim() || (kind === "gsheet" ? !dirUrl : kind === "list" ? !ids.trim() : !dirSheet)} onClick={go}>
          <Cloud size={15} /> Consolidate
        </button>
      </div>
    </div>
  );
}

export function GoogleDialog() {
  const props = useApp((s) => s.dialogProps) as { tab?: "import" | "consolidate" | "account" };
  const [tab, setTab] = useState(props.tab || "consolidate");
  const [status, setStatus] = useState<GStatus | null>(null);
  useEffect(() => {
    rpc<GStatus>("google.status").then(({ result }) => {
      setStatus(result);
      if (result.mode === "public" && !props.tab) setTab("account");
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && closeDialog();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  return (
    <div className="overlay fade-in" onMouseDown={closeDialog}>
      <div className="dialog wide scale-in" onMouseDown={(e) => e.stopPropagation()}>
        <div className="dialog-head">
          <div>
            <h2>Google Sheets</h2>
            <p>Linked data you can refresh any time</p>
          </div>
          <button className="icon-btn" onClick={closeDialog}>
            <X size={18} />
          </button>
        </div>
        <div className="dialog-body">
          <div className="seg full">
            <button className={tab === "consolidate" ? "on" : ""} onClick={() => setTab("consolidate")}>
              Consolidate many sheets
            </button>
            <button className={tab === "import" ? "on" : ""} onClick={() => setTab("import")}>
              Import one sheet
            </button>
            <button className={tab === "account" ? "on" : ""} onClick={() => setTab("account")}>
              Account {status && status.mode !== "public" ? "✓" : ""}
            </button>
          </div>
          {tab === "account" && <Account status={status} onChange={setStatus} />}
          {tab === "import" && <ImportTab />}
          {tab === "consolidate" && <ConsolidateTab />}
        </div>
      </div>
    </div>
  );
}
