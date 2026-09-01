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
    """Stand-in for b2_shadow_records under the REPAIRED identity model.

    Mirrors the post-migration schema exactly: ``storage_id`` is the primary
    key, ``record_id`` is an indexed non-unique logical column. Modelling the
    OLD schema here is what let the live collision through, so the double now
    enforces the same constraint the database will.
    """

    def __init__(self, fail_ids=None, raise_on_batch=False, raise_always=False):
        self.rows: dict[str, dict] = {}          # storage_id -> row
        self.fail_ids = set(fail_ids or ())      # storage_ids that reject
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
            inserted, duplicate, failed, conflicted = [], [], [], []
            for row in rows:
                one = self.insert_rows([row])
                inserted.extend(one.inserted)
                duplicate.extend(one.duplicate)
                failed.extend(one.failed)
                conflicted.extend(one.conflicted)
            return b2_bridge.InsertOutcome(
                backend="supabase", durable=not failed,
                inserted=tuple(inserted), duplicate=tuple(duplicate),
                failed=tuple(failed), conflicted=tuple(conflicted),
            )
        inserted, duplicate, failed, conflicted = [], [], [], []
        for row in rows:
            sid = row["storage_id"]
            if sid in self.fail_ids:
                failed.append(sid)
            elif sid in self.rows:
                # ON CONFLICT DO NOTHING: the stored row is never modified.
                stored = self.rows[sid].get("content_hash", "")
                incoming = row.get("content_hash", "")
                if stored and incoming and stored != incoming:
                    conflicted.append(sid)
                else:
                    duplicate.append(sid)
            else:
                self.rows[sid] = dict(row)
                inserted.append(sid)
        return b2_bridge.InsertOutcome(
            backend="supabase", durable=not failed,
            inserted=tuple(inserted), duplicate=tuple(duplicate),
            failed=tuple(failed), conflicted=tuple(conflicted),
        )

    def record_exists(self, storage_id):
        return storage_id in self.rows

    def stored_content_hash(self, storage_id):
        row = self.rows.get(storage_id)
        return row.get("content_hash") if row else None

    def logical_record_exists(self, record_id):
        return any(r.get("record_id") == record_id for r in self.rows.values())

    def existing_storage_ids(self, storage_ids):
        return {sid for sid in storage_ids if sid in self.rows}

    def row_count(self):
        return len(self.rows)

    def seed(self, storage_id, *, instrument="Gold", record_id=None, evaluated_at=None,
             record=None):
        """Insert a row directly, bypassing insert semantics (test fixture)."""
        self.rows[storage_id] = {
            "storage_id": storage_id,
            "record_id": record_id or f"logical-{storage_id}",
            "instrument": instrument,
            "horizon": "tactical",
            "evaluated_at": (evaluated_at or NOW).isoformat()
            if not isinstance(evaluated_at, str) else evaluated_at,
            "schema_version": 1,
            "content_hash": "seeded",
            "record": record if record is not None else {},
        }

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
    """One V2 observation cycle against a fake row store.

    Pinned to the Tactical horizon on purpose. This suite proves the storage
    contract itself -- round-trip, immutability, dedup, migration, cutover,
    rollback -- which is horizon-orthogonal, so one horizon keeps the row
    arithmetic deterministic and the original intent readable. Live
    dual-horizon orchestration is covered by the Stage D-3 suite, and the
    concurrency test below deliberately runs the unpinned live path.
    """
    store = store or shadow.InMemoryShadowStore()
    with _PatchProduction(**overrides):
        with mock.patch.object(
            b2_bridge, "resolve_record_store", return_value=record_store
        ), mock.patch.object(
            b2_bridge, "live_shadow_horizons",
            return_value=(b2_bridge.Horizon.TACTICAL,),
        ):
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
        # Stage D: new payloads are v2, which is what adds market_anchor.
        # Legacy rows already in the table stay at v1 and are never rewritten.
        self.assertEqual(row["schema_version"], shadow.CURRENT_SCHEMA_VERSION)
        self.assertEqual(row["schema_version"], 2)
        rec = row["record"]
        for key in (
            "record_id", "instrument", "evaluated_at", "horizon", "schema_version",
            "mode", "claim", "decision_state", "decision", "confidence", "families",
            "available_families", "unavailable_families", "regime", "event_risk_state",
            "event_timing", "execution", "size_directive", "conflicts_detected",
            "scenarios", "cross_asset", "asset_module_reading", "aggregation_config",
            "market_anchor",
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
            "storage_id": "fixed", "record_id": "logical",
            "instrument": "Gold", "horizon": "tactical",
            "evaluated_at": "2020-01-01T00:00:00+00:00", "schema_version": 1,
            "content_hash": "original", "record": {"original": True},
        }
        result = table.insert_rows([{
            "storage_id": "fixed", "record_id": "logical",
            "instrument": "Gold", "horizon": "tactical",
            "evaluated_at": "2099-01-01T00:00:00+00:00", "schema_version": 9,
            "content_hash": "tampered", "record": {"original": False},
        }])
        # Different payload at the same point-in-time identity is an integrity
        # conflict, reported rather than silently resolved either way.
        self.assertEqual(result.conflicted, ("fixed",))
        self.assertEqual(result.inserted, ())
        kept = table.rows["fixed"]
        self.assertEqual(kept["evaluated_at"], "2020-01-01T00:00:00+00:00")
        self.assertEqual(kept["schema_version"], 1)
        self.assertEqual(kept["record"], {"original": True})

    def test_an_exact_retry_is_a_benign_duplicate_not_a_conflict(self):
        table = FakeRow()
        row = {
            "storage_id": "sid", "record_id": "logical", "instrument": "Gold",
            "horizon": "tactical", "evaluated_at": NOW.isoformat(),
            "schema_version": 1, "content_hash": "same", "record": {"a": 1},
        }
        table.insert_rows([row])
        again = table.insert_rows([dict(row)])
        self.assertEqual(again.duplicate, ("sid",))
        self.assertEqual(again.conflicted, ())
        self.assertEqual(len(table.rows), 1)

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
            {"storage_id": f"s{i}", "record_id": f"r{i}", "instrument": "Gold",
             "horizon": "tactical", "evaluated_at": NOW.isoformat(),
             "schema_version": 1, "content_hash": f"h{i}", "record": {}}
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
                def json(self): return [{"storage_id": payload[0]["storage_id"]}]
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
            result = store.insert_rows([{"storage_id": "x", "record_id": "x"}])
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
            result = store.insert_rows([{"storage_id": "x", "record_id": "x"}])
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
                "storage_id": "sa", "record_id": "a", "instrument": "Gold", "horizon": "tactical",
                "evaluated_at": NOW.isoformat(), "schema_version": 1, "record": {},
            }])
            self.assertEqual(result.backend, "local")
            self.assertFalse(result.durable, "local must never claim durability")
            self.assertEqual(result.inserted, ("sa",))

    def test_local_fallback_is_append_only_and_deduplicates(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "rows.jsonl")
            local = b2_bridge.LocalShadowRecordStore(path)
            row = {
                "storage_id": "sa", "record_id": "a", "instrument": "Gold", "horizon": "tactical",
                "evaluated_at": NOW.isoformat(), "schema_version": 1, "record": {},
            }
            local.insert_rows([row])
            second = local.insert_rows([row])
            self.assertEqual(second.duplicate, ("sa",))
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
        # Deliberately NOT pinned to one horizon: this is the only test in this
        # suite that exercises the real live dual-horizon path under contention.
        self.assertEqual(len(table.rows), 6, "one row per instrument-horizon-hour")
        self.assertEqual(
            {(r["instrument"], r["horizon"]) for r in table.rows.values()},
            {
                (i, h)
                for i in ("Gold", "Oil", "NDX")
                for h in ("tactical", "execution")
            },
        )

    def test_more_than_2000_records_are_all_retained(self):
        table = FakeRow()
        for i in range(2100):
            table.insert_rows([{
                "storage_id": f"s{i}", "record_id": f"r{i}",
                "instrument": "Gold", "horizon": "tactical",
                "evaluated_at": (NOW + timedelta(hours=i)).isoformat(),
                "schema_version": 1, "content_hash": f"h{i}", "record": {"i": i},
            }])
        self.assertEqual(len(table.rows), 2100)
        self.assertIn("s0", table.rows, "the oldest record must NOT be trimmed")

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
        for row in table.rows.values():
            rid = row["record_id"]
            self.assertIn(rid, legacy)
            self.assertEqual(row["evaluated_at"], legacy[rid]["evaluated_at"])
            self.assertEqual(row["record"], legacy[rid])
            # storage_id is derived, deterministic, and distinct per record.
            self.assertEqual(
                row["storage_id"],
                shadow.canonical_storage_id(
                    rid, row["instrument"], row["horizon"], row["evaluated_at"]
                ),
            )

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
        doomed = next(
            r for r in _legacy_payload()["records"] if r["record_id"] == "legacy2"
        )
        doomed_storage_id = shadow.canonical_storage_id(
            doomed["record_id"], doomed["instrument"],
            doomed["horizon"], doomed["evaluated_at"],
        )
        table = FakeRow(fail_ids={doomed_storage_id})
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
        # After a restart the durable cadence check catches it before any work,
        # so it is counted as a LOGICAL duplicate rather than a physical one.
        self.assertEqual(b2_bridge.HOOK_STATS["v2_logical_duplicate"], 1)
        self.assertEqual(len(table.rows), 1)

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


