# Changelog

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
[Semantic Versioning](https://semver.org/).

## [Unreleased]

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
