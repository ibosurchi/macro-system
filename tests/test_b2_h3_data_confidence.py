"""H3 -- member-level Data Confidence coverage.

The defect H3 closes:

    Data Confidence reported HIGH with five of fifteen declared member signals
    present, because the only availability test was ``FamilyReading.is_available``
    and a family reports available when a SINGLE member survives.

The suite is organised around the four safety invariants, because those are what
make H3 safe to land in a frozen architecture:

    I-1  H3 introduces NO new path to data == LOW.
    I-2  H3 is monotone downward.
    I-3  Coverage is direction-blind.
    I-4  Nothing else about an evaluation changes.

Every test is deterministic: fixed timestamps, no clock, no I/O, no randomness
outside the null benchmark's own seeded generator.
"""
from __future__ import annotations

import copy
import json
import math
import unittest
from dataclasses import replace
from datetime import datetime, timezone

from apex.b2 import confidence as confidence_mod
from apex.b2 import families, registry
from apex.b2.confidence import (
    ARCHITECTURAL_CAP_LEVEL,
    COVERAGE_FLOOR,
    architectural_completeness_cap,
    assemble_confidence,
    evidence_coverage,
    level_from_coverage_ratio,
)
from apex.b2.enums import ConfidenceLevel, DecisionState, Direction, Horizon
from apex.b2.evaluate import run_shadow_evaluation
from apex.b2.evaluation.null_benchmark import run_null_benchmark
from apex.b2.horizons import SeriesFrequency, Staleness
from apex.b2.registry import (
    CANONICAL_MACRO_FAMILY_KEYS,
    EvidenceExpectation,
    FamilyDefinition,
    MemberScale,
    MemberSpec,
    Role,
)
from apex.b2.shadow import (
    CURRENT_SCHEMA_VERSION,
    FREEZE_SCHEMA_VERSION,
    evidence_epoch,
)

NOW = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)

#: Every declared member of the live voting core, all present.
ALL_PRESENT: dict[str, dict[str, float | None]] = {
    "policy_real_rates": {
        "policy_rate_momentum": 0.5,
        "real_yield_momentum": 0.5,
        "nominal_yield_momentum": 0.5,
        "inflation_expectations_momentum": 0.5,
    },
    "macro_activity": {
        "inflation_momentum": 0.5,
        "labor_momentum": 0.5,
        "growth_momentum": 0.5,
    },
    "news_geopolitical": {"rule_based_news": 0.5, "ai_news": 0.5},
    "directional": {
        "short_horizon_return": 1.0,
        "medium_horizon_return": 1.0,
        "multi_timeframe_alignment": 1.0,
    },
    "structure": {
        "breakout_quality": 1.0,
        "price_structure_zone": None,
        "retest_behaviour": None,
    },
}

#: The reproduction case from the audit: one surviving member per family.
ONE_PER_FAMILY: dict[str, dict[str, float | None]] = {
    "policy_real_rates": {"policy_rate_momentum": 0.5},
    "macro_activity": {"inflation_momentum": 0.5},
    "news_geopolitical": {"rule_based_news": 0.5},
    "directional": {"short_horizon_return": 1.0},
    "structure": {"breakout_quality": 1.0},
}

#: Eligible expected-member counts per horizon, derived by hand from the
#: registry declarations and asserted so a silent registry drift is caught.
#:
#:   STRUCTURAL 12 = 15 declared - 2 unobtainable - 1 optional
#:   TACTICAL   11 = 12 - growth_momentum (quarterly)
#:   EXECUTION   8 = 12 - policy_rate_momentum and all 3 macro_activity members
EXPECTED_ELIGIBLE = {
    Horizon.STRUCTURAL: 12,
    Horizon.TACTICAL: 11,
    Horizon.EXECUTION: 8,
}


def _signals(**overrides: dict[str, float | None]) -> dict[str, dict[str, float | None]]:
    merged = copy.deepcopy(ALL_PRESENT)
    merged.update(copy.deepcopy(overrides))
    return merged


def _readings(signals=None, horizon: Horizon | None = Horizon.TACTICAL):
    return families.evaluate_families(
        registry.VOTING_FAMILIES, signals if signals is not None else ALL_PRESENT, horizon
    )


def _coverage(signals=None, horizon: Horizon | None = Horizon.TACTICAL):
    return evidence_coverage(
        readings=_readings(signals, horizon), decision_horizon=horizon
    )


def _assemble(signals=None, horizon: Horizon | None = Horizon.TACTICAL, **kwargs):
    return assemble_confidence(
        readings=_readings(signals, horizon),
        candidate=kwargs.pop("candidate", Direction.BULLISH),
        macro_keys=registry.MACRO_FAMILY_KEYS,
        technical_keys=registry.TECHNICAL_FAMILY_KEYS,
        critical_family_keys=registry.CRITICAL_FAMILY_KEYS,
        decision_horizon=horizon,
        **kwargs,
    )


