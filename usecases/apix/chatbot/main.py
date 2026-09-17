"""
main.py — Unified FastAPI application for the APIX Chatbot.

Single entry point: defines the chat + health endpoints, mounts the ingestion
router, and warms the LLM client on startup so the first request is fast.
All heavy lifting lives in :mod:`chatbot.pipeline` (orchestration) and the data
agents it delegates to — this file is just the thin HTTP layer.

Routes:
    POST   /chat_agent             — NL → SQL → NL analytics chat
    GET    /chat_history/{user_id} — fetch a user's conversation history
    DELETE /chat_history/{user_id} — clear a user's conversation history
    GET    /health                 — liveness probe
    POST   /ingest                 — weekly blob → SQLite ingestion

Starting the server::

    # From inside the chatbot/ folder:
    uvicorn main:app --host 0.0.0.0 --port 8000 --reload

    # Or from the repository root (equivalent, used by the container):
    uvicorn chatbot.main:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

# --- Path bootstrap -------------------------------------------------------
# Make the absolute ``chatbot.*`` imports below resolve regardless of where the
# server is launched from. Running ``uvicorn main:app`` from inside chatbot/
# only puts that folder on sys.path, so the ``chatbot`` package (its parent)
# would be missing. Inserting the repository root (the parent of this file's
# directory) fixes that while keeping the package-qualified module names — which
# the logging setup in chatbot.core.config relies on (logger namespace
# ``chatbot.*``). Running ``uvicorn chatbot.main:app`` from the repo root or the
# container also works, since the path is already correct there.
import os as _os
import sys as _sys

_REPO_ROOT = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
if _REPO_ROOT not in _sys.path:
    _sys.path.insert(0, _REPO_ROOT)
# --------------------------------------------------------------------------

import asyncio
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.requests import Request

from chatbot.core.config import get_logger
from chatbot.core.auth import AuthError, authenticate
from chatbot.core.schemas import ChatRequest, ChatResponse
from chatbot.core import sessions as session_store
from chatbot.orchestration.pipeline import run_chat_pipeline, get_history, clear_history
from chatbot.llm import warmup as llm_warmup
from chatbot.sources.azure_source import warmup as rep_sql_warmup
from chatbot.sources.sqlite_source import warmup as sqlite_warmup
from chatbot.ingestion import router as ingest_router

log = get_logger(__name__)

app = FastAPI(
    title="APIX Chatbot API",
    description="Unified API — chat analytics, data ingestion, and health checks",
    version="1.0.0",
    docs_url="/apix/docs",
    redoc_url="/apix/redoc",
    openapi_url="/apix/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Authentication ────────────────────────────────────────────────────────────
# Every route except the public ones below requires a valid short-lived JWT
# minted by the trusted Streamlit backend (see chatbot/core/auth.py). The token
# is validated here once per request; its claims are stashed on request.state so
# individual handlers can enforce fine-grained scope (e.g. user_id binding).
_PUBLIC_PATHS = {
    "/health",
    "/apix/docs",
    "/apix/redoc",
    "/apix/openapi.json",
}


@app.middleware("http")
async def _auth_middleware(request: Request, call_next):
    # Allow CORS preflight and the handful of public, non-sensitive routes.
    if request.method == "OPTIONS" or request.url.path in _PUBLIC_PATHS:
        return await call_next(request)
    try:
        request.state.claims = authenticate(request.headers.get("authorization"))
    except AuthError as exc:
        log.warning("auth rejected %s %s → %s", request.method, request.url.path, exc)
        return JSONResponse(status_code=401, content={"detail": f"unauthorized: {exc}"})
    return await call_next(request)


@app.on_event("startup")
async def _startup_warmup() -> None:
    """Fire-and-forget warmup so the first chat request is fast.

    Warms the LLM client, the Azure SQL connection, and the SQLite connection
    so each datastore connects once at boot and is reused for every request —
    the user's first query never pays a cold-connect penalty.
    """
    asyncio.create_task(llm_warmup())
    asyncio.create_task(rep_sql_warmup())
    asyncio.create_task(asyncio.to_thread(sqlite_warmup))
    log.info("startup → LLM + Azure SQL + SQLite warmup scheduled in background")

    # LLMOps: initialise the trace sink (fail-open).
    try:
        from chatbot import llmops

        llmops.init_tracer()
        log.info(
            "startup → LLMOps platform=%s tracer=%s",
            "available" if llmops.PLATFORM_AVAILABLE else "absent (fail-open)",
            _os.environ.get("LLMOPS_TRACER", "jsonl"),
        )
    except Exception as exc:  # never block startup on observability
        log.warning("startup → LLMOps init skipped: %s", exc)


def _enforce_user_scope(request: Request, target_user_id: Any) -> None:
    """Reject the request unless the token subject matches *target_user_id*.

    Prevents an authenticated user from requesting another user's data by
    passing a different ``user_id`` in the body or path. Skipped when auth is
    disabled for local development (claims carry ``auth_disabled``).
    """
    from fastapi import HTTPException

    claims = getattr(request.state, "claims", None) or {}
    if claims.get("auth_disabled"):
        return
    subject = str(claims.get("sub"))
    if subject != str(target_user_id):
        log.warning(
            "scope violation → token sub=%s attempted user_id=%s",
            subject, target_user_id,
        )
        raise HTTPException(status_code=403, detail="forbidden: user scope mismatch")


# ── Chat ────────────────────────────────────────────────────────────────────
@app.post("/chat_agent", response_model=ChatResponse, tags=["chat"])
async def chat_agent(req: ChatRequest, request: Request) -> ChatResponse:
    """Main chat endpoint — full NL → SQL → NL pipeline."""
    _enforce_user_scope(request, req.user_id)
    # LLMOps: attribute this conversation's traces (fail-open).
    try:
        from chatbot import llmops

        llmops.set_session_context(f"{req.user_id}:{getattr(req, 'session_id', '') or ''}")
    except Exception:
        pass
    return await run_chat_pipeline(req)


@app.get("/chat_history/{user_id}", tags=["chat"])
async def get_chat_history(user_id: int, request: Request) -> dict[str, Any]:
    """Return the in-memory conversation history for *user_id*."""
    _enforce_user_scope(request, user_id)
    return {"user_id": user_id, "history": get_history(user_id)}


@app.delete("/chat_history/{user_id}", tags=["chat"])
async def clear_chat_history(user_id: int, request: Request) -> dict[str, str]:
    """Clear the conversation history for *user_id*."""
    _enforce_user_scope(request, user_id)
    clear_history(user_id)
    log.info("clear_chat_history → cleared history for user_id=%s", user_id)
    return {"status": "cleared", "user_id": str(user_id)}


# ── Sessions ────────────────────────────────────────────────────────────────
@app.post("/sessions/{user_id}", tags=["sessions"])
async def create_session(user_id: int, request: Request, body: dict[str, Any] | None = None) -> dict[str, Any]:
    """Create a new chat session for the user."""
    _enforce_user_scope(request, user_id)
    title = (body or {}).get("title", "")
    return session_store.create_session(user_id, title=title)


@app.get("/sessions/{user_id}", tags=["sessions"])
async def list_sessions(user_id: int, request: Request) -> list[dict[str, Any]]:
    """List all sessions (metadata) for a user, newest first."""
    _enforce_user_scope(request, user_id)
    return session_store.list_sessions(user_id)


@app.get("/sessions/{user_id}/{session_id}", tags=["sessions"])
async def get_session(user_id: int, session_id: str, request: Request) -> dict[str, Any]:
    """Load a full session with messages."""
    _enforce_user_scope(request, user_id)
    data = session_store.get_session(user_id, session_id)
    if data is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Session not found")
    return data


@app.post("/sessions/{user_id}/{session_id}/messages", tags=["sessions"])
async def add_session_message(user_id: int, session_id: str, body: dict[str, Any], request: Request) -> dict[str, Any]:
    """Append a message to a session."""
    _enforce_user_scope(request, user_id)
    role = body.get("role", "user")
    content = body.get("content", "")
    ai_responded = body.get("ai_responded", True)
    latency_ms = body.get("latency_ms")
    if not content:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="content is required")
    try:
        return session_store.add_message(
            user_id, session_id, role, content, ai_responded=ai_responded,
            latency_ms=latency_ms,
        )
    except ValueError as exc:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=str(exc))


@app.patch("/sessions/{user_id}/{session_id}", tags=["sessions"])
async def rename_session(user_id: int, session_id: str, body: dict[str, Any], request: Request) -> dict[str, Any]:
    """Rename a session's title."""
    _enforce_user_scope(request, user_id)
    title = body.get("title", "").strip()
    if not title:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="title is required")
    result = session_store.update_title(user_id, session_id, title)
    if result is None:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Session not found")
    return result


