"""Coach / agent inventory utility.

Reads the *raw-transcripts* parquet file(s) for a date (or date range) and
produces a per-coach summary — how many agents (EmployeeIDs) and calls each
coach has — so that downstream steps (denoise) can be scoped to specific
coaches via ``--coach`` / ``AI_PIPELINE_COACH_FILTER``.

Usage
-----
    # Single program, single date
    python -m ai_pipeline.utils.coach_inventory --program pso --date 2026-07-18

    # Mode + date range, save JSON report to storage
    python -m ai_pipeline.utils.coach_inventory --mode wcc \
        --start 2026-07-18 --end 2026-07-24 --write

    # Print only the CoachID list (handy to pipe into --coach)
    python -m ai_pipeline.utils.coach_inventory --program pso --date 2026-07-18 --ids-only

Output
------
    {
      "date_range": {"start": "...", "end": "...", "files_found": N},
      "totals": {"coaches": C, "agents": A, "calls": T},
      "coaches": [
        {
          "coach_id": 9040400,
          "coach_name": "Elvin Balawat",
          "program_names": ["VZW BBT"],
          "agent_count": 12,
          "call_count": 143,
          "agents": [{"employee_id": 123, "employee_name": "...", "call_count": 9}, ...]
        },
        ...
      ]
    }
"""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import polars as pl

from ai_pipeline.logging_config import get_logger, setup_logging
from ai_pipeline.programs_config import load_mode_config, load_program_config
from ai_pipeline.programs_config.base import PipelineConfig
from ai_pipeline.services.storage import StorageService
from ai_pipeline.services.sql import SqlService
from ai_pipeline.utils import build_program_filter_sql
from ai_pipeline.utils.coach_mapping import enrich_frame

logger = get_logger("utils.coach_inventory")

# Columns we try to read from raw transcripts. Some programs omit optional ones,
# so the loader adapts to whatever the parquet actually exposes. agent_pbxid and
# start_tm_local are needed for the Azure SQL coach enrichment (join + window).
_PREFERRED_COLUMNS = [
    "CoachID", "CoachName", "EmployeeID", "EmployeeName", "ProgramName",
    "contact_id", "agent_pbxid", "start_tm_local",
]


def _date_range(start: date, end: date) -> list[date]:
    days, d = [], start
    while d <= end:
        days.append(d)
        d += timedelta(days=1)
    return days


def _build_where(cfg: PipelineConfig) -> Optional[str]:
    """Build the ProgramName WHERE clause from the config (mode or program)."""
    if cfg.mode:
        return build_program_filter_sql(cfg.mode) or None
    if cfg.program_filter:
        filters = cfg.program_filter if isinstance(cfg.program_filter, (list, tuple)) else [cfg.program_filter]
        escaped = ", ".join(f"'{n}'" for n in filters)
        return f"ProgramName IN ({escaped})"
    return None


def _load_raw(storage: StorageService, cfg: PipelineConfig, days: list[date]) -> tuple[pl.DataFrame, int]:
    """Load and concat available raw parquet files for the given days.

    Returns (dataframe, files_found). Missing files are skipped with a log line.
    """
    where = _build_where(cfg)
    frames: list[pl.DataFrame] = []
    files_found = 0
    for day in days:
        filename = f"{day}.parquet"
        try:
            df = storage.read_parquet_sql(cfg.storage.raw_container, filename, where=where)
        except FileNotFoundError:
            logger.debug("No raw file for %s, skipping", day)
            continue
        files_found += 1
        if df.is_empty():
            continue
        # Keep only the columns we actually need (whichever exist).
        keep = [c for c in _PREFERRED_COLUMNS if c in df.columns]
        df = df.select(keep).with_columns(pl.lit(str(day)).alias("date_utc"))
        frames.append(df)

    if not frames:
        return pl.DataFrame(), files_found
    return pl.concat(frames, how="diagonal_relaxed"), files_found


