"""The typeset text of a document, to look for a sentence in it.

Why. The queries of the tree find structure: the arguments of every section, the
environments of one name, the files loaded. They do not find a sentence.
“kinetic energy” may be cut by an `\\emph{}`, written `\\'energie`, split by a
line ending, or laid inside the argument of a macro. The typeset text, on the
other hand, is searched with `find` or with `re` — and every piece keeps where
it comes from: the position in the source, and the node of the tree.

What is rendered: the text, the mandatory arguments of the commands that
typeset, the body of the environments, the verbatim, the common substitutions
(`\\og`, `\\LaTeX`, `\\ldots`) and the accents — `\\'e` becomes “é”. The blanks of
the source are reduced to one space: a sentence cut by a line ending becomes a
sentence again, and that is the whole point. The breaks that matter stay: blank
line, `\\\\`, `\\item`, titles, environments.

What is not: comments, arguments that name something (`\\label`,
`\\includegraphics`), the bodies of definitions, key-value settings, drawings
(`tikzpicture`), and the optional arguments, which almost always carry settings.
An unknown command disappears, but not its braces: their content is text until
proven otherwise, and that is the package's tolerant stance.

What we cannot render is what TeX computes: a `\\ref` does not give its number, a
formula is not laid out — it is rendered as written, or skipped (`math`) —, a
`\\multicolumn` aligns nothing. On a source that is not expanded, both branches
of a conditional are rendered; on the expanded view (`expand`), only the one TeX
reads.

For the export. The map designates **nodes**, not only positions: a search leads
to the element of the tree, whose exact positions say what to edit in the source.
That is what the export needs (`export.py`).
"""

import re
import unicodedata
from bisect import bisect_right
from collections.abc import Sequence
from dataclasses import dataclass

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
from latexdetok.conditions import MATH_ENVIRONMENTS
from latexdetok.definitions import DEFINING_COMMANDS
from latexdetok.parser import UNTYPESET_ARGUMENTS
from latexdetok.semantics import NAME_ARGUMENTS

__all__ = ["Found", "Mark", "TexText", "to_text"]

