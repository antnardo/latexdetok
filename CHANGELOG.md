# Changelog

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.6.4] — 2026-09-30

### Fixed

- A package that declares itself with `\ProvidesExplPackage` is read in expl3.
  That declaration ends on `\ExplSyntaxOn`, so a package written in expl3 never
  writes it; read without it, `\l__pkg_volume_fp` in the body of one of its
  macros is `\l` followed by subscripts, and every document using that macro
  reported a `_` outside math — on a file that compiles. `\ProvidesExplClass`
  and `\ProvidesExplFile` do the same, and the three have their signature
  (four mandatory arguments), read before the switch: their own description
  keeps its spaces.

## [0.6.3] — 2026-09-27

### Fixed

- An escaped character no longer stops `\item` outside a list from being
  reported. The guard behind it is right — an unknown command may have opened
  one — but `\%` is not a command: a backslash and one character that is not a
  letter is that character. One `40\%` in a paper and the rest of the document
  went unchecked.
- `\%`, `\&`, `\#`, `\{` and `\}` join the kernel table, where `\_`, `\$`, `\,`
  and `\;` already were: the harvest does not catch the way `latex.ltx` defines
  them.
- The symbols of gensymb — `\degree`, `\celsius`, `\ohm`, `\micro`,
  `\perthousand` — are known.

## [0.6.2] — 2026-09-27

### Fixed

- `to_text(math="skip")` left two spaces where a skipped formula parted a blank
  from a control space or a `~`: `The space $x$\ is.` gave “The space  is.”,
  which a spelling checker reads as a mistake. The blanks the formula parted
  become one; what no formula parted is left alone, `The word \ is.` printing
  two spaces in TeX as well.
- `compilable()` wrote an environment out as `{…}` in math, where a brace is an
  Ord atom: `$a{-}b$` sets the sign tight where the source set it as a binary
  operator, and the page changed. In math the group is now
  `\begingroup … \endgroup`, which is what `\begin{x}` opens.
- An environment the document hangs a hook on (`\AddToHook{env/x/begin}`, and
  the four of etoolbox) is no longer expanded under `writable=True`: written
  out as a brace it took the hook's code away with it, silently.

Both `compilable()` fixes were measured against pdflatex, before and after,
pixel for pixel.

## [0.6.1] — 2026-09-27

### Fixed

- A conditional on a parameter was decided in the body of a definition the
  tokeniser does not recognise. The bodies of `\csdef` and `\newrobustcmd`
  (etoolbox), `\cs_new:Npn` and `\cs_new_protected:Npn` (expl3) or
  `\newtcolorbox` read like text, where `\ifstrempty{#1}` was false and
  `\IfValueTF{#1}` true whatever the use passes, and `compilable()` wrote the
  branch it had chosen: `\csdef{Fill}#1{\ifstrempty{#1}{(empty)}{#1}}` became
  `\csdef{Fill}#1{#1}`, and the document lost its “(empty)”. A test that holds a
  parameter, `#1` or `##1`, is no longer decided, in any body; nor is an
  `\ifnum` whose second number a parameter goes on (`\ifnum 1<2#1`). Found by
  the bench of the Stack Overflow question 1509799.
- `compilable()` lost the `\ignorespacesafterend` of an environment. Written as
  `}`, its `\end` no longer ignored the spaces after it —
  `\newenvironment{tag}{[}{]\ignorespacesafterend}` gave “A [x] b.” for
  `A \begin{tag}x\end{tag} b.`, where LaTeX prints “A [x]b.” — and the global
  flag it sets, left in the braces, made the next `\end{…}` of the document eat
  the spaces after it. An `\ignorespacesafterend` that ends the environment
  moves after the brace, as `\ignorespaces`; `expand(tex, writable=True)` leaves
  unexpanded an environment that sets it anywhere else, and `compilable()` warns
  about one in a view not built so. The `\@doendpe` the same rewriting was
  thought to lose — no indent after an environment that ends on a list or a
  `center` — crosses the braces since LaTeX 2024-11-01; it is only lost with an
  older kernel.
