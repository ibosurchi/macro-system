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
from enum import Enum
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

# ---------------------------------------------------------------------------
# CADENCE DEFAULTS -- VERSIONED RESEARCH DEFAULTS, not architectural truth.
#
# These are the single definition of the two cadence numbers. ``config.py``
# READS them and records them alongside its version rather than restating them,
# for the same reason the horizon windows are read from ``horizons.py``: a
# second copy is a second definition, and the two would eventually disagree
# without anything failing.
# ---------------------------------------------------------------------------

#: How many multiples of a series' own cadence may separate the last usable bar
#: from the window end before the window is called incomplete. At 2.5 a daily
#: series tolerates a normal weekend (a Friday close is 1.5-2 days from a
#: Sunday window end) while still catching a genuine multi-day outage.
#: RESEARCH DEFAULT -- chosen to span a weekend, not fitted to any result.
DEFAULT_MAX_GAP_MULTIPLE = 2.5

#: Bars required before a cadence may be ESTIMATED from observation. Below this
#: the estimate is refused outright rather than computed from one or two gaps.
#: RESEARCH DEFAULT.
DEFAULT_MIN_BARS_FOR_CADENCE = 5


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


def bar_close_time(bar_time: datetime, granularity: str) -> datetime | None:
    """When this bar's period ENDS.

    ``bar_time`` is the bar's OPEN. The distinction is load-bearing and is the
    reason this function exists rather than being inlined: a daily bar opening
    inside a validation window can still be CLOSING up to a full session after
    that window ends, and using its close as a terminal value would read a price
    from beyond the horizon that was actually claimed.

    Live data makes this concrete: the stored bars do not open at midnight. FX,
    CME futures and ICE futures each open at their own UTC offset, so "the bar
    for day D" is not a calendar day and cannot be treated as one.

    Returns None for an unknown granularity -- never a guessed duration.
    """
    period = GRANULARITY_SECONDS.get(str(granularity))
    if not period:
        return None
    return _utc(bar_time) + timedelta(seconds=period)


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
    def bar_close_time(self) -> datetime | None:
        """When this bar's period ends. See ``bar_close_time`` above."""
        return bar_close_time(self.bar_time, self.granularity)

    @property
    def bar_close_time_iso(self) -> str | None:
        closes = self.bar_close_time
        return canonical_bar_time_iso(closes) if closes is not None else None

    def closes_within(self, window_end: datetime) -> bool:
        """Whether this bar's period ends at or before ``window_end``.

        A bar whose close falls outside the window is not evidence about that
        window. Unknown granularity is treated as NOT within: an unmeasurable
        bar is excluded rather than admitted on an assumption.
        """
        closes = self.bar_close_time
        return closes is not None and closes <= _utc(window_end)

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

    D-2C0 note: the SELECTION rule above is unchanged, and deliberately so --
    this function's open-time contract is relied on by the D-1 read path. Only
    the tie-break was made total (``canonical_sort_key``), because two bars
    sharing a timestamp previously came back in whatever order the caller
    supplied. No bar is added or removed by that change; it removes an
    arbitrary ordering, nothing else. Unlike ``path_bars`` this function does
    NOT deduplicate or withhold conflicts, because its job is "which bars came
    after the prediction", not "which bars are usable evidence".
    """
    start = _utc(evaluated_at)
    end = start + window
    selected = [
        bar for bar in bars if start < _utc(bar.bar_time) <= end
    ]
    selected.sort(key=canonical_sort_key)
    return tuple(selected)


def canonical_sort_key(bar: MarketBar) -> tuple[datetime, str]:
    """The TOTAL ordering key for market bars: ``(bar_time, observation_id)``.

    ``bar_time`` alone is not a total order. Python's sort is stable, so two
    bars sharing a timestamp keep whatever order the caller happened to supply
    -- which made ``terminal_bar`` depend on input ordering rather than on the
    market. Appending ``observation_id`` makes the order total and independent
    of the caller.

    ``captured_at`` is deliberately NOT a tiebreaker. When we happened to fetch
    a bar says nothing about market time, and using it would make the answer
    depend on our capture schedule.
    """
    return (_utc(bar.bar_time), bar.observation_id)


@dataclass(frozen=True)
class BarConflict:
    """Two bars claiming one physical identity with DIFFERENT values.

    An append-only store cannot arbitrate this: both rows assert they are the
    same bar, and they disagree about what the market did. Neither may be
    chosen, so the conflict is carried out to the caller intact.
    """

    observation_id: str
    symbol: str
    granularity: str
    bar_time: datetime
    content_hashes: tuple[str, ...]

    def as_record(self) -> dict[str, Any]:
        return {
            "observation_id": self.observation_id,
            "symbol": self.symbol,
            "granularity": self.granularity,
            "bar_time": canonical_bar_time_iso(self.bar_time),
            "content_hashes": list(self.content_hashes),
        }


@dataclass(frozen=True)
class BarCanonicalization:
    """One deterministic view of a bar set, with everything it had to resolve."""

    #: Deduplicated, totally ordered, conflict-free bars.
    bars: tuple[MarketBar, ...]
    #: Identical re-captures collapsed. Not an error: the store is append-only
    #: and a repeated capture of a closed bar is expected.
    duplicates_collapsed: int
    #: Physical identities carrying contradictory values. Surfaced, never
    #: arbitrated. Their bars are absent from ``bars``.
    conflicts: tuple[BarConflict, ...]

    @property
    def has_conflict(self) -> bool:
        return bool(self.conflicts)

    @property
    def conflicting_observation_ids(self) -> tuple[str, ...]:
        return tuple(c.observation_id for c in self.conflicts)

    def as_record(self) -> dict[str, Any]:
        return {
            "bars": len(self.bars),
            "duplicates_collapsed": self.duplicates_collapsed,
            "conflicts": [c.as_record() for c in self.conflicts],
            "has_conflict": self.has_conflict,
        }


def canonicalize_bars(bars: Iterable[MarketBar]) -> BarCanonicalization:
    """Reduce a bar set to one deterministic, conflict-free sequence.

    Three distinct situations, kept distinct:

    *   **Identical duplicate** -- same ``observation_id`` AND same
        ``content_hash``. Idempotent re-capture of one immutable bar. Collapsed
        to a single bar and counted. It must not inflate a path-bar count, move
        a terminal selection, or shift a later excursion index.

    *   **Conflicting duplicate** -- same ``observation_id``, DIFFERENT
        ``content_hash``. Two payloads assert they are the same bar and
        disagree. There is no honest way to choose: last-write-wins, newest
        ``captured_at`` and averaging are all fabrication. The identity is
        reported as a conflict and ALL of its bars are withheld, so no caller
        can accidentally use one.

    *   **Everything else** -- ordered by ``canonical_sort_key``.

    Never raises, never mutates the input, and reads no clock.
    """
    grouped: dict[str, list[MarketBar]] = {}
    for bar in bars:
        grouped.setdefault(bar.observation_id, []).append(bar)

    kept: list[MarketBar] = []
    conflicts: list[BarConflict] = []
    duplicates = 0

    for observation_id, group in grouped.items():
        hashes = {bar.content_hash for bar in group}
        if len(hashes) > 1:
            first = group[0]
            conflicts.append(
                BarConflict(
                    observation_id=observation_id,
                    symbol=first.symbol,
                    granularity=first.granularity,
                    bar_time=_utc(first.bar_time),
                    # Sorted so the conflict record itself is order-independent.
                    content_hashes=tuple(sorted(hashes)),
                )
            )
            continue
        duplicates += len(group) - 1
        kept.append(group[0])

    kept.sort(key=canonical_sort_key)
    conflicts.sort(key=lambda c: (c.bar_time, c.observation_id))
    return BarCanonicalization(
        bars=tuple(kept),
        duplicates_collapsed=duplicates,
        conflicts=tuple(conflicts),
    )


@dataclass(frozen=True)
class RowConversion:
    """Rows read back from storage, split into usable bars and skipped rows.

    ``row_to_bar`` already returns ``None`` for anything malformed, which is the
    correct behaviour -- a half-formed bar in an outcome calculation is worse
    than a reported gap. What was missing is the COUNT: a silently skipped row
    is indistinguishable from a row that never existed, and a validation run has
    to be able to say how much it discarded.
    """

    bars: tuple[MarketBar, ...]
    malformed: int

    def as_record(self) -> dict[str, Any]:
        return {"bars": len(self.bars), "malformed_skipped": self.malformed}


def bars_from_rows(rows: Iterable[Mapping[str, Any]]) -> RowConversion:
    """Convert stored rows to bars, counting the ones that could not be read.

    A malformed row is skipped and counted. It is never repaired, never given
    substituted values, and never turned into a directional failure.
    """
    converted: list[MarketBar] = []
    malformed = 0
    for row in rows:
        bar = row_to_bar(row)
        if bar is None:
            malformed += 1
            continue
        converted.append(bar)
    return RowConversion(bars=tuple(converted), malformed=malformed)


def path_bars(
    bars: Iterable[MarketBar],
    *,
    evaluated_at: datetime,
    window: timedelta,
) -> tuple[MarketBar, ...]:
    """The bars that may be used as OUTCOME EVIDENCE for one observation.

    Stricter than ``forward_bars`` at BOTH ends, and deliberately a separate
    function rather than a change to it. ``forward_bars`` bounds by bar OPEN
    time, which is the correct rule for asking "which bars came after the
    prediction". This asks a different question -- "which bars are wholly
    contained in the claimed horizon" -- and needs both:

        bar_time       >  evaluated_at     (nothing straddling the prediction)
        bar_close_time <= window_end       (nothing closing beyond the horizon)

    The second condition is the one ``forward_bars`` lacks. Without it a daily
    bar opening one minute before the window end contributes a close taken up
    to a full session later, which quietly extends every horizon the system
    claims to be testing.

    Bars of unknown granularity are excluded: their close cannot be located, so
    whether they belong is unknowable rather than assumed.

    The result is CANONICAL: identical re-captures are collapsed so they cannot
    inflate the count, conflicting identities are withheld because no honest
    choice exists between them, and ordering is total via
    ``canonical_sort_key``. Supplying the same logical bars in any order yields
    the same sequence. Use ``canonicalize_bars`` directly when the conflict and
    duplicate accounting itself is needed.
    """
    start = _utc(evaluated_at)
    end = start + window
    eligible = [
        bar
        for bar in bars
        if start < _utc(bar.bar_time) and bar.closes_within(end)
    ]
    return canonicalize_bars(eligible).bars


def terminal_bar(
    bars: Iterable[MarketBar],
    *,
    evaluated_at: datetime,
    window: timedelta,
) -> MarketBar | None:
    """The last bar wholly inside the horizon -- the terminal value's source.

    None when the window contains no usable bar. None means unresolved; it is
    never a zero return and never a failed prediction.
    """
    selected = path_bars(bars, evaluated_at=evaluated_at, window=window)
    return selected[-1] if selected else None


class CadenceBasis(Enum):
    """How a series' cadence was arrived at. Never left implicit."""

    #: Measured from the series' own inter-bar gaps.
    OBSERVED_MEDIAN = "observed_median"
    #: Too few bars to measure. The estimate is REFUSED, not approximated.
    INSUFFICIENT_HISTORY = "insufficient_history"
    #: The granularity itself is unknown, so no period can be established.
    UNKNOWN_GRANULARITY = "unknown_granularity"


