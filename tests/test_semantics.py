"""Meaning: what is properly matched but has no business where it is written."""

import pytest

from latexdetok import TexFile, expand
from latexdetok.semantics import read_meaning


def document(body):
    """`body` inside a whole document, starting at line 3."""
    return f"\\documentclass{{article}}\n\\begin{{document}}\n{body}\\end{{document}}\n"


@pytest.fixture
def meaning():
    def read(source, developed=False):
        tex = TexFile(source.splitlines(keepends=True))
        tex.analyse()
        return read_meaning(expand(tex) if developed else tex)

    return read


def described(diagnostics):
    return [(d.code, str(d.severity), d.start, d.end) for d in diagnostics]


def related(diagnostic):
    return [(item.start, item.message) for item in diagnostic.related]


class TestItem:
    def test_outside_any_list(self, meaning):
        (diagnostic,) = meaning(document("\\item a\n"))
        assert (diagnostic.code, diagnostic.start, diagnostic.end, diagnostic.message) == (
            "item-outside-list",
            (3, 0),
            (3, 5),
            "“\\item” outside any list",
        )

    def test_inside_an_environment_that_is_not_a_list(self, meaning):
        (diagnostic,) = meaning(document("\\begin{figure}\n\\item a\n\\end{figure}\n"))
        assert related(diagnostic) == [((3, 0), "inside “\\begin{figure}”, which is not a list")]

    @pytest.mark.parametrize(
        "source",
        [
            document("\\begin{center}\\item a\\end{center}\n"),
            document("\\begin{monenvironnement}\\item a\\end{monenvironnement}\n"),
            # Found in the corpus: `\vrbitem` opens the list.
            document("\\vrbitem\n\\item a\n"),
            "\\item a\n",
        ],
    )
    def test_a_list_is_possible_so_nothing_is_reported(self, meaning, source):
        assert meaning(source) == []


class TestAfterAStructureMistake:
    @pytest.mark.parametrize(
        "body",
        [
            "\\begin{itemize}\n\\item \\emph{a\n\\item b\n\\end{itemize}\n",
            "\\begin{itemze}\n\\item a\n\\end{itemize}\n",
        ],
    )
    def test_no_cascade(self, meaning, body):
        # The dissolved environment leaves its `\item` flat: the structure already reported the cause.
        assert meaning(document(body)) == []


class TestAmpersand:
    def test_outside_an_alignment(self, meaning):
        (diagnostic,) = meaning(document("a & b\n"))
        assert (diagnostic.code, diagnostic.start, diagnostic.suggestion) == (
            "ampersand-outside-alignment",
            (3, 2),
            "“\\&” for the character",
        )

    def test_inside_an_equation(self, meaning):
        (diagnostic,) = meaning(document("\\begin{equation} a & b \\end{equation}\n"))
        assert (diagnostic.start, related(diagnostic)) == (
            (3, 19),
            [((3, 0), "inside “\\begin{equation}”, which aligns nothing")],
        )

    @pytest.mark.parametrize(
        "body",
        [
            "\\begin{tabular}{cc}a & b\\end{tabular}\n",
            "\\begin{align}a & b\\end{align}\n",
            "\\begin{tblr}{cc}a & b\\end{tblr}\n",
            "\\catcode`\\&=12 a & b\n",
        ],
    )
    def test_an_alignment_or_an_ordinary_character(self, meaning, body):
        assert meaning(document(body)) == []


class TestModes:
    def test_superscript_and_subscript_outside_math(self, meaning):
        assert described(meaning(document("x^2 et a_b\n"))) == [
            ("script-outside-math", "error", (3, 1), (3, 2)),
            ("script-outside-math", "error", (3, 8), (3, 9)),
        ]

    def test_a_math_command_in_text(self, meaning):
        (diagnostic,) = meaning(document("l'angle \\alpha\n"))
        assert (diagnostic.code, diagnostic.start, diagnostic.message, diagnostic.suggestion) == (
            "math-command-in-text",
            (3, 8),
            "“\\alpha” outside math",
            "“$\\alpha$”",
        )

    @pytest.mark.parametrize(
        "body",
        [
            "$x^2$ et $\\alpha$\n",
            "\\ensuremath\\alpha\n",
            "\\label{eq_1} \\ce{H_2O}\n",
            "\\def\\deg{^\\circ}\n",
            "\\begin{tabular}{>{$}c<{$}}x^2\\end{tabular}\n",
            # Found in the corpus: a column type defined in a package.
            "\\begin{tabular}{lC}a & x_1\\end{tabular}\n",
            # beamer's `\alert`, redefined for other documents: in math, we do not know.
            "$\\textbf{\\varphi}$\n",
            "\\begin{tikzpicture}\\draw (0,0) -- (1,1)^2;\\end{tikzpicture}\n",
        ],
    )
    def test_math_or_an_unknown_mode(self, meaning, body):
        assert meaning(document(body)) == []