def _evaluate(signals=None, horizon: Horizon = Horizon.TACTICAL, **kwargs):
    return run_shadow_evaluation(
        instrument="Gold",
        decision_horizon=horizon,
        signals_by_family=signals if signals is not None else ALL_PRESENT,
        evaluated_at=NOW,
        **kwargs,
    )


# ===========================================================================
# A. Coverage arithmetic
# ===========================================================================
class TestCoverageArithmetic(unittest.TestCase):
    def test_full_evidence_yields_ratio_one(self):
        for horizon in Horizon:
            with self.subTest(horizon=horizon):
                self.assertEqual(_coverage(horizon=horizon).ratio, 1.0)

    def test_eligible_counts_per_horizon_are_exactly_as_declared(self):
        for horizon, count in EXPECTED_ELIGIBLE.items():
            with self.subTest(horizon=horizon):
                self.assertEqual(_coverage(horizon=horizon).eligible_count, count)

    def test_expected_is_exactly_present_plus_missing_and_they_are_disjoint(self):
        cov = _coverage(ONE_PER_FAMILY)
        self.assertEqual(cov.present_count + len(cov.missing), cov.eligible_count)
        self.assertTrue(set(cov.missing) <= set(cov.expected))
        self.assertEqual(len(set(cov.expected)), len(cov.expected))

    def test_the_five_of_fifteen_reproduction_case_is_no_longer_complete(self):
        """The exact defect H3 exists to close."""
        cov = _coverage(ONE_PER_FAMILY)
        self.assertLess(cov.ratio, 0.5)
        self.assertFalse(cov.is_complete)
        self.assertIsNot(cov.level, ConfidenceLevel.HIGH)

    def test_partial_evidence_sits_between_full_and_minimal(self):
        full = _coverage(ALL_PRESENT).ratio
        partial = _coverage(
            _signals(directional={"short_horizon_return": 1.0})
        ).ratio
        minimal = _coverage(ONE_PER_FAMILY).ratio
        self.assertGreater(full, partial)
        self.assertGreater(partial, minimal)

    def test_a_zero_denominator_is_undefined_not_complete(self):
        """An empty denominator must never read as 1.0."""
        empty = FamilyDefinition(
            key="optional_only",
            label="Optional Only",
            role=Role.ACTIVE_VOTING,
            horizon=Horizon.TACTICAL,
            members=("only_member",),
            member_specs=(
                MemberSpec(
                    key="only_member",
                    scale=MemberScale.BOUNDED_UNIT,
                    frequency=SeriesFrequency.DAILY,
                    expectation=EvidenceExpectation.OPTIONAL,
                ),
            ),
            justification="test fixture",
            data_sources=("test",),
        )
        reading = families.evaluate_family(empty, {"only_member": 0.5})
        cov = evidence_coverage(
            readings=(reading,),
            decision_horizon=Horizon.TACTICAL,
            definitions={"optional_only": empty},
        )
        self.assertIsNone(cov.ratio)
        self.assertFalse(cov.defined)
        self.assertFalse(cov.is_complete)
        # Conservative before the floor, floored afterwards.
        self.assertIs(cov.level_before_floor, ConfidenceLevel.LOW)
        self.assertIs(cov.level, COVERAGE_FLOOR)

    def test_ratio_ladder_is_deterministic(self):
        self.assertIs(level_from_coverage_ratio(1.0), ConfidenceLevel.HIGH)
        self.assertIs(level_from_coverage_ratio(0.80), ConfidenceLevel.HIGH)
        self.assertIs(level_from_coverage_ratio(0.79), ConfidenceLevel.MODERATE)
        self.assertIs(level_from_coverage_ratio(0.50), ConfidenceLevel.MODERATE)
        self.assertIs(level_from_coverage_ratio(0.49), ConfidenceLevel.LOW)
        self.assertIs(level_from_coverage_ratio(0.0), ConfidenceLevel.LOW)
        self.assertIs(level_from_coverage_ratio(None), ConfidenceLevel.LOW)

    def test_no_evidence_at_all_gives_ratio_zero(self):
        nothing = {k: {} for k in ALL_PRESENT}
        cov = _coverage(nothing)
        self.assertEqual(cov.ratio, 0.0)
        self.assertEqual(cov.present_count, 0)

    def test_an_unclassified_family_is_named_rather_than_dropped(self):
        cov = evidence_coverage(readings=_readings(), definitions={})
        self.assertEqual(len(cov.unclassified), len(registry.VOTING_FAMILIES))
        self.assertIsNone(cov.ratio)


