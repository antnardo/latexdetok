"""The language server: a buffer in, diagnostics the protocol understands out."""

import json
import os
import subprocess
import sys

import pytest
from lsprotocol import types

from latexdetok.diagnostics import Severity
from latexdetok.server import SEVERITIES, diagnose, server, settings

FAUTIF = "\\documentclass{article}\n\\begin{document}\nSoit $x\n\nla suite.\n\\end{document}\n"


@pytest.fixture(autouse=True)
def _reglages_par_defaut():
    settings.follow_inputs, settings.expand = True, True
    yield
    settings.follow_inputs, settings.expand = True, True


def uri_of(path):
    return path.as_uri()


class TestDiagnose:
    def test_the_span_starts_at_zero_as_the_protocol_counts(self, tmp_path):
        (found,) = diagnose(FAUTIF, uri_of(tmp_path / "cours.tex"))
        # `Soit $x`: line 3 and column 5 for the package, line 2 and character 5 here.
        assert (found.range.start.line, found.range.start.character) == (2, 5)
        assert (found.range.end.line, found.range.end.character) == (2, 6)

    def test_the_code_and_the_source(self, tmp_path):
        (found,) = diagnose(FAUTIF, uri_of(tmp_path / "cours.tex"))
        assert (found.code, found.source, found.severity) == (
            "unclosed-math",
            "latexdetok",
            types.DiagnosticSeverity.Error,
        )

    def test_the_fix_is_the_second_line_of_the_message(self, tmp_path):
        (found,) = diagnose(FAUTIF, uri_of(tmp_path / "cours.tex"))
        assert found.message.splitlines() == [
            "“$” never closed",
            "close it with “$” before the blank line",
        ]

    def test_a_related_place_points_into_the_same_document(self, tmp_path):
        uri = uri_of(tmp_path / "cours.tex")
        (found,) = diagnose(FAUTIF, uri)
        (place,) = found.related_information
        assert (place.location.uri, place.location.range.start.line) == (uri, 3)

    def test_every_severity_is_mapped(self):
        assert set(SEVERITIES) == set(Severity)

    def test_a_clean_document_says_nothing(self, tmp_path):
        source = "\\documentclass{article}\n\\begin{document}\nSoit $x$.\n\\end{document}\n"
        assert diagnose(source, uri_of(tmp_path / "cours.tex")) == []

    def test_a_buffer_with_no_file_behind_it(self):
        # An untitled document: no folder, so nothing to look for beside it, but it still reads.
        (found,) = diagnose(FAUTIF, "untitled:Untitled-1")
        assert found.code == "unclosed-math"

    def test_the_inclusions_come_from_the_folder_of_the_document(self, tmp_path):
        (tmp_path / "defs.tex").write_text("\\newcommand{\\beq}{\\begin{equation}}\n", encoding="utf-8")
        source = "\\input{defs}\n\\begin{document}\n\\beq x\\end{equation}\n\\end{document}\n"
        # Without the definition read next door, `\end{equation}` would close nothing.
        assert diagnose(source, uri_of(tmp_path / "cours.tex")) == []

    def test_the_settings_turn_the_readings_off(self, tmp_path):
        (tmp_path / "defs.tex").write_text("\\newcommand{\\beq}{\\begin{equation}}\n", encoding="utf-8")
        source = "\\input{defs}\n\\begin{document}\n\\beq x\\end{equation}\n\\end{document}\n"
        settings.follow_inputs = False
        (found,) = diagnose(source, uri_of(tmp_path / "cours.tex"))
        assert found.code == "end-without-begin"

    def test_the_line_endings_of_the_buffer_do_not_shift_the_lines(self, tmp_path):
        (found,) = diagnose(FAUTIF.replace("\n", "\r\n"), uri_of(tmp_path / "cours.tex"))
        assert found.range.start.line == 2

    def test_a_form_feed_does_not_cut_a_line(self, tmp_path):
        # `str.splitlines` would cut there and report the error one line too far.
        source = "\\begin{document}\na\x0cb\nSoit $x\n\n.\n\\end{document}\n"
        (found,) = diagnose(source, uri_of(tmp_path / "cours.tex"))
        assert found.range.start.line == 2


