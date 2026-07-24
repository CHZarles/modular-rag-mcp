"""Logging facade.

A2/A3 only need a stderr writer; the full JSON-Lines formatter lives in F2.
This module is the single import path for callers so we can swap handlers
later without touching call sites.
"""

from __future__ import annotations

import logging
import sys

_DEFAULT_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"


def get_logger(name: str, level: int = logging.INFO) -> logging.Logger:
    """Return a logger that writes plain text to stderr.

    Args:
        name: Typically ``__name__`` of the calling module.
        level: Logging level (defaults to INFO).

    Returns:
        A configured :class:`logging.Logger` with a single stderr handler.
        Calling this twice with the same name returns the same logger
        (idempotent — we don't add duplicate handlers).
    """
    logger = logging.getLogger(name)
    if logger.handlers:  # already configured by an earlier call
        return logger
    handler = logging.StreamHandler(stream=sys.stderr)
    handler.setFormatter(logging.Formatter(_DEFAULT_FORMAT))
    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False
    return logger