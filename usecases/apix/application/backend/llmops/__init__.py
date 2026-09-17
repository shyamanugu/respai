"""APIX dashboard <-> LLMOps platform adapter.

Importing this package locates and executes ``platform/bootstrap.py`` (fail-open)
so the platform libraries are importable, and exposes the helpers the dashboard
wires in at its LLM choke point (``backend.services.coaching_ai``). Fail-open: if
the platform tree is absent, ``PLATFORM_AVAILABLE`` is False and coaching runs
unchanged with no tracing/guardrails.
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
    alias_for_step,
    current_env,
    current_step,
    set_session_context,
    set_step_context,
)
from .guardrails_gate import check_input, check_output, get_guardrail  # noqa: E402
from .observability import get_tracer, init_tracer, record_step_event  # noqa: E402
from .runner import resolve_deployment  # noqa: E402

__all__ = [
    "PLATFORM_AVAILABLE",
    "USECASE",
    "alias_for_step",
    "current_env",
    "current_step",
    "set_session_context",
    "set_step_context",
    "get_guardrail",
    "check_input",
    "check_output",
    "get_tracer",
    "init_tracer",
    "record_step_event",
    "resolve_deployment",
]
