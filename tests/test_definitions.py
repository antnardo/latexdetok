"""Definitions learned as reading goes on, and `\\input` files followed."""

import pytest

from latexdetok import CatcodeTable, TexCommand, TexFile


def signature(tex, name):
    found = tex.signatures.command(name)
    return None if found is None else found.spec


def bound(tex, name):
    (command,) = [
        node
        for node in tex.iter()
        if isinstance(node, TexCommand) and node.base_name == name and node.arguments is not None
    ]
    return [None if node is None else str(node) for node in command.arguments]


class TestNewcommand:
    @pytest.mark.parametrize(
        ("definition", "spec"),
        [
            ("\\newcommand{\\x}{X}", ""),
            ("\\newcommand{\\x}[2]{#1#2}", "+m +m"),
            ("\\newcommand{\\x}[2][d]{#1#2}", "+o +m"),
            ("\\newcommand*\\x[1]{#1}", "m"),
            ("\\renewcommand{\\section}[1]{#1}", "+m"),
            ("\\DeclareRobustCommand{\\x}[3]{}", "+m +m +m"),
        ],
    )
    def test_the_signature_is_learned(self, parse, definition, spec):
        assert signature(parse(definition + "\n"), "x" if "\\x" in definition else "section") == spec

    def test_the_use_is_read_with_the_signature(self, parse):
        tex = parse("\\newcommand{\\fig}[2][0.5]{#1#2}\n\\fig[0.8]{a.png}\n")
        assert bound(tex, "fig") == ["[0.8]", "{a.png}"]

    def test_before_the_definition_the_command_is_unknown(self, parse):
        tex = parse("\\fig{a}\n\\newcommand{\\fig}[1]{#1}\n")
        first = next(node for node in tex.iter() if isinstance(node, TexCommand) and node.base_name == "fig")
        assert first.signature is None

    @pytest.mark.parametrize(
        "body",
        [
            "\\@ifstar{\\moveup\\@rmq}{\\@rmq}",
            "\\@ifnextchar[{\\rmq@opt}{\\rmq@noopt}",
            "\\@testopt\\rmq@opt{}",
        ],
    )
    def test_a_body_that_tests_what_follows_is_not_learned(self, parse, body):
        # Found in the corpus: \rmqphys reads “*” and one argument through \@rmqphys.
        tex = parse(f"\\makeatletter\n\\newcommand{{\\rmq}}{{{body}}}\n")
        assert signature(tex, "rmq") is None

    def test_a_body_that_tests_what_follows_with_an_argument_count(self, parse):
        tex = parse("\\makeatletter\n\\newcommand{\\rmq}[1]{\\@ifstar{#1}{#1}}\n")
        assert signature(tex, "rmq") == "+m"

    def test_providecommand_does_not_replace(self, parse):
        assert signature(parse("\\providecommand{\\section}{X}\n"), "section") == "s o m"

    @pytest.mark.parametrize("count", ["x", "12", "{2}"])
    def test_an_unreadable_argument_count_is_ignored(self, parse, count):
        assert signature(parse(f"\\newcommand{{\\x}}[{count}]{{}}\n"), "x") is None


