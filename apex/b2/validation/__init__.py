"""Architecture B2 -- Stage D validation primitives.

Stage D is measurement infrastructure. Its first job is to make it possible to
ask what B2 actually predicted, at what horizon, and what happened afterwards --
without ever letting the answer leak back into the question.

Everything in this subpackage is **pure**, inheriting the guarantee that holds
for all of ``apex.b2``: it imports nothing from ``apex.production_core``,
Streamlit, ``requests`` or ``threading``, so no code path here can issue an AI
request, open a network connection, start a thread, send a message or write
durable state. The I/O half lives in ``apex.b2_validation_bridge``.

Three rules are enforced structurally rather than by convention:

*   **Features are frozen.** Everything a prediction was made from is read from
    the stored record payload and nothing else. Market bars are only ever an
    input to *outcome* computation, never to a feature.
*   **Strictly forward.** Only bars whose period opens strictly after
    ``evaluated_at`` may resolve an observation. A bar that straddles the
    evaluation moment contains price action from before the prediction.
*   **Missing is not wrong.** An observation that could not be resolved is
    reported as unresolved, with the reason. It is never scored as an incorrect
    prediction, and it never quietly shrinks a denominator.

Stage D-1 scope is anchor semantics and the market-observation record. Outcome
labelling, metrics, ablation and calibration are later, separately approved
steps. Nothing here calibrates anything, and B2 remains SHADOW /
NON-PRODUCTION / UNCALIBRATED.
"""
from __future__ import annotations

from .anchor import (
    ANCHOR_GRANULARITY,
    ANCHOR_PRICE_SOURCE,
    AnchorResolution,
    AnchorStatus,
    MarketAnchor,
    SymbolConvention,
    build_market_anchor,
    classify_anchor,
    read_anchor,
)
from .bars import (
    BAR_PRICE_SOURCE,
    GRANULARITY_1D,
    GRANULARITY_5M,
    GRANULARITY_SECONDS,
    MarketBar,
    MarketObservationError,
    analysis_ohlc,
    bar_is_final,
    canonical_bar_content_hash,
    canonical_bar_time_iso,
    canonical_observation_id,
    coverage,
    forward_bars,
    row_to_bar,
)

__all__ = [
    "ANCHOR_GRANULARITY",
    "ANCHOR_PRICE_SOURCE",
    "AnchorResolution",
    "AnchorStatus",
    "BAR_PRICE_SOURCE",
    "GRANULARITY_1D",
    "GRANULARITY_5M",
    "GRANULARITY_SECONDS",
    "MarketAnchor",
    "MarketBar",
    "MarketObservationError",
    "SymbolConvention",
    "analysis_ohlc",
    "bar_is_final",
    "build_market_anchor",
    "canonical_bar_content_hash",
    "canonical_bar_time_iso",
    "canonical_observation_id",
    "classify_anchor",
    "coverage",
    "forward_bars",
    "read_anchor",
    "row_to_bar",
]
