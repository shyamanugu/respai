"""sqlite.py — SQLite data source for the JSON / KPI-meaning analytics tables.

A single, self-contained pipeline for the 6 flat wide-tables (kpis, scores,
comparison, wcc_metrics, trends, coaching).  Consolidates what used to be three
parallel modules (generation, validation and execution) since they all serve
one purpose: answer a KPI question against the local SQLite database.

Sections
--------
1. GENERATION   — natural-language → SQL (deterministic fast-path + LLM fallback),
                  including metric/period/agent resolution and schema building.
2. VALIDATION   — SELECT-only safety across the 6 allowed flat tables.
3. EXECUTION    — run the query with ``employee_id IN (...)`` scope injection.

Employee scoping is applied only at execution time so callers always see just
their authorized employees.
"""

from __future__ import annotations

import os
import re
import sqlite3
import json
import difflib
import threading
from datetime import date as _date
from pathlib import Path

from chatbot.core.config import get_logger
from chatbot.core.config import DB_PATH
from chatbot.llm.dictionaries import glossary_text, meaning_for
from chatbot.llm import generate_completion
from chatbot.llm import load_prompt

log = get_logger(__name__)

# Sentinel distinguishing "not supplied by caller" from an explicit ``None``.
_UNSET: object = object()

# ───────────────────────────────────────────────────────────────────────────
# Persistent read-only connection — opened once and reused for every query so
# we never pay the open/close cost per request. SQLite is opened read-only
# (``mode=ro``) with ``check_same_thread=False``; concurrent access is
# serialized through ``_DB_LOCK`` (queries return only 5–20 rows, so the lock
# is held briefly and adds negligible latency).
# ───────────────────────────────────────────────────────────────────────────
_db_conn: sqlite3.Connection | None = None
_DB_LOCK = threading.Lock()


def get_db_connection() -> sqlite3.Connection:
    """Return the shared read-only SQLite connection, opening it on first use."""
    global _db_conn
    if _db_conn is None:
        with _DB_LOCK:
            if _db_conn is None:  # double-checked under lock
                conn = sqlite3.connect(
                    f"file:{DB_PATH}?mode=ro", uri=True, check_same_thread=False
                )
                conn.row_factory = sqlite3.Row
                _db_conn = conn
                log.info("sqlite → persistent read-only connection opened (%s)", DB_PATH)
    return _db_conn


def warmup() -> None:
    """Open and prime the shared SQLite connection at startup (best-effort)."""
    try:
        get_db_connection().execute("SELECT 1").fetchall()
        log.info("warmup → SQLite connection warmed up")
    except Exception as exc:  # best-effort only
        log.warning("warmup → SQLite warmup failed (non-fatal): %s", exc)


# ═══════════════════════════════════════════════════════════════════════════
# 1. GENERATION — natural-language → SQL
# ═══════════════════════════════════════════════════════════════════════════
# ``DB_PATH`` is imported from chatbot.core.config (single source of truth).

# ───────────────────────────────────────────────────────────────────────────
# Compact schema — derived from the LIVE database so the SQL the LLM writes can
# only reference columns that actually exist.  ``_delta`` / ``_benchmark``
# sibling columns are summarised in a note (instead of listed) to keep the
# token count low.  Falls back to the static schema below if the DB is absent.
# ───────────────────────────────────────────────────────────────────────────

# Tables the chat agent is allowed to query, in prompt order.
_QUERYABLE_TABLES = ["kpis", "scores", "comparison", "wcc_metrics", "pso_metrics", "trends", "coaching", "improvements"]

# Large free-text storage columns that must not appear in generated SELECT lists.
_SCHEMA_EXCLUDE_COLS = {"summary", "examples"}

_SCHEMA_NOTE = (
    "\nNotes: every metric column also has a matching _delta column "
    "(week-over-week change), e.g. escalations_delta, bs_empathy_delta, "
    "wkpi_resolution_actual_delta. Each comparison cmp_* column has a "
    "cmp_*_benchmark sibling (team average)."
)

_FALLBACK_SCHEMA = """\
Table kpis (employee_id TEXT, employee_name TEXT, period TEXT, program_name TEXT, total_call_count REAL, overall_score REAL, overall_behavior_score REAL, new_line_pitches REAL, new_line_pitches_delta REAL, upgrade_attempts REAL, upgrade_attempts_delta REAL, save_attempts REAL, save_attempts_delta REAL, fwa_attempts REAL, mobile_protection_attempts REAL, we_got_you_utterances REAL, escalations REAL, escalations_delta REAL, customer_experience REAL, customer_experience_delta REAL, np_total REAL, np_converted REAL, np_conversion_rate REAL)
Table scores (employee_id TEXT, employee_name TEXT, period TEXT, program_name TEXT, bs_active_listening REAL, bs_acknowledgment REAL, bs_empathy REAL, bs_confidence REAL, bs_clarity REAL, bs_needs_discovery REAL, bs_solution_guidance REAL, bs_next_steps_summary REAL, bs_objection_handling REAL, bs_value_positioning REAL, bs_assumptive_close REAL, bs_compliance_disclosures REAL, bs_call_control REAL, bs_professional_tone REAL, ch_comprehension REAL, ch_language_proficiency REAL, ch_emotional_intelligence REAL, ch_relationship_building REAL, ch_professional_skills REAL, ch_subject_matter_expertise REAL)
Table comparison (employee_id TEXT, employee_name TEXT, period TEXT, program_name TEXT, cmp_resolution_rate REAL, cmp_resolution_rate_benchmark REAL, cmp_save_rate REAL, cmp_save_rate_benchmark REAL, cmp_sale_rate REAL, cmp_sale_rate_benchmark REAL, cmp_escalations REAL, cmp_escalations_benchmark REAL, cmp_customer_experience REAL, cmp_customer_experience_benchmark REAL, cmp_new_line_pitches REAL, cmp_upgrade_attempts REAL, cmp_save_attempts REAL)
Table wcc_metrics (employee_id TEXT, employee_name TEXT, period TEXT, program_name TEXT, wkpi_resolution_opportunity_exists REAL, wkpi_resolution_attempted REAL, wkpi_resolution_actual REAL, wkpi_save_opportunity_exists REAL, wkpi_save_attempted REAL, wkpi_save_actual REAL, wkpi_sale_opportunity_exists REAL, wkpi_sale_attempted REAL, wkpi_sale_actual REAL, wkpi_resolution_attempted_rate REAL, wkpi_resolution_actual_rate REAL, wkpi_save_attempted_rate REAL, wkpi_save_actual_rate REAL, wkpi_sale_attempted_rate REAL, wkpi_sale_actual_rate REAL, wbs_greeting_connection REAL, wbs_build_connection REAL, wbs_gather_information REAL, wbs_set_up_for_success REAL)
Table pso_metrics (employee_id TEXT, employee_name TEXT, period TEXT, program_name TEXT, pkpi_predicted_csat REAL, pkpi_customer_confidence REAL, pkpi_customer_effort REAL, pkpi_fcr_likelihood REAL, pkpi_resolution_completeness REAL, pkpi_resolution_confidence REAL, pkpi_next_steps_clarity REAL, pkpi_issue_resolution_effectiveness REAL, pkpi_quality REAL, pkpi_compliance REAL, pkpi_process_adherence REAL, pkpi_issue_ownership REAL, pkpi_escalation_handling REAL, pkpi_transfer_avoidance REAL, pkpi_case_management REAL, pkpi_aht_efficiency REAL, pkpi_contact_handling_efficiency REAL, pkpi_hold_management REAL, pkpi_repeat_contact_risk REAL, pkpi_escalation_risk REAL, pkpi_callback_risk REAL, pkpi_reopen_risk REAL)
Table trends (employee_id TEXT, employee_name TEXT, period TEXT, metric_key TEXT, w0 REAL, w1 REAL, w2 REAL, w3 REAL, w4 REAL)
Table coaching (id INTEGER, employee_id TEXT, employee_name TEXT, period TEXT, tip_rank INTEGER, tip_text TEXT, priority TEXT, expected_impact TEXT)"""


def _build_trends_key_note(db_path: str = DB_PATH) -> str:
    """Return a note enumerating the ACTUAL ``trends.metric_key`` values.

    The trends table stores one row per (employee, metric) with a free-text
    ``metric_key`` — a mix of snake_case rates (``resolution_actual_rate``) and
    display names (``New line Pitches``).  Listing the exact ingested keys lets
    the LLM filter ``WHERE metric_key = '<exact value>'`` instead of guessing.
    Returns ``""`` when the DB/table is unavailable.
    """
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            keys = [
                str(k[0])
                for k in conn.execute(
                    "SELECT DISTINCT metric_key FROM trends "
                    "WHERE metric_key IS NOT NULL ORDER BY metric_key"
                ).fetchall()
            ]
        finally:
            conn.close()
    except sqlite3.Error:
        return ""
    if not keys:
        return ""
    return (
        "\nFor table trends, filter on metric_key using EXACTLY one of these "
        "stored values: " + ", ".join(f"'{k}'" for k in keys) + "."
    )


# Built once at import; rebuilt by refresh_caches() after re-ingestion.
_TRENDS_KEY_NOTE: str = _build_trends_key_note()


