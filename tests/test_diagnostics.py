"""Structure diagnostics: one per cause, with the opening, what cut it short and the likely cause."""

import json

import pytest

from latexdetok import Severity, TexDiagnostic
from latexdetok.diagnostics import Related, dumps, is_fragment, render_text, similar

DOCUMENT = "\\documentclass{article}\n\\begin{document}\n{}\\end{document}\n"


def document(body):
    """`body` inside a whole document, starting at line 3."""
    return f"\\documentclass{{article}}\n\\begin{{document}}\n{body}\\end{{document}}\n"


def described(tex):
    return [(d.code, str(d.severity), d.start, d.end) for d in tex.diagnostics]


def related(diagnostic):
    return [(item.start, item.message) for item in diagnostic.related]


class TestBraces:
    def test_a_brace_forgotten_before_an_end_one_single_diagnostic(self, parse):
        tex = parse(document("\\begin{itemize}\n\\item \\emph{a\n\\end{itemize}\n"))
        (diagnostic,) = tex.diagnostics
        assert (
            diagnostic.code,
            diagnostic.severity,
            diagnostic.start,
            diagnostic.end,
            diagnostic.message,
        ) == (
            "unclosed-brace",
            Severity.ERROR,
            (4, 11),
            (4, 12),
            "“{” of “\\emph” never closed",
        )
        assert related(diagnostic) == [((5, 0), "“\\end{itemize}” comes before it is closed")]

    def test_a_brace_fallen_into_a_comment(self, parse):
        # Found in the corpus: the “}” of the `\emph{` is in the commented line that follows.
        tex = parse(document("« Force vive » : \\emph{C'est celle\n%et qui produit un effet}\n\nSuite.\n"))
        (diagnostic,) = tex.diagnostics
        assert related(diagnostic) == [
            ((4, 0), "a “}” in this comment, line 4"),
            ((5, 0), "first blank line of the group, line 5: the brace is probably missing before it"),
            ((7, 0), "“\\end{document}” comes before it is closed"),
        ]

    def test_an_extra_brace_shows_the_previous_one(self, parse):
        # Found in the corpus: the `\rmxex{` is already closed at the end of the line.
        tex = parse(document("\\rmxex{\n\\item a}\n\\item b\n}\n"))
        (diagnostic,) = tex.diagnostics
        assert (diagnostic.code, diagnostic.start, related(diagnostic)) == (
            "extra-brace",
            (6, 0),
            [((4, 7), "the previous “{” (opened line 3) is already closed here")],
        )

    def test_an_unclosed_brace_at_the_end_of_a_fragment_warns(self, parse):
        assert described(parse("\\section{Titre\n")) == [("unclosed-brace", "warning", (1, 8), (1, 9))]


class TestEnvironments:
    def test_a_typo(self, parse):
        tex = parse(document("\\begin{itemze}\n\\item a\n\\end{itemize}\n"))
        (diagnostic,) = tex.diagnostics
        assert (
            diagnostic.code,
            diagnostic.severity,
            diagnostic.start,
            diagnostic.message,
            related(diagnostic),
            diagnostic.suggestion,
        ) == (
            "crossed-environment",
            Severity.ERROR,
            (5, 0),
            "“\\begin{itemze}” closed by “\\end{itemize}”",
            [((3, 0), "opened here")],
            "“\\end{itemze}”, or “\\begin{itemize}”",
        )

    def test_crossed_environments(self, parse):
        tex = parse(document("\\begin{center}\\begin{minipage}{1cm}\n\\end{center}\n\\end{minipage}\n"))
        (diagnostic,) = tex.diagnostics
        assert (diagnostic.code, diagnostic.start, diagnostic.end, related(diagnostic)) == (
            "crossed-environments",
            (4, 0),
            (4, 12),
            [((3, 14), "“\\begin{minipage}” opened here"), ((5, 0), "closed here, too late")],
        )

    def test_unknown_crossed_environments_warn(self, parse):
        # Found in the corpus, in a file that compiles: beamer reads the body of the frame itself.
        tex = parse(document("\\begin{beamersubsectiontop}{}\nx\n\\end{beamersubsection}\n"))
        assert described(tex) == [("crossed-environment", "warning", (5, 0), (5, 22))]

    def test_an_environment_non_ferme_coupe_par_l_accolade(self, parse):
        (diagnostic,) = parse(document("{\\begin{center}x}\n")).diagnostics
        assert (diagnostic.code, diagnostic.start, related(diagnostic)) == (
            "unclosed-environment",
            (3, 1),
            [((3, 16), "“}” closes the group around it")],
        )

    def test_an_end_without_begin_in_a_document(self, parse):
        assert described(parse(document("\\end{center}\n"))) == [
            ("end-without-begin", "error", (3, 0), (3, 12))
        ]

    def test_an_end_without_begin_in_a_fragment_warns(self, parse):
        # An included file may close what its master opened.
        assert described(parse("\\end{fig}\n")) == [("end-without-begin", "warning", (1, 0), (1, 9))]

    def test_a_chapter_that_declares_its_master_is_not_a_fragment(self, parse):
        tex = parse("% !TEX root = ../cours.tex\n\\end{center}\n")
        assert described(tex) == [("end-without-begin", "error", (2, 0), (2, 12))]


