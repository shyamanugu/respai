"""
normalized_store.py — Fully **normalized** SQLite store for employee analytics.

Where :mod:`chatbot.ingestion.jobs` keeps a flat *wide* schema (one row per
employee/period with hundreds of KPI columns), this module stores the *same*
per-employee JSON blob in a **normalized** shape with real PRIMARY KEY /
FOREIGN KEY relations and captures **everything** the blob carries — including
the nested pieces the wide schema drops (transcript excerpts, per-call KPI
evidence, customer-experience / sales-outcome buckets, escalations).

Design goals
------------
* **Dynamic KPIs** — KPIs "change or add more" over time, so metrics are stored
  catalog-driven (a ``metrics`` dimension + a ``report_metrics`` value table)
  instead of as fixed columns. A brand-new KPI is just a new ``metrics`` row —
  **no schema migration required**.
* **Referential integrity** — every child row FKs back to its ``report`` (and
  ``report`` FKs to ``employees`` / ``programs``). ``PRAGMA foreign_keys=ON`` is
  enabled on every connection and ``ON DELETE CASCADE`` keeps children tidy.
* **Idempotent re-ingest** — re-ingesting an employee/period deletes the old
  ``report`` row; the cascade wipes all of its children before the fresh insert.

Entity map
----------
    programs ─┐
              ├─< reports >─┬─< report_metrics >── metrics
    employees ┘            ├─< metric_comparisons >── metrics
                           ├─< metric_trends >── metrics
                           ├─< coaching_tips >─< coaching_examples >─┐
                           ├─< key_improvements >                    │
                           ├─< metric_evidence >── metrics           │
                           ├─< classification_counts >               │
                           ├─< contact_classifications >─────────────┤
                           ├─< escalations >─────────────────────────┤
                           └─< contacts >─┬─< contact_tags >          │
                                          └─< transcript_excerpts >   │
                                          └──────────────────────────-┘
                                          (contacts referenced by the
                                           examples/evidence/classification
                                           junctions above)

All DDL and every create / insert / delete / read helper lives in this one file
so the schema can be called or modified in future from a single place.

CLI::

    python -m chatbot.ingestion.normalized_store create
    python -m chatbot.ingestion.normalized_store ingest sample_pso.json
    python -m chatbot.ingestion.normalized_store ingest ./reports_folder
    python -m chatbot.ingestion.normalized_store show 9061789 2026-07-24
    python -m chatbot.ingestion.normalized_store delete 9061789 2026-07-24
    python -m chatbot.ingestion.normalized_store verify
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from chatbot.core.config import DB_PATH as _DEFAULT_DB

log = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# DDL — every table, its PRIMARY KEY, FOREIGN KEYs and indexes
# ═══════════════════════════════════════════════════════════════════════════
# Edit the schema here; ``create_schema`` / ``drop_schema`` below apply it.

SCHEMA_SQL = """
-- ── Dimension: programs (pso / telesales / wcc …) ──────────────────────────
CREATE TABLE IF NOT EXISTS programs (
    program_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    program_name TEXT NOT NULL UNIQUE
);

-- ── Dimension: employees (natural key = source employeeId) ─────────────────
CREATE TABLE IF NOT EXISTS employees (
    employee_id   INTEGER PRIMARY KEY,
    employee_name TEXT NOT NULL
);

-- ── Dimension: metrics catalog (dynamic — new KPIs are just new rows) ──────
CREATE TABLE IF NOT EXISTS metrics (
    metric_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    metric_key TEXT NOT NULL UNIQUE,
    label      TEXT,
    unit       TEXT,
    category   TEXT NOT NULL DEFAULT 'kpi'   -- kpi | behavior | soft_skill
);

-- ── Fact root: one report per employee per period ──────────────────────────
CREATE TABLE IF NOT EXISTS reports (
    report_id              INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id            INTEGER NOT NULL
                               REFERENCES employees(employee_id) ON DELETE CASCADE,
    program_id             INTEGER
                               REFERENCES programs(program_id) ON DELETE SET NULL,
    period                 TEXT NOT NULL,   -- verbatim source date, e.g. 2026-07-24
    date_utc               TEXT,
    period_kind            TEXT,            -- weekly / monthly …
    total_call_count       INTEGER,
    summary                TEXT,
    overall_behavior_score REAL,
    np_total               INTEGER,
    np_converted           INTEGER,
    np_conversion_rate     REAL,
    ingested_at            TEXT NOT NULL,
    UNIQUE (employee_id, period)
);

-- ── KPI / behavior / soft-skill values (EAV; dynamic set) ──────────────────
CREATE TABLE IF NOT EXISTS report_metrics (
    report_metric_id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id INTEGER NOT NULL REFERENCES reports(report_id) ON DELETE CASCADE,
    metric_id INTEGER NOT NULL REFERENCES metrics(metric_id) ON DELETE CASCADE,
    value REAL,
    unit  TEXT,
    delta REAL,
    UNIQUE (report_id, metric_id)
);