# ---------------------------------------------------------------------------
# LIVE COLLISION REPRODUCTION AND RECOVERY
#
# Reproduces the exact production incident of 2026-08-30: a legacy observation
# at 22:04:43.893828+00 and a newer V2 observation at 22:39:43.194179+00 share
# one record_id, because record_id is a UTC-HOUR-BUCKET identity. Under the old
# schema the second insert was refused and the legacy record could not be
# represented. These tests fail against the old identity model.
# ---------------------------------------------------------------------------
COLLISION_INSTRUMENTS = (
    "AUD", "CAD", "CHF", "EUR", "GBP", "Gold", "JPY", "NDX", "NZD", "Oil", "USD",
)
LEGACY_AT = datetime(2026, 8, 30, 22, 4, 43, 893828, tzinfo=timezone.utc)
NEWER_AT = datetime(2026, 8, 30, 22, 39, 43, 194179, tzinfo=timezone.utc)


def _observation(instrument, moment, *, with_provenance):
    """A record shaped like the real ones, at a specific instant."""
    record_id = b2_bridge.observation_record_id(
        b2_bridge.observation_key(instrument, b2_bridge.Horizon.TACTICAL, moment)
    )
    record = {
        "record_id": record_id,
        "instrument": instrument,
        "horizon": "tactical",
        "evaluated_at": moment.isoformat(),
        "schema_version": 1,
        "mode": "SHADOW / NON-PRODUCTION / UNCALIBRATED",
        "decision_state": "confirmed_thesis",
    }
    if with_provenance:
        record["aggregation_config"] = DEFAULT_AGGREGATION.as_provenance()
    return record


