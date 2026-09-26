"""The tree model: rewriting, walking, order, predicates and queries."""

import re

import pytest

from latexdetok import TexComment, TexContainer, TexContent, TexGroup, TexVerbatim
from latexdetok.characters import collapse_spaces


class TestRewriting:
    @pytest.mark.parametrize(
        "source",
        [
            "hello world\nfoo",
            "\\section{Intro} Texte",
            "a\n\nb",
            "\\foreach \\x in {1,2} \\draw (\\x,0);",
            "a \\LaTeX{} b",
            "\\emph{a} b",
            "\\item b",
            "x % commentaire\ny",
            "{a%c\n}",
            "\\begin{center}\nau centre\n\\end{center}",
            "\\begin{verbatim}\na\\b}%\n\\end{verbatim}\nafter",
            "\\verb+a{b+ c",
            "\\url{http://a%b}",
            "$x$ et $$y$$ et \\[z\\] et \\(t\\)",
            "$a$$b$",
            "\\\\[2mm]",
            "\\includegraphics[width=3cm]{fig1.png}",
            "\\begin{tabular}{cc}\na&b\\\\\n\\end{tabular}",
            "a\\ b",
            "l\\'eau",
        ],
    )
    def test_a_normalised_source_is_rewritten_identically(self, parse, source):
        assert parse(source).content() == source

    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            ("plusieurs     espaces", "plusieurs espaces"),
            ("   indenté", "indenté"),
            ("\\begin  {center}a\\end {center}", "\\begin{center}a\\end{center}"),
            ("{ a }", "{ a }"),
            ("{ }", "{ }"),
            # Found in the corpus: `$ $` rewrote as `$$`.
            ("De Donder $ $", "De Donder $ $"),
            ("\\begin{center}\n\\end{center}", "\\begin{center}\n\\end{center}"),
            ("a\n\n\n\nb", "a\n\nb"),
            ("\\section{A}   \n\n  suite", "\\section{A}\n\nsuite"),
        ],
    )
    def test_normalised_blanks(self, parse, source, expected):
        assert parse(source).content() == expected

    def test_words_of_two_lines_stay_apart(self, parse):
        # A regression: `hello\nworld` became `helloworld`.
        assert parse("hello\nworld\n").content() == "hello\nworld"

    def test_a_command_and_the_letters_after_it_stay_apart(self, parse):
        # A regression: `\foreach \x in` became `\foreach\xin`.
        assert "\\x in" in parse("\\foreach \\x in {1}\n").content()

    def test_a_comment_does_not_eat_the_next_line(self, parse):
        # A regression: `a%c` then `b` became `a%cb`.
        assert parse("a%c\nb\n").content() == "a%c\nb"

    def test_a_paragraph_at_the_end_of_the_file_keeps_its_blank_line(self, parse, shape):
        # Found on 136 files of the MP corpus: the final blank line disappeared.
        tex = parse("a\n\n")
        assert shape(parse(tex.content()).container) == shape(tex.container)

    def test_a_non_breaking_space_is_not_collapsed(self):
        assert collapse_spaces("  a \xa0 b\t\n") == "a \xa0 b"

    def test_a_paragraph_is_rewritten_as_a_blank_line(self, parse):
        # A regression: a PAR rewrote as “PAR” spelled out.
        assert "PAR" not in parse("a\n\nb\n").content()

    def test_the_root_without_a_latexfile_environment(self, parse):
        # A regression: the rewriting wrapped itself in an invalid `\begin{latexfile}`.
        assert "latexfile" not in parse("a\n").content()

    def test_verbatim_with_no_added_lines(self, parse):
        source = "\\begin{verbatim}\nx\n\\end{verbatim}"
        assert str(parse(source).container[0]) == source

    def test_verbatim_keeps_its_delimiter(self, parse):
        # A regression: `\verb+a{b+` became `\verb{a{b}`.
        assert str(parse("\\verb+a{b+\n").container[0]) == "\\verb+a{b+"

    def test_display_math_keeps_its_delimiter(self, parse):
        assert str(parse("\\[x\\]\n").container[0]) == "\\[x\\]"

    def test_arg_of_a_group_is_its_inside(self, parse):
        assert parse("\\section{Mise en perspective}\n").container[1].arg() == "Mise en perspective"

    def test_a_container_with_no_positions_is_parted_by_spaces(self):
        container = TexContainer([TexContent("a"), TexContent("b", command=True)])
        assert str(container) == "a \\b"

    def test_a_comment_at_the_end_of_a_selection_keeps_its_line_ending(self, parse):
        tex = parse("\\section{A}%c\n\\section{B}\n")
        first, _ = tex.get_commands_to_next("section", remove_comments=False)
        assert str(first) == "{\\section{A}%c\n}"

    def test_re_analysing_the_rewriting_gives_the_same_tree(self, fixture_file, parse, shape):
        assert shape(parse(fixture_file.content()).container) == shape(fixture_file.container)


