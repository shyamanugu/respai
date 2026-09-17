"""
backend/config/logging.py — Centralized application logger
============================================================
Provides a pre-configured rotating file logger for the entire application.

Features:
  - TimedRotatingFileHandler with 7-day retention and auto-delete
  - Captures: timestamp, level, filename, function name, line number, message
  - Console output for development
  - Single shared logger instance via get_logger()

Usage:
    from backend.config.logging import get_logger
    logger = get_logger(__name__)
    logger.info("Week discovered", extra={"week": "2025-08-28"})
"""

import logging
import os
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

# ---------------------------------------------------------------------------
# Log directory — resolved relative to the application root (where main.py lives)
# ---------------------------------------------------------------------------
_APP_ROOT = Path(__file__).resolve().parent.parent.parent  # application/
LOGS_DIR = _APP_ROOT / "logs"
LOGS_DIR.mkdir(exist_ok=True)

LOG_FILE = LOGS_DIR / "apix.log"

# ---------------------------------------------------------------------------
# Format: 2026-04-27 10:30:45,123 | INFO | backend.auth.session | login | L82 | msg
# ---------------------------------------------------------------------------
LOG_FORMAT = (
    "%(asctime)s | %(levelname)-8s | %(name)s | %(funcName)s | L%(lineno)d | %(message)s"
)
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# ---------------------------------------------------------------------------
# Log level from env (default INFO)
# ---------------------------------------------------------------------------
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()


def _build_root_logger() -> logging.Logger:
    """
    Build and return the application root logger.

    Called once at import time. All child loggers created via
    ``get_logger(__name__)`` inherit this configuration.
    """
    root = logging.getLogger("apix")
    root.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))

    # Avoid adding duplicate handlers on Streamlit hot-reload
    if root.handlers:
        return root

    # ---- Rotating file handler (midnight rollover, keep 7 days) ----
    file_handler = TimedRotatingFileHandler(
        filename=str(LOG_FILE),
        when="midnight",
        interval=1,
        backupCount=7,        # keep 7 days, auto-delete older
        encoding="utf-8",
        utc=False,
    )
    file_handler.suffix = "%Y-%m-%d"
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT))

    # ---- Console handler (for local dev / Docker stdout) ----
    console_handler = logging.StreamHandler()
    console_handler.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
    console_handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT))

    root.addHandler(file_handler)
    root.addHandler(console_handler)

    return root


# Initialize once at import
_ROOT_LOGGER = _build_root_logger()


def get_logger(name: str) -> logging.Logger:
    """
    Return a child logger under the ``apix`` namespace.

    Args:
        name: Typically ``__name__`` of the calling module.

    Returns:
        A configured ``logging.Logger`` instance.

    Example::

        from backend.config.logging import get_logger
        logger = get_logger(__name__)
        logger.info("User %s logged in", username)
    """
    return _ROOT_LOGGER.getChild(name)
