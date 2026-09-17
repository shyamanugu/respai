"""rep_sql.py — Azure SQL data source for vzw.rep_pivoted performance metrics.

A single, self-contained pipeline for the ``vzw.rep_pivoted`` MetricDesc data
dictionary on Azure SQL.  Consolidates what used to be four parallel modules
(connection config, T-SQL generation, validation and execution) since they all
serve one purpose: answer a rep-metric question against Azure SQL.

Sections
--------
1. CONNECTION   — Azure AD token auth (DefaultAzureCredential), cached + refreshed.
2. VALIDATION   — SELECT-only safety for the single ``vzw.rep_pivoted`` table.
3. GENERATION   — natural-language → T-SQL (deterministic fast-path + LLM fallback).
4. EXECUTION    — run the query with ``EmployeeID IN (...)`` scope injection.

Employee scoping is applied only at execution time so callers always see just
their authorized employees.
"""

from __future__ import annotations

import re
import struct
import json
import os
import threading
import time
from chatbot.core.config import get_logger
from chatbot.core.config import (
    AZURE_SQL_SERVER as SERVER,
    AZURE_SQL_DATABASE as DATABASE,
    AZURE_SQL_PORT as PORT,
    AZURE_SQL_DRIVER as DRIVER,
    REP_TABLE as TABLE,
)
from chatbot.llm import generate_completion
from chatbot.llm import load_prompt
from chatbot.llm.dictionaries import match_rep_metrics
from chatbot.sources.sqlite_source import _AGG_KEYWORDS, _resolve_period
from chatbot.sources import azure_catalog

log = get_logger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# 1. CONNECTION — Azure AD token auth
# ═══════════════════════════════════════════════════════════════════════════
# Connection settings (SERVER/DATABASE/PORT/DRIVER/TABLE) are imported from
# chatbot.core.config, which loads the relevant .env files at import time.

# Microsoft-defined connection attribute for an AAD access token (msodbcsql.h).
SQL_COPT_SS_ACCESS_TOKEN = 1256
# AAD token audience for Azure SQL Database.
_TOKEN_SCOPE = "https://database.windows.net/.default"

# Login timeout (s) — bounds a stuck connect / serverless DB resume. Generous
# by default because a paused serverless tier can take ~30–90s to wake.
_LOGIN_TIMEOUT: int = int(os.getenv("REP_SQL_LOGIN_TIMEOUT", "90"))
# Per-statement timeout (s) — 0 disables (default) so a legitimate cold query
# is not killed; set >0 to bound runaway queries.
_QUERY_TIMEOUT: int = int(os.getenv("REP_SQL_QUERY_TIMEOUT", "0"))

_credential = None  # lazy DefaultAzureCredential
_conn = None  # cached pyodbc connection
_lock = threading.Lock()
_token_expires_on: float = 0.0
# Cached credential probe so the router can fall back to SQLite without
# retrying a failed credential on every request. None = not yet probed.
_configured_cache: bool | None = None


def connection_string() -> str:
    """Build the (credential-free) pyodbc connection string."""
    return (
        f"DRIVER={{{DRIVER}}};"
        f"SERVER={SERVER};"
        f"PORT={PORT};"
        f"DATABASE={DATABASE}"
    )


def _get_credential():
    global _credential
    if _credential is None:
        from azure.identity import DefaultAzureCredential

        _credential = DefaultAzureCredential()
    return _credential


def _token_struct() -> bytes:
    """Acquire an AAD access token and pack it for SQL_COPT_SS_ACCESS_TOKEN."""
    global _token_expires_on
    token = _get_credential().get_token(_TOKEN_SCOPE)
    _token_expires_on = float(token.expires_on)
    token_bytes = token.token.encode("utf-16-le")
    return struct.pack(f"<I{len(token_bytes)}s", len(token_bytes), token_bytes)


def is_configured() -> bool:
    """Return ``True`` when an AAD token can be obtained.

    The probe runs once and is cached so the source router can fall back to
    SQLite gracefully (no per-request retries) when no credential is available.
    """
    global _configured_cache
    if _configured_cache is not None:
        return _configured_cache
    try:
        _token_struct()  # raises if no credential / network
        _configured_cache = True
        log.info("rep_sql → credential probe OK (token auth available)")
    except Exception as exc:
        _configured_cache = False
        log.warning("rep_sql → credential probe failed, will use SQLite: %s", exc)
    return _configured_cache


