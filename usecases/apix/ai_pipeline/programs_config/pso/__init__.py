"""PSO (VZ Mobile service) program configuration.

PSO is the Verizon **VZ Mobile service** customer-care program. It uses a
dedicated, standalone evaluation model (``PsoAgentEvaluation``) and its own
analysis + reflection prompts — it does NOT extend telesales or WCC.

PSO is driven by the mobile customer-care context and surfaces:
    * KPI group evaluations (True/False, aggregated to a 0-1 fraction across
      calls, each with supporting segment-ID evidence) — Customer Experience,
      Resolve, Quality, Customer Care Handling, Operational Efficiency and
      Repeat Contact Risk.
    * Behavioural KPIs (0-1 fraction) across Communication, Customer Care,
      Problem Solving, Resolution and Operations.
    * The six call-handling soft skills (shared with telesales/WCC).
    * Escalations, Customer Experience and Sales Outcome report sections.
    * Key improvement areas and coaching recommendations from the reflection step.
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
from ai_pipeline.programs_config.pso.schemas import PsoAgentEvaluation
from ai_pipeline.programs_config.pso.prompts import (
    SALES_AGENT_SYSTEM_PROMPT,  # alias for PSO_AGENT_SYSTEM_PROMPT
    REFLECTION_SYSTEM_PROMPT,
)
# PSO retains the same soft-skill fields as telesales/WCC (comprehension, etc.)
from ai_pipeline.programs_config.telesales import (
    SOFT_SKILL_KEYS,
)
from ai_pipeline.utils import get_programs_for_mode

logger = get_logger("programs_config.pso")


# ── Constants ────────────────────────────────────────────────────────────────

PROGRAM_ID = "pso"
# Supported PSO program names are sourced from the PSO_PROGRAMS env var (see
# .env). The hardcoded list is only a fallback when the env var is unset.
PROGRAM_FILTER = get_programs_for_mode("pso") or [
    "VZ Mobile Service",
]

SALES_OUTCOMES = [
    "Closed deal",
    "Not closed",
    "In progress but not closed",
    "Not applicable",
]

EXPAND_COLUMNS = ["escalation", "coaching_tip", "customer_experience", "sales_outcome"]
ESCALATION_COLUMNS = ["escalation_due_to_frustration", "escalation_requested_by_customer"]
UTF8_CAST_COLUMNS: list = []


# ── Behaviour / Soft-Skill key mappings for summary aggregation ──────────────

# Behavioural KPIs (boolean .mean() across calls → 0-1 fraction) → behavior_scores
BEHAVIOR_SCORE_KEYS = [
    # Communication
    ("Active Listening",        "active_listening"),
    ("Clarity",                 "clarity"),
    ("Professional Tone",       "professional_tone"),
    # Customer Care
    ("Empathy",                 "empathy"),
    ("Acknowledgment",          "acknowledgment"),
    ("Reassurance",             "reassurance"),
    ("Ownership",               "ownership_behavior"),
    # Problem Solving
    ("Issue Investigation",     "issue_investigation"),
    ("Solution Guidance",       "solution_guidance"),
    # Resolution
    ("Resolution Verification", "resolution_verification"),
    ("Next Steps Summary",      "next_steps_summary"),
    # Operations
    ("Call Control",            "call_control"),
    ("Escalation Management",   "escalation_management"),
]

BEHAVIOR_COUNT_KEYS: list = []


# ── KPI group boolean columns (aggregated as 0-1 fraction) ───────────────────

# key → (label, boolean column, segment_ids column). Each boolean is averaged
# across the agent's calls (→ 0-1 fraction) and its segment IDs feed the
# per-call evidence block (mirrors the WCC core-KPI evidence pattern).
BOOL_KPI_COLUMNS = {
    # Customer Experience
    "predicted_csat":              ("Predicted CSAT",              "predicted_csat",              "predicted_csat_segment_ids"),
    "customer_confidence":         ("Customer Confidence",         "customer_confidence",         "customer_confidence_segment_ids"),
    "customer_effort":             ("Customer Effort (Ease)",      "customer_effort",             "customer_effort_segment_ids"),
    # Resolve
    "fcr_likelihood":              ("FCR Likelihood",              "fcr_likelihood",              "fcr_likelihood_segment_ids"),
    "resolution_completeness":     ("Resolution Completeness",     "resolution_completeness",     "resolution_completeness_segment_ids"),
    "resolution_confidence":       ("Resolution Confidence",       "resolution_confidence",       "resolution_confidence_segment_ids"),
    "next_steps_clarity":          ("Next Steps Communication",    "next_steps_communication",    "next_steps_communication_segment_ids"),
    "issue_resolution_effectiveness": ("Issue Resolution Effectiveness", "issue_resolution_effectiveness", "issue_resolution_effectiveness_segment_ids"),
    # Quality
    "quality":                     ("Quality Score",               "quality",                     "quality_segment_ids"),
    "compliance":                  ("Compliance Score",            "compliance",                  "compliance_segment_ids"),
    "process_adherence":           ("Process Adherence",           "process_adherence",           "process_adherence_segment_ids"),
    # Customer Care Handling
    "issue_ownership":             ("Issue Ownership Effectiveness", "issue_ownership",           "issue_ownership_segment_ids"),
    "escalation_handling":         ("Escalation Necessity",        "escalation_necessity",        "escalation_necessity_segment_ids"),
    "transfer_avoidance":          ("Transfer Avoidance",          "transfer_avoidance",          "transfer_avoidance_segment_ids"),
    "case_management":             ("Case Management Effectiveness", "case_management",           "case_management_segment_ids"),
    # Operational Efficiency
    "aht_efficiency":              ("AHT Assessment",              "aht_assessment",              "aht_assessment_segment_ids"),
    "contact_handling_efficiency": ("Contact Handling Efficiency", "contact_handling_efficiency", "contact_handling_efficiency_segment_ids"),
    "hold_management":             ("Hold Management Effectiveness", "hold_management",           "hold_management_segment_ids"),
    # Repeat Contact Risk (True = more risk)
    "repeat_contact_risk":         ("Repeat Contact Risk",         "repeat_contact_risk",         "repeat_contact_risk_segment_ids"),
    "escalation_risk":             ("Escalation Risk",             "escalation_risk",             "escalation_risk_segment_ids"),
    "callback_risk":               ("Callback Risk",               "callback_risk",               "callback_risk_segment_ids"),
    "reopen_risk":                 ("Reopen Risk",                 "reopen_risk",                 "reopen_risk_segment_ids"),
}

# Evidence KPI keys consumed by the summary step: (key, label, bool_col, seg_col)
EVIDENCE_KPI_KEYS = [
    (key, label, bool_col, seg_col)
    for key, (label, bool_col, seg_col) in BOOL_KPI_COLUMNS.items()
]

# KPIs expressed as an absolute count of calls (not a 0-1 fraction). Every other
# KPI is percentage-style (mean of the per-call boolean → 0-1). Repeat-contact
# risks and escalations are naturally counts ("how many calls at risk").
COUNT_KPI_KEYS = [
    "repeat_contact_risk",
    "escalation_risk",
    "callback_risk",
    "reopen_risk",
    "escalations",
]

# Top core KPIs (besides escalations) surfaced in the comparison block as
# individual-vs-team-average — one representative KPI from each core PSO group.
CORE_COMPARISON_KPIS = [
    "predicted_csat",                 # Customer Experience
    "fcr_likelihood",                 # Resolve
    "quality",                        # Quality
    "issue_resolution_effectiveness",  # Resolve / effectiveness
]


# ── KPI definitions ──────────────────────────────────────────────────────────

KPI_DEFINITIONS = [
    KPIDefinition(key=key, label=label, aggregate="custom")
    for key, (label, _col, _seg) in BOOL_KPI_COLUMNS.items()
] + [
    KPIDefinition(key="escalations", label="Escalations", aggregate="custom"),
    KPIDefinition(key="customer_experience", label="Customer Experience", aggregate="custom"),
]

COMPARISON_METRICS = [
    {"metric": "Escalations", "columns": ["escalation_due_to_frustration", "escalation_requested_by_customer"]},
]


# ── KPI groups (surfaced as a grouped block in the summary JSON) ─────────────

KPI_GROUPS = [
    ("Customer Experience", ["predicted_csat", "customer_confidence", "customer_effort"]),
    ("Resolve", [
        "fcr_likelihood",
        "resolution_completeness",
        "resolution_confidence",
        "next_steps_clarity",
        "issue_resolution_effectiveness",
    ]),
    ("Quality", ["quality", "compliance", "process_adherence"]),
    ("Customer Care Handling", [
        "issue_ownership",
        "escalation_handling",
        "transfer_avoidance",
        "case_management",
    ]),
    ("Operational Efficiency", [
        "aht_efficiency",
        "contact_handling_efficiency",
        "hold_management",
    ]),
    ("Repeat Contact Risk", [
        "repeat_contact_risk",
        "escalation_risk",
        "callback_risk",
        "reopen_risk",
    ]),
    ("Customer", ["escalations", "customer_experience"]),
]



# ── Custom KPI computation ───────────────────────────────────────────────────

def compute_custom_kpi(key: str, dfe: pl.DataFrame, df_all: pl.DataFrame, cfg: PipelineConfig):
    """Compute PSO KPIs — 0-1 boolean fractions plus escalations / CX."""
    logger.debug("Computing custom KPI: %s (rows=%d)", key, len(dfe))

    if key in BOOL_KPI_COLUMNS:
        _label, col, _seg = BOOL_KPI_COLUMNS[key]
        if col in dfe.columns:
            # Count-style KPIs → number of calls where the flag fired.
            if key in COUNT_KPI_KEYS:
                return int(dfe[col].cast(pl.Int64).fill_null(0).sum())
            # Percentage-style KPIs → fraction of calls (0-1).
            non_null = dfe[col].drop_nulls()
            return round(float(non_null.mean()), 2) if len(non_null) > 0 else 0.0
        return 0 if key in COUNT_KPI_KEYS else 0.0

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
    """Return the full pipeline config for PSO (VZ Mobile service)."""
    logger.debug("Loading PSO pipeline config")
    return PipelineConfig(
        program_id=PROGRAM_ID,
        program_filter=PROGRAM_FILTER,
        openai=AzureOpenAIConfig(),
        storage=StorageConfig(),
        denoise_throttle=ThrottleConfig(requests_per_minute=800, max_concurrent=400, request_timeout=300),
        analysis_throttle=ThrottleConfig(requests_per_minute=950, max_concurrent=400, request_timeout=240),
        summary_throttle=ThrottleConfig(requests_per_minute=1300, max_concurrent=200, request_timeout=300),
        # Schema (standalone PSO evaluation model)
        analysis_schema=PsoAgentEvaluation,
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
        # Prompts (standalone PSO analysis + reflection prompts)
        denoise_system_prompt=DENOISING_SYSTEM_PROMPT,
        analysis_system_prompt=SALES_AGENT_SYSTEM_PROMPT,
        reflection_system_prompt=REFLECTION_SYSTEM_PROMPT,
        # Behaviour / soft-skill keys
        behavior_score_keys=BEHAVIOR_SCORE_KEYS,
        behavior_count_keys=BEHAVIOR_COUNT_KEYS,
        soft_skill_keys=SOFT_SKILL_KEYS,
        wcc_behavior_keys=[],
        evidence_kpi_keys=EVIDENCE_KPI_KEYS,
        kpi_groups=KPI_GROUPS,
        count_kpi_keys=COUNT_KPI_KEYS,
        comparison_kpi_keys=CORE_COMPARISON_KPIS,
        summary_lookback_days=7,
        trend_weeks=4,
    )
