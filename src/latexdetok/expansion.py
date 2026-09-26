"""The expanded view of a document: the user's macros replaced by their bodies.

This is level 2 of the expansion (see the roadmap).

Why re-read text. Replacing the `\\beq` node in the tree with the nodes of its
body would not reveal the structure that crosses several uses: `\\beq x \\eeq`
is only an environment once the two bodies are put end to end and read again.
Every use is therefore replaced, in the text, by its body with the parameters
substituted, and the text obtained is read again by the tokeniser, as TeX reads
again what an expansion produces. A body that uses another macro is expanded on
the next pass, up to `MAX_PASSES` passes and `MAX_DEPTH` nested expansions.

What expands, when the mandatory arguments are there:

- a command whose body the registry keeps (`Macro`, see `definitions`). A
  missing `o` becomes `-NoValue-` there, a star or a `t` that was read becomes
  `\\BooleanTrue`, as ltcmd does it;
- a user environment (`EnvironmentMacro`): its begin code is inserted after its
  arguments, its end code before `\\end`. The group of the environment stays, as
  in TeX where `\\begin` opens a group before running the begin code; so do its
  arguments, still bound, so that they appear twice in the view when the code
  uses them.

Nothing else: kernel commands, primitives and package commands stay as they
are. Nothing either where TeX does not expand: the arguments of a definition or
of a `\\let`, the token following `\\noexpand`, `\\string` or `\\ifx`.

Conditionals. The ones `conditions` can decide are decided: primitive
conditionals from the value the tokeniser gave the `\\if…`, conditionals with
arguments (`\\IfBooleanTF`, `\\ifstrempty`) from their first argument, and a
macro that tests what follows (`\\@ifstar{A}{B}`) from what its signature read.
No branch is removed: both stay in the text, and every piece knows which branch
it belongs to (`Condition`). Read again, each branch becomes a `TexBranch`: the
discarded one is read apart, the taken one in the stream (see `TexParser`). A
conditional one of whose pieces would cut a use in two is not decided.

Catcodes. TeX cuts a body into tokens when it defines it, and the arguments
when it reads them at the use. The view does the same: every piece written by a
body is read again under the table of its definition (`\\@dd` of a package stays
one command in the document), the rest under the table in force where it is.

Positions. The view is a `TexFile` on the text produced: positions, `raw_text()`
and `str()` read there as anywhere else, in its own coordinates. It is a text
the package writes, in `\\n` whatever the source's line endings (see `analyse`):
a `\\r\\n` source gives the view of its `\\n` copy, piece for piece. The
correspondence back to the source is kept by pieces of text: a piece copied from
the source (outside the uses, and the arguments) keeps its exact positions; a
piece written by a body points back to the `Expansion` that produced it, and
through it to the use written in the source. That is what will let the
diagnostics of milestone 2 point at the source rather than at the view.

Departures from TeX, none of them affecting the structure:

- a use taken as an argument without braces (`\\frac\\demi x`) is replaced
  between braces, so as to stay one single argument;
- the blanks that follow a use with no argument are eaten as TeX does after a
  control word, but not the line ending;
- an argument that is a blank line (`+m`) becomes a blank line;
- an environment whose final optional argument is missing is not expanded if its
  begin code starts with `[`: read again, that bracket would become the
  argument;
- a space separates a control word from a letter that follows it after
  substitution (`\\medskip#2`): TeX had cut the body beforehand, the text would
  glue `\\medskipCorps` back together. TeX skips that space when reading again;
- a line ending that would make a blank line at the junction of two pieces
  becomes a space: `\\fi%⏎` at the end of a body, or `{…⏎}` at the end of an
  argument, followed by the line ending of the use. For TeX those are an eaten
  line ending and a space, not a `\\par`.
"""

import re
from bisect import bisect_left, bisect_right
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from functools import cache
from itertools import accumulate, pairwise

from latexdetok.analyse import TexFile
from latexdetok.catcodes import CatcodeTable
from latexdetok.classes import Position, TexBranch, TexCommand, TexContainer, TexGroup
from latexdetok.conditions import (
    ARGUMENT_TESTS,
    BOOLEAN_FALSE,
    BOOLEAN_TRUE,
    NO_VALUE,
    PRIMITIVE_CONDITIONALS,
    Role,
    argument_test_value,
    ifnum_test,
)
from latexdetok.definitions import DEFINING_COMMANDS
from latexdetok.logger import logger
from latexdetok.parser import BranchRegion
from latexdetok.signatures import ArgumentSpec, SignatureRegistry

__all__ = ["MAX_DEPTH", "MAX_PASSES", "Condition", "ExpandedFile", "Expansion", "SourceMap", "expand"]

MAX_DEPTH = 8
# Conditionals are sometimes decided one pass after the macros that write them.
MAX_PASSES = 16
# A body that multiplies (`\def\a{\a\a}`) stops the expansion: the view keeps the previous pass.
MAX_GROWTH = 16
GROWTH_MARGIN = 100_000

