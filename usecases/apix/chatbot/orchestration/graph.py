"""
chatbot/graph.py — LangGraph chat orchestrator (state + nodes + builder).

A single ``ChatState`` dict flows through every node.  Each **node** is a thin
async function that performs one step inside a trace span and returns a partial
state; each **edge** function makes a routing decision and records it on the
trace.  All heavy lifting lives in :mod:`chatbot.pipeline` (classifier,
conversation, cache, source-router) and the two data-source agents
(:mod:`chatbot.sqlite_source` / :mod:`chatbot.azure_source`) — the graph only
sequences them, so no LLM calls are added and latency is unchanged.

Pipeline (query flow)::

    classify ─┬─greeting/off_topic────────────────────────────────► respond
              └─on_topic─► scope ─► ambiguity ─┬─ambiguous────────► respond
                                               └─► cache ─┬─hit───► respond
                                                          └─miss─► rewrite_route
                                                                        │
                              ┌──────────── route by source ────────────┤
                              ▼                                          ▼
                        sqlite_agent (data #1)                  azure_agent (data #2)
                              │                                          │
                   ┌──────────┘                          ┌───────────────┤
                   │ (conditional: rows?)                │ (conditional: rows?)
                   ▼                                     ▼               ▼ no rows
                narrate ◄───────────────────────────── narrate     sqlite_agent (fallback)
                   │                                                     │
                   └──────────────► finalize ─► respond ◄────────────────┘

Conditional data-agent edges
----------------------------
Each data agent ends in a *conditional* edge rather than an unconditional one:

  * ``azure_agent`` → ``narrate`` when it returns rows; otherwise it **falls
    back** to ``sqlite_agent`` (the always-available local store) so an empty
    Azure result still gets a second chance against local data.
  * ``sqlite_agent`` → ``narrate`` always (it is the terminal data source and
    never routes back, so the graph stays loop-free).

The routing decision is recorded on the trace via ``tr.edge(...)`` for both
the "has rows" and "no rows" branches.

Data-source flag (``SOURCE_MODE``)
----------------------------------
The ``SOURCE_MODE`` config flag forces which source(s) are queried:

  * ``"auto"``   — score-based routing (default).
  * ``"sqlite"`` — local SQLite only.
  * ``"azure"``  — Azure SQL only (downgrades to SQLite if Azure isn't set up).
  * ``"both"``   — query **both** sources: the Azure agent always continues to
    the SQLite agent and their rows are merged before narration.
"""

from __future__ import annotations

from typing import Any, TypedDict

from fastapi import HTTPException
from langgraph.graph import StateGraph, END

from chatbot.core.config import get_logger
from chatbot.core.tracing import Trace
from chatbot.core.schemas import ChatRequest, ChatResponse
from chatbot.orchestration.pipeline import (
    DECLINE_MSG,
    check_ambiguous,
    classify_query,
    greeting_response,
    resolve_scope,
    narrow_scope,
    parse_mentions,
    route_source,
    cache_key,
    cache_get,
    cache_put,
    get_history,
    add_to_history,
    clear_history,
    is_same_session,
    set_active_session,
    resolve_context,
    merge_entities,
    entity_clarification,
    needs_period_clarification,
    rewrite_query,
    out_of_scope_message,
    record_qa,
    _HISTORY_MAX_MSGS,
)
from chatbot.sources.sqlite_source import (
    generate_sql,
    validate_sql,
    SQLValidationError,
    execute_sql,
    should_build_profile,  # noqa: F401  (kept for API compatibility)
    agent_analysis_focus,
    build_agent_profile,
    agent_in_scope,
    available_periods,
    is_profile_query,
)
from chatbot.llm.extract import extract_entities, quick_greeting
from chatbot.sources.azure_source import (
    generate_rep_sql,
    validate_rep_sql,
    RepSQLValidationError,
    execute_rep_sql,
)
from chatbot.llm import generate_response, summarize_coaching, is_coaching_rows

