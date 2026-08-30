"""Stage A tests for Architecture B2.

These tests deliberately import ONLY ``apex.b2``. That package imports nothing
from ``apex.production_core``, so running this module performs no network call
and writes no durable state -- which is itself asserted below, because the
existing suite was found to mutate a production state file at the repository
root during a test run.
"""
from __future__ import annotations

import ast
import inspect
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from apex import b2
from apex.b2 import (
    adapters,
    aggregation,
    confidence,
    decision,
    enums,
    evaluate,
    event_timing,
    execution,
    families,
    gates,
    horizons,
    modules,
    predictions,
    regime,
    registry,
    risk,
    scenarios,
    shadow,
    thesis,
)
from apex.b2.modules import base as modules_base
from apex.b2.modules import fx as modules_fx
from apex.b2.modules import gold as modules_gold
from apex.b2.modules import nasdaq as modules_nasdaq
from apex.b2.modules import oil as modules_oil

# Every module in the package. The purity constraints below apply to all of
# them, so a module added in a later stage is covered automatically once it is
# listed here.
ALL_B2_MODULES = (
    adapters,
    aggregation,
    confidence,
    decision,
    enums,
    evaluate,
    event_timing,
    execution,
    families,
    gates,
    horizons,
    modules,
    modules_base,
    modules_fx,
    modules_gold,
    modules_nasdaq,
    modules_oil,
    predictions,
    regime,
    registry,
    risk,
    scenarios,
    shadow,
    thesis,
)


def _b2_source() -> str:
    """Concatenated source of every module in the package, for structural checks."""
    return "\n".join(inspect.getsource(module) for module in ALL_B2_MODULES)


def _b2_imported_modules() -> set[str]:
    """Every module name any B2 module actually imports, via AST rather than text.

    A text search would be fooled by prose: the docstrings legitimately discuss
    ``apex.production_core`` while importing nothing from it.
    """
    imported: set[str] = set()
    for module in ALL_B2_MODULES:
        tree = ast.parse(inspect.getsource(module))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imported.add(alias.name)
            elif isinstance(node, ast.ImportFrom):
                # level > 0 is a relative import inside this package.
                imported.add(("." * node.level) + (node.module or ""))
    return imported


def _b2_executable_source() -> str:
    """Source with all docstrings stripped, so prose cannot mask a real call."""
    chunks: list[str] = []
    for module in ALL_B2_MODULES:
        tree = ast.parse(inspect.getsource(module))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                if (
                    node.body
                    and isinstance(node.body[0], ast.Expr)
                    and isinstance(node.body[0].value, ast.Constant)
                    and isinstance(node.body[0].value.value, str)
                ):
                    node.body = node.body[1:]
        chunks.append(ast.unparse(tree))
    return "\n".join(chunks)


def _uniform(definition, value):
    """Every member of a family set to the same signal value."""
    return {member: value for member in definition.members}


def _readings(policy=None, activity=None, news=None, directional=None, structure=None):
    """Build the five family readings from per-family signal values or dicts."""
    def signals(definition, spec):
        if spec is None:
            return {}
        if isinstance(spec, dict):
            return spec
        return _uniform(definition, spec)

    return families.evaluate_families(
        registry.VOTING_FAMILIES,
        {
            "policy_real_rates": signals(registry.POLICY_REAL_RATES, policy),
            "macro_activity": signals(registry.MACRO_ACTIVITY, activity),
            "news_geopolitical": signals(registry.NEWS_GEOPOLITICAL, news),
            "directional": signals(registry.DIRECTIONAL, directional),
            "structure": signals(registry.STRUCTURE, structure),
        },
    )


def _good_execution(**overrides):
    kwargs = dict(
        invalidation_level=95.0,
        entry_zone=(99.0, 101.0),
        current_price=100.0,
        atr=1.0,
        asymmetry_ratio=2.5,
        volatility_regime="normal",
    )
    kwargs.update(overrides)
    return execution.assess_execution(**kwargs)


def _decide(readings, **overrides):
    kwargs = dict(
        readings=readings,
        macro_keys=registry.MACRO_FAMILY_KEYS,
        technical_keys=registry.TECHNICAL_FAMILY_KEYS,
        critical_family_keys=registry.CRITICAL_FAMILY_KEYS,
        decision_horizon=enums.Horizon.TACTICAL,
    )
    kwargs.update(overrides)
    return decision.resolve_decision(**kwargs)


# ---------------------------------------------------------------------------
# Voting budget and family membership
# ---------------------------------------------------------------------------
class TestVotingBudget(unittest.TestCase):
    def test_budget_not_exceeded(self):
        self.assertLessEqual(len(registry.VOTING_FAMILIES), registry.VOTING_BUDGET)

    def test_approved_five_families_declared(self):
        self.assertEqual(
            list(registry.voting_family_keys()),
            [
                "policy_real_rates",
                "macro_activity",
                "news_geopolitical",
                "directional",
                "structure",
            ],
        )

    def test_every_voting_family_has_written_justification(self):
        for family in registry.VOTING_FAMILIES:
            self.assertTrue(family.justification.strip(), family.key)
            self.assertGreater(len(family.justification), 80, family.key)

    def test_a_member_belongs_to_exactly_one_family(self):
        seen = set()
        for family in registry.VOTING_FAMILIES:
            for member in family.members:
                self.assertNotIn(member, seen, f"{member} appears in two families")
                seen.add(member)

    def test_registry_rejects_exceeding_the_budget(self):
        extra = [
            registry.FamilyDefinition(
                key=f"invented_{i}",
                label=f"Invented {i}",
                role=enums.Role.ACTIVE_VOTING,
                horizon=enums.Horizon.TACTICAL,
                members=(f"m{i}",),
                justification="x" * 100,
                data_sources=(),
            )
            for i in range(registry.VOTING_BUDGET + 1)
        ]
        original = registry.VOTING_FAMILIES
        try:
            registry.VOTING_FAMILIES = tuple(extra)
            with self.assertRaises(ValueError):
                registry._validate_registry()
        finally:
            registry.VOTING_FAMILIES = original
        registry._validate_registry()

    def test_registry_rejects_a_non_voting_role_in_the_voting_core(self):
        with self.assertRaises(ValueError):
            registry.FamilyDefinition(
                key="sneaky",
                label="Sneaky",
                role=enums.Role.ACTIVE_NON_VOTING,
                horizon=enums.Horizon.TACTICAL,
                members=("m",),
                justification="x" * 100,
                data_sources=(),
            )

    def test_unsupported_b2_components_are_registered_dormant(self):
        dormant = set(registry.dormant_keys())
        for key in (
            "liquidity_funding",
            "positioning_crowding",
            "fiscal_issuance",
            "financial_cycle",
            "sovereign_refinancing",
            "corporate_maturity_wall",
            "systemic_risk_buildup",
            "order_flow_market_depth",
            "vintage_macro_data",
        ):
            self.assertIn(key, dormant)

    def test_dormant_components_are_not_voting(self):
        voting = set(registry.voting_family_keys())
        self.assertFalse(voting & set(registry.dormant_keys()))
        self.assertFalse(voting & set(registry.withheld_keys()))

    def test_only_active_voting_may_influence_direction(self):
        self.assertEqual(enums.DIRECTION_INFLUENCING_ROLES, frozenset({enums.Role.ACTIVE_VOTING}))
        for family in registry.VOTING_FAMILIES:
            self.assertIs(family.role, enums.Role.ACTIVE_VOTING)


