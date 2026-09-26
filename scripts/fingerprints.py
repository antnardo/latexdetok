"""A fingerprint of everything the analysis of a folder of `.tex` produces, to compare two versions.

The “speed” milestone rewrites the hot loop: it must change nothing in what that
loop produces. `corpus.py` checks properties (rewriting, re-reading the source);
here, we compare everything, file by file: every node (type, content, positions,
bound arguments, role and value of a conditional, signature, macro), the catcode
changes, the registry learned, and with `--expand` the text of the view, its
expansions, its conditionals, its pieces and its tree. The diagnostics have their
own fingerprint: a change of message does not hide an identical tree.

    python3 scripts/fingerprints.py path/to/corpus --inputs --expand -o before.json
    python3 scripts/fingerprints.py path/to/corpus --inputs --expand --compare before.json

A file whose text changed between the two passes is counted apart.
"""

import argparse
import hashlib
import json
import sys
from pathlib import Path

from latexdetok import ExpandedFile, TexBranch, TexCommand, TexFile, TexGroup, expand
from latexdetok.classes import TexContainer

IGNORED_PARTS = {"backup", ".sauvegardes"}


def _walk(node: TexContainer):
    yield node
    if isinstance(node, TexGroup):
        for child in node.content:
            yield from _walk(child)


def _node(node: TexContainer) -> tuple:
    described: list = [type(node).__name__, node.start_position, node.end_position]
    if isinstance(node, TexGroup):
        described += [node.name, node.env, node.math, node.delimiter, node.inner_start, node.inner_end]
        described += [str(node.signature) if node.signature is not None else None]
        described += [node.macro.name if node.macro is not None else None]
        if isinstance(node, TexBranch):
            described += [node.taken, node.braced, node.condition.test, node.condition.start]
    else:
        described.append(node.content)
    if isinstance(node, TexCommand):
        described += [str(node.signature) if node.signature is not None else None, node.role, node.value]
        described += [node.macro.name if node.macro is not None else None]
    arguments = getattr(node, "arguments", None)
    if arguments is not None:
        described.append([None if a is None else (a.start_position, a.end_position) for a in arguments])
    return tuple(described)


def _file(tex: TexFile) -> list:
    registry = tex.signatures
    return [
        [_node(node) for node in _walk(tex.container)],
        [str(change) for change in tex.catcode_changes],
        str(tex.root),
        _registry(registry),
    ]


def _diagnostics(tex: TexFile) -> list:
    return [
        (d.code, str(d.severity), d.message, d.start, d.end, [(r.start, r.end, r.message) for r in d.related])
        for d in tex.diagnostics
    ]


def _queries(tex: TexFile) -> list:
    def spans(container) -> list:
        return [(node.start_position, node.end_position, str(node)) for node in container]

    return [
        spans(tex.get_sections()),
        spans(tex.get_envs("itemize")),
        spans(tex.get_envs("$")),
        spans(tex.get_commands_arguments(["section", "vect", "frac", "item", "begin"])),
        spans(tex.get_commands_arguments("frac", nargs=2)),
        spans(tex.get_graphics()),
        spans(tex.get_inputs()),
        spans(tex.get_preamble()),
        [spans(group) for group in tex.get_commands_to_next("section")],
    ]


def _registry(registry) -> list:
    tables = (
        registry._commands,
        registry._delegations,
        registry._macros,
        registry._environments,
        registry._environment_macros,
        registry._booleans,
    )
    return [sorted((name, repr(value)) for name, value in table.items()) for table in tables]


def _view(view: ExpandedFile) -> list:
    index = {id(expansion): i for i, expansion in enumerate(view.expansions)}
    return [
        view.source_map.target.text,
        [
            (e.name, e.start, e.end, e.environment, index.get(id(e.parent)) if e.parent else None)
            for e in view.expansions
        ],
        [(c.test, c.value, c.start, c.end) for c in view.conditions],
        [
            (
                p.start,
                p.end,
                p.source,
                index.get(id(p.expansion)) if p.expansion else None,
                repr(p.catcodes),
                [(s[0].start, s[0].test, s[1], s[2]) for s in p.branches],
                index.get(id(p.within)) if p.within else None,
            )
            for p in view.source_map.pieces
        ],
        _file(view),
    ]


def _digest(value: object) -> str:
    return hashlib.sha256(repr(value).encode()).hexdigest()[:16]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("folder", type=Path)
    parser.add_argument("--inputs", action="store_true")
    parser.add_argument("--expand", action="store_true")
    parser.add_argument("-o", "--output", type=Path, help="write the fingerprints as JSON")
    parser.add_argument("--compare", type=Path, help="compare with the fingerprints of a JSON file")
    options = parser.parse_args()
    folder = options.folder.expanduser()
    paths = sorted(p for p in folder.rglob("*.tex") if p.is_file() and not IGNORED_PARTS & set(p.parts))

    prints: dict[str, dict[str, str]] = {}
    for path in paths:
        name = str(path.relative_to(folder))
        tex = TexFile(path)
        tex.analyse(follow_inputs=options.inputs)
        prints[name] = {
            "texte": _digest(tex.lines),
            "source": _digest(_file(tex)),
            "str": _digest(tex.content()),
            "queries": _digest(_queries(tex)),
            "diagnostics": _digest(_diagnostics(tex)),
        }
        if options.expand:
            prints[name]["vue"] = _digest(_view(expand(tex)))

    if options.output is not None:
        options.output.write_text(json.dumps(prints, indent=1, ensure_ascii=False), encoding="utf-8")
        print(f"{len(prints)} files → {options.output}")
    if options.compare is not None:
        reference = json.loads(options.compare.read_text(encoding="utf-8"))
        differing = {
            name: sorted(key for key in digests if reference.get(name, {}).get(key) != digests[key])
            for name, digests in prints.items()
        }
        # The corpus lives: a file changed in the meantime is not a regression.
        edited = sorted(name for name, keys in differing.items() if "texte" in keys)
        differing = {name: keys for name, keys in differing.items() if keys and name not in edited}
        missing = sorted(set(reference) - set(prints))
        print(
            f"{len(prints)} files compared: {len(differing)} differ, "
            f"{len(edited)} changed in the meantime, {len(missing)} missing"
        )
        for name in edited:
            print(f"    changed: {name}")
        for name, keys in list(differing.items())[:20]:
            print(f"    {name} : {', '.join(keys)}")
        if differing or missing:
            sys.exit(1)


if __name__ == "__main__":
    main()
