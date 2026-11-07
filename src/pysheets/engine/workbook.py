"""The workbook: sheets backed by DuckDB tables, formulas, recalculation and undo."""

from __future__ import annotations

import copy
import itertools
import tempfile
import threading
from typing import Any

import duckdb

from ..formula import parser as P
from ..formula.compiler import (
    NUM,
    STR,
    A,
    Compiler,
    FormulaError,
    S,
    duck_to_typ,
    txt,
)
from ..formula.refs import Ref, cell_name, col_to_letters
from .model import Column, Formula, Sheet
from .values import (
    common_type,
    duck_literal,
    is_numeric_duck,
    parse_input,
    to_json,
    value_to_text,
)

INF = 1 << 60
DEFAULT_COLS = 26
DEFAULT_ROWS = 1000


class EngineError(Exception):
    """User-facing error (shown as a toast)."""


def q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def merge_duck(cur: str, new: str) -> str:
    cur_u, new_u = cur.upper(), new.upper()
    if cur_u == new_u:
        return cur
    if is_numeric_duck(cur_u) and is_numeric_duck(new_u):
        return "DOUBLE"
    if {cur_u, new_u} <= {"DATE", "TIMESTAMP"}:
        return "TIMESTAMP"
    if cur_u.startswith("TIMESTAMP") and new_u.startswith("TIMESTAMP"):
        return "TIMESTAMP"
    return "VARCHAR"


def norm_duck(t: str) -> str:
    """Map exotic DuckDB types to the handful the grid understands."""
    u = t.upper()
    if u in ("DOUBLE", "BIGINT", "VARCHAR", "BOOLEAN", "DATE", "TIMESTAMP"):
        return u
    if is_numeric_duck(u):
        if u.startswith(("DECIMAL", "NUMERIC", "FLOAT", "REAL", "HUGEINT", "UHUGEINT", "UBIGINT")):
            return "DOUBLE"
        return "BIGINT"
    if u.startswith("TIMESTAMP"):
        return "TIMESTAMP"
    if u == "NULL" or u == '"NULL"':
        return "VARCHAR"
    return "VARCHAR"


def overlaps(a1, a2, b1, b2) -> bool:
    a2 = INF if a2 is None else a2
    b2 = INF if b2 is None else b2
    return a1 <= b2 and b1 <= a2


