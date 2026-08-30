"""Architecture B2 -- Regime as a shared meta-state.

Regime is not an ordinary directional vote. It is a state that many layers read
concurrently, and it modifies interpretation, thresholds and risk treatment --
never direction. ``RegimeReading`` deliberately exposes no direction field and
is never passed to the aggregator.

Two values are kept separate, as required: the **Regime State** and the
**Regime Confidence** in that state.

The state space is kept to three (plus an explicit Unavailable) because
regime-conditional parameters multiply the parameter count while dividing the
sample available per parameter. Trending / Range / Stress is the smallest set
that still distinguishes the cases the rest of the architecture cares about.

Classification is strictly **causal**: it reads only values supplied for the
current timestamp. Nothing here looks at a full sample, and no label is ever
assigned with knowledge of what happened later -- that is the subtlest leakage
vector in the whole design.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from .enums import ConfidenceLevel, Direction, FamilyState
from .families import FamilyReading
from .horizons import utcnow

#: Minimum resolved transmission steps before the prediction log is allowed to
#: say anything about regime confidence at all.
MIN_TRANSMISSION_SAMPLE = 5

#: Below this confirmation rate the mechanisms the system predicted are not
#: operating, which is a reason to trust the regime read less.
TRANSMISSION_FAILURE_RATE = 0.5


class RegimeState(Enum):
    TRENDING = "trending"
    RANGE = "range"
    STRESS = "stress"
    UNAVAILABLE = "unavailable"   # not the same as "range"


@dataclass(frozen=True)
class RegimeReading:
    """A regime state plus a separate confidence in it. Carries no direction."""

    state: RegimeState
    confidence: ConfidenceLevel
    evidence: tuple[str, ...]
    rationale: str
    observed_at: datetime

    @property
    def is_stress(self) -> bool:
        return self.state is RegimeState.STRESS

    @property
    def is_available(self) -> bool:
        return self.state is not RegimeState.UNAVAILABLE

    def as_record(self) -> dict[str, object]:
        return {
            "regime_state": self.state.value,
            "regime_confidence": self.confidence.name,
            "evidence": list(self.evidence),
            "rationale": self.rationale,
            "observed_at": self.observed_at.isoformat(),
        }


def classify_regime(
    *,
    volatility_regime: str = "unavailable",
    readings: tuple[FamilyReading, ...] = (),
    candidate_direction: Direction = Direction.FLAT,
    transmission_rate: float | None = None,
    transmission_sample: int = 0,
    observed_at: datetime | None = None,
    technical_keys: frozenset[str] = frozenset(),
) -> RegimeReading:
    """Classify the regime from information available at this timestamp only.

    ``transmission_rate`` is the confirmation rate from the prediction log. It
    is the single quantitative channel that log has into the system, and it may
    only *lower* regime confidence -- raising confidence off a handful of
    confirmations would be exactly the false precision this architecture rules
    out.
    """
    moment = observed_at or utcnow()
    evidence: list[str] = []

    volatility_known = volatility_regime in {"compression", "normal", "expansion"}
    if volatility_known:
        evidence.append(f"volatility_regime={volatility_regime}")

    technical = tuple(r for r in readings if r.family_key in technical_keys)
    directional = next((r for r in technical if r.family_key == "directional"), None)
    directional_available = directional is not None and directional.is_available
    if directional_available:
        evidence.append(
            f"directional={directional.direction.value}/{directional.strength.name}"
        )

    structure = next((r for r in technical if r.family_key == "structure"), None)
    structure_available = structure is not None and structure.is_available
    if structure_available:
        evidence.append(f"structure={structure.direction.value}")

    disagreement = any(
        r.state_against(candidate_direction) is FamilyState.CONFLICTS for r in readings
    )
    if disagreement:
        evidence.append("family_disagreement=True")

    available_inputs = sum(
        (volatility_known, directional_available, structure_available)
    )

    if available_inputs == 0:
        return RegimeReading(
            state=RegimeState.UNAVAILABLE,
            confidence=ConfidenceLevel.LOW,
            evidence=tuple(evidence),
            rationale=(
                "No regime input was available. This is Unavailable, not Range -- "
                "the system does not know which regime it is in."
            ),
            observed_at=moment,
        )

    if volatility_regime == "expansion" and disagreement:
        state = RegimeState.STRESS
        rationale = (
            "Volatility is expanding while factor families disagree: the "
            "relationships the system relies on may not be operating. Note that "
            "this is a conditioning state, not a bearish read."
        )
    elif (
        directional_available
        and directional.direction.is_directional
        and directional.strength.value >= 2
    ):
        state = RegimeState.TRENDING
        rationale = (
            f"The directional family carries a {directional.strength.name.lower()} "
            f"{directional.direction.value} read without stress conditions."
        )
    else:
        state = RegimeState.RANGE
        rationale = (
            "No sustained directional read and no stress signature; conditions "
            "read as range-bound."
        )

    if available_inputs >= 3:
        confidence = ConfidenceLevel.HIGH
    elif available_inputs == 2:
        confidence = ConfidenceLevel.MODERATE
    else:
        confidence = ConfidenceLevel.LOW

    if (
        transmission_rate is not None
        and transmission_sample >= MIN_TRANSMISSION_SAMPLE
        and transmission_rate < TRANSMISSION_FAILURE_RATE
    ):
        confidence = confidence.capped_at(ConfidenceLevel.LOW)
        evidence.append(
            f"transmission_confirmation_rate={transmission_rate:.2f}"
            f" over n={transmission_sample}"
        )
        rationale += (
            " Regime confidence is capped: predicted transmission steps are mostly "
            "failing to occur, so the mechanism assumed by this read is in doubt."
        )

    return RegimeReading(
        state=state,
        confidence=confidence,
        evidence=tuple(evidence),
        rationale=rationale,
        observed_at=moment,
    )
