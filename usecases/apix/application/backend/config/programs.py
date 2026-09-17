"""
backend/config/programs.py — Multi-program configuration
==========================================================
Provides data-driven program configs so the dashboard adapts to
any program (different KPI groups, scoring rules, risk thresholds).

Each program has a JSON config file under ``backend/config/programs/``.
The config specifies KPI display groups, scoring components, risk rules,
and manager dashboard layout.  Programs without a config file get a
generic fallback that auto-groups KPIs from the data.

Usage::

    from backend.config.programs import load_program_config, discover_programs

    cfg = load_program_config("telesales")
    programs = discover_programs()
"""

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.config.settings import Settings
from backend.config.logging import get_logger

logger = get_logger(__name__)

_PROGRAMS_DIR = Path(__file__).parent / "programs"

DEFAULT_PROGRAM = "telesales"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class KPISpec:
    """A single KPI reference within a display group."""

    key: str
    label: str
    source: Optional[str] = None  # Optional source dataset (e.g. "wcc_count_kpis")
    source_type: Optional[str] = None  # "dict" if source is a dict of KPI values, else list
    popup: bool = False  # If True, card is clickable and opens a detail popup
    items: Optional[str] = None  # "list" if KPI has multiple items to show in the popup
    items_prop: Optional[Dict[str, str]] = None  # For dict sources, which properties to show in the popup
    category: Optional[str] = None  # "main" or "derived"; only "main" KPIs count in avg score
    prompt: Optional[str] = None  # Optional prompt text associated with this KPI
    pre_scaled: bool = True  # True = value is already 0-100; False = value is 0-1, multiply by 100


@dataclass
class KPIGroup:
    """An ordered group of KPIs to display together (e.g. "Sales KPIs")."""

    name: str
    kpis: List[KPISpec] = field(default_factory=list)


@dataclass
class ScoringComponent:
    """One component of the overall performance score.

    Types:
        quality  — KPIs whose raw value IS a 0-100 score. Averaged, weighted.
        target   — KPIs compared against a target. Normalised to 0-100, averaged, weighted.
        trend    — Universal delta-based score across all KPIs. Weighted.
        penalty  — KPIs that reduce score when above threshold. Tiered penalty.
    """

    type: str  # quality | target | trend | penalty
    weight: float = 0.0
    kpis: List[Any] = field(default_factory=list)          # str or {"key": ..., "source": ...}
    targets: List[Any] = field(default_factory=list)       # [{"key": ..., "target": ..., "source": ...}] or dict
    tiers: List[Dict[str, Any]] = field(default_factory=list)
    source: str = ""                                       # component-level source (target/trend)
    metrics_ref: str = ""                                   # trend: JSON path for metric definitions


@dataclass
class RiskRule:
    """A single risk-identification rule evaluated per KPI.

    Types:
        delta_decline — Fires when delta < threshold (applies to ALL KPIs).
        low_score     — Fires when value < threshold for listed KPIs.
        high_value    — Fires when value > threshold for listed KPIs.
        below_target  — Fires when value < threshold for listed KPIs.
    """

    type: str  # delta_decline | low_score | high_value | below_target
    threshold: float = 0.0
    severity: str = "high"  # high | critical
    kpis: List[str] = field(default_factory=list)
    reason: str = ""


@dataclass
class Coreskills:
    """Core skills config for telesales program."""

    name: str
    kpis: List[KPISpec] = field(default_factory=list)


@dataclass
class BehaviouralSkills:
    """Behavioural skills config."""

    name: str
    kpis: List[KPISpec] = field(default_factory=list)


@dataclass
class WCCKpis:
    """WCC-specific KPIs that count the kpi occurrences."""

    name: str
    kpis: List[KPISpec] = field(default_factory=list)


