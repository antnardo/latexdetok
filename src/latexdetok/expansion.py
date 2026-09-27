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
of a `\\let`, the token following `\\noexpand`, `\\string` or `\\ifx`. Nor what
the caller keeps (`keep`), nor, for a view meant to be written back
(`writable`), a body TeX would not run the same in the document: written under
`\\makeatletter`, `\\@title` is one command in the body, `\\@` and “title” in
the document; an `\\ignorespacesafterend` is only read by the `\\end` of its
environment, which the written view no longer has (see `compilable`); and the
`-NoValue-` a use passes on to a command that stays is text once written, where
ltcmd passed its own marker.

Definitions made by a body. `\\newcommand{\\setauthor}[1]{\\renewcommand{\\theauthor}{#1}}`
defines `\\theauthor` where `\\setauthor` is used, not before (see `TexParser`).
A pass expands the uses with the definitions the tokeniser learned in the text
of the previous pass: a use of `\\theauthor` that follows `\\setauthor{Alice}`
waits for the next pass, where the `\\renewcommand` written by the body has been
read. Those after it wait with it; the ones before are expanded with the old
definition, as TeX does.

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
- the blanks that follow a use ending on a control word (`\\pkg`, or the
  `\\LaTeX` of `\\twice\\LaTeX`) are eaten as TeX does, and its line ending is
  commented out: a `%` the body writes before it keeps the lines of the source,
  where the line ending alone would become a space after the body;
- a blank that follows a use ending on a brace is a space TeX has already read:
  if the body ends on a control word that reads nothing after it (`\\itshape`,
  `\\textdegree`), the body writes `{}` after it, which the control word would
  otherwise swallow. Not after one that reads what follows (`\\item` looks for
  its `[`, skipping blanks; `\\ignorespaces`), where TeX skips that space too,
  nor after an unknown one, which could do either;
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
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass
from functools import cache
from io import StringIO
from itertools import accumulate, pairwise

from latexdetok.analyse import TexFile
from latexdetok.catcodes import CatcodeTable
from latexdetok.characters import is_control_word
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
from latexdetok.export import Edit, rewrite
from latexdetok.logger import logger
from latexdetok.parser import BranchRegion
from latexdetok.signatures import ArgumentSpec, EnvironmentMacro, Macro, SignatureRegistry

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

