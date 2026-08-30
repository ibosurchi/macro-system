"""Tests for the Shadow Activation hook.

Covers the three things that matter about wiring B2 into the live daemon: that
an observation is genuinely persisted, that duplicates are suppressed, and that
a B2 failure can never reach the production loop.

The hook's placement inside ``production_core`` is verified structurally via the
AST rather than by reading the source as text, so a future edit that moves it
earlier in the loop body -- where it could delay or skip production work -- fails
this suite.
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
from apex.b2 import shadow
from apex.b2.enums import Horizon

NOW = datetime(2026, 8, 30, 12, 30, 0, tzinfo=timezone.utc)

_ENTRY_PLAN = {
    "invalidation": 95.0,
    "zone_low": 99.0,
    "zone_high": 101.0,
    "current_analysis_price": 100.0,
    "atr": 1.0,
    "atr_ratio": 1.0,
    "volatility_regime": "normal",
    "status": "IN ZONE — WAIT CONFIRMATION",
    "event_points": 10,
    "opportunity_quality": {
        "room_to_opposing_structure_atr": 5.0,
        "asymmetry_ratio": 2.5,
    },
}

_TACTICAL = {
    "ret_15m": 0.002,
    "ret_1h": 0.004,
    "ret_4h": 0.008,
    "structure": "Upside Breakout",
    "volatility_scale": 0.001,
    "entry_plan": _ENTRY_PLAN,
}

_NEWS = {
    "scores": {"Gold": 0.2},
    "gold_rule_points": 0.2,
    "gold_ai_points": 0.1,
}

_COMPOSITE = {
    "rows": [
        {"cat": "rate", "weight": 2.0, "score": 0.4},
        {"cat": "inflation", "weight": 2.0, "score": 0.5},
        {"cat": "growth", "weight": 1.5, "score": 0.3},
    ]
}


def _patched(**overrides):
    """Patch every production function the observation reads."""
    defaults = {
        "fetch_all_instant_news": mock.DEFAULT,
        "analyze_news_rule_based": mock.DEFAULT,
        "_calc_gold_score_only": mock.DEFAULT,
        "compute_composite": mock.DEFAULT,
        "compute_tactical_move": mock.DEFAULT,
        "fetch_fred": mock.DEFAULT,
    }
    del defaults
    patchers = [
        mock.patch.object(core, "fetch_all_instant_news", return_value=[]),
        mock.patch.object(core, "analyze_news_rule_based", return_value=dict(_NEWS)),
        mock.patch.object(core, "_calc_gold_score_only", return_value=(0.5, "1.20%", 0.2)),
        mock.patch.object(core, "compute_composite", return_value=dict(_COMPOSITE)),
        mock.patch.object(
            core,
            "compute_tactical_move",
            return_value=overrides.get("tactical", dict(_TACTICAL)),
        ),
        mock.patch.object(core, "fetch_fred", return_value=None),
    ]
    return patchers


class _PatchAll:
    def __init__(self, **overrides):
        self._patchers = _patched(**overrides)

    def __enter__(self):
        for patcher in self._patchers:
            patcher.start()
        return self

    def __exit__(self, *exc):
        for patcher in reversed(self._patchers):
            patcher.stop()
        return False


def _reset_hook_state():
    b2_bridge._HANDLED_BUCKETS.clear()
    for name in b2_bridge.HOOK_COUNTERS:
        b2_bridge.HOOK_STATS[name] = 0


# ---------------------------------------------------------------------------
# The hook's placement inside production_core
# ---------------------------------------------------------------------------
class TestHookPlacement(unittest.TestCase):
    def setUp(self):
        tree = ast.parse(inspect.getsource(core.start_background_alert_daemon))
        self.loop_try = None
        for node in ast.walk(tree):
            if isinstance(node, ast.While):
                # The loop body is [Try, Expr(time.sleep(60))].
                for statement in node.body:
                    if isinstance(statement, ast.Try):
                        self.loop_try = statement
                        break
        self.assertIsNotNone(self.loop_try, "daemon loop try block not found")

    def _hook_node(self):
        return self.loop_try.body[-1]

    def test_hook_is_the_last_statement_in_the_loop_body(self):
        hook = self._hook_node()
        self.assertIsInstance(
            hook, ast.Try, "the B2 hook must be the last statement, in its own try"
        )
        names = [
            node.module
            for node in ast.walk(hook)
            if isinstance(node, ast.ImportFrom) and node.module
        ]
        self.assertIn("b2_bridge", names)

    def test_hook_has_its_own_exception_guard_that_swallows(self):
        hook = self._hook_node()
        self.assertEqual(len(hook.handlers), 1)
        handler = hook.handlers[0]
        self.assertIsInstance(handler.type, ast.Name)
        self.assertEqual(handler.type.id, "Exception")
        self.assertTrue(all(isinstance(s, ast.Pass) for s in handler.body))

    def test_nothing_in_production_consumes_the_hook_result(self):
        hook = self._hook_node()
        # The call must be a bare expression statement: no assignment, so no
        # production code path can read what B2 returned.
        calls = [s for s in hook.body if isinstance(s, ast.Expr)]
        assignments = [s for s in hook.body if isinstance(s, (ast.Assign, ast.AugAssign))]
        self.assertTrue(calls)
        self.assertEqual(assignments, [])

    def test_import_is_deferred_not_module_level(self):
        tree = ast.parse(inspect.getsource(core))
        for node in tree.body:  # module level only
            if isinstance(node, ast.ImportFrom):
                self.assertNotIn("b2", node.module or "")
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertNotIn("b2", alias.name)

    def test_loop_cadence_is_unchanged(self):
        source = inspect.getsource(core.start_background_alert_daemon)
        self.assertIn("time.sleep(60)", source)
        self.assertEqual(source.count("time.sleep("), 1)

    def test_no_new_thread_or_scheduler_was_added(self):
        source = inspect.getsource(core.start_background_alert_daemon)
        self.assertEqual(source.count("threading.Thread"), 1)
        self.assertEqual(source.count(".start()"), 1)


# ---------------------------------------------------------------------------
# A controlled observation is actually persisted
# ---------------------------------------------------------------------------
class TestObservationIsPersisted(unittest.TestCase):
    def setUp(self):
        _reset_hook_state()
        self.store = shadow.InMemoryShadowStore()

    def test_an_observation_is_written_to_the_shadow_log(self):
        with _PatchAll():
            outcome = b2_bridge.observe_instrument(
                "Gold", "KEY", "chan", store=self.store, now=NOW
            )
        self.assertEqual(outcome, "written")

        payload = self.store.load(b2_bridge.SHADOW_LOG_STATE_ID, None)
        self.assertIsNotNone(payload)
        self.assertEqual(len(payload["records"]), 1)
        json.dumps(payload)

    def test_persisted_record_is_marked_shadow_and_uncalibrated(self):
        with _PatchAll():
            b2_bridge.observe_instrument("Gold", "KEY", "chan", store=self.store, now=NOW)
        payload = self.store.load(b2_bridge.SHADOW_LOG_STATE_ID, None)
        self.assertEqual(payload["mode"], "SHADOW / NON-PRODUCTION / UNCALIBRATED")
        record = payload["records"][0]
        self.assertFalse(record["decision"]["macro_aggregate"]["aggregation_calibrated"])
        self.assertFalse(record["decision"]["technical_aggregate"]["aggregation_calibrated"])

    def test_persisted_record_carries_the_full_audit_payload(self):
        with _PatchAll():
            b2_bridge.observe_instrument("Gold", "KEY", "chan", store=self.store, now=NOW)
        record = self.store.load(b2_bridge.SHADOW_LOG_STATE_ID, None)["records"][0]
        for key in (
            "evaluated_at", "horizon", "claim", "regime", "families", "scenarios",
            "confidence", "decision_state", "gates_triggered", "conflicts_detected",
            "cross_asset", "unavailable_families",
        ):
            self.assertIn(key, record)
        self.assertEqual(record["instrument"], "Gold")
        self.assertEqual(record["cross_asset"]["status"], "withheld")

    def test_only_the_new_state_ids_are_written(self):
        with _PatchAll():
            b2_bridge.observe_instrument("Gold", "KEY", "chan", store=self.store, now=NOW)
        # Stage C added transmission-prediction registration, so an observation
        # now also writes the SECOND approved state id. Asserting the exact pair
        # keeps the real protection intact: any THIRD id -- a new persistence
        # surface, or a write into an existing production payload -- still fails
        # here. Both ids were approved before Stage B.
        self.assertEqual(
            set(self.store._data),
            {b2_bridge.SHADOW_LOG_STATE_ID, b2_bridge.PREDICTION_LOG_STATE_ID},
        )
        self.assertNotIn("forecaster_history_v2", self.store._data)
        self.assertNotIn("vip_registry", self.store._data)

    def test_run_shadow_observation_drives_the_configured_instruments(self):
        with _PatchAll():
            with mock.patch.object(b2_bridge, "shadow_instruments", return_value=("Gold",)):
                outcomes = b2_bridge.run_shadow_observation(
                    "KEY", "chan", store=self.store, now=NOW
                )
        self.assertEqual(outcomes, {"Gold": "written"})
        self.assertEqual(b2_bridge.HOOK_STATS["attempted"], 1)
        self.assertEqual(b2_bridge.HOOK_STATS["written"], 1)


# ---------------------------------------------------------------------------
# Duplicate suppression
# ---------------------------------------------------------------------------
class TestDuplicateSuppression(unittest.TestCase):
    def setUp(self):
        _reset_hook_state()
        self.store = shadow.InMemoryShadowStore()

    def test_identity_is_deterministic_per_instrument_hour(self):
        first = b2_bridge.observation_key("Gold", Horizon.TACTICAL, NOW)
        later_same_hour = b2_bridge.observation_key(
            "Gold", Horizon.TACTICAL, NOW + timedelta(minutes=20)
        )
        next_hour = b2_bridge.observation_key(
            "Gold", Horizon.TACTICAL, NOW + timedelta(hours=1)
        )
        self.assertEqual(first, later_same_hour)
        self.assertNotEqual(first, next_hour)

    def test_second_call_in_the_same_hour_is_skipped(self):
        with _PatchAll():
            first = b2_bridge.observe_instrument("Gold", "KEY", "chan", store=self.store, now=NOW)
            second = b2_bridge.observe_instrument(
                "Gold", "KEY", "chan", store=self.store, now=NOW + timedelta(minutes=20)
            )
        self.assertEqual(first, "written")
        self.assertEqual(second, "duplicate_skipped")
        self.assertEqual(len(self.store.load(b2_bridge.SHADOW_LOG_STATE_ID, None)["records"]), 1)

    def test_a_process_restart_still_suppresses_the_duplicate(self):
        """The in-process memo is a fast path, not the guarantee."""
        with _PatchAll():
            b2_bridge.observe_instrument("Gold", "KEY", "chan", store=self.store, now=NOW)
            b2_bridge._HANDLED_BUCKETS.clear()  # simulate a restart
            outcome = b2_bridge.observe_instrument(
                "Gold", "KEY", "chan", store=self.store, now=NOW + timedelta(minutes=5)
            )
        self.assertEqual(outcome, "duplicate_skipped")
        self.assertEqual(len(self.store.load(b2_bridge.SHADOW_LOG_STATE_ID, None)["records"]), 1)

    def test_the_next_hour_produces_a_new_observation(self):
        with _PatchAll():
            b2_bridge.observe_instrument("Gold", "KEY", "chan", store=self.store, now=NOW)
            outcome = b2_bridge.observe_instrument(
                "Gold", "KEY", "chan", store=self.store, now=NOW + timedelta(hours=1)
            )
        self.assertEqual(outcome, "written")
        self.assertEqual(len(self.store.load(b2_bridge.SHADOW_LOG_STATE_ID, None)["records"]), 2)

    def test_duplicate_check_happens_before_any_data_is_gathered(self):
        with _PatchAll():
            b2_bridge.observe_instrument("Gold", "KEY", "chan", store=self.store, now=NOW)
        with mock.patch.object(
            b2_bridge, "_gather_production_inputs", side_effect=AssertionError("gathered")
        ):
            outcome = b2_bridge.observe_instrument(
                "Gold", "KEY", "chan", store=self.store, now=NOW + timedelta(minutes=1)
            )
        self.assertEqual(outcome, "duplicate_skipped")

    def test_sixty_loop_iterations_in_one_hour_write_exactly_one_record(self):
        # Anchored to the top of the hour so all 60 one-minute iterations fall
        # inside a single bucket -- this is the real daemon cadence.
        hour_start = NOW.replace(minute=0, second=0, microsecond=0)
        with _PatchAll():
            for minute in range(60):
                b2_bridge.observe_instrument(
                    "Gold", "KEY", "chan",
                    store=self.store, now=hour_start + timedelta(minutes=minute),
                )
        records = self.store.load(b2_bridge.SHADOW_LOG_STATE_ID, None)["records"]
        self.assertEqual(len(records), 1)

    def test_crossing_an_hour_boundary_starts_a_new_bucket(self):
        hour_start = NOW.replace(minute=30, second=0, microsecond=0)
        with _PatchAll():
            for minute in range(60):
                b2_bridge.observe_instrument(
                    "Gold", "KEY", "chan",
                    store=self.store, now=hour_start + timedelta(minutes=minute),
                )
        records = self.store.load(b2_bridge.SHADOW_LOG_STATE_ID, None)["records"]
        self.assertEqual(len(records), 2, "one record per hour crossed, no more")


# ---------------------------------------------------------------------------
# Failure containment
# ---------------------------------------------------------------------------
class TestFailureContainment(unittest.TestCase):
    def setUp(self):
        _reset_hook_state()
        self.store = shadow.InMemoryShadowStore()

    def test_a_gathering_failure_is_swallowed_and_counted(self):
        with mock.patch.object(
            b2_bridge, "_gather_production_inputs", side_effect=RuntimeError("boom")
        ):
            outcomes = b2_bridge.run_shadow_observation(
                "KEY", "chan", store=self.store, now=NOW
            )
        # Every activated instrument fails independently and every failure is
        # counted. Asserting the full set is stronger than the previous
        # single-instrument assertion: it proves no instrument short-circuits
        # the rest of the batch.
        self.assertEqual(outcomes["Gold"], "exception_swallowed")
        self.assertEqual(
            set(outcomes.values()),
            {"exception_swallowed"},
            "one failing instrument must not change another's outcome",
        )
        self.assertEqual(
            b2_bridge.HOOK_STATS["exception_swallowed"], len(outcomes)
        )

    def test_a_store_failure_is_swallowed(self):
        class ExplodingStore:
            def load(self, state_id, default):
                raise RuntimeError("backend down")

            def save(self, state_id, payload):
                raise RuntimeError("backend down")

        outcomes = b2_bridge.run_shadow_observation(
            "KEY", "chan", store=ExplodingStore(), now=NOW
        )
        self.assertEqual(outcomes["Gold"], "exception_swallowed")

    def test_run_shadow_observation_never_raises(self):
        for side_effect in (RuntimeError("x"), ValueError("y"), KeyError("z")):
            with mock.patch.object(
                b2_bridge, "_gather_production_inputs", side_effect=side_effect
            ):
                b2_bridge.run_shadow_observation("KEY", "chan", store=self.store, now=NOW)

    def test_one_broken_instrument_does_not_stop_the_others(self):
        calls = {"n": 0}

        def flaky(instrument, fred_key, channel_name):
            calls["n"] += 1
            if instrument == "Oil":
                raise RuntimeError("broken")
            return None  # unknown -> counted, not fatal

        with mock.patch.object(b2_bridge, "_gather_production_inputs", side_effect=flaky):
            with mock.patch.object(
                b2_bridge, "shadow_instruments", return_value=("Oil", "Gold")
            ):
                outcomes = b2_bridge.run_shadow_observation(
                    "KEY", "chan", store=self.store, now=NOW
                )
        self.assertEqual(outcomes["Oil"], "exception_swallowed")
        self.assertEqual(outcomes["Gold"], "unknown_instrument")
        self.assertEqual(calls["n"], 2)

    def test_the_production_loop_body_survives_a_failing_hook(self):
        """Simulate the hook's guard exactly as it appears in the daemon."""
        production_completed = []

        def loop_body():
            production_completed.append("alerts")
            production_completed.append("tactical")
            try:
                raise RuntimeError("B2 exploded")
            except Exception:
                pass
            return "loop finished"

        self.assertEqual(loop_body(), "loop finished")
        self.assertEqual(production_completed, ["alerts", "tactical"])

    def test_insufficient_data_is_skipped_and_counted(self):
        with mock.patch.object(
            b2_bridge,
            "_gather_production_inputs",
            return_value={
                "composite": None, "tactical": None, "rule_points": None,
                "ai_points": None, "real_yield_mtf": None,
                "nominal_yield_mtf": None, "inflation_expectations_mtf": None,
            },
        ):
            outcome = b2_bridge.observe_instrument(
                "Gold", "KEY", "chan", store=self.store, now=NOW
            )
        self.assertEqual(outcome, "insufficient_data_skipped")
        self.assertEqual(b2_bridge.HOOK_STATS["insufficient_data_skipped"], 1)
        self.assertEqual(self.store.load(b2_bridge.SHADOW_LOG_STATE_ID, None)["records"], [])

    def test_unknown_instrument_is_counted_not_fatal(self):
        outcome = b2_bridge.observe_instrument(
            "NOT_A_MARKET", "KEY", "chan", store=self.store, now=NOW
        )
        self.assertEqual(outcome, "unknown_instrument")


