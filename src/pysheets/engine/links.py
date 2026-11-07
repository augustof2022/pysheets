"""Linked data: sheets filled from Google Sheets that update with Refresh.

Network work happens *outside* the workbook lock; only the final write takes it, so the
grid stays responsive while dozens of spreadsheets download.
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime
from typing import Any

from ..connectors.google import (
    GoogleClient,
    GoogleError,
    clean_header,
    consolidate,
    extract_sheet_id,
    values_to_rows,
)
from .io import Progress, rows_to_arrow
from .model import Column, Sheet
from .workbook import EngineError, Workbook, norm_duck, q

LOG_COLUMNS = [
    "refreshed_at",
    "source",
    "spreadsheet_id",
    "tab",
    "status",
    "rows",
    "missing_columns",
    "extra_columns",
    "error",
]


def _client(client: GoogleClient | None) -> GoogleClient:
    return client if client is not None else GoogleClient.connect(interactive=False)


# ------------------------------------------------------------------ writing data


def write_rows(
    wb: Workbook,
    names: list[str],
    rows: list[list],
    *,
    sheet: Sheet | None = None,
    name: str | None = None,
    link: dict | None = None,
    index: int | None = None,
) -> Sheet:
    """Create a sheet from rows, or replace the linked columns of an existing sheet.

    On replace, columns that came from the source are matched by header (case/space
    insensitive); columns the user added (formulas, notes) are kept, as are formats, rules
    and column formulas, which are recalculated over the new rows.
    """
    tbl, names = rows_to_arrow(list(names), rows)
    reg = f"__link_{uuid.uuid4().hex[:8]}"
    with wb.lock:
        wb.register(reg, tbl)
        raw = f"__linkraw_{uuid.uuid4().hex[:8]}"
        try:
            wb.con.execute(f"CREATE OR REPLACE TABLE {q(raw)} AS SELECT * FROM {reg}")
        finally:
            wb.unregister(reg)
        try:
            if sheet is None:
                wb._begin("Import from Google Sheets", [])
                sid = wb._new_sheet_id()
                renamed = f"__linkraw2_{sid}"
                sel = ", ".join(f"{q(f'__c{j}')} AS {q(f'k{j}')}" for j in range(len(names))) or "1 AS dummy"
                wb.con.execute(f"CREATE OR REPLACE TABLE {q(renamed)} AS SELECT {sel} FROM {q(raw)}")
                sh = wb._adopt_table(sid, name or "Data", renamed, header=True)
                wb.con.execute(f"DROP TABLE IF EXISTS {q(renamed)}")
                for c, n in zip(sh.columns, names, strict=False):
                    c.name = n
                    c.linked = True
                wb._autosize(sh)
                sh.link = link
                if index is None:
                    wb.sheets.append(sh)
                else:
                    wb.sheets.insert(index, sh)
                wb._touch(sh)
            else:
                sh = sheet
                _replace(wb, sh, raw, names)
                sh.link = link if link is not None else sh.link
                wb._apply_filters(sh)
                wb._recalc_sheet(sh)
                wb._touch(sh)
                # refreshes are not undoable; drop history that could resurrect old data
                for e in wb.undo_stack + wb.redo_stack:
                    wb._drop_entry(e)
                wb.undo_stack.clear()
                wb.redo_stack.clear()
        finally:
            wb.con.execute(f"DROP TABLE IF EXISTS {q(raw)}")
            wb._pending = None
    return sh


def _replace(wb: Workbook, sh: Sheet, raw: str, names: list[str]):
    desc = {r[0]: str(r[1]) for r in wb.con.execute(f"DESCRIBE {q(raw)}").fetchall()}
    src = {}
    for j, n in enumerate(names):
        k = clean_header(n)
        if k and k not in src:
            src[k] = f"__c{j}"
    used = set()
    sel = []
    for c in sh.columns:
        k = clean_header(c.name)
        if c.linked and k in src:
            rawcol = src[k]
            used.add(rawcol)
            c.duck = norm_duck(desc[rawcol])
            sel.append(f"CAST(r.{q(rawcol)} AS {c.duck}) AS {q(c.id)}")
        elif c.linked:
            sel.append(f"CAST(NULL AS {c.duck}) AS {q(c.id)}")
        else:
            sel.append(f"o.{q(c.id)} AS {q(c.id)}")
    for j, n in enumerate(names):
        rawcol = f"__c{j}"
        if (
            rawcol in used
            or not clean_header(n)
            or clean_header(n) in {clean_header(c.name) for c in sh.columns if c.linked}
        ):
            continue
        col = Column(id=sh.new_col_id(), name=n, duck=norm_duck(desc[rawcol]), linked=True)
        sh.columns.append(col)
        sel.append(f"CAST(r.{q(rawcol)} AS {col.duck}) AS {q(col.id)}")
    newt = f"__linknew_{sh.id}"
    wb.con.execute(
        f"CREATE OR REPLACE TABLE {q(newt)} AS SELECT r.rowid AS __rid{', ' + ', '.join(sel) if sel else ''} "
        f"FROM {q(raw)} r LEFT JOIN {q(sh.table)} o ON o.__rid = r.rowid ORDER BY r.rowid"
    )
    wb.con.execute(f"DROP TABLE IF EXISTS {q(sh.table)}")
    wb.con.execute(f"ALTER TABLE {q(newt)} RENAME TO {q(sh.table)}")
    sh.nrows = wb.con.execute(f"SELECT count(*) FROM {q(sh.table)}").fetchone()[0]
    # keep blank rows below the data for typing, like a regular sheet
    for f in list(wb.formulas.values()):
        if f.sheet_id == sh.id and f.kind == "cell" and f.row > sh.nrows:
            del wb.formulas[f.id]


# ------------------------------------------------------------------ single tab


def import_tabs(
    wb: Workbook, url: str, tabs: list[str], *, typed: bool = True, client: GoogleClient | None = None
) -> dict:
    sid = extract_sheet_id(url)
    if not sid:
        raise EngineError("Paste a Google Sheets link or ID")
    if not tabs:
        raise EngineError("Choose at least one tab")
    cl = _client(client)
    with Progress(wb, "Downloading from Google Sheets"):
        values = cl.get_values(sid, tabs, typed=typed)
    created = []
    for tab in tabs:
        v = values.get(tab)
        if v is None:
            raise EngineError(f"Tab '{tab}' was not found in that spreadsheet")
        names, rows = values_to_rows(v)
        link = {
            "kind": "gsheet",
            "spreadsheet_id": sid,
            "tab": tab,
            "typed": typed,
            "refreshed_at": datetime.now().isoformat(timespec="seconds"),
            "rows": len(rows),
        }
        sh = write_rows(wb, names, rows, name=tab, link=link)
        created.append(sh.id)
    return {"sheets": created}


# --------------------------------------------------------------- consolidation


def read_directory(wb: Workbook, spec: dict, client: GoogleClient) -> list[tuple[str, str]]:
    """Resolve the list of (label, spreadsheet_id) sources from the spec's directory."""
    d = spec.get("directory") or {}
    id_col = clean_header(spec.get("id_column") or "Spreadsheet ID")
    url_col = clean_header(spec.get("url_column") or "")
    label_col = clean_header(spec.get("label_column") or "")
    kind = d.get("kind", "gsheet")
    if kind == "list":
        return [(extract_sheet_id(x), extract_sheet_id(x)) for x in d.get("ids", []) if str(x).strip()]
    if kind == "sheet":
        with wb.lock:
            sh = wb.sheet(d["sheet"])
            cols = {clean_header(c.name): c for c in sh.columns}
            if id_col not in cols and url_col not in cols:
                raise EngineError(f"Column '{spec.get('id_column')}' not found in sheet '{sh.name}'")
            parts = []
            for key in (id_col, url_col, label_col):
                c = cols.get(key)
                parts.append(f"CAST({q(c.id)} AS VARCHAR)" if c else "NULL")
            rows = wb.con.execute(f"SELECT {', '.join(parts)} FROM {q(sh.table)} ORDER BY __rid").fetchall()
    else:
        dir_id = extract_sheet_id(d.get("url", ""))
        tab = d.get("tab") or "Sheet1"
        if not dir_id:
            raise EngineError("Enter the directory spreadsheet link")
        vals = client.get_values(dir_id, [tab], typed=False).get(tab)
        if vals is None:
            raise EngineError(f"Directory tab '{tab}' not found")
        names, data = values_to_rows(vals)
        keys = [clean_header(n) for n in names]

        def pick(r, key):
            return r[keys.index(key)] if key and key in keys else None

        if id_col not in keys and url_col not in keys:
            raise EngineError(f"Column '{spec.get('id_column')}' not found in the directory tab")
        rows = [(pick(r, id_col), pick(r, url_col), pick(r, label_col)) for r in data]
    out = []
    seen = set()
    for sid, url, label in rows:
        s = extract_sheet_id(str(sid or "").strip() or str(url or "").strip())
        if not s or s in seen:
            continue
        seen.add(s)
        out.append((str(label).strip() if label else s, s))
    return out


