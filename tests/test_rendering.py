"""Compact views: the frame in blocks, the outline, the HTML page."""

import json
import re

import pytest

from latexdetok import expand
from latexdetok.rendering import _TwoFaces, blocks, html_page, outline

DOCUMENT = """\\documentclass{book}
\\begin{document}
\\chapter{Premier}
Du texte, trois mots.
\\section{Une}
\\begin{itemize}
\\item a $x$
\\item b
\\end{itemize}
\\begin{align}
a &= b
\\end{align}
\\begin{align}
c &= d
\\end{align}
\\section{Deux}
\\fbox{
boîte
}
\\end{document}
"""


def summary(block):
    return [(child.kind, child.label, child.start, child.end) for child in block.children]


@pytest.fixture
def document(parse):
    return parse(DOCUMENT)


class TestBlocks:
    def test_sections_up_to_the_next_one(self, document):
        (body,) = blocks(document).children
        (chapter,) = body.children
        assert summary(chapter) == [
            ("section", "section · Une", 5, 15),
            ("section", "section · Deux", 16, 20),
        ]

    def test_environments_boxes_and_math(self, document):
        chapter = blocks(document).children[0].children[0]
        first, second = chapter.children
        assert (summary(first), summary(second)) == (
            [("environment", "itemize", 6, 9), ("math", "align", 10, 12), ("math", "align", 13, 15)],
            [("command", "fbox", 17, 19)],
        )

    def test_a_section_title_is_outside_the_counts(self, document):
        chapter = blocks(document).children[0].children[0]
        assert chapter.stats["words"] == 4

    def test_the_counts_of_the_block(self, document):
        itemize = blocks(document).children[0].children[0].children[0].children[0]
        assert (itemize.stats["\\item"], itemize.stats["$…$"], itemize.stats["words"]) == (2, 1, 2)

    def test_a_taken_branch_in_the_stream_a_discarded_one_counted(self, parse):
        view = expand(
            parse("\\newif\\ifa\n\\ifa A\\else\\begin{center}B\\end{center}\\fi\n\\iftrue C\\else D\\fi\n")
        )
        root = blocks(view)
        assert (summary(root), root.stats["discarded branches"]) == (
            [("environment", "center", 2, 2)],
            2,
        )

    def test_a_block_of_a_discarded_branch_knows_its_side(self, parse):
        view = expand(parse("\\iffalse\n\\begin{center}\nB\n\\end{center}\n\\fi\n"))
        (branch,) = blocks(view).children
        assert (branch.kind, branch.side, [child.side for child in branch.children]) == (
            "branch",
            False,
            [False],
        )


class TestOutline:
    def test_short_neighbours_are_grouped(self, document):
        assert "align ×2" in outline(document, min_lines=4)

    def test_a_limited_depth_sums_up_the_sub_blocks(self, document):
        lines = outline(document, depth=2).splitlines()
        assert (len(lines), "2 \\item" in lines[-1]) == (4, True)

    def test_one_line_per_block_with_its_lines_on_the_right(self, document):
        lines = outline(document).splitlines()
        assert (lines[0].split(" · ")[:2], lines[2].split()[:3], lines[2].split()[-1]) == (
            ["<test>", "20 lines"],
            ["2", "└", "document"],
            "2–20",
        )

    def test_colours_only_on_demand(self, document):
        assert ("\033[" in outline(document), "\033[" in outline(document, color=True)) == (False, True)


def test_page_html(document):
    page = html_page(document)
    assert ('id="L20"' in page, '<nav id="outline">' in page, '"view"' in page) == (True, True, True)


RMQ = (
    "\\makeatletter\\newcommand{\\rmq}{\\@ifstar{\\moveup\\@rmq}{\\@rmq}}"
    "\\newcommand\\@rmq[1]{\\begin{boite}#1\\end{boite}}\\makeatother\n"
)


