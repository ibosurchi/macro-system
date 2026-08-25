from __future__ import annotations
import streamlit as st
import apex_core as core

core.bootstrap_runtime()
auth_user = core.render_vip_gate()
if auth_user and auth_user.get("is_authenticated"):
    st.switch_page("terminal.py")
