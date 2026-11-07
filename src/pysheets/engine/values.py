"""Parsing user input and converting DuckDB values to JSON."""

from __future__ import annotations

import datetime as dt
import decimal
import math
import re
import uuid

_NUM_RE = re.compile(r"^\s*([-+]?)\s*(\$|€|£|¥)?\s*((?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d*)?|\.\d+)([eE][-+]?\d+)?\s*(%?)\s*$")
_DATE_FORMATS = ["%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%Y/%m/%d", "%d-%b-%Y", "%d %b %Y", "%b %d %Y",
                 "%b %d, %Y", "%B %d, %Y", "%d %B %Y"]
_TS_FORMATS = ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M",
               "%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M"]

NUMERIC_DUCK = ("BIGINT", "INTEGER", "DOUBLE", "FLOAT", "HUGEINT", "SMALLINT", "TINYINT", "UBIGINT",
                "UINTEGER", "USMALLINT", "UTINYINT", "REAL", "DECIMAL", "UHUGEINT", "NUMERIC")


def is_numeric_duck(t: str) -> bool:
    return t.upper().startswith(NUMERIC_DUCK)


def parse_input(text: str | None):
    """Interpret what a user typed into a cell.

    Returns ``(value, duck_type, format_hint)``.  Formulas are handled by the caller.
    """
    if text is None:
        return None, None, None
    if not isinstance(text, str):
        if isinstance(text, bool):
            return text, "BOOLEAN", None
        if isinstance(text, (int, float)):
            return float(text), "DOUBLE", None
        return str(text), "VARCHAR", None
    s = text
    if s == "":
        return None, None, None
    if s.startswith("'"):
        return s[1:], "VARCHAR", None
    up = s.strip().upper()
    if up in ("TRUE", "FALSE"):
        return up == "TRUE", "BOOLEAN", None
    m = _NUM_RE.match(s)
    if m:
        sign, cur, body, exp, pct = m.groups()
        try:
            v = float(body.replace(",", "") + (exp or ""))
        except ValueError:
            v = None
        if v is not None:
            if sign == "-":
                v = -v
            fmt = None
            if pct:
                v = v / 100.0
                fmt = "0.00%" if "." in body else "0%"
            elif cur:
                fmt = cur + "#,##0.00"
            elif "," in body:
                fmt = "#,##0" + (".00" if "." in body else "")
            return v, "DOUBLE", fmt
    st = s.strip()
    if 6 <= len(st) <= 30 and re.search(r"\d", st):
        for f in _TS_FORMATS:
            try:
                return dt.datetime.strptime(st, f), "TIMESTAMP", None
            except ValueError:
                pass
        for f in _DATE_FORMATS:
            try:
                return dt.datetime.strptime(st, f).date(), "DATE", None
            except ValueError:
                pass
    return s, "VARCHAR", None


def value_to_text(v) -> str:
    """Sheets-like text rendering used when a value lands in a text column."""
    if v is None:
        return ""
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, float):
        if math.isfinite(v) and v == int(v) and abs(v) < 1e15:
            return str(int(v))
        return repr(v)
    if isinstance(v, decimal.Decimal):
        return value_to_text(float(v))
    if isinstance(v, dt.datetime):
        return v.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(v, dt.date):
        return v.isoformat()
    return str(v)


def to_json(v):
    """Make a DuckDB value JSON-friendly."""
    if v is None or isinstance(v, (bool, str)):
        return v
    if isinstance(v, int):
        if abs(v) > 2**53:
            return str(v)
        return v
    if isinstance(v, float):
        if math.isnan(v) or math.isinf(v):
            return None
        return v
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, dt.datetime):
        return v.isoformat(sep=" ")
    if isinstance(v, (dt.date, dt.time)):
        return v.isoformat()
    if isinstance(v, dt.timedelta):
        return str(v)
    if isinstance(v, uuid.UUID):
        return str(v)
    if isinstance(v, (bytes, bytearray, memoryview)):
        return bytes(v).hex()
    if isinstance(v, (list, tuple, dict)):
        return str(v)
    return str(v)


def duck_literal(v, duck: str | None = None) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, (int, float)):
        if isinstance(v, float) and not math.isfinite(v):
            return "NULL"
        return f"CAST({v!r} AS DOUBLE)"
    if isinstance(v, dt.datetime):
        return f"TIMESTAMP '{v.isoformat(sep=' ')}'"
    if isinstance(v, dt.date):
        return f"DATE '{v.isoformat()}'"
    s = str(v).replace("'", "''")
    return f"'{s}'"


def common_type(types: list[str | None]) -> str | None:
    ts = {t for t in types if t}
    if not ts:
        return None
    if len(ts) == 1:
        return ts.pop()
    if ts <= {"DATE", "TIMESTAMP"}:
        return "TIMESTAMP"
    return "VARCHAR"
