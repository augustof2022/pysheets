"""Tokenizer and Pratt parser for spreadsheet formulas (Google Sheets dialect)."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from .refs import Ref, letters_to_col, normalize


class FormulaSyntaxError(ValueError):
    pass


# --------------------------------------------------------------------------- AST


@dataclass(frozen=True)
class Num:
    value: float


@dataclass(frozen=True)
class Str:
    value: str


@dataclass(frozen=True)
class Bool:
    value: bool


@dataclass(frozen=True)
class Err:
    code: str


@dataclass(frozen=True)
class Blank:
    """An omitted argument, e.g. the middle of ``IF(A1,,2)``."""


@dataclass(frozen=True)
class ArrayLit:
    rows: tuple[tuple[object, ...], ...]


@dataclass(frozen=True)
class RefNode:
    ref: Ref


@dataclass(frozen=True)
class Name:
    name: str


@dataclass(frozen=True)
class Unary:
    op: str
    operand: object


@dataclass(frozen=True)
class Percent:
    operand: object


@dataclass(frozen=True)
class Binary:
    op: str
    left: object
    right: object


@dataclass(frozen=True)
class Call:
    name: str
    args: tuple[object, ...]


# ----------------------------------------------------------------------- lexer

_SHEET = r"(?:'(?:[^']|'')+'|[A-Za-z_][A-Za-z0-9_.]*)!"
_CELL = r"\$?[A-Za-z]{1,3}\$?[0-9]+"
_COLP = r"\$?[A-Za-z]{1,3}"
_ROWP = r"\$?[0-9]+"
_REF = (
    rf"(?P<sheet>{_SHEET})?"
    rf"(?P<body>{_CELL}(?::(?:{_CELL}|{_COLP}|{_ROWP}))?"
    rf"|{_COLP}:(?:{_CELL}|{_COLP})"
    rf"|{_ROWP}:{_ROWP})"
    r"(?![A-Za-z0-9_(.!])"
)

_TOKEN_SPEC = [
    ("WS", r"\s+"),
    ("STRING", r'"(?:[^"]|"")*"'),
    ("REF", _REF),
    ("NUMBER", r"(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?"),
    ("ERROR", r"#(?:N/A|REF!|VALUE!|DIV/0!|NAME\?|NUM!|NULL!|ERROR!)"),
    ("IDENT", r"[A-Za-z_][A-Za-z0-9_.]*"),
    ("OP", r"<=|>=|<>|[-+*/^&=<>%(),;{}:]"),
]
_LEXER = re.compile("|".join(f"(?P<{n}>{p})" for n, p in _TOKEN_SPEC))


@dataclass
class Token:
    kind: str
    text: str
    start: int
    end: int
    sheet: str | None = None
    body: str | None = None


def tokenize(src: str) -> list[Token]:
    pos = 0
    out: list[Token] = []
    n = len(src)
    while pos < n:
        m = _LEXER.match(src, pos)
        if not m:
            raise FormulaSyntaxError(f"Unexpected character {src[pos]!r} at {pos + 1}")
        kind = m.lastgroup
        # nested named groups inside REF shadow lastgroup; detect explicitly
        if m.group("REF") is not None:
            kind = "REF"
        text = m.group(0)
        if kind != "WS":
            tok = Token(kind, text, m.start(), m.end())
            if kind == "REF":
                tok.sheet = m.group("sheet")
                tok.body = m.group("body")
            out.append(tok)
        pos = m.end()
    return out


def _unquote_sheet(s: str | None) -> str | None:
    if not s:
        return None
    s = s[:-1]  # drop '!'
    if s.startswith("'"):
        s = s[1:-1].replace("''", "'")
    return s


def _parse_part(p: str):
    m = re.match(r"^(\$?)([A-Za-z]{1,3})(\$?)([0-9]+)$", p)
    if m:
        return int(m.group(4)) - 1, letters_to_col(m.group(2)), bool(m.group(3)), bool(m.group(1))
    m = re.match(r"^(\$?)([A-Za-z]{1,3})$", p)
    if m:
        return None, letters_to_col(m.group(2)), False, bool(m.group(1))
    m = re.match(r"^(\$?)([0-9]+)$", p)
    if m:
        return int(m.group(2)) - 1, None, bool(m.group(1)), False
    raise FormulaSyntaxError(f"Bad reference {p}")


def ref_from_token(tok: Token) -> Ref:
    sheet = _unquote_sheet(tok.sheet)
    parts = tok.body.split(":")
    r1, c1, ar1, ac1 = _parse_part(parts[0])
    if len(parts) == 1:
        return Ref(sheet, r1, c1, r1, c1, ar1, ac1, ar1, ac1)
    r2, c2, ar2, ac2 = _parse_part(parts[1])
    # "A2:A" keeps r2 open; "A:A2" keeps r1 open
    return normalize(Ref(sheet, r1, c1, r2, c2, ar1, ac1, ar2, ac2, True))


# ---------------------------------------------------------------------- parser

_BINARY_PREC = {
    "=": 1,
    "<>": 1,
    "<": 1,
    ">": 1,
    "<=": 1,
    ">=": 1,
    "&": 2,
    "+": 3,
    "-": 3,
    "*": 4,
    "/": 4,
    "^": 5,
}
_UNARY_PREC = 6
_POSTFIX_PREC = 7


class _Parser:
    def __init__(self, tokens: list[Token]):
        self.toks = tokens
        self.i = 0

    def peek(self) -> Token | None:
        return self.toks[self.i] if self.i < len(self.toks) else None

    def next(self) -> Token:
        tok = self.peek()
        if tok is None:
            raise FormulaSyntaxError("Unexpected end of formula")
        self.i += 1
        return tok

    def expect(self, text: str) -> None:
        tok = self.next()
        if tok.text != text:
            raise FormulaSyntaxError(f"Expected '{text}' but found '{tok.text}'")

    def parse(self):
        node = self.expr(0)
        if self.peek() is not None:
            raise FormulaSyntaxError(f"Unexpected '{self.peek().text}'")
        return node

    def expr(self, min_prec: int):
        left = self.prefix()
        while True:
            tok = self.peek()
            if tok is None or tok.kind != "OP":
                break
            if tok.text == "%":
                if _POSTFIX_PREC < min_prec:
                    break
                self.next()
                left = Percent(left)
                continue
            prec = _BINARY_PREC.get(tok.text)
            if prec is None or prec < min_prec:
                break
            self.next()
            # ^ is left-associative in Sheets too
            right = self.expr(prec + 1)
            left = Binary(tok.text, left, right)
        return left

    def prefix(self):
        tok = self.next()
        k = tok.kind
        if k == "NUMBER":
            return Num(float(tok.text))
        if k == "STRING":
            return Str(tok.text[1:-1].replace('""', '"'))
        if k == "ERROR":
            return Err(tok.text)
        if k == "REF":
            return RefNode(ref_from_token(tok))
        if k == "IDENT":
            nxt = self.peek()
            if nxt is not None and nxt.text == "(":
                self.next()
                return Call(tok.text.upper(), tuple(self.args()))
            up = tok.text.upper()
            if up == "TRUE":
                return Bool(True)
            if up == "FALSE":
                return Bool(False)
            return Name(tok.text)
        if k == "OP":
            if tok.text in ("-", "+"):
                operand = self.expr(_UNARY_PREC)
                return operand if tok.text == "+" else Unary("-", operand)
            if tok.text == "(":
                node = self.expr(0)
                self.expect(")")
                return node
            if tok.text == "{":
                return self.array()
        raise FormulaSyntaxError(f"Unexpected '{tok.text}'")

    def args(self) -> list:
        out: list = []
        if self.peek() is not None and self.peek().text == ")":
            self.next()
            return out
        while True:
            tok = self.peek()
            if tok is not None and tok.text in (",", ")"):
                out.append(Blank())
            else:
                out.append(self.expr(0))
            tok = self.next()
            if tok.text == ")":
                return out
            if tok.text not in (",", ";"):
                raise FormulaSyntaxError(f"Expected ',' or ')' but found '{tok.text}'")

    def array(self):
        rows: list[list] = [[]]
        if self.peek() is not None and self.peek().text == "}":
            self.next()
            return ArrayLit(((),))
        while True:
            rows[-1].append(self.expr(0))
            tok = self.next()
            if tok.text == "}":
                break
            if tok.text == ",":
                continue
            if tok.text == ";":
                rows.append([])
                continue
            raise FormulaSyntaxError(f"Unexpected '{tok.text}' in array")
        return ArrayLit(tuple(tuple(r) for r in rows))


def parse_formula(text: str):
    """Parse formula text (with or without the leading '=')."""
    src = text[1:] if text.startswith("=") else text
    if not src.strip():
        raise FormulaSyntaxError("Empty formula")
    return _Parser(tokenize(src)).parse()


# ------------------------------------------------------------- ref rewriting


def transform_refs(text: str, fn: Callable[[Ref], Ref | None]) -> str:
    """Rewrite every reference in formula ``text`` through ``fn``.

    ``fn`` returns a new Ref, or None to replace the reference with ``#REF!``.
    Whitespace and everything else are preserved verbatim.
    """
    lead = "=" if text.startswith("=") else ""
    src = text[len(lead) :]
    try:
        toks = tokenize(src)
    except FormulaSyntaxError:
        return text
    out = []
    pos = 0
    for tok in toks:
        if tok.kind != "REF":
            continue
        out.append(src[pos : tok.start])
        new = fn(ref_from_token(tok))
        out.append("#REF!" if new is None else new.to_a1())
        pos = tok.end
    out.append(src[pos:])
    return lead + "".join(out)


def shift_formula(text: str, drow: int, dcol: int) -> str:
    """Relative-reference shift used by fill / copy-paste."""

    def fn(ref: Ref) -> Ref | None:
        def mv(v, absolute, d):
            if v is None or absolute:
                return v
            return v + d

        r1 = mv(ref.r1, ref.ar1, drow)
        r2 = mv(ref.r2, ref.ar2, drow)
        c1 = mv(ref.c1, ref.ac1, dcol)
        c2 = mv(ref.c2, ref.ac2, dcol)
        for v in (r1, r2, c1, c2):
            if v is not None and v < 0:
                return None
        return Ref(ref.sheet, r1, c1, r2, c2, ref.ar1, ref.ac1, ref.ar2, ref.ac2, ref.rng)

    return transform_refs(text, fn)


def iter_refs(node) -> list[Ref]:
    """All references appearing in an AST."""
    out: list[Ref] = []

    def walk(n):
        if isinstance(n, RefNode):
            out.append(n.ref)
        elif isinstance(n, (Unary, Percent)):
            walk(n.operand)
        elif isinstance(n, Binary):
            walk(n.left)
            walk(n.right)
        elif isinstance(n, Call):
            for a in n.args:
                walk(a)
        elif isinstance(n, ArrayLit):
            for row in n.rows:
                for a in row:
                    walk(a)

    walk(node)
    return out
