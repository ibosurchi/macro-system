"""Architecture B2 -- explicit horizon separation.

Three horizons, each carrying its own claim, its own timestamp and its own
evaluation deadline, so each can be validated separately:

*   STRUCTURAL -- weeks to months
*   TACTICAL   -- days to weeks (the primary macro thesis horizon)
*   EXECUTION  -- hours to days

Two rules are enforced here rather than left to discipline.

**Frequency-relative staleness.** A three-week-old monthly release is normal; a
three-week-old daily series is broken. Staleness is therefore judged against
each series' own publication period, never against a single global timeout.

**No frequency smuggling.** ``assert_horizon_compatible`` refuses to let a
quarterly series inform an execution-horizon decision. Mixing a structural
release into an intraday decision as though it had the same frequency is the
horizon-mismatch failure this module exists to prevent.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum

from .enums import Direction, Horizon


class SeriesFrequency(Enum):
    """Natural publication cadence of a data series, with its period in days."""

    INTRADAY = ("intraday", 1.0 / 24.0)
    DAILY = ("daily", 1.0)
    WEEKLY = ("weekly", 7.0)
    MONTHLY = ("monthly", 30.0)
    QUARTERLY = ("quarterly", 91.0)
    EVENT = ("event", 0.0)  # irregular; released on a calendar, not a cadence

    def __init__(self, label: str, period_days: float) -> None:
        self.label = label
        self.period_days = period_days


class Staleness(Enum):
    FRESH = "fresh"          # within the current publication period
    EXPECTED = "expected"    # older, but normal for this cadence
    STALE = "stale"          # overdue relative to its own cadence
    BROKEN = "broken"        # far past any plausible publication delay
    UNKNOWN = "unknown"      # no observation timestamp available


#: Which frequencies may inform which horizon.
#:
#: A series slower than the decision horizon may still *condition* it, but it
#: must not be treated as evidence arriving at the horizon's own cadence. The
#: execution horizon accepts only intraday and daily data; the tactical horizon
#: accepts up to monthly; the structural horizon accepts everything.
HORIZON_MAX_FREQUENCY: dict[Horizon, float] = {
    Horizon.EXECUTION: SeriesFrequency.DAILY.period_days,
    Horizon.TACTICAL: SeriesFrequency.MONTHLY.period_days,
    Horizon.STRUCTURAL: SeriesFrequency.QUARTERLY.period_days,
}

#: Predefined evaluation window per horizon, taken from the upper end of each
#: horizon's own stated range (hours-days / days-weeks / weeks-months). An
#: outcome is attached at this deadline and not before, so a structural claim is
#: never scored on an execution timescale or the reverse.
HORIZON_EVALUATION_WINDOW: dict[Horizon, timedelta] = {
    Horizon.EXECUTION: timedelta(days=3),
    Horizon.TACTICAL: timedelta(days=14),
    Horizon.STRUCTURAL: timedelta(days=90),
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def classify_staleness(
    observed_at: datetime | None,
    frequency: SeriesFrequency,
    now: datetime | None = None,
) -> Staleness:
    """Staleness relative to the series' own publication period."""
    if observed_at is None:
        return Staleness.UNKNOWN
    if frequency is SeriesFrequency.EVENT:
        # Event releases have no cadence to be overdue against.
        return Staleness.FRESH
    reference = now or utcnow()
    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=timezone.utc)
    age_days = (reference - observed_at).total_seconds() / 86400.0
    if age_days < 0:
        return Staleness.FRESH
    period = frequency.period_days
    if period <= 0:
        return Staleness.UNKNOWN
    if age_days <= period:
        return Staleness.FRESH
    if age_days <= 2.0 * period:
        return Staleness.EXPECTED
    if age_days <= 3.0 * period:
        return Staleness.STALE
    return Staleness.BROKEN


def is_usable(staleness: Staleness) -> bool:
    """BROKEN and UNKNOWN readings are Unavailable, not flat."""
    return staleness in (Staleness.FRESH, Staleness.EXPECTED, Staleness.STALE)


def assert_horizon_compatible(frequency: SeriesFrequency, horizon: Horizon) -> None:
    """Raise if a series is too slow to be evidence at this horizon."""
    if frequency is SeriesFrequency.EVENT:
        return
    limit = HORIZON_MAX_FREQUENCY[horizon]
    if frequency.period_days > limit:
        raise ValueError(
            f"{frequency.label} data (period {frequency.period_days:g}d) cannot be "
            f"evidence at the {horizon.value} horizon (max period {limit:g}d). "
            "Slower data may condition this horizon, but it must not be mixed in "
            "as though it arrived at the same frequency."
        )


def horizon_compatible(frequency: SeriesFrequency, horizon: Horizon) -> bool:
    try:
        assert_horizon_compatible(frequency, horizon)
    except ValueError:
        return False
    return True


def evaluation_deadline(horizon: Horizon, registered_at: datetime | None = None) -> datetime:
    """When this horizon's claim becomes due for outcome attachment."""
    start = registered_at or utcnow()
    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    return start + HORIZON_EVALUATION_WINDOW[horizon]


@dataclass(frozen=True)
class HorizonClaim:
    """One directional claim, bound to exactly one horizon and one timestamp."""

    horizon: Horizon
    direction: Direction
    claim: str
    registered_at: datetime
    evaluate_at: datetime
    evidence_keys: tuple[str, ...]

    @property
    def is_due(self) -> bool:
        return utcnow() >= self.evaluate_at

    def as_record(self) -> dict[str, object]:
        return {
            "horizon": self.horizon.value,
            "direction": self.direction.value,
            "claim": self.claim,
            "registered_at": self.registered_at.isoformat(),
            "evaluate_at": self.evaluate_at.isoformat(),
            "evidence_keys": list(self.evidence_keys),
        }


def build_claim(
    *,
    horizon: Horizon,
    direction: Direction,
    claim: str,
    evidence_keys: tuple[str, ...] = (),
    registered_at: datetime | None = None,
) -> HorizonClaim:
    """Create a horizon-bound claim with its evaluation deadline fixed up front."""
    stamped = registered_at or utcnow()
    if stamped.tzinfo is None:
        stamped = stamped.replace(tzinfo=timezone.utc)
    return HorizonClaim(
        horizon=horizon,
        direction=direction,
        claim=claim,
        registered_at=stamped,
        evaluate_at=evaluation_deadline(horizon, stamped),
        evidence_keys=tuple(evidence_keys),
    )


@dataclass(frozen=True)
class SeriesObservation:
    """A data reading with the provenance needed to judge whether it is usable."""

    key: str
    value: float | None
    frequency: SeriesFrequency
    observed_at: datetime | None
    source: str = ""

    def staleness(self, now: datetime | None = None) -> Staleness:
        return classify_staleness(self.observed_at, self.frequency, now)

    def usable_value(self, horizon: Horizon, now: datetime | None = None) -> float | None:
        """The value if it may be used at this horizon, otherwise None.

        None here propagates as Unavailable, never as a flat 0.0.
        """
        if self.value is None:
            return None
        if not horizon_compatible(self.frequency, horizon):
            return None
        if not is_usable(self.staleness(now)):
            return None
        return self.value

    def as_record(self, now: datetime | None = None) -> dict[str, object]:
        return {
            "key": self.key,
            "value": self.value,
            "frequency": self.frequency.label,
            "observed_at": self.observed_at.isoformat() if self.observed_at else None,
            "staleness": self.staleness(now).value,
            "source": self.source,
        }
