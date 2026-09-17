"""Summary step — aggregates weekly analysis into per-employee JSON reports."""

from __future__ import annotations

import asyncio
import json
from datetime import date, timedelta
from typing import Any, Dict, List

import pandas as pd
import polars as pl
from openai import AsyncOpenAI

from ai_pipeline.programs_config.base import PipelineConfig, ReflectionResponse
from ai_pipeline.logging_config import get_logger
from ai_pipeline.services import query, Status
from ai_pipeline.utils import retry_async, get_mode_for_program, build_program_filter_sql
from ai_pipeline.utils.throttle import Throttle, run_throttled
from ai_pipeline.services.storage import StorageService
from ai_pipeline.services.sql import SqlService
from ai_pipeline.utils.coach_mapping import enrich_frame

logger = get_logger("steps.summary")


def _coerce_int(value: Any) -> Any:
    """Return ``value`` as a plain int, or ``None``.

    Guards against pandas surfacing numeric ids as floats (e.g. ``9064400.0``)
    so the coach index keys/ids stay clean integers.
    """
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# ── helpers (preserved from original) ────────────────────────────────────────

def _fetch_raw(storage: StorageService, day, program_filter=None, mode=None):
    df = storage.read_parquet(storage.config.raw_container, f"{day}.parquet")
    if mode:
        # Mode-based: use env-mapped program names
        from ai_pipeline.utils import get_programs_for_mode
        all_names: list[str] = []
        for m in mode.split("|"):
            all_names.extend(get_programs_for_mode(m.strip()))
        if all_names:
            df = df.filter(pl.col("ProgramName").is_in(all_names))
    elif program_filter:
        df = df.filter(pl.col("ProgramName") == program_filter)
    if len(df) == 0:
        raise ValueError("No matching rows for program filter")
    return df.to_pandas()


def _fetch_transcript(storage: StorageService, date_utc, contact_id, start_id=None, end_id=None):
    df = storage.read_parquet(storage.config.denoised_container, f"{date_utc}.parquet")
    df = df.filter(pl.col("contact_id") == contact_id)
    transcript = df["denoised_text"][0].to_list()
    start_id = max(start_id, 0) if start_id is not None else 0
    end_id = min(end_id, len(transcript) - 1) if end_id is not None else len(transcript) - 1
    return [transcript[i] for i in range(start_id, end_id + 1)]


def _expand_struct_columns(df: pl.DataFrame, rules: list) -> pl.DataFrame:
    """Expand struct columns using ExpandColumnRule definitions from config."""
    for rule in rules:
        col = rule.column
        if col not in df.columns:
            continue
        tmp = pl.DataFrame(df[col].to_list(), infer_schema_length=None)
        tmp = tmp.rename({
            c: f"{col}_{c}" for c in tmp.columns
            if c in rule.rename_subfields
        })
        df = df.with_columns([pl.Series(name, tmp[name]) for name in tmp.columns])
        df = df.drop(col)
    return df


def _ensure_columns(df: pl.DataFrame, defaults: dict) -> pl.DataFrame:
    """Add missing columns with default values (mirrors reference ensure_columns)."""
    for col, default_expr in defaults.items():
        if col not in df.columns:
            df = df.with_columns(default_expr.alias(col))
    return df


def _build_column_defaults(cfg: PipelineConfig) -> dict:
    """Derive column defaults from config expand_column_rules + report_sections."""
    defaults: dict = {}
    for rule in cfg.expand_column_rules:
        for sub in rule.rename_subfields:
            col_name = f"{rule.column}_{sub}"
            if "segment_ids" in sub:
                defaults[col_name] = pl.lit(None, dtype=pl.List(pl.Int64))
            else:
                defaults[col_name] = pl.lit(None, dtype=pl.Utf8)
    for section in cfg.report_sections:
        for col in (section.or_columns or []):
            if col not in defaults:
                defaults[col] = pl.lit(None, dtype=pl.Boolean)
    return defaults


def _get_url(account_name: str, date_utc) -> str:
    return f"https://{account_name}.blob.core.windows.net/denoised-transcripts/{date_utc}.parquet"


# ── KPI calculation ─────────────────────────────────────────────────────────