class TestDelegation:
    """Level 1 of the expansion: the start of a body that tests what follows."""

    RMQ = "\\makeatletter\n\\newcommand{\\rmqphys}{\\@ifstar{\\moveup\\@rmqphys}{\\@rmqphys}}\n"

    def test_the_star_then_the_helper(self, parse):
        # Found in the corpus (34 macros \rmq…).
        tex = parse(self.RMQ + "\\newcommand{\\@rmqphys}[1]{#1}\n\\makeatother\n\\rmqphys*{A} \\rmqphys{B}\n")
        uses = [
            node
            for node in tex.iter()
            if isinstance(node, TexCommand) and node.base_name == "rmqphys" and node.arguments is not None
        ]
        assert (signature(tex, "rmqphys"), [[str(a) for a in node.arguments] for node in uses]) == (
            "s +m",
            [["{A}"], ["{B}"]],
        )

    def test_a_helper_defined_after_the_use_of_the_definition(self, parse):
        tex = parse(self.RMQ + "\\newcommand{\\@rmqphys}[2]{}\n")
        assert signature(tex, "rmqphys") == "s +m +m"

    def test_an_unknown_helper(self, parse):
        assert signature(parse(self.RMQ), "rmqphys") is None

    @pytest.mark.parametrize(
        ("test", "expected"),
        [
            ("\\@ifnextchar[{\\x@o}{\\x@n}", "o m"),
            ("\\kernel@ifnextchar[\\x@o\\x@n", "o m"),
            ("\\@ifnextchar({\\x@o}{\\x@n}", "d() m"),
        ],
    )
    def test_an_announced_optional_argument(self, parse, test, expected):
        tex = parse(f"\\makeatletter\n\\newcommand\\x{{{test}}}\n\\newcommand*\\x@n[1]{{}}\n")
        assert signature(tex, "x") == expected

    def test_the_helpers_star_is_no_longer_at_the_head(self, parse):
        tex = parse(
            "\\makeatletter\n\\newcommand\\x{\\@ifnextchar[{\\x@o}{\\x@n}}\n\\NewDocumentCommand\\x@n{s m}{}\n"
        )
        assert signature(tex, "x") == "o t* m"

    @pytest.mark.parametrize(
        "body",
        [
            "\\par\\@ifstar{\\a}{\\b}",
            "\\@ifstar{\\a}{\\moveup\\b}",
            "\\@ifnextchar<{\\a}{\\b}",
            "\\@ifstar{\\a}{\\b}\\relax",
        ],
    )
    def test_a_form_that_is_not_understood_is_not_learned(self, parse, body):
        tex = parse(f"\\makeatletter\n\\newcommand\\b[1]{{}}\n\\newcommand\\x{{{body}}}\n")
        assert signature(tex, "x") is None

    def test_a_loop_of_delegations(self, parse):
        tex = parse(
            "\\makeatletter\n\\newcommand\\a{\\@ifstar{\\b}{\\b}}\n\\newcommand\\b{\\@ifstar{\\a}{\\a}}\n"
        )
        assert (signature(tex, "a"), signature(tex, "b")) == (None, None)

    def test_a_redefinition_replaces_the_delegation(self, parse):
        tex = parse(self.RMQ + "\\newcommand{\\@rmqphys}[1]{}\n\\renewcommand{\\rmqphys}[2]{}\n")
        assert signature(tex, "rmqphys") == "+m +m"

    def test_let_copies_the_delegation(self, parse):
        tex = parse(self.RMQ + "\\let\\remarque\\rmqphys\n\\newcommand{\\@rmqphys}[1]{}\n")
        assert signature(tex, "remarque") == "s +m"


class TestDocumentCommand:
    def test_the_signature_as_it_stands(self, parse):
        tex = parse("\\NewDocumentCommand{\\pt}{s O{1} m}{}\n\\pt*{b}\n")
        assert (signature(tex, "pt"), bound(tex, "pt")) == ("s O{1} m", [None, "{b}"])

    def test_an_unreadable_signature_ignoree(self, parse):
        assert signature(parse("\\NewDocumentCommand{\\pt}{x}{}\n"), "pt") is None

    def test_an_environment(self, parse):
        tex = parse("\\NewDocumentEnvironment{boite}{o m}{}{}\n\\begin{boite}{T}x\\end{boite}\n")
        (boite,) = tex.get_envs("boite")
        assert [None if node is None else str(node) for node in boite.arguments] == [None, "{T}"]


class TestNewenvironment:
    def test_the_signature_and_the_use(self, parse):
        tex = parse("\\newenvironment{boite}[1]{a}{b}\n\\begin{boite}{Titre}x\\end{boite}\n")
        (boite,) = tex.get_envs("boite")
        assert (tex.signatures.environment("boite").spec, [str(node) for node in boite.arguments]) == (
            "+m",
            ["{Titre}"],
        )


class TestCopies:
    def test_let(self, parse):
        tex = parse("\\let\\titre=\\section\n\\titre{A}\n")
        assert (signature(tex, "titre"), bound(tex, "titre")) == ("s o m", [None, "{A}"])

    def test_let_towards_an_unknown_command_forgets(self, parse):
        assert signature(parse("\\let\\section\\foo\n"), "section") is None

    def test_newcommandcopy(self, parse):
        assert signature(parse("\\NewCommandCopy\\titre\\section\n"), "titre") == "s o m"


class TestDef:
    @pytest.mark.parametrize(
        ("definition", "spec"),
        [
            ("\\def\\x#1#2{#2#1}", "m m"),
            ("\\def\\x{X}", ""),
            ("\\def\\x #1{#1}", "m"),
            ("\\gdef\\x#1{#1}", "m"),
            ("\\edef\\x#1{#1}", "m"),
            ("\\long\\def\\x#1{#1}", "m"),
        ],
    )
    def test_the_signature_is_learned(self, parse, definition, spec):
        assert signature(parse(definition + "\n"), "x") == spec

    @pytest.mark.parametrize(
        "definition",
        ["\\def\\section#1.{}", "\\def\\section#1 {}", "\\def\\section[#1]{}", "\\def\\section#2#1{}"],
    )
    def test_parametres_delimites_font_oublier(self, parse, definition):
        assert signature(parse(definition + "\n"), "section") is None

    def test_the_use_is_read_with_the_signature(self, parse):
        assert bound(parse("\\def\\vect#1{\\overrightarrow{#1}}\n$\\vect u$\n"), "vect") == ["u"]

    def test_with_no_body_nothing_is_learned(self, parse):
        assert signature(parse("\\def\\section\n"), "section") == "s o m"


