"""Building the tree: text, commands, groups, math, verbatim, tolerance."""

import pytest

from latexdetok import CatcodeTable, TexCommand, TexContent, TexFile, TexParser, TexVerbatim
from latexdetok.catcodes import EXPL_SYNTAX
from latexdetok.parser import BranchRegion


class TestText:
    def test_multiple_spaces_collapse(self, tree):
        assert tree("plusieurs     espaces\tet  tab\n") == ["plusieurs espaces et tab"]

    def test_spaces_at_the_start_and_end_of_a_line_are_ignored(self, tree):
        assert tree("   indenté   \n") == ["indenté"]

    def test_one_line_one_text_node(self, tree):
        assert tree("hello\nworld\n") == ["hello", "world"]

    def test_a_non_breaking_space_stays_text(self, tree):
        assert tree("a\xa0: b\n") == ["a\xa0: b"]

    def test_an_ignored_character_is_taken_out_of_the_text(self, tree):
        assert tree("a\x00b  \x00 c\n") == ["ab c"]

    def test_a_text_ends_on_its_last_character(self, parse):
        text = parse("ab  cd   \\x\n").container[0]
        assert (text.content, text.start_position, text.end_position) == ("ab cd", (1, 0), (1, 6))

    def test_text_after_a_one_character_argument(self, tree):
        assert tree("\\frac12 et la suite\n") == ["\\frac", "1", "2", "et la suite"]

    def test_lines_with_no_line_ending(self, shape):
        tex = TexParser(["a", "b"]).parse()
        assert shape(tex)[1] == ["a", "b"]

    @pytest.mark.parametrize("ending", ["\r\n", "\r"])
    def test_crlf_and_cr_line_endings(self, shape, ending):
        tex = TexParser([f"\\section{{A}}{ending}", f"texte{ending}"]).parse()
        assert shape(tex)[1] == ["\\section", ("{", ["A"]), "texte"]

    def test_an_empty_source(self, shape):
        assert shape(TexParser([]).parse()) == ("latexfile", [])


class TestParagraphs:
    def test_a_blank_line_makes_a_par(self, tree):
        assert tree("a\n\nb\n") == ["a", "PAR", "b"]

    def test_several_blank_lines_make_one_par(self, tree):
        assert tree("a\n\n\n\nb\n") == ["a", "PAR", "b"]

    def test_a_line_of_spaces_makes_a_par(self, tree):
        assert tree("a\n    \nb\n") == ["a", "PAR", "b"]

    def test_a_commented_line_is_not_blank(self, tree):
        assert tree("a\n%\nb\n") == ["a", "%", "b"]

    def test_a_leading_blank_line_makes_a_par(self, tree):
        assert tree("\nb\n") == ["PAR", "b"]

    def test_a_line_starting_with_a_dollar_is_not_blank(self, tree):
        # A regression: `$x$` at the start of a line added a phantom PAR.
        assert tree("$x$\n") == [("$", ["x"])]


class TestCommands:
    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            ("\\section\n", ["\\section"]),
            ("\\section*{A}\n", ["\\section*", ("{", ["A"])]),
            ("\\foo123\n", ["\\foo", "123"]),
            ("\\café\n", ["\\caf", "é"]),
            ("\\foo@bar\n", ["\\foo", "@bar"]),
            ("x\\%y\n", ["x", "\\%", "y"]),
            ("a\\\\b\n", ["a", "\\\\", "b"]),
            ("a\\\\*b\n", ["a", "\\\\*", "b"]),
            ("l\\'eau\n", ["l", "\\'", "e", "au"]),
            ("a\\ b\n", ["a", "\\ ", "b"]),
            ("\\{x\\}\n", ["\\{", "x", "\\}"]),
        ],
    )
    def test_a_command_name_per_tex(self, tree, source, expected):
        assert tree(source) == expected

    def test_an_at_sign_after_a_backslash_is_a_symbol(self, tree):
        # A regression: `\@ifnextchar` raised an AssertionError.
        assert tree("\\@ifnextchar a\n") == ["\\@", "ifnextchar a"]

    def test_a_backslash_at_the_end_of_a_line_is_a_control_space(self, tree):
        assert tree("a\\\nb\n") == ["a", "\\ ", "b"]

    def test_spaces_after_a_command_are_not_text(self, tree):
        assert tree("\\item   b\n") == ["\\item", "b"]


