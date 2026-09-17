"""
chatbot/api/schemas.py — Pydantic request/response models for the chat API.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    """Incoming chat request payload.

    Attributes:
        user_query: Natural-language question (1-2000 chars).
        user_id: Numeric ID of the authenticated user (coach or manager).
        role: Caller's role -- ``"coach"`` or ``"manager"``.
        reporting_agents: Scope payload whose shape depends on *role*:

            * **coach** -- ``list[int]`` of employee IDs the coach manages
              directly, e.g. ``[9058816, 9063989]``.
            * **manager** -- ``dict[str, int]`` mapping each employee ID
              (as string key) to their reporting coach ID,
              e.g. ``{"9058816": 42, "9063989": 42, "9063995": 55}``.
    """

    user_query: str = Field(..., min_length=1, max_length=2000)
    user_id: int = Field(...)
    user_name: str = Field(default="", max_length=200)
    role: str = Field(..., pattern=r"^(coach|manager)$")
    reporting_agents: list[int] | dict[str, int] = Field(...)
    session_id: str | None = Field(
        default=None,
        description="Active chat session ID. The server loads recent messages "
                    "from the session store to seed conversation context.",
    )
    focus_agent_id: int | None = Field(
        default=None,
        description="Employee ID of the agent the user is currently viewing in "
                    "the dashboard. Used as the authoritative agent context so "
                    "follow-up questions resolve to the selected agent and a "
                    "change of selection immediately switches context — even "
                    "within the same session.",
    )
    focus_scope: str = Field(
        default="agent",
        pattern=r"^(agent|team)$",
        description="Whether the UI is focused on a single agent (``agent``) or "
                    "the whole team (``team``). ``team`` drops any single-agent "
                    "context so questions are answered team-wide.",
    )
    period: str | None = Field(
        default=None,
        description="Reporting period (week) currently selected in the UI, e.g. "
                    "``2026-07-24``. Seeds the period context when the question "
                    "doesn't name one explicitly.",
    )


class ChatResponse(BaseModel):
    """Response payload returned to the frontend.

    Attributes:
        query: Echo of the original ``user_query``.
        data: Raw SQL result rows (list of column-value dicts).
        response: GPT-5 generated natural-language answer.
        details: Optional full-length text when ``response`` is a shortened
            summary (e.g. coaching pointers). The frontend reveals it behind a
            "Show full message" toggle. ``None`` when there is nothing extra.
        elapsed_ms: Total server-side processing time in milliseconds.
    """

    query: str
    data: list[dict[str, Any]]
    response: str
    details: str | None = None
    elapsed_ms: int
