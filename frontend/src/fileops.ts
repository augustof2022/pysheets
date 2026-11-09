import { DESKTOP, rpc } from "./api";
import { invalidate } from "./actions";
import { call, openDialog, query, toast, useApp } from "./store";

export async function newWorkbook() {
  const info = useApp.getState().info;
  if (info?.dirty && !window.confirm("Discard unsaved changes and start a new spreadsheet?")) return;
  await call("workbook.new");
  useApp.setState({ perSheet: {} });
  invalidate();
}

export async function openFile() {
  if (DESKTOP) {
    const res = await query<{ paths?: string[] }>("dialog.open", { multiple: true });
    const paths = res?.paths || [];
    if (!paths.length) return;
    if (paths.length === 1 && paths[0].toLowerCase().endsWith(".pysheets")) {
      await call("workbook.open", { path: paths[0] }, { label: "Opening", success: "Workbook opened" });
      useApp.setState({ perSheet: {} });
      invalidate();
      return;
    }
    openDialog("import", { paths });
    return;
  }
  openDialog("import");
}

export async function saveWorkbook() {
  const info = useApp.getState().info;
  if (info?.path) {
    await call("workbook.save", { path: info.path }, { label: "Saving", success: "Saved" });
    return;
  }
  await saveWorkbookAs();
}

/** New workbooks are saved in Documents\PySheets (the save dialog starts there). */
export async function saveWorkbookAs() {
  const info = useApp.getState().info;
  const name = `${(info?.title || "Untitled spreadsheet").replace(/[\\/:*?"<>|]+/g, "_")}.pysheets`;
  let path: string | null | undefined;
  if (DESKTOP) {
    const res = await query<{ path?: string | null }>("dialog.save", { name, types: ["PySheets workbook (*.pysheets)"] });
    if (!res?.path) return;
    path = res.path;
  }
  // browser mode: no path -> the server picks Documents/PySheets/<title>.pysheets
  const r = await call<{ path: string }>("workbook.save", path ? { path } : {}, { label: "Saving" });
  if (r?.path) toast(`Saved to ${r.path}`, "success", { label: "Show in folder", run: () => revealPath(r.path) }, 6000);
}

function revealPath(path?: string) {
  rpc("paths.reveal", path ? { path } : {}).catch((e) => toast(e instanceof Error ? e.message : String(e), "error"));
}

export function openPySheetsFolder() {
  revealPath();
}

export function triggerDownload(url: string) {
  const a = document.createElement("a");
  a.href = url;
  a.download = "";
  document.body.appendChild(a);
  a.click();
  a.remove();
}

export function exportDialog() {
  openDialog("export");
}
