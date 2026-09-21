"""Definitions read in the source, learned as reading goes on.

LaTeX demands that a macro be defined before it is used: one pass is enough. As
soon as the arguments of a defining command are bound, the signature it declares
enters the registry, and the uses that follow are read with it.

What is understood:

- `\\newcommand{\\x}[2][d]{…}` and its relatives: `+o +m`, or `+m +m` with no
  default (long arguments, except with the star);
- `\\newenvironment{x}[1]{…}{…}` and its relatives;
- `\\NewDocumentCommand{\\x}{s o m}{…}` and its relatives: the signature as it
  stands;
- `\\def\\x#1#2{…}` and `\\gdef`, `\\edef`, `\\xdef`: `m m`, if the parameters are
  not delimited; `\\def\\x#1.{…}` makes `\\x` forgotten;
- `\\let\\x\\y`, `\\NewCommandCopy\\x\\y`: `\\x` takes the signature of `\\y`;
- `\\newif\\ifprof`: the boolean `prof`, false (its value is then followed by the
  tokeniser).

Along with the signature, the body is kept (`Macro`, `EnvironmentMacro` for the
begin and end code of an environment) when `expansion` will know how to expand
it: parameters `m`, `o`, `O{…}`, `t` and a leading star, which ltcmd's
conditionals (`\\IfBooleanTF`, `\\IfValueTF`) can then decide. Not the body of an
`\\edef` or an `\\xdef`, expanded at definition time in a state the document may
have changed since.

`learn` also returns the files a command makes TeX read — `\\input`,
`\\include`, `\\usepackage`, `\\RequirePackage`, `\\documentclass` — with the
extension TeX would give them; finding and reading them is `resolution`'s job.

Macros are not expanded, but the beginning of a body is read (level 1 of the
expansion, see the roadmap). A definition without `[n]` whose body starts by
testing what follows reads its arguments through another macro:
`\\newcommand{\\rmqphys}{\\@ifstar{\\moveup\\@rmqphys}{\\@rmqphys}}` delegates to
`\\@rmqphys` after the star, `\\@ifnextchar[{A}{B}` to `B` after an optional `[`.
A test anywhere but at the head, or a branch that is not a single command: the
definition is not learned, rather than learned wrong. Both branches are kept:
the use, which read the star or the bracket, says which one expands.
"""

import re
from collections.abc import Sequence
from dataclasses import replace

from latexdetok.catcodes import CatcodeTable
from latexdetok.classes import TexCommand, TexContainer, TexGroup
from latexdetok.signatures import (
    ArgumentSpec,
    Boolean,
    CommandSignature,
    Delegation,
    EnvironmentMacro,
    EnvironmentSignature,
    Macro,
    SignatureRegistry,
    parse_spec,
)

__all__ = ["DEFINING_COMMANDS", "INCLUDING_COMMANDS", "Inclusion", "learn"]

NEWCOMMAND = frozenset({"newcommand", "renewcommand", "providecommand", "DeclareRobustCommand"})
NEWENVIRONMENT = frozenset({"newenvironment", "renewenvironment"})
DOCUMENT_COMMAND = frozenset(
    {
        "NewDocumentCommand",
        "RenewDocumentCommand",
        "ProvideDocumentCommand",
        "DeclareDocumentCommand",
        "NewExpandableDocumentCommand",
    }
)
DOCUMENT_ENVIRONMENT = frozenset(
    {
        "NewDocumentEnvironment",
        "RenewDocumentEnvironment",
        "ProvideDocumentEnvironment",
        "DeclareDocumentEnvironment",
    }
)
COMMAND_COPY = frozenset({"NewCommandCopy", "RenewCommandCopy", "DeclareCommandCopy"})
DEF = frozenset({"def", "gdef", "edef", "xdef"})
# Body expanded at definition time: keeping it as written would expand it too late.
EXPANDED_DEF = frozenset({"edef", "xdef"})
PROVIDE = frozenset({"providecommand", "ProvideDocumentCommand", "ProvideDocumentEnvironment"})
# Arguments that are names or bodies: nothing in them runs where it is written.
DEFINING_COMMANDS = (
    NEWCOMMAND
    | NEWENVIRONMENT
    | DOCUMENT_COMMAND
    | DOCUMENT_ENVIRONMENT
    | COMMAND_COPY
    | DEF
    | frozenset({"let", "futurelet"})
)
# `#1#2…` in order: undelimited parameters.
UNDELIMITED_PARAMETERS = re.compile(r"(?:#[1-9])+")
# Types a substitution renders unconditionally: `o` and `s` need `\\IfValueTF`, `\\IfBooleanTF`.
EXPANDABLE_KINDS = frozenset("mOot")
# Command → (extension, index of the argument that names the file).
# `\\@ifnextchar<c>`: the optional argument the character announces.
OPTIONAL_BY_CHARACTER = {"[": ArgumentSpec("o"), "(": ArgumentSpec("d", delimiters="()")}