# ---------------------------------------------------------------------------
# Neutral is not Unavailable
# ---------------------------------------------------------------------------
class TestNeutralVersusUnavailable(unittest.TestCase):
    def test_missing_and_flat_are_different_states(self):
        self.assertIs(families.classify_signal(None), enums.Direction.UNAVAILABLE)
        self.assertIs(families.classify_signal(0.0), enums.Direction.FLAT)
        self.assertIsNot(families.classify_signal(None), families.classify_signal(0.0))

    def test_corrupt_values_are_unavailable_not_flat(self):
        self.assertIs(families.classify_signal(float("nan")), enums.Direction.UNAVAILABLE)
        self.assertIs(families.classify_signal(float("inf")), enums.Direction.UNAVAILABLE)
        self.assertIs(families.classify_signal("abc"), enums.Direction.UNAVAILABLE)

    def test_family_with_no_data_is_unavailable(self):
        reading = families.evaluate_family(registry.MACRO_ACTIVITY, {})
        self.assertIs(reading.direction, enums.Direction.UNAVAILABLE)
        self.assertFalse(reading.is_available)
        self.assertIs(
            reading.state_against(enums.Direction.BULLISH), enums.FamilyState.UNAVAILABLE
        )

    def test_family_with_flat_data_is_neutral_not_unavailable(self):
        reading = families.evaluate_family(
            registry.MACRO_ACTIVITY, _uniform(registry.MACRO_ACTIVITY, 0.0)
        )
        self.assertIs(reading.direction, enums.Direction.FLAT)
        self.assertTrue(reading.is_available)
        self.assertIs(reading.state_against(enums.Direction.BULLISH), enums.FamilyState.NEUTRAL)

    def test_unavailable_reduces_data_confidence(self):
        gate = gates.evaluate_data_confidence_gate(
            _readings(policy=None, activity=0.4, news=0.4, directional=0.4, structure=0.4),
            registry.CRITICAL_FAMILY_KEYS,
        )
        self.assertTrue(gate.triggered)
        self.assertIs(gate.max_confidence, enums.ConfidenceLevel.LOW)

    def test_neutral_does_not_reduce_data_confidence(self):
        gate = gates.evaluate_data_confidence_gate(
            _readings(policy=0.0, activity=0.0, news=0.0, directional=0.0, structure=0.0),
            registry.CRITICAL_FAMILY_KEYS,
        )
        self.assertFalse(gate.triggered)

    def test_missing_data_never_reverses_direction(self):
        full = _decide(_readings(policy=0.5, activity=0.5, news=0.5, directional=0.5, structure=0.5))
        partial = _decide(
            _readings(policy=0.5, activity=0.5, news=None, directional=0.5, structure=0.5)
        )
        self.assertIs(full.macro_direction, enums.Direction.BULLISH)
        self.assertIs(partial.macro_direction, enums.Direction.BULLISH)


# ---------------------------------------------------------------------------
# Confirmation cap: agreement raises strength, never contribution count
# ---------------------------------------------------------------------------
class TestConfirmationCap(unittest.TestCase):
    def test_three_agreeing_members_are_one_contribution(self):
        reading = families.evaluate_family(
            registry.DIRECTIONAL, _uniform(registry.DIRECTIONAL, 0.6)
        )
        self.assertIs(reading.direction, enums.Direction.BULLISH)
        self.assertIs(reading.strength, enums.FamilyStrength.STRONG)
        self.assertEqual(reading.contribution_count, 1)

    def test_contribution_count_is_always_one(self):
        for value in (0.6, -0.6, 0.0):
            for definition in registry.VOTING_FAMILIES:
                reading = families.evaluate_family(definition, _uniform(definition, value))
                self.assertEqual(reading.contribution_count, 1, definition.key)

    def test_more_agreement_raises_strength_only_and_then_saturates(self):
        one = families.evaluate_family(
            registry.POLICY_REAL_RATES, {"policy_rate_momentum": 0.6}
        )
        two = families.evaluate_family(
            registry.POLICY_REAL_RATES,
            {"policy_rate_momentum": 0.6, "real_yield_momentum": 0.6},
        )
        three = families.evaluate_family(
            registry.POLICY_REAL_RATES,
            {
                "policy_rate_momentum": 0.6,
                "real_yield_momentum": 0.6,
                "nominal_yield_momentum": 0.6,
            },
        )
        four = families.evaluate_family(
            registry.POLICY_REAL_RATES, _uniform(registry.POLICY_REAL_RATES, 0.6)
        )
        self.assertIs(one.strength, enums.FamilyStrength.WEAK)
        self.assertIs(two.strength, enums.FamilyStrength.MODERATE)
        self.assertIs(three.strength, enums.FamilyStrength.STRONG)
        # The fourth agreeing member adds nothing at all.
        self.assertIs(four.strength, enums.FamilyStrength.STRONG)
        self.assertEqual(four.contribution_count, three.contribution_count)

    def test_internal_dissent_downgrades_strength_without_flipping(self):
        clean = families.evaluate_family(
            registry.DIRECTIONAL, _uniform(registry.DIRECTIONAL, 0.6)
        )
        split = families.evaluate_family(
            registry.DIRECTIONAL,
            {
                "short_horizon_return": 0.6,
                "medium_horizon_return": 0.6,
                "multi_timeframe_alignment": -0.6,
            },
        )
        self.assertIs(split.direction, enums.Direction.BULLISH)
        self.assertLess(split.strength.value, clean.strength.value)
        self.assertTrue(split.has_internal_disagreement)

    def test_exact_cancellation_is_flat_not_directional(self):
        reading = families.evaluate_family(
            registry.NEWS_GEOPOLITICAL, {"rule_based_news": 0.6, "ai_news": -0.6}
        )
        self.assertIs(reading.direction, enums.Direction.FLAT)

    def test_membership_is_frozen_against_smuggled_evidence(self):
        with self.assertRaises(ValueError):
            families.evaluate_family(
                registry.DIRECTIONAL, {"short_horizon_return": 0.5, "extra_vote": 0.9}
            )


