"""Architecture B2 -- the pinned market series for validation capture.

A production symbol convention answers "how does production price this
instrument today", and it is allowed to fall back when a vendor symbol is
unavailable, because a production chart that degrades to a second symbol is
better than a blank one. A validation series must answer a different question:
"which single market series is this instrument's recorded history made of".

Those two questions came apart in the live data. Gold's production convention
is ``XAUUSD=X`` with ``GC=F`` declared as a fallback. ``XAUUSD=X`` returned HTTP
404 throughout Stage D-1 activation, so every stored Gold bar is ``GC=F``. The
symbol is part of ``canonical_observation_id``, so if ``XAUUSD=X`` ever starts
answering again, capture would begin appending a SECOND Gold series -- spot
rather than futures, a different scale -- under the same instrument, with no
identity collision anywhere to reveal it. Nothing would fail. The two series
would simply coexist and be read together.

A pin closes that door. For a pinned instrument, capture tries the pinned
symbol and nothing else: availability can make the capture fail, but it can
never make the capture switch series.

Three properties are deliberate:

**A pin only ever NARROWS.** ``pinned_symbols_are_production_approved`` (in the
tests) asserts every pin names a symbol production already declares -- primary
or fallback -- with production's own inversion. A pin cannot invent a series
production never sanctioned, and cannot introduce an inversion disagreement.

**A pin is capture-side only.** Nothing here is read by the production Gold
model, by ``symbol_convention``, by ``classify_anchor``, or by the resolution
read path, which resolves each observation against the symbol recorded on its
own anchor. No stored observation changes meaning because of a pin.

**Changing a pin is a versioned research decision.** ``SERIES_PIN_VERSION`` and
the table below are asserted by a golden test. Editing either without
deliberately updating that test fails CI. The version is intentionally NOT
folded into ``ValidationConfig.config_hash``: a pin governs which bars get
captured, not the arithmetic of validation, and hashing it would invalidate the
config hash of every already-stored result for no analytical gain.

This module is pure. It performs no I/O, holds no clock and reads no record.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

#: Bumped whenever the pinned series table below changes in any way. A pin
#: change is a research decision about what an instrument's recorded history
#: MEANS, so it is versioned rather than edited silently.
SERIES_PIN_VERSION = "b2-series-pin-v1"


@dataclass(frozen=True)
class SeriesPin:
    """The one market series a pinned instrument's capture may use."""

    instrument: str
    symbol: str
    invert: bool
    #: ISO date of the research decision that established this pin.
    pinned_on: str
    #: Why this instrument is pinned. Recorded so a future reader does not have
    #: to reconstruct the reasoning from repository history.
    reason: str


#: The pinned series, by instrument. An instrument ABSENT from this mapping
#: keeps the ordinary behaviour -- production's primary symbol, then its
#: declared fallbacks in order. Only Gold is pinned today, and Gold is also the
#: only instrument production declares a fallback for, so this table is the
#: complete set of places where capture could otherwise switch series.
PINNED_CAPTURE_SYMBOLS: Mapping[str, SeriesPin] = {
    "Gold": SeriesPin(
        instrument="Gold",
        symbol="GC=F",
        invert=False,
        pinned_on="2026-09-02",
        reason=(
            "XAUUSD=X returned HTTP 404 throughout Stage D-1 activation, so the "
            "entire captured Gold series is GC=F. Allowing capture to switch "
            "back on availability would append a second, differently-scaled "
            "series under the same instrument, with no identity collision to "
            "reveal it."
        ),
    ),
}


def pinned_capture_symbol(instrument: str) -> SeriesPin | None:
    """The pin for one instrument, or None when it is not pinned.

    Returns None rather than raising for an unknown instrument: an unpinned
    instrument is the normal case, not an error.
    """
    return PINNED_CAPTURE_SYMBOLS.get(str(instrument or "").strip())


def pinned_instruments() -> tuple[str, ...]:
    """Every pinned instrument, sorted. Used for reporting, never for capture."""
    return tuple(sorted(PINNED_CAPTURE_SYMBOLS))


__all__ = [
    "PINNED_CAPTURE_SYMBOLS",
    "SERIES_PIN_VERSION",
    "SeriesPin",
    "pinned_capture_symbol",
    "pinned_instruments",
]
