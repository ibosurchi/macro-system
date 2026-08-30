"""Architecture B2 -- the execution layer, separated from the thesis.

A strong macro thesis plus strong technical confirmation does not mean the
current entry is good. Execution asks a different question: given where price
is now, is this a good place to act?

Two structural rules are enforced here.

*   **Invalidation is defined before entry quality is assessed.** Entry quality
    depends on distance to invalidation, so ``assess_execution`` takes the
    invalidation level as a required keyword argument and returns a degraded,
    blocked assessment if it is absent. A caller cannot obtain an entry-quality
    verdict without having first supplied an invalidation.

*   **Volatility conditions execution, never direction.** Volatility appears in
    this module only as a ceiling on execution confidence and an input to
    sizing. There is no branch anywhere in this package where high volatility
    implies a bearish (or bullish) read.

The entry location is always a **zone**, never a single price: a point entry
would imply a precision no model here possesses.
"""
from __future__ import annotations

from dataclasses import dataclass

from .enums import ConfidenceLevel, GateAction
from .gates import GateOutcome

#: Volatility regime labels, matching the vocabulary the live entry-plan layer
#: already produces (compression / normal / expansion / unavailable).
VOLATILITY_REGIMES = frozenset({"compression", "normal", "expansion", "unavailable"})


@dataclass(frozen=True)
class ExecutionAssessment:
    """Execution quality. An output of the execution layer, not an input to the thesis."""

    invalidation_defined: bool
    invalidation_level: float | None
    entry_zone: tuple[float, float] | None
    current_price: float | None
    invalidation_distance: float | None
    invalidation_distance_atr: float | None
    room_to_opposing_atr: float | None
    asymmetry_ratio: float | None
    volatility_regime: str
    in_zone: bool
    extended: bool
    execution_confidence: ConfidenceLevel
    blocked: bool
    block_reason: str
    notes: tuple[str, ...]

    @property
    def deferred_not_invalidated(self) -> bool:
        """True when execution is blocked by a gate but the setup itself stands."""
        return self.blocked and self.invalidation_defined and not self.extended

    def as_record(self) -> dict[str, object]:
        return {
            "invalidation_defined": self.invalidation_defined,
            "invalidation_level": self.invalidation_level,
            "entry_zone_low": self.entry_zone[0] if self.entry_zone else None,
            "entry_zone_high": self.entry_zone[1] if self.entry_zone else None,
            "current_price": self.current_price,
            "invalidation_distance": self.invalidation_distance,
            "invalidation_distance_atr": self.invalidation_distance_atr,
            "room_to_opposing_atr": self.room_to_opposing_atr,
            "asymmetry_ratio": self.asymmetry_ratio,
            "volatility_regime": self.volatility_regime,
            "in_zone": self.in_zone,
            "extended": self.extended,
            "execution_confidence": self.execution_confidence.name,
            "blocked": self.blocked,
            "block_reason": self.block_reason,
            "notes": list(self.notes),
        }


def _undefined_invalidation(volatility_regime: str) -> ExecutionAssessment:
    return ExecutionAssessment(
        invalidation_defined=False,
        invalidation_level=None,
        entry_zone=None,
        current_price=None,
        invalidation_distance=None,
        invalidation_distance_atr=None,
        room_to_opposing_atr=None,
        asymmetry_ratio=None,
        volatility_regime=volatility_regime,
        in_zone=False,
        extended=False,
        execution_confidence=ConfidenceLevel.LOW,
        blocked=True,
        block_reason=(
            "Invalidation is undefined, so entry quality cannot be assessed. "
            "Entry quality depends on distance to invalidation; assessing it "
            "first would be a dependency inversion."
        ),
        notes=("invalidation_required_before_entry_quality",),
    )


