"""Stage D-2A + D-2B: validation config, outcome vocabulary, maturity, series binding.

Scope is deliberately narrow. D-2C outcome COMPUTATION is not implemented and is
not exercised here: these tests cover the types, the invariants, the maturity
model, the series binding that fixes the live Gold defect, and the bar-window
rules that fix the weekend coverage defect.

The two tests that matter most come first, because each pins a defect found in
the live system rather than a hypothetical:

    TestGoldSeriesBinding  -- a Gold anchor recorded XAUUSD=X while the daily
                              capture stored GC=F, so exact-equality joining
                              found zero bars and reported a capture failure.
    TestWeekendCoverage    -- a window ending on a Sunday was reported as a
                              coverage gap despite complete data, removing
                              roughly one day in seven of evidence on a
                              calendar-correlated basis.

Imports ``apex.production_core``, so durable-state isolation is installed first.
No test reaches a network: nothing here performs I/O at all.
"""
from __future__ import annotations

import ast
import inspect
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
from apex.b2.enums import Horizon
from apex.b2.horizons import HORIZON_EVALUATION_WINDOW
from apex.b2.validation import bars as bars_mod
from apex.b2.validation import config as config_mod
from apex.b2.validation import maturity as maturity_mod
from apex.b2.validation import outcome as outcome_mod
from apex.b2.validation import series as series_mod
from apex.b2.validation.anchor import MarketAnchor, SymbolConvention
from apex.b2.validation.bars import (
    GRANULARITY_1D,
    CadenceBasis,
    MarketBar,
    analysis_ohlc,
    bar_close_time,
    coverage,
    estimate_cadence,
    forward_bars,
    path_bars,
    terminal_bar,
)
from apex.b2.validation.config import (
    DEFAULT_VALIDATION_CONFIG,
    VALIDATION_CONFIG_VERSION,
    ValidationConfig,
)
from apex.b2.validation.maturity import MaturityState, assess_maturity
from apex.b2.validation.outcome import (
    DataResolution,
    DirectionOutcome,
    EligibilityPool,
    ExclusionReason,
    ExcursionMeasures,
    ExecutionOutcome,
    OutcomeAxes,
    OutcomeInvariantError,
    SetupInvalidation,
    ThesisInvalidation,
    unresolved_axes,
)
from apex.b2.validation.series import (
    InversionAgreement,
    SeriesBinding,
    SeriesBindingQuality,
    bind_series,
    bound_bars,
    candidate_symbols,
)

NOW = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)
EVAL_AT = datetime(2026, 8, 30, 22, 4, 43, 893828, tzinfo=timezone.utc)
TACTICAL = HORIZON_EVALUATION_WINDOW[Horizon.TACTICAL]


def _bar(symbol, instrument, day, month=9, *, invert=False, hour=0,
         o=100.0, h=101.0, l=99.0, c=100.5, price_source=None):
    kwargs = {} if price_source is None else {"price_source": price_source}
    return MarketBar(
        symbol=symbol, instrument=instrument, granularity=GRANULARITY_1D,
        bar_time=datetime(2026, month, day, hour, tzinfo=timezone.utc),
        open=o, high=h, low=l, close=c, volume=None, invert=invert, **kwargs,
    )


def _anchor(symbol="XAUUSD=X", *, invert=False, requested=None, price=3330.0,
            granularity="5m", price_source="yahoo_5m_tactical"):
    return MarketAnchor(
        analysis_price=price, last_price=price, symbol=symbol,
        symbol_requested=requested if requested is not None else symbol,
        symbol_fallback_used=False, invert=invert, market_ts=1,
        market_ts_iso="2026-08-30T22:04:43+00:00", volatility_scale=0.0012,
        atr=12.0, atr_ratio=1.05, volatility_regime="normal",
        price_source=price_source, granularity=granularity,
    )


def _weekday_bars(start, end, symbol, instrument, *, invert=False):
    out, day = [], start
    while day <= end:
        if day.weekday() < 5:
            out.append(MarketBar(
                symbol=symbol, instrument=instrument, granularity=GRANULARITY_1D,
                bar_time=day, open=1.08, high=1.10, low=1.07, close=1.09,
                volume=None, invert=invert,
            ))
        day += timedelta(days=1)
    return out


