"""Resolving the loaded files: the document folder, the texmf trees, catcodes, cache."""

import os
import shutil

import pytest

from latexdetok import CatcodeTable, Category, TexCommand, TexFile, resolution
from latexdetok.resolution import TexmfResolver, clear_caches, decode_lines, read_lines

requires_kpsewhich = pytest.mark.skipif(shutil.which("kpsewhich") is None, reason="TeX Live absent")


@pytest.fixture(autouse=True)
def _caches_vides():
    clear_caches()
    yield
    clear_caches()


@pytest.fixture
def texmf(tmp_path, monkeypatch):
    """A test `TEXMFHOME` tree; returns a function that writes a file into it."""
    home = tmp_path / "texmf"
    monkeypatch.setenv("TEXMFHOME", str(home))

    def write(name, text):
        path = home / "tex" / "latex" / "essai" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    return write


@pytest.fixture
def document(tmp_path):
    """Writes the main document into its folder and analyses it, following the inclusions."""

    def analyse(text, name="cours.tex"):
        folder = tmp_path / "cours"
        folder.mkdir(exist_ok=True)
        path = folder / name
        path.write_text(text, encoding="utf-8")
        tex = TexFile(path)
        tex.analyse(follow_inputs=True)
        return tex

    return analyse


def spec(tex, name):
    signature = tex.signatures.command(name)
    return None if signature is None else signature.spec


class TestSearching:
    def test_the_documents_folder_first(self, tmp_path):
        (tmp_path / "defs.tex").write_text("", encoding="utf-8")
        assert TexmfResolver(tmp_path).find("defs", "tex") == tmp_path / "defs.tex"

    def test_the_name_as_it_stands_after_the_extension(self, tmp_path):
        (tmp_path / "notes.aux").write_text("", encoding="utf-8")
        assert TexmfResolver(tmp_path).find("notes.aux", "tex") == tmp_path / "notes.aux"

    def test_an_extension_already_given(self, tmp_path):
        (tmp_path / "defs.tex").write_text("", encoding="utf-8")
        assert TexmfResolver(tmp_path).find("defs.tex", "tex") == tmp_path / "defs.tex"

    def test_a_relative_path(self, tmp_path):
        (tmp_path / "LaTeX").mkdir()
        (tmp_path / "LaTeX" / "definitions.tex").write_text("", encoding="utf-8")
        assert (
            TexmfResolver(tmp_path).find("LaTeX/definitions", "tex") == tmp_path / "LaTeX" / "definitions.tex"
        )

    @requires_kpsewhich
    def test_the_users_texmf_tree(self, tmp_path, texmf):
        path = texmf("monpaquet.sty", "")
        assert TexmfResolver(tmp_path).find("monpaquet", "sty") == path

    @requires_kpsewhich
    def test_not_found(self, tmp_path, texmf):
        assert TexmfResolver(tmp_path).find("paquet-qui-n-existe-pas", "sty") is None

    def test_an_explicit_path_is_not_looked_for_in_texmf(self, tmp_path, monkeypatch):
        calls = []
        monkeypatch.setattr(resolution, "_kpsewhich", lambda *arguments: calls.append(arguments))
        TexmfResolver(tmp_path).find("../LaTeX/definitions", "tex")
        assert calls == []

    @requires_kpsewhich
    def test_pythons_current_folder_is_ignored(self, tmp_path, monkeypatch, texmf):
        # kpsewhich looks in “.”: it must not find whatever lies where Python happens to run.
        elsewhere = tmp_path / "ailleurs"
        elsewhere.mkdir()
        (elsewhere / "piege.sty").write_text("", encoding="utf-8")
        monkeypatch.chdir(elsewhere)
        document_folder = tmp_path / "cours"
        document_folder.mkdir()
        assert TexmfResolver(document_folder).find("piege", "sty") is None

    def test_without_tex_live(self, tmp_path, monkeypatch):
        monkeypatch.setattr(resolution.shutil, "which", lambda _: None)
        assert TexmfResolver(tmp_path).find("article", "cls") is None

    def test_a_texmf_search_happens_only_once(self, tmp_path, monkeypatch):
        calls = []
        monkeypatch.setattr(resolution, "_kpsewhich", lambda *arguments: calls.append(arguments))
        resolver = TexmfResolver(tmp_path)
        resolver.find("absent", "sty")
        resolver.find("absent", "sty")
        assert calls == [("-must-exist", "absent.sty")]


