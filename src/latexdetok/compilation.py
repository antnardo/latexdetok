"""Modules compiled by mypyc: load them instead of the sources, but only if they match.

Why compile. The tokeniser and the expansion are Python loops over typed nodes;
mypyc turns them into C from the very annotations mypy already checks. On a
1000-line course, the analysis goes from 32 to 13 ms and the expansion from 440
to 215 ms, without changing one line of the sources.

Why apart. An extension wins over the source of the same name: compiled next to
`parser.py`, it would keep running after `parser.py` changed, with nothing to
say so, tests included. The extensions therefore live in `_mypyc/`, with a
manifest: the extension suffix of the interpreter that built them, and a digest
of every source in the package. When the package is imported, if the manifest
matches and every extension is there, `_mypyc/` goes to the front of the
package path; otherwise the sources are used, silently. One modified source is
enough to fall back to the sources until the next build
(`scripts/compile.py`).

`LATEXDETOK_PURE=1` forces the sources. `COMPILED` says what was loaded.

What changes in compiled mode, and what the sources put up with: a method of a
compiled class can no longer be replaced at run time, a compiled class cannot be
subclassed outside the package, and an attribute takes a value of the annotated
type or raises `TypeError` (a position is a tuple, not a list).
"""

import hashlib
import json
import os
import sys
from collections.abc import MutableSequence
from importlib.machinery import EXTENSION_SUFFIXES
from pathlib import Path

__all__ = [
    "BUILD",
    "COMPILED",
    "COMPILED_MODULES",
    "ENVIRONMENT",
    "GROUP",
    "PACKAGE",
    "manifest",
    "sources_digest",
    "use_compiled",
]

PACKAGE = Path(__file__).parent
BUILD = PACKAGE / "_mypyc"
MANIFEST = "manifest.json"
ENVIRONMENT = "LATEXDETOK_PURE"
# Name of the shared library of the compiled modules: `latexdetok.compiled__mypyc`, inside the package.
GROUP = "latexdetok.compiled"
# What runs on every analysis and every expansion pass. `resolution` stays Python:
# it starts processes, and its tests replace its functions at run time.
COMPILED_MODULES = (
    "analyse",
    "binding",
    "catcodes",
    "characters",
    "checks",
    "classes",
    "conditions",
    "definitions",
    "diagnostics",
    "expansion",
    "parser",
    "semantics",
    "signatures",
)


def sources_digest(package: Path = PACKAGE) -> str:
    """Digest of the package sources: a build is only good for the sources it came from."""
    digest = hashlib.sha256()
    for path in sorted(package.glob("*.py")):
        digest.update(path.name.encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def manifest(package: Path = PACKAGE) -> dict[str, object]:
    """What `scripts/compile.py` writes next to the extensions, for the sources at hand."""
    return {
        "suffix": EXTENSION_SUFFIXES[0],
        "sources": sources_digest(package),
        "modules": list(COMPILED_MODULES),
        "python": sys.version.split()[0],
    }


def use_compiled(package_path: MutableSequence[str], package: Path = PACKAGE, build: Path = BUILD) -> bool:
    """Put `build` at the front of `package_path` if its extensions match the sources; say whether it did."""
    if os.environ.get(ENVIRONMENT):
        return False
    try:
        written = json.loads((build / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    suffix = EXTENSION_SUFFIXES[0]
    if not isinstance(written, dict) or written.get("suffix") != suffix:
        return False
    if written.get("modules") != list(COMPILED_MODULES):
        return False
    names = [*COMPILED_MODULES, GROUP.rsplit(".", 1)[1] + "__mypyc"]
    if not all((build / f"{name}{suffix}").is_file() for name in names):
        return False
    # The digest last: it is the only check that reads the sources.
    if written.get("sources") != sources_digest(package):
        return False
    if str(build) not in package_path:
        package_path.insert(0, str(build))
    return True


COMPILED = use_compiled(sys.modules[__name__.rsplit(".", 1)[0]].__path__)
