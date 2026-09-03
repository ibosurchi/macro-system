"""Stage B tests for Architecture B2.

Imports only ``apex.b2``, which performs no I/O, so this module writes no
durable state. The bridge that does touch persistence is exercised through an
in-memory store, never against the real backend.
"""
from __future__ import annotations

import json
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from apex.b2 import (
    confidence as confidence_mod,
    evaluate as evaluate_mod,
    horizons,
    predictions,
    regime as regime_mod,
    registry,
    scenarios,
    shadow,
    thesis as thesis_mod,
)
from apex.b2 import families, risk
from apex.b2.enums import (
    ConfidenceLevel,
    DecisionState,
    Direction,
    Horizon,
    ThesisState,
)

NOW = datetime(2026, 8, 30, 12, 0, 0, tzinfo=timezone.utc)


def _uniform(definition, value):
    return {member: value for member in definition.members}


def _signals(policy=None, activity=None, news=None, directional=None, structure=None):
    def spec(definition, value):
        if value is None:
            return {}
        if isinstance(value, dict):
            return value
        return _uniform(definition, value)

    return {
        "policy_real_rates": spec(registry.POLICY_REAL_RATES, policy),
        "macro_activity": spec(registry.MACRO_ACTIVITY, activity),
        "news_geopolitical": spec(registry.NEWS_GEOPOLITICAL, news),
        "directional": spec(registry.DIRECTIONAL, directional),
        "structure": spec(registry.STRUCTURE, structure),
    }


def _readings(**kwargs):
    return families.evaluate_families(registry.VOTING_FAMILIES, _signals(**kwargs))


def _evaluate(**overrides):
    kwargs = dict(
        instrument="XAUUSD",
        decision_horizon=Horizon.TACTICAL,
        signals_by_family=_signals(
            policy=0.5, activity=0.5, news=0.5, directional=0.5, structure=0.5
        ),
        invalidation_level=95.0,
        entry_zone=(99.0, 101.0),
        current_price=100.0,
        atr=1.0,
        atr_ratio=1.0,
        asymmetry_ratio=2.5,
        volatility_regime="normal",
        evaluated_at=NOW,
    )
    kwargs.update(overrides)
    return evaluate_mod.run_shadow_evaluation(**kwargs)


# ---------------------------------------------------------------------------
# 1. Horizons
# ---------------------------------------------------------------------------
class TestHorizons(unittest.TestCase):
    def test_three_horizons_exist_and_are_distinct(self):
        self.assertEqual(
            {h.value for h in Horizon}, {"structural", "tactical", "execution"}
        )

    def test_staleness_is_judged_against_the_series_own_frequency(self):
        three_weeks_ago = NOW - timedelta(days=21)
        monthly = horizons.classify_staleness(
            three_weeks_ago, horizons.SeriesFrequency.MONTHLY, NOW
        )
        daily = horizons.classify_staleness(
            three_weeks_ago, horizons.SeriesFrequency.DAILY, NOW
        )
        self.assertIs(monthly, horizons.Staleness.FRESH)
        self.assertIs(daily, horizons.Staleness.BROKEN)

    def test_missing_timestamp_is_unknown_not_fresh(self):
        self.assertIs(
            horizons.classify_staleness(None, horizons.SeriesFrequency.DAILY, NOW),
            horizons.Staleness.UNKNOWN,
        )

    def test_broken_and_unknown_are_not_usable(self):
        self.assertFalse(horizons.is_usable(horizons.Staleness.BROKEN))
        self.assertFalse(horizons.is_usable(horizons.Staleness.UNKNOWN))
        self.assertTrue(horizons.is_usable(horizons.Staleness.EXPECTED))

    def test_quarterly_data_cannot_inform_an_execution_decision(self):
        with self.assertRaises(ValueError):
            horizons.assert_horizon_compatible(
                horizons.SeriesFrequency.QUARTERLY, Horizon.EXECUTION
            )
        with self.assertRaises(ValueError):
            horizons.assert_horizon_compatible(
                horizons.SeriesFrequency.MONTHLY, Horizon.EXECUTION
            )
        horizons.assert_horizon_compatible(
            horizons.SeriesFrequency.DAILY, Horizon.EXECUTION
        )

    def test_structural_data_may_inform_the_structural_horizon(self):
        self.assertTrue(
            horizons.horizon_compatible(
                horizons.SeriesFrequency.QUARTERLY, Horizon.STRUCTURAL
            )
        )

    def test_incompatible_frequency_yields_unavailable_not_zero(self):
        observation = horizons.SeriesObservation(
            key="gdp",
            value=0.8,
            frequency=horizons.SeriesFrequency.QUARTERLY,
            observed_at=NOW,
        )
        self.assertIsNone(observation.usable_value(Horizon.EXECUTION, NOW))
        self.assertEqual(observation.usable_value(Horizon.STRUCTURAL, NOW), 0.8)

    def test_broken_series_yields_unavailable_not_zero(self):
        observation = horizons.SeriesObservation(
            key="dgs10",
            value=0.4,
            frequency=horizons.SeriesFrequency.DAILY,
            observed_at=NOW - timedelta(days=60),
        )
        self.assertIsNone(observation.usable_value(Horizon.EXECUTION, NOW))

    def test_each_horizon_has_its_own_evaluation_deadline(self):
        deadlines = {
            h: horizons.evaluation_deadline(h, NOW) for h in Horizon
        }
        self.assertLess(deadlines[Horizon.EXECUTION], deadlines[Horizon.TACTICAL])
        self.assertLess(deadlines[Horizon.TACTICAL], deadlines[Horizon.STRUCTURAL])

    def test_claims_record_horizon_direction_and_timestamps(self):
        claim = horizons.build_claim(
            horizon=Horizon.TACTICAL,
            direction=Direction.BULLISH,
            claim="test",
            registered_at=NOW,
        )
        record = claim.as_record()
        self.assertEqual(record["horizon"], "tactical")
        self.assertEqual(record["direction"], "bullish")
        self.assertEqual(record["registered_at"], NOW.isoformat())
        self.assertEqual(
            record["evaluate_at"],
            horizons.evaluation_deadline(Horizon.TACTICAL, NOW).isoformat(),
        )


