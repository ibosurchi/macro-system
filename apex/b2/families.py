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
from .horizons import horizon_compatible
from .registry import BOUNDED_UNIT_FLAT_THRESHOLD, FamilyDefinition, MemberSpec

#: Default neutral band, for a member on the BOUNDED_UNIT scale.
#:
#: Retained under its original name and value because that is the scale it was
#: always correct for: it is the 0.05 "flat vote" cutoff
#: _gold_evidence_conflict_diagnostics already uses on a [-1, 1] score.
#:
#: It is NO LONGER applied package-wide. A member declares its own scale and
#: band in ``registry.MemberSpec``, and ``evaluate_family`` reads the band from
#: there. Applying this constant to a member on a standard-deviation scale --
#: where 0.05 means five hundredths of a sigma rather than five percent of full
#: scale -- classified ordinary noise as directional evidence. That is the
#: defect the per-member declaration exists to close, so callers that classify a
#: registered member must pass the member's own threshold rather than relying on
#: this default.
FLAT_THRESHOLD = BOUNDED_UNIT_FLAT_THRESHOLD

#: Agreeing members needed for each strength level. Sharply diminishing: the
#: third agreeing member is the last one that changes anything.
_STRENGTH_LADDER: tuple[tuple[int, FamilyStrength], ...] = (
    (3, FamilyStrength.STRONG),
    (2, FamilyStrength.MODERATE),
    (1, FamilyStrength.WEAK),
)