def _new_connection():
    import pyodbc  # imported lazily so the module loads even without pyodbc

    log.info("rep_sql → connecting to %s/%s via AAD token (login_timeout=%ds)",
             SERVER, DATABASE, _LOGIN_TIMEOUT)
    conn = pyodbc.connect(
        connection_string(),
        attrs_before={SQL_COPT_SS_ACCESS_TOKEN: _token_struct()},
        timeout=_LOGIN_TIMEOUT,  # bound a stuck login (e.g. serverless resume)
    )
    # Per-statement timeout (0 = unbounded). Keeps a runaway query from hanging
    # the request indefinitely while still allowing a cold serverless resume.
    if _QUERY_TIMEOUT > 0:
        conn.timeout = _QUERY_TIMEOUT
    log.info("rep_sql → connection established")
    return conn


def get_connection(*, validate: bool = False):
    """Return the cached connection, (re)creating it on token expiry.

    The hot path reuses the cached connection **without** a ``SELECT 1`` probe
    so a normal query pays no extra round trip. Pass ``validate=True`` (used by
    warmup) to verify liveness. A dropped connection is handled lazily by
    ``reset_connection`` + retry in the callers.
    """
    global _conn
    with _lock:
        # Refresh proactively when the token is within 60s of expiry.
        token_expired = bool(_token_expires_on) and (time.time() > _token_expires_on - 60)

        if _conn is not None and not token_expired:
            if not validate:
                return _conn  # reuse cached connection — no liveness round trip
            try:
                _conn.cursor().execute("SELECT 1")
                return _conn
            except Exception:  # stale / dropped connection — rebuild below
                log.warning("rep_sql → cached connection stale, reconnecting")

        if _conn is not None:
            try:
                _conn.close()
            except Exception:
                pass
            _conn = None

        _conn = _new_connection()
        return _conn


def reset_connection() -> None:
    """Drop the cached connection so the next ``get_connection`` rebuilds it."""
    global _conn
    with _lock:
        if _conn is not None:
            try:
                _conn.close()
            except Exception:
                pass
            _conn = None



async def warmup() -> None:
    """Prime the Azure SQL connection so the first user query is not cold.

    A paused serverless tier can take ~30–90s to resume on the first connect,
    which previously landed on the user's first request (and exceeded the
    frontend timeout).  Running this at startup pays that cost once at boot.
    Best-effort: failures are swallowed and the router falls back to SQLite.
    """
    import asyncio

    def _prime() -> None:
        if not is_configured():
            log.info("warmup → Azure SQL not configured, skipping")
            return
        t0 = time.time()
        conn = get_connection(validate=True)
        conn.cursor().execute("SELECT 1").fetchall()
        log.info("warmup → Azure SQL connection warmed up in %.1fs", time.time() - t0)

    try:
        await asyncio.to_thread(_prime)
    except Exception as exc:  # best-effort only
        log.warning("warmup → Azure SQL warmup failed (non-fatal): %s", exc)


# ═══════════════════════════════════════════════════════════════════════════
# 2. VALIDATION — SELECT-only safety for vzw.rep_pivoted
# ═══════════════════════════════════════════════════════════════════════════
_FORBIDDEN_PATTERNS = [
    re.compile(r"\b(DELETE|UPDATE|INSERT|DROP|ALTER|CREATE|TRUNCATE|REPLACE|MERGE|EXEC|EXECUTE)\b", re.IGNORECASE),
    re.compile(r"\b(ATTACH|DETACH|GRANT|REVOKE)\b", re.IGNORECASE),
    re.compile(r"--"),       # line comment
    re.compile(r"/\*"),      # block comment
    re.compile(r";\s*\S"),   # stacked statements
]


class RepSQLValidationError(Exception):
    pass


