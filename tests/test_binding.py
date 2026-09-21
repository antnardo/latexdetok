"""Binding the arguments through signatures, as reading goes on."""

import pytest

from latexdetok import TexCommand, TexFile


def commands(tex, name):
    return [node for node in tex.iter() if isinstance(node, TexCommand) and node.base_name == name]


def bound(command):
    return (
        None
        if command.arguments is None
        else [None if node is None else str(node) for node in command.arguments]
    )


class TestMilestoneCriteria:
    def test_the_short_title_of_a_section(self, parse):
        (section,) = commands(parse("\\section*[court]{Titre long}\n"), "section")
        assert (section.star, bound(section)) == (True, ["[court]", "{Titre long}"])

    def test_a_starred_line_break_with_spacing(self, parse):
        (line_break,) = commands(parse("a\\\\*[2mm]\n"), "\\")
        assert (line_break.star, bound(line_break)) == (True, ["[2mm]"])

    def test_minipage(self, parse):
        (minipage,) = parse("\\begin{minipage}[t]{3cm}x\\end{minipage}\n").get_envs("minipage")
        assert [None if node is None else str(node) for node in minipage.arguments] == [
            "[t]",
            None,
            None,
            "{3cm}",
        ]

    @pytest.mark.parametrize(
        ("source", "text"), [("node[above]\n", "node[above]"), ("voir [1]\n", "voir [1]")]
    )
    def test_brackets_after_text_stay_text(self, tree, source, text):
        assert tree(source) == [text]


class TestArguments:
    def test_a_missing_optional(self, parse):
        (section,) = commands(parse("\\section{Titre}\n"), "section")
        assert bound(section) == [None, "{Titre}"]

    def test_a_comment_is_skipped(self, parse):
        (section,) = commands(parse("\\section%\n{Titre}\n"), "section")
        assert bound(section) == [None, "{Titre}"]

    def test_an_argument_without_braces_is_one_character(self, parse):
        (frac,) = commands(parse("$\\frac12$\n"), "frac")
        assert bound(frac) == ["1", "2"]

    def test_text_cut_without_changing_the_rewriting(self, parse, tree):
        assert (tree("$\\frac123$\n"), parse("$\\frac123$\n").content()) == (
            [("$", ["\\frac", "1", "2", "3"])],
            "$\\frac123$",
        )

    def test_a_command_taken_as_a_token(self, parse):
        (frac,) = commands(parse("$\\frac\\alpha\\beta$\n"), "frac")
        assert bound(frac) == ["\\alpha", "\\beta"]

    def test_a_token_that_is_not_called(self, parse):
        tex = parse("\\let\\titre\\section\n")
        (section,) = commands(tex, "section")
        assert (bound(section), section.missing_arguments()) == (None, [])

    def test_a_blank_line_leaves_the_argument_missing(self, parse):
        (section,) = commands(parse("\\section\n\ntexte\n"), "section")
        assert ([spec.kind for spec in section.missing_arguments()], bound(section)) == (["m"], [None, None])

    @pytest.mark.parametrize(
        "source",
        [
            "\\titleformat{\\section}[runin]{x}\n",
            "\\renewcommand{\\section}{X}\n",
            "\\texttt{\\~}\n",
            "\\foo[\\section]{x}\n",
        ],
    )
    def test_a_command_alone_in_its_braces_is_being_named(self, parse, source):
        # Found in the corpus: titlesec, \forcsvlist{\cmd}, \texttt{\~}.
        named = [
            node
            for node in parse(source).iter()
            if isinstance(node, TexCommand) and node.signature is not None
        ]
        assert [node.missing_arguments() for node in named if node.base_name in ("section", "~")] == [[]]

    def test_a_long_argument_takes_the_blank_line(self, parse):
        # Found in the corpus: \newcommand{\methode}[2] is a long macro.
        tex = parse("\\newcommand{\\methode}[2]{#1#2}\n\\methode{Titre}\n\ntexte\n")
        (methode,) = [
            node
            for node in tex.iter()
            if isinstance(node, TexCommand) and node.base_name == "methode" and node.arguments is not None
        ]
        assert (methode.missing_arguments(), methode.arguments[1].is_par()) == ([], True)

    def test_a_short_argument_does_not_take_the_blank_line(self, parse):
        tex = parse("\\newcommand*{\\methode}[2]{#1#2}\n\\methode{Titre}\n\ntexte\n")
        (methode,) = [
            node
            for node in tex.iter()
            if isinstance(node, TexCommand) and node.base_name == "methode" and node.arguments is not None
        ]
        assert [spec.kind for spec in methode.missing_arguments()] == ["m"]

    def test_the_end_of_a_group_leaves_the_argument_missing(self, parse):
        (frac,) = commands(parse("{\\frac{a}}\n"), "frac")
        assert bound(frac) == ["{a}", None]

    def test_end_is_never_taken_as_an_argument(self, parse, tree):
        # Found in the corpus: `\frac{…}` with no denominator.
        tex = parse("\\begin{equation}\na=\\frac{b}\n\\end{equation}\n")
        (frac,) = commands(tex, "frac")
        assert (bound(frac), tex.diagnostics, tree("\\begin{equation}\\frac{b}\\end{equation}\n")[0][0]) == (
            ["{b}", None],
            [],
            "equation",
        )

    def test_an_optional_token(self, parse):
        (let,) = commands(parse("\\let\\a=\\b\n"), "let")
        assert bound(let) == ["\\a", "=", "\\b"]

    def test_a_delimited_argument(self, parse):
        (put,) = commands(parse("\\put(1, 2){x}\n"), "put")
        assert (bound(put), put.arguments[0].raw_text()) == (["(1, 2)", "{x}"], "(1, 2)")

    def test_a_verbatim_argument(self, parse):
        tex = parse("\\verb*|a b| c\n")
        assert (str(tex.container[0]), tex.container[0].is_verbatim()) == ("\\verb*|a b|", True)

    def test_an_unbound_type_stops_the_binding(self, parse):
        tex = TexFile(["\\NewDocumentCommand\\x{m e{^_}}{}\n", "\\x{a}^b\n"])
        tex.analyse()
        (x,) = [node for node in commands(tex, "x") if node.arguments is not None]
        assert bound(x) == ["{a}", None]