class TestLiveCollisionRepair(unittest.TestCase):
    def setUp(self):
        _reset()

    def test_the_two_observations_really_do_share_a_record_id(self):
        """The premise of the incident, proven from the shipped functions."""
        for instrument in COLLISION_INSTRUMENTS:
            legacy = _observation(instrument, LEGACY_AT, with_provenance=False)
            newer = _observation(instrument, NEWER_AT, with_provenance=True)
            self.assertEqual(
                legacy["record_id"], newer["record_id"],
                f"{instrument}: the incident requires a shared record_id",
            )
            self.assertNotEqual(legacy["evaluated_at"], newer["evaluated_at"])

    def test_both_observations_coexist_after_the_repair(self):
        table = FakeRow()
        for instrument in COLLISION_INSTRUMENTS:
            newer = shadow.record_to_row(
                _observation(instrument, NEWER_AT, with_provenance=True)
            )
            self.assertEqual(table.insert_rows([newer]).inserted, (newer["storage_id"],))

        # Now the legacy backfill arrives second, exactly as it did live.
        recovered = 0
        for instrument in COLLISION_INSTRUMENTS:
            legacy = shadow.record_to_row(
                _observation(instrument, LEGACY_AT, with_provenance=False)
            )
            result = table.insert_rows([legacy])
            self.assertEqual(
                result.inserted, (legacy["storage_id"],),
                f"{instrument}: the legacy observation must now be insertable",
            )
            self.assertEqual(result.conflicted, ())
            recovered += 1

        self.assertEqual(recovered, 11)
        self.assertEqual(len(table.rows), 22, "11 legacy + 11 newer")

    def test_each_instrument_keeps_exactly_two_distinct_timestamps(self):
        table = FakeRow()
        for instrument in COLLISION_INSTRUMENTS:
            for moment, prov in ((NEWER_AT, True), (LEGACY_AT, False)):
                table.insert_rows([
                    shadow.record_to_row(
                        _observation(instrument, moment, with_provenance=prov)
                    )
                ])
        for instrument in COLLISION_INSTRUMENTS:
            rows = [r for r in table.rows.values() if r["instrument"] == instrument]
            self.assertEqual(len(rows), 2, instrument)
            self.assertEqual(
                {r["evaluated_at"] for r in rows},
                {LEGACY_AT.isoformat(), NEWER_AT.isoformat()},
                instrument,
            )
            # One logical bucket, two physical rows -- the whole point.
            self.assertEqual(len({r["record_id"] for r in rows}), 1, instrument)
            self.assertEqual(len({r["storage_id"] for r in rows}), 2, instrument)

    def test_legacy_and_new_provenance_stay_correct_side_by_side(self):
        table = FakeRow()
        for instrument in COLLISION_INSTRUMENTS:
            table.insert_rows([
                shadow.record_to_row(_observation(instrument, NEWER_AT, with_provenance=True))
            ])
            table.insert_rows([
                shadow.record_to_row(_observation(instrument, LEGACY_AT, with_provenance=False))
            ])
        for row in table.rows.values():
            if row["evaluated_at"] == NEWER_AT.isoformat():
                self.assertEqual(
                    row["record"]["aggregation_config"]["version"], "b2-agg-v1"
                )
            else:
                self.assertNotIn(
                    "aggregation_config", row["record"],
                    "legacy records must never receive fabricated provenance",
                )

    def test_recovery_backfill_is_idempotent_over_the_collision_set(self):
        table = FakeRow()
        for instrument in COLLISION_INSTRUMENTS:
            table.insert_rows([
                shadow.record_to_row(_observation(instrument, NEWER_AT, with_provenance=True))
            ])
        legacy_rows = [
            shadow.record_to_row(_observation(i, LEGACY_AT, with_provenance=False))
            for i in COLLISION_INSTRUMENTS
        ]
        first = table.insert_rows(legacy_rows)
        snapshot = json.dumps(table.rows, sort_keys=True)
        second = table.insert_rows(legacy_rows)
        third = table.insert_rows(legacy_rows)

        self.assertEqual(len(first.inserted), 11)
        self.assertEqual(len(second.duplicate), 11)
        self.assertEqual(len(third.duplicate), 11)
        self.assertEqual(second.conflicted, ())
        self.assertEqual(len(table.rows), 22)
        self.assertEqual(
            json.dumps(table.rows, sort_keys=True), snapshot,
            "re-running recovery must not mutate a single stored row",
        )

    def test_full_migration_recovers_all_26_frozen_records(self):
        """End-to-end shape of the live incident: 15 clean + 11 collided."""
        records = []
        for i in range(15):
            records.append({
                "record_id": f"clean{i}", "instrument": "Gold", "horizon": "tactical",
                "evaluated_at": (LEGACY_AT - timedelta(hours=i + 1)).isoformat(),
                "schema_version": 1,
            })
        for instrument in COLLISION_INSTRUMENTS:
            records.append(_observation(instrument, LEGACY_AT, with_provenance=False))
        self.assertEqual(len(records), 26)

        store = shadow.InMemoryShadowStore()
        store.save(b2_bridge.SHADOW_LOG_STATE_ID, {
            "schema_version": 1, "mode": "SHADOW / NON-PRODUCTION / UNCALIBRATED",
            "diagnostics": {}, "records": records,
        })

        table = FakeRow()
        # The newer V2 rows already occupy the colliding logical ids.
        for instrument in COLLISION_INSTRUMENTS:
            table.insert_rows([
                shadow.record_to_row(_observation(instrument, NEWER_AT, with_provenance=True))
            ])
        self.assertEqual(len(table.rows), 11)

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(
                b2_bridge, "MIGRATION_STATE_FILE", os.path.join(tmp, "m.json")
            ), mock.patch.object(b2_bridge, "SHADOW_BACKUP_DIR", tmp):
                b2_bridge.freeze_legacy_shadow_log(store, now=NEWER_AT)
                result = b2_bridge.backfill_legacy_records(store, table)
                # Idempotency over the real shape.
                state = b2_bridge._migration_state()
                state["backfill_complete"] = False
                state["cursor"] = 0
                b2_bridge._save_migration_state(state)
                b2_bridge.backfill_legacy_records(store, table)

        self.assertEqual(result["status"], "complete")
        self.assertEqual(len(table.rows), 37, "26 legacy + 11 newer, none lost")

        # Every frozen legacy observation is represented EXACTLY.
        stored = {(r["record_id"], r["evaluated_at"]) for r in table.rows.values()}
        missing = [
            r for r in records
            if (r["record_id"], r["evaluated_at"]) not in stored
        ]
        self.assertEqual(missing, [], "missing legacy = 0")

        # And the newer observations are still there as additional rows.
        newer_present = [
            r for r in table.rows.values() if r["evaluated_at"] == NEWER_AT.isoformat()
        ]
        self.assertEqual(len(newer_present), 11)

    def test_verification_does_not_rely_on_aggregation_config_being_null(self):
        """Legacy identity is proven by storage identity, not by absence of a field.

        The docstring is stripped first: the function's prose legitimately
        explains that it never fabricates aggregation_config, and a raw text
        search cannot tell that apart from actually branching on the field.
        """
        module = ast.parse(inspect.getsource(b2_bridge))
        target = next(
            n for n in ast.walk(module)
            if isinstance(n, ast.FunctionDef) and n.name == "backfill_legacy_records"
        )
        if (
            target.body
            and isinstance(target.body[0], ast.Expr)
            and isinstance(target.body[0].value, ast.Constant)
            and isinstance(target.body[0].value.value, str)
        ):
            target.body = target.body[1:]
        self.assertNotIn("aggregation_config", ast.unparse(target))


