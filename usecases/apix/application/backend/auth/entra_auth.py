# ============================================================================
# Microsoft Entra ID Authentication Module
# ============================================================================

import os
import logging
from typing import Optional, Dict, Any

import msal
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# ─── Configuration ──────────────────────────────────────────────────────────

ENTRA_CLIENT_ID: str = os.getenv("ENTRA_CLIENT_ID", "")
ENTRA_CLIENT_SECRET: str = os.getenv("ENTRA_CLIENT_SECRET", "")
ENTRA_TENANT_ID: str = os.getenv("ENTRA_TENANT_ID", "")
ENTRA_REDIRECT_URI: str = os.getenv("ENTRA_REDIRECT_URI", "http://localhost:8501")
ENTRA_AUTHORITY: str = f"https://login.microsoftonline.com/{ENTRA_TENANT_ID}"

# Scopes requested during login – User.Read for basic profile info
SCOPES: list[str] = ["User.Read"]

# Mapping from Entra ID app-role values to local application roles.
# Configure app roles in Azure Portal → App registrations → App roles.
# The keys must match the "value" field of each app role definition.
ROLE_MAP: Dict[str, str] = {
    "Admin": "admin",
    "Manager": "manager",
    "Coach": "coach",
}

# Default role when no app-role is assigned in Entra ID
DEFAULT_ROLE: str = "coach"


# ─── MSAL helpers ───────────────────────────────────────────────────────────

def _build_msal_app() -> msal.ConfidentialClientApplication:
    """Build a confidential MSAL client application."""
    return msal.ConfidentialClientApplication(
        client_id=ENTRA_CLIENT_ID,
        client_credential=ENTRA_CLIENT_SECRET or None,
        authority=ENTRA_AUTHORITY,
    )


def _resolve_role(id_token_claims: Dict[str, Any]) -> str:
    """
    Determine the application role from the Entra ID token claims.

    Checks the 'roles' claim (populated when App Roles are assigned in
    Azure Portal) and maps it via ROLE_MAP. Falls back to DEFAULT_ROLE.
    """
    roles = id_token_claims.get("roles", [])
    for role_value in roles:
        if role_value in ROLE_MAP:
            return ROLE_MAP[role_value]
    return DEFAULT_ROLE


# ─── Public API ─────────────────────────────────────────────────────────────

class EntraAuth:
    """Microsoft Entra ID authentication for Streamlit applications."""

    def __init__(self) -> None:
        self._app = _build_msal_app()
        self._init_session_state()

    # ── session helpers ──────────────────────────────────────────────────

    @staticmethod
    def _init_session_state() -> None:
        defaults = {
            "authenticated": False,
            "user_name": None,
            "user_role": None,
            "user_id": None,
            "user_email": None,
            "entra_flow": None,
        }
        for key, value in defaults.items():
            if key not in st.session_state:
                st.session_state[key] = value

    # ── status checks ────────────────────────────────────────────────────

    @staticmethod
    def is_authenticated() -> bool:
        return st.session_state.get("authenticated", False)

    @staticmethod
    def get_current_user() -> Optional[Dict[str, Any]]:
        """Return the current authenticated user's info, or None."""
        if not st.session_state.get("authenticated"):
            return None
        return {
            "user_id": st.session_state.user_id,
            "user_name": st.session_state.user_name,
            "user_role": st.session_state.user_role,
            "user_email": st.session_state.user_email,
        }

    # ── login flow ───────────────────────────────────────────────────────

    def show_login(self) -> None:
        """Render the Entra ID login button and handle the OAuth callback."""
        self._handle_callback()

        if self.is_authenticated():
            return

        st.markdown("### Sign in with Microsoft Entra ID")
        if st.button("🔑 Sign in with Microsoft"):
            self._initiate_login()

    def _initiate_login(self) -> None:
        """Start the authorization code flow and redirect the user."""
        flow = self._app.initiate_auth_code_flow(
            scopes=SCOPES,
            redirect_uri=ENTRA_REDIRECT_URI,
        )
        st.session_state.entra_flow = flow
        auth_uri = flow.get("auth_uri", "")
        if auth_uri:
            st.markdown(
                f'<meta http-equiv="refresh" content="0;url={auth_uri}">',
                unsafe_allow_html=True,
            )
            st.stop()
        else:
            st.error("Failed to generate authorization URL. Check Entra ID configuration.")

    def _handle_callback(self) -> None:
        """Process the OAuth callback (query parameters after redirect)."""
        query_params = st.query_params
        code = query_params.get("code")
        flow = st.session_state.get("entra_flow")

        if not code or not flow:
            return

        try:
            result = self._app.acquire_token_by_auth_code_flow(
                auth_code_flow=flow,
                auth_response=dict(query_params),
            )
        except Exception:
            logger.exception("Token acquisition failed")
            st.error("Authentication failed. Please try again.")
            st.session_state.entra_flow = None
            return

        if "error" in result:
            logger.error("Entra auth error: %s - %s",
                         result.get("error"), result.get("error_description"))
            st.error(f"Authentication error: {result.get('error_description', 'Unknown error')}")
            st.session_state.entra_flow = None
            return

        id_token_claims = result.get("id_token_claims", {})

        st.session_state.authenticated = True
        st.session_state.user_name = id_token_claims.get("name", "Unknown")
        st.session_state.user_email = id_token_claims.get("preferred_username", "")
        st.session_state.user_id = id_token_claims.get("oid", "")
        st.session_state.user_role = _resolve_role(id_token_claims)
        st.session_state.entra_flow = None

        # Clear OAuth query params from URL
        st.query_params.clear()

        logger.info("User authenticated: %s (%s)",
                     st.session_state.user_name, st.session_state.user_role)

    # ── logout ───────────────────────────────────────────────────────────

    @staticmethod
    def logout() -> None:
        """Clear local session and redirect to Entra ID logout endpoint."""
        post_logout_uri = ENTRA_REDIRECT_URI
        logout_url = (
            f"{ENTRA_AUTHORITY}/oauth2/v2.0/logout"
            f"?post_logout_redirect_uri={post_logout_uri}"
        )

        st.session_state.authenticated = False
        st.session_state.user_name = None
        st.session_state.user_role = None
        st.session_state.user_id = None
        st.session_state.user_email = None
        st.session_state.entra_flow = None

        st.markdown(
            f'<meta http-equiv="refresh" content="0;url={logout_url}">',
            unsafe_allow_html=True,
        )
        st.stop()


