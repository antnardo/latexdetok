"""Meaning: what is properly matched but has no business where it is written."""

import json
from pathlib import Path

import pytest

from latexdetok import Severity, TexFile, expand
from latexdetok.semantics import read_meaning

# What LaTeX refuses, case by case, each one compiled by LuaLaTeX (`scripts/oracle.py`).
TABLE = json.loads((Path(__file__).parent / "fixtures" / "refused.json").read_text(encoding="utf-8"))


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
            # Found in the corpus: a column type defined in a package (here, in the document:
            # loading nothing, the document would have `C` refused, see `TestTables`).
            "\\newcolumntype{C}{>{$}c<{$}}\\begin{tabular}{lC}a & x_1\\end{tabular}\n",
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
            # A column type defined elsewhere.
            "\\newcolumntype{C}{>{$}c<{$}}\\begin{longtable}{lC}a & x_1\\end{longtable}\n",
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


class TestInvalidInMath:
    """What LaTeX guards with `\\@inmatherr`: an error of its own, not a matter of taste.

    A `$` is an opening and a closing at once, so a forgotten one does not leave
    math open where it was forgotten: it pairs with the next `$`. With two
    forgotten, the count is even again and the prose between them is read as
    math — nothing was reported, and the file does not compile.
    """

    def test_an_item_that_math_swallowed(self, meaning):
        # `\item` is the second one: the first `$` pairs with the `$` of the next item.
        (diagnostic,) = meaning(
            document("\\begin{itemize}\n\\item Soit $x\n\\item et $y ici\n\\end{itemize}\n")
        )
        assert (diagnostic.code, diagnostic.start, diagnostic.message) == (
            "invalid-in-math",
            (5, 0),
            "“\\item” inside math, where LaTeX refuses it",
        )

    def test_it_points_at_the_math_still_open(self, meaning):
        (diagnostic,) = meaning(
            document("\\begin{itemize}\n\\item Soit $x\n\\item et $y ici\n\\end{itemize}\n")
        )
        assert related(diagnostic) == [((4, 11), "the math opened by “$” is still open here")]

    def test_a_list_that_closes_its_math_says_nothing(self, meaning):
        assert meaning(document("\\begin{itemize}\n\\item Soit $x$\n\\item et $y$\n\\end{itemize}\n")) == []

    def test_an_item_outside_a_list_is_still_that_one(self, meaning):
        # In text the reading is the other: the list is missing, not a `$`.
        (diagnostic,) = meaning(document("\\item a\n"))
        assert diagnostic.code == "item-outside-list"

    def test_a_command_latex_refuses_in_math(self, meaning):
        (diagnostic,) = meaning(document("$x \\circle{4} y$\n"))
        assert (diagnostic.code, diagnostic.start) == ("invalid-in-math", (3, 3))

    def test_the_same_command_in_text_says_nothing(self, meaning):
        assert meaning(document("\\begin{picture}(4,4)\n\\circle{4}\n\\end{picture}\n")) == []

    def test_nothing_where_the_mode_is_a_guess(self, meaning):
        # Inside an unknown environment the mode is not known — a package may typeset its
        # body in math (`tblr`) — and reporting there would invent errors by the hundred.
        assert meaning(document("\\begin{inconnu}\n\\item a\n\\end{inconnu}\n")) == []

    def test_an_explicit_dollar_is_math_wherever_it_is_written(self, meaning):
        # The mode of the environment is a guess; a `$` opened by hand is not.
        (diagnostic,) = meaning(document("\\begin{inconnu}\n$\\item a$\n\\end{inconnu}\n"))
        assert diagnostic.code == "invalid-in-math"


