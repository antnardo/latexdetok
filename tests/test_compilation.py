"""Compiled modules: only use the ones that match the sources."""

import json
from importlib.machinery import EXTENSION_SUFFIXES

import pytest

from latexdetok.compilation import (
    COMPILED_MODULES,
    ENVIRONMENT,
    GROUP,
    manifest,
    sources_digest,
    use_compiled,
)

SUFFIX = EXTENSION_SUFFIXES[0]


@pytest.fixture
def package(tmp_path):
    folder = tmp_path / "latexdetok"
    folder.mkdir()
    (folder / "parser.py").write_text("x = 1\n", encoding="utf-8")
    (folder / "classes.py").write_text("y = 2\n", encoding="utf-8")
    return folder


@pytest.fixture
def build(tmp_path, package, monkeypatch):
    """A build that matches the sources: a manifest and one extension per module."""
    monkeypatch.delenv(ENVIRONMENT, raising=False)
    folder = tmp_path / "_mypyc"
    folder.mkdir()
    for name in [*COMPILED_MODULES, GROUP.rsplit(".", 1)[1] + "__mypyc"]:
        (folder / f"{name}{SUFFIX}").write_bytes(b"")
    (folder / "manifest.json").write_text(json.dumps(manifest(package)), encoding="utf-8")
    return folder


def rewrite_manifest(build, **changes):
    path = build / "manifest.json"
    written = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps({**written, **changes}), encoding="utf-8")


class TestUse:
    def test_an_up_to_date_build_goes_to_the_front_of_the_path(self, package, build):
        path = [str(package)]
        assert use_compiled(path, package, build)
        assert path == [str(build), str(package)]

    def test_not_inserted_twice(self, package, build):
        path = [str(package)]
        use_compiled(path, package, build)
        use_compiled(path, package, build)
        assert path == [str(build), str(package)]

    def test_a_changed_source_means_the_sources_are_used(self, package, build):
        (package / "parser.py").write_text("x = 3\n", encoding="utf-8")
        path = [str(package)]
        assert not use_compiled(path, package, build)
        assert path == [str(package)]

    def test_an_added_source_means_the_sources_are_used(self, package, build):
        (package / "nouveau.py").write_text("", encoding="utf-8")
        assert not use_compiled([str(package)], package, build)

    def test_the_environment_variable_forces_the_sources(self, package, build, monkeypatch):
        monkeypatch.setenv(ENVIRONMENT, "1")
        assert not use_compiled([str(package)], package, build)

    def test_with_no_manifest(self, package, build):
        (build / "manifest.json").unlink()
        assert not use_compiled([str(package)], package, build)

    def test_an_unreadable_manifest(self, package, build):
        (build / "manifest.json").write_text("{", encoding="utf-8")
        assert not use_compiled([str(package)], package, build)

    def test_another_python_version(self, package, build):
        rewrite_manifest(build, suffix=".cpython-313-darwin.so")
        assert not use_compiled([str(package)], package, build)

    def test_another_list_of_modules(self, package, build):
        rewrite_manifest(build, modules=["parser"])
        assert not use_compiled([str(package)], package, build)

    def test_a_missing_extension(self, package, build):
        (build / f"parser{SUFFIX}").unlink()
        assert not use_compiled([str(package)], package, build)


def test_the_digest_depends_on_the_name_and_the_content(package):
    before = sources_digest(package)
    (package / "classes.py").rename(package / "autre.py")
    assert sources_digest(package) != before