def _compute_kpis(dfe: pl.DataFrame, df_all: pl.DataFrame, cfg: PipelineConfig, employee_list: list) -> tuple:
    """Compute KPI values and comparison metrics from config definitions."""
    kpis = []
    kpi_values = {}
    count_keys = set(cfg.count_kpi_keys)

    for kdef in cfg.kpi_definitions:
        if kdef.aggregate == "sum" and kdef.column:
            if kdef.filter_column:
                val = dfe.filter(pl.col(kdef.filter_column))[kdef.column].sum()
            else:
                val = dfe[kdef.column].sum()
        elif kdef.aggregate == "custom" and cfg.compute_custom_kpi is not None:
            val = cfg.compute_custom_kpi(kdef.key, dfe, df_all, cfg)
        else:
            val = 0
        unit = "count" if kdef.key in count_keys else "percent"
        kpis.append({"key": kdef.key, "label": kdef.label, "value": val, "unit": unit, "delta": None})
        kpi_values[kdef.key] = val

    comparisons = []
    for cm in cfg.comparison_metrics:
        individual = kpi_values.get(
            next((k.key for k in cfg.kpi_definitions if k.label == cm["metric"]), ""), 0
        )
        if "column" in cm:
            team_avg = df_all[cm["column"]].sum() / len(employee_list)
        elif "columns" in cm:
            team_avg = sum(df_all[c].sum() for c in cm["columns"] if c in df_all.columns) / len(employee_list)
        else:
            team_avg = 0
        comparisons.append({"metric": cm["metric"], "individual": individual, "teamAvg": team_avg, "unit": "count"})

    # ── Top core KPIs: individual vs team-average ────────────────────────
    # For percentage KPIs the team average is the population fraction across all
    # calls; for count KPIs it is the average number of at-risk calls per agent.
    if cfg.comparison_kpi_keys:
        bool_col_by_key = {k: bc for (k, _l, bc, _s) in cfg.evidence_kpi_keys}
        label_by_key = {kd.key: kd.label for kd in cfg.kpi_definitions}
        n_emp = max(len(employee_list), 1)
        for key in cfg.comparison_kpi_keys:
            bc = bool_col_by_key.get(key)
            if not bc or bc not in df_all.columns:
                continue
            if key in count_keys:
                team_avg = round(int(df_all[bc].cast(pl.Int64).fill_null(0).sum()) / n_emp, 2)
                unit = "count"
            else:
                non_null = df_all[bc].drop_nulls()
                team_avg = round(float(non_null.mean()), 2) if len(non_null) > 0 else 0.0
                unit = "percent"
            comparisons.append({
                "metric": label_by_key.get(key, key),
                "individual": kpi_values.get(key, 0),
                "teamAvg": team_avg,
                "unit": unit,
            })

    return kpis, comparisons


# ── generic report section builders ─────────────────────────────────────────

def _build_row_item(row, section, sf, storage, get_url):
    """Build a single report item dict from a DataFrame row using config."""
    seg_ids_col = section.segment_ids_column
    seg_ids = row[seg_ids_col].item() if seg_ids_col and seg_ids_col in row.columns else []
    if hasattr(seg_ids, "to_list"):
        seg_ids = seg_ids.to_list()
    elif seg_ids is None:
        seg_ids = []

    row_date = row["date_utc"].item()
    row_cid = row["contact_id"].item()

    summary_parts = []
    for col in section.summary_columns:
        if col in row.columns:
            summary_parts.append(row[col].item() or "")
    summary_text = " ".join(summary_parts).strip()

    tags_col = sf.tags_column
    tags = list(row[tags_col].item()) if tags_col and tags_col in row.columns else []

    excerpt = []
    if seg_ids and len(seg_ids) > 0:
        excerpt = _fetch_transcript(storage, row_date, row_cid, min(seg_ids) - 2, max(seg_ids) + 2)

    item = {
        "date_utc": row_date,
        "contact_id": row_cid,
        "summary": summary_text,
        "tags": tags,
        "transcript_url": get_url(row_date),
        "transcript_ids": sorted(list(seg_ids)) if seg_ids else [],
        "transcript_excerpt": excerpt,
    }

    for out_key, col_name in section.extra_columns.items():
        if col_name in row.columns:
            item[out_key] = row[col_name].item()

    return item


def _build_or_section(dfe, section, sf, storage, get_url):
    """Build a flat list section using OR across boolean columns."""
    mask = pl.lit(False)
    for col in (section.or_columns or []):
        if col in dfe.columns:
            mask = mask | (pl.col(col) == True)
    filtered = dfe.filter(mask)

    items = []
    for i in range(len(filtered)):
        items.append(_build_row_item(filtered[i], section, sf, storage, get_url))
    return items


def _build_value_section(dfe, section, sf, value, storage, get_url):
    """Build a list of items filtered by a specific column value."""
    filtered = dfe.filter(pl.col(section.filter_column) == value)

    items = []
    for i in range(len(filtered)):
        items.append(_build_row_item(filtered[i], section, sf, storage, get_url))
    return items


# ── WCC Core KPI evidence builders (resolution / survival / right-of-sell) ────

def _normalize_segment_ids(row_val):
    if row_val is None:
        return []
    if isinstance(row_val, list):
        return [int(x) for x in row_val if x is not None]
    if hasattr(row_val, "to_list"):
        return [int(x) for x in row_val.to_list() if x is not None]
    if isinstance(row_val, str):
        import re
        nums = re.findall(r"\b(\d+)\b", row_val.split("[")[-1])
        return [int(x) for x in nums]
    return []


def _wcc_core_evidence_item(storage, row, seg_ids):
    call_date = row["date_utc"]
    call_contact_id = row["contact_id"]
    excerpt = (
        _fetch_transcript(storage, call_date, call_contact_id, min(seg_ids) - 2, max(seg_ids) + 2)
        if seg_ids else []
    )
    return {
        "date_utc": call_date,
        "contact_id": call_contact_id,
        "segment_ids": sorted(seg_ids),
        "transcript_excerpt": excerpt,
    }


def _collect_flag_evidence(dfe, storage, flag_col, seg_col):
    """Per-call evidence items where flag_col is True."""
    items = []
    if flag_col not in dfe.columns:
        return items
    for i in range(len(dfe)):
        row = dfe.row(i, named=True)
        if not row.get(flag_col):
            continue
        items.append(_wcc_core_evidence_item(storage, row, _normalize_segment_ids(row.get(seg_col))))
    items.sort(key=lambda x: str(x["date_utc"]))
    return items


