"""The expanded view: the user's macros replaced by their bodies, tied to the source."""

import pytest

from latexdetok import TexBranch, TexCommand, TexFile, TexGroup, expand
from latexdetok import expansion as expansion_module
from latexdetok.expansion import MAX_DEPTH

BEQ = "\\def\\beq{\\begin{equation}}\n\\def\\eeq{\\end{equation}}\n\\beq x=1 \\eeq\n"
METHODE = (
    "\\newcommand{\\methode}[2]{\\begin{methodeenv}\\gras{#1}\\medskip#2\\end{methodeenv}}\n"
    "\\methode{Titre}{Corps}\n"
)
VNABLA = "\\def\\vect#1{\\overrightarrow{#1}}\\def\\vnabla{\\vect{\\nabla}}\n$\\vnabla$\n"


@pytest.fixture
def view(parse):
    def develop(source):
        return expand(parse(source))

    return develop


def last_line(view):
    return view.content().splitlines()[-1]


def nodes(container):
    for node in container.content:
        yield node
        if isinstance(node, TexGroup):
            yield from nodes(node)


def branches(view):
    """The branches of the view, at any depth: (taken?, text)."""
    return [(node.taken, str(node)) for node in nodes(view.container) if isinstance(node, TexBranch)]


def in_document(tex, predicate):
    """The first element that fits inside the last element of the file, after the definitions."""
    return next(node for node in nodes(tex.container.content[-1]) if predicate(node))


class TestMilestoneCriteria:
    def test_beq_eeq_become_an_environment(self, parse, shape, problems):
        tex = parse(BEQ)
        developed = expand(tex)
        assert (len(tex.get_envs("equation")), shape(developed.container)[1][-1], problems(developed)) == (
            0,
            ("equation", ["x=1"]),
            [],
        )

    def test_methode_becomes_an_environment(self, view, shape):
        assert shape(view(METHODE).container)[1][-1] == (
            "methodeenv",
            ["\\gras", ("{", ["Titre"]), "\\medskip", "Corps"],
        )


class TestSubstitution:
    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            ("\\def\\x#1#2{#2#1}\n\\x ab\n", "ba"),
            ("\\newcommand\\f[2][d]{(#1,#2)}\n\\f{b}\n", "(d,b)"),
            ("\\newcommand\\f[2][d]{(#1,#2)}\n\\f[a]{b}\n", "(a,b)"),
            ("\\NewDocumentCommand\\g{O{d} m}{(#1,#2)}\n\\g{b}\n", "(d,b)"),
            ("\\def\\x#1{\\def\\y##1{#1##1}}\n\\x a\n", "\\def\\y#1{a#1}"),
            ("\\def\\x#1{\\#1}\n\\x a\n", "\\#1"),
            ("\\newcommand\\vect[1]{\\overrightarrow{#1}}\n$\\vect u$\n", "$\\overrightarrow{u}$"),
            ("\\newcommand\\x[1]{#1#1}\n\\x{\\vect}\n", "\\vect\\vect"),
        ],
    )
    def test_parameters_substituted(self, view, source, expected):
        assert last_line(view(source)) == expected

    def test_a_body_that_uses_another_macro(self, view):
        assert last_line(view(VNABLA)) == "$\\overrightarrow{\\nabla}$"

    def test_a_definition_produced_by_an_expansion(self, view):
        source = "\\newcommand\\declare[1]{\\newcommand#1{X}}\n\\declare\\y\n\\y\n"
        assert view(source).content().splitlines()[-2:] == ["\\newcommand\\y{X}", "X"]