# ---------------------------------------------------------------------------
# 2 & 3. Scenarios and pre-registered invalidation conditions
# ---------------------------------------------------------------------------
class TestScenarios(unittest.TestCase):
    def setUp(self):
        self.readings = _readings(
            policy=0.5, activity=0.5, news=-0.5, directional=0.5, structure=0.5
        )
        self.set = scenarios.build_scenario_set(
            direction=Direction.BULLISH,
            readings=self.readings,
            horizon=Horizon.TACTICAL,
            registered_at=NOW,
        )

    def test_base_alternative_and_tail_are_all_present(self):
        kinds = {s.kind for s in self.set.scenarios}
        self.assertEqual(
            kinds,
            {scenarios.ScenarioKind.BASE, scenarios.ScenarioKind.ALTERNATIVE, scenarios.ScenarioKind.TAIL},
        )

    def test_scenarios_carry_no_evidence_value_and_no_score(self):
        for scenario in self.set.scenarios:
            fields = set(vars(scenario))
            self.assertNotIn("score", fields)
            self.assertNotIn("evidence", fields)
            self.assertNotIn("weight", fields)

    def test_probabilities_are_categorical_bands_not_numbers(self):
        for scenario in self.set.scenarios:
            self.assertIsInstance(scenario.band, scenarios.ProbabilityBand)
            self.assertIn(scenario.band.value, {"likely", "possible", "unlikely"})

    def test_conditions_are_registered_with_a_timestamp_before_any_outcome(self):
        self.assertTrue(self.set.conditions)
        for condition in self.set.conditions:
            self.assertEqual(condition.registered_at, NOW)
            self.assertTrue(condition.observable.strip())
            self.assertTrue(condition.description.strip())

    def test_every_supporting_family_gets_a_falsifiable_condition(self):
        supporting = {"policy_real_rates", "macro_activity", "directional", "structure"}
        observables = {c.observable for c in self.set.conditions}
        for key in supporting:
            self.assertIn(f"family:{key}.state == CONFLICTS", observables)

    def test_unavailable_family_gets_a_restore_condition(self):
        readings = _readings(policy=0.5, activity=0.5, news=None)
        built = scenarios.build_scenario_set(
            direction=Direction.BULLISH,
            readings=readings,
            horizon=Horizon.TACTICAL,
            registered_at=NOW,
        )
        observables = {c.observable for c in built.conditions}
        self.assertIn("family:news_geopolitical.available == True", observables)

    def test_tail_case_asserts_no_direction(self):
        self.assertIs(self.set.tail.direction, Direction.FLAT)

    def test_conditions_are_evaluated_against_current_readings(self):
        triggered = scenarios.evaluate_conditions(
            self.set, self.readings, Direction.BULLISH, regime_is_stress=False
        )
        observables = {c.observable for c in triggered}
        # news conflicts, so its "resolve" condition has not fired...
        self.assertNotIn("family:news_geopolitical.state == SUPPORTS", observables)
        # ...but no supporting family has flipped either.
        self.assertNotIn("family:policy_real_rates.state == CONFLICTS", observables)

    def test_a_flipped_family_triggers_its_registered_condition(self):
        flipped = _readings(
            policy=-0.5, activity=0.5, news=-0.5, directional=0.5, structure=0.5
        )
        triggered = scenarios.evaluate_conditions(self.set, flipped, Direction.BULLISH)
        self.assertIn(
            "family:policy_real_rates.state == CONFLICTS",
            {c.observable for c in triggered},
        )

    def test_scenario_set_is_not_accepted_by_the_aggregator(self):
        import inspect

        from apex.b2 import aggregation

        source = inspect.getsource(aggregation)
        self.assertNotIn("ScenarioSet", source)
        self.assertNotIn("scenarios", source)


# ---------------------------------------------------------------------------
# 4. Transmission prediction log
# ---------------------------------------------------------------------------
def _steps():
    return (
        predictions.TransmissionStep(
            index=0,
            source="policy_shock",
            target="yields",
            expected_direction=Direction.BULLISH,
            expects_within=timedelta(hours=6),
        ),
        predictions.TransmissionStep(
            index=1,
            source="yields",
            target="usd",
            expected_direction=Direction.BULLISH,
            expects_within=timedelta(hours=24),
        ),
        predictions.TransmissionStep(
            index=2,
            source="usd",
            target="gold",
            expected_direction=Direction.BEARISH,
            expects_within=timedelta(hours=48),
        ),
    )


