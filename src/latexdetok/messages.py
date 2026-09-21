"""What is written for a person, kept out of the code so that it can be translated.

Why. The package is in English, but its diagnostics speak to whoever is writing
the document — and they may want to read them in French. So the messages do not
live in the code: they live in `data/messages-<language>.txt`, one message per
line, and the code only knows the key.

    say("unclosed-brace.fix")                    # "close it with « } »"
    say("math-in-math", control="\\\\[", opening="$")

What stays in the code, because a machine reads it: the diagnostic codes
(`TexDiagnostic.code`), the values of `Severity`, the JSON keys. What goes
through here: the message, the related notes, the fix, and the severity as it
is displayed. One more language is therefore one more file, and nothing else.

Language. `LATEXDETOK_LANG` picks it, English otherwise; `set_language` changes
it on the fly. A key missing from the chosen language falls back to English and
is logged: one message in the wrong language beats a crash while reporting what
is wrong.

File format. `key  template`, separated by blanks; `#` comments a line. Templates
never write LaTeX themselves: whatever comes from the document (`\\end{itemize}`,
`\\left`) arrives ready-made in a field, which spares every translation from
doubling the braces `format` would otherwise eat.
"""

import os
from importlib.resources import files
from pathlib import Path

from latexdetok.logger import logger

__all__ = ["DEFAULT_LANGUAGE", "catalogue", "language", "languages", "say", "set_language"]

DATA = Path(str(files("latexdetok"))) / "data"
PREFIX = "messages-"
DEFAULT_LANGUAGE = "en"
ENVIRONMENT = "LATEXDETOK_LANG"

_catalogues: dict[str, dict[str, str]] = {}
_language: str | None = None


def languages() -> list[str]:
    """The languages shipped with the package, in alphabetical order."""
    return sorted(path.stem[len(PREFIX) :] for path in DATA.glob(f"{PREFIX}*.txt"))


def language() -> str:
    """The language in force: the chosen one, the one in `LATEXDETOK_LANG`, else English."""
    global _language
    if _language is None:
        wanted = os.environ.get(ENVIRONMENT, "").strip().lower()
        _language = wanted if wanted in languages() else DEFAULT_LANGUAGE
    return _language


def set_language(name: str) -> None:
    """Choose the language of the messages; `""` goes back to the environment's."""
    global _language
    if name and name not in languages():
        raise ValueError(f"unknown language {name!r}: {', '.join(languages())}")
    _language = name or None


def catalogue(name: str | None = None) -> dict[str, str]:
    """Every template of a language, read once and kept."""
    name = name or language()
    if name not in _catalogues:
        _catalogues[name] = _read(DATA / f"{PREFIX}{name}.txt")
    return _catalogues[name]


def say(key: str, **fields: object) -> str:
    """The message a key stands for, with its fields filled in.

    Fields are substituted by name, `{like}` this, and nothing else is touched:
    a message is full of LaTeX braces, and no translator should have to double
    them the way `str.format` would demand.
    """
    template = catalogue().get(key)
    if template is None:
        template = catalogue(DEFAULT_LANGUAGE).get(key)
        if template is None:
            logger.warning("unknown message: %s", key)
            return key
        logger.debug("message missing from %s: %s", language(), key)
    for name, value in fields.items():
        template = template.replace("{" + name + "}", str(value))
    return template


def _read(path: Path) -> dict[str, str]:
    entries: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        key, _, template = text.partition(" ")
        entries[key] = template.strip()
    return entries