# ===========================================================================
# 1. THE GOLD DEFECT -- the reason series.py exists
# ===========================================================================
class TestGoldSeriesBinding(unittest.TestCase):
    """A Gold anchor records XAUUSD=X; the daily capture stored GC=F."""

    def setUp(self):
        self.convention = b2_bridge.symbol_convention("Gold")
        self.gc_bars = [
            _bar("GC=F", "Gold", d, hour=23) for d in range(1, 14)
        ]

    def test_gold_xauusd_anchor_resolves_against_gcf_bars(self):
        binding = bind_series(
            anchor=_anchor("XAUUSD=X"), convention=self.convention,
            bars=self.gc_bars, granularity=GRANULARITY_1D,
        )
        self.assertTrue(binding.is_bound)
        self.assertEqual(binding.bound_symbol, "GC=F")
        self.assertEqual(len(bound_bars(binding, self.gc_bars)), 13)

    def test_it_is_stamped_series_substituted(self):
        binding = bind_series(
            anchor=_anchor("XAUUSD=X"), convention=self.convention,
            bars=self.gc_bars, granularity=GRANULARITY_1D,
        )
        self.assertIs(binding.quality, SeriesBindingQuality.SERIES_SUBSTITUTED)
        self.assertTrue(binding.cross_source)
        self.assertTrue(
            any(n.startswith("substituted:") for n in binding.notes), binding.notes
        )

    def test_a_substituted_series_never_enters_the_captured_pool(self):
        binding = bind_series(
            anchor=_anchor("XAUUSD=X"), convention=self.convention,
            bars=self.gc_bars, granularity=GRANULARITY_1D,
        )
        self.assertFalse(binding.permits_capture_pool)
        self.assertFalse(binding.quality.permits_capture_pool)

    def test_cross_granularity_alone_does_not_bar_the_captured_pool(self):
        """Otherwise the captured pool would be permanently empty: the anchor is
        always a 5-minute close and the bars are always daily."""
        exact = [_bar("EURUSD=X", "EUR", d) for d in range(1, 8)]
        binding = bind_series(
            anchor=_anchor("EURUSD=X"),
            convention=b2_bridge.symbol_convention("EUR"),
            bars=exact, granularity=GRANULARITY_1D,
        )
        self.assertTrue(binding.cross_granularity)
        self.assertFalse(binding.cross_source)
        self.assertTrue(binding.permits_capture_pool)

    def test_exact_gold_binding_when_the_anchor_symbol_has_bars(self):
        """Both symbols present -> the anchor's own series wins, and is exact."""
        both = self.gc_bars + [_bar("XAUUSD=X", "Gold", d) for d in range(1, 14)]
        binding = bind_series(
            anchor=_anchor("XAUUSD=X"), convention=self.convention,
            bars=both, granularity=GRANULARITY_1D,
        )
        self.assertIs(binding.quality, SeriesBindingQuality.SERIES_EXACT)
        self.assertEqual(binding.bound_symbol, "XAUUSD=X")
        self.assertFalse(binding.cross_source)
        self.assertTrue(binding.permits_capture_pool)
        # Same series, two sampling rates -- recorded, never disqualifying.
        self.assertTrue(binding.cross_granularity)

    def test_an_anchor_that_already_used_the_fallback_binds_exactly(self):
        binding = bind_series(
            anchor=_anchor("GC=F", requested="XAUUSD=X"),
            convention=self.convention, bars=self.gc_bars,
            granularity=GRANULARITY_1D,
        )
        self.assertIs(binding.quality, SeriesBindingQuality.SERIES_EXACT)
        self.assertEqual(binding.bound_symbol, "GC=F")

    def test_substitution_can_be_refused(self):
        binding = bind_series(
            anchor=_anchor("XAUUSD=X"), convention=self.convention,
            bars=self.gc_bars, granularity=GRANULARITY_1D,
            allow_substitution=False,
        )
        self.assertIs(binding.quality, SeriesBindingQuality.SERIES_UNAVAILABLE)
        self.assertFalse(binding.is_bound)
        self.assertEqual(bound_bars(binding, self.gc_bars), ())

    def test_no_candidate_with_bars_is_unavailable_not_a_failure(self):
        binding = bind_series(
            anchor=_anchor("XAUUSD=X"), convention=self.convention,
            bars=[_bar("EURUSD=X", "EUR", 3)], granularity=GRANULARITY_1D,
        )
        self.assertIs(binding.quality, SeriesBindingQuality.SERIES_UNAVAILABLE)
        self.assertIn("no_candidate_symbol_had_bars", binding.notes)
        self.assertEqual(binding.bar_count, 0)


