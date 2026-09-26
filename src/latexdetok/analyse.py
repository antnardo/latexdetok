"""A LaTeX file: reading, analysis, and queries on the tree it yields.

Reading goes without `txtfiles`: its `TextFile` subclasses `pathlib.Path` and
calls `super().__init__()`, which has been broken since Python 3.12, and it
pulls in `chardet` and `nltk` to detect a language nobody needs here. All that
is left is a question of encoding, settled in `resolution.read_lines`.

Line endings. `lines` holds the file as it is written, `\\r\\n` and a lone `\\r`
included. The tokeniser reads the text of every line, whatever ends it, and a
column counts in that text: a `\\r\\n` file gives the tree, the positions and the
diagnostics of its `\\n` copy. What is copied from the source keeps its endings —
`raw_text()`, and `rewrite`, which gives the file back byte for byte. What the
package writes ends its lines in `\\n`, as Python does in memory: `str()` and
`content()`, which rewrite an equivalent LaTeX, the content of a verbatim, the
expanded view, `to_text`.

The other way — reading in `\\n` as Python's text mode does, noting the endings
and putting them back in `rewrite` — was measured against this one on 26
September 2026, on the corpus and on copies of it in `\\r\\n`, in `\\r` and in
mixed endings. Both give every file back and the same analysis, at the same
speed save `rewrite`, which putting the endings back made two to three times
slower on the copy in `\\r\\n`. Yet `lines` and `raw_text()` then no longer say
what the file holds: set against the output of `rewrite`, as the README does,
every line of a `\\r\\n` file counts as changed, and an edit writing a node's
own `raw_text()` back changed 958 files out of 1,181 in mixed endings, whose
line endings it can only guess.
"""

from collections.abc import Sequence
from os import PathLike
from pathlib import Path

from latexdetok.catcodes import CatcodeChange, CatcodeTable
from latexdetok.characters import ROOT_NAME, index_in_line
from latexdetok.classes import Position, TexCommand, TexContainer, TexGroup
from latexdetok.diagnostics import TexDiagnostic
from latexdetok.logger import verbose_logging
from latexdetok.parser import BranchRegion, CatcodeRegions, TexParser
from latexdetok.resolution import TexmfResolver, read_lines, root_of
from latexdetok.signatures import SignatureRegistry

__all__ = ["TexFile", "read_lines"]