class TestPredictionLog(unittest.TestCase):
    def setUp(self):
        self.log = predictions.PredictionLog()
        self.record = predictions.build_prediction(
            horizon=Horizon.TACTICAL,
            thesis_direction=Direction.BEARISH,
            instrument="XAUUSD",
            steps=_steps(),
            created_at=NOW,
        )
        self.log.append(self.record)

    def test_prediction_records_the_expected_chain_with_windows(self):
        self.assertEqual(len(self.record.steps), 3)
        self.assertEqual(self.record.steps[0].source, "policy_shock")
        self.assertEqual(self.record.steps[2].target, "gold")
        for step in self.record.steps:
            self.assertGreater(step.expects_within.total_seconds(), 0)

    def test_prediction_is_timestamped_and_has_an_evaluation_deadline(self):
        self.assertEqual(self.record.created_at, NOW)
        self.assertGreater(self.record.evaluate_at, self.record.created_at)

    def test_predictions_are_immutable(self):
        with self.assertRaises(Exception):
            self.record.thesis_direction = Direction.BULLISH  # type: ignore[misc]

    def test_log_is_append_only(self):
        with self.assertRaises(predictions.PredictionLogError):
            self.log.append(self.record)

    def test_outcome_cannot_predate_its_prediction(self):
        with self.assertRaises(predictions.PredictionLogError):
            self.log.attach_outcome(
                record_id=self.record.record_id,
                step_index=0,
                state=predictions.StepOutcomeState.CONFIRMED,
                observed_at=NOW - timedelta(hours=1),
            )

    def test_a_resolved_step_cannot_be_rescored(self):
        self.log.attach_outcome(
            record_id=self.record.record_id,
            step_index=0,
            state=predictions.StepOutcomeState.CONFIRMED,
            observed_at=NOW + timedelta(hours=2),
        )
        with self.assertRaises(predictions.PredictionLogError):
            self.log.attach_outcome(
                record_id=self.record.record_id,
                step_index=0,
                state=predictions.StepOutcomeState.CONTRADICTED,
                observed_at=NOW + timedelta(hours=3),
            )

    def test_outcomes_are_stored_separately_from_predictions(self):
        self.log.attach_outcome(
            record_id=self.record.record_id,
            step_index=0,
            state=predictions.StepOutcomeState.CONFIRMED,
            observed_at=NOW + timedelta(hours=2),
        )
        payload = self.log.as_record()
        self.assertIn("predictions", payload)
        self.assertIn("outcomes", payload)
        serialised = json.dumps(payload["predictions"])
        self.assertNotIn("confirmed", serialised)

    def test_unknown_record_or_step_is_rejected(self):
        with self.assertRaises(predictions.PredictionLogError):
            self.log.attach_outcome(
                record_id="nope",
                step_index=0,
                state=predictions.StepOutcomeState.CONFIRMED,
                observed_at=NOW + timedelta(hours=1),
            )
        with self.assertRaises(predictions.PredictionLogError):
            self.log.attach_outcome(
                record_id=self.record.record_id,
                step_index=99,
                state=predictions.StepOutcomeState.CONFIRMED,
                observed_at=NOW + timedelta(hours=1),
            )

    def test_pending_steps_appear_only_once_their_window_elapses(self):
        self.assertEqual(self.log.pending_steps(NOW), ())
        due = self.log.pending_steps(NOW + timedelta(hours=7))
        self.assertEqual(due, ((self.record.record_id, 0),))

    def test_unavailable_steps_are_excluded_from_the_confirmation_rate(self):
        self.log.attach_outcome(
            record_id=self.record.record_id, step_index=0,
            state=predictions.StepOutcomeState.CONFIRMED, observed_at=NOW + timedelta(hours=7),
        )
        self.log.attach_outcome(
            record_id=self.record.record_id, step_index=1,
            state=predictions.StepOutcomeState.UNAVAILABLE, observed_at=NOW + timedelta(hours=25),
        )
        rate, n = self.log.confirmation_rate()
        self.assertEqual(n, 1)
        self.assertEqual(rate, 1.0)

    def test_no_measurable_outcome_yields_none_not_zero(self):
        rate, n = predictions.PredictionLog().confirmation_rate()
        self.assertIsNone(rate)
        self.assertEqual(n, 0)

    def test_round_trip_serialisation_preserves_the_log(self):
        self.log.attach_outcome(
            record_id=self.record.record_id, step_index=1,
            state=predictions.StepOutcomeState.CONTRADICTED,
            observed_at=NOW + timedelta(hours=25),
        )
        restored = predictions.PredictionLog.from_record(
            json.loads(json.dumps(self.log.as_record()))
        )
        self.assertEqual(len(restored.records), 1)
        self.assertIs(
            restored.state_of(self.record.record_id, 1),
            predictions.StepOutcomeState.CONTRADICTED,
        )
        self.assertIs(
            restored.state_of(self.record.record_id, 2),
            predictions.StepOutcomeState.PENDING,
        )

    def test_confirmed_propagation_feeds_regime_confidence_only(self):
        import inspect

        from apex.b2 import aggregation, decision

        # The prediction log must not reach the aggregator or the decision layer.
        self.assertNotIn("prediction", inspect.getsource(aggregation).lower())
        self.assertNotIn("transmission", inspect.getsource(decision).lower())
        # Its only consumer is the regime classifier.
        self.assertIn("transmission_rate", inspect.getsource(regime_mod))

    def test_transmission_failure_lowers_regime_confidence_only(self):
        readings = _readings(directional=0.6, structure=0.6)
        healthy = regime_mod.classify_regime(
            volatility_regime="normal", readings=readings,
            candidate_direction=Direction.BULLISH,
            transmission_rate=0.9, transmission_sample=10,
            observed_at=NOW, technical_keys=registry.TECHNICAL_FAMILY_KEYS,
        )
        failing = regime_mod.classify_regime(
            volatility_regime="normal", readings=readings,
            candidate_direction=Direction.BULLISH,
            transmission_rate=0.1, transmission_sample=10,
            observed_at=NOW, technical_keys=registry.TECHNICAL_FAMILY_KEYS,
        )
        self.assertIs(healthy.state, failing.state)
        self.assertLess(failing.confidence.value, healthy.confidence.value)

    def test_transmission_cannot_raise_confidence(self):
        readings = _readings(directional=0.6)
        low_inputs = regime_mod.classify_regime(
            volatility_regime="unavailable", readings=readings,
            candidate_direction=Direction.BULLISH,
            transmission_rate=1.0, transmission_sample=500,
            observed_at=NOW, technical_keys=registry.TECHNICAL_FAMILY_KEYS,
        )
        self.assertIs(low_inputs.confidence, ConfidenceLevel.LOW)


