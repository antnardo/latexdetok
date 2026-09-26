"""The tokeniser: from the lines of a LaTeX source to the tree of `latexdetok.classes`.

The original principle is kept: a stack of open groups, whose top receives what
is read; closing a group adds it to its parent.

Positions. The old loop recomputed the positions afterwards, through offsets
(`decalage_pos`, `addcol - 1`, `spaces_skipped`) spread over a dozen branches
with crossed counters; each one had its own one-column error (`\\\\` read back as
`o\\`), and a line starting with `$` passed for empty. Here every element notes
its position when it starts and when it ends: no position is derived.

Blanks. They are not nodes: `str()` finds them again in the gap between
positions (see `classes`). The text of a line is one single node, inner spaces
collapsed; it never crosses a line ending.

Tolerance. No valid LaTeX is refused, and nothing raises. Yet without expanding
the macros, one does not always know what matches:
`\\newenvironment{sol}{\\begin{proof}}{\\end{proof}}` is valid, so is a lone `$`
in `\\def\\m{$}`, and an `\\input` file may open `document` without closing it.
Hence one single rule: an opening is only **provisional** as long as it is not
closed. If it can no longer be — the brace around it closes, an `\\end` lower in
the stack is found, a blank line arrives (neither an optional argument nor math
holds a paragraph), the file ends — it is **dissolved**: its opening tokens and
its children join the parent, flat, and the structure report notes it (see
`diagnostics`), which draws one diagnostic per cause at the end of the reading.
Except inside the arguments of a definition: the body of a macro is only a run
of tokens, whose `\\begin` and `$` TeX only matches once it is expanded (see
`expansion`); reporting `\\def\\beq{\\begin{equation}}` there would be refusing
valid LaTeX.

Signatures. Every element added to a group goes through `_add`, which offers it
to the binding opened by the last known command (see `binding`). The signature
therefore decides what a `[` is (an optional argument or text), what a character
taken alone is (`\\frac12`), what an argument read verbatim is (`\\verb|…|`). The
definitions of the file enter the registry as soon as they are read (see
`definitions`). For an unknown command, the original heuristics remain:

- `[` only opens an optional argument right behind an unknown command, a group
  `{}`/`[]` that is the argument of no known command, or an unknown
  `\\begin{...}`; elsewhere it is text (“see [1]”);
- in math mode, `[` and `]` are text (`x\\in[0;1[`), and `\\[` does not open
  display mode there;
- a text-mode command met in math has been redefined elsewhere (`\\d`, `\\v` of
  the physicists): its kernel signature is ignored.

Conditionals. The tokeniser follows what TeX runs, expanding nothing: the value
of the booleans of `\\newif`, group scope included, and the open primitive
conditionals (see `conditions`). Every `\\if…` gets its role and, if it decides,
its value (`TexCommand.value`); a `\\proftrue` written in a branch we know to be
discarded is not run, and in an undecided branch or in the argument of a
command, it makes the value unknown. The branches an expanded view has decided
are given to it (`branch_regions`): each one becomes a `TexBranch`, read apart
if it is discarded, provisional if it is taken.

Speed. The loop read one character per Python iteration, and a course is mostly
text: 41,000 calls per course to add its letters one by one. A run of ordinary
characters is now taken at once, through a regular expression, and a command
name likewise. What counts as ordinary depends on the catcodes: the patterns are
built for each table (`_patterns`), and a run stops where a forced table
(`catcode_regions`) begins or ends. The binding is only consulted at the first
character of a text, which keeps the character-by-character path; the result is
identical, checked on the corpus by `scripts/fingerprints.py`. For the rest, no
detours: nodes built without `**kwargs`, the rest of a line cut only for the
commands that read it, debug traces computed only if they are shown.
"""

import logging
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from latexdetok.binding import ArgumentBinding
from latexdetok.catcodes import CATCODE_COMMANDS, CatcodeChange, CatcodeTable, Category, interpret
from latexdetok.characters import (
    MATH_DELIMITERS,
    MATHS_MODE_CAR,
    NOT_VERBATIM_DELIMITERS,
    ROOT_NAME,
    SPACE_CARS,
    collapse_spaces,
)
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
from latexdetok.conditions import (
    MATH_ARGUMENT_COMMANDS,
    MATH_ENVIRONMENTS,
    PRIMITIVE_CONDITIONALS,
    ROLE_NAMES,
    TEXT_ARGUMENT_COMMANDS,
    Role,
    conditional_role,
    ifnum_test,
    setter,
)
from latexdetok.definitions import DEFINING_COMMANDS, learn
from latexdetok.diagnostics import Cause, Context, StructureReport, TexDiagnostic, is_fragment
from latexdetok.logger import logger
from latexdetok.signatures import Boolean, CommandSignature, EnvironmentMacro, Macro, Mode, SignatureRegistry

__all__ = [
    "UNTYPESET_ARGUMENTS",
    "BranchRegion",
    "CatcodeRegions",
    "InputResolver",
    "TexParser",
    "unknown_command_before",
]


class InputResolver(Protocol):
    """Reads the definitions of a file the document loads (see `resolution`)."""

    def include(
        self,
        registry: SignatureRegistry,
        name: str,
        extension: str,
        catcodes: CatcodeTable,
        preamble_only: bool = False,
    ) -> CatcodeTable | None:
        """Return the table in force at the end of an `\\input`, which TeX keeps; None otherwise."""
        ...


BLANK_CATEGORIES = frozenset({Category.SPACE, Category.END_OF_LINE})

# Line → (start column, end column excluded, table), in column order.
CatcodeRegions = Mapping[int, Sequence[tuple[int, int, CatcodeTable]]]
# Control words of a body, and environments it opens: what it may run.
CONTROL_WORD = re.compile(r"\\([A-Za-z@]+)")
BEGIN_ENVIRONMENT = re.compile(r"\\begin\s*\{([^}]*)\}")


@dataclass(frozen=True, slots=True)
class BranchRegion:
    """A branch of a decided conditional, in the text to read (see `TexBranch`).

    From `start` to `end`, braces included for a `braced` branch; its content
    goes from `inner_start` to `inner_end`, on the same lines.
    """

    start: Position
    inner_start: Position
    inner_end: Position
    end: Position
    condition: Any
    taken: bool
    braced: bool


def _body(line: str) -> str:
    """The line without its line ending."""
    return line.rstrip("\r\n")


@dataclass(slots=True)
class _Opening:
    """An open group: the tokens that replace it if it dissolves, the binding and catcodes under way."""

    group: TexGroup
    tokens: list[TexContainer]
    catcodes: CatcodeTable
    binding: ArgumentBinding | None = None
    # The last element added came under a known signature: no heuristic for `[`.
    after_known: bool = False
    # The last element added is the argument of a command: it does not read arguments itself.
    after_argument: bool = False
    # Tables to restore at `\endgroup`: these plain groups are not in the stack.
    saved_catcodes: list[CatcodeTable] = field(default_factory=list)
    # Inside the arguments of a definition: nothing matches there before expansion.
    in_definition: bool = False
    # Inside a discarded branch: nothing runs there (definitions, catcodes, booleans).
    inactive: bool = False
    # The branch of a conditional this group stands for.
    branch: BranchRegion | None = None
    # The command this group is the expected argument of; or a group following an unknown command.
    argument_of: TexCommand | None = None
    after_unknown: bool = False
    # Values of the booleans changed locally, to restore when the group closes.
    saved_booleans: dict[str, bool | None] = field(default_factory=dict)
    # A bracket opened by heuristic, with no signature expecting it: perhaps text.
    guessed: bool = False
    # The argument of an unknown command, or inside one: we do not know whether it is typeset.
    untrusted: bool = False
    # An argument that is not typeset (`\directlua`): expanded, conditionals included; whatever is
    # reported there is only an info.
    untypeset: bool = False
    # The column specification of a table (`>{$}c<{$}`): characters, where nothing is reported.
    columns: bool = False
    # The unknown command whose name this group follows.
    owner: TexCommand | None = None