@dataclass
class MetricSpec:
    """A single KPI on the Individual Performance Metrics page.

    ``column_key`` is the descriptor value matched against the named query's
    output, ``label`` is the display text, and ``query`` is the name of the
    SQL template (in ``individual_metric_queries``) to fetch this metric from.

    The remaining fields are optional and used by the behavior-skills modal:
    ``kpi_name`` is the short metric name (defaults to ``column_key``),
    ``table`` is the informational source table, ``is_percentage`` controls
    display formatting, and ``higher_is_better`` drives the Strong/Focus split.
    """

    column_key: str
    label: str = ""
    query: str = "rep_pivoted"
    kpi_name: str = ""
    table: str = ""
    is_percentage: bool = False
    higher_is_better: bool = True
    # Unit conversion applied everywhere the metric value is consumed (KPI
    # cards, radar, trends, performance score, coaching). ``unit_divisor`` scales
    # the raw SQL value (e.g. 3600 converts seconds → hours); ``unit_suffix`` is
    # appended to the formatted display value (e.g. " hrs").
    unit_divisor: float = 1.0
    unit_suffix: str = ""


@dataclass
class MetricGroup:
    """An ordered group of Azure SQL KPIs for the individual metrics page."""

    name: str
    kpis: List[MetricSpec] = field(default_factory=list)


@dataclass
class HeaderStat:
    """A stat card shown in the individual page right column."""
    key: str                     # dot-notation path within source, e.g. "new_prospect.total"
    label: str
    icon: str = ""
    source: str = "report"       # which source to resolve from (report, behavior_scores, etc.)


@dataclass
class OverviewSection:
    """One section on the individual overview tab."""
    key: str                       # unique id / data lookup key in report dict (e.g. "escalations")
    type: str                      # summary | risks | radar | section_renderer | coaching
    renderer: str = ""             # which section renderer to call (escalations, customer_experience, etc.)
    source: str = ""               # data source (e.g. "report")


@dataclass
class TrendMetric:
    """A single metric to show in the trends tab."""
    metric: str          # key in report["trends"] dict
    label: str = ""      # display label (defaults to metric)
    source: str = "trends"  # parent key in employee report JSON
    pre_scaled: bool = False  # True when data is already in display range (skip *100)


@dataclass
class TrendsConfig:
    """Config for the trends tab data source."""
    source: str = "trends"         # which key in report dict holds trend data
    trend_metrics: List["TrendMetric"] = field(default_factory=list)


@dataclass
class BehaviorSummaryConfig:
    """Config for the strong/focus behavior summary panel."""
    sources: List[str] = field(default_factory=lambda: ["behavior_scores"])
    threshold: float = 0.5
    strong_label: str = "STRONG"
    strong_color: str = "#0F9ED5"
    focus_label: str = "FOCUS"
    focus_color: str = "#ef4444"


@dataclass
class RadarMetric:
    """A metric to show on the radar chart."""
    metric: str          # key matching comparison[].metric in report
    label: str = ""      # display label (defaults to metric)
    source: str = "comparison"  # parent key in employee report JSON
    scale: str = "number"  # "percentage" converts 0-1 to 0-100; "number" keeps as-is
    pre_scaled: bool = False  # True when data is already 0-100 (skip *100)


@dataclass
class CoachingPriority:
    """One priority theme for the overview summary / coaching recommendations.

    ``key`` is a short id (e.g. ``vxs``), ``label`` is the display/prompt text,
    and ``metrics`` lists the metric ``column_key``s that belong to the theme.
    The order of these entries defines the priority (most important first).
    """
    key: str
    label: str = ""
    metrics: List[str] = field(default_factory=list)


