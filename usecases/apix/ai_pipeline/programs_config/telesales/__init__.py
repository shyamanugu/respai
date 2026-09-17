"""Telesales (VZW Telesales) program configuration.

To add a new program, create a new folder under ``programs_config/<name>/``
with the following files:

    __init__.py   — must export ``get_config() -> PipelineConfig``
    schemas.py    — Pydantic evaluation model for per-call LLM output
    prompts.py    — system prompts for analysis and reflection
"""

from __future__ import annotations

import polars as pl

from ai_pipeline.logging_config import get_logger
from ai_pipeline.programs_config.base import (
    AzureOpenAIConfig,
    ExpandColumnRule,
    KPIDefinition,
    PipelineConfig,
    ReportSectionDef,
    StorageConfig,
    SummaryFieldMapping,
    ThrottleConfig,
    DENOISING_SYSTEM_PROMPT,
)
from ai_pipeline.programs_config.telesales.schemas import SalesAgentEvaluation
from ai_pipeline.programs_config.telesales.prompts import (
    SALES_AGENT_SYSTEM_PROMPT,
    REFLECTION_SYSTEM_PROMPT,
)

logger = get_logger("programs_config.telesales")


# ── Constants ────────────────────────────────────────────────────────────────

PROGRAM_ID = "telesales"
PROGRAM_FILTER = [
    "VZW Telesales"
]

SALES_OUTCOMES = [
    "Closed deal",
    "Not closed",
    "In progress but not closed",
    "Not applicable",
]

EXPAND_COLUMNS = ["escalation", "coaching_tip", "customer_experience", "sales_outcome"]
ESCALATION_COLUMNS = ["escalation_due_to_frustration", "escalation_requested_by_customer"]
UTF8_CAST_COLUMNS = ["weve_got_you_statement"]


# ── Behavior / Soft-Skill key mappings for summary aggregation ───────────────

# Score-only behaviors (boolean .mean() across calls → 0-1 fraction)
BEHAVIOR_SCORE_KEYS = [
    ("Active Listening",       "active_listening"),
    ("Acknowledgment",         "acknowledgment"),
    ("Empathy",                "empathy"),
    ("Confidence",             "confidence"),
    ("Clarity",                "clarity"),
    ("Needs Discovery",        "needs_discovery"),
    ("Solution Guidance",      "solution_guidance"),
    ("Next Steps Summary",     "next_steps_summary"),
    ("Call Control",           "call_control"),
    ("Professional Tone",      "professional_tone"),
]

# Optional boolean behaviors (mean for score, sum for count, is_not_null().sum() for total)
BEHAVIOR_COUNT_KEYS = [
    ("Objection Handling",       "objection_handling",       "objection_handling"),
    ("Value Positioning",        "value_positioning",        "value_positioning"),
    ("Assumptive Close",         "assumptive_close",         "assumptive_close"),
    ("Compliance Disclosures",   "compliance_disclosures",   "compliance_disclosures"),
]

SOFT_SKILL_KEYS = [
    ("Comprehension",             "comprehension"),
    ("Language Proficiency",      "language_proficiency"),
    ("Emotional Intelligence",    "emotional_intelligence"),
    ("Relationship Building",     "relationship_building"),
    ("Professional Skills",       "professional_skills"),
    ("Subject Matter Expertise",  "subject_matter_expertise"),
]


# ── KPI definitions ──────────────────────────────────────────────────────────

KPI_DEFINITIONS = [
    KPIDefinition(key="new_line_pitches", label="New line Pitches", column="pitched_new_line", filter_column="new_line_opportunity_exists"),
    KPIDefinition(key="new_line_opportunity_exists", label="New line Opportunity Exists", column="new_line_opportunity_exists"),
    KPIDefinition(key="new_line_opportunity_missed", label="New line Opportunity Missed", aggregate="custom"),
    KPIDefinition(key="upgrade_attempts", label="No. of upgrade attempts", column="pitched_plan_upgrade", filter_column="upgrade_opportunity_exists"),
    KPIDefinition(key="upgrade_opportunity_exists", label="Upgrade Opportunity Exists", column="upgrade_opportunity_exists"),
    KPIDefinition(key="upgrade_opportunity_missed", label="Upgrade Opportunity Missed", aggregate="custom"),
    KPIDefinition(key="save_attempts", label="No. of save attempts", column="save_attempt"),
    KPIDefinition(key="escalations", label="Escalations", aggregate="custom"),
    KPIDefinition(key="fwa_attempts", label="FWA attempts", column="pitched_fwa"),
    KPIDefinition(key="mobile_protection_attempts", label="Mobile Protection attempts", column="pitched_mobile_protection"),
    KPIDefinition(key="we_got_you_utterances", label="\u201cWe\u2019ve got you\u201d utterances", aggregate="custom"),
    KPIDefinition(key="customer_experience", label="Customer Experience", aggregate="custom"),
]

COMPARISON_METRICS = [
    {"metric": "New line Pitches", "column": "pitched_new_line"},
    {"metric": "Upgrade attempts", "column": "pitched_plan_upgrade"},
    {"metric": "Save attempts", "column": "save_attempt"},
    {"metric": "Escalations", "columns": ["escalation_due_to_frustration", "escalation_requested_by_customer"]},
]


# ── Custom KPI computation ───────────────────────────────────────────────────

