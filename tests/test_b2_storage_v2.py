"""Storage V2 tests: append-only shadow records, migration, cutover, rollback.

Imports ``apex.production_core``, so durable-state isolation is installed first.
No test reaches a real network: the Supabase client is exercised against a fake
transport, and the local backend is redirected to a per-test temporary file.
"""
from __future__ import annotations

import ast
import inspect
import json
import os
import sys
import tempfile
import threading
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
from apex.b2.aggregation import AGGREGATION_CONFIG_VERSION, DEFAULT_AGGREGATION

NOW = datetime(2026, 8, 31, 10, 0, 0, tzinfo=timezone.utc)

_ENTRY_PLAN = {
    "invalidation": 3280.0, "zone_low": 3320.0, "zone_high": 3340.0,
    "current_analysis_price": 3330.0, "atr": 12.0, "atr_ratio": 1.05,
    "volatility_regime": "normal", "status": "IN ZONE", "event_points": 10,
    "opportunity_quality": {
        "room_to_opposing_structure_atr": 6.0, "asymmetry_ratio": 2.2,
    },
}
_TACTICAL = {
    "ret_15m": 0.0021, "ret_1h": 0.0044, "ret_4h": 0.0090,
    "structure": "Upside Breakout", "volatility_scale": 0.0012,
    "entry_plan": _ENTRY_PLAN,
}
_NEWS = {
    "scores": {
        "Gold": .22, "Oil": .15, "Nasdaq": .18, "USD": .05, "EUR": .12,
        "GBP": .10, "CAD": .08, "JPY": -.06, "CHF": .04, "AUD": .09, "NZD": .07,
    },
    "gold_rule_points": .22, "gold_ai_points": .11,
}
_COMPOSITE = {
    "macro_score": 0.31,
    "rows": [
        {"cat": "rate", "weight": 2.0, "score": -0.35},
        {"cat": "inflation", "weight": 2.0, "score": 0.42},
        {"cat": "growth", "weight": 1.5, "score": 0.28},
    ],
}
_CALENDAR = [{
    "title": "ISM PMI", "country": "USD", "impact": "High",
    "date": (NOW + timedelta(minutes=200)).isoformat(),
}]


class _PatchProduction:
    def __init__(self, **overrides):
        self.overrides = overrides

    def __enter__(self):
        o = self.overrides
        self._patchers = [
            mock.patch.object(core, "fetch_all_instant_news", return_value=[]),
            mock.patch.object(core, "analyze_news_rule_based", return_value=dict(_NEWS)),
            mock.patch.object(core, "_calc_gold_score_only", return_value=(.44, "1.18%", .22)),
            mock.patch.object(core, "_calc_oil_score_only", return_value=(.30, .15)),
            mock.patch.object(core, "_calc_ndx_score_only", return_value=(.25, .18)),
            mock.patch.object(core, "_calc_currency_score_only", return_value=.20),
            mock.patch.object(core, "_oil_price_momentum_score", return_value=.35),
            mock.patch.object(core, "compute_composite", return_value=dict(_COMPOSITE)),
            mock.patch.object(
                core, "compute_tactical_move",
                return_value=o.get("tactical", dict(_TACTICAL)),
            ),
            mock.patch.object(core, "fetch_fred", return_value=o.get("fred", None)),
            mock.patch.object(
                core, "fetch_forex_factory_calendar_rolling",
                return_value=list(o.get("calendar", _CALENDAR)),
            ),
        ]
        for p in self._patchers:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in reversed(self._patchers):
            p.stop()
        return False


class FakeRow:
    """A stand-in for the b2_shadow_records table with real PK semantics."""

    def __init__(self, fail_ids=None, raise_on_batch=False, raise_always=False):
        self.rows: dict[str, dict] = {}
        self.fail_ids = set(fail_ids or ())
        self.raise_on_batch = raise_on_batch
        self.raise_always = raise_always
        self.insert_calls = 0
        self.batch_sizes: list[int] = []

    def insert_rows(self, rows):
        self.insert_calls += 1
        self.batch_sizes.append(len(rows))
        if self.raise_always:
            raise RuntimeError("table unreachable")
        if self.raise_on_batch and len(rows) > 1:
            # Mimic a batch rejection so per-record isolation must kick in.
            inserted, duplicate, failed = [], [], []
            for row in rows:
                one = self.insert_rows([row])
                inserted.extend(one.inserted)
                duplicate.extend(one.duplicate)
                failed.extend(one.failed)
            return b2_bridge.InsertOutcome(
                backend="supabase", durable=not failed,
                inserted=tuple(inserted), duplicate=tuple(duplicate),
                failed=tuple(failed),
            )
        inserted, duplicate, failed = [], [], []
        for row in rows:
            rid = row["record_id"]
            if rid in self.fail_ids:
                failed.append(rid)
            elif rid in self.rows:
                duplicate.append(rid)          # ON CONFLICT DO NOTHING
            else:
                self.rows[rid] = dict(row)
                inserted.append(rid)
        return b2_bridge.InsertOutcome(
            backend="supabase", durable=not failed,
            inserted=tuple(inserted), duplicate=tuple(duplicate),
            failed=tuple(failed),
        )

    def record_exists(self, record_id):
        return record_id in self.rows

    def query_records(self, *, instrument=None, start=None, end=None, limit=1000, select=""):
        out = []
        for row in self.rows.values():
            if instrument and row["instrument"] != instrument:
                continue
            if start is not None and row["evaluated_at"] < start.isoformat():
                continue
            if end is not None and row["evaluated_at"] > end.isoformat():
                continue
            out.append(row)
        out.sort(key=lambda r: r["evaluated_at"], reverse=True)
        return out[:limit]