-- ── Individual vs team benchmark ───────────────────────────────────────────
CREATE TABLE IF NOT EXISTS metric_comparisons (
    comparison_id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id INTEGER NOT NULL REFERENCES reports(report_id) ON DELETE CASCADE,
    metric_id INTEGER NOT NULL REFERENCES metrics(metric_id) ON DELETE CASCADE,
    individual REAL,
    team_avg   REAL,
    unit       TEXT,
    UNIQUE (report_id, metric_id)
);

-- ── Weekly trend points ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS metric_trends (
    trend_id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id INTEGER NOT NULL REFERENCES reports(report_id) ON DELETE CASCADE,
    metric_id INTEGER NOT NULL REFERENCES metrics(metric_id) ON DELETE CASCADE,
    point_index INTEGER NOT NULL,
    x_label TEXT,
    y_value REAL,
    UNIQUE (report_id, metric_id, point_index)
);

-- ── Contacts (calls) referenced by tips / evidence / classifications ───────
CREATE TABLE IF NOT EXISTS contacts (
    contact_pk     INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id      INTEGER NOT NULL REFERENCES reports(report_id) ON DELETE CASCADE,
    contact_id     TEXT NOT NULL,
    date_utc       TEXT,
    summary        TEXT,
    transcript_url TEXT,
    UNIQUE (report_id, contact_id)
);

CREATE TABLE IF NOT EXISTS contact_tags (
    tag_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_pk INTEGER NOT NULL REFERENCES contacts(contact_pk) ON DELETE CASCADE,
    tag        TEXT NOT NULL,
    UNIQUE (contact_pk, tag)
);

CREATE TABLE IF NOT EXISTS transcript_excerpts (
    excerpt_id INTEGER PRIMARY KEY AUTOINCREMENT,
    contact_pk INTEGER NOT NULL REFERENCES contacts(contact_pk) ON DELETE CASCADE,
    seq        INTEGER NOT NULL,
    turn_id    INTEGER,
    speaker    TEXT,
    text       TEXT,
    UNIQUE (contact_pk, seq)
);

-- ── Coaching tips + their call examples ────────────────────────────────────
CREATE TABLE IF NOT EXISTS coaching_tips (
    tip_id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id INTEGER NOT NULL REFERENCES reports(report_id) ON DELETE CASCADE,
    tip_rank INTEGER,
    tip_text TEXT,
    priority TEXT,
    expected_impact TEXT,
    UNIQUE (report_id, tip_rank)
);

CREATE TABLE IF NOT EXISTS coaching_examples (
    example_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    tip_id      INTEGER NOT NULL REFERENCES coaching_tips(tip_id) ON DELETE CASCADE,
    contact_pk  INTEGER NOT NULL REFERENCES contacts(contact_pk) ON DELETE CASCADE,
    segment_ids TEXT,
    UNIQUE (tip_id, contact_pk)
);

-- ── Key improvement areas (free text, ranked) ──────────────────────────────
CREATE TABLE IF NOT EXISTS key_improvements (
    improvement_id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id INTEGER NOT NULL REFERENCES reports(report_id) ON DELETE CASCADE,
    area_rank INTEGER,
    area_text TEXT,
    UNIQUE (report_id, area_rank)
);

-- ── Per-KPI call-level evidence (kpi_groups[].items) ───────────────────────
CREATE TABLE IF NOT EXISTS metric_evidence (
    evidence_id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id  INTEGER NOT NULL REFERENCES reports(report_id) ON DELETE CASCADE,
    metric_id  INTEGER NOT NULL REFERENCES metrics(metric_id) ON DELETE CASCADE,
    contact_pk INTEGER NOT NULL REFERENCES contacts(contact_pk) ON DELETE CASCADE,
    segment_ids TEXT,
    UNIQUE (report_id, metric_id, contact_pk)
);

-- ── Bucketed classifications (customer_experience / sales_outcome) ─────────
CREATE TABLE IF NOT EXISTS classification_counts (
    count_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id INTEGER NOT NULL REFERENCES reports(report_id) ON DELETE CASCADE,
    dimension TEXT NOT NULL,          -- customer_experience | sales_outcome
    bucket    TEXT NOT NULL,          -- Good/Medium/Poor | Closed deal/…
    count     INTEGER,
    UNIQUE (report_id, dimension, bucket)
);

CREATE TABLE IF NOT EXISTS contact_classifications (
    classification_id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id  INTEGER NOT NULL REFERENCES reports(report_id) ON DELETE CASCADE,
    contact_pk INTEGER NOT NULL REFERENCES contacts(contact_pk) ON DELETE CASCADE,
    dimension  TEXT NOT NULL,
    bucket     TEXT NOT NULL,
    UNIQUE (report_id, contact_pk, dimension, bucket)
);

-- ── Escalations (shape varies; contact link optional, detail kept as JSON) ─
CREATE TABLE IF NOT EXISTS escalations (
    escalation_id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_id  INTEGER NOT NULL REFERENCES reports(report_id) ON DELETE CASCADE,
    contact_pk INTEGER REFERENCES contacts(contact_pk) ON DELETE SET NULL,
    detail     TEXT
);

