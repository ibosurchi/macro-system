"""Architecture B2 -- factor family evaluation and the confirmation cap.

The rule this module exists to enforce:

    Within-family agreement increases the family's STRENGTH.
    It does not create multiple independent confirmations.

    Trend bullish + Momentum bullish + Multi-Timeframe bullish
      -> Directional Family = Strong Bullish Confirmation  (ONE contribution)
      -> NOT three independent bullish votes

``FamilyReading.contribution_count`` is therefore a constant 1, no matter how
many members agree. Strength saturates at STRONG after three agreeing members,
so a fourth and fifth agreeing member add nothing at all.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from .enums import Direction, FamilyState, FamilyStrength, Horizon, Role
from .registry import FamilyDefinition

#: Below this magnitude a member reading is flat -- present, but not directional.
#: Reused from the existing project convention rather than introduced here:
#: _gold_evidence_conflict_diagnostics uses the same 0.05 "flat vote" cutoff.
FLAT_THRESHOLD = 0.05

#: Agreeing members needed for each strength level. Sharply diminishing: the
#: third agreeing member is the last one that changes anything.
_STRENGTH_LADDER: tuple[tuple[int, FamilyStrength], ...] = (
    (3, FamilyStrength.STRONG),
    (2, FamilyStrength.MODERATE),
    (1, FamilyStrength.WEAK),
)


def classify_signal(value: float | None) -> Direction:
    """Map one member signal to a direction.

    ``None`` means the member is unavailable and yields ``Direction.UNAVAILABLE``.
    It is never coerced to ``0.0``, because a missing series and a flat series
    are different facts about the world.
    """
    if value is None:
        return Direction.UNAVAILABLE
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return Direction.UNAVAILABLE
    if numeric != numeric or numeric in (float("inf"), float("-inf")):
        # NaN / infinity is corrupt input, not a flat reading.
        return Direction.UNAVAILABLE
    if numeric > FLAT_THRESHOLD:
        return Direction.BULLISH
    if numeric < -FLAT_THRESHOLD:
        return Direction.BEARISH
    return Direction.FLAT


@dataclass(frozen=True)
class FamilyReading:
    """One family's reading. Exactly one capped contribution, always."""

    family_key: str
    label: str
    role: Role
    horizon: Horizon
    direction: Direction
    strength: FamilyStrength
    agreeing_members: tuple[str, ...]
    dissenting_members: tuple[str, ...]
    flat_members: tuple[str, ...]
    unavailable_members: tuple[str, ...]
    rationale: str

    @property
    def contribution_count(self) -> int:
        """A family contributes once. This is not configurable."""
        return 1

    @property
    def is_available(self) -> bool:
        return self.direction is not Direction.UNAVAILABLE

    @property
    def has_internal_disagreement(self) -> bool:
        return bool(self.dissenting_members)

    def state_against(self, candidate: Direction) -> FamilyState:
        """This family's state relative to a candidate direction under test."""
        if self.direction is Direction.UNAVAILABLE:
            return FamilyState.UNAVAILABLE
        if self.direction is Direction.FLAT:
            return FamilyState.NEUTRAL
        if not candidate.is_directional:
            # Nothing to support or conflict with.
            return FamilyState.NEUTRAL
        if self.direction is candidate:
            return FamilyState.SUPPORTS
        return FamilyState.CONFLICTS

    def as_record(self) -> dict[str, object]:
        """Structured, queryable audit record -- why this family reached its state."""
        return {
            "family": self.family_key,
            "label": self.label,
            "role": self.role.value,
            "horizon": self.horizon.value,
            "direction": self.direction.value,
            "strength": self.strength.name,
            "contribution_count": self.contribution_count,
            "agreeing": list(self.agreeing_members),
            "dissenting": list(self.dissenting_members),
            "flat": list(self.flat_members),
            "unavailable": list(self.unavailable_members),
            "rationale": self.rationale,
        }


