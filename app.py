from __future__ import annotations
from pathlib import Path
import streamlit as st

st.set_page_config(
    page_title="ApexMacro — Global Intelligence Desk",
    page_icon="🏛️",
    layout="wide",
    initial_sidebar_state="collapsed",
)

ROOT = Path(__file__).resolve().parent

pages = [
    st.Page(str(ROOT / "home.py"), title="ApexMacro", default=True),
    st.Page(str(ROOT / "login.py"), title="Login", url_path="login"),
    st.Page(str(ROOT / "vip.py"), title="VIP Access", url_path="vip"),
    st.Page(str(ROOT / "terminal.py"), title="Terminal", url_path="terminal"),
    st.Page(str(ROOT / "admin.py"), title="Master Admin", url_path="admin"),
]

router = st.navigation(pages, position="hidden")
router.run()
