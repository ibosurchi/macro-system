"""Architecture B2 -- five separate confidence dimensions.

Macro, Technical, Execution, Regime and Data confidence are kept apart and are
**never averaged into a single number**. ``ConfidenceSet`` provides no mean, no
overall score and no ordering between the five: collapsing them would hide
exactly the information they exist to surface.

Technical (and Macro) Confidence is an **output**, built from counts of
independent factor families in agreement -- not from a smooth weighted average
of correlated components. Counting families rather than indicators is what
keeps three agreeing members of one family from reading as three confirmations.

Disagreement is reported alongside the levels, never absorbed into them.

Confidence is categorical. Until a calibration exercise exists there is no
basis for a percentage, so none is emitted.
"""
from __future__ import annotations

from dataclasses import dataclass

from typing import Mapping

from .enums import ConfidenceLevel, FamilyState, Direction, Horizon
from .families import FamilyReading
from .gates import GateOutcome, combined_confidence_ceiling
from .horizons import Staleness, horizon_compatible
from .registry import (
    EXPECTATION_VERSION,
    FAMILIES_BY_KEY,
    EvidenceExpectation,
    FamilyDefinition,
    dormant_canonical_macro_families,
)

#: Agreeing-family counts and the confidence they justify. Two independent
#: families is the point at which a read stops resting on a single source.
_COUNT_TO_LEVEL: tuple[tuple[int, ConfidenceLevel], ...] = (
    (3, ConfidenceLevel.HIGH),
    (2, ConfidenceLevel.MODERATE),
    (0, ConfidenceLevel.LOW),
)


def level_from_agreeing_count(count: int) -> ConfidenceLevel:
    for threshold, level in _COUNT_TO_LEVEL:
        if count >= threshold:
            return level
    return ConfidenceLevel.LOW


# ===========================================================================
# H3 -- EVIDENCE COVERAGE
#
# Data Confidence answers two questions that were previously conflated:
#
#   QUALITY   is the evidence I DO have sound?      (staleness, source conflict)
#   COVERAGE  do I have the evidence I EXPECTED?    (this section)
#
# Before H3 only a family-level boolean existed, and ``FamilyReading`` reports a
# family as available when a SINGLE member survives. Data Confidence could
# therefore report HIGH with five of fifteen declared member signals present:
# the four silent members of a four-member family were invisible.
#
# Both terms stay INTERNAL. They are combined into the one public ``data``
# dimension, because the five-dimension model is deliberately stable and a sixth
# public dimension would be a larger change than the defect warrants.
#
# Three properties of this computation are load-bearing:
#
#   DIRECTION-BLIND  It reads only whether a value exists, never what the value
#                    says. A coverage term that could see direction would be a
#                    route for Data Confidence to leak into the thesis.
#
#   HORIZON-AWARE    A member too slow for the decision horizon is removed from
#                    BOTH sides of the ratio. It is not missing evidence; the
#                    architecture declined to read it here. Counting it as
#                    missing would report H1 working correctly as a data outage.
#
#   FLOORED          Coverage can lower HIGH to MODERATE and stops there. It
#                    never reaches LOW. See COVERAGE_FLOOR.
# ===========================================================================

#: Coverage ratios and the confidence they justify BEFORE the floor is applied.
#:
#: 0.80 is not a tuned number and is not calibrated against any outcome. It is
#: the point at which more than one expected member in five is absent, which is
#: the coarsest defensible statement of "materially incomplete" available before
#: a corpus exists. It is declared here as one constant so a later calibration
#: exercise has exactly one number to revise.
_COVERAGE_TO_LEVEL: tuple[tuple[float, ConfidenceLevel], ...] = (
    (0.80, ConfidenceLevel.HIGH),
    (0.50, ConfidenceLevel.MODERATE),
    (0.0, ConfidenceLevel.LOW),
)