# ===========================================================================
# 2. THE WEEKEND DEFECT -- the reason coverage() changed
# ===========================================================================
class TestWeekendCoverage(unittest.TestCase):
    def setUp(self):
        self.bars = _weekday_bars(
            datetime(2026, 9, 1, tzinfo=timezone.utc),
            datetime(2026, 10, 5, tzinfo=timezone.utc),
            "EURUSD=X", "EUR",
        )

    def test_a_sunday_ending_window_with_complete_data_is_not_a_gap(self):
        """The exact live regression: eval Sunday, window ends Sunday."""
        evaluated = datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc)
        self.assertEqual(evaluated.weekday(), 6, "fixture must be a Sunday")
        result = coverage(
            self.bars, evaluated_at=evaluated, window=TACTICAL, now=NOW
        )
        self.assertEqual(result["status"], "resolvable")

    def test_no_weekday_produces_a_false_gap(self):
        for day in range(1, 15):
            evaluated = datetime(2026, 9, day, 12, 0, tzinfo=timezone.utc)
            result = coverage(
                self.bars, evaluated_at=evaluated, window=TACTICAL, now=NOW
            )
            self.assertEqual(
                result["status"], "resolvable",
                f"{evaluated.date()} ({evaluated.strftime('%a')}) regressed",
            )

    def test_a_genuine_multi_day_outage_is_still_a_gap(self):
        """The fix must not make coverage permissive."""
        truncated = [b for b in self.bars if b.bar_time.day <= 10]
        evaluated = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
        result = coverage(
            truncated, evaluated_at=evaluated, window=TACTICAL, now=NOW
        )
        self.assertEqual(result["status"], "unresolved_coverage_gap")

    def test_the_hardcoded_one_day_slack_is_gone(self):
        source = inspect.getsource(bars_mod.coverage)
        self.assertNotIn("timedelta(days=1)", source)

    def test_coverage_reports_the_basis_it_used(self):
        evaluated = datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc)
        result = coverage(
            self.bars, evaluated_at=evaluated, window=TACTICAL, now=NOW
        )
        self.assertEqual(result["cadence_basis"], CadenceBasis.OBSERVED_MEDIAN.value)
        self.assertIsNotNone(result["tolerance_seconds"])
        self.assertIsNotNone(result["trailing_gap_seconds"])
        self.assertLessEqual(
            result["trailing_gap_seconds"], result["tolerance_seconds"]
        )

    def test_window_open_still_precedes_any_coverage_judgment(self):
        evaluated = datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc)
        result = coverage(
            self.bars, evaluated_at=evaluated, window=TACTICAL,
            now=evaluated + timedelta(days=2),
        )
        self.assertEqual(result["status"], "unresolved_window_open")

    def test_no_coverage_status_is_a_correctness_verdict(self):
        for status in ("unresolved_window_open", "unresolved_no_bars",
                       "unresolved_coverage_gap", "resolvable"):
            self.assertNotIn("fail", status)
            self.assertNotIn("correct", status)


# ===========================================================================
# 3. CADENCE
# ===========================================================================
class TestCadence(unittest.TestCase):
    def test_insufficient_history_is_unknown_not_guessed(self):
        few = [_bar("EURUSD=X", "EUR", d) for d in (1, 2)]
        estimate = estimate_cadence(few)
        self.assertIsNone(estimate.seconds)
        self.assertFalse(estimate.is_known)
        self.assertIs(estimate.basis, CadenceBasis.INSUFFICIENT_HISTORY)

    def test_a_single_bar_is_unknown(self):
        estimate = estimate_cadence([_bar("EURUSD=X", "EUR", 1)])
        self.assertIs(estimate.basis, CadenceBasis.INSUFFICIENT_HISTORY)
        self.assertIsNone(estimate.seconds)

    def test_no_bars_is_unknown_granularity(self):
        estimate = estimate_cadence([])
        self.assertIs(estimate.basis, CadenceBasis.UNKNOWN_GRANULARITY)
        self.assertIsNone(estimate.seconds)

    def test_a_daily_series_measures_one_day(self):
        daily = [_bar("EURUSD=X", "EUR", d) for d in range(1, 12)]
        estimate = estimate_cadence(daily)
        self.assertIs(estimate.basis, CadenceBasis.OBSERVED_MEDIAN)
        self.assertEqual(estimate.seconds, 86400.0)

    def test_the_median_ignores_weekend_outliers(self):
        """A mean would be inflated by Fri->Mon gaps; the median must not be."""
        weekdays = _weekday_bars(
            datetime(2026, 9, 1, tzinfo=timezone.utc),
            datetime(2026, 9, 30, tzinfo=timezone.utc), "EURUSD=X", "EUR",
        )
        self.assertEqual(estimate_cadence(weekdays).seconds, 86400.0)

    def test_duplicate_bar_times_do_not_create_zero_gaps(self):
        dupes = [_bar("EURUSD=X", "EUR", d) for d in range(1, 8)]
        dupes += [_bar("EURUSD=X", "EUR", 3)]
        self.assertEqual(estimate_cadence(dupes).seconds, 86400.0)

    def test_coverage_falls_back_to_the_granularity_bound_when_unknown(self):
        one = [_bar("EURUSD=X", "EUR", 1)]
        result = coverage(
            one, evaluated_at=datetime(2026, 8, 30, 22, 4, tzinfo=timezone.utc),
            window=TACTICAL, now=NOW,
        )
        self.assertEqual(result["cadence_basis"], CadenceBasis.INSUFFICIENT_HISTORY.value)
        self.assertEqual(result["status"], "unresolved_coverage_gap")


