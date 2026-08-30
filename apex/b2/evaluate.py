"""Architecture B2 -- the shadow-mode evaluation orchestrator.

Pure. Everything it needs is passed in; it performs no I/O, calls no provider
and reads no global state. Given one snapshot of already-computed production
values it produces a complete, auditable evaluation.

Order matters in exactly two places, and both are structural rather than
stylistic:

*   Gates are evaluated before execution, because a veto has to be able to
    reach the execution assessment.
*   Invalidation reaches ``assess_execution`` before any entry-quality verdict
    exists, because entry quality is defined in terms of distance to
    invalidation.

Regime is *not* a pipeline step. It is computed alongside and read by the
layers that need it; nothing downstream waits on it for a directional answer,
because it never provides one.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Mapping

from .aggregation import DEFAULT_AGGREGATION, AggregateResult, AggregationConfig, resolve_direction
from .confidence import ConfidenceSet, assemble_confidence
from .decision import DecisionOutcome, resolve_decision
from .enums import ConfidenceLevel, Direction, Horizon
from .execution import ExecutionAssessment, assess_execution
from .families import FamilyReading, evaluate_families
from .gates import GateOutcome, evaluate_gates
from .horizons import HorizonClaim, SeriesObservation, Staleness, build_claim, utcnow
from .predictions import PredictionLog, PredictionRecord, regime_confidence_contribution
from .regime import RegimeReading, classify_regime
from .registry import (
    CRITICAL_FAMILY_KEYS,
    MACRO_FAMILY_KEYS,
    TECHNICAL_FAMILY_KEYS,
    VOTING_FAMILIES,
)
from .risk import DEFAULT_RISK_PARAMETERS, RiskParameters, SizeDirective, size_directive
from .scenarios import ScenarioSet, build_scenario_set
from .modules import module_for
from .modules.base import AssetModuleReading
from .shadow import ShadowRecord, build_shadow_record
from .thesis import ThesisRecord


@dataclass(frozen=True)
class ShadowEvaluation:
    """Everything one shadow evaluation produced, plus the record to log."""

    readings: tuple[FamilyReading, ...]
    direction: Direction
    aggregate: AggregateResult
    gates: tuple[GateOutcome, ...]
    execution: ExecutionAssessment
    regime: RegimeReading
    decision: DecisionOutcome
    confidence: ConfidenceSet
    scenarios: ScenarioSet
    claim: HorizonClaim
    size: SizeDirective
    record: ShadowRecord
    asset_module: AssetModuleReading | None = None

    def as_record(self) -> dict[str, object]:
        return self.record.as_record()


def run_shadow_evaluation(
    *,
    instrument: str,
    decision_horizon: Horizon,
    signals_by_family: Mapping[str, Mapping[str, float | None]],
    invalidation_level: float | None = None,
    entry_zone: tuple[float, float] | None = None,
    current_price: float | None = None,
    atr: float | None = None,
    atr_ratio: float | None = None,
    room_to_opposing_atr: float | None = None,
    asymmetry_ratio: float | None = None,
    volatility_regime: str = "unavailable",
    technical_invalidated: bool = False,
    minutes_to_event: float | None = None,
    is_top_tier_event: bool = False,
    event_can_invalidate_thesis: bool = False,
    unsettled_unscheduled_event: bool = False,
    event_label: str = "",
    position_open: bool = False,
    thesis: ThesisRecord | None = None,
    prediction_log: PredictionLog | None = None,
    predictions: tuple[PredictionRecord, ...] = (),
    observations: tuple[SeriesObservation, ...] = (),
    conflicting_sources: bool = False,
    risk_parameters: RiskParameters = DEFAULT_RISK_PARAMETERS,
    asset_module_inputs: Mapping[str, object] | None = None,
    event_timing: Mapping[str, object] | None = None,
    config: AggregationConfig = DEFAULT_AGGREGATION,
    evaluated_at: datetime | None = None,
    observation_key: str = "",
) -> ShadowEvaluation:
    """Run one complete shadow evaluation over a snapshot of inputs.

    ``observation_key`` is passed straight through to the record so a caller can
    impose a deterministic observation identity and make repeated evaluations of
    the same instrument-hour idempotent.
    """
    moment = evaluated_at or utcnow()

    readings = evaluate_families(VOTING_FAMILIES, signals_by_family)
    direction, aggregate = resolve_direction(
        readings, MACRO_FAMILY_KEYS, TECHNICAL_FAMILY_KEYS, config
    )

    gates = evaluate_gates(
        readings=readings,
        candidate=direction,
        critical_family_keys=CRITICAL_FAMILY_KEYS,
        minutes_to_event=minutes_to_event,
        is_top_tier=is_top_tier_event,
        can_invalidate_thesis=event_can_invalidate_thesis,
        unsettled_unscheduled_event=unsettled_unscheduled_event,
        position_open=position_open,
        event_label=event_label,
    )

    # Invalidation first: entry quality is defined relative to it.
    execution = assess_execution(
        invalidation_level=invalidation_level,
        entry_zone=entry_zone,
        current_price=current_price,
        atr=atr,
        room_to_opposing_atr=room_to_opposing_atr,
        asymmetry_ratio=asymmetry_ratio,
        volatility_regime=volatility_regime,
        gates=gates,
    )

    transmission_rate, transmission_sample = (None, 0)
    if prediction_log is not None:
        transmission_rate, transmission_sample = regime_confidence_contribution(prediction_log)

    regime = classify_regime(
        volatility_regime=volatility_regime,
        readings=readings,
        candidate_direction=direction,
        transmission_rate=transmission_rate,
        transmission_sample=transmission_sample,
        observed_at=moment,
        technical_keys=TECHNICAL_FAMILY_KEYS,
    )

    decision = resolve_decision(
        readings=readings,
        macro_keys=MACRO_FAMILY_KEYS,
        technical_keys=TECHNICAL_FAMILY_KEYS,
        critical_family_keys=CRITICAL_FAMILY_KEYS,
        decision_horizon=decision_horizon,
        gates=gates,
        execution=execution,
        position_open=position_open,
        technical_invalidated=technical_invalidated,
        thesis_state=thesis.state if thesis else None,
        config=config,
    )

    staleness: tuple[Staleness, ...] = tuple(o.staleness(moment) for o in observations)

    confidence = assemble_confidence(
        readings=readings,
        candidate=direction,
        macro_keys=MACRO_FAMILY_KEYS,
        technical_keys=TECHNICAL_FAMILY_KEYS,
        critical_family_keys=CRITICAL_FAMILY_KEYS,
        execution_confidence=execution.execution_confidence,
        regime_confidence=regime.confidence,
        gates=gates,
        staleness_observations=staleness,
        conflicting_sources=conflicting_sources,
    )

    size = size_directive(
        risk_parameters=risk_parameters,
        atr_ratio=atr_ratio,
        execution=execution,
        gates=gates,
        disagreement_present=aggregate.disagreement_present,
        data_confidence=confidence.data,
    )

    # Asset-specific transmission diagnostic. Computed AFTER the direction is
    # resolved, because it asks whether each channel is carrying that thesis --
    # and it deliberately does not participate in resolving it. A module failure
    # degrades the record to no asset-module section rather than blocking it.
    asset_module: AssetModuleReading | None = None
    module = module_for(instrument)
    if module is not None and asset_module_inputs is not None:
        try:
            asset_module = module.evaluate(
                thesis_direction=direction,
                horizon=decision_horizon,
                **dict(asset_module_inputs),
            )
        except Exception:
            asset_module = None

    scenarios = build_scenario_set(
        direction=direction,
        readings=readings,
        horizon=decision_horizon,
        registered_at=moment,
    )

    claim = build_claim(
        horizon=decision_horizon,
        direction=direction,
        claim=(
            f"{instrument}: {decision.state.value} with a {direction.value} read at "
            f"the {decision_horizon.value} horizon."
        ),
        evidence_keys=tuple(r.family_key for r in readings if r.is_available),
        registered_at=moment,
    )

    record = build_shadow_record(
        instrument=instrument,
        horizon=decision_horizon,
        decision=decision,
        confidence=confidence,
        regime=regime,
        readings=readings,
        claim=claim,
        scenarios=scenarios,
        predictions=predictions,
        execution=execution,
        gates=gates,
        observations=observations,
        thesis=thesis,
        size=size,
        asset_module=asset_module,
        event_timing=event_timing,
        # Provenance of the aggregation shape this evaluation ran under. Stored
        # on every new record so it stays interpretable if the constants change.
        aggregation_config=config.as_provenance(),
        evaluated_at=moment,
        observation_key=observation_key,
    )

    return ShadowEvaluation(
        readings=readings,
        direction=direction,
        aggregate=aggregate,
        gates=gates,
        execution=execution,
        regime=regime,
        decision=decision,
        confidence=confidence,
        scenarios=scenarios,
        claim=claim,
        size=size,
        record=record,
        asset_module=asset_module,
    )


def thesis_input_keys(readings: tuple[FamilyReading, ...]) -> tuple[str, ...]:
    """Which family keys actually carried a reading, for the thesis-input registry.

    Recording this at thesis-open time is what makes a non-circular confirmation
    set possible later: a variable that built the thesis is excluded from
    confirming it.
    """
    return tuple(r.family_key for r in readings if r.is_available)


__all__ = [
    "ConfidenceLevel",
    "ShadowEvaluation",
    "run_shadow_evaluation",
    "thesis_input_keys",
]