#: The floor coverage may never breach. THIS IS INVARIANT I-1 IN CODE.
#:
#: Missing evidence is a reason to stop claiming completeness; it is not by
#: itself a reason to declare the system degraded. LOW Data Confidence is load
#: bearing downstream -- it caps macro and technical conviction and raises the
#: size-directive confirmation bar -- and those consequences belong to the
#: pre-existing conditions that already produce LOW (a critical family
#: unavailable, two or more families unavailable, a BROKEN series). H3 adds no
#: new route to LOW, so no decision changes because coverage was measured.
COVERAGE_FLOOR: ConfidenceLevel = ConfidenceLevel.MODERATE

#: The ceiling imposed while any canonical universal macro family is dormant.
#: MODERATE, never LOW, for the same reason as COVERAGE_FLOOR.
ARCHITECTURAL_CAP_LEVEL: ConfidenceLevel = ConfidenceLevel.MODERATE


def _member_key(family_key: str, member: str) -> str:
    """Qualified member identity, so a record never depends on global uniqueness."""
    return f"{family_key}.{member}"


@dataclass(frozen=True)
class EvidenceCoverage:
    """How much of the EXPECTED evidence actually arrived, for one horizon.

    Every field is part of the answer to "why did Data Confidence have this
    value?", which is what H4 re-scorability requires. The three exclusion sets
    are reported separately rather than merged because they are excluded for
    three genuinely different reasons, and collapsing them would make a
    deliberate architectural choice indistinguishable from a data gap.
    """

    #: Members counted in the denominator: REQUIRED or EXPECTED, and eligible at
    #: this horizon. Qualified ``family.member`` keys, in declaration order.
    expected: tuple[str, ...]
    #: The subset of ``expected`` that carried no usable value this evaluation.
    missing: tuple[str, ...]
    #: Removed because the member publishes too slowly for this horizon (H1).
    excluded_horizon: tuple[str, ...]
    #: Removed because the member can never be obtained in this project.
    excluded_unobtainable: tuple[str, ...]
    #: Removed because its absence is not a data deficiency.
    excluded_optional: tuple[str, ...]
    #: Readings with no matching definition, so their members could not be
    #: classified at all. Normally empty; recorded so nothing vanishes silently.
    unclassified: tuple[str, ...]
    #: available_expected / eligible_expected. None when the denominator is zero,
    #: which is a distinct fact from 1.0 and is never coerced to it.
    ratio: float | None
    #: The level coverage justifies, AFTER COVERAGE_FLOOR. This is what is used.
    level: ConfidenceLevel
    #: The level before the floor. Recorded so an auditor can see both what the
    #: ratio implied and that the floor is what stopped it, rather than having to
    #: trust that I-1 was honoured.
    level_before_floor: ConfidenceLevel
    decision_horizon: Horizon | None
    expectation_version: str = EXPECTATION_VERSION

    @property
    def defined(self) -> bool:
        """False when nothing was expected, so no ratio exists to interpret."""
        return self.ratio is not None

    @property
    def eligible_count(self) -> int:
        return len(self.expected)

    @property
    def present_count(self) -> int:
        return len(self.expected) - len(self.missing)

    @property
    def is_complete(self) -> bool:
        return self.defined and not self.missing

    def as_record(self) -> dict[str, object]:
        return {
            "expected": list(self.expected),
            "missing": list(self.missing),
            "excluded_horizon": list(self.excluded_horizon),
            "excluded_unobtainable": list(self.excluded_unobtainable),
            "excluded_optional": list(self.excluded_optional),
            "unclassified": list(self.unclassified),
            "eligible_count": self.eligible_count,
            "present_count": self.present_count,
            "coverage_ratio": self.ratio,
            "coverage_level": self.level.name,
            "coverage_level_before_floor": self.level_before_floor.name,
            "coverage_floor": COVERAGE_FLOOR.name,
            "decision_horizon": (
                self.decision_horizon.value if self.decision_horizon else None
            ),
            "expectation_version": self.expectation_version,
        }