class TestEnvironments:
    SOL = "\\newenvironment{sol}[1]{\\begin{proof}[#1]}{\\end{proof}}\n\\begin{sol}{Titre}\nx\n\\end{sol}\n"

    def test_begin_and_end_code_inside_the_group(self, view, shape):
        assert shape(view(self.SOL).container)[1][-1] == (
            "sol",
            [("{", ["Titre"]), ("proof", [("[", ["Titre"]), "x"])],
        )

    def test_the_arguments_of_the_end_code(self, view, shape):
        source = "\\NewDocumentEnvironment{cadre}{m}{\\begin{#1}}{\\end{#1}}\n\\begin{cadre}{center}x\\end{cadre}\n"
        assert shape(view(source).container)[1][-1] == ("cadre", [("{", ["center"]), ("center", ["x"])])

    def test_newenvironment_gives_no_arguments_to_the_end_code(self, view):
        developed = view("\\newenvironment{x}[1]{(#1)}{[#1]}\n\\begin{x}{a}\\end{x}\n")
        assert last_line(developed) == "\\begin{x}{a}(a)[#1]\\end{x}"

    def test_a_default_optional_argument(self, view):
        assert last_line(view("\\newenvironment{x}[2][d]{(#1,#2)}{}\n\\begin{x}{a}\\end{x}\n")) == (
            "\\begin{x}{a}(d,a)\\end{x}"
        )

    def test_a_macro_inside_an_argument_of_the_environment(self, parse):
        # In one single pass: the argument and the begin code that takes it up expand together.
        tex = parse("\\def\\y{Y}\n\\newenvironment{x}[1]{(#1)}{}\n\\begin{x}{\\y}z\\end{x}\n")
        assert last_line(expand(tex, max_depth=1)) == "\\begin{x}{Y}(Y)z\\end{x}"

    def test_expanded_only_once(self, view):
        developed = view("\\def\\y{Y}\n\\newenvironment{x}{\\y}{E}\n\\begin{x}\\end{x}\n")
        assert (last_line(developed), [item.name for item in developed.expansions]) == (
            "\\begin{x}YE\\end{x}",
            ["x", "x", "y"],
        )

    def test_an_environment_inside_the_code_of_another(self, view, shape):
        source = "\\newenvironment{a}{\\begin{b}}{\\end{b}}\\newenvironment{b}{B}{E}\n\\begin{a}x\\end{a}\n"
        assert shape(view(source).container)[1][-1] == ("a", [("b", ["BxE"])])

    @pytest.mark.parametrize(
        "source",
        [
            "\\newenvironment{x}{X}{Y}\n\\newcommand\\z{\\begin{x}\\end{x}}\n",
            "\\newenvironment{x}{X}{Y}\n\\begin{x}\n",
            "\\NewDocumentEnvironment{x}{m O{d}}{[#2]}{}\n\\begin{x}{a}b\\end{x}\n",
            "\\NewDocumentEnvironment{x}{e{^}}{X}{Y}\n\\begin{x}\\end{x}\n",
            "\\newenvironment*{x}[1]{X}{Y}\n\\begin{x}\n\n\\end{x}\n",
        ],
    )
    def test_not_expanded(self, parse, source):
        tex = parse(source)
        assert expand(tex).content() == tex.content()

    def test_the_map_of_the_begin_and_end_code(self, view):
        developed = view(self.SOL)
        (proof,) = developed.get_envs("proof")
        opening, closing = developed.origin(proof), developed.expansions[1]
        assert (
            opening.name,
            opening.environment,
            opening.start,
            opening.end,
            closing.start,
            closing.end,
        ) == (
            "sol",
            True,
            (2, 0),
            (2, 18),
            (4, 0),
            (4, 9),
        )


