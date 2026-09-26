"""Finding and reading the files a document loads, the way TeX would.

Searching. TeX looks first in the build folder, then in the texmf trees
(`TEXINPUTS`). The folder is looked at directly; the trees through `kpsewhich`,
which knows the configuration of the installation better than we do.
`\\input{x}` tries `x.tex` then `x`, `\\usepackage{x}` looks for `x.sty`,
`\\documentclass{x}` looks for `x.cls`.

What is read. The files of the distribution (`TEXMFDIST`) are found but not
read: the “kernel only” decision. The user's trees (`TEXMFHOME`, `TEXMFLOCAL`)
and the document's folder are read, because that is where their macros live.

Reading them was tried, on 16 September 2026, and measured on a slide course:
the analysis goes from 0.4 s to 9.4 s, the registry gains 3,500 invented names
(`+`, `0`, `##1`, pieces of expl3 read askew) and **loses 48 kernel
signatures**, among them `\\def`, `\\gdef` and `\\edef`, which some package
redefines in a form we cannot read. The commands that matter stay unknown
(`\\includegraphics`, `\\textcolor`, `\\SI`) or wrong (`\\dfrac` with no
argument). The packages that are worth it declare their interface through code
(`\\@ifnextchar`, expl3, `\\DeclareMathSymbol`), not through a readable
declaration: their signatures are written by hand in `data/packages.txt`.

Cost. One call to `kpsewhich` costs 90 ms, nearly all of it start-up (reading
the configuration and the `ls-R` databases): the hundred searches of one course
made up 90 % of its analysis. One single `kpsewhich -interactive` therefore
stays open per process, and answers in a millisecond or two; kpathsea itself
does the searching, not an imitation of its rules (order of the trees, `!!`,
`//`, suffixes), which would drift. It only writes its answer to a terminal,
hence the pseudo-terminal, and says nothing about a file it cannot find, hence
the sentinel asked for after every name. Without a pseudo-terminal (Windows),
or if it stops answering, we fall back to one call per search.

A course document also re-reads the same 16,000 lines of personal macros.
Everything is cached for the process: every search once (per `TEXMFHOME`
setting), and for every file read, the sequence of definitions it produced,
replayed as long as the file has not changed and the booleans of `\\newif`,
which it may test, have the same values.

Master document. A chapter (`% !TEX root = ./EM.tex`) has no preamble: its
macros come from the document that includes it. `root_of` reads that
declaration, and the master's preamble is read before the chapter
(`preamble_only`), without its other chapters; since the build runs from the
master, inclusions are then looked for in its folder.

Catcodes. A `.sty` or a `.cls` is read with `@` as a letter, and what it changes
in the categories does not leave its own reading (LaTeX restores `@` after a
package). An included `.tex` is read with the table in force where it is
included, and the table it leaves applies to the includer: `\\input` opens no
group.

Encoding. `read_lines` reads UTF-8, else the Latin-1 of old sources, which
reads any byte; the encoding it gives back is the one that writes the file
identically. Python's `utf-8-sig` also reads a file with no byte order mark, and
writing with it adds the three bytes of one: the mark is therefore looked for,
and a file without one says `utf-8`.
"""

import atexit
import codecs
import os
import select
import shutil
import subprocess
from collections.abc import Sequence
from pathlib import Path

try:
    import pty
    import termios
except ImportError:  # Windows: no pseudo-terminal, one call per search
    pty = termios = None  # type: ignore[assignment]

from latexdetok.catcodes import CatcodeTable, Category
from latexdetok.characters import TEX_ROOT, TEX_ROOT_LINES
from latexdetok.logger import logger
from latexdetok.parser import TexParser
from latexdetok.signatures import JournalEntry, SignatureRegistry

__all__ = ["FALLBACK_ENCODINGS", "TexmfResolver", "clear_caches", "read_lines", "root_of"]

FALLBACK_ENCODINGS = ("utf-8", "latin-1")
# The two spellings of UTF-8 that `codecs` knows; which one a file gets is its byte order mark's call.
UTF8_CODECS = frozenset({"utf-8", "utf-8-sig"})
BYTE_ORDER_MARK = "\ufeff"
DISTRIBUTION_VARIABLES = ("TEXMFDIST", "TEXMFMAIN")
# Settings that change what kpsewhich finds: the cache key depends on them.
KPATHSEA_ENVIRONMENT = ("TEXMFHOME", "TEXMFLOCAL", "TEXINPUTS", "TEXMFCNF")

_found: dict[tuple[str, tuple[str | None, ...]], Path | None] = {}
_distribution: dict[tuple[str | None, ...], tuple[Path, ...]] = {}

# (file, date, size, starting table, preamble only, booleans) → (definitions produced, table left).
# The booleans are part of it: `\ifprof \newcommand…\fi` does not learn the same thing depending on `prof`.
DefinitionsKey = tuple[Path, int, int, CatcodeTable, bool, frozenset[tuple[str, bool | None]]]
_definitions: dict[DefinitionsKey, tuple[tuple[JournalEntry, ...], CatcodeTable]] = {}


