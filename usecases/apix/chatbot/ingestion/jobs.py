"""
db_crud.py — Flat wide-table schema for employee analytics chatbot.

v3: Denormalized design — 6 tables, each has employee_id, employee_name,
period directly. No dimension tables, no JOINs needed.

Tables:
    kpis           — KPI metrics (wide: one row per employee per period)
    scores         — Behavior scores + call handling (wide, prefixed columns)
    wcc_metrics    — WCC KPIs + WCC behavior scores (wide, prefixed columns)
    comparison     — Individual vs benchmark (wide, prefixed columns)
    trends         — Weekly trend data (narrow: metric_key + w0-w4)
    coaching       — Coaching tips (narrow: text data)

Usage::

    from chatbot.ingestion import get_connection, create_tables, ingest_employee_json

    conn = get_connection()
    create_tables(conn)
    ingest_employee_json(conn, json_blob)
    conn.close()
"""

import argparse
import json
import logging
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from azure.storage.blob import BlobServiceClient, ContainerClient
from dotenv import load_dotenv
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from chatbot.core.config import DB_PATH as _DEFAULT_DB
from chatbot.llm.dictionaries import (
    BEHAVIOR_KEY_MAP as _BEHAVIOR_KEY_MAP,
    BEHAVIOR_SCORE_KEYS,
    CALL_HANDLING_KEY_MAP as _CALL_HANDLING_KEY_MAP,
    CALL_HANDLING_KEYS,
    COMPARISON_KEY_MAP,
    COMPARISON_KEYS as _DICT_COMPARISON_KEYS,
    KPI_KEYS,
    WCC_BEHAVIOR_KEYS,
    WCC_KPI_KEYS,
    PSO_KPI_KEYS,
)

log = logging.getLogger(__name__)

# ── Blob ingestion environment (loaded once) ───────────────────────────────
_APP_ENV = Path(__file__).resolve().parents[2] / "application" / ".env"
load_dotenv(dotenv_path=_APP_ENV)
load_dotenv()  # CWD .env as fallback
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# ═══════════════════════════════════════════════════════════════════════════
# COLUMN DEFINITIONS
# ═══════════════════════════════════════════════════════════════════════════
# All metric key lists and JSON-key→column maps are derived from the single
# source of truth: chatbot/ingestion/kpi_dictionary.py.  Editing the dictionary
# automatically updates both the schema (DDL below) and ingestion.

# Comparison: the 5 canonical metrics from the dictionary, plus 3 legacy KPI
# comparison columns retained for backward compatibility with existing data.
_LEGACY_COMPARISON_KEYS = ["new_line_pitches", "upgrade_attempts", "save_attempts"]
COMPARISON_KEYS = _DICT_COMPARISON_KEYS + [
    k for k in _LEGACY_COMPARISON_KEYS if k not in _DICT_COMPARISON_KEYS
]

# Legacy JSON labels for the retained comparison columns (dictionary covers the rest).
_COMPARISON_KEY_MAP = {
    **COMPARISON_KEY_MAP,
    "New line Pitches": "new_line_pitches",
    "Upgrade attempts": "upgrade_attempts",
    "Save attempts": "save_attempts",
}


# ═══════════════════════════════════════════════════════════════════════════
# DDL — Table Creation
# ═══════════════════════════════════════════════════════════════════════════

def _kpi_columns_ddl() -> str:
    """Generate column definitions for the kpis table."""
    cols = []
    for k in KPI_KEYS:
        cols.append(f"    {k} REAL")
        cols.append(f"    {k}_delta REAL")
    # New prospect columns
    cols.append("    np_total INTEGER")
    cols.append("    np_converted INTEGER")
    cols.append("    np_conversion_rate REAL")
    return ",\n".join(cols)


def _scores_columns_ddl() -> str:
    """Generate column definitions for the scores table."""
    cols = []
    for k in BEHAVIOR_SCORE_KEYS:
        cols.append(f"    bs_{k} REAL")
        cols.append(f"    bs_{k}_delta REAL")
    for k in CALL_HANDLING_KEYS:
        cols.append(f"    ch_{k} REAL")
        cols.append(f"    ch_{k}_delta REAL")
    return ",\n".join(cols)


def _wcc_columns_ddl() -> str:
    """Generate column definitions for the wcc_metrics table."""
    cols = []
    for k in WCC_KPI_KEYS:
        cols.append(f"    wkpi_{k} REAL")
        cols.append(f"    wkpi_{k}_delta REAL")
    for k in WCC_BEHAVIOR_KEYS:
        cols.append(f"    wbs_{k} REAL")
        cols.append(f"    wbs_{k}_delta REAL")
    return ",\n".join(cols)


def _comparison_columns_ddl() -> str:
    """Generate column definitions for the comparison table."""
    cols = []
    for k in COMPARISON_KEYS:
        cols.append(f"    cmp_{k} REAL")
        cols.append(f"    cmp_{k}_benchmark REAL")
    return ",\n".join(cols)


def _pso_columns_ddl() -> str:
    """Generate column definitions for the pso_metrics table."""
    cols = []
    for k in PSO_KPI_KEYS:
        cols.append(f"    pkpi_{k} REAL")
        cols.append(f"    pkpi_{k}_delta REAL")
    return ",\n".join(cols)