# ---------------------------------------------------------------------------
# 9. Regime as meta-state
# ---------------------------------------------------------------------------
class TestRegimeMetaState(unittest.TestCase):
    def test_regime_carries_no_direction(self):
        reading = regime_mod.classify_regime(
            volatility_regime="normal",
            readings=_readings(directional=0.6, structure=0.6),
            technical_keys=registry.TECHNICAL_FAMILY_KEYS,
            observed_at=NOW,
        )
        self.assertNotIn("direction", set(vars(reading)))

    def test_state_and_confidence_are_separate_values(self):
        reading = regime_mod.classify_regime(
            volatility_regime="normal",
            readings=_readings(directional=0.6, structure=0.6),
            candidate_direction=Direction.BULLISH,
            technical_keys=registry.TECHNICAL_FAMILY_KEYS,
            observed_at=NOW,
        )
        self.assertIsInstance(reading.state, regime_mod.RegimeState)
        self.assertIsInstance(reading.confidence, ConfidenceLevel)

    def test_regime_space_is_small(self):
        self.assertLessEqual(
            len([s for s in regime_mod.RegimeState if s is not regime_mod.RegimeState.UNAVAILABLE]),
            3,
        )

    def test_no_inputs_is_unavailable_not_range(self):
        reading = regime_mod.classify_regime(
            volatility_regime="unavailable",
            readings=_readings(),
            technical_keys=registry.TECHNICAL_FAMILY_KEYS,
            observed_at=NOW,
        )
        self.assertIs(reading.state, regime_mod.RegimeState.UNAVAILABLE)
        self.assertIsNot(reading.state, regime_mod.RegimeState.RANGE)

    def test_stress_requires_expansion_and_disagreement(self):
        conflicted = _readings(
            policy=0.6, activity=0.6, news=-0.6, directional=0.6, structure=0.6
        )
        stressed = regime_mod.classify_regime(
            volatility_regime="expansion", readings=conflicted,
            candidate_direction=Direction.BULLISH,
            technical_keys=registry.TECHNICAL_FAMILY_KEYS, observed_at=NOW,
        )
        self.assertIs(stressed.state, regime_mod.RegimeState.STRESS)

    def test_expansion_alone_is_not_stress_and_not_bearish(self):
        agreed = _readings(
            policy=0.6, activity=0.6, news=0.6, directional=0.6, structure=0.6
        )
        reading = regime_mod.classify_regime(
            volatility_regime="expansion", readings=agreed,
            candidate_direction=Direction.BULLISH,
            technical_keys=registry.TECHNICAL_FAMILY_KEYS, observed_at=NOW,
        )
        self.assertIsNot(reading.state, regime_mod.RegimeState.STRESS)

    def test_regime_is_computed_only_from_supplied_current_values(self):
        import inspect

        # Causal by construction: every parameter is a current-timestamp value.
        # A parameter naming a series, a history or a future window would be an
        # opening for full-sample labelling, the subtlest leakage vector here.
        signature = inspect.signature(regime_mod.classify_regime)
        for name in signature.parameters:
            for forbidden in ("history", "series", "future", "lookahead", "full_sample"):
                self.assertNotIn(forbidden, name.lower(), name)

    def test_regime_classification_is_deterministic_for_one_snapshot(self):
        readings = _readings(directional=0.6, structure=0.6)
        first = regime_mod.classify_regime(
            volatility_regime="normal", readings=readings,
            candidate_direction=Direction.BULLISH,
            technical_keys=registry.TECHNICAL_FAMILY_KEYS, observed_at=NOW,
        )
        second = regime_mod.classify_regime(
            volatility_regime="normal", readings=readings,
            candidate_direction=Direction.BULLISH,
            technical_keys=registry.TECHNICAL_FAMILY_KEYS, observed_at=NOW,
        )
        self.assertEqual(first.as_record(), second.as_record())


