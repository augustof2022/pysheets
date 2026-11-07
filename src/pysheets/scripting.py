"""Python scripts inside the workbook - "Apps Script, but Python".

* **Macros** run on demand (Run button, Scripts menu, command palette).
* **Function libraries** define custom formula functions with ``@ps.function``; they are
  registered as DuckDB UDFs, so ``=MY_CHECK(A2)`` works in cells and whole-column formulas.
* **File scripts** run an existing ``.py`` file (e.g. a pipeline) with your own Python or the
  bundled one, streaming its output into the console.

Scripts get a ``ps`` object (see :class:`Api`) plus ``pd`` (pandas) and ``duckdb``.
Every sheet is also a SQL view named after the tab, so ``ps.sql('SELECT * FROM "Sales"')`` works.
"""

from __future__ import annotations

import ctypes
import datetime as dt
import io
import itertools
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
import uuid
from collections.abc import Callable
from typing import Any

from .engine.workbook import EngineError, Workbook, norm_duck, q
from .formula.refs import parse_a1

_run_ids = itertools.count(1)


# ------------------------------------------------------------------ output capture


class _ThreadOutput(io.TextIOBase):
    """sys.stdout/stderr replacement that routes writes from script threads to their run."""

    def __init__(self, original, stream: str):
        self.original = original
        self.stream = stream
        self.targets: dict[int, ScriptRun] = {}

    def write(self, s: str) -> int:
        run = self.targets.get(threading.get_ident())
        if run is not None:
            run.emit(s, self.stream)
            return len(s)
        if self.original is not None:
            try:
                return self.original.write(s)
            except (OSError, ValueError):
                return len(s)
        return len(s)

    def flush(self):
        if self.original is not None:
            try:
                self.original.flush()
            except (OSError, ValueError):
                pass


_OUT: _ThreadOutput | None = None
_ERR: _ThreadOutput | None = None


def _install_capture():
    global _OUT, _ERR
    if _OUT is None:
        _OUT = _ThreadOutput(sys.stdout, "out")
        _ERR = _ThreadOutput(sys.stderr, "err")
        sys.stdout = _OUT
        sys.stderr = _ERR


class ScriptRun:
    def __init__(self, script_id: str | None, name: str):
        self.id = f"run{next(_run_ids)}"
        self.script_id = script_id
        self.name = name
        self.status = "running"
        self.lines: list[dict] = []
        self.started = time.time()
        self.ended: float | None = None
        self.error: str | None = None
        self.thread: threading.Thread | None = None
        self.proc: subprocess.Popen | None = None
        self.cursor = None
        self.toasts: list[str] = []
        self._partial = {"out": "", "err": ""}
        self._lock = threading.Lock()

    def emit(self, text: str, stream: str = "out"):
        with self._lock:
            buf = self._partial.get(stream, "") + text
            *full, rest = buf.split("\n")
            self._partial[stream] = rest
            for line in full:
                self.lines.append({"t": round(time.time() - self.started, 2), "s": stream, "text": line})
            if len(self.lines) > 20000:
                del self.lines[: len(self.lines) - 20000]

    def finish(self, status: str, error: str | None = None):
        for stream, rest in self._partial.items():
            if rest:
                self.lines.append({"t": round(time.time() - self.started, 2), "s": stream, "text": rest})
        self._partial = {"out": "", "err": ""}
        self.status = status
        self.error = error
        self.ended = time.time()

    def to_dict(self, since: int = 0) -> dict:
        return {
            "id": self.id,
            "script_id": self.script_id,
            "name": self.name,
            "status": self.status,
            "lines": self.lines[since:],
            "next": len(self.lines),
            "error": self.error,
            "elapsed": round((self.ended or time.time()) - self.started, 2),
            "toasts": self.toasts,
        }


# --------------------------------------------------------------------- the API


_PY_TO_DUCK = {
    str: "VARCHAR",
    int: "DOUBLE",
    float: "DOUBLE",
    bool: "BOOLEAN",
    dt.date: "DATE",
    dt.datetime: "TIMESTAMP",
}
_DUCK_TO_TYP = {"VARCHAR": "str", "DOUBLE": "num", "BOOLEAN": "bool", "DATE": "date", "TIMESTAMP": "ts"}
_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_.]*$")