_DDL_TEMPLATE = """
-- ═══════════════════════════════════════════
-- Table 1: kpis (wide — one row per employee per period)
-- ═══════════════════════════════════════════
CREATE TABLE IF NOT EXISTS kpis (
    employee_id INTEGER NOT NULL,
    employee_name TEXT NOT NULL,
    period TEXT NOT NULL,
    program_name TEXT,
    total_call_count INTEGER,
    overall_score REAL,
    overall_behavior_score REAL,
    summary TEXT,
{kpi_cols},
    PRIMARY KEY (employee_id, period)
);

-- ═══════════════════════════════════════════
-- Table 2: scores (wide — behavior + call handling merged)
-- ═══════════════════════════════════════════
CREATE TABLE IF NOT EXISTS scores (
    employee_id INTEGER NOT NULL,
    employee_name TEXT NOT NULL,
    period TEXT NOT NULL,
    program_name TEXT,
{scores_cols},
    PRIMARY KEY (employee_id, period)
);

-- ═══════════════════════════════════════════
-- Table 3: wcc_metrics (wide — WCC KPIs + WCC behavior scores)
-- ═══════════════════════════════════════════
CREATE TABLE IF NOT EXISTS wcc_metrics (
    employee_id INTEGER NOT NULL,
    employee_name TEXT NOT NULL,
    period TEXT NOT NULL,
    program_name TEXT,
{wcc_cols},
    PRIMARY KEY (employee_id, period)
);

-- ═══════════════════════════════════════════
-- Table 3b: pso_metrics (wide — PSO customer-care KPIs)
-- ═══════════════════════════════════════════
CREATE TABLE IF NOT EXISTS pso_metrics (
    employee_id INTEGER NOT NULL,
    employee_name TEXT NOT NULL,
    period TEXT NOT NULL,
    program_name TEXT,
{pso_cols},
    PRIMARY KEY (employee_id, period)
);

-- ═══════════════════════════════════════════
-- Table 4: comparison (wide — individual vs benchmark)
-- ═══════════════════════════════════════════
CREATE TABLE IF NOT EXISTS comparison (
    employee_id INTEGER NOT NULL,
    employee_name TEXT NOT NULL,
    period TEXT NOT NULL,
    program_name TEXT,
{cmp_cols},
    PRIMARY KEY (employee_id, period)
);

-- ═══════════════════════════════════════════
-- Table 5: trends (narrow — metric_key + weekly values)
-- ═══════════════════════════════════════════
CREATE TABLE IF NOT EXISTS trends (
    employee_id INTEGER NOT NULL,
    employee_name TEXT NOT NULL,
    period TEXT NOT NULL,
    metric_key TEXT NOT NULL,
    w0 REAL,
    w1 REAL,
    w2 REAL,
    w3 REAL,
    w4 REAL,
    PRIMARY KEY (employee_id, period, metric_key)
);

-- ═══════════════════════════════════════════
-- Table 6: coaching (narrow — tips text)
-- ═══════════════════════════════════════════
CREATE TABLE IF NOT EXISTS coaching (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id INTEGER NOT NULL,
    employee_name TEXT NOT NULL,
    period TEXT NOT NULL,
    tip_rank INTEGER,
    tip_text TEXT,
    priority TEXT,
    expected_impact TEXT,
    examples TEXT
);

-- ═══════════════════════════════════════════
-- Table 7: improvements (narrow — key improvement areas)
-- Distinct from coaching: these are the JSON ``key_improvements`` (the most
-- important areas to improve), NOT the ``coaching_tips`` recommendations.
-- ═══════════════════════════════════════════
CREATE TABLE IF NOT EXISTS improvements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id INTEGER NOT NULL,
    employee_name TEXT NOT NULL,
    period TEXT NOT NULL,
    area_rank INTEGER,
    area_text TEXT
);

-- ═══════════════════════════════════════════
-- INDEXES
-- ═══════════════════════════════════════════
CREATE INDEX IF NOT EXISTS idx_kpis_name ON kpis(employee_name);
CREATE INDEX IF NOT EXISTS idx_kpis_period ON kpis(period);
CREATE INDEX IF NOT EXISTS idx_scores_name ON scores(employee_name);
CREATE INDEX IF NOT EXISTS idx_scores_period ON scores(period);
CREATE INDEX IF NOT EXISTS idx_wcc_name ON wcc_metrics(employee_name);
CREATE INDEX IF NOT EXISTS idx_wcc_period ON wcc_metrics(period);
CREATE INDEX IF NOT EXISTS idx_pso_name ON pso_metrics(employee_name);
CREATE INDEX IF NOT EXISTS idx_pso_period ON pso_metrics(period);
CREATE INDEX IF NOT EXISTS idx_cmp_name ON comparison(employee_name);
CREATE INDEX IF NOT EXISTS idx_cmp_period ON comparison(period);
CREATE INDEX IF NOT EXISTS idx_trends_name ON trends(employee_name);
CREATE INDEX IF NOT EXISTS idx_trends_period ON trends(period);
CREATE INDEX IF NOT EXISTS idx_coaching_name ON coaching(employee_name);
CREATE INDEX IF NOT EXISTS idx_coaching_period ON coaching(period);
CREATE INDEX IF NOT EXISTS idx_improvements_name ON improvements(employee_name);
CREATE INDEX IF NOT EXISTS idx_improvements_period ON improvements(period);
"""


def _build_ddl() -> str:
    return _DDL_TEMPLATE.format(
        kpi_cols=_kpi_columns_ddl(),
        scores_cols=_scores_columns_ddl(),
        wcc_cols=_wcc_columns_ddl(),
        pso_cols=_pso_columns_ddl(),
        cmp_cols=_comparison_columns_ddl(),
    )


# ═══════════════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════════════

def get_connection(db_path: str = _DEFAULT_DB) -> sqlite3.Connection:
    """Get a SQLite connection with WAL mode and foreign keys enabled."""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def create_tables(conn: sqlite3.Connection) -> None:
    """Create all tables and indexes (idempotent)."""
    conn.executescript(_build_ddl())
    _migrate_schema(conn)
    log.info("create_tables → 8 tables created/verified")


# Additive, backward-compatible column migrations. ``CREATE TABLE IF NOT
# EXISTS`` never alters an existing table, so columns introduced after a DB was
# first created are added here. Each entry is (table, column, type).
_COLUMN_MIGRATIONS = [
    ("kpis", "summary", "TEXT"),
    ("coaching", "examples", "TEXT"),
]


def _migrate_schema(conn: sqlite3.Connection) -> None:
    """Add any new columns to pre-existing tables (no data loss)."""
    for table, column, col_type in _COLUMN_MIGRATIONS:
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")
            log.info("_migrate_schema → added %s.%s", table, column)


# ═══════════════════════════════════════════════════════════════════════════
# UTILITY FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════

def _safe_real(val: Any) -> Optional[float]:
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _safe_int(val: Any) -> Optional[int]:
    if val is None:
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