def validate_rep_sql(sql: str) -> str:
    """Validate Azure SQL T-SQL safety. Returns cleaned SQL or raises.

    Unlike the SQLite validator this understands the schema-qualified (dotted)
    table name and T-SQL ``TOP`` (no ``LIMIT`` requirement).
    """
    log.info("validate_rep_sql → START sql_len=%d", len(sql) if sql else 0)

    if not sql or not sql.strip():
        log.error("validate_rep_sql → REJECTED: empty query")
        raise RepSQLValidationError("Empty SQL query")

    sql = sql.strip().rstrip(";").strip()

    if not sql.upper().startswith("SELECT"):
        log.error("validate_rep_sql → REJECTED: not a SELECT")
        raise RepSQLValidationError("Only SELECT queries are allowed")

    for pattern in _FORBIDDEN_PATTERNS:
        if pattern.search(sql):
            log.error("validate_rep_sql → REJECTED: forbidden pattern '%s'", pattern.pattern)
            raise RepSQLValidationError(f"Forbidden SQL pattern: {pattern.pattern}")

    # Allowed tables come from the editable registry (azure_catalog) so newly
    # added/enabled tables are accepted automatically — no validator change.
    allowed = azure_catalog.allowed_table_names()
    tables = _extract_tables(sql)
    disallowed = {t for t in tables if t not in allowed}
    if disallowed:
        log.error("validate_rep_sql → REJECTED: disallowed tables %s", disallowed)
        raise RepSQLValidationError(f"Disallowed table(s): {', '.join(sorted(disallowed))}")
    if not tables:
        log.error("validate_rep_sql → REJECTED: no allowed table in FROM/JOIN")
        raise RepSQLValidationError(
            f"Query must select FROM one of: {', '.join(sorted(allowed))}"
        )

    log.info("validate_rep_sql → PASSED")
    return sql


def _extract_tables(sql: str) -> set[str]:
    """Extract (optionally schema-qualified) table names from FROM/JOIN."""
    tables: set[str] = set()
    pat = re.compile(r"\b(?:FROM|JOIN)\s+(\[?[A-Za-z0-9_]+\]?(?:\.\[?[A-Za-z0-9_]+\]?)?)", re.IGNORECASE)
    for m in pat.finditer(sql):
        name = m.group(1).replace("[", "").replace("]", "").lower()
        tables.add(name)
    return tables


# ═══════════════════════════════════════════════════════════════════════════
# 3. GENERATION — natural-language → T-SQL
# ═══════════════════════════════════════════════════════════════════════════
_REP_SYSTEM_PROMPT = load_prompt("rep_sql_system")


def _escape(value: str) -> str:
    """Escape single quotes for safe T-SQL string literals."""
    return value.replace("'", "''")


def _date_window_clause(date_col: str, period: str) -> str:
    """Inclusive 7-day window ending on *period* for ``date_col``.

    Example: period=2036-04-10 -> window [2036-04-04, 2036-04-10].
    """
    p = _escape(period)
    return (
        f"CAST({date_col} AS date) BETWEEN "
        f"DATEADD(day, -6, CAST('{p}' AS date)) AND CAST('{p}' AS date)"
    )


def _build_deterministic_sql(metric_descs: list[str], period: str | None) -> str:
    """Build a fixed-shape SELECT for one or more exact metric values.

    The target table is taken from the registry's long/pivoted table
    (``azure_catalog.metric_table()``) rather than a hard-coded name, so the
    fast-path follows whatever pivoted table is configured.

    When *period* is given we filter to an inclusive 7-day window ending on
    that date. When it is omitted we return each agent's OWN latest reading per
    metric (correlated MAX(Timeframe) scoped to the same EmployeeID +
    MetricDesc) and use SELECT DISTINCT so each agent contributes a single,
    most-recent record.
    """
    mt = azure_catalog.metric_table()
    table = mt.name if mt else TABLE
    in_list = ", ".join(f"'{_escape(m)}'" for m in metric_descs)

    if period:
        date_filter = _date_window_clause("Timeframe", period)
        sql = (
            "SELECT EmployeeID, MetricDesc, Result_Num, Result_Den, "
            "IsCalculated, Timeframe "
            f"FROM {table} "
            f"WHERE MetricDesc IN ({in_list}) "
            f"AND {date_filter} "
            "ORDER BY Timeframe DESC"
        )
    else:
        # No period asked → latest timeframe per agent + metric (DISTINCT).
        sql = (
            "SELECT DISTINCT p.EmployeeID, p.MetricDesc, p.Result_Num, "
            "p.Result_Den, p.IsCalculated, p.Timeframe "
            f"FROM {table} p "
            f"WHERE p.MetricDesc IN ({in_list}) "
            "AND CAST(p.Timeframe AS date) = ("
            "SELECT MAX(CAST(p2.Timeframe AS date)) "
            f"FROM {table} p2 "
            "WHERE p2.EmployeeID = p.EmployeeID AND p2.MetricDesc = p.MetricDesc) "
            "ORDER BY p.Timeframe DESC"
        )
    log.info("rep _build_deterministic_sql → %s", sql)
    return sql


