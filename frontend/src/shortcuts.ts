import * as A from "./actions";
import { saveWorkbook, saveWorkbookAs } from "./fileops";
import { currentSheet, openDialog, selectSheet, useApp } from "./store";

const isMac = /Mac|iPhone|iPad/.test(navigator.platform);
export const MOD = isMac ? "⌘" : "Ctrl";

/** App-wide shortcuts.  Returns true when handled. */
export function handleShortcut(e: KeyboardEvent, ctx: "grid" | "global"): boolean {
  const mod = e.ctrlKey || e.metaKey;
  const key = e.key.toLowerCase();
  const st = useApp.getState();
  if (st.editor && ctx === "global") return false;
  if (mod && !e.altKey) {
    switch (key) {
      case "z":
        if (e.shiftKey) A.redo();
        else A.undo();
        return true;
      case "y":
        A.redo();
        return true;
      case "b":
        A.toggleStyle("bold");
        return true;
      case "i":
        A.toggleStyle("italic");
        return true;
      case "u":
        A.toggleStyle("underline");
        return true;
      case "f":
        A.openFind("find");
        return true;
      case "h":
        A.openFind("replace");
        return true;
      case "k":
      case "p":
        if (key === "p" && !e.shiftKey) return false;
        useApp.setState({ palette: true });
        return true;
      case "o":
        A.openImport();
        return true;
      case "s":
        if (e.shiftKey) saveWorkbookAs();
        else saveWorkbook();
        return true;
      case "e":
        if (e.shiftKey) {
          A.openExport();
          return true;
        }
        return false;
      case "d":
        if (ctx !== "grid") return false;
        A.fillDown();
        return true;
      case "enter":
        if (ctx !== "grid") return false;
        A.fillColumn();
        return true;
      case "a":
        if (ctx !== "grid") return false;
        A.selectAll();
        return true;
      case " ":
        if (ctx !== "grid") return false;
        A.selectColumn();
        return true;
      case "l":
        if (e.shiftKey) {
          A.toggleFilter();
          return true;
        }
        return false;
      case "/":
        openDialog("shortcuts");
        return true;
      case ";": {
        if (ctx !== "grid") return false;
        const d = new Date();
        const pad = (n: number) => String(n).padStart(2, "0");
        A.startEdit(`${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`);
        return true;
      }
      case "pagedown":
      case "pageup": {
        const info = st.info;
        const sh = currentSheet();
        if (!info || !sh) return false;
        const i = info.sheets.findIndex((s) => s.id === sh.id);
        const j = (i + (key === "pagedown" ? 1 : -1) + info.sheets.length) % info.sheets.length;
        selectSheet(info.sheets[j].id);
        return true;
      }
      case "5":
        A.toggleStyle("strike");
        return true;
    }
  }
  if (ctx === "grid" && !mod && !e.altKey) {
    if (e.key === "Delete" || e.key === "Backspace") {
      A.clearSelection("contents");
      return true;
    }
    if (e.key === " " && e.shiftKey) {
      A.selectRow();
      return true;
    }
  }
  if (e.key === "F1" && !mod && !e.altKey) {
    openDialog("docs");
    return true;
  }
  if (e.altKey && e.shiftKey && e.key === "5") {
    A.toggleStyle("strike");
    return true;
  }
  return false;
}