-- ── Indexes on the hot foreign keys / lookups ──────────────────────────────
CREATE INDEX IF NOT EXISTS idx_reports_employee   ON reports(employee_id);
CREATE INDEX IF NOT EXISTS idx_reports_period      ON reports(period);
CREATE INDEX IF NOT EXISTS idx_rm_report           ON report_metrics(report_id);
CREATE INDEX IF NOT EXISTS idx_rm_metric           ON report_metrics(metric_id);
CREATE INDEX IF NOT EXISTS idx_cmp_report          ON metric_comparisons(report_id);
CREATE INDEX IF NOT EXISTS idx_trend_report        ON metric_trends(report_id);
CREATE INDEX IF NOT EXISTS idx_contacts_report     ON contacts(report_id);
CREATE INDEX IF NOT EXISTS idx_excerpt_contact     ON transcript_excerpts(contact_pk);
CREATE INDEX IF NOT EXISTS idx_tip_report          ON coaching_tips(report_id);
CREATE INDEX IF NOT EXISTS idx_evidence_report     ON metric_evidence(report_id);
CREATE INDEX IF NOT EXISTS idx_class_report        ON contact_classifications(report_id);
"""

# Ordered most-dependent → least-dependent, for a clean ``drop_schema``.
_ALL_TABLES = [
    "escalations",
    "contact_classifications",
    "classification_counts",
    "metric_evidence",
    "key_improvements",
    "coaching_examples",
    "coaching_tips",
    "transcript_excerpts",
    "contact_tags",
    "contacts",
    "metric_trends",
    "metric_comparisons",
    "report_metrics",
    "reports",
    "metrics",
    "employees",
    "programs",
]


# ═══════════════════════════════════════════════════════════════════════════
# CONNECTION + SCHEMA LIFECYCLE
# ═══════════════════════════════════════════════════════════════════════════

def get_connection(db_path: str = _DEFAULT_DB) -> sqlite3.Connection:
    """Open a SQLite connection with WAL mode and FOREIGN KEY enforcement ON."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def create_schema(conn: sqlite3.Connection) -> None:
    """Create every table + index (idempotent — ``IF NOT EXISTS`` throughout)."""
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    log.info("create_schema → %d normalized tables created/verified", len(_ALL_TABLES))


def drop_schema(conn: sqlite3.Connection) -> None:
    """Drop every normalized table (child → parent order). Destructive."""
    conn.execute("PRAGMA foreign_keys=OFF")
    for table in _ALL_TABLES:
        conn.execute(f"DROP TABLE IF EXISTS {table}")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.commit()
    log.info("drop_schema → dropped %d tables", len(_ALL_TABLES))


# ═══════════════════════════════════════════════════════════════════════════
# COERCION / KEY HELPERS
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
    """Reporting period = the source date verbatim (``YYYY-MM-DD``)."""
    try:
        return datetime.fromisoformat(date_str).date().isoformat()
    except (ValueError, TypeError):
        return (date_str or "").strip() or "UNKNOWN"


def _metric_key(raw: str) -> str:
    """Normalize a KPI/behavior label or key to a stable snake_case metric_key."""
    s = (raw or "").strip().lower()
    s = s.replace("&", " and ")
    s = re.sub(r"[()/]", " ", s)
    s = re.sub(r"[^a-z0-9]+", "_", s)
    return s.strip("_")


# ═══════════════════════════════════════════════════════════════════════════
# DIMENSION UPSERTS (return surrogate ids)
# ═══════════════════════════════════════════════════════════════════════════

def upsert_program(conn: sqlite3.Connection, program_name: str | None) -> Optional[int]:
    if not program_name:
        return None
    conn.execute(
        "INSERT INTO programs (program_name) VALUES (?) "
        "ON CONFLICT(program_name) DO NOTHING",
        (program_name,),
    )
    row = conn.execute(
        "SELECT program_id FROM programs WHERE program_name = ?", (program_name,)
    ).fetchone()
    return row["program_id"] if row else None


def upsert_employee(conn: sqlite3.Connection, employee_id: int, name: str) -> int:
    conn.execute(
        "INSERT INTO employees (employee_id, employee_name) VALUES (?, ?) "
        "ON CONFLICT(employee_id) DO UPDATE SET employee_name = excluded.employee_name",
        (employee_id, name or ""),
    )
    return employee_id


def upsert_metric(
    conn: sqlite3.Connection,
    metric_key: str,
    label: str | None = None,
    unit: str | None = None,
    category: str = "kpi",
    _cache: dict[str, int] | None = None,
) -> int:
    """Insert (or fetch) a metric in the catalog. Fills label/unit if missing."""
    if _cache is not None and metric_key in _cache:
        return _cache[metric_key]
    conn.execute(
        "INSERT INTO metrics (metric_key, label, unit, category) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(metric_key) DO UPDATE SET "
        "  label    = COALESCE(metrics.label, excluded.label), "
        "  unit     = COALESCE(metrics.unit, excluded.unit)",
        (metric_key, label, unit, category),
    )
    row = conn.execute(
        "SELECT metric_id FROM metrics WHERE metric_key = ?", (metric_key,)
    ).fetchone()
    mid = row["metric_id"]
    if _cache is not None:
        _cache[metric_key] = mid
    return mid


