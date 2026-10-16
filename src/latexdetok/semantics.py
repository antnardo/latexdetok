"""Meaning: what is properly matched but cannot be typeset where it is written.

Structure says what opens and closes (see `diagnostics`); meaning says whether
what we read belongs there: an `\\item` outside a list, an `&` outside an
alignment, a `^` or an `\\alpha` outside math, two subscripts on one symbol, a
mandatory argument that is missing, a `\\left` without its `\\right`, a `\\\\`
with no line to end. TeX refuses them with messages about its own machinery
(“Missing $ inserted”, “Misplaced alignment tab character &”, “Double
subscript”, “There's no line here to end”); here, we name what is written, and
where.

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
from collections.abc import Callable
from dataclasses import dataclass, replace
from functools import cache
from typing import Protocol

from latexdetok.analyse import TexFile
from latexdetok.classes import (
    Position,
    TexBranch,
    TexCommand,
    TexComment,
    TexContainer,
    TexContent,
    TexGroup,
    TexVerbatim,
)
from latexdetok.conditions import MATH_ARGUMENT_COMMANDS, MATH_ENVIRONMENTS, TEXT_ARGUMENT_COMMANDS, setter
from latexdetok.definitions import (
    DEF,
    DEFINING_COMMANDS,
    DOCUMENT_COMMAND,
    DOCUMENT_ENVIRONMENT,
    NEWCOMMAND,
    NEWENVIRONMENT,
)
from latexdetok.diagnostics import Related, Severity, TexDiagnostic, similar
from latexdetok.messages import say
from latexdetok.parser import UNTYPESET_ARGUMENTS, unknown_command_before
from latexdetok.refusals import (
    AMSMATH_ROWS,
    ARRAY_PACKAGES,
    BOX_COMMANDS,
    BOX_ENVIRONMENTS,
    CAPTION_PLACES,
    CLASS_ENVIRONMENTS,
    COUNTED_LISTS,
    DELIMITED_COMMANDS,
    DELIMITER_CHARACTERS,
    DISPLAY_ENVIRONMENTS,
    FIXED_COLUMNS,
    FLOATS,
    FRACTIONS,
    INNER_MATH_ENVIRONMENTS,
    KERNEL_ENVIRONMENTS,
    KERNEL_OPERATORS,
    LIST_DEPTH,
    LIST_LIMITS,
    MATRICES,
    NUMBERED_ROWS,
    PACKAGE_COLUMNS,
    PLAIN_PACKAGES,
    PREAMBLE_ONLY,
    RULES,
    SHORT_ARGUMENTS,
    SINGLE_NUMBER,
    STANDARD_CLASSES,
    TABLES,
    TAG_REFUSED,
    TEXT_COMMANDS,
    columns,
    is_length,
    parameters,
    xparse_count,
)
from latexdetok.signatures import DATA, Mode, SignatureRegistry

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
    "double-subscript": "two subscripts on the same symbol (`x_a_b`)",
    "double-superscript": "two superscripts on the same symbol (`x^a^b`, `f^2'`)",
    "script-without-argument": "“^” or “_” with nothing to take (`x^` at the end, `x__b`)",
    "missing-delimiter": "“\\left”, “\\right”, “\\big”… followed by what is no delimiter (`\\left{`)",
    "middle-without-left": "“\\middle” without “\\left” in the same group",
    "limits-without-operator": "“\\limits” or “\\nolimits” after what is no operator",
    "ambiguous-fraction": "two “\\over” (or “\\atop”, “\\choose”…) in the same group",
    "too-many-columns": "more cells in a row than the table or the environment has columns",
    "display-in-math": "a display environment (`align`, `equation`…) inside math",
    "blank-line-in-math": "a blank line inside a display environment",
    "multiple-labels": "two “\\label” on the same equation",
    "multiple-tags": "two “\\tag” on the same equation",
    "tag-not-allowed": "“\\tag” where nothing is numbered (inline math, `aligned`…)",
    "invalid-length": "a length that is not one (`\\\\[0,1]`, `\\hspace{2}`)",
    "misplaced-rule": "“\\hline” or “\\cline” in the middle of a row",
    "misplaced-multicolumn": "“\\multicolumn” after the start of its cell",
    "unknown-column": "a letter that is no column type, in the specification of a table",
    "text-before-item": "text before the first “\\item” of a list",
    "too-deeply-nested": "lists nested deeper than LaTeX allows",
    "preamble-only": "a command of the preamble after “\\begin{document}”",
    "text-in-preamble": "text before “\\begin{document}”",
    "illegal-parameter": "“#n” beyond the arguments of a definition",
    "already-defined": "“\\newcommand” or “\\newenvironment” of a name already defined",
    "not-a-command-name": "a command defined without its backslash (`\\newcommand{foo}`)",
    "verb-in-argument": "“\\verb” inside the argument of a command",
    "float-in-box": "a float inside a box (`minipage`, a cell, `\\fbox`)",
    "caption-outside-float": "“\\caption” outside a float",
    "blank-line-in-argument": "a blank line inside an argument that cannot hold one",
    "math-command-in-text": "a command or an environment that only means something in math, written in text",
    "invalid-in-math": "a command LaTeX refuses in math mode",
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
        "eqref",
        "vref",
        "Vref",
        "vpageref",
        "bibitem",
        "citep",
        "citet",
        "citealt",
        "citealp",
        "citenum",
        "citeauthor",
        "citeyear",
        "Citet",
        "Citep",
        "Citeauthor",
        "parencite",
        "Parencite",
        "textcite",
        "Textcite",
        "autocite",
        "Autocite",
        "smartcite",
        "footcite",
        "footcitetext",
        "fullcite",
        "citetitle",
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
# What LaTeX guards with `\@inmatherr` in latex.ltx: an error, not a matter of taste.
# `\end{x}` is guarded there too, but a math that swallows an `\end` never closes,
# and `unclosed-math` says so first.
INVALID_IN_MATH = frozenset({"item", "circle"})
# What leaves the nucleus as it is: `\limits` changes the atom already there, `\relax`
# does nothing, and TeX skips it while looking for the argument of a script.
SCRIPT_TRANSPARENT = frozenset({"limits", "nolimits", "displaylimits", "relax"})
# The kernel's other names for `_` and `^` (`\let\sb=_`).
SCRIPT_COMMANDS = {"sb": "_", "sp": "^"}
# Written as several atoms, by fontmath.ltx and amsmath (`\neq` is `\not=`): a script
# takes the first one only, and the last one is a new nucleus.
COMPOSITE_SYMBOLS = frozenset(
    {
        "neq",
        "ne",
        "mapsto",
        "longmapsto",
        "hookrightarrow",
        "hookleftarrow",
        "longrightarrow",
        "longleftarrow",
        "longleftrightarrow",
        "Longrightarrow",
        "Longleftarrow",
        "Longleftrightarrow",
        "bowtie",
        "models",
        "iff",
        "implies",
        "impliedby",
    }
)
# Tables whose columns are set by key-value pairs (tabularray, nicematrix).
KEYVAL_TABLES = frozenset({"tblr", "longtblr", "talltblr", "NiceTabular"})
BRACED = re.compile(r"\{[^{}]*\}")
# Text that TeX would typeset: a word, not `>0` left by a test.
WORD = re.compile(r"[^\W\d_]{3,}")
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
    # The math open here, to point at its opening: outside math there is none to point at.
    math: TexGroup | None = None
    # In a float, where `\caption` belongs.
    floating: bool | None = False
    # In the outer paragraph mode, where a float may open; when False, `box` is what boxes it.
    outer: bool | None = True
    box: TexContainer | None = None
    # How deep the lists are: itemize, enumerate, and every list built on `\list`.
    depths: tuple[int, int, int] = (0, 0, 0)
    # The command whose argument this is: no `\verb` there.
    argument_of: TexCommand | None = None
    # After `\begin{document}`: None in a fragment, which may be included anywhere.
    body: bool | None = None

    def __post_init__(self) -> None:
        if self.mode is not Mode.MATH and self.math is not None:
            object.__setattr__(self, "math", None)


@dataclass(slots=True)
class _Nucleus:
    """The scripts the current nucleus carries: where each one is written, and by what."""

    sub: tuple[Position, Position, str] | None = None
    sup: tuple[Position, Position, str] | None = None


class _MathList:
    """One math list read the way TeX builds it: nuclei, their scripts, its fractions.

    Scripts. TeX hangs one `_` and one `^` on a nucleus, and refuses a second one
    (“Double subscript”) on the atom that already has one. Whatever it makes an
    atom of starts a new nucleus: a character, a group even empty (`x_a{}_b`), a
    command (`x_a\\,_b`). Blanks and comments do not, nor `\\limits` or `\\relax`.
    A script takes the next atom as its argument, which is no new nucleus; with
    nothing to take — the end of the list, another script, an `&` — it is
    “Missing { inserted”. A prime is a superscript (`f'` is `f^{\\prime}`) that
    takes in a `^` written right after it (`f'^2`), but not one after a blank:
    `f' ^2` is refused.

    The rest of the list. `\\limits` after an atom that is no operator is
    “Limit controls must follow a math operator”; a second `\\over` in the same
    list, “Ambiguous; you need another { and }”. A cell of an alignment is a list
    of its own: `&` and `\\\\` start another one.

    What cannot be followed starts a new nucleus as well: an unknown command
    given as an argument, which may write several atoms, a branch of a
    conditional, a character whose category the document changes. That can make
    an error go unseen, never report one TeX does not make.
    """

    def __init__(
        self,
        lines: list[str],
        changed: set[str],
        commands: dict[str, str],
        ordinary: Callable[[str], bool],
        found: list[TexDiagnostic],
    ) -> None:
        self._lines = lines
        self._changed = changed
        self._commands = commands
        self._ordinary = ordinary
        self._found = found
        self._nucleus = _Nucleus()
        # The script waiting for its argument — `_`, `^`, or "'" for the one a prime takes
        # in — and where it is written.
        self._pending: str | None = None
        self._pending_at: tuple[Position, Position, str] | None = None
        # Right after a prime: a `^` now joins it.
        self._primes = False
        # The last atom, for `\limits`: "start" of the list, "char", "group", "ordinary"
        # (a symbol of the kernel), None for anything else; and how it is written.
        self._last: str | None = "start"
        self._last_written = ""
        # The first `\over` of the list.
        self._fraction: TexCommand | None = None

    def read(self, group: TexGroup) -> None:
        # The arguments of an environment (`{cc}` of an array) are specifications, not atoms.
        bound = {id(node) for node in group.arguments if node is not None}
        for node in group.content:
            if id(node) in bound or isinstance(node, TexComment):
                continue
            if isinstance(node, TexCommand):
                # Its arguments are part of the atom it makes.
                bound.update(id(argument) for argument in _consumed(node))
                self._command(node)
            elif isinstance(node, TexBranch) or _dissolved_opening(node):
                self._new()
            elif isinstance(node, TexGroup):
                # A group in braces is one atom; TeX reads `[` as a character, and takes only it.
                self._atom(single=node.bracket, last="group" if node.bracket else None, written="{…}")
            elif node.is_pure_text() and not node.is_par():
                self._characters(node)
            else:
                self._new()
        if not isinstance(group, TexBranch):
            # A branch is read on its own, but flows into the list around it.
            self._missing()

    def _new(self) -> None:
        """A nucleus with no script yet, after something we cannot follow."""
        self._nucleus = _Nucleus()
        self._pending = None
        self._pending_at = None
        self._primes = False
        self._last = None

    def _cell(self) -> None:
        """`&` or `\\\\`: a new list begins."""
        self._missing()
        self._new()
        self._last = "start"
        self._last_written = ""
        self._fraction = None

    def _atom(self, single: bool, last: str | None = None, written: str = "") -> None:
        """The argument of the script waiting for one, or else a new nucleus.

        `single`: certainly one atom. Otherwise the script takes the first one, and
        the last one is a new nucleus, of which we know nothing.
        """
        self._primes = False
        if self._pending is not None and single:
            self._pending = None
            self._pending_at = None
            return
        self._new()
        if self._pending is None:
            self._last = last
            self._last_written = written

    def _command(self, command: TexCommand) -> None:
        assert command.start_position is not None and command.end_position is not None
        name = command.base_name
        if name == "\\":
            self._cell()
        elif name in self._commands and command.macro is None:
            self._script(self._commands[name], command.start_position, command.end_position, f"\\{name}")
        elif name in SCRIPT_TRANSPARENT:
            self._primes = False
            if self._pending is not None:
                if name != "relax":
                    self._new()  # `x_\limits`: no argument, another error
            elif name != "relax" and self._last is not None:
                self._limits(command)
        elif name in FRACTIONS and command.macro is None:
            self._over(command)
        else:
            known = command.signature is not None and command.macro is None
            last = "ordinary" if known and self._ordinary(name) else None
            self._atom(known and name not in COMPOSITE_SYMBOLS, last, f"\\{name}")

    def _characters(self, node: TexContent) -> None:
        assert node.start_position is not None and node.end_position is not None
        (line, start), (_, end) = node.start_position, node.end_position
        raw = self._lines[line - 1][start:end] if 1 <= line <= len(self._lines) else ""
        for offset, character in enumerate(raw):
            where, after = (line, start + offset), (line, start + offset + 1)
            if character in self._changed:
                self._new()  # its meaning is no longer sure
            elif character in " \t":
                self._primes = False  # TeX reads the blank after a prime
            elif character in "_^":
                self._script(character, where, after, character)
            elif character == "'":
                self._prime(where, after)
            elif character == "&":
                self._cell()
            elif character == "~":
                self._new()  # a space
            else:
                self._atom(single=True, last="char", written=character)

    def _script(self, kind: str, start: Position, end: Position, written: str) -> None:
        if self._pending == "'":
            self._new()  # `f'^_`: what the prime's macro reads then is not ours to say
        elif self._pending is not None:
            self._missing()  # `x__b`: the first one has nothing to take
            self._new()
        if self._primes and kind == "^":
            self._primes = False
            self._pending = "'"  # `f'^2`: the prime takes the superscript in
            return
        self._primes = False
        self._hang(kind, start, end, written)
        self._pending = kind
        self._pending_at = (start, end, written)

    def _prime(self, start: Position, end: Position) -> None:
        if self._pending is not None:
            self._new()  # `x^'`: not a superscript of its own
        elif not self._primes:  # `f''`: one superscript, the primes in a row
            self._hang("^", start, end, "'")
            self._primes = True

    def _hang(self, kind: str, start: Position, end: Position, written: str) -> None:
        """Hang a script on the nucleus, or report the one already there."""
        first = self._nucleus.sub if kind == "_" else self._nucleus.sup
        if first is not None:
            self._double(kind, first, start, end, written)
            self._nucleus = _Nucleus()  # TeX gives the script an empty nucleus of its own
        if kind == "_":
            self._nucleus.sub = (start, end, written)
        else:
            self._nucleus.sup = (start, end, written)

    def _double(
        self, kind: str, first: tuple[Position, Position, str], start: Position, end: Position, written: str
    ) -> None:
        code = "double-subscript" if kind == "_" else "double-superscript"
        prime = "'" in (written, first[2])
        self._found.append(
            TexDiagnostic(
                code,
                Severity.ERROR,
                say(code),
                start,
                end,
                (Related(first[0], first[1], say(f"{code}.related")),),
                say(f"{code}.fix" + (".prime" if prime else "")),
            )
        )

    def _missing(self) -> None:
        """The script waiting has nothing to take: “Missing { inserted”."""
        if self._pending in ("_", "^") and self._pending_at is not None:
            start, end, written = self._pending_at
            self._found.append(
                TexDiagnostic(
                    "script-without-argument",
                    Severity.ERROR,
                    say("script-without-argument", script=written),
                    start,
                    end,
                    suggestion=say("script-without-argument.fix", script=written),
                )
            )
        self._pending = None
        self._pending_at = None

    def _limits(self, command: TexCommand) -> None:
        if self._last not in ("start", "char", "group", "ordinary"):
            return
        assert command.start_position is not None and command.end_position is not None
        name = command.base_name
        if self._last == "start":
            message = say("limits-without-operator.start", command=name)
        else:
            message = say("limits-without-operator", command=name, atom=self._last_written)
        self._found.append(
            TexDiagnostic(
                "limits-without-operator",
                Severity.ERROR,
                message,
                command.start_position,
                command.end_position,
                suggestion=say("limits-without-operator.fix", command=name),
            )
        )

    def _over(self, command: TexCommand) -> None:
        """A generalised fraction: the list becomes its numerator, and a second one is ambiguous."""
        assert command.start_position is not None and command.end_position is not None
        first = self._fraction
        if first is None:
            self._fraction = command
        else:
            self._found.append(
                TexDiagnostic(
                    "ambiguous-fraction",
                    Severity.ERROR,
                    say("ambiguous-fraction", command=command.base_name, first=first.base_name),
                    command.start_position,
                    command.end_position,
                    _span_related(first, say("ambiguous-fraction.related")),
                    say("ambiguous-fraction.fix"),
                )
            )
        self._new()
        self._last = "start"
        self._last_written = ""


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
        # `\sb` and `\sp`, unless the document gave them another meaning.
        self._script_commands = {
            name: character
            for name, character in SCRIPT_COMMANDS.items()
            if self._registry.command(name) == self._kernel.command(name)
        }
        # What the document loads, and what it changes that some rules depend on.
        self._class, self._packages = _loaded(tex.container)
        source = "".join(tex.lines)
        self._amsmath = bool(self._packages & {"amsmath", "mathtools", "empheq"}) or (
            self._class is not None and self._class.startswith("ams")
        )
        # Packages we know all about: beyond them, a column type or a list depth may come from elsewhere.
        self._plain = (
            self._class in STANDARD_CLASSES
            and self._packages <= PLAIN_PACKAGES
            and not _reads_more(tex.container)
        )
        self._column_types = self._plain and "newcolumntype" not in source
        self._lists_limited = self._plain and not re.search(r"\\(?:setlistdepth|renewlist|newlist)\b", source)
        self._matrix_columns = "MaxMatrixCols" not in source
        self._captions_anywhere = bool(re.search(r"\\captionsetup\s*\{[^}]*type\s*=", source))
        # Names the document has defined already, at the top of a file, for `\newcommand`.
        self._defined: dict[str, TexCommand] = {}
        self._preamble = not tex.fragment and any(
            isinstance(node, TexGroup) and node.env and node.name == "document"
            for node in tex.container.content
        )

    def read(self) -> list[TexDiagnostic]:
        unknown = self._tex.fragment
        root = _Frame(
            Mode.TEXT,
            None if unknown else False,
            None if unknown else False,
            trusted=True,
            floating=None if unknown else False,
            outer=None if unknown else True,
            body=None if unknown else False,
        )
        self._group(self._tex.container, root)
        self._typos()
        return self._found

    # The walk

    def _group(self, group: TexGroup, frame: _Frame, lefts: _Lefts | None = None) -> None:
        if frame.trusted and frame.mode is Mode.MATH:
            _MathList(
                self._tex.lines, self._changed, self._script_commands, self._ordinary, self._found
            ).read(group)
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
                if frame.lists is False and _unknown(node) and not _control_symbol(node):
                    # An unknown command may have opened a list (`\vrbitem`): we no longer know.
                    frame = replace(frame, lists=None)
            elif isinstance(node, TexGroup):
                inner = self._inner(node, frame, bound.get(id(node)), content, index)
                if node.env and not node.math and frame.trusted:
                    self._opening_environment(node, frame, inner)
                if node.env and node.name == "document":
                    self._preamble = False
                self._group(node, inner)
            elif _dissolved_opening(node):
                # An opening never closed, already reported: what follows is no longer in its place
                # in the tree (the `\item` of a dissolved `itemize`), we no longer know where we are.
                frame = replace(frame, mode=None, lists=None, alignment=None, floating=None, outer=None)
            elif isinstance(node, TexVerbatim):
                if frame.trusted and node.name.rstrip("*") == "verb" and frame.argument_of is not None:
                    self._verb(node, frame.argument_of)
            elif node.is_par():
                if frame.trusted and frame.math is not None and frame.math.env and not frame.math.math:
                    self._blank_in_math(node, frame.math)
            elif node.is_pure_text() and frame.trusted:
                if (
                    self._preamble
                    and group is self._tex.container
                    and id(node) not in bound
                    and WORD.search(node.content)
                    and unknown_command_before(content, index) is None
                ):
                    self._preamble_text(node)
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
            return replace(frame, mode=Mode.MATH, alignment=False, math=group)
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
            return replace(
                frame,
                mode=mode,
                lists=None,
                alignment=None,
                trusted=frame.trusted and name not in CODE_ENVIRONMENTS,
                environment=group,
                math=None,
                floating=None,
                outer=None,
            )
        mode = Mode.MATH if name in MATH_ENVIRONMENTS or name == "array" else Mode.TEXT
        if mode is Mode.TEXT and name in ALIGNMENT_ENVIRONMENTS and _math_columns(group):
            mode = None  # `>{$}c<{$}`, or a column type defined elsewhere: cells in math
        lists = True if name in LIST_ENVIRONMENTS else False if name == "document" else frame.lists
        boxed = name in BOX_ENVIRONMENTS
        return replace(
            frame,
            mode=mode,
            lists=lists,
            alignment=name in ALIGNMENT_ENVIRONMENTS,
            environment=group,
            math=group,
            floating=True if name in CAPTION_PLACES else frame.floating,
            outer=False if boxed else frame.outer,
            box=group if boxed else frame.box,
            depths=_deeper(frame.depths, name),
            body=True if name == "document" else frame.body,
        )

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
        boxed = name in BOX_COMMANDS
        return replace(
            frame,
            mode=mode,
            argument_of=command,
            outer=False if boxed else frame.outer,
            box=command if boxed else frame.box,
        )

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
        if name in INVALID_IN_MATH and frame.mode is Mode.MATH and frame.trusted:
            self._invalid_in_math(command, frame)
        elif name == "item" and frame.mode is Mode.TEXT and frame.lists is False:
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
        # `\left{`: a brace is no delimiter, and what it opens is no argument.
        brace = frame.mode is not Mode.TEXT and name in DELIMITED_COMMANDS and self._delimiter(command)
        if (
            command.arguments is not None
            and name not in DEFINING_COMMANDS
            and command.missing_arguments()
            and not brace
        ):
            # The arguments of a definition are tokens, which may be a closing brace.
            self._missing(command, group, index)
        if frame.mode is Mode.MATH:
            if name == "left" and not brace:
                lefts.open.append(command)
            elif name == "right":
                if lefts.open:
                    lefts.open.pop()
                elif lefts.excused:
                    lefts.excused -= 1
                elif not brace:
                    self._add(
                        "right-without-left",
                        say("right-without-left"),
                        command,
                        suggestion=say("right-without-left.fix"),
                    )
            elif name == "middle" and not lefts.open and not lefts.excused:
                self._add(
                    "middle-without-left",
                    say("middle-without-left"),
                    command,
                    suggestion=say("middle-without-left.fix"),
                )
            elif name == "tag" and _numbers_nothing(frame.math):
                assert frame.math is not None
                place = frame.math.enclosures()[0]
                self._add(
                    "tag-not-allowed",
                    say("tag-not-allowed", place=place),
                    command,
                    suggestion=say("tag-not-allowed.fix"),
                )
        if name in PREAMBLE_ONLY and frame.body is True:
            self._add(
                "preamble-only",
                say("preamble-only", command=name),
                command,
                suggestion=say("preamble-only.fix"),
            )
        if name == "caption" and frame.floating is False and not self._captions_anywhere:
            self._add(
                "caption-outside-float",
                say("caption-outside-float"),
                command,
                suggestion=say("caption-outside-float.fix"),
            )
        if name in ("\\", "hspace", "vspace", "rule"):
            self._lengths(command, frame)
        if name in SHORT_ARGUMENTS:
            self._short(command)
        if name in NEWCOMMAND | NEWENVIRONMENT | DOCUMENT_COMMAND | DOCUMENT_ENVIRONMENT | DEF:
            self._definition(command, group)
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

    def _invalid_in_math(self, command: TexCommand, frame: _Frame) -> None:
        """What LaTeX itself refuses there — and what put it there is usually a `$` too many."""
        related: tuple[Related, ...] = ()
        if frame.math is not None:
            opening = frame.math.enclosures()[0]
            related = _opening(frame.math, say("invalid-in-math.related", opening=opening))
        self._add(
            "invalid-in-math",
            say("invalid-in-math", command=f"\\{command.base_name}"),
            command,
            related,
            say("invalid-in-math.fix"),
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

    # What LaTeX refuses where it is written (0.7)

    def _ordinary(self, name: str) -> bool:
        """A symbol of the kernel that is no operator: `\\limits` after it is refused."""
        harvested = _harvested().command(name)
        return (
            harvested is not None
            and harvested.mode is Mode.MATH
            and name not in KERNEL_OPERATORS
            and self._registry.command(name) == self._kernel.command(name)
        )

    def _opening_environment(self, group: TexGroup, frame: _Frame, inner: _Frame) -> None:
        """What an environment refuses as soon as it opens, and what it holds."""
        name = group.name
        if name in DISPLAY_ENVIRONMENTS and frame.mode is Mode.MATH:
            related: tuple[Related, ...] = ()
            if frame.math is not None:
                related = _opening(
                    frame.math, say("display-in-math.related", opening=frame.math.enclosures()[0])
                )
            self._add_span(
                "display-in-math",
                say("display-in-math", name=name),
                _opening_span(group),
                related,
                say("display-in-math.fix"),
            )
        elif name in INNER_MATH_ENVIRONMENTS and frame.mode is Mode.TEXT:
            self._add_span(
                "math-command-in-text",
                say("math-command-in-text", command=f"\\begin{{{name}}}"),
                _opening_span(group),
                suggestion=say(
                    "math-command-in-text.fix", math=f"\\[ \\begin{{{name}}} … \\end{{{name}}} \\]"
                ),
            )
        if name in FLOATS and frame.outer is False and frame.box is not None and not _here(group):
            box = frame.box
            written = f"\\begin{{{box.name}}}" if isinstance(box, TexGroup) else f"\\{box.content}"
            self._add_span(
                "float-in-box",
                say("float-in-box", name=name, box=written),
                _opening_span(group),
                _span_related(box, say("float-in-box.related", box=written)),
                say("float-in-box.fix"),
            )
        if self._lists_limited and name in COUNTED_LISTS:
            self._depth(group, inner.depths)
        if name in ("itemize", "enumerate", "description"):
            self._before_first_item(group)
        if name in SINGLE_NUMBER or name in NUMBERED_ROWS:
            self._numbering(group, name)
        columns = self._columns(group)
        if columns is not None or name in TABLES:
            self._cells(group, columns)

    def _depth(self, group: TexGroup, depths: tuple[int, int, int]) -> None:
        name = group.name
        itemize, enumerate_, total = depths
        depth = {"itemize": itemize, "enumerate": enumerate_}.get(name, total)
        limit = LIST_LIMITS.get(name, LIST_DEPTH)
        if depth <= limit and total <= LIST_DEPTH:
            return
        if depth <= limit:
            depth, limit = total, LIST_DEPTH
        self._add_span(
            "too-deeply-nested",
            say("too-deeply-nested", name=name, depth=depth, limit=limit),
            _opening_span(group),
            suggestion=say("too-deeply-nested.fix"),
        )

    def _before_first_item(self, group: TexGroup) -> None:
        """Text before the first `\\item`: “Something's wrong--perhaps a missing \\item”."""
        bound = {id(node) for node in group.arguments if node is not None}
        for node in group.content:
            if id(node) in bound or isinstance(node, TexComment) or node.is_par():
                continue
            if isinstance(node, TexBranch):
                return
            if isinstance(node, TexCommand):
                bound.update(id(argument) for argument in _consumed(node))
                if node.base_name == "item" or node.macro is not None or node.signature is None:
                    return  # an unknown command, or a macro left as is, may well be an `\item`
                if node.base_name in TEXT_COMMANDS:
                    self._before_item(group, _span(node))
                    return
            elif isinstance(node, TexGroup):
                if node.env:
                    self._before_item(group, _opening_span(node))
                    return
            elif node.is_pure_text() and node.content.strip():
                self._before_item(group, _span(node))
                return

    def _before_item(self, group: TexGroup, span: tuple[Position, Position]) -> None:
        self._add_span(
            "text-before-item",
            say("text-before-item", name=group.name),
            span,
            suggestion=say("text-before-item.fix", name=group.name),
        )

    def _numbering(self, group: TexGroup, name: str) -> None:
        """One `\\label` and one `\\tag` per numbered equation: per row, in an `align`."""
        rows = name in NUMBERED_ROWS
        labels = not name.endswith("*") and (name != "equation" or self._amsmath)
        first: dict[str, TexCommand] = {}

        def visit(container: TexGroup, top: bool) -> None:
            for node in container.content:
                if isinstance(node, TexBranch):
                    if node.taken:
                        visit(node, top)
                elif isinstance(node, TexCommand):
                    kind = node.base_name
                    if kind == "\\" and top and rows:
                        first.clear()
                    elif kind == "tag" or (kind == "label" and labels):
                        if kind in first:
                            code = "multiple-tags" if kind == "tag" else "multiple-labels"
                            self._add(
                                code,
                                say(code),
                                node,
                                _span_related(first[kind], say(f"{code}.related")),
                                say(f"{code}.fix"),
                            )
                        else:
                            first[kind] = node
                elif isinstance(node, TexGroup) and not node.math and node.name not in DISPLAY_ENVIRONMENTS:
                    visit(node, False)

        visit(group, True)

    def _columns(self, group: TexGroup) -> int | None:
        """The columns an alignment has; None when it cannot be known, or has no limit."""
        name = group.name
        if name in FIXED_COLUMNS:
            return FIXED_COLUMNS[name]
        if name in MATRICES:
            return 10 if self._matrix_columns else None
        arguments = [argument for argument in group.arguments if argument is not None]
        if name in ("alignat", "alignat*", "alignedat"):
            text = _group_text(arguments[0]) if arguments else None
            return 2 * int(text) if text is not None and text.isdigit() else None
        if name not in TABLES or not arguments or not arguments[-1].is_bracket_group():
            return None
        spec = arguments[-1]
        assert isinstance(spec, TexGroup)
        extra: dict[str, int] = {"X": 0} if name == "tabularx" else {}
        for package, letters in PACKAGE_COLUMNS.items():
            if package in self._packages:
                extra.update(letters)
        text = spec.arg()
        read = columns(text, extra, array=bool(self._packages & ARRAY_PACKAGES))
        if self._column_types and read.unknown:
            self._unknown_column(spec, read.unknown)
        return read.count

    def _unknown_column(self, spec: TexGroup, unknown: tuple[tuple[int, str], ...]) -> None:
        line, column = spec.inner_start or spec.start_position or (0, 0)
        for offset, letter in unknown:
            where = (
                (line, column + offset)
                if spec.start_position
                and spec.end_position
                and spec.start_position[0] == spec.end_position[0]
                else _span(spec)[0]
            )
            self._found.append(
                TexDiagnostic(
                    "unknown-column",
                    Severity.ERROR,
                    say("unknown-column", letter=letter),
                    where,
                    (where[0], where[1] + 1),
                    suggestion=say("unknown-column.fix"),
                )
            )

    def _cells(self, group: TexGroup, count: int | None) -> None:
        """The cells of each row against the columns; what may only start a row or a cell."""
        table = group.name in TABLES
        bound = {id(node) for node in group.arguments if node is not None}
        position, span = 0, 1  # the columns before the current cell, the columns it takes
        row_typeset = cell_typeset = False
        followed = True  # nothing in the row we cannot follow
        for node in group.content:
            if id(node) in bound or isinstance(node, TexComment):
                continue
            if isinstance(node, TexBranch):
                followed = False
            elif isinstance(node, TexCommand):
                bound.update(id(argument) for argument in _consumed(node))
                name = node.base_name
                if name in ("\\", "tabularnewline"):
                    position, span, row_typeset, cell_typeset, followed = 0, 1, False, False, True
                elif table and name in RULES:
                    if row_typeset and followed:
                        self._misplaced("misplaced-rule", node)
                elif table and name == "multicolumn":
                    if cell_typeset and followed:
                        self._misplaced("misplaced-multicolumn", node)
                    columns = _group_text((node.arguments or [None])[0])
                    if columns is not None and columns.strip().isdigit():
                        span = int(columns)
                    else:
                        followed = False
                    row_typeset = cell_typeset = True
                elif node.signature is None and node.macro is None:
                    followed = False  # an unknown command may write a cell, or nothing
                else:
                    row_typeset = cell_typeset = True
            elif isinstance(node, TexGroup):
                row_typeset = cell_typeset = True
            elif node.is_pure_text() and not node.is_par():
                assert node.start_position is not None and node.end_position is not None
                (line, start), (_, end) = node.start_position, node.end_position
                raw = self._tex.lines[line - 1][start:end] if 1 <= line <= len(self._tex.lines) else ""
                for offset, character in enumerate(raw):
                    if character == "&" and "&" not in self._changed:
                        position += span
                        span, cell_typeset = 1, False
                        if count is not None and followed and position >= count:
                            self._too_many((line, start + offset), group, count)
                            position, row_typeset = 0, False  # TeX ends the row there
                    elif not character.isspace():
                        row_typeset = cell_typeset = True

    def _too_many(self, where: Position, group: TexGroup, count: int) -> None:
        name = group.name
        fix = "too-many-columns.fix.matrix" if name in MATRICES else "too-many-columns.fix"
        self._found.append(
            TexDiagnostic(
                "too-many-columns",
                Severity.ERROR,
                say("too-many-columns", count=count, name=name),
                where,
                (where[0], where[1] + 1),
                _opening(group, say("too-many-columns.related", name=name)),
                say(fix, needed=count + 1),
            )
        )

    def _misplaced(self, code: str, command: TexCommand) -> None:
        self._add(code, say(code, command=command.base_name), command, suggestion=say(f"{code}.fix"))

    def _delimiter(self, command: TexCommand) -> bool:
        """After `\\left`, `\\big`…: a delimiter, or “Missing delimiter”. True for a brace."""
        assert command.end_position is not None
        line, column = command.end_position
        if command.bare or not 1 <= line <= len(self._tex.lines):
            return False  # `{\big}`: a size passed to another command (`\pare[\big]`), not used
        rest = self._tex.lines[line - 1][column:]
        stripped = rest.lstrip(" \t")
        if not stripped or stripped[0] in "\r\n\\%" or stripped[0] in DELIMITER_CHARACTERS:
            return False  # a command, the end of the line: we do not judge
        written = stripped[0]
        if written in self._changed:
            return False
        brace = written in "{}"
        assert command.start_position is not None
        end = (line, column + len(rest) - len(stripped) + 1)
        name = command.base_name
        fix = (
            say("missing-delimiter.fix.brace", command=name, written=written)
            if brace
            else say("missing-delimiter.fix", command=name)
        )
        self._add_span(
            "missing-delimiter",
            say("missing-delimiter", command=name, written=written),
            (command.start_position, end),
            suggestion=fix,
        )
        return brace

    def _lengths(self, command: TexCommand, frame: _Frame) -> None:
        """What must be a length: the spacing of `\\\\`, `\\hspace`, `\\vspace`, `\\rule`."""
        name = command.base_name
        for argument in command.arguments or ():
            if not isinstance(argument, TexGroup):
                continue
            if name == "\\":
                if not argument.option:
                    continue
                environment = frame.environment
                if (
                    environment is not None
                    and environment.name in AMSMATH_ROWS
                    and argument.start_position != command.end_position
                ):
                    continue  # amsmath takes `[` only right after `\\`: `\\ [0,1]` is a bracket
            elif name in ("hspace", "vspace") and not argument.bracket:
                continue
            text = argument.arg()
            if is_length(text) is False:
                fix = "invalid-length.fix.row" if name == "\\" else "invalid-length.fix"
                self._add(
                    "invalid-length",
                    say("invalid-length", text=text.strip()),
                    argument,
                    suggestion=say(fix),
                )

    def _short(self, command: TexCommand) -> None:
        """A blank line in an argument that refuses one: “Paragraph ended before … was complete”."""
        for argument in command.arguments or ():
            if not isinstance(argument, TexGroup):
                continue
            blank = _first_blank_line(argument)
            if blank is not None:
                assert blank.start_position is not None
                line = blank.start_position[0]
                self._add_span(
                    "blank-line-in-argument",
                    say("blank-line-in-argument", command=command.base_name),
                    ((line, 0), (line, 0)),
                    _span_related(command, say("blank-line-in-argument.related", command=command.base_name)),
                    say("blank-line-in-argument.fix"),
                )
                return

    def _definition(self, command: TexCommand, group: TexGroup) -> None:
        """A definition LaTeX refuses: no backslash, a name taken, a `#n` it has not got."""
        name = command.base_name
        arguments = command.arguments or []
        target = arguments[0] if arguments else None
        if target is None:
            return
        environment = name in NEWENVIRONMENT or name in DOCUMENT_ENVIRONMENT
        defined: str | None = None
        if isinstance(target, TexCommand):
            defined = target.base_name
        elif isinstance(target, TexGroup):
            text = target.arg().strip()
            if environment:
                defined = text
            elif text.startswith("\\"):
                defined = text[1:]
            elif text and name not in DEF:
                self._add(
                    "not-a-command-name",
                    say("not-a-command-name", text=text),
                    target,
                    suggestion=say("not-a-command-name.fix", text=text),
                )
                return
        if defined and name in (
            "newcommand",
            "newenvironment",
            "NewDocumentCommand",
            "NewDocumentEnvironment",
        ):
            self._taken(command, target, defined, environment)
        if defined and not environment and (group is self._tex.container or group.name == "document"):
            self._defined.setdefault(defined, command)
        self._parameters(command, arguments)

    def _taken(self, command: TexCommand, target: TexContainer, defined: str, environment: bool) -> None:
        related: tuple[Related, ...] = ()
        if environment:
            taken = (
                defined in KERNEL_ENVIRONMENTS
                or (self._class in STANDARD_CLASSES and defined in CLASS_ENVIRONMENTS)
                or _harvested().command(defined) is not None
            )
        else:
            earlier = self._defined.get(defined)
            if earlier is not None and earlier is not command:
                related = _span_related(earlier, say("already-defined.related"))
            taken = (
                earlier is not None
                or _harvested().command(defined) is not None
                or defined.startswith("end")
                or defined == "relax"
            )
        if not taken:
            return
        written = f"{{{defined}}}" if environment else f"\\{defined}"
        self._add(
            "already-defined",
            say("already-defined", name=written),
            target,
            related,
            say("already-defined.fix.environment" if environment else "already-defined.fix"),
        )

    def _parameters(self, command: TexCommand, arguments: list[TexContainer | None]) -> None:
        """`#n` beyond the arguments: “Illegal parameter number in definition”."""
        name = command.base_name
        present = [argument for argument in arguments if argument is not None]
        bodies: list[tuple[TexContainer, int]] = []
        if name in NEWCOMMAND or name in NEWENVIRONMENT:
            count_text = _group_text(arguments[1]) if len(arguments) > 1 else None
            if count_text is not None and not count_text.strip().isdigit():
                return
            count = int(count_text) if count_text is not None else 0
            if name in NEWCOMMAND and len(arguments) >= 4 and arguments[3] is not None:
                bodies = [(arguments[3], count)]
            elif name in NEWENVIRONMENT and len(arguments) >= 5:
                # The end code takes no argument at all.
                bodies = [(body, limit) for body, limit in ((arguments[3], count), (arguments[4], 0)) if body]
        elif name in DOCUMENT_COMMAND or name in DOCUMENT_ENVIRONMENT:
            spec = _group_text(arguments[1]) if len(arguments) > 1 else None
            count_or_none = xparse_count(spec) if spec is not None else None
            if count_or_none is None:
                return
            bodies = [(body, count_or_none) for body in present[2:]]
        elif name in DEF and len(arguments) >= 3 and arguments[2] is not None:
            text = arguments[1].content if arguments[1] is not None and arguments[1].is_pure_text() else ""
            bodies = [(arguments[2], max((digit for _, digit in parameters(text)), default=0))]
        for body, limit in bodies:
            if isinstance(body, TexGroup):
                self._beyond(command, body, limit)

    def _beyond(self, command: TexCommand, body: TexGroup, limit: int) -> None:
        for node in body.content:
            if isinstance(node, TexGroup):
                self._beyond(command, node, limit)
            elif isinstance(node, TexContent) and not isinstance(node, TexCommand) and node.is_pure_text():
                assert node.start_position is not None
                line, start = node.start_position
                raw = self._tex.lines[line - 1][start:] if 1 <= line <= len(self._tex.lines) else ""
                raw = raw[: len(node.content)] if node.end_position and node.end_position[0] == line else raw
                for offset, digit in parameters(raw):
                    if digit > limit:
                        count = say(
                            "illegal-parameter.count."
                            + ("none" if limit == 0 else "one" if limit == 1 else "many"),
                            count=limit,
                        )
                        where = (line, start + offset)
                        self._found.append(
                            TexDiagnostic(
                                "illegal-parameter",
                                Severity.ERROR,
                                say("illegal-parameter", digit=digit, command=command.base_name, count=count),
                                where,
                                (where[0], where[1] + 2),
                                suggestion=say("illegal-parameter.fix", digit=digit),
                            )
                        )

    def _blank_in_math(self, node: TexContainer, math: TexGroup) -> None:
        assert node.start_position is not None
        line = node.start_position[0]
        self._add_span(
            "blank-line-in-math",
            say("blank-line-in-math", name=math.name),
            ((line, 0), (line, 0)),
            _opening(math, say("blank-line-in-math.related", name=math.name)),
            say("blank-line-in-math.fix"),
        )

    def _verb(self, node: TexVerbatim, owner: TexCommand) -> None:
        self._add(
            "verb-in-argument",
            say("verb-in-argument", command=owner.base_name),
            node,
            _span_related(owner, say("verb-in-argument.related", command=owner.base_name)),
            say("verb-in-argument.fix"),
        )

    def _preamble_text(self, node: TexContainer) -> None:
        """The first text before `\\begin{document}`: TeX stops on it, “Missing \\begin{document}”."""
        self._preamble = False
        self._add("text-in-preamble", say("text-in-preamble"), node, suggestion=say("text-in-preamble.fix"))

    def _add_span(
        self,
        code: str,
        message: str,
        span: tuple[Position, Position],
        related: tuple[Related, ...] = (),
        suggestion: str | None = None,
    ) -> None:
        self._found.append(
            TexDiagnostic(code, Severity.ERROR, message, span[0], span[1], related, suggestion)
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


def _control_symbol(node: TexCommand) -> bool:
    """`\\%`, `\\&`, `\\~`: a backslash and one character that is not a letter.

    TeX reads it as that character, or as a space; whatever the tables hold of
    it, it opens nothing. Counting one as a command that might have opened a
    list made `40\\% of the paper` a reason to stop checking the rest.
    """
    return len(node.base_name) == 1 and not node.base_name.isalpha()


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


@cache
def _harvested() -> SignatureRegistry:
    """What latex.ltx and fontmath.ltx define, whatever the class: the harvest alone, not the hand tables."""
    registry = SignatureRegistry()
    with (DATA / "kernel-harvested.txt").open(encoding="utf-8") as file:
        registry.load(file, origin="kernel-harvested.txt")
    return registry


def _loaded(container: TexGroup) -> tuple[str | None, set[str]]:
    """The class and the packages a document loads in its preamble."""
    loaded: set[str] = set()
    documentclass: str | None = None
    for node in container.content:
        if isinstance(node, TexGroup) and node.env and node.name == "document":
            break
        if not isinstance(node, TexCommand) or node.base_name not in (
            "documentclass",
            "usepackage",
            "RequirePackage",
        ):
            continue
        arguments = [argument for argument in node.arguments or () if argument is not None]
        names = next(
            (_argument_text(argument) for argument in arguments if argument.is_bracket_group()), None
        )
        if names is None:
            continue
        if node.base_name == "documentclass":
            documentclass = names.strip()
        else:
            loaded.update(part.strip() for part in names.split(",") if part.strip())
    return documentclass, loaded


def _deeper(depths: tuple[int, int, int], name: str) -> tuple[int, int, int]:
    """The list depths inside `\\begin{name}`."""
    itemize, enumerate_, total = depths
    if name == "itemize":
        itemize += 1
    elif name == "enumerate":
        enumerate_ += 1
    if name in COUNTED_LISTS:
        total += 1
    return itemize, enumerate_, total


def _numbers_nothing(math: TexGroup | None) -> bool:
    """Is `\\tag` refused in this math: inline, or an inner environment of amsmath?"""
    if math is None:
        return False
    return math.inlinemath if math.math else math.name in TAG_REFUSED or math.name == "math"


def _here(float_: TexGroup) -> bool:
    """`[H]` of the float package: the figure is put here, it does not float."""
    option = next((a for a in float_.arguments if a is not None and a.is_bracket_group() is False), None)
    return option is not None and "H" in str(option)


def _opening_span(group: TexGroup) -> tuple[Position, Position]:
    """`\\begin{name}`, and what it reads."""
    assert group.start_position is not None
    return group.start_position, group.inner_start or group.start_position


def _first_blank_line(group: TexGroup) -> TexContainer | None:
    for node in group.content:
        if isinstance(node, TexGroup):
            inner = _first_blank_line(node)
            if inner is not None:
                return inner
        elif node.is_par():
            return node
    return None


def _group_text(argument: TexContainer | None) -> str | None:
    """The text of an argument, in braces or in brackets: `2` of `[2]`."""
    if isinstance(argument, TexGroup):
        return argument.arg().strip()
    if argument is not None and argument.is_pure_text():
        return argument.content.strip()
    return None


def _consumed(command: TexCommand) -> list[TexContainer]:
    """The arguments TeX takes from the stream: `\\futurelet\\cs\\a\\b` takes `\\cs`, and only peeks at `\\b`.

    Found in the corpus: a `\\footnote` redefined to end on `\\futurelet\\nextToken\\isFootnote`,
    followed in a table by the `\\\\` that ends the row.
    """
    arguments = [argument for argument in command.arguments or () if argument is not None]
    return arguments[:1] if command.base_name == "futurelet" else arguments


def _reads_more(container: TexGroup) -> bool:
    """Does the preamble read files we do not see in it: `\\input`, `\\include`?"""
    for node in container.content:
        if isinstance(node, TexGroup) and node.env and node.name == "document":
            return False
        if isinstance(node, TexCommand) and node.base_name in ("input", "include", "InputIfFileExists"):
            return True
    return False