def _load_periods(db_path: str = DB_PATH) -> list[str]:
    """Return the distinct reporting periods (``YYYY-MM-DD``), most recent first."""
    if not Path(db_path).exists():
        return []
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            rows = conn.execute(
                "SELECT DISTINCT period FROM kpis "
                "WHERE period IS NOT NULL ORDER BY period DESC"
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return []
    return [str(r[0]) for r in rows]


def _build_period_note(db_path: str = DB_PATH) -> str:
    """Describe the available reporting periods and relative-week mapping.

    Injected into the prompt so the LLM can resolve relative references
    ("this/latest week", "last/previous week", "N weeks ago") to concrete
    dates now that the DB spans multiple weeks.
    """
    periods = _load_periods(db_path)
    if not periods:
        return ""
    note = (
        "\nReporting periods available (most recent first): "
        + ", ".join(periods)
        + f". The latest / current / this week = '{periods[0]}'."
    )
    if len(periods) > 1:
        note += f" Previous / last week = '{periods[1]}'."
    note += (
        " Map relative references ('last week', 'previous week', "
        "'two weeks ago') to the matching date from this list. When no period "
        "is mentioned, default to each agent's OWN latest period."
    )
    return note


# Built once at import; rebuilt by refresh_caches() after re-ingestion.
_PERIODS: list[str] = _load_periods()
_PERIOD_NOTE: str = _build_period_note()


def available_periods() -> list[str]:
    """Return the loaded reporting periods (``YYYY-MM-DD``), newest first.

    Public accessor over the module-level cache so the LLM entity extractor can
    ground relative period references ("last week", "april 3") without importing
    a private name.
    """
    return list(_PERIODS)


def _build_schema_from_db(db_path: str = DB_PATH) -> str:
    """Introspect *db_path* and return a compact, accurate schema string.

    Lists only the base metric columns (``_delta`` / ``_benchmark`` siblings are
    described in a trailing note).  Returns :data:`_FALLBACK_SCHEMA` if the DB
    cannot be read so SQL generation never breaks on a missing file.
    """
    if not Path(db_path).exists():
        log.warning("_build_schema_from_db → DB missing at %s, using fallback", db_path)
        return _FALLBACK_SCHEMA
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            lines: list[str] = []
            for table in _QUERYABLE_TABLES:
                rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
                if not rows:
                    continue
                cols: list[str] = []
                for r in rows:
                    name, ctype = r[1], (r[2] or "").strip()
                    if name in _SCHEMA_EXCLUDE_COLS:
                        continue
                    if name.endswith("_delta") or name.endswith("_benchmark"):
                        continue
                    cols.append(f"{name} {ctype}".strip())
                lines.append(f"Table {table} ({', '.join(cols)})")
        finally:
            conn.close()
        if not lines:
            return _FALLBACK_SCHEMA
        schema = "\n".join(lines) + _SCHEMA_NOTE + _build_period_note(db_path) + _build_trends_key_note(db_path)
        log.info("_build_schema_from_db → built schema for %d tables (%d chars)",
                 len(lines), len(schema))
        return schema
    except Exception as exc:
        log.warning("_build_schema_from_db → introspection failed (%s), using fallback", exc)
        return _FALLBACK_SCHEMA


_COMPACT_SCHEMA = _build_schema_from_db()


# ───────────────────────────────────────────────────────────────────────────
# Metric → Table mapping (deterministic resolution)
# ───────────────────────────────────────────────────────────────────────────

# Which column lives in which table
METRIC_TABLE_MAP: dict[str, str] = {}

# KPIs table columns
_KPI_METRICS = [
    "new_line_pitches", "new_line_opportunity_exists", "new_line_opportunity_missed",
    "upgrade_attempts", "upgrade_opportunity_exists", "upgrade_opportunity_missed",
    "save_attempts", "fwa_attempts", "mobile_protection_attempts",
    "we_got_you_utterances", "escalations", "customer_experience",
    "np_total", "np_converted", "np_conversion_rate",
    "total_call_count", "overall_score", "overall_behavior_score",
]
for _k in _KPI_METRICS:
    METRIC_TABLE_MAP[_k] = "kpis"

# Scores table columns (bs_ and ch_ prefixes)
_BEHAVIOR_METRICS = [
    "active_listening", "acknowledgment", "empathy", "confidence", "clarity",
    "needs_discovery", "solution_guidance", "next_steps_summary",
    "objection_handling", "value_positioning", "assumptive_close",
    "compliance_disclosures", "call_control", "professional_tone",
]
_CALL_HANDLING_METRICS = [
    "comprehension", "language_proficiency", "emotional_intelligence",
    "relationship_building", "professional_skills", "subject_matter_expertise",
]
for _k in _BEHAVIOR_METRICS:
    METRIC_TABLE_MAP[f"bs_{_k}"] = "scores"
    METRIC_TABLE_MAP[_k] = "scores"  # allow without prefix too
for _k in _CALL_HANDLING_METRICS:
    METRIC_TABLE_MAP[f"ch_{_k}"] = "scores"
    METRIC_TABLE_MAP[_k] = "scores"

# WCC metrics table columns
_WCC_KPI_METRICS = [
    "resolution_opportunity_exists", "resolution_attempted", "resolution_actual",
    "save_opportunity_exists", "save_attempted", "save_actual",
    "sale_opportunity_exists", "sale_attempted", "sale_actual",
    "resolution_attempted_rate", "resolution_actual_rate",
    "save_attempted_rate", "save_actual_rate",
    "sale_attempted_rate", "sale_actual_rate",
]
_WCC_BEHAVIOR_METRICS = [
    "greeting_connection", "build_connection_active_listening",
    "build_connection_empathize", "build_connection_ownership",
    "set_next_steps_review_for_save_opportunities",
    "gather_information_use_tools_resources",
    "gather_information_ask_questions_to_understand",
    "gather_information_uncover_needs",
    "gather_information_watch_out_for_bells_of_churn",
    "address_initial_needs_present_solution_gain_agreement",
    "test_for_resolution_ensure_successful_before_moving_forward",
    "sell_transition_statements", "add_value_position_additional_solutions",
    "overcome_objections_reframe_resolve_use_empathy_benefit_focused_responses",
    "sso_enablement", "set_up_for_success_summarize_recap",
    "set_up_for_success_set_clear_expectations_next_steps",
    "ask_additional_concerns_after_resolving_main_concern",
    "nps_survey_spiel", "closing_restate_commitment_appreciation",
    "build_connection", "gather_information", "set_up_for_success",
]
for _k in _WCC_KPI_METRICS:
    METRIC_TABLE_MAP[f"wkpi_{_k}"] = "wcc_metrics"
    METRIC_TABLE_MAP[_k] = "wcc_metrics"
for _k in _WCC_BEHAVIOR_METRICS:
    METRIC_TABLE_MAP[f"wbs_{_k}"] = "wcc_metrics"

# PSO metrics table columns (customer-care KPIs)
_PSO_KPI_METRICS = [
    "predicted_csat", "customer_confidence", "customer_effort",
    "fcr_likelihood", "resolution_completeness", "resolution_confidence",
    "next_steps_clarity", "issue_resolution_effectiveness", "quality",
    "compliance", "process_adherence", "issue_ownership",
    "escalation_handling", "transfer_avoidance", "case_management",
    "aht_efficiency", "contact_handling_efficiency", "hold_management",
    "repeat_contact_risk", "escalation_risk", "callback_risk", "reopen_risk",
]
for _k in _PSO_KPI_METRICS:
    METRIC_TABLE_MAP[f"pkpi_{_k}"] = "pso_metrics"
    METRIC_TABLE_MAP[_k] = "pso_metrics"

# Comparison table
_CMP_METRICS = ["resolution_rate", "save_rate", "sale_rate"]
for _k in _CMP_METRICS:
    METRIC_TABLE_MAP[f"cmp_{_k}"] = "comparison"

# Trends and coaching
METRIC_TABLE_MAP["trends"] = "trends"
METRIC_TABLE_MAP["coaching"] = "coaching"


def _singularize(word: str) -> str:
    """Light, conservative singularization so plurals match their phrase keys.

    Handles the common English plural endings the metric phrases need
    ("counts"→"count", "attempts"→"attempt", "pitches"→"pitch",
    "discoveries"→"discovery") without over-stemming short words or ``-ss``
    endings ("less" stays "less").
    """
    if len(word) < 4 or not word.endswith("s") or word.endswith("ss"):
        return word
    if word.endswith("ies"):
        return word[:-3] + "y"
    if word.endswith(("ches", "shes", "sses", "xes", "zes")):
        return word[:-2]
    return word[:-1]


def _stem_tokens(text: str) -> set[str]:
    """Tokenize *text* into a set of singularized word tokens for matching."""
    return {
        _singularize(w)
        for w in re.split(r"[^a-z0-9]+", text.lower())
        if w
    }


def _resolve_metrics(question: str) -> list[dict]:
    """Match metric names from the question to table + column.

    Matching is **order-insensitive** (a phrase matches when all of its words
    appear, in any order — "actual resolution" == "resolution actual") and
    **specificity-aware** (a more specific phrase wins over a generic one whose
    words it contains — "resolution actual" beats the bare "resolution"), so a
    generic fallback can no longer hijack a precise metric. Plurals are matched
    via light singularization.
    """
    q_lower = question.lower()
    q_norm = re.sub(r"[_\-]", " ", q_lower)
    q_tokens = _stem_tokens(q_norm)

    matched: list[dict] = []
    seen: set[str] = set()

    # Natural-language phrase → (table, column). Aligned with the canonical
    # KPI data dictionary (kpis / wcc_kpis / rate_kpis / nl_to_kpi_mapping).
    # IMPORTANT: plain "resolution rate" / "save rate" / "sale rate" /
    # "conversion rate" are the *actual-rate* success KPIs that live in
    # wcc_metrics (wkpi_*_actual_rate) — NOT the comparison/benchmark cmp_*
    # columns (those are reached only via compare/benchmark queries → LLM).
    _NL_TO_COL = {
        # ── Behavior scores (scores.bs_*) ──
        "active listening": ("scores", "bs_active_listening"),
        "acknowledgment": ("scores", "bs_acknowledgment"),
        "empathy": ("scores", "bs_empathy"),
        "confidence": ("scores", "bs_confidence"),
        "clarity": ("scores", "bs_clarity"),
        "needs discovery": ("scores", "bs_needs_discovery"),
        "solution guidance": ("scores", "bs_solution_guidance"),
        "next steps summary": ("scores", "bs_next_steps_summary"),
        "objection handling": ("scores", "bs_objection_handling"),
        "value positioning": ("scores", "bs_value_positioning"),
        "assumptive close": ("scores", "bs_assumptive_close"),
        "compliance disclosures": ("scores", "bs_compliance_disclosures"),
        "call control": ("scores", "bs_call_control"),
        "professional tone": ("scores", "bs_professional_tone"),
        # ── Call-handling / soft skills (scores.ch_*) ──
        "comprehension": ("scores", "ch_comprehension"),
        "language proficiency": ("scores", "ch_language_proficiency"),
        "emotional intelligence": ("scores", "ch_emotional_intelligence"),
        "relationship building": ("scores", "ch_relationship_building"),
        "professional skills": ("scores", "ch_professional_skills"),
        "subject matter expertise": ("scores", "ch_subject_matter_expertise"),
        "behavior score": ("scores", None),
        "call handling": ("scores", None),
        # ── KPIs (kpis.*) — sales activity / opportunity / experience ──
        "new line pitch": ("kpis", "new_line_pitches"),
        "new line offer": ("kpis", "new_line_pitches"),
        "add line attempt": ("kpis", "new_line_pitches"),
        "line selling": ("kpis", "new_line_pitches"),
        "new line opportunity": ("kpis", "new_line_opportunity_exists"),
        "line opportunity": ("kpis", "new_line_opportunity_exists"),
        "missed new line": ("kpis", "new_line_opportunity_missed"),
        "lost line opportunity": ("kpis", "new_line_opportunity_missed"),
        "upgrade attempt": ("kpis", "upgrade_attempts"),
        "plan upgrade": ("kpis", "upgrade_attempts"),
        "upgrade opportunity": ("kpis", "upgrade_opportunity_exists"),
        "missed upgrade": ("kpis", "upgrade_opportunity_missed"),
        "save attempt": ("kpis", "save_attempts"),
        "retention attempt": ("kpis", "save_attempts"),
        # WCC-qualified variants win over the generic kpis phrase above via the
        # specificity filter (the extra "wcc" token makes them more specific).
        "wcc save attempt": ("wcc_metrics", "wkpi_save_attempted"),
        "save attempt wcc": ("wcc_metrics", "wkpi_save_attempted"),
        "fwa attempt": ("kpis", "fwa_attempts"),
        "fwa sale": ("kpis", "fwa_attempts"),
        "fixed wireless": ("kpis", "fwa_attempts"),
        "mobile protection": ("kpis", "mobile_protection_attempts"),
        "device protection": ("kpis", "mobile_protection_attempts"),
        "insurance sale": ("kpis", "mobile_protection_attempts"),
        "protection offer": ("kpis", "mobile_protection_attempts"),
        "we got you": ("kpis", "we_got_you_utterances"),
        "reassurance phrase": ("kpis", "we_got_you_utterances"),
        "comfort statement": ("kpis", "we_got_you_utterances"),
        "escalation": ("kpis", "escalations"),
        "manager escalation": ("kpis", "escalations"),
        "supervisor escalation": ("kpis", "escalations"),
        "customer experience": ("kpis", "customer_experience"),
        "customer satisfaction": ("kpis", "customer_experience"),
        "csat": ("kpis", "customer_experience"),
        "experience score": ("kpis", "customer_experience"),
        # ── New prospect (kpis.np_*) ──
        "new prospect": ("kpis", "np_total"),
        "prospect conversion": ("kpis", "np_conversion_rate"),
        "prospect conversion rate": ("kpis", "np_conversion_rate"),
        # ── WCC resolution counts (wcc_metrics.wkpi_resolution_*) ──
        "resolvable case": ("wcc_metrics", "wkpi_resolution_opportunity_exists"),
        "resolution opportunity": ("wcc_metrics", "wkpi_resolution_opportunity_exists"),
        "resolution attempted": ("wcc_metrics", "wkpi_resolution_attempted"),
        "resolution attempt": ("wcc_metrics", "wkpi_resolution_attempted"),
        "attempted resolution": ("wcc_metrics", "wkpi_resolution_attempted"),
        "resolution try": ("wcc_metrics", "wkpi_resolution_attempted"),
        "resolution actual": ("wcc_metrics", "wkpi_resolution_actual"),
        "actual resolution": ("wcc_metrics", "wkpi_resolution_actual"),
        "resolution count": ("wcc_metrics", "wkpi_resolution_actual"),
        "resolved": ("wcc_metrics", "wkpi_resolution_actual"),
        "issue resolved": ("wcc_metrics", "wkpi_resolution_actual"),
        "issue fixed": ("wcc_metrics", "wkpi_resolution_actual"),
        # ── Resolution rates (wcc_metrics.wkpi_resolution_*_rate) ──
        "resolution rate": ("wcc_metrics", "wkpi_resolution_actual_rate"),
        "resolution actual rate": ("wcc_metrics", "wkpi_resolution_actual_rate"),
        "fix success": ("wcc_metrics", "wkpi_resolution_actual_rate"),
        "fcr rate": ("wcc_metrics", "wkpi_resolution_actual_rate"),
        "resolution attempted rate": ("wcc_metrics", "wkpi_resolution_attempted_rate"),
        "resolution effort rate": ("wcc_metrics", "wkpi_resolution_attempted_rate"),
        # ── WCC save / retention counts (wcc_metrics.wkpi_save_*) ──
        "churn risk": ("wcc_metrics", "wkpi_save_opportunity_exists"),
        "retention opportunity": ("wcc_metrics", "wkpi_save_opportunity_exists"),
        "save attempted": ("wcc_metrics", "wkpi_save_attempted"),
        "save actual": ("wcc_metrics", "wkpi_save_actual"),
        "save count": ("wcc_metrics", "wkpi_save_actual"),
        "retained customer": ("wcc_metrics", "wkpi_save_actual"),
        # ── Save rates (wcc_metrics.wkpi_save_*_rate) ──
        "save rate": ("wcc_metrics", "wkpi_save_actual_rate"),
        "save actual rate": ("wcc_metrics", "wkpi_save_actual_rate"),
        "retention rate": ("wcc_metrics", "wkpi_save_actual_rate"),
        "survival rate": ("wcc_metrics", "wkpi_save_actual_rate"),
        "customer survival rate": ("wcc_metrics", "wkpi_save_actual_rate"),
        "save attempted rate": ("wcc_metrics", "wkpi_save_attempted_rate"),
        "save effort rate": ("wcc_metrics", "wkpi_save_attempted_rate"),
        # ── WCC sale counts (wcc_metrics.wkpi_sale_*) ──
        "sale opportunity": ("wcc_metrics", "wkpi_sale_opportunity_exists"),
        "sales opportunity": ("wcc_metrics", "wkpi_sale_opportunity_exists"),
        "sale attempted": ("wcc_metrics", "wkpi_sale_attempted"),
        "sales attempt": ("wcc_metrics", "wkpi_sale_attempted"),
        "wcc sale attempt": ("wcc_metrics", "wkpi_sale_attempted"),
        "sale attempt wcc": ("wcc_metrics", "wkpi_sale_attempted"),
        "sales pitch": ("wcc_metrics", "wkpi_sale_attempted"),
        "sale actual": ("wcc_metrics", "wkpi_sale_actual"),
        "sale count": ("wcc_metrics", "wkpi_sale_actual"),
        "sales made": ("wcc_metrics", "wkpi_sale_actual"),
        "closed deal": ("wcc_metrics", "wkpi_sale_actual"),
        # ── Sale rates (wcc_metrics.wkpi_sale_*_rate) ──
        "sale rate": ("wcc_metrics", "wkpi_sale_actual_rate"),
        "sale actual rate": ("wcc_metrics", "wkpi_sale_actual_rate"),
        "conversion rate": ("wcc_metrics", "wkpi_sale_actual_rate"),
        "sales success rate": ("wcc_metrics", "wkpi_sale_actual_rate"),
        "sale attempted rate": ("wcc_metrics", "wkpi_sale_attempted_rate"),
        "sales effort rate": ("wcc_metrics", "wkpi_sale_attempted_rate"),
        # ── WCC behavior skills (wcc_metrics.wbs_*) ──
        # WCC agents are scored on a different behavior rubric than telesales.
        # These phrases are WCC-native (no telesales equivalent) so they map
        # straight to the wbs_* columns; overlapping terms like "empathy" /
        # "active listening" stay on the telesales columns above and are
        # remapped per-agent by _remap_metrics_for_wcc when a WCC agent is named.
        "greeting connection": ("wcc_metrics", "wbs_greeting_connection"),
        "build connection": ("wcc_metrics", "wbs_build_connection"),
        "ownership": ("wcc_metrics", "wbs_build_connection_ownership"),
        "take ownership": ("wcc_metrics", "wbs_build_connection_ownership"),
        "gather information": ("wcc_metrics", "wbs_gather_information"),
        "uncover needs": ("wcc_metrics", "wbs_gather_information_uncover_needs"),
        "ask questions": ("wcc_metrics", "wbs_gather_information_ask_questions_to_understand"),
        "use tools": ("wcc_metrics", "wbs_gather_information_use_tools_resources"),
        "bells of churn": ("wcc_metrics", "wbs_gather_information_watch_out_for_bells_of_churn"),
        "churn signals": ("wcc_metrics", "wbs_gather_information_watch_out_for_bells_of_churn"),
        "test for resolution": ("wcc_metrics", "wbs_test_for_resolution_ensure_successful_before_moving_forward"),
        "transition statement": ("wcc_metrics", "wbs_sell_transition_statements"),
        "add value": ("wcc_metrics", "wbs_add_value_position_additional_solutions"),
        "sso enablement": ("wcc_metrics", "wbs_sso_enablement"),
        "set up for success": ("wcc_metrics", "wbs_set_up_for_success"),
        "nps survey": ("wcc_metrics", "wbs_nps_survey_spiel"),
        # ── PSO customer-care KPIs (pso_metrics.pkpi_*) ── these concepts are
        # PSO-only, so they resolve to pso_metrics regardless of program. The
        # ambiguous "csat" / "customer satisfaction" concept is handled
        # program-aware in _remap_metrics_for_program (PSO → predicted csat).
        "predicted csat": ("pso_metrics", "pkpi_predicted_csat"),
        "customer confidence": ("pso_metrics", "pkpi_customer_confidence"),
        "customer effort": ("pso_metrics", "pkpi_customer_effort"),
        "fcr likelihood": ("pso_metrics", "pkpi_fcr_likelihood"),
        "first contact resolution": ("pso_metrics", "pkpi_fcr_likelihood"),
        "resolution completeness": ("pso_metrics", "pkpi_resolution_completeness"),
        "resolution confidence": ("pso_metrics", "pkpi_resolution_confidence"),
        "next steps clarity": ("pso_metrics", "pkpi_next_steps_clarity"),
        "issue resolution effectiveness": ("pso_metrics", "pkpi_issue_resolution_effectiveness"),
        "process adherence": ("pso_metrics", "pkpi_process_adherence"),
        "issue ownership": ("pso_metrics", "pkpi_issue_ownership"),
        "escalation handling": ("pso_metrics", "pkpi_escalation_handling"),
        "transfer avoidance": ("pso_metrics", "pkpi_transfer_avoidance"),
        "case management": ("pso_metrics", "pkpi_case_management"),
        "aht efficiency": ("pso_metrics", "pkpi_aht_efficiency"),
        "contact handling efficiency": ("pso_metrics", "pkpi_contact_handling_efficiency"),
        "hold management": ("pso_metrics", "pkpi_hold_management"),
        "repeat contact risk": ("pso_metrics", "pkpi_repeat_contact_risk"),
        "escalation risk": ("pso_metrics", "pkpi_escalation_risk"),
        "callback risk": ("pso_metrics", "pkpi_callback_risk"),
        "reopen risk": ("pso_metrics", "pkpi_reopen_risk"),
        # ── Coaching / trends / volume / overall ──
        "coaching": ("coaching", None),
        "trend": ("trends", None),
        "call count": ("kpis", "total_call_count"),
        "total call": ("kpis", "total_call_count"),
        "total conversation": ("kpis", "total_call_count"),
        "conversation": ("kpis", "total_call_count"),
        "overall score": ("kpis", "overall_score"),
        "performance score": ("kpis", "overall_score"),
        # "overall behavior score" is a distinct column; the extra "behavior"
        # token makes it win over the generic "overall score" above.
        "overall behavior score": ("kpis", "overall_behavior_score"),
        "overall behaviour score": ("kpis", "overall_behavior_score"),
        "behavior score overall": ("kpis", "overall_behavior_score"),
    }

    # Order-insensitive matching: a phrase matches when ALL its (singularized)
    # words appear in the question, in any order. Track each phrase's word-set
    # so the more specific phrase can suppress the generic one below.
    candidates: list[tuple[frozenset[str], str, str, str | None]] = []
    for phrase, (table, column) in _NL_TO_COL.items():
        words = _stem_tokens(phrase)
        if words and words <= q_tokens:
            candidates.append((frozenset(words), phrase, table, column))

    # Specificity filter: drop any phrase whose words are a strict subset of a
    # longer matched phrase. This is what stops the generic "resolution"
    # (→ resolution rate) from co-matching alongside "resolution actual"
    # (→ resolution actual count) and producing a conflicting hint.
    kept = [
        c for c in candidates
        if not any(c[0] < other[0] for other in candidates)
    ]

    # De-duplicate by resolved (table, column) so synonym phrases pointing at
    # the same column (e.g. "resolution actual" / "resolution count") don't add
    # duplicate hints; None-column table hints are kept as-is.
    seen_cols: set[tuple[str, str | None]] = set()
    for words, phrase, table, column in kept:
        if phrase in seen:
            continue
        if column is not None and (table, column) in seen_cols:
            continue
        seen.add(phrase)
        if column is not None:
            seen_cols.add((table, column))
        matched.append({"phrase": phrase, "table": table, "column": column})

    log.info("_resolve_metrics → %d match(es) for '%s'", len(matched), question[:80])
    return matched


# ───────────────────────────────────────────────────────────────────────────
# Period (date) resolution — deterministic
# ───────────────────────────────────────────────────────────────────────────

# Matches an ISO date: 2026-04-17  (the period IS the date — no week conversion)
_RE_DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")

# Natural-language date forms we accept deterministically:
#   - "april 24 2026", "apr 24, 2026"
#   - "24 april 2026", "24-april-2026", "24 apr 2026"
_RE_MONTH_DAY_YEAR = re.compile(
    r"\b([A-Za-z]{3,9})\s+(\d{1,2})(?:st|nd|rd|th)?(?:,)?\s+(\d{4})\b",
    re.IGNORECASE,
)
_RE_DAY_MONTH_YEAR = re.compile(
    r"\b(\d{1,2})(?:st|nd|rd|th)?[-\s]([A-Za-z]{3,9})[-\s,]+(\d{4})\b",
    re.IGNORECASE,
)

# Month + day WITHOUT a year (e.g. "april 3", "apr 3rd", "week of april 3").
# Resolved against the available periods (the year is inferred from the store).
_RE_MONTH_DAY = re.compile(
    r"\b([A-Za-z]{3,9})\s+(\d{1,2})(?:st|nd|rd|th)?\b",
    re.IGNORECASE,
)

_MONTHS = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}

