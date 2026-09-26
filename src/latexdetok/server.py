"""A language server: the diagnostics of the buffer, as it is being typed.

Why a server rather than a command. `latexdetok check -` answers in 14 to 76 ms
once the package is imported, but a fresh process costs some 600 ms: import,
then the first search through `kpsewhich`. Run on every keystroke that is
unusable, and run on save it is late. A server pays those 600 ms once and keeps
the answers warm, so the editor underlines while the sentence is still being
written.

Why nothing is translated here. A `TexDiagnostic` already carries what the
protocol asks for: a span with its two ends, a severity, a stable code, a
message, the places that explain it, and the fix. This module moves them
across, and that is all — the one real conversion is the column, counted here
in characters and there in UTF-16 code units, which differ on anything outside
the basic plane.

What the editor holds is a buffer, not a file: `TexFile(lines, path=…)` reads
the lines given and only uses the path to find the neighbours (`\\input`,
`\\usepackage`, `% !TEX root`). A document being typed is wrong most of the
time — half-written commands, environments not yet closed — which is exactly
what the tolerance of the package is for: it never refuses, it reports.

Settings arrive in `initializationOptions`: `language` (`en`, `fr`),
`followInputs` and `expand`, which turn off the two readings that cost.

    latexdetok-lsp            # speaks the protocol on the standard input

`pip install latexdetok[lsp]` brings `pygls`, the only dependency the package
has ever taken, and only for this module: the core stays on the standard
library, and whoever does not want a server does not carry it.
"""

import asyncio
import sys
import traceback
from pathlib import Path

try:
    from lsprotocol import types
    from pygls import uris
    from pygls.lsp.server import LanguageServer
except ImportError as error:  # pragma: no cover - the message is what is tested
    # An entry point cannot depend on an extra: `pip install latexdetok` installs
    # `latexdetok-lsp` all the same, and an editor that finds it on the PATH would
    # otherwise show a traceback about a module nobody asked for.
    print(
        f"latexdetok: the language server needs pygls ({error.name} is missing):"
        ' pip install "latexdetok[lsp]"',
        file=sys.stderr,
    )
    raise SystemExit(1) from error

from latexdetok import __version__
from latexdetok.analyse import TexFile
from latexdetok.checks import check
from latexdetok.diagnostics import Severity, TexDiagnostic
from latexdetok.messages import set_language
from latexdetok.resolution import split_lines

__all__ = ["SEVERITIES", "diagnose", "main", "server"]

# The language server protocol has four severities, the package three.
SEVERITIES = {
    Severity.ERROR: types.DiagnosticSeverity.Error,
    Severity.WARNING: types.DiagnosticSeverity.Warning,
    Severity.INFO: types.DiagnosticSeverity.Information,
}
SOURCE = "latexdetok"
# Long enough that a burst of keystrokes is one analysis, short enough to feel immediate.
DEBOUNCE = 0.3


class _Settings:
    """What `initializationOptions` may change; the defaults are those of `check`."""

    def __init__(self) -> None:
        self.follow_inputs = True
        self.expand = True

    def read(self, options: object) -> None:
        if not isinstance(options, dict):
            return
        language = options.get("language")
        if isinstance(language, str):
            set_language(language)
        self.follow_inputs = bool(options.get("followInputs", self.follow_inputs))
        self.expand = bool(options.get("expand", self.expand))


settings = _Settings()
server = LanguageServer(SOURCE, __version__, text_document_sync_kind=types.TextDocumentSyncKind.Incremental)
_pending: dict[str, asyncio.Task[None]] = {}


def _utf16(line: str, column: int) -> int:
    """The column in UTF-16 code units, which is how the protocol counts by default.

    A character outside the basic plane — an emoji, a `𝔸` — takes two units and
    one character: without this, everything after it on the line is off by one.
    """
    return len(line[:column].encode("utf-16-le")) // 2


