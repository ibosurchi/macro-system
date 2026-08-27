"""Shared responsive authenticated navigation.

Desktop: persistent institutional left sidebar.
Mobile: sleek horizontal swipeable navigation tab-bar.
Only real existing routes are rendered.
"""
from __future__ import annotations

from html import escape
import streamlit as st
from .. import production_core as core

ROUTES = {
    "dashboard": ("⌂", "Dashboard", "pages/dashboard.py"),
    "forex": ("💱", "Forex", "pages/forex.py"),
    "gold": ("🥇", "Gold", "pages/gold.py"),
    "oil": ("🛢️", "Oil", "pages/oil.py"),
    "nasdaq": ("📊", "Nasdaq-100", "pages/nasdaq.py"),
    "forecaster": ("🎯", "Forecaster", "pages/forecaster.py"),
    "admin": ("👑", "Admin", "pages/admin.py"),
}


def render_terminal_nav(active_page: str, auth_user: dict | None = None) -> None:
    is_admin = bool(auth_user and auth_user.get("is_admin"))
    keys = ["dashboard", "forex", "gold", "oil", "nasdaq", "forecaster"]
    if is_admin:
        keys.append("admin")

    # 1. Desktop Persistent Sidebar
    with st.sidebar:
        core.render_html("""<div class="apex-sidebar-brand">
<div class="apex-sidebar-logo-icon">▲</div>
<div>
<div class="apex-sidebar-brand-title">APEXMACRO</div>
<div class="apex-sidebar-brand-subtitle">Intelligence Desk</div>
</div>
</div>
<div class="apex-sidebar-sep"></div>""")

        for key in keys:
            icon, label, path = ROUTES[key]
            is_active = (active_page == key)
            if st.button(
                f"{icon}  {label}",
                key=f"terminal_side_{key}_{active_page}",
                use_container_width=True,
                type="primary" if is_active else "secondary",
            ):
                st.session_state["active_tab"] = label
                st.switch_page(path)

        now = core.get_current_time()
        core.render_html(f"""<div class="apex-sidebar-bottom">
<div class="apex-side-meta"><span>◷</span> Market Time (UTC)</div>
<div class="apex-side-clock">{now.strftime('%H:%M:%S')}</div>
<div class="apex-side-date">{now.strftime('%d %b %Y, %a')}</div>
</div>
<div class="apex-sidebar-mode-toggle">
<span>🌙 Dark Mode</span>
<span style="font-size:9px;">⌵</span>
</div>""")

    # 2. Mobile Responsive Horizontal Navigation Bar
    st.markdown('<div class="apex-mobile-nav-container">', unsafe_allow_html=True)
    cols = st.columns(len(keys), gap="small")
    for i, key in enumerate(keys):
        icon, label, path = ROUTES[key]
        is_active = (active_page == key)
        with cols[i]:
            if st.button(
                f"{icon} {label}",
                key=f"m_nav_{key}_{active_page}",
                use_container_width=True,
                type="primary" if is_active else "secondary",
            ):
                st.session_state["active_tab"] = label
                st.switch_page(path)
    st.markdown('</div>', unsafe_allow_html=True)
