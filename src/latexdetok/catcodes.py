"""Character categories (catcodes): the table in force and its changes.

Why a table rather than hard-coded characters. What makes a character the start
of a command, of a comment or of a group is not the character itself but its
category, which the source can change: `@` is a letter in a `.sty` and between
`\\makeatletter` and `\\makeatother`, `_` and `:` are under `\\ExplSyntaxOn`,
`\\catcode`\\%=12` makes `%` an ordinary character. Without following these
changes, `\\newcommand\\rslt@opt` reads as `\\newcommand\\rslt` followed by
`@opt`, and redefines `\\rslt`.

Scope. As in TeX, a change holds until the end of the group where it happens:
the tokeniser keeps one table per open group (see `parser`). `CatcodeTable` is
therefore immutable; changing a category yields another table.

An accepted limit: a category can take its role away from a special character
(`%`, `$`, `{`, `\\` become ordinary) or make a character a letter, a blank or
an ignored character, but it does not give a special role to another character
(`\\catcode`\\|=0`): the `str()` rewriting writes `\\`, `{`, `$`, and could not
reproduce `|`. Those characters stay text.
"""

import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from enum import IntEnum
from string import ascii_letters

__all__ = ["CATCODE_COMMANDS", "CatcodeChange", "CatcodeTable", "Category", "interpret"]


class Category(IntEnum):
    """TeX's sixteen categories, under their numbers."""

    ESCAPE = 0
    BEGIN_GROUP = 1
    END_GROUP = 2
    MATH_SHIFT = 3
    ALIGNMENT = 4
    END_OF_LINE = 5
    PARAMETER = 6
    SUPERSCRIPT = 7
    SUBSCRIPT = 8
    IGNORED = 9
    SPACE = 10
    LETTER = 11
    OTHER = 12
    ACTIVE = 13
    COMMENT = 14
    INVALID = 15


# LaTeX's table for a document (latex.ltx). The form feed and the vertical tab are
# active and superscript there; we read them as blanks, which course sources do not
# use and which thereby leave the lines intact.
_LATEX: dict[str, Category] = {
    "\\": Category.ESCAPE,
    "{": Category.BEGIN_GROUP,
    "}": Category.END_GROUP,
    "$": Category.MATH_SHIFT,
    "&": Category.ALIGNMENT,
    "\r": Category.END_OF_LINE,
    "\n": Category.END_OF_LINE,
    "#": Category.PARAMETER,
    "^": Category.SUPERSCRIPT,
    "_": Category.SUBSCRIPT,
    "\0": Category.IGNORED,
    " ": Category.SPACE,
    "\t": Category.SPACE,
    "\f": Category.SPACE,
    "\v": Category.SPACE,
    "~": Category.ACTIVE,
    "%": Category.COMMENT,
    "\x7f": Category.INVALID,
    **dict.fromkeys(ascii_letters, Category.LETTER),
}

# `\ExplSyntaxOn` (expl3): `_` and `:` letters, blanks and line ends ignored, `~` a space.
EXPL_SYNTAX: dict[str, Category] = {
    "_": Category.LETTER,
    ":": Category.LETTER,
    " ": Category.IGNORED,
    "\t": Category.IGNORED,
    "\r": Category.IGNORED,
    "\n": Category.IGNORED,
    "~": Category.SPACE,
}


# `\catcode <character> [=] <category>`: `\@=11`, `` `@=11 ``, `64=11`, `"40=11`, `'100=11`, `` `\^^M=13 ``.
CATCODE_ASSIGNMENT = re.compile(
    r"""\s*(?:`\\?(?:\^\^(?P<caret>.)|(?P<character>\S))|(?P<decimal>\d+)|"(?P<hex>[0-9A-F]+)|'(?P<octal>[0-7]+))"""
    r"""\s*=?\s*(?P<value>\d+|\\active|\\@letter|\\@other)"""
)
NAMED_CATEGORIES = {"\\active": Category.ACTIVE, "\\@letter": Category.LETTER, "\\@other": Category.OTHER}
MAKEOTHER = re.compile(r"\s*\\(?P<character>\S)")


