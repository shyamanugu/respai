"""
services/azure_sql_query.py — Application-specific Azure SQL query helpers.
===========================================================================

Provides employee metric queries against the application Azure SQL connection.
"""

from __future__ import annotations
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional
from datetime import datetime, timedelta

from backend.config.logging import get_logger
from backend.services.azure_sql import (
    acquire_connection,
    discard_connection,
    is_configured,
    release_connection,
)

logger = get_logger(__name__)

# Max number of result rows logged per query (query + results are always logged
# for the individual metrics page; kept bounded so a large result set can't
# flood the log).
_LOG_ROW_CAP = 50

# Upper bound on the number of Azure SQL queries run concurrently during a single
# page load. MARS is not enabled, so every in-flight query needs its own
# connection; each pool worker owns one reused connection, so this also bounds
# the number of physical connections opened at once. Raising it fires more
# queries in parallel (lower latency) at the cost of more simultaneous
# connections. Override with the APP_METRIC_MAX_WORKERS environment variable.
_MAX_QUERY_WORKERS: int = max(1, int(os.getenv("APP_METRIC_MAX_WORKERS", "8")))

# Date column used to window queries across the (assumed shared) schema.
DATE_COLUMN = "Timeframe"

# Default named query templates. Used when a program JSON has no
# ``individual_metric_queries`` section. Placeholders are filled at runtime:
#   {employee_ids} {start_date} {end_date} {metric_keys}
# Each query must SELECT EmployeeID, descriptor, AVG, SUM in that column order.
_DEFAULT_QUERIES: Dict[str, str] = {
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

# Default ordered metric groups (group_name -> [column_key, ...]); all read
# from the default ``rep_pivoted`` query. Order is preserved in the UI.
_DEFAULT_GROUP_DEFS = [
    (
        "Resolve",
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
        [
            "VXS Overall Rep",
            "Thumbs Up %",
            "Thumbs Down %",
        ],
    ),
]


def _normalize_metric_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").lower())


def _norm_map_for(metrics: List[str]) -> Dict[str, str]:
    """Build a normalized-key → canonical column_key map for matching DB values."""
    return {_normalize_metric_name(m): m for m in metrics}


def _canonical_metric_name(metric_desc: str, norm_map: Dict[str, str]) -> Optional[str]:
    """Return the canonical ``column_key`` for a DB descriptor via exact/near matching."""
    if not metric_desc:
        return None

    norm_desc = _normalize_metric_name(metric_desc)
    exact = norm_map.get(norm_desc)
    if exact:
        return exact

    # Near-match fallback: handles minor punctuation/spacing/name variants.
    for norm_metric, metric in norm_map.items():
        if norm_metric and (norm_metric in norm_desc or norm_desc in norm_metric):
            return metric
    return None


# ---------------------------------------------------------------------------
# Metric group / query resolution (ordered, program-aware)
# ---------------------------------------------------------------------------

_default_groups_cache = None
_metric_groups_cache: Dict[str, list] = {}
_metric_more_groups_cache: Dict[str, list] = {}
_metric_queries_cache: Dict[str, Dict[str, str]] = {}
_behavior_skill_groups_cache: Dict[str, list] = {}


def _default_metric_groups() -> list:
    """Build (and cache) the default ordered metric groups as MetricGroup objects."""
    global _default_groups_cache
    if _default_groups_cache is None:
        from backend.config.programs import MetricGroup, MetricSpec

        _default_groups_cache = [
            MetricGroup(name=name, kpis=[MetricSpec(column_key=k, label=k) for k in keys])
            for name, keys in _DEFAULT_GROUP_DEFS
        ]
    return _default_groups_cache


def get_metric_groups(program_id: Optional[str] = None) -> list:
    """Return the ordered list of ``MetricGroup`` for a program (config or defaults)."""
    cache_key = program_id or "__default__"
    if cache_key in _metric_groups_cache:
        return _metric_groups_cache[cache_key]

    groups = _default_metric_groups()
    if program_id:
        try:
            from backend.config.programs import load_program_config

            cfg = load_program_config(program_id)
            if cfg.individual_metric_groups:
                groups = cfg.individual_metric_groups
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Using default metric groups for program '%s': %s", program_id, exc)

    _metric_groups_cache[cache_key] = groups
    return groups


def get_behavior_skill_groups(program_id: Optional[str] = None) -> list:
    """Return the ordered behavior-skill ``MetricGroup`` list for a program.

    These KPIs power the metrics-page "View All Behaviors" modal. They are
    fetched alongside the display metric groups (they share the ``rep_pivoted``
    source, so they merge into the same scan) but are never rendered as KPI
    cards. Returns an empty list when the program has none configured.
    """
    cache_key = program_id or "__default__"
    if cache_key in _behavior_skill_groups_cache:
        return _behavior_skill_groups_cache[cache_key]

    groups: list = []
    if program_id:
        try:
            from backend.config.programs import load_program_config

            cfg = load_program_config(program_id)
            groups = cfg.behavior_skill_groups or []
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("No behavior-skill groups for program '%s': %s", program_id, exc)

    _behavior_skill_groups_cache[cache_key] = groups
    return groups


def get_metric_more_groups(program_id: Optional[str] = None) -> list:
    """Return the overflow ``MetricGroup`` list shown in the metrics "More KPIs" popup.

    These read from the same ``rep_pivoted`` source as the display groups (so
    they merge into the same employee scan) but are never rendered as top-level
    KPI cards. Returns an empty list when the program configures none.
    """
    cache_key = program_id or "__default__"
    if cache_key in _metric_more_groups_cache:
        return _metric_more_groups_cache[cache_key]

    groups: list = []
    if program_id:
        try:
            from backend.config.programs import load_program_config

            cfg = load_program_config(program_id)
            groups = cfg.individual_metric_more_groups or []
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("No overflow metric groups for program '%s': %s", program_id, exc)

    _metric_more_groups_cache[cache_key] = groups
    return groups


def _individual_fetch_groups(program_id: Optional[str] = None) -> list:
    """Display metric groups plus overflow + behavior-skill groups for the fetch.

    The employee (individual) scan pulls all three so both the "More KPIs" popup
    and the "View All Behaviors" modal read their values from the same response.
    The team benchmark keeps using only the display groups (see
    :func:`get_metric_groups`) to stay lean.
    """
    return (
        list(get_metric_groups(program_id))
        + list(get_metric_more_groups(program_id))
        + list(get_behavior_skill_groups(program_id))
    )


def get_metric_queries(program_id: Optional[str] = None) -> Dict[str, str]:
    """Return ``{query_name: sql_template}`` for a program (config merged over defaults)."""
    cache_key = program_id or "__default__"
    if cache_key in _metric_queries_cache:
        return _metric_queries_cache[cache_key]

    queries = dict(_DEFAULT_QUERIES)
    if program_id:
        try:
            from backend.config.programs import load_program_config

            cfg = load_program_config(program_id)
            if cfg.individual_metric_queries:
                queries.update(cfg.individual_metric_queries)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Using default metric queries for program '%s': %s", program_id, exc)

    _metric_queries_cache[cache_key] = queries
    return queries


def get_metric_categories(program_id: Optional[str] = None) -> Dict[str, List[str]]:
    """Return ordered ``{group_name: [display_label, ...]}`` (label or column_key)."""
    return {
        g.name: [(s.label or s.column_key) for s in g.kpis]
        for g in get_metric_groups(program_id)
    }


def _format_rows_table(columns: List[str], rows: list, limit: int = 2) -> str:
    """Render the top *limit* rows as a fixed-width table string for logs."""
    if not columns:
        return "(no columns)"
    sample = rows[:limit]
    widths = [len(str(c)) for c in columns]
    for row in sample:
        for i, val in enumerate(row):
            widths[i] = max(widths[i], len(str(val)))
    sep = " | "
    header = sep.join(str(c).ljust(widths[i]) for i, c in enumerate(columns))
    divider = "-+-".join("-" * w for w in widths)
    lines = [header, divider]
    for row in sample:
        lines.append(sep.join(str(v).ljust(widths[i]) for i, v in enumerate(row)))
    if not sample:
        lines.append("(0 rows)")
    return "\n".join(lines)


def _run_metric_query(conn, query: str, name: str = "metric") -> list:
    """Run a query on its own cursor, fully drain results, then close the cursor.

    Always logs the SQL sent to Azure SQL, the row count, and the returned rows
    (up to ``_LOG_ROW_CAP``) as a table. Using a dedicated cursor per query and
    materializing the rows avoids the ODBC "Connection is busy with results for
    another command" error that occurs when a second statement is executed
    before the previous result set is fully consumed (MARS is not enabled).
    """
    cursor = conn.cursor()
    try:
        logger.info("Executing query '%s':\n%s", name, query)
        cursor.execute(query)
        rows = list(cursor.fetchall())
        columns = [col[0] for col in cursor.description] if cursor.description else []
        logger.info(
            "Query '%s' returned %d rows (showing up to %d):\n%s",
            name, len(rows), _LOG_ROW_CAP,
            _format_rows_table(columns, rows, _LOG_ROW_CAP),
        )
        return rows
    finally:
        cursor.close()


def _build_query(template: str, employee_id: str, keys: List[str], start_date, end_date) -> str:
    """Inject runtime variables into a named SQL template (single employee)."""
    safe_emp = "'" + str(employee_id).replace("'", "''") + "'"
    key_list = ",".join("'" + str(k).replace("'", "''") + "'" for k in keys)
    return template.format(
        employee_id=safe_emp,
        employee_ids=safe_emp,
        metric_keys=key_list,
        start_date=start_date.strftime("%Y-%m-%d"),
        end_date=end_date.strftime("%Y-%m-%d"),
    )


# Matches the "<date column> BETWEEN '{start_date}' AND '{end_date}'" filter in a
# template so the window can be widened and bucketed in a single scan.
_DATE_FILTER_RE = re.compile(
    r"(?P<col>\[[^\]]+\]|\w+)\s+BETWEEN\s+'\{start_date\}'\s+AND\s+'\{end_date\}'",
    re.IGNORECASE,
)


def _build_windowed_query(
    template: str, employee_id: str, keys: List[str], bounds: List[tuple]
) -> Optional[str]:
    """Build ONE query returning every trend window in a single scan, or ``None``.

    Instead of running a template once per window (one table scan each), this
    widens the date filter to the full span and adds a ``WindowIdx`` computed
    column that buckets each row into its trailing window. ``GROUP BY`` then
    yields one aggregate row per (metric, window) from a **single** scan — proven
    equivalent to the per-window queries but dramatically cheaper (the DB scans
    the range once rather than N times).

    Returns ``None`` when the template's shape can't be transformed safely (no
    single date filter, contains a sub-SELECT / multiple FROMs, or has no trailing
    ``GROUP BY``); the caller then falls back to per-window fetches. ``WindowIdx``
    is appended as the LAST selected column, so result rows are
    ``(EmployeeID, descriptor, AVG, SUM, WindowIdx)``.
    """
    if len(bounds) <= 1:
        return None

    # Exactly one "<col> BETWEEN '{start_date}' AND '{end_date}'" filter.
    matches = list(_DATE_FILTER_RE.finditer(template))
    if len(matches) != 1:
        return None
    m = matches[0]
    date_col = m.group("col")

    # Blind SELECT/GROUP BY injection is only safe on a single-table template
    # (no derived tables / correlated sub-queries) that ends with a GROUP BY.
    if "(SELECT" in template.upper().replace(" ", "") or template.upper().count(" FROM ") != 1:
        return None
    gb = re.search(r"\bGROUP BY\b", template, re.IGNORECASE)
    if not gb or re.search(r"\b(HAVING|ORDER BY|UNION)\b", template[gb.end():], re.IGNORECASE):
        return None

    # Full span (bounds are newest -> oldest) + a CASE that maps each row's date
    # to its window index.
    full_start = bounds[-1][0].strftime("%Y-%m-%d")
    full_end = bounds[0][1].strftime("%Y-%m-%d")
    whens = " ".join(
        f"WHEN {date_col} BETWEEN '{s.strftime('%Y-%m-%d')}' AND '{e.strftime('%Y-%m-%d')}' THEN {i}"
        for i, (s, e) in enumerate(bounds)
    )
    case_expr = f"CASE {whens} END"

    widened = (
        template[: m.start()]
        + f"{date_col} BETWEEN '{full_start}' AND '{full_end}'"
        + template[m.end():]
    )
    from_idx = widened.upper().index(" FROM ")
    widened = widened[:from_idx] + f", {case_expr} AS WindowIdx" + widened[from_idx:]
    widened = widened.rstrip() + f", {case_expr}"

    safe_emp = "'" + str(employee_id).replace("'", "''") + "'"
    key_list = ",".join("'" + str(k).replace("'", "''") + "'" for k in keys)
    return widened.format(employee_id=safe_emp, employee_ids=safe_emp, metric_keys=key_list)


def _accumulate(bucket: Dict[str, Dict[str, float]], key: str, avg_value: float, sum_value: float) -> None:
    """Accumulate a metric value, averaging duplicate avgs and summing duplicate sums."""
    if key in bucket["avg"]:
        bucket["avg"][key] = (bucket["avg"][key] + avg_value) / 2.0
        bucket["sum"][key] += sum_value
    else:
        bucket["avg"][key] = avg_value
        bucket["sum"][key] = sum_value


def _fetch_window(conn, employee_id, groups, queries, start_date, end_date, log_prefix: str = ""):
    """Fetch one time window for one employee, one query per referenced query name.

    KPIs are grouped by their ``query`` name (each may span multiple tables);
    each named template is filled and run once, then rows are mapped back to
    KPIs by matching their ``column_key`` to the descriptor column.
    ``log_prefix`` (e.g. a section name) is prepended to the query label in the
    logs so per-section loads are distinguishable.
    Returns ``{"avg": {column_key: val}, "sum": {column_key: val}}``.
    """
    by_query: Dict[str, list] = {}
    for group in groups:
        for spec in group.kpis:
            by_query.setdefault(spec.query, []).append(spec)

    bucket: Dict[str, Dict[str, float]] = {"avg": {}, "sum": {}}
    for query_name, specs in by_query.items():
        template = queries.get(query_name)
        if not template:
            logger.warning("No query template named '%s'; skipping %d KPIs", query_name, len(specs))
            continue
        keys = [s.column_key for s in specs]
        norm_map = _norm_map_for(keys)
        label = f"{log_prefix}{query_name}" if log_prefix else query_name
        # Each named query is isolated: a failure (missing table, permissions,
        # bad column) leaves only its own KPIs as N/A and never breaks the other
        # groups/tables in the same window.
        try:
            sql = _build_query(template, employee_id, keys, start_date, end_date)
            logger.debug("Metric query '%s': %s", label, sql)
            rows = _run_metric_query(conn, sql, label)
        except Exception as exc:
            logger.warning(
                "Metric query '%s' failed (%s); leaving its %d KPIs as N/A",
                label, exc, len(specs),
            )
            continue
        for row in rows:
            canonical = _canonical_metric_name(row[1], norm_map)
            if not canonical:
                continue
            avg_value = float(row[2]) if row[2] is not None else 0.0
            sum_value = float(row[3]) if row[3] is not None else 0.0
            _accumulate(bucket, canonical, avg_value, sum_value)
    return bucket


def _fetch_team_window(conn, employee_id, groups, queries, start_date, end_date, log_prefix: str = ""):
    """Fetch one time window of TEAM-average values for the employee's team.

    For each KPI source (``spec.query``) a companion team template named
    ``"<query>_team"`` is looked up in ``queries``. The team template resolves
    the employee's team internally (e.g. same ``VirtualLocationID`` for
    ``rep_pivoted`` or same ``Manager 1`` for ``customer_experience``) and
    returns the team average per descriptor, so the definition of "team" lives
    entirely in the JSON query. Sources without a ``*_team`` template are
    skipped (their KPIs simply carry no team benchmark).

    Team templates must emit the same 4-column shape as the individual queries
    (``EmployeeID, descriptor, AVG, SUM``). Returns
    ``{"avg": {column_key: val}, "sum": {column_key: val}}``.
    """
    by_query: Dict[str, list] = {}
    for group in groups:
        for spec in group.kpis:
            by_query.setdefault(spec.query, []).append(spec)

    bucket: Dict[str, Dict[str, float]] = {"avg": {}, "sum": {}}
    for query_name, specs in by_query.items():
        team_name = f"{query_name}_team"
        template = queries.get(team_name)
        if not template:
            logger.debug(
                "No team query '%s'; skipping team benchmark for %d KPIs",
                team_name, len(specs),
            )
            continue
        keys = [s.column_key for s in specs]
        norm_map = _norm_map_for(keys)
        label = f"{log_prefix}{team_name}" if log_prefix else team_name
        try:
            sql = _build_query(template, employee_id, keys, start_date, end_date)
            rows = _run_metric_query(conn, sql, label)
        except Exception as exc:
            logger.warning(
                "Team query '%s' failed (%s); leaving its %d KPIs without a team benchmark",
                label, exc, len(specs),
            )
            continue
        for row in rows:
            canonical = _canonical_metric_name(row[1], norm_map)
            if not canonical:
                continue
            avg_value = float(row[2]) if row[2] is not None else 0.0
            sum_value = float(row[3]) if row[3] is not None else 0.0
            _accumulate(bucket, canonical, avg_value, sum_value)
    return bucket


def _fetch_windows_for_query(
    conn, employee_id, group, queries, bounds, log_prefix: str = ""
) -> List[Dict[str, Dict[str, float]]]:
    """Fetch EVERY trend window for one query source, ideally in a single scan.

    The source's KPIs are read with one window-bucketed query (see
    :func:`_build_windowed_query`) that returns all windows from a single table
    scan. If the template can't be safely bucketed (or the merged query fails at
    runtime), this transparently falls back to one :func:`_fetch_window` per
    window, so callers always get correct data. Returns a list of
    ``{"avg": {...}, "sum": {...}}`` buckets, one per window, aligned with
    ``bounds`` (newest -> oldest).
    """
    keys = [s.column_key for s in group.kpis]
    query_name = group.kpis[0].query if group.kpis else group.name
    template = queries.get(query_name)
    buckets: List[Dict[str, Dict[str, float]]] = [
        {"avg": {}, "sum": {}} for _ in bounds
    ]
    if not template or not keys:
        if not template:
            logger.warning("No query template named '%s'; skipping %d KPIs", query_name, len(keys))
        return buckets

    label = f"{log_prefix}{query_name}" if log_prefix else query_name
    norm_map = _norm_map_for(keys)

    # Fast path: one window-bucketed scan covers every window at once.
    windowed = _build_windowed_query(template, employee_id, keys, bounds)
    if windowed is not None:
        try:
            rows = _run_metric_query(conn, windowed, f"{label} (all {len(bounds)} windows)")
            for row in rows:
                canonical = _canonical_metric_name(row[1], norm_map)
                if not canonical:
                    continue
                widx = int(row[4]) if row[4] is not None else -1
                if not 0 <= widx < len(bounds):
                    continue
                avg_value = float(row[2]) if row[2] is not None else 0.0
                sum_value = float(row[3]) if row[3] is not None else 0.0
                _accumulate(buckets[widx], canonical, avg_value, sum_value)
            return buckets
        except Exception as exc:
            logger.warning(
                "Windowed query '%s' failed (%s); falling back to per-window scans",
                label, exc,
            )

    # Fallback: one query per window (still correct, just more round-trips).
    for wi, (start, end) in enumerate(bounds):
        buckets[wi] = _fetch_window(
            conn, employee_id, [group], queries, start, end, log_prefix=log_prefix
        )
    return buckets


def _run_concurrent(jobs: List) -> List:
    """Run each ``job(conn)`` callable concurrently over the shared connection pool.

    Each worker borrows a live connection from :func:`acquire_connection` (which
    reuses warm connections across page loads instead of reopening them) and
    returns it with :func:`release_connection`. A connection that errors is
    retired via :func:`discard_connection` so a dropped socket is never reused.
    Because MARS is not enabled, running the jobs on separate pooled connections
    is what lets the queries execute in parallel. Results are returned in the same
    order as ``jobs``.
    """
    results: List = [None] * len(jobs)
    if not jobs:
        return results

    def _run(index, job):
        conn = acquire_connection()
        try:
            value = job(conn)
        except Exception:
            discard_connection(conn)
            raise
        release_connection(conn)
        return index, value

    workers = max(1, min(_MAX_QUERY_WORKERS, len(jobs)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_run, i, job) for i, job in enumerate(jobs)]
        for future in as_completed(futures):
            index, value = future.result()
            results[index] = value
    return results


def _merge_groups_by_query(groups: list) -> list:
    """Collapse every section's KPIs into one synthetic group per query template.

    Sections that read the same source overlap: e.g. Resolution KPIs, Efficiency
    and Quality all read ``rep_pivoted`` for the same window and differ only by
    their ``MetricDesc`` filter. Merging their KPIs under a single template lets
    one SQL statement serve every one of them, so a window issues just one query
    per distinct table instead of one per section — roughly halving the number of
    round-trips. Returns a list of ``MetricGroup`` objects (named after the query)
    in first-seen order; each maps to exactly one SQL statement in
    :func:`_fetch_window` / :func:`_fetch_team_window`.
    """
    from backend.config.programs import MetricGroup

    by_query: Dict[str, list] = {}
    order: List[str] = []
    for group in groups:
        for spec in group.kpis:
            if spec.query not in by_query:
                by_query[spec.query] = []
                order.append(spec.query)
            by_query[spec.query].append(spec)
    return [MetricGroup(name=q, kpis=by_query[q]) for q in order]


def fetch_team_metrics(
    employee_id: str,
    days_back: int = 7,
    end_date: Optional[datetime] = None,
    program_id: Optional[str] = None,
) -> Dict[str, Dict[str, float]]:
    """Fetch TEAM-average metric values for the employee's current window.

    Used by the Performance Radar to plot the agent's scores against their team
    average. "Team" is defined inside each ``*_team`` SQL template (loaded from
    the program JSON ``individual_metric_queries``) — e.g. same site
    (``VirtualLocationID``) for ``rep_pivoted`` and same ``Manager 1`` for
    ``customer_experience`` — so the grouping is fully config-driven.

    Each metric section loads on its own worker/connection (async), scoped to
    that section's KPIs. Only the current window is fetched (the radar compares
    the current period); the window bounds match window 0 of
    :func:`_window_bounds` (``end_date - days_back`` .. ``end_date``). Returns
    ``{"avg": {column_key: val}, "sum": {column_key: val}}``; an empty structure
    when SQL is unavailable or no ``*_team`` templates exist.
    """
    empty: Dict[str, Dict[str, float]] = {"avg": {}, "sum": {}}
    if not employee_id:
        return empty
    if not is_configured():
        logger.warning("Application Azure SQL not configured; cannot fetch team metrics")
        return empty

    groups = get_metric_groups(program_id)
    queries = get_metric_queries(program_id)
    if not groups:
        return empty

    try:
        if end_date is None:
            end_date = datetime.now()
        start_date = end_date - timedelta(days=days_back)

        # Sections sharing a table/window overlap; collapse them into one query
        # per table so the team benchmark is one query per source, not per section.
        merged_groups = _merge_groups_by_query(groups)
        jobs = [
            (lambda conn, g=group: _fetch_team_window(
                conn, employee_id, [g], queries, start_date, end_date,
                log_prefix=f"{g.name}/",
            ))
            for group in merged_groups
        ]
        results = _run_concurrent(jobs)

        bucket: Dict[str, Dict[str, float]] = {"avg": {}, "sum": {}}
        for section_bucket in results:
            if not section_bucket:
                continue
            bucket["avg"].update(section_bucket["avg"])
            bucket["sum"].update(section_bucket["sum"])
        logger.info(
            "Fetched team-average metrics (%d sources across \u2264%d workers) for employee %s",
            len(merged_groups), min(_MAX_QUERY_WORKERS, len(jobs)), employee_id,
        )
        return bucket
    except Exception as exc:
        logger.error("Failed to fetch application Azure SQL team metrics: %s", exc)
        return empty


# Result-dict key suffixes per trailing window, ordered newest -> oldest.
# Index 0 == current week, 1 == one week back, etc. Kept backward-compatible:
# "avg"/"sum" (current) and "avg_prev"/"sum_prev" (W-1) are unchanged, with
# "_prev2"/"_prev3" added for the deeper trend history.
_PREV_SUFFIXES: List[str] = ["", "_prev", "_prev2", "_prev3", "_prev4", "_prev5"]


def _window_bounds(
    end_date: datetime, days_back: int, weeks_back: int
) -> List[tuple]:
    """Build ``weeks_back + 1`` consecutive (start, end) windows.

    Returned newest -> oldest: index 0 is the current period ending at
    ``end_date``; each subsequent window is the immediately preceding,
    non-overlapping span of the same length.
    """
    bounds: List[tuple] = []
    w_end = end_date
    for _ in range(weeks_back + 1):
        w_start = w_end - timedelta(days=days_back)
        bounds.append((w_start, w_end))
        w_end = w_start - timedelta(days=1)
    return bounds


def _assemble_trend_result(buckets: List[Dict[str, Dict[str, float]]]) -> Dict[str, Dict[str, float]]:
    """Flatten ordered window buckets (newest->oldest) into the result dict.

    Produces ``avg``/``sum`` for the current window and ``avg_prevN``/
    ``sum_prevN`` for each prior window, preserving the legacy 2-window keys.
    """
    result: Dict[str, Dict[str, float]] = {}
    for idx, bucket in enumerate(buckets):
        suffix = _PREV_SUFFIXES[idx] if idx < len(_PREV_SUFFIXES) else f"_prev{idx}"
        result["avg" + suffix] = bucket.get("avg", {})
        result["sum" + suffix] = bucket.get("sum", {})
    return result


def _merge_source_windows(
    source_results: List, window_count: int
) -> List[Dict[str, Dict[str, float]]]:
    """Merge each source's per-window buckets into one combined bucket per window.

    ``source_results`` has one entry per query source (as returned by
    :func:`_fetch_windows_for_query`) — each a list of ``window_count`` buckets
    aligned newest -> oldest. Returns a single list of merged ``{"avg", "sum"}``
    buckets, one per window.
    """
    merged: List[Dict[str, Dict[str, float]]] = [
        {"avg": {}, "sum": {}} for _ in range(window_count)
    ]
    for source_windows in source_results:
        if not source_windows:
            continue
        for wi, bucket in enumerate(source_windows):
            if wi >= window_count or not bucket:
                continue
            merged[wi]["avg"].update(bucket["avg"])
            merged[wi]["sum"].update(bucket["sum"])
    return merged


def fetch_employee_metrics_by_section(
    employee_id: str,
    days_back: int = 7,
    end_date: Optional[datetime] = None,
    program_id: Optional[str] = None,
    weeks_back: int = 3,
) -> Dict[str, Dict[str, float]]:
    """Fetch the current period plus ``weeks_back`` trailing weeks for one employee.

    Sections that read the same source overlap (they differ only by their
    ``MetricDesc`` filter), so they are collapsed into one query per distinct
    table. Each source then fetches ALL of its trend windows in a single
    window-bucketed scan (see :func:`_fetch_windows_for_query`), and the sources
    run concurrently on a bounded connection pool. Results are keyed by each KPI's
    ``column_key`` and flattened into the multi-window dict
    (``avg``/``sum``/``avg_prevN``/``sum_prevN``) the UI consumes.
    """
    if not employee_id:
        return {}
    if not is_configured():
        logger.warning("Application Azure SQL not configured; cannot fetch employee metrics")
        return {}

    groups = _individual_fetch_groups(program_id)
    queries = get_metric_queries(program_id)
    if not groups:
        return {}

    if end_date is None:
        end_date = datetime.now()
    bounds = _window_bounds(end_date, days_back, weeks_back)

    try:
        merged_groups = _merge_groups_by_query(groups)
        jobs = [
            (lambda conn, g=group: _fetch_windows_for_query(
                conn, employee_id, g, queries, bounds, log_prefix=f"{g.name}/",
            ))
            for group in merged_groups
        ]
        source_results = _run_concurrent(jobs)
        merged = _merge_source_windows(source_results, len(bounds))
        result = _assemble_trend_result(merged)
        logger.info(
            "Fetched %d-window trend metrics (%d sources, single-scan, "
            "\u2264%d workers) for employee %s",
            len(bounds), len(merged_groups), min(_MAX_QUERY_WORKERS, len(jobs)),
            employee_id,
        )
        return result
    except Exception as exc:
        logger.error("Trend metric fetch failed for employee %s: %s", employee_id, exc)
        return {}


def fetch_employee_and_team_metrics(
    employee_id: str,
    days_back: int = 7,
    end_date: Optional[datetime] = None,
    program_id: Optional[str] = None,
    weeks_back: int = 3,
) -> tuple:
    """Fetch the employee trend AND the team-average window in ONE concurrent burst.

    Builds every query the page needs \u2014 one single-scan, window-bucketed query
    per source for the employee's whole trend, plus one current-window query per
    source for the team average \u2014 and runs them all together on a single bounded
    connection pool. This collapses what used to be two sequential passes
    (individual, then team) into one parallel gather AND folds each source's trend
    windows into a single scan, so the page loads from roughly one query per
    source per scope instead of one per (source \u00d7 window). Returns
    ``(trend_result, team_bucket)``; falls back to two independent fetches on error.
    """
    empty_team: Dict[str, Dict[str, float]] = {"avg": {}, "sum": {}}
    if not employee_id:
        return {}, empty_team
    if not is_configured():
        logger.warning("Application Azure SQL not configured; cannot fetch metrics")
        return {}, empty_team

    groups = get_metric_groups(program_id)
    queries = get_metric_queries(program_id)
    if not groups:
        return {}, empty_team

    if end_date is None:
        end_date = datetime.now()
    bounds = _window_bounds(end_date, days_back, weeks_back)
    team_start, team_end = bounds[0]  # current window doubles as the team window

    try:
        # Employee scan pulls display + behavior-skill groups; the team benchmark
        # stays lean on the display groups only.
        ind_merged = _merge_groups_by_query(_individual_fetch_groups(program_id))
        team_merged = _merge_groups_by_query(groups)
        # One job per source for the employee trend (all windows in a single
        # scan) followed by one job per source for the team's current window.
        ind_jobs = [
            (lambda conn, g=group: _fetch_windows_for_query(
                conn, employee_id, g, queries, bounds, log_prefix=f"{g.name}/",
            ))
            for group in ind_merged
        ]
        team_jobs = [
            (lambda conn, g=group: _fetch_team_window(
                conn, employee_id, [g], queries, team_start, team_end,
                log_prefix=f"{g.name}/",
            ))
            for group in team_merged
        ]
        results = _run_concurrent(ind_jobs + team_jobs)
        source_results = results[: len(ind_jobs)]
        team_results = results[len(ind_jobs):]

        merged = _merge_source_windows(source_results, len(bounds))
        trend_result = _assemble_trend_result(merged)

        team_bucket: Dict[str, Dict[str, float]] = {"avg": {}, "sum": {}}
        for bucket in team_results:
            if not bucket:
                continue
            team_bucket["avg"].update(bucket["avg"])
            team_bucket["sum"].update(bucket["sum"])

        logger.info(
            "Fetched employee trend (%d windows) + team in ONE concurrent burst "
            "(%d queries across \u2264%d workers) for employee %s",
            len(bounds), len(ind_jobs) + len(team_jobs),
            min(_MAX_QUERY_WORKERS, len(ind_jobs) + len(team_jobs)), employee_id,
        )
        return trend_result, team_bucket
    except Exception as exc:
        logger.warning(
            "Combined metric fetch failed for employee %s (%s); "
            "falling back to independent fetches",
            employee_id, exc,
        )
        trend = fetch_employee_metrics_by_section(
            employee_id, days_back=days_back, end_date=end_date,
            program_id=program_id, weeks_back=weeks_back,
        )
        team = fetch_team_metrics(
            employee_id, days_back=days_back, end_date=end_date, program_id=program_id,
        )
        return trend, team


def normalize_metric_name(value: str) -> str:
    """Public normalization helper for matching metric labels across sources."""
    return _normalize_metric_name(value)