class TestMath:
    def test_a_blank_line_inside_math(self, parse):
        (diagnostic,) = parse(document("Soit $x\n\nla suite.\n")).diagnostics
        assert (diagnostic.code, diagnostic.start, related(diagnostic), diagnostic.suggestion) == (
            "unclosed-math",
            (3, 5),
            [((4, 0), "the blank line cuts the math")],
            "close it with “$” before the blank line",
        )

    @pytest.mark.parametrize(
        ("body", "expected"),
        [
            ("\\[ x $ y \\]\n", [("dollar-in-math", "error", (3, 5), (3, 6))]),
            ("\\[ x $$ y \\]\n", [("dollar-in-math", "error", (3, 5), (3, 7))]),
            ("\\[ x = $y$ \\]\n", [("dollar-in-math", "error", (3, 7), (3, 8))]),
            ("$a \\[ b$\n", [("math-in-math", "error", (3, 3), (3, 5))]),
            ("a \\]\n", [("stray-math-close", "error", (3, 2), (3, 4))]),
            ("$\\text{\\[}$\n", []),
        ],
    )
    def test_delimiters(self, parse, body, expected):
        assert described(parse(document(body))) == expected

    def test_a_redefined_bracket_is_not_reported(self, parse):
        assert parse(document("\\def\\[{[}\\def\\]{]}\n$x\\in\\[0;1\\]$\n")).diagnostics == []


class TestBrackets:
    def test_a_bracket_expected_by_a_signature(self, parse):
        (diagnostic,) = parse(document("\\item[0 ; 1\n\nsuite\n")).diagnostics
        assert (diagnostic.code, diagnostic.start, diagnostic.message) == (
            "unclosed-bracket",
            (3, 5),
            "“[” of “\\item” never closed",
        )

    def test_a_guessed_bracket_is_not_reported(self, parse):
        # `\og` is unknown: its `[` only opens an argument by heuristic, it may be an interval.
        assert parse(document("\\og [0;1[\n\nsuite ]\n")).diagnostics == []


class TestVerbatim:
    def test_a_verbatim_never_closed(self, parse):
        assert described(parse(document("\\begin{verbatim}\nx\n"))) == [
            ("unclosed-environment", "error", (2, 0), (2, 16)),
            ("unclosed-verbatim", "error", (3, 0), (3, 16)),
        ]

    def test_verb_stops_at_the_end_of_the_line(self, parse):
        tex = parse(document("\\verb|x\n|y|\n"))
        verbatim = tex.container[2][0]
        assert (described(tex), verbatim.content, str(verbatim)) == (
            [("verb-across-lines", "error", (3, 0), (3, 6))],
            "x",
            "\\verb|x",
        )

    def test_a_url_over_two_lines_is_still_read(self, parse):
        assert parse(document("\\url{http://\nexemple}\n")).diagnostics == []