def compute_custom_kpi(key: str, dfe: pl.DataFrame, df_all: pl.DataFrame, cfg: PipelineConfig):
    """Compute telesales KPIs that can't be expressed as simple column sums."""
    logger.debug("Computing custom KPI: %s (rows=%d)", key, len(dfe))

    if key == "new_line_opportunity_missed":
        has = dfe.filter(pl.col("new_line_opportunity_exists") == True)
        pitched = has.filter(pl.col("pitched_new_line") == True)
        return round(1 - len(pitched) / max(len(has), 1), 2)

    if key == "upgrade_opportunity_missed":
        has = dfe.filter(pl.col("upgrade_opportunity_exists") == True)
        pitched = has.filter(pl.col("pitched_plan_upgrade") == True)
        return round(1 - len(pitched) / max(len(has), 1), 2)

    if key == "escalations":
        mask = pl.lit(False)
        for col in cfg.escalation_columns:
            if col in dfe.columns:
                mask = mask | (pl.col(col) == True)
        return len(dfe.filter(mask))

    if key == "we_got_you_utterances":
        return len(dfe.filter(pl.col("weve_got_you_statement").is_not_null()))

    if key == "customer_experience":
        rated = dfe.filter(pl.col("customer_experience_rating").is_not_null())
        not_poor = len(rated.filter(pl.col("customer_experience_rating") != "Poor"))
        return round(not_poor / max(len(rated), 1), 2)

    return 0


# ── Struct expand rules ──────────────────────────────────────────────────────

EXPAND_COLUMN_RULES = [
    ExpandColumnRule(column="escalation", rename_subfields=["segment_ids", "explanation"]),
    ExpandColumnRule(column="coaching_tip", rename_subfields=["segment_ids", "explanation"]),
    ExpandColumnRule(column="customer_experience", rename_subfields=["segment_ids", "rating", "justification"]),
    ExpandColumnRule(column="sales_outcome", rename_subfields=["segment_ids", "outcome", "explanation"]),
]

SUMMARY_FIELDS = SummaryFieldMapping(
    sort_column="call_importance",
    sort_descending=True,
    tags_column="tagging",
    intent_column="customer_intent",
    resolution_column="issue_resolution_steps",
)

REPORT_SECTIONS = [
    ReportSectionDef(
        key="escalations",
        or_columns=["escalation_due_to_frustration", "escalation_requested_by_customer"],
        segment_ids_column="escalation_segment_ids",
        summary_columns=["customer_intent", "issue_resolution_steps", "escalation_reason"],
        extra_columns={
            "escalation_requested_by_customer": "escalation_requested_by_customer",
            "escalation_due_to_frustration": "escalation_due_to_frustration",
        },
    ),
    ReportSectionDef(
        key="customer_experience",
        filter_column="customer_experience_rating",
        filter_values=["Good", "Medium", "Poor"],
        segment_ids_column="customer_experience_segment_ids",
        summary_columns=["customer_intent", "issue_resolution_steps", "customer_experience_justification"],
    ),
    ReportSectionDef(
        key="sales_outcome",
        filter_column="sales_outcome_outcome",
        segment_ids_column="sales_outcome_segment_ids",
        summary_columns=["customer_intent", "issue_resolution_steps", "sales_outcome_explanation"],
    ),
]


# ── Config factory ───────────────────────────────────────────────────────────

def get_config() -> PipelineConfig:
    """Return the full pipeline config for telesales."""
    logger.debug("Loading telesales pipeline config")
    return PipelineConfig(
        program_id=PROGRAM_ID,
        program_filter=PROGRAM_FILTER,
        openai=AzureOpenAIConfig(),
        storage=StorageConfig(),
        denoise_throttle=ThrottleConfig(requests_per_minute=800, max_concurrent=400, request_timeout=300),
        analysis_throttle=ThrottleConfig(requests_per_minute=950, max_concurrent=400, request_timeout=240),
        summary_throttle=ThrottleConfig(requests_per_minute=1300, max_concurrent=200, request_timeout=300),
        # Schemas
        analysis_schema=SalesAgentEvaluation,
        reflection_schema=None,
        # KPIs & metrics
        kpi_definitions=KPI_DEFINITIONS,
        compute_custom_kpi=compute_custom_kpi,
        comparison_metrics=COMPARISON_METRICS,
        sales_outcomes=SALES_OUTCOMES,
        expand_columns=EXPAND_COLUMNS,
        escalation_columns=ESCALATION_COLUMNS,
        utf8_cast_columns=UTF8_CAST_COLUMNS,
        # Summary report config
        expand_column_rules=EXPAND_COLUMN_RULES,
        summary_fields=SUMMARY_FIELDS,
        report_sections=REPORT_SECTIONS,
        # Prompts
        denoise_system_prompt=DENOISING_SYSTEM_PROMPT,
        analysis_system_prompt=SALES_AGENT_SYSTEM_PROMPT,
        reflection_system_prompt=REFLECTION_SYSTEM_PROMPT,
        # Behavior / soft-skill keys
        behavior_score_keys=BEHAVIOR_SCORE_KEYS,
        behavior_count_keys=BEHAVIOR_COUNT_KEYS,
        soft_skill_keys=SOFT_SKILL_KEYS,
        summary_lookback_days=7,
        trend_weeks=4,
    )