def _derive_period(date_str: str) -> str:
    """Return the reporting period as the UTC date itself (``YYYY-MM-DD``).

    The raw ``date_utc`` value is used verbatim — no ISO-week conversion — so
    the period always matches the source date.  Any time component is dropped.
    """
    try:
        return datetime.fromisoformat(date_str).date().isoformat()
    except (ValueError, TypeError):
        cleaned = (date_str or "").strip()
        return cleaned or "UNKNOWN"


def _normalize_key(key: str) -> str:
    """Convert 'Title Case Key' or 'snake_case' to normalized snake_case."""
    return key.lower().replace(" ", "_").replace("-", "_")


# ═══════════════════════════════════════════════════════════════════════════
# INGESTION — Insert one employee JSON blob
# ═══════════════════════════════════════════════════════════════════════════

def ingest_employee_json(conn: sqlite3.Connection, json_data: dict) -> str:
    """Ingest one employee-week JSON blob into all flat tables.

    Returns the period string. Entire operation is a single transaction.
    """
    employee_id: int = json_data["employeeId"]
    employee_name: str = json_data.get("employeeName", "")
    program_name: str = json_data.get("programName", "")
    date_utc: str = json_data.get("date_utc", "")
    period = _derive_period(date_utc)

    try:
        conn.execute("BEGIN")

        _upsert_kpis(conn, employee_id, employee_name, period, program_name, json_data)
        _upsert_scores(conn, employee_id, employee_name, period, program_name, json_data)
        _upsert_wcc_metrics(conn, employee_id, employee_name, period, program_name, json_data)
        _upsert_pso_metrics(conn, employee_id, employee_name, period, program_name, json_data)
        _upsert_comparison(conn, employee_id, employee_name, period, program_name, json_data)
        _insert_trends(conn, employee_id, employee_name, period, json_data)
        _insert_coaching(conn, employee_id, employee_name, period, json_data)
        _insert_improvements(conn, employee_id, employee_name, period, json_data)

        conn.execute("COMMIT")
        return period

    except Exception:
        conn.execute("ROLLBACK")
        raise


# ═══════════════════════════════════════════════════════════════════════════
# UPSERT HELPERS
# ═══════════════════════════════════════════════════════════════════════════

def _upsert_kpis(conn: sqlite3.Connection, employee_id: int,
                 employee_name: str, period: str, program_name: str,
                 data: dict) -> None:
    """Upsert one row into the kpis table (wide format)."""
    # Build column values from JSON kpis array
    values = {}
    for kpi in data.get("kpis", []):
        key = kpi.get("key", "")
        if key in KPI_KEYS:
            values[key] = _safe_real(kpi.get("value"))
            values[f"{key}_delta"] = _safe_real(kpi.get("delta"))

    # New prospect
    np_data = data.get("new_prospect", {})
    values["np_total"] = _safe_int(np_data.get("total"))
    values["np_converted"] = _safe_int(np_data.get("converted"))
    values["np_conversion_rate"] = _safe_real(np_data.get("conversion_rate"))

    # Build all columns for INSERT
    base_cols = ["employee_id", "employee_name", "period", "program_name",
                 "total_call_count", "overall_score", "overall_behavior_score", "summary"]
    base_vals = [employee_id, employee_name, period, program_name,
                 _safe_int(data.get("totalCallCount")),
                 _safe_real(data.get("overall_score")),
                 _safe_real(data.get("overall_behavior_score")),
                 data.get("summary")]

    metric_cols = []
    metric_vals = []
    for k in KPI_KEYS:
        metric_cols.append(k)
        metric_vals.append(values.get(k))
        metric_cols.append(f"{k}_delta")
        metric_vals.append(values.get(f"{k}_delta"))
    metric_cols.extend(["np_total", "np_converted", "np_conversion_rate"])
    metric_vals.extend([values.get("np_total"), values.get("np_converted"),
                        values.get("np_conversion_rate")])

    all_cols = base_cols + metric_cols
    all_vals = base_vals + metric_vals
    placeholders = ", ".join(["?"] * len(all_cols))
    col_str = ", ".join(all_cols)

    # ON CONFLICT update all metric columns
    update_parts = [f"{c} = excluded.{c}" for c in metric_cols + ["employee_name", "program_name",
                    "total_call_count", "overall_score", "overall_behavior_score", "summary"]]
    update_str = ", ".join(update_parts)

    sql = (f"INSERT INTO kpis ({col_str}) VALUES ({placeholders}) "
           f"ON CONFLICT(employee_id, period) DO UPDATE SET {update_str}")
    conn.execute(sql, all_vals)


def _upsert_scores(conn: sqlite3.Connection, employee_id: int,
                   employee_name: str, period: str, program_name: str,
                   data: dict) -> None:
    """Upsert one row into the scores table (behavior + call handling)."""
    values = {}

    # Behavior scores (JSON keys are Title Case)
    bs_data = data.get("behavior_scores", {})
    for json_key, col_key in _BEHAVIOR_KEY_MAP.items():
        obj = bs_data.get(json_key)
        if isinstance(obj, dict):
            values[f"bs_{col_key}"] = _safe_real(obj.get("score"))
            values[f"bs_{col_key}_delta"] = _safe_real(obj.get("delta"))
        elif obj is not None:
            values[f"bs_{col_key}"] = _safe_real(obj)
            values[f"bs_{col_key}_delta"] = None

    # Call handling (JSON keys are Title Case)
    ch_data = data.get("call_handling_and_soft_skills") or data.get("call_handling_and_softs_kills", {})
    for json_key, col_key in _CALL_HANDLING_KEY_MAP.items():
        obj = ch_data.get(json_key)
        if isinstance(obj, dict):
            values[f"ch_{col_key}"] = _safe_real(obj.get("score"))
            values[f"ch_{col_key}_delta"] = _safe_real(obj.get("delta"))
        elif obj is not None:
            values[f"ch_{col_key}"] = _safe_real(obj)
            values[f"ch_{col_key}_delta"] = None

    # Build INSERT
    base_cols = ["employee_id", "employee_name", "period", "program_name"]
    base_vals = [employee_id, employee_name, period, program_name]

    metric_cols = []
    metric_vals = []
    for k in BEHAVIOR_SCORE_KEYS:
        metric_cols.append(f"bs_{k}")
        metric_vals.append(values.get(f"bs_{k}"))
        metric_cols.append(f"bs_{k}_delta")
        metric_vals.append(values.get(f"bs_{k}_delta"))
    for k in CALL_HANDLING_KEYS:
        metric_cols.append(f"ch_{k}")
        metric_vals.append(values.get(f"ch_{k}"))
        metric_cols.append(f"ch_{k}_delta")
        metric_vals.append(values.get(f"ch_{k}_delta"))

    all_cols = base_cols + metric_cols
    all_vals = base_vals + metric_vals
    placeholders = ", ".join(["?"] * len(all_cols))
    col_str = ", ".join(all_cols)

    update_parts = [f"{c} = excluded.{c}" for c in metric_cols + ["employee_name", "program_name"]]
    update_str = ", ".join(update_parts)

    sql = (f"INSERT INTO scores ({col_str}) VALUES ({placeholders}) "
           f"ON CONFLICT(employee_id, period) DO UPDATE SET {update_str}")
    conn.execute(sql, all_vals)


