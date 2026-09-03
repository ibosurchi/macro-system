"""Architecture B2 -- the Final Fixes → Freeze blockers.

One test class per blocker, each written to fail against the pre-fix code:

    C1     the Directional family read sub-sigma noise as directional evidence
    C4     missing production data reached B2 as FLAT rather than Unavailable
    C2/C3  a macro-chosen flag drove a decision state labelled technical, and
           execution geometry was measured against the opposite trade's plan
    H4     records stored verdicts but not the values they were reached from
    H1/H1b Tactical and Execution were the same claim, and the frequency rule
           was implemented but never invoked

Plus the null benchmark, the transmission containment, and the registry's
own wiring rule applied consistently.

The single most important test here is
``TestC1DirectionalNoiseSensitivity.test_the_family_is_not_a_noise_generator``.
The defect it covers survived 1,533 tests, because every component behaved
exactly as specified and the specification was wrong about what its own
constant meant. No unit test of a component would have caught it; only feeding
the pipeline input known to contain nothing does.
"""
from __future__ import annotations

import math
import os
import sys
import unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from apex.b2 import adapters, decision, execution, families, registry, shadow
from apex.b2.enums import (
    ConfidenceLevel,
    DecisionState,
    Direction,
    FamilyStrength,
    Horizon,
)
from apex.b2.evaluate import run_shadow_evaluation
from apex.b2.evaluation.null_benchmark import (
    Baseline,
    run_baseline,
    run_null_benchmark,
    threshold_response,
)
from apex.b2.horizons import SeriesFrequency, horizon_compatible

NOW = datetime(2026, 9, 3, 12, 0, 0, tzinfo=timezone.utc)
VOL = 0.001


def _tactical(r15, r1h, r4h, structure="Range / Mean-Reversion"):
    return {"ret_15m": r15, "ret_1h": r1h, "ret_4h": r4h, "structure": structure}


