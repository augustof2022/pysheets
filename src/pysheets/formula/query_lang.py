"""Translate Google Sheets QUERY() (Google Visualization query language) to DuckDB SQL."""

from __future__ import annotations

import re

from .refs import col_to_letters

_TOKEN = re.compile(
    r"""\s*(?:
        (?P<str>'(?:[^']|'')*'|"(?:[^"]|"")*")
      | (?P<bt>`[^`]*`)
      | (?P<num>\d+(?:\.\d*)?(?:[eE][+-]?\d+)?|\.\d+)
      | (?P<id>[A-Za-z_][A-Za-z0-9_]*)
      | (?P<op><=|>=|<>|!=|[-+*/(),=<>%])
    )""",
    re.X,
)

_CLAUSES = ["select", "where", "group by", "pivot", "order by", "limit", "offset", "label",
            "format", "options"]

_AGGS = {"sum", "avg", "count", "max", "min"}


def _tokens(q: str) -> list[tuple[str, str]]:
    out = []
    pos = 0
    q = q.strip()
    while pos < len(q):
        m = _TOKEN.match(q, pos)
        if not m or m.end() == pos:
            if q[pos:].strip() == "":
                break
            raise _err(f"Unexpected text in query near '{q[pos:pos + 15]}'")
        kind = m.lastgroup
        out.append((kind, m.group(kind)))
        pos = m.end()
    return out


def _err(msg):
    from .compiler import FormulaError

    return FormulaError("#VALUE!", f"QUERY: {msg}")


def _split_clauses(toks):
    clauses: dict[str, list] = {}
    cur = None
    i = 0
    while i < len(toks):
        kind, val = toks[i]
        low = val.lower() if kind == "id" else None
        nxt = toks[i + 1][1].lower() if i + 1 < len(toks) and toks[i + 1][0] == "id" else None
        key = None
        if low in ("group", "order") and nxt == "by":
            key = f"{low} by"
            i += 2
        elif low in ("select", "where", "pivot", "limit", "offset", "label", "format", "options"):
            key = low
            i += 1
        if key:
            if key in clauses:
                raise _err(f"duplicate {key} clause")
            clauses[key] = []
            cur = key
            continue
        if cur is None:
            raise _err(f"query must start with a clause, found '{val}'")
        clauses[cur].append(toks[i])
        i += 1
    return clauses


def _split_commas(toks):
    items, cur, depth = [], [], 0
    for t in toks:
        if t[0] == "op" and t[1] == "(":
            depth += 1
        elif t[0] == "op" and t[1] == ")":
            depth -= 1
        if t[0] == "op" and t[1] == "," and depth == 0:
            items.append(cur)
            cur = []
        else:
            cur.append(t)
    if cur:
        items.append(cur)
    return items


def _norm(toks) -> str:
    return "".join(v.lower() for _, v in toks)