@requires_kpsewhich
class TestKpsewhichSession:
    """One single `kpsewhich -interactive` answers every search of the process."""

    def test_it_answers_like_a_plain_call(self, tmp_path, texmf):
        path = texmf("monpaquet.sty", "")
        for name in ("monpaquet.sty", "absent.sty", "article.cls", "article"):
            answered, found = resolution._session_find(shutil.which("kpsewhich"), name)
            single = resolution.subprocess.run(
                ["kpsewhich", "-must-exist", name], capture_output=True, text=True, check=False, cwd="/"
            ).stdout.strip()
            assert answered
            assert found == (single or None)
        assert TexmfResolver(tmp_path).find("monpaquet", "sty") == path

    def test_one_single_session_for_several_searches(self, tmp_path, texmf, monkeypatch):
        texmf("un.sty", "")
        texmf("deux.sty", "")
        started = []
        session = resolution._Session
        monkeypatch.setattr(
            resolution, "_Session", lambda executable: started.append(1) or session(executable)
        )
        resolver = TexmfResolver(tmp_path)
        assert resolver.find("un", "sty") is not None
        assert resolver.find("deux", "sty") is not None
        assert resolver.find("trois", "sty") is None
        assert started == [1]

    def test_with_no_pseudo_terminal_one_call_per_search(self, tmp_path, texmf, monkeypatch):
        path = texmf("monpaquet.sty", "")
        monkeypatch.setattr(resolution, "pty", None)
        assert TexmfResolver(tmp_path).find("monpaquet", "sty") == path
        assert resolution._sessions == {}

    def test_a_broken_session_falls_back_to_the_plain_call(self, tmp_path, texmf, monkeypatch):
        path = texmf("monpaquet.sty", "")

        def broken(self, filename):
            raise TimeoutError("no answer")

        monkeypatch.setattr(resolution._Session, "find", broken)
        assert TexmfResolver(tmp_path).find("monpaquet", "sty") == path
        assert list(resolution._sessions.values()) == [None]

    @pytest.mark.parametrize("name", ["", "  ", "-version"])
    def test_names_the_session_does_not_take(self, name):
        assert resolution._session_find(shutil.which("kpsewhich"), name) == (False, None)

    def test_clearing_the_caches_closes_the_session(self, tmp_path, texmf):
        texmf("monpaquet.sty", "")
        TexmfResolver(tmp_path).find("monpaquet", "sty")
        (session,) = resolution._sessions.values()
        clear_caches()
        assert resolution._sessions == {}
        assert session._process.poll() is not None


