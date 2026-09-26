"""The tree of a LaTeX source: text, commands, groups, verbatim, comments.

Positions
    `start_position` and `end_position` are `(line, column)` pairs: the line
    counted from 1, the column from 0, end excluded. They cover the **whole**
    element, delimiters included — `\\begin{center}` through `\\end{center}`, `{`
    through `}`, `$` through `$`. The old convention (the inside alone for
    groups, the outside for commands) broke every composed selection:
    `\\section{Intro}` read back as `\\section{Intr`. The inside of a group,
    between its delimiters, is in `inner_start` and `inner_end`.

`str()` and `raw_text()`
    `raw_text()` gives the exact source between the positions, line endings as
    the file writes them; `str()` rewrites an equivalent LaTeX, with multiple
    blanks collapsed and its lines ending in `\\n`, whatever the file's (see
    `analyse`). A column that goes beyond the text of its line designates the
    end of that line, never the middle of a `\\r\\n`. Blanks are not nodes:
    `str()` derives them from the gap between two neighbouring positions —
    nothing if they touch, a line break if the line changes, a space otherwise.
    That is what keeps `\\foreach \\x in` apart, separates the words of two
    lines, and ends every comment with its line break.

Access common to every container
    - `len(x)`: the number of children, or 1 for a non-empty string;
    - `x[i]`: the i-th child (or character); `for y in x`: the children;
    - `x.iter()`: a flat walk, the delimiters of the groups included;
    - `x + y`, `x += y`, `x.append(y)`: concatenation of elements;
    - `<`, `==`…: document order, by position; `x.is_in(y)`: containment.

Queries
    `get_commands_arguments`, `get_commands_to_next`, `get_envs`,
    `get_preamble`, and on groups `get_sections`, `get_graphics`, `get_inputs`.
"""

from collections.abc import Callable, Iterator, Sequence
from typing import Any, Self

from latexdetok.characters import (
    CLOSING_COUPLE,
    MATH_DELIMITERS,
    MATHS_MODE_CAR,
    ROOT_NAME,
    valid_command_name,
    valid_group_start,
)
from latexdetok.conditions import Role
from latexdetok.signatures import (
    ArgumentSpec,
    CommandSignature,
    EnvironmentMacro,
    EnvironmentSignature,
    Macro,
)

__all__ = [
    "BRANCH_NAME",
    "Position",
    "TexBranch",
    "TexCommand",
    "TexComment",
    "TexContainer",
    "TexContent",
    "TexGroup",
    "TexVerbatim",
]

Position = tuple[int, int]
BRANCH_NAME = "branche"
NodeSearch = Callable[..., "TexContainer | None"]


def _gap(previous: "TexContainer | None", before: Position | None, after: Position | None) -> str:
    """The blank to rewrite between two neighbouring positions."""
    if isinstance(previous, TexComment):
        # A comment runs to the end of its line, whatever follows it.
        return "\n"
    if before is None or after is None:
        return "" if previous is None else " "
    if after[0] != before[0]:
        return "\n"
    return "" if after[1] == before[1] else " "


def _bare(node: "TexContainer") -> bool:
    """A command left bare: the argument of another one, not a call (see `TexCommand.bare`)."""
    return isinstance(node, TexCommand) and node.bare


def _join(nodes: Sequence["TexContainer"], start: Position | None = None, end: Position | None = None) -> str:
    if not nodes:
        # `$ $` must not rewrite as `$$`, nor `{ }` lose its space.
        return _gap(None, start, end) if start is not None and end is not None else ""
    parts = []
    previous: TexContainer | None = None
    cursor = start
    for node in nodes:
        parts.append(_gap(previous, cursor, node.start_position))
        parts.append(str(node))
        previous, cursor = node, node.end_position
    if nodes and end is not None:
        parts.append(_gap(previous, cursor, end))
    elif nodes and nodes[-1].is_par():
        # A PAR only writes as the blank line that follows it: at the end of a text, it must be given.
        parts.append("\n")
    return "".join(parts)


