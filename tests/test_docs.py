"""The examples of the documentation run and give what they announce."""

import doctest
import re
from pathlib import Path

import pytest

from latexdetok import TexFile
from latexdetok.__main__ import main
from latexdetok.rendering import html_page

ROOT = Path(__file__).parents[1]
COMPARISON = "\n## TeX's message, and latexdetok's\n"


@pytest.mark.parametrize("name", ["README.md", "docs/EXAMPLES.md", "docs/API.md"])
def test_examples_of_the_documentation(name):
    result = doctest.testfile(
        str(ROOT / name),
        module_relative=False,
        optionflags=doctest.ELLIPSIS | doctest.NORMALIZE_WHITESPACE,
        encoding="utf-8",
    )
    assert result.failed == 0


def test_the_example_page_is_the_one_the_code_draws():
    # Redrawn with `python scripts/render.py tests/fixtures/course.tex --format html -o docs/frame-example.html`.
    tex = TexFile(ROOT / "tests" / "fixtures" / "course.tex")
    tex.analyse()
    assert (ROOT / "docs" / "frame-example.html").read_text(encoding="utf-8") == html_page(tex)


def test_the_readme_comparison_is_what_the_command_prints(tmp_path, monkeypatch, capsys):
    # The README sets pdflatex's message against `latexdetok check intro.tex`. The first was copied
    # from TeX Live and cannot move; the second is rerun here, so that it cannot go stale.
    section = (ROOT / "README.md").read_text(encoding="utf-8").split(COMPARISON)[1].split("\n## ")[0]
    blocks = re.findall(r"^```(\w+)\n(.*?)^```$", section, re.MULTILINE | re.DOTALL)
    (source,) = [text for language, text in blocks if language == "latex"]
    shown = [text for language, text in blocks if language == "text"][-1]
    (tmp_path / "intro.tex").write_text(source, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    main(["check", "intro.tex", "--color", "never"])
    # The README drops the trailing blanks of the output, which markdown would not show anyway.
    assert [line.rstrip() for line in capsys.readouterr().out.splitlines()] == shown.splitlines()
