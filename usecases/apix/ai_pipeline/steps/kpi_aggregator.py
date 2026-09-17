"""KPI Aggregator step — reads per-employee JSON reports, flattens into a
unified DataFrame with coach mappings, computes runtime KPIs, and uploads
the result as CSV to Azure Blob Storage.

This is a post-summary step: it reads the output of ``run_summary`` and
produces a flat CSV consumed by the application dashboard.

Usage (standalone):
    python -m ai_pipeline.steps.kpi_aggregator --program telesales --date 2025-08-28
"""

from __future__ import annotations

import json
import re
from datetime import date
from typing import Optional

import pandas as pd

from ai_pipeline.logging_config import get_logger
from ai_pipeline.programs_config.base import PipelineConfig
from ai_pipeline.services.storage import StorageService

logger = get_logger("steps.kpi_aggregator")


# ── KPI group definitions (mirrors the groups used in the dashboard) ─────────

KPI_GROUPS = {
    "Sales KPIs": [
        "new_line_pitches",
        "new_line_opportunity_exists",
        "new_line_opportunity_missed",
        "upgrade_attempts",
        "upgrade_opportunity_exists",
        "upgrade_opportunity_missed",
    ],
    "Retention & Assurance KPIs": [
        "save_attempts",
        "mobile_protection_attempts",
        "we_got_you_utterances",
    ],
    "Customer KPIs": [
        "escalations",
        "customer_experience",
        "fwa_attempts",
    ],
    "Core Skills KPIs": [
        "Comprehension",
        "Language Proficiency",
        "Emotional Intelligence",
        "Relationship Building",
        "Professional Skills",
        "Subject Matter Expertise",
    ],
    "Behavior KPIs": [
        "Active Listening",
        "Acknowledgment",
        "Empathy",
        "Confidence",
        "Clarity",
        "Needs Discovery",
        "Solution Guidance",
        "Next Steps Summary",
        "Objection Handling",
        "Value Positioning",
        "Assumptive Close",
        "Compliance Disclosures",
        "Call Control",
        "Professional Tone",
    ],
    "WCC Core KPIs": [
        "resolution_opportunities",
        "resolutions",
        "survival_opportunities",
        "saves",
        "right_of_sell_opportunities",
        "right_of_sell_actuals",
        "sales_made",
        "new_prospects",
        "new_prospects_converted",
    ],
    "WCC Behavior KPIs": [
        "Greetings Connection",
        "Build Connection",
        "Gather Information",
        "Verification",
        "Callback",
        "Address Needs",
        "Test Resolution",
        "Sell Transition",
        "Sell Confidence",
        "Overcoming Objections",
        "SSO Enablement",
        "Setup Success",
        "Additional Concerns",
        "NPS Survey",
        "Closing Restate",
    ],
    "PSO Customer Experience KPIs": [
        "predicted_csat",
        "customer_confidence",
        "customer_effort",
    ],
    "PSO Resolve KPIs": [
        "fcr_likelihood",
        "resolution_completeness",
        "resolution_confidence",
        "next_steps_clarity",
        "issue_resolution_effectiveness",
    ],
    "PSO Quality KPIs": [
        "quality",
        "compliance",
        "process_adherence",
    ],
    "PSO Customer Care Handling KPIs": [
        "issue_ownership",
        "escalation_handling",
        "transfer_avoidance",
        "case_management",
    ],
    "PSO Operational Efficiency KPIs": [
        "aht_efficiency",
        "contact_handling_efficiency",
        "hold_management",
    ],
    "PSO Repeat Contact Risk KPIs": [
        "repeat_contact_risk",
        "escalation_risk",
        "callback_risk",
        "reopen_risk",
    ],
    "PSO Behavior KPIs": [
        "Active Listening",
        "Clarity",
        "Professional Tone",
        "Empathy",
        "Acknowledgment",
        "Reassurance",
        "Ownership",
        "Issue Investigation",
        "Solution Guidance",
        "Resolution Verification",
        "Next Steps Summary",
        "Call Control",
        "Escalation Management",
    ],
    "Computed Scores": [
        "performance_score",
        "risk_count",
        "overall_behavior_score",
        "strong_behavior_count",
        "focus_behavior_count",
        "status_label",
    ],
}

# Flat lookup: kpi_key → group name
_KPI_TO_GROUP = {
    kpi_key: group
    for group, keys in KPI_GROUPS.items()
    for kpi_key in keys
}


# ── Index loading ────────────────────────────────────────────────────────────

