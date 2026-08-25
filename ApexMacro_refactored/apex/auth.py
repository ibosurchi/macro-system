"""Central authentication and authorization guards."""
import streamlit as st
from . import production_core as core

def restore_auth_user() -> dict | None:
    return core.restore_authenticated_session()

def require_auth() -> dict:
    auth_user = restore_auth_user()
    if not (auth_user and auth_user.get("is_authenticated")):
        st.session_state["APEX_PUBLIC_VIEW"] = "login"
        st.switch_page("pages/login.py")
        st.stop()
    return auth_user

def require_admin() -> dict:
    auth_user = require_auth()
    if not auth_user.get("is_admin"):
        st.error("⛔ Administrator access is required.")
        st.switch_page("pages/forex.py")
        st.stop()
    return auth_user

def logout() -> None:
    # Logout clears only current Streamlit authentication state.
    # Persistent client/payment/Telegram records are intentionally untouched.
    st.session_state.pop("APEX_AUTH_USER", None)
    st.session_state["APEX_PUBLIC_VIEW"] = "login"
    st.switch_page("pages/login.py")
