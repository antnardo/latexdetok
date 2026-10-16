"""What LaTeX refuses whatever the place: a length, a column type, a parameter.

Why apart from `semantics`. The walk there knows where we are — the mode, the
list, the alignment; what is here knows what a piece of text may be, wherever
it is written: whether `0,1` is a length (it is not: no unit), how many columns
`*{2}{c}|p{1cm}` makes, whether `#2` exists in a definition that takes one
argument. And the lists of commands that behave apart.

Where each list comes from. Every one was compiled with LuaLaTeX, case by case,
or read in the kernel: `PREAMBLE_ONLY` is what latex.ltx declares with
`\\@onlypreamble`; `SHORT_ARGUMENTS`, what refused a blank line in its argument
(“Paragraph ended before \\@sect was complete”) — the text font commands do,
since `\\text@command`, with or without amsmath. A command missing from a list
is a mistake we keep quiet about, never one we make up.
"""

import re
from collections.abc import Iterator
from dataclasses import dataclass

__all__ = [
    "AMSMATH_ROWS",
    "ARRAY_PACKAGES",
    "BOX_COMMANDS",
    "BOX_ENVIRONMENTS",
    "CAPTION_PLACES",
    "CLASS_ENVIRONMENTS",
    "COUNTED_LISTS",
    "DELIMITED_COMMANDS",
    "DELIMITER_CHARACTERS",
    "DISPLAY_ENVIRONMENTS",
    "FIXED_COLUMNS",
    "FLOATS",
    "FRACTIONS",
    "INNER_MATH_ENVIRONMENTS",
    "KERNEL_ENVIRONMENTS",
    "KERNEL_OPERATORS",
    "LIST_DEPTH",
    "LIST_LIMITS",
    "MATRICES",
    "NUMBERED_ROWS",
    "PACKAGE_COLUMNS",
    "PLAIN_PACKAGES",
    "PREAMBLE_ONLY",
    "RULES",
    "SHORT_ARGUMENTS",
    "SINGLE_NUMBER",
    "STANDARD_CLASSES",
    "TABLES",
    "TAG_REFUSED",
    "TEXT_COMMANDS",
    "Columns",
    "columns",
    "is_length",
    "parameters",
    "xparse_count",
]

# latex.ltx, `\@onlypreamble`: after `\begin{document}`, “Can be used only in preamble”.
PREAMBLE_ONLY = frozenset(
    {
        "AtEndOfClass",
        "AtEndOfPackage",
        "CheckCommand",
        "DeclareDefaultHookRule",
        "DeclareErrorFont",
        "DeclareFontEncoding",
        "DeclareFontEncodingDefaults",
        "DeclareFontSubstitution",
        "DeclareMathAccent",
        "DeclareMathAlphabet",
        "DeclareMathDelimiter",
        "DeclareMathRadical",
        "DeclareMathSizes",
        "DeclareMathSymbol",
        "DeclareMathVersion",
        "DeclareOption",
        "DeclarePreloadSizes",
        "DeclareSizeFunction",
        "DeclareSymbolFont",
        "DeclareSymbolFontAlphabet",
        "DeclareTextAccent",
        "DeclareTextAccentDefault",
        "DeclareTextCommand",
        "DeclareTextCommandDefault",
        "DeclareTextComposite",
        "DeclareTextCompositeCommand",
        "DeclareTextSymbol",
        "DeclareTextSymbolDefault",
        "documentclass",
        "documentstyle",
        "ExecuteOptions",
        "includeonly",
        "listfiles",
        "LoadClass",
        "LoadClassWithOptions",
        "makeglossary",
        "makeindex",
        "NeedsTeXFormat",
        "nofiles",
        "OptionNotUsed",
        "PassOptionsToClass",
        "PassOptionsToPackage",
        "ProcessOptions",
        "ProvidesClass",
        "ProvidesPackage",
        "RequirePackage",
        "RequirePackageWithOptions",
        "SetMathAlphabet",
        "SetSymbolFont",
        "UndeclareTextCommand",
        "usepackage",
    }
)

