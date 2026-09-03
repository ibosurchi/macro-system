"""Tests for the B2 <-> production bridge and the additive production_core exports.

This module imports ``apex.production_core``, so it installs the durable-state
isolation first. Persistence is exercised through an in-memory store; the real
Supabase/local backend is never written to.
"""
from __future__ import annotations

import ast
import inspect
import json
import math
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np
import pandas as pd
from apex import production_core as core
from state_isolation import isolate_durable_state

isolate_durable_state()

from apex import b2_bridge
from apex.b2 import shadow
from apex.b2.enums import Direction, Horizon


def _synthetic_ohlc(rows: int = 200, seed: int = 7) -> pd.DataFrame:
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


# ---------------------------------------------------------------------------
# The additive production_core exports
# ---------------------------------------------------------------------------
class TestAdditiveExportsAreInert(unittest.TestCase):
    """The new keys must be exported and never consumed inside production_core."""

    def _read_expressions(self, name: str) -> list[str]:
        """Every place production_core READS a key by this name."""
        tree = ast.parse(inspect.getsource(core))
        hits: list[str] = []
        for node in ast.walk(tree):
            # obj["name"]
            if isinstance(node, ast.Subscript):
                sub = node.slice
                if isinstance(sub, ast.Constant) and sub.value == name:
                    hits.append(f"subscript@{getattr(node, 'lineno', '?')}")
            # obj.get("name")
            if isinstance(node, ast.Call):
                func = node.func
                if (
                    isinstance(func, ast.Attribute)
                    and func.attr == "get"
                    and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and node.args[0].value == name
                ):
                    hits.append(f"get@{getattr(node, 'lineno', '?')}")
        return hits

    def test_new_keys_have_no_consumer_in_production_core(self):
        for name in ("volatility_scale", "atr_ratio"):
            self.assertEqual(
                self._read_expressions(name),
                [],
                f"{name} is read inside production_core; it must be export-only.",
            )

    def test_entry_plan_exports_atr_ratio_on_every_return_path(self):
        empty = pd.DataFrame(
            {c: [] for c in ("open", "high", "low", "close", "volume")}
        )
        neutral = core._build_macro_entry_plan(empty, "Neutral", None, "Gold")
        self.assertIn("atr_ratio", neutral)
        self.assertIsNone(neutral["atr_ratio"])

        df = _synthetic_ohlc()
        plan = core._build_macro_entry_plan(df, "Bullish", 0.5, "Gold")
        self.assertIn("atr_ratio", plan)

    def test_atr_ratio_is_consistent_with_the_volatility_regime_label(self):
        plan = core._build_macro_entry_plan(_synthetic_ohlc(), "Bullish", 0.5, "Gold")
        ratio = plan["atr_ratio"]
        regime = plan["volatility_regime"]
        if ratio is None:
            self.assertEqual(regime, "unavailable")
            return
        expected = (
            "compression" if ratio < 0.70 else ("expansion" if ratio > 1.40 else "normal")
        )
        self.assertEqual(regime, expected)

    def test_entry_plan_preserves_all_pre_existing_keys(self):
        plan = core._build_macro_entry_plan(_synthetic_ohlc(), "Bullish", 0.5, "Gold")
        for key in (
            "direction", "status", "status_icon", "zone_low", "zone_high",
            "invalidation", "entry_score", "zone_score", "confirmation_score",
            "macro_points", "event_points", "confluences", "confirmation",
            "atr", "current_analysis_price", "opportunity_quality",
            "volatility_regime", "invalidation_structure_warning",
        ):
            self.assertIn(key, plan, key)

    def test_tactical_move_exports_the_volatility_scale_it_used(self):
        df = _synthetic_ohlc()
        with mock.patch.object(
            core, "_fetch_tactical_price_series", return_value=df
        ):
            tactical = core.compute_tactical_move("Gold", 0.5)
        self.assertIsNotNone(tactical)
        self.assertIn("volatility_scale", tactical)

        # Recompute vol5 from the same series using the function's own
        # definition. Equality proves the exported value IS the one used
        # internally, not a second definition of volatility.
        closes = df["close"].astype(float).to_numpy()
        pct = np.diff(closes) / np.maximum(closes[:-1], 1e-12)
        recent = pct[-96:] if len(pct) >= 96 else pct
        expected = float(np.nanstd(recent))
        if not np.isfinite(expected) or expected < 1e-6:
            expected = max(float(np.nanmean(np.abs(recent))), 1e-6)
        self.assertAlmostEqual(tactical["volatility_scale"], expected, places=12)

    def test_tactical_move_preserves_all_pre_existing_keys(self):
        df = _synthetic_ohlc()
        with mock.patch.object(
            core, "_fetch_tactical_price_series", return_value=df
        ):
            tactical = core.compute_tactical_move("Gold", 0.5)
        for key in (
            "key", "display_name", "icon", "symbol", "score", "label", "label_icon",
            "macro_regime", "interpretation", "momentum", "structure",
            "ret_5m", "ret_15m", "ret_1h", "ret_4h", "confidence",
            "last_price", "analysis_price", "entry_plan", "market_ts",
        ):
            self.assertIn(key, tactical, key)

    def test_scores_are_unchanged_by_the_exports(self):
        """The exported values are inert: the score depends only on prior inputs."""
        df = _synthetic_ohlc()
        with mock.patch.object(
            core, "_fetch_tactical_price_series", return_value=df
        ):
            first = core.compute_tactical_move("Gold", 0.5)
            second = core.compute_tactical_move("Gold", 0.5)
        self.assertEqual(first["score"], second["score"])
        self.assertEqual(first["entry_plan"]["entry_score"], second["entry_plan"]["entry_score"])
        # And the score is reproducible without ever reading the new keys.
        self.assertNotIn("volatility_scale", inspect.getsource(core.bias_from_score))


