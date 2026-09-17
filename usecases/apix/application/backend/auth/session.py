"""
auth/session.py — Authentication session management
=====================================================
AuthSystem class with role-based access control, audit logging,
and session state management for Streamlit.
"""

import json
import uuid
from datetime import datetime
from typing import Optional

import streamlit as st

from backend.auth.local_auth import verify_user as db_verify_user, get_user as db_get_user
from backend.auth.upn_map import resolve_user
from backend.config.logging import get_logger
from backend.config.programs import find_mode_for_program
from backend.config.settings import settings

logger = get_logger(__name__)


def _try_parse_int(value: str) -> Optional[int]:
    """Return int(value) if value is numeric, else None."""
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def log_audit_event(event_type: str, identity: str, details: str = "") -> None:
    """
    Append a JSON audit record to the audit log file.

    Args:
        event_type: Event label (e.g. 'login_db', 'login_sso', 'logout').
        identity: Username or UPN of the actor.
        details: Optional extra context.
    """
    log_entry = {
        "timestamp": datetime.now().isoformat(),
        "event_type": event_type,
        "identity": identity,
        "details": details,
    }
    settings.LOGS_DIR.mkdir(exist_ok=True)
    with open(settings.AUDIT_LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(log_entry) + "\n")
    logger.info("Audit: %s | identity=%s | %s", event_type, identity, details)