# ===========================================================================
# 4. TERMINAL / PATH BAR RULES
# ===========================================================================
class TestWindowRules(unittest.TestCase):
    def test_bar_close_time_is_open_plus_granularity(self):
        self.assertEqual(
            bar_close_time(datetime(2026, 9, 1, tzinfo=timezone.utc), GRANULARITY_1D),
            datetime(2026, 9, 2, tzinfo=timezone.utc),
        )
        self.assertIsNone(bar_close_time(datetime(2026, 9, 1), "1h"))

    def test_a_bar_closing_after_window_end_is_excluded_from_the_path(self):
        """The rule forward_bars lacks. Window ends 13 Sep 22:04."""
        crossing = _bar("EURUSD=X", "EUR", 13)          # closes 14 Sep 00:00
        selected = path_bars([crossing], evaluated_at=EVAL_AT, window=TACTICAL)
        self.assertEqual(selected, ())

    def test_forward_bars_still_includes_it_unchanged(self):
        """D-1 semantics preserved: forward_bars bounds by OPEN time."""
        crossing = _bar("EURUSD=X", "EUR", 13)
        self.assertEqual(
            len(forward_bars([crossing], evaluated_at=EVAL_AT, window=TACTICAL)), 1
        )

    def test_a_bar_closing_exactly_at_window_end_is_included(self):
        evaluated = datetime(2026, 9, 1, tzinfo=timezone.utc)
        window = timedelta(days=2)                       # ends 3 Sep 00:00
        inside = _bar("EURUSD=X", "EUR", 2)              # closes 3 Sep 00:00
        self.assertEqual(
            len(path_bars([inside], evaluated_at=evaluated, window=window)), 1
        )

    def test_a_bar_straddling_the_evaluation_moment_is_excluded(self):
        bars = [_bar("EURUSD=X", "EUR", 30, month=8), _bar("EURUSD=X", "EUR", 31, month=8)]
        selected = path_bars(bars, evaluated_at=EVAL_AT, window=TACTICAL)
        self.assertEqual([b.bar_time.day for b in selected], [31])

    def test_path_bars_are_time_ordered(self):
        unordered = [_bar("EURUSD=X", "EUR", d) for d in (5, 1, 3)]
        selected = path_bars(unordered, evaluated_at=EVAL_AT, window=TACTICAL)
        self.assertEqual([b.bar_time.day for b in selected], [1, 3, 5])

    def test_terminal_bar_is_the_last_wholly_contained_bar(self):
        bars = [_bar("EURUSD=X", "EUR", d) for d in range(1, 14)]
        last = terminal_bar(bars, evaluated_at=EVAL_AT, window=TACTICAL)
        self.assertIsNotNone(last)
        self.assertEqual(last.bar_time.day, 12)          # 13 Sep closes too late

    def test_terminal_bar_is_none_when_nothing_is_contained(self):
        self.assertIsNone(
            terminal_bar([], evaluated_at=EVAL_AT, window=TACTICAL)
        )

    def test_a_bar_of_unknown_granularity_is_excluded_not_assumed(self):
        class Odd(MarketBar):
            pass
        odd = _bar("EURUSD=X", "EUR", 3)
        object.__setattr__(odd, "granularity", "1h")
        self.assertEqual(
            path_bars([odd], evaluated_at=EVAL_AT, window=TACTICAL), ()
        )


# ===========================================================================
# 5. INVERSION
# ===========================================================================
class TestInversion(unittest.TestCase):
    def test_agreement_when_anchor_and_bars_match(self):
        binding = bind_series(
            anchor=_anchor("USDJPY=X", invert=True),
            convention=b2_bridge.symbol_convention("JPY"),
            bars=[_bar("USDJPY=X", "JPY", d, invert=True) for d in range(1, 8)],
            granularity=GRANULARITY_1D,
        )
        self.assertIs(binding.inversion, InversionAgreement.AGREED)
        self.assertTrue(binding.is_usable)

    def test_mismatch_is_flagged_and_never_reconciled(self):
        bars = [_bar("USDJPY=X", "JPY", d, invert=False) for d in range(1, 8)]
        binding = bind_series(
            anchor=_anchor("USDJPY=X", invert=True),
            convention=b2_bridge.symbol_convention("JPY"),
            bars=bars, granularity=GRANULARITY_1D,
        )
        self.assertIs(binding.inversion, InversionAgreement.MISMATCH)
        self.assertFalse(binding.is_usable)
        self.assertFalse(binding.permits_capture_pool)
        # The bars exist but must not be handed back for use.
        self.assertEqual(bound_bars(binding, bars), ())

    def test_bars_that_disagree_among_themselves_are_a_mismatch(self):
        bars = [_bar("USDJPY=X", "JPY", 1, invert=True),
                _bar("USDJPY=X", "JPY", 2, invert=False)]
        binding = bind_series(
            anchor=_anchor("USDJPY=X", invert=True),
            convention=b2_bridge.symbol_convention("JPY"),
            bars=bars, granularity=GRANULARITY_1D,
        )
        self.assertIs(binding.inversion, InversionAgreement.MISMATCH)
        self.assertIn("bars_disagree_on_invert", binding.notes)

    def test_no_anchor_yields_unknown_not_a_mismatch(self):
        bars = [_bar("EURUSD=X", "EUR", d) for d in range(1, 8)]
        binding = bind_series(
            anchor=None, convention=b2_bridge.symbol_convention("EUR"),
            bars=bars, granularity=GRANULARITY_1D,
        )
        self.assertIs(binding.inversion, InversionAgreement.UNKNOWN)
        self.assertTrue(binding.is_usable)

    def test_cad_chf_jpy_are_inverted_and_the_others_are_not(self):
        for instrument in ("CAD", "CHF", "JPY"):
            self.assertTrue(
                b2_bridge.symbol_convention(instrument).invert, instrument
            )
        for instrument in ("EUR", "GBP", "AUD", "NZD", "Gold", "Oil", "NDX", "USD"):
            self.assertFalse(
                b2_bridge.symbol_convention(instrument).invert, instrument
            )

    def test_inverted_strength_rises_as_the_quote_falls(self):
        for instrument, symbol in (("CAD", "USDCAD=X"), ("CHF", "USDCHF=X"),
                                   ("JPY", "USDJPY=X")):
            strong = _bar(symbol, instrument, 1, invert=True,
                          o=100.0, h=101.0, l=98.0, c=98.0)
            weak = _bar(symbol, instrument, 2, invert=True,
                        o=100.0, h=101.0, l=98.0, c=101.0)
            self.assertGreater(
                strong.analysis_close, weak.analysis_close, instrument
            )

    def test_production_parity_of_analysis_ohlc_is_preserved(self):
        import pandas as pd
        raw = {"open": [100.0], "high": [110.0], "low": [90.0], "close": [105.0]}
        for instrument in b2_bridge.default_shadow_instruments():
            convention = b2_bridge.symbol_convention(instrument)
            frame = core._tactical_analysis_ohlc(
                pd.DataFrame(raw), bool(convention.invert)
            )
            ours = analysis_ohlc(100.0, 110.0, 90.0, 105.0, convention.invert)
            for index, column in enumerate(("open", "high", "low", "close")):
                self.assertAlmostEqual(
                    ours[index], float(frame[column].iloc[0]), places=12,
                    msg=f"{instrument}.{column}",
                )