class TestTwoFaces:
    """Every use written in the source, and the text it becomes in the expanded view."""

    def faces(self, parse, source):
        tex = parse(source)
        return _TwoFaces(tex, expand(tex))

    def tree(self, faces):
        source, view = faces._source.text, faces._target.text

        def describe(use):
            return (
                source[use.start : use.end],
                view[use.view_start : use.view_end],
                [describe(c) for c in use.children],
            )

        return [describe(use) for use in faces.roots]

    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            (
                "\\newcommand\\vect[1]{\\overrightarrow{#1}}\n$\\vect u$\n",
                [("\\vect u", "\\overrightarrow{u}", [])],
            ),
            (
                "\\newcommand\\gras[1]{\\textbf{#1}}\\newcommand\\boite[1]{\\fbox{#1}}\n\\boite{\\gras{x}}\n",
                [("\\boite{\\gras{x}}", "\\fbox{\\textbf{x}}", [("\\gras{x}", "\\textbf{x}", [])])],
            ),
            (
                "\\newcommand\\gras[1]{\\textbf{#1}}\\newcommand\\deux[1]{#1/#1}\n\\deux{\\gras{x}}\n",
                [("\\deux{\\gras{x}}", "\\textbf{x}/\\textbf{x}", [("\\gras{x}", "\\textbf{x}", [])])],
            ),
            (RMQ + "\\rmq*{a}\n", [("\\rmq*{a}", "\\moveup\\@rmq\\begin{boite}a\\end{boite}", [])]),
            (
                "\\NewDocumentEnvironment{x}{m}{[#1}{#1]}\n\\newcommand\\y{Y}\n\\begin{x}{\\y}z\\end{x}\n",
                [("\\begin{x}{\\y}", "\\begin{x}{Y}[Y", [("\\y", "Y", [])]), ("\\end{x}", "Y]\\end{x}", [])],
            ),
        ],
    )
    def test_the_text_of_every_use(self, parse, source, expected):
        assert self.tree(self.faces(parse, source)) == expected

    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            # The argument of the delegation, first copied into the body of `\\ex`, stays with `\\rmq`.
            (
                "\\newcommand\\ex[1]{<#1>}\n" + RMQ + "\\ex{\\rmq{a}}\n",
                [
                    (
                        "\\ex{\\rmq{a}}",
                        "<\\moveup\\@rmq\\begin{boite}a\\end{boite}>",
                        [("\\rmq{a}", "\\moveup\\@rmq\\begin{boite}a\\end{boite}", [])],
                    )
                ],
            ),
            # The end code takes the argument up again, then a conditional copies it on the next pass.
            (
                "\\NewDocumentEnvironment{x}{m}{}{\\ifstrempty{#1}{A}{B}}\\newcommand\\R{RR}\n"
                "\\begin{x}{\\R}z\\end{x}\n",
                [
                    ("\\begin{x}{\\R}", "\\begin{x}{RR}", [("\\R", "RR", [])]),
                    ("\\end{x}", "\\ifstrempty{RR}{A}{B}\\end{x}", []),
                ],
            ),
            # `\\R` is only defined on the first pass: its copy in the end code expands on the second.
            (
                "\\newcommand\\declare{\\newcommand\\R{RR}}\\NewDocumentEnvironment{x}{m}{}{(#1)}\n"
                "\\declare\n\\begin{x}{\\R}z\\end{x}\n",
                [
                    ("\\declare", "\\newcommand\\R{RR}", []),
                    ("\\begin{x}{\\R}", "\\begin{x}{RR}", [("\\R", "RR", [])]),
                    ("\\end{x}", "(RR)\\end{x}", []),
                ],
            ),
        ],
    )
    def test_uses_substituted_over_several_passes(self, parse, source, expected):
        assert self.tree(self.faces(parse, source)) == expected

    def test_expanding_everything_gives_the_view_back(self, parse):
        source = (
            RMQ + "\\newcommand\\vect[1]{\\overrightarrow{#1}}\\newif\\ifa\n"
            "\\NewDocumentEnvironment{sol}{m}{\\begin{proof}[#1]}{\\end{proof}}\n"
            "\\begin{sol}{$\\vect v$}\n\\rmq{$\\vect u$}\n\\ifa A\\else B\\fi\n\\end{sol}\n"
        )
        faces = self.faces(parse, source)
        text, view = faces._source.text, faces._target.text
        parts, cursor = [], 0
        for use in faces.roots:
            parts += [text[cursor : use.start], view[use.view_start : use.view_end]]
            cursor = use.end
        assert "".join([*parts, text[cursor:]]) == view

    def test_a_discarded_branch_is_dimmed_in_the_source(self, parse):
        faces = self.faces(parse, "\\iffalse A\\else B\\fi\n")
        assert '\\iffalse<span class="skip"> A</span>\\else' in faces.render()


class TestPage:
    def test_two_views_and_their_blocks(self, document):
        page = html_page(document)
        data = json.loads(
            re.search(r'<script type="application/json" id="data">(.*?)</script>', page).group(1)
        )
        assert (sorted(data), 'data-mode="source"' in page, 'data-mode="view"' in page) == (
            ["source", "view"],
            True,
            True,
        )

    def test_a_use_with_two_faces(self, parse):
        page = html_page(parse("\\newcommand\\vect[1]{\\overrightarrow{#1}}\n$\\vect u$\n"))
        assert (
            '<span class="u" data-a="2" data-b="2" title="\\vect"><span class="s">\\vect u</span>'
            '<span class="x">\\overrightarrow{u}</span></span>'
        ) in page

    def test_blocks_of_the_view_at_the_lines_of_the_source(self, parse):
        page = html_page(parse("\\def\\beq{\\begin{center}\n}\n\\def\\eeq{\\end{center}}\n\\beq x\n\\eeq\n"))
        data = json.loads(re.search(r'id="data">(.*?)</script>', page).group(1))
        (center,) = [block for block in data["view"]["c"] if block["l"] == "center"]
        assert (center["l"], center["a"], center["b"]) == ("center", 4, 5)

    def test_json_with_no_script_end(self, parse):
        page = html_page(parse("\\newcommand\\x{</script>}\n\\x\n"))
        assert page.count("</script>") == 2

    def test_a_view_given_directly(self, parse):
        tex = parse("\\def\\x{X}\n\\x\n")
        assert html_page(expand(tex)) == html_page(tex)
