"""Checking a document: the diagnostics of its expanded view, brought back to the source.

Why the expanded view. A macro hides the structure it writes: `\\beq x` without
`\\eeq` leaves nothing open in the source, but an `equation` open in the view;
the other way round, a `\\begin{equation}` closed by `\\eeq` is only orphaned in
the source. Diagnostics are therefore read in the view (see `expansion`), and
each one is brought back to the source: the span as written, or the use of the
macro whose body wrote what is at fault. Loaded files are read for the same
reason: without the user's macros, one sees neither what they open nor what
they close.

Is anything that comes from the source alone lost? No: a diagnostic of the
source the view no longer has is a structure the expansion matched. Discarded
branches of conditions are not diagnosed: TeX does not read them, and they
often match with the other branch (`\\ifprof\\begin{solution}\\else
\\begin{enonce}\\fi`).
"""

import shutil
from collections.abc import Iterable
from os import PathLike

from latexdetok.analyse import TexFile
from latexdetok.classes import Position
from latexdetok.diagnostics import Related, TexDiagnostic
from latexdetok.expansion import ExpandedFile, Expansion, expand
from latexdetok.messages import say
from latexdetok.resolution import TexmfResolver
from latexdetok.semantics import read_meaning

__all__ = ["DocumentFiles", "check", "diagnose", "in_source"]


def check(
    source: TexFile | str | PathLike[str], *, follow_inputs: bool = True, expanded: bool = True
) -> list[TexDiagnostic]:
    """The diagnostics of a document, at source positions, in the order of the text.

    A `TexFile` that is given is re-analysed with these settings. `follow_inputs`
    reads the loaded files (see `TexFile.analyse`), `expanded` expands the user's
    macros; without them, an environment that a macro closes passes for unclosed.
    """
    tex = source if isinstance(source, TexFile) else TexFile(source)
    tex.analyse(follow_inputs=follow_inputs)
    if not expanded:
        files = DocumentFiles(tex) if follow_inputs else None
        return _sorted([*tex.diagnostics, *read_meaning(tex, files)])
    return diagnose(expand(tex))


def diagnose(view: ExpandedFile) -> list[TexDiagnostic]:
    """The diagnostics of an expanded view, structure and meaning, at its source's positions.

    Loaded files are only looked for if the view followed them.
    """
    files = DocumentFiles(view) if view.follow_inputs else None
    return in_source(view, [*view.diagnostics, *read_meaning(view, files)])


class DocumentFiles:
    """A document's files, looked for the way TeX does: build folder, then texmf trees.

    The folder is the master's for a chapter that declares one (see
    `resolution`). Without kpsewhich, no file can be said to be missing anywhere
    but in the folder: a package or a class is not looked for at all.
    """

    def __init__(self, tex: TexFile) -> None:
        main = tex.root or tex.src_file
        self._resolver = TexmfResolver(main.parent) if main is not None else None
        self._texmf = shutil.which("kpsewhich") is not None

    def exists(self, extension: str, name: str) -> bool | None:
        if self._resolver is None or (not self._texmf and extension != "tex"):
            return None
        return self._resolver.find(name, extension) is not None


def in_source(view: ExpandedFile, diagnostics: Iterable[TexDiagnostic]) -> list[TexDiagnostic]:
    """From the view's diagnostics to source positions; what comes from a body, to the use that wrote it.

    A body expanded several times at the same use would give the same diagnostic:
    each one is rendered only once.
    """
    located: dict[tuple[str, str, Position, Position], TexDiagnostic] = {}
    for diagnostic in diagnostics:
        start, end, author = _locate(view, diagnostic.start, diagnostic.end)
        message = diagnostic.message
        if author is not None:
            message = say("message.with-origin", message=message, origin=_written_by(author))
        related = tuple(
            Related(*_locate(view, item.start, item.end)[:2], item.message) for item in diagnostic.related
        )
        moved = TexDiagnostic(
            diagnostic.code, diagnostic.severity, message, start, end, related, diagnostic.suggestion
        )
        located.setdefault((moved.code, moved.message, start, end), moved)
    return _sorted(located.values())


def _locate(
    view: ExpandedFile, start: Position, end: Position
) -> tuple[Position, Position, Expansion | None]:
    source_map = view.source_map
    first, last = source_map.target.offset(start), source_map.target.offset(end)
    source_start = source_map.source_start(first)
    source_end = source_map.source_end(last) if last > first else source_start
    return source_start, max(source_end, source_start), source_map.expansion_at(first)


def _written_by(expansion: Expansion) -> str:
    """The macro whose body wrote it, and the source use its chain starts from, if that is another one."""
    outer = expansion
    while outer.parent is not None:
        outer = outer.parent
    if outer is expansion:
        return say("related.written-by", command=_usage(expansion))
    return say("related.written-by.inside", command=_usage(expansion), outer=_usage(outer))


def _usage(expansion: Expansion) -> str:
    return f"\\begin{{{expansion.name}}}" if expansion.environment else f"\\{expansion.name}"


def _sorted(diagnostics: Iterable[TexDiagnostic]) -> list[TexDiagnostic]:
    return sorted(diagnostics, key=lambda diagnostic: (diagnostic.start, diagnostic.end, diagnostic.code))
