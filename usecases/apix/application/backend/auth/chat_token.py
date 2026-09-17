"""
backend/auth/chat_token.py — Mint short-lived JWTs for the chat widget.

The floating chat widget calls the chatbot API **directly from the browser**, so
it needs a credential that proves the request comes from an authenticated APIX
user without exposing any long-lived secret. This module mints a short-lived
HMAC-signed JWT (shared secret ``CHAT_JWT_SECRET``) scoped to the logged-in user;
the chatbot service validates it and binds the request's ``user_id`` to the
token subject. See ``chatbot/core/auth.py`` for the verifying side.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import jwt  # PyJWT

from backend.config.logging import get_logger
from backend.config.settings import settings

logger = get_logger(__name__)


def mint_chat_token(user_id: int | str, role: str, name: str = "") -> str:
    """Return a signed JWT for *user_id*, or ``""`` if no secret is configured.

    The token carries the user id as ``sub`` plus role/name for auditing and
    expires after ``CHAT_JWT_TTL_MINUTES`` so a leaked token is short-lived.
    """
    if not settings.CHAT_JWT_SECRET:
        logger.warning(
            "CHAT_JWT_SECRET is not set — chat widget will be unauthenticated. "
            "Configure the shared secret to secure the chatbot API."
        )
        return ""

    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "role": role,
        "name": name,
        "iss": settings.CHAT_JWT_ISSUER,
        "aud": settings.CHAT_JWT_AUDIENCE,
        "iat": now,
        "exp": now + timedelta(minutes=settings.CHAT_JWT_TTL_MINUTES),
    }
    return jwt.encode(payload, settings.CHAT_JWT_SECRET, algorithm=settings.CHAT_JWT_ALG)