# ═══════════════════════════════════════════════════════════════════════════
# INGEST — one JSON blob → normalized tables (single transaction)
# ═══════════════════════════════════════════════════════════════════════════

def ingest_report(conn: sqlite3.Connection, data: dict) -> int:
    """Ingest one employee/period JSON blob and return the ``report_id``.

    Idempotent: an existing report for the same (employee_id, period) is deleted
    first so its cascade clears all children before the fresh insert.
    """
    employee_id = _safe_int(data.get("employeeId"))
    if employee_id is None:
        raise ValueError("ingest_report → blob missing 'employeeId'")
    employee_name = data.get("employeeName", "") or ""
    program_name = data.get("programName") or ""
    date_utc = data.get("date_utc", "") or ""
    period = _derive_period(date_utc)

    metric_cache: dict[str, int] = {}
    # label (lower) → metric_id, used to map trends/comparison (keyed by label).
    label_index: dict[str, int] = {}

    try:
        conn.execute("BEGIN")

        upsert_employee(conn, employee_id, employee_name)
        program_id = upsert_program(conn, program_name)

        # Replace-on-reingest: drop the old report (cascades to all children).
        delete_report(conn, employee_id, period, _own_tx=False)

        np = data.get("new_prospect", {}) or {}
        cur = conn.execute(
            "INSERT INTO reports (employee_id, program_id, period, date_utc, "
            "  period_kind, total_call_count, summary, overall_behavior_score, "
            "  np_total, np_converted, np_conversion_rate, ingested_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                employee_id, program_id, period, date_utc,
                data.get("period"),
                _safe_int(data.get("totalCallCount")),
                data.get("summary"),
                _safe_real(data.get("overall_behavior_score")),
                _safe_int(np.get("total")),
                _safe_int(np.get("converted")),
                _safe_real(np.get("conversion_rate")),
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
            ),
        )
        report_id = int(cur.lastrowid)

        _ingest_metrics(conn, report_id, data, metric_cache, label_index)
        _ingest_comparisons(conn, report_id, data, metric_cache, label_index)
        _ingest_trends(conn, report_id, data, metric_cache, label_index)

        contact_cache: dict[str, int] = {}  # contact_id → contact_pk (this report)
        _ingest_coaching(conn, report_id, data, contact_cache)
        _ingest_improvements(conn, report_id, data)
        _ingest_kpi_group_evidence(conn, report_id, data, metric_cache, label_index, contact_cache)
        _ingest_classifications(conn, report_id, data, contact_cache)
        _ingest_escalations(conn, report_id, data, contact_cache)

        conn.execute("COMMIT")
        log.info("ingest_report → employee=%s period=%s report_id=%d",
                 employee_id, period, report_id)
        return report_id
    except Exception:
        conn.execute("ROLLBACK")
        raise


# ── metrics: kpis[] + behavior_scores{} + call_handling_and_soft_skills{} ──
def _ingest_metrics(conn, report_id, data, mcache, lindex) -> None:
    def _put(metric_key, label, unit, category, value, delta):
        mid = upsert_metric(conn, metric_key, label, unit, category, _cache=mcache)
        if label:
            lindex[label.strip().lower()] = mid
        conn.execute(
            "INSERT INTO report_metrics (report_id, metric_id, value, unit, delta) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(report_id, metric_id) DO UPDATE SET "
            "  value = excluded.value, unit = excluded.unit, delta = excluded.delta",
            (report_id, mid, _safe_real(value), unit, _safe_real(delta)),
        )

    for kpi in data.get("kpis", []) or []:
        key = kpi.get("key") or _metric_key(kpi.get("label", ""))
        if not key:
            continue
        _put(key, kpi.get("label"), kpi.get("unit"), "kpi",
             kpi.get("value"), kpi.get("delta"))

    for label, obj in (data.get("behavior_scores") or {}).items():
        score = obj.get("score") if isinstance(obj, dict) else obj
        delta = obj.get("delta") if isinstance(obj, dict) else None
        _put(_metric_key(label), label, None, "behavior", score, delta)

    for label, obj in (data.get("call_handling_and_soft_skills") or {}).items():
        score = obj.get("score") if isinstance(obj, dict) else obj
        delta = obj.get("delta") if isinstance(obj, dict) else None
        _put(_metric_key(label), label, None, "soft_skill", score, delta)