# ---------------------------------------------------------------------------
# PHASE D: recovery from the EXACT live post-SQL state
#
# Verified live after the schema migration:
#   total_rows = 26, distinct_logical_ids = 26,
#   storage_id PRIMARY KEY, record_id indexed non-unique, RLS enabled.
#
# That is 15 legacy rows that backfilled cleanly plus 11 newer rows whose
# record_ids collided with the 11 legacy records that never landed. The earlier
# backfill recorded backfill_complete = True, having counted those collisions as
# duplicates, so without an identity-model re-run the 11 would stay lost.
# ---------------------------------------------------------------------------
def _live_frozen_records():
    """The 26 frozen legacy records, matching the live shape."""
    records = []
    for i in range(15):
        records.append({
            "record_id": f"clean{i}",
            "instrument": "Gold",
            "horizon": "tactical",
            "evaluated_at": (LEGACY_AT - timedelta(hours=i + 1)).isoformat(),
            "schema_version": 1,
            "mode": "SHADOW / NON-PRODUCTION / UNCALIBRATED",
        })
    for instrument in COLLISION_INSTRUMENTS:
        records.append(_observation(instrument, LEGACY_AT, with_provenance=False))
    return records


def _live_table_after_sql():
    """A FakeRow holding exactly what the live table holds right now: 26 rows."""
    table = FakeRow()
    frozen = _live_frozen_records()
    # The 15 legacy records that landed cleanly.
    for record in frozen[:15]:
        table.insert_rows([shadow.record_to_row(record)])
    # The 11 newer observations that occupied the colliding logical ids.
    for instrument in COLLISION_INSTRUMENTS:
        table.insert_rows([
            shadow.record_to_row(_observation(instrument, NEWER_AT, with_provenance=True))
        ])
    return table, frozen