def _reset():
    b2_bridge._HANDLED_BUCKETS.clear()
    for name in b2_bridge.HOOK_COUNTERS:
        b2_bridge.HOOK_STATS[name] = 0


def _v2(record_store, store=None, now=NOW, instruments=None, **overrides):
    """One V2 observation cycle against a fake row store."""
    store = store or shadow.InMemoryShadowStore()
    with _PatchProduction(**overrides):
        with mock.patch.object(b2_bridge, "resolve_record_store", return_value=record_store):
            if instruments is not None:
                with mock.patch.object(
                    b2_bridge, "shadow_instruments", return_value=tuple(instruments)
                ):
                    out = b2_bridge.run_shadow_observation(
                        "FAKE_KEY", "chan", store=store, now=now
                    )
            else:
                out = b2_bridge.run_shadow_observation(
                    "FAKE_KEY", "chan", store=store, now=now
                )
    return out, store


# ---------------------------------------------------------------------------
# 1-2. Round-trip integrity and immutability
# ---------------------------------------------------------------------------
class TestRoundTripAndImmutability(unittest.TestCase):
    def setUp(self):
        _reset()

    def test_record_round_trips_with_validation_critical_data_intact(self):
        table = FakeRow()
        _v2(table, instruments=["Gold"])
        row = next(iter(table.rows.values()))
        self.assertEqual(row["instrument"], "Gold")
        self.assertEqual(row["horizon"], "tactical")
        self.assertEqual(row["schema_version"], 1)
        rec = row["record"]
        for key in (
            "record_id", "instrument", "evaluated_at", "horizon", "schema_version",
            "mode", "claim", "decision_state", "decision", "confidence", "families",
            "available_families", "unavailable_families", "regime", "event_risk_state",
            "event_timing", "execution", "size_directive", "conflicts_detected",
            "scenarios", "cross_asset", "asset_module_reading", "aggregation_config",
        ):
            self.assertIn(key, rec, key)
        json.dumps(row)

    def test_promoted_columns_match_the_embedded_record(self):
        table = FakeRow()
        _v2(table, instruments=["Gold", "Oil"])
        for row in table.rows.values():
            self.assertEqual(row["record_id"], row["record"]["record_id"])
            self.assertEqual(row["instrument"], row["record"]["instrument"])
            self.assertEqual(row["horizon"], row["record"]["horizon"])
            self.assertEqual(row["evaluated_at"], row["record"]["evaluated_at"])

    def test_existing_record_is_never_overwritten(self):
        table = FakeRow()
        table.rows["fixed"] = {
            "record_id": "fixed", "instrument": "Gold", "horizon": "tactical",
            "evaluated_at": "2020-01-01T00:00:00+00:00", "schema_version": 1,
            "record": {"original": True},
        }
        result = table.insert_rows([{
            "record_id": "fixed", "instrument": "Gold", "horizon": "tactical",
            "evaluated_at": "2099-01-01T00:00:00+00:00", "schema_version": 9,
            "record": {"original": False, "tampered": True},
        }])
        self.assertEqual(result.duplicate, ("fixed",))
        self.assertEqual(result.inserted, ())
        kept = table.rows["fixed"]
        self.assertEqual(kept["evaluated_at"], "2020-01-01T00:00:00+00:00")
        self.assertEqual(kept["schema_version"], 1)
        self.assertEqual(kept["record"], {"original": True})

    def test_insert_uses_ignore_duplicates_not_upsert(self):
        source = inspect.getsource(b2_bridge.SupabaseShadowRecordStore.insert_rows)
        self.assertIn("resolution=ignore-duplicates", source)
        self.assertNotIn("merge-duplicates", source)


