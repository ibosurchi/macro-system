"""Stage D-1 storage layer: market anchor capture and append-only market bars.

Covers the storage half of Stage D only. Outcome labelling, metrics, ablation
and calibration are later, separately approved steps and are deliberately not
exercised or implied here.

Imports ``apex.production_core``, so durable-state isolation is installed first.
No test reaches a real network: the Supabase client is exercised against a fake
transport, the Yahoo fetch against a fake response, and the local backend is
redirected to a per-test temporary file.
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

from apex import b2_bridge, b2_validation_bridge as vb
from apex.b2 import shadow
from apex.b2.validation import anchor as anchor_mod
from apex.b2.validation import bars as bars_mod
from apex.b2.validation import revisions as revisions_mod
from apex.b2.validation import outcomes as outcomes_mod
from apex.b2.validation import series_pins as series_pins_mod
from apex.b2.validation.anchor import (
    AnchorStatus,
    MarketAnchor,
    SymbolConvention,
    build_market_anchor,
    classify_anchor,
)
from apex.b2.validation.bars import (
    GRANULARITY_1D,
    MarketBar,
    MarketObservationError,
    analysis_ohlc,
    bar_is_final,
    canonical_bar_content_hash,
    canonical_bar_time_iso,
    canonical_observation_id,
    coverage,
    forward_bars,
    row_to_bar,
)

NOW = datetime(2026, 9, 15, 12, 0, 0, tzinfo=timezone.utc)
EVAL_AT = datetime(2026, 8, 30, 22, 4, 43, 893828, tzinfo=timezone.utc)
EVAL_AT_2 = datetime(2026, 8, 30, 22, 39, 43, 194179, tzinfo=timezone.utc)


def _bar(symbol="XAUUSD=X", instrument="Gold", day=30, month=8, *, invert=False,
         o=3400.0, h=3420.0, l=3390.0, c=3410.0, volume=None):
    return MarketBar(
        symbol=symbol, instrument=instrument, granularity=GRANULARITY_1D,
        bar_time=datetime(2026, month, day, tzinfo=timezone.utc),
        open=o, high=h, low=l, close=c, volume=volume, invert=invert,
    )


def _shadow_row(storage_id="s1", instrument="Gold", evaluated_at=EVAL_AT,
                record_id="logical-1", anchor=None, schema_version=2,
                execution=None):
    record = {
        "schema_version": schema_version,
        "record_id": record_id,
        "instrument": instrument,
        "horizon": "tactical",
        "evaluated_at": evaluated_at.isoformat(),
        "market_anchor": anchor,
        "execution": execution,
    }
    return {
        "storage_id": storage_id, "record_id": record_id,
        "instrument": instrument, "horizon": "tactical",
        "evaluated_at": evaluated_at.isoformat(),
        "schema_version": schema_version, "content_hash": "h",
        "record": record,
    }


_CAPTURED_ANCHOR = {
    "analysis_price": 3410.0, "last_price": 3410.0, "symbol": "XAUUSD=X",
    "symbol_requested": "XAUUSD=X", "symbol_fallback_used": False,
    "invert": False, "market_ts": 1787011200,
    "market_ts_iso": "2026-08-18T00:00:00+00:00",
    "volatility_scale": 0.0012, "atr": 12.0, "atr_ratio": 1.05,
    "volatility_regime": "normal", "price_source": "yahoo_5m_tactical",
    "granularity": "5m", "anchor_status": "anchor_captured",
}


class FakeMarketTable:
    """Stand-in for b2_market_observations, enforcing the real constraints."""

    def __init__(self, raise_on_batch=False, raise_always=False):
        self.rows: dict[str, dict] = {}
        self.raise_on_batch = raise_on_batch
        self.raise_always = raise_always
        self.insert_calls = 0
        self.query_calls = 0
        self.available = True

    def insert_rows(self, rows):
        self.insert_calls += 1
        if self.raise_always:
            raise RuntimeError("table unreachable")
        if self.raise_on_batch and len(rows) > 1:
            inserted, duplicate, conflicted, failed = [], [], [], []
            for row in rows:
                one = self.insert_rows([row])
                inserted.extend(one.inserted)
                duplicate.extend(one.duplicate)
                conflicted.extend(one.conflicted)
                failed.extend(one.failed)
            return b2_bridge.InsertOutcome(
                backend="supabase", durable=not failed, inserted=tuple(inserted),
                duplicate=tuple(duplicate), conflicted=tuple(conflicted),
                failed=tuple(failed),
            )
        inserted, duplicate, conflicted = [], [], []
        for row in rows:
            oid = row["observation_id"]
            # The table's CHECK (is_final) constraint.
            if not row.get("is_final"):
                raise RuntimeError("b2_market_obs_final_only_ck violated")
            if oid in self.rows:
                if self.rows[oid]["content_hash"] != row["content_hash"]:
                    conflicted.append(oid)      # never overwritten
                else:
                    duplicate.append(oid)
                continue
            self.rows[oid] = dict(row)
            inserted.append(oid)
        return b2_bridge.InsertOutcome(
            backend="supabase", durable=True, inserted=tuple(inserted),
            duplicate=tuple(duplicate), conflicted=tuple(conflicted),
        )

    def stored_content_hash(self, observation_id):
        row = self.rows.get(observation_id)
        return row.get("content_hash") if row else None

    def row_count(self):
        return len(self.rows)

    def query_bars(self, *, symbols, start, end, granularity=GRANULARITY_1D, limit=10000):
        self.query_calls += 1
        wanted = set(symbols)
        low, high = canonical_bar_time_iso(start), canonical_bar_time_iso(end)
        out = [
            r for r in self.rows.values()
            if r["symbol"] in wanted and r["granularity"] == granularity
            and low < r["bar_time"] <= high
        ]
        out.sort(key=lambda r: (r["symbol"], r["bar_time"]))
        return out[:limit]


def _yahoo_payload(timestamps, closes, *, opens=None, highs=None, lows=None,
                   volumes=None, tz="UTC"):
    n = len(timestamps)
    return {"chart": {"result": [{
        "meta": {"exchangeTimezoneName": tz},
        "timestamp": list(timestamps),
        "indicators": {"quote": [{
            "open": opens if opens is not None else list(closes),
            "high": highs if highs is not None
            else [None if c is None else c * 1.01 for c in closes],
            "low": lows if lows is not None
            else [None if c is None else c * 0.99 for c in closes],
            "close": list(closes),
            "volume": volumes if volumes is not None else [None] * n,
        }]},
    }]}}


def _referenced_names(module) -> set[str]:
    """Every NAME the module's code touches -- imports, calls, attributes.

    AST rather than raw text, deliberately. These modules legitimately NAME
    things in prose to explain what they must never do, and a text scan would
    fire on the documentation instead of the code.
    """
    tree = ast.parse(inspect.getsource(module))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.add(node.module or "")
            names.update(a.name for a in node.names)
    return names


def _epochs(start_day, count, month=8):
    base = datetime(2026, month, start_day, tzinfo=timezone.utc)
    return [int((base + timedelta(days=i)).timestamp()) for i in range(count)]


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


# ---------------------------------------------------------------------------
# 1. Identity and idempotency
# ---------------------------------------------------------------------------
class TestIdentity(unittest.TestCase):
    def test_observation_id_is_deterministic(self):
        self.assertEqual(_bar().observation_id, _bar().observation_id)

    def test_observation_id_matches_the_natural_key(self):
        bar = _bar()
        self.assertEqual(
            bar.observation_id,
            canonical_observation_id(
                bar.symbol, bar.granularity, bar.bar_time_iso, bar.price_source
            ),
        )

    def test_reformatting_the_timestamp_does_not_change_the_id(self):
        """Z-suffix vs +00:00, and a non-UTC tzinfo, must hash identically."""
        utc = _bar()
        as_offset = MarketBar(
            symbol="XAUUSD=X", instrument="Gold", granularity=GRANULARITY_1D,
            bar_time=datetime(2026, 8, 30, 2, 0, tzinfo=timezone(timedelta(hours=2))),
            open=3400.0, high=3420.0, low=3390.0, close=3410.0,
            volume=None, invert=False,
        )
        self.assertEqual(utc.observation_id, as_offset.observation_id)

    def test_a_naive_timestamp_is_treated_as_utc(self):
        naive = MarketBar(
            symbol="XAUUSD=X", instrument="Gold", granularity=GRANULARITY_1D,
            bar_time=datetime(2026, 8, 30), open=3400.0, high=3420.0,
            low=3390.0, close=3410.0, volume=None, invert=False,
        )
        self.assertEqual(naive.observation_id, _bar().observation_id)

    def test_different_symbols_at_the_same_time_are_distinct(self):
        self.assertNotEqual(
            _bar().observation_id,
            _bar(symbol="GC=F").observation_id,
        )

    def test_gold_fallback_symbol_is_a_distinct_row_same_instrument(self):
        primary, fallback = _bar(), _bar(symbol="GC=F")
        self.assertNotEqual(primary.observation_id, fallback.observation_id)
        self.assertEqual(primary.instrument, fallback.instrument)

    def test_content_hash_ignores_identity_and_capture_time(self):
        self.assertEqual(_bar().content_hash, _bar(symbol="GC=F").content_hash)

    def test_content_hash_changes_when_a_value_changes(self):
        self.assertNotEqual(_bar().content_hash, _bar(c=3411.0).content_hash)

    def test_exact_retry_is_idempotent(self):
        table = FakeMarketTable()
        rows = [_bar(day=d).to_row() for d in (26, 27, 28)]
        first = table.insert_rows(rows)
        self.assertEqual((len(first.inserted), len(first.duplicate)), (3, 0))
        for _ in range(4):
            again = table.insert_rows(rows)
            self.assertEqual(len(again.inserted), 0)
            self.assertEqual(len(again.duplicate), 3)
        self.assertEqual(table.row_count(), 3)


# ---------------------------------------------------------------------------
# 2. Append-only and revision conflict
# ---------------------------------------------------------------------------
class TestAppendOnly(unittest.TestCase):
    def test_an_existing_row_is_never_overwritten(self):
        table = FakeMarketTable()
        table.insert_rows([_bar(c=3410.0).to_row()])
        stored = dict(next(iter(table.rows.values())))
        table.insert_rows([_bar(c=3395.0).to_row()])
        self.assertEqual(next(iter(table.rows.values()))["close"], stored["close"])
        self.assertEqual(next(iter(table.rows.values()))["close"], 3410.0)

    def test_a_revised_bar_is_reported_as_conflicted_not_merged(self):
        table = FakeMarketTable()
        table.insert_rows([_bar(c=3410.0).to_row()])
        outcome = table.insert_rows([_bar(c=3395.0).to_row()])
        self.assertEqual(len(outcome.conflicted), 1)
        self.assertEqual(len(outcome.inserted), 0)
        self.assertEqual(len(outcome.duplicate), 0)
        self.assertEqual(table.row_count(), 1)

    def test_insert_uses_ignore_duplicates_not_merge(self):
        source = inspect.getsource(vb.SupabaseMarketObservationStore.insert_rows)
        self.assertIn("resolution=ignore-duplicates", source)
        self.assertNotIn("merge-duplicates", source)

    def test_no_update_or_delete_verb_exists_in_the_validation_bridge(self):
        source = inspect.getsource(vb)
        for forbidden in ("requests.patch", "requests.delete", "requests.put",
                          "merge-duplicates"):
            self.assertNotIn(forbidden, source, forbidden)

    def test_conflict_classification_never_asserts_a_conflict_it_cannot_check(self):
        store = vb.SupabaseMarketObservationStore()
        with mock.patch.object(store, "stored_content_hash", return_value=None):
            duplicate, conflicted = store._classify_duplicates(
                [_bar().to_row()], [_bar().observation_id]
            )
        self.assertEqual(len(duplicate), 1)
        self.assertEqual(conflicted, [])

    def test_a_postgrest_float_read_back_is_never_mistaken_for_a_conflict(self):
        """Stage D-4 regression.

        PostgREST serialises ``double precision`` at 15 significant digits, so a
        stored ``4033.699951171875`` reads back as ``4033.69995117188`` and
        rehashes differently. Measured across all eleven captured symbols, that
        would produce a false conflict on roughly 95% of FX rows.

        It does not, because ``_classify_duplicates`` compares against the
        stored ``content_hash`` COLUMN and never recomputes a hash from the
        numerics beside it. This test fails the moment that stops being true.
        """
        lossy = 4033.699951171875
        self.assertNotEqual(float(f"{lossy:.15g}"), lossy)   # the premise

        bar = _bar(o=4000.0, h=4100.0, l=3990.0, c=lossy)
        store = vb.SupabaseMarketObservationStore()
        with mock.patch.object(
            store, "stored_content_hash", return_value=bar.content_hash
        ):
            duplicate, conflicted = store._classify_duplicates(
                [bar.to_row()], [bar.observation_id]
            )
        self.assertEqual(duplicate, [bar.observation_id])
        self.assertEqual(conflicted, [])

    def test_conflict_detection_reads_the_hash_column_and_recomputes_nothing(self):
        """Source guard for the invariant above.

        The stored ``content_hash`` column is the sole integrity authority for a
        stored bar. Recomputing it from returned floats is the defect; this
        catches it at the point a refactor would introduce it.
        """
        classify = inspect.getsource(
            vb.SupabaseMarketObservationStore._classify_duplicates
        )
        self.assertNotIn("canonical_bar_content_hash", classify)
        self.assertIn("stored_content_hash", classify)

        reader = inspect.getsource(
            vb.SupabaseMarketObservationStore.stored_content_hash
        )
        self.assertIn('"select": "content_hash"', reader)


# ---------------------------------------------------------------------------
# 3. Closed bars only
# ---------------------------------------------------------------------------
class TestFinalBarsOnly(unittest.TestCase):
    def test_an_in_progress_daily_bar_is_not_final(self):
        opened = datetime(2026, 8, 30, tzinfo=timezone.utc)
        self.assertFalse(
            bar_is_final(opened, GRANULARITY_1D, opened + timedelta(hours=6))
        )

    def test_a_closed_daily_bar_is_final(self):
        opened = datetime(2026, 8, 30, tzinfo=timezone.utc)
        self.assertTrue(
            bar_is_final(opened, GRANULARITY_1D, opened + timedelta(days=1))
        )

    def test_the_fetcher_refuses_the_in_progress_bar(self):
        """The final timestamp is today's open bar; it must not be captured."""
        stamps = _epochs(25, 7)                      # 25..31 Aug
        payload = _yahoo_payload(stamps, [3400.0 + i for i in range(7)])
        now = datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc)
        with mock.patch.object(vb.requests, "get", return_value=_FakeResponse(payload)):
            captured = vb.fetch_daily_bars(
                "XAUUSD=X", instrument="Gold", invert=False, now=now
            )
        self.assertTrue(captured)
        last = max(b.bar_time for b in captured)
        self.assertEqual(last, datetime(2026, 8, 30, tzinfo=timezone.utc))
        self.assertTrue(all(b.is_final(now) for b in captured))

    def test_every_row_sent_declares_is_final(self):
        self.assertTrue(_bar().to_row()["is_final"])

    def test_the_table_rejects_a_non_final_row(self):
        table = FakeMarketTable()
        row = _bar().to_row()
        row["is_final"] = False
        with self.assertRaises(RuntimeError):
            table.insert_rows([row])


