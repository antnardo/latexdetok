"""Character categories, modelled on TeX's catcodes.

One rule for command names, TeX's own: a control sequence is either a run of
ASCII letters (`\\section`) or a single non-letter character (`\\%`, `\\\\`,
`\\@`). The old whitelist of escape characters let `\\@ifnextchar` through, all
the way to an assertion.

A trailing star (`\\section*`, `\\\\*`, `\\verb*`) belongs to the name: that is
how one searches for it, and the rewriting stays identical to the source.

What counts as a letter depends on the catcodes in force: see `catcodes`.

Any character may be written `^^`: `^^69` is `i`, `^^M` the line end, and
`\\wr^^69te` is `\\write`. TeX reduces the form before it looks at the category,
in a command name as in the text, so the tokeniser does too (`reduce_caret`).
Otherwise valid LaTeX reads wrong: `\\catcode`\\^^M=13` as an accent `\\^`
followed by a superscript outside math, `$x^^2$` (TeX's `$xr$`) as a
superscript with no argument. And whatever reads command names, a filter
included, sees `\\wr` where TeX runs `\\write`. What is reduced enters the
tree as the character it stands for; `str()` writes back in that notation the
control characters it could not print (`caret_notation`), as TeX does in its
messages.
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
    "caret_notation",
    "collapse_spaces",
    "index_in_line",
    "is_control_word",
    "reduce_caret",
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


def is_control_word(name: str) -> bool:
    """Does TeX skip the blanks after `\\name`?

    After a name of letters, yes: `\\LaTeX is` prints “LaTeXis”, `\\i` too. Not
    after a control symbol (`\\,`), nor after a star, a character of its own.
    """
    if len(name) > 1:
        return not name.endswith("*")
    return name in LETTERS


def valid_group_start(c: str) -> bool:
    return c in GROUP_CARS


def index_in_line(line: str, column: int) -> int:
    """Where a column falls in a line: beyond its text, after its line ending, whatever its width.

    A column counts characters in the text of the line, the same whatever the
    file's line endings; one that goes beyond the text designates the end of the
    line, and never cuts a `\\r\\n` in two.
    """
    return column if column <= len(line.rstrip("\r\n")) else len(line)


def collapse_spaces(text: str) -> str:
    """Collapse blanks the way TeX does, leaving the non-breaking space alone.

    `str.split()` will not do: it also cuts on U+00A0, which is a `~`.
    """
    return _SPACE_RUN.sub(" ", text).strip(" ")


# Hexadecimal digits of the `^^` forms: lowercase only, as in TeX (`^^E9` is `^^E` then `9`).
_HEX = frozenset("0123456789abcdef")
# What `str()` cannot write as itself: the C0 controls, DEL and the C1 controls, one of
# which, U+0085, ends a line for `str.splitlines`. A blank never reaches a text (it is
# collapsed), and U+00A0 must stay what it is: a `~`, not a `^^a0`.
_CONTROL = re.compile("[\x00-\x1f\x7f-\x9f]")


def reduce_caret(text: str, col: int) -> tuple[str, int] | None:
    """The character TeX reads for the `^^` form at `col`, and how many columns it takes.

    `text[col]` is a superscript character, which `text[col + 1]` repeats. The
    forms, the longest first: six or four of them followed by as many lowercase
    hexadecimal digits (LuaTeX and XeTeX: `^^^^00e9`), two followed by two
    digits (`^^e9`), two followed by a character below 128, whose code moves by
    64 (`^^M` is the line end, `^^?` DEL). `None` if none applies: `^^é` is two
    superscripts.
    """
    caret = text[col]
    for count in (6, 4):
        end = col + 2 * count
        digits = text[col + count : end]
        if end <= len(text) and text.startswith(caret * count, col) and all(c in _HEX for c in digits):
            code = int(digits, 16)
            if code <= 0x10FFFF:
                return chr(code), 2 * count
    digits = text[col + 2 : col + 4]
    if len(digits) == 2 and digits[0] in _HEX and digits[1] in _HEX:
        return chr(int(digits, 16)), 4
    if col + 2 < len(text) and ord(text[col + 2]) < 128:
        code = ord(text[col + 2])
        return chr(code + 64 if code < 64 else code - 64), 3
    return None


def caret_notation(text: str) -> str:
    """The text, its control characters written as TeX prints them: `^^M`, `^^?`, `^^85`."""
    if _CONTROL.search(text) is None:
        return text
    return _CONTROL.sub(_caret_form, text)


def _caret_form(match: re.Match[str]) -> str:
    code = ord(match[0])
    return "^^" + (chr(code ^ 64) if code < 128 else f"{code:02x}")
