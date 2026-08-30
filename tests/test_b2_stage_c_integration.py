"""Stage C integration tests: asset modules inside the live shadow path.

Imports ``apex.production_core``, so durable-state isolation is installed
first. Persistence is exercised through an in-memory store; the real backend is
never written to.

The questions under test are whether a Stage C observation is fully tagged and
persisted, whether the asset module can affect anything it must not, and
whether production is provably untouched.
"""
from __future__ import annotations

import ast
import inspect
import json
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
import pandas as pd
from apex import production_core as core
from state_isolation import isolate_durable_state

isolate_durable_state()

from apex import b2_bridge
from apex.b2 import modules, registry, shadow
from apex.b2.enums import ConfidenceLevel, Direction, Horizon

NOW = datetime(2026, 8, 31, 12, 0, 0, tzinfo=timezone.utc)

_ENTRY_PLAN = {
    "invalidation": 3280.0,
    "zone_low": 3320.0,
    "zone_high": 3340.0,
    "current_analysis_price": 3330.0,
    "atr": 12.0,
    "atr_ratio": 1.05,
    "volatility_regime": "normal",
    "status": "IN ZONE — WAIT CONFIRMATION",
    "event_points": 10,
    "opportunity_quality": {
        "room_to_opposing_structure_atr": 6.0,
        "asymmetry_ratio": 2.2,
    },
}

_TACTICAL = {
    "ret_15m": 0.0021,
    "ret_1h": 0.0044,
    "ret_4h": 0.0090,
    "structure": "Upside Breakout",
    "volatility_scale": 0.0012,
    "entry_plan": _ENTRY_PLAN,
}

_NEWS = {
    "scores": {"Gold": 0.22, "Oil": 0.15, "Nasdaq": 0.18, "EUR": 0.12, "USD": 0.05},
    "gold_rule_points": 0.22,
    "gold_ai_points": 0.11,
}

_COMPOSITE = {
    "macro_score": 0.31,
    "rows": [
        {"cat": "rate", "weight": 2.0, "score": -0.35},
        {"cat": "inflation", "weight": 2.0, "score": 0.42},
        {"cat": "labor_neg", "weight": 1.8, "score": 0.10},
        {"cat": "growth", "weight": 1.5, "score": 0.28},
    ],
}

_CALENDAR = [
    {
        "title": "CPI m/m",
        "country": "USD",
        "impact": "High",
        "date": (NOW + timedelta(minutes=95)).isoformat(),
    }
]


class _PatchProduction:
    """Patch every production function the observation path reads."""

    def __init__(self, **overrides):
        self.overrides = overrides

    def __enter__(self):
        o = self.overrides
        self._patchers = [
            mock.patch.object(core, "fetch_all_instant_news", return_value=[]),
            mock.patch.object(
                core, "analyze_news_rule_based", return_value=dict(_NEWS)
            ),
            mock.patch.object(
                core, "_calc_gold_score_only", return_value=(0.44, "1.18%", 0.22)
            ),
            mock.patch.object(core, "_calc_oil_score_only", return_value=(0.30, 0.15)),
            mock.patch.object(core, "_calc_ndx_score_only", return_value=(0.25, 0.18)),
            mock.patch.object(core, "_calc_currency_score_only", return_value=0.20),
            mock.patch.object(core, "_oil_price_momentum_score", return_value=0.35),
            mock.patch.object(
                core, "compute_composite", return_value=dict(_COMPOSITE)
            ),
            mock.patch.object(
                core,
                "compute_tactical_move",
                return_value=o.get("tactical", dict(_TACTICAL)),
            ),
            mock.patch.object(core, "fetch_fred", return_value=o.get("fred", None)),
            mock.patch.object(
                core,
                "fetch_forex_factory_calendar_rolling",
                return_value=o.get("calendar", list(_CALENDAR)),
            ),
        ]
        for patcher in self._patchers:
            patcher.start()
        return self

    def __exit__(self, *exc):
        for patcher in reversed(self._patchers):
            patcher.stop()
        return False


def _reset():
    b2_bridge._HANDLED_BUCKETS.clear()
    for name in b2_bridge.HOOK_COUNTERS:
        b2_bridge.HOOK_STATS[name] = 0


