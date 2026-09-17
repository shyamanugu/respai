"""Pipeline configuration dataclasses and dynamic loader.

Every pipeline step receives a ``PipelineConfig`` instance — no hardcoded
program references leak into step logic.
"""

from __future__ import annotations

import importlib
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Type

from dotenv import load_dotenv, set_key
from pydantic import BaseModel


ROOT_PATH = Path(__file__).parents[2]  # ai_pipeline/
ENV_PATH = ROOT_PATH / ".env"
load_dotenv(dotenv_path=ENV_PATH)


def _env_or_persist(key: str, default: str) -> str:
    """Return the env value for *key*; if unset, persist *default* to the .env.

    Ensures the value is sourced from the environment only, while
    bootstrapping the .env with the default the first time it is missing so
    the variable is present for subsequent runs.
    """
    value = os.environ.get(key)
    if value:
        return value
    os.environ[key] = default
    try:
        set_key(str(ENV_PATH), key, default)
    except Exception:
        pass
    return default


def _sql_env(suffix: str, default: str) -> str:
    """Resolve an Azure SQL setting from env, reusing the app's variables.

    Prefers the pipeline's ``AI_PIPELINE_AZURE_SQL_<suffix>`` variable; when that
    is unset, falls back to the application's ``APP_AZURE_SQL_<suffix>`` so a
    single ``.env`` configures both. Returns *default* when neither is set.
    """
    value = os.environ.get(f"AI_PIPELINE_AZURE_SQL_{suffix}")
    if value:
        return value
    value = os.environ.get(f"APP_AZURE_SQL_{suffix}")
    if value:
        return value
    return default


def _openai_base_url() -> str:
    """Return the OpenAI-compatible ``/openai/v1/`` base URL.

    Prefers ``REASONING_MODEL_BASE_URL`` when set; otherwise derives it from the
    host of ``REASONING_MODEL_ENDPOINT`` (a Foundry project/resource endpoint,
    e.g. ``https://<resource>.services.ai.azure.com/api/projects/<proj>``).
    """
    from urllib.parse import urlparse

    override = os.environ.get("REASONING_MODEL_BASE_URL", "").strip()
    if override:
        return override
    endpoint = os.environ.get("REASONING_MODEL_ENDPOINT", "").strip()
    if not endpoint:
        return ""
    parsed = urlparse(endpoint)
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}/openai/v1/"
    return endpoint.rstrip("/") + "/openai/v1/"


@dataclass
class AzureOpenAIConfig:
    api_version: str = "2024-12-01-preview"
    chat_deployment: str = field(default_factory=lambda: os.environ.get("REASONING_MODEL_DEPLOYMENT", "gpt-5-nano"))
    reasoning_deployment: str = field(default_factory=lambda: os.environ.get("REASONING_MODEL_DEPLOYMENT", "gpt-5-nano"))
    chat_api_key: str = field(default_factory=lambda: os.environ.get("REASONING_MODEL_APIKEY", ""))
    reasoning_api_key: str = field(default_factory=lambda: os.environ.get("REASONING_MODEL_APIKEY", ""))
    # ``*_endpoint`` holds the OpenAI-compatible ``/openai/v1/`` base URL derived
    # from REASONING_MODEL_ENDPOINT (a Foundry project/resource endpoint), used
    # with the plain ``AsyncOpenAI`` client.
    chat_endpoint: str = field(default_factory=lambda: _openai_base_url())
    reasoning_endpoint: str = field(default_factory=lambda: _openai_base_url())
    denoise_temperature: float = 1.0
    analyze_temperature: float = 1.0
    # Starting output-token budget. 0 means "don't cap" (use the model default)
    # on the first attempt; the query layer only sets/raises an explicit budget
    # when a response is truncated by the length limit.
    max_completion_tokens: int = field(
        default_factory=lambda: int(os.environ.get("REASONING_MODEL_MAX_TOKENS", "0"))
    )


@dataclass
class StorageConfig:
    account_name: str = field(default_factory=lambda: os.environ.get("SALES_STORAGE_ACCOUNT_NAME", ""))
    account_key: str = field(default_factory=lambda: os.environ.get("SALES_STORAGE_ACCOUNT_KEY", ""))
    raw_container: str = field(default_factory=lambda: os.environ.get("SALES_RAW_CONTAINER", ""))
    denoised_container: str = field(default_factory=lambda: os.environ.get("SALES_DENOISED_CONTAINER", ""))
    analysis_container: str = field(default_factory=lambda: os.environ.get("SALES_ANALYSIS_CONTAINER", ""))
    summary_container: str = field(default_factory=lambda: os.environ.get("SALES_SUMMARY_CONTAINER", ""))
    coach_hierarchy_container: str = field(default_factory=lambda: _env_or_persist("SALES_COACH_HIERARCHY_CONTAINER", "coach-employee-hierarchy"))