# ---------------------------------------------------------------------------
# Saturating aggregation and global caps
# ---------------------------------------------------------------------------
class TestSaturatingAggregation(unittest.TestCase):
    def test_aggregation_is_not_additive(self):
        config = aggregation.DEFAULT_AGGREGATION
        one = aggregation.saturating_total([1.0], config)
        five = aggregation.saturating_total([1.0] * 5, config)
        self.assertLess(five, 5.0 * one)

    def test_fifth_agreeing_family_is_worth_materially_less_than_the_first(self):
        config = aggregation.DEFAULT_AGGREGATION
        totals = [aggregation.saturating_total([1.0] * n, config) for n in range(1, 6)]
        first_increment = totals[0]
        fifth_increment = totals[4] - totals[3]
        self.assertLess(fifth_increment, 0.15 * first_increment)

    def test_marginal_value_strictly_diminishes(self):
        config = aggregation.DEFAULT_AGGREGATION
        totals = [aggregation.saturating_total([1.0] * n, config) for n in range(1, 7)]
        increments = [b - a for a, b in zip(totals, totals[1:])]
        for earlier, later in zip(increments, increments[1:]):
            self.assertLess(later, earlier)

    def test_more_agreement_still_never_decreases_evidence(self):
        config = aggregation.DEFAULT_AGGREGATION
        totals = [aggregation.saturating_total([1.0] * n, config) for n in range(1, 7)]
        for earlier, later in zip(totals, totals[1:]):
            self.assertGreaterEqual(later, earlier)

    def test_macro_group_cap_applies_when_all_macro_families_align(self):
        result = aggregation.aggregate(
            _readings(policy=0.7, activity=0.7, news=0.7),
            enums.Direction.BULLISH,
            registry.MACRO_FAMILY_KEYS,
            registry.TECHNICAL_FAMILY_KEYS,
        )
        macro = result.group("macro")
        self.assertIsNotNone(macro)
        self.assertTrue(macro.cap_applied)
        self.assertLessEqual(macro.support, aggregation.DEFAULT_AGGREGATION.macro_group_cap)
        self.assertGreater(macro.raw_support, macro.support)

    def test_global_cap_bounds_total_evidence(self):
        result = aggregation.aggregate(
            _readings(policy=0.7, activity=0.7, news=0.7, directional=0.7, structure=0.7),
            enums.Direction.BULLISH,
            registry.MACRO_FAMILY_KEYS,
            registry.TECHNICAL_FAMILY_KEYS,
        )
        self.assertLessEqual(abs(result.net_evidence), aggregation.DEFAULT_AGGREGATION.global_cap)

    def test_contribution_count_is_per_family_not_per_indicator(self):
        result = aggregation.aggregate(
            _readings(policy=0.7, activity=0.7, news=0.7, directional=0.7, structure=0.7),
            enums.Direction.BULLISH,
            registry.MACRO_FAMILY_KEYS,
            registry.TECHNICAL_FAMILY_KEYS,
        )
        # Twelve agreeing member signals across five families is five contributions.
        self.assertEqual(result.contribution_count, 5)

    def test_disagreement_is_never_hidden_inside_the_aggregate(self):
        result = aggregation.aggregate(
            _readings(policy=0.7, activity=0.7, news=-0.7),
            enums.Direction.BULLISH,
            registry.MACRO_FAMILY_KEYS,
            registry.TECHNICAL_FAMILY_KEYS,
        )
        self.assertTrue(result.disagreement_present)
        self.assertIn("news_geopolitical", result.conflicting_families)
        self.assertIn("news_geopolitical", result.as_record()["conflicting"])

    def test_aggregation_constants_are_declared_uncalibrated(self):
        self.assertFalse(aggregation.DEFAULT_AGGREGATION.calibrated)

    def test_caps_are_derived_from_the_shape_constants_not_hand_set(self):
        config = aggregation.DEFAULT_AGGREGATION
        self.assertAlmostEqual(
            config.block_cap, config.strong_weight * (1.0 + config.diminishing_factor)
        )
        self.assertAlmostEqual(config.macro_group_cap, config.block_cap)
        self.assertAlmostEqual(config.technical_group_cap, config.block_cap)
        self.assertAlmostEqual(
            config.global_cap, config.block_cap * (1.0 + config.diminishing_factor)
        )
        self.assertIsNone(config.block_cap_override)
        self.assertIsNone(config.global_cap_override)

    def test_the_block_cap_can_actually_bind_for_a_real_family_layout(self):
        # A cap that the maximum achievable evidence can never reach would be
        # dead code pretending to be a control.
        config = aggregation.DEFAULT_AGGREGATION
        macro_max = aggregation.saturating_total(
            [config.strong_weight, config.strong_weight, config.moderate_weight], config
        )
        self.assertGreater(macro_max, config.macro_group_cap)

    def test_global_cap_binds_when_every_family_aligns(self):
        result = aggregation.aggregate(
            _readings(policy=0.7, activity=0.7, news=0.7, directional=0.7, structure=0.7),
            enums.Direction.BULLISH,
            registry.MACRO_FAMILY_KEYS,
            registry.TECHNICAL_FAMILY_KEYS,
        )
        self.assertTrue(result.global_cap_applied)
        self.assertIn("global_cap", result.caps_applied)