class TestBranches:
    """`\\@ifstar` et `\\@ifnextchar` : l'emploi dit quelle branche TeX prendrait."""

    RMQ = (
        "\\makeatletter\\newcommand{\\rmq}{\\@ifstar{\\moveup\\@rmq}{\\@rmq}}"
        "\\newcommand\\@rmq[1]{\\begin{boite}#1\\end{boite}}\\makeatother\n"
    )
    FIG = (
        "\\makeatletter\\newcommand{\\fig}{\\@ifnextchar[{\\fig@opt}{\\fig@def}}"
        "\\newcommand\\fig@opt[2][d]{O(#1,#2)}\\newcommand\\fig@def[1]{D(#1)}\\makeatother\n"
    )

    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            (
                RMQ + "\\rmq*{a}\n",
                [(True, "\\moveup"), (False, "\\@rmq"), (True, "\\begin{boite}a\\end{boite}")],
            ),
            (RMQ + "\\rmq{b}\n", [(False, "\\moveup\\@rmq"), (True, "\\begin{boite}b\\end{boite}")]),
            (FIG + "\\fig[x]{y}\n", [(False, "\\fig@def"), (True, " O(x,y)")]),
            (FIG + "\\fig{z}\n", [(False, "\\fig@opt"), (True, " D(z)")]),
        ],
    )
    def test_both_branches_are_kept(self, view, source, expected):
        developed = view(source)
        assert branches(developed) == expected

    def test_a_command_being_named_is_not_decided(self, view):
        developed = view(self.RMQ + "\\titleformat{\\rmq}\n")
        assert (last_line(developed), developed.conditions) == ("\\titleformat{\\rmq}", [])

    def test_a_branch_points_back_to_the_use(self, view):
        developed = view(self.RMQ + "\\rmq*{a}\n")
        moveup = next(
            node
            for node in nodes(developed.container)
            if node.is_command("moveup") and node.start_position[0] == 2
        )
        origin = developed.origin(moveup)
        assert (origin.name, origin.start, origin.end) == ("rmq", (2, 0), (2, 5))


