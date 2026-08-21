"""Console logging opt-in: friendly CLI/recipe lines, silence as a library.

The package logs through the standard `logging` module and never configures
handlers on import (the "datamosh" logger carries a NullHandler, per library
etiquette) -- embedders route, filter, or silence datamosh output with the
tools they already use. enable_console_logging() is the one-call opt-in made
for the CLI, the UI, and recipes: INFO lines render bare, byte-identical to
the print() output they replaced, and WARNING+ lines gain a "WARNING: "-style
prefix so problems stand out without a traceback.

Stdlib-only on purpose: datamosh/__init__.py imports eagerly, so anything
heavier here would create an import cycle.
"""

import logging
import sys

# attribute stamped on the handler so repeat enable calls find it (idempotence)
_TAG = "_datamosh_console_handler"


class _ConsoleFormatter(logging.Formatter):
    """Bare %(message)s below WARNING; "LEVEL: message" from WARNING up."""

    def format(self, record):
        msg = record.getMessage()
        if record.levelno >= logging.WARNING:
            return f"{record.levelname}: {msg}"
        return msg


def enable_console_logging(level=logging.INFO, stream=None):
    """Print datamosh log lines to the console; returns the handler.

    Attaches one tagged StreamHandler to the "datamosh" logger -- calling this
    again is a no-op apart from refreshing the level, so recipes, the CLI, and
    an embedding app can all call it without doubling output. stream=None
    means sys.stdout (resolved at call time, so redirected stdout is honored).
    """
    logger = logging.getLogger("datamosh")
    logger.setLevel(level)
    for h in logger.handlers:
        if getattr(h, _TAG, False):
            h.setLevel(level)
            return h
    handler = logging.StreamHandler(stream if stream is not None else sys.stdout)
    handler.setLevel(level)
    handler.setFormatter(_ConsoleFormatter())
    setattr(handler, _TAG, True)
    logger.addHandler(handler)
    return handler


def disable_console_logging():
    """Detach the console handler attached by enable_console_logging (if any)."""
    logger = logging.getLogger("datamosh")
    for h in list(logger.handlers):
        if getattr(h, _TAG, False):
            logger.removeHandler(h)
            h.close()