# ===========================================================================
# 6. CANDIDATE ORDERING
# ===========================================================================
class TestCandidateSymbols(unittest.TestCase):
    def test_ordered_anchor_first_then_convention_then_fallbacks(self):
        self.assertEqual(
            candidate_symbols(
                _anchor("A", requested="B"),
                SymbolConvention("X", "C", False, ("D", "E")),
            ),
            ("A", "B", "C", "D", "E"),
        )

    def test_duplicates_are_removed_keeping_first_priority(self):
        self.assertEqual(
            candidate_symbols(
                _anchor("GC=F", requested="XAUUSD=X"),
                b2_bridge.symbol_convention("Gold"),
            ),
            ("GC=F", "XAUUSD=X"),
        )

    def test_empty_entries_are_dropped(self):
        self.assertEqual(
            candidate_symbols(None, SymbolConvention("X", "C", False, ("", "D"))),
            ("C", "D"),
        )

    def test_no_anchor_and_no_convention_yields_nothing(self):
        self.assertEqual(candidate_symbols(None, None), ())

    def test_granularity_mismatch_finds_no_bars(self):
        five_minute = MarketBar(
            symbol="EURUSD=X", instrument="EUR", granularity="5m",
            bar_time=datetime(2026, 9, 1, tzinfo=timezone.utc),
            open=1.0, high=1.1, low=0.9, close=1.05, volume=None, invert=False,
        )
        binding = bind_series(
            anchor=_anchor("EURUSD=X"),
            convention=b2_bridge.symbol_convention("EUR"),
            bars=[five_minute], granularity=GRANULARITY_1D,
        )
        self.assertIs(binding.quality, SeriesBindingQuality.SERIES_UNAVAILABLE)


# ===========================================================================
# 7. MATURITY
# ===========================================================================
class TestMaturity(unittest.TestCase):
    def test_an_open_window_is_not_matured(self):
        assessment = assess_maturity(
            evaluated_at=EVAL_AT, window=TACTICAL,
            now=EVAL_AT + timedelta(days=1),
        )
        self.assertIs(assessment.state, MaturityState.NOT_MATURED)
        self.assertFalse(assessment.permits_verdict)
        self.assertIs(
            assessment.state.to_data_resolution(), DataResolution.NOT_MATURED
        )

    def test_one_day_into_a_fourteen_day_window_is_not_a_failure(self):
        assessment = assess_maturity(
            evaluated_at=EVAL_AT, window=TACTICAL,
            now=EVAL_AT + timedelta(days=1),
        )
        self.assertAlmostEqual(assessment.elapsed_fraction, 1 / 14, places=4)
        self.assertFalse(assessment.permits_verdict)

    def test_a_covered_elapsed_window_is_matured(self):
        assessment = assess_maturity(
            evaluated_at=EVAL_AT, window=TACTICAL, now=NOW,
            coverage_status="resolvable",
            latest_captured_bar=datetime(2026, 10, 1, tzinfo=timezone.utc),
        )
        self.assertIs(assessment.state, MaturityState.MATURED)
        self.assertTrue(assessment.permits_verdict)

    def test_elapsed_but_uncaptured_is_awaiting_bars_not_partial(self):
        """Operational lag must not masquerade as missing market data."""
        assessment = assess_maturity(
            evaluated_at=EVAL_AT, window=TACTICAL, now=NOW,
            coverage_status="unresolved_no_bars",
            latest_captured_bar=datetime(2026, 8, 28, tzinfo=timezone.utc),
        )
        self.assertIs(assessment.state, MaturityState.MATURED_AWAITING_BARS)
        self.assertFalse(assessment.permits_verdict)
        self.assertIs(
            assessment.state.to_data_resolution(), DataResolution.INSUFFICIENT_DATA
        )

    def test_a_real_gap_after_capture_is_partial(self):
        assessment = assess_maturity(
            evaluated_at=EVAL_AT, window=TACTICAL, now=NOW,
            coverage_status="unresolved_coverage_gap",
            latest_captured_bar=datetime(2026, 10, 1, tzinfo=timezone.utc),
        )
        self.assertIs(assessment.state, MaturityState.MATURED_PARTIAL)
        self.assertTrue(assessment.permits_verdict)
        self.assertIs(
            assessment.state.to_data_resolution(), DataResolution.PARTIAL
        )

    def test_clock_skew_is_not_matured_and_never_negative(self):
        assessment = assess_maturity(
            evaluated_at=NOW + timedelta(days=5), window=TACTICAL, now=NOW,
        )
        self.assertIs(assessment.state, MaturityState.NOT_MATURED)
        self.assertGreaterEqual(assessment.elapsed_fraction, 0.0)

    def test_maturity_is_deterministic_for_a_fixed_now(self):
        kwargs = dict(evaluated_at=EVAL_AT, window=TACTICAL, now=NOW,
                      coverage_status="resolvable",
                      latest_captured_bar=datetime(2026, 10, 1, tzinfo=timezone.utc))
        self.assertEqual(
            assess_maturity(**kwargs).as_record(),
            assess_maturity(**kwargs).as_record(),
        )

    def test_the_boundary_moment_is_matured(self):
        assessment = assess_maturity(
            evaluated_at=EVAL_AT, window=TACTICAL, now=EVAL_AT + TACTICAL,
            coverage_status="resolvable",
            latest_captured_bar=EVAL_AT + TACTICAL,
        )
        self.assertIs(assessment.state, MaturityState.MATURED)