def classify_signal(
    value: float | None, threshold: float = FLAT_THRESHOLD
) -> Direction:
    """Map one member signal to a direction, against that member's own band.

    ``None`` means the member is unavailable and yields ``Direction.UNAVAILABLE``.
    It is never coerced to ``0.0``, because a missing series and a flat series
    are different facts about the world.

    ``threshold`` is the member's neutral band **in the member's own units**.
    It defaults to the bounded-unit band so a caller holding a [-1, 1] score
    behaves exactly as before; a caller classifying a registered family member
    must pass ``MemberSpec.threshold`` instead, because a band is meaningless
    without the scale it belongs to.
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
    band = abs(float(threshold))
    if numeric > band:
        return Direction.BULLISH
    if numeric < -band:
        return Direction.BEARISH
    return Direction.FLAT


def _numeric_or_none(value: float | None) -> float | None:
    """The value as a plain float, or None when it is not a usable number.

    Used only to record what a member carried. It applies no threshold and
    makes no directional judgement: NaN, infinity and unparseable input all
    become None, matching what ``classify_signal`` treats as unavailable, so the
    stored value never disagrees with the stored classification.
    """
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if numeric != numeric or numeric in (float("inf"), float("-inf")):
        return None
    return numeric


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
    #: The numeric value each member actually carried, in declaration order.
    #: ``None`` for a member that was unavailable, preserved as None rather than
    #: dropped so the distinction survives into storage. Without this a stored
    #: record cannot be re-thresholded, ablated or re-normalised later -- the
    #: reading would be permanently frozen against whatever band happened to be
    #: in force when it was written.
    member_values: tuple[tuple[str, float | None], ...] = ()
    #: Members refused because their publication cadence is too slow to be
    #: evidence at the decision horizon. These are NOT missing data: the series
    #: arrived and was usable, and the architecture declined to read it at this
    #: horizon. Reported apart from ``unavailable_members`` for that reason.
    horizon_excluded_members: tuple[str, ...] = ()
    #: The decision horizon this reading was evaluated for, when one was
    #: supplied. None means no horizon filter was applied.
    decision_horizon: Horizon | None = None

    @property
    def contribution_count(self) -> int:
        """A family contributes once. This is not configurable."""
        return 1

    @property
    def is_available(self) -> bool:
        return self.direction is not Direction.UNAVAILABLE

    @property
    def is_horizon_excluded(self) -> bool:
        """True when this family is silent ONLY because of horizon incompatibility.

        The distinction matters downstream. An unavailable family normally means
        "the system does not know", which must cap Data Confidence and can
        degrade the decision. A horizon-excluded family means "this evidence
        exists and is fine, and is too slow to speak at this horizon" -- a
        structural property of the architecture, not a data outage. Reporting
        the second as the first would make a correct design decision look like a
        broken feed on every single Execution record.
        """
        return (
            not self.is_available
            and bool(self.horizon_excluded_members)
            and not self.unavailable_members
        )

    @property
    def value_for(self) -> dict[str, float | None]:
        return {key: value for key, value in self.member_values}

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
            # Schema v3: the evidence itself, not merely the verdict on it.
            "member_values": [
                {"member": key, "value": value, "available": value is not None}
                for key, value in self.member_values
            ],
            "horizon_excluded": list(self.horizon_excluded_members),
            "is_horizon_excluded": self.is_horizon_excluded,
            "decision_horizon": (
                self.decision_horizon.value if self.decision_horizon else None
            ),
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
    decision_horizon: Horizon | None = None,
) -> FamilyReading:
    """Evaluate one family from its member signals.

    ``signals`` may omit a member, which is treated as unavailable. Passing a
    key that is not a declared member of this family is an error: it would be a
    way to smuggle extra evidence into a frozen family.

    ``decision_horizon``, when supplied, applies the horizon-compatibility rule
    from ``horizons``: a member whose slowest series publishes more slowly than
    the horizon allows is refused as evidence at that horizon. It is recorded as
    horizon-excluded rather than merely unavailable, because "too slow to speak
    here" and "we do not know" are different facts and only the second is a data
    problem. Passing None applies no horizon filter, which is what a caller
    reasoning about a family in the abstract wants.

    Each member is classified against ITS OWN neutral band, taken from the
    registry's ``MemberSpec``. A single package-wide threshold cannot be correct
    across members that do not share a scale.
    """
    unknown = set(signals) - set(definition.members)
    if unknown:
        raise ValueError(
            f"{definition.key}: {sorted(unknown)} are not members of this family. "
            "Family membership is frozen in the registry."
        )

    specs: dict[str, MemberSpec | None] = {
        member: definition.spec_for(member) for member in definition.members
    }

    horizon_excluded: list[str] = []
    per_member: dict[str, Direction] = {}
    member_values: list[tuple[str, float | None]] = []

    for member in definition.members:
        spec = specs[member]
        raw = signals.get(member)

        if (
            decision_horizon is not None
            and spec is not None
            and not horizon_compatible(spec.frequency, decision_horizon)
        ):
            # Refused BEFORE the value is read, so a horizon-incompatible member
            # cannot contribute even accidentally. The value is still recorded,
            # so a later analyst can see exactly what was withheld and why.
            horizon_excluded.append(member)
            per_member[member] = Direction.UNAVAILABLE
            member_values.append((member, _numeric_or_none(raw)))
            continue

        threshold = spec.threshold if spec is not None else FLAT_THRESHOLD
        per_member[member] = classify_signal(raw, threshold)
        member_values.append((member, _numeric_or_none(raw)))

    values = tuple(member_values)
    excluded = tuple(horizon_excluded)

    unavailable = tuple(
        m for m, d in per_member.items()
        if d is Direction.UNAVAILABLE and m not in excluded
    )
    flat = tuple(m for m, d in per_member.items() if d is Direction.FLAT)
    bullish = tuple(m for m, d in per_member.items() if d is Direction.BULLISH)
    bearish = tuple(m for m, d in per_member.items() if d is Direction.BEARISH)

    if len(unavailable) + len(excluded) == len(definition.members):
        if excluded and not unavailable:
            rationale = (
                "Unavailable at this horizon: every member of this family "
                f"publishes more slowly than the {decision_horizon.value if decision_horizon else 'requested'} "
                "horizon accepts (" + ", ".join(sorted(excluded)) + "). The data "
                "arrived and is usable; it is refused HERE because slower "
                "evidence may condition this horizon but must not be mixed in as "
                "though it arrived at the same cadence. This is a structural "
                "exclusion, not missing data, and must not be read as one."
            )
        else:
            rationale = (
                "Unavailable: no member of this family returned usable data. "
                "This is not a neutral reading and must reduce Data Confidence."
            )
            if excluded:
                rationale += (
                    " Additionally horizon-excluded: " + ", ".join(sorted(excluded)) + "."
                )
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
            rationale=rationale,
            member_values=values,
            horizon_excluded_members=excluded,
            decision_horizon=decision_horizon,
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

    if excluded:
        rationale += (
            f" Horizon-excluded (too slow for the "
            f"{decision_horizon.value if decision_horizon else 'requested'} horizon): "
            + ", ".join(sorted(excluded))
            + "."
        )

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
        member_values=values,
        horizon_excluded_members=excluded,
        decision_horizon=decision_horizon,
    )


def evaluate_families(
    definitions: tuple[FamilyDefinition, ...],
    signals_by_family: Mapping[str, Mapping[str, float | None]],
    decision_horizon: Horizon | None = None,
) -> tuple[FamilyReading, ...]:
    """Evaluate several families. A family with no entry is fully unavailable.

    ``decision_horizon`` is passed through to every family, which is what makes
    the same snapshot of evidence produce genuinely different readings at
    different horizons instead of one claim wearing two labels.
    """
    return tuple(
        evaluate_family(
            definition,
            signals_by_family.get(definition.key, {}),
            decision_horizon,
        )
        for definition in definitions
    )