class TexContainer:
    """The base container: a list of elements, or a string.

    Subclasses: `TexContent` (text or command), `TexGroup`, `TexVerbatim`,
    `TexComment`. A plain `TexContainer` gathers the results of a query: it has
    no position, but keeps the reference to the file (`rootfile`) so that its
    elements stay readable.

    Nodes have `__slots__`: one course has tens of thousands of them, and an
    expanded view as many per pass. Without a dictionary per node, the tree takes
    less room and is built faster.
    """

    __slots__ = ("content", "end_position", "rootfile", "start_position")

    def __init__(
        self,
        content: "list[TexContainer] | str | TexContainer",
        position: Position | None = None,
        end_position: Position | None = None,
        rootfile: Any = None,
    ) -> None:
        if isinstance(content, TexContainer):
            rootfile = content.rootfile
            position, end_position = content.start_position, content.end_position
            content = list(content.content) if content.list_container else content.content
        if not isinstance(content, (list, str)):
            raise TypeError(f"expected a list or a string, got {type(content).__name__}")
        self.content: Any = content  # a list of elements or a string, depending on the subclass
        self.rootfile: Any = rootfile
        self.start_position: Position | None = position
        self.end_position: Position | None = end_position
        if type(self) is TexContainer:
            # A container of results gathers scattered elements: no span.
            self.start_position = None
            self.end_position = None

    @property
    def list_container(self) -> bool:
        return isinstance(self.content, list)

    def __getitem__(self, i: int | slice) -> Any:
        return self.content[i]

    def __iter__(self) -> Iterator[Any]:
        return iter(self.content)

    def __len__(self) -> int:
        """0 if empty, 1 for a non-empty string, otherwise the number of elements."""
        if not self.list_container:
            return int(len(self.content) > 0)
        return len(self.content)

    def __repr__(self) -> str:
        if self.list_container and len(self) > 1:
            s = "[" + ",\n ".join(repr(c) for c in self.content) + "]"
        else:
            s = repr(self.content)
        return (
            f"TeXContainer({s}, position={self.start_position}, "
            f"end_position={self.end_position}, rootfile={self.rootfile})"
        )

    def __str__(self) -> str:
        if self.list_container:
            return _join(self.content)
        return self.content

    def _nodes(self) -> "list[TexContainer]":
        return list(self.content) if self.list_container else [self]

    def __add__(self, other: object) -> "TexContainer":
        if not isinstance(other, TexContainer):
            return NotImplemented
        return TexContainer(self._nodes() + other._nodes(), rootfile=self.rootfile)

    def __iadd__(self, other: object) -> Self:
        if not isinstance(other, TexContainer):
            return NotImplemented
        if not self.list_container:
            raise TypeError("+= only extends a list container")
        self.content.extend(other._nodes())
        return self

    def append(self, x: "TexContainer") -> None:
        if not isinstance(x, TexContainer):
            raise TypeError(f"expected a TexContainer, got {type(x).__name__}")
        if not self.list_container:
            raise TypeError("append only adds to a list container")
        self.content.append(x)

    def iter(self) -> "Iterator[TexContainer]":
        """A flat walk; a group adds its two delimiters to it."""
        if self.list_container:
            for node in self.content:
                yield from node.iter()
        else:
            yield self

    def raw_text(self) -> str:
        """The exact source of the element; with no file and no position, `str(self)`."""
        if self.rootfile is None or self.start_position is None or self.end_position is None:
            return str(self)
        return self.rootfile.raw_text(self)

    # Predicates

    def is_command(self, name: str | None = None) -> bool:
        return isinstance(self, TexContent) and self.command and (name is None or self.content == name)

    def is_command_in(self, name_list: Sequence[str]) -> bool:
        return isinstance(self, TexContent) and self.command and self.content in name_list

    def is_env(self, name: str | None = None) -> bool:
        return isinstance(self, (TexGroup, TexVerbatim)) and self.env and (name is None or self.name == name)

    def is_bracket_group(self) -> bool:
        return isinstance(self, TexGroup) and self.bracket

    def is_option_group(self) -> bool:
        return isinstance(self, TexGroup) and self.option

    def is_verbatim(self) -> bool:
        return isinstance(self, TexVerbatim)

    def is_comment(self) -> bool:
        return isinstance(self, TexComment)

    def is_math(self) -> bool:
        return isinstance(self, TexGroup) and self.math

    def is_begin(self) -> bool:
        return self.is_command("begin")

    def is_end(self) -> bool:
        return self.is_command("end")

    def is_pure_text(self) -> bool:
        return isinstance(self, TexContent) and not self.command

    def is_par(self) -> bool:
        return self.is_pure_text() and len(self) == 0

    # Document order

    def _span_key(self) -> tuple[int, int, int, int]:
        if self.start_position is None or self.end_position is None:
            raise TypeError("an element with no position cannot be compared")
        (start_line, start_col), (end_line, end_col) = self.start_position, self.end_position
        # At equal starts, the enclosing element comes before what it contains.
        return start_line, start_col, -end_line, -end_col

    def _check_same_file(self, other: "TexContainer") -> None:
        if other.rootfile is not self.rootfile:
            raise ValueError("positions from two different files: nothing to compare")

    def __eq__(self, other: object) -> bool:
        """The same span in the same file; for the content, compare the `str()`."""
        if not isinstance(other, TexContainer):
            return NotImplemented
        if None in (self.start_position, self.end_position, other.start_position, other.end_position):
            return self is other
        return (
            self.rootfile is other.rootfile
            and self.start_position == other.start_position
            and self.end_position == other.end_position
        )

    __hash__ = None  # type: ignore[assignment]

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, TexContainer):
            return NotImplemented
        self._check_same_file(other)
        return self._span_key() < other._span_key()

    def __le__(self, other: object) -> bool:
        if not isinstance(other, TexContainer):
            return NotImplemented
        self._check_same_file(other)
        return self._span_key() <= other._span_key()

    def __gt__(self, other: object) -> bool:
        if not isinstance(other, TexContainer):
            return NotImplemented
        self._check_same_file(other)
        return self._span_key() > other._span_key()

    def __ge__(self, other: object) -> bool:
        if not isinstance(other, TexContainer):
            return NotImplemented
        self._check_same_file(other)
        return self._span_key() >= other._span_key()

    def is_in(self, other: "TexContainer") -> bool:
        """True if the element lies entirely inside `other`."""
        self._check_same_file(other)
        start, end = self.start_position, self.end_position
        outer_start, outer_end = other.start_position, other.end_position
        if start is None or end is None or outer_start is None or outer_end is None:
            raise TypeError("an element with no position cannot be located")
        return outer_start <= start and end <= outer_end

    # Queries

    @staticmethod
    def _command_names(commands: str | Sequence[str], starred: bool) -> list[str]:
        # A copy: the old version extended the caller's list at every call.
        names = [commands] if isinstance(commands, str) else list(commands)
        if starred:
            names += [name + "*" for name in names if not name.endswith("*")]
        return names

    def get_commands_arguments(
        self,
        commands: str | Sequence[str],
        nargs: int | None = None,
        nopt: int | None = None,
        starred: bool = True,
    ) -> "TexContainer":
        """Every command found, followed by its arguments.

        Without `nargs` or `nopt`, a command of known signature comes with the
        arguments the tokeniser bound to it: none if its signature has none
        (`\\maketitle`). Otherwise, or for an unknown command, up to `nopt`
        optional arguments `[...]` (0 by default) are taken if they are there,
        then the next `nargs` elements (1 by default). A missing argument (a
        command at the end of a group) is simply left out.

        A command left bare (`TexCommand.bare`: `\\section` in
        `\\let\\titre\\section`) comes alone, `nargs` or not: it is the argument
        of another command, to which what follows it belongs.
        """
        names = self._command_names(commands, starred)
        by_signature = nargs is None and nopt is None

        def search_command(node: TexContainer, index: int, parent: TexContainer) -> TexGroup | None:
            if not node.is_command_in(names):
                return None
            found = TexGroup(rootfile=self.rootfile)
            found.append(node)
            if isinstance(node, TexCommand) and (node.bare or (by_signature and node.signature is not None)):
                # A bare command has nothing bound: it comes alone.
                for argument in node.arguments or ():
                    if argument is not None:
                        found.append(argument)
            else:
                following = parent.content[index + 1 :]
                optional = nopt or 0
                taken = 0
                while taken < min(optional, len(following)) and following[taken].is_option_group():
                    taken += 1
                for argument in following[: taken + (1 if nargs is None else nargs)]:
                    found.append(argument)
            found.set_positions()
            return found

        found = self.map_contents(self, search_command)
        return TexContainer(sorted(found), rootfile=self.rootfile)

    def get_commands_to_next(
        self,
        command: str,
        close_at_same_level: bool = True,
        remove_comments: bool = True,
        starred: bool = True,
    ) -> "TexGroup":
        """Every command and what follows it in its group.

        With `close_at_same_level`, we stop at the next occurrence of the same
        command (starred or not, per `starred`); otherwise we go to the end of the
        group, and the selections may overlap. A command left bare
        (`\\titleformat{\\section}`, see `TexCommand.bare`) is not a call: it
        neither starts a selection nor ends one.
        """
        names = self._command_names(command, starred)

        def search_command(node: TexContainer, index: int, parent: TexContainer) -> TexGroup | None:
            if not node.is_command_in(names) or _bare(node):
                return None
            selection = [node]
            for sibling in parent.content[index + 1 :]:
                if close_at_same_level and sibling.is_command_in(names) and not _bare(sibling):
                    break
                if remove_comments and sibling.is_comment():
                    continue
                selection.append(sibling)
            group = TexGroup(content=selection, rootfile=self.rootfile)
            group.set_positions()
            return group

        return self.map_contents(self, search_command)

    def get_envs(self, name: str) -> "TexContainer":
        """Every environment `name`, at any depth; `'$'` for math."""

        def search_env(node: TexContainer) -> TexGroup | None:
            if not node.is_env(name=name):
                return None
            found = TexGroup(rootfile=node.rootfile)
            found.append(node)
            return found

        return TexContainer(sorted(self.map_groups(self, search_env)), rootfile=self.rootfile)

    def get_preamble(self) -> "TexContainer":
        """What comes before `\\begin{document}`; nothing if there is none."""
        nodes = self.content if self.list_container else []
        for index, node in enumerate(nodes):
            if node.is_env("document"):
                return TexContainer(nodes[:index], rootfile=self.rootfile)
        return TexContainer([], rootfile=self.rootfile)

    @classmethod
    def map_contents(
        cls, container: "TexContainer", func: NodeSearch, found: "TexGroup | None" = None
    ) -> "TexGroup":
        """Apply `func` to every non-group element, recursively.

        `func(element, index=i, parent=group)` returns `None` or an empty group
        when there is nothing, otherwise a group gathering what was found; these
        groups pile up in `found`.
        """
        if found is None:
            found = TexGroup(rootfile=container.rootfile)
        if not container.list_container:
            return found
        for i, content in enumerate(container):
            if isinstance(content, TexGroup):
                cls.map_contents(content, func, found)
            else:
                x = func(content, index=i, parent=container)
                if x is not None and len(x) > 0:
                    found.append(x)
        return found

    @classmethod
    def map_groups(
        cls, container: "TexContainer", func: NodeSearch, found: "TexGroup | None" = None
    ) -> "TexGroup":
        """Apply `func` to every group and verbatim, recursively; flattens."""
        if found is None:
            found = TexGroup(rootfile=container.rootfile)
        if not container.list_container:
            return found
        for content in container:
            if isinstance(content, (TexGroup, TexVerbatim)):
                selection = func(content)
                if selection is not None:
                    found += selection
            if isinstance(content, TexGroup):
                cls.map_groups(content, func, found)
        return found