@dataclass(slots=True)
class _OpenCondition:
    """An open primitive conditional: its value, and which side of `\\else` we are reading."""

    value: bool | None
    in_else: bool = False


class TexParser:
    """Builds the tree of a sequence of lines.

    `parse()` returns the root; `diagnostics` then lists what could not be
    matched (see `diagnostics`), and `signatures` the registry enriched with the
    definitions read. With no registry given, every analysis starts from a copy
    of the kernel; a registry that is given is enriched in place. `resolver`, if
    it is given, reads the definitions of the files the source loads (`\\input`,
    `\\usepackage`…). `preamble_only` stops the reading at `\\begin{document}`.
    `catcodes` is the starting table (`CatcodeTable.package()` for a `.sty`);
    after the reading, `catcode_changes` lists the changes met and `catcodes` the
    table in force at the end. `catcode_regions` forces a table here and there,
    start and end columns per line: that is how the expanded view reads a macro
    body again under the table of its definition. `branch_regions` gives the
    branches of the conditionals the view decided.
    """

    def __init__(
        self,
        lines: Sequence[str],
        rootfile: Any = None,
        name: str = "<source>",
        signatures: SignatureRegistry | None = None,
        resolver: InputResolver | None = None,
        catcodes: CatcodeTable | None = None,
        preamble_only: bool = False,
        catcode_regions: CatcodeRegions | None = None,
        branch_regions: Sequence[BranchRegion] = (),
    ) -> None:
        self._lines = lines
        self._catcode_regions = catcode_regions or {}
        self._branch_events = _branch_events(branch_regions)
        self._preamble_only = preamble_only
        self._rootfile = rootfile
        self._name = name
        self._given_signatures = signatures
        self._resolver = resolver
        self._initial_catcodes = catcodes or CatcodeTable.latex()
        self._reset()

    def _reset(self) -> None:
        self.diagnostics: list[TexDiagnostic] = []
        # With no `\documentclass`, no `document` and no `% !TEX root`: a file another one includes.
        self.fragment = False
        self._report = StructureReport(self._name)
        # A `\documentclass` or a `document` read: a document, not a fragment included by another.
        self._document_seen = False
        # Math where a `$` has already been reported.
        self._dollars: set[int] = set()
        self.signatures = self._given_signatures or SignatureRegistry.kernel()
        self.catcodes = self._initial_catcodes
        self.catcode_changes: list[CatcodeChange] = []
        self._pile: list[_Opening] = []
        # The table forced where we read (`catcode_regions`); otherwise the group's.
        self._imposed: CatcodeTable | None = None
        self._text = ""
        self._text_start: Position = (0, 0)
        self._text_end: Position = (0, 0)
        self._pending_space = False
        self._verbatim: TexVerbatim | None = None
        self._end: Position | None = None  # end of \end{document}
        self._conditions: list[_OpenCondition] = []
        # Booleans the expansion of a macro may change, per macro.
        self._touched: dict[int, tuple[Macro | EnvironmentMacro, frozenset[str]]] = {}
        # The arguments of a trace are computed before the call: we only compute them if it shows.
        self._debug = logger.isEnabledFor(logging.DEBUG)

    def parse(self) -> TexGroup:
        self._reset()
        root = TexGroup(ROOT_NAME, env=True, rootfile=self._rootfile, position=(1, 0))
        self._pile = [_Opening(root, [], self._initial_catcodes)]
        previous_blank = False
        for lineno, line in enumerate(self._lines, start=1):
            body = _body(line)
            col = 0
            events = self._branch_events.get(lineno)
            if events is not None:
                previous_blank = False
                self._parse_with_branches(lineno, line, body, events)
                if self._end is not None:
                    break
                continue
            if self._verbatim is not None:
                resumed = self._continue_verbatim(lineno, line, 0)
                if resumed is None:
                    continue
                col = resumed
            elif not body.strip(SPACE_CARS):
                # An ignored line ending (`\ExplSyntaxOn`) makes no paragraph.
                if self._catcodes.category("\n") is Category.END_OF_LINE:
                    self._blank_line(lineno, body, previous_blank)
                    previous_blank = True
                continue
            previous_blank = False
            self._parse_line(lineno, line, body, col)
            self._imposed = None
            self._flush_text()
            if self._end is not None:
                break
        self._finish()
        self.fragment = is_fragment(self._lines, self._document_seen)
        self.diagnostics = self._report.finish(document=not self.fragment)
        self.catcodes = self._pile[0].catcodes
        root.end_position = self._end or self._last_position()
        root.inner_start, root.inner_end = root.start_position, root.end_position
        return root

    # Reading a line

    def _parse_with_branches(
        self, lineno: int, line: str, body: str, events: list[tuple[int, bool, BranchRegion]]
    ) -> None:
        """Read a line where branches open or close, piece by piece."""
        col = 0
        for event_col, opens, region in events:
            if col < event_col:
                self._read_until(lineno, line, body, col, event_col)
                col = event_col
            self._flush_text()
            if self._end is not None:
                return
            col = self._open_branch(region) if opens else self._close_branch(region, lineno, event_col)
        self._read_until(lineno, line, body, col, None)

    def _read_until(self, lineno: int, line: str, body: str, col: int, stop: int | None) -> None:
        if stop is not None:
            line, body = line[:stop], body[:stop]
        if self._verbatim is not None:
            resumed = self._continue_verbatim(lineno, line, col)
            if resumed is None:
                return
            col = resumed
        self._parse_line(lineno, line, body, col)
        self._imposed = None
        self._flush_text()

    def _parse_line(self, lineno: int, line: str, body: str, col: int) -> None:
        regions = self._catcode_regions.get(lineno)
        while self._end is None:
            if regions is not None:
                self._imposed = _imposed_table(regions, col)
            if self._verbatim is not None:
                # Before the end test: an open verbatim keeps its line ending.
                resumed = self._continue_verbatim(lineno, line, col)
                if resumed is None:
                    return
                col = resumed
                continue
            if col >= len(body):
                return
            c = body[col]
            # The role of a character comes from its category, but only the usual character
            # plays it (see `catcodes`): a `|` made an escape would stay text.
            catcodes = self._imposed or self._pile[-1].catcodes
            category = catcodes.category(c)
            if category in BLANK_CATEGORIES:
                self._pending_space = bool(self._text)
                limit = len(body) if regions is None else _region_boundary(regions, col, len(body))
                blanks = _patterns(catcodes).blank_run.match(body, col, limit)
                col = col + 1 if blanks is None else max(blanks.end(), col + 1)
            elif category is Category.IGNORED:
                col += 1
            elif category is Category.COMMENT and c == "%":
                if self._text:
                    self._flush_text()
                self._add(TexComment(body[col + 1 :], (lineno, col), (lineno, len(body)), self._rootfile))
                return
            elif category is Category.ESCAPE and c == "\\":
                col = self._command(lineno, body, col)
            elif category is Category.MATH_SHIFT and c == MATHS_MODE_CAR:
                col = self._dollar(lineno, body, col)
            elif (category is Category.BEGIN_GROUP and c == "{") or (
                c == "[" and category is Category.OTHER and self._opens_option()
            ):
                if self._text:
                    self._flush_text()
                group = TexGroup(c, position=(lineno, col), rootfile=self._rootfile)
                token = TexContent(c, (lineno, col), (lineno, col + 1), self._rootfile)
                self._open(group, (lineno, col + 1), [token])
                col += 1
            elif (
                category is Category.END_GROUP
                and c == "}"
                and self._find_open(_is_brace, through_braces=True) is not None
            ):
                if self._text:
                    self._flush_text()
                self._dissolve_until(_is_brace, True, Cause.BRACE, "}", ((lineno, col), (lineno, col + 1)))
                self._close_top(lineno, col, width=1)
                col += 1
            elif c == "]" and category is Category.OTHER and self._top.option:
                if self._text:
                    self._flush_text()
                self._close_top(lineno, col, width=1)
                col += 1
            elif c in SPECIAL_CHARACTERS or (self._pile[-1].binding is not None and not self._text):
                col = self._character(lineno, body, col)
            else:
                limit = len(body) if regions is None else _region_boundary(regions, col, len(body))
                col = self._text_run(lineno, body, col, limit)

    def _character(self, lineno: int, body: str, col: int) -> int:
        c = body[col]
        binding = self._pile[-1].binding
        wanted = binding.wants_character(c) if binding is not None and not self._text else None
        if wanted == "delimited":
            closing = _closing_delimiter(body, col, c, _CLOSERS.get(c, c))
            if closing is not None:
                text = collapse_spaces(body[col : closing + 1])
                self._add(TexContent(text, (lineno, col), (lineno, closing + 1), self._rootfile))
                return closing + 1
        if c == "}" and self._catcodes.category(c) is Category.END_GROUP:
            context = self._context()
            if context is not None:
                self._report.extra_brace((lineno, col), _last_brace(self._pile[-1].group), context)
        self._add_text(lineno, col, c)
        if wanted == "token":
            # An argument without braces is only one character: `\frac12`.
            self._flush_text()
        return col + 1

    def _text_run(self, lineno: int, body: str, col: int, limit: int) -> int:
        """Add at once the ordinary characters that follow, inner blanks collapsed.

        The same result as `_character` character by character, without one Python
        iteration per character: this is most of the text of a course. Nothing the
        run holds changes the catcodes, and the binding under way is only consulted
        at the first character of a text, which goes through `_character`.
        """
        patterns = _patterns(self._catcodes)
        match = patterns.run.match(body, col, limit)
        assert match is not None  # the first character is ordinary
        end = match.end()
        text = match.group()
        if patterns.blanks is not None:
            text = patterns.blanks.sub(" ", text)
        if not self._text:
            self._text_start = (lineno, col)
        elif self._pending_space:
            self._text += " "
        self._text += text
        self._pending_space = False
        self._text_end = (lineno, end)
        return end

    def _blank_line(self, lineno: int, body: str, previous_blank: bool) -> None:
        # Neither an optional argument nor math holds a paragraph.
        while self._top.option or self._top.math:
            self._dissolve(Cause.BLANK_LINE, "", ((lineno, 0), (lineno, len(body))))
        # Only the first blank line of a run makes a paragraph.
        if not previous_blank:
            self._add(TexContent("", (lineno, 0), (lineno, len(body)), self._rootfile))

    def _add_text(self, lineno: int, col: int, c: str) -> None:
        if not self._text:
            self._text_start = (lineno, col)
        elif self._pending_space:
            self._text += " "
        self._text += c
        self._pending_space = False
        self._text_end = (lineno, col + 1)

    def _flush_text(self) -> None:
        if self._text:
            self._add(TexContent(self._text, self._text_start, self._text_end, self._rootfile))
        self._text = ""
        self._pending_space = False

    # Commands

    def _command(self, lineno: int, body: str, col: int) -> int:
        if self._text:
            self._flush_text()
        self._settle_binding(command=True)
        start = col + 1
        if start >= len(body):
            # `\` at the end of a line: TeX reads a control space there.
            name, end = " ", start
        else:
            catcodes = self._catcodes
            letters = _patterns(catcodes).letters
            match = letters.match(body, start) if letters is not None else None
            end = start + 1 if match is None else match.end()
            if end < len(body) and body[end] == "*":
                end += 1
            name = body[start:end]

        star = len(name) > 1 and name.endswith("*")
        signature = self._command_signature(name[:-1] if star else name)
        if star and signature is not None and not signature.starred:
            # The star does not belong to the command: `\cdot*` is `\cdot` followed by `*`.
            name, end = name[:-1], end - 1

        # A star kept on the name is that of a starred signature: the body is under the name without it.
        macro = self.signatures.macro(name[:-1] if len(name) > 1 and name.endswith("*") else name)
        binding = self._pile[-1].binding
        if (
            binding is not None
            and binding.wants_command()
            and (name not in STRUCTURAL or _names_delimiter(binding, name))
        ):
            # An argument without braces: the command is a plain token (`\def\url`, `\let\a\b`).
            token = TexCommand(
                name, (lineno, col), (lineno, end), self._rootfile, signature=signature, macro=macro
            )
            token.bare = True
            self._add(token)
            return end
        if not (self._pile[-1].inactive or self._skipping()):
            self._change_catcodes(name, body, end, (lineno, col))
        if signature is not None and signature.verbatim and _starts_verbatim(body, end):
            return self._begin_verbatim_command(lineno, body, col, end, name)
        if name in ("begin", "end"):
            argument = _environment_argument(body, end)
            if argument is not None:
                if name == "begin":
                    return self._begin_environment(lineno, body, col, end, *argument)
                return self._end_environment(lineno, col, end, *argument)
        control = "\\" + name
        if control in ("\\[", "\\(") and not self._in_math():
            self._open_math(control, lineno, col)
            return end
        if control in ("\\]", "\\)"):

            def is_math(group: TexGroup) -> bool:
                return group.math and MATH_DELIMITERS[group.delimiter or ""] == control

            if self._find_open(is_math, through_braces=False) is not None:
                self._dissolve_until(is_math, False, Cause.MATH, control, ((lineno, col), (lineno, end)))
                self._close_top(lineno, col, width=len(control))
                return end
            context = self._context()
            if context is not None and macro is None:
                self._report.stray_math_close((lineno, col), (lineno, end), control, context)
        elif control in ("\\[", "\\(") and macro is None and self._math_mode() is True:
            # Redefined (`\def\[{[}`), it is a macro like any other: nothing to report.
            context = self._context()
            math = next((opening.group for opening in reversed(self._pile) if opening.group.math), None)
            if context is not None and math is not None:
                self._report.math_in_math((lineno, col), (lineno, end), control, math, context)
        node = TexCommand(
            name, (lineno, col), (lineno, end), self._rootfile, signature=signature, macro=macro
        )
        if self._executed():
            # Only an `\if…` reads the rest of the line (`\ifnum 1<2`, `\ifstrempty{`).
            self._execute(node, body[end:] if name.startswith("if") else "")
        self._add(node)
        return end

    # Conditionals and booleans

    def _executed(self) -> bool:
        """Does TeX run what we are reading? Not the body of a definition, nor a discarded branch."""
        top = self._pile[-1]
        return not (top.in_definition or top.inactive)

    def _skipping(self) -> bool:
        """Are we reading a branch a decided conditional discards? TeX skips it without running anything."""
        return bool(self._conditions) and any(
            condition.value is not None and condition.value == condition.in_else
            for condition in self._conditions
        )

    def _execute(self, node: TexCommand, following: str) -> None:
        """What the command does to the open conditionals and to the booleans."""
        name = node.base_name
        registry = self.signatures
        role = None
        if name.startswith("if") or name in ROLE_NAMES:
            known = registry.command(name) is not None or registry.macro(name) is not None
            role = conditional_role(name, following, registry.is_boolean, known)
        node.role = role
        if role is Role.IF:
            if not (name in PRIMITIVE_CONDITIONALS or registry.is_boolean(name[2:])):
                # Perhaps not a conditional: the `\fi` that follow would go to other `\if`.
                for condition in self._conditions:
                    condition.value = None
            node.value = self._condition_value(name, following)
            self._conditions.append(_OpenCondition(node.value))
        elif role is Role.ELSE and self._conditions:
            self._conditions[-1].in_else = True
        elif role is Role.OR and self._conditions:
            self._conditions[-1].value = None  # `\ifcase`: we do not follow its cases
        elif role is Role.FI and self._conditions:
            self._conditions.pop()
        elif role is None:
            assignment = setter(name, registry.is_boolean)
            if assignment is not None:
                self._set_boolean(*assignment)
            elif node.macro is not None:
                self._forget_booleans(node.macro)

    def _condition_value(self, name: str, following: str) -> bool | None:
        if name in ("iftrue", "iffalse"):
            return name == "iftrue"
        if name == "ifnum":
            test = ifnum_test(following)
            return None if test is None else test[0]
        if name == "ifmmode":
            return self._math_mode()
        return self.signatures.boolean(name[2:])

    def _assignment_context(self) -> bool | None:
        """Is an assignment read here actually run? None when it cannot be known."""
        unknown = False
        for condition in self._conditions:
            if condition.value is None:
                unknown = True
            elif condition.value == condition.in_else:
                return False
        for opening in self._pile[1:]:
            # The argument of a command may be run elsewhere, several times, or never.
            if opening.branch is None and (opening.argument_of is not None or opening.after_unknown):
                unknown = True
        return None if unknown else True

    def _set_boolean(self, name: str, value: bool) -> None:
        context = self._assignment_context()
        if context is False:
            return
        content = self._pile[-1].group.content
        is_global = bool(content) and content[-1].is_command("global")
        self._assign_boolean(name, value if context else None, is_global)

    def _assign_boolean(self, name: str, value: bool | None, is_global: bool = False) -> None:
        if is_global:
            for opening in self._pile:
                opening.saved_booleans.pop(name, None)
        elif value is not None:
            # A known value only holds to the end of the TeX group; an unknown one stays unknown.
            scope = next((opening for opening in reversed(self._pile[1:]) if _is_tex_group(opening)), None)
            if scope is not None and name not in scope.saved_booleans:
                scope.saved_booleans[name] = self.signatures.boolean(name)
        self.signatures.define(Boolean(name, value))

    def _forget_booleans(self, macro: Macro | EnvironmentMacro) -> None:
        """The booleans the expansion of `macro` may change become unknown."""
        touched = self._touched_booleans(macro)
        if not touched or self._assignment_context() is False:
            return
        for name in touched:
            self._assign_boolean(name, None)

    def _touched_booleans(self, macro: Macro | EnvironmentMacro) -> frozenset[str]:
        cached = self._touched.get(id(macro))
        if cached is not None and cached[0] is macro:
            return cached[1]
        registry = self.signatures
        names: set[str] = set()
        seen: set[str] = set()
        texts = _macro_texts(macro)
        while texts and len(seen) < MAX_TOUCHED_MACROS:
            text = texts.pop()
            for word in CONTROL_WORD.findall(text):
                assignment = setter(word, registry.is_boolean)
                if assignment is not None:
                    names.add(assignment[0])
                elif word not in seen:
                    seen.add(word)
                    called = registry.macro(word)
                    if called is not None:
                        texts.extend(_macro_texts(called))
            for environment in BEGIN_ENVIRONMENT.findall(text):
                code = registry.environment_macro(environment.strip())
                if code is not None and environment not in seen:
                    seen.add(environment)
                    texts.extend(_macro_texts(code))
        touched = frozenset(names)
        # The macro is kept with its result: its identity cannot be reused by another.
        self._touched[id(macro)] = (macro, touched)
        return touched

    def _math_mode(self) -> bool | None:
        """`\\ifmmode`: are we reading in math? None if an unknown command or environment decides it."""
        for opening in reversed(self._pile[1:]):
            group = opening.group
            if isinstance(group, TexBranch):
                continue
            if group.math:
                return True
            if group.env:
                if group.name in MATH_ENVIRONMENTS or (
                    group.signature is not None and group.signature.mode is Mode.MATH
                ):
                    return True
                return None if group.signature is None else False
            command = opening.argument_of
            if command is not None:
                if command.base_name in MATH_ARGUMENT_COMMANDS:
                    return True
                if command.base_name in TEXT_ARGUMENT_COMMANDS or (
                    command.signature is not None and command.signature.mode is Mode.TEXT
                ):
                    return False
            elif opening.after_unknown:
                return None
        return False

    # Branches decided by the expanded view

    def _open_branch(self, region: BranchRegion) -> int:
        parent = self._pile[-1]
        group = TexBranch(
            region.condition, region.taken, region.braced, position=region.start, rootfile=self._rootfile
        )
        group.inner_start = region.inner_start
        self._pile.append(
            _Opening(
                group,
                [],
                parent.catcodes,
                in_definition=parent.in_definition or not region.taken,
                inactive=parent.inactive or not region.taken,
                branch=region,
            )
        )
        return region.inner_start[1]

    def _close_branch(self, region: BranchRegion, lineno: int, col: int) -> int:
        end = region.end[1]
        index = next((i for i in range(len(self._pile) - 1, 0, -1) if self._pile[i].branch is region), None)
        if index is None:
            return end  # already dissolved: an `\end` or a brace went through it
        opening = self._pile[index]
        if not region.taken:
            # Read apart: what stays open there does not leave the branch.
            verbatim = self._verbatim
            if (
                verbatim is not None
                and verbatim.start_position is not None
                and verbatim.start_position >= region.start
            ):
                verbatim.end_position = (lineno, col)
                self._verbatim = None
            while len(self._pile) - 1 > index:
                self._dissolve(Cause.BRANCH)
        elif len(self._pile) - 1 > index or opening.binding is not None:
            # The structure spills out of the taken branch: it melts into the stream, as in TeX.
            self._melt(index)
            return end
        self._pile.pop()
        self._close_binding(opening)
        group = opening.group
        group.inner_end = (lineno, col)
        group.end_position = (lineno, end)
        if region.taken:
            self._pile[-1].catcodes = opening.catcodes  # a branch is not a TeX group
        self._add(group)
        return end

    def _melt(self, index: int) -> None:
        """The taken branch at `index` joins its parent; what opened inside it stays open."""
        opening = self._pile.pop(index)
        parent = self._pile[index - 1]
        parent.group.content.extend(opening.group.content)
        parent.catcodes = opening.catcodes
        if opening.binding is not None and index == len(self._pile) and parent.binding is not None:
            # What the parent was waiting for (the `[` of a `\\` before the branch) can no longer come:
            # the branch read a command first. Without this, that command lost its arguments.
            self._close_binding(parent)
        if opening.binding is not None and parent.binding is None and index == len(self._pile):
            # The command that ends the branch reads its arguments after it.
            parent.binding = opening.binding
            parent.after_known = opening.after_known
        else:
            self._close_binding(opening)

    def _change_catcodes(self, name: str, body: str, end: int, position: Position) -> None:
        """Apply to the current group what the command, ended at `end`, changes in the categories."""
        opening = self._pile[-1]
        if name == "begingroup":
            opening.saved_catcodes.append(opening.catcodes)
            return
        if name == "endgroup":
            if opening.saved_catcodes:
                opening.catcodes = opening.saved_catcodes.pop()
            return
        if name not in CATCODE_COMMANDS:
            return
        changes = interpret(name, body[end:])
        if not changes:
            return
        content = opening.group.content
        local = not (content and content[-1].is_command("global"))
        current = opening.catcodes
        self.catcode_changes.extend(
            CatcodeChange(position, character, current.category(character), category, name, local)
            for character, category in changes.items()
            if current.category(character) is not category
        )
        if local:
            opening.catcodes = current.with_categories(changes)
            return
        # `\global\catcode`: holds in every open group, and survives their ends.
        for target in self._pile:
            target.catcodes = target.catcodes.with_categories(changes)
            target.saved_catcodes = [table.with_categories(changes) for table in target.saved_catcodes]

    def _command_signature(self, name: str) -> CommandSignature | None:
        signature = self.signatures.command(name)
        if signature is not None and signature.mode is Mode.TEXT and self._in_math():
            return None
        return signature

    def _dollar(self, lineno: int, body: str, col: int) -> int:
        double = body.startswith("$$", col)
        top = self._top
        if not top.math:
            self._flush_text()
            delimiter = "$$" if double else "$"
            self._open_math(delimiter, lineno, col)
            return col + len(delimiter)
        closing = MATH_DELIMITERS[top.delimiter or ""]
        # Inside `$` math, a `$` closes: `$a$$b$` makes two formulas.
        if closing == "$" or (closing == "$$" and double):
            self._flush_text()
            self._close_top(lineno, col, width=len(closing))
            return col + len(closing)
        # A lone `$` inside `$$`, `\[` or `\(`: TeX refuses it, we keep it as text.
        width = 2 if double else 1
        context = self._context()
        if context is not None and id(top) not in self._dollars:
            # Only one per math: `\[ x = $y$ \]` is one mistake, not two.
            self._dollars.add(id(top))
            self._report.dollar_in_math((lineno, col), width, top, context)
        self._add_text(lineno, col, "$")
        if double:
            self._add_text(lineno, col + 1, "$")
        return col + width

    def _open_math(self, delimiter: str, lineno: int, col: int) -> None:
        self._settle_binding()
        end = (lineno, col + len(delimiter))
        group = TexGroup(
            MATHS_MODE_CAR, env=True, delimiter=delimiter, position=(lineno, col), rootfile=self._rootfile
        )
        if delimiter.startswith("\\"):
            token = TexContent(delimiter[1:], (lineno, col), end, self._rootfile, command=True)
        else:
            token = TexContent(delimiter, (lineno, col), end, self._rootfile)
        self._open(group, end, [token])

    # Environments

    def _begin_environment(
        self, lineno: int, body: str, col: int, end: int, brace: int, close: int, name: str
    ) -> int:
        after = close + 1
        self._settle_binding()
        if name == "document" and self._executed() and not self._skipping():
            self._document_seen = True
        if self._preamble_only and name == "document" and self._executed() and not self._skipping():
            # The preamble of a master document: the rest would teach its chapters nothing.
            self._end = (lineno, col)
            return after
        signature = self.signatures.environment(name)
        if signature is not None and signature.verbatim:
            verbatim = TexVerbatim(name, env=True, position=(lineno, col), rootfile=self._rootfile)
            self._add(verbatim)
            self._verbatim = verbatim
            if self._debug:
                logger.debug("VERBATIM %s opened %s", name, _at((lineno, col)))
            return after
        group = TexGroup(name, env=True, position=(lineno, col), rootfile=self._rootfile)
        group.macro = self.signatures.environment_macro(name)
        if group.macro is not None and self._executed():
            self._forget_booleans(group.macro)
        tokens = [
            TexContent("begin", (lineno, col), (lineno, end), self._rootfile, command=True),
            self._name_group(lineno, body, brace, close),
        ]
        self._open(group, (lineno, after), tokens)
        if signature is not None:
            group.signature = signature
            opening = self._pile[-1]
            opening.after_known = True
            if signature.arguments:
                opening.binding = ArgumentBinding(group, signature.arguments)
        return after

    def _end_environment(self, lineno: int, col: int, end: int, brace: int, close: int, name: str) -> int:
        def is_environment(group: TexGroup) -> bool:
            return group.env and not group.math and not group.is_root and group.name == name

        closing = ((lineno, col), (lineno, close + 1))
        if self._find_open(is_environment, through_braces=False) is None:
            # Opened elsewhere: inside a macro definition, or in another file.
            self._report_stray_end(name, *closing)
            self._add(TexCommand("end", (lineno, col), (lineno, end), self._rootfile))
            return end  # `{name}` will be read as an ordinary group
        self._dissolve_until(is_environment, False, Cause.END, f"\\end{{{name}}}", closing)
        self._close_top(lineno, col, width=close + 1 - col)
        if name == "document" and self._executed() and not self._skipping():
            self._end = (lineno, close + 1)
            if self._debug:
                logger.debug("END OF DOCUMENT %s", _at(self._end))
        return close + 1

    def _name_group(self, lineno: int, body: str, brace: int, close: int) -> TexGroup:
        """The `{name}` of a `\\begin`, as an ordinary reading would have made it."""
        group = TexGroup(
            "{", position=(lineno, brace), end_position=(lineno, close + 1), rootfile=self._rootfile
        )
        group.inner_start, group.inner_end = (lineno, brace + 1), (lineno, close)
        raw = body[brace + 1 : close]
        if raw.strip(SPACE_CARS):
            left = brace + 1 + len(raw) - len(raw.lstrip(SPACE_CARS))
            right = close - (len(raw) - len(raw.rstrip(SPACE_CARS)))
            group.content.append(
                TexContent(collapse_spaces(raw), (lineno, left), (lineno, right), self._rootfile)
            )
        return group

    # Verbatim

    def _begin_verbatim_command(self, lineno: int, body: str, col: int, end: int, name: str) -> int:
        verbatim = TexVerbatim(name, car=body[end], position=(lineno, col), rootfile=self._rootfile)
        self._add(verbatim)
        if self._debug:
            logger.debug("VERBATIM %s opened %s", verbatim.opening, _at((lineno, col)))
        if body[end] == "{":
            # Between braces, the argument matches them: \pyv{r"\begin{document}"}.
            close = _closing_delimiter(body, end, "{", "}")
            if close is None and "}" in body[end + 1 :]:
                close = body.index("}", end + 1)
                context = self._context()
                if context is not None:
                    self._report.verbatim_braces(verbatim, context)
            if close is not None:
                verbatim.content = body[end + 1 : close]
                verbatim.end_position = (lineno, close + 1)
                return close + 1
        self._verbatim = verbatim
        return end + 1

    def _continue_verbatim(self, lineno: int, line: str, col: int) -> int | None:
        """Read the verbatim from `col`; return the column that follows its end, or None
        if it goes on to the next line."""
        verbatim = self._verbatim
        assert verbatim is not None
        exit_phrase = verbatim.verbatim_exit_phrase
        found = line.find(exit_phrase, col)
        if found < 0 and not verbatim.env and verbatim.name in LINE_VERBATIM:
            # TeX stops `\verb` at the end of the line: the rest of the file is not verbatim.
            body = _body(line)
            verbatim.content += body[col:]
            verbatim.end_position = (lineno, len(body))
            verbatim.closed = False
            self._verbatim = None
            context = self._context()
            if context is not None:
                self._report.verb_line_end(verbatim, context)
            return None
        if found < 0:
            # Its line ending is written `\n`, like every one the tree writes: `\r\n` stays in the source.
            body = _body(line)
            verbatim.content += body[col:] + ("\n" if len(body) < len(line) else "")
            return None
        verbatim.content += line[col:found]
        verbatim.end_position = (lineno, found + len(exit_phrase))
        self._verbatim = None
        if self._debug:
            logger.debug("VERBATIM %s closed %s", verbatim.name, _at(verbatim.end_position))
        return found + len(exit_phrase)

    # Stack and bindings

    @property
    def _top(self) -> TexGroup:
        return self._pile[-1].group

    @property
    def _catcodes(self) -> CatcodeTable:
        """The table that cuts what we read: forced here and there, otherwise the group's."""
        if self._imposed is not None:
            return self._imposed
        return self._pile[-1].catcodes

    def _add(self, node: TexContainer) -> None:
        """Add to the group on top, and offer the element to the binding under way."""
        opening = self._pile[-1]
        opening.group.content.append(node)
        binding = opening.binding
        opening.after_argument = False
        if binding is not None:
            if binding.consume(node):
                opening.after_known = True
                opening.after_argument = True
                if binding.done:
                    self._close_binding(opening)
                return
            self._close_binding(opening)
        opening.after_known = False
        if isinstance(node, TexCommand) and node.signature is not None:
            opening.after_known = True
            if node.signature.arguments:
                opening.binding = ArgumentBinding(node, node.signature.arguments)

    def _settle_binding(self, command: bool = False) -> None:
        """Close the binding under way if what comes cannot belong to it.

        Without this, `\\usepackage{x}`, which is still waiting for its final
        `[date]`, would only be read at the next element — after that element had
        been read without the definitions of `x`. A command may be an `m`
        argument; math or an environment may not.
        """
        opening = self._pile[-1]
        if opening.binding is not None and not (command and opening.binding.wants_command()):
            self._close_binding(opening)
            opening.after_known = False

    def _close_binding(self, opening: _Opening) -> None:
        binding = opening.binding
        if binding is None:
            return
        opening.binding = None
        binding.close()
        if isinstance(binding.target, TexCommand):
            self._learn(opening, binding.target)

    def _learn(self, opening: _Opening, command: TexCommand) -> None:
        if opening.inactive or self._skipping():
            return
        if command.base_name == "documentclass" and not opening.in_definition:
            self._document_seen = True
        inclusions = learn(command, self.signatures, opening.catcodes)
        if self._resolver is None:
            return
        for name, extension in inclusions:
            final = self._resolver.include(self.signatures, name, extension, opening.catcodes)
            if final is not None:
                # `\input` opens no group: its catcode changes stay in its own.
                opening.catcodes = final

    def _open(self, group: TexGroup, inner_start: Position, tokens: list[TexContainer]) -> None:
        group.inner_start = inner_start
        parent = self._pile[-1]
        target = parent.binding.target if parent.binding is not None else None
        content = parent.group.content
        owner = None
        if (
            (group.bracket or group.option)
            and parent.binding is None
            and not parent.after_argument
            and content
        ):
            owner = unknown_command_before(content)
        reader = target if isinstance(target, TexCommand) else owner
        in_definition = parent.in_definition or (
            isinstance(target, TexCommand) and target.base_name in DEFINING_COMMANDS
        )
        untypeset = parent.untypeset or (
            group.bracket and reader is not None and reader.base_name in UNTYPESET_ARGUMENTS
        )
        columns = parent.columns or (group.bracket and _column_specification(parent, target, owner))
        after_unknown = (
            group.bracket
            and parent.binding is None
            and not parent.after_argument
            and bool(content)
            and isinstance(content[-1], TexCommand)
            and content[-1].signature is None
            and content[-1].role is None
        )
        self._pile.append(
            _Opening(
                group,
                tokens,
                parent.catcodes,
                in_definition=in_definition,
                inactive=parent.inactive,
                argument_of=target if isinstance(target, TexCommand) else None,
                after_unknown=after_unknown,
                untrusted=parent.untrusted or owner is not None,
                guessed=group.option and parent.binding is None,
                untypeset=untypeset,
                columns=columns,
                owner=owner,
            )
        )
        if self._debug:
            logger.debug("OPEN %s %s", group.enclosures()[0], _at(group.start_position))

    def _close_top(self, lineno: int, col: int, width: int) -> None:
        opening = self._pile.pop()
        self._close_binding(opening)
        group = opening.group
        if (group.bracket or group.option) and len(group.content) == 1:
            _named_not_called(group.content[0])
        if group.option:
            # An optional argument is not a TeX group: its catcode changes stay.
            self._pile[-1].catcodes = opening.catcodes
        for name, value in opening.saved_booleans.items():
            self.signatures.define(Boolean(name, value))
        group.inner_end = (lineno, col)
        group.end_position = (lineno, col + width)
        self._add(group)
        if self._debug:
            logger.debug("CLOSE %s %s", group.enclosures()[1], _at(group.end_position))

    def _find_open(self, wanted: Callable[[TexGroup], bool], through_braces: bool) -> int | None:
        """The index in the stack of the nearest open group that fits.

        Without `through_braces`, the search stops at the first brace: an `\\end`
        does not close an environment opened outside its group.
        """
        for index in range(len(self._pile) - 1, 0, -1):
            opening = self._pile[index]
            if wanted(opening.group):
                return index
            if opening.group.bracket and not through_braces:
                return None
            if opening.branch is not None and not opening.branch.taken:
                return None  # a discarded branch closes nothing outside itself
        return None

    def _dissolve_until(
        self,
        wanted: Callable[[TexGroup], bool],
        through_braces: bool,
        cause: Cause,
        closer: str,
        at: tuple[Position, Position],
    ) -> None:
        index = self._find_open(wanted, through_braces)
        assert index is not None
        while len(self._pile) - 1 > index:
            self._dissolve(cause, closer, at)

    def _dissolve(self, cause: Cause, closer: str = "", at: tuple[Position, Position] | None = None) -> None:
        """The top will not close: its tokens and its children join the parent."""
        opening = self._pile.pop()
        self._close_binding(opening)
        parent = self._pile[-1]
        # Never closed, the group restored nothing: its catcodes stay in force.
        parent.catcodes = opening.catcodes
        # The group the parent's binding was waiting for will not come.
        self._close_binding(parent)
        parent.after_known = False
        parent.group.content.extend([*opening.tokens, *opening.group.content])
        if opening.branch is not None:
            return  # a branch has no delimiter to report: it melts into the stream
        context = self._context()
        if context is None:
            return
        group = opening.group
        known = not opening.guessed
        if group.env and not group.math:
            known = self._known_environment(group.name)
        owner = opening.argument_of or opening.owner
        self._report.unclosed(group, cause, context, closer, at, owner, known)

    def _context(self) -> Context | None:
        """The context of what we read at the top of the stack; None where nothing is reported."""
        opening = self._pile[-1]
        if opening.inactive or opening.columns:
            return None
        if opening.in_definition or opening.untypeset:
            return Context.DEFINITION
        return Context.UNKNOWN_ARGUMENT if opening.untrusted else Context.NORMAL

    def _known_environment(self, name: str) -> bool:
        registry = self.signatures
        return (
            registry.environment(name) is not None
            or registry.environment_macro(name) is not None
            or name in MATH_ENVIRONMENTS
        )

    def _report_stray_end(self, name: str, start: Position, end: Position) -> None:
        """An `\\end{name}` that closes nothing in its group: what is open around it, for the report."""
        context = self._context()
        if context is None:
            return
        braces: list[tuple[TexGroup, TexCommand | None]] = []
        target = environment = None
        for opening in reversed(self._pile[1:]):
            group = opening.group
            if opening.branch is not None and not opening.branch.taken:
                break
            if group.bracket and not isinstance(group, TexBranch):
                braces.append((group, opening.argument_of or opening.owner))
            elif group.env and not group.math and not isinstance(group, TexBranch):
                if group.name == name:
                    target = group
                    break
                if environment is None:
                    environment = group
        if target is None:
            braces = []
        self._report.stray_end(
            name,
            start,
            end,
            context,
            self._known_environment(name),
            target,
            braces,
            None if target is not None else environment,
            environment is not None and self._known_environment(environment.name),
        )

    def _in_math(self) -> bool:
        return any(opening.group.math for opening in self._pile)

    def _opens_option(self) -> bool:
        if self._text:
            return False
        opening = self._pile[-1]
        if opening.binding is not None:
            return opening.binding.accepts_opener("[")
        if self._in_math() or opening.after_known:
            return False
        group = opening.group
        if not group.content:
            # Right after an unknown \begin{…}: \begin{tikzpicture}[scale=2].
            return group.env and not group.is_root
        previous = group.content[-1]
        return previous.is_command() or previous.is_bracket_group() or previous.is_option_group()

    # End of the reading

    def _finish(self) -> None:
        if self._verbatim is not None:
            verbatim = self._verbatim
            context = self._context()
            if context is not None:
                self._report.unclosed_verbatim(verbatim, context)
            verbatim.end_position = self._last_position()
            verbatim.closed = False
            self._verbatim = None
        while len(self._pile) > 1:
            self._dissolve(Cause.END_OF_FILE)
        self._close_binding(self._pile[0])

    def _last_position(self) -> Position:
        if not self._lines:
            return (1, 0)
        return (len(self._lines), len(_body(self._lines[-1])))


