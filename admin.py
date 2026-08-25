from __future__ import annotations
import streamlit as st
import apex_core as core

core.bootstrap_runtime()
auth_user = st.session_state.get("APEX_AUTH_USER")
if not (auth_user and auth_user.get("is_authenticated")):
    st.switch_page("pages/login.py")
if not auth_user.get("is_admin", False):
    st.error("Master Admin access is restricted.")
    if st.button("← Back to Terminal", use_container_width=True):
        st.switch_page("pages/terminal.py")
    st.stop()

core.render_top_header(auth_user)
if st.button("← Back to Terminal", key="admin_back_terminal"):
    st.switch_page("pages/terminal.py")
core.render_admin_key_generator()
