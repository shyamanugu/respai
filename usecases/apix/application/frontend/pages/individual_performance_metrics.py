"""
ui/pages/individual_performance_metrics.py — Individual Performance Metrics
=======================================================================================
Renders the *Individual Performance Metrics* page using the SAME visual theme as
the *Individual Report* (KPI cards, behaviour-panel styling, the Performance Radar
and the Overview / Trends / Goals / Notes tabs) but sourced entirely from
**Azure SQL** instead of the blob JSON reports:

  * KPI group cards (Resolve / Efficiency / Quality) come from the program JSON
    ``individual_metrics`` groups + ``individual_metric_queries`` (rep_pivoted,
    customer_experience), fetched per-employee with week-over-week deltas.
  * The Performance Radar is built at runtime from the SQL values, plotting the
    agent's current scores against their team average. "Team" is defined by the
    ``*_team`` SQL templates in the program JSON (same ``VirtualLocationID`` for
    ``rep_pivoted`` metrics, same ``Manager 1`` for ``customer_experience``).
  * Coaching recommendations / risks are generated at runtime by GPT from the
    current-vs-previous week KPI values (best-effort; "N/A" when unavailable).
  * Sections without a SQL source (sales / retention / escalations / behaviours)
    keep the UI styling and show "N/A" placeholders.

Each metric section (Resolve / Efficiency / Quality) is loaded asynchronously
via ``fetch_employee_metrics_by_section`` — every section runs its own
section-scoped query on its own connection, concurrently, and every query and
its results are logged. The Individual Report and its shared ``individual.py``
renderers are intentionally left untouched.

Scope: this view always operates on ONE selected agent (not a list/multiple agents).
"""

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
import streamlit.components.v1 as components
from datetime import datetime
from typing import Optional

from backend.auth.session import log_audit_event
from backend.db.notes import add_note, get_notes, edit_note, hide_note
from backend.config.logging import get_logger
from backend.config.programs import DEFAULT_PROGRAM, load_program_config, get_cache_ttl
from backend.services.blob_service import get_selected_week
from backend.services.scoring import (
    calculate_trend_velocity,
    get_trend_color,
)
from backend.services import coaching_ai
from frontend.components.charts import (
    create_performance_radar,
    create_speedometer_gauge,
    create_trend_heatmap,
    render_kpi_card,
)
from frontend.components.sections import render_key_improvements_section
from backend.services.azure_sql_query import (
    fetch_employee_metrics_by_section,
    fetch_employee_and_team_metrics,
    fetch_team_metrics,
    normalize_metric_name,
    get_metric_groups,
    get_metric_more_groups,
    get_behavior_skill_groups,
)
# Coaching-tip renderer reused from the Individual Report to keep identical styling.
from frontend.pages.individual import _render_coaching_tips

logger = get_logger(__name__)

# Metrics where a higher value is undesirable; a positive delta is shown red.
# Handle-time / response-time / thumbs-down are "lower is better" too, so a rising
# value should read as a regression in both the KPI-card colours and the score.
_INVERSE_METRIC_KEYWORDS = (
    "disconnect", "outbound", "hold", "wait", "queue",
    "return", "transfer", "escalation",
    "aht", "response time", "thumbs down",
)

# Count/volume KPI groups excluded from the radar and the performance score:
# their magnitudes (units, orders, counts) differ by orders of magnitude from
# the 0–100 rate metrics and would otherwise flatten the radar and peg the
# score. They are still shown as KPI cards and in the trend charts.
_RADAR_EXCLUDED_GROUPS = {"Sales", "Behaviours"}

# X-axis labels for the 4-point trend series, oldest -> newest.
_TREND_WINDOW_LABELS = ("3 wks ago", "2 wks ago", "1 wk ago", "Current")

# Number of KPI cards per row in the metrics grid.
_CARDS_PER_ROW = 5

# Temporary testing toggle for which EmployeeID the Azure SQL queries run against.
#   _HARDCODE_EMP_ID = True  -> the dropdown selection is mapped by its position
#                               (position % len) to one of the stand-in agent ids
#                               below, so each dropdown name demos a real agent's
#                               Azure SQL data. The selected NAME is still shown in
#                               the header; the mapped agent id is shown as the
#                               Employee ID and used for every query.
#   _HARDCODE_EMP_ID = False -> use the EmployeeID coming from the agent dropdown.
_HARDCODE_EMP_ID: bool = True
# Stand-in agents that have real Azure SQL data (mapped by dropdown position).
_METRICS_AGENT_IDS = ["2306", "1106", "3974", "7849", "14653", "25692", "30048"]

# Coaching notes for THIS page are stored in a dedicated namespace so they never
# mix with the Individual Report's notes (which use the default namespace).
# Physically: blob path ``notes/metrics/{emp_id}.json`` vs report ``notes/{emp_id}.json``.
_METRICS_NOTES_NAMESPACE: str = "metrics"


class _SafeFormatDict(dict):
    """dict for ``str.format_map`` that leaves unknown placeholders untouched."""

    def __missing__(self, key):
        return "{" + key + "}"


def _metrics_page_cfg(program_cfg):
    """Return the Individual Performance Metrics page layout config, or ``None``.

    The config is loaded from the program JSON ``individual_metrics`` block
    (header, behavior panel, tabs, overview sections, radar/trend metrics). All
    renderers degrade gracefully to built-in defaults when it is absent.
    """
    return getattr(program_cfg, "individual_metrics_page", None)


def _effective_emp_id(emp_id: Optional[str]) -> Optional[str]:
    """Clean an EmployeeID string for use in the Azure SQL queries.

    The dropdown-position → stand-in-agent mapping (when ``_HARDCODE_EMP_ID`` is
    enabled) is applied upstream in the render function; this helper only
    normalizes whatever id it is handed.
    """
    emp = emp_id.strip() if isinstance(emp_id, str) else emp_id
    return emp or None


def _mapped_agent_id(position: int) -> Optional[str]:
    """Map a 0-based dropdown position to a stand-in agent id (``position % N``).

    Cycles through ``_METRICS_AGENT_IDS`` so every dropdown selection resolves to
    one of the real-data agents, wrapping around once the list is exhausted.
    """
    if not _METRICS_AGENT_IDS:
        return None
    return str(_METRICS_AGENT_IDS[position % len(_METRICS_AGENT_IDS)]).strip() or None


def _dropdown_position(emp_names: dict, selected_emp_name: Optional[str]) -> int:
    """0-based index of the selected name within the sorted dropdown order.

    Mirrors the sidebar's ``sorted(emp_names.keys())`` ordering so the position
    matches exactly what the user picked. Falls back to 0 when not found.
    """
    names = sorted((emp_names or {}).keys())
    try:
        return names.index(selected_emp_name)
    except (ValueError, TypeError):
        return 0


def _agg_key(metric_agg: str) -> str:
    """Map the sidebar 'Average'/'Sum' choice to the SQL bucket key ('avg'/'sum')."""
    return "sum" if str(metric_agg).strip().lower().startswith("sum") else "avg"


def _week_to_end_date(week: Optional[str]) -> Optional[datetime]:
    """Parse a ``YYYY-MM-DD`` week label into an end-date for the SQL window."""
    if not week:
        return None
    try:
        return datetime.strptime(str(week), "%Y-%m-%d")
    except (TypeError, ValueError):
        return None


def _is_positive_metric(metric_name: str) -> bool:
    """True when a higher value is good for this metric."""
    return not any(neg in metric_name.lower() for neg in _INVERSE_METRIC_KEYWORDS)


def _looks_percentage(label: str) -> bool:
    """Heuristic: does this KPI live on a 0–100 percentage scale?

    Used by the performance score to fall back to an absolute attainment value
    (the percentage itself) when a KPI has no team benchmark to normalise
    against. Deliberately conservative so non-percentage KPIs (AHT, response
    time, VXS score) are never mistaken for percentages.
    """
    lbl = str(label).strip().lower()
    return lbl.endswith("%") or "resolve" in lbl


# ---------------------------------------------------------------------------
# Cross-session caches (mirrors blob_service). The SQL trend/team fetch and the
# GPT coaching call are the two expensive operations on this page and both are
# deterministic for a given (program, employee, period). Wrapping them in
# ``@st.cache_data`` caches their results PROCESS-WIDE for the configured TTL, so
# repeat page loads — including a *fresh* browser session/tab within the TTL —
# resolve WITHOUT re-hitting Azure SQL or the LLM. This is exactly what makes the
# blob-backed pages fast; the SQL page previously only had a per-session
# ``st.session_state`` cache, so every new session re-paid the full cold cost.
# The per-session cache below still serves same-session reruns instantly.
#
# All cache-key args are strings/ints so they hash cleanly. For coaching, the
# ``_current``/``_previous`` dicts are underscore-prefixed so Streamlit EXCLUDES
# them from the cache key (they are deterministic for a given program/emp/week).
# ---------------------------------------------------------------------------


def _end_key_to_date(end_key: str) -> Optional[datetime]:
    """Inverse of the ``end_date -> 'YYYY-MM-DD'`` cache-key encoding."""
    if not end_key or end_key == "none":
        return None
    try:
        return datetime.strptime(end_key, "%Y-%m-%d")
    except (TypeError, ValueError):
        return None


@st.cache_data(ttl=get_cache_ttl(), show_spinner=False)
def _cached_employee_and_team(program_id: str, emp_id: str, end_key: str, days_back: int):
    """Cross-session cached combined trend + team fetch (one parallel burst)."""
    return fetch_employee_and_team_metrics(
        emp_id, days_back=days_back, end_date=_end_key_to_date(end_key), program_id=program_id
    )


