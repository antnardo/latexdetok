"""Measures the speed of the analysis and of the expansion, on a file or a folder.

On a file: the median of `--repeat` analyses (7 by default), in milliseconds and
in characters per second; with `--expand`, the median of the expansions. With
`--inputs`, the first analysis reads the inclusions and fills the cache of
`resolution`, the following ones replay it: it is given apart. `--profile` adds
the costliest functions (`cProfile`, own time), `--memory` the allocation peak
of the analysis (`tracemalloc`).

On a folder: one pass over every `.tex`, cache included, as the corpus does. The
processor time is given next to the elapsed time: on a busy machine, that is the
one to compare (without the calls to `kpsewhich`, which are mostly waiting).

    python3 scripts/bench.py cours.tex --profile
    python3 scripts/bench.py path/to/corpus --inputs --expand
"""

import argparse
import cProfile
import pstats
import statistics
import time
import tracemalloc
from collections.abc import Callable
from pathlib import Path

from latexdetok import COMPILED, TexFile, expand
from latexdetok.resolution import clear_caches

IGNORED_PARTS = {"backup", ".sauvegardes"}


def _analysed(path: Path, inputs: bool) -> TexFile:
    tex = TexFile(path)
    tex.analyse(follow_inputs=inputs)
    return tex


def _timed(action: Callable[[], object]) -> float:
    start = time.perf_counter()
    action()
    return time.perf_counter() - start


def _profile(action: Callable[[], object], count: int) -> None:
    profiler = cProfile.Profile()
    profiler.runcall(action)
    stats = pstats.Stats(profiler)
    stats.sort_stats("tottime").print_stats(count)


def _peak(action: Callable[[], object]) -> int:
    tracemalloc.start()
    try:
        action()
        return tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()


def bench_file(path: Path, options: argparse.Namespace) -> None:
    lines = TexFile(path).lines
    characters = sum(map(len, lines))
    print(f"{path.name}: {len(lines)} lines, {characters} characters")
    clear_caches()
    if options.inputs:
        cold = _timed(lambda: _analysed(path, True))
        print(f"first analysis, inclusions read: {cold * 1000:.0f} ms")
    durations = [_timed(lambda: _analysed(path, options.inputs)) for _ in range(options.repeat)]
    median = statistics.median(durations)
    print(f"analysis: {median * 1000:.1f} ms (median of {options.repeat}), {characters / median:,.0f} char/s")
    if options.expand:
        tex = _analysed(path, options.inputs)
        durations = [_timed(lambda: expand(tex)) for _ in range(options.repeat)]
        print(f"expansion: {statistics.median(durations) * 1000:.1f} ms")
    if options.memory:
        peak = _peak(lambda: _analysed(path, options.inputs))
        print(f"memory peak of the analysis: {peak / 1024:,.0f} KiB")
        if options.expand:
            tex = _analysed(path, options.inputs)
            print(f"memory peak of the expansion: {_peak(lambda: expand(tex)) / 1024:,.0f} KiB")
    if options.profile:
        _profile(lambda: _analysed(path, options.inputs), options.profile)
        if options.expand:
            tex = _analysed(path, options.inputs)
            _profile(lambda: expand(tex), options.profile)


def bench_folder(folder: Path, options: argparse.Namespace) -> None:
    paths = sorted(p for p in folder.rglob("*.tex") if p.is_file() and not IGNORED_PARTS & set(p.parts))
    clear_caches()
    analysed: list[TexFile] = []

    def analyse_all() -> None:
        analysed.extend(_analysed(path, options.inputs) for path in paths)

    characters = sum(sum(map(len, TexFile(path).lines)) for path in paths)
    cpu = time.process_time()
    elapsed = _timed(analyse_all)
    cpu = time.process_time() - cpu
    print(f"{len(paths)} files, {characters / 1e6:.1f} M characters")
    print(f"analysis: {elapsed:.2f} s ({cpu:.2f} s of processor), {characters / elapsed:,.0f} char/s")
    if options.expand:
        cpu = time.process_time()
        elapsed = _timed(lambda: [expand(tex) for tex in analysed])
        print(f"expansion: {elapsed:.2f} s ({time.process_time() - cpu:.2f} s of processor)")
    if options.profile:
        clear_caches()
        _profile(analyse_all, options.profile)
        if options.expand:
            _profile(lambda: [expand(tex) for tex in analysed], options.profile)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("target", type=Path, help="a .tex file, or a folder")
    parser.add_argument("--inputs", action="store_true", help="read the definitions of the loaded files")
    parser.add_argument("--expand", action="store_true", help="measure the expansion too")
    parser.add_argument("--repeat", type=int, default=7)
    parser.add_argument("--profile", type=int, nargs="?", const=10, default=0, help="functions to show")
    parser.add_argument("--memory", action="store_true")
    options = parser.parse_args()
    target = options.target.expanduser()
    print(f"latexdetok {'compiled' if COMPILED else 'from sources'}")
    if target.is_dir():
        bench_folder(target, options)
    else:
        bench_file(target, options)


if __name__ == "__main__":
    main()