class TestPackageSignatures:
    """`data/packages.txt`: what packages declare through code, written by hand."""

    def test_includegraphics_binds_the_option_and_the_file(self, parse):
        (graphic,) = [n for n in parse("\\includegraphics[width=2cm]{fig}\n").container if n.is_command()]
        assert [None if a is None else str(a) for a in graphic.arguments] == ["[width=2cm]", None, "{fig}"]

    def test_a_list_with_the_options_of_enumitem(self, parse):
        (itemize,) = parse("\\begin{itemize}[label=\\textbullet]\\item a\\end{itemize}\n").get_envs("itemize")
        assert [str(a) for a in itemize.arguments if a is not None] == ["[label=\\textbullet]"]

    def test_a_siunitx_unit_taken_as_an_argument(self, parse, tree):
        # `\SI{24}h`: TeX reads `h` as the second argument, and so does the tokeniser.
        assert tree("\\SI{24}h)\n") == ["\\SI", ("{", ["24"]), "h", ")"]


class TestDefinitionTokens:
    def test_a_definition_names_a_math_delimiter(self, parse):
        tex = parse("\\def\\[{[}\n")
        (definition,) = [node for node in tex.container.content if node.is_command("def")]
        assert (str(definition.arguments[0]), tex.signatures.macro("[").body) == ("\\[", "[")

    def test_futurelet_does_not_take_the_next_end(self, tree):
        # Found in the corpus: `\footnote` ends there with `\futurelet\nextToken\isFootnote`.
        assert tree("\\begin{itemize}\\item a\\futurelet\\n\\t\n\\end{itemize}\n")[0][0] == "itemize"


class TestGroups:
    def test_nested_braces(self, tree):
        assert tree("\\emph{bla\\gras{x}}\n") == [
            "\\emph",
            ("{", ["bla", "\\gras", ("{", ["x"])]),
        ]

    def test_an_empty_group(self, tree):
        assert tree("\\LaTeX{} b\n") == ["\\LaTeX", ("{", []), "b"]

    def test_a_group_over_several_lines(self, tree):
        assert tree("{a\nb}\n") == [("{", ["a", "b"])]

    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            ("\\item[a] b\n", ["\\item", ("[", ["a"]), "b"]),
            ("\\item [a]\n", ["\\item", ("[", ["a"])]),
            ("\\\\[2mm]\n", ["\\\\", ("[", ["2mm"])]),
            (
                "\\newcommand{\\x}[1]{#1}\n",
                ["\\newcommand", ("{", ["\\x"]), ("[", ["1"]), ("{", ["#1"])],
            ),
            (
                "\\includepdf[pages={2-7},clip]{f.pdf}\n",
                ["\\includepdf", ("[", ["pages=", ("{", ["2-7"]), ",clip"]), ("{", ["f.pdf"])],
            ),
            (
                "\\begin{tikzpicture}[scale=2]\n\\end{tikzpicture}\n",
                [("tikzpicture", [("[", ["scale=2"])])],
            ),
        ],
    )
    def test_a_bracket_after_a_command_group_or_begin_opens_an_option(self, tree, source, expected):
        assert tree(source) == expected

    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            ("voir [1]\n", ["voir [1]"]),
            ("[a]\n", ["[a]"]),
            ("node[above]\n", ["node[above]"]),
            ("a ] b\n", ["a ] b"]),
            ("{[}\n", [("{", ["["])]),
        ],
    )
    def test_a_bracket_after_text_stays_text(self, tree, source, expected):
        assert tree(source) == expected

    def test_a_closing_bracket_inside_braces_of_an_option(self, tree):
        assert tree("\\foo[{a]}]\n") == ["\\foo", ("[", [("{", ["a]"])])]


