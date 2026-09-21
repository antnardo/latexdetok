"""The package's log: quiet by default, a trace of the analysis in verbose mode.

No more files opened on import. The old module created `log/detok_debug.log` and
`log/detok_errors.log` inside the package folder: since `log/` was not tracked by
git, `import latexdetok` failed on any fresh clone, and every error a test
expected piled up in the log.

A `TexFile`'s verbose mode only lasts as long as its analysis
(`verbose_logging`): the old global setting, redone at every `TexFile()`,
overwrote the level chosen by the calling application.
"""

import logging
import sys
from collections.abc import Iterator
from contextlib import contextmanager

__all__ = ["LOGGER_NAME", "change_logging_level", "logger", "verbose_logging"]

LOGGER_NAME = "latexdetok"

logger = logging.getLogger(LOGGER_NAME)
logger.addHandler(logging.NullHandler())

_console = logging.StreamHandler()
_console.setFormatter(logging.Formatter("%(message)s"))


def change_logging_level(verbose: bool) -> None:
    """Show the trace of the analysis on the console for good, or take it away."""
    if verbose:
        # The current error output, not the one at import time (Jupyter and pytest replace it).
        _console.setStream(sys.stderr)
        logger.setLevel(logging.DEBUG)
        if _console not in logger.handlers:
            logger.addHandler(_console)
    else:
        logger.setLevel(logging.NOTSET)
        logger.removeHandler(_console)


@contextmanager
def verbose_logging(enabled: bool) -> Iterator[None]:
    """Trace on the console for the length of the block, then give the setting back."""
    if not enabled or _console in logger.handlers:
        yield
        return
    level = logger.level
    change_logging_level(True)
    try:
        yield
    finally:
        logger.removeHandler(_console)
        logger.setLevel(level)