# Month names as a standalone-word regex — used to blank out calendar words
# before agent-name matching, so a month like "april" in "week april 10" is
# never mistaken for a roster first name (real agents are named April, May…).
_RE_MONTH_WORD = re.compile(
    r"\b(" + "|".join(sorted(_MONTHS, key=len, reverse=True)) + r")\b",
    re.IGNORECASE,
)


def _strip_calendar_tokens(text: str) -> str:
    """Blank out date / month references so they can't collide with names.

    Removes full dates and any standalone month word (january…december), which
    otherwise leak into name-token scanning (e.g. "april" is also a first name).
    """
    for rx in (_RE_MONTH_DAY_YEAR, _RE_DAY_MONTH_YEAR, _RE_DATE, _RE_MONTH_DAY, _RE_MONTH_WORD):
        text = rx.sub(" ", text)
    return text


def _distinct_period_count(question: str) -> int:
    """Return how many distinct calendar dates are referenced in *question*.

    Counts ISO dates and month/day(/year) forms. Used to detect multi-period
    questions ("from 2026-04-10 to 2026-04-17") where a single consolidated
    value would be misleading.
    """
    found: set[str] = set()
    for m in _RE_DATE.finditer(question):
        found.add(m.group(0).lower())
    for rx in (_RE_MONTH_DAY_YEAR, _RE_DAY_MONTH_YEAR, _RE_MONTH_DAY):
        for m in rx.finditer(question):
            found.add(m.group(0).lower())
    return len(found)


def _resolve_all_periods(question: str) -> list[str]:
    """Return every distinct reporting period (``YYYY-MM-DD``) named in *question*.

    Resolves ISO dates and month/day(/year) forms; bare month/day fragments are
    matched against the periods present in the store. Order-preserving and
    de-duplicated. Used to build multi-period breakdowns deterministically.
    """
    out: list[str] = []
    seen: set[str] = set()

    def _add(p: str | None) -> None:
        if p and p not in seen:
            seen.add(p)
            out.append(p)

    for m in _RE_DATE.finditer(question):
        try:
            _add(_date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat())
        except ValueError:
            pass
    for m in _RE_MONTH_DAY_YEAR.finditer(question):
        mo = _MONTHS.get(m.group(1).lower())
        if mo:
            try:
                _add(_date(int(m.group(3)), mo, int(m.group(2))).isoformat())
            except ValueError:
                pass
    for m in _RE_DAY_MONTH_YEAR.finditer(question):
        mo = _MONTHS.get(m.group(2).lower())
        if mo:
            try:
                _add(_date(int(m.group(3)), mo, int(m.group(1))).isoformat())
            except ValueError:
                pass
    # Bare month + day (no year) — resolve against the store's periods.
    for m in _RE_MONTH_DAY.finditer(question):
        mo = _MONTHS.get(m.group(1).lower())
        if not mo:
            continue
        day = int(m.group(2))
        for per in _PERIODS:
            parts = per.split("-")
            if len(parts) == 3 and int(parts[1]) == mo and int(parts[2]) == day:
                _add(per)
    return out


def _resolve_period(question: str) -> str | None:
    """Resolve a specific reporting period (``YYYY-MM-DD``) from the question.

    The period is the UTC date itself — an ISO date in the question is used
    verbatim.  Returns ``None`` when no date is referenced (caller then
    defaults to the latest period).
    """
    m = _RE_DATE.search(question)
    if m:
        try:
            period = _date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
            log.info("_resolve_period → date '%s'", period)
            return period
        except ValueError:
            log.warning("_resolve_period → invalid date '%s'", m.group(0))

    m = _RE_MONTH_DAY_YEAR.search(question)
    if m:
        month_txt, day_txt, year_txt = m.groups()
        month = _MONTHS.get(month_txt.lower())
        if month:
            try:
                period = _date(int(year_txt), month, int(day_txt)).isoformat()
                log.info("_resolve_period → date '%s'", period)
                return period
            except ValueError:
                log.warning("_resolve_period → invalid date '%s'", m.group(0))

    m = _RE_DAY_MONTH_YEAR.search(question)
    if m:
        day_txt, month_txt, year_txt = m.groups()
        month = _MONTHS.get(month_txt.lower())
        if month:
            try:
                period = _date(int(year_txt), month, int(day_txt)).isoformat()
                log.info("_resolve_period → date '%s'", period)
                return period
            except ValueError:
                log.warning("_resolve_period → invalid date '%s'", m.group(0))

    # Month + day without a year (e.g. "april 3", "week of apr 3") — resolve
    # against the available periods by matching month/day. When several years
    # match, the most recent period wins (periods are newest-first).
    if _PERIODS:
        md = _RE_MONTH_DAY.search(question)
        if md:
            month_md = _MONTHS.get(md.group(1).lower())
            if month_md:
                suffix = f"-{month_md:02d}-{int(md.group(2)):02d}"
                for per in _PERIODS:
                    if per.endswith(suffix):
                        log.info("_resolve_period → month/day '%s' = '%s'", md.group(0), per)
                        return per

    # Relative-week references, resolved against the loaded period list
    # (most recent first) now that the DB spans multiple weeks.
    if _PERIODS:
        q = question.lower()
        # "N weeks ago" → index N back from the latest period.
        m_ago = re.search(r"\b(\d+)\s+weeks?\s+ago\b", q)
        if m_ago:
            idx = int(m_ago.group(1))
            if 0 <= idx < len(_PERIODS):
                log.info("_resolve_period → '%s weeks ago' = '%s'", idx, _PERIODS[idx])
                return _PERIODS[idx]
        if re.search(r"\b(this|current|latest|most recent)\s+week\b", q):
            log.info("_resolve_period → this/latest week = '%s'", _PERIODS[0])
            return _PERIODS[0]
        if re.search(r"\b(last|previous|prior)\s+week\b", q) and len(_PERIODS) > 1:
            log.info("_resolve_period → last/previous week = '%s'", _PERIODS[1])
            return _PERIODS[1]

    return None


def _explicit_calendar_ref(question: str) -> str | None:
    """Return an explicitly-typed calendar reference from the question.

    - A full date (ISO / month-day-year / day-month-year) → its ``YYYY-MM-DD``.
    - A month + day without a year → the matching period if one exists, else a
      human label like ``"April 5"`` (signalling it was typed but not found).
    - Anything else (no explicit date, or only a relative reference) → ``None``.
    """
    m = _RE_DATE.search(question)
    if m:
        try:
            return _date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
        except ValueError:
            return None
    m = _RE_MONTH_DAY_YEAR.search(question)
    if m:
        mo = _MONTHS.get(m.group(1).lower())
        if mo:
            try:
                return _date(int(m.group(3)), mo, int(m.group(2))).isoformat()
            except ValueError:
                return None
    m = _RE_DAY_MONTH_YEAR.search(question)
    if m:
        mo = _MONTHS.get(m.group(2).lower())
        if mo:
            try:
                return _date(int(m.group(3)), mo, int(m.group(1))).isoformat()
            except ValueError:
                return None
    md = _RE_MONTH_DAY.search(question)
    if md:
        mo = _MONTHS.get(md.group(1).lower())
        if mo:
            day = int(md.group(2))
            suffix = f"-{mo:02d}-{day:02d}"
            for per in _PERIODS:
                if per.endswith(suffix):
                    return per
            return f"{md.group(1).title()} {day}"
    return None


def unavailable_period(question: str) -> tuple[str, list[str]] | None:
    """Detect a user-named week that is NOT in the store.

    Returns ``(typed_period, available_periods)`` when the question explicitly
    names a specific date/week that does not exist in the loaded data, so the
    caller can ask the user to pick a real week instead of silently assuming
    one.  Returns ``None`` when no explicit week is named or the named week is
    available.  Relative references ("last week") always map onto an existing
    period and are ignored here.
    """
    typed = _explicit_calendar_ref(question)
    if typed is None:
        return None
    if _PERIODS and typed in _PERIODS:
        return None
    return typed, list(_PERIODS)


# ───────────────────────────────────────────────────────────────────────────
# Specific-agent resolution — deterministic
# ───────────────────────────────────────────────────────────────────────────

# A standalone employee id (6-9 digits) referenced in the question.
_RE_EMP_ID = re.compile(r"\b(\d{6,9})\b")
# A "Lastname, Firstname" name fragment (e.g. "Badiang, Kerr A").
_RE_NAME = re.compile(r"\b([A-Z][A-Za-z.\-']+),\s*([A-Z][A-Za-z.\-']+)")
# A bare word token (used to test against known surnames).
_RE_WORD = re.compile(r"[A-Za-z][A-Za-z.\-']{2,}")

# Explicit "naming cue" — an agent noun that introduces an agent reference.
# The token captured after it is treated as an intended agent name, so we may
# fuzzy-match it against the roster even when it is misspelled (e.g.
# "agent esterlla"). Only concrete agent nouns are used as cues (NOT "for"/
# "about", which are far too noisy — "for the week", "about scores", …).
_RE_NAMING_CUE = re.compile(
    r"(?i)\b(?:agents?|reps?|representatives?|employees?|associates?|advisors?)\s+"
    r"([A-Za-z][A-Za-z.\-']{2,})"
)

# Words that may follow an agent noun without being a name ("agent performance",
# "agent scores", "agent name", …) — never treated as a (misspelled) name.
_NAME_CUE_STOPWORDS = frozenset({
    "performance", "score", "scores", "scoring", "metric", "metrics", "kpi",
    "kpis", "rate", "rates", "resolution", "empathy", "name", "names", "id",
    "ids", "number", "report", "reports", "summary", "data", "details",
    "the", "this", "that", "week", "weekly", "month", "day", "level", "type",
    "list", "roster", "team", "info", "information", "record", "records",
})

# Minimum similarity for a misspelled token to be accepted as a known name.
_FUZZY_NAME_CUTOFF = 0.8

# Stricter cutoff for a BARE (un-cued) misspelled token — without a naming cue
# there is more room for false positives, so the token must be a very close
# match to exactly one roster name to be accepted.
_BARE_FUZZY_CUTOFF = 0.85

# Common analytics / question words that must NEVER be fuzzily coerced into a
# surname when they appear WITHOUT an explicit naming cue. Combined with the
# roster surnames themselves (handled separately) and the cue stopwords above.
_BARE_NAME_STOPWORDS = _NAME_CUE_STOPWORDS | frozenset({
    "what", "when", "where", "which", "whom", "whose", "show", "tell", "give",
    "about", "doing", "trend", "trends", "trending", "improve", "improved",
    "improvement", "improvements", "area", "areas", "coaching", "coach",
    "risk", "risks", "action", "actions", "compare", "compared", "comparison",
    "versus", "against", "benchmark", "better", "worse", "best", "worst",
    "highest", "lowest", "average", "overall", "their", "there", "them",
    "they", "please", "current", "latest", "recent", "period", "weekly",
    "monthly", "should", "would", "could", "does", "done", "with", "from",
    "have", "need", "needs", "want", "over", "under", "more", "less", "than",
    "then", "into", "also", "still", "just", "like", "some", "much", "many",
    "very", "good", "great", "poor", "strong", "weak", "here", "that", "this",
    "these", "those", "how", "why", "who", "sales", "sale", "save", "saves",
    "call", "calls", "customer", "experience", "quality", "compliance",
    "escalation", "escalations", "conversion", "upgrade", "upgrades",
    "attempt", "attempts", "pitch", "pitches", "protection", "connection",
    "listening", "confidence", "clarity", "ownership", "prospect", "prospects",
})


def _fuzzy_name_token(word: str) -> str | None:
    """Return the known roster name token closest to *word*, or ``None``.

    Only used for tokens that appear in an explicit naming context (after a
    ``_RE_NAMING_CUE`` word), so ordinary metric/period words are never fuzzily
    coerced into a surname. An exact hit wins immediately; otherwise the single
    closest known token within ``_FUZZY_NAME_CUTOFF`` is returned.
    """
    if not _KNOWN_SURNAMES:
        return None
    low = word.lower()
    if low in _NAME_CUE_STOPWORDS:
        return None
    if low in _KNOWN_SURNAMES:
        return low
    if len(low) < 4:
        return None
    matches = difflib.get_close_matches(
        low, _KNOWN_SURNAMES, n=1, cutoff=_FUZZY_NAME_CUTOFF
    )
    if matches:
        log.info("_fuzzy_name_token → '%s' ≈ '%s'", word, matches[0])
        return matches[0]
    return None


def _cued_name_tokens(question: str) -> list[str]:
    """Return raw tokens that follow an explicit agent naming cue (no stopwords)."""
    return [
        m.group(1)
        for m in _RE_NAMING_CUE.finditer(question)
        if m.group(1).lower() not in _NAME_CUE_STOPWORDS
    ]


def _bare_fuzzy_name(text: str) -> str | None:
    """Return a roster name from a BARE misspelled token in *text*, or ``None``.

    Scans every word (no naming cue required) and keeps those that are a very
    close match (``_BARE_FUZZY_CUTOFF``) to a known roster name, skipping exact
    matches (handled elsewhere), roster surnames, common analytics words and
    short tokens. Accepts the result only when the surviving tokens all point to
    a SINGLE roster name, so ordinary words are never coerced into a surname and
    an ambiguous typo is left unresolved.
    """
    if not _KNOWN_SURNAMES:
        return None
    canon_hits: set[str] = set()
    for word in _RE_WORD.findall(text):
        low = word.lower()
        if (
            len(low) < 4
            or low in _KNOWN_SURNAMES          # exact — resolved by caller
            or low in _BARE_NAME_STOPWORDS     # ordinary query word
        ):
            continue
        match = difflib.get_close_matches(
            low, _KNOWN_SURNAMES, n=1, cutoff=_BARE_FUZZY_CUTOFF
        )
        if match:
            canon_hits.add(match[0])
    if len(canon_hits) == 1:
        canon = next(iter(canon_hits))
        log.info("_bare_fuzzy_name → resolved misspelled token to '%s'", canon)
        return canon
    if len(canon_hits) > 1:
        log.info("_bare_fuzzy_name → ambiguous (%s) — not resolving", canon_hits)
    return None


def _name_tokens(full: str) -> set[str]:
    """Return lower-cased name tokens from a "Surname, Firstname M" value.

    Yields the surname and each given-name token (length >= 3, so single-letter
    middle initials are skipped). Used so an agent can be matched by either
    their surname or their first name.
    """
    tokens: set[str] = set()
    if not full:
        return tokens
    parts = full.split(",")
    surname = parts[0].strip()
    if len(surname) >= 3:
        tokens.add(surname.lower())
    if len(parts) > 1:
        for tok in parts[1].split():
            tok = tok.strip(".-' ")
            if len(tok) >= 3:
                tokens.add(tok.lower())
    return tokens



def _load_known_surnames(db_path: str = DB_PATH) -> set[str]:
    """Return the set of lower-cased name tokens present in the live DB.

    Includes both the surname (before the comma) AND the given-name tokens
    (after the comma) of every ``employee_name`` — names are stored as
    "Surname, Firstname M", so a user may refer to an agent by either part
    (e.g. "Badiang" or "Kerr"). Single-letter middle initials are skipped to
    avoid false-positives. Falls back to an empty set if the DB is unavailable.
    """
    names: set[str] = set()
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            for tbl in ("kpis", "scores"):
                try:
                    rows = con.execute(f"SELECT DISTINCT employee_name FROM {tbl}").fetchall()
                except sqlite3.Error:
                    continue
                for (full,) in rows:
                    if not full:
                        continue
                    for token in _name_tokens(full):
                        names.add(token)
        finally:
            con.close()
    except sqlite3.Error as exc:
        log.warning("_load_known_surnames → DB unavailable (%s); bare-surname match disabled", exc)
    log.info("_load_known_surnames → %d known surnames loaded", len(names))
    return names



