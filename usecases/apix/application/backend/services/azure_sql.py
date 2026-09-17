"""
services/azure_sql.py — Application-specific Azure SQL connection helpers.
=====================================================================

Provides a separate Azure SQL connection implementation for the Streamlit
application, using application-specific environment variable names.
"""

from __future__ import annotations

import os
import struct
import threading
import time
import logging

from dotenv import load_dotenv
from backend.config.settings import Settings

logger = logging.getLogger(__name__)

# Load application dotenv values if not already loaded.
load_dotenv()

SQL_COPT_SS_ACCESS_TOKEN = 1256
_TOKEN_SCOPE = "https://database.windows.net/.default"

_LOGIN_TIMEOUT: int = int(os.getenv("APP_AZURE_SQL_LOGIN_TIMEOUT", "90"))
_QUERY_TIMEOUT: int = int(os.getenv("APP_AZURE_SQL_QUERY_TIMEOUT", "0"))

_credential = None
_conn = None
_token_expires_on: float = 0.0
_lock = threading.Lock()
_configured_cache: bool | None = None

# Cached, packed AAD access-token struct shared by every connection. Acquiring a
# token can be expensive (AzureCliCredential shells out to ``az`` on each call),
# so it is packed once and reused until shortly before expiry.
_token_struct_cache: bytes | None = None
_token_struct_expires_on: float = 0.0
_token_struct_lock = threading.Lock()
# Refresh the cached token this many seconds before it actually expires.
_TOKEN_REFRESH_SKEW: int = 300


def connection_string() -> str:
    """Build the pyodbc connection string for the application Azure SQL source."""
    return (
        f"DRIVER={{{Settings.APP_AZURE_SQL_DRIVER}}};"
        f"SERVER={Settings.APP_AZURE_SQL_SERVER};"
        f"PORT={Settings.APP_AZURE_SQL_PORT};"
        f"DATABASE={Settings.APP_AZURE_SQL_DATABASE}"
    )


def _get_credential():
    global _credential
    if _credential is None:
        from azure.identity import DefaultAzureCredential

        _credential = DefaultAzureCredential()
    return _credential


def _token_struct() -> bytes:
    """Return a cached, pyodbc-ready AAD access-token struct, refreshing as needed.

    The access token is acquired once and the packed struct is reused across every
    connection until shortly before it expires. Without this cache each new
    connection would re-invoke the credential — with ``AzureCliCredential`` that
    shells out to ``az account get-access-token`` on every call, which is slow and
    serializes/times out when many connections open concurrently. Double-checked
    locking keeps concurrent callers to a single token acquisition.
    """
    global _token_struct_cache, _token_struct_expires_on
    now = time.time()
    if _token_struct_cache is not None and now < _token_struct_expires_on - _TOKEN_REFRESH_SKEW:
        return _token_struct_cache
    with _token_struct_lock:
        now = time.time()
        if _token_struct_cache is not None and now < _token_struct_expires_on - _TOKEN_REFRESH_SKEW:
            return _token_struct_cache
        token = _get_credential().get_token(_TOKEN_SCOPE)
        token_bytes = token.token.encode("utf-16-le")
        _token_struct_cache = struct.pack(f"<I{len(token_bytes)}s", len(token_bytes), token_bytes)
        _token_struct_expires_on = float(token.expires_on)
        logger.info(
            "app azure_sql → AAD token acquired and cached (expires_on=%s)",
            int(_token_struct_expires_on),
        )
        return _token_struct_cache


def is_configured() -> bool:
    """Return True when the application Azure SQL settings are configured."""
    global _configured_cache
    if _configured_cache is not None:
        return _configured_cache

    if not (Settings.APP_AZURE_SQL_SERVER and Settings.APP_AZURE_SQL_DATABASE and Settings.APP_AZURE_SQL_PORT and Settings.APP_AZURE_SQL_DRIVER):
        logger.warning("APP Azure SQL settings not configured")
        _configured_cache = False
        return False

    try:
        _token_struct()
        _configured_cache = True
        logger.info("app azure_sql → credential probe OK")
    except Exception as exc:
        _configured_cache = False
        logger.warning("app azure_sql → credential probe failed: %s", exc)
    return _configured_cache


def _new_connection():
    import pyodbc

    logger.info(
        "app azure_sql → connecting to %s/%s via AAD token (login_timeout=%ds)",
        Settings.APP_AZURE_SQL_SERVER,
        Settings.APP_AZURE_SQL_DATABASE,
        _LOGIN_TIMEOUT,
    )
    conn = pyodbc.connect(
        connection_string(),
        attrs_before={SQL_COPT_SS_ACCESS_TOKEN: _token_struct()},
        timeout=_LOGIN_TIMEOUT,
    )
    if _QUERY_TIMEOUT > 0:
        conn.timeout = _QUERY_TIMEOUT
    logger.info("app azure_sql → connection established")
    return conn