@st.cache_data(ttl=get_cache_ttl(), show_spinner=False)
def _cached_employee_by_section(program_id: str, emp_id: str, end_key: str, days_back: int):
    """Cross-session cached individual-only section fetch."""
    return fetch_employee_metrics_by_section(
        emp_id, days_back=days_back, end_date=_end_key_to_date(end_key), program_id=program_id
    )


@st.cache_data(ttl=get_cache_ttl(), show_spinner=False)
def _cached_team(program_id: str, emp_id: str, end_key: str, days_back: int):
    """Cross-session cached team-only current-window fetch."""
    return fetch_team_metrics(
        emp_id, days_back=days_back, end_date=_end_key_to_date(end_key), program_id=program_id
    )


@st.cache_data(ttl=get_cache_ttl(), show_spinner=False)
def _cached_coaching_insights(program_id: str, emp_id: str, week: str, _current: dict, _previous: dict, _priority=None):
    """Cross-session cached GPT coaching (keyed by program/emp/week only)."""
    return coaching_ai.generate_coaching_insights(emp_id, _current, _previous, program_id, priority=_priority)


def _get_cached_metrics(emp_id: str, end_date: Optional[datetime], days_back: int, program_id: str) -> dict:
    """Return current+previous metrics for an employee+period, using session cache.

    Cache strategy:
      - Key is (program, employee, period). Toggling Average/Sum reuses the entry.
      - When program, employee or period changes, the key changes and we re-query.
    """
    end_key = end_date.strftime("%Y-%m-%d") if isinstance(end_date, datetime) else "none"
    cache_key = f"{program_id}|{emp_id}|{end_key}|{days_back}"

    cached = st.session_state.get("_azure_metrics_cache")
    if cached and cached.get("key") == cache_key:
        return cached.get("data", {})

    # Key changed (program/employee/period) → re-query Azure SQL via the
    # cross-session cache (a fresh session within the TTL skips the SQL round
    # trip entirely). Each metric section loads asynchronously on its own
    # connection; this falls back to the per-window parallel fetch on any error.
    metrics = _cached_employee_by_section(program_id, emp_id, end_key, days_back)
    empty = {
        "avg": {}, "sum": {},
        "avg_prev": {}, "sum_prev": {},
        "avg_prev2": {}, "sum_prev2": {},
        "avg_prev3": {}, "sum_prev3": {},
    }
    emp_data = metrics if metrics else empty
    st.session_state["_azure_metrics_cache"] = {"key": cache_key, "data": emp_data}
    return emp_data


def _get_cached_team_metrics(emp_id: str, end_date: Optional[datetime], days_back: int, program_id: str) -> dict:
    """Return current-window TEAM-average metrics for an employee, using session cache.

    Cached separately from the individual metrics but with the same
    (program, employee, period) key so toggling Average/Sum reuses the entry.
    Returns the ``{"avg": {...}, "sum": {...}}`` structure produced by
    :func:`fetch_team_metrics` (team defined by the ``*_team`` SQL templates).
    """
    end_key = end_date.strftime("%Y-%m-%d") if isinstance(end_date, datetime) else "none"
    cache_key = f"{program_id}|{emp_id}|{end_key}|{days_back}"

    cached = st.session_state.get("_azure_team_metrics_cache")
    if cached and cached.get("key") == cache_key:
        return cached.get("data", {})

    team = _cached_team(program_id, emp_id, end_key, days_back)
    data = team if team else {"avg": {}, "sum": {}}
    st.session_state["_azure_team_metrics_cache"] = {"key": cache_key, "data": data}
    return data


def _prime_metrics_and_team_cache(
    emp_id: str, end_date: Optional[datetime], days_back: int, program_id: str
) -> None:
    """Fill BOTH the individual-trend and team-average caches in one parallel burst.

    When either cache is cold for the current (program, employee, period) key, the
    employee's trend windows and the team-average window are fetched together via
    :func:`fetch_employee_and_team_metrics` — all queries fire concurrently — and
    both session caches are primed at once. When both caches are already warm this
    is a no-op (an Average/Sum toggle reuses the same entries). Priming here is
    what lets the two cached getters below resolve without any sequential
    round-trips to Azure SQL.
    """
    end_key = end_date.strftime("%Y-%m-%d") if isinstance(end_date, datetime) else "none"
    cache_key = f"{program_id}|{emp_id}|{end_key}|{days_back}"

    ind_cached = st.session_state.get("_azure_metrics_cache")
    team_cached = st.session_state.get("_azure_team_metrics_cache")
    ind_hit = bool(ind_cached and ind_cached.get("key") == cache_key)
    team_hit = bool(team_cached and team_cached.get("key") == cache_key)
    if ind_hit and team_hit:
        return

    trend, team = _cached_employee_and_team(program_id, emp_id, end_key, days_back)
    # Never let a transient empty result (e.g. a brief SQL blip / timeout) sit in
    # the cross-session cache for the whole TTL — purge it so the next render
    # retries instead of showing N/A for minutes.
    if not (trend and (trend.get("avg") or trend.get("sum"))):
        _cached_employee_and_team.clear()
    empty_trend = {
        "avg": {}, "sum": {},
        "avg_prev": {}, "sum_prev": {},
        "avg_prev2": {}, "sum_prev2": {},
        "avg_prev3": {}, "sum_prev3": {},
    }
    if not ind_hit:
        st.session_state["_azure_metrics_cache"] = {"key": cache_key, "data": trend or empty_trend}
    if not team_hit:
        st.session_state["_azure_team_metrics_cache"] = {"key": cache_key, "data": team or {"avg": {}, "sum": {}}}


def _lookup_metric_value(employee_metrics: dict, norm_metrics: dict, metric_name: str):
    """Resolve a metric value with exact then normalized matching (avoids false N/A)."""
    if metric_name in employee_metrics:
        return employee_metrics[metric_name]
    return norm_metrics.get(normalize_metric_name(metric_name))


def _unit_conversion_map(program_id: str) -> dict:
    """Return ``{normalized_column_key: divisor}`` for metrics with a unit divisor.

    Driven entirely by the program JSON (``unit_divisor`` on a metric spec, e.g.
    ``3600`` to convert AHT seconds → hours). Used to convert raw SQL values
    consistently across the KPI cards, radar, trends, performance score and the
    coaching input so a large-magnitude metric doesn't dwarf the others.
    """
    conv: dict = {}
    for group in get_metric_groups(program_id):
        for spec in group.kpis:
            divisor = getattr(spec, "unit_divisor", 1.0) or 1.0
            if divisor and divisor != 1.0:
                conv[normalize_metric_name(spec.column_key)] = divisor
    return conv


def _apply_unit_conversions(metrics: dict, conv_map: dict) -> dict:
    """Return a NEW dict with configured metrics divided by their unit divisor.

    Never mutates the input (the source dicts come from a shared cache) and
    leaves values for metrics without a configured divisor untouched.
    """
    if not metrics:
        return {}
    if not conv_map:
        return dict(metrics)
    out: dict = {}
    for key, value in metrics.items():
        divisor = conv_map.get(normalize_metric_name(key))
        if divisor and isinstance(value, (int, float)):
            out[key] = float(value) / divisor
        else:
            out[key] = value
    return out


# ---------------------------------------------------------------------------
# SQL data-fetch + mapping pipeline (query → mapping → report-like dict)
# ---------------------------------------------------------------------------


def fetch_metrics_report(
    emp_id: str,
    program_id: str,
    end_date: Optional[datetime],
    days_back: int,
    aggregation: str,
) -> dict:
    """Build a report-like dict for the metrics page entirely from Azure SQL.

    Mirrors the structure consumed by the Individual Report chart/section
    renderers (``comparison`` for the radar, ``trends`` for the heatmap/lines)
    so the existing UI components can be reused unchanged. Returns empty /
    ``"N/A"`` placeholders when SQL data is not yet available.
    """
    eff_emp = _effective_emp_id(emp_id)
    adjusted_days = max(1, days_back - 1) if isinstance(days_back, int) else days_back

    # Prime both the individual-trend and team caches in ONE concurrent burst so
    # the two cached getters below resolve without sequential round-trips.
    _prime_metrics_and_team_cache(eff_emp, end_date, adjusted_days, program_id)
    trend_data = _get_cached_metrics(eff_emp, end_date, adjusted_days, program_id)
    team_data = _get_cached_team_metrics(eff_emp, end_date, adjusted_days, program_id)
    prev_key = "avg_prev" if aggregation == "avg" else "sum_prev"
    current = (trend_data.get(aggregation) or {}) if trend_data else {}
    previous = (trend_data.get(prev_key) or {}) if trend_data else {}
    team_current = (team_data.get(aggregation) or {}) if team_data else {}
    # Behavior-skill KPIs always surface the selected-period AVERAGE value from
    # the DB, regardless of the page's avg/sum aggregation toggle.
    current_avg = (trend_data.get("avg") or {}) if trend_data else {}
    previous_avg = (trend_data.get("avg_prev") or {}) if trend_data else {}

    # Program-driven unit conversion (e.g. AHT seconds → hours) applied ONCE at
    # the source so every consumer built below — KPI cards, radar comparison,
    # performance score, behaviour panel and coaching input — sees the display
    # unit. Trend windows are converted per-spec inside _build_trends_series
    # (they read the raw ``trend_data`` windows directly).
    conv_map = _unit_conversion_map(program_id)
    if conv_map:
        current = _apply_unit_conversions(current, conv_map)
        previous = _apply_unit_conversions(previous, conv_map)
        current_avg = _apply_unit_conversions(current_avg, conv_map)
        previous_avg = _apply_unit_conversions(previous_avg, conv_map)
        team_current = _apply_unit_conversions(team_current, conv_map)

    return {
        "employee_id": eff_emp or emp_id,
        "current": current,
        "previous": previous,
        "current_avg": current_avg,
        "previous_avg": previous_avg,
        "comparison": _build_radar_comparison(current, team_current, program_id),
        "trends": _build_trends_series(trend_data, aggregation, program_id),
        "performance_score": _compute_performance_score(
            current, previous, team_current, program_id
        ),
    }