def level_from_coverage_ratio(ratio: float | None) -> ConfidenceLevel:
    """The level a coverage ratio justifies, BEFORE the floor.

    ``None`` -- nothing was expected at this horizon -- yields LOW rather than
    HIGH. An empty denominator is not evidence of completeness, and defaulting
    it upward is exactly the "absence reads as success" failure this module
    exists to prevent. The floor then lifts it to MODERATE, so the conservative
    reading still cannot degrade a decision.
    """
    if ratio is None:
        return ConfidenceLevel.LOW
    for threshold, level in _COVERAGE_TO_LEVEL:
        if ratio >= threshold:
            return level
    return ConfidenceLevel.LOW


def evidence_coverage(
    *,
    readings: tuple[FamilyReading, ...],
    decision_horizon: Horizon | None = None,
    definitions: Mapping[str, FamilyDefinition] = FAMILIES_BY_KEY,
) -> EvidenceCoverage:
    """Member-level coverage of the evidence the architecture expected.

    Pure, and deliberately narrow: it reads the declared expectation and
    frequency of each member from the registry, and whether that member carried
    a value in ``FamilyReading.member_values``. It never inspects the value
    itself, so flipping every sign in the evaluation cannot change the result.

    A value of ``0.0`` is PRESENT. Zero is a measurement -- the member was read
    and showed nothing -- and treating it as absent would recreate the
    flat/unavailable collapse the whole package is built to prevent. Only
    ``None`` counts as missing, which is already what ``member_values`` stores
    for anything unusable, NaN and infinity included.

    Exclusion precedence is expectation FIRST, then horizon. Expectation is a
    permanent property of the member; horizon eligibility varies per evaluation.
    A member excluded on both grounds is reported under its permanent reason.
    """
    expected: list[str] = []
    missing: list[str] = []
    excluded_horizon: list[str] = []
    excluded_unobtainable: list[str] = []
    excluded_optional: list[str] = []
    unclassified: list[str] = []

    for reading in readings:
        definition = definitions.get(reading.family_key)
        if definition is None:
            # No declaration, so no expectation and no denominator entry. Named
            # rather than dropped: a family the registry cannot classify is a
            # fact an auditor needs, not one to discover from a count mismatch.
            unclassified.append(reading.family_key)
            continue

        values = reading.value_for
        for member in definition.members:
            spec = definition.spec_for(member)
            qualified = _member_key(definition.key, member)
            expectation = (
                spec.expectation if spec is not None else EvidenceExpectation.EXPECTED
            )

            if expectation is EvidenceExpectation.UNOBTAINABLE:
                excluded_unobtainable.append(qualified)
                continue
            if expectation is EvidenceExpectation.OPTIONAL:
                excluded_optional.append(qualified)
                continue
            if (
                decision_horizon is not None
                and spec is not None
                and not horizon_compatible(spec.frequency, decision_horizon)
            ):
                # H1: removed from BOTH sides of the ratio. Its data may well
                # have arrived; it is refused HERE, and refusing it is not a
                # deficiency of the evidence set.
                excluded_horizon.append(qualified)
                continue

            expected.append(qualified)
            if values.get(member) is None:
                missing.append(qualified)

    ratio: float | None = None
    if expected:
        ratio = (len(expected) - len(missing)) / len(expected)

    before_floor = level_from_coverage_ratio(ratio)
    # Raise to the floor. Coverage may lower confidence, never condemn it.
    level = ConfidenceLevel(max(before_floor.value, COVERAGE_FLOOR.value))

    return EvidenceCoverage(
        expected=tuple(expected),
        missing=tuple(missing),
        excluded_horizon=tuple(excluded_horizon),
        excluded_unobtainable=tuple(excluded_unobtainable),
        excluded_optional=tuple(excluded_optional),
        unclassified=tuple(unclassified),
        ratio=ratio,
        level=level,
        level_before_floor=before_floor,
        decision_horizon=decision_horizon,
    )