@dataclass
class IndividualPageConfig:
    """Layout config for the individual employee page."""
    # KPI display (moved from top-level)
    kpi_groups: List[KPIGroup] = field(default_factory=list)
    # Extra KPI groups shown in the "More KPIs" popup (right of the dashboard)
    more_kpi_groups: List[KPIGroup] = field(default_factory=list)
    coreskills: Optional[Coreskills] = None
    behavioural_skills: Optional[BehaviouralSkills] = None
    wcc_kpis: Optional[WCCKpis] = None
    # Page layout
    header_stats: List[HeaderStat] = field(default_factory=list)
    tabs: List[str] = field(default_factory=lambda: ["overview", "trends", "goals", "notes"])
    overview_sections: List[OverviewSection] = field(default_factory=list)
    trends: Optional["TrendsConfig"] = None
    behavior_summary: Optional["BehaviorSummaryConfig"] = None
    radar_metrics: List["RadarMetric"] = field(default_factory=list)

    def has_kpi_groups(self) -> bool:
        """True if explicit KPI groups are configured."""
        return bool(self.kpi_groups)

    def has_more_kpi_groups(self) -> bool:
        """True if extra "More KPIs" popup groups are configured."""
        return bool(self.more_kpi_groups)


@dataclass
class MetricsHeaderConfig:
    """Header labels for the Individual Performance Metrics page.

    ``title_caption`` supports ``{week}``, ``{emp_id}`` and ``{program_id}``
    placeholders.
    """
    title_caption: str = "Week: {week} | Employee ID: {emp_id} | Program: {program_id}"
    performance_score_label: str = "Performance Score"
    dashboard_label: str = "Performance Dashboard"


@dataclass
class MetricsPanelStat:
    """The single header stat card in the metrics-page behavior panel.

    ``metric`` is the Azure SQL metric (column_key/label) whose value is shown.
    """
    label: str = "Total Calls"
    icon: str = "💬"
    metric: str = "Agent Calls"


@dataclass
class MetricsStrongFocusConfig:
    """Strong/Focus summary block in the metrics-page behavior panel."""
    enabled: bool = False
    threshold: float = 0.5
    strong_label: str = "STRONG"
    strong_color: str = "#0F9ED5"
    focus_label: str = "FOCUS"
    focus_color: str = "#ef4444"
    unavailable_caption: str = "Behaviour analytics are not available from Azure SQL yet."


@dataclass
class MetricsBehaviorPanelConfig:
    """Right-column behavior panel config for the metrics page."""
    stat: Optional[MetricsPanelStat] = None
    show_speedometer: bool = True
    strong_focus: Optional[MetricsStrongFocusConfig] = None


@dataclass
class MetricsOverviewSection:
    """One section on the metrics-page overview tab.

    ``type`` is one of ``radar``, ``coaching`` or ``risks``.
    """
    type: str
    label: str = ""


@dataclass
class IndividualMetricsPageConfig:
    """Layout config for the Individual Performance Metrics page (Azure SQL).

    Mirrors ``IndividualPageConfig`` but sourced from the program JSON
    ``individual_metrics`` block. ``groups`` reuses the SQL metric groups; the
    remaining fields drive the header, behavior panel, tabs, overview sections
    and the radar/trend metric selection.
    """
    groups: List[MetricGroup] = field(default_factory=list)
    header: Optional[MetricsHeaderConfig] = None
    behavior_panel: Optional[MetricsBehaviorPanelConfig] = None
    tabs: List[str] = field(default_factory=lambda: ["overview", "trends", "goals", "notes"])
    overview_sections: List[MetricsOverviewSection] = field(default_factory=list)
    radar_metrics: List["RadarMetric"] = field(default_factory=list)
    trend_metrics: List["TrendMetric"] = field(default_factory=list)
    # Ordered priority themes driving the overview summary + coaching recs.
    coaching_priority: List["CoachingPriority"] = field(default_factory=list)


@dataclass
class ManagerTopMetric:
    """A top-level metric card on the manager page."""
    key: str
    type: str = "computed"   # computed | kpi_agg
    computation: str = ""    # team_size | avg_performance | top_performers | needs_focus
    label_key: str = ""      # ui_labels key for the label
    subtitle_key: str = ""   # ui_labels key for the subtitle


