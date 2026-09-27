"""The typeset text: what is typeset, what is not, and where every character comes from."""

import pytest

from latexdetok import TexFile, expand
from latexdetok.text import to_text


@pytest.fixture
def text():
    def read(source, **options):
        tex = TexFile(source.splitlines(keepends=True))
        tex.analyse()
        return to_text(tex, **options)

    return read


@pytest.fixture
def developed():
    def read(source):
        tex = TexFile(source.splitlines(keepends=True))
        tex.analyse()
        return to_text(expand(tex))

    return read


class TestWhatIsTypeset:
    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            ("Un  texte   espac\\'e.\n", "Un texte espacé."),
            # A sentence cut by a line ending becomes a sentence again: that is the point.
            ("L'\\'energie\ncin\\'etique\n", "L'énergie cinétique"),
            ("a\\textbf{b}c\n", "abc"),
            # babel-french: `\\og` and `\\fg` bring their no-break space.
            ("\\og Bonjour\\fg{} \\LaTeX\\ et \\ldots\n", "«\u00a0Bonjour\u00a0» LaTeX et …"),
            ('\\c{c}a, \\"ile, na\\"ive\n', "ça, ïle, naïve"),
            ("\\verb|du code| ici\n", "du code ici"),
            ("Une macro \\inconnue{avec du texte}.\n", "Une macro avec du texte."),
            ("\\item[Étiquette] un point\n", "Étiquette un point"),
        ],
    )
    def test_rendering(self, text, source, expected):
        assert text(source).text.strip() == expected

    def test_the_breaks_that_matter(self, text):
        source = "\\section{Titre}\nDu texte\\\\ coupé.\n\n\\begin{itemize}\n\\item Un\n\\item Deux\n\\end{itemize}\n"
        assert text(source).text.strip() == "Titre\nDu texte\ncoupé.\n\nUn\nDeux"


class TestWhatIsNotTypeset:
    @pytest.mark.parametrize(
        "source",
        [
            "% un commentaire\n",
            "\\label{sec:a}\n",
            "\\includegraphics[width=2cm]{figure.png}\n",
            "\\newcommand{\\vect}[1]{le corps}\n",
            "\\hypersetup{colorlinks=true}\n",
            "\\begin{tikzpicture}\\draw (0,0) node {texte du dessin};\\end{tikzpicture}\n",
            "\\input{chapitre}\n",
        ],
    )
    def test_nothing_is_rendered(self, text, source):
        assert text(source).text.strip() == ""

    def test_an_optional_argument_of_settings(self, text):
        assert text("\\caption[court]{Le vrai titre}\n").text.strip() == "Le vrai titre"


class TestBlanksTeXSkips:
    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            # After a control word: `\\LaTeX is` prints “LaTeXis”, the mistake the PDF shows.
            ("c\\oe ur, Stra\\ss e, \\ae sthetic\n", "cœur, Straße, æsthetic"),
            ("\\LaTeX is here\n", "LaTeXis here"),
            ("\\LaTeX{} is, \\LaTeX\\ is\n", "LaTeX is, LaTeX is"),
            ("\\LaTeX\nis\n", "LaTeXis"),
            # A comment takes its line ending with it.
            ("word%\nnext, word % note\nnext\n", "wordnext, word next"),
            # Space that TeX computes, whatever the blanks around it.
            ("a\\hfill b, a\\hspace{1cm}b\n", "a b, a b"),
            # babel-french: `\\fg` removes the space before it.
            ("\\og texte \\fg{} suite\n", "«\u00a0texte\u00a0» suite"),
            # `\\i` and `\\j` lose their dot to take the accent.
            ("na\\\"\\i ve, Mar\\'\\i a\n", "naïve, María"),
        ],
    )
    def test_rendering(self, text, source, expected):
        assert text(source).text.strip() == expected


class TestNotes:
    def test_set_apart_at_the_end_of_the_paragraph(self, text):
        typeset = text(
            "A function\\footnote{A side note.} that grows. The end.\\footnote{See also.}\n\nNext.\n"
        )
        assert typeset.text == "A function that grows. The end.\n\nA side note.\n\nSee also.\n\nNext."

    def test_before_the_next_item(self, text):
        typeset = text("\\begin{itemize}\n\\item One\\footnote{Note.} two\n\\item Three\n\\end{itemize}\n")
        assert typeset.text.strip() == "One two\n\nNote.\n\nThree"

    def test_a_note_keeps_its_position(self, text):
        (found,) = text("Word\\footnote{A long\nnote.} end.\n").find("note")
        assert found.position == (2, 0)

    @pytest.mark.parametrize("command", ["marginpar", "todo", "footnotetext"])
    def test_other_notes(self, text, command):
        assert text(f"A\\{command}{{b}} c.\n").text == "A c.\n\nb"


class TestReferences:
    """TeX computes what a reference prints: a stand-in of the same kind keeps the sentence whole."""

    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            ("See Figure~\\ref{fig:a} and Section~\\ref{sec:b}.\n", "See Figure 1 and Section 1."),
            ("As in~\\eqref{eq:e} and \\vref{fig:b}, on page~\\pageref{x}.\n", "As in (1) and 1, on page 1."),
            ("As Einstein~\\cite{e05} and \\citep*[p.~3]{k}.\n", "As Einstein [1] and [1]."),
            ("As \\citeauthor{knuth84} (\\citeyear{knuth84}) says.\n", "As [1] (1) says."),
            ("A note says so\\footcite{k}.\n", "A note says so."),
            (
                "\\begin{thebibliography}{9}\n\\bibitem{k84} D. Knuth.\n\\bibitem[E]{e05} A. Einstein.\n\\end{thebibliography}\n",
                "D. Knuth.\nA. Einstein.",
            ),
        ],
    )
    def test_rendering(self, text, source, expected):
        assert text(source).text.strip() == expected


