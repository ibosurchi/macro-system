"""Architecture B2 -- macro thesis lifecycle and the escalation rule.

Two failure modes are guarded here, in both directions:

*   **Price alone must not flip the thesis.** A stopped-out setup is a setup
    failure, not a macro failure, and one bad trade changes nothing.
*   **Price alone must not preserve it either.** A thesis that survives every
    contradiction is an immortal thesis. When technical and cross-asset failure
    is *repeated*, *broad* and *unexplained*, the thesis moves to Under Review
    and only **new macro evidence** can restore it.

All three conditions must hold. Each is defined at its smallest honest value:
repeated means more than one failure, broad means more than one instrument, and
unexplained means the divergence survived diagnosis. These are not tuned
numbers -- they are the literal reading of the words.

This module also owns the **thesis-input registry**: the set of variables that
were used to build the thesis. Recording it is what makes a non-circular
confirmation set possible later. Nothing here computes a cross-asset
relationship; the bridge itself stays withheld.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime

from .enums import Direction, Horizon, ThesisState
from .horizons import utcnow

#: "Repeated" means more than one independent setup has failed.
MIN_REPEATED_FAILURES = 2

#: "Broad" means the failure is not confined to a single instrument.
MIN_DISTINCT_INSTRUMENTS = 2


@dataclass(frozen=True)
class SetupFailure:
    """One failed setup that was consistent with the thesis.

    ``explanation`` is the diagnosis, if any: a data issue, a known
    correlation-regime shift, or incomplete transmission. An empty explanation
    means the divergence survived diagnosis and is therefore unexplained.
    """

    instrument: str
    failed_at: datetime
    explanation: str = ""

    @property
    def is_unexplained(self) -> bool:
        return not self.explanation.strip()

    def as_record(self) -> dict[str, object]:
        return {
            "instrument": self.instrument,
            "failed_at": self.failed_at.isoformat(),
            "explanation": self.explanation,
            "unexplained": self.is_unexplained,
        }


@dataclass(frozen=True)
class ThesisTransition:
    """An appended, timestamped record of one state change."""

    at: datetime
    from_state: ThesisState
    to_state: ThesisState
    reason: str

    def as_record(self) -> dict[str, object]:
        return {
            "at": self.at.isoformat(),
            "from_state": self.from_state.value,
            "to_state": self.to_state.value,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class EscalationAssessment:
    """Why the escalation rule did or did not fire. All three tests reported."""

    repeated: bool
    broad: bool
    unexplained: bool
    failure_count: int
    instruments: tuple[str, ...]
    unexplained_count: int

    @property
    def should_escalate(self) -> bool:
        return self.repeated and self.broad and self.unexplained

    def as_record(self) -> dict[str, object]:
        return {
            "repeated": self.repeated,
            "broad": self.broad,
            "unexplained": self.unexplained,
            "should_escalate": self.should_escalate,
            "failure_count": self.failure_count,
            "instruments": list(self.instruments),
            "unexplained_count": self.unexplained_count,
        }


@dataclass(frozen=True)
class ThesisRecord:
    """A macro thesis, its lifecycle state and the inputs that built it."""

    thesis_id: str
    direction: Direction
    horizon: Horizon
    opened_at: datetime
    state: ThesisState = ThesisState.INTACT
    thesis_input_keys: tuple[str, ...] = ()
    failures: tuple[SetupFailure, ...] = ()
    transitions: tuple[ThesisTransition, ...] = field(default_factory=tuple)

    # -- thesis-input registry --------------------------------------------
    def is_thesis_input(self, key: str) -> bool:
        return key in self.thesis_input_keys

    def confirmation_candidates(self, available_keys: tuple[str, ...]) -> tuple[str, ...]:
        """Keys eligible to confirm this thesis: everything that did not build it.

        A variable used to construct the thesis cannot also confirm it --
        re-reading an input measures the input, not the thesis. This function is
        the mechanical form of that rule. It computes no relationship itself;
        the cross-asset bridge remains withheld until it is built on top of this.
        """
        return tuple(k for k in available_keys if not self.is_thesis_input(k))

    def as_record(self) -> dict[str, object]:
        return {
            "thesis_id": self.thesis_id,
            "direction": self.direction.value,
            "horizon": self.horizon.value,
            "opened_at": self.opened_at.isoformat(),
            "state": self.state.value,
            "thesis_input_keys": list(self.thesis_input_keys),
            "failures": [f.as_record() for f in self.failures],
            "transitions": [t.as_record() for t in self.transitions],
        }


def open_thesis(
    *,
    thesis_id: str,
    direction: Direction,
    horizon: Horizon,
    thesis_input_keys: tuple[str, ...] = (),
    opened_at: datetime | None = None,
) -> ThesisRecord:
    return ThesisRecord(
        thesis_id=thesis_id,
        direction=direction,
        horizon=horizon,
        opened_at=opened_at or utcnow(),
        state=ThesisState.INTACT,
        thesis_input_keys=tuple(thesis_input_keys),
    )


def _transition(
    record: ThesisRecord,
    to_state: ThesisState,
    reason: str,
    at: datetime | None = None,
) -> ThesisRecord:
    if to_state is record.state:
        return record
    moment = at or utcnow()
    entry = ThesisTransition(
        at=moment, from_state=record.state, to_state=to_state, reason=reason
    )
    return replace(
        record, state=to_state, transitions=record.transitions + (entry,)
    )


def record_failure(
    record: ThesisRecord,
    failure: SetupFailure,
) -> ThesisRecord:
    """Append a setup failure. Appending alone never changes thesis state."""
    return replace(record, failures=record.failures + (failure,))


def assess_escalation(record: ThesisRecord) -> EscalationAssessment:
    """Evaluate the repeated / broad / unexplained test over recorded failures.

    Only unexplained failures count toward all three tests. A failure that was
    diagnosed -- bad data, a known correlation-regime shift, transmission that
    has not completed yet -- is evidence about the diagnosis, not about the
    thesis.
    """
    unexplained = tuple(f for f in record.failures if f.is_unexplained)
    instruments = tuple(dict.fromkeys(f.instrument for f in unexplained))
    return EscalationAssessment(
        repeated=len(unexplained) >= MIN_REPEATED_FAILURES,
        broad=len(instruments) >= MIN_DISTINCT_INSTRUMENTS,
        unexplained=bool(unexplained),
        failure_count=len(record.failures),
        instruments=instruments,
        unexplained_count=len(unexplained),
    )


def apply_escalation(
    record: ThesisRecord,
    at: datetime | None = None,
) -> tuple[ThesisRecord, EscalationAssessment]:
    """Move to Under Review only when all three conditions hold.

    Market evidence can raise a question about the thesis; it cannot answer it.
    That is why this escalates to Under Review and never straight to Invalidated.
    """
    assessment = assess_escalation(record)
    if record.state in (ThesisState.INVALIDATED, ThesisState.UNDER_REVIEW):
        return record, assessment
    if not assessment.should_escalate:
        return record, assessment
    reason = (
        f"{assessment.unexplained_count} unexplained setup failures across "
        f"{len(assessment.instruments)} instruments "
        f"({', '.join(assessment.instruments)}) survived diagnosis. New macro "
        "evidence is required to restore the thesis; price alone cannot."
    )
    return _transition(record, ThesisState.UNDER_REVIEW, reason, at), assessment


def apply_macro_evidence(
    record: ThesisRecord,
    *,
    supporting_families: int,
    conflicting_families: int,
    at: datetime | None = None,
) -> ThesisRecord:
    """Update thesis state from a change in macro evidence itself.

    This is the only path to Invalidated. Setup failures escalate to Under
    Review; only macro evidence turning against the thesis invalidates it.
    """
    if conflicting_families > 0 and supporting_families == 0:
        return _transition(
            record,
            ThesisState.INVALIDATED,
            f"No macro family still supports the thesis and {conflicting_families} "
            "now conflict with it.",
            at,
        )
    if conflicting_families > 0:
        return _transition(
            record,
            ThesisState.WEAKENING,
            f"{conflicting_families} macro famil"
            f"{'y' if conflicting_families == 1 else 'ies'} now conflict with the "
            f"thesis while {supporting_families} still support it.",
            at,
        )
    return record


def restore_thesis(
    record: ThesisRecord,
    *,
    new_macro_evidence: bool,
    description: str = "",
    at: datetime | None = None,
) -> ThesisRecord:
    """Restore a thesis to Intact. Requires NEW MACRO EVIDENCE, not price.

    Passing ``new_macro_evidence=False`` is a no-op by design: a thesis under
    review that simply starts working again on the chart has not been
    re-established, it has been un-falsified by the same kind of evidence that
    could not have falsified it in the first place.
    """
    if record.state is ThesisState.INTACT:
        return record
    if record.state is ThesisState.INVALIDATED:
        # An invalidated thesis is not restored; a new thesis is opened.
        return record
    if not new_macro_evidence:
        return record
    reason = description.strip() or "New macro evidence restored the thesis."
    return _transition(record, ThesisState.INTACT, reason, at)
