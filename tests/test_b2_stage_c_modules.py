"""Stage C tests for the B2 asset-specific transmission modules.

Imports only ``apex.b2``, which performs no I/O, so this module writes no
durable state.

The central claim under test is that asset modules are diagnostics, not
evidence: they restate what already voted, they never vote again, and the
voting budget is untouched by their existence.
"""
from __future__ import annotations

import ast
import inspect
import json
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from apex.b2 import event_timing, modules, registry
from apex.b2.enums import Direction, FamilyStrength, Horizon, Role
from apex.b2.modules import base
from apex.b2.modules.base import (
    DriverDefinition,
    DriverEvidenceClass,
    TransmissionState,
)

NOW = datetime(2026, 8, 30, 12, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Duplicate-evidence protection -- the load-bearing Stage C guarantee
# ---------------------------------------------------------------------------
class TestNoAdditionalVotingPower(unittest.TestCase):
    def test_voting_budget_is_unchanged_by_asset_modules(self):
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
        self.assertEqual(len(registry.VOTING_FAMILIES), 5)

    def test_asset_modules_are_registered_with_the_non_voting_role(self):
        self.assertTrue(registry.ASSET_MODULES)
        for module in registry.ASSET_MODULES:
            self.assertIs(module.role, Role.ASSET_SPECIFIC_MODULE)
            self.assertIsNot(module.role, Role.ACTIVE_VOTING)

    def test_asset_module_keys_never_collide_with_families_or_components(self):
        module_keys = set(registry.asset_module_keys())
        self.assertFalse(module_keys & set(registry.voting_family_keys()))
        self.assertFalse(module_keys & set(registry.dormant_keys()))
        self.assertFalse(module_keys & set(registry.withheld_keys()))

    def test_no_driver_in_any_module_claims_independence(self):
        for instrument, module in modules.MODULES.items():
            for driver in module.DRIVERS:
                self.assertIsNot(
                    driver.evidence_class,
                    DriverEvidenceClass.INDEPENDENT_ASSET_SPECIFIC,
                    f"{instrument}.{driver.key} claims independent evidence",
                )

    def test_every_driver_names_the_family_it_restates(self):
        for instrument, module in modules.MODULES.items():
            for driver in module.DRIVERS:
                self.assertIn(
                    driver.universal_family_overlap,
                    set(registry.voting_family_keys()),
                    f"{instrument}.{driver.key} overlap is not a declared family",
                )

    def test_a_driver_overlapping_a_family_cannot_be_independent(self):
        with self.assertRaises(ValueError):
            DriverDefinition(
                key="sneaky",
                label="Sneaky second vote",
                evidence_class=DriverEvidenceClass.INDEPENDENT_ASSET_SPECIFIC,
                horizon=Horizon.TACTICAL,
                universal_family_overlap="policy_real_rates",
                rationale="x" * 40,
            )

    def test_an_independent_driver_with_no_overlap_is_still_permitted(self):
        # The rule bans double counting, not independence itself. If genuinely
        # independent data ever arrives, this must remain expressible.
        allowed = DriverDefinition(
            key="future_independent",
            label="Some genuinely new dataset",
            evidence_class=DriverEvidenceClass.INDEPENDENT_ASSET_SPECIFIC,
            horizon=Horizon.TACTICAL,
            universal_family_overlap=None,
            rationale="x" * 40,
        )
        self.assertIsNone(allowed.universal_family_overlap)

    def test_module_readings_carry_no_evidence_value(self):
        reading = modules.gold.evaluate(
            thesis_direction=Direction.BULLISH,
            real_yield_mtf={"score": -0.5},
            usd_macro_score=-0.4,
            gold_rule_points=0.3,
        )
        self.assertFalse(reading.contributes_evidence)
        for field in ("score", "evidence", "weight", "net_evidence", "vote"):
            self.assertFalse(hasattr(reading, field), field)
        for driver in reading.drivers:
            self.assertFalse(driver.contributes_vote)

    def test_the_aggregator_and_decision_layer_never_see_a_module(self):
        from apex.b2 import aggregation, decision

        for module in (aggregation, decision):
            source = inspect.getsource(module)
            self.assertNotIn("AssetModuleReading", source)
            self.assertNotIn("asset_module", source)
            self.assertNotIn("modules", source)

    def test_modules_are_capped_at_three_drivers(self):
        for instrument, module in modules.MODULES.items():
            self.assertLessEqual(len(module.DRIVERS), 3, instrument)
        with self.assertRaises(ValueError):
            base.validate_definitions(
                "too_many",
                tuple(
                    DriverDefinition(
                        key=f"d{i}",
                        label=f"D{i}",
                        evidence_class=DriverEvidenceClass.TRANSFORMATION,
                        horizon=Horizon.TACTICAL,
                        universal_family_overlap="macro_activity",
                        rationale="x" * 40,
                    )
                    for i in range(4)
                ),
            )

    def test_every_driver_carries_a_written_rationale(self):
        for module in modules.MODULES.values():
            for driver in module.DRIVERS:
                self.assertGreater(len(driver.rationale), 80, driver.key)


# ---------------------------------------------------------------------------
# Neutral vs Unavailable, and no fabricated evidence
# ---------------------------------------------------------------------------
class TestDriverStates(unittest.TestCase):
    DEF = DriverDefinition(
        key="probe",
        label="Probe channel",
        evidence_class=DriverEvidenceClass.TRANSFORMATION,
        horizon=Horizon.TACTICAL,
        universal_family_overlap="macro_activity",
        rationale="x" * 40,
    )

    def test_missing_is_unavailable_not_neutral(self):
        reading = base.evaluate_driver(self.DEF, None, Direction.BULLISH)
        self.assertIs(reading.state, TransmissionState.UNAVAILABLE)
        self.assertIs(reading.direction, Direction.UNAVAILABLE)
        self.assertIsNone(reading.value)
        self.assertFalse(reading.is_available)

    def test_flat_is_neutral_and_available(self):
        reading = base.evaluate_driver(self.DEF, 0.0, Direction.BULLISH)
        self.assertIs(reading.state, TransmissionState.NEUTRAL)
        self.assertIs(reading.direction, Direction.FLAT)
        self.assertTrue(reading.is_available)

    def test_unavailable_and_neutral_are_different_states(self):
        missing = base.evaluate_driver(self.DEF, None, Direction.BULLISH)
        flat = base.evaluate_driver(self.DEF, 0.0, Direction.BULLISH)
        self.assertIsNot(missing.state, flat.state)
        self.assertNotEqual(missing.as_record(), flat.as_record())

    def test_corrupt_values_are_unavailable_not_flat(self):
        for bad in (float("nan"), float("inf"), "abc"):
            reading = base.evaluate_driver(self.DEF, bad, Direction.BULLISH)
            self.assertIs(reading.state, TransmissionState.UNAVAILABLE)

    def test_supporting_and_conflicting_are_relative_to_the_thesis(self):
        bullish_value = base.evaluate_driver(self.DEF, 0.5, Direction.BULLISH)
        against = base.evaluate_driver(self.DEF, 0.5, Direction.BEARISH)
        self.assertIs(bullish_value.state, TransmissionState.SUPPORTING)
        self.assertIs(against.state, TransmissionState.CONFLICTING)

    def test_no_directional_thesis_yields_neutral_not_supporting(self):
        reading = base.evaluate_driver(self.DEF, 0.5, Direction.FLAT)
        self.assertIs(reading.state, TransmissionState.NEUTRAL)

    def test_strength_bands_reuse_existing_project_thresholds(self):
        self.assertIs(
            base.evaluate_driver(self.DEF, 0.10, Direction.BULLISH).strength,
            FamilyStrength.WEAK,
        )
        self.assertIs(
            base.evaluate_driver(self.DEF, 0.25, Direction.BULLISH).strength,
            FamilyStrength.MODERATE,
        )
        self.assertIs(
            base.evaluate_driver(self.DEF, 0.60, Direction.BULLISH).strength,
            FamilyStrength.STRONG,
        )

    def test_undeclared_values_are_rejected(self):
        with self.assertRaises(ValueError):
            base.build_module_reading(
                module="probe_module",
                instrument="Probe",
                horizon=Horizon.TACTICAL,
                thesis_direction=Direction.BULLISH,
                definitions=(self.DEF,),
                values={"probe": 0.2, "smuggled": 0.9},
            )

    def test_a_module_with_nothing_measurable_is_unavailable_not_neutral(self):
        reading = base.build_module_reading(
            module="probe_module",
            instrument="Probe",
            horizon=Horizon.TACTICAL,
            thesis_direction=Direction.BULLISH,
            definitions=(self.DEF,),
            values={"probe": None},
        )
        self.assertIs(reading.transmission_summary, TransmissionState.UNAVAILABLE)
        self.assertIn("not the same as reading neutral", reading.rationale)


# ---------------------------------------------------------------------------
# C1 -- Gold
# ---------------------------------------------------------------------------
class TestGoldModule(unittest.TestCase):
    def _evaluate(self, **kwargs):
        params = dict(
            thesis_direction=Direction.BULLISH,
            real_yield_mtf={"score": -0.5},
            usd_macro_score=-0.4,
            gold_rule_points=0.3,
            gold_ai_points=0.2,
        )
        params.update(kwargs)
        return modules.gold.evaluate(**params)

    def test_gold_declares_exactly_three_channels(self):
        self.assertEqual(
            [d.key for d in modules.gold.DRIVERS],
            [
                "real_rate_transmission",
                "usd_transmission",
                "safe_haven_news_transmission",
            ],
        )

    def test_channels_map_onto_the_expected_families(self):
        overlaps = {d.key: d.universal_family_overlap for d in modules.gold.DRIVERS}
        self.assertEqual(overlaps["real_rate_transmission"], "policy_real_rates")
        self.assertEqual(overlaps["usd_transmission"], "macro_activity")
        self.assertEqual(overlaps["safe_haven_news_transmission"], "news_geopolitical")

    def test_falling_real_yields_support_a_bullish_gold_thesis(self):
        reading = self._evaluate(real_yield_mtf={"score": -0.5})
        self.assertIs(
            reading.driver("real_rate_transmission").state, TransmissionState.SUPPORTING
        )

    def test_rising_real_yields_conflict_with_a_bullish_gold_thesis(self):
        reading = self._evaluate(real_yield_mtf={"score": 0.5})
        self.assertIs(
            reading.driver("real_rate_transmission").state, TransmissionState.CONFLICTING
        )
        self.assertTrue(reading.conflicts_with_macro_thesis)

    def test_stronger_usd_conflicts_with_a_bullish_gold_thesis(self):
        reading = self._evaluate(usd_macro_score=0.5)
        self.assertIs(
            reading.driver("usd_transmission").state, TransmissionState.CONFLICTING
        )

    def test_sign_convention_matches_the_production_formula(self):
        # Production computes gold_ry = -real_yield_score and gold_usd = -usd_macro.
        values = modules.gold.driver_values(
            real_yield_mtf={"score": 0.3}, usd_macro_score=0.4
        )
        self.assertAlmostEqual(values["real_rate_transmission"], -0.3)
        self.assertAlmostEqual(values["usd_transmission"], -0.4)

    def test_missing_real_yield_is_unavailable_not_zero(self):
        reading = self._evaluate(real_yield_mtf=None)
        driver = reading.driver("real_rate_transmission")
        self.assertIs(driver.state, TransmissionState.UNAVAILABLE)
        self.assertIsNone(driver.value)
        self.assertIn("real_rate_transmission", reading.unavailable_drivers)

    def test_news_uses_whichever_legs_arrived(self):
        both = modules.gold.driver_values(gold_rule_points=0.4, gold_ai_points=0.2)
        rule_only = modules.gold.driver_values(gold_rule_points=0.4, gold_ai_points=None)
        neither = modules.gold.driver_values(gold_rule_points=None, gold_ai_points=None)
        self.assertAlmostEqual(both["safe_haven_news_transmission"], 0.3 / 0.5)
        self.assertAlmostEqual(rule_only["safe_haven_news_transmission"], 0.4 / 0.5)
        self.assertIsNone(neither["safe_haven_news_transmission"])

    def test_gold_dormant_evidence_is_declared_not_proxied(self):
        reading = self._evaluate()
        self.assertEqual(
            set(reading.dormant_drivers), {"official_sector_demand", "gold_etf_flows"}
        )
        for name in reading.dormant_drivers:
            self.assertIsNone(reading.driver(name))

    def test_gold_dormant_evidence_is_registered_globally(self):
        for key in ("gold_official_sector_demand", "gold_etf_flows"):
            self.assertIn(key, registry.dormant_keys())

    def test_mixed_transmission_is_reported_not_averaged_away(self):
        reading = self._evaluate(real_yield_mtf={"score": -0.5}, usd_macro_score=0.5)
        self.assertTrue(reading.conflicts_with_macro_thesis)
        states = {d.key: d.state for d in reading.drivers}
        self.assertIs(states["real_rate_transmission"], TransmissionState.SUPPORTING)
        self.assertIs(states["usd_transmission"], TransmissionState.CONFLICTING)

    def test_module_output_is_json_serialisable_and_fully_tagged(self):
        record = self._evaluate().as_record()
        json.dumps(record)
        self.assertEqual(record["asset_module"], "gold_module_v1")
        self.assertEqual(record["instrument"], "Gold")
        self.assertEqual(record["role"], "asset_specific_module")
        self.assertEqual(record["horizon"], "tactical")
        self.assertFalse(record["contributes_evidence"])
        self.assertIn("transmission_summary", record)
        for driver in record["drivers"]:
            self.assertFalse(driver["contributes_vote"])
            self.assertTrue(driver["rationale"].strip())

    def test_gold_is_the_registered_module_for_gold(self):
        self.assertIs(modules.module_for("Gold"), modules.gold)
        self.assertIsNone(modules.module_for("NOT_A_MARKET"))


# ---------------------------------------------------------------------------
# C2 -- Oil
# ---------------------------------------------------------------------------
class TestOilModule(unittest.TestCase):
    def _evaluate(self, **kwargs):
        params = dict(
            thesis_direction=Direction.BULLISH,
            oil_price_momentum=0.4,
            usd_macro_score=-0.3,
            oil_news_points=0.2,
        )
        params.update(kwargs)
        return modules.oil.evaluate(**params)

    def test_oil_declares_exactly_three_channels(self):
        self.assertEqual(
            [d.key for d in modules.oil.DRIVERS],
            [
                "price_trend_transmission",
                "usd_transmission",
                "supply_narrative_transmission",
            ],
        )

    def test_channels_map_onto_the_expected_families(self):
        overlaps = {d.key: d.universal_family_overlap for d in modules.oil.DRIVERS}
        self.assertEqual(overlaps["price_trend_transmission"], "directional")
        self.assertEqual(overlaps["usd_transmission"], "macro_activity")
        self.assertEqual(overlaps["supply_narrative_transmission"], "news_geopolitical")

    def test_price_trend_does_not_vote_twice_with_the_directional_family(self):
        driver = next(
            d for d in modules.oil.DRIVERS if d.key == "price_trend_transmission"
        )
        self.assertIs(driver.evidence_class, DriverEvidenceClass.TRANSFORMATION)
        self.assertEqual(driver.universal_family_overlap, "directional")

    def test_rising_crude_supports_a_bullish_oil_thesis(self):
        reading = self._evaluate(oil_price_momentum=0.5)
        self.assertIs(
            reading.driver("price_trend_transmission").state,
            TransmissionState.SUPPORTING,
        )

    def test_stronger_usd_conflicts_with_a_bullish_oil_thesis(self):
        reading = self._evaluate(usd_macro_score=0.5)
        self.assertIs(
            reading.driver("usd_transmission").state, TransmissionState.CONFLICTING
        )

    def test_sign_convention_matches_the_production_formula(self):
        # _calc_oil_score_only prices 0.40*momentum + 0.20*(-usd_macro) + 0.40*news.
        values = modules.oil.driver_values(
            oil_price_momentum=0.4, usd_macro_score=0.3, oil_news_points=0.25
        )
        self.assertAlmostEqual(values["price_trend_transmission"], 0.4)
        self.assertAlmostEqual(values["usd_transmission"], -0.3)
        self.assertAlmostEqual(values["supply_narrative_transmission"], 0.5)

    def test_missing_price_momentum_is_unavailable_not_zero(self):
        reading = self._evaluate(oil_price_momentum=None)
        driver = reading.driver("price_trend_transmission")
        self.assertIs(driver.state, TransmissionState.UNAVAILABLE)
        self.assertIsNone(driver.value)

    def test_physical_supply_data_is_dormant_not_proxied(self):
        reading = self._evaluate()
        self.assertEqual(
            set(reading.dormant_drivers),
            {
                "crude_inventories",
                "opec_production_quotas",
                "refinery_and_shipping",
                "crude_term_structure",
            },
        )
        for name in reading.dormant_drivers:
            self.assertIsNone(reading.driver(name))

    def test_the_supply_channel_is_labelled_a_narrative_not_a_balance(self):
        driver = next(
            d for d in modules.oil.DRIVERS if d.key == "supply_narrative_transmission"
        )
        self.assertIn("narrative", driver.label.lower())
        self.assertIn("not the balance itself", driver.rationale)

    def test_oil_dormant_evidence_is_registered_globally(self):
        for key in (
            "oil_inventories",
            "opec_supply",
            "crude_term_structure",
            "refinery_and_shipping",
        ):
            self.assertIn(key, registry.dormant_keys())

    def test_module_output_is_json_serialisable_and_fully_tagged(self):
        record = self._evaluate().as_record()
        json.dumps(record)
        self.assertEqual(record["asset_module"], "oil_module_v1")
        self.assertEqual(record["instrument"], "Oil")
        self.assertEqual(record["role"], "asset_specific_module")
        self.assertFalse(record["contributes_evidence"])

    def test_oil_is_the_registered_module_for_oil(self):
        self.assertIs(modules.module_for("Oil"), modules.oil)


# ---------------------------------------------------------------------------
# C3 -- FX
# ---------------------------------------------------------------------------
class TestFxModule(unittest.TestCase):
    def _evaluate(self, **kwargs):
        params = dict(
            thesis_direction=Direction.BULLISH,
            currency="EUR",
            domestic_macro_score=0.4,
            counter_macro_score=0.1,
            domestic_rate_score=0.3,
            counter_rate_score=0.0,
            domestic_news_points=0.2,
        )
        params.update(kwargs)
        return modules.fx.evaluate(**params)

    def test_fx_declares_exactly_three_channels(self):
        self.assertEqual(
            [d.key for d in modules.fx.DRIVERS],
            [
                "relative_macro_pressure",
                "relative_policy_pressure",
                "domestic_news_transmission",
            ],
        )

    def test_channels_map_onto_the_expected_families(self):
        overlaps = {d.key: d.universal_family_overlap for d in modules.fx.DRIVERS}
        self.assertEqual(overlaps["relative_macro_pressure"], "macro_activity")
        self.assertEqual(overlaps["relative_policy_pressure"], "policy_real_rates")
        self.assertEqual(overlaps["domestic_news_transmission"], "news_geopolitical")

    def test_fx_is_relative_not_isolated(self):
        values = modules.fx.driver_values(
            currency="EUR", domestic_macro_score=0.4, counter_macro_score=0.1
        )
        self.assertAlmostEqual(values["relative_macro_pressure"], 0.3)

    def test_a_currency_outperforming_its_counter_supports_a_bullish_thesis(self):
        reading = self._evaluate(domestic_macro_score=0.5, counter_macro_score=-0.2)
        self.assertIs(
            reading.driver("relative_macro_pressure").state,
            TransmissionState.SUPPORTING,
        )

    def test_a_currency_underperforming_its_counter_conflicts(self):
        reading = self._evaluate(domestic_macro_score=-0.2, counter_macro_score=0.5)
        self.assertIs(
            reading.driver("relative_macro_pressure").state,
            TransmissionState.CONFLICTING,
        )

    def test_equal_legs_are_neutral_not_unavailable(self):
        reading = self._evaluate(domestic_macro_score=0.3, counter_macro_score=0.3)
        driver = reading.driver("relative_macro_pressure")
        self.assertIs(driver.state, TransmissionState.NEUTRAL)
        self.assertTrue(driver.is_available)

    def test_a_missing_counter_leg_is_unavailable_not_neutral(self):
        reading = self._evaluate(counter_macro_score=None)
        driver = reading.driver("relative_macro_pressure")
        self.assertIs(driver.state, TransmissionState.UNAVAILABLE)
        self.assertIsNone(driver.value)

    def test_exactly_one_counter_currency_per_instrument(self):
        for currency in modules.fx.INSTRUMENTS:
            counter = modules.fx.counter_currency_for(currency)
            self.assertIsInstance(counter, (str, type(None)))
            if counter is not None:
                self.assertNotEqual(counter, currency)

    def test_usd_is_the_base_and_has_no_counter(self):
        self.assertIsNone(modules.fx.counter_currency_for("USD"))
        reading = self._evaluate(
            currency="USD", counter_macro_score=None, counter_rate_score=None
        )
        self.assertIs(
            reading.driver("relative_macro_pressure").state,
            TransmissionState.UNAVAILABLE,
        )
        self.assertIs(
            reading.driver("relative_policy_pressure").state,
            TransmissionState.UNAVAILABLE,
        )
        self.assertTrue(any("base currency" in n for n in reading.notes))

    def test_usd_still_reads_its_own_news_channel(self):
        reading = self._evaluate(
            currency="USD",
            counter_macro_score=None,
            counter_rate_score=None,
            domestic_news_points=0.3,
        )
        self.assertIs(
            reading.driver("domestic_news_transmission").state,
            TransmissionState.SUPPORTING,
        )

    def test_the_counter_currency_is_recorded_on_the_reading(self):
        reading = self._evaluate(currency="EUR")
        self.assertTrue(any("USD" in note for note in reading.notes))

    def test_only_one_comparison_is_made_per_evaluation(self):
        """The same domestic evidence must not surface in several comparisons."""
        reading = self._evaluate(currency="CHF")
        relative_drivers = [
            d for d in reading.drivers if d.key.startswith("relative_")
        ]
        self.assertEqual(len(relative_drivers), 2)  # one macro, one policy -- not per counter
        self.assertTrue(any("One comparison only" in n for n in reading.notes))

    def test_news_is_the_domestic_leg_only(self):
        driver = next(
            d for d in modules.fx.DRIVERS if d.key == "domestic_news_transmission"
        )
        self.assertIn("domestic leg only", driver.rationale)

    def test_jpy_substitution_is_recorded_not_hidden(self):
        reading = self._evaluate(
            currency="JPY",
            counter_rate_substitution="Counter rate leg uses US 10Y yield momentum (DGS10).",
        )
        self.assertTrue(any("DGS10" in note for note in reading.notes))

    def test_fx_dormant_evidence_is_declared(self):
        reading = self._evaluate()
        self.assertEqual(
            set(reading.dormant_drivers),
            {"fx_positioning_crowding", "central_bank_intervention", "cross_currency_basis"},
        )

    def test_positioning_remains_globally_dormant(self):
        self.assertIn("positioning_crowding", registry.dormant_keys())

    def test_module_output_is_json_serialisable_and_fully_tagged(self):
        record = self._evaluate().as_record()
        json.dumps(record)
        self.assertEqual(record["asset_module"], "fx_module_v1")
        self.assertEqual(record["instrument"], "EUR")
        self.assertFalse(record["contributes_evidence"])
        self.assertTrue(record["notes"])

    def test_every_configured_currency_resolves_to_the_fx_module(self):
        for currency in modules.fx.INSTRUMENTS:
            self.assertIs(modules.module_for(currency), modules.fx)


# ---------------------------------------------------------------------------
# C4 -- Nasdaq
# ---------------------------------------------------------------------------
class TestNasdaqModule(unittest.TestCase):
    def _evaluate(self, **kwargs):
        params = dict(
            thesis_direction=Direction.BULLISH,
            real_yield_mtf={"score": -0.4},
            usd_macro_score=-0.3,
            nasdaq_news_points=0.2,
        )
        params.update(kwargs)
        return modules.nasdaq.evaluate(**params)

    def test_nasdaq_declares_exactly_three_channels(self):
        self.assertEqual(
            [d.key for d in modules.nasdaq.DRIVERS],
            [
                "real_yield_sensitivity",
                "usd_financial_conditions",
                "growth_risk_news_transmission",
            ],
        )

    def test_channels_map_onto_the_expected_families(self):
        overlaps = {d.key: d.universal_family_overlap for d in modules.nasdaq.DRIVERS}
        self.assertEqual(overlaps["real_yield_sensitivity"], "policy_real_rates")
        self.assertEqual(overlaps["usd_financial_conditions"], "macro_activity")
        self.assertEqual(overlaps["growth_risk_news_transmission"], "news_geopolitical")

    def test_real_yields_do_not_receive_a_second_independent_vote(self):
        driver = next(
            d for d in modules.nasdaq.DRIVERS if d.key == "real_yield_sensitivity"
        )
        self.assertIs(driver.evidence_class, DriverEvidenceClass.TRANSFORMATION)
        self.assertEqual(driver.universal_family_overlap, "policy_real_rates")

    def test_price_trend_is_not_a_nasdaq_channel(self):
        keys = {d.key for d in modules.nasdaq.DRIVERS}
        self.assertNotIn("price_trend_transmission", keys)
        for driver in modules.nasdaq.DRIVERS:
            self.assertNotEqual(driver.universal_family_overlap, "directional")

    def test_the_price_trend_omission_is_recorded_as_a_decision(self):
        self.assertIn("price_trend", modules.nasdaq.COVERED_ELSEWHERE)
        reading = self._evaluate()
        self.assertTrue(any("Directional" in note for note in reading.notes))

    def test_rising_real_yields_conflict_with_a_bullish_nasdaq_thesis(self):
        reading = self._evaluate(real_yield_mtf={"score": 0.5})
        self.assertIs(
            reading.driver("real_yield_sensitivity").state,
            TransmissionState.CONFLICTING,
        )

    def test_falling_real_yields_support_a_bullish_nasdaq_thesis(self):
        reading = self._evaluate(real_yield_mtf={"score": -0.5})
        self.assertIs(
            reading.driver("real_yield_sensitivity").state,
            TransmissionState.SUPPORTING,
        )

    def test_sign_convention_matches_the_production_formula(self):
        # _calc_ndx_score_only prices 0.20*(-real_yield) + 0.15*(-usd_macro) + news.
        values = modules.nasdaq.driver_values(
            real_yield_mtf={"score": 0.3}, usd_macro_score=0.4, nasdaq_news_points=0.25
        )
        self.assertAlmostEqual(values["real_yield_sensitivity"], -0.3)
        self.assertAlmostEqual(values["usd_financial_conditions"], -0.4)
        self.assertAlmostEqual(values["growth_risk_news_transmission"], 0.5)

    def test_missing_real_yield_is_unavailable_not_zero(self):
        reading = self._evaluate(real_yield_mtf=None)
        self.assertIs(
            reading.driver("real_yield_sensitivity").state,
            TransmissionState.UNAVAILABLE,
        )

    def test_earnings_data_is_dormant_not_inferred_from_news(self):
        reading = self._evaluate()
        self.assertIn("earnings_revisions", reading.dormant_drivers)
        news_driver = next(
            d for d in modules.nasdaq.DRIVERS if d.key == "growth_risk_news_transmission"
        )
        self.assertIn("not an earnings dataset", news_driver.rationale)

    def test_nasdaq_dormant_evidence_is_registered_globally(self):
        for key in (
            "nasdaq_earnings_revisions",
            "equity_liquidity_flows",
            "index_breadth_concentration",
        ):
            self.assertIn(key, registry.dormant_keys())

    def test_module_output_is_json_serialisable_and_fully_tagged(self):
        record = self._evaluate().as_record()
        json.dumps(record)
        self.assertEqual(record["asset_module"], "nasdaq_module_v1")
        self.assertEqual(record["instrument"], "NDX")
        self.assertFalse(record["contributes_evidence"])

    def test_nasdaq_is_the_registered_module_for_ndx(self):
        self.assertIs(modules.module_for("NDX"), modules.nasdaq)


class TestAllFourModulesRegistered(unittest.TestCase):
    def test_all_four_stage_c_modules_exist(self):
        self.assertEqual(
            set(registry.asset_module_keys()),
            {"gold_module_v1", "oil_module_v1", "fx_module_v1", "nasdaq_module_v1"},
        )

    def test_every_registered_instrument_resolves(self):
        for instrument in modules.registered_instruments():
            self.assertIsNotNone(modules.module_for(instrument))

    def test_no_module_exceeds_three_drivers(self):
        for instrument, module in modules.MODULES.items():
            self.assertLessEqual(len(module.DRIVERS), 3, instrument)

    def test_every_module_declares_dormant_evidence(self):
        for instrument, module in modules.MODULES.items():
            self.assertTrue(module.DORMANT_DRIVERS, instrument)

    def test_no_module_anywhere_claims_independent_evidence(self):
        for module in modules.MODULES.values():
            for driver in module.DRIVERS:
                self.assertIsNot(
                    driver.evidence_class,
                    DriverEvidenceClass.INDEPENDENT_ASSET_SPECIFIC,
                )


# ---------------------------------------------------------------------------
# Event timing -- true minutes, never fabricated
# ---------------------------------------------------------------------------
class TestEventTiming(unittest.TestCase):
    def _event(self, minutes, impact="High", currency="USD", title="CPI"):
        release = NOW + timedelta(minutes=minutes)
        return {
            "title": title,
            "country": currency,
            "impact": impact,
            "date": release.isoformat(),
        }

    def test_true_minutes_come_from_the_calendar_timestamp(self):
        timing = event_timing.minutes_to_nearest_event(
            [self._event(37)], {"USD"}, NOW
        )
        self.assertAlmostEqual(timing.minutes, 37.0, places=2)
        self.assertEqual(timing.source, "calendar_timestamp")
        self.assertTrue(timing.is_available)

    def test_the_nearest_event_wins(self):
        timing = event_timing.minutes_to_nearest_event(
            [self._event(300, title="Far"), self._event(20, title="Near")], {"USD"}, NOW
        )
        self.assertEqual(timing.title, "Near")

    def test_a_just_released_event_reads_negative(self):
        timing = event_timing.minutes_to_nearest_event(
            [self._event(-8)], {"USD"}, NOW
        )
        self.assertLess(timing.minutes, 0)

    def test_only_high_impact_events_are_considered(self):
        timing = event_timing.minutes_to_nearest_event(
            [self._event(10, impact="Low"), self._event(90, impact="High")], {"USD"}, NOW
        )
        self.assertAlmostEqual(timing.minutes, 90.0, places=2)

    def test_only_relevant_currencies_are_considered(self):
        timing = event_timing.minutes_to_nearest_event(
            [self._event(10, currency="JPY"), self._event(90, currency="USD")],
            {"USD"},
            NOW,
        )
        self.assertAlmostEqual(timing.minutes, 90.0, places=2)

    def test_no_calendar_is_unavailable_not_clear(self):
        timing = event_timing.minutes_to_nearest_event([], {"USD"}, NOW)
        self.assertIsNone(timing.minutes)
        self.assertEqual(timing.source, "unavailable")
        self.assertFalse(timing.is_available)

    def test_unparseable_timestamps_are_reported_unavailable(self):
        broken = {"title": "X", "country": "USD", "impact": "High", "date": "not-a-date"}
        timing = event_timing.minutes_to_nearest_event([broken], {"USD"}, NOW)
        self.assertIsNone(timing.minutes)
        self.assertEqual(timing.source, "unavailable")
        self.assertIn("unparseable", timing.reason)

    def test_nothing_in_window_is_a_measured_absence_not_unavailable(self):
        timing = event_timing.minutes_to_nearest_event(
            [self._event(5000)], {"USD"}, NOW
        )
        self.assertIsNone(timing.minutes)
        self.assertEqual(timing.source, "calendar_timestamp")
        self.assertIn("No relevant high-impact event", timing.reason)

    def test_timing_provenance_is_recorded(self):
        record = event_timing.minutes_to_nearest_event(
            [self._event(45)], {"USD"}, NOW
        ).as_record()
        json.dumps(record)
        self.assertEqual(record["event_timing_source"], "calendar_timestamp")
        self.assertIn("minutes_to_event", record)

    def test_timing_module_performs_no_io(self):
        tree = ast.parse(inspect.getsource(event_timing))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module or "")
        self.assertFalse(imported & {"requests", "urllib", "threading", "os"})


