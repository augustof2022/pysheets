"""JSON-RPC surface used by the UI.  Every method takes (workbook, params)."""

from __future__ import annotations

import logging
import os
import traceback
from collections.abc import Callable
from typing import Any

from .connectors.google import GoogleClient, GoogleError
from .engine import features as F
from .engine import io as IO
from .engine import links as L
from .engine.workbook import EngineError, Workbook
from .formula.compiler import ALL_FUNCTIONS, FormulaError
from .formula.parser import FormulaSyntaxError

log = logging.getLogger("pysheets")

Handler = Callable[[Workbook, dict], Any]
METHODS: dict[str, tuple[Handler, bool, bool]] = {}


def rpc(name: str, mutates: bool = True, lock: bool = True):
    """Register an RPC method.  ``lock=False`` handlers take the workbook lock themselves
    (used for long network work such as Google Sheets downloads and Python scripts)."""

    def deco(fn: Handler) -> Handler:
        METHODS[name] = (fn, mutates, lock)
        return fn

    return deco


def dispatch(wb: Workbook, method: str, params: dict, ctx: dict | None = None) -> dict:
    entry = METHODS.get(method)
    if entry is None:
        return {"ok": False, "error": f"Unknown method {method}"}
    fn, mutates, locked = entry
    if ctx is not None:
        params = dict(params, __ctx=ctx)
    if locked:
        with wb.lock:
            return _run(wb, fn, params, mutates)
    return _run(wb, fn, params, mutates, locked=False)


def _info(wb: Workbook) -> dict:
    with wb.lock:
        return wb.info()


def _run(wb: Workbook, fn: Handler, params: dict, mutates: bool, locked: bool = True) -> dict:
    if locked:
        wb._pending = None
    try:
        result = fn(wb, params)
        if locked:
            wb._pending = None
        out = {"ok": True, "result": result}
        if mutates:
            out["info"] = _info(wb)
        return out
    except (
        EngineError,
        FormulaError,
        FormulaSyntaxError,
        ValueError,
        KeyError,
        IndexError,
        GoogleError,
    ) as e:
        if locked:
            _rollback(wb)
        msg = e.args[0] if isinstance(e, KeyError) and e.args else str(e)
        out = {"ok": False, "error": str(msg).split("\n")[0][:500], "info": _info(wb) if mutates else None}
        if getattr(e, "code", None):
            out["code"] = e.code
        return out
    except Exception as e:  # pragma: no cover - unexpected engine failures
        log.error("RPC failed: %s\n%s", e, traceback.format_exc())
        if locked:
            _rollback(wb)
        first = str(e).splitlines()[0][:400] if str(e) else ""
        return {"ok": False, "error": f"{type(e).__name__}: {first}", "info": _info(wb) if mutates else None}


def _rollback(wb: Workbook):
    if wb._pending is not None:
        try:
            wb.abort_pending()
        except Exception:  # pragma: no cover
            log.exception("rollback failed")
    wb._pending = None


# ---------------------------------------------------------------- workbook


@rpc("workbook.info", mutates=False)
def _workbook_info(wb, p):
    return wb.info()


@rpc("workbook.new")
def _new(wb, p):
    return IO.new_workbook(wb)


@rpc("workbook.open")
def _open(wb, p):
    return IO.open_workbook(wb, p["path"])


@rpc("workbook.save")
def _save(wb, p):
    path = p.get("path") or wb.path
    if not path:
        # no location chosen (e.g. browser mode): save into Documents/PySheets
        from .paths import safe_name, unique_path, workbooks_dir

        path = str(unique_path(workbooks_dir(), safe_name(wb.title) + ".pysheets"))
    if not path.lower().endswith(".pysheets"):
        path += ".pysheets"
    return IO.save_workbook(wb, path)


@rpc("workbook.title")
def _title(wb, p):
    wb.title = p["title"].strip() or wb.title
    wb.dirty = True
    return {"title": wb.title}


@rpc("undo")
def _undo(wb, p):
    return wb.undo()


@rpc("redo")
def _redo(wb, p):
    return wb.redo()


@rpc("functions", mutates=False)
def _functions(wb, p):
    return ALL_FUNCTIONS


# ------------------------------------------------------------------ sheets


@rpc("sheet.add")
def _sheet_add(wb, p):
    sh = wb.add_sheet(p.get("name"), index=p.get("index"))
    return {"sheet": sh.id}


@rpc("sheet.delete")
def _sheet_delete(wb, p):
    wb.delete_sheet(p["sheet"])


@rpc("sheet.duplicate")
def _sheet_dup(wb, p):
    return {"sheet": wb.duplicate_sheet(p["sheet"]).id}


@rpc("sheet.rename")
def _sheet_rename(wb, p):
    wb.rename_sheet(p["sheet"], p["name"])