@requires_kpsewhich
class TestDefinitions:
    def test_a_user_package(self, texmf, document):
        texmf("monpaquet.sty", "\\newcommand\\vect[1]{\\overrightarrow{#1}}\n")
        tex = document("\\usepackage{monpaquet}\n$\\vect u$\n")
        (vect,) = [node for node in tex.iter() if isinstance(node, TexCommand) and node.base_name == "vect"]
        assert (spec(tex, "vect"), [str(node) for node in vect.arguments]) == ("+m", ["u"])

    def test_several_packages_in_one_usepackage(self, texmf, document):
        texmf("un.sty", "\\newcommand\\un[1]{}\n")
        texmf("deux.sty", "\\newcommand\\deux[2]{}\n")
        tex = document("\\usepackage[opt]{un, deux}\n")
        assert (spec(tex, "un"), spec(tex, "deux")) == ("+m", "+m +m")

    def test_nested_inclusions_in_the_tree(self, texmf, document):
        texmf("monpaquet.sty", "\\input{raccourcis}\n")
        texmf("raccourcis.tex", "\\newcommand\\dd{\\mathrm{d}}\n")
        assert spec(document("\\usepackage{monpaquet}\n"), "dd") == ""

    def test_a_class(self, texmf, document):
        texmf("macours.cls", "\\LoadClass{book}\n\\newcommand\\chapitre[1]{}\n")
        assert spec(document("\\documentclass{macours}\n"), "chapitre") == "+m"

    def test_the_distribution_is_found_but_not_read(self, document):
        # amsmath defines \numberwithin and \Vvert; its sources are not read: only what
        # `data/packages.txt` declares is known (see the header of `resolution`).
        tex = document("\\usepackage{amsmath}\n")
        assert (
            TexmfResolver(tex.src_file.parent).find("amsmath", "sty") is not None,
            spec(tex, "numberwithin"),
            spec(tex, "Vvert"),
        ) == (True, "m m", None)

    def test_the_included_content_stays_outside_the_tree(self, texmf, document):
        texmf("monpaquet.sty", "\\section{Ailleurs}\n")
        assert len(document("\\usepackage{monpaquet}\n").get_sections()) == 0


@requires_kpsewhich
class TestCatcodes:
    def test_a_package_is_read_with_at_sign_as_a_letter(self, texmf, document):
        # Without this, \newcommand\rslt@opt would read as \newcommand\rslt followed by @opt.
        texmf("monpaquet.sty", "\\newcommand\\rslt[1]{#1}\n\\newcommand\\rslt@opt[2]{#1#2}\n")
        tex = document("\\usepackage{monpaquet}\n")
        assert (spec(tex, "rslt"), spec(tex, "rslt@opt")) == ("+m", "+m +m")

    def test_a_package_does_not_change_the_documents_catcodes(self, texmf, document):
        texmf("monpaquet.sty", "\\catcode`\\%=12\n")
        tex = document("\\usepackage{monpaquet}\n% commentaire\n")
        assert tex.container[-1].is_comment()

    def test_an_input_keeps_its_changes(self, texmf, document):
        texmf("reglages.tex", "\\makeatletter\n")
        tex = document("\\input{reglages}\n\\a@b\n")
        assert str(tex.container[-1]) == "\\a@b"

    def test_an_input_is_read_with_the_includers_table(self, texmf, document):
        texmf("interne.tex", "\\newcommand\\am@x[1]{}\n")
        assert spec(document("\\makeatletter\n\\input{interne}\n"), "am@x") == "+m"

    def test_an_input_with_no_makeatletter(self, texmf, document):
        texmf("interne.tex", "\\newcommand\\am@x[1]{}\n")
        assert spec(document("\\input{interne}\n"), "am@x") is None


