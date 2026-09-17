"""
chatbot/core/auth.py — API authentication for the chatbot service.

The chatbot API is called **directly from the browser** by the Streamlit
application's chat widget, so it cannot rely on network isolation. Instead the
trusted Streamlit backend (which already authenticates the user via Entra/DB)
mints a short-lived HMAC-signed JWT per logged-in user and the browser sends it
on every request as ``Authorization: Bearer <token>``.

This module validates that token with a shared secret (``CHAT_JWT_SECRET``) and
exposes helpers the HTTP layer uses to:

* reject unauthenticated / expired / tampered requests, and
* bind the request's ``user_id`` to the token subject so a caller cannot spoof
  another user's data scope.

Set ``CHAT_AUTH_DISABLED=true`` to bypass auth for purely local development.
"""

from __future__ import annotations

import os

import jwt  # PyJWT

from chatbot.core.config import get_logger

log = get_logger(__name__)

# ── Configuration (shared with the Streamlit app's token minter) ────────────
JWT_SECRET: str = os.getenv("CHAT_JWT_SECRET", "")
JWT_ALGORITHM: str = os.getenv("CHAT_JWT_ALG", "HS256")
JWT_ISSUER: str = os.getenv("CHAT_JWT_ISSUER", "apix-app")
JWT_AUDIENCE: str = os.getenv("CHAT_JWT_AUDIENCE", "apix-chatbot")

# Escape hatch for local development only — NEVER enable in a deployed service.
AUTH_DISABLED: bool = os.getenv("CHAT_AUTH_DISABLED", "false").lower() in (
    "1", "true", "yes",
)

if AUTH_DISABLED:
    log.warning(
        "CHAT_AUTH_DISABLED is set — chatbot API authentication is OFF. "
        "This must only be used for local development."
    )
elif not JWT_SECRET:
    log.warning(
        "CHAT_JWT_SECRET is not set — every request will be rejected with 401. "
        "Configure the shared secret to enable authentication."
    )


class AuthError(Exception):
    """Raised when a bearer token is missing, malformed, expired or invalid."""


def decode_token(token: str) -> dict:
    """Validate *token* and return its claims, or raise :class:`AuthError`.

    Verifies the HMAC signature, expiry (``exp``), issuer and audience so only
    tokens minted by the Streamlit backend with the shared secret are accepted.
    """
    if not JWT_SECRET:
        raise AuthError("server auth not configured")
    try:
        return jwt.decode(
            token,
            JWT_SECRET,
            algorithms=[JWT_ALGORITHM],
            audience=JWT_AUDIENCE,
            issuer=JWT_ISSUER,
            options={"require": ["exp", "sub"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise AuthError("token expired") from exc
    except jwt.InvalidTokenError as exc:
        raise AuthError("invalid token") from exc


def extract_bearer(authorization: str | None) -> str:
    """Return the raw token from an ``Authorization: Bearer <token>`` header."""
    if not authorization:
        raise AuthError("missing Authorization header")
    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].strip():
        raise AuthError("malformed Authorization header")
    return parts[1].strip()


def authenticate(authorization: str | None) -> dict:
    """Full header → validated-claims path used by the HTTP middleware.

    Honours ``CHAT_AUTH_DISABLED`` for local development (returns a synthetic
    claim set); otherwise validates the bearer token.
    """
    if AUTH_DISABLED:
        return {"sub": None, "auth_disabled": True}
    return decode_token(extract_bearer(authorization))