class TestConditionals:
    """Both branches stay in the view; each one knows whether TeX takes it."""

    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            ("\\newif\\ifprof\\proftrue\n\\ifprof A\\else B\\fi\n", [(True, " A"), (False, " B")]),
            ("\\newif\\ifprof\n\\ifprof A\\else B\\fi\n", [(False, " A"), (True, " B")]),
            ("\\iffalse A\\fi\n", [(False, " A")]),
            ("\\iftrue A\\else B\\fi\n", [(True, " A"), (False, " B")]),
            ("\\ifnum 2>1 A\\else B\\fi\n", [(True, "A"), (False, " B")]),
            ("\\ifnum1=0 A\\fi\n", [(False, "A")]),
        ],
    )
    def test_a_primitive_conditional(self, view, source, expected):
        assert branches(view(source)) == expected

    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            ("\\ifmmode A\\else B\\fi\n", [(False, " A"), (True, " B")]),
            ("$\\ifmmode A\\else B\\fi$\n", [(True, " A"), (False, " B")]),
            ("\\begin{equation}\\ifmmode A\\fi\\end{equation}\n", [(True, " A")]),
            ("$\\mbox{\\ifmmode A\\fi}$\n", [(False, " A")]),
            ("\\ensuremath{\\ifmmode A\\fi}\n", [(True, " A")]),
        ],
    )
    def test_ifmmode(self, view, source, expected):
        assert branches(view(source)) == expected

    @pytest.mark.parametrize(
        "source",
        [
            "$\\foo{\\ifmmode A\\fi}$\n",
            "\\begin{monbloc}\\ifmmode A\\fi\\end{monbloc}\n",
            "\\ifx\\a\\undefined A\\else B\\fi\n",
            "\\ifnum\\value{page}>1 A\\fi\n",
            "\\ifcase 1 A\\or B\\fi\n",
            "\\newif\\ifprof\\proftrue\n\\ifprof\\ifpdf A\\fi\\fi\n",
            "\\newif\\ifprof\n\\textbf{\\proftrue}\\ifprof A\\fi\n",
            "\\newif\\ifprof\n\\ifx\\a\\undefined\\proftrue\\fi\\ifprof A\\fi\n",
        ],
    )
    def test_not_decided(self, parse, source):
        tex = parse(source)
        developed = expand(tex)
        assert (developed.conditions, developed.content()) == ([], tex.content())

    def test_the_value_is_followed_through_the_text(self, view):
        source = "\\newif\\ifprof\n\\ifprof A\\fi\\proftrue\\ifprof B\\fi{\\proffalse\\ifprof C\\fi}\\ifprof D\\fi\n"
        assert [condition.value for condition in view(source).conditions] == [False, True, False, True]

    def test_a_global_assignment(self, view):
        source = "\\newif\\ifprof\n\\begin{center}\\global\\proftrue\\end{center}\\ifprof A\\fi\n"
        assert [condition.value for condition in view(source).conditions] == [True]

    def test_an_assignment_in_a_discarded_branch_is_ignored(self, view):
        source = "\\newif\\ifprof\n\\iffalse\\proftrue\\fi\\ifprof A\\fi\n"
        assert [condition.value for condition in view(source).conditions] == [False, False]

    def test_a_boolean_set_by_a_macro(self, view):
        source = (
            "\\newif\\ifprof\n\\def\\paramprof#1{\\ifnum#1=0 \\proffalse\\else\\proftrue\\fi}\n"
            "\\paramprof{1}\n\\ifprof P\\else E\\fi\n"
        )
        developed = view(source)
        assert [(item.test, item.value) for item in developed.conditions] == [
            ("ifnum", False),
            ("ifprof", True),
        ]

    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            (
                "\\NewDocumentCommand\\x{s m}{\\IfBooleanTF{#1}{[#2]}{(#2)}}\n\\x*{a}\n",
                [(True, "{[a]}"), (False, "{(a)}")],
            ),
            (
                "\\NewDocumentCommand\\x{s m}{\\IfBooleanTF{#1}{[#2]}{(#2)}}\n\\x{a}\n",
                [(False, "{[a]}"), (True, "{(a)}")],
            ),
            ("\\NewDocumentCommand\\x{o m}{\\IfValueT{#1}{[#1]}#2}\n\\x{a}\n", [(False, "{[-NoValue-]}")]),
            ("\\NewDocumentCommand\\x{o m}{\\IfNoValueF{#1}{[#1]}#2}\n\\x[w]{a}\n", [(True, "{[w]}")]),
            ("\\NewDocumentCommand\\x{t+ m}{\\IfBooleanF{#1}{-}#2}\n\\x+{a}\n", [(False, "{-}")]),
            ("\\ifstrempty{}{A}{B}\n", [(True, "{A}"), (False, "{B}")]),
            ("\\ifstrempty{ }{A}{B}\n", [(False, "{A}"), (True, "{B}")]),
            ("\\ifblank{ }{A}{B}\n", [(True, "{A}"), (False, "{B}")]),
        ],
    )
    def test_conditional_with_arguments(self, view, source, expected):
        assert branches(view(source)) == expected

    def test_a_discarded_branch_is_read_apart(self, view, shape):
        developed = view("\\iffalse\\begin{center}}\\def\\y{Y}\\makeatletter\\fi\\y\\a@b\n")
        root = shape(developed.container)[1]
        assert (root[-3:], developed.diagnostics) == (["\\y", "\\a", "@b"], [])

    def test_nothing_is_expanded_in_a_discarded_branch(self, view):
        developed = view("\\def\\y{Y}\n\\iffalse\\y\\else\\y\\fi\n")
        assert branches(developed) == [(False, "\\y"), (True, " Y")]

    def test_a_taken_branch_that_spills_out_melts_into_the_stream(self, view, shape):
        developed = view("\\newif\\ifsol\\soltrue\n\\ifsol\\begin{center}\\fi x \\ifsol\\end{center}\\fi\n")
        (center,) = developed.get_envs("center")
        assert (
            shape(center)[1],
            [(condition.test, taken) for condition, taken in developed.branches(center)],
        ) == (["\\fi", "x", "\\ifsol"], [("ifsol", True)])

    def test_a_taken_branch_that_fits_stays_a_node(self, view):
        developed = view("\\newif\\ifprof\\proftrue\n\\ifprof\\begin{center}A\\end{center}\\fi\n")
        (branch,) = [node for node in developed.container.content if isinstance(node, TexBranch)]
        assert [group.name for group in branch.content] == ["center"]

    def test_a_conditional_cut_by_a_use_is_decided_after_it(self, view):
        developed = view("\\newif\\ifa\\atrue\\def\\x#1{#1}\n\\ifa\\x{A\\else}B\\fi\n")
        assert (last_line(developed), branches(developed)) == (
            "\\ifa A\\else B\\fi",
            [(True, " A"), (False, " B")],
        )

    def test_nested_branches(self, view):
        developed = view("\\iftrue\\ifnum1=1 A\\fi\\else B\\fi\n")
        a = next(node for node in nodes(developed.container) if node.is_pure_text() and node.content == "A")
        sides = [(condition.test, taken) for condition, taken in developed.branches(a)]
        assert sides == [("iftrue", True), ("ifnum", True)]

    def test_the_map_of_a_conditional(self, view):
        developed = view("\\newif\\ifprof\n\\def\\x{\\ifprof A\\fi}\n\\x\n")
        (condition,) = developed.conditions
        assert (condition.start, condition.end, condition.parent.name) == ((3, 0), (3, 2), "x")


