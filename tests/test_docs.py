"""The examples of the documentation run and give what they announce."""

import doctest
from pathlib import Path

import pytest

from latexdetok import TexFile
from latexdetok.rendering import html_page

ROOT = Path(__file__).parents[1]


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
