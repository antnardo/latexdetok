"""Signatures in the ltcmd format, kernel tables and registry."""

import pytest

from latexdetok.catcodes import CatcodeTable
from latexdetok.signatures import (
    DATA,
    TABLES,
    ArgumentSpec,
    Boolean,
    CommandSignature,
    EnvironmentMacro,
    EnvironmentSignature,
    Macro,
    Mode,
    SignatureRegistry,
    parse_spec,
)


class TestParseSpec:
    @pytest.mark.parametrize(
        ("spec", "starred", "kinds"),
        [
            ("", False, ""),
            ("m", False, "m"),
            ("s o m", True, "om"),
            ("som", True, "om"),
            ("s m O{0} o +m", True, "mOom"),
            ("m t= m", False, "mtm"),
            ("r() d()", False, "rd"),
            ("o v", False, "ov"),
            ("e{^_} u{\\stop} l b g G{x}", False, "eulbgG"),
        ],
    )
    def test_types(self, spec, starred, kinds):
        is_starred, arguments = parse_spec(spec)
        assert (is_starred, "".join(argument.kind for argument in arguments)) == (starred, kinds)

    def test_a_default_value_with_nested_braces(self):
        (_, (argument,)) = parse_spec("O{\\arabic{x}}")
        assert argument.default == "\\arabic{x}"

    def test_spaces_between_a_type_and_its_parameter(self):
        # Found in latex.ltx: `\DeclareKeys { O { \@currname } +m }`.
        (_, (optional, body)) = parse_spec("O { \\@currname } +m")
        assert (optional.default, body.long) == (" \\@currname ", True)

    def test_delimiters(self):
        (_, (argument,)) = parse_spec("r()")
        assert (argument.opener, argument.closer, argument.mandatory) == ("(", ")", True)

    def test_a_star_after_an_argument_is_a_token(self):
        (starred, (_, star)) = parse_spec("m s")
        assert (starred, star.kind, star.delimiters) == (False, "t", "*")

    def test_prefixes_with_no_effect(self):
        (_, (argument,)) = parse_spec("!>{\\SplitList{,}}+m")
        assert (argument.kind, argument.long) == ("m", True)

    @pytest.mark.parametrize("spec", ["x", "O", "O{", "r(", "+"])
    def test_an_unreadable_signature(self, spec):
        with pytest.raises(ValueError, match="signature"):
            parse_spec(spec)

    @pytest.mark.parametrize("spec", ["s o m", "m O{0} o +m", "t= m", "r() d()", "e{^_}"])
    def test_rewriting(self, spec):
        assert CommandSignature.from_spec("x", spec).spec == spec


class TestArgumentSpec:
    @pytest.mark.parametrize(
        ("kind", "mandatory", "bound"),
        [("m", True, True), ("o", False, True), ("v", True, True), ("e", False, False), ("u", False, False)],
    )
    def test_nature(self, kind, mandatory, bound):
        argument = ArgumentSpec(kind)
        assert (argument.mandatory, argument.bound) == (mandatory, bound)


