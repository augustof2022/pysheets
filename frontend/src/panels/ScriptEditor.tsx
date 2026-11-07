import { indentWithTab } from "@codemirror/commands";
import { python } from "@codemirror/lang-python";
import { Compartment, EditorState, Prec } from "@codemirror/state";
import { oneDark } from "@codemirror/theme-one-dark";
import { EditorView, keymap } from "@codemirror/view";
import { basicSetup } from "codemirror";
import { ChevronDown, FileCode, FunctionSquare, Play, Plus, Save, ShieldAlert, Square, Terminal, Trash2, X } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { rpc } from "../api";
import { FloatingMenu, usePresence } from "../components/Menu";
import { enableScripts, loadScripts, runScript, saveScript, stopScript, type CustomFunction, type Script } from "../scripts";
import { openDialog, toast, useApp, type MenuItem } from "../store";

const KIND_LABEL: Record<Script["kind"], string> = { macro: "Macro", functions: "Custom functions", file: "Run a .py file" };

function CodeEditor({ value, onChange, onRun, onSave }: { value: string; onChange: (v: string) => void; onRun: () => void; onSave: () => void }) {
  const host = useRef<HTMLDivElement>(null);
  const view = useRef<EditorView | null>(null);
  const theme = useRef(new Compartment());
  const dark = useApp((s) => s.theme) === "dark";
  const handlers = useRef({ onChange, onRun, onSave });
  handlers.current = { onChange, onRun, onSave };

  useEffect(() => {
    const v = new EditorView({
      parent: host.current!,
      state: EditorState.create({
        doc: value,
        extensions: [
          basicSetup,
          python(),
          // highest precedence so basicSetup's Mod-Enter (insertBlankLine) doesn't swallow "run"
          Prec.highest(
            keymap.of([
              { key: "Mod-s", preventDefault: true, run: () => (handlers.current.onSave(), true) },
              { key: "Mod-Enter", preventDefault: true, run: () => (handlers.current.onRun(), true) },
            ]),
          ),
          keymap.of([indentWithTab]),
          theme.current.of(dark ? oneDark : []),
          EditorView.updateListener.of((u) => {
            if (u.docChanged) handlers.current.onChange(u.state.doc.toString());
          }),
          EditorView.theme({ "&": { height: "100%", fontSize: "13px" }, ".cm-scroller": { fontFamily: "var(--mono)", lineHeight: "1.55" } }),
        ],
      }),
    });
    view.current = v;
    return () => v.destroy();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    view.current?.dispatch({ effects: theme.current.reconfigure(dark ? oneDark : []) });
  }, [dark]);

  useEffect(() => {
    const v = view.current;
    if (v && v.state.doc.toString() !== value) v.dispatch({ changes: { from: 0, to: v.state.doc.length, insert: value } });
  }, [value]);

  return <div className="code-host" ref={host} />;
}