def _upsert_wcc_metrics(conn: sqlite3.Connection, employee_id: int,
                        employee_name: str, period: str, program_name: str,
                        data: dict) -> None:
    """Upsert one row into the wcc_metrics table."""
    values = {}

    # WCC KPIs (JSON keys are snake_case)
    for wk in data.get("wcc_kpis", []):
        key = wk.get("key", "")
        if key in WCC_KPI_KEYS:
            values[f"wkpi_{key}"] = _safe_real(wk.get("score", wk.get("value")))
            values[f"wkpi_{key}_delta"] = _safe_real(wk.get("delta"))

    # WCC Behavior scores (JSON keys are snake_case)
    wcc_bs = data.get("wcc_behavior_scores", {})
    for key in WCC_BEHAVIOR_KEYS:
        obj = wcc_bs.get(key)
        if isinstance(obj, dict):
            values[f"wbs_{key}"] = _safe_real(obj.get("score"))
            values[f"wbs_{key}_delta"] = _safe_real(obj.get("delta"))
        elif obj is not None:
            values[f"wbs_{key}"] = _safe_real(obj)
            values[f"wbs_{key}_delta"] = None

    # Skip entirely if no WCC data
    if not values:
        return

    base_cols = ["employee_id", "employee_name", "period", "program_name"]
    base_vals = [employee_id, employee_name, period, program_name]

    metric_cols = []
    metric_vals = []
    for k in WCC_KPI_KEYS:
        metric_cols.append(f"wkpi_{k}")
        metric_vals.append(values.get(f"wkpi_{k}"))
        metric_cols.append(f"wkpi_{k}_delta")
        metric_vals.append(values.get(f"wkpi_{k}_delta"))
    for k in WCC_BEHAVIOR_KEYS:
        metric_cols.append(f"wbs_{k}")
        metric_vals.append(values.get(f"wbs_{k}"))
        metric_cols.append(f"wbs_{k}_delta")
        metric_vals.append(values.get(f"wbs_{k}_delta"))

    all_cols = base_cols + metric_cols
    all_vals = base_vals + metric_vals
    placeholders = ", ".join(["?"] * len(all_cols))
    col_str = ", ".join(all_cols)

    update_parts = [f"{c} = excluded.{c}" for c in metric_cols + ["employee_name", "program_name"]]
    update_str = ", ".join(update_parts)

    sql = (f"INSERT INTO wcc_metrics ({col_str}) VALUES ({placeholders}) "
           f"ON CONFLICT(employee_id, period) DO UPDATE SET {update_str}")
    conn.execute(sql, all_vals)


def _upsert_pso_metrics(conn: sqlite3.Connection, employee_id: int,
                        employee_name: str, period: str, program_name: str,
                        data: dict) -> None:
    """Upsert one row into the pso_metrics table.

    PSO agents emit their KPIs flat in ``kpi_groups`` (each ``{key, score,
    delta}``); older blobs may carry them in ``kpis`` (``{key, value, delta}``).
    Both shapes are accepted. Skips entirely when no PSO KPI is present, so
    telesales / WCC agents never get an empty pso_metrics row.
    """
    values = {}

    def _absorb(items, score_field):
        for it in items or []:
            key = it.get("key", "")
            if key in PSO_KPI_KEYS:
                values[f"pkpi_{key}"] = _safe_real(it.get(score_field, it.get("value")))
                values[f"pkpi_{key}_delta"] = _safe_real(it.get("delta"))

    _absorb(data.get("kpi_groups", []), "score")
    # Fill any gaps from the flat kpis array (does not overwrite kpi_groups).
    for kpi in data.get("kpis", []):
        key = kpi.get("key", "")
        if key in PSO_KPI_KEYS and f"pkpi_{key}" not in values:
            values[f"pkpi_{key}"] = _safe_real(kpi.get("value"))
            values[f"pkpi_{key}_delta"] = _safe_real(kpi.get("delta"))

    # Skip entirely if no PSO data.
    if not values:
        return

    base_cols = ["employee_id", "employee_name", "period", "program_name"]
    base_vals = [employee_id, employee_name, period, program_name]

    metric_cols = []
    metric_vals = []
    for k in PSO_KPI_KEYS:
        metric_cols.append(f"pkpi_{k}")
        metric_vals.append(values.get(f"pkpi_{k}"))
        metric_cols.append(f"pkpi_{k}_delta")
        metric_vals.append(values.get(f"pkpi_{k}_delta"))

    all_cols = base_cols + metric_cols
    all_vals = base_vals + metric_vals
    placeholders = ", ".join(["?"] * len(all_cols))
    col_str = ", ".join(all_cols)

    update_parts = [f"{c} = excluded.{c}" for c in metric_cols + ["employee_name", "program_name"]]
    update_str = ", ".join(update_parts)

    sql = (f"INSERT INTO pso_metrics ({col_str}) VALUES ({placeholders}) "
           f"ON CONFLICT(employee_id, period) DO UPDATE SET {update_str}")
    conn.execute(sql, all_vals)


