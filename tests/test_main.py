"""`latexdetok check`: compiler-style or JSON output, exit code."""

import io
import json
from importlib.metadata import entry_points
from pathlib import Path

import pytest

from latexdetok.__main__ import ERRORS, OK, UNREADABLE, main

FAUTIF = "\\documentclass{article}\n\\begin{document}\nSoit $x\n\nla suite.\n\\end{document}\n"
PROPRE = "\\documentclass{article}\n\\begin{document}\nSoit $x$.\n\\end{document}\n"


def write(folder, name, text):
    path = folder / name
    path.write_text(text, encoding="utf-8")
    return path


def test_an_error_exits_in_error(tmp_path, capsys):
    path = write(tmp_path, "cours.tex", FAUTIF)
    status = main(["check", str(path), "--color", "never"])
    lines = capsys.readouterr().out.splitlines()
    assert (status, lines[0], lines[-1]) == (
        ERRORS,
        f"{path}:3:6: error [unclosed-math] “$” never closed",
        "1 error, 0 warning, 0 info",
    )


def test_a_clean_document(tmp_path, capsys):
    path = write(tmp_path, "cours.tex", PROPRE)
    assert (main(["check", str(path)]), capsys.readouterr().out) == (
        OK,
        "0 error, 0 warning, 0 info\n",
    )


def test_json(tmp_path, capsys):
    path = write(tmp_path, "cours.tex", FAUTIF)
    main(["check", str(path), "--json"])
    (entry,) = json.loads(capsys.readouterr().out)
    assert (entry["file"], entry["line"], entry["column"], entry["code"], entry["related"][0]["line"]) == (
        str(path),
        3,
        6,
        "unclosed-math",
        4,
    )


def test_infos_hidden_by_default(tmp_path, capsys):
    path = write(tmp_path, "cours.tex", "\\def\\beq{\\begin{equation}}\n")
    main(["check", str(path)])
    hidden = capsys.readouterr().out
    main(["check", str(path), "--infos", "--color", "never"])
    shown = capsys.readouterr().out
    assert (hidden, "info [unclosed-environment]" in shown) == (
        "0 error, 0 warning, 1 info (1 hidden, see --infos)\n",
        True,
    )


def test_a_folder_is_walked(tmp_path, capsys):
    write(tmp_path, "a.tex", FAUTIF)
    write(tmp_path, "b.tex", FAUTIF)
    main(["check", str(tmp_path), "--json"])
    # `file` carries the path as the system writes it: cut it with `Path`, not on “/”.
    assert sorted(Path(entry["file"]).name for entry in json.loads(capsys.readouterr().out)) == [
        "a.tex",
        "b.tex",
    ]


def test_a_missing_file(tmp_path, capsys):
    status = main(["check", str(tmp_path / "absent.tex")])
    assert (status, "unreadable" in capsys.readouterr().err) == (UNREADABLE, True)


def test_the_command_is_installed_with_the_package():
    # Without it, `uvx latexdetok` and `pipx install latexdetok` fail: the metadata is what they read.
    (script,) = entry_points(group="console_scripts", name="latexdetok")
    assert script.load() is main


@pytest.fixture
def stdin(monkeypatch):
    """Feeds the standard input with bytes, the way a shell or an editor does."""

    def feed(text, encoding="utf-8"):
        stream = io.TextIOWrapper(io.BytesIO(text.encode(encoding)))
        monkeypatch.setattr("sys.stdin", stream)

    return feed


class TestStandardInput:
    def test_a_document_read_from_the_standard_input(self, stdin, capsys):
        stdin(FAUTIF)
        status = main(["check", "-", "--color", "never"])
        lines = capsys.readouterr().out.splitlines()
        assert (status, lines[0]) == (ERRORS, "-:3:6: error [unclosed-math] “$” never closed")

    def test_the_name_given_is_the_one_reported(self, stdin, capsys):
        stdin(FAUTIF)
        main(["check", "-", "--json", "--stdin-filename", "chapitres/un.tex"])
        (found,) = json.loads(capsys.readouterr().out)
        assert found["file"] == str(Path("chapitres/un.tex"))

    def test_the_buffer_is_read_and_not_the_file_on_disk(self, tmp_path, stdin, capsys):
        # The point of the whole thing: an editor checks what is being typed, not what was saved.
        path = write(tmp_path, "cours.tex", PROPRE)
        stdin(FAUTIF)
        status = main(["check", "-", "--color", "never", "--stdin-filename", str(path)])
        assert (status, f"{path}:3:6: error" in capsys.readouterr().out) == (ERRORS, True)

    def test_the_inclusions_are_looked_for_in_the_folder_of_that_name(self, tmp_path, stdin, capsys):
        write(tmp_path, "defs.tex", "\\newcommand{\\beq}{\\begin{equation}}\n")
        path = tmp_path / "cours.tex"
        stdin("\\input{defs}\n\\begin{document}\n\\beq x\\end{equation}\n\\end{document}\n")
        status = main(["check", "-", "--color", "never", "--stdin-filename", str(path)])
        # `\beq` opens the environment that `\end{equation}` closes: without the definition read
        # in defs.tex, the `\end` would have nothing to close.
        assert (status, capsys.readouterr().out) == (OK, "0 error, 0 warning, 0 info\n")

    def test_the_line_endings_of_the_buffer_are_kept(self, stdin, capsys):
        stdin(FAUTIF.replace("\n", "\r\n"))
        status = main(["check", "-", "--color", "never"])
        assert (status, "-:3:6: error" in capsys.readouterr().out) == (ERRORS, True)

    def test_a_buffer_in_latin1(self, stdin, capsys):
        stdin("\\begin{document}\nÉtude de $x\n\n.\n\\end{document}\n", encoding="latin-1")
        status = main(["check", "-", "--color", "never"])
        assert (status, "Étude" in capsys.readouterr().out) == (ERRORS, True)

    def test_the_standard_input_goes_alone(self, tmp_path, capsys):
        path = write(tmp_path, "cours.tex", PROPRE)
        with pytest.raises(SystemExit):
            main(["check", "-", str(path)])
        assert "without other paths" in capsys.readouterr().err

    def test_the_name_without_the_standard_input(self, tmp_path, capsys):
        path = write(tmp_path, "cours.tex", PROPRE)
        with pytest.raises(SystemExit):
            main(["check", str(path), "--stdin-filename", "autre.tex"])
        assert "--stdin-filename" in capsys.readouterr().err
