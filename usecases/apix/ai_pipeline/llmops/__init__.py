"""APIX ai_pipeline <-> LLMOps platform adapter.

Importing this package:
  1. locates and executes ``platform/bootstrap.py`` (fail-open) so the platform
     libraries (``model_management``, ``guardrails``, ``observability``,
     ``feedback``) become importable by bare name;
  2. exposes the thin helpers the pipeline wires in at its LLM choke point.

Everything is fail-open: if the platform tree is absent, ``PLATFORM_AVAILABLE``
is False and the pipeline runs unchanged with no tracing/guardrails.
"""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path

PLATFORM_AVAILABLE = False


def _bootstrap_platform() -> None:
    global PLATFORM_AVAILABLE
    boot: Path | None = None

    override = os.environ.get("LLMOPS_PLATFORM_ROOT", "").strip()
    if override:
        base = Path(override).expanduser()
        for cand in (base / "bootstrap.py", base.parent / "bootstrap.py"):
            if cand.is_file():
                boot = cand
                break

    if boot is None:
        for parent in Path(__file__).resolve().parents:
            cand = parent / "platform" / "bootstrap.py"
            if cand.is_file():
                boot = cand
                break

    if boot is not None:
        try:
            spec = importlib.util.spec_from_file_location("afni_llmops_bootstrap", boot)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            PLATFORM_AVAILABLE = True
        except Exception:
            PLATFORM_AVAILABLE = False


_bootstrap_platform()

from .context import (  # noqa: E402
    USECASE,
    current_env,
    set_run_context,
    set_step_context,
)
from .observability import emit_pipeline_event, get_tracer, init_tracer, run_totals  # noqa: E402
from .runner import instrumented_query  # noqa: E402

__all__ = [
    "PLATFORM_AVAILABLE",
    "USECASE",
    "current_env",
    "set_run_context",
    "set_step_context",
    "init_tracer",
    "get_tracer",
    "emit_pipeline_event",
    "run_totals",
    "instrumented_query",
]
