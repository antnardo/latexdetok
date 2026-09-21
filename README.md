# latexdetok

A tolerant LaTeX tokeniser: a `.tex` file read into a tree of groups, commands
and text, which rewrites itself identically and never refuses valid LaTeX.
Written to analyse course sources, full of personal macros and included files.

```pycon
>>> from latexdetok import TexFile, expand
>>> tex = TexFile(["\\newcommand{\\R}{\\mathbb{R}}\n", "\\section{Intro}\n", "Soit $x\\in\\R$.\n"])
>>> _ = tex.analyse()
>>> [str(node) for node in tex.get_sections()[0]]
['\\section', '{Intro}']
>>> print(expand(tex).content().splitlines()[-1])
Soit $x\in\mathbb{R}$.

```

## What it does

- **Tree**: groups, environments, math, verbatim, comments, with the exact
  position of every element; `str()` rewrites the source.
- **Tolerance**: whatever does not match is read flat and reported
  (`diagnostics`), never refused.
- **Diagnostics**: `check` and `python -m latexdetok check` say what is wrong,
  better than TeX does: the cause rather than the place where TeX gave up, the
  opening facing the closing, the fix, every mistake at once. Structure and
  meaning (`\item` outside a list, `&` outside a table, `^` outside math, a
  missing argument…), macros expanded, and nothing that may be valid is an
  error.
- **Signatures**: the commands of the LaTeX2e kernel bind their arguments
  (`\section`: `s o m`), and a hand-written table covers the common packages
  (amsmath, graphicx, siunitx, beamer, booktabs, enumitem…); the definitions of
  the file (`\newcommand`, `\def`, `\NewDocumentCommand`, `\newenvironment`…)
  are learned while reading.
- **Catcodes**: `\makeatletter`, `\catcode`, `\ExplSyntaxOn`, group by group.
- **Inclusions**: `\input`, `\usepackage`, `\documentclass` looked for the way
  TeX does (the folder, then `kpsewhich`), `% !TEX root` of chapters.
- **Expanded view**: the user's macros replaced by their bodies, every node tied
  to what produced it in the source.
- **Conditionals**: the ones that can be decided without compiling are, and both
  their branches stay in the tree.
- **Typeset text**: `to_text` renders the prose of a document, accents included,
  to look for a sentence in it; every character keeps its node and its position.
- **Translatable messages**: the diagnostics read in English or in French
  (`set_language`, `LATEXDETOK_LANG`); the codes, for their part, do not move.
- **Edits**: a modified output drawn from the analysis, the rest of the source
  identical byte for byte — for a minimal diff.
- **Frame**: an outline in text, an HTML page with two views.
- **Speed**: the core compiles with mypyc, twice as fast.

## Installing

```bash
pip install latexdetok
```

Python 3.13 or newer, the standard library alone. To look for inclusions in the
texmf trees, TeX Live (`kpsewhich`); without it, only the files of the
document's folder are found.

## Documentation

| Document | Contents |
| --- | --- |
| [docs/EXAMPLES.md](docs/EXAMPLES.md) | recipes, from analysing a file to the HTML page; every one checked as a doctest |
| [docs/API.md](docs/API.md) | the full reference: classes, functions, attributes, constants, scripts |
| [docs/AUDIT.md](docs/AUDIT.md) | defects fixed, API changes, known limits |
| [CHANGELOG.md](CHANGELOG.md) | what changed, release by release |

## Diagnosing a document

```bash
python3 -m latexdetok check cours.tex
python3 -m latexdetok check chapitres/ --json
```

Compiler-style output, exit code 1 if there is an error.

In VS Code, two tasks turn that output into problems in the editor. Declared in
the user's `tasks.json` rather than a workspace's, they work in every folder:

| Task | What it checks |
| --- | --- |
| `latexdetok check` | the open file; it is the default build task, so ⇧⌘B runs it |
| `latexdetok check (folder)` | every `.tex` of the folder of the open file |

The first one keeps the terminal closed: only the Problems panel and the
underlines in the editor. `LATEXDETOK_LANG` in the task picks the language of
the messages; the problem matchers accept both, so switching it changes nothing
else. The file itself is in [docs/EXAMPLES.md](docs/EXAMPLES.md).

## Developing

```bash
uv sync --group dev
uv run pytest -q
LATEXDETOK_PURE=1 uv run pytest -q
uv run ruff check src tests scripts && uv run ruff format --check src tests scripts
uv run mypy
```

The core compiles with mypyc, for the interpreter that will run it; the
extensions only serve if they match the sources, so one edited module is enough
to fall back on them until the next build.

```bash
uv sync --group dev --group compile
uv run python scripts/compile.py
```

After a change to the tokeniser, on a corpus of `.tex` of one's own:

```bash
python3 scripts/corpus.py path/to/corpus --inputs --expand
python3 scripts/fingerprints.py path/to/corpus --inputs --expand -o before.json
```

## Layout

| Path | Contents |
| --- | --- |
| `analyse.py` | `TexFile`: reading, analysis, queries |
| `checks.py`, `__main__.py` | `check`: the diagnostics of a document; the command line |
| `diagnostics.py`, `semantics.py` | structure diagnostics and renderings; meaning diagnostics |
| `classes.py` | the nodes of the tree |
| `parser.py`, `binding.py` | tokeniser, binding of the arguments |
| `catcodes.py`, `characters.py` | character categories, names |
| `signatures.py`, `definitions.py`, `data/` | signatures of the kernel and of the common packages, definitions learned |
| `conditions.py`, `expansion.py` | conditionals, expanded view |
| `resolution.py` | loaded files, `kpsewhich` |
| `messages.py`, `data/messages-*.txt` | messages, one language per file |
| `text.py` | `to_text`: typeset text, map back to the source |
| `export.py` | `Edit`, `corrected`, `rewrite`: editing the source |
| `rendering.py` | outline, HTML page |
| `compilation.py`, `_mypyc/` | loading of the compiled modules (folder ignored by git) |

The package is in `src/latexdetok/`; `scripts/` holds the development helpers
(rendering, corpus, fingerprints, bench, build, signature harvest) and `tests/`
the tests, including the examples of the documentation.

## Licence

MIT — see [LICENSE](LICENSE).