# Tokens that follow these commands and that TeX does not expand: how many.
NOT_EXPANDED_AFTER = {
    "noexpand": 1,
    "string": 1,
    "meaning": 1,
    "show": 1,
    "expandafter": 1,
    "ifdefined": 1,
    "ifx": 2,
}

# Inside a body: a control sequence (including `\#`), `##`, or a parameter.
BODY_TOKEN = re.compile(r"\\.|##|#[1-9]", re.DOTALL)
BLANKS = " \t"
# End of a text in a control word: an unescaped backslash, then letters.
ENDS_WITH_CONTROL_WORD = re.compile(r"(?<!\\)(?:\\\\)*\\[A-Za-z@]+$")
# Length of the end of text to look in: a copied piece may be a whole chapter.
CONTROL_WORD_TAIL = 80
# Start of an environment code that would take the place of a missing optional argument.
OPENS_WITH_BRACKET = re.compile(r"(?:\s|%[^\n]*\n)*\[")
LETTER = re.compile(r"[A-Za-z@]")
LEADING_LINE_END = re.compile(r"[ \t]*\n")


@dataclass(frozen=True, slots=True)
class Expansion:
    """One use of a macro replaced by its body.

    `start` and `end`: the span, in the source, of the use as written. For a use
    that the body of another macro produced, it is the span of the use that
    produced it, and `parent` is that other expansion; `parent` is set too for a
    use written in an argument another expansion substituted, and which was only
    expanded afterwards. An environment has two (`environment`): `\\begin{name}`
    and its arguments, where the begin code is inserted, then `\\end{name}`, where
    the end code is inserted.
    """

    name: str
    start: Position
    end: Position
    parent: "Expansion | None" = None
    environment: bool = False

    @property
    def depth(self) -> int:
        return 1 if self.parent is None else self.parent.depth + 1


@dataclass(frozen=True, slots=True, eq=False)
class Condition:
    """A decided conditional, both branches of which stay in the view.

    `test`: the command (`ifprof`, `ifnum`, `ifmmode`, `IfBooleanTF`,
    `ifstrempty`, `@ifstar`…); `value`: is the true branch taken? `start` and
    `end`: the span of the test in the source, as for an `Expansion`, and
    `parent` the expansion that wrote it when it comes from a body. Compared by
    identity: one body expanded twice makes two conditionals.
    """

    test: str
    value: bool
    start: Position
    end: Position
    parent: Expansion | None = None


# (conditional, branch taken, branch between braces)
Side = tuple[Condition, bool, bool]


class _Offsets:
    """Going between `(line, column)` positions and indices in the text of a file."""

    def __init__(self, lines: Sequence[str]) -> None:
        # One line ending, `\n`, as in every text the tree writes: a `\r\n` source gives the same view
        # as a `\n` one, and the columns do not move. A list of lines may come without them.
        lines = [line.rstrip("\r\n") + "\n" for line in lines]
        self.text = "".join(lines)
        self._starts = list(accumulate((len(line) for line in lines), initial=0))

    def offset(self, position: Position) -> int:
        line, col = position
        return self._starts[line - 1] + col if line >= 1 else col

    def position(self, offset: int) -> Position:
        index = max(bisect_right(self._starts, offset) - 1, 0)
        return index + 1, offset - self._starts[index]

    @property
    def starts(self) -> list[int]:
        """The index of the start of every line, in order."""
        return self._starts

    def line_end(self, line: int) -> int:
        """The index that follows the line ending of `line`."""
        return self._starts[min(line, len(self._starts) - 1)]


@dataclass(slots=True)
class _Piece:
    """A piece of the text produced: copied from the source starting at `source`, or written by `expansion`.

    `catcodes`: for a piece written by a body, the table to read it under.
    `branches`: the branches of decided conditionals it is part of. `within`: the
    expansion in whose body the piece was written the first time — for a copied
    piece, the one that substituted that argument; for a piece written by a body,
    the one in whose body that use was substituted. It is not necessarily the use
    that surrounds it in the source: the end code of an environment takes up an
    argument written at the `\\begin`, macros included.
    """

    start: int
    end: int
    source: int | None = None
    expansion: Expansion | None = None
    catcodes: CatcodeTable | None = None
    branches: tuple[Side, ...] = ()
    within: Expansion | None = None