# ---------------------------------------------------------------------------
# 3-6. Duplicates, restart safety, multi-asset, partial failure
# ---------------------------------------------------------------------------
class TestDuplicatesAndMultiAsset(unittest.TestCase):
    def setUp(self):
        _reset()

    def test_same_instrument_hour_twice_yields_one_row(self):
        table = FakeRow()
        _v2(table, instruments=["Gold"], now=NOW)
        _v2(table, instruments=["Gold"], now=NOW + timedelta(minutes=30))
        self.assertEqual(len(table.rows), 1)

    def test_restart_still_suppresses_the_duplicate(self):
        table = FakeRow()
        _v2(table, instruments=["Gold"], now=NOW)
        b2_bridge._HANDLED_BUCKETS.clear()          # simulate process restart
        out, _ = _v2(table, instruments=["Gold"], now=NOW + timedelta(minutes=20))
        self.assertEqual(out["Gold"], "duplicate_skipped")
        self.assertEqual(len(table.rows), 1)

    def test_durable_dedup_does_not_load_history(self):
        """The DB primary key is the authority; nothing reads past records."""
        source = inspect.getsource(b2_bridge._run_v2_observation)
        self.assertNotIn("load_shadow_log", source)
        self.assertNotIn("query_records", source)

    def test_next_hour_creates_a_new_row(self):
        table = FakeRow()
        _v2(table, instruments=["Gold"], now=NOW)
        _v2(table, instruments=["Gold"], now=NOW + timedelta(hours=1))
        self.assertEqual(len(table.rows), 2)

    def test_all_eleven_instruments_persist(self):
        table = FakeRow()
        out, _ = _v2(table)
        self.assertEqual(len(table.rows), 11)
        self.assertEqual(set(out.values()), {"written"})
        self.assertEqual(
            {r["instrument"] for r in table.rows.values()},
            set(b2_bridge.shadow_instruments()),
        )

    def test_one_failing_record_does_not_block_the_other_ten(self):
        table = FakeRow()
        # Discover Oil's deterministic id, then make only that row fail.
        probe = FakeRow()
        _v2(probe, instruments=["Oil"], now=NOW)
        oil_id = next(iter(probe.rows))
        _reset()

        table.fail_ids = {oil_id}
        out, _ = _v2(table, now=NOW)
        self.assertEqual(out["Oil"], "failed")
        self.assertEqual(len(table.rows), 10)
        self.assertNotIn("Oil", {r["instrument"] for r in table.rows.values()})
        for other in ("Gold", "NDX", "EUR", "USD"):
            self.assertEqual(out[other], "written", other)

    def test_a_failed_record_is_retried_on_the_next_tick(self):
        """A failure must not silently consume the instrument's hour."""
        probe = FakeRow()
        _v2(probe, instruments=["Gold"], now=NOW)
        gold_id = next(iter(probe.rows))
        _reset()

        table = FakeRow(fail_ids={gold_id})
        out, _ = _v2(table, instruments=["Gold"], now=NOW)
        self.assertEqual(out["Gold"], "failed")
        self.assertNotIn("Gold", b2_bridge._HANDLED_BUCKETS)

        table.fail_ids = set()
        out2, _ = _v2(table, instruments=["Gold"], now=NOW + timedelta(minutes=1))
        self.assertEqual(out2["Gold"], "written")
        self.assertEqual(len(table.rows), 1)

    def test_batch_rejection_falls_back_to_per_record_isolation(self):
        store = b2_bridge.SupabaseShadowRecordStore()
        rows = [
            {"record_id": f"r{i}", "instrument": "Gold", "horizon": "tactical",
             "evaluated_at": NOW.isoformat(), "schema_version": 1, "record": {}}
            for i in range(3)
        ]
        calls = {"n": 0}

        def flaky_post(*args, **kwargs):
            calls["n"] += 1
            payload = kwargs.get("json")
            if isinstance(payload, list) and len(payload) > 1:
                raise RuntimeError("batch rejected")
            class R:
                status_code = 201
                def raise_for_status(self): return None
                def json(self): return [{"record_id": payload[0]["record_id"]}]
            return R()

        with mock.patch.object(core, "_supabase_enabled", return_value=True), \
             mock.patch.object(core, "SUPABASE_URL", "https://example.invalid"), \
             mock.patch.object(b2_bridge.requests, "post", side_effect=flaky_post):
            result = store.insert_rows(rows)
        self.assertEqual(len(result.inserted), 3)
        self.assertEqual(result.failed, ())
        self.assertEqual(calls["n"], 4)   # 1 batch + 3 singles