def _build_radar_comparison(current: dict, team: dict, program_id: str) -> list:
    """Map SQL metric values into radar ``comparison`` entries.

    Each entry is ``{"metric", "individual", "teamAvg"}``. ``individual`` comes
    from the employee's current-window values; ``teamAvg`` comes from the
    team-average window (:func:`fetch_team_metrics` — team defined by the
    ``*_team`` SQL templates). Values are matched by ``column_key`` (exact then
    normalized). When a metric has no team value, ``teamAvg`` falls back to the
    individual value so the radar still renders. Returns an empty list when no
    SQL data exists so the radar shows a "no data" message.
    """
    if not current:
        return []

    norm_current = {normalize_metric_name(k): v for k, v in current.items()}
    team = team or {}
    norm_team = {normalize_metric_name(k): v for k, v in team.items()}
    comparison = []
    for group in get_metric_groups(program_id):
        for spec in group.kpis:
            value = _lookup_metric_value(current, norm_current, spec.column_key)
            individual = round(float(value), 2) if isinstance(value, (int, float)) else 0
            team_value = _lookup_metric_value(team, norm_team, spec.column_key)
            team_avg = (
                round(float(team_value), 2)
                if isinstance(team_value, (int, float))
                else individual  # fall back to individual when no team benchmark
            )
            comparison.append(
                {
                    "metric": spec.label or spec.column_key,
                    "individual": individual,
                    "teamAvg": team_avg,
                }
            )
    return comparison


def _build_trends_series(trend_data: dict, aggregation: str, program_id: str) -> dict:
    """Map the SQL trend windows into a ``{metric: [{x, y}, ...]}`` series.

    Builds a 4-point series per metric (oldest → newest) aligned with
    ``_TREND_WINDOW_LABELS`` from the four SQL windows for the selected
    aggregation (``{agg}_prev3`` → ``{agg}_prev2`` → ``{agg}_prev`` → ``{agg}``).
    A metric is included only when its current (newest) value is numeric; any
    missing window value is emitted as ``None``. Returns ``{}`` when no SQL data
    exists.
    """
    if not trend_data:
        return {}

    # Window keys oldest -> newest, aligned with _TREND_WINDOW_LABELS.
    window_keys = [
        f"{aggregation}_prev3",
        f"{aggregation}_prev2",
        f"{aggregation}_prev",
        aggregation,
    ]
    windows = [(trend_data.get(k) or {}) for k in window_keys]

    # Nothing to plot without a current (newest) window.
    if not windows[-1]:
        return {}

    norm_windows = [
        {normalize_metric_name(k): v for k, v in window.items()} for window in windows
    ]

    trends: dict = {}
    for group in get_metric_groups(program_id):
        for spec in group.kpis:
            label = spec.label or spec.column_key
            divisor = getattr(spec, "unit_divisor", 1.0) or 1.0

            cur_val = _lookup_metric_value(windows[-1], norm_windows[-1], spec.column_key)
            if not isinstance(cur_val, (int, float)):
                continue

            # Every included metric emits exactly one point per window so the
            # heatmap z-matrix stays rectangular; missing values become None.
            # Configured metrics are converted to their display unit (e.g. AHT
            # seconds → hours) so the trend series matches the KPI cards.
            points = []
            for i, window in enumerate(windows):
                val = _lookup_metric_value(window, norm_windows[i], spec.column_key)
                if isinstance(val, (int, float)):
                    y = round(float(val) / divisor, 3 if divisor != 1.0 else 2)
                else:
                    y = None
                points.append({"x": _TREND_WINDOW_LABELS[i], "y": y})
            trends[label] = points
    return trends


def _display_metric_norm_keys(program_id: str) -> set:
    """Normalized column keys of the DISPLAY metric groups only.

    Behaviour-skill KPIs are fetched into ``current``/``previous`` for the "View
    All Behaviors" modal, so the performance score and coaching input must be
    restricted to the display groups to preserve their prior values.
    """
    return {
        normalize_metric_name(spec.column_key)
        for group in get_metric_groups(program_id)
        for spec in group.kpis
    }


def _filter_to_display(metrics: dict, program_id: str) -> dict:
    """Drop behavior-skill KPIs, keeping only the display metric values."""
    if not metrics:
        return {}
    display_norm = _display_metric_norm_keys(program_id)
    return {
        k: v for k, v in metrics.items()
        if normalize_metric_name(k) in display_norm
    }


def _compute_performance_score(
    current: dict, previous: dict, team: dict, program_id: str
):
    """Derive a 0–100 performance score from the DISPLAY SQL KPIs (or ``"N/A"``).

    Mirrors the Individual Report's benchmark-and-trend scoring, adapted to the
    SQL data shape rather than a naive average of mismatched units:

    * **Attainment** — every KPI is scored against the TEAM benchmark, where
      *meeting or beating the team = 100* and falling short scales down
      proportionally. The direction is respected via :func:`_is_positive_metric`,
      so "lower is better" KPIs (AHT, disconnects, response time, thumbs-down)
      invert. This normalises heterogeneous units (%, hours, survey scores) onto
      one comparable 0–100 scale. A KPI with no team value falls back to its own
      absolute percentage when it is percentage-like, otherwise it is skipped.
    * **Trend** — a small ±adjustment rewards KPIs improving week-over-week and
      penalises declines (again direction-aware).

    Returns ``"N/A"`` only when no KPI can be scored.
    """
    specs = [spec for group in get_metric_groups(program_id) for spec in group.kpis]
    if not specs:
        return "N/A"

    norm_current = {normalize_metric_name(k): v for k, v in (current or {}).items()}
    norm_previous = {normalize_metric_name(k): v for k, v in (previous or {}).items()}
    norm_team = {normalize_metric_name(k): v for k, v in (team or {}).items()}

    attainment: list = []
    improving = declining = with_delta = 0

    for spec in specs:
        label = spec.label or spec.column_key
        value = _lookup_metric_value(current or {}, norm_current, spec.column_key)
        if not isinstance(value, (int, float)):
            continue
        value = float(value)
        higher_is_better = _is_positive_metric(label)

        # ── Attainment vs the team benchmark (meeting the team = 100) ──
        team_value = _lookup_metric_value(team or {}, norm_team, spec.column_key)
        goodness = None
        if isinstance(team_value, (int, float)):
            team_value = float(team_value)
            if higher_is_better and team_value > 0:
                goodness = (value / team_value) * 100
            elif not higher_is_better and value > 0:
                goodness = (team_value / value) * 100
            elif not higher_is_better and value == 0:
                goodness = 100.0  # zero of a "lower is better" KPI is ideal
        # Fallback: absolute percentage attainment when there is no team benchmark.
        if goodness is None and _looks_percentage(label) and 0 <= value <= 100:
            goodness = value if higher_is_better else (100 - value)
        if goodness is not None:
            attainment.append(max(0.0, min(100.0, goodness)))

        # ── Trend (direction-aware) ──
        prev_value = _lookup_metric_value(previous or {}, norm_previous, spec.column_key)
        if isinstance(prev_value, (int, float)):
            signed = (
                (value - float(prev_value))
                if higher_is_better
                else (float(prev_value) - value)
            )
            with_delta += 1
            if signed > 1e-9:
                improving += 1
            elif signed < -1e-9:
                declining += 1

    if not attainment:
        return "N/A"

    base = sum(attainment) / len(attainment)
    trend_adj = ((improving - declining) / with_delta) * 8 if with_delta else 0.0
    return max(0, min(100, round(base + trend_adj)))


def _fetch_goals(emp_id: str) -> list:
    """Return goal/action items for an employee (Individual Report parity).

    Mirrors the Individual Report's Goals tab, which presents a set of
    goal/action items with progress toward target. Kept as a data hook (keyed by
    ``emp_id``) so it can be swapped for a real Azure SQL query later without
    changing the Goals tab renderer.
    """
    # TODO: replace with an Azure SQL query for goals when the table is available.
    return [
        {"name": "Improve AHT", "target": 300, "current": 320, "due": "2025-11-01", "status": "in_progress"},
        {"name": "Increase FCR", "target": 85, "current": 78, "due": "2025-10-15", "status": "pending"},
        {"name": "Quality Score", "target": 95, "current": 92, "due": "2025-10-30", "status": "in_progress"},
    ]


# ---------------------------------------------------------------------------
# SQL-driven dashboard renderers (Individual Report theme, Azure SQL data)
# ---------------------------------------------------------------------------