class TestMath:
    def test_renderinges_telles_qu_ecrites(self, text):
        assert text("vaut $E_c = \\frac12 m v^2$ ici\n").text.strip() == "vaut E_c = \\frac12 m v^2 ici"

    def test_skipped_on_demand(self, text):
        assert text("vaut $E_c$ ici\n", math="skip").text.strip() == "vaut ici"

    @pytest.mark.parametrize(
        ("math", "expected"), [("source", "The set M is closed."), ("skip", "The set is closed.")]
    )
    def test_ensuremath(self, text, math, expected):
        assert text("The set \\ensuremath{M} is closed.\n", math=math).text.strip() == expected

    def test_a_math_environment(self, text):
        assert text("\\begin{equation}\na = b\n\\end{equation}\n").text.strip() == "a = b"

    def test_an_unknown_setting_is_refused(self, text):
        with pytest.raises(ValueError, match="math expected"):
            text("a\n", math="latex")


class TestMap:
    def test_the_position_of_a_hit(self, text):
        found = text("Une ligne\nUn  point mat\\'eriel.\n").find("point matériel")
        assert [(item.start, item.position) for item in found] == [(13, (2, 4))]

    def test_the_node_of_a_hit(self, text):
        (found,) = text("Le \\textbf{mot} cherché.\n").find("mot")
        assert (str(found.node), found.position) == ("mot", (1, 11))

    def test_a_position_inside_a_rendered_blank(self, text):
        # The space between two nodes is written nowhere: we return the end of the piece before.
        composed = text("un\ndeux\n")
        assert composed.text[2] == " " and composed.position(2) == (1, 2)

    def test_outside_the_text(self, text):
        assert text("a\n").position(50) == (1, 1)

    def test_a_character_with_its_node(self, text):
        assert text("abc\n").at(1).position == (1, 1)

    @pytest.mark.parametrize("ending", ["\r\n", "\r"])
    def test_the_line_endings_of_the_file_change_nothing(self, text, ending):
        def mapped(typeset):
            return typeset.text, [
                (mark.offset, mark.length, mark.node.start_position) for mark in typeset.marks
            ]

        source = "Une ligne\nUn  point mat\\'eriel.\n\\begin{verbatim}\nbrut\n\\end{verbatim}\n"
        assert mapped(text(source.replace("\n", ending))) == mapped(text(source))


class TestExpandedView:
    def test_the_discarded_branch_typesets_nothing(self, developed):
        source = "\\newif\\ifprof\\proffalse\n\\ifprof La correction\\else L'énoncé\\fi\n"
        assert developed(source).text.strip() == "L'énoncé"

    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            ("\\newcommand{\\shout}[1]{#1\\itshape}\nThen \\shout{loud} words.\n", "Then loud words."),
            ("\\newcommand{\\pkg}{\\textsf{scalerel}}\nB and \\pkg\nC.\n", "B and scalerelC."),
        ],
    )
    def test_the_blanks_around_a_use(self, developed, source, expected):
        assert developed(source).text.strip() == expected

    def test_the_body_of_a_macro_is_typeset(self, developed):
        source = "\\newcommand{\\rmq}[1]{Remarque : #1}\n\\rmq{attention}\n"
        assert developed(source).text.strip() == "Remarque : attention"


class TestASkippedFormulaMeetsItsBlanks:
    """`math="skip"` takes the formula away, and the blanks it parted become one.

    An ordinary blank merges of itself — it is asked for, not written — but a
    control space and a `~` are written, and used to leave two spaces where the
    source had one word between two others. A spelling checker reads that as a
    mistake.
    """

    @pytest.mark.parametrize(
        "source",
        [
            "The space $x$ is.",
            "The space $x$\\ is.",
            "The space $x$~is.",
            "The space~$x$ is.",
            "The space\\ $x$ is.",
            "The space \\ensuremath{x}\\ is.",
            "The space \\(x\\)\\ is.",
            "The space $$x$$\\ is.",
        ],
    )
    def test_one_space_wherever_the_blanks_are_written(self, text, source):
        assert text(source + "\n", math="skip").text.strip() == "The space is."

    def test_two_formulas_in_a_row(self, text):
        assert text("The $x$\\ $y$\\ is.\n", math="skip").text.strip() == "The is."

    def test_a_tie_that_carries_punctuation(self, text):
        assert text("A $x$, et $y$~: fin.\n", math="skip").text.strip() == "A , et : fin."

    @pytest.mark.parametrize("source", ["The word \\ is.", "The word~ is."])
    def test_with_no_formula_the_two_spaces_are_texs_own(self, text, source):
        # A blank of the source followed by a space written by hand: TeX sets both.
        assert text(source + "\n", math="skip").text.strip() == "The word  is."

    def test_the_default_mode_is_untouched(self, text):
        assert text("The space $x$\\ is.\n").text.strip() == "The space x is."

    def test_every_character_still_knows_where_it_comes_from(self, text):
        composed = text("The space $x$\\ is.\n", math="skip")
        assert [composed.position(offset) for offset in range(len(composed.text.rstrip()))] == [
            (1, 0), (1, 1), (1, 2), (1, 3), (1, 4), (1, 5), (1, 6), (1, 7), (1, 8),
            (1, 9), (1, 15), (1, 16), (1, 17),
        ]  # fmt: skip
