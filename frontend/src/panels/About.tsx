import { ArrowUpCircle, BookOpen, CheckCircle2, Download, ExternalLink, RefreshCw, TriangleAlert } from "lucide-react";
import { useEffect, useState } from "react";
import { rpc } from "../api";
import { saveWorkbook } from "../fileops";
import { openDialog, toast, useApp } from "../store";
import { Shell } from "./Dialogs";

interface Asset {
  name: string;
  url: string;
  size: number | null;
}
interface CheckResult {
  current: string;
  latest: string;
  update_available: boolean;
  published_at: string | null;
  notes: string;
  page: string;
  installer: Asset | null;
  install_kind: "installer" | "portable" | "source";
  platform: string;
}
interface DownloadState {
  state: "idle" | "downloading" | "verifying" | "ready" | "installing" | "error";
  done: number;
  total: number | null;
  error: string | null;
  version: string | null;
}
type Phase = { kind: "idle" } | { kind: "checking" } | { kind: "error"; message: string } | { kind: "result"; r: CheckResult };

function Logo() {
  return (
    <svg viewBox="0 0 32 32" width="52" height="52" aria-hidden className="about-logo">
      <defs>
        <linearGradient id="about-lg" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="var(--accent)" />
          <stop offset="1" stopColor="var(--accent-2)" />
        </linearGradient>
      </defs>
      <rect width="32" height="32" rx="7.5" fill="url(#about-lg)" />
      <rect x="6.5" y="7.5" width="19" height="17" rx="2.4" fill="none" stroke="#fff" strokeWidth="1.6" />
      <path d="M6.5 9.9a2.4 2.4 0 0 1 2.4-2.4h14.2a2.4 2.4 0 0 1 2.4 2.4v3.4h-19z" fill="#fff" />
      <path d="M6.5 19h19M14.1 13.3v11.2" stroke="#fff" strokeWidth="1.6" />
    </svg>
  );
}

const mb = (n: number) => `${(n / 1048576).toFixed(1)} MB`;
const err = (e: unknown) => (e instanceof Error ? e.message : String(e));