_CLOSERS = {"(": ")", "[": "]", "<": ">", "{": "}"}
# Beyond that, we give up following what a macro calls (and loops stop).
MAX_TOUCHED_MACROS = 200


def _branch_events(regions: Sequence[BranchRegion]) -> dict[int, list[tuple[int, bool, BranchRegion]]]:
    """Per line, the openings and closings of branches, in reading order.

    At the same column, we close before opening, the inner branch before the
    outer one, and we open the outer one before the inner one.
    """
    keyed: dict[int, list[tuple[tuple[int, int, int, int, int], tuple[int, bool, BranchRegion]]]] = {}
    for region in regions:
        (start_line, start_col), (close_line, close_col) = region.start, region.inner_end
        end_line, end_col = region.end
        opening = ((start_col, 1, -end_line, -end_col, 0), (start_col, True, region))
        closing = ((close_col, 0, -start_line, -start_col, 0), (close_col, False, region))
        keyed.setdefault(start_line, []).append(opening)
        keyed.setdefault(close_line, []).append(closing)
    return {
        line: [event for _, event in sorted(items, key=lambda item: item[0])] for line, items in keyed.items()
    }


def _is_tex_group(opening: _Opening) -> bool:
    """A group TeX closes by restoring the values: braces, environment, math."""
    group = opening.group
    return opening.branch is None and not group.option


