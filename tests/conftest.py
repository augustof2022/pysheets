import pytest

from pysheets.engine.workbook import Workbook


@pytest.fixture
def wb():
    w = Workbook()
    yield w
    w.close()


@pytest.fixture
def sales(wb):
    sh = wb.sheet_from_sql(
        "Sales",
        """SELECT range + 1 AS "ID", ['North','South','East','West'][range % 4 + 1] AS "Region",
           'item ' || (range % 5) AS "Item", ((range * 7) % 100) / 2.0 AS "Price", (range % 3) + 1 AS "Qty"
           FROM range(1000)""",
        record=False,
    )
    return sh


def grid(wb, sh, r0, r1, c0, c1):
    return wb.viewport(sh.id, r0, r1, c0, c1)["cells"]


def val(wb, sh, row, col):
    return wb.viewport(sh.id, row, row + 1, col, col + 1)["cells"][0][0]