class TestTextProduced:
    def test_a_control_word_is_parted_from_the_letter_after_it(self, view):
        assert last_line(view("\\newcommand\\x[1]{\\medskip#1}\n\\x{Corps}\n")) == "\\medskip Corps"

    def test_blanks_after_a_use_with_no_argument_are_eaten(self, view):
        assert last_line(view("\\def\\x{X}\n\\x bar\n")) == "Xbar"

    def test_a_use_taken_as_an_argument_gets_braces(self, view):
        assert last_line(view("\\def\\demi{1/2}\n$\\frac\\demi x$\n")) == "$\\frac{1/2}x$"

    @pytest.mark.parametrize(
        "source",
        [
            "\\newcommand\\x[1]{#1%\n}\n\\[\n\\x{a}\n\\]\n",
            "\\newcommand\\x[1]{#1}\n\\[\n\\x{a\n}\n\\]\n",
            "\\def\\x{}\n\\[\na\n\\x\nb\n\\]\n",
        ],
    )
    def test_no_blank_line_at_the_junction(self, view, source):
        developed = view(source)
        assert (developed.diagnostics, [node.is_par() for node in developed.container.content]) == (
            [],
            [False] * len(developed.container.content),
        )

    def test_a_blank_line_of_the_source_is_kept(self, view, shape):
        assert shape(view("\\def\\x{}\na\n\\x\n\nb\n").container)[1][-3:] == ["a", "PAR", "b"]

    def test_a_long_argument_that_is_a_blank_line(self, view, shape):
        assert "PAR" in shape(view("\\newcommand\\x[1]{a#1b}\n\\x\n\n").container)[1]

    @pytest.mark.parametrize("ending", ["\r\n", "\r"])
    @pytest.mark.parametrize(
        "source",
        [
            BEQ,
            METHODE,
            VNABLA,
            "\\newcommand\\x[1]{#1%\n}\n\\[\n\\x{a}\n\\]\n",
            "\\newif\\ifprof\n\\proftrue\n\\ifnum1<2 \\ifprof A\\else B\\fi\\fi\n",
        ],
        ids=["beq", "methode", "vnabla", "junction", "conditionals"],
    )
    def test_the_line_endings_of_the_source_do_not_change_the_view(self, parse, shape, source, ending):
        # The view is a text the package writes, like `str()`: in `\n`, whatever the file.
        def seen(tex):
            developed = expand(tex)
            return (
                developed.source_map.target.text,
                shape(developed.container),
                [(item.name, item.start, item.end) for item in developed.expansions],
                [str(diagnostic) for diagnostic in developed.diagnostics],
            )

        tex = TexFile(source.replace("\n", ending).splitlines(keepends=True))
        tex.analyse()
        assert seen(tex) == seen(parse(source))


