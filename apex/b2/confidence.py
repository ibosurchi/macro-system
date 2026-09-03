"""Architecture B2 -- five separate confidence dimensions.

Macro, Technical, Execution, Regime and Data confidence are kept apart and are
**never averaged into a single number**. ``ConfidenceSet`` provides no mean, no
overall score and no ordering between the five: collapsing them would hide
exactly the information they exist to surface.

Technical (and Macro) Confidence is an **output**, built from counts of
independent factor families in agreement -- not from a smooth weighted average
of correlated components. Counting families rather than indicators is what
keeps three agreeing members of one family from reading as three confirmations.

Disagreement is reported alongside the levels, never absorbed into them.

Confidence is categorical. Until a calibration exercise exists there is no
basis for a percentage, so none is emitted.
"""
from __future__ import annotations

from dataclasses import dataclass

from .enums import ConfidenceLevel, FamilyState, Direction
from .families import FamilyReading
from .gates import GateOutcome, combined_confidence_ceiling
from .horizons import Staleness

#: Agreeing-family counts and the confidence they justify. Two independent
#: families is the point at which a read stops resting on a single source.
_COUNT_TO_LEVEL: tuple[tuple[int, ConfidenceLevel], ...] = (
    (3, ConfidenceLevel.HIGH),
    (2, ConfidenceLevel.MODERATE),
    (0, ConfidenceLevel.LOW),
)


def level_from_agreeing_count(count: int) -> ConfidenceLevel:
    for threshold, level in _COUNT_TO_LEVEL:
        if count >= threshold:
            return level
    return ConfidenceLevel.LOW


@dataclass(frozen=True)
class ConfidenceSet:
    """Five separate dimensions. Deliberately offers no way to average them."""

    macro: ConfidenceLevel
    technical: ConfidenceLevel
    execution: ConfidenceLevel
    regime: ConfidenceLevel
    data: ConfidenceLevel
    disagreements: tuple[str, ...]
    unavailable: tuple[str, ...]
    caps_applied: tuple[str, ...]
    notes: tuple[str, ...]
    #: Families silent because their cadence is too slow for this horizon.
    #: Reported apart from ``unavailable`` because they are not a data problem.
    horizon_excluded: tuple[str, ...] = ()

    @property
    def has_disagreement(self) -> bool:
        return bool(self.disagreements)

    def as_record(self) -> dict[str, object]:
        return {
            "macro_confidence": self.macro.name,
            "technical_confidence": self.technical.name,
            "execution_confidence": self.execution.name,
            "regime_confidence": self.regime.name,
            "data_confidence": self.data.name,
            "disagreements": list(self.disagreements),
            "unavailable": list(self.unavailable),
            "horizon_excluded": list(self.horizon_excluded),
            "caps_applied": list(self.caps_applied),
            "notes": list(self.notes),
        }


def _block_confidence(
    readings: tuple[FamilyReading, ...],
    keys: frozenset[str],
    candidate: Direction,
) -> tuple[ConfidenceLevel, tuple[str, ...], tuple[str, ...]]:
    """Confidence for one block, from counts of agreeing independent families."""
    block = tuple(r for r in readings if r.family_key in keys)
    supporting = tuple(
        r.family_key for r in block if r.state_against(candidate) is FamilyState.SUPPORTS
    )
    conflicting = tuple(
        r.family_key for r in block if r.state_against(candidate) is FamilyState.CONFLICTS
    )
    # Horizon-excluded families are not counted as unavailable: they are not a
    # data deficiency and must not cap this block's confidence as though the
    # system had failed to read something it needed.
    unavailable = tuple(
        r.family_key for r in block if not r.is_available and not r.is_horizon_excluded
    )

    level = level_from_agreeing_count(len(supporting))
    if conflicting:
        # Disagreement caps rather than nets out, and is reported separately.
        level = level.capped_at(ConfidenceLevel.MODERATE)
    if unavailable:
        level = level.capped_at(ConfidenceLevel.MODERATE)
    return level, conflicting, unavailable