# ===========================================================================
# 8. OUTCOME INVARIANTS
# ===========================================================================
def _axes(**overrides):
    base = dict(
        data_resolution=DataResolution.RESOLVED,
        direction=DirectionOutcome.CONFIRMED,
        setup_invalidation=SetupInvalidation.NOT_INVALIDATED,
        thesis_invalidation=ThesisInvalidation.NOT_INVALIDATED,
        execution=ExecutionOutcome.ENTRY_JUSTIFIED,
        excursion=ExcursionMeasures(),
        eligibility_pool=EligibilityPool.CAPTURED,
    )
    base.update(overrides)
    return OutcomeAxes(**base)


class TestOutcomeInvariants(unittest.TestCase):
    def test_not_matured_cannot_be_failed(self):
        with self.assertRaises(OutcomeInvariantError):
            _axes(data_resolution=DataResolution.NOT_MATURED,
                  direction=DirectionOutcome.FAILED)

    def test_not_matured_cannot_be_confirmed_either(self):
        with self.assertRaises(OutcomeInvariantError):
            _axes(data_resolution=DataResolution.NOT_MATURED,
                  direction=DirectionOutcome.CONFIRMED)

    def test_not_matured_requires_unresolved(self):
        axes = _axes(data_resolution=DataResolution.NOT_MATURED,
                     direction=DirectionOutcome.UNRESOLVED)
        self.assertIs(axes.direction, DirectionOutcome.UNRESOLVED)

    def test_insufficient_data_cannot_be_failed(self):
        with self.assertRaises(OutcomeInvariantError):
            _axes(data_resolution=DataResolution.INSUFFICIENT_DATA,
                  direction=DirectionOutcome.FAILED)

    def test_unavailable_cannot_be_confirmed(self):
        with self.assertRaises(OutcomeInvariantError):
            _axes(data_resolution=DataResolution.UNAVAILABLE,
                  direction=DirectionOutcome.CONFIRMED,
                  eligibility_pool=EligibilityPool.RECONSTRUCTED_RESEARCH)

    def test_excluded_must_record_a_reason(self):
        with self.assertRaises(OutcomeInvariantError):
            _axes(eligibility_pool=EligibilityPool.EXCLUDED,
                  direction=DirectionOutcome.UNRESOLVED)

    def test_unavailable_cannot_be_in_the_captured_pool(self):
        with self.assertRaises(OutcomeInvariantError):
            _axes(data_resolution=DataResolution.UNAVAILABLE,
                  direction=DirectionOutcome.UNRESOLVED,
                  eligibility_pool=EligibilityPool.CAPTURED)

    def test_reconstructed_research_is_never_calibration_eligible(self):
        axes = _axes(eligibility_pool=EligibilityPool.RECONSTRUCTED_RESEARCH)
        self.assertFalse(axes.is_calibration_eligible)
        self.assertFalse(EligibilityPool.RECONSTRUCTED_RESEARCH.permits_calibration)

    def test_captured_with_a_verdict_is_calibration_eligible(self):
        self.assertTrue(_axes().is_calibration_eligible)

    def test_an_abstention_is_not_calibration_eligible_and_is_not_a_failure(self):
        axes = _axes(direction=DirectionOutcome.ABSTAINED)
        self.assertFalse(axes.is_calibration_eligible)
        self.assertFalse(axes.direction.is_verdict)

    def test_neutral_abstained_and_unresolved_are_all_distinct(self):
        values = {
            DirectionOutcome.NEUTRAL_WITHIN_BAND.value,
            DirectionOutcome.ABSTAINED.value,
            DirectionOutcome.UNRESOLVED.value,
            DirectionOutcome.NOT_APPLICABLE.value,
        }
        self.assertEqual(len(values), 4)
        for state in (DirectionOutcome.NEUTRAL_WITHIN_BAND,
                      DirectionOutcome.ABSTAINED,
                      DirectionOutcome.UNRESOLVED):
            self.assertFalse(state.is_verdict)

    def test_setup_and_thesis_invalidation_are_separate_fields(self):
        axes = _axes(setup_invalidation=SetupInvalidation.INVALIDATED,
                     thesis_invalidation=ThesisInvalidation.NOT_INVALIDATED)
        record = axes.as_record()
        self.assertEqual(record["setup_invalidation"], "invalidated")
        self.assertEqual(record["thesis_invalidation"], "not_invalidated")
        self.assertIs(axes.direction, DirectionOutcome.CONFIRMED)

    def test_the_two_invalidation_enums_are_not_the_same_type(self):
        self.assertIsNot(SetupInvalidation, ThesisInvalidation)
        self.assertNotIn(
            "not_assessable", {s.value for s in SetupInvalidation}
        )

    def test_unresolved_axes_helper_is_consistent(self):
        axes = unresolved_axes(
            data_resolution=DataResolution.NOT_MATURED,
            eligibility_pool=EligibilityPool.RECONSTRUCTED_RESEARCH,
        )
        self.assertIs(axes.direction, DirectionOutcome.UNRESOLVED)
        self.assertFalse(axes.is_calibration_eligible)

    def test_unresolved_axes_excluded_requires_a_reason(self):
        axes = unresolved_axes(
            data_resolution=DataResolution.UNAVAILABLE,
            eligibility_pool=EligibilityPool.EXCLUDED,
            exclusion_reason=ExclusionReason.ANCHOR_MISSING,
        )
        self.assertEqual(axes.as_record()["exclusion_reason"], "anchor_missing")

    def test_excursion_measures_are_measurements_not_scores(self):
        record = ExcursionMeasures(
            terminal_return=0.012, mfe=0.02, mae=-0.005, path_bars=9
        ).as_record()
        for key in ("score", "grade", "rating", "accuracy"):
            self.assertNotIn(key, record)