# ---------------------------------------------------------------------------
# 7-9. Unavailability, timeout, local fallback provenance
# ---------------------------------------------------------------------------
class TestFailureAndFallback(unittest.TestCase):
    def setUp(self):
        _reset()

    def test_supabase_unavailable_is_fail_open_and_not_reported_durable(self):
        store = b2_bridge.SupabaseShadowRecordStore()
        with mock.patch.object(core, "_supabase_enabled", return_value=False):
            result = store.insert_rows([{"record_id": "x"}])
        self.assertEqual(result.backend, "unavailable")
        self.assertFalse(result.durable)
        self.assertEqual(result.inserted, ())

    def test_a_timeout_does_not_raise_and_is_reported_failed(self):
        store = b2_bridge.SupabaseShadowRecordStore()
        with mock.patch.object(core, "_supabase_enabled", return_value=True), \
             mock.patch.object(core, "SUPABASE_URL", "https://example.invalid"), \
             mock.patch.object(
                 b2_bridge.requests, "post", side_effect=TimeoutError("timed out")
             ):
            result = store.insert_rows([{"record_id": "x"}])
        self.assertEqual(result.failed, ("x",))
        self.assertFalse(result.durable)
        self.assertIn("timed out", result.error)

    def test_a_totally_unreachable_table_never_raises_into_production(self):
        table = FakeRow(raise_always=True)
        out, _ = _v2(table, instruments=["Gold"])
        self.assertEqual(out["Gold"], "failed")

    def test_local_fallback_is_never_reported_as_durable(self):
        with tempfile.TemporaryDirectory() as tmp:
            local = b2_bridge.LocalShadowRecordStore(os.path.join(tmp, "rows.jsonl"))
            result = local.insert_rows([{
                "record_id": "a", "instrument": "Gold", "horizon": "tactical",
                "evaluated_at": NOW.isoformat(), "schema_version": 1, "record": {},
            }])
            self.assertEqual(result.backend, "local")
            self.assertFalse(result.durable, "local must never claim durability")
            self.assertEqual(result.inserted, ("a",))

    def test_local_fallback_is_append_only_and_deduplicates(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "rows.jsonl")
            local = b2_bridge.LocalShadowRecordStore(path)
            row = {
                "record_id": "a", "instrument": "Gold", "horizon": "tactical",
                "evaluated_at": NOW.isoformat(), "schema_version": 1, "record": {},
            }
            local.insert_rows([row])
            second = local.insert_rows([row])
            self.assertEqual(second.duplicate, ("a",))
            with open(path, encoding="utf-8") as fh:
                self.assertEqual(len([l for l in fh if l.strip()]), 1)

    def test_resolve_picks_local_when_supabase_is_not_configured(self):
        with mock.patch.object(core, "_supabase_enabled", return_value=False):
            self.assertIsInstance(
                b2_bridge.resolve_record_store(), b2_bridge.LocalShadowRecordStore
            )
        with mock.patch.object(core, "_supabase_enabled", return_value=True):
            self.assertIsInstance(
                b2_bridge.resolve_record_store(), b2_bridge.SupabaseShadowRecordStore
            )