def _render_metric_group_cards(groups, current: dict, previous: dict) -> bool:
    """Render a list of ``MetricGroup`` as KPI cards (3 per row). Returns True if any."""
    norm_current = {normalize_metric_name(k): v for k, v in (current or {}).items()}
    norm_previous = {normalize_metric_name(k): v for k, v in (previous or {}).items()}

    any_group = False
    for group in groups:
        if not group.kpis:
            continue
        any_group = True
        st.caption(f"**{group.name}**")

        for row_start in range(0, len(group.kpis), 3):
            row_specs = group.kpis[row_start:row_start + 3]
            cols = st.columns(3, gap="medium")
            for col_idx, spec in enumerate(row_specs):
                with cols[col_idx]:
                    label = spec.label or spec.column_key
                    value = _lookup_metric_value(current, norm_current, spec.column_key)
                    prev_value = _lookup_metric_value(previous, norm_previous, spec.column_key)

                    unit_suffix = getattr(spec, "unit_suffix", "") or ""
                    # Percentage KPIs render a trailing % inside the card; plain
                    # number KPIs show the value as-is (unless a custom suffix set).
                    if not unit_suffix and getattr(spec, "is_percentage", False):
                        unit_suffix = "%"
                    _converted = (getattr(spec, "unit_divisor", 1.0) or 1.0) != 1.0

                    delta = None
                    if isinstance(value, (int, float)) and isinstance(prev_value, (int, float)):
                        delta = round(value - prev_value, 2 if _converted else 1)

                    if isinstance(value, float):
                        _num = f"{value:.2f}" if _converted else f"{value:.1f}"
                        formatted = f"{_num}{unit_suffix}"
                    elif value is not None:
                        formatted = f"{value}{unit_suffix}"
                    else:
                        formatted = "N/A"
                    st.markdown(
                        render_kpi_card(
                            label, formatted, delta, _is_positive_metric(label), clickable=False
                        ),
                        unsafe_allow_html=True,
                    )
    return any_group


def _render_sql_kpi_groups(program_id, current: dict, previous: dict):
    """Render Azure SQL KPI group cards (3 per row) in the Individual Report theme.

    One ``st.caption`` per group, then ``render_kpi_card`` cards with the
    week-over-week delta (current − previous). KPIs with no SQL value render as
    "N/A" so the section layout/placeholders are preserved.
    """
    any_group = _render_metric_group_cards(get_metric_groups(program_id), current, previous)
    if not any_group:
        st.info("No KPI groups configured for this program.")


@st.dialog("More KPIs", width="large")
def render_sql_more_kpis_dialog(program_id, current: dict, previous: dict):
    """Popup showing the overflow PSO metric groups as KPI cards (no page clutter)."""
    groups = get_metric_more_groups(program_id)
    if not _render_metric_group_cards(groups, current, previous):
        st.info("No additional KPIs configured for this program.")



# ---------------------------------------------------------------------------
# Behavior-skills modal ("View All Behaviors") — parity with the Individual
# Report modal, but ONLY the Behavior Skills tab. KPIs come from rep_pivoted
# (fetched into current/previous alongside the display metrics).
# ---------------------------------------------------------------------------


def _fmt_behavior_value(value, is_percentage: bool) -> str:
    """Format a behavior KPI value for a metric card ("N/A" when missing).

    NOTE: values are shown as-is for now – the raw ``rep_pivoted`` number is
    displayed without any percentage conversion, regardless of ``is_percentage``.
    """
    if not isinstance(value, (int, float)):
        return "N/A"
    return f"{value:,.1f}"


def _fmt_behavior_delta(delta, is_percentage: bool):
    """Format a signed week-over-week delta, or ``None`` when not applicable.

    NOTE: shown as-is for now – no percentage sign is appended.
    """
    if not isinstance(delta, (int, float)) or delta == 0:
        return None
    return f"{delta:+,.1f}"


def _build_behavior_items(behavior_groups, current: dict, previous: dict) -> list:
    """Resolve behavior-skill KPIs into display items with a Strong/Focus verdict.

    Each item carries the ``header - kpi name`` label, the current value, the
    week-over-week delta and an ``is_strong`` flag. A KPI is "Strong" when it is
    holding or improving week-over-week (the direction flips for KPIs whose
    ``higher_is_better`` is ``False``); a KPI with no current value falls into
    Need Focus. Data is read from the same ``current``/``previous`` dicts the
    page already fetched (rep_pivoted), matched by ``column_key``.
    """
    norm_current = {normalize_metric_name(k): v for k, v in (current or {}).items()}
    norm_previous = {normalize_metric_name(k): v for k, v in (previous or {}).items()}

    items: list = []
    for group in behavior_groups or []:
        for spec in group.kpis:
            cur = _lookup_metric_value(current or {}, norm_current, spec.column_key)
            prev = _lookup_metric_value(previous or {}, norm_previous, spec.column_key)
            cur_num = float(cur) if isinstance(cur, (int, float)) else None
            prev_num = float(prev) if isinstance(prev, (int, float)) else None
            delta = (
                round(cur_num - prev_num, 2)
                if cur_num is not None and prev_num is not None
                else None
            )
            higher_is_better = getattr(spec, "higher_is_better", True)
            if cur_num is None:
                is_strong = False
            elif delta is None:
                is_strong = True
            elif higher_is_better:
                is_strong = delta >= 0
            else:
                is_strong = delta <= 0

            items.append(
                {
                    "label": spec.label or spec.column_key,
                    "value": cur_num,
                    "delta": delta,
                    "is_percentage": getattr(spec, "is_percentage", False),
                    "higher_is_better": higher_is_better,
                    "is_strong": is_strong,
                }
            )
    return items


def _compute_behavior_score(behavior_items: list):
    """Derive a 0–100 behaviour score from the behaviour-skill KPIs (or ``"N/A"``).

    The score is the share of tracked behaviours that are *on track* — holding or
    improving week-over-week (``is_strong``) — so it reads on the same scale as
    the Individual Report's behaviour gauge and stays consistent with the
    Strong / Focus split rendered directly beneath the speedometer. A behaviour
    with no current value counts against the score (not demonstrated). Returns
    ``"N/A"`` when no behaviours are configured.
    """
    if not behavior_items:
        return "N/A"
    strong = sum(1 for item in behavior_items if item.get("is_strong"))
    return max(0, min(100, round(strong / len(behavior_items) * 100)))


def _render_behavior_column_cards(items: list, empty_msg: str) -> None:
    """Render one Strong / Need-Focus column of behavior cards (two per row).

    Mirrors the Individual Report modal's cards: a gradient ``st.metric`` (value
    + signed delta) followed by a ``st.progress`` bar. Percentage KPIs fill the
    bar by ``value/100``; count/hour KPIs fill relative to the largest value in
    the same column so the bars stay comparable despite mixed units.
    """
    if not items:
        st.info(empty_msg)
        return
    # Largest non-% magnitude in this column, to normalize count/hour bars.
    _np_vals = [
        abs(it["value"]) for it in items
        if not it["is_percentage"] and isinstance(it["value"], (int, float))
    ]
    col_max = max(_np_vals, default=0.0)

    for i in range(0, len(items), 2):
        sub_cols = st.columns(2, gap="small")
        for col_idx in range(2):
            if i + col_idx >= len(items):
                continue
            item = items[i + col_idx]
            with sub_cols[col_idx]:
                with st.container():
                    value = item["value"]
                    if value is None:
                        st.metric(label=item["label"], value="N/A", delta=None)
                        st.progress(0)
                        continue
                    st.metric(
                        label=item["label"],
                        value=_fmt_behavior_value(value, item["is_percentage"]),
                        delta=_fmt_behavior_delta(item["delta"], item["is_percentage"]),
                        delta_color="normal" if item["higher_is_better"] else "inverse",
                    )
                    if item["is_percentage"]:
                        prog = value / 100.0
                    elif col_max > 0:
                        prog = abs(value) / col_max
                    else:
                        prog = 0.0
                    st.progress(max(0.0, min(1.0, prog)))