def _macro_texts(macro: Macro | EnvironmentMacro) -> list[str]:
    if isinstance(macro, EnvironmentMacro):
        return [macro.begin, macro.end]
    return [macro.body] if macro.otherwise is None else [macro.body, macro.otherwise]


# Never taken as an argument without braces: TeX would do it (`\frac{a}\end{align}`),
# but that would undo the structure for a missing argument, which the diagnostic
# reports better in its place.
STRUCTURAL = frozenset({"begin", "end", "[", "]", "(", ")"})


def _column_specification(
    parent: _Opening, target: TexCommand | TexGroup | None, owner: TexCommand | None
) -> bool:
    """Is the group that opens a column specification?

    The argument of a table, through its signature (`tabular`) or, for a package
    table, right after its `\\begin`; or that of a column type being defined
    (`\\newcolumntype{C}{>{$}c<{$}}`), whose unknown command is `owner`.
    """
    group = parent.group
    if isinstance(target, TexGroup):
        return target.name in TABULARS
    reader = target if isinstance(target, TexCommand) else owner
    if reader is not None:
        return reader.base_name in COLUMN_TYPES
    return group.env and group.name in TABULARS and group.signature is None and not group.content


def _names_delimiter(binding: ArgumentBinding, name: str) -> bool:
    """`\\def\\[{…}`, `\\let\\]\\relax`: the first argument of a definition names the delimiter defined.

    Only that one: `\\futurelet\\next\\test` still reads a token, which must not be
    the `\\end{itemize}` of the next line.
    """
    target = binding.target
    return (
        name in "[]()"
        and isinstance(target, TexCommand)
        and target.base_name in DEFINING_COMMANDS
        and not target.arguments
    )


