import pytest

from pysheets.formula import parser as P
from pysheets.formula.refs import Ref, col_to_letters, letters_to_col, parse_a1


def test_col_letters_roundtrip():
    for i in [0, 1, 25, 26, 27, 51, 52, 701, 702, 16383]:
        assert letters_to_col(col_to_letters(i)) == i
    assert col_to_letters(0) == "A" and col_to_letters(26) == "AA" and col_to_letters(701) == "ZZ"


@pytest.mark.parametrize(
    "text,expected",
    [
        ("=A1", Ref(None, 0, 0, 0, 0)),
        ("=$B$2", Ref(None, 1, 1, 1, 1, True, True, True, True)),
        ("=A:A", Ref(None, None, 0, None, 0, rng=True)),
        ("=A2:C", Ref(None, 1, 0, None, 2, rng=True)),
        ("=Sheet2!B3:D9", Ref("Sheet2", 2, 1, 8, 3, rng=True)),
        ("='My Sheet'!A1", Ref("My Sheet", 0, 0, 0, 0)),
        ("=1:3", Ref(None, 0, None, 2, None, rng=True)),
    ],
)
def test_refs(text, expected):
    node = P.parse_formula(text)
    assert isinstance(node, P.RefNode)
    assert node.ref == expected


def test_precedence_and_calls():
    node = P.parse_formula('=IF(A1>2,"a"&B1,-2^2)')
    assert isinstance(node, P.Call) and node.name == "IF"
    assert isinstance(node.args[0], P.Binary) and node.args[0].op == ">"
    assert isinstance(node.args[1], P.Binary) and node.args[1].op == "&"
    # unary minus binds tighter than ^ in Sheets
    assert isinstance(node.args[2], P.Binary) and node.args[2].op == "^"


def test_function_named_like_cell():
    node = P.parse_formula("=LOG10(100)")
    assert isinstance(node, P.Call) and node.name == "LOG10"


def test_array_literal_and_blank_args():
    node = P.parse_formula("={1,2;3,4}")
    assert isinstance(node, P.ArrayLit) and len(node.rows) == 2
    node = P.parse_formula("=IF(A1,,2)")
    assert isinstance(node.args[1], P.Blank)


def test_syntax_error():
    with pytest.raises(P.FormulaSyntaxError):
        P.parse_formula("=1+")


def test_shift_formula():
    assert P.shift_formula("=A2+$B$2+B$3+$C4+SUM(A2:A)", 3, 1) == "=B5+$B$2+C$3+$C7+SUM(B5:B)"
    assert P.shift_formula("=Sheet2!A1", 1, 0) == "=Sheet2!A2"
    assert P.shift_formula("=SUM($E$2:E2)", 5, 0) == "=SUM($E$2:E7)"
    assert P.shift_formula("=A1", -1, 0) == "=#REF!"


def test_strings_are_not_refs():
    assert P.shift_formula('="A1"&A1', 1, 0) == '="A1"&A2'


def test_parse_a1():
    assert parse_a1("B2:C10") == Ref(None, 1, 1, 9, 2, rng=True)
    assert parse_a1("nonsense!!") is None