class TestParameterText:
    """`l`: everything up to the brace, for `\\def\\x#1#2{…}`."""

    @pytest.mark.parametrize(
        ("source", "arguments"),
        [
            ("\\def\\x#1#2{X}\n", ["\\x", "#1#2", "{X}"]),
            ("\\def\\x{X}\n", ["\\x", None, "{X}"]),
            ("\\def\\x[#1]{X}\n", ["\\x", "[#1]", "{X}"]),
            ("\\def\\x#1\\relax#2{X}\n", ["\\x", "#1", "{X}"]),
            ("\\def\\x$#1${X}\n", ["\\x", None, None]),
        ],
    )
    def test_the_first_element_is_kept(self, parse, source, arguments):
        (definition,) = commands(parse(source), "def")
        assert bound(definition) == arguments

    def test_a_blank_line_stops_the_parameter_text(self, parse):
        # With no brace, the parameter text is not over: neither it nor the body is found.
        (definition,) = commands(parse("\\def\\x#1\n\nsuite{X}\n"), "def")
        assert bound(definition) == ["\\x", None, None]


class TestBrackets:
    def test_a_signature_with_no_option(self, tree):
        # \centering has no argument: the bracket that follows is text.
        assert tree("\\centering [voir]\n") == ["\\centering", "[voir]"]

    def test_a_group_that_is_the_argument_of_a_known_command(self, tree):
        assert tree("\\textbf{a} [1]\n") == ["\\textbf", ("{", ["a"]), "[1]"]

    def test_an_unknown_command_keeps_the_heuristic(self, tree):
        assert tree("\\includegraphics[width=3cm]{f}\n") == [
            "\\includegraphics",
            ("[", ["width=3cm"]),
            ("{", ["f"]),
        ]

    def test_a_known_option_inside_math(self, tree):
        assert tree("$\\sqrt[n]{x}$\n") == [("$", ["\\sqrt", ("[", ["n"]), ("{", ["x"])])]

    def test_a_known_environment_with_no_argument(self, tree):
        assert tree("\\begin{center}[x]\\end{center}\n") == [("center", ["[x]"])]

    def test_an_unknown_environment_keeps_the_heuristic(self, tree):
        assert tree("\\begin{tikzpicture}[scale=2]\\end{tikzpicture}\n") == [
            ("tikzpicture", [("[", ["scale=2"])])
        ]


class TestModes:
    def test_a_star_outside_a_signature_is_text(self, tree):
        assert tree("$a\\cdot*b$\n") == [("$", ["a", "\\cdot", "*b"])]

    def test_a_text_accent_redefined_in_math(self, parse):
        # `\d` is an accent in text; in math, it must have been redefined (a differential).
        tex = parse("$\\d x$ et \\d x\n")
        math_d, text_d = commands(tex, "d")
        assert (math_d.signature, bound(text_d)) == (None, ["x"])


class TestQueries:
    def test_bound_arguments_by_default(self, parse):
        (found,) = parse("\\section%\n[court]{Titre}\n").get_sections()
        assert [str(node) for node in found] == ["\\section", "[court]", "{Titre}"]

    def test_the_heuristic_on_demand(self, parse):
        (found,) = parse("\\section[court]{Titre}\n").get_commands_arguments("section", nargs=1)
        assert [str(node) for node in found] == ["\\section", "[court]"]

    def test_an_unknown_command(self, parse):
        (found,) = parse("\\foo[a]{b}\n").get_commands_arguments("foo")
        assert [str(node) for node in found] == ["\\foo", "[a]"]