- `compilable()` wrote out the `-NoValue-` of a missing optional argument passed
  on to a command that stays — of a package, of expl3, or kept by `keep`. It is
  text there, not ltcmd's marker: with
  `\NewDocumentCommand{\opt}{o m}{#2 \ShowOpt{#1}}`, `\opt{a}` became
  `a \ShowOpt{-NoValue-}`, and the `\tl_if_novalue:nTF` of `\ShowOpt` printed
  “[-NoValue-]” instead of “[none]”. `expand(tex, writable=True)` now leaves such
  a use unexpanded, with its macro — `\opt[b]{c}` still expands. For an
  environment, every use stays: its `\begin` is in the view whether expanded or
  not, and a script that removes the definitions of what was expanded (the
  answer to question 1509799) must keep the definition of any `\begin` left.
  A `-NoValue-` that a decided `\IfValueTF` tests, or that sits in a branch it
  discards, still expands. `compilable()` warns about such uses in a view not
  built so.

## [0.6.0] — 2026-09-27

### Added

- `invalid-in-math`: a command LaTeX refuses in math mode, `\item` and
  `\circle` — the two it guards with `\@inmatherr` in `latex.ltx`, so an error
  of its own and not a matter of taste. A `$` is an opening and a closing at
  once: a forgotten one does not leave math open where it was forgotten, it
  pairs with the next `$`. Forget two and the count is even again, the prose
  between them is read as math, and nothing was reported although the file does
  not compile — `Command \item invalid in math mode`. The diagnostic lands on
  the `\item`, as TeX's does, and points at the `$` that opened the math, which
  TeX never says.
- `ExpandedFile.compilable(edits=())`: the expanded view written back as a
  source TeX compiles into the same document. The view keeps, for the analysis,
  what TeX would run twice or read otherwise: `\begin{env}` around the code it
  inserted, both branches of the conditionals it decided, `-NoValue-` written
  out — which is not ltcmd's marker, so that `\IfValueTF{-NoValue-}` took the
  other branch. Written back, a user environment becomes a group, a decided
  conditional with arguments the inside of its branch taken, and the branch of
  an `\@ifstar` the use did not take goes. `edits` apply with it, to remove the
  definitions of what was expanded for instance. Checked by compiling: the
  documents of the Stack Overflow question 1509799, and TeX Live's
  `scalerel.tex` (17 pages) and `section-doc.tex` (9 pages), give the same PDF,
  text and pixels.
- `expand(tex, keep=…)` leaves the macros and environments it names as they
  are, and `expand(tex, writable=True)` the bodies TeX would read otherwise in
  the document: under `\makeatletter`, `\@title` is one command in the body,
  `\@` and “title” once written out. `compilable()` warns in the log when a view
  not built so writes one.
- `SignatureRegistry.open_group()`, `close_group()`, `dissolve_group()`,
  `globally()` and `depth`: the save stack of TeX's groups (see below).
- `characters.is_control_word(name)`: does TeX skip the blanks after `\name`?

### Fixed

- A definition in the body of a macro was learned when that macro was
  defined. `\newcommand{\setauthor}[1]{\renewcommand{\theauthor}{#1}}` turned
  every `\theauthor` of the document into `#1`, before `\setauthor{Alice}` as
  after, and the view no longer compiled; `\newcommand{\inner}[1]{#1 ##1}` in the
  body of `\makeinner` gave `B #1` for `\inner{B}`. It is learned where the
  body is used, by the expanded view, and a use that follows `\setauthor{Alice}`
  waits for the pass where the `\renewcommand` has been read: “After: Alice.”,
  as TeX prints. On the corpus, the same cause had `\xdef\@devoirdate{#4}` in
  `\devoirlibre` erase the default of `\@devoirdate`, and `\let\par\relax` in a
  helper strip `\par` of its signature for the whole document.
- A definition outlived its group. `{\renewcommand{\x}{B}\x}\x` read `B` twice
  where TeX prints “BA”. It ends with its braces, its environment, its math or
  its `\begingroup`, and a file included there defines inside the group;
  `\gdef`, `\xdef` and `\global` outlive them all. The braces of an argument
  undo nothing — TeX strips them before running the code, and
  `\AtBeginDocument{\renewcommand…}` holds for the document —, nor does
  `\end{document}`.