class SheetHandle:
    """A tab, seen from a script."""

    def __init__(self, api: Api, sheet_id: str):
        self._api = api
        self._id = sheet_id

    @property
    def _sh(self):
        return self._api._wb.sheet(self._id)

    @property
    def name(self) -> str:
        return self._sh.name

    @property
    def nrows(self) -> int:
        return self._sh.nrows

    @property
    def columns(self) -> list[str]:
        return [c.name for c in self._sh.columns]

    @property
    def view(self) -> str:
        """SQL identifier for this sheet (header names as column names)."""
        self._api._sync_views()
        return q(self.name)

    def __repr__(self):
        return f"<Sheet {self.name!r}: {self.nrows:,} rows>"

    def df(self, limit: int | None = None):
        """The sheet as a pandas DataFrame (row 1 = column names)."""
        self._api._sync_views()
        lim = f" LIMIT {int(limit)}" if limit else ""
        return self._api.con.execute(f"SELECT * FROM {q(self.name)}{lim}").df()

    def arrow(self):
        self._api._sync_views()
        return self._api.con.execute(f"SELECT * FROM {q(self.name)}").fetch_arrow_table()

    def get(self, a1: str):
        r, c = _cell(a1)
        with self._api._wb.lock:
            return self._api._wb.get_cell(self._id, r, c)["value"]

    def set(self, a1: str, value: Any):
        """Set one cell; strings starting with '=' are formulas."""
        r, c = _cell(a1)
        with self._api._wb.lock:
            self._api._wb.set_cell(self._id, r, c, _as_input(value))
        self._api._changed()

    def set_values(self, a1: str, rows: list[list[Any]]):
        r, c = _cell(a1)
        with self._api._wb.lock:
            self._api._wb.set_range(
                self._id, r, c, [[_as_input(v) for v in row] for row in rows], label="Script"
            )
        self._api._changed()

    def write(self, df, start: str = "A1", header: bool = True):
        """Write a DataFrame (or list of rows) at ``start``."""
        rows = _df_rows(df, header)
        self.set_values(start, rows)

    def replace(self, df):
        """Replace the whole sheet with a DataFrame (types are kept; formats stay)."""
        from .engine.links import write_rows

        names, rows = _df_names_rows(df)
        sh = self._sh
        for c in sh.columns:
            c.linked = True
        write_rows(self._api._wb, names, rows, sheet=sh, link=sh.link)
        self._api._changed()

    def append(self, df):
        """Append rows below the data, matching columns by header name."""
        names, rows = _df_names_rows(df)
        with self._api._wb.lock:
            sh = self._sh
            pos = {n.strip().lower(): i for i, n in enumerate(c.name for c in sh.columns)}
            width = len(sh.columns)
            extra = [n for n in names if n.strip().lower() not in pos]
            for n in extra:
                pos[n.strip().lower()] = width
                width += 1
            out = []
            for r in rows:
                row = [None] * width
                for n, v in zip(names, r, strict=False):
                    row[pos[n.strip().lower()]] = v
                out.append([_as_input(v) for v in row])
            if extra:
                hdr = [None] * width
                for n in extra:
                    hdr[pos[n.strip().lower()]] = n
                self._api._wb.set_range(sh.id, 0, len(sh.columns), [hdr[len(sh.columns) :]], label="Script")
            self._api._wb.set_range(sh.id, sh.nrows + 1, 0, out, label="Script")
        self._api._changed()

    def clear(self):
        with self._api._wb.lock:
            sh = self._sh
            self._api._wb.clear_range(sh.id, 1, 0, sh.nrows, max(len(sh.columns) - 1, 0))
        self._api._changed()

    def refresh(self):
        """Re-pull a linked (Google Sheets) tab."""
        from .engine.links import refresh

        refresh(self._api._wb, self._id)
        self._api._changed()


