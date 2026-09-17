"""
ui/pages/login.py — Login / authentication page
=================================================
"""

import streamlit as st

from backend.auth.session import AuthSystem
from backend.config.logging import get_logger
from backend.config.programs import load_program_config, DEFAULT_PROGRAM

logger = get_logger(__name__)

# Conditional SSO import (same pattern as the original monolith)
_SSO_AVAILABLE = False
try:
    from backend.auth.sso import (
        AZURE_SCOPE,
        REDIRECT_UI,
        initialize_app,
        handle_sso_callback,
    )
    _SSO_AVAILABLE = True
except Exception:
    pass


def render_login_page() -> None:
    """
    Render the authentication / login page.

    Provides a username/password form (SQLite path) and an optional
    Microsoft SSO button when SSO is configured.
    """
    logger.info("Rendering login page")
    

    st.markdown(
        f"""
    <div class="enterprise-header">
        <h1>APIX - Afni Performance Intelligence Index</h1>
        <div class="subtitle">Employee Coaching &amp; Performance Dashboard</div>
    </div>
    """,
        unsafe_allow_html=True,
    )

    col1, col2, col3 = st.columns([1, 2, 1])

    with col2:
        st.markdown("### Sign In")

        username = st.text_input("Username")
        password = st.text_input("Password", type="password")

        if st.button("Login", width="stretch"):
            if AuthSystem.login(username, password):
                st.success("Login successful!")
                st.rerun()
            else:
                st.error("Invalid credentials — check your coach ID and password")

        st.markdown(
            "<div style='text-align:center; margin: 0.75rem 0; color: #94a3b8;"
            " font-size: 0.85rem;'>or</div>",
            unsafe_allow_html=True,
        )

        if _SSO_AVAILABLE:
            try:
                sso_app = initialize_app()
                auth_url = sso_app.get_authorization_request_url(
                    [AZURE_SCOPE], redirect_uri=REDIRECT_UI
                )
                st.link_button("Login with Microsoft SSO", auth_url, width="stretch")
            except Exception as e:
                print(f"SSO initialization error: {e}")
                st.info("SSO is not configured. Use username/password to sign in.")
        else:
            st.info("SSO is not configured. Use username/password to sign in.")

        st.markdown("---")