class TestBody:
    """What `expansion` will know how to expand: the body and its parameters."""

    def macro(self, tex, name="x"):
        found = tex.signatures.macro(name)
        return None if found is None else ([str(spec) for spec in found.parameters], found.body)

    @pytest.mark.parametrize(
        ("definition", "expected"),
        [
            ("\\newcommand{\\x}[2][d]{#1#2}", (["O{d}", "m"], "#1#2")),
            ("\\newcommand*\\x{X}", ([], "X")),
            ("\\newcommand\\x\\y", ([], "\\y")),
            ("\\def\\x#1{(#1)}", (["m"], "(#1)")),
            ("\\gdef\\x{X}", ([], "X")),
            ("\\NewDocumentCommand\\x{O{a} +m}{#1#2}", (["O{a}", "m"], "#1#2")),
            ("\\NewDocumentCommand\\x{o t+ m}{#1#2#3}", (["o", "t+", "m"], "#1#2#3")),
        ],
    )
    def test_the_body_is_kept(self, parse, definition, expected):
        assert self.macro(parse(definition + "\n")) == expected

    @pytest.mark.parametrize(
        "definition",
        [
            "\\edef\\x{X}",
            "\\xdef\\x{X}",
            "\\NewDocumentCommand\\x{e{^} m}{X}",
            "\\NewDocumentCommand\\x{v}{X}",
            "\\newcommand\\x[a]{X}",
            "\\providecommand\\section{X}",
        ],
    )
    def test_the_body_is_not_kept(self, parse, definition):
        assert self.macro(parse(definition + "\n"), "section" if "section" in definition else "x") is None

    def test_the_branches_of_a_body_that_tests_what_follows(self, parse):
        tex = parse("\\makeatletter\n\\newcommand{\\rmq}{\\@ifstar{\\moveup\\@rmq}{\\@rmq}}\n")
        macro = tex.signatures.macro("rmq")
        assert (macro.parameters, macro.body, macro.otherwise) == ((), "\\moveup\\@rmq", "\\@rmq")

    def test_a_leading_star(self, parse):
        macro = parse("\\NewDocumentCommand\\x{s m}{#1#2}\n").signatures.macro("x")
        assert (macro.starred, [str(spec) for spec in macro.parameters]) == (True, ["m"])

    def test_a_redefinition_with_no_body_forgets_the_old_one(self, parse):
        assert self.macro(parse("\\def\\x{X}\n\\NewDocumentCommand\\x{e{^}}{Y}\n")) is None

    def test_let_copies_the_body(self, parse):
        assert self.macro(parse("\\def\\y#1{Y#1}\n\\let\\x\\y\n")) == (["m"], "Y#1")

    def test_a_body_over_several_lines(self, parse):
        assert self.macro(parse("\\newcommand\\x{%\n  A\n  B}\n"))[1] == "%\nA\nB"

    def test_the_table_of_the_definition(self, parse):
        tex = parse("\\makeatletter\n\\def\\x{\\@y}\n\\makeatother\n")
        assert tex.signatures.macro("x").catcodes == CatcodeTable.package()

    def test_the_use_carries_the_body_in_force(self, parse):
        tex = parse("\\def\\x{A}\n\\x\n\\def\\x{B}\n\\x\n")
        uses = [node for node in tex.iter() if isinstance(node, TexCommand) and node.content == "x"]
        # The name of the second definition carries the body of that moment: the expansion does not expand that name.
        assert [None if node.macro is None else node.macro.body for node in uses] == [None, "A", "A", "B"]