class TestPackages:
    def test_an_item_inside_a_box_is_reported(self, meaning):
        assert described(meaning(document("\\begin{tcolorbox}\\item a\\end{tcolorbox}\n"))) == [
            ("item-outside-list", "error", (3, 17), (3, 22))
        ]

    @pytest.mark.parametrize(
        "body",
        [
            # amsthm builds its environments on `trivlist`.
            "\\begin{proof}[Démonstration]\\item a\\end{proof}\n",
            # tabularray sets its columns by key-value pairs: their mode stays unknown.
            "\\begin{tblr}{cc}a & x^2\\end{tblr}\n",
            # A column type defined elsewhere (`\\newcolumntype{C}{>{$}c<{$}}`).
            "\\begin{longtable}{lC}a & x_1\\end{longtable}\n",
        ],
    )
    def test_what_packages_make_possible(self, meaning, body):
        assert meaning(document(body)) == []


class TestArguments:
    def test_a_missing_argument_before_the_end_of_the_environment(self, meaning):
        # Found in the corpus.
        (diagnostic,) = meaning(document("\\begin{equation}\na=\\frac{b}\n\\end{equation}\n"))
        assert (diagnostic.code, diagnostic.start, diagnostic.message, related(diagnostic)) == (
            "missing-argument",
            (4, 2),
            "“\\frac”: 1 mandatory argument missing out of 2",
            [((5, 0), "“\\end{equation}” comes before the argument")],
        )

    def test_a_blank_line_before_the_argument(self, meaning):
        (diagnostic,) = meaning(document("\\textbf\n\nsuite\n"))
        assert related(diagnostic) == [((4, 0), "the blank line stops the reading of arguments")]

    @pytest.mark.parametrize(
        "body",
        [
            "\\let\\a\\textbf\n",
            "{\\textbf}\n",
            "\\futurelet\\next\\test}\n",
        ],
    )
    def test_a_command_being_named_or_tokens_of_a_definition(self, meaning, body):
        assert meaning(document(body)) == []

    def test_an_unclosed_brace_already_reported(self, meaning):
        assert meaning(document("\\emph{a\n")) == []


class TestLeftRight:
    def test_left_without_right(self, meaning):
        (diagnostic,) = meaning(document("$\\left( x$\n"))
        assert (diagnostic.code, diagnostic.start, related(diagnostic)) == (
            "left-without-right",
            (3, 1),
            [((3, 9), "“$” closes the group first")],
        )

    def test_right_without_left(self, meaning):
        assert described(meaning(document("$x \\right)$\n"))) == [
            ("right-without-left", "error", (3, 3), (3, 9))
        ]

    def test_cut_by_a_cell_one_single_diagnostic(self, meaning):
        (diagnostic,) = meaning(document("\\begin{align}\\left( a & b \\right)\\end{align}\n"))
        assert (diagnostic.code, diagnostic.start, related(diagnostic)) == (
            "left-across-cells",
            (3, 22),
            [((3, 13), "“\\left” opened here")],
        )

    def test_branches_of_a_macro_in_the_same_stream(self, meaning):
        # `\pare` of the shorthands: `\ifstrempty{#1}{\left(}{#1(}#2\ifstrempty{#1}{\right)}{#1)}`.
        source = document(
            "\\newcommand{\\pare}[2][]{\\ifstrempty{#1}{\\left(}{#1(}#2\\ifstrempty{#1}{\\right)}{#1)}}\n$\\pare{x}$\n"
        )
        assert meaning(source, developed=True) == []


class TestLineBreak:
    @pytest.mark.parametrize(
        ("body", "reason"),
        [
            ("a\n\n\\\\ b\n", ((4, 0), "after the blank line, no paragraph has begun")),
            (
                "\\begin{center}a\\end{center}\\\\ b\n",
                ((3, 15), "“\\end{center}” ends between two paragraphs"),
            ),
        ],
    )
    def test_with_no_line_to_end(self, meaning, body, reason):
        (diagnostic,) = meaning(document(body))
        assert (diagnostic.code, related(diagnostic)) == ("line-break-without-line", [reason])

    def test_inside_a_paragraph_or_a_table(self, meaning):
        assert meaning(document("a\\\\ b\n\\begin{tabular}{c}\n\n\\\\\\end{tabular}\n")) == []


class TestTypos:
    def test_a_kernel_command(self, meaning):
        (diagnostic,) = meaning(document("\\sectoin{Titre}\n"))
        assert (diagnostic.code, str(diagnostic.severity), diagnostic.message) == (
            "unknown-command",
            "info",
            "“\\sectoin” unknown: “\\section”?",
        )

    def test_an_environment(self, meaning):
        (diagnostic,) = meaning(document("\\begin{itemzie}\\item a\\end{itemzie}\n"))
        assert (diagnostic.code, diagnostic.message) == (
            "unknown-environment",
            "environment “itemzie” unknown: “itemize”?",
        )

    @pytest.mark.parametrize(
        "body",
        [
            "$\\dfrac12$ \\xspace\n",  # package commands, one letter from the kernel
            "$\\rvert x \\rVert$\n",  # case alone
            "\\sectoin{a} \\sectoin{b}\n",  # used twice: a command, not a typo
        ],
    )
    def test_what_is_not_a_typo(self, meaning, body):
        assert [d for d in meaning(document(body)) if d.code == "unknown-command"] == []
