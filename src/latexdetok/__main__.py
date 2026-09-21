"""Command line: `python -m latexdetok check file.tex…`.

Renders the diagnostics of one or more documents (see `checks`), compiler-style
or as JSON, and exits in error if there is at least one error: enough for a
script, a commit hook or a VS Code task, whose problem matcher reads the lines
`file:line:column: severity [code] message`. A folder gives all its `.tex`.
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

__all__ = ["main"]

# Exit codes: diagnostics with no error, at least one error, unreadable or missing file.
OK, ERRORS, UNREADABLE = 0, 1, 2


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m latexdetok", description="Tolerant LaTeX tokeniser.")
    commands = parser.add_subparsers(dest="command", required=True)
    checking = commands.add_parser(
        "check",
        help="diagnose documents",
        description="Diagnostics of a document: structure and meaning, macros expanded, source positions.",
    )
    checking.add_argument("paths", nargs="+", type=Path, help=".tex files, or folders to walk")
    checking.add_argument("--json", action="store_true", help="a JSON array, columns counted from 1")
    checking.add_argument("--infos", action="store_true", help="show the infos too (valid LaTeX)")
    checking.add_argument(
        "--no-inputs", action="store_true", help="do not read the loaded files (\\input, \\usepackage…)"
    )
    checking.add_argument("--no-expand", action="store_true", help="do not expand the macros")
    checking.add_argument("--color", choices=("auto", "always", "never"), default="auto")
    options = parser.parse_args(argv)
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
            tex = TexFile(path)
        except (OSError, UnicodeDecodeError) as error:
            print(say("cli.unreadable", path=path, error=error), file=sys.stderr)
            status = UNREADABLE
            continue
        diagnostics = check(tex, follow_inputs=not options.no_inputs, expanded=not options.no_expand)
        counts.update(diagnostic.severity for diagnostic in diagnostics)
        shown = [d for d in diagnostics if options.infos or d.severity is not Severity.INFO]
        hidden += len(diagnostics) - len(shown)
        name = str(path)
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


def _documents(paths: Sequence[Path]) -> list[Path]:
    documents: list[Path] = []
    for path in paths:
        if path.is_dir():
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