# ===========================================================================
# B. Expectation classes
# ===========================================================================
class TestExpectationClasses(unittest.TestCase):
    def test_optional_member_absent_does_not_reduce_coverage(self):
        """Operator decision: turning off the AI switch is not a data gap."""
        with_ai = _coverage(ALL_PRESENT)
        without_ai = _coverage(_signals(news_geopolitical={
            "rule_based_news": 0.5, "ai_news": None
        }))
        self.assertEqual(with_ai.ratio, without_ai.ratio)
        self.assertEqual(without_ai.eligible_count, with_ai.eligible_count)
        self.assertIn("news_geopolitical.ai_news", without_ai.excluded_optional)

    def test_optional_member_is_never_in_the_denominator(self):
        for horizon in Horizon:
            with self.subTest(horizon=horizon):
                cov = _coverage(horizon=horizon)
                self.assertNotIn("news_geopolitical.ai_news", cov.expected)

    def test_unobtainable_members_are_excluded_from_the_denominator(self):
        cov = _coverage()
        for member in ("structure.price_structure_zone", "structure.retest_behaviour"):
            self.assertIn(member, cov.excluded_unobtainable)
            self.assertNotIn(member, cov.expected)
            self.assertNotIn(member, cov.missing)

    def test_unobtainable_absence_never_degrades_a_healthy_evaluation(self):
        """Otherwise every record ever written carries a permanent penalty."""
        self.assertEqual(_coverage(ALL_PRESENT).ratio, 1.0)

    def test_required_and_expected_both_count_in_the_denominator(self):
        definition = FamilyDefinition(
            key="mixed",
            label="Mixed",
            role=Role.ACTIVE_VOTING,
            horizon=Horizon.TACTICAL,
            members=("req", "exp", "opt", "unobt"),
            member_specs=(
                MemberSpec(key="req", scale=MemberScale.BOUNDED_UNIT,
                           frequency=SeriesFrequency.DAILY,
                           expectation=EvidenceExpectation.REQUIRED),
                MemberSpec(key="exp", scale=MemberScale.BOUNDED_UNIT,
                           frequency=SeriesFrequency.DAILY,
                           expectation=EvidenceExpectation.EXPECTED),
                MemberSpec(key="opt", scale=MemberScale.BOUNDED_UNIT,
                           frequency=SeriesFrequency.DAILY,
                           expectation=EvidenceExpectation.OPTIONAL),
                MemberSpec(key="unobt", scale=MemberScale.BOUNDED_UNIT,
                           frequency=SeriesFrequency.DAILY,
                           frequency_basis="test fixture reason",
                           expectation=EvidenceExpectation.UNOBTAINABLE),
            ),
            justification="test fixture",
            data_sources=("test",),
        )
        reading = families.evaluate_family(
            definition, {"req": 0.5, "exp": 0.5, "opt": None, "unobt": None}
        )
        cov = evidence_coverage(
            readings=(reading,), decision_horizon=Horizon.TACTICAL,
            definitions={"mixed": definition},
        )
        self.assertEqual(cov.eligible_count, 2)
        self.assertEqual(cov.ratio, 1.0)

    def test_a_missing_required_member_degrades_coverage_but_never_reaches_low(self):
        """I-1 at member granularity: REQUIRED is not a back door to LOW."""
        definition = FamilyDefinition(
            key="required_only",
            label="Required Only",
            role=Role.ACTIVE_VOTING,
            horizon=Horizon.TACTICAL,
            members=("a", "b"),
            member_specs=(
                MemberSpec(key="a", scale=MemberScale.BOUNDED_UNIT,
                           frequency=SeriesFrequency.DAILY,
                           expectation=EvidenceExpectation.REQUIRED),
                MemberSpec(key="b", scale=MemberScale.BOUNDED_UNIT,
                           frequency=SeriesFrequency.DAILY,
                           expectation=EvidenceExpectation.REQUIRED),
            ),
            justification="test fixture",
            data_sources=("test",),
        )
        reading = families.evaluate_family(definition, {"a": None, "b": None})
        cov = evidence_coverage(
            readings=(reading,), decision_horizon=Horizon.TACTICAL,
            definitions={"required_only": definition},
        )
        self.assertEqual(cov.ratio, 0.0)
        self.assertIs(cov.level_before_floor, ConfidenceLevel.LOW)
        self.assertIs(cov.level, ConfidenceLevel.MODERATE)

    def test_every_production_member_declares_an_expectation(self):
        for family in registry.VOTING_FAMILIES:
            for spec in family.member_specs:
                with self.subTest(member=spec.key):
                    self.assertIsInstance(spec.expectation, EvidenceExpectation)

    def test_operator_decided_assignments_are_in_force(self):
        by_key = {
            spec.key: spec.expectation
            for family in registry.VOTING_FAMILIES
            for spec in family.member_specs
        }
        self.assertIs(by_key["ai_news"], EvidenceExpectation.OPTIONAL)
        self.assertIs(by_key["price_structure_zone"], EvidenceExpectation.UNOBTAINABLE)
        self.assertIs(by_key["retest_behaviour"], EvidenceExpectation.UNOBTAINABLE)
        self.assertIs(by_key["growth_momentum"], EvidenceExpectation.EXPECTED)

    def test_member_spec_default_keeps_pre_h3_construction_valid(self):
        spec = MemberSpec(
            key="legacy",
            scale=MemberScale.BOUNDED_UNIT,
            frequency=SeriesFrequency.DAILY,
        )
        self.assertIs(spec.expectation, EvidenceExpectation.EXPECTED)

    def test_dataclasses_replace_still_works_for_the_null_benchmark(self):
        spec = registry.DIRECTIONAL.member_specs[0]
        rebanded = replace(spec, flat_threshold=0.9)
        self.assertEqual(rebanded.threshold, 0.9)
        self.assertIs(rebanded.expectation, spec.expectation)


