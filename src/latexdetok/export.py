"""Editing a LaTeX source from its analysis, without rebuilding it.

Why edits. The tree can rewrite everything — `str()` gives back an equivalent
LaTeX — but equivalent is not identical: multiple blanks melt away, the
indentation is derived from the positions, and the whole file changes when one
wanted to change three arguments. A diff nobody can read is a diff nobody reads.
An edit, on the other hand, replaces one span and nothing else: whatever none of
them touches comes out of the file **byte for byte**, comments, layout and line
endings included.

On disk. `rewrite` returns the source as the file writes it, `\\r\\n` included
(see `analyse`); written back with `encoding=tex.encoding`, which puts back a
byte order mark if there was one, and `newline=""`, without which Python would
turn every `\\n` into `\\r\\n` on Windows, the file only differs where an edit is.
The text of an edit is written as it is given, its line endings no more
translated than the file's: in a `\\r\\n` file, an added line ends in `\\r\\n` if
it is written so.

How. `Edit(start, end, text)` replaces the source between two positions;
`start == end` inserts. The constructors start from the nodes, whose positions
are exact: `Edit.of` replaces a whole node, `Edit.inside` the inside of a group,
`Edit.before` and `Edit.after` insert next to it, `Edit.opening` and
`Edit.closing` aim at the delimiters of an environment or of a group.
`corrected` applies a function to nodes: it returns the LaTeX that replaces
them, or `None` to leave them alone. `rewrite` returns the edited source.

What sibling arguments make easy. Since `\\section{Title}` is a command
**followed** by its group, and not a command that holds it, renaming the command
does not touch the title: `Edit.of(command, "\\subsection")` replaces eleven
characters. Changing the argument without touching the command is the same
gesture, from the other side.

What raises rather than producing a wrong source: two edits that overlap, a
position outside the file, a node with no position, and a node that comes from
another file — typically from an expanded view (`expand`), whose positions are
not the source's. To fix the source from a diagnostic of the view, bring it back
to the source first (`checks.in_source`).
"""

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field

from latexdetok.analyse import TexFile
from latexdetok.characters import index_in_line
from latexdetok.classes import Position, TexContainer, TexGroup

__all__ = ["Edit", "corrected", "rewrite"]


@dataclass(frozen=True, slots=True)
class Edit:
    """Replace the source between two positions; `start == end` inserts, an empty `text` deletes.

    `node` is the node the edit is drawn from, when it is drawn from one: it only
    serves to check that we are editing the file it comes from, and to name the
    culprit in the errors.
    """

    start: Position
    end: Position
    text: str
    node: TexContainer | None = field(default=None, compare=False, repr=False)

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ValueError(f"backwards edit: {self.start} comes after {self.end}")

    @property
    def insertion(self) -> bool:
        return self.start == self.end

    @classmethod
    def of(cls, node: TexContainer, text: str) -> "Edit":
        """Replace a whole node, delimiters included."""
        start, end = _span(node)
        return cls(start, end, text, node)

    @classmethod
    def inside(cls, group: TexGroup, text: str) -> "Edit":
        """Replace the inside of a group, of an argument or of an environment."""
        start, end = _inner(group)
        return cls(start, end, text, group)

    @classmethod
    def before(cls, node: TexContainer, text: str) -> "Edit":
        """Insert right before a node."""
        start, _ = _span(node)
        return cls(start, start, text, node)

    @classmethod
    def after(cls, node: TexContainer, text: str) -> "Edit":
        """Insert right after a node — an optional argument after its command, for instance."""
        _, end = _span(node)
        return cls(end, end, text, node)

    @classmethod
    def opening(cls, group: TexGroup, text: str) -> "Edit":
        """Replace the opening delimiter: `\\begin{itemize}`, `{`, `$`."""
        start, _ = _span(group)
        return cls(start, _inner(group)[0], text, group)

    @classmethod
    def closing(cls, group: TexGroup, text: str) -> "Edit":
        """Replace the closing delimiter: `\\end{itemize}`, `}`, `$`."""
        _, end = _span(group)
        return cls(_inner(group)[1], end, text, group)


def corrected(nodes: Iterable[TexContainer], fix: Callable[[TexContainer], str | None]) -> list[Edit]:
    """The edits a function asks for on nodes.

    `fix` receives a node and returns the LaTeX that replaces it, or `None` to
    leave it as it is — that is where the filter goes, and that is why it receives
    the whole node: it can read its text, its arguments, its position.
    """
    return [Edit.of(node, text) for node in nodes if (text := fix(node)) is not None]


def rewrite(tex: TexFile, edits: Iterable[Edit]) -> str:
    """The edited source, everything else identical byte for byte, line endings included.

    The edits apply in the order of the document, whatever order they arrive in.
    Two that overlap raise: better an error than a wrong source. Several
    insertions at the same place are written in the order given. To write the
    result: `encoding=tex.encoding, newline=""` (see the module header).
    """
    source = "".join(tex.lines)
    starts = _line_starts(tex.lines)
    ordered = sorted(
        (
            (_offset(tex, starts, edit.start, edit), _offset(tex, starts, edit.end, edit), edit)
            for edit in edits
        ),
        key=lambda item: (item[0], item[1]),
    )
    pieces: list[str] = []
    cursor = 0
    for start, end, edit in ordered:
        if start < cursor:
            raise ValueError(f"two edits overlap at {edit.start}: {_name(edit)}")
        pieces.append(source[cursor:start])
        pieces.append(edit.text)
        cursor = end
    pieces.append(source[cursor:])
    return "".join(pieces)


def _span(node: TexContainer) -> tuple[Position, Position]:
    if node.start_position is None or node.end_position is None:
        raise ValueError(f"a node with no position has no span to edit: {node!s:.40}")
    return node.start_position, node.end_position


def _inner(group: TexGroup) -> tuple[Position, Position]:
    if not isinstance(group, TexGroup):
        raise TypeError(f"the inside of a group was expected, got {type(group).__name__}")
    if group.inner_start is None or group.inner_end is None:
        raise ValueError(f"group with no located inside: {group!s:.40}")
    return group.inner_start, group.inner_end


def _line_starts(lines: Sequence[str]) -> list[int]:
    """The index of the first character of every line, plus the end of the file."""
    starts = [0]
    for line in lines:
        starts.append(starts[-1] + len(line))
    return starts


def _offset(tex: TexFile, starts: Sequence[int], position: Position, edit: Edit) -> int:
    """The index of the character in the whole source; the position must be this file's."""
    if edit.node is not None and edit.node.rootfile is not tex:
        raise ValueError(
            f"an edit from another file than the one being rewritten: {_name(edit)}"
            " — an expanded view has its own positions (see `checks.in_source`)"
        )
    line, column = position
    lines: Sequence[str] = tex.lines
    if line == len(lines) + 1 and column == 0:
        # The end of the file: the nodes that run to the very end finish there.
        return starts[-1]
    if not 1 <= line <= len(lines):
        raise ValueError(f"line {line} is outside the file {tex.name} ({len(lines)} lines)")
    if not 0 <= column <= len(lines[line - 1]):
        raise ValueError(f"column {column} is outside line {line} of {tex.name}")
    return starts[line - 1] + index_in_line(lines[line - 1], column)


def _name(edit: Edit) -> str:
    return f"“{edit.node!s:.30}”" if edit.node is not None else f"{edit.start}–{edit.end}"
