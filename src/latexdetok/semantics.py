"""Meaning: what is properly matched but cannot be typeset where it is written.

Structure says what opens and closes (see `diagnostics`); meaning says whether
what we read belongs there: an `\\item` outside a list, an `&` outside an
alignment, a `^` or an `\\alpha` outside math, a mandatory argument that is
missing, a `\\left` without its `\\right`, a `\\\\` with no line to end. TeX
refuses them with messages about its own machinery (“Missing $ inserted”,
“Misplaced alignment tab character &”, “There's no line here to end”); here, we
name what is written, and where.

Context. The tree is walked with, for every group, what we know of the place:
the mode (text, math), whether we are in a list, in an alignment. Every answer
may be “we do not know”, and then nothing is reported. That is the case in an
unknown environment (a package may make a list or a table of it), in the
argument of an unknown command, of an unexpanded user macro or of a command that
does not typeset its argument (`\\label`, `\\directlua`), and in the body of a
definition. A fragment, which another file may include inside a list or a table,
starts with no list and no alignment known.

A kernel environment is only known as long as the document has not redefined it;
`center`, `quote` and the other environments built on `trivlist` accept
`\\item`, as in LaTeX. The environments of amsmath are known by their name
(`conditions.MATH_ENVIRONMENTS`).

The walk works on what it is given: the expanded view, so that the user's macros
write what they write (see `checks`).
"""

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, replace
from typing import Protocol

from latexdetok.analyse import TexFile
from latexdetok.classes import Position, TexBranch, TexCommand, TexComment, TexContainer, TexContent, TexGroup
from latexdetok.conditions import MATH_ARGUMENT_COMMANDS, MATH_ENVIRONMENTS, TEXT_ARGUMENT_COMMANDS, setter
from latexdetok.definitions import DEFINING_COMMANDS
from latexdetok.diagnostics import Related, Severity, TexDiagnostic, similar
from latexdetok.messages import say
from latexdetok.parser import UNTYPESET_ARGUMENTS, unknown_command_before
from latexdetok.signatures import Mode, SignatureRegistry

__all__ = [
    "ALIGNMENT_ENVIRONMENTS",
    "LIST_ENVIRONMENTS",
    "NAME_ARGUMENTS",
    "SEMANTIC_CATALOGUE",
    "FileFinder",
    "read_meaning",
]

SEMANTIC_CATALOGUE: dict[str, str] = {
    "item-outside-list": "“\\item” outside any list",
    "ampersand-outside-alignment": "“&” outside a table or an alignment",
    "script-outside-math": "“^” or “_” outside math",
    "math-command-in-text": "a command that only means something in math mode, written in text",
    "missing-argument": "a mandatory argument is missing",
    "left-without-right": "“\\left” without “\\right” in the same group",
    "right-without-left": "“\\right” without “\\left” in the same group",
    "left-across-cells": "“\\left” and “\\right” on either side of a “&” or a “\\\\”",
    "line-break-without-line": "“\\\\” where no line has begun",
    "file-not-found": "a loaded file (`\\input`, `\\usepackage`…) or an image is missing",
    "unknown-command": "unknown command, one typo away from a known one",
    "unknown-environment": "unknown environment, one typo away from a known one",
}