# ===========================================================================
# C1 -- the Directional family must not read noise as evidence
# ===========================================================================
class TestC1DirectionalNoiseSensitivity(unittest.TestCase):
    """The band must belong to the scale the member is actually on."""

    def test_the_family_is_not_a_noise_generator(self):
        """The control that would have caught C1.

        Pre-fix, on driftless random walks containing no signal whatsoever, the
        family emitted STRONG -- full aggregation weight -- 44.6% of the time.
        The threshold response below shows that rate under the old band, so the
        assertion is anchored to a measurement rather than to a wish.
        """
        result = run_baseline(Baseline.PURE_NOISE, samples=6000)

        # STRONG is the rate that matters: the concave aggregator turns WEAK
        # into 0.35 and STRONG into 1.00, so a STRONG reading on noise is
        # full-weight evidence manufactured out of nothing.
        self.assertLess(
            result.strong_rate, 0.30,
            f"STRONG on pure noise at {result.strong_rate:.1%}; pre-fix was 44.6%",
        )
        # And the family must be capable of saying nothing at all.
        self.assertGreater(
            result.flat_rate, 0.15,
            "a family that is essentially never flat cannot express 'no evidence'",
        )

    def test_the_old_band_is_measurably_worse_on_the_same_draws(self):
        """Same seed, same draws, only the band differs."""
        old, new = threshold_response([0.05, 0.5], samples=6000)
        self.assertGreater(
            old.strong_rate, new.strong_rate,
            "the corrected band must reduce full-weight readings on noise",
        )

    def test_a_real_signal_still_reads_through(self):
        """Silence about everything is not the goal; silence about noise is."""
        noise = run_baseline(Baseline.PURE_NOISE, samples=6000)
        drift = run_baseline(Baseline.DRIFTING, samples=6000)
        self.assertGreater(
            drift.strong_rate, noise.strong_rate,
            "the family must respond more strongly to drift than to noise",
        )

    def test_members_declare_the_scale_their_band_belongs_to(self):
        by_scale = {
            spec.key: spec
            for family in registry.VOTING_FAMILIES
            for spec in family.member_specs
        }
        for key in ("short_horizon_return", "medium_horizon_return"):
            self.assertIs(by_scale[key].scale, registry.MemberScale.STANDARDISED_SIGMA)
            self.assertEqual(
                by_scale[key].threshold, registry.STANDARDISED_SIGMA_FLAT_THRESHOLD
            )
        for key in ("inflation_momentum", "rule_based_news", "real_yield_momentum"):
            self.assertIs(by_scale[key].scale, registry.MemberScale.BOUNDED_UNIT)
            self.assertEqual(by_scale[key].threshold, 0.05)

    def test_a_sub_sigma_band_cannot_be_declared_for_a_sigma_member(self):
        """The registry refuses to reintroduce the defect."""
        # Construction itself is permitted; the guard lives in registry
        # validation, which runs at import and can be re-run explicitly.
        definition = registry.FamilyDefinition(
            key="bad",
            label="Bad",
            role=registry.Role.ACTIVE_VOTING,
            horizon=Horizon.EXECUTION,
            members=("m",),
            justification="x",
            data_sources=(),
            member_specs=(
                registry.MemberSpec(
                    key="m",
                    scale=registry.MemberScale.STANDARDISED_SIGMA,
                    frequency=SeriesFrequency.INTRADAY,
                    flat_threshold=0.05,
                ),
            ),
        )
        original = registry.VOTING_FAMILIES
        try:
            registry.VOTING_FAMILIES = (definition,)
            with self.assertRaises(ValueError) as caught:
                registry._validate_registry()
            self.assertIn("sigma", str(caught.exception))
        finally:
            registry.VOTING_FAMILIES = original
            registry._validate_registry()

    def test_productions_sqrt_bars_term_is_restored(self):
        signals = adapters.directional_signals(
            tactical=_tactical(0.001, 0.001, 0.001), volatility_scale=VOL
        )
        self.assertAlmostEqual(
            signals["short_horizon_return"], 0.001 / (VOL * math.sqrt(3)), places=9
        )
        self.assertAlmostEqual(
            signals["medium_horizon_return"], 0.001 / (VOL * math.sqrt(12)), places=9
        )

    def test_standardised_members_are_not_clamped(self):
        """A 6-sigma move must not be stored as if it were a 1-sigma move."""
        signals = adapters.directional_signals(
            tactical=_tactical(0.02, 0.02, 0.02), volatility_scale=VOL
        )
        self.assertGreater(signals["short_horizon_return"], 1.0)

    def test_alignment_requires_magnitude_not_only_sign(self):
        tiny = 0.05 * VOL
        signals = adapters.directional_signals(
            tactical=_tactical(tiny, tiny, tiny), volatility_scale=VOL
        )
        self.assertEqual(signals["multi_timeframe_alignment"], 0.0)

        big = 3.0 * VOL
        signals = adapters.directional_signals(
            tactical=_tactical(big, big * 4, big * 8), volatility_scale=VOL
        )
        self.assertEqual(signals["multi_timeframe_alignment"], 1.0)

    def test_alignment_uses_the_same_band_as_the_members(self):
        """A band the members and the alignment gate could disagree about would
        let alignment confirm a move the family had already called flat."""
        value = 0.4 * VOL  # below 0.5 sigma once standardised over 3 bars
        signals = adapters.directional_signals(
            tactical=_tactical(value, value, value), volatility_scale=VOL,
            neutral_band=5.0,
        )
        self.assertEqual(signals["multi_timeframe_alignment"], 0.0)