# ---------------------------------------------------------------------------
# Gates cap, block and defer -- they never point
# ---------------------------------------------------------------------------
class TestGatesAreNotVotes(unittest.TestCase):
    def test_gate_outcome_carries_no_direction_and_no_score(self):
        outcome = gates.evaluate_event_risk_gate(minutes_to_event=5.0, is_top_tier=True)
        fields = set(vars(outcome))
        self.assertNotIn("direction", fields)
        self.assertNotIn("score", fields)
        self.assertNotIn("points", fields)

    def test_event_risk_is_a_state_not_a_number(self):
        outcome = gates.evaluate_event_risk_gate(minutes_to_event=5.0, is_top_tier=True)
        self.assertIsInstance(outcome.event_risk_state, enums.EventRiskState)

    def test_event_gate_result_is_independent_of_candidate_direction(self):
        readings = _readings(policy=0.5, activity=0.5, news=0.5, directional=0.5, structure=0.5)
        bull = gates.evaluate_gates(
            readings=readings,
            candidate=enums.Direction.BULLISH,
            critical_family_keys=registry.CRITICAL_FAMILY_KEYS,
            minutes_to_event=5.0,
            is_top_tier=True,
        )[0]
        bear = gates.evaluate_gates(
            readings=readings,
            candidate=enums.Direction.BEARISH,
            critical_family_keys=registry.CRITICAL_FAMILY_KEYS,
            minutes_to_event=5.0,
            is_top_tier=True,
        )[0]
        self.assertEqual(bull.as_record(), bear.as_record())

    def test_top_tier_release_vetoes_execution(self):
        outcome = gates.evaluate_event_risk_gate(minutes_to_event=5.0, is_top_tier=True)
        self.assertTrue(outcome.vetoes_execution)
        self.assertIs(outcome.event_risk_state, enums.EventRiskState.CRITICAL)

    def test_unsettled_unscheduled_event_vetoes_execution(self):
        outcome = gates.evaluate_event_risk_gate(
            minutes_to_event=None, unsettled_unscheduled_event=True
        )
        self.assertTrue(outcome.vetoes_execution)

    def test_thesis_threatening_event_caps_confidence(self):
        outcome = gates.evaluate_event_risk_gate(
            minutes_to_event=60.0, can_invalidate_thesis=True
        )
        self.assertIs(outcome.action, enums.GateAction.CAP_CONFIDENCE)
        self.assertIs(outcome.max_confidence, enums.ConfidenceLevel.MODERATE)

    def test_distant_event_only_warns(self):
        outcome = gates.evaluate_event_risk_gate(minutes_to_event=300.0)
        self.assertIs(outcome.action, enums.GateAction.WARN)
        self.assertIs(outcome.event_risk_state, enums.EventRiskState.NORMAL)

    def test_event_risk_applies_to_open_positions(self):
        outcome = gates.evaluate_event_risk_gate(
            minutes_to_event=5.0, is_top_tier=True, position_open=True
        )
        self.assertTrue(outcome.applies_to_open_position)

    def test_ceilings_are_minimums_not_averages(self):
        low = gates.GateOutcome(
            gate="a", triggered=True, action=enums.GateAction.CAP_CONFIDENCE,
            reason="", max_confidence=enums.ConfidenceLevel.LOW,
        )
        high = gates.GateOutcome(
            gate="b", triggered=True, action=enums.GateAction.CAP_CONFIDENCE,
            reason="", max_confidence=enums.ConfidenceLevel.HIGH,
        )
        self.assertIs(
            gates.combined_confidence_ceiling((low, high)), enums.ConfidenceLevel.LOW
        )

    def test_confidence_is_categorical_not_a_percentage(self):
        for level in enums.ConfidenceLevel:
            self.assertIn(level.name, {"LOW", "MODERATE", "HIGH"})
        self.assertIs(
            enums.ConfidenceLevel.HIGH.capped_at(enums.ConfidenceLevel.LOW),
            enums.ConfidenceLevel.LOW,
        )
        self.assertIs(
            enums.ConfidenceLevel.LOW.capped_at(enums.ConfidenceLevel.HIGH),
            enums.ConfidenceLevel.LOW,
        )


# ---------------------------------------------------------------------------
# Execution separation
# ---------------------------------------------------------------------------
class TestExecutionSeparation(unittest.TestCase):
    def test_entry_quality_cannot_be_assessed_without_invalidation(self):
        assessment = execution.assess_execution(
            invalidation_level=None, entry_zone=(99.0, 101.0), current_price=100.0, atr=1.0
        )
        self.assertFalse(assessment.invalidation_defined)
        self.assertTrue(assessment.blocked)
        self.assertIs(assessment.execution_confidence, enums.ConfidenceLevel.LOW)
        self.assertIn("invalidation_required_before_entry_quality", assessment.notes)

    def test_good_entry_yields_high_execution_confidence(self):
        self.assertIs(_good_execution().execution_confidence, enums.ConfidenceLevel.HIGH)

    def test_price_outside_the_zone_caps_execution_confidence(self):
        assessment = _good_execution(current_price=101.5)
        self.assertFalse(assessment.in_zone)
        self.assertIs(assessment.execution_confidence, enums.ConfidenceLevel.MODERATE)

    def test_extended_move_is_representable(self):
        assessment = _good_execution(current_price=105.0)
        self.assertTrue(assessment.extended)
        self.assertIs(assessment.execution_confidence, enums.ConfidenceLevel.LOW)

    def test_constrained_room_caps_execution_confidence(self):
        self.assertIs(
            _good_execution(asymmetry_ratio=0.5).execution_confidence,
            enums.ConfidenceLevel.LOW,
        )

    def test_entry_is_a_zone_never_a_single_price(self):
        assessment = _good_execution()
        self.assertIsNotNone(assessment.entry_zone)
        self.assertNotEqual(assessment.entry_zone[0], assessment.entry_zone[1])

    def test_veto_blocks_execution_without_touching_the_setup(self):
        veto = gates.evaluate_event_risk_gate(minutes_to_event=5.0, is_top_tier=True)
        assessment = _good_execution(gates=(veto,))
        self.assertTrue(assessment.blocked)
        self.assertTrue(assessment.invalidation_defined)
        self.assertTrue(assessment.deferred_not_invalidated)