# Built once at import from the live DB.
_KNOWN_SURNAMES: set[str] = _load_known_surnames()


def _load_employee_index(db_path: str = DB_PATH) -> dict[str, set[str]]:
    """Return a ``{name_token_lower: {employee_id, ...}}`` index from the DB.

    Lets a bare surname, a given name, or a "Lastname, Firstname" fragment be
    mapped to the concrete employee id(s) it belongs to. Falls back to an empty
    index if the DB is unavailable.
    """
    index: dict[str, set[str]] = {}
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            for tbl in ("kpis", "scores"):
                try:
                    rows = con.execute(
                        f"SELECT DISTINCT employee_id, employee_name FROM {tbl}"
                    ).fetchall()
                except sqlite3.Error:
                    continue
                for emp_id, full in rows:
                    if not emp_id or not full:
                        continue
                    for token in _name_tokens(full):
                        index.setdefault(token, set()).add(str(emp_id))
        finally:
            con.close()
    except sqlite3.Error as exc:
        log.warning("_load_employee_index → DB unavailable (%s); name→id map disabled", exc)
    log.info("_load_employee_index → %d surnames mapped to ids", len(index))
    return index


# Built once at import from the live DB.
_KNOWN_EMP_INDEX: dict[str, set[str]] = _load_employee_index()


def _load_employee_surnames(db_path: str = DB_PATH) -> dict[str, str]:
    """Return a ``{employee_id: surname}`` map from the live DB.

    Lets a numeric employee id (e.g. the UI's currently-selected agent) be
    turned into a display surname for context injection / narration. Falls back
    to an empty map if the DB is unavailable.
    """
    by_id: dict[str, str] = {}
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            for tbl in ("kpis", "scores"):
                try:
                    rows = con.execute(
                        f"SELECT DISTINCT employee_id, employee_name FROM {tbl}"
                    ).fetchall()
                except sqlite3.Error:
                    continue
                for emp_id, full in rows:
                    if not emp_id or not full:
                        continue
                    surname = str(full).split(",", 1)[0].strip()
                    if surname:
                        by_id.setdefault(str(emp_id), surname)
        finally:
            con.close()
    except sqlite3.Error as exc:
        log.warning("_load_employee_surnames → DB unavailable (%s); id→name map disabled", exc)
    return by_id


# Built once at import from the live DB.
_EMP_ID_TO_SURNAME: dict[str, str] = _load_employee_surnames()


def agent_from_employee_id(employee_id: int | str | None) -> dict | None:
    """Build an agent-context dict from a numeric employee id.

    Returns ``{"employee_id": str, "last_name": str}`` (surname omitted when the
    id is unknown to the local store), or ``None`` when *employee_id* is falsy.
    Used to turn the UI's currently-selected agent into the authoritative agent
    context for the chat pipeline.
    """
    if not employee_id:
        return None
    eid = str(employee_id).strip()
    if not eid:
        return None
    agent: dict = {"employee_id": eid}
    surname = _EMP_ID_TO_SURNAME.get(eid)
    if surname:
        agent["last_name"] = surname
    return agent


def agent_in_scope(agent: dict | None, employee_ids: list[int] | None) -> bool:
    """True when *agent* is within the caller's authorised roster.

    * ``None`` agent (team-wide) → always in scope.
    * An empty roster → treated as unrestricted (nothing to enforce here; the
      SQL layer still applies its own guards).
    * An agent with an ``employee_id`` → must be a member of the roster.
    * An agent known only by ``last_name`` → at least one employee bearing that
      name must be in the roster; an unknown name is left for the clarifier.
    """
    if not agent:
        return True
    allowed = {str(e) for e in (employee_ids or [])}
    if not allowed:
        return True

    eid = agent.get("employee_id")
    if eid:
        return str(eid) in allowed

    name = agent.get("last_name")
    if name:
        ids = _KNOWN_EMP_INDEX.get(str(name).lower(), set())
        if not ids:
            return True  # unknown name — let the ambiguity clarifier handle it
        return bool(ids & allowed)

    return True


def resolve_employee_ids(text: str) -> list[str]:
    """Return employee ids referenced by *text* (explicit ids + known names).

    Combines: any 6–9 digit ids written literally, plus surnames (from a
    "Lastname, Firstname" fragment or a bare known surname) mapped to their
    employee id(s) via the DB index. Returns a sorted, de-duplicated list of
    string ids. The caller is responsible for intersecting with the user's
    authorized scope — this function performs no authorization itself.
    """
    ids: set[str] = set(_RE_EMP_ID.findall(text))

    surnames: set[str] = {m[0].lower() for m in _RE_NAME.findall(text)}
    if _KNOWN_EMP_INDEX:
        for word in _RE_WORD.findall(text):
            low = word.lower()
            if low in _KNOWN_EMP_INDEX:
                surnames.add(low)

    for surname in surnames:
        ids.update(_KNOWN_EMP_INDEX.get(surname, set()))

    # Typo-tolerant fallback: resolve misspelled names that appear right after
    # an explicit naming cue (e.g. "agent esterlla" → "Estrella").
    if _KNOWN_EMP_INDEX:
        for token in _cued_name_tokens(text):
            if token.lower() in surnames:
                continue
            canon = _fuzzy_name_token(token)
            if canon:
                ids.update(_KNOWN_EMP_INDEX.get(canon, set()))

    resolved = sorted(ids)
    if resolved:
        log.info("resolve_employee_ids → %d id(s) from text", len(resolved))
    return resolved


def _resolve_agent(question: str) -> dict | None:
    """Detect a reference to a *single specific* agent in the question.

    Returns a dict with an ``employee_id`` and/or ``last_name`` key, or
    ``None`` when the question is team-wide (no specific agent named).
    """
    result: dict = {}

    m_id = _RE_EMP_ID.search(question)
    if m_id:
        result["employee_id"] = m_id.group(1)

    m_name = _RE_NAME.search(question)
    if m_name:
        result["last_name"] = m_name.group(1)
    elif _KNOWN_SURNAMES:
        # No "Lastname, Firstname" fragment — look for a bare name token
        # (surname OR first name) that matches a real employee in the DB
        # (avoids matching metric words). Strip calendar words first so a month
        # like "april" in "week april 10" is not mistaken for a first name.
        scan_text = _strip_calendar_tokens(question)
        matched = {
            w for w in _RE_WORD.findall(scan_text) if w.lower() in _KNOWN_SURNAMES
        }
        if len(matched) == 1:
            name = matched.pop()
            # Prefer a concrete employee_id when the name maps to exactly one
            # agent — the most precise, unambiguous filter.
            ids = _KNOWN_EMP_INDEX.get(name.lower(), set())
            if len(ids) == 1 and "employee_id" not in result:
                result["employee_id"] = next(iter(ids))
            else:
                result["last_name"] = name
        elif not matched:
            # No exact name token — try a typo-tolerant match on any token that
            # follows an explicit naming cue (e.g. "agent esterlla").
            for token in _cued_name_tokens(question):
                canon = _fuzzy_name_token(token)
                if not canon:
                    continue
                ids = _KNOWN_EMP_INDEX.get(canon, set())
                if len(ids) == 1 and "employee_id" not in result:
                    result["employee_id"] = next(iter(ids))
                else:
                    result["last_name"] = canon
                break

            # Still nothing — try a stricter typo-tolerant match on BARE tokens
            # (no naming cue), so "how is esterlla doing" resolves to Estrella.
            # Only accept when exactly ONE roster name is a close match, to avoid
            # coercing ordinary words into a surname.
            if not result:
                canon = _bare_fuzzy_name(scan_text)
                if canon:
                    ids = _KNOWN_EMP_INDEX.get(canon, set())
                    if len(ids) == 1:
                        result["employee_id"] = next(iter(ids))
                    else:
                        result["last_name"] = canon

    if result:
        log.info("_resolve_agent → specific agent %s", result)
        return result
    return None


def unresolved_named_agent(question: str) -> tuple[str, list[str]] | None:
    """Detect an agent named via a cue that could not be resolved to the roster.

    Returns ``(typed_name, suggestions)`` when *question* references an agent
    with an explicit cue (e.g. "agent esterlla", "for badiang") whose token
    matches neither a real name nor a close (typo-tolerant) roster name; the
    suggestions are the nearest roster display names, if any. Returns ``None``
    when no cued name is present or the name resolves normally — in which case
    the caller should proceed as usual.

    This lets the orchestrator ask for clarification instead of silently
    reusing whatever agent was mentioned earlier in the conversation.
    """
    if not _KNOWN_SURNAMES:
        return None
    # A concrete id or a "Lastname, Firstname" fragment is unambiguous.
    if _RE_EMP_ID.search(question) or _RE_NAME.search(question):
        return None

    cued = _cued_name_tokens(question)
    if not cued:
        return None

    for token in cued:
        low = token.lower()
        if low in _KNOWN_SURNAMES or _fuzzy_name_token(token):
            return None  # resolvable (exactly or via typo tolerance)

    # None of the cued tokens resolved — surface the first unknown one with
    # the closest roster suggestions to help the user correct it.
    unknown = cued[0]
    close = difflib.get_close_matches(
        unknown.lower(), _KNOWN_SURNAMES, n=3, cutoff=0.6
    )
    suggestions = sorted({_display_name_for(tok) for tok in close} - {""})
    log.info("unresolved_named_agent → '%s' (suggestions=%s)", unknown, suggestions)
    return unknown, suggestions


def _display_name_for(token: str) -> str:
    """Return a roster display name (``Surname, Firstname``) for a name *token*.

    Picks the first employee whose name contains *token*; used only to make
    clarification suggestions human-readable. Returns ``""`` if unavailable.
    """
    ids = _KNOWN_EMP_INDEX.get(token.lower())
    if not ids:
        return ""
    emp_id = next(iter(ids))
    try:
        con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        try:
            for tbl in ("kpis", "scores"):
                try:
                    row = con.execute(
                        f"SELECT employee_name FROM {tbl} WHERE employee_id = ? LIMIT 1",
                        (emp_id,),
                    ).fetchone()
                except sqlite3.Error:
                    continue
                if row and row[0]:
                    return str(row[0])
        finally:
            con.close()
    except sqlite3.Error:
        pass
    return token.title()


# ───────────────────────────────────────────────────────────────────────────
# Deterministic SQL fast-path — skips the LLM for simple single-metric lookups
# ───────────────────────────────────────────────────────────────────────────

# Keywords that ALWAYS need ranking / comparison / grouping → use the LLM.
# These cannot be answered by a single-metric SELECT, so the deterministic
# fast-path bails out and lets the model compose the query.
_RANK_COMPARE = re.compile(
    r"(?i)\b(top\s*\d*|bottom\s*\d*|best|worst|highest|lowest|maximum|minimum|"
    r"max|min|rank|ranking|most|least|compare|comparison|versus|\bvs\b|"
    r"benchmark|trend|trending|over\s+time|more\s+than|less\s+than|greater|"
    r"fewer|between|above|below|exceed|under\s+\d|group\s+by|per\s+\w+|each\b)\b"
)

# Statistical-aggregate words. For a SINGLE metric these map cleanly to a
# deterministic AVG/SUM over the consolidation window (a specific agent's weeks,
# or the whole team), so we keep them on the fast-path instead of the LLM.
_STAT_AGG = re.compile(r"(?i)\b(avg|average|mean|median|sum|total|overall)\b")

# Explicit central-tendency intent. When the user literally asks for the
# average / mean / median of a metric, the team aggregate must be AVG even for
# count columns that would otherwise default to SUM (e.g. "average call count").
_WANTS_AVG = re.compile(r"(?i)\b(avg|average|mean|median)\b")

# Superlative / ranking words — these questions ("who has the MOST …", "top 3")
# stay on the LLM/ranking path and must NOT be hijacked by the multi-agent /
# multi-metric breakdown branches below.
_SUPERLATIVE = re.compile(
    r"(?i)\b(top|bottom|best|worst|highest|lowest|max|min|maximum|minimum|"
    r"most|least|rank|ranking)\b"
)

# "All behaviour scores" intent — enumerate every behaviour sub-score (bs_*)
# for a named agent. Requires the explicit word "all" (or "sub-scores") so a
# single "overall behavior score" lookup is never captured.
_ALL_BEHAVIOURS = re.compile(
    r"(?i)(\ball\s+(the\s+)?behaviou?r(al)?\s+(scores?|metrics?)|"
    r"behaviou?r(al)?\s+sub-?scores?|\ball\s+soft[- ]skills?)"
)

# Team-average / benchmark intent. When the user asks for the *team* figure of
# a metric (even while naming an agent for context), we must return the team
# benchmark — for comparison metrics this is the stored ``cmp_*_benchmark``
# sibling; for every other metric it is the roster-wide aggregate.
_TEAM_BENCHMARK = re.compile(
    r"(?i)(team\s*[- ]?\s*(average|avg|mean|benchmark|wide)|"
    r"\bbenchmark\b|average\s+(for|across|of)\s+the\s+team|team'?s\b)"
)

# Soft count phrases — a direct value lookup when a specific agent is named,
# a team SUM otherwise.
_SOFT_COUNT = re.compile(r"(?i)\b(how\s+many|number\s+of)\b")

# Qualitative "coaching / improvement" intent — where an agent is lacking, what
# to improve, recommended trainings, or what's missing to drive more sales.
# These map to the ``coaching`` table (actionable tips) rather than a metric.
_ADVICE = re.compile(
    r"(?i)(lacking|weakness|weaknesses|\bweak\b|struggl|improvement|improve|"
    r"areas?\s+to\s+(work|improve|focus)|coaching|trainings?|recommend|"
    r"drive\s+more\s+sales|help\s+.*\b(sell|resolve|improve)\b|"
    r"focus\s+areas?|development\s+areas?)"
)

# Improvement-area intent — the JSON ``key_improvements`` (the most important
# areas to improve). Maps to the ``improvements`` table. Kept DISTINCT from
# coaching recommendations so each can be addressed explicitly.
_IMPROVEMENT = re.compile(
    r"(?i)(improvement\s+areas?|areas?\s+(for|of|to)\s+improv|"
    r"key\s+improvements?|room\s+for\s+improvement|development\s+areas?|"
    r"what\s+(should|can|does|do|to)\s+.*\bimprove\b|where\s+.*\bimprove\b)"
)

# Coaching-recommendation intent — the JSON ``coaching_tips``. Maps to the
# ``coaching`` table.
_COACHING = re.compile(
    r"(?i)(coaching\s+(recommendation|tip|advice|suggestion)|coaching|"
    r"\btips?\b|trainings?|recommend|action\s+items?|next\s+steps?)"
)

# Developmental / advisory intent — questions the database has no dedicated
# data for: career guidance, professional development, promotion readiness,
# personal growth, "about himself/herself" development. These are answered by
# assembling the agent's full KPI profile for the period and narrating it from
# a growth/development perspective (analysis_focus = "development"), rather than
# returning "no data".
_DEVELOPMENT = re.compile(
    r"(?i)("
    r"car(?:eer|rier|rer|reer)\s+(guidance|advice|development|growth|"
    r"advancement|progression|path|goals?|prospects?|plan)|"
    r"professional\s+(development|growth)|grow\s+professionally|"
    r"\bpromotion\b|\bpromote(d|able)?\b|ready\s+for\s+(a\s+)?promotion|"
    r"next\s+level|move\s+up|advance(ment)?\s+(his|her|their|the)\s+career|"
    r"personal\s+(development|growth)|self[-\s]?(development|improvement)|"
    r"develop\s+(himself|herself|themsel(f|ves))|"
    r"how\s+(can|could|should|do|does)\s+(he|she|they)\s+"
    r"(grow|develop|advance|progress|improve\s+overall)|"
    r"guidance\s+(for|to)\s+(him|her|them)|guide\s+(him|her|them)|"
    r"about\s+(himself|herself|themsel(f|ves))|"
    r"career\s+coaching|future\s+(position|role|positions|roles)|"
    r"grow\s+(in|into|toward)|readiness\s+(for|to)|ready\s+to\s+lead|"
    r"become\s+a\s+(coach|lead|leader|supervisor|mentor)|"
    r"(team\s+)?lead(ership)?\s+(role|position|potential|readiness)|"
    r"strongest\s+(kpi|kpis|area|areas|skill|skills)"
    r")"
)

# Named-training recommendation intent — "what trainings should I suggest /
# recommend for X", "which training pays off", "upskilling plan". Distinct from
# the generic coaching-tip ask: it maps the agent's WEAKEST behaviours / KPIs to
# concrete, named training programmes. Routed through the consolidated profile
# (focus = "training") so the recommendation is grounded in the agent's own weak
# areas, key improvements and coaching tips.
_TRAINING = re.compile(
    r"(?i)(\btrainings?\b|\bupskill(ing)?\b|\bre[- ]?skill(ing)?\b|"
    r"training\s+(plan|programme?|recommend|need|module|course)|"
    r"skill[-\s]?(gap|development)\s+plan|learning\s+plan|"
    r"which\s+course|what\s+course)"
)

