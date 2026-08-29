"""Catalyst Forecaster and Causal Intelligence facade."""
from .production_core import (
    _normalize_catalyst_title,
    _find_legacy_catalyst_meta,
    _build_ff_event_code,
    get_upcoming_catalyst_events,
    _nasdaq_forecaster_implication,
    compute_event_nowcast,
    get_causal_macro_ai_analysis,
    render_causal_macro_ai_panel,
    page_catalyst_forecaster,
)