# Their arguments refuse a blank line. Not `\footnote`, `\parbox`, `\fbox`, `\mbox`, `\title`:
# those take one.
SHORT_ARGUMENTS = frozenset(
    {
        "part",
        "chapter",
        "section",
        "subsection",
        "subsubsection",
        "paragraph",
        "subparagraph",
        "label",
        "ref",
        "pageref",
        "eqref",
        "cite",
        "caption",
        "author",
        "date",
        "item",
        "textbf",
        "textit",
        "textsl",
        "textsc",
        "textup",
        "textmd",
        "textrm",
        "textsf",
        "texttt",
        "textnormal",
        "emph",
        "textsuperscript",
        "textsubscript",
        "hspace",
        "vspace",
        "input",
        "text",
        "tag",
        "intertext",
    }
)
# What typesets text at once: before the first `\item` of a list, as wrong as text.
TEXT_COMMANDS = frozenset(
    {
        "textbf",
        "textit",
        "textsl",
        "textsc",
        "textup",
        "textmd",
        "textrm",
        "textsf",
        "texttt",
        "textnormal",
        "emph",
        "underline",
        "mbox",
        "fbox",
        "makebox",
        "framebox",
    }
)

# A display of its own: inside math, “Bad math environment delimiter”, or amsmath's
# “Erroneous nesting of equation structures”.
DISPLAY_ENVIRONMENTS = frozenset(
    {
        "equation",
        "equation*",
        "align",
        "align*",
        "gather",
        "gather*",
        "multline",
        "multline*",
        "flalign",
        "flalign*",
        "alignat",
        "alignat*",
        "eqnarray",
        "eqnarray*",
        "displaymath",
    }
)
# Math only, inside a formula: in text, “Missing $ inserted” or “allowed only in math mode”.
MATRICES = frozenset({"matrix", "pmatrix", "bmatrix", "Bmatrix", "vmatrix", "Vmatrix", "smallmatrix"})
INNER_MATH_ENVIRONMENTS = MATRICES | {
    "aligned",
    "alignedat",
    "gathered",
    "split",
    "cases",
    "subarray",
    "array",
}
# Where the `\\` of amsmath takes `[` only right after it: `\\ [0,1]` is a bracket there.
AMSMATH_ROWS = MATRICES | {
    "align",
    "align*",
    "gather",
    "gather*",
    "multline",
    "multline*",
    "flalign",
    "flalign*",
    "alignat",
    "alignat*",
    "aligned",
    "alignedat",
    "gathered",
    "split",
    "cases",
}
# `\newenvironment` refuses them: the kernel defines them whatever the class
# (`\documentclass{minimal}`), and article, book and report define the others.
KERNEL_ENVIRONMENTS = frozenset(
    {
        "array",
        "center",
        "displaymath",
        "document",
        "enumerate",
        "eqnarray",
        "eqnarray*",
        "equation",
        "filecontents",
        "filecontents*",
        "flushleft",
        "flushright",
        "itemize",
        "list",
        "lrbox",
        "math",
        "minipage",
        "picture",
        "sloppypar",
        "tabbing",
        "tabular",
        "tabular*",
        "trivlist",
        "verbatim",
        "verbatim*",
    }
)
CLASS_ENVIRONMENTS = frozenset(
    {
        "description",
        "figure",
        "figure*",
        "quotation",
        "quote",
        "table",
        "table*",
        "thebibliography",
        "theindex",
        "titlepage",
        "verse",
    }
)
STANDARD_CLASSES = frozenset({"article", "book", "report"})