@requires_kpsewhich
class TestCache:
    def test_the_definitions_are_replayed(self, texmf, document, monkeypatch):
        texmf("monpaquet.sty", "\\newcommand\\vect[1]{}\n")
        document("\\usepackage{monpaquet}\n")
        reads = []
        original = TexmfResolver._read
        monkeypatch.setattr(
            TexmfResolver, "_read", lambda self, *args: reads.append(args[1]) or original(self, *args)
        )
        tex = document("\\usepackage{monpaquet}\n", name="autre.tex")
        assert (spec(tex, "vect"), reads) == ("+m", [])

    def test_a_body_is_replayed_with_its_table(self, texmf, document):
        texmf("monpaquet.sty", "\\newcommand\\vect[1]{\\@vect{#1}}\n")
        document("\\usepackage{monpaquet}\n")
        macro = document("\\usepackage{monpaquet}\n", name="autre.tex").signatures.macro("vect")
        assert (macro.body, macro.catcodes) == ("\\@vect{#1}", CatcodeTable.package())

    def test_the_cache_key_follows_the_booleans(self, texmf, document):
        texmf("monpaquet.sty", "\\ifprof\\newcommand\\vect[1]{}\\fi\n")
        without = document("\\newif\\ifprof\n\\usepackage{monpaquet}\n")
        with_prof = document("\\newif\\ifprof\\proftrue\n\\usepackage{monpaquet}\n", name="autre.tex")
        assert (spec(without, "vect"), spec(with_prof, "vect")) == (None, "+m")

    def test_a_changed_file_is_read_again(self, texmf, document):
        path = texmf("monpaquet.sty", "\\newcommand\\vect[1]{}\n")
        document("\\usepackage{monpaquet}\n")
        path.write_text("\\newcommand\\vect[2]{}\n", encoding="utf-8")
        os.utime(path, ns=(path.stat().st_atime_ns, path.stat().st_mtime_ns + 1_000_000_000))
        assert spec(document("\\usepackage{monpaquet}\n"), "vect") == "+m +m"

    def test_a_complete_journal_despite_an_inclusion_already_read(self, texmf, document):
        texmf("raccourcis.tex", "\\newcommand\\dd{}\n")
        texmf("monpaquet.sty", "\\input{raccourcis}\n\\newcommand\\vect[1]{}\n")
        document("\\input{raccourcis}\n\\usepackage{monpaquet}\n")
        tex = document("\\usepackage{monpaquet}\n", name="autre.tex")
        assert (spec(tex, "dd"), spec(tex, "vect")) == ("", "+m")

    def test_a_circular_inclusion_is_not_cached(self, texmf, document):
        texmf("a.tex", "\\input{b}\n\\newcommand\\x[1]{}\n")
        texmf("b.tex", "\\input{a}\n\\newcommand\\y[2]{}\n")
        tex = document("\\input{a}\n")
        assert (spec(tex, "x"), spec(tex, "y"), len(resolution._definitions)) == ("+m", "+m +m", 0)

    def test_the_cache_key_follows_the_table(self, texmf, document):
        texmf("interne.tex", "\\newcommand\\am@x[1]{}\n")
        document("\\input{interne}\n")
        assert spec(document("\\makeatletter\n\\input{interne}\n", name="autre.tex"), "am@x") == "+m"


def test_the_starting_table_of_an_inclusion(tmp_path):
    (tmp_path / "defs.tex").write_text("\\makeatother\n", encoding="utf-8")
    table = CatcodeTable.latex().with_categories({"@": Category.LETTER})
    resolver = TexmfResolver(tmp_path)
    from latexdetok.signatures import SignatureRegistry

    assert resolver.include(SignatureRegistry.kernel(), "defs", "tex", table) == CatcodeTable.latex()