# ===========================================================================
# C4 -- missing is never flat
# ===========================================================================
class TestC4MissingIsNeverFlat(unittest.TestCase):
    def test_no_articles_makes_both_news_members_unavailable(self):
        signals = adapters.news_geopolitical_signals(
            rule_points=0.0, ai_points=0.0, article_count=0, ai_active=True
        )
        self.assertIsNone(signals["rule_based_news"])
        self.assertIsNone(signals["ai_news"])

    def test_an_inactive_ai_batch_makes_only_the_ai_member_unavailable(self):
        signals = adapters.news_geopolitical_signals(
            rule_points=0.25, ai_points=0.0, article_count=3, ai_active=False
        )
        self.assertAlmostEqual(signals["rule_based_news"], 0.5)
        self.assertIsNone(signals["ai_news"])

    def test_a_genuine_zero_with_articles_present_is_flat_not_unavailable(self):
        """The invariant cuts both ways: a real balanced read must stay FLAT."""
        signals = adapters.news_geopolitical_signals(
            rule_points=0.0, ai_points=0.0, article_count=7, ai_active=True
        )
        self.assertEqual(signals["rule_based_news"], 0.0)
        self.assertIs(
            families.classify_signal(signals["rule_based_news"]), Direction.FLAT
        )

    def test_unstated_availability_leaves_the_points_trusted(self):
        signals = adapters.news_geopolitical_signals(rule_points=0.25)
        self.assertAlmostEqual(signals["rule_based_news"], 0.5)

    def test_returns_without_a_volatility_scale_are_unavailable(self):
        signals = adapters.directional_signals(tactical=_tactical(0.002, 0.004, 0.008))
        for key in ("short_horizon_return", "medium_horizon_return",
                    "multi_timeframe_alignment"):
            self.assertIsNone(signals[key], key)

    def test_a_missing_timeframe_makes_alignment_unavailable_not_flat(self):
        signals = adapters.directional_signals(
            tactical={"ret_15m": 0.002, "ret_1h": 0.004}, volatility_scale=VOL
        )
        self.assertIsNone(signals["multi_timeframe_alignment"])

    def test_an_unknown_structure_label_is_unavailable(self):
        signals = adapters.structure_signals(tactical={"structure": "Something New"})
        self.assertIsNone(signals["breakout_quality"])

    def test_every_adapter_returns_none_and_never_zero_for_absent_input(self):
        """Swept across the adapter surface, not only the reported examples."""
        for signals in (
            adapters.policy_real_rates_signals(),
            adapters.macro_activity_signals(),
            adapters.news_geopolitical_signals(),
            adapters.directional_signals(),
            adapters.structure_signals(),
        ):
            for key, value in signals.items():
                self.assertIsNone(value, key)


