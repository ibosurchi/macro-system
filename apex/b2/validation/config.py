"""Architecture B2 -- Stage D-2 versioned validation configuration.

Every number a validation result depends on lives here, is versioned, and is
hashed into the result that used it. Without that, a result computed today and
a result computed after a constant changed would be indistinguishable -- and an
analyst could not tell whether a difference came from the market or from us.

Two rules govern this module.

**Nothing here is architectural truth.** Every value is a VERSIONED RESEARCH
DEFAULT: a starting point chosen for a stated structural reason, not a fitted
parameter and not a claim about how markets behave. Changing one is a research
decision that bumps the version, never a silent edit.

**Values are READ from their owning module, never restated.** The horizon
windows come from ``horizons.HORIZON_EVALUATION_WINDOW`` -- the same constants
that already stamped ``claim.evaluate_at`` onto every stored record -- and the
cadence defaults come from ``bars``. A second copy would be a second definition,
and the two would eventually disagree without anything failing. The config
RECORDS what it used and where it came from; it does not own it.

This module is pure. It performs no I/O, holds no clock, and reads no record.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Mapping

from ..enums import Horizon
from ..horizons import HORIZON_EVALUATION_WINDOW
from .bars import (
    DEFAULT_MAX_GAP_MULTIPLE,
    DEFAULT_MIN_BARS_FOR_CADENCE,
    GRANULARITY_1D,
)

#: Bumped when the MEANING of validation changes -- a new axis, a new
#: eligibility rule, a different window semantic. Changing only a numeric value
#: is already captured by ``config_hash``, so the two together answer both
#: "what shape of validation was this" and "with exactly which numbers".
VALIDATION_CONFIG_VERSION = "b2-valcfg-v1"

#: Where the horizon windows come from. Recorded so a reader of a stored result
#: can find the authority rather than assuming this module invented them.
HORIZON_WINDOWS_SOURCE = "apex.b2.horizons.HORIZON_EVALUATION_WINDOW"

#: Where the cadence defaults come from, for the same reason.
CADENCE_DEFAULTS_SOURCE = "apex.b2.validation.bars"

#: How the neutral band is derived. ATR is preferred because it is already
#: captured on the anchor in the instrument's own units; the volatility-scale
#: form is the fallback for observations whose entry plan produced no ATR.
NEUTRAL_BAND_ATR = "atr"
NEUTRAL_BAND_VOLATILITY_SCALE = "volatility_scale"
NEUTRAL_BAND_MODES = frozenset({NEUTRAL_BAND_ATR, NEUTRAL_BAND_VOLATILITY_SCALE})


def _default_horizon_windows() -> dict[str, timedelta]:
    """The architecture's own windows, read rather than redefined."""
    return {horizon.value: window for horizon, window in HORIZON_EVALUATION_WINDOW.items()}


