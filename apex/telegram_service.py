"""Telegram service facade."""
from .production_core import (
    send_telegram_alert,
    _telegram_api,
    _alert_settings_text,
    _alert_settings_keyboard,
    _send_alert_settings_menu,
    _edit_alert_settings_menu,
    _handle_telegram_update,
    _telegram_bot_fingerprint,
    _load_telegram_update_offset,
    _save_telegram_update_offset,
    _get_telegram_update_controller,
    start_telegram_update_worker,
)
