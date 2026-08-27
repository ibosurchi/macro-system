"""Shared authenticated terminal chrome.

Presentation only: no strategy, data, alert, auth or worker logic lives here.
"""
from __future__ import annotations

from html import escape
import streamlit as st
from .. import production_core as core


def _shared_auth_css() -> None:
    core.render_html(r'''
<style>
:root {
  --apex-auth-bg: #02080d;
  --apex-auth-panel: rgba(6,21,30,.96);
  --apex-auth-border: rgba(60,155,175,.20);
  --apex-auth-cyan: #27dce7;
  --apex-auth-text: #f2f6f8;
  --apex-auth-muted: #94a2b0;
}

/* Hide default streamlit headers and deploy buttons */
header[data-testid="stHeader"],
.stDeployButton,
[data-testid="stHeaderActionElements"],
[data-testid="stToolbar"] {
  display: none !important;
  visibility: hidden !important;
  height: 0 !important;
}

[data-testid="stAppViewContainer"] {
  background: radial-gradient(circle at 10% 0%, rgba(0,220,230,.03), transparent 28%), var(--apex-auth-bg) !important;
}

[data-testid="stSidebar"] {
  background: linear-gradient(180deg, #03111a 0%, #020a10 100%) !important;
  border-right: 1px solid rgba(39, 220, 231, 0.22) !important;
  box-shadow: 2px 0 24px rgba(0, 0, 0, 0.5) !important;
}

@media (min-width: 1024px) {
  [data-testid="stSidebar"] {
    min-width: 230px !important;
    max-width: 230px !important;
    width: 230px !important;
    transform: none !important;
    visibility: visible !important;
    display: block !important;
  }
  [data-testid="stSidebarCollapsedControl"],
  [data-testid="collapsedControl"] {
    display: none !important;
    visibility: hidden !important;
  }
  .apex-mobile-header-bar-container {
    display: none !important;
  }
}

@media (max-width: 1023px) {
  [data-testid="stSidebar"] {
    display: none !important;
  }
  [data-testid="stSidebarCollapsedControl"],
  [data-testid="collapsedControl"] {
    display: none !important;
  }
}

[data-testid="stSidebar"] > div:first-child {
  padding: 16px 10px 18px !important;
}

/* Sidebar Nav Button Styles */
[data-testid="stSidebar"] [data-testid="stButton"] button {
  min-height: 44px !important;
  border-radius: 9px !important;
  text-align: left !important;
  justify-content: flex-start !important;
  font-size: 13.5px !important;
  font-weight: 550 !important;
  box-shadow: none !important;
  margin: 2px 0 !important;
  letter-spacing: 0.15px;
  transition: all 0.15s ease !important;
}

/* Active Nav Item */
[data-testid="stSidebar"] [data-testid="stButton"] button[kind="primary"] {
  background: linear-gradient(90deg, rgba(20, 210, 225, 0.14) 0%, rgba(20, 210, 225, 0.03) 100%) !important;
  border: 1px solid rgba(39, 220, 231, 0.40) !important;
  color: #27dce7 !important;
  box-shadow: inset 0 0 16px rgba(39, 220, 231, 0.06), 0 0 12px rgba(39, 220, 231, 0.08) !important;
}

/* Inactive Nav Items */
[data-testid="stSidebar"] [data-testid="stButton"] button[kind="secondary"] {
  background: transparent !important;
  border: 1px solid transparent !important;
  color: #a4b3be !important;
}

[data-testid="stSidebar"] [data-testid="stButton"] button[kind="secondary"]:hover {
  background: rgba(255, 255, 255, 0.03) !important;
  border-color: rgba(70, 145, 165, 0.25) !important;
  color: #f0f4f8 !important;
}

/* ── MOBILE HEADER BAR & HAMBURGER (IMAGE 1 & 2) ────────── */
.apex-mobile-header-bar-container {
  display: block;
  margin: 0 0 12px;
  padding: 8px 4px 10px;
  border-bottom: 1px solid rgba(70, 145, 165, 0.18);
}

.apex-mobile-header-bar-container button {
  min-height: 40px !important;
  border-radius: 10px !important;
  border: 1px solid rgba(39, 220, 231, 0.45) !important;
  background: rgba(7, 25, 35, 0.85) !important;
  color: #27dce7 !important;
  font-size: 18px !important;
  font-weight: 800 !important;
  box-shadow: 0 0 12px rgba(39, 220, 231, 0.18) !important;
}

/* ── MOBILE FULL SCREEN DRAWER (IMAGE 4 & 5) ─────────────── */
.apex-mobile-drawer-wrap {
  padding: 10px 4px 30px;
}

.apex-mobile-drawer-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 12px;
  padding-bottom: 10px;
  border-bottom: 1px solid rgba(70, 145, 165, 0.18);
}

/* Large, one-handed friendly tap targets in mobile menu */
.apex-mobile-drawer-wrap [data-testid="stButton"] button {
  min-height: 48px !important;
  border-radius: 12px !important;
  text-align: left !important;
  justify-content: flex-start !important;
  font-size: 14.5px !important;
  font-weight: 650 !important;
  margin: 6px 0 !important;
  letter-spacing: 0.2px;
}

.apex-mobile-drawer-wrap [data-testid="stButton"] button[kind="primary"] {
  background: linear-gradient(90deg, rgba(20, 210, 225, 0.20) 0%, rgba(20, 210, 225, 0.05) 100%) !important;
  border: 1px solid rgba(39, 220, 231, 0.55) !important;
  color: #27dce7 !important;
  box-shadow: inset 0 0 18px rgba(39, 220, 231, 0.08), 0 0 14px rgba(39, 220, 231, 0.12) !important;
}

.apex-mobile-drawer-wrap [data-testid="stButton"] button[kind="secondary"] {
  background: rgba(7, 25, 35, 0.40) !important;
  border: 1px solid rgba(70, 145, 165, 0.18) !important;
  color: #c8d5de !important;
}

.apex-mobile-drawer-wrap [data-testid="stButton"] button[kind="secondary"]:hover {
  border-color: rgba(39, 220, 231, 0.35) !important;
  color: #f5f8fa !important;
}

.apex-mobile-profile-card {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 16px;
  border-radius: 14px;
  border: 1px solid rgba(70, 145, 165, 0.25);
  background: rgba(7, 25, 35, 0.75);
  margin-top: 30px;
}
.apex-mobile-profile-left {
  display: flex;
  align-items: center;
  gap: 12px;
}
.apex-mobile-profile-avatar {
  width: 38px;
  height: 38px;
  border-radius: 50%;
  border: 1px solid rgba(39, 220, 231, 0.40);
  background: rgba(39, 220, 231, 0.10);
  color: #27dce7;
  font-size: 13px;
  font-weight: 800;
  display: grid;
  place-items: center;
}
.apex-mobile-profile-name {
  font-size: 14px;
  font-weight: 800;
  color: #f3f7f9;
}
.apex-mobile-profile-role {
  font-size: 11px;
  color: #27dce7;
  margin-top: 1px;
}

.apex-sidebar-brand {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 4px 6px 18px;
}
.apex-sidebar-logo-icon {
  width: 38px;
  height: 38px;
  border-radius: 9px;
  border: 1px solid rgba(39, 220, 231, 0.35);
  background: rgba(39, 220, 231, 0.06);
  display: grid;
  place-items: center;
  color: #27dce7;
  font-size: 22px;
  font-weight: 950;
  font-style: italic;
  box-shadow: inset 0 0 14px rgba(39, 220, 231, 0.08);
  flex-shrink: 0;
}
.apex-sidebar-brand-title {
  font-size: 16px;
  font-weight: 850;
  letter-spacing: 1.8px;
  color: #f5f7f9;
  line-height: 1.1;
}
.apex-sidebar-brand-subtitle {
  font-size: 10px;
  color: #27dce7;
  margin-top: 2px;
  letter-spacing: 0.3px;
}
.apex-sidebar-sep {
  height: 1px;
  background: rgba(80, 145, 165, 0.14);
  margin: 0 0 12px;
}
.apex-sidebar-bottom {
  margin-top: 24px;
  padding: 12px 14px;
  border: 1px solid rgba(70, 145, 165, 0.18);
  border-radius: 11px;
  background: rgba(7, 25, 35, 0.50);
}
.apex-side-meta {
  font-size: 10px;
  letter-spacing: 0.4px;
  color: #748895;
  text-transform: uppercase;
  display: flex;
  align-items: center;
  gap: 5px;
}
.apex-side-clock {
  font-size: 20px;
  font-weight: 800;
  color: #f3f6f8;
  margin-top: 4px;
  font-variant-numeric: tabular-nums;
  letter-spacing: 0.5px;
}
.apex-side-date {
  font-size: 9.5px;
  color: #899aa7;
  margin-top: 2px;
}
.apex-sidebar-mode-toggle {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 8px 12px;
  border-radius: 8px;
  border: 1px solid rgba(70, 145, 165, 0.15);
  background: rgba(7, 25, 35, 0.35);
  margin-top: 8px;
  font-size: 11px;
  color: #94a2b0;
}

.apex-auth-mobile-head { display: none; }
.apex-auth-desktop-strip {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  margin: 0 0 10px;
}
.apex-auth-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-height: 32px;
  padding: 0 12px;
  border: 1px solid var(--apex-auth-border);
  border-radius: 999px;
  background: rgba(7, 25, 35, 0.72);
  color: #c4d0d7;
  font-size: 11.5px;
}
.apex-auth-chip.admin {
  border-color: rgba(181, 78, 227, 0.35);
  color: #d4a0f0;
  background: rgba(181, 78, 227, 0.06);
}
.apex-auth-chip.vip {
  border-color: rgba(255, 178, 26, 0.30);
  color: #f0cc80;
  background: rgba(255, 178, 26, 0.05);
}

.block-container {
  max-width: 1750px !important;
  padding: 22px 28px 30px !important;
}

@media (max-width: 768px) {
  .block-container {
    padding: 12px 10px 24px !important;
  }
}
</style>''')


def render_top_header(auth_user: dict | None = None) -> None:
    """Render compact authenticated chrome instead of the legacy oversized terminal header."""
    _shared_auth_css()
    user = auth_user or {}
    is_admin = bool(user.get("is_admin"))
    role = "Admin" if is_admin else "VIP"
    name = escape(str(user.get("user_name") or user.get("username") or role))
    now = core.get_current_time()
    core.render_html(
        f'''<div class="apex-auth-desktop-strip">
              <div class="apex-auth-chip {'admin' if is_admin else 'vip'}">{'♛' if is_admin else '👑'} {role}</div>
              <div class="apex-auth-chip">{name}</div>
              <div class="apex-auth-chip">◷ {now.strftime('%H:%M')}</div>
            </div>'''
    )


def render_footer() -> None:
    core.render_html(f"""
    <div class="app-foot">
      <div>© 2026 ApexMacro • Institutional Macro Intelligence</div>
      <div><span class="live-dot"></span><span style="color:#00ffa3;font-weight:700;">Engine Active &nbsp; {core.get_current_time().strftime('%H:%M:%S')}</span></div>
    </div>
    """)
