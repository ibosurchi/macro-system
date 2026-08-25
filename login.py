import streamlit as st
from apex.bootstrap import prepare_page
from apex.auth import restore_auth_user
from apex.production_core import render_vip_gate

prepare_page()

existing = restore_auth_user()
if existing and existing.get("is_authenticated"):
    st.switch_page("pages/dashboard.py")

auth_user = render_vip_gate()
if auth_user and auth_user.get("is_authenticated"):
    st.switch_page("pages/dashboard.py")