# ---------------------------------------------------------------------------
# 4. Convention parity: invert, FX and JPY
# ---------------------------------------------------------------------------
class TestConventionParity(unittest.TestCase):
    def test_non_inverted_conversion_is_identity(self):
        self.assertEqual(analysis_ohlc(1.0, 2.0, 0.5, 1.5, False), (1.0, 2.0, 0.5, 1.5))

    def test_inverting_swaps_high_and_low(self):
        o, h, l, c = analysis_ohlc(100.0, 110.0, 90.0, 105.0, True)
        self.assertAlmostEqual(h, 1.0 / 90.0)    # raw LOW becomes analysis HIGH
        self.assertAlmostEqual(l, 1.0 / 110.0)   # raw HIGH becomes analysis LOW
        self.assertAlmostEqual(c, 1.0 / 105.0)
        self.assertGreater(h, l)

    def test_conversion_matches_production_tactical_analysis_ohlc(self):
        """Parity with production's own function, for every live instrument."""
        import pandas as pd

        raw = {"open": [100.0], "high": [110.0], "low": [90.0], "close": [105.0]}
        for instrument in b2_bridge.default_shadow_instruments():
            convention = b2_bridge.symbol_convention(instrument)
            self.assertIsNotNone(convention, instrument)
            frame = core._tactical_analysis_ohlc(
                pd.DataFrame(raw), bool(convention.invert)
            )
            ours = analysis_ohlc(100.0, 110.0, 90.0, 105.0, convention.invert)
            for index, column in enumerate(("open", "high", "low", "close")):
                self.assertAlmostEqual(
                    ours[index], float(frame[column].iloc[0]), places=12,
                    msg=f"{instrument}.{column}",
                )

    def test_jpy_strength_rises_when_usdjpy_falls(self):
        strong = _bar(symbol="USDJPY=X", instrument="JPY", invert=True,
                      o=147.0, h=148.0, l=146.0, c=146.0)
        weak = _bar(symbol="USDJPY=X", instrument="JPY", day=29, invert=True,
                    o=147.0, h=148.0, l=146.0, c=148.0)
        self.assertGreater(strong.analysis_close, weak.analysis_close)

    def test_eur_strength_rises_when_eurusd_rises(self):
        up = _bar(symbol="EURUSD=X", instrument="EUR", invert=False,
                  o=1.08, h=1.10, l=1.07, c=1.10)
        down = _bar(symbol="EURUSD=X", instrument="EUR", day=29, invert=False,
                    o=1.08, h=1.10, l=1.07, c=1.07)
        self.assertGreater(up.analysis_close, down.analysis_close)

    def test_invert_is_read_from_the_stored_row_not_recomputed(self):
        row = _bar(symbol="USDJPY=X", instrument="JPY", invert=True).to_row()
        self.assertTrue(row["invert"])
        self.assertTrue(row_to_bar(row).invert)

    def test_production_symbol_conventions_are_the_single_source(self):
        gold = b2_bridge.symbol_convention("Gold")
        self.assertEqual(gold.symbol, "XAUUSD=X")
        self.assertIn("GC=F", gold.fallback_symbols)
        self.assertTrue(gold.is_fallback("GC=F"))
        self.assertFalse(gold.is_fallback("XAUUSD=X"))
        self.assertTrue(b2_bridge.symbol_convention("JPY").invert)
        self.assertFalse(b2_bridge.symbol_convention("EUR").invert)
        self.assertIsNone(b2_bridge.symbol_convention("NOT_AN_ASSET"))