# The same as `rendering.SECTION_LEVELS`, without the ranks: here only the break matters.
SECTIONS = frozenset(
    {
        "part",
        "chapter",
        "section",
        "subsection",
        "subsubsection",
        "paragraph",
        "subparagraph",
        "frametitle",
        "framesubtitle",
        "title",
    }
)
# Commands that break the line, and by how much.
BREAKS = {
    "\\": 1,
    "newline": 1,
    "linebreak": 1,
    "par": 2,
    "newpage": 2,
    "clearpage": 2,
    "cleardoublepage": 2,
    "pagebreak": 1,
    "bigskip": 1,
    "medskip": 1,
    "smallskip": 1,
    "hline": 1,
    "toprule": 1,
    "midrule": 1,
    "bottomrule": 1,
}
# Settings: a mandatory argument that typesets nothing. `NAME_ARGUMENTS` already holds half of them.
SETTINGS = frozenset(
    {
        "hypersetup",
        "geometry",
        "newgeometry",
        "lstset",
        "lstdefinestyle",
        "sisetup",
        "tcbset",
        "tikzset",
        "pgfset",
        "tikzstyle",
        "pgfplotsset",
        "usetikzlibrary",
        "usepgfplotslibrary",
        "setbeamertemplate",
        "setbeamercolor",
        "setbeamerfont",
        "setbeamersize",
        "captionsetup",
        "setlist",
        "newlist",
        "setlistdepth",
        "newcolumntype",
        "newfloat",
        "floatstyle",
        "restylefloat",
        "newtheorem",
        "theoremstyle",
        "DeclareMathOperator",
        "DeclarePairedDelimiter",
        "newtcolorbox",
        "newtcbox",
        "NewEnviron",
        "RenewEnviron",
        "AtBeginEnvironment",
        "AtEndEnvironment",
        "AtBeginDocument",
        "AtEndDocument",
        "apptocmd",
        "pretocmd",
        "patchcmd",
        "robustify",
        "numberwithin",
        "setmainfont",
        "setsansfont",
        "setmonofont",
        "newfontfamily",
        "hyphenation",
        "index",
        "glossary",
        "vspace",
        "hspace",
        "rule",
        "includepdf",
        "movie",
    }
)
# Everything none of whose arguments is typeset where it is written.
SILENT_COMMANDS = NAME_ARGUMENTS | UNTYPESET_ARGUMENTS | DEFINING_COMMANDS | SETTINGS
# Environments whose content is a drawing or code, not text.
SILENT_ENVIRONMENTS = frozenset(
    {
        "tikzpicture",
        "pgfpicture",
        "pgfonlayer",
        "scope",
        "axis",
        "semilogxaxis",
        "semilogyaxis",
        "loglogaxis",
        "circuitikz",
        "forest",
        "picture",
        "comment",
        "filecontents",
        "filecontents*",
        "asy",
    }
)
# Commands worth one character. The symbols of the kernel are too many to enter here.
SUBSTITUTIONS = {
    "og": "«",
    "fg": "»",
    "guillemotleft": "«",
    "guillemotright": "»",
    "LaTeX": "LaTeX",
    "LaTeXe": "LaTeX2e",
    "TeX": "TeX",
    "XeLaTeX": "XeLaTeX",
    "LuaLaTeX": "LuaLaTeX",
    "BibTeX": "BibTeX",
    "ldots": "…",
    "dots": "…",
    "textellipsis": "…",
    "textbackslash": "\\",
    "textasciitilde": "~",
    "textasciicircum": "^",
    "textbullet": "•",
    "textdegree": "°",
    "degre": "°",
    "degres": "°",
    "texteuro": "€",
    "euro": "€",
    "pounds": "£",
    "copyright": "©",
    "textcopyright": "©",
    "S": "§",
    "P": "¶",
    "dag": "†",
    "ddag": "‡",
    "ss": "ß",
    "ae": "æ",
    "AE": "Æ",
    "oe": "œ",
    "OE": "Œ",
    "aa": "å",
    "AA": "Å",
    "o": "ø",
    "O": "Ø",
    "l": "ł",
    "L": "Ł",
    "i": "ı",
    "j": "ȷ",
    "ier": "er",
    "iere": "re",
    "ieme": "e",
    "iemes": "es",
    "&": "&",
    "%": "%",
    "$": "$",
    "#": "#",
    "_": "_",
    "{": "{",
    "}": "}",
    " ": " ",
    ",": " ",
    ";": " ",
    ":": " ",
    "quad": " ",
    "qquad": " ",
    "enspace": " ",
    "thinspace": " ",
    "nobreakspace": " ",
    "!": "",
    "-": "",
}
# Accents: the combining mark that follows the letter. `\\'e` writes “e” then U+0301, composed into “é”.
ACCENTS = {
    "'": "́",
    "`": "̀",
    "^": "̂",
    '"': "̈",
    "~": "̃",
    "=": "̄",
    ".": "̇",
    "u": "̆",
    "v": "̌",
    "H": "̋",
    "c": "̧",
    "k": "̨",
    "b": "̱",
    "d": "̣",
    "r": "̊",
    "t": "͡",
}
# The strength of a pending blank: between two pieces, the strongest wins.
BLANKS = {"": 0, " ": 1, "\n": 2, "\n\n": 3}


@dataclass(frozen=True, slots=True)
class Mark:
    """A piece of the text and the node that wrote it.

    `offset` and `length` place the piece in the rendered text. `faithful` says
    that the piece is the text of the node, character for character save the
    blanks: the position of each one can be computed exactly. Otherwise — a
    substitution, verbatim, math — we give the position of the node.
    """

    offset: int
    length: int
    node: TexContainer
    faithful: bool = False


@dataclass(frozen=True, slots=True)
class Found:
    """A piece found: its span in the text, its node, its position in the source."""

    start: int
    end: int
    text: str
    node: TexContainer | None
    position: Position | None

    def __str__(self) -> str:
        return self.text