class TestEnvironments:
    def test_begin_end(self, tree):
        assert tree("\\begin{center}\nau centre\n\\end{center}\n") == [("center", ["au centre"])]

    def test_spaces_between_begin_and_the_name(self, tree):
        # A regression reported in test2.tex: “this used to cause an error”.
        assert tree("\\begin  {center}a\\end {center}\n") == [("center", ["a"])]

    def test_nested_environments(self, tree):
        source = "\\begin{itemize}\n\\item \\begin{center}x\\end{center}\n\\end{itemize}\n"
        assert tree(source) == [("itemize", ["\\item", ("center", ["x"])])]

    def test_the_arguments_of_an_environment_are_its_first_children(self, tree):
        assert tree("\\begin{tabular}{cc}a&b\\end{tabular}\n") == [("tabular", [("{", ["cc"]), "a&b"])]

    def test_a_starred_name(self, tree):
        assert tree("\\begin{align*}\na&=b\\\\\n\\end{align*}\n") == [("align*", ["a&=b", "\\\\"])]

    def test_the_end_of_the_document_stops_the_reading(self, parse, shape):
        tex = parse("\\begin{document}\nbla\n\\end{document}and\nother things\n")
        assert shape(tex.container)[1] == [("document", ["bla"])]

    def test_the_root_ends_with_the_document(self, parse):
        tex = parse("\\begin{document}\nbla\n\\end{document}and\nother\n")
        assert tex.container.end_position == (3, 14)


class TestMath:
    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            ("$x$\n", [("$", ["x"])]),
            ("$$x$$\n", [("$$", ["x"])]),
            ("\\[x\\]\n", [("\\[", ["x"])]),
            ("\\(x\\)\n", [("\\(", ["x"])]),
            ("$$\n1+1\n$$\n", [("$$", ["1+1"])]),
        ],
    )
    def test_delimiters(self, tree, source, expected):
        assert tree(source) == expected

    def test_two_formulas_glued_together(self, tree):
        # A regression: `$a$$b$` left the stack open.
        assert tree("$a$$b$\n") == [("$", ["a"]), ("$", ["b"])]

    def test_brackets_inside_math_are_text(self, tree):
        assert tree("$x\\in[0;1[$\n") == [("$", ["x", "\\in", "[0;1["])]

    def test_the_option_of_a_known_command_inside_math(self, tree):
        # The `o m` signature of \sqrt wins over “brackets = text in math”.
        assert tree("$\\sqrt[3]{x}$\n") == [("$", ["\\sqrt", ("[", ["3"]), ("{", ["x"])])]

    def test_the_bracket_of_an_unknown_command_in_math_stays_text(self, tree):
        assert tree("$\\racine[3]{x}$\n") == [("$", ["\\racine", "[3]", ("{", ["x"])])]

    def test_an_escaped_bracket_in_math_is_a_command(self, tree):
        # A mechanics course of the corpus writes `$r\in\[0;\infty\[$`.
        assert tree("$r\\in\\[0;\\infty\\[$\n") == [("$", ["r", "\\in", "\\[", "0;", "\\infty", "\\["])]

    def test_math_inside_text_inside_math(self, tree):
        assert tree("$\\text{a $y$}$\n") == [("$", ["\\text", ("{", ["a", ("$", ["y"])])])]

    def test_an_escaped_dollar_opens_no_math(self, tree):
        assert tree("a\\$b\n") == ["a", "\\$", "b"]

    def test_an_escaped_closing_bracket_outside_math_is_a_command(self, tree):
        assert tree("a\\]b\n") == ["a", "\\]", "b"]

    def test_math_is_an_environment_named_dollar(self, parse):
        math = parse("\\[x\\]\n").container[0]
        assert (math.name, math.env, math.displaymath, math.delimiter) == ("$", True, True, "\\[")