# Where `\item` is allowed: the kernel lists, and what it builds on `trivlist`.
LIST_ENVIRONMENTS = frozenset(
    {
        "itemize",
        "enumerate",
        "description",
        "list",
        "trivlist",
        "thebibliography",
        "center",
        "flushleft",
        "flushright",
        "quote",
        "quotation",
        "verse",
        # amsthm builds its environments on `trivlist`: `\item` is allowed there, as in `center`.
        "proof",
        "theorem",
        "lemma",
        "definition",
        "remark",
        "example",
    }
)
# Cells separated by `&`: kernel and amsmath.
ALIGNMENT_ENVIRONMENTS = frozenset(
    {
        "tabular",
        "tabular*",
        "array",
        "eqnarray",
        "eqnarray*",
        "align",
        "align*",
        "aligned",
        "alignat",
        "alignat*",
        "alignedat",
        "flalign",
        "flalign*",
        "split",
        "cases",
        "matrix",
        "pmatrix",
        "bmatrix",
        "Bmatrix",
        "vmatrix",
        "Vmatrix",
        "smallmatrix",
        "subarray",
        # Packages declared in `data/packages.txt`.
        "tabularx",
        "tabulary",
        "longtable",
        "supertabular",
        "xtabular",
        "tblr",
        "longtblr",
        "talltblr",
        "NiceTabular",
        "NiceArray",
        "NiceMatrix",
        "pNiceMatrix",
        "bNiceMatrix",
        "vNiceMatrix",
        "dcases",
        "dcases*",
        "rcases",
        "drcases",
        "psmallmatrix",
        "bsmallmatrix",
        "vsmallmatrix",
        "Bsmallmatrix",
        "Vsmallmatrix",
    }
)
# Arguments that name something (a label, a file, a counter): nothing in them is typeset.
NAME_ARGUMENTS = frozenset(
    {
        "label",
        "ref",
        "pageref",
        "cite",
        "nocite",
        "bibliography",
        "bibliographystyle",
        "input",
        "include",
        "includeonly",
        "InputIfFileExists",
        "IfFileExists",
        "usepackage",
        "RequirePackage",
        "documentclass",
        "LoadClass",
        "NeedsTeXFormat",
        "ProvidesPackage",
        "ProvidesClass",
        "ProvidesFile",
        "PassOptionsToPackage",
        "PassOptionsToClass",
        "DeclareOption",
        "newcounter",
        "setcounter",
        "addtocounter",
        "stepcounter",
        "refstepcounter",
        "value",
        "arabic",
        "roman",
        "Roman",
        "alph",
        "Alph",
        "fnsymbol",
        "newlength",
        "setlength",
        "addtolength",
        "pagestyle",
        "thispagestyle",
        "pagenumbering",
        "begin",
        "end",
        # Packages: names of files, labels, colours or styles.
        "includegraphics",
        "includepdf",
        "graphicspath",
        "lstinputlisting",
        "inputminted",
        "addbibresource",
        "cref",
        "Cref",
        "cpageref",
        "crefrange",
        "labelcref",
        "autoref",
        "nameref",
        "hypertarget",
        "hyperlink",
        "citep",
        "citet",
        "citealt",
        "parencite",
        "textcite",
        "autocite",
        "definecolor",
        "colorlet",
        "usetheme",
        "usecolortheme",
    }
)
# Environments whose body is code (TikZ): nothing is checked in them.
CODE_ENVIRONMENTS = frozenset({"tikzpicture", "pgfpicture", "circuitikz", "axis", "picture"})
# Package environments whose body is text, in the mode of what surrounds them.
TEXT_CONTAINERS = frozenset(
    {
        "frame",
        "block",
        "alertblock",
        "exampleblock",
        "columns",
        "column",
        "onlyenv",
        "overlayarea",
        "overprint",
        "multicols",
        "multicols*",
        "tcolorbox",
        "adjustbox",
        "landscape",
        "spacing",
        "singlespace",
        "onehalfspace",
        "doublespace",
    }
)
# A column specification that enters math (`>{$}c<{$}`) or inserts code.
MATH_COLUMNS = re.compile(r"[$<>]|\\\(")
# Tables whose columns are set by key-value pairs (tabularray, nicematrix).
KEYVAL_TABLES = frozenset({"tblr", "longtblr", "talltblr", "NiceTabular"})
BRACED = re.compile(r"\{[^{}]*\}")
# Tokens the tokeniser leaves flat when an opening is dissolved.
DISSOLVED_COMMANDS = frozenset({"begin", "[", "("})
DISSOLVED_TEXTS = frozenset({"{", "[", "$", "$$"})
# After them, TeX is between two paragraphs: a `\\` has no line to end.
VERTICAL_AFTER = LIST_ENVIRONMENTS | {"figure", "figure*", "table", "table*"}


class FileFinder(Protocol):
    """Finds the files a document loads (see `checks`)."""

    def exists(self, extension: str, name: str) -> bool | None:
        """`extension`: `tex`, `sty` or `cls`. None when it cannot be known."""
        ...