class TestEnvironmentCode:
    def environment(self, tex, name="x"):
        found = tex.signatures.environment_macro(name)
        if found is None:
            return None
        return [str(spec) for spec in found.parameters], found.begin, found.end, found.end_arguments

    @pytest.mark.parametrize(
        ("definition", "expected"),
        [
            ("\\newenvironment{x}{A}{B}", ([], "A", "B", False)),
            ("\\newenvironment{x}[2][d]{A#1#2}{B}", (["O{d}", "m"], "A#1#2", "B", False)),
            ("\\renewenvironment*{x}[1]{A}%\n{B}", (["m"], "A", "B", False)),
            ("\\NewDocumentEnvironment{x}{O{d} m}{A}{B#1}", (["O{d}", "m"], "A", "B#1", True)),
        ],
    )
    def test_the_code_is_kept(self, parse, definition, expected):
        assert self.environment(parse(definition + "\n")) == expected

    @pytest.mark.parametrize(
        "definition",
        [
            "\\newenvironment{x}{A}",
            "\\NewDocumentEnvironment{x}{e{^}}{A}{B}",
            "\\NewDocumentEnvironment{x}{s}{A}{B}",
            "\\newenvironment{x}[a]{A}{B}",
        ],
    )
    def test_the_code_is_not_kept(self, parse, definition):
        assert self.environment(parse(definition + "\n")) is None

    def test_a_redefinition_with_no_code_forgets_the_old_one(self, parse):
        tex = parse("\\newenvironment{x}{A}{B}\n\\NewDocumentEnvironment{x}{e{^}}{A}{B}\n")
        assert self.environment(tex) is None

    def test_the_group_carries_the_code_in_force(self, parse):
        tex = parse("\\begin{x}\\end{x}\n\\newenvironment{x}{A}{B}\n\\begin{x}\\end{x}\n")
        first, second = tex.get_envs("x")
        assert (first.macro, second.macro.begin) == (None, "A")


class TestNewif:
    def test_a_false_boolean(self, parse):
        tex = parse("\\newif\\ifprof\n")
        assert (tex.signatures.is_boolean("prof"), tex.signatures.boolean("prof")) == (True, False)

    @pytest.mark.parametrize("definition", ["\\newif\\prof", "\\newif\\if"])
    def test_a_name_with_no_if_is_ignored(self, parse, definition):
        assert not parse(definition + "\n").signatures.is_boolean("prof")


class TestRegistryParFichier:
    def test_every_analysis_starts_from_the_kernel(self, parse):
        tex = parse("\\newcommand{\\x}[1]{}\n")
        tex.lines = ["a\n"]
        tex.analyse()
        assert signature(tex, "x") is None

    def test_the_definitions_of_one_file_do_not_reach_another(self, parse):
        parse("\\newcommand{\\x}[1]{}\n")
        assert signature(parse("a\n"), "x") is None


class TestInput:
    def test_the_definitions_are_read_in_the_included_file(self, tmp_path):
        (tmp_path / "LaTeX").mkdir()
        (tmp_path / "LaTeX" / "definitions.tex").write_text(
            "\\newcommand{\\vect}[1]{\\overrightarrow{#1}}\n", encoding="utf-8"
        )
        main = tmp_path / "cours.tex"
        main.write_text("\\input{LaTeX/definitions}\n$\\vect u$\n", encoding="utf-8")
        tex = TexFile(main)
        tex.analyse(follow_inputs=True)
        assert (signature(tex, "vect"), bound(tex, "vect")) == ("+m", ["u"])

    def test_without_the_option_inclusions_are_not_read(self, tmp_path):
        (tmp_path / "defs.tex").write_text("\\newcommand{\\vect}[1]{}\n", encoding="utf-8")
        main = tmp_path / "cours.tex"
        main.write_text("\\input{defs}\n", encoding="utf-8")
        tex = TexFile(main)
        tex.analyse()
        assert signature(tex, "vect") is None

    def test_the_included_content_stays_outside_the_tree(self, tmp_path):
        (tmp_path / "defs.tex").write_text("\\section{Ailleurs}\n", encoding="utf-8")
        main = tmp_path / "cours.tex"
        main.write_text("\\input{defs.tex}\n", encoding="utf-8")
        tex = TexFile(main)
        tex.analyse(follow_inputs=True)
        assert len(tex.get_sections()) == 0

    def test_circular_inclusions(self, tmp_path):
        (tmp_path / "a.tex").write_text("\\input{b}\n\\newcommand{\\x}[1]{}\n", encoding="utf-8")
        (tmp_path / "b.tex").write_text("\\input{a}\n\\newcommand{\\y}[2]{}\n", encoding="utf-8")
        tex = TexFile(tmp_path / "a.tex")
        tex.analyse(follow_inputs=True)
        assert (signature(tex, "x"), signature(tex, "y")) == ("+m", "+m +m")

    def test_a_missing_file_is_ignored(self, tmp_path):
        main = tmp_path / "cours.tex"
        main.write_text("\\input{absent}\ntexte\n", encoding="utf-8")
        tex = TexFile(main)
        tex.analyse(follow_inputs=True)
        assert tex.diagnostics == []

    def test_a_list_of_lines_is_relative_to_the_current_folder(self, tmp_path, monkeypatch):
        (tmp_path / "defs.tex").write_text("\\newcommand{\\vect}[1]{}\n", encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        tex = TexFile(["\\input{defs}\n"])
        tex.analyse(follow_inputs=True)
        assert signature(tex, "vect") == "+m"
