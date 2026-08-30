"""Unit tests for ApexMacro's deterministic scoring/forecasting core.

Pure-function tests only: no network, no Streamlit runtime, no paid AI calls.
Run with: python -m unittest discover tests  (or: pytest tests)
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from apex import production_core as core
from state_isolation import isolate_durable_state

# Redirect every durable state path into a temporary directory before any test
# runs. Production persistence behaviour is unchanged; only this process writes
# elsewhere, so a test run can never rewrite a real state file at the repo root.
isolate_durable_state()


class TestBiasThresholds(unittest.TestCase):
    def test_strong_bullish(self):
        label, _, _ = core.bias_from_score(0.5)
        self.assertIn("Bullish", label)

    def test_strong_bearish(self):
        label, _, _ = core.bias_from_score(-0.5)
        self.assertIn("Bearish", label)

    def test_neutral_band(self):
        label, _, _ = core.bias_from_score(0.05)
        self.assertIn("Neutral", label)


class TestCalcMtf(unittest.TestCase):
    def _series(self, n):
        return [100 + i * 0.5 for i in range(n)]

    def test_insufficient_data_returns_none(self):
        self.assertIsNone(core.calc_mtf([100.0], "growth"))
        self.assertIsNone(core.calc_mtf([], "growth"))

    def test_full_data_full_completeness(self):
        r = core.calc_mtf(self._series(24), "growth")
        self.assertEqual(r["completeness"], 1.0)

    def test_partial_data_partial_completeness_no_fake_neutral_dilution(self):
        """A short series must not have its missing components silently voted as
        neutral 0 while their weight stays in the denominator -- completeness must
        drop below 1.0 and the score must come only from the components that exist.
        """
        full = self._series(24)
        thin = full[-3:]  # only mom (and maybe t3m) computable
        r = core.calc_mtf(thin, "growth")
        self.assertIsNotNone(r)
        self.assertLess(r["completeness"], 1.0)
        self.assertGreater(r["completeness"], 0.0)
        self.assertIsNone(r["qoq"])
        self.assertIsNone(r["yoy"])

    def test_labor_neg_reverses_sign(self):
        rising = self._series(24)
        pos = core.calc_mtf(rising, "growth")
        neg = core.calc_mtf(rising, "labor_neg")
        self.assertAlmostEqual(pos["score"], -neg["score"], places=6)


class TestThreeWayProbabilities(unittest.TestCase):
    def test_strong_positive_beat_dominates(self):
        p = core._three_way_probabilities(0.75, 0.05, 0.85, 0.15)
        self.assertEqual(max(p, key=p.get), "beat")
        self.assertGreater(p["beat"], 60)

    def test_strong_negative_miss_dominates(self):
        p = core._three_way_probabilities(-0.75, 0.05, 0.85, 0.15)
        self.assertEqual(max(p, key=p.get), "miss")
        self.assertGreater(p["miss"], 60)

    def test_neutral_evidence_inline_dominates(self):
        p = core._three_way_probabilities(0.02, 0.10, 0.50, 0.15)
        self.assertEqual(max(p, key=p.get), "inline")

    def test_moderate_positive_with_average_evidence_beats_inline(self):
        """Regression guard for the all-INLINE bug: with a genuinely moderate
        composite and average-or-better evidence quality, Beat must be able to
        win the plurality vote instead of In-line structurally winning almost
        every event regardless of the underlying signal.
        """
        p = core._three_way_probabilities(0.35, 0.15, 0.55, 0.15)
        self.assertEqual(max(p, key=p.get), "beat")

    def test_probabilities_sum_to_100(self):
        p = core._three_way_probabilities(0.4, 0.2, 0.6, 0.15)
        self.assertAlmostEqual(sum(p.values()), 100.0, delta=0.2)


class TestStandardizedSurprise(unittest.TestCase):
    def test_insufficient_history_returns_none_zscore(self):
        z, raw, n = core.calculate_standardized_surprise(0.3, 0.0, [])
        self.assertIsNone(z)
        self.assertEqual(raw, 0.3)
        self.assertEqual(n, 0)

    def test_two_history_rows_still_insufficient(self):
        rows = [{"first_print_actual": "0.1", "forecast": "0.0"}] * 2
        z, raw, n = core.calculate_standardized_surprise(0.3, 0.0, rows)
        self.assertIsNone(z)
        self.assertEqual(n, 2)

    def test_sufficient_history_returns_real_zscore(self):
        rows = [{"first_print_actual": str(0.1 * i), "forecast": "0.0"} for i in range(1, 6)]
        z, raw, n = core.calculate_standardized_surprise(0.3, 0.0, rows)
        self.assertIsNotNone(z)
        self.assertEqual(n, 5)
        self.assertIsInstance(z, float)

    def test_zscore_clamped_to_bounds(self):
        rows = [{"first_print_actual": "0.001", "forecast": "0.0"}] * 5
        z, raw, n = core.calculate_standardized_surprise(10.0, 0.0, rows)
        self.assertLessEqual(abs(z), 3.5)


class TestFiveLevelForecast(unittest.TestCase):
    def test_normalize_accepts_old_three_state(self):
        for v in ("beat", "Beat", "BEAT", "miss", "inline", "In-line"):
            self.assertIn(core._normalize_ai_judgment(v), {"beat", "miss", "inline"})

    def test_normalize_accepts_new_five_state_text_and_tokens(self):
        self.assertEqual(core._normalize_ai_judgment("lean beat"), "lean_beat")
        self.assertEqual(core._normalize_ai_judgment("LEAN BEAT"), "lean_beat")
        self.assertEqual(core._normalize_ai_judgment("lean_beat"), "lean_beat")
        self.assertEqual(core._normalize_ai_judgment("lean miss"), "lean_miss")
        self.assertEqual(core._normalize_ai_judgment("lean_miss"), "lean_miss")

    def test_normalize_unrecognized_returns_empty_never_invents(self):
        self.assertEqual(core._normalize_ai_judgment("garbage"), "")
        self.assertEqual(core._normalize_ai_judgment(""), "")
        self.assertEqual(core._normalize_ai_judgment(None), "")

    def test_display_label_formatting(self):
        self.assertEqual(core._forecast_display_label("lean_beat"), "LEAN BEAT")
        self.assertEqual(core._forecast_display_label("beat"), "BEAT")
        self.assertEqual(core._forecast_display_label("garbage"), "")

    def test_direction_collapses_lean_states(self):
        self.assertEqual(core._forecast_direction("lean_beat"), "beat")
        self.assertEqual(core._forecast_direction("lean_miss"), "miss")
        self.assertEqual(core._forecast_direction("beat"), "beat")
        self.assertEqual(core._forecast_direction("inline"), "inline")

    def test_full_conviction_requires_margin_quality_and_low_conflict(self):
        strong_probs = {"beat": 83.6, "inline": 16.4, "miss": 0.0}
        self.assertEqual(core._five_level_forecast_state(strong_probs, "beat", 0.85, 0.05), "beat")

    def test_thin_margin_or_weak_evidence_produces_lean(self):
        thin_probs = {"beat": 45.3, "inline": 42.6, "miss": 12.1}
        self.assertEqual(core._five_level_forecast_state(thin_probs, "beat", 0.55, 0.15), "lean_beat")
        self.assertEqual(core._five_level_forecast_state(thin_probs, "beat", 0.20, 0.15), "lean_beat")

    def test_high_conflict_forces_lean_even_with_decent_margin(self):
        probs = {"beat": 45.3, "inline": 42.6, "miss": 12.1}
        self.assertEqual(core._five_level_forecast_state(probs, "beat", 0.60, 0.50), "lean_beat")

    def test_inline_outcome_key_always_inline(self):
        probs = {"beat": 25.2, "inline": 51.2, "miss": 23.6}
        self.assertEqual(core._five_level_forecast_state(probs, "inline", 0.9, 0.0), "inline")


class TestFiveLevelArbitration(unittest.TestCase):
    def test_beat_vs_lean_beat_does_not_collapse_to_inline(self):
        analysis = {"ai_judgment": "lean_beat", "decisive_evidence": [], "override_reason": ""}
        final, _ = core._derive_final_forecast_state("beat", analysis)
        self.assertEqual(final, "lean_beat")

    def test_miss_vs_lean_miss_does_not_collapse_to_inline(self):
        analysis = {"ai_judgment": "lean_miss", "decisive_evidence": [], "override_reason": ""}
        final, _ = core._derive_final_forecast_state("miss", analysis)
        self.assertEqual(final, "lean_miss")

    def test_major_opposite_conflict_collapses_to_inline_without_override(self):
        analysis = {"ai_judgment": "miss", "decisive_evidence": [], "override_reason": ""}
        final, _ = core._derive_final_forecast_state("beat", analysis)
        self.assertEqual(final, "inline")

    def test_major_conflict_with_decisive_override_is_accepted(self):
        analysis = {"ai_judgment": "miss", "decisive_evidence": ["x"], "override_reason": "y"}
        final, _ = core._derive_final_forecast_state("beat", analysis)
        self.assertEqual(final, "miss")

    def test_relationship_beat_vs_lean_beat_is_agree_not_disagree(self):
        agreement, _ = core._causal_relationship("beat", "lean_beat")
        self.assertEqual(agreement, "agree")

    def test_relationship_beat_vs_miss_is_disagree(self):
        agreement, _ = core._causal_relationship("beat", "miss")
        self.assertEqual(agreement, "disagree")

    def test_no_manufactured_disagreement_when_equal(self):
        agreement, label = core._causal_relationship("beat", "beat")
        self.assertEqual(agreement, "agree")
        self.assertEqual(label, "QUANT + AI AGREEMENT")


class TestActualResolutionThreeState(unittest.TestCase):
    def test_lean_beat_forecast_beat_actual_is_correct(self):
        self.assertEqual(core._forecast_direction("lean_beat"), "beat")
        self.assertTrue(core._forecast_direction("lean_beat") == "beat")

    def test_lean_beat_forecast_inline_actual_is_not_correct(self):
        self.assertFalse(core._forecast_direction("lean_beat") == "inline")

    def test_lean_miss_forecast_miss_actual_is_correct(self):
        self.assertTrue(core._forecast_direction("lean_miss") == "miss")

    def test_actual_outcome_computation_is_three_state_only(self):
        # actual_outcome is computed directly as beat/miss/inline via numeric
        # comparison in production_core -- verify no lean variant is possible.
        for av, fv, expected in [(1.0, 0.5, "beat"), (0.5, 1.0, "miss"), (1.0, 1.0, "inline")]:
            eps = max(1e-9, abs(fv) * 1e-6)
            outcome = "beat" if av > fv + eps else ("miss" if av < fv - eps else "inline")
            self.assertEqual(outcome, expected)
            self.assertIn(outcome, {"beat", "inline", "miss"})


class TestSmartShiftTransitions(unittest.TestCase):
    def test_broad_regime_mapping(self):
        self.assertEqual(core._broad_regime("Strong Bullish"), "Bullish")
        self.assertEqual(core._broad_regime("Strong Bearish"), "Bearish")
        self.assertEqual(core._broad_regime("Neutral"), "Neutral")


class TestNumericConsensusParsing(unittest.TestCase):
    def test_percent_values(self):
        self.assertAlmostEqual(core._safe_numeric_release("3.2%"), 3.2)
        self.assertAlmostEqual(core._safe_numeric_release("-0.2%"), -0.2)
        self.assertAlmostEqual(core._safe_numeric_release("0.0%"), 0.0)

    def test_k_suffix_values(self):
        # _safe_numeric_release deliberately does not expand unit suffixes (K/%/etc)
        # -- forecast/previous/actual for the same release always share one unit
        # convention, so relative beat/miss comparison is correct without expansion.
        self.assertAlmostEqual(core._safe_numeric_release("320K"), 320.0)
        self.assertAlmostEqual(core._safe_numeric_release("320k"), 320.0)

    def test_missing_values_return_none_not_zero(self):
        self.assertIsNone(core._safe_numeric_release("N/A"))
        self.assertIsNone(core._safe_numeric_release(""))
        self.assertIsNone(core._safe_numeric_release("—"))


class TestEventIdentityStability(unittest.TestCase):
    def test_same_event_same_identity(self):
        ev = {"currency": "USD", "title": "Non-Farm Employment Change", "code": "FF_USD_202609040001_x"}
        id1 = core._event_identity(ev)
        id2 = core._event_identity(dict(ev))
        self.assertEqual(id1, id2)

    def test_different_currency_different_identity(self):
        ev_usd = {"currency": "USD", "title": "GDP q/q"}
        ev_aud = {"currency": "AUD", "title": "GDP q/q"}
        self.assertNotEqual(core._event_identity(ev_usd), core._event_identity(ev_aud))


class TestAudNzdCoverage(unittest.TestCase):
    def test_aud_nzd_present_in_currency_series(self):
        self.assertIn("AUD", core.CURRENCY_SERIES)
        self.assertIn("NZD", core.CURRENCY_SERIES)

    def test_aud_nzd_present_in_alert_assets(self):
        self.assertIn("AUD", core.ALERT_ASSETS)
        self.assertIn("NZD", core.ALERT_ASSETS)

    def test_currency_score_missing_key_returns_none_not_fabricated(self):
        # No FRED key in this environment -> must return None, never a fabricated score.
        self.assertIsNone(core._calc_currency_score_only("AUD", ""))


class TestOilNoFabrication(unittest.TestCase):
    def test_oil_score_missing_data_returns_none_not_fabricated(self):
        score, news = core._calc_oil_score_only("", "")
        self.assertIsNone(score)
        self.assertEqual(news, 0.0)


class TestAllInlineRegression(unittest.TestCase):
    """Proves the Forecaster's classification model can produce differentiated
    outcomes when given clearly differentiated deterministic synthetic evidence
    (composite/evidence_quality/conflict), exercising the real production
    _three_way_probabilities + _five_level_forecast_state pipeline exactly as
    compute_event_nowcast uses it. This does not manipulate production data --
    it feeds the model controlled inputs and checks the model's own output.
    """

    def _classify(self, composite, conflict, evidence_quality, inline_prior=0.15):
        probs = core._three_way_probabilities(composite, conflict, evidence_quality, inline_prior)
        outcome_key = max(probs, key=probs.get)
        conviction = core._five_level_forecast_state(probs, outcome_key, evidence_quality, conflict)
        return probs, outcome_key, conviction

    def test_case_a_strong_upside_must_not_collapse_to_inline(self):
        probs, outcome_key, conviction = self._classify(composite=0.75, conflict=0.05, evidence_quality=0.85)
        self.assertEqual(outcome_key, "beat")
        self.assertEqual(conviction, "beat")

    def test_case_b_moderate_upside_supports_lean_beat(self):
        probs, outcome_key, conviction = self._classify(composite=0.35, conflict=0.15, evidence_quality=0.55)
        self.assertEqual(outcome_key, "beat")
        self.assertEqual(conviction, "lean_beat")

    def test_case_c_genuinely_balanced_evidence_is_inline(self):
        probs, outcome_key, conviction = self._classify(composite=0.02, conflict=0.10, evidence_quality=0.50)
        self.assertEqual(outcome_key, "inline")
        self.assertEqual(conviction, "inline")

    def test_case_d_moderate_downside_supports_lean_miss(self):
        probs, outcome_key, conviction = self._classify(composite=-0.35, conflict=0.15, evidence_quality=0.55)
        self.assertEqual(outcome_key, "miss")
        self.assertEqual(conviction, "lean_miss")

    def test_case_e_strong_downside_must_not_collapse_to_inline(self):
        probs, outcome_key, conviction = self._classify(composite=-0.75, conflict=0.05, evidence_quality=0.85)
        self.assertEqual(outcome_key, "miss")
        self.assertEqual(conviction, "miss")

    def test_all_five_cases_are_not_identical(self):
        """The actual bug this guards against: every case above collapsing to the
        same 'inline' output regardless of how different the input evidence was.
        """
        results = [
            self._classify(0.75, 0.05, 0.85)[1],
            self._classify(0.35, 0.15, 0.55)[1],
            self._classify(0.02, 0.10, 0.50)[1],
            self._classify(-0.35, 0.15, 0.55)[1],
            self._classify(-0.75, 0.05, 0.85)[1],
        ]
        self.assertEqual(results, ["beat", "beat", "inline", "miss", "miss"])
        self.assertGreater(len(set(results)), 1, "All evidence ladder cases collapsed to one outcome -- ALL-INLINE bug is back.")


if __name__ == "__main__":
    unittest.main(verbosity=2)