# ---------------------------------------------------------------------------
# 10-12. Concurrency, scale, O(new)
# ---------------------------------------------------------------------------
class TestConcurrencyAndScale(unittest.TestCase):
    def setUp(self):
        _reset()

    def test_overlapping_cycles_lose_no_rows_and_create_no_duplicates(self):
        table = FakeRow()
        lock = threading.Lock()
        original = table.insert_rows

        def synchronised(rows):
            with lock:
                return original(rows)

        table.insert_rows = synchronised
        errors: list[BaseException] = []
        store = shadow.InMemoryShadowStore()

        def worker():
            try:
                b2_bridge.run_shadow_observation(
                    "FAKE_KEY", "chan", store=store, now=NOW
                )
            except BaseException as exc:      # pragma: no cover
                errors.append(exc)

        # All patching happens on the MAIN thread and stays in place for the
        # duration. mock.patch mutates module attributes and is not thread-safe,
        # so patching from inside the workers would corrupt global state and
        # leak into unrelated tests.
        with tempfile.TemporaryDirectory() as tmp:
            with _PatchProduction(), \
                 mock.patch.object(b2_bridge, "resolve_record_store", return_value=table), \
                 mock.patch.object(
                     b2_bridge, "shadow_instruments",
                     return_value=("Gold", "Oil", "NDX"),
                 ), \
                 mock.patch.object(
                     b2_bridge, "MIGRATION_STATE_FILE", os.path.join(tmp, "m.json")
                 ), \
                 mock.patch.object(b2_bridge, "SHADOW_BACKUP_DIR", tmp):
                threads = [threading.Thread(target=worker) for _ in range(4)]
                for t in threads:
                    t.start()
                for t in threads:
                    t.join()

        self.assertEqual(errors, [])
        self.assertEqual(len(table.rows), 3, "one row per instrument-hour")
        self.assertEqual(
            {r["instrument"] for r in table.rows.values()}, {"Gold", "Oil", "NDX"}
        )

    def test_more_than_2000_records_are_all_retained(self):
        table = FakeRow()
        for i in range(2100):
            table.insert_rows([{
                "record_id": f"r{i}", "instrument": "Gold", "horizon": "tactical",
                "evaluated_at": (NOW + timedelta(hours=i)).isoformat(),
                "schema_version": 1, "record": {"i": i},
            }])
        self.assertEqual(len(table.rows), 2100)
        self.assertIn("r0", table.rows, "the oldest record must NOT be trimmed")

    def test_v2_has_no_max_records_cap(self):
        source = inspect.getsource(b2_bridge._run_v2_observation)
        self.assertNotIn("max_records", source)
        for name in ("SupabaseShadowRecordStore", "LocalShadowRecordStore"):
            self.assertNotIn("max_records", inspect.getsource(getattr(b2_bridge, name)))

    def test_month_scale_history_keeps_write_work_constant(self):
        table = FakeRow()
        for i in range(8000):
            table.rows[f"seed{i}"] = {
                "record_id": f"seed{i}", "instrument": "Gold", "horizon": "tactical",
                "evaluated_at": (NOW - timedelta(hours=i + 1)).isoformat(),
                "schema_version": 1, "record": {"i": i},
            }
        table.batch_sizes.clear()
        out, _ = _v2(table, now=NOW)
        self.assertEqual(set(out.values()), {"written"})
        # One batch of exactly the 11 NEW records -- not 8011.
        self.assertEqual(table.batch_sizes, [11])
        self.assertEqual(len(table.rows), 8011)

    def test_point_in_time_reconstruction_by_instrument_and_range(self):
        table = FakeRow()
        for i in range(48):
            table.rows[f"g{i}"] = {
                "record_id": f"g{i}", "instrument": "Gold", "horizon": "tactical",
                "evaluated_at": (NOW + timedelta(hours=i)).isoformat(),
                "schema_version": 1, "record": {"i": i},
            }
            table.rows[f"o{i}"] = {
                "record_id": f"o{i}", "instrument": "Oil", "horizon": "tactical",
                "evaluated_at": (NOW + timedelta(hours=i)).isoformat(),
                "schema_version": 1, "record": {"i": i},
            }
        window = table.query_records(
            instrument="Gold", start=NOW + timedelta(hours=10),
            end=NOW + timedelta(hours=20),
        )
        self.assertEqual(len(window), 11)
        self.assertTrue(all(r["instrument"] == "Gold" for r in window))

    def test_recurring_inserts_never_hold_the_production_lock(self):
        source = inspect.getsource(b2_bridge._run_v2_observation)
        for forbidden in ("_save_persistent_state", "_load_persistent_state", "_PERSISTENCE_LOCK"):
            self.assertNotIn(forbidden, source, forbidden)
        for name in ("SupabaseShadowRecordStore", "LocalShadowRecordStore"):
            klass = inspect.getsource(getattr(b2_bridge, name))
            for forbidden in ("_save_persistent_state", "_load_persistent_state", "_PERSISTENCE_LOCK"):
                self.assertNotIn(forbidden, klass, f"{name}: {forbidden}")

    def test_the_production_lock_is_free_during_a_v2_insert(self):
        """Empirical: the lock can be acquired while an insert is in flight."""
        acquired = []

        class Watcher(FakeRow):
            def insert_rows(self, rows):
                got = core._PERSISTENCE_LOCK.acquire(blocking=False)
                acquired.append(got)
                if got:
                    core._PERSISTENCE_LOCK.release()
                return super().insert_rows(rows)

        _v2(Watcher(), instruments=["Gold"])
        self.assertEqual(acquired, [True])


# ---------------------------------------------------------------------------
# 14-19. Legacy freeze, backfill, cutover, rollback
# ---------------------------------------------------------------------------
def _legacy_payload(n=5):
    return {
        "schema_version": 1,
        "mode": "SHADOW / NON-PRODUCTION / UNCALIBRATED",
        "diagnostics": {"written": n},
        "records": [
            {
                "record_id": f"legacy{i}", "instrument": "Gold",
                "horizon": "tactical", "schema_version": 1,
                "evaluated_at": (NOW - timedelta(hours=n - i)).isoformat(),
                "decision_state": "confirmed_thesis",
            }
            for i in range(n)
        ],
    }