class TestDoubleScripts:
    """Each case compiled with LuaLaTeX: the refused ones stop on “Double subscript” or “superscript”."""

    @pytest.mark.parametrize(
        ("formula", "code"),
        [
            ("$x_a_b$", "double-subscript"),
            ("$\\Sigma_G_1$", "double-subscript"),
            ("$\\vec{u}_z_0$", "double-subscript"),
            ("$x_a^b_c$", "double-subscript"),
            ("$x^a^b$", "double-superscript"),
            ("$x^a_b^c$", "double-superscript"),
            ("$f^2'$", "double-superscript"),
            ("$x^{\\prime}^2$", "double-superscript"),
            ("$x^\\prime^2$", "double-superscript"),
            ("$\\int\\limits_{S}_{T}$", "double-subscript"),
            ("$\\int_a\\nolimits_b$", "double-subscript"),
            ("$x_{a}_{b}$", "double-subscript"),
            ("$x_\\mathrm{a}_b$", "double-subscript"),
            ("$x_\\text{a}_b$", "double-subscript"),
            ("$x'_1^2$", "double-superscript"),
            ("$f' ^2$", "double-superscript"),
            ("$x' '$", "double-superscript"),
            ("$x_a _b$", "double-subscript"),
            ("$x_a%\n_b$", "double-subscript"),
            ("$_a_b$", "double-subscript"),
            ("$x_a\\relax_b$", "double-subscript"),
            ("$\\left(x\\right)_a_b$", "double-subscript"),
            ("$\\frac{a}{b}_c_d$", "double-subscript"),
            ("$\\sqrt[3]{x}_a_b$", "double-subscript"),
            ("$\\frac{x_a_b}{c}$", "double-subscript"),
            ("$x^a\\sp b$", "double-superscript"),
            ("\\begin{equation} x_a_b \\end{equation}", "double-subscript"),
        ],
    )
    def test_refused_by_tex(self, meaning, formula, code):
        assert [diagnostic.code for diagnostic in meaning(document(formula + "\n"))] == [code]

    @pytest.mark.parametrize(
        "formula",
        [
            "$x_a^b$",
            "$\\sum_{i}^{n}$",
            "$\\int\\limits_a^b$",
            "$\\sum\\nolimits_{i}^{n}$",
            "$f'^2$",
            "$x''^2$",
            "$x'_1 + x_1'$",
            "$x'%\n^2$",
            "${x_a}_b$",
            "$x_{a_b}$",
            "$x_a {}_b$",
            "$T^{a}{}_{b}$",
            "${}^{14}_{6}C$",
            "$x_a\\,_b$",
            "$x_a\\quad_b$",
            "$x_a~_b$",
            "$x_a\\displaystyle_b$",
            "$x_a\\alpha_b$",
            "$x_a\\mathrm{b}_c$",
            "$x_\\neq_b$",
            "$x_\\mapsto_b$",
            "\\begin{align} x_a & _b \\\\ _c \\end{align}",
        ],
    )
    def test_accepted_by_tex(self, meaning, formula):
        assert meaning(document(formula + "\n")) == []

    def test_the_second_script_is_at_fault_and_the_first_is_shown(self, meaning):
        (diagnostic,) = meaning(document("$\\iint\\limits_{\\Sigma_G_1} \\vec{E}\\cdot d\\vec{S}$\n"))
        assert (
            diagnostic.start,
            diagnostic.end,
            diagnostic.message,
            related(diagnostic),
            diagnostic.suggestion,
        ) == (
            (3, 23),
            (3, 24),
            "two subscripts in a row on the same symbol",
            [((3, 21), "its first subscript")],
            "“x_{a,b}”, “x_{a_b}” or “{x_a}_b”",
        )

    def test_a_prime_is_shown_first(self, meaning):
        (diagnostic,) = meaning(document("$f^2'$\n"))
        assert (diagnostic.start, diagnostic.suggestion) == (
            (3, 4),
            "the prime first, being a superscript itself: “f'^2”",
        )

    def test_each_extra_script_is_reported_as_tex_does(self, meaning):
        assert [d.start for d in meaning(document("$x_a_b_c$\n"))] == [(3, 4), (3, 6)]

    def test_an_unknown_command_as_argument_may_write_several_atoms(self, meaning):
        assert meaning(document("$x_\\inconnue_b$\n")) == []

    def test_a_redefined_sp_is_not_a_superscript(self, meaning):
        assert meaning(document("\\renewcommand\\sp{s}\n$x^a\\sp b$\n")) == []

    def test_an_underscore_that_is_no_longer_a_subscript(self, meaning):
        assert meaning(document("\\catcode`\\_=12\n$x_a_b$\n")) == []

    def test_text_in_math_is_not_a_math_list(self, meaning):
        # Its mode is not known either (see `TestModes`): nothing is said of it.
        assert meaning(document("$\\text{a_b_c}$\n")) == []

    def test_a_macro_writes_what_it_writes(self, meaning):
        source = document("\\newcommand\\SG{\\Sigma_G}\n$\\SG_1$\n")
        assert [d.code for d in meaning(source, developed=True)] == ["double-subscript"]


