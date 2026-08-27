"""Thin render orchestration for authenticated pages."""
from . import production_core as core
from .ui.common import render_top_header, render_footer
from .ui.terminal_nav import render_terminal_nav
from .ui.dashboard import render_dashboard as render_dashboard_ui

def render_dashboard(auth_user: dict) -> None:
    render_dashboard_ui(auth_user)
    render_footer()

def render_forex(auth_user: dict, *, active_page: str = "forex") -> None:
    render_top_header(auth_user)
    render_terminal_nav(active_page, auth_user)
    core.page_forex(core.DEFAULT_FRED_KEY, core.DEFAULT_TELEGRAM_CHANNEL)
    render_footer()

def render_gold(auth_user: dict) -> None:
    render_top_header(auth_user)
    render_terminal_nav("gold", auth_user)
    core.page_gold(core.DEFAULT_FRED_KEY, core.DEFAULT_TELEGRAM_CHANNEL)
    render_footer()

def render_oil(auth_user: dict) -> None:
    render_top_header(auth_user)
    render_terminal_nav("oil", auth_user)
    core.page_oil(core.DEFAULT_FRED_KEY, core.DEFAULT_TELEGRAM_CHANNEL)
    render_footer()

def render_nasdaq(auth_user: dict) -> None:
    render_top_header(auth_user)
    render_terminal_nav("nasdaq", auth_user)
    core.page_nasdaq(core.DEFAULT_FRED_KEY, core.DEFAULT_TELEGRAM_CHANNEL)
    render_footer()

def render_forecaster(auth_user: dict) -> None:
    render_top_header(auth_user)
    render_terminal_nav("forecaster", auth_user)
    core.page_catalyst_forecaster(
        core.DEFAULT_FRED_KEY,
        core.DEFAULT_TELEGRAM_CHANNEL,
        auth_user,
    )
    render_footer()

def render_admin(auth_user: dict) -> None:
    render_top_header(auth_user)
    render_terminal_nav("admin", auth_user)
    core.render_admin_key_generator()
    render_footer()