class TestLegacyFreezeAndBackfill(unittest.TestCase):
    def setUp(self):
        _reset()
        self.store = shadow.InMemoryShadowStore()
        self.store.save(b2_bridge.SHADOW_LOG_STATE_ID, _legacy_payload())
        self._tmp = tempfile.TemporaryDirectory()
        self._patch = mock.patch.object(
            b2_bridge, "MIGRATION_STATE_FILE",
            os.path.join(self._tmp.name, "migration.json"),
        )
        self._patch.start()
        self._patch2 = mock.patch.object(b2_bridge, "SHADOW_BACKUP_DIR", self._tmp.name)
        self._patch2.start()

    def tearDown(self):
        self._patch2.stop()
        self._patch.stop()
        self._tmp.cleanup()

    def test_freeze_preserves_count_and_hash_and_leaves_v1_untouched(self):
        before = json.dumps(self.store.load(b2_bridge.SHADOW_LOG_STATE_ID, None), sort_keys=True)
        result = b2_bridge.freeze_legacy_shadow_log(self.store, now=NOW)
        self.assertEqual(result["status"], "frozen")
        self.assertEqual(result["legacy_record_count"], 5)
        after = json.dumps(self.store.load(b2_bridge.SHADOW_LOG_STATE_ID, None), sort_keys=True)
        self.assertEqual(before, after, "v1 must not be modified by freezing")

        frozen = core._load_persistent_state(
            result["frozen_state_id"],
            b2_bridge._frozen_backup_path(result["frozen_state_id"]), None,
        )
        self.assertEqual(len(frozen["records"]), 5)
        self.assertEqual(
            b2_bridge.canonical_payload_hash(frozen), result["legacy_hash"]
        )

    def test_freeze_is_idempotent_and_never_overwrites(self):
        first = b2_bridge.freeze_legacy_shadow_log(self.store, now=NOW)
        second = b2_bridge.freeze_legacy_shadow_log(self.store, now=NOW)
        self.assertEqual(first["status"], "frozen")
        self.assertEqual(second["status"], "already_frozen")

    def test_backfill_preserves_original_identity_and_timestamps(self):
        b2_bridge.freeze_legacy_shadow_log(self.store, now=NOW)
        table = FakeRow()
        result = b2_bridge.backfill_legacy_records(self.store, table)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(len(table.rows), 5)
        legacy = {r["record_id"]: r for r in _legacy_payload()["records"]}
        for rid, row in table.rows.items():
            self.assertIn(rid, legacy)
            self.assertEqual(row["evaluated_at"], legacy[rid]["evaluated_at"])
            self.assertEqual(row["record"], legacy[rid])

    def test_backfill_never_fabricates_aggregation_config_for_legacy(self):
        b2_bridge.freeze_legacy_shadow_log(self.store, now=NOW)
        table = FakeRow()
        b2_bridge.backfill_legacy_records(self.store, table)
        for row in table.rows.values():
            self.assertNotIn("aggregation_config", row["record"])

    def test_backfill_is_idempotent(self):
        b2_bridge.freeze_legacy_shadow_log(self.store, now=NOW)
        table = FakeRow()
        b2_bridge.backfill_legacy_records(self.store, table)
        snapshot = json.dumps(table.rows, sort_keys=True)
        state = b2_bridge._migration_state()
        state["backfill_complete"] = False
        state["cursor"] = 0
        b2_bridge._save_migration_state(state)
        b2_bridge.backfill_legacy_records(self.store, table)
        self.assertEqual(len(table.rows), 5)
        self.assertEqual(json.dumps(table.rows, sort_keys=True), snapshot)

    def test_backfill_is_bounded_and_resumable(self):
        self.store.save(b2_bridge.SHADOW_LOG_STATE_ID, _legacy_payload(250))
        b2_bridge.freeze_legacy_shadow_log(self.store, now=NOW)
        table = FakeRow()
        first = b2_bridge.backfill_legacy_records(self.store, table, batch_size=100)
        self.assertEqual(first["status"], "in_progress")
        self.assertEqual(len(table.rows), 100)
        b2_bridge.backfill_legacy_records(self.store, table, batch_size=100)
        final = b2_bridge.backfill_legacy_records(self.store, table, batch_size=100)
        self.assertEqual(final["status"], "complete")
        self.assertEqual(len(table.rows), 250)

    def test_an_interrupted_backfill_does_not_advance_past_unlanded_rows(self):
        b2_bridge.freeze_legacy_shadow_log(self.store, now=NOW)
        table = FakeRow(fail_ids={"legacy2"})
        result = b2_bridge.backfill_legacy_records(self.store, table)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(b2_bridge._migration_state().get("cursor", 0), 0)
        # Recover once the failure clears.
        table.fail_ids = set()
        again = b2_bridge.backfill_legacy_records(self.store, table)
        self.assertEqual(again["status"], "complete")
        self.assertEqual(len(table.rows), 5)

    def test_backfill_is_blocked_until_the_freeze_is_verified(self):
        result = b2_bridge.backfill_legacy_records(self.store, FakeRow())
        self.assertEqual(result["status"], "blocked")

    def test_malformed_legacy_entries_are_skipped_not_half_written(self):
        payload = _legacy_payload(3)
        payload["records"].append({"record_id": "", "instrument": "Gold"})
        self.store.save(b2_bridge.SHADOW_LOG_STATE_ID, payload)
        b2_bridge.freeze_legacy_shadow_log(self.store, now=NOW)
        table = FakeRow()
        result = b2_bridge.backfill_legacy_records(self.store, table)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(len(table.rows), 3)
        self.assertEqual(result["skipped_malformed"], 1)

    def test_migration_status_is_explicit(self):
        b2_bridge.freeze_legacy_shadow_log(self.store, now=NOW)
        b2_bridge.backfill_legacy_records(self.store, FakeRow())
        status = b2_bridge.migration_status()
        self.assertTrue(status["freeze_verified"])
        self.assertTrue(status["backfill_complete"])
        self.assertEqual(status["storage_mode"], "v2")
        self.assertEqual(status["legacy_record_count"], 5)