# ---------------------------------------------------------------------------
# 6. Five separate confidence dimensions
# ---------------------------------------------------------------------------
class TestConfidenceDimensions(unittest.TestCase):
    def _assemble(self, readings, **kwargs):
        params = dict(
            readings=readings,
            candidate=Direction.BULLISH,
            macro_keys=registry.MACRO_FAMILY_KEYS,
            technical_keys=registry.TECHNICAL_FAMILY_KEYS,
            critical_family_keys=registry.CRITICAL_FAMILY_KEYS,
            execution_confidence=ConfidenceLevel.HIGH,
            regime_confidence=ConfidenceLevel.HIGH,
        )
        params.update(kwargs)
        return confidence_mod.assemble_confidence(**params)

    def test_all_five_dimensions_are_present_and_separate(self):
        result = self._assemble(
            _readings(policy=0.5, activity=0.5, news=0.5, directional=0.5, structure=0.5)
        )
        record = result.as_record()
        for key in (
            "macro_confidence",
            "technical_confidence",
            "execution_confidence",
            "regime_confidence",
            "data_confidence",
        ):
            self.assertIn(key, record)

    def test_there_is_no_way_to_average_them(self):
        result = self._assemble(_readings(policy=0.5, activity=0.5, news=0.5))
        for forbidden in ("average", "mean", "overall", "combined", "total", "score"):
            self.assertFalse(
                hasattr(result, forbidden), f"ConfidenceSet exposes {forbidden}"
            )

    def test_no_numeric_percentage_is_emitted(self):
        result = self._assemble(_readings(policy=0.5, activity=0.5, news=0.5))
        for key, value in result.as_record().items():
            if key.endswith("_confidence"):
                self.assertIn(value, {"LOW", "MODERATE", "HIGH"})

    def test_confidence_is_built_from_family_counts_not_weighted_averages(self):
        # All five families available throughout; only the number of macro
        # families that AGREE varies. Flat families are Neutral, not missing.
        one = self._assemble(
            _readings(policy=0.5, activity=0.0, news=0.0, directional=0.5, structure=0.5)
        )
        two = self._assemble(
            _readings(policy=0.5, activity=0.5, news=0.0, directional=0.5, structure=0.5)
        )
        three = self._assemble(
            _readings(policy=0.5, activity=0.5, news=0.5, directional=0.5, structure=0.5)
        )
        self.assertIs(one.macro, ConfidenceLevel.LOW)
        self.assertIs(two.macro, ConfidenceLevel.MODERATE)
        self.assertIs(three.macro, ConfidenceLevel.HIGH)

    def test_many_agreeing_members_in_one_family_do_not_raise_confidence(self):
        single_member = self._assemble(
            _readings(
                policy={"policy_rate_momentum": 0.6},
                activity=0.0, news=0.0, directional=0.0, structure=0.0,
            )
        )
        all_members = self._assemble(
            _readings(policy=0.6, activity=0.0, news=0.0, directional=0.0, structure=0.0)
        )
        # Four agreeing members instead of one changes the family's strength,
        # never the confidence, which counts families rather than indicators.
        self.assertIs(single_member.macro, all_members.macro)

    def test_disagreement_is_reported_not_absorbed(self):
        result = self._assemble(
            _readings(policy=0.5, activity=0.5, news=-0.5, directional=0.5, structure=0.5)
        )
        self.assertTrue(result.has_disagreement)
        self.assertIn("news_geopolitical", result.disagreements)
        self.assertIn("news_geopolitical", result.as_record()["disagreements"])

    def test_missing_data_lowers_data_confidence_only(self):
        full = self._assemble(
            _readings(policy=0.5, activity=0.5, news=0.5, directional=0.5, structure=0.5)
        )
        partial = self._assemble(
            _readings(policy=0.5, activity=0.5, news=None, directional=0.5, structure=0.5)
        )
        # SUPERSEDED BY H3. This previously asserted HIGH for a complete set of
        # the five DECLARED families. That expectation is exactly the circular
        # claim H3 closes: the declared five were drawn to match the data this
        # project happens to hold, and three of the four canonical universal
        # macro families (liquidity, positioning, issuance) have no data source
        # at all. While that is true the evidence base is structurally
        # incomplete and HIGH is not a claim the system may make, however well
        # the declared families reported.
        self.assertIs(full.data, ConfidenceLevel.MODERATE)
        self.assertIn("data:architectural_dormant_canonical", full.caps_applied)

        # The property this test exists to protect -- missing data must be
        # VISIBLE and must lower the completeness measure -- is now asserted on
        # the coverage ratio rather than the three-state label. That is a
        # strengthening, not a weakening: the ratio is a float that separates
        # every degree of incompleteness, where the label can only separate
        # three, and under the architectural cap both readings sit at MODERATE.
        self.assertLessEqual(partial.data.value, full.data.value)
        self.assertLess(partial.coverage.ratio, full.coverage.ratio)
        self.assertEqual(full.coverage.ratio, 1.0)
        self.assertIn("news_geopolitical.rule_based_news", partial.coverage.missing)

    def test_critical_family_unavailable_drops_data_confidence_to_low(self):
        result = self._assemble(_readings(policy=None, activity=0.5, news=0.5))
        self.assertIs(result.data, ConfidenceLevel.LOW)

    def test_broken_series_caps_data_confidence(self):
        result = self._assemble(
            _readings(policy=0.5, activity=0.5, news=0.5, directional=0.5, structure=0.5),
            staleness_observations=(horizons.Staleness.BROKEN,),
        )
        self.assertIs(result.data, ConfidenceLevel.LOW)

    def test_conflicting_sources_are_flagged_not_silently_resolved(self):
        result = self._assemble(
            _readings(policy=0.5, activity=0.5, news=0.5, directional=0.5, structure=0.5),
            conflicting_sources=True,
        )
        self.assertIn("data:conflicting_sources", result.caps_applied)
        self.assertTrue(any("flagged for review" in n for n in result.notes))

    def test_low_regime_confidence_requires_more_confirmation_without_reweighting(self):
        result = self._assemble(
            _readings(policy=0.5, activity=0.5, news=0.5, directional=0.5, structure=0.5),
            regime_confidence=ConfidenceLevel.LOW,
        )
        self.assertIn("regime_confidence_low", result.caps_applied)
        self.assertTrue(any("NOT reweighted" in n for n in result.notes))

    def test_execution_confidence_from_stage_a_is_preserved(self):
        result = self._assemble(
            _readings(policy=0.5, activity=0.5, news=0.5, directional=0.5, structure=0.5),
            execution_confidence=ConfidenceLevel.LOW,
        )
        self.assertIs(result.execution, ConfidenceLevel.LOW)

    def test_technical_confidence_ceiling_is_declared_honestly(self):
        result = self._assemble(
            _readings(policy=0.5, activity=0.5, news=0.5, directional=0.5, structure=0.5)
        )
        self.assertTrue(any("cannot reach HIGH" in n for n in result.notes))


