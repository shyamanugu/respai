"""
chatbot/llm.py — Azure OpenAI client, prompt loading, and NL response generation.

One cohesive module for everything LLM-related:
  1. CLIENT    — async GPT-5 nano chat completions (with retry + warmup).
  2. PROMPTS   — load externalized prompt templates from ``prompts/*.txt``.
  3. NARRATION — turn SQL result rows into a professional NL answer
                 (deterministic for simple results, LLM for complex ones).

GPT-5.4 nano specifics: uses ``max_completion_tokens`` (not ``max_tokens``),
temperature is fixed at 1, and output may arrive in ``reasoning_content``.
It is called through the Azure OpenAI v1 API (no api-version) using the
standard OpenAI client pointed at the resource's ``/openai/v1/`` base URL.
"""

from __future__ import annotations

import asyncio
import json
import random
import re
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any

from openai import AsyncOpenAI, APIError, APIConnectionError, RateLimitError, APIStatusError

from chatbot.core.config import (
    get_logger,
    LLM_API_KEY as API_KEY,
    LLM_BASE_URL as BASE_URL,
    LLM_ENDPOINT as ENDPOINT,
    LLM_DEPLOYMENT as DEPLOYMENT,
)

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
# API_KEY / BASE_URL / ENDPOINT / DEPLOYMENT come from chatbot.core.config,
# which loads them from the environment (.env files are loaded there).

MAX_RETRIES: int = 3
RETRY_DELAY: float = 1.0

# Errors that are safe to retry
_RETRYABLE = (APIConnectionError, RateLimitError)


def _build_client() -> AsyncOpenAI:
    log.debug("_build_client → base_url=%s, deployment=%s", BASE_URL, DEPLOYMENT)
    if not API_KEY:
        log.error("_build_client → REASONING_MODEL_APIKEY not set")
        raise EnvironmentError(
            "REASONING_MODEL_APIKEY is not set. "
            "Provide it via application/.env, ai_pipeline/.env, or environment variables."
        )
    if not BASE_URL:
        log.error("_build_client → REASONING_MODEL_ENDPOINT / BASE_URL not set")
        raise EnvironmentError(
            "REASONING_MODEL_ENDPOINT is not set. "
            "Provide it via chatbot/.env or environment variables."
        )
    # GPT-5.4 nano uses the Azure OpenAI v1 API — the standard OpenAI client
    # pointed at ``<endpoint>/openai/v1/`` with the resource key.  No api-version.
    client = AsyncOpenAI(
        base_url=BASE_URL,
        api_key=API_KEY,
    )
    log.info("_build_client → client built successfully")
    return client


_client: AsyncOpenAI | None = None


def get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        log.debug("get_client → first call, creating client")
        _client = _build_client()
    else:
        log.debug("get_client → reusing cached client")
    return _client


async def warmup() -> None:
    """Open the HTTP connection pool and prime the model with a tiny request.

    Run as a background task at startup so the first real user request does
    not pay the cold-connection + cold-model penalty.  Failures are swallowed
    (warmup is best-effort).
    """
    try:
        client = get_client()
        await client.chat.completions.create(
            model=DEPLOYMENT,
            messages=[{"role": "user", "content": "ping"}],
            max_completion_tokens=16,
            reasoning_effort="minimal",
        )
        log.info("warmup → LLM client warmed up")
    except Exception as exc:  # best-effort only
        log.warning("warmup → failed (non-fatal): %s", exc)


# ---------------------------------------------------------------------------
# Core completion function — GPT-5 nano compatible
# ---------------------------------------------------------------------------

