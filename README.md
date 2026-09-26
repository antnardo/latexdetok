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

## TeX's message, and latexdetok's

A section title that lost its closing brace, and a list closed by the wrong
environment:

```latex
\documentclass{article}
\begin{document}
\section{Introduction
Energy is conserved.

\begin{itemize}
  \item $E = mc^2$
\end{enumerate}
\end{document}
```

`pdflatex` stops at the first mistake, gives no line, and names one of LaTeX's
internal macros:

```text
! File ended while scanning use of \@xdblarg.
<inserted text>
                \par
<*> intro.tex

! Emergency stop.
<*> intro.tex
```

`latexdetok check intro.tex` reports both, each where it is, with the fix:

```text
intro.tex:3:9: error [unclosed-brace] “{” of “\section” never closed
     3 │ \section{Introduction
       │         ^
     5 │
       │ ^ first blank line of the group, line 5: the brace is probably missing before it
     9 │ \end{document}
       │ ^~~~~~~~~~~~~~ “\end{document}” comes before it is closed
       = close it with “}”

intro.tex:8:1: error [crossed-environment] “\begin{itemize}” closed by “\end{enumerate}”
     8 │ \end{enumerate}
       │ ^~~~~~~~~~~~~~~
     6 │ \begin{itemize}
       │ ^~~~~~~~~~~~~~~ opened here

2 errors, 0 warning, 0 info
```

## What it does

- **Tree**: groups, environments, math, verbatim, comments, with the exact
  position of every element; `str()` rewrites the source.
- **Tolerance**: whatever does not match is read flat and reported
  (`diagnostics`), never refused.