def build_coach_inventory(storage: StorageService, cfg: PipelineConfig, days: list[date]) -> dict:
    """Read raw transcripts and build a per-(program, coach) inventory.

    Produces one row per **(ProgramName, CoachID)** with the unique agent count
    (distinct EmployeeIDs) and the total conversations (calls) handled by all
    agents under that coach. Works irrespective of program:

    - If the raw schema carries ``CoachID``, rows are keyed by (program, coach).
    - If ``CoachID`` is absent for a program, that program's rows use a null
      coach id (grouped by program only) so the table is never empty.
    """
    df, files_found = _load_raw(storage, cfg, days)

    # Enrich with CoachID / CoachName from Azure SQL (agent_pbxid -> dim_angel
    # -> dim_employee_hcht). Best-effort: nulled coach columns when SQL is
    # unavailable, so the table still renders (Coach ID shown as "-").
    if not df.is_empty():
        df = enrich_frame(df, SqlService(cfg.azure_sql))
    result: dict = {
        "date_range": {
            "start": str(days[0]),
            "end": str(days[-1]),
            "files_found": files_found,
        },
        "has_coach": False,
        "totals": {"programs": 0, "coaches": 0, "agents": 0, "calls": 0},
        "rows": [],
    }

    if df.is_empty():
        logger.warning("No raw rows found for the requested range.")
        return result

    has_coach = "CoachID" in df.columns and df["CoachID"].drop_nulls().len() > 0
    has_emp = "EmployeeID" in df.columns
    has_emp_name = "EmployeeName" in df.columns
    has_name = "CoachName" in df.columns
    has_program = "ProgramName" in df.columns
    result["has_coach"] = has_coach

    # Normalise id columns and ensure the grouping columns always exist.
    if has_coach:
        df = df.with_columns(pl.col("CoachID").cast(pl.Int64, strict=False))
    else:
        df = df.with_columns(pl.lit(None, dtype=pl.Int64).alias("CoachID"))
    if has_emp:
        df = df.with_columns(pl.col("EmployeeID").cast(pl.Int64, strict=False))
    if not has_program:
        df = df.with_columns(pl.lit(None, dtype=pl.Utf8).alias("ProgramName"))

    def _agents_for(group: pl.DataFrame) -> list[dict]:
        agents: list[dict] = []
        if not has_emp:
            return agents
        emp_group = group.filter(pl.col("EmployeeID").is_not_null())
        for emp_id, egroup in emp_group.group_by("EmployeeID"):
            eid = emp_id[0] if isinstance(emp_id, tuple) else emp_id
            ename = egroup["EmployeeName"].drop_nulls().to_list()[:1] if has_emp_name else []
            agents.append({
                "employee_id": eid,
                "employee_name": ename[0] if ename else None,
                "call_count": len(egroup),
            })
        agents.sort(key=lambda a: a["call_count"], reverse=True)
        return agents

    total_agents = int(df.filter(pl.col("EmployeeID").is_not_null())["EmployeeID"].n_unique()) if has_emp else 0
    total_programs = int(df.filter(pl.col("ProgramName").is_not_null())["ProgramName"].n_unique())
    total_coaches = int(df.filter(pl.col("CoachID").is_not_null())["CoachID"].n_unique()) if has_coach else 0

    # One row per (program, coach). group_by keeps nulls so coachless programs
    # still appear (with coach_id = None).
    rows: list[dict] = []
    for keys, group in df.group_by(["ProgramName", "CoachID"]):
        prog, cid = keys
        agents = _agents_for(group)
        coach_name = group["CoachName"].drop_nulls().to_list()[:1] if has_name else []
        unique_agents = len(agents) if has_emp else 0
        rows.append({
            "program_name": prog,
            "coach_id": cid,
            "coach_name": coach_name[0] if coach_name else None,
            "unique_agents": unique_agents,
            "total_conversations": len(group),
            "agents": agents,
        })

    # Sort by program, then by conversations desc within a program.
    rows.sort(key=lambda r: (r["program_name"] or "", -r["total_conversations"]))

    result["rows"] = rows
    result["totals"] = {
        "programs": total_programs,
        "coaches": total_coaches,
        "agents": total_agents,
        "calls": len(df),
    }
    return result


