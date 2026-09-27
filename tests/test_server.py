"""The language server: a buffer in, diagnostics the protocol understands out."""

import json
import os
import subprocess
import sys

import pytest
from lsprotocol import types

from latexdetok.diagnostics import Severity
from latexdetok.server import SEVERITIES, code_actions, diagnose, server, settings

FAUTIF = "\\documentclass{article}\n\\begin{document}\nSoit $x\n\nla suite.\n\\end{document}\n"
# `$` at the ninth character, after one wide in UTF-8 only (`É`, `é`) and one wide in both (`𝔸`).
ACCENTUE = "\\begin{document}\nÉté 𝔸 : $x\n\n.\n\\end{document}\n"
# The byte codec of each unit a client may count in, and its width.
UNITS = {"utf-8": ("utf-8", 1), "utf-16": ("utf-16-le", 2), "utf-32": ("utf-32-le", 4)}


@pytest.fixture(autouse=True)
def _reglages_par_defaut():
    settings.follow_inputs, settings.expand = True, True
    yield
    settings.follow_inputs, settings.expand = True, True


def uri_of(path):
    return path.as_uri()


def applied(line, edit, encoding):
    """The line once `edit` is applied by a client that counts in `encoding`.

    Cut in the middle of a character, the decoding raises: an edit counted in the
    wrong unit can do worse than land in the wrong place.
    """
    codec, width = UNITS[encoding]
    raw = line.encode(codec)
    start, end = edit.range.start.character * width, edit.range.end.character * width
    return (raw[:start] + edit.new_text.encode(codec) + raw[end:]).decode(codec)


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
    """The package counts in characters, the client in the unit it chose: UTF-16 unless it says so."""

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

    @pytest.mark.parametrize(
        ("encoding", "expected"),
        [
            ("utf-16", 9),  # VS Code, Sublime Text: `𝔸` counts two
            ("utf-8", 13),  # Neovim, Helix: `É` and `é` count two, `𝔸` four
            ("utf-32", 8),  # Emacs: one character, one unit
        ],
    )
    def test_a_column_in_the_unit_the_client_chose(self, tmp_path, encoding, expected):
        (found,) = diagnose(ACCENTUE, uri_of(tmp_path / "cours.tex"), encoding)
        assert found.range.start.character == expected


class TestQuickFixes:
    """The fix, offered as an edit the editor applies: ⌥⌘. on the place at fault."""

    @staticmethod
    def span(line, character=0):
        place = types.Position(line=line, character=character)
        return types.Range(start=place, end=place)

    def test_the_fix_is_offered_on_the_place_at_fault(self, tmp_path):
        uri = uri_of(tmp_path / "cours.tex")
        (action,) = code_actions(FAUTIF, uri, self.span(2, 5))
        assert (action.title, action.kind) == (
            "close it with “$” before the blank line",
            types.CodeActionKind.QuickFix,
        )

    def test_it_carries_the_edit_that_settles_it(self, tmp_path):
        uri = uri_of(tmp_path / "cours.tex")
        (action,) = code_actions(FAUTIF, uri, self.span(2, 5))
        (edit,) = action.edit.changes[uri]
        assert (edit.range.start, edit.range.end, edit.new_text) == (
            types.Position(line=2, character=7),
            types.Position(line=2, character=7),
            "$",
        )

    def test_it_names_the_diagnostic_it_answers(self, tmp_path):
        uri = uri_of(tmp_path / "cours.tex")
        (action,) = code_actions(FAUTIF, uri, self.span(2, 5))
        (answered,) = action.diagnostics
        assert answered.code == "unclosed-math"

    def test_nothing_is_offered_elsewhere_in_the_document(self, tmp_path):
        assert code_actions(FAUTIF, uri_of(tmp_path / "cours.tex"), self.span(5)) == []

    def test_a_diagnostic_with_no_repair_offers_nothing(self, tmp_path):
        # `\item` outside a list: where the list should open is anyone's guess.
        source = "\\begin{document}\n\\item hors liste\n\\end{document}\n"
        assert code_actions(source, uri_of(tmp_path / "cours.tex"), self.span(1)) == []

    @pytest.mark.parametrize("encoding", ["utf-16", "utf-8", "utf-32"])
    @pytest.mark.parametrize(
        ("line", "repaired"),
        [
            ("Été 𝔸 : $x", "Été 𝔸 : $x$"),  # a closing added at the end of the line
            ("Été 𝔸 : a} b", "Été 𝔸 : a b"),  # a brace taken out of the middle
        ],
        ids=["closing-added", "brace-removed"],
    )
    def test_the_edit_lands_where_the_client_counts(self, tmp_path, line, repaired, encoding):
        # Counted in UTF-16 for a client that counts bytes, the `$` went in before the colon.
        uri = uri_of(tmp_path / "cours.tex")
        source = f"\\begin{{document}}\n{line}\n\n.\n\\end{{document}}\n"
        (found,) = diagnose(source, uri, encoding)
        (action,) = code_actions(source, uri, found.range, encoding)
        (edit,) = action.edit.changes[uri]
        assert (edit.range.start.line, applied(line, edit, encoding)) == (1, repaired)


