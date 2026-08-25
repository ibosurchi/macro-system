from __future__ import annotations
import streamlit as st

st.set_page_config(
    page_title="ApexMacro — Global Intelligence Desk",
    page_icon="🏛️",
    layout="wide",
    initial_sidebar_state="collapsed",
)

pages = [
    st.Page("pages/home.py", title="ApexMacro", default=True),
    st.Page("pages/login.py", title="Login", url_path="login"),
    st.Page("pages/vip.py", title="VIP Access", url_path="vip"),
    st.Page("pages/terminal.py", title="Terminal", url_path="terminal"),
    st.Page("pages/admin.py", title="Master Admin", url_path="admin"),
]

router = st.navigation(pages, position="hidden")
router.run()