async def _generate_completion_impl(
    messages: list[dict[str, str]],
    *,
    max_tokens: int = 16384,
    deployment: str | None = None,
    reasoning_effort: str | None = "minimal",
) -> tuple[str, tuple[int, int]]:
    """Send a chat-completion request to GPT-5 nano and return the assistant text.

    GPT-5 nano only supports temperature=1 (the default), so the
    temperature parameter is not exposed.
    Uses ``max_completion_tokens`` (GPT-5 requirement) instead of the
    legacy ``max_tokens`` parameter.

    ``reasoning_effort`` controls how many internal reasoning tokens the model
    spends.  ``"minimal"`` is ~4-7x faster than the default for structured
    tasks like SQL generation and short narration, so it is the default here.

    Retries on transient errors (connection / rate-limit) with exponential
    back-off + jitter.  Content-filter and bad-request errors are raised
    immediately.
    """
    client = get_client()
    model = deployment or DEPLOYMENT
    log.info("generate_completion → model=%s, max_completion_tokens=%d, effort=%s, messages=%d",
            model, max_tokens, reasoning_effort, len(messages))
    log.debug("generate_completion → system_prompt_len=%d",
             len(messages[0].get("content", "")) if messages else 0)

    create_kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_completion_tokens": max_tokens,
    }
    if reasoning_effort:
        create_kwargs["reasoning_effort"] = reasoning_effort

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            log.debug("generate_completion → attempt %d/%d", attempt, MAX_RETRIES)

            resp = await client.chat.completions.create(**create_kwargs)

            # GPT-5 nano returns content in choices[0].message.content
            choice = resp.choices[0]
            content = choice.message.content or ""

            # GPT-5 nano may put output in reasoning_content instead
            if not content and hasattr(choice.message, "reasoning_content"):
                content = choice.message.reasoning_content or ""
                if content:
                    log.info("generate_completion → used reasoning_content (content was empty)")

            # Log finish reason (stop, length, content_filter, etc.)
            log.debug("generate_completion → finish_reason=%s", choice.finish_reason)
            if choice.finish_reason == "content_filter":
                log.warning("generate_completion → response was truncated by content filter")

            result = content.strip()

            # Retry on empty response (transient model issue)
            if not result and attempt < MAX_RETRIES:
                log.warning("generate_completion → empty response (attempt %d/%d), retrying",
                           attempt, MAX_RETRIES)
                wait = RETRY_DELAY * (1 + random.random()) * attempt
                await asyncio.sleep(wait)
                continue

            log.info("generate_completion → success, response_len=%d chars", len(result))

            # Log token usage
            usage = (0, 0)
            if resp.usage:
                usage = (resp.usage.prompt_tokens, resp.usage.completion_tokens)
                log.debug("generate_completion → tokens: prompt=%d, completion=%d, total=%d",
                         resp.usage.prompt_tokens,
                         resp.usage.completion_tokens,
                         resp.usage.total_tokens)
            return result, usage

        except _RETRYABLE as exc:
            if attempt == MAX_RETRIES:
                log.error("generate_completion → failed after %d attempts: %s", MAX_RETRIES, exc)
                raise
            wait = RETRY_DELAY * (1 + random.random()) * attempt
            log.warning("generate_completion → retryable error (attempt %d/%d), "
                       "retrying in %.1fs: %s", attempt, MAX_RETRIES, wait, exc)
            await asyncio.sleep(wait)

        except APIStatusError as exc:
            # 4xx errors (bad request, auth, content filter) — don't retry
            log.error("generate_completion → non-retryable API error %d: %s",
                     exc.status_code, exc.message)
            raise

    # Unreachable, but keeps type checkers happy
    raise RuntimeError("Exhausted retries")


_BLOCKED_MESSAGE = "I'm sorry, I can't help with that request."


