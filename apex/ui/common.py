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
}

@media (max-width: 1023px) {
  header[data-testid="stHeader"] {
    display: flex !important;
    visibility: visible !important;
    background: transparent !important;
    height: 48px !important;
    z-index: 999990 !important;
  }
  
  [data-testid="stSidebarCollapsedControl"],
  [data-testid="collapsedControl"],
  button[data-testid="stSidebarCollapseButton"],
  button[aria-label="Expand sidebar"],
  button[aria-label="Open sidebar"],
  button[kind="header"] {
    display: flex !important;
    visibility: visible !important;
    position: fixed !important;
    top: 10px !important;
    left: 10px !important;
    z-index: 999999 !important;
    background: rgba(7, 25, 35, 0.94) !important;
    border: 1px solid rgba(39, 220, 231, 0.50) !important;
    border-radius: 9px !important;
    padding: 6px 10px !important;
    color: #27dce7 !important;
    box-shadow: 0 0 16px rgba(39, 220, 231, 0.25) !important;
    cursor: pointer !important;
  }

  [data-testid="stSidebarCollapsedControl"] svg,
  [data-testid="collapsedControl"] svg {
    fill: #27dce7 !important;
    stroke: #27dce7 !important;
    color: #27dce7 !important;
  }

  [data-testid="stSidebar"] {
    z-index: 1000000 !important;
    background: #03111a !important;
    box-shadow: 4px 0 30px rgba(0, 0, 0, 0.85) !important;
  }

  [data-testid="stSidebar"] button[aria-label="Close sidebar"],
  [data-testid="stSidebar"] [data-testid="stSidebarCollapseButton"] {
    display: flex !important;
    visibility: visible !important;
    color: #27dce7 !important;
  }
  
  .block-container {
    padding-top: 54px !important;
    padding-left: 12px !important;
    padding-right: 12px !important;
  }
}

[data-testid="stSidebar"] > div:first-child {
  padding: 16px 10px 18px !important;
}

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
}

@media (max-width: 768px) {
  .apex-auth-desktop-strip { display: none; }
  .apex-auth-mobile-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
    min-height: 44px;
    margin: 0 0 12px;
    padding: 7px 12px;
    border: 1px solid rgba(55, 150, 170, 0.18);
    border-radius: 11px;
    background: linear-gradient(145deg, rgba(6, 21, 30, 0.92), rgba(3, 13, 20, 0.97));
  }
  .apex-auth-mobile-left {
    display: flex;
    align-items: center;
    gap: 8px;
    min-width: 0;
  }
  .apex-auth-mobile-mark {
    font-weight: 950;
    font-size: 17px;
    color: #27dce7;
  }
  .apex-auth-mobile-title {
    font-size: 12.5px;
    font-weight: 800;
    color: #eef4f7;
    letter-spacing: 0.7px;
  }
  .apex-auth-mobile-role {
    font-size: 9px;
    color: #8fa1ad;
    border: 1px solid rgba(70, 145, 165, 0.18);
    padding: 3px 8px;
    border-radius: 999px;
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
            </div>
            <div class="apex-auth-mobile-head">
              <div class="apex-auth-mobile-left"><span class="apex-auth-mobile-mark">▲</span><span class="apex-auth-mobile-title">APEXMACRO</span></div>
              <span class="apex-auth-mobile-role">{role}</span>
            </div>'''
    )


def render_footer() -> None:
    core.render_html(f"""
    <div class="app-foot">
      <div>© 2026 ApexMacro • Institutional Macro Intelligence</div>
      <div><span class="live-dot"></span><span style="color:#00ffa3;font-weight:700;">Engine Active &nbsp; {core.get_current_time().strftime('%H:%M:%S')}</span></div>
    </div>
    """)