class Api:
    """The ``ps`` object available inside scripts."""

    def __init__(self, wb: Workbook, manager: ScriptManager, run: ScriptRun | None):
        self._wb = wb
        self._mgr = manager
        self._run = run
        self._views_version = -1
        self._con = None
        self.functions_defined: list[str] = []

    # -- data access ----------------------------------------------------------------

    @property
    def con(self):
        """A DuckDB connection to the workbook (sheets are views named after the tabs)."""
        if self._con is None:
            self._con = self._wb.con.cursor()
            self._con.execute("CREATE SCHEMA IF NOT EXISTS sheets")
            self._con.execute("SET search_path = 'sheets,main'")
            if self._run is not None:
                self._run.cursor = self._con
        self._sync_views()
        return self._con

    def _sync_views(self):
        if self._views_version == self._wb.version:
            return
        con = self._con if self._con is not None else self.con
        with self._wb.lock:
            for sh in self._wb.sheets:
                seen: dict[str, int] = {}
                sel = []
                for i, c in enumerate(sh.columns):
                    n = c.name.strip() or _letters(i)
                    k = n.lower()
                    if k in seen:
                        seen[k] += 1
                        n = f"{n}_{seen[k]}"
                    else:
                        seen[k] = 1
                    sel.append(f"{q(c.id)} AS {q(n)}")
                cols = ", ".join(sel) or "NULL AS empty"
                con.execute(
                    f"CREATE OR REPLACE VIEW sheets.{q(sh.name)} AS SELECT {cols} FROM main.{q(sh.table)} "
                    f"WHERE __rid < {sh.nrows} ORDER BY __rid"
                )
            self._views_version = self._wb.version

    def sql(self, query: str, *params):
        """Run SQL; returns a pandas DataFrame."""
        return self.con.execute(query, list(params) if params else None).df()

    def sheets(self) -> list[str]:
        return [s.name for s in self._wb.sheets]

    def sheet(self, name: str) -> SheetHandle:
        sh = self._wb.get_sheet(name)
        if sh is None:
            raise EngineError(f"No sheet named '{name}'")
        return SheetHandle(self, sh.id)

    def new_sheet(self, name: str, df=None, replace: bool = True) -> SheetHandle:
        """Create (or replace) a sheet, optionally filled from a DataFrame / SQL result."""
        from .engine.links import write_rows

        existing = self._wb.get_sheet(name)
        if existing is not None and not replace:
            raise EngineError(f"A sheet named '{name}' already exists")
        if df is None:
            if existing is not None:
                return SheetHandle(self, existing.id)
            with self._wb.lock:
                sh = self._wb.add_sheet(name)
            self._changed()
            return SheetHandle(self, sh.id)
        names, rows = _df_names_rows(df)
        if existing is not None:
            for c in existing.columns:
                c.linked = True
            sh = write_rows(self._wb, names, rows, sheet=existing, link=existing.link)
        else:
            sh = write_rows(self._wb, names, rows, name=name)
            for c in sh.columns:
                c.linked = False
        self._changed()
        return SheetHandle(self, sh.id)

    def read(self, path: str, **kw):
        """Read CSV / Parquet / JSON / Excel into a DataFrame with DuckDB."""
        p = str(path)
        ext = os.path.splitext(p)[1].lower()
        if ext in (".xlsx", ".xlsm", ".xls", ".xlsb", ".ods"):
            import pandas as pd

            kw.setdefault("engine", "calamine")  # bundled Rust reader; openpyxl is optional
            return pd.read_excel(p, **kw)
        fn = {".parquet": "read_parquet", ".json": "read_json_auto", ".ndjson": "read_json_auto"}.get(
            ext, "read_csv"
        )
        return self.con.execute(f"SELECT * FROM {fn}(?)", [p]).df()

    # -- google sheets ---------------------------------------------------------------

    def gsheet(self, url_or_id: str, tab: str | None = None, typed: bool = False):
        """Read one Google Sheets tab into a DataFrame (uses the connected account)."""
        import pandas as pd

        from .connectors.google import GoogleClient, extract_sheet_id, values_to_rows

        client = self._mgr.google()
        sid = extract_sheet_id(url_or_id)
        if tab is None:
            tab = client.spreadsheet_info(sid)["tabs"][0]["title"]
        vals = client.get_values(sid, [tab], typed=typed).get(tab)
        if vals is None:
            raise EngineError(f"Tab '{tab}' not found in {sid}")
        names, rows = values_to_rows(vals)
        _ = GoogleClient
        return pd.DataFrame(rows, columns=names)

    def gsheet_tabs(self, url_or_id: str) -> list[str]:
        from .connectors.google import extract_sheet_id

        return [t["title"] for t in self._mgr.google().spreadsheet_info(extract_sheet_id(url_or_id))["tabs"]]

    def refresh_all(self):
        from .engine.links import refresh_all

        out = refresh_all(self._wb, self._mgr.google())
        self._changed()
        return out

    # -- running other code ----------------------------------------------------------

    def run_file(self, path: str, *args: str, python: str | None = None, cwd: str | None = None) -> int:
        """Run an existing .py file, streaming its output here.  Returns the exit code."""
        cmd = _python_cmd(python) + [str(path), *[str(a) for a in args]]
        return _stream_process(cmd, cwd or os.path.dirname(os.path.abspath(path)), self._run)

    # -- functions & UI --------------------------------------------------------------

    def function(self, fn: Callable | None = None, *, name: str | None = None, vectorized: bool = False):
        """Decorator: expose a Python function as a spreadsheet function, e.g. ``=HHID_OK(A2)``.

        Type hints choose argument/return types (str, int/float, bool, date, datetime);
        unannotated arguments arrive as text.  ``vectorized=True`` passes whole pandas
        Series (much faster on millions of rows).
        """

        def deco(f: Callable) -> Callable:
            fname = (name or f.__name__).upper()
            self._mgr.register_function(fname, f, vectorized, self._run.script_id if self._run else None)
            self.functions_defined.append(fname)
            return f

        return deco(fn) if fn is not None else deco

    def toast(self, message: str):
        """Show a notification in PySheets."""
        if self._run is not None:
            self._run.toasts.append(str(message))

    def progress(self, done: int, total: int | None = None, label: str = "Running script"):
        self._wb.progress = {"active": True, "label": label, "done": done, "total": total}

    def _changed(self):
        self._views_version = -1


