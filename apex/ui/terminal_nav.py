"""Shared responsive authenticated navigation.

Desktop: persistent institutional left sidebar matching Image 1.
Mobile: right-side glassmorphism drawer sliding in with blurred/dimmed overlay matching Image 4 & 5.
All routes, permissions, and engine strategies are 100% preserved.
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

    # ── 2. Mobile Drawer Open State (Overlay + Right-Slide Glass Drawer)
    if st.session_state.get("apex_mobile_menu_open", False):
        # Full-page dimmed and blurred background overlay
        core.render_html('<div class="apex-mobile-menu-overlay"></div>')

        # Backdrop click-detector button to close when tapping outside
        if st.button("✕", key=f"m_overlay_bg_{active_page}", help="Close navigation menu"):
            st.session_state["apex_mobile_menu_open"] = False
            st.rerun()

        # Right-side glass drawer
        st.markdown('<div class="apex-mobile-menu-drawer">', unsafe_allow_html=True)

        # Drawer Header with Brand Logo & Close 'X'
        d_col1, d_col2 = st.columns([0.80, 0.20])
        with d_col1:
            core.render_html("""<div class="apex-mobile-drawer-brand">
<div class="apex-sidebar-logo-icon">▲</div>
<div>
<div class="apex-sidebar-brand-title">APEXMACRO</div>
<div class="apex-sidebar-brand-subtitle">INTELLIGENCE DESK</div>
</div>
</div>""")
        with d_col2:
            if st.button("✕", key=f"m_drawer_close_btn_{active_page}", help="Close navigation menu"):
                st.session_state["apex_mobile_menu_open"] = False
                st.rerun()

        # Drawer Navigation Menu List
        st.markdown('<div class="apex-mobile-menu-list">', unsafe_allow_html=True)
        for key in keys:
            icon, label, path = ROUTES[key]
            is_active = (active_page == key)
            if st.button(
                f"{icon}    {label}",
                key=f"m_drawer_item_{key}_{active_page}",
                use_container_width=True,
                type="primary" if is_active else "secondary",
            ):
                st.session_state["apex_mobile_menu_open"] = False
                st.session_state["active_tab"] = label
                st.switch_page(path)
        st.markdown('</div>', unsafe_allow_html=True)

        # Drawer Bottom Account / User Card
        core.render_html(f"""<div class="apex-mobile-account-card">
<div class="apex-mobile-account-left">
<div class="apex-mobile-account-avatar">{avatar_initials}</div>
<div>
<div class="apex-mobile-account-name">{user_name}</div>
<div class="apex-mobile-account-plan">{'Administrator' if is_admin else 'VIP Access'} • {now.strftime('%H:%M UTC')}</div>
</div>
</div>
<div class="apex-mobile-account-chevron">›</div>
</div>
</div>""")
        st.stop()
        return True

    # ── 3. Mobile Header Bar (Closed State: Brand Logo + Hamburger Button)
    st.markdown('<div class="apex-mobile-header-container">', unsafe_allow_html=True)
    m_col1, m_col2 = st.columns([0.80, 0.20])
    with m_col1:
        core.render_html("""<div class="apex-mobile-drawer-brand">
<div class="apex-sidebar-logo-icon">▲</div>
<div>
<div class="apex-sidebar-brand-title">APEXMACRO</div>
<div class="apex-sidebar-brand-subtitle">INTELLIGENCE DESK</div>
</div>
</div>""")
    with m_col2:
        st.markdown('<div class="apex-mobile-menu-trigger">', unsafe_allow_html=True)
        if st.button("☰", key=f"btn_open_m_menu_{active_page}", help="Open navigation menu"):
            st.session_state["apex_mobile_menu_open"] = True
            st.rerun()
        st.markdown('</div>', unsafe_allow_html=True)
    st.markdown('</div>', unsafe_allow_html=True)

    return False
