"""Shows the frame of a LaTeX file: an outline in text, an HTML page.

    python3 scripts/render.py cours.tex                         # the outline, in the terminal
    python3 scripts/render.py cours.tex --depth 3 --inputs
    python3 scripts/render.py cours.tex --format html -o cours.html --inputs

`--inputs` reads the definitions of the loaded files. `--expand` shows the
expanded view (macros replaced, conditionals decided) instead of the source; the
HTML page always carries both views.
"""

import argparse
import sys
from pathlib import Path

from latexdetok import TexFile, expand
from latexdetok.rendering import html_page, outline


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("source", type=Path)
    parser.add_argument("--format", choices=("outline", "html"), default="outline")
    parser.add_argument("-o", "--output", type=Path, help="the file produced; standard output otherwise")
    parser.add_argument("--inputs", action="store_true", help="read the definitions of the loaded files")
    parser.add_argument("--expand", action="store_true", help="outline of the expanded view")
    parser.add_argument("--depth", type=int, help="depth of the outline")
    options = parser.parse_args()

    tex = TexFile(options.source.expanduser())
    tex.analyse(follow_inputs=options.inputs)
    if options.format == "html":
        text = html_page(tex)
    else:
        shown = expand(tex) if options.expand else tex
        text = outline(shown, depth=options.depth, color=options.output is None and sys.stdout.isatty())
    if options.output is None:
        print(text)
    else:
        options.output.write_text(text, encoding="utf-8")
        print(options.output)


if __name__ == "__main__":
    main()