export function AboutDialog() {
  const autoCheck = !!(useApp((s) => s.dialogProps) as { check?: boolean }).check;
  const [version, setVersion] = useState<string>("");
  const [kind, setKind] = useState<CheckResult["install_kind"]>("installer");
  const [phase, setPhase] = useState<Phase>({ kind: "idle" });
  const [dl, setDl] = useState<DownloadState | null>(null);

  useEffect(() => {
    rpc<{ version: string; install_kind: CheckResult["install_kind"] }>("app.version").then(({ result }) => {
      setVersion(result.version);
      setKind(result.install_kind);
    });
    rpc<DownloadState>("app.update_status").then(({ result }) => result.state !== "idle" && setDl(result));
    if (autoCheck) check();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // follow the download while it runs; install as soon as it is verified
  useEffect(() => {
    if (!dl || !["downloading", "verifying"].includes(dl.state)) return;
    const t = window.setInterval(async () => {
      try {
        const { result } = await rpc<DownloadState>("app.update_status");
        setDl(result);
        if (result.state === "ready") install();
      } catch {
        /* keep polling */
      }
    }, 300);
    return () => window.clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dl?.state]);

  const check = async () => {
    setPhase({ kind: "checking" });
    try {
      const { result } = await rpc<CheckResult>("app.check_update");
      // a short pause so "checking" doesn't just flash
      await new Promise((r) => setTimeout(r, 350));
      setPhase({ kind: "result", r: result });
    } catch (e) {
      setPhase({ kind: "error", message: err(e) });
    }
  };

  const update = async () => {
    if (useApp.getState().info?.dirty) {
      const save = window.confirm("PySheets restarts to finish the update.\n\nSave your workbook first?\n\nOK = save and update · Cancel = update without saving");
      if (save) {
        await saveWorkbook();
        if (useApp.getState().info?.dirty) return; // save was cancelled
      }
    }
    try {
      setDl((await rpc<DownloadState>("app.update_start")).result);
    } catch (e) {
      toast(err(e), "error");
    }
  };

  const install = async () => {
    try {
      setDl((await rpc<DownloadState>("app.update_install")).result);
    } catch (e) {
      setDl((d) => (d ? { ...d, state: "error", error: err(e) } : d));
    }
  };

  const cancel = async () => setDl((await rpc<DownloadState>("app.update_cancel")).result);
  const openReleases = () => rpc("app.open_releases").catch((e) => toast(err(e), "error"));

  let card: React.ReactNode;
  if (dl && dl.state !== "idle") {
    const pct = dl.total ? Math.min(100, (dl.done / dl.total) * 100) : null;
    card =
      dl.state === "error" ? (
        <div className="upd-card warn">
          <TriangleAlert size={20} />
          <div className="upd-text">
            <b>The update didn't finish</b>
            <span>{dl.error}</span>
          </div>
          <button className="btn sm" onClick={update}>
            Try again
          </button>
        </div>
      ) : dl.state === "installing" ? (
        <div className="upd-card">
          <span className="mini-spin lg" />
          <div className="upd-text">
            <b>Installing PySheets {dl.version}…</b>
            <span>PySheets will close and reopen by itself in a moment.</span>
          </div>
        </div>
      ) : (
        <div className="upd-card col">
          <div className="upd-row">
            <Download size={18} />
            <div className="upd-text">
              <b>{dl.state === "verifying" || dl.state === "ready" ? "Verifying the download…" : `Downloading PySheets ${dl.version}…`}</b>
              <span>
                {mb(dl.done)}
                {dl.total ? ` of ${mb(dl.total)}` : ""}
              </span>
            </div>
            {dl.state === "downloading" && (
              <button className="btn ghost sm" onClick={cancel}>
                Cancel
              </button>
            )}
          </div>
          <div className="upd-bar">
            <div style={{ width: `${pct ?? 30}%` }} className={pct == null ? "indeterminate" : ""} />
          </div>
        </div>
      );
  } else if (phase.kind === "checking") {
    card = (
      <div className="upd-card">
        <span className="mini-spin lg" />
        <div className="upd-text">
          <b>Checking for updates…</b>
          <span>Looking up the latest release on GitHub</span>
        </div>
      </div>
    );
  } else if (phase.kind === "error") {
    card = (
      <div className="upd-card warn">
        <TriangleAlert size={20} />
        <div className="upd-text">
          <b>Couldn't check for updates</b>
          <span>{phase.message}</span>
        </div>
        <button className="btn sm" onClick={check}>
          Try again
        </button>
      </div>
    );
  } else if (phase.kind === "result" && !phase.r.update_available) {
    card = (
      <div className="upd-card ok pop-in">
        <CheckCircle2 size={20} />
        <div className="upd-text">
          <b>Your app is on the latest version</b>
          <span>PySheets {phase.r.current} is the newest release.</span>
        </div>
        <button className="btn ghost sm" onClick={check} data-tip="Check again">
          <RefreshCw size={14} />
        </button>
      </div>
    );
  } else if (phase.kind === "result") {
    const r = phase.r;
    const date = r.published_at ? new Date(r.published_at).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" }) : "";
    const canInstall = r.install_kind === "installer" && !!r.installer && r.platform === "win32";
    const notes = r.notes
      .split("\n")
      .filter((l) => !/full changelog/i.test(l))
      .join("\n")
      .replace(/\*\*/g, "")
      .replace(/^#+\s*/gm, "")
      .trim();
    card = (
      <div className="upd-card new col pop-in">
        <div className="upd-row">
          <ArrowUpCircle size={20} />
          <div className="upd-text">
            <b>PySheets {r.latest} is available</b>
            <span>
              You have {r.current}
              {date ? ` · released ${date}` : ""}
            </span>
          </div>
          {canInstall ? (
            <button className="btn primary sm" onClick={update}>
              <Download size={14} /> Update to {r.latest}
            </button>
          ) : (
            <button className="btn primary sm" onClick={openReleases}>
              <ExternalLink size={14} /> Download
            </button>
          )}
        </div>
        {notes && <pre className="upd-notes">{notes}</pre>}
        {!canInstall && (
          <span className="muted upd-hint">
            {r.install_kind === "portable"
              ? "This copy is the portable zip, so download the new zip (or the installer) from GitHub."
              : r.install_kind === "source"
                ? "PySheets is running from source: pull the latest code to update."
                : "Download the new version from GitHub."}
          </span>
        )}
      </div>
    );
  } else {
    card = (
      <div className="upd-card">
        <RefreshCw size={18} />
        <div className="upd-text">
          <b>Updates</b>
          <span>PySheets never updates on its own. Check whenever you like.</span>
        </div>
        <button className="btn primary sm" onClick={check}>
          Check for updates
        </button>
      </div>
    );
  }

  return (
    <Shell title="About PySheets">
      <div className="about-head">
        <Logo />
        <div>
          <h3>PySheets</h3>
          <div className="about-ver">
            Version <b>{version || "…"}</b>
            {kind === "portable" && <span className="about-tag">portable</span>}
            {kind === "source" && <span className="about-tag">from source</span>}
          </div>
        </div>
      </div>
      {card}
      <p className="about">
        A fast desktop spreadsheet for millions of rows. Data lives in an embedded DuckDB database, and formulas compile to vectorised SQL, so a column formula over ten million rows runs as a single query.
      </p>
      <div className="about-links">
        <button className="link" onClick={() => openDialog("docs")}>
          <BookOpen size={14} /> Documentation
        </button>
        <button className="link" onClick={openReleases}>
          <ExternalLink size={14} /> Release notes
        </button>
      </div>
    </Shell>
  );
}