@dataclass
class ManagerPageConfig:
    """Layout config for the manager overview page."""
    top_metrics: List[ManagerTopMetric] = field(default_factory=list)
    sections: List[str] = field(default_factory=lambda: ["quality_sales", "behavior_analysis", "team_roster", "coaching_priorities"])
    behavior_focus_count: int = 3
    behavior_excellence_count: int = 3
    escalation_threshold_factor: float = 1.5
    behavior_kpi_group: str = "Behavior Scores"    # kpi_group filter (fallback when behavior_kpis is empty)
    behavior_kpis: List[Dict[str, str]] = field(default_factory=list)  # Explicit behavior KPIs for analysis
    escalation_kpi: str = "escalations"             # KPI key used for escalation threshold
    wcc_roster_kpis: List[str] = field(default_factory=list)  # WCC KPIs to show in team roster
    summary_kpis: List[Dict[str, str]] = field(default_factory=list)  # Quality & Sales aggregates
    roster_kpis: List[Dict[str, str]] = field(default_factory=list)   # Team roster KPI columns
    perf_score_key: str = "performance_score"       # KPI key for performance score column
    risk_count_key: str = "risk_count"              # KPI key for risk count
    top_performer_quantile: float = 0.9             # Quantile threshold for top performers
    needs_attention_quantile: float = 0.8           # Quantile threshold for needs attention
    risk_count_threshold: int = 2                   # Risk count >= this triggers needs attention


@dataclass
class ProgramConfig:
    """Complete configuration for a single program."""

    program_id: str
    program_name: str
    blob_prefix: Optional[str] = None  # None → legacy flat paths
    cache_ttl_seconds: int = 300       # Cache TTL for blob/data loaders

    inverse_kpis: List[str] = field(default_factory=list)

    # Scoring
    scoring_components: List[ScoringComponent] = field(default_factory=list)

    # Risks
    risk_rules: List[RiskRule] = field(default_factory=list)

    # Page layout configs
    individual_page: Optional[IndividualPageConfig] = None
    manager_page: Optional[ManagerPageConfig] = None
    analytics_kpi_groups: List[KPIGroup] = field(default_factory=list)

    # Ordered Azure SQL metric groups for the Individual Performance Metrics page
    individual_metric_groups: List[MetricGroup] = field(default_factory=list)
    # Overflow Azure SQL metric groups shown only in the metrics-page "More KPIs" popup
    individual_metric_more_groups: List[MetricGroup] = field(default_factory=list)
    # Behavior-skill groups shown in the metrics-page "View All Behaviors" modal
    behavior_skill_groups: List[MetricGroup] = field(default_factory=list)
    # Named SQL query templates referenced by metric KPIs (name -> SQL with placeholders)
    individual_metric_queries: Dict[str, str] = field(default_factory=dict)

    # Full layout config for the Individual Performance Metrics page (from the
    # extended ``individual_metrics`` block: header, behavior panel, tabs,
    # overview sections, radar/trend metric selection).
    individual_metrics_page: Optional["IndividualMetricsPageConfig"] = None

    # UI display labels (nested dict: section → key → text)
    ui_labels: Dict[str, Dict[str, str]] = field(default_factory=dict)

    def has_kpi_groups(self) -> bool:
        """True if explicit KPI groups are configured (delegates to individual_page)."""
        return bool(self.individual_page and self.individual_page.has_kpi_groups())

    def has_analytics_kpi_groups(self) -> bool:
        """True if analytics-specific KPI groups are configured in the program JSON."""
        return bool(self.analytics_kpi_groups)

    def has_scoring(self) -> bool:
        """True if scoring components are configured."""
        return bool(self.scoring_components)

    def is_inverse_kpi(self, key: str) -> bool:
        """True if lower values are better for this KPI (e.g. escalations)."""
        return key in self.inverse_kpis

    def get_label(self, section: str, key: str, default: str = "") -> str:
        """Return a UI label from the program config.

        Usage::

            cfg.get_label("manager", "page_title", "Team Overview")

        If the label isn't defined for this program, *default* is returned.
        """
        return self.ui_labels.get(section, {}).get(key, default)



# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

_program_cache: Dict[str, ProgramConfig] = {}