# ===========================================================================
# C2 / C3 -- no macro evidence wearing a technical label
# ===========================================================================
class TestC2C3MacroDependencySevered(unittest.TestCase):
    def test_the_entry_plan_status_is_not_admissible_as_technical_evidence(self):
        inputs = adapters.execution_inputs(
            entry_plan={"status": "INVALIDATED", "direction": "SELL"}
        )
        self.assertIsNone(inputs["technical_invalidated"])
        self.assertEqual(inputs["entry_plan_status"], "INVALIDATED")

    def test_unknown_invalidation_does_not_produce_a_decision_state(self):
        readings = families.evaluate_families(
            registry.VOTING_FAMILIES,
            {
                "policy_real_rates": {"real_yield_momentum": 0.5},
                "macro_activity": {"inflation_momentum": 0.5},
                "news_geopolitical": {"rule_based_news": 0.5},
            },
        )
        outcome = decision.resolve_decision(
            readings=readings,
            macro_keys=registry.MACRO_FAMILY_KEYS,
            technical_keys=registry.TECHNICAL_FAMILY_KEYS,
            critical_family_keys=registry.CRITICAL_FAMILY_KEYS,
            decision_horizon=Horizon.TACTICAL,
            technical_invalidated=None,
        )
        self.assertIsNot(outcome.state, DecisionState.TECHNICAL_SETUP_INVALIDATED)
        self.assertTrue(
            any("UNKNOWN" in note for note in outcome.notes),
            "an unknown invalidation state must be recorded, not silently ignored",
        )

    def test_an_explicit_true_still_invalidates(self):
        """The branch is preserved for a source B2 would trust."""
        readings = families.evaluate_families(
            registry.VOTING_FAMILIES,
            {
                "policy_real_rates": {"real_yield_momentum": 0.5},
                "macro_activity": {"inflation_momentum": 0.5},
                "news_geopolitical": {"rule_based_news": 0.5},
            },
        )
        outcome = decision.resolve_decision(
            readings=readings,
            macro_keys=registry.MACRO_FAMILY_KEYS,
            technical_keys=registry.TECHNICAL_FAMILY_KEYS,
            critical_family_keys=registry.CRITICAL_FAMILY_KEYS,
            decision_horizon=Horizon.TACTICAL,
            technical_invalidated=True,
        )
        self.assertIs(outcome.state, DecisionState.TECHNICAL_SETUP_INVALIDATED)

    def test_opposite_side_entry_plan_produces_no_geometry(self):
        assessment = execution.assess_execution(
            invalidation_level=95.0,
            entry_zone=(99.0, 101.0),
            current_price=100.0,
            atr=1.0,
            thesis_direction=Direction.BULLISH,
            entry_plan_direction=Direction.BEARISH,
        )
        self.assertTrue(assessment.direction_mismatch)
        self.assertTrue(assessment.blocked)
        self.assertFalse(assessment.geometry_measured)
        # None of the geometry may be computed against the wrong trade.
        self.assertIsNone(assessment.invalidation_distance)
        self.assertIsNone(assessment.invalidation_distance_atr)
        self.assertIsNone(assessment.entry_zone)
        self.assertFalse(assessment.in_zone)
        self.assertFalse(assessment.extended)
        self.assertIn("entry_plan_direction_mismatch", assessment.notes)
        self.assertIn("opposite trade", assessment.block_reason.lower())

    def test_an_agreeing_plan_is_measured_normally(self):
        assessment = execution.assess_execution(
            invalidation_level=95.0,
            entry_zone=(99.0, 101.0),
            current_price=100.0,
            atr=1.0,
            thesis_direction=Direction.BULLISH,
            entry_plan_direction=Direction.BULLISH,
        )
        self.assertFalse(assessment.direction_mismatch)
        self.assertTrue(assessment.geometry_measured)
        self.assertTrue(assessment.in_zone)

    def test_both_directions_are_recorded_even_when_they_agree(self):
        """So the mismatch RATE is measurable, not only its occurrences."""
        record = execution.assess_execution(
            invalidation_level=95.0,
            current_price=100.0,
            thesis_direction=Direction.BULLISH,
            entry_plan_direction=Direction.BULLISH,
        ).as_record()
        self.assertEqual(record["thesis_direction"], "bullish")
        self.assertEqual(record["entry_plan_direction"], "bullish")
        self.assertIn("direction_mismatch", record)

    def test_a_neutral_plan_is_not_treated_as_a_mismatch(self):
        assessment = execution.assess_execution(
            invalidation_level=None,
            thesis_direction=Direction.BULLISH,
            entry_plan_direction=Direction.UNAVAILABLE,
        )
        self.assertFalse(assessment.direction_mismatch)
        self.assertTrue(assessment.blocked)


