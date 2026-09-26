# Using latexdetok: examples

Recipes, from the simplest to the fullest. Every example is a Python session
that runs as it stands: the whole file is checked as a doctest, from the parent
folder of `latexdetok`, and `tests/test_docs.py` does it on every test run.

```bash
python3 -m doctest -o ELLIPSIS -o NORMALIZE_WHITESPACE latexdetok/EXAMPLES.md
```

The details of every class and every function are in [API.md](API.md).

## Analysing a source

A `TexFile` is built from a path or from a list of lines, line endings included.
`analyse()` builds the tree and returns it; it never raises on LaTeX, however
wrong.

```pycon
>>> from latexdetok import TexFile
>>> source = r"""\documentclass{article}
... \newcommand{\vect}[1]{\overrightarrow{#1}}
... \begin{document}
... \section[Court]{Le titre long}
... Soit $\vect{u}$ un vecteur, et $\frac{1}{2}$.
... \begin{itemize}
... \item premier % un commentaire
... \item second
... \end{itemize}
... \end{document}
... """
>>> tex = TexFile(source.splitlines(keepends=True), name="cours.tex")
>>> root = tex.analyse()
>>> root is tex.container
True
>>> tex.diagnostics
[]

```

`content()` rewrites the source from the tree. Blanks are not nodes: a run of
spaces rewrites as one, the rest is identical.

```pycon
>>> print(tex.content())
\documentclass{article}
\newcommand{\vect}[1]{\overrightarrow{#1}}
\begin{document}
\section[Court]{Le titre long}
Soit $\vect{u}$ un vecteur, et $\frac{1}{2}$.
\begin{itemize}
\item premier % un commentaire
\item second
\end{itemize}
\end{document}

```

## Walking the tree

The root is the environment `latexfile`. A group (`TexGroup`) is a brace, an
optional argument, an environment or math; a leaf is text or a command
(`TexContent`, `TexCommand`), a comment (`TexComment`) or a verbatim
(`TexVerbatim`). The arguments of a command stay its siblings in the tree.

```pycon
>>> document = tex.get_envs("document")[0]
>>> for node in document.content:
...     print(f"{type(node).__name__:12} {str(node)[:30]!r}")
TexCommand   '\\section'
TexGroup     '[Court]'
TexGroup     '{Le titre long}'
TexContent   'Soit'
TexGroup     '$\\vect{u}$'
TexContent   'un vecteur, et'
TexGroup     '$\\frac{1}{2}$'
TexContent   '.'
TexGroup     '\\begin{itemize}\n\\item premier '

```

The predicates spare the `isinstance`; `name`, `env`, `math` and `delimiter`
describe a group.

```pycon
>>> section, option, title = document.content[:3]
>>> section.is_command("section"), option.is_option_group(), title.is_bracket_group()
(True, True, True)
>>> formula = document.content[4]
>>> formula.is_math(), formula.name, formula.delimiter, formula.arg()
(True, '$', '$', '\\vect{u}')
>>> itemize = document.content[-1]
>>> itemize.is_env("itemize"), [str(node) for node in itemize.content]
(True, ['\\item', 'premier', '% un commentaire', '\\item', 'second'])

```

To walk everything, a recursion over `content` is enough. `\begin` and `\end`
are not there: they are part of the group of the environment.

```pycon
>>> from latexdetok import TexGroup
>>> def walk(node):
...     yield node
...     if isinstance(node, TexGroup):
...         for child in node.content:
...             yield from walk(child)
>>> sorted({node.content for node in walk(tex.container) if node.is_command()})
['documentclass', 'frac', 'item', 'newcommand', 'overrightarrow', 'section', 'vect']

```

## Positions and exact text

Positions are `(line, column)`: the line from 1, the column from 0, end
excluded. They cover the whole element, delimiters included; the inside of a
group lies between `inner_start` and `inner_end`. `raw_text()` gives the exact
source, line endings as the file writes them; `str()` an equivalent LaTeX, whose
lines end in `\n`.

```pycon
>>> title.start_position, title.end_position, title.inner_start, title.inner_end
((4, 15), (4, 30), (4, 16), (4, 29))
>>> title.raw_text()
'{Le titre long}'
>>> itemize.start_position, itemize.end_position
((6, 0), (9, 13))

```