async def generate_completion(
    messages: list[dict[str, str]],
    *,
    max_tokens: int = 16384,
    deployment: str | None = None,
    reasoning_effort: str | None = "minimal",
) -> str:
    """LLMOps-instrumented chat completion — the public choke point.

    Wraps :func:`_generate_completion_impl` (the unchanged OpenAI call) with the
    ``chatbot.llmops`` adapter: input/output guardrails (usecase ``apix_chat`` —
    prompt-injection enabled), model-alias resolution, and a traced StepEvent
    (real prompt/completion tokens + cost + latency). Fully fail-open — if the
    platform is unavailable the impl runs directly and returns the text as before.
    """
    try:
        from chatbot import llmops
    except Exception:
        text, _ = await _generate_completion_impl(
            messages, max_tokens=max_tokens, deployment=deployment, reasoning_effort=reasoning_effort
        )
        return text

    import time as _time

    step = llmops.current_step()
    alias = llmops.alias_for_step(step)
    env = llmops.current_env()
    guardrail = llmops.get_guardrail(llmops.USECASE, env)

    # Input guardrail over the full prompt. A hard block (prompt injection /
    # secret leak) short-circuits with a safe refusal — the LLM is never called.
    prompt_text = "\n".join(m.get("content", "") for m in messages)
    allowed_in, reason_in = llmops.check_input(guardrail, prompt_text)
    resolved = llmops.resolve_deployment(alias, fallback=(deployment or DEPLOYMENT), environment=env)
    if not allowed_in:
        llmops.record_step_event(
            step=step, alias=alias, deployment=resolved, latency_ms=0.0,
            status="skipped", guardrail_allowed=False, guardrail_reason=reason_in,
        )
        log.warning("generate_completion → blocked by guardrail: %s", reason_in)
        return _BLOCKED_MESSAGE

    t0 = _time.perf_counter()
    try:
        text, (ptok, ctok) = await _generate_completion_impl(
            messages, max_tokens=max_tokens, deployment=resolved, reasoning_effort=reasoning_effort
        )
    except Exception as exc:
        latency_ms = (_time.perf_counter() - t0) * 1000.0
        llmops.record_step_event(
            step=step, alias=alias, deployment=resolved, latency_ms=latency_ms,
            status="error", guardrail_allowed=True, guardrail_reason=reason_in, error=repr(exc),
        )
        raise
    latency_ms = (_time.perf_counter() - t0) * 1000.0

    allowed_out, reason_out = llmops.check_output(guardrail, text)
    reason = "; ".join(r for r in (reason_in, reason_out) if r)
    llmops.record_step_event(
        step=step, alias=alias, deployment=resolved,
        input_tokens=ptok, output_tokens=ctok, latency_ms=latency_ms, status="ok",
        guardrail_allowed=bool(allowed_in and allowed_out), guardrail_reason=reason,
    )
    return text


# ═══════════════════════════════════════════════════════════════════════════
# 2. PROMPTS — externalized prompt / message templates (prompts/*.txt)
# ═══════════════════════════════════════════════════════════════════════════
_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


@lru_cache(maxsize=None)
def load_prompt(name: str) -> str:
    """Return the text of ``prompts/<name>.txt`` (trailing newline stripped).

    Result is cached; restart the process to pick up edits.
    """
    path = _PROMPTS_DIR / f"{name}.txt"
    return path.read_text(encoding="utf-8").rstrip("\n")


# ═══════════════════════════════════════════════════════════════════════════
# 3. NARRATION — SQL result rows → professional NL answer
# ═══════════════════════════════════════════════════════════════════════════
_RESPONSE_SYSTEM_PROMPT = load_prompt("response_system")