# ===========================================================================
# H4 -- the corpus must be re-scorable
# ===========================================================================
class TestH4EvidenceIsPersisted(unittest.TestCase):
    def _evaluation(self, horizon=Horizon.TACTICAL):
        return run_shadow_evaluation(
            instrument="Gold",
            decision_horizon=horizon,
            signals_by_family=adapters.build_signals(
                composite_rows=[
                    {"cat": "inflation", "score": 0.4, "weight": 1.0},
                    {"cat": "rate", "score": 0.3, "weight": 1.0},
                ],
                rule_points=0.25,
                article_count=5,
                ai_active=False,
                tactical=_tactical(0.002, 0.004, 0.008, "Upside Breakout"),
                volatility_scale=VOL,
            ),
            signal_provenance=adapters.signal_provenance(
                volatility_scale=VOL, article_count=5, ai_active=False
            ),
            evaluated_at=NOW,
        )

    def test_every_member_value_is_stored_including_the_absent_ones(self):
        record = self._evaluation().record.as_record()
        for family in record["families"]:
            declared = set(registry.FAMILIES_BY_KEY[family["family"]].members)
            stored = {m["member"] for m in family["member_values"]}
            self.assertEqual(stored, declared, family["family"])
            for member in family["member_values"]:
                if member["value"] is None:
                    self.assertFalse(member["available"], member["member"])

    def test_an_unavailable_member_is_stored_as_none_not_zero(self):
        record = self._evaluation().record.as_record()
        news = next(f for f in record["families"] if f["family"] == "news_geopolitical")
        values = {m["member"]: m for m in news["member_values"]}
        self.assertIsNone(values["ai_news"]["value"])
        self.assertFalse(values["ai_news"]["available"])

    def test_provenance_is_sufficient_to_rescore_the_record(self):
        record = self._evaluation().record.as_record()
        provenance = record["evidence_provenance"]
        self.assertIsNotNone(provenance)
        specs = provenance["member_specs"]
        self.assertIn("scale_thresholds", specs)
        for family_key, members in specs["families"].items():
            for member in members:
                self.assertIn("scale", member)
                self.assertIn("flat_threshold", member)
                self.assertIn("frequency", member)
        signals = provenance["signals"]
        self.assertIn("sqrt(bars)", signals["directional"]["normalisation"])
        self.assertFalse(signals["directional"]["clamped"])

    def test_a_stored_record_can_be_reclassified_under_a_different_band(self):
        """The point of H4: re-score history without re-running the pipeline."""
        record = self._evaluation().record.as_record()
        directional = next(
            f for f in record["families"] if f["family"] == "directional"
        )
        values = {m["member"]: m["value"] for m in directional["member_values"]}
        strict = {
            key: families.classify_signal(value, 5.0)
            for key, value in values.items()
            if value is not None
        }
        self.assertTrue(strict)
        self.assertTrue(all(d is Direction.FLAT for d in strict.values()))

    def test_the_schema_version_marks_the_freeze_boundary(self):
        record = self._evaluation().record.as_record()
        # SUPERSEDED BY H3, and the correction makes the test stronger. It
        # previously required a new record's version to EQUAL the freeze
        # boundary, which silently conflated two different constants: "what is
        # written now" and "where the pre/post-freeze line sits". They were
        # equal only while v3 happened to be current. H3 writes v4, so the
        # relationship being protected is >=, and the boundary itself must stay
        # exactly where the Final Fix put it.
        self.assertEqual(record["schema_version"], shadow.CURRENT_SCHEMA_VERSION)
        self.assertGreaterEqual(
            record["schema_version"], shadow.FREEZE_SCHEMA_VERSION
        )
        self.assertEqual(shadow.FREEZE_SCHEMA_VERSION, 3)
        self.assertEqual(record["evidence_epoch"], shadow.EVIDENCE_EPOCH_POST_FREEZE)
        self.assertFalse(shadow.is_pre_freeze_record(record))

    def test_legacy_records_are_classified_pre_freeze_and_not_rewritten(self):
        for version in (1, 2):
            self.assertTrue(shadow.is_pre_freeze_record({"schema_version": version}))
            self.assertEqual(shadow.evidence_epoch(version), "pre_freeze")
        # A payload with no version at all is pre-freeze, the safe direction.
        self.assertTrue(shadow.is_pre_freeze_record({}))
        self.assertTrue(shadow.is_pre_freeze_record(None))
        # Storage-row shape is understood too.
        self.assertFalse(
            shadow.is_pre_freeze_record({"record": {"schema_version": 3}})
        )

    def test_a_legacy_record_still_maps_onto_a_row_unchanged(self):
        legacy = {
            "record_id": "abc", "instrument": "Gold", "horizon": "tactical",
            "evaluated_at": NOW.isoformat(), "schema_version": 2,
        }
        row = shadow.record_to_row(legacy)
        self.assertEqual(row["schema_version"], 2)
        self.assertEqual(row["record"], legacy)
        self.assertNotIn("evidence_provenance", row["record"])


