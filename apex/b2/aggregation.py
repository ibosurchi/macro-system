"""Architecture B2 -- saturating aggregation across families.

Two rules are enforced here.

1.  Aggregation across families is **saturating (concave), not additive**. The
    fifth agreeing family is worth materially less than the first. Family
    contributions are ranked by strength and each successive one is discounted
    geometrically, so evidence accumulates with diminishing returns instead of
    summing.

2.  A **global cap** on total evidential contribution, applied per group and
    then overall. During systemic stress every macro family aligns by
    construction, so unbounded agreement would manufacture false certainty.
    The macro and technical blocks are also not fully independent of each
    other, which is why their agreement is capped again jointly.

Disagreement is never hidden inside the aggregate. ``AggregateResult`` reports
conflicting and unavailable families explicitly alongside the evidence value,
because disagreement is itself information.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .enums import Direction, FamilyState, FamilyStrength
from .families import FamilyReading


@dataclass(frozen=True)
class AggregationConfig:
    """Shape constants for the concave aggregation.

    IMPORTANT: these are architectural shape constants, not fitted parameters.
    They were chosen to satisfy the structural requirements above and have NOT
    been tuned against historical results -- doing so would make this an
    overfitting surface. They are injectable so a future validation exercise
    can vary them deliberately rather than by accident.

    Only two numbers are chosen here: the strength weights and the diminishing
    factor. **Every cap is derived from them rather than picked**, so there is
    no separate dial to tune:

    *   ``block_cap`` = the value of two fully-aligned STRONG families
        (``strong_weight * (1 + diminishing_factor)``). However many families
        inside a block align, the block is worth at most two independent strong
        families. This is what stops systemic stress -- where every macro
        family aligns by construction -- from reading as overwhelming evidence.
    *   ``global_cap`` applies the same diminishing factor *between* blocks,
        because macro and technical evidence are not fully independent of each
        other either.

    Caps are expressed in "full-strength-family equivalents".
    """

    strong_weight: float = 1.00
    moderate_weight: float = 0.65
    weak_weight: float = 0.35

    #: Each successive agreeing family is worth this fraction of the previous
    #: one. At 0.55 the sequence is 1.00, 0.55, 0.30, 0.17, 0.09 -- the fifth
    #: agreeing family is worth about 9% of the first.
    diminishing_factor: float = 0.55

    #: Escape hatches for a future calibration exercise. Left None so the caps
    #: stay derived rather than hand-set.
    block_cap_override: float | None = None
    global_cap_override: float | None = None

    #: Set True only once a calibration exercise has justified these values.
    calibrated: bool = False

    @property
    def block_cap(self) -> float:
        """Ceiling for one horizon block: two fully-aligned STRONG families."""
        if self.block_cap_override is not None:
            return self.block_cap_override
        return self.strong_weight * (1.0 + self.diminishing_factor)

    @property
    def macro_group_cap(self) -> float:
        return self.block_cap

    @property
    def technical_group_cap(self) -> float:
        return self.block_cap

    @property
    def global_cap(self) -> float:
        """Ceiling across blocks, discounted again for macro/technical dependence."""
        if self.global_cap_override is not None:
            return self.global_cap_override
        return self.block_cap * (1.0 + self.diminishing_factor)

    def weight_for(self, strength: FamilyStrength) -> float:
        return {
            FamilyStrength.STRONG: self.strong_weight,
            FamilyStrength.MODERATE: self.moderate_weight,
            FamilyStrength.WEAK: self.weak_weight,
            FamilyStrength.NONE: 0.0,
        }[strength]


DEFAULT_AGGREGATION = AggregationConfig()


def saturating_total(weights: list[float], config: AggregationConfig) -> float:
    """Concave accumulation of family weights.

    Strongest first, each successive contribution discounted geometrically.
    This is monotone non-decreasing in the number of agreeing families but has
    strictly diminishing marginal value, which is the property that stops
    correlated agreement from inflating evidence linearly.
    """
    ordered = sorted((w for w in weights if w > 0.0), reverse=True)
    total = 0.0
    for index, weight in enumerate(ordered):
        total += weight * (config.diminishing_factor ** index)
    return total


@dataclass(frozen=True)
class GroupEvidence:
    """Evidence from one horizon group (macro or technical)."""

    group: str
    supporting: tuple[str, ...]
    conflicting: tuple[str, ...]
    neutral: tuple[str, ...]
    unavailable: tuple[str, ...]
    raw_support: float
    raw_conflict: float
    support: float
    conflict: float
    cap: float
    cap_applied: bool

    @property
    def net(self) -> float:
        return self.support - self.conflict


@dataclass(frozen=True)
class AggregateResult:
    """The aggregate for one candidate direction. Never a single hidden number."""

    candidate: Direction
    groups: tuple[GroupEvidence, ...]
    net_evidence: float
    global_cap_applied: bool
    caps_applied: tuple[str, ...]
    conflicting_families: tuple[str, ...]
    unavailable_families: tuple[str, ...]
    neutral_families: tuple[str, ...]
    supporting_families: tuple[str, ...]
    config: AggregationConfig = field(repr=False, default=DEFAULT_AGGREGATION)

    @property
    def disagreement_present(self) -> bool:
        """Exposed separately so a conflict can never be averaged out of sight."""
        return bool(self.conflicting_families)

    @property
    def contribution_count(self) -> int:
        """One per participating family -- never one per agreeing indicator."""
        return len(self.supporting_families) + len(self.conflicting_families)

    def group(self, name: str) -> GroupEvidence | None:
        for group in self.groups:
            if group.group == name:
                return group
        return None

    def as_record(self) -> dict[str, object]:
        return {
            "candidate": self.candidate.value,
            "net_evidence": round(self.net_evidence, 4),
            "contribution_count": self.contribution_count,
            "disagreement_present": self.disagreement_present,
            "supporting": list(self.supporting_families),
            "conflicting": list(self.conflicting_families),
            "neutral": list(self.neutral_families),
            "unavailable": list(self.unavailable_families),
            "caps_applied": list(self.caps_applied),
            "groups": [
                {
                    "group": g.group,
                    "support": round(g.support, 4),
                    "conflict": round(g.conflict, 4),
                    "net": round(g.net, 4),
                    "cap": g.cap,
                    "cap_applied": g.cap_applied,
                }
                for g in self.groups
            ],
            "aggregation_calibrated": self.config.calibrated,
        }


def _group_evidence(
    group_name: str,
    readings: tuple[FamilyReading, ...],
    candidate: Direction,
    cap: float,
    config: AggregationConfig,
) -> GroupEvidence:
    supporting: list[str] = []
    conflicting: list[str] = []
    neutral: list[str] = []
    unavailable: list[str] = []
    support_weights: list[float] = []
    conflict_weights: list[float] = []

    for reading in readings:
        state = reading.state_against(candidate)
        weight = config.weight_for(reading.strength)
        if state is FamilyState.SUPPORTS:
            supporting.append(reading.family_key)
            support_weights.append(weight)
        elif state is FamilyState.CONFLICTS:
            conflicting.append(reading.family_key)
            conflict_weights.append(weight)
        elif state is FamilyState.UNAVAILABLE:
            unavailable.append(reading.family_key)
        else:
            neutral.append(reading.family_key)

    raw_support = saturating_total(support_weights, config)
    raw_conflict = saturating_total(conflict_weights, config)
    support = min(raw_support, cap)
    conflict = min(raw_conflict, cap)

    return GroupEvidence(
        group=group_name,
        supporting=tuple(supporting),
        conflicting=tuple(conflicting),
        neutral=tuple(neutral),
        unavailable=tuple(unavailable),
        raw_support=raw_support,
        raw_conflict=raw_conflict,
        support=support,
        conflict=conflict,
        cap=cap,
        cap_applied=raw_support > cap or raw_conflict > cap,
    )


def aggregate(
    readings: tuple[FamilyReading, ...],
    candidate: Direction,
    macro_keys: frozenset[str],
    technical_keys: frozenset[str],
    config: AggregationConfig = DEFAULT_AGGREGATION,
) -> AggregateResult:
    """Aggregate family readings against one candidate direction."""
    macro = tuple(r for r in readings if r.family_key in macro_keys)
    technical = tuple(r for r in readings if r.family_key in technical_keys)

    macro_group = _group_evidence("macro", macro, candidate, config.macro_group_cap, config)
    technical_group = _group_evidence(
        "technical", technical, candidate, config.technical_group_cap, config
    )
    groups = (macro_group, technical_group)

    caps: list[str] = []
    if macro_group.cap_applied:
        caps.append("macro_group_cap")
    if technical_group.cap_applied:
        caps.append("technical_group_cap")

    raw_net = sum(g.net for g in groups)
    magnitude = min(abs(raw_net), config.global_cap)
    global_capped = abs(raw_net) > config.global_cap
    if global_capped:
        caps.append("global_cap")
    net = magnitude if raw_net >= 0 else -magnitude

    return AggregateResult(
        candidate=candidate,
        groups=groups,
        net_evidence=net,
        global_cap_applied=global_capped,
        caps_applied=tuple(caps),
        supporting_families=tuple(k for g in groups for k in g.supporting),
        conflicting_families=tuple(k for g in groups for k in g.conflicting),
        neutral_families=tuple(k for g in groups for k in g.neutral),
        unavailable_families=tuple(k for g in groups for k in g.unavailable),
        config=config,
    )


def resolve_direction(
    readings: tuple[FamilyReading, ...],
    macro_keys: frozenset[str],
    technical_keys: frozenset[str],
    config: AggregationConfig = DEFAULT_AGGREGATION,
) -> tuple[Direction, AggregateResult]:
    """Pick the better-supported candidate direction and return its aggregate.

    Both candidates are evaluated symmetrically. If neither has positive net
    evidence, or they tie, the result is FLAT -- an honest "no edge", which is
    a different outcome from missing data.
    """
    bullish = aggregate(readings, Direction.BULLISH, macro_keys, technical_keys, config)
    bearish = aggregate(readings, Direction.BEARISH, macro_keys, technical_keys, config)

    if bullish.net_evidence > 0 and bullish.net_evidence > bearish.net_evidence:
        return Direction.BULLISH, bullish
    if bearish.net_evidence > 0 and bearish.net_evidence > bullish.net_evidence:
        return Direction.BEARISH, bearish

    flat = aggregate(readings, Direction.FLAT, macro_keys, technical_keys, config)
    return Direction.FLAT, flat