# Downward-trend intent — which KPIs are declining. Maps to the ``trends`` table
# (w0 = current week … w4 = oldest), reported where the metric fell (w0 < w4).
_TREND_DOWN = re.compile(
    r"(?i)(trend(ing)?\s+down|downward|declin|dropping|decreas|falling|"
    r"slipping|going\s+down|getting\s+worse|worsening)"
)
_TREND_UP = re.compile(
    r"(?i)(trend(ing)?\s+up|upward|improving|increasing|rising|getting\s+better)"
)

# Consolidated-profile intent — "tell me about X", "give me an overview /
# rundown / scorecard / report card", "how is X doing". Produces a single
# consolidated snapshot (headline KPIs + behaviours + key improvements +
# coaching recommendations) for one named agent.
_PROFILE = re.compile(
    r"(?i)(tell\s+me\s+about|give\s+me\s+(a\s+|an\s+)?(rundown|overview|summary|"
    r"snapshot|breakdown|report\s+card|scorecard|report)|\boverview\b|"
    r"\bprofile\b|\bscorecard\b|report\s+card|full\s+picture|"
    r"everything\s+about|walk\s+me\s+through|how\s+(is|are|did|has|have)\s+"
    r".+\s+(doing|perform(ing|ed)?|getting\s+on))"
)

# Operational-risk / actions-to-be-taken intent — actionable items and risk
# signals for an agent (or the team). Routes to the combined improvements +
# coaching recommendation output.
_ACTIONS = re.compile(
    r"(?i)(operational\s+risk|risk\s+areas?|risk\s+factors?|risk\s+of\b|"
    r"\brisks?\b|\bat\s+risk\b|action(s)?\s+"
    r"(to\s+be\s+taken|to\s+take|required|needed|item(s)?|plan)|"
    r"actions?\s+to\s+take|recommended\s+actions?|what\s+(should|do|can)\s+"
    r"(we|i|they)\s+do|steps?\s+to\s+take|remediat|mitigat|corrective\s+action|"
    r"needs?\s+attention|flag(ged)?\s+for)"
)

# Ratio / score columns are averaged across the consolidation window; every
# other numeric KPI is a count and is summed.
_RATIO_PREFIXES = ("bs_", "ch_", "wbs_", "cmp_")
_RATIO_EXACT = {
    "overall_score", "overall_behavior_score", "customer_experience",
    "np_conversion_rate",
}


def _is_ratio_col(col: str) -> bool:
    """True when *col* is a rate/score (→ AVG); False for counts (→ SUM)."""
    return (
        col.endswith("_rate")
        or col.startswith(_RATIO_PREFIXES)
        or col in _RATIO_EXACT
    )


# Backwards-compatible combined matcher — the Azure SQL source imports this to
# decide when to defer to the LLM. It keeps the original "any aggregation"
# semantics (rank/compare + statistical + count phrasing) and is deliberately
# NOT used by the SQLite fast-path, which relies on the finer-grained split
# above so month/team consolidation stays deterministic.
_AGG_KEYWORDS = re.compile(
    r"(?i)\b(avg|average|mean|median|total|sum|count|how\s+many|number\s+of|"
    r"top\s*\d*|bottom\s*\d*|best|worst|highest|lowest|maximum|minimum|max|min|"
    r"rank|ranking|most|least|compare|comparison|versus|\bvs\b|benchmark|"
    r"trend|trending|over\s+time|more\s+than|less\s+than|greater|fewer|between|"
    r"above|below|exceed|under|over\s+\d|group\s+by|per\s+\w+|each\b)\b"
)

# Numeric KPI columns that have a week-over-week ``_delta`` sibling column.
_DELTA_COLUMNS = {
    "escalations",
    "new_line_pitches",
    "upgrade_attempts",
    "save_attempts",
    "customer_experience",
}


def _load_benchmark_columns(db_path: str = DB_PATH) -> set[str]:
    """Return base columns that have a stored ``_benchmark`` sibling.

    These are the comparison metrics (``cmp_*``) for which the store already
    holds a per-row team-average benchmark, so a "team average" question can be
    answered directly from the sibling column instead of the individual value.
    """
    cols: set[str] = set()
    if not Path(db_path).exists():
        return cols
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            for table in _QUERYABLE_TABLES:
                names = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
                for n in names:
                    if n.endswith("_benchmark") and n[: -len("_benchmark")] in names:
                        cols.add(n[: -len("_benchmark")])
        finally:
            conn.close()
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("_load_benchmark_columns → failed (%s)", exc)
    return cols


# Base columns whose team average is stored in a ``_benchmark`` sibling.
_BENCHMARK_COLUMNS = _load_benchmark_columns()


# ───────────────────────────────────────────────────────────────────────────
# Program-aware behavior remap (WCC vs telesales rubric)
#
# The behavior-score rubric differs by program: telesales agents are scored on
# ``scores.bs_*`` columns, while WCC agents are scored on ``wcc_metrics.wbs_*``
# columns. A generic term like "empathy" resolves to the telesales column by
# default, so for a WCC agent it would read ``scores.bs_empathy`` (typically
# 0/NA) instead of the real ``wcc_metrics.wbs_build_connection_empathize``
# value. When a WCC agent is explicitly named we remap these overlapping
# behaviors to their WCC equivalents.
# ───────────────────────────────────────────────────────────────────────────
_BS_TO_WBS: dict[str, str] = {
    "bs_active_listening": "wbs_build_connection_active_listening",
    "bs_empathy": "wbs_build_connection_empathize",
}

_wcc_agent_cache: dict[str, bool] = {}


def _agent_is_wcc(employee_id: str | int) -> bool:
    """True when *employee_id* is scored on the WCC rubric (has wcc_metrics rows).

    Telesales agents have no ``wcc_metrics`` record, so presence there is a
    reliable per-agent discriminator. The result is cached; lookup failures
    default to ``False`` so the telesales rubric remains the safe fallback.
    """
    eid = str(employee_id)
    cached = _wcc_agent_cache.get(eid)
    if cached is not None:
        return cached
    is_wcc = False
    try:
        conn = get_db_connection()
        with _DB_LOCK:
            row = conn.execute(
                "SELECT 1 FROM wcc_metrics WHERE employee_id = ? LIMIT 1", (eid,)
            ).fetchone()
        is_wcc = row is not None
    except sqlite3.Error as exc:  # pragma: no cover - defensive
        log.warning("_agent_is_wcc \u2192 lookup failed for %s (%s)", eid, exc)
    _wcc_agent_cache[eid] = is_wcc
    return is_wcc


# PSO program names (from PSO_PROGRAMS env, loaded from application/.env). Used
# as a secondary discriminator when an agent has no pso_metrics row yet but the
# program_name identifies them as PSO.
def _load_pso_programs() -> frozenset[str]:
    raw = os.getenv("PSO_PROGRAMS", "") or ""
    return frozenset(
        p.strip().lower() for p in raw.split(",") if p.strip()
    )


_PSO_PROGRAMS = _load_pso_programs()

_pso_agent_cache: dict[str, bool] = {}


def _agent_is_pso(employee_id: str | int) -> bool:
    """True when *employee_id* is scored on the PSO (customer-care) rubric.

    An agent is PSO when they have a ``pso_metrics`` record, or their stored
    ``program_name`` matches a name in ``PSO_PROGRAMS``. The result is cached;
    lookup failures default to ``False`` so the telesales rubric stays the safe
    fallback.
    """
    eid = str(employee_id)
    cached = _pso_agent_cache.get(eid)
    if cached is not None:
        return cached
    is_pso = False
    try:
        conn = get_db_connection()
        with _DB_LOCK:
            row = conn.execute(
                "SELECT 1 FROM pso_metrics WHERE employee_id = ? LIMIT 1", (eid,)
            ).fetchone()
            is_pso = row is not None
            if not is_pso and _PSO_PROGRAMS:
                prow = conn.execute(
                    "SELECT program_name FROM kpis WHERE employee_id = ? LIMIT 1",
                    (eid,),
                ).fetchone()
                if prow and prow[0] and str(prow[0]).strip().lower() in _PSO_PROGRAMS:
                    is_pso = True
    except sqlite3.Error as exc:  # pragma: no cover - defensive
        log.warning("_agent_is_pso \u2192 lookup failed for %s (%s)", eid, exc)
    _pso_agent_cache[eid] = is_pso
    return is_pso


def _remap_metrics_for_wcc(resolved: list[dict], agent: dict | None) -> list[dict]:
    """Swap overlapping telesales behavior columns to their WCC equivalents.

    Only fires when a specific WCC agent is named; telesales agents and
    team-wide queries are left untouched. Behaviors with no WCC counterpart are
    kept as-is (legitimately NA for the other rubric).
    """
    if not agent or not agent.get("employee_id"):
        return resolved
    if not _agent_is_wcc(agent["employee_id"]):
        return resolved
    changed = False
    out: list[dict] = []
    for m in resolved:
        col = m.get("column")
        if m.get("table") == "scores" and col in _BS_TO_WBS:
            out.append({**m, "table": "wcc_metrics", "column": _BS_TO_WBS[col]})
            changed = True
        else:
            out.append(m)
    if changed:
        log.info(
            "_remap_metrics_for_wcc \u2192 remapped behavior(s) to WCC columns for agent %s",
            agent.get("employee_id"),
        )
    return out


# Ambiguous "customer satisfaction / csat" concept: telesales & WCC agents are
# scored on kpis.customer_experience, but PSO (customer-care) agents have their
# own predicted-CSAT model in pso_metrics. Resolve program-aware so "csat" for a
# PSO agent points at the right column instead of a 0/NA telesales field.
_PSO_CONCEPT_REMAP: dict[tuple[str, str], tuple[str, str]] = {
    ("kpis", "customer_experience"): ("pso_metrics", "pkpi_predicted_csat"),
}


def _remap_metrics_for_program(resolved: list[dict], agent: dict | None) -> list[dict]:
    """Apply program-aware column remaps for a named agent.

    Runs the WCC behavior remap, then (for PSO agents) swaps ambiguous
    cross-program concepts like CSAT to their pso_metrics equivalents. Telesales
    agents and team-wide queries are left untouched.
    """
    resolved = _remap_metrics_for_wcc(resolved, agent)
    if not agent or not agent.get("employee_id"):
        return resolved
    if not _agent_is_pso(agent["employee_id"]):
        return resolved
    changed = False
    out: list[dict] = []
    for m in resolved:
        key = (m.get("table"), m.get("column"))
        if key in _PSO_CONCEPT_REMAP:
            t, c = _PSO_CONCEPT_REMAP[key]
            out.append({**m, "table": t, "column": c})
            changed = True
        else:
            out.append(m)
    if changed:
        log.info(
            "_remap_metrics_for_program \u2192 remapped CSAT-family to PSO columns "
            "for agent %s",
            agent.get("employee_id"),
        )
    return out


def _build_deterministic_sql(
    metric: dict,
    period: str | None,
    agent: dict | None,
) -> str:
    """Build a simple single-metric SELECT without invoking the LLM.

    Only called for unambiguous lookups (one concrete metric, no aggregation),
    so the query shape is fixed and safe.
    """
    table = metric["table"]
    col = metric["column"]

    select_cols = ["employee_name", col]
    if col in _DELTA_COLUMNS:
        select_cols.append(f"{col}_delta")

    if period:
        # Explicit period asked → filter to that exact period.
        where = [f"period = '{period}'"]
    else:
        # No period asked → each agent's OWN latest period (correlated per
        # employee), so every agent contributes their most-recent record.
        where = [
            f"period = (SELECT MAX(period) FROM {table} t2 "
            f"WHERE t2.employee_id = {table}.employee_id)"
        ]
    if agent:
        if agent.get("employee_id"):
            where.append(f"employee_id = {agent['employee_id']}")
        elif agent.get("last_name"):
            # Names are stored "Surname, Firstname M" — match the fragment
            # anywhere so either the surname or the given name resolves.
            where.append(f"employee_name LIKE '%{agent['last_name']}%'")

    # DISTINCT so a single latest record per agent is returned even when the
    # source has duplicate rows for the resolved period.
    sql = (
        f"SELECT DISTINCT {', '.join(select_cols)} FROM {table} "
        f"WHERE {' AND '.join(where)} "
        f"ORDER BY employee_name LIMIT 20"
    )
    log.info("_build_deterministic_sql → %s", sql)
    return sql


def _agent_where(agent: dict | None, table: str) -> str | None:
    """Return an agent filter fragment for *table*, or ``None`` for team-wide."""
    if not agent:
        return None
    if agent.get("employee_id"):
        return f"employee_id = {agent['employee_id']}"
    if agent.get("last_name"):
        return f"employee_name LIKE '%{agent['last_name']}%'"
    return None


def _build_breakdown_sql(
    table: str,
    cols: list[str],
    *,
    ids: list[str] | None = None,
    agent: dict | None = None,
    periods: list[str] | None = None,
) -> str:
    """Build a per-row breakdown SELECT (no LLM) for multi-* questions.

    Returns one row per agent (and per period when several are named), listing
    every requested column. Used for:

    * multiple named agents, one metric  → per-agent rows;
    * one agent, multiple metrics         → all columns on one row;
    * multiple agents × multiple periods  → per-agent, per-period rows;
    * "all behaviour scores"              → every ``bs_*`` column.

    Roster-scope injection still applies at execution, so ``ids`` only narrows
    to the named subset within the caller's authorised team.
    """
    multi_period = bool(periods) and len(periods) >= 2
    select = ["employee_name"]
    if multi_period:
        select.append("period")
    select.extend(cols)

    where: list[str] = []
    if periods:
        if len(periods) == 1:
            where.append(f"period = '{periods[0]}'")
        else:
            where.append("period IN (" + ", ".join(f"'{p}'" for p in periods) + ")")
    else:
        # No period named → each agent's own latest record (correlated).
        where.append(
            f"period = (SELECT MAX(period) FROM {table} t2 "
            f"WHERE t2.employee_id = {table}.employee_id)"
        )

    if ids:
        where.append("employee_id IN (" + ", ".join(str(i) for i in ids) + ")")
    else:
        agent_clause = _agent_where(agent, table)
        if agent_clause:
            where.append(agent_clause)

    order = "employee_name" + (", period" if multi_period else "")
    sql = (
        f"SELECT DISTINCT {', '.join(select)} FROM {table} "
        f"WHERE {' AND '.join(where)} ORDER BY {order} LIMIT 50"
    )
    log.info("_build_breakdown_sql → %s", sql)
    return sql


def _build_consolidated_sql(
    metric: dict,
    agent: dict | None,
) -> str:
    """Build a month-consolidated single-metric query (no period filter).

    When no reporting week is given we consolidate across *all* periods in the
    store (the month): counts are ``SUM``-ed, rates/scores are ``AVG``-ed, and
    ``NULL`` weeks are ignored by SQLite's aggregates. A named agent yields one
    row per agent (``GROUP BY``); a team query yields a single aggregate row.
    """
    table = metric["table"]
    col = metric["column"]
    agg = "AVG" if _is_ratio_col(col) else "SUM"
    agg_expr = f"ROUND({agg}({col}), 4) AS {col}"

    where = _agent_where(agent, table)
    if agent:
        sql = (
            f"SELECT employee_name, {agg_expr} FROM {table} "
            f"{('WHERE ' + where + ' ') if where else ''}"
            f"GROUP BY employee_name ORDER BY employee_name LIMIT 20"
        )
    else:
        # Team-wide single figure; scope injection adds the roster filter.
        sql = f"SELECT {agg_expr} FROM {table} LIMIT 1"
    log.info("_build_consolidated_sql → %s", sql)
    return sql


def _build_benchmark_sql(metric: dict, period: str | None) -> str:
    """Return the team-average benchmark for a comparison metric.

    The ``comparison`` table stores a per-row ``cmp_*_benchmark`` sibling holding
    the team average, so we read that column (not the individual value). A named
    agent is irrelevant — the benchmark is team-level — so no agent filter is
    applied; roster scope injection still narrows it to the caller's team.
    """
    table = metric["table"]
    bench = f"{metric['column']}_benchmark"
    if period:
        where = f"period = '{period}'"
    else:
        where = f"period = (SELECT MAX(period) FROM {table})"
    sql = (
        f"SELECT ROUND(AVG({bench}), 4) AS {bench} FROM {table} "
        f"WHERE {where} LIMIT 1"
    )
    log.info("_build_benchmark_sql → %s", sql)
    return sql