def _observe(instrument="Gold", store=None, now=NOW, **overrides):
    store = store or shadow.InMemoryShadowStore()
    with _PatchProduction(**overrides):
        outcome = b2_bridge.observe_instrument(
            instrument, "FAKE_KEY", "chan", store=store, now=now
        )
    return outcome, store


def _record(store):
    payload = store.load(b2_bridge.SHADOW_LOG_STATE_ID, None)
    return payload["records"][-1]


# ---------------------------------------------------------------------------
# Asset tagging
# ---------------------------------------------------------------------------
class TestAssetTagging(unittest.TestCase):
    def setUp(self):
        _reset()

    def test_record_carries_every_required_tag(self):
        _, store = _observe("Gold")
        record = _record(store)
        for key in (
            "instrument",
            "asset_module",
            "horizon",
            "schema_version",
            "mode",
            "evaluated_at",
        ):
            self.assertIn(key, record, key)

    def test_mode_is_labelled_shadow_non_production_uncalibrated(self):
        _, store = _observe("Gold")
        self.assertEqual(
            _record(store)["mode"], "SHADOW / NON-PRODUCTION / UNCALIBRATED"
        )
        self.assertEqual(
            store.load(b2_bridge.SHADOW_LOG_STATE_ID, None)["mode"],
            "SHADOW / NON-PRODUCTION / UNCALIBRATED",
        )

    def test_each_instrument_tags_its_own_module(self):
        expected = {
            "Gold": "gold_module_v1",
            "Oil": "oil_module_v1",
            "NDX": "nasdaq_module_v1",
            "EUR": "fx_module_v1",
        }
        for instrument, module_key in expected.items():
            _reset()
            _, store = _observe(instrument)
            record = _record(store)
            self.assertEqual(record["instrument"], instrument)
            self.assertEqual(record["asset_module"], module_key, instrument)

    def test_asset_module_reading_is_embedded_in_the_record(self):
        _, store = _observe("Gold")
        reading = _record(store)["asset_module_reading"]
        self.assertIsNotNone(reading)
        self.assertEqual(reading["asset_module"], "gold_module_v1")
        self.assertEqual(reading["role"], "asset_specific_module")
        self.assertFalse(reading["contributes_evidence"])
        self.assertEqual(len(reading["drivers"]), 3)
        for driver in reading["drivers"]:
            self.assertFalse(driver["contributes_vote"])
            self.assertIn("transmission_state", driver)
            self.assertTrue(driver["rationale"].strip())

    def test_records_remain_json_serialisable(self):
        _, store = _observe("Gold")
        json.dumps(store.load(b2_bridge.SHADOW_LOG_STATE_ID, None))


