"""Command names under TeX's catcodes, and columns in a line whatever its ending."""

import pytest

from latexdetok.characters import index_in_line, valid_command_name, valid_group_start


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


@pytest.mark.parametrize(
    ("line", "column", "index"),
    [
        ("abc\n", 2, 2),
        ("abc\n", 3, 3),
        ("abc\n", 4, 4),
        ("abc\r\n", 3, 3),
        ("abc\r\n", 4, 5),
        ("abc\r\n", 5, 5),
        ("abc\r", 4, 4),
        ("abc", 4, 3),
    ],
)
def test_a_column_beyond_the_text_falls_after_the_line_ending(line, column, index):
    assert index_in_line(line, column) == index