def _build_coaching_sql(agent: dict | None, period: str | None = None) -> str:
    """Return coaching tips for *agent* (or the whole team).

    Answers qualitative "where is X lacking / what to improve / recommended
    trainings / what's missing to drive sales" questions deterministically from
    the ``coaching`` table. When *period* is given the advice is pinned to that
    week; otherwise each employee's most-recent period is used so the advice
    reflects the current month.
    """
    where = _agent_where(agent, "coaching")
    if agent:
        pf = (
            f"period = '{period}'" if period else
            "period = (SELECT MAX(period) FROM coaching t2 "
            "WHERE t2.employee_id = coaching.employee_id)"
        )
        clause = f"WHERE {where} AND {pf}" if where else f"WHERE {pf}"
        return (
            "SELECT tip_rank, priority, tip_text, expected_impact, period "
            f"FROM coaching {clause} ORDER BY tip_rank LIMIT 10"
        )
    # Team-wide — pin to the named period when given, else the global latest
    # period (no employee_id reference so roster scope injection still applies).
    team_pf = f"period = '{period}'" if period else "period = (SELECT MAX(period) FROM coaching)"
    return (
        "SELECT employee_name, tip_rank, priority, tip_text FROM coaching "
        f"WHERE {team_pf} "
        "ORDER BY employee_name, tip_rank LIMIT 20"
    )


def _build_improvements_sql(agent: dict | None, period: str | None = None) -> str:
    """Return key improvement areas for *agent* (or the team).

    Reads the ``improvements`` table (JSON ``key_improvements``). When *period*
    is given the areas are pinned to that week; otherwise each employee's
    most-recent period is used. Kept separate from coaching so improvement
    areas are addressed explicitly.
    """
    where = _agent_where(agent, "improvements")
    if agent:
        pf = (
            f"period = '{period}'" if period else
            "period = (SELECT MAX(period) FROM improvements t2 "
            "WHERE t2.employee_id = improvements.employee_id)"
        )
        clause = f"WHERE {where} AND {pf}" if where else f"WHERE {pf}"
        return (
            "SELECT area_rank, area_text, period "
            f"FROM improvements {clause} ORDER BY area_rank LIMIT 10"
        )
    team_pf = f"period = '{period}'" if period else "period = (SELECT MAX(period) FROM improvements)"
    return (
        "SELECT employee_name, area_rank, area_text FROM improvements "
        f"WHERE {team_pf} "
        "ORDER BY employee_name, area_rank LIMIT 20"
    )


def _build_advice_combined_sql(agent: dict) -> str:
    """Return BOTH improvement areas and coaching recommendations for *agent*.

    Used when a question asks for both at once. A single UNION-ALL tags each row
    with its ``category`` so the response addresses them under separate
    headings. Requires a named agent (the per-agent filter also guards the
    second SELECT against roster-scope injection touching only the first
    branch).
    """
    wi = _agent_where(agent, "improvements")
    wc = _agent_where(agent, "coaching")
    latest_i = (
        "period = (SELECT MAX(period) FROM improvements t2 "
        "WHERE t2.employee_id = improvements.employee_id)"
    )
    latest_c = (
        "period = (SELECT MAX(period) FROM coaching t2 "
        "WHERE t2.employee_id = coaching.employee_id)"
    )
    return (
        "SELECT 'Improvement Area' AS category, area_rank AS item_rank, "
        "area_text AS detail, NULL AS priority "
        f"FROM improvements WHERE {wi} AND {latest_i} "
        "UNION ALL "
        "SELECT 'Coaching Recommendation' AS category, tip_rank AS item_rank, "
        "tip_text AS detail, priority "
        f"FROM coaching WHERE {wc} AND {latest_c} "
        "ORDER BY category, item_rank LIMIT 20"
    )


def _build_trend_down_sql(agent: dict | None) -> str:
    """Return KPIs trending downwards over the latest 5-week window.

    ``trends`` stores a right-aligned rolling window where w1 is the current
    week and w4 the oldest (w0 is a not-yet-populated future slot, usually
    NULL). "Newest" is the first non-null of w0…w4 and "oldest" the first
    non-null of w4…w0; a metric is falling when newest < oldest.
    """
    newest = "COALESCE(w0, w1, w2, w3, w4)"
    oldest = "COALESCE(w4, w3, w2, w1, w0)"
    change = f"ROUND({newest} - {oldest}, 4)"
    latest = (
        "period = (SELECT MAX(period) FROM trends t2 "
        "WHERE t2.employee_id = trends.employee_id)"
    )
    where = _agent_where(agent, "trends")
    agent_clause = f"{where} AND " if where else ""
    if agent:
        return (
            f"SELECT metric_key, ROUND({oldest}, 4) AS prior, "
            f"ROUND({newest}, 4) AS current, {change} AS change FROM trends "
            f"WHERE {agent_clause}{latest} AND {newest} < {oldest} "
            "ORDER BY change ASC LIMIT 10"
        )
    return (
        f"SELECT employee_name, metric_key, {change} AS change FROM trends "
        "WHERE period = (SELECT MAX(period) FROM trends) "
        f"AND {newest} < {oldest} ORDER BY change ASC LIMIT 20"
    )


# ───────────────────────────────────────────────────────────────────────────
# Consolidated agent profile — a single snapshot combining headline KPIs,
# program-appropriate behaviours, key improvement areas and coaching
# recommendations. Because this spans several tables (including the multi-row
# coaching / improvements tables), it is assembled from a few small queries and
# returned as one dict row rather than a single SELECT.
# ───────────────────────────────────────────────────────────────────────────

# Headline KPIs surfaced for every agent regardless of program.
_PROFILE_KPI_COLS = [
    "total_call_count", "overall_score", "overall_behavior_score",
    "customer_experience", "escalations", "new_line_pitches",
    "upgrade_attempts", "save_attempts", "mobile_protection_attempts",
    "fwa_attempts", "np_total", "np_converted", "np_conversion_rate",
]
# Telesales-only sales-pitch KPIs — these do NOT exist in the WCC rubric (WCC
# is judged on resolution / save / sale rates via wkpi_*), so they are pruned
# from a WCC agent's profile to avoid surfacing irrelevant "0.0 missed
# opportunities" noise. new-prospect (np_*) KPIs are shared and kept for both.
_PROFILE_TS_ONLY_KPI_COLS = [
    "new_line_pitches", "upgrade_attempts", "save_attempts",
    "mobile_protection_attempts", "fwa_attempts",
]
# WCC-rubric behaviour rollups + the most-coached sub-behaviours.
_PROFILE_WCC_COLS = [
    "wkpi_resolution_actual_rate", "wkpi_save_actual_rate",
    "wkpi_sale_actual_rate", "wbs_build_connection", "wbs_gather_information",
    "wbs_set_up_for_success", "wbs_build_connection_empathize",
    "wbs_build_connection_active_listening", "wbs_build_connection_ownership",
]
# Telesales-rubric behaviour scores.
_PROFILE_BS_COLS = [
    "bs_active_listening", "bs_empathy", "bs_confidence", "bs_clarity",
    "bs_needs_discovery", "bs_solution_guidance", "bs_objection_handling",
    "bs_value_positioning", "bs_professional_tone", "bs_call_control",
]
# WCC (BBV Voice) behaviour skills live in wcc_metrics.wbs_* — used instead of
# the telesales bs_* set when an "all behaviour scores" ask targets a WCC agent.
_PROFILE_WBS_COLS = [
    "wbs_greeting_connection", "wbs_build_connection",
    "wbs_gather_information", "wbs_set_up_for_success",
]
# PSO customer-care KPIs surfaced for a PSO agent's profile (headline set,
# aligned with the application's PSO coaching-priority groups).
_PROFILE_PSO_COLS = [
    "pkpi_predicted_csat", "pkpi_customer_confidence", "pkpi_customer_effort",
    "pkpi_fcr_likelihood", "pkpi_resolution_completeness",
    "pkpi_resolution_confidence", "pkpi_next_steps_clarity",
    "pkpi_issue_resolution_effectiveness", "pkpi_quality", "pkpi_compliance",
    "pkpi_process_adherence", "pkpi_issue_ownership",
    "pkpi_escalation_handling", "pkpi_transfer_avoidance",
    "pkpi_aht_efficiency", "pkpi_repeat_contact_risk",
    "pkpi_escalation_risk", "pkpi_callback_risk", "pkpi_reopen_risk",
]
# PSO risk KPIs where a LOWER score is better (mirrors pso.json inverse_kpis).
_PSO_RISK_COLS = frozenset({
    "pkpi_repeat_contact_risk", "pkpi_escalation_risk",
    "pkpi_callback_risk", "pkpi_reopen_risk",
})

# Comparison-radar metric pairs (value vs stored team benchmark). All are
# "higher is better" EXCEPT escalations, where fewer is better — the direction
# is what lets the analysis call a value a strength or a gap.
_CMP_RADAR_PAIRS = [
    ("Resolution rate", "cmp_resolution_rate"),
    ("Save rate", "cmp_save_rate"),
    ("Sale rate", "cmp_sale_rate"),
    ("Escalations", "cmp_escalations"),
    ("Customer experience", "cmp_customer_experience"),
    ("New line pitches", "cmp_new_line_pitches"),
    ("Upgrade attempts", "cmp_upgrade_attempts"),
    ("Save attempts", "cmp_save_attempts"),
]
_CMP_LOWER_IS_BETTER = {"cmp_escalations"}

# Risk thresholds — mirror the application's settings so the chatbot flags risk
# the same way the dashboards do.
_ESCALATION_THRESHOLD_HIGH = 2      # escalations at/above this are a risk
_TREND_DECLINE_THRESHOLD = -3.0     # count-metric drop that flags a decline
_TREND_RATE_DECLINE_THRESHOLD = -0.05  # rate-metric (0–1) drop that flags one


def is_profile_query(question: str) -> bool:
    """True when the question asks for a consolidated agent profile / summary."""
    return bool(_PROFILE.search(question or ""))


def should_build_profile(question: str) -> bool:
    """True when a consolidated profile is the best answer for *question*.

    Requires profile wording AND the absence of a more specific ask (a single
    concrete metric, advice / coaching / improvement, actions or trend), so a
    targeted question like "tell me about empathy for X" is NOT hijacked into a
    full profile.
    """
    if not is_profile_query(question):
        return False
    if any(m.get("column") for m in _resolve_metrics(question)):
        return False
    if (
        _ACTIONS.search(question) or _ADVICE.search(question)
        or _COACHING.search(question) or _IMPROVEMENT.search(question)
        or _TREND_DOWN.search(question) or _TREND_UP.search(question)
    ):
        return False
    return True


def agent_analysis_focus(question: str) -> str | None:
    """Return the consolidated-analysis focus for *question*, or ``None``.

    - ``"risk"``     → operational-risk / actions-to-be-taken wording; the
      narration leads with risks and the actions to take.
    - ``"overview"`` → profile / summary wording; a balanced rundown.

    The graph routes both to :func:`build_agent_profile` when a specific agent is
    named (so the analysis is program-aware and covers KPIs, comparison radar,
    trends, escalations, improvements and coaching). Returns ``None`` when the
    question is neither, or when a targeted metric/advice ask should win.
    """
    if _ACTIONS.search(question):
        return "risk"
    if _DEVELOPMENT.search(question):
        return "development"
    if _TRAINING.search(question):
        return "training"
    if should_build_profile(question):
        return "overview"
    return None


def _profile_agent_filter(
    agent: dict | None, employee_ids: list | None
) -> tuple[str | None, list]:
    """Build a WHERE fragment pinning the query to *agent*, within scope.

    Returns ``(None, [])`` when the agent cannot be identified or is outside the
    caller's authorized ``employee_ids`` scope.
    """
    scope = [int(e) for e in (employee_ids or [])]
    if agent and agent.get("employee_id"):
        eid = int(agent["employee_id"])
        if scope and eid not in scope:
            return None, []
        return "employee_id = ?", [eid]
    if agent and agent.get("last_name"):
        name = f"%{agent['last_name']}%"
        if scope:
            placeholders = ",".join("?" * len(scope))
            return f"employee_name LIKE ? AND employee_id IN ({placeholders})", [name, *scope]
        return "employee_name LIKE ?", [name]
    return None, []


def build_agent_profile(
    agent: dict | None,
    period: str | None,
    employee_ids: list | None = None,
    role: str = "coach",
    focus: str = "overview",
) -> list[dict]:
    """Assemble a consolidated, program-aware analysis for a single agent.

    Mirrors how the application analyses telesales vs WCC: it always looks at
    the program-appropriate KPIs and behaviours, the comparison radar (value vs
    team benchmark), week-over-week trends, escalations, key improvement areas
    and coaching recommendations — then attaches deterministic ``risk_flags`` so
    the narration can define the analysis and the actions to take.

    ``focus`` is ``"risk"`` for operational-risk / actions questions (the
    narration leads with risks and actions) or ``"overview"`` for a profile.
    Returns ``[analysis_dict]`` or ``[]`` when the agent is unknown / out of
    scope / has no data for the effective period.
    """
    filt, params = _profile_agent_filter(agent, employee_ids)
    if filt is None:
        return []
    try:
        conn = get_db_connection()
        with _DB_LOCK:
            eff = period
            if not eff:
                row = conn.execute(
                    f"SELECT MAX(period) FROM kpis WHERE {filt}", params
                ).fetchone()
                eff = row[0] if row else None
            if not eff:
                return []

            kpi_cols = ["employee_id", "employee_name", "program_name", "period", *_PROFILE_KPI_COLS]
            krow = conn.execute(
                f"SELECT {', '.join(kpi_cols)} FROM kpis "
                f"WHERE {filt} AND period = ? LIMIT 1",
                [*params, eff],
            ).fetchone()
            if krow is None:
                return []
            profile = {k: krow[k] for k in krow.keys()}
            eid = profile.pop("employee_id")

            # Program-appropriate behaviours. Determine the rubric inline (with
            # the connection we already hold) — calling _agent_is_wcc() /
            # _agent_is_pso() here would re-enter the non-reentrant _DB_LOCK and
            # deadlock. PSO takes priority (pso_metrics row OR PSO program name),
            # then WCC, else telesales as the default fallback.
            is_pso = conn.execute(
                "SELECT 1 FROM pso_metrics WHERE employee_id = ? LIMIT 1", (eid,)
            ).fetchone() is not None
            if not is_pso and _PSO_PROGRAMS:
                _pn = (profile.get("program_name") or "").strip().lower()
                if _pn in _PSO_PROGRAMS:
                    is_pso = True
            is_wcc = (not is_pso) and conn.execute(
                "SELECT 1 FROM wcc_metrics WHERE employee_id = ? LIMIT 1", (eid,)
            ).fetchone() is not None
            profile["program"] = "PSO" if is_pso else ("WCC" if is_wcc else "Telesales")
            profile["analysis_focus"] = focus if focus in ("risk", "development", "training") else "overview"
            if is_pso:
                # PSO agents are customer-care, not sales — drop the telesales
                # sales-pitch KPIs so the analysis stays program-appropriate.
                for col in _PROFILE_TS_ONLY_KPI_COLS:
                    profile.pop(col, None)
                prow = conn.execute(
                    f"SELECT {', '.join(_PROFILE_PSO_COLS)} FROM pso_metrics "
                    "WHERE employee_id = ? AND period = ? LIMIT 1",
                    (eid, eff),
                ).fetchone()
                if prow is not None:
                    profile.update({k: prow[k] for k in prow.keys()})
                # PSO behaviours reuse the telesales bs_* behaviour rubric
                # (pso.json scores its skills from "behavior_scores").
                brow = conn.execute(
                    f"SELECT {', '.join(_PROFILE_BS_COLS)} FROM scores "
                    "WHERE employee_id = ? AND period = ? LIMIT 1",
                    (eid, eff),
                ).fetchone()
            elif is_wcc:
                # WCC agents are not measured on telesales sales-pitch KPIs;
                # drop them so the analysis stays program-appropriate.
                for col in _PROFILE_TS_ONLY_KPI_COLS:
                    profile.pop(col, None)
                brow = conn.execute(
                    f"SELECT {', '.join(_PROFILE_WCC_COLS)} FROM wcc_metrics "
                    "WHERE employee_id = ? AND period = ? LIMIT 1",
                    (eid, eff),
                ).fetchone()
            else:
                brow = conn.execute(
                    f"SELECT {', '.join(_PROFILE_BS_COLS)} FROM scores "
                    "WHERE employee_id = ? AND period = ? LIMIT 1",
                    (eid, eff),
                ).fetchone()
            if brow is not None:
                profile.update({k: brow[k] for k in brow.keys()})

            # Comparison radar (value vs team benchmark) and week-over-week
            # trends — both feed the risk analysis below.
            comparison = _profile_comparison(conn, eid, eff)
            profile["comparison_vs_benchmark"] = comparison
            trends = _profile_trend_analysis(conn, eid, eff)
            profile["trends"] = trends

            risk_flags = _profile_risk_flags(profile, comparison, trends)
            profile["risk_flags"] = risk_flags
            # Data-driven "what to do" derived from the agent's own weak KPIs /
            # risks for this program+period — this is the actions-to-take set,
            # NOT the pre-written coaching table.
            profile["recommended_actions"] = _profile_recommended_actions(
                profile, comparison, trends
            )

            # The coaching table (and improvement areas) are only surfaced when
            # the user explicitly asked for coaching / a profile — never for a
            # risk / actions-to-take question, where they would masquerade as
            # the "actions" and leak into unrelated answers.
            if focus != "risk":
                profile["key_improvements"] = _profile_list(
                    conn, "improvements", "area_text", eid, eff, order="area_rank"
                )
                tips = _profile_rows(
                    conn, "coaching", ["priority", "tip_text"], eid, eff, order="tip_rank"
                )
                profile["coaching_recommendations"] = [
                    {"priority": t["priority"], "recommendation": t["tip_text"]} for t in tips
                ]

        return [profile]
    except sqlite3.Error as exc:  # pragma: no cover - defensive
        log.warning("build_agent_profile → failed for %s (%s)", agent, exc)
        return []


