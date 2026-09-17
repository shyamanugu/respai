"""The instrumented wrapper around the pipeline's LLM choke point.

``services.query`` delegates here. This adds — all fail-open, domain logic
unchanged — model-alias resolution, input/output guardrails, and a traced
StepEvent (tokens + cost + latency) per call.
"""
from __future__ import annotations

import json
import time

from . import context
from .guardrails_gate import check_input, check_output, get_guardrail
from .observability import record_step_event


def resolve_deployment(alias: str, fallback: str, environment: str) -> str:
    """Resolve a model alias to a deployment via the platform, falling back to
    the caller-supplied deployment when the alias is unprovisioned or the
    platform is unavailable."""
    try:
        from model_management.model_router import resolve

        handle = resolve(alias, environment)
        deployment = getattr(handle, "deployment", None)
        return deployment or fallback
    except Exception:
        return fallback


def _extract_output_text(result) -> str:
    try:
        msg = result.get("message") if isinstance(result, dict) else None
        if isinstance(msg, str):
            return msg
        if msg is not None:
            return json.dumps(msg, default=str)
    except Exception:
        pass
    return ""


async def instrumented_query(
    impl,
    *,
    client,
    user_prompt: str,
    system_prompt: str,
    model: str,
    temperature: float = 1.0,
    schema=None,
    max_completion_tokens: int | None = None,
    max_token_retries: int = 2,
):
    step = context.current_step()
    alias = context.alias_for_step(step)
    env = context.current_env()
    usecase = context.current_usecase()

    # 1. Model routing: alias -> deployment (fail-open to the passed model).
    deployment = resolve_deployment(alias, fallback=model, environment=env)

    # 2. Input guardrail. Only a genuine block (e.g. secret leak) short-circuits;
    #    flags (PII) are recorded but never drop a call.
    guardrail = get_guardrail(usecase, env)
    allowed_in, reason_in = check_input(guardrail, f"{system_prompt}\n{user_prompt}")
    guardrail_reason = reason_in
    if not allowed_in:
        record_step_event(
            step=step, alias=alias, deployment=deployment,
            latency_ms=0.0, status="skipped",
            guardrail_allowed=False, guardrail_reason=reason_in,
        )
        from ai_pipeline.services import Response, Status

        return Response(
            status=Status.SKIPPED.value, message="guardrail blocked input", prompt_filters=[]
        ).model_dump()

    # 3. Call the real implementation (timed).
    t0 = time.perf_counter()
    try:
        result = await impl(
            client=client, user_prompt=user_prompt, system_prompt=system_prompt,
            model=deployment, temperature=temperature, schema=schema,
            max_completion_tokens=max_completion_tokens, max_token_retries=max_token_retries,
        )
    except Exception as exc:
        latency_ms = (time.perf_counter() - t0) * 1000.0
        record_step_event(
            step=step, alias=alias, deployment=deployment, latency_ms=latency_ms,
            status="error", guardrail_allowed=True, guardrail_reason=guardrail_reason,
            error=repr(exc),
        )
        raise
    latency_ms = (time.perf_counter() - t0) * 1000.0

    # 4. Output guardrail (flag-only for apix — record, don't drop).
    allowed_out, reason_out = check_output(guardrail, _extract_output_text(result))
    if reason_out:
        guardrail_reason = "; ".join(r for r in (guardrail_reason, reason_out) if r)

    # 5. Record the traced StepEvent (+cost).
    status = result.get("status", "ok") if isinstance(result, dict) else "ok"
    record_step_event(
        step=step, alias=alias, deployment=deployment,
        input_tokens=(result.get("prompt_tokens", 0) if isinstance(result, dict) else 0),
        output_tokens=(result.get("completion_tokens", 0) if isinstance(result, dict) else 0),
        latency_ms=latency_ms, status=status,
        guardrail_allowed=bool(allowed_in and allowed_out), guardrail_reason=guardrail_reason,
    )
    return result