# ---------------------------------------------------------------------------
# 7. Thesis state and the escalation rule
# ---------------------------------------------------------------------------
class TestThesisLifecycle(unittest.TestCase):
    def _thesis(self):
        return thesis_mod.open_thesis(
            thesis_id="t1",
            direction=Direction.BULLISH,
            horizon=Horizon.TACTICAL,
            thesis_input_keys=("policy_real_rates", "macro_activity"),
            opened_at=NOW,
        )

    def _fail(self, record, instrument, explanation=""):
        return thesis_mod.record_failure(
            record,
            thesis_mod.SetupFailure(
                instrument=instrument, failed_at=NOW, explanation=explanation
            ),
        )

    def test_all_four_states_exist(self):
        self.assertEqual(
            {s.value for s in ThesisState},
            {"intact", "weakening", "under_review", "invalidated"},
        )

    def test_a_single_failure_does_not_escalate(self):
        record = self._fail(self._thesis(), "XAUUSD")
        updated, assessment = thesis_mod.apply_escalation(record)
        self.assertFalse(assessment.repeated)
        self.assertIs(updated.state, ThesisState.INTACT)

    def test_repeated_failures_on_one_instrument_are_not_broad(self):
        record = self._fail(self._fail(self._thesis(), "XAUUSD"), "XAUUSD")
        updated, assessment = thesis_mod.apply_escalation(record)
        self.assertTrue(assessment.repeated)
        self.assertFalse(assessment.broad)
        self.assertIs(updated.state, ThesisState.INTACT)

    def test_explained_failures_do_not_escalate(self):
        record = self._fail(
            self._fail(self._thesis(), "XAUUSD", "stale data feed"), "EURUSD", "known regime shift"
        )
        updated, assessment = thesis_mod.apply_escalation(record)
        self.assertFalse(assessment.unexplained)
        self.assertIs(updated.state, ThesisState.INTACT)

    def test_repeated_broad_and_unexplained_escalates_to_under_review(self):
        record = self._fail(self._fail(self._thesis(), "XAUUSD"), "EURUSD")
        updated, assessment = thesis_mod.apply_escalation(record, at=NOW)
        self.assertTrue(assessment.should_escalate)
        self.assertIs(updated.state, ThesisState.UNDER_REVIEW)
        self.assertEqual(updated.transitions[-1].to_state, ThesisState.UNDER_REVIEW)

    def test_price_evidence_never_invalidates_the_macro_thesis(self):
        record = self._fail(self._fail(self._thesis(), "XAUUSD"), "EURUSD")
        updated, _ = thesis_mod.apply_escalation(record)
        self.assertIsNot(updated.state, ThesisState.INVALIDATED)

    def test_only_macro_evidence_invalidates(self):
        record = thesis_mod.apply_macro_evidence(
            self._thesis(), supporting_families=0, conflicting_families=2, at=NOW
        )
        self.assertIs(record.state, ThesisState.INVALIDATED)

    def test_partial_macro_conflict_weakens_rather_than_invalidates(self):
        record = thesis_mod.apply_macro_evidence(
            self._thesis(), supporting_families=2, conflicting_families=1, at=NOW
        )
        self.assertIs(record.state, ThesisState.WEAKENING)

    def test_price_alone_cannot_restore_a_thesis_under_review(self):
        record = self._fail(self._fail(self._thesis(), "XAUUSD"), "EURUSD")
        record, _ = thesis_mod.apply_escalation(record)
        unchanged = thesis_mod.restore_thesis(record, new_macro_evidence=False)
        self.assertIs(unchanged.state, ThesisState.UNDER_REVIEW)

    def test_new_macro_evidence_restores_a_thesis_under_review(self):
        record = self._fail(self._fail(self._thesis(), "XAUUSD"), "EURUSD")
        record, _ = thesis_mod.apply_escalation(record)
        restored = thesis_mod.restore_thesis(
            record, new_macro_evidence=True, description="policy pivot confirmed", at=NOW
        )
        self.assertIs(restored.state, ThesisState.INTACT)

    def test_an_invalidated_thesis_is_not_restored(self):
        record = thesis_mod.apply_macro_evidence(
            self._thesis(), supporting_families=0, conflicting_families=2
        )
        self.assertIs(
            thesis_mod.restore_thesis(record, new_macro_evidence=True).state,
            ThesisState.INVALIDATED,
        )

    def test_transitions_are_appended_with_timestamps(self):
        record = thesis_mod.apply_macro_evidence(
            self._thesis(), supporting_families=1, conflicting_families=1, at=NOW
        )
        self.assertEqual(len(record.transitions), 1)
        self.assertEqual(record.transitions[0].at, NOW)
        self.assertTrue(record.transitions[0].reason.strip())

    def test_thesis_input_registry_excludes_its_own_inputs_from_confirmation(self):
        record = self._thesis()
        candidates = record.confirmation_candidates(
            ("policy_real_rates", "macro_activity", "directional", "structure")
        )
        self.assertNotIn("policy_real_rates", candidates)
        self.assertNotIn("macro_activity", candidates)
        self.assertIn("directional", candidates)