def _render_metrics_behaviors_modal(behavior_groups, current: dict, previous: dict) -> None:
    """Content of the metrics-page "View All Behaviors" dialog (Behavior Skills only)."""
    _focus_color = "#ef4444"
    _strong_color = "#0F9ED5"

    items = _build_behavior_items(behavior_groups, current, previous)
    needs_focus = [it for it in items if not it["is_strong"]]
    strong = [it for it in items if it["is_strong"]]

    # Advanced futuristic CSS (parity with the Individual Report modal).
    st.markdown("""
    <style>
    [data-testid="stDialog"] {
        background: linear-gradient(135deg, rgba(15, 23, 42, 0.95) 0%, rgba(30, 41, 59, 0.95) 100%) !important;
        backdrop-filter: blur(8px) saturate(120%) !important;
    }
    [data-testid="stMetric"] {
        background: linear-gradient(135deg, rgba(255,255,255,0.95), rgba(248,250,252,0.9));
        backdrop-filter: blur(12px);
        border-radius: 12px;
        padding: 0.85rem 1rem;
        border: 1px solid rgba(148, 163, 184, 0.2);
        box-shadow: 0 4px 16px rgba(0,0,0,0.06);
        transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1);
        position: relative;
        overflow: hidden;
        min-height: 118px;
        box-sizing: border-box;
    }
    [data-testid="stMetric"]::before {
        content: '';
        position: absolute;
        top: 0;
        left: -100%;
        width: 100%;
        height: 100%;
        background: linear-gradient(90deg, transparent, rgba(102, 126, 234, 0.1), transparent);
        transition: left 0.5s;
    }
    [data-testid="stMetric"]:hover::before { left: 100%; }
    [data-testid="stMetric"]:hover {
        transform: translateY(-2px);
        box-shadow: 0 8px 24px rgba(102, 126, 234, 0.15);
        border-color: rgba(102, 126, 234, 0.3);
    }
    [data-testid="stMetric"] label {
        font-size: 0.7rem !important;
        font-weight: 700 !important;
        color: #64748b !important;
        text-transform: uppercase !important;
        letter-spacing: 0.08em !important;
    }
    [data-testid="stMetric"] [data-testid="stMetricValue"] {
        font-size: 1.6rem !important;
        font-weight: 900 !important;
        background: linear-gradient(135deg, #667eea, #764ba2, #f093fb);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        background-clip: text;
    }
    .stProgress > div > div {
        background: linear-gradient(90deg, #667eea, #764ba2, #f093fb) !important;
        border-radius: 8px !important;
        box-shadow: 0 2px 8px rgba(102, 126, 234, 0.3);
        animation: pulse-glow 2s ease-in-out infinite;
    }
    @keyframes pulse-glow {
        0%, 100% { opacity: 0.9; box-shadow: 0 2px 8px rgba(102, 126, 234, 0.3); }
        50% { opacity: 1; box-shadow: 0 2px 12px rgba(240, 147, 251, 0.5); }
    }
    .stTabs [data-baseweb="tab-list"] {
        gap: 8px;
        background: rgba(255,255,255,0.05);
        padding: 0.5rem;
        border-radius: 12px;
    }
    .stTabs [data-baseweb="tab"] {
        background: transparent;
        border-radius: 8px;
        color: #94a3b8;
        font-weight: 600;
        padding: 0.5rem 1rem;
        transition: all 0.3s ease;
    }
    .stTabs [data-baseweb="tab"]:hover {
        background: rgba(102, 126, 234, 0.1);
        color: #667eea;
    }
    .stTabs [aria-selected="true"] {
        background: linear-gradient(135deg, #667eea, #764ba2) !important;
        color: white !important;
        box-shadow: 0 4px 12px rgba(102, 126, 234, 0.3);
    }
    </style>
    """, unsafe_allow_html=True)

    st.markdown("""
    <div style="text-align: center; margin-bottom: 1.5rem; padding: 1rem;
                background: linear-gradient(135deg, rgba(102, 126, 234, 0.15), rgba(240, 147, 251, 0.15));
                border-radius: 14px; border: 1.5px solid rgba(102, 126, 234, 0.3);
                box-shadow: 0 4px 16px rgba(102, 126, 234, 0.1);">
        <div style="font-size: 1.1rem; font-weight: 800; background: linear-gradient(135deg, #667eea, #f093fb);
                    -webkit-background-clip: text; -webkit-text-fill-color: transparent; margin-bottom: 0.3rem;">
            Behavior Performance Skills
        </div>
        <div style="font-size: 0.75rem; color: #64748b; font-weight: 500;">
            Week-over-week behavior analytics
        </div>
    </div>
    """, unsafe_allow_html=True)

    col1, col2, col3 = st.columns(3, gap="small")
    with col1:
        st.metric("📊 Total", len(items))
    with col2:
        st.metric("⭐ Strong", len(strong))
    with col3:
        st.metric("🔴 Need Focus", len(needs_focus))

    st.markdown("<div style='margin: 1.5rem 0'></div>", unsafe_allow_html=True)

    if not items:
        st.info("No behavior skills are configured for this program.")
        return

    tabs = st.tabs([f"Behavior Skills ({len(items)})"])
    with tabs[0]:
        # CSS: bordered gradient columns + scrollable inner via JS-injected class.
        st.markdown(f"""
        <style>
        .bhvm-col-focus {{
            border: 1.5px solid {_focus_color}40;
            border-radius: 12px;
            padding: 0.6rem;
            background: linear-gradient(180deg, {_focus_color}08 0%, transparent 100%);
        }}
        .bhvm-col-strong {{
            border: 1.5px solid {_strong_color}40;
            border-radius: 12px;
            padding: 0.6rem;
            background: linear-gradient(180deg, {_strong_color}08 0%, transparent 100%);
        }}
        .bhvm-scroll-inner {{
            overflow-y: auto !important;
            padding-right: 2px;
            max-height: 35vh;
        }}
        .bhvm-scroll-inner::-webkit-scrollbar {{
            width: 6px;
        }}
        .bhvm-col-focus::-webkit-scrollbar-thumb {{
            background: {_focus_color}50;
            border-radius: 3px;
        }}
        .bhvm-col-strong::-webkit-scrollbar-thumb {{
            background: {_strong_color}50;
            border-radius: 3px;
        }}
        .bhvm-col-focus [data-testid="stMetric"],
        .bhvm-col-strong [data-testid="stMetric"] {{
            border: 1px solid rgba(148, 163, 184, 0.25);
            border-radius: 10px;
            padding: 0.7rem 0.8rem;
            box-shadow: 0 2px 8px rgba(0,0,0,0.04);
            background: linear-gradient(135deg, rgba(255,255,255,0.97), rgba(248,250,252,0.92));
            min-height: 110px;
            box-sizing: border-box;
        }}
        .bhvm-col-focus [data-testid="stMetric"] {{
            border-left: 3px solid {_focus_color}60;
        }}
        .bhvm-col-strong [data-testid="stMetric"] {{
            border-left: 3px solid {_strong_color}60;
        }}
        </style>
        """, unsafe_allow_html=True)

        col_focus, col_strong = st.columns(2, gap="medium")

        # --- Left column: Need Focus ---
        with col_focus:
            st.markdown(
                f"<div id='bhvm-focus-marker'></div>"
                f"<div style='width:100%;padding:0.55rem 0;margin-bottom:0.6rem;"
                f"background:linear-gradient(135deg,{_focus_color}18,{_focus_color}08);"
                f"border:1.5px solid {_focus_color}35;border-radius:10px;"
                f"text-align:center;font-weight:800;font-size:0.9rem;"
                f"color:{_focus_color};letter-spacing:0.04em;"
                f"box-shadow:0 2px 8px {_focus_color}12;'>"
                f"\U0001f534 NEED FOCUS &nbsp;·&nbsp; {len(needs_focus)}</div>",
                unsafe_allow_html=True,
            )
            with st.container():
                st.markdown("<div id='bhvm-focus-scroll-start'></div>", unsafe_allow_html=True)
                _render_behavior_column_cards(needs_focus, "No declining behaviors")

        # --- Right column: Strong ---
        with col_strong:
            st.markdown(
                f"<div id='bhvm-strong-marker'></div>"
                f"<div style='width:100%;padding:0.55rem 0;margin-bottom:0.6rem;"
                f"background:linear-gradient(135deg,{_strong_color}18,{_strong_color}08);"
                f"border:1.5px solid {_strong_color}35;border-radius:10px;"
                f"text-align:center;font-weight:800;font-size:0.9rem;"
                f"color:{_strong_color};letter-spacing:0.04em;"
                f"box-shadow:0 2px 8px {_strong_color}12;'>"
                f"\U0001f7e2 STRONG &nbsp;·&nbsp; {len(strong)}</div>",
                unsafe_allow_html=True,
            )
            with st.container():
                st.markdown("<div id='bhvm-strong-scroll-start'></div>", unsafe_allow_html=True)
                _render_behavior_column_cards(strong, "No improving behaviors")

        # JS to apply the border class to each stColumn and the scroll class to
        # its inner content block (mirrors the Individual Report modal).
        components.html("""
        <script>
        const root = window.parent.document;
        function applyClasses(markerId, colCls, scrollMarkerId) {
            const marker = root.getElementById(markerId);
            if (!marker) return;
            let col = marker.closest('[data-testid="stColumn"]');
            if (col && !col.classList.contains(colCls)) {
                col.classList.add(colCls);
            }
            const scrollMarker = root.getElementById(scrollMarkerId);
            if (!scrollMarker) return;
            let block = scrollMarker.closest('[data-testid="stVerticalBlockBorderWrapper"]')
                      || scrollMarker.closest('[data-testid="stVerticalBlock"]');
            if (block && !block.classList.contains('bhvm-scroll-inner')) {
                block.classList.add('bhvm-scroll-inner');
            }
        }
        let tries = 0;
        const iv = setInterval(() => {
            applyClasses('bhvm-focus-marker', 'bhvm-col-focus', 'bhvm-focus-scroll-start');
            applyClasses('bhvm-strong-marker', 'bhvm-col-strong', 'bhvm-strong-scroll-start');
            tries++;
            if (tries > 30) clearInterval(iv);
        }, 150);
        </script>
        """, height=0)