class TestRawText:
    def test_leaves_read_their_source_again(self, fixture_file, walk):
        mismatches = []
        for node in walk(fixture_file.container):
            # `raw_text()` keeps the file's line endings — `\r\n` in a Windows checkout — and `str()`
            # writes `\n`: they are compared in `\n`.
            raw = re.sub(r"\r\n?", "\n", node.raw_text())
            if node.is_pure_text() and not node.is_par():
                ok = collapse_spaces(raw) == node.content
            elif node.is_par():
                ok = raw.strip() == ""
            elif isinstance(node, TexGroup):
                opening, closing = node.enclosures()
                ok = re.sub(r"\s", "", raw).startswith(opening) and raw.endswith(closing)
            else:
                ok = raw == str(node)
            if not ok:
                mismatches.append((node.start_position, str(node)[:40], raw[:40]))
        assert mismatches == []

    def test_with_no_file_it_returns_the_rewriting(self):
        assert TexContent("x", command=True).raw_text() == "\\x"


class TestRepr:
    def test_par(self, parse):
        assert repr(parse("a\n\nb\n").container[1]) == "<TexContent> : PAR"

    def test_a_comment(self):
        assert repr(TexComment(" c")) == "<TexComment> : % c"

    def test_verbatim(self, parse):
        assert repr(parse("\\verb|x|\n").container[0]) == "<TexVerbatim verb> : 'x'"

    def test_hierarchy(self, parse):
        assert parse("\\emph{a} $b$\n").repr_hierarchy() == (
            "<TexGroup latexfile>\n"
            " <TexContent> : \\emph\n"
            " <TexGroup >{<TexContent> : a}\n"
            " <TexGroup $><TexContent> : b\n"
            " "
        )

    def test_a_folded_hierarchy(self, parse):
        assert parse("{a}\n").repr_hierarchy(expand=False) == "<TexGroup latexfile>"

    def test_a_container(self):
        assert repr(TexContainer("x")).startswith("TeXContainer('x', position=None")


class TestIter:
    def test_a_flat_walk_with_delimiters(self, parse):
        assert [str(node) for node in parse("\\emph{a} $b$ \\[c\\]\n").iter()] == [
            "\\emph", "{", "a", "}", "$", "b", "$", "\\[", "c", "\\]",
        ]  # fmt: skip

    def test_an_environment(self, parse):
        assert [str(node) for node in parse("\\begin{center}x\\end{center}\n").iter()] == [
            "\\begin{center}", "x", "\\end{center}",
        ]  # fmt: skip

    def test_delimiters_are_positioned_with_no_file(self, parse):
        opening = next(iter(parse("  {a}\n").container[0].iter()))
        assert (opening.start_position, opening.end_position, opening.rootfile) == ((1, 2), (1, 3), None)

    def test_a_container_of_results_walks_its_elements(self, parse):
        envs = parse("\\begin{a}x\\end{a}\\begin{a}y\\end{a}\n").get_envs("a")
        assert [str(node) for node in envs.iter()] == [
            "\\begin{a}", "x", "\\end{a}", "\\begin{a}", "y", "\\end{a}",
        ]  # fmt: skip