def _load_index(storage: StorageService, cfg: PipelineConfig, week: str) -> list[dict]:
    """Load the employee index for *week* from the coach-hierarchy container
    and return a flat list of ``{"id", "name", "coach_id"}`` dicts.
    """
    container = cfg.storage.coach_hierarchy_container
    filename = f"index/{week}.json"

    if not storage.exists(container, filename):
        # Fallback: try summary index path
        alt_container = cfg.storage.summary_container
        alt_filename = f"index/{week}.json"
        if storage.exists(alt_container, alt_filename):
            container = alt_container
            filename = alt_filename
        else:
            logger.warning("No index found for week %s", week)
            return []

    data = storage.read_json(container, filename)
    if not data:
        return []

    flattened: list[dict] = []

    # Nested dict keyed by coach_id
    if isinstance(data, dict):
        for coach_id, coach_info in data.items():
            if not isinstance(coach_info, dict):
                continue
            employees = coach_info.get("employees") or coach_info.get("Employees") or []
            for emp in employees:
                emp_id = emp.get("EmployeeID") or emp.get("id") or emp.get("employeeId")
                emp_name = emp.get("EmployeeName") or emp.get("name") or emp.get("employeeName")
                if emp_id and emp_name:
                    flattened.append({
                        "id": str(emp_id).strip(),
                        "name": str(emp_name).strip(),
                        "coach_id": str(coach_id).strip(),
                    })
    # Flat list fallback
    elif isinstance(data, list):
        for emp in data:
            if not isinstance(emp, dict):
                continue
            emp_id = emp.get("id") or emp.get("EmployeeID") or emp.get("employeeId") or emp.get("employee_id")
            emp_name = emp.get("name") or emp.get("EmployeeName") or emp.get("employeeName") or emp.get("employee_name")
            coach_id = emp.get("coach_id")
            if emp_id and emp_name:
                flattened.append({
                    "id": str(emp_id).strip(),
                    "name": str(emp_name).strip(),
                    "coach_id": str(coach_id).strip() if coach_id else None,
                })

    return flattened


def _load_report(storage: StorageService, cfg: PipelineConfig, emp_id: str, week: str) -> Optional[dict]:
    """Load a single employee report JSON for *week*."""
    container = cfg.storage.summary_container
    paths_to_try = [
        f"{week}/{emp_id}.json",
        f"{week}/{emp_id}.JSON",
    ]
    for path in paths_to_try:
        if storage.exists(container, path):
            return storage.read_json(container, path)

    logger.debug("Report not found for employee %s in week %s", emp_id, week)
    return None


# ── Runtime KPI calculators (ported from reference kpi_aggregator.py) ────────

def _calculate_performance_score(emp_kpis: list[dict]) -> float:
    """Weighted composite performance score (0-100).

    Components & weights:
        1. VXS Quality   – 40%  (keys: vxs, vxs_solutions; already 0-100)
        2. Sales Perf    – 30%  (keys: new_line_pitches /40, mobile_protection /30, save_attempts /10)
        3. Trend Perf    – 20%  (sum of capped delta contributions, normalised)
        4. Quality Issues – 10% (starts at 100, penalties per escalation count)
    """
    if not emp_kpis:
        return 0.0

    vxs_score = 0.0
    vxs_count = 0
    sales_score = 0.0
    sales_count = 0
    trend_score = 0.0
    quality_score = 100.0

    for kpi in emp_kpis:
        key = kpi["key"]
        value = kpi["value"]
        delta = kpi["delta"] or 0.0

        # VXS (40%)
        if key in ("vxs", "vxs_solutions"):
            vxs_score += value
            vxs_count += 1

        # Sales (30%)
        elif key in ("new_line_pitches", "mobile_protection", "save_attempts"):
            if key == "new_line_pitches":
                normalised = min(100.0, (value / 40) * 100)
            elif key == "mobile_protection":
                normalised = min(100.0, (value / 30) * 100)
            else:  # save_attempts
                normalised = min(100.0, (value / 10) * 100)
            sales_score += normalised
            sales_count += 1

        # Escalations (quality penalty)
        elif key == "escalations":
            if value == 0:
                penalty = 0.0
            elif value <= 2:
                penalty = value * 10
            elif value <= 5:
                penalty = 20 + (value - 2) * 15
            else:
                penalty = 65 + (value - 5) * 10
            quality_score = max(0.0, quality_score - penalty)

        # Trend contribution (all deltas)
        if delta > 0:
            trend_score += min(delta * 5, 20)
        elif delta < 0:
            trend_score += max(delta * 3, -15)

    # Weighted assembly
    vxs_component = ((vxs_score / vxs_count) * 0.40) if vxs_count else 0.0
    sales_component = ((sales_score / sales_count) * 0.30) if sales_count else 0.0
    trend_component = max(0.0, min(20.0, ((trend_score + 100) / 200) * 100 * 0.20))
    quality_component = quality_score * 0.10

    return max(0.0, min(100.0, round(
        vxs_component + sales_component + trend_component + quality_component, 2
    )))