def read_lines(path: Path, encoding: str | None = None) -> tuple[str, list[str]]:
    """The lines of the file, line endings included, and the encoding that writes them back.

    With no encoding given: UTF-8, then the Latin-1 of old sources, which reads
    any byte at all and makes a safe fallback. A UTF-8 file, found or forced,
    says `utf-8-sig` if it starts with a byte order mark, which is then not part
    of the first line, and `utf-8` otherwise.
    """
    encodings = (encoding,) if encoding else FALLBACK_ENCODINGS
    for candidate in encodings:
        utf8 = codecs.lookup(candidate).name in UTF8_CODECS
        try:
            with path.open(encoding="utf-8" if utf8 else candidate) as file:
                lines = file.readlines()
        except UnicodeDecodeError:
            if candidate == encodings[-1]:
                raise
            continue
        if not utf8:
            return candidate, lines
        if not lines or not lines[0].startswith(BYTE_ORDER_MARK):
            return "utf-8", lines
        lines[0] = lines[0].removeprefix(BYTE_ORDER_MARK)
        return "utf-8-sig", lines if lines[0] else lines[1:]
    raise AssertionError("unreachable: the last encoding either raises or returns")


def root_of(path: Path, lines: Sequence[str]) -> Path | None:
    """The master document declared by `% !TEX root = …`, if it exists and is not the file itself."""
    for line in lines[:TEX_ROOT_LINES]:
        match = TEX_ROOT.match(line)
        if match is not None:
            root = (path.parent / match["root"]).resolve()
            return root if root.is_file() and root != path.resolve() else None
    return None


def clear_caches() -> None:
    """Forget cached searches and definitions (files changed, tests)."""
    _found.clear()
    _distribution.clear()
    _definitions.clear()
    _close_sessions()


def _environment_key() -> tuple[str | None, ...]:
    return tuple(os.environ.get(variable) for variable in KPATHSEA_ENVIRONMENT)


def _kpsewhich(*arguments: str) -> str | None:
    executable = shutil.which("kpsewhich")
    if executable is None:
        return None
    if len(arguments) == 2 and arguments[0] == "-must-exist":
        answered, found = _session_find(executable, arguments[1])
        if answered:
            return found
    # kpsewhich also looks in “.”. Started from the root, it can find nothing there:
    # the build folder is looked at by `TexmfResolver.find`, and Python's current
    # folder has nothing to do with the document.
    result = subprocess.run([executable, *arguments], capture_output=True, text=True, check=False, cwd="/")
    output = result.stdout.strip()
    return output or None


# A path that exists and that no document asks for: its answer closes the one for the name asked.
SENTINEL = str(Path(__file__).resolve())
SESSION_TIMEOUT = 10.0


class _Session:
    """One `kpsewhich -interactive` kept open, answering one line per name found.

    Its `stdout` is a pseudo-terminal: on a pipe, the C library would buffer the
    answers until the program exits.
    """

    def __init__(self, executable: str) -> None:
        master, slave = pty.openpty()
        attributes = termios.tcgetattr(slave)
        attributes[1] &= ~termios.ONLCR  # “\n”, not “\r\n”
        termios.tcsetattr(slave, termios.TCSANOW, attributes)
        try:
            self._process = subprocess.Popen(
                [executable, "-interactive", "-must-exist", SENTINEL],
                stdin=subprocess.PIPE,
                stdout=slave,
                stderr=subprocess.DEVNULL,
                cwd="/",  # as in `_kpsewhich`: “.” must find nothing
            )
        finally:
            os.close(slave)
        self._master = master
        self._buffer = b""
        try:
            if self._readline() != SENTINEL:
                raise OSError("interactive kpsewhich: unexpected answer")
        except BaseException:
            self.close()
            raise

    def find(self, filename: str) -> str | None:
        assert self._process.stdin is not None
        self._process.stdin.write(os.fsencode(f"{filename}\n{SENTINEL}\n"))
        self._process.stdin.flush()
        line = self._readline()
        if line == SENTINEL:
            return None
        if self._readline() != SENTINEL:
            raise OSError("interactive kpsewhich: unexpected answer")
        return line

    def _readline(self) -> str:
        while b"\n" not in self._buffer:
            ready, _, _ = select.select([self._master], [], [], SESSION_TIMEOUT)
            if not ready:
                raise TimeoutError("interactive kpsewhich does not answer")
            chunk = os.read(self._master, 65536)
            if not chunk:
                raise EOFError("interactive kpsewhich has stopped")
            self._buffer += chunk
        line, self._buffer = self._buffer.split(b"\n", 1)
        return os.fsdecode(line)

    def close(self) -> None:
        if self._process.stdin is not None:
            self._process.stdin.close()
        self._process.kill()
        self._process.wait()
        os.close(self._master)


# Per kpathsea setting, the open session; None if it failed, and we do not go back to it.
_sessions: dict[tuple[str | None, ...], _Session | None] = {}