class TestOrder:
    SOURCE = "\\section{A}\n\\begin{center}\n$x$\n\\end{center}\n"

    def test_sorting_in_document_order(self, parse):
        container = parse(self.SOURCE).container
        section, group, center = container.content
        assert sorted([center, group, section]) == [section, group, center]

    def test_the_enclosing_before_the_enclosed_at_equal_starts(self, parse):
        tex = parse(self.SOURCE)
        (selection,) = tex.get_sections()
        assert sorted([selection[0], selection]) == [selection, selection[0]]

    def test_equality_by_position(self, parse):
        tex = parse(self.SOURCE)
        (selection,) = tex.get_commands_arguments("section", nargs=0)
        assert selection == tex.container[0]

    def test_the_same_positions_in_two_different_files(self, parse):
        assert parse(self.SOURCE).container[0] != parse(self.SOURCE).container[0]

    def test_equality_with_something_else_is_false_without_raising(self, parse):
        # A regression: `==` raised an AssertionError against a non-container.
        node = parse(self.SOURCE).container[0]
        assert (node == "\\section", node in ["x", None]) == (False, False)

    def test_elements_of_different_files_do_not_compare(self, parse):
        with pytest.raises(ValueError, match="two different files"):
            _ = parse(self.SOURCE).container[0] < parse(self.SOURCE).container[1]

    def test_elements_with_no_position_are_equal_by_identity(self):
        assert (TexContainer([]) == TexContainer([]), TexContainer([]) != TexContainer([])) == (False, True)

    def test_an_element_with_no_position_does_not_sort(self):
        with pytest.raises(TypeError):
            sorted([TexContent("a"), TexContent("b")])

    @pytest.mark.parametrize(
        ("inner", "outer", "expected"),
        [
            ((2, 0), (2,), True),
            ((2,), (2, 0), False),
            ((1, 0), (2,), False),
            ((2, 0, 0), (2,), True),
        ],
    )
    def test_is_in(self, parse, inner, outer, expected):
        container = parse(self.SOURCE).container

        def at(path):
            node = container
            for index in path:
                node = node[index]
            return node

        assert at(inner).is_in(at(outer)) is expected

    def test_is_in_at_an_equal_start(self, parse):
        # A regression: is_in compared the start of one with the end of the other.
        (selection,) = parse(self.SOURCE).get_sections()
        assert (selection[0].is_in(selection), selection.is_in(selection[0])) == (True, False)


class TestPredicates:
    def test_an_empty_comment_is_not_a_par(self):
        # A regression: a lone `%` passed for a blank line.
        assert TexComment("").is_par() is False

    @pytest.mark.parametrize(
        ("source", "predicate", "expected"),
        [
            ("\\begin{center}x\\end{center}\n", "is_env", True),
            ("\\begin{verbatim}x\\end{verbatim}\n", "is_env", True),
            ("$x$\n", "is_env", True),
            ("$x$\n", "is_math", True),
            ("{x}\n", "is_bracket_group", True),
            ("{x}\n", "is_env", False),
            ("\\foo[x]\n", "is_command", True),
            ("\\verb|x|\n", "is_verbatim", True),
            ("%x\n", "is_comment", True),
            ("x\n", "is_pure_text", True),
            ("\\x\n", "is_pure_text", False),
            ("\\begin\n", "is_begin", True),
            ("\\end\n", "is_end", True),
        ],
    )
    def test_the_first_element(self, parse, source, predicate, expected):
        assert getattr(parse(source).container[0], predicate)() is expected

    def test_is_env_by_name(self, parse):
        center = parse("\\begin{center}x\\end{center}\n").container[0]
        assert (center.is_env("center"), center.is_env("itemize")) == (True, False)

    def test_is_command_in(self, parse):
        assert parse("\\section{x}\n").container[0].is_command_in(["chapter", "section"])

    def test_the_root(self, parse):
        tex = parse("{x}\n")
        assert (tex.container.is_root, tex.container[0].is_root) == (True, False)


class TestConstruction:
    def test_a_group_with_an_invalid_name(self):
        with pytest.raises(ValueError, match="invalid LaTeX group name"):
            TexGroup("(")

    def test_an_environment_with_an_empty_name(self):
        with pytest.raises(ValueError, match="empty environment name"):
            TexGroup("", env=True)

    @pytest.mark.parametrize(
        ("kwargs", "delimiter", "displaymath"),
        [
            ({}, "$", False),
            ({"displaymath": True}, "$$", True),
            ({"delimiter": "\\["}, "\\[", True),
            ({"delimiter": "\\(", "displaymath": True}, "\\(", False),
        ],
    )
    def test_math_delimiter_and_display_agree(self, kwargs, delimiter, displaymath):
        math = TexGroup("$", env=True, **kwargs)
        assert (math.delimiter, math.displaymath) == (delimiter, displaymath)

    def test_an_unknown_math_delimiter(self):
        with pytest.raises(ValueError, match="unknown math delimiter"):
            TexGroup("$", delimiter="\\begin")

    def test_a_command_with_an_invalid_name(self):
        with pytest.raises(ValueError, match="invalid command name"):
            TexContent("ab1", command=True)

    def test_a_verbatim_command_with_no_delimiter(self):
        # A regression: KeyError on CLOSING_COUPLE[None].
        with pytest.raises(ValueError, match="needs its delimiter"):
            TexVerbatim("verb")

    def test_a_verbatim_with_no_name(self):
        with pytest.raises(ValueError, match="with no name"):
            TexVerbatim("", env=True)

    def test_a_content_of_an_invalid_type(self):
        with pytest.raises(TypeError):
            TexContainer(42)  # type: ignore[arg-type]

    def test_a_container_pur_n_a_pas_de_positions(self):
        container = TexContainer([], position=(1, 0), end_position=(1, 2))
        assert (container.start_position, container.end_position) == (None, None)

    def test_a_copy_does_not_share_the_list(self, parse):
        group = parse("{a}\n").container[0]
        copy = TexContainer(group)
        copy.append(TexContent("b"))
        assert len(group) == 1

    def test_addition_concatenates_the_elements(self, parse):
        tex = parse("{a}{b}\n")
        total = tex[0] + tex[1]
        assert ([str(node) for node in total], total.rootfile) == (["a", "b"], tex)

    def test_addition_of_leaves(self):
        assert [str(node) for node in TexContent("a") + TexContent("b")] == ["a", "b"]

    def test_iadd_extends(self, parse):
        group = parse("{a}\n").container[0]
        group += TexContent("b")
        assert [str(node) for node in group] == ["a", "b"]

    def test_iadd_on_a_leaf_is_refused(self):
        leaf = TexContent("a")
        with pytest.raises(TypeError):
            leaf += TexContent("b")

    def test_append_refuses_a_string(self):
        with pytest.raises(TypeError):
            TexGroup().append("x")  # type: ignore[arg-type]

    def test_len(self):
        assert (len(TexContent("")), len(TexContent("abc")), len(TexGroup())) == (0, 1, 0)