class TexGroup(TexContainer):
    """A group `{...}`, an optional argument `[...]`, an environment or math mode.

    Math has `'$'` for a name and `env=True` whatever its delimiter (`$`, `$$`,
    `\\[`, `\\(`), which is kept in `delimiter` for the rewriting. The root of a
    file is the environment `latexfile`, rewritten without delimiters.
    """

    __slots__ = (
        "arguments",
        "delimiter",
        "displaymath",
        "env",
        "inner_end",
        "inner_start",
        "macro",
        "name",
        "signature",
    )

    def __init__(
        self,
        name: str = "{",
        content: "list[TexContainer] | None" = None,
        env: bool = False,
        displaymath: bool = False,
        delimiter: str | None = None,
        *,
        position: Position | None = None,
        end_position: Position | None = None,
        rootfile: Any = None,
    ) -> None:
        super().__init__([] if content is None else content, position, end_position, rootfile)
        self.name = name
        self.env = env
        if name == MATHS_MODE_CAR:
            if delimiter is None:
                delimiter = "$$" if displaymath else "$"
            if delimiter not in MATH_DELIMITERS:
                raise ValueError(f"unknown math delimiter: {delimiter!r}")
            displaymath = delimiter in ("$$", "\\[")
        elif not env and not valid_group_start(name):
            raise ValueError(f"invalid LaTeX group name: {name!r}")
        elif env and not name:
            raise ValueError("empty environment name")
        self.delimiter = delimiter
        self.displaymath = displaymath
        self.inner_start = self.start_position
        self.inner_end = self.end_position
        # For an environment of known signature: its first children that are its arguments.
        self.signature: EnvironmentSignature | None = None
        self.arguments: list[TexContainer | None] = []
        # For a user environment: its begin and end code (see `expansion`).
        self.macro: EnvironmentMacro | None = None

    def pop(self) -> TexContainer:
        return self.content.pop()

    @property
    def top(self) -> TexContainer:
        return self.content[-1]

    @property
    def first(self) -> TexContainer:
        return self.content[0]

    @property
    def is_root(self) -> bool:
        return self.env and self.name == ROOT_NAME

    @property
    def math(self) -> bool:
        return self.name == MATHS_MODE_CAR

    @property
    def inlinemath(self) -> bool:
        return self.math and not self.displaymath

    @property
    def bracket(self) -> bool:
        return not self.env and self.name == "{"

    @property
    def option(self) -> bool:
        return not self.env and self.name == "["

    @property
    def closing(self) -> str:
        return self.enclosures()[1]

    def enclosures(self) -> tuple[str, str]:
        if self.math:
            assert self.delimiter is not None
            return self.delimiter, MATH_DELIMITERS[self.delimiter]
        if self.is_root:
            return "", ""
        if self.env:
            return f"\\begin{{{self.name}}}", f"\\end{{{self.name}}}"
        return self.name, CLOSING_COUPLE[self.name]

    def set_positions(self) -> None:
        """The span of a selection: from the first element to the last."""
        if len(self) > 0:
            self.start_position = self.first.start_position
            self.end_position = self.top.end_position
            self.inner_start, self.inner_end = self.start_position, self.end_position

    def iter(self) -> Iterator[TexContainer]:
        # The delimiters are pseudo-elements with no file: raw_text() == str().
        opening, closing = self.enclosures()
        if opening:
            yield TexContent(opening, position=self.start_position, end_position=self.inner_start)
        for node in self.content:
            yield from node.iter()
        if closing:
            yield TexContent(closing, position=self.inner_end, end_position=self.end_position)

    def repr_hierarchy(self, level: int = 0, expand: bool = True) -> str:
        s = "<TexGroup {}>".format(self.name if self.env else "")
        if not expand:
            return s
        opening, closing = ("", "") if self.env else self.enclosures()

        def child_repr(child: TexContainer) -> str:
            if isinstance(child, TexGroup):
                return child.repr_hierarchy(level + 2)
            return repr(child)

        if len(self.content) == 1:
            return s + opening + child_repr(self.content[0]) + closing
        s += opening
        if self.content:
            indent = "\n" + " " * (level + 1)
            s += indent + indent.join(child_repr(child) for child in self.content) + indent
        return s + closing

    def __repr__(self) -> str:
        return self.repr_hierarchy()

    def arg(self) -> str:
        """The rewriting of the inside, without the delimiters."""
        if self.is_root:
            # No delimiters to part from the content, and no indentation to keep.
            return _join(self.content)
        return _join(self.content, self.inner_start, self.inner_end)

    def __str__(self) -> str:
        opening, closing = self.enclosures()
        return opening + self.arg() + closing

    def get_sections(self, starred: bool = True) -> TexContainer:
        """`\\section` and `\\section*`, the short title `[...]` included when there is one."""
        return self.get_commands_arguments("section", starred=starred)

    def get_graphics(self) -> TexContainer:
        return self.get_commands_arguments(
            ["includegraphics", "includetoolargegraphics", "includepdf", "includetikz"],
            starred=False,
            nopt=1,
            nargs=1,
        )

    def get_inputs(self, document: bool | None = None) -> TexContainer:
        """`\\input`: `document=None` looks inside the document if there is one, otherwise
        everywhere; `True` only inside the document; `False` everywhere."""
        if document is False:
            return self.get_commands_arguments(["input"], starred=False)
        doc_env = self.get_envs("document")
        if document is None and len(doc_env) == 0:
            return self.get_commands_arguments(["input"], starred=False)
        return doc_env.get_commands_arguments(["input"], starred=False)