class TestCutoverAndRollback(unittest.TestCase):
    def setUp(self):
        _reset()

    def test_v2_is_the_default_mode(self):
        self.assertEqual(b2_bridge.shadow_store_mode(), b2_bridge.STORAGE_MODE_V2)

    def test_legacy_mode_is_selectable_for_rollback(self):
        with mock.patch.object(core, "get_secret", return_value="legacy"):
            self.assertEqual(b2_bridge.shadow_store_mode(), b2_bridge.STORAGE_MODE_LEGACY)

    def test_after_cutover_the_legacy_blob_stops_growing(self):
        table = FakeRow()
        store = shadow.InMemoryShadowStore()
        store.save(b2_bridge.SHADOW_LOG_STATE_ID, _legacy_payload(3))
        before = json.dumps(store.load(b2_bridge.SHADOW_LOG_STATE_ID, None), sort_keys=True)
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(
                b2_bridge, "MIGRATION_STATE_FILE", os.path.join(tmp, "m.json")
            ), mock.patch.object(b2_bridge, "SHADOW_BACKUP_DIR", tmp):
                _v2(table, store=store, now=NOW)
        after = json.dumps(store.load(b2_bridge.SHADOW_LOG_STATE_ID, None), sort_keys=True)
        self.assertEqual(before, after, "v1 must not grow after cutover")
        self.assertGreaterEqual(len(table.rows), 11)

    def test_rollback_to_legacy_still_works_and_leaves_v2_rows_intact(self):
        table = FakeRow()
        store = shadow.InMemoryShadowStore()
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(
                b2_bridge, "MIGRATION_STATE_FILE", os.path.join(tmp, "m.json")
            ), mock.patch.object(b2_bridge, "SHADOW_BACKUP_DIR", tmp):
                _v2(table, store=store, instruments=["Gold"], now=NOW)
        v2_rows = dict(table.rows)
        self.assertEqual(len(v2_rows), 1)

        _reset()
        with _PatchProduction():
            with mock.patch.object(
                b2_bridge, "shadow_store_mode",
                return_value=b2_bridge.STORAGE_MODE_LEGACY,
            ), mock.patch.object(
                b2_bridge, "shadow_instruments", return_value=("Gold",)
            ):
                out = b2_bridge.run_shadow_observation(
                    "FAKE_KEY", "chan", store=store, now=NOW + timedelta(hours=1)
                )
        self.assertEqual(out["Gold"], "written")
        self.assertEqual(
            len(store.load(b2_bridge.SHADOW_LOG_STATE_ID, None)["records"]), 1
        )
        self.assertEqual(table.rows, v2_rows, "rollback must not touch V2 rows")


