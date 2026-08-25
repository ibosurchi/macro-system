from __future__ import annotations
import streamlit as st
import apex_core as core

fred_key, channel_name = core.bootstrap_runtime()
auth_user = st.session_state.get("APEX_AUTH_USER")
if not (auth_user and auth_user.get("is_authenticated")):
    st.switch_page("pages/login.py")

core.render_top_header(auth_user)
core.page_dashboard(fred_key, channel_name, auth_user)
core.render_html(f"""
<div class="app-foot">
  <div>© 2026 ApexMacro • Institutional Macro Intelligence</div>
  <div><span class="live-dot"></span><span style="color:#00ffa3;font-weight:700;">Engine Active &nbsp; {core.get_current_time().strftime('%H:%M:%S')}</span></div>
</div>
""")
