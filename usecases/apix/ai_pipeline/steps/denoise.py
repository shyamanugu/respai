"""Denoise step — cleans raw transcripts via LLM."""

from __future__ import annotations

import asyncio
import json
from datetime import date

import polars as pl
from openai import AsyncOpenAI

from ai_pipeline.programs_config.base import PipelineConfig, Transcript
from ai_pipeline.logging_config import get_logger
from ai_pipeline.services import query, Status, get_timestamp
from ai_pipeline.utils import retry_async, validate_transcript, agent_word_fraction, customer_word_fraction, get_mode_for_program, build_program_filter_sql, ContentFilterSkip
from ai_pipeline.utils.throttle import Throttle, run_throttled
from ai_pipeline.utils.coach_mapping import enrich_frame
from ai_pipeline.services.storage import StorageService
from ai_pipeline.services.sql import SqlService

logger = get_logger("steps.denoise")


async def _process_entry(row, *, chat_client: AsyncOpenAI, cfg: PipelineConfig):
    output = {}

    def _get(col, default=None):
        """Safely read a column value; some programs omit optional columns."""
        return row[col].item() if col in row.columns else default

    try:
        output["CoachID"] = _get("CoachID")
        output["CoachName"] = _get("CoachName")
        output["contact_id"] = _get("contact_id")
        output["EmployeeID"] = _get("EmployeeID")
        output["ProgramName"] = _get("ProgramName")
        output["totalcalltime"] = _get("totalcalltime")
        output["totalholdtime"] = _get("totalholdtime")
        output["agent_word_frac"] = agent_word_fraction(row["full_text"].item())
        output["customer_word_frac"] = customer_word_fraction(row["full_text"].item())
        output["start_timestamp"] = get_timestamp()
        output["error_message"] = None
        full_text = row["full_text"].item()

        _query = retry_async(cfg.max_retries, cfg.retry_delay)(query)
        result = await _query(
            client=chat_client,
            model=cfg.openai.chat_deployment,
            system_prompt=cfg.denoise_system_prompt,
            user_prompt=full_text,
            temperature=cfg.openai.denoise_temperature,
            schema=Transcript,
            max_completion_tokens=cfg.openai.max_completion_tokens,
        )

        output["end_timestamp"] = result["timestamp"]
        # Content filter / non-retryable failures are classified by the query
        # layer — surface their status without trying to parse a transcript.
        if result["status"] != Status.OK.value:
            output["status"] = result["status"]
            output["error_message"] = result.get("message")
            output["prompt_filters"] = result.get("prompt_filters")
            return output

        message = result["message"]
        if isinstance(message, str):
            # Structured output may arrive as a raw JSON string rather than a
            # parsed dict — decode it before extracting the transcript.
            try:
                message = json.loads(message)
            except (json.JSONDecodeError, TypeError) as exc:
                raise ValueError("Model did not return valid JSON transcript") from exc
        transcript = message.get("transcript") if isinstance(message, dict) else message
        if not isinstance(transcript, list):
            raise ValueError("Model response missing a valid 'transcript' list")
        output["denoised_text"] = validate_transcript(transcript)
        output["status"] = result["status"]
        output["denoise_tokens"] = result["total_tokens"]
        output["prompt_filters"] = result["prompt_filters"]

    except ContentFilterSkip:
        logger.warning("Skipped row %s (content filter)", output.get("contact_id"))
        output["status"] = Status.SKIPPED.value
        output["error_message"] = "content filter"

    except Exception as e:
        logger.exception("Error processing row: %s", e)
        output["status"] = Status.ERROR.value
        output["error_message"] = str(e)

    return output


async def run_denoise(date_utc: date, cfg: PipelineConfig, storage: StorageService) -> None:
    """Fetch raw transcripts, denoise, write results."""
    logger.info("=== DENOISE START | date=%s program=%s ===", date_utc, cfg.program_id)

    chat_client = AsyncOpenAI(
        base_url=cfg.openai.chat_endpoint,
        api_key=cfg.openai.chat_api_key,
    )

    if cfg.mode:
        where = build_program_filter_sql(cfg.mode)
    elif cfg.program_filter:
        filters = cfg.program_filter if isinstance(cfg.program_filter, (list, tuple)) else [cfg.program_filter]
        escaped = ", ".join(f"'{n}'" for n in filters)
        where = f"ProgramName IN ({escaped})"
    else:
        where = None

    # EmployeeID (agent) allow-list can be applied in-SQL — raw carries EmployeeID.
    # CoachID is NOT present in raw; it is enriched from Azure SQL below and then
    # filtered in-memory, so it must not go into the parquet WHERE clause.
    if cfg.agent_filter:
        agent_ids = ", ".join(str(int(a)) for a in cfg.agent_filter)
        agent_clause = f"EmployeeID IN ({agent_ids})"
        where = f"({where}) AND {agent_clause}" if where else agent_clause
        logger.info("Agent filter active | EmployeeID IN (%s)", agent_ids)

    df = storage.read_parquet_sql(cfg.storage.raw_container, f"{date_utc}.parquet", where=where)
    if df.is_empty():
        logger.warning(
            "No rows matched filter [%s] in %s.parquet - nothing to denoise (writing empty output)",
            where or "(all)", date_utc,
        )
    else:
        logger.info("Loaded %d raw rows (filter: %s)", len(df), where or "(all)")

    # Enrich raw rows with CoachID / CoachName from Azure SQL BEFORE denoising
    # (agent_pbxid -> dim_angel.SourceID -> dim_employee_hcht). Best-effort:
    # when SQL is unavailable the coach columns are nulled and all coaches run.
    if not df.is_empty():
        df = enrich_frame(df, SqlService(cfg.azure_sql))
        # Coach allow-list is applied in-memory (raw has no CoachID to filter in SQL).
        if cfg.coach_filter:
            coach_ids = [int(c) for c in cfg.coach_filter]
            before = len(df)
            df = df.filter(pl.col("CoachID").is_in(coach_ids))
            logger.info(
                "Coach filter active | CoachID IN (%s) | %d -> %d rows",
                ", ".join(str(c) for c in coach_ids), before, len(df),
            )
        else:
            logger.info("No coach filter — denoising all coaches.")

    throttle = Throttle(cfg.denoise_throttle)
    rows = [df[i] for i in range(len(df))]
    tasks = [
        (_process_entry(row, chat_client=chat_client, cfg=cfg), str(row["contact_id"].item()))
        for row in rows
    ]
    responses = await run_throttled(tasks, throttle)

    ok = [r for r in responses if r is not None and r.get("status") == "ok"]
    skipped = [r for r in responses if r is not None and r.get("status") == Status.SKIPPED.value]
    errors = [r for r in responses if r is not None and r.get("status") == Status.ERROR.value]
    logger.info(
        "Denoise complete | ok=%d skipped=%d errors=%d", len(ok), len(skipped), len(errors)
    )
    storage.write_parquet(pl.DataFrame(ok), cfg.storage.denoised_container, f"{date_utc}.parquet")
    logger.info("=== DENOISE END ===")