def _named_not_called(node: TexContainer) -> None:
    """Alone in its braces or brackets and with no argument found, a command is being named.

    `\\titleformat{\\section}`, `\\renewcommand{\\section}`, `\\pare[\\big]{x}`: the
    group passes the command to another one, which does not run it there. Looking
    for its arguments would make as many missing arguments.
    """
    if (
        isinstance(node, TexCommand)
        and node.arguments
        and all(argument is None for argument in node.arguments)
    ):
        node.arguments = None
        node.bare = True


# Commands whose argument is not typeset where it is written: it goes off to Lua, to a
# file or to the log. As in the body of a definition, whatever does not match there is only
# an info; but TeX expands it, conditionals included, and the tokeniser follows them.
UNTYPESET_ARGUMENTS = frozenset(
    {
        "directlua",
        "latelua",
        "luadirect",
        "luaexec",
        "luastring",
        "luaescapestring",
        "write",
        "message",
        "typeout",
        "wlog",
        "special",
        "detokenize",
        "unexpanded",
        "ShellEscape",
        "PackageInfo",
        "PackageWarning",
        "PackageError",
        "ClassWarning",
        "ClassError",
        "GenericWarning",
        "GenericError",
        # Packages: numbers, units, chemical formulas, key-value pairs.
        "SI",
        "si",
        "num",
        "ang",
        "numlist",
        "numrange",
        "SIlist",
        "SIrange",
        "qty",
        "unit",
        "qtylist",
        "qtyrange",
        "tablenum",
        "sisetup",
        "ce",
        "cee",
        "cf",
        "chemfig",
        "lstset",
        "tcbset",
        "hypersetup",
        "geometry",
        "newgeometry",
        "tikzset",
        "pgfplotsset",
        "usetikzlibrary",
        "captionsetup",
    }
)
# Define a column type, whose last argument is a specification.
COLUMN_TYPES = frozenset({"newcolumntype", "NewColumnType"})
# Environments one of whose arguments gives the columns: `>{$}c<{$}` is a specification there, not math.
TABULARS = frozenset(
    {
        "tabular",
        "tabular*",
        "array",
        "longtable",
        "tabularx",
        "tabulary",
        "supertabular",
        "xtabular",
        "tblr",
        "longtblr",
        "NiceTabular",
        "NiceArray",
        "tabu",
        "longtabu",
    }
)
# Verbatim that stops at the end of its line, closed or not.
LINE_VERBATIM = frozenset({"verb", "verb*"})

