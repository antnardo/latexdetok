"""Shared fixtures: analysing a string like a file, and describing a tree.

`shape` sums a tree up into Python structures one can compare at a glance: a
command or a text by its rewriting (`"\\section"`, `"Intro"`), a blank line by
`"PAR"`, a group by `(delimiter or name, [children])`. Math is designated by its
opening delimiter (`"$"`, `"\\["`).
"""

from pathlib import Path

import pytest

from latexdetok import Severity, TexContainer, TexFile, TexGroup
from latexdetok.messages import set_language

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def english():
    """Messages are compared in English, whatever the language of the machine."""
    set_language("en")
    yield
    set_language("")


def _shape(node):
    if isinstance(node, TexGroup):
        label = node.delimiter if node.math else node.name
        return (label, [_shape(child) for child in node.content])
    if isinstance(node, TexContainer) and node.is_par():
        return "PAR"
    return str(node)


def _walk(node):
    yield node
    if isinstance(node, TexGroup):
        for child in node.content:
            yield from _walk(child)


@pytest.fixture
def parse():
    def analyse(source, name="<test>"):
        tex = TexFile(source.splitlines(keepends=True), name=name)
        tex.analyse()
        return tex

    return analyse


@pytest.fixture
def tree(parse):
    """The children of the root, summed up by `shape`."""

    def analyse(source):
        return _shape(parse(source).container)[1]

    return analyse


@pytest.fixture
def shape():
    return _shape


@pytest.fixture
def walk():
    return _walk


@pytest.fixture
def problems():
    """The diagnostics of an analysis, without the infos: what might be a mistake."""
    return lambda tex: [
        diagnostic for diagnostic in tex.diagnostics if diagnostic.severity is not Severity.INFO
    ]


@pytest.fixture
def fixture_path():
    return lambda name: FIXTURES / name


@pytest.fixture(params=sorted(path.name for path in FIXTURES.glob("*.tex")))
def fixture_file(request):
    tex = TexFile(FIXTURES / request.param)
    tex.analyse()
    return tex
