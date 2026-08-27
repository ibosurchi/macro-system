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

/* ── DESKTOP PERSISTENT SIDEBAR (>= 1024px) ─────────────── */
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
  .apex-mobile-header-container {
    display: none !important;
  }
  .apex-mobile-menu-overlay,
  .apex-mobile-menu-drawer,
  button[key*="m_overlay_bg"] {
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

[data-testid="stSidebar"] [data-testid="stButton"] button[kind="primary"] {
  background: linear-gradient(90deg, rgba(20, 210, 225, 0.14) 0%, rgba(20, 210, 225, 0.03) 100%) !important;
  border: 1px solid rgba(39, 220, 231, 0.40) !important;
  color: #27dce7 !important;
  box-shadow: inset 0 0 16px rgba(39, 220, 231, 0.06), 0 0 12px rgba(39, 220, 231, 0.08) !important;
}

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

/* ── 1. CLOSED MOBILE HEADER (IMAGE 1, 2, 3) ─────────────── */
.apex-mobile-header-container {
  display: block;
  margin: 0 0 12px;
  padding: 6px 0 10px;
  border-bottom: 1px solid rgba(70, 145, 165, 0.18);
}

.apex-mobile-menu-trigger button {
  width: 44px !important;
  height: 44px !important;
  min-height: 44px !important;
  display: flex !important;
  align-items: center !important;
  justify-content: center !important;
  border-radius: 10px !important;
  background: rgba(6, 22, 31, 0.72) !important;
  border: 1px solid rgba(30, 210, 220, 0.35) !important;
  color: #F1F5F7 !important;
  font-size: 20px !important;
  font-weight: 700 !important;
  box-shadow: 0 0 12px rgba(39, 220, 231, 0.15) !important;
  cursor: pointer !important;
  transition: transform 180ms ease, opacity 180ms ease !important;
}

.apex-mobile-drawer-brand {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 0;
}

/* ── 4. FULL PAGE OVERLAY (BLUR & DIM) ──────────────────── */
.apex-mobile-menu-overlay {
  position: fixed !important;
  inset: 0 !important;
  z-index: 99980 !important;
  background: rgba(1, 7, 12, 0.58) !important;
  backdrop-filter: blur(8px) !important;
  -webkit-backdrop-filter: blur(8px) !important;
  pointer-events: none !important;
}

/* Backdrop button for tapping outside drawer to close */
button[key*="m_overlay_bg"] {
  position: fixed !important;
  inset: 0 !important;
  width: 100vw !important;
  height: 100vh !important;
  background: transparent !important;
  border: none !important;
  z-index: 99985 !important;
  color: transparent !important;
  cursor: pointer !important;
}

/* ── 6. RIGHT-SIDE SLIDE-IN GLASS DRAWER ────────────────── */
.apex-mobile-menu-drawer {
  position: fixed !important;
  top: 0 !important;
  right: 0 !important;
  bottom: 0 !important;
  width: min(88vw, 360px) !important;
  z-index: 99999 !important;
  display: flex !important;
  flex-direction: column !important;
  padding: 18px 14px !important;
  box-sizing: border-box !important;
  background: linear-gradient(160deg, rgba(5, 22, 32, 0.98), rgba(2, 12, 19, 0.995)) !important;
  border-left: 1px solid rgba(28, 205, 220, 0.34) !important;
  box-shadow: -18px 0 55px rgba(0, 0, 0, 0.42) !important;
  overflow-y: auto !important;
  -webkit-overflow-scrolling: touch !important;
  transform: translateX(0) !important;
  animation: apexSlideFromRight 220ms cubic-bezier(0.2, 0.7, 0.2, 1) forwards !important;
}

@keyframes apexSlideFromRight {
  from { transform: translateX(100%); }
  to { transform: translateX(0); }
}

@media (prefers-reduced-motion: reduce) {
  .apex-mobile-menu-drawer {
    animation: none !important;
  }
}

/* ── 9. CLOSE X BUTTON IN DRAWER ────────────────────────── */
.apex-mobile-menu-drawer button[key*="m_drawer_close"] {
  width: 42px !important;
  height: 42px !important;
  min-height: 42px !important;
  display: flex !important;
  align-items: center !important;
  justify-content: center !important;
  border-radius: 10px !important;
  border: 1px solid rgba(25, 205, 220, 0.25) !important;
  background: rgba(6, 23, 33, 0.72) !important;
  color: #21DFE8 !important;
  font-size: 24px !important;
  line-height: 1 !important;
  padding: 0 !important;
  cursor: pointer !important;
}

/* ── 11 & 12. DRAWER MENU ITEMS ─────────────────────────── */
.apex-mobile-menu-list {
  display: flex !important;
  flex-direction: column !important;
  margin-top: 16px !important;
  margin-bottom: 20px !important;
}

.apex-mobile-menu-list button {
  min-height: 48px !important;
  display: flex !important;
  align-items: center !important;
  justify-content: flex-start !important;
  gap: 13px !important;
  padding: 10px 12px !important;
  margin-bottom: 5px !important;
  border-radius: 9px !important;
  font-size: 14px !important;
  font-weight: 550 !important;
  text-align: left !important;
  letter-spacing: 0.2px !important;
  border: 1px solid transparent !important;
  transition: all 0.15s ease !important;
}

.apex-mobile-menu-list button[kind="primary"] {
  color: #2BE3EB !important;
  background: linear-gradient(90deg, rgba(15, 205, 218, 0.15), rgba(15, 205, 218, 0.04)) !important;
  border: 1px solid rgba(25, 210, 220, 0.42) !important;
  box-shadow: inset 0 0 16px rgba(39, 220, 231, 0.06), 0 0 12px rgba(39, 220, 231, 0.10) !important;
}

.apex-mobile-menu-list button[kind="secondary"] {
  background: rgba(6, 23, 33, 0.40) !important;
  border: 1px solid rgba(70, 145, 165, 0.16) !important;
  color: #E8EFF3 !important;
}

.apex-mobile-menu-list button[kind="secondary"]:hover {
  background: rgba(255, 255, 255, 0.04) !important;
  border-color: rgba(39, 220, 231, 0.35) !important;
  color: #ffffff !important;
}

/* ── 16. BOTTOM ACCOUNT / USER CARD ─────────────────────── */
.apex-mobile-account-card {
  margin-top: auto !important;
  padding: 12px 14px !important;
  display: flex !important;
  align-items: center !important;
  justify-content: space-between !important;
  border-radius: 12px !important;
  background: rgba(7, 28, 39, 0.72) !important;
  border: 1px solid rgba(24, 205, 220, 0.28) !important;
}

.apex-mobile-account-left {
  display: flex !important;
  align-items: center !important;
  gap: 12px !important;
}

.apex-mobile-account-avatar {
  width: 36px !important;
  height: 36px !important;
  border-radius: 50% !important;
  border: 1px solid rgba(39, 220, 231, 0.45) !important;
  background: rgba(39, 220, 231, 0.12) !important;
  color: #27dce7 !important;
  font-size: 13px !important;
  font-weight: 800 !important;
  display: grid !important;
  place-items: center !important;
}

.apex-mobile-account-name {
  font-size: 13.5px !important;
  font-weight: 750 !important;
  color: #f1f5f7 !important;
  line-height: 1.2 !important;
}

.apex-mobile-account-plan {
  font-size: 10.5px !important;
  color: #25DFE8 !important;
  margin-top: 1px !important;
}

.apex-mobile-account-chevron {
  color: #25DFE8 !important;
  font-size: 18px !important;
  font-weight: 700 !important;
}

/* ── BRAND LOGO & SHARED CHIPS ──────────────────────────── */
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
    """Render compact authenticated chrome."""
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