# ── comparison[] (keyed by label) → metric_comparisons ─────────────────────
def _ingest_comparisons(conn, report_id, data, mcache, lindex) -> None:
    for cmp in data.get("comparison", []) or []:
        label = cmp.get("metric") or cmp.get("label") or cmp.get("key") or ""
        if not label:
            continue
        mid = lindex.get(label.strip().lower())
        if mid is None:
            mid = upsert_metric(conn, _metric_key(label), label, cmp.get("unit"), "kpi", _cache=mcache)
        conn.execute(
            "INSERT INTO metric_comparisons (report_id, metric_id, individual, team_avg, unit) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(report_id, metric_id) DO UPDATE SET "
            "  individual = excluded.individual, team_avg = excluded.team_avg, unit = excluded.unit",
            (report_id, mid, _safe_real(cmp.get("individual")),
             _safe_real(cmp.get("teamAvg", cmp.get("team_avg"))), cmp.get("unit")),
        )


# ── trends{} (label → [{x,y}]) → metric_trends ─────────────────────────────
def _ingest_trends(conn, report_id, data, mcache, lindex) -> None:
    for label, points in (data.get("trends") or {}).items():
        mid = lindex.get(label.strip().lower())
        if mid is None:
            mid = upsert_metric(conn, _metric_key(label), label, None, "kpi", _cache=mcache)
        if isinstance(points, dict):
            points = [{"x": k, "y": v} for k, v in points.items()]
        for idx, pt in enumerate(points or []):
            if isinstance(pt, dict):
                x_label = str(pt.get("x", pt.get("label", idx)))
                y_value = pt.get("y", pt.get("value"))
            else:
                x_label, y_value = str(idx), pt
            conn.execute(
                "INSERT INTO metric_trends (report_id, metric_id, point_index, x_label, y_value) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(report_id, metric_id, point_index) DO UPDATE SET "
                "  x_label = excluded.x_label, y_value = excluded.y_value",
                (report_id, mid, idx, x_label, _safe_real(y_value)),
            )


# ── contacts (shared unit): create once per (report, contact_id) ───────────
def _get_or_create_contact(conn, report_id, item, cache) -> Optional[int]:
    contact_id = item.get("contact_id")
    if not contact_id:
        return None
    if contact_id in cache:
        return cache[contact_id]

    cur = conn.execute(
        "INSERT INTO contacts (report_id, contact_id, date_utc, summary, transcript_url) "
        "VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(report_id, contact_id) DO UPDATE SET "
        "  date_utc       = COALESCE(contacts.date_utc, excluded.date_utc), "
        "  summary        = COALESCE(contacts.summary, excluded.summary), "
        "  transcript_url = COALESCE(contacts.transcript_url, excluded.transcript_url)",
        (report_id, contact_id, item.get("date_utc"), item.get("summary"),
         item.get("transcript_url")),
    )
    if cur.lastrowid:
        contact_pk = int(cur.lastrowid)
    else:
        row = conn.execute(
            "SELECT contact_pk FROM contacts WHERE report_id = ? AND contact_id = ?",
            (report_id, contact_id),
        ).fetchone()
        contact_pk = row["contact_pk"]
    cache[contact_id] = contact_pk

    for tag in item.get("tags", []) or []:
        conn.execute(
            "INSERT INTO contact_tags (contact_pk, tag) VALUES (?, ?) "
            "ON CONFLICT(contact_pk, tag) DO NOTHING",
            (contact_pk, tag),
        )
    for seq, turn in enumerate(item.get("transcript_excerpt", []) or []):
        conn.execute(
            "INSERT INTO transcript_excerpts (contact_pk, seq, turn_id, speaker, text) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(contact_pk, seq) DO NOTHING",
            (contact_pk, seq, _safe_int(turn.get("id")), turn.get("speaker"), turn.get("text")),
        )
    return contact_pk


# ── coaching_tips[] + examples[] ───────────────────────────────────────────
def _ingest_coaching(conn, report_id, data, contact_cache) -> None:
    for rank, tip in enumerate(data.get("coaching_tips", []) or [], start=1):
        cur = conn.execute(
            "INSERT INTO coaching_tips (report_id, tip_rank, tip_text, priority, expected_impact) "
            "VALUES (?, ?, ?, ?, ?)",
            (report_id, rank, tip.get("tip"), tip.get("priority"), tip.get("expected_impact")),
        )
        tip_id = int(cur.lastrowid)
        for ex in tip.get("examples", []) or []:
            contact_pk = _get_or_create_contact(conn, report_id, ex, contact_cache)
            if contact_pk is None:
                continue
            seg = ex.get("transcript_ids") or ex.get("segment_ids")
            conn.execute(
                "INSERT INTO coaching_examples (tip_id, contact_pk, segment_ids) "
                "VALUES (?, ?, ?) ON CONFLICT(tip_id, contact_pk) DO NOTHING",
                (tip_id, contact_pk, json.dumps(seg) if seg is not None else None),
            )


# ── key_improvements[] (free text) ─────────────────────────────────────────
def _ingest_improvements(conn, report_id, data) -> None:
    for rank, area in enumerate(data.get("key_improvements", []) or [], start=1):
        if isinstance(area, dict):
            text = area.get("text") or area.get("area") or area.get("description")
        else:
            text = area
        if text is None:
            continue
        conn.execute(
            "INSERT INTO key_improvements (report_id, area_rank, area_text) VALUES (?, ?, ?) "
            "ON CONFLICT(report_id, area_rank) DO UPDATE SET area_text = excluded.area_text",
            (report_id, rank, str(text)),
        )


