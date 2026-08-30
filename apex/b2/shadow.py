"""Architecture B2 -- shadow-mode evaluation records.

Shadow mode means the existing production system keeps running unchanged and
this architecture logs its own outputs independently, in parallel, so outcomes
can be attached later at each horizon's predefined evaluation time.

A ``ShadowRecord`` captures everything needed to audit one evaluation after the
fact: what was available and what was not, which horizon the claim belongs to,
every family's state and strength and *why*, the scenario conditions, the
transmission predictions (written before outcomes are known), the technical and
execution picture, all five confidence dimensions, the event-risk state, the
decision, the gates that fired and the conflicts detected.

``ShadowLog`` is append-only. Records are frozen and keyed by a content hash
including their timestamp, so an evaluation cannot be quietly rewritten once
written; a changed evaluation is a new record.

This module stays pure. It defines the ``ShadowStore`` protocol and an
in-memory implementation only -- persistence that touches the filesystem or
Supabase lives outside this package in ``apex.b2_bridge``, which keeps the
guarantee that nothing under ``apex.b2`` performs I/O.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from typing import Mapping, Protocol, runtime_checkable

from .confidence import ConfidenceSet
from .decision import DecisionOutcome
from .enums import Horizon
from .execution import ExecutionAssessment
from .families import FamilyReading
from .gates import GateOutcome
from .horizons import HorizonClaim, SeriesObservation, utcnow
from .modules.base import AssetModuleReading
from .predictions import PredictionRecord
from .regime import RegimeReading
from .risk import SizeDirective
from .scenarios import ScenarioSet
from .thesis import ThesisRecord

#: Recorded verbatim in every shadow record so a later reader is never left to
#: infer why the cross-asset section is empty.
#: Stamped on every record and on the log payload. B2 is not production.
SHADOW_MODE_LABEL = "SHADOW / NON-PRODUCTION / UNCALIBRATED"

CROSS_ASSET_STATUS = "withheld"
CROSS_ASSET_REASON = (
    "The cross-asset bridge is withheld: the existing implementation confirms a "
    "thesis using variables that built it. It stays inactive until it is rebuilt "
    "on the thesis-input registry with relationship-stability states, where a "
    "Broken relationship returns Unavailable rather than 'no confirmation'."
)


@dataclass(frozen=True)
class ShadowRecord:
    """One complete, immutable shadow-mode evaluation."""

    record_id: str
    evaluated_at: datetime
    instrument: str
    horizon: Horizon
    claim: HorizonClaim | None
    decision: DecisionOutcome
    confidence: ConfidenceSet
    regime: RegimeReading
    readings: tuple[FamilyReading, ...]
    scenarios: ScenarioSet | None
    predictions: tuple[PredictionRecord, ...]
    execution: ExecutionAssessment | None
    gates: tuple[GateOutcome, ...]
    observations: tuple[SeriesObservation, ...]
    thesis: ThesisRecord | None
    size: SizeDirective | None = None
    #: Stage C: the asset-specific transmission diagnostic, when one is
    #: registered for this instrument. Adds no evidence to the voting core.
    asset_module: AssetModuleReading | None = None
    #: Stage C: true minutes-to-event provenance, so a record never implies a
    #: precision the calendar did not supply.
    event_timing: Mapping[str, object] | None = None
    #: Storage V2: the exact aggregation shape this record was produced under.
    #: Without it a historical record becomes uninterpretable if the constants
    #: are ever changed. Absent (None) on legacy records, which are left
    #: historically truthful rather than backfilled with a fabricated value.
    aggregation_config: Mapping[str, object] | None = None
    mode: str = SHADOW_MODE_LABEL
    schema_version: int = 1

    def as_record(self) -> dict[str, object]:
        event_gate = next(
            (g for g in self.gates if g.event_risk_state is not None), None
        )
        return {
            "schema_version": self.schema_version,
            "mode": self.mode,
            "record_id": self.record_id,
            "evaluated_at": self.evaluated_at.isoformat(),
            "instrument": self.instrument,
            "asset_module": (
                self.asset_module.module if self.asset_module else None
            ),
            "asset_module_reading": (
                self.asset_module.as_record() if self.asset_module else None
            ),
            "event_timing": dict(self.event_timing) if self.event_timing else None,
            "aggregation_config": (
                dict(self.aggregation_config) if self.aggregation_config else None
            ),
            "horizon": self.horizon.value,
            "claim": self.claim.as_record() if self.claim else None,
            "decision_state": self.decision.state.value,
            "decision": self.decision.as_record(),
            "confidence": self.confidence.as_record(),
            "regime": self.regime.as_record(),
            "families": [r.as_record() for r in self.readings],
            "available_families": [r.family_key for r in self.readings if r.is_available],
            "unavailable_families": [
                r.family_key for r in self.readings if not r.is_available
            ],
            "scenarios": self.scenarios.as_record() if self.scenarios else None,
            "transmission_predictions": [p.as_record() for p in self.predictions],
            "execution": self.execution.as_record() if self.execution else None,
            "size_directive": self.size.as_record() if self.size else None,
            "gates_triggered": [g.as_record() for g in self.gates if g.triggered],
            "event_risk_state": (
                event_gate.event_risk_state.value if event_gate and event_gate.event_risk_state else None
            ),
            "conflicts_detected": list(self.decision.conflicts_detected),
            "observations": [o.as_record() for o in self.observations],
            "thesis": self.thesis.as_record() if self.thesis else None,
            "cross_asset": {
                "status": CROSS_ASSET_STATUS,
                "reason": CROSS_ASSET_REASON,
                "relationship_stability": None,
            },
        }


#: Column names of the append-only ``b2_shadow_records`` table. Declared here,
#: next to the record, so the mapping stays in one place; the client that talks
#: to the database lives outside this pure package.
SHADOW_ROW_COLUMNS = (
    "record_id",
    "instrument",
    "horizon",
    "evaluated_at",
    "schema_version",
    "record",
)


def record_to_row(record: Mapping[str, object]) -> dict[str, object]:
    """Map an already-serialised shadow record onto one database row.

    Pure. Takes the ``as_record()`` dict rather than the object so it can also
    map a LEGACY record read back out of the v1 blob during backfill, where no
    ``ShadowRecord`` instance exists any more. The full record is preserved
    verbatim in the ``record`` column; the promoted columns are copies for
    indexing, never a replacement for the payload.

    Returns ``{}`` for anything lacking the identity a row requires, so a
    malformed legacy entry is skipped rather than inserted half-formed.
    """
    if not isinstance(record, Mapping):
        return {}
    record_id = str(record.get("record_id") or "").strip()
    instrument = str(record.get("instrument") or "").strip()
    horizon = str(record.get("horizon") or "").strip()
    evaluated_at = str(record.get("evaluated_at") or "").strip()
    if not (record_id and instrument and horizon and evaluated_at):
        return {}
    try:
        schema_version = int(record.get("schema_version") or 1)
    except (TypeError, ValueError):
        schema_version = 1
    return {
        "record_id": record_id,
        "instrument": instrument,
        "horizon": horizon,
        "evaluated_at": evaluated_at,
        "schema_version": schema_version,
        "record": dict(record),
    }


def build_shadow_record(
    *,
    instrument: str,
    horizon: Horizon,
    decision: DecisionOutcome,
    confidence: ConfidenceSet,
    regime: RegimeReading,
    readings: tuple[FamilyReading, ...],
    claim: HorizonClaim | None = None,
    scenarios: ScenarioSet | None = None,
    predictions: tuple[PredictionRecord, ...] = (),
    execution: ExecutionAssessment | None = None,
    gates: tuple[GateOutcome, ...] = (),
    observations: tuple[SeriesObservation, ...] = (),
    thesis: ThesisRecord | None = None,
    size: SizeDirective | None = None,
    asset_module: AssetModuleReading | None = None,
    event_timing: Mapping[str, object] | None = None,
    aggregation_config: Mapping[str, object] | None = None,
    evaluated_at: datetime | None = None,
    observation_key: str = "",
) -> ShadowRecord:
    """Build one shadow record.

    ``observation_key``, when supplied, becomes the sole basis of the record id.
    That lets a caller impose a deterministic observation identity -- for
    instance one observation per instrument per hour -- so a rerun or a restart
    recomputes the same id and is rejected by the append-only log instead of
    creating an uncontrolled duplicate. With no key the id falls back to the
    full evaluation timestamp, which is unique per call.
    """
    moment = evaluated_at or utcnow()
    basis = observation_key or "|".join(
        [
            moment.isoformat(),
            instrument,
            horizon.value,
            decision.state.value,
            decision.direction.value,
        ]
    )
    digest = hashlib.sha256(basis.encode("utf-8")).hexdigest()[:20]
    return ShadowRecord(
        record_id=digest,
        evaluated_at=moment,
        instrument=instrument,
        horizon=horizon,
        claim=claim,
        decision=decision,
        confidence=confidence,
        regime=regime,
        readings=readings,
        scenarios=scenarios,
        predictions=predictions,
        execution=execution,
        gates=gates,
        observations=observations,
        thesis=thesis,
        size=size,
        asset_module=asset_module,
        event_timing=event_timing,
        aggregation_config=aggregation_config,
    )


class ShadowLogError(RuntimeError):
    """Raised on any attempt to rewrite a logged evaluation."""


@dataclass
class ShadowLog:
    """Append-only collection of shadow evaluations."""

    max_records: int = 2000
    _records: list[dict[str, object]] = field(default_factory=list)
    _ids: set[str] = field(default_factory=set)
    #: Counters describing how the observation hook behaved. Kept inside this
    #: log's own payload so no separate state id is needed and nothing
    #: user-facing or alert-generating is involved.
    diagnostics: dict[str, int] = field(default_factory=dict)

    def contains(self, record_id: str) -> bool:
        return record_id in self._ids

    def bump(self, counter: str, amount: int = 1) -> None:
        self.diagnostics[counter] = int(self.diagnostics.get(counter, 0)) + amount

    def append(self, record: ShadowRecord) -> dict[str, object]:
        if record.record_id in self._ids:
            raise ShadowLogError(
                f"Shadow record {record.record_id} already exists. The log is "
                "append-only; a changed evaluation is a new record."
            )
        payload = record.as_record()
        self._records.append(payload)
        self._ids.add(record.record_id)
        if len(self._records) > self.max_records:
            dropped = self._records[: len(self._records) - self.max_records]
            for item in dropped:
                self._ids.discard(str(item.get("record_id", "")))
            self._records = self._records[-self.max_records :]
        return payload

    @property
    def records(self) -> tuple[dict[str, object], ...]:
        return tuple(self._records)

    def __len__(self) -> int:
        return len(self._records)

    def as_record(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "mode": SHADOW_MODE_LABEL,
            "records": list(self._records),
            "diagnostics": dict(self.diagnostics),
        }

    @classmethod
    def from_record(cls, payload: dict[str, object] | None, max_records: int = 2000) -> "ShadowLog":
        log = cls(max_records=max_records)
        if not isinstance(payload, dict):
            return log
        diagnostics = payload.get("diagnostics")
        if isinstance(diagnostics, dict):
            log.diagnostics = {str(k): int(v) for k, v in diagnostics.items() if isinstance(v, int)}
        for item in payload.get("records", []) or []:
            if not isinstance(item, dict):
                continue
            record_id = str(item.get("record_id", ""))
            if not record_id or record_id in log._ids:
                continue
            log._records.append(item)
            log._ids.add(record_id)
        return log


@runtime_checkable
class ShadowStore(Protocol):
    """Minimal persistence surface. Implementations live outside this package."""

    def load(self, state_id: str, default: object) -> object: ...

    def save(self, state_id: str, payload: object) -> None: ...


@dataclass
class InMemoryShadowStore:
    """Reference implementation used by tests and by callers with no backend."""

    _data: dict[str, object] = field(default_factory=dict)

    def load(self, state_id: str, default: object) -> object:
        return self._data.get(state_id, default)

    def save(self, state_id: str, payload: object) -> None:
        self._data[state_id] = payload