class TestVerbatim:
    def test_an_environment_keeps_the_exact_content(self, parse):
        tex = parse("\\begin{verbatim}\nmlkqsd\\mlksqdf}%$\n\\end{verbatim}\nafter\n")
        verbatim, after = tex.container.content
        assert (verbatim.content, str(after)) == ("\nmlkqsd\\mlksqdf}%$\n", "after")

    @pytest.mark.parametrize("ending", ["\r\n", "\r"])
    def test_its_line_endings_are_written_lf_and_kept_in_the_source(self, ending):
        # The content is written like `str()`, whatever the file; `raw_text()` is the source.
        source = "\\begin{verbatim}\nx\n\\end{verbatim}\n".replace("\n", ending)
        (verbatim,) = TexFile(source.splitlines(keepends=True)).analyse().content
        assert (verbatim.content, verbatim.raw_text()) == ("\nx\n", source.removesuffix(ending))

    def test_an_environment_closed_in_the_middle_of_a_line(self, tree):
        assert tree("\\begin{verbatim}x\\end{verbatim} suite\n") == [
            "\\begin{verbatim}x\\end{verbatim}",
            "suite",
        ]

    def test_a_verbatim_environment_is_not_a_group(self, parse):
        assert isinstance(parse("\\begin{lstlisting}\n{\n\\end{lstlisting}\n").container[0], TexVerbatim)

    @pytest.mark.parametrize(
        ("source", "content", "car"),
        [
            ("\\verb|x| y\n", "x", "|"),
            ("\\verb+a{b+ c\n", "a{b", "+"),
            ("\\verb$x$\n", "x", "$"),
            ("\\verb*|a b|\n", "a b", "|"),
            ("\\url{http://a%b}\n", "http://a%b", "{"),
            ("\\pyc{234{}\n", "234{", "{"),
            ("\\lstinline!a%b!\n", "a%b", "!"),
        ],
    )
    def test_a_verbatim_command(self, parse, source, content, car):
        verbatim = parse(source).container[0]
        assert (verbatim.content, verbatim.car) == (content, car)

    def test_a_verbatim_between_braces_matches_them(self, parse):
        # Found in the corpus.
        verbatim = parse('\\pyv{r"\\begin{document}"} suite\n').container[0]
        assert (verbatim.content, verbatim.raw_text()) == (
            'r"\\begin{document}"',
            '\\pyv{r"\\begin{document}"}',
        )

    def test_a_verbatim_between_unmatched_braces_closes_at_the_first(self, parse):
        tex = parse("\\pyc{234{} x\n")
        assert (tex.container[0].content, [d.code for d in tex.diagnostics]) == (
            "234{",
            ["verbatim-braces"],
        )

    def test_a_verbatim_command_over_two_lines(self, parse):
        verbatim = parse("\\url{http://\nexemple}\n").container[0]
        assert verbatim.content == "http://\nexemple"

    @pytest.mark.parametrize(
        "source",
        [
            "\\newcommand{\\myurl}{\\url}\n",
            "\\let\\oldverb\\verb\n",
            "\\def\\url#1{#1}\n",
            "\\renewcommand*\\url[1]{#1}\n",
            "\\url \n",
            "\\verb\n",
        ],
    )
    def test_a_verbatim_name_being_named_opens_no_verbatim(self, parse, source):
        tex = parse(source)
        assert not any(node.is_verbatim() for node in tex.iter())


class TestComments:
    def test_the_content_after_the_percent(self, parse):
        comment = parse("x % commentaire\n").container[1]
        assert (comment.content, comment.is_comment()) == (" commentaire", True)

    @pytest.mark.parametrize("source", ["x %", "a\n%", "%"])
    def test_a_lone_percent_at_the_end_of_the_file(self, tree, source):
        # A regression: IndexError on a line reduced to `%` with no line ending.
        assert tree(source)[-1] == "%"

    def test_a_comment_inside_a_group(self, tree):
        assert tree("{a%c\n}\n") == [("{", ["a", "%c"])]

    def test_an_escaped_percent_is_not_a_comment(self, tree):
        assert tree("50\\% de\n") == ["50", "\\%", "de"]