# ---------------------------------------------------------------------------
# The module cannot influence anything it must not
# ---------------------------------------------------------------------------
class TestModuleCannotAffectTheCore(unittest.TestCase):
    def setUp(self):
        _reset()

    def test_voting_families_are_unchanged_by_the_module(self):
        _, store = _observe("Gold")
        record = _record(store)
        self.assertEqual(len(record["families"]), 5)
        for family in record["families"]:
            self.assertEqual(family["contribution_count"], 1)

    def test_a_failing_module_does_not_block_the_record(self):
        with mock.patch.object(
            modules.gold, "evaluate", side_effect=RuntimeError("module boom")
        ):
            outcome, store = _observe("Gold")
        self.assertEqual(outcome, "written")
        record = _record(store)
        self.assertIsNone(record["asset_module"])
        self.assertIsNone(record["asset_module_reading"])

    def test_the_module_does_not_change_the_resolved_direction(self):
        _reset()
        _, with_module = _observe("Gold")
        direction_with = _record(with_module)["decision"]["direction"]
        _reset()
        with mock.patch.object(
            modules.gold, "evaluate", side_effect=RuntimeError("module boom")
        ):
            _, without_module = _observe("Gold")
        direction_without = _record(without_module)["decision"]["direction"]
        self.assertEqual(direction_with, direction_without)

    def test_the_module_does_not_change_the_decision_state(self):
        _reset()
        _, with_module = _observe("Gold")
        state_with = _record(with_module)["decision_state"]
        _reset()
        with mock.patch.object(
            modules.gold, "evaluate", side_effect=RuntimeError("module boom")
        ):
            _, without_module = _observe("Gold")
        self.assertEqual(state_with, _record(without_module)["decision_state"])

    def test_the_module_does_not_change_any_confidence_dimension(self):
        _reset()
        _, with_module = _observe("Gold")
        conf_with = _record(with_module)["confidence"]
        _reset()
        with mock.patch.object(
            modules.gold, "evaluate", side_effect=RuntimeError("module boom")
        ):
            _, without_module = _observe("Gold")
        self.assertEqual(conf_with, _record(without_module)["confidence"])

    def test_the_bridge_is_the_only_module_importing_stage_c_modules(self):
        """Stage C reaches production through exactly one sanctioned file.

        The bridge importing the modules is its whole purpose. What must not
        happen is a SECOND importer appearing anywhere outside apex/b2, which
        would be a new route for Stage C output into production.
        """
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        importers: list[str] = []
        for folder, _dirs, files in os.walk(root):
            if any(
                part in folder
                for part in ("_backup_", "_baseline_", ".git", "__pycache__", "tests")
            ):
                continue
            # apex/b2 is the architecture itself; the question is who OUTSIDE it
            # reaches in.
            if os.path.join("apex", "b2") in folder:
                continue
            for filename in files:
                if not filename.endswith(".py"):
                    continue
                path = os.path.join(folder, filename)
                with open(path, "r", encoding="utf-8") as handle:
                    try:
                        tree = ast.parse(handle.read())
                    except SyntaxError:
                        continue
                for node in ast.walk(tree):
                    if isinstance(node, ast.ImportFrom) and "b2.modules" in (
                        node.module or ""
                    ):
                        importers.append(os.path.basename(path))
        self.assertEqual(sorted(set(importers)), ["b2_bridge.py"])


# ---------------------------------------------------------------------------
# Preserved Stage A/B behaviour through the Stage C path
# ---------------------------------------------------------------------------
class TestPreservedBehaviour(unittest.TestCase):
    def setUp(self):
        _reset()

    def test_five_confidence_dimensions_survive_and_stay_categorical(self):
        _, store = _observe("Gold")
        confidence = _record(store)["confidence"]
        for key in (
            "macro_confidence",
            "technical_confidence",
            "execution_confidence",
            "regime_confidence",
            "data_confidence",
        ):
            self.assertIn(confidence[key], {"LOW", "MODERATE", "HIGH"}, key)

    def test_horizons_remain_separated(self):
        _, store = _observe("Gold")
        record = _record(store)
        self.assertEqual(record["horizon"], "tactical")
        self.assertEqual(record["asset_module_reading"]["horizon"], "tactical")
        self.assertIn(record["claim"]["horizon"], {"tactical"})

    def test_cross_asset_remains_withheld_in_every_record(self):
        for instrument in ("Gold", "Oil", "NDX", "EUR"):
            _reset()
            _, store = _observe(instrument)
            self.assertEqual(_record(store)["cross_asset"]["status"], "withheld")
            self.assertIsNone(_record(store)["cross_asset"]["relationship_stability"])

    def test_execution_stays_blocked_when_invalidation_is_unavailable(self):
        plan = dict(_ENTRY_PLAN)
        plan["invalidation"] = None
        tactical = dict(_TACTICAL)
        tactical["entry_plan"] = plan
        _, store = _observe("Gold", tactical=tactical)
        execution = _record(store)["execution"]
        self.assertFalse(execution["invalidation_defined"])
        self.assertTrue(execution["blocked"])
        self.assertEqual(execution["execution_confidence"], "LOW")

    def test_scenarios_remain_present_and_uncalibrated(self):
        _, store = _observe("Gold")
        scenarios = _record(store)["scenarios"]
        for kind in ("base", "alternative", "tail"):
            self.assertIn(scenarios[kind]["band"], {"likely", "possible", "unlikely"})
        self.assertTrue(scenarios["conditions"])

    def test_unavailable_evidence_is_reported_not_filled_in(self):
        # fetch_fred is patched to None, so the yield legs genuinely did not arrive.
        _, store = _observe("Gold")
        reading = _record(store)["asset_module_reading"]
        real_rate = next(
            d for d in reading["drivers"] if d["driver"] == "real_rate_transmission"
        )
        self.assertEqual(real_rate["transmission_state"], "unavailable")
        self.assertIsNone(real_rate["value"])
        self.assertIn("real_rate_transmission", reading["unavailable_drivers"])

    def test_dormant_evidence_is_declared_on_every_module(self):
        for instrument in ("Gold", "Oil", "NDX", "EUR"):
            _reset()
            _, store = _observe(instrument)
            self.assertTrue(
                _record(store)["asset_module_reading"]["dormant_drivers"], instrument
            )


