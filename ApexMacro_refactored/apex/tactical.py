"""Tactical Move facade."""
from .production_core import (
    _tactical_symbol_config,
    _fetch_tactical_price_series,
    _tactical_label,
    _tactical_icon,
    _tactical_interpretation,
    compute_tactical_move,
    render_tactical_move_panel,
    _load_tactical_state,
    _save_tactical_state,
    _update_tactical_alert_state,
    _build_tactical_alert_msg,
    send_personalized_tactical_alert,
    check_global_tactical_moves,
)