class SourceMap:
    """Where every piece of a produced text comes from: the source, or an expansion."""

    def __init__(self, pieces: list[_Piece], source: _Offsets, target: _Offsets) -> None:
        self.pieces = pieces
        self.source = source
        self.target = target
        self._starts = [piece.start for piece in pieces]

    @classmethod
    def identity(cls, lines: Sequence[str]) -> "SourceMap":
        offsets = _Offsets(lines)
        return cls([_Piece(0, len(offsets.text), source=0)], offsets, offsets)

    def index(self, offset: int) -> int:
        return max(bisect_right(self._starts, offset) - 1, 0)

    def piece(self, offset: int) -> _Piece:
        return self.pieces[self.index(offset)]

    def producer(self, offset: int) -> Expansion | None:
        """The expansion that produced the character at `offset`: written by it, or an argument of it."""
        piece = self.piece(offset)
        return piece.expansion if piece.expansion is not None else piece.within

    def expansion_at(self, offset: int) -> Expansion | None:
        """The expansion that wrote the character at `offset`; None if it is copied from the source."""
        return self.piece(offset).expansion

    def source_start(self, offset: int) -> Position:
        piece = self.piece(offset)
        if piece.expansion is not None:
            return piece.expansion.start
        assert piece.source is not None
        return self.source.position(piece.source + offset - piece.start)

    def source_end(self, offset: int) -> Position:
        """The end position (excluded) in the source of a span that ends at `offset`."""
        if offset <= 0:
            return self.source_start(0)
        piece = self.piece(offset - 1)
        if piece.expansion is not None:
            return piece.expansion.end
        assert piece.source is not None
        line, col = self.source.position(piece.source + offset - 1 - piece.start)
        return line, col + 1

    def catcode_regions(self) -> dict[int, list[tuple[int, int, CatcodeTable]]]:
        """Where the produced text reads again under a table other than the document's (see `TexParser`)."""
        regions: dict[int, list[tuple[int, int, CatcodeTable]]] = defaultdict(list)
        for piece in self.pieces:
            if piece.catcodes is None:
                continue
            start = piece.start
            while start < piece.end:
                line, col = self.target.position(start)
                stop = min(piece.end, self.target.line_end(line))
                regions[line].append((col, col + stop - start, piece.catcodes))
                start = stop
        return dict(regions)

    def branch_regions(self) -> list[BranchRegion]:
        """The branches of the produced text: every run of pieces of one branch (see `TexParser`)."""
        regions: list[BranchRegion] = []
        active: dict[tuple[int, bool], tuple[int, Side]] = {}
        for piece in self.pieces:
            present = {(id(side[0]), side[1]): side for side in piece.branches}
            for key in [key for key in active if key not in present]:
                start, side = active.pop(key)
                regions.append(self._region(start, piece.start, side))
            for key, side in present.items():
                active.setdefault(key, (piece.start, side))
        end = self.pieces[-1].end if self.pieces else 0
        regions.extend(self._region(start, end, side) for start, side in active.values())
        return regions

    def _region(self, start: int, end: int, side: Side) -> BranchRegion:
        condition, taken, braced = side
        inner_start, inner_end = (start + 1, end - 1) if braced else (start, end)
        position = self.target.position
        return BranchRegion(
            position(start),
            position(inner_start),
            position(inner_end),
            position(end),
            condition,
            taken,
            braced,
        )


class ExpandedFile(TexFile):
    """An expanded document: a `TexFile` on the text produced, tied back to its source.

    `container`, `diagnostics` and the queries are those of the expanded text;
    `source_span`, `origin` and `expansions` lead back to the source; `conditions`
    and `branches` say which conditionals were decided, and which side every
    element is on.
    """

    def __init__(
        self,
        source: TexFile,
        lines: Sequence[str],
        source_map: SourceMap,
        expansions: list[Expansion],
        conditions: list[Condition],
    ) -> None:
        super().__init__(list(lines), name=source.name)
        # Same inclusions and same master document as the source: we compile the same file.
        self.src_file = source.src_file
        self.encoding = source.encoding
        self.source = source
        self.source_map = source_map
        self.expansions = expansions
        self.conditions = conditions
        self.catcode_regions = source_map.catcode_regions()
        self.branch_regions = source_map.branch_regions()

    def source_position(self, position: Position) -> Position:
        """The position in the source; for a text written by a body, the start of the use."""
        return self.source_map.source_start(self.source_map.target.offset(position))

    def source_span(self, node: TexContainer) -> tuple[Position, Position]:
        """The span in the source of what produced `node`."""
        if node.start_position is None or node.end_position is None:
            raise TypeError("an element with no position cannot be located")
        target = self.source_map.target
        start, end = target.offset(node.start_position), target.offset(node.end_position)
        source_start = self.source_map.source_start(start)
        return source_start, self.source_map.source_end(end) if end > start else source_start

    def origin(self, node: TexContainer) -> Expansion | None:
        """The expansion whose body wrote the start of `node`; None if it is written in the source."""
        if node.start_position is None:
            return None
        return self.source_map.expansion_at(self.source_map.target.offset(node.start_position))

    def branches(self, node: TexContainer) -> tuple[tuple[Condition, bool], ...]:
        """The decided branches where `node` starts: (conditional, taken?), from the outer to the inner.

        It also holds for an element of a taken branch that melted into the stream.
        """
        if node.start_position is None:
            return ()
        piece = self.source_map.piece(self.source_map.target.offset(node.start_position))
        return tuple((condition, taken) for condition, taken, _ in piece.branches)


# The value of a parameter: the span of the text to copy, the text written, or nothing.
Value = tuple[int, int] | str | None