# ===========================================================================
# 9. VALIDATION CONFIG
# ===========================================================================
class TestValidationConfig(unittest.TestCase):
    def test_horizon_windows_are_read_from_the_architecture(self):
        for horizon, window in HORIZON_EVALUATION_WINDOW.items():
            self.assertEqual(
                DEFAULT_VALIDATION_CONFIG.window_for(horizon), window, horizon.value
            )
            self.assertEqual(
                DEFAULT_VALIDATION_CONFIG.window_for(horizon.value), window
            )

    def test_windows_are_not_restated_in_the_config_module(self):
        source = inspect.getsource(config_mod)
        for literal in ("days=3", "days=14", "days=90"):
            self.assertNotIn(literal, source, literal)

    def test_an_unknown_horizon_returns_none_not_a_default(self):
        self.assertIsNone(DEFAULT_VALIDATION_CONFIG.window_for("nonsense"))

    def test_cadence_defaults_are_read_from_bars(self):
        self.assertEqual(
            DEFAULT_VALIDATION_CONFIG.max_gap_multiple,
            bars_mod.DEFAULT_MAX_GAP_MULTIPLE,
        )
        self.assertEqual(
            DEFAULT_VALIDATION_CONFIG.min_bars_for_cadence,
            bars_mod.DEFAULT_MIN_BARS_FOR_CADENCE,
        )

    def test_config_hash_is_deterministic(self):
        self.assertEqual(
            ValidationConfig().config_hash, ValidationConfig().config_hash
        )

    def test_changing_a_value_changes_the_hash(self):
        self.assertNotEqual(
            ValidationConfig().config_hash,
            ValidationConfig(max_gap_multiple=3.0).config_hash,
        )

    def test_provenance_states_it_is_uncalibrated(self):
        provenance = DEFAULT_VALIDATION_CONFIG.as_provenance()
        self.assertEqual(provenance["version"], VALIDATION_CONFIG_VERSION)
        self.assertIn("RESEARCH DEFAULTS", provenance["status"])
        self.assertIn("NOT CALIBRATED", provenance["status"])
        self.assertIn("horizon_windows", provenance["sources"])

    def test_provenance_carries_the_chosen_values_not_only_a_hash(self):
        chosen = DEFAULT_VALIDATION_CONFIG.as_provenance()["chosen"]
        for key in ("max_gap_multiple", "min_bars_for_cadence",
                    "neutral_band_mode", "horizon_windows_hours"):
            self.assertIn(key, chosen)

    def test_invalid_configurations_are_rejected(self):
        for bad in ({"max_gap_multiple": 0.0}, {"min_bars_for_cadence": 1},
                    {"neutral_band_mode": "vibes"}, {"version": ""},
                    {"neutral_band_atr_multiple": -1.0}, {"horizon_windows": {}}):
            with self.assertRaises(ValueError, msg=str(bad)):
                ValidationConfig(**bad)

    def test_no_percentage_or_significance_language_appears(self):
        source = inspect.getsource(config_mod).lower()
        for forbidden in ("p_value", "p-value", "significance", "confidence_pct",
                          "hit_rate", "accuracy"):
            self.assertNotIn(forbidden, source, forbidden)