@dataclass(frozen=True, slots=True)
class TexText:
    """The typeset text of a document, and where every character comes from.

    `text` is searched like a string; `find` and `search` also return the node
    and the position of every hit. `position(offset)` places a character in the
    source, `node(offset)` gives the element of the tree that wrote it — that is
    what one edits the source through.
    """

    text: str
    marks: tuple[Mark, ...] = ()

    def __str__(self) -> str:
        return self.text

    def __len__(self) -> int:
        return len(self.text)

    def __contains__(self, sub: str) -> bool:
        return sub in self.text

    def _mark(self, offset: int) -> Mark | None:
        index = bisect_right(self.marks, offset, key=lambda mark: mark.offset) - 1
        return self.marks[index] if index >= 0 else None

    def node(self, offset: int) -> TexContainer | None:
        """The node that wrote the character at `offset`, if there is one."""
        mark = self._mark(offset)
        return None if mark is None else mark.node

    def position(self, offset: int) -> Position | None:
        """The `(line, column)` position of the character at `offset` in the source."""
        mark = self._mark(offset)
        if mark is None or mark.node.start_position is None:
            return None
        inside = offset - mark.offset
        if inside >= mark.length:
            # In the blank that follows the piece: that blank is written nowhere.
            return mark.node.end_position
        return _inside(mark.node, inside) if mark.faithful else mark.node.start_position

    def at(self, offset: int) -> Found:
        """The character at `offset`, with its node and its position."""
        return Found(
            offset, offset + 1, self.text[offset : offset + 1], self.node(offset), self.position(offset)
        )

    def find(self, sub: str) -> list[Found]:
        """Every occurrence of a text, taken as it stands."""
        return self.search(re.escape(sub))

    def search(self, pattern: str | re.Pattern[str], flags: int = 0) -> list[Found]:
        """Every occurrence of a regular expression."""
        return [
            Found(
                match.start(),
                match.end(),
                match.group(),
                self.node(match.start()),
                self.position(match.start()),
            )
            for match in re.finditer(pattern, self.text, flags)
        ]


def to_text(source: TexFile | TexContainer, math: str = "source") -> TexText:
    """The typeset text of an analysed document, of an expanded view or of a node.

    `math` says what the formulas become: `"source"` renders them as written,
    without their delimiters, `"skip"` passes them over. A `TexFile` must have
    been analysed; an expanded view (`expand`) gives its own positions, and the
    branch TeX discards is not read there.
    """
    if math not in ("source", "skip"):
        raise ValueError(f"math expected: 'source' or 'skip', got {math!r}")
    container = source.container if isinstance(source, TexFile) else source
    reader = _Reader(math)
    reader.read(container.content if container.list_container else [container])
    return TexText(reader.text(), tuple(reader.marks))


def _inside(node: TexContainer, delta: int) -> Position:
    """The position of the `delta`-th rendered character of a text node.

    The text of a node reduces every run of blanks of the source to one space
    (`'Un  texte'` reads `'Un texte'`): we read the source again, counting.
    """
    raw = node.raw_text()
    line, column = node.start_position or (1, 0)
    seen = index = 0
    while index < len(raw) and seen < delta:
        if raw[index].isspace():
            while index < len(raw) and raw[index].isspace():
                line, column = (line + 1, 0) if raw[index] == "\n" else (line, column + 1)
                index += 1
        else:
            index, column = index + 1, column + 1
        seen += 1
    return (line, column)


class _Writer:
    """Puts the text together: blanks are asked for, not written, and the strongest wins."""

    __slots__ = ("_accent", "_cursor", "_length", "_pending", "marks", "parts")

    def __init__(self) -> None:
        self.parts: list[str] = []
        self.marks: list[Mark] = []
        self._length = 0
        self._pending = ""
        self._accent = ""
        self._cursor: Position | None = None

    def arrive(self, position: Position | None) -> None:
        """Before reading a node: a blank if the source has one, nothing if it touches the previous one."""
        if self._cursor is not None and position != self._cursor:
            self.blank(" ")

    def leave(self, position: Position | None) -> None:
        if position is not None:
            self._cursor = position

    def blank(self, blank: str) -> None:
        # Nothing written yet: no blank at the head of the text.
        if self.parts and BLANKS[blank] > BLANKS[self._pending]:
            self._pending = blank

    def accent(self, combining: str) -> None:
        self._accent = combining

    def write(self, chunk: str, node: TexContainer, faithful: bool = False) -> None:
        if not chunk:
            return
        if self._accent:
            composed = unicodedata.normalize("NFC", chunk[0] + self._accent) + chunk[1:]
            # `\\'z` does not compose: the mark stays one character more, and the map would not follow.
            faithful = faithful and len(composed) == len(chunk)
            chunk, self._accent = composed, ""
        if self._pending:
            self.parts.append(self._pending)
            self._length += len(self._pending)
            self._pending = ""
        self.marks.append(Mark(self._length, len(chunk), node, faithful))
        self.parts.append(chunk)
        self._length += len(chunk)

    def text(self) -> str:
        return "".join(self.parts)