class CatcodeTable:
    """The category of every character; those it does not name are `OTHER`.

    Immutable and hashable: it serves as a cache key (see `resolution`).
    """

    __slots__ = ("_categories", "_key")

    def __init__(self, categories: Mapping[str, Category]) -> None:
        # `OTHER` is the default: not storing it makes two equivalent tables equal.
        self._categories = {
            character: category
            for character, category in categories.items()
            if category is not Category.OTHER
        }
        self._key = frozenset(self._categories.items())

    @classmethod
    def latex(cls) -> "CatcodeTable":
        """The table of a LaTeX document: `@` is an ordinary character."""
        return _LATEX_TABLE

    @classmethod
    def package(cls) -> "CatcodeTable":
        """The table of a `.sty` or a `.cls`: `@` is a letter."""
        return _PACKAGE_TABLE

    def category(self, character: str) -> Category:
        return self._categories.get(character, Category.OTHER)

    def items(self) -> Iterator[tuple[str, Category]]:
        """The characters the table names, with their category; the others are `OTHER`."""
        return iter(self._categories.items())

    def is_letter(self, character: str) -> bool:
        return self._categories.get(character) is Category.LETTER

    def with_categories(self, changes: Mapping[str, Category]) -> "CatcodeTable":
        """The changed table; a table equal to one already seen is given back as is.

        The tokeniser files what it derives from a table (reading patterns) under
        that table: two equal but distinct tables would compare at every lookup,
        and the expanded view forces thousands of them.
        """
        table = CatcodeTable({**self._categories, **changes})
        return _INTERNED.setdefault(table._key, table)

    def differences(self, other: "CatcodeTable") -> Iterator[tuple[str, Category, Category]]:
        """`(character, here, there)` for every character whose category differs."""
        for character in sorted(self._categories.keys() | other._categories.keys()):
            mine, theirs = self.category(character), other.category(character)
            if mine is not theirs:
                yield character, mine, theirs

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, CatcodeTable):
            return NotImplemented
        return self._key == other._key

    def __hash__(self) -> int:
        return hash(self._key)

    def __repr__(self) -> str:
        changed = {character: int(category) for character, _, category in _LATEX_TABLE.differences(self)}
        return f"CatcodeTable(latex + {changed})"


_LATEX_TABLE = CatcodeTable(_LATEX)
_INTERNED: dict[frozenset[tuple[str, Category]], CatcodeTable] = {_LATEX_TABLE._key: _LATEX_TABLE}
_PACKAGE_TABLE = _LATEX_TABLE.with_categories({"@": Category.LETTER})


@dataclass(frozen=True, slots=True)
class CatcodeChange:
    """A category change read in the source, and what made it.

    `command` names the command (`makeatletter`, `catcode`, `ExplSyntaxOn`…);
    `local` is false for a `\\global\\catcode`.
    """

    position: tuple[int, int]
    character: str
    old: Category
    new: Category
    command: str
    local: bool = True

    def __str__(self) -> str:
        line, col = self.position
        scope = "" if self.local else ", global"
        return (
            f"line {line}, column {col}: \\{self.command}: {self.character!r} "
            f"{self.old.name.lower()} ({self.old}) → {self.new.name.lower()} ({self.new}){scope}"
        )


# The commands `interpret` understands: for any other, no need to pass it the rest of the line.
CATCODE_COMMANDS = frozenset(
    {"makeatletter", "makeatother", "ExplSyntaxOn", "ExplSyntaxOff", "@makeother", "catcode"}
)


def interpret(command: str, following: str) -> dict[str, Category] | None:
    """The categories `\\command` changes, followed by the rest of its line; None if it changes none.

    Understands `\\makeatletter`, `\\makeatother`, `\\ExplSyntaxOn`, `\\ExplSyntaxOff`,
    `\\catcode` in its numeric forms and `\\@makeother\\X`. What goes through a macro
    (`\\let\\do\\@makeother\\dospecials`) would need expanding it: ignored.
    """
    if command == "makeatletter":
        return {"@": Category.LETTER}
    if command == "makeatother":
        return {"@": Category.OTHER}
    if command == "ExplSyntaxOn":
        return dict(EXPL_SYNTAX)
    if command == "ExplSyntaxOff":
        return {character: _LATEX_TABLE.category(character) for character in EXPL_SYNTAX}
    if command == "@makeother":
        match = MAKEOTHER.match(following)
        return {match["character"]: Category.OTHER} if match else None
    if command != "catcode":
        return None
    match = CATCODE_ASSIGNMENT.match(following)
    if match is None:
        return None
    if match["caret"] is not None:
        code = ord(match["caret"])
        character = chr(code - 64 if code >= 64 else code + 64)
    elif match["character"] is not None:
        character = match["character"]
    elif match["decimal"] is not None:
        character = chr(int(match["decimal"]))
    elif match["hex"] is not None:
        character = chr(int(match["hex"], 16))
    else:
        character = chr(int(match["octal"], 8))
    value = match["value"]
    category = NAMED_CATEGORIES[value] if value in NAMED_CATEGORIES else int(value)
    if not 0 <= category <= 15:
        return None
    return {character: Category(category)}