# ===========================================================================
# 10. PURITY AND PRODUCTION SAFETY
# ===========================================================================
NEW_MODULES = (config_mod, outcome_mod, maturity_mod, series_mod, bars_mod)


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


class TestPurityAndSafety(unittest.TestCase):
    def test_the_new_modules_perform_no_io(self):
        for module in NEW_MODULES:
            tree = ast.parse(inspect.getsource(module))
            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    names = [a.name for a in node.names] + [
                        getattr(node, "module", "") or ""
                    ]
                    for name in names:
                        for forbidden in ("requests", "streamlit", "threading",
                                          "production_core", "socket", "urllib",
                                          "sqlite3", "psycopg", "asyncio",
                                          "subprocess", "multiprocessing"):
                            self.assertNotIn(
                                forbidden, name, f"{module.__name__}:{name}"
                            )

    def test_no_ai_or_telegram_reference(self):
        for module in NEW_MODULES:
            names = {n.lower() for n in _referenced_names(module)}
            for forbidden in ("telegram", "sendmessage", "openai", "anthropic",
                              "gemini", "groq", "completions"):
                self.assertFalse(
                    any(forbidden in name for name in names),
                    f"{module.__name__} references {forbidden}",
                )

    def test_no_scheduler_thread_or_daemon(self):
        """AST, not text: these modules legitimately NAME a capture schedule in
        prose to explain what they must never depend on."""
        for module in NEW_MODULES:
            names = {n.lower() for n in _referenced_names(module)}
            for forbidden in ("thread", "timer", "sleep", "crontab", "scheduler",
                              "daemon", "spawn"):
                self.assertFalse(
                    any(forbidden in name for name in names),
                    f"{module.__name__} references {forbidden}",
                )
            tree = ast.parse(inspect.getsource(module))
            self.assertFalse(
                [n for n in ast.walk(tree) if isinstance(n, ast.While)],
                f"{module.__name__} contains a while loop",
            )

    def test_no_ddl_or_dml_verb_appears(self):
        for module in NEW_MODULES:
            source = inspect.getsource(module).upper()
            for verb in ("CREATE TABLE", "ALTER TABLE", "DROP TABLE", "TRUNCATE",
                         "CREATE INDEX", "INSERT INTO", "UPDATE ", "DELETE FROM"):
                self.assertNotIn(verb, source, f"{module.__name__}:{verb}")

    def test_no_persistence_lock_or_state_write(self):
        for module in NEW_MODULES:
            names = _referenced_names(module)
            for forbidden in ("_save_persistent_state", "_load_persistent_state",
                              "_PERSISTENCE_LOCK", "insert_rows", "query_bars"):
                self.assertNotIn(forbidden, names, f"{module.__name__}:{forbidden}")

    def test_production_core_is_byte_for_byte_unchanged(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        result = subprocess.run(
            ["git", "diff", "--exit-code", "--", "apex/production_core.py"],
            cwd=root, capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout[:2000])

    def test_only_authorized_validation_modules_exist(self):
        """The validation surface may only grow by approved stage.

        ``resolve.py`` was authorized and added by D-2C2, so its presence is no
        longer an accident. The guard's job is unchanged -- it still fails the
        moment an UNapproved module appears -- so the boundary advanced rather
        than the check weakening. ``metrics.py`` remains forbidden: aggregation
        is a later stage and must not arrive early.
        """
        validation_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "apex", "b2", "validation",
        )
        present = {f for f in os.listdir(validation_dir) if f.endswith(".py")}
        self.assertEqual(
            present,
            {"__init__.py", "anchor.py", "bars.py", "config.py",
             "maturity.py", "outcome.py", "resolve.py", "series.py"},
        )
        self.assertNotIn("metrics.py", present)

    def test_no_module_computes_a_rate_or_percentage(self):
        for module in NEW_MODULES:
            names = {n.lower() for n in _referenced_names(module)}
            for forbidden in ("hit_rate", "accuracy", "win_rate", "calibrate",
                              "significance", "p_value", "wilson"):
                self.assertFalse(
                    any(forbidden in name for name in names),
                    f"{module.__name__} references {forbidden}",
                )

    def test_the_validation_package_surface_is_unchanged(self):
        """__init__.py was deliberately not modified; D-1 imports still work."""
        from apex.b2 import validation
        for name in ("MarketAnchor", "MarketBar", "classify_anchor",
                     "coverage", "forward_bars", "analysis_ohlc"):
            self.assertTrue(hasattr(validation, name), name)


if __name__ == "__main__":
    unittest.main()
