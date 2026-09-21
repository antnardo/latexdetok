"""Catcode tables, the commands that change them, and their scope while reading."""

import pytest

from latexdetok import CatcodeChange, CatcodeTable, Category, TexFile
from latexdetok.catcodes import interpret


class TestTable:
    @pytest.mark.parametrize(
        ("character", "category"),
        [
            ("\\", Category.ESCAPE),
            ("{", Category.BEGIN_GROUP),
            ("}", Category.END_GROUP),
            ("$", Category.MATH_SHIFT),
            ("&", Category.ALIGNMENT),
            ("\n", Category.END_OF_LINE),
            ("#", Category.PARAMETER),
            ("^", Category.SUPERSCRIPT),
            ("_", Category.SUBSCRIPT),
            (" ", Category.SPACE),
            ("a", Category.LETTER),
            ("Z", Category.LETTER),
            ("@", Category.OTHER),
            ("[", Category.OTHER),
            ("é", Category.OTHER),
            ("~", Category.ACTIVE),
            ("%", Category.COMMENT),
        ],
    )
    def test_the_table_of_a_document(self, character, category):
        assert CatcodeTable.latex().category(character) is category

    def test_at_sign_is_a_letter_in_a_package(self):
        assert CatcodeTable.package().is_letter("@")

    def test_immutable(self):
        table = CatcodeTable.latex()
        table.with_categories({"@": Category.LETTER})
        assert table.category("@") is Category.OTHER

    def test_equality_by_content(self):
        rebuilt = (
            CatcodeTable.latex()
            .with_categories({"@": Category.LETTER})
            .with_categories({"@": Category.OTHER})
        )
        assert (rebuilt == CatcodeTable.latex(), hash(rebuilt) == hash(CatcodeTable.latex())) == (True, True)

    def test_differences(self):
        assert list(CatcodeTable.latex().differences(CatcodeTable.package())) == [
            ("@", Category.OTHER, Category.LETTER)
        ]

    def test_repr_shows_the_gap_to_latex(self):
        assert repr(CatcodeTable.package()) == "CatcodeTable(latex + {'@': 11})"


class TestInterpret:
    @pytest.mark.parametrize(
        ("command", "following", "expected"),
        [
            ("makeatletter", "", {"@": Category.LETTER}),
            ("makeatother", "", {"@": Category.OTHER}),
            ("catcode", "`\\@=11", {"@": Category.LETTER}),
            ("catcode", "`@=12", {"@": Category.OTHER}),
            ("catcode", " 64 = 11", {"@": Category.LETTER}),
            ("catcode", '"40 11', {"@": Category.LETTER}),
            ("catcode", "'100=11", {"@": Category.LETTER}),
            ("catcode", "`\\^^M=13", {"\r": Category.ACTIVE}),
            ("catcode", "`\\%=\\active", {"%": Category.ACTIVE}),
            ("catcode", "`\\|=\\@other", {"|": Category.OTHER}),
            ("@makeother", "\\%", {"%": Category.OTHER}),
        ],
    )
    def test_the_changes_understood(self, command, following, expected):
        assert interpret(command, following) == expected

    @pytest.mark.parametrize(
        ("command", "following"),
        [("catcode", "`\\@=16"), ("catcode", "\\count0"), ("section", ""), ("@makeother", "")],
    )
    def test_nothing_to_change(self, command, following):
        assert interpret(command, following) is None

    def test_expl3_then_back(self):
        on, off = interpret("ExplSyntaxOn", ""), interpret("ExplSyntaxOff", "")
        table = CatcodeTable.latex().with_categories(on).with_categories(off)
        assert (on["_"], on[" "], table) == (Category.LETTER, Category.IGNORED, CatcodeTable.latex())


