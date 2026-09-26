# The latexdetok API

A reference for everything the package makes public: every module declares its
names in `__all__`, and `latexdetok` re-exports those one usually needs. The
recipes are in [EXAMPLES.md](EXAMPLES.md); the why of the choices, in the module
docstrings.

The few examples on this page run like those of `EXAMPLES.md`
(`tests/test_docs.py`).

## Contents

- [Conventions](#conventions)
- [The `latexdetok` package](#the-latexdetok-package)
- [`analyse`: `TexFile`](#analyse-texfile)
- [`checks`: checking a document](#checks-checking-a-document)
- [`diagnostics`: what is wrong](#diagnostics-what-is-wrong)
- [`semantics`: meaning](#semantics-meaning)
- [Command line](#command-line)
- [`classes`: the tree](#classes-the-tree)
- [`parser`: the tokeniser](#parser-the-tokeniser)
- [`catcodes`: character categories](#catcodes-character-categories)
- [`signatures`: signatures, bodies, booleans](#signatures-signatures-bodies-booleans)
- [`definitions`: what a command defines](#definitions-what-a-command-defines)
- [`binding`: binding the arguments](#binding-binding-the-arguments)
- [`conditions`: TeX's conditionals](#conditions-texs-conditionals)
- [`resolution`: loaded files](#resolution-loaded-files)
- [`expansion`: the expanded view](#expansion-the-expanded-view)
- [`rendering`: the frame of a document](#rendering-the-frame-of-a-document)
- [`text`: the typeset text of a document](#text-the-typeset-text-of-a-document)
- [`export`: editing a source](#export-editing-a-source)
- [`messages`: what is written for a person](#messages-what-is-written-for-a-person)
- [`characters`: characters and names](#characters-characters-and-names)
- [`logger`: the log](#logger-the-log)
- [`compilation`: compiled modules](#compilation-compiled-modules)
- [Scripts](#scripts)

## Conventions

- **Positions**: `Position = tuple[int, int]`, `(line, column)`, the line counted
  from 1, the column from 0, end excluded. `start_position` and `end_position`
  cover the whole element, delimiters included (`\begin{x}` through `\end{x}`);
  `inner_start` and `inner_end` bound the inside of a group.
- **Tolerance**: no analysis raises on LaTeX, valid or not. Whatever does not
  match is read flat and reported (`TexDiagnostic`). Exceptions only come from a
  wrong use of the API (an argument type, a node with no position being
  compared, and so on).
- **Command names**: without the backslash; the star of a starred command stays
  on the name (`section*`), `base_name` takes it off.
- **Signatures**: in the format of `\NewDocumentCommand` (ltcmd): `s o m` for
  `\section`. The LaTeX2e kernel comes from the tables `data/kernel*.txt`; the
  common packages, from `data/packages.txt`, written by hand (their `.sty` are
  not read, see `resolution`). The rest keeps the heuristics.
- **Compiled mode**: if the mypyc extensions match the sources, the thirteen
  modules of the core run compiled (see
  [`compilation`](#compilation-compiled-modules)); the API is the same.

## The `latexdetok` package

```python
from latexdetok import (
    COMPILED,
    CatcodeChange,
    CatcodeTable,
    Category,
    CommandSignature,
    EnvironmentSignature,
    Macro,
    Mode,
    SignatureRegistry,
    Related,
    Severity,
    TexDiagnostic,
    check,
    Condition,
    ExpandedFile,
    Expansion,
    expand,
    TexBranch,
    TexCommand,
    TexComment,
    TexContainer,
    TexContent,
    TexGroup,
    TexVerbatim,
    TexFile,
    TexParser,
    read_lines,
    logger,
)
```

| Name | Defined in | Role |
| --- | --- | --- |
| `TexFile`, `read_lines` | `analyse` | a LaTeX file, its analysis and its queries |
| `TexContainer`, `TexContent`, `TexCommand`, `TexGroup`, `TexBranch`, `TexVerbatim`, `TexComment` | `classes` | the nodes of the tree |
| `TexDiagnostic`, `Severity`, `Related` | `diagnostics` | a diagnostic, a severity, a related place |
| `check` | `checks` | the diagnostics of a document |
| `TexParser` | `parser` | the tokeniser, under `TexFile.analyse` |
| `CatcodeTable`, `Category`, `CatcodeChange` | `catcodes` | character categories |
| `SignatureRegistry`, `CommandSignature`, `EnvironmentSignature`, `Macro`, `Mode` | `signatures` | the signatures and bodies known |
| `expand`, `ExpandedFile`, `Expansion`, `Condition` | `expansion` | the expanded view |
| `to_text`, `TexText`, `Edit`, `corrected`, `rewrite`, `set_language` | `text`, `export`, `messages` | the typeset text; editing the source; the language of the messages |
| `logger` | `logger` | the `latexdetok` log |
| `COMPILED` | `compilation` | are the compiled modules running? |

`semantics`, `rendering`, `resolution`, `conditions`, `definitions`, `binding`
and `characters` are imported from their module (`from latexdetok.rendering
import outline`).

## `analyse`: `TexFile`

### `TexFile`

```python
TexFile(src, *, encoding=None, name=None, verbose=False)
```

A LaTeX source, from a path (`str` or `os.PathLike`) or from a list (or tuple)
of lines, line endings included. Any other type raises `TypeError`.

| Parameter | Role |
| --- | --- |
| `src` | the path of the file, or its lines |
| `encoding` | a forced encoding; by default UTF-8, then Latin-1 (see `read_lines`) |
| `name` | the name displayed (diagnostics, outline); by default the file name, or `<List of strings content>` |
| `verbose` | traces the analysis on the console (`set_verbose`) |
Attributs :
Attributes:
| Attribut | Type | Contenu |
| Attribute | Type | Contents |
| --- | --- | --- |
| `src_file` | `Path \| None` | the path of the source, `None` for lines |
| `encoding` | `str \| None` | the encoding that was used to read, and that writes the file back: `utf-8-sig` only if it starts with a byte order mark |
| `lines` | `list[str]` | the lines of the source as written, line endings included: `\r\n` stays `\r\n` |
| `name` | `str` | the name displayed |
| `container` | `TexGroup` | the root of the tree, the environment `latexfile`; empty before `analyse()` |
| `diagnostics` | `list[TexDiagnostic]` | what could not be matched, in the text as it is written (see `check` for a document) |
| `fragment` | `bool` | no `\documentclass`, no `document`, no `% !TEX root`: a file another one includes |
| `signatures` | `SignatureRegistry` | the kernel and the definitions learned during the analysis |
| `catcode_changes` | `list[CatcodeChange]` | the category changes that were read |
| `root` | `Path \| None` | the master document declared by `% !TEX root`, if the analysis followed it |
| `follow_inputs` | `bool` | the setting of the last analysis |
| `verbose` | `bool` | traces on the console |
| `catcode_regions` | `CatcodeRegions \| None` | tables forced here and there (expanded view) |
| `branch_regions` | `Sequence[BranchRegion]` | decided branches (expanded view) |

Methods:

| Method | Returns | Role |
| --- | --- | --- |
| `analyse(verbose=None, follow_inputs=False)` | `TexGroup` | analyses (or re-analyses) the lines; the root is also in `container` |
| `content()` | `str` | a rewriting of the source from the tree, multiple spaces collapsed, lines ending in `\n` whatever the file's; for the file as it is written, `rewrite(tex, [])` |
| `raw_text(container)` | `str` | the exact source between the positions of a node of this file, line endings as written; `ValueError` for a node of another file |
| `repr_hierarchy(expand=True)` | `str` | the indented tree, for debugging |
| `iter()` | an iterator | a flat walk of the root (see `TexContainer.iter`) |
| `set_verbose(verbose)` | `None` | traces the analysis or not |
| `self[i]` | `TexContainer` | `container[i]` |
| `get_commands_arguments(...)`, `get_commands_to_next(...)`, `get_envs(name)`, `get_preamble()`, `get_sections(starred=True)`, `get_graphics()`, `get_inputs(document=None)` | | the queries of the root (see `TexContainer` and `TexGroup`) |
| `get_lines_to_next(command, starred=False)` | `list[list[str]]` | the source lines of every occurrence of `\command` up to the next one; the last one runs to the line before `\end{document}`, or to the end of the file |

`analyse(follow_inputs=True)` looks for the files the document loads (`\input`,
`\include`, `\InputIfFileExists`, `\usepackage`, `\RequirePackage`,
`\documentclass`, `\LoadClass`) the way TeX does: the document's folder (or its
master's), then the texmf trees through `kpsewhich`. The files of the
distribution are found but not read; the user's are read, for their definitions.
A chapter that declares `% !TEX root = …` is read after the preamble of its
master, and its inclusions are looked for from the master's folder.

### `read_lines`

```python
read_lines(path: Path, encoding: str | None = None) -> tuple[str, list[str]]
```

`(encoding, lines)` of a file, line endings included and not translated: the
lines are cut at `\n`, `\r\n` or a lone `\r`, as TeX cuts them, and keep their
ending. With no encoding: `utf-8`, then `latin-1`, which reads any byte at all.
The encoding returned writes the lines back identically: a UTF-8 file, found or
forced, gives `utf-8-sig` if it starts with a byte order mark, which is then
not part of the first line, and `utf-8` otherwise. A forced encoding that does
not fit raises `UnicodeDecodeError`.

## `checks`: checking a document

### `check`

```python
check(source: TexFile | str | PathLike[str], *, follow_inputs: bool = True, expanded: bool = True) -> list[TexDiagnostic]
```

The diagnostics of a document, at source positions, in the order of the text:
structure (`diagnostics`) and meaning (`semantics`). A `TexFile` that is given
is re-analysed with these settings. `follow_inputs` reads the loaded files and
looks for the ones that are missing; `expanded` expands the user's macros and
reads the diagnostics in the view, then brings them back to the source. Without
expansion, a structure a macro closes (`\begin{equation}` … `\eeq`) passes for
unclosed.

A diagnostic whose start is written by a macro body is placed on the use, and
its message says so: “written by “\beq””, “written by “\x” inside “\y”” when the
use of `\x` itself comes from a body.

### `diagnose` and `in_source`

| Function | Returns |
| --- | --- |
| `diagnose(view)` | the structure and meaning diagnostics of an expanded view (`ExpandedFile`), at the positions of its source; the loaded files are looked for if the view followed them |
| `in_source(view, diagnostics)` | diagnostics of the view brought back to the source, with no duplicates (one body expanded several times at the same use) |

### `DocumentFiles`

```python
DocumentFiles(tex: TexFile)
```

The files of a document, looked for the way TeX does (`TexmfResolver.find`) from
the folder of the document or of its master. `exists(extension, name)` returns
`True`, `False`, or `None` when it cannot be known: with no file on disk, or
with no kpsewhich for a `.sty` or a `.cls`. Implements `semantics.FileFinder`.

## `diagnostics`: what is wrong

### `TexDiagnostic`

A frozen dataclass.

| Attribute | Type | Contents |
| --- | --- | --- |
| `code` | `str` | a stable code (see `CATALOGUE` and `SEMANTIC_CATALOGUE`) |
| `severity` | `Severity` | the severity |
| `message` | `str` | what is wrong, in one sentence |
| `start`, `end` | `Position` | the span at fault, end excluded |
| `related` | `tuple[Related, ...]` | the other useful places: the opening, what cut it short, the likely cause |
| `suggestion` | `str \| None` | the fix, when it is known |

`str()` returns `line:column: severity [code] message`, the column counted from 1.

```pycon
>>> from latexdetok import Related, Severity, TexDiagnostic
>>> diagnostic = TexDiagnostic("item-outside-list", Severity.ERROR, "“\\item” outside any list", (3, 0), (3, 5))
>>> str(diagnostic), diagnostic.severity.rank
('3:1: error [item-outside-list] “\\item” outside any list', 0)

```

### `Severity` and `Related`

- `Severity`: a `StrEnum`, `ERROR`, `WARNING`, `INFO`; `rank` is 0, 1, 2. The
  value is stable and read by machines; `text` gives the word in the language in
  force, and `counted(n)` puts it at the right number (see `messages`). An error
  is what TeX would refuse whatever the context; a warning is a likely mistake
  an invisible context may make valid; an info reports valid LaTeX.
- `Related`: a frozen dataclass, `start`, `end`, `message`.

### Renderings

| Function | Returns |
| --- | --- |
| `render_text(diagnostics, lines, name, color=False)` | compiler-style: `name:line:column: severity [code] message`, the underlined excerpt, every related place underlined with its message, then `= fix`; a long line is shown around the column; `color` adds the ANSI colours of the severity |
| `to_json(diagnostics, name)` | `list[dict]`: `file`, `line`, `column`, `end_line`, `end_column` (columns from 1), `severity`, `code`, `message`, `related` (the same position keys, and `message`), `suggestion` |
| `dumps(diagnostics, name)` | `to_json` as text |

### `StructureReport`

The structure events of one reading, which the tokeniser notes, and the
diagnostics drawn from them. `finish(document)` returns one diagnostic per
cause, in the order of the text; `document` is false for a fragment.

| Method | Event |
| --- | --- |
| `unclosed(group, cause, context, closer="", at=None, owner=None, known=True)` | an opening dissolved; `cause` (`Cause`) says what cut it short, `closer` and `at` the text and the span of what cut it, `owner` the command it is the argument of, `known` whether the environment is known (or the bracket expected by a signature) |
| `stray_end(name, start, end, context, known, target=None, braces=(), environment=None, environment_known=False)` | an `\end{name}` that closes nothing in its group; `target` and `braces`: the environment `name` opened beyond braces, and those braces; `environment`: otherwise, the nearest open environment |
| `extra_brace(position, previous, context)` | a `}` that closes nothing; `previous`, the last brace closed at the same level |
| `dollar_in_math(start, width, math, context)`, `math_in_math(start, end, control, math, context)`, `stray_math_close(start, end, control, context)` | misplaced math delimiters |
| `unclosed_verbatim(verbatim, context)`, `verb_line_end(verbatim, context)`, `verbatim_braces(verbatim, context)` | a verbatim never closed, a `\verb` stopped at the end of its line, the braces of a verbatim argument |

Groupings: an `\end` that arrives inside an unclosed brace becomes an error on
the brace, and the environment it was closing is no longer reported; an orphan
`\end{y}` and a `\begin{x}` never closed around it become a
`crossed-environment`; an environment cut short by the `\end` of its parent then
closed further on, `crossed-environments`.

| Name | Contents |
| --- | --- |
| `Cause` | `END_OF_FILE`, `BLANK_LINE`, `BRACE`, `END`, `MATH`, `BRANCH` (ignored) |
| `Context` | `NORMAL`; `DEFINITION` (the body of a definition, an argument that is not typeset: infos); `UNKNOWN_ARGUMENT` (the argument of an unknown command: an error becomes a warning) |
| `CATALOGUE` | code → description, for the structure diagnostics |
| `is_fragment(lines, document)` | a file with no `\documentclass`, no `document` (`document` false) and no `% !TEX root` |
| `similar(first, second, most=None)` | two names one typo apart (two beyond six letters; at most `most`), transposition included |

## `semantics`: meaning

```python
read_meaning(tex: TexFile, files: FileFinder | None = None) -> list[TexDiagnostic]
```

The meaning diagnostics of an analysed file (or of a view), in its own
positions; without `files`, the loaded files are not looked for. Every group is
read with what we know of the place — mode, list, alignment — and nothing is
reported where we do not know it: an unknown environment, the argument of an
unknown command or of one that does not typeset it, an unexpanded macro, the
body of a definition, a discarded branch, after a dissolved opening. A fragment
starts with no list and no alignment known.

| Name | Contents |
| --- | --- |
| `SEMANTIC_CATALOGUE` | code → description: `item-outside-list`, `ampersand-outside-alignment`, `script-outside-math`, `math-command-in-text`, `missing-argument`, `left-without-right`, `right-without-left`, `left-across-cells`, `line-break-without-line`, `file-not-found`, `unknown-command`, `unknown-environment` |
| `LIST_ENVIRONMENTS` | where `\item` is allowed: the kernel lists, and `center`, `quote`… built on `trivlist` |
| `ALIGNMENT_ENVIRONMENTS` | where `&` separates cells: kernel and amsmath |
| `NAME_ARGUMENTS` | commands whose arguments name something (`\label`, `\input`, `\setcounter`…): nothing is checked in them |
| `FileFinder` | a protocol: `exists(extension, name) -> bool \| None` |

## Command line

```bash
python3 -m latexdetok check CHEMIN… [--json] [--infos] [--no-inputs] [--no-expand] [--color auto|always|never]
```

A folder gives all its `.tex`. The rendering of `render_text`, infos hidden without `--infos`, then a summary
(`2 errors, 1 warning, 0 info`); with `--json`, one single array for every file.
Exit codes: 0 with no error, 1 with at least one error, 2 for an unreadable
file. `main(argv)` (in `latexdetok.__main__`) returns that code.

## `classes`: the tree

```text
TexContainer          a list of elements or a string; query results
├── TexContent        text, or a command (command=True); empty text = paragraph
│   └── TexCommand    a command read by the tokeniser, arguments bound
├── TexGroup          {…}, […], an environment, math, the root
│   └── TexBranch     a branch of a decided conditional (expanded view)
├── TexVerbatim       \verb|…|, \url{…}, a verbatim environment
└── TexComment        % a comment
```

Constants: `Position = tuple[int, int]`; `BRANCH_NAME = "branche"`, the name of
a `TexBranch`.

Nodes have `__slots__`: no attribute can be added to them.

### `TexContainer`

```python
TexContainer(content, position=None, end_position=None, rootfile=None)
```

`content` is a list of nodes, a string, or another `TexContainer` (whose
content, positions and file are taken over). A plain `TexContainer` (not a
subclass) gathers results: its positions are always `None`.

| Attribute | Type | Contents |
| --- | --- | --- |
| `content` | `list[TexContainer] \| str` | children, or text |
| `rootfile` | `TexFile \| None` | the file of the node, for `raw_text()` |
| `start_position`, `end_position` | `Position \| None` | the span |

The container protocol:

| Spelling | Effect |
| --- | --- |
| `len(x)` | the number of children; for a string, 1 if it is not empty, 0 otherwise |
| `x[i]`, `x[a:b]` | a child or a slice (a character for a string) |
| `for y in x` | the children (characters for a string) |
| `str(x)` | the LaTeX rewriting; the blanks between elements come from their positions, and a line ending is written `\n`, whatever the file's |
| `x + y` | a `TexContainer` of the elements of both (a string node counts for itself) |
| `x += y` | adds the elements of `y`; `TypeError` on a string node |
| `x.append(y)` | adds a node; `TypeError` on a string node or for a non-node |
| `x.list_container` | is the content a list? |

Walking and text:

| Method | Returns |
| --- | --- |
| `iter()` | a flat walk; a group adds its delimiters to it, as `TexContent` with no file |
| `raw_text()` | the exact source of the element, line endings as written; with no file and no position, `str()` |

Predicates, all without arguments unless stated:

| Predicate | True for |
| --- | --- |
| `is_command(name=None)` | a command (of that name, star included) |
| `is_command_in(names)` | a command whose name is in the list |
| `is_env(name=None)` | an environment (group or verbatim), of that name; `'$'` for math |
| `is_bracket_group()` | a group `{…}` |
| `is_option_group()` | an optional argument `[…]` |
| `is_math()` | math, whatever the delimiter |
| `is_verbatim()` | a `TexVerbatim` |
| `is_comment()` | a `TexComment` |
| `is_begin()`, `is_end()` | a `\begin` or `\end` command left alone (without its group) |
| `is_pure_text()` | a text |
| `is_par()` | a paragraph: an empty text, written by a blank line |

Order and equality:

- `==`: the same span in the same file; for a node with no position, identity.
  Nodes are not hashable. To compare contents, compare the `str()`.
- `<`, `<=`, `>`, `>=`: document order (the start, then the enclosing before the
  enclosed). `ValueError` between two files, `TypeError` with no position.
- `is_in(other)`: does the element lie inside the span of `other`?

Queries (on a list container, recursive, results in document order):

| Method | Returns |
| --- | --- |
| `get_commands_arguments(commands, nargs=None, nopt=None, starred=True)` | a `TexContainer` of groups: every command found, followed by its arguments |
| `get_commands_to_next(command, close_at_same_level=True, remove_comments=True, starred=True)` | a `TexGroup` of groups: every command and what follows it in its group |
| `get_envs(name)` | a `TexContainer` of the `name` environments, at any depth |
| `get_preamble()` | a `TexContainer` of what comes before `\begin{document}` in this container; empty with no document |

- `commands`: one name or a list of names. With `starred`, the starred variants
  are looked for too.
- Without `nargs` or `nopt`, a command of known signature comes with the
  arguments the tokeniser bound to it (the missing ones are left out).
  Otherwise, or for an unknown command: up to `nopt` arguments `[…]` if they are
  there (0 by default), then the next `nargs` elements (1 by default).
- `get_commands_to_next`: with `close_at_same_level`, every selection stops at
  the next occurrence; otherwise it goes to the end of the group, and the
  selections overlap. `remove_comments` takes the comments out.

Class methods for walking:

| Method | Role |
| --- | --- |
| `map_contents(container, func, found=None)` | calls `func(element, index=i, parent=group)` on every element that is not a group, recursively; gathers the non-empty results into `found` (a `TexGroup`) |
| `map_groups(container, func, found=None)` | calls `func(group)` on every group and verbatim, recursively; flattens the results into `found` |

### `TexContent`

```python
TexContent(content, position=None, end_position=None, rootfile=None, *, command=False)
```

Text, or a command with `command=True` (`content` without the backslash). A
command name written by hand is checked against the letters of the default table
(`ValueError` otherwise: `ab1`, `café`). An empty text is a paragraph.

| Member | Role |
| --- | --- |
| `command` | `bool`: a command or text |
| `name` | the content |
| `arg()` | the content |

### `TexCommand`

```python
TexCommand(content, position=None, end_position=None, rootfile=None, *, signature=None, macro=None)
```

A command read by the tokeniser. Its name is not checked: it was read under the
catcodes in force, which can make any character a letter.

| Attribute | Type | Contents |
| --- | --- | --- |
| `signature` | `CommandSignature \| None` | the signature known at the use; `None` for an unknown command, or for a text-mode one read in math |
| `arguments` | `list[TexContainer \| None] \| None` | the bound arguments, aligned with `signature.arguments`; `None` in the list for an argument that is absent (optional) or missing (mandatory); `None` outright if nothing is bound |
| `macro` | `Macro \| None` | the body of the user macro in force at the use |
| `role` | `Role \| None` | the role in a primitive conditional: `if`, `else`, `or`, `fi` |
| `value` | `bool \| None` | the value of the conditional an `\if…` opens, when the tokeniser could decide it |
| `star` | `bool` | the name ends with a star |
| `base_name` | `str` | the name without the star |

`missing_arguments()` returns the mandatory, bindable `ArgumentSpec` that were
not found.

The arguments stay siblings in the tree: `arguments` designates them without
moving them. `arguments` is `None` for an unknown command, and for a command
taken as a plain token (`\section` in `\let\titre\section`) or alone in its
braces with no argument (`\titleformat{\section}`).

### `TexGroup`

```python
TexGroup(name="{", content=None, env=False, displaymath=False, delimiter=None, *,
         position=None, end_position=None, rootfile=None)
```

| Kind | `name` | `env` | `delimiter` |
| --- | --- | --- | --- |
| braces | `{` | `False` | `None` |
| optional argument | `[` | `False` | `None` |
| environment | the name of the environment | `True` | `None` |
| math | `$` | `True` | `$`, `$$`, `\[` or `\(` |
| root | `latexfile` | `True` | `None` |

A group name other than `{` or `[` outside an environment, an empty environment
name or an unknown math delimiter raise `ValueError`.

| Attribute | Type | Contents |
| --- | --- | --- |
| `name`, `env`, `delimiter` | | see the table |
| `displaymath` | `bool` | display math (`$$`, `\[`) |
| `inner_start`, `inner_end` | `Position \| None` | the inside, between the delimiters |
| `signature` | `EnvironmentSignature \| None` | for a known environment |
| `arguments` | `list[TexContainer \| None]` | the bound arguments of a known environment: its first children |
| `macro` | `EnvironmentMacro \| None` | the begin and end code of a user environment |

| Property or method | Returns |
| --- | --- |
| `is_root` | the root of a file |
| `math`, `inlinemath` | math; inline math (`$`, `\(`) |
| `bracket`, `option` | a group `{…}`; an argument `[…]` |
| `first`, `top` | the first and the last child |
| `enclosures()` | `(opening, closing)`: `\begin{x}`/`\end{x}`, `$`/`$`, `{`/`}`…; nothing for the root |
| `closing` | the closing one |
| `arg()` | the rewriting of the inside, without the delimiters |
| `pop()` | takes off and returns the last child |
| `set_positions()` | the span of a selection: from the start of the first child to the end of the last |
| `repr_hierarchy(level=0, expand=True)` | the indented tree |
| `get_sections(starred=True)` | `\section` (and `\section*`) with their bound arguments: the short title `[…]` if there is one, the title last |
| `get_graphics()` | `\includegraphics`, `\includetoolargegraphics`, `\includepdf`, `\includetikz`, with one optional and one argument |
| `get_inputs(document=None)` | `\input` and its argument: inside the document if there is one, otherwise everywhere (`None`); only inside the document (`True`); everywhere (`False`) |

### `TexBranch`

```python
TexBranch(condition, taken, braced=False, *, position=None, end_position=None, rootfile=None)
```

A branch of a decided conditional, in an expanded view. Both branches stay in
the tree.

| Attribute | Contents |
| --- | --- |
| `condition` | the `Condition`, shared by both branches |
| `taken` | does TeX take this branch? |
| `braced` | a branch of a conditional with arguments, written between braces |

`name` is `BRANCH_NAME`, `env` is false. `enclosures()` returns `("{", "}")`
for a branch between braces, `("", "")` otherwise. A discarded branch is read
apart, with no effect on the rest; a taken branch whose structure spills out
melts into the stream (see `ExpandedFile.branches`).

### `TexVerbatim`

```python
TexVerbatim(name, env=False, car=None, content="", *, position=None, end_position=None, rootfile=None)
```

Content read without analysis. `content` is the source between the
delimiters, line endings included and written `\n`, like everything the tree
writes; `raw_text()` has them as the file does. An empty name, or a command
form with no delimiter, raise `ValueError`.

| Member | Contents |
| --- | --- |
| `name` | `verb`, `url`, `verbatim`, `minted`… |
| `env` | an environment (`\begin{verbatim}`) or a command (`\verb\|…\|`) |
| `car` | the opening delimiter of a command: `\|`, `{`… |
| `opening` | `\begin{name}` or `\name` followed by the delimiter |
| `verbatim_exit_phrase` | `\end{name}`, or the closing delimiter (`}` for `{`) |
| `closed` | false for a verbatim that nothing closed (end of file, end of the line of a `\verb`): `str()` adds no end to it |

### `TexComment`

A comment: `content` is the text after `%`, without the line ending; `str()`
rewrites it with its `%`.

## `parser`: the tokeniser

### `TexParser`

```python
TexParser(
    lines,
    rootfile=None,
    name="<source>",
    signatures=None,
    resolver=None,
    catcodes=None,
    preamble_only=False,
    catcode_regions=None,
    branch_regions=(),
)
```

Builds the tree of a sequence of lines; `TexFile.analyse` uses it.

| Parameter | Role |
| --- | --- |
| `lines` | the lines to read |
| `rootfile` | the file given to the nodes |
| `name` | the name in the log |
| `signatures` | a registry to enrich in place; without it, a copy of the kernel |
| `resolver` | the reader of the loaded files (`InputResolver`), for their definitions |
| `catcodes` | the starting table; `CatcodeTable.latex()` by default, `package()` for a `.sty` |
| `preamble_only` | stop at `\begin{document}` |
| `catcode_regions` | tables forced here and there (`CatcodeRegions`) |
| `branch_regions` | branches of decided conditionals (`BranchRegion`) |

`parse()` returns the root. Then: `diagnostics` (see `StructureReport`),
`fragment`, `signatures` (the enriched registry), `catcodes` (the table in force
at the end), `catcode_changes`.

### `unknown_command_before` and `UNTYPESET_ARGUMENTS`

`unknown_command_before(content)` returns the unknown command whose arguments
are the last elements of `content` (`\foo[a]{b}` before a `{`), the stream of a
`\write` skipped; `None` otherwise. `UNTYPESET_ARGUMENTS`: the commands whose
argument is not typeset (`\directlua`, `\write`, `\typeout`…), where whatever
does not match is only an info.

### `CatcodeRegions`

`Mapping[int, Sequence[tuple[int, int, CatcodeTable]]]`: per line, the start and
end (excluded) columns where to read under another table, in column order. That
is how the view reads a macro body again under the table of its definition.

### `BranchRegion`

A frozen dataclass: `start`, `inner_start`, `inner_end`, `end` (positions),
`condition`, `taken`, `braced`. A decided branch, in the text to read: from
`start` to `end`, braces included for a `braced` branch.

### `InputResolver`

The protocol of a reader of loaded files:

```python
def include(self, registry, name, extension, catcodes, preamble_only=False) -> CatcodeTable | None
```

Learns into `registry` the definitions of the file `name` (`extension`: `tex`,
`sty`, `cls`) read under `catcodes`, and returns the table in force at the end
of an `\input`, which TeX keeps; `None` otherwise. `TexmfResolver` implements it.

## `catcodes`: character categories

### `Category`

An `IntEnum` of TeX's sixteen categories: `ESCAPE` (0), `BEGIN_GROUP` (1),
`END_GROUP` (2), `MATH_SHIFT` (3), `ALIGNMENT` (4), `END_OF_LINE` (5),
`PARAMETER` (6), `SUPERSCRIPT` (7), `SUBSCRIPT` (8), `IGNORED` (9), `SPACE` (10),
`LETTER` (11), `OTHER` (12), `ACTIVE` (13), `COMMENT` (14), `INVALID` (15).

### `CatcodeTable`

```python
CatcodeTable(categories: Mapping[str, Category])
```

The category of every character; those it does not name are `OTHER`. Immutable
and hashable; two equal tables compare equal.

| Member | Returns |
| --- | --- |
| `CatcodeTable.latex()` | the table of a document: `@` ordinary |
| `CatcodeTable.package()` | the table of a `.sty` or a `.cls`: `@` a letter |
| `category(c)` | the `Category` of the character |
| `is_letter(c)` | is the character a letter? |
| `items()` | the `(character, category)` pairs that are named |
| `with_categories(changes)` | the changed table (a table equal to one already seen is given back as it stands) |
| `differences(other)` | `(character, here, there)` for every category that differs |

```pycon
>>> from latexdetok import CatcodeTable, Category
>>> table = CatcodeTable.latex().with_categories({"@": Category.LETTER})
>>> table == CatcodeTable.package(), table.category("@"), table.is_letter("@")
(True, <Category.LETTER: 11>, True)
>>> list(CatcodeTable.latex().differences(table))
[('@', <Category.OTHER: 12>, <Category.LETTER: 11>)]

```

A limit: a category can take its role away from `%`, `$`, `{`, `\` or make a
character a letter, a blank or an ignored character, but it cannot give a
special role to another character (`\catcode`\|=0`): those characters stay text.

### `CatcodeChange`

A frozen dataclass: `position`, `character`, `old`, `new` (`Category`),
`command` (`makeatletter`, `catcode`, `ExplSyntaxOn`…), `local` (false for
`\global\catcode`). `str()` returns a readable line.

### `interpret` et `CATCODE_COMMANDS`

```python
interpret(command: str, following: str) -> dict[str, Category] | None
```

The categories `\command` changes, followed by the rest of its line; `None` if
it changes none. Understands `\makeatletter`, `\makeatother`, `\ExplSyntaxOn`,
`\ExplSyntaxOff`, `\@makeother\X` and `\catcode` in its numeric forms
(`` `\@=11 ``, `64=11`, `"40`, `'100`, `` `\^^M ``, `\active`, `\@letter`,
`\@other`). `CATCODE_COMMANDS` is the set of those commands.

## `signatures`: signatures, bodies, booleans

### `Mode`

A `StrEnum`: `TEXT`, `MATH`, `ANY`. The mode where a command means something. A
text-mode command read in math passes for redefined: its signature is ignored. A
math-mode command read in text is a meaning diagnostic.

### `ArgumentSpec`

A frozen dataclass: `kind`, `long=False`, `default=None`, `delimiters=""`.

| Type | Signature | Bound in the tree |
| --- | --- | --- |
| `m` | mandatory | yes |
| `o`, `O{default}` | optional, in brackets | yes |
| `d<o><c>`, `D<o><c>{default}` | optional, delimited | yes |
| `r<o><c>`, `R<o><c>{default}` | mandatory, delimited | yes |
| `t<token>` | optional token (`t*`) | yes |
| `v` | verbatim | yes |
| `l` | everything up to the next brace (the parameter text of `\def`) | yes |
| `s` | a leading star: glued to the name, `starred` on the signature; elsewhere, a `t*` | |
| `b`, `g`, `G{default}`, `e{…}`, `E{…}{…}`, `u…` | recognised | no: they stop the binding |

Prefixes: `+` (a long argument, which may hold a blank line), `!`, `>{…}`,
`={…}` (no effect on the reading).

| Property | Returns |
| --- | --- |
| `mandatory` | `m`, `v`, `r`, `R` |
| `bound` | the type is bound in the tree |
| `opener`, `closer` | the delimiters: `[` and `]` for `o`/`O` |
| `str()` | the ltcmd spelling (`+m`, `O{1}`, `r()`) |

### `parse_spec`

```python
parse_spec(spec: str) -> tuple[bool, tuple[ArgumentSpec, ...]]
```

`(starred, arguments)` of an ltcmd signature; `ValueError` if it cannot be read.

```pycon
>>> from latexdetok.signatures import parse_spec
>>> starred, arguments = parse_spec("s o +m")
>>> starred, [str(argument) for argument in arguments]
(True, ['o', '+m'])

```

### `CommandSignature` and `EnvironmentSignature`

Frozen dataclasses.

- `CommandSignature(name, arguments=(), starred=False, mode=Mode.ANY)`: `name`
  without the backslash and without the star.
- `EnvironmentSignature(name, arguments=(), verbatim=False, mode=Mode.ANY)`: the
  arguments of an environment are its first children; with `verbatim`, its
  content is read without analysis.

| Member | Returns |
| --- | --- |
| `from_spec(name, spec, mode=Mode.ANY)` | the signature from its ltcmd spelling; for an environment, `verbatim` reads it without analysis |
| `spec` | the ltcmd spelling, star included (`s o m`); `verbatim` for an environment read without analysis |
| `CommandSignature.verbatim` | the first argument is a `v` |

### `Delegation`

A frozen dataclass: `name`, `target`, `arguments=()`, `starred=False`. The
signature of a macro that lets another one read its arguments:
`\newcommand{\x}{\@ifstar{A}{\y}}` reads the star, then whatever `\y` reads. The
signature of the target is looked up at the use.

### `Macro` and `EnvironmentMacro`

Frozen dataclasses, what `expansion` expands.

- `Macro(name, parameters, body, catcodes, otherwise=None, starred=False)`:
  `parameters` in `m`, `o`, `O{…}`, `t`; `body`, the text of the body;
  `catcodes`, the table it was read under. For a macro that tests what follows
  (`\@ifstar{A}{B}`), `body` is the branch taken with the star or the bracket,
  `otherwise` the other one.
- `EnvironmentMacro(name, parameters, begin, end, catcodes, end_arguments=False)`:
  the begin and end code; `end_arguments` when the end code receives the
  arguments too (`\NewDocumentEnvironment`).

### `Boolean`

A frozen dataclass: `name` (`prof` for `\ifprof`), `value: bool | None`, `None`
when the value cannot be known.

### `SignatureRegistry`

The signatures, bodies and booleans known by name. Every analysis works on its
own copy of the kernel.

| Method | Returns | Role |
| --- | --- | --- |
| `SignatureRegistry.kernel()` | a registry | a copy of the kernel: the tables `data/kernel-harvested.txt`, `data/kernel.txt`, `data/packages.txt`, `data/verbatim.txt` |
| `copy()` | a registry | an independent copy |
| `command(name)` | `CommandSignature \| None` | the signature of a command, delegations resolved |
| `environment(name)` | `EnvironmentSignature \| None` | the signature of an environment |
| `macro(name)` | `Macro \| None` | the body of a user macro |
| `command_names()`, `environment_names()` | `frozenset[str]` | the names known: signature, delegation or body |
| `environment_macro(name)` | `EnvironmentMacro \| None` | the code of a user environment |
| `is_boolean(name)` | `bool` | is there a `\newif` boolean of that name? |
| `boolean(name)` | `bool \| None` | its value; `None` if unknown or non-existent |
| `booleans()` | `frozenset` | the `(name, value)` pairs |
| `define(definition)` | `None` | records a signature, a delegation, a body or a boolean; the last definition of a name wins, and a new signature forgets its body |
| `forget_command(name)` | `None` | forgets signature, delegation and body |
| `copy_command(target, source)` | `None` | `\let\target\source` |
| `record()` | a context manager | returns the list where the `("define", definition)` and `("forget", name)` of the block are noted |
| `replay(journal)` | `None` | replays a journal |
| `load(lines, origin="<table>")` | `None` | loads a table; `ValueError` with the offending line |
| `len(registry)` | `int` | the number of command and environment signatures |

The format of a table: one line per name, `\name mode signature` or
`{environment} mode signature`, `-` for an empty signature, `verbatim` for an
environment read without analysis; `#` starts a comment.

```pycon
>>> from latexdetok import SignatureRegistry
>>> registry = SignatureRegistry.kernel()
>>> registry.command("section").spec, registry.environment("verbatim").verbatim
('s o m', True)
>>> registry.load(["\\vect math m\n"])
>>> registry.command("vect")
CommandSignature(name='vect', arguments=(ArgumentSpec(kind='m', long=False, default=None, delimiters=''),), starred=False, mode=<Mode.MATH: 'math'>)

```

## `definitions`: what a command defines

```python
learn(command: TexCommand, registry: SignatureRegistry, catcodes: CatcodeTable) -> list[Inclusion]
```

Records in `registry` what a command whose arguments are bound defines, read
under `catcodes`, and returns the files it makes TeX read, `Inclusion =
tuple[str, str]`: `(name, extension)`.

| Definition | What is learned |
| --- | --- |
| `\newcommand{\x}[2][d]{…}`, `\renewcommand`, `\providecommand`, `\DeclareRobustCommand` | `+o +m`, or `+m +m` with no default (short arguments with the star); the body |
| `\newenvironment{x}[1]{…}{…}`, `\renewenvironment` | the signature; the begin and end code |
| `\NewDocumentCommand{\x}{s o m}{…}` and its relatives, `\NewDocumentEnvironment` | the signature as it stands; the body if its parameters are `m`, `o`, `O{…}`, `t` or the leading star |
| `\def\x#1#2{…}`, `\gdef` | `m m` and the body, if the parameters are not delimited; otherwise `\x` is forgotten |
| `\edef`, `\xdef` | the signature, without the body (expanded at definition time) |
| `\let\x\y`, `\NewCommandCopy\x\y` and their relatives | `\x` takes the signature and the body of `\y` |
| `\newif\ifprof` | the boolean `prof`, false |
| `\input`, `\include`, `\usepackage`, `\RequirePackage`, `\documentclass`, `\LoadClass`, `\InputIfFileExists` | nothing: the file is returned, with its extension |

| Constant | Contents |
| --- | --- |
| `DEFINING_COMMANDS` | the commands whose arguments are definitions: nothing in them is matched or expanded |
| `INCLUDING_COMMANDS` | command → `(extension, index of the name argument)`: `input`, `include`, `InputIfFileExists`, `usepackage`, `RequirePackage`, `documentclass`, `LoadClass` |

## `binding`: binding the arguments

```python
ArgumentBinding(target: TexCommand | TexGroup, specs: tuple[ArgumentSpec, ...])
```

The arguments a command or an environment expects, filled in as reading goes on.
The tokeniser offers it every following element of the same group.

| Member | Role |
| --- | --- |
| `target` | the command or the environment; its `arguments` list fills up |
| `done` | every argument has been found |
| `accepts_opener(char)` | does this character open an expected optional argument in brackets? |
| `wants_character(char)` | what a text character would do: `"token"` (a one-character argument), `"delimited"`, or `None` |
| `wants_command()` | would a command read now be a plain token of the binding? |
| `consume(node)` | keeps the node if it belongs to the binding; `False` when it is over |
| `close()` | whatever is missing becomes `None` |

The rules: blanks, comments and branches without braces do not count; a missing
optional lets the element through; an `m` takes the element whatever it is, one
single character for text; a blank line leaves a non-long `m` missing; an
unbound type stops the binding.

## `conditions`: TeX's conditionals

| Name | Contents |
| --- | --- |
| `Role` | a `StrEnum`: `IF`, `ELSE`, `OR`, `FI` |
| `PRIMITIVE_CONDITIONALS` | the `\if…` of TeX, e-TeX and pdfTeX |
| `ROLE_NAMES` | `else`, `or`, `fi` |
| `ARGUMENT_TESTS` | conditional with arguments → indices of the true and false branches (`None` if absent): `IfBooleanTF`, `IfValueTF`, `IfNoValueTF` and their `T`/`F` variants, `ifstrempty`, `ifblank` |
| `BOOLEAN_TRUE`, `BOOLEAN_FALSE`, `NO_VALUE` | `\BooleanTrue`, `\BooleanFalse`, `-NoValue-`: what the expansion substitutes for a star or a missing optional |
| `MATH_ENVIRONMENTS` | environments whose inside is math (kernel, amsmath) |
| `MATH_ARGUMENT_COMMANDS`, `TEXT_ARGUMENT_COMMANDS` | commands whose argument is math (`\ensuremath`), or text (`\mbox`, `\text`…) |

| Function | Returns |
| --- | --- |
| `conditional_role(name, following, is_boolean, known)` | the role of `\name` in a primitive conditional, `None` otherwise; `following` is the rest of the line, `known` says whether the registry knows the command |
| `ifnum_test(following)` | `(value, length of the test)` for `\ifnum` followed by two written numbers; `None` otherwise |
| `setter(name, is_boolean)` | `("prof", True)` for the `\proftrue` of a known boolean; `None` otherwise |
| `argument_test_value(name, test)` | the value of a conditional with arguments from the text of its first argument; `None` if undecidable |

## `resolution`: loaded files

### `TexmfResolver`

```python
TexmfResolver(folder: Path, main: Path | None = None)
```

Reads, for one analysis, the definitions of the loaded files. `folder` is the
build folder; `main`, the main document (never re-read if it includes itself).
The files of the distribution are found but never read: their signatures are
written by hand in `data/packages.txt` (see the header of `resolution` for the
measurement that settled it).

| Method | Returns |
| --- | --- |
| `find(name, extension)` | `Path \| None`: `name.extension` in the folder, then the name as it stands for a `.tex`, then `kpsewhich` (not for an explicit path `./…`, `../…` or absolute) |
| `include(registry, name, extension, catcodes, preamble_only=False)` | learns the definitions of the file and returns the table it leaves if it is a `.tex` (see `InputResolver`) |

A `.sty` or a `.cls` is read with `@` as a letter, and its catcode changes stay
inside its own reading; an included `.tex` keeps those of the document and
leaves it its own.

### Functions

| Function | Returns |
| --- | --- |
| `read_lines(path, encoding=None)` | see [`analyse`](#read_lines) |
| `root_of(path, lines)` | the master document declared by `% !TEX root = …` in the first 20 lines, if it exists and is not the file itself |
| `clear_caches()` | forgets the cached searches and definitions, and closes the interactive `kpsewhich` |

`FALLBACK_ENCODINGS = ("utf-8", "latin-1")`.

Cached, for the process: every search once per kpathsea setting (`TEXMFHOME`,
`TEXMFLOCAL`, `TEXINPUTS`, `TEXMFCNF`); for every file read, its definitions,
replayed as long as the file has not changed (date and size) and the starting
table and the booleans are the same. Searches go through one single
`kpsewhich -interactive` kept open, or through one call each without a
pseudo-terminal.

## `expansion`: the expanded view

### `expand`

```python
expand(tex: TexFile, max_depth: int = MAX_DEPTH) -> ExpandedFile
```

Expands the user's macros of an analysed file and decides its conditionals. The
view is analysed like the source (`follow_inputs` included). Every pass expands
the uses and decides the conditionals of the previous one, as long as any are
left, `MAX_PASSES` at most; an expansion deeper than `max_depth` does not
happen. A text that goes beyond sixteen times the source (plus a margin of
100,000 characters) stops the expansion, with a message in the log: the view
keeps the previous pass. With nothing to expand, the view has the text of the
source; otherwise its lines end in `\n`, whatever the source's, and a `\r\n`
source gives the view of its `\n` copy.

What expands: the `Macro` and `EnvironmentMacro` of the registry, at uses whose
mandatory arguments are there. Nothing inside the arguments of a definition, nor
after `\noexpand`, `\string`, `\ifx`, `\expandafter`.

What is decided: `\iftrue`, `\iffalse`, the booleans of `\newif`, `\ifnum` on
written numbers, `\ifmmode`, `\IfBooleanTF`, `\IfValueTF`, `\IfNoValueTF`,
`\ifstrempty`, `\ifblank`, and the branches of `\@ifstar`/`\@ifnextchar`.

`MAX_DEPTH = 8`, `MAX_PASSES = 16`.

### `ExpandedFile`

A `TexFile` on the text produced: `lines`, `container`, `diagnostics` and the
queries are the view's.

| Attribute | Contents |
| --- | --- |
| `source` | the `TexFile` that was expanded |
| `source_map` | the `SourceMap` of the text produced |
| `expansions` | `list[Expansion]`, in the order they were made |
| `conditions` | the `list[Condition]` that were decided |

| Method | Returns |
| --- | --- |
| `source_position(position)` | the position in the source; for a text written by a body, the start of the use |
| `source_span(node)` | `(start, end)` in the source of what produced the node: its text if it is copied, the use otherwise |
| `origin(node)` | the `Expansion` whose body wrote the start of the node; `None` if it is copied from the source |
| `branches(node)` | `((Condition, taken?), …)` of the decided branches where the node starts, from the outer to the inner, even when melted into the stream |

### `Expansion`

A frozen dataclass: `name`, `start`, `end`, `parent=None`, `environment=False`.

- `start`, `end`: the span in the source of the use as written; for a use
  produced by a body, that of the use that produced it.
- `parent`: the expansion whose body wrote this use, or which substituted the
  argument it was written in.
- `environment`: an environment has two expansions, at the `\begin` and at the
  `\end`.
- `depth`: 1 with no parent, otherwise the parent's depth plus one.

### `Condition`

A frozen dataclass, compared by identity: `test` (`ifprof`, `ifnum`,
`IfBooleanTF`, `@ifstar`…), `value` (is the true branch taken?), `start`, `end`
(the span of the test in the source), `parent` (the expansion that wrote it).

### `SourceMap`

Where every piece of the text produced comes from. Built by `expand`.

| Member | Returns |
| --- | --- |
| `SourceMap.identity(lines)` | the map of a text copied as it stands |
| `pieces`, `source`, `target` | pieces, offsets of the source and of the text produced (internal types) |
| `index(offset)`, `piece(offset)` | the index and the piece of an offset in the text produced |
| `expansion_at(offset)` | the expansion that wrote the character; `None` if it is copied |
| `producer(offset)` | the expansion that produced it: written by it, or an argument it substituted |
| `source_start(offset)`, `source_end(offset)` | the start and end (excluded) positions in the source |
| `catcode_regions()` | tables to force in order to read the text produced again (`CatcodeRegions`) |
| `branch_regions()` | the branches of the text produced (`BranchRegion`) |

## `rendering`: the frame of a document

| Function | Returns |
| --- | --- |
| `blocks(tex)` | the root `Block` of the frame of an analysed file or of a view |
| `outline(tex, depth=None, min_lines=3, width=110, color=False)` | the outline in text: one block per line, with its lines and what it holds; `depth` limits the depth, neighbours of fewer than `min_lines` lines are grouped, `width` bounds the width, `color` adds the ANSI colours |
| `html_page(tex, view=None)` | a standalone HTML page on the compact view (the source) and the expanded one (the expanded view, computed if `view` is not given): outline and text tied together, uses expanded or folded on a click |

The outline and the page count and mark the diagnostics of the file
(`TexFile.diagnostics`), without the infos.

### `Block`

A dataclass: `kind`, `label`, `start`, `end` (lines), `children`, `stats`
(a `Counter`: words, formulas, commands by name), `level`, `side`.

- `kind`: `root`, `section`, `environment`, `math`, `command` (a box or a
  definition), `branch`, `verbatim`.
- `side`: inside a decided branch, `True` if it is taken, `False` otherwise.
- `lines`: the number of lines; `walk(depth=0)`: `(block, depth)`, depth first.

## `text`: the typeset text of a document

| Function | Returns |
| --- | --- |
| `to_text(source, math="source")` | the `TexText` of an analysed file, of an expanded view or of a node; `math` is `"source"` (the formulas as written, without delimiters) or `"skip"` |

Typeset: the text, the mandatory arguments of the commands that typeset, the
body of the environments, the verbatim, the substitutions (`\og`, `\LaTeX`,
`\ldots`) and the accents (`\'e` becomes “é”). Not typeset: comments, arguments
that name something (`NAME_ARGUMENTS`), the bodies of definitions
(`DEFINING_COMMANDS`), settings (`SETTINGS`), drawings (`SILENT_ENVIRONMENTS`)
and optional arguments. The blanks of the source are reduced to one space; the
breaks stay (blank line, `\\`, `\item`, titles, environments).

### `TexText`

A dataclass: `text` (the typeset string), `marks` (the `Mark`, in order).

| Member | Returns |
| --- | --- |
| `str(t)`, `len(t)`, `sub in t` | the string, its length, membership |
| `t.find(sub)` | the `Found` of a text taken as it stands |
| `t.search(pattern, flags=0)` | the `Found` of a regular expression |
| `t.position(offset)` | the `(line, column)` of the character in the source |
| `t.node(offset)` | the node of the tree that wrote that character |
| `t.at(offset)` | the `Found` of one single character |

### `Found` and `Mark`

`Found`: `start`, `end` (in the text), `text`, `node`, `position`.

`Mark`: `offset`, `length` (the piece in the text), `node`, `faithful` — true
when the piece is the text of the node character for character, save the blanks,
and the position of each one can be computed exactly; false for a substitution,
verbatim or math, where the position of the node is given.

## `export`: editing a source

| Function | Returns |
| --- | --- |
| `corrected(nodes, fix)` | the `Edit` a function asks for: `fix(node)` returns the LaTeX that replaces the node, or `None` to leave it |
| `rewrite(tex, edits)` | the edited source; whatever no edit touches comes out of it byte for byte, line endings included |

`rewrite` applies the edits in the order of the document, whatever order they
arrive in; several insertions at the same place keep the order given. Raise: two
edits that overlap, a position outside the file, a node with no position, a node
that comes from another file — an expanded view (`expand`) has its own
positions, to be brought back to the source by `checks.in_source`.

The result is the source as the file writes it: to put it on disk, write it
with `encoding=tex.encoding` and `newline=""`, without which Python would turn
every `\n` into `\r\n` on Windows. The text of an edit is written as it is
given, its line endings included; a column that goes beyond the text of its
line designates the end of that line, line ending included.

### `Edit`

A dataclass: `start`, `end` (positions, end excluded), `text`, `node` (the node
the edit is drawn from, outside comparison). `start == end` inserts, an empty
`text` deletes; `insertion` says so.

| Constructor | Edits |
| --- | --- |
| `Edit.of(node, text)` | the whole node, delimiters included |
| `Edit.inside(group, text)` | the inside of a group, of an argument, of an environment |
| `Edit.before(node, text)` | an insertion right before |
| `Edit.after(node, text)` | an insertion right after — an optional argument after its command |
| `Edit.opening(group, text)` | the opening delimiter: `\begin{itemize}`, `{`, `$` |
| `Edit.closing(group, text)` | the closing delimiter: `\end{itemize}`, `}`, `$` |

Since the arguments are siblings and not children (`TexCommand.arguments`
designates them), renaming a command does not touch its arguments, and changing
an argument does not touch the command.

## `messages`: what is written for a person

The messages are not in the code: they live in `data/messages-<language>.txt`,
one line per message, `key  template`. The code only knows the key. What a
machine reads — diagnostic codes, values of `Severity`, JSON keys — stays in
English, whatever happens.

| Function | Returns |
| --- | --- |
| `say(key, **fields)` | the message of a key, its `{fields}` filled in; a key missing from the language falls back to English |
| `language()` | the language in force |
| `set_language(name)` | changes it; `""` goes back to the one in `LATEXDETOK_LANG`, else English |
| `languages()` | the languages shipped |
| `catalogue(name=None)` | every template of a language |

`set_language` is also exported by the package. Adding a language means adding
`data/messages-xx.txt`: nothing to rebuild, and the keys that are missing fall
back to English.

## `characters`: characters and names

| Name | Contents |
| --- | --- |
| `ROOT_NAME` | `latexfile`, the name of the root |
| `LETTERS` | ASCII letters |
| `SPACE_CARS` | blanks: space, tab, line endings, form feed, vertical tab |
| `GROUP_CARS` | `{` and `[` |
| `CLOSING_COUPLE` | opening → closing: `{}`, `[]`, `()` |
| `MATHS_MODE_CAR` | `$` |
| `MATH_DELIMITERS` | opening → closing of math: `$`, `$$`, `\[`, `\(` |
| `NOT_VERBATIM_DELIMITERS` | after a verbatim name, these characters make it a name being quoted, not a use |
| `TEX_ROOT`, `TEX_ROOT_LINES` | the `% !TEX root = …` declaration, looked for in the first 20 lines |
| `collapse_spaces(text)` | blanks collapsed the way TeX does, the non-breaking space kept |
| `index_in_line(line, column)` | the index of a column in a line; beyond the text of the line, the index after its line ending, whatever its width |
| `valid_command_name(name)` | letters (ASCII, `@`, `_`, `:`) or a single character, a trailing star allowed |
| `valid_group_start(c)` | does `c` open a group? |

## `logger`: the log

| Name | Role |
| --- | --- |
| `logger` | the `latexdetok` log (`LOGGER_NAME`); analysis diagnostics at `WARNING` level (infos at `DEBUG`), the trace at `DEBUG` |
| `change_logging_level(verbose)` | shows the trace on the console for good, or takes it away |
| `verbose_logging(enabled)` | a context manager: the trace for the length of the block |

## `compilation`: compiled modules

Imported first by the package, `compilation` puts the extensions of `_mypyc/` at
the front of the package path if they match the sources.

| Name | Contents |
| --- | --- |
| `COMPILED` | are the compiled modules running? |
| `ENVIRONMENT` | `LATEXDETOK_PURE`: a non-empty value forces the sources |
| `COMPILED_MODULES` | `analyse`, `binding`, `catcodes`, `characters`, `checks`, `classes`, `conditions`, `definitions`, `diagnostics`, `expansion`, `parser`, `semantics`, `signatures` |
| `PACKAGE`, `BUILD` | the package folder, the extensions folder |
| `GROUP` | `latexdetok.compiled`: the name of the shared library |
| `sources_digest(package=PACKAGE)` | the SHA-256 digest of the `*.py` sources of the package |
| `manifest(package=PACKAGE)` | what `scripts/compile.py` writes: extension suffix, digest, modules, Python version |
| `use_compiled(package_path, package=PACKAGE, build=BUILD)` | puts `build` at the front of `package_path` if the manifest matches (suffix, modules, extensions present, digest); returns `True` if it did |

In compiled mode, a method of a compiled class cannot be replaced at run time,
a compiled class cannot be subclassed outside the package, and an attribute
refuses a value of a type other than the annotated one (`TypeError`).

## Scripts

In `scripts/`, to be run from the root of the repository.

| Script | Role | Options |
| --- | --- | --- |
| `render.py SOURCE` | outline in text or HTML page | `--format outline\|html`, `-o FILE`, `--inputs`, `--expand` (outline of the view), `--depth N` |
| `corpus.py FOLDER` | non-regression: analysis, rewriting, re-reading of the source, diagnostics by severity and by code; with `--expand`, the view and its correspondence, the diagnostics of `check`, and the errors found in the files that compile (a recent log with no error) | `--inputs`, `--expand` |
| `fingerprints.py FOLDER` | a fingerprint of everything the analysis and the view produce, file by file | `--inputs`, `--expand`, `-o JSON`, `--compare JSON` |
| `bench.py TARGET` | the speed of a file (median) or of a folder | `--inputs`, `--expand`, `--repeat N`, `--profile [N]`, `--memory` |
| `compile.py` | compiles the core with mypyc, for the interpreter that runs it | `--clean` |
| `harvest.py` | harvests the kernel signatures from TeX Live (`data/kernel-harvested.txt`) | |
