"""Architecture B2 -- gates.

A gate can block, defer or cap. A gate can never contribute direction, and a
gate is never averaged into a score. Event Risk in particular is a gate with
three states, not a fourth confidence factor.

Gates are evaluated continuously and independently, not as a sequential
pipeline step -- ``evaluate_gates`` returns all of them and the caller reads
whichever apply.
"""
from __future__ import annotations

from dataclasses import dataclass

from .enums import ConfidenceLevel, EventRiskState, FamilyState, GateAction, Direction
from .families import FamilyReading

# ---------------------------------------------------------------------------
# Event windows.
#
# These minute boundaries are NOT new numbers. They are the windows the live
# system already uses in _calculate_dynamic_event_safety: a hard block from
# -10 to +30 minutes around a release, elevated proximity out to 120 minutes,
# moderate proximity out to 360 minutes, clear beyond that. Reusing them keeps
# the gate consistent with the behaviour operators already observe.
# ---------------------------------------------------------------------------
EVENT_BLOCK_WINDOW = (-10.0, 30.0)
EVENT_ELEVATED_WINDOW_MAX = 120.0
EVENT_MODERATE_WINDOW_MAX = 360.0


@dataclass(frozen=True)
class GateOutcome:
    """One gate's verdict."""

    gate: str
    triggered: bool
    action: GateAction
    reason: str
    max_confidence: ConfidenceLevel | None = None
    event_risk_state: EventRiskState | None = None
    applies_to_open_position: bool = False

    @property
    def vetoes_execution(self) -> bool:
        return self.action is GateAction.VETO_EXECUTION

    @property
    def reduces_execution_confidence(self) -> bool:
        return self.action in (
            GateAction.REDUCE_EXECUTION_CONFIDENCE,
            GateAction.VETO_EXECUTION,
        )

    def as_record(self) -> dict[str, object]:
        return {
            "gate": self.gate,
            "triggered": self.triggered,
            "action": self.action.value,
            "reason": self.reason,
            "max_confidence": self.max_confidence.name if self.max_confidence else None,
            "event_risk_state": (
                self.event_risk_state.value if self.event_risk_state else None
            ),
            "applies_to_open_position": self.applies_to_open_position,
        }


def evaluate_event_risk_gate(
    *,
    minutes_to_event: float | None,
    is_top_tier: bool = False,
    can_invalidate_thesis: bool = False,
    unsettled_unscheduled_event: bool = False,
    position_open: bool = False,
    event_label: str = "",
) -> GateOutcome:
    """Event Risk as a gate.

    ``minutes_to_event`` is negative after a release and positive before it, in
    the same convention as the live calendar layer. ``None`` means no relevant
    event is in range.

    This gate applies to open positions as well as new entries: holding through
    a top-tier release is a distinct risk decision from entering before one, and
    ``applies_to_open_position`` records which situation triggered it.
    """
    label = event_label or "scheduled event"

    if unsettled_unscheduled_event:
        return GateOutcome(
            gate="event_risk",
            triggered=True,
            action=GateAction.VETO_EXECUTION,
            reason=(
                "An unscheduled event has occurred and pricing has not settled. "
                "Execution is deferred; the underlying thesis is not invalidated."
            ),
            max_confidence=ConfidenceLevel.LOW,
            event_risk_state=EventRiskState.CRITICAL,
            applies_to_open_position=position_open,
        )

    if minutes_to_event is None:
        return GateOutcome(
            gate="event_risk",
            triggered=False,
            action=GateAction.NONE,
            reason="No relevant scheduled event within the evaluated window.",
            event_risk_state=EventRiskState.NORMAL,
        )

    minutes = float(minutes_to_event)
    in_block_window = EVENT_BLOCK_WINDOW[0] <= minutes <= EVENT_BLOCK_WINDOW[1]

    if in_block_window and is_top_tier:
        return GateOutcome(
            gate="event_risk",
            triggered=True,
            action=GateAction.VETO_EXECUTION,
            reason=(
                f"Top-tier {label} inside the release window "
                f"({minutes:.0f}m). Setup may remain valid; execution is deferred."
            ),
            max_confidence=ConfidenceLevel.LOW,
            event_risk_state=EventRiskState.CRITICAL,
            applies_to_open_position=position_open,
        )

    if in_block_window:
        return GateOutcome(
            gate="event_risk",
            triggered=True,
            action=GateAction.REDUCE_EXECUTION_CONFIDENCE,
            reason=f"{label} inside the release window ({minutes:.0f}m); spreads and slippage rise.",
            max_confidence=ConfidenceLevel.MODERATE,
            event_risk_state=EventRiskState.ELEVATED,
            applies_to_open_position=position_open,
        )

    if 0.0 <= minutes <= EVENT_ELEVATED_WINDOW_MAX:
        if can_invalidate_thesis:
            return GateOutcome(
                gate="event_risk",
                triggered=True,
                action=GateAction.CAP_CONFIDENCE,
                reason=(
                    f"{label} in {minutes:.0f}m could plausibly invalidate the macro "
                    "thesis itself; overall confidence is capped."
                ),
                max_confidence=ConfidenceLevel.MODERATE,
                event_risk_state=EventRiskState.ELEVATED,
                applies_to_open_position=position_open,
            )
        return GateOutcome(
            gate="event_risk",
            triggered=True,
            action=GateAction.REDUCE_EXECUTION_CONFIDENCE,
            reason=f"Approaching {label} in {minutes:.0f}m; execution quality degrades.",
            max_confidence=ConfidenceLevel.MODERATE,
            event_risk_state=EventRiskState.ELEVATED,
            applies_to_open_position=position_open,
        )

    if 0.0 <= minutes <= EVENT_MODERATE_WINDOW_MAX:
        return GateOutcome(
            gate="event_risk",
            triggered=True,
            action=GateAction.WARN,
            reason=f"{label} in {minutes / 60.0:.1f}h; same-session proximity.",
            event_risk_state=EventRiskState.NORMAL,
            applies_to_open_position=position_open,
        )

    return GateOutcome(
        gate="event_risk",
        triggered=False,
        action=GateAction.NONE,
        reason=f"Nearest {label} is beyond the evaluated proximity window.",
        event_risk_state=EventRiskState.NORMAL,
    )


