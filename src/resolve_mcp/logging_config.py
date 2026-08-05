"""stderr-only logging.

stdout belongs to the MCP transport; anything written there corrupts the session. All
logging — including the tracebacks that never reach a tool result — goes to stderr.
"""

from __future__ import annotations

import logging
import sys

from .config import Config, get_config

LOGGER_NAME = "resolve_mcp"


def configure_logging(config: Config | None = None) -> logging.Logger:
    config = config or get_config()
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(config.log_level.upper())
    logger.propagate = False
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        logger.addHandler(handler)
    return logger


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"{LOGGER_NAME}.{name}")