# ===========================================================================
# C. Value semantics -- zero is evidence, direction is invisible
# ===========================================================================
class TestValueSemantics(unittest.TestCase):
    def test_zero_valued_evidence_is_present_not_missing(self):
        zeros = {
            family.key: {member: 0.0 for member in family.members}
            for family in registry.VOTING_FAMILIES
        }
        cov = _coverage(zeros)
        self.assertEqual(cov.ratio, 1.0)
        self.assertEqual(cov.missing, ())

    def test_zero_and_none_are_not_interchangeable(self):
        zero = _coverage(_signals(directional={
            "short_horizon_return": 0.0, "medium_horizon_return": 0.0,
            "multi_timeframe_alignment": 0.0,
        }))
        none = _coverage(_signals(directional={
            "short_horizon_return": None, "medium_horizon_return": None,
            "multi_timeframe_alignment": None,
        }))
        self.assertEqual(zero.ratio, 1.0)
        self.assertLess(none.ratio, zero.ratio)

    def test_genuinely_neutral_evidence_keeps_full_coverage(self):
        """Flat is a measurement. It must not read as an evidence gap."""
        flat = _evaluate({
            family.key: {member: 0.0 for member in family.members}
            for family in registry.VOTING_FAMILIES
        })
        self.assertEqual(flat.confidence.coverage.ratio, 1.0)

    def test_nan_and_infinity_count_as_missing_never_as_present(self):
        for bad in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=bad):
                cov = _coverage(_signals(directional={
                    "short_horizon_return": bad,
                    "medium_horizon_return": 1.0,
                    "multi_timeframe_alignment": 1.0,
                }))
                self.assertIn("directional.short_horizon_return", cov.missing)

    def test_i3_coverage_is_direction_blind(self):
        """I-3: flipping every sign must not move coverage by one bit."""
        positive = _signals()
        negative = {
            fam: {m: (None if v is None else -v) for m, v in members.items()}
            for fam, members in positive.items()
        }
        a, b = _coverage(positive), _coverage(negative)
        self.assertEqual(a.as_record(), b.as_record())

    def test_i3_holds_for_partial_evidence_too(self):
        partial = _signals(policy_real_rates={
            "policy_rate_momentum": 0.5, "real_yield_momentum": None,
            "nominal_yield_momentum": 0.5, "inflation_expectations_momentum": None,
        })
        flipped = {
            fam: {m: (None if v is None else -v) for m, v in members.items()}
            for fam, members in partial.items()
        }
        self.assertEqual(_coverage(partial).as_record(), _coverage(flipped).as_record())

    def test_conflicting_directional_evidence_still_has_full_coverage(self):
        """Conflict is a direction fact, never a coverage fact."""
        conflicted = _signals(
            policy_real_rates={
                "policy_rate_momentum": 0.5, "real_yield_momentum": -0.5,
                "nominal_yield_momentum": 0.5, "inflation_expectations_momentum": -0.5,
            },
            news_geopolitical={"rule_based_news": -0.9, "ai_news": -0.9},
        )
        result = _assemble(conflicted)
        self.assertEqual(result.coverage.ratio, 1.0)
        self.assertTrue(result.has_disagreement)