log = get_logger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# STATE
# ═══════════════════════════════════════════════════════════════════════════
class ChatState(TypedDict, total=False):
    """End-to-end state for one chat request as it traverses the graph."""

    # ── Request (set at entry) ──────────────────────────────────────────────
    user_query: str                   # query with @-directives stripped
    raw_query: str                    # original text as typed (for the echo)
    user_id: int
    user_name: str
    role: str
    reporting_agents: Any  # list[int] (coach) | dict[str, int] (manager)
    source_override: str | None       # "sqlite" | "azure" from an @-directive
    scope_override: bool              # True → @no-agent narrows the roster
    session_id: str | None            # active session for loading history
    focus_agent_id: int | None        # UI-selected agent (authoritative context)
    focus_scope: str                  # "agent" | "team" — UI focus mode
    req_period: str | None            # UI-selected week/period

    # ── Observability ───────────────────────────────────────────────────────
    trace: Trace

    # ── Derived during the run ──────────────────────────────────────────────
    intent: str                       # greeting | on_topic | off_topic
    entities: Any                     # QueryEntities from the single LLM extract
    scope: dict                       # {role, employee_ids, coach_ids}
    history: list[dict]               # short-term conversation history
    eff_period: str | None            # carried-forward / resolved period
    eff_agent: dict | None            # carried-forward / resolved agent
    eff_metric: str | None            # carried-forward / resolved metric
    rewritten: str                    # self-contained, context-injected query
    source: str                       # "sqlite" | "azure_sql" | "both"
    merge: bool                       # True → fetch both sources and merge rows
    rep_matches: list[dict]           # rep_pivoted matches (for azure agent)
    cache_key: str
    sql: str
    data: list[dict]                  # SQL result rows

    # ── Output / control ────────────────────────────────────────────────────
    answer: str                       # natural-language response
    details: str | None               # full-length text when answer is a summary
    done: bool                        # early-return marker (short-circuit)
    response: ChatResponse            # final payload built by the respond node


# ═══════════════════════════════════════════════════════════════════════════
# NODES
# ═══════════════════════════════════════════════════════════════════════════
async def classify_node(state: ChatState) -> dict:
    """Understand the query with a single low-latency LLM call.

    A trivial greeting/thanks short-circuits with zero LLM cost via
    :func:`quick_greeting`. Otherwise one :func:`extract_entities` call gets the
    intent AND the entities (agent/period/metric/follow-up) at once; the result
    is stashed on ``state['entities']`` and reused by every downstream node, so
    the whole request still costs exactly one extraction call.
    """
    tr = state["trace"]
    query = state["user_query"]

    if quick_greeting(query):
        with tr.span("classify") as sp:
            sp["intent"] = "greeting"
            sp["fast_path"] = True
        answer = greeting_response(state["user_name"], state["role"])
        return {"intent": "greeting", "answer": answer, "data": [], "done": True}

    history = get_history(state["user_id"], state.get("session_id"))
    with tr.span("extract") as sp:
        entities = await extract_entities(query, history, available_periods())
        sp["intent"] = entities.intent
        sp["scope"] = entities.scope
        sp["agent_name"] = entities.agent_name
        sp["period"] = entities.period
        sp["followup"] = entities.is_followup

    if entities.intent == "greeting":
        answer = greeting_response(state["user_name"], state["role"])
        return {"intent": "greeting", "entities": entities,
                "answer": answer, "data": [], "done": True}
    if entities.intent == "off_topic":
        return {"intent": "off_topic", "entities": entities,
                "answer": DECLINE_MSG, "data": [], "done": True}
    return {"intent": "on_topic", "entities": entities}