class TestRegistry:
    def test_a_loaded_table(self):
        registry = SignatureRegistry()
        registry.load(
            ["# commentaire", "", "\\section text s o m", "{tabular} text o m", "{verbatim} text verbatim"]
        )
        section, tabular, verbatim = (
            registry.command("section"),
            registry.environment("tabular"),
            registry.environment("verbatim"),
        )
        assert (section.spec, section.mode, tabular.spec, verbatim.verbatim) == (
            "s o m",
            Mode.TEXT,
            "o m",
            True,
        )

    def test_an_empty_signature(self):
        registry = SignatureRegistry()
        registry.load(["\\alpha math -"])
        assert registry.command("alpha").arguments == ()

    @pytest.mark.parametrize(
        ("line", "message"),
        [
            ("\\section", "expected"),
            ("\\section texte m", "texte"),
            ("section text m", "name"),
            ("\\section text O", "brace"),
        ],
    )
    def test_the_offending_line_is_reported(self, line, message):
        with pytest.raises(ValueError, match=f"table>:1: .*{message}"):
            SignatureRegistry().load([line])

    def test_the_last_table_wins(self):
        registry = SignatureRegistry()
        registry.load(["\\sqrt any -", "\\sqrt math o m"])
        assert registry.command("sqrt").spec == "o m"

    def test_an_independent_copy(self):
        kernel = SignatureRegistry.kernel()
        kernel.define(CommandSignature.from_spec("maMacro", "m"))
        assert SignatureRegistry.kernel().command("maMacro") is None

    def test_copying_a_command(self):
        registry = SignatureRegistry.kernel()
        registry.copy_command("titre", "section")
        assert registry.command("titre").spec == "s o m"

    def test_copying_an_unknown_command_forgets_the_target(self):
        registry = SignatureRegistry.kernel()
        registry.copy_command("section", "inconnue")
        assert registry.command("section") is None


class TestDelegation:
    def test_the_journal_replays_the_delegation(self):
        from latexdetok.signatures import Delegation

        source = SignatureRegistry.kernel()
        with source.record() as journal:
            source.define(Delegation("rmq", "section", starred=True))
        target = SignatureRegistry.kernel()
        target.replay(journal)
        assert target.command("rmq").spec == "s t* o m"

    def test_forgetting(self):
        from latexdetok.signatures import Delegation

        registry = SignatureRegistry.kernel()
        registry.define(Delegation("rmq", "section"))
        registry.forget_command("rmq")
        assert registry.command("rmq") is None


class TestMacro:
    BODY = Macro("vect", (ArgumentSpec("m"),), "\\overrightarrow{#1}", CatcodeTable.latex())

    def registry(self):
        registry = SignatureRegistry.kernel()
        registry.define(CommandSignature.from_spec("vect", "m"))
        registry.define(self.BODY)
        return registry

    def test_a_new_signature_forgets_the_body(self):
        registry = self.registry()
        registry.define(CommandSignature.from_spec("vect", "m m"))
        assert registry.macro("vect") is None

    def test_a_delegation_forgets_the_body(self):
        from latexdetok.signatures import Delegation

        registry = self.registry()
        registry.define(Delegation("vect", "section"))
        assert registry.macro("vect") is None

    def test_forgetting(self):
        registry = self.registry()
        registry.forget_command("vect")
        assert registry.macro("vect") is None

    def test_copying_a_command(self):
        registry = self.registry()
        registry.copy_command("v", "vect")
        assert (registry.macro("v").name, registry.macro("v").body) == ("v", "\\overrightarrow{#1}")

    def test_copying_with_no_body_forgets_the_targets_one(self):
        registry = self.registry()
        registry.copy_command("vect", "section")
        assert registry.macro("vect") is None

    def test_copying_the_registry(self):
        assert self.registry().copy().macro("vect") == self.BODY

    def test_the_journal_replays_the_body(self):
        source = SignatureRegistry.kernel()
        with source.record() as journal:
            source.define(CommandSignature.from_spec("vect", "m"))
            source.define(self.BODY)
        target = SignatureRegistry.kernel()
        target.replay(journal)
        assert target.macro("vect") == self.BODY


class TestEnvironmentMacro:
    CODE = EnvironmentMacro("sol", (), "\\begin{proof}", "\\end{proof}", CatcodeTable.latex())

    def test_a_new_signature_forgets_the_code(self):
        registry = SignatureRegistry.kernel()
        registry.define(EnvironmentSignature.from_spec("sol", ""))
        registry.define(self.CODE)
        registry.define(EnvironmentSignature.from_spec("sol", "m"))
        assert registry.environment_macro("sol") is None

    def test_copying_the_registry(self):
        registry = SignatureRegistry.kernel()
        registry.define(self.CODE)
        assert registry.copy().environment_macro("sol") == self.CODE

    def test_the_journal_replays_the_code(self):
        source = SignatureRegistry.kernel()
        with source.record() as journal:
            source.define(self.CODE)
        target = SignatureRegistry.kernel()
        target.replay(journal)
        assert target.environment_macro("sol") == self.CODE