# ===========================================================================
# D. Horizon safety -- H1 must not regress
# ===========================================================================
class TestHorizonSafety(unittest.TestCase):
    def test_horizon_excluded_members_are_in_neither_side_of_the_ratio(self):
        cov = _coverage(horizon=Horizon.TACTICAL)
        self.assertIn("macro_activity.growth_momentum", cov.excluded_horizon)
        self.assertNotIn("macro_activity.growth_momentum", cov.expected)
        self.assertNotIn("macro_activity.growth_momentum", cov.missing)

    def test_excluded_horizon_and_missing_are_always_disjoint(self):
        for horizon in Horizon:
            for signals in (ALL_PRESENT, ONE_PER_FAMILY):
                with self.subTest(horizon=horizon):
                    cov = _coverage(signals, horizon)
                    self.assertFalse(set(cov.excluded_horizon) & set(cov.missing))
                    self.assertFalse(set(cov.excluded_horizon) & set(cov.expected))

    def test_one_snapshot_gives_three_different_denominators(self):
        ratios = {h: _coverage(horizon=h).eligible_count for h in Horizon}
        self.assertEqual(len(set(ratios.values())), 3)

    def test_execution_horizon_excludes_the_whole_macro_activity_family(self):
        cov = _coverage(horizon=Horizon.EXECUTION)
        for member in registry.MACRO_ACTIVITY.members:
            self.assertIn(f"macro_activity.{member}", cov.excluded_horizon)

    def test_execution_records_do_not_claim_a_data_outage(self):
        """The H1 guarantee: a structural exclusion is not a broken feed."""
        result = _evaluate(horizon=Horizon.EXECUTION)
        self.assertIsNot(
            result.decision.state, DecisionState.INSUFFICIENT_DATA_SYSTEM_DEGRADED
        )
        self.assertEqual(result.confidence.coverage.ratio, 1.0)
        self.assertIn("macro_activity", result.confidence.horizon_excluded)

    def test_a_slow_member_failing_is_invisible_at_a_faster_horizon(self):
        broken_growth = _signals(macro_activity={
            "inflation_momentum": 0.5, "labor_momentum": 0.5, "growth_momentum": None,
        })
        structural = _coverage(broken_growth, Horizon.STRUCTURAL)
        execution = _coverage(broken_growth, Horizon.EXECUTION)
        self.assertIn("macro_activity.growth_momentum", structural.missing)
        self.assertNotIn("macro_activity.growth_momentum", execution.missing)
        self.assertEqual(execution.ratio, 1.0)

    def test_no_horizon_filter_applies_when_none_is_supplied(self):
        cov = _coverage(horizon=None)
        self.assertEqual(cov.excluded_horizon, ())
        self.assertEqual(cov.eligible_count, EXPECTED_ELIGIBLE[Horizon.STRUCTURAL])


# ===========================================================================
# E. The architectural completeness cap
# ===========================================================================
class TestArchitecturalCap(unittest.TestCase):
    def test_the_canonical_macro_set_is_exactly_the_operator_decision(self):
        self.assertEqual(
            CANONICAL_MACRO_FAMILY_KEYS,
            frozenset({"policy_real_rates", "liquidity_funding",
                       "positioning_crowding", "fiscal_issuance"}),
        )

    def test_three_canonical_families_are_currently_dormant(self):
        dormant = registry.dormant_canonical_macro_families()
        self.assertEqual(
            dormant, ("fiscal_issuance", "liquidity_funding", "positioning_crowding")
        )

    def test_the_cap_is_derived_from_registry_state_not_hard_coded(self):
        self.assertIs(architectural_completeness_cap(("liquidity_funding",)),
                      ARCHITECTURAL_CAP_LEVEL)
        self.assertIsNone(architectural_completeness_cap(()))

    def test_the_cap_lifts_when_the_canonical_set_is_complete(self):
        """Proves the mechanism unblocks itself; nothing must be remembered."""
        self.assertIsNone(architectural_completeness_cap(()))
        self.assertIs(ARCHITECTURAL_CAP_LEVEL, ConfidenceLevel.MODERATE)

    def test_the_cap_is_moderate_and_never_low(self):
        self.assertIs(ARCHITECTURAL_CAP_LEVEL, ConfidenceLevel.MODERATE)
        self.assertIsNot(ARCHITECTURAL_CAP_LEVEL, ConfidenceLevel.LOW)

    def test_high_is_unreachable_while_canonical_families_are_dormant(self):
        result = _assemble(ALL_PRESENT)
        self.assertEqual(result.coverage.ratio, 1.0)
        self.assertIsNot(result.data, ConfidenceLevel.HIGH)
        self.assertIs(result.data, ConfidenceLevel.MODERATE)
        self.assertIn("data:architectural_dormant_canonical", result.caps_applied)

    def test_the_cap_is_recorded_with_the_families_that_caused_it(self):
        result = _assemble(ALL_PRESENT)
        self.assertIs(result.architectural_cap, ConfidenceLevel.MODERATE)
        self.assertIn("liquidity_funding", result.dormant_canonical)
        self.assertIn("positioning_crowding", result.dormant_canonical)

    def test_dormant_canonical_families_never_contribute_direction(self):
        """A dormant family must not fabricate evidence of any kind."""
        result = _evaluate(ALL_PRESENT)
        family_keys = {r.family_key for r in result.readings}
        for key in registry.dormant_canonical_macro_families():
            self.assertNotIn(key, family_keys)


# ===========================================================================
# F. Invariants I-1 and I-2
# ===========================================================================
def _pre_h3_data_level(readings, staleness=(), conflicting=False) -> ConfidenceLevel:
    """The exact pre-H3 Data Confidence rule, replicated for comparison."""
    horizon_excluded = {r.family_key for r in readings if r.is_horizon_excluded}
    unavailable = [
        r.family_key for r in readings
        if not r.is_available and r.family_key not in horizon_excluded
    ]
    critical_missing = [
        k for k in unavailable if k in registry.CRITICAL_FAMILY_KEYS
    ]
    if critical_missing or len(unavailable) >= 2:
        data = ConfidenceLevel.LOW
    elif unavailable:
        data = ConfidenceLevel.MODERATE
    else:
        data = ConfidenceLevel.HIGH
    if any(s is Staleness.BROKEN for s in staleness):
        data = data.capped_at(ConfidenceLevel.LOW)
    elif any(s in (Staleness.STALE, Staleness.UNKNOWN) for s in staleness):
        data = data.capped_at(ConfidenceLevel.MODERATE)
    if conflicting:
        data = data.capped_at(ConfidenceLevel.MODERATE)
    return data