class AuthSystem:
    """Authentication system with role-based access control."""

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    @staticmethod
    def init_session() -> None:
        """Initialise session state keys for authentication."""
        defaults = {
            "authenticated": False,
            "user_name": None,
            "upn": None,
            "employee_id": None,
            "user_role": None,
            "coach_ids": [],
            "selected_program": None,
        }
        for key, value in defaults.items():
            if key not in st.session_state:
                st.session_state[key] = value

    # ------------------------------------------------------------------
    # Auth paths
    # ------------------------------------------------------------------

    @staticmethod
    def login(username: str, password: str) -> bool:
        """
        Authenticate a user via the local SQLite database (password path).

        After credential verification the user profile is resolved from the
        UPN map (by UPN when the username contains ``@``, otherwise by
        numeric employee_id).  This ensures the session receives the same
        display name, role, and coach_ids as the SSO path.

        Args:
            username: Username as stored in auth.db.
            password: Plaintext password attempt.

        Returns:
            True if authentication succeeded, False otherwise.
        """
        if not db_verify_user(username, password):
            logger.warning("Login failed for user: %s", username)
            return False

        user = db_get_user(username)
        if not user:
            return False

        db_role = user.get("role", "coach")

        # Resolve the full user profile from the UPN map so that both
        # login paths produce identical session state.
        if "@" in username:
            profile = resolve_user(upn=username.lower())
        else:
            eid = _try_parse_int(username)
            profile = resolve_user(employee_id=eid) if eid is not None else None

        if profile:
            AuthSystem._set_session(
                user_name=profile["name"],
                upn=None if "@" not in username else username.lower(),
                user_id=profile["employee_id"],
                user_role=profile.get("role", db_role),
                coach_ids=profile.get("coach_ids", []),
                program=find_mode_for_program(profile.get("program", "")),
            )
        else:
            # Fallback: user not in UPN map — use DB role and raw username.
            AuthSystem._set_session(
                user_name=username,
                upn=None if "@" not in username else username.lower(),
                user_id=_try_parse_int(username),
                user_role=db_role,
            )

        # Ensure a default program is always set after login
        if not st.session_state.get("selected_program"):
            from backend.config.programs import DEFAULT_PROGRAM
            st.session_state.selected_program = DEFAULT_PROGRAM

        log_audit_event("login_db", username)
        logger.info("DB login succeeded for user: %s (role=%s)", username, st.session_state.user_role)
        return True

    @staticmethod
    def sso_login(user_name: str, roles: list, upn: str) -> bool:
        """
        Authenticate a user from Azure SSO token data.

        The UPN is resolved against the UPN map to obtain the same
        canonical profile (display name, employee_id, role, coach_ids)
        that the password path would produce.  The AAD role list is used
        as a fallback when the UPN map does not contain a role.

        Args:
            user_name: Display name from the AAD token.
            roles: List of AAD app role values assigned to the user.
            upn: User Principal Name (email) from the token.

        Returns:
            True if authentication succeeded, False otherwise.
        """
        if not user_name or not upn:
            return False

        # Normalise UPN casing to match the password path and upn_map lookups.
        upn = upn.lower()

        # Map AAD role names to internal roles and pick the highest-ranking one
        # so that a user holding both "Admins" and "Managers" always gets admin.
        role_map = {"Managers": "manager", "Coaches": "coach", "Agents": "agent"}
        hierarchy = {"manager": 3, "coach": 2, "agent": 1}
        aad_role = "coach"
        for r in roles or []:
            mapped = role_map.get(r)
            if mapped and hierarchy.get(mapped, 0) > hierarchy.get(aad_role, 0):
                aad_role = mapped

        profile = resolve_user(upn=upn)

        if profile:
            user_role = profile.get("role", aad_role)
            AuthSystem._set_session(
                user_name=profile["name"],
                upn=upn,
                user_id=profile["employee_id"],
                user_role=user_role,
                coach_ids=profile.get("coach_ids", []),
                program=find_mode_for_program(profile.get("program", "")),
            )
        else:
            # Fallback: UPN not in the map — use AAD-provided data.
            AuthSystem._set_session(
                user_name=user_name,
                upn=upn,
                user_id=None,
                user_role=aad_role,
            )

        # Ensure a default program is always set after SSO login
        if not st.session_state.get("selected_program"):
            from backend.config.programs import DEFAULT_PROGRAM
            st.session_state.selected_program = DEFAULT_PROGRAM

        log_audit_event("login_sso", upn)
        logger.info("SSO login succeeded for UPN: %s (role=%s)", upn, st.session_state.user_role)
        return True

    @staticmethod
    def logout() -> None:
        """Log out the current user and clear session state."""
        user = st.session_state.get("upn", "unknown")
        logger.info("User logout: %s", user)
        log_audit_event("logout", user)
        AuthSystem._clear_session()

    # ------------------------------------------------------------------
    # Session accessors
    # ------------------------------------------------------------------

    @staticmethod
    def is_authenticated() -> bool:
        """Return True if a user is currently authenticated."""
        return st.session_state.get("authenticated", False)

    @staticmethod
    def get_user_name() -> Optional[str]:
        """Return the authenticated user's display name."""
        return st.session_state.get("user_name")

    @staticmethod
    def get_upn() -> Optional[str]:
        """Return the authenticated user's UPN (email)."""
        return st.session_state.get("upn")

    @staticmethod
    def get_employee_id() -> Optional[int]:
        """Return the authenticated user's integer employee_id."""
        return st.session_state.get("employee_id")

    @staticmethod
    def get_user_role() -> Optional[str]:
        """Return the authenticated user's role string."""
        return st.session_state.get("user_role")

    @staticmethod
    def get_coach_ids() -> Optional[list]:
        """Return the list of coach employee_ids this manager oversees.

        Returns None if the user has no UPN map entry (unmapped manager),
        or an empty list if explicitly mapped with no coaches.
        """
        return st.session_state.get("coach_ids")

    @staticmethod
    def get_selected_program() -> Optional[str]:
        """Return the currently selected program ID."""
        return st.session_state.get("selected_program")

    @staticmethod
    def has_role(required_role: str) -> bool:
        """
        Check whether the current user holds at least the required role.

        Role hierarchy: manager > coach > agent.

        Args:
            required_role: Minimum role required ('manager', 'coach', 'agent').

        Returns:
            True if the user's role satisfies the requirement.
        """
        hierarchy = {"manager": 3, "coach": 2, "agent": 1}
        user_role = AuthSystem.get_user_role()
        if not user_role:
            return False
        return hierarchy.get(user_role, 0) >= hierarchy.get(required_role, 99)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _set_session(
        user_name: str,
        upn: str,
        user_id: Optional[int],
        user_role: str,
        coach_ids: Optional[list] = None,
        program: Optional[str] = None,
    ) -> None:
        """Write canonical session payload."""
        st.session_state.authenticated = True
        st.session_state.user_name = user_name
        st.session_state.upn = upn
        st.session_state.user_id = user_id
        st.session_state.user_role = user_role
        st.session_state.coach_ids = coach_ids
        # Fresh per-login token so the chatbot starts a new conversation on
        # every login/logout instead of resuming history across sessions.
        st.session_state.login_token = uuid.uuid4().hex
        if program:
            st.session_state.selected_program = program

    @staticmethod
    def _clear_session() -> None:
        """Reset all session keys to their unauthenticated defaults."""
        st.session_state.authenticated = False
        st.session_state.user_name = None
        st.session_state.upn = None
        st.session_state.user_id = None
        st.session_state.user_role = None
        st.session_state.coach_ids = []
        st.session_state.login_token = None
        st.session_state.selected_program = None
