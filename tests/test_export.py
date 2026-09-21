"""Edits: what they change, what they leave intact, what they refuse."""

import pytest

from latexdetok import TexFile, expand
from latexdetok.export import Edit, corrected, rewrite

SOURCE = """\\documentclass{article}
\\begin{document}
\\section{Le titre}   % un commentaire gardé
\\rmq[ancien]{Du texte}
\\rmq{Sans option}
\\begin{itemize}
\\item premier
\\end{itemize}
\\end{document}
"""


@pytest.fixture
def tex():
    document = TexFile(SOURCE.splitlines(keepends=True))
    document.analyse()
    return document


def command(tex, name):
    return next(node for node in tex.container.iter() if node.is_command(name))


def options(tex, name):
    return [node for found in tex.get_commands_arguments(name) for node in found if node.is_option_group()]


def line(text, number):
    return text.splitlines()[number - 1]


class TestEdits:
    def test_changing_an_argument(self, tex):
        (option,) = options(tex, "rmq")
        assert line(rewrite(tex, [Edit.inside(option, "nouveau")]), 4) == "\\rmq[nouveau]{Du texte}"

    def test_adding_an_optional(self, tex):
        edit = Edit.after(command(tex, "rmq"), "[XX]")
        assert line(rewrite(tex, [edit]), 4) == "\\rmq[XX][ancien]{Du texte}"

    def test_changing_the_command_without_touching_the_argument(self, tex):
        assert line(rewrite(tex, [Edit.of(command(tex, "section"), "\\subsection")]), 3) == (
            "\\subsection{Le titre}   % un commentaire gardé"
        )

    def test_changing_an_environment(self, tex):
        (group,) = list(tex.container.get_envs("itemize"))
        edits = [Edit.opening(group, "\\begin{enumerate}"), Edit.closing(group, "\\end{enumerate}")]
        result = rewrite(tex, edits)
        assert (line(result, 6), line(result, 8)) == ("\\begin{enumerate}", "\\end{enumerate}")

    def test_deleting(self, tex):
        (option,) = options(tex, "rmq")
        assert line(rewrite(tex, [Edit.of(option, "")]), 4) == "\\rmq{Du texte}"

    def test_insertions_at_the_same_place_in_the_order_given(self, tex):
        target = command(tex, "section")
        assert line(rewrite(tex, [Edit.before(target, "1"), Edit.before(target, "2")]), 3).startswith(
            "12\\section"
        )


class TestCorrectingFunction:
    def test_the_filter_goes_through_none(self, tex):
        edits = corrected(
            (node for node in tex.container.iter() if node.is_command()),
            lambda node: "\\subsection" if node.is_command("section") else None,
        )
        assert [edit.text for edit in edits] == ["\\subsection"]

    def test_the_function_reads_the_node(self, tex):
        edits = corrected(options(tex, "rmq"), lambda group: f"[{group.arg().upper()}]")
        assert line(rewrite(tex, edits), 4) == "\\rmq[ANCIEN]{Du texte}"


class TestMinimalDiff:
    def test_the_rest_is_identical_byte_for_byte(self, tex):
        result = rewrite(tex, [Edit.of(command(tex, "section"), "\\subsection")])
        changed = [
            (before, after)
            for before, after in zip(SOURCE.splitlines(), result.splitlines(), strict=True)
            if before != after
        ]
        assert len(changed) == 1 and changed[0][0].startswith("\\section")

    def test_with_no_edit_the_source_does_not_move(self, tex):
        assert rewrite(tex, []) == SOURCE

    def test_an_edit_after_the_last_node(self, tex):
        assert rewrite(tex, [Edit.after(tex.container, "% fin")]).endswith("\\end{document}% fin\n")

    def test_an_edit_at_the_end_of_the_file(self, tex):
        end = (len(tex.lines) + 1, 0)
        assert rewrite(tex, [Edit(end, end, "% fin\n")]).endswith("\\end{document}\n% fin\n")


class TestWhatRaises:
    def test_two_edits_that_overlap(self, tex):
        target = command(tex, "section")
        with pytest.raises(ValueError, match="two edits overlap"):
            rewrite(tex, [Edit.of(target, "a"), Edit.of(target, "b")])

    def test_a_node_from_an_expanded_view(self, tex):
        view = expand(tex)
        with pytest.raises(ValueError, match="another file"):
            rewrite(tex, [Edit.of(command(view, "section"), "\\subsection")])

    def test_a_backwards_edit(self):
        with pytest.raises(ValueError, match="backwards edit"):
            Edit((3, 5), (2, 0), "x")

    def test_a_position_outside_the_file(self, tex):
        with pytest.raises(ValueError, match="outside the file"):
            rewrite(tex, [Edit((99, 0), (99, 0), "x")])

    def test_a_column_outside_the_line(self, tex):
        with pytest.raises(ValueError, match="outside line"):
            rewrite(tex, [Edit((1, 99), (1, 99), "x")])

    def test_the_inside_of_a_node_that_is_not_a_group(self, tex):
        with pytest.raises(TypeError, match="inside of a group"):
            Edit.inside(command(tex, "section"), "x")

    def test_a_node_with_no_position(self):
        loose = TexFile(["a\n"])
        loose.analyse()
        with pytest.raises(ValueError, match="no position"):
            Edit.of(loose.get_commands_arguments("inconnue"), "x")