def architectural_completeness_cap(
    dormant_canonical: tuple[str, ...],
) -> ConfidenceLevel | None:
    """The ceiling imposed while canonical universal macro families are dormant.

    This is the H3 answer to a question coverage cannot reach. Coverage measures
    the evidence the architecture DECLARED it would gather; it says nothing about
    evidence the architecture never declared because no data source exists. The
    voting core here was drawn to match what this project happens to have, so
    "every declared family spoke" is a circular basis for claiming HIGH.

    While any canonical macro family is dormant, the macro evidence base is
    structurally incomplete and Data Confidence must not report HIGH. The cap is
    derived from live registry state rather than hard-coded, so it lifts by
    itself the moment such a family is genuinely activated.

    Returns None -- no cap -- when the canonical set is complete.
    """
    return ARCHITECTURAL_CAP_LEVEL if dormant_canonical else None


@dataclass(frozen=True)
class ConfidenceSet:
    """Five separate dimensions. Deliberately offers no way to average them."""

    macro: ConfidenceLevel
    technical: ConfidenceLevel
    execution: ConfidenceLevel
    regime: ConfidenceLevel
    data: ConfidenceLevel
    disagreements: tuple[str, ...]
    unavailable: tuple[str, ...]
    caps_applied: tuple[str, ...]
    notes: tuple[str, ...]
    #: Families silent because their cadence is too slow for this horizon.
    #: Reported apart from ``unavailable`` because they are not a data problem.
    horizon_excluded: tuple[str, ...] = ()
    #: H3: member-level coverage of the evidence expected at this horizon.
    #: None only for a caller that assembled a set without running coverage.
    coverage: EvidenceCoverage | None = None
    #: H3: canonical universal macro families with no data in this project.
    dormant_canonical: tuple[str, ...] = ()
    #: H3: the ceiling those dormant families imposed, if any.
    architectural_cap: ConfidenceLevel | None = None
    #: H3: which data-QUALITY inputs the caller actually supplied. Recorded
    #: because the staleness and source-conflict caps can only fire when a
    #: caller passes them, and the live capture path currently passes neither.
    #: Without this a reader would take an empty ``caps_applied`` as evidence
    #: the series were checked and found healthy, when nothing was checked.
    #: Wiring those inputs is out of H3 scope; making the gap legible is not.
    quality_inputs: Mapping[str, object] | None = None

    @property
    def has_disagreement(self) -> bool:
        return bool(self.disagreements)

    def as_record(self) -> dict[str, object]:
        return {
            "macro_confidence": self.macro.name,
            "technical_confidence": self.technical.name,
            "execution_confidence": self.execution.name,
            "regime_confidence": self.regime.name,
            "data_confidence": self.data.name,
            "disagreements": list(self.disagreements),
            "unavailable": list(self.unavailable),
            "horizon_excluded": list(self.horizon_excluded),
            "caps_applied": list(self.caps_applied),
            "notes": list(self.notes),
            # H3: everything needed to explain this record's Data Confidence
            # without consulting repository history. See EvidenceCoverage.
            "data_confidence_basis": {
                "coverage": self.coverage.as_record() if self.coverage else None,
                "dormant_canonical_macro": list(self.dormant_canonical),
                "architectural_cap": (
                    self.architectural_cap.name if self.architectural_cap else None
                ),
                "quality_inputs": (
                    dict(self.quality_inputs) if self.quality_inputs else None
                ),
            },
        }


def _block_confidence(
    readings: tuple[FamilyReading, ...],
    keys: frozenset[str],
    candidate: Direction,
) -> tuple[ConfidenceLevel, tuple[str, ...], tuple[str, ...]]:
    """Confidence for one block, from counts of agreeing independent families."""
    block = tuple(r for r in readings if r.family_key in keys)
    supporting = tuple(
        r.family_key for r in block if r.state_against(candidate) is FamilyState.SUPPORTS
    )
    conflicting = tuple(
        r.family_key for r in block if r.state_against(candidate) is FamilyState.CONFLICTS
    )
    # Horizon-excluded families are not counted as unavailable: they are not a
    # data deficiency and must not cap this block's confidence as though the
    # system had failed to read something it needed.
    unavailable = tuple(
        r.family_key for r in block if not r.is_available and not r.is_horizon_excluded
    )

    level = level_from_agreeing_count(len(supporting))
    if conflicting:
        # Disagreement caps rather than nets out, and is reported separately.
        level = level.capped_at(ConfidenceLevel.MODERATE)
    if unavailable:
        level = level.capped_at(ConfidenceLevel.MODERATE)
    return level, conflicting, unavailable