# ---------------------------------------------------------------------------
# 5. No lookahead
# ---------------------------------------------------------------------------
class TestNoLookahead(unittest.TestCase):
    def test_a_bar_opening_exactly_at_evaluated_at_is_excluded(self):
        at = _bar(day=30)
        selected = forward_bars(
            [at], evaluated_at=datetime(2026, 8, 30, tzinfo=timezone.utc),
            window=timedelta(days=14),
        )
        self.assertEqual(selected, ())

    def test_a_bar_straddling_the_evaluation_moment_is_excluded(self):
        """The 22:04 evaluation sits inside the 30 Aug daily bar."""
        selected = forward_bars(
            [_bar(day=30), _bar(day=31)],
            evaluated_at=EVAL_AT, window=timedelta(days=14),
        )
        self.assertEqual([b.bar_time.day for b in selected], [31])

    def test_the_window_end_is_inclusive(self):
        selected = forward_bars(
            [_bar(day=13, month=9)], evaluated_at=EVAL_AT, window=timedelta(days=14)
        )
        self.assertEqual(len(selected), 1)

    def test_bars_beyond_the_window_are_excluded(self):
        selected = forward_bars(
            [_bar(day=20, month=9)], evaluated_at=EVAL_AT, window=timedelta(days=14)
        )
        self.assertEqual(selected, ())

    def test_selected_bars_are_time_ordered(self):
        unordered = [_bar(day=5, month=9), _bar(day=1, month=9), _bar(day=3, month=9)]
        selected = forward_bars(
            unordered, evaluated_at=EVAL_AT, window=timedelta(days=14)
        )
        self.assertEqual([b.bar_time.day for b in selected], [1, 3, 5])

    def test_market_bars_cannot_reach_a_prediction_feature(self):
        """Structural: nothing in the pure bar/anchor layer reads the families,
        decision, confidence or regime sections of a record."""
        for module in (bars_mod, anchor_mod):
            source = inspect.getsource(module)
            for feature in ('"families"', '"decision"', '"confidence"', '"regime"',
                            '"scenarios"', '"claim"'):
                self.assertNotIn(feature, source, f"{module.__name__}:{feature}")


# ---------------------------------------------------------------------------
# 6. Missing data never becomes a wrong prediction
# ---------------------------------------------------------------------------
class TestMissingIsNotWrong(unittest.TestCase):
    def test_an_open_window_is_unresolved_not_incorrect(self):
        result = coverage(
            [], evaluated_at=EVAL_AT, window=timedelta(days=14),
            now=EVAL_AT + timedelta(days=2),
        )
        self.assertEqual(result["status"], "unresolved_window_open")

    def test_an_elapsed_window_with_no_bars_is_unresolved_not_incorrect(self):
        result = coverage(
            [], evaluated_at=EVAL_AT, window=timedelta(days=14), now=NOW
        )
        self.assertEqual(result["status"], "unresolved_no_bars")

    def test_a_coverage_gap_is_reported_explicitly(self):
        early = [_bar(day=d, month=8) for d in (31,)]
        result = coverage(
            early, evaluated_at=EVAL_AT, window=timedelta(days=14), now=NOW
        )
        self.assertEqual(result["status"], "unresolved_coverage_gap")

    def test_a_covered_window_is_resolvable(self):
        full = [_bar(day=d, month=9) for d in range(1, 14)]
        result = coverage(
            full, evaluated_at=EVAL_AT, window=timedelta(days=14), now=NOW
        )
        self.assertEqual(result["status"], "resolvable")

    def test_no_status_value_is_ever_a_correctness_verdict(self):
        for status in ("unresolved_window_open", "unresolved_no_bars",
                       "unresolved_coverage_gap", "resolvable"):
            self.assertNotIn("correct", status)
            self.assertNotIn("incorrect", status)

    def test_volume_absent_stays_none_and_is_never_zeroed(self):
        self.assertIsNone(_bar(volume=None).to_row()["volume"])

    def test_a_malformed_row_is_skipped_not_repaired(self):
        bad = _bar().to_row()
        bad["close"] = None
        self.assertIsNone(row_to_bar(bad))
        self.assertIsNone(row_to_bar({"symbol": "X"}))
        self.assertIsNone(row_to_bar(None))


