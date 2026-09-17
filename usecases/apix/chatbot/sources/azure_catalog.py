"""
chatbot/sources/azure_catalog.py — Editable registry of Azure SQL tables.

This is the **single place** to ADD, MODIFY, or DISABLE ("comment out") the
Azure SQL tables that the chat agent is allowed to query.  Each entry carries
its own *data dictionary*:

  * a human description,
  * its columns (name → plain-English comment),
  * the column that holds the row's "metric key" (for long/pivoted tables),
  * the date column (for "latest period" resolution),
  * the employee column (for automatic scope injection),
  * the default SELECT columns (so results stay interpretable).

The Azure data-source agent (:mod:`chatbot.sources.azure_source`) derives three
things from this registry, so extending coverage is a *config edit* — no code
change is required:

  1. ``allowed_table_names()`` — the validator's allow-list.
  2. ``build_schema_prompt()`` — the schema block injected into the NL→SQL prompt.
  3. ``primary_table()`` — the table used by the deterministic metric fast-path.

To add a table:    append a new ``AzureTable(...)`` to ``AZURE_TABLES``.
To modify a table: edit its ``AzureTable(...)`` fields in place.
To comment a table out: set ``enabled=False`` (or delete the entry).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from chatbot.core.config import get_logger, REP_TABLE
from chatbot.llm.dictionaries import REP_PIVOTED_METRICS

log = get_logger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# TABLE DEFINITION
# ═══════════════════════════════════════════════════════════════════════════
@dataclass(frozen=True)
class AzureColumn:
    """One column in an Azure SQL table, with a documentation comment."""

    name: str
    sql_type: str
    comment: str = ""


@dataclass(frozen=True)
class AzureTable:
    """One queryable Azure SQL table plus its data dictionary.

    Set ``enabled=False`` to keep the definition for reference while removing
    the table from the agent's allow-list and prompt (a soft "comment out").
    """

    name: str                              # schema-qualified, e.g. "vzw.rep_pivoted"
    description: str
    columns: list[AzureColumn]
    employee_column: str                   # column used for scope injection
    date_column: str                       # column used for "latest period"
    enabled: bool = True
    metric_column: str | None = None       # column holding the metric key (long tables)
    metrics: dict[str, str] = field(default_factory=dict)  # metric key → meaning
    default_select: list[str] = field(default_factory=list)
    notes: str = ""                        # free-form extra guidance for the LLM


# ═══════════════════════════════════════════════════════════════════════════
# REGISTRY  ──  add / modify / disable tables here
# ═══════════════════════════════════════════════════════════════════════════
AZURE_TABLES: list[AzureTable] = [
    # ── Primary table: Verizon rep performance metrics (long / pivoted) ──────
    AzureTable(
        name=REP_TABLE,  # "vzw.rep_pivoted" (from config / .env)
        enabled=True,
        description=(
            "Verizon rep performance metrics in long/pivoted form — one row per "
            "employee, named metric (MetricDesc) and timeframe.  USE THIS TABLE "
            "when the question asks for a KPI by its metric name, abbreviation or "
            "synonym (e.g. AHT, VPH, NVPH, Rep Quality Score, Agent Close Rate, "
            "ACSS AA Utilization %, etc.).  All named metrics are stored as rows; "
            "filter on MetricDesc to pick the right one.  The KPI value is "
            "always Result_Num — select it directly."
        ),
        columns=[
            AzureColumn("Timeframe", "datetime", "reporting date/time of the metric row"),
            AzureColumn("CIMWorkerNumber", "nvarchar", "worker number (supplemental id)"),
            AzureColumn("MetricID", "int", "numeric id of the metric (do not filter on this — use MetricDesc)"),
            AzureColumn("MetricDesc", "nvarchar",
                        "EXACT metric name/key — ALWAYS filter rows with WHERE MetricDesc = '<name>' "
                        "using a name from the RELEVANT METRICS list; do not guess or invent values"),
            AzureColumn("IsCalculated", "bit", "ignore — do not use in calculations"),
            AzureColumn("VirtualLocationID", "int", "virtual location id (ignore unless explicitly asked)"),
            AzureColumn("GeographicLocationID", "int", "geographic location id (ignore unless explicitly asked)"),
            AzureColumn("Result_Num", "decimal", "the KPI value — always SELECT this column directly"),
            AzureColumn("Result_Den", "decimal", "ignore — do not use in calculations"),
            AzureColumn("EmployeeID", "int", "the agent the metric row belongs to — scope injection target"),
        ],
        employee_column="EmployeeID",
        date_column="Timeframe",
        metric_column="MetricDesc",
        metrics=REP_PIVOTED_METRICS,
        default_select=["EmployeeID", "MetricDesc", "Result_Num", "Timeframe"],
        notes=(
            "ROUTING: prefer this table whenever the question names a KPI by a "
            "recognised MetricDesc value (see RELEVANT METRICS list). "
            "SELECT Result_Num as the KPI value — always use it directly, no division. "
            "Do NOT use this table for call-grain columns like Handle_Tm_Seconds, "
            "Transfer_Flag, Repeat_* counts, VXS_* scores — those live in vzw.solutions."
        ),
    ),

    # ── ADD NEW TABLES BELOW ─────────────────────────────────────────────────
    # Wide (one column per metric) per-interaction table: call handling, repeat
    # resolution, value/sales and experience metrics at the call grain.
    AzureTable(
        name="vzw.solutions",
        enabled=False,
        description=(
            "Per-interaction (call-grain) fact table — one row per handled "
            "contact.  USE THIS TABLE when the question asks for call-grain "
            "metrics whose values are DIRECT COLUMNS: handle time in seconds "
            "(Handle_Tm_Seconds), transfers (Transfer_Flag / CallsAnswered), "
            "repeat-resolution counts (Repeat_*), experience scores "
            "(VXS_Overall_Rep_Pass / VXS_Overall_Rep_Cnt), value-per-hour "
            "(VPH_Num), negative value (NVPH_Num), and similar call-level "
            "metrics.  Values are direct numeric columns — no MetricDesc "
            "indirection and no Result_Num / Result_Den pattern."
        ),
        columns=[
            AzureColumn("ReportDate", "datetime", "date/time the interaction was captured"),
            AzureColumn("IVR_Call_ID", "nvarchar", "unique id of the call/session from the IVR system"),
            AzureColumn("Acss_Call_ID", "nvarchar", "unique id of the automated self-service session"),
            AzureColumn("GeographicLocationDescription", "nvarchar", "location/region where the interaction was handled"),
            AzureColumn("DeptGroupDescription", "nvarchar", "department or business group handling the interaction"),
            AzureColumn("ScorecardGroupDesc", "nvarchar", "performance group used for scorecard reporting"),
            AzureColumn("EmployeeID", "nvarchar", "unique id of the agent handling the interaction"),
            AzureColumn("EmployeeName", "nvarchar", "name of the agent handling the interaction"),
            AzureColumn("SkillGroup", "nvarchar", "skill category assigned to the agent (e.g. billing, tech support)"),
            AzureColumn("HourofDay", "nvarchar", "hour bucket when the interaction occurred"),
            AzureColumn("IVRIntentDesc", "nvarchar", "customer intent captured at the IVR stage"),
            AzureColumn("Super_Call_Type", "nvarchar", "high-level call type (billing, tech, sales, etc.)"),
            AzureColumn("Handle_Tm_Seconds", "decimal",
                        "total handle time in seconds (talk + hold + wrap-up) — "
                        "synonyms: handle time, handling time, aht raw seconds"),
            # For a comparable AHT metric per timeframe use vzw.rep_pivoted MetricDesc='Agent AHT'
            AzureColumn("CallsAnswered", "decimal",
                        "total number of calls answered handled by the agent (call volume, calls answered)"),
            AzureColumn("Transfer_Flag", "decimal",
                        "1 = call was transferred (transfers, transfer rate, transfer count), "
                        "0 = not transferred"),
            # Rate: SUM(Transfer_Flag) / NULLIF(SUM(CallsAnswered), 0)
            AzureColumn("Repeat_Den", "decimal", "total contacts eligible for repeat/contact-rate calculation"),
            AzureColumn("Repeat_Partial_2Hour_Cnt", "decimal", "repeat contacts partially resolved within 2 hours"),
            AzureColumn("Repeat_Full_2Hour_Cnt", "decimal", "repeat contacts fully resolved within 2 hours"),
            AzureColumn("Repeat_2Hour_Value", "decimal", "metric value for 2-hour repeat performance"),
            AzureColumn("Repeat_Partial_Sameday_Cnt", "decimal", "repeat contacts partially resolved same day"),
            AzureColumn("Repeat_Full_Sameday_Cnt", "decimal", "repeat contacts fully resolved same day"),
            AzureColumn("Repeat_Sameday_Value", "decimal", "metric value for same-day repeat resolution"),
            AzureColumn("Repeat_Partial_3Day_Cnt", "decimal", "repeat contacts partially resolved within 3 days"),
            AzureColumn("Repeat_Full_3Day_Cnt", "decimal", "repeat contacts fully resolved within 3 days"),
            AzureColumn("Repeat_3Day_Value", "decimal", "metric value for 3-day repeat resolution"),
            AzureColumn("Repeat_Partial_7Day_Cnt", "decimal", "repeat contacts partially resolved within 7 days"),
            AzureColumn("Repeat_Full_7Day_Cnt", "decimal", "repeat contacts fully resolved within 7 days"),
            AzureColumn("Repeat_7Day_Value", "decimal", "metric value for 7-day repeat resolution"),
            AzureColumn("VXS_Overall_Rep_Pass", "decimal", "interactions passing the overall experience standard"),
            AzureColumn("VXS_Overall_Rep_Cnt", "decimal", "total interactions measured for overall experience"),
            AzureColumn("VXS_VZ_Sat_Pass", "decimal", "interactions passing the Verizon satisfaction threshold"),
            AzureColumn("VXS_VZ_Sat_Cnt", "decimal", "total interactions measured for Verizon satisfaction"),
            AzureColumn("NVPH_Num", "decimal",
                        "total negative value generated per hour (nvph, negative vph) — call-grain total"),
            # For period-aggregated NVPH metric use vzw.rep_pivoted MetricDesc='NVPH'
            AzureColumn("OCC_Amt", "decimal", "total value/revenue or credit amount generated during interactions"),
            AzureColumn("Occ_Trans_Cnt", "decimal", "number of value-driving transactions (sales, upgrades, etc.)"),
            AzureColumn("AppEng_Num", "decimal", "count of app-engagement events triggered"),
            AzureColumn("VPH_Num", "decimal",
                        "total value generated per hour (vph, value per hour) — call-grain total"),
            # For period-aggregated VPH metric use vzw.rep_pivoted MetricDesc='VPH'
            AzureColumn("VT_Eligible_Count", "decimal", "interactions eligible for 'View Together' (co-browse)"),
            AzureColumn("VT_Ind_Count", "decimal", "interactions where the co-browse indicator applied"),
            AzureColumn("VT_ATTACH_NUM", "decimal", "sessions where co-browse was attached"),
            AzureColumn("TRG_Session_Cnt", "decimal", "sessions involving the technical resolution group"),
            AzureColumn("Trg_Call_Cnt", "decimal", "calls transferred to the technical group"),
            AzureColumn("TRG_Eligible", "decimal", "interactions eligible for technical escalation"),
            AzureColumn("RecoveryKey", "nvarchar", "unique key used for tracking or data recovery"),
            AzureColumn("Resolve_Total_Contacts", "decimal", "total contacts used for resolution measurement"),
            AzureColumn("Resolve_Same_Day_Count", "decimal", "contacts resolved on the same day"),
            AzureColumn("Resolve_2Hr_Count", "decimal", "contacts resolved within 2 hours"),
            AzureColumn("Resolve_3Day_Count", "decimal", "contacts resolved within 3 days"),
            AzureColumn("Resolve_5Day_Count", "decimal", "contacts resolved within 5 days"),
            AzureColumn("Resolve_7Day_Count", "decimal", "contacts resolved within 7 days"),
            AzureColumn("VPC_Num", "decimal", "total value generated per customer interaction"),
        ],
        employee_column="EmployeeID",
        date_column="ReportDate",
        default_select=["EmployeeID", "EmployeeName"],
        notes=(
            "ROUTING: prefer this table when the question asks for a metric "
            "that matches a direct column name (Handle_Tm_Seconds, Transfer_Flag, "
            "Repeat_* counts, VXS_* pass/count, VPH_Num, NVPH_Num, etc.) and "
            "does NOT match a named MetricDesc entry in vzw.rep_pivoted. "
            "Wide table — each metric is its OWN column (NOT a MetricDesc key). "
            "Values are direct: no Result_Num / Result_Den indirection. "
            "Aggregate with SUM/AVG + GROUP BY EmployeeID for per-agent figures. "
            "Rates are ratios computed in SQL, e.g. transfer rate = "
            "SUM(Transfer_Flag) / NULLIF(SUM(CallsAnswered), 0); experience pass "
            "rate = SUM(VXS_Overall_Rep_Pass) / NULLIF(SUM(VXS_Overall_Rep_Cnt), 0). "
            "To LIST the distinct values of a text/dimension column use "
            "SELECT DISTINCT of ONLY that column plus EmployeeID/EmployeeName."
        ),
    ),

    # ── ADD MORE TABLES BELOW ────────────────────────────────────────────────
    # Example template (copy, fill in, and remove enabled=False to activate):
    #
    # AzureTable(
    #     name="vzw.rep_daily_summary",
    #     description="One pre-aggregated summary row per employee per day.",
    #     columns=[
    #         AzureColumn("EmployeeID", "int", "agent id"),
    #         AzureColumn("SummaryDate", "date", "calendar day of the summary"),
    #         AzureColumn("CallsHandled", "int", "total calls handled that day"),
    #         AzureColumn("AvgHandleTime", "decimal", "average handle time (seconds)"),
    #     ],
    #     employee_column="EmployeeID",
    #     date_column="SummaryDate",
    #     default_select=["EmployeeID", "SummaryDate", "CallsHandled", "AvgHandleTime"],
    #     enabled=False,   # ← flip to True (or delete) to activate
    # ),
]


# ═══════════════════════════════════════════════════════════════════════════
# DERIVED ACCESSORS  (used by the Azure data-source agent)
# ═══════════════════════════════════════════════════════════════════════════
def enabled_tables() -> list[AzureTable]:
    """Return every table currently activated for querying."""
    return [t for t in AZURE_TABLES if t.enabled]


def allowed_table_names() -> set[str]:
    """Lower-cased names of all enabled tables (the validator allow-list)."""
    return {t.name.lower() for t in enabled_tables()}


def primary_table() -> AzureTable:
    """The first enabled table — used by the deterministic metric fast-path."""
    tables = enabled_tables()
    if not tables:
        raise RuntimeError("azure_catalog: no enabled tables in AZURE_TABLES")
    return tables[0]


def metric_table() -> AzureTable | None:
    """First enabled long/pivoted table (one that filters on a metric column).

    The deterministic metric fast-path only applies to such a table; ``None``
    means no pivoted table is registered, so every query goes through the LLM.
    """
    for t in enabled_tables():
        if t.metric_column:
            return t
    return None


# ── Query-based table selection ─────────────────────────────────────────────
import re as _re

# Common words that carry no table-discriminating signal.
_STOP_TOKENS = {
    "the", "and", "for", "with", "per", "show", "list", "give", "what", "which",
    "who", "how", "many", "much", "average", "avg", "total", "sum", "count",
    "agent", "agents", "employee", "employees", "team", "rep", "reps", "call",
    "calls", "this", "that", "their", "value", "metric", "metrics", "number",
    "date", "time", "name", "all", "top", "from", "have", "has", "are",
}


def _tokenize(text: str) -> set[str]:
    """Lower-cased word tokens (length ≥ 3, stop-words removed)."""
    return {
        w for w in _re.split(r"[^a-z0-9]+", (text or "").lower())
        if len(w) >= 3 and w not in _STOP_TOKENS
    }


def select_table(question: str, matches: list[dict] | None = None) -> AzureTable:
    """Pick the most relevant enabled table for *question*.

    The choice is data-driven (no hard-coded table): each enabled table is
    scored by how many of its column-name tokens appear in the question, plus a
    strong bonus for a long/pivoted table when the question matched its metric
    dictionary (``matches``). The highest score wins; ties fall back to the
    first enabled table. Returns a table even on a zero score so callers always
    have a target.
    """
    table, _ = _score_tables(question, matches)
    return table or primary_table()


def best_table_score(question: str, matches: list[dict] | None = None) -> float:
    """Relevance score of the best-matching enabled table (0 = nothing matched).

    Used by the source router to decide whether an Azure table fits a question
    better than the local SQLite store.
    """
    _, score = _score_tables(question, matches)
    return score


def _score_tables(
    question: str, matches: list[dict] | None = None
) -> tuple[AzureTable | None, float]:
    """Return ``(best_table, best_score)`` for *question* across enabled tables.

    Scoring rules
    -------------
    1. Column-name token overlap:   +2.0 per column whose name tokens appear in
       the question; +0.5 per column whose comment tokens appear in the question.
    2. MetricDesc dictionary bonus: +5.0 × len(matches) for a long/pivoted table
       whose metric dictionary matched the question.  This is deliberately larger
       than any realistic column-overlap score for ``vzw.solutions`` so that a
       named-metric question (AHT, VPH, Rep Quality Score, …) always routes to
       ``vzw.rep_pivoted`` rather than being captured by a partial column match
       in the wide table.
    3. Tie-break: first table in AZURE_TABLES wins (``rep_pivoted`` is listed
       first, so it wins equal-score ties).
    """
    qtokens = _tokenize(question)
    best: AzureTable | None = None
    best_score = -1.0

    for t in enabled_tables():
        score = 0.0
        for c in t.columns:
            col_tokens = _tokenize(c.name.replace("_", " "))
            if col_tokens & qtokens:
                score += 2.0
            if _tokenize(c.comment) & qtokens:
                score += 0.5
        # A long/pivoted table that matched its metric dictionary is a very
        # strong hit — use 5.0x so it always beats column-overlap on vzw.solutions.
        if t.metric_column and matches:
            score += 5.0 * len(matches)
        # Soft bonus: question tokens appearing in the metric-name vocabulary of a
        # pivoted table.  Catches abbreviations like "AHT" that are embedded in a
        # multi-word MetricDesc ("Agent AHT") and so missed by the phrase matcher.
        # Generic qualifier words ("rate", "score", "count", …) that appear in
        # many metric names are excluded so they don't falsely boost rep_pivoted
        # when a question like "transfer rate" clearly targets a solutions column.
        # Capped at 3.0 so the bonus never overwhelms a strong column-overlap
        # signal on vzw.solutions.
        _SOFT_EXCL = {"rate", "score", "count", "pct", "percent", "percentage"}
        if t.metric_column and t.metrics:
            all_metric_tokens: set[str] = set()
            for _desc in t.metrics:
                all_metric_tokens |= _tokenize(_desc.replace(".", " ").replace("-", " "))
            kw_overlap = (qtokens & all_metric_tokens) - _SOFT_EXCL
            if kw_overlap:
                score += min(len(kw_overlap) * 1.5, 3.0)
        if score > best_score:
            best_score, best = score, t

    score = max(best_score, 0.0)
    log.info("azure_catalog _score_tables → '%s' (score=%.1f) for: %s",
             best.name if best else "-", score, (question or "")[:80])
    return best, score



def get_table(name: str) -> AzureTable | None:
    """Look up an enabled table by (case-insensitive) name."""
    target = name.lower()
    for t in enabled_tables():
        if t.name.lower() == target:
            return t
    return None


# ── Single-measure column matching (wide / call-grain tables) ───────────────
# Numeric SQL types that can carry a reportable metric value.
_NUMERIC_SQL_TYPES = {
    "int", "bigint", "smallint", "tinyint", "decimal", "numeric",
    "float", "real", "money", "bit",
}


def _is_numeric(col: AzureColumn) -> bool:
    """True when the column's SQL type holds a numeric measure."""
    return col.sql_type.lower().split("(", 1)[0].strip() in _NUMERIC_SQL_TYPES


