"""Architecture B2 -- the market anchor.

An anchor is the point-in-time market state one shadow evaluation was taken
against: the price, the symbol that price came from, the direction convention in
force for that symbol, and the market timestamp behind it.

Why it has to be captured rather than looked up later
-----------------------------------------------------
The only price source in this project keeps **five days** of intraday history.
An observation whose price was never recorded does not merely wait to be
resolved -- after five days the data it needed is gone. Re-deriving a price for
it afterwards is *reconstruction*, and reconstruction is a different, weaker
claim than capture. Both are useful; conflating them is not.

That distinction is the whole content of ``AnchorStatus``:

``CAPTURED``
    The record carries a usable price AND the symbol it came from, both written
    at evaluation time. Point-in-time safe.

``RECONSTRUCTED``
    No usable anchor was captured, but the instrument still maps to a known
    symbol, so a price can be recovered from stored daily bars. Historically
    honest only if it is *labelled*: it must never be pooled with captured
    anchors for a calibration claim, because a daily bar is not what the
    evaluation actually saw.

``MISSING``
    Neither. The observation is unvalidatable. It is counted and reported, never
    silently dropped and never scored as a wrong prediction.

Nothing here computes an anchor from the future, and nothing here is read by any
scoring path. The anchor travels alongside the evaluation; it never enters it.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping

#: The anchor price comes from the live 5-minute tactical series, while forward
#: resolution uses daily bars. Recording both source and granularity keeps that
#: difference visible: an anchor is not a daily bar and must never be read as
#: one. The prices are directly comparable -- same symbol, same quote scale --
#: but the sampling is not, and a reader is entitled to know which is which.
ANCHOR_PRICE_SOURCE = "yahoo_5m_tactical"
ANCHOR_GRANULARITY = "5m"

#: Guard matching production's ``np.maximum(x, 1e-12)`` in _tactical_analysis_ohlc.
_RECIPROCAL_FLOOR = 1e-12


class AnchorStatus(Enum):
    """How the price behind an observation was obtained. Never inferred loosely."""

    CAPTURED = "anchor_captured"
    RECONSTRUCTED = "anchor_reconstructed"
    MISSING = "anchor_missing"

    @property
    def is_point_in_time(self) -> bool:
        """Only a captured anchor is point-in-time safe."""
        return self is AnchorStatus.CAPTURED


@dataclass(frozen=True)
class SymbolConvention:
    """The production symbol mapping for one instrument, passed in by the caller.

    Deliberately injected rather than imported. ``_tactical_symbol_config`` lives
    in ``production_core``, which nothing under ``apex.b2`` may import; and
    restating the mapping here would create a second definition that could drift
    from the one production actually trades on.
    """

    instrument: str
    symbol: str
    invert: bool
    fallback_symbols: tuple[str, ...] = ()

    def is_fallback(self, used_symbol: str) -> bool:
        """Whether a served symbol was the fallback rather than the primary."""
        used = str(used_symbol or "").strip()
        return bool(used) and used != self.symbol and used in self.fallback_symbols


def _numeric(value: Any) -> float | None:
    """Coerce to float, or None. NaN and infinities are unusable, not zero."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _positive(value: Any) -> float | None:
    number = _numeric(value)
    return number if number is not None and number > 0.0 else None