def _build_wide_latest_sql(
    question: str,
    table: "azure_catalog.AzureTable",
    period: str | None,
) -> str | None:
    """Latest single record per agent for ONE requested measure column.

    Wide/call-grain tables hold many rows per agent (one per interaction), so a
    plain ``SELECT`` returns several raw rows padded with unrelated columns. For
    a single-metric lookup we instead return each agent's most-recent row
    exposing only the name and the requested measure — one record per agent and
    just the asked-for column. Returns ``None`` when the question does not
    clearly name a single measure (the caller then uses the LLM).
    """
    col = azure_catalog.match_metric_column(table, question)
    if col is None:
        return None
    sql = _wide_latest_for(table, col.name, period)
    log.info("rep _build_wide_latest_sql → metric=%s | %s", col.name, sql)
    return sql


def _wide_latest_for(
    table: "azure_catalog.AzureTable", metric: str, period: str | None
) -> str:
    """Latest record per agent exposing only the name + *metric* column."""
    name_col = azure_catalog.display_name_column(table)
    emp_col = table.employee_column
    date_col = table.date_column

    outer_cols = ", ".join(c for c in (name_col, metric) if c)
    inner_cols = ", ".join(dict.fromkeys(c for c in (name_col, metric, emp_col) if c))
    where = f"WHERE {metric} IS NOT NULL"
    if period:
        where += f" AND {_date_window_clause(date_col, period)}"
    # Inner WHERE is the FIRST WHERE, so EmployeeID scope injection lands inside
    # the subquery where the employee column is in scope; ROW_NUMBER then keeps
    # only the most-recent row per agent.
    return (
        f"SELECT {outer_cols} FROM ("
        f"SELECT {inner_cols}, "
        f"ROW_NUMBER() OVER (PARTITION BY {emp_col} ORDER BY {date_col} DESC) AS _rn "
        f"FROM {table.name} {where}"
        f") q WHERE q._rn = 1 "
        f"ORDER BY {name_col or metric}"
    )


def _wide_distinct_for(
    table: "azure_catalog.AzureTable", column: str, period: str | None
) -> str:
    """Distinct values of a text/dimension *column* (e.g. Super_Call_Type).

    Returns the unique values scoped per agent (EmployeeID injected at
    execution) — no duplicate rows and no unrelated measure columns.
    """
    name_col = azure_catalog.display_name_column(table)
    emp_col = table.employee_column
    date_col = table.date_column
    cols = ", ".join(dict.fromkeys(c for c in (emp_col, name_col, column) if c))
    where = f"WHERE {column} IS NOT NULL"
    if period:
        where += f" AND {_date_window_clause(date_col, period)}"
    return f"SELECT DISTINCT {cols} FROM {table.name} {where} ORDER BY {column}"