class TexBranch(TexGroup):
    """A branch of a conditional the expansion decided: taken or discarded.

    Both branches stay in the tree of the expanded view: `taken` says the one TeX
    follows, `condition` which conditional it is (an `expansion.Condition`,
    shared by both branches). `braced`: a branch of a conditional with arguments
    (`\\IfBooleanTF{b}{A}{B}`), written between braces.

    A discarded branch is read apart: neither its structure, nor its definitions,
    nor its catcodes, nor its booleans touch the rest, and nothing in it is
    reported. A taken branch is read in the stream, as TeX runs it. If its
    structure spills out of it — a `\\begin` closed after `\\fi`, a command that
    reads its arguments after the branch — it dissolves like any provisional
    opening of the tokeniser: its elements join the stream, and
    `ExpandedFile.branches` still says which side they are on.
    """

    __slots__ = ("braced", "condition", "taken")

    def __init__(
        self,
        condition: Any,
        taken: bool,
        braced: bool = False,
        *,
        position: Position | None = None,
        end_position: Position | None = None,
        rootfile: Any = None,
    ) -> None:
        super().__init__("{", position=position, end_position=end_position, rootfile=rootfile)
        self.name = BRANCH_NAME
        self.condition = condition
        self.taken = taken
        self.braced = braced

    def enclosures(self) -> tuple[str, str]:
        return ("{", "}") if self.braced else ("", "")

    def __repr__(self) -> str:
        side = "taken" if self.taken else "discarded"
        return f"<TexBranch {side}> : {self}"


