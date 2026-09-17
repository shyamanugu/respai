"""Auth routes — password login, Entra SSO redirect flow, session, chat-token."""
from __future__ import annotations

import os

from fastapi import APIRouter, Depends, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from .security import (
    COOKIE_NAME,
    Principal,
    get_principal,
    highest_role,
    mint_session_cookie,
    principal_from_record,
    sso_exchange,
    sso_login_url,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _cookie_kwargs() -> dict:
    secure = os.environ.get("APIX_COOKIE_SECURE", "true").lower() != "false"
    return {"httponly": True, "samesite": "lax", "secure": secure, "path": "/"}


def _set_session(resp: Response, principal: Principal) -> None:
    resp.set_cookie(COOKIE_NAME, mint_session_cookie(principal), **_cookie_kwargs())


class PasswordLogin(BaseModel):
    username: str
    password: str


@router.post("/login/password")
def login_password(body: PasswordLogin, response: Response) -> dict:
    from backend.auth.local_auth import get_user, verify_user
    from backend.auth.upn_map import resolve_user
    from backend.config.programs import DEFAULT_PROGRAM, find_mode_for_program

    if not verify_user(body.username, body.password):
        response.status_code = 401
        return {"authenticated": False, "detail": "invalid credentials"}

    db_user = get_user(body.username) or {}
    db_role = db_user.get("role", "coach")
    username = body.username
    record = (
        resolve_user(upn=username.lower()) if "@" in username
        else resolve_user(employee_id=int(username)) if username.isdigit() else None
    )
    principal = principal_from_record(record or {}, fallback_name=username,
                                      fallback_role=db_role, upn=username.lower())
    if not principal.program:
        principal.program = find_mode_for_program(DEFAULT_PROGRAM)
    _set_session(response, principal)
    return {"authenticated": True, "user": principal.model_dump()}


@router.get("/login")
def login_sso() -> dict:
    return {"auth_url": sso_login_url()}


@router.get("/callback")
def sso_callback(code: str) -> RedirectResponse:
    from backend.auth.upn_map import resolve_user
    from backend.config.programs import DEFAULT_PROGRAM, find_mode_for_program

    name, roles, upn = sso_exchange(code)
    record = resolve_user(upn=(upn or "").lower()) if upn else None
    principal = principal_from_record(record or {}, fallback_name=name,
                                      fallback_role=highest_role(roles), upn=upn)
    if not principal.program:
        principal.program = find_mode_for_program(DEFAULT_PROGRAM)

    spa = os.environ.get("APIX_SPA_URL", "/")
    resp = RedirectResponse(url=spa, status_code=302)
    _set_session(resp, principal)
    return resp


@router.get("/session")
def session(principal: Principal = Depends(get_principal)) -> dict:
    return {"authenticated": True, **principal.model_dump()}


@router.post("/logout")
def logout(response: Response) -> dict:
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"status": "logged_out"}


@router.get("/chat-token")
def chat_token(principal: Principal = Depends(get_principal)) -> dict:
    from backend.auth.chat_token import mint_chat_token

    token = mint_chat_token(principal.sub, principal.role, principal.name)
    return {
        "token": token,
        "chat_api_base": os.environ.get("CHAT_API_BASE_URL", ""),
        "expires_in_minutes": int(os.environ.get("CHAT_JWT_TTL_MINUTES", "30")),
        "user_id": principal.sub,
    }