# ---------------------------------------------------------------------------
# 7. Schema constraints mirrored in the pure layer
# ---------------------------------------------------------------------------
class TestConstraints(unittest.TestCase):
    def test_unknown_granularity_rejected(self):
        with self.assertRaises(MarketObservationError):
            _bar_with(granularity="1h")

    def test_non_positive_price_rejected(self):
        with self.assertRaises(MarketObservationError):
            _bar(c=0.0)
        with self.assertRaises(MarketObservationError):
            _bar(c=-1.0)

    def test_non_finite_price_rejected(self):
        with self.assertRaises(MarketObservationError):
            _bar(c=float("nan"))
        with self.assertRaises(MarketObservationError):
            _bar(c=float("inf"))

    def test_inconsistent_ohlc_rejected(self):
        with self.assertRaises(MarketObservationError):
            _bar(h=3000.0)          # high below open/close
        with self.assertRaises(MarketObservationError):
            _bar(l=3999.0)          # low above open/close

    def test_negative_volume_rejected(self):
        with self.assertRaises(MarketObservationError):
            _bar(volume=-1.0)

    def test_empty_identity_rejected(self):
        with self.assertRaises(MarketObservationError):
            _bar(symbol="")
        with self.assertRaises(MarketObservationError):
            _bar(instrument="")


def _bar_with(**kwargs):
    base = dict(
        symbol="X", instrument="Gold", granularity=GRANULARITY_1D,
        bar_time=datetime(2026, 8, 30, tzinfo=timezone.utc),
        open=1.0, high=1.0, low=1.0, close=1.0, volume=None, invert=False,
    )
    base.update(kwargs)
    return MarketBar(**base)


# ---------------------------------------------------------------------------
# 8. Anchor semantics
# ---------------------------------------------------------------------------
class TestAnchorSemantics(unittest.TestCase):
    def test_captured_anchor_is_point_in_time(self):
        resolution = classify_anchor(
            _shadow_row(anchor=_CAPTURED_ANCHOR)["record"],
            b2_bridge.symbol_convention("Gold"),
        )
        self.assertIs(resolution.status, AnchorStatus.CAPTURED)
        self.assertTrue(resolution.status.is_point_in_time)
        self.assertEqual(resolution.symbol, "XAUUSD=X")

    def test_legacy_record_is_reconstructed_never_captured(self):
        legacy = _shadow_row(anchor=None, schema_version=1)["record"]
        resolution = classify_anchor(legacy, b2_bridge.symbol_convention("Gold"))
        self.assertIs(resolution.status, AnchorStatus.RECONSTRUCTED)
        self.assertFalse(resolution.status.is_point_in_time)
        self.assertIn(
            "anchor_not_captured_at_evaluation_time", resolution.caveats
        )

    def test_legacy_gold_carries_symbol_uncertainty(self):
        legacy = _shadow_row(anchor=None, schema_version=1)["record"]
        resolution = classify_anchor(legacy, b2_bridge.symbol_convention("Gold"))
        self.assertIn("symbol_uncertain_fallback_configured", resolution.caveats)

    def test_legacy_eur_has_no_symbol_uncertainty(self):
        legacy = _shadow_row(instrument="EUR", anchor=None, schema_version=1)["record"]
        resolution = classify_anchor(legacy, b2_bridge.symbol_convention("EUR"))
        self.assertNotIn("symbol_uncertain_fallback_configured", resolution.caveats)

    def test_no_convention_and_no_anchor_is_missing(self):
        resolution = classify_anchor(
            _shadow_row(anchor=None)["record"], None
        )
        self.assertIs(resolution.status, AnchorStatus.MISSING)

    def test_an_anchor_without_a_price_is_not_captured(self):
        partial = dict(_CAPTURED_ANCHOR, analysis_price=None, last_price=None)
        resolution = classify_anchor(
            _shadow_row(anchor=partial)["record"],
            b2_bridge.symbol_convention("Gold"),
        )
        self.assertIs(resolution.status, AnchorStatus.RECONSTRUCTED)

    def test_a_legacy_execution_price_cross_checks_and_never_replaces(self):
        legacy = _shadow_row(
            anchor=None, schema_version=1, execution={"current_price": 3405.0}
        )["record"]
        resolution = classify_anchor(legacy, b2_bridge.symbol_convention("Gold"))
        self.assertIs(resolution.status, AnchorStatus.RECONSTRUCTED)
        self.assertEqual(resolution.legacy_execution_price, 3405.0)

    def test_a_neutral_regime_legacy_record_has_no_execution_price(self):
        legacy = _shadow_row(
            anchor=None, schema_version=1, execution={"current_price": None}
        )["record"]
        resolution = classify_anchor(legacy, b2_bridge.symbol_convention("Gold"))
        self.assertIsNone(resolution.legacy_execution_price)

    def test_classification_never_writes_an_anchor_back(self):
        record = _shadow_row(anchor=None, schema_version=1)["record"]
        before = json.dumps(record, sort_keys=True)
        classify_anchor(record, b2_bridge.symbol_convention("Gold"))
        self.assertEqual(json.dumps(record, sort_keys=True), before)
        self.assertIsNone(record["market_anchor"])

    def test_fallback_symbol_is_flagged_on_a_captured_anchor(self):
        built = build_market_anchor(
            convention=b2_bridge.symbol_convention("Gold"),
            tactical={"symbol": "GC=F", "analysis_price": 3400.0,
                      "last_price": 3400.0, "market_ts": 1787011200},
            execution_inputs={},
        )
        self.assertTrue(built.symbol_fallback_used)
        self.assertEqual(built.symbol, "GC=F")
        self.assertEqual(built.symbol_requested, "XAUUSD=X")
        resolution = classify_anchor(
            _shadow_row(anchor=built.as_record())["record"],
            b2_bridge.symbol_convention("Gold"),
        )
        self.assertIn("symbol_fallback_used", resolution.caveats)

    def test_no_tactical_result_yields_no_anchor(self):
        self.assertIsNone(
            build_market_anchor(
                convention=b2_bridge.symbol_convention("Gold"),
                tactical=None, execution_inputs={},
            )
        )

    def test_anchor_round_trips_through_a_record(self):
        rebuilt = MarketAnchor.from_record(_CAPTURED_ANCHOR)
        self.assertEqual(rebuilt.as_record()["symbol"], "XAUUSD=X")
        self.assertTrue(rebuilt.has_usable_price)
        self.assertEqual(rebuilt.strength_price, 3410.0)

    def test_strength_price_inverts_a_raw_only_anchor(self):
        raw_only = MarketAnchor(
            analysis_price=None, last_price=150.0, symbol="USDJPY=X",
            symbol_requested="USDJPY=X", symbol_fallback_used=False, invert=True,
            market_ts=None, market_ts_iso="", volatility_scale=None, atr=None,
            atr_ratio=None, volatility_regime="unavailable",
        )
        self.assertAlmostEqual(raw_only.strength_price, 1.0 / 150.0)