async def scope_node(state: ChatState) -> dict:
    """Resolve employee/coach scope and load short-term history (no LLM)."""
    tr = state["trace"]
    with tr.span("load_history") as sp:
        user_id = state["user_id"]
        session_id = state.get("session_id")
        history = get_history(user_id, session_id)

        # Per the session id the frontend sends on every hit, decide whether
        # this is the SAME session (keep the in-memory short-term memory) or a
        # NEW/different one (reload context from blob storage). A cold server
        # with no in-memory history also triggers a reload. Because short-term
        # memory and carried context are keyed by (user_id, session_id), every
        # conversation is already isolated — switching sessions cannot surface
        # another session's agent/period, so no explicit clear is required.
        same_session = is_same_session(user_id, session_id)
        sp["same_session"] = same_session
        sp["session_id"] = session_id

        if (not same_session) or not history:
            from chatbot.core import sessions as _sess
            import os

            if not same_session:
                log.info("scope_node → active session for user=%d is now %s → "
                         "seeding context from blob", user_id, session_id)

            # Clear only THIS conversation's in-memory copy before re-seeding so
            # revisiting a session doesn't append duplicate turns. Other
            # sessions' memory (under their own keys) is left untouched.
            clear_history(user_id, session_id)
            # Re-seed short-term memory with the last 5 conversations
            # (=_HISTORY_MAX_MSGS messages) of THIS running session only,
            # loaded from Azure blob storage by session_id. Never blends other
            # sessions, so it cannot surface stale "old query" turns.
            seeded = _sess.session_context_messages(
                user_id,
                session_id,
                max_messages=_HISTORY_MAX_MSGS,
                exclude_query=state["user_query"],
            )
            # No anchoring session id (or a brand-new empty session): fall back
            # to the user's most recent conversations for cross-session
            # continuity. A selected session that exists is never blended.
            if not seeded and not session_id:
                _max_sessions = int(os.getenv("CHAT_RECENT_SESSIONS", "5"))
                seeded = _sess.recent_context_messages(
                    user_id,
                    current_session_id=session_id,
                    max_sessions=_max_sessions,
                    max_messages=_HISTORY_MAX_MSGS,
                )

            for m in seeded:
                add_to_history(user_id, m["role"], m["content"], session_id)

            set_active_session(user_id, session_id)
            history = get_history(user_id, session_id)
            sp["seeded_from_blob"] = True
            sp["seeded_msgs"] = len(seeded)
        sp["msgs"] = len(history)
    with tr.span("resolve_scope") as sp:
        scope = resolve_scope(state["role"], state["reporting_agents"])
        # @no-agent → derive the reporting agents from the query / history
        # (intersected with the authorised roster, so it can only narrow).
        if state.get("scope_override"):
            scope = narrow_scope(
                scope, state["user_query"], history, state["reporting_agents"]
            )
        sp["employees"] = len(scope["employee_ids"])
        sp["coaches"] = len(scope["coach_ids"])
        sp["narrowed"] = bool(state.get("scope_override"))
    return {"scope": scope, "history": history}


async def ambiguity_node(state: ChatState) -> dict:
    """Ask a clarifying question when the query can't stand on its own."""
    tr = state["trace"]
    entities = state.get("entities")
    with tr.span("ambiguity") as sp:
        # Prefer the entity-grounded gate (roster/period validated
        # deterministically); fall back to the regex check only if extraction
        # produced nothing.
        if entities is not None:
            # A broad overview ("tell me about X", "how is X doing") is a
            # COMPLETE request — never bounce it back asking which metric. Drop
            # the LLM's metric-clarification for profile queries with an
            # identified agent, but still honor deterministic roster/period
            # validation (an unknown agent / unloaded week still clarifies).
            if is_profile_query(state["user_query"]) and (
                entities.agent_name or entities.employee_id
            ):
                entities.clarification = None
            clarify = entity_clarification(entities)
        else:
            clarify = check_ambiguous(state["user_query"], state["history"])
        # When the query targets a concrete weekly metric but names no period,
        # ask "for which week?" before answering (instead of silently defaulting
        # to the latest week).
        if clarify is None:
            clarify = needs_period_clarification(
                state["user_query"], entities,
                user_id=state["user_id"],
                session_id=state.get("session_id"),
                req_period=state.get("req_period"),
                focus_agent_id=state.get("focus_agent_id"),
            )
        sp["ambiguous"] = clarify is not None
    if clarify is not None:
        add_to_history(state["user_id"], "user", state["user_query"], state.get("session_id"))
        add_to_history(state["user_id"], "assistant", clarify, state.get("session_id"))
        return {"answer": clarify, "data": [], "done": True}
    return {}