def _collect_missed_evidence(dfe, storage, opp_flag_col, opp_seg_col, actual_flag_col):
    """Per-call evidence where opportunity existed but no actual action."""
    items = []
    if opp_flag_col not in dfe.columns:
        return items
    for i in range(len(dfe)):
        row = dfe.row(i, named=True)
        if not row.get(opp_flag_col) or row.get(actual_flag_col):
            continue
        items.append(_wcc_core_evidence_item(storage, row, _normalize_segment_ids(row.get(opp_seg_col))))
    items.sort(key=lambda x: str(x["date_utc"]))
    return items


def _collect_kpi_flag_pointers(dfe, bool_col, seg_col):
    """Cheap per-call evidence pointers (date/contact/segment_ids) for every
    call where ``bool_col`` is True. Unlike the WCC evidence builders this
    does NOT fetch transcript excerpts, so it stays inexpensive even across
    the ~20 PSO boolean KPIs."""
    items = []
    if bool_col not in dfe.columns:
        return items
    for i in range(len(dfe)):
        row = dfe.row(i, named=True)
        if not row.get(bool_col):
            continue
        items.append({
            "date_utc": row.get("date_utc"),
            "contact_id": row.get("contact_id"),
            "segment_ids": sorted(_normalize_segment_ids(row.get(seg_col))),
        })
    items.sort(key=lambda x: str(x["date_utc"]))
    return items


def _collect_kpi_evidence_items(dfe, storage, bool_col, seg_col, limit=3):
    """Per-call evidence WITH transcript excerpts for calls where ``bool_col``
    is True. Mirrors the escalation report evidence (date/contact/segment_ids +
    ``transcript_excerpt``). Capped to the ``limit`` most important calls to keep
    the transcript fetch inexpensive across the many PSO KPIs."""
    if bool_col not in dfe.columns:
        return []
    matched = dfe.filter(pl.col(bool_col) == True)
    if matched.is_empty():
        return []
    if "call_importance" in matched.columns:
        matched = matched.sort("call_importance", descending=True, nulls_last=True)
    items = []
    for i in range(min(len(matched), limit)):
        row = matched.row(i, named=True)
        items.append(_wcc_core_evidence_item(storage, row, _normalize_segment_ids(row.get(seg_col))))
    return items


def _build_core_kpi(dfe, storage, prefix: str) -> dict:
    """Build a single WCC core KPI (exists/actual/missed counts + evidence + rate)."""
    exists_col = f"{prefix}_opportunity_exists"
    exists_seg = f"{prefix}_opportunity_segment_ids"
    actual_col = f"{prefix}_actual_exists"
    actual_seg = f"{prefix}_actual_segment_ids"

    def _bool_count(col):
        if col not in dfe.columns:
            return 0
        return int(dfe[col].cast(pl.Int64).fill_null(0).sum())

    exists_items = _collect_flag_evidence(dfe, storage, exists_col, exists_seg)
    actual_items = _collect_flag_evidence(dfe, storage, actual_col, actual_seg)
    missed_items = _collect_missed_evidence(dfe, storage, exists_col, exists_seg, actual_col)

    exists_count = _bool_count(exists_col)
    actual_count = _bool_count(actual_col)
    missed_count = len(missed_items)
    rate = round(actual_count / exists_count, 2) if exists_count else 0.0
    return {
        "exists_count": exists_count,
        "actual_count": actual_count,
        "missed_count": missed_count,
        "rate": rate,
        "exists_items": exists_items,
        "actual_items": actual_items,
        "missed_items": missed_items,
    }


def _build_wcc_count_kpis(dfe, storage) -> list:
    """Build the WCC Core KPI list (resolution / survival / right-of-sell) with evidence.

    Returns [] when the WCC core KPI columns are absent (non-WCC programs).
    """
    if "resolution_opportunity_exists" not in dfe.columns:
        return []

    resolution = _build_core_kpi(dfe, storage, "resolution")
    survival_rate = _build_core_kpi(dfe, storage, "survival_rate")
    right_of_sell = _build_core_kpi(dfe, storage, "right_of_sell")

    return [
        # ---------------- Resolution ----------------
        {"key": "resolution_rate", "label": "Resolution Rate", "score": resolution["rate"], "items": [], "delta": None},
        {"key": "resolution_exists", "label": "Resolution Opportunity Exists", "score": resolution["exists_count"], "items": resolution["exists_items"], "delta": None},
        {"key": "resolution_actual", "label": "Resolution Actual", "score": resolution["actual_count"], "items": resolution["actual_items"], "delta": None},
        {"key": "resolution_missed", "label": "Resolution Opportunity Missed", "score": resolution["missed_count"], "items": resolution["missed_items"], "delta": None},
        # ---------------- Survival / Saves ----------------
        {"key": "survival_rate", "label": "Survival Rate", "score": survival_rate["rate"], "items": [], "delta": None},
        {"key": "survival_rate_exists", "label": "Survival Opportunity Exists", "score": survival_rate["exists_count"], "items": survival_rate["exists_items"], "delta": None},
        {"key": "survival_rate_actual", "label": "Survival Actual (Saves)", "score": survival_rate["actual_count"], "items": survival_rate["actual_items"], "delta": None},
        {"key": "survival_rate_missed", "label": "Survival Opportunity Missed", "score": survival_rate["missed_count"], "items": survival_rate["missed_items"], "delta": None},
        # ---------------- Right of Sell ----------------
        {"key": "right_of_sell_rate", "label": "Right of Sell Rate", "score": right_of_sell["rate"], "items": [], "delta": None},
        {"key": "right_of_sell_exists", "label": "Right of Sell Opportunity Exists", "score": right_of_sell["exists_count"], "items": right_of_sell["exists_items"], "delta": None},
        {"key": "right_of_sell_actual", "label": "Right of Sell Actual", "score": right_of_sell["actual_count"], "items": right_of_sell["actual_items"], "delta": None},
        {"key": "right_of_sell_missed", "label": "Right of Sell Opportunity Missed", "score": right_of_sell["missed_count"], "items": right_of_sell["missed_items"], "delta": None},
    ]