# Packages that add no column type and leave the lists as LaTeX has them: with any other
# one loaded, a letter or a depth may come from what we do not read.
PLAIN_PACKAGES = frozenset(
    {
        "amsmath",
        "amssymb",
        "amsfonts",
        "amsthm",
        "mathtools",
        "graphicx",
        "graphics",
        "xcolor",
        "color",
        "geometry",
        "babel",
        "fontenc",
        "inputenc",
        "lmodern",
        "hyperref",
        "url",
        "booktabs",
        "multirow",
        "float",
        "caption",
        "subcaption",
        "capt-of",
        "fontspec",
        "unicode-math",
        "microtype",
        "tikz",
        "pgfplots",
        "physics",
        "textcomp",
        "mathrsfs",
        "bm",
        "upgreek",
        "esint",
        "cancel",
        "xspace",
        "fancyhdr",
        "titlesec",
        "setspace",
        "parskip",
        "csquotes",
        "lipsum",
        "ragged2e",
        "wrapfig",
        "listings",
        "xparse",
        "etoolbox",
        "calc",
        "ifthen",
        "array",
        "tabularx",
        "tabulary",
        "siunitx",
        "dcolumn",
        "colortbl",
        "longtable",
    }
)
# Packages that load array (`m{…}`, `>{…}`), and the column types packages add, with the
# groups each one reads after it.
ARRAY_PACKAGES = frozenset({"array", "tabularx", "tabulary", "siunitx", "dcolumn", "colortbl"})
PACKAGE_COLUMNS: dict[str, dict[str, int]] = {
    "siunitx": {"S": 0, "s": 0},
    "dcolumn": {"D": 3},
    "tabulary": {"L": 0, "C": 0, "R": 0, "J": 0},
}

# One number for the whole display; one per row; and where `\tag` numbers nothing.
SINGLE_NUMBER = frozenset({"equation", "equation*", "multline", "multline*"})
NUMBERED_ROWS = frozenset(
    {"align", "align*", "gather", "gather*", "flalign", "flalign*", "alignat", "alignat*"}
)
TAG_REFUSED = frozenset({"aligned", "alignedat", "gathered", "split"})
# Columns fixed by the environment: amsmath's `cases` and `split`, the kernel's `eqnarray`.
FIXED_COLUMNS = {"cases": 2, "split": 2, "eqnarray": 3, "eqnarray*": 3}
# Tables whose specification is their last mandatory argument, and the rules that start a row:
# `\noalign` itself, which they all are (a `\whline` of the document is `\noalign{…}\hline`).
TABLES = frozenset({"tabular", "tabular*", "tabularx", "array", "longtable"})
RULES = frozenset(
    {
        "noalign",
        "hline",
        "cline",
        "toprule",
        "midrule",
        "bottomrule",
        "cmidrule",
        "specialrule",
        "addlinespace",
    }
)

# What LaTeX counts: four itemize, four enumerate, six of anything built on `\list`.
LIST_LIMITS = {"itemize": 4, "enumerate": 4}
LIST_DEPTH = 6
COUNTED_LISTS = frozenset(
    {"itemize", "enumerate", "description", "list", "quote", "quotation", "verse", "thebibliography"}
)

# Floats, and where `\caption` may stand; what boxes its content, where no float opens.
FLOATS = frozenset({"figure", "figure*", "table", "table*"})
CAPTION_PLACES = FLOATS | {"longtable", "wrapfigure", "wraptable", "sidewaysfigure", "sidewaystable"}
BOX_ENVIRONMENTS = frozenset({"minipage", "tabular", "tabular*", "tabularx", "array"}) | FLOATS
BOX_COMMANDS = frozenset(
    {
        "parbox",
        "fbox",
        "mbox",
        "makebox",
        "framebox",
        "raisebox",
        "footnote",
        "footnotetext",
        "marginpar",
        "savebox",
        "sbox",
    }
)