# ------------------------------------------------------------------ helpers


def _letters(i: int) -> str:
    from .formula.refs import col_to_letters

    return col_to_letters(i)


def _cell(a1: str) -> tuple[int, int]:
    ref = parse_a1(a1)
    if ref is None or ref.r1 is None or ref.c1 is None:
        raise EngineError(f"'{a1}' is not a cell reference")
    return ref.r1, ref.c1


def _as_input(v: Any) -> Any:
    if v is None:
        return ""
    try:
        import math

        if isinstance(v, float) and math.isnan(v):
            return ""
    except TypeError:
        pass
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, (int, float)):
        return repr(v) if isinstance(v, float) else str(v)
    if isinstance(v, (dt.date, dt.datetime)):
        return v.isoformat(sep=" ") if isinstance(v, dt.datetime) else v.isoformat()
    if hasattr(v, "isoformat"):
        return v.isoformat()
    if isinstance(v, str) and not v.startswith("=") and v != "" and _looks_typed(v):
        return "'" + v  # keep text like 00123 as text
    return str(v)


def _looks_typed(s: str) -> bool:
    return bool(re.match(r"^\s*0\d+\s*$", s))


def _df_names_rows(df) -> tuple[list[str], list[list]]:
    try:
        import pandas as pd
    except ImportError:  # pragma: no cover
        pd = None
    if pd is not None and isinstance(df, pd.DataFrame):
        names = [str(c) for c in df.columns]
        clean = df.astype(object).where(pd.notna(df), None)
        rows = [list(r) for r in clean.itertuples(index=False, name=None)]
        rows = [[(v.to_pydatetime() if hasattr(v, "to_pydatetime") else v) for v in r] for r in rows]
        return names, rows
    if hasattr(df, "to_pandas"):
        return _df_names_rows(df.to_pandas())
    if hasattr(df, "df"):
        return _df_names_rows(df.df())
    rows = [list(r) for r in df]
    if not rows:
        return [], []
    return [str(x) for x in rows[0]], rows[1:]


