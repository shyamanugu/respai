"""WCC (Wireless Customer Care) program configuration.

WCC uses a dedicated, standalone evaluation model (``WccAgentEvaluation``)
and its own analysis prompt (``WCC_AGENT_SYSTEM_PROMPT``) — it does NOT extend
the telesales sales model or prompt.

WCC focuses on resolution, customer experience, retention (survival) and
right-of-sell behaviours across the LEARN / PROVIDE / CLOSE phases, plus the
WCC Core KPIs (Resolution, Survival/Retention, Right of Sell).
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
from ai_pipeline.programs_config.wcc.schemas import WccAgentEvaluation
from ai_pipeline.programs_config.wcc.prompts import (
    SALES_AGENT_SYSTEM_PROMPT,  # alias for WCC_AGENT_SYSTEM_PROMPT
    REFLECTION_SYSTEM_PROMPT,
)
# WCC retains the same soft-skill fields as telesales (comprehension, etc.)
from ai_pipeline.programs_config.telesales import (
    SOFT_SKILL_KEYS,
)

# WCC has no telesales-style behavior flags (active_listening, etc.)
BEHAVIOR_SCORE_KEYS: list = []
BEHAVIOR_COUNT_KEYS: list = []

logger = get_logger("programs_config.wcc")


# ── Constants ────────────────────────────────────────────────────────────────

PROGRAM_ID = "wcc"
PROGRAM_FILTER = [
    "VZMobile BGCO Loyalty",
    "VZW BGCO Voice",
    "BBV Voice",
]

SALES_OUTCOMES = [
    "Closed deal",
    "Not closed",
    "In progress but not closed",
    "Not applicable",
]

EXPAND_COLUMNS = ["escalation", "customer_experience", "sales_outcome"]
ESCALATION_COLUMNS = ["escalation_due_to_frustration", "escalation_requested_by_customer"]
UTF8_CAST_COLUMNS: list = []

# WCC-specific boolean KPIs (label, column_name) — 15 fields across LEARN/PROVIDE/CLOSE
WCC_BEHAVIOR_KEYS = [
    # LEARN phase
    ("Greetings Connection",    "wcc_greetings_connection"),
    ("Build Connection",        "wcc_build_connection"),
    ("Gather Information",       "wcc_gather_information"),
    ("Verification",            "wcc_verification"),
    ("Callback",                "wcc_callback"),
    # PROVIDE phase
    ("Address Needs",           "wcc_address_needs"),
    ("Test Resolution",         "wcc_test_resolution"),
    ("Sell Transition",         "wcc_sell_transition"),
    ("Sell Confidence",         "wcc_sell_confidence"),
    ("Overcoming Objections",   "wcc_overcoming_objections"),
    # CLOSE phase
    ("SSO Enablement",          "wcc_sso_enablement"),
    ("Setup Success",           "wcc_setup_success"),
    ("Additional Concerns",     "wcc_additional_concerns"),
    ("NPS Survey",              "wcc_nps_survey"),
    ("Closing Restate",         "wcc_closing_restate"),
]


# ── KPI definitions ──────────────────────────────────────────────────────────

KPI_DEFINITIONS = [
    # Core KPIs — Resolution
    KPIDefinition(key="resolution_opportunities", label="Resolution Opportunities", column="resolution_opportunity_exists"),
    KPIDefinition(key="resolutions", label="Resolutions", column="resolution_actual_exists", filter_column="resolution_opportunity_exists"),
    # Core KPIs — Survival / Retention
    KPIDefinition(key="survival_opportunities", label="Survival Opportunities", column="survival_rate_opportunity_exists"),
    KPIDefinition(key="saves", label="Saves", column="survival_rate_actual_exists", filter_column="survival_rate_opportunity_exists"),
    # Core KPIs — Right of Sell
    KPIDefinition(key="right_of_sell_opportunities", label="Right of Sell Opportunities", column="right_of_sell_opportunity_exists"),
    KPIDefinition(key="right_of_sell_actuals", label="Right of Sell Actuals", column="right_of_sell_actual_exists", filter_column="right_of_sell_opportunity_exists"),
    # Outcome KPIs
    KPIDefinition(key="sales_made", label="Sales Made", column="sale_made"),
    KPIDefinition(key="new_prospects", label="New Prospects", column="new_prospect"),
    KPIDefinition(key="new_prospects_converted", label="New Prospects Converted", column="new_prospect_converted"),
    KPIDefinition(key="escalations", label="Escalations", aggregate="custom"),
    KPIDefinition(key="customer_experience", label="Customer Experience", aggregate="custom"),
]

COMPARISON_METRICS = [
    {"metric": "Resolutions", "column": "resolution_actual_exists"},
    {"metric": "Saves", "column": "survival_rate_actual_exists"},
    {"metric": "Right of Sell Actuals", "column": "right_of_sell_actual_exists"},
    {"metric": "Escalations", "columns": ["escalation_due_to_frustration", "escalation_requested_by_customer"]},
]


# ── Custom KPI computation ───────────────────────────────────────────────────

def compute_custom_kpi(key: str, dfe: pl.DataFrame, df_all: pl.DataFrame, cfg: PipelineConfig):
    """Compute WCC KPIs that can't be expressed as simple column sums."""
    logger.debug("Computing custom KPI: %s (rows=%d)", key, len(dfe))

    if key == "escalations":
        mask = pl.lit(False)
        for col in cfg.escalation_columns:
            if col in dfe.columns:
                mask = mask | (pl.col(col) == True)
        return len(dfe.filter(mask))

    if key == "customer_experience":
        rated = dfe.filter(pl.col("customer_experience_rating").is_not_null())
        not_poor = len(rated.filter(pl.col("customer_experience_rating") != "Poor"))
        return round(not_poor / max(len(rated), 1), 2)

    return 0


