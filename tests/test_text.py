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
            ("\\og Bonjour\\fg{} \\LaTeX\\ et \\ldots\n", "« Bonjour» LaTeX et …"),
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
            "\\label{sec:a}\\ref{sec:a}\n",
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


class TestMath:
    def test_renderinges_telles_qu_ecrites(self, text):
        assert text("vaut $E_c = \\frac12 m v^2$ ici\n").text.strip() == "vaut E_c = \\frac12 m v^2 ici"

    def test_skipped_on_demand(self, text):
        assert text("vaut $E_c$ ici\n", math="skip").text.strip() == "vaut ici"

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

    def test_the_body_of_a_macro_is_typeset(self, developed):
        source = "\\newcommand{\\rmq}[1]{Remarque : #1}\n\\rmq{attention}\n"
        assert developed(source).text.strip() == "Remarque : attention"
