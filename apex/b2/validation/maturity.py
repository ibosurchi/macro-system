"""Architecture B2 -- Stage D-2 maturity.

Maturity answers one question: **has enough forward time passed for this
observation to be judged at all?**

It exists because the alternative is the single most damaging error this whole
stage could make. A tactical claim registered one day into a fourteen-day
window has not been wrong. It has not been *looked at*. Anything that reports it
as a failure is manufacturing evidence out of the passage of time, and it would
do so systematically against the newest observations -- exactly the ones a
forward-validation programme accumulates fastest.

Four states, and the distinctions between them all carry meaning:

``NOT_MATURED``
    The window has not elapsed. No verdict of any kind is permitted.

``MATURED_AWAITING_BARS``
    The window elapsed, but the capture run has not yet reached past it. This is
    an OPERATIONAL lag -- we have not looked -- and must not be confused with
    market data being absent.

``MATURED_PARTIAL``
    The window elapsed, capture has run past it, and there is still a genuine
    gap inside the window. We looked, and the data is missing.

``MATURED``
    The window elapsed and is covered to within the series' own cadence.

``MATURED_AWAITING_BARS`` versus ``MATURED_PARTIAL`` is the distinction worth
the extra state. Collapsing them would let a capture backlog masquerade as
evidence loss, and would make a fixable operational problem look like a
permanent hole in the record.

This module is pure. It performs no I/O and holds no clock: ``now`` is always
passed in, which is what makes maturity deterministic under test.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

from .outcome import DataResolution

#: Coverage statuses produced by ``bars.coverage``. Named here so the mapping
#: from coverage to maturity is explicit rather than a chain of string literals.
COVERAGE_WINDOW_OPEN = "unresolved_window_open"
COVERAGE_NO_BARS = "unresolved_no_bars"
COVERAGE_GAP = "unresolved_coverage_gap"
COVERAGE_RESOLVABLE = "resolvable"


class MaturityState(Enum):
    """How far along its evaluation window one observation is."""

    NOT_MATURED = "not_matured"
    MATURED_AWAITING_BARS = "matured_awaiting_bars"
    MATURED_PARTIAL = "matured_partial"
    MATURED = "matured"

    @property
    def is_matured(self) -> bool:
        return self is not MaturityState.NOT_MATURED

    @property
    def permits_verdict(self) -> bool:
        """Only a matured window with usable coverage may carry a verdict.

        ``MATURED_AWAITING_BARS`` deliberately does NOT permit one: the window
        has elapsed but nobody has looked, and a verdict drawn from bars that
        were never fetched would be a verdict about our capture schedule.
        """
        return self in (MaturityState.MATURED, MaturityState.MATURED_PARTIAL)

    def to_data_resolution(self) -> DataResolution:
        """The data-resolution state this maturity implies, before any other check.

        The mapping is total and one-directional, so no caller has to reinvent
        it -- and so ``NOT_MATURED`` can never be translated into anything that
        permits a verdict.
        """
        if self is MaturityState.NOT_MATURED:
            return DataResolution.NOT_MATURED
        if self is MaturityState.MATURED_AWAITING_BARS:
            return DataResolution.INSUFFICIENT_DATA
        if self is MaturityState.MATURED_PARTIAL:
            return DataResolution.PARTIAL
        return DataResolution.RESOLVED


def _utc(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class MaturityAssessment:
    """One observation's maturity, with the arithmetic that produced it."""

    state: MaturityState
    evaluated_at: datetime
    window: timedelta
    window_end: datetime
    now: datetime
    elapsed_fraction: float
    coverage_status: str | None = None
    #: Newest bar time seen anywhere in the supplied series, used only to tell
    #: "we have not captured that far yet" from "the data is genuinely missing".
    latest_captured_bar: datetime | None = None

    @property
    def is_matured(self) -> bool:
        return self.state.is_matured

    @property
    def permits_verdict(self) -> bool:
        return self.state.permits_verdict

    def as_record(self) -> dict[str, Any]:
        return {
            "maturity_state": self.state.value,
            "evaluated_at": self.evaluated_at.isoformat(),
            "window_end": self.window_end.isoformat(),
            "window_hours": self.window.total_seconds() / 3600.0,
            "elapsed_fraction": round(self.elapsed_fraction, 6),
            "coverage_status": self.coverage_status,
            "latest_captured_bar": (
                self.latest_captured_bar.isoformat()
                if self.latest_captured_bar
                else None
            ),
            "permits_verdict": self.permits_verdict,
        }


def assess_maturity(
    *,
    evaluated_at: datetime,
    window: timedelta,
    now: datetime,
    coverage_status: str | None = None,
    latest_captured_bar: datetime | None = None,
) -> MaturityAssessment:
    """Decide whether an observation may be judged, and why.

    ``coverage_status`` is ``bars.coverage(...)["status"]``. ``latest_captured_bar``
    is the newest bar time present for the series in question -- NOT restricted
    to the window -- because it is what separates "capture has not reached this
    window yet" from "capture reached it and the bars are missing".

    A clock-skewed observation (``evaluated_at`` in the future) is NOT_MATURED
    rather than producing a negative elapsed fraction: the honest reading is
    that its window has not started, not that it is somehow over-mature.
    """
    start = _utc(evaluated_at)
    reference = _utc(now)
    end = start + window

    span = window.total_seconds()
    raw_fraction = (reference - start).total_seconds() / span if span > 0 else 0.0
    elapsed_fraction = max(0.0, raw_fraction)

    if reference < end:
        return MaturityAssessment(
            state=MaturityState.NOT_MATURED,
            evaluated_at=start,
            window=window,
            window_end=end,
            now=reference,
            elapsed_fraction=elapsed_fraction,
            coverage_status=coverage_status,
            latest_captured_bar=latest_captured_bar,
        )

    # The window has elapsed. Whether we can judge it now depends on whether we
    # have actually looked past its end.
    captured_past_window = (
        latest_captured_bar is not None and _utc(latest_captured_bar) >= end
    )

    if coverage_status == COVERAGE_RESOLVABLE:
        state = MaturityState.MATURED
    elif coverage_status in (COVERAGE_NO_BARS, COVERAGE_GAP, None):
        # No bars, or a gap. If capture has not reached past the window end,
        # this is our backlog rather than the market's absence.
        state = (
            MaturityState.MATURED_PARTIAL
            if captured_past_window and coverage_status == COVERAGE_GAP
            else MaturityState.MATURED_AWAITING_BARS
        )
    elif coverage_status == COVERAGE_WINDOW_OPEN:
        # Coverage disagrees with the clock. Trust the more conservative answer
        # rather than reconciling: an inconsistency here must not silently
        # resolve in favour of producing a verdict.
        state = MaturityState.MATURED_AWAITING_BARS
    else:
        state = MaturityState.MATURED_AWAITING_BARS

    return MaturityAssessment(
        state=state,
        evaluated_at=start,
        window=window,
        window_end=end,
        now=reference,
        elapsed_fraction=elapsed_fraction,
        coverage_status=coverage_status,
        latest_captured_bar=latest_captured_bar,
    )


__all__ = [
    "COVERAGE_GAP",
    "COVERAGE_NO_BARS",
    "COVERAGE_RESOLVABLE",
    "COVERAGE_WINDOW_OPEN",
    "MaturityAssessment",
    "MaturityState",
    "assess_maturity",
]
