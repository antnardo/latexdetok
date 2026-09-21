"""TeX's conditionals: recognising them, and deciding the ones that can be decided without compiling.

Two families.

- Primitive conditionals, delimited by `\\else` and `\\fi`: `\\ifnum`,
  `\\ifmmode`, the booleans of `\\newif` (`\\ifprof`)… They do not respect
  groups, so we match them token by token, in the order of the text. An `\\if…`
  we do not know is counted as a conditional: that is what TeX would do if it is
  one, and if it is not, the conditionals open around it are simply not decided.
  One that is followed by a brace is a package macro (`\\ifstrempty{…}`,
  `\\ifthenelse{…}`), not a primitive conditional.
- Conditionals with arguments, whose branches are arguments:
  `\\IfBooleanTF{b}{A}{B}` and its relatives (ltcmd), `\\ifstrempty{t}{A}{B}` and
  `\\ifblank` (etoolbox).

What can be decided, and how:

- `\\iftrue`, `\\iffalse`;
- `\\ifnum a<b` when `a` and `b` are numbers written in decimal;
- a boolean of `\\newif`, from its value followed as reading goes on (see
  `TexParser`);
- `\\ifmmode`, from the open groups: math, math environment, argument of a
  command that goes back to text (`\\mbox`, `\\text`). The argument of an unknown
  command, or an unknown environment, leaves the question open: the command may
  enter math (`\\ensuremath`) or leave it;
- `\\IfBooleanTF{\\BooleanTrue}` (what the expansion substitutes for a star that
  was read), `\\IfValueTF{-NoValue-}` (a missing optional argument);
- `\\ifstrempty{text}`: etoolbox does not expand the argument, its text is
  enough.

The rest (`\\ifx`, `\\ifdim`, `\\ifdefined`, `\\ifthenelse`…) is not decided.
"""

import re
from collections.abc import Callable
from enum import StrEnum

__all__ = [
    "ARGUMENT_TESTS",
    "BOOLEAN_FALSE",
    "BOOLEAN_TRUE",
    "MATH_ARGUMENT_COMMANDS",
    "MATH_ENVIRONMENTS",
    "NO_VALUE",
    "PRIMITIVE_CONDITIONALS",
    "ROLE_NAMES",
    "TEXT_ARGUMENT_COMMANDS",
    "Role",
    "argument_test_value",
    "conditional_role",
    "ifnum_test",
    "setter",
]

BOOLEAN_TRUE = "\\BooleanTrue"
BOOLEAN_FALSE = "\\BooleanFalse"
NO_VALUE = "-NoValue-"


class Role(StrEnum):
    """The role of a command in a primitive conditional."""

    IF = "if"
    ELSE = "else"
    OR = "or"
    FI = "fi"


# Primitive conditionals of TeX, e-TeX and pdfTeX.
PRIMITIVE_CONDITIONALS = frozenset(
    {
        "if",
        "ifcat",
        "ifnum",
        "ifdim",
        "ifodd",
        "ifvmode",
        "ifhmode",
        "ifmmode",
        "ifinner",
        "ifvoid",
        "ifhbox",
        "ifvbox",
        "ifx",
        "ifeof",
        "iftrue",
        "iffalse",
        "ifcase",
        "ifdefined",
        "ifcsname",
        "iffontchar",
        "ifincsname",
        "ifabsnum",
        "ifabsdim",
        "ifpdfprimitive",
    }
)

# `\ifnum <number> <relation> <number>`: TeX eats one space after the second number.
IFNUM_TEST = re.compile(r"\s*([+-]?\d+)\s*([<=>])\s*([+-]?\d+)(?!\d) ?")
COMPARE: dict[str, Callable[[int, int], bool]] = {
    "<": lambda a, b: a < b,
    "=": lambda a, b: a == b,
    ">": lambda a, b: a > b,
}

