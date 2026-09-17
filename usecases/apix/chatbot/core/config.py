"""
chatbot/config.py — Centralized configuration, environment loading, and logging.

Single source of truth for filesystem paths, the SQLite database location,
Azure SQL connection settings, Azure OpenAI settings, and the package logger.
All configuration values live in ``chatbot/.env`` and are loaded once at import
so every module sees a consistent environment regardless of import order.
Every module gets its logger via :func:`get_logger`.
"""

from __future__ import annotations

import logging
import os
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

# ── Filesystem anchors ─────────────────────────────────────────────────────
PACKAGE_ROOT: Path = Path(__file__).resolve().parent.parent   # chatbot/ (this file lives in chatbot/core/)
WORKSPACE_ROOT: Path = PACKAGE_ROOT.parent                # repo root

# ── Environment file ───────────────────────────────────────────────────────
# All configuration values live in chatbot/.env (the single source of truth).
# Load it explicitly so the values are seen regardless of the current working
# directory.  Process env always wins (``override=False``).
load_dotenv(PACKAGE_ROOT / ".env", override=False)

# ── Data directory + SQLite database ───────────────────────────────────────
DATA_DIR: Path = PACKAGE_ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)

# Resolve the SQLite DB path to an absolute location so it works no matter the
# current working directory (e.g. `uvicorn main:app` from inside chatbot/ vs.
# `uvicorn chatbot.main:app` from the repo root). A relative CHAT_AGENT_DB_PATH
# (such as "chatbot/data/employee_analytics.db") is anchored at the repo root,
# matching the convention used in chatbot/.env.
_db_env = os.getenv("CHAT_AGENT_DB_PATH")
if _db_env:
    _db_path = Path(_db_env)
    if not _db_path.is_absolute():
        _db_path = (WORKSPACE_ROOT / _db_path).resolve()
    DB_PATH: str = str(_db_path)
else:
    DB_PATH: str = str(DATA_DIR / "employee_analytics.db")

# ── Logging ────────────────────────────────────────────────────────────────
LOG_DIR: Path = PACKAGE_ROOT / "logs"
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").upper()

# ── Azure SQL (vzw.rep_pivoted data source) ────────────────────────────────
AZURE_SQL_SERVER: str = os.getenv("AZURE_SQL_SERVER", "")
AZURE_SQL_DATABASE: str = os.getenv("AZURE_SQL_DATABASE", "")
AZURE_SQL_PORT: str = os.getenv("AZURE_SQL_PORT", "")
AZURE_SQL_DRIVER: str = os.getenv("AZURE_SQL_DRIVER", "")

# Schema-qualified table the Azure SQL data source exposes.
REP_TABLE: str = os.getenv("REP_TABLE", "")

# ── Data-source mode (easy flag) ───────────────────────────────────────────
# Force which data source(s) the chat agent fetches from. Set SOURCE_MODE in
# the environment / .env:
#   "auto"   → score-based routing: pick SQLite or Azure SQL per question (default)
#   "sqlite" → always fetch from local SQLite only
#   "azure"  → always fetch from Azure SQL only (falls back to SQLite if Azure
#              is not configured)
#   "both"   → fetch from BOTH sources and merge the rows
_VALID_SOURCE_MODES = {"auto", "sqlite", "azure", "both"}
SOURCE_MODE: str = os.getenv("SOURCE_MODE", "auto").strip().lower()
if SOURCE_MODE not in _VALID_SOURCE_MODES:
    SOURCE_MODE = "auto"

# ── Azure OpenAI (GPT-5.4 nano reasoning model) ────────────────────────────
# All values (including the API key) are read from chatbot/.env / the process
# env — never hard-code them here.  GPT-5.4 nano is called through the Azure
# OpenAI v1 API, which needs no api-version: requests go to the resource's
# ``/openai/v1/`` base URL via the standard OpenAI client.
LLM_API_KEY: str = os.getenv("REASONING_MODEL_APIKEY", "")
LLM_ENDPOINT: str = os.getenv("REASONING_MODEL_ENDPOINT", "")
LLM_DEPLOYMENT: str = os.getenv("REASONING_MODEL_DEPLOYMENT", "")

# Base URL for the v1 API.  Use REASONING_MODEL_BASE_URL if provided, otherwise
# derive it from the resource host (``<scheme>://<host>/openai/v1/``).  Deriving
# from the host root (rather than appending to the raw endpoint) keeps it correct
# for both bare resource endpoints and Foundry project endpoints that carry an
# ``/api/projects/...`` path.
def _derive_base_url(endpoint: str) -> str:
    if not endpoint:
        return ""
    parsed = urlparse(endpoint)
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}/openai/v1/"
    return endpoint.rstrip("/") + "/openai/v1/"


LLM_BASE_URL: str = os.getenv("REASONING_MODEL_BASE_URL", "") or _derive_base_url(LLM_ENDPOINT)


# ═══════════════════════════════════════════════════════════════════════════
# LOGGING — ``chatbot`` package logger (timed-rotating file + console)
# ═══════════════════════════════════════════════════════════════════════════
LOG_DIR.mkdir(exist_ok=True)
_LOG_FILE = LOG_DIR / "chatbot.log"
_LOG_FORMAT = (
    "%(asctime)s | %(levelname)-8s | %(name)s | %(funcName)s | L%(lineno)d | %(message)s"
)
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
_NOISY_LOGGERS = ("azure", "openai", "httpx", "httpcore", "urllib3")


def _build_root_logger() -> logging.Logger:
    """Build the ``chatbot`` parent logger once (file + console handlers)."""
    root = logging.getLogger("chatbot")
    root.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
    if root.handlers:          # avoid duplicate handlers on hot-reload
        return root

    fh = TimedRotatingFileHandler(
        filename=str(_LOG_FILE), when="midnight", interval=1,
        backupCount=7, encoding="utf-8", utc=False,
    )
    fh.suffix = "%Y-%m-%d"
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT))

    ch = logging.StreamHandler()
    ch.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
    ch.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT))

    root.addHandler(fh)
    root.addHandler(ch)
    for _name in _NOISY_LOGGERS:
        logging.getLogger(_name).setLevel(logging.WARNING)
    return root


_ROOT_LOGGER = _build_root_logger()


def get_logger(name: str) -> logging.Logger:
    """Return a child logger under the ``chatbot`` namespace (e.g. ``__name__``)."""
    return logging.getLogger(name)