# ---------------------------------------------------------------------------
# 20-25. Provenance, prediction log, and the preserved guarantees
# ---------------------------------------------------------------------------
class TestProvenanceAndGuarantees(unittest.TestCase):
    def setUp(self):
        _reset()

    def test_new_records_carry_full_aggregation_provenance(self):
        table = FakeRow()
        _v2(table, instruments=["Gold"])
        provenance = next(iter(table.rows.values()))["record"]["aggregation_config"]
        self.assertEqual(provenance["version"], AGGREGATION_CONFIG_VERSION)
        for key in (
            "strong_weight", "moderate_weight", "weak_weight",
            "diminishing_factor", "block_cap_override", "global_cap_override",
            "calibrated",
        ):
            self.assertIn(key, provenance["chosen"], key)
        for key in ("block_cap", "macro_group_cap", "technical_group_cap", "global_cap"):
            self.assertIn(key, provenance["derived"], key)
        self.assertIn("config_hash", provenance)
        self.assertFalse(provenance["chosen"]["calibrated"])

    def test_provenance_is_sufficient_to_reconstruct_the_aggregation(self):
        provenance = DEFAULT_AGGREGATION.as_provenance()
        rebuilt = type(DEFAULT_AGGREGATION)(**{
            k: v for k, v in provenance["chosen"].items()
        })
        self.assertEqual(rebuilt.block_cap, provenance["derived"]["block_cap"])
        self.assertEqual(rebuilt.global_cap, provenance["derived"]["global_cap"])
        self.assertEqual(rebuilt.as_provenance()["config_hash"], provenance["config_hash"])

    def test_changing_a_constant_changes_the_hash(self):
        a = DEFAULT_AGGREGATION.as_provenance()["config_hash"]
        b = type(DEFAULT_AGGREGATION)(diminishing_factor=0.6).as_provenance()["config_hash"]
        self.assertNotEqual(a, b)

    def test_prediction_log_behaviour_is_unchanged(self):
        table = FakeRow()
        store = shadow.InMemoryShadowStore()
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(
                b2_bridge, "MIGRATION_STATE_FILE", os.path.join(tmp, "m.json")
            ), mock.patch.object(b2_bridge, "SHADOW_BACKUP_DIR", tmp):
                _v2(table, store=store, now=NOW)
                first = len(store.load(b2_bridge.PREDICTION_LOG_STATE_ID, None)["predictions"])
                _reset()
                _v2(table, store=store, now=NOW + timedelta(hours=3))
                second = len(store.load(b2_bridge.PREDICTION_LOG_STATE_ID, None)["predictions"])
        self.assertEqual(first, 11)
        self.assertEqual(second, 11, "prediction day-bucket idempotency preserved")

    def test_predictions_only_registered_for_persisted_records(self):
        probe = FakeRow()
        _v2(probe, instruments=["Gold"], now=NOW)
        gold_id = next(iter(probe.rows))
        _reset()
        table = FakeRow(fail_ids={gold_id})
        store = shadow.InMemoryShadowStore()
        _v2(table, store=store, instruments=["Gold"], now=NOW)
        self.assertIsNone(store.load(b2_bridge.PREDICTION_LOG_STATE_ID, None))

    def test_cross_asset_remains_withheld_in_v2_records(self):
        table = FakeRow()
        _v2(table)
        for row in table.rows.values():
            self.assertEqual(row["record"]["cross_asset"]["status"], "withheld")

    def test_mode_label_is_preserved_on_every_v2_record(self):
        table = FakeRow()
        _v2(table)
        for row in table.rows.values():
            self.assertEqual(
                row["record"]["mode"], "SHADOW / NON-PRODUCTION / UNCALIBRATED"
            )

    def test_diagnostic_counters_distinguish_the_outcomes(self):
        for name in (
            "v2_inserted", "v2_duplicate", "v2_failed", "v2_local_fallback",
            "migration_pending", "migration_complete",
            "backfill_inserted", "backfill_duplicate", "backfill_failed",
        ):
            self.assertIn(name, b2_bridge.HOOK_COUNTERS, name)

    def test_v2_counters_move_as_expected(self):
        table = FakeRow()
        _v2(table, instruments=["Gold"], now=NOW)
        self.assertEqual(b2_bridge.HOOK_STATS["v2_inserted"], 1)
        b2_bridge._HANDLED_BUCKETS.clear()
        _v2(table, instruments=["Gold"], now=NOW)
        self.assertEqual(b2_bridge.HOOK_STATS["v2_duplicate"], 1)

    def test_production_core_is_untouched_by_storage_v2(self):
        tree = ast.parse(inspect.getsource(core))
        b2_imports = [
            n for n in ast.walk(tree)
            if isinstance(n, ast.ImportFrom) and "b2" in (n.module or "")
        ]
        self.assertEqual(len(b2_imports), 1)
        self.assertEqual(
            tuple(a.name for a in b2_imports[0].names), ("run_shadow_observation",)
        )
        for name in ("b2_shadow_records", "SupabaseShadowRecordStore", "aggregation_config"):
            self.assertNotIn(name, inspect.getsource(core), name)

    def test_production_signal_thresholds_are_unchanged(self):
        self.assertEqual(core.bias_from_score(0.40)[0], "🚀 Strong Bullish")
        self.assertEqual(core.bias_from_score(-0.40)[0], "🔻 Strong Bearish")
        self.assertEqual(core._broad_regime("🚀 Strong Bullish"), "Bullish")

    def test_no_secret_is_logged_or_returned(self):
        source = inspect.getsource(b2_bridge)
        self.assertNotIn("print(", source)
        self.assertNotIn("SUPABASE_SERVICE_ROLE_KEY", source)


if __name__ == "__main__":
    unittest.main()