# Command → (extension, index of the argument that names the file). Not images: their folders
# come from `\graphicspath` written inside the user's packages, often made of macros
# (`\appendtographicspath{{./\devoirname/}}`), which we cannot run.
FILE_COMMANDS: dict[str, tuple[str, int]] = {
    "input": ("tex", 0),
    "include": ("tex", 0),
    "usepackage": ("sty", 1),
    "RequirePackage": ("sty", 1),
    "documentclass": ("cls", 1),
    "LoadClass": ("cls", 1),
}
# A typo shows on a name long enough, used once.
TYPO_MIN_LENGTH = 5
TYPO_MAX_USES = 1
# An unknown name used at least that many times is a package command, not a typo.
FREQUENT_USES = 3
# Commands of common packages, one letter away from a kernel command: never typos.
COMMON_PACKAGE_COMMANDS = frozenset(
    {
        "xspace",
        "dfrac",
        "tfrac",
        "mathbb",
        "mathscr",
        "mathfrak",
        "uline",
        "iint",
        "iiint",
        "oiint",
        "geqslant",
        "leqslant",
        "bcancel",
        "xcancel",
        "eqref",
        "Cref",
        "cref",
        "bgroup",
        "egroup",
    }
)


@dataclass(slots=True)
class _Lefts:
    """The open `\\left` of a group; `excused`: as many `\\right` as an alignment has already reported."""

    open: list[TexCommand]
    excused: int = 0


@dataclass(frozen=True, slots=True)
class _Frame:
    """What we know of a place: None for “we do not know”."""

    mode: Mode | None
    lists: bool | None
    alignment: bool | None
    trusted: bool
    environment: TexGroup | None = None


def read_meaning(tex: TexFile, files: FileFinder | None = None) -> list[TexDiagnostic]:
    """The meaning diagnostics of an analysed file, in its own positions.

    Without `files`, the loaded files are not looked for.
    """
    return _Reader(tex, files).read()


