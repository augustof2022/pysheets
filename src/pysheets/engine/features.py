"""Spreadsheet features built on the workbook: formatting, validation, find/replace,
duplicates, pivot tables and selection statistics."""

from __future__ import annotations

import itertools
import re
from typing import Any

import duckdb

from ..formula import parser as P
from ..formula.compiler import (
    BOOL,
    DATE,
    NUM,
    STR,
    TS,
    A,
    FormulaError,
    S,
    as_date,
    boo,
    num_nullable,
    sql_str,
    txt,
)
from ..formula.refs import col_to_letters
from .model import Column, Sheet
from .values import duck_literal, to_json
from .workbook import EngineError, Workbook, merge_duck, q

_rule_ids = itertools.count(1)


def _rid() -> str:
    return f"r{next(_rule_ids)}"


# ----------------------------------------------------------------- literals


def _lit_for(col: Column, v: Any) -> str:
    """SQL literal of ``v`` matching the column type (filter values come from JSON)."""
    if v is None:
        return "NULL"
    t = col.typ
    if t == NUM:
        try:
            return f"CAST({float(v)!r} AS DOUBLE)"
        except (TypeError, ValueError):
            return "NULL"
    if t == BOOL:
        if isinstance(v, str):
            return "TRUE" if v.upper() == "TRUE" else "FALSE"
        return "TRUE" if v else "FALSE"
    if t == DATE:
        return f"TRY_CAST({sql_str(str(v))} AS DATE)"
    if t == TS:
        return f"TRY_CAST({sql_str(str(v))} AS TIMESTAMP)"
    return sql_str(str(v))


def _is_number(v) -> bool:
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return True
    try:
        float(str(v).replace(",", ""))
        return True
    except (TypeError, ValueError):
        return False


def _compare_expr(col: Column, x: str, op: str, v: Any, v2: Any = None) -> str | None:
    """Comparison of column expression ``x`` against user value(s)."""
    xs = S(x, col.typ)
    ops = {"gt": ">", "gte": ">=", "lt": "<", "lte": "<=", "eq": "=", "neq": "<>"}
    if op in ("between", "not_between"):
        if col.typ in (DATE, TS):
            e = f"({as_date(xs)} BETWEEN TRY_CAST({sql_str(str(v))} AS DATE) AND TRY_CAST({sql_str(str(v2))} AS DATE))"
        else:
            e = f"({num_nullable(xs)} BETWEEN {float(v)!r} AND {float(v2)!r})"
        return e if op == "between" else f"(NOT {e})"
    if op in ops:
        o = ops[op]
        if col.typ in (DATE, TS) and not _is_number(v):
            return f"({as_date(xs)} {o} TRY_CAST({sql_str(str(v))} AS DATE))"
        if _is_number(v) and col.typ != STR or (_is_number(v) and op not in ("eq", "neq")):
            return f"({num_nullable(xs)} {o} {float(str(v).replace(',', ''))!r})"
        return f"(lower({txt(xs)}) {o} lower({sql_str(str(v))}))"
    if op == "contains":
        return f"({txt(xs)} ILIKE {sql_str('%' + _esc_like(str(v)) + '%')} ESCAPE '\\')"
    if op == "not_contains":
        return f"(NOT coalesce({txt(xs)} ILIKE {sql_str('%' + _esc_like(str(v)) + '%')} ESCAPE '\\', FALSE))"
    if op == "starts":
        return f"({txt(xs)} ILIKE {sql_str(_esc_like(str(v)) + '%')} ESCAPE '\\')"
    if op == "ends":
        return f"({txt(xs)} ILIKE {sql_str('%' + _esc_like(str(v)))} ESCAPE '\\')"
    if op == "exact":
        return f"(lower({txt(xs)}) = lower({sql_str(str(v))}))"
    if op == "empty":
        return f"({x} IS NULL OR {txt(xs)} = '')"
    if op == "not_empty":
        return f"({x} IS NOT NULL AND {txt(xs)} <> '')"
    if op == "regex":
        return f"regexp_matches({txt(xs)}, {sql_str(str(v))})"
    return None


