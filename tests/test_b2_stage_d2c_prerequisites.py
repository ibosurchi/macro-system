"""Stage D-2C0 + D-2C1: bar canonicalization and horizon-scaled neutral band.

Prerequisite blocker fixes only. There is deliberately NO outcome resolver
after this stage: D-2C2 onward are separate, separately approved steps, and a
test here asserts that ``resolve.py`` and ``metrics.py`` still do not exist.

Two live defects are pinned by the first two classes:

    TestCanonicalOrdering  -- terminal_bar depended on the order the caller
                              happened to supply bars in, because Python's
                              stable sort leaves tied timestamps alone.
    TestNeutralBandScaling -- the band compared a 70-minute ATR against a
                              14-day move, understating it ~17x.

Imports ``apex.production_core`` (for the safety assertions), so durable-state
isolation is installed first. Nothing here performs I/O.
"""
from __future__ import annotations

import ast
import inspect
import json
import math
import os
import subprocess
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from apex import production_core as core
from state_isolation import isolate_durable_state

isolate_durable_state()

from apex.b2.enums import Horizon
from apex.b2.horizons import HORIZON_EVALUATION_WINDOW
from apex.b2.validation import bars as bars_mod
from apex.b2.validation import config as config_mod
from apex.b2.validation import outcome as outcome_mod
from apex.b2.validation.bars import (
    GRANULARITY_1D,
    GRANULARITY_SECONDS,
    BarCanonicalization,
    BarConflict,
    MarketBar,
    RowConversion,
    bars_from_rows,
    canonical_sort_key,
    canonicalize_bars,
    forward_bars,
    path_bars,
    row_to_bar,
    terminal_bar,
)
from apex.b2.validation.config import (
    DEFAULT_VALIDATION_CONFIG,
    NEUTRAL_BAND_ATR,
    NEUTRAL_BAND_VOLATILITY_SCALE,
    BandMode,
    BandUnavailableReason,
    ValidationConfig,
    neutral_band,
)
from apex.b2.validation.outcome import ExclusionReason

UTC = timezone.utc
EVAL_AT = datetime(2026, 8, 30, 22, 4, 43, 893828, tzinfo=UTC)
TACTICAL = HORIZON_EVALUATION_WINDOW[Horizon.TACTICAL]

#: The audited live sample: Gold anchor, 5-minute tactical series.
SAMPLE_ATR = 12.0
SAMPLE_PRICE = 3330.0
SAMPLE_VOL = 0.0012


def _identifiers(obj) -> set[str]:
    """Every identifier a function or module's CODE touches.

    AST, not text. These functions deliberately NAME the things they must never
    use ("captured_at is not a tiebreaker", "no randomness"), and a substring
    scan would fire on the documentation instead of the code.
    """
    tree = ast.parse(inspect.getsource(obj).lstrip())
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


def _bar(day, *, month=9, hour=0, close=100.5, symbol="EURUSD=X",
         instrument="EUR", invert=False, granularity=GRANULARITY_1D):
    """A valid bar. ``close`` varies the content hash without breaking OHLC."""
    return MarketBar(
        symbol=symbol, instrument=instrument, granularity=granularity,
        bar_time=datetime(2026, month, day, hour, tzinfo=UTC),
        open=100.0, high=max(101.0, close), low=min(99.0, close),
        close=close, volume=None, invert=invert,
    )