class TestContext:
    @pytest.mark.parametrize(
        "source",
        [
            "\\def\\AMCbeginAnswer{\\begin{minipage}{5cm}}\n",
            "\\newenvironment{sol}{\\begin{proof}}{\\end{proof}}\n",
            "\\newcommand{\\dbl}{$$}\n",
        ],
    )
    def test_the_body_of_a_definition_as_an_info(self, parse, source):
        tex = parse(document(source))
        assert tex.diagnostics and {d.severity for d in tex.diagnostics} == {Severity.INFO}

    def test_the_argument_of_an_unknown_command_warns(self, parse):
        # Found in the corpus: a pgfplotstable key keeps the code for later.
        tex = parse(document("\\pgfplotstabletypeset[begin table={\\begin{longtable}{ll}}]{x.csv}\n"))
        assert described(tex) == [("unclosed-environment", "warning", (3, 35), (3, 52))]

    def test_an_argument_that_is_not_typeset_as_an_info(self, parse):
        # Found in the corpus: the argument goes off to Lua, `$` opens no math there.
        tex = parse(document('\\directlua{os.execute("echo $PATH")}\n'))
        assert {d.severity for d in tex.diagnostics} == {Severity.INFO}

    def test_the_stream_of_a_write_is_not_typeset(self, parse):
        tex = parse(document("\\immediate\\write18{echo $HOME}\n"))
        assert {d.severity for d in tex.diagnostics} == {Severity.INFO}

    def test_a_column_specification_is_silent(self, parse):
        assert (
            parse(
                document("\\begin{tabular}{>{$}c<{$}}x\\end{tabular}\n\\newcolumntype{C}{>{$}c<{$}}\n")
            ).diagnostics
            == []
        )

    def test_nothing_inside_a_discarded_branch(self, parse):
        from latexdetok import expand

        developed = expand(parse(document("\\iffalse\\begin{center}\\fi\n")))
        assert developed.diagnostics == []


class TestRenderings:
    DIAGNOSTIC = TexDiagnostic(
        "unclosed-brace",
        Severity.ERROR,
        "“{” of “\\emph” never closed",
        (2, 6),
        (2, 7),
        (Related((3, 0), (3, 13), "“\\end{itemize}” comes before it is closed"),),
        "close it with “}”",
    )
    LINES = ("\\begin{itemize}\n", "\\item\t\\emph{a\n", "\\end{itemize}\n")

    def test_the_string(self):
        assert str(self.DIAGNOSTIC) == "2:7: error [unclosed-brace] “{” of “\\emph” never closed"

    def test_the_text_underlines_the_span_and_the_related_places(self):
        assert render_text([self.DIAGNOSTIC], self.LINES, "cours.tex") == (
            "cours.tex:2:7: error [unclosed-brace] “{” of “\\emph” never closed\n"
            "     2 │ \\item \\emph{a\n"
            "       │       ^\n"
            "     3 │ \\end{itemize}\n"
            "       │ ^~~~~~~~~~~~~ “\\end{itemize}” comes before it is closed\n"
            "       = close it with “}”"
        )

    def test_a_long_line_is_shown_around_the_column(self):
        line = "x" * 300 + "{" + "y" * 300 + "\n"
        diagnostic = TexDiagnostic("unclosed-brace", Severity.ERROR, "m", (1, 300), (1, 301))
        excerpt = render_text([diagnostic], [line], "long.tex").splitlines()
        assert (len(excerpt[1]) < 120, excerpt[1][9], excerpt[2].index("^") - excerpt[1].index("{")) == (
            True,
            "…",
            0,
        )

    def test_json_with_columns_from_one(self):
        (entry,) = json.loads(dumps([self.DIAGNOSTIC], "cours.tex"))
        assert (entry["line"], entry["column"], entry["end_column"], entry["related"][0]["column"]) == (
            2,
            7,
            8,
            1,
        )


class TestTools:
    @pytest.mark.parametrize(
        ("first", "second", "expected"),
        [
            ("itemze", "itemize", True),
            ("enumerate", "enumreate", True),
            ("center", "centre", True),
            ("figure", "table", False),
            ("a", "b", False),
            ("frame", "frame", False),
        ],
    )
    def test_neighbouring_names(self, first, second, expected):
        assert similar(first, second) is expected

    @pytest.mark.parametrize(
        ("lines", "document", "expected"),
        [
            (["\\section{A}\n"], False, True),
            (["% !TEX root = ./cours.tex\n"], False, False),
            (["\\section{A}\n"], True, False),
        ],
    )
    def test_fragment(self, lines, document, expected):
        assert is_fragment(lines, document) is expected
