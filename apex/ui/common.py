"""Shared authenticated terminal chrome.

Presentation only: no strategy, data, alert, auth or worker logic lives here.
"""
from __future__ import annotations

from html import escape
import streamlit as st
from .. import production_core as core


def _shared_auth_css() -> None:
    st.markdown(r'''
<style>
:root{
  --apex-auth-bg:#02080d;
  --apex-auth-panel:rgba(6,21,30,.96);
  --apex-auth-border:rgba(60,155,175,.20);
  --apex-auth-cyan:#27dce7;
  --apex-auth-text:#f2f6f8;
  --apex-auth-muted:#94a2b0;
}
[data-testid="stAppViewContainer"]{
  background:radial-gradient(circle at 12% 0%,rgba(39,220,231,.035),transparent 28%),var(--apex-auth-bg)!important;
}
[data-testid="stSidebar"]{
  background:linear-gradient(180deg,rgba(3,17,26,.995),rgba(2,10,16,1))!important;
  border-right:1px solid rgba(30,200,215,.20)!important;
}
[data-testid="stSidebar"] > div:first-child{padding-top:14px!important;}
[data-testid="stSidebar"] [data-testid="stButton"] button{
  min-height:46px!important;
  border-radius:10px!important;
  justify-content:flex-start!important;
  text-align:left!important;
  font-size:14px!important;
  font-weight:600!important;
  box-shadow:none!important;
  margin:2px 0!important;
}
[data-testid="stSidebar"] [data-testid="stButton"] button[kind="primary"]{
  background:linear-gradient(90deg,rgba(20,210,225,.14),rgba(20,210,225,.035))!important;
  border:1px solid rgba(25,210,225,.36)!important;
  color:#2ce3ed!important;
}
.apex-auth-brand{display:flex;align-items:center;gap:10px;padding:4px 5px 16px;}
.apex-auth-mark{width:38px;height:38px;border-radius:10px;border:1px solid rgba(39,220,231,.28);display:grid;place-items:center;color:#27dce7;font-size:25px;font-weight:900;font-style:italic;background:rgba(39,220,231,.035);box-shadow:inset 0 0 16px rgba(39,220,231,.04);}
.apex-auth-brand-name{font-size:16px;font-weight:850;letter-spacing:1.8px;color:#f3f7f9;}
.apex-auth-brand-sub{font-size:10px;color:#27dce7;margin-top:2px;}
.apex-auth-side-sep{height:1px;background:rgba(80,145,165,.12);margin:5px 0 10px;}
.apex-auth-side-status{margin-top:18px;padding:12px 13px;border:1px solid var(--apex-auth-border);border-radius:11px;background:rgba(7,25,35,.52);}
.apex-auth-side-label{font-size:10px;letter-spacing:.4px;color:#748895;text-transform:uppercase;}
.apex-auth-side-clock{font-size:19px;font-weight:800;color:#eef4f6;margin-top:4px;}
.apex-auth-side-date{font-size:10px;color:#899aa7;margin-top:2px;}
.apex-auth-mobile-head{display:none;}
.apex-auth-desktop-strip{display:flex;align-items:center;justify-content:flex-end;gap:8px;margin:0 0 8px;}
.apex-auth-chip{display:inline-flex;align-items:center;gap:6px;min-height:34px;padding:0 11px;border:1px solid var(--apex-auth-border);border-radius:999px;background:rgba(6,21,30,.72);color:#c4d0d7;font-size:11px;}
.block-container{max-width:1800px!important;}
@media (min-width:1024px){
  [data-testid="stSidebar"]{min-width:240px!important;max-width:240px!important;width:240px!important;}
}
@media (min-width:769px) and (max-width:1100px){
  [data-testid="stSidebar"]{min-width:200px!important;max-width:200px!important;width:200px!important;}
}
@media (max-width:768px){
  .block-container{padding-top:12px!important;padding-left:14px!important;padding-right:14px!important;}
  .apex-auth-desktop-strip{display:none;}
  .apex-auth-mobile-head{display:flex;align-items:center;justify-content:space-between;gap:10px;min-height:48px;margin:0 0 12px;padding:8px 10px;border:1px solid rgba(55,150,170,.17);border-radius:12px;background:linear-gradient(145deg,rgba(6,21,30,.92),rgba(3,13,20,.97));}
  .apex-auth-mobile-left{display:flex;align-items:center;gap:8px;min-width:0;}
  .apex-auth-mobile-mark{font-weight:900;font-size:19px;color:#27dce7;}
  .apex-auth-mobile-title{font-size:13px;font-weight:800;color:#eef4f7;letter-spacing:.7px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}
  .apex-auth-mobile-role{font-size:9px;color:#8fa1ad;border:1px solid rgba(70,145,165,.18);padding:4px 7px;border-radius:999px;white-space:nowrap;}
  [data-testid="stSidebar"] [data-testid="stButton"] button{min-height:44px!important;}
}
</style>
''', unsafe_allow_html=True)


def render_top_header(auth_user: dict | None = None) -> None:
    """Render compact authenticated chrome instead of the legacy oversized terminal header."""
    _shared_auth_css()
    user = auth_user or {}
    is_admin = bool(user.get("is_admin"))
    role = "Admin" if is_admin else "VIP"
    name = escape(str(user.get("user_name") or user.get("username") or role))
    now = core.get_current_time()
    st.markdown(
        f'''<div class="apex-auth-desktop-strip">
              <div class="apex-auth-chip">{'♛' if is_admin else '◇'} {role}</div>
              <div class="apex-auth-chip">{name}</div>
              <div class="apex-auth-chip">◷ {now.strftime('%H:%M')}</div>
            </div>
            <div class="apex-auth-mobile-head">
              <div class="apex-auth-mobile-left"><span class="apex-auth-mobile-mark">A</span><span class="apex-auth-mobile-title">APEXMACRO</span></div>
              <span class="apex-auth-mobile-role">{role}</span>
            </div>''',
        unsafe_allow_html=True,
    )


def render_footer() -> None:
    core.render_html(f"""
    <div class="app-foot">
      <div>© 2026 ApexMacro • Institutional Macro Intelligence</div>
      <div><span class="live-dot"></span><span style="color:#00ffa3;font-weight:700;">Engine Active &nbsp; {core.get_current_time().strftime('%H:%M:%S')}</span></div>
    </div>
    """)
