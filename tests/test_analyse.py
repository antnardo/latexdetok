"""`TexFile`: reading files, reading the source back, queries on the examples."""

import pytest

from latexdetok import TexFile, read_lines


class TestReading:
    def test_from_a_path(self, tmp_path):
        path = tmp_path / "cours.tex"
        path.write_text("\\section{A}\n", encoding="utf-8")
        tex = TexFile(path)
        assert (tex.name, tex.src_file, tex.lines, tex.encoding) == (
            "cours.tex",
            path,
            ["\\section{A}\n"],
            "utf-8",
        )

    def test_from_a_path_string(self, tmp_path):
        path = tmp_path / "cours.tex"
        path.write_text("a\n", encoding="utf-8")
        assert TexFile(str(path)).lines == ["a\n"]

    @pytest.mark.parametrize(
        ("data", "forced", "encoding", "lines"),
        [
            ("é\n".encode(), None, "utf-8", ["é\n"]),
            ("\ufeffé\n".encode(), None, "utf-8-sig", ["é\n"]),
            ("\ufeff".encode(), None, "utf-8-sig", []),
            ("Étude\n".encode("latin-1"), None, "latin-1", ["Étude\n"]),
            ("é\n".encode(), "utf-8-sig", "utf-8", ["é\n"]),
            ("\ufeffé\n".encode(), "utf-8", "utf-8-sig", ["é\n"]),
            ("é\n".encode("cp1252"), "cp1252", "cp1252", ["é\n"]),
        ],
        ids=["utf-8", "bom", "bom-alone", "latin-1", "forced-sig-no-bom", "forced-utf-8-bom", "forced-other"],
    )
    def test_the_encoding_is_the_one_that_writes_the_file_back(self, tmp_path, data, forced, encoding, lines):
        # A byte order mark is not text: out of the first line, and in the encoding, which puts it back.
        path = tmp_path / "cours.tex"
        path.write_bytes(data)
        tex = TexFile(path, encoding=forced)
        assert (tex.encoding, tex.lines, "".join(tex.lines).encode(tex.encoding)) == (encoding, lines, data)

    def test_a_forced_encoding(self, tmp_path):
        path = tmp_path / "vieux.tex"
        path.write_bytes("Étude\n".encode("latin-1"))
        with pytest.raises(UnicodeDecodeError):
            TexFile(path, encoding="utf-8")

    def test_windows_line_endings_are_normalised(self, tmp_path):
        path = tmp_path / "crlf.tex"
        path.write_bytes(b"a\r\nb\r\n")
        assert TexFile(path).lines == ["a\n", "b\n"]

    def test_a_form_feed_does_not_cut_the_line(self, tmp_path):
        # `str.splitlines` would cut on \f and shift the line numbers.
        path = tmp_path / "ff.tex"
        path.write_bytes(b"a\x0cb\nc\n")
        assert len(read_lines(path)[1]) == 2

    def test_a_missing_file(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            TexFile(tmp_path / "absent.tex")

    def test_from_a_list(self):
        tex = TexFile(["a\n"])
        assert (tex.name, tex.src_file, tex.lines) == ("<List of strings content>", None, ["a\n"])

    def test_from_a_tuple_with_a_name(self):
        assert TexFile(("a\n",), name="extrait").name == "extrait"

    def test_an_invalid_source(self):
        with pytest.raises(TypeError, match="expected a path"):
            TexFile(42)  # type: ignore[arg-type]


class TestAnalysis:
    def test_returns_the_root(self):
        tex = TexFile(["a\n"])
        assert tex.analyse() is tex.container

    def test_before_the_analysis_the_root_is_empty(self):
        assert len(TexFile(["a\n"]).container) == 0

    def test_access_by_index(self, parse):
        assert str(parse("\\a \\b\n")[1]) == "\\b"

    def test_verbose_none_keeps_the_setting(self):
        tex = TexFile(["a\n"], verbose=True)
        tex.analyse()
        verbose = tex.verbose
        tex.set_verbose(False)
        assert verbose is True

    def test_content(self, parse):
        assert parse("\\section{A}\n\ntexte\n").content() == "\\section{A}\n\ntexte"

    def test_hierarchy(self, parse):
        assert parse("x\n").repr_hierarchy() == "<TexGroup latexfile><TexContent> : x"


class TestRawText:
    def test_an_element_of_another_file(self, parse):
        with pytest.raises(ValueError, match="does not belong"):
            parse("a\n").raw_text(parse("a\n").container[0])

    def test_over_several_lines_with_no_line_endings(self):
        tex = TexFile(["\\begin{center}", "x", "\\end{center}"])
        tex.analyse()
        assert tex.container[0].raw_text() == "\\begin{center}\nx\n\\end{center}"

    def test_the_root(self, parse):
        assert parse("a\nb\n").container.raw_text() == "a\nb"

    def test_an_empty_root(self):
        tex = TexFile([])
        tex.analyse()
        assert tex.container.raw_text() == ""


class TestGetLinesToNext:
    def test_up_to_the_end_of_the_document(self, parse):
        tex = parse("\\begin{document}\n\\section{A}\na\n\\section{B}\nb\n\\end{document}\n")
        assert tex.get_lines_to_next("section") == [["\\section{A}\n", "a\n"], ["\\section{B}\n", "b\n"]]

    def test_with_no_document_up_to_the_end(self, parse):
        tex = parse("\\section{A}\na\n")
        assert tex.get_lines_to_next("section") == [["\\section{A}\n", "a\n"]]

    def test_with_no_occurrence(self, parse):
        assert parse("a\n").get_lines_to_next("section") == []

    def test_every_piece_re_analyses(self, fixture_path):
        tex = TexFile(fixture_path("exam.tex"))
        tex.analyse()
        pieces = [TexFile(lines) for lines in tex.get_lines_to_next("section")]
        assert all(piece.analyse() is not None for piece in pieces)


class TestExamples:
    """Queries on the documents of `tests/fixtures/`, read as a whole."""

    def test_sections_of_the_exam(self, fixture_path):
        tex = TexFile(fixture_path("exam.tex"))
        tex.analyse()
        assert [section[-1].arg() for section in tex.get_sections()] == [
            "Diffusion of a solute",
            "A piston",
            "Plane capacitor",
        ]

    def test_figures_of_the_exam(self, fixture_path):
        tex = TexFile(fixture_path("exam.tex"))
        tex.analyse()
        assert [graphic[-1].arg() for graphic in tex.get_graphics()] == [
            "exam/tube",
            "exam/piston.pdf",
            "exam/capacitor",
            "exam/field",
        ]

    def test_splitting_of_the_exam(self, fixture_path):
        tex = TexFile(fixture_path("exam.tex"))
        tex.analyse()
        assert [len(lines) for lines in tex.get_lines_to_next("section")] == [13, 13, 11]

    @pytest.mark.parametrize(
        ("name", "envs"),
        [
            (
                "course.tex",
                {"center": 2, "itemize": 1, "enumerate": 1, "figure": 2, "remark": 1, "$": 16},
            ),
            ("exam.tex", {"center": 3, "itemize": 1, "enumerate": 1, "document": 1, "$": 11}),
            ("edge-cases.tex", {"center": 3, "itemize": 1, "document": 1, "$": 8, "verbatim": 1}),
        ],
    )
    def test_environments(self, fixture_path, name, envs):
        tex = TexFile(fixture_path(name))
        tex.analyse()
        assert {env: len(tex.get_envs(env)) for env in envs} == envs

    def test_the_sections_are_also_found_as_commands(self, fixture_path):
        tex = TexFile(fixture_path("course.tex"))
        tex.analyse()
        assert len(tex.get_commands_to_next("section")) == 3

    def test_diagnostics_of_the_examples(self, fixture_file):
        # edge-cases.tex holds `\pyc{234{}` on purpose, whose braces do not match.
        # course.tex writes an interval `$r\in\[0;\infty\[$`, which LaTeX refuses.
        expected = {
            "course.tex": [
                "44:27: error [math-in-math] “\\[” inside math already opened by “$”",
                "44:37: error [math-in-math] “\\[” inside math already opened by “$”",
            ],
            "edge-cases.tex": [
                "18:1: warning [verbatim-braces] unbalanced braces in “\\pyc{”: closed at the first “}”"
            ],
        }
        assert [str(diagnostic) for diagnostic in fixture_file.diagnostics] == expected.get(
            fixture_file.name, []
        )

    def test_escaped_brackets_in_math_make_no_formulas(self, fixture_path):
        # The old module opened math on every `\[`: `$r\in\[0;\infty\[$` gave three formulas
        # instead of one. Only the display math of line 35 opens with `\[`.
        tex = TexFile(fixture_path("course.tex"))
        tex.analyse()
        assert [group.delimiter for group in tex.get_envs("$")].count("\\[") == 1
