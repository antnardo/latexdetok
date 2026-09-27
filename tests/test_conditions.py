"""Conditionals: recognising an `\\if…`, and deciding what can be decided without compiling."""

import pytest

from latexdetok.conditions import Role, argument_test_value, conditional_role, ifnum_test, setter


def booleans(name):
    return name in ("prof", "AMC@correc")


class TestRole:
    @pytest.mark.parametrize(
        ("name", "following", "known", "role"),
        [
            ("ifnum", "1=0", False, Role.IF),
            ("ifprof", " A", False, Role.IF),
            ("ifAMC@correc", "", False, Role.IF),
            ("else", "", False, Role.ELSE),
            ("or", "", False, Role.OR),
            ("fi", "", False, Role.FI),
            ("ifpdf", "", False, Role.IF),
            ("ifstrempty", "{#1}{A}{B}", False, None),
            ("ifthenelse", " {\\boolean{x}}", False, None),
            ("ifsol", "", True, None),
            ("section", "{A}", False, None),
        ],
    )
    def test_role(self, name, following, known, role):
        assert conditional_role(name, following, booleans, known) == role


@pytest.mark.parametrize(
    ("following", "expected"),
    [
        ("1=0 A", (False, 4)),
        (" 2 > 1 A", (True, 7)),
        ("-3<2\\relax", (True, 4)),
        ("10=10", (True, 5)),
        ("\\value{page}>1", None),
        ("1=\\foo", None),
        ('"1F=31', None),
    ],
)
def test_ifnum(following, expected):
    assert ifnum_test(following) == expected


@pytest.mark.parametrize(
    ("name", "expected"),
    [("proftrue", ("prof", True)), ("proffalse", ("prof", False)), ("soltrue", None), ("prof", None)],
)
def test_setter(name, expected):
    assert setter(name, booleans) == expected


@pytest.mark.parametrize(
    ("name", "test", "value"),
    [
        ("IfBooleanTF", "\\BooleanTrue", True),
        ("IfBooleanT", " \\BooleanFalse ", False),
        ("IfBooleanF", "#1", None),
        ("IfValueTF", "-NoValue-", False),
        ("IfValueT", "x", True),
        ("IfNoValueTF", "-NoValue-", True),
        ("ifstrempty", "", True),
        ("ifstrempty", " ", False),
        ("ifblank", " ", True),
        ("ifblank", "x", False),
    ],
)
def test_conditional_with_arguments(name, test, value):
    assert argument_test_value(name, test) == value


@pytest.mark.parametrize(
    ("name", "test"),
    [
        ("ifstrempty", "#1"),
        ("ifblank", " #2 "),
        ("IfValueTF", "#1"),
        ("IfNoValueT", "a#1"),
        ("ifstrempty", "##1"),
    ],
)
def test_a_test_holding_a_parameter_is_undecided(name, test):
    # `\csdef{Fill}#1{\ifstrempty{#1}…}`: only the use of the body gives `#1` a value.
    assert argument_test_value(name, test) is None


def test_an_escaped_hash_is_decided_as_a_character():
    assert argument_test_value("ifstrempty", "\\#1") is False


def test_ifnum_on_a_number_a_parameter_goes_on_is_undecided():
    # With `#1` = 3, TeX compares 1 with 23.
    assert ifnum_test("1<2#1 A") is None