class _Reader:
    """Walks the tree and writes what TeX would typeset, to the word."""

    def __init__(self, math: str) -> None:
        self._math = math
        self._writer = _Writer()
        self.marks = self._writer.marks

    def text(self) -> str:
        return self._writer.text()

    def read(self, nodes: Sequence[TexContainer], bound: set[int] | None = None) -> None:
        """Read a run of nodes; `bound` is the set of arguments already read by their command."""
        bound = set() if bound is None else bound
        for node in nodes:
            # An argument already read by its command: we only move on, with no blank in its place.
            if id(node) in bound:
                self._writer.leave(node.end_position)
                continue
            self._writer.arrive(node.start_position)
            self._node(node, bound)
            self._writer.leave(node.end_position)

    def _node(self, node: TexContainer, bound: set[int]) -> None:
        if isinstance(node, TexComment):
            return
        if isinstance(node, TexBranch):
            # A discarded branch is not read by TeX: it typesets nothing.
            if node.taken:
                self._inner(node)
        elif isinstance(node, TexVerbatim):
            self._writer.write(node.content, node)
        elif isinstance(node, TexGroup):
            self._group(node)
        elif isinstance(node, TexContent) and node.command:
            self._command(node, bound)
        elif node.is_par():
            self._writer.blank("\n\n")
        elif node.list_container:
            self.read(node.content)
        else:
            self._writer.write(node.content.replace("~", " "), node, faithful=True)

    def _inner(self, group: TexGroup, bound: set[int] | None = None) -> None:
        """Read the content of a group: its delimiters are not blanks of the text."""
        self._writer.leave(group.inner_start)
        self.read(group.content, bound)
        self._writer.leave(group.inner_end)

    def _group(self, group: TexGroup) -> None:
        if group.math or (group.env and group.name in MATH_ENVIRONMENTS):
            if self._math == "source":
                self._writer.write(group.arg().strip(), group)
            return
        if not group.env:
            self._inner(group)
            return
        if group.name in SILENT_ENVIRONMENTS:
            return
        self._writer.blank("\n")
        self._inner(group, {id(argument) for argument in group.arguments if argument is not None})
        self._writer.blank("\n")

    def _command(self, command: TexContent, bound: set[int]) -> None:
        name = command.base_name if isinstance(command, TexCommand) else command.content
        if name in SECTIONS or name == "item":
            self._writer.blank("\n")
        elif name in BREAKS:
            self._writer.blank("\n" * BREAKS[name])
        elif name in ACCENTS:
            self._writer.accent(ACCENTS[name])
        elif name in SUBSTITUTIONS:
            self._writer.write(SUBSTITUTIONS[name], command)
        # The command is read: its arguments are not parted from it, not even by a blank of the source.
        self._writer.leave(command.end_position)
        self._arguments(command, bound, silent=name in SILENT_COMMANDS, labelled=name == "item")
        if name in SECTIONS:
            self._writer.blank("\n")

    def _arguments(self, command: TexContent, bound: set[int], silent: bool, labelled: bool) -> None:
        """The bound arguments: the mandatory ones are typeset, the others carry settings.

        `labelled` is for `\\item`, whose optional argument is the label one reads.
        An unknown command has nothing bound (`arguments` is `None`): its braces
        stay siblings, and their content is read as text — the package's tolerant
        stance.
        """
        if not isinstance(command, TexCommand) or command.arguments is None or command.signature is None:
            return
        for spec, argument in zip(command.signature.arguments, command.arguments, strict=False):
            if argument is None:
                continue
            bound.add(id(argument))
            if silent or not (spec.mandatory or labelled):
                continue
            if isinstance(argument, TexGroup):
                self._inner(argument)
            else:
                # An argument in tokens (`\\'e`): the blank that announces it is not typeset.
                self._writer.leave(argument.start_position)
                self.read([argument])
