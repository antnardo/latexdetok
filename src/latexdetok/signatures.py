"""Signatures of commands and environments, in the format of `\\NewDocumentCommand`.

Why that format: it is the kernel's own since 2020 (ltcmd, the former xparse).
Signatures declared by the kernel and by the user therefore read as they stand,
with no translation table. `\\section` is written `s o m`: a possible star, one
optional argument, one mandatory.

Argument types. Every ltcmd type is recognised when reading, but only
`m o O t v r R d D` serve to bind arguments in the tree. The others
(`e E u l b g G`) stop the binding: better to bind nothing than to bind wrong.
The star `s` is only understood at the head, where the tokeniser glues it to the
name (`\\section*`).

Tables, loaded in this order, the last one winning:

- `data/kernel-harvested.txt`: produced by `scripts/harvest.py` from the TeX
  Live sources (math symbols and accents, commands declared with their
  signature);
- `data/kernel.txt`: written by hand — the LaTeX2e kernel and the standard
  classes, wherever the harvest cannot see the signature (`\\@ifstar`,
  `\\@ifnextchar` do not read);
- `data/packages.txt`: outside the kernel, the signatures of the common
  packages, written by hand. The `.sty` of the distribution are not read: their
  interfaces are declared through code, and reading them invents names and loses
  kernel signatures (see `resolution`);
- `data/verbatim.txt`: outside the kernel, the commands and environments of
  packages whose content must be read without analysis (`\\url`, `minted`…),
  failing which one `%` or one `{` of code breaks the structure of the whole
  file.

The format of a line: name, mode, signature (`-` if empty; `verbatim` for an
environment read without analysis). `\\name` for a command, `{name}` for an
environment; a line starting with `#` is a comment.

The registry also keeps the bodies of the user's macros (`Macro`) and the code
of environments (`EnvironmentMacro`), which `expansion` expands: a signature
says how to read a use, a body what it becomes. It also follows the value of
the booleans of `\\newif` (`Boolean`), which the tokeniser keeps up to date as
reading goes on.
"""

from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from enum import StrEnum
from functools import cache
from importlib.resources import files
from pathlib import Path
from typing import Self

from latexdetok.catcodes import CatcodeTable

__all__ = [
    "ArgumentSpec",
    "Boolean",
    "CommandSignature",
    "Delegation",
    "EnvironmentMacro",
    "EnvironmentSignature",
    "Macro",
    "Mode",
    "SignatureRegistry",
    "parse_spec",
]

# Through the package, not through `__file__`: compiled, this module lives in `_mypyc/` (see `compilation`).
DATA = Path(str(files("latexdetok"))) / "data"
TABLES = ("kernel-harvested.txt", "kernel.txt", "packages.txt", "verbatim.txt")

# Types whose binding is understood; the others stop it.
BOUND_KINDS = frozenset("mOotvrRdDl")
MANDATORY_KINDS = frozenset("mvrR")


class Mode(StrEnum):
    """The mode where a command means something: the meaning diagnostics use it (see `semantics`)."""

    TEXT = "text"
    MATH = "math"
    ANY = "any"


@dataclass(frozen=True, slots=True)
class ArgumentSpec:
    """One argument: its ltcmd type, and what makes it precise."""

    kind: str
    long: bool = False
    default: str | None = None  # O{…}, R()…{…}, D()…{…}
    delimiters: str = ""  # r() → "()", t= → "="

    @property
    def mandatory(self) -> bool:
        return self.kind in MANDATORY_KINDS

    @property
    def bound(self) -> bool:
        return self.kind in BOUND_KINDS

    @property
    def opener(self) -> str:
        """The character that announces the argument: `[` for `o`, `(` for `r()`…"""
        if self.kind in "oO":
            return "["
        return self.delimiters[:1]

    @property
    def closer(self) -> str:
        if self.kind in "oO":
            return "]"
        return self.delimiters[1:2]

    def __str__(self) -> str:
        delimiters = f"{{{self.delimiters}}}" if self.kind in "eEu" else self.delimiters
        text = ("+" if self.long else "") + self.kind + delimiters
        return text if self.default is None else f"{text}{{{self.default}}}"


def _skip_spaces(spec: str, index: int) -> int:
    # ltcmd allows spaces between a type and its parameters: `O { \\@currname }`.
    while index < len(spec) and spec[index].isspace():
        index += 1
    return index


