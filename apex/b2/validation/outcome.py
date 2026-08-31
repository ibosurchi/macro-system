"""Architecture B2 -- Stage D-2 outcome vocabulary.

Six ORTHOGONAL axes. This module defines the states and the rules that hold
between them; it computes nothing. Computation is D-2C.

The reason for six axes rather than a verdict is that "right or wrong" destroys
the distinctions the architecture spent Stages A-C building:

*   A correct macro thesis with a bad entry is not the same failure as a wrong
    thesis, so Direction and Execution are separate axes.
*   A stopped-out setup on an intact thesis is not a directional failure, so
    setup invalidation and thesis invalidation are separate FIELDS, not two
    values of one field.
*   Missing data is not failure, unavailable is not neutral, and neutral is not
    failed -- so each has its own state, and none of them is spelled the same
    way as a wrong prediction.
*   A reconstructed anchor is not a captured one, so eligibility is its own
    dimension and cannot be inferred from the outcome.

Two invariants are enforced structurally rather than by convention, because
both are rules the operator stated as non-negotiable:

    NOT_MATURED       -> direction MUST be UNRESOLVED
    INSUFFICIENT_DATA -> direction MUST NOT be CONFIRMED or FAILED

``OutcomeAxes`` validates them in ``__post_init__``, so an outcome that breaks
either cannot be constructed at all -- there is no path that produces one and
relies on a later check to catch it.

This module is pure. It performs no I/O and reads no market data.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class DataResolution(Enum):
    """Whether the market data needed to judge this observation exists."""

    RESOLVED = "resolved"                    # window complete, evidence usable
    PARTIAL = "partial"                      # window elapsed, a genuine gap inside it
    INSUFFICIENT_DATA = "insufficient_data"  # elapsed, nothing usable arrived
    UNAVAILABLE = "unavailable"              # cannot be judged at all (no anchor/series)
    NOT_MATURED = "not_matured"              # forward time has not elapsed yet

    @property
    def permits_direction(self) -> bool:
        """Only a resolved or partial window may carry a directional verdict."""
        return self in (DataResolution.RESOLVED, DataResolution.PARTIAL)


class DirectionOutcome(Enum):
    """Did the stated directional claim behave correctly over its horizon?"""

    CONFIRMED = "confirmed"
    FAILED = "failed"
    #: Data present, move inside the noise band. Not a failure -- an honest "no
    #: material move", which is different from being wrong.
    NEUTRAL_WITHIN_BAND = "neutral_within_band"
    #: B2 declined to make a directional claim. A FEATURE, never a miss:
    #: penalising abstention would push the system toward false confidence.
    ABSTAINED = "abstained"
    #: Cannot be judged yet, or the data does not permit it.
    UNRESOLVED = "unresolved"
    #: There was no directional claim to judge (claim was UNAVAILABLE).
    NOT_APPLICABLE = "not_applicable"

    @property
    def is_verdict(self) -> bool:
        """Whether this state asserts the claim was right or wrong."""
        return self in (DirectionOutcome.CONFIRMED, DirectionOutcome.FAILED)


class SetupInvalidation(Enum):
    """Did the TECHNICAL setup fail? Price-derived."""

    NOT_INVALIDATED = "not_invalidated"
    INVALIDATED = "invalidated"
    UNKNOWN = "unknown"
    NOT_APPLICABLE = "not_applicable"   # no invalidation level was defined


class ThesisInvalidation(Enum):
    """Did the MACRO thesis fail? EVIDENCE-derived, never price-derived.

    Deliberately a separate field from ``SetupInvalidation`` rather than another
    value of it. Price can raise a question about a thesis; it cannot answer
    one. Only later macro evidence can move this axis -- which is the guarantee
    ``thesis.apply_macro_evidence`` exists to enforce, preserved here.
    """

    NOT_INVALIDATED = "not_invalidated"
    INVALIDATED = "invalidated"
    UNKNOWN = "unknown"
    #: No later observations exist to replay macro evidence over.
    NOT_ASSESSABLE = "not_assessable"


class ExecutionOutcome(Enum):
    """Was the ENTRY-QUALITY judgment right, relative to invalidation?

    Judged independently of Direction. A thesis can be right and the entry
    terrible; a deferral can be correct while the direction was right.
    """

    ENTRY_JUSTIFIED = "entry_justified"
    ENTRY_PREMATURE = "entry_premature"
    ENTRY_LATE = "entry_late"
    #: Execution was blocked/vetoed and acting would have lost. The gate worked.
    DEFERRAL_CORRECT = "deferral_correct"
    #: Execution was blocked and acting would have won. This is what proves a
    #: gate is reducing false confidence rather than merely cutting coverage.
    DEFERRAL_COSTLY = "deferral_costly"
    NOT_APPLICABLE = "not_applicable"   # no invalidation defined -> not judgeable
    UNRESOLVED = "unresolved"


class EligibilityPool(Enum):
    """Which evidence pool this outcome may ever be used in.

    The captured pool is the ONLY one a future calibration claim may draw on. A
    reconstructed anchor or a substituted market series is real research
    evidence, but it is not a point-in-time observation of the series the
    evaluation actually saw -- so it is tiered here rather than filtered out,
    because silent exclusion and silent inclusion are both dishonest.
    """

    CAPTURED = "captured"
    RECONSTRUCTED_RESEARCH = "reconstructed_research"
    EXCLUDED = "excluded"

    @property
    def permits_calibration(self) -> bool:
        return self is EligibilityPool.CAPTURED


class ExclusionReason(Enum):
    """Why an observation could not be validated. Always recorded, never dropped."""

    ANCHOR_MISSING = "anchor_missing"
    SERIES_UNAVAILABLE = "series_unavailable"
    GRANULARITY_MISMATCH = "granularity_mismatch"
    INVERSION_MISMATCH = "inversion_mismatch"
    BAD_TIMESTAMP = "bad_timestamp"
    NO_BARS_AFTER_MATURITY = "no_bars_after_maturity"
    COVERAGE_GAP = "coverage_gap"
    WINDOW_OPEN = "window_open"
    ANCHOR_PRICE_UNUSABLE = "anchor_price_unusable"
    UNKNOWN_HORIZON = "unknown_horizon"
    SERIES_SUBSTITUTION_DISALLOWED = "series_substitution_disallowed"
    RECONSTRUCTED_ANCHOR_DISALLOWED = "reconstructed_anchor_disallowed"


@dataclass(frozen=True)
class ExcursionMeasures:
    """Path measurements. MEASUREMENTS, deliberately not scores.

    Recording these without collapsing them into a number is the point: what a
    favourable excursion is worth depends on the invalidation distance, which is
    an execution question, not a directional one. Turning them into a score here
    would answer that question by accident.

    All values are in the instrument's STRENGTH convention, so "up" always means
    "bullish for this instrument" for inverted pairs too.
    """

    terminal_return: float | None = None
    mfe: float | None = None                 # max favourable excursion, signed toward the claim
    mae: float | None = None                 # max adverse excursion, signed against the claim
    mfe_atr: float | None = None             # same, in anchor-ATR units
    mae_atr: float | None = None
    bars_to_mfe: int | None = None
    bars_to_mae: int | None = None
    path_bars: int = 0

    def as_record(self) -> dict[str, Any]:
        return {
            "terminal_return": self.terminal_return,
            "mfe": self.mfe,
            "mae": self.mae,
            "mfe_atr": self.mfe_atr,
            "mae_atr": self.mae_atr,
            "bars_to_mfe": self.bars_to_mfe,
            "bars_to_mae": self.bars_to_mae,
            "path_bars": self.path_bars,
        }


class OutcomeInvariantError(ValueError):
    """Raised when an outcome would break a rule that must never be breakable."""


@dataclass(frozen=True)
class OutcomeAxes:
    """The six orthogonal axes of one validation outcome.

    No axis is a score. No axis collapses into another. Constructing a set that
    breaks an invariant raises rather than being quietly corrected, because a
    corrected invariant violation is an invariant violation nobody sees.
    """

    data_resolution: DataResolution
    direction: DirectionOutcome
    setup_invalidation: SetupInvalidation
    thesis_invalidation: ThesisInvalidation
    execution: ExecutionOutcome
    excursion: ExcursionMeasures
    eligibility_pool: EligibilityPool
    exclusion_reason: ExclusionReason | None = None

    def __post_init__(self) -> None:
        # INVARIANT 1 -- an immature observation has no verdict of any kind.
        # A tactical claim one day into a fourteen-day window has not failed;
        # it has not been judged. Collapsing the two would manufacture failures
        # out of the passage of time.
        if (
            self.data_resolution is DataResolution.NOT_MATURED
            and self.direction is not DirectionOutcome.UNRESOLVED
        ):
            raise OutcomeInvariantError(
                "NOT_MATURED requires direction UNRESOLVED, got "
                f"{self.direction.value}. An observation whose window has not "
                "elapsed has not been judged, and must never be recorded as "
                "confirmed or failed."
            )

        # INVARIANT 2 -- missing data is not evidence about the claim.
        if (
            self.data_resolution
            in (DataResolution.INSUFFICIENT_DATA, DataResolution.UNAVAILABLE)
            and self.direction.is_verdict
        ):
            raise OutcomeInvariantError(
                f"{self.data_resolution.value} cannot carry a directional "
                f"verdict, got {self.direction.value}. Absent market data says "
                "nothing about whether the claim was right."
            )

        # INVARIANT 3 -- an excluded outcome must say why, and a non-excluded
        # one must not carry a reason it did not act on.
        if (
            self.eligibility_pool is EligibilityPool.EXCLUDED
            and self.exclusion_reason is None
        ):
            raise OutcomeInvariantError(
                "An EXCLUDED outcome must record its exclusion reason. Silent "
                "exclusion is how a denominator shrinks without anyone noticing."
            )

        # INVARIANT 4 -- the captured pool admits nothing it did not observe.
        # Enforced again downstream, and mirrored by a database CHECK when
        # outcomes are eventually persisted.
        if (
            self.eligibility_pool is EligibilityPool.CAPTURED
            and self.data_resolution is DataResolution.UNAVAILABLE
        ):
            raise OutcomeInvariantError(
                "An UNAVAILABLE observation cannot be in the captured pool."
            )

    @property
    def is_calibration_eligible(self) -> bool:
        """Whether this outcome may ever inform a calibration claim.

        Requires BOTH a captured pool and an actual verdict. Neither alone is
        enough: a captured abstention is not evidence of accuracy, and a verdict
        from a reconstructed anchor is not evidence about the live system.
        """
        return self.eligibility_pool.permits_calibration and self.direction.is_verdict

    def as_record(self) -> dict[str, Any]:
        return {
            "data_resolution": self.data_resolution.value,
            "direction_outcome": self.direction.value,
            "setup_invalidation": self.setup_invalidation.value,
            "thesis_invalidation": self.thesis_invalidation.value,
            "execution_outcome": self.execution.value,
            "eligibility_pool": self.eligibility_pool.value,
            "exclusion_reason": (
                self.exclusion_reason.value if self.exclusion_reason else None
            ),
            "calibration_eligible": self.is_calibration_eligible,
            **self.excursion.as_record(),
        }


def unresolved_axes(
    *,
    data_resolution: DataResolution,
    eligibility_pool: EligibilityPool,
    exclusion_reason: ExclusionReason | None = None,
) -> OutcomeAxes:
    """The canonical shape of an outcome that could not be judged.

    Provided so every non-judgeable path produces the SAME shape rather than
    each caller inventing its own combination of "not applicable" values. A
    validation run must be able to count its exclusions, and it can only do that
    if they look alike.
    """
    return OutcomeAxes(
        data_resolution=data_resolution,
        direction=DirectionOutcome.UNRESOLVED,
        setup_invalidation=SetupInvalidation.UNKNOWN,
        thesis_invalidation=ThesisInvalidation.NOT_ASSESSABLE,
        execution=ExecutionOutcome.UNRESOLVED,
        excursion=ExcursionMeasures(),
        eligibility_pool=eligibility_pool,
        exclusion_reason=exclusion_reason,
    )


__all__ = [
    "DataResolution",
    "DirectionOutcome",
    "EligibilityPool",
    "ExclusionReason",
    "ExcursionMeasures",
    "ExecutionOutcome",
    "OutcomeAxes",
    "OutcomeInvariantError",
    "SetupInvalidation",
    "ThesisInvalidation",
    "unresolved_axes",
]