class TestColumns:
    """The protocol counts in UTF-16 code units, the package in characters."""

    @pytest.mark.parametrize(
        ("before", "expected"),
        [
            ("", 5),
            ("é", 6),  # one character, one unit
            ("𝔸", 7),  # one character, two units
            ("𝔸𝔸", 9),
        ],
    )
    def test_a_column_after_a_wide_character(self, tmp_path, before, expected):
        source = f"\\begin{{document}}\n{before}Soit $x\n\n.\n\\end{{document}}\n"
        (found,) = diagnose(source, uri_of(tmp_path / "cours.tex"))
        assert found.range.start.character == expected


def test_the_features_the_editor_talks_to_are_registered():
    # Renaming a handler without its decorator would silently stop the underlining.
    assert set(server.protocol.fm.features) >= {
        "initialize",
        "textDocument/didOpen",
        "textDocument/didChange",
        "textDocument/didSave",
        "textDocument/didClose",
    }


class TestSettings:
    def test_the_options_of_the_editor_are_read(self):
        settings.read({"followInputs": False, "expand": False})
        assert (settings.follow_inputs, settings.expand) == (False, False)

    def test_no_options_at_all(self):
        settings.read(None)
        assert (settings.follow_inputs, settings.expand) == (True, True)

    def test_the_language_of_the_messages(self, tmp_path):
        settings.read({"language": "fr"})
        (found,) = diagnose(FAUTIF, uri_of(tmp_path / "cours.tex"))
        assert found.message.splitlines()[0] == "« $ » jamais fermé"


class TestOverTheProtocol:
    """The server as an editor sees it: a process, the protocol on its standard input."""

    @staticmethod
    def send(process, message):
        body = json.dumps(message).encode()
        process.stdin.write(f"Content-Length: {len(body)}\r\n\r\n".encode() + body)
        process.stdin.flush()

    @staticmethod
    def receive(process):
        """The next message; the header gives the length, which is how the protocol frames."""
        length = 0
        while line := process.stdout.readline():
            if line in (b"\r\n", b"\n"):
                break
            name, _, value = line.decode().partition(":")
            if name.strip().lower() == "content-length":
                length = int(value)
        return json.loads(process.stdout.read(length))

    def test_a_document_opened_is_a_document_underlined(self, tmp_path):
        path = tmp_path / "cours.tex"
        uri = path.as_uri()
        with subprocess.Popen(
            [sys.executable, "-m", "latexdetok.server"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            env={**os.environ, "LATEXDETOK_LANG": "en"},
        ) as process:
            try:
                self.send(
                    process,
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": {"processId": None, "rootUri": None, "capabilities": {}},
                    },
                )
                self.receive(process)  # the answer to `initialize`
                self.send(process, {"jsonrpc": "2.0", "method": "initialized", "params": {}})
                self.send(
                    process,
                    {
                        "jsonrpc": "2.0",
                        "method": "textDocument/didOpen",
                        "params": {
                            "textDocument": {"uri": uri, "languageId": "latex", "version": 1, "text": FAUTIF}
                        },
                    },
                )
                while (message := self.receive(process))["method"] != "textDocument/publishDiagnostics":
                    pass
            finally:
                process.stdin.close()
                process.kill()
        (found,) = message["params"]["diagnostics"]
        assert (message["params"]["uri"], found["code"], found["range"]["start"]) == (
            uri,
            "unclosed-math",
            {"line": 2, "character": 5},
        )


def test_without_pygls_the_command_says_what_to_install():
    # `pip install latexdetok` installs `latexdetok-lsp` all the same: an entry point
    # cannot depend on an extra. Found on the PATH, it must say so, not show a traceback.
    blocked = "import sys; sys.modules['lsprotocol'] = None; import latexdetok.server"
    done = subprocess.run([sys.executable, "-c", blocked], capture_output=True, text=True)
    assert (done.returncode, 'pip install "latexdetok[lsp]"' in done.stderr) == (1, True)