# ---------------------------------------------------------------------------
# 9. Anchor capture through the live observation path
# ---------------------------------------------------------------------------
_ENTRY_PLAN = {
    "invalidation": 3280.0, "zone_low": 3320.0, "zone_high": 3340.0,
    "current_analysis_price": 3330.0, "atr": 12.0, "atr_ratio": 1.05,
    "volatility_regime": "normal", "status": "IN ZONE",
}
_NEUTRAL_ENTRY_PLAN = {
    "direction": "WAIT", "status": "NO MACRO EDGE", "zone_low": None,
    "zone_high": None, "invalidation": None, "opportunity_quality": None,
    "volatility_regime": "unavailable", "atr_ratio": None,
}
_TACTICAL = {
    "ret_15m": 0.0021, "ret_1h": 0.0044, "ret_4h": 0.0090,
    "structure": "Upside Breakout", "volatility_scale": 0.0012,
    "entry_plan": _ENTRY_PLAN, "symbol": "XAUUSD=X",
    "analysis_price": 3330.0, "last_price": 3330.0,
    "market_ts": int(EVAL_AT.timestamp()),
}
_NEWS = {"scores": {k: 0.1 for k in
                    ("Gold", "Oil", "Nasdaq", "USD", "EUR", "GBP", "CAD",
                     "JPY", "CHF", "AUD", "NZD")},
         "gold_rule_points": .22, "gold_ai_points": .11}
_COMPOSITE = {"macro_score": 0.31, "rows": [
    {"cat": "rate", "weight": 2.0, "score": -0.35},
    {"cat": "inflation", "weight": 2.0, "score": 0.42},
]}


class _PatchProduction:
    def __init__(self, tactical=None):
        self.tactical = tactical

    def __enter__(self):
        tactical = self.tactical if self.tactical is not None else dict(_TACTICAL)
        self._patchers = [
            mock.patch.object(core, "fetch_all_instant_news", return_value=[]),
            mock.patch.object(core, "analyze_news_rule_based", return_value=dict(_NEWS)),
            mock.patch.object(core, "_calc_gold_score_only", return_value=(.44, "1%", .22)),
            mock.patch.object(core, "_calc_oil_score_only", return_value=(.30, .15)),
            mock.patch.object(core, "_calc_ndx_score_only", return_value=(.25, .18)),
            mock.patch.object(core, "_calc_currency_score_only", return_value=.20),
            mock.patch.object(core, "_oil_price_momentum_score", return_value=.35),
            mock.patch.object(core, "compute_composite", return_value=dict(_COMPOSITE)),
            mock.patch.object(core, "compute_tactical_move", return_value=tactical),
            mock.patch.object(core, "fetch_fred", return_value=None),
            mock.patch.object(core, "fetch_forex_factory_calendar_rolling",
                              return_value=[]),
        ]
        for p in self._patchers:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in reversed(self._patchers):
            p.stop()
        return False


def _observe(instrument, tactical=None):
    b2_bridge._HANDLED_BUCKETS.clear()
    with _PatchProduction(tactical):
        status, evaluation = b2_bridge._build_observation(
            instrument, "KEY", "chan", moment=EVAL_AT,
            horizon=b2_bridge.Horizon.TACTICAL,
            observation_identity=f"k|{instrument}",
        )
    return status, evaluation


class TestAnchorCapture(unittest.TestCase):
    def test_a_new_record_carries_a_captured_anchor(self):
        status, evaluation = _observe("Gold")
        self.assertEqual(status, "ok")
        record = evaluation.record.as_record()
        anchor = record["market_anchor"]
        self.assertIsNotNone(anchor)
        self.assertEqual(anchor["anchor_status"], "anchor_captured")
        self.assertEqual(anchor["symbol"], "XAUUSD=X")
        self.assertEqual(anchor["analysis_price"], 3330.0)
        self.assertEqual(anchor["market_ts"], int(EVAL_AT.timestamp()))
        self.assertEqual(anchor["atr"], 12.0)
        self.assertEqual(anchor["volatility_regime"], "normal")
        self.assertEqual(anchor["granularity"], "5m")

    def test_new_records_are_current_schema_and_post_freeze(self):
        _, evaluation = _observe("Gold")
        # v3 is the freeze BOUNDARY: scale-aware bands, horizon-filtered family
        # evaluation, an entry-plan direction check, Unavailable-preserving
        # adapters, and stored member values with their provenance.
        #
        # SUPERSEDED BY H3, which writes v4 because the serialised meaning of
        # data_confidence changed. The literal 3 pinned "what is written now",
        # which a schema bump is defined to change; the test now tracks the
        # constant instead, so it keeps protecting the real properties -- new
        # records are current, they are post-freeze, and the boundary itself
        # has not moved -- without re-breaking on the next legitimate bump.
        record = evaluation.record.as_record()
        self.assertEqual(record["schema_version"], shadow.CURRENT_SCHEMA_VERSION)
        self.assertEqual(record["evidence_epoch"], "post_freeze")
        self.assertIsNotNone(record["evidence_provenance"])
        self.assertGreaterEqual(
            shadow.CURRENT_SCHEMA_VERSION, shadow.FREEZE_SCHEMA_VERSION
        )
        self.assertEqual(shadow.FREEZE_SCHEMA_VERSION, 3)

    def test_the_anchor_survives_a_neutral_macro_regime(self):
        """The gap Stage D exists to close: no entry plan, still an anchor."""
        tactical = dict(_TACTICAL, entry_plan=dict(_NEUTRAL_ENTRY_PLAN))
        _, evaluation = _observe("Gold", tactical)
        record = evaluation.record.as_record()
        self.assertIsNone(record["execution"]["current_price"])
        self.assertFalse(record["execution"]["invalidation_defined"])
        anchor = record["market_anchor"]
        self.assertEqual(anchor["anchor_status"], "anchor_captured")
        self.assertEqual(anchor["analysis_price"], 3330.0)
        self.assertIsNone(anchor["atr"])

    def test_every_live_instrument_captures_an_anchor(self):
        for instrument in b2_bridge.default_shadow_instruments():
            convention = b2_bridge.symbol_convention(instrument)
            tactical = dict(_TACTICAL, symbol=convention.symbol)
            status, evaluation = _observe(instrument, tactical)
            self.assertEqual(status, "ok", instrument)
            anchor = evaluation.record.as_record()["market_anchor"]
            self.assertEqual(anchor["anchor_status"], "anchor_captured", instrument)
            self.assertEqual(anchor["symbol"], convention.symbol, instrument)
            self.assertEqual(anchor["invert"], convention.invert, instrument)

    def test_anchor_capture_issues_no_extra_request(self):
        """Every anchor field comes from a dict the caller already held."""
        with mock.patch.object(vb.requests, "get") as fake_get:
            _observe("Gold")
            fake_get.assert_not_called()
        source = inspect.getsource(b2_bridge.symbol_convention)
        self.assertNotIn("requests", source)

    def test_a_tactical_result_without_price_fields_still_records_an_anchor(self):
        """Degradation is self-describing rather than silent."""
        tactical = {"ret_15m": 0.002, "ret_1h": 0.004, "ret_4h": 0.009,
                    "structure": "Upside Breakout", "entry_plan": _ENTRY_PLAN}
        _, evaluation = _observe("Gold", tactical)
        anchor = evaluation.record.as_record()["market_anchor"]
        self.assertEqual(anchor["anchor_status"], "anchor_missing")
        self.assertIsNone(anchor["analysis_price"])
        self.assertEqual(anchor["symbol"], "XAUUSD=X")

    def test_the_anchor_changes_no_evaluation_output(self):
        """The anchor travels alongside the evaluation; it never enters it."""
        _, with_price = _observe("Gold")
        stripped = dict(_TACTICAL)
        stripped.pop("analysis_price")
        stripped.pop("symbol")
        _, without = _observe("Gold", stripped)
        for section in ("decision", "confidence", "regime", "families",
                        "scenarios", "claim"):
            self.assertEqual(
                with_price.record.as_record()[section],
                without.record.as_record()[section],
                section,
            )


