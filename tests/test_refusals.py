"""What a piece of text may be, wherever it stands: a length, a table specification, a parameter."""

import pytest

from latexdetok.refusals import Columns, columns, is_length, parameters, xparse_count


class TestLength:
    @pytest.mark.parametrize(
        "text",
        [
            "2pt",
            "-1ex",
            "1 cm",
            ".5em",
            "3,5cm",
            "0pt plus 1fill",
            "1em plus 2pt minus 1pt",
            "2 truept",
            "\\baselineskip",
            "0.5\\linewidth",
            "-\\parindent",
            "\\stretch{1}",
        ],
    )
    def test_a_length(self, text):
        assert is_length(text) is True

    @pytest.mark.parametrize("text", ["", "2", "3.", "0,1", "x,y", "abc", "1 c m"])
    def test_not_a_length(self, text):
        assert is_length(text) is False

    def test_a_macro_may_write_any_length(self):
        assert is_length("\\mylength + 2pt") is None


class TestColumns:
    @pytest.mark.parametrize(
        ("spec", "count"),
        [
            ("cc", 2),
            ("|c|c|", 2),
            ("@{}lcr@{}", 3),
            ("p{2cm}c", 2),
            ("*{3}{c}", 3),
            ("*{2}{c|}l", 3),
            ("l@{ : }r", 2),
        ],
    )
    def test_the_columns_of_the_kernel(self, spec, count):
        assert columns(spec) == Columns(count)

    def test_array_adds_its_letters(self):
        assert (columns("m{1cm}>{\\bfseries}c", array=True), columns("m{1cm}")) == (
            Columns(2),
            Columns(None, ((0, "m"),)),
        )

    def test_a_package_adds_its_own(self):
        assert columns("SD{.}{.}{2}", {"S": 0, "D": 3}) == Columns(2)

    def test_an_unknown_letter_is_located(self):
        assert columns("cxq") == Columns(None, ((1, "x"), (2, "q")))

    def test_a_command_in_the_specification_cannot_be_counted(self):
        assert columns("c\\mycolumns") == Columns(None)


class TestParameters:
    def test_the_parameters_of_a_body(self):
        assert list(parameters("#1 et #2")) == [(0, 1), (6, 2)]

    def test_a_doubled_one_belongs_to_a_definition_inside(self):
        assert list(parameters("\\def\\b##1{##1#1}")) == [(13, 1)]


class TestXparse:
    @pytest.mark.parametrize(
        ("spec", "count"),
        [("m", 1), ("O{a}m", 2), ("s o m", 3), ("+m", 1), ("d()m", 2), ("D<>{x}m", 2), ("t+m", 2), ("", 0)],
    )
    def test_the_arguments_of_a_specification(self, spec, count):
        assert xparse_count(spec) == count

    def test_what_it_does_not_follow(self):
        assert xparse_count("e{^_}m") is None