@dataclass(frozen=True, slots=True)
class _Use:
    """A use to expand, in indices in the text of the pass.

    `start` to `end` is replaced by `body` with the parameters substituted
    (`values`); for the code of an environment, the span is empty (an insertion),
    and `opens` is the start of its `\\begin`, noted so as not to expand it a
    second time. A macro that tests what follows writes both its branches:
    `body`, taken if `value`, then `alternative`. `kept`: the discarded branches
    the use crosses to read its arguments, copied before the body.
    """

    start: int
    end: int  # end of the use, eaten blanks included
    body: str
    catcodes: CatcodeTable
    values: tuple[Value, ...]
    braced: bool
    expansion: Expansion
    opens: int | None = None
    alternative: str | None = None
    test: str = ""
    value: bool = True
    kept: tuple[tuple[int, int], ...] = ()


@dataclass(frozen=True, slots=True)
class _Part:
    """A piece of a conditional: a branch, or what TeX reads without running it (`taken` None)."""

    start: int
    end: int
    taken: bool | None
    braced: bool = False


@dataclass(frozen=True, slots=True)
class _Decision:
    """A conditional to decide: from `start` to `end`, in pieces; the test ends at `test_end`."""

    start: int
    end: int
    test: str
    value: bool
    test_end: int
    parts: tuple[_Part, ...]

    @property
    def opens(self) -> int:
        return self.start


def expand(tex: TexFile, max_depth: int = MAX_DEPTH) -> ExpandedFile:
    """Expand the user's macros of an analysed file, and decide its conditionals.

    The view is analysed as the source was (`follow_inputs` included). With
    nothing to expand, it has the text of the source. Every pass expands the uses
    and decides the conditionals of the previous one, as long as any are left; an
    expansion deeper than `max_depth` does not happen, which stops a recursion.
    """
    source_map = SourceMap.identity(tex.lines)
    limit = MAX_GROWTH * len(source_map.source.text) + GROWTH_MARGIN
    current: TexFile = tex
    expansions: list[Expansion] = []
    conditions: list[Condition] = []
    # Starts of the expanded environments and of the decided conditionals, in the text of the pass.
    done: set[int] = set()
    for _ in range(MAX_PASSES):
        uses = _collect(current.container, source_map, done, current.signatures, max_depth)
        if not uses:
            break
        writer = _Writer(source_map, uses, done)
        writer.write_range(0, len(source_map.target.text))
        text = writer.text()
        if len(text) > limit:
            logger.warning("%s: expansion stopped, the text goes beyond %d characters", tex.name, limit)
            break
        lines = text.splitlines(keepends=True)
        source_map = SourceMap(writer.pieces, source_map.source, _Offsets(lines))
        expansions = [*expansions, *writer.expansions]
        conditions = [*conditions, *writer.conditions]
        done = writer.done
        current = ExpandedFile(tex, lines, source_map, expansions, conditions)
        current.analyse(follow_inputs=tex.follow_inputs)
    if isinstance(current, ExpandedFile):
        return current
    view = ExpandedFile(tex, tex.lines, source_map, [], [])
    view.analyse(follow_inputs=tex.follow_inputs)
    return view


@dataclass(slots=True)
class _Pending:
    """A primitive conditional open while collecting: its `\\if…`, its `\\else`, and whether it decides."""

    opener: TexCommand
    otherwise: TexCommand | None = None
    decidable: bool = True


def _collect(
    root: TexGroup, source_map: SourceMap, done: set[int], registry: SignatureRegistry, max_depth: int
) -> list[_Use | _Decision]:
    """Uses to expand and conditionals to decide in the tree of one pass, in the order of the text."""
    uses: list[_Use | _Decision] = []
    pending: list[_Pending] = []

    def visit(group: TexGroup) -> None:
        content = group.content
        skip_until = -1
        token_arguments: set[int] = set()
        for index, node in enumerate(content):
            if index <= skip_until:
                continue
            if isinstance(node, TexBranch):
                if node.taken:
                    visit(node)  # a discarded branch is not run: nothing in it is expanded
                continue
            if isinstance(node, TexGroup):
                environment = _environment_uses(node, source_map, done, max_depth)
                if environment is not None:
                    uses.append(environment[0])
                visit(node)
                if environment is not None:
                    uses.append(environment[1])
                continue
            if not isinstance(node, TexCommand):
                continue
            if node.role is not None:
                decision = _match(pending, node, source_map, done, registry)
                if decision is not None:
                    uses.append(decision)
            name = node.base_name
            if name in DEFINING_COMMANDS and node.arguments is not None:
                # Names and bodies: expanded at the use of what they define, not here.
                skip_until = _last_argument(content, index, node.arguments)
                continue
            if name in NOT_EXPANDED_AFTER:
                skip_until = _following(content, index, NOT_EXPANDED_AFTER[name])
                continue
            if name in ARGUMENT_TESTS:
                decision = _argument_decision(node, content, index, source_map, done)
                if decision is not None:
                    uses.append(decision)
                continue
            if node.arguments:
                token_arguments.update(id(arg) for arg in node.arguments if isinstance(arg, TexCommand))
            use = _use(node, id(node) in token_arguments, source_map, max_depth, content, index)
            if use is not None:
                uses.append(use)

    visit(root)
    # The begin code of an environment is inserted after its arguments, visited after it.
    uses.sort(key=lambda use: use.start)
    return _without_crossings(uses)