def consolidate_gsheets(
    wb: Workbook, spec: dict, client: GoogleClient | None = None, refresh_group: str | None = None
) -> dict:
    """Pull the same tab(s) from every spreadsheet in a directory and stack them."""
    tabs = [t.strip() for t in spec.get("tabs", []) if str(t).strip()]
    if not tabs:
        raise EngineError("Enter at least one tab name to pull")
    cl = _client(client)
    group = refresh_group or spec.get("group") or uuid.uuid4().hex[:8]
    spec = dict(spec, group=group)
    with Progress(wb, "Reading the list of spreadsheets") as prog:
        sources = read_directory(wb, spec, cl)
        if not sources:
            raise EngineError("The directory has no spreadsheet IDs")
        if spec.get("limit"):
            sources = sources[: int(spec["limit"])]
        started = time.time()

        def tick(done, total, label):
            prog.update(done, total, f"Downloading {done}/{total} · {label}")

        data, results = consolidate(
            cl,
            sources,
            tabs,
            typed=bool(spec.get("typed")),
            drop_blank=spec.get("drop_blank", True),
            add_metadata=spec.get("add_metadata", True),
            expected=spec.get("expected") or None,
            workers=int(spec.get("workers") or 4),
            progress=tick,
        )
        prog.update(len(sources), len(sources), "Writing sheets")
    stamp = datetime.now().isoformat(timespec="seconds")
    created = []
    name_tpl = spec.get("name") or "{tab}"
    for tab in tabs:
        names, rows = data[tab]
        link = {
            "kind": "gsheet_consolidate",
            "group": group,
            "tab": tab,
            "spec": spec,
            "refreshed_at": stamp,
            "rows": len(rows),
            "sources": len(sources),
        }
        existing = next(
            (s for s in wb.sheets if s.link and s.link.get("group") == group and s.link.get("tab") == tab),
            None,
        )
        sh = write_rows(wb, names, rows, sheet=existing, name=name_tpl.replace("{tab}", tab), link=link)
        created.append(sh.id)
    # refresh log
    log_rows = [
        [
            stamp,
            r.label,
            r.spreadsheet_id,
            r.tab,
            r.status,
            r.rows,
            ", ".join(r.missing_columns),
            ", ".join(r.extra_columns),
            r.error,
        ]
        for r in results
    ]
    log_rows.sort(key=lambda r: (r[4] == "Done", r[1], r[3]))
    existing_log = next(
        (
            s
            for s in wb.sheets
            if s.link and s.link.get("group") == group and s.link.get("kind") == "gsheet_log"
        ),
        None,
    )
    log_link = {"kind": "gsheet_log", "group": group, "refreshed_at": stamp}
    write_rows(
        wb,
        list(LOG_COLUMNS),
        log_rows,
        sheet=existing_log,
        name=(spec.get("log_name") or f"{name_tpl.replace('{tab}', tabs[0])} log"),
        link=log_link,
    )
    failed = sum(1 for r in results if r.status != "Done")
    return {
        "sheets": created,
        "sources": len(sources),
        "rows": {t: len(data[t][1]) for t in tabs},
        "failed": failed,
        "seconds": round(time.time() - started, 1),
    }