def evaluate_data_confidence_gate(
    readings: tuple[FamilyReading, ...],
    critical_family_keys: frozenset[str],
) -> GateOutcome:
    """Missing evidence reduces Data Confidence. It never reverses direction.

    An unavailable family is counted here precisely because it is not neutral:
    neutral would silently pass as "no evidence either way", whereas
    unavailable means the system does not know.

    A HORIZON-EXCLUDED family is deliberately NOT counted. Its data arrived and
    was usable; the architecture declined to read it at this horizon because it
    publishes too slowly to be evidence here. That is a design decision, not an
    outage, and reporting it as reduced Data Confidence would mean every
    Execution record permanently claimed a data problem it does not have.
    """
    excluded = frozenset(r.family_key for r in readings if r.is_horizon_excluded)
    unavailable = tuple(
        r.family_key for r in readings
        if not r.is_available and r.family_key not in excluded
    )
    critical_missing = tuple(k for k in unavailable if k in critical_family_keys)

    if critical_missing:
        return GateOutcome(
            gate="data_confidence",
            triggered=True,
            action=GateAction.CAP_CONFIDENCE,
            reason=(
                "Critical families unavailable: "
                f"{', '.join(sorted(critical_missing))}. The system does not know; "
                "this is not a neutral reading."
            ),
            max_confidence=ConfidenceLevel.LOW,
        )

    if unavailable:
        return GateOutcome(
            gate="data_confidence",
            triggered=True,
            action=GateAction.CAP_CONFIDENCE,
            reason=f"Unavailable families: {', '.join(sorted(unavailable))}.",
            max_confidence=ConfidenceLevel.MODERATE,
        )

    reason = "All declared voting families returned usable data."
    if excluded:
        reason += (
            " Horizon-excluded (usable, but too slow to be evidence at this "
            "horizon, and therefore not a data deficiency): "
            + ", ".join(sorted(excluded))
            + "."
        )
    return GateOutcome(
        gate="data_confidence",
        triggered=False,
        action=GateAction.NONE,
        reason=reason,
    )


def evaluate_disagreement_gate(
    readings: tuple[FamilyReading, ...],
    candidate: Direction,
) -> GateOutcome:
    """Conflicting families cap confidence; they are never averaged away."""
    conflicting = tuple(
        r.family_key
        for r in readings
        if r.state_against(candidate) is FamilyState.CONFLICTS
    )
    internally_split = tuple(r.family_key for r in readings if r.has_internal_disagreement)

    if conflicting:
        return GateOutcome(
            gate="family_disagreement",
            triggered=True,
            action=GateAction.CAP_CONFIDENCE,
            reason=(
                f"Families conflicting with the {candidate.value} read: "
                f"{', '.join(sorted(conflicting))}."
            ),
            max_confidence=ConfidenceLevel.MODERATE,
        )

    if internally_split:
        return GateOutcome(
            gate="family_disagreement",
            triggered=True,
            action=GateAction.WARN,
            reason=(
                "Internal member disagreement inside "
                f"{', '.join(sorted(internally_split))}; family strength already downgraded."
            ),
        )

    return GateOutcome(
        gate="family_disagreement",
        triggered=False,
        action=GateAction.NONE,
        reason="No family conflicts with the candidate direction.",
    )


def combined_confidence_ceiling(gates: tuple[GateOutcome, ...]) -> ConfidenceLevel | None:
    """The strictest ceiling any gate imposes.

    Gates cap rather than subtract weighted points, so the result is the
    minimum ceiling, not an accumulated penalty.
    """
    ceilings = [g.max_confidence for g in gates if g.max_confidence is not None]
    if not ceilings:
        return None
    return min(ceilings, key=lambda level: level.value)


def evaluate_gates(
    *,
    readings: tuple[FamilyReading, ...],
    candidate: Direction,
    critical_family_keys: frozenset[str],
    minutes_to_event: float | None = None,
    is_top_tier: bool = False,
    can_invalidate_thesis: bool = False,
    unsettled_unscheduled_event: bool = False,
    position_open: bool = False,
    event_label: str = "",
) -> tuple[GateOutcome, ...]:
    """Evaluate every gate. Order of evaluation carries no meaning."""
    return (
        evaluate_event_risk_gate(
            minutes_to_event=minutes_to_event,
            is_top_tier=is_top_tier,
            can_invalidate_thesis=can_invalidate_thesis,
            unsettled_unscheduled_event=unsettled_unscheduled_event,
            position_open=position_open,
            event_label=event_label,
        ),
        evaluate_data_confidence_gate(readings, critical_family_keys),
        evaluate_disagreement_gate(readings, candidate),
    )