def assemble_confidence(
    *,
    readings: tuple[FamilyReading, ...],
    candidate: Direction,
    macro_keys: frozenset[str],
    technical_keys: frozenset[str],
    critical_family_keys: frozenset[str],
    execution_confidence: ConfidenceLevel = ConfidenceLevel.LOW,
    regime_confidence: ConfidenceLevel = ConfidenceLevel.LOW,
    gates: tuple[GateOutcome, ...] = (),
    staleness_observations: tuple[Staleness, ...] = (),
    conflicting_sources: bool = False,
) -> ConfidenceSet:
    """Build all five dimensions, keeping every one of them separate."""
    notes: list[str] = []
    caps: list[str] = []

    macro, macro_conflicts, macro_unavailable = _block_confidence(
        readings, macro_keys, candidate
    )
    technical, technical_conflicts, technical_unavailable = _block_confidence(
        readings, technical_keys, candidate
    )

    if len(technical_keys) < 3:
        notes.append(
            f"Technical confidence cannot reach HIGH: only {len(technical_keys)} "
            "independent technical families exist, so three cannot agree."
        )

    # --- Data confidence -------------------------------------------------
    horizon_excluded = tuple(r.family_key for r in readings if r.is_horizon_excluded)
    unavailable = tuple(
        r.family_key for r in readings
        if not r.is_available and not r.is_horizon_excluded
    )
    critical_missing = tuple(k for k in unavailable if k in critical_family_keys)
    if horizon_excluded:
        notes.append(
            "Horizon-excluded families are reported apart from unavailable ones "
            "and do not reduce Data Confidence: "
            + ", ".join(sorted(horizon_excluded))
            + ". Their data arrived and is usable; it is too slow to be evidence "
            "at this horizon."
        )

    if critical_missing or len(unavailable) >= 2:
        data = ConfidenceLevel.LOW
    elif unavailable:
        data = ConfidenceLevel.MODERATE
    else:
        data = ConfidenceLevel.HIGH

    if critical_missing:
        notes.append(
            "Critical families unavailable: " + ", ".join(sorted(critical_missing))
        )

    if any(s is Staleness.BROKEN for s in staleness_observations):
        data = data.capped_at(ConfidenceLevel.LOW)
        caps.append("data:broken_series")
    elif any(s in (Staleness.STALE, Staleness.UNKNOWN) for s in staleness_observations):
        data = data.capped_at(ConfidenceLevel.MODERATE)
        caps.append("data:stale_series")

    if conflicting_sources:
        data = data.capped_at(ConfidenceLevel.MODERATE)
        caps.append("data:conflicting_sources")
        notes.append(
            "Sources disagree on the same quantity; flagged for review rather "
            "than silently resolved in favour of one."
        )

    # --- Gate ceilings ----------------------------------------------------
    ceiling = combined_confidence_ceiling(gates)
    if ceiling is not None:
        for gate in gates:
            if gate.max_confidence is not None:
                caps.append(f"gate:{gate.gate}")
        macro = macro.capped_at(ceiling)
        technical = technical.capped_at(ceiling)
        execution_confidence = execution_confidence.capped_at(ceiling)

    # Low data or regime confidence caps conviction. It never reverses a read,
    # and it never reweights the factors -- it raises the bar instead.
    conviction_ceiling: ConfidenceLevel | None = None
    if data is ConfidenceLevel.LOW:
        conviction_ceiling = ConfidenceLevel.LOW
        caps.append("data_confidence_low")
    elif regime_confidence is ConfidenceLevel.LOW:
        conviction_ceiling = ConfidenceLevel.MODERATE
        caps.append("regime_confidence_low")
        notes.append(
            "Regime confidence is Low: stronger confirmation is required, and the "
            "factors are deliberately NOT reweighted in response."
        )
    if conviction_ceiling is not None:
        macro = macro.capped_at(conviction_ceiling)
        technical = technical.capped_at(conviction_ceiling)

    disagreements = tuple(dict.fromkeys(macro_conflicts + technical_conflicts))
    if disagreements:
        notes.append(
            "Families in conflict with the candidate read: "
            + ", ".join(sorted(disagreements))
            + ". Reported alongside confidence, not absorbed into it."
        )

    return ConfidenceSet(
        macro=macro,
        technical=technical,
        execution=execution_confidence,
        regime=regime_confidence,
        data=data,
        disagreements=disagreements,
        unavailable=tuple(dict.fromkeys(unavailable)),
        horizon_excluded=tuple(dict.fromkeys(horizon_excluded)),
        caps_applied=tuple(dict.fromkeys(caps)),
        notes=tuple(notes),
    )
