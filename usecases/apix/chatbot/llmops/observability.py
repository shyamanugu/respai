"""Tracing + cost sink for the APIX chatbot's LLM calls. Emits one StepEvent
per completion through a platform Tracer (default sink: local JSONL the
ops-console tails). Fully fail-open.
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


class _NoopTracer:
    def record_step(self, event) -> None:
        pass

    def record_pipeline(self, event) -> None:
        pass


class _JsonlTracer:
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
                "app": "chatbot",
                "program": "",
                "env": context.current_env(),
                **dataclasses.asdict(event),
            }
            with _lock:
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(row, default=str) + "\n")
        except Exception:
            pass

    def record_step(self, event) -> None:
        self._write("step", event)

    def record_pipeline(self, event) -> None:
        self._write("pipeline", event)


def init_tracer():
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
        else:
            path = os.environ.get("LLMOPS_TRACE_FILE", "data/traces/trace.jsonl")
            _tracer = _JsonlTracer(path)
    except Exception:
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
    try:
        cost = _compute_cost(deployment or "", input_tokens, output_tokens)
        event = _make_step_event(
            session_id=context.current_session(),
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
        get_tracer().record_step(event)
    except Exception:
        pass
