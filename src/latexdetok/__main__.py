"""Command line: `latexdetok check file.tex…`, or `python -m latexdetok check file.tex…`.

The `latexdetok` command is the one to document: it is what `uvx latexdetok` and
`pipx install latexdetok` run, the two ways in for a TeX user who does not keep
a Python environment. `-m` stays for scripts that must use a given interpreter.

Renders the diagnostics of one or more documents (see `checks`), compiler-style
or as JSON, and exits in error if there is at least one error: enough for a
script, a commit hook or a VS Code task, whose problem matcher reads the lines
`file:line:column: severity [code] message`. A folder gives all its `.tex`.

The path `-` reads the document from the standard input, which is what an
editor has to offer: the buffer, unsaved. `--stdin-filename` then says which
file those bytes are the content of, so that inclusions are looked for in its
folder and the diagnostics carry its name — the bytes are still the ones
given, never re-read from the disk.
"""

import argparse
import json
import sys
from collections import Counter
from collections.abc import Sequence
from pathlib import Path

from latexdetok.analyse import TexFile
from latexdetok.checks import check
from latexdetok.diagnostics import Severity, render_text, to_json
from latexdetok.messages import say
from latexdetok.resolution import decode_lines

__all__ = ["main"]

# Exit codes: diagnostics with no error, at least one error, unreadable or missing file.
OK, ERRORS, UNREADABLE = 0, 1, 2
# The path that means “read the standard input”, as every linter spells it.
STDIN = Path("-")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="latexdetok", description="Tolerant LaTeX tokeniser.")
    commands = parser.add_subparsers(dest="command", required=True)
    checking = commands.add_parser(
        "check",
        help="diagnose documents",
        description="Diagnostics of a document: structure and meaning, macros expanded, source positions.",
    )
    checking.add_argument(
        "paths", nargs="+", type=Path, help=".tex files, or folders to walk; `-` is the standard input"
    )
    checking.add_argument(
        "--stdin-filename",
        type=Path,
        metavar="PATH",
        help="with `-`, the file the standard input is the content of",
    )
    checking.add_argument("--json", action="store_true", help="a JSON array, columns counted from 1")
    checking.add_argument("--infos", action="store_true", help="show the infos too (valid LaTeX)")
    checking.add_argument(
        "--no-inputs", action="store_true", help="do not read the loaded files (\\input, \\usepackage…)"
    )
    checking.add_argument("--no-expand", action="store_true", help="do not expand the macros")
    checking.add_argument("--color", choices=("auto", "always", "never"), default="auto")
    options = parser.parse_args(argv)
    if STDIN in options.paths and len(options.paths) > 1:
        checking.error(say("cli.stdin-alone"))
    if options.stdin_filename is not None and STDIN not in options.paths:
        checking.error(say("cli.stdin-filename-without-stdin"))
    return _check(options)


def _check(options: argparse.Namespace) -> int:
    color = options.color == "always" or (options.color == "auto" and sys.stdout.isatty())
    counts: Counter[Severity] = Counter()
    hidden = 0
    blocks: list[str] = []
    found: list[dict[str, object]] = []
    status = OK
    for path in _documents(options.paths):
        try:
            tex = _read(path, options.stdin_filename)
        except (OSError, UnicodeDecodeError) as error:
            print(say("cli.unreadable", path=path, error=error), file=sys.stderr)
            status = UNREADABLE
            continue
        diagnostics = check(tex, follow_inputs=not options.no_inputs, expanded=not options.no_expand)
        counts.update(diagnostic.severity for diagnostic in diagnostics)
        shown = [d for d in diagnostics if options.infos or d.severity is not Severity.INFO]
        hidden += len(diagnostics) - len(shown)
        # The name the diagnostics carry: the whole path, which a problem matcher opens.
        name = str(options.stdin_filename or path) if path == STDIN else str(path)
        if options.json:
            found.extend(to_json(shown, name))
        elif shown:
            blocks.append(render_text(shown, tex.lines, name, color=color))
    if options.json:
        print(json.dumps(found, ensure_ascii=False, indent=1))
    else:
        if blocks:
            print("\n\n".join(blocks), end="\n\n")
        print(_summary(counts, hidden))
    if status == OK and counts[Severity.ERROR]:
        status = ERRORS
    return status


def _read(path: Path, stdin_filename: Path | None) -> TexFile:
    """The document at `path`, or the standard input when it is `-`."""
    if path != STDIN:
        return TexFile(path)
    encoding, lines = decode_lines(sys.stdin.buffer.read())
    name = None if stdin_filename is not None else str(STDIN)
    return TexFile(lines, encoding=encoding, name=name, path=stdin_filename)


def _documents(paths: Sequence[Path]) -> list[Path]:
    documents: list[Path] = []
    for path in paths:
        if path != STDIN and path.is_dir():
            documents.extend(sorted(path.rglob("*.tex")))
        else:
            documents.append(path)
    return documents


def _summary(counts: Counter[Severity], hidden: int) -> str:
    parts = [
        severity.counted(counts[severity]) for severity in (Severity.ERROR, Severity.WARNING, Severity.INFO)
    ]
    return ", ".join(parts) + (" " + say("cli.hidden", count=hidden) if hidden else "")


if __name__ == "__main__":
    sys.exit(main())