# Characters `_parse_line` examines one by one, whatever their category.
SPECIAL_CHARACTERS = frozenset("\\{}$%[]")


@dataclass(frozen=True, slots=True)
class _TablePatterns:
    """What the reading loop looks for at once under a table of catcodes.

    `run`: a run of ordinary characters (neither special, nor blank, nor
    ignored), inner blanks included; a blank only enters it between two ordinary
    characters, so that the run ends on the last character written. `blanks`:
    those blanks, to be collapsed. `blank_run`: blanks and ignored characters,
    which change nothing in the text under way but a space to come. `letters`: a
    command name.
    """

    run: re.Pattern[str]
    blanks: re.Pattern[str] | None
    blank_run: re.Pattern[str]
    letters: re.Pattern[str] | None


_PATTERNS: dict[CatcodeTable, _TablePatterns] = {}


def _patterns(catcodes: CatcodeTable) -> _TablePatterns:
    patterns = _PATTERNS.get(catcodes)
    if patterns is None:
        blank = "".join(sorted(c for c, category in catcodes.items() if category in BLANK_CATEGORIES))
        ignored = "".join(sorted(c for c, category in catcodes.items() if category is Category.IGNORED))
        letters = "".join(sorted(c for c, category in catcodes.items() if category is Category.LETTER))
        plain = f"[^{re.escape(''.join(sorted(SPECIAL_CHARACTERS)) + blank + ignored)}]+"
        patterns = _PATTERNS[catcodes] = _TablePatterns(
            run=re.compile(f"{plain}(?:[{re.escape(blank)}]+{plain})*" if blank else plain),
            blanks=re.compile(f"[{re.escape(blank)}]+") if blank else None,
            # With no blank and no ignored character, never used (no character is blank).
            blank_run=re.compile(f"[{re.escape(blank + ignored)}]*" if blank or ignored else ""),
            letters=re.compile(f"[{re.escape(letters)}]+") if letters else None,
        )
    return patterns