class TestPhaseDLiveRecovery(unittest.TestCase):
    def setUp(self):
        _reset()
        self.table, self.frozen = _live_table_after_sql()
        self.store = shadow.InMemoryShadowStore()
        self.store.save(b2_bridge.SHADOW_LOG_STATE_ID, {
            "schema_version": 1, "mode": "SHADOW / NON-PRODUCTION / UNCALIBRATED",
            "diagnostics": {}, "records": self.frozen,
        })
        self._tmp = tempfile.TemporaryDirectory()
        self._p1 = mock.patch.object(
            b2_bridge, "MIGRATION_STATE_FILE", os.path.join(self._tmp.name, "m.json")
        )
        self._p2 = mock.patch.object(b2_bridge, "SHADOW_BACKUP_DIR", self._tmp.name)
        self._p1.start(); self._p2.start()
        # Reproduce the migration state the live deployment is actually in:
        # complete, but under the OLD identity model.
        b2_bridge._save_migration_state({
            "freeze_verified": True,
            "frozen_state_id": "b2_shadow_log_v1_frozen_20260830",
            "legacy_record_count": 26,
            "legacy_hash": "whatever",
            "cursor": 26,
            "total": 26,
            "backfill_complete": True,
        })

    def tearDown(self):
        self._p2.stop(); self._p1.stop(); self._tmp.cleanup()

    def test_the_live_starting_state_matches_the_reported_post_check(self):
        self.assertEqual(len(self.table.rows), 26, "total_rows = 26")
        self.assertEqual(
            len({r["record_id"] for r in self.table.rows.values()}), 26,
            "distinct_logical_ids = 26",
        )
        self.assertEqual(
            len({r["storage_id"] for r in self.table.rows.values()}), 26,
            "duplicate_storage_ids = 0",
        )
        # And 11 frozen legacy observations are NOT represented yet.
        stored = {(r["record_id"], r["evaluated_at"]) for r in self.table.rows.values()}
        missing = [
            r for r in self.frozen if (r["record_id"], r["evaluated_at"]) not in stored
        ]
        self.assertEqual(len(missing), 11)

    def test_without_the_identity_model_rerun_nothing_would_be_recovered(self):
        """Proves the re-run is necessary, not incidental."""
        result = b2_bridge.backfill_legacy_records(self.store, self.table)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(len(self.table.rows), 26, "a stale 'complete' recovers nothing")

    def test_advance_migration_reruns_and_recovers_exactly_eleven(self):
        result = b2_bridge.advance_migration(self.store, self.table)
        self.assertIn(result["status"], {"in_progress", "complete"})
        self.assertEqual(len(self.table.rows), 37, "26 legacy + 11 newer")
        self.assertEqual(result["inserted"], 11)
        self.assertEqual(result["duplicate"], 15)

    def test_all_26_frozen_records_are_represented_exactly(self):
        b2_bridge.advance_migration(self.store, self.table)
        stored = {(r["record_id"], r["evaluated_at"]) for r in self.table.rows.values()}
        missing = [
            r for r in self.frozen if (r["record_id"], r["evaluated_at"]) not in stored
        ]
        self.assertEqual(missing, [], "missing legacy = 0")

        # Timestamps and payloads preserved exactly -- no mismatch.
        by_key = {
            (r["record_id"], r["evaluated_at"]): r for r in self.table.rows.values()
        }
        for record in self.frozen:
            row = by_key[(record["record_id"], record["evaluated_at"])]
            self.assertEqual(row["evaluated_at"], record["evaluated_at"])
            self.assertEqual(row["record"], record)

    def test_the_eleven_newer_rows_survive_untouched(self):
        before = {
            sid: json.dumps(row, sort_keys=True)
            for sid, row in self.table.rows.items()
            if row["evaluated_at"] == NEWER_AT.isoformat()
        }
        self.assertEqual(len(before), 11)
        b2_bridge.advance_migration(self.store, self.table)
        after = {
            sid: json.dumps(row, sort_keys=True)
            for sid, row in self.table.rows.items()
            if row["evaluated_at"] == NEWER_AT.isoformat()
        }
        self.assertEqual(before, after, "no existing V2 row may be mutated")

    def test_each_collided_instrument_ends_with_both_observations(self):
        b2_bridge.advance_migration(self.store, self.table)
        for instrument in COLLISION_INSTRUMENTS:
            # Select by the COLLIDED logical id: the 15 clean legacy records are
            # also instrument "Gold", so filtering by instrument alone would mix
            # them in.
            collided_id = _observation(
                instrument, LEGACY_AT, with_provenance=False
            )["record_id"]
            rows = [
                r for r in self.table.rows.values() if r["record_id"] == collided_id
            ]
            self.assertEqual(len(rows), 2, instrument)
            self.assertEqual(
                {r["evaluated_at"] for r in rows},
                {LEGACY_AT.isoformat(), NEWER_AT.isoformat()},
                instrument,
            )
            self.assertEqual(len({r["record_id"] for r in rows}), 1, instrument)

    def test_provenance_stays_correct_after_recovery(self):
        b2_bridge.advance_migration(self.store, self.table)
        for row in self.table.rows.values():
            if row["evaluated_at"] == NEWER_AT.isoformat():
                self.assertEqual(
                    row["record"]["aggregation_config"]["version"], "b2-agg-v1"
                )
            else:
                self.assertNotIn("aggregation_config", row["record"])

    def test_recovery_is_idempotent_across_repeated_ticks(self):
        b2_bridge.advance_migration(self.store, self.table)
        snapshot = json.dumps(self.table.rows, sort_keys=True)
        for _ in range(5):
            b2_bridge.advance_migration(self.store, self.table)
        self.assertEqual(len(self.table.rows), 37)
        self.assertEqual(json.dumps(self.table.rows, sort_keys=True), snapshot)

    def test_migration_state_records_the_identity_model(self):
        b2_bridge.advance_migration(self.store, self.table)
        status = b2_bridge.migration_status()
        self.assertTrue(status["identity_model_up_to_date"])
        self.assertEqual(status["identity_model"], b2_bridge.IDENTITY_MODEL_VERSION)
        self.assertTrue(status["backfill_complete"])
        self.assertIsNotNone(status["reran_for_identity_model_at"])

    def test_the_freeze_is_never_reset_by_the_rerun(self):
        b2_bridge.advance_migration(self.store, self.table)
        status = b2_bridge.migration_status()
        self.assertTrue(status["freeze_verified"])
        self.assertEqual(status["frozen_state_id"], "b2_shadow_log_v1_frozen_20260830")

    def test_interrupted_rerun_resumes_without_duplicating(self):
        result = b2_bridge.advance_migration(self.store, self.table, )
        self.assertEqual(len(self.table.rows), 37)
        # Simulate an interruption: cursor rewound, model still current.
        state = b2_bridge._migration_state()
        state.update({"cursor": 10, "backfill_complete": False})
        b2_bridge._save_migration_state(state)
        b2_bridge.advance_migration(self.store, self.table)
        b2_bridge.advance_migration(self.store, self.table)
        self.assertEqual(len(self.table.rows), 37, "resume must not duplicate")