# ===========================================================================
# 1. D-2C0 -- TOTAL DETERMINISTIC ORDERING   (tests 1, 2, 11)
# ===========================================================================
class TestCanonicalOrdering(unittest.TestCase):
    def test_same_bars_any_input_order_canonicalize_identically(self):
        bars = [_bar(d) for d in (5, 1, 9, 3, 7)]
        orders = [
            list(bars),
            list(reversed(bars)),
            [bars[2], bars[0], bars[4], bars[1], bars[3]],
        ]
        results = [
            [b.observation_id for b in canonicalize_bars(o).bars] for o in orders
        ]
        self.assertEqual(results[0], results[1])
        self.assertEqual(results[0], results[2])
        self.assertEqual([b.bar_time.day for b in canonicalize_bars(orders[1]).bars],
                         [1, 3, 5, 7, 9])

    def test_tied_bar_time_is_ordered_by_observation_id(self):
        """Two DIFFERENT series at the same instant: a legitimate tie."""
        a = _bar(3, symbol="EURUSD=X", instrument="EUR")
        b = _bar(3, symbol="GBPUSD=X", instrument="GBP")
        self.assertEqual(a.bar_time, b.bar_time)
        self.assertNotEqual(a.observation_id, b.observation_id)
        expected = sorted([a, b], key=lambda x: x.observation_id)
        for order in ([a, b], [b, a]):
            got = canonicalize_bars(order).bars
            self.assertEqual([x.observation_id for x in got],
                             [x.observation_id for x in expected])

    def test_terminal_bar_is_order_independent_on_a_tie(self):
        """The exact live defect: terminal selection followed input order."""
        a = _bar(3, symbol="EURUSD=X", instrument="EUR", close=100.5)
        b = _bar(3, symbol="GBPUSD=X", instrument="GBP", close=200.0)
        first = terminal_bar([a, b], evaluated_at=EVAL_AT, window=TACTICAL)
        second = terminal_bar([b, a], evaluated_at=EVAL_AT, window=TACTICAL)
        self.assertEqual(first.observation_id, second.observation_id)

    def test_path_bars_are_order_independent(self):
        bars = [_bar(d) for d in (4, 1, 6, 2)]
        one = path_bars(bars, evaluated_at=EVAL_AT, window=TACTICAL)
        two = path_bars(list(reversed(bars)), evaluated_at=EVAL_AT, window=TACTICAL)
        self.assertEqual([b.observation_id for b in one],
                         [b.observation_id for b in two])

    def test_canonical_sort_key_is_total_and_uses_no_captured_at(self):
        key = canonical_sort_key(_bar(3))
        self.assertEqual(len(key), 2)
        self.assertIsInstance(key[0], datetime)
        self.assertIsInstance(key[1], str)
        self.assertNotIn("captured_at", _identifiers(bars_mod.canonical_sort_key))

    def test_canonicalization_does_not_mutate_the_input(self):
        bars = [_bar(d) for d in (5, 1, 3)]
        before_ids = [b.observation_id for b in bars]
        before_json = json.dumps([b.to_row() for b in bars], sort_keys=True)
        canonicalize_bars(bars)
        self.assertEqual([b.observation_id for b in bars], before_ids)
        self.assertEqual(
            json.dumps([b.to_row() for b in bars], sort_keys=True), before_json
        )

    def test_empty_input_is_handled(self):
        result = canonicalize_bars([])
        self.assertEqual(result.bars, ())
        self.assertEqual(result.duplicates_collapsed, 0)
        self.assertFalse(result.has_conflict)


# ===========================================================================
# 2. D-2C0 -- IDENTICAL DUPLICATES   (tests 3, 4, 5)
# ===========================================================================
class TestIdenticalDuplicates(unittest.TestCase):
    def test_identical_duplicate_collapses_to_one(self):
        a, b = _bar(3), _bar(3)
        self.assertEqual(a.observation_id, b.observation_id)
        self.assertEqual(a.content_hash, b.content_hash)
        result = canonicalize_bars([a, b])
        self.assertEqual(len(result.bars), 1)
        self.assertEqual(result.duplicates_collapsed, 1)
        self.assertFalse(result.has_conflict)

    def test_many_identical_duplicates_collapse_and_are_counted(self):
        result = canonicalize_bars([_bar(3) for _ in range(5)])
        self.assertEqual(len(result.bars), 1)
        self.assertEqual(result.duplicates_collapsed, 4)

    def test_duplicate_does_not_change_path_bar_count(self):
        unique = [_bar(d) for d in (1, 2, 3)]
        withdupe = unique + [_bar(2)]
        self.assertEqual(
            len(path_bars(unique, evaluated_at=EVAL_AT, window=TACTICAL)),
            len(path_bars(withdupe, evaluated_at=EVAL_AT, window=TACTICAL)),
        )

    def test_duplicate_does_not_change_terminal_bar(self):
        unique = [_bar(d) for d in (1, 2, 3)]
        withdupe = unique + [_bar(3), _bar(3)]
        self.assertEqual(
            terminal_bar(unique, evaluated_at=EVAL_AT, window=TACTICAL).observation_id,
            terminal_bar(withdupe, evaluated_at=EVAL_AT, window=TACTICAL).observation_id,
        )

    def test_duplicate_does_not_shift_positional_indices(self):
        """bars_to_mfe/bars_to_mae will be indices into this sequence."""
        unique = [_bar(d) for d in (1, 2, 3, 4)]
        withdupe = [_bar(1), _bar(1), _bar(2), _bar(3), _bar(3), _bar(4)]
        a = path_bars(unique, evaluated_at=EVAL_AT, window=TACTICAL)
        b = path_bars(withdupe, evaluated_at=EVAL_AT, window=TACTICAL)
        self.assertEqual([x.bar_time.day for x in a], [x.bar_time.day for x in b])

    def test_a_duplicate_is_never_a_failure_or_a_conflict(self):
        result = canonicalize_bars([_bar(3), _bar(3)])
        self.assertFalse(result.has_conflict)
        self.assertEqual(result.conflicts, ())


