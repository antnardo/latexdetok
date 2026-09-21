"""Harvesting signatures from the kernel sources (`scripts/harvest.py`), on typical lines."""

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts" / "harvest.py"


@pytest.fixture(scope="module")
def harvest():
    spec = importlib.util.spec_from_file_location("harvest", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.harvest


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ('\\DeclareMathSymbol{\\alpha}{\\mathord}{letters}{"0B}', {"alpha": ("math", "-")}),
        ('\\DeclareMathAccent{\\hat}{\\mathalpha}{operators}{"5E}', {"hat": ("math", "m")}),
        ("\\DeclareTextAccentDefault{\\'}{OT1}", {"'": ("text", "m")}),
        ("\\DeclareTextSymbolDefault{\\textbackslash}{OMS}", {"textbackslash": ("text", "-")}),
        ("\\NewDocumentCommand \\AddToHook { m o +m }", {"AddToHook": ("any", "m o +m")}),
        ("\\NewDocumentCommand\\MakeLinkTarget{sO{}m}{%", {"MakeLinkTarget": ("any", "sO{}m")}),
        ("\\newcommand\\qbezier[2][0]{\\bezier{#1}#2}", {"qbezier": ("any", "o m")}),
        ("\\DeclareRobustCommand{\\GenericError}[4]{%", {"GenericError": ("any", "m m m m")}),
        ("\\DeclareRobustCommand\\log{\\mathop{\\operator@font log}\\nolimits}", {"log": ("math", "-")}),
    ],
)
def test_declaration(harvest, line, expected):
    assert harvest([line]) == expected


@pytest.mark.parametrize(
    "line",
    [
        "\\DeclareMathSymbol{a}{\\mathalpha}{letters}{`a}",
        "\\DeclareRobustCommand\\@internal[1]{}",
        "% \\DeclareRobustCommand\\commentee[1]{}",
    ],
)
def test_ignored(harvest, line):
    assert harvest([line]) == {}


def test_explicit_signature_wins(harvest):
    lines = [
        "\\DeclareRobustCommand\\x{}",
        "\\NewDocumentCommand\\x{s o m}{}",
        "\\DeclareRobustCommand\\x[1]{}",
    ]
    assert harvest(lines) == {"x": ("any", "s o m")}