# Tests of what follows: the macro that uses them does not state its own arguments.
LOOKAHEAD = re.compile(r"\\(?:@ifnextchar|@ifstar|kernel@ifnextchar|@testopt|@dblarg|peek_)")

INCLUDING_COMMANDS = {
    "input": ("tex", 0),
    "include": ("tex", 0),
    "InputIfFileExists": ("tex", 0),
    "usepackage": ("sty", 1),
    "RequirePackage": ("sty", 1),
    "documentclass": ("cls", 1),
    "LoadClass": ("cls", 1),
}

Inclusion = tuple[str, str]  # (name as written, extension)


def learn(command: TexCommand, registry: SignatureRegistry, catcodes: CatcodeTable) -> list[Inclusion]:
    """Record what `command` defines; return the files it makes TeX read.

    `catcodes` is the table the definition was read under.
    """
    name = command.base_name
    arguments = command.arguments
    if arguments is None:
        return []
    if name in NEWCOMMAND:
        target, count, default = (_command_name(arguments[0]), arguments[1], arguments[2])
        spec = _arity_spec(count, default, long=not command.star)
        body = arguments[3]
        definitions: Sequence[CommandSignature | Delegation | Macro] = ()
        if target and count is None and body is not None and LOOKAHEAD.search(str(body)):
            definitions = _delegation(target, body, catcodes)
        elif target and spec is not None:
            signature = CommandSignature.from_spec(target, spec)
            parameters = _parameters(signature.arguments, default)
            bodies = [] if body is None else [Macro(target, parameters, _body_text(body), catcodes)]
            definitions = [signature, *bodies]
        if name in PROVIDE and registry.command(target or ""):
            return []
        for definition in definitions:
            registry.define(definition)
    elif name in DEF:
        _learn_def(name, arguments, registry, catcodes)
    elif name in NEWENVIRONMENT:
        target = _text(arguments[0])
        spec = _arity_spec(arguments[1], arguments[2], long=not command.star)
        if target and spec is not None:
            environment = EnvironmentSignature.from_spec(target, spec)
            registry.define(environment)
            begin, end = arguments[3], arguments[4]
            if begin is not None and end is not None:
                parameters = _parameters(environment.arguments, arguments[2])
                registry.define(
                    EnvironmentMacro(target, parameters, _body_text(begin), _body_text(end), catcodes)
                )
    elif name in DOCUMENT_COMMAND | DOCUMENT_ENVIRONMENT:
        _learn_document(name, arguments, registry, catcodes)
    elif name == "newif":
        conditional = _command_name(arguments[0])
        if conditional is not None and conditional.startswith("if") and len(conditional) > 2:
            registry.define(Boolean(conditional[2:], False))
    elif name == "let" or name in COMMAND_COPY:
        source = arguments[-1] if name == "let" else arguments[1]
        target, origin = _command_name(arguments[0]), _command_name(source)
        if target and origin:
            registry.copy_command(target, origin)
    elif name in INCLUDING_COMMANDS:
        return _inclusions(name, arguments)
    return []


def _inclusions(name: str, arguments: list[TexContainer | None]) -> list[Inclusion]:
    extension, index = INCLUDING_COMMANDS[name]
    text = _text(arguments[index]) if index < len(arguments) else None
    if not text:
        return []
    names = [part.strip() for part in text.split(",")] if extension == "sty" else [text]
    # `\\input{#1}` in a definition, `\\input{\\jobname.aux}`: nothing to look for.
    return [(part, extension) for part in names if part and "#" not in part and "\\" not in part]


def _delegation(name: str, body: TexContainer, catcodes: CatcodeTable) -> list[Delegation | Macro]:
    """Delegation and branches of a body starting with `\\@ifstar` or `\\@ifnextchar`; nothing otherwise."""
    nodes = [node for node in body if not node.is_comment()] if body.list_container else []
    test = nodes[0] if nodes else None
    # The body is only the test and its arguments (siblings, see `binding`), all present.
    if not isinstance(test, TexCommand) or test.arguments is None or None in test.arguments:
        return []
    if nodes[1:] != test.arguments:
        return []
    delegation = None
    if test.base_name == "@ifstar":
        taken, otherwise = test.arguments
        target = _command_name(otherwise)
        delegation = Delegation(name, target, starred=True) if target else None
    elif test.base_name in ("@ifnextchar", "kernel@ifnextchar"):
        character, taken, otherwise = test.arguments
        target = _command_name(otherwise)
        optional = OPTIONAL_BY_CHARACTER.get(_text(character) or "")
        delegation = Delegation(name, target, arguments=(optional,)) if target and optional else None
    if delegation is None:
        return []
    assert taken is not None and otherwise is not None
    return [delegation, Macro(name, (), _body_text(taken), catcodes, otherwise=_body_text(otherwise))]