# ===========================================================================
# H1 / H1b -- horizons must be able to differ, and the rule must be enforced
# ===========================================================================
class TestH1HorizonSeparation(unittest.TestCase):
    SIGNALS = {
        "policy_real_rates": {
            "policy_rate_momentum": 0.6,
            "real_yield_momentum": -0.6,
            "nominal_yield_momentum": -0.6,
            "inflation_expectations_momentum": -0.6,
        },
        "macro_activity": {
            "inflation_momentum": 0.6,
            "labor_momentum": 0.6,
            "growth_momentum": 0.6,
        },
        "news_geopolitical": {"rule_based_news": 0.6, "ai_news": 0.6},
        # Flat, not unavailable: present evidence carrying no direction, so
        # the macro block alone decides and the horizon effect is visible.
        "directional": {
            "short_horizon_return": 0.0,
            "medium_horizon_return": 0.0,
            "multi_timeframe_alignment": 0.0,
        },
        "structure": {"breakout_quality": 0.0},
    }

    def _readings(self, horizon):
        return families.evaluate_families(
            registry.VOTING_FAMILIES, self.SIGNALS, horizon
        )

    def test_monthly_evidence_cannot_vote_at_the_execution_horizon(self):
        readings = {r.family_key: r for r in self._readings(Horizon.EXECUTION)}
        activity = readings["macro_activity"]
        self.assertIs(activity.direction, Direction.UNAVAILABLE)
        self.assertEqual(
            set(activity.horizon_excluded_members),
            {"inflation_momentum", "labor_momentum", "growth_momentum"},
        )

    def test_quarterly_evidence_cannot_vote_at_the_tactical_horizon(self):
        readings = {r.family_key: r for r in self._readings(Horizon.TACTICAL)}
        activity = readings["macro_activity"]
        self.assertIn("growth_momentum", activity.horizon_excluded_members)
        # ...but the monthly members still do, so the family survives.
        self.assertIs(activity.direction, Direction.BULLISH)

    def test_daily_and_event_evidence_survives_at_every_horizon(self):
        for horizon in (Horizon.EXECUTION, Horizon.TACTICAL, Horizon.STRUCTURAL):
            readings = {r.family_key: r for r in self._readings(horizon)}
            self.assertIs(
                readings["news_geopolitical"].direction, Direction.BULLISH, horizon
            )
            self.assertTrue(readings["policy_real_rates"].is_available, horizon)

    def test_horizon_exclusion_is_distinguishable_from_flat(self):
        excluded = {r.family_key: r for r in self._readings(Horizon.EXECUTION)}[
            "macro_activity"
        ]
        flat = families.evaluate_family(
            registry.NEWS_GEOPOLITICAL,
            {"rule_based_news": 0.0, "ai_news": 0.0},
            Horizon.EXECUTION,
        )
        self.assertIs(excluded.direction, Direction.UNAVAILABLE)
        self.assertIs(flat.direction, Direction.FLAT)
        self.assertTrue(excluded.is_horizon_excluded)
        self.assertFalse(flat.is_horizon_excluded)

    def test_horizon_exclusion_is_distinguishable_from_missing_data(self):
        missing = families.evaluate_family(
            registry.MACRO_ACTIVITY, {}, Horizon.TACTICAL
        )
        excluded = families.evaluate_family(
            registry.MACRO_ACTIVITY,
            {"inflation_momentum": 0.6, "labor_momentum": 0.6, "growth_momentum": 0.6},
            Horizon.EXECUTION,
        )
        self.assertIs(missing.direction, Direction.UNAVAILABLE)
        self.assertIs(excluded.direction, Direction.UNAVAILABLE)
        self.assertFalse(missing.is_horizon_excluded)
        self.assertTrue(excluded.is_horizon_excluded)
        self.assertIn("must reduce Data Confidence", missing.rationale)
        self.assertIn("structural exclusion", excluded.rationale)

    def test_a_horizon_exclusion_is_not_reported_as_a_data_outage(self):
        """Otherwise every Execution record claims a data problem it lacks."""
        evaluation = run_shadow_evaluation(
            instrument="Gold",
            decision_horizon=Horizon.EXECUTION,
            signals_by_family=self.SIGNALS,
            evaluated_at=NOW,
        )
        self.assertIsNot(
            evaluation.decision.state,
            DecisionState.INSUFFICIENT_DATA_SYSTEM_DEGRADED,
        )
        self.assertIn("macro_activity", evaluation.confidence.horizon_excluded)
        self.assertNotIn("macro_activity", evaluation.confidence.unavailable)

    def test_the_two_horizons_can_legitimately_differ(self):
        """The point of H1: one snapshot, two genuinely different readings."""
        tactical = run_shadow_evaluation(
            instrument="Gold", decision_horizon=Horizon.TACTICAL,
            signals_by_family=self.SIGNALS, evaluated_at=NOW,
        )
        execution_eval = run_shadow_evaluation(
            instrument="Gold", decision_horizon=Horizon.EXECUTION,
            signals_by_family=self.SIGNALS, evaluated_at=NOW,
        )
        self.assertNotEqual(
            tactical.record.as_record()["families"],
            execution_eval.record.as_record()["families"],
            "identical family readings across horizons means the horizons are "
            "one claim wearing two labels",
        )
        # Not merely different records: a different ANSWER. At Tactical the
        # monthly activity leg votes and carries the read bullish; at Execution
        # it is correctly withheld and what remains points the other way.
        self.assertIs(tactical.direction, Direction.BULLISH)
        self.assertIs(execution_eval.direction, Direction.BEARISH)

    def test_the_frequency_rule_is_actually_invoked(self):
        """H1b: the guard existed and was called from no production path."""
        self.assertFalse(
            horizon_compatible(SeriesFrequency.MONTHLY, Horizon.EXECUTION)
        )
        record = run_shadow_evaluation(
            instrument="Gold", decision_horizon=Horizon.EXECUTION,
            signals_by_family=self.SIGNALS, evaluated_at=NOW,
        ).record.as_record()
        self.assertTrue(record["evidence_provenance"]["horizon_enforced"])
        activity = next(
            f for f in record["families"] if f["family"] == "macro_activity"
        )
        self.assertTrue(activity["is_horizon_excluded"])
        self.assertEqual(activity["decision_horizon"], "execution")

    def test_every_member_declares_a_frequency_with_its_basis(self):
        for family in registry.VOTING_FAMILIES:
            self.assertEqual(len(family.member_specs), len(family.members), family.key)
            for spec in family.member_specs:
                self.assertIsInstance(spec.frequency, SeriesFrequency)
                self.assertTrue(spec.frequency_basis.strip(), spec.key)

    def test_no_horizon_filter_means_no_exclusion(self):
        reading = families.evaluate_family(registry.MACRO_ACTIVITY, self.SIGNALS[
            "macro_activity"], None)
        self.assertEqual(reading.horizon_excluded_members, ())
        self.assertIs(reading.direction, Direction.BULLISH)