class TexComment(TexContainer):
    """A comment: `content` is the text after `%`, without the line ending."""

    __slots__ = ()

    def __repr__(self) -> str:
        return f"<TexComment> : {self}"

    def __str__(self) -> str:
        return "%" + self.content


class TexVerbatim(TexContainer):
    """Content read without analysis: `\\verb|...|`, `\\url{...}` or `\\begin{verbatim}`.

    `content` is the source between the delimiters, its line endings written
    `\\n` like everything the tree writes; `raw_text()` has them as the file does.
    `closed` is false for a verbatim that nothing closed (end of file, end of the
    line of a `\\verb`): its rewriting invents no end for it.
    """

    __slots__ = ("car", "closed", "env", "name")

    def __init__(
        self,
        name: str,
        env: bool = False,
        car: str | None = None,
        content: str = "",
        *,
        position: Position | None = None,
        end_position: Position | None = None,
        rootfile: Any = None,
    ) -> None:
        super().__init__(content, position, end_position, rootfile)
        if not name:
            raise ValueError("verbatim with no name")
        if not env and not car:
            raise ValueError(f"\\{name} needs its delimiter character")
        self.name = name
        self.car = car  # delimiter of the \verb|...| forms
        self.env = env
        self.closed = True

    @property
    def opening(self) -> str:
        return f"\\begin{{{self.name}}}" if self.env else f"\\{self.name}{self.car}"

    @property
    def verbatim_exit_phrase(self) -> str:
        if self.env:
            return f"\\end{{{self.name}}}"
        assert self.car is not None
        return CLOSING_COUPLE.get(self.car, self.car)

    def __repr__(self) -> str:
        return "<TexVerbatim {}{}> : {}".format("(env)" if self.env else "", self.name, repr(self.content))

    def __str__(self) -> str:
        return self.opening + self.content + (self.verbatim_exit_phrase if self.closed else "")


