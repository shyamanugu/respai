"""Attribution for the dashboard's LLM calls (coaching insights)."""
from __future__ import annotations

import contextvars
import os

# The coaching call is batch-side policy (same as ai_pipeline): PII flagged,
# secrets blocked, prompt-injection off.
USECASE = "apix"

_session: contextvars.ContextVar[str] = contextvars.ContextVar("app_session", default="")
_step: contextvars.ContextVar[str] = contextvars.ContextVar("app_step", default="coaching")


def set_session_context(session_id: str) -> None:
    _session.set(session_id or "")


def set_step_context(step: str) -> None:
    _step.set(step or "")


def current_session() -> str:
    return _session.get() or "dashboard"


def current_step() -> str:
    return _step.get() or "coaching"


def current_usecase() -> str:
    return USECASE


def current_env() -> str:
    return os.environ.get("APIX_ENV", "dev").strip() or "dev"


def alias_for_step(step: str) -> str:
    # Coaching generation is reasoning-grade.
    return "reason"