def _session_find(executable: str, filename: str) -> tuple[bool, str | None]:
    """`(True, path)`, `(True, None)` if not found, `(False, None)` if the session does not answer."""
    # An empty line ends interactive mode; a name starting with “-” would be an option for the plain call.
    if pty is None or not filename.strip() or filename.startswith("-") or "\n" in filename:
        return False, None
    key = _environment_key()
    if key not in _sessions:
        try:
            _sessions[key] = _Session(executable)
        except (OSError, EOFError) as error:
            logger.debug("kpsewhich interactif indisponible : %s", error)
            _sessions[key] = None
    session = _sessions[key]
    if session is None:
        return False, None
    try:
        return True, session.find(filename)
    except (OSError, EOFError) as error:
        logger.debug("interactive kpsewhich given up: %s", error)
        session.close()
        _sessions[key] = None
        return False, None


@atexit.register
def _close_sessions() -> None:
    for session in _sessions.values():
        if session is not None:
            session.close()
    _sessions.clear()


def find_in_texmf(filename: str) -> Path | None:
    """The path kpsewhich gives for `filename`, or None (not found, or no TeX)."""
    key = (filename, _environment_key())
    if key not in _found:
        output = _kpsewhich("-must-exist", filename)
        _found[key] = Path(output.splitlines()[0]) if output else None
    return _found[key]


def distribution_roots() -> tuple[Path, ...]:
    key = _environment_key()
    if key not in _distribution:
        roots = {_kpsewhich("-var-value", variable) for variable in DISTRIBUTION_VARIABLES}
        _distribution[key] = tuple(Path(root).resolve() for root in roots if root)
    return _distribution[key]


class TexmfResolver:
    """Reads, for one analysis, the definitions of the files a document loads.

    `folder` is the build folder: that of the main document; `main` is the
    document itself, never re-read if it includes itself. The files of the
    distribution are found but not read (see the module header). A file already
    read is replayed from the cache, which keeps the log of whoever includes it
    complete; a file being read that includes itself again is ignored, and
    nothing around it is cached then.
    """

    def __init__(self, folder: Path, main: Path | None = None) -> None:
        self._folder = folder
        self._loading: list[Path] = [main.resolve()] if main is not None else []
        self._incomplete = False  # a circular inclusion was ignored during the reading under way

    def find(self, name: str, extension: str) -> Path | None:
        candidates = [name] if name.endswith(f".{extension}") else [f"{name}.{extension}"]
        if extension == "tex" and candidates[0] != name:
            candidates.append(name)  # `\input{notes.aux}`: the name as it stands
        for candidate in candidates:
            local = self._folder / candidate
            if local.is_file():
                return local
        if Path(name).is_absolute() or name.startswith(("./", "../")):
            # An explicit path: TeX only looks for it from the build folder, already seen.
            return None
        for candidate in candidates:
            found = find_in_texmf(candidate)
            if found is not None:
                return found
        return None

    def include(
        self,
        registry: SignatureRegistry,
        name: str,
        extension: str,
        catcodes: CatcodeTable,
        preamble_only: bool = False,
    ) -> CatcodeTable | None:
        """Learn the definitions of the file; return the table it leaves if it is a `.tex`.

        `preamble_only` stops at `\\begin{document}`: that is how a master document
        is read for its chapters.
        """
        path = self.find(name, extension)
        if path is None:
            logger.debug("INTROUVABLE %s (%s)", name, extension)
            return None
        resolved = path.resolve()
        if any(resolved.is_relative_to(root) for root in distribution_roots()):
            return None  # the distribution is found, not read
        if resolved in self._loading:
            self._incomplete = True
            return None
        package = extension in ("sty", "cls")
        if package:
            catcodes = catcodes.with_categories({"@": Category.LETTER})
        stat = resolved.stat()
        key = (resolved, stat.st_mtime_ns, stat.st_size, catcodes, preamble_only, registry.booleans())
        cached = _definitions.get(key)
        if cached is None:
            cached = self._read(registry, resolved, catcodes, key, preamble_only)
            if cached is None:
                return None
        else:
            registry.replay(cached[0])
        return None if package else cached[1]

    def _read(
        self,
        registry: SignatureRegistry,
        path: Path,
        catcodes: CatcodeTable,
        key: DefinitionsKey,
        preamble_only: bool = False,
    ) -> tuple[tuple[JournalEntry, ...], CatcodeTable] | None:
        logger.debug("DEFINITIONS read in %s", path)
        outer_incomplete, self._incomplete = self._incomplete, False
        self._loading.append(path)
        try:
            with registry.record() as journal:
                lines = read_lines(path)[1]
                parser = TexParser(
                    lines,
                    name=str(path),
                    signatures=registry,
                    resolver=self,
                    catcodes=catcodes,
                    preamble_only=preamble_only,
                )
                parser.parse()
        except OSError as error:
            logger.warning("illisible : %s (%s)", path, error)
            return None
        finally:
            self._loading.pop()
        result = (tuple(journal), parser.catcodes)
        if not self._incomplete:
            # A reading that skipped a circular inclusion depends on who started it.
            _definitions[key] = result
        self._incomplete = outer_incomplete or self._incomplete
        return result