class TestWhatDoesNotExpand:
    @pytest.mark.parametrize(
        "source",
        [
            "\\section{Titre}\n",
            "\\newcommand*\\x[1]{X#1}\n\\x\n\nsuite\n",
            "\\newcommand\\x{X}\n\\newcommand\\y{\\x}\n",
            "\\def\\x{X}\n\\noexpand\\x\n",
            "\\def\\x{X}\n\\let\\y\\x\n",
            "\\def\\x{X}\\def\\y{Y}\n\\ifx\\x\\y\\fi\n",
            "\\def\\x{X}\n\\def\\y#1\\x{}\n",
            "\\edef\\x{X}\n\\x\n",
            "\\def\\x#1.{X}\n\\x a.\n",
            "\\NewDocumentCommand\\x{e{^} m}{X}\n\\x{a}\n",
            "\\makeatletter\n\\newcommand\\x{\\@ifstar{A}{\\B}}\n\\x*\n",
            "\\x\n\\def\\x{X}\n",
            "\\def\\x{X}\n\\renewcommand{\\x}{Y}\n",
            "\\def\\x{X}\n% \\x\n\\verb|\\x|\n",
        ],
    )
    def test_the_text_is_unchanged(self, parse, source):
        tex = parse(source)
        developed = expand(tex)
        assert (developed.content(), developed.expansions) == (tex.content(), [])


class TestMapping:
    def test_a_copied_element_keeps_its_position(self, view):
        developed = view(METHODE)
        title = in_document(developed, lambda node: node.is_pure_text() and node.content == "Titre")
        assert (developed.source_span(title), developed.origin(title)) == (((2, 9), (2, 14)), None)

    def test_an_element_written_by_a_body_points_back_to_the_use(self, view):
        developed = view(METHODE)
        gras = in_document(developed, lambda node: node.is_command("gras"))
        assert (developed.origin(gras).name, developed.source_span(gras)) == ("methode", ((2, 0), (2, 22)))

    def test_an_environment_between_two_uses(self, view):
        developed = view(BEQ)
        (equation,) = developed.get_envs("equation")
        assert developed.source_span(equation) == ((3, 0), (3, 13))

    def test_a_nested_expansion(self, view):
        developed = view(VNABLA)
        arrow = in_document(developed, lambda node: node.is_command("overrightarrow"))
        origin = developed.origin(arrow)
        assert (origin.name, origin.depth, origin.parent.name, origin.start, origin.end) == (
            "vect",
            2,
            "vnabla",
            (2, 1),
            (2, 8),
        )

    def test_text_carried_by_an_argument_keeps_its_author(self, view):
        developed = view(VNABLA)
        nabla = in_document(developed, lambda node: node.is_command("nabla"))
        assert developed.origin(nabla).name == "vnabla"

    def test_a_diagnostic_brought_back_to_the_source(self, view, problems):
        developed = view("\\def\\eeq{\\end{equation}}\ntexte\n\\eeq\n")
        (diagnostic,) = problems(developed)
        assert developed.source_position(diagnostic.start) == (3, 0)

    def test_the_list_of_expansions(self, view):
        developed = view(BEQ)
        assert [(item.name, item.start, item.end) for item in developed.expansions] == [
            ("beq", (3, 0), (3, 4)),
            ("eeq", (3, 9), (3, 13)),
        ]

    @pytest.mark.parametrize("separator", ["\x0c", "\x85"], ids=["form-feed", "next-line"])
    def test_a_form_feed_or_next_line_does_not_cut_the_view(self, separator):
        # `str.splitlines` cuts at both, TeX at neither: `\x85` is a cp1252 `…` read as Latin-1.
        tex = TexFile(["\\def\\x{X}\n", f"a{separator}b \\x\n", "\\item c\n"])
        tex.analyse()
        developed = expand(tex)
        item = next(node for node in nodes(developed.container) if node.is_command("item"))
        assert (
            developed.lines,
            developed.source_map.target.text,
            developed.source_position(item.start_position),
        ) == (
            ["\\def\\x{X}\n", f"a{separator}b X\n", "\\item c\n"],
            "".join(developed.lines),
            (3, 0),
        )