class TestPositions:
    SOURCE = "\\section{Intro} Texte\n\\begin{center}\n  $x^2$ \\verb|%|\n\\end{center}\n"

    @pytest.mark.parametrize(
        ("path", "raw"),
        [
            ((0,), "\\section"),
            ((1,), "{Intro}"),
            ((1, 0), "Intro"),
            ((2,), "Texte"),
            ((3,), "\\begin{center}\n  $x^2$ \\verb|%|\n\\end{center}"),
            ((3, 0), "$x^2$"),
            ((3, 0, 0), "x^2"),
            ((3, 1), "\\verb|%|"),
        ],
    )
    def test_raw_text_covers_the_whole_element(self, parse, path, raw):
        node = parse(self.SOURCE).container
        for index in path:
            node = node[index]
        assert node.raw_text() == raw

    def test_the_inside_of_an_environment(self, parse):
        center = parse(self.SOURCE).container[3]
        assert (center.inner_start, center.inner_end) == ((2, 14), (4, 0))

    @pytest.mark.parametrize(
        ("source", "index", "raw"),
        [
            ("foo\\\\bar\n", 1, "\\\\"),
            ("x\\%y\n", 1, "\\%"),
            ("\\[1+2\\]\n", 0, "\\[1+2\\]"),
            ("$$a$$\n", 0, "$$a$$"),
            ("a  b \\c\n", 0, "a  b"),
            ("\\verb+a{b+ c\n", 1, "c"),
            ("x % c\n", 1, "% c"),
        ],
    )
    def test_raw_text_with_no_offset(self, parse, source, index, raw):
        # A regression: control symbols and math read back offset.
        assert parse(source).container[index].raw_text() == raw

    def test_the_positions_of_a_par(self, parse):
        par = parse("a\n   \nb\n").container[1]
        assert (par.start_position, par.end_position, par.raw_text()) == ((2, 0), (2, 3), "   ")


