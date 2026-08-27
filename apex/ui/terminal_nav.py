"""Shared responsive authenticated navigation.

Desktop: persistent institutional left sidebar matching Image 1.
Mobile: sleek interactive glassmorphism drawer matching Image 4 & 5.
"""
from __future__ import annotations

from html import escape
import streamlit as st
from .. import production_core as core

ROUTES = {
    "dashboard":  ("⌂",  "Dashboard",            "pages/dashboard.py"),
    "forex":      ("💱", "Forex Terminal",       "pages/forex.py"),
    "gold":       ("🥇", "Gold Analysis",        "pages/gold.py"),
    "oil":        ("🛢️", "Oil Analysis",         "pages/oil.py"),
    "nasdaq":     ("📊", "Nasdaq Analysis",      "pages/nasdaq.py"),
    "forecaster": ("🎯", "Catalyst Forecaster",  "pages/forecaster.py"),
    "admin":      ("👑", "Admin Terminal",       "pages/admin.py"),
}


def render_terminal_nav(active_page: str, auth_user: dict | None = None) -> bool:
    """Render navigation for authenticated pages.
    Returns True if mobile menu is open and rendered, so the caller stops further page execution.
    """
    is_admin = bool(auth_user and auth_user.get("is_admin"))
    role = "Admin" if is_admin else "VIP"
    user_name = escape(str((auth_user or {}).get("user_name") or (auth_user or {}).get("username") or role))
    avatar_initials = user_name[:2].upper() if user_name else "AD"
    now = core.get_current_time()

    keys = ["dashboard", "forex", "gold", "oil", "nasdaq", "forecaster"]
    if is_admin:
        keys.append("admin")

    # ── 1. Desktop Persistent Sidebar (min-width: 1024px) ───────────────
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

        core.render_html(f"""<div class="apex-sidebar-bottom">
<div class="apex-side-meta"><span>◷</span> Market Time (UTC)</div>
<div class="apex-side-clock">{now.strftime('%H:%M:%S')}</div>
<div class="apex-side-date">{now.strftime('%d %b %Y, %a')}</div>
</div>
<div class="apex-sidebar-mode-toggle">
<span>🌙 Dark Mode</span>
<span style="font-size:9px;">⌵</span>
</div>""")

    # ── 2. Mobile Drawer Open State (Full Glass Menu) ──────────────────
    if st.session_state.get("mobile_menu_open", False):
        st.markdown('<div class="apex-mobile-drawer-wrap">', unsafe_allow_html=True)
        st.markdown('<div class="apex-mobile-drawer-head">', unsafe_allow_html=True)

        col_logo, col_close = st.columns([0.82, 0.18])
        with col_logo:
            core.render_html("""<div class="apex-sidebar-brand" style="padding:0;margin:0;">
<div class="apex-sidebar-logo-icon">▲</div>
<div>
<div class="apex-sidebar-brand-title">APEXMACRO</div>
<div class="apex-sidebar-brand-subtitle">INTELLIGENCE DESK</div>
</div>
</div>""")
        with col_close:
            if st.button("☰", key=f"m_drawer_close_{active_page}", use_container_width=True, help="Close navigation menu"):
                st.session_state["mobile_menu_open"] = False
                st.rerun()

        st.markdown('</div>', unsafe_allow_html=True)

        # Large visible menu buttons
        st.markdown("<div class='apex-mobile-menu-list'>", unsafe_allow_html=True)
        for key in keys:
            icon, label, path = ROUTES[key]
            is_active = (active_page == key)
            if st.button(
                f"{icon}   {label}",
                key=f"m_drawer_item_{key}_{active_page}",
                use_container_width=True,
                type="primary" if is_active else "secondary",
            ):
                st.session_state["mobile_menu_open"] = False
                st.session_state["active_tab"] = label
                st.switch_page(path)
        st.markdown("</div>", unsafe_allow_html=True)

        # Bottom Profile Card
        core.render_html(f"""<div class="apex-mobile-profile-card">
<div class="apex-mobile-profile-left">
<div class="apex-mobile-profile-avatar">{avatar_initials}</div>
<div>
<div class="apex-mobile-profile-name">{user_name}</div>
<div class="apex-mobile-profile-role">{'Administrator' if is_admin else 'VIP Access'} • {now.strftime('%H:%M UTC')}</div>
</div>
</div>
<div style="color:#27dce7;font-size:18px;font-weight:700;">›</div>
</div>
</div>""")
        st.stop()
        return True

    # ── 3. Mobile Top Header Bar (Closed State) ───────────────────────
    st.markdown('<div class="apex-mobile-header-bar-container">', unsafe_allow_html=True)
    m_col1, m_col2 = st.columns([0.82, 0.18])
    with m_col1:
        core.render_html("""<div class="apex-sidebar-brand" style="padding:0;margin:0;">
<div class="apex-sidebar-logo-icon">▲</div>
<div>
<div class="apex-sidebar-brand-title">APEXMACRO</div>
<div class="apex-sidebar-brand-subtitle">INTELLIGENCE DESK</div>
</div>
</div>""")
    with m_col2:
        if st.button("☰", key=f"btn_open_m_menu_{active_page}", use_container_width=True, help="Open navigation menu"):
            st.session_state["mobile_menu_open"] = True
            st.rerun()
    st.markdown('</div>', unsafe_allow_html=True)

    return False