def display_name_column(table: AzureTable) -> str | None:
    """The name column to show alongside a value (e.g. ``EmployeeName``)."""
    for c in table.columns:
        if "name" in c.name.lower():
            return c.name
    return None


def metric_columns(table: AzureTable) -> list[AzureColumn]:
    """Numeric *measure* columns of *table* (excludes id/key/date/name columns).

    These are the columns that can answer a "what is metric X" question on a
    wide table, where each metric is its own column rather than a row key.
    """
    skip = {table.employee_column.lower(), table.date_column.lower()}
    out: list[AzureColumn] = []
    for c in table.columns:
        lc = c.name.lower()
        if lc in skip:
            continue
        if lc.endswith(("id", "_id", "key")) or "name" in lc:
            continue
        if not _is_numeric(c):
            continue
        out.append(c)
    return out


def match_metric_column(table: AzureTable, question: str) -> AzureColumn | None:
    """Return the single measure column *question* clearly refers to, else None.

    Scores each measure column by token overlap of its (humanized) name and
    comment against the question. A column wins only when it has a direct
    name-word hit AND is an unambiguous top scorer; otherwise ``None`` is
    returned so the caller falls back to the LLM. This lets a single-metric
    question on a wide table ("handle time in seconds for agent X") resolve to
    exactly one column instead of dumping every default column.
    """
    qtokens = _tokenize(question)
    if not qtokens:
        return None
    scored: list[tuple[float, int, AzureColumn]] = []
    for c in metric_columns(table):
        name_overlap = len(_tokenize(c.name.replace("_", " ")) & qtokens)
        comment_overlap = len(_tokenize(c.comment) & qtokens)
        score = name_overlap * 2.0 + comment_overlap * 0.5
        if score > 0:
            scored.append((score, name_overlap, c))
    if not scored:
        return None
    scored.sort(key=lambda s: (s[0], s[1]), reverse=True)
    top_score, top_name_overlap, top_col = scored[0]
    # Require a direct name-word hit and an unambiguous winner (no tie).
    if top_name_overlap < 1:
        return None
    if len(scored) > 1 and abs(scored[1][0] - top_score) < 1e-9:
        return None
    log.info("azure_catalog match_metric_column → '%s' (score=%.1f) for: %s",
             top_col.name, top_score, (question or "")[:80])
    return top_col


