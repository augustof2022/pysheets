"""Compile spreadsheet formulas into DuckDB SQL.

Every formula becomes SQL.  Two compile modes exist:

* ``cell``: the formula lives in one cell.  Cell references become scalar
  sub-queries, ranges become relations, and the final value is either a scalar
  expression (``SELECT <expr>``) or an array that *spills*.
* ``row``: the formula is applied to every row of a sheet at once (a column
  formula / fill-down).  The current row is the alias ``t`` and same-row
  references become plain column references, so the whole column is computed
  by one vectorised query: this is what makes formulas fast on millions of rows.

Values flowing through the compiler are ``S`` (a scalar SQL expression with a
type) or ``A`` (an array: either a relation with column expressions, or a small
inline matrix of ``S``).
"""

from __future__ import annotations

import itertools
import re
from dataclasses import dataclass, field
from typing import Any

from . import parser as P
from .refs import Ref

NUM, STR, BOOL, DATE, TS, NULL = "num", "str", "bool", "date", "ts", "null"


class FormulaError(Exception):
    """A user-facing formula error, shown in the cell (e.g. ``#NAME?``)."""

    def __init__(self, code: str, message: str = ""):
        super().__init__(f"{code} {message}".strip())
        self.code = code
        self.message = message


# ----------------------------------------------------------------- values


@dataclass
class S:
    sql: str
    typ: str
    lit: Any = None  # python literal value when known at compile time
    is_lit: bool = False


@dataclass
class Base:
    frm: str
    alias: str
    where: list[str]
    order: str
    key: Any


@dataclass
class Src:
    """Where a raw range came from (used by lookups and QUERY)."""

    sheet: Any
    cols: list[int]  # column positions
    rid1: int | str  # first data rid (int) or SQL expression (row mode)
    rid2: int | str | None  # last data rid inclusive; None = open-ended
    has_header: bool
    first_col: int = 0


@dataclass
class A:
    base: Base | None
    cols: list[S] = field(default_factory=list)
    inline: list[list[S]] | None = None
    header: list[S] | None = None
    src: Src | None = None

    @property
    def ncols(self) -> int:
        if self.inline is not None:
            return max((len(r) for r in self.inline), default=0)
        return len(self.cols)


def lit_sql(v: Any) -> S:
    if v is None:
        return S("NULL", NULL, None, True)
    if isinstance(v, bool):
        return S("TRUE" if v else "FALSE", BOOL, v, True)
    if isinstance(v, (int, float)):
        r = repr(float(v))
        if r in ("inf", "-inf", "nan"):
            return S("NULL", NULL, None, True)
        return S(f"CAST({r} AS DOUBLE)", NUM, float(v), True)
    s = str(v)
    return S("'" + s.replace("'", "''") + "'", STR, s, True)