def _region_boundary(regions: Sequence[tuple[int, int, CatcodeTable]], col: int, length: int) -> int:
    """The first column after `col` where the forced table may change."""
    for start, end, _ in regions:
        if start > col:
            return min(start, length)
        if col < end:
            return min(end, length)
    return length


def _imposed_table(regions: Sequence[tuple[int, int, CatcodeTable]], col: int) -> CatcodeTable | None:
    for start, end, table in regions:
        if start > col:
            break
        if col < end:
            return table
    return None


def _is_brace(group: TexGroup) -> bool:
    return group.bracket


def _last_brace(group: TexGroup) -> TexGroup | None:
    """The last braced group of `group`: the one an extra `}` was perhaps closing."""
    return next((node for node in reversed(group.content) if node.is_bracket_group()), None)


def unknown_command_before(content: Sequence[TexContainer], end: int | None = None) -> TexCommand | None:
    """The unknown command whose last elements are the arguments: `\\foo[a]{b}` before `{`.

    The stream of a `\\write` is skipped: `\\write18{…}`, `\\write\\@auxout{…}`. A
    backwards walk, with no copy: this is called at every group opened, in a
    content that may hold thousands of elements. `end`: only look before that index.
    """
    index = _previous(content, len(content) if end is None else end)
    while index >= 0 and (content[index].is_bracket_group() or content[index].is_option_group()):
        index = _previous(content, index)
    if index < 0:
        return None
    node = content[index]
    before = _previous(content, index)
    if (
        before >= 0
        and content[before].is_command("write")
        and (node.is_command() or STREAM.fullmatch(str(node)))
    ):
        node = content[before]
    if isinstance(node, TexCommand) and node.signature is None and node.role is None:
        return node
    return None


