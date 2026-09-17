"""SQLite mirror of the LLMOps JSONL sinks.

Every APIX app appends StepEvent/PipelineEvent rows to a shared JSONL trace file
(``LLMOPS_TRACE_FILE``) and feedback to ``APIX_FEEDBACK_PATH``. This module tails
those files into a local SQLite database (idempotent, byte-offset tracked) so the
ops-console can serve fast aggregations. No platform dependency on the read path
— cost is already computed into each step row.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from pathlib import Path

_LOCK = threading.Lock()


def _db_path() -> str:
    return os.environ.get("OPS_DB_PATH", "data/ops/ops.db")


def _trace_files() -> list[str]:
    # Allow multiple comma-separated trace files; default to the shared sink.
    raw = os.environ.get("LLMOPS_TRACE_FILE", "data/traces/trace.jsonl")
    return [p.strip() for p in raw.split(",") if p.strip()]


def _feedback_file() -> str:
    return os.environ.get("APIX_FEEDBACK_PATH", "data/feedback/feedback.jsonl")


def _eval_file() -> str:
    return os.environ.get("APIX_EVAL_HISTORY_PATH", "data/eval/eval_runs.jsonl")


def _connect() -> sqlite3.Connection:
    path = Path(_db_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _LOCK, _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS steps (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL, usecase TEXT, app TEXT, program TEXT, env TEXT,
                session_id TEXT, step_name TEXT, model_alias TEXT, provider TEXT,
                deployment TEXT, input_tokens INTEGER, output_tokens INTEGER,
                cost_usd REAL, latency_ms REAL, guardrail_allowed INTEGER,
                guardrail_reason TEXT, error TEXT
            );
            CREATE TABLE IF NOT EXISTS pipelines (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL, usecase TEXT, app TEXT, program TEXT, env TEXT,
                session_id TEXT, pipeline_name TEXT, step_count INTEGER,
                total_cost_usd REAL, total_latency_ms REAL, error TEXT
            );
            CREATE TABLE IF NOT EXISTS feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL, session_id TEXT, step_name TEXT, rating TEXT,
                comment TEXT, corrected_output TEXT, rater_role TEXT
            );
            CREATE TABLE IF NOT EXISTS eval_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL, usecase TEXT, env TEXT, passed INTEGER, n_cases INTEGER,
                pass_rate REAL, threshold REAL, detail TEXT
            );
            CREATE TABLE IF NOT EXISTS ingest_state (
                path TEXT PRIMARY KEY, offset INTEGER
            );
            CREATE INDEX IF NOT EXISTS ix_steps_app ON steps(app);
            CREATE INDEX IF NOT EXISTS ix_steps_ts ON steps(ts);
            CREATE INDEX IF NOT EXISTS ix_steps_session ON steps(session_id);
            """
        )


def _offset(conn: sqlite3.Connection, path: str) -> int:
    row = conn.execute("SELECT offset FROM ingest_state WHERE path = ?", (path,)).fetchone()
    return int(row["offset"]) if row else 0


def _set_offset(conn: sqlite3.Connection, path: str, offset: int) -> None:
    conn.execute(
        "INSERT INTO ingest_state(path, offset) VALUES(?, ?) "
        "ON CONFLICT(path) DO UPDATE SET offset = excluded.offset",
        (path, offset),
    )


def _iter_new_lines(conn: sqlite3.Connection, path: str):
    p = Path(path)
    if not p.exists():
        return
    start = _offset(conn, path)
    size = p.stat().st_size
    if size < start:  # file truncated/rotated — re-read from 0
        start = 0
    with open(p, "r", encoding="utf-8") as f:
        f.seek(start)
        for line in f:
            line = line.strip()
            if line:
                try:
                    yield json.loads(line)
                except Exception:
                    continue
        _set_offset(conn, path, f.tell())


def _ingest_traces(conn: sqlite3.Connection) -> None:
    for path in _trace_files():
        for row in _iter_new_lines(conn, path):
            kind = row.get("kind")
            if kind == "step":
                conn.execute(
                    """INSERT INTO steps(ts,usecase,app,program,env,session_id,step_name,
                       model_alias,provider,deployment,input_tokens,output_tokens,cost_usd,
                       latency_ms,guardrail_allowed,guardrail_reason,error)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        row.get("ts"), row.get("usecase"), row.get("app"), row.get("program"),
                        row.get("env"), row.get("session_id"), row.get("step_name"),
                        row.get("model_alias"), row.get("provider"), row.get("deployment"),
                        int(row.get("input_tokens") or 0), int(row.get("output_tokens") or 0),
                        float(row.get("cost_usd") or 0.0), float(row.get("latency_ms") or 0.0),
                        1 if row.get("guardrail_allowed", True) else 0,
                        row.get("guardrail_reason") or "", row.get("error"),
                    ),
                )
            elif kind == "pipeline":
                conn.execute(
                    """INSERT INTO pipelines(ts,usecase,app,program,env,session_id,pipeline_name,
                       step_count,total_cost_usd,total_latency_ms,error)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        row.get("ts"), row.get("usecase"), row.get("app"), row.get("program"),
                        row.get("env"), row.get("session_id"), row.get("pipeline_name"),
                        int(row.get("step_count") or 0), float(row.get("total_cost_usd") or 0.0),
                        float(row.get("total_latency_ms") or 0.0), row.get("error"),
                    ),
                )


def _ingest_feedback(conn: sqlite3.Connection) -> None:
    for row in _iter_new_lines(conn, _feedback_file()):
        conn.execute(
            """INSERT INTO feedback(ts,session_id,step_name,rating,comment,corrected_output,rater_role)
               VALUES(?,?,?,?,?,?,?)""",
            (
                row.get("ts") or 0.0, row.get("session_id"), row.get("step_name"),
                row.get("rating"), row.get("comment"), row.get("corrected_output"),
                row.get("rater_role"),
            ),
        )


def _ingest_eval(conn: sqlite3.Connection) -> None:
    for row in _iter_new_lines(conn, _eval_file()):
        conn.execute(
            """INSERT INTO eval_runs(ts,usecase,env,passed,n_cases,pass_rate,threshold,detail)
               VALUES(?,?,?,?,?,?,?,?)""",
            (
                row.get("ts") or 0.0, row.get("usecase"), row.get("env"),
                1 if row.get("passed") else 0, int(row.get("n_cases") or 0),
                float(row.get("pass_rate") or 0.0), float(row.get("threshold") or 0.0),
                json.dumps(row.get("detail")) if row.get("detail") is not None else None,
            ),
        )


def ingest() -> None:
    """Tail all JSONL sinks into SQLite. Idempotent; cheap to call per request.
    Ensures the schema exists first so it is safe to call before ``init_db``."""
    init_db()
    with _LOCK, _connect() as conn:
        _ingest_traces(conn)
        _ingest_feedback(conn)
        _ingest_eval(conn)
        conn.commit()


def query(sql: str, params: tuple = ()) -> list[dict]:
    with _LOCK, _connect() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def record_feedback(session_id: str, step_name: str, rating: str, comment: str = "",
                    corrected_output: str | None = None, rater_role: str = "reviewer") -> None:
    """Append feedback to the shared JSONL file (so it flows through the same
    pipeline as app-produced feedback) and ingest immediately."""
    import time

    path = Path(_feedback_file())
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {
        "ts": time.time(), "session_id": session_id, "step_name": step_name,
        "rating": rating, "comment": comment, "corrected_output": corrected_output,
        "rater_role": rater_role,
    }
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")
    ingest()