def _strength_for(agreeing: int, dissenting: int) -> FamilyStrength:
    """Strength from agreement count, with a downgrade for internal dissent."""
    if agreeing <= 0:
        return FamilyStrength.NONE
    strength = FamilyStrength.WEAK
    for threshold, level in _STRENGTH_LADDER:
        if agreeing >= threshold:
            strength = level
            break
    if dissenting:
        # Internal disagreement is information: it lowers strength but never
        # flips the family, and never removes the family's single contribution.
        strength = FamilyStrength(max(FamilyStrength.WEAK.value, strength.value - 1))
    return strength


def evaluate_family(
    definition: FamilyDefinition,
    signals: Mapping[str, float | None],
) -> FamilyReading:
    """Evaluate one family from its member signals.

    ``signals`` may omit a member, which is treated as unavailable. Passing a
    key that is not a declared member of this family is an error: it would be a
    way to smuggle extra evidence into a frozen family.
    """
    unknown = set(signals) - set(definition.members)
    if unknown:
        raise ValueError(
            f"{definition.key}: {sorted(unknown)} are not members of this family. "
            "Family membership is frozen in the registry."
        )

    per_member: dict[str, Direction] = {
        member: classify_signal(signals.get(member)) for member in definition.members
    }

    unavailable = tuple(m for m, d in per_member.items() if d is Direction.UNAVAILABLE)
    flat = tuple(m for m, d in per_member.items() if d is Direction.FLAT)
    bullish = tuple(m for m, d in per_member.items() if d is Direction.BULLISH)
    bearish = tuple(m for m, d in per_member.items() if d is Direction.BEARISH)

    if len(unavailable) == len(definition.members):
        return FamilyReading(
            family_key=definition.key,
            label=definition.label,
            role=definition.role,
            horizon=definition.horizon,
            direction=Direction.UNAVAILABLE,
            strength=FamilyStrength.NONE,
            agreeing_members=(),
            dissenting_members=(),
            flat_members=(),
            unavailable_members=unavailable,
            rationale=(
                "Unavailable: no member of this family returned usable data. "
                "This is not a neutral reading and must reduce Data Confidence."
            ),
        )

    if len(bullish) > len(bearish):
        direction, agreeing, dissenting = Direction.BULLISH, bullish, bearish
    elif len(bearish) > len(bullish):
        direction, agreeing, dissenting = Direction.BEARISH, bearish, bullish
    else:
        # Either nothing is directional, or directional members cancel exactly.
        direction, agreeing, dissenting = Direction.FLAT, (), bullish + bearish

    strength = _strength_for(len(agreeing), len(dissenting))

    if direction is Direction.FLAT:
        if dissenting:
            rationale = (
                f"Flat: {len(dissenting)} directional members cancel exactly "
                f"({', '.join(sorted(dissenting))}). Data is present; the family "
                "shows no net directional evidence."
            )
        else:
            rationale = (
                f"Flat: {len(flat)} member(s) present and below the "
                f"{FLAT_THRESHOLD} directional threshold. Data is present; there "
                "is no directional evidence."
            )
    else:
        rationale = (
            f"{direction.value.title()} at {strength.name} strength from "
            f"{len(agreeing)} agreeing member(s) ({', '.join(sorted(agreeing))})"
        )
        if dissenting:
            rationale += f"; downgraded by dissent from {', '.join(sorted(dissenting))}"
        if unavailable:
            rationale += f"; unavailable: {', '.join(sorted(unavailable))}"
        rationale += ". One capped contribution regardless of member count."

    return FamilyReading(
        family_key=definition.key,
        label=definition.label,
        role=definition.role,
        horizon=definition.horizon,
        direction=direction,
        strength=strength,
        agreeing_members=tuple(sorted(agreeing)),
        dissenting_members=tuple(sorted(dissenting)),
        flat_members=flat,
        unavailable_members=unavailable,
        rationale=rationale,
    )


def evaluate_families(
    definitions: tuple[FamilyDefinition, ...],
    signals_by_family: Mapping[str, Mapping[str, float | None]],
) -> tuple[FamilyReading, ...]:
    """Evaluate several families. A family with no entry is fully unavailable."""
    return tuple(
        evaluate_family(definition, signals_by_family.get(definition.key, {}))
        for definition in definitions
    )