async def generate_response(
    user_query: str,
    sql_result: list[dict[str, Any]],
    rewritten_query: str | None = None,
    period: str | None = None,
) -> str:
    """Generate a professional NL answer from the SQL result set.

    *user_query* is what the user literally typed; *rewritten_query* (when it
    differs) is the standalone, context-resolved version used to fetch the
    data. Both are given to the LLM so it answers the user's actual intent
    intelligently in natural language instead of dumping rows.
    """
    log.info("generate_response → START query='%s', rows=%d",
             user_query[:100], len(sql_result))

    if not sql_result:
        log.info("generate_response → no data")
        return load_prompt("no_data")

    # Try deterministic formatting for simple results (skips the LLM entirely).
    deterministic = _format_deterministic(user_query, sql_result, period)
    if deterministic:
        log.info("generate_response → deterministic (%d chars)", len(deterministic))
        return deterministic

    # LLM for complex results.
    display = sql_result[:30]
    rewritten = (rewritten_query or "").strip()
    intent_line = (
        f"Resolved intent: {rewritten}\n"
        if rewritten and rewritten != user_query.strip()
        else ""
    )
    user_content = (
        f"Question: {user_query}\n"
        f"{intent_line}"
        f"Data ({len(sql_result)} rows):\n"
        f"{json.dumps(display, default=str)}\n\n"
        "Answer the user's question directly and intelligently in natural "
        "language. Only use the columns relevant to what was asked; ignore "
        "unrelated columns. If the question asks for the distinct values of "
        "something, list those unique values without repetition."
    )
    messages = [
        {"role": "system", "content": _RESPONSE_SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]
    answer = await generate_completion(messages, max_tokens=1024)
    if not answer.strip():
        answer = _format_fallback(sql_result)
    log.info("generate_response → DONE (%d chars)", len(answer))
    return answer


# ── Coaching tips: short pointers + full breakdown ──────────────────────────
_COACHING_SUMMARY_PROMPT = load_prompt("coaching_summary")


def is_coaching_rows(data: list[dict]) -> bool:
    """True when the result set is coaching tips (has a ``tip_text`` column)."""
    if not data or not isinstance(data, list):
        return False
    first = data[0]
    return isinstance(first, dict) and "tip_text" in {k.lower() for k in first}


def _coaching_pointers_fallback(data: list[dict]) -> str:
    """Deterministic short pointers when the LLM summary is unavailable.

    Uses the first sentence of each tip so a compact, useful list is always
    available even if the model call fails.
    """
    lines: list[str] = []
    for row in data:
        tip = str(row.get("tip_text") or row.get("Tip_Text") or "").strip()
        if not tip:
            continue
        # First sentence (up to the first period) trimmed to a readable length.
        first = re.split(r"(?<=[.!?])\s", tip, maxsplit=1)[0].strip()
        if len(first) > 140:
            first = first[:137].rstrip() + "…"
        pr = str(row.get("priority") or row.get("Priority") or "").strip()
        suffix = f" ({pr})" if pr else ""
        lines.append(f"- {first}{suffix}")
    return "\n".join(lines)


async def summarize_coaching(data: list[dict]) -> str | None:
    """Condense coaching tips into short, skimmable pointers.

    Returns a compact markdown bullet list, or ``None`` when there are no tips.
    Falls back to a deterministic first-sentence summary if the LLM returns
    nothing.
    """
    if not is_coaching_rows(data):
        return None

    tips = []
    for row in data:
        tips.append({
            "rank": row.get("tip_rank") or row.get("Tip_Rank"),
            "priority": row.get("priority") or row.get("Priority"),
            "tip_text": row.get("tip_text") or row.get("Tip_Text"),
        })
    tips = [t for t in tips if t["tip_text"]]
    if not tips:
        return None

    user_content = (
        "Coaching tips (JSON):\n"
        + json.dumps(tips, default=str, ensure_ascii=False)
        + "\n\nRewrite these as short pointers per the rules."
    )
    messages = [
        {"role": "system", "content": _COACHING_SUMMARY_PROMPT},
        {"role": "user", "content": user_content},
    ]
    try:
        short = (await generate_completion(messages, max_tokens=512)).strip()
    except Exception as exc:  # narration must never break the reply
        log.warning("summarize_coaching → LLM failed (%s); using fallback", exc)
        short = ""
    if not short:
        short = _coaching_pointers_fallback(data)
    return short or None


def _format_deterministic(query: str, results: list[dict], period: str | None = None) -> str | None:
    """Format simple results professionally without the LLM."""
    # Whole-team list results (typically ≤25 agents) are formatted here too,
    # which skips the slow LLM narration call entirely.
    if len(results) > 25:
        return None

    cols = list(results[0].keys())
    if len(cols) > 8:
        return None

    # Single scalar value.
    if len(results) == 1 and len(cols) == 1:
        col = cols[0]
        val = results[0][col]
        label = _humanize_col(col)
        return f"The {label.lower()} is {_fmt_val(val, col)}."

    # Single row, multiple columns.
    if len(results) == 1:
        row = results[0]
        name_col = next((c for c in cols if "name" in c.lower()), None)
        # For one-row outputs with helper columns (ids/dates/week bounds),
        # return only the primary KPI to keep answers short.
        concise = _concise_single_metric_answer(query, row, name_col, period)
        if concise:
            return concise
        value_cols = [c for c in cols if c != name_col]
        # One named measure → a single, natural sentence (not a list).
        if len(value_cols) == 1:
            c = value_cols[0]
            label = _humanize_col(c).lower()
            val = _fmt_val(row[c], c)
            wk = _row_period(row) or period
            suffix = f" for the week of {wk}" if wk else ""
            if name_col and row[name_col]:
                return f"For {row[name_col]}, the {label} is {val}{suffix}."
            return f"The {label} is {val}{suffix}."
        parts = [f"{_humanize_col(c)}: {_fmt_val(row[c], c)}" for c in value_cols]
        prefix = f"For {row[name_col]}, " if name_col and row[name_col] else ""
        return prefix + "the results are — " + ", ".join(parts) + "."

    # Multiple rows — build a professional bullet list.
    name_col = next((c for c in cols if "name" in c.lower()), None)
    value_cols = [
        c for c in cols
        if c != name_col and c.lower() not in ("employee_id", "employeeid")
    ]
    if not value_cols:
        return None

    # Superlative questions ("who has the highest/lowest …") want a single
    # answer, not the whole roster — resolve the extreme row directly.
    superlative = _superlative_answer(query, results, name_col, value_cols)
    if superlative:
        return superlative

    if name_col:
        unique_names = {row[name_col] for row in results if row.get(name_col) is not None}
        header = "Here's the breakdown for that agent:\n" if len(unique_names) == 1 else "Here's the breakdown:\n"
    else:
        unique_ids = {
            row.get("employee_id") or row.get("EmployeeID")
            for row in results
            if row.get("employee_id") or row.get("EmployeeID")
        }
        header = "Here's the breakdown for that agent:\n" if len(unique_ids) == 1 else "Here's the breakdown:\n"

    lines = [header]
    for row in results:
        name = row.get(name_col, f"ID {row.get('employee_id', '?')}") if name_col else ""
        vals = ", ".join(f"{_humanize_col(vc)}: {_fmt_val(row[vc], vc)}" for vc in value_cols)
        if name:
            lines.append(f"• **{name}** — {vals}")
        else:
            lines.append(f"• {vals}")
    return "\n".join(lines)


_PERIOD_VAL_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _row_period(row: dict) -> str | None:
    """Best-effort extract the week/period label (YYYY-MM-DD) from a result row.

    So single-metric answers can state *which* week they refer to instead of
    silently using the selected week (which triggered "for which week?" loops).
    Prefers a week-start column; falls back to any date-like helper column.
    """
    preferred = ("week_start", "week_start_date", "week", "period", "reportdate", "report_date")
    for col, val in row.items():
        if str(col).lower() in preferred and val is not None:
            m = _PERIOD_VAL_RE.search(str(val))
            if m:
                return m.group(0)
    for col, val in row.items():
        cl = str(col).lower()
        if ("date" in cl or "week" in cl or "period" in cl) and "end" not in cl and val is not None:
            m = _PERIOD_VAL_RE.search(str(val))
            if m:
                return m.group(0)
    return None


def _concise_single_metric_answer(query: str, row: dict, name_col: str | None, period: str | None = None) -> str | None:
    """Return a terse one-line KPI answer from a single-row record.

    Ignores helper dimensions (IDs, dates, week boundaries) and picks one
    primary numeric metric to answer directly.
    """
    q = (query or "").lower()

    ignore_exact = {
        "employeeid", "employee_id", "cimworkernumber", "metricid",
        "timeframe", "reportdate", "week_start_date", "week_end_date",
        "iscalculated", "result_den",
    }
    ignore_sub = ("date", "week", "time", "start", "end")

    metric_desc = row.get("MetricDesc") or row.get("metricdesc")

    candidates: list[tuple[str, int]] = []
    query_named = 0  # how many candidate metrics the user explicitly named
    for col, raw in row.items():
        cl = col.lower()
        if cl in ignore_exact or any(tok in cl for tok in ignore_sub):
            continue
        if col == name_col:
            continue
        try:
            float(raw)
        except (TypeError, ValueError):
            continue
        score = 0
        if cl in ("result_num",):
            score += 5
        if any(k in cl for k in ("avg", "average", "aht", "handle")):
            score += 4
        qtokens = set(re.findall(r"[a-z]+", q))
        coltokens = set(re.findall(r"[a-z]+", cl.replace("_", " ")))
        overlap = qtokens & coltokens
        if overlap:
            query_named += 1
        score += len(overlap)
        candidates.append((col, score))

    if not candidates:
        return None

    # The user explicitly asked about SEVERAL metrics (e.g. "new line pitches,
    # upgrade attempts and save attempts") — don't collapse to one. Defer to the
    # caller so every requested metric is reported. Likewise when they asked for
    # "all" scores/metrics and the row carries several.
    if query_named >= 2:
        return None
    if len(candidates) >= 2 and re.search(r"\ball\b", q):
        return None

    candidates.sort(key=lambda x: x[1], reverse=True)
    metric_col = candidates[0][0]
    metric_val = _fmt_val(row.get(metric_col), metric_col)

    if metric_desc:
        label = str(metric_desc)
    else:
        label = _humanize_col(metric_col)

    wk = _row_period(row) or period
    suffix = f" for the week of {wk}" if wk else ""
    if name_col and row.get(name_col):
        return f"For {row[name_col]}, {label} is {metric_val}{suffix}."
    return f"{label} is {metric_val}{suffix}."


# Superlative keywords → direction of the extremum the user is asking for.
_SUPERLATIVE_MAX = (
    "highest", "most", "top", "best", "maximum", "max", "greatest", "largest",
    "leader", "leading", "strongest", "biggest", "high",
)
_SUPERLATIVE_MIN = (
    "lowest", "least", "worst", "minimum", "min", "bottom", "fewest",
    "smallest", "weakest", "poorest", "low",
)


def _superlative_answer(
    query: str,
    results: list[dict],
    name_col: str | None,
    value_cols: list[str],
) -> str | None:
    """Answer "who has the highest/lowest <metric>" with the single extreme row.

    Returns a natural-language sentence (handling ties) or ``None`` when the
    query is not a superlative or the data is not suitable for one.
    """
    if not name_col:
        return None

    q_words = set(re.findall(r"[a-z]+", query.lower()))
    if q_words & set(_SUPERLATIVE_MAX):
        want_max = True
    elif q_words & set(_SUPERLATIVE_MIN):
        want_max = False
    else:
        return None  # not a superlative question — let the caller list rows

    # Choose the metric column: the single value column, or the one whose
    # humanized name best overlaps the question.
    if len(value_cols) == 1:
        target = value_cols[0]
    else:
        def _overlap(col: str) -> int:
            return len(q_words & set(re.findall(r"[a-z]+", _humanize_col(col).lower())))

        scored = sorted(value_cols, key=_overlap, reverse=True)
        target = scored[0] if _overlap(scored[0]) > 0 else None
        if target is None:
            return None  # ambiguous metric — fall back to the list

    # Collect numeric (name, value) pairs for the chosen metric.
    pairs: list[tuple[str, float]] = []
    for row in results:
        raw = row.get(target)
        try:
            num = float(raw)
        except (TypeError, ValueError):
            continue
        name = row.get(name_col) or f"ID {row.get('employee_id', '?')}"
        pairs.append((str(name), num))

    if not pairs:
        return None

    best = max(p[1] for p in pairs) if want_max else min(p[1] for p in pairs)
    leaders = [name for name, num in pairs if num == best]
    label = _humanize_col(target).lower()
    value = _fmt_val(best, target)
    direction = "highest" if want_max else "lowest"

    if len(leaders) == 1:
        return f"**{leaders[0]}** has the {direction} {label} at {value}."
    if len(leaders) == 2:
        names = " and ".join(f"**{n}**" for n in leaders)
    else:
        names = ", ".join(f"**{n}**" for n in leaders[:-1]) + f", and **{leaders[-1]}**"
    return f"{names} are tied for the {direction} {label} at {value}."


def _format_fallback(results: list[dict]) -> str:
    """Simple fallback formatting if the LLM also fails."""
    if not results:
        return "No data found."
    cols = list(results[0].keys())
    lines = []
    for row in results[:10]:
        parts = [f"{_humanize_col(c)}: {_fmt_val(row[c], c)}" for c in cols]
        lines.append(" | ".join(parts))
    return "\n".join(lines)


def _fmt_val(val: Any, col: str) -> str:
    """Format a value professionally.

    Unit policy (business rule): only PSO KPIs (``pkpi_*``) and the universal
    behaviour / quality SCORES (``bs_*``, ``ch_*``, ``wbs_*`` and the composite
    ``overall_score`` / ``overall_behavior_score`` / ``customer_experience``)
    are percentages — stored 0-1 and shown on a 0-100 scale. Every other KPI —
    WCC (``wkpi_*``) and Telesales KPI groups (pitches, attempts, conversions,
    escalations, call counts, ``np_*``) — is a COUNT and is shown as its raw
    number. When a column's unit is unknown, it is treated as a count.
    """
    col_lower = col.lower()
    # Behavior / coaching quality scores (bs_*, ch_*, wbs_*) are stored as 0.0
    # when never measured (never NULL).  Surfacing "0.0%" is misleading, so
    # report it as "NA".  Genuine KPIs/rates keep their real 0 value.
    _is_quality_score = (
        col_lower.startswith(("bs_", "ch_", "wbs_")) and not col_lower.endswith("_delta")
    )
    if val is None:
        return "NA"
    # Azure/pyodbc returns DECIMAL as Decimal — normalize to float so it shares
    # the numeric formatting below (avoids ugly trailing zeros like "282.0000").
    if isinstance(val, Decimal):
        val = float(val)
    if _is_quality_score and isinstance(val, (int, float)) and float(val) == 0.0:
        return "NA"
    if isinstance(val, float):
        # Strip the +/- variants so the base column drives the unit decision.
        _base = col_lower
        for _suf in ("_delta", "_benchmark"):
            if _base.endswith(_suf):
                _base = _base[: -len(_suf)]
                break
        # PERCENTAGE unit — PSO KPIs + universal behaviour/quality scores only.
        _pct = (
            _base.startswith(("pkpi_", "bs_", "ch_", "wbs_"))
            or _base in {"overall_score", "overall_behavior_score", "customer_experience"}
        )
        if _pct:
            # Stored 0-1 → 0-100; a value already >1 is assumed pre-scaled.
            scaled = val * 100 if -1.0 <= val <= 1.0 else val
            return f"{scaled:.1f}%"
        # COUNT unit (default) — WCC/Telesales KPIs and anything else: raw number.
        if val == int(val):
            return str(int(val))
        return f"{val:.2f}"
    return str(val)


def _humanize_col(col: str) -> str:
    """Convert a column name to a human-readable label."""
    for prefix in ("bs_", "ch_", "wkpi_", "wbs_", "cmp_", "np_"):
        if col.startswith(prefix):
            col = col[len(prefix):]
            break
    if col.endswith("_delta"):
        col = col[:-6] + " (change)"
    elif col.endswith("_benchmark"):
        col = col[:-10] + " (team avg)"
    return col.replace("_", " ").title()