# ---------------------------------------------------------------------------
# Event risk: true timing
# ---------------------------------------------------------------------------
class TestEventRiskTiming(unittest.TestCase):
    def setUp(self):
        _reset()

    def test_true_minutes_come_from_the_calendar(self):
        _, store = _observe("Gold")
        timing = _record(store)["event_timing"]
        self.assertEqual(timing["event_timing_source"], "calendar_timestamp")
        self.assertAlmostEqual(timing["minutes_to_event"], 95.0, places=1)
        self.assertEqual(timing["event_title"], "CPI m/m")

    def test_the_gate_still_fires_on_real_timing(self):
        _, store = _observe("Gold")
        record = _record(store)
        self.assertIsNotNone(record["event_risk_state"])
        self.assertIn(record["event_risk_state"], {"normal", "elevated", "critical"})

    def test_an_imminent_release_vetoes_execution(self):
        calendar = [
            {
                "title": "NFP",
                "country": "USD",
                "impact": "High",
                "date": (NOW + timedelta(minutes=4)).isoformat(),
            }
        ]
        _, store = _observe("Gold", calendar=calendar)
        record = _record(store)
        self.assertEqual(record["event_risk_state"], "critical")
        self.assertTrue(record["execution"]["blocked"])

    def test_missing_calendar_is_reported_unavailable_not_clear(self):
        _, store = _observe("Gold", calendar=[])
        timing = _record(store)["event_timing"]
        self.assertIsNone(timing["minutes_to_event"])
        self.assertEqual(timing["event_timing_source"], "unavailable")

    def test_timing_is_never_fabricated_when_the_fetch_fails(self):
        with mock.patch.object(
            core, "fetch_forex_factory_calendar_rolling", side_effect=RuntimeError("down")
        ):
            timing = b2_bridge._event_timing_for("Gold", NOW)
        self.assertIsNone(timing.minutes)
        self.assertEqual(timing.source, "unavailable")

    def test_no_tier_midpoint_fallback_remains(self):
        source = inspect.getsource(b2_bridge)
        self.assertNotIn("_EVENT_POINTS_TO_MINUTES", source)


# ---------------------------------------------------------------------------
# Persistence, duplicates and fail-open
# ---------------------------------------------------------------------------
class TestShadowPersistence(unittest.TestCase):
    def setUp(self):
        _reset()

    def test_only_the_two_approved_state_ids_are_written(self):
        _, store = _observe("Gold")
        self.assertTrue(
            set(store._data)
            <= {b2_bridge.SHADOW_LOG_STATE_ID, b2_bridge.PREDICTION_LOG_STATE_ID}
        )

    def test_duplicate_suppression_still_holds_with_modules_active(self):
        store = shadow.InMemoryShadowStore()
        first, _ = _observe("Gold", store=store, now=NOW)
        b2_bridge._HANDLED_BUCKETS.clear()
        second, _ = _observe("Gold", store=store, now=NOW + timedelta(minutes=30))
        self.assertEqual(first, "written")
        self.assertEqual(second, "duplicate_skipped")
        self.assertEqual(
            len(store.load(b2_bridge.SHADOW_LOG_STATE_ID, None)["records"]), 1
        )

    def test_diagnostic_outcomes_are_recorded(self):
        _, store = _observe("Gold")
        diagnostics = store.load(b2_bridge.SHADOW_LOG_STATE_ID, None)["diagnostics"]
        self.assertEqual(diagnostics["written"], 1)
        for name in (
            "attempted",
            "written",
            "duplicate_skipped",
            "insufficient_data_skipped",
            "exception_swallowed",
        ):
            self.assertIn(name, b2_bridge.HOOK_COUNTERS)

    def test_a_module_failure_is_fail_open_through_run_shadow_observation(self):
        store = shadow.InMemoryShadowStore()
        with mock.patch.object(
            modules.gold, "evaluate", side_effect=RuntimeError("module boom")
        ):
            with _PatchProduction():
                outcomes = b2_bridge.run_shadow_observation(
                    "FAKE_KEY", "chan", store=store, now=NOW
                )
        self.assertEqual(outcomes["Gold"], "written")

    def test_a_prediction_log_failure_does_not_lose_the_observation(self):
        class HalfBrokenStore(shadow.InMemoryShadowStore):
            def save(self, state_id, payload):
                if state_id == b2_bridge.PREDICTION_LOG_STATE_ID:
                    raise RuntimeError("prediction backend down")
                super().save(state_id, payload)

        store = HalfBrokenStore()
        outcome, _ = _observe("Gold", store=store)
        self.assertEqual(outcome, "written")
        self.assertEqual(
            len(store.load(b2_bridge.SHADOW_LOG_STATE_ID, None)["records"]), 1
        )