@dataclass(frozen=True)
class CadenceEstimate:
    """A series' observed publication cadence, or an explicit refusal."""

    seconds: float | None
    basis: CadenceBasis
    samples: int

    @property
    def is_known(self) -> bool:
        return self.seconds is not None and self.seconds > 0.0

    def as_record(self) -> dict[str, Any]:
        return {
            "cadence_seconds": self.seconds,
            "cadence_basis": self.basis.value,
            "cadence_samples": self.samples,
        }


def _median(values: Sequence[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return float(ordered[middle])
    return (float(ordered[middle - 1]) + float(ordered[middle])) / 2.0


def estimate_cadence(
    bars: Sequence[MarketBar],
    *,
    min_bars: int = DEFAULT_MIN_BARS_FOR_CADENCE,
) -> CadenceEstimate:
    """Estimate one series' publication cadence from its own bars.

    The MEDIAN inter-bar gap, not the mean: a weekend triples the Friday gap
    and a holiday can quadruple it, and a mean would let those outliers inflate
    the cadence until a genuine outage looked normal. The median answers the
    question that actually matters -- "how often does this series usually
    print" -- and is unmoved by a handful of long gaps.

    Below ``min_bars`` the estimate is REFUSED rather than computed. Two bars
    would produce a single gap that is as likely to be a weekend as a cadence,
    and a wrong cadence is worse than an absent one: it would silently pass or
    silently fail every coverage check that followed.

    Callers must pass ONE series (one symbol at one granularity). Mixing series
    would measure the interleaving, not any real cadence.
    """
    usable = [b for b in bars if b.granularity in GRANULARITY_SECONDS]
    if not usable:
        return CadenceEstimate(None, CadenceBasis.UNKNOWN_GRANULARITY, 0)

    ordered = sorted({_utc(b.bar_time) for b in usable})
    gaps = [
        (later - earlier).total_seconds()
        for earlier, later in zip(ordered, ordered[1:])
        if (later - earlier).total_seconds() > 0
    ]

    if len(ordered) < max(2, int(min_bars)) or not gaps:
        return CadenceEstimate(
            None, CadenceBasis.INSUFFICIENT_HISTORY, len(gaps)
        )

    return CadenceEstimate(_median(gaps), CadenceBasis.OBSERVED_MEDIAN, len(gaps))


def coverage(
    bars: Sequence[MarketBar],
    *,
    evaluated_at: datetime,
    window: timedelta,
    now: datetime,
    max_gap_multiple: float = DEFAULT_MAX_GAP_MULTIPLE,
    min_bars_for_cadence: int = DEFAULT_MIN_BARS_FOR_CADENCE,
) -> dict[str, Any]:
    """Whether the forward window is covered, and how it is not.

    Distinguishes the reasons a window can be incomplete, because they mean
    opposite things: forward time that has not yet elapsed is the system working
    as designed, while an elapsed window with missing bars is a capture failure.
    Neither may ever become an incorrect prediction.

    **Coverage is judged against the series' OWN cadence, never against the
    calendar.** The previous rule allowed the last bar to sit up to one wall
    clock day short of the window end. That silently failed every window ending
    on a Sunday: the last bar is Friday's, which is more than a day from a
    Sunday close, so a complete FX history was reported as a gap. Because the
    shadow daemon observes hourly, that removed roughly one day in seven of all
    evidence -- and removed it on a CALENDAR-CORRELATED basis, which is a
    sampling bias rather than a random loss.

    Measuring the trailing gap in multiples of the series' own median inter-bar
    gap absorbs weekends and holidays without a holiday calendar, and without
    this project inventing one it does not have.

    When cadence cannot be measured the estimate is refused (see
    ``estimate_cadence``) and the granularity's own period is used as a
    STRUCTURAL LOWER BOUND -- a ``1d`` series cannot print more often than daily
    by definition. That is a property of the granularity, not an inference about
    the market, and ``cadence_basis`` records which of the two was used so a
    reader is never left to assume.
    """
    start = _utc(evaluated_at)
    end = start + window
    reference = _utc(now)
    # Outcome evidence, so the close-bounded rule applies: a bar closing beyond
    # the horizon is not evidence about that horizon.
    selected = path_bars(bars, evaluated_at=start, window=window)
    elapsed = reference >= end

    cadence = estimate_cadence(list(bars), min_bars=min_bars_for_cadence)
    tolerance_seconds: float | None = None
    trailing_gap_seconds: float | None = None

    if not selected:
        status = "unresolved_no_bars" if elapsed else "unresolved_window_open"
    elif not elapsed:
        status = "unresolved_window_open"
    else:
        last_close = selected[-1].bar_close_time
        if last_close is None:
            status = "unresolved_coverage_gap"
        else:
            if cadence.is_known:
                cadence_seconds = float(cadence.seconds)
            else:
                # Structural lower bound from the granularity itself.
                cadence_seconds = float(
                    GRANULARITY_SECONDS.get(selected[-1].granularity, 0)
                )
            if cadence_seconds <= 0:
                status = "unresolved_coverage_gap"
            else:
                tolerance_seconds = float(max_gap_multiple) * cadence_seconds
                trailing_gap_seconds = (end - last_close).total_seconds()
                status = (
                    "resolvable"
                    if trailing_gap_seconds <= tolerance_seconds
                    else "unresolved_coverage_gap"
                )

    return {
        "status": status,
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "window_elapsed": elapsed,
        "bars": len(selected),
        "first_bar": selected[0].bar_time_iso if selected else None,
        "last_bar": selected[-1].bar_time_iso if selected else None,
        "last_bar_close": (
            selected[-1].bar_close_time_iso if selected else None
        ),
        "trailing_gap_seconds": trailing_gap_seconds,
        "tolerance_seconds": tolerance_seconds,
        "max_gap_multiple": float(max_gap_multiple),
        **cadence.as_record(),
    }
