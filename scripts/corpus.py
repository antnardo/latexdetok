"""Runs a folder of `.tex` through the tokeniser, read-only.

For every file: the analysis does not raise, `str()` re-analysed gives the same
tree back, every element reads its own source again; then the count of the
diagnostics by severity and by code, with a few lines as examples. This is the
non-regression corpus of the roadmap: a change to the tokeniser must degrade
nothing here.

With `--expand`, every file is also expanded (`expansion`): the view must not
raise, and every element copied from the source must read its text again there.
The summary counts the uses expanded, the conditionals decided, the environments
the view reveals, then the diagnostics of `check` (expanded view, source
positions).

The criterion of milestone 2: no error on a file that compiles. A file compiles
if its `.log`, more recent than itself, holds no TeX error; the errors of
`check` on those files are listed apart.

    python3 scripts/corpus.py path/to/corpus [--inputs] [--expand]
"""

import argparse
import re
import time
from collections import Counter, defaultdict
from pathlib import Path

from latexdetok import ExpandedFile, Severity, TexContent, TexFile, TexGroup, expand
from latexdetok.characters import collapse_spaces
from latexdetok.checks import diagnose

IGNORED_PARTS = {"backup", ".sauvegardes"}
EXAMPLES = 5
# A TeX error in a log: `! Message`, or `file:line: message` with -file-line-error.
TEX_ERROR = re.compile(r"^! |^[^:\n]+\.(?:tex|sty|cls):\d+: ", re.MULTILINE)
# `raw_text()` keeps the file's line endings, the tree writes `\n`: they are compared in `\n`.
LINE_ENDING = re.compile(r"\r\n?")


def shape(node):
    if isinstance(node, TexGroup):
        return (node.delimiter if node.math else node.name, [shape(child) for child in node.content])
    return (type(node).__name__, str(node))


def walk(node):
    yield node
    if isinstance(node, TexGroup):
        for child in node.content:
            yield from walk(child)


def rereads_source(node) -> bool:
    raw = LINE_ENDING.sub("\n", node.raw_text())
    if node.is_par():
        return not raw.strip()
    if node.is_pure_text():
        return collapse_spaces(raw) == node.content
    if isinstance(node, TexGroup):
        opening, closing = node.enclosures()
        return re.sub(r"\s", "", raw).startswith(re.sub(r"\s", "", opening)) and raw.endswith(closing)
    # `\` at the end of a line rewrites as the control space `\ `.
    return raw == str(node) or (node.is_command(" ") and raw == "\\")


def copied_text_matches(view: ExpandedFile, node) -> bool:
    """An element copied in one piece from the source reads the same text there."""
    if isinstance(node, TexGroup) or node.is_par() or view.origin(node) is not None:
        return True
    offsets = view.source_map.target
    start, end = offsets.offset(node.start_position), offsets.offset(node.end_position)
    if end <= start or view.source_map.index(start) != view.source_map.index(end - 1):
        return True
    source_start, source_end = view.source_span(node)
    written = TexContent("", position=source_start, end_position=source_end, rootfile=view.source)
    return LINE_ENDING.sub("\n", view.source.raw_text(written)) == LINE_ENDING.sub("\n", view.raw_text(node))


def environments(tex: TexFile) -> Counter[str]:
    return Counter(node.name for node in walk(tex.container) if isinstance(node, TexGroup) and node.env)


def compiles(path: Path) -> bool:
    """A log more recent than the source, with no TeX error."""
    log = path.with_suffix(".log")
    if not log.is_file() or log.stat().st_mtime < path.stat().st_mtime:
        return False
    return TEX_ERROR.search(log.read_text(encoding="latin-1")) is None