def _parse_json_obj(raw: str) -> dict | None:
    """Best-effort parse of the first JSON object in an LLM reply."""
    s = (raw or "").strip()
    if s.startswith("```"):
        s = s.strip("`")
        if s.lower().startswith("json"):
            s = s[4:]
    m = re.search(r"\{.*\}", s, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


async def _map_wide_column_llm(
    question: str, table: "azure_catalog.AzureTable"
) -> tuple[str, str] | None:
    """GPT fallback that maps *question* to ONE column of a wide table.

    Used only when deterministic token matching fails to resolve a column —
    this catches telecom-domain synonyms (e.g. "AHT" → ``Handle_Tm_Seconds``,
    "FCR" → first-call resolution) that share no literal tokens with the column
    name. Returns ``(column_name, mode)`` where *mode* is ``"distinct"`` (list
    the unique values of a text column) or ``"value"`` (the numeric measure per
    agent), or ``None`` when no column clearly fits (caller then uses the full
    SQL-generation LLM path).
    """
    col_lines = "\n".join(
        f"- {c.name} ({c.sql_type}): {c.comment}" for c in table.columns
    )
    system = (
        "You map a call-center analytics question to EXACTLY ONE column of a "
        "SQL table, using telecom domain knowledge for synonyms and "
        "abbreviations. Examples: AHT / average handle time / handle time → the "
        "handle-time-seconds column; FCR → first-call/repeat resolution; "
        "transfer rate → the transfer-flag column; calls / volume → calls "
        "answered. Only choose a column that genuinely answers the question.\n\n"
        f"Columns of {table.name}:\n{col_lines}\n\n"
        'Reply with ONLY compact JSON: {"column": "<exact column name, or empty '
        'string if none fits>", "mode": "distinct" or "value"}. Use "distinct" '
        "when the user wants to LIST the unique values of a text/dimension "
        'column; use "value" when they want a numeric measure for the agent(s).'
    )
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": question},
    ]
    try:
        raw = await generate_completion(messages, max_tokens=120)
    except Exception as exc:
        log.warning("rep _map_wide_column_llm → LLM error: %s", exc)
        return None
    data = _parse_json_obj(raw)
    if not data:
        return None
    col_name = str(data.get("column") or "").strip()
    if not col_name:
        return None
    match = next(
        (c for c in table.columns if c.name.lower() == col_name.lower()), None
    )
    if match is None:
        log.info("rep _map_wide_column_llm → '%s' is not a column of %s",
                 col_name, table.name)
        return None
    mode = str(data.get("mode") or "value").strip().lower()
    if mode not in ("distinct", "value"):
        mode = "value"
    log.info("rep _map_wide_column_llm → %s mode=%s for: %s",
             match.name, mode, (question or "")[:80])
    return match.name, mode


def _try_fast_path(
    question: str,
    matches: list[dict],
    period: str | None,
    target: "azure_catalog.AzureTable | None" = None,
) -> str | None:
    """Deterministic T-SQL for simple metric lookups; ``None`` → use LLM.

    Two shapes are covered:
      * a long/pivoted metric table matched against its metric dictionary, and
      * a wide/call-grain table where the question names a single measure
        column (returns that agent's latest record for just that column).
    Aggregation/ranking questions always fall through to the LLM.
    """
    if _AGG_KEYWORDS.search(question):
        return None
    mt = azure_catalog.metric_table()
    # 1) Long/pivoted metric table → exact-metric fast-path.
    if matches and mt is not None and (target is None or target.name == mt.name):
        metric_descs = [m["metric_desc"] for m in matches]
        return _build_deterministic_sql(metric_descs, period)
    # 2) Wide/call-grain table → latest-record-per-agent single-measure path.
    if target is not None and target.metric_column is None:
        return _build_wide_latest_sql(question, target, period)
    return None