class TestVolatilityIsNeverDirectional(unittest.TestCase):
    def test_volatility_regime_does_not_change_direction(self):
        readings = _readings(policy=0.5, activity=0.5, news=0.5, directional=0.5, structure=0.5)
        directions = set()
        for regime in ("compression", "normal", "expansion", "unavailable"):
            outcome = _decide(readings, execution=_good_execution(volatility_regime=regime))
            directions.add(outcome.direction)
        self.assertEqual(directions, {enums.Direction.BULLISH})

    def test_expansion_only_caps_execution_confidence(self):
        normal = _good_execution(volatility_regime="normal")
        expanded = _good_execution(volatility_regime="expansion")
        self.assertIs(normal.execution_confidence, enums.ConfidenceLevel.HIGH)
        self.assertIs(expanded.execution_confidence, enums.ConfidenceLevel.MODERATE)

    def test_high_volatility_is_not_hardcoded_bearish(self):
        source = _b2_source().lower()
        self.assertNotIn("high volatility = bearish", source)
        for line in source.splitlines():
            if "expansion" in line and ("bearish" in line or "bullish" in line):
                self.fail(f"volatility regime appears tied to direction: {line.strip()}")


# ---------------------------------------------------------------------------
# Horizons
# ---------------------------------------------------------------------------
class TestHorizonSeparation(unittest.TestCase):
    def test_every_family_declares_a_horizon(self):
        for family in registry.VOTING_FAMILIES:
            self.assertIsInstance(family.horizon, enums.Horizon)

    def test_macro_and_technical_blocks_are_horizon_disjoint(self):
        self.assertFalse(registry.MACRO_FAMILY_KEYS & registry.TECHNICAL_FAMILY_KEYS)
        for family in registry.VOTING_FAMILIES:
            if family.key in registry.MACRO_FAMILY_KEYS:
                self.assertIs(family.horizon, enums.Horizon.TACTICAL)
            else:
                self.assertIs(family.horizon, enums.Horizon.EXECUTION)

    def test_no_structural_horizon_family_votes(self):
        for family in registry.VOTING_FAMILIES:
            self.assertIsNot(family.horizon, enums.Horizon.STRUCTURAL)

    def test_operational_priority_follows_the_decision_horizon(self):
        self.assertEqual(decision.operational_priority_for(enums.Horizon.EXECUTION), "technical")
        self.assertEqual(decision.operational_priority_for(enums.Horizon.STRUCTURAL), "macro")
        self.assertEqual(decision.operational_priority_for(enums.Horizon.TACTICAL), "balanced")

    def test_every_decision_records_its_horizon(self):
        for horizon in enums.Horizon:
            outcome = _decide(
                _readings(policy=0.5, activity=0.5, news=0.5), decision_horizon=horizon
            )
            self.assertIs(outcome.horizon, horizon)
            self.assertEqual(outcome.as_record()["horizon"], horizon.value)


# ---------------------------------------------------------------------------
# Decision states
# ---------------------------------------------------------------------------
class TestDecisionStates(unittest.TestCase):
    def test_confirmed_thesis(self):
        outcome = _decide(
            _readings(policy=0.5, activity=0.5, news=0.5, directional=0.5, structure=0.5),
            execution=_good_execution(),
        )
        self.assertIs(outcome.state, enums.DecisionState.CONFIRMED_THESIS)

    def test_thesis_confirmed_but_wait_for_better_entry(self):
        outcome = _decide(
            _readings(policy=0.5, activity=0.5, news=0.5, directional=0.5, structure=0.5),
            execution=_good_execution(asymmetry_ratio=0.4),
        )
        self.assertIs(outcome.state, enums.DecisionState.THESIS_VALID_WAIT_FOR_ENTRY)

    def test_late_or_extended_entry_is_not_a_fresh_setup(self):
        outcome = _decide(
            _readings(policy=0.5, activity=0.5, news=0.5, directional=0.5, structure=0.5),
            execution=_good_execution(current_price=110.0),
        )
        self.assertIs(outcome.state, enums.DecisionState.THESIS_CONFIRMED_LATE_EXTENDED)

    def test_macro_strong_technical_absent_waits(self):
        outcome = _decide(
            _readings(policy=0.5, activity=0.5, news=0.5, directional=0.0, structure=0.0),
            execution=_good_execution(),
        )
        self.assertIs(outcome.state, enums.DecisionState.THESIS_VALID_WAIT_FOR_ENTRY)

    def test_technical_setup_with_weak_macro_support(self):
        outcome = _decide(
            _readings(policy=0.0, activity=0.0, news=0.0, directional=0.5, structure=0.5),
            execution=_good_execution(),
        )
        self.assertIs(outcome.state, enums.DecisionState.TECHNICAL_SETUP_WEAK_MACRO_SUPPORT)

    def test_mixed_no_edge_is_distinct_from_insufficient_data(self):
        no_edge = _decide(
            _readings(policy=0.0, activity=0.0, news=0.0, directional=0.0, structure=0.0)
        )
        degraded = _decide(_readings(policy=None, activity=None, news=None))
        self.assertIs(no_edge.state, enums.DecisionState.MIXED_NO_EDGE)
        self.assertIs(degraded.state, enums.DecisionState.INSUFFICIENT_DATA_SYSTEM_DEGRADED)
        self.assertIsNot(no_edge.state, degraded.state)

    def test_execution_blocked_carries_a_reason(self):
        veto = gates.evaluate_event_risk_gate(minutes_to_event=2.0, is_top_tier=True)
        outcome = _decide(
            _readings(policy=0.5, activity=0.5, news=0.5, directional=0.5, structure=0.5),
            gates=(veto,),
            execution=_good_execution(gates=(veto,)),
        )
        self.assertIs(outcome.state, enums.DecisionState.EXECUTION_BLOCKED)
        self.assertTrue(outcome.reason.strip())

    def test_high_event_risk_without_a_setup(self):
        veto = gates.evaluate_event_risk_gate(minutes_to_event=2.0, is_top_tier=True)
        outcome = _decide(
            _readings(policy=0.0, activity=0.0, news=0.0, directional=0.0, structure=0.0),
            gates=(veto,),
        )
        self.assertIs(outcome.state, enums.DecisionState.HIGH_EVENT_RISK)

    def test_open_position_states(self):
        readings = _readings(policy=0.5, activity=0.5, news=0.5, directional=0.5, structure=0.5)
        intact = _decide(readings, position_open=True, execution=_good_execution())
        self.assertIs(intact.state, enums.DecisionState.POSITION_OPEN_THESIS_INTACT)

        veto = gates.evaluate_event_risk_gate(
            minutes_to_event=2.0, is_top_tier=True, position_open=True
        )
        under_review = _decide(readings, position_open=True, gates=(veto,))
        self.assertIs(under_review.state, enums.DecisionState.POSITION_OPEN_UNDER_REVIEW)

    def test_nothing_collapses_to_buy_sell_neutral(self):
        values = {state.value for state in enums.DecisionState}
        self.assertFalse(values & {"buy", "sell", "neutral"})
        self.assertGreaterEqual(len(values), 13)