# --------------------------------------------------------------------- refresh


def refresh(wb: Workbook, sheet_id: str, client: GoogleClient | None = None) -> dict:
    with wb.lock:
        sh = wb.sheet(sheet_id)
        link = dict(sh.link or {})
    kind = link.get("kind")
    if kind == "gsheet":
        cl = _client(client)
        with Progress(wb, "Refreshing from Google Sheets"):
            v = cl.get_values(link["spreadsheet_id"], [link["tab"]], typed=link.get("typed", True)).get(
                link["tab"]
            )
        if v is None:
            raise EngineError(f"Tab '{link['tab']}' no longer exists in the source spreadsheet")
        names, rows = values_to_rows(v)
        link.update(refreshed_at=datetime.now().isoformat(timespec="seconds"), rows=len(rows))
        write_rows(wb, names, rows, sheet=sh, link=link)
        return {"sheets": [sh.id], "rows": len(rows)}
    if kind in ("gsheet_consolidate", "gsheet_log"):
        group = link["group"]
        spec = None
        with wb.lock:
            for s in wb.sheets:
                if s.link and s.link.get("group") == group and s.link.get("spec"):
                    spec = s.link["spec"]
                    break
        if spec is None:
            raise EngineError("The consolidated sheets for this log were deleted")
        return consolidate_gsheets(wb, spec, client, refresh_group=group)
    raise EngineError("This sheet is not linked to a data source")


def refresh_all(wb: Workbook, client: GoogleClient | None = None) -> dict:
    with wb.lock:
        targets = []
        groups = set()
        for s in wb.sheets:
            if not s.link:
                continue
            g = s.link.get("group")
            if g:
                if g in groups:
                    continue
                groups.add(g)
            targets.append(s.id)
    cl = _client(client) if targets else None
    done, errors = [], []
    for sid in targets:
        try:
            refresh(wb, sid, cl)
            done.append(sid)
        except (EngineError, GoogleError) as e:
            errors.append(f"{sid}: {e}")
    return {"refreshed": len(done), "errors": errors}


def describe_link(link: dict | None) -> dict[str, Any] | None:
    if not link:
        return None
    out = {k: link.get(k) for k in ("kind", "tab", "refreshed_at", "rows", "sources", "spreadsheet_id")}
    return out