async def generate_rep_sql(
    question: str,
    chat_history: list[dict] | None = None,
    period: str | None = None,
    matches: list[dict] | None = None,
    *,
    explicit: bool = False,
) -> str:
    """Return a T-SQL string for *question*.

    Args:
        question: the (already self-contained) user question.
        chat_history: optional prior turns for LLM context.
        period: optional ``YYYY-MM-DD`` filter; resolved from question if None.
        matches: optional pre-computed rep metric matches (from the router).
        explicit: ``True`` when the caller pinned this source via an ``@sql``
            directive — the prompt then points hard at the Azure source/table;
            otherwise it lets the data dictionary pick among all tables.
    """
    if period is None:
        period = _resolve_period(question)
    if matches is None:
        matches = match_rep_metrics(question)

    # Auto-decide which registered table best fits the question (no hard-coding).
    target = azure_catalog.select_table(question, matches)

    # 1) Deterministic fast-path (only when the chosen table is the pivoted one).
    fast = _try_fast_path(question, matches, period, target)
    if fast is not None:
        log.info("generate_rep_sql → fast-path on %s (explicit=%s)", target.name, explicit)
        return fast

    # 1b) GPT column-map fallback for wide tables — resolves telecom-domain
    #     synonyms that token matching misses (e.g. "AHT" → Handle_Tm_Seconds)
    #     and "list <dimension>" → SELECT DISTINCT of that column. Aggregation/
    #     ranking questions are left to the full LLM path below.
    if (
        target is not None
        and target.metric_column is None
        and not _AGG_KEYWORDS.search(question)
    ):
        mapped = await _map_wide_column_llm(question, target)
        if mapped is not None:
            col_name, mode = mapped
            sql = (
                _wide_distinct_for(target, col_name, period)
                if mode == "distinct"
                else _wide_latest_for(target, col_name, period)
            )
            log.info("generate_rep_sql → GPT column-map %s mode=%s on %s",
                     col_name, mode, target.name)
            return sql

    # 2) LLM fallback (aggregation / ranking / comparison / wide tables).
    glossary = "\n".join(
        f"  - {m['metric_desc']} -> {m['meaning']}" for m in matches
    ) or "  (none matched — infer the closest metric)"

    # Point the model explicitly at the chosen source/table. When the source
    # was directed via @sql we lock to the Azure tables; otherwise we let the
    # data dictionary decide among every ALLOWED table.
    if explicit:
        target_block = (
            "This question is explicitly directed to the Azure SQL source "
            "(@sql) — use ONLY the ALLOWED Azure tables above. Primary table "
            f"identified from the data dictionary: {target.name}. Use it unless "
            "another ALLOWED table is clearly required by the question."
        )
    else:
        target_block = (
            f"Most relevant table (chosen from the data dictionary): {target.name}. "
            "Prefer it, but pick any other ALLOWED table when the question fits it better."
        )
    system = (
        _REP_SYSTEM_PROMPT
        .replace("{schema}", azure_catalog.build_schema_prompt())
        .replace("{glossary}", glossary)
        .replace("{target}", target_block)
    )
    if period:
        system += (
            "\n\nDATE RULE: when a specific date is provided by the user, use an "
            "inclusive 7-day window ending on that date. In SQL this means "
            "CAST(<date_col> AS date) BETWEEN DATEADD(day, -6, CAST('"
            f"{_escape(period)}' AS date)) AND CAST('{_escape(period)}' AS date)."
        )
    log.info("generate_rep_sql → LLM path source=azure_sql table=%s explicit=%s",
             target.name, explicit)

    messages: list[dict[str, str]] = [{"role": "system", "content": system}]
    for turn in (chat_history or [])[-4:]:
        role = turn.get("role")
        content = turn.get("content")
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": question})

    raw = await generate_completion(messages, max_tokens=2048)
    sql = _clean(raw)
    log.info("generate_rep_sql → LLM SQL: %s", sql)
    return sql


def _clean(raw: str) -> str:
    """Strip code fences / trailing semicolons from an LLM SQL response."""
    sql = (raw or "").strip()
    if sql.startswith("```"):
        sql = sql.split("```", 2)[1] if sql.count("```") >= 2 else sql.strip("`")
        if sql.lower().startswith("sql"):
            sql = sql[3:]
    return sql.strip().rstrip(";").strip()


# ═══════════════════════════════════════════════════════════════════════════
# 4. EXECUTION — run with EmployeeID scope injection
# ═══════════════════════════════════════════════════════════════════════════

# Hard ceiling on rows returned: we never fetch large result sets (e.g. 500).
# The cap scales with team size but stays within [MIN_ROW_CAP, MAX_ROW_CAP].
MIN_ROW_CAP: int = 5
MAX_ROW_CAP: int = 20


def _row_cap(team_size: int) -> int:
    """Row cap based on team size, clamped to ``[MIN_ROW_CAP, MAX_ROW_CAP]``."""
    return max(MIN_ROW_CAP, min(MAX_ROW_CAP, team_size or MIN_ROW_CAP))


def _has_employee_scope(sql: str, emp_col: str = "EmployeeID") -> bool:
    """Return True when the SQL already contains an employee scope filter."""
    return bool(re.search(
        rf"\b{re.escape(emp_col)}\b\s*(?:=|<>|!=|<=|>=|<|>|IN\b)",
        sql,
        re.IGNORECASE,
    ))