def test_the_features_the_editor_talks_to_are_registered():
    # Renaming a handler without its decorator would silently stop the underlining.
    assert set(server.protocol.fm.features) >= {
        "initialize",
        "textDocument/didOpen",
        "textDocument/didChange",
        "textDocument/didSave",
        "textDocument/didClose",
        "textDocument/codeAction",
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

    def talk(self, uri, text, capabilities):
        """What an editor gets back: the answer to `initialize`, the diagnostics, the fixes over the first."""
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
                        "params": {"processId": None, "rootUri": None, "capabilities": capabilities},
                    },
                )
                answer = self.receive(process)["result"]
                self.send(process, {"jsonrpc": "2.0", "method": "initialized", "params": {}})
                self.send(
                    process,
                    {
                        "jsonrpc": "2.0",
                        "method": "textDocument/didOpen",
                        "params": {
                            "textDocument": {"uri": uri, "languageId": "latex", "version": 1, "text": text}
                        },
                    },
                )
                while (published := self.receive(process)).get("method") != "textDocument/publishDiagnostics":
                    pass
                first = published["params"]["diagnostics"][0]
                self.send(
                    process,
                    {
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "textDocument/codeAction",
                        "params": {
                            "textDocument": {"uri": uri},
                            "range": first["range"],
                            "context": {"diagnostics": [first]},
                        },
                    },
                )
                while (fixes := self.receive(process)).get("id") != 2:
                    pass
            finally:
                process.stdin.close()
                process.kill()
        return answer, published["params"], fixes["result"]

    def test_a_document_opened_is_a_document_underlined(self, tmp_path):
        uri = uri_of(tmp_path / "cours.tex")
        _, published, _ = self.talk(uri, FAUTIF, {})
        (found,) = published["diagnostics"]
        assert (published["uri"], found["code"], found["range"]["start"]) == (
            uri,
            "unclosed-math",
            {"line": 2, "character": 5},
        )

    def test_a_client_that_counts_bytes_gets_bytes(self, tmp_path):
        # Neovim and Helix offer UTF-8 first, and pygls takes it. Counted in UTF-16, the
        # underline began four bytes early, and the `$` of the fix went in before the colon.
        uri = uri_of(tmp_path / "cours.tex")
        answer, published, fixes = self.talk(
            uri, ACCENTUE, {"general": {"positionEncodings": ["utf-8", "utf-16"]}}
        )
        (found,) = published["diagnostics"]
        ((edit,),) = [fix["edit"]["changes"][uri] for fix in fixes]
        assert (
            answer["capabilities"]["positionEncoding"],
            found["range"]["start"]["character"],
            edit["range"]["start"]["character"],
        ) == ("utf-8", 13, 15)


def test_without_pygls_the_command_says_what_to_install():
    # `pip install latexdetok` installs `latexdetok-lsp` all the same: an entry point
    # cannot depend on an extra. Found on the PATH, it must say so, not show a traceback.
    blocked = "import sys; sys.modules['lsprotocol'] = None; import latexdetok.server"
    done = subprocess.run([sys.executable, "-c", blocked], capture_output=True, text=True)
    assert (done.returncode, 'pip install "latexdetok[lsp]"' in done.stderr) == (1, True)
