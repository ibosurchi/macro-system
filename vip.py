from __future__ import annotations
import streamlit as st
import apex_core as core

core.bootstrap_runtime()
auth_user = st.session_state.get("APEX_AUTH_USER")
if auth_user and auth_user.get("is_authenticated"):
    st.switch_page("terminal.py")
core.render_public_checkout_page()
auth_user = st.session_state.get("APEX_AUTH_USER")
if auth_user and auth_user.get("is_authenticated"):
    st.switch_page("terminal.py")
