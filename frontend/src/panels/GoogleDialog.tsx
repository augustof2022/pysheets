import { Cloud, FileKey2, FileSpreadsheet, FolderKey, KeyRound, LogOut, RefreshCw, ShieldCheck, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { invalidate } from "../actions";
import { DESKTOP, rpc, RpcError } from "../api";
import { call, closeDialog, currentSheet, query, selectSheet, toast, useApp } from "../store";

interface GStatus {
  folder: string;
  signed_in: boolean;
  email: string | null;
  method: "oauth" | "token" | "service_account" | null;
  has_client: boolean;
  client_source: "file" | "bundled" | null;
  expired?: string;
  warning?: string;
  sign_in: { pending: boolean; auth_url: string | null; error: string | null; email: string | null };
}

interface Tab {
  title: string;
  rows: number;
  cols: number;
}

function GoogleG({ size = 18 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 48 48" aria-hidden="true">
      <path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z" />
      <path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z" />
      <path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z" />
      <path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z" />
    </svg>
  );
}

async function pickPath(kind: "folder" | "file", typed: string): Promise<string | null> {
  if (!DESKTOP) return typed.trim() || null;
  const res = await query<{ paths?: string[]; path?: string }>(kind === "folder" ? "dialog.folder" : "dialog.open", kind === "file" ? { types: ["JSON (*.json)"] } : {});
  return res?.paths?.[0] || res?.path || null;
}

/** Shown until the user is signed in: nothing Google-related is available before that. */
function SignInGate({ status, reason, onStatus }: { status: GStatus; reason?: string; onStatus: (s: GStatus) => void }) {
  const [more, setMore] = useState(false);
  const [path, setPath] = useState("");
  const [working, setWorking] = useState(false);
  const pending = status.sign_in.pending;
  const error = status.sign_in.error;

  // watch for the sign-in to finish: fast while the browser flow runs, slowly otherwise
  // (and whenever the window regains focus, e.g. coming back from the browser)
  useEffect(() => {
    const check = async () => {
      try {
        onStatus((await rpc<GStatus>("google.status")).result);
      } catch {
        /* keep polling */
      }
    };
    const t = window.setInterval(check, pending ? 700 : 2500);
    window.addEventListener("focus", check);
    return () => {
      window.clearInterval(t);
      window.removeEventListener("focus", check);
    };
  }, [pending, onStatus]);

  const run = async (method: string, params: Record<string, unknown> = {}) => {
    setWorking(true);
    try {
      const { result } = await rpc<GStatus>(method, params);
      onStatus(result);
      return result;
    } catch (e) {
      toast(e instanceof Error ? e.message : String(e), "error");
      return null;
    } finally {
      setWorking(false);
    }
  };
  const signIn = () => run("google.sign_in");
  const setup = async (field: "client_secret" | "folder" | "service_account") => {
    const p = await pickPath(field === "folder" ? "folder" : "file", path);
    if (!p) return toast(DESKTOP ? "Nothing selected" : "Type the path on this computer first", "info");
    const s = await run("google.setup", { [field]: p });
    // a new OAuth client goes straight on to the browser sign-in
    if (s && field === "client_secret" && !s.signed_in) await run("google.sign_in");
  };

  return (
    <div className="g-gate pop-in">
      <div className="g-gate-icon">
        <FileSpreadsheet size={28} />
      </div>
      <h3>Sign in to Google Sheets</h3>
      <p>{reason && reason.toLowerCase().includes("expired") ? reason : "Connect your Google account to import, consolidate and refresh spreadsheets. PySheets only asks for read-only access."}</p>
      {status.expired && <div className="g-error">{status.expired}</div>}

      {!DESKTOP && (
        <input className="g-path" placeholder="Path on this computer (for the options below), e.g. C:\PythonScripts\ku_ir_consolidator\credentials.json" value={path} onChange={(e) => setPath(e.target.value)} />
      )}

      {pending ? (
        <div className="g-wait fade-in">
          <span className="mini-spin lg" />
          <b>Finish signing in in your browser…</b>
          <span>Choose your Google account and allow access. PySheets continues automatically once you're done.</span>
          <div className="g-wait-actions">
            <button className="link" onClick={() => run("google.sign_in_reopen")}>
              Open the sign-in page again
            </button>
            <span className="muted">·</span>
            <button className="link" onClick={() => run("google.sign_in_cancel")}>
              Cancel
            </button>
          </div>
        </div>
      ) : status.has_client ? (
        <button className="g-signin-btn" onClick={signIn} disabled={working}>
          <GoogleG /> Sign in with Google
        </button>
      ) : (
        <div className="g-setup">
          <div className="g-step">
            <span className="g-step-n">1</span>
            <div>
              <b>Choose your OAuth client file</b>
              <span>The credentials.json from Google Cloud (Desktop app). You can reuse the one your Python scripts already use.</span>
            </div>
            <button className="btn primary" onClick={() => setup("client_secret")} disabled={working}>
              <FileKey2 size={15} /> Choose…
            </button>
          </div>
          <div className="g-step off">
            <span className="g-step-n">2</span>
            <div>
              <b>Sign in with Google</b>
              <span>Your browser opens. Pick your account and allow read-only access.</span>
            </div>
          </div>
          <details className="g-help">
            <summary>How do I get a credentials.json?</summary>
            <ol>
              <li>Open Google Cloud Console → APIs &amp; Services, and enable the Google Sheets API.</li>
              <li>Credentials → Create credentials → OAuth client ID → Application type: Desktop app.</li>
              <li>Download the JSON and choose it here. It stays on this computer.</li>
            </ol>
          </details>
        </div>
      )}
      {error && !pending && <div className="g-error fade-in">{error}</div>}

      <div className="g-more">
        <button className="link" onClick={() => setMore(!more)}>
          {more ? "Hide other ways to sign in" : "Other ways to sign in"}
        </button>
        {more && (
          <div className="g-actions fade-in">
            <button className="g-card" onClick={() => setup("folder")} disabled={working}>
              <FolderKey size={18} />
              <b>Use my existing script folder</b>
              <span>Pick the folder with token.json (and credentials.json), e.g. your consolidator. Signs in with that saved login.</span>
            </button>
            <button className="g-card" onClick={() => setup("service_account")} disabled={working}>
              <KeyRound size={18} />
              <b>Use a service account key</b>
              <span>A service-account JSON key. Share each sheet with its e-mail.</span>
            </button>
            {status.has_client && (
              <button className="g-card" onClick={() => setup("client_secret")} disabled={working}>
                <FileKey2 size={18} />
                <b>Change OAuth client file</b>
                <span>{status.client_source === "bundled" ? "Use your own credentials.json instead of the built-in one." : "Choose a different credentials.json."}</span>
              </button>
            )}
          </div>
        )}
      </div>
      <small className="muted g-foot-note">
        <ShieldCheck size={13} /> Read-only access · your login stays on this computer ({status.folder})
      </small>
    </div>
  );
}

function AccountChip({ status, onSignOut }: { status: GStatus; onSignOut: () => void }) {
  const who = status.email || (status.method === "token" ? "Saved login from your scripts" : "Google account");
  return (
    <div className="g-chip fade-in" title={status.method === "service_account" ? "Service account" : "Signed in"}>
      <span className="g-avatar">{(status.email || "G")[0].toUpperCase()}</span>
      <span className="g-who">{who}</span>
      <button className="icon-btn sm" data-tip="Sign out" onClick={onSignOut}>
        <LogOut size={14} />
      </button>
    </div>
  );
}

function ImportTab({ onAuthLost }: { onAuthLost: () => void }) {
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
      if (e instanceof RpcError && e.code === "google_auth") onAuthLost();
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
  const props = useApp((s) => s.dialogProps) as { tab?: "import" | "consolidate" | "account"; reason?: string; retry?: () => void; cancel?: () => void };
  const [tab, setTab] = useState<"import" | "consolidate">(props.tab === "import" ? "import" : "consolidate");
  const [status, setStatus] = useState<GStatus | null>(null);
  const finished = useRef(false);
  const pendingRef = useRef(false);
  pendingRef.current = !!status?.sign_in.pending;
  const wasSignedIn = useRef<boolean | null>(null);

  useEffect(() => {
    rpc<GStatus>("google.status", { verify: true })
      .then(({ result }) => setStatus(result))
      .catch((e) => toast(e instanceof Error ? e.message : String(e), "error"));
    return () => {
      if (pendingRef.current) rpc("google.sign_in_cancel").catch(() => undefined);
      if (!finished.current) props.cancel?.();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // the moment sign-in completes: continue what the user was doing
  useEffect(() => {
    if (!status) return;
    const before = wasSignedIn.current;
    wasSignedIn.current = status.signed_in;
    if (status.signed_in && before !== true) {
      if (before === false) toast(`Signed in${status.email ? ` as ${status.email}` : ""}`, "success");
      if (props.retry) {
        finished.current = true;
        const retry = props.retry;
        closeDialog();
        retry();
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && closeDialog();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const signOut = async () => {
    const s = await query<GStatus>("google.sign_out");
    if (s) {
      setStatus(s);
      toast("Signed out of Google");
    }
  };

  return (
    <div className="overlay fade-in" onMouseDown={closeDialog}>
      <div className="dialog wide scale-in" onMouseDown={(e) => e.stopPropagation()}>
        <div className="dialog-head">
          <div>
            <h2>Google Sheets</h2>
            <p>{status?.signed_in ? "Linked data you can refresh any time" : "Sign in to continue"}</p>
          </div>
          <div className="row-gap">
            {status?.signed_in && <AccountChip status={status} onSignOut={signOut} />}
            <button className="icon-btn" onClick={closeDialog}>
              <X size={18} />
            </button>
          </div>
        </div>
        <div className="dialog-body">
          {!status ? (
            <div className="g-checking">
              <span className="mini-spin" /> Checking your Google sign-in…
            </div>
          ) : !status.signed_in ? (
            <SignInGate status={status} reason={props.reason} onStatus={setStatus} />
          ) : props.retry ? (
            <div className="g-checking">
              <span className="mini-spin" /> Continuing…
            </div>
          ) : (
            <div className="g-main fade-in">
              {status.warning && <div className="g-error">{status.warning}</div>}
              <div className="seg full">
                <button className={tab === "consolidate" ? "on" : ""} onClick={() => setTab("consolidate")}>
                  Consolidate many sheets
                </button>
                <button className={tab === "import" ? "on" : ""} onClick={() => setTab("import")}>
                  Import one sheet
                </button>
              </div>
              {tab === "import" && <ImportTab onAuthLost={() => rpc<GStatus>("google.status").then(({ result }) => setStatus(result))} />}
              {tab === "consolidate" && <ConsolidateTab />}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