# ── per-employee reflection LLM call ────────────────────────────────────────

async def _process_entry(input_data: dict, *, reasoning_client: AsyncOpenAI, cfg: PipelineConfig):
    try:
        dfe = input_data["df"]
        output = {
            "CoachID": input_data["CoachID"],
            "EmployeeID": input_data["EmployeeID"],
        }

        # Resolve mode-specific reflection prompt and schema
        reflection_prompt = cfg.reflection_system_prompt
        reflection_schema = cfg.reflection_schema or ReflectionResponse
        emp_program = input_data.get("ProgramName")
        if cfg.mode_configs and emp_program:
            emp_mode = get_mode_for_program(emp_program)
            mode_cfg = cfg.mode_configs.get(emp_mode)
            if mode_cfg:
                reflection_prompt = mode_cfg.reflection_system_prompt
                reflection_schema = mode_cfg.reflection_schema or ReflectionResponse

        _query = retry_async(cfg.max_retries, cfg.retry_delay)(query)
        response = await _query(
            client=reasoning_client,
            model=cfg.openai.reasoning_deployment,
            system_prompt=reflection_prompt,
            user_prompt=str(dfe.rows(named=True)),
            temperature=cfg.openai.analyze_temperature,
            schema=reflection_schema,
        )

        output["status"] = response["status"]
        output["query_response"] = response["message"]
        return output

    except Exception as e:
        logger.exception("Error processing employee %s: %s", input_data.get("EmployeeID"), e)
        return {"EmployeeID": input_data["EmployeeID"], "CoachID": input_data.get("CoachID"), "status": Status.ERROR.value}


# ── main entry point ────────────────────────────────────────────────────────