async def cache_node(state: ChatState) -> dict:
    """Scope-aware cache lookup — a hit skips both LLM calls entirely."""
    tr = state["trace"]
    scope = state["scope"]
    with tr.span("cache_lookup") as sp:
        # Fold the source directive into the key so @sqlite / @sql variants of
        # the same question don't return each other's cached answer.
        key_query = state["user_query"]
        if state.get("source_override"):
            key_query = f"@{state['source_override']} {key_query}"
        ck = cache_key(
            key_query, state["user_id"], state["role"],
            scope["employee_ids"], scope["coach_ids"],
            focus_agent_id=state.get("focus_agent_id"),
            period=state.get("req_period"),
        )
        cached = cache_get(ck)
        sp["hit"] = cached is not None

    if cached is not None:
        add_to_history(state["user_id"], "user", state["user_query"], state.get("session_id"))
        add_to_history(state["user_id"], "assistant", cached[1], state.get("session_id"))
        return {"cache_key": ck, "data": cached[0], "answer": cached[1], "done": True}
    return {"cache_key": ck}


async def rewrite_route_node(state: ChatState) -> dict:
    """Carry context → rewrite into a standalone query → route to a source."""
    tr = state["trace"]
    entities = state.get("entities")
    with tr.span("rewrite") as sp:
        if entities is not None:
            eff_period, eff_agent, eff_metric, _clarify = merge_entities(
                state["user_id"], entities,
                session_id=state.get("session_id"),
                focus_agent_id=state.get("focus_agent_id"),
                focus_scope=state.get("focus_scope", "agent"),
                req_period=state.get("req_period"),
            )
        else:  # extraction unavailable — deterministic fallback
            eff_period, eff_agent, eff_metric = resolve_context(
                state["user_id"], state["user_query"],
                session_id=state.get("session_id"),
                focus_agent_id=state.get("focus_agent_id"),
                focus_scope=state.get("focus_scope", "agent"),
                req_period=state.get("req_period"),
            )
        sp["eff_agent"] = eff_agent

    # ── Scope guard (RBAC) ──────────────────────────────────────────────────
    # If the effective agent is outside the caller's authorised roster, refuse
    # explicitly with a validation message instead of silently returning empty
    # data and narrating a generic "no results" answer.
    if eff_agent and not agent_in_scope(eff_agent, state["scope"]["employee_ids"]):
        with tr.span("scope_guard") as sp:
            sp["blocked_agent"] = eff_agent
        msg = out_of_scope_message(eff_agent)
        add_to_history(state["user_id"], "user", state["user_query"], state.get("session_id"))
        add_to_history(state["user_id"], "assistant", msg, state.get("session_id"))
        log.info("rewrite_route → agent %s out of scope for user=%d → refused",
                 eff_agent, state["user_id"])
        return {
            "eff_period": eff_period,
            "eff_agent": eff_agent,
            "eff_metric": eff_metric,
            "answer": msg,
            "data": [],
            "done": True,
        }

    with tr.span("rewrite") as sp:
        rewritten = rewrite_query(
            state["user_query"], eff_period, eff_agent, eff_metric
        )
        sp["rewritten"] = rewritten
    with tr.span("route_source") as sp:
        source, rep_matches = route_source(rewritten, state.get("source_override"))
        sp["source"] = source
        sp["override"] = state.get("source_override")
        sp["rep_matches"] = len(rep_matches)
    return {
        "eff_period": eff_period,
        "eff_agent": eff_agent,
        "eff_metric": eff_metric,
        "rewritten": rewritten,
        "source": source,
        "merge": source == "both",
        "rep_matches": rep_matches,
    }