class TestMasterDocument:
    def test_the_declared_root(self, tmp_path):
        (tmp_path / "EM.tex").write_text("", encoding="utf-8")
        chapter = tmp_path / "EM1.tex"
        assert resolution.root_of(chapter, ["% !TEX root = ./EM.tex\n", "\\chapter{x}\n"]) == (
            tmp_path / "EM.tex"
        )

    @pytest.mark.parametrize(
        "lines",
        [["\\chapter{x}\n"], ["% !TEX root = ./absent.tex\n"], ["% !TEX root = EM1.tex\n"]],
    )
    def test_with_no_usable_root(self, tmp_path, lines):
        # No declaration, a master that cannot be found, or the file naming itself.
        assert resolution.root_of(tmp_path / "EM1.tex", lines) is None

    def test_a_chapter_is_read_after_the_masters_preamble(self, tmp_path):
        (tmp_path / "EM.tex").write_text(
            "\\newcommand\\vect[1]{#1}\n\\begin{document}\n\\include{EM1}\n\\newcommand\\apres[1]{}\n\\end{document}\n",
            encoding="utf-8",
        )
        chapter = tmp_path / "EM1.tex"
        chapter.write_text("% !TEX root = ./EM.tex\n$\\vect u$\n", encoding="utf-8")
        tex = TexFile(chapter)
        tex.analyse(follow_inputs=True)
        assert (tex.root, spec(tex, "vect"), spec(tex, "apres")) == (tmp_path / "EM.tex", "+m", None)

    def test_inclusions_are_looked_for_from_the_masters_folder(self, tmp_path):
        (tmp_path / "defs.tex").write_text("\\newcommand\\vect[1]{}\n", encoding="utf-8")
        (tmp_path / "main.tex").write_text(
            "\\input{defs}\n\\begin{document}\n\\end{document}\n", encoding="utf-8"
        )
        (tmp_path / "chapitres").mkdir()
        chapter = tmp_path / "chapitres" / "ch1.tex"
        chapter.write_text("% !TEX root = ../main.tex\n", encoding="utf-8")
        tex = TexFile(chapter)
        tex.analyse(follow_inputs=True)
        assert spec(tex, "vect") == "+m"

    def test_the_catcodes_of_the_preamble(self, tmp_path):
        (tmp_path / "main.tex").write_text("\\makeatletter\n\\begin{document}\n", encoding="utf-8")
        chapter = tmp_path / "ch1.tex"
        chapter.write_text("% !TEX root = main.tex\n\\a@b\n", encoding="utf-8")
        tex = TexFile(chapter)
        tex.analyse(follow_inputs=True)
        assert str(tex.container[-1]) == "\\a@b"

    def test_without_follow_inputs_the_master_is_not_read(self, tmp_path):
        (tmp_path / "main.tex").write_text("\\newcommand\\vect[1]{}\n", encoding="utf-8")
        chapter = tmp_path / "ch1.tex"
        chapter.write_text("% !TEX root = main.tex\n", encoding="utf-8")
        tex = TexFile(chapter)
        tex.analyse()
        assert (tex.root, spec(tex, "vect")) == (None, None)


class TestDecodeLines:
    """The bytes of a buffer read like the bytes of a file: an editor sees the same document."""

    @pytest.mark.parametrize(
        ("data", "expected"),
        [
            (b"a\nb\n", ["a\n", "b\n"]),
            (b"a\r\nb\r\n", ["a\r\n", "b\r\n"]),
            (b"a\rb\r", ["a\r", "b\r"]),
            (b"a\r\nb\nc\r", ["a\r\n", "b\n", "c\r"]),
            (b"a\nb", ["a\n", "b"]),
            (b"", []),
            # `str.splitlines` would cut on the form feed and shift every line number after it.
            (b"a\x0cb\nc\n", ["a\x0cb\n", "c\n"]),
        ],
    )
    def test_the_line_endings_are_the_ones_written(self, data, expected):
        assert decode_lines(data)[1] == expected

    @pytest.mark.parametrize(
        ("data", "expected"),
        [
            ("é\n".encode(), ("utf-8", ["é\n"])),
            ("\ufeffé\n".encode(), ("utf-8-sig", ["é\n"])),
            ("\ufeff\né\n".encode(), ("utf-8-sig", ["\n", "é\n"])),
            ("Étude\n".encode("latin-1"), ("latin-1", ["Étude\n"])),
        ],
    )
    def test_the_encoding_found(self, data, expected):
        assert decode_lines(data) == expected

    def test_a_forced_encoding_that_does_not_read(self):
        with pytest.raises(UnicodeDecodeError):
            decode_lines("Étude\n".encode("latin-1"), "utf-8")

    def test_a_file_and_its_bytes_read_the_same(self, tmp_path):
        data = "\ufeffÉtude\r\nde $x$\n".encode()
        path = tmp_path / "cours.tex"
        path.write_bytes(data)
        assert read_lines(path) == decode_lines(data)
