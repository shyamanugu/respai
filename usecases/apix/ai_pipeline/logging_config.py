"""Centralized logging configuration for the ai_pipeline."""

import logging
import os
import sys
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Optional


_CONFIGURED = False

# Log directory — resolved relative to the ai_pipeline package root
# (this file lives at ai_pipeline/logging_config.py).
_PACKAGE_ROOT = Path(__file__).resolve().parent
LOGS_DIR = _PACKAGE_ROOT / "logs"
LOG_FILE = LOGS_DIR / "pipeline.log"


def setup_logging(level: str = "INFO", pipeline_run_id: Optional[str] = None) -> None:
    """Configure structured logging for the entire pipeline.

    Logs are written to BOTH stdout (for local dev / Docker) and a rotating
    file under ``ai_pipeline/logs/pipeline.log`` (midnight rollover, 7-day
    retention). Uncaught exceptions are also routed to the log.

    Args:
        level: Log level string (DEBUG, INFO, WARNING, ERROR, CRITICAL).
        pipeline_run_id: Optional run identifier injected into every log line.
    """
    global _CONFIGURED
    if _CONFIGURED:
        return

    fmt_parts = ["%(asctime)s", "%(levelname)s", "%(name)s"]
    if pipeline_run_id:
        fmt_parts.append(f"run={pipeline_run_id}")
    fmt_parts.append("%(message)s")
    fmt = " | ".join(fmt_parts)
    formatter = logging.Formatter(fmt, datefmt="%Y-%m-%dT%H:%M:%S")

    resolved_level = getattr(logging, level.upper(), logging.INFO)

    root = logging.getLogger()
    # Root at DEBUG so the file captures everything; console is gated separately.
    root.setLevel(logging.DEBUG)
    root.handlers.clear()

    # ── Console handler (stdout) ─────────────────────────────────────────────
    # Reconfigure stdout to UTF-8 (replace unencodable chars) so non-ASCII
    # characters never crash logging on Windows' cp1252 console.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setLevel(resolved_level)
    stream_handler.setFormatter(formatter)
    root.addHandler(stream_handler)

    # ── Rotating file handler (midnight rollover, keep 7 days) ───────────────
    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        file_handler = TimedRotatingFileHandler(
            filename=str(LOG_FILE),
            when="midnight",
            interval=1,
            backupCount=7,        # keep 7 days, auto-delete older
            encoding="utf-8",
            utc=False,
        )
        file_handler.suffix = "%Y-%m-%d"
        file_handler.setLevel(logging.DEBUG)  # capture everything to file
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)
    except OSError:
        # If the file system is read-only, keep console logging only.
        root.warning("Could not create log file at %s — logging to stdout only", LOG_FILE)

    # Suppress noisy third-party loggers
    for name in ("azure", "openai", "httpx", "httpcore", "urllib3", "adlfs", "fsspec", "azure.core.pipeline.policies.http_logging_policy"):
        logging.getLogger(name).setLevel(logging.WARNING)

    # ── Route uncaught exceptions to the log ─────────────────────────────────
    def _log_uncaught(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        logging.getLogger("ai_pipeline").critical(
            "Uncaught exception", exc_info=(exc_type, exc_value, exc_tb)
        )

    sys.excepthook = _log_uncaught

    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """Return a child logger under the ``ai_pipeline`` namespace."""
    return logging.getLogger(f"ai_pipeline.{name}")