class TestTolerance:
    """Nothing raises: whatever does not match is read flat and reported."""

    @pytest.mark.parametrize(
        "source",
        [
            "\\newenvironment{sol}{\\begin{proof}}{\\end{proof}}\n",
            "\\newcommand{\\debut}{\\begin{itemize}}\n\\newcommand{\\fin}{\\end{itemize}}\n",
            "\\def\\m{$}\n",
            "\\newcommand{\\dbl}{$$}\n",
            "l'intervalle \\og [0;1[ \\fg{}\n",
            "\\begin{document}\nfichier dont la fin est ailleurs\n",
            "\\end{itemize}\n\\end{document}\n",
            "\\begin\n{center}x\\end{center}\n",
            "\\iffalse{\\fi\n",
            "\\def\\url#1{\\href{#1}{#1}}\n",
        ],
    )
    def test_valid_latex_is_never_refused(self, parse, source):
        assert parse(source).content() == source.rstrip("\n")

    @pytest.mark.parametrize(
        "source",
        [
            "{a\n",
            "a}b\n",
            "\\begin{center}\\end{itemize}\n",
            "\\begin{verbatim}\nx\n",
            "$a\n",
            "$$a$\n",
            "\\[ $ \\]\n",
            "\\begin{itemize}\\begin{center}\\end{itemize}\n",
        ],
    )
    def test_invalid_latex_does_not_raise(self, parse, source):
        assert parse(source).diagnostics

    def test_a_begin_in_a_definition_dissolves_at_the_brace(self, tree):
        assert tree("\\newenvironment{sol}{\\begin{proof}}{\\end{proof}}\n") == [
            "\\newenvironment",
            ("{", ["sol"]),
            ("{", ["\\begin", ("{", ["proof"])]),
            ("{", ["\\end", ("{", ["proof"])]),
        ]

    def test_a_dissolution_reports_the_opening_and_what_cut_it(self, parse):
        (diagnostic,) = parse("\\textbf{$}\n").diagnostics
        assert (
            diagnostic.code,
            diagnostic.start,
            diagnostic.end,
            [(related.start, related.message) for related in diagnostic.related],
        ) == ("unclosed-math", (1, 8), (1, 9), [((1, 9), "“}” closes the group around it")])

    @pytest.mark.parametrize(
        "source",
        [
            "\\newenvironment{sol}{\\begin{proof}}{\\end{proof}}\n",
            "\\newcommand{\\debut}{\\begin{itemize}}\n\\newcommand{\\fin}{\\end{itemize}}\n",
            "\\def\\m{$}\n",
            "\\def\\beq{\\begin{equation}}\n\\def\\eeq{\\end{equation}}\n",
            "\\NewDocumentCommand{\\x}{m}{\\begin{center}[#1}\n",
            "\\newcommand{\\dbl}{$$}\n",
            "\\newcommand{\\x}{\\begin{a}{$}}\n",
        ],
    )
    def test_the_body_of_a_definition_only_as_an_info(self, parse, problems, source):
        # A body is only matched once expanded: what does not match in it is not a mistake.
        tex = parse(source)
        assert (bool(tex.diagnostics), problems(tex)) == (True, [])

    def test_a_brace_of_a_body_never_closed_is_reported(self, parse, problems):
        (diagnostic,) = problems(parse("\\newcommand{\\x}{\\begin{a}\n"))
        assert (diagnostic.code, diagnostic.start) == ("unclosed-brace", (1, 15))

    def test_outside_the_body_the_diagnostic_comes_back(self, parse, problems):
        (diagnostic,) = problems(parse("\\def\\beq{\\begin{equation}}\\end{itemize}\n"))
        assert (diagnostic.code, diagnostic.start, diagnostic.end) == ("end-without-begin", (1, 26), (1, 39))

    def test_an_end_without_begin_is_reported(self, parse):
        (diagnostic,) = parse("\\end{itemize}\n").diagnostics
        assert (diagnostic.code, diagnostic.start, diagnostic.message) == (
            "end-without-begin",
            (1, 0),
            "“\\end{itemize}” without “\\begin{itemize}”",
        )

    def test_an_end_closes_by_dissolving_what_stays_open(self, tree):
        assert tree("\\begin{itemize}\\begin{center}x\\end{itemize}\n") == [
            ("itemize", ["\\begin", ("{", ["center"]), "x"])
        ]

    def test_an_end_does_not_go_through_a_brace(self, tree):
        assert tree("\\begin{a}{\\end{a}}\\end{a}\n") == [("a", [("{", ["\\end", ("{", ["a"])])])]

    def test_an_unclosed_option_dissolves_at_the_blank_line(self, tree):
        # `\\intervalle` is unknown: its `[` passes for an optional argument, for want of better.
        assert tree("\\intervalle [0;1[\n\nsuite ]\n") == ["\\intervalle", "[", "0;1[", "PAR", "suite ]"]

    def test_unclosed_math_dissolves_at_the_blank_line(self, tree):
        # The next `$` therefore opens new math instead of closing the first one.
        assert tree("$a\n\nb$\n") == ["$", "a", "PAR", "b", "$"]

    def test_the_bracket_of_a_dissolved_environment_becomes_a_command_again(self, parse):
        (token,) = parse("\\[ x\n").container.content[:1]
        assert (type(token), token.is_command("["), token.raw_text()) == (TexContent, True, "\\[")

    def test_an_orphan_closing_brace_stays_text(self, tree):
        assert tree("a}b\n") == ["a}b"]

    def test_a_lone_dollar_inside_display_math_stays_text(self, tree):
        assert tree("$$ a $ b $$\n") == [("$$", ["a $ b"])]

    def test_a_verbatim_with_no_end_runs_to_the_end(self, parse):
        tex = parse("\\begin{verbatim}\nx\n")
        assert (tex.container[0].content, tex.container[0].end_position) == ("\nx\n", (2, 1))

    def test_re_analysing_duplicates_nothing(self, parse, shape):
        # A regression: a second analyse() doubled the tree and stopped at once.
        tex = parse("\\begin{document}\na\n\\end{document}\n")
        first = shape(tex.container)
        tex.analyse()
        assert shape(tex.container) == first

    def test_diagnostics_are_reset_at_every_analysis(self, parse):
        tex = parse("{a\n")
        tex.lines = ["{a}\n"]
        tex.analyse()
        assert tex.diagnostics == []


class TestForcedTables:
    """`catcode_regions`: a table here and there, as for an expanded macro body."""

    def commands(self, root):
        return [node.content for node in root.content if isinstance(node, TexCommand)]

    def test_a_table_forced_on_the_columns(self):
        regions = {1: [(0, 4, CatcodeTable.package())]}
        root = TexParser(["\\a@b \\a@b\n"], catcode_regions=regions).parse()
        assert self.commands(root) == ["a@b", "a"]

    def test_outside_the_named_lines_the_groups_table(self):
        regions = {2: [(0, 4, CatcodeTable.package())]}
        root = TexParser(["\\a@b\n", "\\a@b\n"], catcode_regions=regions).parse()
        assert self.commands(root) == ["a", "a@b"]

    def test_a_table_forced_in_the_middle_of_a_text(self):
        # Under expl3, the space is ignored: the text changes rule at column 4.
        expl = CatcodeTable.latex().with_categories(EXPL_SYNTAX)
        root = TexParser(["a b c d\n"], catcode_regions={1: [(4, 7, expl)]}).parse()
        assert [str(node) for node in root.content] == ["a b cd"]

    def test_a_group_opened_in_the_region_keeps_the_documents_table(self):
        regions = {1: [(0, 1, CatcodeTable.package())]}
        root = TexParser(["{\\a@b}\n"], catcode_regions=regions).parse()
        assert self.commands(root.content[0]) == ["a"]