def _scenario_space():
    """A broad, deterministic sweep of availability shapes."""
    yield "all_present", ALL_PRESENT
    yield "one_per_family", ONE_PER_FAMILY
    yield "nothing", {k: {} for k in ALL_PRESENT}
    yield "all_zero", {
        f.key: {m: 0.0 for m in f.members} for f in registry.VOTING_FAMILIES
    }
    yield "no_news", _signals(news_geopolitical={"rule_based_news": None, "ai_news": None})
    yield "no_ai", _signals(news_geopolitical={"rule_based_news": 0.5, "ai_news": None})
    yield "no_policy", _signals(policy_real_rates={})
    yield "no_activity", _signals(macro_activity={})
    yield "no_directional", _signals(directional={})
    yield "no_structure", _signals(structure={})
    yield "half_policy", _signals(policy_real_rates={
        "policy_rate_momentum": 0.5, "real_yield_momentum": 0.5,
        "nominal_yield_momentum": None, "inflation_expectations_momentum": None,
    })
    yield "conflicting", _signals(news_geopolitical={
        "rule_based_news": -0.9, "ai_news": 0.9
    })


class TestInvariantOneNoNewLowPath(unittest.TestCase):
    def test_i1_data_is_low_only_when_the_pre_h3_rule_was_already_low(self):
        for name, signals in _scenario_space():
            for horizon in Horizon:
                with self.subTest(scenario=name, horizon=horizon):
                    readings = _readings(signals, horizon)
                    actual = _assemble(signals, horizon).data
                    if actual is ConfidenceLevel.LOW:
                        self.assertIs(
                            _pre_h3_data_level(readings), ConfidenceLevel.LOW,
                            f"{name}/{horizon.value}: H3 created a new LOW",
                        )

    def test_i1_coverage_level_never_returns_low(self):
        for name, signals in _scenario_space():
            for horizon in Horizon:
                with self.subTest(scenario=name, horizon=horizon):
                    self.assertIsNot(
                        _coverage(signals, horizon).level, ConfidenceLevel.LOW
                    )

    def test_i1_coverage_floor_is_moderate(self):
        self.assertIs(COVERAGE_FLOOR, ConfidenceLevel.MODERATE)

    def test_i1_holds_even_with_zero_coverage(self):
        nothing = {k: {} for k in ALL_PRESENT}
        cov = _coverage(nothing)
        self.assertEqual(cov.ratio, 0.0)
        self.assertIs(cov.level_before_floor, ConfidenceLevel.LOW)
        self.assertIs(cov.level, ConfidenceLevel.MODERATE)

    def test_existing_low_behaviour_is_preserved(self):
        """A critical family unavailable must still produce LOW."""
        result = _assemble(_signals(policy_real_rates={}))
        self.assertIs(result.data, ConfidenceLevel.LOW)

    def test_broken_series_still_caps_to_low(self):
        result = _assemble(ALL_PRESENT, staleness_observations=(Staleness.BROKEN,))
        self.assertIs(result.data, ConfidenceLevel.LOW)
        self.assertIn("data:broken_series", result.caps_applied)


class TestInvariantTwoMonotoneDownward(unittest.TestCase):
    def test_i2_h3_never_raises_data_confidence(self):
        for name, signals in _scenario_space():
            for horizon in Horizon:
                with self.subTest(scenario=name, horizon=horizon):
                    readings = _readings(signals, horizon)
                    before = _pre_h3_data_level(readings)
                    after = _assemble(signals, horizon).data
                    self.assertLessEqual(
                        after.value, before.value,
                        f"{name}/{horizon.value}: H3 raised confidence",
                    )