@dataclass(frozen=True)
class MarketAnchor:
    """The market state one evaluation was taken against. Immutable."""

    #: Last close in the ASSET-STRENGTH convention -- the same quantity the
    #: Directional family's returns were computed from. Bullish for this
    #: instrument always means this number rising.
    analysis_price: float | None
    #: Last close in the RAW QUOTE convention, exactly as the venue prints it.
    last_price: float | None
    #: The symbol that actually served this observation, which for Gold may be
    #: the fallback rather than the configured primary.
    symbol: str
    symbol_requested: str
    symbol_fallback_used: bool
    #: Production's direction convention for this symbol. True means the
    #: strength convention is the reciprocal (USDJPY -> JPY strength).
    invert: bool
    #: Open time of the last bar behind the anchor, epoch seconds.
    market_ts: int | None
    market_ts_iso: str
    #: Per-bar realised return volatility, exported unchanged by
    #: compute_tactical_move. Recorded so the noise band can be computed later
    #: on the same scale production used, not a second definition of volatility.
    volatility_scale: float | None
    atr: float | None
    atr_ratio: float | None
    volatility_regime: str
    price_source: str = ANCHOR_PRICE_SOURCE
    granularity: str = ANCHOR_GRANULARITY

    @property
    def has_usable_price(self) -> bool:
        return self.analysis_price is not None and bool(self.symbol)

    @property
    def strength_price(self) -> float | None:
        """The anchor price in the strength convention.

        ``analysis_price`` is already converted by production, so this returns
        it directly. When only a raw quote survived, the conversion is applied
        here using the recorded ``invert`` flag rather than a guess.
        """
        if self.analysis_price is not None:
            return self.analysis_price
        if self.last_price is None:
            return None
        if not self.invert:
            return self.last_price
        return 1.0 / max(self.last_price, _RECIPROCAL_FLOOR)

    def as_record(self) -> dict[str, object]:
        return {
            "analysis_price": self.analysis_price,
            "last_price": self.last_price,
            "symbol": self.symbol,
            "symbol_requested": self.symbol_requested,
            "symbol_fallback_used": self.symbol_fallback_used,
            "invert": self.invert,
            "market_ts": self.market_ts,
            "market_ts_iso": self.market_ts_iso,
            "volatility_scale": self.volatility_scale,
            "atr": self.atr,
            "atr_ratio": self.atr_ratio,
            "volatility_regime": self.volatility_regime,
            "price_source": self.price_source,
            "granularity": self.granularity,
            "anchor_status": (
                AnchorStatus.CAPTURED.value
                if self.has_usable_price
                else AnchorStatus.MISSING.value
            ),
        }

    @classmethod
    def from_record(cls, payload: Mapping[str, Any] | None) -> "MarketAnchor | None":
        """Rebuild an anchor from a stored record. Returns None when absent."""
        if not isinstance(payload, Mapping):
            return None
        symbol = str(payload.get("symbol") or "").strip()
        return cls(
            analysis_price=_positive(payload.get("analysis_price")),
            last_price=_positive(payload.get("last_price")),
            symbol=symbol,
            symbol_requested=str(payload.get("symbol_requested") or "").strip(),
            symbol_fallback_used=bool(payload.get("symbol_fallback_used")),
            invert=bool(payload.get("invert")),
            market_ts=(
                int(payload["market_ts"])
                if _numeric(payload.get("market_ts")) is not None
                else None
            ),
            market_ts_iso=str(payload.get("market_ts_iso") or ""),
            volatility_scale=_positive(payload.get("volatility_scale")),
            atr=_positive(payload.get("atr")),
            atr_ratio=_positive(payload.get("atr_ratio")),
            volatility_regime=str(payload.get("volatility_regime") or "unavailable"),
            price_source=str(payload.get("price_source") or ANCHOR_PRICE_SOURCE),
            granularity=str(payload.get("granularity") or ANCHOR_GRANULARITY),
        )


def _market_ts_iso(epoch_seconds: int | None) -> str:
    if epoch_seconds is None:
        return ""
    try:
        return datetime.fromtimestamp(int(epoch_seconds), tz=timezone.utc).isoformat()
    except (OSError, OverflowError, ValueError):
        return ""


def build_market_anchor(
    *,
    convention: SymbolConvention,
    tactical: Mapping[str, Any] | None,
    execution_inputs: Mapping[str, Any] | None = None,
) -> MarketAnchor | None:
    """Assemble the anchor from values production already computed.

    Every field is read from a dictionary the caller already holds: nothing is
    fetched, nothing is recomputed, and no request is issued. ``analysis_price``,
    ``last_price``, ``symbol``, ``market_ts`` and ``volatility_scale`` are
    exports of ``compute_tactical_move``; ``atr``, ``atr_ratio`` and
    ``volatility_regime`` come from the entry plan the execution layer already
    read. That is what keeps anchor capture free at the call site.

    Returns None when there is no tactical result at all -- there is no market
    state to record, which is different from recording that it was unavailable.
    A partial anchor is still written: an anchor missing its price is
    self-describing through ``anchor_status`` and is far more useful than
    silence.
    """
    if not isinstance(tactical, Mapping):
        return None

    used_symbol = str(tactical.get("symbol") or "").strip()
    execution = execution_inputs if isinstance(execution_inputs, Mapping) else {}

    market_ts_raw = _numeric(tactical.get("market_ts"))
    market_ts = int(market_ts_raw) if market_ts_raw is not None else None

    return MarketAnchor(
        analysis_price=_positive(tactical.get("analysis_price")),
        last_price=_positive(tactical.get("last_price")),
        symbol=used_symbol or convention.symbol,
        symbol_requested=convention.symbol,
        symbol_fallback_used=convention.is_fallback(used_symbol),
        invert=bool(convention.invert),
        market_ts=market_ts,
        market_ts_iso=_market_ts_iso(market_ts),
        volatility_scale=_positive(tactical.get("volatility_scale")),
        atr=_positive(execution.get("atr")),
        atr_ratio=_positive(execution.get("atr_ratio")),
        volatility_regime=str(execution.get("volatility_regime") or "unavailable"),
    )