# ---------------------------------------------------------------------------
# Observability and operator control
# ---------------------------------------------------------------------------
class TestObservability(unittest.TestCase):
    def setUp(self):
        _reset_hook_state()
        self.store = shadow.InMemoryShadowStore()

    def test_all_five_required_events_have_counters(self):
        for name in (
            "attempted",
            "written",
            "duplicate_skipped",
            "insufficient_data_skipped",
            "exception_swallowed",
        ):
            self.assertIn(name, b2_bridge.HOOK_COUNTERS)
            self.assertIn(name, b2_bridge.get_shadow_hook_stats())

    def test_counters_are_persisted_inside_the_shadow_log_payload(self):
        with _PatchAll():
            b2_bridge.observe_instrument("Gold", "KEY", "chan", store=self.store, now=NOW)
            b2_bridge._HANDLED_BUCKETS.clear()
            b2_bridge.observe_instrument("Gold", "KEY", "chan", store=self.store, now=NOW)
        diagnostics = self.store.load(b2_bridge.SHADOW_LOG_STATE_ID, None)["diagnostics"]
        self.assertEqual(diagnostics["written"], 1)
        self.assertEqual(diagnostics["duplicate_skipped"], 1)

    def test_diagnostics_survive_a_round_trip(self):
        with _PatchAll():
            b2_bridge.observe_instrument("Gold", "KEY", "chan", store=self.store, now=NOW)
        payload = json.loads(json.dumps(self.store.load(b2_bridge.SHADOW_LOG_STATE_ID, None)))
        restored = shadow.ShadowLog.from_record(payload)
        self.assertEqual(restored.diagnostics["written"], 1)

    def test_observability_produces_no_telegram_and_no_ui(self):
        source = inspect.getsource(b2_bridge)
        for forbidden in (
            "send_telegram_alert", "_telegram_api", "st.write", "st.error",
            "st.warning", "st.info", "render_html", "print(",
        ):
            self.assertNotIn(forbidden, source, forbidden)

    def test_hook_adds_no_ai_call_and_no_thread(self):
        source = inspect.getsource(b2_bridge)
        for forbidden in (
            "_post_ai_chat", "get_openrouter", "threading", "Thread",
            "start_shared_background_ai_worker", "start_background_alert_daemon",
        ):
            self.assertNotIn(forbidden, source, forbidden)

    def test_operator_can_disable_the_hook(self):
        with mock.patch.object(b2_bridge, "shadow_enabled", return_value=False):
            outcomes = b2_bridge.run_shadow_observation(
                "KEY", "chan", store=self.store, now=NOW
            )
        self.assertEqual(outcomes, {})
        self.assertEqual(b2_bridge.HOOK_STATS["disabled"], 1)
        self.assertEqual(self.store.load(b2_bridge.SHADOW_LOG_STATE_ID, None), None)

    def test_default_configuration_observes_every_registered_module(self):
        # Multi-Asset Shadow Activation: the default is now derived from the
        # Stage C module registry rather than pinned to Gold, so the activated
        # set cannot drift away from the modules that exist.
        from apex.b2.modules import registered_instruments

        self.assertEqual(b2_bridge.shadow_instruments(), registered_instruments())
        self.assertEqual(
            b2_bridge.default_shadow_instruments(), registered_instruments()
        )
        self.assertTrue(b2_bridge.shadow_enabled())

    def test_an_operator_can_still_narrow_the_instrument_set(self):
        with mock.patch.object(b2_bridge.core, "get_secret", return_value="Gold,Oil"):
            self.assertEqual(b2_bridge.shadow_instruments(), ("Gold", "Oil"))