# ===========================================================================
# G. Invariant I-4 -- nothing else moves
# ===========================================================================
class TestInvariantFourNothingElseChanges(unittest.TestCase):
    """H3 must not touch direction, decision, the other four dimensions or size.

    Coverage is disabled by handing ``assemble_confidence`` an empty definitions
    mapping, which makes every reading unclassified and the ratio undefined. The
    architectural cap is unaffected by that, so the comparison isolates the
    coverage term while leaving everything else identical.
    """

    def _pair(self, signals, horizon):
        full = _evaluate(signals, horizon)
        return full

    def test_direction_is_unaffected_by_coverage(self):
        for name, signals in _scenario_space():
            for horizon in Horizon:
                with self.subTest(scenario=name, horizon=horizon):
                    result = self._pair(signals, horizon)
                    readings = _readings(signals, horizon)
                    from apex.b2.aggregation import resolve_direction
                    expected, _ = resolve_direction(
                        readings, registry.MACRO_FAMILY_KEYS,
                        registry.TECHNICAL_FAMILY_KEYS,
                    )
                    self.assertIs(result.direction, expected)

    def test_family_readings_are_untouched_by_h3(self):
        for name, signals in _scenario_space():
            with self.subTest(scenario=name):
                result = self._pair(signals, Horizon.TACTICAL)
                plain = _readings(signals, Horizon.TACTICAL)
                self.assertEqual(
                    [r.as_record() for r in result.readings],
                    [r.as_record() for r in plain],
                )

    def test_other_four_dimensions_do_not_depend_on_the_coverage_term(self):
        """Only ``data`` may differ when coverage is removed from the equation."""
        for name, signals in _scenario_space():
            for horizon in Horizon:
                with self.subTest(scenario=name, horizon=horizon):
                    readings = _readings(signals, horizon)
                    common = dict(
                        readings=readings,
                        candidate=Direction.BULLISH,
                        macro_keys=registry.MACRO_FAMILY_KEYS,
                        technical_keys=registry.TECHNICAL_FAMILY_KEYS,
                        critical_family_keys=registry.CRITICAL_FAMILY_KEYS,
                        decision_horizon=horizon,
                    )
                    withcov = assemble_confidence(**common)
                    nocov = assemble_confidence(**common, definitions={})
                    self.assertIs(withcov.macro, nocov.macro)
                    self.assertIs(withcov.technical, nocov.technical)
                    self.assertIs(withcov.execution, nocov.execution)
                    self.assertIs(withcov.regime, nocov.regime)

    def test_decision_state_never_changes_because_of_coverage(self):
        """Decision resolution does not receive Data Confidence at all."""
        import inspect
        from apex.b2 import decision as decision_mod
        signature = inspect.signature(decision_mod.resolve_decision)
        for forbidden in ("confidence", "data_confidence", "coverage"):
            self.assertNotIn(forbidden, signature.parameters)

    def test_size_directive_only_reacts_to_low_which_h3_cannot_create(self):
        for name, signals in _scenario_space():
            with self.subTest(scenario=name):
                result = self._pair(signals, Horizon.TACTICAL)
                if "low_data_confidence" in result.size.bar_reasons:
                    self.assertIs(result.confidence.data, ConfidenceLevel.LOW)
                    self.assertIs(
                        _pre_h3_data_level(_readings(signals, Horizon.TACTICAL)),
                        ConfidenceLevel.LOW,
                    )

    def test_gates_are_unchanged_by_h3(self):
        from apex.b2.gates import evaluate_gates
        for name, signals in _scenario_space():
            with self.subTest(scenario=name):
                result = self._pair(signals, Horizon.TACTICAL)
                expected = evaluate_gates(
                    readings=_readings(signals, Horizon.TACTICAL),
                    candidate=result.direction,
                    critical_family_keys=registry.CRITICAL_FAMILY_KEYS,
                )
                self.assertEqual(
                    [g.as_record() for g in result.gates],
                    [g.as_record() for g in expected],
                )


# ===========================================================================
# H. Provenance, serialization, re-scoring (H4)
# ===========================================================================
class TestProvenanceAndReScoring(unittest.TestCase):
    def test_the_basis_block_is_present_on_every_record(self):
        record = _evaluate().record.as_record()
        basis = record["confidence"]["data_confidence_basis"]
        self.assertIsNotNone(basis["coverage"])
        for field in ("expected", "missing", "excluded_horizon",
                      "excluded_unobtainable", "excluded_optional",
                      "coverage_ratio", "coverage_level",
                      "coverage_level_before_floor", "expectation_version"):
            self.assertIn(field, basis["coverage"], field)
        self.assertIn("dormant_canonical_macro", basis)
        self.assertIn("architectural_cap", basis)

    def test_the_record_is_json_serialisable(self):
        payload = _evaluate().record.as_record()
        json.loads(json.dumps(payload, default=str))

    def test_coverage_ratio_is_reconstructible_from_the_record_alone(self):
        """H4: no repository access required to explain the value."""
        basis = _evaluate(ONE_PER_FAMILY).record.as_record()[
            "confidence"]["data_confidence_basis"]["coverage"]
        expected = len(basis["expected"])
        missing = len(basis["missing"])
        self.assertAlmostEqual(
            basis["coverage_ratio"], (expected - missing) / expected
        )

    def test_member_spec_provenance_carries_every_expectation(self):
        provenance = registry.member_spec_provenance()
        self.assertEqual(provenance["expectation_version"],
                         registry.EXPECTATION_VERSION)
        for family in registry.VOTING_FAMILIES:
            for spec in provenance["families"][family.key]:
                self.assertIn("expectation", spec)

    def test_provenance_names_the_dormant_canonical_families(self):
        provenance = registry.member_spec_provenance()
        self.assertEqual(
            provenance["dormant_canonical_macro_families"],
            list(registry.dormant_canonical_macro_families()),
        )

    def test_quality_input_gap_is_auditable_rather_than_implied_healthy(self):
        """An empty caps list must not read as 'series checked and fine'."""
        basis = _evaluate().record.as_record()[
            "confidence"]["data_confidence_basis"]
        self.assertEqual(basis["quality_inputs"]["staleness_observations_supplied"], 0)
        self.assertIs(basis["quality_inputs"]["conflicting_sources_supplied"], False)

    def test_the_cap_reason_is_named_in_caps_applied(self):
        result = _assemble(ONE_PER_FAMILY)
        self.assertIn("data:coverage_incomplete", result.caps_applied)
        self.assertIn("data:architectural_dormant_canonical", result.caps_applied)

    def test_notes_explain_the_shortfall_in_words(self):
        result = _assemble(ONE_PER_FAMILY)
        joined = " ".join(result.notes)
        self.assertIn("Expected evidence incomplete", joined)