class TestReading:
    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            (
                "\\makeatletter\\a@b\\makeatother\\a@b\n",
                ["\\makeatletter", "\\a@b", "\\makeatother", "\\a", "@b"],
            ),
            ("\\catcode`\\@=11 \\a@b\n", ["\\catcode", "`", "\\@", "=11", "\\a@b"]),
            ("\\makeatletter\\@ifnextchar[\n", ["\\makeatletter", "\\@ifnextchar", "["]),
        ],
    )
    def test_at_sign_as_a_letter(self, tree, source, expected):
        assert tree(source) == expected

    def test_a_non_ascii_letter_in_a_name(self, tree):
        # Valid under XeTeX and LuaTeX: the name is read per the table, with no raise.
        assert tree("\\catcode`\\é=11 \\éte x\n") == ["\\catcode", "`", "\\é", "=11", "\\éte", "x"]

    def test_the_scope_of_a_group(self, tree):
        assert tree("{\\makeatletter\\a@b}\\a@b\n") == [("{", ["\\makeatletter", "\\a@b"]), "\\a", "@b"]

    def test_the_scope_of_an_environment(self, tree):
        assert tree("\\begin{x}\\makeatletter\\end{x}\\a@b\n") == [("x", ["\\makeatletter"]), "\\a", "@b"]

    def test_the_scope_of_begingroup(self, tree):
        assert tree("\\begingroup\\makeatletter\\a@b\\endgroup\\a@b\n") == [
            "\\begingroup", "\\makeatletter", "\\a@b", "\\endgroup", "\\a", "@b",
        ]  # fmt: skip

    def test_global_survives_the_group(self, tree):
        assert tree("{\\global\\catcode`\\@=11}\\a@b\n")[-1] == "\\a@b"

    def test_an_optional_argument_is_not_a_group(self, tree):
        assert tree("\\foo[\\makeatletter]\\a@b\n")[-1] == "\\a@b"

    def test_a_dissolved_group_keeps_its_changes(self, tree):
        # The option dissolves at the blank line; `\\a@b` is read after it, in the parent group.
        assert tree("\\foo[\\makeatletter\n\n\\a@b\n")[-1] == "\\a@b"

    def test_an_open_group_inherits_the_table(self, tree):
        assert tree("\\makeatletter{\\a@b}\n")[-1] == ("{", ["\\a@b"])

    def test_an_ordinary_percent(self, tree):
        assert tree("\\catcode`\\%=12 50% de\n") == ["\\catcode", "`", "\\%", "=12 50% de"]

    def test_an_ordinary_dollar(self, tree):
        assert tree("\\catcode`\\$=12 5$\n") == ["\\catcode", "`", "\\$", "=12 5$"]

    def test_an_ordinary_brace_with_no_warning(self, parse):
        tex = parse("\\catcode`\\}=12 a}\n")
        assert (str(tex.container[-1]), tex.diagnostics) == ("=12 a}", [])

    def test_another_escape_character_stays_text(self, tree):
        # See the limit of `catcodes`: `str()` would not know how to rewrite `|section`.
        assert tree("\\catcode`\\|=0 |section\n")[-1] == "=0 |section"

    def test_the_rewriting_does_not_change(self, parse):
        source = "\\makeatletter\n\\def\\a@b{\\c@d}\n\\makeatother\n\\a@b"
        assert parse(source).content() == source

    def test_verbatim_is_untouched(self, parse):
        verbatim = parse("\\makeatletter\\verb|\\a@b|\n").container[1]
        assert verbatim.content == "\\a@b"


class TestExpl3:
    SOURCE = "\\ExplSyntaxOn\n\\cs_new:Npn \\foo_bar:n #1 { x }\n\n\\ExplSyntaxOff\n\\foo_bar:n\n"

    def test_expl3_names(self, tree):
        assert tree(self.SOURCE)[:3] == ["\\ExplSyntaxOn", "\\cs_new:Npn", "\\foo_bar:n"]

    def test_back_to_latex(self, tree):
        assert tree(self.SOURCE)[-2:] == ["\\foo", "_bar:n"]

    def test_a_blank_line_is_ignored(self, tree):
        # An ignored line ending: no paragraph, as in TeX.
        assert "PAR" not in tree(self.SOURCE)

    def test_a_tilde_is_a_space(self, tree):
        assert tree("\\ExplSyntaxOn \\a~\\b\n") == ["\\ExplSyntaxOn", "\\a", "\\b"]


class TestTracking:
    def test_the_changes_are_noted(self, parse):
        tex = parse("\\makeatletter\n{\\global\\catcode`\\%=12}\n")
        assert tex.catcode_changes == [
            CatcodeChange((1, 0), "@", Category.OTHER, Category.LETTER, "makeatletter"),
            CatcodeChange((2, 8), "%", Category.COMMENT, Category.OTHER, "catcode", local=False),
        ]

    def test_a_change_with_no_effect_is_not_noted(self, parse):
        assert parse("\\makeatother\n").catcode_changes == []

    def test_readable(self):
        change = CatcodeChange((3, 0), "@", Category.OTHER, Category.LETTER, "makeatletter")
        assert str(change) == "line 3, column 0: \\makeatletter: '@' other (12) → letter (11)"

    def test_a_command_taken_as_a_token_changes_nothing(self, parse):
        tex = parse("\\let\\a\\makeatletter\n\\x@c\n")
        assert (tex.catcode_changes, str(tex.container[-1])) == ([], "@c")


class TestFiles:
    def test_a_package_is_read_with_at_sign_as_a_letter(self, tmp_path):
        path = tmp_path / "macros.sty"
        path.write_text("\\newcommand\\am@x[1]{#1}\n", encoding="utf-8")
        tex = TexFile(path)
        tex.analyse()
        assert tex.signatures.command("am@x").spec == "+m"

    def test_a_document_is_read_with_an_ordinary_at_sign(self, tmp_path):
        path = tmp_path / "cours.tex"
        path.write_text("\\newcommand\\am@x[1]{#1}\n", encoding="utf-8")
        tex = TexFile(path)
        tex.analyse()
        assert tex.signatures.command("am@x") is None