@app.delete("/sessions/{user_id}/{session_id}", tags=["sessions"])
async def delete_session(user_id: int, session_id: str, request: Request) -> dict[str, str]:
    """Delete a session."""
    _enforce_user_scope(request, user_id)
    ok = session_store.delete_session(user_id, session_id)
    if not ok:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Session not found")
    return {"status": "deleted"}


# ── Feedback (chat memory) ──────────────────────────────────────────────────
@app.post("/feedback/{user_id}", tags=["feedback"])
async def submit_feedback(user_id: int, body: dict[str, Any], request: Request) -> dict[str, str]:
    """Store feedback for a bot response (background, non-blocking)."""
    _enforce_user_scope(request, user_id)
    session_id = body.get("session_id", "")
    query = body.get("query", "")
    response = body.get("response", "")
    rating = body.get("rating", "")  # "up" or "down"
    comment = body.get("comment", "")
    if rating not in ("up", "down"):
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="rating must be 'up' or 'down'")
    session_store.add_feedback(user_id, session_id, query, response, rating, comment)
    return {"status": "ok"}


# ── Dashboard (chat analytics) ──────────────────────────────────────────────
@app.get("/chat/dashboard/data", tags=["dashboard"])
async def chat_dashboard_data() -> dict[str, Any]:
    """Aggregated chart data from chat_sessions + chat_memory (parallel scan)."""
    return await session_store.dashboard_data()