- The view lost the space after a use whose body ends on a control word:
  `\degree{20} C` printed “20°C”, `\shout{loud} words` “loudwords”, and a
  `\begin{remark}` at the end of its line, with a begin code ending on
  `\itshape`, “Remark.Words”. TeX had read that blank as a space before the
  body ran; the body now writes `{}` after a control word that reads nothing
  after it. Not after one that looks ahead (`\item`, `\ignorespaces`), nor after
  `\begin{env}` when `\newenvironment{env}[1][d]` looked for its optional
  argument there and skipped the blanks.
- The view turned into a space the line ending TeX drops after a use without
  argument: `\pkg` at the end of a line, then `C.`, printed “scalerel C.” for
  “scalerelC.”, and changed 2 pages of TeX Live's `scalerel.tex`. The body
  writes a `%` before that line ending, which keeps the lines of the source. The
  same after a use that ends on an argument written without braces, `\twice\LaTeX`.
- A boolean set in the second argument of an unknown command was taken as set
  for sure, then undone at its brace: `\DeclareOptionX{bloc}{\AMC@qbloctrue}`
  left `\ifAMC@qbloc` false, where TeX has it true when the option is given. Its
  value is now unknown, as it already was in the first argument. On the corpus,
  59 conditionals of automultiplechoice are no longer decided.
- `to_text` glued a footnote to the word before it: `point.\footnote{See…}`
  gave “point.See”, one spelling mistake and one broken sentence per note. A
  note (`\footnote`, `\footnotetext`, `\marginpar`, `\marginnote`, `\todo`) now
  comes out as a paragraph of its own at the next break, where it keeps its
  positions.
- `to_text` printed the keys of `\eqref`, `\vref`, `\citeauthor`, `\citeyear`,
  `\footcite` and `\bibitem` (“As in eq:rest”). They are declared (varioref,
  natbib and biblatex citations, with their star) and their keys are names, not
  text: `check` no longer takes the `_` of `\eqref{eq_a}` or `\bibitem{k_84}`
  for a subscript outside math.
- `to_text` kept the formula of `\ensuremath{…}` with `math="skip"`.
- `to_text` kept the blank after a control word, which TeX skips: `c\oe ur`
  gave “cœ ur”, `Stra\ss e` “Straß e”, and `\LaTeX is` hid the “LaTeXis” the
  PDF shows. The same after a comment, which takes its line ending with it.
  `\og` and `\fg` bring their no-break space, as babel-french does, and
  `\hfill`, `\hspace` still part the words around them.
- `\"\i` and `\'\i` gave a dotless ı and a combining accent, not “ï” and “í”.

### Changed

- `to_text` gives a reference a stand-in of the kind TeX prints, rather than
  nothing: `\ref`, `\pageref`, `\vref`, `\cref` “1”, `\eqref` “(1)”, the
  citations “[1]”, `\citeyear` “1”. “Figure~\ref{fig:a} and” read “Figure  and”,
  and “Einstein~\cite{e05}.” “Einstein .”: two false positives for a grammar
  checker in every such sentence.

## [0.5.1] — 2026-09-27

### Fixed

- The server counts columns in the unit the client chose when it started, not
  always in UTF-16. pygls takes the client's first choice: UTF-16 for VS Code,
  which offers nothing else, but UTF-8 for Neovim and Helix, and UTF-32 for
  Emacs. In Neovim the underline started a place early for every `é` before it
  on the line, and the quick fixes of 0.5.0 edited the wrong place: closing
  `Réponse été déjà : $x+1` gave `Réponse été déjà :$ $x+1`. Emacs was wrong
  only past the basic plane, on a `𝔸` or an emoji. VS Code was never affected.
  `diagnose` and `code_actions` take the unit as `encoding`, UTF-16 by default.
- Searches through the interactive `kpsewhich` are asked one question at a
  time. That session answers one line per name, so two threads asking at once
  read each other's answers: one gave the session up, the other wrote to a pipe
  that had just been closed, and `ValueError: write to closed file` — which is
  not an `OSError` and so was caught nowhere — stopped the analysis. A language
  server analyses several open documents in parallel threads, which is where it
  showed. `ValueError` is caught too, and the plain call answers instead.

### Changed

