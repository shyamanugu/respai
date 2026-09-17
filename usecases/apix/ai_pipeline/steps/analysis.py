"""Analysis step — evaluates denoised transcripts via LLM."""

from __future__ import annotations

import asyncio
from datetime import date

import polars as pl
from openai import AsyncOpenAI

from ai_pipeline.programs_config.base import PipelineConfig
from ai_pipeline.logging_config import get_logger
from ai_pipeline.services import query, Status, get_timestamp
from ai_pipeline.utils import retry_async, get_mode_for_program, build_program_filter_sql
from ai_pipeline.utils.throttle import Throttle, run_throttled
from ai_pipeline.services.storage import StorageService

logger = get_logger("steps.analysis")


def _resolve_prompt_and_schema(program_name: str, cfg: PipelineConfig):
    """Resolve the correct prompt and schema for a row based on ProgramName.

    In single-program mode, always returns cfg's prompt/schema.
    In multi-mode, looks up the mode from env mapping and returns
    the corresponding mode config's prompt/schema.

    Returns (system_prompt, schema) or (None, None) if unknown.
    """
    if not cfg.mode_configs:
        # Single program mode — use cfg directly
        return cfg.analysis_system_prompt, cfg.analysis_schema

    row_mode = get_mode_for_program(program_name)
    if row_mode == "unknown":
        return None, None

    mode_cfg = cfg.mode_configs.get(row_mode)
    if mode_cfg is None:
        return None, None

    return mode_cfg.analysis_system_prompt, mode_cfg.analysis_schema


async def _process_entry(row, *, reasoning_client: AsyncOpenAI, cfg: PipelineConfig):
    output = {}
    try:
        output["CoachID"] = row["CoachID"].item()
        output["contact_id"] = row["contact_id"].item()
        output["EmployeeID"] = row["EmployeeID"].item()
        output["ProgramName"] = row["ProgramName"].item()
        output["analysis_start_timestamp"] = get_timestamp()
        output["error_message"] = None
        denoised_text = row["denoised_text"].item()

        # Resolve mode-specific prompt and schema for this row
        system_prompt, schema = _resolve_prompt_and_schema(output["ProgramName"], cfg)
        if system_prompt is None:
            logger.warning(
                "Skipping contact_id=%s — ProgramName '%s' not mapped in .env",
                output["contact_id"], output["ProgramName"],
            )
            output["analysis_status"] = "skipped"
            output["error_message"] = f"ProgramName '{output['ProgramName']}' not mapped in .env"
            return output

        _query = retry_async(cfg.max_retries, cfg.retry_delay)(query)
        result = await _query(
            client=reasoning_client,
            model=cfg.openai.reasoning_deployment,
            system_prompt=system_prompt,
            user_prompt=str(denoised_text),
            temperature=cfg.openai.analyze_temperature,
            schema=schema,
            max_completion_tokens=cfg.openai.max_completion_tokens,
        )

        output["analysis_end_timestamp"] = result["timestamp"]
        output["analysis_status"] = result["status"]
        output["analysis_response"] = result["message"]
        output["analysis_tokens"] = result["total_tokens"]

    except Exception as e:
        logger.exception("Error processing row: %s", e)
        output["analysis_status"] = Status.ERROR.value
        output["error_message"] = str(e)

    return output


async def run_analysis(date_utc: date, cfg: PipelineConfig, storage: StorageService) -> None:
    """Read denoised transcripts, run analysis, write results."""
    logger.info("=== ANALYSIS START | date=%s program=%s mode=%s ===", date_utc, cfg.program_id, cfg.mode)

    reasoning_client = AsyncOpenAI(
        base_url=cfg.openai.reasoning_endpoint,
        api_key=cfg.openai.reasoning_api_key,
    )

    # Build WHERE clause: mode-based filter or single program filter
    if cfg.mode:
        where = build_program_filter_sql(cfg.mode)
        where = f"status = 'ok' AND {where}" if where else "status = 'ok'"
    else:
        where = "status = 'ok'"
    df = storage.read_parquet_sql(cfg.storage.denoised_container, f"{date_utc}.parquet", where=where)
    logger.info("Loaded %d denoised rows (filter: %s)", len(df), where)

    throttle = Throttle(cfg.analysis_throttle)
    rows = [df[i] for i in range(len(df))]
    tasks = [
        (_process_entry(row, reasoning_client=reasoning_client, cfg=cfg), str(row["contact_id"].item()))
        for row in rows
    ]
    responses = await run_throttled(tasks, throttle)

    ok = [r for r in responses if r is not None and r.get("analysis_status") == "ok"]
    skipped = [r for r in responses if r is not None and r.get("analysis_status") == "skipped"]
    logger.info("Analysis complete | ok=%d skipped=%d errors=%d", len(ok), len(skipped), len(responses) - len(ok) - len(skipped))
    storage.write_parquet(
        pl.DataFrame(ok, infer_schema_length=None),
        cfg.storage.analysis_container,
        f"{date_utc}.parquet",
    )
    logger.info("=== ANALYSIS END ===")
