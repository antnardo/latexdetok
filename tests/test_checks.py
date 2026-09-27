"""`check`: the diagnostics of the expanded view, brought back to the source, loaded files included."""

import shutil

import pytest

from latexdetok import Severity, TexFile
from latexdetok.checks import check
from latexdetok.diagnostics import Repair
from latexdetok.export import Edit, rewrite

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


REPAIR_CASES = {
    "math cut by a blank line": "\\begin{document}\nSoit $x\n\nsuite.\n\\end{document}\n",
    "math cut by a closing brace": "\\begin{document}\n\\section{Le $x titre}\na\n\\end{document}\n",
    "math cut by an \\end": "\\begin{document}\n\\begin{center}\n$x\n\\end{center}\n\\end{document}\n",
    "brace cut by a blank line": "\\begin{document}\n\\emph{a\n\nsuite.\n\\end{document}\n",
    "brace cut by an \\end": "\\begin{document}\n\\begin{center}\n\\emph{a\n\\end{center}\n\\end{document}\n",
    "environment never closed": "\\begin{document}\n\\begin{center}\na\n\\end{document}\n",
    "bracket never closed": "\\begin{document}\n\\includegraphics[width=2cm\n\na\n\\end{document}\n",
    "brace too many": "\\begin{document}\na}\nsuite.\n\\end{document}\n",
}


class TestRepairs:
    """What `suggestion` says in words, `repairs` says as edits — and applying it settles it."""

    @pytest.mark.parametrize("source", REPAIR_CASES.values(), ids=list(REPAIR_CASES))
    def test_applying_the_repair_settles_the_diagnostic(self, source):
        tex = TexFile(source.splitlines(keepends=True), name="c.tex")
        repaired = 0
        for diagnostic in check(tex):
            if not diagnostic.repairs:
                continue
            repaired += 1
            edits = [Edit(repair.start, repair.end, repair.text) for repair in diagnostic.repairs]
            written = rewrite(tex, edits).splitlines(keepends=True)
            again = check(TexFile(written, name="c.tex"))
            assert diagnostic.code not in [found.code for found in again]
        assert repaired == 1

    def test_the_closing_lands_where_the_message_says(self):
        source = "\\begin{document}\nSoit $x\n\nsuite.\n\\end{document}\n"
        tex = TexFile(source.splitlines(keepends=True), name="c.tex")
        (diagnostic,) = [found for found in check(tex) if found.repairs]
        # Not on the blank line, which would stop being blank and weld two paragraphs.
        assert diagnostic.repairs == (Repair((2, 7), (2, 7), "$"),)

    def test_an_info_carries_no_repair(self):
        # `\newcommand{\beq}{\begin{equation}}` is how that kind of macro is written:
        # repairing it would close the environment inside the definition and break it.
        source = "\\newcommand{\\beq}{\\begin{equation}}\n\\begin{document}\n\\beq x\n\\end{document}\n"
        found = check(TexFile(source.splitlines(keepends=True), name="c.tex"))
        assert [d.severity for d in found if d.repairs] == []

    def test_what_a_macro_body_wrote_is_not_repaired_in_the_source(self):
        # The place to repair is the definition, in another file's business.
        source = "\\newcommand{\\beq}{\\begin{equation}}\n\\begin{document}\n\\beq x\n\\end{document}\n"
        (error,) = [
            d for d in check(TexFile(source.splitlines(keepends=True))) if d.severity is Severity.ERROR
        ]
        assert (error.code, error.repairs) == ("unclosed-environment", ())

    def test_a_brace_that_hides_in_a_comment_is_not_repaired(self):
        # The message offers another reading — that “}” freed of its “%” — and a repair
        # elsewhere would close the group against it.
        source = "\\begin{document}\n\\emph{a\n% et }\nsuite.\n\\end{document}\n"
        (diagnostic,) = [
            d for d in check(TexFile(source.splitlines(keepends=True))) if d.code == "unclosed-brace"
        ]
        assert diagnostic.repairs == ()