def _upsert_comparison(conn: sqlite3.Connection, employee_id: int,
                       employee_name: str, period: str, program_name: str,
                       data: dict) -> None:
    """Upsert one row into the comparison table."""
    values = {}

    for comp in data.get("comparison", []):
        raw_key = comp.get("key") or comp.get("metric_key") or comp.get("metric", "")
        col_key = _COMPARISON_KEY_MAP.get(raw_key, _normalize_key(raw_key))
        if col_key not in COMPARISON_KEYS:
            continue
        individual = comp.get("individual") if comp.get("individual") is not None else comp.get("individual_value")
        benchmark = comp.get("benchmark") if comp.get("benchmark") is not None else (
            comp.get("benchmark_value") if comp.get("benchmark_value") is not None else comp.get("teamAvg")
        )
        values[f"cmp_{col_key}"] = _safe_real(individual)
        values[f"cmp_{col_key}_benchmark"] = _safe_real(benchmark)

    # Skip if no comparison data
    if not values:
        return

    base_cols = ["employee_id", "employee_name", "period", "program_name"]
    base_vals = [employee_id, employee_name, period, program_name]

    metric_cols = []
    metric_vals = []
    for k in COMPARISON_KEYS:
        metric_cols.append(f"cmp_{k}")
        metric_vals.append(values.get(f"cmp_{k}"))
        metric_cols.append(f"cmp_{k}_benchmark")
        metric_vals.append(values.get(f"cmp_{k}_benchmark"))

    all_cols = base_cols + metric_cols
    all_vals = base_vals + metric_vals
    placeholders = ", ".join(["?"] * len(all_cols))
    col_str = ", ".join(all_cols)

    update_parts = [f"{c} = excluded.{c}" for c in metric_cols + ["employee_name", "program_name"]]
    update_str = ", ".join(update_parts)

    sql = (f"INSERT INTO comparison ({col_str}) VALUES ({placeholders}) "
           f"ON CONFLICT(employee_id, period) DO UPDATE SET {update_str}")
    conn.execute(sql, all_vals)


