"""Model-alias resolution for the dashboard (fail-open to the caller's deployment)."""
from __future__ import annotations


def resolve_deployment(alias: str, fallback: str, environment: str) -> str:
    try:
        from model_management.model_router import resolve

        handle = resolve(alias, environment)
        return getattr(handle, "deployment", None) or fallback
    except Exception:
        return fallback