@rpc("sheet.move")
def _sheet_move(wb, p):
    wb.move_sheet(p["sheet"], p["index"])


@rpc("sheet.props")
def _sheet_props(wb, p):
    wb.set_sheet_props(p["sheet"], **{k: v for k, v in p.items() if k not in ("sheet", "__ctx")})


@rpc("sheet.viewport", mutates=False)
def _viewport(wb, p):
    return wb.viewport(p["sheet"], p["r0"], p["r1"], p["c0"], p["c1"])


# ------------------------------------------------------------------- cells


@rpc("cell.get", mutates=False)
def _cell_get(wb, p):
    return wb.get_cell(p["sheet"], p["row"], p["col"])


@rpc("cell.set")
def _cell_set(wb, p):
    return wb.set_cell(p["sheet"], p["row"], p["col"], p.get("value"), fill=p.get("fill"))


@rpc("range.set")
def _range_set(wb, p):
    return wb.set_range(p["sheet"], p["row"], p["col"], p["values"], label=p.get("label", "Paste"))


@rpc("range.clear")
def _range_clear(wb, p):
    return wb.clear_range(p["sheet"], p["r1"], p["c1"], p["r2"], p["c2"], p.get("what", "contents"))


@rpc("range.text", mutates=False)
def _range_text(wb, p):
    return wb.get_range_text(p["sheet"], p["r1"], p["c1"], p["r2"], p["c2"])


@rpc("range.paste")
def _range_paste(wb, p):
    return wb.paste_internal(p["sheet"], p["src"], p["row"], p["col"], p.get("mode", "all"))


@rpc("range.fill")
def _range_fill(wb, p):
    return wb.fill(p["sheet"], p["src"], p["dst"])


@rpc("rows.insert")
def _rows_insert(wb, p):
    return wb.insert_rows(p["sheet"], p["row"], p.get("count", 1))


@rpc("rows.delete")
def _rows_delete(wb, p):
    return wb.delete_rows(p["sheet"], p["r1"], p["r2"])


@rpc("cols.insert")
def _cols_insert(wb, p):
    return wb.insert_cols(p["sheet"], p["col"], p.get("count", 1))


@rpc("cols.delete")
def _cols_delete(wb, p):
    return wb.delete_cols(p["sheet"], p["c1"], p["c2"])


@rpc("cols.move")
def _cols_move(wb, p):
    return wb.move_cols(p["sheet"], p["c1"], p["c2"], p["to"])


@rpc("col.props")
def _col_props(wb, p):
    kw = {}
    if "width" in p:
        kw["width"] = p["width"]
    if "fmt" in p:
        kw["fmt"] = p["fmt"]
    return wb.set_column_props(p["sheet"], p.get("col", 0), cols=p.get("cols"), **kw)


@rpc("col.type")
def _col_type(wb, p):
    return F.set_column_type(wb, p["sheet"], p["col"], p["type"])


@rpc("col.profile", mutates=False)
def _col_profile(wb, p):
    return F.column_profile(wb, p["sheet"], p["col"])


# ------------------------------------------------------- sort & filter


@rpc("sort")
def _sort(wb, p):
    return wb.sort(p["sheet"], p["keys"])


@rpc("filter.set")
def _filter_set(wb, p):
    return wb.set_filter(p["sheet"], p["col"], p.get("spec"))


@rpc("filter.toggle")
def _filter_toggle(wb, p):
    return wb.toggle_filter(p["sheet"], p.get("on"))


@rpc("filter.values", mutates=False)
def _filter_values(wb, p):
    return wb.column_values(p["sheet"], p["col"], p.get("search", ""), p.get("limit", 500))


# --------------------------------------------------------------- features


@rpc("style.set")
def _style(wb, p):
    return F.set_style(wb, p["sheet"], p["rects"], p["patch"])


@rpc("style.borders")
def _borders(wb, p):
    return F.set_borders(
        wb, p["sheet"], p["rects"], p["mode"], p.get("line", "thin"), p.get("color", "#1f2937")
    )


@rpc("cells.merge")
def _merge(wb, p):
    return F.merge_cells(wb, p["sheet"], p["rects"], p.get("mode", "all"), bool(p.get("dry_run")))


@rpc("cells.merge_check", mutates=False)
def _merge_check(wb, p):
    return F.merge_cells(wb, p["sheet"], p["rects"], p.get("mode", "all"), dry_run=True)


@rpc("cf.add")
def _cf_add(wb, p):
    return F.add_cond_format(wb, p["sheet"], p["rule"])


@rpc("cf.delete")
def _cf_del(wb, p):
    return F.delete_cond_format(wb, p["sheet"], p["id"])


@rpc("dv.add")
def _dv_add(wb, p):
    return F.add_validation(wb, p["sheet"], p["rule"])