def _braced(spec: str, index: int) -> tuple[str, int]:
    """The content of the group `{…}` starting at `index`, and the index that follows it."""
    index = _skip_spaces(spec, index)
    if index >= len(spec) or spec[index] != "{":
        raise ValueError(f"signature {spec!r}: a brace was expected at {index}")
    depth = 0
    for position in range(index, len(spec)):
        if spec[position] == "{":
            depth += 1
        elif spec[position] == "}":
            depth -= 1
            if depth == 0:
                return spec[index + 1 : position], position + 1
    raise ValueError(f"signature {spec!r}: unclosed brace")


def _token(spec: str, index: int) -> tuple[str, int]:
    """One token: a character, or a control sequence `\\name`."""
    index = _skip_spaces(spec, index)
    if index >= len(spec):
        raise ValueError(f"signature {spec!r}: a token was expected at the end")
    if spec[index] == "\\":
        end = index + 1
        while end < len(spec) and spec[end].isalpha():
            end += 1
        return spec[index : max(end, index + 2)], max(end, index + 2)
    return spec[index], index + 1


def parse_spec(spec: str) -> tuple[bool, tuple[ArgumentSpec, ...]]:
    """`(starred, arguments)` of an ltcmd signature; raises `ValueError` if unreadable."""
    arguments: list[ArgumentSpec] = []
    starred = False
    index = 0
    while index < len(spec):
        char = spec[index]
        if char.isspace():
            index += 1
            continue
        long = False
        while char in "+!>=":
            if char == "+":
                long = True
            index += 1
            if char in ">=":
                # Processor (`>{\SplitList{,}}`) or key (`={name}`): no effect on the reading.
                _, index = _braced(spec, index)
            if index >= len(spec):
                raise ValueError(f"signature {spec!r}: a type was expected after a prefix")
            char = spec[index]
        kind = char
        index += 1
        if kind in "mvlbog":
            arguments.append(ArgumentSpec(kind, long))
        elif kind == "s":
            if arguments or starred:
                arguments.append(ArgumentSpec("t", long, delimiters="*"))
            else:
                starred = True
        elif kind in "OG":
            default, index = _braced(spec, index)
            arguments.append(ArgumentSpec(kind, long, default=default))
        elif kind == "t":
            token, index = _token(spec, index)
            arguments.append(ArgumentSpec(kind, long, delimiters=token))
        elif kind in "rRdD":
            opener, index = _token(spec, index)
            closer, index = _token(spec, index)
            default = None
            if kind in "RD":
                default, index = _braced(spec, index)
            arguments.append(ArgumentSpec(kind, long, default=default, delimiters=opener + closer))
        elif kind in "eE":
            tokens, index = _braced(spec, index)
            if kind == "E":
                _, index = _braced(spec, index)
            arguments.append(ArgumentSpec(kind, long, delimiters=tokens))
        elif kind == "u":
            index = _skip_spaces(spec, index)
            tokens, index = _braced(spec, index) if spec[index : index + 1] == "{" else _token(spec, index)
            arguments.append(ArgumentSpec(kind, long, delimiters=tokens))
        else:
            raise ValueError(f"signature {spec!r}: unknown argument type {kind!r}")
    return starred, tuple(arguments)


@dataclass(frozen=True, slots=True)
class CommandSignature:
    """The signature of a command; `name` without backslash or star."""

    name: str
    arguments: tuple[ArgumentSpec, ...] = ()
    starred: bool = False
    mode: Mode = Mode.ANY

    @classmethod
    def from_spec(cls, name: str, spec: str, mode: Mode = Mode.ANY) -> Self:
        starred, arguments = parse_spec(spec)
        return cls(name, arguments, starred, mode)

    @property
    def spec(self) -> str:
        return " ".join((["s"] if self.starred else []) + [str(argument) for argument in self.arguments])

    @property
    def verbatim(self) -> bool:
        """The first argument is read without analysis (`\\verb|…|`, `\\url{…}`)."""
        return bool(self.arguments) and self.arguments[0].kind == "v"


@dataclass(frozen=True, slots=True)
class EnvironmentSignature:
    """The signature of an environment: its arguments are its first children."""

    name: str
    arguments: tuple[ArgumentSpec, ...] = ()
    verbatim: bool = False
    mode: Mode = Mode.ANY

    @classmethod
    def from_spec(cls, name: str, spec: str, mode: Mode = Mode.ANY) -> Self:
        if spec == "verbatim":
            return cls(name, (), verbatim=True, mode=mode)
        _, arguments = parse_spec(spec)
        return cls(name, arguments, mode=mode)

    @property
    def spec(self) -> str:
        return "verbatim" if self.verbatim else " ".join(str(argument) for argument in self.arguments)