async def sqlite_agent_node(state: ChatState) -> dict:
    """Data agent #1 — local SQLite KPI store (generate → validate → execute)."""
    tr = state["trace"]
    scope = state["scope"]

    # Consolidated, program-aware analysis short-circuit for a named agent —
    # covers both "tell me about <agent>" (overview) and operational-risk /
    # actions-to-be-taken (risk). Spans several tables (KPIs + behaviours +
    # comparison radar + trends + improvements + coaching), so it is assembled
    # directly rather than via a single generated SELECT.
    analysis_focus = agent_analysis_focus(state["rewritten"])
    if state.get("eff_agent") and analysis_focus:
        with tr.span("sqlite_agent.profile", source="sqlite") as sp:
            sp["focus"] = analysis_focus
            profile = build_agent_profile(
                agent=state["eff_agent"],
                period=state["eff_period"],
                employee_ids=scope["employee_ids"],
                role=scope["role"],
                focus=analysis_focus,
            )
            sp["rows"] = len(profile)
        if profile:
            existing = state.get("data") or []
            return {"sql": "<agent profile>", "data": existing + profile}
        log.info("sqlite_agent.profile → no profile rows; falling back to generation")

    with tr.span("sqlite_agent.generate", source="sqlite") as sp:
        sp["explicit"] = state.get("source_override") == "sqlite"
        sql = await generate_sql(
            state["rewritten"],
            chat_history=state["history"] or None,
            period=state["eff_period"],
            agent=state["eff_agent"],
            explicit=state.get("source_override") == "sqlite",
            raw_question=state["user_query"],
        )
        sp["sql_len"] = len(sql)

    # An intentionally-empty result means the question is out of scope for this
    # database (e.g. "career guidance" — no such data exists). Return no rows so
    # narration answers with the graceful no-data / out-of-scope message rather
    # than raising a validation error.
    if not sql or not sql.strip():
        log.info("sqlite_agent → empty SQL (out-of-scope question); returning no rows")
        return {"sql": "", "data": state.get("data") or []}

    with tr.span("sqlite_agent.validate") as sp:
        try:
            sql = validate_sql(sql)
        except SQLValidationError as exc:
            sp["rejected"] = str(exc)
            log.warning("SQL validation failed: %s | SQL: %s", exc, sql)
            raise HTTPException(status_code=422, detail=f"Generated SQL failed validation: {exc}")

    with tr.span("sqlite_agent.execute") as sp:
        result = execute_sql(
            sql,
            employee_ids=scope["employee_ids"],
            coach_ids=scope["coach_ids"],
            role=scope["role"],
        )
        sp["rows"] = len(result)

    # In "both"/fallback flows the Azure agent may have already produced rows;
    # merge them so narration sees the combined result set.
    existing = state.get("data") or []
    return {"sql": sql, "data": existing + result}


async def azure_agent_node(state: ChatState) -> dict:
    """Data agent #2 — Azure SQL ``vzw.rep_pivoted`` (generate → validate → execute)."""
    tr = state["trace"]
    scope = state["scope"]

    with tr.span("azure_agent.generate", source="azure_sql") as sp:
        sp["explicit"] = state.get("source_override") == "azure"
        sql = await generate_rep_sql(
            state["rewritten"],
            chat_history=state["history"] or None,
            period=state["eff_period"],
            matches=state["rep_matches"],
            explicit=state.get("source_override") == "azure",
        )
        sp["sql_len"] = len(sql)

    with tr.span("azure_agent.validate") as sp:
        try:
            sql = validate_rep_sql(sql)
        except RepSQLValidationError as exc:
            sp["rejected"] = str(exc)
            log.warning("Rep SQL validation failed: %s | SQL: %s", exc, sql)
            raise HTTPException(status_code=422, detail=f"Generated SQL failed validation: {exc}")

    with tr.span("azure_agent.execute") as sp:
        result = execute_rep_sql(
            sql,
            employee_ids=scope["employee_ids"],
            coach_ids=scope["coach_ids"],
            role=scope["role"],
        )
        sp["rows"] = len(result)

    return {"sql": sql, "data": result}


async def narrate_node(state: ChatState) -> dict:
    """Turn result rows into a professional NL answer (deterministic or LLM)."""
    tr = state["trace"]
    with tr.span("narrate") as sp:
        # State the week single-metric answers refer to, using the period the
        # query actually resolved to (UI-selected or explicitly asked), so the
        # answer never leaves the week ambiguous and pre-empts "for which week?".
        answer = await generate_response(
            state["user_query"],
            state["data"],
            rewritten_query=state.get("rewritten"),
            period=state.get("eff_period"),
        )
        details = None
        # Coaching tips are long. Show short LLM pointers by default and keep
        # the full breakdown behind a "Show full message" toggle in the UI.
        if is_coaching_rows(state.get("data", [])):
            short = await summarize_coaching(state["data"])
            if short and short.strip() != answer.strip():
                details = answer      # full breakdown
                answer = short        # short pointers become the primary reply
                sp["coaching_summarized"] = True
        sp["answer_len"] = len(answer)
    return {"answer": answer, "details": details}


