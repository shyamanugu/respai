"""Thin wrappers over the existing Streamlit backend, made API-safe.

Rule: reuse the backend's data logic in-process; never reimplement Azure SQL /
Blob / scoring / coaching. Streamlit-cached loaders are called with explicit
``week``/``prefix`` (so no ``st.session_state`` read happens) and via their
undecorated form when available. All backend imports are lazy so this module
imports even where azure/streamlit/pyodbc are absent (only the called route fails).
"""
from __future__ import annotations

from functools import lru_cache


def _uncached(fn):
    """Return the undecorated function behind an @st.cache_data wrapper if
    exposed, else the wrapper itself (still executes, just caches)."""
    return getattr(fn, "__wrapped__", fn)


# ── Programs ─────────────────────────────────────────────────────────────────
def list_programs() -> list[dict]:
    from backend.config.programs import get_all_program_configs

    out = []
    for pid, cfg in get_all_program_configs().items():
        out.append({"id": pid, "label": getattr(cfg, "program_name", pid),
                    "blob_prefix": getattr(cfg, "blob_prefix", "")})
    return out


def program_prefix(program_id: str) -> str:
    from backend.config.programs import load_program_config

    return getattr(load_program_config(program_id), "blob_prefix", "") or ""


def program_config(program_id: str):
    from backend.config.programs import load_program_config

    return load_program_config(program_id)


# ── Blob (weekly index + reports) ────────────────────────────────────────────
def available_weeks(program_id: str) -> list[str]:
    from backend.services.blob_service import discover_available_weeks

    return list(_uncached(discover_available_weeks)(program_prefix(program_id)) or [])


def week_index(program_id: str, week: str) -> list[dict]:
    """Load the flattened week index; force explicit prefix+week so no session
    read occurs. Falls back to passing week only if the fn ignores prefix."""
    from backend.services.blob_service import load_index_for_week

    fn = _uncached(load_index_for_week)
    try:
        return list(fn(week=week) or [])
    except TypeError:
        return list(fn(week) or [])


def employee_report(program_id: str, employee_id: str, week: str) -> dict | None:
    from backend.services.blob_service import load_report_for_week

    fn = _uncached(load_report_for_week)
    try:
        return fn(emp_id=str(employee_id), week=week)
    except TypeError:
        return fn(str(employee_id), week)


# ── Scoring / risks ──────────────────────────────────────────────────────────
def score_and_risks(report: dict, program_id: str) -> dict:
    from backend.services.scoring import calculate_performance_score, identify_risk_areas

    cfg = program_config(program_id)
    return {
        "score": calculate_performance_score(report, cfg),
        "risks": identify_risk_areas(report, cfg),
    }


# ── Azure SQL metrics ────────────────────────────────────────────────────────
def employee_metrics(employee_id: str, program_id: str, weeks_back: int = 3) -> dict:
    from backend.services.azure_sql_query import (
        fetch_employee_and_team_metrics,
        get_metric_groups,
    )

    trend, team = fetch_employee_and_team_metrics(
        str(employee_id), program_id=program_id, weeks_back=weeks_back
    )
    groups = get_metric_groups(program_id)
    return {"trend": trend, "team": team, "groups": _groups_to_dicts(groups)}


def _groups_to_dicts(groups) -> list[dict]:
    out = []
    for g in groups or []:
        if isinstance(g, dict):
            out.append(g)
        else:
            out.append({
                "name": getattr(g, "name", getattr(g, "title", "")),
                "metrics": getattr(g, "metrics", getattr(g, "keys", [])),
            })
    return out


# ── Coaching AI (already platform-wired at the choke) ────────────────────────
def coaching(employee_id: str, current: dict, previous: dict, program_id: str,
             priority: list[dict] | None = None) -> dict | None:
    from backend.services.coaching_ai import generate_coaching_insights

    return generate_coaching_insights(str(employee_id), current, previous, program_id, priority)


# ── Notes ────────────────────────────────────────────────────────────────────
def notes_list(emp_id: str, week: str = "", namespace: str = "") -> list[dict]:
    from backend.db.notes import get_notes

    return get_notes(str(emp_id), week=week, namespace=namespace)


def notes_add(emp_id: str, author: str, note: str, week: str = "", author_name: str = "",
              author_role: str = "", namespace: str = "") -> str:
    from backend.db.notes import add_note

    return add_note(str(emp_id), author, note, week=week, author_name=author_name,
                    author_role=author_role, namespace=namespace)


def notes_edit(emp_id: str, note_id: str, author: str, new_text: str, namespace: str = "") -> bool:
    from backend.db.notes import edit_note

    return edit_note(str(emp_id), note_id, author, new_text, namespace=namespace)


def notes_hide(emp_id: str, note_id: str, author: str, namespace: str = "") -> bool:
    from backend.db.notes import hide_note

    return hide_note(str(emp_id), note_id, author, namespace=namespace)


# ── Managers (superuser view-as) ─────────────────────────────────────────────
@lru_cache(maxsize=1)
def _managers_cached() -> tuple:
    from backend.auth.upn_map import get_all_managers

    return tuple(
        {"employee_id": str(m.get("employee_id")), "name": m.get("name"),
         "coach_ids": [str(c) for c in (m.get("coach_ids") or [])]}
        for m in get_all_managers()
    )


def managers() -> list[dict]:
    return [dict(m) for m in _managers_cached()]