def translate_query(comp, data, query: str, headers: int):
    from .compiler import STR, A, Base, S, sql_str

    data = comp.to_relation(data)
    b = data.base
    where0 = " AND ".join(b.where) or "TRUE"
    ncols = data.ncols
    alias = comp.alias("qd")
    sel = ", ".join(f"{c.sql} AS v{j}" for j, c in enumerate(data.cols))
    src = f"(SELECT {sel}, {b.order} AS __o FROM {b.frm} WHERE {where0}) AS {alias}"

    # column identifiers: sheet letters when the data is a range, plus Col1..ColN
    idmap: dict[str, int] = {}
    first = data.src.first_col if data.src is not None else None
    for j in range(ncols):
        idmap[f"COL{j + 1}"] = j
        if first is not None:
            idmap[col_to_letters(first + j)] = j
    labels_in = [None] * ncols
    if data.header is not None and headers != 0:
        for j, h in enumerate(data.header):
            labels_in[j] = h.lit if h.is_lit else None
    col_types = [c.typ for c in data.cols]

    toks = _tokens(query)
    clauses = _split_clauses(toks) if toks else {}

    def expr(tokens) -> tuple[str, str]:
        """Translate an expression; returns (sql, type guess)."""
        out = []
        typ = None
        i = 0
        while i < len(tokens):
            kind, val = tokens[i]
            low = val.lower()
            if kind == "str":
                q = val[0]
                inner = val[1:-1].replace(q + q, q)
                out.append(sql_str(inner))
                typ = typ or STR
            elif kind == "bt":
                name = val[1:-1].upper()
                if name not in idmap:
                    raise _err(f"unknown column {val}")
                out.append(f"{alias}.v{idmap[name]}")
                typ = typ or col_types[idmap[name]]
            elif kind == "num":
                out.append(val)
                typ = typ or "num"
            elif kind == "op":
                out.append("<>" if val == "!=" else val)
            elif kind == "id":
                nxt = tokens[i + 1][1] if i + 1 < len(tokens) else None
                nxt2 = tokens[i + 2][1].lower() if i + 2 < len(tokens) else None
                if nxt == "(":
                    j, depth, inner = i + 2, 1, []
                    while j < len(tokens):
                        if tokens[j][1] == "(":
                            depth += 1
                        elif tokens[j][1] == ")":
                            depth -= 1
                            if depth == 0:
                                break
                        inner.append(tokens[j])
                        j += 1
                    args = [expr(a)[0] for a in _split_commas(inner)] if inner else []
                    if low == "month":
                        out.append(f"(month({args[0]}) - 1)")
                    elif low == "todate":
                        out.append(f"CAST({args[0]} AS DATE)")
                    elif low == "datediff":
                        out.append(f"date_diff('day', CAST({args[1]} AS DATE), CAST({args[0]} AS DATE))")
                    elif low == "dayofweek":
                        out.append(f"(dayofweek({args[0]}) + 1)")
                    elif low == "now":
                        out.append("CAST(now() AS TIMESTAMP)")
                    elif low == "count" and inner and inner[0][1] == "*":
                        out.append("count(*)")
                    else:
                        out.append(f"{_FUNC_MAP.get(low, low)}({', '.join(args)})")
                    if low in _AGGS:
                        typ = typ or "num"
                    i = j + 1
                    continue
                if low in ("date", "datetime", "timestamp", "timeofday") and nxt and tokens[i + 1][0] == "str":
                    lit = tokens[i + 1][1][1:-1]
                    sqlt = {"date": "DATE", "timeofday": "TIME"}.get(low, "TIMESTAMP")
                    out.append(f"{sqlt} {sql_str(lit)}")
                    i += 2
                    continue
                if low == "contains" and nxt is not None:
                    rhs, _ = expr([tokens[i + 1]])
                    out.append(f"LIKE '%' || {rhs} || '%'")
                    i += 2
                    continue
                if low in ("starts", "ends") and nxt2 is not None and nxt.lower() == "with":
                    rhs, _ = expr([tokens[i + 2]])
                    out.append(f"LIKE {rhs} || '%'" if low == "starts" else f"LIKE '%' || {rhs}")
                    i += 3
                    continue
                if low == "matches" and nxt is not None:
                    # regexp_full_match is infix-unfriendly; emulate with SIMILAR TO semantics
                    rhs, _ = expr([tokens[i + 1]])
                    out.append(f"SIMILAR TO {rhs}")
                    i += 2
                    continue
                up = val.upper()
                if up in idmap and low not in _KEYWORDS:
                    out.append(f"{alias}.v{idmap[up]}")
                    typ = typ or col_types[idmap[up]]
                elif low in _KEYWORDS:
                    out.append(low.upper())
                elif up in idmap:
                    out.append(f"{alias}.v{idmap[up]}")
                else:
                    raise _err(f"unknown column '{val}'")
            i += 1
        return " ".join(out), typ or "num"

    def label_for(item_toks) -> str:
        if len(item_toks) == 1 and item_toks[0][0] in ("id", "bt"):
            name = item_toks[0][1].strip("`").upper()
            if name in idmap:
                lab = labels_in[idmap[name]]
                return lab if lab is not None else ""
        # aggregate of a column → "sum Price"
        if (len(item_toks) == 4 and item_toks[0][1].lower() in _AGGS and item_toks[1][1] == "("
                and item_toks[3][1] == ")"):
            name = item_toks[2][1].strip("`").upper()
            if name in idmap:
                lab = labels_in[idmap[name]] or item_toks[2][1]
                return f"{item_toks[0][1].lower()} {lab}"
        return " ".join(v for _, v in item_toks)

    # --- select
    sel_toks = clauses.get("select")
    if sel_toks is None or (len(sel_toks) == 1 and sel_toks[0][1] == "*"):
        items = [[("id", f"COL{j + 1}")] for j in range(ncols)]
        if first is not None:
            items = [[("id", col_to_letters(first + j))] for j in range(ncols)]
    else:
        items = _split_commas(sel_toks)

    group_items = _split_commas(clauses.get("group by", []))
    pivot_items = _split_commas(clauses.get("pivot", []))

    def is_agg(item):
        return any(t[0] == "id" and t[1].lower() in _AGGS and i + 1 < len(item) and item[i + 1][1] == "("
                   for i, t in enumerate(item))

    custom_labels: dict[str, str] = {}
    for lab_item in _split_commas(clauses.get("label", [])):
        if len(lab_item) >= 2 and lab_item[-1][0] == "str":
            q = lab_item[-1][1][0]
            custom_labels[_norm(lab_item[:-1])] = lab_item[-1][1][1:-1].replace(q + q, q)

    where_sql = expr(clauses["where"])[0] if "where" in clauses else "TRUE"
    group_sql = [expr(g)[0] for g in group_items]

    sel_exprs = []
    sel_labels = []
    pivot_vals = []
    if pivot_items:
        piv_sql = expr(pivot_items[0])[0]
        rows = comp.con.execute(
            f"SELECT DISTINCT {piv_sql} AS p FROM {src} WHERE {where_sql} ORDER BY 1 NULLS LAST LIMIT 500"
        ).fetchall()
        pivot_vals = [r[0] for r in rows]
    for item in items:
        sql, typ = expr(item)
        lab = custom_labels.get(_norm(item), label_for(item))
        if pivot_items and is_agg(item):
            # expand aggregate per pivot value
            agg_name = item[0][1].lower()
            inner = expr(item[2:-1])[0]
            for pv in pivot_vals:
                cond = f"{piv_sql} IS NULL" if pv is None else f"{piv_sql} = {_py_sql(pv)}"
                sel_exprs.append((f"{agg_name}({inner}) FILTER (WHERE {cond})", "num"))
                pv_lab = "" if pv is None else _fmt_label(pv)
                n_agg = sum(1 for it in items if is_agg(it))
                sel_labels.append(pv_lab if n_agg == 1 else f"{pv_lab} {lab}")
            continue
        sel_exprs.append((sql, typ))
        sel_labels.append(lab)

    order_items = _split_commas(clauses.get("order by", []))
    order_sql = []
    for it in order_items:
        direction = "ASC"
        if it and it[-1][0] == "id" and it[-1][1].lower() in ("asc", "desc"):
            direction = it[-1][1].upper()
            it = it[:-1]
        order_sql.append(f"{expr(it)[0]} {direction} NULLS LAST")
    has_group = bool(group_sql) or any(is_agg(it) for it in items)
    if not order_sql:
        if group_sql:
            order_sql = [f"{g} ASC NULLS LAST" for g in group_sql]
        elif not has_group:
            order_sql = [f"{alias}.__o ASC NULLS LAST"]
    limit = int(clauses["limit"][0][1]) if "limit" in clauses else None
    offset = int(clauses["offset"][0][1]) if "offset" in clauses else 0

    sel_list = ", ".join(f"{s} AS v{j}" for j, (s, _) in enumerate(sel_exprs))
    keys = ", ".join(f"{o.rsplit(' ', 3)[0]} AS __k{j}" for j, o in enumerate(order_sql))
    key_order = ", ".join(f"__k{j} {' '.join(o.rsplit(' ', 3)[1:])}" for j, o in enumerate(order_sql))
    inner = f"SELECT {sel_list}{', ' + keys if keys else ''} FROM {src} WHERE {where_sql}"
    if group_sql:
        inner += " GROUP BY " + ", ".join(group_sql)
    out_alias = comp.alias("qr")
    vcols = ", ".join(f"v{j}" for j in range(len(sel_exprs)))
    frm = (f"(SELECT {vcols}, row_number() OVER ({'ORDER BY ' + key_order if key_order else ''}) AS __o "
           f"FROM ({inner}) AS _qi) AS {out_alias}")
    conds = []
    if offset:
        conds.append(f"{out_alias}.__o > {offset}")
    if limit is not None:
        conds.append(f"{out_alias}.__o <= {offset + limit}")
    base = Base(frm, out_alias, conds, f"{out_alias}.__o", ("query", out_alias))

    # infer output types with a zero-row probe
    try:
        desc = comp.con.execute(f"SELECT * FROM ({inner}) AS _qi LIMIT 0").description
        from .compiler import duck_to_typ

        types = [duck_to_typ(str(d[1])) for d in desc[: len(sel_exprs)]]
    except Exception as exc:  # surface SQL problems as #VALUE!
        raise _err(str(exc).split("\n")[0]) from exc
    cols = [S(f"{out_alias}.v{j}", types[j]) for j in range(len(sel_exprs))]
    want_header = headers != 0 and (data.header is not None or headers > 0 or bool(custom_labels)
                                    or has_group or pivot_items)
    from .compiler import lit_sql

    header = [lit_sql(lab) for lab in sel_labels] if want_header else None
    return A(base, cols, header=header)


def _py_sql(v):
    from .compiler import sql_str

    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, (int, float)):
        return repr(v)
    if hasattr(v, "isoformat"):
        return f"CAST({sql_str(v.isoformat())} AS {'DATE' if len(v.isoformat()) == 10 else 'TIMESTAMP'})"
    return sql_str(str(v))


def _fmt_label(v) -> str:
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    if hasattr(v, "isoformat"):
        return v.isoformat()
    return str(v)


_KEYWORDS = {"and", "or", "not", "is", "null", "true", "false", "asc", "desc", "like", "in",
             "between"}

_FUNC_MAP = {
    "avg": "avg", "sum": "sum", "count": "count", "max": "max", "min": "min",
    "upper": "upper", "lower": "lower", "year": "year", "day": "day", "hour": "hour",
    "minute": "minute", "second": "second", "quarter": "quarter",
    "dayofweek": "1 + dayofweek", "now": "now", "todate": "CAST",
    "datediff": "date_diff", "millisecond": "millisecond",
}