# ---------------------------------------------------------------------------
# Transmission predictions
# ---------------------------------------------------------------------------
class TestTransmissionPredictions(unittest.TestCase):
    def setUp(self):
        _reset()

    def test_an_observation_registers_the_modules_chain(self):
        _, store = _observe("Gold")
        payload = store.load(b2_bridge.PREDICTION_LOG_STATE_ID, None)
        self.assertIsNotNone(payload)
        self.assertEqual(len(payload["predictions"]), 1)
        record = payload["predictions"][0]
        self.assertEqual(record["instrument"], "Gold")
        self.assertEqual(record["horizon"], "tactical")
        self.assertTrue(record["steps"])
        for step in record["steps"]:
            self.assertGreater(step["expects_within_hours"], 0)
            self.assertTrue(step["rationale"].strip())

    def test_predictions_are_day_bucketed(self):
        store = shadow.InMemoryShadowStore()
        self.assertEqual(
            b2_bridge.register_transmission_prediction(
                store, instrument="Gold", direction=Direction.BULLISH, now=NOW
            ),
            "registered",
        )
        self.assertEqual(
            b2_bridge.register_transmission_prediction(
                store,
                instrument="Gold",
                direction=Direction.BULLISH,
                now=NOW + timedelta(hours=6),
            ),
            "duplicate_skipped",
        )
        self.assertEqual(
            b2_bridge.register_transmission_prediction(
                store,
                instrument="Gold",
                direction=Direction.BULLISH,
                now=NOW + timedelta(days=1),
            ),
            "registered",
        )
        self.assertEqual(
            len(store.load(b2_bridge.PREDICTION_LOG_STATE_ID, None)["predictions"]), 2
        )

    def test_no_prediction_without_a_directional_thesis(self):
        store = shadow.InMemoryShadowStore()
        self.assertEqual(
            b2_bridge.register_transmission_prediction(
                store, instrument="Gold", direction=Direction.FLAT, now=NOW
            ),
            "no_direction",
        )

    def test_no_prediction_for_an_unregistered_instrument(self):
        store = shadow.InMemoryShadowStore()
        self.assertEqual(
            b2_bridge.register_transmission_prediction(
                store, instrument="NOT_A_MARKET", direction=Direction.BULLISH, now=NOW
            ),
            "no_module",
        )

    def test_outcome_attachment_remains_manual(self):
        source = inspect.getsource(b2_bridge)
        self.assertNotIn("attach_outcome", source)
        self.assertNotIn("pending_steps", source)

    def test_predictions_remain_append_only_and_immutable(self):
        _, store = _observe("Gold")
        payload = store.load(b2_bridge.PREDICTION_LOG_STATE_ID, None)
        self.assertIn("predictions", payload)
        self.assertIn("outcomes", payload)
        self.assertEqual(payload["outcomes"], [])


# ---------------------------------------------------------------------------
# Production is provably untouched
# ---------------------------------------------------------------------------
def _synthetic_ohlc(rows: int = 200, seed: int = 11) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    steps = rng.normal(0.0, 0.0015, rows).cumsum()
    close = 100.0 * np.exp(steps)
    high = close * (1.0 + np.abs(rng.normal(0.0, 0.0008, rows)))
    low = close * (1.0 - np.abs(rng.normal(0.0, 0.0008, rows)))
    open_ = np.r_[close[0], close[:-1]]
    return pd.DataFrame(
        {
            "ts": np.arange(rows) * 300,
            "open": open_,
            "high": np.maximum.reduce([high, open_, close]),
            "low": np.minimum.reduce([low, open_, close]),
            "close": close,
            "volume": np.full(rows, 1000.0),
        }
    )