@dataclass
class AzureSQLConfig:
    """Azure SQL connection settings for the individual-metrics step.

    Authentication mirrors the dashboard application: DefaultAzureCredential
    acquires an AAD access token that is passed to pyodbc. Configure via the
    ``AI_PIPELINE_AZURE_SQL_*`` environment variables; when those are unset the
    application's ``APP_AZURE_SQL_*`` variables are reused so a single ``.env``
    works for both the app and the pipeline.
    """

    server: str = field(default_factory=lambda: _sql_env("SERVER", ""))
    database: str = field(default_factory=lambda: _sql_env("DATABASE", ""))
    port: str = field(default_factory=lambda: _sql_env("PORT", "1433"))
    driver: str = field(default_factory=lambda: _sql_env("DRIVER", "{ODBC Driver 18 for SQL Server}"))
    login_timeout: int = field(default_factory=lambda: int(_sql_env("LOGIN_TIMEOUT", "90")))
    query_timeout: int = field(default_factory=lambda: int(_sql_env("QUERY_TIMEOUT", "0")))


# ── Default individual-metric queries & groups (ported from the dashboard app) ──
# Each group is (group_name, query_name, [metric_desc, ...]). The query template
# accepts {employee_ids}, {metric_keys}, {start_date}, {end_date} placeholders and
# must SELECT (EmployeeID, MetricDesc, AvgValue, SumValue) in that column order.

DEFAULT_INDIVIDUAL_METRIC_QUERIES: Dict[str, str] = {
    "rep_pivoted": (
        "SELECT EmployeeID, MetricDesc, "
        "AVG(CAST(Result_Num AS FLOAT)) AS AvgValue, "
        "SUM(CAST(Result_Num AS FLOAT)) AS SumValue "
        "FROM vzw.rep_pivoted "
        "WHERE EmployeeID IN ({employee_ids}) "
        "AND Timeframe BETWEEN '{start_date}' AND '{end_date}' "
        "AND MetricDesc IS NOT NULL AND MetricDesc != '' "
        "AND MetricDesc IN ({metric_keys}) "
        "GROUP BY EmployeeID, MetricDesc"
    ),
}

DEFAULT_INDIVIDUAL_METRIC_GROUPS: List[tuple] = [
    (
        "Resolve",
        "rep_pivoted",
        [
            "2-Hour Resolve",
            "3-Day Resolve",
            "30-Day Resolve",
            "3 Day Contact Disconnect %",
            "30 Day Contact Disconnect %",
            "90 Day Contact Disconnect %",
        ],
    ),
    (
        "Efficiency",
        "rep_pivoted",
        [
            "Agent AHT",
            "Agent Outbound AHT",
            "Avg Response Time",
            "Agent Calls",
            "AFRRT (Avg First Rep Response Time)",
        ],
    ),
    (
        "Quality",
        "rep_pivoted",
        [
            "VXS Overall Rep",
            "Thumbs Up %",
            "Thumbs Down %",
        ],
    ),
]


@dataclass
class ThrottleConfig:
    requests_per_minute: int = 800
    max_concurrent: int = 400
    request_timeout: int = 300


@dataclass
class KPIDefinition:
    key: str
    label: str
    column: Optional[str] = None  # polars column to .sum()
    filter_column: Optional[str] = None  # column that must be True before summing
    aggregate: str = "sum"  # sum | count_true | custom


@dataclass
class ExpandColumnRule:
    """How to rename sub-fields when expanding a struct column."""
    column: str  # struct column name (e.g. "escalation")
    rename_subfields: List[str] = field(default_factory=list)  # sub-fields to prefix (e.g. ["segment_ids", "explanation"])


@dataclass
class SummaryFieldMapping:
    """Column names the summary step reads from expanded analysis data.

    Each program defines which columns hold tags, intent, resolution steps, etc.
    This eliminates all hardcoded column references in summary.py.
    """
    sort_column: str = "call_importance"  # column to sort per-employee calls
    sort_descending: bool = True
    tags_column: str = "tagging"  # column holding call tags (list)
    intent_column: str = "customer_intent"  # primary call reason
    resolution_column: Optional[str] = "issue_resolution_steps"  # steps to resolve