async def finalize_node(state: ChatState) -> dict:
    """Full-path side-effects: store the answer in cache + history."""
    tr = state["trace"]
    with tr.span("finalize") as sp:
        cache_put(state["cache_key"], state["data"], state["answer"])
        add_to_history(state["user_id"], "user", state["user_query"], state.get("session_id"))
        add_to_history(state["user_id"], "assistant", state["answer"], state.get("session_id"))
        sp["rows"] = len(state["data"])
    return {}


async def respond_node(state: ChatState) -> dict:
    """Terminal node — build the ChatResponse and log the trace waterfall."""
    tr = state["trace"]
    resp = ChatResponse(
        query=state.get("raw_query") or state["user_query"],
        data=state.get("data", []),
        response=state.get("answer", ""),
        details=state.get("details"),
        elapsed_ms=tr.elapsed_ms(),
    )
    # Structured Q&A audit: query + answer + the data source/tables it came from.
    try:
        record_qa(
            user_id=state.get("user_id"),
            session_id=state.get("session_id"),
            role=state.get("role"),
            query=state.get("raw_query") or state["user_query"],
            rewritten=state.get("rewritten"),
            source=state.get("source"),
            sql=state.get("sql"),
            agent=state.get("eff_agent"),
            period=state.get("eff_period"),
            row_count=len(state.get("data", [])),
            answer=state.get("answer", ""),
            elapsed_ms=tr.elapsed_ms(),
            trace_id=getattr(tr, "trace_id", None),
        )
    except Exception as e:  # audit logging must never break a reply
        log.warning("respond_node → record_qa failed: %s", e)
    log.info(tr.summary())
    return {"response": resp}


# ═══════════════════════════════════════════════════════════════════════════
# EDGES (conditional routers — each decision is recorded on the trace)
# ═══════════════════════════════════════════════════════════════════════════
def route_after_classify(state: ChatState) -> str:
    tr = state["trace"]
    if state.get("done"):
        return tr.edge("classify", "respond", state.get("intent", "early"))
    return tr.edge("classify", "scope", "on_topic")


def route_after_ambiguity(state: ChatState) -> str:
    tr = state["trace"]
    if state.get("done"):
        return tr.edge("ambiguity", "respond", "clarify")
    return tr.edge("ambiguity", "cache", "clear")


def route_after_cache(state: ChatState) -> str:
    tr = state["trace"]
    if state.get("done"):
        return tr.edge("cache", "respond", "hit")
    return tr.edge("cache", "rewrite_route", "miss")


def route_by_source(state: ChatState) -> str:
    """Pick which data agent runs first based on the source router.

    ``azure_sql`` and ``both`` start at the Azure agent; ``sqlite`` starts at
    the SQLite agent. In ``both`` mode the Azure agent always continues to the
    SQLite agent (see :func:`route_after_azure`) so the rows are merged.
    """
    tr = state["trace"]
    if state.get("done"):
        # Scope guard refused the query — skip the data agents entirely.
        return tr.edge("rewrite_route", "respond", "out_of_scope")
    dst = "azure_agent" if state["source"] in ("azure_sql", "both") else "sqlite_agent"
    return tr.edge("rewrite_route", dst, state["source"])


def route_after_azure(state: ChatState) -> str:
    """Conditional edge for data agent #2 (Azure SQL).

    * ``both`` mode → always continue to ``sqlite_agent`` to fetch and merge the
      local rows too.
    * an explicit ``@sql`` directive → narrate the Azure result directly (no
      cross-source fallback): the user pinned this source, so the other one is
      removed from the flow even on an empty result.
    * otherwise → narrate when Azure returned rows; on an empty result fall
      back to the always-available local SQLite store for a second attempt.

    SQLite never routes back here, so neither path can loop.
    """
    tr = state["trace"]
    if state.get("merge"):
        return tr.edge("azure_agent", "sqlite_agent", "both→merge")
    if state.get("source_override") == "azure":
        return tr.edge("azure_agent", "narrate", "directed@sql")
    if state.get("data"):
        return tr.edge("azure_agent", "narrate", "rows")
    return tr.edge("azure_agent", "sqlite_agent", "no_rows→fallback")


