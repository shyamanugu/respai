"""Auth for the dashboard API — the API is the OAuth confidential client.

Login (password or Entra SSO) resolves a canonical principal, which is stored in
a signed **HttpOnly session cookie** (stateless, so it works across Container
Apps replicas). RBAC scoping is always derived from this server-trusted
principal, never from client-supplied ids.
"""
from __future__ import annotations

import os
import time

import jwt
from fastapi import Cookie, HTTPException
from pydantic import BaseModel

COOKIE_NAME = "apix_session"
_ALG = "HS256"
_TTL_SECONDS = int(os.environ.get("APIX_SESSION_TTL_SECONDS", str(8 * 3600)))

# AAD app role name -> internal role (mirrors backend/auth/session.py).
_AAD_ROLE_MAP = {"Managers": "manager", "Coaches": "coach", "Agents": "agent"}
_ROLE_RANK = {"agent": 1, "coach": 2, "manager": 3}


class Principal(BaseModel):
    sub: str  # canonical employee_id (string)
    name: str = ""
    upn: str | None = None
    role: str = "coach"
    coach_ids: list[str] = []
    program: str | None = None


def _secret() -> str:
    s = os.environ.get("APIX_SESSION_SECRET", "").strip()
    if not s:
        # Dev fallback so local runs work; MUST be set in any shared/prod env.
        s = "dev-insecure-apix-session-secret-change-me"
    return s


def mint_session_cookie(principal: Principal) -> str:
    now = int(time.time())
    payload = {**principal.model_dump(), "iat": now, "exp": now + _TTL_SECONDS}
    return jwt.encode(payload, _secret(), algorithm=_ALG)


def _decode(token: str) -> Principal | None:
    try:
        claims = jwt.decode(token, _secret(), algorithms=[_ALG])
        return Principal(
            sub=str(claims.get("sub", "")),
            name=claims.get("name", "") or "",
            upn=claims.get("upn"),
            role=claims.get("role", "coach"),
            coach_ids=[str(c) for c in (claims.get("coach_ids") or [])],
            program=claims.get("program"),
        )
    except Exception:
        return None


def get_principal(apix_session: str | None = Cookie(default=None)) -> Principal:
    """FastAPI dependency — returns the authenticated principal or 401."""
    if not apix_session:
        raise HTTPException(status_code=401, detail="not authenticated")
    principal = _decode(apix_session)
    if principal is None:
        raise HTTPException(status_code=401, detail="invalid or expired session")
    return principal


def highest_role(aad_roles: list[str]) -> str:
    best = "coach"
    best_rank = _ROLE_RANK["coach"]
    for r in aad_roles or []:
        internal = _AAD_ROLE_MAP.get(r)
        if internal and _ROLE_RANK[internal] >= best_rank:
            best, best_rank = internal, _ROLE_RANK[internal]
    return best


def principal_from_record(record: dict, fallback_name: str = "", fallback_role: str = "coach",
                          upn: str | None = None) -> Principal:
    """Build a Principal from an upn_map ``resolve_user`` record (or a fallback)."""
    if record:
        return Principal(
            sub=str(record.get("employee_id", "")),
            name=record.get("name") or fallback_name,
            upn=upn or record.get("upn"),
            role=record.get("role") or fallback_role,
            coach_ids=[str(c) for c in (record.get("coach_ids") or [])],
            program=record.get("program"),
        )
    return Principal(sub=str(upn or fallback_name), name=fallback_name, upn=upn, role=fallback_role)


# ── Entra SSO (confidential client, ENTRA_* env) ─────────────────────────────
def _msal_app():
    import msal

    tenant = os.environ.get("ENTRA_TENANT_ID", "")
    client_id = os.environ.get("ENTRA_CLIENT_ID", "")
    secret = os.environ.get("ENTRA_CLIENT_SECRET", "")
    if not (tenant and client_id and secret):
        raise HTTPException(status_code=503, detail="SSO not configured")
    return msal.ConfidentialClientApplication(
        client_id, authority=f"https://login.microsoftonline.com/{tenant}", client_credential=secret
    )


def _scopes() -> list[str]:
    # Graph User.Read is sufficient to get name/upn/roles from the id token.
    return ["User.Read"]


def sso_login_url() -> str:
    app = _msal_app()
    return app.get_authorization_request_url(
        _scopes(), redirect_uri=os.environ.get("ENTRA_REDIRECT_URI", "")
    )


def sso_exchange(code: str) -> tuple[str, list[str], str | None]:
    """Exchange an auth code for (name, roles, upn) from the id-token claims."""
    app = _msal_app()
    result = app.acquire_token_by_authorization_code(
        code, scopes=_scopes(), redirect_uri=os.environ.get("ENTRA_REDIRECT_URI", "")
    )
    claims = result.get("id_token_claims", {}) if isinstance(result, dict) else {}
    name = claims.get("name", "") or ""
    upn = claims.get("preferred_username") or claims.get("upn")
    roles = claims.get("roles", []) or []
    return name, roles, upn
