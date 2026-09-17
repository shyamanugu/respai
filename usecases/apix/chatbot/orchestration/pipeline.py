"""
chatbot/pipeline.py — Chat pipeline services (deterministic, ~0 added latency).

Consolidates the pre-LLM gating and orchestration layer:
  * CLASSIFIER    — ambiguity detection, intent classification, scope resolution.
  * CONVERSATION  — per-user history + carried context + follow-up rewrite.
  * CACHE         — in-memory LRU cache of answers, keyed by query + scope.
  * SOURCE ROUTER — pick the SQLite vs Azure SQL data source per question.
  * ORCHESTRATOR  — ``run_chat_pipeline`` façade over the LangGraph state machine.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
from collections import OrderedDict

from chatbot.core.config import LOG_DIR, get_logger
from chatbot.llm import load_prompt
from chatbot.sources.sqlite_source import (
    _resolve_period,
    _resolve_agent,
    _resolve_metrics,
    resolve_employee_ids,
    unresolved_named_agent,
    unavailable_period,
    agent_from_employee_id,
    agent_in_scope,
    available_periods,
    is_profile_query,
)
from chatbot.sources import azure_source as rep_sql
from chatbot.sources import azure_catalog
from chatbot.llm.dictionaries import match_rep_metrics
from chatbot.core.schemas import ChatRequest, ChatResponse

log = get_logger(__name__)

# Structured Q&A audit trail (query + answer + data source), one JSON line each.
_QA_AUDIT_FILE = LOG_DIR / "qa_audit.jsonl"
# ═══════════════════════════════════════════════════════════════════════════
# MENTIONS — inline ``@`` directives that override source / scope per message
# ═══════════════════════════════════════════════════════════════════════════
# ``@sqlite`` / ``@sqlite3``           → force the local SQLite source.
# ``@sql`` / ``@azure`` / ``@azuresql``→ force the Azure SQL source.
# ``@no-agent`` / ``@noagent``         → derive the reporting agents from the
#                                        query (or chat history) instead of the
#                                        caller's default reporting_agents.
# Order matters: ``@sqlite`` is tried before ``@sql`` so the longer token wins.
_MENTION_SQLITE_RE = re.compile(r"(?i)(?<![\w/])@sqlite3?\b")
_MENTION_AZURE_RE = re.compile(r"(?i)(?<![\w/])@(?:azure(?:[-_]?sql)?|sql)\b")
_MENTION_NOAGENT_RE = re.compile(r"(?i)(?<![\w/])@no[-_]?agents?\b")


def parse_mentions(query: str) -> tuple[str, str | None, bool]:
    """Extract ``@`` directives from *query*.

    Returns ``(cleaned_query, source_override, scope_override)`` where:
      * ``cleaned_query`` is *query* with every recognised directive removed and
        whitespace collapsed.
      * ``source_override`` is ``"sqlite"``, ``"azure"`` or ``None``.
      * ``scope_override`` is ``True`` when ``@no-agent`` was present.

    ``@sqlite`` takes precedence over ``@sql`` when both appear.
    """
    source_override: str | None = None
    if _MENTION_SQLITE_RE.search(query):
        source_override = "sqlite"
    elif _MENTION_AZURE_RE.search(query):
        source_override = "azure"

    scope_override = bool(_MENTION_NOAGENT_RE.search(query))

    cleaned = _MENTION_SQLITE_RE.sub(" ", query)
    cleaned = _MENTION_AZURE_RE.sub(" ", cleaned)
    cleaned = _MENTION_NOAGENT_RE.sub(" ", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()

    if source_override or scope_override:
        log.info("parse_mentions → source_override=%s scope_override=%s cleaned='%s'",
                 source_override, scope_override, cleaned)
    return cleaned or query.strip(), source_override, scope_override


def narrow_scope(
    scope: dict,
    query: str,
    history: list[dict[str, str]] | None,
    reporting_agents: list[int] | dict[str, int],
) -> dict:
    """Resolve the ``@no-agent`` directive into an explicit agent scope.

    The reporting agents are taken from the conversation instead of the
    caller's roster:

      * agent(s) named in *query* win; otherwise the most recent agent(s)
        referenced in *history* are used.
      * the resolved agent(s) are used **as-is, irrespective of the caller's
        reporting list** — ``@no-agent`` deliberately points at whoever was
        mentioned, not at the default roster.
      * when nothing resolvable is found, the original *scope* is returned
        unchanged (fall back to the caller's own reporting agents).
    """
    referenced = resolve_employee_ids(query)
    source = "query"
    if not referenced and history:
        recent = " ".join(
            m.get("content", "") for m in history[-6:] if m.get("role") == "user"
        )
        referenced = resolve_employee_ids(recent)
        source = "history"

    if not referenced:
        log.info("narrow_scope → no agent mentioned; keeping reporting-based roster")
        return scope

    employee_ids = [int(e) for e in referenced]
    if scope["role"] == "manager" and isinstance(reporting_agents, dict):
        coach_ids = sorted({reporting_agents[e] for e in referenced if e in reporting_agents})
    else:
        coach_ids = []

    log.info("narrow_scope → @no-agent points to %d agent(s) from %s (ignoring reporting list)",
             len(employee_ids), source)
    return {"role": scope["role"], "employee_ids": employee_ids, "coach_ids": coach_ids}


# ── Ambiguity detection ────────────────────────────────────────────────────
# Reference / follow-up words that depend on prior context to be understood.
# Demonstratives (this/that/these/those) are NOT treated as references when
# they introduce a time phrase ("this period", "that week") — those are
# self-contained, not context-dependent.
_REFERENCE_RE = re.compile(
    r"(?i)("
    r"\b(they|them|their|theirs|it|its|he|him|his|she|her|hers|again)\b"
    r"|\b(the\s+same|that\s+one|this\s+one)\b"
    r"|\b(those|these|that|this)\b(?!\s+(period|week|month|quarter|year|time|day|reporting)s?\b)"
    r")"
)

# Phrases that are inherently follow-ups (only meaningful with prior context).
_FOLLOWUP_RE = re.compile(
    r"(?i)^\s*(and|what\s+about|how\s+about|what\s+of|also|then|"
    r"compared\s+to|versus|vs\.?|same\s+for|now\s+show)\b"
)

# A concrete subject the query can stand on: a metric keyword, a name, or an id.
_HAS_SUBJECT_RE = re.compile(r"(?i)\b([A-Z][a-z]+,\s*[A-Z]|\d{6,9})\b")

# ── Query classification ───────────────────────────────────────────────────
# Greeting patterns
_GREETING_RE = re.compile(
    r"^\s*"
    r"(h(i|ello|ey|owdy|iya)|good\s*(morning|afternoon|evening|day)"
    r"|what'?s\s*up|yo\b|sup\b|greetings|namaste|thanks?|thank\s*you"
    r"|how\s*are\s*you|how'?s\s*it\s*going)"
    r"(\s+(there|everyone|all|folks|team|buddy|mate))?"
    r"[!?.,\s]*$",
    re.IGNORECASE,
)

# On-topic keyword patterns (performance analytics domain)
_TOPIC_KEYWORDS = re.compile(
    r"(?i)"
    r"(\bscore\b|\bmetric|\bkpi\b|\brate\b|\bcall\b|\bresolution\b"
    r"|\bescalat|\bbehavior|\bbehaviour|\bsave\b|\bsale\b|\bpitch"
    r"|\bmobile\s*protection|\bcustomer\s*experience"
    r"|\bperformance|\bcoaching|\brecommend|\bimprove|\bfocus"
    r"|\btrend|\bcompar|\brank|\btop\s+\d|\bbottom\s+\d|\bbest\b|\bworst\b"
    r"|\baverage|\btotal\b|\bcount\b|\bpercent"
    r"|\bagent|\bemployee|\bteam\b|\bmember|\broster"
    r"|\bweek\b|\bmonth\b|\bperiod\b|\bdate\b"
    r"|\bpriority|\battention|\brisk|\balert"
    r"|\bquality|\btarget|\bpenalt|\bsoft\s*skill"
    r"|\bhandle|\bhandling|\bhold\b|\btransfer"
    r"|\bnew\s*line|\bsave\s*attempt|\bprotection"
    r"|\binsight|\banalysis|\banalytics|\bsummary|\boverview)"
)

# Obvious off-topic patterns (checked BEFORE question-word fallback)
_OFF_TOPIC_RE = re.compile(
    r"(?i)"
    r"(joke|funny|weather|recipe|cook|movie|music|song|game|sport"
    r"|stock\s*market|crypto|bitcoin|politics|news|celebrity"
    r"|write\s*(a\s*)?(poem|story|essay|code|script)"
    r"|translate|define\s+the\s+word|meaning\s+of\s+life"
    r"|capital\s+of|president\s+of|who\s+invented"
    r"|play\s+a\s+game|tell\s+me\s+(a\s+)?(joke|story|riddle|fact))"
)

_DECLINE_MSG = load_prompt("decline")


def check_ambiguous(query: str, history: list[dict[str, str]]) -> str | None:
    """Decide whether *query* needs clarification before SQL generation.

    Returns a clarifying-question string when the query is ambiguous AND
    there is no prior context to resolve it; otherwise ``None``.
    """
    has_context = bool(history)
    is_followup = bool(_FOLLOWUP_RE.search(query))
    has_reference = bool(_REFERENCE_RE.search(query))

    # A named agent that could not be matched to the roster (even with typo
    # tolerance) must NOT silently fall back to the previously discussed agent.
    # Ask the user to confirm who they mean instead of answering about the
    # wrong person.
    unresolved = unresolved_named_agent(query)
    if unresolved is not None:
        typed, suggestions = unresolved
        log.info("check_ambiguous → named agent '%s' not on roster", typed)
        template = load_prompt("clarify_agent")
        if suggestions:
            hint = " Did you mean: " + ", ".join(suggestions) + "?"
        else:
            hint = ""
        return template.format(name=typed, suggestions=hint)

    # A user-named week that is NOT in the store must NOT be silently assumed.
    # Ask which available week they mean instead of returning empty results for
    # a non-existent period.
    missing_period = unavailable_period(query)
    if missing_period is not None:
        typed_period, available = missing_period
        log.info("check_ambiguous → week '%s' not in store", typed_period)
        template = load_prompt("clarify_period")
        weeks = ", ".join(available[:12]) if available else "(none loaded)"
        return template.format(period=typed_period, weeks=weeks)

    # A concrete subject the query can stand on its own with.
    has_metric = any(m.get("column") for m in _resolve_metrics(query))
    has_named_agent = _resolve_agent(query) is not None
    has_subject = (
        bool(_HAS_SUBJECT_RE.search(query))
        or bool(_TOPIC_KEYWORDS.search(query))
        or has_metric
        or has_named_agent
    )

    # Follow-up phrasing or pronoun reference WITHOUT prior context → ambiguous,
    # but only when the query has no self-contained subject. A sentence like
    # "what KPIs are trending downwards and what are their effects?" contains
    # the pronoun "their" yet stands on its own (subject = KPIs/trend), so it
    # should NOT be treated as a context-dependent follow-up.
    if (is_followup or has_reference) and not has_context and not has_subject:
        log.info("check_ambiguous → reference/follow-up without context")
        return load_prompt("clarify_ambiguous")

    # Very short, no recognizable subject, no metric, no context → ambiguous.
    word_count = len(query.split())
    if word_count <= 3 and not has_subject and not has_context:
        log.info("check_ambiguous → too short / no subject")
        return load_prompt("clarify_short")

    return None


def classify_query(text: str) -> str:
    """Classify *text* as ``greeting``, ``on_topic``, or ``off_topic``."""
    stripped = text.strip()
    if _GREETING_RE.match(stripped):
        return "greeting"
    if _OFF_TOPIC_RE.search(stripped):
        return "off_topic"
    if _TOPIC_KEYWORDS.search(stripped):
        return "on_topic"
    # Defer to the deterministic domain resolvers — they encode the full
    # analytics vocabulary (every metric, agent id/name, and period format),
    # so a hit here is a strong on-topic signal the keyword list may miss.
    if _resolve_metrics(stripped) or _resolve_agent(stripped) or _resolve_period(stripped):
        return "on_topic"
    # Short queries with question words — benefit of the doubt
    if len(stripped.split()) <= 6 and re.match(
        r"(?i)(who|what|how|show|list|give|tell|which)", stripped
    ):
        return "on_topic"
    return "off_topic"


def greeting_response(user_name: str, role: str) -> str:
    """Return a personalized greeting."""
    name = user_name.split(",")[0].strip() if user_name else ""
    display = name or "there"
    role_label = "Coach" if role == "coach" else "Manager"
    return load_prompt("greeting").format(display=display, role_label=role_label)



def resolve_scope(
    role: str,
    reporting_agents: list[int] | dict[str, int],
) -> dict:
    """Extract employee_ids and coach_ids from the reporting_agents payload.

    - **coach**: ``reporting_agents`` is ``list[int]`` of employee IDs.
      ``coach_ids`` is empty (scope filters by employee_id only).
    - **manager**: ``reporting_agents`` is ``dict[str, int]`` mapping
      employee ID (str key) -> coach ID.  Both lists are extracted.
    """
    if role == "coach":
        employee_ids = list(reporting_agents)  # type: ignore[arg-type]
        log.info("resolve_scope → role=coach, employee_ids=%s", employee_ids)
        return {
            "role": role,
            "employee_ids": employee_ids,
            "coach_ids": [],
        }

    # manager — reporting_agents is {employee_id_str: coach_id}
    agents_map: dict[str, int] = reporting_agents  # type: ignore[assignment]
    employee_ids = [int(eid) for eid in agents_map]
    coach_ids = list(set(agents_map.values()))
    log.info("resolve_scope → role=manager, employee_ids=%d, coach_ids=%s",
             len(employee_ids), coach_ids)
    return {
        "role": role,
        "employee_ids": employee_ids,
        "coach_ids": coach_ids,
    }


# Exposed so the decline message can be reused by the orchestrator.
DECLINE_MSG = _DECLINE_MSG


# ═══════════════════════════════════════════════════════════════════════════
# CONVERSATION — per-user history + carried context + follow-up rewrite
# ═══════════════════════════════════════════════════════════════════════════
# Short-term memory window: the last N *conversations* (user+assistant pairs)
# of the CURRENTLY RUNNING session only. When the frontend switches the active
# session, this window is cleared and re-seeded from the new session's blob
# (see scope_node), so the model never carries stale turns across sessions.
SHORT_TERM_CONVERSATIONS = max(1, int(os.getenv("CHAT_SHORT_TERM_CONVERSATIONS", "5")))
_HISTORY_MAX_MSGS = SHORT_TERM_CONVERSATIONS * 2  # 5 pairs ≈ 10 messages

# Short-term memory and carried context are keyed by ``(user_id, session_id)``
# — NOT by user_id alone — so every conversation is fully isolated. Two browser
# tabs / sessions for the same user never bleed context into each other, and a
# session switch automatically starts from a clean slate (the new key has no
# entry) instead of relying on an explicit clear.
_MemKey = tuple[int, str | None]
_chat_history: dict[_MemKey, list[dict[str, str]]] = {}

# Carried-forward query context per conversation (last resolved period / agent /
# metric), keyed by ``(user_id, session_id)``.
_user_context: dict[_MemKey, dict] = {}

# Short-term memory of the session each user is currently chatting in. Lets us
# detect when the frontend switches the active session (new vs. same) so we can
# reload context from blob storage instead of carrying stale in-memory history.
_SESSION_UNSET = object()
_active_session: dict[int, str | None] = {}


def _mem_key(user_id: int, session_id: str | None = None) -> _MemKey:
    """Composite ``(user_id, session_id)`` key for per-conversation state.

    When *session_id* is omitted, the user's currently-active session is used so
    debug/admin callers (which only know the user id) still address the live
    conversation.
    """
    if session_id is None:
        session_id = _active_session.get(user_id)
    return (user_id, session_id)

_TEAM_SCOPE_RE = re.compile(
    r"(?i)\b(team|everyone|every\s*agent|all\s+agents?|all\s+members?|roster|whole\s+team)\b"
)

_RANKING_RE = re.compile(
    r"(?i)\b(who|whom|which|highest|lowest|top|bottom|best|worst|rank(?:ed|ing)?|"
    r"most|least|leader(?:board)?|compare|comparison|versus|vs|above\s+average|"
    r"below\s+average|more\s+than|less\s+than|greater\s+than|fewer\s+than)\b"
)

# Continuation signal — a follow-up that clearly refers back to the agent we are
# already talking about (pronouns or "this/that/same agent"). When present with a
# carried single agent, we KEEP that agent even if the question also contains
# ranking / comparison words ("which areas should SHE improve", "how is HE
# trending"), so the reference resolves to the right person instead of the team.
_CONTINUATION_RE = re.compile(
    r"(?i)\b(he|him|his|she|her|hers|they|them|their|theirs|"
    r"this\s+(agent|rep|person|employee)|that\s+(agent|rep|person|employee)|"
    r"same\s+(agent|rep|person|employee))\b"
)

_METRIC_INTENT_RE = re.compile(
    r"(?i)\b("
    r"score|scores|rate|rates|attempt|attempts|actual|total|count|volume"
    r"|escalat\w*|resolution\w*|save\b|saves\b|sale\b|sales\b|pitch\w*"
    r"|call\s*(count|volume|total|handling)|behavior|behaviour"
    r"|empathy|clarity|confidence|listening|coaching|trend"
    r"|conversion|upgrade|customer\s*experience|protection|fwa"
    r"|compliance|quality|kpi|metric|performance|overall"
    r"|new\s*line|new\s*prospect|mobile|we\s*got\s*you"
    r")\b"
)


def _same_agent(a: dict | None, b: dict | None) -> bool:
    """True when two agent-context dicts refer to the same person."""
    if not a or not b:
        return False
    if a.get("employee_id") and b.get("employee_id"):
        return str(a["employee_id"]) == str(b["employee_id"])
    if a.get("last_name") and b.get("last_name"):
        return str(a["last_name"]).lower() == str(b["last_name"]).lower()
    return False


def resolve_context(
    user_id: int,
    query: str,
    session_id: str | None = None,
    focus_agent_id: int | str | None = None,
    focus_scope: str = "agent",
    req_period: str | None = None,
) -> tuple[str | None, dict | None, str | None]:
    """Resolve the effective period/agent/metric for *query*, carrying context.

    Agent-resolution precedence (highest first):
      1. An agent **named in the query** — always wins.
      2. ``focus_scope == "team"`` or a team/ranking phrase → no single agent.
      3. The UI's **focus agent** (*focus_agent_id*) — the agent the user is
         currently viewing. This is authoritative for follow-ups and, crucially,
         **switching the selected agent switches context immediately** (it
         overrides any stale carried agent), fixing same-session agent switches.
      4. A carried agent kept alive by a continuation pronoun ("how is *she*…").
      5. The previously carried agent (pure follow-up).
    """
    ctx = _user_context.setdefault(
        _mem_key(user_id, session_id), {"period": None, "agent": None, "metric": None}
    )

    # Period: explicit in query → UI-selected week → carried.
    period = _resolve_period(query) or req_period or ctx.get("period")

    explicit_agent = _resolve_agent(query)
    focus_agent = agent_from_employee_id(focus_agent_id) if focus_agent_id else None
    carried_agent = ctx.get("agent")

    agent: dict | None
    if explicit_agent:
        agent = explicit_agent  # current query names an agent — always wins
    elif focus_scope == "team" or _TEAM_SCOPE_RE.search(query):
        agent = None  # explicit team focus / team-wide question
    elif focus_agent:
        # The UI has an agent in focus. Keep the carried agent only when it is
        # the SAME person and the user is clearly continuing about them;
        # otherwise the focus agent is authoritative (handles the user selecting
        # a different agent mid-session).
        if carried_agent and _same_agent(carried_agent, focus_agent) and _CONTINUATION_RE.search(query):
            agent = carried_agent
        else:
            agent = focus_agent
    elif carried_agent and _CONTINUATION_RE.search(query):
        # Pronoun / "this agent" follow-up about the person we're discussing —
        # keep them even if ranking/comparison words are also present.
        agent = carried_agent
    elif _RANKING_RE.search(query):
        agent = None  # ranking / comparison query — drop carried single-agent filter
    else:
        agent = carried_agent  # pure follow-up — carry the current agent forward

    metrics = _resolve_metrics(query)
    if metrics:
        metric = metrics[0]["phrase"]
    elif _METRIC_INTENT_RE.search(query):
        metric = None  # user mentions a metric concept — don't pollute with old one
    else:
        metric = ctx.get("metric")  # pure follow-up — carry forward

    ctx["period"] = period
    ctx["agent"] = agent
    ctx["metric"] = metric
    log.info("resolve_context → period=%s, agent=%s, metric=%s (focus=%s scope=%s)",
             period, agent, metric, focus_agent_id, focus_scope)
    return period, agent, metric


_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def entity_clarification(entities) -> str | None:
    """Deterministic clarification gate for LLM-extracted entities.

    Returns a user-facing clarifying question when the extracted reference can't
    be honored — a named agent that isn't on the roster, or an explicit week we
    haven't loaded — otherwise ``None``. The model's own ``clarification`` (e.g.
    a genuinely ambiguous request) takes precedence. Authorization/availability
    stay deterministic here; the LLM never decides them.
    """
    if entities.clarification:
        return entities.clarification
    if entities.agent_name and not entities.employee_id:
        if not resolve_employee_ids(entities.agent_name):
            unres = unresolved_named_agent(f"agent {entities.agent_name}")
            suggestions = unres[1] if unres else []
            hint = " Did you mean: " + ", ".join(suggestions) + "?" if suggestions else ""
            return load_prompt("clarify_agent").format(
                name=entities.agent_name, suggestions=hint
            )
    if entities.period and _ISO_DATE_RE.match(entities.period):
        periods = available_periods()
        if periods and entities.period not in periods:
            return load_prompt("clarify_period").format(
                period=entities.period, weeks=", ".join(periods[:12]) or "(none loaded)"
            )
    return None


# Intents that legitimately span or auto-pick a period — never bounce these for
# a week (trends show a series, coaching/career/profile/ranking auto-pick latest).
_MULTI_PERIOD_INTENT_RE = re.compile(
    r"(?i)\b(trend|trending|over\s+time|history|historical|"
    r"coach\w*|career|develop\w*|growth|promot\w*|train\w*|"
    r"improv\w*|risk|red\s*flag|profile|overview|summar\w*)\b"
)


def _remember_pending_metric(
    user_id: int,
    session_id: str | None,
    query: str,
    entities,
    focus_agent_id: int | str | None,
) -> None:
    """Persist the metric/agent the user is asking about so a following bare-week
    reply (e.g. "2026-04-17", "last week") resolves against it instead of
    starting over."""
    ctx = _user_context.setdefault(
        _mem_key(user_id, session_id),
        {"period": None, "agent": None, "metric": None},
    )
    agent = _resolve_agent(query)
    if not agent and entities is not None and entities.agent_name:
        ids = resolve_employee_ids(entities.agent_name)
        if len(ids) == 1:
            agent = agent_from_employee_id(ids[0])
    if not agent and entities is not None and entities.employee_id:
        agent = agent_from_employee_id(entities.employee_id)
    if not agent and focus_agent_id:
        agent = agent_from_employee_id(focus_agent_id)
    if agent:
        ctx["agent"] = agent
    metrics = _resolve_metrics(query)
    if metrics:
        ctx["metric"] = metrics[0]["phrase"]


def needs_period_clarification(
    query: str,
    entities,
    *,
    user_id: int,
    session_id: str | None,
    req_period: str | None = None,
    focus_agent_id: int | str | None = None,
) -> str | None:
    """Ask "for which week?" for a bare single-metric lookup with no period.

    Only fires for a concrete metric-value question that omits a period; profile,
    coaching, career, trend, ranking and comparison queries are left untouched
    (they auto-pick or span periods by design). Remembers the pending
    metric/agent so the next bare-week turn completes. Returns the clarifying
    question or ``None``.
    """
    if entities is not None and entities.intent != "analytics":
        return None
    # A concrete metric column must be the subject of the question.
    if not any(m.get("column") for m in _resolve_metrics(query)):
        return None
    # Leave multi-period / narrative / ranking intents to their own handling.
    if (
        is_profile_query(query)
        or _RANKING_RE.search(query)
        or _MULTI_PERIOD_INTENT_RE.search(query)
    ):
        return None
    # A period supplied anywhere (query, LLM entity, UI week, carried) → answer.
    if _resolve_period(query) is not None:
        return None
    if entities is not None and entities.period:
        return None
    if req_period:
        return None
    ctx = _user_context.get(_mem_key(user_id, session_id))
    if ctx and ctx.get("period"):
        return None
    _remember_pending_metric(user_id, session_id, query, entities, focus_agent_id)
    weeks = ", ".join(available_periods()[:8]) or "(none loaded)"
    return load_prompt("clarify_week").format(weeks=weeks)


def merge_entities(
    user_id: int,
    entities,
    *,
    session_id: str | None = None,
    focus_agent_id: int | str | None = None,
    focus_scope: str = "agent",
    req_period: str | None = None,
) -> tuple[str | None, dict | None, str | None, str | None]:
    """LLM-first context resolver — the low-latency replacement for the regex path.

    Takes the entities extracted by a single LLM call
    (:class:`chatbot.llm.extract.QueryEntities`) and folds them into the
    per-conversation short-term memory, returning
    ``(period, agent, metric, clarification)``.

    Authorization is never delegated to the model: the extracted agent name/id
    is *validated deterministically* against the loaded roster (via
    :func:`resolve_employee_ids` / :func:`unresolved_named_agent`) and the
    period against the loaded weeks, so RBAC and availability stay exact. The
    resolved entity set (which agent/period/metric the conversation currently
    "belongs to") is persisted under ``(user_id, session_id)`` and carried
    forward on follow-ups.
    """
    ctx = _user_context.setdefault(
        _mem_key(user_id, session_id),
        {"period": None, "agent": None, "metric": None, "entities": None},
    )
    clarify = entity_clarification(entities)

    # ── Agent: validate the LLM's reference against the authorised roster ────
    explicit_agent: dict | None = None
    if entities.employee_id:
        explicit_agent = agent_from_employee_id(entities.employee_id)
    elif entities.agent_name:
        ids = resolve_employee_ids(entities.agent_name)
        if len(ids) == 1:
            explicit_agent = agent_from_employee_id(ids[0])
        elif ids:
            explicit_agent = {"last_name": entities.agent_name}
        # else: unresolved → clarify already set by entity_clarification()

    focus_agent = agent_from_employee_id(focus_agent_id) if focus_agent_id else None
    carried_agent = ctx.get("agent")

    agent: dict | None
    if explicit_agent:
        agent = explicit_agent                      # current query names an agent
    elif entities.scope == "team" or focus_scope == "team":
        agent = None                                # team-wide / ranking / comparison
    elif focus_agent:
        # UI has an agent in focus. Keep the carried agent only when it is the
        # SAME person and this is a continuation; otherwise focus is authoritative.
        if carried_agent and _same_agent(carried_agent, focus_agent) and entities.is_followup:
            agent = carried_agent
        else:
            agent = focus_agent
    else:
        agent = carried_agent                       # keep the agent the chat belongs to

    # ── Period: explicit (LLM-normalized) → UI week → carried ────────────────
    period = entities.period or req_period or ctx.get("period")

    # ── Metric: explicit → carried on follow-up → drop ───────────────────────
    if entities.metric:
        metric = entities.metric
    elif entities.is_followup:
        metric = ctx.get("metric")
    else:
        metric = None

    ctx["period"] = period
    ctx["agent"] = agent
    ctx["metric"] = metric
    ctx["entities"] = entities.to_memory()
    log.info("merge_entities → period=%s agent=%s metric=%s (scope=%s followup=%s focus=%s)",
             period, agent, metric, entities.scope, entities.is_followup, focus_agent_id)
    return period, agent, metric, clarify


def rewrite_query(
    query: str,
    period: str | None,
    agent: dict | None,
    metric: str | None,
) -> str:
    """Rewrite *query* into a self-contained question using carried context."""
    additions: list[str] = []

    if metric and not _resolve_metrics(query) and not _METRIC_INTENT_RE.search(query):
        additions.append(metric)

    if agent and not _resolve_agent(query):
        if agent.get("last_name") and agent.get("employee_id"):
            additions.append(f"for agent {agent['last_name']} {agent['employee_id']}")
        elif agent.get("employee_id"):
            additions.append(f"for agent {agent['employee_id']}")
        elif agent.get("last_name"):
            additions.append(f"for agent {agent['last_name']}")

    if period and not _resolve_period(query):
        additions.append(f"for period {period}")

    if not additions:
        return query.strip()

    rewritten = f"{query.strip()} ({'; '.join(additions)})"
    log.info("rewrite_query → '%s'", rewritten)
    return rewritten


def get_history(user_id: int, session_id: str | None = None) -> list[dict[str, str]]:
    """Return the chronological message history for a conversation (oldest→newest)."""
    return _chat_history.get(_mem_key(user_id, session_id), [])


def add_to_history(
    user_id: int, role: str, content: str, session_id: str | None = None
) -> None:
    """Append a message and trim to the most recent ``_HISTORY_MAX_MSGS``."""
    hist = _chat_history.setdefault(_mem_key(user_id, session_id), [])
    hist.append({"role": role, "content": content})
    if len(hist) > _HISTORY_MAX_MSGS:
        del hist[:-_HISTORY_MAX_MSGS]


def clear_history(user_id: int, session_id: str | None = None) -> None:
    """Drop stored history and carried context for a conversation.

    * With *session_id* → clears only that conversation.
    * Without *session_id* → clears **every** conversation belonging to the user
      and forgets their active session (used by the admin ``DELETE`` endpoint).
    """
    if session_id is not None:
        _chat_history.pop((user_id, session_id), None)
        _user_context.pop((user_id, session_id), None)
        return
    for key in [k for k in _chat_history if k[0] == user_id]:
        _chat_history.pop(key, None)
    for key in [k for k in _user_context if k[0] == user_id]:
        _user_context.pop(key, None)
    _active_session.pop(user_id, None)


def is_same_session(user_id: int, session_id: str | None) -> bool:
    """True when *session_id* matches the user's last-seen active session.

    A user not seen before (cold server / first hit) is reported as *not* the
    same session, so the caller reloads context from blob storage.
    """
    return _active_session.get(user_id, _SESSION_UNSET) == session_id


def set_active_session(user_id: int, session_id: str | None) -> None:
    """Record *session_id* as the user's current session (short-term memory)."""
    _active_session[user_id] = session_id


# ═══════════════════════════════════════════════════════════════════════════
# SCOPE VALIDATION — explicit governance response for out-of-roster agents
# ═══════════════════════════════════════════════════════════════════════════
def out_of_scope_message(agent: dict | None) -> str:
    """Human-readable refusal when the resolved agent is outside the roster."""
    who = ""
    if agent:
        if agent.get("last_name") and agent.get("employee_id"):
            who = f" **{agent['last_name']}** (ID {agent['employee_id']})"
        elif agent.get("last_name"):
            who = f" **{agent['last_name']}**"
        elif agent.get("employee_id"):
            who = f" agent ID **{agent['employee_id']}**"
    return (
        f"I can only report on agents in your team. The agent you referenced"
        f"{who} isn't in your reporting scope, so I can't share their "
        f"performance details. Please pick an agent from your roster and try "
        f"again."
    )


# ═══════════════════════════════════════════════════════════════════════════
# Q&A AUDIT LOG — one JSON line per answered turn (query + answer + source)
# ═══════════════════════════════════════════════════════════════════════════
_TABLE_RE = re.compile(r"\b(?:FROM|JOIN)\s+([A-Za-z_][\w.]*)", re.IGNORECASE)
_QA_KEYWORDS = frozenset({
    "select", "distinct", "as", "on", "and", "or", "where", "group",
    "order", "by", "having", "limit", "top", "inner", "left", "right",
    "outer", "join", "max", "min", "avg", "sum", "count",
})


def _tables_from_sql(sql: str | None) -> list[str]:
    """Best-effort extraction of the base table names a query reads from."""
    if not sql:
        return []
    seen: list[str] = []
    for name in _TABLE_RE.findall(sql):
        low = name.lower()
        if low in _QA_KEYWORDS or name.startswith("("):
            continue
        if name not in seen:
            seen.append(name)
    return seen


def record_qa(
    *,
    user_id: int | None,
    session_id: str | None,
    role: str | None,
    query: str,
    rewritten: str | None,
    source: str | None,
    sql: str | None,
    agent: dict | None,
    period: str | None,
    row_count: int,
    answer: str,
    elapsed_ms: float | int | None = None,
    trace_id: str | None = None,
) -> None:
    """Append a structured Q&A record (query, answer, data source) to the audit
    log so every response can be traced back to the exact source and rows.

    Writes a JSON line to ``chatbot/logs/qa_audit.jsonl`` and emits a compact
    INFO summary on the normal log. Failures here never break a response.
    """
    tables = _tables_from_sql(sql)
    src = source
    if src is None and sql:
        # Infer a coarse source label when the node didn't set one.
        src = "azure_sql" if "rep_pivoted" in sql.lower() else "sqlite"
    agent_label = None
    if agent:
        agent_label = agent.get("last_name") or agent.get("employee_id")

    record = {
        "ts": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "trace_id": trace_id,
        "user_id": user_id,
        "session_id": session_id,
        "role": role,
        "query": query,
        "rewritten": rewritten if rewritten and rewritten != query else None,
        "source": src,
        "tables": tables,
        "agent": agent_label,
        "period": period,
        "row_count": row_count,
        "sql": sql,
        "answer": answer,
        "elapsed_ms": round(elapsed_ms) if elapsed_ms is not None else None,
    }

    try:
        _QA_AUDIT_FILE.parent.mkdir(parents=True, exist_ok=True)
        with _QA_AUDIT_FILE.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    except Exception as e:  # never let audit logging break a reply
        log.warning("record_qa → failed to write audit line: %s", e)

    log.info(
        "QA │ user=%s session=%s role=%s │ source=%s tables=%s agent=%s "
        "period=%s rows=%d │ query=%r │ answer=%r",
        user_id, session_id, role, src, tables or "-", agent_label or "-",
        period or "-", row_count, query,
        (answer[:160] + "…") if answer and len(answer) > 160 else answer,
    )


# ═══════════════════════════════════════════════════════════════════════════
# CACHE — in-memory LRU cache of answers, keyed by query + scope
# ═══════════════════════════════════════════════════════════════════════════
_CACHE_MAX = 256
_cache: OrderedDict[str, tuple[list[dict], str]] = OrderedDict()


def cache_key(
    query: str,
    user_id: int,
    role: str,
    employee_ids: list[int],
    coach_ids: list[int],
    focus_agent_id: int | str | None = None,
    period: str | None = None,
) -> str:
    """Build a deterministic cache key from the raw query and user scope.

    The UI's *focus_agent_id* and *period* are folded into the key so that the
    same follow-up question ("how is their coaching?") asked while a different
    agent / week is selected never returns a stale cached answer.
    """
    emps = ",".join(str(a) for a in sorted(employee_ids))
    coaches = ",".join(str(c) for c in sorted(coach_ids))
    fa = str(focus_agent_id or "")
    pd = str(period or "")
    raw = f"{query}:{user_id}:{role}:{emps}:{coaches}:{fa}:{pd}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def cache_get(key: str) -> tuple[list[dict], str] | None:
    """Return the cached ``(data, response)`` for *key*, or ``None`` on miss."""
    if key in _cache:
        _cache.move_to_end(key)
        log.debug("cache_get → HIT key=%s", key)
        return _cache[key]
    log.debug("cache_get → MISS key=%s", key)
    return None


def cache_put(key: str, data: list[dict], response: str) -> None:
    """Store ``(data, response)`` under *key*, evicting the LRU entry if full."""
    _cache[key] = (data, response)
    if len(_cache) > _CACHE_MAX:
        _cache.popitem(last=False)


# ═══════════════════════════════════════════════════════════════════════════
# SOURCE ROUTER — pick the SQLite vs Azure SQL data source per question
# ═══════════════════════════════════════════════════════════════════════════
def _sqlite_specificity(question: str) -> int:
    """Length of the longest concrete (column-bearing) JSON-dictionary match."""
    matches = _resolve_metrics(question)
    concrete = [m for m in matches if m.get("column")]
    if not concrete:
        if matches:
            return max(len(m["phrase"]) for m in matches)
        return 0
    return max(len(m["phrase"]) for m in concrete)


def route_source(question: str, source_override: str | None = None) -> tuple[str, list[dict]]:
    """Return ``(source, rep_matches)`` for *question*.

    ``source`` is one of ``"sqlite"``, ``"azure_sql"`` or ``"both"``.

    A per-message ``@`` directive (*source_override* = ``"sqlite"`` or
    ``"azure"``) takes precedence over everything else. Otherwise the
    ``SOURCE_MODE`` config flag (env ``SOURCE_MODE``) overrides routing:
      * ``"sqlite"`` → always local SQLite.
      * ``"azure"``  → always Azure SQL (downgraded to SQLite if Azure is not
        configured, so the agent always has a working source).
      * ``"both"``   → fetch from both sources and merge (downgraded to SQLite
        when Azure is not configured).
      * ``"auto"``   → score-based routing (the default behaviour below).
    """
    from chatbot.core.config import SOURCE_MODE

    rep_matches = match_rep_metrics(question)

    # ── Per-message @-directive (highest precedence) ────────────────────────
    if source_override == "sqlite":
        log.info("route_source → sqlite (@sqlite directive)")
        return "sqlite", rep_matches
    if source_override == "azure":
        if rep_sql.is_configured():
            log.info("route_source → azure_sql (@sql directive)")
            return "azure_sql", rep_matches
        log.info("route_source → sqlite (@sql directive but azure not configured)")
        return "sqlite", rep_matches

    # ── Forced modes (easy flag) ────────────────────────────────────────────
    if SOURCE_MODE == "sqlite":
        log.info("route_source → sqlite (SOURCE_MODE=sqlite)")
        return "sqlite", rep_matches

    if SOURCE_MODE in ("azure", "both"):
        if not rep_sql.is_configured():
            log.info("route_source → sqlite (SOURCE_MODE=%s but azure not configured)", SOURCE_MODE)
            return "sqlite", rep_matches
        forced = "azure_sql" if SOURCE_MODE == "azure" else "both"
        log.info("route_source → %s (SOURCE_MODE=%s)", forced, SOURCE_MODE)
        return forced, rep_matches

    # ── auto: score-based routing ───────────────────────────────────────────
    rep_score = rep_matches[0]["score"] if rep_matches else 0
    sqlite_score = _sqlite_specificity(question)

    # How well any registered Azure table (wide or pivoted) fits the question.
    azure_configured = rep_sql.is_configured()
    azure_score = (
        azure_catalog.best_table_score(question, rep_matches) if azure_configured else 0.0
    )

    if not rep_matches:
        # No pivoted-metric match, but a wide Azure table may still own this
        # question (e.g. handle time / transfers on vzw.solutions).
        if azure_configured and azure_score >= 4.0 and azure_score > sqlite_score:
            log.info("route_source → azure_sql (wide table=%.1f > sqlite=%d)",
                     azure_score, sqlite_score)
            return "azure_sql", rep_matches
        log.info("route_source → sqlite (no rep match; azure=%.1f sqlite=%d)",
                 azure_score, sqlite_score)
        return "sqlite", rep_matches

    if not azure_configured:
        log.info("route_source → sqlite (azure not configured; rep_score=%d)", rep_score)
        return "sqlite", rep_matches

    if rep_score > sqlite_score:
        log.info("route_source → azure_sql (rep=%d > sqlite=%d)", rep_score, sqlite_score)
        return "azure_sql", rep_matches

    log.info("route_source → sqlite (rep=%d <= sqlite=%d)", rep_score, sqlite_score)
    return "sqlite", rep_matches


# ═══════════════════════════════════════════════════════════════════════════
# ORCHESTRATOR — stable façade over the LangGraph state machine
# ═══════════════════════════════════════════════════════════════════════════
async def run_chat_pipeline(req: ChatRequest) -> ChatResponse:
    """Execute the full NL → SQL → NL pipeline for a single request.

    Delegates to the compiled LangGraph orchestrator.  The import is deferred
    to avoid a circular import (``chatbot.orchestration.graph`` imports this module).

    Raises:
        HTTPException(422): Generated SQL failed safety validation.
        HTTPException(500): Any unhandled server error.
    """
    from chatbot.orchestration.graph import run_chat_graph
    return await run_chat_graph(req)
