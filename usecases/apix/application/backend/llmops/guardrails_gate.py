"""Guardrail gate for the dashboard coaching call (usecase ``apix``). Fail-open."""
from __future__ import annotations

_cache: dict[tuple[str, str], object] = {}


def get_guardrail(usecase: str, environment: str):
    key = (usecase, environment)
    if key in _cache:
        return _cache[key]
    guardrail = None
    try:
        from guardrails.builder import build_guardrail

        guardrail = build_guardrail(usecase, environment)
    except Exception:
        guardrail = None
    _cache[key] = guardrail
    return guardrail


def _check(guardrail, method: str, text: str) -> tuple[bool, str]:
    if guardrail is None or not text:
        return True, ""
    try:
        result = getattr(guardrail, method)(text)
        return bool(result.allowed), (result.reason or "")
    except Exception:
        return True, ""


def check_input(guardrail, text: str) -> tuple[bool, str]:
    return _check(guardrail, "check_input", text)


def check_output(guardrail, text: str) -> tuple[bool, str]:
    return _check(guardrail, "check_output", text)
