"""Architecture B2 -- the transmission prediction log.

This is deliberately a lightweight prediction log and not a speculative causal
graph. For a thesis it records, *in advance*: the expected sequence of
transmission steps, the expected direction of each, the time window each is
expected within, and the timestamp at which the whole thing was written down.

    Policy shock -> yields react -> USD reacts -> Gold reacts

Later, which of those predicted steps actually occurred is attached as a
separate observation.

The storage design makes hindsight narration structurally impossible rather
than merely discouraged:

*   ``PredictionRecord`` is frozen and its id is a content hash that includes
    the creation timestamp, so editing any field produces a different record
    rather than a quietly rewritten one.
*   ``PredictionLog.append`` refuses a duplicate id.
*   Outcomes live in a **separate** collection keyed by (record id, step index).
    Attaching an outcome never touches the prediction.
*   An outcome whose ``observed_at`` precedes the prediction's ``created_at`` is
    rejected: you cannot observe something before you predicted it.
*   An outcome may be attached once. A second attempt for the same step is
    rejected, so a resolved step cannot be re-scored after the fact.

Confirmed propagation is a DIAGNOSTIC. It never becomes an independent
directional vote. Its only quantitative use is to inform Regime Confidence,
which is exposed here as an explicit, narrowly-named function.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum

from .enums import Direction, Horizon
from .horizons import evaluation_deadline, utcnow


#: Status of the transmission-prediction corpus as a whole.
#:
#: Every prediction registered before the B2 freeze was written by a caller that
#: stamped ``expected_direction`` with the instrument's thesis direction on EVERY
#: step, so the intermediate legs of each chain assert the opposite of the
#: mechanism the asset modules describe. The records themselves are immutable and
#: are not rewritten -- they are historically truthful about what was registered.
#: They are simply not admissible as evidence about anything.
#:
#: No outcome has ever been attached to any of them (``attach_outcome`` has no
#: caller), so nothing has been scored and no confirmation rate exists. That must
#: stay true: resolving this corpus would score inverted claims, and no outcome
#: may be invented for it retrospectively.
CORPUS_STATUS = "invalid_pre_freeze"

CORPUS_STATUS_REASON = (
    "Pre-freeze transmission predictions were registered with every step's "
    "expected direction set to the thesis direction, which inverts the "
    "intermediate legs of each chain (a bullish-gold thesis asserts rising real "
    "yields and a rising dollar). The chain endpoints are also free text with no "
    "resolver, so no step was measurable. This corpus must never be resolved, "
    "scored, reinterpreted, or used to inform regime confidence. It is retained "
    "unmodified as a record of what was registered, and registration is disabled."
)


class StepOutcomeState(Enum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    CONTRADICTED = "contradicted"
    NOT_OBSERVED = "not_observed"   # window elapsed with nothing happening
    UNAVAILABLE = "unavailable"     # could not be measured -- not the same as not observed


@dataclass(frozen=True)
class TransmissionStep:
    """One expected link in the chain, with its own expected window."""

    index: int
    source: str
    target: str
    expected_direction: Direction
    expects_within: timedelta
    rationale: str = ""

    def as_record(self) -> dict[str, object]:
        return {
            "index": self.index,
            "source": self.source,
            "target": self.target,
            "expected_direction": self.expected_direction.value,
            "expects_within_hours": self.expects_within.total_seconds() / 3600.0,
            "rationale": self.rationale,
        }


@dataclass(frozen=True)
class PredictionRecord:
    """An immutable, pre-registered transmission prediction."""

    record_id: str
    created_at: datetime
    horizon: Horizon
    thesis_direction: Direction
    instrument: str
    steps: tuple[TransmissionStep, ...]
    evaluate_at: datetime

    def step(self, index: int) -> TransmissionStep | None:
        for candidate in self.steps:
            if candidate.index == index:
                return candidate
        return None

    def as_record(self) -> dict[str, object]:
        return {
            "record_id": self.record_id,
            "created_at": self.created_at.isoformat(),
            "horizon": self.horizon.value,
            "thesis_direction": self.thesis_direction.value,
            "instrument": self.instrument,
            "evaluate_at": self.evaluate_at.isoformat(),
            "steps": [s.as_record() for s in self.steps],
        }


@dataclass(frozen=True)
class StepOutcome:
    """An observation attached to one predicted step. Never merged into it."""

    record_id: str
    step_index: int
    state: StepOutcomeState
    observed_at: datetime
    note: str = ""

    def as_record(self) -> dict[str, object]:
        return {
            "record_id": self.record_id,
            "step_index": self.step_index,
            "state": self.state.value,
            "observed_at": self.observed_at.isoformat(),
            "note": self.note,
        }


def _normalise(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def build_prediction(
    *,
    horizon: Horizon,
    thesis_direction: Direction,
    instrument: str,
    steps: tuple[TransmissionStep, ...],
    created_at: datetime | None = None,
    identity_key: str = "",
) -> PredictionRecord:
    """Create a prediction whose id is a content hash including its timestamp.

    ``identity_key``, when supplied, becomes the sole basis of the id so a
    caller can impose a deterministic registration identity -- for instance one
    prediction per instrument per day. This does not weaken the anti-hindsight
    guarantees: the record is still frozen, ``created_at`` still records the
    true registration moment, outcomes still live in a separate collection, and
    an outcome still cannot predate the prediction it resolves.
    """
    if not steps:
        raise ValueError("A transmission prediction must contain at least one step.")
    indices = [s.index for s in steps]
    if len(set(indices)) != len(indices):
        raise ValueError("Transmission steps must have unique indices.")

    stamped = _normalise(created_at or utcnow())
    payload = identity_key or "|".join(
        [
            stamped.isoformat(),
            horizon.value,
            thesis_direction.value,
            instrument,
            *[f"{s.index}:{s.source}->{s.target}:{s.expected_direction.value}" for s in steps],
        ]
    )
    record_id = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]
    return PredictionRecord(
        record_id=record_id,
        created_at=stamped,
        horizon=horizon,
        thesis_direction=thesis_direction,
        instrument=instrument,
        steps=tuple(sorted(steps, key=lambda s: s.index)),
        evaluate_at=evaluation_deadline(horizon, stamped),
    )


class PredictionLogError(RuntimeError):
    """Raised on any attempt to rewrite history."""


@dataclass
class PredictionLog:
    """Append-only log of predictions with separately stored outcomes."""

    _records: dict[str, PredictionRecord] = field(default_factory=dict)
    _outcomes: dict[tuple[str, int], StepOutcome] = field(default_factory=dict)

    # -- writing -----------------------------------------------------------
    def append(self, record: PredictionRecord) -> PredictionRecord:
        if record.record_id in self._records:
            raise PredictionLogError(
                f"Prediction {record.record_id} already exists. The log is append-only; "
                "a changed prediction is a new record, never an edit."
            )
        self._records[record.record_id] = record
        return record

    def attach_outcome(
        self,
        *,
        record_id: str,
        step_index: int,
        state: StepOutcomeState,
        observed_at: datetime | None = None,
        note: str = "",
    ) -> StepOutcome:
        record = self._records.get(record_id)
        if record is None:
            raise PredictionLogError(f"Unknown prediction {record_id}.")
        if record.step(step_index) is None:
            raise PredictionLogError(
                f"Prediction {record_id} has no step {step_index}."
            )
        key = (record_id, step_index)
        if key in self._outcomes:
            raise PredictionLogError(
                f"Step {step_index} of {record_id} is already resolved as "
                f"{self._outcomes[key].state.value}. A resolved step is immutable."
            )
        moment = _normalise(observed_at or utcnow())
        if moment < record.created_at:
            raise PredictionLogError(
                "An outcome cannot predate the prediction it resolves "
                f"({moment.isoformat()} < {record.created_at.isoformat()})."
            )
        outcome = StepOutcome(
            record_id=record_id,
            step_index=step_index,
            state=state,
            observed_at=moment,
            note=note,
        )
        self._outcomes[key] = outcome
        return outcome

    # -- reading -----------------------------------------------------------
    @property
    def records(self) -> tuple[PredictionRecord, ...]:
        return tuple(self._records.values())

    @property
    def outcomes(self) -> tuple[StepOutcome, ...]:
        return tuple(self._outcomes.values())

    def outcome_for(self, record_id: str, step_index: int) -> StepOutcome | None:
        return self._outcomes.get((record_id, step_index))

    def state_of(self, record_id: str, step_index: int) -> StepOutcomeState:
        outcome = self.outcome_for(record_id, step_index)
        return outcome.state if outcome else StepOutcomeState.PENDING

    def pending_steps(self, now: datetime | None = None) -> tuple[tuple[str, int], ...]:
        """Steps whose expected window has elapsed but which are unresolved."""
        reference = _normalise(now or utcnow())
        due: list[tuple[str, int]] = []
        for record in self._records.values():
            for step in record.steps:
                if (record.record_id, step.index) in self._outcomes:
                    continue
                if reference >= record.created_at + step.expects_within:
                    due.append((record.record_id, step.index))
        return tuple(due)

    # -- diagnostics -------------------------------------------------------
    def confirmation_rate(self) -> tuple[float | None, int]:
        """Share of resolved, measurable steps that were confirmed.

        Returns ``(rate, n)`` with ``rate`` None when nothing measurable has
        resolved yet -- not 0.0, which would read as "everything failed".
        Steps recorded UNAVAILABLE are excluded rather than counted as failures.
        """
        measurable = [
            o
            for o in self._outcomes.values()
            if o.state in (StepOutcomeState.CONFIRMED, StepOutcomeState.CONTRADICTED,
                           StepOutcomeState.NOT_OBSERVED)
        ]
        if not measurable:
            return None, 0
        confirmed = sum(1 for o in measurable if o.state is StepOutcomeState.CONFIRMED)
        return confirmed / len(measurable), len(measurable)

    # -- serialisation -----------------------------------------------------
    def as_record(self) -> dict[str, object]:
        return {
            "predictions": [r.as_record() for r in self._records.values()],
            "outcomes": [o.as_record() for o in self._outcomes.values()],
            "corpus_status": CORPUS_STATUS,
            "corpus_status_reason": CORPUS_STATUS_REASON,
        }

    @classmethod
    def from_record(cls, payload: dict[str, object] | None) -> "PredictionLog":
        log = cls()
        if not isinstance(payload, dict):
            return log
        for raw in payload.get("predictions", []) or []:
            if not isinstance(raw, dict):
                continue
            steps = tuple(
                TransmissionStep(
                    index=int(s["index"]),
                    source=str(s["source"]),
                    target=str(s["target"]),
                    expected_direction=Direction(str(s["expected_direction"])),
                    expects_within=timedelta(hours=float(s.get("expects_within_hours", 0.0))),
                    rationale=str(s.get("rationale", "")),
                )
                for s in raw.get("steps", []) or []
                if isinstance(s, dict)
            )
            if not steps:
                continue
            log._records[str(raw["record_id"])] = PredictionRecord(
                record_id=str(raw["record_id"]),
                created_at=datetime.fromisoformat(str(raw["created_at"])),
                horizon=Horizon(str(raw["horizon"])),
                thesis_direction=Direction(str(raw["thesis_direction"])),
                instrument=str(raw.get("instrument", "")),
                steps=steps,
                evaluate_at=datetime.fromisoformat(str(raw["evaluate_at"])),
            )
        for raw in payload.get("outcomes", []) or []:
            if not isinstance(raw, dict):
                continue
            key = (str(raw["record_id"]), int(raw["step_index"]))
            log._outcomes[key] = StepOutcome(
                record_id=key[0],
                step_index=key[1],
                state=StepOutcomeState(str(raw["state"])),
                observed_at=datetime.fromisoformat(str(raw["observed_at"])),
                note=str(raw.get("note", "")),
            )
        return log


def regime_confidence_contribution(log: PredictionLog) -> tuple[float | None, int]:
    """The ONLY quantitative channel from the prediction log into the system.

    Confirmed propagation informs Regime Confidence and nothing else. It is not
    a directional vote, it does not reach the aggregator, and no caller may use
    it to move a thesis direction -- if transmission is confirming, that says
    the mechanism is operating, not which way it points.
    """
    return log.confirmation_rate()
