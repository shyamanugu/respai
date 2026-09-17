"""Run/step attribution for LLM calls, carried via contextvars so the choke
point (`services.query`) can label every trace without threading extra args
through the step functions.
"""
from __future__ import annotations

import contextvars
import os

# The platform usecase id this app registers under (see usecases/apix/config).
USECASE = "apix"

_run_id: contextvars.ContextVar[str] = contextvars.ContextVar("llmops_run_id", default="")
_program: contextvars.ContextVar[str] = contextvars.ContextVar("llmops_program", default="")
_step: contextvars.ContextVar[str] = contextvars.ContextVar("llmops_step", default="")


def set_run_context(run_id: str, program: str = "") -> None:
    _run_id.set(run_id or "")
    _program.set(program or "")


def set_step_context(step: str) -> None:
    _step.set(step or "")


def current_run_id() -> str:
    return _run_id.get() or "run"


def current_program() -> str:
    return _program.get() or ""


def current_step() -> str:
    return _step.get() or "llm"


def current_usecase() -> str:
    return USECASE


def current_env() -> str:
    return os.environ.get("APIX_ENV", "dev").strip() or "dev"


def alias_for_step(step: str) -> str:
    """Map a pipeline step to a platform model alias.

    denoise is high-volume, cheap cleanup -> ``bulk``; every other LLM step
    (analysis, summary, individual_metrics) is reasoning-grade -> ``reason``.
    """
    return "bulk" if step == "denoise" else "reason"