# ---------------------------------------------------------------------------
# 5. Shadow logging
# ---------------------------------------------------------------------------
class TestShadowLogging(unittest.TestCase):
    def test_record_carries_every_required_field(self):
        record = _evaluate().record.as_record()
        for key in (
            "evaluated_at",
            "horizon",
            "claim",
            "regime",
            "families",
            "available_families",
            "unavailable_families",
            "scenarios",
            "transmission_predictions",
            "execution",
            "confidence",
            "event_risk_state",
            "decision_state",
            "gates_triggered",
            "conflicts_detected",
            "observations",
            "cross_asset",
        ):
            self.assertIn(key, record)

    def test_every_family_records_state_strength_and_why(self):
        record = _evaluate().record.as_record()
        for family in record["families"]:
            self.assertIn("direction", family)
            self.assertIn("strength", family)
            self.assertTrue(family["rationale"].strip())

    def test_record_is_json_serialisable(self):
        json.dumps(_evaluate().record.as_record())

    def test_shadow_log_is_append_only(self):
        log = shadow.ShadowLog()
        evaluation = _evaluate()
        log.append(evaluation.record)
        with self.assertRaises(shadow.ShadowLogError):
            log.append(evaluation.record)

    def test_shadow_log_round_trips(self):
        log = shadow.ShadowLog()
        log.append(_evaluate().record)
        restored = shadow.ShadowLog.from_record(json.loads(json.dumps(log.as_record())))
        self.assertEqual(len(restored), 1)

    def test_cross_asset_section_declares_it_is_withheld(self):
        record = _evaluate().record.as_record()
        self.assertEqual(record["cross_asset"]["status"], "withheld")
        self.assertIsNone(record["cross_asset"]["relationship_stability"])

    def test_in_memory_store_satisfies_the_protocol(self):
        store = shadow.InMemoryShadowStore()
        self.assertIsInstance(store, shadow.ShadowStore)
        store.save("k", {"a": 1})
        self.assertEqual(store.load("k", None), {"a": 1})


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------
class TestShadowEvaluation(unittest.TestCase):
    def test_full_agreement_yields_a_confirmed_thesis(self):
        result = _evaluate()
        self.assertIs(result.decision.state, DecisionState.CONFIRMED_THESIS)
        self.assertIs(result.direction, Direction.BULLISH)

    def test_evaluation_preserves_stage_a_voting_registry(self):
        result = _evaluate()
        self.assertEqual(
            [r.family_key for r in result.readings], list(registry.voting_family_keys())
        )
        for reading in result.readings:
            self.assertEqual(reading.contribution_count, 1)

    def test_evaluation_applies_stage_a_caps(self):
        result = _evaluate(
            signals_by_family=_signals(
                policy=0.9, activity=0.9, news=0.9, directional=0.9, structure=0.9
            )
        )
        self.assertTrue(result.aggregate.global_cap_applied)

    def test_missing_critical_data_degrades_rather_than_reverses(self):
        result = _evaluate(signals_by_family=_signals(policy=None, activity=None, news=0.5))
        self.assertIs(
            result.decision.state, DecisionState.INSUFFICIENT_DATA_SYSTEM_DEGRADED
        )
        self.assertIs(result.confidence.data, ConfidenceLevel.LOW)

    def test_event_veto_defers_execution_without_invalidating(self):
        result = _evaluate(minutes_to_event=5.0, is_top_tier_event=True)
        self.assertIs(result.decision.state, DecisionState.EXECUTION_BLOCKED)
        self.assertTrue(result.execution.deferred_not_invalidated)

    def test_thesis_state_flows_into_the_decision(self):
        record = thesis_mod.open_thesis(
            thesis_id="t", direction=Direction.BULLISH, horizon=Horizon.TACTICAL,
            opened_at=NOW,
        )
        record = thesis_mod.apply_macro_evidence(
            record, supporting_families=0, conflicting_families=2, at=NOW
        )
        result = _evaluate(thesis=record)
        self.assertIs(result.decision.state, DecisionState.MACRO_THESIS_INVALIDATED)

    def test_sizing_uses_the_exposed_atr_ratio(self):
        calm = _evaluate(atr_ratio=1.0)
        wild = _evaluate(atr_ratio=2.0)
        self.assertEqual(calm.size.volatility_multiplier, 1.0)
        self.assertLess(wild.size.volatility_multiplier, 1.0)

    def test_sizing_is_unavailable_when_volatility_cannot_be_measured(self):
        result = _evaluate(atr_ratio=None)
        self.assertIsNone(result.size.volatility_multiplier)
        self.assertIn("volatility_scale_unmeasurable", result.size.unavailable_reasons)

    def test_scenarios_and_claim_share_the_evaluation_timestamp(self):
        result = _evaluate()
        self.assertEqual(result.scenarios.registered_at, NOW)
        self.assertEqual(result.claim.registered_at, NOW)

    def test_thesis_input_keys_records_available_families(self):
        result = _evaluate()
        keys = evaluate_mod.thesis_input_keys(result.readings)
        self.assertIn("policy_real_rates", keys)
        self.assertEqual(len(keys), 5)