def _match(
    pending: list[_Pending],
    node: TexCommand,
    source_map: SourceMap,
    done: set[int],
    registry: SignatureRegistry,
) -> _Decision | None:
    """Match `\\if…`, `\\else` and `\\fi`; return the conditional this `\\fi` closes, if it decides."""
    if node.role is Role.IF:
        name = node.base_name
        if not (name in PRIMITIVE_CONDITIONALS or registry.is_boolean(name[2:])):
            # Perhaps not a conditional: the `\fi` that follow would go to other `\if`.
            for entry in pending:
                entry.decidable = False
        pending.append(_Pending(node, decidable=node.value is not None))
    elif node.role is Role.ELSE and pending:
        if pending[-1].otherwise is None:
            pending[-1].otherwise = node
        else:
            pending[-1].decidable = False
    elif node.role is Role.OR and pending:
        pending[-1].decidable = False
    elif node.role is Role.FI and pending:
        entry = pending.pop()
        if entry.decidable:
            return _primitive_decision(entry, node, source_map, done)
    return None


def _primitive_decision(
    entry: _Pending, fi: TexCommand, source_map: SourceMap, done: set[int]
) -> _Decision | None:
    offsets = source_map.target
    opener, otherwise = entry.opener, entry.otherwise
    if opener.value is None or not _positioned(opener, fi, otherwise):
        return None
    start = offsets.offset(opener.start_position)  # type: ignore[arg-type]
    if start in done:
        return None
    test_end = offsets.offset(opener.end_position)  # type: ignore[arg-type]
    if opener.base_name == "ifnum":
        line_end = offsets.text.find("\n", test_end)
        test = ifnum_test(offsets.text[test_end:line_end])
        if test is None:
            return None
        test_end += test[1]
    fi_start, fi_end = offsets.offset(fi.start_position), offsets.offset(fi.end_position)  # type: ignore[arg-type]
    value = opener.value
    if otherwise is None:
        parts = [_Part(start, test_end, None), _Part(test_end, fi_start, value)]
    else:
        else_start = offsets.offset(otherwise.start_position)  # type: ignore[arg-type]
        else_end = offsets.offset(otherwise.end_position)  # type: ignore[arg-type]
        parts = [
            _Part(start, test_end, None),
            _Part(test_end, else_start, value),
            _Part(else_start, else_end, None),
            _Part(else_end, fi_start, not value),
        ]
    parts.append(_Part(fi_start, fi_end, None))
    if any(part.start > part.end for part in parts) or any(a.end != b.start for a, b in pairwise(parts)):
        return None
    return _Decision(start, fi_end, opener.base_name, value, test_end, tuple(parts))


def _positioned(*nodes: TexContainer | None) -> bool:
    return all(
        node is None or (node.start_position is not None and node.end_position is not None) for node in nodes
    )


def _argument_decision(
    node: TexCommand, content: list[TexContainer], index: int, source_map: SourceMap, done: set[int]
) -> _Decision | None:
    """`\\IfBooleanTF{b}{A}{B}`, `\\ifstrempty{t}{A}{B}` and their relatives, if their test decides."""
    true_index, false_index = ARGUMENT_TESTS[node.base_name]
    count = 1 + (true_index is not None) + (false_index is not None)
    if node.arguments is not None:
        arguments: list[TexContainer | None] = list(node.arguments[:count])
    else:
        # A package command, with no signature: its arguments are the groups that follow it.
        following = [sibling for sibling in content[index + 1 :] if not sibling.is_comment()][:count]
        arguments = [sibling if sibling.is_bracket_group() else None for sibling in following]
    if len(arguments) < count or None in arguments or not _positioned(node, *arguments):
        return None
    test, branches = arguments[0], arguments[1:]
    if not all(isinstance(branch, TexGroup) and branch.bracket for branch in branches):
        return None
    offsets = source_map.target
    start = offsets.offset(node.start_position)  # type: ignore[arg-type]
    if start in done:
        return None
    assert test is not None
    if isinstance(test, TexGroup) and test.bracket:
        text = offsets.text[offsets.offset(test.inner_start) : offsets.offset(test.inner_end)]  # type: ignore[arg-type]
    else:
        text = str(test)
    value = argument_test_value(node.base_name, text)
    if value is None:
        return None
    parts = [_Part(start, offsets.offset(branches[0].start_position), None)]  # type: ignore[union-attr,arg-type]
    for position, branch in enumerate(branches, start=1):
        assert branch is not None and branch.start_position is not None and branch.end_position is not None
        taken = value if position == true_index else not value
        branch_start, branch_end = offsets.offset(branch.start_position), offsets.offset(branch.end_position)
        if parts[-1].end != branch_start:
            parts.append(_Part(parts[-1].end, branch_start, None))
        parts.append(_Part(branch_start, branch_end, taken, braced=True))
    test_end = offsets.offset(test.end_position)  # type: ignore[arg-type]
    return _Decision(start, parts[-1].end, node.base_name, value, test_end, tuple(parts))