export default function ScriptEditor() {
  const open = useApp((s) => s.scriptsOpen);
  const run = useApp((s) => s.run);
  const trustedInfo = useApp((s) => s.info?.scripts_trusted ?? true);
  const { mounted, state } = usePresence(open, 240);
  const [scripts, setScripts] = useState<Script[]>([]);
  const [functions, setFunctions] = useState<CustomFunction[]>([]);
  const [templates, setTemplates] = useState<Script[]>([]);
  const [current, setCurrent] = useState<Script | null>(null);
  const [dirty, setDirty] = useState(false);
  const [menu, setMenu] = useState<{ x: number; y: number; items: MenuItem[] } | null>(null);
  const [consoleOpen, setConsoleOpen] = useState(true);
  const consoleRef = useRef<HTMLDivElement>(null);

  const reload = async (selectId?: string) => {
    const st = await loadScripts();
    setScripts(st.scripts);
    setFunctions(st.functions);
    if (selectId) setCurrent(st.scripts.find((s) => s.id === selectId) || null);
    else if (!current && st.scripts.length) setCurrent(st.scripts[0]);
  };

  useEffect(() => {
    if (!open) return;
    reload();
    rpc<Script[]>("scripts.templates").then(({ result }) => setTemplates(result));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  useEffect(() => {
    const el = consoleRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [run?.lines.length]);

  const save = async () => {
    if (!current) return;
    const saved = await saveScript(current);
    if (saved) {
      setDirty(false);
      await reload(saved.id);
      toast(saved.kind === "functions" ? "Saved — functions registered" : "Saved", "success");
    }
  };

  const doRun = async () => {
    if (!current) return;
    if (current.kind === "functions") return save();
    if (dirty) {
      const saved = await saveScript(current);
      if (!saved) return;
      setDirty(false);
      await reload(saved.id);
    }
    setConsoleOpen(true);
    runScript(current.id);
  };

  const create = async (tpl?: Script) => {
    const base: Partial<Script> = tpl ? { ...tpl, id: undefined } : { name: "Untitled script", kind: "macro", code: "print(ps.sheets())\n" };
    delete (base as { id?: string }).id;
    const saved = await saveScript(base);
    if (saved) await reload(saved.id);
  };

  const remove = async () => {
    if (!current || !window.confirm(`Delete “${current.name}”?`)) return;
    await rpc("scripts.delete", { id: current.id });
    setCurrent(null);
    await reload();
  };

  const myFns = useMemo(() => functions.filter((f) => !current || f.script_id === current.id), [functions, current]);
  if (!mounted) return null;
  const running = run?.status === "running";

  return (
    <>
      <div className={`scrim ${state}`} onMouseDown={() => useApp.setState({ scriptsOpen: false })} />
      <section className="script-drawer" data-state={state}>
        <header className="sd-head">
          <div className="sd-title">
            <Terminal size={18} />
            <h3>Scripts</h3>
            <span className="muted small">Python · runs on this computer with pandas & DuckDB</span>
          </div>
          <button className="icon-btn" onClick={() => useApp.setState({ scriptsOpen: false })}>
            <X size={18} />
          </button>
        </header>
        {!trustedInfo && (
          <div className="trust-bar">
            <ShieldAlert size={16} />
            <span>This workbook came with Python code. Review it, then enable scripts and custom functions.</span>
            <button className="btn primary sm" onClick={enableScripts}>
              Enable scripts
            </button>
          </div>
        )}
        <div className="sd-body">
          <aside className="sd-list">
            <button
              className="btn primary sm full"
              onClick={(e) => {
                const r = (e.currentTarget as HTMLElement).getBoundingClientRect();
                setMenu({ x: r.left, y: r.bottom + 4, items: [{ label: "Blank script", icon: "plus", run: () => create() }, { divider: true }, ...templates.map((t) => ({ label: t.name, icon: t.kind === "functions" ? "function" : t.kind === "file" ? "folder-open" : "sparkles", run: () => create(t) }))] });
              }}
            >
              <Plus size={15} /> New script <ChevronDown size={14} />
            </button>
            <div className="sd-items">
              {scripts.length === 0 && <div className="muted small sd-empty">No scripts yet. Start from a template.</div>}
              {scripts.map((s) => (
                <button key={s.id} className={`sd-item ${current?.id === s.id ? "on" : ""}`} onClick={() => (setCurrent(s), setDirty(false))}>
                  {s.kind === "functions" ? <FunctionSquare size={15} /> : s.kind === "file" ? <FileCode size={15} /> : <Terminal size={15} />}
                  <span>{s.name}</span>
                </button>
              ))}
            </div>
            {functions.length > 0 && (
              <div className="sd-fns">
                <div className="label">Custom functions</div>
                {functions.map((f) => (
                  <div key={f.name} className="sd-fn" title={f.doc}>
                    <code>
                      ={f.name}({f.param_names.join(", ")})
                    </code>
                  </div>
                ))}
              </div>
            )}
          </aside>
          <div className="sd-main">
            {!current ? (
              <div className="sd-placeholder">
                <Terminal size={30} />
                <b>Automate your spreadsheet with Python</b>
                <span>Consolidate Google Sheets, run checks with SQL, or turn Python into formula functions.</span>
                <div className="chips">
                  {templates.slice(0, 4).map((t) => (
                    <button key={t.id} className="chip lg" onClick={() => create(t)}>
                      {t.name}
                    </button>
                  ))}
                </div>
              </div>
            ) : (
              <>
                <div className="sd-toolbar">
                  <input
                    className="sd-name"
                    value={current.name}
                    onChange={(e) => {
                      setCurrent({ ...current, name: e.target.value });
                      setDirty(true);
                    }}
                  />
                  <select
                    value={current.kind}
                    onChange={(e) => {
                      setCurrent({ ...current, kind: e.target.value as Script["kind"] });
                      setDirty(true);
                    }}
                  >
                    {Object.entries(KIND_LABEL).map(([k, l]) => (
                      <option key={k} value={k}>
                        {l}
                      </option>
                    ))}
                  </select>
                  <div className="tb-flex" />
                  {dirty && <span className="sd-dirty">Unsaved</span>}
                  <button className="icon-btn" data-tip="Delete" onClick={remove}>
                    <Trash2 size={16} />
                  </button>
                  <button className="btn ghost sm" onClick={save}>
                    <Save size={15} /> Save
                  </button>
                  {running ? (
                    <button className="btn danger sm" onClick={stopScript}>
                      <Square size={14} /> Stop
                    </button>
                  ) : (
                    <button className="btn primary sm" onClick={doRun} disabled={!trustedInfo}>
                      <Play size={15} /> {current.kind === "functions" ? "Save & register" : "Run"}
                    </button>
                  )}
                </div>
                {current.kind === "file" ? (
                  <div className="sd-file">
                    <div className="field">
                      <label>Python file</label>
                      <div className="row-gap">
                        <input value={current.path || ""} placeholder="C:\PythonScripts\my_project\scripts\run.py" onChange={(e) => (setCurrent({ ...current, path: e.target.value }), setDirty(true))} />
                        <button
                          className="btn ghost"
                          onClick={async () => {
                            const r = await rpc<{ paths?: string[]; unsupported?: boolean }>("dialog.open", { types: ["Python (*.py)"] });
                            if (r.result.paths?.[0]) {
                              setCurrent({ ...current, path: r.result.paths[0] });
                              setDirty(true);
                            } else if (r.result.unsupported) toast("Type the full path to the file", "info");
                          }}
                        >
                          Browse…
                        </button>
                      </div>
                    </div>
                    <div className="field">
                      <label>Arguments</label>
                      <input value={current.args || ""} placeholder="--extract-date 2026-05-28 --skip-external" onChange={(e) => (setCurrent({ ...current, args: e.target.value }), setDirty(true))} />
                    </div>
                    <div className="field">
                      <label>Python</label>
                      <select value={current.python || "auto"} onChange={(e) => (setCurrent({ ...current, python: e.target.value }), setDirty(true))}>
                        <option value="auto">Installed Python (uses your packages), else built-in</option>
                        <option value="bundled">Built-in (pandas, DuckDB, pyarrow, google-auth)</option>
                      </select>
                      <small>Output appears in the console below. Use a macro with ps.run_file(...) to chain steps and then load results into sheets.</small>
                    </div>
                  </div>
                ) : (
                  <div className="sd-editor">
                    <CodeEditor
                      key={current.id}
                      value={current.code || ""}
                      onChange={(v) => {
                        setCurrent((c) => (c ? { ...c, code: v } : c));
                        setDirty(true);
                      }}
                      onRun={doRun}
                      onSave={save}
                    />
                  </div>
                )}
                {current.kind === "functions" && myFns.length > 0 && (
                  <div className="sd-fnbar">
                    {myFns.map((f) => (
                      <span key={f.name} className="chip">
                        ={f.name}({f.param_names.join(", ")}) → {f.ret}
                        {f.vectorized ? " · vectorised" : ""}
                      </span>
                    ))}
                  </div>
                )}
                <div className={`sd-console ${consoleOpen ? "" : "closed"}`}>
                  <div className="sd-console-head" onClick={() => setConsoleOpen(!consoleOpen)}>
                    <Terminal size={14} />
                    <b>Console</b>
                    {run && (
                      <span className={`run-chip ${run.status}`}>
                        {run.status === "running" ? <span className="mini-spin" /> : null}
                        {run.name} · {run.status} · {run.elapsed}s
                      </span>
                    )}
                    <div className="tb-flex" />
                    <span className="muted small">Ctrl+Enter run · Ctrl+S save</span>
                  </div>
                  <div className="sd-console-body" ref={consoleRef}>
                    {!run && <div className="muted small">Output from print() and errors appear here.</div>}
                    {run?.lines.map((l, i) => (
                      <div key={i} className={`cl ${l.s}`}>
                        {l.text || "\u00a0"}
                      </div>
                    ))}
                    {run?.error && run.status !== "running" && <div className="cl err">✖ {run.error}</div>}
                    {run?.error && run.status !== "running" && /sign in/i.test(run.error) && /google/i.test(run.error) && (
                      <button className="btn sm primary cl-action" onClick={() => openDialog("google", { retry: () => run.scriptId && runScript(run.scriptId) })}>
                        Sign in to Google, then run again
                      </button>
                    )}
                  </div>
                </div>
              </>
            )}
          </div>
        </div>
        {menu && <FloatingMenu items={menu.items} x={menu.x} y={menu.y} onClose={() => setMenu(null)} />}
      </section>
    </>
  );
}
