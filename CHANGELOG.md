# Changelog

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.3.0] — 2026-09-27

### Added

- `latexdetok check -` reads the document from the standard input, and
  `--stdin-filename PATH` says which file those bytes are the content of: its
  folder is where the inclusions are looked for, and its name is what the
  diagnostics carry. An editor can now have the buffer checked — what is being
  typed, before it is saved — through any linter bridge, without an extension
  of its own.
- `TexFile(lines, path=…)`: lines held in memory that say which file they are
  the content of. Same thing in the process: the lines given are the ones read,
  and the path only says where the neighbours are.
- `decode_lines(data, encoding=None)`, the reading of bytes that `read_lines`
  now goes through. A file and a buffer give the same lines, endings included,
  or an editor would be checking another document than the one on disk.

## [0.2.0] — 2026-09-26

### Added

- `TexFile.text_between(start, end)`: the exact source of any span, line
  endings as written — a diagnostic's, an `Expansion`'s, or one that
  `source_span` brings back from the expanded view. `raw_text(node)` is that
  text between the positions of the node.
- `ExpandedFile.source_text(node)`: what produced a node of the expanded view,
  as the user typed it, where `raw_text(node)` is the text of the view.
- `TexCommand.bare`: the command is the argument of another one — taken as a
  plain token (`\section` in `\let\titre\section`, `\demi` in `\frac\demi x`)
  or alone in its braces (`\titleformat{\section}`) — and reads none of its own
  where it is written.

### Fixed

- `get_commands_arguments` gave a command left bare the element that follows
  it, as it does for an unknown command: `\let\titre\section` followed by
  `\newcommand` gave `\section` the `\newcommand`, `\def\vect#1{…}` gave `\vect`
  the parameter text `#1`, and `\let\mm\marginpar` before a blank line gave
  `\marginpar` the paragraph. Such a command now comes alone, `nargs` or not.
- `get_commands_to_next` and `get_lines_to_next` took a command left bare for a
  call: `\titleformat{\section}` in a preamble started a piece. It now neither
  starts one nor ends one.
- `ExpandedFile.source_span` placed the two ends of a node apart. A text that
  starts or ends on an argument cut its use in two (`x\id{ab` without its brace,
  `p_i}{p_j` without its command); for a macro that writes its arguments back in
  another order (`#2#1`), the span ended before it started; for an argument
  that the end code of an environment writes again, it ran from the `\begin` to
  the `\end`. It now covers what produced each piece of the node, uses whole:
  on 1.1 million nodes of expanded views, 2,114 spans grow to their whole use,
  and nothing else changes.

### Changed

- `get_commands_arguments` gives a known command whose signature has no
  argument (`\maketitle`, `\omega`, a `\newcommand{\R}{x}`) alone, as its
  documentation said: it came with the next element, as if it were unknown.
  `nargs` still takes the following elements on demand.

## [0.1.2] — 2026-09-26

### Fixed

- `TexFile.encoding` and `read_lines` said `utf-8-sig` for every UTF-8 file:
  writing the source back with it, as the README does, added a byte order mark
  the file never had. They now say `utf-8`, and `utf-8-sig` only for a file that
  starts with the mark, whether UTF-8 was found or forced.
- `\r\n` line endings came back as `\n`: `rewrite` gave a Windows file back with
  Unix line endings. `TexFile.lines` and `read_lines` now keep the line endings
  as written — `\r\n`, a lone `\r`, even mixed — so that `rewrite` and
  `raw_text()` give the source byte for byte. The tree, the positions, the
  diagnostics, `check`, `to_text` and the expanded view are those of the same
  file in `\n`, checked on 1,181 files and their copies in each ending.
- Lines given as a list with `\r\n` endings: a verbatim kept its `\r`, and the
  expanded view could differ from that of the same lines in `\n`.
- A column beyond the text of a line no longer cuts a `\r\n` in two in `rewrite`
  and `raw_text()`: it designates the end of the line.
- The expanded view was cut into lines where `str.splitlines` cuts: also at a
  form feed, at `\x85` — a cp1252 `…` read as Latin-1 — and at `\x0b`,
  `\x1c`–`\x1e`, `\u2028` and `\u2029`, which TeX and `read_lines` read inside
  the line. Every position after one of them led to the wrong place in the
  source, and one at the end of a line gave the view a blank line: `check` could
  report a `$` never closed in a file that compiles. The view is now cut at its
  line endings alone, like the source.
- The README and the examples write the edited source with `newline=""`, without
  which Windows turns every `\n` into `\r\n`.

### Changed

- `str()`, `content()`, the content of a verbatim and the expanded view end
  their lines in `\n` whatever the file's: they are what the package writes,
  where `lines`, `raw_text()` and `rewrite` are what the file holds.
- `FALLBACK_ENCODINGS` is `("utf-8", "latin-1")`; `characters.index_in_line`
  places a column in a line whatever its ending.

## [0.1.1] — 2026-09-26

### Added

- The `latexdetok` command, installed with the package: `latexdetok check
  cours.tex` does what `python -m latexdetok check` does, and `uvx latexdetok`
  or `pipx install latexdetok` now work.
- The README sets the message of `pdflatex` against the one of `latexdetok
  check`, on the same faulty file; a test reruns the latter.

## [0.1.0] — 2026-09-23

First public release. The tokeniser was rewritten from a private 2018 module,
keeping the tree model and the public API.

### Added

- `TexFile`: a `.tex` file read into a tree of groups, environments, math,
  verbatim and comments, every element carrying its exact position. `str()`
  rewrites the source byte for byte.
- Tolerance: whatever does not match is read flat and reported in
  `diagnostics`, never refused.
- `check` and `python -m latexdetok check`: 26 diagnostics of structure and of
  meaning, with the opening at fault, the fix, and every mistake at once.
  Compiler-style output, `--json`, exit code 1 on an error.
- Signatures: the LaTeX2e kernel binds its arguments, a hand-written table
  covers the common packages, and the definitions of the file (`\newcommand`,
  `\def`, `\NewDocumentCommand`, `\newenvironment`…) are learned while reading.
- Catcodes followed group by group (`\makeatletter`, `\catcode`,
  `\ExplSyntaxOn`), and inclusions looked for the way TeX does (the folder,
  then `kpsewhich`).
- `expand`: a view where the user's macros are replaced by their bodies, every
  node tied to what produced it in the source; the conditionals that can be
  decided without compiling keep both their branches.
- `to_text`: the typeset prose of a document, accents included, every character
  keeping its node and its position.
- `Edit`, `corrected`, `rewrite`: a modified output drawn from the analysis,
  the rest of the source identical byte for byte.
- `outline` and `html_page`: the frame of a long document.
- Translatable messages (`set_language`, `LATEXDETOK_LANG`), English and
  French; the diagnostic codes stay in English and do not move.
- A mypyc build of the core (`scripts/compile.py`), twice as fast, used only
  when it matches the sources.