def _df_rows(df, header: bool) -> list[list]:
    names, rows = _df_names_rows(df)
    return ([names] if header else []) + rows


def _python_cmd(python: str | None) -> list[str]:
    if python and python not in ("auto", "bundled"):
        return [python]
    if python != "bundled":
        found = shutil.which("python") or shutil.which("python3") or shutil.which("py")
        if found and "WindowsApps" not in found:
            return [found]
    if getattr(sys, "frozen", False):
        return [sys.executable, "--run-python"]
    return [sys.executable]


def _stream_process(cmd: list[str], cwd: str | None, run: ScriptRun | None) -> int:
    env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    proc = subprocess.Popen(
        cmd,
        cwd=cwd or None,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
        creationflags=flags,
        bufsize=1,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if run is not None:
        run.proc = proc
        run.emit(f"$ {' '.join(cmd)}\n", "sys")
    assert proc.stdout is not None
    for line in proc.stdout:
        if run is not None:
            run.emit(line, "out")
        else:
            print(line, end="")
    code = proc.wait()
    if run is not None:
        run.emit(f"[exit code {code}]\n", "sys" if code == 0 else "err")
    return code


# ------------------------------------------------------------------ manager


class ScriptManager:
    def __init__(self, wb: Workbook):
        self.wb = wb
        self.runs: dict[str, ScriptRun] = {}
        self.functions: dict[str, dict] = {}
        self._google = None
        self.trusted = True

    # -- scripts stored in the workbook ------------------------------------------------

    @property
    def scripts(self) -> list[dict]:
        return self.wb.scripts

    def get(self, script_id: str) -> dict:
        for s in self.scripts:
            if s["id"] == script_id:
                return s
        raise EngineError("Script not found")

    def save(self, script: dict) -> dict:
        script = dict(script)
        script.setdefault("id", uuid.uuid4().hex[:10])
        script.setdefault("kind", "macro")
        script.setdefault("name", "Untitled script")
        script["updated"] = dt.datetime.now().isoformat(timespec="seconds")
        for i, s in enumerate(self.scripts):
            if s["id"] == script["id"]:
                self.scripts[i] = {**s, **script}
                break
        else:
            script["created"] = script["updated"]
            self.scripts.append(script)
        self.wb.dirty = True
        self.wb.version += 1
        saved = self.get(script["id"])
        if saved.get("kind") == "functions" and self.trusted:
            self.load_functions(saved)
        return saved

    def delete(self, script_id: str):
        self.unregister_script_functions(script_id)
        self.wb.scripts[:] = [s for s in self.scripts if s["id"] != script_id]
        self.wb.dirty = True
        self.wb.version += 1

    # -- custom functions --------------------------------------------------------------

    def register_function(self, fname: str, fn: Callable, vectorized: bool, script_id: str | None):
        import inspect

        from .formula.compiler import SCALAR_FUNCS, Compiler

        if not _NAME_RE.match(fname):
            raise EngineError(f"'{fname}' is not a valid function name (letters, digits, _ and .)")
        if fname in SCALAR_FUNCS or hasattr(Compiler, "fn_" + fname.replace(".", "_")):
            raise EngineError(f"{fname} is a built-in function; choose another name")
        sig = inspect.signature(fn)
        try:
            hints = inspect.get_annotations(fn, eval_str=True)
        except Exception:  # noqa: BLE001
            hints = {}
        params = []
        for p in sig.parameters.values():
            if p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD):
                raise EngineError(f"{fname}: *args/**kwargs are not supported in custom functions")
            params.append(_PY_TO_DUCK.get(hints.get(p.name), "VARCHAR"))
        ret = _PY_TO_DUCK.get(hints.get("return"), "VARCHAR")
        sql_name = "ps_fn_" + re.sub(r"\W", "_", fname.lower())
        con = self.wb.con
        with self.wb.lock:
            self.wb.flush()
            try:
                con.remove_function(sql_name)
            except Exception:  # noqa: BLE001 - not registered yet
                pass
            if vectorized:
                import pyarrow as pa

                def impl(*cols, _f=fn):
                    import pandas as pd

                    res = _f(*[c.to_pandas() for c in cols])
                    if isinstance(res, pd.Series):
                        res = res.astype(object).where(pd.notna(res), None).tolist()
                    return pa.array(res)

                # DuckDB needs a function with exactly one positional parameter per argument
                argnames = ", ".join(f"a{i}" for i in range(len(params)))
                ns: dict = {"_impl": impl}
                exec(f"def arrow_fn({argnames}):\n    return _impl({argnames})\n", ns)
                con.create_function(
                    sql_name,
                    ns["arrow_fn"],
                    params,
                    ret,
                    type="arrow",
                    null_handling="special",
                    exception_handling="return_null",
                    side_effects=False,
                )
            else:
                con.create_function(
                    sql_name,
                    fn,
                    params,
                    ret,
                    type="native",
                    null_handling="special",
                    exception_handling="return_null",
                    side_effects=False,
                )
        self.functions[fname] = {
            "name": fname,
            "sql": sql_name,
            "params": [_DUCK_TO_TYP[t] for t in params],
            "param_names": list(sig.parameters),
            "ret": _DUCK_TO_TYP[ret],
            "doc": (inspect.getdoc(fn) or "").split("\n")[0],
            "script_id": script_id,
            "vectorized": vectorized,
        }
        self.wb.custom_functions = self.functions

    def unregister_script_functions(self, script_id: str):
        for name, meta in list(self.functions.items()):
            if meta.get("script_id") == script_id:
                try:
                    self.wb.flush()
                    self.wb.con.remove_function(meta["sql"])
                except Exception:  # noqa: BLE001
                    pass
                del self.functions[name]
        self.wb.custom_functions = self.functions

    def load_functions(self, script: dict) -> list[str]:
        """Execute a function library so its @ps.function definitions register."""
        self.unregister_script_functions(script["id"])
        run = ScriptRun(script["id"], script.get("name", "functions"))
        api = Api(self.wb, self, run)
        ns = {"ps": api, "__name__": "__pysheets_functions__"}
        try:
            exec(compile(script.get("code", ""), f"<{script.get('name', 'functions')}>", "exec"), ns)
        except Exception as e:
            raise EngineError(f"{script.get('name')}: {type(e).__name__}: {e}") from e
        with self.wb.lock:
            self.wb.recalc(None)
            self.wb._touch()
        return api.functions_defined

    def load_all_functions(self) -> dict:
        loaded, errors = [], []
        for s in self.scripts:
            if s.get("kind") == "functions":
                try:
                    loaded += self.load_functions(s)
                except EngineError as e:
                    errors.append(str(e))
        self.trusted = True
        return {"functions": loaded, "errors": errors}

    # -- running -------------------------------------------------------------------------

    def google(self):
        """Client for the signed-in Google account (re-checked each call, so sign-out takes effect)."""
        from .connectors.google import GoogleClient

        return GoogleClient.connect()

    def start(self, script_id: str | None = None, code: str | None = None, name: str = "Script") -> ScriptRun:
        if not self.trusted:
            raise EngineError("Enable scripts for this workbook first")
        script = (
            self.get(script_id)
            if script_id
            else {"id": None, "name": name, "code": code or "", "kind": "macro"}
        )
        if code is not None:
            script = dict(script, code=code)
        run = ScriptRun(script.get("id"), script.get("name", name))
        self.runs[run.id] = run
        for old in list(self.runs.values())[:-30]:
            self.runs.pop(old.id, None)
        kind = script.get("kind", "macro")
        target = self._run_file if kind == "file" else self._run_code
        t = threading.Thread(target=target, args=(run, script), name=f"pysheets-{run.id}", daemon=True)
        run.thread = t
        _install_capture()
        t.start()
        return run

    def _run_code(self, run: ScriptRun, script: dict):
        assert _OUT is not None and _ERR is not None
        tid = threading.get_ident()
        _OUT.targets[tid] = run
        _ERR.targets[tid] = run
        api = Api(self.wb, self, run)
        ns: dict[str, Any] = {"ps": api, "__name__": "__main__"}
        try:
            import pandas as pd

            ns["pd"] = pd
        except ImportError:  # pragma: no cover
            pass
        import duckdb

        ns["duckdb"] = duckdb
        try:
            code = compile(script.get("code", ""), f"<{script.get('name', 'script')}>", "exec")
            exec(code, ns)
            if script.get("kind") == "functions":
                with self.wb.lock:
                    self.wb.recalc(None)
                    self.wb._touch()
                run.emit(f"Registered: {', '.join(api.functions_defined) or '(no functions)'}\n", "sys")
            run.finish("done")
        except KeyboardInterrupt:
            run.finish("cancelled", "Stopped")
        except SystemExit as e:
            run.finish("done" if not e.code else "error", None if not e.code else f"exit {e.code}")
        except Exception as e:  # noqa: BLE001 - reported to the user
            tb = traceback.format_exc()
            # hide PySheets' own frames
            lines = [ln for ln in tb.splitlines() if "pysheets/scripting.py" not in ln.replace("\\", "/")]
            run.emit("\n".join(lines) + "\n", "err")
            run.finish("error", f"{type(e).__name__}: {e}")
        finally:
            _OUT.targets.pop(tid, None)
            _ERR.targets.pop(tid, None)
            if api._con is not None:
                try:
                    api._con.close()
                except Exception:  # noqa: BLE001
                    pass
            self.wb.progress = {"active": False}
            with self.wb.lock:
                self.wb._touch()

    def _run_file(self, run: ScriptRun, script: dict):
        path = script.get("path") or ""
        if not os.path.isfile(path):
            run.emit(f"File not found: {path}\n", "err")
            run.finish("error", "File not found")
            return
        import shlex

        args = shlex.split(script.get("args") or "", posix=os.name != "nt")
        try:
            code = _stream_process(
                _python_cmd(script.get("python")) + [path, *args],
                script.get("cwd") or os.path.dirname(os.path.abspath(path)),
                run,
            )
            run.finish("done" if code == 0 else "error", None if code == 0 else f"exit code {code}")
        except Exception as e:  # noqa: BLE001
            run.emit(f"{type(e).__name__}: {e}\n", "err")
            run.finish("error", str(e))

    def cancel(self, run_id: str) -> dict:
        run = self.runs.get(run_id)
        if run is None or run.status != "running":
            return {"ok": False}
        if run.proc is not None:
            run.proc.terminate()
        if run.cursor is not None:
            try:
                run.cursor.interrupt()
            except Exception:  # noqa: BLE001
                pass
        if run.thread is not None and run.thread.ident and run.proc is None:
            ctypes.pythonapi.PyThreadState_SetAsyncExc(
                ctypes.c_ulong(run.thread.ident), ctypes.py_object(KeyboardInterrupt)
            )
        return {"ok": True}

    def status(self, run_id: str, since: int = 0) -> dict:
        run = self.runs.get(run_id)
        if run is None:
            raise EngineError("Unknown run")
        out = run.to_dict(since)
        out["version"] = self.wb.version
        return out

    def describe(self) -> dict:
        return {
            "scripts": self.scripts,
            "functions": list(self.functions.values()),
            "trusted": self.trusted,
            "running": [r.to_dict(len(r.lines)) for r in self.runs.values() if r.status == "running"],
        }


def run_python_file(argv: list[str]) -> int:
    """``PySheets.exe --run-python script.py args`` (bundled interpreter for file scripts)."""
    import runpy

    if not argv:
        print("usage: --run-python script.py [args...]")
        return 2
    # a windowed (GUI) build may start without Python-level std streams even when the parent passed pipes
    for name, fd in (("stdout", 1), ("stderr", 2)):
        if getattr(sys, name) is None:
            try:
                setattr(
                    sys, name, open(fd, "w", encoding="utf-8", errors="replace", buffering=1, closefd=False)
                )
            except OSError:
                setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))
    sys.argv = list(argv)
    sys.path.insert(0, os.path.dirname(os.path.abspath(argv[0])))
    try:
        runpy.run_path(argv[0], run_name="__main__")
    except SystemExit as e:
        return int(e.code or 0) if not isinstance(e.code, str) else 1
    return 0


_ = norm_duck
