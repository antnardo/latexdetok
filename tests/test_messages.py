"""The message catalogue: one language per file, and code that only knows the keys."""

import pytest

from latexdetok import TexFile, check
from latexdetok.diagnostics import Severity
from latexdetok.messages import DEFAULT_LANGUAGE, catalogue, language, languages, say, set_language

SOURCE = "\\documentclass{article}\n\\begin{document}\n\\emph{a\n\\end{document}\n"


@pytest.fixture
def diagnostic(tmp_path):
    path = tmp_path / "cours.tex"
    path.write_text(SOURCE, encoding="utf-8")
    return lambda: check(path)[0]


class TestCatalogue:
    def test_the_languages_shipped(self):
        assert set(languages()) >= {"en", "fr"} and DEFAULT_LANGUAGE in languages()

    @pytest.mark.parametrize("name", ["en", "fr"])
    def test_the_same_keys_everywhere(self, name):
        # A missing key would fall back to English: say so here rather than at run time.
        assert catalogue(name).keys() == catalogue(DEFAULT_LANGUAGE).keys()

    @pytest.mark.parametrize("name", ["en", "fr"])
    def test_no_empty_template(self, name):
        assert [key for key, template in catalogue(name).items() if not template] == []


class TestSay:
    def test_the_fields_are_filled_in(self):
        assert say("unclosed-math", opening="$") == "“$” never closed"

    def test_the_latex_braces_are_left_alone(self):
        assert say("unclosed-environment.fix", closing="\\end{itemize}") == "close it with “\\end{itemize}”"

    def test_an_unknown_key_returns_itself(self, caplog):
        assert say("cle-qui-n-existe-pas") == "cle-qui-n-existe-pas"
        assert "unknown message" in caplog.text


class TestLanguage:
    def test_changing_language(self):
        set_language("fr")
        assert (language(), say("unclosed-brace")) == ("fr", "« { » jamais fermée")

    def test_going_back_to_the_environment(self, monkeypatch):
        monkeypatch.delenv("LATEXDETOK_LANG", raising=False)
        set_language("")
        assert language() == DEFAULT_LANGUAGE

    def test_the_language_of_the_environment(self, monkeypatch):
        monkeypatch.setenv("LATEXDETOK_LANG", "fr")
        set_language("")
        assert language() == "fr"

    def test_an_unknown_language_raises(self):
        with pytest.raises(ValueError, match="unknown language"):
            set_language("klingon")


class TestDiagnostics:
    def test_the_message_follows_the_language(self, diagnostic):
        found = diagnostic()
        english = (found.code, found.message, found.suggestion)
        set_language("fr")
        found = diagnostic()
        assert (english, found.code, found.message, found.suggestion) == (
            ("unclosed-brace", "“{” of “\\emph” never closed", "close it with “}”"),
            "unclosed-brace",
            "« { » de « \\emph » jamais fermée",
            "fermer par « } »",
        )

    def test_the_severity_shows_translated_and_compares_in_english(self):
        set_language("fr")
        assert (str(Severity.ERROR), Severity.ERROR.text, Severity.WARNING.counted(2)) == (
            "error",
            "erreur",
            "2 avertissements",
        )

    def test_the_json_stays_english(self, tmp_path):
        from latexdetok.diagnostics import to_json

        set_language("fr")
        path = tmp_path / "cours.tex"
        path.write_text(SOURCE, encoding="utf-8")
        rendered = to_json(check(path), "cours.tex")[0]
        assert (rendered["severity"], rendered["code"]) == ("error", "unclosed-brace")

    def test_the_code_does_not_move_with_the_language(self):
        tex = TexFile(SOURCE.splitlines(keepends=True))
        tex.analyse()
        codes = [diagnostic.code for diagnostic in tex.diagnostics]
        set_language("fr")
        tex.analyse()
        assert codes == [diagnostic.code for diagnostic in tex.diagnostics] == ["unclosed-brace"]
