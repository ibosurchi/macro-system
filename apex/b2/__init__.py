"""Architecture B2 -- rich analytical coverage, small independent voting core.

Stage A + Stage B scope. This package contains the voting-family framework, the
confirmation caps, the gates, the execution separation and the risk controls
(Stage A); and the three explicit horizons, scenario reasoning with
pre-registered invalidation conditions, the append-only transmission prediction
log, regime meta-state, the five separate confidence dimensions, thesis-state
transitions with the escalation rule, and shadow-mode records (Stage B).

It is **shadow infrastructure**: nothing in ``apex.production_core`` imports it,
so no production score, alert, page or Telegram message is affected by its
presence. Persistence that touches the filesystem or Supabase lives outside this
package in ``apex.b2_bridge``, which is what keeps everything here pure.

Three structural guarantees hold for everything under ``apex.b2``:

1.  It imports nothing from ``apex.production_core``, Streamlit, ``requests``
    or ``threading``. It therefore cannot issue an AI request, open a network
    connection, start a background thread, send a Telegram message or write
    durable state.
2.  Only components registered as ``Role.ACTIVE_VOTING`` in ``registry`` can
    influence direction. Gates cap, block and defer; they never point.
3.  ``Neutral`` and ``Unavailable`` are distinct throughout. Missing evidence
    reduces confidence; it never reverses a directional read.

Still deferred, and registered as withheld rather than implied: the cross-asset
bridge (blocked on non-circularity enforcement, whose thesis-input registry now
exists in ``thesis``), false-signal/whipsaw detection, and every dormant data
component. Asset-specific driver modules are Stage C.
"""
from __future__ import annotations

from .adapters import (
    build_signals,
    directional_signals,
    execution_inputs,
    macro_activity_signals,
    news_geopolitical_signals,
    policy_real_rates_signals,
    structure_signals,
)
from .aggregation import (
    DEFAULT_AGGREGATION,
    AggregateResult,
    AggregationConfig,
    aggregate,
    resolve_direction,
    saturating_total,
)
from .confidence import ConfidenceSet, assemble_confidence, level_from_agreeing_count
from .decision import DecisionOutcome, operational_priority_for, resolve_decision
from .evaluate import ShadowEvaluation, run_shadow_evaluation, thesis_input_keys
from .horizons import (
    HORIZON_EVALUATION_WINDOW,
    HORIZON_MAX_FREQUENCY,
    HorizonClaim,
    SeriesFrequency,
    SeriesObservation,
    Staleness,
    assert_horizon_compatible,
    build_claim,
    classify_staleness,
    evaluation_deadline,
    horizon_compatible,
    is_usable,
    utcnow,
)
from .predictions import (
    PredictionLog,
    PredictionLogError,
    PredictionRecord,
    StepOutcome,
    StepOutcomeState,
    TransmissionStep,
    build_prediction,
    regime_confidence_contribution,
)
from .regime import RegimeReading, RegimeState, classify_regime
from .scenarios import (
    ConditionPolarity,
    InvalidationCondition,
    ProbabilityBand,
    Scenario,
    ScenarioKind,
    ScenarioSet,
    build_scenario_set,
    evaluate_conditions,
)
from .shadow import (
    InMemoryShadowStore,
    ShadowLog,
    ShadowLogError,
    ShadowRecord,
    ShadowStore,
    build_shadow_record,
)
from .thesis import (
    EscalationAssessment,
    SetupFailure,
    ThesisRecord,
    ThesisTransition,
    apply_escalation,
    apply_macro_evidence,
    assess_escalation,
    open_thesis,
    record_failure,
    restore_thesis,
)
from .enums import (
    ConfidenceLevel,
    DecisionState,
    Direction,
    EventRiskState,
    FamilyState,
    FamilyStrength,
    GateAction,
    Horizon,
    Role,
    ThesisState,
    DIRECTION_INFLUENCING_ROLES,
)
from .execution import ExecutionAssessment, assess_execution
from .families import (
    FLAT_THRESHOLD,
    FamilyReading,
    classify_signal,
    evaluate_families,
    evaluate_family,
)
from .gates import (
    GateOutcome,
    combined_confidence_ceiling,
    evaluate_data_confidence_gate,
    evaluate_disagreement_gate,
    evaluate_event_risk_gate,
    evaluate_gates,
)
from .registry import (
    CRITICAL_FAMILY_KEYS,
    DORMANT_COMPONENTS,
    FAMILIES_BY_KEY,
    MACRO_FAMILY_KEYS,
    TECHNICAL_FAMILY_KEYS,
    VOTING_BUDGET,
    VOTING_FAMILIES,
    WITHHELD_COMPONENTS,
    FamilyDefinition,
    InactiveComponent,
    describe_budget,
    dormant_keys,
    voting_family_keys,
    withheld_keys,
)
from .risk import (
    DEFAULT_RISK_PARAMETERS,
    OPERATOR_MUST_SET,
    RiskParameters,
    SizeDirective,
    size_directive,
    volatility_scale,
)