# ── kpi_groups[].items[] → per-KPI call evidence ───────────────────────────
def _ingest_kpi_group_evidence(conn, report_id, data, mcache, lindex, contact_cache) -> None:
    for grp in data.get("kpi_groups", []) or []:
        key = grp.get("key") or _metric_key(grp.get("label", ""))
        if not key:
            continue
        mid = upsert_metric(conn, key, grp.get("label"), grp.get("unit"), "kpi", _cache=mcache)
        # Ensure the group's headline score is captured even if absent from kpis[].
        conn.execute(
            "INSERT INTO report_metrics (report_id, metric_id, value, unit, delta) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT(report_id, metric_id) DO NOTHING",
            (report_id, mid, _safe_real(grp.get("score")), grp.get("unit"), _safe_real(grp.get("delta"))),
        )
        for it in grp.get("items", []) or []:
            contact_pk = _get_or_create_contact(conn, report_id, it, contact_cache)
            if contact_pk is None:
                continue
            seg = it.get("segment_ids") or it.get("transcript_ids")
            conn.execute(
                "INSERT INTO metric_evidence (report_id, metric_id, contact_pk, segment_ids) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(report_id, metric_id, contact_pk) DO NOTHING",
                (report_id, mid, contact_pk, json.dumps(seg) if seg is not None else None),
            )


# ── customer_experience{} + sales_outcome{} → bucket counts + per-contact ──
def _ingest_classifications(conn, report_id, data, contact_cache) -> None:
    for dimension in ("customer_experience", "sales_outcome"):
        buckets = data.get(dimension)
        if not isinstance(buckets, dict):
            continue
        for bucket, payload in buckets.items():
            if not isinstance(payload, dict):
                continue
            conn.execute(
                "INSERT INTO classification_counts (report_id, dimension, bucket, count) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(report_id, dimension, bucket) DO UPDATE SET count = excluded.count",
                (report_id, dimension, bucket, _safe_int(payload.get("count"))),
            )
            for it in payload.get("items", []) or []:
                contact_pk = _get_or_create_contact(conn, report_id, it, contact_cache)
                if contact_pk is None:
                    continue
                conn.execute(
                    "INSERT INTO contact_classifications (report_id, contact_pk, dimension, bucket) "
                    "VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(report_id, contact_pk, dimension, bucket) DO NOTHING",
                    (report_id, contact_pk, dimension, bucket),
                )


# ── escalations[] (variable shape) ─────────────────────────────────────────
def _ingest_escalations(conn, report_id, data, contact_cache) -> None:
    for esc in data.get("escalations", []) or []:
        contact_pk = None
        if isinstance(esc, dict):
            contact_pk = _get_or_create_contact(conn, report_id, esc, contact_cache)
        conn.execute(
            "INSERT INTO escalations (report_id, contact_pk, detail) VALUES (?, ?, ?)",
            (report_id, contact_pk, json.dumps(esc, default=str)),
        )


# ═══════════════════════════════════════════════════════════════════════════
# DELETE
# ═══════════════════════════════════════════════════════════════════════════

def delete_report(conn: sqlite3.Connection, employee_id: int, period: str,
                  *, _own_tx: bool = True) -> int:
    """Delete a single report (cascades to every child). Returns rows removed.

    ``_own_tx`` commits when called standalone; the ingest path passes False so
    the delete participates in the surrounding transaction.
    """
    cur = conn.execute(
        "DELETE FROM reports WHERE employee_id = ? AND period = ?",
        (employee_id, period),
    )
    if _own_tx:
        conn.commit()
    return cur.rowcount


def delete_employee(conn: sqlite3.Connection, employee_id: int) -> int:
    """Delete an employee and every report/child of theirs (cascade)."""
    cur = conn.execute("DELETE FROM employees WHERE employee_id = ?", (employee_id,))
    conn.commit()
    return cur.rowcount


def delete_period(conn: sqlite3.Connection, period: str) -> int:
    """Delete every report for a period across all employees (cascade)."""
    cur = conn.execute("DELETE FROM reports WHERE period = ?", (period,))
    conn.commit()
    return cur.rowcount


# ═══════════════════════════════════════════════════════════════════════════
# READ / QUERY API — reconstruct data from the DB (exercises the FK joins)
# ═══════════════════════════════════════════════════════════════════════════

def get_report_id(conn: sqlite3.Connection, employee_id: int, period: str) -> Optional[int]:
    row = conn.execute(
        "SELECT report_id FROM reports WHERE employee_id = ? AND period = ?",
        (employee_id, period),
    ).fetchone()
    return row["report_id"] if row else None