# ---------------------------------------------------------------------------
# 10. Fetch behaviour and fail-open
# ---------------------------------------------------------------------------
class TestFetchAndFailOpen(unittest.TestCase):
    def test_the_approved_fetch_shape_is_used(self):
        self.assertEqual(vb.DAILY_RANGE, "1mo")
        self.assertEqual(vb.DAILY_INTERVAL, "1d")
        captured = {}

        def fake_get(url, params=None, **kwargs):
            captured["url"] = url
            captured["params"] = params
            return _FakeResponse(_yahoo_payload(_epochs(1, 20), [3400.0] * 20))

        with mock.patch.object(vb.requests, "get", side_effect=fake_get):
            vb.fetch_daily_bars("XAUUSD=X", instrument="Gold", invert=False, now=NOW)
        self.assertIn("query1.finance.yahoo.com/v8/finance/chart", captured["url"])
        self.assertEqual(captured["params"]["range"], "1mo")
        self.assertEqual(captured["params"]["interval"], "1d")

    def test_an_unreachable_endpoint_returns_no_bars_and_never_raises(self):
        with mock.patch.object(vb.requests, "get", side_effect=RuntimeError("down")):
            self.assertEqual(
                vb.fetch_daily_bars("XAUUSD=X", instrument="Gold",
                                    invert=False, now=NOW),
                [],
            )

    def test_a_malformed_payload_returns_no_bars(self):
        for payload in ({}, {"chart": {}}, {"chart": {"result": []}},
                        {"chart": {"result": [{"timestamp": [], "indicators": {}}]}}):
            with mock.patch.object(vb.requests, "get",
                                   return_value=_FakeResponse(payload)):
                self.assertEqual(
                    vb.fetch_daily_bars("XAUUSD=X", instrument="Gold",
                                        invert=False, now=NOW),
                    [],
                )

    def test_too_few_bars_is_discarded_rather_than_partially_trusted(self):
        payload = _yahoo_payload(_epochs(1, 3), [3400.0] * 3)
        with mock.patch.object(vb.requests, "get", return_value=_FakeResponse(payload)):
            self.assertEqual(
                vb.fetch_daily_bars("XAUUSD=X", instrument="Gold",
                                    invert=False, now=NOW),
                [],
            )

    def test_one_malformed_bar_does_not_cost_the_rest(self):
        stamps = _epochs(1, 10)
        closes = [3400.0 + i for i in range(10)]
        closes[4] = None
        payload = _yahoo_payload(stamps, closes)
        with mock.patch.object(vb.requests, "get", return_value=_FakeResponse(payload)):
            captured = vb.fetch_daily_bars("XAUUSD=X", instrument="Gold",
                                           invert=False, now=NOW)
        self.assertEqual(len(captured), 9)

    def test_capture_never_raises_when_the_store_explodes(self):
        payload = _yahoo_payload(_epochs(1, 20), [3400.0] * 20)
        with mock.patch.object(vb.requests, "get", return_value=_FakeResponse(payload)):
            report = vb.capture_daily_bars(
                ["Gold"], store=FakeMarketTable(raise_always=True), now=NOW
            )
        self.assertFalse(report["durable"])
        self.assertEqual(report["backend"], "unavailable")

    def test_gold_daily_capture_uses_fallback_symbol_when_primary_has_no_bars(self):
        primary_payload = _yahoo_payload(_epochs(1, 3), [3400.0] * 3)
        fallback_payload = _yahoo_payload(_epochs(1, 20), [3400.0] * 20)

        def fake_get(url, **kwargs):
            if "XAUUSD" in url:
                return _FakeResponse(primary_payload)
            if "GC%3DF" in url or "GC=F" in url:
                return _FakeResponse(fallback_payload)
            raise AssertionError(f"unexpected symbol request: {url}")

        table = FakeMarketTable()

        with mock.patch.object(vb.requests, "get", side_effect=fake_get):
            report = vb.capture_daily_bars(
                ["Gold"],
                store=table,
                now=NOW,
            )

        self.assertEqual(report["instruments"]["Gold"], "fetched")
        self.assertEqual(report["symbols"]["Gold"], "GC=F")
        self.assertGreater(report["inserted"], 0)
        self.assertTrue(
            all(row["symbol"] == "GC=F" for row in table.rows.values())
        )

    def test_one_failing_instrument_does_not_stop_the_others(self):
        def fake_get(url, **kwargs):
            if "GC" in url or "CL" in url:
                raise RuntimeError("down")
            return _FakeResponse(_yahoo_payload(_epochs(1, 20), [3400.0] * 20))

        table = FakeMarketTable()
        with mock.patch.object(vb.requests, "get", side_effect=fake_get):
            report = vb.capture_daily_bars(["Gold", "Oil", "EUR"],
                                           store=table, now=NOW)
        self.assertEqual(report["instruments"]["Oil"], "no_bars")
        self.assertEqual(report["instruments"]["EUR"], "fetched")
        self.assertGreater(report["inserted"], 0)

    def test_an_unknown_instrument_is_reported_not_fabricated(self):
        report = vb.capture_daily_bars(["NOT_AN_ASSET"],
                                       store=FakeMarketTable(), now=NOW)
        self.assertEqual(report["instruments"]["NOT_AN_ASSET"], "unknown_instrument")
        self.assertEqual(report["inserted"], 0)

    def test_supabase_unavailable_is_fail_open_and_not_durable(self):
        store = vb.SupabaseMarketObservationStore()
        with mock.patch.object(core, "_supabase_enabled", return_value=False):
            outcome = store.insert_rows([_bar().to_row()])
        self.assertEqual(outcome.backend, "unavailable")
        self.assertFalse(outcome.durable)
        self.assertEqual(len(outcome.failed), 1)

    def test_a_timeout_is_reported_failed_and_never_raises(self):
        store = vb.SupabaseMarketObservationStore()
        with mock.patch.object(core, "_supabase_enabled", return_value=True), \
             mock.patch.object(core, "SUPABASE_URL", "https://x.example"), \
             mock.patch.object(vb.requests, "post", side_effect=TimeoutError("t")):
            outcome = store.insert_rows([_bar().to_row()])
        self.assertFalse(outcome.durable)
        self.assertEqual(len(outcome.failed), 1)

    def test_batch_rejection_falls_back_to_per_row_isolation(self):
        table = FakeMarketTable(raise_on_batch=True)
        outcome = table.insert_rows([_bar(day=d).to_row() for d in (26, 27, 28)])
        self.assertEqual(len(outcome.inserted), 3)