class _Reader:
    def __init__(self, tex: TexFile, files: FileFinder | None) -> None:
        self._tex = tex
        self._files = files
        self._registry = tex.signatures
        self._kernel = SignatureRegistry.kernel()
        # A character whose category the document changes no longer has a sure meaning.
        self._changed = {change.character for change in tex.catcode_changes}
        self._found: list[TexDiagnostic] = []
        # For the typos: every name used, and the uses of the unknown ones where they are typeset.
        self._uses: Counter[str] = Counter()
        self._unknown_commands: dict[str, list[TexCommand]] = defaultdict(list)
        self._unknown_environments: dict[str, list[TexGroup]] = defaultdict(list)

    def read(self) -> list[TexDiagnostic]:
        unknown = self._tex.fragment
        root = _Frame(Mode.TEXT, None if unknown else False, None if unknown else False, trusted=True)
        self._group(self._tex.container, root)
        self._typos()
        return self._found

    # The walk

    def _group(self, group: TexGroup, frame: _Frame, lefts: _Lefts | None = None) -> None:
        content = group.content
        bound: dict[int, TexCommand | TexGroup] = {
            id(node): group for node in group.arguments if node is not None
        }
        # A taken branch is not a group for TeX: its `\left` match in the stream around it.
        within_flow = lefts is not None
        lefts = _Lefts([]) if lefts is None else lefts
        previous: TexContainer | None = None
        for index, node in enumerate(content):
            if isinstance(node, TexComment):
                continue
            if isinstance(node, TexBranch):
                if node.taken:
                    self._group(node, frame, lefts)  # the discarded one is never read
                previous = node
                continue
            if isinstance(node, TexCommand):
                self._uses[node.base_name] += 1
                for argument in node.arguments or ():
                    if argument is not None:
                        bound[id(argument)] = node
                owner = bound.get(id(node))
                # A token read by a definition (`\def\deg{…}`) or a macro is named, not typeset;
                # the one of another command is read in its mode (`\ensuremath\cdot`).
                if not isinstance(owner, TexCommand):
                    token_frame = frame
                elif _typesets(owner):
                    token_frame = self._argument(owner, frame)
                else:
                    token_frame = replace(frame, trusted=False)
                if token_frame.trusted:
                    self._command(node, token_frame, group, index, previous, lefts)
                if frame.lists is False and _unknown(node):
                    # An unknown command may have opened a list (`\vrbitem`): we no longer know.
                    frame = replace(frame, lists=None)
            elif isinstance(node, TexGroup):
                self._group(node, self._inner(node, frame, bound.get(id(node)), content, index))
            elif _dissolved_opening(node):
                # An opening never closed, already reported: what follows is no longer in its place
                # in the tree (the `\item` of a dissolved `itemize`), we no longer know where we are.
                frame = _Frame(None, None, None, frame.trusted, frame.environment)
            elif node.is_pure_text() and frame.trusted and not node.is_par():
                self._text(node, frame, lefts)
            previous = node
        if frame.trusted and frame.mode is Mode.MATH and not within_flow:
            for left in lefts.open:
                self._left_without_right(left, group)

    def _inner(
        self,
        group: TexGroup,
        frame: _Frame,
        owner: TexCommand | TexGroup | None,
        content: list[TexContainer],
        index: int,
    ) -> _Frame:
        if group.math:
            return _Frame(Mode.MATH, frame.lists, False, frame.trusted, frame.environment)
        if group.env:
            return self._environment(group, frame)
        if isinstance(owner, TexGroup):
            return replace(frame, trusted=False)  # an environment argument: a specification
        if isinstance(owner, TexCommand):
            if not group.bracket or not _typesets(owner):
                return replace(frame, trusted=False)
            return self._argument(owner, frame)
        if unknown_command_before(content, index) is not None:
            return replace(frame, trusted=False)  # perhaps the argument of an unknown command
        return frame

    def _environment(self, group: TexGroup, frame: _Frame) -> _Frame:
        name = group.name
        if frame.trusted and not self._known_environment(name):
            self._unknown_environments[name].append(group)
        if not (self._kernel_environment(name) or name in MATH_ENVIRONMENTS):
            # Unknown: a package may typeset its body in math (`tblr`). Except the known text
            # containers, and the environments of the document, whose view shows the code.
            text = name in TEXT_CONTAINERS or self._registry.environment_macro(name) is not None
            mode = frame.mode if text else None
            return _Frame(mode, None, None, frame.trusted and name not in CODE_ENVIRONMENTS, group)
        mode = Mode.MATH if name in MATH_ENVIRONMENTS or name == "array" else Mode.TEXT
        if mode is Mode.TEXT and name in ALIGNMENT_ENVIRONMENTS and _math_columns(group):
            mode = None  # `>{$}c<{$}`, or a column type defined elsewhere: cells in math
        lists = True if name in LIST_ENVIRONMENTS else False if name == "document" else frame.lists
        return _Frame(mode, lists, name in ALIGNMENT_ENVIRONMENTS, frame.trusted, group)

    def _argument(self, command: TexCommand, frame: _Frame) -> _Frame:
        name = command.base_name
        signature = command.signature
        mode = frame.mode
        if name in MATH_ARGUMENT_COMMANDS or (signature is not None and signature.mode is Mode.MATH):
            mode = Mode.MATH
        elif name in TEXT_ARGUMENT_COMMANDS or (signature is not None and signature.mode is Mode.TEXT):
            # In math, a text command often comes from a macro meant for elsewhere
            # (`\alert` of beamer, redefined for documents without beamer): we do not know.
            mode = None if frame.mode is Mode.MATH else Mode.TEXT
        return replace(frame, mode=mode)

    def _known_environment(self, name: str) -> bool:
        registry = self._registry
        return (
            registry.environment(name) is not None
            or registry.environment_macro(name) is not None
            or name in MATH_ENVIRONMENTS
            or name in TEXT_CONTAINERS
            or name in CODE_ENVIRONMENTS
        )

    def _kernel_environment(self, name: str) -> bool:
        signature = self._kernel.environment(name)
        return (
            signature is not None
            and self._registry.environment(name) == signature
            and self._registry.environment_macro(name) is None
        )

    # Checks

    def _command(
        self,
        command: TexCommand,
        frame: _Frame,
        group: TexGroup,
        index: int,
        previous: TexContainer | None,
        lefts: _Lefts,
    ) -> None:
        name = command.base_name
        if name == "item" and frame.mode is Mode.TEXT and frame.lists is False:
            self._item(command, frame)
        if self._files is not None and name in FILE_COMMANDS:
            self._file(command)
        if (
            command.signature is None
            and command.macro is None
            and command.role is None
            and len(name) >= TYPO_MIN_LENGTH
            and name.isalpha()
            and setter(name, self._registry.is_boolean) is None
        ):
            self._unknown_commands[name].append(command)
        signature = command.signature
        if (
            signature is not None
            and signature.mode is Mode.MATH
            and frame.mode is Mode.TEXT
            and command.macro is None
        ):
            self._add(
                "math-command-in-text",
                say("math-command-in-text", command=f"\\{command.content}"),
                command,
                suggestion=say("math-command-in-text.fix", math=f"${command.raw_text()}$"),
            )
        if command.arguments is not None and name not in DEFINING_COMMANDS and command.missing_arguments():
            # The arguments of a definition are tokens, which may be a closing brace.
            self._missing(command, group, index)
        if frame.mode is Mode.MATH:
            if name == "left":
                lefts.open.append(command)
            elif name == "right":
                if lefts.open:
                    lefts.open.pop()
                elif lefts.excused:
                    lefts.excused -= 1
                else:
                    self._add(
                        "right-without-left",
                        say("right-without-left"),
                        command,
                        suggestion=say("right-without-left.fix"),
                    )
        if name == "\\":
            if frame.alignment and lefts.open:
                self._crossed(lefts, command, "\\\\")
            elif frame.mode is Mode.TEXT and frame.alignment is False and _vertical(previous):
                self._no_line(command, previous)

    def _text(self, node: TexContainer, frame: _Frame, lefts: _Lefts) -> None:
        assert node.start_position is not None and node.end_position is not None
        (line, start), (_, end) = node.start_position, node.end_position
        raw = self._tex.lines[line - 1][start:end] if 1 <= line <= len(self._tex.lines) else ""
        ampersand = raw.find("&")
        if ampersand >= 0 and "&" not in self._changed:
            where = (line, start + ampersand)
            if frame.alignment and lefts.open:
                self._crossed(lefts, where, "&")
            elif frame.alignment is False:
                self._ampersand(where, frame)
        if frame.mode is Mode.TEXT:
            for character in "^_":
                column = raw.find(character)
                if column >= 0 and character not in self._changed:
                    where = (line, start + column)
                    written = "\\textasciicircum" if character == "^" else "\\_"
                    self._found.append(
                        TexDiagnostic(
                            "script-outside-math",
                            Severity.ERROR,
                            say("script-outside-math", character=character),
                            where,
                            (line, start + column + 1),
                            suggestion=say("script-outside-math.fix", character=character, written=written),
                        )
                    )

    def _file(self, command: TexCommand) -> None:
        assert self._files is not None
        name = command.base_name
        extension, position = FILE_COMMANDS[name]
        arguments = command.arguments or []
        argument = arguments[position] if position < len(arguments) else None
        text = _argument_text(argument)
        if argument is None or text is None or "\\" in text or "#" in text:
            return
        names = [part.strip() for part in text.split(",")] if extension == "sty" else [text]
        missing = [part for part in names if part and self._files.exists(extension, part) is False]
        if not missing:
            return
        suggestion = say("file-not-found.fix" + (".include" if name == "include" else ""))
        # A fragment searches from its own folder, which may not be the one it is compiled in.
        doubtful = name == "include" or self._tex.fragment
        assert argument.start_position is not None and argument.end_position is not None
        self._found.append(
            TexDiagnostic(
                "file-not-found",
                Severity.WARNING if doubtful else Severity.ERROR,
                say("file-not-found", command=f"\\{command.content}{{{', '.join(missing)}}}"),
                argument.start_position,
                argument.end_position,
                suggestion=suggestion,
            )
        )

    def _typos(self) -> None:
        """An unknown name, used once, one typo away from a known name: an info.

        Packages are not read: their commands are unknown, and many are one letter
        away from another (`\\dfrac` and `\\frac`, `\\xspace` and `\\hspace`). Only
        one letter changed or two letters swapped count, therefore, on a name of at
        least five letters, against a known command or an unknown name the document
        uses often; and the commands of the most common packages are never taken
        for typos.
        """
        frequent = {
            name
            for name in self._unknown_commands
            if self._uses[name] >= FREQUENT_USES and name not in COMMON_PACKAGE_COMMANDS
        }
        commands = self._registry.command_names() | frequent
        for name, uses in self._unknown_commands.items():
            if self._uses[name] > TYPO_MAX_USES or name in COMMON_PACKAGE_COMMANDS:
                continue
            match = _closest(name, commands)
            for command in uses if match is not None else ():
                self._found.append(
                    TexDiagnostic(
                        "unknown-command",
                        Severity.INFO,
                        say("unknown-command", command=f"\\{command.content}", match=f"\\{match}"),
                        *_span(command),
                    )
                )
        environments = self._registry.environment_names() | MATH_ENVIRONMENTS | TEXT_CONTAINERS
        for name, groups in self._unknown_environments.items():
            if len(groups) > TYPO_MAX_USES:
                continue
            match = _closest(name, environments)
            for group in groups if match is not None else ():
                assert group.start_position is not None
                self._found.append(
                    TexDiagnostic(
                        "unknown-environment",
                        Severity.INFO,
                        say("unknown-environment", name=name, match=match),
                        group.start_position,
                        group.inner_start or group.start_position,
                    )
                )

    def _item(self, command: TexCommand, frame: _Frame) -> None:
        related: tuple[Related, ...] = ()
        environment = frame.environment
        if environment is not None and environment.name != "document":
            related = _opening(
                environment,
                say("item-outside-list.related", opening=f"\\begin{{{environment.name}}}"),
            )
        self._add(
            "item-outside-list",
            say("item-outside-list"),
            command,
            related,
            say("item-outside-list.fix"),
        )

    def _ampersand(self, where: Position, frame: _Frame) -> None:
        environment = frame.environment
        related: tuple[Related, ...] = ()
        if environment is not None and environment.name != "document":
            related = _opening(
                environment,
                say("ampersand-outside-alignment.related", opening=f"\\begin{{{environment.name}}}"),
            )
        suggestion = say("ampersand-outside-alignment.fix." + ("math" if frame.mode is Mode.MATH else "text"))
        self._found.append(
            TexDiagnostic(
                "ampersand-outside-alignment",
                Severity.ERROR,
                say("ampersand-outside-alignment"),
                where,
                (where[0], where[1] + 1),
                related,
                suggestion,
            )
        )

    def _missing(self, command: TexCommand, group: TexGroup, index: int) -> None:
        content = group.content
        signature = command.signature
        assert signature is not None and command.arguments is not None
        missing = len(command.missing_arguments())
        mandatory = sum(1 for spec in signature.arguments if spec.mandatory and spec.bound)
        present = [argument for argument in command.arguments if argument is not None]
        last = present[-1] if present else command
        position = next((i for i in range(index, len(content)) if content[i] is last), index)
        following = next((node for node in content[position + 1 :] if not isinstance(node, TexComment)), None)
        if following is not None and following.is_pure_text() and following.content.startswith("{"):
            return  # the brace was never closed: the structure has already reported it
        related: tuple[Related, ...] = ()
        if following is not None and following.start_position is not None:
            if following.is_par():
                reason = say("missing-argument.related.blank-line")
            elif following.is_env():
                reason = say("missing-argument.related.environment")
            else:
                reason = say("missing-argument.related.instead")
            related = (
                Related(following.start_position, following.end_position or following.start_position, reason),
            )
        elif following is None and not group.is_root and group.inner_end is not None and group.end_position:
            closing = group.enclosures()[1]
            if closing:
                related = (
                    Related(
                        group.inner_end,
                        group.end_position,
                        say("missing-argument.related.closing", closing=closing),
                    ),
                )
        count = say(f"missing-argument.count.{'one' if missing == 1 else 'many'}", count=missing)
        self._add(
            "missing-argument",
            say(
                "missing-argument",
                command=f"\\{command.content}",
                count=count,
                mandatory=mandatory,
            ),
            command,
            related,
            say("missing-argument.fix", spec=signature.spec),
        )

    def _crossed(self, lefts: _Lefts, where: TexCommand | Position, separator: str) -> None:
        left = lefts.open[-1]
        if isinstance(where, TexCommand):
            assert where.start_position is not None and where.end_position is not None
            start, end = where.start_position, where.end_position
        else:
            start, end = where, (where[0], where[1] + 1)
        self._found.append(
            TexDiagnostic(
                "left-across-cells",
                Severity.ERROR,
                say("left-across-cells", separator=separator),
                start,
                end,
                _span_related(left, say("left-across-cells.related")),
                say("left-across-cells.fix"),
            )
        )
        # Their `\\right`, in another cell, are the same mistake: we do not report them yet.
        lefts.excused += len(lefts.open)
        lefts.open.clear()

    def _left_without_right(self, left: TexCommand, group: TexGroup) -> None:
        related: tuple[Related, ...] = ()
        if group.inner_end is not None and group.end_position is not None and not group.is_root:
            closing = group.enclosures()[1] or say("left-without-right.related.end-of-group")
            related = (
                Related(
                    group.inner_end, group.end_position, say("left-without-right.related", closing=closing)
                ),
            )
        self._add(
            "left-without-right",
            say("left-without-right"),
            left,
            related,
            say("left-without-right.fix"),
        )

    def _no_line(self, command: TexCommand, previous: TexContainer | None) -> None:
        related: tuple[Related, ...] = ()
        if previous is not None and previous.start_position is not None:
            if previous.is_par():
                reason = say("line-break-without-line.related.blank-line")
            elif isinstance(previous, TexGroup):
                reason = say(
                    "line-break-without-line.related.environment",
                    closing=f"\\end{{{previous.name}}}",
                )
            else:
                reason = say("line-break-without-line.related.none")
            end = previous.end_position or previous.start_position
            start = (
                previous.inner_end
                if isinstance(previous, TexGroup) and previous.inner_end
                else previous.start_position
            )
            related = (Related(start, end, reason),)
        self._add(
            "line-break-without-line",
            say("line-break-without-line"),
            command,
            related,
            say("line-break-without-line.fix"),
        )

    def _add(
        self,
        code: str,
        message: str,
        node: TexContainer,
        related: tuple[Related, ...] = (),
        suggestion: str | None = None,
    ) -> None:
        assert node.start_position is not None and node.end_position is not None
        self._found.append(
            TexDiagnostic(
                code, Severity.ERROR, message, node.start_position, node.end_position, related, suggestion
            )
        )


