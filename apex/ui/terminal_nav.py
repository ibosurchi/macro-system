"""Shared responsive authenticated navigation.

Desktop: persistent institutional left sidebar.
Mobile: Streamlit's native collapsed sidebar acts as the hamburger/drawer.
Only real existing routes are rendered.
"""
from __future__ import annotations

import streamlit as st
from .. import production_core as core

ROUTES = {
    "dashboard": ("⌂", "Dashboard", "pages/dashboard.py"),
    "forex": ("◉", "Forex", "pages/forex.py"),
    "gold": ("◆", "Gold", "pages/gold.py"),
    "oil": ("◔", "Oil", "pages/oil.py"),
    "nasdaq": ("▥", "Nasdaq-100", "pages/nasdaq.py"),
    "forecaster": ("▣", "Forecaster", "pages/forecaster.py"),
    "admin": ("♛", "Admin", "pages/admin.py"),
}


def render_terminal_nav(active_page: str, auth_user: dict | None = None) -> None:
    is_admin = bool(auth_user and auth_user.get("is_admin"))
    keys = ["dashboard", "forex", "gold", "oil", "nasdaq", "forecaster"]
    if is_admin:
        keys.append("admin")

    with st.sidebar:
        st.markdown(
            '''<div class="apex-auth-brand">
                 <div class="apex-auth-mark">A</div>
                 <div><div class="apex-auth-brand-name">APEXMACRO</div><div class="apex-auth-brand-sub">Intelligence Desk</div></div>
               </div><div class="apex-auth-side-sep"></div>''',
            unsafe_allow_html=True,
        )
        for key in keys:
            icon, label, path = ROUTES[key]
            if st.button(
                f"{icon}  {label}",
                key=f"terminal_side_{key}",
                use_container_width=True,
                type="primary" if active_page == key else "secondary",
            ):
                st.session_state["active_tab"] = label
                st.switch_page(path)

        now = core.get_current_time()
        st.markdown(
            f'''<div class="apex-auth-side-status">
                  <div class="apex-auth-side-label">Market Time</div>
                  <div class="apex-auth-side-clock">{now.strftime('%H:%M:%S')}</div>
                  <div class="apex-auth-side-date">{now.strftime('%d %b %Y')}</div>
                </div>''',
            unsafe_allow_html=True,
        )