# ── Struct expand rules ──────────────────────────────────────────────────────

EXPAND_COLUMN_RULES = [
    ExpandColumnRule(column="escalation", rename_subfields=["segment_ids", "explanation"]),
    ExpandColumnRule(column="customer_experience", rename_subfields=["segment_ids", "rating", "justification"]),
    ExpandColumnRule(column="sales_outcome", rename_subfields=["segment_ids", "outcome", "explanation"]),
]

SUMMARY_FIELDS = SummaryFieldMapping(
    sort_column="resolution_opportunity_exists",
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
    """Return the full pipeline config for WCC."""
    logger.debug("Loading WCC pipeline config")
    return PipelineConfig(
        program_id=PROGRAM_ID,
        program_filter=PROGRAM_FILTER,
        openai=AzureOpenAIConfig(),
        storage=StorageConfig(),
        denoise_throttle=ThrottleConfig(requests_per_minute=800, max_concurrent=400, request_timeout=300),
        analysis_throttle=ThrottleConfig(requests_per_minute=950, max_concurrent=400, request_timeout=240),
        summary_throttle=ThrottleConfig(requests_per_minute=1300, max_concurrent=200, request_timeout=300),
        # Schema (standalone WCC evaluation model)
        analysis_schema=WccAgentEvaluation,
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
        # Prompts (standalone WCC analysis prompt)
        denoise_system_prompt=DENOISING_SYSTEM_PROMPT,
        analysis_system_prompt=SALES_AGENT_SYSTEM_PROMPT,
        reflection_system_prompt=REFLECTION_SYSTEM_PROMPT,
        # Behavior / soft-skill keys (WCC has soft skills + WCC behavioral KPIs only)
        behavior_score_keys=BEHAVIOR_SCORE_KEYS,
        behavior_count_keys=BEHAVIOR_COUNT_KEYS,
        soft_skill_keys=SOFT_SKILL_KEYS,
        wcc_behavior_keys=WCC_BEHAVIOR_KEYS,
        summary_lookback_days=7,
        trend_weeks=4,
    )