# ---------------------------------------------------------------------------
# 11. Local fallback
# ---------------------------------------------------------------------------
class TestLocalFallback(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".jsonl")
        os.close(handle)
        os.unlink(self.path)
        self.store = vb.LocalMarketObservationStore(self.path)

    def tearDown(self):
        if os.path.exists(self.path):
            os.unlink(self.path)

    def test_a_local_write_is_never_reported_durable(self):
        outcome = self.store.insert_rows([_bar().to_row()])
        self.assertEqual(outcome.backend, "local")
        self.assertFalse(outcome.durable)
        self.assertEqual(len(outcome.inserted), 1)

    def test_local_is_append_only_and_deduplicates(self):
        row = _bar().to_row()
        self.store.insert_rows([row])
        again = self.store.insert_rows([row])
        self.assertEqual(len(again.inserted), 0)
        self.assertEqual(len(again.duplicate), 1)
        with open(self.path, encoding="utf-8") as handle:
            self.assertEqual(len([l for l in handle if l.strip()]), 1)

    def test_local_surfaces_a_content_conflict(self):
        self.store.insert_rows([_bar(c=3410.0).to_row()])
        outcome = self.store.insert_rows([_bar(c=3395.0).to_row()])
        self.assertEqual(len(outcome.conflicted), 1)
        self.assertEqual(len(outcome.inserted), 0)

    def test_local_query_respects_symbol_granularity_and_window(self):
        self.store.insert_rows([
            _bar(day=1, month=9).to_row(),
            _bar(day=5, month=9).to_row(),
            _bar(day=20, month=9).to_row(),
            _bar(symbol="GC=F", day=5, month=9).to_row(),
        ])
        rows = self.store.query_bars(
            symbols=["XAUUSD=X"],
            start=datetime(2026, 8, 31, tzinfo=timezone.utc),
            end=datetime(2026, 9, 10, tzinfo=timezone.utc),
        )
        self.assertEqual([r["bar_time"][:10] for r in rows],
                         ["2026-09-01", "2026-09-05"])

    def test_resolve_picks_local_when_supabase_is_not_configured(self):
        with mock.patch.object(core, "_supabase_enabled", return_value=False):
            self.assertIsInstance(
                vb.resolve_market_store(), vb.LocalMarketObservationStore
            )

    def test_resolve_picks_supabase_when_configured(self):
        with mock.patch.object(core, "_supabase_enabled", return_value=True):
            self.assertIsInstance(
                vb.resolve_market_store(), vb.SupabaseMarketObservationStore
            )


# ---------------------------------------------------------------------------
# 12. Resolution, sharing and the N+1 guarantee
# ---------------------------------------------------------------------------
class TestResolution(unittest.TestCase):
    def _seeded(self):
        table = FakeMarketTable()
        table.insert_rows(
            [_bar(day=d, month=8).to_row() for d in (28, 29, 30, 31)]
            + [_bar(day=d, month=9).to_row() for d in range(1, 14)]
        )
        return table

    def test_the_collision_pair_resolves_from_the_same_shared_bars(self):
        """Two point-in-time observations, one record_id, one set of bars."""
        table = self._seeded()
        records = [
            _shadow_row("sid-a", evaluated_at=EVAL_AT, record_id="same-logical",
                        anchor=_CAPTURED_ANCHOR),
            _shadow_row("sid-b", evaluated_at=EVAL_AT_2, record_id="same-logical",
                        anchor=_CAPTURED_ANCHOR),
        ]
        report = vb.resolve_range(records, store=table, now=NOW)
        self.assertEqual(len(report["resolved"]), 2)
        self.assertEqual({r["record_id"] for r in report["resolved"]},
                         {"same-logical"})
        self.assertEqual({r["storage_id"] for r in report["resolved"]},
                         {"sid-a", "sid-b"})
        self.assertEqual(report["resolved"][0]["forward_bars"],
                         report["resolved"][1]["forward_bars"])
        # One shared set of bars, stored once.
        self.assertEqual(len([r for r in table.rows.values()
                              if r["bar_time"].startswith("2026-09-01")]), 1)

    def test_range_resolution_issues_exactly_one_bar_query(self):
        table = self._seeded()
        records = [
            _shadow_row(f"sid-{i}", evaluated_at=EVAL_AT + timedelta(hours=i),
                        record_id=f"logical-{i}", anchor=_CAPTURED_ANCHOR)
            for i in range(25)
        ]
        report = vb.resolve_range(records, store=table, now=NOW)
        self.assertEqual(table.query_calls, 1)
        self.assertEqual(report["queries"], 1)
        self.assertEqual(len(report["resolved"]), 25)

    def test_a_record_with_no_anchor_is_unvalidatable_not_incorrect(self):
        table = self._seeded()
        record = _shadow_row("sid-x", anchor=None, schema_version=1)
        with mock.patch.object(b2_bridge, "symbol_convention", return_value=None), \
             mock.patch.object(vb, "symbol_convention", return_value=None):
            report = vb.resolve_range([record], store=table, now=NOW)
        self.assertEqual(report["resolved"][0]["status"], "unvalidatable_no_anchor")

    def test_a_legacy_record_resolves_as_reconstructed(self):
        table = self._seeded()
        record = _shadow_row("sid-legacy", anchor=None, schema_version=1)
        report = vb.resolve_range([record], store=table, now=NOW)
        resolved = report["resolved"][0]
        self.assertEqual(resolved["anchor"]["anchor_status"], "anchor_reconstructed")
        self.assertFalse(resolved["anchor"]["point_in_time"])
        self.assertGreater(resolved["forward_bars"], 0)

    def test_resolution_excludes_the_straddling_bar(self):
        table = self._seeded()
        record = _shadow_row("sid-a", anchor=_CAPTURED_ANCHOR)
        report = vb.resolve_range([record], store=table, now=NOW)
        self.assertEqual(report["resolved"][0]["first_forward_bar"][:10], "2026-08-31")

    def test_a_bad_timestamp_is_unvalidatable_not_guessed(self):
        record = _shadow_row("sid-a", anchor=_CAPTURED_ANCHOR)
        record["record"]["evaluated_at"] = "not-a-timestamp"
        result = vb.resolve_observation(record, [], now=NOW)
        self.assertEqual(result["status"], "unvalidatable_bad_timestamp")

    def test_empty_input_does_no_work(self):
        table = self._seeded()
        report = vb.resolve_range([], store=table, now=NOW)
        self.assertEqual(report["queries"], 0)
        self.assertEqual(table.query_calls, 0)

    def test_forward_window_comes_from_the_architecture_not_a_new_constant(self):
        from apex.b2.horizons import HORIZON_EVALUATION_WINDOW
        from apex.b2.enums import Horizon
        for horizon in Horizon:
            self.assertEqual(
                vb.forward_window_for(horizon.value),
                HORIZON_EVALUATION_WINDOW[horizon],
            )
        self.assertEqual(vb.forward_window_for("nonsense"), timedelta(days=14))

    def test_anchor_census_counts_every_status(self):
        census = vb.anchor_census([
            _shadow_row("a", anchor=_CAPTURED_ANCHOR),
            _shadow_row("b", anchor=None, schema_version=1),
            _shadow_row("c", instrument="EUR", anchor=None, schema_version=1),
        ])
        self.assertEqual(census["total"], 3)
        self.assertEqual(census["by_status"]["anchor_captured"], 1)
        self.assertEqual(census["by_status"]["anchor_reconstructed"], 2)
        self.assertIn("Gold", census["by_instrument"])