class TestProductionUnchanged(unittest.TestCase):
    def test_production_core_does_not_import_any_stage_c_module(self):
        tree = ast.parse(inspect.getsource(core))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                self.assertNotIn("modules", node.module or "")
                self.assertNotIn("event_timing", node.module or "")

    def test_production_core_still_has_exactly_one_b2_import(self):
        tree = ast.parse(inspect.getsource(core))
        b2_imports = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and "b2" in (node.module or "")
        ]
        self.assertEqual(len(b2_imports), 1)
        self.assertEqual(
            tuple(a.name for a in b2_imports[0].names), ("run_shadow_observation",)
        )

    def test_production_scoring_functions_are_deterministic_and_module_free(self):
        for func in (
            core._calc_gold_score_only,
            core._calc_oil_score_only,
            core._calc_ndx_score_only,
            core._calc_currency_score_only,
            core.compute_composite,
            core._build_macro_entry_plan,
            core.compute_tactical_move,
            core.bias_from_score,
        ):
            source = inspect.getsource(func)
            self.assertNotIn("asset_module", source, func.__name__)
            self.assertNotIn("b2", source, func.__name__)
            self.assertNotIn("transmission", source, func.__name__)

    def test_entry_plan_output_is_stable(self):
        df = _synthetic_ohlc()
        first = core._build_macro_entry_plan(df, "Bullish", 0.5, "Gold")
        second = core._build_macro_entry_plan(df, "Bullish", 0.5, "Gold")
        self.assertEqual(first["entry_score"], second["entry_score"])
        self.assertEqual(first["zone_low"], second["zone_low"])
        self.assertEqual(first["invalidation"], second["invalidation"])

    def test_tactical_move_output_is_stable(self):
        df = _synthetic_ohlc()
        with mock.patch.object(core, "_fetch_tactical_price_series", return_value=df):
            first = core.compute_tactical_move("Gold", 0.5)
            second = core.compute_tactical_move("Gold", 0.5)
        self.assertEqual(first["score"], second["score"])
        self.assertEqual(first["label"], second["label"])
        self.assertEqual(first["structure"], second["structure"])

    def test_bias_thresholds_are_untouched(self):
        self.assertEqual(core.bias_from_score(0.40)[0], "🚀 Strong Bullish")
        self.assertEqual(core.bias_from_score(0.20)[0], "📈 Moderate Bullish")
        self.assertEqual(core.bias_from_score(0.00)[0], "⚖️ Neutral / Balanced")
        self.assertEqual(core.bias_from_score(-0.20)[0], "📉 Moderate Bearish")
        self.assertEqual(core.bias_from_score(-0.40)[0], "🔻 Strong Bearish")

    def test_smart_shift_transitions_are_untouched(self):
        self.assertEqual(core._broad_regime("🚀 Strong Bullish"), "Bullish")
        self.assertEqual(core._broad_regime("🔻 Strong Bearish"), "Bearish")
        self.assertEqual(core._broad_regime("⚖️ Neutral / Balanced"), "Neutral")

    def test_fx_module_currencies_match_production_configuration(self):
        self.assertEqual(
            set(modules.fx.INSTRUMENTS), set(core.CURRENCY_SERIES.keys())
        )

    def test_the_bridge_adds_no_ai_thread_or_telegram(self):
        source = inspect.getsource(b2_bridge)
        for forbidden in (
            "_post_ai_chat",
            "get_openrouter",
            "send_telegram_alert",
            "threading",
            "Thread",
            "start_background_alert_daemon",
            "start_shared_background_ai_worker",
        ):
            self.assertNotIn(forbidden, source, forbidden)

    def test_asset_modules_are_registered_non_voting(self):
        self.assertEqual(len(registry.VOTING_FAMILIES), 5)
        self.assertEqual(len(registry.asset_module_keys()), 4)


if __name__ == "__main__":
    unittest.main()