def _argument_text(argument: TexContainer | None) -> str | None:
    if argument is None:
        return None
    if isinstance(argument, TexGroup) and argument.bracket:
        return argument.arg().strip()
    return argument.content if argument.is_pure_text() else None


def _closest(name: str, known: frozenset[str] | set[str]) -> str | None:
    """A known name of the same length, one letter changed or two swapped, from `name`.

    Not a mere difference of case: `\\rvert` and `\\rVert`, `\\varTheta` and
    `\\vartheta` are two commands.
    """
    candidates = sorted(
        candidate
        for candidate in known
        if len(candidate) == len(name) and candidate.lower() != name.lower() and similar(name, candidate, 1)
    )
    return candidates[0] if candidates else None


def _span(node: TexContainer) -> tuple[Position, Position]:
    assert node.start_position is not None and node.end_position is not None
    return node.start_position, node.end_position


def _dissolved_opening(node: TexContainer) -> bool:
    """The opening token of a dissolved group: `\\begin` (outside a `TexCommand`), `{`, `$`, `\\[`."""
    if isinstance(node, TexCommand) or not isinstance(node, TexContent):
        return False
    return node.content in DISSOLVED_COMMANDS if node.command else node.content in DISSOLVED_TEXTS


def _typesets(command: TexCommand) -> bool:
    """Are its arguments typeset where they are written?"""
    name = command.base_name
    return not (
        name in DEFINING_COMMANDS
        or name in UNTYPESET_ARGUMENTS
        or name in NAME_ARGUMENTS
        or command.macro is not None  # an unexpanded macro: we do not know where the argument goes
    )


