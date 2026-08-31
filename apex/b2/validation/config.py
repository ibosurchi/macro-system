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
import math
from dataclasses import dataclass, field
from datetime import timedelta
from enum import Enum
from typing import Any, Mapping

from ..enums import Horizon
from ..horizons import HORIZON_EVALUATION_WINDOW
from .bars import (
    DEFAULT_MAX_GAP_MULTIPLE,
    DEFAULT_MIN_BARS_FOR_CADENCE,
    GRANULARITY_1D,
    GRANULARITY_SECONDS,
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
    #: RESEARCH DEFAULT, unchanged at 0.5. It is a multiple of ONE HORIZON
    #: SIGMA, not of a raw intraday range -- see ``neutral_band`` for why the
    #: distinction is load-bearing.
    neutral_band_mode: str = NEUTRAL_BAND_ATR
    neutral_band_atr_multiple: float = 0.5

    #: The period count behind production's stored ATR. Declared here so the
    #: reference DURATION of the anchor's ATR is structural and versioned
    #: rather than a magic "70 minutes" buried in a formula:
    #:
    #:     atr_reference_seconds = atr_period_bars x anchor_granularity_seconds
    #:
    #: 14 mirrors ``_build_macro_entry_plan``'s ``rolling(14)``. This is a
    #: VALIDATION-SIDE RESEARCH DEFAULT recording what production computes; it
    #: does not configure production and cannot change how production's ATR is
    #: calculated. If production's ATR period ever changes, this must be
    #: updated deliberately and the config version bumped.
    atr_period_bars: int = 14

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
        if int(self.atr_period_bars) < 1:
            raise ValueError("atr_period_bars must be at least 1.")
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
            "atr_period_bars": int(self.atr_period_bars),
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


# ===========================================================================
# NEUTRAL BAND -- horizon-scaled, configuration-derived
#
# The band separates "no material move" from a directional one. It lives here
# rather than in a resolver because it is entirely determined by the versioned
# configuration plus point-in-time scalars already captured on the anchor: no
# market path, no clock, no outcome. Computing it is not resolving anything.
#
# WHY SCALING IS REQUIRED
# -----------------------
# The stored ``atr`` is production's 14-period ATR of FIVE-MINUTE bars -- about
# seventy minutes of range. Comparing it directly against a FOURTEEN-DAY move
# understates the band by a factor of ~17: it produced roughly 0.18% for the
# audited sample, so essentially every observation would resolve directional on
# noise and NEUTRAL_WITHIN_BAND would never fire.
#
# The correction reuses production's OWN sqrt-time rule rather than inventing
# one. ``compute_tactical_move`` normalises by ``vol5 * sqrt(bars)``; the same
# principle scales a short-window range to a longer horizon. Applied to the
# audited sample the ATR mode gives ~3.06% and the volatility mode ~3.81% --
# two independent routes agreeing within 1.25x, which is the evidence that the
# scaling is principled rather than a fudge.
#
# The multiple k is unchanged at 0.5 and remains a VERSIONED RESEARCH DEFAULT.
# It is not a production trading threshold and configures nothing in production.
# ===========================================================================


class BandMode(Enum):
    """Which volatility source produced the band actually used."""

    ATR = NEUTRAL_BAND_ATR
    VOLATILITY_SCALE = NEUTRAL_BAND_VOLATILITY_SCALE
    UNAVAILABLE = "unavailable"


class BandUnavailableReason(Enum):
    """Why no band could be produced. Never a silent default."""

    NO_USABLE_VOLATILITY = "no_usable_volatility"
    UNKNOWN_ANCHOR_GRANULARITY = "unknown_anchor_granularity"
    UNKNOWN_HORIZON = "unknown_horizon"
    NON_POSITIVE_HORIZON = "non_positive_horizon"


def _finite_positive(value: Any) -> float | None:
    """A usable positive magnitude, or None. NaN/inf/<=0 are all unusable."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number if number > 0.0 else None


@dataclass(frozen=True)
class NeutralBand:
    """The horizon-scaled neutral band, with everything needed to audit it.

    Both candidate values are carried even when only one is selected, so a
    later reader can see what the alternative mode would have said without
    recomputing anything. All band values are FRACTIONAL RETURNS, directly
    comparable to a terminal return.
    """

    mode: BandMode
    band: float | None
    band_atr: float | None
    band_volatility: float | None
    k: float
    atr: float | None
    volatility_scale: float | None
    analysis_price: float | None
    anchor_granularity: str
    anchor_granularity_seconds: int | None
    atr_period_bars: int
    atr_reference_seconds: float | None
    horizon: str
    horizon_seconds: float | None
    config_version: str
    config_hash: str
    reason: BandUnavailableReason | None = None

    @property
    def is_available(self) -> bool:
        return self.mode is not BandMode.UNAVAILABLE and self.band is not None

    def as_record(self) -> dict[str, Any]:
        return {
            "band_mode": self.mode.value,
            "band": self.band,
            "band_atr": self.band_atr,
            "band_volatility": self.band_volatility,
            "k": self.k,
            "atr": self.atr,
            "volatility_scale": self.volatility_scale,
            "analysis_price": self.analysis_price,
            "anchor_granularity": self.anchor_granularity,
            "anchor_granularity_seconds": self.anchor_granularity_seconds,
            "atr_period_bars": self.atr_period_bars,
            "atr_reference_seconds": self.atr_reference_seconds,
            "horizon": self.horizon,
            "horizon_seconds": self.horizon_seconds,
            "band_available": self.is_available,
            "band_unavailable_reason": self.reason.value if self.reason else None,
            "validation_config_version": self.config_version,
            "validation_config_hash": self.config_hash,
            "status": "VERSIONED RESEARCH DEFAULT -- NOT CALIBRATED",
        }


def neutral_band(
    *,
    horizon: str | Horizon,
    anchor_granularity: str,
    atr: Any = None,
    volatility_scale: Any = None,
    analysis_price: Any = None,
    config: "ValidationConfig | None" = None,
) -> NeutralBand:
    """The horizon-scaled neutral band for one observation.

    Pure: no clock, no market path, no I/O, no randomness. Every input is a
    point-in-time scalar already captured on the anchor at evaluation time --
    nothing here can reach a future volatility, and no reconstructed value is
    admissible.

    ATR mode::

        atr_reference_seconds = atr_period_bars x anchor_granularity_seconds
        band_atr = k x atr x sqrt(horizon_seconds / atr_reference_seconds)
                     / analysis_price          -> fractional return

    Volatility mode::

        band_volatility = k x volatility_scale
                            x sqrt(horizon_seconds / anchor_granularity_seconds)

    ``volatility_scale`` is production's ``vol5``, a per-bar RETURN standard
    deviation, so it is already fractional and needs no price to normalise it.
    That asymmetry matters: a missing ``analysis_price`` blocks the ATR mode
    only, and the volatility mode can still serve.

    Selection follows ``config.neutral_band_mode``, falling back to the other
    mode when the configured one has no usable input. When neither does, the
    result is explicitly UNAVAILABLE with a reason -- never a fabricated default
    and never a division by zero.
    """
    settings = config or DEFAULT_VALIDATION_CONFIG
    horizon_key = horizon.value if isinstance(horizon, Horizon) else str(horizon)
    granularity_key = str(anchor_granularity or "")
    granularity_seconds = GRANULARITY_SECONDS.get(granularity_key)

    k = float(settings.neutral_band_atr_multiple)
    period_bars = int(settings.atr_period_bars)
    usable_atr = _finite_positive(atr)
    usable_vol = _finite_positive(volatility_scale)
    usable_price = _finite_positive(analysis_price)

    window = settings.window_for(horizon_key)
    horizon_seconds = window.total_seconds() if window is not None else None

    def unavailable(reason: BandUnavailableReason) -> NeutralBand:
        return NeutralBand(
            mode=BandMode.UNAVAILABLE,
            band=None,
            band_atr=None,
            band_volatility=None,
            k=k,
            atr=usable_atr,
            volatility_scale=usable_vol,
            analysis_price=usable_price,
            anchor_granularity=granularity_key,
            anchor_granularity_seconds=granularity_seconds,
            atr_period_bars=period_bars,
            atr_reference_seconds=(
                float(period_bars * granularity_seconds)
                if granularity_seconds
                else None
            ),
            horizon=horizon_key,
            horizon_seconds=horizon_seconds,
            config_version=settings.version,
            config_hash=settings.config_hash,
            reason=reason,
        )

    if window is None:
        return unavailable(BandUnavailableReason.UNKNOWN_HORIZON)
    if horizon_seconds is None or horizon_seconds <= 0:
        return unavailable(BandUnavailableReason.NON_POSITIVE_HORIZON)
    if not granularity_seconds:
        return unavailable(BandUnavailableReason.UNKNOWN_ANCHOR_GRANULARITY)

    atr_reference_seconds = float(period_bars * granularity_seconds)

    band_atr: float | None = None
    if usable_atr is not None and usable_price is not None:
        scale = math.sqrt(horizon_seconds / atr_reference_seconds)
        band_atr = (k * usable_atr * scale) / usable_price

    band_volatility: float | None = None
    if usable_vol is not None:
        scale = math.sqrt(horizon_seconds / float(granularity_seconds))
        band_volatility = k * usable_vol * scale

    preferred = (
        BandMode.ATR
        if settings.neutral_band_mode == NEUTRAL_BAND_ATR
        else BandMode.VOLATILITY_SCALE
    )
    by_mode = {BandMode.ATR: band_atr, BandMode.VOLATILITY_SCALE: band_volatility}
    fallback = (
        BandMode.VOLATILITY_SCALE if preferred is BandMode.ATR else BandMode.ATR
    )

    if by_mode[preferred] is not None:
        selected = preferred
    elif by_mode[fallback] is not None:
        selected = fallback
    else:
        return unavailable(BandUnavailableReason.NO_USABLE_VOLATILITY)

    return NeutralBand(
        mode=selected,
        band=by_mode[selected],
        band_atr=band_atr,
        band_volatility=band_volatility,
        k=k,
        atr=usable_atr,
        volatility_scale=usable_vol,
        analysis_price=usable_price,
        anchor_granularity=granularity_key,
        anchor_granularity_seconds=granularity_seconds,
        atr_period_bars=period_bars,
        atr_reference_seconds=atr_reference_seconds,
        horizon=horizon_key,
        horizon_seconds=horizon_seconds,
        config_version=settings.version,
        config_hash=settings.config_hash,
    )


__all__ = [
    "CADENCE_DEFAULTS_SOURCE",
    "DEFAULT_VALIDATION_CONFIG",
    "HORIZON_WINDOWS_SOURCE",
    "NEUTRAL_BAND_ATR",
    "NEUTRAL_BAND_MODES",
    "NEUTRAL_BAND_VOLATILITY_SCALE",
    "VALIDATION_CONFIG_VERSION",
    "BandMode",
    "BandUnavailableReason",
    "NeutralBand",
    "ValidationConfig",
    "neutral_band",
]
