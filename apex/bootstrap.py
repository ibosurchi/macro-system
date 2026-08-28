"""Central application bootstrap."""
from . import production_core as core
from .ui.styles import apply_global_styles

def initialize_background_services() -> None:
    # Both underlying implementations retain their existing cached controller/process-lock protections.
    core.start_telegram_update_worker()
    if core.DEFAULT_FRED_KEY:
        core.start_background_alert_daemon(
            core.DEFAULT_FRED_KEY,
            core.DEFAULT_TELEGRAM_CHANNEL,
        )
    core.start_shared_background_ai_worker()

def prepare_page() -> None:
    apply_global_styles()
    # Defensive route-level health check: only ensures the singleton supervisor
    # exists. It never performs a paid request on page navigation.
    core.start_shared_background_ai_worker()