@dataclass
class ReportSectionDef:
    """Defines a filterable report section in the summary JSON output.

    Each section filters employee calls by a column value and builds
    per-row detail items with transcript excerpts.

    Examples:
        - Escalations: filter_column="escalation_due_to_frustration" OR multiple columns
        - Customer experience: filter_column="customer_experience_rating", filter_values=["Good", "Medium", "Poor"]
        - Sales outcome: filter_column="sales_outcome_outcome", filter_values from cfg.sales_outcomes
    """
    key: str  # output JSON key (e.g. "escalations", "customer_experience")
    filter_column: Optional[str] = None  # column to filter on
    filter_values: Optional[List[str]] = None  # values to group by (creates sub-keys)
    or_columns: Optional[List[str]] = None  # OR-based boolean filter (e.g. escalation columns)
    segment_ids_column: Optional[str] = None  # column with segment IDs for excerpt
    summary_columns: List[str] = field(default_factory=list)  # columns to concatenate for summary text
    extra_columns: Dict[str, str] = field(default_factory=dict)  # extra fields to include: output_key -> column_name


@dataclass
class PipelineConfig:
    """Top-level config consumed by every pipeline step."""

    program_id: str  # e.g. "telesales"
    program_filter: Optional[str] = None  # SQL WHERE value for ProgramName
    mode: Optional[str] = None  # e.g. "telesales", "wcc", "telesales|wcc", or None for all
    # Optional CoachID allow-list. When set, steps (denoise) only process rows
    # whose CoachID is in this list. Populated from --coach CLI arg or the
    # AI_PIPELINE_COACH_FILTER env var (comma-separated).
    coach_filter: Optional[List[int]] = field(
        default_factory=lambda: (
            [int(c.strip()) for c in os.environ.get("AI_PIPELINE_COACH_FILTER", "").split(",") if c.strip()]
            or None
        )
    )
    # Optional EmployeeID (agent) allow-list. Useful for raw datasets that have
    # no CoachID column (e.g. pso). Populated from --agent CLI arg or the
    # AI_PIPELINE_AGENT_FILTER env var (comma-separated).
    agent_filter: Optional[List[int]] = field(
        default_factory=lambda: (
            [int(c.strip()) for c in os.environ.get("AI_PIPELINE_AGENT_FILTER", "").split(",") if c.strip()]
            or None
        )
    )

    openai: AzureOpenAIConfig = field(default_factory=AzureOpenAIConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)

    # Per-step throttle overrides
    denoise_throttle: ThrottleConfig = field(default_factory=lambda: ThrottleConfig(
        requests_per_minute=800, max_concurrent=400, request_timeout=300
    ))
    analysis_throttle: ThrottleConfig = field(default_factory=lambda: ThrottleConfig(
        requests_per_minute=950, max_concurrent=400, request_timeout=240
    ))
    summary_throttle: ThrottleConfig = field(default_factory=lambda: ThrottleConfig(
        requests_per_minute=1300, max_concurrent=200, request_timeout=300
    ))

    # ── Program-level schema classes ─────────────────────────────────────
    # Each program provides its own Pydantic model for analysis output.
    # The pipeline passes this to the LLM structured-output call.
    analysis_schema: Optional[Type[BaseModel]] = None
    reflection_schema: Optional[Type[BaseModel]] = None

    # KPI definitions for summary step
    kpi_definitions: List[KPIDefinition] = field(default_factory=list)

    # Custom KPI computation function.
    # Signature: (key, dfe, df_all, cfg) -> numeric
    # Programs provide this to handle KPIs that can't be expressed as simple column sums.
    compute_custom_kpi: Optional[Callable] = None

    # Comparison metrics for team-level benchmarking
    comparison_metrics: List[Dict[str, str]] = field(default_factory=list)

    # Sales outcome categories
    sales_outcomes: List[str] = field(default_factory=list)

    # Struct columns to expand in summary
    expand_columns: List[str] = field(default_factory=list)

    # Escalation filter columns
    escalation_columns: List[str] = field(default_factory=list)

    # Columns to cast to Utf8 after loading analysis data
    utf8_cast_columns: List[str] = field(default_factory=list)

    # ── Summary report configuration ────────────────────────────────────
    # Rules for expanding struct columns from analysis_response
    expand_column_rules: List[ExpandColumnRule] = field(default_factory=list)

    # Field mappings for summary step (column names in expanded data)
    summary_fields: SummaryFieldMapping = field(default_factory=SummaryFieldMapping)

    # Report sections to build in summary JSON output
    report_sections: List[ReportSectionDef] = field(default_factory=list)

    # Prompt templates — set by program config
    denoise_system_prompt: str = ""
    analysis_system_prompt: str = ""
    reflection_system_prompt: str = ""

    # Behavior / soft-skill key mappings for summary aggregation
    # Each entry is a tuple: (label, column_name)
    behavior_score_keys: List[tuple] = field(default_factory=list)
    # Each entry is a tuple: (label, score_column, occurred_column)
    behavior_count_keys: List[tuple] = field(default_factory=list)
    # Each entry is a tuple: (label, column_name)
    soft_skill_keys: List[tuple] = field(default_factory=list)
    # WCC-only behavior keys: (label, column_name)
    wcc_behavior_keys: List[tuple] = field(default_factory=list)
    # Boolean KPIs that also collect per-call evidence (segment IDs + excerpts).
    # Each entry is a tuple: (kpi_key, label, bool_column, segment_ids_column).
    # The summary step emits a ``kpi_evidence`` block (mirrors WCC core KPIs).
    evidence_kpi_keys: List[tuple] = field(default_factory=list)
    # Optional grouping of KPI keys for the summary report.
    # Each entry is a tuple: (group_label, [kpi_key, ...]). When set, the
    # summary step emits a ``kpi_groups`` block that buckets the computed
    # KPIs (with values + deltas) under each group label.
    kpi_groups: List[tuple] = field(default_factory=list)

    # KPI keys whose value is an absolute count (integer) rather than a 0-1
    # fraction. The summary step tags these with unit="count" (others "percent").
    count_kpi_keys: List[str] = field(default_factory=list)
    # Core KPI keys (besides the escalation comparison_metrics) surfaced in the
    # comparison block as individual-vs-team-average.
    comparison_kpi_keys: List[str] = field(default_factory=list)

    # Lookback window for summary (days)
    summary_lookback_days: int = 7
    trend_weeks: int = 4

    # ── Individual-metrics step (Azure SQL driven coaching) ──────────────────
    azure_sql: AzureSQLConfig = field(default_factory=AzureSQLConfig)
    individual_metric_queries: Dict[str, str] = field(
        default_factory=lambda: dict(DEFAULT_INDIVIDUAL_METRIC_QUERIES)
    )
    individual_metric_groups: List[tuple] = field(
        default_factory=lambda: list(DEFAULT_INDIVIDUAL_METRIC_GROUPS)
    )
    # Size (days) of the current metric window; the previous window is the
    # equally sized window immediately preceding it.
    individual_metric_window_days: int = 7

    # Max retries for LLM calls
    max_retries: int = 3
    retry_delay: int = 3

    # Tags file path (relative to ROOT_PATH)
    tags_file: Optional[str] = None

    # Per-mode configs for multi-mode runs (populated by load_mode_config)
    # Maps mode_id → PipelineConfig for that mode
    mode_configs: Dict[str, Any] = field(default_factory=dict)