@rpc("dv.delete")
def _dv_del(wb, p):
    return F.delete_validation(wb, p["sheet"], p["id"])


@rpc("dv.report", mutates=False)
def _dv_report(wb, p):
    return F.validation_report(wb, p["sheet"], p.get("id"))


@rpc("dv.options", mutates=False)
def _dv_options(wb, p):
    return F.validation_options(wb, p["sheet"], p["id"])


@rpc("find", mutates=False)
def _find(wb, p):
    return F.find(
        wb,
        p["sheet"],
        p["query"],
        p.get("match_case", False),
        p.get("whole", False),
        p.get("regex", False),
        p.get("row", 0),
        p.get("col", -1),
        p.get("backwards", False),
        p.get("all_sheets", False),
    )


@rpc("replace_all")
def _replace_all(wb, p):
    return F.replace_all(
        wb,
        p["sheet"],
        p["query"],
        p.get("replacement", ""),
        p.get("match_case", False),
        p.get("whole", False),
        p.get("regex", False),
        p.get("all_sheets", False),
    )


@rpc("stats", mutates=False)
def _stats(wb, p):
    return F.selection_stats(wb, p["sheet"], p["rects"])


@rpc("dups")
def _dups(wb, p):
    return F.duplicates(
        wb, p["sheet"], p["cols"], p.get("action", "count"), p.get("ignore_case", True), p.get("trim", True)
    )


@rpc("clean.text")
def _clean(wb, p):
    return F.transform_text(wb, p["sheet"], p["rects"], p["op"])


@rpc("split.column")
def _split(wb, p):
    return F.split_column(wb, p["sheet"], p["col"], p.get("delimiter", ","))


@rpc("pivot.create")
def _pivot_create(wb, p):
    return F.create_pivot(wb, p["spec"], p.get("target"))


@rpc("pivot.preview", mutates=False)
def _pivot_preview(wb, p):
    return F.pivot_preview(wb, p["spec"])


# --------------------------------------------------------------------- io


@rpc("io.preview", mutates=False)
def _io_preview(wb, p):
    return IO.preview(wb, p["path"], p.get("options"))


@rpc("io.import")
def _io_import(wb, p):
    return IO.import_file(wb, p["path"], p.get("options"))


@rpc("io.export", mutates=False)
def _io_export(wb, p):
    return IO.export(
        wb, p["path"], p.get("format"), p.get("scope", "sheet"), p.get("sheet"), p.get("options")
    )


@rpc("io.sql_tables", mutates=False)
def _io_sql_tables(wb, p):
    return IO.list_sql_url_tables(p["url"])


@rpc("io.sql_import")
def _io_sql_import(wb, p):
    return IO.import_sql_url(wb, p["url"], p["query"], p.get("name") or "Query")


@rpc("io.sql_export", mutates=False)
def _io_sql_export(wb, p):
    return IO.export_sql_url(
        wb, p["url"], p.get("scope", "sheet"), p.get("sheet"), p.get("if_exists", "replace")
    )


@rpc("io.temp_path", mutates=False)
def _io_temp(wb, p):
    name = os.path.basename(p.get("name") or "export.xlsx")
    return {"path": os.path.join(wb._tmpdir, name)}


# ----------------------------------------------------------------- dialogs


@rpc("paths.folders", mutates=False, lock=False)
def _paths_folders(wb, p):
    from .paths import exports_dir, workbooks_dir

    return {"workbooks": str(workbooks_dir()), "exports": str(exports_dir())}


@rpc("paths.reveal", mutates=False, lock=False)
def _paths_reveal(wb, p):
    """Show the PySheets folder (or a saved file) in Explorer."""
    from pathlib import Path

    from .paths import exports_dir, reveal, workbooks_dir

    target = p.get("path")
    if target == "exports":
        target = exports_dir()
    elif not target:
        target = workbooks_dir()
    reveal(Path(target))
    return {"path": str(target)}


@rpc("dialog.open", mutates=False, lock=False)
def _dialog_open(wb, p):
    ctx = p.get("__ctx") or {}
    win = ctx.get("window")
    if win is None:
        return {"unsupported": True}
    import webview

    types = tuple(
        p.get("types")
        or (
            "Spreadsheets (*.csv;*.tsv;*.txt;*.xlsx;*.xlsm;*.xls;*.parquet;*.json;*.pysheets;"
            "*.sqlite;*.db;*.duckdb)",
            "All files (*.*)",
        )
    )
    from .paths import workbooks_dir

    res = win.create_file_dialog(
        getattr(webview, "FileDialog", webview).OPEN
        if hasattr(webview, "FileDialog")
        else webview.OPEN_DIALOG,
        directory=str(p.get("directory") or workbooks_dir()),
        allow_multiple=bool(p.get("multiple")),
        file_types=types,
    )
    return {"paths": list(res) if res else []}