def _previous(content: Sequence[TexContainer], index: int) -> int:
    """The index of the element before `index`, comments skipped; -1 if there is none."""
    index -= 1
    while index >= 0 and content[index].is_comment():
        index -= 1
    return index


# The stream number of a `\write`.
STREAM = re.compile(r"\d+")


def _at(position: Position | None) -> str:
    line, col = position or (0, 0)
    return f"ligne {line}, colonne {col}"


def _starts_verbatim(body: str, end: int) -> bool:
    """A verbatim name followed by `}`, `\\` or a blank is being named, not used."""
    return end < len(body) and body[end] not in NOT_VERBATIM_DELIMITERS


def _environment_argument(body: str, end: int) -> tuple[int, int, str] | None:
    """`(brace, closing, name)` of the `{name}` that follows `\\begin`, if it is on the line."""
    brace = end
    while brace < len(body) and body[brace] in SPACE_CARS:
        brace += 1
    if brace >= len(body) or body[brace] != "{":
        return None
    close = body.find("}", brace)
    name = body[brace + 1 : close].strip(SPACE_CARS) if close >= 0 else ""
    if not name:
        return None
    return brace, close, name


def _closing_delimiter(body: str, start: int, opener: str, closer: str) -> int | None:
    """The column of the `closer` that answers the `opener` at `start`, on the line, nesting included."""
    depth = 1
    for index in range(start + 1, len(body)):
        if body[index] == closer:
            depth -= 1
            if depth == 0:
                return index
        elif body[index] == opener:
            depth += 1
    return None
