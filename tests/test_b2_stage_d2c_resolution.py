"""Stage D-2C2: direction and excursion/path resolution.

Covers the directional claim, terminal return, MFE/MAE, ATR normalisation and
path completeness for ONE observation. Deliberately does NOT exercise setup
invalidation, thesis invalidation, execution quality (D-2C3) or identity hashes,
context copy and overlap metadata (D-2C4) -- a test asserts those axes are still
absent rather than quietly stubbed.

Imports ``apex.production_core`` for the safety assertions, so durable-state
isolation is installed first. Nothing here performs I/O; a companion test blocks
outbound sockets to prove it.
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

from apex import b2_bridge
from apex.b2.enums import Direction
from apex.b2.validation import resolve as resolve_mod
from apex.b2.validation.bars import GRANULARITY_1D, MarketBar, analysis_ohlc
from apex.b2.validation.config import (
    DEFAULT_VALIDATION_CONFIG,
    NEUTRAL_BAND_VOLATILITY_SCALE,
    BandMode,
    ValidationConfig,
)
from apex.b2.validation.outcome import (
    DataResolution,
    DirectionOutcome,
    EligibilityPool,
    ExclusionReason,
)
from apex.b2.validation.resolve import (
    DirectionPathResolution,
    claim_direction,
    resolve_direction_and_path,
)
from apex.b2.validation.series import SeriesBindingQuality

UTC = timezone.utc
EVAL_AT = datetime(2026, 8, 30, 22, 4, 43, 893828, tzinfo=UTC)
NOW = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)
ANCHOR_PRICE = 3330.0
ANCHOR_ATR = 12.0
ANCHOR_VOL = 0.0012
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _anchor(symbol="XAUUSD=X", *, invert=False, atr=ANCHOR_ATR,
            price=ANCHOR_PRICE, vol=ANCHOR_VOL, status="anchor_captured",
            requested=None):
    return {
        "analysis_price": price, "last_price": price, "symbol": symbol,
        "symbol_requested": requested or symbol, "symbol_fallback_used": False,
        "invert": invert, "market_ts": 1, "market_ts_iso": "",
        "volatility_scale": vol, "atr": atr, "atr_ratio": 1.05,
        "volatility_regime": "normal", "price_source": "yahoo_5m_tactical",
        "granularity": "5m", "anchor_status": status,
    }


def _record(direction="bullish", *, instrument="Gold", anchor=None,
            horizon="tactical", evaluated_at=EVAL_AT, storage_id="s1",
            record_id="r1", claim=True):
    payload = {
        "schema_version": 2, "record_id": record_id, "instrument": instrument,
        "horizon": horizon,
        "evaluated_at": evaluated_at if isinstance(evaluated_at, str)
        else evaluated_at.isoformat(),
        "market_anchor": _anchor() if anchor is None else anchor,
        "claim": ({"direction": direction, "horizon": horizon} if claim else None),
    }
    return {"storage_id": storage_id, "record_id": record_id,
            "instrument": instrument, "horizon": horizon, "record": payload}


def _bar(day, close, *, high=None, low=None, symbol="XAUUSD=X",
         instrument="Gold", invert=False, month=9, granularity=GRANULARITY_1D):
    high = close * 1.001 if high is None else high
    low = close * 0.999 if low is None else low
    return MarketBar(
        symbol=symbol, instrument=instrument, granularity=granularity,
        bar_time=datetime(2026, month, day, tzinfo=UTC),
        open=close, high=max(high, close), low=min(low, close), close=close,
        volume=None, invert=invert,
    )


def _flat_path(days=(1, 2, 3), price=ANCHOR_PRICE, **kw):
    """Bars that neither rise nor fall: a clean control path."""
    return [_bar(d, price, high=price, low=price, **kw) for d in days]


def _capture_tail(bars):
    """Bars AFTER the window, mirroring the series already supplied.

    Real capture keeps running long past a 14-day window, so ``assess_maturity``
    sees ``latest_captured_bar >= window_end`` and knows we actually looked.
    Without a tail every fixture would report MATURED_AWAITING_BARS -- correct
    behaviour for a capture backlog, but not what a matured observation looks
    like in production. These bars are outside the horizon and the close-bounded
    path rule excludes them from the evidence, which the poison-pill tests rely
    on.
    """
    if not bars:
        return []
    like = bars[-1]
    return [
        MarketBar(symbol=like.symbol, instrument=like.instrument,
                  granularity=like.granularity,
                  bar_time=datetime(2026, 9, day, tzinfo=UTC),
                  open=like.close, high=like.close, low=like.close,
                  close=like.close, volume=None, invert=like.invert)
        for day in (20, 25)
    ]


def _resolve(record=None, bars=None, *, now=NOW, instrument="Gold",
             config=None, convention=True, tail=True):
    supplied = bars if bars is not None else _flat_path()
    if tail:
        supplied = list(supplied) + _capture_tail(supplied)
    return resolve_direction_and_path(
        record=record if record is not None else _record(),
        bars=supplied,
        now=now,
        convention=b2_bridge.symbol_convention(instrument) if convention else None,
        config=config,
    )


def _band_fraction():
    """The selected band for the standard sample, from the D-2C1 helper."""
    return _resolve().band.band


# ===========================================================================
# A. DETERMINISM   (1-7)
# ===========================================================================
class TestDeterminism(unittest.TestCase):
    def test_same_inputs_same_result(self):
        bars = [_bar(d, ANCHOR_PRICE * (1 + 0.004 * d)) for d in range(1, 9)]
        a = _resolve(bars=bars).as_record()
        b = _resolve(bars=bars).as_record()
        self.assertEqual(json.dumps(a, sort_keys=True), json.dumps(b, sort_keys=True))

    def test_shuffled_bars_give_an_identical_result(self):
        bars = [_bar(d, ANCHOR_PRICE * (1 + 0.004 * d)) for d in range(1, 9)]
        straight = _resolve(bars=bars).as_record()
        reversed_ = _resolve(bars=list(reversed(bars))).as_record()
        scrambled = _resolve(bars=[bars[4], bars[0], bars[7], bars[2],
                                   bars[6], bars[1], bars[5], bars[3]]).as_record()
        self.assertEqual(json.dumps(straight, sort_keys=True),
                         json.dumps(reversed_, sort_keys=True))
        self.assertEqual(json.dumps(straight, sort_keys=True),
                         json.dumps(scrambled, sort_keys=True))

    def test_identical_duplicate_bars_give_an_identical_result(self):
        bars = [_bar(d, ANCHOR_PRICE * (1 + 0.004 * d)) for d in range(1, 6)]
        plain = _resolve(bars=bars)
        duped = _resolve(bars=bars + [bars[2], bars[4], bars[4]])
        self.assertEqual(plain.excursion.as_record(), duped.excursion.as_record())
        self.assertEqual(plain.excursion.path_bars, duped.excursion.path_bars)
        self.assertEqual(duped.canonicalization.duplicates_collapsed, 3)

    def test_same_time_bars_use_canonical_observation_id_ordering(self):
        """Two SERIES at one instant, both bound-eligible for their own symbol."""
        from apex.b2.validation.bars import canonicalize_bars
        a = _bar(3, 100.0, symbol="AAA=X", instrument="EUR")
        b = _bar(3, 200.0, symbol="BBB=X", instrument="EUR")
        forward = [x.observation_id for x in canonicalize_bars([a, b]).bars]
        backward = [x.observation_id for x in canonicalize_bars([b, a]).bars]
        self.assertEqual(forward, backward)
        self.assertEqual(forward, sorted(forward))

    def test_no_mutation_of_the_source_record(self):
        record = _record()
        before = json.dumps(record, sort_keys=True)
        _resolve(record=record)
        self.assertEqual(json.dumps(record, sort_keys=True), before)

    def test_no_mutation_of_the_bars(self):
        bars = [_bar(d, ANCHOR_PRICE * (1 + 0.004 * d)) for d in range(1, 6)]
        before = json.dumps([b.to_row() for b in bars], sort_keys=True)
        order_before = [b.observation_id for b in bars]
        _resolve(bars=bars)
        self.assertEqual(json.dumps([b.to_row() for b in bars], sort_keys=True), before)
        self.assertEqual([b.observation_id for b in bars], order_before)

    def test_no_mutation_of_the_config(self):
        config = ValidationConfig()
        before = json.dumps(config.as_provenance(), sort_keys=True)
        _resolve(config=config)
        self.assertEqual(json.dumps(config.as_provenance(), sort_keys=True), before)
        self.assertEqual(config.config_hash, ValidationConfig().config_hash)


# ===========================================================================
# B. NO LOOKAHEAD   (8-14)
# ===========================================================================
class TestNoLookahead(unittest.TestCase):
    def test_bar_exactly_at_evaluated_at_is_excluded(self):
        at = MarketBar(
            symbol="XAUUSD=X", instrument="Gold", granularity=GRANULARITY_1D,
            bar_time=EVAL_AT, open=ANCHOR_PRICE, high=ANCHOR_PRICE,
            low=ANCHOR_PRICE, close=ANCHOR_PRICE, volume=None, invert=False,
        )
        result = _resolve(bars=[at] + _flat_path())
        self.assertEqual(result.excursion.path_bars, 3)

    def test_bar_closing_after_window_end_is_excluded(self):
        """Window ends 13 Sep 22:04; the 13 Sep bar closes 14 Sep 00:00."""
        result = _resolve(bars=_flat_path() + [_bar(13, ANCHOR_PRICE * 2)])
        self.assertEqual(result.excursion.path_bars, 3)
        self.assertLess(result.excursion.terminal_return or 0.0, 0.01)

    def test_bar_closing_exactly_at_window_end_is_included(self):
        evaluated = datetime(2026, 9, 1, tzinfo=UTC)
        record = _record(evaluated_at=evaluated, horizon="execution")
        inside = _bar(3, ANCHOR_PRICE, high=ANCHOR_PRICE, low=ANCHOR_PRICE)
        result = _resolve(record=record, bars=[inside],
                          now=datetime(2026, 9, 20, tzinfo=UTC))
        self.assertEqual(result.excursion.path_bars, 1)

    def test_poison_pill_future_bar_cannot_change_terminal_return(self):
        base = _flat_path()
        poisoned = base + [_bar(25, ANCHOR_PRICE * 5), _bar(30, ANCHOR_PRICE * 0.1)]
        self.assertEqual(_resolve(bars=base).excursion.terminal_return,
                         _resolve(bars=poisoned).excursion.terminal_return)

    def test_poison_pill_future_bar_cannot_change_mfe(self):
        base = _flat_path()
        poisoned = base + [_bar(25, ANCHOR_PRICE, high=ANCHOR_PRICE * 9,
                                low=ANCHOR_PRICE)]
        self.assertEqual(_resolve(bars=base).excursion.mfe,
                         _resolve(bars=poisoned).excursion.mfe)

    def test_poison_pill_future_bar_cannot_change_mae(self):
        base = _flat_path()
        poisoned = base + [_bar(25, ANCHOR_PRICE, high=ANCHOR_PRICE,
                                low=ANCHOR_PRICE * 0.1)]
        self.assertEqual(_resolve(bars=base).excursion.mae,
                         _resolve(bars=poisoned).excursion.mae)

    def test_captured_at_is_not_a_field_and_is_never_referenced(self):
        self.assertNotIn("captured_at", MarketBar.__dataclass_fields__)
        self.assertNotIn("captured_at", _identifiers(resolve_mod))

    def test_the_resolver_reads_no_wall_clock(self):
        names = _identifiers(resolve_mod)
        for forbidden in ("now_utc", "utcnow", "monotonic", "random", "randint"):
            self.assertNotIn(forbidden, names, forbidden)
        signature = inspect.signature(resolve_direction_and_path)
        self.assertIn("now", signature.parameters)
        self.assertIs(signature.parameters["now"].default,
                      inspect.Parameter.empty, "now must be injected, not defaulted")


# ===========================================================================
# C/D/E. DIRECTION MAPPING   (15-28)
# ===========================================================================
class TestDirectionMapping(unittest.TestCase):
    def _at_return(self, fraction, direction="bullish"):
        """A COMPLETE 12-bar path ending at the requested terminal return."""
        price = ANCHOR_PRICE * (1.0 + fraction)
        bars = _flat_path(days=tuple(range(1, 12)))
        bars.append(_bar(12, price, high=price, low=price))
        return _resolve(record=_record(direction), bars=bars)

    # -- bullish --------------------------------------------------------
    def test_bullish_confirmed(self):
        result = self._at_return(_band_fraction() + 0.02)
        self.assertIs(result.direction, DirectionOutcome.CONFIRMED)
        self.assertIs(result.data_resolution, DataResolution.RESOLVED)

    def test_bullish_failed(self):
        result = self._at_return(-(_band_fraction() + 0.02))
        self.assertIs(result.direction, DirectionOutcome.FAILED)

    def test_bullish_neutral(self):
        self.assertIs(self._at_return(_band_fraction() / 2).direction,
                      DirectionOutcome.NEUTRAL_WITHIN_BAND)

    def test_bullish_exact_positive_band_boundary_is_neutral(self):
        """`> +band` is required for a verdict, so exactly +band is neutral.

        Asserted against the mapping directly: building a price whose float
        division lands EXACTLY on the band is not reliably possible, and a test
        that drifted by one ulp would be testing arithmetic rather than the
        contract.
        """
        band = _band_fraction()
        self.assertIs(
            resolve_mod._direction_outcome(
                claim=Direction.BULLISH, terminal_return=band, band=band),
            DirectionOutcome.NEUTRAL_WITHIN_BAND,
        )
        self.assertIs(
            resolve_mod._direction_outcome(
                claim=Direction.BULLISH, terminal_return=band * 1.000001,
                band=band),
            DirectionOutcome.CONFIRMED,
        )

    def test_bullish_exact_negative_band_boundary_is_neutral(self):
        result = self._at_return(-_band_fraction())
        self.assertAlmostEqual(result.excursion.terminal_return,
                               -result.band.band, places=12)
        self.assertIs(result.direction, DirectionOutcome.NEUTRAL_WITHIN_BAND)

    # -- bearish --------------------------------------------------------
    def test_bearish_confirmed(self):
        result = self._at_return(-(_band_fraction() + 0.02), "bearish")
        self.assertIs(result.direction, DirectionOutcome.CONFIRMED)

    def test_bearish_failed(self):
        result = self._at_return(_band_fraction() + 0.02, "bearish")
        self.assertIs(result.direction, DirectionOutcome.FAILED)

    def test_bearish_neutral(self):
        self.assertIs(self._at_return(-_band_fraction() / 2, "bearish").direction,
                      DirectionOutcome.NEUTRAL_WITHIN_BAND)

    def test_bearish_exact_positive_band_boundary_is_neutral(self):
        band = _band_fraction()
        self.assertIs(
            resolve_mod._direction_outcome(
                claim=Direction.BEARISH, terminal_return=band, band=band),
            DirectionOutcome.NEUTRAL_WITHIN_BAND,
        )
        self.assertIs(
            resolve_mod._direction_outcome(
                claim=Direction.BEARISH, terminal_return=band * 1.000001,
                band=band),
            DirectionOutcome.FAILED,
        )

    def test_exact_negative_band_boundary_is_neutral_for_both_claims(self):
        band = _band_fraction()
        for claim in (Direction.BULLISH, Direction.BEARISH):
            self.assertIs(
                resolve_mod._direction_outcome(
                    claim=claim, terminal_return=-band, band=band),
                DirectionOutcome.NEUTRAL_WITHIN_BAND, claim.value,
            )

    def test_bearish_exact_negative_band_boundary_is_neutral(self):
        self.assertIs(self._at_return(-_band_fraction(), "bearish").direction,
                      DirectionOutcome.NEUTRAL_WITHIN_BAND)

    # -- abstention -----------------------------------------------------
    def test_flat_claim_is_abstained(self):
        result = self._at_return(0.20, "flat")
        self.assertIs(result.claim_direction, Direction.FLAT)
        self.assertIs(result.direction, DirectionOutcome.ABSTAINED)
        self.assertFalse(result.direction.is_verdict)

    def test_unavailable_claim_is_not_applicable(self):
        result = self._at_return(0.20, "unavailable")
        self.assertIs(result.direction, DirectionOutcome.NOT_APPLICABLE)

    def test_flat_does_not_fabricate_excursions(self):
        result = self._at_return(0.20, "flat")
        self.assertIsNone(result.excursion.mfe)
        self.assertIsNone(result.excursion.mae)
        self.assertIsNone(result.excursion.mfe_atr)
        self.assertIsNone(result.excursion.bars_to_mfe)
        # Raw path metadata still present -- the bars existed.
        self.assertGreater(result.excursion.path_bars, 0)
        self.assertIsNotNone(result.excursion.terminal_return)

    def test_unavailable_does_not_fabricate_excursions(self):
        result = self._at_return(0.20, "unavailable")
        self.assertIsNone(result.excursion.mfe)
        self.assertIsNone(result.excursion.mae)

    def test_a_record_without_a_claim_is_not_applicable(self):
        result = _resolve(record=_record(claim=False))
        self.assertIs(result.claim_direction, Direction.UNAVAILABLE)
        self.assertIs(result.direction, DirectionOutcome.NOT_APPLICABLE)

    def test_only_the_primary_claim_is_used(self):
        record = _record("bullish")
        record["record"]["decision"] = {"direction": "bearish",
                                        "macro_direction": "bearish",
                                        "technical_direction": "bearish"}
        self.assertIs(claim_direction(record), Direction.BULLISH)
        self.assertIs(self._at_return(_band_fraction() + 0.02).direction,
                      DirectionOutcome.CONFIRMED)


# ===========================================================================
# F. INVERTED FX   (29-38)
# ===========================================================================
class TestInvertedFX(unittest.TestCase):
    PAIRS = (("CAD", "USDCAD=X"), ("CHF", "USDCHF=X"), ("JPY", "USDJPY=X"))

    def _fx(self, instrument, symbol, quotes, direction="bullish"):
        quote_anchor = 100.0
        anchor = _anchor(symbol, invert=True, price=1.0 / quote_anchor,
                         atr=0.0001, vol=ANCHOR_VOL)
        record = _record(direction, instrument=instrument, anchor=anchor)
        bars = [
            _bar(i + 1, q, high=q * 1.01, low=q * 0.99, symbol=symbol,
                 instrument=instrument, invert=True)
            for i, q in enumerate(quotes)
        ]
        return resolve_direction_and_path(
            record=record, bars=bars + _capture_tail(bars), now=NOW,
            convention=b2_bridge.symbol_convention(instrument),
        )

    def test_cad_chf_jpy_conventions_are_inverted(self):
        for instrument, _ in self.PAIRS:
            self.assertTrue(b2_bridge.symbol_convention(instrument).invert,
                            instrument)

    def test_quote_down_means_strength_up_for_all_three(self):
        for instrument, symbol in self.PAIRS:
            result = self._fx(instrument, symbol, [100.0, 95.0, 90.0])
            self.assertGreater(result.excursion.terminal_return, 0.0, instrument)
            self.assertIs(result.direction, DirectionOutcome.CONFIRMED, instrument)

    def test_quote_up_means_strength_down_for_all_three(self):
        for instrument, symbol in self.PAIRS:
            result = self._fx(instrument, symbol, [100.0, 105.0, 115.0])
            self.assertLess(result.excursion.terminal_return, 0.0, instrument)
            self.assertIs(result.direction, DirectionOutcome.FAILED, instrument)

    def test_inverted_high_derives_from_quote_low(self):
        bar = _bar(1, 100.0, high=110.0, low=90.0, symbol="USDJPY=X",
                   instrument="JPY", invert=True)
        self.assertAlmostEqual(bar.analysis_high, 1.0 / 90.0, places=12)

    def test_inverted_low_derives_from_quote_high(self):
        bar = _bar(1, 100.0, high=110.0, low=90.0, symbol="USDJPY=X",
                   instrument="JPY", invert=True)
        self.assertAlmostEqual(bar.analysis_low, 1.0 / 110.0, places=12)

    def test_analysis_ohlc_parity_with_production(self):
        import pandas as pd
        raw = {"open": [100.0], "high": [110.0], "low": [90.0], "close": [105.0]}
        for instrument in b2_bridge.default_shadow_instruments():
            convention = b2_bridge.symbol_convention(instrument)
            frame = core._tactical_analysis_ohlc(pd.DataFrame(raw),
                                                 bool(convention.invert))
            ours = analysis_ohlc(100.0, 110.0, 90.0, 105.0, convention.invert)
            for index, column in enumerate(("open", "high", "low", "close")):
                self.assertAlmostEqual(ours[index], float(frame[column].iloc[0]),
                                       places=12, msg=f"{instrument}.{column}")

    def test_bullish_inverted_mfe_comes_from_the_quote_low(self):
        result = self._fx("JPY", "USDJPY=X", [100.0])
        expected = (1.0 / 99.0) / (1.0 / 100.0) - 1.0
        self.assertAlmostEqual(result.excursion.mfe, expected, places=12)

    def test_bullish_inverted_mae_comes_from_the_quote_high(self):
        result = self._fx("JPY", "USDJPY=X", [100.0])
        expected = 1.0 - (1.0 / 101.0) / (1.0 / 100.0)
        self.assertAlmostEqual(result.excursion.mae, expected, places=12)

    def test_bearish_inverted_mfe_and_mae_swap(self):
        bullish = self._fx("CHF", "USDCHF=X", [100.0], "bullish")
        bearish = self._fx("CHF", "USDCHF=X", [100.0], "bearish")
        self.assertAlmostEqual(bullish.excursion.mfe, bearish.excursion.mae,
                               places=12)
        self.assertAlmostEqual(bullish.excursion.mae, bearish.excursion.mfe,
                               places=12)

    def test_inverted_excursions_stay_non_negative(self):
        for instrument, symbol in self.PAIRS:
            for direction in ("bullish", "bearish"):
                result = self._fx(instrument, symbol, [100.0, 90.0, 110.0],
                                  direction)
                self.assertGreaterEqual(result.excursion.mfe, 0.0)
                self.assertGreaterEqual(result.excursion.mae, 0.0)


# ===========================================================================
# G. EXCURSION   (39-48)
# ===========================================================================
class TestExcursion(unittest.TestCase):
    def test_terminal_return_uses_the_last_canonical_bar(self):
        bars = [_bar(1, ANCHOR_PRICE * 1.10, high=ANCHOR_PRICE * 1.10,
                     low=ANCHOR_PRICE * 1.10),
                _bar(2, ANCHOR_PRICE * 1.05, high=ANCHOR_PRICE * 1.05,
                     low=ANCHOR_PRICE * 1.05)]
        result = _resolve(bars=bars)
        self.assertAlmostEqual(result.excursion.terminal_return, 0.05, places=12)
        self.assertEqual(result.terminal_bar_time[:10], "2026-09-02")

    def test_bullish_mfe_and_mae_are_correct(self):
        bars = [_bar(1, ANCHOR_PRICE, high=ANCHOR_PRICE * 1.08,
                     low=ANCHOR_PRICE * 0.97)]
        result = _resolve(bars=bars)
        self.assertAlmostEqual(result.excursion.mfe, 0.08, places=12)
        self.assertAlmostEqual(result.excursion.mae, 0.03, places=12)

    def test_bearish_mfe_and_mae_are_mirrored(self):
        bars = [_bar(1, ANCHOR_PRICE, high=ANCHOR_PRICE * 1.08,
                     low=ANCHOR_PRICE * 0.97)]
        result = _resolve(record=_record("bearish"), bars=bars)
        self.assertAlmostEqual(result.excursion.mfe, 0.03, places=12)
        self.assertAlmostEqual(result.excursion.mae, 0.08, places=12)

    def test_excursions_are_never_negative(self):
        bars = [_bar(1, ANCHOR_PRICE * 1.05, high=ANCHOR_PRICE * 1.06,
                     low=ANCHOR_PRICE * 1.04)]
        result = _resolve(bars=bars)
        self.assertGreaterEqual(result.excursion.mfe, 0.0)
        self.assertGreaterEqual(result.excursion.mae, 0.0)

    def test_first_occurrence_of_a_repeated_maximum_is_used(self):
        high = ANCHOR_PRICE * 1.05
        bars = [
            _bar(1, ANCHOR_PRICE, high=ANCHOR_PRICE, low=ANCHOR_PRICE),
            _bar(2, ANCHOR_PRICE, high=high, low=ANCHOR_PRICE),
            _bar(3, ANCHOR_PRICE, high=high, low=ANCHOR_PRICE),
            _bar(4, ANCHOR_PRICE, high=high, low=ANCHOR_PRICE),
        ]
        self.assertEqual(_resolve(bars=bars).excursion.bars_to_mfe, 1)

    def test_bars_to_indices_are_zero_based_into_the_canonical_path(self):
        bars = [
            _bar(1, ANCHOR_PRICE, high=ANCHOR_PRICE, low=ANCHOR_PRICE * 0.90),
            _bar(2, ANCHOR_PRICE, high=ANCHOR_PRICE, low=ANCHOR_PRICE),
            _bar(3, ANCHOR_PRICE, high=ANCHOR_PRICE * 1.20, low=ANCHOR_PRICE),
        ]
        result = _resolve(bars=bars)
        self.assertEqual(result.excursion.bars_to_mae, 0)
        self.assertEqual(result.excursion.bars_to_mfe, 2)
        self.assertEqual(result.excursion.path_bars, 3)
        self.assertLess(result.excursion.bars_to_mfe, result.excursion.path_bars)

    def test_indices_are_stable_under_shuffling(self):
        bars = [
            _bar(1, ANCHOR_PRICE, high=ANCHOR_PRICE, low=ANCHOR_PRICE * 0.90),
            _bar(2, ANCHOR_PRICE, high=ANCHOR_PRICE * 1.20, low=ANCHOR_PRICE),
        ]
        self.assertEqual(_resolve(bars=bars).excursion.as_record(),
                         _resolve(bars=list(reversed(bars))).excursion.as_record())

    def test_a_flat_path_yields_zero_excursions(self):
        result = _resolve(bars=_flat_path())
        self.assertAlmostEqual(result.excursion.mfe, 0.0, places=12)
        self.assertAlmostEqual(result.excursion.mae, 0.0, places=12)
        self.assertAlmostEqual(result.excursion.terminal_return, 0.0, places=12)


# ===========================================================================
# H. ATR NORMALISATION   (49-57)
# ===========================================================================
class TestAtrNormalisation(unittest.TestCase):
    def _with_atr(self, atr):
        bars = [_bar(1, ANCHOR_PRICE, high=ANCHOR_PRICE * 1.08,
                     low=ANCHOR_PRICE * 0.97)]
        return _resolve(record=_record(anchor=_anchor(atr=atr)), bars=bars)

    def test_valid_atr_normalises_mfe_and_mae(self):
        result = self._with_atr(ANCHOR_ATR)
        self.assertAlmostEqual(result.excursion.mfe_atr,
                               (0.08 * ANCHOR_PRICE) / ANCHOR_ATR, places=9)
        self.assertAlmostEqual(result.excursion.mae_atr,
                               (0.03 * ANCHOR_PRICE) / ANCHOR_ATR, places=9)

    def test_normalisation_uses_price_distance_not_the_raw_fraction(self):
        """The fraction alone would be ~0.0067 ATR, not ~22."""
        result = self._with_atr(ANCHOR_ATR)
        self.assertGreater(result.excursion.mfe_atr, 1.0)
        self.assertNotAlmostEqual(result.excursion.mfe_atr,
                                  0.08 / ANCHOR_ATR, places=6)

    def test_invalid_atr_leaves_normalisation_unavailable(self):
        for bad in (None, 0.0, -5.0, float("nan"), float("inf")):
            result = self._with_atr(bad)
            self.assertIsNone(result.excursion.mfe_atr, repr(bad))
            self.assertIsNone(result.excursion.mae_atr, repr(bad))

    def test_raw_excursions_survive_an_invalid_atr(self):
        for bad in (None, 0.0, -5.0, float("nan"), float("inf")):
            result = self._with_atr(bad)
            self.assertAlmostEqual(result.excursion.mfe, 0.08, places=12, msg=repr(bad))
            self.assertAlmostEqual(result.excursion.mae, 0.03, places=12, msg=repr(bad))

    def test_an_invalid_atr_falls_back_to_the_volatility_band(self):
        result = self._with_atr(None)
        self.assertIs(result.band.mode, BandMode.VOLATILITY_SCALE)
        self.assertTrue(result.band.is_available)

    def test_no_future_volatility_is_used(self):
        """ATR comes from the anchor, never from the bars."""
        result = self._with_atr(ANCHOR_ATR)
        self.assertEqual(result.band.atr, ANCHOR_ATR)


# ===========================================================================
# I. MATURITY   (58-63)
# ===========================================================================
class TestMaturity(unittest.TestCase):
    OPEN_NOW = EVAL_AT + timedelta(days=1)

    def test_not_matured_is_unresolved(self):
        result = _resolve(now=self.OPEN_NOW)
        self.assertIs(result.data_resolution, DataResolution.NOT_MATURED)
        self.assertIs(result.direction, DirectionOutcome.UNRESOLVED)
        self.assertIn(ExclusionReason.WINDOW_OPEN, result.reasons)

    def test_not_matured_is_never_a_verdict_or_neutral(self):
        big_move = [_bar(1, ANCHOR_PRICE * 1.50, high=ANCHOR_PRICE * 1.50,
                         low=ANCHOR_PRICE * 1.50)]
        for direction in ("bullish", "bearish", "flat"):
            result = _resolve(record=_record(direction), bars=big_move,
                              now=self.OPEN_NOW)
            self.assertNotIn(result.direction,
                             (DirectionOutcome.CONFIRMED, DirectionOutcome.FAILED,
                              DirectionOutcome.NEUTRAL_WITHIN_BAND), direction)
            self.assertIs(result.direction, DirectionOutcome.UNRESOLVED, direction)

    def test_not_matured_computes_no_excursion(self):
        result = _resolve(now=self.OPEN_NOW)
        self.assertIsNone(result.excursion.terminal_return)
        self.assertIsNone(result.excursion.mfe)
        self.assertEqual(result.excursion.path_bars, 0)

    def test_now_only_gates_maturity_and_never_the_mathematics(self):
        bars = [_bar(d, ANCHOR_PRICE * (1 + 0.004 * d)) for d in range(1, 9)]
        early = _resolve(bars=bars, now=NOW)
        late = _resolve(bars=bars, now=NOW + timedelta(days=400))
        self.assertEqual(early.excursion.as_record(), late.excursion.as_record())
        self.assertIs(early.direction, late.direction)

    def test_maturity_comes_from_the_existing_module(self):
        names = _identifiers(resolve_mod)
        self.assertIn("assess_maturity", names)
        self.assertIn("MaturityState", names)


# ===========================================================================
# J. MISSING DATA   (64-77)
# ===========================================================================
class TestMissingData(unittest.TestCase):
    def test_matured_with_no_bars_is_insufficient_data(self):
        result = _resolve(bars=[])
        self.assertIn(result.data_resolution,
                      (DataResolution.INSUFFICIENT_DATA, DataResolution.UNAVAILABLE))
        self.assertIs(result.direction, DirectionOutcome.UNRESOLVED)

    def test_no_bars_is_never_failed(self):
        for direction in ("bullish", "bearish"):
            self.assertIsNot(_resolve(record=_record(direction), bars=[]).direction,
                             DirectionOutcome.FAILED)

    def test_partial_coverage_is_stamped_and_flagged(self):
        sparse = [_bar(1, ANCHOR_PRICE, high=ANCHOR_PRICE, low=ANCHOR_PRICE)]
        result = _resolve(bars=sparse)
        self.assertEqual(result.coverage_status, "unresolved_coverage_gap")
        self.assertFalse(result.path_complete)
        self.assertTrue(result.excursion_is_lower_bound)
        self.assertIn(ExclusionReason.COVERAGE_GAP, result.reasons)

    def test_partial_is_never_silently_promoted_to_resolved(self):
        sparse = [_bar(1, ANCHOR_PRICE, high=ANCHOR_PRICE, low=ANCHOR_PRICE)]
        self.assertIsNot(_resolve(bars=sparse).data_resolution,
                         DataResolution.RESOLVED)

    def test_complete_coverage_is_resolved_and_not_lower_bound(self):
        full = [_bar(d, ANCHOR_PRICE, high=ANCHOR_PRICE, low=ANCHOR_PRICE)
                for d in range(1, 13)]
        result = _resolve(bars=full)
        self.assertIs(result.data_resolution, DataResolution.RESOLVED)
        self.assertTrue(result.path_complete)
        self.assertFalse(result.excursion_is_lower_bound)

    def test_bar_content_conflict_yields_no_verdict(self):
        a = _bar(3, ANCHOR_PRICE, high=ANCHOR_PRICE, low=ANCHOR_PRICE)
        b = _bar(3, ANCHOR_PRICE * 2, high=ANCHOR_PRICE * 2, low=ANCHOR_PRICE * 2)
        self.assertEqual(a.observation_id, b.observation_id)
        self.assertNotEqual(a.content_hash, b.content_hash)
        result = _resolve(bars=[a, b])
        self.assertIs(result.data_resolution, DataResolution.UNAVAILABLE)
        self.assertIs(result.direction, DirectionOutcome.UNRESOLVED)
        self.assertIn(ExclusionReason.BAR_CONTENT_CONFLICT, result.reasons)

    def test_conflict_never_selects_one_version(self):
        a = _bar(3, ANCHOR_PRICE, high=ANCHOR_PRICE, low=ANCHOR_PRICE)
        b = _bar(3, ANCHOR_PRICE * 2, high=ANCHOR_PRICE * 2, low=ANCHOR_PRICE * 2)
        forward, backward = _resolve(bars=[a, b]), _resolve(bars=[b, a])
        self.assertIsNone(forward.excursion.terminal_return)
        self.assertEqual(forward.as_record(), backward.as_record())

    def test_missing_anchor_is_excluded(self):
        record = _record()
        record["record"]["market_anchor"] = None
        flat = _flat_path()
        result = resolve_direction_and_path(record=record,
                                            bars=flat + _capture_tail(flat),
                                            now=NOW, convention=None)
        self.assertIs(result.data_resolution, DataResolution.UNAVAILABLE)
        self.assertIn(ExclusionReason.ANCHOR_MISSING, result.reasons)
        self.assertIs(result.eligibility_pool, EligibilityPool.EXCLUDED)

    def test_bad_anchor_price_is_excluded(self):
        for bad in (0.0, -1.0):
            anchor = _anchor(price=bad)
            anchor["last_price"] = bad
            result = _resolve(record=_record(anchor=anchor))
            self.assertIs(result.direction, DirectionOutcome.UNRESOLVED, repr(bad))
            self.assertIn(ExclusionReason.ANCHOR_PRICE_UNUSABLE, result.reasons)

    def test_series_unavailable_is_excluded(self):
        other = [_bar(1, 1.10, symbol="EURUSD=X", instrument="EUR")]
        result = _resolve(bars=other)
        self.assertIs(result.data_resolution, DataResolution.UNAVAILABLE)
        self.assertIn(ExclusionReason.SERIES_UNAVAILABLE, result.reasons)

    def test_inversion_mismatch_is_excluded(self):
        anchor = _anchor("USDJPY=X", invert=True, price=0.01, atr=0.0001)
        record = _record("bullish", instrument="JPY", anchor=anchor)
        bars = [_bar(1, 100.0, symbol="USDJPY=X", instrument="JPY", invert=False)]
        result = resolve_direction_and_path(
            record=record, bars=bars + _capture_tail(bars), now=NOW,
            convention=b2_bridge.symbol_convention("JPY"))
        self.assertIn(ExclusionReason.INVERSION_MISMATCH, result.reasons)
        self.assertIs(result.direction, DirectionOutcome.UNRESOLVED)
        self.assertIs(result.eligibility_pool, EligibilityPool.EXCLUDED)

    def test_granularity_mismatch_is_reported_distinctly(self):
        five_minute = [_bar(1, ANCHOR_PRICE, granularity="5m")]
        result = _resolve(bars=five_minute)
        self.assertIn(ExclusionReason.GRANULARITY_MISMATCH, result.reasons)

    def test_unknown_horizon_is_excluded(self):
        result = _resolve(record=_record(horizon="nonsense"))
        self.assertIs(result.data_resolution, DataResolution.UNAVAILABLE)
        self.assertIn(ExclusionReason.UNKNOWN_HORIZON, result.reasons)

    def test_bad_timestamp_is_excluded(self):
        result = _resolve(record=_record(evaluated_at="not-a-timestamp"))
        self.assertIn(ExclusionReason.BAD_TIMESTAMP, result.reasons)
        self.assertIs(result.direction, DirectionOutcome.UNRESOLVED)

    def test_clock_skew_is_unresolved_never_failed(self):
        result = _resolve(now=EVAL_AT - timedelta(days=5))
        self.assertIn(ExclusionReason.CLOCK_SKEW, result.reasons)
        self.assertIs(result.direction, DirectionOutcome.UNRESOLVED)
        self.assertIs(result.data_resolution, DataResolution.NOT_MATURED)

    def test_no_missing_data_path_ever_returns_failed(self):
        cases = [
            dict(bars=[]),
            dict(now=EVAL_AT + timedelta(days=1)),
            dict(now=EVAL_AT - timedelta(days=5)),
            dict(record=_record(horizon="nonsense")),
            dict(record=_record(evaluated_at="bad")),
            dict(bars=[_bar(1, 1.10, symbol="EURUSD=X", instrument="EUR")]),
        ]
        for case in cases:
            self.assertIsNot(_resolve(**case).direction, DirectionOutcome.FAILED,
                             str(case)[:80])


# ===========================================================================
# K. PROVENANCE   (78-83)
# ===========================================================================
class TestProvenance(unittest.TestCase):
    def test_exact_captured_series_is_captured_eligible(self):
        result = _resolve()
        self.assertIs(result.binding.quality, SeriesBindingQuality.SERIES_EXACT)
        self.assertIs(result.eligibility_pool, EligibilityPool.CAPTURED)
        self.assertTrue(result.anchor.status.is_point_in_time)

    def test_substituted_gold_is_research_only(self):
        gc_bars = [_bar(d, ANCHOR_PRICE, high=ANCHOR_PRICE, low=ANCHOR_PRICE,
                        symbol="GC=F") for d in range(1, 4)]
        result = _resolve(bars=gc_bars)
        self.assertIs(result.binding.quality, SeriesBindingQuality.SERIES_SUBSTITUTED)
        self.assertEqual(result.binding.bound_symbol, "GC=F")
        self.assertIs(result.eligibility_pool, EligibilityPool.RECONSTRUCTED_RESEARCH)
        self.assertFalse(result.eligibility_pool.permits_calibration)

    def test_reconstructed_anchor_is_research_only(self):
        record = _record()
        record["record"]["market_anchor"] = None
        record["record"]["schema_version"] = 1
        result = _resolve(record=record)
        self.assertFalse(result.anchor.status.is_point_in_time)
        self.assertIsNot(result.eligibility_pool, EligibilityPool.CAPTURED)

    def test_partial_coverage_does_not_erase_provenance(self):
        sparse = [_bar(1, ANCHOR_PRICE, high=ANCHOR_PRICE, low=ANCHOR_PRICE)]
        result = _resolve(bars=sparse)
        self.assertIs(result.binding.quality, SeriesBindingQuality.SERIES_EXACT)
        self.assertIs(result.eligibility_pool, EligibilityPool.CAPTURED)
        self.assertFalse(result.path_complete)

    def test_gold_substitution_is_never_captured_eligible(self):
        gc_bars = [_bar(d, ANCHOR_PRICE, high=ANCHOR_PRICE, low=ANCHOR_PRICE,
                        symbol="GC=F") for d in range(1, 13)]
        self.assertIsNot(_resolve(bars=gc_bars).eligibility_pool,
                         EligibilityPool.CAPTURED)


# ===========================================================================
# L. BAND REGRESSION   (84-88)
# ===========================================================================
class TestBandIntegration(unittest.TestCase):
    def test_the_resolver_uses_the_d2c1_helper(self):
        self.assertIn("neutral_band", _identifiers(resolve_mod))
        source = inspect.getsource(resolve_mod)
        self.assertNotIn("sqrt", source, "band formula must not be duplicated here")

    def test_the_audited_tactical_sample_matches_the_approved_value(self):
        band = _resolve().band
        self.assertIs(band.mode, BandMode.ATR)
        self.assertAlmostEqual(band.band_atr * 100, 3.0578, places=3)
        self.assertAlmostEqual(band.band_volatility * 100, 3.8099, places=3)

    def test_the_old_unscaled_defect_is_not_reproduced(self):
        band = _resolve().band
        unscaled = 0.5 * ANCHOR_ATR / ANCHOR_PRICE
        self.assertAlmostEqual(unscaled * 100, 0.1802, places=3)
        self.assertGreater(band.band, 10 * unscaled)

    def test_volatility_fallback_serves_when_atr_is_absent(self):
        result = _resolve(record=_record(anchor=_anchor(atr=None)))
        self.assertIs(result.band.mode, BandMode.VOLATILITY_SCALE)
        self.assertIsNotNone(result.band.band)

    def test_the_selected_band_is_emitted_in_the_record(self):
        record = _resolve().as_record()
        for key in ("band", "band_atr", "band_volatility", "band_mode", "k"):
            self.assertIn(key, record)
        json.dumps(record)

    def test_an_unavailable_band_leaves_direction_unresolved(self):
        anchor = _anchor(atr=None, vol=None)
        bars = [_bar(d, ANCHOR_PRICE * 1.50, high=ANCHOR_PRICE * 1.50,
                     low=ANCHOR_PRICE * 1.50) for d in range(1, 13)]
        result = _resolve(record=_record(anchor=anchor), bars=bars)
        self.assertFalse(result.band.is_available)
        self.assertIs(result.direction, DirectionOutcome.UNRESOLVED)
        self.assertIsNotNone(result.excursion.terminal_return)


# ===========================================================================
# M. SCOPE + PRODUCTION SAFETY   (89-100)
# ===========================================================================
def _identifiers(obj) -> set[str]:
    """Identifiers the CODE touches. AST, not text: these modules name in prose
    the very things they must never use."""
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


class TestScopeBoundary(unittest.TestCase):
    def test_d2c3_axes_are_not_implemented(self):
        fields = DirectionPathResolution.__dataclass_fields__
        for absent in ("setup_invalidation", "thesis_invalidation",
                       "execution", "execution_outcome"):
            self.assertNotIn(absent, fields, absent)
        names = _identifiers(resolve_mod)
        for absent in ("SetupInvalidation", "ThesisInvalidation",
                       "ExecutionOutcome", "OutcomeAxes"):
            self.assertNotIn(absent, names, absent)

    def test_d2c4_identity_is_not_implemented(self):
        fields = DirectionPathResolution.__dataclass_fields__
        for absent in ("validation_id", "input_hash", "outcome_hash", "context",
                       "overlap_block_key"):
            self.assertNotIn(absent, fields, absent)
        names = _identifiers(resolve_mod)
        for absent in ("sha256", "hashlib"):
            self.assertNotIn(absent, names, absent)

    def test_the_record_declares_what_it_did_not_resolve(self):
        declared = _resolve().as_record()["not_resolved_in_this_stage"]
        for axis in ("setup_invalidation", "thesis_invalidation",
                     "execution_outcome", "validation_id", "input_hash",
                     "outcome_hash", "context", "overlap_metadata"):
            self.assertIn(axis, declared, axis)

    def test_no_metrics_module_exists(self):
        """Metrics/aggregation stays forbidden; D-2C3 added ``invalidation.py``,
        D-2C4 added ``envelope.py``, D-2C5 added ``readiness.py``."""
        present = {f for f in os.listdir(os.path.join(ROOT, "apex", "b2", "validation"))
                   if f.endswith(".py")}
        self.assertNotIn("metrics.py", present)
        self.assertEqual(present, {"__init__.py", "anchor.py", "bars.py",
                                   "config.py", "maturity.py", "outcome.py",
                                   "resolve.py", "series.py", "invalidation.py",
                                   "envelope.py", "readiness.py"})


class TestProductionSafety(unittest.TestCase):
    def _unchanged(self, path):
        result = subprocess.run(["git", "diff", "--exit-code", "--", path],
                                cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0,
                         f"{path} changed:\n{result.stdout[:800]}")

    def test_production_core_is_byte_for_byte_unchanged(self):
        self._unchanged("apex/production_core.py")

    def test_every_protected_file_is_unchanged(self):
        for path in ("apex/b2_bridge.py", "apex/b2_validation_bridge.py",
                     "apex/b2/validation/anchor.py", "apex/b2/validation/bars.py",
                     "apex/b2/validation/config.py",
                     "apex/b2/validation/maturity.py",
                     "apex/b2/validation/outcome.py",
                     "apex/b2/validation/series.py",
                     "apex/b2/validation/__init__.py", "apex/b2/shadow.py",
                     "apex/b2/evaluate.py", "apex/b2/registry.py",
                     "apex/b2/aggregation.py", "apex/b2/horizons.py"):
            self._unchanged(path)

    def test_resolve_has_no_forbidden_imports(self):
        tree = ast.parse(inspect.getsource(resolve_mod))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [a.name for a in node.names] + [
                    getattr(node, "module", "") or ""]
                for name in names:
                    for forbidden in ("requests", "urllib", "socket", "http",
                                      "streamlit", "threading", "asyncio",
                                      "subprocess", "multiprocessing", "sqlite",
                                      "psycopg", "supabase", "production_core",
                                      "b2_bridge", "b2_validation_bridge",
                                      "random", "os"):
                        self.assertNotIn(forbidden, name, name)

    def test_resolve_makes_no_ai_telegram_or_scheduler_reference(self):
        names = {n.lower() for n in _identifiers(resolve_mod)}
        for forbidden in ("telegram", "sendmessage", "openai", "anthropic",
                          "gemini", "groq", "completions", "thread", "timer",
                          "sleep", "crontab", "scheduler", "daemon"):
            self.assertFalse(any(forbidden in n for n in names),
                             f"resolve.py references {forbidden}")
        # File I/O is checked as a CALL, not a substring: WINDOW_OPEN would
        # otherwise match "open" and fire on a reason code.
        tree = ast.parse(inspect.getsource(resolve_mod))
        called = {
            node.func.id for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        for builtin in ("open", "exec", "eval", "compile", "__import__", "input"):
            self.assertNotIn(builtin, called, builtin)

    def test_resolve_performs_no_ddl_dml_or_persistence(self):
        upper = inspect.getsource(resolve_mod).upper()
        for verb in ("CREATE TABLE", "ALTER TABLE", "DROP TABLE", "TRUNCATE",
                     "INSERT INTO", "DELETE FROM"):
            self.assertNotIn(verb, upper, verb)
        names = _identifiers(resolve_mod)
        for forbidden in ("_save_persistent_state", "_load_persistent_state",
                          "_PERSISTENCE_LOCK", "insert_rows", "query_bars",
                          "capture_daily_bars"):
            self.assertNotIn(forbidden, names, forbidden)

    def test_no_production_module_imports_resolve(self):
        """Nothing except resolve.py itself may import validation.resolve --
        D-2C4's ``envelope.py`` and D-2C5's ``readiness.py`` are the ONLY
        approved exceptions (D-2C4 Decision 4: nominal imports of
        resolve.py/invalidation.py are how the envelope binds their
        concrete result shapes together; D-2C5 needs resolve.py's public
        ``claim_direction`` for its lineage check). Any OTHER importer
        still fails this guard."""
        importers = []
        for folder, _dirs, files in os.walk(ROOT):
            if any(p in folder for p in ("_backup_", "_baseline_", ".git",
                                         "__pycache__", "tests")):
                continue
            for filename in files:
                if not filename.endswith(".py") or filename == "resolve.py":
                    continue
                path = os.path.join(folder, filename)
                with open(path, encoding="utf-8") as handle:
                    try:
                        tree = ast.parse(handle.read())
                    except SyntaxError:
                        continue
                for node in ast.walk(tree):
                    if isinstance(node, ast.ImportFrom) and "validation.resolve" in (
                            node.module or ""):
                        importers.append(filename)
                    if isinstance(node, ast.Import) and any(
                            "validation.resolve" in a.name for a in node.names):
                        importers.append(filename)
        self.assertEqual(sorted(set(importers)), ["envelope.py", "readiness.py"])

    def test_cross_asset_remains_withheld(self):
        with open(os.path.join(ROOT, "apex", "b2", "shadow.py"), encoding="utf-8") as h:
            self.assertIn('CROSS_ASSET_STATUS = "withheld"', h.read())

    def test_no_rate_or_calibration_is_computed(self):
        names = {n.lower() for n in _identifiers(resolve_mod)}
        for forbidden in ("hit_rate", "accuracy", "win_rate", "calibrate",
                          "significance", "p_value", "wilson", "benchmark"):
            self.assertFalse(any(forbidden in n for n in names), forbidden)

    def test_production_signal_thresholds_are_unchanged(self):
        self.assertEqual(core.bias_from_score(0.40)[0], "🚀 Strong Bullish")
        self.assertEqual(core._broad_regime("🚀 Strong Bullish"), "Bullish")


if __name__ == "__main__":
    unittest.main()
