"""JSON-RPC surface used by the UI.  Every method takes (workbook, params)."""

from __future__ import annotations

import logging
import os
import traceback
from collections.abc import Callable
from typing import Any

from .engine import features as F
from .engine import io as IO
from .engine.workbook import EngineError, Workbook
from .formula.compiler import ALL_FUNCTIONS, FormulaError
from .formula.parser import FormulaSyntaxError

log = logging.getLogger("pysheets")

Handler = Callable[[Workbook, dict], Any]
METHODS: dict[str, tuple[Handler, bool]] = {}


def rpc(name: str, mutates: bool = True):
    def deco(fn: Handler) -> Handler:
        METHODS[name] = (fn, mutates)
        return fn

    return deco


def dispatch(wb: Workbook, method: str, params: dict, ctx: dict | None = None) -> dict:
    entry = METHODS.get(method)
    if entry is None:
        return {"ok": False, "error": f"Unknown method {method}"}
    fn, mutates = entry
    with wb.lock:
        wb._pending = None
        try:
            if ctx is not None:
                params = dict(params, __ctx=ctx)
            result = fn(wb, params)
            wb._pending = None
            out = {"ok": True, "result": result}
            if mutates:
                out["info"] = wb.info()
            return out
        except (EngineError, FormulaError, FormulaSyntaxError, ValueError, KeyError, IndexError) as e:
            _rollback(wb)
            msg = e.args[0] if isinstance(e, KeyError) and e.args else str(e)
            return {
                "ok": False,
                "error": str(msg).split("\n")[0][:500],
                "info": wb.info() if mutates else None,
            }
        except Exception as e:  # pragma: no cover - unexpected engine failures
            log.error("RPC %s failed: %s\n%s", method, e, traceback.format_exc())
            _rollback(wb)
            return {
                "ok": False,
                "error": f"{type(e).__name__}: {str(e).splitlines()[0][:400] if str(e) else ''}",
                "info": wb.info() if mutates else None,
            }


def _rollback(wb: Workbook):
    if wb._pending is not None:
        try:
            wb.abort_pending()
        except Exception:  # pragma: no cover
            log.exception("rollback failed")
    wb._pending = None


# ---------------------------------------------------------------- workbook


@rpc("workbook.info", mutates=False)
def _info(wb, p):
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
        raise EngineError("Choose where to save the workbook")
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


@rpc("dialog.open", mutates=False)
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
    res = win.create_file_dialog(
        getattr(webview, "FileDialog", webview).OPEN
        if hasattr(webview, "FileDialog")
        else webview.OPEN_DIALOG,
        allow_multiple=bool(p.get("multiple")),
        file_types=types,
    )
    return {"paths": list(res) if res else []}


@rpc("dialog.save", mutates=False)
def _dialog_save(wb, p):
    ctx = p.get("__ctx") or {}
    win = ctx.get("window")
    if win is None:
        return {"unsupported": True}
    import webview

    kind = webview.FileDialog.SAVE if hasattr(webview, "FileDialog") else webview.SAVE_DIALOG
    res = win.create_file_dialog(
        kind,
        save_filename=p.get("name") or "export.xlsx",
        file_types=tuple(p.get("types") or ("All files (*.*)",)),
    )
    if not res:
        return {"path": None}
    return {"path": res if isinstance(res, str) else res[0]}