- The README gives the lines that start the server in Neovim, Emacs (Eglot)
  and Helix, instead of saying there was nothing to configure. They were tried
  as written, quick fixes included, in Neovim 0.12, Emacs 30 and 31 with and
  without AUCTeX, and Helix 25.07. The Emacs lines hook both `LaTeX-mode`,
  AUCTeX's, and `latex-mode`, the one Emacs comes with: hooked on the first
  alone, an Emacs without AUCTeX never started the server.

## [0.5.0] — 2026-09-27

### Added

- **Quick fixes.** A diagnostic that knows how to repair itself carries the
  repair as edits, `repairs`, and not only as a sentence. In an editor, ⌥⌘. on
  the place at fault closes the `$`, the brace, the bracket or the environment,
  or removes the brace too many — `textDocument/codeAction` on the server side,
  `code_actions(text, uri, span)` for whoever wants it in Python.
- `Repair(start, end, text)`, the same three fields as an `Edit`, which is what
  applies it.

Five codes carry a repair: `unclosed-math`, `unclosed-brace`,
`unclosed-bracket`, `unclosed-environment` and `extra-brace`. The others
describe a mistake whose repair is anyone's guess — an `\item` outside a list
does not say where the list should open — and offer nothing rather than guess.

Three rules, each one a way of not making the source worse than it was. A
closing lands at the end of what is written before the cut: never on the blank
line that cuts a paragraph, which would stop being blank and weld two
paragraphs into one, and never before the opening it closes. An info never
carries a repair, because it reports LaTeX that is valid —
`\newcommand{\beq}{\begin{equation}}` is how that kind of macro is written, and
closing it inside the definition would break it. And what a macro body wrote
carries none either: the place to repair is the definition, which the document
does not own.

A test applies every repair and checks the diagnostic is gone.

## [0.4.3] — 2026-09-27

### Changed

- The client's README says where the extension applies: installed for the user,
  every window has it, but one already open keeps the extension host it started
  with, profiles carry their own extensions, and in a dev container or on a
  remote it runs on that side — where the files it reads live, so the server
  has to be installed there too. A release is what carries a README to PyPI.

## [0.4.2] — 2026-09-27

### Fixed

- The server logs the traceback of what it could not analyse, not only the name
  of the document: a server that swallows the reason is a server nobody can
  fix. The editor shows it in its output channel.
- The VS Code client declares what it does in an untrusted workspace. Without
  that declaration VS Code disabled it there and nothing was underlined, with
  no message. The command that starts the server is now taken from the user
  settings only, never from a workspace one has not approved.
- The client restarts even when the previous server never started: stopping a
  client in that state raises, and the restart stopped there.

### Changed

- The README no longer promises “every mistake at once” without a word on what
  an unmatched opening swallows. Independent mistakes are all reported; inside
  a `$` left open, an `\item` is math, not an `\item` out of place, and
  reporting it would be reporting the same mistake twice. In an editor the next
  one appears as soon as the first is corrected.

## [0.4.1] — 2026-09-27

### Fixed

- `latexdetok-lsp` without the `lsp` extra says what to install instead of
  showing a traceback about `lsprotocol`. An entry point cannot depend on an
  extra: `pip install latexdetok` installs the command all the same, and an
  editor that finds that one on its `PATH` — before the environment the server
  really lives in — failed with a stack trace naming a module nobody had asked
  for.

## [0.4.0] — 2026-09-27

### Added

- A **language server**: `pip install "latexdetok[lsp]"` installs
  `latexdetok-lsp`, which speaks the protocol on its standard input and
  underlines the buffer as it is typed. A command answers about the file that
  was saved; the server answers about what is being written — 14 to 76 ms a
  check, against some 600 ms for a fresh process, which is the whole reason it
  is a server. Settings travel in `initializationOptions`: `language`,
  `followInputs`, `expand`.
- A client for VS Code, in `editors/vscode/`: it starts the server and hands it
  the buffer, and does nothing else. Not on the marketplace — `npm install`,
  `npx @vscode/vsce package`, `code --install-extension`. Any other LSP editor
  points its own client at `latexdetok-lsp`, over stdio.
- `split_lines(text)`, beside `decode_lines(data)`: the lines of a text as
  written, cut where a file is cut. An editor holds a string, not bytes.

`pygls` is the only dependency the package has ever taken, and only for the
server: the core stays on the standard library, and whoever does not want a
server does not carry it.

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
