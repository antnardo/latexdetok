"""Command names under TeX's catcodes, and columns in a line whatever its ending."""

import pytest

from latexdetok.characters import (
    caret_notation,
    index_in_line,
    reduce_caret,
    valid_command_name,
    valid_group_start,
)


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


class TestCarets:
    """The `^^` forms, each checked with LuaLaTeX (TeX Live 2026)."""

    @pytest.mark.parametrize(
        ("text", "reduced"),
        [
            ("^^69", ("i", 4)),
            ("^^e9", ("é", 4)),
            ("^^5c", ("\\", 4)),
            ("^^M", ("\r", 3)),
            ("^^J", ("\n", 3)),
            ("^^?", ("\x7f", 3)),
            ("^^2", ("r", 3)),
            ("^^4x", ("t", 3)),
            ("^^E9", ("\x05", 3)),
            ("^^^^0041", ("A", 8)),
            ("^^^^^^000042", ("B", 12)),
            ("^^^^004", ("\x1e", 3)),
        ],
    )
    def test_tex_reads_one_character(self, text, reduced):
        assert reduce_caret(text, 0) == reduced

    @pytest.mark.parametrize("text", ["^^", "^^é"])
    def test_nothing_to_reduce_leaves_two_superscripts(self, text):
        assert reduce_caret(text, 0) is None

    def test_the_form_is_read_where_it_starts(self):
        assert reduce_caret("\\wr^^69te", 3) == ("i", 4)

    @pytest.mark.parametrize(
        ("text", "written"),
        [
            ("\r", "^^M"),
            ("a\nb", "a^^Jb"),
            ("\x7f", "^^?"),
            ("\x00", "^^@"),
            ("\x85", "^^85"),
            ("\x9f", "^^9f"),
            ("é\xa0:", "é\xa0:"),
        ],
    )
    def test_control_characters_are_written_as_tex_prints_them(self, text, written):
        assert caret_notation(text) == written
