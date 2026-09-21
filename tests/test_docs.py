"""The examples of the documentation run and give what they announce."""

import doctest
from pathlib import Path

import pytest

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
