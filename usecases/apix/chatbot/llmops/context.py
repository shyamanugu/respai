"""Attribution for chatbot LLM calls, carried via contextvars so the choke
point (`llm.client.generate_completion`) can label every trace without
threading args through the LangGraph nodes.
"""
from __future__ import annotations

import contextvars
import os

# The platform usecase id the user-facing chatbot registers under (stricter
# guardrails than the batch `apix` policy — prompt-injection enabled).
USECASE = "apix_chat"

_session: contextvars.ContextVar[str] = contextvars.ContextVar("chat_session", default="")
_step: contextvars.ContextVar[str] = contextvars.ContextVar("chat_step", default="")


def set_session_context(session_id: str) -> None:
    _session.set(session_id or "")


def set_step_context(step: str) -> None:
    _step.set(step or "")


def current_session() -> str:
    return _session.get() or "chat"


def current_step() -> str:
    return _step.get() or "chat"


def current_usecase() -> str:
    return USECASE


def current_env() -> str:
    return os.environ.get("APIX_ENV", "dev").strip() or "dev"


def alias_for_step(step: str) -> str:
    """SQL generation is reasoning-grade -> ``reason``; NL narration and other
    turns are high-volume -> ``bulk``."""
    s = (step or "").lower()
    return "reason" if "sql" in s else "bulk"
