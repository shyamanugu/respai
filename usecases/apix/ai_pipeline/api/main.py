"""Thin HTTP trigger for the ai_pipeline batch job.

Lets the combined container expose the pipeline as an endpoint alongside the
dashboard and chatbot, instead of only being runnable via
``python -m ai_pipeline.main``. Domain logic is untouched — this calls the
exact same ``run_pipeline`` coroutine the CLI uses, as a background task, and
tracks status in memory (fine for a trigger endpoint; not meant to replace the
CLI for anything that needs durable run history).

Reachable on the public ingress in the combined deployment (see
``usecases/apix/combined/``), so every mutating route requires a bearer token
(``PIPELINE_TRIGGER_TOKEN``) — fail-closed: if the token env var isn't set,
every request is rejected rather than left open.
"""
from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Optional

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel

from ai_pipeline.main import run_pipeline

app = FastAPI(title="APIX Pipeline Trigger API", version="0.1.0")

_runs: dict[str, dict] = {}


def _require_token(authorization: str | None = Header(default=None)) -> None:
    expected = os.environ.get("PIPELINE_TRIGGER_TOKEN", "").strip()
    if not expected:
        raise HTTPException(status_code=503, detail="PIPELINE_TRIGGER_TOKEN not configured")
    if authorization != f"Bearer {expected}":
        raise HTTPException(status_code=401, detail="invalid or missing bearer token")


class RunRequest(BaseModel):
    program: Optional[str] = None
    mode: Optional[str] = None
    date: Optional[str] = None
    start: Optional[str] = None
    end: Optional[str] = None
    step: Optional[str] = None
    coach: Optional[str] = None
    agent: Optional[str] = None


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "apix-pipeline-api"}


@app.post("/run", dependencies=[Depends(_require_token)])
async def run(body: RunRequest) -> dict:
    if not body.program and not body.mode:
        raise HTTPException(status_code=400, detail="one of 'program' or 'mode' is required")

    run_id = uuid.uuid4().hex[:8]
    args = SimpleNamespace(
        program=body.program, mode=body.mode, date=body.date, start=body.start,
        end=body.end, step=body.step, coach=body.coach, agent=body.agent, log_level="INFO",
    )
    _runs[run_id] = {
        "status": "running", "started_at": datetime.now(timezone.utc).isoformat(),
        "finished_at": None, "error": None, "request": body.model_dump(),
    }

    async def _task() -> None:
        try:
            await run_pipeline(args)
            _runs[run_id]["status"] = "done"
        except Exception as exc:  # the trigger endpoint must never crash the process
            _runs[run_id]["status"] = "failed"
            _runs[run_id]["error"] = repr(exc)
        finally:
            _runs[run_id]["finished_at"] = datetime.now(timezone.utc).isoformat()

    asyncio.create_task(_task())
    return {"run_id": run_id, "status": "running"}


@app.get("/status/{run_id}", dependencies=[Depends(_require_token)])
def status(run_id: str) -> dict:
    if run_id not in _runs:
        raise HTTPException(status_code=404, detail="unknown run_id")
    return {"run_id": run_id, **_runs[run_id]}


@app.get("/runs", dependencies=[Depends(_require_token)])
def list_runs() -> list[dict]:
    return [{"run_id": k, **v} for k, v in _runs.items()]