# True and false branches of each conditional with arguments: argument indices, None if absent.
ARGUMENT_TESTS: dict[str, tuple[int | None, int | None]] = {
    "IfBooleanTF": (1, 2),
    "IfBooleanT": (1, None),
    "IfBooleanF": (None, 1),
    "IfValueTF": (1, 2),
    "IfValueT": (1, None),
    "IfValueF": (None, 1),
    "IfNoValueTF": (1, 2),
    "IfNoValueT": (1, None),
    "IfNoValueF": (None, 1),
    "ifstrempty": (1, 2),
    "ifblank": (1, 2),
}


# Separate or close a conditional; the other commands that play a role in one start with `if`.
ROLE_NAMES = frozenset({"else", "or", "fi"})


def conditional_role(
    name: str, following: str, is_boolean: Callable[[str], bool], known: bool
) -> Role | None:
    """The role of `\\name` in a primitive conditional; None for any other command.

    `following` is what comes next on the line, `known` says whether the registry
    knows the command (a macro named `\\if…` is not a conditional).
    """
    if name in ROLE_NAMES:
        return Role(name)
    if not name.startswith("if"):
        return None
    if name in PRIMITIVE_CONDITIONALS or is_boolean(name[2:]):
        return Role.IF
    if known or following.lstrip().startswith("{"):
        return None
    return Role.IF


def ifnum_test(following: str) -> tuple[bool, int] | None:
    """The value of `\\ifnum` followed by `following`, and the length of the test.

    None when the numbers are not written out.
    """
    match = IFNUM_TEST.match(following)
    if match is None:
        return None
    left, relation, right = match.groups()
    return COMPARE[relation](int(left), int(right)), match.end()


def setter(name: str, is_boolean: Callable[[str], bool]) -> tuple[str, bool] | None:
    """`\\proftrue` → `("prof", True)` for a known boolean; None otherwise."""
    for suffix, value in (("true", True), ("false", False)):
        if name.endswith(suffix) and is_boolean(name[: -len(suffix)]):
            return name[: -len(suffix)], value
    return None


def argument_test_value(name: str, test: str) -> bool | None:
    """The value of a conditional with arguments, from the text of its first argument; None if undecidable."""
    stripped = test.strip()
    if name.startswith("IfBoolean"):
        if stripped == BOOLEAN_TRUE:
            return True
        if stripped == BOOLEAN_FALSE:
            return False
        return None
    if name.startswith("IfValue"):
        return stripped != NO_VALUE
    if name.startswith("IfNoValue"):
        return stripped == NO_VALUE
    if name == "ifstrempty":
        return test == ""
    if name == "ifblank":
        return stripped == ""
    return None


# For `\ifmmode`. Environments whose inside is math (kernel, amsmath).
MATH_ENVIRONMENTS = frozenset(
    {
        "math",
        "displaymath",
        "equation",
        "equation*",
        "eqnarray",
        "eqnarray*",
        "array",
        "align",
        "align*",
        "alignat",
        "alignat*",
        "flalign",
        "flalign*",
        "gather",
        "gather*",
        "multline",
        "multline*",
        "aligned",
        "alignedat",
        "gathered",
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
        # mathtools and nicematrix, declared in `data/packages.txt`.
        "dcases",
        "dcases*",
        "rcases",
        "drcases",
        "multlined",
        "psmallmatrix",
        "bsmallmatrix",
        "vsmallmatrix",
        "Bsmallmatrix",
        "Vsmallmatrix",
        "NiceArray",
        "NiceMatrix",
        "pNiceMatrix",
        "bNiceMatrix",
        "vNiceMatrix",
    }
)
# Commands whose argument is math, wherever they are.
MATH_ARGUMENT_COMMANDS = frozenset({"ensuremath"})
# Commands whose argument is text, even inside math.
TEXT_ARGUMENT_COMMANDS = frozenset(
    {
        "mbox",
        "makebox",
        "fbox",
        "framebox",
        "parbox",
        "raisebox",
        "sbox",
        "savebox",
        "hbox",
        "vbox",
        "vtop",
        "text",
        "textrm",
        "textsf",
        "texttt",
        "textmd",
        "textbf",
        "textup",
        "textit",
        "textsl",
        "textsc",
        "textnormal",
        "emph",
        "intertext",
        "shortintertext",
        "colorbox",
        "fcolorbox",
    }
)
