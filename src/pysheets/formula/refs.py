"""A1-style reference helpers.

Coordinates used throughout the engine are 0-based *sheet* coordinates:
row 0 is the header row (row "1" in the UI), row ``r`` >= 1 holds data row
``rid = r - 1``.  Columns are 0-based positions (A = 0).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

_COL_RE = re.compile(r"^[A-Za-z]{1,3}$")


def col_to_letters(idx: int) -> str:
    if idx < 0:
        raise ValueError("negative column")
    out = ""
    n = idx + 1
    while n:
        n, rem = divmod(n - 1, 26)
        out = chr(65 + rem) + out
    return out


def letters_to_col(s: str) -> int:
    n = 0
    for ch in s.upper():
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def is_col_letters(s: str) -> bool:
    return bool(_COL_RE.match(s))


def cell_name(row: int, col: int) -> str:
    return f"{col_to_letters(col)}{row + 1}"


def quote_sheet(name: str) -> str:
    if re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name) and not re.match(r"^[A-Za-z]{1,3}[0-9]+$", name):
        return name
    return "'" + name.replace("'", "''") + "'"


@dataclass(frozen=True)
class Ref:
    """A cell or range reference.  ``None`` bounds are open ends (A:A, A2:A)."""

    sheet: str | None
    r1: int | None
    c1: int | None
    r2: int | None
    c2: int | None
    ar1: bool = False
    ac1: bool = False
    ar2: bool = False
    ac2: bool = False
    rng: bool = False  # written as a range (A1:A1), even if it covers one cell

    @property
    def is_cell(self) -> bool:
        return (
            not self.rng
            and self.r1 is not None
            and self.c1 is not None
            and self.r1 == self.r2
            and self.c1 == self.c2
        )

    def with_sheet(self, sheet: str | None) -> Ref:
        return replace(self, sheet=sheet)

    def to_a1(self) -> str:
        def part(r, c, ar, ac):
            s = ""
            if c is not None:
                s += ("$" if ac else "") + col_to_letters(c)
            if r is not None:
                s += ("$" if ar else "") + str(r + 1)
            return s

        a = part(self.r1, self.c1, self.ar1, self.ac1)
        prefix = (quote_sheet(self.sheet) + "!") if self.sheet else ""
        if self.is_cell or (
            self.r1 == self.r2
            and self.c1 == self.c2
            and not self.rng
            and self.r1 is not None
            and self.c1 is not None
        ):
            return prefix + a
        b = part(self.r2, self.c2, self.ar2, self.ac2)
        return f"{prefix}{a}:{b}"


_CELL_RE = re.compile(r"^(\$?)([A-Za-z]{1,3})(\$?)(\d+)$")


def parse_cell(text: str) -> tuple[int, int, bool, bool] | None:
    """Parse ``$A$1`` → (row, col, abs_row, abs_col)."""
    m = _CELL_RE.match(text)
    if not m:
        return None
    row = int(m.group(4)) - 1
    if row < 0:
        return None
    return row, letters_to_col(m.group(2)), bool(m.group(3)), bool(m.group(1))


def parse_a1(text: str) -> Ref | None:
    """Parse a standalone reference like ``Sheet1!A1:B``.  Used by the name box."""
    text = text.strip()
    sheet = None
    if "!" in text:
        sheet, text = text.rsplit("!", 1)
        if sheet.startswith("'") and sheet.endswith("'"):
            sheet = sheet[1:-1].replace("''", "'")
    parts = text.split(":")
    if len(parts) == 1:
        c = parse_cell(parts[0])
        if not c:
            return None
        return Ref(sheet, c[0], c[1], c[0], c[1], c[2], c[3], c[2], c[3])
    if len(parts) != 2:
        return None
    ends = []
    for p in parts:
        c = parse_cell(p)
        if c:
            ends.append(c)
            continue
        m = re.match(r"^(\$?)([A-Za-z]{1,3})$", p)
        if m:
            ends.append((None, letters_to_col(m.group(2)), False, bool(m.group(1))))
            continue
        m = re.match(r"^(\$?)(\d+)$", p)
        if m:
            ends.append((int(m.group(2)) - 1, None, bool(m.group(1)), False))
            continue
        return None
    (r1, c1, ar1, ac1), (r2, c2, ar2, ac2) = ends
    return normalize(Ref(sheet, r1, c1, r2, c2, ar1, ac1, ar2, ac2, True))


def normalize(ref: Ref) -> Ref:
    r1, r2, ar1, ar2 = ref.r1, ref.r2, ref.ar1, ref.ar2
    c1, c2, ac1, ac2 = ref.c1, ref.c2, ref.ac1, ref.ac2
    if r1 is not None and r2 is not None and r1 > r2:
        r1, r2, ar1, ar2 = r2, r1, ar2, ar1
    if c1 is not None and c2 is not None and c1 > c2:
        c1, c2, ac1, ac2 = c2, c1, ac2, ac1
    return Ref(ref.sheet, r1, c1, r2, c2, ar1, ac1, ar2, ac2, ref.rng)
