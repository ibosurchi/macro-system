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

*, *::before, *::after {
  box-sizing: border-box !important;
}

html, body {
  overflow-x: clip !important;
  max-width: 100vw !important;
  width: 100% !important;
  position: relative !important;
  touch-action: pan-y !important;
  margin: 0 !important;
  padding: 0 !important;
}

[data-testid="stAppViewContainer"],
[data-testid="stAppViewBlockContainer"],
.stApp,
.main,
.block-container {
  overflow-x: clip !important;
  max-width: 100vw !important;
  width: 100% !important;
  box-sizing: border-box !important;
}

/* Hide default streamlit headers and deploy buttons completely */
header[data-testid="stHeader"],
[data-testid="stHeader"],
.stDeployButton,
[data-testid="stHeaderActionElements"],
[data-testid="stToolbar"] {
  display: none !important;
  visibility: hidden !important;
  height: 0 !important;
  min-height: 0 !important;
  padding: 0 !important;
  margin: 0 !important;
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
  .apex-mobile-header-bar-container,
  .apex-mobile-menu-overlay,
  .apex-mobile-menu-drawer,
  .st-key-apex_mobile_header,
  .st-key-apex_mobile_menu_overlay,
  .st-key-apex_mobile_menu_drawer {
    display: none !important;
  }
  .apex-auth-desktop-strip {
    display: flex !important;
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
  .apex-auth-desktop-strip {
    display: none !important;
    visibility: hidden !important;
    height: 0 !important;
    margin: 0 !important;
    padding: 0 !important;
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

/* ── APEX COMPACT MOBILE NAV ─────────────────────────────── */
.st-key-apex_mobile_header { display:block; margin:0 0 12px !important; padding:0 8px 0 2px !important; box-sizing:border-box !important; }
.st-key-apex_mobile_header [data-testid="stHorizontalBlock"]{display:flex!important;flex-wrap:nowrap!important;align-items:center!important;justify-content:space-between!important;gap:8px!important;width:100%!important;}
.st-key-apex_mobile_header [data-testid="column"]:first-child{flex:1 1 auto!important;min-width:0!important;}
.st-key-apex_mobile_header [data-testid="column"]:last-child{flex:0 0 46px!important;min-width:46px!important;max-width:46px!important;display:flex!important;justify-content:flex-end!important;padding-right:6px!important;}
.st-key-apex_mobile_header button{width:40px!important;height:40px!important;min-height:40px!important;padding:0!important;border-radius:10px!important;border:1px solid rgba(39,220,231,.45)!important;background:rgba(6,22,31,.90)!important;color:#27dce7!important;font-size:19px!important;box-shadow:0 0 14px rgba(39,220,231,.15)!important;margin-right:6px!important;transition:transform 150ms ease,background 180ms ease!important;}
.st-key-apex_mobile_header button:active{transform:scale(.92)!important;}
.apex-mobile-top-brand,.apex-mobile-drawer-brand{display:flex;align-items:center;gap:10px;min-width:0;}
.apex-brand-logo{width:42px;height:42px;min-width:42px;border-radius:11px;border:1px solid rgba(39,220,231,.36);background:linear-gradient(145deg,rgba(11,37,48,.96),rgba(4,18,26,.98));display:grid;place-items:center;box-shadow:inset 0 0 16px rgba(39,220,231,.07),0 0 14px rgba(39,220,231,.06);}
.apex-brand-logo svg{width:28px;height:28px;display:block;}
.apex-brand-copy{min-width:0;}
.apex-mobile-top-brand .apex-sidebar-brand-title,.apex-mobile-drawer-brand .apex-sidebar-brand-title{font-size:15px;letter-spacing:1.25px;white-space:nowrap;}
.apex-mobile-top-brand .apex-sidebar-brand-subtitle,.apex-mobile-drawer-brand .apex-sidebar-brand-subtitle{font-size:8.5px;letter-spacing:.55px;white-space:nowrap;}

/* Real full-screen overlay. The empty Streamlit block itself is fixed. */
.st-key-apex_mobile_menu_overlay{position:fixed!important;inset:0!important;z-index:99980!important;background:rgba(1,7,12,.52)!important;backdrop-filter:blur(7px)!important;-webkit-backdrop-filter:blur(7px)!important;animation:apexOverlayIn 220ms ease both!important;}
.st-key-apex_mobile_menu_overlay [data-testid="stButton"]{position:absolute!important;inset:0!important;margin:0!important;}
.st-key-apex_mobile_menu_overlay button{position:absolute!important;inset:0!important;width:100%!important;height:100%!important;min-height:100%!important;border:0!important;border-radius:0!important;background:transparent!important;color:transparent!important;box-shadow:none!important;padding:0!important;}
@keyframes apexOverlayIn{from{opacity:0;backdrop-filter:blur(0)}to{opacity:1;backdrop-filter:blur(7px)}}

/* IMPORTANT: width is on the keyed Streamlit container that ACTUALLY owns the widgets. */
.st-key-apex_mobile_menu_drawer{position:fixed!important;top:0!important;right:0!important;bottom:0!important;left:auto!important;width:min(78vw,320px)!important;max-width:320px!important;min-width:0!important;height:100dvh!important;z-index:99990!important;box-sizing:border-box!important;padding:14px 12px 18px!important;overflow-y:auto!important;overflow-x:hidden!important;background:linear-gradient(160deg,rgba(5,22,32,.995),rgba(2,12,19,.998))!important;border-left:1px solid rgba(30,205,220,.32)!important;box-shadow:-18px 0 50px rgba(0,0,0,.42)!important;animation:apexDrawerIn 285ms cubic-bezier(.22,.8,.25,1) both!important;}
.st-key-apex_mobile_menu_drawer>div{width:100%!important;max-width:100%!important;min-width:0!important;}
@keyframes apexDrawerIn{from{transform:translateX(102%);opacity:.65}to{transform:translateX(0);opacity:1}}
.st-key-apex_mobile_menu_drawer [data-testid="stHorizontalBlock"]{display:flex!important;flex-wrap:nowrap!important;align-items:center!important;gap:8px!important;width:100%!important;}
.st-key-apex_mobile_menu_drawer [data-testid="column"]:first-child{flex:1 1 auto!important;min-width:0!important;}
.st-key-apex_mobile_menu_drawer [data-testid="column"]:last-child{flex:0 0 38px!important;min-width:38px!important;max-width:38px!important;}
.st-key-apex_mobile_menu_drawer [data-testid="column"]:last-child button{width:36px!important;height:36px!important;min-height:36px!important;padding:0!important;border-radius:9px!important;border:1px solid rgba(39,220,231,.34)!important;background:rgba(8,28,39,.88)!important;color:#27dce7!important;font-size:18px!important;transition:transform 150ms ease!important;}
.st-key-apex_mobile_menu_drawer [data-testid="column"]:last-child button:active{transform:scale(.90)!important;}
.st-key-apex_mobile_menu_drawer .apex-brand-logo{width:36px;height:36px;min-width:36px;border-radius:9px;}
.st-key-apex_mobile_menu_drawer .apex-brand-logo svg{width:24px;height:24px;}

/* Compact buttons + staggered entrance */
.st-key-apex_mobile_menu_items{margin-top:14px!important;margin-bottom:14px!important;}
.st-key-apex_mobile_menu_items [data-testid="stButton"]{margin:0 0 7px!important;opacity:0;transform:translateX(14px) scale(.985);animation:apexItemIn 250ms cubic-bezier(.22,.8,.25,1) forwards;}
.st-key-apex_mobile_menu_items [data-testid="stButton"]:nth-child(1){animation-delay:45ms}.st-key-apex_mobile_menu_items [data-testid="stButton"]:nth-child(2){animation-delay:80ms}.st-key-apex_mobile_menu_items [data-testid="stButton"]:nth-child(3){animation-delay:115ms}.st-key-apex_mobile_menu_items [data-testid="stButton"]:nth-child(4){animation-delay:150ms}.st-key-apex_mobile_menu_items [data-testid="stButton"]:nth-child(5){animation-delay:185ms}.st-key-apex_mobile_menu_items [data-testid="stButton"]:nth-child(6){animation-delay:220ms}.st-key-apex_mobile_menu_items [data-testid="stButton"]:nth-child(7){animation-delay:255ms}
@keyframes apexItemIn{to{opacity:1;transform:translateX(0) scale(1)}}
.st-key-apex_mobile_menu_items button{width:100%!important;min-height:44px!important;height:44px!important;padding:8px 11px!important;border-radius:10px!important;font-size:13.5px!important;font-weight:600!important;text-align:left!important;justify-content:flex-start!important;letter-spacing:.1px!important;transition:transform 130ms ease,border-color 180ms ease,background 180ms ease!important;}
.st-key-apex_mobile_menu_items button:active{transform:scale(.97)!important;}
.st-key-apex_mobile_menu_items button[kind="primary"]{color:#2be3eb!important;background:linear-gradient(90deg,rgba(20,210,225,.15),rgba(20,210,225,.04))!important;border:1px solid rgba(39,220,231,.48)!important;box-shadow:inset 0 0 14px rgba(39,220,231,.05),0 0 12px rgba(39,220,231,.05)!important;}
.st-key-apex_mobile_menu_items button[kind="secondary"]{color:#c3d0d9!important;background:rgba(7,25,35,.58)!important;border:1px solid rgba(70,145,165,.20)!important;}
.st-key-apex_mobile_menu_items button[kind="secondary"]:hover{color:#fff!important;border-color:rgba(39,220,231,.34)!important;background:rgba(9,31,43,.78)!important;}

.apex-mobile-account-card{width:100%;min-width:0;box-sizing:border-box;padding:10px;overflow:hidden;display:flex;align-items:center;justify-content:space-between;gap:8px;border-radius:11px;background:rgba(7,28,39,.76);border:1px solid rgba(24,205,220,.25);margin-top:10px;}
.apex-mobile-profile-left{display:flex;align-items:center;gap:9px;min-width:0;overflow:hidden;}
.apex-mobile-profile-avatar{width:32px;height:32px;min-width:32px;border-radius:50%;border:1px solid rgba(39,220,231,.40);background:rgba(39,220,231,.10);color:#27dce7;font-size:11px;font-weight:800;display:grid;place-items:center;}
.apex-mobile-profile-copy{min-width:0;overflow:hidden;}.apex-mobile-profile-name{font-size:12px;font-weight:800;color:#f3f7f9;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}.apex-mobile-profile-role{font-size:9px;color:#27dce7;margin-top:1px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}.apex-mobile-profile-chevron{color:#27dce7;font-size:16px;font-weight:700;}

@media (max-width:380px){.st-key-apex_mobile_menu_drawer{width:min(82vw,300px)!important;max-width:300px!important;padding-left:10px!important;padding-right:10px!important;}.st-key-apex_mobile_menu_items button{font-size:13px!important;}.apex-mobile-top-brand .apex-sidebar-brand-title{font-size:14px;}}
@media (min-width:600px) and (max-width:1023px){.st-key-apex_mobile_menu_drawer{width:320px!important;max-width:320px!important;}}
@media (min-width:1024px){.st-key-apex_mobile_header,.st-key-apex_mobile_menu_overlay,.st-key-apex_mobile_menu_drawer{display:none!important;}}
@media (prefers-reduced-motion:reduce){.st-key-apex_mobile_menu_overlay,.st-key-apex_mobile_menu_drawer,.st-key-apex_mobile_menu_items [data-testid="stButton"]{animation:none!important;transition:none!important;opacity:1!important;transform:none!important;}}

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
  .main .block-container,
  [data-testid="stAppViewBlockContainer"],
  .block-container {
    padding-top: 0 !important;
    padding-left: 10px !important;
    padding-right: 10px !important;
    padding-bottom: 24px !important;
    margin-top: -30px !important;
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