def assess_execution(
    *,
    invalidation_level: float | None,
    entry_zone: tuple[float, float] | None = None,
    current_price: float | None = None,
    atr: float | None = None,
    room_to_opposing_atr: float | None = None,
    asymmetry_ratio: float | None = None,
    volatility_regime: str = "unavailable",
    gates: tuple[GateOutcome, ...] = (),
) -> ExecutionAssessment:
    """Assess execution quality. ``invalidation_level`` is required first."""
    regime = volatility_regime if volatility_regime in VOLATILITY_REGIMES else "unavailable"

    if invalidation_level is None:
        return _undefined_invalidation(regime)

    notes: list[str] = []
    confidence = ConfidenceLevel.HIGH

    invalidation_distance = None
    invalidation_distance_atr = None
    if current_price is not None:
        invalidation_distance = abs(float(current_price) - float(invalidation_level))
        if atr and float(atr) > 0:
            invalidation_distance_atr = invalidation_distance / float(atr)

    in_zone = False
    extended = False
    if entry_zone is not None and current_price is not None:
        low, high = float(entry_zone[0]), float(entry_zone[1])
        if low > high:
            low, high = high, low
        in_zone = low <= float(current_price) <= high
        if not in_zone and atr and float(atr) > 0:
            # "Already extended" is representable rather than silently treated
            # as a fresh setup. The 1.8-ATR distance matches the existing
            # do-not-chase boundary in the live entry-plan layer.
            distance_beyond = min(
                abs(float(current_price) - low), abs(float(current_price) - high)
            )
            extended = distance_beyond > 1.8 * float(atr)

    if entry_zone is None:
        confidence = confidence.capped_at(ConfidenceLevel.LOW)
        notes.append("no_entry_zone_available")
    elif not in_zone:
        confidence = confidence.capped_at(ConfidenceLevel.MODERATE)
        notes.append("price_outside_entry_zone")

    if extended:
        confidence = confidence.capped_at(ConfidenceLevel.LOW)
        notes.append("move_already_extended")

    if asymmetry_ratio is not None and float(asymmetry_ratio) < 1.0:
        confidence = confidence.capped_at(ConfidenceLevel.LOW)
        notes.append("constrained_room_before_opposing_structure")

    if invalidation_distance_atr is None:
        confidence = confidence.capped_at(ConfidenceLevel.MODERATE)
        notes.append("invalidation_distance_not_measurable")

    if regime == "expansion":
        # Conditioning only: wider stops and worse fills, not a directional read.
        confidence = confidence.capped_at(ConfidenceLevel.MODERATE)
        notes.append("volatility_expansion_conditions_execution")
    elif regime == "unavailable":
        confidence = confidence.capped_at(ConfidenceLevel.MODERATE)
        notes.append("volatility_regime_unavailable")

    blocked = False
    block_reason = ""
    for gate in gates:
        if gate.vetoes_execution:
            blocked = True
            block_reason = gate.reason
            confidence = confidence.capped_at(ConfidenceLevel.LOW)
            notes.append(f"gate_veto:{gate.gate}")
        elif gate.action is GateAction.REDUCE_EXECUTION_CONFIDENCE:
            confidence = confidence.capped_at(ConfidenceLevel.MODERATE)
            notes.append(f"gate_reduced_execution_confidence:{gate.gate}")

    return ExecutionAssessment(
        invalidation_defined=True,
        invalidation_level=float(invalidation_level),
        entry_zone=(float(entry_zone[0]), float(entry_zone[1])) if entry_zone else None,
        current_price=float(current_price) if current_price is not None else None,
        invalidation_distance=invalidation_distance,
        invalidation_distance_atr=invalidation_distance_atr,
        room_to_opposing_atr=(
            float(room_to_opposing_atr) if room_to_opposing_atr is not None else None
        ),
        asymmetry_ratio=float(asymmetry_ratio) if asymmetry_ratio is not None else None,
        volatility_regime=regime,
        in_zone=in_zone,
        extended=extended,
        execution_confidence=confidence,
        blocked=blocked,
        block_reason=block_reason,
        notes=tuple(notes),
    )
