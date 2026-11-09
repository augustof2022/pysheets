import { Upload } from "lucide-react";
import { lazy, Suspense, useEffect, useState } from "react";
import { rpc } from "./api";
import { BusyBar, FormulaBar, SheetTabs, Toasts, TopBar } from "./components/Chrome";
import { FloatingMenu } from "./components/Menu";
import Toolbar from "./components/Toolbar";
import Tooltip from "./components/Tooltip";
import Grid from "./grid/Grid";
import { Dialogs } from "./panels/Dialogs";
import { CommandPalette, FilterPopover, FindReplace } from "./panels/Popovers";
import { SidePanel } from "./panels/SidePanels";
import { enableScripts, openScripts } from "./scripts";
import { ShieldAlert } from "lucide-react";
import { handleShortcut } from "./shortcuts";
import { openDialog, refreshInfo, toast, useApp } from "./store";

const ScriptEditor = lazy(() => import("./panels/ScriptEditor"));

export default function App() {
  const theme = useApp((s) => s.theme);
  const info = useApp((s) => s.info);
  const menu = useApp((s) => s.menu);
  const scriptsOpen = useApp((s) => s.scriptsOpen);
  const [scriptsEverOpened, setScriptsEverOpened] = useState(false);
  useEffect(() => {
    if (scriptsOpen) setScriptsEverOpened(true);
  }, [scriptsOpen]);
  const [dropping, setDropping] = useState(false);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  useEffect(() => {
    refreshInfo().catch((e) => toast(String(e), "error"));
    rpc<string[]>("functions")
      .then(({ result }) => useApp.setState({ functions: result, builtinFunctions: result }))
      .catch(() => undefined);
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      if (t.closest("input, textarea, select") && !t.classList.contains("grid-input")) {
        // still allow the palette and save from text fields
        if ((e.ctrlKey || e.metaKey) && ["k", "s"].includes(e.key.toLowerCase())) {
          if (handleShortcut(e, "global")) e.preventDefault();
        }
        return;
      }
      if (t.classList.contains("grid-input")) return;
      if (handleShortcut(e, "global")) e.preventDefault();
    };
    window.addEventListener("keydown", onKey);
    const onBefore = (e: BeforeUnloadEvent) => {
      if (useApp.getState().info?.dirty) e.preventDefault();
    };
    window.addEventListener("beforeunload", onBefore);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("beforeunload", onBefore);
    };
  }, []);

  useEffect(() => {
    let depth = 0;
    const isFiles = (e: DragEvent) => !!e.dataTransfer && [...e.dataTransfer.types].includes("Files");
    const enter = (e: DragEvent) => {
      if (!isFiles(e)) return;
      depth++;
      setDropping(true);
    };
    const leave = (e: DragEvent) => {
      if (!isFiles(e)) return;
      depth = Math.max(0, depth - 1);
      if (!depth) setDropping(false);
    };
    const over = (e: DragEvent) => {
      if (isFiles(e)) e.preventDefault();
    };
    const drop = (e: DragEvent) => {
      if (!isFiles(e)) return;
      e.preventDefault();
      depth = 0;
      setDropping(false);
      if (useApp.getState().dialog === "import") return;
      const files = e.dataTransfer?.files;
      if (files?.length) {
        openDialog("import");
        window.setTimeout(() => {
          const input = document.querySelector<HTMLInputElement>(".dialog input[type=file]");
          if (input) {
            const dt = new DataTransfer();
            for (const f of files) dt.items.add(f);
            input.files = dt.files;
            input.dispatchEvent(new Event("change", { bubbles: true }));
          }
        }, 30);
      }
    };
    window.addEventListener("dragenter", enter);
    window.addEventListener("dragleave", leave);
    window.addEventListener("dragover", over);
    window.addEventListener("drop", drop);
    return () => {
      window.removeEventListener("dragenter", enter);
      window.removeEventListener("dragleave", leave);
      window.removeEventListener("dragover", over);
      window.removeEventListener("drop", drop);
    };
  }, []);

  return (
    <div className="app">
      <BusyBar />
      <TopBar />
      <Toolbar />
      <FormulaBar />
      {info && !info.scripts_trusted && (
        <div className="trust-banner pop-in">
          <ShieldAlert size={16} />
          <span>This workbook contains Python scripts. They stay off until you enable them — custom functions show #NAME? meanwhile.</span>
          <button className="btn ghost sm" onClick={openScripts}>
            Review code
          </button>
          <button className="btn primary sm" onClick={enableScripts}>
            Enable scripts
          </button>
        </div>
      )}
      <main className="workspace">
        <div className="grid-wrap">
          {info ? <Grid /> : <div className="boot"><span className="mini-spin" /> Starting engine…</div>}
          <FindReplace />
        </div>
        <SidePanel />
      </main>
      <SheetTabs />
      <FilterPopover />
      <Dialogs />
      <CommandPalette />
      {scriptsEverOpened && (
        <Suspense fallback={null}>
          <ScriptEditor />
        </Suspense>
      )}
      <Toasts />
      <Tooltip />
      {menu && <FloatingMenu items={menu.items} x={menu.x} y={menu.y} onClose={() => useApp.setState({ menu: null })} />}
      {dropping && (
        <div className="drop-overlay fade-in">
          <div className="drop-card scale-in">
            <Upload size={28} />
            <b>Drop to import</b>
            <span>CSV, Excel, Parquet, JSON, SQLite, DuckDB</span>
          </div>
        </div>
      )}
    </div>
  );
}
