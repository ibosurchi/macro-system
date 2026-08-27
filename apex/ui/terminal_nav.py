"""Shared responsive authenticated navigation.

Desktop: persistent institutional left sidebar matching Image 1.
Mobile: compact right-side glass drawer (width: min(78vw, 320px)) with blurred left overlay.
"""
from __future__ import annotations

from html import escape
import streamlit as st
from .. import production_core as core
from .common import _shared_auth_css

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
    # Navigation CSS must exist even on the open-drawer rerun, where this
    # function intentionally stops page execution before render_top_header().
    _shared_auth_css()

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

    # ── 2. Mobile Drawer Open State ────────────────────────────────
    # Keyed Streamlit containers intentionally create stable `.st-key-*`
    # hooks.  The drawer width is applied to the REAL Streamlit block, not
    # to a decorative HTML wrapper (which cannot contain Streamlit widgets).
    if st.session_state.get("mobile_menu_open", False):
        with st.container(key="apex_mobile_menu_overlay"):
            if st.button("", key=f"m_overlay_bg_{active_page}", help="Close navigation menu"):
                st.session_state["mobile_menu_open"] = False
                st.rerun()

        with st.container(key="apex_mobile_menu_drawer"):
            col_logo, col_close = st.columns([0.80, 0.20], vertical_alignment="center")
            with col_logo:
                core.render_html("""<div class="apex-mobile-drawer-brand">
<div class="apex-brand-logo" aria-hidden="true"><svg viewBox="0 0 64 64"><defs><linearGradient id="apexNavLogo" x1="0" y1="1" x2="1" y2="0"><stop offset="0" stop-color="#18dbe6"/><stop offset="1" stop-color="#39f0f5"/></linearGradient></defs><path d="M8 54 31.8 8 56 54H45.5L31.9 27.5 18.4 54Z" fill="url(#apexNavLogo)"/><path d="M25.3 43.5h13.4l5.4 10.5H19.9Z" fill="#06131b" opacity=".88"/></svg></div>
<div class="apex-brand-copy"><div class="apex-sidebar-brand-title">APEXMACRO</div><div class="apex-sidebar-brand-subtitle">INTELLIGENCE DESK</div></div>
</div>""")
            with col_close:
                if st.button("✕", key=f"m_drawer_close_{active_page}", use_container_width=True, help="Close navigation menu"):
                    st.session_state["mobile_menu_open"] = False
                    st.rerun()

            with st.container(key="apex_mobile_menu_items"):
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

            core.render_html(f"""<div class="apex-mobile-account-card">
<div class="apex-mobile-profile-left">
<div class="apex-mobile-profile-avatar">{avatar_initials}</div>
<div class="apex-mobile-profile-copy"><div class="apex-mobile-profile-name">{user_name}</div><div class="apex-mobile-profile-role">{'Administrator' if is_admin else 'VIP Access'} • {now.strftime('%H:%M UTC')}</div></div>
</div><div class="apex-mobile-profile-chevron">›</div></div>""")
        st.stop()
        return True

    # ── 3. Mobile Top Header Bar (compact logo + hamburger) ────────────
    with st.container(key="apex_mobile_header"):
        m_col1, m_col2 = st.columns([0.78, 0.22], vertical_alignment="center")
        with m_col1:
            core.render_html("""<div class="apex-mobile-top-brand">
<div class="apex-brand-logo" aria-hidden="true"><svg viewBox="0 0 64 64"><defs><linearGradient id="apexTopLogo" x1="0" y1="1" x2="1" y2="0"><stop offset="0" stop-color="#18dbe6"/><stop offset="1" stop-color="#39f0f5"/></linearGradient></defs><path d="M8 54 31.8 8 56 54H45.5L31.9 27.5 18.4 54Z" fill="url(#apexTopLogo)"/><path d="M25.3 43.5h13.4l5.4 10.5H19.9Z" fill="#06131b" opacity=".88"/></svg></div>
<div class="apex-brand-copy"><div class="apex-sidebar-brand-title">APEXMACRO</div><div class="apex-sidebar-brand-subtitle">INTELLIGENCE DESK</div></div>
</div>""")
        with m_col2:
            if st.button("☰", key=f"btn_open_m_menu_{active_page}", use_container_width=True, help="Open navigation menu"):
                st.session_state["mobile_menu_open"] = True
                st.rerun()

    return False
