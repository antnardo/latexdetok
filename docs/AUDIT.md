# Audit of latexdetok — September 2026

Starting state: a private module written in 2018, which no longer imported —
it leaned on `txtfiles`, a helper of the same drawer. The tokeniser was
rewritten (`parser.py`), keeping the tree model and the public API. The before/after
reference is the old module run on a corpus of course sources: sections, figures,
`\input` and the splitting by section are identical. Only two differences are
wanted: escaped brackets in math (`$r\in\[0;\infty\[$`) no longer open a formula
of their own, and the `verbatim` environment is now found by `get_envs`.

## Defects found and fixed

### Import and environment

| Defect | Effect | Fix |
| --- | --- | --- |
| `txtfiles` imports `chardet` and `nltk` | `import latexdetok` fails | reading with the standard library, UTF-8 then Latin-1 |
| `txtfiles.TextFile` subclasses `Path` | broken since Python 3.12 | the same |
| `logger.py` opens `log/*.log` at import | fails on any clone, `log/` not being tracked | no more files; the log is quiet by default |
| `TexFile()` set the level of the global log | overwrote the caller's configuration | verbose mode limited to the length of `analyse()` |
| `\[` in a docstring | `SyntaxWarning` under 3.14 | docstrings rewritten |

### Analysis

| Defect | Example | Fix |
| --- | --- | --- |
| positions off by one column | `\\` read back as `o\\`, `\section{Intro}` → `\section{Intr` | every element notes its position at its start and at its end |
| a lone `%` at the end of the file | `IndexError` | the comment is read to the end of the line |
| `\@ifnextchar`, `\(` | `AssertionError` | TeX's rule: letters, or one single character |
| a line starting with `$` | a phantom PAR added | the blank line is detected before reading |
| `$a$$b$` | the stack never closed | a `$` closes `$` math |
| `\[` inside math | phantom nested math | an ordinary command in math mode |
| `[` after text outside math | “voir [1” refused | `[` only opens an option after a command, a group or a `\begin` |
| a second `analyse()` | the tree doubled, the reading stopped | the root is rebuilt |
| valid LaTeX refused | `\newenvironment{sol}{\begin{proof}}{\end{proof}}`, `\def\m{$}`, a `document` closed in another file | provisional openings, dissolved and reported in `diagnostics` |
| code inside `minted`, `Verbatim`, `comment`… | `{`, `$` and `%` unmatched | verbatim environments added |
| a letter made by a catcode inside a name | `\catcode`\é=11 \éte`: `ValueError` | the name read under the table is no longer checked against the default letters (milestone 3) |
| booleans a macro changes, cached by `id()` | a macro redefined then freed could lend its identity to another | the macro is kept along with its result (milestone 3) |
| an unclosed `\verb` | the verbatim ran to the next delimiter, the following lines included | stopped at the end of its line, as in TeX, and reported (milestone 2) |
| `\def\[{…}` | `\[` opened math instead of being named | the first argument of a definition may be `\[`, `\]`, `\(`, `\)` (milestone 2) |
| a taken branch ended by a command with arguments, after `\\` | `\\` then `\rmqcons{…}` (a view of the corpus): the binding of the `\\`, still waiting for a `[`, took its place, and `\rmqcons` lost its argument | the parent's binding closes, the command reads its arguments after the branch (milestone 2) |
| `\LaTeXe` | filed under math mode by the harvest | `any` in `data/kernel.txt` (milestone 2) |

### Rewriting with `str()`

| Defect | Before | After |
| --- | --- | --- |
| words of two lines glued | `helloworld` | `hello\nworld` |
| a command glued to the next word | `\foreach\xin` | `\foreach \x in` |
| a comment with no line ending | `a%cb`: `b` lost | `a%c\nb` |
| a paragraph | `PAR` spelled out | a blank line |
| the root | `\begin{latexfile}…`, which is invalid | the content alone |
| verbatim | lines added, `\verb+a{b+` → `\verb{a{b}` | the exact source |
| a verbatim never closed | an end invented: `\begin{verbatim}x` → `…x\end{verbatim}` | the exact source (milestone 2) |
| `\[x\]` | `$$x$$` | `\[x\]` |

### Model and queries

| Defect | Fix |
| --- | --- |
| `get_preamble`: `AttributeError` | what comes before `\begin{document}` |
| `get_commands_arguments`: `IndexError` at the end of a group | missing arguments left out |
| the caller's list of commands was modified | a copy |
| `get_sections` took `[short]` for the title | the option is read, the title is at `[-1]` |
| `get_inputs` lost the file: `raw_text()` returned `str()` | `rootfile` passed on |
| `get_commands_to_next` ignored `\section*` and `remove_comments` outside `close_at_same_level` | both taken into account |
| `is_in` compared the start of one with the end of the other | containment of the spans |
| `==` raised `AssertionError` against a non-container | `NotImplemented` |
| `TexComment('').is_par()` was true | a PAR is an empty text |
| `TexVerbatim('verb')`: `KeyError` | an explicit `ValueError` |

## API changes

- `analyse()` no longer raises `ValueError` on an unmatched structure; read
  `TexFile.diagnostics` (a list of `TexDiagnostic`).
- `analyse(verbose_stop=…)` removed; `analyse()` returns the root.
- Positions: `(line, column)` pairs covering the whole element; the inside of
  groups is in `inner_start` and `inner_end`.
- `map_contents` and `map_groups`: the accumulator is called `found`.
- `utils.py` deleted (no longer used); the example `.tex` live in
  `tests/fixtures/`.
- Milestone 3: nodes have `__slots__`, so no attribute can be added to them;
  their constructors name their parameters (`position`, `end_position`,
  `rootfile`) instead of `**kwargs`, and `TexCommand` no longer accepts
  `command=`.
- Building with mypyc (`scripts/compile.py`): `latexdetok.COMPILED` says whether
  the extensions are running, `LATEXDETOK_PURE=1` forces the sources. In
  compiled mode, a method of a class of the package can no longer be replaced at
  run time, nor can the class be subclassed outside the package, and an
  attribute of an annotated type refuses a value of another type.
- Milestone 2: `TexWarning` is replaced, with no alias, by `TexDiagnostic` (code,
  severity, message, span, related places, fix); `TexFile.warnings` and
  `TexParser.warnings` become `diagnostics`, whose text and positions change
  (columns from 1 in `str()`). The bodies of definitions appear there as infos,
  instead of being silenced. New: `check`, `TexFile.fragment`,
  `TexVerbatim.closed`, `SignatureRegistry.command_names()` and
  `environment_names()`, the command `python -m latexdetok check`.
- Anglicisation (20 September 2026): the diagnostic codes and the values of
  `Severity` are in English (`accolade-non-fermee` → `unclosed-brace`, `erreur` →
  `error`); the messages leave the code for `data/messages-<language>.txt`, and
  `set_language` or `LATEXDETOK_LANG` chooses the language. Also renamed:
  `data/noyau.txt` → `kernel.txt`, `noyau-recolte.txt` → `kernel-harvested.txt`,
  `paquets.txt` → `packages.txt`, `scripts/empreintes.py` → `fingerprints.py`,
  `LATEXDETOK_PUR` → `LATEXDETOK_PURE`, `get_preambule` → `get_preamble`, and the
  keys of `Block.stats` (`mots` → `words`).

## Known limits

- The tree is the source's: macros are not expanded in it. Since milestone 1,
  kernel commands are read with their signature; those of packages are still
  guessed. The user's macros expand in a separate view, `expand(F)`, where the
  conditionals that can be decided without compiling keep both their branches.
- An unclosed verbatim environment runs to the end of the file; an unclosed
  `\verb`, to the end of its line.
- The `.sty` of the distribution are not read: the common packages are declared
  by hand in `data/packages.txt`, the rest keeps the heuristics. An unknown
  command is therefore only reported when it is one typo away from a known
  command, and nothing is checked inside an unknown environment or argument.
  Missing images are not looked for.

## Checking

```bash
cd latexdetok
python3 -m pytest
ruff check . && ruff format --check .
```

Diagnosing a document:

```bash
python3 -m latexdetok check cours.tex
```

Before and after a change to the tokeniser, on a corpus of `.tex`:

```bash
python3 scripts/fingerprints.py path/to/corpus --inputs --expand -o before.json
python3 scripts/fingerprints.py path/to/corpus --inputs --expand --compare before.json
python3 scripts/bench.py path/to/corpus --inputs --expand
```

The tests run in both modes, and the build is redone after any change to the
sources (otherwise the sources are used):

```bash
python3 -m pytest latexdetok/tests
LATEXDETOK_PURE=1 python3 -m pytest latexdetok/tests
~/.cache/latexdetok-mypyc/bin/python latexdetok/scripts/compile.py
```