def _range(start: tuple[int, int], end: tuple[int, int], lines: list[str]) -> types.Range:
    """A span of the source, `(line from 1, column from 0)`, as the protocol writes it."""

    def position(line: int, column: int) -> types.Position:
        text = lines[line - 1] if 1 <= line <= len(lines) else ""
        return types.Position(line=line - 1, character=_utf16(text, column))

    return types.Range(start=position(*start), end=position(*end))


def _diagnostic(found: TexDiagnostic, uri: str, lines: list[str]) -> types.Diagnostic:
    related = [
        types.DiagnosticRelatedInformation(
            location=types.Location(uri=uri, range=_range(place.start, place.end, lines)),
            message=place.message,
        )
        for place in found.related
    ]
    # The protocol has no field for a fix that is not an edit: it goes to the
    # second line of the message, where the editor shows it on hover.
    message = f"{found.message}\n{found.suggestion}" if found.suggestion else found.message
    return types.Diagnostic(
        range=_range(found.start, found.end, lines),
        message=message,
        severity=SEVERITIES[found.severity],
        code=found.code,
        source=SOURCE,
        related_information=related or None,
    )


def diagnose(text: str, uri: str) -> list[types.Diagnostic]:
    """The diagnostics of a buffer, ready to be published.

    `uri` says where the document lives: its folder is where the inclusions are
    looked for. An untitled buffer has none, and is read on its own.
    """
    lines = split_lines(text)
    path = uris.to_fs_path(uri)
    tex = TexFile(lines, path=Path(path) if path else None)
    tex.analyse(follow_inputs=settings.follow_inputs)
    found = check(tex, follow_inputs=settings.follow_inputs, expanded=settings.expand)
    return [_diagnostic(diagnostic, uri, lines) for diagnostic in found]


def _publish(uri: str, diagnostics: list[types.Diagnostic]) -> None:
    server.text_document_publish_diagnostics(types.PublishDiagnosticsParams(uri=uri, diagnostics=diagnostics))


async def _analyse(uri: str, delay: float) -> None:
    """Analyse after `delay`, unless another keystroke replaces this task first."""
    try:
        if delay:
            await asyncio.sleep(delay)
        document = server.workspace.get_text_document(uri)
        _publish(uri, await asyncio.to_thread(diagnose, document.source, uri))
    except asyncio.CancelledError:
        raise
    except Exception:  # a server that dies stops underlining anything at all
        # With the traceback: a server that swallows the reason is a server nobody can fix.
        server.window_log_message(
            types.LogMessageParams(
                type=types.MessageType.Error,
                message=f"latexdetok: {uri}\n{traceback.format_exc()}",
            )
        )
    finally:
        _pending.pop(uri, None)


def _schedule(uri: str, delay: float = DEBOUNCE) -> None:
    waiting = _pending.pop(uri, None)
    if waiting is not None:
        waiting.cancel()
    _pending[uri] = asyncio.get_event_loop().create_task(_analyse(uri, delay))


@server.feature(types.INITIALIZE)
def _initialize(params: types.InitializeParams) -> None:
    settings.read(params.initialization_options)


@server.feature(types.TEXT_DOCUMENT_DID_OPEN)
def _opened(params: types.DidOpenTextDocumentParams) -> None:
    _schedule(params.text_document.uri, delay=0)


@server.feature(types.TEXT_DOCUMENT_DID_CHANGE)
def _changed(params: types.DidChangeTextDocumentParams) -> None:
    _schedule(params.text_document.uri)


@server.feature(types.TEXT_DOCUMENT_DID_SAVE)
def _saved(params: types.DidSaveTextDocumentParams) -> None:
    # Saved, the neighbours may have changed too: read them again.
    _schedule(params.text_document.uri, delay=0)


@server.feature(types.TEXT_DOCUMENT_DID_CLOSE)
def _closed(params: types.DidCloseTextDocumentParams) -> None:
    waiting = _pending.pop(params.text_document.uri, None)
    if waiting is not None:
        waiting.cancel()
    _publish(params.text_document.uri, [])


def main() -> None:
    """Speak the protocol on the standard input, which is how an editor starts a server."""
    server.start_io()


if __name__ == "__main__":
    main()