__all__ = [
    "AggregateResult",
    "AggregationConfig",
    "CRITICAL_FAMILY_KEYS",
    "ConditionPolarity",
    "ConfidenceLevel",
    "ConfidenceSet",
    "EscalationAssessment",
    "HORIZON_EVALUATION_WINDOW",
    "HORIZON_MAX_FREQUENCY",
    "HorizonClaim",
    "InMemoryShadowStore",
    "InvalidationCondition",
    "PredictionLog",
    "PredictionLogError",
    "PredictionRecord",
    "ProbabilityBand",
    "RegimeReading",
    "RegimeState",
    "Scenario",
    "ScenarioKind",
    "ScenarioSet",
    "SeriesFrequency",
    "SeriesObservation",
    "SetupFailure",
    "ShadowEvaluation",
    "ShadowLog",
    "ShadowLogError",
    "ShadowRecord",
    "ShadowStore",
    "Staleness",
    "StepOutcome",
    "StepOutcomeState",
    "ThesisRecord",
    "ThesisTransition",
    "TransmissionStep",
    "apply_escalation",
    "apply_macro_evidence",
    "assemble_confidence",
    "assert_horizon_compatible",
    "assess_escalation",
    "build_claim",
    "build_prediction",
    "build_scenario_set",
    "build_shadow_record",
    "classify_regime",
    "classify_staleness",
    "evaluate_conditions",
    "evaluation_deadline",
    "horizon_compatible",
    "is_usable",
    "level_from_agreeing_count",
    "open_thesis",
    "record_failure",
    "regime_confidence_contribution",
    "restore_thesis",
    "run_shadow_evaluation",
    "thesis_input_keys",
    "utcnow",
    "DEFAULT_AGGREGATION",
    "DEFAULT_RISK_PARAMETERS",
    "DIRECTION_INFLUENCING_ROLES",
    "DORMANT_COMPONENTS",
    "DecisionOutcome",
    "DecisionState",
    "Direction",
    "EventRiskState",
    "ExecutionAssessment",
    "FAMILIES_BY_KEY",
    "FLAT_THRESHOLD",
    "FamilyDefinition",
    "FamilyReading",
    "FamilyState",
    "FamilyStrength",
    "GateAction",
    "GateOutcome",
    "Horizon",
    "InactiveComponent",
    "MACRO_FAMILY_KEYS",
    "OPERATOR_MUST_SET",
    "Role",
    "RiskParameters",
    "SizeDirective",
    "TECHNICAL_FAMILY_KEYS",
    "ThesisState",
    "VOTING_BUDGET",
    "VOTING_FAMILIES",
    "WITHHELD_COMPONENTS",
    "aggregate",
    "assess_execution",
    "build_signals",
    "classify_signal",
    "combined_confidence_ceiling",
    "describe_budget",
    "directional_signals",
    "dormant_keys",
    "evaluate_data_confidence_gate",
    "evaluate_disagreement_gate",
    "evaluate_event_risk_gate",
    "evaluate_families",
    "evaluate_family",
    "evaluate_gates",
    "execution_inputs",
    "macro_activity_signals",
    "news_geopolitical_signals",
    "operational_priority_for",
    "policy_real_rates_signals",
    "resolve_decision",
    "resolve_direction",
    "saturating_total",
    "size_directive",
    "structure_signals",
    "volatility_scale",
    "voting_family_keys",
    "withheld_keys",
]
