from __future__ import annotations
import streamlit as st

st.set_page_config(
    page_title="ApexMacro — Global Intelligence Desk",
    page_icon="🏛️",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# IMPORTANT:
# Register page sources with the SAME relative paths used by st.switch_page().
# Streamlit requires the target page to exactly match a page registered here.
pages = [
    st.Page("home.py", title="ApexMacro", default=True),
    st.Page("login.py", title="Login", url_path="login"),
    st.Page("vip.py", title="VIP Access", url_path="vip"),
    st.Page("terminal.py", title="Terminal", url_path="terminal"),
    st.Page("admin.py", title="Master Admin", url_path="admin"),
]

router = st.navigation(pages, position="hidden")
router.run()
