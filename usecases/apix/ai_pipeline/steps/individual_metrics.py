"""Individual-metrics step.

Enriches each employee's weekly-summary JSON (already written to blob storage by
the ``summary`` step) with Azure SQL driven coaching recommendations and key
improvement areas — pre-computed here so the dashboard app no longer needs to
call the reasoning model at request time.

For every employee JSON under ``summary_container/<date>/``:

    1. Read the existing employee JSON from blob storage for the period.
    2. Run the program's individual-metric queries against Azure SQL for the
       current and previous windows (same queries the app uses at runtime).
    3. Prompt the reasoning model with the metric data to generate coaching
       recommendations and key improvement areas.
    4. Add the result under the ``individual_metrics`` key and upload the JSON
       back to the same blob path (non-destructive — all existing keys kept).
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Dict, List, Optional

from openai import AsyncOpenAI
from pydantic import BaseModel, Field

from ai_pipeline.logging_config import get_logger
from ai_pipeline.programs_config.base import PipelineConfig
from ai_pipeline.services import query
from ai_pipeline.services.sql import SqlService
from ai_pipeline.services.storage import StorageService
from ai_pipeline.utils import retry_async

logger = get_logger("steps.individual_metrics")


# ── Structured coaching response schema ──────────────────────────────────────

class CoachingTip(BaseModel):
    tip: str
    priority: str = "Medium"  # High | Medium | Low
    expected_impact: str = ""
    actionable_steps: List[str] = Field(default_factory=list)
    examples: List[str] = Field(default_factory=list)


class IndividualCoachingResponse(BaseModel):
    summary: str = ""
    tips: List[CoachingTip] = Field(default_factory=list)
    risks: List[str] = Field(default_factory=list)
    key_improvements: List[str] = Field(default_factory=list)


COACHING_SYSTEM_PROMPT = (
    "You are a performance coaching assistant for contact-center sales agents. "
    "You are given an agent's recent performance metrics with the current-period "
    "value, previous-period value and the delta between them.\n\n"
    "Analyze the metrics and respond with STRICT JSON matching this schema:\n"
    "{\n"
    '  "summary": string — 2-3 sentence overview of the agent\'s performance,\n'
    '  "tips": [\n'
    "    {\n"
    '      "tip": string — a concrete coaching recommendation,\n'
    '      "priority": "High" | "Medium" | "Low",\n'
    '      "expected_impact": string — the metric/outcome this should improve,\n'
    '      "actionable_steps": [string, ...] — specific steps the agent can take,\n'
    '      "examples": [string, ...] — short illustrative examples (may be empty)\n'
    "    }\n"
    "  ],\n"
    '  "risks": [string, ...] — metrics or behaviors trending in the wrong direction,\n'
    '  "key_improvements": [string, ...] — the most important areas to improve next\n'
    "}\n\n"
    "Base every statement on the provided metrics. Do not invent metrics that were "
    "not provided. Return JSON only — no prose outside the JSON object."
)


# ── Metric-name matching helpers (ported from the dashboard app) ─────────────

def _normalize_metric_name(value: Optional[str]) -> str:
    return re.sub(r"[^a-z0-9]+", "", (value or "").lower())


def _canonical_metric_name(metric_desc: Optional[str], norm_map: Dict[str, str]) -> Optional[str]:
    """Map a raw ``MetricDesc`` back to the requested canonical key."""
    norm = _normalize_metric_name(metric_desc)
    if not norm:
        return None
    if norm in norm_map:
        return norm_map[norm]
    for key_norm, canonical in norm_map.items():
        if key_norm and (key_norm in norm or norm in key_norm):
            return canonical
    return None


def _sql_quote(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _build_query(
    template: str,
    employee_id: str,
    metric_keys: List[str],
    start_date: date,
    end_date: date,
) -> str:
    employee_clause = _sql_quote(employee_id)
    metric_clause = ", ".join(_sql_quote(k) for k in metric_keys)
    return template.format(
        employee_ids=employee_clause,
        employee_id=employee_clause,
        metric_keys=metric_clause,
        start_date=start_date.strftime("%Y-%m-%d"),
        end_date=end_date.strftime("%Y-%m-%d"),
    )


def _window_bounds(end_date: date, window_days: int):
    """Return (current_start, current_end, prev_start, prev_end).

    The current window is ``window_days`` days ending (inclusive) at ``end_date``;
    the previous window is the equally sized window immediately before it.
    """
    span = max(window_days - 1, 0)
    current_start = end_date - timedelta(days=span)
    current_end = end_date
    prev_end = current_start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=span)
    return current_start, current_end, prev_start, prev_end


def _fetch_window(
    sql_service: SqlService,
    employee_id: str,
    cfg: PipelineConfig,
    start_date: date,
    end_date: date,
) -> Dict[str, float]:
    """Fetch average metric values for one window, keyed by canonical name."""
    # Group requested metric keys by the query that serves them.
    by_query: Dict[str, List[str]] = {}
    for _group_name, query_name, keys in cfg.individual_metric_groups:
        by_query.setdefault(query_name, []).extend(keys)

    avg_values: Dict[str, float] = {}
    for query_name, keys in by_query.items():
        template = cfg.individual_metric_queries.get(query_name)
        if not template:
            logger.warning("No query template named '%s' — skipping", query_name)
            continue
        # De-duplicate keys while preserving order.
        seen = set()
        unique_keys = [k for k in keys if not (k in seen or seen.add(k))]
        if not unique_keys:
            continue
        norm_map = {_normalize_metric_name(k): k for k in unique_keys}
        sql = _build_query(template, employee_id, unique_keys, start_date, end_date)
        try:
            _columns, rows = sql_service.run_query(sql)
        except Exception as exc:  # noqa: BLE001 — one bad query shouldn't kill the run
            logger.warning("Metric query '%s' failed for %s: %s", query_name, employee_id, exc)
            continue
        # Rows: (EmployeeID, MetricDesc, AvgValue, SumValue)
        for row in rows:
            if len(row) < 3:
                continue
            metric_desc, avg_value = row[1], row[2]
            canonical = _canonical_metric_name(metric_desc, norm_map)
            if canonical is not None and avg_value is not None:
                try:
                    avg_values[canonical] = float(avg_value)
                except (TypeError, ValueError):
                    continue
    return avg_values


def _build_metric_rows(current: Dict[str, float], previous: Dict[str, float]) -> List[dict]:
    rows: List[dict] = []
    for metric in sorted(set(current) | set(previous)):
        cur = current.get(metric)
        prev = previous.get(metric)
        delta = None
        if cur is not None and prev is not None:
            delta = round(cur - prev, 4)
        rows.append({"metric": metric, "current": cur, "previous": prev, "delta": delta})
    return rows


def _build_user_prompt(employee_id: str, metric_rows: List[dict]) -> str:
    lines = [f"Agent ID: {employee_id}", "", "Metrics (metric | current | previous | delta):"]
    for r in metric_rows:
        cur = "n/a" if r["current"] is None else round(r["current"], 4)
        prev = "n/a" if r["previous"] is None else round(r["previous"], 4)
        delta = "n/a" if r["delta"] is None else r["delta"]
        lines.append(f"- {r['metric']} | {cur} | {prev} | {delta}")
    return "\n".join(lines)


async def _generate_coaching(
    employee_id: str,
    metric_rows: List[dict],
    *,
    reasoning_client: AsyncOpenAI,
    cfg: PipelineConfig,
) -> Optional[dict]:
    """Call the reasoning model and return the parsed coaching dict, or None."""
    _query = retry_async(cfg.max_retries, cfg.retry_delay)(query)
    response = await _query(
        client=reasoning_client,
        model=cfg.openai.reasoning_deployment,
        system_prompt=COACHING_SYSTEM_PROMPT,
        user_prompt=_build_user_prompt(employee_id, metric_rows),
        temperature=cfg.openai.analyze_temperature,
        schema=IndividualCoachingResponse,
        max_completion_tokens=cfg.openai.max_completion_tokens,
    )
    if response.get("status") != "ok":
        logger.warning("Coaching generation failed for %s: %s", employee_id, response.get("message"))
        return None
    return response.get("message")


# ── main entry point ─────────────────────────────────────────────────────────

async def run_individual_metrics(date_utc: date, cfg: PipelineConfig, storage: StorageService) -> None:
    logger.info(
        "=== INDIVIDUAL METRICS START | date=%s program=%s mode=%s ===",
        date_utc,
        cfg.program_id,
        cfg.mode,
    )

    sql_service = SqlService(cfg.azure_sql)
    if not sql_service.is_configured():
        logger.warning("Azure SQL not configured — skipping individual metrics step")
        return

    summary_container = cfg.storage.summary_container
    filenames = storage.list_files(summary_container, str(date_utc), suffix=".json")
    if not filenames:
        logger.warning("No employee summaries found under %s/%s", summary_container, date_utc)
        return
    logger.info("Enriching %d employee summaries", len(filenames))

    reasoning_client = AsyncOpenAI(
        base_url=cfg.openai.reasoning_endpoint,
        api_key=cfg.openai.reasoning_api_key,
    )

    window_days = cfg.individual_metric_window_days
    cur_start, cur_end, prev_start, prev_end = _window_bounds(date_utc, window_days)

    processed = 0
    for filename in filenames:
        blob_path = f"{date_utc}/{filename}"
        try:
            employee_json = storage.read_json(summary_container, blob_path)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not read %s: %s", blob_path, exc)
            continue

        employee_id = employee_json.get("employeeId")
        if employee_id is None:
            employee_id = filename[:-5] if filename.endswith(".json") else filename
        employee_id = str(employee_id)

        try:
            current = _fetch_window(sql_service, employee_id, cfg, cur_start, cur_end)
            previous = _fetch_window(sql_service, employee_id, cfg, prev_start, prev_end)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Metric fetch failed for %s: %s", employee_id, exc)
            continue

        metric_rows = _build_metric_rows(current, previous)
        if not metric_rows:
            logger.info("No metric data for %s — skipping", employee_id)
            continue

        coaching = await _generate_coaching(
            employee_id, metric_rows, reasoning_client=reasoning_client, cfg=cfg
        )
        if coaching is None:
            continue

        employee_json["individual_metrics"] = {
            "coaching": {
                "summary": coaching.get("summary", ""),
                "tips": coaching.get("tips", []),
                "risks": coaching.get("risks", []),
            },
            "key_improvements": coaching.get("key_improvements", []),
            "metrics": {
                "current": current,
                "previous": previous,
            },
        }

        try:
            storage.write_json(employee_json, summary_container, blob_path)
            processed += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not write enriched JSON for %s: %s", employee_id, exc)

    logger.info(
        "=== INDIVIDUAL METRICS DONE | date=%s | enriched=%d/%d ===",
        date_utc,
        processed,
        len(filenames),
    )