def branch(start, end, taken, braced=False, condition="test"):
    """A branch region on line 1, from column `start` to `end` (excluded)."""
    inner = (start + 1, end - 1) if braced else (start, end)
    return BranchRegion((1, start), (1, inner[0]), (1, inner[1]), (1, end), condition, taken, braced)


class TestBranches:
    """Branches of conditionals given by the expanded view (see `expansion`)."""

    def parse(self, line, *regions):
        parser = TexParser([line + "\n"], branch_regions=regions)
        return parser, parser.parse()

    def test_a_discarded_branch_runs_nothing(self):
        # \iffalse⟦\def\y{Y}\makeatletter⟧\fi\y\a@b
        parser, root = self.parse(
            "\\iffalse\\def\\y{Y}\\makeatletter\\fi\\y\\a@b", branch(8, 30, taken=False)
        )
        commands = [node.content for node in root.content if isinstance(node, TexCommand)]
        assert (parser.signatures.macro("y"), commands[-2:], parser.diagnostics) == (None, ["y", "a"], [])

    def test_a_discarded_branch_closes_nothing_outside_itself(self, shape):
        _, root = self.parse("{x}\\end{y}z}", branch(2, 10, taken=False))
        assert shape(root)[1] == [("{", ["x", ("branche", ["}", "\\end", ("{", ["y"])]), "z"])]

    def test_a_taken_branch_that_fits_stays_a_node(self, shape):
        _, root = self.parse("A\\textbf{B}C", branch(1, 11, taken=True))
        assert shape(root)[1] == ["A", ("branche", ["\\textbf", ("{", ["B"])]), "C"]

    def test_a_taken_branch_that_spills_out_melts(self, shape):
        _, root = self.parse("\\begin{center}x\\end{center}", branch(0, 14, taken=True))
        assert shape(root)[1] == [("center", ["x"])]

    def test_a_command_that_reads_its_arguments_after_the_branch(self):
        _, root = self.parse("\\textbf{B}", branch(0, 7, taken=True))
        (command,) = [node for node in root.content if isinstance(node, TexCommand)]
        assert [str(argument) for argument in command.arguments] == ["{B}"]

    def test_the_binding_skips_a_discarded_branch_without_braces(self):
        _, root = self.parse("\\textbf X{B}", branch(7, 9, taken=False))
        assert [str(argument) for argument in root.content[0].arguments] == ["{B}"]

    def test_a_branch_between_braces_is_an_argument(self):
        line = "\\IfBooleanTF{\\BooleanTrue}{A}{B}"
        _, root = self.parse(
            line, branch(26, 29, taken=True, braced=True), branch(29, 32, taken=False, braced=True)
        )
        assert [type(argument).__name__ for argument in root.content[0].arguments] == [
            "TexGroup",
            "TexBranch",
            "TexBranch",
        ]

    def test_nested_branches(self, shape):
        _, root = self.parse(
            "ABC", branch(0, 3, taken=True, condition="a"), branch(1, 2, taken=False, condition="b")
        )
        assert shape(root)[1] == [("branche", ["A", ("branche", ["B"]), "C"])]