def _calculate_risk_count(emp_kpis: list[dict]) -> int:
    """Count risk areas from the already-extracted KPI list.

    Thresholds:
        - delta < -3                     → risk
        - vxs / vxs_solutions value < 70 → risk
        - escalations > 2               → risk
        - new_line_pitches < 30          → risk
        - mobile_protection < 20         → risk
    """
    count = 0
    for kpi in emp_kpis:
        key = kpi["key"]
        value = kpi["value"]
        delta = kpi["delta"] or 0.0

        if delta < -3:
            count += 1
        elif key in ("vxs", "vxs_solutions") and value < 70:
            count += 1
        elif key == "escalations" and value > 2:
            count += 1
        elif key == "new_line_pitches" and value < 30:
            count += 1
        elif key == "mobile_protection" and value < 20:
            count += 1
    return count


def _calculate_behavior_metrics(
    emp_behavior_scores: dict[str, float],
    emp_call_skills: dict[str, float],
    overall_from_json: Optional[float] = None,
) -> dict:
    """Derive behaviour-level computed KPIs from already-extracted score dicts.

    Returns dict with:
        overall_behavior_score  – 0-100 (mean of all scores × 100)
        strong_behavior_count   – #behaviours with score > 0.5
        focus_behavior_count    – #behaviours with score ≤ 0.5
    """
    all_scores = dict(emp_behavior_scores)
    all_scores.update(emp_call_skills)

    if not all_scores:
        return {
            "overall_behavior_score": 0.0,
            "strong_behavior_count": 0,
            "focus_behavior_count": 0,
        }

    # Prefer top-level overall_behavior_score from JSON if present
    overall: Optional[float] = None
    if overall_from_json is not None:
        try:
            overall = float(overall_from_json) * 100  # stored 0-1 in JSON
        except (TypeError, ValueError):
            overall = None

    if not overall:
        scores = [s for s in all_scores.values() if s is not None]
        overall = (sum(scores) / len(scores)) * 100 if scores else 0.0

    strong = sum(1 for s in all_scores.values() if (s or 0) > 0.5)
    focus = sum(1 for s in all_scores.values() if (s or 0) <= 0.5)

    return {
        "overall_behavior_score": round(overall, 2),
        "strong_behavior_count": strong,
        "focus_behavior_count": focus,
    }


def _performance_status(score: float) -> str:
    """Map performance_score → status label (matches manager overview)."""
    if score >= 80:
        return "Strong"
    if score >= 70:
        return "Developing"
    return "Focus"


# ── DataFrame builder ────────────────────────────────────────────────────────