def route_after_sqlite(state: ChatState) -> str:
    """Conditional edge for data agent #1 (SQLite) — terminal data source.

    Always proceeds to narration; the branch label records whether rows were
    found so the trace shows the empty-result path explicitly.
    """
    tr = state["trace"]
    label = "rows" if state.get("data") else "no_rows"
    return tr.edge("sqlite_agent", "narrate", label)


# ═══════════════════════════════════════════════════════════════════════════
# BUILDER
# ═══════════════════════════════════════════════════════════════════════════
def build_graph():
    """Construct and compile the chat pipeline state graph."""
    g = StateGraph(ChatState)

    # Nodes
    g.add_node("classify", classify_node)
    g.add_node("scope", scope_node)
    g.add_node("ambiguity", ambiguity_node)
    g.add_node("cache", cache_node)
    g.add_node("rewrite_route", rewrite_route_node)
    g.add_node("sqlite_agent", sqlite_agent_node)   # data agent #1
    g.add_node("azure_agent", azure_agent_node)     # data agent #2
    g.add_node("narrate", narrate_node)
    g.add_node("finalize", finalize_node)
    g.add_node("respond", respond_node)

    # Edges
    g.set_entry_point("classify")
    g.add_conditional_edges(
        "classify", route_after_classify,
        {"scope": "scope", "respond": "respond"},
    )
    g.add_edge("scope", "ambiguity")
    g.add_conditional_edges(
        "ambiguity", route_after_ambiguity,
        {"cache": "cache", "respond": "respond"},
    )
    g.add_conditional_edges(
        "cache", route_after_cache,
        {"rewrite_route": "rewrite_route", "respond": "respond"},
    )
    g.add_conditional_edges(
        "rewrite_route", route_by_source,
        {"sqlite_agent": "sqlite_agent", "azure_agent": "azure_agent", "respond": "respond"},
    )
    # Conditional data-agent edges: azure may fall back to sqlite on empty
    # results; sqlite always proceeds to narration (loop-free).
    g.add_conditional_edges(
        "azure_agent", route_after_azure,
        {"narrate": "narrate", "sqlite_agent": "sqlite_agent"},
    )
    g.add_conditional_edges(
        "sqlite_agent", route_after_sqlite,
        {"narrate": "narrate"},
    )
    g.add_edge("narrate", "finalize")
    g.add_edge("finalize", "respond")
    g.add_edge("respond", END)

    compiled = g.compile()
    log.info("chat graph compiled — nodes: classify→scope→ambiguity→cache→"
             "rewrite_route→{azure_agent⇢sqlite_agent|sqlite_agent}→narrate→"
             "finalize→respond")
    return compiled


# Compiled once and reused for every request.
GRAPH = build_graph()


async def run_chat_graph(req: ChatRequest) -> ChatResponse:
    """Execute the full NL → SQL → NL pipeline via the LangGraph orchestrator.

    Raises:
        HTTPException(422): Generated SQL failed safety validation.
        HTTPException(500): Any unhandled server error.
    """
    trace = Trace("chat", user_id=req.user_id, role=req.role,
                  query=req.user_query[:80])
    # Strip inline @-directives up front: they control source/scope, not SQL.
    cleaned_query, source_override, scope_override = parse_mentions(req.user_query)
    init: ChatState = {  # type: ignore[assignment]
        "user_query": cleaned_query,
        "raw_query": req.user_query,
        "user_id": req.user_id,
        "user_name": req.user_name,
        "role": req.role,
        "reporting_agents": req.reporting_agents,
        "source_override": source_override,
        "scope_override": scope_override,
        "session_id": req.session_id,
        "focus_agent_id": req.focus_agent_id,
        "focus_scope": req.focus_scope,
        "req_period": req.period,
        "trace": trace,
        "done": False,
    }
    try:
        final = await GRAPH.ainvoke(init)
        return final["response"]
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        log.exception("chat graph error id=%s", trace.trace_id)
        raise HTTPException(status_code=500, detail=str(exc))