class TestInvalidationSeparation(unittest.TestCase):
    def test_technical_invalidation_does_not_invalidate_the_macro_thesis(self):
        outcome = _decide(
            _readings(policy=0.5, activity=0.5, news=0.5, directional=0.5, structure=0.5),
            technical_invalidated=True,
        )
        self.assertIs(outcome.state, enums.DecisionState.TECHNICAL_SETUP_INVALIDATED)
        self.assertIsNot(outcome.state, enums.DecisionState.MACRO_THESIS_INVALIDATED)
        self.assertIs(outcome.macro_direction, enums.Direction.BULLISH)
        self.assertTrue(any("re-entry" in note for note in outcome.notes))

    def test_macro_invalidation_is_its_own_state(self):
        outcome = _decide(
            _readings(policy=0.5, activity=0.5, news=0.5, directional=0.5, structure=0.5),
            thesis_state=enums.ThesisState.INVALIDATED,
        )
        self.assertIs(outcome.state, enums.DecisionState.MACRO_THESIS_INVALIDATED)

    def test_thesis_under_review_is_distinct(self):
        outcome = _decide(
            _readings(policy=0.5, activity=0.5, news=0.5, directional=0.5, structure=0.5),
            thesis_state=enums.ThesisState.UNDER_REVIEW,
        )
        self.assertIs(outcome.state, enums.DecisionState.THESIS_UNDER_REVIEW)

    def test_false_signal_detection_is_registered_separately_and_withheld(self):
        self.assertIn("false_signal_whipsaw_detection", registry.withheld_keys())


class TestHorizonPriorityConflicts(unittest.TestCase):
    def _conflict(self, horizon):
        return _decide(
            _readings(policy=0.5, activity=0.5, news=0.5, directional=-0.5, structure=-0.5),
            decision_horizon=horizon,
            execution=_good_execution(),
        )

    def test_tactical_horizon_waits_on_conflict(self):
        outcome = self._conflict(enums.Horizon.TACTICAL)
        self.assertIs(outcome.state, enums.DecisionState.THESIS_VALID_WAIT_FOR_ENTRY)

    def test_execution_horizon_gives_technical_operational_priority(self):
        outcome = self._conflict(enums.Horizon.EXECUTION)
        self.assertIs(outcome.state, enums.DecisionState.TECHNICAL_SETUP_WEAK_MACRO_SUPPORT)
        self.assertEqual(outcome.operational_priority, "technical")
        self.assertIs(outcome.direction, enums.Direction.BEARISH)

    def test_macro_never_universally_dominates(self):
        tactical = self._conflict(enums.Horizon.TACTICAL)
        execution_horizon = self._conflict(enums.Horizon.EXECUTION)
        self.assertIsNot(tactical.state, execution_horizon.state)

    def test_agreement_does_not_push_conviction_to_maximum(self):
        outcome = _decide(
            _readings(policy=0.7, activity=0.7, news=0.7, directional=0.7, structure=0.7),
            execution=_good_execution(),
        )
        self.assertTrue(
            any("not fully independent" in note for note in outcome.notes)
        )

    def test_crowding_is_reported_unavailable_not_assumed_absent(self):
        outcome = _decide(
            _readings(policy=0.5, activity=0.5, news=0.5, directional=0.5, structure=0.5),
            execution=_good_execution(),
        )
        self.assertTrue(any("Crowding check unavailable" in n for n in outcome.notes))


# ---------------------------------------------------------------------------
# Risk controls
# ---------------------------------------------------------------------------
class TestRiskControls(unittest.TestCase):
    def test_no_trading_parameters_are_invented(self):
        self.assertEqual(
            set(risk.DEFAULT_RISK_PARAMETERS.unset_parameters()),
            {
                "max_risk_fraction_per_trade",
                "max_total_exposure_fraction",
                "max_drawdown_fraction",
                "max_concurrent_correlated_positions",
            },
        )
        self.assertFalse(risk.DEFAULT_RISK_PARAMETERS.is_configured)

    def test_unset_parameters_are_marked_for_the_operator(self):
        record = risk.DEFAULT_RISK_PARAMETERS.as_record()
        self.assertTrue(all(v == risk.OPERATOR_MUST_SET for v in record.values()))

    def test_sizing_is_unavailable_until_the_operator_configures_it(self):
        directive = risk.size_directive(atr_ratio=1.0)
        self.assertFalse(directive.available)
        self.assertIn("risk_parameters_not_configured", directive.unavailable_reasons)

    def test_unmeasurable_volatility_is_not_defaulted(self):
        self.assertIsNone(risk.volatility_scale(None))
        self.assertIsNone(risk.volatility_scale(0.0))
        self.assertIsNone(risk.volatility_scale(float("nan")))

    def test_volatility_scaling_reduces_size_when_volatility_rises(self):
        calm = risk.volatility_scale(0.5)
        normal = risk.volatility_scale(1.0)
        wild = risk.volatility_scale(2.0)
        self.assertEqual(calm, 1.0)
        self.assertEqual(normal, 1.0)
        self.assertLess(wild, normal)

    def test_risk_raises_the_confirmation_bar_rather_than_inventing_a_multiplier(self):
        directive = risk.size_directive(
            atr_ratio=1.0,
            disagreement_present=True,
            data_confidence=enums.ConfidenceLevel.LOW,
        )
        self.assertTrue(directive.confirmation_bar_raised)
        self.assertIn("family_disagreement", directive.bar_reasons)
        self.assertIn("low_data_confidence", directive.bar_reasons)

    def test_event_risk_on_an_open_position_is_flagged(self):
        gate = gates.evaluate_event_risk_gate(
            minutes_to_event=5.0, is_top_tier=True, position_open=True
        )
        directive = risk.size_directive(atr_ratio=1.0, gates=(gate,))
        self.assertTrue(any("OPEN position" in note for note in directive.notes))