def read_anchor(record: Mapping[str, Any] | None) -> MarketAnchor | None:
    """The anchor stored on a shadow record, or None on a legacy record."""
    if not isinstance(record, Mapping):
        return None
    return MarketAnchor.from_record(record.get("market_anchor"))


def _legacy_execution_price(record: Mapping[str, Any]) -> float | None:
    """A legacy record's execution price, when the entry plan produced one.

    Present only when production's macro regime was Bullish or Bearish at
    evaluation time; the neutral entry plan carries no price at all. It is NOT
    an anchor -- no symbol, no market timestamp and no convention flag accompany
    it -- but where it exists it is a genuine point-in-time price and can
    cross-check a reconstructed anchor rather than replace it.
    """
    execution = record.get("execution")
    if not isinstance(execution, Mapping):
        return None
    return _positive(execution.get("current_price"))


@dataclass(frozen=True)
class AnchorResolution:
    """How one stored record can be anchored, and with what caveats."""

    status: AnchorStatus
    symbol: str
    invert: bool
    anchor: MarketAnchor | None
    #: A legacy execution price, where one exists, for cross-checking a
    #: reconstructed anchor. Never used as the anchor itself.
    legacy_execution_price: float | None = None
    caveats: tuple[str, ...] = ()

    def as_record(self) -> dict[str, object]:
        return {
            "anchor_status": self.status.value,
            "symbol": self.symbol,
            "invert": self.invert,
            "point_in_time": self.status.is_point_in_time,
            "legacy_execution_price": self.legacy_execution_price,
            "caveats": list(self.caveats),
        }


def classify_anchor(
    record: Mapping[str, Any] | None,
    convention: SymbolConvention | None = None,
) -> AnchorResolution:
    """Decide how a stored record can be anchored, and say so explicitly.

    ``convention`` is the production symbol mapping for the record's instrument,
    supplied by the caller. Without it a record that carries no anchor cannot be
    reconstructed either, because the symbol its price would have to come from
    is unknown.

    This is a **read-time** classification. It writes nothing, and it never
    backfills an anchor into a stored record: a legacy observation stays exactly
    as truthful as it was, and the weaker claim is carried in the label instead
    of being hidden by a fabricated field.
    """
    if not isinstance(record, Mapping):
        return AnchorResolution(
            status=AnchorStatus.MISSING,
            symbol="",
            invert=False,
            anchor=None,
            caveats=("record_unreadable",),
        )

    anchor = read_anchor(record)
    if anchor is not None and anchor.has_usable_price:
        caveats: list[str] = []
        if anchor.symbol_fallback_used:
            caveats.append("symbol_fallback_used")
        if anchor.market_ts is None:
            caveats.append("market_timestamp_unavailable")
        return AnchorResolution(
            status=AnchorStatus.CAPTURED,
            symbol=anchor.symbol,
            invert=anchor.invert,
            anchor=anchor,
            legacy_execution_price=_legacy_execution_price(record),
            caveats=tuple(caveats),
        )

    if convention is None or not convention.symbol:
        return AnchorResolution(
            status=AnchorStatus.MISSING,
            symbol=(anchor.symbol if anchor else ""),
            invert=bool(anchor.invert) if anchor else False,
            anchor=anchor,
            legacy_execution_price=_legacy_execution_price(record),
            caveats=("no_anchor_and_no_symbol_convention",),
        )

    caveats = [
        "anchor_not_captured_at_evaluation_time",
        "price_recovered_from_daily_bars_not_from_the_5m_series_the_evaluation_saw",
    ]
    if convention.fallback_symbols:
        # Gold is the live case: XAUUSD=X falls back to GC=F, and a legacy
        # record does not say which one served it. Two different instruments
        # could be behind the same observation.
        caveats.append("symbol_uncertain_fallback_configured")

    return AnchorResolution(
        status=AnchorStatus.RECONSTRUCTED,
        symbol=convention.symbol,
        invert=bool(convention.invert),
        anchor=anchor,
        legacy_execution_price=_legacy_execution_price(record),
        caveats=tuple(caveats),
    )