def create_connection():
    """Return a brand-new, independent Azure SQL connection (not cached/shared).

    Used for parallel/concurrent queries: because MARS is not enabled, a single
    connection can only run one statement at a time, so each worker thread must
    own its own connection. Callers are responsible for closing it.
    """
    return _new_connection()


# --- Bounded, persistent connection pool for concurrent queries ---------------
# Concurrency needs more than one connection (MARS is off), but opening a fresh
# connection costs seconds of TLS + AAD login. Closing them after every page load
# means every load pays that cost again. This pool keeps a small set of live
# connections and hands them out / takes them back, so only the FIRST page load
# pays the connect cost and subsequent loads reuse warm connections.
_POOL_MAX: int = max(1, int(os.getenv("APP_AZURE_SQL_POOL_SIZE", os.getenv("APP_METRIC_MAX_WORKERS", "8"))))
# Connections idle longer than this (seconds) are cheaply validated on checkout,
# since a server/firewall may have dropped them; recently used ones are trusted.
_POOL_VALIDATE_AFTER: float = float(os.getenv("APP_AZURE_SQL_POOL_VALIDATE_AFTER", "30"))
_pool: list = []  # list of [conn, last_returned_ts], only IDLE connections
_pool_lock = threading.Lock()


def acquire_connection():
    """Borrow a live connection from the persistent pool (creating one if empty).

    Reuses connections across calls so repeated page loads don't repay the
    seconds-long TLS+login cost every time. Always pair with
    :func:`release_connection` (on success) or :func:`discard_connection` (if the
    connection erred) so it is returned or retired correctly.
    """
    with _pool_lock:
        entry = _pool.pop() if _pool else None
    if entry is not None:
        conn, last_used = entry
        if (time.time() - last_used) <= _POOL_VALIDATE_AFTER:
            return conn
        # Idle a while — cheaply validate; reconnect if the server dropped it.
        try:
            conn.cursor().execute("SELECT 1")
            return conn
        except Exception:
            try:
                conn.close()
            except Exception:
                pass
    return _new_connection()


def release_connection(conn) -> None:
    """Return a healthy connection to the pool for reuse (close it if pool full)."""
    if conn is None:
        return
    with _pool_lock:
        if len(_pool) < _POOL_MAX:
            _pool.append([conn, time.time()])
            return
    try:
        conn.close()
    except Exception:
        pass


def discard_connection(conn) -> None:
    """Permanently close a connection that errored — never return it to the pool."""
    if conn is None:
        return
    try:
        conn.close()
    except Exception:
        pass


def close_pool() -> None:
    """Close and drop every idle pooled connection (e.g. on credential reset)."""
    with _pool_lock:
        entries, _pool[:] = list(_pool), []
    for conn, _ in entries:
        try:
            conn.close()
        except Exception:
            pass


def prewarm_pool(count: int = 4) -> None:
    """Fire-and-forget: pre-open a few pooled connections on a background thread.

    Opening a connection costs seconds of TLS + AAD login, so the very first
    page load in a fresh process otherwise stalls while it establishes the
    connections it needs. Calling this at app startup opens ``count`` connections
    off the main thread (never blocking startup) and parks them in the pool, so
    the first real query finds warm connections waiting and skips the cold
    connect. It is safe to call more than once; excess connections are simply
    closed by :func:`release_connection` once the pool is full.
    """

    def _warm() -> None:
        try:
            if not is_configured():
                return
        except Exception:
            return
        opened = 0
        conns = []
        for _ in range(max(1, count)):
            try:
                conns.append(_new_connection())
                opened += 1
            except Exception as exc:
                logger.info("app azure_sql → prewarm stopped early: %s", exc)
                break
        for conn in conns:
            release_connection(conn)
        if opened:
            logger.info("app azure_sql → prewarmed %d pooled connection(s)", opened)

    threading.Thread(target=_warm, name="sql-pool-prewarm", daemon=True).start()


def get_connection(*, validate: bool = False):
    """Return a cached Azure SQL connection for the application."""
    global _conn, _token_expires_on
    with _lock:
        token_expired = bool(_token_expires_on) and (time.time() > _token_expires_on - 60)

        if _conn is not None and not token_expired:
            if not validate:
                return _conn
            try:
                _conn.cursor().execute("SELECT 1")
                return _conn
            except Exception:
                logger.warning("app azure_sql → cached connection stale, reconnecting")

        if _conn is not None:
            try:
                _conn.close()
            except Exception:
                pass
            _conn = None

        _conn = _new_connection()
        return _conn


def reset_connection() -> None:
    global _conn
    with _lock:
        if _conn is not None:
            try:
                _conn.close()
            except Exception:
                pass
            _conn = None
    close_pool()
