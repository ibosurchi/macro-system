"""Architecture B2 -- immutable market bars for Stage D outcome resolution.

A bar is a **public market fact**, not a property of any B2 observation. It is
therefore keyed to the market series -- ``(symbol, granularity, bar_time)`` --
and shared by every shadow observation whose forward window contains it. Keying
bars to an observation instead would store the same immutable fact once per
private observation, and would make two observations of one instrument in the
same hour each carry a near-identical copy of an overlapping forward path.

Three identities, deliberately separate, mirroring the model that Storage V2
arrived at after a live collision proved one identifier could not do all three
jobs:

``observation_id``
    PHYSICAL identity -- "is this the exact same bar?" Deterministic, so a
    re-capture reproduces it and cannot duplicate.

natural key
    The same tuple, unhashed. Asserted by the database so that if the hash basis
    were ever changed by a bug, inserts fail loudly instead of silently
    duplicating every bar in the table.

``content_hash``
    INTEGRITY identity -- "do two rows claiming one bar carry the same values?"
    A vendor revision is then reported rather than silently kept or silently
    overwritten.

This module is pure. It performs no I/O; the client that talks to the database
lives in ``apex.b2_validation_bridge``.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Sequence

#: The only price source Stage D-1 uses, and the endpoint production already
#: uses for the tactical series. Recorded on every row so a future second
#: source can never be silently pooled with this one.
BAR_PRICE_SOURCE = "yahoo_chart_v8"

GRANULARITY_1D = "1d"
GRANULARITY_5M = "5m"

#: Period length per granularity. Used to decide whether a bar has CLOSED --
#: an in-progress bar is revised after capture, and an append-only store must
#: never hold a value that is expected to change.
GRANULARITY_SECONDS: Mapping[str, int] = {
    GRANULARITY_1D: 24 * 3600,
    GRANULARITY_5M: 300,
}

#: Separator for identity basis strings. Cannot occur in an ISO timestamp, a
#: market symbol or a granularity label.
_IDENTITY_SEPARATOR = "|"

#: Guard matching production's ``np.maximum(x, 1e-12)`` in _tactical_analysis_ohlc.
_RECIPROCAL_FLOOR = 1e-12


class MarketObservationError(ValueError):
    """Raised when a bar cannot be formed honestly from the given values."""


def _utc(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def canonical_bar_time_iso(bar_time: datetime) -> str:
    """The canonical UTC ISO string for a bar's open time.

    Every identity basis and every stored row uses THIS function, never a
    caller's own formatting. Re-formatting is precisely how two semantically
    identical timestamps hash differently -- the same failure the shadow
    record's ``canonical_storage_id`` docstring warns about.
    """
    return _utc(bar_time).astimezone(timezone.utc).isoformat()


def canonical_observation_id(
    symbol: str, granularity: str, bar_time_iso: str, price_source: str
) -> str:
    """Deterministic physical identity for one market bar.

    ``bar_time_iso`` MUST come from ``canonical_bar_time_iso``. Same
    construction and same 32-hex width as the shadow record's storage id, so
    the two identity schemes read alike in a database and in a log.
    """
    basis = _IDENTITY_SEPARATOR.join(
        [str(symbol), str(granularity), str(bar_time_iso), str(price_source)]
    )
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:32]


def canonical_bar_content_hash(
    open_: float, high: float, low: float, close: float, volume: float | None
) -> str:
    """Deterministic hash of a bar's values, independent of key ordering.

    Covers only the measured values, never the identity or the capture time: two
    captures of the same closed bar must hash identically, while a genuine
    vendor revision must not.
    """
    canonical = json.dumps(
        {
            "open": float(open_),
            "high": float(high),
            "low": float(low),
            "close": float(close),
            "volume": None if volume is None else float(volume),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


def bar_is_final(
    bar_time: datetime, granularity: str, now: datetime
) -> bool:
    """Has this bar's period fully closed?

    Only closed bars may be stored. Yahoo revises an in-progress bar
    continuously, so capturing one would write a value that is expected to
    change into a store whose whole guarantee is that nothing changes -- and
    ``ON CONFLICT DO NOTHING`` would then preserve the partial version forever.
    """
    period = GRANULARITY_SECONDS.get(str(granularity))
    if not period:
        return False
    return _utc(now) >= _utc(bar_time) + timedelta(seconds=period)


def analysis_ohlc(
    open_: float, high: float, low: float, close: float, invert: bool
) -> tuple[float, float, float, float]:
    """Convert a raw quote bar into the asset-strength convention.

    This mirrors production's ``_tactical_analysis_ohlc`` exactly, including the
    high/low swap: inverting a bar turns its low into its high. Getting that
    backwards would silently corrupt every excursion metric for the seven
    USD-quoted currencies while leaving the close-to-close return correct, which
    is the kind of error that survives a casual review.

    The convention is production's own, deliberately: B2 must be validated
    against the same definition of "this instrument went up" that the
    Directional family's returns were computed from.
    """
    if not invert:
        return float(open_), float(high), float(low), float(close)
    return (
        1.0 / max(float(open_), _RECIPROCAL_FLOOR),
        1.0 / max(float(low), _RECIPROCAL_FLOOR),   # low  -> high
        1.0 / max(float(high), _RECIPROCAL_FLOOR),  # high -> low
        1.0 / max(float(close), _RECIPROCAL_FLOOR),
    )


@dataclass(frozen=True)
class MarketBar:
    """One immutable market bar, in the RAW quote convention."""

    symbol: str
    instrument: str
    granularity: str
    bar_time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float | None
    invert: bool
    price_source: str = BAR_PRICE_SOURCE
    meta: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if not str(self.symbol).strip():
            raise MarketObservationError("A market bar must name its symbol.")
        if not str(self.instrument).strip():
            raise MarketObservationError("A market bar must name its instrument.")
        if str(self.granularity) not in GRANULARITY_SECONDS:
            raise MarketObservationError(
                f"Unknown granularity {self.granularity!r}; "
                f"expected one of {sorted(GRANULARITY_SECONDS)}."
            )
        prices = (self.open, self.high, self.low, self.close)
        for value in prices:
            number = float(value)
            if number != number or number in (float("inf"), float("-inf")):
                raise MarketObservationError("A market bar price must be finite.")
            if number <= 0.0:
                raise MarketObservationError("A market bar price must be positive.")
        if self.high < max(self.open, self.close) or self.low > min(self.open, self.close):
            raise MarketObservationError(
                "Inconsistent OHLC: the high must be the highest and the low the "
                "lowest. These are the same invariants the database enforces."
            )
        if self.volume is not None and float(self.volume) < 0.0:
            raise MarketObservationError("Volume cannot be negative.")

    @property
    def bar_time_iso(self) -> str:
        return canonical_bar_time_iso(self.bar_time)

    @property
    def observation_id(self) -> str:
        return canonical_observation_id(
            self.symbol, self.granularity, self.bar_time_iso, self.price_source
        )

    @property
    def content_hash(self) -> str:
        return canonical_bar_content_hash(
            self.open, self.high, self.low, self.close, self.volume
        )

    @property
    def analysis_close(self) -> float:
        """Close in the strength convention -- what 'this instrument rose' means."""
        return analysis_ohlc(
            self.open, self.high, self.low, self.close, self.invert
        )[3]

    @property
    def analysis_high(self) -> float:
        return analysis_ohlc(self.open, self.high, self.low, self.close, self.invert)[1]

    @property
    def analysis_low(self) -> float:
        return analysis_ohlc(self.open, self.high, self.low, self.close, self.invert)[2]

    def is_final(self, now: datetime) -> bool:
        return bar_is_final(self.bar_time, self.granularity, now)

    def to_row(self) -> dict[str, Any]:
        """Map onto one ``b2_market_observations`` row.

        ``is_final`` is always True: the capture path refuses an in-progress
        bar, and the table's CHECK constraint refuses one too. Sending the
        column explicitly keeps the invariant visible in the payload rather than
        relying on a default.
        """
        return {
            "observation_id": self.observation_id,
            "symbol": self.symbol,
            "instrument": self.instrument,
            "granularity": self.granularity,
            "price_source": self.price_source,
            "bar_time": self.bar_time_iso,
            "is_final": True,
            "open": float(self.open),
            "high": float(self.high),
            "low": float(self.low),
            "close": float(self.close),
            "volume": None if self.volume is None else float(self.volume),
            "invert": bool(self.invert),
            "content_hash": self.content_hash,
            "meta": dict(self.meta) if self.meta else {},
        }


def row_to_bar(row: Mapping[str, Any] | None) -> MarketBar | None:
    """Rebuild a bar from a stored row. Returns None for anything malformed.

    A row that cannot be read back honestly is skipped rather than repaired: a
    half-formed bar in an outcome calculation is worse than a reported gap.
    """
    if not isinstance(row, Mapping):
        return None
    try:
        raw_time = row.get("bar_time")
        bar_time = (
            raw_time
            if isinstance(raw_time, datetime)
            else datetime.fromisoformat(str(raw_time).replace("Z", "+00:00"))
        )
        volume = row.get("volume")
        return MarketBar(
            symbol=str(row.get("symbol") or ""),
            instrument=str(row.get("instrument") or ""),
            granularity=str(row.get("granularity") or ""),
            bar_time=_utc(bar_time),
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            volume=None if volume is None else float(volume),
            invert=bool(row.get("invert")),
            price_source=str(row.get("price_source") or BAR_PRICE_SOURCE),
            meta=row.get("meta") if isinstance(row.get("meta"), Mapping) else None,
        )
    except (KeyError, TypeError, ValueError, MarketObservationError):
        return None


def forward_bars(
    bars: Iterable[MarketBar],
    *,
    evaluated_at: datetime,
    window: timedelta,
) -> tuple[MarketBar, ...]:
    """The bars that may resolve an observation, in time order.

    The selection rule is **strictly** ``bar_time > evaluated_at``, and the
    strictness is load-bearing. A daily bar opening at 00:00 on the day of a
    22:04 evaluation spans the evaluation moment: its high and low contain price
    action from *before* the prediction was made. Admitting it would be
    lookahead of the subtlest kind -- not a future price used as a feature, but
    a pre-prediction price used as an outcome.

    The cost is real and accepted: a 14-day window yields roughly nine or ten
    usable daily bars rather than fourteen.
    """
    start = _utc(evaluated_at)
    end = start + window
    selected = [
        bar for bar in bars if start < _utc(bar.bar_time) <= end
    ]
    selected.sort(key=lambda bar: _utc(bar.bar_time))
    return tuple(selected)


def coverage(
    bars: Sequence[MarketBar],
    *,
    evaluated_at: datetime,
    window: timedelta,
    now: datetime,
) -> dict[str, Any]:
    """Whether the forward window is covered, and how it is not.

    Distinguishes the two reasons a window can be incomplete, because they mean
    opposite things: forward time that has not yet elapsed is the system working
    as designed, while an elapsed window with missing bars is a capture failure.
    Neither may ever become an incorrect prediction.
    """
    start = _utc(evaluated_at)
    end = start + window
    reference = _utc(now)
    selected = forward_bars(bars, evaluated_at=start, window=window)
    elapsed = reference >= end

    if not selected:
        status = "unresolved_no_bars" if elapsed else "unresolved_window_open"
    elif not elapsed:
        status = "unresolved_window_open"
    elif _utc(selected[-1].bar_time) + timedelta(
        seconds=GRANULARITY_SECONDS.get(selected[-1].granularity, 0)
    ) < end - timedelta(days=1):
        status = "unresolved_coverage_gap"
    else:
        status = "resolvable"

    return {
        "status": status,
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "window_elapsed": elapsed,
        "bars": len(selected),
        "first_bar": selected[0].bar_time_iso if selected else None,
        "last_bar": selected[-1].bar_time_iso if selected else None,
    }