class Workbook:
    def __init__(self, path: str | None = None):
        self.lock = threading.RLock()
        self._tmpdir = tempfile.mkdtemp(prefix="pysheets-")
        # In-memory storage is ~3x faster than a file; DuckDB offloads to temp_directory
        # when the data outgrows the memory limit, so very large sheets still work.
        self.con = duckdb.connect(path or ":memory:")
        self.con.execute(f"SET temp_directory = '{self._tmpdir.replace(chr(39), '')}'")
        self.con.execute("SET preserve_insertion_order = true")
        self.sheets: list[Sheet] = []
        self.formulas: dict[int, Formula] = {}
        self._fid = itertools.count(1)
        self._sid = itertools.count(1)
        self._tmp = itertools.count(1)
        self.undo_stack: list[dict] = []
        self.redo_stack: list[dict] = []
        self.version = 0
        self.title = "Untitled spreadsheet"
        self.path: str | None = None
        self.dirty = False
        self.progress: dict[str, Any] = {"active": False}
        self._cache: dict = {}
        self._in_pivot_refresh = False
        self._pending: dict | None = None

    # ------------------------------------------------------------------ duckdb

    def register(self, name: str, obj):
        """Register an Arrow table as a view (DuckDB 2 needs pending results closed first)."""
        self.con.execute("SELECT 1").fetchall()
        self.con.register(name, obj)

    def unregister(self, name: str):
        self.con.execute("SELECT 1").fetchall()
        self.con.unregister(name)

    # ------------------------------------------------------------------ catalog

    def get_sheet(self, name: str | None) -> Sheet | None:
        if name is None:
            return None
        low = name.lower()
        for s in self.sheets:
            if s.name.lower() == low:
                return s
        return None

    def sheet(self, sheet_id: str) -> Sheet:
        for s in self.sheets:
            if s.id == sheet_id:
                return s
        raise EngineError(f"Unknown sheet {sheet_id}")

    def info(self) -> dict:
        return {
            "title": self.title,
            "path": self.path,
            "dirty": self.dirty,
            "version": self.version,
            "can_undo": bool(self.undo_stack),
            "can_redo": bool(self.redo_stack),
            "undo_label": self.undo_stack[-1]["label"] if self.undo_stack else None,
            "redo_label": self.redo_stack[-1]["label"] if self.redo_stack else None,
            "sheets": [self.sheet_info(s) for s in self.sheets],
        }

    def sheet_info(self, sh: Sheet) -> dict:
        d = sh.to_info()
        cells = []
        for f in self.formulas.values():
            if f.sheet_id != sh.id:
                continue
            try:
                c = sh.col_index(f.col_id)
            except KeyError:
                continue
            h, w = f.spill or (1, 1)
            entry = {"id": f.id, "row": f.row, "col": c, "kind": f.kind, "error": f.error, "h": h, "w": w}
            if f.kind == "column":
                entry["row_end"] = f.row_end if f.row_end is not None else sh.nrows
                entry["text"] = f.text
            cells.append(entry)
        d["formulas"] = cells
        return d

    def _unique_name(self, base: str) -> str:
        names = {s.name.lower() for s in self.sheets}
        if base.lower() not in names:
            return base
        for i in itertools.count(2):
            cand = f"{base} ({i})"
            if cand.lower() not in names:
                return cand
        raise AssertionError

    def _new_sheet_id(self) -> str:
        existing = {s.id for s in self.sheets}
        while True:
            sid = f"s{next(self._sid)}"
            if sid not in existing:
                return sid

    def _touch(self, sh: Sheet | None = None):
        self.version += 1
        self.dirty = True
        self._cache.clear()
        if sh is not None:
            sh.version += 1
            if not self._in_pivot_refresh:
                self._in_pivot_refresh = True
                try:
                    self._refresh_pivots()
                finally:
                    self._in_pivot_refresh = False

    # ------------------------------------------------------------- sheet create

    def add_sheet(
        self,
        name: str | None = None,
        ncols: int = DEFAULT_COLS,
        nrows: int = DEFAULT_ROWS,
        index: int | None = None,
        record: bool = True,
    ) -> Sheet:
        if record:
            self._begin("Add sheet", [])
        sid = self._new_sheet_id()
        name = self._unique_name(name or f"Sheet{len(self.sheets) + 1}")
        sh = Sheet(id=sid, name=name, table=f"t_{sid}", nrows=nrows, frozen_rows=0)
        for _ in range(ncols):
            sh.columns.append(Column(id=sh.new_col_id(), duck="VARCHAR", width=110))
        cols = ", ".join(f"CAST(NULL AS VARCHAR) AS {q(c.id)}" for c in sh.columns)
        self.con.execute(
            f"CREATE OR REPLACE TABLE {q(sh.table)} AS SELECT range AS __rid"
            f"{', ' + cols if cols else ''} FROM range({nrows})"
        )
        if index is None:
            self.sheets.append(sh)
        else:
            self.sheets.insert(index, sh)
        self._touch(sh)
        return sh

    def sheet_from_sql(
        self,
        name: str,
        select_sql: str,
        header: bool = True,
        source: str | None = None,
        params: list | None = None,
        record: bool = True,
        index: int | None = None,
    ) -> Sheet:
        """Materialise any relation as a new sheet.  Column names become the header row."""
        if record:
            self._begin("Import", [])
        sid = self._new_sheet_id()
        raw = f"__raw_{sid}"
        self.con.execute(f"CREATE OR REPLACE TABLE {q(raw)} AS {select_sql}", params or [])
        try:
            sh = self._adopt_table(sid, name, raw, header=header, source=source)
        finally:
            self.con.execute(f"DROP TABLE IF EXISTS {q(raw)}")
        if index is None:
            self.sheets.append(sh)
        else:
            self.sheets.insert(index, sh)
        self._touch(sh)
        return sh

    def _adopt_table(
        self, sid: str, name: str, raw: str, header: bool = True, source: str | None = None
    ) -> Sheet:
        desc = self.con.execute(f"DESCRIBE {q(raw)}").fetchall()
        sh = Sheet(id=sid, name=self._unique_name(name), table=f"t_{sid}", source=source)
        sel = []
        for colname, ctype, *_ in desc:
            c = Column(id=sh.new_col_id(), name=str(colname) if header else "", duck=norm_duck(str(ctype)))
            expr = q(colname)
            if c.duck != str(ctype).upper():
                expr = f"TRY_CAST({expr} AS {c.duck})" if c.duck != "VARCHAR" else f"CAST({expr} AS VARCHAR)"
            sel.append(f"{expr} AS {q(c.id)}")
            sh.columns.append(c)
        self.con.execute(
            f"CREATE OR REPLACE TABLE {q(sh.table)} AS SELECT rowid AS __rid"
            f"{', ' + ', '.join(sel) if sel else ''} FROM {q(raw)}"
        )
        sh.nrows = self.con.execute(f"SELECT count(*) FROM {q(sh.table)}").fetchone()[0]
        sh.frozen_rows = 1 if header else 0
        self._autosize(sh)
        return sh

    def _autosize(self, sh: Sheet, sample: int = 300):
        if not sh.columns:
            return
        parts = ", ".join(f"max(length({txt(S(q(c.id), c.typ))}))" for c in sh.columns)
        try:
            row = self.con.execute(
                f"SELECT {parts} FROM (SELECT * FROM {q(sh.table)} WHERE __rid < {sample})"
            ).fetchone()
        except duckdb.Error:
            return
        for c, ln in zip(sh.columns, row, strict=True):
            n = max(len(c.name) + 3, ln or 0)
            if c.typ == NUM:
                n = max(n, 8)
            c.width = int(min(max(n * 7.4 + 22, 72), 340))

    # ------------------------------------------------------------- sheet admin

    def rename_sheet(self, sheet_id: str, name: str):
        sh = self.sheet(sheet_id)
        name = name.strip()
        if not name:
            raise EngineError("Sheet name cannot be empty")
        other = self.get_sheet(name)
        if other is not None and other is not sh:
            raise EngineError(f"A sheet named '{name}' already exists")
        self._begin("Rename sheet", [])
        old = sh.name
        sh.name = name
        # update references in formulas
        for f in self.formulas.values():
            f.text = P.transform_refs(
                f.text,
                lambda r, old=old: r.with_sheet(name) if r.sheet and r.sheet.lower() == old.lower() else r,
            )
        self._touch(sh)

    def delete_sheet(self, sheet_id: str):
        if len(self.sheets) <= 1:
            raise EngineError("A workbook needs at least one sheet")
        sh = self.sheet(sheet_id)
        self._begin("Delete sheet", [(sh.id, "table")])
        self.sheets.remove(sh)
        for fid in [f.id for f in self.formulas.values() if f.sheet_id == sh.id]:
            del self.formulas[fid]
        self.con.execute(f"DROP TABLE IF EXISTS {q(sh.table)}")
        self._drop_view(sh)
        self._touch()
        self.recalc(None)

    def duplicate_sheet(self, sheet_id: str) -> Sheet:
        src = self.sheet(sheet_id)
        self._begin("Duplicate sheet", [])
        sid = self._new_sheet_id()
        meta = copy.deepcopy(src.to_meta())
        meta.update(
            id=sid, table=f"t_{sid}", name=self._unique_name(src.name + " copy"), view_table=None, nview=0
        )
        sh = Sheet.from_meta(meta)
        self.con.execute(f"CREATE TABLE {q(sh.table)} AS SELECT * FROM {q(src.table)} ORDER BY __rid")
        self.sheets.insert(self.sheets.index(src) + 1, sh)
        for f in list(self.formulas.values()):
            if f.sheet_id == src.id:
                nf = copy.deepcopy(f)
                nf.id = next(self._fid)
                nf.sheet_id = sid
                self.formulas[nf.id] = nf
        self._apply_filters(sh)
        self._touch(sh)
        return sh

    def move_sheet(self, sheet_id: str, index: int):
        sh = self.sheet(sheet_id)
        self._begin("Move sheet", [])
        self.sheets.remove(sh)
        self.sheets.insert(max(0, min(index, len(self.sheets))), sh)
        self._touch()

    def set_sheet_props(self, sheet_id: str, **props):
        sh = self.sheet(sheet_id)
        self._begin("Sheet settings", [])
        for k in ("frozen_rows", "frozen_cols", "tab_color", "row_height"):
            if k in props and props[k] is not None:
                setattr(sh, k, props[k])
        if "frozen_rows" in props:
            sh.frozen_rows = max(0, int(sh.frozen_rows))
        if "frozen_cols" in props:
            sh.frozen_cols = max(0, int(sh.frozen_cols))
        self._touch(sh)

    def set_column_props(
        self,
        sheet_id: str,
        col: int,
        width: int | None = None,
        fmt: Any = "__keep__",
        cols: list[int] | None = None,
    ):
        sh = self.sheet(sheet_id)
        targets = cols if cols is not None else [col]
        self._ensure_cols(sh, max(targets))
        for c in targets:
            column = sh.columns[c]
            if width is not None:
                column.width = int(max(24, min(width, 2000)))
            if fmt != "__keep__":
                column.fmt = fmt
        self._touch(sh)

    # --------------------------------------------------------------- viewport

    def viewport(self, sheet_id: str, r0: int, r1: int, c0: int, c1: int) -> dict:
        """Values for visual rows [r0, r1) and columns [c0, c1).

        Visual row 0 is always the header row.  Without a filter visual row ``v`` is
        sheet row ``v``; with a filter it is the (v-1)-th visible data row.
        """
        sh = self.sheet(sheet_id)
        r0 = max(0, r0)
        ncols = len(sh.columns)
        cols = sh.columns[c0 : min(c1, ncols)]
        n = len(cols)
        a, b = max(r0, 1) - 1, r1 - 2  # data positions
        fetched: dict[int, tuple] = {}
        extra = self._cell_flags_sql(sh, cols)
        if b >= a and cols:
            sel = ", ".join(f"t.{q(c.id)}" for c in cols)
            flag_sel = "".join(f", {e}" for e in extra["exprs"])
            if sh.view_table:
                base = (
                    f"SELECT v.pos AS __k, t.__rid, {sel}{{flags}} FROM {q(sh.view_table)} v "
                    f"JOIN {q(sh.table)} t ON t.__rid = v.rid WHERE v.pos BETWEEN {a} AND {b} ORDER BY v.pos"
                )
            else:
                base = (
                    f"SELECT t.__rid AS __k, t.__rid, {sel}{{flags}} FROM {q(sh.table)} t "
                    f"WHERE t.__rid BETWEEN {a} AND {b} ORDER BY t.__rid"
                )
            try:
                data = self.con.execute(base.format(flags=flag_sel)).fetchall()
            except duckdb.Error:
                extra = {"exprs": [], "meta": []}
                data = self.con.execute(base.format(flags="")).fetchall()
            for row in data:
                fetched[row[0]] = row[1:]
        rows: list[int] = []
        cells: list[list] = []
        flags: list = []
        for v in range(r0, r1):
            if v == 0:
                rows.append(0)
                cells.append([c.name if c.name != "" else None for c in cols])
                continue
            rec = fetched.get(v - 1)
            if rec is None:
                rows.append(-1 if sh.view_table else v)
                cells.append([None] * n)
                continue
            rows.append(rec[0] + 1)
            cells.append([to_json(x) for x in rec[1 : n + 1]])
            for k, val in enumerate(rec[n + 1 :]):
                if val is not None and val is not False:
                    kind, j, idx = extra["meta"][k]
                    flags.append([len(rows) - 1, j, kind, idx if kind != "scale" else to_json(val), idx])
        return {
            "sheet": sh.id,
            "version": sh.version,
            "r0": r0,
            "c0": c0,
            "rows": rows,
            "cells": cells,
            "flags": flags,
        }

    def _cell_flags_sql(self, sh: Sheet, cols: list[Column]) -> dict:
        """Extra boolean columns for conditional formatting and validation in a viewport."""
        from .features import cond_format_expr, validation_expr

        exprs, meta = [], []
        col_ids = {c.id: j for j, c in enumerate(cols)}
        for k, rule in enumerate(sh.cond_formats):
            for cid in rule.get("cols", []):
                if cid not in col_ids:
                    continue
                j = col_ids[cid]
                e = cond_format_expr(self, sh, rule, sh.col_by_id(cid))
                if e is None:
                    continue
                exprs.append(self._row_bound(rule, e))
                meta.append(("scale" if rule.get("kind") == "color_scale" else "cf", j, k))
        for k, rule in enumerate(sh.validations):
            for cid in rule.get("cols", []):
                if cid not in col_ids:
                    continue
                j = col_ids[cid]
                e = validation_expr(self, sh, rule, sh.col_by_id(cid))
                if e is None:
                    continue
                c = sh.col_by_id(cid)
                exprs.append(self._row_bound(rule, f"(t.{q(c.id)} IS NOT NULL AND NOT coalesce({e}, FALSE))"))
                meta.append(("invalid", j, k))
        return {"exprs": exprs, "meta": meta}

    @staticmethod
    def _row_bound(rule: dict, expr: str) -> str:
        r1 = rule.get("r1") or 1
        r2 = rule.get("r2")
        cond = f"t.__rid >= {r1 - 1}"
        if r2 is not None:
            cond += f" AND t.__rid <= {r2 - 1}"
        return f"(CASE WHEN {cond} THEN {expr} END)"

    # ----------------------------------------------------------- cell reading

    def get_cell(self, sheet_id: str, row: int, col: int) -> dict:
        sh = self.sheet(sheet_id)
        out: dict[str, Any] = {"row": row, "col": col, "ref": cell_name(row, col)}
        if col >= len(sh.columns):
            out.update(value=None, input="", type=STR)
            return out
        c = sh.columns[col]
        if row == 0:
            value = c.name or None
        else:
            r = self.con.execute(f"SELECT {q(c.id)} FROM {q(sh.table)} WHERE __rid = ?", [row - 1]).fetchone()
            value = r[0] if r else None
        out["value"] = to_json(value)
        out["type"] = c.typ if row else STR
        out["input"] = value_to_input(value)
        f = self._formula_at(sh, row, col)
        if f is not None:
            if f.kind == "column":
                out["input"] = P.shift_formula(f.text, row - f.row, 0)
                out["formula"] = {
                    "id": f.id,
                    "kind": "column",
                    "anchor": cell_name(f.row, col),
                    "row": f.row,
                    "row_end": f.row_end if f.row_end is not None else sh.nrows,
                    "error": f.error,
                }
            else:
                out["input"] = f.text
                out["formula"] = {"id": f.id, "kind": "cell", "error": f.error, "spill": f.spill}
        else:
            owner = self._spill_owner(sh, row, col)
            if owner is not None:
                oc = sh.col_index(owner.col_id)
                out["spill_of"] = {
                    "anchor": cell_name(owner.row, oc),
                    "text": owner.text,
                    "row": owner.row,
                    "col": oc,
                }
        for rule in sh.validations:
            if (
                c.id in rule.get("cols", [])
                and row >= (rule.get("r1") or 1)
                and (rule.get("r2") is None or row <= rule["r2"])
            ):
                out["validation"] = rule
                break
        return out

    def _formula_at(self, sh: Sheet, row: int, col: int) -> Formula | None:
        cid = sh.columns[col].id if col < len(sh.columns) else None
        if cid is None:
            return None
        hit = None
        for f in self.formulas.values():
            if f.sheet_id != sh.id or f.col_id != cid:
                continue
            if f.kind == "cell" and f.row == row:
                return f
            if f.kind == "column" and row >= f.row and row not in f.exceptions:
                end = f.row_end if f.row_end is not None else sh.nrows
                if row <= end:
                    hit = f
        return hit

    def _spill_owner(self, sh: Sheet, row: int, col: int) -> Formula | None:
        for f in self.formulas.values():
            if f.sheet_id != sh.id or f.kind != "cell" or not f.spill:
                continue
            try:
                fc = sh.col_index(f.col_id)
            except KeyError:
                continue
            h, w = f.spill
            if f.row <= row < f.row + h and fc <= col < fc + w and (row, col) != (f.row, fc):
                return f
        return None

    # ---------------------------------------------------------------- editing

    def set_cell(self, sheet_id: str, row: int, col: int, text: Any, fill: str | None = None) -> dict:
        sh = self.sheet(sheet_id)
        if row < 0 or col < 0:
            raise EngineError("Invalid cell")
        self._begin("Edit cell", [(sh.id, "rect", row, row)])
        self._ensure_cols(sh, col)
        self._ensure_rows(sh, row)
        cid = sh.columns[col].id
        existing = self._formula_at(sh, row, col)
        is_formula = isinstance(text, str) and text.startswith("=") and len(text) > 1
        if existing is not None and existing.kind == "column" and existing.row == row and is_formula:
            existing.text = text
            if fill == "column":
                existing.row_end = None
            self._compile_check(sh, existing)
            self.recalc([existing.id], seeds_are_formulas=True)
            self._touch(sh)
            return {"ok": True}
        if existing is not None:
            if existing.kind == "cell":
                self._clear_spill(existing)
                del self.formulas[existing.id]
            else:
                existing.exceptions.append(row)
        if is_formula:
            self._validate_strict(sh, col, None, text_is_formula=True)
            f = Formula(id=next(self._fid), sheet_id=sh.id, row=row, col_id=cid, text=text)
            if fill == "column" and row >= 1:
                f.kind = "column"
                self._remove_column_formulas(sh, cid, row)
            elif isinstance(fill, int) and row >= 1:
                f.kind = "column"
                f.row_end = int(fill)
            self._compile_check(sh, f)
            self.formulas[f.id] = f
            self.recalc([f.id], seeds_are_formulas=True)
        else:
            value, duck, fmt = parse_input(text)
            self._validate_strict(sh, col, value, row=row)
            self._write_cells(sh, row, col, [[value]], [duck])
            if fmt and sh.columns[col].fmt is None and row >= 1:
                sh.columns[col].fmt = fmt
            self.recalc([(sh.id, col, col, row, row)])
        self._touch(sh)
        return {"ok": True}

    def _compile_check(self, sh: Sheet, f: Formula):
        """Close dangling parentheses/quotes like Sheets does; other syntax errors show as #ERROR!."""
        f.text = autoclose(f.text)

    def _remove_column_formulas(self, sh: Sheet, cid: str, from_row: int):
        for fid in [
            f.id
            for f in self.formulas.values()
            if f.sheet_id == sh.id and f.col_id == cid and f.row >= from_row and f.id
        ]:
            f = self.formulas[fid]
            if f.kind == "cell":
                self._clear_spill(f)
            del self.formulas[fid]

    def set_range(
        self, sheet_id: str, row: int, col: int, values: list[list[Any]], label: str = "Paste"
    ) -> dict:
        """Write a block of user input (e.g. a paste).  Formulas are allowed."""
        sh = self.sheet(sheet_id)
        if not values:
            return {"ok": True}
        h = len(values)
        w = max(len(r) for r in values)
        self._begin(label, [(sh.id, "rect", row, row + h - 1)])
        self._ensure_cols(sh, col + w - 1)
        self._ensure_rows(sh, row + h - 1)
        # drop formulas in the target area
        self._drop_formulas_in(sh, row, row + h - 1, col, col + w - 1)
        parsed: list[list[Any]] = []
        col_types: list[list[str | None]] = [[] for _ in range(w)]
        new_formulas = []
        for i, r in enumerate(values):
            out = []
            for j in range(w):
                v = r[j] if j < len(r) else None
                if isinstance(v, str) and v.startswith("=") and len(v) > 1:
                    new_formulas.append(
                        Formula(
                            id=next(self._fid),
                            sheet_id=sh.id,
                            row=row + i,
                            col_id=sh.columns[col + j].id,
                            text=v,
                        )
                    )
                    out.append(None)
                    continue
                val, duck, _ = parse_input(v if v is None or isinstance(v, str) else v)
                out.append(val)
                if row + i >= 1:
                    col_types[j].append(duck)
            parsed.append(out)
        ducks = [common_type(t) for t in col_types]
        self._write_cells(sh, row, col, parsed, ducks)
        for f in new_formulas:
            self.formulas[f.id] = f
        self.recalc([(sh.id, col, col + w - 1, row, row + h - 1)] + [f.id for f in new_formulas], mixed=True)
        self._touch(sh)
        return {"ok": True}

    def clear_range(self, sheet_id: str, r1: int, c1: int, r2: int, c2: int, what: str = "contents") -> dict:
        sh = self.sheet(sheet_id)
        r2 = min(r2, sh.nrows)
        c2 = min(c2, len(sh.columns) - 1)
        if c2 < c1 or r2 < r1:
            return {"ok": True}
        whole_cols = r1 <= 1 and r2 >= sh.nrows
        scope = [(sh.id, "table")] if (r2 - r1) > 50000 else [(sh.id, "rect", r1, r2)]
        self._begin("Clear", scope)
        if what in ("contents", "all"):
            self._drop_formulas_in(sh, r1, r2, c1, c2, whole_cols=whole_cols)
            sets = ", ".join(f"{q(sh.columns[c].id)} = NULL" for c in range(c1, c2 + 1))
            lo, hi = max(r1, 1) - 1, r2 - 1
            if hi >= lo:
                self.con.execute(f"UPDATE {q(sh.table)} SET {sets} WHERE __rid BETWEEN {lo} AND {hi}")
            if r1 == 0:
                for c in range(c1, c2 + 1):
                    sh.columns[c].name = ""
        if what in ("formats", "all"):
            from .features import clear_styles

            clear_styles(sh, r1, r2, c1, c2)
        self.recalc([(sh.id, c1, c2, r1, r2)])
        self._touch(sh)
        return {"ok": True}

    def _drop_formulas_in(self, sh: Sheet, r1: int, r2: int, c1: int, c2: int, whole_cols: bool = False):
        ids = {sh.columns[c].id for c in range(c1, min(c2, len(sh.columns) - 1) + 1)}
        for f in list(self.formulas.values()):
            if f.sheet_id != sh.id or f.col_id not in ids:
                continue
            if f.kind == "cell" and r1 <= f.row <= r2:
                self._clear_spill(f)
                del self.formulas[f.id]
            elif f.kind == "column":
                end = f.row_end if f.row_end is not None else sh.nrows
                if r1 <= f.row and (r2 >= end or whole_cols):
                    del self.formulas[f.id]
                elif overlaps(r1, r2, f.row, end):
                    if r1 <= f.row:
                        # clearing the top part: keep the formula below the cleared area
                        f.exceptions.extend(range(f.row, r2 + 1))
                    else:
                        f.exceptions.extend(range(max(r1, f.row), min(r2, end) + 1))

    # -------------------------------------------------------- low-level writes

    def _ensure_cols(self, sh: Sheet, col: int):
        while len(sh.columns) <= col:
            c = Column(id=sh.new_col_id(), duck="VARCHAR", width=110)
            self.con.execute(f"ALTER TABLE {q(sh.table)} ADD COLUMN {q(c.id)} VARCHAR")
            sh.columns.append(c)

    def _ensure_rows(self, sh: Sheet, row: int):
        need = row  # sheet row r needs rid r-1 → nrows >= r
        if need > sh.nrows:
            self.con.execute(f"INSERT INTO {q(sh.table)} (__rid) SELECT range FROM range({sh.nrows}, {need})")
            sh.nrows = need

    def _outside_nonnull(self, sh: Sheet, col: Column, rid1: int, rid2: int) -> int:
        return self.con.execute(
            f"SELECT count({q(col.id)}) FROM {q(sh.table)} WHERE __rid < {rid1} OR __rid > {rid2}"
        ).fetchone()[0]

    def _retype(self, sh: Sheet, col: Column, new: str, clear: bool = False):
        new = norm_duck(new)
        if col.duck.upper() == new and not clear:
            return
        if clear:
            using = f"CAST(NULL AS {new})"
        elif new == "VARCHAR":
            using = txt(S(q(col.id), col.typ))
        else:
            using = f"TRY_CAST({q(col.id)} AS {new})"
        self.con.execute(f"ALTER TABLE {q(sh.table)} ALTER {q(col.id)} TYPE {new} USING {using}")
        col.duck = new

    def _prepare_col(self, sh: Sheet, col: Column, duck: str | None, rid1: int, rid2: int) -> str:
        """Make ``col`` able to hold values of ``duck`` in rows rid1..rid2; returns the column type."""
        if duck is None:
            return col.duck
        duck = norm_duck(duck)
        if col.duck.upper() == duck:
            return col.duck
        if self._outside_nonnull(sh, col, rid1, rid2) == 0:
            self._retype(sh, col, duck, clear=True)
            return col.duck
        merged = merge_duck(col.duck, duck)
        if col.duck.upper() == "BIGINT" and duck == "DOUBLE":
            merged = "DOUBLE"
        self._retype(sh, col, merged)
        return col.duck

    def _write_cells(self, sh: Sheet, row: int, col: int, values: list[list[Any]], ducks: list[str | None]):
        """Write python values into the grid starting at (row, col).  Row 0 sets header names."""
        h = len(values)
        w = max((len(r) for r in values), default=0)
        if row == 0:
            for j in range(w):
                v = values[0][j] if j < len(values[0]) else None
                sh.columns[col + j].name = value_to_text(v)
            values = values[1:]
            row = 1
            h -= 1
        if h <= 0:
            return
        rid1 = row - 1
        rid2 = rid1 + h - 1
        for j in range(w):
            column = sh.columns[col + j]
            target = self._prepare_col(sh, column, ducks[j] if j < len(ducks) else None, rid1, rid2)
            colvals = [r[j] if j < len(r) else None for r in values]
            if target.upper() == "VARCHAR":
                colvals = [
                    None if v is None else (v if isinstance(v, str) else value_to_text(v)) for v in colvals
                ]
            if h == 1:
                self.con.execute(
                    f"UPDATE {q(sh.table)} SET {q(column.id)} = TRY_CAST(? AS {target}) WHERE __rid = ?",
                    [colvals[0], rid1],
                )
            else:
                self._bulk_update(sh, column, target, rid1, colvals)

    def _bulk_update(self, sh: Sheet, column: Column, target: str, rid1: int, colvals: list):
        import pyarrow as pa

        tmp = f"__w{next(self._tmp)}"
        tbl = pa.table(
            {
                "i": pa.array(range(len(colvals)), pa.int64()),
                "v": pa.array(
                    [
                        None
                        if v is None
                        else (
                            v if isinstance(v, str) else value_to_text(v) if target == "VARCHAR" else str(v)
                        )
                        for v in colvals
                    ],
                    pa.string(),
                ),
            }
        )
        self.register(tmp, tbl)
        try:
            if target.upper() in ("DATE", "TIMESTAMP"):
                conv = f"TRY_CAST(s.v AS {target})"
            elif target.upper() == "BOOLEAN":
                conv = "TRY_CAST(s.v AS BOOLEAN)"
            else:
                conv = f"TRY_CAST(s.v AS {target})"
            self.con.execute(
                f"UPDATE {q(sh.table)} AS t SET {q(column.id)} = {conv} FROM {tmp} s "
                f"WHERE t.__rid = s.i + {rid1}"
            )
        finally:
            self.unregister(tmp)

    # ---------------------------------------------------------------- formulas

    def _compiler(self, sh: Sheet, row: int, col: int, mode: str) -> Compiler:
        return Compiler(self, sh, row, col, mode, con=self.con)

    def _out_region(self, f: Formula, attempted: bool = False) -> tuple | None:
        try:
            sh = self.sheet(f.sheet_id)
            c = sh.col_index(f.col_id)
        except (EngineError, KeyError):
            return None
        if f.kind == "column":
            return (sh.id, c, c, f.row, f.row_end)
        h, w = f.spill or (f.want if attempted and f.want else (1, 1))
        return (sh.id, c, c + w - 1, f.row, f.row + h - 1)

    def _depends_on(self, f: Formula, region: tuple) -> bool:
        sid, c1, c2, r1, r2 = region
        for dsid, dc1, dc2, dr1, dr2 in f.deps:
            if dsid == sid and overlaps(dc1, dc2, c1, c2) and overlaps(dr1, dr2, r1, r2):
                return True
        return False

    def recalc(self, seeds, seeds_are_formulas: bool = False, mixed: bool = False):
        """Recompute formulas affected by ``seeds`` (regions or formula ids). ``None`` = everything."""
        if seeds is None:
            todo = set(self.formulas)
            # compile all first so deps are known
            for f in self.formulas.values():
                self._refresh_deps(f)
        else:
            todo = set()
            regions = []
            for s in seeds:
                if isinstance(s, int):
                    todo.add(s)
                else:
                    regions.append(s)
            for f in self.formulas.values():
                if f.id in todo:
                    continue
                for reg in regions:
                    if self._depends_on(f, reg):
                        todo.add(f.id)
                        break
                    out = (
                        self._out_region(f, attempted=True)
                        if (f.kind == "cell" and (f.spill or f.want))
                        else None
                    )
                    if (
                        out is not None
                        and out[0] == reg[0]
                        and overlaps(out[1], out[2], reg[1], reg[2])
                        and overlaps(out[3], out[4], reg[3], reg[4])
                    ):
                        todo.add(f.id)  # an edit landed inside this formula's output → re-evaluate
                        break
            for fid in list(todo):
                self._refresh_deps(self.formulas[fid])
        # expand to dependents (transitively)
        frontier = list(todo)
        while frontier:
            fid = frontier.pop()
            f = self.formulas.get(fid)
            if f is None:
                continue
            out = self._out_region(f)
            if out is None:
                continue
            for g in self.formulas.values():
                if g.id not in todo and self._depends_on(g, out):
                    if not g.deps:
                        self._refresh_deps(g)
                    todo.add(g.id)
                    frontier.append(g.id)
        order, cyclic = self._topo(todo)
        for fid in cyclic:
            f = self.formulas[fid]
            f.error = "#REF! Circular dependency detected"
            self._write_error(f)
        for fid in order:
            f = self.formulas.get(fid)
            if f is not None:
                self._evaluate(f)

    def _recalc_sheet(self, sh: Sheet):
        """Recalculate after a structural change to ``sh`` (refs were rewritten)."""
        for f in self.formulas.values():
            self._refresh_deps(f)
        seeds: list = [(sh.id, 0, INF, 0, None)]
        seeds += [f.id for f in self.formulas.values() if f.sheet_id == sh.id]
        self.recalc(seeds)

    def _refresh_deps(self, f: Formula):
        """Conservative dependency regions from the formula's references."""
        deps = []
        try:
            sh = self.sheet(f.sheet_id)
            ast = P.parse_formula(f.text)
        except (P.FormulaSyntaxError, EngineError):
            f.deps = []
            return
        for ref in P.iter_refs(ast):
            tsh = self.get_sheet(ref.sheet) if ref.sheet else sh
            if tsh is None:
                continue
            r1 = ref.r1 if ref.r1 is not None else 0
            r2 = ref.r2
            if f.kind == "column" and r2 is not None and not ref.ar2:
                r2 = None  # relative rows slide down the column
            c1 = ref.c1 if ref.c1 is not None else 0
            c2 = ref.c2 if ref.c2 is not None else INF
            deps.append((tsh.id, c1, c2, r1, r2))
        f.deps = list(dict.fromkeys(deps))

    def _topo(self, ids: set[int]) -> tuple[list[int], list[int]]:
        ids = set(ids)
        outs = {i: self._out_region(self.formulas[i]) for i in ids if i in self.formulas}
        edges: dict[int, set[int]] = {i: set() for i in outs}
        indeg = dict.fromkeys(outs, 0)
        for i, out in outs.items():
            if out is None:
                continue
            for j in outs:
                if i != j and self._depends_on(self.formulas[j], out):
                    edges[i].add(j)
        for i in edges:
            for j in edges[i]:
                indeg[j] += 1
        # self-dependency = cycle
        cyclic = [i for i, out in outs.items() if out is not None and self._self_cycle(self.formulas[i], out)]
        ready = sorted(i for i in outs if indeg[i] == 0 and i not in cyclic)
        order = []
        while ready:
            i = ready.pop(0)
            order.append(i)
            for j in sorted(edges[i]):
                indeg[j] -= 1
                if indeg[j] == 0 and j not in cyclic:
                    ready.append(j)
        remaining = [i for i in outs if i not in order and i not in cyclic]
        return order, cyclic + remaining

    def _self_cycle(self, f: Formula, out: tuple) -> bool:
        if f.kind == "column":
            # a column formula reading rows of its own output range is circular in vectorised form
            return self._depends_on(f, out)
        sid, c1, c2, r1, r2 = out
        anchor = (sid, c1, c1, r1, r1)
        return self._depends_on(f, anchor)

    def _write_error(self, f: Formula):
        try:
            sh = self.sheet(f.sheet_id)
            c = sh.col_index(f.col_id)
        except (EngineError, KeyError):
            return
        self._clear_spill(f)
        if f.kind == "cell":
            if f.row == 0:
                sh.columns[c].name = ""
            else:
                self.con.execute(
                    f"UPDATE {q(sh.table)} SET {q(f.col_id)} = NULL WHERE __rid = ?", [f.row - 1]
                )
        else:
            cond = self._colf_cond(sh, f)
            self.con.execute(f"UPDATE {q(sh.table)} AS t SET {q(f.col_id)} = NULL WHERE {cond}")

    def _evaluate(self, f: Formula):
        sh = self.sheet(f.sheet_id)
        try:
            c = sh.col_index(f.col_id)
        except KeyError:
            del self.formulas[f.id]
            return
        f.error = None
        try:
            ast = P.parse_formula(f.text)
            mode = "row" if f.kind == "column" else "cell"
            comp = self._compiler(sh, f.row, c, mode)
            val = comp.compile(ast)
            self._refresh_deps(f)
            f.volatile = comp.volatile
            if f.kind == "column":
                if isinstance(val, A):
                    val = comp.to_scalar(val)
                self._eval_column(sh, f, val, comp)
            elif isinstance(val, A):
                self._spill(sh, f, c, val, comp)
            else:
                self._clear_spill(f)
                res = self.con.execute(f"SELECT {val.sql}").fetchone()[0]
                duck = self._duck_of(val)
                self._write_cells(sh, f.row, c, [[res]], [duck if res is not None else None])
        except FormulaError as e:
            f.error = e.code + (f" {e.message}" if e.message else "")
            self._write_error(f)
        except P.FormulaSyntaxError as e:
            f.error = f"#ERROR! Formula parse error: {e}"
            self._write_error(f)
        except duckdb.Error as e:
            msg = str(e).split("\n")[0]
            code = "#DIV/0!" if "Division by zero" in msg else "#VALUE!"
            f.error = f"{code} {msg[:300]}"
            self._write_error(f)

    def _duck_of(self, v: S) -> str:
        return {NUM: "DOUBLE", STR: "VARCHAR", "bool": "BOOLEAN", "date": "DATE", "ts": "TIMESTAMP"}.get(
            v.typ, "VARCHAR"
        )

    def _colf_cond(self, sh: Sheet, f: Formula) -> str:
        cond = f"t.__rid >= {f.row - 1}"
        if f.row_end is not None:
            cond += f" AND t.__rid <= {f.row_end - 1}"
        ex = sorted({r - 1 for r in f.exceptions if r >= 1})
        if ex:
            cond += f" AND t.__rid NOT IN ({', '.join(map(str, ex))})"
        return cond

    def _eval_column(self, sh: Sheet, f: Formula, val: S, comp: Compiler):
        col = sh.col_by_id(f.col_id)
        cond = self._colf_cond(sh, f)
        expr = val.sql
        probe = self.con.execute(f"SELECT {expr} AS v FROM {q(sh.table)} AS t LIMIT 0").description
        rtype = norm_duck(str(probe[0][1])) if probe else "VARCHAR"
        if val.typ == "null":
            rtype = col.duck
        rid1 = f.row - 1
        rid2 = (f.row_end - 1) if f.row_end is not None else sh.nrows - 1
        outside = self.con.execute(
            f"SELECT count({q(col.id)}) FROM {q(sh.table)} AS t WHERE NOT ({cond})"
        ).fetchone()[0]
        if outside == 0:
            target = rtype
            if col.duck.upper() != target:
                self._retype(sh, col, target, clear=True)
        else:
            target = merge_duck(col.duck, rtype)
            if col.duck.upper() != target:
                self._retype(sh, col, target)
        if target == "VARCHAR" and rtype != "VARCHAR":
            expr = txt(S(f"({expr})", duck_to_typ(rtype)))
        _ = rid1, rid2
        if comp.uses_window:
            self.con.execute(
                f"CREATE OR REPLACE TABLE {q(sh.table)} AS SELECT t.* REPLACE (CASE WHEN {cond} THEN "
                f"CAST({expr} AS {target}) ELSE t.{q(col.id)} END AS {q(col.id)}) FROM {q(sh.table)} AS t "
                f"ORDER BY t.__rid"
            )
        else:
            self.con.execute(
                f"UPDATE {q(sh.table)} AS t SET {q(col.id)} = CAST({expr} AS {target}) WHERE {cond}"
            )

    # ------------------------------------------------------------------ spills

    def _clear_spill(self, f: Formula):
        if f.kind != "cell" or not f.spill:
            return
        try:
            sh = self.sheet(f.sheet_id)
            c = sh.col_index(f.col_id)
        except (EngineError, KeyError):
            f.spill = None
            return
        h, w = f.spill
        f.spill = None
        r1, r2 = f.row, f.row + h - 1
        c2 = min(c + w - 1, len(sh.columns) - 1)
        if r1 == 0:
            for j in range(c, c2 + 1):
                if j != c:
                    sh.columns[j].name = ""
        lo = max(r1, 1) - 1
        hi = min(r2 - 1, sh.nrows - 1)
        if hi >= lo:
            for j in range(c, c2 + 1):
                cid = sh.columns[j].id
                if j == c:
                    extra = f" AND __rid <> {f.row - 1}" if f.row >= 1 else ""
                else:
                    extra = ""
                self.con.execute(
                    f"UPDATE {q(sh.table)} SET {q(cid)} = NULL WHERE __rid BETWEEN {lo} AND {hi}{extra}"
                )

    def _spill(self, sh: Sheet, f: Formula, c: int, a: A, comp: Compiler):
        self._clear_spill(f)
        tmp = f"__sp{next(self._tmp)}"
        header_vals = None
        if a.header is not None:
            sel = ", ".join(h.sql for h in a.header) or "NULL"
            header_vals = list(self.con.execute(f"SELECT {sel}").fetchone())
        if a.inline is not None:
            rows = a.inline
            width = a.ncols
            if not rows:
                rows = [[]]
            cells = [r[j] if j < len(r) else S("NULL", "null") for r in rows for j in range(width)]
            vals = self.con.execute(f"SELECT {', '.join(x.sql for x in cells) or 'NULL'}").fetchone()
            data = [list(vals[i * width : (i + 1) * width]) for i in range(len(rows))]
            types = []
            for j in range(width):
                types.append(
                    common_type(
                        [
                            self._duck_of(rows[i][j]) if j < len(rows[i]) and data[i][j] is not None else None
                            for i in range(len(rows))
                        ]
                    )
                    or "VARCHAR"
                )
            n = len(data)
        else:
            b = a.base
            where = " AND ".join(b.where) or "TRUE"
            sel = ", ".join(f"{x.sql} AS v{j}" for j, x in enumerate(a.cols))
            self.con.execute(
                f"CREATE OR REPLACE TEMP TABLE {tmp} AS SELECT row_number() OVER (ORDER BY __o) - 1 "
                f"AS __i, * EXCLUDE (__o) FROM (SELECT {sel}, {b.order} AS __o FROM {b.frm} "
                f"WHERE {where}) AS _s"
            )
            desc = self.con.execute(f"DESCRIBE {tmp}").fetchall()
            types = [norm_duck(str(d[1])) for d in desc[1:]]
            width = len(types)
            n = self.con.execute(f"SELECT count(*) FROM {tmp}").fetchone()[0]
            data = None
        try:
            hh = n + (1 if header_vals is not None else 0)
            if hh == 0 or width == 0:
                hh, width = 1, 1
            # collision check
            blocker = self._spill_blocked(sh, f, c, hh, width)
            f.want = (hh, width) if blocker else None
            if blocker:
                raise FormulaError(
                    "#REF!", f"Array result was not expanded because it would overwrite data in {blocker}."
                )
            self._ensure_cols(sh, c + width - 1)
            self._ensure_rows(sh, f.row + hh - 1)
            # assemble: rows of the spill grid; header first
            R = f.row
            start_i = 0  # index into data rows
            grid_row = R
            if header_vals is not None:
                self._write_cells(sh, grid_row, c, [header_vals], ["VARCHAR"] * width)
                grid_row += 1
            if data is not None:
                if data:
                    self._write_cells(sh, grid_row, c, data, types)
            else:
                if grid_row == 0 and n > 0:
                    first = self.con.execute(f"SELECT * EXCLUDE (__i) FROM {tmp} WHERE __i = 0").fetchone()
                    self._write_cells(sh, 0, c, [list(first)], ["VARCHAR"] * width)
                    grid_row = 1
                    start_i = 1
                if n - start_i > 0:
                    rid1 = grid_row - 1
                    rid2 = rid1 + (n - start_i) - 1
                    sets = []
                    for j in range(width):
                        col = sh.columns[c + j]
                        target = self._prepare_col(sh, col, types[j], rid1, rid2)
                        src = f"s.v{j}"
                        if target.upper() == "VARCHAR" and types[j] != "VARCHAR":
                            src = txt(S(src, duck_to_typ(types[j])))
                        elif target.upper() != types[j]:
                            src = f"TRY_CAST({src} AS {target})"
                        sets.append(f"{q(col.id)} = {src}")
                    self.con.execute(
                        f"UPDATE {q(sh.table)} AS t SET {', '.join(sets)} FROM {tmp} s "
                        f"WHERE t.__rid = s.__i + {rid1 - start_i} AND s.__i >= {start_i}"
                    )
            f.spill = (hh, width) if (hh, width) != (1, 1) else None
        finally:
            if data is None:
                self.con.execute(f"DROP TABLE IF EXISTS {tmp}")

    def _spill_blocked(self, sh: Sheet, f: Formula, c: int, h: int, w: int) -> str | None:
        R = f.row
        # other formulas anchored in the area
        for g in self.formulas.values():
            if g.id == f.id or g.sheet_id != sh.id:
                continue
            try:
                gc = sh.col_index(g.col_id)
            except KeyError:
                continue
            if g.kind == "cell" and R <= g.row < R + h and c <= gc < c + w:
                return cell_name(g.row, gc)
            if g.kind == "column" and c <= gc < c + w:
                end = g.row_end if g.row_end is not None else sh.nrows
                lo, hi = max(g.row, R), min(end, R + h - 1)
                if hi >= lo:
                    ex = {e for e in g.exceptions if lo <= e <= hi}
                    if gc == c and lo <= R <= hi:
                        ex.add(R)
                    if (hi - lo + 1) - len(ex) > 0:
                        first = next(r for r in range(lo, hi + 1) if r not in ex)
                        return cell_name(first, gc)
        ncols = len(sh.columns)
        if R == 0:
            for j in range(c + 1, min(c + w, ncols)):
                if sh.columns[j].name:
                    return cell_name(0, j)
        lo = max(R, 1) - 1
        hi = min(R + h - 2, sh.nrows - 1)
        if hi >= lo:
            for j in range(c, min(c + w, ncols)):
                cid = sh.columns[j].id
                extra = f" AND __rid <> {R - 1}" if (j == c and R >= 1) else ""
                r = self.con.execute(
                    f"SELECT min(__rid) FROM {q(sh.table)} WHERE __rid BETWEEN {lo} AND {hi} "
                    f"AND {q(cid)} IS NOT NULL{extra}"
                ).fetchone()[0]
                if r is not None:
                    return cell_name(r + 1, j)
        return None

    def _refresh_pivots(self):
        from .features import refresh_stale_pivots

        refresh_stale_pivots(self)

    # ------------------------------------------------------ fill & copy/paste

    def fill(self, sheet_id: str, src: list[int], dst: list[int]) -> dict:
        """Fill handle: extend ``src`` [r1,c1,r2,c2] to cover ``dst`` (a superset rectangle)."""
        sh = self.sheet(sheet_id)
        sr1, sc1, sr2, sc2 = src
        dr1, dc1, dr2, dc2 = dst
        self._begin("Fill", [(sh.id, "rect", dr1, dr2)] if dr2 - dr1 < 50000 else [(sh.id, "table")])
        self._ensure_cols(sh, dc2)
        self._ensure_rows(sh, dr2)
        down = dr2 > sr2 or dr1 < sr1
        if down:
            for c in range(sc1, sc2 + 1):
                self._fill_column(sh, c, sr1, sr2, dr1, dr2)
        else:
            for r in range(sr1, sr2 + 1):
                for c in range(dc1, dc2 + 1):
                    if sc1 <= c <= sc2:
                        continue
                    k = (c - sc1) % (sc2 - sc1 + 1)
                    srcc = sc1 + k
                    f = self._formula_at(sh, r, srcc)
                    if f is not None and f.kind == "cell":
                        text = P.shift_formula(f.text, 0, c - srcc)
                        self._drop_formulas_in(sh, r, r, c, c)
                        nf = Formula(
                            id=next(self._fid), sheet_id=sh.id, row=r, col_id=sh.columns[c].id, text=text
                        )
                        self.formulas[nf.id] = nf
                        self.recalc([nf.id], seeds_are_formulas=True)
                    else:
                        cell = self.get_cell(sh.id, r, srcc)
                        self._drop_formulas_in(sh, r, r, c, c)
                        value, duck, _ = parse_input(cell["input"]) if cell["input"] else (None, None, None)
                        self._write_cells(sh, r, c, [[value]], [duck])
            self.recalc([(sh.id, dc1, dc2, dr1, dr2)])
        self._touch(sh)
        return {"ok": True}

    def _fill_column(self, sh: Sheet, c: int, sr1: int, sr2: int, dr1: int, dr2: int):
        f = self._formula_at(sh, sr1, c)
        if f is not None and sr1 == sr2:
            text = (
                P.shift_formula(f.text, 0, 0) if f.kind == "cell" else P.shift_formula(f.text, sr1 - f.row, 0)
            )
            start = sr1 if dr1 == sr1 else dr1
            self._drop_formulas_in(sh, start, dr2, c, c)
            if start != sr1:
                text = P.shift_formula(text, start - sr1, 0)
            if start == 0:
                raise EngineError("Fill down from the header row is not supported")
            nf = Formula(
                id=next(self._fid),
                sheet_id=sh.id,
                row=start,
                col_id=sh.columns[c].id,
                text=text,
                kind="column",
                row_end=dr2 if dr2 < sh.nrows else None,
            )
            self.formulas[nf.id] = nf
            self.recalc([nf.id], seeds_are_formulas=True)
            return
        # values: detect a linear numeric series, else repeat the pattern
        n = sr2 - sr1 + 1
        col = sh.columns[c]
        rows = self.con.execute(
            f"SELECT {q(col.id)} FROM {q(sh.table)} WHERE __rid BETWEEN ? AND ? ORDER BY __rid",
            [max(sr1, 1) - 1, sr2 - 1],
        ).fetchall()
        vals = [r[0] for r in rows]
        if sr1 == 0:
            vals = [col.name] + vals
        step = None
        if n >= 2 and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in vals):
            diffs = {round(vals[i + 1] - vals[i], 9) for i in range(n - 1)}
            if len(diffs) == 1:
                step = diffs.pop()
        lo, hi = (sr2 + 1, dr2) if dr2 > sr2 else (dr1, sr1 - 1)
        if hi < lo:
            return
        self._drop_formulas_in(sh, lo, hi, c, c)
        rid_lo = lo - 1
        cnt = hi - lo + 1
        if step is not None:
            if dr2 > sr2:
                expr = f"CAST({vals[-1]!r} AS DOUBLE) + (range + 1) * {step!r}"
            else:
                expr = f"CAST({vals[0]!r} AS DOUBLE) - ({cnt} - range) * {step!r}"
            self._prepare_col(sh, col, "DOUBLE", rid_lo, rid_lo + cnt - 1)
            self.con.execute(
                f"UPDATE {q(sh.table)} AS t SET {q(col.id)} = s.v FROM "
                f"(SELECT range AS i, {expr} AS v FROM range({cnt})) s WHERE t.__rid = s.i + {rid_lo}"
            )
        else:
            duck = col.duck
            arr = ", ".join(f"TRY_CAST({duck_literal(v)} AS {duck})" for v in vals)
            self.con.execute(
                f"UPDATE {q(sh.table)} AS t SET {q(col.id)} = TRY_CAST(([{arr}])[((s.i + {(lo - sr1) % n}) % {n}) + 1] AS {duck}) "
                f"FROM (SELECT range AS i FROM range({cnt})) s WHERE t.__rid = s.i + {rid_lo}"
            )
        self.recalc([(sh.id, c, c, lo, hi)])

    def get_range_text(
        self, sheet_id: str, r1: int, c1: int, r2: int, c2: int, limit: int = 2_000_000
    ) -> dict:
        sh = self.sheet(sheet_id)
        c2 = min(c2, len(sh.columns) - 1)
        if c2 < c1:
            return {"tsv": "", "rows": 0}
        if (r2 - r1 + 1) * (c2 - c1 + 1) > limit:
            raise EngineError("Selection is too large to copy to the clipboard. Use Export instead.")
        lines = []
        cols = sh.columns[c1 : c2 + 1]
        if r1 == 0:
            lines.append("\t".join(c.name for c in cols))
        lo, hi = max(r1, 1) - 1, min(r2 - 1, sh.nrows - 1)
        if hi >= lo:
            sel = ", ".join(txt(S(q(c.id), c.typ)) for c in cols)
            for row in self.con.execute(
                f"SELECT {sel} FROM {q(sh.table)} WHERE __rid BETWEEN {lo} AND {hi} ORDER BY __rid"
            ).fetchall():
                lines.append(
                    "\t".join("" if v is None else str(v).replace("\t", " ").replace("\n", " ") for v in row)
                )
        return {"tsv": "\n".join(lines), "rows": len(lines)}

    def paste_internal(self, sheet_id: str, src: dict, row: int, col: int, mode: str = "all") -> dict:
        """Paste a range copied inside the app: formulas shift, types are kept."""
        ssh = self.sheet(src["sheet"])
        sh = self.sheet(sheet_id)
        r1, c1, r2, c2 = src["r1"], src["c1"], src["r2"], src["c2"]
        r2 = min(r2, ssh.nrows)
        c2 = min(c2, len(ssh.columns) - 1)
        h, w = r2 - r1 + 1, c2 - c1 + 1
        if h <= 0 or w <= 0:
            return {"ok": True}
        scope = [(sh.id, "table")] if h > 50000 else [(sh.id, "rect", row, row + h - 1)]
        self._begin("Paste", scope)
        # snapshot source (it may overlap the destination)
        tmp = f"__cp{next(self._tmp)}"
        cols = ssh.columns[c1 : c2 + 1]
        self.con.execute(
            f"CREATE OR REPLACE TEMP TABLE {tmp} AS SELECT __rid - {max(r1, 1) - 1} AS i, "
            f"{', '.join(q(c.id) for c in cols)} FROM {q(ssh.table)} "
            f"WHERE __rid BETWEEN {max(r1, 1) - 1} AND {r2 - 1}"
        )
        header = [c.name for c in cols] if r1 == 0 else None
        src_formulas = []
        if mode != "values":
            for f in self.formulas.values():
                if f.sheet_id != ssh.id:
                    continue
                try:
                    fc = ssh.col_index(f.col_id)
                except KeyError:
                    continue
                if c1 <= fc <= c2 and r1 <= f.row <= r2:
                    src_formulas.append((f, fc))
        src_styles = []
        if mode == "all":
            from .features import styles_in

            src_styles = styles_in(ssh, r1, r2, c1, c2)
        try:
            self._ensure_cols(sh, col + w - 1)
            self._ensure_rows(sh, row + h - 1)
            self._drop_formulas_in(sh, row, row + h - 1, col, col + w - 1)
            grid_row = row
            if header is not None:
                self._write_cells(sh, row, col, [header], ["VARCHAR"] * w)
                grid_row += 1
            n = h - (1 if header is not None else 0)
            if n > 0 and grid_row >= 1:
                rid1 = grid_row - 1
                for j, sc in enumerate(cols):
                    dcol = sh.columns[col + j]
                    target = self._prepare_col(sh, dcol, sc.duck, rid1, rid1 + n - 1)
                    srcexpr = f"s.{q(sc.id)}"
                    if target.upper() == "VARCHAR" and sc.duck.upper() != "VARCHAR":
                        srcexpr = txt(S(srcexpr, sc.typ))
                    self.con.execute(
                        f"UPDATE {q(sh.table)} AS t SET {q(dcol.id)} = CAST({srcexpr} AS {target}) "
                        f"FROM {tmp} s WHERE t.__rid = s.i + {rid1}"
                    )
                    if sc.fmt and not dcol.fmt:
                        dcol.fmt = sc.fmt
            dr, dc = row - r1, col - c1
            new_ids = []
            for f, fc in src_formulas:
                nf = Formula(
                    id=next(self._fid),
                    sheet_id=sh.id,
                    row=f.row + dr,
                    col_id=sh.columns[fc + dc].id,
                    text=P.shift_formula(f.text, dr, dc),
                    kind=f.kind,
                    row_end=(f.row_end + dr)
                    if f.row_end is not None
                    else (
                        None
                        if f.kind == "column" and row + h - 1 >= sh.nrows
                        else (row + h - 1 if f.kind == "column" else None)
                    ),
                    exceptions=[e + dr for e in f.exceptions],
                )
                self.formulas[nf.id] = nf
                new_ids.append(nf.id)
            for st in src_styles:
                st = dict(st)
                st["r1"] = st["r1"] + dr if st.get("r1") is not None else None
                st["r2"] = st["r2"] + dr if st.get("r2") is not None else None
                st["c1"] = st["c1"] + dc
                st["c2"] = st["c2"] + dc
                sh.styles.append(st)
        finally:
            self.con.execute(f"DROP TABLE IF EXISTS {tmp}")
        self.recalc([(sh.id, col, col + w - 1, row, row + h - 1)] + new_ids)
        self._touch(sh)
        return {"ok": True}

    # ------------------------------------------------------- structure editing

    def _rewrite_all_refs(self, sheet: Sheet, fn_rows=None, fn_cols=None):
        """Rewrite references pointing at ``sheet`` in every formula."""

        def make(f: Formula):
            fsheet = self.sheet(f.sheet_id)

            def tr(ref: Ref):
                target = self.get_sheet(ref.sheet) if ref.sheet else fsheet
                if target is not sheet:
                    return ref
                r1, r2, c1, c2 = ref.r1, ref.r2, ref.c1, ref.c2
                if fn_rows is not None:
                    res = fn_rows(r1, r2, ref.is_cell)
                    if res is None:
                        return None
                    r1, r2 = res
                if fn_cols is not None:
                    res = fn_cols(c1, c2, ref.is_cell)
                    if res is None:
                        return None
                    c1, c2 = res
                return Ref(ref.sheet, r1, c1, r2, c2, ref.ar1, ref.ac1, ref.ar2, ref.ac2, ref.rng)

            return tr

        for f in self.formulas.values():
            f.text = P.transform_refs(f.text, make(f))
            f.deps = []

    def insert_rows(self, sheet_id: str, row: int, count: int = 1) -> dict:
        sh = self.sheet(sheet_id)
        if row < 1:
            raise EngineError("Rows can only be inserted below the header row")
        self._begin("Insert rows", [(sh.id, "table")])
        rid = row - 1
        self.con.execute(f"UPDATE {q(sh.table)} SET __rid = __rid + {count} WHERE __rid >= {rid}")
        self.con.execute(f"INSERT INTO {q(sh.table)} (__rid) SELECT range FROM range({rid}, {rid + count})")
        self._resort(sh)
        sh.nrows += count
        shift = _shift_insert(row, count)
        self._rewrite_all_refs(sh, fn_rows=shift)
        for f in self.formulas.values():
            if f.sheet_id == sh.id:
                if f.row >= row:
                    f.row += count
                if f.row_end is not None and f.row_end >= row:
                    f.row_end += count
                f.exceptions = [e + count if e >= row else e for e in f.exceptions]
        from .features import shift_rules_rows

        shift_rules_rows(sh, lambda r1, r2: shift(r1, r2, False))
        self._apply_filters(sh)
        self._recalc_sheet(sh)
        self._touch(sh)
        return {"ok": True}

    def delete_rows(self, sheet_id: str, row1: int, row2: int) -> dict:
        sh = self.sheet(sheet_id)
        row1 = max(row1, 1)
        row2 = min(row2, sh.nrows)
        if row2 < row1:
            return {"ok": True}
        self._begin("Delete rows", [(sh.id, "table")])
        n = row2 - row1 + 1
        lo, hi = row1 - 1, row2 - 1
        self.con.execute(f"DELETE FROM {q(sh.table)} WHERE __rid BETWEEN {lo} AND {hi}")
        self.con.execute(f"UPDATE {q(sh.table)} SET __rid = __rid - {n} WHERE __rid > {hi}")
        self._resort(sh)
        sh.nrows -= n
        shift = _shift_delete(row1, row2)
        for fid in [
            f.id
            for f in self.formulas.values()
            if f.sheet_id == sh.id and f.kind == "cell" and row1 <= f.row <= row2
        ]:
            del self.formulas[fid]
        self._rewrite_all_refs(sh, fn_rows=shift)
        for f in list(self.formulas.values()):
            if f.sheet_id != sh.id:
                continue
            if f.kind == "column":
                end = f.row_end
                res = shift(f.row, end, False)
                if res is None:
                    del self.formulas[f.id]
                    continue
                f.row, f.row_end = res
                f.exceptions = [e - n if e > row2 else e for e in f.exceptions if not (row1 <= e <= row2)]
            elif f.row > row2:
                f.row -= n
        from .features import shift_rules_rows

        shift_rules_rows(sh, lambda r1, r2: shift(r1, r2, False))
        self._apply_filters(sh)
        self._recalc_sheet(sh)
        self._touch(sh)
        return {"ok": True}

    def insert_cols(self, sheet_id: str, col: int, count: int = 1) -> dict:
        sh = self.sheet(sheet_id)
        self._begin("Insert columns", [])
        self._ensure_cols(sh, col - 1) if col > 0 else None
        new = []
        for _ in range(count):
            c = Column(id=sh.new_col_id(), duck="VARCHAR", width=110)
            self.con.execute(f"ALTER TABLE {q(sh.table)} ADD COLUMN {q(c.id)} VARCHAR")
            new.append(c)
        sh.columns[col:col] = new
        shift = _shift_insert(col, count)
        self._rewrite_all_refs(sh, fn_cols=shift)
        from .features import shift_rules_cols

        shift_rules_cols(sh, lambda c1, c2: shift(c1, c2, False))
        if sh.frozen_cols > col:
            sh.frozen_cols += count
        self._recalc_sheet(sh)
        self._touch(sh)
        return {"ok": True}

    def delete_cols(self, sheet_id: str, col1: int, col2: int) -> dict:
        sh = self.sheet(sheet_id)
        col2 = min(col2, len(sh.columns) - 1)
        if col2 < col1:
            return {"ok": True}
        self._begin("Delete columns", [(sh.id, "table")])
        doomed = sh.columns[col1 : col2 + 1]
        ids = {c.id for c in doomed}
        for fid in [f.id for f in self.formulas.values() if f.sheet_id == sh.id and f.col_id in ids]:
            del self.formulas[fid]
        for c in doomed:
            self.con.execute(f"ALTER TABLE {q(sh.table)} DROP COLUMN {q(c.id)}")
        del sh.columns[col1 : col2 + 1]
        for cid in ids:
            sh.filters.pop(cid, None)
        shift = _shift_delete(col1, col2)
        self._rewrite_all_refs(sh, fn_cols=shift)
        from .features import shift_rules_cols

        shift_rules_cols(sh, lambda c1, c2: shift(c1, c2, False), removed=ids)
        if sh.frozen_cols > col1:
            sh.frozen_cols = max(col1, sh.frozen_cols - (col2 - col1 + 1))
        if not sh.columns:
            self._ensure_cols(sh, 0)
        self._apply_filters(sh)
        self._recalc_sheet(sh)
        self._touch(sh)
        return {"ok": True}

    def move_cols(self, sheet_id: str, col1: int, col2: int, to: int) -> dict:
        """Reorder columns (drag & drop).  Formulas referencing them are rewritten."""
        sh = self.sheet(sheet_id)
        self._begin("Move columns", [])
        n = len(sh.columns)
        order = list(range(n))
        block = order[col1 : col2 + 1]
        rest = order[:col1] + order[col2 + 1 :]
        to = max(0, min(to, len(rest)))
        new_order = rest[:to] + block + rest[to:]
        pos = {old: new for new, old in enumerate(new_order)}

        def fn_cols(c1, c2, is_cell):
            if c1 is None:
                return c1, c2
            if is_cell or c1 == c2:
                return pos.get(c1, c1), pos.get(c2, c2)
            mapped = sorted(pos.get(c, c) for c in range(c1, (c2 if c2 is not None else c1) + 1))
            return mapped[0], mapped[-1]

        self._rewrite_all_refs(sh, fn_cols=fn_cols)
        sh.columns = [sh.columns[i] for i in new_order]
        self._recalc_sheet(sh)
        self._touch(sh)
        return {"ok": True}

    def _resort(self, sh: Sheet):
        self.con.execute(
            f"CREATE OR REPLACE TABLE {q(sh.table)} AS SELECT * FROM {q(sh.table)} ORDER BY __rid"
        )

    def sort(self, sheet_id: str, keys: list[dict]) -> dict:
        """Physically sort all data rows by ``keys`` = [{"col": int, "asc": bool}]."""
        sh = self.sheet(sheet_id)
        if not keys or sh.nrows == 0:
            return {"ok": True}
        self._begin("Sort", [(sh.id, "table")])
        order = []
        for k in keys:
            c = sh.columns[k["col"]]
            expr = f"lower({q(c.id)})" if c.typ == STR else q(c.id)
            order.append(f"{expr} {'ASC' if k.get('asc', True) else 'DESC'} NULLS LAST")
        order.append("__rid")
        # spills are recomputed after sorting; clear them so data doesn't get scrambled
        mine = [f for f in self.formulas.values() if f.sheet_id == sh.id]
        for f in mine:
            if f.kind == "cell":
                self._clear_spill(f)
        mp = f"__map{next(self._tmp)}"
        self.con.execute(
            f"CREATE OR REPLACE TEMP TABLE {mp} AS SELECT __rid AS old, "
            f"row_number() OVER (ORDER BY {', '.join(order)}) - 1 AS new FROM {q(sh.table)}"
        )
        self.con.execute(
            f"CREATE OR REPLACE TABLE {q(sh.table)} AS SELECT m.new AS __rid, t.* EXCLUDE (__rid) "
            f"FROM {q(sh.table)} t JOIN {mp} m ON m.old = t.__rid ORDER BY m.new"
        )
        moved = [f for f in mine if f.kind == "cell" and f.row >= 1]
        if moved:
            olds = ", ".join(str(f.row - 1) for f in moved)
            mapping = dict(self.con.execute(f"SELECT old, new FROM {mp} WHERE old IN ({olds})").fetchall())
            for f in moved:
                f.row = mapping.get(f.row - 1, f.row - 1) + 1
        for f in mine:
            if f.kind == "column" and f.exceptions:
                olds = ", ".join(str(e - 1) for e in f.exceptions if e >= 1)
                if olds:
                    mapping = dict(
                        self.con.execute(f"SELECT old, new FROM {mp} WHERE old IN ({olds})").fetchall()
                    )
                    f.exceptions = [mapping.get(e - 1, e - 1) + 1 for e in f.exceptions]
        self.con.execute(f"DROP TABLE IF EXISTS {mp}")
        self._apply_filters(sh)
        self._recalc_sheet(sh)
        self._touch(sh)
        return {"ok": True}

    # ------------------------------------------------------------------ filters

    def _drop_view(self, sh: Sheet):
        if sh.view_table:
            self.con.execute(f"DROP TABLE IF EXISTS {q(sh.view_table)}")
        sh.view_table = None
        sh.nview = 0

    def _apply_filters(self, sh: Sheet):
        from .features import filter_condition

        conds = []
        for cid, spec in sh.filters.items():
            try:
                col = sh.col_by_id(cid)
            except KeyError:
                continue
            cnd = filter_condition(col, spec)
            if cnd:
                conds.append(cnd)
        if not conds or not sh.filter_on:
            self._drop_view(sh)
            return
        name = f"__view_{sh.id}"
        self.con.execute(
            f"CREATE OR REPLACE TABLE {q(name)} AS SELECT row_number() OVER (ORDER BY __rid) - 1 AS pos, "
            f"__rid AS rid FROM {q(sh.table)} AS t WHERE {' AND '.join(conds)} ORDER BY __rid"
        )
        sh.view_table = name
        sh.nview = self.con.execute(f"SELECT count(*) FROM {q(name)}").fetchone()[0]

    def set_filter(self, sheet_id: str, col: int, spec: dict | None) -> dict:
        sh = self.sheet(sheet_id)
        self._begin("Filter", [])
        cid = sh.columns[col].id
        if spec:
            sh.filters[cid] = spec
        else:
            sh.filters.pop(cid, None)
        sh.filter_on = True
        self._apply_filters(sh)
        self._touch(sh)
        return {"nview": sh.nview if sh.view_table else sh.nrows}

    def toggle_filter(self, sheet_id: str, on: bool | None = None) -> dict:
        sh = self.sheet(sheet_id)
        self._begin("Toggle filter", [])
        sh.filter_on = (not sh.filter_on) if on is None else bool(on)
        if not sh.filter_on:
            sh.filters = {}
        self._apply_filters(sh)
        self._touch(sh)
        return {"filter_on": sh.filter_on}

    def column_values(self, sheet_id: str, col: int, search: str = "", limit: int = 500) -> dict:
        from .features import filter_condition

        sh = self.sheet(sheet_id)
        c = sh.columns[col]
        conds = []
        for cid, spec in sh.filters.items():
            if cid == c.id:
                continue
            try:
                other = sh.col_by_id(cid)
            except KeyError:
                continue
            e = filter_condition(other, spec)
            if e:
                conds.append(e)
        tx = txt(S(f"t.{q(c.id)}", c.typ))
        if search:
            conds.append(f"{tx} ILIKE ?")
        where = " AND ".join(conds) or "TRUE"
        params = [f"%{search}%"] if search else []
        order = f"lower({tx})" if c.typ == STR else f"t.{q(c.id)}"
        rows = self.con.execute(
            f"SELECT t.{q(c.id)} AS v, count(*) AS n FROM {q(sh.table)} t WHERE {where} GROUP BY 1 "
            f"ORDER BY {order.replace('t.' + q(c.id), 'v') if c.typ != STR else 'lower(' + txt(S('v', c.typ)) + ')'} "
            f"NULLS FIRST LIMIT {limit + 1}",
            params,
        ).fetchall()
        total = self.con.execute(
            f"SELECT count(DISTINCT t.{q(c.id)}) FROM {q(sh.table)} t WHERE {where}", params
        ).fetchone()[0]
        return {
            "values": [[to_json(v), n] for v, n in rows[:limit]],
            "truncated": len(rows) > limit,
            "distinct": total,
            "type": c.typ,
            "spec": sh.filters.get(c.id),
        }

    # -------------------------------------------------------------------- undo

    def _begin(self, label: str, scopes: list):
        """Snapshot what the coming command may change."""
        entry = {"label": label, "meta": self._meta_snapshot(), "data": []}
        for sc in scopes:
            entry["data"].append(self._data_snapshot(sc))
        self.undo_stack.append(entry)
        self._pending = entry
        self._trim(self.undo_stack)
        for e in self.redo_stack:
            self._drop_entry(e)
        self.redo_stack.clear()

    def _meta_snapshot(self) -> dict:
        return {
            "sheets": [copy.deepcopy(s.to_meta()) for s in self.sheets],
            "formulas": copy.deepcopy(self.formulas),
        }

    def _data_snapshot(self, sc: tuple) -> dict:
        sid, kind = sc[0], sc[1]
        sh = self.sheet(sid)
        name = f"__undo{next(self._tmp)}"
        if kind == "table":
            self.con.execute(f"CREATE TABLE {q(name)} AS SELECT * FROM {q(sh.table)} ORDER BY __rid")
            return {"sheet": sid, "kind": "table", "name": name, "table": sh.table}
        r1, r2 = sc[2], sc[3]
        lo, hi = max(r1, 1) - 1, max(r2, 1) - 1
        self.con.execute(
            f"CREATE TABLE {q(name)} AS SELECT * FROM {q(sh.table)} WHERE __rid BETWEEN {lo} AND {hi}"
        )
        return {
            "sheet": sid,
            "kind": "rect",
            "name": name,
            "lo": lo,
            "hi": hi,
            "nrows": sh.nrows,
            "cols": [(c.id, c.duck) for c in sh.columns],
            "table": sh.table,
        }

    def _drop_entry(self, e: dict):
        for d in e.get("data", []):
            self.con.execute(f"DROP TABLE IF EXISTS {q(d['name'])}")

    def _trim(self, stack: list, limit: int = 60):
        while len(stack) > limit:
            self._drop_entry(stack.pop(0))
        big = [e for e in stack if any(d["kind"] == "table" for d in e["data"])]
        while len(big) > 12:
            e = big.pop(0)
            stack.remove(e)
            self._drop_entry(e)

    def _restore(self, entry: dict) -> dict:
        """Restore an entry; returns the inverse entry (for redo/undo)."""
        inverse = {"label": entry["label"], "meta": self._meta_snapshot(), "data": []}
        target_ids = {m["id"] for m in entry["meta"]["sheets"]}
        snap_ids = {d["sheet"] for d in entry["data"]}
        for s in self.sheets:
            if s.id not in target_ids and s.id not in snap_ids:
                inverse["data"].append(self._data_snapshot((s.id, "table")))
        for d in entry["data"]:
            sid = d["sheet"]
            exists = any(s.id == sid for s in self.sheets)
            if d["kind"] == "table" or not exists:
                if exists:
                    inverse["data"].append(self._data_snapshot((sid, "table")))
                else:
                    inverse["data"].append({"sheet": sid, "kind": "table", "name": None, "table": d["table"]})
            else:
                sh = self.sheet(sid)
                hi = max(d["hi"], sh.nrows - 1)
                inverse["data"].append(self._data_snapshot((sid, "rect", d["lo"] + 1, hi + 1)))
        # metadata
        self.sheets = [Sheet.from_meta(copy.deepcopy(m)) for m in entry["meta"]["sheets"]]
        self.formulas = copy.deepcopy(entry["meta"]["formulas"])
        live = {s.table for s in self.sheets}
        for d in entry["data"]:
            if d["kind"] == "table":
                if d["name"] is None:
                    self.con.execute(f"DROP TABLE IF EXISTS {q(d['table'])}")
                else:
                    self.con.execute(
                        f"CREATE OR REPLACE TABLE {q(d['table'])} AS SELECT * FROM {q(d['name'])} "
                        f"ORDER BY __rid"
                    )
            else:
                sh = next(s for s in self.sheets if s.id == d["sheet"])
                self._restore_rect(sh, d)
        # tables created by the undone command (e.g. a new sheet) disappear
        rows = self.con.execute(
            "SELECT table_name FROM duckdb_tables() WHERE table_name LIKE 't\\_%' ESCAPE '\\'"
        ).fetchall()
        for (tn,) in rows:
            if tn not in live:
                self.con.execute(f"DROP TABLE IF EXISTS {q(tn)}")
        # schema alignment for sheets without data snapshots (e.g. columns added)
        for sh in self.sheets:
            self._align_schema(sh)
            self._apply_filters(sh)
        return inverse

    def _align_schema(self, sh: Sheet):
        try:
            desc = self.con.execute(f"DESCRIBE {q(sh.table)}").fetchall()
        except duckdb.Error:
            return
        have = {r[0]: r[1] for r in desc}
        want = {c.id: c for c in sh.columns}
        for name in have:
            if name != "__rid" and name not in want:
                self.con.execute(f"ALTER TABLE {q(sh.table)} DROP COLUMN {q(name)}")
        for c in sh.columns:
            if c.id not in have:
                self.con.execute(f"ALTER TABLE {q(sh.table)} ADD COLUMN {q(c.id)} {c.duck}")
            elif norm_duck(str(have[c.id])) != c.duck.upper():
                cur = Column(id=c.id, duck=norm_duck(str(have[c.id])))
                using = (
                    txt(S(q(c.id), cur.typ))
                    if c.duck.upper() == "VARCHAR"
                    else f"TRY_CAST({q(c.id)} AS {c.duck})"
                )
                self.con.execute(f"ALTER TABLE {q(sh.table)} ALTER {q(c.id)} TYPE {c.duck} USING {using}")
        n = self.con.execute(f"SELECT count(*) FROM {q(sh.table)}").fetchone()[0]
        if n > sh.nrows:
            self.con.execute(f"DELETE FROM {q(sh.table)} WHERE __rid >= {sh.nrows}")
        elif n < sh.nrows:
            self.con.execute(f"INSERT INTO {q(sh.table)} (__rid) SELECT range FROM range({n}, {sh.nrows})")

    def _restore_rect(self, sh: Sheet, d: dict):
        self._align_schema(sh)
        snap_cols = [cid for cid, _ in d["cols"] if any(c.id == cid for c in sh.columns)]
        if snap_cols:
            sets = ", ".join(f"{q(c)} = s.{q(c)}" for c in snap_cols)
            self.con.execute(
                f"UPDATE {q(sh.table)} AS t SET {sets} FROM {q(d['name'])} s WHERE t.__rid = s.__rid"
            )

    def abort_pending(self):
        """Roll back a command that failed half-way."""
        entry, self._pending = self._pending, None
        if entry is None or not self.undo_stack or self.undo_stack[-1] is not entry:
            return
        self.undo_stack.pop()
        inv = self._restore(entry)
        self._drop_entry(entry)
        self._drop_entry(inv)
        self.recalc(None)
        self._touch()

    def undo(self) -> dict:
        if not self.undo_stack:
            return {"ok": False}
        entry = self.undo_stack.pop()
        inv = self._restore(entry)
        self._drop_entry(entry)
        self.redo_stack.append(inv)
        self.recalc(None)
        self._touch()
        for s in self.sheets:
            s.version += 1
        self._refresh_pivots()
        return {"ok": True, "label": entry["label"]}

    def redo(self) -> dict:
        if not self.redo_stack:
            return {"ok": False}
        entry = self.redo_stack.pop()
        inv = self._restore(entry)
        self._drop_entry(entry)
        self.undo_stack.append(inv)
        self.recalc(None)
        self._touch()
        for s in self.sheets:
            s.version += 1
        self._refresh_pivots()
        return {"ok": True, "label": entry["label"]}

    # ---------------------------------------------------------- strict checks

    def _validate_strict(
        self, sh: Sheet, col: int, value, text_is_formula: bool = False, row: int | None = None
    ):
        from .features import check_value_against_rules

        if text_is_formula:
            return
        msg = check_value_against_rules(self, sh, col, value, row)
        if msg:
            raise EngineError(msg)

    def close(self):
        try:
            self.con.close()
        except duckdb.Error:
            pass


def autoclose(text: str) -> str:
    depth = 0
    in_str = False
    for ch in text:
        if ch == '"':
            in_str = not in_str
        elif not in_str:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
    if in_str:
        text += '"'
    if depth > 0:
        text += ")" * depth
    return text


def value_to_input(v) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    return value_to_text(v)


def _shift_insert(at: int, n: int):
    def fn(a, b, is_cell):
        a2 = a + n if a is not None and a >= at else a
        b2 = b + n if b is not None and b >= at else b
        return a2, b2

    return fn


def _shift_delete(lo: int, hi: int):
    n = hi - lo + 1

    def fn(a, b, is_cell):
        if a is None and b is None:
            return a, b
        if is_cell:
            if lo <= a <= hi:
                return None
            return (a - n, b - n) if a > hi else (a, b)
        na = a
        if a is not None:
            if a > hi:
                na = a - n
            elif a >= lo:
                na = lo
        nb = b
        if b is not None:
            if b > hi:
                nb = b - n
            elif b >= lo:
                nb = lo - 1
        if na is not None and nb is not None and nb < na:
            return None
        return na, nb

    return fn


__all__ = ["Workbook", "EngineError", "col_to_letters"]
