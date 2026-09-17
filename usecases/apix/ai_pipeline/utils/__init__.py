"""Shared utility functions."""

from __future__ import annotations

import asyncio
import functools
import logging
import os
import random

from aiohttp import ClientError
from openai import OpenAIError, RateLimitError, APITimeoutError, APIConnectionError, InternalServerError

from ai_pipeline.logging_config import get_logger

logger = get_logger("utils.helpers")


class ContentFilterSkip(Exception):
    """Raised when the model rejects an item due to the content filter."""


# Errors that are transient and worth retrying with backoff rather than failing.
_RETRYABLE = (RateLimitError, APITimeoutError, APIConnectionError, InternalServerError, ClientError)

# Rate limits are near-certain to succeed once capacity frees up, so give them a
# more generous, dedicated retry budget than other transient errors.
_MAX_RATE_LIMIT_RETRIES = 8


def _retry_after_seconds(error) -> float | None:
    """Extract the server-suggested Retry-After delay (seconds), if provided."""
    resp = getattr(error, "response", None)
    if resp is None:
        return None
    value = resp.headers.get("retry-after") or resp.headers.get("x-ratelimit-reset")
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def retry_async(max_retries: int, retry_delay: int):
    """Decorator: retry an async function on transient OpenAI / aiohttp errors.

    - Content-filter rejections are non-retryable and surfaced as ContentFilterSkip.
    - Rate limits (429) get their own generous budget with exponential backoff,
      honoring the server's Retry-After header when present.
    - Other transient errors use the standard bounded retry.
    """
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            attempt = 0
            rate_limit_hits = 0
            while True:
                try:
                    return await func(*args, **kwargs)
                except RateLimitError as e:
                    rate_limit_hits += 1
                    if rate_limit_hits > _MAX_RATE_LIMIT_RETRIES:
                        logger.error("Rate limit: giving up after %d retries", rate_limit_hits - 1)
                        raise Exception("Rate limit exceeded after retries") from None
                    delay = _retry_after_seconds(e)
                    if delay is None:
                        delay = min(60, retry_delay * (2 ** min(rate_limit_hits, 6)))
                    delay *= 1 + random.random() * 0.3  # jitter to de-sync callers
                    logger.warning("Rate limited - backing off %.1fs (hit %d/%d)", delay, rate_limit_hits, _MAX_RATE_LIMIT_RETRIES)
                    await asyncio.sleep(delay)
                except _RETRYABLE as e:
                    attempt += 1
                    logger.warning("[Attempt %d] transient error: %s", attempt, e)
                    if attempt >= max_retries:
                        raise Exception("Max retries reached. Skipping item.") from None
                    await asyncio.sleep((retry_delay * (1 + random.random())) * attempt)
                except OpenAIError as e:
                    if "content filter" in str(e).lower():
                        raise ContentFilterSkip("Content filter triggered") from None
                    raise
        return wrapper
    return decorator


# ── Program-to-mode mapping (reads from .env once, cached) ───────────────────

_MODE_ENV_KEYS = {
    "telesales": "TELESALES_PROGRAMS",
    "wcc": "WCC_PROGRAMS",
    "pso": "PSO_PROGRAMS",
}


@functools.lru_cache(maxsize=1)
def _load_program_map() -> dict[str, str]:
    """Parse TELESALES_PROGRAMS / WCC_PROGRAMS env vars into a {program: mode} map.

    Cached after first call so env vars are read only once per process.
    """
    mapping: dict[str, str] = {}
    for mode, key in _MODE_ENV_KEYS.items():
        for p in os.environ.get(key, "").split(","):
            name = p.strip()
            if name:
                mapping[name] = mode
    return mapping


def get_mode_for_program(program_name: str) -> str:
    """Return 'telesales', 'wcc', or 'unknown' for a given ProgramName."""
    return _load_program_map().get(program_name, "unknown")


def get_programs_for_mode(mode: str) -> list[str]:
    """Return program names mapped to *mode* in .env."""
    return [p for p, m in _load_program_map().items() if m == mode]


def get_all_programs() -> list[str]:
    """Return all known program names across all modes from .env."""
    return list(_load_program_map())


def build_program_filter_sql(mode: str | None) -> str:
    """Build a SQL WHERE fragment filtering by ProgramName.

    Args:
        mode: 'telesales', 'wcc', or 'telesales|wcc' to scope to those modes.
              None to include ALL known programs from .env.

    Returns e.g. ``ProgramName IN ('VZW Telesales', 'VZW WCC')`` or
    empty string if no programs are configured.
    """
    if mode:
        names: list[str] = []
        for m in mode.split("|"):
            names.extend(get_programs_for_mode(m.strip()))
    else:
        names = get_all_programs()
    if not names:
        return ""
    escaped = ", ".join(f"'{n}'" for n in names)
    return f"ProgramName IN ({escaped})"


def validate_speaker(speaker: str) -> str:
    if speaker in ("Agent", "Customer"):
        return speaker
    return "Other"


def validate_transcript(transcript: list[dict]) -> list[dict]:
    for i, segment in enumerate(transcript):
        segment["id"] = i
        segment["speaker"] = validate_speaker(segment["speaker"])
    return transcript


def agent_word_fraction(transcript: str) -> float:
    transcript = transcript.lower()
    total = len(transcript.replace("agent:", "").replace("customer:", "").strip().split())
    if not total:
        return 0
    return sum(
        len(s.replace("agent:", "").strip().split())
        for s in transcript.split("\n") if "agent:" in s
    ) / total * 100


def customer_word_fraction(transcript: str) -> float:
    transcript = transcript.lower()
    total = len(transcript.replace("agent:", "").replace("customer:", "").strip().split())
    if not total:
        return 0
    return sum(
        len(s.replace("customer:", "").strip().split())
        for s in transcript.split("\n") if "customer:" in s
    ) / total * 100
