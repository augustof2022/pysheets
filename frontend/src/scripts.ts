import { rpc } from "./api";
import { invalidate } from "./actions";
import { applyInfo, refreshInfo, toast, useApp, type RunState, type ScriptLine } from "./store";

export interface Script {
  id: string;
  name: string;
  kind: "macro" | "functions" | "file";
  code?: string;
  path?: string;
  args?: string;
  python?: string;
  updated?: string;
}

export interface CustomFunction {
  name: string;
  params: string[];
  param_names: string[];
  ret: string;
  doc: string;
  script_id: string | null;
  vectorized: boolean;
}

export interface ScriptsState {
  scripts: Script[];
  functions: CustomFunction[];
  trusted: boolean;
}

interface RunDto {
  id: string;
  script_id: string | null;
  name: string;
  status: RunState["status"];
  lines: ScriptLine[];
  next: number;
  error: string | null;
  elapsed: number;
  toasts: string[];
  version?: number;
}

let polling = 0;

/** Start a script (saved by id, or ad-hoc code) and stream its output into the store. */
export async function runScript(id: string | null, code?: string, name?: string) {
  const cur = useApp.getState().run;
  if (cur && cur.status === "running") {
    toast("A script is already running", "error");
    return;
  }
  let dto: RunDto;
  try {
    dto = (await rpc<RunDto>("scripts.run", { id, code, name })).result;
  } catch (e) {
    toast(e instanceof Error ? e.message : String(e), "error");
    return;
  }
  const token = ++polling;
  useApp.setState({ run: { id: dto.id, scriptId: dto.script_id, name: dto.name, status: dto.status, lines: dto.lines, error: null, elapsed: 0 } });
  let next = dto.next;
  let shownToasts = 0;
  let version = useApp.getState().info?.version ?? 0;
  const tick = async () => {
    if (token !== polling) return;
    let st: RunDto;
    try {
      st = (await rpc<RunDto>("scripts.status", { run: dto.id, since: next })).result;
    } catch {
      window.setTimeout(tick, 800);
      return;
    }
    next = st.next;
    const prev = useApp.getState().run;
    useApp.setState({
      run: { id: st.id, scriptId: st.script_id, name: st.name, status: st.status, lines: [...(prev?.id === st.id ? prev.lines : []), ...st.lines].slice(-5000), error: st.error, elapsed: st.elapsed },
    });
    for (const t of st.toasts.slice(shownToasts)) toast(t, "success");
    shownToasts = st.toasts.length;
    if (st.version !== undefined && st.version !== version) {
      version = st.version;
      await refreshInfo();
      invalidate();
    }
    if (st.status === "running") window.setTimeout(tick, 350);
    else {
      await refreshInfo();
      invalidate();
      if (st.status === "done") toast(`${st.name} finished in ${st.elapsed}s`, "success");
      else if (st.status === "error") toast(`${st.name} failed: ${st.error}`, "error");
    }
  };
  window.setTimeout(tick, 200);
}

export async function stopScript() {
  const run = useApp.getState().run;
  if (run && run.status === "running") await rpc("scripts.cancel", { run: run.id });
}

export async function loadScripts(): Promise<ScriptsState> {
  const { result } = await rpc<ScriptsState>("scripts.list");
  return result;
}

export async function saveScript(s: Partial<Script>): Promise<Script | undefined> {
  try {
    const { result, info } = await rpc<Script>("scripts.save", { script: s });
    applyInfo(info);
    invalidate();
    return result;
  } catch (e) {
    toast(e instanceof Error ? e.message : String(e), "error");
    return undefined;
  }
}

export async function enableScripts() {
  try {
    const { result, info } = await rpc<{ functions: string[]; errors: string[] }>("scripts.trust");
    applyInfo(info);
    invalidate();
    if (result.errors.length) toast(result.errors.join("; "), "error");
    else toast(result.functions.length ? `Enabled ${result.functions.length} custom function(s)` : "Scripts enabled", "success");
  } catch (e) {
    toast(e instanceof Error ? e.message : String(e), "error");
  }
}

export function openScripts() {
  useApp.setState({ scriptsOpen: true });
}