# After `\left`, `\right`, `\middle` and the `\big` family: a delimiter, or “Missing delimiter”.
DELIMITED_COMMANDS = frozenset(
    {"left", "right", "middle"}
    | {size + side for size in ("big", "Big", "bigg", "Bigg") for side in ("", "l", "r", "m")}
)
DELIMITER_CHARACTERS = frozenset("()[]/|.<>")
# The operators of the kernel (fontmath.ltx, the log-like of latex.ltx) and of amsmath:
# `\limits` after anything else is “Limit controls must follow a math operator”.
KERNEL_OPERATORS = frozenset(
    {
        "sum",
        "prod",
        "coprod",
        "int",
        "intop",
        "oint",
        "ointop",
        "smallint",
        "bigcap",
        "bigcup",
        "bigvee",
        "bigwedge",
        "bigodot",
        "bigotimes",
        "bigoplus",
        "biguplus",
        "bigsqcup",
        "iint",
        "iiint",
        "iiiint",
        "idotsint",
        "arccos",
        "arcsin",
        "arctan",
        "arg",
        "cos",
        "cosh",
        "cot",
        "coth",
        "csc",
        "deg",
        "det",
        "dim",
        "exp",
        "gcd",
        "hom",
        "inf",
        "ker",
        "lg",
        "lim",
        "liminf",
        "limsup",
        "ln",
        "log",
        "max",
        "min",
        "Pr",
        "sec",
        "sin",
        "sinh",
        "sup",
        "tan",
        "tanh",
        "injlim",
        "projlim",
        "varinjlim",
        "varprojlim",
        "varliminf",
        "varlimsup",
        "mathop",
        "operatorname",
        "overbrace",
        "underbrace",
    }
)
# The generalised fractions of TeX, and the macros of plain TeX that are one.
FRACTIONS = frozenset(
    {
        "over",
        "atop",
        "above",
        "overwithdelims",
        "atopwithdelims",
        "abovewithdelims",
        "choose",
        "brace",
        "brack",
    }
)

# A length: a number and a unit, or a register with a factor; glue may follow.
_NUMBER = r"[+-]*\s*(?:\d+(?:[.,]\d*)?|[.,]\d+)"
_UNIT = r"(?:true\s*)?(?:pt|pc|in|bp|cm|mm|dd|cc|sp|ex|em|px|nd|nc|mu)"
_FIL = r"fil{1,3}"
_GLUE = rf"(?:\s*(?:plus|minus)\s*{_NUMBER}\s*(?:{_UNIT}|{_FIL}))*"
LENGTH = re.compile(rf"\s*{_NUMBER}\s*{_UNIT}{_GLUE}\s*", re.IGNORECASE)
REGISTER = re.compile(rf"\s*[+-]*\s*(?:{_NUMBER})?\s*\\[A-Za-z@]+{_GLUE}\s*")
STRETCH = re.compile(r"\s*\\stretch\s*\{[^{}]*\}\s*")


def is_length(text: str) -> bool | None:
    """Is `text` a length TeX reads (`2pt`, `-1.5em plus 1fil`, `0.5\\linewidth`)?

    None when a command we do not follow is in it: a macro may write any length.
    """
    if LENGTH.fullmatch(text) or REGISTER.fullmatch(text) or STRETCH.fullmatch(text):
        return True
    return None if "\\" in text else False


@dataclass(frozen=True, slots=True)
class Columns:
    """A table specification read: its columns, and the letters that are no column type.

    `count` is None when it cannot be known: a type defined elsewhere, a command.
    `unknown` holds the offset of each letter in the specification.
    """

    count: int | None
    unknown: tuple[tuple[int, str], ...] = ()


# What each letter reads after it: `p{3cm}` one group, `w{c}{3cm}` two.
_LETTERS: dict[str, int] = {"l": 0, "c": 0, "r": 0, "p": 1}
_ARRAY_LETTERS: dict[str, int] = {"m": 1, "b": 1, "w": 2, "W": 2}
# Markers that make no column: `|`, `@{…}`, `!{…}`, `>{…}`, `<{…}`.
_MARKERS: dict[str, int] = {"|": 0, "@": 1}
_ARRAY_MARKERS: dict[str, int] = {"!": 1, ">": 1, "<": 1}


