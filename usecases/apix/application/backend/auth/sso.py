import requests
import streamlit as st
from msal import ConfidentialClientApplication
import jwt
from jwt import PyJWKClient

from dotenv import load_dotenv
import os

from backend.config.logging import get_logger

logger = get_logger(__name__)

load_dotenv()

AZURE_CLIENT_ID: str = str(os.environ.get("APPLICATION_CLIENT_ID"))
AZURE_CLIENT_SECRET: str = str(os.environ.get("APPLICATION_CLIENT_SECRET"))
AZURE_TENANT_ID: str = str(os.environ.get("APPLICATION_TENANT_ID"))
REDIRECT_UI: str = str(os.environ.get("APPLICATION_URI"))
AZURE_SCOPE: str = f"api://{AZURE_CLIENT_ID}/User.Read"

# JWKS endpoint used to verify JWT signatures issued by Azure AD.
_JWKS_URI: str = (
    f"https://login.microsoftonline.com/{AZURE_TENANT_ID}/discovery/v2.0/keys"
)


def initialize_app():
    logger.info("Initializing MSAL ConfidentialClientApplication")
    client_id = AZURE_CLIENT_ID
    tenant_id = AZURE_TENANT_ID
    client_secret = AZURE_CLIENT_SECRET
    authority_url = f"https://login.microsoftonline.com/{tenant_id}"
    return ConfidentialClientApplication(
        client_id, authority=authority_url, client_credential=client_secret
    )


def acquire_access_token(app, code, scopes, redirect_uri):
    return app.acquire_token_by_authorization_code(
        code, scopes=scopes, redirect_uri=redirect_uri
    )


def decode_token(access_token: str) -> dict:
    decoded_token = jwt.decode(access_token, options={"verify_signature": False})
    return decoded_token


def handle_sso_callback(app, code: str):
    """Exchange an OAuth authorization code for user info. Returns (user_name, roles, upn)."""
    logger.info("Processing SSO callback")
    token_result = acquire_access_token(app, code, [AZURE_SCOPE], REDIRECT_UI)
    if "access_token" in token_result:
        decoded = decode_token(token_result["access_token"])
        upn = decoded.get("upn")
        logger.info("SSO token decoded for UPN: %s", upn)
        return decoded.get("name"), decoded.get("roles", []), upn
    logger.warning("SSO callback failed — no access_token in response")
    return None, [], None


def authentication_process(app):
    """Legacy UI-driven auth flow. Renders a link and handles the callback via query params."""
    auth_url = app.get_authorization_request_url(
        [AZURE_SCOPE], redirect_uri=REDIRECT_UI
    )
    st.markdown(f"Please go to [this URL]({auth_url}) and authorize the app.")
    code = st.query_params.get("code")
    if code:
        st.session_state["auth_code"] = code
        user_name, roles, upn = handle_sso_callback(app, code)
        if user_name:
            return user_name, roles, upn
        st.error("Failed to acquire token. Please check your input and try again.")
    return None, [], None