class TexFile:
    """A LaTeX source, from a path or from a list of lines.

    `analyse()` builds the tree into `self.container`; the queries
    (`get_sections`, `get_envs`…) then apply to the whole file.
    """

    def __init__(
        self,
        src: str | PathLike[str] | list[str] | tuple[str, ...],
        *,
        encoding: str | None = None,
        name: str | None = None,
        verbose: bool = False,
    ) -> None:
        if isinstance(src, (str, PathLike)):
            self.src_file: Path | None = Path(src)
            self.encoding: str | None
            self.encoding, self.lines = read_lines(self.src_file, encoding)
            self.name = name or self.src_file.name
        elif isinstance(src, (list, tuple)):
            self.src_file = None
            self.encoding = None
            self.lines = list(src)
            self.name = name or "<List of strings content>"
        else:
            raise TypeError(f"expected a path or a list of lines, got {type(src).__name__}")
        self.container = TexGroup(ROOT_NAME, env=True, rootfile=self, position=(1, 0), end_position=(1, 0))
        self.diagnostics: list[TexDiagnostic] = []
        # No `\documentclass`, no `document`, no `% !TEX root`: a file another one includes.
        self.fragment = False
        self.signatures = SignatureRegistry.kernel()
        self.catcode_changes: list[CatcodeChange] = []
        self.root: Path | None = None
        self.follow_inputs = False
        # Tables forced here and there, and decided branches (see `TexParser`): an expanded view.
        self.catcode_regions: CatcodeRegions | None = None
        self.branch_regions: Sequence[BranchRegion] = ()
        self.set_verbose(verbose)

    def __getitem__(self, i: int) -> TexContainer:
        return self.container[i]

    def set_verbose(self, verbose: bool) -> None:
        """Trace the analysis of this file on the console."""
        self.verbose = verbose

    def analyse(self, verbose: bool | None = None, follow_inputs: bool = False) -> TexGroup:
        """Analyse (or re-analyse) the lines. Never raises: whatever does not match
        is read flat, and reported in `self.diagnostics` (see `diagnostics`).
        `verbose=None` keeps the current setting.

        With `follow_inputs`, the files the document loads (`\\input`,
        `\\include`, `\\usepackage`, `\\documentclass`) are looked for the way TeX
        would — the document's folder, then the texmf trees — and read to learn
        their definitions, except those of the distribution (see `resolution`);
        their content does not enter the tree. A chapter that declares its master
        (`% !TEX root = ./EM.tex`) is read after that master's preamble, noted in
        `self.root`.
        """
        if verbose is not None:
            self.set_verbose(verbose)
        self.follow_inputs = follow_inputs
        package = self.src_file is not None and self.src_file.suffix in (".sty", ".cls")
        catcodes = CatcodeTable.package() if package else CatcodeTable.latex()
        signatures = SignatureRegistry.kernel()
        resolver = None
        self.root = None
        if follow_inputs:
            self.root = root_of(self.src_file, self.lines) if self.src_file is not None else None
            # The master is what gets compiled: searches start from its folder.
            main = self.root or self.src_file
            folder = main.parent if main is not None else Path.cwd()
            resolver = TexmfResolver(folder, main=self.src_file)
            if self.root is not None:
                catcodes = (
                    resolver.include(signatures, self.root.name, "tex", catcodes, preamble_only=True)
                    or catcodes
                )
        parser = TexParser(
            self.lines,
            rootfile=self,
            name=self.name,
            signatures=signatures,
            resolver=resolver,
            catcodes=catcodes,
            catcode_regions=self.catcode_regions,
            branch_regions=self.branch_regions,
        )
        with verbose_logging(self.verbose):
            self.container = parser.parse()
        self.diagnostics = parser.diagnostics
        self.fragment = parser.fragment
        self.signatures = parser.signatures
        self.catcode_changes = parser.catcode_changes
        return self.container

    def content(self) -> str:
        """The LaTeX the tree rewrites: blanks collapsed, lines ending in `\\n`; the file is `rewrite`'s."""
        return str(self.container)

    def repr_hierarchy(self, expand: bool = True) -> str:
        return self.container.repr_hierarchy(expand=expand)

    def raw_text(self, container: TexContainer) -> str:
        """The exact source between the positions of `container`, line endings as written."""
        if container.rootfile is not self:
            raise ValueError("this element does not belong to this file")
        if container.start_position is None or container.end_position is None:
            return str(container)
        return self.text_between(container.start_position, container.end_position)

    def text_between(self, start: Position, end: Position) -> str:
        """The exact source from `start` to `end` (excluded), line endings as written.

        Any span of this file: a diagnostic's, an `Expansion`'s, or one that
        `ExpandedFile.source_span` brings back from the expanded view. A column
        beyond the text of its line designates the end of that line, never the
        middle of a `\\r\\n` (see `characters.index_in_line`).
        """
        if end < start:
            raise ValueError(f"the span ends before it starts: {start} to {end}")
        (start_line, start_col), (end_line, end_col) = start, end
        if start_line == end_line:
            line = self._line(start_line)
            return line[index_in_line(line, start_col) : index_in_line(line, end_col)]
        first, last = self._line(start_line, eol=True), self._line(end_line)
        middle = [self._line(lineno, eol=True) for lineno in range(start_line + 1, end_line)]
        return (
            first[index_in_line(first, start_col) :] + "".join(middle) + last[: index_in_line(last, end_col)]
        )

    def _line(self, lineno: int, eol: bool = False) -> str:
        if not 1 <= lineno <= len(self.lines):
            return ""
        line = self.lines[lineno - 1]
        # A list of lines may come without line endings: put them back.
        return line + "\n" if eol and not line.endswith(("\n", "\r")) else line

    def get_commands_arguments(self, *args, **kwargs) -> TexContainer:  # type: ignore[no-untyped-def]
        return self.container.get_commands_arguments(*args, **kwargs)

    def get_commands_to_next(self, *args, **kwargs) -> TexGroup:  # type: ignore[no-untyped-def]
        return self.container.get_commands_to_next(*args, **kwargs)

    def get_envs(self, name: str) -> TexContainer:
        return self.container.get_envs(name)

    def get_preamble(self) -> TexContainer:
        return self.container.get_preamble()

    def get_sections(self, starred: bool = True) -> TexContainer:
        return self.container.get_sections(starred=starred)

    def get_graphics(self) -> TexContainer:
        return self.container.get_graphics()

    def get_inputs(self, document: bool | None = None) -> TexContainer:
        return self.container.get_inputs(document=document)

    def iter(self):  # type: ignore[no-untyped-def]
        return self.container.iter()

    def get_lines_to_next(self, command: str, starred: bool = False) -> list[list[str]]:
        """Source lines of every occurrence of `command` up to the next one;
        the last one runs to `\\end{document}` excluded, or to the end of the file.
        A command left bare (`\\titleformat{\\section}`, see `TexCommand.bare`) is
        not a call: it starts nothing."""
        start_lines = [
            content.start_position[0]
            for content in self.container.get_commands_arguments(command, nargs=0, starred=starred)
            if not (isinstance(content[0], TexCommand) and content[0].bare)
        ]
        if not start_lines:
            return []
        document_env = self.get_envs("document")
        # With no document, to the end of the file; otherwise to the line before \end{document}.
        end = self.container.end_position if len(document_env) == 0 else document_env[0].inner_end
        assert end is not None  # after the analysis, every span is known
        end_line = end[0] if len(document_env) == 0 else end[0] - 1
        stops = [*start_lines[1:], end_line + 1]
        return [self.lines[start - 1 : stop - 1] for start, stop in zip(start_lines, stops, strict=True)]