- **Diagnostics**: `check` and the `latexdetok check` command say what is
  wrong, better than TeX does: the cause rather than the place where TeX gave
  up, the opening facing the closing, the fix, every mistake at once. Structure
  and meaning (`\item` outside a list, `&` outside a table, `^` outside math, a
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
  identical byte for byte, line endings and byte order mark included — for a
  minimal diff.
- **In the editor**: a language server (`latexdetok-lsp`) underlines the
  buffer as it is typed, with a client for VS Code; any LSP editor can use it.
- **Frame**: an outline in text, an HTML page with two views.
- **Speed**: the core compiles with mypyc, twice as fast.

## Installing

```bash
pip install latexdetok
```

The package installs the `latexdetok` command. For the command alone, with no
Python project around it, `uvx latexdetok check cours.tex` runs it without
installing anything, and `pipx install latexdetok` installs it for good.

Python 3.13 or newer, the standard library alone. To look for inclusions in the
texmf trees, TeX Live (`kpsewhich`); without it, only the files of the
document's folder are found.

## A longer example

A longer course, made up for the example: twelve chapters, 96 sections, a
figure and a list in each, and one mistake.

```pycon
>>> import tempfile
>>> from pathlib import Path
>>> from latexdetok import Edit, TexFile, check, rewrite
>>> lines = ["\\documentclass{book}\n", "\\usepackage{graphicx}\n", "\\begin{document}\n"]
>>> for chapter in range(1, 13):
...     lines.append(f"\\chapter{{Chapter {chapter}}}\n")
...     for section in range(1, 9):
...         lines += [
...             f"\\section{{Part {chapter}.{section}}}\n",
...             f"\\includegraphics[width=6cm]{{fig/{chapter}-{section}}}\n",
...             "\\begin{itemize}\n", "\\item $E = mc^2$\n", "\\item $F = ma$\n", "\\end{itemize}\n",
...         ]
>>> lines.insert(300, "\\item a stray item\n")
>>> lines.append("\\end{document}\n")
>>> folder = Path(tempfile.mkdtemp())
>>> _ = (folder / "course.tex").write_text("".join(lines), encoding="utf-8")

```

Reading it, then the questions one asks of a course: its sections and where
they start, its chapters cut apart, its figures, what is wrong in it.

```pycon
>>> tex = TexFile(folder / "course.tex")
>>> _ = tex.analyse()
>>> len(tex.lines)
593
>>> sections = tex.get_sections()
>>> len(sections), [(found[-1].arg(), found[0].start_position[0]) for found in sections[:3]]
(96, [('Part 1.1', 5), ('Part 1.2', 11), ('Part 1.3', 17)])
>>> chapters = tex.get_lines_to_next("chapter")
>>> len(chapters), [len(chapter) for chapter in chapters[:3]]
(12, [49, 49, 49])
>>> len(tex.get_graphics()), len(tex.get_envs("itemize")), len(tex.get_envs("$"))
(96, 96, 192)
>>> for diagnostic in check(tex):
...     print(diagnostic)
301:1: error [item-outside-list] “\item” outside any list

```

Targeted changes: every figure to the width of the text, and the sections of
the third chapter unnumbered. The rest of the file comes out byte for byte, so
those lines are the only ones that differ.

```pycon
>>> edits = [
...     Edit.inside(found[1], "width=0.8\\textwidth") for found in tex.get_commands_arguments("includegraphics")
... ]
>>> starts = [found[0].start_position[0] for found in tex.get_commands_arguments("chapter")]
>>> edits += [
...     Edit.of(found[0], "\\section*") for found in sections if starts[2] < found[0].start_position[0] < starts[3]
... ]
>>> edited = rewrite(tex, edits)
>>> edited.splitlines()[5]
'\\includegraphics[width=0.8\\textwidth]{fig/1-1}'
>>> pairs = zip(tex.lines, edited.splitlines(keepends=True), strict=True)
>>> changed = [number for number, (before, after) in enumerate(pairs, start=1) if before != after]
>>> len(changed), changed[:4]
(104, [6, 12, 18, 24])

```

On disk, the same: written back with the encoding it was read with, and
`newline=""` so that Python translates no line ending (Windows would turn every
`\n` into `\r\n`), the edited file differs from the course by those lines alone.
`tex.lines` keeps the line endings of the file, `\r\n` included, and
`tex.encoding` says `utf-8-sig` only for a file that starts with a byte order
mark.

```pycon
>>> tex.encoding
'utf-8'
>>> _ = (folder / "course-edited.tex").write_text(edited, encoding=tex.encoding, newline="")
>>> before, after = ((folder / name).read_bytes().splitlines(keepends=True) for name in ("course.tex", "course-edited.tex"))
>>> sum(old != new for old, new in zip(before, after, strict=True))
104

```

## How it compares

Written from a survey of September 2026: the code, the documentation or the
issues of each project were read, and pandoc, ChkTeX and lacheck were run. The
Python packages were not installed: their speed comes from their own published
benchmarks. The inputs differ from one figure to the next, so only the orders of
magnitude mean something.

### What the others do better

- **pylatexenc 3** is the serious Python alternative: MIT, no dependency, exact
  positions, tolerant parsing with recovery nodes. Above all, it is made to be
  extended from the outside — a specification brings its own parser, a construct
  can change the parsing state, the context database is filtered and composed by
  category. Describing a package latexdetok does not know means editing its
  `data/packages.txt`, that is, the package itself; and in compiled mode,
  latexdetok's classes cannot be subclassed. pylatexenc also converts, both ways
  (`latex_to_text`, `latexencode`), where `to_text` only renders prose to search
  in. Version 3 is still a beta.
- **pandoc** is the most complete expander among the tolerant tools: delimited
  `\def`, `\edef`, `\let`, `\newif`, `\newenvironment`, and xparse on its main
  branch, with years of use on every kind of document. It is not a Python
  library.
- **ChkTeX and lacheck** have decades of use behind them. lacheck runs an order
  of magnitude faster (57 MB/s), ChkTeX about as fast (6 MB/s), against some
  3.5 million characters per second here, 7 compiled — for less work, but a
  quick check needs no more. ChkTeX has 49 warnings; latexdetok has 26
  diagnostic codes, and TeXiFy-IDEA 75 inspections.
- **texlab and TeXiFy-IDEA** live in the editor, with a language server or
  IntelliJ's incremental analysis. latexdetok is a command, and a VS Code task
  that runs it.
- **Overleaf's Code Check** has the most readable catalogue of messages, and
  recovery heuristics worth copying: a precedence between delimiters, a look
  back at the previous `\begin`.
- **plasTeX, LaTeXML and Texcraft** are engines: real catcodes, real expansion,
  real `.sty` files executed. Where latexdetok guesses, they know — at the price
  of stopping on what does not run.
- **digestif** builds its signatures from the `.sty` and `.ltx` files actually
  installed. latexdetok describes about thirty packages by hand and falls back
  on heuristics for the rest. Reading the distribution was tried and measured:
  23 times slower, thousands of names invented, kernel signatures lost.
- **TexSoup** is simpler, and enough for a well-formed file.

And latexdetok is young: version 0.2, one author, tried on one corpus — about
960 physics course files written by the same hand. Its rule that nothing valid
is an error was checked there, on the 107 files that compile (a recent log, with
no error); on other packages and other habits, expect infos and warnings it has
not yet learnt to hold back. It needs Python 3.13, knows nothing of incremental
analysis, and its outline and HTML page speak only English.

### Where it differs

Nothing like the following was found elsewhere — which proves nothing about
private tools:

1. **A map from the expanded view back to the source, through nested
   expansions.** pandoc and YaLafi give every expanded token the position of the
   use, a point; flachtex gives each character an origin, without the chain of
   expansions.
2. **Conditionals decided, both branches kept.** pandoc and arxiv-latex-cleaner
   decide, but delete the branch not taken.
3. **Catcodes followed group by group, in a reader that refuses nothing.** The
   other tolerant tools have a global flag (pandoc), one table per kind of file
   (digestif), re-parsed regions (unified-latex) or hard-coded values (texlab).
   Only the engines have the real table, and they tolerate nothing.
4. **A signature deduced from the body of a macro**: a `\@ifstar` in it makes
   the user's command take a star.

### At a glance

| Project | Language | Tolerance | Catcodes | The document's own macros | Diagnostics |
| --- | --- | --- | --- | --- | --- |
| latexdetok | Python, stdlib | never raises, reports | a table per group | learned, expanded, mapped back | 26 codes, with the fix |
| [pylatexenc 3](https://github.com/phfaist/pylatexenc) | Python, stdlib | recovery nodes | `@` settable | a parser to write oneself | exceptions |
| [TexSoup](https://github.com/alvinwan/TexSoup) | Python | unclosed groups | `@` in names | no | no |
| [plasTeX](https://github.com/plastex/plastex) | Python | little | real | executed | the engine's |
| [pandoc](https://github.com/jgm/pandoc) | Haskell | skips and logs | a global `@` flag | expanded, one branch, the position of the use | a log of what was skipped |
| [unified-latex](https://github.com/siefkenj/unified-latex) | TypeScript | permissive grammar | re-parsed regions | `\newcommand`, not `\def`; no link to the use | 9 lint rules |
| [texlab](https://github.com/latex-lsp/texlab) | Rust | error nodes | hard-coded | for completion | 3 syntax errors, labels, plus ChkTeX and the log |
| [ChkTeX](https://ctan.org/pkg/chktex), lacheck | C | no tree | no | no | 49 warnings; unmatched pairs |
| [LaTeXML](https://github.com/brucemiller/LaTeXML) | Perl | little | real | executed | located messages |

### When to use something else

- To **convert** LaTeX into text or back, or to extend the parser from your own
  code: pylatexenc.
- To **convert** a document into another format: pandoc.
- To **run** arbitrary `.sty` files faithfully: plasTeX or LaTeXML.
- For **squiggles while typing**: texlab, with ChkTeX.
- To **query and edit** sources full of personal macros, where the answer must
  point at the right line of the right file, and the edit must leave the rest
  untouched: that is what latexdetok is for.

## Documentation

| Document | Contents |
| --- | --- |
| [docs/EXAMPLES.md](https://github.com/antnardo/latexdetok/blob/main/docs/EXAMPLES.md) | recipes, from analysing a file to the HTML page; every one checked as a doctest |
| [docs/API.md](https://github.com/antnardo/latexdetok/blob/main/docs/API.md) | the full reference: classes, functions, attributes, constants, scripts |
| [CHANGELOG.md](https://github.com/antnardo/latexdetok/blob/main/CHANGELOG.md) | what changed, release by release |

## Diagnosing a document

```bash
latexdetok check cours.tex
latexdetok check chapitres/ --json
```

Compiler-style output, exit code 1 if there is an error.

The path `-` reads the document from the standard input, and
`--stdin-filename` says which file those bytes are the content of: its folder
is where the inclusions are looked for, and its name is what the diagnostics
carry. That is what an editor has to offer — the buffer, with what has just
been typed and not yet saved — and it is enough to plug latexdetok into a
linter bridge (`efm-langserver`, `nvim-lint`, a “lint on save” extension)
without writing one.

```bash
cat cours.tex | latexdetok check - --json --stdin-filename cours.tex
```

## In an editor

A command run on a file answers about what was saved. A **language server**
answers about what is being typed: the editor sends it the buffer at every
keystroke and gets back the places to underline. That is the whole protocol —
one process, started once, spoken to over its standard input.

```bash
pip install "latexdetok[lsp]"   # brings pygls, the only dependency there is
latexdetok-lsp                  # what the editor starts; it waits on stdin
```

Why a server and not the command: `latexdetok check -` answers in 14 to 76 ms
once the package is imported, but a fresh process costs some 600 ms — the
import, then the first search through `kpsewhich`. The server pays that once.

Settings travel in `initializationOptions`: `language` (`en`, `fr`),
`followInputs` and `expand`.

### VS Code

The client lives in
[editors/vscode](https://github.com/antnardo/latexdetok/tree/main/editors/vscode).
It is not on the marketplace; build it and install it in one go:

```bash
cd editors/vscode && npm install && npx @vscode/vsce package -o latexdetok.vsix
code --install-extension latexdetok.vsix
```

Then `latexdetok.serverPath` in the settings, with the whole path of the
server. It is worth setting even when `latexdetok-lsp` is on the `PATH` VS Code
sees: the command is installed by `latexdetok` itself, extra or no extra, so
the first one found may well be an environment where `pygls` is not — it says
so rather than starting, but it does not start.

```json
{
  "latexdetok.serverPath": "~/Envs/Main/bin/latexdetok-lsp",
  "latexdetok.language": "fr"
}
```

### Any other editor

Neovim, Emacs, Kate, Helix: point their LSP client at the command
`latexdetok-lsp`, over stdio, for the `latex` language. There is nothing else
to configure, and nothing of VS Code in the server.

### Without a server

Two tasks give the same diagnostics on demand, without installing anything more
than the package. Declared in the user's `tasks.json` rather than a
workspace's, they work in every folder:

| Task | What it checks |
| --- | --- |
| `latexdetok check` | the open file; it is the default build task, so ⇧⌘B runs it |
| `latexdetok check (folder)` | every `.tex` of the folder of the open file |

The first one keeps the terminal closed: only the Problems panel and the
underlines in the editor. `LATEXDETOK_LANG` in the task picks the language of
the messages; the problem matchers accept both, so switching it changes nothing
else. The file itself is in [docs/EXAMPLES.md](https://github.com/antnardo/latexdetok/blob/main/docs/EXAMPLES.md).

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
| `server.py` | `latexdetok-lsp`: the language server (needs the `lsp` extra) |
| `compilation.py`, `_mypyc/` | loading of the compiled modules (folder ignored by git) |

The package is in `src/latexdetok/`; `scripts/` holds the development helpers
(rendering, corpus, fingerprints, bench, build, signature harvest), `tests/`
the tests, including the examples of the documentation, and `editors/vscode/`
the client that starts the server in VS Code.

## Licence

MIT — see [LICENSE](https://github.com/antnardo/latexdetok/blob/main/LICENSE).