# ===========================================================================
# 3. D-2C0 -- CONFLICTING DUPLICATES   (tests 6, 7, 8, 9)
# ===========================================================================
class TestConflictingDuplicates(unittest.TestCase):
    def setUp(self):
        self.a = _bar(3, close=100.5)
        self.b = _bar(3, close=200.0)

    def test_same_identity_different_content_is_a_conflict(self):
        self.assertEqual(self.a.observation_id, self.b.observation_id)
        self.assertNotEqual(self.a.content_hash, self.b.content_hash)
        result = canonicalize_bars([self.a, self.b])
        self.assertTrue(result.has_conflict)
        self.assertEqual(len(result.conflicts), 1)
        self.assertEqual(result.conflicts[0].observation_id, self.a.observation_id)

    def test_conflicting_bars_are_withheld_entirely(self):
        """Neither is chosen. Both are absent from the canonical set."""
        result = canonicalize_bars([self.a, self.b])
        self.assertEqual(result.bars, ())
        self.assertEqual(
            path_bars([self.a, self.b], evaluated_at=EVAL_AT, window=TACTICAL), ()
        )
        self.assertIsNone(
            terminal_bar([self.a, self.b], evaluated_at=EVAL_AT, window=TACTICAL)
        )

    def test_conflict_is_never_resolved_by_input_order(self):
        one = canonicalize_bars([self.a, self.b])
        two = canonicalize_bars([self.b, self.a])
        self.assertEqual(one.bars, two.bars)
        self.assertEqual(one.conflicts[0].content_hashes,
                         two.conflicts[0].content_hashes)

    def test_conflict_hashes_are_sorted_so_the_record_is_order_free(self):
        conflict = canonicalize_bars([self.b, self.a]).conflicts[0]
        self.assertEqual(list(conflict.content_hashes),
                         sorted(conflict.content_hashes))

    def test_captured_at_is_not_used_to_arbitrate(self):
        """captured_at is not even a field on MarketBar, and must not become one
        for this purpose."""
        self.assertNotIn("captured_at", MarketBar.__dataclass_fields__)
        self.assertNotIn("captured_at", _identifiers(bars_mod.canonicalize_bars))

    def test_conflicting_values_are_never_averaged(self):
        result = canonicalize_bars([self.a, self.b])
        self.assertEqual(result.bars, ())
        for bar in result.bars:
            self.assertNotAlmostEqual(bar.close, (100.5 + 200.0) / 2.0)

    def test_a_conflict_does_not_discard_unrelated_bars(self):
        others = [_bar(d) for d in (1, 5)]
        result = canonicalize_bars([self.a, self.b] + others)
        self.assertEqual(len(result.bars), 2)
        self.assertEqual([b.bar_time.day for b in result.bars], [1, 5])
        self.assertTrue(result.has_conflict)

    def test_multiple_independent_conflicts_are_deterministic(self):
        c, d = _bar(7, close=100.5), _bar(7, close=300.0)
        forward = canonicalize_bars([self.a, self.b, c, d])
        backward = canonicalize_bars([d, c, self.b, self.a])
        self.assertEqual(len(forward.conflicts), 2)
        self.assertEqual(
            [x.observation_id for x in forward.conflicts],
            [x.observation_id for x in backward.conflicts],
        )
        self.assertEqual([x.bar_time.day for x in forward.conflicts], [3, 7])

    def test_conflict_record_serialises(self):
        record = canonicalize_bars([self.a, self.b]).as_record()
        self.assertTrue(record["has_conflict"])
        self.assertEqual(len(record["conflicts"]), 1)
        json.dumps(record)


