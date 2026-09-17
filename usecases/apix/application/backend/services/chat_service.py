"""
backend/services/chat_service.py — Chat message handler for APIX Insight
=========================================================================
Processes user messages and returns responses. Currently provides a
rule-based handler; can be swapped for an LLM backend later.
"""

from __future__ import annotations

import streamlit as st
from backend.config.logging import get_logger

logger = get_logger(__name__)


def process_chat_message(message: str) -> str:
    """
    Process a user chat message and return a bot response.

    Parameters
    ----------
    message : str
        The user's message text.

    Returns
    -------
    str
        The bot's response string.
    """
    logger.info("Chat message received: %s", message[:100])
    msg = message.strip().lower()

    # ── Greetings ──
    if any(w in msg for w in ("hello", "hi", "hey", "good morning", "good afternoon")):
        user = st.session_state.get("display_name", "")
        greeting = f"Hello{' ' + user if user else ''}!"
        return f"{greeting} How can I help you with your performance data today?"

    # ── Help / capabilities ──
    if any(w in msg for w in ("help", "what can you do", "capabilities", "features")):
        return (
            "I can help you with:\n"
            "• Understanding your APIX score and KPIs\n"
            "• Explaining coaching recommendations\n"
            "• Navigating the dashboard pages\n"
            "• Answering questions about performance metrics\n\n"
            "Just ask me anything!"
        )

    # ── APIX / score related ──
    if any(w in msg for w in ("apix", "score", "index", "performance")):
        return (
            "APIX (Afni Performance Intelligence Index) measures agent "
            "performance across multiple KPIs. Each metric is weighted "
            "and scored to produce an overall index. Check the Individual "
            "Report page for detailed breakdowns, or the Manager Overview "
            "for team-level insights."
        )

    # ── KPI questions ──
    if any(w in msg for w in ("kpi", "metric", "metrics", "measure")):
        return (
            "KPIs are the key performance indicators that make up your "
            "APIX score. Each program defines its own set of KPIs with "
            "specific weights. Visit the Analytics page to see trend "
            "charts and comparisons across your team."
        )

    # ── Coaching ──
    if any(w in msg for w in ("coaching", "recommendation", "tip", "improve")):
        return (
            "Coaching recommendations are generated based on your "
            "performance data and prioritized by impact level (Very High, "
            "High, Medium, Low). Check the Individual Report page to see "
            "your personalized coaching tips."
        )

    # ── Navigation ──
    if any(w in msg for w in ("navigate", "page", "where", "find", "go to")):
        return (
            "The dashboard has three main views:\n"
            "• Manager Overview — team-level performance summary\n"
            "• Individual Report — detailed agent performance & coaching\n"
            "• Analytics — trends, comparisons, and deep dives\n\n"
            "Use the sidebar to switch between them."
        )

    # ── Thanks ──
    if any(w in msg for w in ("thank", "thanks", "thx")):
        return "You're welcome! Let me know if you need anything else."

    # ── Fallback ──
    return (
        "I'm still learning! I can help with questions about your APIX "
        "score, KPIs, coaching recommendations, and dashboard navigation. "
        "Try asking about one of those topics."
    )
