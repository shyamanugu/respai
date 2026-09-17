"""
qa_validation.py — End-to-end accuracy harness for the SQLite chat agent.

For a fixed coach identity, this script:
  1. Builds a pool of natural-language questions across multiple agents, weeks
     and metrics.
  2. Reads the *ground truth* for each question directly from the Azure Blob
     source-of-truth JSON (the same files the SQLite DB was ingested from).
  3. Runs each question through the real chat pipeline (``run_chat_graph``).
  4. Extracts the predicted value from the structured result rows / answer text
     and compares it to the ground truth.
  5. Writes a detailed, reasoned report to an Excel workbook.

Run (from repo root, with the chatbot venv)::

    chatbot/chat_venv/Scripts/python.exe -m chatbot.tests.qa_validation

Options via env:
    QA_LIMIT   — cap the number of questions (default 50)
    QA_XLSX    — output path (default chatbot/tests/qa_results.xlsx)
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import re
import sys
from pathlib import Path
from typing import Any, Callable

# Reduce noise but keep WARNINGs; set QA_VERBOSE=1 to see full pipeline logs.
if os.getenv("QA_VERBOSE") != "1":
    logging.disable(logging.INFO)

# ── Path bootstrap so ``chatbot.*`` imports resolve when run as a module ─────
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Point the pipeline at a standalone, consolidated copy of the analytics DB.
# The live ``employee_analytics.db`` is held open (WAL mode) by another process
# (e.g. a VS Code DB viewer), which makes the persistent read-only connection
# hang. ``employee_analytics_qa.db`` is a self-contained DELETE-mode snapshot
# produced via the SQLite backup API — same data, no external lock contention.
os.environ.setdefault("CHAT_AGENT_DB_PATH",
                      str(_REPO_ROOT / "chatbot" / "data" / "employee_analytics_qa.db"))

from chatbot.core.schemas import ChatRequest  # noqa: E402
from chatbot.orchestration.graph import run_chat_graph  # noqa: E402
from chatbot.ingestion.jobs import (  # noqa: E402
    _get_container_client,
    read_json_from_blob,
)
from chatbot.llm.dictionaries import (  # noqa: E402
    BEHAVIOR_KEY_MAP,
    CALL_HANDLING_KEY_MAP,
)

# ── Coach identity used for every request (defines the roster scope) ─────────
COACH = {
    "user_id": 903935,
    "user_name": "Garry Jasper Hugo",
    "role": "coach",
    "reporting_agents": [
        9060678, 9062362, 9063931, 9057253, 9063885, 9062363, 9056917,
    ],
}
WEEKS = ["2026-04-03", "2026-04-10", "2026-04-17", "2026-04-24"]

# ── Blob ground-truth cache ──────────────────────────────────────────────────
_client = _get_container_client()
_blob_cache: dict[tuple[int, str], dict | None] = {}


def blob_json(agent_id: int, week: str) -> dict | None:
    """Return the source-of-truth JSON for an agent+week (cached)."""
    key = (agent_id, week)
    if key not in _blob_cache:
        try:
            _blob_cache[key] = read_json_from_blob(_client, f"{week}/{agent_id}.json")
        except Exception:
            _blob_cache[key] = None
    return _blob_cache[key]


def agent_name(agent_id: int, week: str) -> str | None:
    d = blob_json(agent_id, week)
    return d.get("employeeName") if d else None


# ── Ground-truth extractors per metric section ───────────────────────────────
def gt_kpi(d: dict, key: str) -> float | None:
    for it in d.get("kpis", []):
        if it.get("key") == key:
            return it.get("value")
    return None


def gt_wcc(d: dict, key: str) -> float | None:
    for it in d.get("wcc_kpis", []):
        if it.get("key") == key:
            return it.get("score")
    return None


def gt_behavior(d: dict, label: str) -> float | None:
    node = (d.get("behavior_scores") or {}).get(label)
    return node.get("score") if isinstance(node, dict) else None


def gt_callhandling(d: dict, label: str) -> float | None:
    node = (d.get("call_handling_and_softs_kills") or {}).get(label)
    return node.get("score") if isinstance(node, dict) else None


def gt_scalar(d: dict, key: str) -> float | None:
    return d.get(key)


def gt_new_prospect(d: dict, key: str) -> float | None:
    return (d.get("new_prospect") or {}).get(key)


# ── Probe catalogue: (id, question_template, gt_fn, sqlite_col, tol) ──────────
# ``{name}`` and ``{week}`` are filled per case. tol=0 → exact integer match.
Probe = dict[str, Any]

PROBES: list[Probe] = [
    # KPIs (kpis table)
    dict(id="escalations", q="How many escalations did {name} have in the week of {week}?",
         gt=lambda d: gt_kpi(d, "escalations"), col="escalations", tol=0),
    dict(id="upgrade_attempts", q="What is the number of upgrade attempts for {name} for {week}?",
         gt=lambda d: gt_kpi(d, "upgrade_attempts"), col="upgrade_attempts", tol=0),
    dict(id="save_attempts", q="How many save attempts did {name} make in {week}?",
         gt=lambda d: gt_kpi(d, "save_attempts"), col="save_attempts", tol=0),
    dict(id="new_line_pitches", q="How many new line pitches did {name} make in {week}?",
         gt=lambda d: gt_kpi(d, "new_line_pitches"), col="new_line_pitches", tol=0),
    dict(id="fwa_attempts", q="What is the FWA attempts count for {name} in {week}?",
         gt=lambda d: gt_kpi(d, "fwa_attempts"), col="fwa_attempts", tol=0),
    dict(id="mobile_protection_attempts", q="How many mobile protection attempts for {name} in {week}?",
         gt=lambda d: gt_kpi(d, "mobile_protection_attempts"), col="mobile_protection_attempts", tol=0),
    dict(id="we_got_you_utterances", q="How many 'we've got you' utterances did {name} say in {week}?",
         gt=lambda d: gt_kpi(d, "we_got_you_utterances"), col="we_got_you_utterances", tol=0),
    dict(id="customer_experience", q="What is the customer experience score for {name} in {week}?",
         gt=lambda d: gt_kpi(d, "customer_experience"), col="customer_experience", tol=0.5),
    # Scalars on kpis
    dict(id="total_call_count", q="How many total calls did {name} handle in {week}?",
         gt=lambda d: gt_scalar(d, "totalCallCount"), col="total_call_count", tol=0),
    dict(id="overall_behavior_score", q="What is the overall behavior score for {name} for {week}?",
         gt=lambda d: gt_scalar(d, "overall_behavior_score"), col="overall_behavior_score", tol=0.01),
    # New prospect
    dict(id="np_total", q="How many new prospects did {name} have in {week}?",
         gt=lambda d: gt_new_prospect(d, "total"), col="np_total", tol=0),
    # Behavior scores (scores table, bs_*)
    dict(id="bs_empathy", q="What is {name}'s empathy score for {week}?",
         gt=lambda d: gt_behavior(d, "Empathy"), col="bs_empathy", tol=0.01),
    dict(id="bs_clarity", q="What is the clarity score for {name} in {week}?",
         gt=lambda d: gt_behavior(d, "Clarity"), col="bs_clarity", tol=0.01),
    dict(id="bs_confidence", q="What is {name}'s confidence score for {week}?",
         gt=lambda d: gt_behavior(d, "Confidence"), col="bs_confidence", tol=0.01),
    # Call handling (scores table, ch_*)
    dict(id="ch_comprehension", q="What is the comprehension score for {name} in {week}?",
         gt=lambda d: gt_callhandling(d, "Comprehension"), col="ch_comprehension", tol=0.01),
    # WCC metrics (wcc_metrics table, wkpi_*)
    dict(id="wkpi_resolution_attempted", q="What is the resolution attempts for {name} in {week}?",
         gt=lambda d: gt_wcc(d, "resolution_attempted"), col="wkpi_resolution_attempted", tol=0),
    dict(id="wkpi_resolution_actual", q="How many issues did {name} actually resolve in {week}?",
         gt=lambda d: gt_wcc(d, "resolution_actual"), col="wkpi_resolution_actual", tol=0),
    dict(id="wkpi_resolution_opportunity_exists", q="How many resolution opportunities did {name} have in {week}?",
         gt=lambda d: gt_wcc(d, "resolution_opportunity_exists"), col="wkpi_resolution_opportunity_exists", tol=0),
    dict(id="wkpi_save_attempted", q="How many save attempts (WCC) did {name} make in {week}?",
         gt=lambda d: gt_wcc(d, "save_attempted"), col="wkpi_save_attempted", tol=0),
    dict(id="wkpi_sale_attempted", q="How many sale attempts did {name} make in {week}?",
         gt=lambda d: gt_wcc(d, "sale_attempted"), col="wkpi_sale_attempted", tol=0),
]


# ── Prediction extraction from a ChatResponse ────────────────────────────────
_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")


def _surname(name: str) -> str:
    return name.split(",")[0].strip().lower()


def extract_prediction(resp, agent_nm: str, col: str) -> tuple[float | None, str]:
    """Return (predicted_value, how) from the structured rows, else answer text."""
    sur = _surname(agent_nm)
    rows = resp.data or []
    # 1) Exact column on the agent's row.
    for row in rows:
        nm = str(row.get("employee_name", ""))
        if sur and sur in nm.lower():
            if col in row and row[col] is not None:
                return _to_float(row[col]), f"row[{col}]"
    # 2) Single-row result, take the column.
    if len(rows) == 1 and col in rows[0] and rows[0][col] is not None:
        return _to_float(rows[0][col]), f"single-row[{col}]"
    # 3) Any numeric non-id cell on the agent's row.
    for row in rows:
        nm = str(row.get("employee_name", ""))
        if sur and sur in nm.lower():
            for k, v in row.items():
                if k in ("employee_id", "employee_name", "period"):
                    continue
                fv = _to_float(v)
                if fv is not None:
                    return fv, f"row[{k}]~"
    # 4) First number in the NL answer.
    m = _NUM_RE.search(resp.response or "")
    if m:
        return float(m.group(0)), "answer-text"
    return None, "none"


def _to_float(v: Any) -> float | None:
    try:
        if v is None:
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _match(pred: float | None, gt: float | None, tol: float) -> bool:
    if pred is None or gt is None:
        return False
    if tol == 0:
        return round(pred) == round(gt)
    return math.isclose(pred, gt, abs_tol=tol)


# ── Case building ────────────────────────────────────────────────────────────
def _candidate(probe: Probe, agent_id: int, week: str) -> dict | None:
    d = blob_json(agent_id, week)
    if not d:
        return None
    gt = probe["gt"](d)
    if gt is None:
        return None
    nm = d.get("employeeName") or str(agent_id)
    return dict(
        probe_id=probe["id"],
        agent_id=agent_id,
        agent_name=nm,
        week=week,
        question=probe["q"].format(name=nm, week=week),
        col=probe["col"],
        tol=probe["tol"],
        gt=gt,
    )


def build_cases(limit: int) -> list[dict]:
    """Build a *diverse* test set.

    Every probe (metric type) is exercised across multiple agents and weeks,
    preferring non-zero ground truth so metric resolution is genuinely stressed
    rather than trivially matching zeros. Cases are spread round-robin over all
    four weeks and the whole roster.
    """
    per_probe = max(1, limit // len(PROBES) + 1)
    seen: set[tuple] = set()
    buckets: list[list[dict]] = []

    for probe in PROBES:
        nonzero: list[dict] = []
        zero: list[dict] = []
        # Rotate week order per probe so weeks are evenly represented.
        for wi, week in enumerate(WEEKS):
            for agent_id in COACH["reporting_agents"]:
                c = _candidate(probe, agent_id, week)
                if not c:
                    continue
                key = (c["probe_id"], c["agent_id"], c["week"])
                if key in seen:
                    continue
                (nonzero if (c["gt"] or 0) != 0 else zero).append(c)
        # Prefer non-zero ground truth; top up with zeros if needed.
        chosen = (nonzero + zero)[:per_probe]
        for c in chosen:
            seen.add((c["probe_id"], c["agent_id"], c["week"]))
        buckets.append(chosen)

    # Interleave probe buckets so the run visits all metric types early.
    cases: list[dict] = []
    for i in range(per_probe):
        for b in buckets:
            if i < len(b):
                cases.append(b[i])
            if len(cases) >= limit:
                return cases
    return cases[:limit]


async def run_all(cases: list[dict]) -> list[dict]:
    import time
    progress = Path(__file__).parent / "_progress.log"
    pf = progress.open("w", encoding="utf-8")

    def log_line(msg: str) -> None:
        print(msg, flush=True)
        pf.write(msg + "\n")
        pf.flush()

    per_q_timeout = float(os.getenv("QA_TIMEOUT", "90"))
    results = []
    for i, c in enumerate(cases, 1):
        log_line(f"[{i:>2}/{len(cases)}] asking: {c['question']!r}")
        t0 = time.time()
        req = ChatRequest(
            user_query=c["question"],
            user_id=COACH["user_id"],
            user_name=COACH["user_name"],
            role=COACH["role"],
            reporting_agents=COACH["reporting_agents"],
            session_id=f"qa-{i}",
        )
        try:
            resp = await asyncio.wait_for(run_chat_graph(req), timeout=per_q_timeout)
            pred, how = extract_prediction(resp, c["agent_name"], c["col"])
            answer = resp.response
            rows = len(resp.data or [])
        except asyncio.TimeoutError:
            pred, how, answer, rows = None, "TIMEOUT", "(timed out)", 0
        except Exception as exc:  # noqa: BLE001
            pred, how, answer, rows = None, f"ERROR: {exc}", str(exc), 0
        ok = _match(pred, c["gt"], c["tol"])
        results.append({**c, "pred": pred, "how": how, "answer": answer,
                        "rows": rows, "pass": ok})
        status = "PASS" if ok else "FAIL"
        log_line(f"[{i:>2}/{len(cases)}] {status} {c['probe_id']:<32} "
                 f"gt={c['gt']} pred={pred} ({how}) {time.time()-t0:.1f}s")
    pf.close()
    return results
    return results


def write_excel(results: list[dict], path: str) -> None:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    wb = Workbook()
    ws = wb.active
    ws.title = "QA Results"
    headers = ["#", "Metric", "Agent", "Week", "Question", "Expected (blob)",
               "Predicted (bot)", "Source col", "Match?", "Extraction",
               "Rows", "Bot answer", "Reasoning"]
    ws.append(headers)
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="305496")
    green = PatternFill("solid", fgColor="C6EFCE")
    red = PatternFill("solid", fgColor="FFC7CE")
    for i, r in enumerate(results, 1):
        reasoning = (
            f"Ground truth read from blob {r['week']}/{r['agent_id']}.json "
            f"({r['col']}={r['gt']}). Bot returned {r['pred']} via {r['how']}. "
            + ("Values match within tolerance." if r["pass"]
               else "MISMATCH — bot value differs from source of truth.")
        )
        ws.append([
            i, r["probe_id"], r["agent_name"], r["week"], r["question"],
            r["gt"], r["pred"], r["col"], "PASS" if r["pass"] else "FAIL",
            r["how"], r["rows"], (r["answer"] or "")[:500], reasoning,
        ])
        fill = green if r["pass"] else red
        ws.cell(row=i + 1, column=9).fill = fill

    # Summary sheet
    s = wb.create_sheet("Summary")
    total = len(results)
    passed = sum(1 for r in results if r["pass"])
    s.append(["Total questions", total])
    s.append(["Passed", passed])
    s.append(["Failed", total - passed])
    s.append(["Accuracy %", round(100 * passed / total, 1) if total else 0])
    for cell in s["A"]:
        cell.font = Font(bold=True)

    # Column widths
    widths = [4, 30, 20, 12, 48, 14, 14, 26, 8, 14, 6, 60, 70]
    for idx, w in enumerate(widths, 1):
        ws.column_dimensions[chr(64 + idx) if idx <= 26 else "A"].width = w

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    print(f"\nExcel written: {path}")


def main() -> None:
    # Pre-open the shared SQLite connection. ``execute_sql`` holds ``_DB_LOCK``
    # and then calls ``get_db_connection()``, which on first use also tries to
    # acquire ``_DB_LOCK`` (non-reentrant) → deadlock. The server avoids this by
    # calling ``warmup()`` at startup; the harness must do the same.
    from chatbot.sources import sqlite_source
    sqlite_source.warmup()

    limit = int(os.getenv("QA_LIMIT", "50"))
    xlsx = os.getenv("QA_XLSX", str(Path(__file__).parent / "qa_results.xlsx"))
    cases = build_cases(limit)
    print(f"Built {len(cases)} test cases. Running…\n")
    results = asyncio.run(run_all(cases))
    total = len(results)
    passed = sum(1 for r in results if r["pass"])
    print(f"\n{'=' * 60}\nAccuracy: {passed}/{total} "
          f"({100 * passed / total:.1f}%)" if total else "no cases")
    fails = [r for r in results if not r["pass"]]
    if fails:
        print("\nFAILURES:")
        for r in fails:
            print(f"  - {r['probe_id']} | {r['agent_name']} | {r['week']} | "
                  f"gt={r['gt']} pred={r['pred']} | {r['how']} | "
                  f"ans={ (r['answer'] or '')[:120]!r}")
    write_excel(results, xlsx)


if __name__ == "__main__":
    main()