class Tally:
    """Diagnostics counted by severity and code, with a few examples."""

    def __init__(self) -> None:
        self.kinds: Counter[tuple[int, str, str]] = Counter()
        self.examples: dict[tuple[int, str, str], list[str]] = defaultdict(list)
        self.files = 0

    def add(self, name: str, tex: TexFile, diagnostics) -> None:
        self.files += bool(diagnostics)
        for diagnostic in diagnostics:
            kind = (diagnostic.severity.rank, str(diagnostic.severity), diagnostic.code)
            self.kinds[kind] += 1
            if len(self.examples[kind]) < EXAMPLES:
                line = tex.lines[diagnostic.start[0] - 1].strip()[:100] if tex.lines else ""
                self.examples[kind].append(f"{name}:{diagnostic.start[0]} : {diagnostic.message} ── {line}")

    def show(self, title: str) -> None:
        print(f"\n{title}: {sum(self.kinds.values())} in {self.files} files")
        for kind in sorted(self.kinds):
            print(f"\n{self.kinds[kind]:5d}  {kind[1]} [{kind[2]}]")
            for item in self.examples[kind]:
                print(f"       {item}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("folder", type=Path)
    parser.add_argument("--inputs", action="store_true", help="read the definitions of the \\input files")
    parser.add_argument("--expand", action="store_true", help="expand the macros and check the view")
    options = parser.parse_args()
    folder = options.folder.expanduser()

    # A folder may be named `x.tex` too.
    paths = sorted(p for p in folder.rglob("*.tex") if p.is_file() and not IGNORED_PARTS & set(p.parts))
    failures: dict[str, list[str]] = defaultdict(list)
    source_tally, checked_tally = Tally(), Tally()
    characters, elapsed = 0, 0.0
    expanded_files, expansions, deepest, expand_elapsed = 0, 0, 0, 0.0
    gained: Counter[str] = Counter()
    decided: Counter[str] = Counter()
    compiling = 0
    compiling_errors: list[str] = []

    for path in paths:
        name = str(path.relative_to(folder))
        try:
            tex = TexFile(path)
            characters += sum(map(len, tex.lines))
            start = time.perf_counter()
            tex.analyse(follow_inputs=options.inputs)
            elapsed += time.perf_counter() - start
        except Exception as error:  # the corpus must read everything: record it and go on
            failures["exception"].append(f"{name} : {type(error).__name__} : {error}")
            continue
        reparsed = TexFile(tex.content().splitlines(keepends=True), name=name)
        # The same folder, so the same inclusions and the same definitions as the original.
        reparsed.src_file = path
        reparsed.analyse(follow_inputs=options.inputs)
        if shape(reparsed.container) != shape(tex.container):
            failures["rewriting"].append(name)
        wrong = next((node for node in walk(tex.container) if not rereads_source(node)), None)
        if wrong is not None:
            failures["raw_text"].append(f"{name} {wrong.start_position}")
        source_tally.add(name, tex, tex.diagnostics)
        if not options.expand:
            continue
        try:
            start = time.perf_counter()
            view = expand(tex)
            expand_elapsed += time.perf_counter() - start
            diagnostics = diagnose(view)
        except Exception as error:  # idem : consigner et continuer
            failures["expansion"].append(f"{name} : {type(error).__name__} : {error}")
            continue
        expanded_files += bool(view.expansions)
        expansions += len(view.expansions)
        decided.update(condition.test for condition in view.conditions)
        deepest = max([deepest, *(expansion.depth for expansion in view.expansions)])
        wrong = next((node for node in walk(view.container) if not copied_text_matches(view, node)), None)
        if wrong is not None:
            failures["correspondance"].append(f"{name} {wrong.start_position}")
        gained.update(environments(view) - environments(tex))
        checked_tally.add(name, tex, diagnostics)
        if compiles(path):
            compiling += 1
            compiling_errors.extend(f"{name}:{d}" for d in diagnostics if d.severity is Severity.ERROR)

    print(f"{len(paths)} files, {characters / 1e6:.1f} M characters, analysed in {elapsed:.2f} s")
    print(f"failures: { {kind: len(names) for kind, names in failures.items()} or 'none' }")
    for kind, names in failures.items():
        print(f"\n{kind}")
        for item in names[:EXAMPLES]:
            print(f"    {item}")
    source_tally.show("Diagnostics of the source")
    if not options.expand:
        return
    print(f"\nExpansion in {expand_elapsed:.2f} s: {expansions} uses in {expanded_files} files,")
    print(f"deepest nesting {deepest}")
    print(f"conditionals decided: {sum(decided.values())}, {dict(decided.most_common(12))}")
    print(f"environments revealed: {dict(gained.most_common(15))}")
    checked_tally.show("Diagnostics of check (expanded view)")
    print(f"\n{compiling} files compile (recent log with no error): {len(compiling_errors)} errors")
    for item in compiling_errors:
        print(f"    {item}")


if __name__ == "__main__":
    main()