def clear_program_cache() -> None:
    """Clear the in-memory program config cache."""
    _program_cache.clear()


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def load_program_config(program_id: str) -> ProgramConfig:
    """Load and cache a program configuration from its JSON file.

    Falls back to a generic config if no JSON file exists for *program_id*.
    """
    if program_id in _program_cache:
        return _program_cache[program_id]

    config_path = _PROGRAMS_DIR / f"{program_id}.json"
    if not config_path.exists():
        logger.warning("No config file for program '%s' — using generic", program_id)
        cfg = _generic_config(program_id)
        _program_cache[program_id] = cfg
        return cfg

    with open(config_path, "r", encoding="utf-8") as f:
        raw = json.load(f)

    cfg = _parse_config(raw)
    _program_cache[program_id] = cfg
    logger.info("Loaded program config: %s (%s)", cfg.program_id, cfg.program_name)
    return cfg


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def discover_programs() -> List[str]:
    """Return sorted list of program IDs that have local config files."""
    programs: List[str] = []
    if _PROGRAMS_DIR.exists():
        for f in _PROGRAMS_DIR.glob("*.json"):
            programs.append(f.stem)
    return sorted(programs)


def find_mode_for_program(program_name: str) -> str:
    """Find the config ID for a given program name (case-insensitive)."""
    if not program_name:
        return DEFAULT_PROGRAM
    lower = program_name.strip().lower()
    if lower in [p.strip().lower() for p in Settings.TELESALES_PROGRAMS]:
        return "telesales"
    elif lower in [p.strip().lower() for p in Settings.WCC_PROGRAMS]:
        return "wcc"
    elif lower in [p.strip().lower() for p in Settings.PSO_PROGRAMS]:
        return "pso"
    else:
        logger.warning("Program '%s' not found in TELESALES_PROGRAMS, WCC_PROGRAMS or PSO_PROGRAMS. Defaulting to '%s'.", program_name, DEFAULT_PROGRAM)
        return DEFAULT_PROGRAM


def get_all_program_configs() -> Dict[str, ProgramConfig]:
    """Load and return configs for all discovered programs."""
    return {pid: load_program_config(pid) for pid in discover_programs()}


