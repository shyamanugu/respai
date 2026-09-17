"""LLMOps ops-console backend — FastAPI aggregation API over the SQLite mirror
of the JSONL trace/feedback sinks. Read-only monitoring plus feedback capture.

Run:  uvicorn app:app --host 0.0.0.0 --port 8100   (from ops-console/backend)
"""
from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import store

app = FastAPI(title="APIX LLMOps Ops Console", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("OPS_CORS_ORIGINS", "*").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _startup() -> None:
    store.init_db()
    store.ingest()


def _refresh() -> None:
    """Tail any new sink lines before serving a read."""
    try:
        store.ingest()
    except Exception:
        pass


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = max(0, min(len(s) - 1, int(round((pct / 100.0) * (len(s) - 1)))))
    return round(s[k], 2)


def _app_filter(app_name: str | None) -> tuple[str, tuple]:
    if app_name:
        return " WHERE app = ?", (app_name,)
    return "", ()


@app.get("/healthz")
def healthz() -> dict[str, Any]:
    _refresh()
    counts = {
        "steps": store.query("SELECT COUNT(*) c FROM steps")[0]["c"],
        "pipelines": store.query("SELECT COUNT(*) c FROM pipelines")[0]["c"],
        "feedback": store.query("SELECT COUNT(*) c FROM feedback")[0]["c"],
        "eval_runs": store.query("SELECT COUNT(*) c FROM eval_runs")[0]["c"],
    }
    return {"status": "ok", "sink": os.environ.get("LLMOPS_TRACE_FILE", "data/traces/trace.jsonl"), "counts": counts}


@app.get("/api/monitoring/summary")
def monitoring_summary(app_name: str | None = Query(None, alias="app")) -> dict[str, Any]:
    _refresh()
    where, params = _app_filter(app_name)
    rows = store.query(f"SELECT * FROM steps{where}", params)
    calls = len(rows)
    total_cost = round(sum(r["cost_usd"] or 0 for r in rows), 6)
    total_in = sum(r["input_tokens"] or 0 for r in rows)
    total_out = sum(r["output_tokens"] or 0 for r in rows)
    errors = sum(1 for r in rows if r["error"])
    blocked = sum(1 for r in rows if not r["guardrail_allowed"])
    lats = [r["latency_ms"] or 0.0 for r in rows]
    return {
        "calls": calls,
        "total_cost_usd": total_cost,
        "input_tokens": total_in,
        "output_tokens": total_out,
        "total_tokens": total_in + total_out,
        "error_rate": round(errors / calls, 4) if calls else 0.0,
        "guardrail_block_rate": round(blocked / calls, 4) if calls else 0.0,
        "latency_p50_ms": _percentile(lats, 50),
        "latency_p95_ms": _percentile(lats, 95),
        "by_app": store.query(
            "SELECT app, COUNT(*) calls, ROUND(SUM(cost_usd),6) cost_usd, "
            "SUM(input_tokens+output_tokens) tokens FROM steps GROUP BY app ORDER BY calls DESC"
        ),
    }


@app.get("/api/runs")
def runs(limit: int = 100) -> list[dict[str, Any]]:
    _refresh()
    return store.query(
        "SELECT session_id, pipeline_name, usecase, app, program, env, step_count, "
        "total_cost_usd, total_latency_ms, error, ts FROM pipelines ORDER BY ts DESC LIMIT ?",
        (limit,),
    )


@app.get("/api/runs/{session_id}")
def run_steps(session_id: str) -> dict[str, Any]:
    _refresh()
    steps = store.query("SELECT * FROM steps WHERE session_id = ? ORDER BY ts", (session_id,))
    pipeline = store.query("SELECT * FROM pipelines WHERE session_id = ? ORDER BY ts DESC LIMIT 1", (session_id,))
    return {"session_id": session_id, "pipeline": pipeline[0] if pipeline else None, "steps": steps}


@app.get("/api/traces")
def traces(
    app_name: str | None = Query(None, alias="app"),
    step: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    _refresh()
    clauses, params = [], []
    if app_name:
        clauses.append("app = ?")
        params.append(app_name)
    if step:
        clauses.append("step_name = ?")
        params.append(step)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(limit)
    return store.query(f"SELECT * FROM steps{where} ORDER BY ts DESC LIMIT ?", tuple(params))


@app.get("/api/cost/summary")
def cost_summary(groupby: str = "app") -> list[dict[str, Any]]:
    _refresh()
    col = {"app": "app", "deployment": "deployment", "usecase": "usecase",
           "day": "date(ts, 'unixepoch')"}.get(groupby, "app")
    label = "day" if groupby == "day" else groupby
    return store.query(
        f"SELECT {col} AS {label}, COUNT(*) calls, ROUND(SUM(cost_usd),6) cost_usd, "
        f"SUM(input_tokens) input_tokens, SUM(output_tokens) output_tokens "
        f"FROM steps GROUP BY {col} ORDER BY cost_usd DESC"
    )


@app.get("/api/latency")
def latency(groupby: str = "deployment") -> list[dict[str, Any]]:
    _refresh()
    col = {"deployment": "deployment", "app": "app", "step": "step_name"}.get(groupby, "deployment")
    rows = store.query(f"SELECT {col} AS grp, latency_ms FROM steps")
    buckets: dict[str, list[float]] = {}
    for r in rows:
        buckets.setdefault(r["grp"] or "?", []).append(r["latency_ms"] or 0.0)
    return [
        {groupby: k, "calls": len(v), "p50_ms": _percentile(v, 50), "p95_ms": _percentile(v, 95)}
        for k, v in sorted(buckets.items(), key=lambda kv: -len(kv[1]))
    ]


@app.get("/api/guardrails")
def guardrails(limit: int = 200) -> dict[str, Any]:
    _refresh()
    blocked = store.query(
        "SELECT ts, app, usecase, step_name, guardrail_allowed, guardrail_reason "
        "FROM steps WHERE guardrail_allowed = 0 OR guardrail_reason != '' "
        "ORDER BY ts DESC LIMIT ?",
        (limit,),
    )
    total = store.query("SELECT COUNT(*) c FROM steps")[0]["c"]
    n_blocked = store.query("SELECT COUNT(*) c FROM steps WHERE guardrail_allowed = 0")[0]["c"]
    n_flagged = store.query("SELECT COUNT(*) c FROM steps WHERE guardrail_reason != ''")[0]["c"]
    return {"total_calls": total, "blocked": n_blocked, "flagged": n_flagged, "events": blocked}


@app.get("/api/eval-runs")
def eval_runs(limit: int = 100) -> list[dict[str, Any]]:
    _refresh()
    return store.query("SELECT * FROM eval_runs ORDER BY ts DESC LIMIT ?", (limit,))


@app.get("/api/feedback")
def get_feedback(limit: int = 200) -> list[dict[str, Any]]:
    _refresh()
    return store.query("SELECT * FROM feedback ORDER BY ts DESC LIMIT ?", (limit,))


class FeedbackIn(BaseModel):
    session_id: str
    step_name: str = "llm"
    rating: str = ""
    comment: str = ""
    corrected_output: str | None = None
    rater_role: str = "reviewer"


@app.post("/api/feedback")
def post_feedback(body: FeedbackIn) -> dict[str, str]:
    store.record_feedback(
        body.session_id, body.step_name, body.rating, body.comment,
        body.corrected_output, body.rater_role,
    )
    return {"status": "recorded"}


class PromoteIn(BaseModel):
    out_path: str = "data/eval/promoted_golden.jsonl"


@app.post("/api/feedback/promote")
def promote_feedback(body: PromoteIn) -> dict[str, Any]:
    """Promote corrected feedback into an eval golden dataset via the platform
    (fail-open — returns count 0 if the platform is unavailable)."""
    try:
        import importlib.util
        from pathlib import Path

        for parent in Path(__file__).resolve().parents:
            boot = parent / "platform" / "bootstrap.py"
            if boot.is_file():
                spec = importlib.util.spec_from_file_location("afni_llmops_bootstrap", boot)
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                break
        from feedback.promotion import promote_to_golden_dataset
        from feedback.types import FeedbackEvent

        rows = store.query("SELECT * FROM feedback WHERE corrected_output IS NOT NULL AND corrected_output != ''")
        events = [
            FeedbackEvent(
                session_id=r["session_id"], step_name=r["step_name"] or "llm",
                rating=r["rating"] or "", corrected_output=r["corrected_output"],
                comment=r["comment"] or "",
            )
            for r in rows
        ]
        n = promote_to_golden_dataset(events, Path(body.out_path))
        return {"promoted": n, "out_path": body.out_path}
    except Exception as exc:
        return {"promoted": 0, "error": type(exc).__name__}


@app.get("/api/prompts")
def prompts() -> list[dict[str, Any]]:
    """List registered platform prompts (fail-open — empty if unavailable)."""
    try:
        import importlib.util
        from pathlib import Path

        platform_dir = None
        for parent in Path(__file__).resolve().parents:
            boot = parent / "platform" / "bootstrap.py"
            if boot.is_file():
                spec = importlib.util.spec_from_file_location("afni_llmops_bootstrap", boot)
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                platform_dir = parent / "platform"
                break
        if platform_dir is None:
            return []
        from prompt_management.registry import PromptRegistry

        shared = platform_dir / "services" / "02-prompt-management" / "prompts" / "shared"
        registry = PromptRegistry(prompt_dirs=[str(shared)], fragment_dirs=[str(shared)])
        return [{"name": n} for n in registry.list_prompts()]
    except Exception:
        return []