class TexContent(TexContainer):
    """A command (`command=True`, `content` without the backslash) or text.

    An empty text is a paragraph break (a blank line).
    """

    __slots__ = ("command",)

    def __init__(
        self,
        content: "str | TexContainer",
        position: Position | None = None,
        end_position: Position | None = None,
        rootfile: Any = None,
        *,
        command: bool = False,
    ) -> None:
        super().__init__(content, position, end_position, rootfile)
        if not isinstance(self.content, str):
            raise TypeError("a TexContent holds a string")
        self.command = command
        if self.command and not valid_command_name(self.content):
            raise ValueError(f"invalid command name: {self.content!r}")

    @property
    def name(self) -> str:
        return self.content

    def __repr__(self) -> str:
        return "<TexContent> : PAR" if self.is_par() else f"<TexContent> : {self}"

    def arg(self) -> str:
        return self.content

    def __str__(self) -> str:
        return ("\\" if self.command else "") + self.content


class TexCommand(TexContent):
    """A command read by the tokeniser, with what it knows of its arguments.

    The arguments stay siblings in the tree, as before: `arguments` designates
    them without moving them, so that positions, `str()` and indexing do not
    change. The list is aligned with `signature.arguments`; `None` marks there an
    argument that is absent (optional) or missing (mandatory).

    `arguments` is `None` when nothing was bound: an unknown signature (the
    original heuristics apply), a signature without arguments, or a command left
    bare. `bare` tells that last case apart, whether the signature is known or
    not: a command taken as a plain token (`\\section` in `\\let\\titre\\section`,
    `\\demi` in `\\frac\\demi x`) or alone in its braces (`\\titleformat{\\section}`)
    is itself the argument of another command. It reads none of its own where it
    is written: what follows it belongs to that other command.

    `macro` is the body of the user macro in force at the use (see `expansion`).
    `role` says whether the command opens, separates or closes a primitive
    conditional (`\\ifnum`, `\\else`, `\\fi`), and `value` the value of the
    conditional it opens, when the tokeniser could decide it (see `conditions`).
    """

    __slots__ = ("arguments", "bare", "macro", "role", "signature", "value")

    def __init__(
        self,
        content: "str | TexContainer",
        position: Position | None = None,
        end_position: Position | None = None,
        rootfile: Any = None,
        *,
        signature: CommandSignature | None = None,
        macro: Macro | None = None,
    ) -> None:
        super().__init__(content, position, end_position, rootfile)
        # No check on the name: it was read under the catcodes in force, and
        # `\catcode`\é=11` makes `é` a letter. `TexContent` checks a name written
        # by hand, which has no table.
        self.command = True
        self.signature = signature
        self.macro = macro
        self.arguments: list[TexContainer | None] | None = None
        self.bare = False
        self.role: Role | None = None
        self.value: bool | None = None

    @property
    def star(self) -> bool:
        content = self.content
        return len(content) > 1 and content[-1] == "*"

    @property
    def base_name(self) -> str:
        content = self.content
        return content[:-1] if len(content) > 1 and content[-1] == "*" else content

    def missing_arguments(self) -> list[ArgumentSpec]:
        """Mandatory arguments of the signature that were not found."""
        if self.signature is None or self.arguments is None:
            return []
        found = self.arguments + [None] * (len(self.signature.arguments) - len(self.arguments))
        return [
            spec
            for spec, node in zip(self.signature.arguments, found, strict=True)
            if spec.mandatory and spec.bound and node is None
        ]