# ===========================================================================
# The null benchmark itself
# ===========================================================================
class TestNullBenchmark(unittest.TestCase):
    def test_it_is_deterministic(self):
        self.assertEqual(
            run_null_benchmark(samples=500).as_record(),
            run_null_benchmark(samples=500).as_record(),
        )

    def test_the_baselines_bracket_the_family(self):
        result = run_null_benchmark(samples=3000)
        flat = result.result(Baseline.ALWAYS_FLAT)
        random_direction = result.result(Baseline.RANDOM_DIRECTION)
        noise = result.result(Baseline.PURE_NOISE)
        self.assertEqual(flat.directional_rate, 0.0)
        self.assertEqual(random_direction.directional_rate, 1.0)
        self.assertLess(noise.strong_rate, random_direction.strong_rate)
        self.assertGreater(noise.strong_rate, flat.strong_rate)

    def test_it_returns_no_recommended_parameter(self):
        """It is a control, not a calibration engine."""
        record = run_null_benchmark(samples=200).as_record()
        for forbidden in ("recommended", "best", "optimal", "fitted", "tuned"):
            self.assertNotIn(forbidden, str(record).lower(), forbidden)
        self.assertIn("not a calibration", record["disclaimer"])

    def test_it_reads_no_stored_observation(self):
        import inspect

        from apex.b2.evaluation import null_benchmark

        source = inspect.getsource(null_benchmark)
        for forbidden in ("b2_bridge", "production_core", "requests",
                          "ShadowRecord", "cohort"):
            self.assertNotIn(forbidden, source, forbidden)


