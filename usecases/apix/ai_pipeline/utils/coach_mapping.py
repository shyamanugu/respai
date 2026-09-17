"""Coach mapping enrichment from Azure SQL.

Replicates the pre-migration ``merge()`` / ``preprocess()`` join chain that
attached coach information to raw voice records:

    raw.agent_pbxid  ==  [mdm].[dim_angel].SourceID
    dim_angel.EmployeeID  ==  [mdm].[dim_employee_hcht].EmployeeID

The raw voice data is keyed on ``agent_pbxid`` (== ``SourceID``), **not** on
``EmployeeID`` — so the ``dim_angel`` translation is required. ``CoachID`` /
``CoachName`` / ``EmployeeName`` come straight from ``dim_employee_hcht``.

Because an agent can appear under multiple ``(ProgramName, CoachID)`` combos
over time, validity is enforced by a derived date window
(``program_start_date = MIN(DateTimeUTC)`` .. ``program_end_date =
MAX(DateTimeUTC)`` per ``(EmployeeID, ProgramName, CoachID)``): a call is only
attributed to a coach when ``program_start_date <= start_tm_local <=
program_end_date``.

Everything here is *best-effort*: when Azure SQL is not configured (or the
query fails), enrichment is skipped and the raw frame is returned with null
coach columns so the rest of the pipeline keeps working for **all** coaches.
"""

from __future__ import annotations

from typing import Iterable, Optional

import polars as pl

from ai_pipeline.logging_config import get_logger
from ai_pipeline.services.sql import SqlService

logger = get_logger("utils.coach_mapping")

# SourceID → EmployeeID / CoachID / CoachName (+ derived program date window).
# Mirrors get_program(): dim_angel LEFT JOIN dim_employee_hcht on EmployeeID,
# regrouped by (SourceID, ProgramName, EmployeeName, CoachID). The SourceID set
# is restricted to the agents present in the raw file (no full-table scan).
_MAPPING_SQL = """
SELECT
    a.SourceID                AS SourceID,
    b.EmployeeID              AS EmployeeID,
    b.ProgramName             AS ProgramName,
    b.EmployeeName            AS EmployeeName,
    b.CoachID                 AS CoachID,
    b.CoachName               AS CoachName,
    MIN(b.DateTimeUTC)        AS program_start_date,
    MAX(b.DateTimeUTC)        AS program_end_date
FROM [mdm].[dim_angel] a
LEFT JOIN [mdm].[dim_employee_hcht] b
    ON a.EmployeeID = b.EmployeeID
WHERE a.SourceID IN ({source_ids})
GROUP BY a.SourceID, b.EmployeeID, b.ProgramName, b.EmployeeName, b.CoachID, b.CoachName
"""

# Columns the enrichment needs to be present on the raw frame.
JOIN_KEY = "agent_pbxid"
CALL_TIME_COL = "start_tm_local"


def _empty_coach_cols(df: pl.DataFrame) -> pl.DataFrame:
    """Ensure CoachID / CoachName exist (nulled) so downstream code is stable."""
    add = []
    if "CoachID" not in df.columns:
        add.append(pl.lit(None, dtype=pl.Int64).alias("CoachID"))
    if "CoachName" not in df.columns:
        add.append(pl.lit(None, dtype=pl.Utf8).alias("CoachName"))
    return df.with_columns(add) if add else df


def _clean_source_ids(source_ids: Iterable) -> list[str]:
    """Normalise agent_pbxid / SourceID values to a distinct, quoted-safe list."""
    out: list[str] = []
    seen: set[str] = set()
    for v in source_ids:
        if v is None:
            continue
        s = str(v).strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def fetch_coach_mapping(sql_service: SqlService, source_ids: Optional[Iterable] = None) -> pl.DataFrame:
    """Return the SourceID→coach mapping for the given agents (empty if unavailable).

    ``source_ids`` are the ``agent_pbxid`` values present in the raw file; the
    query only fetches coaches for those agents (no full-table scan). When no
    ids are supplied there is nothing to look up, so an empty frame is returned.
    """
    ids = _clean_source_ids(source_ids or [])
    if not ids:
        logger.warning("No agent SourceIDs to look up — coach enrichment skipped (all coaches).")
        return pl.DataFrame()
    if not sql_service.is_configured():
        logger.warning("Azure SQL not configured — coach enrichment skipped (all coaches).")
        return pl.DataFrame()

    # Escape single quotes and build the IN list for the agents in the file.
    in_list = ", ".join("'" + s.replace("'", "''") + "'" for s in ids)
    sql = _MAPPING_SQL.format(source_ids=in_list)
    try:
        columns, rows = sql_service.run_query(sql)
    except Exception as exc:  # noqa: BLE001 — best-effort, never break the pipeline
        logger.warning("Coach mapping query failed: %s — skipping enrichment.", exc)
        return pl.DataFrame()
    if not rows:
        logger.warning("Coach mapping query returned no rows for %d agents.", len(ids))
        return pl.DataFrame()

    df = pl.DataFrame({col: [r[i] for r in rows] for i, col in enumerate(columns)})
    df = df.with_columns(
        pl.col("SourceID").cast(pl.Utf8, strict=False).str.strip_chars(),
        pl.col("CoachID").cast(pl.Int64, strict=False),
        pl.col("EmployeeID").cast(pl.Int64, strict=False),
        pl.col("program_start_date").cast(pl.Datetime, strict=False),
        pl.col("program_end_date").cast(pl.Datetime, strict=False),
    )
    logger.info(
        "Loaded coach mapping: %d rows across %d SourceIDs / %d coaches.",
        len(df), df["SourceID"].n_unique(), df["CoachID"].drop_nulls().n_unique(),
    )
    return df