def _render_sql_behavior_panel(report: dict, current: dict, program_id: str, mp_cfg=None):
    """Right-column panel in the Individual Report theme, driven by Azure SQL.

    Every label/icon/color and the header stat metric are loaded from the
    program JSON ``individual_metrics.behavior_panel`` config: the header stat
    (default Total Calls from the SQL ``Agent Calls`` metric), the
    performance-score speedometer and the Strong/Focus summary. The Strong/Focus
    counts and the "View All Behaviors" modal are driven by the behavior-skill
    KPIs (``individual_metrics.behavior_skills``), fetched from rep_pivoted into
    ``current``/``previous`` and split by week-over-week trend.
    """
    score = report.get("performance_score")
    previous = report.get("previous") or {}

    # Behavior-skill KPIs (rep_pivoted) drive the Strong/Focus summary + modal.
    # They always show the selected-period AVERAGE value from the DB, independent
    # of the page's avg/sum aggregation toggle.
    behavior_current = report.get("current_avg") or current
    behavior_previous = report.get("previous_avg") or previous
    behavior_groups = get_behavior_skill_groups(program_id)
    behavior_items = _build_behavior_items(behavior_groups, behavior_current, behavior_previous)
    strong_count = sum(1 for it in behavior_items if it["is_strong"])
    focus_count = len(behavior_items) - strong_count
    # Behaviour score for the speedometer (gauge is labelled "BEHAVIOR SCORE"):
    # the share of tracked behaviours holding/improving, matching the Strong /
    # Focus split shown beneath it. Falls back to the overall performance score
    # only when no behaviour KPIs are configured for the program.
    behavior_score = _compute_behavior_score(behavior_items)
    gauge_score = behavior_score if isinstance(behavior_score, (int, float)) else score

    panel = mp_cfg.behavior_panel if mp_cfg else None
    stat = panel.stat if panel else None
    stat_label = stat.label if stat else "Total Calls"
    stat_icon = stat.icon if stat else "💬"
    stat_metric = stat.metric if stat else "Agent Calls"
    show_speedometer = panel.show_speedometer if panel else True

    sf = panel.strong_focus if panel else None
    strong_label = sf.strong_label if sf else "STRONG"
    strong_color = sf.strong_color if sf else "#0F9ED5"
    focus_label = sf.focus_label if sf else "FOCUS"
    focus_color = sf.focus_color if sf else "#ef4444"
    unavailable_caption = (
        sf.unavailable_caption if sf else "Behaviour analytics are not available from Azure SQL yet."
    )

    norm_current = {normalize_metric_name(k): v for k, v in (current or {}).items()}
    stat_val = _lookup_metric_value(current or {}, norm_current, stat_metric)
    stat_display = f"{stat_val:.0f}" if isinstance(stat_val, (int, float)) else "N/A"

    st.markdown(
        f"""
<div style='background: linear-gradient(135deg, rgba(255,255,255,0.95) 0%, rgba(255,255,255,0.9) 100%);
            backdrop-filter: blur(10px); border-radius: 16px; padding: 0.85rem;
            box-shadow: 0 4px 16px rgba(0,0,0,0.08); border: 1px solid rgba(255,255,255,0.18);
            margin-top: -0.5rem; margin-bottom: 1rem;'>
    <div style='display: flex; justify-content: space-around; text-align: center;'>
        <div style='flex:1;'>
            <div style='display:inline-flex;align-items:center;justify-content:center;
                        background:linear-gradient(135deg,#0ea5e9,#a855f7);width:32px;height:32px;
                        border-radius:8px;box-shadow:0 3px 10px rgba(14,165,233,0.25);margin-bottom:0.4rem;'>
                <span style='font-size:1.1rem;'>{stat_icon}</span></div>
            <div style='font-size:0.7rem;color:#64748b;font-weight:600;text-transform:uppercase;
                        letter-spacing:0.08em;margin-bottom:0.3rem;'>{stat_label}</div>
            <div style='font-size:2.5rem;font-weight:900;background:linear-gradient(135deg,#0ea5e9,#a855f7);
                        -webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;
                        line-height:1;margin-bottom:0.3rem;'>{stat_display}</div>
        </div>
    </div>
</div>
""",
        unsafe_allow_html=True,
    )

    # Speedometer shows the BEHAVIOR SCORE (share of behaviours holding/improving),
    # matching the gauge's label and the Strong/Focus split rendered below it.
    if show_speedometer:
        if isinstance(gauge_score, (int, float)):
            gauge_html = create_speedometer_gauge({"_": {"score": 0}}, overall_score=float(gauge_score))
            if gauge_html:
                components.html(gauge_html, height=300)
            else:
                st.info("No data available for Behavior Score gauge.")
        else:
            st.info("No data available for Behavior Score gauge.")

    _strong_display = str(strong_count) if behavior_items else "N/A"
    _focus_display = str(focus_count) if behavior_items else "N/A"
    st.markdown(
        f"""
<div style='margin-top: 1rem; padding: 1rem; background: white; border-radius: 12px; box-shadow: 0 2px 8px rgba(0,0,0,0.05);'>
    <div style='display: flex; justify-content: space-around; text-align: center;'>
        <div>
            <div style='font-size: 1.75rem; font-weight: 800; color: {strong_color};'>{_strong_display}</div>
            <div style='font-size: 0.7rem; color: #64748b; font-weight: 600;'>{strong_label}</div>
        </div>
        <div style='width: 1px; background: #e2e8f0;'></div>
        <div>
            <div style='font-size: 1.75rem; font-weight: 800; color: {focus_color};'>{_focus_display}</div>
            <div style='font-size: 0.7rem; color: #64748b; font-weight: 600;'>{focus_label}</div>
        </div>
    </div>
</div>
""",
        unsafe_allow_html=True,
    )

    # "View All Behaviors" launcher → opens the Behavior Skills modal. Only shown
    # when behavior-skill KPIs are configured for the program.
    if behavior_items:
        st.markdown("<div style='margin-top: 0.75rem'></div>", unsafe_allow_html=True)
        if st.button(
            "✨ View All Behaviors",
            key="view_all_behaviors_metrics_btn",
            width="stretch",
        ):
            st.session_state["show_metrics_behaviors_modal"] = True

        @st.dialog("Performance Analytics", width="large")
        def _show_metrics_behaviors_modal():
            _render_metrics_behaviors_modal(behavior_groups, behavior_current, behavior_previous)

        if st.session_state.get("show_metrics_behaviors_modal", False):
            _show_metrics_behaviors_modal()
            st.session_state["show_metrics_behaviors_modal"] = False
    elif unavailable_caption:
        st.caption(unavailable_caption)


def _get_cached_coaching(emp_id, week, program_id, current: dict, previous: dict, mp_cfg=None):
    """Generate (and session-cache) GPT coaching insights for a selection.

    Cached per (program, employee, week) so the GPT endpoint is hit at most once
    per selection. The cached value may be ``None`` (GPT unavailable / failed),
    which still suppresses repeated calls until the selection changes.
    """
    cache_key = f"{program_id}|{emp_id}|{week}"
    cached = st.session_state.get("_coaching_cache")
    if cached and cached.get("key") == cache_key:
        return cached.get("data")

    # Coaching considers the DISPLAY metrics plus any metrics named in the
    # program's ``coaching_priority`` themes (e.g. sales / behaviour KPIs that
    # live in the behavior-skill groups and are absent from the display cards).
    # Everything else in current/previous (the modal-only behaviour KPIs) is
    # excluded from the prompt.
    priority = getattr(mp_cfg, "coaching_priority", None) or []
    keep = set(_display_metric_norm_keys(program_id))
    for _item in priority:
        for _m in getattr(_item, "metrics", None) or []:
            keep.add(normalize_metric_name(_m))

    def _select(metrics: dict) -> dict:
        return {
            k: v for k, v in (metrics or {}).items()
            if normalize_metric_name(k) in keep
        }

    _cur = _select(current)
    _prev = _select(previous)
    # Ordered priority themes (most important first) steer the summary/tips/risks.
    _priority = [
        {"area": (_item.label or _item.key), "metrics": list(getattr(_item, "metrics", None) or [])}
        for _item in priority
    ]
    # Cross-session cached so a fresh session within the TTL reuses the same GPT
    # result instead of re-paying the reasoning-model latency on first paint.
    data = _cached_coaching_insights(program_id, emp_id, week, _cur, _prev, _priority)
    st.session_state["_coaching_cache"] = {"key": cache_key, "data": data}
    return data


# ---------------------------------------------------------------------------
# Tab renderers (Individual Report theme, SQL/GPT/placeholder driven)
# ---------------------------------------------------------------------------


def _render_metrics_overview_tab(report: dict, current: dict, previous: dict, emp_id, week, program_id, mp_cfg=None):
    """Overview tab — config-driven sections (summary / risks / coaching).

    Section order and presence come from ``individual_metrics.overview_sections``.
    The summary is shown as the Overview body text; the Performance Radar (honouring
    ``individual_metrics.radar_metrics``) renders inside the "Areas Needing
    Attention" section, above the attention points. GPT coaching insights are
    fetched at most once and shared across the summary, risks and coaching sections.
    """
    st.markdown("### Overview")

    # Config-driven section order. Fallback: summary as the Overview body, then
    # the radar + areas needing attention, then coaching tips.
    if mp_cfg and mp_cfg.overview_sections:
        section_specs = [(s.type, s.label) for s in mp_cfg.overview_sections]
    else:
        section_specs = [("summary", ""), ("risks", ""), ("coaching", "")]

    # When a dedicated summary section is present it owns the GPT summary; the
    # coaching section then renders only the tips (older configs without a
    # summary section keep the summary inline in coaching for compatibility).
    summary_has_own_section = any(t == "summary" for t, _ in section_specs)

    radar_metrics = (mp_cfg.radar_metrics if mp_cfg else None) or None

    # Coaching insights are fetched lazily + cached so coaching + risks reuse them.
    _insights_box: dict = {}

    def _insights():
        if "data" not in _insights_box:
            _insights_box["data"] = _get_cached_coaching(emp_id, week, program_id, current, previous, mp_cfg)
        return _insights_box["data"]

    def _render_radar():
        radar_fig = create_performance_radar(report, radar_metrics=radar_metrics)
        if radar_fig:
            st.plotly_chart(radar_fig, width="stretch")
        else:
            st.info("No data available for Performance Radar.")

    for sec_type, sec_label in section_specs:
        if sec_type == "summary":
            # "Overview" is the tab heading above; the summary is its body text
            # (no separate sub-heading, mirroring the Individual Report).
            insights = _insights()
            summary = insights.get("summary") if insights else None
            if summary:
                st.info(summary)

        elif sec_type == "radar":
            _render_radar()

        elif sec_type == "risks":
            # "Areas Needing Attention": the Performance Radar first, then the
            # specific attention points listed below it.
            st.markdown(f"### {sec_label or 'Areas Needing Attention'}")
            _render_radar()
            insights = _insights()
            risks = insights.get("risks") if insights else None
            if risks:
                # Clone the Individual Report style: render as a "Key Improvement
                # Areas" bullet list instead of yellow warning callouts.
                render_key_improvements_section(list(risks))
            else:
                st.info("No specific areas needing attention.")

        elif sec_type == "coaching":
            insights = _insights()
            st.markdown(f"### {sec_label or '💡 Coaching Recommendations'}")
            tips = insights.get("tips") if insights else None
            # A dedicated summary section owns the GPT summary; without one, keep
            # showing the summary here for backward compatibility.
            show_inline_summary = (
                not summary_has_own_section and bool(insights and insights.get("summary"))
            )
            if show_inline_summary:
                st.info(insights["summary"])
            if tips:
                _render_coaching_tips(tips, None)
            elif not show_inline_summary:
                st.info("No coaching recommendations available.")