def list_reports(conn: sqlite3.Connection, employee_id: int | None = None) -> list[dict]:
    """List reports (optionally for one employee) with program + employee joined."""
    sql = (
        "SELECT r.report_id, r.employee_id, e.employee_name, p.program_name, "
        "       r.period, r.total_call_count, r.overall_behavior_score "
        "FROM reports r "
        "JOIN employees e ON e.employee_id = r.employee_id "
        "LEFT JOIN programs p ON p.program_id = r.program_id "
    )
    params: tuple = ()
    if employee_id is not None:
        sql += "WHERE r.employee_id = ? "
        params = (employee_id,)
    sql += "ORDER BY r.period DESC, e.employee_name"
    return [dict(row) for row in conn.execute(sql, params)]


def get_kpi(conn: sqlite3.Connection, employee_id: int, period: str,
            metric_key: str) -> Optional[dict]:
    """Fetch a single KPI value by joining reports → report_metrics → metrics."""
    row = conn.execute(
        "SELECT m.metric_key, m.label, m.unit, rm.value, rm.delta "
        "FROM reports r "
        "JOIN report_metrics rm ON rm.report_id = r.report_id "
        "JOIN metrics m ON m.metric_id = rm.metric_id "
        "WHERE r.employee_id = ? AND r.period = ? AND m.metric_key = ?",
        (employee_id, period, metric_key),
    ).fetchone()
    return dict(row) if row else None


def fetch_report(conn: sqlite3.Connection, employee_id: int, period: str) -> Optional[dict]:
    """Reconstruct a full report from the normalized tables (all FK joins)."""
    r = conn.execute(
        "SELECT r.*, e.employee_name, p.program_name "
        "FROM reports r JOIN employees e ON e.employee_id = r.employee_id "
        "LEFT JOIN programs p ON p.program_id = r.program_id "
        "WHERE r.employee_id = ? AND r.period = ?",
        (employee_id, period),
    ).fetchone()
    if not r:
        return None
    rid = r["report_id"]
    out: dict[str, Any] = dict(r)

    out["metrics"] = [dict(x) for x in conn.execute(
        "SELECT m.metric_key, m.label, m.category, rm.value, rm.unit, rm.delta "
        "FROM report_metrics rm JOIN metrics m ON m.metric_id = rm.metric_id "
        "WHERE rm.report_id = ? ORDER BY m.category, m.label", (rid,))]

    out["comparisons"] = [dict(x) for x in conn.execute(
        "SELECT m.metric_key, m.label, c.individual, c.team_avg, c.unit "
        "FROM metric_comparisons c JOIN metrics m ON m.metric_id = c.metric_id "
        "WHERE c.report_id = ?", (rid,))]

    out["trends"] = [dict(x) for x in conn.execute(
        "SELECT m.label, t.point_index, t.x_label, t.y_value "
        "FROM metric_trends t JOIN metrics m ON m.metric_id = t.metric_id "
        "WHERE t.report_id = ? ORDER BY m.label, t.point_index", (rid,))]

    out["coaching"] = []
    for tip in conn.execute(
        "SELECT tip_id, tip_rank, tip_text, priority, expected_impact "
        "FROM coaching_tips WHERE report_id = ? ORDER BY tip_rank", (rid,)):
        tipd = dict(tip)
        tipd["examples"] = [dict(x) for x in conn.execute(
            "SELECT c.contact_id, c.summary, ce.segment_ids "
            "FROM coaching_examples ce JOIN contacts c ON c.contact_pk = ce.contact_pk "
            "WHERE ce.tip_id = ?", (tip["tip_id"],))]
        out["coaching"].append(tipd)

    out["key_improvements"] = [x["area_text"] for x in conn.execute(
        "SELECT area_text FROM key_improvements WHERE report_id = ? ORDER BY area_rank", (rid,))]

    out["classification_counts"] = [dict(x) for x in conn.execute(
        "SELECT dimension, bucket, count FROM classification_counts WHERE report_id = ?", (rid,))]

    return out


def verify_foreign_keys(conn: sqlite3.Connection) -> list[dict]:
    """Run ``PRAGMA foreign_key_check`` and return any integrity violations."""
    return [dict(row) for row in conn.execute("PRAGMA foreign_key_check")]


# ═══════════════════════════════════════════════════════════════════════════
# BULK INGEST HELPERS
# ═══════════════════════════════════════════════════════════════════════════

def ingest_file(conn: sqlite3.Connection, path: str | Path) -> int:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return ingest_report(conn, data)


def ingest_local_folder(conn: sqlite3.Connection, folder: str | Path) -> dict:
    base = Path(folder)
    files = sorted(base.rglob("*.json"))
    ok, skipped, errors = 0, 0, []
    for fp in files:
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
        except Exception as exc:
            errors.append({"file": str(fp), "error": str(exc)})
            continue
        if not isinstance(data, dict) or "employeeId" not in data:
            skipped += 1
            continue
        try:
            ingest_report(conn, data)
            ok += 1
        except Exception as exc:
            errors.append({"file": str(fp), "error": str(exc)})
    return {"total": len(files), "success": ok, "skipped": skipped,
            "failed": len(errors), "errors": errors}