def enrich_with_coach(df: pl.DataFrame, mapping: pl.DataFrame) -> pl.DataFrame:
    """Attach CoachID / CoachName / EmployeeName to raw rows.

    Joins ``agent_pbxid == SourceID`` (and ``ProgramName`` when both carry it to
    preserve program granularity), then applies the program date-window filter
    so the *active* coach for the call time is selected. Calls that match no
    coach window keep null coach columns (processed as "all coaches").
    """
    if df.is_empty() or mapping.is_empty():
        return _empty_coach_cols(df)
    if JOIN_KEY not in df.columns:
        logger.warning("Raw frame has no '%s' — cannot enrich coach mapping.", JOIN_KEY)
        return _empty_coach_cols(df)

    # Stable per-call id so the one-to-many join can be collapsed afterwards.
    left = df.with_row_index("_rid").with_columns(
        pl.col(JOIN_KEY).cast(pl.Utf8, strict=False).str.strip_chars().alias("_srckey")
    )

    use_program = "ProgramName" in df.columns and "ProgramName" in mapping.columns
    keep_cols = ["_srckey", "CoachID", "CoachName", "EmployeeName", "program_start_date", "program_end_date"]
    if use_program:
        keep_cols.insert(1, "ProgramName")
    map_sel = mapping.rename({"SourceID": "_srckey"})
    map_sel = map_sel.select([c for c in keep_cols if c in map_sel.columns])

    join_keys = ["_srckey", "ProgramName"] if use_program else ["_srckey"]
    enriched = left.join(map_sel, on=join_keys, how="left")

    # Determine whether each candidate coach row is valid for the call time.
    has_time = CALL_TIME_COL in enriched.columns
    if has_time:
        enriched = enriched.with_columns(
            pl.col(CALL_TIME_COL).cast(pl.Datetime, strict=False).alias("_call_ts")
        )
        in_window = (
            pl.col("_call_ts").is_not_null()
            & pl.col("program_start_date").is_not_null()
            & pl.col("program_end_date").is_not_null()
            & (pl.col("_call_ts") >= pl.col("program_start_date"))
            & (pl.col("_call_ts") <= pl.col("program_end_date"))
        )
    else:
        # No call timestamp — fall back to "any matched coach row is valid".
        in_window = pl.col("CoachID").is_not_null()
    enriched = enriched.with_columns(in_window.alias("_in_window"))

    # Does the call have at least one in-window coach candidate?
    has_win = enriched.group_by("_rid").agg(pl.col("_in_window").any().alias("_has_win"))
    enriched = enriched.join(has_win, on="_rid", how="left")

    # Keep in-window rows when available; otherwise keep one row with null coach.
    enriched = enriched.filter(
        (pl.col("_has_win") & pl.col("_in_window")) | (~pl.col("_has_win").fill_null(False))
    ).with_columns(
        pl.when(pl.col("_has_win").fill_null(False)).then(pl.col("CoachID"))
        .otherwise(pl.lit(None, dtype=pl.Int64)).alias("CoachID"),
        pl.when(pl.col("_has_win").fill_null(False)).then(pl.col("CoachName"))
        .otherwise(pl.lit(None, dtype=pl.Utf8)).alias("CoachName"),
    )

    # Collapse back to one row per original call.
    enriched = enriched.unique(subset=["_rid"], keep="first").sort("_rid")

    drop_cols = ["_rid", "_srckey", "_in_window", "_has_win", "program_start_date", "program_end_date"]
    if has_time:
        drop_cols.append("_call_ts")
    enriched = enriched.drop([c for c in drop_cols if c in enriched.columns])

    matched = int(enriched.filter(pl.col("CoachID").is_not_null()).height)
    logger.info("Coach enrichment: %d/%d calls attributed to a coach.", matched, len(df))
    return enriched


def enrich_frame(df: pl.DataFrame, sql_service: SqlService) -> pl.DataFrame:
    """Convenience: look up coaches for the agents in ``df`` and enrich it.

    Extracts the distinct ``agent_pbxid`` values from the frame, fetches only
    those coaches from Azure SQL, then joins/filters by the program window.
    """
    if df.is_empty():
        return _empty_coach_cols(df)
    source_ids = df[JOIN_KEY].to_list() if JOIN_KEY in df.columns else []
    mapping = fetch_coach_mapping(sql_service, source_ids)
    return enrich_with_coach(df, mapping)