Document order compares with `<`, and `is_in` tests containment.

```pycon
>>> title < itemize, itemize.content[0].is_in(itemize)
(True, True)

```

## Diagnostics rather than exceptions

Whatever does not match is read flat and reported in `diagnostics`. Nothing
raises: the body of a macro that opens an environment without closing it is
valid LaTeX, reported as a plain info. These are the diagnostics of the text as
it is written; for those of a document, macros expanded and meaning included,
see [Checking a document](#checking-a-document).

```pycon
>>> faulty = TexFile(
...     [
...         "Le cas $x\n",
...         "\n",
...         "\\newenvironment{sol}{\\begin{proof}}{\\end{proof}}\n",
...         "\\end{itemize}\n",
...     ],
...     name="brouillon.tex",
... )
>>> _ = faulty.analyse()
>>> for diagnostic in faulty.diagnostics:
...     print(diagnostic)
1:8: error [unclosed-math] “$” never closed
3:22: info [unclosed-environment] “\begin{proof}” never closed
3:37: info [end-without-begin] “\end{proof}” without “\begin{proof}”
4:1: warning [end-without-begin] “\end{itemize}” without “\begin{itemize}”
>>> diagnostic.code, diagnostic.severity, diagnostic.start, diagnostic.end
('end-without-begin', <Severity.WARNING: 'warning'>, (4, 0), (4, 13))

```

## Sections, environments, commands

The queries return a `TexContainer` of results, in document order.

```pycon
>>> sections = tex.get_sections()
>>> [str(node) for node in sections[0]]
['\\section', '[Court]', '{Le titre long}']
>>> sections[0][-1].arg()
'Le titre long'
>>> [str(node) for node in tex.get_envs("$")]
['$\\vect{u}$', '$\\frac{1}{2}$']
>>> [str(selection) for selection in tex.get_commands_arguments("frac")]
['{\\frac{1}{2}}']

```

For a command whose signature is not known, `nargs` and `nopt` say how many
arguments to take:

```pycon
>>> unknown = TexFile(["\\macommande[opt]{a}{b}{c}\n"])
>>> _ = unknown.analyse()
>>> [str(node) for node in unknown.get_commands_arguments("macommande", nopt=1, nargs=2)[0]]
['\\macommande', '[opt]', '{a}', '{b}']

```

A command that is only the argument of another one — `\section` in
`\let\titre\section` — reads none of its own (`TexCommand.bare`): it comes
alone, whatever follows it, and `get_commands_to_next` and `get_lines_to_next`
do not take it for a call.

```pycon
>>> let = TexFile(["\\let\\titre\\section\n", "\\newcommand{\\R}{x}\n"])
>>> _ = let.analyse()
>>> [[str(node) for node in found] for found in let.get_commands_arguments("section")]
[['\\section']]

```

`get_commands_to_next` cuts every command up to the next one of the same group;
`get_lines_to_next` returns the matching source lines.

```pycon
>>> [str(selection) for selection in itemize.get_commands_to_next("item")]
['{\\item premier}', '{\\item second}']
>>> tex.get_lines_to_next("section")
[['\\section[Court]{Le titre long}\n', 'Soit $\\vect{u}$ un vecteur, et $\\frac{1}{2}$.\n', '\\begin{itemize}\n', '\\item premier % un commentaire\n', '\\item second\n', '\\end{itemize}\n']]
>>> [str(node) for node in tex.get_preamble()]
['\\documentclass', '{article}', '\\newcommand', '{\\vect}', '[1]', '{\\overrightarrow{#1}}']

```

## Arguments bound by the signatures

The commands of the LaTeX2e kernel have their signature in the ltcmd format
(`\section`: `s o m`), and `data/packages.txt` adds those of the common packages
(`\includegraphics`: `s o o m`). A known command designates its arguments;
`None` marks there an argument that is absent or missing.

```pycon
>>> section.signature.spec
's o m'
>>> [None if argument is None else str(argument) for argument in section.arguments]
['[Court]', '{Le titre long}']
>>> broken = TexFile(["$\\frac{1}$\n"])
>>> _ = broken.analyse()
>>> frac = broken.get_envs("$")[0].content[0]
>>> [str(spec) for spec in frac.missing_arguments()]
['m']

```

The definitions of the file enter the registry (`signatures`) as soon as they
are read: the signature, and the body so that they can be expanded.

```pycon
>>> tex.signatures.command("vect").spec
'+m'
>>> tex.signatures.macro("vect").body
'\\overrightarrow{#1}'
>>> vect = formula.content[0]
>>> vect.signature.spec, str(vect.arguments[0])
('+m', '{u}')

```

## Catcodes

`\makeatletter`, `\catcode`, `\ExplSyntaxOn` change what a letter is, with the
scope of groups. The changes are listed in `catcode_changes`.

```pycon
>>> package = TexFile(["\\makeatletter\n", "\\def\\a@b{x}\n", "\\makeatother\n", "\\a@b\n"])
>>> _ = package.analyse()
>>> [str(node) for node in package.container.content]
['\\makeatletter', '\\def', '\\a@b', '{x}', '\\makeatother', '\\a', '@b']
>>> for change in package.catcode_changes:
...     print(change)
line 1, column 0: \makeatletter: '@' other (12) → letter (11)
line 3, column 0: \makeatother: '@' letter (11) → other (12)

```

## A file on disk, and inclusions

From a path, the encoding is found on its own (UTF-8, otherwise Latin-1), and
`encoding` is the one that writes the file back: `utf-8-sig` only if it starts
with a byte order mark. The lines keep the file's line endings, `\r\n` included;
the tree, its positions and its diagnostics are those of the same file in `\n`.
With `follow_inputs=True`, the loaded files (`\input`, `\usepackage`,
`\documentclass`…) are looked for the way TeX does: the document's folder, then
the texmf trees through `kpsewhich`. Their definitions are learned; their
content does not enter the tree.

```pycon
>>> import tempfile
>>> from pathlib import Path
>>> folder = Path(tempfile.mkdtemp())
>>> _ = (folder / "defs.tex").write_text(
...     "\\newcommand{\\R}{\\mathbb{R}}\n\\newif\\ifprof\\proftrue\n", encoding="utf-8"
... )
>>> _ = (folder / "cours.tex").write_text(
...     "\\input{defs}\n\\begin{document}\n$x\\in\\R$\n\\ifprof Corrigé.\\else Énoncé.\\fi\n\\end{document}\n",
...     encoding="utf-8",
... )
>>> course = TexFile(folder / "cours.tex")
>>> _ = course.analyse()
>>> course.signatures.macro("R") is None
True
>>> _ = course.analyse(follow_inputs=True)
>>> course.signatures.macro("R").body, course.signatures.boolean("prof")
('\\mathbb{R}', True)

```

A chapter that declares its master (`% !TEX root = ../cours.tex`) is read after
the master's preamble; `root` notes it.

```pycon
>>> _ = (folder / "chapitre.tex").write_text("% !TEX root = cours.tex\n$\\R$\n", encoding="utf-8")
>>> chapter = TexFile(folder / "chapitre.tex")
>>> _ = chapter.analyse(follow_inputs=True)
>>> chapter.root.name, chapter.signatures.macro("R") is not None
('cours.tex', True)

```

## The expanded view

`expand` returns a view where the user's macros are replaced by their bodies
(`ExpandedFile`, a `TexFile` on the text produced). The tree of the source does
not change.

```pycon
>>> from latexdetok import expand
>>> view = expand(course)
>>> print(view.content())
\input{defs}
\begin{document}
$x\in\mathbb{R}$
\ifprof Corrigé.\else Énoncé.\fi
\end{document}
>>> view.expansions
[Expansion(name='R', start=(3, 5), end=(3, 7), parent=None, environment=False)]

```

Every node of the view finds again what produced it in the source: `origin`
returns the expansion whose body wrote it, `source_span` the source span, and
`source_text` what the user typed there, where `raw_text` is the text of the
view.

```pycon
>>> produced = view.get_envs("$")[0].content[-1]
>>> str(produced), view.origin(produced).name, view.source_span(produced)
('{R}', 'R', ((3, 5), (3, 7)))
>>> math = view.get_envs("$")[0]
>>> view.raw_text(math), view.source_text(math)
('$x\\in\\mathbb{R}$', '$x\\in\\R$')

```

`text_between` reads any span of a file the same way, line endings as written:
that of a diagnostic, or of an `Expansion`.

```pycon
>>> expansion = view.expansions[0]
>>> course.text_between(expansion.start, expansion.end)
'\\R'

```

A structure hidden behind macros appears in the view:

```pycon
>>> beq = TexFile(["\\def\\beq{\\begin{equation}}\n", "\\def\\eeq{\\end{equation}}\n", "\\beq x=1 \\eeq\n"])
>>> _ = beq.analyse()
>>> len(beq.get_envs("equation")), len(expand(beq).get_envs("equation"))
(0, 1)

```

## Conditionals decided, both branches kept

The conditionals that can be decided without compiling (`\iftrue`, booleans of
`\newif`, `\ifnum` on numbers, `\ifmmode`, `\IfBooleanTF`, `\ifstrempty`…) are
decided in the view. No branch is removed: each one is a `TexBranch`, which
knows whether TeX takes it.

```pycon
>>> from latexdetok import TexBranch
>>> body = view.get_envs("document")[0]
>>> [
...     (branch.taken, str(branch), branch.condition.test)
...     for branch in body.content
...     if isinstance(branch, TexBranch)
... ]
[(True, ' Corrigé.', 'ifprof'), (False, ' Énoncé.', 'ifprof')]
>>> view.conditions
[Condition(test='ifprof', value=True, start=(4, 0), end=(4, 7), parent=None)]
>>> taken = next(node for node in body.content if isinstance(node, TexBranch) and node.taken)
>>> [(condition.test, side) for condition, side in view.branches(taken.content[0])]
[('ifprof', True)]

```

## Checking a document

`check` returns what is wrong in a document: the structure (braces,
environments, math, verbatim) and the meaning (`\item` outside a list, `&`
outside a table, `^` or `\alpha` outside math, a missing argument, `\left`
without `\right`, a file not found). It reads the loaded files, expands the
macros, and brings every diagnostic back to the source: what a body wrote is
reported at the use of the macro.

```pycon
>>> from latexdetok import Severity, check
>>> draft = TexFile(
...     [
...         "\\documentclass{article}\n",
...         "\\newcommand{\\beq}{\\begin{equation}}\n",
...         "\\begin{document}\n",
...         "Soit \\alpha{} un angle.\n",
...         "\\beq x = 1\n",
...         "\n",
...         "\\end{document}\n",
...     ],
...     name="brouillon.tex",
... )
>>> diagnostics = check(draft, follow_inputs=False)
>>> for diagnostic in diagnostics:
...     print(diagnostic)
2:19: info [unclosed-environment] “\begin{equation}” never closed
4:6: error [math-command-in-text] “\alpha” outside math
5:1: error [unclosed-environment] “\begin{equation}” never closed, written by “\beq”

```

An error is what TeX would refuse; a warning, a likely mistake that an invisible
context may make valid (an included file, a package environment); an info, valid
LaTeX but worth knowing, like this macro body. Every diagnostic carries a stable
code, its span, the related places and a fix; `render_text` shows them like a
compiler, columns counted from 1.

```pycon
>>> from latexdetok.diagnostics import render_text
>>> unclosed = diagnostics[-1]
>>> unclosed.code, unclosed.start, unclosed.end, unclosed.suggestion
('unclosed-environment', (5, 0), (5, 4), 'close it with “\\end{equation}”')
>>> print(render_text([unclosed], draft.lines, draft.name))
brouillon.tex:5:1: error [unclosed-environment] “\begin{equation}” never closed, written by “\beq”
     5 │ \beq x = 1
       │ ^~~~
     7 │ \end{document}
       │ ^~~~~~~~~~~~~~ “\end{document}” closes the environment around it
       = close it with “\end{equation}”

```

On the command line: it exits in error if there is an error, shows infos with
`--infos`, a JSON array with `--json`, and every `.tex` of a folder that is
given.

```bash
latexdetok check cours.tex
latexdetok check chapitres/ --json
```

## Checking a buffer, not a file

An editor has something a file does not: what is being typed, before it is
saved. The path `-` reads the document from the standard input, and
`--stdin-filename` says which file those bytes are the content of — its folder
is where `\input` and `\usepackage` are looked for, and its name is what the
diagnostics carry.

```bash
cat cours.tex | latexdetok check - --json --stdin-filename cours.tex
```

In the process, the same thing is `path`: lines held in memory that know where
they come from. The lines given are the ones read — the file on disk is only
ever asked where its neighbours are.

```pycon
>>> _ = (folder / "shorthands.tex").write_text("\\newcommand{\\beq}{\\begin{equation}}\n", encoding="utf-8")
>>> _ = (folder / "en-cours.tex").write_text("c'est ce qui est enregistré\n", encoding="utf-8")
>>> tampon = ["\\input{shorthands}\n", "\\begin{document}\n", "\\beq x\\end{equation}\n", "\\end{document}\n"]
>>> en_cours = TexFile(tampon, path=folder / "en-cours.tex")
>>> en_cours.name, en_cours.lines[2]
('en-cours.tex', '\\beq x\\end{equation}\n')
>>> _ = en_cours.analyse(follow_inputs=True)
>>> check(en_cours)
[]

```

`\beq` opens the environment that `\end{equation}` closes: without
`shorthands.tex`, found next to the path, the `\end` would have nothing to
close.

In VS Code, a task runs the command on the open file, and its diagnostics show
up in the Problems panel. One problem matcher per severity, because VS Code only
knows their names in English; `--color never`, because the terminal of a task
would otherwise receive colours. The expressions accept the severity in both
languages: the task thereby follows `LATEXDETOK_LANG` without being touched
again. As the default build task, ⇧⌘B runs it; in
`~/Library/Application Support/Code/User/tasks.json` it works in every folder,
and a second task with `'${fileDirname}'` in place of `'${file}'` checks a whole
folder.

```json
{
  "version": "2.0.0",
  "tasks": [
    {
      "label": "latexdetok check",
      "type": "shell",
      "command": "python3 -m latexdetok check --color never '${file}'",
      "options": {
        "env": { "LATEXDETOK_LANG": "fr" }
      },
      "group": { "kind": "build", "isDefault": true },
      "presentation": { "reveal": "silent" },
      "problemMatcher": [
        {
          "owner": "latexdetok",
          "fileLocation": "absolute",
          "severity": "error",
          "pattern": { "regexp": "^(.+):(\\d+):(\\d+): (?:error|erreur) \\[([a-z-]+)\\] (.*)$", "file": 1, "line": 2, "column": 3, "code": 4, "message": 5 }
        },
        {
          "owner": "latexdetok",
          "fileLocation": "absolute",
          "severity": "warning",
          "pattern": { "regexp": "^(.+):(\\d+):(\\d+): (?:warning|avertissement) \\[([a-z-]+)\\] (.*)$", "file": 1, "line": 2, "column": 3, "code": 4, "message": 5 }
        }
      ]
    }
  ]
}
```

## Looking for a sentence

`to_text` renders the typeset text: blanks collapsed, accents composed, labels
and settings gone, formulas as written. A sentence cut by a line ending becomes
a sentence again there, and every character keeps its node and its position in
the source.

```pycon
>>> from latexdetok import to_text
>>> text = to_text(tex)
>>> print(text.text)
Le titre long
Soit \vect{u} un vecteur, et \frac{1}{2}.
premier
second
>>> [(found.start, found.position, str(found.node)) for found in text.find("un vecteur")]
[(28, (5, 16), 'un vecteur, et')]
>>> to_text(tex, math="skip").text.splitlines()[1]
'Soit un vecteur, et .'

```

The comment, the short argument of the section and the body of the definition
are not typeset; `\item` breaks the line. On an expanded view (`expand`),
`\vect{u}` would give its body, and the branch a conditional discards would
typeset nothing.

## Editing a source

`export` draws a modified output from the analysis. An edit replaces a span of
the source; whatever none of them touches comes out of the file **byte for
byte** — comments, indentation, spaces. That is what parts `rewrite` from
`str()`, which rewrites the whole file from the tree and gives an unreadable
diff.

The arguments are siblings of the command, not its children: **changing the
command does not touch its arguments**, and the other way round.

```pycon
>>> from latexdetok.export import Edit, corrected, rewrite
>>> section = next(node for node in root.iter() if node.is_command("section"))
>>> rewrite(tex, [Edit.of(section, "\\subsection")]).splitlines()[3]
'\\subsection[Court]{Le titre long}'

```

Changing an argument means editing its inside. `get_commands_arguments` returns
every command followed by its bound arguments.

```pycon
>>> found = next(iter(tex.get_commands_arguments("section")))
>>> [str(node) for node in found]
['\\section', '[Court]', '{Le titre long}']
>>> rewrite(tex, [Edit.inside(found[-1], "Un titre plus court")]).splitlines()[3]
'\\section[Court]{Un titre plus court}'

```

Adding an optional argument means inserting right after the command — on a
filter of commands, as many edits as there are uses.

```pycon
>>> uses = [node for node in root.iter() if node.is_command("vect")]
>>> rewrite(tex, [Edit.after(node, "[XX]") for node in uses]).splitlines()[4]
'Soit $\\vect[XX]{u}$ un vecteur, et $\\frac{1}{2}$.'

```

An environment is changed through its two delimiters, which are not nodes but
spans of the group.

```pycon
>>> (itemize,) = list(root.get_envs("itemize"))
>>> changed = rewrite(
...     tex, [Edit.opening(itemize, "\\begin{enumerate}"), Edit.closing(itemize, "\\end{enumerate}")]
... )
>>> changed.splitlines()[5], changed.splitlines()[8]
('\\begin{enumerate}', '\\end{enumerate}')

```

`corrected` applies a **correcting function** to nodes: it returns the LaTeX
that replaces a node, or `None` to leave it. The filter therefore goes through
it, or through the nodes one hands it. A trap: the name defined by
`\newcommand` is a command like any other; `is_in` leaves it out.

```pycon
>>> definition = next(iter(tex.get_commands_arguments("newcommand")))
>>> uses = [node for node in root.iter() if node.is_command("vect") and not node.is_in(definition)]
>>> len(uses)
1
>>> rewrite(tex, corrected(uses, lambda node: "\\overrightarrow")).splitlines()[4]
'Soit $\\overrightarrow{u}$ un vecteur, et $\\frac{1}{2}$.'

```

From one file to another, with the diff as proof: one single line moves. With
`newline=""`, Python writes the line endings `rewrite` gives, which are the
source's, instead of the platform's.

```pycon
>>> import difflib
>>> edits = corrected(
...     (node for node in course.container.iter() if node.is_command("R")), lambda node: "\\mathbb{R}"
... )
>>> edited = rewrite(course, edits)
>>> _ = (folder / "cours-relu.tex").write_text(edited, encoding=course.encoding, newline="")
>>> print(
...     "".join(
...         difflib.unified_diff(course.lines, edited.splitlines(keepends=True), "cours.tex", "cours-relu.tex")
...     ),
...     end="",
... )
--- cours.tex
+++ cours-relu.tex
@@ -1,5 +1,5 @@
 \input{defs}
 \begin{document}
-$x\in\R$
+$x\in\mathbb{R}$
 \ifprof Corrigé.\else Énoncé.\fi
 \end{document}

```

What raises rather than returning a wrong source: two edits that overlap, a
position outside the file, a node with no position, and a node that comes from
an expanded view, whose positions are not the source's.

```pycon
>>> rewrite(tex, [Edit.of(section, "a"), Edit.of(section, "b")])
Traceback (most recent call last):
  ...
ValueError: two edits overlap at (4, 0): “\section”
>>> rewrite(
...     tex,
...     [
...         Edit.of(
...             next(node for node in expand(tex).container.iter() if node.is_command("section")), "\\subsection"
...         )
...     ],
... )
Traceback (most recent call last):
  ...
ValueError: an edit from another file than the one being rewritten: “\section” — an expanded view has its own positions (see `checks.in_source`)

```

## Changing the language of the messages

The messages are in English and can be translated. `set_language` changes them
on the fly, `LATEXDETOK_LANG` chooses them for a whole session. What a machine
reads does not move: the code of the diagnostic, the value of `Severity`, the
JSON keys.

```pycon
>>> from latexdetok import TexFile, check, set_language
>>> broken = TexFile(
...     ["\\documentclass{article}\n", "\\begin{document}\n", "\\emph{a\n", "\\end{document}\n"],
...     name="brouillon.tex",
... )
>>> _ = broken.analyse()
>>> english = broken.diagnostics[0]
>>> english.code, english.message, english.suggestion
('unclosed-brace', '“{” of “\\emph” never closed', 'close it with “}”')
>>> set_language("fr")
>>> _ = broken.analyse()
>>> french = broken.diagnostics[0]
>>> french.code, french.message, str(french.severity), french.severity.text
('unclosed-brace', '« { » de « \\emph » jamais fermée', 'error', 'erreur')
>>> set_language("")  # back to the language of the environment

```

One more language is one more file in `data/`: `messages-xx.txt`, `key
  template`, the keys that are missing falling back to English (see `messages`).

## The frame of a long document

`rendering` sums a document up without walking the tree: the outline in text and
an HTML page on the compact view (the source) and the expanded one (the expanded
view).

```pycon
>>> from latexdetok.rendering import html_page, outline
>>> print(outline(tex))
cours.tex · 10 lines · 51 nodes · 0 diagnostic
        3 words · 1 \documentclass · 1 \newcommand · 1 \vect
     3  └ document  3–10
     4    └ section · Le titre long 5 words · 2 $…$  4–10
     6      └ itemize 2 words · 2 \item  6–9
>>> page = html_page(course)
>>> _ = (folder / "cours.html").write_text(page, encoding="utf-8")

```

The page stands alone: one file, its style and its script inside, nothing to
serve and nothing to install. At the top, the counts and a switch between the
two views; on the left, the outline, drawn by the script from the JSON the page
carries; on the right, the source with its line numbers.

```pycon
>>> import re
>>> print(re.search(r'<span class="meta">(.*?)</span>', page)[1])
5 lines · 1 macro use · 1 decided conditional · 0 diagnostics

```

Every use of a macro is a `<span class="u">` that holds both sides: the source
(`.s`) and what it expands to (`.x`). A click folds or unfolds it; the switch
unfolds them all. A discarded branch stays in the text, dimmed (`.skip`).

```pycon
>>> print(page[page.index('<i class="n" id="L3">') : page.index('<i class="n" id="L5">')], end="")
<i class="n" id="L3">3</i>$x\in<span class="u" data-a="3" data-b="3" title="\R"><span class="s">\R</span><span class="x">\mathbb{R}</span></span>$
<i class="n" id="L4">4</i>\ifprof Corrigé.\else<span class="skip"> Énoncé.</span>\fi
>>> import shutil
>>> shutil.rmtree(folder)  # the example files are no longer needed

```

A whole page, drawn from `tests/fixtures/course.tex`, is in
[frame-example.html](frame-example.html): download it and open it in a browser
(GitHub shows its source, not the page). A test keeps it in step with the code.

The same thing on the command line:

```bash
python3 scripts/render.py cours.tex                               # the outline
python3 scripts/render.py cours.tex --format html -o cours.html --inputs
```

## Writing a node by hand

The node classes can also be built directly, to compare or to produce LaTeX, for
instance. With no file and no position, `raw_text()` returns `str()`.

```pycon
>>> from latexdetok import TexContent
>>> group = TexGroup("{", [TexContent("gras", command=True), TexContent("texte")])
>>> str(group), group.raw_text()
('{\\gras texte}', '{\\gras texte}')

```

## Scripts

From the root of the repository:

```bash
python3 -m latexdetok check cours.tex                                    # diagnostics
python3 scripts/render.py cours.tex --format html -o cours.html          # the frame
python3 scripts/corpus.py path/to/corpus --inputs --expand               # non-regression
python3 scripts/bench.py cours.tex --inputs --expand --profile           # speed
python3 scripts/fingerprints.py path/to/corpus --inputs -o before.json   # fingerprints
python3 scripts/fingerprints.py path/to/corpus --inputs --compare before.json
~/.cache/latexdetok-mypyc/bin/python scripts/compile.py                  # the build
```

## Compiled mode

Compiled by mypyc, the core runs twice as fast. The extensions only serve if
they match the sources; `COMPILED` says what is running, and
`LATEXDETOK_PURE=1` forces the sources.

```pycon
>>> import latexdetok
>>> isinstance(latexdetok.COMPILED, bool)
True

```