def _render_metrics_trends_tab(report: dict, mp_cfg=None):
    """Trends tab — heatmap + per-metric line charts from the SQL trend series.

    Both the heatmap and the per-metric line charts honour
    ``individual_metrics.trend_metrics`` (selection, order and labels) when set,
    and fall back to every available trend metric otherwise.
    """
    st.markdown("### Trend Analysis")

    trend_metrics = (mp_cfg.trend_metrics if mp_cfg else None) or None
    heatmap_fig = create_trend_heatmap(report, trend_metrics=trend_metrics, trends_field="trends")
    if heatmap_fig:
        st.plotly_chart(heatmap_fig, width="stretch")
    else:
        st.info("No data available for Performance Heatmap.")

    all_trends = report.get("trends", {})
    if not all_trends:
        st.info("No trend data available.")
        return

    # Ordered (label, points) list: config-driven when trend_metrics is set,
    # otherwise every available trend metric in its natural order.
    if trend_metrics:
        ordered = [
            (tm.label or tm.metric, all_trends[tm.metric])
            for tm in trend_metrics
            if all_trends.get(tm.metric)
        ]
    else:
        ordered = list(all_trends.items())

    for idx, (metric_name, points) in enumerate(ordered):
        points = [p for p in points if p.get("y") is not None]
        df = pd.DataFrame(points)
        if df.empty or "y" not in df.columns:
            continue

        velocity = calculate_trend_velocity(points)
        velocity_emoji = "⬆️" if velocity == "improving" else "⬇️" if velocity == "declining" else "➡️"
        st.markdown(f"#### {metric_name} {velocity_emoji}")

        line_color = get_trend_color(idx)
        fig = go.Figure()
        fig.add_trace(
            go.Scatter(
                x=df["x"],
                y=df["y"],
                fill="tozeroy",
                fillcolor=(
                    f"rgba({int(line_color[1:3], 16)}, {int(line_color[3:5], 16)}, "
                    f"{int(line_color[5:7], 16)}, 0.1)"
                ),
                line=dict(width=0),
                showlegend=False,
                hoverinfo="skip",
            )
        )
        fig.add_trace(
            go.Scatter(
                x=df["x"],
                y=df["y"],
                mode="lines+markers",
                name=metric_name,
                line=dict(color=line_color, width=3, shape="spline", smoothing=0.3),
                marker=dict(
                    size=10,
                    color=line_color,
                    line=dict(color="white", width=2),
                    symbol="circle",
                ),
                hovertemplate="<b>%{x}</b><br>Value: %{y}<br><extra></extra>",
            )
        )
        fig.update_layout(
            height=350,
            plot_bgcolor="rgba(248, 250, 252, 0.5)",
            paper_bgcolor="white",
            font=dict(
                family="Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
                size=12,
                color="#334155",
            ),
            xaxis=dict(
                title=dict(text="Period", font=dict(size=13, color="#475569", weight=600)),
                showgrid=True,
                gridwidth=1,
                gridcolor="rgba(226, 232, 240, 0.8)",
                showline=True,
                linewidth=1,
                linecolor="#e2e8f0",
                tickfont=dict(size=11, color="#64748b"),
            ),
            yaxis=dict(
                title=dict(text="Value", font=dict(size=13, color="#475569", weight=600)),
                showgrid=True,
                gridwidth=1,
                gridcolor="rgba(226, 232, 240, 0.8)",
                showline=True,
                linewidth=1,
                linecolor="#e2e8f0",
                tickfont=dict(size=11, color="#64748b"),
                zeroline=True,
                zerolinewidth=1,
                zerolinecolor="rgba(148, 163, 184, 0.3)",
            ),
            hovermode="x unified",
            hoverlabel=dict(
                bgcolor="white",
                font_size=12,
                font_family="Inter, sans-serif",
                bordercolor=line_color,
            ),
            margin=dict(l=60, r=30, t=30, b=50),
            showlegend=False,
        )
        st.plotly_chart(fig, width="stretch")


def _render_metrics_goals_tab(emp_id: str):
    """Goals tab — driven by ``_fetch_goals`` (SQL-ready, placeholder for now)."""
    st.markdown("### Goals & Action Items")

    goals = _fetch_goals(emp_id)
    if not goals:
        st.info("No goals available yet.")
        return

    for goal in goals:
        target = goal.get("target") or 0
        current = goal.get("current") or 0
        progress = (current / target) * 100 if target else 0
        status = goal.get("status", "pending")
        status_color = (
            "#3498db" if status == "in_progress" else "#f39c12" if status == "pending" else "#27ae60"
        )
        with st.container(border=True):
            col1, col2 = st.columns([3, 1])
            with col1:
                st.markdown(f"**{goal.get('name', 'Goal')}**")
                st.caption(
                    f"Target: {target} | Current: {current} | Due: {goal.get('due', 'N/A')}"
                )
            with col2:
                status_label = status.replace("_", " ").title()
                st.markdown(
                    f"<span style='background: {status_color}; color: white; padding: 2px 8px; "
                    f"border-radius: 4px; font-size: 0.8em;'>{status_label}</span>",
                    unsafe_allow_html=True,
                )
            st.progress(min(progress / 100, 1.0))


def _render_metrics_notes_tab(emp_id: str):
    """Notes tab — coaching notes from the notes store (DB-backed, no JSON)."""
    st.markdown("### Coaching Notes")

    author = st.session_state.get("user_id", "Unknown")
    author_name = st.session_state.get("user_name", "")
    author_role = st.session_state.get("user_role", "")
    week = st.session_state.get("selected_week", get_selected_week())

    clear_key = f"_clear_metric_note_{emp_id}"
    if st.session_state.pop(clear_key, False):
        st.session_state[f"metric_note_input_{emp_id}"] = ""

    new_note = st.text_area(
        "Add a Note",
        height=100,
        placeholder="Enter your coaching notes here...",
        key=f"metric_note_input_{emp_id}",
    )
    if st.button("💾 Save Note", key=f"save_metric_note_{emp_id}"):
        if new_note and new_note.strip():
            add_note(
                emp_id,
                author,
                new_note.strip(),
                week=week,
                author_name=author_name,
                author_role=author_role,
                namespace=_METRICS_NOTES_NAMESPACE,
            )
            log_audit_event(
                "add_note",
                author,
                f"Employee: {emp_id} | Week: {week} | Note: {new_note.strip()[:200]}",
            )
            st.session_state[clear_key] = True
            st.success("✅ Note saved successfully!")
            st.rerun()
        else:
            st.warning("Please enter a note before saving.")

    st.markdown("**📅 Recent Notes**")
    notes = get_notes(emp_id, week=week, namespace=_METRICS_NOTES_NAMESPACE)
    if not notes:
        st.info("No coaching notes yet. Add one above!")

    for note in notes:
        editing_key = f"editing_metric_note_{note['id']}"
        is_editing = st.session_state.get(editing_key, False)

        with st.container(border=True):
            if is_editing:
                edited_text = st.text_area(
                    "Edit note:",
                    value=note["note"],
                    key=f"edit_metric_text_{note['id']}",
                    height=80,
                )
                col_save, col_cancel = st.columns(2)
                with col_save:
                    if st.button("✅ Save", key=f"save_metric_edit_{note['id']}"):
                        if edited_text and edited_text.strip():
                            edit_note(emp_id, note["id"], author, edited_text.strip(), namespace=_METRICS_NOTES_NAMESPACE)
                            log_audit_event(
                                "edit_note",
                                author,
                                f"Employee: {emp_id} | Week: {week} | Note ID: {note['id']}",
                            )
                            st.session_state[editing_key] = False
                            st.rerun()
                with col_cancel:
                    if st.button("❌ Cancel", key=f"cancel_metric_edit_{note['id']}"):
                        st.session_state[editing_key] = False
                        st.rerun()
            else:
                col_text, col_actions = st.columns([8, 2])
                with col_text:
                    _display_name = note.get("author_name") or note["author"]
                    _display_role = note.get("author_role", "")
                    _role_tag = f" ({_display_role})" if _display_role else ""
                    st.caption(f"{note['date']} — {_display_name}{_role_tag}")
                    st.write(note["note"])
                with col_actions:
                    if note["author"] == author:
                        btn_cols = st.columns(2)
                        with btn_cols[0]:
                            if st.button("✏️", key=f"edit_metric_note_{note['id']}", help="Edit this note"):
                                st.session_state[editing_key] = True
                                st.rerun()
                        with btn_cols[1]:
                            if st.button("🗑️", key=f"del_metric_note_{note['id']}", help="Delete this note"):
                                hide_note(emp_id, note["id"], author, namespace=_METRICS_NOTES_NAMESPACE)
                                log_audit_event(
                                    "delete_note",
                                    author,
                                    f"Employee: {emp_id} | Week: {week} | Note ID: {note['id']}",
                                )
                                st.rerun()


# ---------------------------------------------------------------------------
# Retained KPI-card renderer (NOT shown for now — KPI groups omitted per spec)
# ---------------------------------------------------------------------------


