"""Data routes — RBAC-scoped filters, individual report/metrics/coaching,
manager overview, analytics, and notes CRUD. Every route derives scope from the
trusted principal (never from client-supplied ids)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from . import rbac, services
from .security import Principal, get_principal

router = APIRouter(prefix="/api", tags=["data"])


@router.get("/programs")
def programs(_: Principal = Depends(get_principal)) -> list[dict]:
    return services.list_programs()


@router.get("/managers")
def managers(principal: Principal = Depends(get_principal)) -> list[dict]:
    # Only non-coach principals (managers/superusers) get the view-as list.
    if principal.role == "coach":
        return []
    return services.managers()


@router.get("/filters/weeks")
def weeks(program: str, _: Principal = Depends(get_principal)) -> dict:
    return {"weeks": services.available_weeks(program)}


@router.get("/filters/coaches")
def coaches(program: str, week: str, view_as: str | None = Query(None),
            principal: Principal = Depends(get_principal)) -> list[dict]:
    idx = services.week_index(program, week)
    scoped = rbac.filter_index(idx, principal, None, view_as)
    seen: dict[str, str] = {}
    for e in scoped:
        cid = str(e.get("CoachID"))
        seen.setdefault(cid, e.get("CoachName") or cid)
    return [{"coach_id": cid, "coach_name": name} for cid, name in sorted(seen.items())]


@router.get("/filters/employees")
def employees(program: str, week: str, coach_id: str | None = Query(None),
              view_as: str | None = Query(None),
              principal: Principal = Depends(get_principal)) -> list[dict]:
    idx = services.week_index(program, week)
    scoped = rbac.filter_index(idx, principal, coach_id, view_as)
    return [
        {"employee_id": str(e.get("EmployeeID")), "employee_name": e.get("EmployeeName"),
         "coach_id": str(e.get("CoachID")), "coach_name": e.get("CoachName")}
        for e in scoped
    ]


@router.get("/individual/{employee_id}/report")
def individual_report(employee_id: str, program: str, week: str, view_as: str | None = Query(None),
                      principal: Principal = Depends(get_principal)) -> dict:
    idx = services.week_index(program, week)
    rbac.assert_can_view_employee(idx, principal, employee_id, view_as)
    report = services.employee_report(program, employee_id, week)
    if report is None:
        raise HTTPException(status_code=404, detail="no report for employee/week")
    sr = services.score_and_risks(report, program)
    return {"report": report, "program_id": program, **sr}


@router.get("/individual/{employee_id}/metrics")
def individual_metrics(employee_id: str, program: str, week: str, weeks_back: int = 3,
                       view_as: str | None = Query(None),
                       principal: Principal = Depends(get_principal)) -> dict:
    idx = services.week_index(program, week)
    rbac.assert_can_view_employee(idx, principal, employee_id, view_as)
    return services.employee_metrics(employee_id, program, weeks_back)


class CoachingIn(BaseModel):
    current: dict
    previous: dict
    priority: list[dict] | None = None


@router.post("/individual/{employee_id}/coaching")
def individual_coaching(employee_id: str, program: str, week: str, body: CoachingIn,
                        view_as: str | None = Query(None),
                        principal: Principal = Depends(get_principal)) -> dict:
    idx = services.week_index(program, week)
    rbac.assert_can_view_employee(idx, principal, employee_id, view_as)
    insights = services.coaching(employee_id, body.current, body.previous, program, body.priority)
    if insights is None:
        return {"available": False}
    return {"available": True, **insights}


@router.get("/manager/overview")
def manager_overview(program: str, week: str, coach_id: str | None = Query(None),
                     view_as: str | None = Query(None),
                     principal: Principal = Depends(get_principal)) -> dict:
    if coach_id:
        rbac.assert_can_view_coach(principal, coach_id, view_as)
    idx = services.week_index(program, week)
    scoped = rbac.filter_index(idx, principal, coach_id, view_as)
    reports = []
    for e in scoped:
        emp = str(e.get("EmployeeID"))
        rpt = services.employee_report(program, emp, week)
        if not rpt:
            continue
        sr = services.score_and_risks(rpt, program)
        reports.append({
            "employee_id": emp, "employee_name": e.get("EmployeeName"),
            "coach_id": str(e.get("CoachID")), "coach_name": e.get("CoachName"),
            "score": sr["score"], "risk_count": len(sr["risks"]),
        })
    scores = [r["score"] for r in reports if isinstance(r["score"], (int, float))]
    reports.sort(key=lambda r: r["score"] if isinstance(r["score"], (int, float)) else 0, reverse=True)
    return {
        "summary": {
            "team_size": len(reports),
            "avg_performance": round(sum(scores) / len(scores), 1) if scores else 0,
            "top_performers": reports[:3],
            "needs_attention": [r for r in reports if r["risk_count"] > 0][:5],
        },
        "roster": reports,
    }


@router.get("/analytics")
def analytics(program: str, week: str, _: Principal = Depends(get_principal)) -> dict:
    cfg = services.program_config(program)
    return {"title": getattr(cfg, "program_name", program), "week": week, "coming_soon": True}


# ── Notes CRUD ───────────────────────────────────────────────────────────────
class NoteIn(BaseModel):
    note: str
    week: str = ""
    namespace: str = ""


class NoteEditIn(BaseModel):
    note: str
    namespace: str = ""


def _guard_employee(program: str, week: str, employee_id: str, principal: Principal,
                    view_as: str | None) -> None:
    if week:
        idx = services.week_index(program, week)
        rbac.assert_can_view_employee(idx, principal, employee_id, view_as)


@router.get("/employees/{employee_id}/notes")
def list_notes(employee_id: str, program: str = "", week: str = "", namespace: str = "",
               view_as: str | None = Query(None),
               principal: Principal = Depends(get_principal)) -> list[dict]:
    _guard_employee(program, week, employee_id, principal, view_as)
    return services.notes_list(employee_id, week=week, namespace=namespace)


@router.post("/employees/{employee_id}/notes")
def add_note(employee_id: str, body: NoteIn, program: str = "",
             view_as: str | None = Query(None),
             principal: Principal = Depends(get_principal)) -> dict:
    _guard_employee(program, body.week, employee_id, principal, view_as)
    note_id = services.notes_add(
        employee_id, author=principal.sub, note=body.note, week=body.week,
        author_name=principal.name, author_role=principal.role, namespace=body.namespace,
    )
    return {"id": note_id}


@router.put("/employees/{employee_id}/notes/{note_id}")
def edit_note(employee_id: str, note_id: str, body: NoteEditIn,
              principal: Principal = Depends(get_principal)) -> dict:
    ok = services.notes_edit(employee_id, note_id, principal.sub, body.note, namespace=body.namespace)
    return {"updated": ok}


@router.delete("/employees/{employee_id}/notes/{note_id}")
def delete_note(employee_id: str, note_id: str, namespace: str = "",
                principal: Principal = Depends(get_principal)) -> dict:
    ok = services.notes_hide(employee_id, note_id, principal.sub, namespace=namespace)
    return {"hidden": ok}
