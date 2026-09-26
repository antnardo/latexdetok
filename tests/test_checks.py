"""`check`: the diagnostics of the expanded view, brought back to the source, loaded files included."""

import shutil

import pytest

from latexdetok import Severity, TexFile
from latexdetok.checks import check

BEQ = "\\documentclass{article}\n\\def\\beq{\\begin{equation}}\n\\begin{document}\n\\beq x=1\n\n\\end{document}\n"


def described(diagnostics):
    return [(d.code, str(d.severity), d.start, d.end) for d in diagnostics]


def write(folder, name, text):
    path = folder / name
    path.write_text(text, encoding="utf-8")
    return path


class TestExpandedView:
    def test_structure_written_by_a_macro_is_brought_back_to_its_use(self):
        diagnostics = check(TexFile(BEQ.splitlines(keepends=True)), follow_inputs=False)
        (diagnostic,) = [d for d in diagnostics if d.severity is not Severity.INFO]
        assert (diagnostic.code, diagnostic.start, diagnostic.end, diagnostic.message) == (
            "unclosed-environment",
            (4, 0),
            (4, 4),
            "“\\begin{equation}” never closed, written by “\\beq”",
        )

    def test_a_macro_that_closes_what_the_source_opens(self):
        source = "\\def\\eeq{\\end{equation}}\n\\begin{equation}\nx\n\\eeq\n"
        tex = TexFile(source.splitlines(keepends=True))
        assert [d.code for d in check(tex, follow_inputs=False) if d.severity is not Severity.INFO] == []

    def test_without_expansion_the_source_alone(self):
        source = "\\def\\eeq{\\end{equation}}\n\\begin{equation}\nx\n\\eeq\n"
        tex = TexFile(source.splitlines(keepends=True))
        assert "unclosed-environment" in [d.code for d in check(tex, follow_inputs=False, expanded=False)]

    def test_meaning_read_in_the_view(self):
        source = "\\documentclass{article}\n\\newcommand\\R{\\mathbb{R}}\n\\begin{document}\nSoit \\alpha.\n\\end{document}\n"
        diagnostics = check(TexFile(source.splitlines(keepends=True)), follow_inputs=False)
        assert described(diagnostics) == [("math-command-in-text", "error", (4, 5), (4, 11))]

    def test_a_path_is_accepted(self, tmp_path):
        path = write(tmp_path, "cours.tex", BEQ)
        assert [d.code for d in check(path) if d.severity is Severity.ERROR] == ["unclosed-environment"]

    @pytest.mark.parametrize("ending", ["\r\n", "\r"])
    def test_the_line_endings_of_the_file_change_nothing(self, tmp_path, ending):
        (tmp_path / "lf.tex").write_bytes(BEQ.encode())
        (tmp_path / "other.tex").write_bytes(BEQ.replace("\n", ending).encode())
        assert described(check(tmp_path / "other.tex")) == described(check(tmp_path / "lf.tex"))


class TestLoadedFiles:
    def test_an_input_that_is_missing(self, tmp_path):
        path = write(
            tmp_path,
            "cours.tex",
            "\\documentclass{article}\n\\begin{document}\n\\input{absent}\n\\end{document}\n",
        )
        (diagnostic,) = check(path)
        assert (
            diagnostic.code,
            diagnostic.severity,
            diagnostic.start,
            diagnostic.end,
            diagnostic.message,
        ) == (
            "file-not-found",
            Severity.ERROR,
            (3, 6),
            (3, 14),
            "“\\input{absent}”: file not found",
        )

    def test_a_missing_include_warns(self, tmp_path):
        # LaTeX only writes “No file absent.tex” and carries on.
        path = write(
            tmp_path,
            "cours.tex",
            "\\documentclass{article}\n\\begin{document}\n\\include{absent}\n\\end{document}\n",
        )
        assert described(check(path)) == [("file-not-found", "warning", (3, 8), (3, 16))]

    def test_an_input_found_in_the_folder(self, tmp_path):
        write(tmp_path, "chapitre.tex", "Texte.\n")
        path = write(
            tmp_path,
            "cours.tex",
            "\\documentclass{article}\n\\begin{document}\n\\input{chapitre}\n\\end{document}\n",
        )
        assert check(path) == []

    @pytest.mark.skipif(
        shutil.which("kpsewhich") is None, reason="without TeX Live, a package is not looked for"
    )
    def test_a_missing_package_among_others(self, tmp_path):
        path = write(
            tmp_path,
            "cours.tex",
            "\\documentclass{article}\n\\usepackage{amsmath,paquetquinexistepas}\n\\begin{document}\n\\end{document}\n",
        )
        (diagnostic,) = check(path)
        assert diagnostic.message == "“\\usepackage{paquetquinexistepas}”: file not found"

    def test_without_following_inclusions_nothing_is_looked_for(self, tmp_path):
        path = write(
            tmp_path,
            "cours.tex",
            "\\documentclass{article}\n\\begin{document}\n\\input{absent}\n\\end{document}\n",
        )
        assert check(path, follow_inputs=False) == []


class TestWhatOneMistakeHides:
    """Independent mistakes are all reported; what an unmatched opening swallows is not.

    Reporting the `\\item` that follows a `$` left open would be reporting the
    same mistake twice: inside that math, an `\\item` is not an `\\item` out of
    place. The editor shows the next one as soon as the first is settled.
    """

    def test_two_independent_mistakes_are_both_reported(self, parse):
        tex = parse("\\begin{document}\n\\item un\n\nsuite.\n\\item deux\n\\end{document}\n")
        assert [d.code for d in check(tex)] == ["item-outside-list", "item-outside-list"]

    def test_what_comes_before_an_unclosed_opening_is_still_reported(self, parse):
        tex = parse("\\begin{document}\n\\item hors liste\n\nSoit $x\n\nsuite.\n\\end{document}\n")
        assert [d.code for d in check(tex)] == ["item-outside-list", "unclosed-math"]

    def test_what_an_unclosed_math_swallows_waits_its_turn(self, parse):
        tex = parse("\\begin{document}\nSoit $x\n\nsuite.\n\\item hors liste\n\\end{document}\n")
        assert [d.code for d in check(tex)] == ["unclosed-math"]
        settled = parse("\\begin{document}\nSoit $x$\n\nsuite.\n\\item hors liste\n\\end{document}\n")
        assert [d.code for d in check(settled)] == ["item-outside-list"]