def _without_crossings(uses: list[_Use | _Decision]) -> list[_Use | _Decision]:
    """Drop the conditionals one of whose pieces would cut a use in two: better not to decide."""
    decisions = [use for use in uses if isinstance(use, _Decision)]
    if not decisions:
        return uses
    starts = [use.start for use in uses]
    rejected: set[int] = set()
    for decision in decisions:
        position = bisect_left(starts, decision.start)
        while position < len(uses) and uses[position].start < decision.end:
            other = uses[position]
            position += 1
            if other is decision:
                continue
            if not any(part.start <= other.start and other.end <= part.end for part in decision.parts):
                rejected.add(id(decision))
                break
    return [use for use in uses if id(use) not in rejected]


def _use(
    command: TexCommand,
    braced: bool,
    source_map: SourceMap,
    max_depth: int,
    content: list[TexContainer],
    index: int,
) -> _Use | None:
    macro = command.macro
    if macro is None or command.start_position is None or command.end_position is None:
        return None
    found = command.arguments or []
    present = [argument for argument in found if argument is not None]
    offsets = source_map.target
    start = offsets.offset(command.start_position)
    alternative, test, value = None, "", True
    if macro.otherwise is not None:
        # The branches replace the command alone: its arguments are the ones the branch will read.
        taken = _star_or_option(command)
        if taken is None:
            return None
        alternative, value = macro.otherwise, taken
        test = "@ifstar" if command.signature is not None and command.signature.starred else "@ifnextchar"
        end = offsets.offset(command.end_position)
        values: tuple[Value, ...] = ()
    else:
        if not _complete(macro.parameters, found):
            return None
        last = present[-1] if present else command
        assert last.end_position is not None
        end = offsets.offset(last.end_position)
        values = _values(macro.parameters, found, source_map, star=command.star if macro.starred else None)
    parent = source_map.producer(start)
    expansion = Expansion(macro.name, source_map.source_start(start), source_map.source_end(end), parent)
    if expansion.depth > max_depth:
        return None
    if not present and command.content[-1:].isalpha():
        text = offsets.text
        while end < len(text) and text[end] in BLANKS:
            end += 1
    between = content[index + 1 : _last_argument(content, index, found)] if present else []
    kept = tuple(
        _inner(node, source_map) for node in between if isinstance(node, TexBranch) and not node.taken
    )
    return _Use(
        start,
        end,
        macro.body,
        macro.catcodes,
        values,
        braced,
        expansion,
        alternative=alternative,
        test=test,
        value=value,
        kept=kept,
    )


def _star_or_option(command: TexCommand) -> bool | None:
    """Has the use of a macro that tests what follows the star or the optional? None if it is not called."""
    signature = command.signature
    if signature is None or (signature.arguments and command.arguments is None):
        return None
    if signature.starred:
        return command.star
    if not command.arguments:
        return None
    return command.arguments[0] is not None


def _complete(parameters: tuple[ArgumentSpec, ...], found: list[TexContainer | None]) -> bool:
    """The mandatory arguments are there, one per parameter."""
    if len(found) != len(parameters):
        return False
    return not any(
        argument is None and spec.kind == "m" for spec, argument in zip(parameters, found, strict=True)
    )


def _values(
    parameters: tuple[ArgumentSpec, ...],
    found: list[TexContainer | None],
    source_map: SourceMap,
    star: bool | None,
) -> tuple[Value, ...]:
    """What each parameter receives: `#1` is the star when `star` is not None, as for ltcmd."""
    values: list[Value] = [] if star is None else [BOOLEAN_TRUE if star else BOOLEAN_FALSE]
    for spec, argument in zip(parameters, found, strict=True):
        if spec.kind == "t":
            values.append(BOOLEAN_TRUE if argument is not None else BOOLEAN_FALSE)
        elif argument is None:
            values.append(spec.default if spec.kind == "O" else NO_VALUE if spec.kind == "o" else None)
        elif argument.is_par():
            values.append("\n\n")
        else:
            values.append(_inner(argument, source_map))
    return tuple(values)


def _environment_uses(
    group: TexGroup, source_map: SourceMap, done: set[int], max_depth: int
) -> tuple[_Use, _Use] | None:
    """The insertions of the begin and end code of a user environment."""
    macro = group.macro
    if (
        macro is None
        or group.start_position is None
        or group.end_position is None
        or group.inner_start is None
        or group.inner_end is None
    ):
        return None
    offsets = source_map.target
    start = offsets.offset(group.start_position)
    if start in done or not _complete(macro.parameters, group.arguments):
        return None
    found = group.arguments
    if found and found[-1] is None and OPENS_WITH_BRACKET.match(macro.begin):
        return None
    present = [argument for argument in found if argument is not None]
    last = present[-1].end_position if present else group.inner_start
    assert last is not None
    begin_at, end_at = offsets.offset(last), offsets.offset(group.inner_end)
    end = offsets.offset(group.end_position)
    values = _values(macro.parameters, found, source_map, star=None)
    opening = Expansion(
        macro.name,
        source_map.source_start(start),
        source_map.source_end(begin_at),
        source_map.producer(start),
        environment=True,
    )
    if opening.depth > max_depth:
        return None
    closing = Expansion(
        macro.name,
        source_map.source_start(end_at),
        source_map.source_end(end),
        source_map.producer(end_at),
        environment=True,
    )
    end_values = values if macro.end_arguments else ()
    return (
        _Use(begin_at, begin_at, macro.begin, macro.catcodes, values, False, opening, start),
        _Use(end_at, end_at, macro.end, macro.catcodes, end_values, False, closing),
    )