def sql_str(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def duck_to_typ(duck: str) -> str:
    d = duck.upper()
    if d.startswith(
        (
            "BIGINT",
            "INTEGER",
            "DOUBLE",
            "DECIMAL",
            "FLOAT",
            "HUGEINT",
            "SMALLINT",
            "TINYINT",
            "UBIGINT",
            "UINTEGER",
            "USMALLINT",
            "UTINYINT",
            "REAL",
            "UHUGEINT",
            "NUMERIC",
            "INT",
        )
    ):
        return NUM
    if d == "BOOLEAN":
        return BOOL
    if d == "DATE":
        return DATE
    if d.startswith("TIMESTAMP"):
        return TS
    return STR


# ------------------------------------------------------------ conversions


def num(x: S) -> str:
    if x.typ == NUM:
        return f"coalesce({x.sql}, 0)"
    if x.typ == NULL:
        return "CAST(0 AS DOUBLE)"
    if x.typ == BOOL:
        return f"CAST(coalesce({x.sql}, FALSE) AS DOUBLE)"
    if x.typ == DATE:
        return f"CAST(({x.sql} - DATE '1899-12-30') AS DOUBLE)"
    if x.typ == TS:
        return f"(epoch({x.sql}) / 86400.0 + 25569)"
    return (
        f"(CASE WHEN {x.sql} IS NULL OR {x.sql} = '' THEN CAST(0 AS DOUBLE) "
        f"ELSE TRY_CAST({x.sql} AS DOUBLE) END)"
    )


def num_nullable(x: S) -> str:
    """Numeric value, keeping blanks as NULL (used by aggregates)."""
    if x.typ == NUM:
        return x.sql
    if x.typ == NULL:
        return "CAST(NULL AS DOUBLE)"
    if x.typ == BOOL:
        return f"CAST({x.sql} AS DOUBLE)"
    if x.typ == DATE:
        return f"CAST(({x.sql} - DATE '1899-12-30') AS DOUBLE)"
    if x.typ == TS:
        return f"(epoch({x.sql}) / 86400.0 + 25569)"
    return f"TRY_CAST({x.sql} AS DOUBLE)"


def txt(x: S) -> str:
    if x.typ == STR:
        return x.sql
    if x.typ == NULL:
        return "CAST(NULL AS VARCHAR)"
    if x.typ == NUM:
        v = x.sql
        return (
            f"(CASE WHEN {v} IS NULL THEN NULL WHEN isfinite({v}) AND {v} = trunc({v}) "
            f"AND abs({v}) < 1e15 THEN CAST(CAST({v} AS HUGEINT) AS VARCHAR) "
            f"ELSE CAST(CAST({v} AS DOUBLE) AS VARCHAR) END)"
        )
    if x.typ == BOOL:
        return f"(CASE WHEN {x.sql} IS NULL THEN NULL WHEN {x.sql} THEN 'TRUE' ELSE 'FALSE' END)"
    if x.typ == DATE:
        return f"strftime({x.sql}, '%Y-%m-%d')"
    if x.typ == TS:
        return f"strftime({x.sql}, '%Y-%m-%d %H:%M:%S')"
    return f"CAST({x.sql} AS VARCHAR)"


def txt0(x: S) -> str:
    return f"coalesce({txt(x)}, '')"


def boo(x: S) -> str:
    if x.typ == BOOL:
        return x.sql
    if x.typ == NULL:
        return "FALSE"
    if x.typ == NUM:
        return f"({x.sql} <> 0)"
    if x.typ in (DATE, TS):
        return f"({x.sql} IS NOT NULL)"
    return (
        f"(CASE WHEN upper({x.sql}) = 'TRUE' THEN TRUE WHEN upper({x.sql}) = 'FALSE' "
        f"THEN FALSE ELSE TRY_CAST({x.sql} AS DOUBLE) <> 0 END)"
    )


def as_date(x: S) -> str:
    if x.typ == DATE:
        return x.sql
    if x.typ == TS:
        return f"CAST({x.sql} AS DATE)"
    if x.typ == NUM:
        return f"(DATE '1899-12-30' + CAST(floor({x.sql}) AS INTEGER))"
    if x.typ == NULL:
        return "CAST(NULL AS DATE)"
    return f"TRY_CAST({x.sql} AS DATE)"


def as_ts(x: S) -> str:
    if x.typ == TS:
        return x.sql
    if x.typ == DATE:
        return f"CAST({x.sql} AS TIMESTAMP)"
    if x.typ == NUM:
        return f"to_timestamp(({x.sql} - 25569) * 86400)::TIMESTAMP"
    if x.typ == NULL:
        return "CAST(NULL AS TIMESTAMP)"
    return f"TRY_CAST({x.sql} AS TIMESTAMP)"


def unify(vals: list[S]) -> tuple[str, list[str]]:
    """Find a common type for CASE/COALESCE branches."""
    typs = {v.typ for v in vals if v.typ != NULL}
    if not typs:
        return NULL, [v.sql for v in vals]
    if len(typs) == 1:
        t = typs.pop()
    elif typs <= {NUM, BOOL}:
        t = NUM
    elif typs <= {DATE, TS}:
        t = TS
    else:
        t = STR
    conv = {NUM: num_nullable, STR: txt, BOOL: lambda v: v.sql, DATE: as_date, TS: as_ts}[t]
    out = []
    for v in vals:
        if v.typ == NULL:
            out.append("NULL")
        elif v.typ == t:
            out.append(v.sql)
        else:
            out.append(conv(v))
    return t, out


def is_empty_str(x: S) -> bool:
    return x.is_lit and x.lit == ""


def blankish(x: S) -> S:
    """In IF/IFERROR branches a literal "" is shown blank; keep the other branch's type."""
    return lit_sql(None) if is_empty_str(x) else x


_CMP = {"=": "=", "<>": "<>", "<": "<", ">": ">", "<=": "<=", ">=": ">="}


def compare(a: S, b: S, op: str) -> S:
    o = _CMP[op]
    if op in ("=", "<>") and (is_empty_str(b) or b.typ == NULL or is_empty_str(a) or a.typ == NULL):
        other = a if (is_empty_str(b) or b.typ == NULL) else b
        if other.typ == NULL or is_empty_str(other):
            return S("TRUE" if op == "=" else "FALSE", BOOL)
        test = f"({other.sql} IS NULL OR {txt(other)} = '')" if other.typ == STR else f"({other.sql} IS NULL)"
        return S(test if op == "=" else f"(NOT {test})", BOOL)
    ta, tb = a.typ, b.typ
    if ta == STR and tb == STR:
        return S(f"(coalesce(lower({a.sql}), '') {o} coalesce(lower({b.sql}), ''))", BOOL)
    if ta in (DATE, TS) or tb in (DATE, TS):
        if ta in (DATE, TS) and tb in (DATE, TS):
            return S(f"({as_ts(a)} {o} {as_ts(b)})", BOOL)
        d, other = (a, b) if ta in (DATE, TS) else (b, a)
        if other.typ == STR:
            lhs, rhs = (
                (as_ts(a), f"TRY_CAST({b.sql} AS TIMESTAMP)")
                if d is a
                else (f"TRY_CAST({a.sql} AS TIMESTAMP)", as_ts(b))
            )
            return S(f"({lhs} {o} {rhs})", BOOL)
    return S(f"({num(a)} {o} {num(b)})", BOOL)


def eq_sql(a: S, b: S) -> str:
    """Equality used by lookups (no blank coalescing; NULL never matches)."""
    if a.typ == STR and b.typ == STR:
        return f"(lower({a.sql}) = lower({b.sql}))"
    if a.typ == STR or b.typ == STR:
        if NUM in (a.typ, b.typ):
            return f"({num_nullable(a)} = {num_nullable(b)})"
        return f"({txt(a)} = {txt(b)})"
    if a.typ in (DATE, TS) or b.typ in (DATE, TS):
        if a.typ in (DATE, TS) and b.typ in (DATE, TS):
            return f"({as_ts(a)} = {as_ts(b)})"
        return f"({num_nullable(a)} = {num_nullable(b)})"
    if a.typ == BOOL and b.typ == BOOL:
        return f"({a.sql} = {b.sql})"
    return f"({num_nullable(a)} = {num_nullable(b)})"


# ------------------------------------------------- smart (format-tolerant) matching
#
# Lookups compare keys in two tiers.  *Exact* means "same kind of value and equal"
# (text with text, case-insensitive; numbers with numbers; dates with dates).  The
# *normalised* tier compares the key's canonical text: invisible characters removed,
# whitespace trimmed and collapsed, lower-cased, and numeric-looking text reduced to
# the number it spells ("00123", "1,234.0", " 123 " → "123", "1234").  A match that
# exists only in the normalised tier is a *format mismatch*: the value is there, the
# formatting differs (text vs number, stray spaces, leading zeros, separators…).

# Installed on every workbook connection (see Workbook.__init__).  A lambda binds the
# cleaned text once; a scalar sub-query would defeat DuckDB's join decorrelation.
NORMKEY_MACROS = r"""
CREATE OR REPLACE TEMP MACRO ps_cleantext(s) AS
  trim(regexp_replace(regexp_replace(s, '[\x{200B}-\x{200D}\x{2060}\x{FEFF}\x{00AD}]', '', 'g'),
       '[\s\x{00A0}\x{2000}-\x{200A}\x{202F}\x{205F}\x{3000}[:cntrl:]]+', ' ', 'g'));
CREATE OR REPLACE TEMP MACRO ps_textnum(c) AS
  CASE WHEN regexp_full_match(c, '[+-]?[$€£¥]?[+-]?(\d{1,3}(,\d{3})+|\d+)(\.\d*)?([eE][+-]?\d+)?|[+-]?[$€£¥]?\.\d+')
       THEN TRY_CAST(regexp_replace(c, '[,$€£¥]', '', 'g') AS DOUBLE) END;
CREATE OR REPLACE TEMP MACRO ps_numtext(n) AS
  CASE WHEN n = trunc(n) AND abs(n) < 1e15 THEN CAST(CAST(n AS HUGEINT) AS VARCHAR) ELSE CAST(n AS VARCHAR) END;
CREATE OR REPLACE TEMP MACRO ps_normkey(s) AS
  NULLIF(list_transform([lower(ps_cleantext(CAST(s AS VARCHAR)))], lambda c:
    list_transform([ps_textnum(c)], lambda n:
      CASE WHEN n IS NOT NULL AND isfinite(n) THEN ps_numtext(n)
           ELSE regexp_replace(c, ' 00:00:00$', '') END)[1])[1], '');
"""

_FAMILY = {NUM: "n", STR: "s", BOOL: "b", DATE: "d", TS: "d"}
# text that ps_normkey would change beyond lower-casing (cheap pre-check)
_ODD_TEXT = r"'^[\s0-9+.$-]|\s$|\s\s|[^ -~]|00:00:00$'"


def exact_eq(a: S, b: S) -> str:
    """Equality between values of the same kind only (text vs number never matches)."""
    fa, fb = _FAMILY.get(a.typ), _FAMILY.get(b.typ)
    if fa is None or fb is None or fa != fb:
        return "FALSE"
    return eq_sql(a, b)


def norm_key(x: S) -> str:
    """The canonical text a value is compared by in the normalised tier."""
    if x.typ == NULL:
        return "CAST(NULL AS VARCHAR)"
    if x.typ == NUM:
        return f"NULLIF({txt(x)}, '')"
    if x.typ == BOOL:
        return f"lower({txt(x)})"
    if x.typ == DATE:
        return txt(x)
    if x.typ == TS:
        return f"regexp_replace({txt(x)}, ' 00:00:00$', '')"
    return f"ps_normkey({x.sql})"


def smart_eq(a: S, b: S) -> str:
    """Normalised equality (a superset of ``exact_eq``)."""
    if a.typ == NULL or b.typ == NULL:
        return "FALSE"
    fa, fb = _FAMILY.get(a.typ), _FAMILY.get(b.typ)
    if fa == fb and fa in ("n", "b", "d"):
        return eq_sql(a, b)
    col, k = (a, b) if b.is_lit else (b, a)
    if k.is_lit and not col.is_lit and col.typ == STR:
        # constant key: only unusual-looking cells pay for full normalisation
        kk = norm_key(k)
        return (
            f"(CASE WHEN regexp_matches({col.sql}, {_ODD_TEXT}) THEN ps_normkey({col.sql}) = {kk} "
            f"ELSE lower({col.sql}) = {kk} END)"
        )
    return f"({norm_key(a)} = {norm_key(b)})"


def like_pattern(s: str) -> str:
    out = []
    i = 0
    while i < len(s):
        ch = s[i]
        if ch == "~" and i + 1 < len(s) and s[i + 1] in "*?":
            out.append(s[i + 1])
            i += 2
            continue
        if ch == "*":
            out.append("%")
        elif ch == "?":
            out.append("_")
        elif ch in "%_\\":
            out.append("\\" + ch)
        else:
            out.append(ch)
        i += 1
    return "".join(out)


_NUMERIC_RE = re.compile(r"^\s*[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?\s*$")


# ------------------------------------------------------------------ compiler


class Compiler:
    """Compiles one formula.  ``catalog`` resolves sheet names to sheet metadata.

    Sheet metadata objects expose: ``id``, ``name``, ``table``, ``nrows``,
    ``columns`` (each with ``id``, ``name``, ``typ``).
    """

    MAX_INLINE = 20000

    def __init__(self, catalog, sheet, row: int, col: int, mode: str = "cell", con=None):
        self.catalog = catalog
        self.sheet = sheet
        self.row = row  # sheet row of the formula (0 = header row)
        self.col = col
        self.mode = mode
        self.con = con
        self.deps: list[tuple[str, int, int, int, int | None]] = []
        self._ids = itertools.count(1)
        self._bases: dict[Any, Base] = {}
        self.volatile = False
        self.uses_window = False
        # "smart": lookups/criteria see through format differences; "strict": exact kinds only
        self.match_mode = getattr(catalog, "match_mode", "smart")
        # per-row match status of each lookup (0 exact, 1 format-only, NULL none), see _probe
        self.match_probes: list[str] = []
        self.notes: list[dict] = []  # e.g. rows FILTER kept only thanks to smart matching
        self._array_depth = 0  # >0 while compiling inside an array (probes can't run there)
        # FILTER conditions: "smart" compares normalised values with "=" / "<>", "exact" only
        # matches values of the same kind (used to count rows kept thanks to smart matching)
        self._smart_cmp: str | None = None
        self._smart_hits = 0

    # ------------------------------------------------------------ utilities

    @property
    def smart(self) -> bool:
        return self.match_mode != "strict"

    def _probe(self, key: S, status_sql: str):
        """Record a lookup's per-row status: -1 blank key, 0 exact, 1 format-only, NULL not found."""
        if self._array_depth:
            return
        if key.typ == NULL:
            return
        blank = f"(nullif(trim({key.sql}), '') IS NULL)" if key.typ == STR else f"({key.sql} IS NULL)"
        sql = f"(CASE WHEN {blank} THEN -1 ELSE {status_sql} END)"
        if sql not in self.match_probes:
            self.match_probes.append(sql)

    def _constify(self, key: S) -> S:
        """In a single cell, read a referenced lookup key once so the search can use the fast path."""
        if (
            self.mode != "cell"
            or self._array_depth
            or key.is_lit
            or key.typ not in (STR, NUM)
            or self.con is None
        ):
            return key
        try:
            v = self.query_scalar(f"SELECT {key.sql}")
        except Exception:
            return key
        if v is None or (isinstance(v, (str, int, float)) and not isinstance(v, bool)):
            return lit_sql(v) if v is not None else S(f"CAST(NULL AS {self._duck_type(key.typ)})", key.typ)
        return key

    def alias(self, prefix: str = "a") -> str:
        return f"{prefix}{next(self._ids)}"

    def query_scalar(self, sql: str):
        if self.con is None:
            raise FormulaError("#VALUE!", "needs a constant argument")
        return self.con.execute(sql).fetchone()[0]

    def const_value(self, v: S):
        if v.is_lit:
            return v.lit
        return self.query_scalar(f"SELECT {v.sql}")

    def const_int(self, node, default: int | None = None) -> int | None:
        if node is None or isinstance(node, P.Blank):
            return default
        v = self.scalar(node)
        val = self.const_value(S(num(v), NUM) if v.typ != NUM else v)
        if val is None:
            return default
        return int(float(val))

    def sheet_of(self, ref: Ref):
        sh = self.catalog.get_sheet(ref.sheet) if ref.sheet else self.sheet
        if sh is None:
            raise FormulaError("#REF!", f"Unresolved sheet name '{ref.sheet}'")
        return sh

    def col_expr(self, sh, c: int, alias: str) -> S:
        if c < len(sh.columns):
            col = sh.columns[c]
            return S(f'{alias}."{col.id}"', col.typ)
        return S("NULL", NULL)

    # ----------------------------------------------------------- entry points

    def compile(self, node) -> S | A:
        return self.comp(node)

    # ------------------------------------------------------------- dispatch

    def comp(self, node) -> S | A:
        if isinstance(node, P.Num):
            return lit_sql(node.value)
        if isinstance(node, P.Str):
            return lit_sql(node.value)
        if isinstance(node, P.Bool):
            return lit_sql(node.value)
        if isinstance(node, P.Blank):
            return lit_sql(None)
        if isinstance(node, P.Err):
            raise FormulaError(node.code, "Error literal")
        if isinstance(node, P.Name):
            raise FormulaError("#NAME?", f"Unknown name: {node.name}")
        if isinstance(node, P.RefNode):
            return self.ref_value(node.ref)
        if isinstance(node, P.ArrayLit):
            rows = [[self.scalar(x) for x in row] for row in node.rows]
            width = max(len(r) for r in rows)
            if any(len(r) != width for r in rows):
                raise FormulaError("#VALUE!", "Array literal rows must have the same length")
            return A(None, inline=rows)
        if isinstance(node, P.Unary):
            return self.lift(lambda a: S(f"(-{num(a)})", NUM), [self.comp(node.operand)])
        if isinstance(node, P.Percent):
            return self.lift(lambda a: S(f"({num(a)} / 100.0)", NUM), [self.comp(node.operand)])
        if isinstance(node, P.Binary):
            return self.binary(node)
        if isinstance(node, P.Call):
            return self.call(node)
        raise FormulaError("#ERROR!", f"Unsupported syntax {type(node).__name__}")

    def scalar(self, node) -> S:
        v = self.comp(node)
        return self.to_scalar(v)

    def to_scalar(self, v: S | A) -> S:
        if isinstance(v, S):
            return v
        # implicit: first cell of an array
        if v.inline is not None:
            return v.inline[0][0] if v.inline and v.inline[0] else lit_sql(None)
        if v.header is not None:
            return v.header[0]
        if not v.cols:
            return lit_sql(None)
        b = v.base
        where = " AND ".join(b.where) or "TRUE"
        c = v.cols[0]
        return S(f"(SELECT {c.sql} FROM {b.frm} WHERE {where} ORDER BY {b.order} LIMIT 1)", c.typ)

    # ---------------------------------------------------------- references

    def ref_value(self, ref: Ref) -> S | A:
        sh = self.sheet_of(ref)
        if ref.is_cell:
            return self.cell_value(sh, ref)
        return self.range_value(sh, ref)

    def _record_dep(self, sh, c1, c2, r1, r2):
        self.deps.append((sh.id, c1, c2, r1, r2))

    def cell_value(self, sh, ref: Ref) -> S:
        R, C = ref.r1, ref.c1
        self._record_dep(sh, C, C, R, R)
        if C >= len(sh.columns):
            return lit_sql(None)
        col = sh.columns[C]
        same = sh is self.sheet
        if R == 0 and (self.mode != "row" or ref.ar1 or self.row == 0):
            return lit_sql(col.name if col.name != "" else None)
        if self.mode == "row" and not ref.ar1:
            d = R - self.row
            if same and d == 0:
                return S(f't."{col.id}"', col.typ)
            x = self.alias("x")
            return S(
                f'(SELECT {x}."{col.id}" FROM "{sh.table}" AS {x} WHERE {x}.__rid = t.__rid + ({d}))', col.typ
            )
        rid = R - 1
        if rid >= sh.nrows:
            return S(f"CAST(NULL AS {self._duck_type(col.typ)})", col.typ)
        x = self.alias("x")
        return S(f'(SELECT {x}."{col.id}" FROM "{sh.table}" AS {x} WHERE {x}.__rid = {rid})', col.typ)

    @staticmethod
    def _duck_type(typ: str) -> str:
        return {NUM: "DOUBLE", STR: "VARCHAR", BOOL: "BOOLEAN", DATE: "DATE", TS: "TIMESTAMP"}.get(
            typ, "VARCHAR"
        )

    def range_value(self, sh, ref: Ref) -> A:
        ncols_sheet = len(sh.columns)
        c1 = ref.c1 if ref.c1 is not None else 0
        c2 = ref.c2 if ref.c2 is not None else max(ncols_sheet - 1, 0)
        cols = list(range(c1, c2 + 1))
        r1 = ref.r1 if ref.r1 is not None else 0
        r2 = ref.r2  # None = open
        self._record_dep(sh, c1, c2, r1, r2)
        has_header = r1 == 0

        rel_rows = self.mode == "row" and (
            (ref.r1 is not None and not ref.ar1) or (ref.r2 is not None and not ref.ar2)
        )
        if rel_rows:
            # Same-row horizontal range in row mode: inline row vector.
            if ref.r1 == ref.r2 and not ref.ar1 and not ref.ar2 and ref.r1 == self.row and sh is self.sheet:
                return A(None, inline=[[self.col_expr(sh, c, "t") for c in cols]])

            def bound(rv, absolute):
                if absolute:
                    return str(rv - 1)
                return f"(t.__rid + ({rv - self.row}))"

            lo = bound(max(r1, 1), ref.ar1) if ref.r1 is not None else "0"
            hi = bound(r2, ref.ar2) if r2 is not None else None
            alias = self.alias("r")
            where = [f"{alias}.__rid >= {lo}"]
            if hi is not None:
                where.append(f"{alias}.__rid <= {hi}")
            base = Base(
                f'"{sh.table}" AS {alias}', alias, where, f"{alias}.__rid", ("rel", sh.id, lo, hi, alias)
            )
            header = None
            return A(
                base,
                [self.col_expr(sh, c, alias) for c in cols],
                header=header,
                src=Src(sh, cols, lo, hi, False, c1),
            )

        rid1 = max(r1, 1) - 1
        rid2 = None if r2 is None else r2 - 1
        key = (sh.id, rid1, rid2)
        base = self._bases.get(key)
        if base is None:
            alias = self.alias("r")
            where = [f"{alias}.__rid >= {rid1}"] if rid1 > 0 else []
            if rid2 is not None:
                where.append(f"{alias}.__rid <= {rid2}")
            base = Base(f'"{sh.table}" AS {alias}', alias, where, f"{alias}.__rid", key)
            self._bases[key] = base
        alias = base.alias
        header = None
        if has_header:
            header = []
            for c in cols:
                name = sh.columns[c].name if c < ncols_sheet else ""
                header.append(lit_sql(name if name != "" else None))
        data_cols = [self.col_expr(sh, c, alias) for c in cols]
        if rid2 is not None and rid2 < rid1:
            # header-only range (e.g. A1:C1)
            return A(None, inline=[header or []], src=Src(sh, cols, rid1, rid2, True, c1))
        return A(base, data_cols, header=header, src=Src(sh, cols, rid1, rid2, has_header, c1))

    # ------------------------------------------------------------- lifting

    def to_relation(self, a: A) -> A:
        """Convert an inline matrix into a relation (VALUES)."""
        if a.inline is None:
            return a
        alias = self.alias("v")
        width = a.ncols
        rows_sql = []
        typs: list[str] = []
        for j in range(width):
            t, _ = unify([r[j] if j < len(r) else lit_sql(None) for r in a.inline])
            typs.append(t)
        for i, r in enumerate(a.inline):
            cells = []
            for j in range(width):
                v = r[j] if j < len(r) else lit_sql(None)
                t = typs[j]
                conv = {
                    NUM: num_nullable,
                    STR: txt,
                    BOOL: lambda x: x.sql,
                    DATE: as_date,
                    TS: as_ts,
                    NULL: lambda x: x.sql,
                }[t]
                cells.append("NULL" if v.typ == NULL else (v.sql if v.typ == t else conv(v)))
            rows_sql.append(f"({', '.join(cells)}, {i})")
        names = ", ".join([f"v{j}" for j in range(width)] + ["__o"])
        frm = f"(VALUES {', '.join(rows_sql)}) AS {alias}({names})"
        base = Base(frm, alias, [], f"{alias}.__o", ("values", alias))
        cols = [S(f"{alias}.v{j}", typs[j]) for j in range(width)]
        return A(base, cols)

    def numbered(self, a: A) -> tuple[str, str, list[S]]:
        """Wrap a relation array as a sub-select with a position column ``__p``."""
        a = self.to_relation(a)
        b = a.base
        alias = self.alias("p")
        where = " AND ".join(b.where) or "TRUE"
        sel = ", ".join(f"{c.sql} AS v{j}" for j, c in enumerate(a.cols))
        frm = (
            f"(SELECT {sel}{', ' if sel else ''}row_number() OVER (ORDER BY {b.order}) AS __p "
            f"FROM {b.frm} WHERE {where}) AS {alias}"
        )
        return frm, alias, [S(f"{alias}.v{j}", c.typ) for j, c in enumerate(a.cols)]

    def align(self, arrays: list[A]) -> tuple[Base | None, list[A]]:
        """Bring arrays onto one shared row base."""
        rel = [a for a in arrays if a.inline is None]
        if not rel:
            return None, arrays
        keys = {a.base.key for a in rel}
        if len(keys) == 1 and len(rel) == len(arrays):
            return rel[0].base, arrays
        # positional join
        first_frm, first_alias, first_cols = self.numbered(arrays[0])
        frm = first_frm
        out = [A(None, first_cols, header=arrays[0].header)]
        for a in arrays[1:]:
            f2, al2, cols2 = self.numbered(a)
            frm += f" JOIN {f2} ON {first_alias}.__p = {al2}.__p"
            out.append(A(None, cols2, header=a.header))
        base = Base(frm, first_alias, [], f"{first_alias}.__p", ("join", first_alias))
        for a in out:
            a.base = base
        return base, out

    def lift(self, fn, args: list[S | A]) -> S | A:
        arr_idx = [i for i, a in enumerate(args) if isinstance(a, A)]
        if not arr_idx:
            return fn(*args)
        self._array_depth += 1
        try:
            return self._lift_arrays(fn, args, arr_idx)
        finally:
            self._array_depth -= 1

    def _lift_arrays(self, fn, args: list[S | A], arr_idx: list[int]) -> A:
        arrays = [args[i] for i in arr_idx]
        if all(a.inline is not None for a in arrays):
            h = max(len(a.inline) for a in arrays)
            w = max(a.ncols for a in arrays)
            rows = []
            for r in range(h):
                row = []
                for c in range(w):
                    call_args = []
                    for a in args:
                        if isinstance(a, A):
                            rr = a.inline[r if len(a.inline) > 1 else 0]
                            call_args.append(rr[c if len(rr) > 1 else 0] if rr else lit_sql(None))
                        else:
                            call_args.append(a)
                    row.append(fn(*call_args))
                rows.append(row)
            return A(None, inline=rows)
        base, aligned = self.align(arrays)
        width = max(a.ncols for a in aligned)
        cols = []
        for c in range(width):
            call_args = list(args)
            for i, a in zip(arr_idx, aligned, strict=True):
                call_args[i] = a.cols[c if a.ncols > 1 else 0]
            cols.append(fn(*call_args))
        header = None
        if all(a.header is not None for a in aligned):
            header = []
            for c in range(width):
                call_args = list(args)
                for i, a in zip(arr_idx, aligned, strict=True):
                    call_args[i] = a.header[c if len(a.header) > 1 else 0]
                header.append(fn(*call_args))
        return A(base, cols, header=header)

    # ------------------------------------------------------------ operators

    def binary(self, node: P.Binary) -> S | A:
        op = node.op
        left = self.comp(node.left)
        right = self.comp(node.right)
        if op in ("=", "<>") and self._smart_cmp:
            return self.lift(lambda a, b: self._smart_compare(a, b, op), [left, right])
        if op in _CMP:
            return self.lift(lambda a, b: compare(a, b, op), [left, right])
        if op == "&":
            return self.lift(lambda a, b: S(f"({txt0(a)} || {txt0(b)})", STR), [left, right])
        return self.lift(lambda a, b: arith(op, a, b), [left, right])

    def _smart_compare(self, a: S, b: S, op: str) -> S:
        plain = compare(a, b, "=")
        if is_empty_str(a) or is_empty_str(b) or a.typ == NULL or b.typ == NULL:
            return compare(a, b, op)
        if self._smart_cmp == "exact":
            e = f"coalesce({exact_eq(a, b)}, FALSE)"
            return S(e if op == "=" else f"(NOT {e})", BOOL)
        self._smart_hits += 1
        e = f"(coalesce({plain.sql}, FALSE) OR coalesce({smart_eq(a, b)}, FALSE))"
        return S(e if op == "=" else f"(NOT {e})", BOOL)

    # ----------------------------------------------------------- reductions

    def values_of(self, v: S | A) -> list[tuple[str | None, list[S], bool]]:
        """Return reduction sources: (from_where_sql or None for inline, exprs, header)."""
        if isinstance(v, S):
            return [(None, [v], False)]
        if v.inline is not None:
            return [(None, [x for row in v.inline for x in row], False)]
        b = v.base
        where = " AND ".join(b.where) or "TRUE"
        return [(f"FROM {b.frm} WHERE {where}", v.cols, v.header is not None)]

    def agg(self, args: list[S | A], kind: str) -> S:
        """Numeric aggregate across all arguments (ranges ignore text, like Sheets)."""
        parts_sum, parts_cnt = [], []
        mins, maxs = [], []
        for a in args:
            for frm, exprs, _ in self.values_of(a):
                if frm is None:
                    for e in exprs:
                        explicit = isinstance(a, S)
                        n = num(e) if explicit else num_nullable(e)
                        parts_sum.append(f"coalesce({n}, 0)")
                        parts_cnt.append(f"(CASE WHEN {n} IS NULL THEN 0 ELSE 1 END)")
                        mins.append(n)
                        maxs.append(n)
                else:
                    for e in exprs:
                        n = num_nullable(e) if e.typ != NUM else e.sql
                        if e.typ == BOOL:
                            n = "CAST(NULL AS DOUBLE)"  # booleans in ranges are ignored
                        parts_sum.append(f"coalesce((SELECT sum({n}) {frm}), 0)")
                        parts_cnt.append(f"(SELECT count({n}) {frm})")
                        mins.append(f"(SELECT min({n}) {frm})")
                        maxs.append(f"(SELECT max({n}) {frm})")
        if not parts_sum:
            return lit_sql(0)
        if kind == "sum":
            return S("(" + " + ".join(parts_sum) + ")", NUM)
        if kind == "count":
            return S("(" + " + ".join(parts_cnt) + ")", NUM)
        if kind == "avg":
            return S(f"(({' + '.join(parts_sum)}) / NULLIF({' + '.join(parts_cnt)}, 0))", NUM)
        if kind == "min":
            return S(f"coalesce(least({', '.join(mins)}), 0)", NUM)
        if kind == "max":
            return S(f"coalesce(greatest({', '.join(maxs)}), 0)", NUM)
        raise FormulaError("#ERROR!", kind)

    def window_agg(self, a: A, kind: str) -> S | None:
        """SUM($B$2:B2)-style running windows in row mode → window functions."""
        if self.mode != "row" or a.src is None or a.base is None:
            return None
        src = a.src
        if src.sheet is not self.sheet or len(a.cols) != 1:
            return None
        lo, hi = src.rid1, src.rid2

        def rel_off(expr):
            m = re.match(r"^\(t\.__rid \+ \((-?\d+)\)\)$", str(expr))
            return int(m.group(1)) if m else None

        frames = []
        cond = []
        lo_off = rel_off(lo)
        hi_off = rel_off(hi) if hi is not None else None
        if lo_off is not None:
            frames.append(f"{-lo_off} PRECEDING" if lo_off <= 0 else f"{lo_off} FOLLOWING")
        else:
            frames.append("UNBOUNDED PRECEDING")
            cond.append(f"__rid >= {lo}")
        if hi is None:
            frames.append("UNBOUNDED FOLLOWING")
        elif hi_off is not None:
            frames.append(f"{-hi_off} PRECEDING" if hi_off < 0 else f"{hi_off} FOLLOWING")
        else:
            frames.append("UNBOUNDED FOLLOWING")
            cond.append(f"__rid <= {hi}")
        col = a.cols[0]
        bare = S(col.sql.replace(f"{a.base.alias}.", "t."), col.typ)
        n = num_nullable(bare)
        if cond:
            c = " AND ".join(x.replace("__rid", "t.__rid") for x in cond)
            n = f"(CASE WHEN {c} THEN {n} END)"
        fn = {"sum": "sum", "count": "count", "avg": "avg", "min": "min", "max": "max"}[kind]
        win = f"OVER (ORDER BY t.__rid ROWS BETWEEN {frames[0]} AND {frames[1]})"
        self.uses_window = True
        expr = f"{fn}({n}) {win}"
        if kind in ("sum", "min", "max"):
            expr = f"coalesce({expr}, 0)"
        return S(expr, NUM)

    def counta(self, args: list[S | A], blank: bool = False) -> S:
        parts = []
        for a in args:
            if isinstance(a, A) and a.header is not None:
                for h in a.header:
                    nonblank = 0 if (h.is_lit and (h.lit is None or h.lit == "")) else 1
                    parts.append(str(1 - nonblank if blank else nonblank))
            for frm, exprs, _ in self.values_of(a):
                for e in exprs:
                    test = f"({e.sql} IS NULL OR {txt(e)} = '')" if e.typ == STR else f"({e.sql} IS NULL)"
                    if not blank:
                        test = f"(NOT {test})"
                    if frm is None:
                        parts.append(f"(CASE WHEN {test} THEN 1 ELSE 0 END)")
                    else:
                        parts.append(f"(SELECT count(*) FILTER (WHERE {test}) {frm})")
        return S("(" + (" + ".join(parts) or "0") + ")", NUM)

    def stat(self, args: list[S | A], fn: str) -> S:
        """Statistical aggregates that need all values in one bag."""
        selects = []
        for a in args:
            for frm, exprs, _ in self.values_of(a):
                for e in exprs:
                    n = num_nullable(e)
                    selects.append(f"SELECT {n} AS v {frm}" if frm else f"SELECT {n} AS v")
        if not selects:
            return lit_sql(None)
        union = " UNION ALL ".join(selects)
        return S(f"(SELECT {fn} FROM ({union}) AS _u)", NUM)

    # ------------------------------------------------------------- criteria

    def criterion(self, node) -> tuple[str, Any]:
        """Return (kind, payload) describing a COUNTIF-style criterion."""
        if isinstance(node, P.Binary) and node.op == "&" and isinstance(node.left, P.Str):
            op = node.left.value.strip()
            if op in ("<", ">", "<=", ">=", "=", "<>"):
                return "op", (op, self.scalar(node.right))
        v = self.comp(node)
        if isinstance(v, A):
            v = self.to_scalar(v)
        if v.is_lit and isinstance(v.lit, str):
            s = v.lit
            m = re.match(r"^(<=|>=|<>|<|>|=)?(.*)$", s, re.S)
            op = m.group(1) or "="
            rest = m.group(2)
            if _NUMERIC_RE.match(rest):
                return "op", (op, lit_sql(float(rest)))
            if rest.upper() in ("TRUE", "FALSE") and op in ("=", "<>"):
                return "op", (op, lit_sql(rest.upper() == "TRUE"))
            if op in ("=", "<>") and ("*" in rest or "?" in rest):
                return "like", (op, like_pattern(rest))
            return "op", (op, lit_sql(rest))
        return "op", ("=", v)

    def crit_sql(self, crit, x: S) -> str:
        kind, payload = crit
        if kind == "like":
            op, pat = payload
            t = f"({txt0(x)} ILIKE {sql_str(pat)} ESCAPE '\\')"
            return t if op == "=" else f"(NOT {t})"
        op, val = payload
        if op in ("=", "<>") and not (val.is_lit and val.lit == "") and val.typ != NULL and self.smart:
            e = smart_eq(x, val)
            return f"coalesce({e}, FALSE)" if op == "=" else f"(NOT coalesce({e}, FALSE))"
        if op in ("=", "<>") and not (val.is_lit and val.lit == "") and val.typ != NULL:
            e = eq_sql(x, val)
            if val.typ == STR and x.typ != STR and not (val.is_lit and _NUMERIC_RE.match(str(val.lit))):
                e = f"(lower({txt(x)}) = lower({val.sql}))"
            return f"coalesce({e}, FALSE)" if op == "=" else f"(NOT coalesce({e}, FALSE))"
        if val.typ == NUM and x.typ == STR:
            # numeric criterion on text column: only numeric-looking cells qualify
            return f"coalesce(TRY_CAST({x.sql} AS DOUBLE) {op} {val.sql}, FALSE)"
        if val.typ == NUM and x.typ == NUM:
            return f"coalesce({x.sql} {op} {val.sql}, FALSE)"
        return f"coalesce({compare(x, val, op).sql}, FALSE)"

    def ifs_agg(self, ranges: list[A], crits: list, value: A | None, fn: str) -> S:
        """COUNTIFS / SUMIFS / AVERAGEIFS / MAXIFS / MINIFS."""
        arrays = list(ranges) + ([value] if value is not None else [])
        arrays = [a if isinstance(a, A) else A(None, inline=[[a]]) for a in arrays]
        base, aligned = self.align(arrays)
        conds = []
        for a, c in zip(aligned[: len(ranges)], crits, strict=True):
            conds.append(self.crit_sql(c, a.cols[0] if a.inline is None else a.inline[0][0]))
        if base is None:
            # all inline
            cond = " AND ".join(conds) or "TRUE"
            v = aligned[-1].inline[0][0] if value is not None else None
            if fn == "count":
                return S(f"(CASE WHEN {cond} THEN 1 ELSE 0 END)", NUM)
            return S(f"(CASE WHEN {cond} THEN {num(v)} ELSE 0 END)", NUM)
        where = " AND ".join(base.where + conds) or "TRUE"
        frm = f"FROM {base.frm} WHERE {where}"
        # header row participates in COUNTIF only
        header_extra = ""
        if fn == "count" and all(a.header is not None for a in aligned[: len(ranges)]):
            hconds = [self.crit_sql(c, a.header[0]) for a, c in zip(aligned, crits, strict=False)]
            header_extra = f" + (CASE WHEN {' AND '.join(hconds)} THEN 1 ELSE 0 END)"
        if fn == "count":
            return S(f"((SELECT count(*) {frm}){header_extra})", NUM)
        v = num_nullable(aligned[-1].cols[0])
        if fn == "sum":
            return S(f"coalesce((SELECT sum({v}) {frm}), 0)", NUM)
        if fn == "avg":
            return S(f"(SELECT avg({v}) {frm})", NUM)
        if fn in ("max", "min"):
            return S(f"coalesce((SELECT {fn}({v}) {frm}), 0)", NUM)
        raise FormulaError("#ERROR!", fn)

    # ---------------------------------------------------------------- calls

    def call(self, node: P.Call) -> S | A:
        name = node.name
        if name.startswith("_XLFN."):
            name = name[6:]
        impl = getattr(self, "fn_" + name.replace(".", "_"), None)
        if impl is not None:
            return impl(node.args)
        custom = (getattr(self.catalog, "custom_functions", None) or {}).get(name)
        if custom is not None:
            return self.custom_call(name, custom, node.args)
        spec = SCALAR_FUNCS.get(name)
        if spec is not None:
            lo, hi, fn = spec
            if len(node.args) < lo or (hi is not None and len(node.args) > hi):
                raise FormulaError("#N/A", f"Wrong number of arguments to {name}")
            vals = [self.comp(a) for a in node.args]
            if name in VOLATILE:
                self.volatile = True
            return self.lift(fn, vals)
        raise FormulaError("#NAME?", f"Unknown function: {name}")

    def custom_call(self, name: str, meta: dict, args) -> S | A:
        """A Python function registered with @ps.function (a DuckDB UDF)."""
        params = meta["params"]
        if len(args) != len(params):
            raise FormulaError("#N/A", f"{name} expects {len(params)} argument(s)")
        conv = {NUM: num_nullable, STR: txt, BOOL: boo, DATE: as_date, TS: as_ts}
        vals = [self.comp(a) for a in args]

        def f(*xs):
            parts = [conv[t](x) for x, t in zip(xs, params, strict=True)]
            return S(f"{meta['sql']}({', '.join(parts)})", meta["ret"])

        return self.lift(f, vals)

    def _args(self, args, lo, hi=None, name=""):
        if len(args) < lo or (hi is not None and len(args) > hi):
            raise FormulaError("#N/A", f"Wrong number of arguments to {name}")

    # -- logical ---------------------------------------------------------

    def fn_IF(self, args):
        self._args(args, 2, 3, "IF")
        c = self.comp(args[0])
        a = self.comp(args[1]) if not isinstance(args[1], P.Blank) else lit_sql(0)
        b = (
            self.comp(args[2])
            if len(args) > 2 and not isinstance(args[2], P.Blank)
            else (lit_sql(False) if len(args) < 3 else lit_sql(0))
        )

        def f(c, a, b):
            t, (sa, sb) = unify([blankish(a), blankish(b)])
            return S(f"(CASE WHEN {boo(c)} THEN {sa} ELSE {sb} END)", t)

        return self.lift(f, [c, a, b])

    def fn_IFS(self, args):
        if len(args) < 2 or len(args) % 2:
            raise FormulaError("#N/A", "IFS needs condition/value pairs")
        vals = [self.comp(a) for a in args]

        def f(*xs):
            conds = xs[0::2]
            res = [blankish(v) for v in xs[1::2]]
            t, sqls = unify(res)
            whens = " ".join(f"WHEN {boo(c)} THEN {s}" for c, s in zip(conds, sqls, strict=True))
            return S(f"(CASE {whens} END)", t)

        return self.lift(f, vals)

    def fn_SWITCH(self, args):
        if len(args) < 3:
            raise FormulaError("#N/A", "SWITCH needs at least 3 arguments")
        vals = [self.comp(a) for a in args]

        def f(expr, *rest):
            pairs = list(zip(rest[0::2], rest[1::2], strict=False))
            default = rest[-1] if len(rest) % 2 else lit_sql(None)
            t, sqls = unify([blankish(v) for _, v in pairs] + [blankish(default)])
            whens = " ".join(
                f"WHEN coalesce({eq_sql(expr, k)}, FALSE) THEN {s}"
                for (k, _), s in zip(pairs, sqls, strict=False)
            )
            return S(f"(CASE {whens} ELSE {sqls[-1]} END)", t)

        return self.lift(f, vals)

    def fn_IFERROR(self, args):
        self._args(args, 1, 2, "IFERROR")
        a = self.comp(args[0])
        b = self.comp(args[1]) if len(args) > 1 else lit_sql("")

        def f(a, b):
            t, (sa, sb) = unify([a, blankish(b)])
            return S(f"coalesce({sa}, {sb})", t)

        return self.lift(f, [a, b])

    fn_IFNA = fn_IFERROR

    def fn_AND(self, args):
        return self._bool_reduce(args, "AND")

    def fn_OR(self, args):
        return self._bool_reduce(args, "OR")

    def fn_XOR(self, args):
        vals = [self.scalar(a) for a in args]
        expr = " + ".join(f"CAST({boo(v)} AS INTEGER)" for v in vals)
        return S(f"(({expr}) % 2 = 1)", BOOL)

    def _bool_reduce(self, args, op):
        parts = []
        agg = "bool_and" if op == "AND" else "bool_or"
        for a in args:
            v = self.comp(a)
            for frm, exprs, _ in self.values_of(v):
                for e in exprs:
                    if frm is None:
                        parts.append(boo(e))
                    else:
                        parts.append(
                            f"coalesce((SELECT {agg}({boo(e)}) {frm} AND {e.sql} IS NOT NULL), "
                            f"{'TRUE' if op == 'AND' else 'FALSE'})"
                        )
        if not parts:
            raise FormulaError("#N/A", f"{op} needs arguments")
        return S("(" + f" {op} ".join(parts) + ")", BOOL)

    def fn_NOT(self, args):
        self._args(args, 1, 1, "NOT")
        return self.lift(lambda a: S(f"(NOT {boo(a)})", BOOL), [self.comp(args[0])])

    def fn_TRUE(self, args):
        return lit_sql(True)

    def fn_FALSE(self, args):
        return lit_sql(False)

    # -- aggregates --------------------------------------------------------

    def _agg_fn(self, args, kind):
        vals = [self.comp(a) for a in args]
        if len(vals) == 1 and isinstance(vals[0], A):
            w = self.window_agg(vals[0], kind)
            if w is not None:
                return w
        return self.agg(vals, kind)

    def fn_SUM(self, args):
        return self._agg_fn(args, "sum")

    def fn_COUNT(self, args):
        return self._agg_fn(args, "count")

    def fn_AVERAGE(self, args):
        return self._agg_fn(args, "avg")

    def fn_MIN(self, args):
        return self._agg_fn(args, "min")

    def fn_MAX(self, args):
        return self._agg_fn(args, "max")

    def fn_COUNTA(self, args):
        return self.counta([self.comp(a) for a in args])

    def fn_COUNTBLANK(self, args):
        return self.counta([self.comp(a) for a in args], blank=True)

    def fn_PRODUCT(self, args):
        return self.stat([self.comp(a) for a in args], "product(v)")

    def fn_MEDIAN(self, args):
        return self.stat([self.comp(a) for a in args], "median(v)")

    def fn_MODE(self, args):
        return self.stat([self.comp(a) for a in args], "mode(v)")

    fn_MODE_SNGL = fn_MODE

    def fn_STDEV(self, args):
        return self.stat([self.comp(a) for a in args], "stddev_samp(v)")

    fn_STDEV_S = fn_STDEV

    def fn_STDEVP(self, args):
        return self.stat([self.comp(a) for a in args], "stddev_pop(v)")

    fn_STDEV_P = fn_STDEVP

    def fn_VAR(self, args):
        return self.stat([self.comp(a) for a in args], "var_samp(v)")

    fn_VAR_S = fn_VAR

    def fn_VARP(self, args):
        return self.stat([self.comp(a) for a in args], "var_pop(v)")

    fn_VAR_P = fn_VARP

    def fn_COUNTUNIQUE(self, args):
        selects = []
        for a in args:
            v = self.comp(a)
            for frm, exprs, _ in self.values_of(v):
                for e in exprs:
                    selects.append(f"SELECT {txt(e)} AS v {frm}" if frm else f"SELECT {txt(e)} AS v")
        union = " UNION ALL ".join(selects)
        return S(f"(SELECT count(DISTINCT v) FROM ({union}) AS _u WHERE v <> '')", NUM)

    def _k_stat(self, args, desc: bool):
        self._args(args, 2, 2, "LARGE/SMALL")
        arr = self.comp(args[0])
        k = self.scalar(args[1])
        selects = []
        for frm, exprs, _ in self.values_of(arr):
            for e in exprs:
                n = num_nullable(e)
                selects.append(f"SELECT {n} AS v {frm}" if frm else f"SELECT {n} AS v")
        union = " UNION ALL ".join(selects)
        order = "DESC" if desc else "ASC"
        return S(
            f"(SELECT v FROM ({union}) AS _u WHERE v IS NOT NULL ORDER BY v {order} "
            f"LIMIT 1 OFFSET CAST({num(k)} AS BIGINT) - 1)",
            NUM,
        )

    def fn_LARGE(self, args):
        return self._k_stat(args, True)

    def fn_SMALL(self, args):
        return self._k_stat(args, False)

    def fn_PERCENTILE(self, args):
        self._args(args, 2, 2, "PERCENTILE")
        k = self.scalar(args[1])
        return self.stat([self.comp(args[0])], f"quantile_cont(v, {num(k)})")

    fn_PERCENTILE_INC = fn_PERCENTILE

    def fn_QUARTILE(self, args):
        self._args(args, 2, 2, "QUARTILE")
        k = self.scalar(args[1])
        return self.stat([self.comp(args[0])], f"quantile_cont(v, {num(k)} / 4.0)")

    def fn_CORREL(self, args):
        self._args(args, 2, 2, "CORREL")
        a, b = self.comp(args[0]), self.comp(args[1])
        base, (x, y) = self.align([a, b])
        where = " AND ".join(base.where) or "TRUE"
        return S(
            f"(SELECT corr({num_nullable(x.cols[0])}, {num_nullable(y.cols[0])}) "
            f"FROM {base.frm} WHERE {where})",
            NUM,
        )

    def fn_SUMPRODUCT(self, args):
        vals = [self.comp(a) for a in args]
        arrays = [v if isinstance(v, A) else A(None, inline=[[v]]) for v in vals]
        base, aligned = self.align(arrays)
        if base is None:
            terms = []
            for r in range(len(aligned[0].inline)):
                for c in range(aligned[0].ncols):
                    terms.append(" * ".join(num(a.inline[r][c]) for a in aligned))
            return S("(" + " + ".join(terms) + ")", NUM)
        where = " AND ".join(base.where) or "TRUE"
        terms = []
        for c in range(aligned[0].ncols):
            terms.append(" * ".join(num(a.cols[c if a.ncols > 1 else 0]) for a in aligned))
        return S(f"coalesce((SELECT sum({' + '.join(terms)}) FROM {base.frm} WHERE {where}), 0)", NUM)

    def fn_COUNTIF(self, args):
        self._args(args, 2, 2, "COUNTIF")
        rng = self.comp(args[0])
        return self.ifs_agg([rng], [self.criterion(args[1])], None, "count")

    def fn_COUNTIFS(self, args):
        if len(args) < 2 or len(args) % 2:
            raise FormulaError("#N/A", "COUNTIFS needs range/criterion pairs")
        ranges = [self.comp(a) for a in args[0::2]]
        crits = [self.criterion(a) for a in args[1::2]]
        return self.ifs_agg(ranges, crits, None, "count")

    def fn_SUMIF(self, args):
        self._args(args, 2, 3, "SUMIF")
        rng = self.comp(args[0])
        val = self.comp(args[2]) if len(args) > 2 else rng
        return self.ifs_agg([rng], [self.criterion(args[1])], val, "sum")

    def fn_AVERAGEIF(self, args):
        self._args(args, 2, 3, "AVERAGEIF")
        rng = self.comp(args[0])
        val = self.comp(args[2]) if len(args) > 2 else rng
        return self.ifs_agg([rng], [self.criterion(args[1])], val, "avg")

    def _ifs_value_first(self, args, fn):
        if len(args) < 3 or len(args) % 2 == 0:
            raise FormulaError("#N/A", "needs value range and range/criterion pairs")
        val = self.comp(args[0])
        ranges = [self.comp(a) for a in args[1::2]]
        crits = [self.criterion(a) for a in args[2::2]]
        return self.ifs_agg(ranges, crits, val, fn)

    def fn_SUMIFS(self, args):
        return self._ifs_value_first(args, "sum")

    def fn_AVERAGEIFS(self, args):
        return self._ifs_value_first(args, "avg")

    def fn_MAXIFS(self, args):
        return self._ifs_value_first(args, "max")

    def fn_MINIFS(self, args):
        return self._ifs_value_first(args, "min")

    # -- text that reduces over ranges -----------------------------------

    def fn_CONCATENATE(self, args):
        parts = []
        for a in args:
            v = self.comp(a)
            for frm, exprs, _ in self.values_of(v):
                for e in exprs:
                    if frm is None:
                        parts.append(txt0(e))
                    else:
                        b = v.base
                        parts.append(
                            f"coalesce((SELECT string_agg({txt(e)}, '' ORDER BY {b.order}) {frm}), '')"
                        )
        return S("(" + (" || ".join(parts) or "''") + ")", STR)

    def fn_CONCAT(self, args):
        self._args(args, 2, 2, "CONCAT")
        return self.lift(
            lambda a, b: S(f"({txt0(a)} || {txt0(b)})", STR), [self.comp(args[0]), self.comp(args[1])]
        )

    def fn_TEXTJOIN(self, args):
        self._args(args, 3, None, "TEXTJOIN")
        delim = self.scalar(args[0])
        ignore = self.scalar(args[1])
        selects = []
        k = 0
        for a in args[2:]:
            v = self.comp(a)
            for frm, exprs, _ in self.values_of(v):
                for e in exprs:
                    k += 1
                    if frm is None:
                        selects.append(f"SELECT {txt(e)} AS v, {k} AS g, 0 AS o")
                    else:
                        selects.append(f"SELECT {txt(e)} AS v, {k} AS g, {v.base.order} AS o {frm}")
        union = " UNION ALL ".join(selects)
        return S(
            f"(SELECT string_agg(v, {txt0(delim)} ORDER BY g, o) FROM ({union}) AS _u "
            f"WHERE NOT ({boo(ignore)} AND coalesce(v, '') = ''))",
            STR,
        )

    def fn_JOIN(self, args):
        self._args(args, 2, None, "JOIN")
        from .parser import Bool as PBool

        return self.fn_TEXTJOIN((args[0], PBool(False), *args[1:]))

    # -- lookups ------------------------------------------------------------

    def _rel(self, v: S | A) -> A:
        if isinstance(v, S):
            return self.to_relation(A(None, inline=[[v]]))
        return self.to_relation(v)

    def fn_VLOOKUP(self, args):
        self._args(args, 3, 4, "VLOOKUP")
        key = self.comp(args[0])
        table = self._rel(self.comp(args[1]))
        idx = self.const_int(args[2])
        is_sorted = True
        if len(args) > 3:
            s = self.scalar(args[3])
            is_sorted = bool(self.const_value(S(boo(s), BOOL)))
        if idx is None or idx < 1 or idx > table.ncols:
            raise FormulaError("#REF!", "VLOOKUP column index out of range")
        kcol, vcol = table.cols[0], table.cols[idx - 1]
        return self.lift(lambda k: self._lookup(table, kcol, vcol, k, 0 if not is_sorted else 1), [key])

    def fn_HLOOKUP(self, args):
        self._args(args, 3, 4, "HLOOKUP")
        key = self.scalar(args[0])
        table = self.comp(args[1])
        idx = self.const_int(args[2])
        if not isinstance(table, A):
            raise FormulaError("#N/A", "HLOOKUP needs a range")
        rows = self.materialize_inline(table)
        if idx is None or idx < 1 or idx > len(rows):
            raise FormulaError("#REF!", "HLOOKUP row index out of range")
        vals = [rows[idx - 1][j] for j in range(len(rows[0]))]
        t, sqls = unify(vals)
        return S(f"(CASE {self._tiered_whens(rows[0], key, sqls)} END)", t)

    def _tiered_whens(self, cells: list[S], key: S, results: list[str]) -> str:
        """CASE branches for a small inline lookup: exact matches first, then format-only ones."""
        whens = [
            f"WHEN coalesce({exact_eq(c, key)}, FALSE) THEN {r}" for c, r in zip(cells, results, strict=True)
        ]
        if self.smart:
            whens += [
                f"WHEN coalesce({smart_eq(c, key)}, FALSE) THEN {r}"
                for c, r in zip(cells, results, strict=True)
            ]
        return " ".join(whens) or "WHEN FALSE THEN NULL"

    def _lookup(self, table: A, kcol: S, vcol: S, key: S, mode: int, last: bool = False) -> S:
        b = table.base
        where = list(b.where)
        if mode == 0:
            fn = "arg_max" if last else "arg_min"
            if key.is_lit and isinstance(key.lit, str) and ("*" in key.lit or "?" in key.lit):
                where.append(f"{txt0(kcol)} ILIKE {sql_str(like_pattern(key.lit))} ESCAPE '\\'")
                w = " AND ".join(where)
                return S(f"(SELECT {fn}({vcol.sql}, {b.order}) FROM {b.frm} WHERE {w})", vcol.typ)
            key = self._constify(key)
            exact = exact_eq(kcol, key)
            near = " AND ".join(where + [smart_eq(kcol, key)])
            self._probe(key, f"(SELECT min(CASE WHEN {exact} THEN 0 ELSE 1 END) FROM {b.frm} WHERE {near})")
            if not self.smart:
                w = " AND ".join(where + [exact])
                return S(f"(SELECT {fn}({vcol.sql}, {b.order}) FROM {b.frm} WHERE {w})", vcol.typ)
            # exact matches win over format-only ones, then the usual first/last row
            prio = f"(CASE WHEN {exact} THEN {0 if not last else 1} ELSE {1 if not last else 0} END)"
            return S(
                f"(SELECT {fn}({vcol.sql}, {prio} * 1e15 + {b.order}) FROM {b.frm} WHERE {near})", vcol.typ
            )
        # approximate: greatest key <= lookup value (mode 1) or smallest >= (mode -1)
        cmp = "<=" if mode == 1 else ">="
        if kcol.typ == STR and key.typ == STR:
            where.append(f"lower({kcol.sql}) {cmp} lower({key.sql})")
            korder = f"lower({kcol.sql})"
        elif kcol.typ in (DATE, TS) and key.typ in (DATE, TS):
            where.append(f"{as_ts(kcol)} {cmp} {as_ts(key)}")
            korder = as_ts(kcol)
        else:
            where.append(f"{num_nullable(kcol)} {cmp} {num(key)}")
            korder = num_nullable(kcol)
        fn = "arg_max" if mode == 1 else "arg_min"
        w = " AND ".join(where)
        return S(f"(SELECT {fn}({vcol.sql}, {korder}) FROM {b.frm} WHERE {w})", vcol.typ)

    def fn_XLOOKUP(self, args):
        self._args(args, 3, 6, "XLOOKUP")
        key = self.comp(args[0])
        look = self.comp(args[1])
        ret = self.comp(args[2])
        not_found = self.comp(args[3]) if len(args) > 3 and not isinstance(args[3], P.Blank) else None
        match_mode = self.const_int(args[4], 0) if len(args) > 4 else 0
        search_mode = self.const_int(args[5], 1) if len(args) > 5 else 1
        if not isinstance(look, A) or not isinstance(ret, A):
            raise FormulaError("#VALUE!", "XLOOKUP needs ranges")
        base, (lk, rt) = self.align([self._rel(look), self._rel(ret)])
        table = A(base, lk.cols + rt.cols)
        kcol = lk.cols[0]
        mode = {0: 0, 2: 0, -1: 1, 1: -1}.get(match_mode, 0)

        def one(k):
            outs = [self._lookup(table, kcol, rc, k, mode, last=search_mode < 0) for rc in rt.cols]
            if not_found is not None:
                nf = self.to_scalar(not_found)
                fixed = []
                for o in outs:
                    t, (a, b2) = unify([o, nf])
                    fixed.append(S(f"coalesce({a}, {b2})", t))
                outs = fixed
            return outs

        if isinstance(key, S):
            outs = one(key)
            return outs[0] if len(outs) == 1 else A(None, inline=[outs])
        return self.lift(lambda k: one(k)[0], [key])

    def fn_MATCH(self, args):
        self._args(args, 2, 3, "MATCH")
        key = self.comp(args[0])
        rng = self.comp(args[1])
        mtype = self.const_int(args[2], 1) if len(args) > 2 else 1
        if isinstance(rng, S):
            rng = A(None, inline=[[rng]])
        return self.lift(lambda k: self._match(rng, k, mtype), [key])

    def _match(self, rng: A, key: S, mtype: int) -> S:
        if rng.inline is not None:
            cells = [x for row in rng.inline for x in row]
            return S(
                f"(CASE {self._tiered_whens(cells, key, [str(i + 1) for i in range(len(cells))])} END)", NUM
            )
        col = rng.cols[0]
        b = rng.base
        src = rng.src
        h = 1 if rng.header is not None else 0
        if src is not None and src.sheet is not None and b.key == (src.sheet.id, src.rid1, src.rid2):
            pos = f"({b.alias}.__rid - {src.rid1} + 1 + {h})"
        else:
            frm, alias, cols = self.numbered(rng)
            b = Base(frm, alias, [], f"{alias}.__p", ("num", alias))
            col = cols[0]
            pos = f"({alias}.__p + {h})"
        where = list(b.where)
        agg = "min"
        data = None
        if mtype == 0:
            if key.is_lit and isinstance(key.lit, str) and ("*" in key.lit or "?" in key.lit):
                where.append(f"{txt0(col)} ILIKE {sql_str(like_pattern(key.lit))} ESCAPE '\\'")
            else:
                key = self._constify(key)
                exact = exact_eq(col, key)
                near = " AND ".join(where + [smart_eq(col, key)])
                self._probe(
                    key, f"(SELECT min(CASE WHEN {exact} THEN 0 ELSE 1 END) FROM {b.frm} WHERE {near})"
                )
                if self.smart:
                    data = (
                        f"(SELECT arg_min({pos}, (CASE WHEN {exact} THEN 0 ELSE 1 END) * 1e15 + {pos}) "
                        f"FROM {b.frm} WHERE {near})"
                    )
                else:
                    where.append(exact)
        else:
            cmp = "<=" if mtype > 0 else ">="
            where.append(compare(col, key, cmp).sql)
            agg = "max"
        if data is None:
            w = " AND ".join(where)
            data = f"(SELECT {agg}({pos}) FROM {b.frm} WHERE {w})"
        if h and mtype == 0:
            hk = rng.header[0]
            heq = f"({exact_eq(hk, key)} OR {smart_eq(hk, key)})" if self.smart else exact_eq(hk, key)
            return S(f"(CASE WHEN coalesce({heq}, FALSE) THEN 1 ELSE {data} END)", NUM)
        return S(data, NUM)

    def fn_INDEX(self, args):
        self._args(args, 1, 3, "INDEX")
        rng = self.comp(args[0])
        if isinstance(rng, S):
            return rng
        rnode = args[1] if len(args) > 1 else None
        cnode = args[2] if len(args) > 2 else None
        r_is_blank = rnode is None or isinstance(rnode, P.Blank)
        c_is_blank = cnode is None or isinstance(cnode, P.Blank)
        rv = None if r_is_blank else self.comp(rnode)
        cconst = 1 if c_is_blank else None
        if not c_is_blank:
            cv = self.scalar(cnode)
            if cv.is_lit:
                cconst = int(cv.lit)
            else:
                cconst = None
        if rng.ncols == 1 and cconst is None:
            cconst = 1
        if cconst is None:
            raise FormulaError("#VALUE!", "INDEX column must be a constant")
        if cconst == 0:
            col_sel = list(range(rng.ncols))
        else:
            if cconst < 1 or cconst > rng.ncols:
                raise FormulaError("#REF!", "INDEX column out of range")
            col_sel = [cconst - 1]
        if rng.inline is not None:
            if rv is None or (isinstance(rv, S) and rv.is_lit and rv.lit == 0):
                return A(None, inline=[[row[c] for c in col_sel] for row in rng.inline])
            rconst = self.const_int(rnode)
            if rconst is None or rconst < 1 or rconst > len(rng.inline):
                raise FormulaError("#REF!", "INDEX row out of range")
            row = rng.inline[rconst - 1]
            if len(col_sel) == 1:
                return row[col_sel[0]]
            return A(None, inline=[[row[c] for c in col_sel]])
        if rv is None or (isinstance(rv, S) and rv.is_lit and rv.lit == 0):
            return A(
                rng.base,
                [rng.cols[c] for c in col_sel],
                header=[rng.header[c] for c in col_sel] if rng.header else None,
            )
        h = 1 if rng.header is not None else 0

        def pick(r: S) -> S:
            if r.is_lit and h and int(r.lit) == 1:
                hs = [rng.header[c] for c in col_sel]
                return hs[0]
            src = rng.src
            c = col_sel[0]
            b = rng.base
            if src is not None and b.key == (src.sheet.id, src.rid1, src.rid2):
                x = self.alias("x")
                colx = self.col_expr(src.sheet, src.cols[c], x)
                return S(
                    f'(SELECT {colx.sql} FROM "{src.sheet.table}" AS {x} WHERE {x}.__rid = '
                    f"{src.rid1} + CAST({num(r)} AS BIGINT) - 1 - {h})",
                    colx.typ,
                )
            frm, alias, cols = self.numbered(rng)
            return S(
                f"(SELECT {cols[c].sql} FROM {frm} WHERE {alias}.__p = CAST({num(r)} AS BIGINT) - {h})",
                cols[c].typ,
            )

        if len(col_sel) > 1:
            if not isinstance(rv, S):
                raise FormulaError("#VALUE!", "INDEX row must be a single value")
            outs = []
            for c in col_sel:
                sub = A(rng.base, [rng.cols[c]], header=[rng.header[c]] if rng.header else None, src=rng.src)
                outs.append(self._index_one(sub, rv, h, [0]))
            return A(None, inline=[outs])
        return self.lift(pick, [rv])

    def _index_one(self, rng, r, h, col_sel):
        frm, alias, cols = self.numbered(rng)
        c = col_sel[0]
        return S(
            f"(SELECT {cols[c].sql} FROM {frm} WHERE {alias}.__p = CAST({num(r)} AS BIGINT) - {h})",
            cols[c].typ,
        )

    def fn_CHOOSE(self, args):
        self._args(args, 2, None, "CHOOSE")
        vals = [self.comp(a) for a in args]

        def f(i, *opts):
            t, sqls = unify(list(opts))
            whens = " ".join(f"WHEN {k + 1} THEN {s}" for k, s in enumerate(sqls))
            return S(f"(CASE CAST({num(i)} AS BIGINT) {whens} END)", t)

        return self.lift(f, vals)

    # -- array functions ------------------------------------------------------

    def fn_ARRAYFORMULA(self, args):
        self._args(args, 1, 1, "ARRAYFORMULA")
        return self.comp(args[0])

    def fn_FILTER(self, args):
        self._args(args, 2, None, "FILTER")
        if not self.smart:
            return self._filter(args)
        prev, hits = self._smart_cmp, self._smart_hits
        self._smart_cmp = "smart"
        try:
            res = self._filter(args)
        finally:
            self._smart_cmp = prev
        if self._smart_hits > hits and self.mode == "cell" and not self._array_depth and self.con is not None:
            # how many rows are in the result only because formats were normalised?
            try:
                self._smart_cmp = "exact"
                strict = self._filter(args)
                n_smart = self.query_scalar(self._count_rows(res))
                n_strict = self.query_scalar(self._count_rows(strict))
                if n_smart and n_strict is not None and n_smart > n_strict:
                    self.notes.append({"fn": "FILTER", "fuzzy": int(n_smart - n_strict)})
            except Exception:
                pass
            finally:
                self._smart_cmp = prev
        return res

    @staticmethod
    def _count_rows(a: A) -> str:
        b = a.base
        return f"SELECT count(*) FROM {b.frm} WHERE {' AND '.join(b.where) or 'TRUE'}"

    def _filter(self, args):
        data = self.comp(args[0])
        conds = [self.comp(a) for a in args[1:]]
        if not isinstance(data, A):
            data = A(None, inline=[[data]])
        arrays = [data] + [c if isinstance(c, A) else A(None, inline=[[c]]) for c in conds]
        arrays = [self.to_relation(a) for a in arrays]
        base, aligned = self.align(arrays)
        d = aligned[0]
        where = list(base.where)
        include_header = d.header is not None
        for c in aligned[1:]:
            where.append(boo(c.cols[0]))
            if c.header is None:
                include_header = False
        header = None
        if include_header and self.con is not None:
            hsql = " AND ".join(boo(c.header[0]) for c in aligned[1:])
            try:
                if self.query_scalar(f"SELECT coalesce({hsql}, FALSE)"):
                    header = d.header
            except Exception:
                header = None
        nb = Base(base.frm, base.alias, where, base.order, ("filter", base.key, tuple(where)))
        return A(nb, d.cols, header=header)

    def fn_SORT(self, args):
        self._args(args, 1, None, "SORT")
        data = self._rel(self.comp(args[0]))
        keys = []
        rest = list(args[1:])
        if not rest:
            rest = [P.Num(1), P.Bool(True)]
        if len(rest) % 2:
            rest.append(P.Bool(True))
        sort_arrays = []
        for i in range(0, len(rest), 2):
            k = self.comp(rest[i])
            asc = self.const_value(S(boo(self.scalar(rest[i + 1])), BOOL))
            if isinstance(k, A):
                sort_arrays.append((k, asc))
                keys.append(("arr", len(sort_arrays) - 1, asc))
            else:
                idx = int(self.const_value(S(num(k), NUM)))
                if idx < 1 or idx > data.ncols:
                    raise FormulaError("#VALUE!", "SORT column out of range")
                keys.append(("col", idx - 1, asc))
        arrays = [data] + [self._rel(a) for a, _ in sort_arrays]
        base, aligned = self.align(arrays)
        d = aligned[0]
        order = []
        for kind, idx, asc in keys:
            col = d.cols[idx] if kind == "col" else aligned[1 + idx].cols[0]
            expr = f"lower({col.sql})" if col.typ == STR else col.sql
            order.append(f"{expr} {'ASC' if asc else 'DESC'} NULLS LAST")
        order.append(base.order)
        alias = self.alias("s")
        where = " AND ".join(base.where) or "TRUE"
        sel = ", ".join(f"{c.sql} AS v{j}" for j, c in enumerate(d.cols))
        frm = (
            f"(SELECT {sel}, row_number() OVER (ORDER BY {', '.join(order)}) AS __o "
            f"FROM {base.frm} WHERE {where}) AS {alias}"
        )
        nb = Base(frm, alias, [], f"{alias}.__o", ("sort", alias))
        return A(nb, [S(f"{alias}.v{j}", c.typ) for j, c in enumerate(d.cols)])

    def fn_UNIQUE(self, args):
        self._args(args, 1, 3, "UNIQUE")
        data = self._rel(self.comp(args[0]))
        b = data.base
        alias = self.alias("u")
        where = " AND ".join(b.where) or "TRUE"
        sel = ", ".join(f"{c.sql} AS v{j}" for j, c in enumerate(data.cols))
        grp = ", ".join(f"v{j}" for j in range(data.ncols))
        nonblank = " OR ".join(f"v{j} IS NOT NULL" for j in range(data.ncols))
        frm = (
            f"(SELECT {grp}, min(__o) AS __o FROM (SELECT {sel}, {b.order} AS __o FROM {b.frm} "
            f"WHERE {where}) AS _q WHERE {nonblank} GROUP BY {grp}) AS {alias}"
        )
        nb = Base(frm, alias, [], f"{alias}.__o", ("unique", alias))
        return A(nb, [S(f"{alias}.v{j}", c.typ) for j, c in enumerate(data.cols)])

    def fn_SORTN(self, args):
        self._args(args, 1, None, "SORTN")
        n = self.const_int(args[1], 1) if len(args) > 1 else 1
        sort_args = (args[0],) + tuple(args[3:]) if len(args) > 3 else (args[0],)
        res = self.fn_SORT(sort_args)
        b = res.base
        nb = Base(b.frm, b.alias, b.where + [f"{b.alias}.__o <= {n}"], b.order, ("sortn", b.key, n))
        return A(nb, res.cols)

    def fn_ARRAY_CONSTRAIN(self, args):
        self._args(args, 3, 3, "ARRAY_CONSTRAIN")
        data = self._rel(self.comp(args[0]))
        nr = self.const_int(args[1])
        nc = self.const_int(args[2])
        frm, alias, cols = self.numbered(data)
        nb = Base(frm, alias, [f"{alias}.__p <= {nr}"], f"{alias}.__p", ("constrain", alias))
        return A(nb, cols[:nc], header=data.header[:nc] if data.header else None)

    def fn_SEQUENCE(self, args):
        self._args(args, 1, 4, "SEQUENCE")
        nr = self.const_int(args[0])
        nc = self.const_int(args[1], 1) if len(args) > 1 else 1
        start = self.scalar(args[2]) if len(args) > 2 and not isinstance(args[2], P.Blank) else lit_sql(1)
        step = self.scalar(args[3]) if len(args) > 3 and not isinstance(args[3], P.Blank) else lit_sql(1)
        if nr is None or nr < 1 or nc < 1:
            raise FormulaError("#VALUE!", "SEQUENCE needs positive sizes")
        alias = self.alias("q")
        frm = f"range(0, {nr}) AS {alias}(i)"
        nb = Base(frm, alias, [], f"{alias}.i", ("seq", alias))
        cols = [S(f"({num(start)} + ({alias}.i * {nc} + {j}) * {num(step)})", NUM) for j in range(nc)]
        return A(nb, cols)

    def fn_FLATTEN(self, args):
        selects = []
        k = 0
        for a in args:
            v = self._rel(self.comp(a))
            b = v.base
            where = " AND ".join(b.where) or "TRUE"
            for j, c in enumerate(v.cols):
                k += 1
                selects.append(f"SELECT {txt(c)} AS v0, {b.order} AS o, {j} AS j FROM {b.frm} WHERE {where}")
        alias = self.alias("f")
        frm = (
            f"(SELECT v0, row_number() OVER (ORDER BY o, j) AS __o FROM "
            f"({' UNION ALL '.join(selects)}) AS _f) AS {alias}"
        )
        nb = Base(frm, alias, [], f"{alias}.__o", ("flat", alias))
        return A(nb, [S(f"{alias}.v0", STR)])

    def materialize_inline(self, a: A, limit: int | None = None) -> list[list[S]]:
        """Fetch a (small) array's values and return them as literal cells."""
        limit = limit or self.MAX_INLINE
        if a.inline is not None:
            return a.inline
        b = a.base
        where = " AND ".join(b.where) or "TRUE"
        sel = ", ".join(c.sql for c in a.cols)
        rows = self.con.execute(
            f"SELECT {sel} FROM {b.frm} WHERE {where} ORDER BY {b.order} LIMIT {limit + 1}"
        ).fetchall()
        if len(rows) > limit:
            raise FormulaError("#VALUE!", "Array too large for this function")
        out = []
        if a.header is not None:
            out.append(a.header)
        for r in rows:
            out.append([py_lit(v, c.typ) for v, c in zip(r, a.cols, strict=True)])
        return out

    def fn_TRANSPOSE(self, args):
        self._args(args, 1, 1, "TRANSPOSE")
        v = self.comp(args[0])
        if isinstance(v, S):
            return v
        rows = self.materialize_inline(v)
        width = max(len(r) for r in rows) if rows else 0
        return A(
            None,
            inline=[
                [rows[i][j] if j < len(rows[i]) else lit_sql(None) for i in range(len(rows))]
                for j in range(width)
            ],
        )

    def fn_SPLIT(self, args):
        self._args(args, 2, 4, "SPLIT")
        text = self.comp(args[0])
        delim = self.scalar(args[1])
        by_each = True
        if len(args) > 2 and not isinstance(args[2], P.Blank):
            by_each = bool(self.const_value(S(boo(self.scalar(args[2])), BOOL)))
        remove_empty = True
        if len(args) > 3:
            remove_empty = bool(self.const_value(S(boo(self.scalar(args[3])), BOOL)))
        d = self.const_value(delim)
        d = "" if d is None else str(d)
        if by_each and len(d) > 1:
            pattern = "[" + re.escape(d).replace("]", "\\]") + "]"
        else:
            pattern = re.escape(d)

        def list_expr(t: S) -> str:
            e = f"regexp_split_to_array({txt0(t)}, {sql_str(pattern)})"
            if remove_empty:
                e = f"list_filter({e}, lambda x: x <> '')"
            return e

        if isinstance(text, S):
            if self.mode == "row":
                return S(f"({list_expr(text)})[1]", STR)
            vals = self.query_scalar(f"SELECT {list_expr(text)}") or []
            return A(None, inline=[[lit_auto(v) for v in vals]] or [[lit_sql(None)]])
        text = self._rel(text)
        b = text.base
        where = " AND ".join(b.where) or "TRUE"
        le = list_expr(text.cols[0])
        width = self.query_scalar(f"SELECT max(len({le})) FROM {b.frm} WHERE {where}") or 1
        return A(b, [S(f"({le})[{j + 1}]", STR) for j in range(int(width))])

    # -- QUERY ----------------------------------------------------------------

    def fn_QUERY(self, args):
        self._args(args, 1, 3, "QUERY")
        from .query_lang import translate_query

        data = self.comp(args[0])
        if isinstance(data, S):
            data = A(None, inline=[[data]])
        q = self.const_value(self.scalar(args[1])) if len(args) > 1 else "select *"
        headers = self.const_int(args[2], -1) if len(args) > 2 else -1
        return translate_query(self, data, str(q or "select *"), headers)

    # -- info -------------------------------------------------------------

    def fn_ROW(self, args):
        if not args:
            if self.mode == "row":
                return S("(t.__rid + 2)", NUM)
            return lit_sql(self.row + 1)
        node = args[0]
        if isinstance(node, P.RefNode):
            ref = node.ref
            if ref.is_cell:
                if self.mode == "row" and not ref.ar1:
                    return S(f"(t.__rid + 2 + ({ref.r1 - self.row}))", NUM)
                return lit_sql(ref.r1 + 1)
            v = self.comp(node)
            if isinstance(v, A) and v.base is not None and v.src is not None:
                b = v.base
                header = [lit_sql(1)] if v.header is not None else None
                return A(b, [S(f"CAST({b.alias}.__rid + 2 AS DOUBLE)", NUM)], header=header)
        raise FormulaError("#VALUE!", "ROW needs a reference")

    def fn_COLUMN(self, args):
        if not args:
            return lit_sql(self.col + 1)
        node = args[0]
        if isinstance(node, P.RefNode) and node.ref.c1 is not None:
            return lit_sql(node.ref.c1 + 1)
        raise FormulaError("#VALUE!", "COLUMN needs a reference")

    def fn_ROWS(self, args):
        v = self.comp(args[0])
        if isinstance(v, S):
            return lit_sql(1)
        if v.inline is not None:
            return lit_sql(len(v.inline))
        b = v.base
        where = " AND ".join(b.where) or "TRUE"
        h = 1 if v.header is not None else 0
        return S(f"((SELECT count(*) FROM {b.frm} WHERE {where}) + {h})", NUM)

    def fn_COLUMNS(self, args):
        v = self.comp(args[0])
        return lit_sql(1 if isinstance(v, S) else v.ncols)

    def fn_ISBLANK(self, args):
        self._args(args, 1, 1, "ISBLANK")
        return self.lift(
            lambda a: S(f"({a.sql} IS NULL)" if a.typ != STR else f"({a.sql} IS NULL OR {a.sql} = '')", BOOL),
            [self.comp(args[0])],
        )

    def fn_ISERROR(self, args):
        return self.lift(lambda a: S(f"({a.sql} IS NULL)", BOOL), [self.comp(args[0])])

    fn_ISNA = fn_ISERROR
    fn_ISERR = fn_ISERROR

    def fn_ISREF(self, args):
        return lit_sql(isinstance(args[0], P.RefNode))

    def fn_ISFORMULA(self, args):
        return lit_sql(False)

    def fn_NA(self, args):
        return lit_sql(None)

    def fn_HYPERLINK(self, args):
        self._args(args, 1, 2, "HYPERLINK")
        return self.comp(args[1] if len(args) > 1 else args[0])

    def fn_TODAY(self, args):
        self.volatile = True
        return S("current_date", DATE)

    def fn_NOW(self, args):
        self.volatile = True
        return S("CAST(now() AS TIMESTAMP)", TS)

    def fn_RAND(self, args):
        self.volatile = True
        return S("random()", NUM)

    def fn_RANDBETWEEN(self, args):
        self._args(args, 2, 2, "RANDBETWEEN")
        self.volatile = True
        lo, hi = self.scalar(args[0]), self.scalar(args[1])
        return S(f"floor(random() * ({num(hi)} - {num(lo)} + 1) + {num(lo)})", NUM)

    def fn_PI(self, args):
        return S("pi()", NUM)

    def fn_TEXT(self, args):
        self._args(args, 2, 2, "TEXT")
        v = self.comp(args[0])
        fmt = self.const_value(self.scalar(args[1]))
        return self.lift(lambda x: text_format(x, str(fmt or "")), [v])


def py_lit(v, typ: str) -> S:
    if v is None:
        return lit_sql(None)
    if typ == DATE:
        return S(f"DATE '{v.isoformat()}'", DATE, v, True)
    if typ == TS:
        return S(f"TIMESTAMP '{v.isoformat(sep=' ')}'", TS, v, True)
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return lit_sql(float(v))
    if hasattr(v, "is_finite"):  # Decimal
        return lit_sql(float(v))
    return lit_sql(v)


def lit_auto(v) -> S:
    if isinstance(v, str) and _NUMERIC_RE.match(v):
        return lit_sql(float(v))
    return lit_sql(v)


def arith(op: str, a: S, b: S) -> S:
    if op in ("+", "-"):
        if a.typ == DATE and b.typ in (NUM, NULL, BOOL):
            return S(f"({a.sql} {op} CAST({num(b)} AS INTEGER))", DATE)
        if op == "+" and b.typ == DATE and a.typ in (NUM, NULL, BOOL):
            return S(f"({b.sql} + CAST({num(a)} AS INTEGER))", DATE)
        if op == "-" and a.typ == DATE and b.typ == DATE:
            return S(f"CAST(({a.sql} - {b.sql}) AS DOUBLE)", NUM)
        if a.typ == TS and b.typ in (NUM, NULL):
            return S(f"({a.sql} {op} to_microseconds(CAST(round({num(b)} * 86400000000) AS BIGINT)))", TS)
        if TS in (a.typ, b.typ) and a.typ in (DATE, TS) and b.typ in (DATE, TS) and op == "-":
            return S(f"(epoch({as_ts(a)}) - epoch({as_ts(b)})) / 86400.0", NUM)
    if op == "/":
        return S(f"({num(a)} / NULLIF({num(b)}, 0))", NUM)
    if op == "^":
        return S(f"power({num(a)}, {num(b)})", NUM)
    return S(f"({num(a)} {op} {num(b)})", NUM)


def text_format(x: S, fmt: str) -> S:
    f = fmt
    lower = f.lower()
    if (
        any(tok in lower for tok in ("yy", "dd", "hh", "ss"))
        or re.search(r"m{3,}", lower)
        or ("m" in lower and ("d" in lower or "y" in lower))
    ):
        mapping = [
            ("yyyy", "%Y"),
            ("yy", "%y"),
            ("mmmm", "%B"),
            ("mmm", "%b"),
            ("dddd", "%A"),
            ("ddd", "%a"),
            ("dd", "%d"),
            ("hh", "%H"),
            ("ss", "%S"),
            ("am/pm", "%p"),
        ]
        out = ""
        i = 0
        while i < len(f):
            for k, v in mapping:
                if lower.startswith(k, i):
                    out += v
                    i += len(k)
                    break
            else:
                ch = lower[i]
                if ch == "m":
                    j = i
                    while j < len(f) and lower[j] == "m":
                        j += 1
                    prev = out.rstrip()[-2:] if out else ""
                    out += "%M" if prev in ("%H",) or (out.endswith(":") and "%H" in out) else "%m"
                    i = j
                elif ch == "d":
                    out += "%-d"
                    i += 1
                elif ch == "h":
                    out += "%-H"
                    i += 1
                else:
                    out += "%%" if f[i] == "%" else f[i]
                    i += 1
        return S(f"strftime({as_ts(x)}, {sql_str(out)})", STR)
    m = re.match(r"^([^0#]*)([#,0]*)(?:\.(0+|#+))?(%?)(.*)$", f)
    if not m:
        return S(txt(x), STR)
    prefix, intpart, dec, pct, suffix = m.groups()
    decimals = len(dec or "")
    comma = "," in intpart
    v = num(x)
    if pct:
        v = f"({v} * 100)"
    spec = "{:" + ("," if comma else "") + f".{decimals}f" + "}"
    body = f"format({sql_str(spec)}, CAST({v} AS DOUBLE))"
    return S(f"({sql_str(prefix)} || {body} || {sql_str(pct + suffix)})", STR)


# ---------------------------------------------------------------- scalar lib


def _n(fn):
    return lambda *a: S(fn(*[num(x) for x in a]), NUM)


def _t(fn, typ=STR):
    return lambda *a: S(fn(*a), typ)


def _proper(x: S) -> str:
    return (
        f"array_to_string(list_transform(string_split(lower({txt(x)}), ' '), "
        f"lambda w: upper(w[1]) || w[2:]), ' ')"
    )


def _mid(s, start, n):
    return f"substring({txt(s)}, CAST({num(start)} AS BIGINT), CAST({num(n)} AS BIGINT))"


def _left(s, n=None):
    return f"left({txt(s)}, CAST({num(n) if n is not None else 1} AS BIGINT))"


def _right(s, n=None):
    return f"right({txt(s)}, CAST({num(n) if n is not None else 1} AS BIGINT))"


def _substitute(s, old, new, inst=None):
    if inst is None:
        return f"replace({txt(s)}, {txt(old)}, {txt0(new)})"
    # replace only the n-th occurrence
    return (
        f"(CASE WHEN CAST({num(inst)} AS BIGINT) < 1 THEN {txt(s)} ELSE "
        f"array_to_string(list_transform(generate_series(1, len(string_split({txt(s)}, {txt(old)}))), "
        f"lambda i: (string_split({txt(s)}, {txt(old)}))[i] || CASE WHEN i = len(string_split({txt(s)}, {txt(old)})) "
        f"THEN '' WHEN i = CAST({num(inst)} AS BIGINT) THEN {txt0(new)} ELSE {txt(old)} END), '') END)"
    )


def _find(needle, hay, start=None, ci=False):
    h, n = txt(hay), txt(needle)
    if ci:
        h, n = f"lower({h})", f"lower({n})"
    if start is None:
        return f"NULLIF(instr({h}, {n}), 0)"
    st = f"CAST({num(start)} AS BIGINT)"
    return f"NULLIF(instr(substring({h}, {st}), {n}) + {st} - 1, {st} - 1)"


def _round(x, d=None, mode="round"):
    dd = f"CAST({num(d)} AS INTEGER)" if d is not None else "0"
    v = num(x)
    if mode == "round":
        return f"(sign({v}) * floor(abs({v}) * power(10, {dd}) + 0.5) / power(10, {dd}))"
    if mode == "up":
        return f"(sign({v}) * ceil(abs({v}) * power(10, {dd}) - 1e-9) / power(10, {dd}))"
    return f"(sign({v}) * floor(abs({v}) * power(10, {dd}) + 1e-9) / power(10, {dd}))"


def _date(y, m, d):
    return (
        f"(make_date(CAST({num(y)} AS BIGINT), 1, 1) + to_months(CAST({num(m)} AS INTEGER) - 1) "
        f"+ to_days(CAST({num(d)} AS INTEGER) - 1))::DATE"
    )


def _datedif(a, b, unit):
    u = txt(unit)
    return (
        f"(CASE upper({u}) WHEN 'D' THEN date_diff('day', {as_date(a)}, {as_date(b)}) "
        f"WHEN 'M' THEN date_diff('month', {as_date(a)}, {as_date(b)}) - CASE WHEN day({as_date(b)}) < day({as_date(a)}) THEN 1 ELSE 0 END "
        f"WHEN 'Y' THEN floor((date_diff('month', {as_date(a)}, {as_date(b)}) - CASE WHEN day({as_date(b)}) < day({as_date(a)}) THEN 1 ELSE 0 END) / 12) "
        f"ELSE date_diff('day', {as_date(a)}, {as_date(b)}) END)"
    )


def _weekday(d, t=None):
    tt = f"CAST({num(t)} AS INTEGER)" if t is not None else "1"
    dow = f"dayofweek({as_date(d)})"  # 0 = Sunday
    return (
        f"(CASE {tt} WHEN 2 THEN ((({dow}) + 6) % 7) + 1 WHEN 3 THEN (({dow}) + 6) % 7 ELSE ({dow}) + 1 END)"
    )


def _value(x):
    if x.typ == NUM:
        return x.sql
    if x.typ in (DATE, TS):
        return num(x)
    return (
        f"coalesce(TRY_CAST(replace(replace(trim({txt(x)}), ',', ''), '$', '') AS DOUBLE), "
        f"CASE WHEN trim({txt(x)}) LIKE '%\\%' ESCAPE '\\' THEN TRY_CAST(rtrim(trim({txt(x)}), '%') AS DOUBLE) / 100 END)"
    )


SCALAR_FUNCS: dict[str, tuple[int, int | None, Any]] = {
    # math
    "ABS": (1, 1, _n(lambda x: f"abs({x})")),
    "ROUND": (1, 2, lambda x, d=None: S(_round(x, d), NUM)),
    "ROUNDUP": (1, 2, lambda x, d=None: S(_round(x, d, "up"), NUM)),
    "ROUNDDOWN": (1, 2, lambda x, d=None: S(_round(x, d, "down"), NUM)),
    "TRUNC": (1, 2, lambda x, d=None: S(_round(x, d, "down"), NUM)),
    "INT": (1, 1, _n(lambda x: f"floor({x})")),
    "MOD": (2, 2, _n(lambda a, b: f"((({a}) % NULLIF({b}, 0)) + ({b})) % NULLIF({b}, 0)")),
    "POWER": (2, 2, _n(lambda a, b: f"power({a}, {b})")),
    "SQRT": (1, 1, _n(lambda x: f"sqrt(CASE WHEN {x} >= 0 THEN {x} END)")),
    "EXP": (1, 1, _n(lambda x: f"exp({x})")),
    "LN": (1, 1, _n(lambda x: f"ln(CASE WHEN {x} > 0 THEN {x} END)")),
    "LOG10": (1, 1, _n(lambda x: f"log10(CASE WHEN {x} > 0 THEN {x} END)")),
    "LOG": (
        1,
        2,
        lambda x, b=None: S(
            f"(ln(CASE WHEN {num(x)} > 0 THEN {num(x)} END) / ln({num(b) if b is not None else 10}))", NUM
        ),
    ),
    "SIGN": (1, 1, _n(lambda x: f"sign({x})")),
    "CEILING": (
        1,
        2,
        lambda x, s=None: S(
            f"(ceil({num(x)} / {num(s) if s is not None else 1}) * {num(s) if s is not None else 1})", NUM
        ),
    ),
    "FLOOR": (
        1,
        2,
        lambda x, s=None: S(
            f"(floor({num(x)} / {num(s) if s is not None else 1}) * {num(s) if s is not None else 1})", NUM
        ),
    ),
    "MROUND": (2, 2, _n(lambda x, m: f"(round({x} / NULLIF({m}, 0)) * {m})")),
    "QUOTIENT": (2, 2, _n(lambda a, b: f"trunc({a} / NULLIF({b}, 0))")),
    "EVEN": (1, 1, _n(lambda x: f"(sign({x}) * ceil(abs({x}) / 2) * 2)")),
    "ODD": (1, 1, _n(lambda x: f"(sign({x}) * (ceil((abs({x}) + 1) / 2) * 2 - 1))")),
    "SIN": (1, 1, _n(lambda x: f"sin({x})")),
    "COS": (1, 1, _n(lambda x: f"cos({x})")),
    "TAN": (1, 1, _n(lambda x: f"tan({x})")),
    "ASIN": (1, 1, _n(lambda x: f"asin({x})")),
    "ACOS": (1, 1, _n(lambda x: f"acos({x})")),
    "ATAN": (1, 1, _n(lambda x: f"atan({x})")),
    "ATAN2": (2, 2, _n(lambda x, y: f"atan2({y}, {x})")),
    "DEGREES": (1, 1, _n(lambda x: f"degrees({x})")),
    "RADIANS": (1, 1, _n(lambda x: f"radians({x})")),
    "FACT": (1, 1, _n(lambda x: f"CAST(factorial(CAST({x} AS INTEGER)) AS DOUBLE)")),
    "GCD": (2, 2, _n(lambda a, b: f"CAST(gcd(CAST({a} AS BIGINT), CAST({b} AS BIGINT)) AS DOUBLE)")),
    "LCM": (2, 2, _n(lambda a, b: f"CAST(lcm(CAST({a} AS BIGINT), CAST({b} AS BIGINT)) AS DOUBLE)")),
    "N": (1, 1, lambda x: S(num(x) if x.typ in (NUM, BOOL, DATE, TS) else "CAST(0 AS DOUBLE)", NUM)),
    "VALUE": (1, 1, lambda x: S(_value(x), NUM)),
    "TO_TEXT": (1, 1, lambda x: S(txt(x), STR)),
    "CLEANKEY": (1, 1, lambda x: S(f"coalesce({norm_key(x)}, '')" if x.typ != NULL else "''", STR)),
    # text
    "TRIM": (1, 1, _t(lambda x: f"trim(regexp_replace({txt(x)}, '\\s+', ' ', 'g'))")),
    "CLEAN": (1, 1, _t(lambda x: f"regexp_replace({txt(x)}, '[\\x00-\\x1F]', '', 'g')")),
    "UPPER": (1, 1, _t(lambda x: f"upper({txt(x)})")),
    "LOWER": (1, 1, _t(lambda x: f"lower({txt(x)})")),
    "PROPER": (1, 1, _t(_proper)),
    "LEN": (1, 1, lambda x: S(f"CAST(length({txt0(x)}) AS DOUBLE)", NUM)),
    "LEFT": (1, 2, _t(_left)),
    "RIGHT": (1, 2, _t(_right)),
    "MID": (3, 3, _t(_mid)),
    "SUBSTITUTE": (3, 4, _t(_substitute)),
    "REPLACE": (
        4,
        4,
        _t(
            lambda s, st, n, new: (
                f"(left({txt(s)}, CAST({num(st)} AS BIGINT) - 1) || {txt0(new)} || "
                f"substring({txt(s)}, CAST({num(st)} AS BIGINT) + CAST({num(n)} AS BIGINT)))"
            )
        ),
    ),
    "REPT": (2, 2, _t(lambda s, n: f"repeat({txt0(s)}, CAST({num(n)} AS BIGINT))")),
    "FIND": (2, 3, lambda n, h, s=None: S(f"CAST({_find(n, h, s)} AS DOUBLE)", NUM)),
    "SEARCH": (2, 3, lambda n, h, s=None: S(f"CAST({_find(n, h, s, ci=True)} AS DOUBLE)", NUM)),
    "EXACT": (2, 2, lambda a, b: S(f"({txt0(a)} = {txt0(b)})", BOOL)),
    "CHAR": (1, 1, _t(lambda x: f"chr(CAST({num(x)} AS INTEGER))")),
    "CODE": (1, 1, lambda x: S(f"CAST(ascii({txt(x)}) AS DOUBLE)", NUM)),
    "UNICODE": (1, 1, lambda x: S(f"CAST(unicode({txt(x)}) AS DOUBLE)", NUM)),
    "T": (1, 1, lambda x: S(x.sql if x.typ == STR else "''", STR)),
    "REGEXMATCH": (2, 2, lambda s, p: S(f"regexp_matches({txt0(s)}, {txt(p)})", BOOL)),
    "REGEXEXTRACT": (
        2,
        2,
        _t(
            lambda s, p: (
                f"(CASE WHEN regexp_matches({txt0(s)}, {txt(p)}) THEN "
                f"regexp_extract({txt0(s)}, {txt(p)}, CASE WHEN regexp_matches({txt(p)}, '\\(([^?]|$)') THEN 1 ELSE 0 END) END)"
            )
        ),
    ),
    "REGEXREPLACE": (
        3,
        3,
        _t(
            lambda s, p, r: (
                f"regexp_replace({txt0(s)}, {txt(p)}, regexp_replace({txt0(r)}, '\\$(\\d)', '\\\\\\1', 'g'), 'g')"
            )
        ),
    ),
    "FIXED": (
        1,
        3,
        _t(
            lambda x, d=None, nc=None: (
                f"format('{{:,.{{}}f}}', {num(x)}, CAST({num(d) if d is not None else 2} AS INTEGER))"
            )
        ),
    ),
    "DOLLAR": (1, 2, _t(lambda x, d=None: f"'$' || format('{{:,.2f}}', {num(x)})")),
    # dates
    "DATE": (3, 3, lambda y, m, d: S(_date(y, m, d), DATE)),
    "DATEVALUE": (1, 1, lambda x: S(as_date(x), DATE)),
    "TIMEVALUE": (1, 1, lambda x: S(f"(epoch(TRY_CAST({txt(x)} AS TIME)) / 86400.0)", NUM)),
    "YEAR": (1, 1, lambda x: S(f"CAST(year({as_date(x)}) AS DOUBLE)", NUM)),
    "MONTH": (1, 1, lambda x: S(f"CAST(month({as_date(x)}) AS DOUBLE)", NUM)),
    "DAY": (1, 1, lambda x: S(f"CAST(day({as_date(x)}) AS DOUBLE)", NUM)),
    "HOUR": (1, 1, lambda x: S(f"CAST(hour({as_ts(x)}) AS DOUBLE)", NUM)),
    "MINUTE": (1, 1, lambda x: S(f"CAST(minute({as_ts(x)}) AS DOUBLE)", NUM)),
    "SECOND": (1, 1, lambda x: S(f"CAST(second({as_ts(x)}) AS DOUBLE)", NUM)),
    "WEEKDAY": (1, 2, lambda d, t=None: S(f"CAST({_weekday(d, t)} AS DOUBLE)", NUM)),
    "WEEKNUM": (1, 2, lambda d, t=None: S(f"CAST(strftime({as_date(d)}, '%U') AS DOUBLE) + 1", NUM)),
    "ISOWEEKNUM": (1, 1, lambda d: S(f"CAST(weekofyear({as_date(d)}) AS DOUBLE)", NUM)),
    "EDATE": (
        2,
        2,
        lambda d, m: S(f"CAST({as_date(d)} + to_months(CAST({num(m)} AS INTEGER)) AS DATE)", DATE),
    ),
    "EOMONTH": (
        2,
        2,
        lambda d, m: S(f"last_day(CAST({as_date(d)} + to_months(CAST({num(m)} AS INTEGER)) AS DATE))", DATE),
    ),
    "DATEDIF": (3, 3, lambda a, b, u: S(f"CAST({_datedif(a, b, u)} AS DOUBLE)", NUM)),
    "DAYS": (2, 2, lambda e, s: S(f"CAST(date_diff('day', {as_date(s)}, {as_date(e)}) AS DOUBLE)", NUM)),
    "NETWORKDAYS": (
        2,
        2,
        lambda s, e: S(
            f"CAST((SELECT count(*) FROM range({as_date(s)}::TIMESTAMP, {as_date(e)}::TIMESTAMP + INTERVAL 1 DAY, "
            f"INTERVAL 1 DAY) AS _d(d) WHERE dayofweek(d) NOT IN (0, 6)) AS DOUBLE)",
            NUM,
        ),
    ),
    "TIME": (3, 3, _n(lambda h, m, s: f"(({h}) * 3600 + ({m}) * 60 + ({s})) / 86400.0")),
    # info
    "ISNUMBER": (
        1,
        1,
        lambda x: S(
            "TRUE" if x.typ == NUM and x.is_lit else (f"({x.sql} IS NOT NULL)" if x.typ == NUM else "FALSE"),
            BOOL,
        ),
    ),
    "ISTEXT": (1, 1, lambda x: S(f"({x.sql} IS NOT NULL)" if x.typ == STR else "FALSE", BOOL)),
    "ISLOGICAL": (1, 1, lambda x: S(f"({x.sql} IS NOT NULL)" if x.typ == BOOL else "FALSE", BOOL)),
    "ISDATE": (1, 1, lambda x: S(f"({x.sql} IS NOT NULL)" if x.typ in (DATE, TS) else "FALSE", BOOL)),
    "ISEMAIL": (
        1,
        1,
        lambda x: S(f"coalesce(regexp_matches({txt(x)}, '^[^@\\s]+@[^@\\s]+\\.[^@\\s]+$'), FALSE)", BOOL),
    ),
    "ISURL": (
        1,
        1,
        lambda x: S(
            f"coalesce(regexp_matches({txt(x)}, '^(https?://)?[\\w.-]+\\.[a-z]{{2,}}(/\\S*)?$', 'i'), FALSE)",
            BOOL,
        ),
    ),
    "ISEVEN": (1, 1, lambda x: S(f"(CAST(trunc({num(x)}) AS BIGINT) % 2 = 0)", BOOL)),
    "ISODD": (1, 1, lambda x: S(f"(CAST(trunc({num(x)}) AS BIGINT) % 2 <> 0)", BOOL)),
}

VOLATILE = {"RAND", "RANDBETWEEN", "NOW", "TODAY"}

_DOTTED = {"STDEV_S", "STDEV_P", "VAR_S", "VAR_P", "MODE_SNGL", "PERCENTILE_INC"}
ALL_FUNCTIONS = sorted(
    set(SCALAR_FUNCS)
    | {(n[3:].replace("_", ".") if n[3:] in _DOTTED else n[3:]) for n in dir(Compiler) if n.startswith("fn_")}
)