# ===========================================================================
# 4. D-2C0 -- MALFORMED BAR ACCOUNTING   (test 10)
# ===========================================================================
class TestMalformedAccounting(unittest.TestCase):
    def test_malformed_rows_are_counted_not_repaired(self):
        good = _bar(3).to_row()
        bad_close = dict(good, close=None)
        missing = {"symbol": "EURUSD=X"}
        result = bars_from_rows([good, bad_close, missing, None])
        self.assertEqual(len(result.bars), 1)
        self.assertEqual(result.malformed, 3)

    def test_row_to_bar_behaviour_is_unchanged(self):
        self.assertIsNone(row_to_bar(None))
        self.assertIsNone(row_to_bar({"symbol": "X"}))
        self.assertIsNotNone(row_to_bar(_bar(3).to_row()))

    def test_all_rows_good_reports_zero_malformed(self):
        rows = [_bar(d).to_row() for d in (1, 2, 3)]
        result = bars_from_rows(rows)
        self.assertEqual(len(result.bars), 3)
        self.assertEqual(result.malformed, 0)

    def test_malformed_is_not_a_directional_failure(self):
        record = bars_from_rows([{"broken": True}]).as_record()
        self.assertEqual(record["malformed_skipped"], 1)
        self.assertNotIn("fail", json.dumps(record).lower())

    def test_conversion_is_deterministic(self):
        rows = [_bar(d).to_row() for d in (3, 1, 2)] + [{"bad": 1}]
        first, second = bars_from_rows(rows), bars_from_rows(rows)
        self.assertEqual([b.observation_id for b in first.bars],
                         [b.observation_id for b in second.bars])
        self.assertEqual(first.malformed, second.malformed)


# ===========================================================================
# 5. D-2C0 -- PRESERVED CONTRACTS / NO LOOKAHEAD   (tests 12-16)
# ===========================================================================
class TestPreservedContracts(unittest.TestCase):
    def test_forward_bars_selection_is_unchanged(self):
        """D-1 open-time contract: a bar closing past window_end is still IN."""
        crossing = _bar(13)                       # closes 14 Sep 00:00
        self.assertEqual(
            len(forward_bars([crossing], evaluated_at=EVAL_AT, window=TACTICAL)), 1
        )
        self.assertEqual(
            forward_bars([_bar(30, month=8)], evaluated_at=EVAL_AT, window=TACTICAL), ()
        )
        self.assertEqual(
            forward_bars([_bar(20)], evaluated_at=EVAL_AT, window=TACTICAL), ()
        )

    def test_forward_bars_does_not_deduplicate(self):
        """Its job is 'which bars came after', not 'which are usable evidence'."""
        self.assertEqual(
            len(forward_bars([_bar(3), _bar(3)], evaluated_at=EVAL_AT,
                             window=TACTICAL)), 2
        )

    def test_path_bars_lower_bound_remains_strict(self):
        at_boundary = _bar(30, month=8, hour=22)
        exactly = MarketBar(
            symbol="EURUSD=X", instrument="EUR", granularity=GRANULARITY_1D,
            bar_time=EVAL_AT, open=100.0, high=101.0, low=99.0, close=100.5,
            volume=None, invert=False,
        )
        self.assertEqual(
            path_bars([exactly], evaluated_at=EVAL_AT, window=TACTICAL), ()
        )
        self.assertEqual(
            path_bars([at_boundary], evaluated_at=EVAL_AT, window=TACTICAL), ()
        )

    def test_straddling_terminal_close_remains_excluded(self):
        self.assertEqual(
            path_bars([_bar(13)], evaluated_at=EVAL_AT, window=TACTICAL), ()
        )

    def test_close_exactly_at_window_end_is_included(self):
        evaluated = datetime(2026, 9, 1, tzinfo=UTC)
        window = timedelta(days=2)                # ends 3 Sep 00:00
        inside = _bar(2)                          # closes 3 Sep 00:00
        self.assertEqual(
            len(path_bars([inside], evaluated_at=evaluated, window=window)), 1
        )

    def test_poison_pill_future_bar_has_no_effect(self):
        base = [_bar(d) for d in (1, 2, 3)]
        poisoned = base + [_bar(25, close=9999.0), _bar(30, close=1.0)]
        self.assertEqual(
            [b.observation_id for b in path_bars(base, evaluated_at=EVAL_AT,
                                                 window=TACTICAL)],
            [b.observation_id for b in path_bars(poisoned, evaluated_at=EVAL_AT,
                                                 window=TACTICAL)],
        )
        self.assertEqual(
            terminal_bar(base, evaluated_at=EVAL_AT, window=TACTICAL).observation_id,
            terminal_bar(poisoned, evaluated_at=EVAL_AT,
                         window=TACTICAL).observation_id,
        )

    def test_unknown_granularity_still_excluded_from_the_path(self):
        odd = _bar(3)
        object.__setattr__(odd, "granularity", "1h")
        self.assertEqual(
            path_bars([odd], evaluated_at=EVAL_AT, window=TACTICAL), ()
        )


