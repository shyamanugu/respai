"""Server-side RBAC scoping — reimplements application/main.py's logic from the
trusted principal so the SPA can never widen its own access.

Roles: manager > coach > agent.
- coach:      sees only employees whose CoachID == their own employee id.
- manager:    sees employees under their assigned coach_ids.
- superuser:  a non-coach principal with empty coach_ids — sees everyone,
              optionally narrowed to one manager's coaches (view-as).
"""
from __future__ import annotations

from fastapi import HTTPException

from .security import Principal


def effective_coach_ids(principal: Principal, view_as_manager_id: str | None = None) -> list[str]:
    """Coach ids this principal may see. Empty list == 'all' (superuser)."""
    if principal.coach_ids:
        return [str(c) for c in principal.coach_ids]
    # Superuser: optionally narrow to a chosen manager's coaches.
    if view_as_manager_id and view_as_manager_id != "__all__":
        try:
            from backend.auth.upn_map import get_all_managers

            for m in get_all_managers():
                if str(m.get("employee_id")) == str(view_as_manager_id):
                    return [str(c) for c in (m.get("coach_ids") or [])]
        except Exception:
            return []
    return []


def filter_index(idx: list[dict], principal: Principal, coach_filter: str | None = None,
                 view_as_manager_id: str | None = None) -> list[dict]:
    """Return the subset of the week index the principal may see, optionally
    narrowed to a single coach (``coach_filter``)."""
    if principal.role == "coach":
        own = str(principal.sub)
        scoped = [e for e in idx if str(e.get("CoachID")) == own]
    else:
        ids = effective_coach_ids(principal, view_as_manager_id)
        scoped = idx if not ids else [e for e in idx if str(e.get("CoachID")) in ids]
    if coach_filter and coach_filter not in ("", "__all__"):
        scoped = [e for e in scoped if str(e.get("CoachID")) == str(coach_filter)]
    return scoped


def visible_employee_ids(idx: list[dict], principal: Principal,
                         view_as_manager_id: str | None = None) -> set[str]:
    return {str(e.get("EmployeeID")) for e in filter_index(idx, principal, None, view_as_manager_id)}


def assert_can_view_employee(idx: list[dict], principal: Principal, employee_id: str,
                             view_as_manager_id: str | None = None) -> None:
    if str(employee_id) not in visible_employee_ids(idx, principal, view_as_manager_id):
        raise HTTPException(status_code=403, detail="forbidden: employee outside your scope")


def assert_can_view_coach(principal: Principal, coach_id: str,
                          view_as_manager_id: str | None = None) -> None:
    if principal.role == "coach":
        if str(coach_id) != str(principal.sub):
            raise HTTPException(status_code=403, detail="forbidden: coach outside your scope")
        return
    ids = effective_coach_ids(principal, view_as_manager_id)
    if ids and str(coach_id) not in ids:
        raise HTTPException(status_code=403, detail="forbidden: coach outside your scope")
