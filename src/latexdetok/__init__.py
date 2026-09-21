"""A LaTeX tokeniser: a `.tex` file read into a tree of groups and commands.

Started on 26 October 2018.

The principle: we push when a group, an environment or a math mode opens; when
it closes, we pop and the group joins its parent. The top of the stack is always
the last group opened.

    F = TexFile("cours.tex")
    F.analyse()
    [section[-1].arg() for section in F.get_sections()]

No valid LaTeX is ever refused: whatever does not match inside the file (the
`\\begin` and `\\end` of a macro definition, a `document` closed in another file)
is read flat and reported in `F.diagnostics` (`TexDiagnostic`).

To learn what is wrong in a document, `check("cours.tex")` returns its
diagnostics: structure and meaning (`\\item` outside a list, `&` outside a table,
`^` outside math…), read in the expanded view and brought back to the source,
with the severity, the opening at fault and the fix (`checks`, `diagnostics`,
`semantics`). On the command line: `python -m latexdetok check cours.tex`.

Messages are in English and can be translated: `set_language("fr")`, or
`LATEXDETOK_LANG=fr`, takes them from `data/messages-fr.txt` (see `messages`).
The diagnostic codes never change: they are what one filters on.

The commands of the LaTeX2e kernel are read with their signature
(`signatures`): `\\section*[short]{Title}` binds its two arguments to the command
(`TexCommand.arguments`), and the definitions of the file (`\\newcommand`…) are
learned as reading goes on. The commands of packages keep heuristics (see
`parser`).

The tree is the source's, macros included. `expand(F)` returns a view where the
user's macros are replaced by their bodies (`\\beq … \\eeq` becomes an
environment there), tied to the source node by node (`expansion`). The
conditionals that can be decided without compiling keep both their branches
(`TexBranch`), each knowing whether TeX takes it.

To look for a sentence rather than a structure, `to_text(F)` returns the
typeset text (`text`): blanks are collapsed there, `\\'e` becomes “é”, labels and
settings disappear, and every character keeps its node and its position in the
source.

From the analysis to a modified output (`export`): an edit replaces a span of
the source (`Edit.of`, `Edit.inside`, `Edit.after`…), `corrected` applies a
function to nodes, `rewrite` returns the edited source — whatever no edit
touches comes out byte for byte, for a minimal diff.

To see the frame of a long document without walking the tree (`rendering`):
`outline(F)` gives its plan and `html_page(F)` a page on its two views, the
compact one and the expanded one, where the outline and the text answer each
other; on the command line, `scripts/render.py`.

Compiled by mypyc (`scripts/compile.py`), the core runs twice as fast; the
extensions only serve if they match the sources (`compilation`, `COMPILED`),
otherwise the sources do.

Catcodes are followed (`catcodes`): `\\makeatletter`, `\\catcode`,
`\\ExplSyntaxOn`, the scope of groups. With `F.analyse(follow_inputs=True)`, the
loaded files are looked for the way TeX does (the folder, then the texmf trees)
and their definitions learned; a chapter that declares `% !TEX root` inherits
the preamble of its master (`resolution`).
"""

# Before any other module of the package: those that follow load compiled, if the build still holds.
from latexdetok.compilation import COMPILED

# isort: split
from latexdetok.analyse import TexFile, read_lines
from latexdetok.catcodes import CatcodeChange, CatcodeTable, Category
from latexdetok.checks import check
from latexdetok.classes import (
    TexBranch,
    TexCommand,
    TexComment,
    TexContainer,
    TexContent,
    TexGroup,
    TexVerbatim,
)
from latexdetok.diagnostics import Related, Severity, TexDiagnostic
from latexdetok.expansion import Condition, ExpandedFile, Expansion, expand
from latexdetok.export import Edit, corrected, rewrite
from latexdetok.logger import logger
from latexdetok.messages import set_language
from latexdetok.parser import TexParser
from latexdetok.signatures import CommandSignature, EnvironmentSignature, Macro, Mode, SignatureRegistry
from latexdetok.text import TexText, to_text

# One source for the version: the publication workflow compares it with the tag.
__version__ = "0.1.0"

__all__ = [
    "COMPILED",
    "CatcodeChange",
    "CatcodeTable",
    "Category",
    "CommandSignature",
    "Condition",
    "Edit",
    "EnvironmentSignature",
    "ExpandedFile",
    "Expansion",
    "Macro",
    "Mode",
    "Related",
    "Severity",
    "SignatureRegistry",
    "TexBranch",
    "TexCommand",
    "TexComment",
    "TexContainer",
    "TexContent",
    "TexDiagnostic",
    "TexFile",
    "TexGroup",
    "TexParser",
    "TexText",
    "TexVerbatim",
    "__version__",
    "check",
    "corrected",
    "expand",
    "logger",
    "read_lines",
    "rewrite",
    "set_language",
    "to_text",
]