# ===========================================================================
# 6. D-2C1 -- NEUTRAL BAND SCALING   (tests 17-25)
# ===========================================================================
class TestNeutralBandScaling(unittest.TestCase):
    def _sample(self, **kw):
        params = dict(horizon="tactical", anchor_granularity="5m",
                      atr=SAMPLE_ATR, volatility_scale=SAMPLE_VOL,
                      analysis_price=SAMPLE_PRICE)
        params.update(kw)
        return neutral_band(**params)

    def test_k_remains_the_existing_versioned_default(self):
        self.assertEqual(DEFAULT_VALIDATION_CONFIG.neutral_band_atr_multiple, 0.5)
        self.assertEqual(self._sample().k, 0.5)

    def test_atr_period_is_represented_structurally(self):
        self.assertEqual(DEFAULT_VALIDATION_CONFIG.atr_period_bars, 14)
        band = self._sample()
        self.assertEqual(band.atr_period_bars, 14)
        self.assertEqual(band.anchor_granularity_seconds, 300)

    def test_atr_reference_is_period_times_anchor_granularity(self):
        band = self._sample()
        self.assertEqual(band.atr_reference_seconds, 14 * 300)
        self.assertEqual(band.atr_reference_seconds, 4200.0)   # 70 minutes

    def test_no_hardcoded_seventy_minutes_anywhere(self):
        source = inspect.getsource(config_mod.neutral_band)
        for literal in ("4200", "70 * 60", "70*60"):
            self.assertNotIn(literal, source, literal)

    def test_the_tactical_band_is_sqrt_time_scaled(self):
        band = self._sample()
        expected_scale = math.sqrt(band.horizon_seconds / band.atr_reference_seconds)
        expected = (0.5 * SAMPLE_ATR * expected_scale) / SAMPLE_PRICE
        self.assertAlmostEqual(band.band_atr, expected, places=12)
        self.assertAlmostEqual(expected_scale, 16.97, places=1)

    def test_regression_the_audited_sample_is_no_longer_zero_point_one_eight(self):
        """The defect: an unscaled 70-minute range judging a 14-day move."""
        unscaled = 0.5 * SAMPLE_ATR / SAMPLE_PRICE
        self.assertAlmostEqual(unscaled * 100, 0.1802, places=3)
        band = self._sample()
        self.assertGreater(band.band_atr, 10 * unscaled)
        self.assertAlmostEqual(band.band_atr / unscaled, 16.97, places=1)

    def test_the_audited_sample_lands_in_the_principled_range(self):
        band = self._sample()
        self.assertAlmostEqual(band.band_atr * 100, 3.0578, places=3)

    def test_volatility_fallback_uses_the_same_sqrt_time_principle(self):
        band = self._sample()
        expected = 0.5 * SAMPLE_VOL * math.sqrt(band.horizon_seconds / 300)
        self.assertAlmostEqual(band.band_volatility, expected, places=12)
        self.assertAlmostEqual(band.band_volatility * 100, 3.8099, places=3)

    def test_the_two_modes_agree_once_scaled(self):
        """Independent evidence the scaling is principled, not a fudge."""
        band = self._sample()
        self.assertLess(band.band_volatility / band.band_atr, 1.5)
        self.assertGreater(band.band_volatility / band.band_atr, 1.0)

    def test_both_candidate_bands_are_emitted_for_audit(self):
        band = self._sample()
        self.assertIsNotNone(band.band_atr)
        self.assertIsNotNone(band.band_volatility)
        record = band.as_record()
        for key in ("band_atr", "band_volatility", "band", "band_mode", "k",
                    "atr_reference_seconds", "horizon_seconds",
                    "validation_config_version", "validation_config_hash"):
            self.assertIn(key, record)
        json.dumps(record)

    def test_selected_mode_follows_config(self):
        self.assertIs(self._sample().mode, BandMode.ATR)
        vol_cfg = ValidationConfig(neutral_band_mode=NEUTRAL_BAND_VOLATILITY_SCALE)
        self.assertIs(self._sample(config=vol_cfg).mode, BandMode.VOLATILITY_SCALE)

    def test_shorter_horizons_give_smaller_bands(self):
        execution = self._sample(horizon="execution")
        tactical = self._sample(horizon="tactical")
        structural = self._sample(horizon="structural")
        self.assertLess(execution.band_atr, tactical.band_atr)
        self.assertLess(tactical.band_atr, structural.band_atr)

    def test_the_band_is_a_versioned_research_default_not_a_production_value(self):
        record = self._sample().as_record()
        self.assertIn("RESEARCH DEFAULT", record["status"])
        self.assertIn("NOT CALIBRATED", record["status"])