async def run_summary(date_utc: date, cfg: PipelineConfig, storage: StorageService) -> None:
    logger.info("=== SUMMARY START | date=%s program=%s mode=%s ===", date_utc, cfg.program_id, cfg.mode)

    reasoning_client = AsyncOpenAI(
        base_url=cfg.openai.reasoning_endpoint,
        api_key=cfg.openai.reasoning_api_key,
    )

    past_week = [date_utc - timedelta(days=i) for i in range(cfg.summary_lookback_days)]
    past_week.reverse()

    # ── coach-employee mapping (program-agnostic, whole-week union) ──────
    # Identify coach<->agent relations from every raw file in the week. Some
    # programs (e.g. telesales) carry the full hierarchy join in raw
    # (CoachID/CoachName/EmployeeName); others (e.g. pso) only carry
    # EmployeeID/ProgramName. We select only the columns that actually exist so
    # a single builder works for all programs and never crashes on missing
    # columns, and we still emit a per-program employee index when no coach
    # data is present.
    logger.info("Building coach-employee mapping for %d days", len(past_week))
    # Read the raw parquet for each day and keep the hierarchy columns plus the
    # keys needed for Azure SQL coach enrichment (agent_pbxid / start_tm_local).
    RAW_COLS = [
        "ProgramName", "CoachID", "CoachName", "EmployeeID", "EmployeeName",
        "contact_id", "agent_pbxid", "start_tm_local",
    ]
    program_names: list[str] = []
    if cfg.mode:
        from ai_pipeline.utils import get_programs_for_mode
        for m in cfg.mode.split("|"):
            program_names.extend(get_programs_for_mode(m.strip()))
    elif cfg.program_filter:
        program_names = list(cfg.program_filter) if isinstance(
            cfg.program_filter, (list, tuple)
        ) else [cfg.program_filter]

    raw_frames: list[pl.DataFrame] = []
    for day in past_week:
        try:
            raw_day = storage.read_parquet(storage.config.raw_container, f"{day}.parquet")
        except Exception:
            logger.debug("No raw data for %s, skipping", day)
            continue
        if program_names and "ProgramName" in raw_day.columns:
            raw_day = raw_day.filter(pl.col("ProgramName").is_in(program_names))
        if raw_day.is_empty() or "EmployeeID" not in raw_day.columns:
            continue
        keep = [c for c in RAW_COLS if c in raw_day.columns]
        raw_frames.append(raw_day.select(keep))

    coach_pl = pl.concat(raw_frames, how="diagonal_relaxed") if raw_frames else pl.DataFrame()

    # Enrich CoachID / CoachName / EmployeeName from Azure SQL for the agents in
    # the week (agent_pbxid -> dim_angel -> dim_employee_hcht). Best-effort: the
    # frame is returned with null coach columns when SQL is unavailable.
    if not coach_pl.is_empty():
        try:
            coach_pl = enrich_frame(coach_pl, SqlService(cfg.azure_sql))
        except Exception as exc:  # noqa: BLE001 — never break the summary
            logger.warning("Coach enrichment failed: %s — index will lack coach names", exc)

    coach_data = coach_pl.to_pandas() if not coach_pl.is_empty() else pd.DataFrame()

    has_coach = not coach_data.empty and "CoachID" in coach_data.columns
    has_coach_name = not coach_data.empty and "CoachName" in coach_data.columns
    has_emp_name = not coach_data.empty and "EmployeeName" in coach_data.columns

    # Direct employee -> name lookup used to label reports. None when the raw
    # data does not carry EmployeeName (we never fabricate names).
    employee_names: Dict[Any, Any] = {}
    if has_emp_name:
        for row in coach_data[["EmployeeID", "EmployeeName"]].drop_duplicates().to_dict(orient="records"):
            name = row.get("EmployeeName")
            if name and not employee_names.get(row["EmployeeID"]):
                employee_names[row["EmployeeID"]] = name

    # Name lookups only. The coach<->agent index membership itself is driven by
    # the analysis data (agents actually present in the week) further below;
    # these dicts merely supply human-readable names for those agents/coaches.
    coach_name_by_id: Dict[int, Any] = {}
    coach_program_by_id: Dict[int, Any] = {}
    if has_coach:
        name_cols = [c for c in ["CoachID", "CoachName", "ProgramName"] if c in coach_data.columns]
        for row in coach_data[name_cols].drop_duplicates().to_dict(orient="records"):
            cid = _coerce_int(row.get("CoachID"))
            if cid is None:
                continue
            if row.get("CoachName") and cid not in coach_name_by_id:
                coach_name_by_id[cid] = row.get("CoachName")
            if row.get("ProgramName") and cid not in coach_program_by_id:
                coach_program_by_id[cid] = row.get("ProgramName")
    employee_name_by_id: Dict[int, Any] = {}
    if has_emp_name:
        for row in coach_data[["EmployeeID", "EmployeeName"]].drop_duplicates().to_dict(orient="records"):
            eid = _coerce_int(row.get("EmployeeID"))
            if eid is not None and row.get("EmployeeName") and eid not in employee_name_by_id:
                employee_name_by_id[eid] = row.get("EmployeeName")

    # ── load analysis data ───────────────────────────────────────────────
    logger.info("Loading analysis data for past week")
    # Build WHERE clause for analysis data
    analysis_where = "analysis_status = 'ok'"
    if cfg.mode:
        program_sql = build_program_filter_sql(cfg.mode)
        if program_sql:
            analysis_where += f" AND {program_sql}"
    df_week = []
    for day in past_week:
        try:
            df_day = storage.read_parquet_sql_multi(
                cfg.storage.analysis_container,
                [f"{day}.parquet"],
                where=analysis_where,
                columns=["CoachID", "contact_id", "EmployeeID", "ProgramName", "analysis_response"],
            )
            df_day = df_day.with_columns(pl.lit(str(day)).alias("date_utc"))
            # ``analysis_response`` is stored as a JSON *string* (the raw LLM
            # structured output). Decode each record into a dict so the KPI
            # fields (predicted_csat, active_listening, ...) become real
            # columns; otherwise the flatten yields a single opaque string
            # column and every downstream KPI/behaviour aggregation reads 0.
            def _decode_response(x):
                if isinstance(x, str):
                    try:
                        return json.loads(x)
                    except (json.JSONDecodeError, TypeError):
                        return {}
                return x if isinstance(x, dict) else {}

            records = [_decode_response(x) for x in df_day["analysis_response"].to_list()]
            df_day = pl.concat([df_day, pl.DataFrame(records, infer_schema_length=None)], how="horizontal")
            df_day = df_day.drop("analysis_response")
            for col in cfg.utf8_cast_columns:
                if col in df_day.columns:
                    df_day = df_day.with_columns(pl.col(col).cast(pl.Utf8))
            df_week.append(df_day)
        except Exception:
            logger.debug("No analysis data for %s, skipping", day)
            continue

    # Days may decode to slightly different column sets (fields absent when
    # null across a whole day), so align them diagonally rather than requiring
    # identical schemas.
    df = pl.concat(df_week, how="diagonal_relaxed")
    df = _expand_struct_columns(df, cfg.expand_column_rules)
    column_defaults = _build_column_defaults(cfg)
    df = _ensure_columns(df, column_defaults)
    employee_list = df["EmployeeID"].drop_nulls().unique().to_list()
    logger.info("Employees to process: %d", len(employee_list))

    # ── coach-agent index (driven by agents present in analysis data) ───
    # Only agents that actually appear in this week's analysis data are indexed,
    # each mapped to their CoachID from the analysis data. Names come from the
    # enrichment lookups; ids stay clean ints (no float ".0").
    coach_mapping: Dict[str, dict] = {}
    if "CoachID" in df.columns and "EmployeeID" in df.columns:
        pair_cols = [c for c in ["CoachID", "EmployeeID", "ProgramName"] if c in df.columns]
        for rec in df.select(pair_cols).unique().to_dicts():
            cid = _coerce_int(rec.get("CoachID"))
            eid = _coerce_int(rec.get("EmployeeID"))
            if cid is None or cid == 0 or eid is None:
                continue
            skey = str(cid)
            entry = coach_mapping.get(skey)
            if entry is None:
                entry = {
                    "ProgramName": rec.get("ProgramName") or coach_program_by_id.get(cid),
                    "CoachId": cid,
                    "CoachName": coach_name_by_id.get(cid),
                    "employees": [],
                    "_seen": set(),
                }
                coach_mapping[skey] = entry
            if eid not in entry["_seen"]:
                entry["_seen"].add(eid)
                entry["employees"].append({
                    "EmployeeID": eid,
                    "EmployeeName": employee_name_by_id.get(eid),
                })
    for entry in coach_mapping.values():
        entry.pop("_seen", None)
    if not coach_mapping:
        logger.warning("No coach<->agent pairs in analysis data; coach-employee index will be empty")

    # ── append/merge into the weekly index file ──────────────────────
    weekly_file = f"index/{date_utc}.json"
    merged_mapping: Dict[str, dict] = {}
    if storage.exists(cfg.storage.coach_hierarchy_container, weekly_file):
        try:
            existing = storage.read_json(cfg.storage.coach_hierarchy_container, weekly_file)
            if isinstance(existing, dict):
                merged_mapping = {str(k): v for k, v in existing.items()}
        except Exception:
            logger.warning("Could not read existing weekly index %s; overwriting", weekly_file)
    for skey, entry in coach_mapping.items():
        if skey not in merged_mapping:
            merged_mapping[skey] = entry
            continue
        existing_entry = merged_mapping[skey]
        seen = {ed.get("EmployeeID") for ed in existing_entry.get("employees", [])}
        for emp in entry.get("employees", []):
            if emp.get("EmployeeID") not in seen:
                existing_entry.setdefault("employees", []).append(emp)
                seen.add(emp.get("EmployeeID"))
        for meta in ("ProgramName", "CoachId", "CoachName"):
            if not existing_entry.get(meta) and entry.get(meta):
                existing_entry[meta] = entry[meta]
    storage.write_json(merged_mapping, cfg.storage.coach_hierarchy_container, weekly_file)
    logger.info(
        "Coach-employee index written: %d coach entries, %d employees",
        len(merged_mapping),
        sum(len(v.get("employees", [])) for v in merged_mapping.values()),
    )

    # ── build per-employee inputs ────────────────────────────────────────
    inputs = []
    for e in employee_list:
        sf = cfg.summary_fields
        dfe = df.filter(pl.col("EmployeeID") == e)
        if sf.sort_column in dfe.columns:
            dfe = dfe.sort(sf.sort_column, descending=sf.sort_descending)
        if len(dfe) == 0:
            continue
        coach_id = dfe[0]["CoachID"].item()
        # Resolve the employee's program for mode-aware prompt dispatch
        emp_program = dfe[0]["ProgramName"].item() if "ProgramName" in dfe.columns else None
        drop_cols = [c for c in ["EmployeeID", "CoachID", "ProgramName"] if c in dfe.columns]
        dfe_clean = dfe.drop(drop_cols).with_columns(
            pl.arange(1, dfe.height + 1).cast(pl.Utf8).alias("reference_id")
        )
        inputs.append({"df": dfe_clean, "EmployeeID": e, "CoachID": coach_id, "ProgramName": emp_program})

    # ── run reflection LLM ───────────────────────────────────────────────
    throttle = Throttle(cfg.summary_throttle)
    tasks = [
        (_process_entry(inp, reasoning_client=reasoning_client, cfg=cfg), str(inp["EmployeeID"]))
        for inp in inputs
    ]
    outputs = await run_throttled(tasks, throttle)
    outputs = [o for o in outputs if o is not None]

    # ── build & write per-employee JSON reports ──────────────────────────
    for output in outputs:
        try:
            e = output["EmployeeID"]
            if output.get("status") != "ok":
                logger.critical("EmployeeID %s | status=%s", e, output.get("status"))
                continue

            # previous week's report
            prev_summary = {}
            prev_date = date_utc - timedelta(days=cfg.summary_lookback_days)
            if storage.exists(cfg.storage.summary_container, f"{prev_date}/{e}.json"):
                prev_summary = storage.read_json(cfg.storage.summary_container, f"{prev_date}/{e}.json")
            else:
                logger.warning("No previous summary for employee %s", e)

            coach_id = output["CoachID"]
            employee_name = employee_names.get(e)
            if not employee_name:
                emp_int = _coerce_int(e)
                employee_name = next(
                    (
                        ed.get("EmployeeName")
                        for ed in coach_mapping.get(str(_coerce_int(coach_id)), {}).get("employees", [])
                        if ed.get("EmployeeID") == emp_int and ed.get("EmployeeName")
                    ),
                    None,
                )
            # Fall back to a stable identifier (never fabricate a name).
            if not employee_name:
                employee_name = str(e)

            # Get input df for this employee
            dfe_input_data = next(inp for inp in inputs if inp["EmployeeID"] == e)
            dfe_input = dfe_input_data["df"]
            emp_program = dfe_input_data.get("ProgramName")
            dfe = _expand_struct_columns(dfe_input.clone(), cfg.expand_column_rules)
            dfe = _ensure_columns(dfe, column_defaults)

            # KPIs
            # KPIs
            kpis, comparisons = _compute_kpis(dfe, df, cfg, employee_list)
            reflection = output["query_response"]

            # ── Behavior scores aggregation (average across calls) ───────
            def _safe_mean(series):
                non_null = series.drop_nulls()
                return round(float(non_null.mean()), 2) if len(non_null) > 0 else 0.0

            behavior_scores = {}
            for label, col in cfg.behavior_score_keys:
                behavior_scores[label] = {
                    "score": _safe_mean(dfe[col]) if col in dfe.columns else 0.0,
                    "delta": None,
                }
            for label, score_col, occurred_col in cfg.behavior_count_keys:
                count = int(dfe[occurred_col].sum()) if occurred_col in dfe.columns else 0
                total = int(dfe[occurred_col].is_not_null().sum()) if occurred_col in dfe.columns else 0
                behavior_scores[label] = {
                    "score": _safe_mean(dfe[score_col]) if score_col in dfe.columns else 0.0,
                    "count": count,
                    "total": total,
                    "delta": None,
                }

            all_behavior_vals = [v["score"] for v in behavior_scores.values() if v["score"] is not None]
            overall_behavior_score = round(sum(all_behavior_vals) / len(all_behavior_vals), 2) if all_behavior_vals else 0.0

            # ── Soft skills aggregation ──────────────────────────────────
            call_handling_and_soft_skills = {}
            for label, col in cfg.soft_skill_keys:
                call_handling_and_soft_skills[label] = {
                    "score": _safe_mean(dfe[col]) if col in dfe.columns else 0.0,
                    "delta": None,
                }

            # ── New prospect aggregation ─────────────────────────────────
            if "new_prospect" in dfe.columns:
                np_total = int(dfe["new_prospect"].sum())
                np_converted = int(dfe["new_prospect_converted"].sum()) if "new_prospect_converted" in dfe.columns else 0
            else:
                np_total = 0
                np_converted = 0
            new_prospect = {
                "total": np_total,
                "converted": np_converted,
                "conversion_rate": round(np_converted / np_total, 2) if np_total > 0 else None,
            }

            # ── WCC behavior aggregation (mode-aware) ────────────────────
            wcc_kpi_entries = {}
            # Resolve which WCC keys to use: per-employee mode or cfg-level
            wcc_keys = cfg.wcc_behavior_keys
            if cfg.mode_configs and emp_program:
                emp_mode = get_mode_for_program(emp_program)
                mode_cfg = cfg.mode_configs.get(emp_mode)
                if mode_cfg:
                    wcc_keys = mode_cfg.wcc_behavior_keys
            if wcc_keys:
                for label, col_name in wcc_keys:
                    score = round(float(dfe[col_name].mean()), 2) if col_name in dfe.columns and len(dfe[col_name].drop_nulls()) > 0 else 0.0
                    wcc_kpi_entries[label] = {
                        "score": score,
                        "delta": None,
                    }

            # Resolve program mode for UI config routing
            resolved_mode = get_mode_for_program(emp_program) if emp_program else None

            out_json = {
                "date_utc": str(date_utc),
                "employeeId": e,
                "employeeName": employee_name,
                "programName": resolved_mode if resolved_mode and resolved_mode != "unknown" else None,
                "totalCallCount": len(dfe),
                "period": "weekly",
                "summary": reflection.get("overall_summary", ""),
                "kpis": kpis,
                "comparison": comparisons,
                "trends": {},
                "coaching_tips": [],
                "key_improvements": reflection.get("key_improvements", []),
                "behavior_scores": behavior_scores,
                "call_handling_and_soft_skills": call_handling_and_soft_skills,
                "overall_behavior_score": overall_behavior_score,
                "new_prospect": new_prospect,
            }

            if wcc_kpi_entries:
                out_json["wcc_behavior"] = wcc_kpi_entries

            # ── WCC Core KPIs (resolution / survival / right-of-sell) ────
            wcc_count_kpis = _build_wcc_count_kpis(dfe, storage)
            if wcc_count_kpis:
                out_json["wcc_count_kpis"] = wcc_count_kpis

            # deltas
            prev_kpis = {k["key"]: k["value"] for k in prev_summary.get("kpis", [])}
            for kpi in out_json["kpis"]:
                past = prev_kpis.get(kpi["key"])
                kpi["delta"] = (kpi["value"] - past) if past is not None else None

            # ── Deltas for behavior scores ───────────────────────────────
            prev_behavior = prev_summary.get("behavior_scores", {})
            for label, entry in out_json["behavior_scores"].items():
                prev_entry = prev_behavior.get(label, {})
                prev_score = prev_entry.get("score")
                if prev_score is not None:
                    entry["delta"] = round(entry["score"] - prev_score, 2)

            # ── Deltas for soft skills ───────────────────────────────────
            prev_soft = prev_summary.get("call_handling_and_soft_skills", {})
            for label, entry in out_json["call_handling_and_soft_skills"].items():
                prev_entry = prev_soft.get(label, {})
                prev_score = prev_entry.get("score")
                if prev_score is not None:
                    entry["delta"] = round(entry["score"] - prev_score, 2)

            # ── Deltas for WCC behavior ──────────────────────────────────
            if wcc_kpi_entries:
                prev_wcc = prev_summary.get("wcc_behavior", {})
                for label in out_json["wcc_behavior"]:
                    past_score = prev_wcc.get(label, {}).get("score")
                    if past_score is not None:
                        out_json["wcc_behavior"][label]["delta"] = round(out_json["wcc_behavior"][label]["score"] - past_score, 2)

            # ── Deltas for WCC Core KPIs ─────────────────────────────────
            if wcc_count_kpis:
                prev_wcc_count = prev_summary.get("wcc_count_kpis", [])
                for count_kpi in out_json["wcc_count_kpis"]:
                    past_score = next((item.get("score") for item in prev_wcc_count if item.get("key") == count_kpi["key"]), None)
                    if past_score is not None:
                        count_kpi["delta"] = round(count_kpi["score"] - past_score, 2)

            # ── Per-call KPI evidence (with supporting transcripts) ──────
            # For each evidence KPI, collect the calls where the flag fired with
            # their segment IDs AND transcript excerpts (capped), so both the
            # kpi_groups block and the kpi_evidence block can cite transcripts
            # exactly like the escalations section.
            kpi_evidence_map = {}
            if cfg.evidence_kpi_keys:
                for key, label, bool_col, seg_col in cfg.evidence_kpi_keys:
                    items = _collect_kpi_evidence_items(dfe, storage, bool_col, seg_col)
                    total = int(dfe[bool_col].is_not_null().sum()) if bool_col in dfe.columns else 0
                    count = int(dfe.filter(pl.col(bool_col) == True).height) if bool_col in dfe.columns else 0
                    kpi_evidence_map[key] = {
                        "key": key,
                        "label": label,
                        "count": count,
                        "total": total,
                        "items": items,
                    }

            # ── KPI groups block (flat PSO KPIs + supporting transcripts) ─
            # Every PSO KPI is emitted flat (not bucketed) with its weekly
            # score (count int or percentage), delta, and the supporting
            # transcript excerpts — so a separate kpi_evidence block is no
            # longer needed.
            kpi_groups_out = []
            for k in out_json["kpis"]:
                ev = kpi_evidence_map.get(k["key"], {})
                kpi_groups_out.append({
                    "key": k["key"],
                    "label": k["label"],
                    "score": k["value"],
                    "unit": k.get("unit", "percent"),
                    "items": ev.get("items", []),
                    "delta": k["delta"],
                })
            out_json["kpi_groups"] = kpi_groups_out

            sf = cfg.summary_fields
            get_url = lambda d: _get_url(cfg.storage.account_name, d)

            # ── config-driven report sections ────────────────────────────
            for section in cfg.report_sections:
                # Determine filter values: use section-level or fall back to cfg.sales_outcomes
                filter_values = section.filter_values
                if section.key == "sales_outcome" and not filter_values:
                    filter_values = cfg.sales_outcomes

                if section.or_columns:
                    # OR-based boolean filter (e.g. escalations)
                    items = _build_or_section(
                        dfe, section, sf, storage, get_url,
                    )
                    out_json[section.key] = items
                elif filter_values:
                    # Grouped by filter values (e.g. CX rating, sales outcome)
                    out_json[section.key] = {}
                    for val in filter_values:
                        items = _build_value_section(
                            dfe, section, sf, val, storage, get_url,
                        )
                        out_json[section.key][val] = {"count": len(items), "items": items}
                else:
                    out_json[section.key] = []

            # coaching tips from reflection
            for entry in reflection.get("coaching_tips", []):
                examples = []
                for example in entry.get("examples", []):
                    ref_id = str(example["reference_id"])
                    seg_ids = example["segment_ids"]
                    call = dfe.filter(pl.col("reference_id") == ref_id)
                    if len(call) == 0:
                        continue
                    call_date = call[0]["date_utc"].item()
                    call_cid = call[0]["contact_id"].item()
                    tags_col = sf.tags_column
                    call_tags = list(call[0][tags_col].item()) if tags_col in call.columns else []
                    summary_parts = []
                    for col in [sf.intent_column, sf.resolution_column]:
                        if col and col in call.columns:
                            summary_parts.append(call[col][0] or "")
                    summary_parts.append(example.get("explanation") or "")
                    ex_excerpt = _fetch_transcript(storage, call_date, call_cid, min(seg_ids) - 2, max(seg_ids) + 2)
                    examples.append({
                        "date_utc": call_date,
                        "contact_id": call_cid,
                        "summary": " ".join(summary_parts).strip(),
                        "tags": call_tags,
                        "transcript_url": get_url(call_date),
                        "transcript_ids": sorted(list(seg_ids)),
                        "transcript_excerpt": ex_excerpt,
                    })
                out_json["coaching_tips"].append({"tip": entry["tip"], "priority": entry["priority"], "expected_impact": entry.get("expected_impact", ""), "examples": examples})

            # trends
            for kpi in out_json["kpis"]:
                label = kpi["label"]
                out_json["trends"][label] = [{"x": "W-1", "y": kpi["value"]}]
                for i in range(1, cfg.trend_weeks):
                    week_i = date_utc - timedelta(days=7 * i)
                    path = f"{week_i}/{e}.json"
                    if storage.exists(cfg.storage.summary_container, path):
                        past_json = storage.read_json(cfg.storage.summary_container, path)
                        for kpi_past in past_json.get("kpis", []):
                            if kpi_past["label"] == label:
                                out_json["trends"][label].insert(0, {"x": f"W-{i + 1}", "y": kpi_past["value"]})
                                break

            # write
            storage.mkdir(cfg.storage.summary_container, str(date_utc))
            storage.write_json(out_json, cfg.storage.summary_container, f"{date_utc}/{e}.json")
            logger.info("Written summary for employee %s", e)

        except Exception as exc:
            logger.exception("Failed to build summary for employee: %s", exc)

    logger.info("=== SUMMARY END ===")