class TestGetCommandsArguments:
    def test_the_arguments_that_follow(self, parse):
        (found,) = parse("\\frac{a}{b} c\n").get_commands_arguments("frac", nargs=2)
        assert found.raw_text() == "\\frac{a}{b}"

    def test_the_option_is_taken_if_it_is_there(self, parse):
        found = parse("\\includegraphics[w=1]{f1}\n\\includegraphics{f2}\n").get_commands_arguments(
            "includegraphics", nopt=1
        )
        assert [selection.raw_text() for selection in found] == [
            "\\includegraphics[w=1]{f1}",
            "\\includegraphics{f2}",
        ]

    def test_the_starred_one_is_found_by_default(self, parse):
        found = parse("\\section{A}\\section*{B}\n").get_commands_arguments("section")
        assert [selection[-1].arg() for selection in found] == ["A", "B"]

    def test_the_starred_one_is_ignored_on_demand(self, parse):
        found = parse("\\section{A}\\section*{B}\n").get_commands_arguments("section", starred=False)
        assert len(found) == 1

    def test_a_missing_argument_at_the_end_of_a_group(self, parse):
        # A regression: IndexError when the command ends its group.
        (found,) = parse("{\\input}\n").get_commands_arguments("input", nargs=1, nopt=1)
        assert str(found) == "{\\input}"

    def test_the_callers_list_is_not_modified(self, parse):
        # A regression: every call added the starred names to the list that was passed.
        names = ["section"]
        parse("\\section{A}\n").get_commands_arguments(names)
        assert names == ["section"]

    def test_found_inside_the_groups(self, parse):
        found = parse("\\emph{\\cite{a}} \\begin{center}\\cite{b}\\end{center}\n").get_commands_arguments(
            "cite"
        )
        assert [selection.raw_text() for selection in found] == ["\\cite{a}", "\\cite{b}"]

    def test_a_complete_raw_text(self, parse):
        # A regression: `\section{Intro}` read back as `\section{Intr`.
        (found,) = parse("\\section{Intro}\n").get_sections()
        assert found.raw_text() == "\\section{Intro}"

    def test_the_results_keep_the_file(self, parse):
        tex = parse("\\input{a}\n")
        assert tex.get_commands_arguments("input").rootfile is tex


class TestGetCommandsToNext:
    SOURCE = "\\section{A}\ntexte A\n%note\n\\section*{B}\ntexte B\n"

    def test_splitting_by_section_starred_or_not(self, parse):
        found = parse(self.SOURCE).get_commands_to_next("section")
        assert [selection.raw_text() for selection in found] == [
            "\\section{A}\ntexte A",
            "\\section*{B}\ntexte B",
        ]

    def test_a_comments_retires(self, parse):
        (first, _) = parse(self.SOURCE).get_commands_to_next("section")
        assert not any(node.is_comment() for node in first)

    def test_a_comments_gardes(self, parse):
        (first, _) = parse(self.SOURCE).get_commands_to_next("section", remove_comments=False)
        assert any(node.is_comment() for node in first)

    def test_without_stopping_at_the_next_one(self, parse):
        found = parse(self.SOURCE).get_commands_to_next("section", close_at_same_level=False)
        assert [len(selection) for selection in found] == [6, 3]

    def test_comments_are_taken_out_without_stopping_too(self, parse):
        # A regression: remove_comments was only applied with close_at_same_level.
        (first, _) = parse(self.SOURCE).get_commands_to_next("section", close_at_same_level=False)
        assert not any(node.is_comment() for node in first)

    def test_a_selection_stops_at_the_end_of_the_group(self, parse):
        (found,) = parse("\\begin{a}\\item x\\end{a} y\n").get_commands_to_next("item")
        assert found.raw_text() == "\\item x"

    def test_a_command_left_bare_neither_starts_nor_ends_a_selection(self, parse):
        tex = parse(
            "\\titleformat{\\section}{\\bfseries}{}{0pt}{}\n"
            "\\section{A}\ntexte\n\\let\\titre\\section\n\\section{B}\n"
        )
        assert [selection.raw_text() for selection in tex.get_commands_to_next("section")] == [
            "\\section{A}\ntexte\n\\let\\titre\\section",
            "\\section{B}",
        ]

    def test_graphics_per_section(self, parse):
        tex = parse("\\section{A}\\includegraphics{a}\n\\section{B}\\includegraphics{b}\n")
        assert [
            [g[-1].arg() for g in section.get_graphics()] for section in tex.get_commands_to_next("section")
        ] == [
            ["a"],
            ["b"],
        ]


