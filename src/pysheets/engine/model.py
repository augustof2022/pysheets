"""Sheet / column / formula metadata.  Cell data itself lives in DuckDB tables."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from ..formula.compiler import duck_to_typ


@dataclass
class Column:
    id: str
    name: str = ""
    duck: str = "VARCHAR"
    width: int = 120
    fmt: str | None = None
    linked: bool = False  # filled by a data connection (replaced on refresh)

    @property
    def typ(self) -> str:
        return duck_to_typ(self.duck)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "type": self.typ,
            "duck": self.duck,
            "width": self.width,
            "fmt": self.fmt,
            "linked": self.linked,
        }


@dataclass
class Formula:
    id: int
    sheet_id: str
    row: int  # sheet row (0 = header row)
    col_id: str
    text: str
    kind: str = "cell"  # "cell" | "column"
    row_end: int | None = None  # column formulas: last sheet row (None = through last data row)
    exceptions: list[int] = field(default_factory=list)  # column formulas: sheet rows overridden
    spill: tuple[int, int] | None = None  # (height, width) of the last array result
    want: tuple[int, int] | None = None  # size of a blocked spill (edits there re-trigger it)
    error: str | None = None
    deps: list = field(default_factory=list)
    volatile: bool = False
    # lookup diagnostics from the last evaluation: rows matched exactly, matched only after
    # normalising formats ("fuzzy"), and found nothing ("missing")
    match: dict | None = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["deps"] = [list(x) for x in self.deps]
        return d


@dataclass
class Sheet:
    id: str
    name: str
    table: str
    columns: list[Column] = field(default_factory=list)
    nrows: int = 0
    frozen_rows: int = 1
    frozen_cols: int = 0
    next_col: int = 0
    filters: dict[str, Any] = field(default_factory=dict)
    filter_on: bool = False
    view_table: str | None = None
    nview: int = 0
    styles: list[dict] = field(default_factory=list)
    merges: list[dict] = field(default_factory=list)  # {r1, c1, r2, c2} in sheet rows / column positions
    cond_formats: list[dict] = field(default_factory=list)
    validations: list[dict] = field(default_factory=list)
    pivot: dict | None = None
    tab_color: str | None = None
    version: int = 0
    row_height: int = 26
    source: str | None = None
    link: dict | None = None  # data connection (Google Sheets, ...) used by Refresh

    def col_index(self, col_id: str) -> int:
        for i, c in enumerate(self.columns):
            if c.id == col_id:
                return i
        raise KeyError(col_id)

    def col_by_id(self, col_id: str) -> Column:
        return self.columns[self.col_index(col_id)]

    def new_col_id(self) -> str:
        cid = f"c{self.next_col}"
        self.next_col += 1
        return cid

    def to_info(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "nrows": self.nrows,
            "ncols": len(self.columns),
            "columns": [c.to_dict() for c in self.columns],
            "frozen_rows": self.frozen_rows,
            "frozen_cols": self.frozen_cols,
            "filter_on": self.filter_on,
            "filters": self.filters,
            "filtered": self.view_table is not None,
            "nview": self.nview if self.view_table is not None else self.nrows,
            "styles": self.styles,
            "merges": self.merges,
            "cond_formats": self.cond_formats,
            "validations": self.validations,
            "pivot": self.pivot,
            "tab_color": self.tab_color,
            "version": self.version,
            "row_height": self.row_height,
            "source": self.source,
            "link": self.link,
        }

    def to_meta(self) -> dict:
        d = {k: v for k, v in self.__dict__.items() if k != "columns"}
        d["columns"] = [asdict(c) for c in self.columns]
        return d

    @classmethod
    def from_meta(cls, d: dict) -> Sheet:
        d = dict(d)
        d.setdefault("merges", [])
        cols = [Column(**c) for c in d.pop("columns")]
        sh = cls(**d)
        sh.columns = cols
        return sh