def assemble_confidence(
    *,
    readings: tuple[FamilyReading, ...],
    candidate: Direction,
    macro_keys: frozenset[str],
    technical_keys: frozenset[str],
    critical_family_keys: frozenset[str],
    execution_confidence: ConfidenceLevel = ConfidenceLevel.LOW,
    regime_confidence: ConfidenceLevel = ConfidenceLevel.LOW,
    gates: tuple[GateOutcome, ...] = (),
    staleness_observations: tuple[Staleness, ...] = (),
    conflicting_sources: bool = False,
    decision_horizon: Horizon | None = None,
    definitions: Mapping[str, FamilyDefinition] = FAMILIES_BY_KEY,
) -> ConfidenceSet:
    """Build all five dimensions, keeping every one of them separate.

    ``decision_horizon`` reaches H3 evidence coverage, which must be measured
    against the evidence eligible at THIS horizon. Passing None applies no
    horizon filter, which is what a caller reasoning about confidence in the
    abstract wants; the live path always supplies it.
    """
    notes: list[str] = []
    caps: list[str] = []

    macro, macro_conflicts, macro_unavailable = _block_confidence(
        readings, macro_keys, candidate
    )
    technical, technical_conflicts, technical_unavailable = _block_confidence(
        readings, technical_keys, candidate
    )

    if len(technical_keys) < 3:
        notes.append(
            f"Technical confidence cannot reach HIGH: only {len(technical_keys)} "
            "independent technical families exist, so three cannot agree."
        )

    # --- Data confidence -------------------------------------------------
    horizon_excluded = tuple(r.family_key for r in readings if r.is_horizon_excluded)
    unavailable = tuple(
        r.family_key for r in readings
        if not r.is_available and not r.is_horizon_excluded
    )
    critical_missing = tuple(k for k in unavailable if k in critical_family_keys)
    if horizon_excluded:
        notes.append(
            "Horizon-excluded families are reported apart from unavailable ones "
            "and do not reduce Data Confidence: "
            + ", ".join(sorted(horizon_excluded))
            + ". Their data arrived and is usable; it is too slow to be evidence "
            "at this horizon."
        )

    if critical_missing or len(unavailable) >= 2:
        data = ConfidenceLevel.LOW
    elif unavailable:
        data = ConfidenceLevel.MODERATE
    else:
        data = ConfidenceLevel.HIGH

    if critical_missing:
        notes.append(
            "Critical families unavailable: " + ", ".join(sorted(critical_missing))
        )

    if any(s is Staleness.BROKEN for s in staleness_observations):
        data = data.capped_at(ConfidenceLevel.LOW)
        caps.append("data:broken_series")
    elif any(s in (Staleness.STALE, Staleness.UNKNOWN) for s in staleness_observations):
        data = data.capped_at(ConfidenceLevel.MODERATE)
        caps.append("data:stale_series")

    if conflicting_sources:
        data = data.capped_at(ConfidenceLevel.MODERATE)
        caps.append("data:conflicting_sources")
        notes.append(
            "Sources disagree on the same quantity; flagged for review rather "
            "than silently resolved in favour of one."
        )

    # --- H3: member-level coverage ---------------------------------------
    # The family-level rule above asks whether each family SPOKE. A family
    # speaks on one surviving member, so that rule cannot see a family that
    # answered with a quarter of its evidence. Coverage measures the members.
    #
    # Applied with capped_at, so it can only lower. Floored at MODERATE inside
    # evidence_coverage, so it can never reach LOW (invariant I-1) and no
    # downstream consumer of "data is LOW" changes behaviour because of it.
    coverage = evidence_coverage(
        readings=readings,
        decision_horizon=decision_horizon,
        definitions=definitions,
    )
    if coverage.level is not ConfidenceLevel.HIGH:
        data = data.capped_at(coverage.level)
        caps.append("data:coverage_incomplete")
        if coverage.missing:
            notes.append(
                f"Expected evidence incomplete: {coverage.present_count} of "
                f"{coverage.eligible_count} expected members present "
                f"({', '.join(sorted(coverage.missing))} absent). Horizon-excluded, "
                "optional and unobtainable members are not counted as missing."
            )
        elif not coverage.defined:
            notes.append(
                "No evidence was expected at this horizon, so coverage is "
                "undefined. That is not the same as complete coverage and is "
                "deliberately not treated as such."
            )

    # --- H3: architectural completeness -----------------------------------
    # Coverage can only measure evidence the architecture declared. While a
    # canonical universal macro family has no data source at all, the macro
    # evidence base is structurally incomplete and HIGH is not a claim this
    # system is entitled to make, however well the declared families performed.
    dormant_canonical = dormant_canonical_macro_families()
    architectural_cap = architectural_completeness_cap(dormant_canonical)
    if architectural_cap is not None:
        data = data.capped_at(architectural_cap)
        caps.append("data:architectural_dormant_canonical")
        notes.append(
            "Canonical universal macro families are dormant in this project ("
            + ", ".join(dormant_canonical)
            + "), so the macro evidence base is structurally incomplete and Data "
            "Confidence is capped at "
            + architectural_cap.name
            + ". This is a statement about the evidence set, not about the "
            "families that did report, and it lifts automatically if a data "
            "source for one of them is activated."
        )

    quality_inputs = {
        "staleness_observations_supplied": len(staleness_observations),
        "conflicting_sources_supplied": bool(conflicting_sources),
    }

    # --- Gate ceilings ----------------------------------------------------
    ceiling = combined_confidence_ceiling(gates)
    if ceiling is not None:
        for gate in gates:
            if gate.max_confidence is not None:
                caps.append(f"gate:{gate.gate}")
        macro = macro.capped_at(ceiling)
        technical = technical.capped_at(ceiling)
        execution_confidence = execution_confidence.capped_at(ceiling)

    # Low data or regime confidence caps conviction. It never reverses a read,
    # and it never reweights the factors -- it raises the bar instead.
    conviction_ceiling: ConfidenceLevel | None = None
    if data is ConfidenceLevel.LOW:
        conviction_ceiling = ConfidenceLevel.LOW
        caps.append("data_confidence_low")
    elif regime_confidence is ConfidenceLevel.LOW:
        conviction_ceiling = ConfidenceLevel.MODERATE
        caps.append("regime_confidence_low")
        notes.append(
            "Regime confidence is Low: stronger confirmation is required, and the "
            "factors are deliberately NOT reweighted in response."
        )
    if conviction_ceiling is not None:
        macro = macro.capped_at(conviction_ceiling)
        technical = technical.capped_at(conviction_ceiling)

    disagreements = tuple(dict.fromkeys(macro_conflicts + technical_conflicts))
    if disagreements:
        notes.append(
            "Families in conflict with the candidate read: "
            + ", ".join(sorted(disagreements))
            + ". Reported alongside confidence, not absorbed into it."
        )

    return ConfidenceSet(
        macro=macro,
        technical=technical,
        execution=execution_confidence,
        regime=regime_confidence,
        data=data,
        disagreements=disagreements,
        unavailable=tuple(dict.fromkeys(unavailable)),
        horizon_excluded=tuple(dict.fromkeys(horizon_excluded)),
        caps_applied=tuple(dict.fromkeys(caps)),
        notes=tuple(notes),
        coverage=coverage,
        dormant_canonical=dormant_canonical,
        architectural_cap=architectural_cap,
        quality_inputs=quality_inputs,
    )
