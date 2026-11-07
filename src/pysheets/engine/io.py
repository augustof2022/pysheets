"""Import / export: CSV, TSV, Excel, Parquet, JSON, SQLite, DuckDB, any SQLAlchemy database,
plus the native ``.pysheets`` workbook format (a DuckDB file)."""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import sqlite3
import time
from collections.abc import Iterable, Iterator

import duckdb

from .model import Formula, Sheet
from .values import to_json, value_to_text
from .workbook import EngineError, Workbook, q

CSV_EXT = {".csv", ".tsv", ".txt", ".tab", ".psv"}
EXCEL_EXT = {".xlsx", ".xlsm", ".xls", ".xlsb", ".ods"}
PARQUET_EXT = {".parquet", ".pq"}
JSON_EXT = {".json", ".ndjson", ".jsonl"}
SQLITE_EXT = {".sqlite", ".sqlite3", ".db", ".db3"}
DUCKDB_EXT = {".duckdb", ".ddb"}
NATIVE_EXT = ".pysheets"
EXCEL_MAX_ROWS = 1_048_576


def lit(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def detect_kind(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext in CSV_EXT:
        return "csv"
    if ext in EXCEL_EXT:
        return "excel"
    if ext in PARQUET_EXT:
        return "parquet"
    if ext in JSON_EXT:
        return "json"
    if ext in DUCKDB_EXT:
        return "duckdb"
    if ext == NATIVE_EXT:
        return "native"
    if ext in SQLITE_EXT:
        with open(path, "rb") as fh:
            head = fh.read(16)
        return "sqlite" if head.startswith(b"SQLite format 3") else "duckdb"
    raise EngineError(f"Unsupported file type: {ext or path}")


def _base_name(path: str) -> str:
    return os.path.splitext(os.path.basename(path))[0] or "Data"


# --------------------------------------------------------------- progress


class Progress:
    def __init__(self, wb: Workbook, label: str):
        self.wb = wb
        self.label = label

    def __enter__(self):
        self.wb.progress = {"active": True, "label": self.label, "done": 0, "total": None, "started": time.time()}
        return self

    def update(self, done: int, total: int | None = None, label: str | None = None):
        p = self.wb.progress
        p["done"] = done
        if total is not None:
            p["total"] = total
        if label:
            p["label"] = label

    def __exit__(self, *exc):
        self.wb.progress = {"active": False}
        return False


# ----------------------------------------------------------------- preview


def preview(wb: Workbook, path: str, options: dict | None = None) -> dict:
    """Peek at a file before importing: tabs/tables and the first rows."""
    options = options or {}
    kind = detect_kind(path)
    out: dict = {"kind": kind, "path": path, "name": _base_name(path), "size": os.path.getsize(path)}
    if kind == "csv":
        rel = _csv_relation(path, options)
        cur = wb.con.cursor()
        try:
            res = cur.execute(f"SELECT * FROM {rel} LIMIT 25")
            cols = [d[0] for d in res.description]
            rows = res.fetchall()
            sniff = _sniff(cur, path)
        finally:
            cur.close()
        out["tables"] = [{"name": out["name"], "columns": cols, "rows": [[to_json(v) for v in r] for r in rows]}]
        out["sniff"] = sniff
    elif kind == "excel":
        from python_calamine import CalamineWorkbook

        book = CalamineWorkbook.from_path(path)
        tables = []
        for name in book.sheet_names:
            sheet = book.get_sheet_by_name(name)
            rows = []
            for i, r in enumerate(sheet.iter_rows()):
                rows.append([to_json(_cal(v)) for v in r])
                if i >= 25:
                    break
            tables.append({"name": name, "columns": [], "rows": rows,
                           "dims": [getattr(sheet, "total_height", None), getattr(sheet, "total_width", None)]})
        out["tables"] = tables
    elif kind in ("parquet", "json"):
        fn = "read_parquet" if kind == "parquet" else "read_json_auto"
        cur = wb.con.cursor()
        try:
            res = cur.execute(f"SELECT * FROM {fn}({lit(path)}) LIMIT 25")
            cols = [d[0] for d in res.description]
            rows = res.fetchall()
            total = None
            if kind == "parquet":
                total = cur.execute(f"SELECT sum(num_rows) FROM parquet_file_metadata({lit(path)})").fetchone()[0]
        finally:
            cur.close()
        out["tables"] = [{"name": out["name"], "columns": cols, "rows": [[to_json(v) for v in r] for r in rows],
                          "total": total}]
    elif kind in ("sqlite", "duckdb"):
        out["tables"] = [{"name": t, "columns": [], "rows": []} for t in list_db_tables(path, kind)]
    elif kind == "native":
        out["tables"] = []
    return out


def _sniff(cur, path: str) -> dict:
    try:
        r = cur.execute(f"SELECT Delimiter, HasHeader FROM sniff_csv({lit(path)})").fetchone()
        return {"delimiter": r[0], "header": bool(r[1])}
    except duckdb.Error:
        return {}


def _cal(v):
    if v == "":
        return None
    if isinstance(v, dt.time):
        return v.isoformat()
    if isinstance(v, dt.timedelta):
        return str(v)
    return v


# ------------------------------------------------------------------ import


def _csv_relation(path: str, options: dict, all_varchar: bool = False, lenient: bool = False) -> str:
    args = [lit(path)]
    if options.get("delimiter"):
        args.append(f"delim={lit(options['delimiter'])}")
    if options.get("header") is not None:
        args.append(f"header={'true' if options['header'] else 'false'}")
    args.append("sample_size=200000")
    if all_varchar:
        args.append("all_varchar=true")
    if lenient:
        args.append("ignore_errors=true")
        args.append("null_padding=true")
        args.append("strict_mode=false")
    if options.get("encoding"):
        args.append(f"encoding={lit(options['encoding'])}")
    return f"read_csv({', '.join(args)})"


def import_file(wb: Workbook, path: str, options: dict | None = None) -> dict:
    options = options or {}
    if not os.path.exists(path):
        raise EngineError(f"File not found: {path}")
    kind = options.get("kind") or detect_kind(path)
    name = options.get("name") or _base_name(path)
    created: list[str] = []
    with Progress(wb, f"Importing {os.path.basename(path)}") as prog:
        if kind == "csv":
            header = options.get("header")
            if header is None:
                header = _sniff(wb.con, path).get("header", True)
            opts = dict(options, header=header)
            last_err = None
            for attempt in ({}, {"all_varchar": True}, {"all_varchar": True, "lenient": True}):
                try:
                    sh = wb.sheet_from_sql(name, f"SELECT * FROM {_csv_relation(path, opts, **attempt)}",
                                           header=header, source=path)
                    created.append(sh.id)
                    break
                except duckdb.Error as e:
                    last_err = e
                    if wb.undo_stack and wb._pending is wb.undo_stack[-1]:
                        wb.undo_stack.pop()
            else:
                raise EngineError(f"Could not read CSV: {str(last_err).splitlines()[0]}")
        elif kind == "parquet":
            sh = wb.sheet_from_sql(name, f"SELECT * FROM read_parquet({lit(path)})", source=path)
            created.append(sh.id)
        elif kind == "json":
            sh = wb.sheet_from_sql(name, f"SELECT * FROM read_json_auto({lit(path)})", source=path)
            created.append(sh.id)
        elif kind == "excel":
            created += _import_excel(wb, path, options, prog)
        elif kind in ("sqlite", "duckdb"):
            tables = options.get("tables") or list_db_tables(path, kind)[:1]
            query = options.get("query")
            if query:
                created.append(import_query(wb, path, kind, query, options.get("name") or "Query", prog))
            else:
                for t in tables:
                    created.append(import_query(wb, path, kind, None, t, prog, table=t))
        elif kind == "native":
            open_workbook(wb, path)
            return {"sheets": [s.id for s in wb.sheets], "opened": True}
        else:
            raise EngineError(f"Cannot import {kind}")
    return {"sheets": created}


def _import_excel(wb: Workbook, path: str, options: dict, prog: Progress) -> list[str]:
    from python_calamine import CalamineWorkbook

    book = CalamineWorkbook.from_path(path)
    wanted = options.get("sheets") or book.sheet_names
    created = []
    for name in wanted:
        sheet = book.get_sheet_by_name(name)
        prog.update(0, getattr(sheet, "total_height", None), f"Reading '{name}'")
        it = iter(sheet.iter_rows())
        header_opt = options.get("header")
        first = next(it, None)
        if first is None:
            created.append(wb.add_sheet(name).id)
            continue
        first = [_cal(v) for v in first]
        if header_opt is None:
            nonblank = [v for v in first if v is not None]
            header_opt = bool(nonblank) and all(isinstance(v, str) for v in nonblank)
        if header_opt:
            names = [value_to_text(v) if v is not None else "" for v in first]
            rows_iter: Iterable = ([_cal(v) for v in r] for r in it)
        else:
            names = [""] * len(first)
            rows_iter = _chain([first], ([_cal(v) for v in r] for r in it))
        sh = _rows_to_sheet(wb, name, names, rows_iter, prog, source=f"{path}#{name}", header=bool(header_opt))
        created.append(sh.id)
    return created


def _chain(a, b):
    yield from a
    yield from b


def _arrow_column(values: list):
    import pyarrow as pa

    kinds = set()
    for v in values:
        if v is None:
            continue
        if isinstance(v, bool):
            kinds.add("bool")
        elif isinstance(v, int):
            kinds.add("int")
        elif isinstance(v, float):
            kinds.add("float")
        elif isinstance(v, dt.datetime):
            kinds.add("ts")
        elif isinstance(v, dt.date):
            kinds.add("date")
        else:
            kinds.add("str")
        if len(kinds) > 2:
            break
    if not kinds:
        return pa.array(values, pa.string()), "null"
    if kinds <= {"int"}:
        try:
            return pa.array(values, pa.int64()), "int"
        except (OverflowError, pa.ArrowInvalid):
            return pa.array([float(v) if v is not None else None for v in values], pa.float64()), "float"
    if kinds <= {"int", "float"}:
        return pa.array([float(v) if v is not None else None for v in values], pa.float64()), "float"
    if kinds == {"bool"}:
        return pa.array(values, pa.bool_()), "bool"
    if kinds == {"date"}:
        return pa.array(values, pa.date32()), "date"
    if kinds <= {"date", "ts"}:
        return pa.array([v if isinstance(v, dt.datetime) or v is None else dt.datetime.combine(v, dt.time())
                         for v in values], pa.timestamp("us")), "ts"
    return pa.array([None if v is None else (v if isinstance(v, str) else value_to_text(v)) for v in values],
                    pa.string()), "str"


_RANK = {"null": 0, "bool": 1, "int": 2, "float": 3, "date": 4, "ts": 5, "str": 9}


def _merge_kind(a: str, b: str) -> str:
    if a == b or b == "null":
        return a
    if a == "null":
        return b
    if {a, b} <= {"int", "float"}:
        return "float"
    if {a, b} <= {"date", "ts"}:
        return "ts"
    return "str"


def _rows_to_sheet(wb: Workbook, name: str, names: list[str], rows: Iterator[list], prog: Progress | None,
                   source: str | None = None, header: bool = True, chunk: int = 50_000) -> Sheet:
    """Stream python rows into a new sheet (used by Excel and SQL imports)."""
    import pyarrow as pa

    tables = []
    kinds: list[str] = []
    width = len(names)
    buf: list[list] = []
    done = 0

    def flush():
        nonlocal buf, kinds, width
        if not buf:
            return
        w = max(width, max(len(r) for r in buf))
        width = w
        cols = []
        ks = []
        for j in range(w):
            arr, k = _arrow_column([r[j] if j < len(r) else None for r in buf])
            cols.append(arr)
            ks.append(k)
        kinds = [_merge_kind(kinds[j] if j < len(kinds) else "null", ks[j]) for j in range(w)]
        tables.append(cols)
        buf = []

    for r in rows:
        buf.append(r)
        if len(buf) >= chunk:
            done += len(buf)
            flush()
            if prog:
                prog.update(done)
    done += len(buf)
    flush()
    # unify chunk types
    target = {"null": pa.string(), "bool": pa.bool_(), "int": pa.int64(), "float": pa.float64(),
              "date": pa.date32(), "ts": pa.timestamp("us"), "str": pa.string()}
    while len(names) < width:
        names.append("")
    colnames = [f"__c{j}" for j in range(width)]
    batches = []
    for cols in tables:
        arrays = []
        for j in range(width):
            arr = cols[j] if j < len(cols) else pa.nulls(len(cols[0]), pa.string())
            want = target[kinds[j]]
            if arr.type != want:
                if want == pa.string():
                    arr = pa.array([None if v is None else value_to_text(v) for v in arr.to_pylist()], pa.string())
                else:
                    arr = arr.cast(want)
            arrays.append(arr)
        batches.append(pa.table(arrays, names=colnames))
    tbl = pa.concat_tables(batches) if batches else pa.table({c: pa.array([], pa.string()) for c in colnames})
    reg = f"__arrow_{int(time.time() * 1000)}"
    wb.register(reg, tbl)
    try:
        sh = wb.sheet_from_sql(name, f"SELECT * FROM {reg}", header=True, source=source)
    finally:
        wb.unregister(reg)
    for c, n in zip(sh.columns, names, strict=False):
        c.name = n if header else ""
    if not header:
        sh.frozen_rows = 0
    wb._autosize(sh)
    return sh


def list_db_tables(path: str, kind: str) -> list[str]:
    if kind == "sqlite":
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            return [r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        finally:
            con.close()
    con = duckdb.connect(path, read_only=True)
    try:
        return [f"{s}.{t}" if s != "main" else t for s, t in con.execute(
            "SELECT schema_name, table_name FROM duckdb_tables() UNION ALL "
            "SELECT schema_name, view_name FROM duckdb_views() WHERE NOT internal ORDER BY 1, 2").fetchall()
            if not t.startswith("__")]
    finally:
        con.close()


def import_query(wb: Workbook, path: str, kind: str, query: str | None, name: str, prog: Progress | None = None,
                 table: str | None = None) -> str:
    if kind == "duckdb":
        alias = "__src"
        wb.con.execute(f"ATTACH {lit(path)} AS {alias} (READ_ONLY)")
        try:
            if query:
                sql = query
                wb.con.execute(f"USE {alias}")
                try:
                    sh = wb.sheet_from_sql(name, sql, source=path)
                finally:
                    wb.con.execute("USE memory")
            else:
                parts = table.split(".")
                ref = ".".join(q(p) for p in parts)
                sh = wb.sheet_from_sql(name, f"SELECT * FROM {alias}.{ref}", source=f"{path}#{table}")
        finally:
            wb.con.execute(f"DETACH {alias}")
        return sh.id
    if kind == "sqlite":
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            cur = con.execute(query or f"SELECT * FROM {q(table)}")
            names = [d[0] for d in cur.description]
            sh = _rows_to_sheet(wb, name, names, _fetch_iter(cur), prog, source=path)
        finally:
            con.close()
        return sh.id
    raise EngineError(f"Unknown database kind {kind}")


def _fetch_iter(cur, n: int = 50_000):
    while True:
        rows = cur.fetchmany(n)
        if not rows:
            return
        for r in rows:
            yield list(r)


def import_sql_url(wb: Workbook, url: str, query: str, name: str = "Query") -> dict:
    """Import from any SQLAlchemy URL (PostgreSQL, MySQL, SQL Server, ...)."""
    if os.path.exists(url):
        kind = detect_kind(url)
        with Progress(wb, "Running query") as prog:
            return {"sheets": [import_query(wb, url, kind, query, name, prog)]}
    try:
        import sqlalchemy as sa
    except ImportError as e:
        raise EngineError("SQLAlchemy is not installed (pip install pysheets[sql])") from e
    engine = sa.create_engine(url)
    with Progress(wb, "Running query") as prog, engine.connect() as conn:
        res = conn.execution_options(stream_results=True).execute(sa.text(query))
        names = list(res.keys())
        sh = _rows_to_sheet(wb, name, names, _fetch_iter(res), prog, source=url.split("@")[-1])
    return {"sheets": [sh.id]}


def list_sql_url_tables(url: str) -> list[str]:
    if os.path.exists(url):
        return list_db_tables(url, detect_kind(url))
    import sqlalchemy as sa

    engine = sa.create_engine(url)
    insp = sa.inspect(engine)
    return insp.get_table_names() + insp.get_view_names()


# ------------------------------------------------------------------ export


def _export_names(sh: Sheet) -> list[str]:
    from ..formula.refs import col_to_letters

    seen: dict[str, int] = {}
    out = []
    for i, c in enumerate(sh.columns):
        n = c.name.strip() or col_to_letters(i)
        k = n.lower()
        if k in seen:
            seen[k] += 1
            n = f"{n}_{seen[k]}"
        else:
            seen[k] = 1
        out.append(n)
    return out


def _last_used_col(wb: Workbook, sh: Sheet) -> int:
    """Index of the last column that holds anything (blank sheets have 26 empty columns)."""
    if not sh.columns:
        return -1
    counts = wb.con.execute(
        f"SELECT {', '.join(f'count({q(c.id)})' for c in sh.columns)} FROM {q(sh.table)}").fetchone()
    last = -1
    for i, (c, n) in enumerate(zip(sh.columns, counts, strict=True)):
        if n or c.name:
            last = i
    return last


def _last_used_row(wb: Workbook, sh: Sheet, ncols: int) -> int:
    if ncols <= 0 or sh.nrows == 0:
        return 0
    cond = " OR ".join(f"{q(c.id)} IS NOT NULL" for c in sh.columns[:ncols])
    r = wb.con.execute(f"SELECT max(__rid) FROM {q(sh.table)} WHERE {cond}").fetchone()[0]
    return 0 if r is None else r + 1


def _select_for_export(wb: Workbook, sh: Sheet, visible_only: bool = False) -> tuple[str, list[str], list]:
    last = _last_used_col(wb, sh)
    cols = sh.columns[:last + 1]
    names = _export_names(sh)[:last + 1]
    nrows = _last_used_row(wb, sh, len(cols))
    if not cols:
        return "SELECT 1 WHERE FALSE", [], []
    sel = ", ".join(f"t.{q(c.id)} AS {q(n)}" for c, n in zip(cols, names, strict=True))
    where = f"t.__rid < {nrows}"
    if visible_only and sh.view_table:
        where += f" AND t.__rid IN (SELECT rid FROM {q(sh.view_table)})"
    return f"SELECT {sel} FROM {q(sh.table)} t WHERE {where} ORDER BY t.__rid", names, cols


def export(wb: Workbook, path: str, fmt: str | None = None, scope: str = "sheet", sheet_id: str | None = None,
           options: dict | None = None) -> dict:
    options = options or {}
    fmt = (fmt or os.path.splitext(path)[1].lstrip(".")).lower()
    sheets = wb.sheets if scope == "all" else [wb.sheet(sheet_id or wb.sheets[0].id)]
    visible = bool(options.get("visible_only"))
    written = []
    with Progress(wb, f"Exporting {os.path.basename(path)}") as prog:
        if fmt in ("csv", "tsv", "parquet", "json"):
            targets = _per_sheet_paths(path, sheets, fmt)
            for sh, out in targets:
                sql, _, _ = _select_for_export(wb, sh, visible)
                if fmt == "parquet":
                    o = "FORMAT PARQUET, COMPRESSION ZSTD"
                elif fmt == "json":
                    o = "FORMAT JSON, ARRAY true"
                else:
                    o = f"FORMAT CSV, HEADER, DELIMITER {lit(chr(9) if fmt == 'tsv' else options.get('delimiter', ','))}"
                wb.con.execute(f"COPY ({sql}) TO {lit(out)} ({o})")
                written.append(out)
        elif fmt in ("xlsx", "xlsm", "excel"):
            _export_xlsx(wb, path, sheets, visible, prog)
            written.append(path)
        elif fmt in ("sqlite", "db", "sqlite3"):
            _export_sqlite(wb, path, sheets, visible, prog)
            written.append(path)
        elif fmt in ("duckdb", "ddb"):
            _export_duckdb(wb, path, sheets, visible)
            written.append(path)
        elif fmt == "sql":
            _export_sql_script(wb, path, sheets, visible, prog)
            written.append(path)
        elif fmt == "pysheets":
            save_workbook(wb, path)
            written.append(path)
        else:
            raise EngineError(f"Unsupported export format: {fmt}")
    return {"files": written}


def _per_sheet_paths(path: str, sheets: list[Sheet], fmt: str) -> list[tuple[Sheet, str]]:
    if len(sheets) == 1:
        return [(sheets[0], path)]
    root, ext = os.path.splitext(path)
    ext = ext or f".{fmt}"
    out = []
    for sh in sheets:
        safe = re.sub(r"[^\w\- ]+", "_", sh.name).strip() or sh.id
        out.append((sh, f"{root}_{safe}{ext}"))
    return out


_EXCEL_FMT = {"date": "yyyy-mm-dd", "ts": "yyyy-mm-dd hh:mm:ss"}


def _export_xlsx(wb: Workbook, path: str, sheets: list[Sheet], visible: bool, prog: Progress):
    try:
        import rustpy_xlsxwriter  # noqa: F401  (Rust writer: ~5x faster)
    except ImportError:
        return _export_xlsx_py(wb, path, sheets, visible, prog)
    return _export_xlsx_rust(wb, path, sheets, visible, prog)


def _export_xlsx_rust(wb: Workbook, path: str, sheets: list[Sheet], visible: bool, prog: Progress):
    import math

    import rustpy_xlsxwriter as rx

    entries = []
    widths: dict = {}
    formats: dict = {}
    freeze: dict = {}
    used: set[str] = set()
    counter = {"done": 0}
    total = sum(s.nrows for s in sheets)
    limit = EXCEL_MAX_ROWS - 1
    for sh in sheets:
        sql, names, cols = _select_for_export(wb, sh, visible)
        if not names:
            entries.append((_xl_name(sh.name, used), [{"": None}]))
            continue
        n = wb.con.execute(f"SELECT count(*) FROM ({sql})").fetchone()[0]
        parts = max(1, math.ceil(n / limit))
        state: dict = {"res": None, "cur": None}

        def part_gen(k, sql=sql, names=names, state=state, parts=parts):
            if state["res"] is None:
                state["cur"] = wb.con.cursor()
                state["res"] = state["cur"].execute(sql)
            emitted = 0
            while emitted < limit:
                batch = state["res"].fetchmany(min(20000, limit - emitted))
                if not batch:
                    break
                for row in batch:
                    yield {n_: (None if isinstance(v, float) and v != v else v) for n_, v in zip(names, row, strict=True)}
                emitted += len(batch)
                counter["done"] += len(batch)
                prog.update(counter["done"], total)
            if k == parts - 1 and state["cur"] is not None:
                state["cur"].close()

        for k in range(parts):
            nm = _xl_name(sh.name if k == 0 else f"{sh.name} ({k + 1})", used)
            entries.append((nm, part_gen(k)))
            widths[nm] = [max(6.0, c.width / 7.2) for c in cols]
            fm = {}
            for c, name in zip(cols, names, strict=True):
                spec = c.fmt or _EXCEL_FMT.get(c.typ)
                if spec:
                    fm[name] = rx.Format().set_num_format(spec)
            if fm:
                formats[nm] = fm
            freeze[nm] = {"row": max(sh.frozen_rows, 1), "col": sh.frozen_cols}
    rx.write_worksheets(entries, path, bold_headers=True, column_widths=widths, column_formats=formats,
                        freeze_panes=freeze, datetime_format="yyyy-mm-dd hh:mm:ss")


def _export_xlsx_py(wb: Workbook, path: str, sheets: list[Sheet], visible: bool, prog: Progress):
    import xlsxwriter

    book = xlsxwriter.Workbook(path, {"constant_memory": True, "strings_to_numbers": False,
                                      "strings_to_formulas": False, "strings_to_urls": False,
                                      "default_date_format": "yyyy-mm-dd", "remove_timezone": True})
    head_fmt = book.add_format({"bold": True, "bg_color": "#F1F3F4", "border": 0})
    used_names: set[str] = set()
    total_rows = sum(s.nrows for s in sheets)
    done = 0
    try:
        for sh in sheets:
            sql, names, cols = _select_for_export(wb, sh, visible)
            fmts = []
            for c in cols:
                spec = c.fmt or _EXCEL_FMT.get(c.typ)
                fmts.append(book.add_format({"num_format": spec}) if spec else None)
            part = 0
            cur = wb.con.cursor()
            try:
                res = cur.execute(sql)
                ws = None
                r = 0
                while True:
                    batch = res.fetchmany(20000)
                    if not batch and ws is not None:
                        break
                    if ws is None or r >= EXCEL_MAX_ROWS:
                        part += 1
                        ws = book.add_worksheet(_xl_name(sh.name if part == 1 else f"{sh.name} ({part})", used_names))
                        for j, (c, _n) in enumerate(zip(cols, names, strict=True)):
                            ws.set_column(j, j, max(6, c.width / 7.2), fmts[j])
                        if sh.columns and any(c.name for c in sh.columns):
                            ws.write_row(0, 0, names, head_fmt)
                            ws.freeze_panes(max(sh.frozen_rows, 1), sh.frozen_cols)
                            r = 1
                        else:
                            r = 0
                    if not batch:
                        break
                    for row in batch:
                        if r >= EXCEL_MAX_ROWS:
                            break
                        for j, v in enumerate(row):
                            if v is None:
                                continue
                            if isinstance(v, (dt.date, dt.datetime)):
                                ws.write_datetime(r, j, v if isinstance(v, dt.datetime) else
                                                  dt.datetime.combine(v, dt.time()), fmts[j])
                            elif isinstance(v, bool):
                                ws.write_boolean(r, j, v)
                            elif isinstance(v, (int, float)):
                                if v == v and v not in (float("inf"), float("-inf")):
                                    ws.write_number(r, j, v, fmts[j])
                            else:
                                s = str(v)
                                ws.write_string(r, j, s[:32767])
                        r += 1
                    done += len(batch)
                    prog.update(done, total_rows)
            finally:
                cur.close()
    finally:
        book.close()


def _xl_name(name: str, used: set[str]) -> str:
    base = re.sub(r"[\[\]:*?/\\]", "_", name)[:31] or "Sheet"
    n = base
    i = 2
    while n.lower() in used:
        suffix = f" ({i})"
        n = base[:31 - len(suffix)] + suffix
        i += 1
    used.add(n.lower())
    return n


def _sql_ident(name: str) -> str:
    return re.sub(r"\W+", "_", name).strip("_") or "sheet"


def _export_duckdb(wb: Workbook, path: str, sheets: list[Sheet], visible: bool):
    alias = "__out"
    wb.con.execute(f"ATTACH {lit(path)} AS {alias}")
    try:
        for sh in sheets:
            sql, _, _ = _select_for_export(wb, sh, visible)
            wb.con.execute(f"CREATE OR REPLACE TABLE {alias}.{q(_sql_ident(sh.name))} AS {sql}")
    finally:
        wb.con.execute(f"DETACH {alias}")


_SQLITE_TYPES = {"num": "REAL", "str": "TEXT", "bool": "INTEGER", "date": "TEXT", "ts": "TEXT"}


def _export_sqlite(wb: Workbook, path: str, sheets: list[Sheet], visible: bool, prog: Progress):
    con = sqlite3.connect(path)
    try:
        done = 0
        for sh in sheets:
            sql, names, cols = _select_for_export(wb, sh, visible)
            tname = _sql_ident(sh.name)
            con.execute(f"DROP TABLE IF EXISTS {q(tname)}")
            defs = ", ".join(f"{q(n)} {_SQLITE_TYPES.get(c.typ, 'TEXT')}" for n, c in zip(names, cols, strict=True))
            con.execute(f"CREATE TABLE {q(tname)} ({defs})")
            ph = ", ".join("?" for _ in names)
            cur = wb.con.cursor()
            try:
                res = cur.execute(sql)
                while True:
                    batch = res.fetchmany(20000)
                    if not batch:
                        break
                    con.executemany(f"INSERT INTO {q(tname)} VALUES ({ph})",
                                    [[_sqlite_val(v) for v in r] for r in batch])
                    done += len(batch)
                    prog.update(done)
            finally:
                cur.close()
        con.commit()
    finally:
        con.close()


def _sqlite_val(v):
    if isinstance(v, (dt.date, dt.datetime)):
        return v.isoformat(sep=" ") if isinstance(v, dt.datetime) else v.isoformat()
    if isinstance(v, bool):
        return int(v)
    if hasattr(v, "is_finite"):
        return float(v)
    return v


_SQL_TYPES = {"num": "DOUBLE PRECISION", "str": "TEXT", "bool": "BOOLEAN", "date": "DATE", "ts": "TIMESTAMP"}


def _export_sql_script(wb: Workbook, path: str, sheets: list[Sheet], visible: bool, prog: Progress):
    """Portable SQL dump: CREATE TABLE + batched INSERT statements."""

    def sql_val(v):
        if v is None:
            return "NULL"
        if isinstance(v, bool):
            return "TRUE" if v else "FALSE"
        if isinstance(v, (int, float)):
            return repr(v) if v == v else "NULL"
        if isinstance(v, (dt.date, dt.datetime)):
            return lit(v.isoformat(sep=" ") if isinstance(v, dt.datetime) else v.isoformat())
        return lit(str(v))

    with open(path, "w", encoding="utf-8") as fh:
        done = 0
        for sh in sheets:
            sql, names, cols = _select_for_export(wb, sh, visible)
            tname = _sql_ident(sh.name)
            fh.write(f"-- Sheet: {sh.name}\nDROP TABLE IF EXISTS {q(tname)};\n")
            defs = ",\n  ".join(f"{q(n)} {_SQL_TYPES.get(c.typ, 'TEXT')}" for n, c in zip(names, cols, strict=True))
            fh.write(f"CREATE TABLE {q(tname)} (\n  {defs}\n);\n")
            cur = wb.con.cursor()
            try:
                res = cur.execute(sql)
                collist = ", ".join(q(n) for n in names)
                while True:
                    batch = res.fetchmany(1000)
                    if not batch:
                        break
                    values = ",\n  ".join("(" + ", ".join(sql_val(v) for v in r) + ")" for r in batch)
                    fh.write(f"INSERT INTO {q(tname)} ({collist}) VALUES\n  {values};\n")
                    done += len(batch)
                    prog.update(done)
            finally:
                cur.close()
            fh.write("\n")


def export_sql_url(wb: Workbook, url: str, scope: str = "sheet", sheet_id: str | None = None,
                   if_exists: str = "replace") -> dict:
    """Write sheets as tables into any SQLAlchemy database."""
    try:
        import sqlalchemy as sa
    except ImportError as e:
        raise EngineError("SQLAlchemy is not installed (pip install pysheets[sql])") from e
    sheets = wb.sheets if scope == "all" else [wb.sheet(sheet_id or wb.sheets[0].id)]
    engine = sa.create_engine(url)
    typemap = {"num": sa.Float, "str": sa.Text, "bool": sa.Boolean, "date": sa.Date, "ts": sa.DateTime}
    out = []
    with Progress(wb, "Writing to database") as prog, engine.begin() as conn:
        done = 0
        for sh in sheets:
            sql, names, cols = _select_for_export(wb, sh, False)
            md = sa.MetaData()
            tbl = sa.Table(_sql_ident(sh.name), md, *[sa.Column(n, typemap.get(c.typ, sa.Text))
                                                     for n, c in zip(names, cols, strict=True)])
            if if_exists == "replace":
                tbl.drop(conn, checkfirst=True)
            tbl.create(conn, checkfirst=True)
            cur = wb.con.cursor()
            try:
                res = cur.execute(sql)
                while True:
                    batch = res.fetchmany(10000)
                    if not batch:
                        break
                    conn.execute(tbl.insert(), [dict(zip(names, r, strict=True)) for r in batch])
                    done += len(batch)
                    prog.update(done)
            finally:
                cur.close()
            out.append(tbl.name)
    return {"tables": out}


# -------------------------------------------------------- native workbook


def save_workbook(wb: Workbook, path: str) -> dict:
    tmp = path + ".tmp"
    for p in (tmp, tmp + ".wal"):
        if os.path.exists(p):
            os.remove(p)
    alias = "__save"
    wb.con.execute(f"ATTACH {lit(tmp)} AS {alias}")
    try:
        for sh in wb.sheets:
            wb.con.execute(f"CREATE TABLE {alias}.{q(sh.table)} AS SELECT * FROM {q(sh.table)} ORDER BY __rid")
        meta = {
            "format": 1,
            "title": wb.title,
            "sheets": [_clean_meta(s.to_meta()) for s in wb.sheets],
            "formulas": [f.to_dict() for f in wb.formulas.values()],
        }
        wb.con.execute(f"CREATE TABLE {alias}.__pysheets_meta (k VARCHAR, v VARCHAR)")
        wb.con.execute(f"INSERT INTO {alias}.__pysheets_meta VALUES ('workbook', ?)", [json.dumps(meta, default=str)])
    finally:
        wb.con.execute(f"DETACH {alias}")
    os.replace(tmp, path)
    wb.path = path
    wb.title = _base_name(path)
    wb.dirty = False
    return {"path": path}


def _clean_meta(m: dict) -> dict:
    m = dict(m)
    m["view_table"] = None
    m["nview"] = 0
    return m


def open_workbook(wb: Workbook, path: str) -> dict:
    alias = "__open"
    wb.con.execute(f"ATTACH {lit(path)} AS {alias} (READ_ONLY)")
    try:
        row = wb.con.execute(f"SELECT v FROM {alias}.__pysheets_meta WHERE k = 'workbook'").fetchone()
        if not row:
            raise EngineError("Not a PySheets workbook")
        meta = json.loads(row[0])
        for sh in wb.sheets:
            wb.con.execute(f"DROP TABLE IF EXISTS {q(sh.table)}")
            wb._drop_view(sh)
        sheets = [Sheet.from_meta(m) for m in meta["sheets"]]
        for sh in sheets:
            wb.con.execute(f"CREATE TABLE {q(sh.table)} AS SELECT * FROM {alias}.{q(sh.table)} ORDER BY __rid")
    finally:
        wb.con.execute(f"DETACH {alias}")
    wb.sheets = sheets
    wb.formulas = {}
    for fd in meta.get("formulas", []):
        fd = dict(fd)
        fd["spill"] = tuple(fd["spill"]) if fd.get("spill") else None
        fd["deps"] = []
        f = Formula(**fd)
        wb.formulas[f.id] = f
    import itertools

    wb._fid = itertools.count(max(wb.formulas, default=0) + 1)
    for e in wb.undo_stack + wb.redo_stack:
        wb._drop_entry(e)
    wb.undo_stack.clear()
    wb.redo_stack.clear()
    wb.title = meta.get("title") or _base_name(path)
    wb.path = path
    for sh in wb.sheets:
        wb._apply_filters(sh)
    for f in wb.formulas.values():
        wb._refresh_deps(f)
    wb.dirty = False
    wb.version += 1
    return {"path": path}


def new_workbook(wb: Workbook) -> dict:
    for sh in wb.sheets:
        wb.con.execute(f"DROP TABLE IF EXISTS {q(sh.table)}")
        wb._drop_view(sh)
    for e in wb.undo_stack + wb.redo_stack:
        wb._drop_entry(e)
    wb.undo_stack.clear()
    wb.redo_stack.clear()
    wb.sheets = []
    wb.formulas = {}
    wb.add_sheet("Sheet1", record=False)
    wb.undo_stack.clear()
    wb.title = "Untitled spreadsheet"
    wb.path = None
    wb.dirty = False
    return {"ok": True}



