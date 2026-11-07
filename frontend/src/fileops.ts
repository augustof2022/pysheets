import { DESKTOP, downloadUrl, rpc } from "./api";
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

export async function saveWorkbookAs() {
  const info = useApp.getState().info;
  const name = `${(info?.title || "Untitled spreadsheet").replace(/[\\/:*?"<>|]+/g, "_")}.pysheets`;
  if (DESKTOP) {
    const res = await query<{ path?: string | null }>("dialog.save", { name, types: ["PySheets workbook (*.pysheets)"] });
    if (!res?.path) return;
    await call("workbook.save", { path: res.path }, { label: "Saving", success: "Saved" });
    return;
  }
  const tmp = await rpc<{ path: string }>("io.temp_path", { name });
  const ok = await call("workbook.save", { path: tmp.result.path }, { label: "Saving" });
  if (ok !== undefined) {
    triggerDownload(downloadUrl(tmp.result.path));
    toast("Workbook downloaded", "success");
  }
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