def load_program_config(program_id: str) -> PipelineConfig:
    """Dynamically load a program config module and return its PipelineConfig."""
    module = importlib.import_module(f"ai_pipeline.programs_config.{program_id}")
    return module.get_config()


def load_mode_config(mode: str) -> PipelineConfig:
    """Load a merged PipelineConfig for one or more modes (``|``-separated).

    When a single mode is given (e.g. ``"telesales"``), this is equivalent to
    ``load_program_config(mode)`` with the ``mode`` field set.

    When multiple modes are given (e.g. ``"telesales|wcc"``), the first mode's
    config is used as the base (openai, storage, throttle) and per-mode configs
    are stored in ``mode_configs`` so steps can resolve per-row.
    """
    modes = [m.strip() for m in mode.split("|") if m.strip()]
    if not modes:
        raise ValueError("At least one mode must be specified")

    configs: Dict[str, PipelineConfig] = {}
    for m in modes:
        configs[m] = load_program_config(m)

    # Use first mode as base config
    base = configs[modes[0]]
    base.mode = mode
    base.program_id = mode  # e.g. "telesales|wcc"

    if len(modes) > 1:
        # Multi-mode: clear single-program filter since we use SQL IN clause
        base.program_filter = None
        # Merge WCC behavior keys if present in any mode
        for m in modes[1:]:
            other = configs[m]
            if other.wcc_behavior_keys and not base.wcc_behavior_keys:
                base.wcc_behavior_keys = other.wcc_behavior_keys
            if other.kpi_groups and not base.kpi_groups:
                base.kpi_groups = other.kpi_groups
            if other.expand_column_rules:
                existing = {r.column for r in base.expand_column_rules}
                for rule in other.expand_column_rules:
                    if rule.column not in existing:
                        base.expand_column_rules.append(rule)
            if other.utf8_cast_columns:
                for col in other.utf8_cast_columns:
                    if col not in base.utf8_cast_columns:
                        base.utf8_cast_columns.append(col)

    base.mode_configs = configs
    return base