# ===========================================================================
# 7. D-2C1 -- INVALID INPUTS   (tests 26-32)
# ===========================================================================
class TestNeutralBandInvalidInputs(unittest.TestCase):
    def _band(self, **kw):
        params = dict(horizon="tactical", anchor_granularity="5m",
                      atr=SAMPLE_ATR, volatility_scale=SAMPLE_VOL,
                      analysis_price=SAMPLE_PRICE)
        params.update(kw)
        return neutral_band(**params)

    def test_missing_atr_falls_back_to_volatility(self):
        band = self._band(atr=None)
        self.assertIs(band.mode, BandMode.VOLATILITY_SCALE)
        self.assertIsNone(band.band_atr)
        self.assertTrue(band.is_available)

    def test_invalid_atr_values_all_fall_back(self):
        for bad in (0.0, -1.0, float("nan"), float("inf"), "x", None):
            band = self._band(atr=bad)
            self.assertIs(band.mode, BandMode.VOLATILITY_SCALE, repr(bad))
            self.assertIsNone(band.band_atr, repr(bad))

    def test_missing_analysis_price_blocks_atr_but_not_volatility(self):
        band = self._band(analysis_price=None)
        self.assertIsNone(band.band_atr)
        self.assertIs(band.mode, BandMode.VOLATILITY_SCALE)
        self.assertTrue(band.is_available)

    def test_zero_price_never_divides(self):
        band = self._band(analysis_price=0.0)
        self.assertIsNone(band.band_atr)
        self.assertIs(band.mode, BandMode.VOLATILITY_SCALE)

    def test_invalid_volatility_falls_back_to_atr(self):
        for bad in (0.0, -0.01, float("nan"), float("inf"), None):
            band = self._band(volatility_scale=bad)
            self.assertIs(band.mode, BandMode.ATR, repr(bad))
            self.assertIsNone(band.band_volatility, repr(bad))

    def test_missing_both_sources_is_explicitly_unavailable(self):
        band = self._band(atr=None, volatility_scale=None)
        self.assertIs(band.mode, BandMode.UNAVAILABLE)
        self.assertIsNone(band.band)
        self.assertFalse(band.is_available)
        self.assertIs(band.reason, BandUnavailableReason.NO_USABLE_VOLATILITY)

    def test_atr_only_but_no_price_and_no_volatility_is_unavailable(self):
        band = self._band(volatility_scale=None, analysis_price=None)
        self.assertIs(band.mode, BandMode.UNAVAILABLE)
        self.assertIs(band.reason, BandUnavailableReason.NO_USABLE_VOLATILITY)

    def test_unknown_anchor_granularity_is_unavailable(self):
        band = self._band(anchor_granularity="1h")
        self.assertIs(band.mode, BandMode.UNAVAILABLE)
        self.assertIs(band.reason, BandUnavailableReason.UNKNOWN_ANCHOR_GRANULARITY)
        self.assertIsNone(band.band)

    def test_empty_anchor_granularity_is_unavailable(self):
        self.assertIs(self._band(anchor_granularity="").mode, BandMode.UNAVAILABLE)

    def test_unknown_horizon_is_unavailable_not_defaulted(self):
        band = self._band(horizon="nonsense")
        self.assertIs(band.mode, BandMode.UNAVAILABLE)
        self.assertIs(band.reason, BandUnavailableReason.UNKNOWN_HORIZON)
        self.assertIsNone(band.band)

    def test_no_fabricated_default_is_ever_returned(self):
        for band in (self._band(atr=None, volatility_scale=None),
                     self._band(horizon="nonsense"),
                     self._band(anchor_granularity="1h")):
            self.assertIsNone(band.band)
            self.assertIsNotNone(band.reason)
            self.assertFalse(band.is_available)

    def test_horizon_enum_and_string_agree(self):
        self.assertEqual(self._band(horizon=Horizon.TACTICAL).band,
                         self._band(horizon="tactical").band)

    def test_no_future_volatility_source_is_reachable(self):
        """Every input is a point-in-time scalar; no path, no clock, no store."""
        signature = inspect.signature(config_mod.neutral_band)
        self.assertEqual(
            set(signature.parameters),
            {"horizon", "anchor_granularity", "atr", "volatility_scale",
             "analysis_price", "config"},
        )
        names = _identifiers(config_mod.neutral_band)
        for forbidden in ("now", "utcnow", "MarketBar", "path_bars",
                          "terminal_bar", "forward_bars", "coverage",
                          "estimate_cadence", "canonicalize_bars"):
            self.assertNotIn(forbidden, names, forbidden)


