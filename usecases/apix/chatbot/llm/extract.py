"""
chatbot/llm/extract.py — LLM-first query understanding (single low-latency call).

Replaces the brittle regex-based intent/entity resolution with ONE fast
GPT-5-nano call per request that returns a compact JSON envelope describing the
latest user message: intent, scope, the referenced agent (name / employee id),
period, metric, whether it is a follow-up, and an optional clarification.

Latency is kept low by:
  * a zero-LLM greeting fast-path (:func:`quick_greeting`) for trivial social
    messages, so "hi"/"thanks" never pay a round-trip;
  * ``reasoning_effort="minimal"`` and a tight ``max_completion_tokens`` budget;
  * a single call whose result is stashed on the graph state and reused by every
    downstream node (classify → ambiguity → rewrite), so no node re-extracts.

The deterministic resolvers in :mod:`chatbot.sources.sqlite_source` remain as a
safety net: this module only *understands* the message; the caller still maps
the extracted agent/period onto the authorised roster and the loaded periods
(RBAC and availability are never delegated to the model).
"""

from __future__ import annotations

import datetime as _dt
import json
import re
from dataclasses import dataclass, field
from typing import Any

from chatbot.core.config import get_logger
from chatbot.llm.client import generate_completion, load_prompt

log = get_logger(__name__)

# Tight budget — the envelope is tiny; this keeps the call fast and cheap.
_EXTRACT_MAX_TOKENS = 320

# Trivial, self-contained greetings/pleasantries that need no model call.
_QUICK_GREETING_RE = re.compile(
    r"^\s*"
    r"(hi+|hey+|hello+|yo|sup|howdy|hiya|greetings|namaste|"
    r"good\s*(morning|afternoon|evening|day)|"
    r"thank\s*(you|s)|thanks|thx|ty|"
    r"how\s*are\s*you|how'?s\s*it\s*going|what'?s\s*up)"
    r"(\s+(there|everyone|all|folks|team|buddy|mate))?"
    r"[!?.,\s]*$",
    re.IGNORECASE,
)


def quick_greeting(text: str) -> bool:
    """True for a trivial greeting/pleasantry that needs no LLM round-trip."""
    return bool(_QUICK_GREETING_RE.match(text or ""))


@dataclass
class QueryEntities:
    """Structured understanding of one user message (the short-term entity set)."""

    intent: str = "analytics"           # greeting | analytics | off_topic
    scope: str = "agent"                # agent | team
    agent_name: str | None = None       # surname / full name as typed
    employee_id: str | None = None      # 6-9 digit id if given
    period: str | None = None           # YYYY-MM-DD if a period was named
    metric: str | None = None           # metric / topic phrase
    is_followup: bool = False
    clarification: str | None = None     # non-null → ask instead of answering
    raw: dict[str, Any] = field(default_factory=dict)

    def to_memory(self) -> dict[str, Any]:
        """Compact dict persisted in short-term memory / traces."""
        return {
            "intent": self.intent,
            "scope": self.scope,
            "agent_name": self.agent_name,
            "employee_id": self.employee_id,
            "period": self.period,
            "metric": self.metric,
            "is_followup": self.is_followup,
        }


def _coerce(value: Any) -> str | None:
    """Normalize a model field to a clean string or ``None``."""
    if value is None:
        return None
    s = str(value).strip()
    if not s or s.lower() in {"null", "none", "n/a", "na", ""}:
        return None
    return s


def _parse_envelope(text: str) -> dict[str, Any]:
    """Best-effort parse of the model's JSON envelope (tolerant of stray text)."""
    if not text:
        return {}
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Salvage the first {...} block if the model wrapped it in prose/fences.
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            log.warning("extract → could not parse salvaged JSON block")
    return {}


def _build_messages(
    query: str,
    history: list[dict[str, str]] | None,
    periods: list[str] | None,
) -> list[dict[str, str]]:
    system = load_prompt("entity_extract")
    today = _dt.date.today().isoformat()
    hint_lines = [f"TODAY: {today}"]
    if periods:
        hint_lines.append("AVAILABLE PERIODS (newest first): " + ", ".join(periods[:16]))
    else:
        hint_lines.append("AVAILABLE PERIODS: (none loaded)")
    system = system + "\n\n" + "\n".join(hint_lines)

    messages: list[dict[str, str]] = [{"role": "system", "content": system}]
    # A short slice of prior turns lets the model resolve pronouns / follow-ups.
    for m in (history or [])[-6:]:
        role = m.get("role")
        content = (m.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content[:800]})
    messages.append({"role": "user", "content": query})
    return messages


async def extract_entities(
    query: str,
    history: list[dict[str, str]] | None = None,
    periods: list[str] | None = None,
) -> QueryEntities:
    """Understand *query* in one fast LLM call → :class:`QueryEntities`.

    On any failure the envelope falls back to a permissive ``analytics`` result
    so the deterministic downstream layer still runs (never harder-fails than
    the old regex path).
    """
    messages = _build_messages(query, history, periods)
    try:
        raw_text = await generate_completion(
            messages,
            max_tokens=_EXTRACT_MAX_TOKENS,
            reasoning_effort="minimal",
        )
    except Exception as exc:  # noqa: BLE001 — never break the request on extract
        log.warning("extract_entities → LLM call failed (%s); defaulting to analytics", exc)
        return QueryEntities(intent="analytics")

    data = _parse_envelope(raw_text)
    if not data:
        log.warning("extract_entities → empty/invalid envelope; defaulting to analytics")
        return QueryEntities(intent="analytics")

    intent = (_coerce(data.get("intent")) or "analytics").lower()
    if intent not in {"greeting", "analytics", "off_topic"}:
        intent = "analytics"
    scope = (_coerce(data.get("scope")) or "agent").lower()
    if scope not in {"agent", "team"}:
        scope = "agent"

    ent = QueryEntities(
        intent=intent,
        scope=scope,
        agent_name=_coerce(data.get("agent_name")),
        employee_id=_coerce(data.get("employee_id")),
        period=_coerce(data.get("period")),
        metric=_coerce(data.get("metric")),
        is_followup=bool(data.get("is_followup")),
        clarification=_coerce(data.get("clarification")),
        raw=data,
    )
    log.info(
        "extract_entities → intent=%s scope=%s agent=%s id=%s period=%s metric=%s followup=%s",
        ent.intent, ent.scope, ent.agent_name, ent.employee_id,
        ent.period, ent.metric, ent.is_followup,
    )
    return ent