class TestGetEnvs:
    def test_nested_ones_in_document_order(self, parse):
        tex = parse("\\begin{a}1\\begin{a}2\\end{a}\\end{a}\\begin{a}3\\end{a}\n")
        assert [env.raw_text() for env in tex.get_envs("a")] == [
            "\\begin{a}1\\begin{a}2\\end{a}\\end{a}",
            "\\begin{a}2\\end{a}",
            "\\begin{a}3\\end{a}",
        ]

    def test_every_math(self, parse):
        tex = parse("$a$ $$b$$ \\[c\\] \\(d\\)\n")
        assert [str(math) for math in tex.get_envs("$")] == ["$a$", "$$b$$", "\\[c\\]", "\\(d\\)"]

    def test_verbatim(self, parse):
        (found,) = parse("\\begin{verbatim}\nx\n\\end{verbatim}\n").get_envs("verbatim")
        assert found.content == "\nx\n"

    def test_the_raw_text_of_a_formula(self, parse):
        # A regression: `$x^2$` read back as `x^2$\n`.
        (math,) = parse("centré $x^2$\n").get_envs("$")
        assert math.raw_text() == "$x^2$"

    def test_absent(self, parse):
        assert len(parse("x\n").get_envs("center")) == 0


class TestGetPreamble:
    def test_what_comes_before_the_document(self, parse):
        # A regression: AttributeError on `self.container`.
        tex = parse("\\documentclass{article}\n\\usepackage{x}\n\\begin{document}\na\n\\end{document}\n")
        assert str(tex.get_preamble()) == "\\documentclass{article}\n\\usepackage{x}"

    def test_with_no_document(self, parse):
        assert len(parse("a\n").get_preamble()) == 0

    def test_on_a_leaf(self):
        assert len(TexContent("a").get_preamble()) == 0


class TestGetSections:
    def test_a_short_title(self, parse):
        # A regression: with no option, `[court]` was taken for the title.
        (section,) = parse("\\section[Court]{Titre long}\n").get_sections()
        assert (section[1].arg(), section[-1].arg()) == ("Court", "Titre long")


class TestGetGraphics:
    def test_every_figure_command(self, parse):
        tex = parse(
            "\\includegraphics{a}\\includetoolargegraphics[scale=1]{b}\\includepdf[pages=1]{c.pdf}\\includetikz{d}\n"
        )
        assert [graphic[-1].arg() for graphic in tex.get_graphics()] == ["a", "b", "c.pdf", "d"]


class TestGetInputs:
    SOURCE = "\\input{defs}\n\\begin{document}\n\\input{chap1}\n\\end{document}\n"

    @pytest.mark.parametrize(
        ("document", "expected"),
        [
            (None, ["chap1"]),
            (True, ["chap1"]),
            (False, ["defs", "chap1"]),
        ],
    )
    def test_per_document(self, parse, document, expected):
        found = parse(self.SOURCE).get_inputs(document=document)
        assert [selection[-1].arg() for selection in found] == expected

    def test_everywhere_with_no_document(self, parse):
        assert len(parse("\\input{a}\n").get_inputs()) == 1

    def test_only_inside_a_document_that_is_missing(self, parse):
        assert len(parse("\\input{a}\n").get_inputs(document=True)) == 0

    def test_the_raw_text_inside_the_document(self, parse):
        # A regression: the file was lost when going through get_envs('document').
        (found,) = parse(self.SOURCE).get_inputs()
        assert found.raw_text() == "\\input{chap1}"
