"""The VS Code client: a manifest the editor can read, and the settings it promises."""

import json
from pathlib import Path

import pytest

MANIFEST = Path(__file__).parents[1] / "editors" / "vscode" / "package.json"


@pytest.fixture(scope="module")
def manifest():
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def test_the_entry_point_exists(manifest):
    assert (MANIFEST.parent / manifest["main"]).is_file()


def test_the_settings_the_readme_documents(manifest):
    properties = manifest["contributes"]["configuration"]["properties"]
    assert set(properties) == {
        "latexdetok.serverPath",
        "latexdetok.language",
        "latexdetok.followInputs",
        "latexdetok.expand",
    }


def test_the_command_it_starts_is_the_one_the_package_installs(manifest):
    # The name of the console script, in `pyproject.toml`: renaming one breaks the other.
    pyproject = (MANIFEST.parents[2] / "pyproject.toml").read_text(encoding="utf-8")
    default = manifest["contributes"]["configuration"]["properties"]["latexdetok.serverPath"]["default"]
    assert f'{default} = "latexdetok.server:main"' in pyproject


def test_the_command_it_registers_is_declared(manifest):
    declared = {command["command"] for command in manifest["contributes"]["commands"]}
    source = (MANIFEST.parent / "extension.js").read_text(encoding="utf-8")
    assert all(f'registerCommand("{command}"' in source for command in declared)
