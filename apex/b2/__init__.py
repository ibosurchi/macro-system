"""Architecture B2 -- rich analytical coverage, small independent voting core.

Stage A scope. This package contains the voting-family framework, the
confirmation caps, the gates, the execution separation and the risk controls.
It is currently **shadow infrastructure**: nothing in ``apex.production_core``
imports it, so no production score, alert, page or Telegram message is affected
by its presence.

Three structural guarantees hold for everything under ``apex.b2``:

1.  It imports nothing from ``apex.production_core``, Streamlit, ``requests``
    or ``threading``. It therefore cannot issue an AI request, open a network
    connection, start a background thread, send a Telegram message or write
    durable state.
2.  Only components registered as ``Role.ACTIVE_VOTING`` in ``registry`` can
    influence direction. Gates cap, block and defer; they never point.
3.  ``Neutral`` and ``Unavailable`` are distinct throughout. Missing evidence
    reduces confidence; it never reverses a directional read.

Deferred to later stages, and registered as withheld rather than implied:
the cross-asset bridge (blocked on non-circularity enforcement), regime and
regime confidence, macro thesis invalidation and its escalation rule, the
prediction/transmission log, scenario generation, and the five-way confidence
assembly. Stage A defines the categorical confidence vocabulary and the cap
mechanism those stages will use, but does not assemble all five dimensions.
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
from .decision import DecisionOutcome, operational_priority_for, resolve_decision
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
    "ConfidenceLevel",
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
