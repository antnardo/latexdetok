"""Character categories, modelled on TeX's catcodes.

One rule for command names, TeX's own: a control sequence is either a run of
ASCII letters (`\\section`) or a single non-letter character (`\\%`, `\\\\`,
`\\@`). The old whitelist of escape characters let `\\@ifnextchar` through, all
the way to an assertion.

A trailing star (`\\section*`, `\\\\*`, `\\verb*`) belongs to the name: that is
how one searches for it, and the rewriting stays identical to the source.

What counts as a letter depends on the catcodes in force: see `catcodes`.
"""

import re
import string

__all__ = [
    "CLOSING_COUPLE",
    "GROUP_CARS",
    "LETTERS",
    "MATHS_MODE_CAR",
    "MATH_DELIMITERS",
    "NOT_VERBATIM_DELIMITERS",
    "ROOT_NAME",
    "SPACE_CARS",
    "TEX_ROOT",
    "TEX_ROOT_LINES",
    "collapse_spaces",
    "valid_command_name",
    "valid_group_start",
]

ROOT_NAME = "latexfile"

# How editors (TeXShop, TeXstudio, LaTeX Workshop) declare the master document,
# within the first lines: `% !TEX root = ./EM.tex`.
TEX_ROOT = re.compile(r"^\s*%\s*!\s*TEX\s+root\s*=\s*(?P<root>.+?)\s*$", re.IGNORECASE)
TEX_ROOT_LINES = 20

# A verbatim name followed by one of these characters is being named, not used:
# `\newcommand{\myurl}{\url}`, `\let\oldverb\verb`.
NOT_VERBATIM_DELIMITERS = frozenset("}\\ \t\r\n\f\v")

LETTERS = frozenset(string.ascii_letters)
# What a catcode may turn into a letter (see `catcodes`); a command name never leaves this set.
_NAME_CHARACTERS = LETTERS | frozenset("@_:")
# A non-breaking space stays text: it is a `~`, not a blank to collapse.
SPACE_CARS = " \t\r\n\f\v"  # a string, so that str.strip can use it too
_SPACE_RUN = re.compile(f"[{re.escape(SPACE_CARS)}]+")
GROUP_CARS = ("{", "[")
MATHS_MODE_CAR = "$"
CLOSING_COUPLE = {"{": "}", "[": "]", "(": ")"}
# Opening → closing of the math modes.
MATH_DELIMITERS = {"$": "$", "$$": "$$", "\\[": "\\]", "\\(": "\\)"}


def valid_command_name(name: str) -> bool:
    """Letters (ASCII, `@`, `_`, `:`) or a single character, with at most one star after."""
    # `\*` is a control symbol: the star is only stripped when it follows a name.
    base = name[:-1] if len(name) > 1 and name.endswith("*") else name
    if len(base) == 1:
        return True
    return len(base) > 1 and all(c in _NAME_CHARACTERS for c in base)


def valid_group_start(c: str) -> bool:
    return c in GROUP_CARS


def collapse_spaces(text: str) -> str:
    """Collapse blanks the way TeX does, leaving the non-breaking space alone.

    `str.split()` will not do: it also cuts on U+00A0, which is a `~`.
    """
    return _SPACE_RUN.sub(" ", text).strip(" ")