# ===========================================================================
# Containment and registry honesty
# ===========================================================================
class TestContainmentAndRegistry(unittest.TestCase):
    def test_transmission_registration_is_disabled_by_default(self):
        from apex import b2_bridge

        self.assertFalse(b2_bridge.TRANSMISSION_PREDICTION_REGISTRATION_ENABLED)
        self.assertEqual(
            b2_bridge.register_transmission_prediction(
                shadow.InMemoryShadowStore(), instrument="Gold",
                direction=Direction.BULLISH, now=NOW,
            ),
            b2_bridge.TRANSMISSION_WITHHELD,
        )

    def test_the_prediction_corpus_is_marked_invalid(self):
        from apex.b2.predictions import CORPUS_STATUS, CORPUS_STATUS_REASON

        self.assertEqual(CORPUS_STATUS, "invalid_pre_freeze")
        self.assertIn("never be resolved", CORPUS_STATUS_REASON)

    def test_unwired_components_are_withheld_not_active(self):
        """The registry's own rule, applied consistently."""
        withheld = set(registry.withheld_keys())
        active = set(registry.active_non_voting_keys())
        for key in (
            "macro_thesis_invalidation",
            "transmission_regime_confidence_channel",
            "transmission_prediction_registration",
            "scenario_condition_evaluation",
        ):
            self.assertIn(key, withheld, key)
            self.assertNotIn(key, active, key)

    def test_a_component_registered_active_produces_a_field_in_the_record(self):
        """What would have caught the misclassification in the first place."""
        record = run_shadow_evaluation(
            instrument="Gold", decision_horizon=Horizon.TACTICAL,
            signals_by_family=adapters.build_signals(
                rule_points=0.25, article_count=4, ai_active=True
            ),
            evaluated_at=NOW,
        ).record.as_record()
        self.assertIn("regime_state", registry.active_non_voting_keys())
        self.assertIsNotNone(record["regime"]["regime_state"])
        # ...and the withheld ones produce nothing, which is why they are withheld.
        self.assertIsNone(record["thesis"])

    def test_withheld_components_still_carry_a_blocking_requirement(self):
        for component in registry.WITHHELD_COMPONENTS:
            self.assertTrue(component.reason.strip(), component.key)


# ===========================================================================
# Nothing above weakened the standing safety posture
# ===========================================================================
class TestSafetyPostureUnchanged(unittest.TestCase):
    def test_b2_remains_shadow_and_uncalibrated(self):
        self.assertEqual(shadow.SHADOW_MODE_LABEL, "SHADOW / NON-PRODUCTION / UNCALIBRATED")
        from apex.b2.aggregation import DEFAULT_AGGREGATION

        self.assertFalse(DEFAULT_AGGREGATION.calibrated)

    def test_cross_asset_and_structural_stay_withheld(self):
        from apex import b2_bridge

        self.assertIn("cross_asset_bridge", registry.withheld_keys())
        self.assertNotIn(Horizon.STRUCTURAL, b2_bridge.live_shadow_horizons())

    def test_gates_still_cannot_point(self):
        from apex.b2.enums import GateAction

        for action in GateAction:
            self.assertNotIn(action.value, ("bullish", "bearish"))

    def test_neutral_and_unavailable_remain_distinct(self):
        self.assertIsNot(Direction.FLAT, Direction.UNAVAILABLE)
        self.assertIs(families.classify_signal(0.0), Direction.FLAT)
        self.assertIs(families.classify_signal(None), Direction.UNAVAILABLE)

    def test_confidence_is_still_categorical(self):
        for level in ConfidenceLevel:
            self.assertIsInstance(level.value, int)

    def test_a_family_still_contributes_exactly_once(self):
        reading = families.evaluate_family(
            registry.POLICY_REAL_RATES,
            {
                "policy_rate_momentum": 0.9,
                "real_yield_momentum": 0.9,
                "nominal_yield_momentum": 0.9,
                "inflation_expectations_momentum": 0.9,
            },
        )
        self.assertEqual(reading.contribution_count, 1)
        self.assertIs(reading.strength, FamilyStrength.STRONG)


if __name__ == "__main__":
    unittest.main()