def _inner(argument: TexContainer, source_map: SourceMap) -> tuple[int, int]:
    """What TeX substitutes: the inside of a group between braces or brackets, otherwise the token."""
    offsets = source_map.target
    if isinstance(argument, TexGroup) and (argument.bracket or argument.option):
        assert argument.inner_start is not None and argument.inner_end is not None
        return offsets.offset(argument.inner_start), offsets.offset(argument.inner_end)
    assert argument.start_position is not None and argument.end_position is not None
    return offsets.offset(argument.start_position), offsets.offset(argument.end_position)


def _last_argument(content: list[TexContainer], index: int, arguments: list[TexContainer | None]) -> int:
    """The index of the last bound argument: what precedes it is the command's (`\\def\\x#1\\relax{}`)."""
    present = [argument for argument in arguments if argument is not None]
    if not present:
        return index
    return next(
        (position for position in range(index + 1, len(content)) if content[position] is present[-1]), index
    )


def _following(content: list[TexContainer], index: int, count: int) -> int:
    """The index of the `count`-th element that follows, comments not counted."""
    position = index
    while count and position + 1 < len(content):
        position += 1
        if not content[position].is_comment():
            count -= 1
    return position


@cache
def _template(body: str) -> tuple[str | int, ...]:
    """The body in pieces: text as it stands, or a parameter number."""
    parts: list[str | int] = []
    literal: list[str] = []
    cursor = 0
    for match in BODY_TOKEN.finditer(body):
        token = match.group()
        if token.startswith("\\"):
            continue
        literal.append(body[cursor : match.start()])
        cursor = match.end()
        if token == "##":
            literal.append("#")
            continue
        parts.append("".join(literal))
        literal = []
        parts.append(int(token[1]))
    literal.append(body[cursor:])
    parts.append("".join(literal))
    return tuple(part for part in parts if part != "")


def _combine(outer: tuple[Side, ...], inner: tuple[Side, ...]) -> tuple[Side, ...]:
    """The branches of `outer`, then those of `inner` that are not already there."""
    if not inner:
        return outer
    known = {(id(side[0]), side[1]) for side in outer}
    return outer + tuple(side for side in inner if (id(side[0]), side[1]) not in known)