class TestStorageIdentityParityGate(unittest.TestCase):
    """The SQL populated storage_id; the app recomputes it. They must agree."""

    def setUp(self):
        _reset()
        self._tmp = tempfile.TemporaryDirectory()
        self._p1 = mock.patch.object(
            b2_bridge, "MIGRATION_STATE_FILE", os.path.join(self._tmp.name, "m.json")
        )
        self._p2 = mock.patch.object(b2_bridge, "SHADOW_BACKUP_DIR", self._tmp.name)
        self._p1.start(); self._p2.start()

    def tearDown(self):
        self._p2.stop(); self._p1.stop(); self._tmp.cleanup()

    def test_parity_holds_when_the_sql_used_the_same_algorithm(self):
        table, frozen = _live_table_after_sql()
        store = shadow.InMemoryShadowStore()
        store.save(b2_bridge.SHADOW_LOG_STATE_ID, {"records": frozen})
        parity = b2_bridge.verify_storage_identity_parity(store, table)
        self.assertEqual(parity["status"], "ok")
        self.assertEqual(parity["checked"], 26)
        self.assertEqual(parity["found"], 15, "the 15 clean legacy rows are found")

    def test_a_parity_mismatch_blocks_the_rerun_instead_of_duplicating(self):
        """If SQL hashed differently, recovery must refuse rather than duplicate."""
        _, frozen = _live_table_after_sql()
        store = shadow.InMemoryShadowStore()
        store.save(b2_bridge.SHADOW_LOG_STATE_ID, {"records": frozen})

        # A table whose storage ids were computed by a DIFFERENT algorithm.
        wrong = FakeRow()
        for i, record in enumerate(frozen):
            wrong.seed(f"sql-computed-differently-{i}",
                       instrument=record["instrument"],
                       record_id=record["record_id"],
                       evaluated_at=record["evaluated_at"])

        parity = b2_bridge.verify_storage_identity_parity(store, wrong)
        self.assertEqual(parity["status"], "mismatch")

        b2_bridge._save_migration_state({
            "freeze_verified": True, "cursor": 26, "total": 26,
            "backfill_complete": True,
        })
        before = len(wrong.rows)
        result = b2_bridge.advance_migration(store, wrong)
        self.assertEqual(result["status"], "blocked_parity_mismatch")
        self.assertEqual(len(wrong.rows), before, "nothing inserted while blocked")
        self.assertFalse(b2_bridge.migration_status()["backfill_complete"] is False
                         and len(wrong.rows) != before)

    def test_an_unavailable_lookup_defers_rather_than_guessing(self):
        _, frozen = _live_table_after_sql()
        store = shadow.InMemoryShadowStore()
        store.save(b2_bridge.SHADOW_LOG_STATE_ID, {"records": frozen})

        class Unknowable(FakeRow):
            def existing_storage_ids(self, storage_ids):
                return None

        table = Unknowable()
        b2_bridge._save_migration_state({
            "freeze_verified": True, "cursor": 26, "total": 26,
            "backfill_complete": True,
        })
        result = b2_bridge.advance_migration(store, table)
        self.assertEqual(result["status"], "deferred")
        self.assertEqual(len(table.rows), 0)

    def test_parity_is_not_applicable_on_an_empty_legacy_log(self):
        store = shadow.InMemoryShadowStore()
        store.save(b2_bridge.SHADOW_LOG_STATE_ID, {"records": []})
        parity = b2_bridge.verify_storage_identity_parity(store, FakeRow())
        self.assertEqual(parity["status"], "not_applicable")


if __name__ == "__main__":
    unittest.main()