@app.get("/chat/dashboard", response_class=HTMLResponse, tags=["dashboard"])
async def chat_dashboard() -> HTMLResponse:
    """Render the chat-analytics dashboard (charts load from /chat/dashboard/data)."""
    return HTMLResponse(content=_DASHBOARD_HTML)


# ── Health ──────────────────────────────────────────────────────────────────
@app.get("/health", tags=["health"])
async def health() -> dict[str, str]:
    """Liveness probe — returns ``{"status": "ok"}``."""
    return {"status": "ok"}


# ── Ingestion (mounted router) ───────────────────────────────────────────────
app.include_router(ingest_router)

log.info("APIX Chatbot API ready — endpoints: chat, health, ingestion")


# ═══════════════════════════════════════════════════════════════════════════
# DASHBOARD HTML — single-page chart view consuming /chat/dashboard/data
# ═══════════════════════════════════════════════════════════════════════════
_DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>APIX Chat Analytics</title>
  <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
  <style>
    :root { --bg:#0f172a; --card:#1e293b; --txt:#e2e8f0; --muted:#94a3b8;
            --accent:#0F9ED5; --green:#10b981; --red:#ef4444; --border:#334155; }
    * { box-sizing:border-box; }
    body { margin:0; background:var(--bg); color:var(--txt);
           font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif; }
    header { padding:20px 28px; border-bottom:1px solid var(--border);
             display:flex; align-items:center; justify-content:space-between; }
    header h1 { font-size:20px; margin:0; }
    header .meta { color:var(--muted); font-size:13px; }
    main { padding:24px 28px; max-width:1280px; margin:0 auto; }
    .kpis { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr));
            gap:14px; margin-bottom:24px; }
    .kpi { background:var(--card); border:1px solid var(--border); border-radius:12px;
           padding:16px 18px; }
    .kpi .v { font-size:26px; font-weight:700; }
    .kpi .l { color:var(--muted); font-size:12px; margin-top:4px;
              text-transform:uppercase; letter-spacing:.04em; }
    .kpi.good .v { color:var(--green); }
    .kpi.warn .v { color:#f59e0b; }
    .kpi.bad .v { color:var(--red); }
    .insight { background:var(--card); border:1px solid var(--border); border-left:4px solid var(--accent);
               border-radius:10px; padding:14px 18px; margin-bottom:20px; font-size:14px;
               color:var(--txt); display:none; }
    .insight.show { display:block; }
    .insight b { color:var(--accent); }
    .grid { display:grid; grid-template-columns:repeat(2,1fr); gap:18px; }
    .panel { background:var(--card); border:1px solid var(--border);
             border-radius:12px; padding:16px 18px; }
    .panel h3 { margin:0 0 12px; font-size:14px; color:var(--muted);
                font-weight:600; }
    .panel.full { grid-column:1 / -1; }
    table { width:100%; border-collapse:collapse; font-size:13px; }
    th,td { text-align:left; padding:8px 10px; border-bottom:1px solid var(--border); }
    th { color:var(--muted); font-weight:600; }
    .pill { padding:2px 8px; border-radius:999px; font-size:11px; font-weight:600; }
    .pill.up { background:rgba(16,185,129,.15); color:var(--green); }
    .pill.down { background:rgba(239,68,68,.15); color:var(--red); }
    .err { color:var(--red); padding:20px; }
    @media (max-width:880px){ .grid{ grid-template-columns:1fr; } }
  </style>
</head>
<body>
  <header>
    <h1>📊 APIX Chat Analytics</h1>
    <div class="meta" id="meta">Loading…</div>
  </header>
  <main>
    <div class="kpis" id="kpis"></div>
    <div id="perfInsight" class="insight"></div>
    <div class="grid">
      <div class="panel"><h3>Sessions per day</h3><canvas id="sessionsDay"></canvas></div>
      <div class="panel"><h3>Messages per day</h3><canvas id="messagesDay"></canvas></div>
      <div class="panel"><h3>Response time percentiles (ms)</h3><canvas id="latPct"></canvas></div>
      <div class="panel"><h3>Response time distribution</h3><canvas id="latDist"></canvas></div>
      <div class="panel full"><h3>Average response time trend (ms/day)</h3><canvas id="latTrend"></canvas></div>
      <div class="panel"><h3>Feedback breakdown</h3><canvas id="fbBreakdown"></canvas></div>
      <div class="panel"><h3>AI response success</h3><canvas id="aiResp"></canvas></div>
      <div class="panel"><h3>Feedback per day</h3><canvas id="fbDay"></canvas></div>
      <div class="panel"><h3>Top users by sessions</h3><canvas id="topUsers"></canvas></div>
      <div class="panel full"><h3>Recent feedback</h3><div id="recent"></div></div>
    </div>
    <div id="error" class="err"></div>
  </main>
  <script>
    const C = { txt:'#e2e8f0', muted:'#94a3b8', accent:'#0F9ED5',
                green:'#10b981', red:'#ef4444', grid:'#334155' };
    Chart.defaults.color = C.muted;
    Chart.defaults.borderColor = C.grid;

    function kpi(v, l){ return `<div class="kpi"><div class="v">${v}</div><div class="l">${l}</div></div>`; }
    function kpiC(v, l, cls){ return `<div class="kpi ${cls||''}"><div class="v">${v}</div><div class="l">${l}</div></div>`; }
    function fmtMs(ms){ if(ms==null) return '–'; return ms>=1000 ? (ms/1000).toFixed(1)+'s' : Math.round(ms)+'ms'; }

    function line(id, labels, data, color){
      new Chart(document.getElementById(id), {
        type:'line',
        data:{ labels, datasets:[{ data, borderColor:color, backgroundColor:color+'33',
               tension:.3, fill:true, pointRadius:2 }] },
        options:{ plugins:{legend:{display:false}}, scales:{ y:{beginAtZero:true} } }
      });
    }
    function doughnut(id, labels, data, colors){
      new Chart(document.getElementById(id), {
        type:'doughnut',
        data:{ labels, datasets:[{ data, backgroundColor:colors, borderWidth:0 }] },
        options:{ plugins:{legend:{position:'bottom'}} }
      });
    }
    function bars(id, labels, datasets, stacked){
      new Chart(document.getElementById(id), {
        type:'bar', data:{ labels, datasets },
        options:{ plugins:{legend:{display:datasets.length>1,position:'bottom'}},
                  scales:{ x:{stacked:!!stacked}, y:{stacked:!!stacked,beginAtZero:true} } }
      });
    }

    async function load(){
      try {
        const r = await fetch('/chat/dashboard/data');
        if(!r.ok) throw new Error('HTTP '+r.status);
        const d = await r.json();
        const k = d.kpis;
        const p95cls = k.p95_latency_ms >= (k.slow_threshold_s*1000) ? 'bad'
                       : (k.p95_latency_ms >= (k.slow_threshold_s*1000)/2 ? 'warn' : 'good');
        const slowcls = k.slow_pct >= 20 ? 'bad' : (k.slow_pct >= 5 ? 'warn' : 'good');
        document.getElementById('kpis').innerHTML =
          kpi(k.users,'Users') + kpi(k.sessions,'Sessions') + kpi(k.messages,'Messages') +
          kpi(k.avg_msgs_per_session,'Avg msgs/session') + kpi(k.feedback,'Feedback') +
          kpi(k.satisfaction_pct+'%','Satisfaction') + kpi(k.ai_success_pct+'%','AI success') +
          kpiC(fmtMs(k.avg_latency_ms),'Avg response') +
          kpiC(fmtMs(k.p95_latency_ms),'P95 response', p95cls) +
          kpiC(k.slow_responses+' ('+k.slow_pct+'%)','Slow (>'+k.slow_threshold_s+'s)', slowcls);
        document.getElementById('meta').textContent = 'Generated ' + (d.generated_at||'');

        // Performance insight banner — data-driven narrative.
        const ls = d.latency_summary || {};
        const ins = document.getElementById('perfInsight');
        if ((ls.measured||0) > 0) {
          let verdict = k.p95_latency_ms < 5000 ? 'healthy'
                      : (k.p95_latency_ms < (k.slow_threshold_s*1000) ? 'acceptable' : 'degraded');
          let msg = `Across <b>${ls.measured}</b> measured responses, the average reply takes `
            + `<b>${fmtMs(ls.avg_ms)}</b> (median ${fmtMs(ls.p50_ms)}, P95 ${fmtMs(ls.p95_ms)}, `
            + `P99 ${fmtMs(ls.p99_ms)}). Performance looks <b>${verdict}</b>. `;
          if (k.slow_responses > 0) {
            msg += `<b>${k.slow_responses}</b> response(s) (${k.slow_pct}%) exceeded `
              + `${k.slow_threshold_s}s — the slowest took <b>${fmtMs(ls.slowest_ms)}</b>.`;
          } else {
            msg += `No responses exceeded the ${k.slow_threshold_s}s threshold.`;
          }
          ins.innerHTML = msg;
          ins.classList.add('show');
        }

        line('sessionsDay', d.sessions_per_day.labels, d.sessions_per_day.counts, C.accent);
        line('messagesDay', d.messages_per_day.labels, d.messages_per_day.counts, C.green);

        // Latency charts.
        const lp = d.latency_percentiles || {labels:[],counts:[]};
        bars('latPct', lp.labels, [{ label:'ms', data:lp.counts,
             backgroundColor:[C.accent,C.accent,'#f59e0b',C.red] }], false);
        const ld = d.latency_distribution || {labels:[],counts:[]};
        bars('latDist', ld.labels, [{ label:'Responses', data:ld.counts,
             backgroundColor:[C.green,C.green,'#84cc16','#f59e0b','#f97316',C.red] }], false);
        const lt = d.latency_trend || {labels:[],avg_ms:[]};
        line('latTrend', lt.labels, lt.avg_ms, '#f59e0b');

        doughnut('fbBreakdown', ['👍 Up','👎 Down'],
                 [d.feedback_breakdown.up, d.feedback_breakdown.down], [C.green, C.red]);
        doughnut('aiResp', ['Responded','Failed'],
                 [d.ai_response.responded, d.ai_response.failed], [C.accent, C.red]);
        bars('fbDay', d.feedback_per_day.labels, [
          { label:'Up', data:d.feedback_per_day.up, backgroundColor:C.green },
          { label:'Down', data:d.feedback_per_day.down, backgroundColor:C.red },
        ], true);
        bars('topUsers', d.top_users.labels.map(String),
             [{ label:'Sessions', data:d.top_users.counts, backgroundColor:C.accent }], false);

        const rows = d.recent_feedback.map(f =>
          `<tr><td>${f.user_id ?? ''}</td>
               <td><span class="pill ${f.rating}">${f.rating}</span></td>
               <td>${(f.query||'').replace(/</g,'&lt;')}</td>
               <td>${(f.comment||'').replace(/</g,'&lt;')}</td>
               <td>${f.timestamp||''}</td></tr>`).join('');
        document.getElementById('recent').innerHTML = rows
          ? `<table><thead><tr><th>User</th><th>Rating</th><th>Query</th><th>Comment</th><th>When</th></tr></thead><tbody>${rows}</tbody></table>`
          : '<p style="color:var(--muted)">No feedback yet.</p>';
      } catch (e) {
        document.getElementById('error').textContent = 'Failed to load dashboard: ' + e.message;
      }
    }
    load();
  </script>
</body>
</html>"""