# ===========================================================================
# I. Schema and freeze boundary
# ===========================================================================
class TestSchemaAndFreezeBoundary(unittest.TestCase):
    def test_schema_version_is_four(self):
        self.assertEqual(CURRENT_SCHEMA_VERSION, 4)
        self.assertEqual(_evaluate().record.as_record()["schema_version"], 4)

    def test_the_freeze_boundary_did_not_move(self):
        self.assertEqual(FREEZE_SCHEMA_VERSION, 3)

    def test_v4_records_are_post_freeze(self):
        self.assertEqual(evidence_epoch(4), "post_freeze")
        self.assertEqual(_evaluate().record.as_record()["evidence_epoch"],
                         "post_freeze")

    def test_older_records_keep_their_epoch_exactly(self):
        self.assertEqual(evidence_epoch(1), "pre_freeze")
        self.assertEqual(evidence_epoch(2), "pre_freeze")
        self.assertEqual(evidence_epoch(3), "post_freeze")
        self.assertEqual(evidence_epoch(None), "pre_freeze")


# ===========================================================================
# J. Determinism
# ===========================================================================
class TestDeterminism(unittest.TestCase):
    def test_coverage_is_bit_identical_across_repeated_runs(self):
        first = _coverage(ONE_PER_FAMILY).as_record()
        for _ in range(50):
            self.assertEqual(_coverage(ONE_PER_FAMILY).as_record(), first)

    def test_repeated_evaluations_produce_an_identical_payload(self):
        first = json.dumps(_evaluate().record.as_record(), sort_keys=True, default=str)
        for _ in range(20):
            again = json.dumps(
                _evaluate().record.as_record(), sort_keys=True, default=str
            )
            self.assertEqual(again, first)

    def test_dormant_canonical_order_is_stable(self):
        self.assertEqual(
            registry.dormant_canonical_macro_families(),
            tuple(sorted(registry.dormant_canonical_macro_families())),
        )


# ===========================================================================
# K. Registry integrity guards added by H3
# ===========================================================================
class TestRegistryGuards(unittest.TestCase):
    def test_unobtainable_members_must_record_a_reason(self):
        for family in registry.VOTING_FAMILIES:
            for spec in family.member_specs:
                if spec.expectation is EvidenceExpectation.UNOBTAINABLE:
                    self.assertTrue(spec.frequency_basis.strip(), spec.key)

    def test_every_canonical_macro_family_is_accounted_for(self):
        known = (
            set(registry.voting_family_keys())
            | set(registry.dormant_keys())
            | set(registry.withheld_keys())
            | set(registry.active_non_voting_keys())
        )
        self.assertTrue(CANONICAL_MACRO_FAMILY_KEYS <= known)

    def test_budget_summary_still_serialises(self):
        summary = registry.describe_budget()
        json.dumps(summary)
        self.assertIn("canonical_macro", summary)
        self.assertIn("dormant_canonical_macro", summary)

    def test_voting_budget_and_membership_are_untouched_by_h3(self):
        self.assertEqual(len(registry.VOTING_FAMILIES), 5)
        self.assertEqual(
            registry.voting_family_keys(),
            ("policy_real_rates", "macro_activity", "news_geopolitical",
             "directional", "structure"),
        )


# ===========================================================================
# L. Null benchmark
# ===========================================================================
class TestNullBenchmarkUnaffected(unittest.TestCase):
    def test_null_benchmark_still_runs_and_is_reproducible(self):
        first = run_null_benchmark(samples=500).as_record()
        second = run_null_benchmark(samples=500).as_record()
        self.assertEqual(first, second)

    def test_null_benchmark_reports_a_noise_rate(self):
        result = run_null_benchmark(samples=1000)
        self.assertIsNotNone(result.as_record())


if __name__ == "__main__":
    unittest.main()