@rpc("dialog.folder", mutates=False, lock=False)
def _dialog_folder(wb, p):
    ctx = p.get("__ctx") or {}
    win = ctx.get("window")
    if win is None:
        return {"unsupported": True}
    import webview

    kind = webview.FileDialog.FOLDER if hasattr(webview, "FileDialog") else webview.FOLDER_DIALOG
    res = win.create_file_dialog(kind)
    return {"paths": list(res) if res else []}


@rpc("dialog.save", mutates=False, lock=False)
def _dialog_save(wb, p):
    ctx = p.get("__ctx") or {}
    win = ctx.get("window")
    if win is None:
        return {"unsupported": True}
    import webview

    from .paths import exports_dir, workbooks_dir

    name = p.get("name") or "export.xlsx"
    # workbooks live in Documents/PySheets, exports in Documents/PySheets/Exports
    folder = p.get("directory") or (workbooks_dir() if name.lower().endswith(".pysheets") else exports_dir())
    kind = webview.FileDialog.SAVE if hasattr(webview, "FileDialog") else webview.SAVE_DIALOG
    res = win.create_file_dialog(
        kind,
        directory=str(folder),
        save_filename=name,
        file_types=tuple(p.get("types") or ("All files (*.*)",)),
    )
    if not res:
        return {"path": None}
    return {"path": res if isinstance(res, str) else res[0]}


# ------------------------------------------------------------ google sheets


@rpc("google.status", mutates=False, lock=False)
def _g_status(wb, p):
    return GoogleClient.status(verify=bool(p.get("verify")))


@rpc("google.setup", mutates=False, lock=False)
def _g_setup(wb, p):
    return GoogleClient.setup(
        p.get("folder"), p.get("client_secret"), p.get("token"), p.get("service_account")
    )


@rpc("google.sign_in", mutates=False, lock=False)
def _g_sign_in(wb, p):
    from .connectors.google import SIGN_IN

    SIGN_IN.start()
    return GoogleClient.status()


@rpc("google.sign_in_reopen", mutates=False, lock=False)
def _g_sign_in_reopen(wb, p):
    from .connectors.google import SIGN_IN

    SIGN_IN.reopen()
    return GoogleClient.status()


@rpc("google.sign_in_cancel", mutates=False, lock=False)
def _g_sign_in_cancel(wb, p):
    from .connectors.google import SIGN_IN

    SIGN_IN.cancel()
    return GoogleClient.status()


@rpc("google.sign_out", mutates=False, lock=False)
def _g_sign_out(wb, p):
    return GoogleClient.sign_out()


@rpc("gsheets.tabs", mutates=False, lock=False)
def _g_tabs(wb, p):
    from .connectors.google import extract_sheet_id

    return GoogleClient.connect().spreadsheet_info(extract_sheet_id(p["url"]))


@rpc("gsheets.import", lock=False)
def _g_import(wb, p):
    return L.import_tabs(wb, p["url"], p.get("tabs") or [], typed=p.get("typed", True))


@rpc("gsheets.consolidate", lock=False)
def _g_consolidate(wb, p):
    return L.consolidate_gsheets(wb, p["spec"])


@rpc("links.refresh", lock=False)
def _links_refresh(wb, p):
    return L.refresh(wb, p["sheet"])


@rpc("links.refresh_all", lock=False)
def _links_refresh_all(wb, p):
    return L.refresh_all(wb)


# ------------------------------------------------------------------ scripts


@rpc("scripts.list", mutates=False, lock=False)
def _scripts_list(wb, p):
    return wb.scripting.describe()


@rpc("scripts.templates", mutates=False, lock=False)
def _scripts_templates(wb, p):
    from .script_templates import TEMPLATES

    return TEMPLATES


@rpc("scripts.save", lock=False)
def _scripts_save(wb, p):
    return wb.scripting.save(p["script"])


@rpc("scripts.delete", lock=False)
def _scripts_delete(wb, p):
    wb.scripting.delete(p["id"])


@rpc("scripts.trust", lock=False)
def _scripts_trust(wb, p):
    return wb.scripting.load_all_functions()


@rpc("scripts.run", mutates=False, lock=False)
def _scripts_run(wb, p):
    run = wb.scripting.start(p.get("id"), p.get("code"), p.get("name") or "Script")
    return run.to_dict()


@rpc("scripts.status", mutates=False, lock=False)
def _scripts_status(wb, p):
    return wb.scripting.status(p["run"], int(p.get("since") or 0))


@rpc("scripts.cancel", mutates=False, lock=False)
def _scripts_cancel(wb, p):
    return wb.scripting.cancel(p["run"])
