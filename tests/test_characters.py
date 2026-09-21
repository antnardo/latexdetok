"""Command names under TeX's catcodes."""

import pytest

from latexdetok.characters import valid_command_name, valid_group_start


@pytest.mark.parametrize(
    "name",
    ["section", "section*", "a", "%", "\\", "\\*", "@", " ", "*", "é", "verb*", "rslt@opt", "cs_new:Npn"],
)
def test_valid_name(name):
    assert valid_command_name(name)


@pytest.mark.parametrize("name", ["", "ab1", "foo-bar", "a*b", "sec*tion*", "**x", "café"])
def test_invalid_name(name):
    assert not valid_command_name(name)


@pytest.mark.parametrize(("car", "expected"), [("{", True), ("[", True), ("(", False), ("$", False)])
def test_group_opening(car, expected):
    assert valid_group_start(car) is expected