# ---------------------------------------------------------------------------
# Circularity
# ---------------------------------------------------------------------------
class TestNoCircularConfirmation(unittest.TestCase):
    WITHHELD_CALLS = (
        "compute_cross_asset_confirmation",
        "compute_macro_regime_context",
        "compute_relative_value",
        "compute_recent_macro_surprise",
    )

    def _module_sources(self):
        sources = [inspect.getsource(base), inspect.getsource(modules)]
        sources.extend(inspect.getsource(m) for m in modules.MODULES.values())
        return "\n".join(sources)

    def test_no_module_calls_withheld_cross_asset_logic(self):
        source = self._module_sources()
        for name in self.WITHHELD_CALLS:
            self.assertNotIn(f"{name}(", source, name)

    def test_cross_asset_remains_withheld(self):
        self.assertIn("cross_asset_bridge", registry.withheld_keys())

    def test_modules_import_nothing_from_production(self):
        for module in (base, modules, *modules.MODULES.values()):
            tree = ast.parse(inspect.getsource(module))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    self.assertNotIn("production_core", node.module or "")
                    self.assertNotIn("b2_bridge", node.module or "")
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertNotIn("production_core", alias.name)

    def test_a_driver_does_not_confirm_the_thesis_it_helped_build(self):
        """Transmission is a diagnostic label, not a confirmation count.

        Every Gold driver restates an input that already voted. The module must
        therefore report transmission state without ever adding to the evidence
        that produced the thesis direction.
        """
        reading = modules.gold.evaluate(
            thesis_direction=Direction.BULLISH,
            real_yield_mtf={"score": -0.9},
            usd_macro_score=-0.9,
            gold_rule_points=0.5,
            gold_ai_points=0.5,
        )
        self.assertIs(reading.transmission_summary, TransmissionState.SUPPORTING)
        self.assertFalse(reading.contributes_evidence)
        self.assertTrue(all(not d.contributes_vote for d in reading.drivers))


if __name__ == "__main__":
    unittest.main()