def columns(spec: str, extra: dict[str, int] | None = None, array: bool = False) -> Columns:
    """The columns of a `tabular` or `array` specification.

    `extra`: the letters packages add (`X` of tabularx), each with the groups it
    reads; `array`: whether the array package is loaded (`m`, `b`, `>{…}`…).
    """
    letters = dict(_LETTERS)
    markers = dict(_MARKERS)
    if array:
        letters.update(_ARRAY_LETTERS)
        markers.update(_ARRAY_MARKERS)
    letters.update(extra or {})
    count: int | None = 0
    unknown: list[tuple[int, str]] = []
    index = 0
    while index < len(spec):
        character = spec[index]
        if character.isspace():
            index += 1
        elif character == "*":
            times, index = _group(spec, index + 1)
            inner, index = _group(spec, index)
            repeated = columns(inner or "", extra, array)
            if times is None or not times.strip().isdigit() or repeated.count is None or count is None:
                count = None
            else:
                count += int(times) * repeated.count
        elif character in markers:
            index = _skip(spec, index + 1, markers[character])
        elif character in letters:
            index = _skip(spec, index + 1, letters[character])
            count = None if count is None else count + 1
        elif character == "\\" or not character.isalpha():
            return Columns(None, tuple(unknown))  # a command, or something we cannot read
        else:
            unknown.append((index, character))
            count = None
            index += 1
    return Columns(count, tuple(unknown))


def _group(spec: str, index: int) -> tuple[str | None, int]:
    """The braced group at `index`, and where it ends; None if there is none."""
    while index < len(spec) and spec[index].isspace():
        index += 1
    if index >= len(spec) or spec[index] != "{":
        return None, index
    depth = 0
    for end in range(index, len(spec)):
        if spec[end] == "{":
            depth += 1
        elif spec[end] == "}":
            depth -= 1
            if depth == 0:
                return spec[index + 1 : end], end + 1
    return None, len(spec)


def _skip(spec: str, index: int, groups: int) -> int:
    for _ in range(groups):
        _, index = _group(spec, index)
    return index


def parameters(text: str) -> Iterator[tuple[int, int]]:
    """`(offset, digit)` of every `#1`… of a definition body: not `##1`, which belongs to one inside."""
    index = 0
    while index < len(text):
        if text[index] != "#":
            index += 1
            continue
        end = index
        while end < len(text) and text[end] == "#":
            end += 1
        if end - index == 1 and end < len(text) and text[end].isdigit() and text[end] != "0":
            yield index, int(text[end])
        index = end


# xparse: the letters that make an argument, and the groups or tokens each one reads.
_XPARSE_GROUPS = {"m": 0, "o": 0, "s": 0, "v": 0, "b": 0, "l": 0, "g": 0, "O": 1, "G": 1}
_XPARSE_TOKENS = {"d": 2, "t": 1, "r": 2}
_XPARSE_BOTH = {"D": (2, 1), "R": (2, 1)}


def xparse_count(spec: str) -> int | None:
    """The arguments of an xparse specification (`O{x}mm` takes three); None if it cannot be read."""
    count = 0
    index = 0
    while index < len(spec):
        character = spec[index]
        if character.isspace() or character in "+!":
            index += 1
        elif character in ">=":
            index = _skip(spec, index + 1, 1)
        elif character in _XPARSE_GROUPS:
            count += 1
            index = _skip(spec, index + 1, _XPARSE_GROUPS[character])
        elif character in _XPARSE_TOKENS:
            count += 1
            index += 1 + _XPARSE_TOKENS[character]
        elif character in _XPARSE_BOTH:
            tokens, groups = _XPARSE_BOTH[character]
            count += 1
            index = _skip(spec, index + 1 + tokens, groups)
        else:
            return None  # `e{…}`, `u{…}`, an argument processor we do not follow
    return count