def _esc_like(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# ------------------------------------------------------------------ filters


def filter_condition(col: Column, spec: dict | None, alias: str = "t") -> str | None:
    if not spec:
        return None
    x = f"{alias}.{q(col.id)}"
    parts = []
    if spec.get("include") is not None:
        vals = spec["include"]
        lits = [_lit_for(col, v) for v in vals if v is not None]
        blank = any(v is None for v in vals)
        ors = []
        if lits:
            if col.typ == STR:
                ors.append(f"{x} IN ({', '.join(lits)})")
            else:
                ors.append(f"{x} IN ({', '.join(lits)})")
        if blank:
            ors.append(f"{x} IS NULL")
        parts.append("(" + (" OR ".join(ors) or "FALSE") + ")")
    if spec.get("exclude"):
        vals = spec["exclude"]
        lits = [_lit_for(col, v) for v in vals if v is not None]
        blank = any(v is None for v in vals)
        ands = []
        if lits:
            ands.append(f"({x} IS NULL OR {x} NOT IN ({', '.join(lits)}))")
        if blank:
            ands.append(f"{x} IS NOT NULL")
        parts.append("(" + " AND ".join(ands) + ")")
    cond = spec.get("cond")
    if cond and cond.get("op"):
        e = _compare_expr(col, x, cond["op"], cond.get("value"), cond.get("value2"))
        if e:
            parts.append(f"coalesce({e}, FALSE)")
    return " AND ".join(parts) if parts else None


# ------------------------------------------------------------------ styles


def set_style(wb: Workbook, sheet_id: str, rects: list[list[int]], patch: dict) -> dict:
    sh = wb.sheet(sheet_id)
    wb._begin("Format", [])
    for r1, c1, r2, c2 in rects:
        _add_style(wb, sh, r1, c1, r2, c2, dict(patch))
    wb._touch(sh)
    return {"ok": True}


def _add_style(
    wb: Workbook, sh: Sheet, r1: int, c1: int, r2: int, c2: int, patch: dict, merge_same: bool = True
):
    if c2 < 5000:
        wb._ensure_cols(sh, c2)
    full_col = r1 <= 1 and r2 >= max(sh.nrows, 1)
    if "fmt" in patch and full_col:
        for c in range(c1, min(c2, len(sh.columns) - 1) + 1):
            sh.columns[c].fmt = patch["fmt"]
        patch = {k: v for k, v in patch.items() if k != "fmt"}
        if not patch:
            return
    entry = {"r1": r1, "r2": None if (full_col or r2 >= sh.nrows + 1000) else r2, "c1": c1, "c2": c2}
    if merge_same:
        for st in reversed(sh.styles):
            if all(st.get(k) == entry[k] for k in ("r1", "r2", "c1", "c2")):
                st.update(patch)
                break
        else:
            entry.update(patch)
            sh.styles.append(entry)
    else:
        entry.update(patch)
        sh.styles.append(entry)
    if len(sh.styles) > 20000:
        sh.styles = sh.styles[-20000:]


BORDER_MODES = ("all", "outer", "inner", "horizontal", "vertical", "top", "bottom", "left", "right", "none")


def set_borders(
    wb: Workbook, sheet_id: str, rects: list[list[int]], mode: str, line: str = "thin", color: str = "#1f2937"
) -> dict:
    """Apply borders like Sheets' border menu.  Each edge is stored as ``"<line>:<color>"``."""
    if mode not in BORDER_MODES:
        raise EngineError(f"Unknown border mode {mode}")
    sh = wb.sheet(sheet_id)
    wb._begin("Borders", [])
    spec = f"{line}:{color}"
    for r1, c1, r2, c2 in rects:
        open_end = r2 >= max(sh.nrows, 1) and r1 <= 1
        r2 = max(r1, min(r2, sh.nrows + 1000))
        parts: list[tuple[int, int, int, int, dict]] = []
        if mode == "none":
            parts.append((r1, c1, r2, c2, {"bt": None, "bb": None, "bl": None, "br": None}))
            # neighbours drawing onto this range's outer edges
            if r1 > 0:
                parts.append((r1 - 1, c1, r1 - 1, c2, {"bb": None}))
            if c1 > 0:
                parts.append((r1, c1 - 1, r2, c1 - 1, {"br": None}))
            parts.append((r2 + 1, c1, r2 + 1, c2, {"bt": None}))
            parts.append((r1, c2 + 1, r2, c2 + 1, {"bl": None}))
        if mode == "all":
            parts.append((r1, c1, r2, c2, {"bt": spec, "bb": spec, "bl": spec, "br": spec}))
        if mode in ("outer", "top"):
            parts.append((r1, c1, r1, c2, {"bt": spec}))
        if mode in ("outer", "bottom") and not open_end:
            parts.append((r2, c1, r2, c2, {"bb": spec}))
        if mode in ("outer", "left"):
            parts.append((r1, c1, r2, c1, {"bl": spec}))
        if mode in ("outer", "right"):
            parts.append((r1, c2, r2, c2, {"br": spec}))
        if mode in ("inner", "horizontal") and r2 > r1:
            parts.append((r1, c1, r2 - 1, c2, {"bb": spec}))
        if mode in ("inner", "vertical") and c2 > c1:
            parts.append((r1, c1, r2, c2 - 1, {"br": spec}))
        for pr1, pc1, pr2, pc2, patch in parts:
            if open_end and pr2 >= r2:
                pr2 = sh.nrows + 1000
            _add_style(wb, sh, pr1, pc1, pr2, pc2, patch, merge_same=mode != "none")
    wb._touch(sh)
    return {"ok": True}


# ------------------------------------------------------------------ merges


def _overlap(a: dict, b: dict) -> bool:
    return a["r1"] <= b["r2"] and b["r1"] <= a["r2"] and a["c1"] <= b["c2"] and b["c1"] <= a["c2"]


def merge_cells(
    wb: Workbook, sheet_id: str, rects: list[list[int]], mode: str = "all", dry_run: bool = False
) -> dict:
    """Merge (all / horizontal / vertical) or unmerge.  Only the top-left value of each merge is kept."""
    from .workbook import q

    sh = wb.sheet(sheet_id)
    new: list[dict] = []
    for r1, c1, r2, c2 in rects:
        r2 = min(r2, max(sh.nrows, r1))
        if mode == "all":
            new.append({"r1": r1, "c1": c1, "r2": r2, "c2": c2})
        elif mode == "horizontal":
            new += [{"r1": r, "c1": c1, "r2": r, "c2": c2} for r in range(r1, r2 + 1)]
        elif mode == "vertical":
            new += [{"r1": r1, "c1": c, "r2": r2, "c2": c} for c in range(c1, c2 + 1)]
        elif mode != "unmerge":
            raise EngineError(f"Unknown merge mode {mode}")
    new = [m for m in new if (m["r2"] > m["r1"] or m["c2"] > m["c1"])]
    if sum((m["r2"] - m["r1"] + 1) * (m["c2"] - m["c1"] + 1) for m in new) > 2_000_000:
        raise EngineError("That merge is too large")
    # count values that would be lost
    lost = 0
    for m in new:
        for c in range(m["c1"], min(m["c2"], len(sh.columns) - 1) + 1):
            col = sh.columns[c]
            lo, hi = max(m["r1"], 1) - 1, m["r2"] - 1
            skip = f" AND __rid <> {m['r1'] - 1}" if c == m["c1"] and m["r1"] >= 1 else ""
            if hi >= lo:
                lost += wb.con.execute(
                    f"SELECT count({q(col.id)}) FROM {q(sh.table)} WHERE __rid BETWEEN {lo} AND {hi}{skip}"
                ).fetchone()[0]
            if m["r1"] == 0 and c != m["c1"] and col.name:
                lost += 1
    if dry_run:
        return {"lost": lost}
    lo_all = min((r[0] for r in rects), default=0)
    hi_all = max((r[2] for r in rects), default=0)
    wb._begin(
        "Unmerge" if mode == "unmerge" else "Merge cells", [(sh.id, "rect", lo_all, min(hi_all, sh.nrows))]
    )
    targets = [{"r1": r1, "c1": c1, "r2": r2, "c2": c2} for r1, c1, r2, c2 in rects]
    sh.merges = [m for m in sh.merges if not any(_overlap(m, t) for t in targets)]
    for m in new:
        # clear everything but the anchor
        for c in range(m["c1"], min(m["c2"], len(sh.columns) - 1) + 1):
            col = sh.columns[c]
            lo, hi = max(m["r1"], 1) - 1, m["r2"] - 1
            skip = f" AND __rid <> {m['r1'] - 1}" if c == m["c1"] and m["r1"] >= 1 else ""
            if hi >= lo:
                wb.con.execute(
                    f"UPDATE {q(sh.table)} SET {q(col.id)} = NULL WHERE __rid BETWEEN {lo} AND {hi}{skip}"
                )
            if m["r1"] == 0 and c != m["c1"]:
                col.name = ""
        for f in list(wb.formulas.values()):
            if f.sheet_id != sh.id or f.kind != "cell":
                continue
            fc = sh.col_index(f.col_id)
            if m["r1"] <= f.row <= m["r2"] and m["c1"] <= fc <= m["c2"] and (f.row, fc) != (m["r1"], m["c1"]):
                wb._clear_spill(f)
                del wb.formulas[f.id]
        sh.merges.append(m)
    wb.recalc([(sh.id, t["c1"], t["c2"], t["r1"], t["r2"]) for t in targets])
    wb._touch(sh)
    return {"ok": True, "lost": lost}


def clear_styles(sh: Sheet, r1: int, r2: int, c1: int, c2: int):
    rect = {"r1": r1, "c1": c1, "r2": r2, "c2": c2}
    sh.merges = [m for m in sh.merges if not _overlap(m, rect)]
    keep = []
    for st in sh.styles:
        sr2 = st["r2"] if st["r2"] is not None else 1 << 60
        inside = st["r1"] >= r1 and sr2 <= r2 and st["c1"] >= c1 and st["c2"] <= c2
        if not inside:
            keep.append(st)
    sh.styles = keep
    if r1 <= 1 and r2 >= sh.nrows:
        for c in range(c1, min(c2, len(sh.columns) - 1) + 1):
            sh.columns[c].fmt = None


def styles_in(sh: Sheet, r1: int, r2: int, c1: int, c2: int) -> list[dict]:
    out = []
    for st in sh.styles:
        sr2 = st["r2"] if st["r2"] is not None else 1 << 60
        if st["r1"] <= r2 and sr2 >= r1 and st["c1"] <= c2 and st["c2"] >= c1:
            e = dict(st)
            e["r1"] = max(st["r1"], r1)
            e["r2"] = min(sr2, r2)
            e["c1"] = max(st["c1"], c1)
            e["c2"] = min(st["c2"], c2)
            out.append(e)
    return out


def shift_rules_rows(sh: Sheet, fn):
    out = []
    for st in sh.styles:
        res = fn(st["r1"], st["r2"])
        if res is None:
            continue
        st["r1"], st["r2"] = res
        out.append(st)
    sh.styles = out
    merges = []
    for m in sh.merges:
        res = fn(m["r1"], m["r2"])
        if res is not None and res[1] is not None and (res[1] > res[0] or m["c2"] > m["c1"]):
            m["r1"], m["r2"] = res
            merges.append(m)
    sh.merges = merges
    for rule in sh.cond_formats + sh.validations:
        res = fn(rule.get("r1") or 1, rule.get("r2"))
        if res is not None:
            rule["r1"], rule["r2"] = res


def shift_rules_cols(sh: Sheet, fn, removed: set | None = None):
    out = []
    for st in sh.styles:
        res = fn(st["c1"], st["c2"])
        if res is None:
            continue
        st["c1"], st["c2"] = res
        out.append(st)
    sh.styles = out
    merges = []
    for m in sh.merges:
        res = fn(m["c1"], m["c2"])
        if res is not None and (res[1] > res[0] or m["r2"] > m["r1"]):
            m["c1"], m["c2"] = res
            merges.append(m)
    sh.merges = merges
    if removed:
        for rule in sh.cond_formats + sh.validations:
            rule["cols"] = [c for c in rule.get("cols", []) if c not in removed]


# ------------------------------------------------------- conditional formats


def add_cond_format(wb: Workbook, sheet_id: str, rule: dict) -> dict:
    sh = wb.sheet(sheet_id)
    wb._begin("Conditional formatting", [])
    rule = dict(rule)
    rule.setdefault("id", _rid())
    rule.setdefault("r1", 1)
    rule.setdefault("r2", None)
    rule.setdefault("style", {"bg": "#fde2e1", "fg": "#9f1c1c"})
    existing = [i for i, r in enumerate(sh.cond_formats) if r["id"] == rule["id"]]
    if existing:
        sh.cond_formats[existing[0]] = rule
    else:
        sh.cond_formats.append(rule)
    wb._touch(sh)
    return {"id": rule["id"]}


def delete_cond_format(wb: Workbook, sheet_id: str, rule_id: str) -> dict:
    sh = wb.sheet(sheet_id)
    wb._begin("Remove conditional formatting", [])
    sh.cond_formats = [r for r in sh.cond_formats if r["id"] != rule_id]
    wb._touch(sh)
    return {"ok": True}


def _cached_scalar(wb: Workbook, key: tuple, sql: str):
    if key not in wb._cache:
        wb._cache[key] = wb.con.execute(sql).fetchone()[0]
    return wb._cache[key]


def _dup_table(wb: Workbook, sh: Sheet, cols: list[Column], key: str) -> str:
    name = f"__cfdup_{sh.id}_{re.sub(r'[^A-Za-z0-9]', '_', key)}"
    ck = ("dup", sh.id, key, sh.version)
    if ck not in wb._cache:
        expr = " || '␟' || ".join(f"coalesce(lower({txt(S(q(c.id), c.typ))}), '')" for c in cols)
        nonnull = " OR ".join(f"{q(c.id)} IS NOT NULL" for c in cols)
        wb.con.execute(
            f"CREATE OR REPLACE TEMP TABLE {name} AS SELECT {expr} AS k FROM {q(sh.table)} "
            f"WHERE {nonnull} GROUP BY 1 HAVING count(*) > 1"
        )
        wb._cache[ck] = True
    return name


def cond_format_expr(wb: Workbook, sh: Sheet, rule: dict, col: Column) -> str | None:
    kind = rule.get("kind")
    x = f"t.{q(col.id)}"
    xs = S(x, col.typ)
    try:
        if kind in (
            "gt",
            "gte",
            "lt",
            "lte",
            "eq",
            "neq",
            "between",
            "not_between",
            "contains",
            "not_contains",
            "starts",
            "ends",
            "exact",
            "empty",
            "not_empty",
            "regex",
        ):
            return _compare_expr(col, x, kind, rule.get("value"), rule.get("value2"))
        if kind in ("duplicate", "unique"):
            tbl = _dup_table(wb, sh, [col], col.id)
            e = f"(coalesce(lower({txt(xs)}), '') IN (SELECT k FROM {tbl}))"
            return f"({x} IS NOT NULL AND {e})" if kind == "duplicate" else f"({x} IS NOT NULL AND NOT {e})"
        if kind == "duplicate_row":
            cols = [sh.col_by_id(c) for c in rule.get("key_cols", rule.get("cols", []))]
            tbl = _dup_table(wb, sh, cols, "row_" + "_".join(c.id for c in cols))
            key = " || '␟' || ".join(f"coalesce(lower({txt(S('t.' + q(c.id), c.typ))}), '')" for c in cols)
            return f"({key} IN (SELECT k FROM {tbl}))"
        if kind in ("top", "bottom"):
            n = int(rule.get("value") or 10)
            order = "DESC" if kind == "top" else "ASC"
            thr = _cached_scalar(
                wb,
                ("thr", sh.id, sh.version, col.id, kind, n),
                f"SELECT v FROM (SELECT {num_nullable(S(q(col.id), col.typ))} AS v FROM {q(sh.table)}) "
                f"WHERE v IS NOT NULL ORDER BY v {order} LIMIT 1 OFFSET {max(n - 1, 0)}",
            )
            if thr is None:
                return f"({num_nullable(xs)} IS NOT NULL)"
            o = ">=" if kind == "top" else "<="
            return f"({num_nullable(xs)} {o} {float(thr)!r})"
        if kind in ("above_avg", "below_avg"):
            avg = _cached_scalar(
                wb,
                ("avg", sh.id, sh.version, col.id),
                f"SELECT avg({num_nullable(S(q(col.id), col.typ))}) FROM {q(sh.table)}",
            )
            if avg is None:
                return None
            o = ">" if kind == "above_avg" else "<"
            return f"({num_nullable(xs)} {o} {float(avg)!r})"
        if kind == "color_scale":
            mn, mx = _cached_scalar(
                wb,
                ("mm", sh.id, sh.version, col.id),
                f"SELECT [min(v), max(v)] FROM (SELECT {num_nullable(S(q(col.id), col.typ))} AS v FROM {q(sh.table)})",
            )
            if mn is None or mx is None:
                return None
            span = (mx - mn) or 1.0
            return f"round(({num_nullable(xs)} - {float(mn)!r}) / {float(span)!r}, 3)"
        if kind == "formula":
            text = rule.get("formula") or ""
            first = rule.get("cols", [col.id])[0]
            dc = sh.col_index(col.id) - sh.col_index(first)
            text = P.shift_formula(text, 0, dc)
            comp = wb._compiler(sh, rule.get("r1") or 1, sh.col_index(col.id), "row")
            v = comp.compile(P.parse_formula(text))
            if isinstance(v, A):
                v = comp.to_scalar(v)
            return boo(v)
    except (FormulaError, P.FormulaSyntaxError, ValueError, TypeError, KeyError, duckdb.Error):
        return None
    return None


# ------------------------------------------------------------- validation


def validation_expr(wb: Workbook, sh: Sheet, rule: dict, col: Column, x: S | None = None) -> str | None:
    x = x or S(f"t.{q(col.id)}", col.typ)
    kind = rule.get("kind")
    try:
        if kind == "list":
            if rule.get("source"):
                ref_text = rule["source"].lstrip("=")
                comp = wb._compiler(sh, 1, 0, "cell")
                arr = comp.compile(P.parse_formula("=" + ref_text))
                if not isinstance(arr, A):
                    return None
                arr = comp.to_relation(arr)
                b = arr.base
                where = " AND ".join(b.where) or "TRUE"
                return f"(lower({txt(x)}) IN (SELECT lower({txt(arr.cols[0])}) FROM {b.frm} WHERE {where}))"
            vals = [str(v) for v in rule.get("values") or []]
            if not vals:
                return None
            return f"(lower({txt(x)}) IN ({', '.join(sql_str(v.lower()) for v in vals)}))"
        if kind == "number":
            op = rule.get("op", "between")
            e = _compare_expr(
                Column(id="x", duck="DOUBLE"), "__X__", op, rule.get("value"), rule.get("value2")
            )
            return (
                None
                if e is None
                else f"({num_nullable(x)} IS NOT NULL AND {e.replace('__X__', num_nullable(x))})"
            )
        if kind == "text_length":
            op = rule.get("op", "between")
            ln = f"CAST(length({txt(x)}) AS DOUBLE)"
            e = _compare_expr(
                Column(id="x", duck="DOUBLE"), "__X__", op, rule.get("value"), rule.get("value2")
            )
            return None if e is None else e.replace("__X__", ln)
        if kind == "date":
            op = rule.get("op", "between")
            d = as_date(x)
            e = _compare_expr(Column(id="x", duck="DATE"), "__X__", op, rule.get("value"), rule.get("value2"))
            return None if e is None else f"({d} IS NOT NULL AND {e.replace('__X__', d)})"
        if kind == "text_contains":
            return (
                f"({txt(x)} ILIKE {sql_str('%' + _esc_like(str(rule.get('value', ''))) + '%')} ESCAPE '\\')"
            )
        if kind == "text_not_contains":
            return f"(NOT ({txt(x)} ILIKE {sql_str('%' + _esc_like(str(rule.get('value', ''))) + '%')} ESCAPE '\\'))"
        if kind == "text_is":
            return f"(lower({txt(x)}) = lower({sql_str(str(rule.get('value', '')))}))"
        if kind == "email":
            return f"regexp_full_match({txt(x)}, '[^@\\s]+@[^@\\s]+\\.[^@\\s]+')"
        if kind == "url":
            return f"regexp_full_match({txt(x)}, '(?i)(https?://)?[\\w.-]+\\.[a-z]{{2,}}(/\\S*)?')"
        if kind == "regex":
            return f"regexp_full_match({txt(x)}, {sql_str(str(rule.get('pattern') or rule.get('value') or '.*'))})"
        if kind == "checkbox":
            if x.typ == BOOL:
                return "TRUE"
            return f"(upper({txt(x)}) IN ('TRUE', 'FALSE'))"
        if kind == "unique":
            if not x.sql.startswith("t."):
                cnt = wb.con.execute(
                    f"SELECT count(*) FROM {q(sh.table)} WHERE lower({txt(S(q(col.id), col.typ))}) = "
                    f"lower({txt(x)})"
                ).fetchone()[0]
                return "TRUE" if cnt == 0 else "FALSE"
            tbl = _dup_table(wb, sh, [col], col.id)
            return f"(coalesce(lower({txt(x)}), '') NOT IN (SELECT k FROM {tbl}))"
        if kind == "formula":
            comp = wb._compiler(sh, rule.get("r1") or 1, sh.col_index(col.id), "row")
            v = comp.compile(P.parse_formula(rule.get("formula") or "=TRUE"))
            if isinstance(v, A):
                v = comp.to_scalar(v)
            return boo(v)
    except (FormulaError, P.FormulaSyntaxError, ValueError, TypeError, KeyError, duckdb.Error):
        return None
    return None


def add_validation(wb: Workbook, sheet_id: str, rule: dict) -> dict:
    sh = wb.sheet(sheet_id)
    wb._begin("Data validation", [])
    rule = dict(rule)
    rule.setdefault("id", _rid())
    rule.setdefault("r1", 1)
    rule.setdefault("r2", None)
    existing = [i for i, r in enumerate(sh.validations) if r["id"] == rule["id"]]
    if existing:
        sh.validations[existing[0]] = rule
    else:
        sh.validations.append(rule)
    if rule.get("kind") == "checkbox":
        for cid in rule.get("cols", []):
            c = sh.col_by_id(cid)
            nonbool = wb.con.execute(
                f"SELECT count(*) FROM {q(sh.table)} WHERE {q(c.id)} IS NOT NULL AND "
                f"TRY_CAST({txt(S(q(c.id), c.typ))} AS BOOLEAN) IS NULL"
            ).fetchone()[0]
            if nonbool == 0 and c.duck != "BOOLEAN":
                wb._retype(sh, c, "BOOLEAN")
    wb._touch(sh)
    return {"id": rule["id"]}


def delete_validation(wb: Workbook, sheet_id: str, rule_id: str) -> dict:
    sh = wb.sheet(sheet_id)
    wb._begin("Remove validation", [])
    sh.validations = [r for r in sh.validations if r["id"] != rule_id]
    wb._touch(sh)
    return {"ok": True}


def validation_report(wb: Workbook, sheet_id: str, rule_id: str | None = None) -> dict:
    """Count invalid cells per rule and locate the first one."""
    sh = wb.sheet(sheet_id)
    out = []
    for rule in sh.validations:
        if rule_id and rule["id"] != rule_id:
            continue
        total = 0
        first = None
        for cid in rule.get("cols", []):
            try:
                col = sh.col_by_id(cid)
            except KeyError:
                continue
            e = validation_expr(wb, sh, rule, col)
            if e is None:
                continue
            cond = wb._row_bound(rule, f"(t.{q(col.id)} IS NOT NULL AND NOT coalesce({e}, FALSE))")
            n, mn = wb.con.execute(
                f"SELECT count(*), min(t.__rid) FROM {q(sh.table)} t WHERE {cond}"
            ).fetchone()
            total += n
            if mn is not None and (first is None or mn + 1 < first[0]):
                first = (mn + 1, sh.col_index(cid))
        out.append({"id": rule["id"], "invalid": total, "first": first})
    return {"rules": out}


def validation_options(wb: Workbook, sheet_id: str, rule_id: str, limit: int = 1000) -> dict:
    sh = wb.sheet(sheet_id)
    rule = next((r for r in sh.validations if r["id"] == rule_id), None)
    if rule is None:
        return {"values": []}
    if rule.get("source"):
        comp = wb._compiler(sh, 1, 0, "cell")
        try:
            arr = comp.compile(P.parse_formula("=" + rule["source"].lstrip("=")))
        except (FormulaError, P.FormulaSyntaxError) as e:
            raise EngineError(f"Invalid list source: {e}") from e
        if not isinstance(arr, A):
            return {"values": []}
        arr = comp.to_relation(arr)
        b = arr.base
        where = " AND ".join(b.where) or "TRUE"
        rows = wb.con.execute(
            f"SELECT DISTINCT {txt(arr.cols[0])} AS v FROM {b.frm} WHERE {where} AND "
            f"{arr.cols[0].sql} IS NOT NULL ORDER BY 1 LIMIT {limit}"
        ).fetchall()
        return {"values": [r[0] for r in rows]}
    return {"values": [str(v) for v in rule.get("values") or []]}


def check_value_against_rules(wb: Workbook, sh: Sheet, col: int, value, row: int | None = None) -> str | None:
    if value is None or col >= len(sh.columns):
        return None
    c = sh.columns[col]
    for rule in sh.validations:
        if not rule.get("strict") or c.id not in rule.get("cols", []):
            continue
        if row is not None and (
            row < (rule.get("r1") or 1) or (rule.get("r2") is not None and row > rule["r2"])
        ):
            continue
        lit = duck_literal(value)
        typ = (
            NUM
            if isinstance(value, (int, float)) and not isinstance(value, bool)
            else BOOL
            if isinstance(value, bool)
            else DATE
            if type(value).__name__ == "date"
            else TS
            if type(value).__name__ == "datetime"
            else STR
        )
        e = validation_expr(wb, sh, rule, c, S(lit, typ))
        if e is None:
            continue
        ok = wb.con.execute(f"SELECT coalesce({e}, FALSE)").fetchone()[0]
        if not ok:
            return (
                rule.get("message")
                or "The value you entered violates the data validation rule for this cell."
            )
    return None


# --------------------------------------------------------------- find/replace


def _match_expr(x: S, query: str, match_case: bool, whole: bool, regex: bool) -> str:
    t = txt(x)
    if regex:
        flags = "" if match_case else "(?i)"
        pat = f"^(?:{query})$" if whole else query
        return f"coalesce(regexp_matches({t}, {sql_str(flags + pat)}), FALSE)"
    if whole:
        return f"({t} = {sql_str(query)})" if match_case else f"(lower({t}) = lower({sql_str(query)}))"
    if match_case:
        return f"(strpos({t}, {sql_str(query)}) > 0)"
    return f"({t} ILIKE {sql_str('%' + _esc_like(query) + '%')} ESCAPE '\\')"


def _py_match(s: str | None, query: str, match_case: bool, whole: bool, regex: bool) -> bool:
    if not s:
        return False
    if regex:
        flags = 0 if match_case else re.I
        return bool(re.fullmatch(query, s, flags) if whole else re.search(query, s, flags))
    if not match_case:
        s, query = s.lower(), query.lower()
    return s == query if whole else query in s


def find(
    wb: Workbook,
    sheet_id: str,
    query: str,
    match_case: bool = False,
    whole: bool = False,
    regex: bool = False,
    row: int = 0,
    col: int = -1,
    backwards: bool = False,
    all_sheets: bool = False,
) -> dict:
    if not query:
        return {"found": False, "count": 0}
    sheets = wb.sheets if all_sheets else [wb.sheet(sheet_id)]
    start_idx = next(i for i, s in enumerate(sheets) if s.id == sheet_id) if all_sheets else 0
    total = 0
    per_sheet = []
    for sh in sheets:
        if not sh.columns:
            per_sheet.append(0)
            continue
        n = _find_count(wb, sh, query, match_case, whole, regex)
        per_sheet.append(n)
        total += n
    if total == 0:
        return {"found": False, "count": 0}
    order = list(range(start_idx, len(sheets))) + list(range(0, start_idx)) + [start_idx]
    if backwards:
        order = list(range(start_idx, -1, -1)) + list(range(len(sheets) - 1, start_idx - 1, -1))
    first_pass = True
    for si in order:
        sh = sheets[si]
        if per_sheet[si] == 0:
            first_pass = False
            continue
        r0, c0 = (
            (row, col)
            if (first_pass and sh.id == sheet_id)
            else ((1 << 60, 1 << 30) if backwards else (0, -1))
        )
        hit = _find_in_sheet(wb, sh, query, match_case, whole, regex, r0, c0, backwards)
        if hit is None and first_pass and sh.id == sheet_id and not all_sheets:
            hit = _find_in_sheet(
                wb,
                sh,
                query,
                match_case,
                whole,
                regex,
                (1 << 60) if backwards else 0,
                (1 << 30) if backwards else -1,
                backwards,
            )
        first_pass = False
        if hit is not None:
            r, c = hit
            vrow = r
            if sh.view_table and r >= 1:
                pos = wb.con.execute(f"SELECT pos FROM {q(sh.view_table)} WHERE rid = ?", [r - 1]).fetchone()
                vrow = (pos[0] + 1) if pos else None
            return {"found": True, "sheet": sh.id, "row": r, "col": c, "vrow": vrow, "count": total}
    return {"found": False, "count": total}


def _find_count(wb, sh, query, match_case, whole, regex) -> int:
    """Number of matching cells (and headers) in a sheet.  The full scan is the slow part of
    Find on big sheets, so it is cached until the sheet changes: pressing Enter again to jump
    to the next match only has to locate that match."""
    key = ("find_count", sh.id, sh.version, len(sh.columns), query, match_case, whole, regex)
    if key not in wb._cache:
        conds = [_match_expr(S(f"t.{q(c.id)}", c.typ), query, match_case, whole, regex) for c in sh.columns]
        cnt_sql = " + ".join(f"coalesce(CAST({c} AS INTEGER), 0)" for c in conds)
        n = wb.con.execute(f"SELECT coalesce(sum({cnt_sql}), 0) FROM {q(sh.table)} t").fetchone()[0]
        n += sum(1 for c in sh.columns if _py_match(c.name, query, match_case, whole, regex))
        wb._cache[key] = int(n)
    return wb._cache[key]


_FIND_CHUNK = 1 << 16  # first window of rows scanned for the next match; doubles each step


def _rid_windows(start: int, end: int, backwards: bool):
    """Row-id windows (lo, hi) moving away from ``start``.  Scanning a small window first means
    a nearby match is found without reading the whole table; the last window is open-ended."""
    size = _FIND_CHUNK
    if not backwards:
        lo = start
        while lo <= end:
            yield lo, lo + size - 1
            lo += size
            size = min(size * 2, 1 << 22)
        yield lo, None
    else:
        hi = start
        while hi >= 0:
            yield max(hi - size + 1, 0), hi
            hi -= size
            size = min(size * 2, 1 << 22)


def _find_in_sheet(wb, sh, query, match_case, whole, regex, row, col, backwards):
    cols = sh.columns
    conds = [_match_expr(S(f"t.{q(c.id)}", c.typ), query, match_case, whole, regex) for c in cols]
    header_hits = [j for j, c in enumerate(cols) if _py_match(c.name, query, match_case, whole, regex)]
    lst = ", ".join(conds)
    anyc = " OR ".join(conds)
    if not backwards:
        if row == 0:
            for j in header_hits:
                if j > col:
                    return 0, j
        rid = max(row, 1) - 1
        for lo, hi in _rid_windows(rid, sh.nrows, False):
            bound = f" AND __rid <= {hi}" if hi is not None else ""
            rows = wb.con.execute(
                f"SELECT __rid, [{lst}] FROM {q(sh.table)} t WHERE __rid >= {lo}{bound} AND ({anyc}) "
                f"ORDER BY __rid LIMIT 2"
            ).fetchall()
            for r, flags in rows:
                for j, fl in enumerate(flags):
                    if fl and (r + 1 > row or j > col):
                        return r + 1, j
        return None
    rid = row - 1
    if rid >= 0:
        # the first window keeps ``rid`` as its upper bound, so rows past nrows are never skipped
        for i, (lo, hi) in enumerate(_rid_windows(min(rid, sh.nrows), sh.nrows, True)):
            rows = wb.con.execute(
                f"SELECT __rid, [{lst}] FROM {q(sh.table)} t WHERE __rid >= {lo} "
                f"AND __rid <= {rid if i == 0 else hi} AND ({anyc}) ORDER BY __rid DESC LIMIT 2"
            ).fetchall()
            for r, flags in rows:
                for j in range(len(flags) - 1, -1, -1):
                    if flags[j] and (r + 1 < row or j < col):
                        return r + 1, j
    for j in reversed(header_hits):
        if row > 0 or j < col:
            return 0, j
    return None


def replace_all(
    wb: Workbook,
    sheet_id: str,
    query: str,
    replacement: str,
    match_case: bool = False,
    whole: bool = False,
    regex: bool = False,
    all_sheets: bool = False,
) -> dict:
    if not query:
        return {"replaced": 0}
    sheets = wb.sheets if all_sheets else [wb.sheet(sheet_id)]
    wb._begin("Replace all", [(s.id, "table") for s in sheets])
    total = 0
    for sh in sheets:
        changed_cols = []
        for j, c in enumerate(sh.columns):
            if _py_match(c.name, query, match_case, whole, regex):
                c.name = _py_replace(c.name, query, replacement, match_case, whole, regex)
                total += 1
            x = S(q(c.id), c.typ)
            cond = _match_expr(x, query, match_case, whole, regex)
            n = wb.con.execute(
                f"SELECT count(*) FROM {q(sh.table)} t WHERE {cond.replace('t.', '')}"
            ).fetchone()[0]
            if not n:
                continue
            total += n
            if c.typ != STR:
                wb._retype(sh, c, "VARCHAR")
                x = S(q(c.id), STR)
            if regex:
                flags = "g" if match_case else "gi"
                pat = f"^(?:{query})$" if whole else query
                rep = re.sub(r"\$(\d)", r"\\\1", replacement)
                expr = f"regexp_replace({x.sql}, {sql_str(pat)}, {sql_str(rep)}, '{flags}')"
            elif whole:
                expr = sql_str(replacement)
            elif match_case:
                expr = f"replace({x.sql}, {sql_str(query)}, {sql_str(replacement)})"
            else:
                expr = f"regexp_replace({x.sql}, {sql_str('(?i)' + re.escape(query))}, {sql_str(replacement.replace(chr(92), chr(92) * 2))}, 'g')"
            wb.con.execute(f"UPDATE {q(sh.table)} SET {q(c.id)} = {expr} WHERE {cond.replace('t.', '')}")
            changed_cols.append(j)
            _maybe_numeric(wb, sh, c)
        if changed_cols:
            wb.recalc([(sh.id, min(changed_cols), max(changed_cols), 0, None)])
        wb._touch(sh)
    return {"replaced": int(total)}


def _maybe_numeric(wb: Workbook, sh: Sheet, c: Column):
    """After text edits, turn a text column back into numbers when every value is numeric."""
    if c.typ != STR:
        return
    bad = wb.con.execute(
        f"SELECT count(*) FROM {q(sh.table)} WHERE {q(c.id)} IS NOT NULL AND "
        f"TRY_CAST({q(c.id)} AS DOUBLE) IS NULL"
    ).fetchone()[0]
    nonnull = wb.con.execute(f"SELECT count({q(c.id)}) FROM {q(sh.table)}").fetchone()[0]
    if bad == 0 and nonnull > 0:
        wb._retype(sh, c, "DOUBLE")


def _py_replace(s, query, replacement, match_case, whole, regex):
    if regex:
        flags = 0 if match_case else re.I
        return re.sub(f"^(?:{query})$" if whole else query, replacement, s, flags=flags)
    if whole:
        return replacement
    if match_case:
        return s.replace(query, replacement)
    return re.sub(re.escape(query), lambda _m: replacement, s, flags=re.I)


# ------------------------------------------------------------ selection stats


def selection_stats(wb: Workbook, sheet_id: str, rects: list[list[int]]) -> dict:
    sh = wb.sheet(sheet_id)
    total = {"sum": 0.0, "count": 0, "counta": 0, "min": None, "max": None, "numeric": 0}
    for r1, c1, r2, c2 in rects:
        c2 = min(c2, len(sh.columns) - 1)
        if c2 < c1:
            continue
        lo, hi = max(r1, 1) - 1, min(r2 - 1, sh.nrows - 1)
        if r1 == 0:
            total["counta"] += sum(1 for c in sh.columns[c1 : c2 + 1] if c.name)
        if hi < lo:
            continue
        parts = []
        for c in sh.columns[c1 : c2 + 1]:
            n = num_nullable(S(q(c.id), c.typ)) if c.typ in (NUM,) else "CAST(NULL AS DOUBLE)"
            parts.append(f"sum({n}), count({n}), count({q(c.id)}), min({n}), max({n})")
        vis = f" AND __rid IN (SELECT rid FROM {q(sh.view_table)})" if sh.view_table else ""
        row = wb.con.execute(
            f"SELECT {', '.join(parts)} FROM {q(sh.table)} WHERE __rid BETWEEN {lo} AND {hi}{vis}"
        ).fetchone()
        for k in range(0, len(row), 5):
            s, cnt, cnta, mn, mx = row[k : k + 5]
            total["sum"] += s or 0
            total["count"] += cnt or 0
            total["counta"] += cnta or 0
            if mn is not None:
                total["min"] = mn if total["min"] is None else min(total["min"], mn)
            if mx is not None:
                total["max"] = mx if total["max"] is None else max(total["max"], mx)
    total["avg"] = (total["sum"] / total["count"]) if total["count"] else None
    return {k: to_json(v) for k, v in total.items()}


# ---------------------------------------------------------------- duplicates


def duplicates(
    wb: Workbook,
    sheet_id: str,
    cols: list[int],
    action: str = "count",
    ignore_case: bool = True,
    trim: bool = True,
) -> dict:
    sh = wb.sheet(sheet_id)
    if not cols:
        raise EngineError("Choose at least one column")
    columns = [sh.columns[c] for c in cols]

    def key_of(c: Column) -> str:
        e = txt(S(q(c.id), c.typ))
        if trim:
            e = f"trim({e})"
        if ignore_case:
            e = f"lower({e})"
        return f"coalesce({e}, '')"

    key = " || '␟' || ".join(key_of(c) for c in columns)
    nonblank = " OR ".join(f"{q(c.id)} IS NOT NULL" for c in columns)
    stats = wb.con.execute(
        f"SELECT count(*), count(DISTINCT k), count(*) FILTER (WHERE n > 1), "
        f"count(DISTINCT k) FILTER (WHERE n > 1) FROM (SELECT {key} AS k, count(*) OVER (PARTITION BY {key}) AS n "
        f"FROM {q(sh.table)} WHERE {nonblank})"
    ).fetchone()
    rows, distinct, in_dup_groups, groups = stats
    info = {
        "rows": rows,
        "unique": distinct,
        "duplicates": rows - distinct,
        "rows_in_groups": in_dup_groups,
        "groups": groups,
    }
    if action == "count":
        examples = wb.con.execute(
            f"SELECT k, n FROM (SELECT {key} AS k, count(*) AS n FROM {q(sh.table)} WHERE {nonblank} GROUP BY 1) "
            f"WHERE n > 1 ORDER BY n DESC LIMIT 20"
        ).fetchall()
        info["top"] = [[k.replace("␟", " | "), n] for k, n in examples]
        return info
    if action == "highlight":
        add_cond_format(
            wb,
            sh.id,
            {
                "kind": "duplicate_row" if len(columns) > 1 else "duplicate",
                "cols": [c.id for c in columns],
                "key_cols": [c.id for c in columns],
                "style": {"bg": "#fff1c2", "fg": "#7a4b00"},
                "name": "Duplicates",
            },
        )
        return info
    if action == "mark":
        wb._begin("Mark duplicates", [(sh.id, "table")])
        newc = Column(id=sh.new_col_id(), name="Is duplicate", duck="BOOLEAN", width=110)
        wb.con.execute(f"ALTER TABLE {q(sh.table)} ADD COLUMN {q(newc.id)} BOOLEAN")
        wb.con.execute(
            f"UPDATE {q(sh.table)} AS t SET {q(newc.id)} = s.d FROM (SELECT __rid, "
            f"row_number() OVER (PARTITION BY {key} ORDER BY __rid) > 1 AS d FROM {q(sh.table)} "
            f"WHERE {nonblank}) s WHERE t.__rid = s.__rid"
        )
        sh.columns.append(newc)
        wb._touch(sh)
        return info
    if action == "remove":
        wb._begin("Remove duplicates", [(sh.id, "table")])
        for f in [f for f in wb.formulas.values() if f.sheet_id == sh.id and f.kind == "cell"]:
            wb._clear_spill(f)
        mp = f"__dmap_{sh.id}"
        wb.con.execute(
            f"CREATE OR REPLACE TEMP TABLE {mp} AS SELECT __rid AS old, row_number() OVER (ORDER BY __rid) - 1 AS new "
            f"FROM {q(sh.table)} QUALIFY ({nonblank.replace(' OR ', ' OR ')}) IS NOT TRUE OR "
            f"row_number() OVER (PARTITION BY {key} ORDER BY __rid) = 1"
        )
        wb.con.execute(
            f"CREATE OR REPLACE TABLE {q(sh.table)} AS SELECT m.new AS __rid, t.* EXCLUDE (__rid) FROM "
            f"{q(sh.table)} t JOIN {mp} m ON m.old = t.__rid ORDER BY m.new"
        )
        mapping = dict(wb.con.execute(f"SELECT old, new FROM {mp}").fetchall()) if len(wb.formulas) else {}
        for f in list(wb.formulas.values()):
            if f.sheet_id == sh.id and f.kind == "cell" and f.row >= 1:
                if f.row - 1 in mapping:
                    f.row = mapping[f.row - 1] + 1
                else:
                    del wb.formulas[f.id]
        wb.con.execute(f"DROP TABLE IF EXISTS {mp}")
        removed = sh.nrows - wb.con.execute(f"SELECT count(*) FROM {q(sh.table)}").fetchone()[0]
        sh.nrows -= removed
        wb._apply_filters(sh)
        wb.recalc(None)
        wb._touch(sh)
        info["removed"] = removed
        return info
    raise EngineError(f"Unknown action {action}")


# -------------------------------------------------------------- data cleanup


def transform_text(wb: Workbook, sheet_id: str, rects: list[list[int]], op: str) -> dict:
    """Trim whitespace / change case / strip non-printing characters in place."""
    sh = wb.sheet(sheet_id)
    exprs = {
        "trim": lambda x: f"trim(regexp_replace({x}, '\\s+', ' ', 'g'))",
        "upper": lambda x: f"upper({x})",
        "lower": lambda x: f"lower({x})",
        "proper": lambda x: (
            f"array_to_string(list_transform(string_split(lower({x}), ' '), "
            f"lambda w: upper(w[1]) || w[2:]), ' ')"
        ),
        "clean": lambda x: f"regexp_replace({x}, '[\\x00-\\x1F]', '', 'g')",
    }
    if op not in exprs:
        raise EngineError(f"Unknown cleanup {op}")
    scope = [(sh.id, "table")]
    wb._begin("Clean up text", scope)
    changed = 0
    regions = []
    for r1, c1, r2, c2 in rects:
        lo, hi = max(r1, 1) - 1, min(r2, sh.nrows) - 1
        for c in sh.columns[c1 : min(c2, len(sh.columns) - 1) + 1]:
            if r1 == 0 and c.name:
                c.name = {
                    "trim": lambda s: " ".join(s.split()),
                    "upper": str.upper,
                    "lower": str.lower,
                    "proper": str.title,
                    "clean": lambda s: re.sub(r"[\x00-\x1f]", "", s),
                }[op](c.name)
            if c.typ != STR or hi < lo:
                continue
            e = exprs[op](q(c.id))
            n = wb.con.execute(
                f"SELECT count(*) FROM {q(sh.table)} WHERE __rid BETWEEN {lo} AND {hi} AND "
                f"{q(c.id)} IS DISTINCT FROM {e}"
            ).fetchone()[0]
            if n:
                wb.con.execute(f"UPDATE {q(sh.table)} SET {q(c.id)} = {e} WHERE __rid BETWEEN {lo} AND {hi}")
                changed += n
        regions.append((sh.id, c1, c2, r1, r2))
    wb.recalc(regions)
    wb._touch(sh)
    return {"changed": changed}


def split_column(wb: Workbook, sheet_id: str, col: int, delimiter: str) -> dict:
    """Split text to columns (inserted to the right of ``col``)."""
    sh = wb.sheet(sheet_id)
    c = sh.columns[col]
    wb._begin("Split text to columns", [(sh.id, "table")])
    pat = sql_str(re.escape(delimiter) if delimiter else "\\s+")
    parts = f"regexp_split_to_array({txt(S(q(c.id), c.typ))}, {pat})"
    width = wb.con.execute(f"SELECT max(len({parts})) FROM {q(sh.table)}").fetchone()[0] or 1
    width = min(int(width), 200)
    new_cols = []
    for k in range(width):
        nc = Column(id=sh.new_col_id(), name=f"{c.name or 'Part'} {k + 1}", duck="VARCHAR", width=110)
        wb.con.execute(f"ALTER TABLE {q(sh.table)} ADD COLUMN {q(nc.id)} VARCHAR")
        wb.con.execute(f"UPDATE {q(sh.table)} SET {q(nc.id)} = NULLIF(trim(({parts})[{k + 1}]), '')")
        new_cols.append(nc)
    sh.columns[col + 1 : col + 1] = new_cols
    for nc in new_cols:
        _maybe_numeric(wb, sh, nc)
    wb._touch(sh)
    return {"columns": width}


# ------------------------------------------------------------------- pivots

_AGG_LABEL = {
    "sum": "SUM",
    "count": "COUNT",
    "counta": "COUNTA",
    "avg": "AVERAGE",
    "min": "MIN",
    "max": "MAX",
    "countd": "COUNTUNIQUE",
    "median": "MEDIAN",
    "stdev": "STDEV",
    "var": "VAR",
}


def _agg_sql(agg: str, col: Column, alias: str = "t") -> str:
    x = f"{alias}.{q(col.id)}"
    n = num_nullable(S(x, col.typ))
    return {
        "sum": f"sum({n})",
        "avg": f"avg({n})",
        "min": f"min({n})",
        "max": f"max({n})",
        "median": f"median({n})",
        "stdev": f"stddev_samp({n})",
        "var": f"var_samp({n})",
        "count": f"count({x})",
        "counta": "count(*)",
        "countd": f"count(DISTINCT {x})",
    }[agg]


def _pivot_sql(wb: Workbook, spec: dict) -> tuple[str, list[str], int]:
    src = wb.sheet(spec["source"])
    rows = [src.col_by_id(c) for c in spec.get("rows", [])]
    colf = [src.col_by_id(c) for c in spec.get("cols", [])]
    vals = [(src.col_by_id(v["col"]), v.get("agg", "sum")) for v in spec.get("values", [])]
    if not vals:
        vals = [(rows[0] if rows else src.columns[0], "counta")]
    conds = []
    for cid, fspec in (spec.get("filters") or {}).items():
        try:
            e = filter_condition(src.col_by_id(cid), fspec)
        except KeyError:
            continue
        if e:
            conds.append(e)
    where = " AND ".join(conds) or "TRUE"
    totals = spec.get("totals", True)
    labels: list[str] = []
    select = []
    for k, rc in enumerate(rows):
        expr = f"t.{q(rc.id)}"
        if k == 0 and totals:
            expr = (
                f"CASE WHEN grouping(t.{q(rc.id)}) = 1 THEN 'Grand Total' ELSE "
                f"{txt(S(f't.{q(rc.id)}', rc.typ))} END"
            )
        select.append(f"{expr} AS r{k}")
        labels.append(rc.name or f"Row {k + 1}")
    value_exprs = []
    if colf:
        key = " || ' · ' || ".join(f"coalesce({txt(S(f't.{q(c.id)}', c.typ))}, '(blank)')" for c in colf)
        combos = [
            r[0]
            for r in wb.con.execute(
                f"SELECT DISTINCT {key} AS k FROM {q(src.table)} t WHERE {where} ORDER BY 1 LIMIT 300"
            ).fetchall()
        ]
        for combo in combos:
            for vc, agg in vals:
                value_exprs.append(
                    (
                        f"{_agg_sql(agg, vc)} FILTER (WHERE {key} = {sql_str(combo)})",
                        combo if len(vals) == 1 else f"{combo} · {_AGG_LABEL[agg]} of {vc.name}",
                    )
                )
        if totals:
            for vc, agg in vals:
                value_exprs.append(
                    (
                        _agg_sql(agg, vc),
                        "Grand Total" if len(vals) == 1 else f"Total {_AGG_LABEL[agg]} of {vc.name}",
                    )
                )
    else:
        for vc, agg in vals:
            value_exprs.append((_agg_sql(agg, vc), f"{_AGG_LABEL[agg]} of {vc.name or 'value'}"))
    for k, (e, lab) in enumerate(value_exprs):
        select.append(f"{e} AS v{k}")
        labels.append(lab)
    group = ", ".join(f"t.{q(rc.id)}" for rc in rows)
    sql = f"SELECT {', '.join(select)} FROM {q(src.table)} t WHERE {where}"
    if rows:
        if totals:
            sql += f" GROUP BY GROUPING SETS (({group}), ())"
            order = [f"grouping(t.{q(rows[0].id)})"] + [
                f"{'lower(' + txt(S(f't.{q(r.id)}', r.typ)) + ')' if r.typ == STR else f't.{q(r.id)}'} "
                f"{'DESC' if spec.get('desc') else 'ASC'} NULLS LAST"
                for r in rows
            ]
        else:
            sql += f" GROUP BY {group}"
            order = [f"t.{q(r.id)} {'DESC' if spec.get('desc') else 'ASC'} NULLS LAST" for r in rows]
        if spec.get("sort_by_value"):
            order = ([f"grouping(t.{q(rows[0].id)})"] if totals else []) + [
                f"v0 {'DESC' if spec.get('desc') else 'ASC'}"
            ]
        sql += " ORDER BY " + ", ".join(order)
    return sql, labels, len(rows)


def create_pivot(wb: Workbook, spec: dict, sheet_id: str | None = None) -> dict:
    src = wb.sheet(spec["source"])
    sql, labels, nrow_fields = _pivot_sql(wb, spec)
    if sheet_id is None:
        wb._begin("Create pivot table", [])
        sh = wb.add_sheet(
            f"Pivot of {src.name}", ncols=0, nrows=0, index=wb.sheets.index(src) + 1, record=False
        )
    else:
        sh = wb.sheet(sheet_id)
        wb._begin("Edit pivot table", [(sh.id, "table")])
    _fill_pivot(wb, sh, spec, sql, labels, nrow_fields)
    wb._touch(sh)
    return {"sheet": sh.id}


def _fill_pivot(wb: Workbook, sh: Sheet, spec: dict, sql: str, labels: list[str], nrow_fields: int):
    src = wb.sheet(spec["source"])
    raw = f"__pv_{sh.id}"
    wb.con.execute(f"CREATE OR REPLACE TABLE {q(raw)} AS {sql}")
    desc = wb.con.execute(f"DESCRIBE {q(raw)}").fetchall()
    from .workbook import norm_duck

    old_widths = {c.name: c.width for c in sh.columns}
    sh.columns = []
    sh.next_col = 0
    sel = []
    for (name, ctype, *_), lab in zip(desc, labels, strict=True):
        c = Column(id=sh.new_col_id(), name=lab, duck=norm_duck(str(ctype)))
        sel.append(f"CAST({q(name)} AS {c.duck}) AS {q(c.id)}")
        sh.columns.append(c)
    wb.con.execute(
        f"CREATE OR REPLACE TABLE {q(sh.table)} AS SELECT rowid AS __rid, {', '.join(sel)} FROM {q(raw)}"
    )
    wb.con.execute(f"DROP TABLE IF EXISTS {q(raw)}")
    sh.nrows = wb.con.execute(f"SELECT count(*) FROM {q(sh.table)}").fetchone()[0]
    sh.frozen_rows = 1
    sh.frozen_cols = nrow_fields
    wb._autosize(sh)
    for c in sh.columns:
        if c.name in old_widths:
            c.width = old_widths[c.name]
    for k, c in enumerate(sh.columns):
        if k >= nrow_fields and c.typ == NUM and not c.fmt:
            c.fmt = "#,##0.##"
    sh.styles = [st for st in sh.styles if not st.get("pivot")]
    if spec.get("totals", True) and nrow_fields:
        sh.styles.append(
            {
                "r1": sh.nrows,
                "r2": sh.nrows,
                "c1": 0,
                "c2": max(len(sh.columns) - 1, 0),
                "bold": True,
                "bg": "#eef2ff",
                "pivot": True,
            }
        )
    spec = dict(spec)
    spec["source_version"] = src.version
    spec["error"] = None
    sh.pivot = spec


def refresh_stale_pivots(wb: Workbook):
    for sh in list(wb.sheets):
        spec = sh.pivot
        if not spec:
            continue
        try:
            src = wb.sheet(spec["source"])
        except EngineError:
            spec["error"] = "Source sheet was deleted"
            continue
        if src.version == spec.get("source_version") or src is sh:
            continue
        try:
            sql, labels, nrf = _pivot_sql(wb, spec)
            _fill_pivot(wb, sh, spec, sql, labels, nrf)
            sh.version += 1
        except (KeyError, duckdb.Error) as e:
            spec["error"] = str(e).split("\n")[0]
            spec["source_version"] = src.version


def pivot_preview(wb: Workbook, spec: dict, limit: int = 50) -> dict:
    sql, labels, _ = _pivot_sql(wb, spec)
    rows = wb.con.execute(f"SELECT * FROM ({sql}) LIMIT {limit}").fetchall()
    return {"labels": labels, "rows": [[to_json(v) for v in r] for r in rows]}


# --------------------------------------------------------------- col types


def set_column_type(wb: Workbook, sheet_id: str, col: int, duck: str) -> dict:
    """Explicitly convert a column (Number / Text / Date / Boolean)."""
    sh = wb.sheet(sheet_id)
    c = sh.columns[col]
    duck = duck.upper()
    if duck not in ("DOUBLE", "BIGINT", "VARCHAR", "DATE", "TIMESTAMP", "BOOLEAN"):
        raise EngineError("Unsupported type")
    wb._begin("Change column type", [(sh.id, "table")])
    if duck in ("DOUBLE", "BIGINT") and c.typ == STR:
        conv = (
            f"TRY_CAST(replace(replace(replace(trim({q(c.id)}), ',', ''), '$', ''), '%', '') AS {duck}) "
            f"/ CASE WHEN trim({q(c.id)}) LIKE '%\\%' ESCAPE '\\' THEN 100 ELSE 1 END"
        )
        wb.con.execute(f"ALTER TABLE {q(sh.table)} ALTER {q(c.id)} TYPE {duck} USING {conv}")
        c.duck = duck
    elif duck == "DATE" and c.typ == STR:
        conv = (
            f"coalesce(TRY_CAST({q(c.id)} AS DATE), TRY_CAST(try_strptime({q(c.id)}, "
            f"['%m/%d/%Y', '%d/%m/%Y', '%m/%d/%y', '%d-%b-%Y', '%b %d, %Y', '%Y/%m/%d']) AS DATE))"
        )
        wb.con.execute(f"ALTER TABLE {q(sh.table)} ALTER {q(c.id)} TYPE DATE USING {conv}")
        c.duck = "DATE"
    else:
        wb._retype(sh, c, duck if duck != c.duck else merge_duck(c.duck, duck))
    wb.recalc([(sh.id, col, col, 0, None)])
    wb._touch(sh)
    return {"ok": True}


def column_profile(wb: Workbook, sheet_id: str, col: int) -> dict:
    """Quick stats for a column header popover."""
    sh = wb.sheet(sheet_id)
    c = sh.columns[col]
    x = q(c.id)
    n = num_nullable(S(x, c.typ))
    row = wb.con.execute(
        f"SELECT count(*), count({x}), count(DISTINCT {x}), min({n}), max({n}), avg({n}) FROM {q(sh.table)}"
    ).fetchone()
    top = wb.con.execute(
        f"SELECT {txt(S(x, c.typ))}, count(*) FROM {q(sh.table)} WHERE {x} IS NOT NULL "
        f"GROUP BY 1 ORDER BY 2 DESC LIMIT 5"
    ).fetchall()
    return {
        "rows": row[0],
        "filled": row[1],
        "distinct": row[2],
        "min": to_json(row[3]),
        "max": to_json(row[4]),
        "avg": to_json(row[5]),
        "top": [[a, b] for a, b in top],
        "type": c.typ,
    }


# ----------------------------------------------------- key matching diagnostics

_FAMILY_LABEL = {NUM: "Number", STR: "Text", BOOL: "TRUE/FALSE", DATE: "Date", TS: "Date & time"}
_INVISIBLE_RE = (
    r"'[\x{200B}-\x{200D}\x{2060}\x{FEFF}\x{00AD}\x{00A0}\x{2000}-\x{200A}\x{202F}\x{205F}\x{3000}[:cntrl:]]'"
)


def _exact_key(x: S) -> str:
    """Value used for exact (same-kind) comparison, mirroring compiler.exact_eq."""
    if x.typ == STR:
        return f"lower({x.sql})"
    if x.typ in (DATE, TS):
        return f"CAST({x.sql} AS TIMESTAMP)"
    return x.sql


def _family(typ: str) -> str:
    return {DATE: "d", TS: "d"}.get(typ, typ)


def _column_health(wb: Workbook, sh: Sheet, c: Column) -> dict:
    """How messy a key column is: numbers stored as text, stray spaces, invisible characters."""
    x = q(c.id)
    s = txt(S(x, c.typ))
    num_text = (
        f"count(*) FILTER (WHERE ps_textnum(lower(ps_cleantext({x}))) IS NOT NULL)" if c.typ == STR else "0"
    )
    row = wb.con.execute(
        f"SELECT count({x}), {num_text}, "
        f"count(*) FILTER (WHERE {s} <> trim({s}) OR contains({s}, '  ')), "
        f"count(*) FILTER (WHERE regexp_matches({s}, {_INVISIBLE_RE})), "
        f"count(*) FILTER (WHERE {x} IS NOT NULL AND ps_normkey({s}) IS NULL) "
        f"FROM {q(sh.table)}"
    ).fetchone()
    filled, numeric_text, spaces, invisible, empty = row
    return {
        "sheet": sh.id,
        "sheet_name": sh.name,
        "col": sh.columns.index(c),
        "name": c.name,
        "type": _FAMILY_LABEL.get(c.typ, "Text"),
        "filled": filled,
        "numbers_as_text": numeric_text,
        "all_numeric": c.typ == NUM
        or (c.typ == STR and filled - empty > 0 and numeric_text == filled - empty),
        "spaces": spaces,
        "invisible": invisible,
        "whitespace_only": empty,
    }


def match_report(wb: Workbook, sheet_a: str, col_a: int, sheet_b: str, col_b: int, samples: int = 25) -> dict:
    """Compare the keys of column A (the values you look up) with column B (where you look
    them up): which match exactly, which match only once formats are normalised (and why),
    and which are genuinely missing."""
    sa_sh, sb_sh = wb.sheet(sheet_a), wb.sheet(sheet_b)
    if col_a >= len(sa_sh.columns) or col_b >= len(sb_sh.columns):
        raise EngineError("Pick a column that exists")
    ca, cb = sa_sh.columns[col_a], sb_sh.columns[col_b]
    xa, xb = S("a.v", ca.typ), S("b.v", cb.typ)
    same_kind = _family(ca.typ) == _family(cb.typ)
    exact_b = f"(SELECT DISTINCT {_exact_key(xb)} AS e FROM bb AS b WHERE b.v IS NOT NULL)"
    is_exact = f"({_exact_key(xa)} IN (SELECT e FROM ex))" if same_kind else "FALSE"
    tmp = f"__mr{next(wb._tmp)}"
    wb.con.execute(
        f"CREATE OR REPLACE TEMP TABLE {tmp} AS "
        f"WITH bb AS (SELECT __rid AS rid, {q(cb.id)} AS v FROM {q(sb_sh.table)}), "
        f"ex AS {exact_b}, "
        f"bk AS (SELECT ps_normkey({txt(xb)}) AS k, arg_min({txt(xb)}, b.rid) AS s, min(b.rid) AS rid "
        f"       FROM bb AS b WHERE b.v IS NOT NULL GROUP BY 1) "
        f"SELECT a.rid, {txt(xa)} AS sa, ps_normkey({txt(xa)}) AS k, {is_exact} AS exact, "
        f"bk.s AS sb, bk.rid AS brid "
        f"FROM (SELECT __rid AS rid, {q(ca.id)} AS v FROM {q(sa_sh.table)}) AS a "
        f"LEFT JOIN bk ON bk.k = ps_normkey({txt(xa)}) "
        f"WHERE a.v IS NOT NULL AND ps_normkey({txt(xa)}) IS NOT NULL"
    )
    try:
        reasons_sql = {
            "type": "FALSE" if same_kind else "TRUE",
            "invisible": f"regexp_matches(sa || sb, {_INVISIBLE_RE})",
            "spaces": "(sa <> trim(sa) OR sb <> trim(sb) OR contains(sa, '  ') OR contains(sb, '  '))",
            "zeros": "(regexp_matches(sa, '^\\s*[+-]?0\\d') OR regexp_matches(sb, '^\\s*[+-]?0\\d'))",
            "separators": "(contains(sa, ',') OR contains(sb, ',') OR regexp_matches(sa || sb, '[$€£¥]'))",
            "decimals": "(regexp_matches(sa, '\\.\\d*$|e[+-]?\\d', 'i') OR regexp_matches(sb, '\\.\\d*$|e[+-]?\\d', 'i'))",
        }
        fuzzy = "(NOT exact AND brid IS NOT NULL)"
        sel = ", ".join(f"count(*) FILTER (WHERE {fuzzy} AND {e})" for e in reasons_sql.values())
        any_reason = " OR ".join(f"({e})" for e in reasons_sql.values())
        row = wb.con.execute(
            f"SELECT count(*), count(*) FILTER (WHERE exact), count(*) FILTER (WHERE {fuzzy}), "
            f"count(*) FILTER (WHERE NOT exact AND brid IS NULL), "
            f"count(*) FILTER (WHERE {fuzzy} AND NOT ({any_reason})), {sel} FROM {tmp}"
        ).fetchone()
        total, n_exact, n_fuzzy, n_missing, n_other = row[:5]
        reasons = dict(zip(reasons_sql, row[5:], strict=True))
        reasons["other"] = n_other
        label = " ".join(f"WHEN {e} THEN '{k}'" for k, e in reasons_sql.items())
        fz = wb.con.execute(
            f"SELECT rid + 1, sa, brid + 1, sb, CASE {label} ELSE 'other' END FROM {tmp} "
            f"WHERE {fuzzy} ORDER BY rid LIMIT {int(samples)}"
        ).fetchall()
        miss = wb.con.execute(
            f"SELECT rid + 1, sa FROM {tmp} WHERE NOT exact AND brid IS NULL ORDER BY rid LIMIT {int(samples)}"
        ).fetchall()
    finally:
        wb.con.execute(f"DROP TABLE IF EXISTS {tmp}")
    ha, hb = _column_health(wb, sa_sh, ca), _column_health(wb, sb_sh, cb)
    suggest = "number" if ha["all_numeric"] and hb["all_numeric"] else "text"
    return {
        "a": ha,
        "b": hb,
        "total": total,
        "exact": n_exact,
        "fuzzy": n_fuzzy,
        "missing": n_missing,
        "reasons": reasons,
        "fuzzy_samples": [
            {"row": r, "value": a, "b_row": br, "b_value": b, "reason": why} for r, a, br, b, why in fz
        ],
        "missing_samples": [{"row": r, "value": a} for r, a in miss],
        "suggest": suggest,
    }


def normalize_columns(
    wb: Workbook, targets: list[dict], to: str = "auto", keep_original: bool = False
) -> dict:
    """Make key columns consistent so exact matching works: remove invisible characters,
    trim and collapse spaces, and store every value as the same kind (number or text).
    Values are kept - "00123" becomes the number 123 only when converting to numbers."""
    if to not in ("auto", "number", "text"):
        raise EngineError("Convert to number, text or auto")
    resolved = []
    for t in targets:
        sh = wb.sheet(t["sheet"])
        if t["col"] >= len(sh.columns):
            raise EngineError("Pick a column that exists")
        c = sh.columns[t["col"]]
        if any(f.sheet_id == sh.id and f.col_id == c.id for f in wb.formulas.values()):
            raise EngineError(
                f"Column {c.name or col_to_letters(t['col'])} on {sh.name} has formulas; normalise its source data instead"
            )
        resolved.append((sh, c))
    if not resolved:
        return {"columns": []}
    # decide the target kind once for all columns so they end up comparable
    if to == "auto":
        healths = [_column_health(wb, sh, c) for sh, c in resolved]
        to = "number" if all(h["all_numeric"] for h in healths) else "text"
    if to == "number":
        for sh, c in resolved:
            if c.typ == NUM:
                continue
            x = q(c.id)
            bad = wb.con.execute(
                f"SELECT count(*), any_value({txt(S(x, c.typ))}) FROM {q(sh.table)} WHERE {x} IS NOT NULL "
                f"AND ps_normkey({txt(S(x, c.typ))}) IS NOT NULL "
                f"AND ps_textnum(lower(ps_cleantext({txt(S(x, c.typ))}))) IS NULL"
            ).fetchone()
            if bad[0]:
                raise EngineError(
                    f"{c.name or 'This column'} on {sh.name} has {bad[0]:,} value(s) that aren't numbers "
                    f"(e.g. {bad[1]!r}); convert to text instead"
                )
    scopes = list(dict.fromkeys((sh.id, "table") for sh, _ in resolved))
    wb._begin("Normalise keys", scopes)
    out = []
    for sh, c in resolved:
        col = sh.columns.index(c)
        x = q(c.id)
        s = txt(S(x, c.typ))
        cleaned = f"NULLIF(ps_cleantext({s}), '')"
        if to == "text" and c.typ == STR:
            # numeric-looking text loses thousands separators / currency ("1,234" → "1234");
            # leading zeros stay, so codes like "00123" are kept as written
            cleaned = (
                f"(CASE WHEN ps_textnum(lower({cleaned})) IS NOT NULL "
                f"THEN regexp_replace({cleaned}, '[,$€£¥]', '', 'g') ELSE {cleaned} END)"
            )
        kind = NUM if to == "number" else STR
        if c.typ != kind:
            changed = wb.con.execute(f"SELECT count({x}) FROM {q(sh.table)}").fetchone()[0]
        else:
            changed = (
                0
                if kind == NUM
                else wb.con.execute(
                    f"SELECT count(*) FROM {q(sh.table)} WHERE {x} IS DISTINCT FROM {cleaned}"
                ).fetchone()[0]
            )
        if changed and keep_original:
            _copy_column_to_end(wb, sh, col, f"{c.name or col_to_letters(col)} (original)")
        if c.typ != kind:
            using = f"ps_textnum(lower({cleaned}))" if kind == NUM else cleaned
            duck = "DOUBLE" if kind == NUM else "VARCHAR"
            wb.con.execute(f"ALTER TABLE {q(sh.table)} ALTER {x} TYPE {duck} USING {using}")
            c.duck = duck
        elif changed:
            wb.con.execute(f"UPDATE {q(sh.table)} SET {x} = {cleaned} WHERE {x} IS DISTINCT FROM {cleaned}")
        out.append({"sheet": sh.id, "col": col, "name": c.name, "changed": changed, "type": to})
    for sh, c in resolved:
        col = sh.columns.index(c)
        wb.recalc([(sh.id, col, col, 0, None)])
        wb._touch(sh)
    return {"columns": out, "type": to}


def _copy_column_to_end(wb: Workbook, sh: Sheet, col: int, name: str) -> int:
    """Copy column ``col`` into the first free column after the used area (inside the caller's
    undo step).  Nothing is inserted, so no existing reference or VLOOKUP index shifts."""
    src = sh.columns[col]
    used = {f.col_id for f in wb.formulas.values() if f.sheet_id == sh.id}
    counts = wb.con.execute(
        f"SELECT {', '.join(f'count({q(c.id)})' for c in sh.columns)} FROM {q(sh.table)}"
    ).fetchone()
    last = max(
        (i for i, c in enumerate(sh.columns) if c.name or counts[i] or c.id in used or c.linked), default=-1
    )
    dest = last + 1
    wb._ensure_cols(sh, dest)
    d = sh.columns[dest]
    wb._retype(sh, d, src.duck, clear=True)
    wb.con.execute(f"UPDATE {q(sh.table)} SET {q(d.id)} = {q(src.id)}")
    d.name, d.fmt, d.width = name, src.fmt, max(d.width, src.width)
    return dest
