"""Singleton-safe background service facade."""
from .production_core import (
    _acquire_telegram_daemon_process_lock,
    _get_daemon_controller,
    start_background_alert_daemon,
)
