"""Compiles the core of latexdetok with mypyc, for the interpreter that runs this script.

    ~/.cache/latexdetok-mypyc/bin/python scripts/compile.py
    python3 scripts/compile.py --clean

The interpreter must have mypy and setuptools, and be of the same version as the
one that will run latexdetok: an extension only loads under the version that
built it. Without touching the system Python:

    uv venv --python python3 ~/.cache/latexdetok-mypyc
    uv pip install --python ~/.cache/latexdetok-mypyc/bin/python mypy setuptools

The sources are copied before compiling, into a folder named `latexdetok` so that
mypy names the modules `latexdetok.…`, and so that a change made during the
compilation does not end up in an extension claiming to match it. The
extensions replace `_mypyc/` all at once, manifest included (see `compilation`).
"""

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

# The sources are what compiles, whatever `_mypyc/` may hold.
os.environ["LATEXDETOK_PURE"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from latexdetok.compilation import BUILD, COMPILED_MODULES, GROUP, MANIFEST, PACKAGE, manifest


def compile_package() -> Path:
    try:
        from mypyc.build import mypycify
        from setuptools import setup
    except ImportError as error:
        sys.exit(f"{error.name} is missing from {sys.executable}: see the documentation of this script")

    with tempfile.TemporaryDirectory(prefix="latexdetok-mypyc-") as temporary:
        work = Path(temporary)
        sources = work / "latexdetok"
        sources.mkdir()
        for path in PACKAGE.glob("*.py"):
            shutil.copy2(path, sources / path.name)
        written = manifest(sources)

        previous = Path.cwd()
        os.chdir(work)
        try:
            extensions = mypycify(
                ["--ignore-missing-imports", *(f"latexdetok/{name}.py" for name in COMPILED_MODULES)],
                group_name=GROUP,
                target_dir="c",
            )
            setup(
                name="latexdetok-compiled",
                ext_modules=extensions,
                script_args=["build_ext", "--build-lib", "lib", "--build-temp", "temp", "--quiet"],
            )
        finally:
            os.chdir(previous)

        built = BUILD.with_name(BUILD.name + ".nouveau")
        shutil.rmtree(built, ignore_errors=True)
        built.mkdir()
        for extension in (work / "lib" / "latexdetok").iterdir():
            shutil.copy2(extension, built / extension.name)
        (built / MANIFEST).write_text(json.dumps(written, indent=1), encoding="utf-8")
        shutil.rmtree(BUILD, ignore_errors=True)
        built.rename(BUILD)
        if manifest(PACKAGE)["sources"] != written["sources"]:
            print("warning: the sources changed while compiling, so the build will not be used")
        return BUILD


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--clean", action="store_true", help="remove the compiled extensions")
    options = parser.parse_args()
    if options.clean:
        shutil.rmtree(BUILD, ignore_errors=True)
        print(f"{BUILD} removed")
        return
    build = compile_package()
    names = sorted(path.name for path in build.iterdir())
    print(f"{len(names) - 1} extensions in {build}, for Python {sys.version.split()[0]}")


if __name__ == "__main__":
    main()
