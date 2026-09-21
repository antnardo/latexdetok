"""The log: quiet by default, a trace in verbose mode, no file opened."""

import logging

import pytest

from latexdetok import TexFile
from latexdetok.logger import change_logging_level, logger


@pytest.fixture(autouse=True)
def _quiet_log():
    yield
    change_logging_level(False)


def test_no_log_file():
    # A regression: the import opened log/*.log and failed on a clone with no log/ folder.
    assert not any(isinstance(handler, logging.FileHandler) for handler in logger.handlers)


def test_verbose_adds_the_console_only_once():
    change_logging_level(True)
    change_logging_level(True)
    consoles = [handler for handler in logger.handlers if type(handler) is logging.StreamHandler]
    assert (len(consoles), logger.level) == (1, logging.DEBUG)


def test_not_verbose_takes_the_console_away():
    change_logging_level(True)
    change_logging_level(False)
    assert [type(handler) for handler in logger.handlers] == [logging.NullHandler]


def test_verbose_analysis_gives_the_setting_back():
    logger.setLevel(logging.ERROR)
    TexFile(["{a}\n"], verbose=True).analyse()
    assert (logger.level, len(logger.handlers)) == (logging.ERROR, 1)


def test_quiet_file_leaves_the_callers_level_alone():
    # A regression: every TexFile() reset the level of the log to its default value.
    logger.setLevel(logging.DEBUG)
    TexFile(["a\n"]).analyse()
    assert logger.level == logging.DEBUG


def test_verbose_analysis_shows_the_trace(capsys):
    TexFile(["{a}\n"], verbose=True).analyse()
    assert "OPEN {" in capsys.readouterr().err


def test_analysis_traces_the_openings(caplog):
    caplog.set_level(logging.DEBUG, logger="latexdetok")
    TexFile(["\\begin{center}x\\end{center}\n"]).analyse()
    assert any("OPEN \\begin{center}" in message for message in caplog.messages)


def test_warning_is_logged(caplog):
    caplog.set_level(logging.WARNING, logger="latexdetok")
    TexFile(["{a\n"], name="brouillon.tex").analyse()
    assert caplog.messages == ["brouillon.tex - 1:1: warning [unclosed-brace] “{” never closed"]