def ingest_blob_folders(
    conn: sqlite3.Connection,
    dates: list[str],
    programs: list[str] | None = None,
) -> dict:
    """Ingest every employee JSON under one or more Azure Blob *date* folders.

    Reuses the blob helpers in :mod:`chatbot.ingestion.jobs` (container client,
    listing, download). Each blob is a single employee report; it is routed into
    the normalized tables via :func:`ingest_report`. When *programs* is given
    (e.g. ``["telesales", "wcc", "pso"]``) only reports whose ``programName``
    matches (case-insensitive) are ingested — everything else is skipped.

    Dimension "pointing" tables (``programs`` / ``employees`` / ``metrics``) are
    upserted automatically as each report is ingested, so new programs and KPIs
    appear without any schema change.
    """
    from chatbot.ingestion.jobs import (
        _get_container_client,
        list_json_files,
        read_json_from_blob,
    )

    wanted = {p.strip().lower() for p in programs} if programs else None
    client = _get_container_client()

    per_folder: list[dict] = []
    total_ok = total_skipped = total_failed = 0
    programs_seen: set[str] = set()

    for folder in dates:
        prefix = folder if folder.endswith("/") else f"{folder}/"
        blob_names = list_json_files(client, prefix)
        ok = skipped = 0
        errors: list[dict] = []

        for blob_name in blob_names:
            try:
                data = read_json_from_blob(client, blob_name)
                if data is None or "employeeId" not in data:
                    skipped += 1
                    continue
                prog = str(data.get("programName", "")).strip()
                if wanted is not None and prog.lower() not in wanted:
                    skipped += 1
                    continue
                ingest_report(conn, data)
                if prog:
                    programs_seen.add(prog)
                ok += 1
            except Exception as exc:  # noqa: BLE001 — collect and continue
                log.error("ingest_blob_folders → %s failed: %s", blob_name, exc)
                errors.append({"file": blob_name, "error": str(exc)})

        log.info("ingest_blob_folders → %s: success=%d skipped=%d failed=%d",
                 folder, ok, skipped, len(errors))
        per_folder.append({
            "folder": folder, "total": len(blob_names),
            "success": ok, "skipped": skipped,
            "failed": len(errors), "errors": errors,
        })
        total_ok += ok
        total_skipped += skipped
        total_failed += len(errors)

    return {
        "folders_processed": len(dates),
        "programs_ingested": sorted(programs_seen),
        "total_success": total_ok,
        "total_skipped": total_skipped,
        "total_failed": total_failed,
        "results": per_folder,
    }


# ═══════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════

def _cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m chatbot.ingestion.normalized_store",
        description="Normalized SQLite store for employee analytics reports.",
    )
    parser.add_argument("--db", default=_DEFAULT_DB, help="SQLite DB path")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("create", help="Create the normalized schema (idempotent)")
    sub.add_parser("drop", help="Drop every normalized table (destructive)")
    sub.add_parser("verify", help="Run PRAGMA foreign_key_check")

    p_ing = sub.add_parser("ingest", help="Ingest a JSON file or a folder of JSON files")
    p_ing.add_argument("path", help="Path to a .json file or a folder")

    p_blob = sub.add_parser(
        "ingest-blob",
        help="Ingest Azure Blob date folders (e.g. 2026-07-24) into the normalized store",
    )
    p_blob.add_argument("dates", nargs="+", help="One or more date folders, e.g. 2026-07-24")
    p_blob.add_argument(
        "--programs",
        default=None,
        help="Comma-separated programName filter, e.g. telesales,wcc,pso (default: all)",
    )

    p_show = sub.add_parser("show", help="Reconstruct and print a report from the DB")
    p_show.add_argument("employee_id", type=int)
    p_show.add_argument("period")

    p_del = sub.add_parser("delete", help="Delete a report by employee_id + period")
    p_del.add_argument("employee_id", type=int)
    p_del.add_argument("period")

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(message)s")
    conn = get_connection(args.db)
    try:
        if args.cmd == "create":
            create_schema(conn)
            print(f"Schema created in {args.db}")
        elif args.cmd == "drop":
            drop_schema(conn)
            print("Schema dropped")
        elif args.cmd == "verify":
            violations = verify_foreign_keys(conn)
            print("OK — no FK violations" if not violations else json.dumps(violations, indent=2))
            return 0 if not violations else 1
        elif args.cmd == "ingest":
            create_schema(conn)
            path = Path(args.path)
            if path.is_dir():
                print(json.dumps(ingest_local_folder(conn, path), indent=2))
            else:
                rid = ingest_file(conn, path)
                print(f"Ingested {path} → report_id={rid}")
        elif args.cmd == "ingest-blob":
            create_schema(conn)
            programs = (
                [p.strip() for p in args.programs.split(",") if p.strip()]
                if args.programs else None
            )
            summary = ingest_blob_folders(conn, args.dates, programs)
            print(json.dumps(summary, indent=2))
        elif args.cmd == "show":
            report = fetch_report(conn, args.employee_id, args.period)
            print(json.dumps(report, indent=2, default=str) if report else "Not found")
        elif args.cmd == "delete":
            n = delete_report(conn, args.employee_id, args.period)
            print(f"Deleted {n} report(s)")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli(sys.argv[1:]))
