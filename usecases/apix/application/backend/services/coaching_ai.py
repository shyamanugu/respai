"""
services/coaching_ai.py — Best-effort GPT coaching insights for the metrics page.
=================================================================================

Generates runtime coaching recommendations, risk areas and a short summary from
an employee's current-week and previous-week Azure SQL KPI values.

This is intentionally *best-effort*: the GPT credentials/endpoint are reused from
the chatbot configuration (``REASONING_MODEL_*``). If the SDK is missing, the
config is incomplete, or the endpoint rejects the call (e.g. a revoked key →
HTTP 401), every public function degrades gracefully to ``None`` so the UI can
render "N/A" placeholders without breaking the page.

The API key is never logged.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# Cap how long a single GPT call may block the Streamlit render (seconds).
_REQUEST_TIMEOUT = 20.0
# Cap completion length for the coaching JSON payload.
_MAX_COMPLETION_TOKENS = 4000


def _derive_base_url(endpoint: str) -> str:
    """Derive the ``/openai/v1/`` base URL from a resource/Foundry endpoint."""
    if not endpoint:
        return ""
    parsed = urlparse(endpoint)
    if parsed.scheme and parsed.netloc:
        return f"{parsed.scheme}://{parsed.netloc}/openai/v1/"
    return endpoint.rstrip("/") + "/openai/v1/"


def _resolve_llm_config() -> Optional[Dict[str, str]]:
    """Resolve ``{api_key, base_url, deployment}`` from the chatbot/app env.

    Prefers the chatbot ``.env`` values as a coherent set (key + endpoint +
    deployment belong to the same resource), falling back to the process env.
    Returns ``None`` when any required field is missing.
    """
    chatbot_values: Dict[str, str] = {}
    try:
        from dotenv import dotenv_values

        chatbot_env = Path(__file__).resolve().parents[3] / "chatbot" / ".env"
        if chatbot_env.is_file():
            chatbot_values = {k: v for k, v in dotenv_values(chatbot_env).items() if v}
    except Exception as exc:  # pragma: no cover - defensive
        logger.debug("coaching_ai → could not read chatbot/.env: %s", exc)

    def pick(name: str) -> str:
        return (chatbot_values.get(name) or os.getenv(name) or "").strip()

    api_key = pick("REASONING_MODEL_APIKEY")
    endpoint = pick("REASONING_MODEL_ENDPOINT")
    deployment = pick("REASONING_MODEL_DEPLOYMENT")
    base_url = pick("REASONING_MODEL_BASE_URL") or _derive_base_url(endpoint)

    if not (api_key and base_url and deployment):
        logger.info("coaching_ai → GPT config incomplete; coaching insights disabled")
        return None
    return {"api_key": api_key, "base_url": base_url, "deployment": deployment}


def _build_metric_rows(current: dict, previous: dict) -> List[dict]:
    """Build ``[{metric, current, previous, delta}]`` from SQL value dicts."""
    rows: List[dict] = []
    for metric, value in (current or {}).items():
        if not isinstance(value, (int, float)):
            continue
        prev = previous.get(metric) if isinstance(previous, dict) else None
        delta = round(value - prev, 2) if isinstance(prev, (int, float)) else None
        rows.append(
            {
                "metric": metric,
                "current": round(float(value), 2),
                "previous": round(float(prev), 2) if isinstance(prev, (int, float)) else None,
                "delta": delta,
            }
        )
    return rows


def _build_messages(
    employee_id: str,
    metric_rows: List[dict],
    priority: Optional[List[dict]] = None,
) -> List[dict]:
    """Assemble the chat messages for the coaching request.

    When *priority* is provided (an ordered list of ``{"area", "metrics"}``
    themes), the model is instructed to prioritise its summary, tips and risks
    in that order (most important first).
    """
    system = (
        "You are an expert contact-center performance coach. You receive an "
        "agent's weekly KPI values (current week, previous week, and the "
        "week-over-week delta). Produce concise, specific, actionable coaching. "
        "Respond with STRICT JSON only (no markdown), matching this schema: "
        '{"summary": string, '
        '"tips": [{"tip": string, "priority": "High"|"Medium"|"Low", '
        '"expected_impact": string, "actionable_steps": [string]}], '
        '"risks": [string]}. '
        "Base every statement on the provided numbers. Prefer 2-4 tips and 1-3 "
        "risks. If a higher value is clearly bad for a metric (e.g. disconnects, "
        "AHT, wait/hold time), treat increases as regressions."
    )

    priority_lines: List[str] = []
    for idx, item in enumerate(priority or [], 1):
        area = (item.get("area") or item.get("key") or "").strip()
        if not area:
            continue
        metrics = ", ".join(m for m in (item.get("metrics") or []) if m)
        priority_lines.append(
            f"{idx}. {area}" + (f" (KPIs: {metrics})" if metrics else "")
        )
    if priority_lines:
        system += (
            " Prioritise the summary, tips and risks in THIS ORDER (most "
            "important first): " + "; ".join(priority_lines) + ". Lead with the "
            "highest-priority area and address lower-priority areas only when "
            "their numbers clearly warrant attention."
        )

    user = (
        f"Agent ID: {employee_id}\n"
        f"KPI values (JSON):\n{json.dumps(metric_rows, indent=2)}\n\n"
        "Generate the coaching JSON now."
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def _parse_payload(text: str) -> Optional[dict]:
    """Parse the model's reply into the coaching dict, tolerating code fences."""
    if not text:
        return None
    cleaned = text.strip()
    if cleaned.startswith("```"):
        # Strip ```json ... ``` fences.
        cleaned = cleaned.split("```", 2)
        cleaned = cleaned[1] if len(cleaned) > 1 else text
        if cleaned.lstrip().lower().startswith("json"):
            cleaned = cleaned.lstrip()[4:]
        cleaned = cleaned.strip()

    try:
        data = json.loads(cleaned)
    except Exception:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                data = json.loads(cleaned[start : end + 1])
            except Exception:
                return {"summary": cleaned[:1500], "tips": [], "risks": []}
        else:
            return {"summary": cleaned[:1500], "tips": [], "risks": []}

    if not isinstance(data, dict):
        return None
    return {
        "summary": data.get("summary", "") or "",
        "tips": data.get("tips", []) if isinstance(data.get("tips"), list) else [],
        "risks": data.get("risks", []) if isinstance(data.get("risks"), list) else [],
    }