def _profile_comparison(conn, eid, period) -> list[dict]:
    """Return the comparison-radar metrics (value vs benchmark, direction-aware).

    Each entry marks whether the agent is a ``strength`` (better than benchmark),
    a ``gap`` (worse) or ``on par`` — honouring that fewer escalations is better.
    """
    row = conn.execute(
        "SELECT * FROM comparison WHERE employee_id = ? AND period = ? LIMIT 1",
        (eid, period),
    ).fetchone()
    if row is None:
        row = conn.execute(
            "SELECT * FROM comparison WHERE employee_id = ? AND period = "
            "(SELECT MAX(period) FROM comparison WHERE employee_id = ?) LIMIT 1",
            (eid, eid),
        ).fetchone()
    if row is None:
        return []
    keys = set(row.keys())
    out = []
    for label, col in _CMP_RADAR_PAIRS:
        bench_col = f"{col}_benchmark"
        if col not in keys or bench_col not in keys:
            continue
        value, bench = row[col], row[bench_col]
        if value is None or bench is None:
            continue
        lower_better = col in _CMP_LOWER_IS_BETTER
        if value == bench:
            assessment = "on par"
        elif (value < bench) if lower_better else (value > bench):
            assessment = "strength"
        else:
            assessment = "gap"
        out.append({
            "metric": label,
            "value": round(value, 4),
            "benchmark": round(bench, 4),
            "direction": "lower_is_better" if lower_better else "higher_is_better",
            "assessment": assessment,
        })
    return out


def _profile_trend_analysis(conn, eid, period) -> dict:
    """Return declining and improving KPIs over the agent's latest 5-week window.

    ``trends`` is right-aligned (w1 current … w4 oldest; w0 usually NULL). A
    metric is declining when the newest value is below the oldest.
    """
    rows = conn.execute(
        "SELECT metric_key, w0, w1, w2, w3, w4 FROM trends "
        "WHERE employee_id = ? AND period = ?",
        (eid, period),
    ).fetchall()
    if not rows:
        rows = conn.execute(
            "SELECT metric_key, w0, w1, w2, w3, w4 FROM trends WHERE employee_id = ? "
            "AND period = (SELECT MAX(period) FROM trends WHERE employee_id = ?)",
            (eid, eid),
        ).fetchall()
    declining, improving = [], []
    for r in rows:
        window = [r["w0"], r["w1"], r["w2"], r["w3"], r["w4"]]
        newest = next((v for v in window if v is not None), None)
        oldest = next((v for v in reversed(window) if v is not None), None)
        if newest is None or oldest is None or newest == oldest:
            continue
        entry = {
            "metric": r["metric_key"],
            "prior": round(oldest, 4),
            "current": round(newest, 4),
            "change": round(newest - oldest, 4),
        }
        (declining if newest < oldest else improving).append(entry)
    declining.sort(key=lambda e: e["change"])
    improving.sort(key=lambda e: e["change"], reverse=True)
    return {"declining": declining, "improving": improving}


def _profile_risk_flags(profile: dict, comparison: list[dict], trends: dict) -> list[str]:
    """Derive deterministic, program-aware risk flags for the narration.

    Combines escalations (fewer is better), below-benchmark comparison metrics
    and declining trends into plain-language flags the response can act on.
    """
    flags = []
    esc = profile.get("escalations")
    if isinstance(esc, (int, float)) and esc >= _ESCALATION_THRESHOLD_HIGH:
        flags.append(f"High escalations: {esc} (risk threshold {_ESCALATION_THRESHOLD_HIGH}).")
    for c in comparison:
        if c["assessment"] == "gap":
            flags.append(
                f"Below benchmark on {c['metric']}: {c['value']} vs {c['benchmark']}."
            )
    for d in trends.get("declining", []):
        metric, change = d["metric"], d["change"]
        is_rate = abs(d["prior"]) <= 1 and abs(d["current"]) <= 1
        threshold = _TREND_RATE_DECLINE_THRESHOLD if is_rate else _TREND_DECLINE_THRESHOLD
        if change <= threshold:
            flags.append(
                f"Declining {metric}: {d['prior']} → {d['current']} (Δ {change})."
            )
    return flags


def _profile_recommended_actions(
    profile: dict, comparison: list[dict], trends: dict
) -> list[str]:
    """Derive data-driven actions from the agent's own weak KPIs / risks.

    These are the "actions to take" — grounded in this agent's escalations,
    below-benchmark comparison metrics and declining trends for the requested
    program and period. Deliberately NOT the pre-written coaching table, so an
    actions/risk question never just echoes generic coaching tips.
    """
    actions = []
    esc = profile.get("escalations")
    if isinstance(esc, (int, float)) and esc >= _ESCALATION_THRESHOLD_HIGH:
        actions.append(
            f"Reduce escalations (currently {esc}): review escalated calls and "
            "reinforce de-escalation, ownership and early needs discovery."
        )
    for c in comparison:
        if c["assessment"] == "gap":
            actions.append(
                f"Lift {c['metric']} toward benchmark ({c['value']} vs "
                f"{c['benchmark']}): make it the focus of the next coaching cycle."
            )
    for d in trends.get("declining", []):
        actions.append(
            f"Reverse the decline in {d['metric']} ({d['prior']} → {d['current']}): "
            "identify what changed week-over-week and correct it."
        )
    if not actions:
        actions.append(
            "No underperforming KPIs for this program and period — maintain "
            "current performance and reinforce the agent's strengths."
        )
    return actions


def _profile_list(conn, table, col, eid, period, *, order):
    """Return a list of *col* values for *eid*, preferring *period* then latest."""
    rows = _profile_rows(conn, table, [col], eid, period, order=order)
    return [r[col] for r in rows]


def _profile_rows(conn, table, cols, eid, period, *, order):
    """Return up to 10 rows of *cols* for *eid* at *period* (or the latest week)."""
    select = ", ".join(cols)
    rows = conn.execute(
        f"SELECT {select} FROM {table} WHERE employee_id = ? AND period = ? "
        f"ORDER BY {order} LIMIT 10",
        (eid, period),
    ).fetchall()
    if not rows:
        rows = conn.execute(
            f"SELECT {select} FROM {table} WHERE employee_id = ? AND period = "
            f"(SELECT MAX(period) FROM {table} WHERE employee_id = ?) "
            f"ORDER BY {order} LIMIT 10",
            (eid, eid),
        ).fetchall()
    return rows


def _try_fast_path(
    question: str,
    resolved: list[dict],
    period: str | None,
    agent: dict | None,
) -> str | None:
    """Return deterministic SQL when the question maps to a known shape;
    otherwise ``None`` (caller falls back to the LLM).

    Resolution order:
      * downward-trend wording                         → trends table
      * coaching / improvement / training wording      → coaching table
      * ranking / comparison / grouping wording        → LLM
      * more than one concrete metric                  → LLM
      * agent + explicit week                          → exact-week SELECT
      * agent + no week                                → month consolidation
      * no agent + stat/count wording                  → team consolidation
      * no agent, plain lookup                         → LLM (listing)
    """
    # Downward-trend questions map to the trends table (skip when the user is
    # explicitly asking about upward movement).
    if _TREND_DOWN.search(question) and not _TREND_UP.search(question):
        return _build_trend_down_sql(agent)

    # Operational-risk / actions-to-be-taken questions map to actionable items:
    # improvement areas + coaching recommendations. For a named agent return the
    # combined result; otherwise fall back to the team coaching list.
    if _ACTIONS.search(question):
        if agent and _agent_where(agent, "improvements"):
            return _build_advice_combined_sql(agent)
        return _build_coaching_sql(agent, period)

    # Qualitative coaching / improvement questions. Improvement AREAS (JSON
    # key_improvements) and coaching RECOMMENDATIONS (JSON coaching_tips) are
    # distinct concepts stored in separate tables — address them explicitly.
    # Check improvement wording BEFORE the broad _ADVICE catch-all (which also
    # matches "improve"). When a question asks for BOTH and names an agent,
    # return a combined result tagged by category.
    imp = bool(_IMPROVEMENT.search(question))
    coa = bool(_COACHING.search(question))
    if imp and coa and agent and _agent_where(agent, "improvements"):
        return _build_advice_combined_sql(agent)
    if imp:
        return _build_improvements_sql(agent, period)
    if coa or _ADVICE.search(question):
        return _build_coaching_sql(agent, period)

    # ── Multi-agent / nested (multi-metric) / multi-period breakdowns ──
    # These produce a per-row breakdown deterministically. Gated so ordinary
    # single-agent, single-metric, team-aggregate and ranking questions are
    # never affected: ranking/superlative wording is excluded, and the branches
    # only fire for a genuine multi-agent subset, a genuine multi-metric list,
    # or an explicit "all behaviour scores" request.
    concrete0 = [m for m in resolved if m.get("column")]
    distinct_cols = list(dict.fromkeys(m["column"] for m in concrete0))
    named_ids = resolve_employee_ids(question)
    periods = _resolve_all_periods(question)
    period_filter = periods or ([period] if period else None)
    if not _SUPERLATIVE.search(question):
        # "All behaviour scores" for a named agent — every behaviour sub-score,
        # program-aware (WCC agents use wbs_*, everyone else the telesales bs_*).
        if _ALL_BEHAVIOURS.search(question) and (agent or named_ids):
            _bid = (agent or {}).get("employee_id") or (named_ids[0] if named_ids else None)
            if _bid and _agent_is_wcc(_bid):
                return _build_breakdown_sql(
                    "wcc_metrics", list(_PROFILE_WBS_COLS),
                    ids=(named_ids or None), agent=agent, periods=period_filter,
                )
            return _build_breakdown_sql(
                "scores", list(_PROFILE_BS_COLS),
                ids=(named_ids or None), agent=agent, periods=period_filter,
            )
        # Nested / multi-metric for a named agent, all columns in one table.
        if len(distinct_cols) >= 2 and (agent or named_ids):
            if len({m["table"] for m in concrete0}) == 1:
                return _build_breakdown_sql(
                    concrete0[0]["table"], distinct_cols,
                    ids=(named_ids or None), agent=agent, periods=period_filter,
                )
            return None  # metrics span tables → let the LLM compose the join
        # Multiple named agents, one metric (optionally across several periods).
        if len(named_ids) >= 2 and len(distinct_cols) == 1:
            return _build_breakdown_sql(
                concrete0[0]["table"], distinct_cols,
                ids=named_ids, agent=None, periods=period_filter,
            )
        # One named agent, one metric, across several explicit periods — a
        # per-period breakdown (deterministic) instead of a misleading single
        # consolidated value or a flaky LLM answer.
        if len(distinct_cols) == 1 and len(periods) >= 2 and (agent or named_ids):
            return _build_breakdown_sql(
                concrete0[0]["table"], distinct_cols,
                ids=(named_ids or None), agent=agent, periods=periods,
            )

    # Team-average / benchmark questions return the *team* figure, not the
    # named agent's value. For comparison metrics we read the stored
    # ``cmp_*_benchmark`` sibling; for any other single metric we fall back to
    # the roster-wide aggregate (a named agent is only context here).
    if _TEAM_BENCHMARK.search(question):
        concrete_tb = [m for m in resolved if m.get("column")]
        if len(concrete_tb) == 1:
            metric_tb = concrete_tb[0]
            col_tb = metric_tb["column"]
            if col_tb in _BENCHMARK_COLUMNS:
                return _build_benchmark_sql(metric_tb, period)
            if period:
                agg_tb = (
                    "AVG"
                    if _is_ratio_col(col_tb) or _WANTS_AVG.search(question)
                    else "SUM"
                )
                return (
                    f"SELECT ROUND({agg_tb}({col_tb}), 4) AS {col_tb} "
                    f"FROM {metric_tb['table']} WHERE period = '{period}' LIMIT 1"
                )
            return _build_consolidated_sql(metric_tb, agent=None)

    if _RANK_COMPARE.search(question):
        return None
    concrete = [m for m in resolved if m.get("column")]
    if len(concrete) != 1:
        return None
    metric = concrete[0]

    # Multiple explicit periods (e.g. "from 2026-04-10 to 2026-04-17") — a
    # single consolidated value silently sums/averages across all the agent's
    # weeks and is misleading. Defer to the LLM for a per-period breakdown.
    if _distinct_period_count(question) >= 2:
        return None

    if agent:
        # A specific week → that week's value; otherwise consolidate the month.
        if period:
            return _build_deterministic_sql(metric, period, agent)
        return _build_consolidated_sql(metric, agent)

    # No specific agent. Only handle it deterministically when the user asked
    # for an aggregate (avg/sum/"how many …"); a bare metric with no agent is a
    # listing/ranking the LLM should shape.
    if _STAT_AGG.search(question) or _SOFT_COUNT.search(question):
        if period:
            # Team aggregate for one week. An explicit average/mean/median
            # request forces AVG even for count columns that default to SUM.
            table, col = metric["table"], metric["column"]
            agg = "AVG" if _is_ratio_col(col) or _WANTS_AVG.search(question) else "SUM"
            return (
                f"SELECT ROUND({agg}({col}), 4) AS {col} FROM {table} "
                f"WHERE period = '{period}' LIMIT 1"
            )
        return _build_consolidated_sql(metric, agent=None)
    return None


# System prompt — flat schema, includes query rewriting.  Externalized to
# prompts/sql_system.txt; contains {schema} and {glossary} placeholders.
_SYSTEM_PROMPT = load_prompt("sql_system")


# ───────────────────────────────────────────────────────────────────────────
# Dynamic prompt retrieval — inject ONLY the schema/glossary the query needs.
#
# Sending the full 6-table schema + entire glossary on every call wastes tokens
# (slower TTFT) and dilutes the model's attention.  When the deterministic
# resolver pins the query to a small set of tables/metrics we narrow the prompt
# to just those — fewer tokens, sharper grounding, identical correctness.  When
# resolution is uncertain (0 tables, or a broad >3-table query) we fall back to
# the full schema/glossary so accuracy is never sacrificed for speed.
# ───────────────────────────────────────────────────────────────────────────

# Parse the compact schema into one line per table for selective inclusion.
_TABLE_SCHEMA_LINES: dict[str, str] = {}
for _line in _COMPACT_SCHEMA.split("\n"):
    _m = re.match(r"Table (\w+) ", _line)
    if _m:
        _TABLE_SCHEMA_LINES[_m.group(1)] = _line

# Above this many distinct tables a query is "broad" → use the full schema.
_MAX_FOCUSED_TABLES = 3


def refresh_caches(db_path: str = DB_PATH) -> None:
    """Rebuild the module-level NL→SQL caches from the live database.

    Call this after the DB is recreated, re-ingested, or has records deleted so
    that schema-grounded SQL generation and bare-surname matching immediately
    reflect the new data (no process restart required).
    """
    global _COMPACT_SCHEMA, _KNOWN_SURNAMES, _TABLE_SCHEMA_LINES, _TRENDS_KEY_NOTE
    global _PERIODS, _PERIOD_NOTE, _BENCHMARK_COLUMNS
    _COMPACT_SCHEMA = _build_schema_from_db(db_path)
    _TRENDS_KEY_NOTE = _build_trends_key_note(db_path)
    _PERIODS = _load_periods(db_path)
    _PERIOD_NOTE = _build_period_note(db_path)
    _BENCHMARK_COLUMNS = _load_benchmark_columns(db_path)
    _TABLE_SCHEMA_LINES = {}
    for line in _COMPACT_SCHEMA.split("\n"):
        m = re.match(r"Table (\w+) ", line)
        if m:
            _TABLE_SCHEMA_LINES[m.group(1)] = line
    _KNOWN_SURNAMES = _load_known_surnames(db_path)
    _wcc_agent_cache.clear()
    _pso_agent_cache.clear()
    log.info("refresh_caches → schema (%d tables) + %d surnames reloaded from %s",
             len(_TABLE_SCHEMA_LINES), len(_KNOWN_SURNAMES), db_path)