def _insert_trends(conn: sqlite3.Connection, employee_id: int,
                   employee_name: str, period: str,
                   data: dict) -> None:
    """Insert trend rows (one per metric) with w0-w4 weekly values."""
    # Delete existing trends for this employee+period (replace strategy)
    conn.execute("DELETE FROM trends WHERE employee_id = ? AND period = ?",
                 (employee_id, period))

    trends = data.get("trends", {})
    rows = []

    for metric_key, trend_points in trends.items():
        week_values = [None, None, None, None, None]  # w0, w1, w2, w3, w4

        if isinstance(trend_points, dict):
            for offset_label, value in trend_points.items():
                idx = _parse_offset(offset_label)
                if 0 <= idx <= 4:
                    week_values[idx] = _safe_real(value)
        elif isinstance(trend_points, list):
            for i, entry in enumerate(trend_points):
                if i > 4:
                    break
                if isinstance(entry, dict):
                    idx = _parse_offset(entry.get("x", entry.get("label", f"W-{i}")))
                    val = entry.get("y") if "y" in entry else entry.get("value")
                else:
                    idx = i
                    val = entry
                if 0 <= idx <= 4:
                    week_values[idx] = _safe_real(val)

        rows.append((employee_id, employee_name, period, metric_key,
                     week_values[0], week_values[1], week_values[2],
                     week_values[3], week_values[4]))

    if rows:
        conn.executemany(
            "INSERT INTO trends (employee_id, employee_name, period, metric_key, "
            "w0, w1, w2, w3, w4) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )


def _insert_coaching(conn: sqlite3.Connection, employee_id: int,
                     employee_name: str, period: str,
                     data: dict) -> None:
    """Insert coaching tips for this employee+period."""
    # Delete existing coaching for this employee+period (replace strategy)
    conn.execute("DELETE FROM coaching WHERE employee_id = ? AND period = ?",
                 (employee_id, period))

    tips = data.get("coaching_tips", [])
    rows = []
    for rank, tip in enumerate(tips, start=1):
        examples = tip.get("examples")
        examples_str = json.dumps(examples, default=str) if examples is not None else None
        rows.append((
            employee_id, employee_name, period, rank,
            tip.get("tip"), tip.get("priority"), tip.get("expected_impact"),
            examples_str,
        ))

    if rows:
        conn.executemany(
            "INSERT INTO coaching (employee_id, employee_name, period, "
            "tip_rank, tip_text, priority, expected_impact, examples) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )


def _insert_improvements(conn: sqlite3.Connection, employee_id: int,
                         employee_name: str, period: str,
                         data: dict) -> None:
    """Insert the JSON ``key_improvements`` areas for this employee+period.

    These are stored separately from ``coaching`` because improvement areas
    ("the most important areas to improve next") are a distinct concept from
    coaching recommendations/tips.  Each area is a free-text string; rank
    preserves the source ordering (most important first).
    """
    # Replace strategy — clear this employee+period before re-inserting.
    conn.execute("DELETE FROM improvements WHERE employee_id = ? AND period = ?",
                 (employee_id, period))

    areas = data.get("key_improvements", []) or []
    rows = []
    for rank, area in enumerate(areas, start=1):
        # Areas are usually plain strings; tolerate dicts with a 'text'/'area' key.
        if isinstance(area, dict):
            text = area.get("text") or area.get("area") or area.get("description")
        else:
            text = area
        if text is None:
            continue
        rows.append((employee_id, employee_name, period, rank, str(text)))

    if rows:
        conn.executemany(
            "INSERT INTO improvements (employee_id, employee_name, period, "
            "area_rank, area_text) VALUES (?, ?, ?, ?, ?)",
            rows,
        )


def _parse_offset(label: str) -> int:
    """Parse 'W-1', 'W-2' etc. into an integer offset."""
    try:
        return int(str(label).replace("W-", "").replace("W", "").strip())
    except (ValueError, AttributeError):
        return 0


# ═══════════════════════════════════════════════════════════════════════════
# BLOB INGESTION — Azure Blob → SQLite
# ═══════════════════════════════════════════════════════════════════════════
def _get_container_client() -> ContainerClient:
    """Build a ContainerClient from env vars."""
    conn_str = os.getenv("AZURE_BLOB_CONNECTION_STRING")
    container = os.getenv("AZURE_BLOB_CONTAINER")
    if not conn_str:
        log.error("_get_container_client → AZURE_BLOB_CONNECTION_STRING not set")
        raise EnvironmentError(
            "AZURE_BLOB_CONNECTION_STRING not found. "
            "Set it in application/.env or as an environment variable."
        )
    if not container:
        log.error("_get_container_client → AZURE_BLOB_CONTAINER not set")
        raise EnvironmentError(
            "AZURE_BLOB_CONTAINER not found. "
            "Set it in application/.env or as an environment variable."
        )
    service = BlobServiceClient.from_connection_string(conn_str)
    log.info("_get_container_client → connected to container='%s'", container)
    return service.get_container_client(container)


def list_json_files(client: ContainerClient, folder_path: str) -> list[str]:
    """List all ``.json`` blob names under *folder_path*."""
    blobs = client.list_blobs(name_starts_with=folder_path)
    names = sorted(b.name for b in blobs if b.name.lower().endswith(".json"))
    log.info("list_json_files → found %d JSON files in '%s'", len(names), folder_path)
    return names


def read_json_from_blob(client: ContainerClient, blob_name: str) -> dict | None:
    """Download a single blob and parse as JSON (None for empty/invalid/non-dict)."""
    blob_client = client.get_blob_client(blob_name)
    raw = blob_client.download_blob().readall()
    if not raw or not raw.strip():
        log.warning("read_json_from_blob → empty blob: %s", blob_name)
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        log.warning("read_json_from_blob → invalid JSON: %s — %s", blob_name, exc)
        return None
    if not isinstance(data, dict):
        log.warning("read_json_from_blob → non-dict blob: %s", blob_name)
        return None
    return data


def discover_week_folders(client: ContainerClient) -> list[str]:
    """Return sorted list of date-formatted top-level folders in the container."""
    folders: set[str] = set()
    for blob in client.list_blobs():
        parts = blob.name.split("/")
        if len(parts) >= 2 and _DATE_RE.match(parts[0]):
            folders.add(parts[0])
    result = sorted(folders)
    log.info("discover_week_folders → found %d folders: %s", len(result), result)
    return result


def ingest_folder(
    client: ContainerClient,
    folder_path: str,
    conn: sqlite3.Connection,
) -> dict:
    """Read every JSON in *folder_path* and push into the SQLite connection.

    Skips empty files, invalid JSON, and blobs missing ``employeeId``.  The
    caller creates the DB/tables beforehand and closes the connection after.
    """
    blob_names = list_json_files(client, folder_path)
    if not blob_names:
        log.warning("ingest_folder → no JSON files in '%s'", folder_path)
        return {"total": 0, "success": 0, "skipped": 0, "failed": 0, "errors": []}

    log.info("ingest_folder → START folder='%s', files=%d", folder_path, len(blob_names))
    successes: list[int] = []
    errors: list[dict] = []
    skipped = 0

    for idx, blob_name in enumerate(blob_names, start=1):
        try:
            json_data = read_json_from_blob(client, blob_name)
            if json_data is None:
                skipped += 1
                continue
            if "employeeId" not in json_data:
                log.warning("[%d/%d] Skipping %s — missing 'employeeId'",
                            idx, len(blob_names), blob_name)
                skipped += 1
                continue
            report_id = ingest_employee_json(conn, json_data)
            successes.append(report_id)
            log.info("[%d/%d] ✓ %s → report_id=%d",
                     idx, len(blob_names), blob_name, report_id)
        except Exception as exc:
            log.error("[%d/%d] ✗ %s — %s", idx, len(blob_names), blob_name, exc)
            errors.append({"file": blob_name, "error": str(exc)})

    log.info("ingest_folder → DONE folder='%s': success=%d, skipped=%d, failed=%d",
             folder_path, len(successes), skipped, len(errors))
    return {
        "total": len(blob_names),
        "success": len(successes),
        "skipped": skipped,
        "failed": len(errors),
        "report_ids": successes,
        "errors": errors,
    }


# ═══════════════════════════════════════════════════════════════════════════
# DATABASE LIFECYCLE — recreate, local-folder ingest, delete-by-period
# ═══════════════════════════════════════════════════════════════════════════

# Every queryable table carries ``employee_id`` + ``period`` directly, so a
# period-scoped delete simply hits each of them.
_ALL_TABLES = ["kpis", "scores", "wcc_metrics", "comparison", "trends", "coaching", "improvements"]


def _refresh_query_caches(db_path: str = _DEFAULT_DB) -> None:
    """Reload the NL→SQL schema/surname caches in the query layer (best-effort).

    The chat agent builds its schema and known-surname caches once at import.
    After the DB is recreated, ingested, or pruned we trigger a reload so
    natural-language queries immediately see the current data.
    """
    try:
        from chatbot.sources import sqlite_source
        sqlite_source.refresh_caches(db_path)
    except Exception as exc:  # never let a cache refresh break the request
        log.warning("_refresh_query_caches → refresh failed (non-fatal): %s", exc)


def recreate_database(db_path: str = _DEFAULT_DB) -> dict[str, Any]:
    """Archive the current SQLite DB (timestamped) and create a fresh empty one.

    Steps:
      1. Checkpoint the WAL into the main file so the archive is self-contained.
      2. Rename the existing DB to ``<name>.<YYYYmmdd_HHMMSS><ext>`` and drop the
         leftover ``-wal`` / ``-shm`` side files.
      3. Create a brand-new DB at the *same* path with the full schema.
      4. Refresh the query-layer caches so connections point at the new DB.

    Returns ``{"new_db": ..., "archived": ... | None}``.
    """
    db = Path(db_path)
    archived: Optional[str] = None

    if db.exists():
        try:
            c = sqlite3.connect(db_path)
            c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            c.close()
        except sqlite3.Error as exc:
            log.warning("recreate_database → WAL checkpoint failed (%s)", exc)

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        archive = db.with_name(f"{db.stem}.{ts}{db.suffix}")
        db.replace(archive)
        archived = str(archive)
        log.info("recreate_database → archived old DB → %s", archive.name)

        for side in (f"{db_path}-wal", f"{db_path}-shm"):
            p = Path(side)
            if p.exists():
                try:
                    p.unlink()
                except OSError as exc:
                    log.warning("recreate_database → could not remove %s (%s)", side, exc)

    conn = get_connection(db_path)
    try:
        create_tables(conn)
    finally:
        conn.close()
    log.info("recreate_database → fresh DB created at %s", db_path)

    _refresh_query_caches(db_path)
    return {"new_db": db_path, "archived": archived}


def ingest_local_folder(folder_path: str, conn: sqlite3.Connection) -> dict[str, Any]:
    """Ingest every ``*.json`` file under a local *folder_path* (recursively).

    Mirrors :func:`ingest_folder` but reads from the local filesystem instead of
    Azure Blob. Each file is grouped into the per-metric tables keyed by
    ``employee_id`` + ``period`` exactly as the JSON provides them (the time
    period is used verbatim — see :func:`_derive_period`). Files that are not a
    JSON object or are missing ``employeeId`` are skipped.
    """
    base = Path(folder_path)
    if not base.exists() or not base.is_dir():
        raise FileNotFoundError(f"Folder not found or not a directory: {folder_path}")

    files = sorted(base.rglob("*.json"))
    log.info("ingest_local_folder → folder='%s': %d JSON file(s)", folder_path, len(files))

    successes: list[str] = []
    skipped = 0
    errors: list[dict[str, str]] = []

    for path in files:
        rel = str(path.relative_to(base))
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            errors.append({"file": rel, "error": f"read/parse failed: {exc}"})
            continue

        if not isinstance(data, dict) or "employeeId" not in data:
            skipped += 1
            log.info("ingest_local_folder → skip '%s' (no employeeId)", rel)
            continue

        try:
            ingest_employee_json(conn, data)
            successes.append(str(data.get("employeeId")))
        except Exception as exc:
            errors.append({"file": rel, "error": str(exc)})
            log.error("ingest_local_folder → FAIL '%s': %s", rel, exc)

    log.info("ingest_local_folder → DONE folder='%s': success=%d, skipped=%d, failed=%d",
             folder_path, len(successes), skipped, len(errors))
    return {
        "total": len(files),
        "success": len(successes),
        "skipped": skipped,
        "failed": len(errors),
        "report_ids": successes,
        "errors": errors,
    }


def delete_by_period(conn: sqlite3.Connection, period: str) -> dict[str, int]:
    """Delete every row for *period* across all tables (single transaction).

    Returns a per-table count of deleted rows.
    """
    period = (period or "").strip()
    if not period:
        raise ValueError("period must be a non-empty string")

    deleted: dict[str, int] = {}
    conn.execute("BEGIN")
    try:
        for table in _ALL_TABLES:
            cur = conn.execute(f"DELETE FROM {table} WHERE period = ?", (period,))
            deleted[table] = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    total = sum(deleted.values())
    log.info("delete_by_period → period='%s' removed %d row(s): %s",
             period, total, deleted)
    return deleted


# ═══════════════════════════════════════════════════════════════════════════
# FASTAPI ROUTER — POST /ingest (included by chatbot.main)
# ═══════════════════════════════════════════════════════════════════════════
router = APIRouter(tags=["ingestion"])


class IngestRequest(BaseModel):
    week_folder: str | None = Field(
        default=None,
        description="Date folder to ingest (e.g. '2026-04-17'). "
                    "Omit to auto-discover and ingest all available folders.",
    )


class FolderResult(BaseModel):
    folder: str
    total: int
    success: int
    skipped: int
    failed: int
    errors: list[dict]


class IngestResponse(BaseModel):
    folders_processed: int
    total_success: int
    total_skipped: int
    total_failed: int
    elapsed_ms: float
    results: list[FolderResult]


@router.post("/ingest", response_model=IngestResponse)
def ingest_week(request: IngestRequest | None = None):
    """Ingest employee JSONs from one or all week folders in Azure Blob Storage."""
    log.info("ingest_week → START week_folder=%s",
             request.week_folder if request else "(auto-discover)")
    start = time.perf_counter()

    try:
        client = _get_container_client()
    except EnvironmentError as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    week_folder = request.week_folder if request else None
    if week_folder:
        week_folders = [week_folder]
    else:
        week_folders = discover_week_folders(client)

    if not week_folders:
        log.warning("ingest_week → no folders found")
        raise HTTPException(status_code=404, detail="No week folders found in blob container.")

    log.info("Ingesting %d folder(s): %s", len(week_folders), ", ".join(week_folders))

    conn = get_connection(_DEFAULT_DB)
    create_tables(conn)

    results: list[FolderResult] = []
    total_success = 0
    total_skipped = 0
    total_failed = 0

    try:
        for folder in week_folders:
            log.info("── %s ──", folder)
            result = ingest_folder(client, folder, conn)
            fr = FolderResult(
                folder=folder,
                total=result["total"],
                success=result["success"],
                skipped=result["skipped"],
                failed=result["failed"],
                errors=result.get("errors", []),
            )
            results.append(fr)
            total_success += fr.success
            total_skipped += fr.skipped
            total_failed += fr.failed
    finally:
        conn.close()

    elapsed_ms = (time.perf_counter() - start) * 1000
    log.info("Ingestion complete — success=%d skipped=%d failed=%d (%.0fms)",
             total_success, total_skipped, total_failed, elapsed_ms)

    _refresh_query_caches(_DEFAULT_DB)

    return IngestResponse(
        folders_processed=len(week_folders),
        total_success=total_success,
        total_skipped=total_skipped,
        total_failed=total_failed,
        elapsed_ms=round(elapsed_ms, 1),
        results=results,
    )


# ───────────────────────────────────────────────────────────────────────────
# POST /database/recreate — archive the DB and start fresh
# ───────────────────────────────────────────────────────────────────────────
class RecreateResponse(BaseModel):
    new_db: str
    archived: str | None
    message: str


@router.post("/database/recreate", response_model=RecreateResponse)
def recreate_db_endpoint():
    """Archive the current SQLite DB (timestamped) and create a fresh empty one.

    The query-layer caches are refreshed afterwards so the chat agent points at
    the new database without a restart.
    """
    log.info("recreate_db_endpoint → START")
    try:
        info = recreate_database(_DEFAULT_DB)
    except Exception as exc:
        log.error("recreate_db_endpoint → FAILED: %s", exc)
        raise HTTPException(status_code=500, detail=f"Failed to recreate database: {exc}")

    archived = info["archived"]
    msg = (
        f"Fresh database created at {info['new_db']}; "
        + (f"previous DB archived as {Path(archived).name}." if archived
           else "no previous database existed.")
    )
    log.info("recreate_db_endpoint → DONE (%s)", msg)
    return RecreateResponse(new_db=info["new_db"], archived=archived, message=msg)


# ───────────────────────────────────────────────────────────────────────────
# POST /ingest_local — ingest a local folder of employee JSON files
# ───────────────────────────────────────────────────────────────────────────
class LocalIngestRequest(BaseModel):
    folder_path: str = Field(
        ...,
        description="Absolute or relative path to a local folder containing "
                    "employee JSON files (searched recursively).",
    )
    recreate: bool = Field(
        default=False,
        description="If true, archive + recreate the DB before ingesting.",
    )


@router.post("/ingest_local", response_model=IngestResponse)
def ingest_local_endpoint(request: LocalIngestRequest):
    """Ingest every JSON file under a local folder into SQLite.

    Tables are grouped by metric and keyed on ``employee_id`` + ``period`` taken
    verbatim from each JSON's date. The schema is created automatically if the
    DB is empty, and the query caches are refreshed when ingestion completes.
    """
    log.info("ingest_local_endpoint → START folder='%s' recreate=%s",
             request.folder_path, request.recreate)
    start = time.perf_counter()

    if request.recreate:
        try:
            recreate_database(_DEFAULT_DB)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Recreate failed: {exc}")

    conn = get_connection(_DEFAULT_DB)
    create_tables(conn)  # ensure schema present ("do the needful")
    try:
        result = ingest_local_folder(request.folder_path, conn)
    except FileNotFoundError as exc:
        conn.close()
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:
        conn.close()
        log.error("ingest_local_endpoint → FAILED: %s", exc)
        raise HTTPException(status_code=500, detail=f"Ingestion failed: {exc}")
    finally:
        if conn:
            try:
                conn.close()
            except Exception:
                pass

    _refresh_query_caches(_DEFAULT_DB)

    elapsed_ms = (time.perf_counter() - start) * 1000
    fr = FolderResult(
        folder=request.folder_path,
        total=result["total"],
        success=result["success"],
        skipped=result["skipped"],
        failed=result["failed"],
        errors=result.get("errors", []),
    )
    log.info("ingest_local_endpoint → DONE success=%d skipped=%d failed=%d (%.0fms)",
             fr.success, fr.skipped, fr.failed, elapsed_ms)
    return IngestResponse(
        folders_processed=1,
        total_success=fr.success,
        total_skipped=fr.skipped,
        total_failed=fr.failed,
        elapsed_ms=round(elapsed_ms, 1),
        results=[fr],
    )


# ───────────────────────────────────────────────────────────────────────────
# DELETE /records/{period} — drop all rows for one time period
# ───────────────────────────────────────────────────────────────────────────
class DeletePeriodResponse(BaseModel):
    period: str
    deleted: dict[str, int]
    total_deleted: int
    message: str


@router.delete("/records/{period}", response_model=DeletePeriodResponse)
def delete_period_endpoint(period: str):
    """Delete every record for *period* (e.g. ``2026-04-17``) across all tables."""
    log.info("delete_period_endpoint → START period='%s'", period)
    conn = get_connection(_DEFAULT_DB)
    try:
        deleted = delete_by_period(conn, period)
    except ValueError as exc:
        conn.close()
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        conn.close()
        log.error("delete_period_endpoint → FAILED: %s", exc)
        raise HTTPException(status_code=500, detail=f"Delete failed: {exc}")
    finally:
        try:
            conn.close()
        except Exception:
            pass

    _refresh_query_caches(_DEFAULT_DB)

    total = sum(deleted.values())
    msg = f"Deleted {total} row(s) for period '{period}'."
    log.info("delete_period_endpoint → DONE (%s)", msg)
    return DeletePeriodResponse(
        period=period, deleted=deleted, total_deleted=total, message=msg,
    )



# ═══════════════════════════════════════════════════════════════════════════
# CLI — python -m chatbot.ingestion [folders...] [--db PATH]
# ═══════════════════════════════════════════════════════════════════════════
def _cli(argv: list[str] | None = None) -> None:
    """Ingest weekly summary JSONs from Azure Blob into SQLite (auto-discover)."""
    parser = argparse.ArgumentParser(
        description="Ingest weekly summary JSONs from Azure Blob into SQLite.",
    )
    parser.add_argument(
        "folders", nargs="*",
        help="Date folders to ingest (e.g. 2026-04-17). "
             "Omit to auto-discover all date folders in the container.",
    )
    parser.add_argument(
        "--db", default=_DEFAULT_DB,
        help="Path to the SQLite database (default: chatbot/data/employee_analytics.db).",
    )
    args = parser.parse_args(argv)

    client = _get_container_client()
    log.info("Connected to blob container")

    week_folders = args.folders or discover_week_folders(client)
    if not week_folders:
        log.warning("No week folders found — nothing to ingest.")
        return

    log.info("Will ingest %d folder(s): %s", len(week_folders), ", ".join(week_folders))
    conn = get_connection(args.db)
    create_tables(conn)
    log.info("SQLite database ready at %s", args.db)

    total_success = total_skipped = total_failed = 0
    for folder in week_folders:
        log.info("── %s ──", folder)
        result = ingest_folder(client, folder, conn)
        total_success += result["success"]
        total_skipped += result["skipped"]
        total_failed += result["failed"]
        for e in result["errors"]:
            log.error("  FAIL: %s → %s", e["file"], e["error"])
    conn.close()

    print(f"\n{'=' * 60}")
    print(f"Folders processed : {len(week_folders)}")
    print(f"Total succeeded   : {total_success}")
    print(f"Total skipped     : {total_skipped}")
    print(f"Total failed      : {total_failed}")


if __name__ == "__main__":
    _cli()