def _render_azure_sql_metrics(emp_id: str, days_back: int = 7, aggregation: str = "avg", end_date: Optional[datetime] = None, program_id: str = DEFAULT_PROGRAM):
    """Render Azure SQL KPI cards for one employee with week-over-week trend deltas.

    Retained for future reuse. The cloned layout intentionally omits KPI groups
    for now, so this is not called by ``render_individual_performance_metrics_view``.
    """
    eff_emp = _effective_emp_id(emp_id)

    if not eff_emp:
        st.info("No employee selected")
        return

    if end_date is None:
        end_date = datetime.now()

    try:
        # Adjust days_back so the range is inclusive of the end_date.
        adjusted_days = max(1, days_back - 1) if isinstance(days_back, int) else days_back

        # Load current + previous-week metrics once; toggling agg reuses session cache.
        trend_data = _get_cached_metrics(eff_emp, end_date, adjusted_days, program_id)
        prev_key = "avg_prev" if aggregation == "avg" else "sum_prev"
        current_metrics = trend_data.get(aggregation, {}) if trend_data else {}
        previous_metrics = trend_data.get(prev_key, {}) if trend_data else {}

        if not current_metrics:
            st.info("No metrics available for the selected employee and period.")
            return

        # Normalized indexes so returned values map to display labels despite minor naming diffs.
        norm_current = {normalize_metric_name(k): v for k, v in current_metrics.items()}
        norm_previous = {normalize_metric_name(k): v for k, v in previous_metrics.items()}

        # Ordered metric groups from the program config (declared order preserved).
        for group in get_metric_groups(program_id):
            if not group.kpis:
                continue

            st.caption(f"**{group.name} Metrics**")

            for row_start in range(0, len(group.kpis), _CARDS_PER_ROW):
                row_specs = group.kpis[row_start:row_start + _CARDS_PER_ROW]
                cols = st.columns(_CARDS_PER_ROW, gap="medium")

                for col_idx, spec in enumerate(row_specs):
                    with cols[col_idx]:
                        label = spec.label or spec.column_key
                        value = _lookup_metric_value(current_metrics, norm_current, spec.column_key)
                        prev_value = _lookup_metric_value(previous_metrics, norm_previous, spec.column_key)

                        # Week-over-week delta: current − previous period value.
                        delta = None
                        if isinstance(value, (int, float)) and isinstance(prev_value, (int, float)):
                            delta = round(value - prev_value, 1)

                        formatted_value = (
                            f"{value:.1f}" if isinstance(value, float)
                            else (str(value) if value is not None else "N/A")
                        )
                        card_html = render_kpi_card(
                            label,
                            formatted_value,
                            delta,
                            _is_positive_metric(label),
                            clickable=False,
                        )
                        st.markdown(card_html, unsafe_allow_html=True)
    except Exception as e:
        logger.error(f"Error rendering Azure SQL metrics: {e}")
        st.error(f"Unable to fetch Azure SQL metrics: {str(e)}")


def render_individual_performance_metrics_view(emp_names: dict, selected_emp_id: Optional[str], selected_emp_name: Optional[str], metric_agg: str = "Average"):
    """Render Individual Performance Metrics for the single selected agent, from Azure SQL.

    Uses the Individual Report visual theme (KPI cards, behaviour-panel styling,
    Performance Radar, Overview/Trends/Goals/Notes tabs) but every value is sourced
    from Azure SQL for the selected employee + week:

      * Left column  — KPI group cards (Resolve / Efficiency / Quality) with
        week-over-week deltas, from ``individual_metrics`` + ``individual_metric_queries``.
      * Right column — performance-score speedometer + behaviour placeholders (N/A).
      * Overview tab — runtime Performance Radar + GPT coaching/risks.
      * Trends tab   — heatmap + per-metric line charts from the SQL trend series.
      * Notes tab    — coaching notes (DB-backed).

    The two SQL windows are fetched in parallel; missing data renders as "N/A".
    """
    logger.info("Rendering individual performance metrics view (Azure SQL)")

    if not selected_emp_id or not selected_emp_name:
        st.info("👈 Select an employee in the sidebar to view individual performance metrics.")
        return

    # Map the dropdown POSITION to a stand-in agent id (position % 7) so each
    # selection demos a real agent's Azure SQL data. The selected NAME is still
    # shown in the header; the mapped agent id becomes the Employee ID shown in
    # the caption and used for every SQL query. When the mapping is disabled the
    # real dropdown-selected id is used instead.
    if _HARDCODE_EMP_ID:
        _position = _dropdown_position(emp_names, selected_emp_name)
        eff_emp = _mapped_agent_id(_position) or _effective_emp_id(selected_emp_id)
    else:
        eff_emp = _effective_emp_id(selected_emp_id)
    # Coaching notes are PERSON-scoped and must follow the actually-selected
    # employee, never the stand-in SQL agent id: using ``eff_emp`` for notes
    # would file every employee's notes under the same id, mix them together,
    # and diverge from the Individual Report page. ``selected_emp_id`` is always
    # set here (guarded above).
    notes_emp = (selected_emp_id or "").strip() or eff_emp
    program_id = st.session_state.get("selected_program", DEFAULT_PROGRAM)
    program_cfg = load_program_config(program_id)
    L = program_cfg.get_label
    mp = _metrics_page_cfg(program_cfg)
    header_cfg = mp.header if mp else None

    week = st.session_state.get("selected_week", get_selected_week())
    end_date = _week_to_end_date(week)
    aggregation = _agg_key(metric_agg)

    log_audit_event(
        "view_metrics",
        st.session_state.get("user_id", "Unknown"),
        f"Employee: {eff_emp} | Week: {week}",
    )

    # ── Fetch all KPI data from Azure SQL (current + previous week, in parallel) ──
    with st.spinner("Loading metrics from Azure SQL…"):
        report = fetch_metrics_report(
            eff_emp, program_id, end_date, days_back=7, aggregation=aggregation
        )

    current = report.get("current", {})
    previous = report.get("previous", {})
    score = report.get("performance_score", "N/A")

    # ========== HEADER (Individual Report theme) ==========
    col1, col2 = st.columns([3, 1])
    with col1:
        st.markdown(f"## {selected_emp_name}")
        _caption_tmpl = (
            header_cfg.title_caption if header_cfg
            else "Week: {week} | Employee ID: {emp_id} | Program: {program_id}"
        )
        st.caption(
            _caption_tmpl.format_map(
                _SafeFormatDict(week=week, emp_id=eff_emp, program_id=program_id)
            )
        )
    with col2:
        _score_label = (
            header_cfg.performance_score_label if header_cfg
            else L("individual", "performance_score_label", "Performance Score")
        )
        st.metric(_score_label, f"{score}/100")

    st.markdown("---")

    if not current:
        st.warning(
            f"No Azure SQL metrics found for employee {eff_emp} in the week ending {week}. "
            "Showing the dashboard layout with N/A placeholders."
        )

    # ========== PERFORMANCE DASHBOARD ==========
    _dashboard_label = (
        header_cfg.dashboard_label if header_cfg
        else L("individual", "performance_dashboard", "Performance Dashboard")
    )
    _has_more_kpis = bool(get_metric_more_groups(program_id))
    if _has_more_kpis:
        _hcol, _icol = st.columns([0.92, 0.08])
        with _hcol:
            st.markdown(f"### {_dashboard_label}")
        with _icol:
            if st.button("⛶", key="metrics_more_kpis_btn", help="View more KPIs"):
                st.session_state["_metrics_more_kpis_open"] = True
    else:
        st.markdown(f"### {_dashboard_label}")

    col_kpis, col_speedometer = st.columns([2.3, 1])

    # ===== LEFT COLUMN: SQL KPI group cards (Individual Report styling) =====
    with col_kpis:
        _render_sql_kpi_groups(program_id, current, previous)

    # ===== RIGHT COLUMN: SQL performance-score speedometer + behaviour placeholders =====
    with col_speedometer:
        _render_sql_behavior_panel(report, current, program_id, mp)

    # ===== "More KPIs" popup (overflow PSO metric groups) =====
    if _has_more_kpis and st.session_state.get("_metrics_more_kpis_open"):
        st.session_state["_metrics_more_kpis_open"] = False
        render_sql_more_kpis_dialog(program_id, current, previous)


    # ========== TABS (Individual Report theme — radar/coaching/trends/notes) ==========
    _tab_defs = {
        "overview": {"label_key": "overview_tab", "default": "Overview"},
        "trends":   {"label_key": "trends_tab",   "default": "Trends"},
        "goals":    {"label_key": "goals_tab",     "default": "Goals"},
        "notes":    {"label_key": "notes_tab",     "default": "Notes"},
    }
    _enabled_tabs = (
        (mp.tabs if mp and mp.tabs else None)
        or (program_cfg.individual_page.tabs if program_cfg.individual_page else None)
        or ["overview", "trends", "goals", "notes"]
    )
    _enabled_tabs = [t for t in _enabled_tabs if t in _tab_defs]
    _tab_labels = [
        L("individual", _tab_defs[t]["label_key"], _tab_defs[t]["default"])
        for t in _enabled_tabs
    ]
    _tabs = st.tabs(_tab_labels)

    _tab_renderers = {
        "overview": lambda: _render_metrics_overview_tab(report, current, previous, eff_emp, week, program_id, mp),
        "trends":   lambda: _render_metrics_trends_tab(report, mp),
        "goals":    lambda: _render_metrics_goals_tab(eff_emp),
        "notes":    lambda: _render_metrics_notes_tab(notes_emp),
    }

    for idx, tab_key in enumerate(_enabled_tabs):
        with _tabs[idx]:
            renderer = _tab_renderers.get(tab_key)
            if renderer:
                renderer()