class TestBoolean:
    def test_the_value_and_the_journal(self):
        source = SignatureRegistry.kernel()
        with source.record() as journal:
            source.define(Boolean("prof", False))
            source.define(Boolean("prof", True))
        target = SignatureRegistry.kernel()
        target.replay(journal)
        assert (target.is_boolean("prof"), target.boolean("prof"), target.booleans()) == (
            True,
            True,
            frozenset({("prof", True)}),
        )

    def test_an_unknown_value(self):
        registry = SignatureRegistry.kernel()
        registry.define(Boolean("prof", None))
        assert (registry.is_boolean("prof"), registry.boolean("prof"), registry.boolean("autre")) == (
            True,
            None,
            None,
        )

    def test_copying_the_registry(self):
        registry = SignatureRegistry.kernel()
        registry.define(Boolean("prof", True))
        copy = registry.copy()
        registry.define(Boolean("prof", False))
        assert copy.boolean("prof") is True


class TestKernelTables:
    @pytest.mark.parametrize("table", TABLES)
    def test_every_table_loads(self, table):
        registry = SignatureRegistry()
        registry.load((DATA / table).read_text(encoding="utf-8").splitlines(), origin=table)
        assert len(registry) > 10

    @pytest.mark.parametrize("table", ["kernel.txt", "verbatim.txt"])
    def test_no_duplicate_in_the_hand_written_tables(self, table):
        names = [
            line.split()[0]
            for line in (DATA / table).read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")
        ]
        assert sorted({name for name in names if names.count(name) > 1}) == []

    @pytest.mark.parametrize(
        ("name", "spec", "mode"),
        [
            ("section", "s o m", Mode.TEXT),
            ("\\", "s o", Mode.ANY),
            ("item", "o", Mode.TEXT),
            ("newcommand", "s m O{0} o +m", Mode.ANY),
            ("frac", "m m", Mode.MATH),
            ("sqrt", "o m", Mode.MATH),
            ("verb", "s v", Mode.ANY),
            ("alpha", "", Mode.MATH),
            ("hat", "m", Mode.MATH),
            ("'", "m", Mode.TEXT),
            ("url", "v", Mode.ANY),
        ],
    )
    def test_reference_signatures(self, name, spec, mode):
        signature = SignatureRegistry.kernel().command(name)
        assert (signature.spec, signature.mode) == (spec, mode)

    @pytest.mark.parametrize(
        ("name", "spec"),
        [
            ("tabular", "o m"),
            ("minipage", "o o o m"),
            ("figure", "o"),
            ("verbatim", "verbatim"),
            ("minted", "verbatim"),
        ],
    )
    def test_reference_environments(self, name, spec):
        assert SignatureRegistry.kernel().environment(name).spec == spec

    @pytest.mark.parametrize(
        ("name", "spec", "mode"),
        [("includegraphics", "s o o m", "any"), ("dfrac", "m m", "math"), ("SI", "o m m", "any")],
    )
    def test_packages_declared_by_hand(self, name, spec, mode):
        # `data/packages.txt`: what packages declare through code, written by hand.
        signature = SignatureRegistry.kernel().command(name)
        assert (signature.spec, str(signature.mode)) == (spec, mode)

    def test_packages_outside_the_table_are_unknown(self):
        # The rest keeps the heuristics: a wrong signature would move arguments around.
        registry = SignatureRegistry.kernel()
        assert [registry.command(name) for name in ("draw", "pgfplotstabletypeset", "mintinline")] == [
            None
        ] * 3

    def test_an_environment_with_a_verbatim_signature(self):
        assert EnvironmentSignature.from_spec("x", "verbatim").verbatim is True