def _learn_def(
    name: str, arguments: list[TexContainer | None], registry: SignatureRegistry, catcodes: CatcodeTable
) -> None:
    target, parameters, body = arguments
    target_name = _command_name(target)
    if not target_name:
        return
    if not isinstance(body, TexGroup) or not body.bracket:
        return  # with no body, nothing is defined
    count = _parameter_count(parameters, body)
    if count is None:
        # Delimited parameters (`#1.`, `[#1]`): another way of reading, which we cannot state.
        registry.forget_command(target_name)
        return
    registry.define(CommandSignature(target_name, (ArgumentSpec("m"),) * count))
    if name not in EXPANDED_DEF:
        registry.define(Macro(target_name, (ArgumentSpec("m"),) * count, _body_text(body), catcodes))


def _parameter_count(parameters: TexContainer | None, body: TexGroup) -> int | None:
    """How many parameters `\\def\\x#1#2{…}` has; None if they are delimited.

    The binding only keeps the first element of the parameter text: alone, it
    touches the brace of the body.
    """
    if parameters is None:
        return 0
    if (
        not parameters.is_pure_text()
        or parameters.end_position != body.start_position
        or not UNDELIMITED_PARAMETERS.fullmatch(parameters.content)
    ):
        return None
    numbers = parameters.content[1::2]
    return len(numbers) if numbers == "123456789"[: len(numbers)] else None


def _learn_document(
    name: str, arguments: list[TexContainer | None], registry: SignatureRegistry, catcodes: CatcodeTable
) -> None:
    environment = name in DOCUMENT_ENVIRONMENT
    target = _text(arguments[0]) if environment else _command_name(arguments[0])
    spec = _text(arguments[1])
    if not target or spec is None:
        return
    if name in PROVIDE and (registry.environment(target) if environment else registry.command(target)):
        return
    try:
        signature = (EnvironmentSignature if environment else CommandSignature).from_spec(target, spec)
        starred = parse_spec(spec)[0]
    except ValueError:
        return  # unreadable signature: better to learn nothing than to learn wrong
    registry.define(signature)
    body = arguments[2]
    if body is None or not all(argument.kind in EXPANDABLE_KINDS for argument in signature.arguments):
        return
    parameters = tuple(replace(argument, long=False) for argument in signature.arguments)
    if isinstance(signature, CommandSignature):
        registry.define(Macro(target, parameters, _body_text(body), catcodes, starred=starred))
    elif starred:
        return  # `\\begin{x}*`: the star of an environment is not read
    elif arguments[3] is not None:
        begin, end = _body_text(body), _body_text(arguments[3])
        registry.define(EnvironmentMacro(target, parameters, begin, end, catcodes, end_arguments=True))


def _parameters(
    arguments: tuple[ArgumentSpec, ...], default: TexContainer | None
) -> tuple[ArgumentSpec, ...]:
    """Parameters of a `\\newcommand` body: the optional one of `[n][d]` takes `d` as its default."""
    return tuple(
        ArgumentSpec("O", default=_text(default) or "") if argument.kind == "o" else ArgumentSpec("m")
        for argument in arguments
    )


def _arity_spec(count: TexContainer | None, default: TexContainer | None, long: bool) -> str | None:
    """`[2][d]` → `+o +m`; `None` if the number of arguments is not an integer from 0 to 9.

    Without a star, `\\newcommand` defines a long macro: an argument may hold a
    blank line (`\\methode{title}` followed by a paragraph).
    """
    text = _text(count) if count is not None else "0"
    if text is None or not text.isdigit() or int(text) > 9:
        return None
    specs = ["m"] * int(text)
    if default is not None and specs:
        specs[0] = "o"
    prefix = "+" if long else ""
    return " ".join(prefix + spec for spec in specs)


def _body_text(node: TexContainer) -> str:
    """The body as it reads back: the inside of the braces, or the lone token."""
    if isinstance(node, TexGroup) and node.bracket:
        return node.arg()
    return str(node)


def _command_name(node: TexContainer | None) -> str | None:
    """`\\x` or `{\\x}` → `x`."""
    if node is not None and node.list_container and len(node) == 1:
        node = node[0]
    if isinstance(node, TexCommand):
        return node.content
    return None


def _text(node: TexContainer | None) -> str | None:
    """The text of an argument: the inside of a group, or the text itself."""
    if node is None:
        return None
    if isinstance(node, TexGroup) and (node.bracket or node.option):
        return node.arg().strip()
    if node.is_pure_text():
        return node.content
    return None