class _Writer:
    """Writes the text of a passage, and notes where every piece comes from."""

    def __init__(self, previous: SourceMap, uses: list[_Use | _Decision], done: set[int]) -> None:
        self._previous = previous
        self._text = previous.target.text
        self._uses = uses
        self._starts = [use.start for use in uses]
        # Starts of expanded environments and decided conditionals, to be found again in the written text.
        self._done = sorted(done | {use.opens for use in uses if use.opens is not None})
        self.done: set[int] = set()
        self.parts: list[str] = []
        self.pieces: list[_Piece] = []
        self.expansions: list[Expansion] = []
        self.conditions: list[Condition] = []
        self._length = 0
        # The branches we are writing in right now.
        self._branches: tuple[Side, ...] = ()
        # The last expansion started or finished: it is what parts what it glued back together.
        self._last_expansion: Expansion | None = None
        # The expansion whose body we are writing: the arguments copied into it go back to it;
        # and the one around it, in whose body this use was substituted.
        self._within: Expansion | None = None
        self._context: Expansion | None = None
        # The written text ends with a line ending and blanks: another one would make a blank line.
        self._blank_tail = False

    def text(self) -> str:
        """The written text, ended by a line ending like every line read again (see `_Offsets`)."""
        text = "".join(self.parts)
        if self.pieces and not text.endswith("\n"):
            # A junction may have turned the last line ending into a space: the last piece takes it back.
            text += "\n"
            self.pieces[-1].end += 1
            self._length += 1
        return text

    def write_range(self, start: int, end: int) -> None:
        """The text from `start` to `end`, uses expanded and conditionals decided."""
        cursor = start
        index = bisect_left(self._starts, start)
        while index < len(self._uses) and self._uses[index].start < end:
            use = self._uses[index]
            index += 1
            if use.start < cursor or use.end > end:
                continue  # inside an argument of a use already written: it was written with it
            self._copy(cursor, use.start)
            if isinstance(use, _Decision):
                self._decide(use)
            else:
                self._expand(use)
            cursor = use.end
        self._copy(cursor, end)

    def _decide(self, decision: _Decision) -> None:
        previous = self._previous
        condition = Condition(
            decision.test,
            decision.value,
            previous.source_start(decision.start),
            previous.source_end(decision.test_end),
            previous.producer(decision.start),
        )
        self.conditions.append(condition)
        for part in decision.parts:
            if part.taken is None:
                self._copy(part.start, part.end)
                continue
            outer = self._branches
            self._branches = _combine(outer, ((condition, part.taken, part.braced),))
            if not part.taken:
                self._copy(part.start, part.end)  # discarded: copied as it stands, nothing in it is expanded
            elif part.braced:
                self._copy(part.start, part.start + 1)
                self.write_range(part.start + 1, part.end - 1)
                self._copy(part.end - 1, part.end)
            else:
                self.write_range(part.start, part.end)
            self._branches = outer

    def _expand(self, use: _Use) -> None:
        expansion = use.expansion
        self.expansions.append(expansion)
        self._last_expansion = expansion
        outer = self._branches
        for kept_start, kept_end in use.kept:
            self._copy(kept_start, kept_end)
        # Written where the use was: in the branches it was in.
        self._branches = _combine(outer, self._previous.piece(use.start).branches)
        outer_within, outer_context = self._within, self._context
        self._context, self._within = self._within, expansion
        if use.braced:
            self._write("{", expansion)
        if use.alternative is None:
            self._write_body(use.body, use)
        else:
            condition = Condition(use.test, use.value, expansion.start, expansion.end, expansion.parent)
            self.conditions.append(condition)
            inside = self._branches
            for text, taken in ((use.body, use.value), (use.alternative, not use.value)):
                self._branches = _combine(inside, ((condition, taken, False),))
                self._write_body(text, use)
            self._branches = inside
        if use.braced:
            self._write("}", expansion)
        self._branches = outer
        self._within, self._context = outer_within, outer_context
        self._last_expansion = expansion

    def _write_body(self, body: str, use: _Use) -> None:
        for part in _template(body):
            if isinstance(part, str):
                self._write(part, use.expansion, use.catcodes)
                continue
            if part > len(use.values):
                self._write(f"#{part}", use.expansion, use.catcodes)  # a parameter the definition lacks
                continue
            value = use.values[part - 1]
            if isinstance(value, tuple):
                self.write_range(*value)
            elif value is not None:
                self._write(value, use.expansion, use.catcodes)

    def _copy(self, start: int, end: int) -> None:
        """Copy the text of the previous pass, with what we know of where it comes from."""
        if start >= end:
            return
        if self._last_expansion is not None:
            self._separate(self._text[start], self._last_expansion)
        first = bisect_left(self._done, start)
        for done in self._done[first : bisect_left(self._done, end)]:
            self.done.add(self._length + done - start)
        self.parts.append(self._join(self._text[start:end]))
        pieces = self._previous.pieces
        index = self._previous.index(start)
        while start < end:
            piece = pieces[index]
            stop = min(end, piece.end)
            branches = _combine(self._branches, piece.branches)
            if piece.source is not None:
                within = piece.within if piece.within is not None else self._within
                source = piece.source + start - piece.start
                self._note(stop - start, source=source, branches=branches, within=within)
            else:
                self._note(
                    stop - start,
                    expansion=piece.expansion,
                    catcodes=piece.catcodes,
                    branches=branches,
                    within=piece.within,
                )
            start = stop
            index += 1

    def _write(self, text: str, expansion: Expansion, catcodes: CatcodeTable | None = None) -> None:
        if not text:
            return
        self._separate(text[0], expansion)
        self.parts.append(self._join(text))
        self._note(
            len(text), expansion=expansion, catcodes=catcodes, branches=self._branches, within=self._context
        )

    def _join(self, text: str) -> str:
        """`text` with no blank line at its junction with what is written; the same length."""
        if self._blank_tail:
            match = LEADING_LINE_END.match(text)
            if match is not None:
                text = text[: match.end() - 1] + " " + text[match.end() :]
        newline = text.rfind("\n")
        if newline >= 0:
            self._blank_tail = not text[newline + 1 :].strip(BLANKS)
        elif text.strip(BLANKS):
            self._blank_tail = False
        return text

    def _separate(self, following: str, expansion: Expansion) -> None:
        tail = self.parts[-1][-CONTROL_WORD_TAIL:] if self.parts else ""
        if LETTER.match(following) and ENDS_WITH_CONTROL_WORD.search(tail):
            self.parts.append(" ")
            # Written for `expansion`: in its body, or just after it in the body around it.
            within = self._context if expansion is self._within else self._within
            self._note(1, expansion=expansion, branches=self._branches, within=within)

    def _note(
        self,
        length: int,
        source: int | None = None,
        expansion: Expansion | None = None,
        catcodes: CatcodeTable | None = None,
        branches: tuple[Side, ...] = (),
        within: Expansion | None = None,
    ) -> None:
        if length <= 0:
            return
        if self.pieces:
            last = self.pieces[-1]
            contiguous_copy = source is not None and last.source == source - (last.end - last.start)
            same_writer = (
                source is None
                and last.source is None
                and last.expansion is expansion
                and last.catcodes is catcodes
            )
            if (contiguous_copy or same_writer) and last.branches == branches and last.within is within:
                last.end += length
                self._length += length
                return
        self.pieces.append(
            _Piece(self._length, self._length + length, source, expansion, catcodes, branches, within)
        )
        self._length += length