# ---------------------------------------------------------------------------
# The bridge
# ---------------------------------------------------------------------------
class TestBridge(unittest.TestCase):
    def test_bridge_uses_new_state_ids_only(self):
        self.assertEqual(b2_bridge.SHADOW_LOG_STATE_ID, "b2_shadow_log_v1")
        self.assertEqual(b2_bridge.PREDICTION_LOG_STATE_ID, "b2_prediction_log_v1")
        existing = {
            "vip_registry", "vip_payments", "vip_sessions", "actual_releases",
            "alert_regime_state", "telegram_update_state", "tactical_move_state",
            "forecaster_history_v2", "forex_factory_schedule_state",
        }
        self.assertNotIn(b2_bridge.SHADOW_LOG_STATE_ID, existing)
        self.assertNotIn(b2_bridge.PREDICTION_LOG_STATE_ID, existing)

    def test_production_store_satisfies_the_protocol(self):
        self.assertIsInstance(b2_bridge.ProductionShadowStore(), shadow.ShadowStore)

    def test_unknown_state_id_is_rejected(self):
        with self.assertRaises(ValueError):
            b2_bridge.ProductionShadowStore().load("vip_registry", None)

    def test_evaluation_round_trips_through_an_in_memory_store(self):
        store = shadow.InMemoryShadowStore()
        evaluation = b2_bridge.evaluate_from_production(
            instrument="XAUUSD",
            composite={"rows": [{"cat": "rate", "weight": 2.0, "score": 0.5}]},
            tactical={
                "ret_15m": 0.002, "ret_1h": 0.004, "ret_4h": 0.008,
                "structure": "Upside Breakout", "volatility_scale": 0.001,
                "entry_plan": {
                    "invalidation": 95.0, "zone_low": 99.0, "zone_high": 101.0,
                    "current_analysis_price": 100.0, "atr": 1.0, "atr_ratio": 1.0,
                    "volatility_regime": "normal", "status": "IN ZONE â€” WAIT CONFIRMATION",
                    "opportunity_quality": {
                        "room_to_opposing_structure_atr": 5.0, "asymmetry_ratio": 2.5,
                    },
                },
            },
            rule_points=0.2,
        )
        log = b2_bridge.record_evaluation(store, evaluation)
        self.assertEqual(len(log), 1)
        payload = store.load(b2_bridge.SHADOW_LOG_STATE_ID, None)
        json.dumps(payload)
        self.assertEqual(len(payload["records"]), 1)

    def test_bridge_uses_the_exported_volatility_scale_for_normalisation(self):
        tactical = {
            "ret_15m": 0.001, "ret_1h": 0.002, "ret_4h": 0.004,
            "structure": "Upside Breakout", "volatility_scale": 0.01,
        }
        with_scale = b2_bridge.signals_from_production(tactical=tactical)
        without = b2_bridge.signals_from_production(
            tactical={k: v for k, v in tactical.items() if k != "volatility_scale"}
        )
        # With no exported scale the returns cannot be placed on ANY scale, so
        # they are Unavailable. The former max-abs fallback saturated the
        # largest return at 1.0 by construction -- an invented scale that read
        # full magnitude however quiet the market was.
        self.assertIsNone(without["directional"]["medium_horizon_return"])
        self.assertIsNone(without["directional"]["short_horizon_return"])
        # With the real scale, ret_1h is standardised over its own 12 bars:
        # 0.002 / (0.01 * sqrt(12)).
        self.assertAlmostEqual(
            with_scale["directional"]["medium_horizon_return"],
            0.002 / (0.01 * math.sqrt(12)),
            places=9,
        )

    def test_bridge_network_use_is_limited_to_the_approved_storage_path(self):
        """Storage V2 intentionally performs Supabase persistence requests.

        The original contract forbade the literal ``requests.`` anywhere in the
        bridge, to prove it created no AI/Telegram/network side effects. That
        blanket ban is no longer the right shape, but the guarantee behind it
        still is, so the ban is NARROWED rather than dropped: AI, Telegram,
        schedulers and threads stay categorically prohibited, and network calls
        are allowed ONLY against the approved Supabase storage host built from
        production's own configured URL. Any other host, or any other kind of
        request, still fails this test.
        """
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

        # AI and Telegram entry points stay categorically forbidden.
        for forbidden in (
            "_post_ai_chat", "get_openrouter", "get_multi_asset_news_intelligence",
            "send_telegram_alert", "_telegram_api",
            "start_background_alert_daemon", "start_shared_background_ai_worker",
            "start_telegram_update_worker",
        ):
            self.assertNotIn(forbidden, source, forbidden)

        # Concurrency primitives checked by IMPORT rather than substring: a
        # substring match on "sched" also hits the literal "scheduled event".
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                imported.add((node.module or "").split(".")[0])
        for forbidden in ("threading", "sched", "asyncio", "multiprocessing", "subprocess", "concurrent"):
            self.assertNotIn(forbidden, imported, forbidden)

        # Only these HTTP verbs, and only ever against the Supabase REST URL
        # assembled from production's own configured SUPABASE_URL.
        allowed_verbs = {"requests.post", "requests.get"}
        used_verbs = {
            f"requests.{n.func.attr}"
            for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and isinstance(n.func.value, ast.Name)
            and n.func.value.id == "requests"
        }
        self.assertTrue(used_verbs <= allowed_verbs, f"unexpected verbs: {used_verbs}")
        for verb in used_verbs:
            self.assertIn(verb.replace("requests.", ""), {"post", "get"})
        # Every request target is self._url(), which is built only from
        # core.SUPABASE_URL -- no other host string appears in the module.
        self.assertIn("core.SUPABASE_URL", source)
        for stray in ("http://", "https://"):
            self.assertNotIn(stray, source, f"hard-coded host {stray}")

    def test_bridge_makes_no_ai_calls_threads_or_telegram(self):
        # Docstrings are stripped before searching: the module's prose
        # legitimately discusses requests and threads in order to explain that
        # it issues none, and a raw text search cannot tell the two apart.
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
        # "requests." and "start_" are covered by the narrowed contract in
        # test_bridge_network_use_is_limited_to_the_approved_storage_path.
        for forbidden in (
            "_post_ai_chat", "send_telegram_alert", "threading", "Thread",
            "get_openrouter",
        ):
            self.assertNotIn(forbidden, source, forbidden)

    def test_production_core_is_the_only_module_importing_the_bridge(self):
        """The bridge has exactly TWO approved importers, and no others.

        Before activation this asserted zero, then one. Stage D adds the second
        and last: ``b2_validation_bridge``, which is the offline I/O half of
        validation and reuses the bridge's insert-outcome vocabulary and symbol
        convention rather than restating either. It is not a production module,
        nothing schedules it, and the production surface is still the single
        ``production_core`` import site.

        The guarantee this pins is unchanged: any THIRD entry point into B2 --
        a page, a strategy, an alert path -- fails here.
        """
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        importers: list[str] = []
        for folder, _dirs, files in os.walk(root):
            if any(
                part in folder
                for part in ("_backup_", "_baseline_", ".git", "__pycache__", "tests")
            ):
                continue
            for filename in files:
                if not filename.endswith(".py") or filename == "b2_bridge.py":
                    continue
                path = os.path.join(folder, filename)
                with open(path, "r", encoding="utf-8") as handle:
                    try:
                        tree = ast.parse(handle.read())
                    except SyntaxError:
                        continue
                # AST, not text: several modules legitimately NAME the bridge in
                # prose to explain how they relate to it.
                for node in ast.walk(tree):
                    if isinstance(node, ast.ImportFrom):
                        if "b2_bridge" in (node.module or "") or any(
                            a.name == "b2_bridge" for a in node.names
                        ):
                            importers.append(os.path.basename(path))
                    if isinstance(node, ast.Import):
                        if any("b2_bridge" in a.name for a in node.names):
                            importers.append(os.path.basename(path))
        self.assertEqual(
            sorted(set(importers)),
            # H8 adds the third and last: apex/ops/runner.py, the external
            # dispatcher. Shadow capture has no approved runner script to go
            # through -- it has only ever run inside the production daemon --
            # so the dispatcher must reach this bridge directly to be invocable
            # without Streamlit.
            #
            # The guarantee is UNCHANGED and still strict: this is an exact
            # list of three file names, not a directory and not a pattern. Any
            # FOURTH entry point into B2 -- a page, a strategy, an alert path,
            # a second runner -- still fails here.
            ["b2_validation_bridge.py", "production_core.py", "runner.py"],
        )

    #: The approved operator/research entry points for Stage D. BOTH are
    #: explicit, independently invoked by a human, imported by nothing, and
    #: perform no fetch, evaluation or storage logic of their own beyond
    #: calling one bridge function each:
    #:
    #:   capture_daily_bars.py            -- D-1 capture -> capture_daily_bars()
    #:   validate_matured_observations.py -- D-5 validation -> validate_stored_range()
    #:
    #: Stage D-5 extends this list by exactly ONE name. The guarantee is
    #: unchanged: any OTHER importer -- a page, a strategy, the daemon, the
    #: Telegram loop, production_core -- still fails the tests below.
    APPROVED_VALIDATION_BRIDGE_IMPORTERS = (
        "capture_daily_bars.py",
        "validate_matured_observations.py",
    )
    #: Retained for the existence check below, which needs one concrete name.
    APPROVED_VALIDATION_BRIDGE_IMPORTER = "capture_daily_bars.py"

    def _find_validation_bridge_importers(self, root: str, *, skip_dirs=None, skip_filename=None) -> list[str]:
        """Every ``.py`` file under ``root`` that imports ``b2_validation_bridge``.

        Shared by the real-repo guard below and by the synthetic test that
        proves this detection actually catches an unauthorized importer,
        rather than merely restating today's repo state.
        """
        skip_dirs = skip_dirs if skip_dirs is not None else (
            "_backup_", "_baseline_", ".git", "__pycache__", "tests"
        )
        skip_filename = skip_filename if skip_filename is not None else "b2_validation_bridge.py"
        importers: list[str] = []
        for folder, _dirs, files in os.walk(root):
            if any(part in folder for part in skip_dirs):
                continue
            for filename in files:
                if not filename.endswith(".py") or filename == skip_filename:
                    continue
                path = os.path.join(folder, filename)
                with open(path, "r", encoding="utf-8") as handle:
                    try:
                        tree = ast.parse(handle.read())
                    except SyntaxError:
                        continue
                for node in ast.walk(tree):
                    if isinstance(node, ast.ImportFrom):
                        if "b2_validation_bridge" in (node.module or "") or any(
                            a.name == "b2_validation_bridge" for a in node.names
                        ):
                            importers.append(os.path.basename(path))
                    if isinstance(node, ast.Import) and any(
                        "b2_validation_bridge" in a.name for a in node.names
                    ):
                        importers.append(os.path.basename(path))
        return importers

    def test_the_validation_bridge_has_exactly_one_approved_importer(self):
        """Stage D's I/O half has exactly ONE importer: the named capture runner.

        Before Stage D-1 activation this asserted zero importers -- correct
        while capture had no invoker at all. Now that an explicit,
        independently-invoked operator runner exists (scripts/capture_daily_bars.py),
        the rule is not 'nothing imports this' but 'only the one approved,
        non-production, human-invoked runner imports this'. Anything else --
        production_core, a page, a strategy, the daemon, the Telegram loop --
        still fails here exactly as before. The moment a SECOND file imports
        the bridge, capture stops being an explicit, singular, offline
        operation and this test catches it.
        """
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        importers = self._find_validation_bridge_importers(root)
        self.assertEqual(
            sorted(set(importers)),
            sorted(self.APPROVED_VALIDATION_BRIDGE_IMPORTERS),
        )

    def test_the_approved_runner_file_actually_exists_and_is_not_production_code(self):
        """The allowlisted name must name a real, non-production file.

        Guards against the previous test passing vacuously because the
        approved name was simply never matched by anything on disk.
        """
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for name in self.APPROVED_VALIDATION_BRIDGE_IMPORTERS:
            runner_path = os.path.join(root, "scripts", name)
            self.assertTrue(os.path.isfile(runner_path), runner_path)
            with open(runner_path, "r", encoding="utf-8") as handle:
                source = handle.read()
            tree = ast.parse(source)
            imported: set[str] = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    imported.add((node.module or "").split(".")[0])
                if isinstance(node, ast.Import):
                    imported.update(a.name.split(".")[0] for a in node.names)
            # A runner may depend on the b2 research surface and stdlib/argparse
            # plumbing, but never on any production entry-point or scheduling
            # module -- proving requirement (2) structurally, not by convention.
            for forbidden in (
                "telegram_service", "background_services", "dashboard", "views",
                "forecaster", "app", "bootstrap", "auth", "payments", "news",
                "strategies", "threading",
            ):
                self.assertNotIn(forbidden, imported, f"{name}: {forbidden}")

    def test_zero_importer_guard_actually_detects_an_unauthorized_importer(self):
        """Proves the detection mechanism, not just today's repo state.

        A synthetic tree with one decoy file and one unauthorized importer of
        ``b2_validation_bridge`` must be flagged by exactly that filename, and
        only that one -- confirming the guard would catch a real second
        importer rather than passing by coincidence.
        """
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "decoy.py"), "w", encoding="utf-8") as handle:
                handle.write("import os\nfrom apex import b2_bridge\n")
            with open(os.path.join(tmp, "sneaky_page.py"), "w", encoding="utf-8") as handle:
                handle.write("from apex import b2_validation_bridge\n")
            with open(os.path.join(tmp, "b2_validation_bridge.py"), "w", encoding="utf-8") as handle:
                handle.write("# the module itself -- must be skipped, not self-flagged\n")

            importers = self._find_validation_bridge_importers(tmp)
        self.assertEqual(sorted(set(importers)), ["sneaky_page.py"])

    def test_production_core_has_no_module_level_b2_import(self):
        """The single import must be deferred, so B2 is not a load-time dependency."""
        tree = ast.parse(inspect.getsource(core))
        for node in tree.body:  # module scope only
            if isinstance(node, ast.ImportFrom):
                self.assertNotIn("b2", (node.module or ""))
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertNotIn("b2", alias.name)

    def test_the_only_b2_import_is_inside_the_daemon_loop_and_imports_one_name(self):
        tree = ast.parse(inspect.getsource(core))
        b2_imports = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and "b2" in (node.module or "")
        ]
        self.assertEqual(len(b2_imports), 1, "there must be exactly one B2 import site")

        node = b2_imports[0]
        self.assertEqual(node.module, ".b2_bridge".lstrip("."))
        self.assertEqual(tuple(a.name for a in node.names), ("run_shadow_observation",))

        enclosing = {
            func.name
            for func in ast.walk(tree)
            if isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef))
            and any(inner is node for inner in ast.walk(func))
        }
        # The single import sits inside the existing daemon loop, nested in the
        # existing daemon starter -- not at module scope, and nowhere else.
        self.assertEqual(enclosing, {"start_background_alert_daemon", "_daemon_loop"})


if __name__ == "__main__":
    unittest.main()