class TestWhatCloudsAList:
    """An unknown command may have opened a list (`\\vrbitem`), and then `\\item` is not judged.

    That guard is right, and it was firing on `\\%`: a backslash and one character
    that is not a letter is a character, not a macro. One `40\\%` in a paper and
    the rest of the document went unchecked.
    """

    @pytest.mark.parametrize("escaped", ["\\%", "\\&", "\\#", "\\_", "\\$", "\\{a\\}", "\\,", "\\;"])
    def test_an_escaped_character_clouds_nothing(self, meaning, escaped):
        (diagnostic,) = meaning(document(f"40{escaped} de la copie\n\\item a\n"))
        assert diagnostic.code == "item-outside-list"

    @pytest.mark.parametrize("symbol", ["\\degree", "\\celsius", "\\ohm", "\\micro"])
    def test_the_symbols_of_gensymb_are_known(self, meaning, symbol):
        (diagnostic,) = meaning(document(f"20{symbol}\n\\item a\n"))
        assert diagnostic.code == "item-outside-list"

    @pytest.mark.parametrize(
        "command", ["\\relax", "\\hskip 1em", "\\kern 2pt", "\\penalty10000", "\\leavevmode", "\\strut"]
    )
    def test_a_primitive_or_a_kernel_command_opens_nothing(self, meaning, command):
        # They were unknown: every one of them stopped the check for the rest of its group.
        assert [d.code for d in meaning(document(f"{command}\n\\item x\n"))] == ["item-outside-list"]

    def test_a_command_nobody_defined_still_clouds_it(self, meaning):
        # The guard is kept: `\vrbitem` of the corpus really does open a list.
        assert meaning(document("\\vrbitem\n\\item a\n")) == []


def table_source(case):
    if "full" in case:
        return case["full"]
    template = TABLE["template"]
    return template.replace("{preamble}", case.get("preamble", "")).replace("{body}", case.get("body", ""))


def table_cases(refused):
    return [
        pytest.param(case["rule"], table_source(case), id=f"{case['rule']}-{index}")
        for index, case in enumerate(TABLE["cases"])
        if case["refused"] is refused
    ]


class TestWhatLatexRefuses:
    """The cases of `fixtures/refused.json`: the refused ones reported, the others left alone."""

    @pytest.mark.parametrize(("rule", "source"), table_cases(True))
    def test_refused_by_tex_is_reported(self, meaning, rule, source):
        assert rule in [diagnostic.code for diagnostic in meaning(source)]

    @pytest.mark.parametrize(("rule", "source"), table_cases(False))
    def test_accepted_by_tex_is_left_alone(self, meaning, rule, source):
        assert [d.code for d in meaning(source) if d.severity is Severity.ERROR] == []

    def test_a_brace_after_left_is_one_mistake(self, meaning):
        # Not three: the brace opens a group, which takes `\\right` away from its `\\left`.
        codes = [d.code for d in meaning(document("$\\left{ x \\right}$\n"))]
        assert codes == ["missing-delimiter", "missing-delimiter"]

    def test_a_size_passed_to_another_command_is_no_delimiter(self, meaning):
        # Found in the corpus: `\\pare[\\big]{x}`, expanded by etoolbox's test of its size.
        body = "$\\ifstrempty{\\big}{\\left(}{\\big(}x\\ifstrempty{\\big}{\\right)}{\\big)}$\n"
        assert meaning(document(body), developed=True) == []

    def test_url_may_sit_in_an_argument(self, meaning):
        assert meaning(document("\\footnote{\\url{http://example.org/a_b}}\n")) == []

    def test_an_unknown_command_before_the_first_item_may_be_one(self, meaning):
        assert meaning(document("\\begin{itemize}\\itemdef texte\\end{itemize}\n")) == []

    def test_futurelet_peeks_at_the_end_of_the_row(self, meaning):
        # Found in the corpus: a `\\footnote` redefined to end on `\\futurelet\\nextToken\\isFootnote`.
        body = "\\begin{tabular}{ll}a & b\\futurelet\\n\\t\\\\\nc & d\\\\\n\\end{tabular}\n"
        assert meaning(document(body)) == []

    def test_a_preamble_read_elsewhere_hides_its_column_types(self, meaning):
        source = "\\documentclass{article}\n\\input{definitions}\n\\begin{document}\n\\begin{tabular}{S}1\\end{tabular}\n\\end{document}\n"
        assert meaning(source) == []

    def test_text_a_test_leaves_in_the_preamble_is_not_typeset(self, meaning):
        source = "\\documentclass{article}\n\\def\\O2couple{x}\n\\begin{document}\nx\n\\end{document}\n"
        assert meaning(source) == []

    def test_two_labels_in_equation_without_amsmath(self, meaning):
        # The kernel's `equation` takes both: only amsmath refuses the second.
        assert meaning(document("\\begin{equation} a \\label{x}\\label{y} \\end{equation}\n")) == []

    def test_a_class_of_its_own_may_define_a_column_type(self, meaning):
        source = "\\documentclass{macours}\n\\begin{document}\n\\begin{tabular}{Y}1\\end{tabular}\n\\end{document}\n"
        assert meaning(source) == []

    def test_the_second_label_points_at_the_first(self, meaning):
        source = "\\documentclass{article}\n\\usepackage{amsmath}\n\\begin{document}\n\\begin{equation} a \\label{x}\\label{y} \\end{equation}\n\\end{document}\n"
        (diagnostic,) = meaning(source)
        assert (diagnostic.code, diagnostic.start, related(diagnostic)) == (
            "multiple-labels",
            (4, 28),
            [((4, 19), "its first label")],
        )