def _clamp_top(sql: str, cap: int) -> str:
    """Clamp an existing T-SQL ``TOP n`` down to ``cap`` (never raising it), or
    inject one after ``SELECT [DISTINCT]`` when absent.  Smaller explicit values
    (e.g. ``TOP 1``) are preserved."""
    m = re.search(r"\bTOP\s+\(?\s*(\d+)\s*\)?\s*", sql, re.IGNORECASE)
    if m:
        new = min(int(m.group(1)), cap)
        return sql[: m.start()] + f"TOP {new} " + sql[m.end():]
    return re.sub(
        r"(?i)^\s*SELECT\s+(DISTINCT\s+)?",
        lambda mm: f"SELECT {mm.group(1) or ''}TOP {cap} ",
        sql,
        count=1,
    )


def execute_rep_sql(
    sql: str,
    *,
    employee_ids: list[int],
    role: str = "coach",
    **kwargs,
) -> list[dict]:
    """Execute T-SQL with EmployeeID scope injection against Azure SQL."""
    log.info("execute_rep_sql → START role=%s, employee_ids=%d", role, len(employee_ids))

    if not employee_ids:
        log.warning("execute_rep_sql → no employee_ids, returning empty")
        return []

    # SECURITY: always inject the caller's roster scope. Treating a query as
    # "already scoped" is unsafe — a correlated subquery or an LLM-inlined
    # literal EmployeeID would previously skip injection and leak other teams'
    # rows. Injecting unconditionally intersects with the roster (fail-closed).
    scoped_sql, params = _inject_scope(sql, employee_ids)

    # Avoid row-cap injection for a single-agent query; only team-wide queries
    # are constrained to a small result set to prevent broad scans.
    if len(employee_ids) > 1:
        cap = _row_cap(len(employee_ids))
        scoped_sql = _clamp_top(scoped_sql, cap)
        log.info("execute_rep_sql → row cap=%d, scoped SQL:\n%s\nParams: %s",
                 cap, scoped_sql, params)
    else:
        cap = None
        log.info("execute_rep_sql → scoped SQL (single agent, no row cap):\n%s\nParams: %s",
                 scoped_sql, params)

    # Reuse the cached connection; on a dropped/stale connection rebuild once
    # and retry so we never probe with a per-request round trip.
    for attempt in (1, 2):
        try:
            conn = get_connection()
            cursor = conn.cursor()
            cursor.execute(scoped_sql, params)
            columns = [d[0] for d in cursor.description] if cursor.description else []
            rows = cursor.fetchall()
            result = [dict(zip(columns, row)) for row in rows]
            preview = json.dumps(result[:3], default=str, ensure_ascii=False, indent=2)
            log.info("execute_rep_sql → DONE: %d rows, columns=%s, preview:\n%s",
                     len(result), columns, preview)
            return result
        except Exception as e:  # pyodbc.Error, credential errors, etc.
            if attempt == 1:
                log.warning("execute_rep_sql → query failed, reconnecting and retrying: %s", e)
                reset_connection()
                continue
            log.error("execute_rep_sql → SQL error: %s", e)
            return []


def _inject_scope(sql: str, employee_ids: list[int]) -> tuple[str, list]:
    """Inject ``EmployeeID IN (?, ?, ...)`` scope filter into the T-SQL."""
    placeholders = ", ".join(["?"] * len(employee_ids))
    scope_condition = f"EmployeeID IN ({placeholders})"

    where_match = re.search(r"\bWHERE\b", sql, re.IGNORECASE)
    if where_match:
        insert_pos = where_match.end()
        scoped_sql = sql[:insert_pos] + f" {scope_condition} AND" + sql[insert_pos:]
    else:
        insert_match = re.search(
            r"\b(ORDER\s+BY|GROUP\s+BY|HAVING)\b", sql, re.IGNORECASE
        )
        if insert_match:
            insert_pos = insert_match.start()
            scoped_sql = sql[:insert_pos] + f"WHERE {scope_condition} " + sql[insert_pos:]
        else:
            scoped_sql = sql + f" WHERE {scope_condition}"

    return scoped_sql, list(employee_ids)