# A definition written in a body, and the name it defines, or the parameter that gives it.
DEFINED_COMMAND = re.compile(
    r"\\(?:(?:re)?newcommand|providecommand|DeclareRobustCommand|[gex]?def|let|futurelet"
    r"|(?:New|Renew|Provide|Declare)DocumentCommand|NewExpandableDocumentCommand"
    r"|(?:New|Renew|Declare)CommandCopy)(?![A-Za-z@])\*?\s*\{?\s*(\\(?:[A-Za-z@]+|.)|#[1-9])"
)
DEFINED_ENVIRONMENT = re.compile(
    r"\\(?:(?:re)?newenvironment\*?|(?:New|Renew|Provide|Declare)DocumentEnvironment)"
    r"\s*\{\s*([^{}\s#]+|#[1-9])\s*\}"
)
CONTROL_WORD = re.compile(r"\\([A-Za-z@]+)")
BEGIN = re.compile(r"\\begin\s*\{\s*([^{}\s]+)\s*\}")
NAMED = re.compile(r"\\([A-Za-z@]+|.)")
# Beyond that many macros called by a body, we stop looking at what they define.
MAX_CALLED = 200
# The conditionals the view writes itself, both branches after the use of a macro that tests what follows.
LOOKAHEAD_TESTS = frozenset({"@ifstar", "@ifnextchar"})
# Control words without arguments that look at what follows all the same, and decide about a space.
READS_AHEAD = frozenset({"ignorespaces", "xspace"})
# Sets a flag the `\end` of the environment reads after its group: then it ignores the spaces that follow.
IGNORE_AFTER_END = "ignorespacesafterend"
SETS_IGNORE_AFTER_END = re.compile(r"\\ignorespacesafterend(?![A-Za-z@])")
# The same at the end of a code, where only blanks and comments follow it.
ENDS_ON_IGNORE_AFTER_END = re.compile(r"\\ignorespacesafterend(?![A-Za-z@])(?:\s|%[^\n]*)*\Z")


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
    argument written at the `\\begin`, macros included. `novalue`: the
    `-NoValue-` a body writes for a missing optional argument, which is not
    ltcmd's own marker once written out.
    """

    start: int
    end: int
    source: int | None = None
    expansion: Expansion | None = None
    catcodes: CatcodeTable | None = None
    branches: tuple[Side, ...] = ()
    within: Expansion | None = None
    novalue: bool = False


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

    def source_span(self, start: int, end: int) -> tuple[Position, Position]:
        """The span in the source of what produced the text from `start` to `end` (excluded).

        Within one piece: its text if it is copied, the use otherwise. Across
        several, the smallest span that covers what produced each of them. Their
        two ends alone would give an end before the start for a body that writes
        its arguments back in another order (`#2#1`), and would cut the use of a
        text that ends on an argument in two: a use whose arguments are copied
        in place is covered whole where the span would cut it, or where its body
        writes them out of order.

        A piece taken up away from where it is written — an argument that the
        end code of an environment writes again — counts as the use that took
        it up (`within`) if that use wrote part of the text too. The braces that
        `\\ifstrempty{#1}` writes around the title of a theorem lead back to the
        `\\end`, while the math of the title alone reads where it is typed.
        """
        first = self.source_start(start)
        if end <= start:
            return first, first
        low, high = self.index(start), self.index(end - 1)
        if low == high:
            return first, self.source_end(end)
        pieces = self.pieces[low : high + 1]
        writers = {id(piece.expansion) for piece in pieces if piece.expansion is not None}
        spans: list[tuple[Position, Position]] = []
        # The uses whose arguments are copied in place, and whether their body writes them out of order.
        in_place: dict[int, tuple[Expansion, bool]] = {}
        previous: _Piece | None = None
        for piece in pieces:
            span = self.source_start(max(start, piece.start)), self.source_end(min(end, piece.end))
            use = piece.within
            inside = use is not None and use.start <= span[0] and span[1] <= use.end
            if use is not None and not inside and id(use) in writers:
                span = use.start, use.end
            elif piece.source is not None and use is not None and inside:
                out_of_order = (
                    previous is not None
                    and previous.source is not None
                    and piece.source < previous.source + previous.end - previous.start
                )
                in_place[id(use)] = use, out_of_order or in_place.get(id(use), (use, False))[1]
            spans.append(span)
            previous = piece
        first, last = min(span[0] for span in spans), max(span[1] for span in spans)
        growing = True
        while growing:
            growing = False
            for use, out_of_order in in_place.values():
                cut = not (use.start <= first and last <= use.end)
                if (cut or out_of_order) and (use.start < first or last < use.end):
                    first, last = min(first, use.start), max(last, use.end)
                    growing = True
        return first, last

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
    `source_span`, `source_text`, `origin` and `expansions` lead back to the
    source; `conditions` and `branches` say which conditionals were decided,
    and which side every element is on. `compilable()` writes it back as a
    source.
    """

    def __init__(
        self,
        source: TexFile,
        lines: Sequence[str],
        source_map: SourceMap,
        expansions: list[Expansion],
        conditions: list[Condition],
        done: Collection[int] = (),
        writable: bool = False,
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
        # Where the expanded environments and the decided conditionals start, in the text of the view.
        self._done = frozenset(done)
        self.writable = writable

    def source_position(self, position: Position) -> Position:
        """The position in the source; for a text written by a body, the start of the use."""
        return self.source_map.source_start(self.source_map.target.offset(position))

    def source_span(self, node: TexContainer) -> tuple[Position, Position]:
        """The span in the source of what produced `node` (see `SourceMap.source_span`)."""
        if node.start_position is None or node.end_position is None:
            raise TypeError("an element with no position cannot be located")
        target = self.source_map.target
        return self.source_map.source_span(
            target.offset(node.start_position), target.offset(node.end_position)
        )

    def source_text(self, node: TexContainer) -> str:
        """What produced `node`, as the source writes it: the text of `source_span`, line endings included.

        `raw_text(node)` is the text of the view, where the macros are expanded;
        this is what the user typed.
        """
        if node.rootfile is not self:
            raise ValueError("this element does not belong to this view")
        return self.source.text_between(*self.source_span(node))

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

    def compilable(self, edits: Iterable[Edit] = ()) -> str:
        """The view written back as a source that TeX compiles like the original; `edits` apply with it.

        The view keeps, for the analysis, what TeX would run twice or read
        otherwise once written; this is where it goes. A user environment whose
        code was inserted becomes a group, `{…}`, without its arguments, which
        the code has taken up. Its `\\end` ran `\\ignorespaces` after the group if
        `\\ignorespacesafterend` asked for it: one that ends the environment moves
        after the brace, as `\\ignorespaces` — left inside, the global flag it sets
        would make the next `\\end{…}` of the document eat its spaces. The
        `\\@doendpe` of a list or a `center` that ends the environment (no indent
        after it) crosses the brace since LaTeX 2024-11-01; with an older kernel,
        the next paragraph is indented. A conditional with arguments that was
        decided (`\\IfValueTF`, `\\IfBooleanTF`, `\\ifstrempty`…) gives way to the
        inside of its branch taken: `-NoValue-` written out is not ltcmd's marker, and
        `\\IfValueTF{-NoValue-}` would take the other branch. For the same reason,
        a `-NoValue-` passed on to a command that stays (a package's, expl3's, one
        `keep` names) cannot be written. The branch of an `\\@ifstar` or an
        `\\@ifnextchar` that the use did not take goes too. The primitive
        conditionals stay as written: TeX decides them again, the same way.

        `edits` are drawn from the view (`Edit.of(node, …)` on its nodes): to
        remove the definitions of what was expanded, for instance. The text is in
        `\\n`, as the view is; to write it, `encoding=tex.encoding`. A body that
        TeX would read otherwise in the document (`\\@title` of a
        `\\makeatletter`), an environment that sets `\\ignorespacesafterend`
        anywhere but at the end of its end code, or a use that passes on a missing
        optional argument, is written as it stands, with a warning in the log:
        `expand(tex, writable=True)` leaves them unexpanded.
        """
        if not self.writable:
            foreign = self._foreign_bodies()
            if foreign:
                logger.warning(
                    "%s: %s written back as they stand, where the document would not run them the same;"
                    " expand(tex, writable=True) keeps them",
                    self.name,
                    ", ".join(foreign),
                )
            passing = sorted({expansion.name for expansion in self._passing_no_value()})
            if passing:
                logger.warning(
                    "%s: -NoValue- written out for %s, where a command that stays would take it for a value;"
                    " expand(tex, writable=True) keeps these uses",
                    self.name,
                    ", ".join(passing),
                )
        own: list[Edit] = []
        self._written_back(self.container, own)
        return rewrite(self, [*own, *edits])

    def _passing_no_value(self) -> set[Expansion]:
        """The uses whose `-NoValue-` `compilable` would write out: passed to a command that stays.

        Out of a decided test and out of a discarded branch, which go when the view
        is written back, the marker of a missing optional argument reaches a command
        of a package, of expl3 or one kept by `keep`; written out, it is text, and
        `\\tl_if_novalue:nTF` or `\\IfValueTF` there would find a value.
        """
        pieces = [piece for piece in self.source_map.pieces if piece.novalue]
        if not pieces:
            return set()
        own: list[Edit] = []
        self._written_back(self.container, own)
        removed = [(edit.start, edit.end) for edit in own]
        position = self.source_map.target.position
        passing: set[Expansion] = set()
        for piece in pieces:
            start, end = position(piece.start), position(piece.end)
            if piece.expansion is not None and not any(a <= start and end <= b for a, b in removed):
                passing.add(piece.expansion)
        return passing

    def _written_back(self, group: TexGroup, edits: list[Edit], math: bool = False) -> None:
        """The edits that write back what the view keeps for the analysis only (see `compilable`).

        `math`: we are inside math, where braces are not a neutral wrapping —
        `{…}` is an Ord atom, and TeX spaces an atom as it spaces none of what
        `\\begin{x}` opens. `\\begingroup` groups without becoming one.
        """
        offsets = self.source_map.target
        content = group.content
        removed: set[int] = set()
        if (
            group.env
            and group.macro is not None
            and group.start_position is not None
            and offsets.offset(group.start_position) in self._done
        ):
            # In math, braces would make an atom of the body and move what surrounds it.
            opening, closing = ("\\begingroup ", "\\endgroup ") if math else ("{", "}")
            ignoring = self._ignoring_after_end(group)
            if ignoring is not None:
                # `\end` ran `\ignorespaces` after the group, and `}` does not: it moves there. Left in the
                # group, the flag it sets would make the next `\end{…}` of the document eat its spaces.
                command, stop = ignoring
                assert command.start_position is not None and group.end_position is not None
                edits.append(Edit(command.start_position, stop, ""))
                removed.add(id(command))
                closing = closing.rstrip() + "\\ignorespaces"
                closing += " " if self._letter_at(group.end_position) else ""
            edits += [Edit.opening(group, opening), Edit.closing(group, closing)]
            for argument in group.arguments:
                if argument is not None:
                    edits.append(Edit.of(argument, ""))
                    removed.add(id(argument))
        for node in content:
            if id(node) in removed:
                continue
            if isinstance(node, TexBranch) and not node.taken and node.condition.test in LOOKAHEAD_TESTS:
                edits.append(Edit.of(node, self._parting(node, node.end_position)))
            elif isinstance(node, TexGroup):
                self._written_back(node, edits, math or node.math)
            elif isinstance(node, TexCommand) and node.base_name in ARGUMENT_TESTS and node.arguments:
                test, *branches = [argument for argument in node.arguments if argument is not None]
                # Decided, with its branches whole: one that spilled out melted into the stream.
                if not branches or not all(isinstance(branch, TexBranch) for branch in branches):
                    continue
                taken = next(
                    (branch for branch in branches if isinstance(branch, TexBranch) and branch.taken), None
                )
                following = taken.inner_start if taken is not None else branches[-1].end_position
                edits += [Edit.of(node, self._parting(node, following)), Edit.of(test, "")]
                removed.update(id(argument) for argument in (test, *branches))
                for branch in branches:
                    assert isinstance(branch, TexBranch)
                    if not branch.taken:
                        edits.append(Edit.of(branch, ""))
                        continue
                    edits += [Edit.opening(branch, ""), Edit.closing(branch, self._closing(node, branch))]
                    self._written_back(branch, edits, math)

    def _ignoring_after_end(self, group: TexGroup) -> tuple[TexCommand, Position] | None:
        """The final `\\ignorespacesafterend` of an environment, comments aside, and where its blanks end."""
        content = group.content
        for index in range(len(content) - 1, -1, -1):
            node = content[index]
            if node.is_comment():
                continue
            if not (isinstance(node, TexCommand) and node.base_name == IGNORE_AFTER_END):
                return None
            following = content[index + 1] if index + 1 < len(content) else group
            stop = following.start_position if following is not group else group.inner_end
            assert stop is not None
            return node, stop
        return None

    def _letter_at(self, position: Position) -> bool:
        line, column = position
        return line <= len(self.lines) and bool(LETTER.match(self.lines[line - 1][column : column + 1]))

    def _parting(self, node: TexContainer, following: Position | None) -> str:
        """What replaces a node removed: a space, if a control word before it would glue to a letter after."""
        assert node.start_position is not None and following is not None
        line, column = node.start_position
        before = self.lines[line - 1][:column][-CONTROL_WORD_TAIL:]
        return " " if ENDS_WITH_CONTROL_WORD.search(before) and self._letter_at(following) else ""

    def _closing(self, test: TexCommand, branch: TexBranch) -> str:
        """The brace of a branch taken, written back: `{}` if a control word would eat the space after it."""
        last = next(argument for argument in reversed(test.arguments or []) if argument is not None)
        assert last.end_position is not None and branch.inner_end is not None
        line, column = last.end_position
        following = self.lines[line - 1][column : column + 1] if line <= len(self.lines) else ""
        inner_line, inner_column = branch.inner_end
        tail = self.lines[inner_line - 1][:inner_column][-CONTROL_WORD_TAIL:].rstrip(BLANKS)
        word = ENDS_WITH_CONTROL_WORD.search(tail)
        quiet = word is not None and _reads_nothing_after(word.group().rsplit("\\", 1)[1], self.signatures)
        return "{}" if quiet and following in (" ", "\t", "\n") else ""

    def _foreign_bodies(self) -> list[str]:
        """The macros expanded here whose body the document would read otherwise (see `_writable`)."""
        registry = self.source.signatures
        names: list[str] = []
        for expansion in self.expansions:
            macro: Macro | EnvironmentMacro | None = (
                registry.environment_macro(expansion.name)
                if expansion.environment
                else registry.macro(expansion.name)
            )
            if macro is not None and not _writable(macro) and expansion.name not in names:
                names.append(expansion.name)
        return names


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

    What follows the use, for the junction (see the module header): `line_end`,
    the use ends on a control word right before a line ending, which TeX drops;
    `space_after`, it ends on something else before a blank, a space TeX has read.
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
    line_end: bool = False
    space_after: bool = False


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


def expand(
    tex: TexFile, max_depth: int = MAX_DEPTH, keep: Collection[str] = (), writable: bool = False
) -> ExpandedFile:
    """Expand the user's macros of an analysed file, and decide its conditionals.

    The view is analysed as the source was (`follow_inputs` included). With
    nothing to expand, it has the text of the source. Every pass expands the uses
    and decides the conditionals of the previous one, as long as any are left; an
    expansion deeper than `max_depth` does not happen, which stops a recursion.
    `keep` names the macros and environments to leave as they are, without
    backslash. `writable` builds a view to be written back
    (`ExpandedFile.compilable`): a body that TeX would read otherwise in the
    document, because it was defined under other catcodes and uses them, is not
    expanded either, nor an environment the document hangs a hook on
    (`\\AddToHook{env/x/begin}`, `\\AtBeginEnvironment`), whose code the name alone
    calls; nor an environment that sets `\\ignorespacesafterend`
    anywhere but at the end of its end code; nor a use that passes a missing
    optional argument on to a command that stays, which only ltcmd's own marker
    tells from a value: the view is made again without expanding it — that use
    of a command, every use of an environment.
    """
    kept = frozenset(keep) | (_hooked(tex) if writable else set())
    refused: frozenset[Expansion] = frozenset()
    view = _expanded(tex, max_depth, kept, writable, refused)
    # Refusing a use can only change what follows it: a few rounds at most, bounded all the same.
    for _ in range(MAX_PASSES):
        passing = view._passing_no_value() if writable else set()
        if not passing:
            break
        # An environment keeps its `\begin` and `\end` in the view, expanded or not: left alone at one
        # use and expanded at another, nothing would tell which of its `\begin` still need its definition.
        refused |= {expansion for expansion in passing if not expansion.environment}
        kept |= {expansion.name for expansion in passing if expansion.environment}
        view = _expanded(tex, max_depth, kept, writable, refused)
    return view


# Commands that hang code on an environment by name: the kernel's hooks and etoolbox's.
# `\\AddToHook` names it as `env/<name>/begin`; the others take the name alone.
ENVIRONMENT_HOOKS = frozenset(
    {"AtBeginEnvironment", "AtEndEnvironment", "BeforeBeginEnvironment", "AfterEndEnvironment"}
)
HOOK_COMMANDS = frozenset({"AddToHook", "AddToHookNext", "AddToHookWithArguments", *ENVIRONMENT_HOOKS})


def _hooked(tex: TexFile) -> set[str]:
    """The environments the document hangs code on: expanded, that code would never run.

    A hook is attached to the name, not to the body: `\\begin{x}` written out as
    `{` takes the hook's text away with it, silently. Nothing here can put it
    back — where it goes is the kernel's business, before or after, inside the
    group or outside — so these environments are left as they are.
    """
    names: set[str] = set()
    for found in tex.get_commands_arguments(sorted(HOOK_COMMANDS)):
        if len(found) < 2:
            continue
        name = found[0].base_name
        argument = found[1].arg()
        if name in ENVIRONMENT_HOOKS:
            names.add(argument)
        elif argument.startswith("env/"):
            names.add(argument.split("/")[1])
    return names - {""}


def _expanded(
    tex: TexFile,
    max_depth: int,
    kept: frozenset[str],
    writable: bool,
    refused: frozenset[Expansion],
) -> ExpandedFile:
    """The view of `expand`, leaving the uses of `refused` as they are."""
    source_map = SourceMap.identity(tex.lines)
    limit = MAX_GROWTH * len(source_map.source.text) + GROWTH_MARGIN
    current: TexFile = tex
    expansions: list[Expansion] = []
    conditions: list[Condition] = []
    # Starts of the expanded environments and of the decided conditionals, in the text of the pass.
    done: set[int] = set()
    for _ in range(MAX_PASSES):
        uses = _collect(
            current.container, source_map, done, current.signatures, max_depth, kept, writable, refused
        )
        if not uses:
            break
        writer = _Writer(source_map, uses, done, current.signatures)
        writer.write_range(0, len(source_map.target.text))
        text = writer.text()
        if len(text) > limit:
            logger.warning("%s: expansion stopped, the text goes beyond %d characters", tex.name, limit)
            break
        # Cut where the source is cut: at the line endings, all `\n` in the text written (see `_Offsets`).
        # `str.splitlines` also cuts at a form feed, or at the `\x85` of a cp1252 `…` read as Latin-1,
        # where TeX does not: every position after it would move, and a blank line could appear.
        lines = StringIO(text, newline="\n").readlines()
        source_map = SourceMap(writer.pieces, source_map.source, _Offsets(lines))
        expansions = [*expansions, *writer.expansions]
        conditions = [*conditions, *writer.conditions]
        done = writer.done
        current = ExpandedFile(tex, lines, source_map, expansions, conditions, done, writable)
        current.analyse(follow_inputs=tex.follow_inputs)
    if isinstance(current, ExpandedFile):
        return current
    view = ExpandedFile(tex, tex.lines, source_map, [], [], set(), writable)
    view.analyse(follow_inputs=tex.follow_inputs)
    return view


@dataclass(slots=True)
class _Pending:
    """A primitive conditional open while collecting: its `\\if…`, its `\\else`, and whether it decides."""

    opener: TexCommand
    otherwise: TexCommand | None = None
    decidable: bool = True


def _collect(
    root: TexGroup,
    source_map: SourceMap,
    done: set[int],
    registry: SignatureRegistry,
    max_depth: int,
    keep: frozenset[str] = frozenset(),
    writable: bool = False,
    refused: frozenset[Expansion] = frozenset(),
) -> list[_Use | _Decision]:
    """Uses to expand and conditionals to decide in the tree of one pass, in the order of the text.

    A use whose macro an earlier use of the pass may redefine is left to the next
    pass (see the module header).
    """
    uses: list[_Use | _Decision] = []
    pending: list[_Pending] = []
    text = source_map.target.text
    redefined: set[tuple[str, str]] = set()

    def collected(use: _Use) -> None:
        uses.append(use)
        redefined.update(_defined_by(use, text, registry))

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
                environment = None
                if node.macro is None or ("environment", node.macro.name) not in redefined:
                    environment = _environment_uses(node, source_map, done, max_depth, keep, writable)
                if environment is not None:
                    collected(environment[0])
                visit(node)
                if environment is not None:
                    collected(environment[1])
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
            if node.macro is None or ("command", node.macro.name) in redefined:
                continue
            use = _use(
                node,
                id(node) in token_arguments,
                source_map,
                max_depth,
                content,
                index,
                keep,
                writable,
                refused,
            )
            if use is not None:
                collected(use)

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
    keep: frozenset[str] = frozenset(),
    writable: bool = False,
    refused: frozenset[Expansion] = frozenset(),
) -> _Use | None:
    macro = command.macro
    if macro is None or command.start_position is None or command.end_position is None:
        return None
    if macro.name in keep or (writable and not _writable(macro)):
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
    if expansion.depth > max_depth or expansion in refused:
        return None
    text = offsets.text
    # The use ends on a control word (its own name, or an argument taken without braces): TeX skips
    # the blanks and the line ending after it. Otherwise, a blank after it is a space already read.
    last_token = present[-1] if present and alternative is None else command
    after_word = isinstance(last_token, TexCommand) and is_control_word(last_token.content)
    if after_word and (not present or alternative is None):
        while end < len(text) and text[end] in BLANKS:
            end += 1
    following = text[end : end + 1]
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
        line_end=after_word and following == "\n",
        space_after=not after_word and alternative is None and following in (" ", "\t", "\n"),
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
    group: TexGroup,
    source_map: SourceMap,
    done: set[int],
    max_depth: int,
    keep: frozenset[str] = frozenset(),
    writable: bool = False,
) -> tuple[_Use, _Use] | None:
    """The insertions of the begin and end code of a user environment."""
    macro = group.macro
    if (
        macro is None
        or group.start_position is None
        or group.end_position is None
        or group.inner_start is None
        or group.inner_end is None
        or macro.name in keep
        or (writable and not _writable(macro))
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
    # `\begin{name}` ends on a brace: a blank after it is a space TeX has read before the begin code. Unless
    # `\newenvironment{name}[1][d]` looked for its optional argument there and did not find it: its
    # `\@ifnextchar` skipped the blanks, line ending included, as after a control word. ltcmd does not
    # skip them before a final optional argument.
    text = offsets.text
    skipped = (
        bool(found) and found[-1] is None and not macro.end_arguments and macro.parameters[-1].kind in "oO"
    )
    after = begin_at
    while skipped and after < len(text) and text[after] in BLANKS:
        after += 1
    following = text[after : after + 1]
    return (
        _Use(
            begin_at,
            after,
            macro.begin,
            macro.catcodes,
            values,
            False,
            opening,
            start,
            line_end=skipped and following == "\n",
            space_after=not skipped and following in (" ", "\t", "\n"),
        ),
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


def _writable(macro: Macro | EnvironmentMacro) -> bool:
    """Once written in the document, does the body run as it did where the macro is used?

    It must read the same under the document's catcodes as under those of its
    definition. Only the characters the body holds count:
    `\\newcommand{\\R}{\\mathbb{R}}` in a `.sty` is written anywhere,
    `\\newcommand{\\ptitle}{\\@title}` of a `\\makeatletter` is not. And an
    environment may only set `\\ignorespacesafterend` at the end of its end code,
    the one place where `compilable` can put back, as `\\ignorespaces`, what the
    `\\end` did with it.
    """
    texts = [macro.begin, macro.end] if isinstance(macro, EnvironmentMacro) else [macro.body]
    if isinstance(macro, Macro) and macro.otherwise is not None:
        texts.append(macro.otherwise)
    foreign = _foreign_characters(macro.catcodes)
    if any(character in foreign for text in texts for character in text):
        return False
    return not isinstance(macro, EnvironmentMacro) or not (
        SETS_IGNORE_AFTER_END.search(macro.begin)
        or (SETS_IGNORE_AFTER_END.search(macro.end) and not ENDS_ON_IGNORE_AFTER_END.search(macro.end))
    )


@cache
def _foreign_characters(catcodes: CatcodeTable) -> frozenset[str]:
    """The characters `catcodes` reads otherwise than a LaTeX document does."""
    return frozenset(character for character, _, _ in CatcodeTable.latex().differences(catcodes))


@cache
def _body_facts(body: str) -> tuple[tuple[tuple[str, str], ...], frozenset[str], frozenset[str]]:
    """What a body defines, and the control words and environments it calls.

    A command defined is written as in the body (`\\x`), an environment by its
    name; either may be a parameter (`#1`), which the use gives.
    """
    defined = [("command", target) for target in DEFINED_COMMAND.findall(body)]
    defined += [("environment", target) for target in DEFINED_ENVIRONMENT.findall(body)]
    return tuple(defined), frozenset(CONTROL_WORD.findall(body)), frozenset(BEGIN.findall(body))


def _defined_by(use: _Use, text: str, registry: SignatureRegistry) -> set[tuple[str, str]]:
    """The commands and environments the expansion of `use` may define, through the macros it calls too.

    A name given by a parameter is read in the argument: `\\newcommand#1{…}`
    defines `\\y` at `\\declare\\y`. A macro the body calls is known by name only
    (its parameters are not followed), within `MAX_CALLED` macros.
    """
    names: set[tuple[str, str]] = set()
    seen: set[str] = set()
    bodies: list[tuple[str, tuple[Value, ...]]] = [(use.body, use.values)]
    if use.alternative is not None:
        bodies.append((use.alternative, use.values))
    while bodies and len(seen) < MAX_CALLED:
        body, values = bodies.pop()
        defined, words, environments = _body_facts(body)
        for kind, target in defined:
            if target.startswith("#"):
                number = int(target[1])
                value = values[number - 1] if number <= len(values) else None
                written = text[value[0] : value[1]] if isinstance(value, tuple) else value
                if written is None:
                    continue
                target = written.strip()
            if kind == "command":
                match = NAMED.fullmatch(target)
                if match is None:
                    continue
                target = match.group(1)
            names.add((kind, target))
        for word in words - seen:
            seen.add(word)
            macro = registry.macro(word)
            if macro is not None:
                bodies.append((macro.body, ()))
                if macro.otherwise is not None:
                    bodies.append((macro.otherwise, ()))
        for environment in environments:
            if "{" + environment in seen:
                continue
            seen.add("{" + environment)
            code = registry.environment_macro(environment)
            if code is not None:
                bodies += [(code.begin, ()), (code.end, ())]
    return names


def _reads_nothing_after(name: str, registry: SignatureRegistry, depth: int = 0) -> bool:
    """Is `\\name` known to leave what follows it alone: no argument, no star, no look ahead?

    A macro of the document without parameters leaves it alone, unless its own body
    ends on a control word that does not. An unknown command may do anything.
    """
    macro = registry.macro(name)
    if macro is not None:
        if macro.parameters or macro.otherwise is not None or macro.starred:
            return False
        tail = macro.body[-CONTROL_WORD_TAIL:].rstrip(BLANKS)
        word = ENDS_WITH_CONTROL_WORD.search(tail)
        if word is None:
            return True
        return depth < MAX_DEPTH and _reads_nothing_after(
            word.group().rsplit("\\", 1)[1], registry, depth + 1
        )
    signature = registry.command(name)
    return (
        signature is not None
        and not signature.arguments
        and not signature.starred
        and name not in READS_AHEAD
    )


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

    def __init__(
        self, previous: SourceMap, uses: list[_Use | _Decision], done: set[int], registry: SignatureRegistry
    ) -> None:
        self._previous = previous
        self._text = previous.target.text
        self._uses = uses
        self._registry = registry
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
                # At the end of an argument substituted in a body, what follows is the body's, not the text's.
                self._expand(use, junction=use.end < end)
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

    def _expand(self, use: _Use, junction: bool = True) -> None:
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
        if junction and (use.line_end or use.space_after):
            self._junction(use)
        self._branches = outer
        self._within, self._context = outer_within, outer_context
        self._last_expansion = expansion

    def _junction(self, use: _Use) -> None:
        """Keep what follows the use as TeX reads it, now that the body ends the text (see the header)."""
        tail = self.parts[-1][-CONTROL_WORD_TAIL:].rstrip(BLANKS) if self.parts else ""
        word = ENDS_WITH_CONTROL_WORD.search(tail)
        if use.line_end and word is None and not self._blank_tail:
            # TeX dropped the line ending after the name of the use; after the body, it would be a space.
            # A body that ends its own line needs nothing: that line ending becomes a space at the head
            # of the next line, where TeX skips it (see `_join`).
            self._write("%", use.expansion)
        elif (
            use.space_after
            and word is not None
            and not use.braced
            and _reads_nothing_after(word.group().rsplit("\\", 1)[1], self._registry)
        ):
            # TeX read that blank as a space, before the body ran: the control word may not swallow it.
            self._write("{}", use.expansion)

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
                self._write(value, use.expansion, use.catcodes, novalue=value == NO_VALUE)

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
                    novalue=piece.novalue,
                )
            start = stop
            index += 1

    def _write(
        self, text: str, expansion: Expansion, catcodes: CatcodeTable | None = None, novalue: bool = False
    ) -> None:
        if not text:
            return
        self._separate(text[0], expansion)
        self.parts.append(self._join(text))
        self._note(
            len(text),
            expansion=expansion,
            catcodes=catcodes,
            branches=self._branches,
            within=self._context,
            novalue=novalue,
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
        novalue: bool = False,
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
            if (
                (contiguous_copy or same_writer)
                and last.branches == branches
                and last.within is within
                and last.novalue == novalue
            ):
                last.end += length
                self._length += length
                return
        self.pieces.append(
            _Piece(
                self._length, self._length + length, source, expansion, catcodes, branches, within, novalue
            )
        )
        self._length += length