def _unknown(node: TexCommand) -> bool:
    return node.signature is None and node.macro is None and node.role is None


def _math_columns(table: TexGroup) -> bool:
    """Can the columns of a table be in math: `>{$}c<{$}`, or a type defined elsewhere?"""
    if table.name in KEYVAL_TABLES:
        return True  # `column{2-4}={mode=math}`: a key-value specification, which we do not read
    for argument in table.arguments:
        if argument is None:
            continue
        # Outside the braces (widths, separators), only the kernel letters are sure.
        spec = BRACED.sub("", str(argument)[1:-1] if argument.is_bracket_group() else str(argument))
        if MATH_COLUMNS.search(str(argument)) or any(c.isalpha() and c not in "lcrpmb" for c in spec):
            return True
    return False


def _vertical(previous: TexContainer | None) -> bool:
    """What comes before leaves TeX between two paragraphs: blank line, list or float closed, title."""
    if previous is None:
        return False
    if previous.is_par():
        return True
    if isinstance(previous, TexGroup):
        return previous.env and previous.name in VERTICAL_AFTER
    return False


def _opening(group: TexGroup, message: str) -> tuple[Related, ...]:
    if group.start_position is None:
        return ()
    return (Related(group.start_position, group.inner_start or group.start_position, message),)


def _span_related(node: TexContainer, message: str) -> tuple[Related, ...]:
    if node.start_position is None or node.end_position is None:
        return ()
    return (Related(node.start_position, node.end_position, message),)