def build_weekly_kpi_dataframe(
    week: str,
    storage: StorageService,
    cfg: PipelineConfig,
) -> pd.DataFrame:
    """Load every employee report for *week*, flatten KPIs into rows, and
    attach coach_id from the index.

    Returns a DataFrame with columns:
        employee_id, employee_name, coach_id, kpi_group, kpi_key,
        kpi_value, kpi_delta, kpi_target
    """
    index = _load_index(storage, cfg, week)
    if not index:
        logger.warning("Empty index for week %s", week)
        return pd.DataFrame()

    rows: list[dict] = []

    for emp in index:
        emp_id = emp["id"]
        emp_name = emp["name"]
        coach_id = emp.get("coach_id")

        report = _load_report(storage, cfg, emp_id, week)
        if report is None:
            continue

        emp_program = report.get("programName")

        # Collect per-employee extracted data for compute functions
        emp_kpis: list[dict] = []  # {key, value, delta}
        emp_behavior_scores: dict[str, float] = {}  # name -> score (0-1)
        emp_call_skills: dict[str, float] = {}  # name -> score (0-1)

        kpis = report.get("kpis", [])
        for kpi in kpis:
            key = kpi.get("key", "")
            value = kpi.get("value")
            if value is None:
                continue
            try:
                value = float(value)
            except (TypeError, ValueError):
                continue

            delta = kpi.get("delta")
            if delta is not None:
                try:
                    delta = float(delta)
                except (TypeError, ValueError):
                    delta = None

            target = kpi.get("target")
            if target is not None:
                try:
                    target = float(target)
                except (TypeError, ValueError):
                    target = None

            emp_kpis.append({"key": key, "value": value, "delta": delta})

            rows.append({
                "employee_id": emp_id,
                "employee_name": emp_name,
                "coach_id": coach_id,
                "program_name": emp_program,
                "kpi_group": _KPI_TO_GROUP.get(key, "Other"),
                "kpi_key": key,
                "kpi_value": value,
                "kpi_delta": delta,
                "kpi_target": target,
            })

        # Flatten behavior_scores as individual KPIs
        behavior_scores = dict(report.get("behavior_scores", {}))
        for bkey, bval in behavior_scores.items():
            bval = dict(bval)
            if bval.get("score") is None:
                continue
            try:
                score_val = float(bval["score"])
            except (TypeError, ValueError):
                continue

            emp_behavior_scores[bkey] = score_val

            rows.append({
                "employee_id": emp_id,
                "employee_name": emp_name,
                "coach_id": coach_id,
                "program_name": emp_program,
                "kpi_group": "Behavior Scores",
                "kpi_key": bkey,
                "kpi_value": score_val,
                "kpi_delta": None,
                "kpi_target": None,
            })

        # Flatten call handling and soft skills
        # Support both key names for backwards compat
        call_handling_skills = (
            report.get("call_handling_and_softs_kills", {})
            or report.get("call_handling_and_soft_skills", {})
        )
        for bkey, bval in call_handling_skills.items():
            bval = dict(bval)
            if bval.get("score") is None:
                continue
            try:
                score_val = float(bval["score"])
            except (TypeError, ValueError):
                continue

            emp_call_skills[bkey] = score_val

            rows.append({
                "employee_id": emp_id,
                "employee_name": emp_name,
                "coach_id": coach_id,
                "program_name": emp_program,
                "kpi_group": "Call Handling and Soft Skills KPIs",
                "kpi_key": bkey,
                "kpi_value": score_val,
                "kpi_delta": None,
                "kpi_target": None,
            })

        # Flatten wcc_behavior scores
        wcc_behavior = report.get("wcc_behavior", {})
        for wkey, wval in wcc_behavior.items():
            if not isinstance(wval, dict):
                continue
            if wval.get("score") is None:
                continue
            try:
                score_val = float(wval["score"])
            except (TypeError, ValueError):
                continue

            delta_val = wval.get("delta")
            if delta_val is not None:
                try:
                    delta_val = float(delta_val)
                except (TypeError, ValueError):
                    delta_val = None

            rows.append({
                "employee_id": emp_id,
                "employee_name": emp_name,
                "coach_id": coach_id,
                "program_name": emp_program,
                "kpi_group": "WCC Behavior KPIs",
                "kpi_key": wkey,
                "kpi_value": score_val,
                "kpi_delta": delta_val,
                "kpi_target": None,
            })

        # Computed / runtime KPIs
        overall_from_json = report.get("overall_behavior_score")

        perf_score = _calculate_performance_score(emp_kpis)
        risk_count = _calculate_risk_count(emp_kpis)
        beh_metrics = _calculate_behavior_metrics(emp_behavior_scores, emp_call_skills, overall_from_json)
        status = _performance_status(perf_score)

        # Map status to numeric for aggregation (Focus=0, Developing=1, Strong=2)
        status_numeric = {"Focus": 0, "Developing": 1, "Strong": 2}.get(status, 0)

        computed_kpis = [
            ("performance_score",      perf_score,                              None, 100.0),
            ("risk_count",             float(risk_count),                       None, None),
            ("overall_behavior_score", beh_metrics["overall_behavior_score"],   None, 100.0),
            ("strong_behavior_count",  float(beh_metrics["strong_behavior_count"]), None, None),
            ("focus_behavior_count",   float(beh_metrics["focus_behavior_count"]),  None, None),
            ("status_label",           float(status_numeric),                   None, None),
        ]

        for ckey, cval, cdelta, ctarget in computed_kpis:
            rows.append({
                "employee_id": emp_id,
                "employee_name": emp_name,
                "coach_id": coach_id,
                "program_name": emp_program,
                "kpi_group": "Computed Scores",
                "kpi_key": ckey,
                "kpi_value": cval,
                "kpi_delta": cdelta,
                "kpi_target": ctarget,
            })

    df = pd.DataFrame(rows)
    return df


# ── Main entry point ─────────────────────────────────────────────────────────

async def run_kpi_aggregator(date_utc: date, cfg: PipelineConfig, storage: StorageService) -> None:
    """Build the weekly KPI DataFrame and upload as CSV to blob storage."""
    week = str(date_utc)
    logger.info("=== KPI AGGREGATOR START | week=%s program=%s ===", week, cfg.program_id)

    df = build_weekly_kpi_dataframe(week, storage, cfg)

    if df.empty:
        logger.warning("No data found for week %s", week)
        return

    logger.info("  %d KPI rows across %d employees", len(df), df["employee_id"].nunique())

    # Upload all-employees CSV to blob: aggregations/{week}.csv
    csv_data = df.to_csv(index=False)
    blob_path = f"aggregations/{week}.csv"
    path = f"abfs://{cfg.storage.summary_container}/{blob_path}"
    with storage.fs.open(path, "w", encoding="utf-8") as f:
        f.write(csv_data)
    logger.info("Uploaded %d rows to %s/%s", len(df), cfg.storage.summary_container, blob_path)

    logger.info("=== KPI AGGREGATOR END ===")
