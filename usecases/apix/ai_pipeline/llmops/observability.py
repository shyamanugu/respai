"""Tracing + cost sink for APIX ai_pipeline LLM calls.

Emits one ``StepEvent`` per LLM call and one ``PipelineEvent`` per run through a
platform ``Tracer``. Default sink is ``jsonl`` (a local file the ops-console
backend tails) so observability works with zero Azure. Everything here is
fail-open: if the platform is absent or a sink write fails, the pipeline runs on.
"""
from __future__ import annotations

import dataclasses
import json
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from . import context

_tracer = None
_lock = threading.Lock()
_totals = {"cost": 0.0, "latency": 0.0, "steps": 0}


# ── Local fallbacks so tracing works even if the platform tree is missing ────
@dataclass
class _StepEventLike:
    session_id: str
    step_name: str
    model_alias: str | None = None
    provider: str | None = None
    deployment: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: float = 0.0
    guardrail_allowed: bool = True
    guardrail_reason: str = ""
    error: str | None = None


@dataclass
class _PipelineEventLike:
    session_id: str
    pipeline_name: str
    step_count: int
    total_cost_usd: float = 0.0
    total_latency_ms: float = 0.0
    error: str | None = None


class _NoopTracer:
    def record_step(self, event) -> None:  # noqa: D401
        pass

    def record_pipeline(self, event) -> None:
        pass


class _JsonlTracer:
    """Append each event as one JSON line, enriched with run/program/env/usecase
    attribution so the ops-console can filter without extra joins."""

    def __init__(self, path: str) -> None:
        self.path = Path(path)
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass

    def _write(self, kind: str, event) -> None:
        try:
            row = {
                "kind": kind,
                "ts": time.time(),
                "usecase": context.current_usecase(),
                "app": "ai_pipeline",
                "program": context.current_program(),
                "env": context.current_env(),
                **dataclasses.asdict(event),
            }
            line = json.dumps(row, default=str)
            with _lock:
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
        except Exception:
            pass

    def record_step(self, event) -> None:
        self._write("step", event)

    def record_pipeline(self, event) -> None:
        self._write("pipeline", event)


def init_tracer():
    """Pick a tracer from ``LLMOPS_TRACER`` (jsonl|memory|null|azure). Fail-open."""
    global _tracer
    mode = os.environ.get("LLMOPS_TRACER", "jsonl").strip().lower()
    try:
        if mode == "null":
            from observability.tracer import NullTracer

            _tracer = NullTracer()
        elif mode == "memory":
            from observability.tracer import InMemoryTracer

            _tracer = InMemoryTracer()
        elif mode == "azure":
            from observability.azure_monitor_tracer import AzureMonitorTracer

            _tracer = AzureMonitorTracer()
        else:  # jsonl (default)
            path = os.environ.get("LLMOPS_TRACE_FILE", "data/traces/trace.jsonl")
            _tracer = _JsonlTracer(path)
    except Exception:
        # jsonl needs no platform; anything else falling through -> noop
        path = os.environ.get("LLMOPS_TRACE_FILE", "").strip()
        _tracer = _JsonlTracer(path) if path else _NoopTracer()
    return _tracer


def get_tracer():
    return _tracer if _tracer is not None else init_tracer()


def _make_step_event(**kw):
    try:
        from observability.types import StepEvent

        return StepEvent(**kw)
    except Exception:
        return _StepEventLike(**kw)


def _make_pipeline_event(**kw):
    try:
        from observability.types import PipelineEvent

        return PipelineEvent(**kw)
    except Exception:
        return _PipelineEventLike(**kw)


def _compute_cost(deployment: str, input_tokens: int, output_tokens: int) -> float:
    try:
        from observability.cost import compute_cost

        return compute_cost(deployment, input_tokens, output_tokens)
    except Exception:
        return 0.0


def record_step_event(
    *,
    step: str,
    alias: str | None,
    deployment: str | None,
    input_tokens: int = 0,
    output_tokens: int = 0,
    latency_ms: float = 0.0,
    status: str = "ok",
    guardrail_allowed: bool = True,
    guardrail_reason: str = "",
    error: str | None = None,
) -> None:
    """Build a StepEvent (+cost) and hand it to the tracer. Never raises."""
    try:
        cost = _compute_cost(deployment or "", input_tokens, output_tokens)
        event = _make_step_event(
            session_id=context.current_run_id(),
            step_name=step,
            model_alias=alias,
            provider="azure_openai",
            deployment=deployment,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
            latency_ms=latency_ms,
            guardrail_allowed=guardrail_allowed,
            guardrail_reason=guardrail_reason or "",
            error=error if status == "error" else None,
        )
        with _lock:
            _totals["cost"] += cost
            _totals["latency"] += latency_ms
            _totals["steps"] += 1
        get_tracer().record_step(event)
    except Exception:
        pass


def emit_pipeline_event(run_id: str, pipeline_name: str, error: str | None = None) -> None:
    """Emit one PipelineEvent summarising the run. Never raises."""
    try:
        event = _make_pipeline_event(
            session_id=run_id or context.current_run_id(),
            pipeline_name=pipeline_name,
            step_count=_totals["steps"],
            total_cost_usd=round(_totals["cost"], 6),
            total_latency_ms=round(_totals["latency"], 2),
            error=error,
        )
        get_tracer().record_pipeline(event)
    except Exception:
        pass


def run_totals() -> dict:
    return dict(_totals)