@dataclass(frozen=True)
class ValidationConfig:
    """One frozen, versioned set of validation research defaults.

    Injectable so a future research exercise can vary a value deliberately and
    have the change appear in ``config_hash`` -- rather than by accident, where
    it would appear nowhere.
    """

    version: str = VALIDATION_CONFIG_VERSION

    #: Forward windows per horizon, keyed by the horizon's own string value.
    #: RESEARCH DEFAULT only in the sense that the architecture may revise them;
    #: they are read from ``horizons`` so a record's registered evaluation
    #: deadline and its validation window can never diverge.
    horizon_windows: Mapping[str, timedelta] = field(
        default_factory=_default_horizon_windows
    )

    #: The bar granularity outcomes are resolved against. Daily, because a
    #: 14-day tactical window cannot be resolved from a five-day intraday
    #: history -- the constraint that produced the daily capture in the first
    #: place.
    resolution_granularity: str = GRANULARITY_1D

    #: Coverage tolerance, in multiples of a series' own observed cadence.
    #: RESEARCH DEFAULT -- read from ``bars``. Chosen to span a weekend.
    max_gap_multiple: float = DEFAULT_MAX_GAP_MULTIPLE

    #: Bars required before a cadence may be estimated at all.
    #: RESEARCH DEFAULT -- read from ``bars``.
    min_bars_for_cadence: int = DEFAULT_MIN_BARS_FOR_CADENCE

    #: How "no meaningful move" is separated from a directional one.
    #: RESEARCH DEFAULT. The multiple is deliberately NOT tuned: 0.5 ATR is half
    #: a typical day's range, which is a structural statement about noise, not a
    #: fitted threshold. D-2B does not apply it; D-2C will.
    neutral_band_mode: str = NEUTRAL_BAND_ATR
    neutral_band_atr_multiple: float = 0.5

    #: Whether a substituted market series may resolve an observation at all.
    #: When True the observation resolves and is STAMPED, landing in the
    #: reconstructed research pool. When False it is excluded outright.
    #: True by default because excluding it would silently drop Gold entirely.
    allow_series_substitution: bool = True

    #: Whether a reconstructed (non point-in-time) anchor may resolve at all.
    #: Same tiering: it resolves into the research pool, never the captured one.
    allow_reconstructed_anchor: bool = True

    def __post_init__(self) -> None:
        if not str(self.version).strip():
            raise ValueError("A validation config must carry a version.")
        if self.neutral_band_mode not in NEUTRAL_BAND_MODES:
            raise ValueError(
                f"Unknown neutral_band_mode {self.neutral_band_mode!r}; "
                f"expected one of {sorted(NEUTRAL_BAND_MODES)}."
            )
        if float(self.max_gap_multiple) <= 0:
            raise ValueError("max_gap_multiple must be positive.")
        if int(self.min_bars_for_cadence) < 2:
            raise ValueError(
                "min_bars_for_cadence must be at least 2: a cadence needs at "
                "least one inter-bar gap to be measured from."
            )
        if float(self.neutral_band_atr_multiple) < 0:
            raise ValueError("neutral_band_atr_multiple cannot be negative.")
        if not self.horizon_windows:
            raise ValueError("A validation config must declare horizon windows.")

    def window_for(self, horizon: str | Horizon) -> timedelta | None:
        """The forward window for a horizon, or None when it is unknown.

        None rather than a default: silently substituting the tactical window
        for an unrecognised horizon would produce a result attributed to the
        wrong claim.
        """
        key = horizon.value if isinstance(horizon, Horizon) else str(horizon)
        return self.horizon_windows.get(key)

    @property
    def chosen(self) -> dict[str, Any]:
        """Exactly the values that were chosen, for hashing and for the record."""
        return {
            "horizon_windows_hours": {
                key: window.total_seconds() / 3600.0
                for key, window in sorted(self.horizon_windows.items())
            },
            "resolution_granularity": str(self.resolution_granularity),
            "max_gap_multiple": float(self.max_gap_multiple),
            "min_bars_for_cadence": int(self.min_bars_for_cadence),
            "neutral_band_mode": str(self.neutral_band_mode),
            "neutral_band_atr_multiple": float(self.neutral_band_atr_multiple),
            "allow_series_substitution": bool(self.allow_series_substitution),
            "allow_reconstructed_anchor": bool(self.allow_reconstructed_anchor),
        }

    @property
    def config_hash(self) -> str:
        """Integrity hash over the chosen values. Not a substitute for storing them."""
        canonical = json.dumps(
            self.chosen, sort_keys=True, separators=(",", ":"), default=str
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]

    def as_provenance(self) -> dict[str, Any]:
        """Everything needed to reconstruct this configuration later.

        Deliberately explicit rather than a bare version string, mirroring
        ``AggregationConfig.as_provenance``: the chosen values, where they came
        from, and a hash over them, so the calculation can be rebuilt from a
        stored result alone without consulting repository history.
        """
        return {
            "version": self.version,
            "chosen": self.chosen,
            "sources": {
                "horizon_windows": HORIZON_WINDOWS_SOURCE,
                "cadence_defaults": CADENCE_DEFAULTS_SOURCE,
            },
            # Stated on the record itself so no reader mistakes these for
            # calibrated or empirically justified values.
            "status": "VERSIONED RESEARCH DEFAULTS -- NOT CALIBRATED",
            "config_hash": self.config_hash,
        }


#: The configuration Stage D-2 uses unless a caller supplies another.
DEFAULT_VALIDATION_CONFIG = ValidationConfig()


__all__ = [
    "CADENCE_DEFAULTS_SOURCE",
    "DEFAULT_VALIDATION_CONFIG",
    "HORIZON_WINDOWS_SOURCE",
    "NEUTRAL_BAND_ATR",
    "NEUTRAL_BAND_MODES",
    "NEUTRAL_BAND_VOLATILITY_SCALE",
    "VALIDATION_CONFIG_VERSION",
    "ValidationConfig",
]