# ---------------------------------------------------------------------------
# 11-15. Stage B constraints
# ---------------------------------------------------------------------------
class TestStageBConstraints(unittest.TestCase):
    def test_withheld_cross_asset_is_still_not_activated(self):
        self.assertIn("cross_asset_bridge", registry.withheld_keys())

    def test_dormant_components_are_still_not_implemented(self):
        for key in (
            "liquidity_funding",
            "positioning_crowding",
            "fiscal_issuance",
            "financial_cycle",
            "systemic_risk_buildup",
            "order_flow_market_depth",
        ):
            self.assertIn(key, registry.dormant_keys())
        self.assertEqual(len(registry.VOTING_FAMILIES), 5)

    def test_voting_budget_is_unchanged_by_stage_b(self):
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

    def test_scenarios_regime_and_predictions_are_all_non_voting(self):
        result = _evaluate()
        # Only the five registered families appear as contributions.
        self.assertEqual(
            result.aggregate.contribution_count,
            len(result.aggregate.supporting_families)
            + len(result.aggregate.conflicting_families),
        )
        self.assertLessEqual(result.aggregate.contribution_count, 5)

    def test_regime_and_volatility_never_change_direction(self):
        directions = set()
        for vol in ("compression", "normal", "expansion", "unavailable"):
            directions.add(_evaluate(volatility_regime=vol).direction)
        self.assertEqual(directions, {Direction.BULLISH})

    def test_neutral_and_unavailable_remain_distinct_end_to_end(self):
        flat = _evaluate(signals_by_family=_signals(
            policy=0.0, activity=0.0, news=0.0, directional=0.0, structure=0.0
        ))
        missing = _evaluate(signals_by_family=_signals(
            policy=None, activity=None, news=None, directional=None, structure=None
        ))
        self.assertIs(flat.decision.state, DecisionState.MIXED_NO_EDGE)
        self.assertIs(
            missing.decision.state, DecisionState.INSUFFICIENT_DATA_SYSTEM_DEGRADED
        )
        # SUPERSEDED BY H3, constant only. The property under test -- flat and
        # missing must stay distinct end to end -- is untouched and is now
        # asserted more strongly: they differ in decision state, in Data
        # Confidence AND in coverage, where flat evidence is fully present
        # (ratio 1.0) and missing evidence is not. The former HIGH became
        # MODERATE because canonical macro families are dormant, which is a
        # statement about the evidence base rather than about these readings.
        self.assertIs(flat.confidence.data, ConfidenceLevel.MODERATE)
        self.assertIs(missing.confidence.data, ConfidenceLevel.LOW)
        self.assertEqual(flat.confidence.coverage.ratio, 1.0)
        self.assertEqual(missing.confidence.coverage.ratio, 0.0)

    def test_no_operator_risk_parameter_is_invented(self):
        self.assertFalse(risk.DEFAULT_RISK_PARAMETERS.is_configured)
        self.assertFalse(_evaluate().size.available)


if __name__ == "__main__":
    unittest.main()