class TestConditionalsPrimitives:
    def values(self, source):
        tex = TexFile(source.splitlines(keepends=True))
        tex.analyse()
        return [
            (node.content, node.value)
            for node in tex.iter()
            if isinstance(node, TexCommand) and node.role == "if"
        ]

    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            ("\\iftrue\\fi\\iffalse\\fi\n", [("iftrue", True), ("iffalse", False)]),
            ("\\ifnum 1<2 \\fi\\ifnum\\value{a}<2 \\fi\n", [("ifnum", True), ("ifnum", None)]),
            ("\\ifmmode\\fi$\\ifmmode\\fi$\n", [("ifmmode", False), ("ifmmode", True)]),
            ("\\newif\\ifa\\ifa\\fi\\atrue\\ifa\\fi\n", [("ifa", False), ("ifa", True)]),
            ("\\newif\\ifa{\\atrue}\\ifa\\fi\n", [("ifa", False)]),
            ("\\newif\\ifa\\iffalse\\atrue\\else\\fi\\ifa\\fi\n", [("iffalse", False), ("ifa", False)]),
            ("\\newif\\ifa\\ifx\\b\\undefined\\atrue\\fi\\ifa\\fi\n", [("ifx", None), ("ifa", None)]),
            ("\\newif\\ifa\\def\\b{\\atrue}\\b\\ifa\\fi\n", [("ifa", None)]),
            ("\\iftrue\\ifpdf\\fi\\fi\n", [("iftrue", True), ("ifpdf", None)]),
        ],
    )
    def test_the_value(self, source, expected):
        assert self.values(source) == expected

    def test_a_definition_in_a_discarded_branch_is_not_learned(self, parse):
        tex = parse("\\iffalse\\newcommand\\x[1]{}\\fi\\iftrue\\newcommand\\y[1]{}\\fi\n")
        assert (tex.signatures.command("x"), tex.signatures.command("y").spec) == (None, "+m")

    def test_an_unknown_conditional_discards_nothing(self, parse):
        # `\ifthing` may not be a conditional: the `\fi` that follows would close `\iffalse`.
        tex = parse(
            "\\iffalse\\ifthing\\fi\\fi\\newcommand\\x[1]{}\n\\iffalse\\ifthing\\fi\\newcommand\\y[1]{}\\fi\n"
        )
        assert tex.signatures.command("y").spec == "+m"


class TestEffectsOfConditionals:
    def test_global_survives_a_local_value(self, parse):
        tex = parse("\\newif\\ifa\n{\\afalse\\global\\atrue}\\ifa\\fi\n")
        (condition,) = [node for node in tex.iter() if isinstance(node, TexCommand) and node.role == "if"]
        assert condition.value is True

    def test_a_macro_that_calls_a_setter(self, parse):
        tex = parse("\\newif\\ifa\\def\\b{\\atrue}\\def\\c{\\b}\n\\c\\ifa\\fi\n")
        (condition,) = [node for node in tex.iter() if isinstance(node, TexCommand) and node.role == "if"]
        assert condition.value is None

    def test_catcodes_unchanged_in_a_discarded_branch(self, parse):
        tex = parse("\\iffalse\\makeatletter\\fi\\a@b\n")
        assert [node.content for node in tex.container.content if isinstance(node, TexCommand)][-1] == "a"

    def test_a_taken_branch_after_an_expected_optional_argument(self):
        # a\\⟦\textbf⟧{b}: found in the corpus, where `\\` comes before `\rmqcons{…}`.
        # The binding of the `\\`, still waiting for its `[`, kept the place: `\textbf` lost its argument.
        parser = TexParser(["a\\\\\\textbf{b}\n"], branch_regions=[branch(3, 10, taken=True)])
        root = parser.parse()
        (textbf,) = [node for node in root.content if node.is_command("textbf")]
        assert [str(argument) for argument in textbf.arguments] == ["{b}"]

    def test_conditionals_expanded_in_an_argument_that_is_not_typeset(self, parse):
        # `\directlua` expands its argument: its conditionals are TeX's.
        tex = parse("\\directlua{\\ifnum 1<2 tex.print(1)\\fi}\n")
        roles = [node.role for node in tex.iter() if isinstance(node, TexCommand) and node.role is not None]
        assert roles == ["if", "fi"]

    def test_a_taken_branch_dissolved_by_an_end_with_no_warning(self, shape):
        # \begin{center}⟦x\end{center}⟧y: the `\end` closes the environment opened before the branch.
        region = branch(14, 27, taken=True)
        parser = TexParser(["\\begin{center}x\\end{center}y\n"], branch_regions=[region])
        root = parser.parse()
        assert (shape(root)[1], parser.diagnostics) == ([("center", ["x"]), "y"], [])