def _print_summary(inv: dict) -> None:
    dr = inv["date_range"]
    tot = inv["totals"]
    scope = "coach-aware" if inv.get("has_coach") else "no CoachID in raw (coach shown as -)"
    print(f"\n=== Inventory | {dr['start']} → {dr['end']} ({dr['files_found']} file(s)) | {scope} ===\n")

    headers = ("Program Name", "Coach ID", "Coach Name", "Unique Agents", "Total Conversations")
    rows = [
        (
            r["program_name"] or "(none)",
            str(r["coach_id"]) if r["coach_id"] is not None else "-",
            r["coach_name"] or "-",
            r["unique_agents"],
            r["total_conversations"],
        )
        for r in inv["rows"]
    ]

    # Column widths sized to content + headers.
    w0 = max(len(headers[0]), *(len(str(r[0])) for r in rows)) if rows else len(headers[0])
    w1 = max(len(headers[1]), *(len(str(r[1])) for r in rows)) if rows else len(headers[1])
    w2 = max(len(headers[2]), *(len(str(r[2])) for r in rows)) if rows else len(headers[2])
    w3 = max(len(headers[3]), *(len(str(r[3])) for r in rows)) if rows else len(headers[3])
    w4 = max(len(headers[4]), *(len(str(r[4])) for r in rows)) if rows else len(headers[4])

    line = f"{headers[0]:<{w0}}  {headers[1]:<{w1}}  {headers[2]:<{w2}}  {headers[3]:>{w3}}  {headers[4]:>{w4}}"
    print(line)
    print("-" * len(line))
    for c0, c1, c2, c3, c4 in rows:
        print(f"{c0:<{w0}}  {c1:<{w1}}  {c2:<{w2}}  {c3:>{w3}}  {c4:>{w4}}")
    print("-" * len(line))
    # Totals row — distinct agents overall and total conversations.
    total_label = "TOTAL"
    print(f"{total_label:<{w0}}  {'':<{w1}}  {'':<{w2}}  {tot['agents']:>{w3}}  {tot['calls']:>{w4}}")
    print(f"\nPrograms: {tot['programs']}  |  Coaches: {tot['coaches']}  |  Unique agents: {tot['agents']}  |  Conversations: {tot['calls']}\n")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Coach / agent inventory from raw transcripts")
    # Program/mode is OPTIONAL — omit both to inventory ALL programs in the raw file.
    group = parser.add_mutually_exclusive_group(required=False)
    group.add_argument("--program", default=None, help="Program config id (e.g. pso, telesales, wcc). Omit to include all programs.")
    group.add_argument("--mode", default=None, help="Processing mode(s), e.g. telesales|wcc. Omit to include all programs.")
    parser.add_argument("--date", default=None, help="Single date YYYY-MM-DD (default: today UTC)")
    parser.add_argument("--week", action="store_true", help="Process the 7-day window ending on --date (inclusive), matching the summary lookback")
    parser.add_argument("--start", default=None, help="Start date YYYY-MM-DD (inclusive)")
    parser.add_argument("--end", default=None, help="End date YYYY-MM-DD (inclusive)")
    parser.add_argument("--write", action="store_true", help="Write JSON report to the coach-hierarchy container")
    parser.add_argument("--out", default=None, help="Local file path to also write the JSON report")
    parser.add_argument("--ids-only", action="store_true", help="Print only a comma-separated CoachID list (or EmployeeIDs when no CoachID) to pipe into --coach/--agent")
    parser.add_argument("--log-level", default="INFO", help="Logging level")
    return parser.parse_args(argv)


def main(argv=None) -> None:
    args = parse_args(argv)
    setup_logging(level=args.log_level)

    cfg = load_mode_config(args.mode) if args.mode else load_program_config(args.program or "pso")
    if not args.mode and not args.program:
        # All-programs mode: reuse base config for storage/openai but drop any
        # ProgramName filter so every program in the raw file is included.
        cfg.mode = None
        cfg.program_filter = None
        cfg.program_id = "all"
        logger.info("No --program/--mode given: inventorying ALL programs.")
    storage = StorageService(cfg.storage)

    # Date resolution mirrors ai_pipeline.main: --start/--end range → --date single → today UTC.
    if args.start and args.end:
        start = datetime.strptime(args.start, "%Y-%m-%d").date()
        end = datetime.strptime(args.end, "%Y-%m-%d").date()
        days = _date_range(start, end)
        logger.info("Date range mode: %s -> %s (%d days)", start, end, len(days))
    elif args.week:
        # 7-day window ending on --date (inclusive), like the summary lookback.
        end = datetime.strptime(args.date, "%Y-%m-%d").date() if args.date else datetime.now(timezone.utc).date()
        start = end - timedelta(days=6)
        days = _date_range(start, end)
        logger.info("Week mode: %s -> %s (%d days)", start, end, len(days))
    elif args.date:
        days = [datetime.strptime(args.date, "%Y-%m-%d").date()]
    else:
        days = [datetime.now(timezone.utc).date()]

    inv = build_coach_inventory(storage, cfg, days)

    if args.ids_only:
        if inv["has_coach"]:
            coach_ids = [r["coach_id"] for r in inv["rows"] if r["coach_id"] is not None]
            print(",".join(str(i) for i in dict.fromkeys(coach_ids)))
        else:
            agent_ids = [a["employee_id"] for r in inv["rows"] for a in r["agents"] if a["employee_id"] is not None]
            print(",".join(str(i) for i in dict.fromkeys(agent_ids)))
        return

    _print_summary(inv)

    payload = json.dumps(inv, indent=2, ensure_ascii=False)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(payload)
        logger.info("Wrote local report: %s", args.out)
    if args.write:
        label = args.mode or args.program or "all"
        filename = f"coach_inventory_{label}_{days[0]}_{days[-1]}.json"
        storage.write_json(inv, cfg.storage.coach_hierarchy_container, filename)
        logger.info("Wrote report to storage: %s/%s", cfg.storage.coach_hierarchy_container, filename)


if __name__ == "__main__":
    main()