def build_schema_prompt() -> str:
    """Render the schema block injected into the NL→SQL system prompt.

    Lists every enabled table, its columns (with comments) and — for long
    metric tables — the column to filter the metric on. The actual metric
    values come from the per-question ``{glossary}`` so the prompt stays small.
    """
    blocks: list[str] = []
    for t in enabled_tables():
        lines = [f"Table {t.name} — {t.description}", "  Columns:"]
        for c in t.columns:
            comment = f"  -- {c.comment}" if c.comment else ""
            lines.append(f"    - {c.name} ({c.sql_type}){comment}")
        if t.metric_column:
            lines.append(f"  Metric filter column: {t.metric_column} "
                         f"(use the RELEVANT METRICS list below; copy values VERBATIM)")
        if t.default_select:
            lines.append(
                f"  Identifier columns (include these so rows are interpretable, "
                f"then ADD ONLY the column(s) the question asks about — do NOT add "
                f"any other columns): {', '.join(t.default_select)}"
            )
        if t.notes:
            lines.append(f"  Note: {t.notes}")
        blocks.append("\n".join(lines))

    schema = "\n\n".join(blocks)
    log.info("azure_catalog → built schema prompt for %d table(s)", len(blocks))
    return schema


log.info("azure_catalog → %d table(s) registered, %d enabled",
         len(AZURE_TABLES), len(enabled_tables()))