def build_focused_schema(tables: set[str]) -> str:
    """Return the schema text for just *tables* (plus the shared note).

    Falls back to the full :data:`_COMPACT_SCHEMA` when *tables* is empty or
    none are recognised, so generation never loses grounding.
    """
    lines = [
        _TABLE_SCHEMA_LINES[t]
        for t in _QUERYABLE_TABLES
        if t in tables and t in _TABLE_SCHEMA_LINES
    ]
    if not lines:
        return _COMPACT_SCHEMA
    # Append the trends metric_key enumeration only when trends is in scope.
    extra = _TRENDS_KEY_NOTE if "trends" in tables else ""
    return "\n".join(lines) + _SCHEMA_NOTE + _PERIOD_NOTE + extra


def build_focused_glossary(resolved: list[dict]) -> str:
    """Glossary lines for only the columns the query actually references.

    Falls back to the full glossary when no concrete column resolved.
    """
    lines: list[str] = []
    seen: set[str] = set()
    for m in resolved:
        col = m.get("column")
        if not col or col in seen:
            continue
        seen.add(col)
        meaning = meaning_for(col)
        if meaning:
            lines.append(f"  - {col}: {meaning}")
    return "\n".join(lines) if lines else glossary_text()


def _select_prompt_context(resolved: list[dict], *, dynamic: bool) -> tuple[str, str]:
    """Choose the schema + glossary for the prompt (focused vs full).

    Returns ``(schema, glossary)``.  Focused only when *dynamic* is on AND the
    query pins to 1.._MAX_FOCUSED_TABLES tables; otherwise the full context.
    """
    if not dynamic:
        return _COMPACT_SCHEMA, glossary_text()
    tables = {m["table"] for m in resolved if m.get("table")}
    if 1 <= len(tables) <= _MAX_FOCUSED_TABLES:
        schema = build_focused_schema(tables)
        glossary = build_focused_glossary(resolved)
        log.info("generate_sql → dynamic prompt (tables=%s, schema_chars=%d)",
                 sorted(tables), len(schema))
        return schema, glossary
    log.info("generate_sql → full prompt (resolved_tables=%d)", len(tables))
    return _COMPACT_SCHEMA, glossary_text()


async def generate_sql(
    question: str,
    chat_history: list[dict] | None = None,
    period: str | None = _UNSET,  # type: ignore[assignment]
    agent: dict | None = _UNSET,  # type: ignore[assignment]
    *,
    dynamic_prompt: bool = True,
    explicit: bool = False,
    raw_question: str | None = None,
) -> str:
    """Generate SQL from natural-language question (single LLM call).

    Combines query understanding + rewriting + SQL generation.
    Optionally uses chat_history for context on follow-up questions.

    ``period`` / ``agent`` may be supplied by the caller to carry context
    forward across follow-up turns.  When left as the ``_UNSET`` sentinel
    they are resolved internally from *question*.

    ``dynamic_prompt`` enables query-driven schema/glossary retrieval (only the
    relevant tables/metrics are sent to the LLM); set ``False`` to always send
    the full schema.

    ``explicit`` is ``True`` when the caller pinned this source via an
    ``@sqlite`` directive — the prompt then states the local SQLite source is
    the directed target; otherwise the data dictionary drives table choice.
    """
    log.info("generate_sql → START question='%s' explicit=%s", question[:120], explicit)

    # Resolve metrics deterministically — drives both the hints and the
    # dynamic schema/glossary selection below.
    resolved = _resolve_metrics(question)

    # Program-aware remap BEFORE schema selection so an ambiguous concept like
    # "csat" points at the right table for the named agent (PSO → pso_metrics
    # predicted csat) and the focused schema follows. The caller passes the
    # resolved agent as ``agent``; when unset, resolve it from the question.
    _ctx_agent = _resolve_agent(question) if agent is _UNSET else agent
    resolved = _remap_metrics_for_program(resolved, _ctx_agent)  # type: ignore[arg-type]

    # The context-injected rewrite can silently drop metrics from a multi-metric
    # ask (e.g. "new line pitches, upgrade attempts and save attempts" → only the
    # first survives). Union in metrics resolved from the ORIGINAL user query so
    # the deterministic multi-metric fast-path stays intact.
    if raw_question and raw_question != question:
        _extra = _remap_metrics_for_program(
            _resolve_metrics(raw_question), _ctx_agent,  # type: ignore[arg-type]
        )
        _seen = {m.get("column") for m in resolved if m.get("column")}
        for _m in _extra:
            if _m.get("column") and _m["column"] not in _seen:
                resolved.append(_m)
                _seen.add(_m["column"])

    # Dynamic prompt retrieval: narrow the schema + glossary to what the query
    # needs (falls back to full context when the query is broad/unresolved).
    schema, glossary = _select_prompt_context(resolved, dynamic=dynamic_prompt)

    # Point the model at the source/tables identified for this question.
    tables = sorted({m["table"] for m in resolved if m.get("table")})
    table_hint = ", ".join(tables) if tables else "the table whose columns best match the question"
    if explicit:
        target_block = (
            "This question is explicitly directed to the local SQLite source "
            f"(@sqlite) — use ONLY the tables in the schema above ({table_hint})."
        )
    else:
        target_block = (
            f"Most relevant table(s) from the data dictionary: {table_hint}. "
            "Use the schema/glossary above to pick the exact columns."
        )
    system_prompt = _SYSTEM_PROMPT.format(
        schema=schema, glossary=glossary, target=target_block
    )

    hint_parts: list[str] = []
    if resolved:
        hint_parts.append("Hints:")
        for m in resolved:
            if m["column"]:
                meaning = meaning_for(m["column"])
                desc = f" — {meaning}" if meaning else ""
                hint_parts.append(f"  - '{m['phrase']}' → column {m['column']} in table {m['table']}{desc}")
            else:
                hint_parts.append(f"  - '{m['phrase']}' → use table {m['table']}")
        hint_parts.append("Use these exact column/table names.")

    # Resolve a specific period (week/date) if the user named one.
    # Caller may override to carry context forward across follow-ups.
    if period is _UNSET:
        period = _resolve_period(question)
    if period:
        hint_parts.append(f"  - Filter this period: WHERE period = '{period}'")

    # Resolve a specific agent if the user named one (or carried forward).
    if agent is _UNSET:
        agent = _resolve_agent(question)
    if agent:
        if agent.get("employee_id"):
            hint_parts.append(
                f"  - Filter this specific agent: WHERE employee_id = {agent['employee_id']}"
            )
        elif agent.get("last_name"):
            hint_parts.append(
                "  - Filter this specific agent (name stored 'Surname, Firstname'): "
                f"WHERE employee_name LIKE '%{agent['last_name']}%'"
            )

    # Program-aware behavior remap already applied above (against the resolved
    # or caller-provided agent). When the agent was only discovered here (period
    # carry-forward paths), re-apply so late-resolved agents still get remapped.
    if agent is not _UNSET and agent is not _ctx_agent:
        resolved = _remap_metrics_for_program(resolved, agent)  # type: ignore[arg-type]

    # ── Deterministic fast-path (no LLM) for simple single-metric lookups ──
    fast_sql = _try_fast_path(question, resolved, period, agent)  # type: ignore[arg-type]
    if fast_sql is not None:
        log.info("generate_sql → FAST-PATH (no LLM) sql_len=%d", len(fast_sql))
        return fast_sql

    hint = ("\n" + "\n".join(hint_parts)) if hint_parts else ""

    # Build user message with optional chat history context (last 5, sorted)
    user_content = question + hint
    if chat_history:
        recent = chat_history[-5:]  # last 5 messages, already chronological
        history_context = "\n\nRecent conversation (oldest→newest, for follow-up context):\n"
        for msg in recent:
            speaker = "User" if msg["role"] == "user" else "Assistant"
            history_context += f"{speaker}: {msg['content'][:200]}\n"
        user_content = (
            history_context
            + "\nResolve any pronouns/references using the context above.\n"
            + "Current question: " + question + hint
        )

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]

    raw = await generate_completion(messages, max_tokens=2048)
    log.debug("generate_sql → raw LLM output (%d chars): %s", len(raw), raw[:300])

    # Retry once on empty
    if not raw.strip():
        log.warning("generate_sql → empty SQL, retrying")
        raw = await generate_completion(messages, max_tokens=2048)

    # Clean up response
    sql = raw.strip()
    if sql.startswith("```"):
        first_nl = sql.find("\n")
        sql = sql[first_nl + 1:] if first_nl != -1 else sql[3:]
    if sql.endswith("```"):
        sql = sql[:-3]
    sql = sql.replace(";", "").strip()

    log.info("generate_sql → DONE sql_len=%d\n%s", len(sql), sql)
    return sql


# ═══════════════════════════════════════════════════════════════════════════
# 2. VALIDATION — SELECT-only safety across the 6 flat tables
# ═══════════════════════════════════════════════════════════════════════════

# Only these tables are allowed (shared by validation + execution). Derived
# from _QUERYABLE_TABLES so the validation allowlist can never drift from the
# schema the LLM is actually shown (otherwise the model generates SQL against a
# table it's then forbidden to use — e.g. pso_metrics).
ALLOWED_TABLES = frozenset(_QUERYABLE_TABLES)

# Dangerous patterns
_FORBIDDEN_PATTERNS = [
    re.compile(r"\b(DELETE|UPDATE|INSERT|DROP|ALTER|CREATE|TRUNCATE|REPLACE)\b", re.IGNORECASE),
    re.compile(r"\b(ATTACH|DETACH)\b", re.IGNORECASE),
    re.compile(r"--"),       # SQL comments
    re.compile(r"/\*"),      # Block comments
]


class SQLValidationError(Exception):
    pass


def validate_sql(sql: str) -> str:
    """Validate SQL safety. Returns cleaned SQL or raises SQLValidationError."""
    log.info("validate_sql → START sql_len=%d", len(sql))

    if not sql or not sql.strip():
        log.error("validate_sql → REJECTED: empty query")
        raise SQLValidationError("Empty SQL query")

    sql = sql.strip()

    # Must be a SELECT
    if not sql.upper().startswith("SELECT"):
        log.error("validate_sql → REJECTED: not a SELECT")
        raise SQLValidationError("Only SELECT queries are allowed")

    # Check forbidden patterns
    for pattern in _FORBIDDEN_PATTERNS:
        if pattern.search(sql):
            log.error("validate_sql → REJECTED: forbidden pattern '%s'", pattern.pattern)
            raise SQLValidationError(f"Forbidden SQL pattern: {pattern.pattern}")

    # Extract table names and verify they're allowed
    tables_found = _extract_tables(sql)
    disallowed = tables_found - ALLOWED_TABLES
    if disallowed:
        log.error("validate_sql → REJECTED: disallowed tables %s", disallowed)
        raise SQLValidationError(f"Disallowed table(s): {', '.join(sorted(disallowed))}")

    log.info("validate_sql → PASSED (tables: %s)", ", ".join(sorted(tables_found)))
    return sql


def _extract_tables(sql: str) -> set[str]:
    """Extract table names from SQL using simple regex."""
    tables = set()

    # FROM clause
    for m in re.finditer(r"\bFROM\s+(\w+)", sql, re.IGNORECASE):
        tables.add(m.group(1).lower())

    # JOIN clause
    for m in re.finditer(r"\bJOIN\s+(\w+)", sql, re.IGNORECASE):
        tables.add(m.group(1).lower())

    # Subqueries: FROM (table) in subquery
    for m in re.finditer(r"\(\s*SELECT\b.*?\bFROM\s+(\w+)", sql, re.IGNORECASE):
        tables.add(m.group(1).lower())

    return tables


# ═══════════════════════════════════════════════════════════════════════════
# 3. EXECUTION — run the query with employee_id scope injection
# ═══════════════════════════════════════════════════════════════════════════

# Hard ceiling on rows returned: we never fetch large result sets (e.g. 500).
# The cap scales with team size but stays within [MIN_ROW_CAP, MAX_ROW_CAP].
MIN_ROW_CAP: int = 5
MAX_ROW_CAP: int = 20


def _row_cap(team_size: int) -> int:
    """Row cap based on team size, clamped to ``[MIN_ROW_CAP, MAX_ROW_CAP]``."""
    return max(MIN_ROW_CAP, min(MAX_ROW_CAP, team_size or MIN_ROW_CAP))


def _clamp_limit(sql: str, cap: int) -> str:
    """Clamp an existing ``LIMIT n`` down to ``cap`` (never raising it), or add
    one when absent.  Smaller explicit limits (e.g. ``LIMIT 1``) are preserved."""
    m = re.search(r"\bLIMIT\s+(\d+)\b", sql, re.IGNORECASE)
    if m:
        new = min(int(m.group(1)), cap)
        return sql[: m.start()] + f"LIMIT {new}" + sql[m.end():]
    return sql.rstrip().rstrip(";").rstrip() + f" LIMIT {cap}"


def _has_employee_scope(sql: str, emp_col: str = "employee_id") -> bool:
    """Return True when the SQL already contains an employee scope filter."""
    return bool(re.search(
        rf"\b{re.escape(emp_col)}\b\s*(?:=|<>|!=|<=|>=|<|>|IN\b)",
        sql,
        re.IGNORECASE,
    ))


def execute_sql(
    sql: str,
    *,
    employee_ids: list[int],
    role: str = "coach",
    **kwargs,
) -> list[dict]:
    """Execute SQL with employee_id scope injection.

    Injects a WHERE employee_id IN (...) clause to restrict results
    to the caller's authorized employees.
    """
    log.info("execute_sql → START role=%s, employee_ids=%d", role, len(employee_ids))

    if not employee_ids:
        log.warning("execute_sql → no employee_ids, returning empty")
        return []

    # SECURITY: always inject the caller's roster scope. Detecting an "already
    # scoped" query is unsafe — a correlated subquery (e.g.
    # ``s2.employee_id = scores.employee_id``) or an LLM-inlined literal id was
    # previously mistaken for an authorization filter, so scope injection was
    # skipped and the query returned OTHER teams' rows. Injecting
    # unconditionally intersects results with the roster, so any out-of-scope id
    # simply yields no rows (fail-closed).
    scoped_sql, params = _inject_scope(sql, employee_ids)

    if len(employee_ids) > 1:
        cap = _row_cap(len(employee_ids))
        scoped_sql = _clamp_limit(scoped_sql, cap)
        log.info("execute_sql → row cap=%d, scoped SQL:\n%s\nParams: %s", cap, scoped_sql, params)
    else:
        cap = None
        log.info("execute_sql → scoped SQL (single agent, no row cap):\n%s\nParams: %s", scoped_sql, params)

    # Execute against the shared read-only connection (no per-request open).
    try:
        with _DB_LOCK:
            cursor = get_db_connection().execute(scoped_sql, params)
            rows = cursor.fetchall()
            columns = [desc[0] for desc in cursor.description] if cursor.description else []
        result = [dict(zip(columns, row)) for row in rows]
        preview = json.dumps(result[:3], default=str, ensure_ascii=False, indent=2)
        log.info("execute_sql → DONE: %d rows, columns=%s, preview:\n%s",
                 len(result), columns, preview)
        return result
    except sqlite3.Error as e:
        log.error("execute_sql → SQL error: %s", e)
        return []


def _inject_scope(sql: str, employee_ids: list[int]) -> tuple[str, list]:
    """Inject employee_id IN (...) scope filter into the SQL.

    Strategy: Find the main FROM table and add/extend WHERE clause.
    """
    placeholders = ", ".join(["?"] * len(employee_ids))
    scope_condition = f"employee_id IN ({placeholders})"

    # Find if there's already a WHERE clause
    # Use case-insensitive search for WHERE
    where_match = re.search(r"\bWHERE\b", sql, re.IGNORECASE)

    if where_match:
        # Insert scope condition after WHERE
        insert_pos = where_match.end()
        scoped_sql = sql[:insert_pos] + f" {scope_condition} AND" + sql[insert_pos:]
    else:
        # Find position to insert WHERE (before ORDER BY, GROUP BY, LIMIT, or end)
        insert_match = re.search(
            r"\b(ORDER\s+BY|GROUP\s+BY|HAVING|LIMIT)\b", sql, re.IGNORECASE
        )
        if insert_match:
            insert_pos = insert_match.start()
            scoped_sql = sql[:insert_pos] + f"WHERE {scope_condition} " + sql[insert_pos:]
        else:
            scoped_sql = sql + f" WHERE {scope_condition}"

    return scoped_sql, list(employee_ids)