@dataclass(frozen=True, slots=True)
class Delegation:
    """The signature of a macro that lets another one read its arguments.

    `\\newcommand{\\rmqphys}{\\@ifstar{…}{\\@rmqphys}}`: `\\rmqphys` reads the star
    (`starred`) or a `[` (`arguments`), then whatever `\\@rmqphys` reads
    (`target`). The target's signature is looked up at the use, not at the
    definition: a helper is often defined after the macro that calls it.
    """

    name: str
    target: str
    arguments: tuple[ArgumentSpec, ...] = ()
    starred: bool = False


@dataclass(frozen=True, slots=True)
class Macro:
    """The body of a user macro, so that it can be expanded (see `expansion`).

    `parameters` only holds `m`, `o`, `O{default}` and `t`: what substituting
    `#1`…`#9` can render. A missing `o` becomes `-NoValue-`, a `t` or the leading
    star (`starred`, then `#1`) becomes `\\BooleanTrue` or `\\BooleanFalse`, which
    `\\IfValueTF` and `\\IfBooleanTF` can then decide (see `conditions`). `body` is
    the text of the body; `catcodes`, the table it was read under. TeX cuts a
    body into tokens at definition time: written under `\\makeatletter`,
    `\\@titre` is one single command there, even when used where `@` is not a
    letter. The expanded view therefore re-reads the body under that table.

    A macro that tests what follows (`\\@ifstar{A}{B}`, see `Delegation`) has two
    branches: `body` is `A`, taken with the star or the optional argument,
    `otherwise` is `B`. The use says which one, since its signature read the star
    or the bracket.
    """

    name: str
    parameters: tuple[ArgumentSpec, ...]
    body: str
    catcodes: CatcodeTable
    otherwise: str | None = None
    starred: bool = False


@dataclass(frozen=True, slots=True)
class EnvironmentMacro:
    """The begin and end code of a user environment (see `Macro`).

    `\\newenvironment` only gives its arguments to the begin code;
    `\\NewDocumentEnvironment` gives them to the end code too (`end_arguments`).
    """

    name: str
    parameters: tuple[ArgumentSpec, ...]
    begin: str
    end: str
    catcodes: CatcodeTable
    end_arguments: bool = False


@dataclass(frozen=True, slots=True)
class Boolean:
    """A boolean of `\\newif` (`prof` for `\\ifprof`) and its value; None when it cannot be known."""

    name: str
    value: bool | None


# Depth of delegation beyond which we give up (and which stops loops).
MAX_DELEGATION_DEPTH = 8

Definition = CommandSignature | EnvironmentSignature | Delegation | Macro | EnvironmentMacro | Boolean
JournalEntry = tuple[str, Definition | str]


