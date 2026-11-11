import { FileSpreadsheet, FolderOpen, Link2 } from "lucide-react";
import { useEffect, useState } from "react";
import { invalidate } from "../actions";
import { DESKTOP, rpc } from "../api";
import { call, closeDialog, query, selectSheet, toast, useApp } from "../store";
import { Shell } from "./Dialogs";

const base = (p: string) => p.split(/[\\/]/).pop() || p;

/**
 * Add another file (another .pysheets workbook, Excel, CSV, Parquet…) to this window so
 * formulas can use both: its tabs become linked sheets that Refresh re-reads from the file.
 */
export function LinkDialog() {
  const props = useApp((s) => s.dialogProps) as { path?: string; offerOpen?: boolean };
  const [path, setPath] = useState(props.path || "");
  const [tabs, setTabs] = useState<string[] | null>(null);
  const [chosen, setChosen] = useState<Set<string>>(new Set());
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!path) return setTabs(null);
    let alive = true;
    rpc<{ tabs: string[] }>("files.tabs", { path })
      .then(({ result }) => {
        if (!alive) return;
        setTabs(result.tabs);
        setChosen(new Set(result.tabs));
      })
      .catch((e) => alive && (setTabs(null), toast(e instanceof Error ? e.message : String(e), "error")));
    return () => {
      alive = false;
    };
  }, [path]);

  const browse = async () => {
    const res = await query<{ paths?: string[] }>("dialog.open", {
      types: ["Spreadsheets (*.pysheets;*.xlsx;*.xlsm;*.xls;*.xlsb;*.ods;*.csv;*.tsv;*.txt;*.parquet;*.json;*.sqlite;*.db;*.duckdb)", "All files (*.*)"],
    });
    if (res?.paths?.[0]) setPath(res.paths[0]);
  };

  const add = async () => {
    setBusy(true);
    const pick = tabs && tabs.length > 1 ? [...chosen] : undefined;
    closeDialog();
    const r = await call<{ sheets: string[]; book: string }>("files.link", { path, tabs: pick }, { label: `Linking ${base(path)}` });
    setBusy(false);
    if (r?.sheets?.length) {
      selectSheet(r.sheets[0]);
      invalidate();
      const names = useApp.getState().info?.sheets.filter((s) => r.sheets.includes(s.id)).map((s) => s.name) || [];
      toast(`Added ${names.join(", ")} from ${r.book}. Use these tabs in formulas; Refresh re-reads the file.`, "success", undefined, 7000);
    }
  };

  const openInstead = async () => {
    closeDialog();
    await call("workbook.open", { path }, { label: "Opening", success: "Workbook opened" });
    useApp.setState({ perSheet: {} });
    invalidate();
  };

  return (
    <Shell
      title="Use another file in formulas"
      sub="Its tabs are added to this workbook, linked to the file"
      footer={
        <>
          {props.offerOpen && (
            <button className="btn ghost" onClick={openInstead} disabled={!path}>
              Open it instead (replace this workbook)
            </button>
          )}
          <span className="tb-flex" />
          <button className="btn" onClick={closeDialog}>
            Cancel
          </button>
          <button className="btn primary" onClick={add} disabled={!path || busy || (tabs !== null && tabs.length > 1 && chosen.size === 0)}>
            <Link2 size={15} /> Add to this workbook
          </button>
        </>
      }
    >
      <div className="link-form">
        <div className="field">
          <label>File</label>
          <div className="row-gap">
            {DESKTOP ? (
              <div className="link-file">
                <FileSpreadsheet size={16} />
                <span>{path ? base(path) : "No file chosen"}</span>
                {path && <small className="muted">{path}</small>}
              </div>
            ) : (
              <input value={path} placeholder="Full path on this computer, e.g. C:\Users\me\Documents\PySheets\prices.pysheets" onChange={(e) => setPath(e.target.value.trim())} />
            )}
            {DESKTOP && (
              <button className="btn ghost" onClick={browse}>
                <FolderOpen size={15} /> Choose…
              </button>
            )}
          </div>
        </div>
        {tabs && tabs.length > 1 && (
          <div className="field">
            <label>Tabs to add</label>
            <div className="chips wrap">
              {tabs.map((t) => (
                <button
                  key={t}
                  className={`chip lg ${chosen.has(t) ? "on" : ""}`}
                  onClick={() => {
                    const s = new Set(chosen);
                    if (s.has(t)) s.delete(t);
                    else s.add(t);
                    setChosen(s);
                  }}
                >
                  {t}
                </button>
              ))}
            </div>
          </div>
        )}
        <div className="note">
          <Link2 size={14} />
          <span>
            Use the new tabs in formulas like any other tab, e.g. <code>=INDEX('prices'!B:B, MATCH(A2, 'prices'!A:A, 0))</code>. Click cells on them while typing a formula to insert references. When the file changes, press <b>Refresh</b> on its tab, and columns you add yourself are kept.
          </span>
        </div>
      </div>
    </Shell>
  );
}