# ===========================================================================
# 8. DETERMINISM / PROVENANCE   (tests 33, 34)
# ===========================================================================
class TestDeterminism(unittest.TestCase):
    def test_config_hash_is_stable(self):
        self.assertEqual(ValidationConfig().config_hash,
                         ValidationConfig().config_hash)

    def test_adding_atr_period_changed_the_hash_deliberately(self):
        self.assertIn("atr_period_bars", DEFAULT_VALIDATION_CONFIG.chosen)
        self.assertNotEqual(
            ValidationConfig().config_hash,
            ValidationConfig(atr_period_bars=20).config_hash,
        )

    def test_same_inputs_give_a_byte_equivalent_band_record(self):
        args = dict(horizon="tactical", anchor_granularity="5m", atr=SAMPLE_ATR,
                    volatility_scale=SAMPLE_VOL, analysis_price=SAMPLE_PRICE)
        a = json.dumps(neutral_band(**args).as_record(), sort_keys=True)
        b = json.dumps(neutral_band(**args).as_record(), sort_keys=True)
        self.assertEqual(a, b)

    def test_band_carries_config_provenance(self):
        band = neutral_band(horizon="tactical", anchor_granularity="5m",
                            atr=SAMPLE_ATR, volatility_scale=SAMPLE_VOL,
                            analysis_price=SAMPLE_PRICE)
        self.assertEqual(band.config_version, DEFAULT_VALIDATION_CONFIG.version)
        self.assertEqual(band.config_hash, DEFAULT_VALIDATION_CONFIG.config_hash)

    def test_canonicalization_is_repeatable(self):
        bars = [_bar(d) for d in (3, 1, 2)] + [_bar(2)]
        a = canonicalize_bars(bars)
        b = canonicalize_bars(bars)
        self.assertEqual([x.observation_id for x in a.bars],
                         [x.observation_id for x in b.bars])
        self.assertEqual(a.duplicates_collapsed, b.duplicates_collapsed)

    def test_no_wall_clock_read_in_the_new_helpers(self):
        for fn in (bars_mod.canonicalize_bars, bars_mod.bars_from_rows,
                   bars_mod.canonical_sort_key, config_mod.neutral_band):
            names = _identifiers(fn)
            for forbidden in ("now", "utcnow", "time", "random", "randint",
                              "shuffle", "monotonic"):
                self.assertNotIn(forbidden, names, f"{fn.__name__}:{forbidden}")


# ===========================================================================
# 9. REASON VOCABULARY   (tests 35-38)
# ===========================================================================
class TestReasonVocabulary(unittest.TestCase):
    def test_new_reason_codes_exist(self):
        self.assertEqual(ExclusionReason.BAR_CONTENT_CONFLICT.value,
                         "bar_content_conflict")
        self.assertEqual(ExclusionReason.MALFORMED_BAR_SKIPPED.value,
                         "malformed_bar_skipped")
        self.assertEqual(ExclusionReason.CLOCK_SKEW.value, "clock_skew")

    def test_existing_reason_members_are_unchanged(self):
        expected = {
            "ANCHOR_MISSING": "anchor_missing",
            "SERIES_UNAVAILABLE": "series_unavailable",
            "GRANULARITY_MISMATCH": "granularity_mismatch",
            "INVERSION_MISMATCH": "inversion_mismatch",
            "BAD_TIMESTAMP": "bad_timestamp",
            "NO_BARS_AFTER_MATURITY": "no_bars_after_maturity",
            "COVERAGE_GAP": "coverage_gap",
            "WINDOW_OPEN": "window_open",
            "ANCHOR_PRICE_UNUSABLE": "anchor_price_unusable",
            "UNKNOWN_HORIZON": "unknown_horizon",
            "SERIES_SUBSTITUTION_DISALLOWED": "series_substitution_disallowed",
            "RECONSTRUCTED_ANCHOR_DISALLOWED": "reconstructed_anchor_disallowed",
        }
        for name, value in expected.items():
            self.assertEqual(getattr(ExclusionReason, name).value, value, name)

    def test_exactly_three_members_were_added(self):
        self.assertEqual(len(list(ExclusionReason)), 15)

    def test_no_reason_code_is_a_correctness_verdict(self):
        for reason in ExclusionReason:
            self.assertNotIn("correct", reason.value)
            self.assertNotIn("failed", reason.value)


# ===========================================================================
# 10. PRODUCTION SAFETY   (tests 39-53)
# ===========================================================================
NEW_SURFACE = (bars_mod, config_mod, outcome_mod)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _referenced_names(module) -> set[str]:
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


