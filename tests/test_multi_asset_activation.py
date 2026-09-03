"""Multi-Asset Shadow Activation tests.

Covers the expansion of live shadow observation from Gold-only to every
instrument with a registered Stage C asset module, and the batching that keeps
that expansion write-neutral.

Imports ``apex.production_core``, so durable-state isolation is installed first.
Persistence runs through an in-memory store; the real backend is never touched.
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

from apex import production_core as core
from state_isolation import isolate_durable_state

isolate_durable_state()

from apex import b2_bridge
from apex.b2 import modules, shadow
from apex.b2.enums import Direction
from apex.b2.modules import fx as fx_module

NOW = datetime(2026, 8, 31, 10, 0, 0, tzinfo=timezone.utc)

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
    "scores": {
        "Gold": 0.22, "Oil": 0.15, "Nasdaq": 0.18,
        "USD": 0.05, "EUR": 0.12, "GBP": 0.10, "CAD": 0.08,
        "JPY": -0.06, "CHF": 0.04, "AUD": 0.09, "NZD": 0.07,
    },
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
        "title": "ISM Manufacturing PMI",
        "country": "USD",
        "impact": "High",
        "date": (NOW + timedelta(minutes=200)).isoformat(),
    }
]


class _PatchProduction:
    """Patch production, counting how many times each entry point is called."""

    def __init__(self, **overrides):
        self.overrides = overrides
        self.calls: dict[str, int] = {}

    def _counting(self, name, value):
        def _fn(*args, **kwargs):
            self.calls[name] = self.calls.get(name, 0) + 1
            return value() if callable(value) else value
        return _fn

    def __enter__(self):
        o = self.overrides
        specs = {
            "fetch_all_instant_news": [],
            "analyze_news_rule_based": lambda: dict(_NEWS),
            "_calc_gold_score_only": (0.44, "1.18%", 0.22),
            "_calc_oil_score_only": (0.30, 0.15),
            "_calc_ndx_score_only": (0.25, 0.18),
            "_calc_currency_score_only": 0.20,
            "_oil_price_momentum_score": 0.35,
            "compute_composite": lambda: dict(_COMPOSITE),
            "compute_tactical_move": lambda: dict(o.get("tactical", _TACTICAL)),
            "fetch_fred": o.get("fred", None),
            "fetch_forex_factory_calendar_rolling": lambda: list(
                o.get("calendar", _CALENDAR)
            ),
        }
        self._patchers = [
            mock.patch.object(core, name, side_effect=self._counting(name, value))
            for name, value in specs.items()
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


class _CountingStore(shadow.InMemoryShadowStore):
    """In-memory store that records how often each state id is read/written."""

    def __init__(self):
        super().__init__()
        self.loads: dict[str, int] = {}
        self.saves: dict[str, int] = {}

    def load(self, state_id, default):
        self.loads[state_id] = self.loads.get(state_id, 0) + 1
        return super().load(state_id, default)

    def save(self, state_id, payload):
        self.saves[state_id] = self.saves.get(state_id, 0) + 1
        super().save(state_id, payload)


def _run(store=None, now=NOW, **overrides):
    """Drive one observation cycle in LEGACY storage mode.

    Storage V2 made append-only rows the default, so these tests are pinned to
    legacy explicitly. That is deliberate and adds coverage rather than
    removing it: every guarantee below (tagging, per-instrument duplicate
    suppression, fail-open isolation, batched read/write cost) is still
    asserted, and it now also proves the ROLLBACK path keeps working. The same
    guarantees are asserted against V2 in tests/test_b2_storage_v2.py.
    """
    store = store or _CountingStore()
    with _PatchProduction(**overrides) as patched:
        with mock.patch.object(
            b2_bridge, "shadow_store_mode", return_value=b2_bridge.STORAGE_MODE_LEGACY
        ):
            outcomes = b2_bridge.run_shadow_observation(
                "FAKE_KEY", "chan", store=store, now=now
            )
    return outcomes, store, patched


def _records(store):
    payload = store.load(b2_bridge.SHADOW_LOG_STATE_ID, None)
    return payload["records"] if payload else []


# ---------------------------------------------------------------------------
# Activated instrument set
# ---------------------------------------------------------------------------
class TestActivatedInstruments(unittest.TestCase):
    def setUp(self):
        _reset()

    def test_default_set_is_derived_from_the_module_registry(self):
        self.assertEqual(
            b2_bridge.shadow_instruments(), modules.registered_instruments()
        )

    def test_all_eleven_registered_instruments_are_activated(self):
        activated = set(b2_bridge.shadow_instruments())
        self.assertEqual(
            activated,
            {"Gold", "Oil", "NDX", "USD", "EUR", "GBP", "CAD", "JPY", "CHF", "AUD", "NZD"},
        )
        self.assertEqual(len(activated), 11)

    def test_the_four_stage_c_modules_are_all_covered(self):
        covered = {
            modules.module_for(i).MODULE_KEY for i in b2_bridge.shadow_instruments()
        }
        self.assertEqual(
            covered,
            {"gold_module_v1", "oil_module_v1", "fx_module_v1", "nasdaq_module_v1"},
        )

    def test_fx_currencies_match_the_actual_module_and_production_config(self):
        activated = set(b2_bridge.shadow_instruments())
        self.assertTrue(set(fx_module.INSTRUMENTS) <= activated)
        self.assertEqual(set(fx_module.INSTRUMENTS), set(core.CURRENCY_SERIES.keys()))

    def test_operator_override_still_works(self):
        with mock.patch.object(core, "get_secret", return_value="Gold"):
            self.assertEqual(b2_bridge.shadow_instruments(), ("Gold",))

    def test_no_duplicate_module_implementations_were_created(self):
        self.assertEqual(len(set(modules.MODULES.values())), 4)


# ---------------------------------------------------------------------------
# Every instrument observes and is tagged
# ---------------------------------------------------------------------------
class TestMultiAssetObservation(unittest.TestCase):
    def setUp(self):
        _reset()

    def test_every_activated_instrument_is_written(self):
        outcomes, store, _ = _run()
        self.assertEqual(len(outcomes), 11)
        self.assertEqual(set(outcomes.values()), {"written"})
        self.assertEqual(len(_records(store)), 11)

    def test_each_record_is_tagged_with_its_own_instrument_and_module(self):
        _, store, _ = _run()
        expected = {
            "Gold": "gold_module_v1",
            "Oil": "oil_module_v1",
            "NDX": "nasdaq_module_v1",
            **{c: "fx_module_v1" for c in fx_module.INSTRUMENTS},
        }
        seen = {r["instrument"]: r["asset_module"] for r in _records(store)}
        self.assertEqual(seen, expected)

    def test_every_record_carries_the_required_tags(self):
        _, store, _ = _run()
        for record in _records(store):
            for key in (
                "instrument", "asset_module", "horizon",
                "evaluated_at", "mode", "schema_version",
            ):
                self.assertIn(key, record, f"{record.get('instrument')}: {key}")
            self.assertEqual(
                record["mode"], "SHADOW / NON-PRODUCTION / UNCALIBRATED"
            )

    def test_each_record_has_a_distinct_identity(self):
        _, store, _ = _run()
        ids = [r["record_id"] for r in _records(store)]
        self.assertEqual(len(ids), len(set(ids)))

    def test_gold_oil_nasdaq_each_produce_a_module_reading(self):
        _, store, _ = _run()
        by_instrument = {r["instrument"]: r for r in _records(store)}
        for instrument in ("Gold", "Oil", "NDX"):
            reading = by_instrument[instrument]["asset_module_reading"]
            self.assertIsNotNone(reading, instrument)
            self.assertEqual(len(reading["drivers"]), 3, instrument)
            self.assertFalse(reading["contributes_evidence"], instrument)

    def test_every_fx_currency_produces_a_module_reading(self):
        _, store, _ = _run()
        by_instrument = {r["instrument"]: r for r in _records(store)}
        for currency in fx_module.INSTRUMENTS:
            reading = by_instrument[currency]["asset_module_reading"]
            self.assertIsNotNone(reading, currency)
            self.assertEqual(reading["asset_module"], "fx_module_v1", currency)
            self.assertEqual(reading["instrument"], currency)
            self.assertTrue(reading["notes"], currency)

    def test_usd_reports_relative_channels_unavailable_as_the_base(self):
        _, store, _ = _run()
        usd = next(r for r in _records(store) if r["instrument"] == "USD")
        states = {
            d["driver"]: d["transmission_state"]
            for d in usd["asset_module_reading"]["drivers"]
        }
        self.assertEqual(states["relative_macro_pressure"], "unavailable")
        self.assertEqual(states["relative_policy_pressure"], "unavailable")

    def test_records_remain_json_serialisable(self):
        _, store, _ = _run()
        json.dumps(store.load(b2_bridge.SHADOW_LOG_STATE_ID, None))


# ---------------------------------------------------------------------------
# Cost: batching and cache reuse
# ---------------------------------------------------------------------------
class TestResourceCost(unittest.TestCase):
    def setUp(self):
        _reset()

    def test_eleven_instruments_cost_one_shadow_write_not_eleven(self):
        _, store, _ = _run()
        self.assertEqual(store.saves.get(b2_bridge.SHADOW_LOG_STATE_ID), 1)

    def test_eleven_instruments_cost_one_shadow_read_not_eleven(self):
        _, store, _ = _run()
        self.assertEqual(store.loads.get(b2_bridge.SHADOW_LOG_STATE_ID), 1)

    def test_a_tick_with_nothing_due_touches_the_store_at_all(self):
        store = _CountingStore()
        _run(store=store)
        reads_after_first = store.loads.get(b2_bridge.SHADOW_LOG_STATE_ID)
        # Second tick in the same hour: everything is already handled.
        outcomes, _, _ = _run(store=store, now=NOW + timedelta(minutes=5))
        self.assertEqual(set(outcomes.values()), {"duplicate_skipped"})
        self.assertEqual(
            store.loads.get(b2_bridge.SHADOW_LOG_STATE_ID),
            reads_after_first,
            "an idle tick must not read the shadow log",
        )

    def test_no_new_ai_entry_point_is_called(self):
        _, _, patched = _run()
        for name in patched.calls:
            self.assertNotIn("openrouter", name.lower())
            self.assertNotIn("ai_", name.lower())

    def test_the_bridge_never_references_an_ai_entry_point(self):
        tree = ast.parse(inspect.getsource(b2_bridge))
        for node in ast.walk(tree):
            if isinstance(
                node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
            ):
                if (
                    node.body
                    and isinstance(node.body[0], ast.Expr)
                    and isinstance(node.body[0].value, ast.Constant)
                    and isinstance(node.body[0].value.value, str)
                ):
                    node.body = node.body[1:]
        source = ast.unparse(tree)
        for forbidden in (
            "_post_ai_chat", "get_openrouter", "get_multi_asset_news_intelligence",
            "start_shared_background_ai_worker", "_run_shared_ai_batch_once",
        ):
            self.assertNotIn(forbidden, source, forbidden)

    def test_news_is_fetched_once_per_instrument_not_more(self):
        _, _, patched = _run()
        # One news read per instrument; caching upstream makes these cheap, and
        # crucially none of them is an AI request.
        self.assertLessEqual(patched.calls.get("fetch_all_instant_news", 0), 11)

    def test_fx_counter_composite_is_always_usd(self):
        counters = {
            fx_module.counter_currency_for(c) for c in fx_module.INSTRUMENTS
        }
        self.assertEqual(counters, {None, "USD"})

    def test_the_daemon_cadence_is_untouched(self):
        source = inspect.getsource(core.start_background_alert_daemon)
        self.assertEqual(source.count("time.sleep("), 1)
        self.assertIn("time.sleep(60)", source)
        self.assertEqual(source.count("threading.Thread"), 1)


# ---------------------------------------------------------------------------
# Duplicate suppression and predictions
# ---------------------------------------------------------------------------
class TestDuplicateAndPredictions(unittest.TestCase):
    def setUp(self):
        _reset()

    def test_duplicate_suppression_is_per_instrument(self):
        store = _CountingStore()
        _run(store=store, now=NOW)
        b2_bridge._HANDLED_BUCKETS.clear()  # simulate a restart
        outcomes, _, _ = _run(store=store, now=NOW + timedelta(minutes=20))
        self.assertEqual(set(outcomes.values()), {"duplicate_skipped"})
        self.assertEqual(len(_records(store)), 11)

    def test_the_next_hour_observes_every_instrument_again(self):
        store = _CountingStore()
        _run(store=store, now=NOW)
        outcomes, _, _ = _run(store=store, now=NOW + timedelta(hours=1))
        self.assertEqual(set(outcomes.values()), {"written"})
        self.assertEqual(len(_records(store)), 22)

    def test_no_predictions_are_registered_for_any_instrument(self):
        # Registration is withheld: every chain stamped each step with the
        # thesis direction, inverting the intermediate legs. A full multi-asset
        # cycle must therefore accumulate nothing.
        _, store, _ = _run()
        self.assertIsNone(store.load(b2_bridge.PREDICTION_LOG_STATE_ID, None))

    def test_prediction_day_bucket_idempotency_holds_across_instruments(self):
        # The bucketing plumbing is unchanged and still covered; only the live
        # path is closed, so it is exercised through the explicit override.
        store = _CountingStore()
        instruments = list(b2_bridge.shadow_instruments())

        def register(moment):
            for instrument in instruments:
                b2_bridge.register_transmission_prediction(
                    store, instrument=instrument, direction=Direction.BULLISH,
                    now=moment, enabled=True,
                )
            return len(store.load(b2_bridge.PREDICTION_LOG_STATE_ID, None)["predictions"])

        first = register(NOW)
        # A later hour on the SAME day must not add new predictions.
        second = register(NOW + timedelta(hours=3))
        self.assertEqual(first, second)
        # The next day does.
        third = register(NOW + timedelta(days=1))
        self.assertGreater(third, second)

    def test_only_the_approved_state_ids_are_ever_written(self):
        # Stricter than before: with registration withheld, an observation
        # writes ONLY the shadow log. Any other id -- a new persistence
        # surface, or a write into an existing production payload -- fails here.
        _, store, _ = _run()
        self.assertEqual(set(store.saves), {b2_bridge.SHADOW_LOG_STATE_ID})
        self.assertLessEqual(
            set(store.saves),
            {b2_bridge.SHADOW_LOG_STATE_ID, b2_bridge.PREDICTION_LOG_STATE_ID},
        )


# ---------------------------------------------------------------------------
# Fail-open isolation
# ---------------------------------------------------------------------------
class TestFailOpenIsolation(unittest.TestCase):
    def setUp(self):
        _reset()

    def test_one_failing_instrument_does_not_stop_the_others(self):
        real_gather = b2_bridge._gather_production_inputs

        def flaky(instrument, fred_key, channel_name):
            if instrument == "Oil":
                raise RuntimeError("Oil is broken")
            return real_gather(instrument, fred_key, channel_name)

        with mock.patch.object(b2_bridge, "_gather_production_inputs", side_effect=flaky):
            outcomes, store, _ = _run()

        self.assertEqual(outcomes["Oil"], "exception_swallowed")
        for instrument in ("Gold", "NDX", "EUR", "USD"):
            self.assertEqual(outcomes[instrument], "written", instrument)
        written = {r["instrument"] for r in _records(store)}
        self.assertNotIn("Oil", written)
        self.assertEqual(len(written), 10)

    def test_a_failing_module_still_writes_the_other_instruments(self):
        with mock.patch.object(
            modules.gold, "evaluate", side_effect=RuntimeError("gold module boom")
        ):
            outcomes, store, _ = _run()
        self.assertEqual(set(outcomes.values()), {"written"})
        gold = next(r for r in _records(store) if r["instrument"] == "Gold")
        self.assertIsNone(gold["asset_module"])
        oil = next(r for r in _records(store) if r["instrument"] == "Oil")
        self.assertEqual(oil["asset_module"], "oil_module_v1")

    def test_a_store_load_failure_is_contained(self):
        class ExplodingStore:
            def load(self, state_id, default):
                raise RuntimeError("backend down")

            def save(self, state_id, payload):
                raise RuntimeError("backend down")

        with _PatchProduction():
            with mock.patch.object(
                b2_bridge, "shadow_store_mode",
                return_value=b2_bridge.STORAGE_MODE_LEGACY,
            ):
                outcomes = b2_bridge.run_shadow_observation(
                    "FAKE_KEY", "chan", store=ExplodingStore(), now=NOW
                )
        self.assertEqual(set(outcomes.values()), {"exception_swallowed"})

    def test_a_save_failure_does_not_raise(self):
        class SaveFailsStore(shadow.InMemoryShadowStore):
            def save(self, state_id, payload):
                raise RuntimeError("write failed")

        with _PatchProduction():
            b2_bridge.run_shadow_observation(
                "FAKE_KEY", "chan", store=SaveFailsStore(), now=NOW
            )

    def test_run_shadow_observation_never_raises_for_any_instrument(self):
        for side_effect in (RuntimeError("x"), ValueError("y"), KeyError("z")):
            _reset()
            with mock.patch.object(
                b2_bridge, "_gather_production_inputs", side_effect=side_effect
            ):
                b2_bridge.run_shadow_observation(
                    "FAKE_KEY", "chan", store=_CountingStore(), now=NOW
                )


# ---------------------------------------------------------------------------
# Preserved guarantees
# ---------------------------------------------------------------------------
class TestPreservedGuarantees(unittest.TestCase):
    def setUp(self):
        _reset()

    def test_production_core_hook_is_unchanged(self):
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

    def test_cross_asset_remains_withheld_for_every_instrument(self):
        _, store, _ = _run()
        for record in _records(store):
            self.assertEqual(record["cross_asset"]["status"], "withheld")

    def test_event_timing_remains_true_timestamp_based(self):
        _, store, _ = _run()
        for record in _records(store):
            self.assertEqual(
                record["event_timing"]["event_timing_source"], "calendar_timestamp"
            )
        gold = next(r for r in _records(store) if r["instrument"] == "Gold")
        self.assertAlmostEqual(
            gold["event_timing"]["minutes_to_event"], 200.0, places=1
        )

    def test_missing_calendar_stays_unavailable_for_every_instrument(self):
        _, store, _ = _run(calendar=[])
        for record in _records(store):
            self.assertIsNone(record["event_timing"]["minutes_to_event"])
            self.assertEqual(
                record["event_timing"]["event_timing_source"], "unavailable"
            )

    def test_unavailable_evidence_stays_explicit_across_instruments(self):
        # fetch_fred is patched to None, so yield-based channels genuinely
        # did not arrive and must say so rather than reading flat.
        _, store, _ = _run()
        gold = next(r for r in _records(store) if r["instrument"] == "Gold")
        reading = gold["asset_module_reading"]
        self.assertIn("real_rate_transmission", reading["unavailable_drivers"])
        for driver in reading["drivers"]:
            if driver["transmission_state"] == "unavailable":
                self.assertIsNone(driver["value"])

    def test_five_confidence_dimensions_survive_for_every_instrument(self):
        _, store, _ = _run()
        for record in _records(store):
            for key in (
                "macro_confidence", "technical_confidence", "execution_confidence",
                "regime_confidence", "data_confidence",
            ):
                self.assertIn(
                    record["confidence"][key], {"LOW", "MODERATE", "HIGH"}, key
                )

    def test_voting_families_stay_at_five_for_every_instrument(self):
        _, store, _ = _run()
        for record in _records(store):
            self.assertEqual(len(record["families"]), 5)
            for family in record["families"]:
                self.assertEqual(family["contribution_count"], 1)

    def test_production_scoring_functions_remain_module_free(self):
        for func in (
            core._calc_gold_score_only,
            core._calc_oil_score_only,
            core._calc_ndx_score_only,
            core._calc_currency_score_only,
            core.compute_composite,
            core.bias_from_score,
        ):
            source = inspect.getsource(func)
            self.assertNotIn("b2", source, func.__name__)
            self.assertNotIn("asset_module", source, func.__name__)

    def test_production_signal_thresholds_are_unchanged(self):
        self.assertEqual(core.bias_from_score(0.40)[0], "🚀 Strong Bullish")
        self.assertEqual(core.bias_from_score(0.20)[0], "📈 Moderate Bullish")
        self.assertEqual(core.bias_from_score(0.00)[0], "⚖️ Neutral / Balanced")
        self.assertEqual(core.bias_from_score(-0.20)[0], "📉 Moderate Bearish")
        self.assertEqual(core.bias_from_score(-0.40)[0], "🔻 Strong Bearish")
        self.assertEqual(core._broad_regime("🚀 Strong Bullish"), "Bullish")


if __name__ == "__main__":
    unittest.main()
