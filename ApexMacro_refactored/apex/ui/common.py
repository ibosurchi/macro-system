"""Shared authenticated terminal UI."""
from .. import production_core as core

def render_top_header(auth_user: dict | None = None) -> None:
    core.render_top_header(auth_user)

def render_footer() -> None:
    core.render_html(f"""
    <div class="app-foot">
      <div>© 2026 ApexMacro • Institutional Macro Intelligence</div>
      <div><span class="live-dot"></span><span style="color:#00ffa3;font-weight:700;">Engine Active &nbsp; {core.get_current_time().strftime('%H:%M:%S')}</span></div>
    </div>
    """)