def get_cache_ttl() -> int:
    """Return ``cache_ttl_seconds`` from the default program config.

    Safe to call at module-load / decorator-application time.
    Falls back to 300 if the config cannot be loaded.
    """
    try:
        return load_program_config(DEFAULT_PROGRAM).cache_ttl_seconds
    except Exception:
        return 300


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _parse_config(raw: dict) -> ProgramConfig:
    """Parse raw JSON dict into a ProgramConfig dataclass."""
    scoring = [
        ScoringComponent(
            type=c["type"],
            weight=c.get("weight", 0),
            kpis=c.get("kpis", []),
            targets=c.get("targets", []),
            tiers=c.get("tiers", []),
            source=c.get("source", ""),
            metrics_ref=c.get("metrics_ref", ""),
        )
        for c in raw.get("scoring", [])
    ]

    risks = [
        RiskRule(
            type=r["type"],
            threshold=r.get("threshold", 0),
            severity=r.get("severity", "high"),
            kpis=r.get("kpis", []),
            reason=r.get("reason", ""),
        )
        for r in raw.get("risk_rules", [])
    ]
    
    ui_labels = raw.get("ui", {})

    # ── Individual page layout ──
    ip_raw = raw.get("individual_page") or {}
    _trends_raw = ip_raw.get("trends") or {}
    _bs_raw = ip_raw.get("behavior_summary") or {}

    # Parse kpi_groups, core_skills, behavior_skills, wcc_count_kpis from individual_page
    def _parse_kpi_spec(k: dict) -> KPISpec:
        return KPISpec(
            key=k["key"], label=k["label"],
            source=k.get("source"), source_type=k.get("source_type"),
            popup=k.get("popup", False), items=k.get("items"),
            items_prop=k.get("items_prop"),
            category=k.get("category"), prompt=k.get("prompt"),
            pre_scaled=k.get("pre_scaled", True),
        )

    _ip_kpi_groups = [
        KPIGroup(
            name=g["name"],
            kpis=[_parse_kpi_spec(k) for k in g.get("kpis", [])],
        )
        for g in ip_raw.get("kpi_groups", [])
    ]
    _ip_more_kpi_groups = [
        KPIGroup(
            name=g["name"],
            kpis=[_parse_kpi_spec(k) for k in g.get("kpis", [])],
        )
        for g in ip_raw.get("more_kpi_groups", [])
    ]
    _cs_raw = ip_raw.get("core_skills") or {}
    _ip_coreskills = Coreskills(
        name=_cs_raw.get("name", "Core Skills"),
        kpis=[_parse_kpi_spec(k) for k in _cs_raw.get("kpis", [])],
    ) if _cs_raw else None
    _bsk_raw = ip_raw.get("behavior_skills") or {}
    _ip_behavioural = BehaviouralSkills(
        name=_bsk_raw.get("name", "Behavior Skills"),
        kpis=[_parse_kpi_spec(k) for k in _bsk_raw.get("kpis", [])],
    ) if _bsk_raw else None
    _wcc_raw = ip_raw.get("wcc_count_kpis") or {}
    _ip_wcc_kpis = WCCKpis(
        name=_wcc_raw.get("name", "WCC Count KPIs"),
        kpis=[_parse_kpi_spec(k) for k in _wcc_raw.get("kpis", [])],
    ) if _wcc_raw else None

    individual_page = IndividualPageConfig(
        kpi_groups=_ip_kpi_groups,
        more_kpi_groups=_ip_more_kpi_groups,
        coreskills=_ip_coreskills,
        behavioural_skills=_ip_behavioural,
        wcc_kpis=_ip_wcc_kpis,
        header_stats=[
            HeaderStat(
                key=s["key"], label=s.get("label", ""), icon=s.get("icon", ""),
                source=s.get("source", "report"),
            )
            for s in ip_raw.get("header_stats", [])
        ],
        tabs=ip_raw.get("tabs", ["overview", "trends", "goals", "notes"]),
        overview_sections=[
            OverviewSection(
                key=sec["key"], type=sec["type"],
                renderer=sec.get("renderer", ""),
                source=sec.get("source", ""),
            )
            for sec in ip_raw.get("overview_sections", [])
        ],
        trends=TrendsConfig(
            source=_trends_raw.get("source", "trends"),
            trend_metrics=[
                TrendMetric(
                    metric=tm["metric"],
                    label=tm.get("label", tm["metric"]),
                    source=tm.get("source", "trends"),
                    pre_scaled=tm.get("pre_scaled", False),
                )
                for tm in _trends_raw.get("trend_metrics", [])
            ],
        ) if _trends_raw else None,
        behavior_summary=BehaviorSummaryConfig(
            sources=_bs_raw.get("sources", ["behavior_scores"]),
            threshold=_bs_raw.get("threshold", 0.5),
            strong_label=_bs_raw.get("strong_label", "STRONG"),
            strong_color=_bs_raw.get("strong_color", "#0F9ED5"),
            focus_label=_bs_raw.get("focus_label", "FOCUS"),
            focus_color=_bs_raw.get("focus_color", "#ef4444"),
        ) if _bs_raw else None,
        radar_metrics=[
            RadarMetric(
                metric=rm["metric"],
                label=rm.get("label", rm["metric"]),
                source=rm.get("source", "comparison"),
                scale=rm.get("scale", "number"),
                pre_scaled=rm.get("pre_scaled", False),
            )
            for rm in ip_raw.get("radar_metrics", [])
        ],
    ) if ip_raw else None

    # ── Analytics page KPI groups (optional) ──
    analytics_raw = raw.get("analytics") or {}
    _analytics_kpi_groups = [
        KPIGroup(name=g["name"], kpis=[_parse_kpi_spec(k) for k in g.get("kpis", [])])
        for g in analytics_raw.get("kpi_groups", [])
    ]

    # Fallback: when no analytics.kpi_groups provided, reuse individual_page.kpi_groups
    if not _analytics_kpi_groups and _ip_kpi_groups:
        # shallow copy the groups so callers can treat analytics_kpi_groups independently
        _analytics_kpi_groups = [
            KPIGroup(name=g.name, kpis=list(g.kpis)) for g in _ip_kpi_groups
        ]

    # ── Individual Performance Metrics page: ordered Azure SQL metric groups ──
    def _parse_metric_spec(k: dict) -> MetricSpec:
        return MetricSpec(
            column_key=k["column_key"],
            label=k.get("label") or k["column_key"],
            query=k.get("query", "rep_pivoted"),
            kpi_name=k.get("kpi_name") or k["column_key"],
            table=k.get("table", ""),
            is_percentage=k.get("is_percentage", False),
            higher_is_better=k.get("higher_is_better", True),
            unit_divisor=float(k.get("unit_divisor", 1.0) or 1.0),
            unit_suffix=k.get("unit_suffix", ""),
        )

    _metric_groups = [
        MetricGroup(
            name=g["name"],
            kpis=[_parse_metric_spec(k) for k in g.get("kpis", [])],
        )
        for g in (raw.get("individual_metrics") or {}).get("groups", [])
    ]
    # Overflow metric groups shown in the metrics-page "More KPIs" popup only.
    _metric_more_groups = [
        MetricGroup(
            name=g["name"],
            kpis=[_parse_metric_spec(k) for k in g.get("kpis", [])],
        )
        for g in (raw.get("individual_metrics") or {}).get("more_groups", [])
    ]
    _metric_queries = dict(raw.get("individual_metric_queries", {}))

    # Behavior-skills groups (rendered in the metrics-page "View All Behaviors"
    # modal). Fetched alongside the display groups but never shown as KPI cards.
    _behavior_skill_groups = [
        MetricGroup(
            name=g["name"],
            kpis=[_parse_metric_spec(k) for k in g.get("kpis", [])],
        )
        for g in (raw.get("individual_metrics") or {}).get("behavior_skills", [])
    ]

    # ── Individual Performance Metrics page layout (extends individual_metrics) ──
    _im_raw = raw.get("individual_metrics") or {}
    _im_header_raw = _im_raw.get("header") or {}
    _im_panel_raw = _im_raw.get("behavior_panel") or {}
    _im_stat_raw = _im_panel_raw.get("stat") or {}
    _im_sf_raw = _im_panel_raw.get("strong_focus") or {}
    individual_metrics_page = IndividualMetricsPageConfig(
        groups=_metric_groups,
        header=MetricsHeaderConfig(
            title_caption=_im_header_raw.get(
                "title_caption", "Week: {week} | Employee ID: {emp_id} | Program: {program_id}"
            ),
            performance_score_label=_im_header_raw.get("performance_score_label", "Performance Score"),
            dashboard_label=_im_header_raw.get("dashboard_label", "Performance Dashboard"),
        ) if _im_header_raw else None,
        behavior_panel=MetricsBehaviorPanelConfig(
            stat=MetricsPanelStat(
                label=_im_stat_raw.get("label", "Total Calls"),
                icon=_im_stat_raw.get("icon", "💬"),
                metric=_im_stat_raw.get("metric", "Agent Calls"),
            ) if _im_stat_raw else None,
            show_speedometer=_im_panel_raw.get("show_speedometer", True),
            strong_focus=MetricsStrongFocusConfig(
                enabled=_im_sf_raw.get("enabled", False),
                threshold=_im_sf_raw.get("threshold", 0.5),
                strong_label=_im_sf_raw.get("strong_label", "STRONG"),
                strong_color=_im_sf_raw.get("strong_color", "#0F9ED5"),
                focus_label=_im_sf_raw.get("focus_label", "FOCUS"),
                focus_color=_im_sf_raw.get("focus_color", "#ef4444"),
                unavailable_caption=_im_sf_raw.get(
                    "unavailable_caption",
                    "Behaviour analytics are not available from Azure SQL yet.",
                ),
            ) if _im_sf_raw else None,
        ) if _im_panel_raw else None,
        tabs=_im_raw.get("tabs", ["overview", "trends", "goals", "notes"]),
        overview_sections=[
            MetricsOverviewSection(type=s["type"], label=s.get("label", ""))
            for s in _im_raw.get("overview_sections", [])
        ],
        radar_metrics=[
            RadarMetric(
                metric=rm["metric"],
                label=rm.get("label", rm["metric"]),
                source=rm.get("source", "comparison"),
                scale=rm.get("scale", "number"),
                pre_scaled=rm.get("pre_scaled", False),
            )
            for rm in _im_raw.get("radar_metrics", [])
        ],
        trend_metrics=[
            TrendMetric(
                metric=tm["metric"],
                label=tm.get("label", tm["metric"]),
                source=tm.get("source", "trends"),
                pre_scaled=tm.get("pre_scaled", True),
            )
            for tm in _im_raw.get("trend_metrics", [])
        ],
        coaching_priority=[
            CoachingPriority(
                key=cp.get("key", ""),
                label=cp.get("label", cp.get("key", "")),
                metrics=list(cp.get("metrics", [])),
            )
            for cp in _im_raw.get("coaching_priority", [])
        ],
    ) if _im_raw else None

    mp_raw = raw.get("manager_page") or {}
    manager_page = ManagerPageConfig(
        top_metrics=[
            ManagerTopMetric(
                key=m["key"], type=m.get("type", "computed"),
                computation=m.get("computation", ""),
                label_key=m.get("label_key", ""),
                subtitle_key=m.get("subtitle_key", ""),
            )
            for m in mp_raw.get("top_metrics", [])
        ],
        sections=mp_raw.get("sections", ["quality_sales", "behavior_analysis", "team_roster", "coaching_priorities"]),
        behavior_focus_count=mp_raw.get("behavior_focus_count", 3),
        behavior_excellence_count=mp_raw.get("behavior_excellence_count", 3),
        escalation_threshold_factor=mp_raw.get("escalation_threshold_factor", 1.5),
        behavior_kpi_group=mp_raw.get("behavior_kpi_group", "Behavior Scores"),
        behavior_kpis=mp_raw.get("behavior_kpis", []),
        escalation_kpi=mp_raw.get("escalation_kpi", "escalations"),
        wcc_roster_kpis=mp_raw.get("wcc_roster_kpis", []),
        summary_kpis=mp_raw.get("summary_kpis", []),
        roster_kpis=mp_raw.get("roster_kpis", []),
        perf_score_key=mp_raw.get("perf_score_key", "performance_score"),
        risk_count_key=mp_raw.get("risk_count_key", "risk_count"),
        top_performer_quantile=mp_raw.get("top_performer_quantile", 0.9),
        needs_attention_quantile=mp_raw.get("needs_attention_quantile", 0.8),
        risk_count_threshold=mp_raw.get("risk_count_threshold", 2),
    ) if mp_raw else None

    return ProgramConfig(
        program_id=raw["program_id"],
        program_name=raw["program_name"],
        blob_prefix=raw.get("blob_prefix"),
        cache_ttl_seconds=raw.get("cache_ttl_seconds", 300),
        inverse_kpis=raw.get("inverse_kpis", []),
        scoring_components=scoring,
        risk_rules=risks,
        individual_page=individual_page,
        manager_page=manager_page,
        ui_labels=ui_labels,
        analytics_kpi_groups=_analytics_kpi_groups,
        individual_metric_groups=_metric_groups,
        individual_metric_more_groups=_metric_more_groups,
        behavior_skill_groups=_behavior_skill_groups,
        individual_metric_queries=_metric_queries,
        individual_metrics_page=individual_metrics_page,
    )


def _generic_config(program_id: str) -> ProgramConfig:
    """Return a minimal generic config for programs with no JSON file.

    The dashboard will auto-group KPIs from the data at runtime.
    """
    return ProgramConfig(
        program_id=program_id,
        program_name=program_id.replace("_", " ").title(),
        blob_prefix=program_id,
    )