def generate_coaching_insights(
    employee_id: str,
    current: dict,
    previous: dict,
    program_id: Optional[str] = None,
    priority: Optional[List[dict]] = None,
) -> Optional[dict]:
    """Return ``{summary, tips, risks}`` coaching insights, or ``None`` on failure.

    Best-effort: returns ``None`` when GPT is unavailable (missing SDK/config) or
    the call fails for any reason (network, 401, parsing). Never raises.

    *priority* is an optional ordered list of ``{"area", "metrics"}`` themes used
    to steer the summary/tips/risks toward the highest-priority areas first.
    """
    metric_rows = _build_metric_rows(current, previous)
    if not metric_rows:
        return None

    config = _resolve_llm_config()
    if not config:
        return None

    try:
        from openai import OpenAI
    except Exception as exc:  # pragma: no cover - SDK not installed
        logger.info("coaching_ai → openai SDK unavailable: %s", exc)
        return None

    messages = _build_messages(employee_id, metric_rows, priority)

    # LLMOps: guardrail + model routing + tracing (fail-open — never breaks the
    # best-effort coaching call; degrades to plain behaviour if platform absent).
    _llmops = None
    _guardrail = None
    _alias = "reason"
    _deployment = config["deployment"]
    _reason_in = ""
    _t0 = time.perf_counter()
    try:
        from backend import llmops as _llmops

        _env = _llmops.current_env()
        _guardrail = _llmops.get_guardrail(_llmops.USECASE, _env)
        _allowed_in, _reason_in = _llmops.check_input(
            _guardrail, "\n".join(m.get("content", "") for m in messages)
        )
        _alias = _llmops.alias_for_step(_llmops.current_step())
        _deployment = _llmops.resolve_deployment(
            _alias, fallback=config["deployment"], environment=_env
        )
        if not _allowed_in:
            _llmops.record_step_event(
                step="coaching", alias=_alias, deployment=_deployment, status="skipped",
                guardrail_allowed=False, guardrail_reason=_reason_in,
            )
            logger.warning("coaching_ai → blocked by guardrail: %s", _reason_in)
            return None
    except Exception:
        _llmops = None

    try:
        client = OpenAI(
            base_url=config["base_url"],
            api_key=config["api_key"],
            timeout=_REQUEST_TIMEOUT,
            max_retries=0,
        )

        # gpt-5.x reasoning models use max_completion_tokens + reasoning_effort.
        base_kwargs = {
            "model": _deployment,
            "messages": messages,
            "max_completion_tokens": _MAX_COMPLETION_TOKENS,
        }
        _t0 = time.perf_counter()
        try:
            response = client.chat.completions.create(
                **base_kwargs,
                reasoning_effort="minimal",
                response_format={"type": "json_object"},
            )
        except TypeError:
            # Older SDK signature without reasoning_effort/response_format.
            response = client.chat.completions.create(**base_kwargs)

        content = (response.choices[0].message.content or "").strip() if response.choices else ""

        # LLMOps: record the traced StepEvent (+cost) — fail-open.
        if _llmops is not None:
            try:
                _usage = getattr(response, "usage", None)
                _allowed_out, _reason_out = _llmops.check_output(_guardrail, content)
                _llmops.record_step_event(
                    step="coaching", alias=_alias, deployment=_deployment,
                    input_tokens=int(getattr(_usage, "prompt_tokens", 0) or 0),
                    output_tokens=int(getattr(_usage, "completion_tokens", 0) or 0),
                    latency_ms=(time.perf_counter() - _t0) * 1000.0, status="ok",
                    guardrail_allowed=_allowed_out,
                    guardrail_reason="; ".join(r for r in (_reason_in, _reason_out) if r),
                )
            except Exception:
                pass

        insights = _parse_payload(content)
        if insights and (insights.get("summary") or insights.get("tips") or insights.get("risks")):
            logger.info("coaching_ai → generated coaching insights for employee %s", employee_id)
            return insights
        return None
    except Exception as exc:
        # Includes auth (401) failures from a revoked key — degrade to N/A.
        logger.warning("coaching_ai → coaching generation failed: %s", type(exc).__name__)
        return None
