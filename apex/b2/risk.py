"""Architecture B2 -- risk controls.

Deliberately conservative about numbers. This module invents no risk
percentages, no lot sizes and no exposure limits: every absolute trading
parameter is surfaced as a configuration item that the operator MUST set, and
until it is set the sizing directive reports itself as unavailable rather than
guessing.

The one quantity computed here without operator input is the volatility scale,
because it is derivable rather than chosen: it is textbook inverse-volatility
scaling over the ATR ratio the live entry-plan layer already computes (current
14-period ATR against its own 50-period average). If that ratio is unavailable,
sizing is unavailable -- it is not defaulted.

Anything else that would tighten risk (family conflict, elevated event risk,
low data confidence) raises the **confirmation bar** rather than multiplying
size by an invented factor. Raising the bar is a stated B2 remedy; a fabricated
0.5x multiplier is not.
"""
from __future__ import annotations

from dataclasses import dataclass

from .enums import ConfidenceLevel, EventRiskState
from .execution import ExecutionAssessment
from .gates import GateOutcome

#: Marker for a parameter that has no defensible default and must be supplied.
OPERATOR_MUST_SET = "OPERATOR_MUST_SET"


@dataclass(frozen=True)
class RiskParameters:
    """Absolute risk limits. Every field must be set by the operator.

    None means "not configured". No value here is guessed, because a wrong
    default risk fraction is worse than a missing one: a missing one stops the
    system, a wrong one silently sizes real positions.
    """

    max_risk_fraction_per_trade: float | None = None
    max_total_exposure_fraction: float | None = None
    max_drawdown_fraction: float | None = None
    max_concurrent_correlated_positions: int | None = None

    def unset_parameters(self) -> tuple[str, ...]:
        return tuple(
            name
            for name in (
                "max_risk_fraction_per_trade",
                "max_total_exposure_fraction",
                "max_drawdown_fraction",
                "max_concurrent_correlated_positions",
            )
            if getattr(self, name) is None
        )

    @property
    def is_configured(self) -> bool:
        return not self.unset_parameters()

    def as_record(self) -> dict[str, object]:
        return {
            name: (getattr(self, name) if getattr(self, name) is not None else OPERATOR_MUST_SET)
            for name in (
                "max_risk_fraction_per_trade",
                "max_total_exposure_fraction",
                "max_drawdown_fraction",
                "max_concurrent_correlated_positions",
            )
        }


#: Nothing is configured out of the box, on purpose.
DEFAULT_RISK_PARAMETERS = RiskParameters()


@dataclass(frozen=True)
class SizeDirective:
    """A *relative* sizing instruction. Never an absolute lot size."""

    available: bool
    volatility_multiplier: float | None
    confirmation_bar_raised: bool
    bar_reasons: tuple[str, ...]
    unavailable_reasons: tuple[str, ...]
    unset_parameters: tuple[str, ...]
    notes: tuple[str, ...]

    def as_record(self) -> dict[str, object]:
        return {
            "available": self.available,
            "volatility_multiplier": (
                round(self.volatility_multiplier, 4)
                if self.volatility_multiplier is not None
                else None
            ),
            "confirmation_bar_raised": self.confirmation_bar_raised,
            "bar_reasons": list(self.bar_reasons),
            "unavailable_reasons": list(self.unavailable_reasons),
            "unset_parameters": list(self.unset_parameters),
            "notes": list(self.notes),
        }


def volatility_scale(atr_ratio: float | None) -> float | None:
    """Inverse-volatility scaling from the ATR ratio.

    ``atr_ratio`` is current ATR over its own longer-window average -- the same
    quantity the live entry-plan layer computes to classify compression /
    normal / expansion. A ratio above 1 means conditions are more volatile than
    usual and the same nominal size carries more risk, so exposure scales down
    by 1/ratio. The result is capped at 1.0: unusually calm conditions are not
    a licence to size up beyond the operator's own configured limit.

    Returns None when the ratio is unavailable. It is never defaulted to 1.0,
    because "we could not measure volatility" is not "volatility is normal".
    """
    if atr_ratio is None:
        return None
    try:
        ratio = float(atr_ratio)
    except (TypeError, ValueError):
        return None
    if ratio != ratio or ratio <= 0:
        return None
    return min(1.0, 1.0 / ratio)


def size_directive(
    *,
    risk_parameters: RiskParameters = DEFAULT_RISK_PARAMETERS,
    atr_ratio: float | None = None,
    execution: ExecutionAssessment | None = None,
    gates: tuple[GateOutcome, ...] = (),
    disagreement_present: bool = False,
    data_confidence: ConfidenceLevel | None = None,
) -> SizeDirective:
    """Produce a relative sizing directive and a confirmation-bar verdict."""
    unavailable_reasons: list[str] = []
    notes: list[str] = []
    bar_reasons: list[str] = []

    multiplier = volatility_scale(atr_ratio)
    if multiplier is None:
        unavailable_reasons.append("volatility_scale_unmeasurable")

    unset = risk_parameters.unset_parameters()
    if unset:
        unavailable_reasons.append("risk_parameters_not_configured")
        notes.append(
            "Absolute position size cannot be produced until the operator sets: "
            + ", ".join(unset)
        )

    if execution is not None:
        if not execution.invalidation_defined:
            unavailable_reasons.append("invalidation_undefined")
            notes.append(
                "Invalidation-aware sizing requires a defined invalidation distance."
            )
        elif execution.invalidation_distance is None:
            unavailable_reasons.append("invalidation_distance_unmeasurable")
        if execution.extended:
            bar_reasons.append("move_already_extended")
        if execution.execution_confidence is ConfidenceLevel.LOW:
            bar_reasons.append("low_execution_confidence")

    for gate in gates:
        if gate.event_risk_state in (EventRiskState.ELEVATED, EventRiskState.CRITICAL):
            bar_reasons.append(f"event_risk_{gate.event_risk_state.value}")
            if gate.applies_to_open_position:
                notes.append(
                    "Event risk is flagged for an OPEN position: holding through a "
                    "release is a distinct decision from entering before one."
                )

    if disagreement_present:
        bar_reasons.append("family_disagreement")
    if data_confidence is ConfidenceLevel.LOW:
        bar_reasons.append("low_data_confidence")

    return SizeDirective(
        available=not unavailable_reasons,
        volatility_multiplier=multiplier,
        confirmation_bar_raised=bool(bar_reasons),
        bar_reasons=tuple(dict.fromkeys(bar_reasons)),
        unavailable_reasons=tuple(dict.fromkeys(unavailable_reasons)),
        unset_parameters=unset,
        notes=tuple(notes),
    )