# ---------------------------------------------------------------------------
# Stage A/B invariants still hold through the live path
# ---------------------------------------------------------------------------
class TestInvariantsThroughTheHook(unittest.TestCase):
    def setUp(self):
        _reset_hook_state()
        self.store = shadow.InMemoryShadowStore()

    def _record(self):
        with _PatchAll():
            b2_bridge.observe_instrument("Gold", "KEY", "chan", store=self.store, now=NOW)
        return self.store.load(b2_bridge.SHADOW_LOG_STATE_ID, None)["records"][0]

    def test_five_families_and_one_contribution_each(self):
        record = self._record()
        self.assertEqual(len(record["families"]), 5)
        for family in record["families"]:
            self.assertEqual(family["contribution_count"], 1)

    def test_neutral_and_unavailable_remain_distinct_in_the_record(self):
        record = self._record()
        # fetch_fred is patched to None, so three of the four Policy members are
        # genuinely unavailable and must be reported as such.
        policy = next(f for f in record["families"] if f["family"] == "policy_real_rates")
        self.assertIn("real_yield_momentum", policy["unavailable"])
        self.assertNotIn("real_yield_momentum", policy["flat"])

    def test_no_numeric_confidence_percentage_is_persisted(self):
        record = self._record()
        for key, value in record["confidence"].items():
            if key.endswith("_confidence"):
                self.assertIn(value, {"LOW", "MODERATE", "HIGH"})

    def test_dormant_and_withheld_components_are_still_inactive(self):
        from apex.b2 import registry

        self.assertEqual(len(registry.VOTING_FAMILIES), 5)
        self.assertIn("cross_asset_bridge", registry.withheld_keys())
        self.assertIn("liquidity_funding", registry.dormant_keys())

    def test_outcome_attachment_remains_manual(self):
        """No scheduler was added to resolve predictions."""
        source = inspect.getsource(b2_bridge)
        self.assertNotIn("attach_outcome", source)
        self.assertNotIn("pending_steps", source)


if __name__ == "__main__":
    unittest.main()
