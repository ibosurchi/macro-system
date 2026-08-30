"""Architecture B2 -- scenarios and pre-registered invalidation conditions.

Role: ACTIVE NON-VOTING. Scenario reasoning does not add directional evidence.
Having three scenarios does not mean having three votes -- ``ScenarioSet``
exposes no evidence value and is never passed to the aggregator.

Its real job is to generate **falsifiable invalidation conditions**: concrete,
observable statements registered *before* the outcome is known, which is what
stops a thesis from becoming immortal. Every condition records what would have
to be observed, at which horizon, and which scenario it would move probability
toward.

Probabilities are deliberately categorical bands rather than numbers. There is
no calibration behind a scenario probability here, so emitting "62%" would be
false precision.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from .enums import Direction, FamilyState, Horizon
from .families import FamilyReading
from .horizons import utcnow


class ScenarioKind(Enum):
    BASE = "base"
    ALTERNATIVE = "alternative"
    TAIL = "tail"


class ProbabilityBand(Enum):
    """Categorical likelihood. Not a calibrated probability."""

    LIKELY = "likely"
    POSSIBLE = "possible"
    UNLIKELY = "unlikely"


class ConditionPolarity(Enum):
    """Which way an observed condition pushes the thesis."""

    WEAKENS_BASE = "weakens_base"
    STRENGTHENS_BASE = "strengthens_base"
    ACTIVATES_TAIL = "activates_tail"


@dataclass(frozen=True)
class InvalidationCondition:
    """A falsifiable statement registered before the outcome is known."""

    condition_id: str
    description: str
    observable: str
    horizon: Horizon
    polarity: ConditionPolarity
    moves_toward: ScenarioKind
    registered_at: datetime

    def as_record(self) -> dict[str, object]:
        return {
            "condition_id": self.condition_id,
            "description": self.description,
            "observable": self.observable,
            "horizon": self.horizon.value,
            "polarity": self.polarity.value,
            "moves_toward": self.moves_toward.value,
            "registered_at": self.registered_at.isoformat(),
        }


@dataclass(frozen=True)
class Scenario:
    kind: ScenarioKind
    label: str
    direction: Direction
    band: ProbabilityBand
    narrative: str

    def as_record(self) -> dict[str, object]:
        return {
            "kind": self.kind.value,
            "label": self.label,
            "direction": self.direction.value,
            "band": self.band.value,
            "narrative": self.narrative,
        }


@dataclass(frozen=True)
class ScenarioSet:
    """Base / Alternative / Tail plus what would move probability between them.

    Carries no evidence value and no score: it is reasoning scaffolding, not a
    vote. The aggregator never sees this object.
    """

    base: Scenario
    alternative: Scenario
    tail: Scenario
    conditions: tuple[InvalidationCondition, ...]
    registered_at: datetime
    horizon: Horizon

    @property
    def scenarios(self) -> tuple[Scenario, ...]:
        return (self.base, self.alternative, self.tail)

    def conditions_for(self, kind: ScenarioKind) -> tuple[InvalidationCondition, ...]:
        return tuple(c for c in self.conditions if c.moves_toward is kind)

    def as_record(self) -> dict[str, object]:
        return {
            "horizon": self.horizon.value,
            "registered_at": self.registered_at.isoformat(),
            "base": self.base.as_record(),
            "alternative": self.alternative.as_record(),
            "tail": self.tail.as_record(),
            "conditions": [c.as_record() for c in self.conditions],
        }


def _condition_id(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]


def build_scenario_set(
    *,
    direction: Direction,
    readings: tuple[FamilyReading, ...],
    horizon: Horizon,
    registered_at: datetime | None = None,
) -> ScenarioSet:
    """Derive scenarios and falsifiable conditions from the family readings.

    Deterministic and evidence-driven: the conditions name the families that
    actually carried the thesis, so the thesis can be falsified by watching the
    same evidence that built it change state. That is falsification, not
    circular confirmation -- the distinction being that a *change of state* in a
    thesis input is grounds to weaken the thesis, whereas re-reading its
    unchanged value as agreement would not be new evidence.
    """
    stamped = registered_at or utcnow()

    supporting = tuple(
        r for r in readings if r.state_against(direction) is FamilyState.SUPPORTS
    )
    conflicting = tuple(
        r for r in readings if r.state_against(direction) is FamilyState.CONFLICTS
    )
    unavailable = tuple(r for r in readings if not r.is_available)

    if direction.is_directional:
        base_direction = direction
        alt_direction = direction.opposite()
        base_label = f"{direction.value.title()} thesis holds"
        alt_label = f"{alt_direction.value.title()} reversal"
        base_narrative = (
            f"Carried by {len(supporting)} supporting famil"
            f"{'y' if len(supporting) == 1 else 'ies'}"
            + (f" ({', '.join(r.family_key for r in supporting)})" if supporting else "")
            + "."
        )
    else:
        base_direction = Direction.FLAT
        alt_direction = Direction.FLAT
        base_label = "No directional edge"
        alt_label = "An edge emerges"
        base_narrative = "No family carries a directional read at this horizon."

    if len(supporting) >= 2 and not conflicting:
        base_band = ProbabilityBand.LIKELY
    elif supporting:
        base_band = ProbabilityBand.POSSIBLE
    else:
        base_band = ProbabilityBand.UNLIKELY

    alt_band = (
        ProbabilityBand.POSSIBLE if conflicting or not supporting else ProbabilityBand.UNLIKELY
    )

    base = Scenario(
        kind=ScenarioKind.BASE,
        label=base_label,
        direction=base_direction,
        band=base_band,
        narrative=base_narrative,
    )
    alternative = Scenario(
        kind=ScenarioKind.ALTERNATIVE,
        label=alt_label,
        direction=alt_direction,
        band=alt_band,
        narrative=(
            f"{len(conflicting)} famil{'y' if len(conflicting) == 1 else 'ies'} already "
            f"conflict with the base case"
            + (f" ({', '.join(r.family_key for r in conflicting)})" if conflicting else "")
            + "."
        ),
    )
    tail = Scenario(
        kind=ScenarioKind.TAIL,
        label="Disorderly repricing",
        direction=Direction.FLAT,
        band=ProbabilityBand.UNLIKELY,
        narrative=(
            "A shock large enough that the relationships this thesis relies on stop "
            "operating. Direction is deliberately not asserted: the tail case is a "
            "regime break, not a bet."
        ),
    )

    conditions: list[InvalidationCondition] = []

    for reading in supporting:
        conditions.append(
            InvalidationCondition(
                condition_id=_condition_id("flip", reading.family_key, direction.value),
                description=(
                    f"{reading.label} stops supporting the {direction.value} read and "
                    "turns conflicting."
                ),
                observable=f"family:{reading.family_key}.state == CONFLICTS",
                horizon=reading.horizon,
                polarity=ConditionPolarity.WEAKENS_BASE,
                moves_toward=ScenarioKind.ALTERNATIVE,
                registered_at=stamped,
            )
        )

    for reading in conflicting:
        conditions.append(
            InvalidationCondition(
                condition_id=_condition_id("resolve", reading.family_key, direction.value),
                description=(
                    f"{reading.label} stops conflicting and joins the "
                    f"{direction.value} read."
                ),
                observable=f"family:{reading.family_key}.state == SUPPORTS",
                horizon=reading.horizon,
                polarity=ConditionPolarity.STRENGTHENS_BASE,
                moves_toward=ScenarioKind.BASE,
                registered_at=stamped,
            )
        )

    for reading in unavailable:
        conditions.append(
            InvalidationCondition(
                condition_id=_condition_id("restore", reading.family_key),
                description=(
                    f"{reading.label} becomes available again and can be read. Until "
                    "then its contribution is unknown, not neutral."
                ),
                observable=f"family:{reading.family_key}.available == True",
                horizon=reading.horizon,
                polarity=ConditionPolarity.STRENGTHENS_BASE,
                moves_toward=ScenarioKind.BASE,
                registered_at=stamped,
            )
        )

    conditions.append(
        InvalidationCondition(
            condition_id=_condition_id("tail", horizon.value, direction.value),
            description=(
                "Volatility expands while families that normally move together stop "
                "doing so -- the relationships this thesis depends on are not operating."
            ),
            observable="regime == STRESS and family_disagreement == True",
            horizon=horizon,
            polarity=ConditionPolarity.ACTIVATES_TAIL,
            moves_toward=ScenarioKind.TAIL,
            registered_at=stamped,
        )
    )

    return ScenarioSet(
        base=base,
        alternative=alternative,
        tail=tail,
        conditions=tuple(conditions),
        registered_at=stamped,
        horizon=horizon,
    )


def evaluate_conditions(
    scenario_set: ScenarioSet,
    readings: tuple[FamilyReading, ...],
    direction: Direction,
    regime_is_stress: bool = False,
) -> tuple[InvalidationCondition, ...]:
    """Which pre-registered conditions are now observed to have occurred.

    Evaluated against the current readings only. This reports observations; it
    does not itself change any thesis state -- that decision belongs to the
    thesis layer.
    """
    by_key = {r.family_key: r for r in readings}
    triggered: list[InvalidationCondition] = []

    any_disagreement = any(
        r.state_against(direction) is FamilyState.CONFLICTS for r in readings
    )

    for condition in scenario_set.conditions:
        observable = condition.observable
        if observable.startswith("family:"):
            body = observable[len("family:") :]
            key, _, expectation = body.partition(".")
            reading = by_key.get(key)
            if reading is None:
                continue
            if expectation == "state == CONFLICTS":
                if reading.state_against(direction) is FamilyState.CONFLICTS:
                    triggered.append(condition)
            elif expectation == "state == SUPPORTS":
                if reading.state_against(direction) is FamilyState.SUPPORTS:
                    triggered.append(condition)
            elif expectation == "available == True":
                if reading.is_available:
                    triggered.append(condition)
        elif observable.startswith("regime == STRESS"):
            if regime_is_stress and any_disagreement:
                triggered.append(condition)

    return tuple(triggered)