class SignatureRegistry:
    """Signatures known by name.

    Every analysis works on its own copy (`SignatureRegistry.kernel()`), which
    the definitions read in the file enrich without touching the tables. A
    command has a signature or a delegation, and sometimes a body (`macro`): any
    new signature for a name forgets its body, which the definition then gives
    back if it has one. `record()` notes what changes during a block, so that it
    can be replayed without re-reading the file that defined it (see
    `resolution`).
    """

    def __init__(self) -> None:
        self._commands: dict[str, CommandSignature] = {}
        self._delegations: dict[str, Delegation] = {}
        self._macros: dict[str, Macro] = {}
        self._environments: dict[str, EnvironmentSignature] = {}
        self._environment_macros: dict[str, EnvironmentMacro] = {}
        self._booleans: dict[str, bool | None] = {}
        self._journals: list[list[JournalEntry]] = []

    @classmethod
    def kernel(cls) -> "SignatureRegistry":
        return _kernel_registry().copy()

    def copy(self) -> "SignatureRegistry":
        registry = SignatureRegistry()
        registry._commands = dict(self._commands)
        registry._delegations = dict(self._delegations)
        registry._macros = dict(self._macros)
        registry._environments = dict(self._environments)
        registry._environment_macros = dict(self._environment_macros)
        registry._booleans = dict(self._booleans)
        return registry

    def command(self, name: str) -> CommandSignature | None:
        return self._resolve(name, MAX_DELEGATION_DEPTH)

    def _resolve(self, name: str, depth: int) -> CommandSignature | None:
        signature = self._commands.get(name)
        if signature is not None:
            return signature
        delegation = self._delegations.get(name)
        if delegation is None or depth == 0:
            return None
        target = self._resolve(delegation.target, depth - 1)
        if target is None:
            return None
        # A star the target reads after what the macro has already read is no longer at the head: `t*`.
        star = (ArgumentSpec("t", delimiters="*"),) if target.starred else ()
        return CommandSignature(
            name,
            delegation.arguments + star + target.arguments,
            starred=delegation.starred,
            mode=target.mode,
        )

    def environment(self, name: str) -> EnvironmentSignature | None:
        return self._environments.get(name)

    def macro(self, name: str) -> Macro | None:
        return self._macros.get(name)

    def environment_macro(self, name: str) -> EnvironmentMacro | None:
        return self._environment_macros.get(name)

    def command_names(self) -> frozenset[str]:
        """The known commands: signature, delegation or body."""
        return frozenset(self._commands) | frozenset(self._delegations) | frozenset(self._macros)

    def environment_names(self) -> frozenset[str]:
        return frozenset(self._environments) | frozenset(self._environment_macros)

    def is_boolean(self, name: str) -> bool:
        return name in self._booleans

    def boolean(self, name: str) -> bool | None:
        """The value of the boolean `name`; None if it is unknown, or if there is no such boolean."""
        return self._booleans.get(name)

    def booleans(self) -> frozenset[tuple[str, bool | None]]:
        """The booleans and their values: what the reading of a file that tests them depends on."""
        return frozenset(self._booleans.items())

    def define(self, signature: Definition) -> None:
        """The last definition of a name wins, signature or delegation.

        A body (`Macro`, `EnvironmentMacro`) completes the signature defined just
        before it.
        """
        if isinstance(signature, CommandSignature):
            self._delegations.pop(signature.name, None)
            self._macros.pop(signature.name, None)
            self._commands[signature.name] = signature
        elif isinstance(signature, Delegation):
            self._commands.pop(signature.name, None)
            self._macros.pop(signature.name, None)
            self._delegations[signature.name] = signature
        elif isinstance(signature, Macro):
            self._macros[signature.name] = signature
        elif isinstance(signature, EnvironmentMacro):
            self._environment_macros[signature.name] = signature
        elif isinstance(signature, Boolean):
            self._booleans[signature.name] = signature.value
        else:
            self._environment_macros.pop(signature.name, None)
            self._environments[signature.name] = signature
        self._note(("define", signature))

    def forget_command(self, name: str) -> None:
        self._commands.pop(name, None)
        self._delegations.pop(name, None)
        self._macros.pop(name, None)
        self._note(("forget", name))

    def copy_command(self, target: str, source: str) -> None:
        """`\\let\\target\\source`: the target takes the signature and body of the source, or forgets them."""
        signature = self._commands.get(source)
        delegation = self._delegations.get(source)
        macro = self._macros.get(source)
        if signature is not None:
            self.define(replace(signature, name=target))
        elif delegation is not None:
            self.define(replace(delegation, name=target))
        else:
            self.forget_command(target)
        if macro is not None and (signature is not None or delegation is not None):
            self.define(replace(macro, name=target))

    @contextmanager
    def record(self) -> Iterator[list[JournalEntry]]:
        """Note the definitions and forgettings of the block, nested blocks included."""
        journal: list[JournalEntry] = []
        self._journals.append(journal)
        try:
            yield journal
        finally:
            self._journals.pop()

    def replay(self, journal: Iterable[JournalEntry]) -> None:
        for _, value in journal:
            if isinstance(value, str):
                self.forget_command(value)
            else:
                self.define(value)

    def _note(self, entry: JournalEntry) -> None:
        for journal in self._journals:
            journal.append(entry)

    def load(self, lines: Iterable[str], origin: str = "<table>") -> None:
        """Load a table; raises `ValueError` with the offending line."""
        for lineno, line in enumerate(lines, start=1):
            text = line.strip()
            if not text or text.startswith("#"):
                continue
            parts = text.split(None, 2)
            if len(parts) < 2:
                raise ValueError(f"{origin}:{lineno}: “name mode signature” expected, read {line.strip()!r}")
            name, mode_name = parts[0], parts[1]
            spec = parts[2] if len(parts) == 3 and parts[2] != "-" else ""
            try:
                mode = Mode(mode_name)
                if name.startswith("{") and name.endswith("}") and len(name) > 2:
                    self.define(EnvironmentSignature.from_spec(name[1:-1], spec, mode))
                elif name.startswith("\\") and len(name) > 1:
                    self.define(CommandSignature.from_spec(name[1:], spec, mode))
                else:
                    raise ValueError(f"name {name!r}: `\\name` or `{{name}}` expected")
            except ValueError as error:
                raise ValueError(f"{origin}:{lineno}: {error}") from error

    def __len__(self) -> int:
        return len(self._commands) + len(self._environments)


@cache
def _kernel_registry() -> SignatureRegistry:
    registry = SignatureRegistry()
    for table in TABLES:
        path = DATA / table
        with path.open(encoding="utf-8") as file:
            registry.load(file, origin=table)
    return registry