# ---------------------------------------------------------------------------
# Adapters
# ---------------------------------------------------------------------------
class TestAdapters(unittest.TestCase):
    ROWS = [
        {"cat": "rate", "weight": 2.0, "score": 0.4},
        {"cat": "inflation", "weight": 2.0, "score": 0.6},
        {"cat": "inflation", "weight": 1.0, "score": 0.3},
        {"cat": "labor_neg", "weight": 1.5, "score": -0.2},
        {"cat": "growth", "weight": 1.5, "score": 0.1},
    ]

    def test_policy_signals_read_only_rate_rows(self):
        signals = adapters.policy_real_rates_signals(composite_rows=self.ROWS)
        self.assertAlmostEqual(signals["policy_rate_momentum"], 0.4)
        self.assertIsNone(signals["real_yield_momentum"])

    def test_activity_signals_are_weighted_by_category(self):
        signals = adapters.macro_activity_signals(composite_rows=self.ROWS)
        self.assertAlmostEqual(signals["inflation_momentum"], (0.6 * 2.0 + 0.3 * 1.0) / 3.0)
        self.assertAlmostEqual(signals["labor_momentum"], -0.2)
        self.assertAlmostEqual(signals["growth_momentum"], 0.1)

    def test_absent_category_is_none_not_zero(self):
        signals = adapters.macro_activity_signals(composite_rows=[{"cat": "rate", "score": 0.5}])
        self.assertIsNone(signals["inflation_momentum"])
        self.assertIsNone(signals["growth_momentum"])

    def test_no_rows_yields_all_none(self):
        for value in adapters.macro_activity_signals(composite_rows=None).values():
            self.assertIsNone(value)

    def test_news_points_are_rescaled_from_the_project_convention(self):
        signals = adapters.news_geopolitical_signals(rule_points=0.25, ai_points=None)
        self.assertAlmostEqual(signals["rule_based_news"], 0.5)
        self.assertIsNone(signals["ai_news"])

    def test_directional_signals_preserve_sign(self):
        tactical = {"ret_15m": 0.002, "ret_1h": 0.004, "ret_4h": 0.008}
        signals = adapters.directional_signals(tactical=tactical)
        self.assertGreater(signals["short_horizon_return"], 0)
        self.assertEqual(signals["multi_timeframe_alignment"], 1.0)

    def test_mixed_timeframes_are_not_aligned(self):
        tactical = {"ret_15m": 0.002, "ret_1h": -0.004, "ret_4h": 0.008}
        signals = adapters.directional_signals(tactical=tactical)
        self.assertEqual(signals["multi_timeframe_alignment"], 0.0)

    def test_alignment_is_unavailable_when_a_timeframe_is_missing(self):
        signals = adapters.directional_signals(tactical={"ret_15m": 0.002, "ret_1h": 0.004})
        self.assertIsNone(signals["multi_timeframe_alignment"])

    def test_structure_uses_only_price_derived_breakout(self):
        self.assertEqual(
            adapters.structure_signals(tactical={"structure": "Upside Breakout"})["breakout_quality"],
            1.0,
        )
        self.assertEqual(
            adapters.structure_signals(tactical={"structure": "Downside Breakdown"})["breakout_quality"],
            -1.0,
        )
        self.assertEqual(
            adapters.structure_signals(tactical={"structure": "Range / Mean-Reversion"})["breakout_quality"],
            0.0,
        )

    def test_trend_labels_do_not_vote_in_the_structure_family(self):
        for label in ("Higher Short-Term Trend", "Lower Short-Term Trend"):
            self.assertEqual(
                adapters.structure_signals(tactical={"structure": label})["breakout_quality"],
                0.0,
                label,
            )

    def test_macro_conditioned_zone_member_is_unavailable_with_a_reason(self):
        signals = adapters.structure_signals(tactical={"structure": "Upside Breakout"})
        self.assertIsNone(signals["price_structure_zone"])
        self.assertIsNone(signals["retest_behaviour"])
        self.assertIn("price_structure_zone", adapters.UNAVAILABLE_REASONS)
        self.assertIn("retest_behaviour", adapters.UNAVAILABLE_REASONS)

    def test_execution_inputs_read_the_entry_plan(self):
        plan = {
            "invalidation": 95.0,
            "zone_low": 99.0,
            "zone_high": 101.0,
            "current_analysis_price": 100.0,
            "atr": 1.0,
            "volatility_regime": "normal",
            "status": "IN ZONE — WAIT CONFIRMATION",
            "opportunity_quality": {
                "room_to_opposing_structure_atr": 5.0,
                "asymmetry_ratio": 2.5,
            },
        }
        inputs = adapters.execution_inputs(entry_plan=plan)
        self.assertEqual(inputs["invalidation_level"], 95.0)
        self.assertEqual(inputs["entry_zone"], (99.0, 101.0))
        self.assertEqual(inputs["asymmetry_ratio"], 2.5)
        self.assertFalse(inputs["technical_invalidated"])

    def test_invalidated_entry_plan_is_detected(self):
        inputs = adapters.execution_inputs(entry_plan={"status": "INVALIDATED"})
        self.assertTrue(inputs["technical_invalidated"])
        self.assertIsNone(inputs["invalidation_level"])

    def test_unavailable_opportunity_quality_is_not_faked(self):
        inputs = adapters.execution_inputs(
            entry_plan={"opportunity_quality": {"unavailable": True, "reason": "none found"}}
        )
        self.assertIsNone(inputs["asymmetry_ratio"])
        self.assertIsNone(inputs["room_to_opposing_atr"])

    def test_build_signals_covers_exactly_the_voting_core(self):
        built = adapters.build_signals()
        self.assertEqual(set(built), set(registry.voting_family_keys()))
        for key, signals in built.items():
            self.assertEqual(set(signals), set(registry.FAMILIES_BY_KEY[key].members))