# ---------------------------------------------------------------------------
# 13. Production safety
# ---------------------------------------------------------------------------
class TestProductionSafety(unittest.TestCase):
    def test_production_core_never_mentions_the_market_table(self):
        source = inspect.getsource(core)
        for name in ("b2_market_observations", "market_anchor",
                     "SupabaseMarketObservationStore", "b2_validation_bridge",
                     "capture_daily_bars"):
            self.assertNotIn(name, source, name)

    def test_production_core_still_has_exactly_one_b2_import(self):
        tree = ast.parse(inspect.getsource(core))
        imports = [
            n for n in ast.walk(tree)
            if isinstance(n, ast.ImportFrom) and "b2" in (n.module or "")
        ]
        self.assertEqual(len(imports), 1)
        self.assertEqual(tuple(a.name for a in imports[0].names),
                         ("run_shadow_observation",))

    def test_the_validation_bridge_starts_no_thread_scheduler_or_daemon(self):
        tree = ast.parse(inspect.getsource(vb))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [a.name for a in node.names] + [getattr(node, "module", "") or ""]
                for name in names:
                    for forbidden in ("threading", "sched", "asyncio",
                                      "multiprocessing", "subprocess", "crontab"):
                        self.assertNotIn(forbidden, name)
        source = inspect.getsource(vb)
        for forbidden in ("Thread(", "Timer(", "time.sleep", "while True"):
            self.assertNotIn(forbidden, source, forbidden)

    def test_no_ai_or_telegram_call_in_the_validation_layer(self):
        for module in (vb, bars_mod, anchor_mod, revisions_mod, series_pins_mod, outcomes_mod):
            names = {n.lower() for n in _referenced_names(module)}
            for forbidden in ("telegram", "sendmessage", "openai", "anthropic",
                              "generativelanguage", "completions", "gemini",
                              "groq", "send_telegram"):
                self.assertFalse(
                    any(forbidden in name for name in names),
                    f"{module.__name__} references {forbidden}",
                )

    def test_the_pure_layer_performs_no_io(self):
        for module in (bars_mod, anchor_mod, revisions_mod, series_pins_mod, outcomes_mod):
            tree = ast.parse(inspect.getsource(module))
            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    names = [a.name for a in node.names] + [
                        getattr(node, "module", "") or ""
                    ]
                    for name in names:
                        for forbidden in ("requests", "streamlit", "threading",
                                          "production_core", "socket", "urllib"):
                            self.assertNotIn(forbidden, name, f"{module.__name__}:{name}")

    def test_the_validation_bridge_never_touches_the_persistence_lock(self):
        names = _referenced_names(vb)
        for forbidden in ("_save_persistent_state", "_load_persistent_state",
                          "_PERSISTENCE_LOCK", "_supabase_save_state"):
            self.assertNotIn(forbidden, names, forbidden)

    def test_the_production_lock_is_free_during_a_market_insert(self):
        table = FakeMarketTable()
        acquired = core._PERSISTENCE_LOCK.acquire(blocking=False)
        try:
            self.assertTrue(acquired, "lock was already held before the test")
            table.insert_rows([_bar().to_row()])
        finally:
            if acquired:
                core._PERSISTENCE_LOCK.release()

    def test_no_secret_is_logged_or_returned(self):
        source = inspect.getsource(vb)
        self.assertNotIn("print(", source)
        self.assertNotIn("SUPABASE_SERVICE_ROLE_KEY", source)

    def test_no_ddl_verb_appears_anywhere(self):
        # H8 extends this guard to the operational package. sql/003 creates
        # b2_ops_job_health and is run BY HAND by an operator, exactly like
        # every other file in sql/. The runtime that reads and writes that table
        # must not be able to create, alter or drop it -- otherwise "this
        # application never executes DDL" would quietly become "except for the
        # newest table", which is how the guarantee dies.
        from apex.ops import (
            config as ops_config,
            heartbeat as ops_heartbeat,
            lease as ops_lease,
            logging as ops_logging,
            runner as ops_runner,
        )

        for module in (vb, bars_mod, anchor_mod, revisions_mod, series_pins_mod,
                       outcomes_mod, ops_config, ops_heartbeat, ops_lease,
                       ops_logging, ops_runner):
            source = inspect.getsource(module).upper()
            for verb in ("CREATE TABLE", "ALTER TABLE", "DROP TABLE",
                         "TRUNCATE", "CREATE INDEX"):
                self.assertNotIn(verb, source, f"{module.__name__}: {verb}")

    def test_production_signal_thresholds_are_unchanged(self):
        self.assertEqual(core.bias_from_score(0.40)[0], "🚀 Strong Bullish")
        self.assertEqual(core.bias_from_score(-0.40)[0], "🔻 Strong Bearish")
        self.assertEqual(core._broad_regime("🚀 Strong Bullish"), "Bullish")

    def test_cross_asset_remains_withheld_on_every_new_record(self):
        _, evaluation = _observe("Gold")
        self.assertEqual(
            evaluation.record.as_record()["cross_asset"]["status"], "withheld"
        )

    def test_the_shadow_mode_label_is_preserved(self):
        _, evaluation = _observe("Gold")
        self.assertEqual(
            evaluation.record.as_record()["mode"],
            "SHADOW / NON-PRODUCTION / UNCALIBRATED",
        )

    def test_all_ten_application_routes_still_import(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        pages = sorted(
            f for f in os.listdir(os.path.join(root, "pages")) if f.endswith(".py")
        )
        self.assertEqual(len(pages), 10)
        for page in pages:
            with open(os.path.join(root, "pages", page), encoding="utf-8") as handle:
                ast.parse(handle.read())


# ---------------------------------------------------------------------------
# 14. Storage V2 and collision-recovery regression
# ---------------------------------------------------------------------------
class TestStorageV2Regression(unittest.TestCase):
    def test_storage_identity_is_unaffected_by_the_anchor(self):
        """storage_id derives from record_id|instrument|horizon|evaluated_at,
        none of which the anchor touches."""
        without = shadow.canonical_storage_id(
            "rid", "Gold", "tactical", EVAL_AT.isoformat()
        )
        _, evaluation = _observe("Gold")
        record = evaluation.record.as_record()
        with_anchor = shadow.canonical_storage_id(
            "rid", "Gold", "tactical", EVAL_AT.isoformat()
        )
        self.assertEqual(without, with_anchor)
        self.assertIsNotNone(record["market_anchor"])

    def test_record_to_row_promotes_the_new_schema_version(self):
        _, evaluation = _observe("Gold")
        row = shadow.record_to_row(evaluation.record.as_record())
        # SUPERSEDED BY H3 (constant only). The property protected here is that
        # record_to_row PROMOTES the payload's version into its own column and
        # the two agree -- not that the number is 3. Asserting the agreement
        # against the live constant is what that property actually says.
        self.assertEqual(row["schema_version"], shadow.CURRENT_SCHEMA_VERSION)
        self.assertEqual(
            row["record"]["schema_version"], shadow.CURRENT_SCHEMA_VERSION
        )
        self.assertEqual(row["schema_version"], row["record"]["schema_version"])
        self.assertIn("market_anchor", row["record"])

    def test_legacy_rows_are_never_rewritten_by_the_read_path(self):
        table = FakeMarketTable()
        legacy = _shadow_row("legacy-1", anchor=None, schema_version=1)
        before = json.dumps(legacy, sort_keys=True)
        vb.resolve_range([legacy], store=table, now=NOW)
        vb.anchor_census([legacy])
        self.assertEqual(json.dumps(legacy, sort_keys=True), before)

    def test_a_legacy_row_keeps_schema_version_one(self):
        legacy = _shadow_row("legacy-1", anchor=None, schema_version=1)
        self.assertEqual(legacy["schema_version"], 1)
        self.assertIsNone(legacy["record"]["market_anchor"])

    def test_no_anchor_is_ever_fabricated_for_a_legacy_row(self):
        legacy = _shadow_row("legacy-1", anchor=None, schema_version=1)
        resolution = classify_anchor(
            legacy["record"], b2_bridge.symbol_convention("Gold")
        )
        self.assertIsNone(resolution.anchor)
        self.assertIs(resolution.status, AnchorStatus.RECONSTRUCTED)

    def test_query_records_now_selects_the_physical_identity(self):
        import inspect as _inspect
        signature = _inspect.signature(
            b2_bridge.SupabaseShadowRecordStore.query_records
        )
        select = signature.parameters["select"].default
        self.assertIn("storage_id", select)
        self.assertIn("content_hash", select)
        self.assertIn("record", select)

    def test_the_eleven_instrument_set_is_unchanged(self):
        self.assertEqual(
            b2_bridge.default_shadow_instruments(),
            ("AUD", "CAD", "CHF", "EUR", "GBP", "Gold", "JPY", "NDX", "NZD",
             "Oil", "USD"),
        )


if __name__ == "__main__":
    unittest.main()
