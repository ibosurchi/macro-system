import streamlit as st
from apex.bootstrap import prepare_page
from apex.production_core import render_public_checkout_page

prepare_page()
render_public_checkout_page()

if st.session_state.get("APEX_AUTH_USER", {}).get("is_authenticated"):
    st.switch_page("pages/dashboard.py")