# ---------------------------------------------------------------------------
# Structural safety constraints (the operator's Stage A requirements)
# ---------------------------------------------------------------------------
class TestStageASafetyConstraints(unittest.TestCase):
    def test_b2_imports_nothing_from_production_core(self):
        imported = _b2_imported_modules()
        for forbidden in ("apex.production_core", "streamlit", "requests", "threading"):
            self.assertNotIn(forbidden, imported)
        for name in imported:
            self.assertNotIn("production_core", name)

    def test_b2_imports_only_stdlib_typing_and_its_own_package(self):
        # Every entry is pure stdlib with no I/O surface: no file, socket,
        # subprocess or thread primitive can be reached through any of them.
        allowed_absolute = {
            "__future__",
            "dataclasses",
            "datetime",
            "enum",
            "hashlib",
            "types",
            "typing",
        }
        for name in _b2_imported_modules():
            if name.startswith("."):
                continue
            self.assertIn(name, allowed_absolute, name)

    def test_production_core_is_not_pulled_in_by_importing_b2(self):
        # apex/__init__.py is a bare docstring, so importing apex.b2 must not
        # drag the monolith (and its module-level durable-state load) into the
        # process. Checked in a clean subprocess, because sys.modules in this
        # process is shared with the other test modules, which do import it.
        import subprocess

        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        script = (
            "import sys; import apex.b2; "
            "leaked = [m for m in sys.modules if 'production_core' in m "
            "or m == 'streamlit' or m == 'requests']; "
            "print(','.join(sorted(leaked)))"
        )
        completed = subprocess.run(
            [sys.executable, "-c", script],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr[-2000:])
        self.assertEqual(completed.stdout.strip(), "", completed.stdout.strip())

    def test_b2_makes_no_ai_calls_no_threads_no_telegram(self):
        source = _b2_executable_source()
        for forbidden in (
            "_post_ai_chat",
            "send_telegram_alert",
            "threading.Thread",
            "st.cache_data",
            "st.cache_resource",
            "requests.get",
            "requests.post",
        ):
            self.assertNotIn(forbidden, source, forbidden)

    def test_b2_writes_no_durable_state(self):
        source = _b2_executable_source()
        for forbidden in (
            "PROJECT_ROOT",
            "_save_persistent_state",
            "json.dump",
            "open(",
            "os.replace",
        ):
            self.assertNotIn(forbidden, source, forbidden)

    def test_circular_cross_asset_implementation_is_not_activated(self):
        source = _b2_executable_source()
        for forbidden in (
            "compute_cross_asset_confirmation",
            "compute_macro_regime_context",
            "compute_relative_value",
            "compute_recent_macro_surprise",
        ):
            # Referenced in registry prose as withheld, never called.
            self.assertNotIn(f"{forbidden}(", source, forbidden)

    def test_dead_shadow_functions_are_registered_withheld_not_implemented(self):
        withheld = set(registry.withheld_keys())
        for key in (
            "cross_asset_bridge",
            "relative_value_layer",
            "macro_regime_context",
            "recent_macro_surprise",
            "macro_thesis_invalidation",
            "regime_confidence",
        ):
            self.assertIn(key, withheld)

    def test_cross_asset_bridge_records_its_non_circularity_requirement(self):
        component = next(
            c for c in registry.WITHHELD_COMPONENTS if c.key == "cross_asset_bridge"
        )
        self.assertIn("thesis-input registry", component.blocking_requirement)

    def test_fabricated_gold_display_values_are_registered_as_non_evidence(self):
        component = next(
            c
            for c in registry.WITHHELD_COMPONENTS
            if c.key == "gold_pricing_matrix_display_rows"
        )
        self.assertIn("never", component.reason.lower())
        # And no B2 adapter reads the Gold page's display rows.
        self.assertNotIn("page_gold", _b2_source())

    def test_no_numeric_confidence_percentage_is_emitted(self):
        outcome = _decide(
            _readings(policy=0.5, activity=0.5, news=0.5, directional=0.5, structure=0.5),
            execution=_good_execution(),
        )
        record = outcome.as_record()
        self.assertIn(record["confidence_ceiling"], {None, "LOW", "MODERATE", "HIGH"})
        self.assertEqual(record["execution"]["execution_confidence"], "HIGH")

    def test_records_are_json_serialisable_for_structured_audit(self):
        import json

        outcome = _decide(
            _readings(policy=0.5, activity=0.5, news=0.5, directional=0.5, structure=0.5),
            gates=gates.evaluate_gates(
                readings=_readings(policy=0.5, activity=0.5, news=0.5),
                candidate=enums.Direction.BULLISH,
                critical_family_keys=registry.CRITICAL_FAMILY_KEYS,
                minutes_to_event=45.0,
            ),
            execution=_good_execution(),
        )
        json.dumps(outcome.as_record())
        json.dumps(registry.describe_budget())
        for reading in _readings(policy=0.5, activity=0.5, news=0.5):
            json.dumps(reading.as_record())

    def test_budget_summary_reports_dormant_and_withheld(self):
        summary = registry.describe_budget()
        self.assertEqual(summary["declared"], 5)
        self.assertEqual(summary["remaining"], 1)
        self.assertTrue(summary["dormant"])
        self.assertTrue(summary["withheld"])

    def test_public_surface_is_importable(self):
        for name in b2.__all__:
            self.assertTrue(hasattr(b2, name), name)


if __name__ == "__main__":
    unittest.main()
