"""Architecture B2 -- final decision states and horizon-dependent priority.

There is no universal rule here that macro always beats technical or the
reverse. The rule implemented is:

    The evidence source closest to the decision horizon receives operational
    priority, while higher-horizon evidence controls conviction, size and
    holding tolerance.

Technical invalidation and macro thesis invalidation are kept strictly
separate: a stopped-out setup on a still-valid macro thesis produces
``TECHNICAL_SETUP_INVALIDATED``, never ``MACRO_THESIS_INVALIDATED``, and the
thesis remains eligible for re-entry on the next qualifying setup.

Decision resolution does not compute macro thesis state. It accepts the state
produced by ``apex.b2.thesis`` and keeps lifecycle transitions separate from
execution decisions; repeated / broad / unexplained escalation is implemented
in that Stage B lifecycle module.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .aggregation import (
    DEFAULT_AGGREGATION,
    AggregateResult,
    AggregationConfig,
    resolve_direction,
)
from .enums import ConfidenceLevel, DecisionState, Direction, Horizon, ThesisState
from .execution import ExecutionAssessment
from .families import FamilyReading
from .gates import GateOutcome, combined_confidence_ceiling

_EMPTY: frozenset[str] = frozenset()


@dataclass(frozen=True)
class DecisionOutcome:
    """A decision state plus everything needed to audit how it was reached."""

    state: DecisionState
    direction: Direction
    horizon: Horizon
    reason: str
    macro_direction: Direction
    technical_direction: Direction
    macro_aggregate: AggregateResult
    technical_aggregate: AggregateResult
    execution: ExecutionAssessment | None
    confidence_ceiling: ConfidenceLevel | None
    gates_triggered: tuple[str, ...]
    conflicts_detected: tuple[str, ...]
    unavailable_families: tuple[str, ...]
    operational_priority: str
    thesis_state: ThesisState | None
    notes: tuple[str, ...] = field(default_factory=tuple)

    def as_record(self) -> dict[str, object]:
        return {
            "decision_state": self.state.value,
            "direction": self.direction.value,
            "horizon": self.horizon.value,
            "reason": self.reason,
            "macro_direction": self.macro_direction.value,
            "technical_direction": self.technical_direction.value,
            "operational_priority": self.operational_priority,
            "confidence_ceiling": (
                self.confidence_ceiling.name if self.confidence_ceiling else None
            ),
            "gates_triggered": list(self.gates_triggered),
            "conflicts_detected": list(self.conflicts_detected),
            "unavailable_families": list(self.unavailable_families),
            "thesis_state": self.thesis_state.value if self.thesis_state else None,
            "macro_aggregate": self.macro_aggregate.as_record(),
            "technical_aggregate": self.technical_aggregate.as_record(),
            "execution": self.execution.as_record() if self.execution else None,
            "notes": list(self.notes),
        }


def operational_priority_for(horizon: Horizon) -> str:
    """Which evidence source leads at this decision horizon."""
    if horizon is Horizon.EXECUTION:
        return "technical"
    if horizon is Horizon.STRUCTURAL:
        return "macro"
    return "balanced"


def _block_read(
    readings: tuple[FamilyReading, ...],
    keys: frozenset[str],
    config: AggregationConfig,
) -> tuple[Direction, AggregateResult]:
    """Resolve one block (macro or technical) in isolation."""
    subset = tuple(r for r in readings if r.family_key in keys)
    return resolve_direction(subset, keys, _EMPTY, config)


def resolve_decision(
    *,
    readings: tuple[FamilyReading, ...],
    macro_keys: frozenset[str],
    technical_keys: frozenset[str],
    critical_family_keys: frozenset[str],
    decision_horizon: Horizon,
    gates: tuple[GateOutcome, ...] = (),
    execution: ExecutionAssessment | None = None,
    position_open: bool = False,
    technical_invalidated: bool | None = None,
    thesis_state: ThesisState | None = None,
    config: AggregationConfig = DEFAULT_AGGREGATION,
) -> DecisionOutcome:
    """Resolve the final decision state.

    ``technical_invalidated`` is TRI-STATE and defaults to ``None``:

    ``True``
        A technical invalidation was observed by a source B2 trusts. Produces
        ``TECHNICAL_SETUP_INVALIDATED``. It never implies macro invalidation;
        that is what ``thesis_state`` is for.
    ``False``
        A trusted source looked and found no invalidation.
    ``None``
        **Unknown, and the live default.** No source in this project can tell B2
        whether its own setup is invalidated: the only invalidation available
        comes from the macro entry plan, whose level and whose comparison side
        are both chosen from production's macro regime. Treating that as a
        technical fact let macro evidence pre-empt the decision state ahead of
        almost every other branch, outside the family framework entirely. An
        unknown invalidation state produces no decision state of its own -- it is
        recorded as unknown and resolution continues.
    """
    macro_direction, macro_aggregate = _block_read(readings, macro_keys, config)
    technical_direction, technical_aggregate = _block_read(readings, technical_keys, config)

    priority = operational_priority_for(decision_horizon)
    ceiling = combined_confidence_ceiling(gates)
    gates_triggered = tuple(g.gate for g in gates if g.triggered)
    unavailable = tuple(r.family_key for r in readings if not r.is_available)
    conflicts = tuple(
        dict.fromkeys(
            macro_aggregate.conflicting_families + technical_aggregate.conflicting_families
        )
    )
    notes: list[str] = []

    veto_gate = next((g for g in gates if g.vetoes_execution), None)

    # A family excluded because its cadence is too slow for this horizon is not
    # missing data. Counting it as a critical outage would report a deliberate
    # architectural rule -- monthly evidence may not vote at the execution
    # horizon -- as a broken feed, and would degrade every single Execution
    # record to INSUFFICIENT_DATA_SYSTEM_DEGRADED.
    horizon_excluded = tuple(r.family_key for r in readings if r.is_horizon_excluded)
    critical_missing = tuple(
        k for k in unavailable
        if k in critical_family_keys and k not in horizon_excluded
    )
    if horizon_excluded:
        notes.append(
            "Horizon-excluded families (present and usable, too slow to be "
            f"evidence at the {decision_horizon.value} horizon): "
            + ", ".join(sorted(horizon_excluded))
            + ". This is a structural exclusion, not missing data."
        )
    if technical_invalidated is None:
        notes.append(
            "Technical invalidation is UNKNOWN: B2 derives no invalidation of "
            "its own, and the production entry plan's invalidation is chosen by "
            "macro regime, so it is not admissible as technical evidence."
        )

    macro_directional = macro_direction.is_directional
    technical_directional = technical_direction.is_directional
    agree = macro_directional and technical_directional and macro_direction is technical_direction
    disagree = (
        macro_directional and technical_directional and macro_direction is not technical_direction
    )

    # Overall direction reported by the decision is the one the operational
    # priority points at; conviction is still controlled by the other block.
    if priority == "technical" and technical_directional:
        direction = technical_direction
    elif macro_directional:
        direction = macro_direction
    elif technical_directional:
        direction = technical_direction
    else:
        direction = Direction.FLAT

    def outcome(state: DecisionState, reason: str) -> DecisionOutcome:
        return DecisionOutcome(
            state=state,
            direction=direction,
            horizon=decision_horizon,
            reason=reason,
            macro_direction=macro_direction,
            technical_direction=technical_direction,
            macro_aggregate=macro_aggregate,
            technical_aggregate=technical_aggregate,
            execution=execution,
            confidence_ceiling=ceiling,
            gates_triggered=gates_triggered,
            conflicts_detected=conflicts,
            unavailable_families=unavailable,
            operational_priority=priority,
            thesis_state=thesis_state,
            notes=tuple(notes),
        )

    # --- The system does not know, which is distinct from knowing there is no edge.
    if critical_missing:
        return outcome(
            DecisionState.INSUFFICIENT_DATA_SYSTEM_DEGRADED,
            "Critical voting families are unavailable: "
            f"{', '.join(sorted(critical_missing))}. The system does not know; this "
            "is not a no-edge reading and direction is not reversed by it.",
        )

    # --- Caller-supplied macro thesis lifecycle takes precedence over setup state.
    if thesis_state is ThesisState.INVALIDATED:
        return outcome(
            DecisionState.MACRO_THESIS_INVALIDATED,
            "Macro thesis is invalidated by macro evidence. Technical setups "
            "derived from it no longer apply.",
        )
    if thesis_state is ThesisState.UNDER_REVIEW:
        return outcome(
            DecisionState.POSITION_OPEN_UNDER_REVIEW
            if position_open
            else DecisionState.THESIS_UNDER_REVIEW,
            "Macro thesis is under review; new macro evidence is required to restore it.",
        )

    # --- Technical failure is a setup failure only. `is True` deliberately:
    # an unknown invalidation state must not take this branch.
    if technical_invalidated is True:
        notes.append(
            "Technical invalidation does not invalidate the macro thesis; the "
            "thesis remains eligible for re-entry on the next qualifying setup."
        )
        return outcome(
            DecisionState.TECHNICAL_SETUP_INVALIDATED,
            "The technical setup is invalidated. Macro thesis state is unchanged by this.",
        )

    # --- Event risk defers execution without invalidating anything.
    if veto_gate is not None:
        notes.append("Setup validity is unaffected; execution is deferred.")
        if position_open:
            return outcome(
                DecisionState.POSITION_OPEN_UNDER_REVIEW,
                f"Open position under an execution veto: {veto_gate.reason} "
                "Holding through this is a distinct risk decision from entering.",
            )
        if agree:
            return outcome(
                DecisionState.EXECUTION_BLOCKED,
                f"Execution blocked. Reason: {veto_gate.reason}",
            )
        return outcome(
            DecisionState.HIGH_EVENT_RISK,
            f"No confirmed setup and execution is vetoed. Reason: {veto_gate.reason}",
        )

    if position_open:
        return outcome(
            DecisionState.POSITION_OPEN_THESIS_INTACT,
            "Position open; no gate veto and no invalidation is present.",
        )

    # --- Technical setup with no macro support: log it, do not manufacture conviction.
    if not macro_directional and technical_directional:
        return outcome(
            DecisionState.TECHNICAL_SETUP_WEAK_MACRO_SUPPORT,
            f"Technical evidence is {technical_direction.value} while macro evidence is "
            f"{macro_direction.value}. Macro conviction is not manufactured to match it.",
        )

    # --- Macro and technical disagree.
    if disagree:
        if priority == "technical":
            notes.append(
                "At the execution horizon technical evidence has operational "
                "priority, while macro evidence caps conviction and holding tolerance."
            )
            return outcome(
                DecisionState.TECHNICAL_SETUP_WEAK_MACRO_SUPPORT,
                f"Technical is {technical_direction.value} against a "
                f"{macro_direction.value} macro read. Logged separately; reduced "
                "conviction and tight invalidation apply.",
            )
        return outcome(
            DecisionState.THESIS_VALID_WAIT_FOR_ENTRY,
            f"Macro is {macro_direction.value} but technical is "
            f"{technical_direction.value}. The thesis may remain intact; "
            "confirmation is missing, so wait.",
        )

    # --- Macro directional, technical not yet confirming.
    if macro_directional and not technical_directional:
        return outcome(
            DecisionState.THESIS_VALID_WAIT_FOR_ENTRY,
            f"Macro is {macro_direction.value}; technical confirmation is "
            f"{technical_direction.value}. Wait for confirmation.",
        )

    # --- Agreement. Entry still has to earn it.
    if agree:
        notes.append(
            "Macro and technical are not fully independent, so agreement is "
            "capped rather than pushed to maximum conviction."
        )
        notes.append(
            "Crowding check unavailable: positioning data is dormant in this "
            "project, so a correct-but-crowded thesis cannot be detected."
        )
        if execution is None:
            return outcome(
                DecisionState.THESIS_VALID_WAIT_FOR_ENTRY,
                "Macro and technical agree, but no execution assessment was "
                "supplied, so entry quality is unknown.",
            )
        if execution.blocked:
            return outcome(
                DecisionState.EXECUTION_BLOCKED,
                f"Macro and technical agree but execution is blocked: {execution.block_reason}",
            )
        if execution.extended:
            return outcome(
                DecisionState.THESIS_CONFIRMED_LATE_EXTENDED,
                "Macro and technical agree, but the move is already extended. "
                "This is a late/extended entry, not a fresh setup.",
            )
        if execution.execution_confidence is ConfidenceLevel.LOW:
            return outcome(
                DecisionState.THESIS_VALID_WAIT_FOR_ENTRY,
                "Thesis confirmed, but execution confidence is Low. Wait for a "
                "better entry rather than forcing one.",
            )
        return outcome(
            DecisionState.CONFIRMED_THESIS,
            f"Macro and technical both read {direction.value} and execution "
            "quality supports acting.",
        )

    # --- Everything present, nothing decisive.
    return outcome(
        DecisionState.MIXED_NO_EDGE,
        "Evidence is available but no directional edge is present. This is a "
        "known absence of edge, not missing data.",
    )