class TestCatcodes:
    def test_a_body_is_read_under_the_table_of_its_definition(self, view):
        source = (
            "\\makeatletter\\newcommand\\@dd{\\mathrm{d}}\\newcommand\\dd{\\@dd}\\makeatother\n$\\dd x$\n"
        )
        assert last_line(view(source)) == "$\\mathrm{d}x$"

    def test_an_argument_is_read_under_the_table_of_the_use(self, view):
        developed = view("\\makeatletter\\newcommand\\vect[1]{\\@vect{#1}}\\makeatother\n$\\vect{\\a@b}$\n")
        math = developed.container.content[-1]
        commands = [node.content for node in nodes(math) if isinstance(node, TexCommand)]
        assert commands == ["@vect", "a"]


class TestLimits:
    def test_a_recursion_is_stopped(self, view):
        developed = view("\\def\\a{\\a}\n\\a\n")
        assert [item.depth for item in developed.expansions] == list(range(1, MAX_DEPTH + 1))

    def test_growth_is_stopped(self, view, monkeypatch):
        monkeypatch.setattr(expansion_module, "MAX_GROWTH", 1)
        monkeypatch.setattr(expansion_module, "GROWTH_MARGIN", 40)
        developed = view("\\def\\a{\\a\\a\\a\\a}\n\\a\n")
        assert len(developed.content()) < 80

    def test_with_no_macro_the_view_has_the_text_of_the_source(self, parse):
        tex = parse("\\section{A}\ntexte\n")
        developed = expand(tex)
        assert (developed.content(), developed.expansions, developed.source) == (tex.content(), [], tex)


def test_the_view_is_analysed_with_the_inclusions(tmp_path):
    (tmp_path / "macros.sty").write_text(
        "\\newcommand\\beq{\\begin{equation}}\n\\newcommand\\eeq{\\end{equation}}\n", encoding="utf-8"
    )
    main = tmp_path / "cours.tex"
    main.write_text("\\usepackage{macros}\n\\beq x \\eeq\n", encoding="utf-8")
    tex = TexFile(main)
    tex.analyse(follow_inputs=True)
    developed = expand(tex)
    assert (len(developed.get_envs("equation")), developed.follow_inputs, developed.src_file) == (
        1,
        True,
        main,
    )


def test_a_final_line_ending_turned_into_a_space(parse):
    # `%⏎` at the end of a body, then the last line ending of the file, turned into a space: the next
    # pass (which expands `\\y`) reads again a line ending the map must cover.
    developed = expand(parse("\\newcommand\\y{Y}\\newcommand\\x[1]{#1%\n}\n\\x{\\y}\n"))
    assert developed.lines[-2:] == ["Y%\n", " \n"]


def test_a_parting_space_in_the_substitution_context(parse):
    # The space that parts `\\medskip` from the argument is written by `\\x`, inside the body of `\\y`.
    view = expand(parse("\\newcommand\\x[1]{\\medskip#1}\\newcommand\\y[1]{(#1)}\n\\y{\\x{Corps}}\n"))
    offset = view.source_map.target.text.index("\\medskip Corps") + len("\\medskip")
    piece = view.source_map.piece(offset)
    assert (piece.expansion.name, piece.within.name) == ("x", "y")


def test_a_use_in_a_substituted_argument_has_the_substituter_as_parent(parse):
    # `\\gras` alone in its braces is not called; substituted by `\\wrap`, it reads `{b}` on the next pass.
    view = expand(parse("\\newcommand\\gras[1]{\\textbf{#1}}\\newcommand\\wrap[1]{#1{b}}\n\\wrap{\\gras}\n"))
    (gras,) = [item for item in view.expansions if item.name == "gras"]
    assert (last_line(view), gras.parent.name, gras.start, gras.end) == (
        "\\textbf{b}",
        "wrap",
        (2, 6),
        (2, 12),
    )