class TestProductionSafety(unittest.TestCase):
    def _unchanged(self, path):
        result = subprocess.run(
            ["git", "diff", "--exit-code", "--", path],
            cwd=ROOT, capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, f"{path} changed:\n{result.stdout[:800]}")

    def test_production_core_is_byte_for_byte_unchanged(self):
        self._unchanged("apex/production_core.py")

    def test_protected_b2_files_are_unchanged(self):
        for path in (
            "apex/b2_bridge.py",
            "apex/b2_validation_bridge.py",
            "apex/b2/validation/anchor.py",
            "apex/b2/validation/maturity.py",
            "apex/b2/validation/series.py",
            "apex/b2/validation/__init__.py",
            "apex/b2/shadow.py",
            "apex/b2/evaluate.py",
            "apex/b2/registry.py",
            "apex/b2/aggregation.py",
            "apex/b2/horizons.py",
        ):
            self._unchanged(path)

    def test_only_authorized_validation_modules_exist(self):
        """The validation surface may only grow by approved stage.

        D-2C0/C1 predate the resolver, and this guard originally asserted its
        absence. D-2C2 authorized ``resolve.py``, so the expected set advanced;
        D-2C3 authorized exactly one further module, ``invalidation.py``, for
        setup invalidation and execution quality, D-2C4 authorized exactly
        one more, ``envelope.py``, for deterministic validation
        identity/provenance, and D-2C5 authorized exactly one more,
        ``readiness.py``, for lineage verification and per-observation
        readiness. The check itself is unchanged and still fails on any
        UNapproved module. ``metrics.py`` stays forbidden -- aggregation
        belongs to a later stage.
        """
        present = {f for f in os.listdir(os.path.join(ROOT, "apex", "b2", "validation"))
                   if f.endswith(".py")}
        self.assertEqual(present, {"__init__.py", "anchor.py", "bars.py",
                                   "config.py", "maturity.py", "outcome.py",
                                   "resolve.py", "series.py", "invalidation.py",
                                   "envelope.py", "readiness.py"})
        self.assertNotIn("metrics.py", present)

    def test_the_modified_modules_perform_no_io(self):
        for module in NEW_SURFACE:
            tree = ast.parse(inspect.getsource(module))
            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    names = [a.name for a in node.names] + [
                        getattr(node, "module", "") or ""
                    ]
                    for name in names:
                        for forbidden in ("requests", "streamlit", "threading",
                                          "production_core", "socket", "urllib",
                                          "asyncio", "subprocess", "sqlite",
                                          "psycopg", "multiprocessing"):
                            self.assertNotIn(forbidden, name,
                                             f"{module.__name__}:{name}")

    def test_no_ai_telegram_scheduler_or_daemon(self):
        for module in NEW_SURFACE:
            names = {n.lower() for n in _referenced_names(module)}
            for forbidden in ("telegram", "sendmessage", "openai", "anthropic",
                              "gemini", "groq", "completions", "thread", "timer",
                              "sleep", "crontab", "scheduler", "daemon"):
                self.assertFalse(any(forbidden in n for n in names),
                                 f"{module.__name__} references {forbidden}")

    def test_no_ddl_dml_or_persistence(self):
        for module in NEW_SURFACE:
            upper = inspect.getsource(module).upper()
            for verb in ("CREATE TABLE", "ALTER TABLE", "DROP TABLE", "TRUNCATE",
                         "INSERT INTO", "DELETE FROM", "CREATE INDEX"):
                self.assertNotIn(verb, upper, f"{module.__name__}:{verb}")
            names = _referenced_names(module)
            for forbidden in ("_save_persistent_state", "_load_persistent_state",
                              "_PERSISTENCE_LOCK", "insert_rows", "query_bars"):
                self.assertNotIn(forbidden, names, f"{module.__name__}:{forbidden}")

    def test_cross_asset_remains_withheld(self):
        with open(os.path.join(ROOT, "apex", "b2", "shadow.py"), encoding="utf-8") as h:
            self.assertIn('CROSS_ASSET_STATUS = "withheld"', h.read())

    def test_no_module_computes_a_rate_or_calibration(self):
        for module in NEW_SURFACE:
            names = {n.lower() for n in _referenced_names(module)}
            for forbidden in ("hit_rate", "accuracy", "win_rate", "calibrate",
                              "significance", "p_value", "wilson"):
                self.assertFalse(any(forbidden in n for n in names),
                                 f"{module.__name__} references {forbidden}")

    def test_production_signal_thresholds_are_unchanged(self):
        self.assertEqual(core.bias_from_score(0.40)[0], "🚀 Strong Bullish")
        self.assertEqual(core.bias_from_score(-0.40)[0], "🔻 Strong Bearish")
        self.assertEqual(core._broad_regime("🚀 Strong Bullish"), "Bullish")

    def test_horizon_windows_still_come_from_the_architecture(self):
        for horizon, window in HORIZON_EVALUATION_WINDOW.items():
            self.assertEqual(DEFAULT_VALIDATION_CONFIG.window_for(horizon), window)


if __name__ == "__main__":
    unittest.main()
