"""Stage D-2D0: the per-observation orchestrator.

Covers ``apex.b2.evaluation.observation``, which composes the four frozen
validation stages -- D-2C2 (``resolve_direction_and_path``), D-2C3
(``resolve_setup_and_execution``), D-2C5 (``verify_lineage`` /
``classify_readiness``) and D-2C4 (``build_validation_envelope``, reached
through D-2C5's ``build_verified_envelope``) -- into one result, and is the
first producer of a ``ValidationEnvelope`` anywhere outside a test.

Nothing here reimplements any of those stages' business logic. Every fixture
runs the real resolvers, and the composition test compares the orchestrator's
output against the SAME primitives invoked independently rather than against a
copied formula -- so this suite verifies the wiring, not a restatement of the
arithmetic.

D-2D0 is not aggregation. No cohort, deduplication, overlap, effective sample
size, ratio or sample floor is exercised here, and a scope guard below asserts
none has arrived early.

Imports ``apex.production_core`` for the safety assertions, so durable-state
isolation is installed first. Nothing here performs I/O.
"""
from __future__ import annotations

import ast
import dataclasses
import hashlib
import inspect
import io
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
from apex.b2.evaluation import observation as observation_mod
from apex.b2.evaluation.observation import (
    EVALUATION_SCHEMA_VERSION,
    DefectReason,
    EvaluatedObservation,
    LineageDefect,
    ProvenanceGrade,
    classify_provenance,
    evaluate_observation,
)
from apex.b2.enums import Direction
from apex.b2.modules import module_for, registered_instruments
from apex.b2.validation.anchor import AnchorStatus
from apex.b2.validation.bars import GRANULARITY_1D, MarketBar
from apex.b2.validation.config import DEFAULT_VALIDATION_CONFIG, ValidationConfig
from apex.b2.validation.envelope import build_validation_envelope
from apex.b2.validation.invalidation import resolve_setup_and_execution
from apex.b2.validation.outcome import (
    DataResolution,
    DirectionOutcome,
    EligibilityPool,
    ExclusionReason,
)
from apex.b2.validation.readiness import ReadinessTier, classify_readiness
from apex.b2.validation.resolve import claim_direction, resolve_direction_and_path
from apex.b2.validation.series import SeriesBindingQuality

UTC = timezone.utc
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

EVAL_AT = datetime(2026, 8, 30, 22, 4, 43, 893828, tzinfo=UTC)
NOW = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)
ANCHOR_PRICE = 3330.0
ANCHOR_ATR = 12.0
ANCHOR_VOL = 0.0012

#: The exact quote level the inverted-FX fixtures anchor on.
JPY_QUOTE = 100.0
JPY_ATR = 0.0001


# ===========================================================================
# Fixtures -- deliberately the same shapes the D-2C suites already use, so a
# result here is comparable with a result there.
# ===========================================================================

def _anchor(symbol="XAUUSD=X", *, invert=False, analysis=ANCHOR_PRICE,
            last=ANCHOR_PRICE, atr=ANCHOR_ATR, vol=ANCHOR_VOL,
            fallback_used=False):
    return {
        "analysis_price": analysis, "last_price": last, "symbol": symbol,
        "symbol_requested": symbol, "symbol_fallback_used": fallback_used,
        "invert": invert, "market_ts": 1, "market_ts_iso": "",
        "volatility_scale": vol, "atr": atr, "atr_ratio": 1.05,
        "volatility_regime": "normal", "price_source": "yahoo_5m_tactical",
        "granularity": "5m", "anchor_status": "anchor_captured",
    }


def _record(direction="bullish", *, instrument="Gold", anchor=None,
            horizon="tactical", evaluated_at=EVAL_AT, storage_id="s1",
            record_id="r1", schema_version=2):
    payload = {
        "schema_version": schema_version, "record_id": record_id,
        "instrument": instrument, "horizon": horizon,
        "evaluated_at": (
            evaluated_at if isinstance(evaluated_at, str) else evaluated_at.isoformat()
        ),
        "market_anchor": _anchor() if anchor is None else anchor,
        "claim": {"direction": direction, "horizon": horizon},
        "execution": None, "gates_triggered": [],
    }
    return {"storage_id": storage_id, "record_id": record_id,
            "instrument": instrument, "horizon": horizon, "record": payload}


def _bar(day, close, *, high=None, low=None, symbol="XAUUSD=X",
         instrument="Gold", invert=False, granularity=GRANULARITY_1D):
    high = close if high is None else high
    low = close if low is None else low
    return MarketBar(
        symbol=symbol, instrument=instrument, granularity=granularity,
        bar_time=datetime(2026, 9, day, tzinfo=UTC), open=close,
        high=max(high, close), low=min(low, close), close=close,
        volume=None, invert=invert,
    )


def _series(terminal, *, symbol="XAUUSD=X", instrument="Gold", invert=False,
            flat=ANCHOR_PRICE):
    """Eleven flat bars, one terminal bar, then two bars past the window end.

    The trailing pair is what lets ``assess_maturity`` distinguish "capture
    reached past this window" from "capture has not got there yet" -- without
    it every fixture would be MATURED_AWAITING_BARS.
    """
    bars = [
        _bar(d, flat, symbol=symbol, instrument=instrument, invert=invert)
        for d in range(1, 12)
    ]
    bars.append(_bar(12, terminal, symbol=symbol, instrument=instrument,
                     invert=invert))
    bars += [
        _bar(d, terminal, symbol=symbol, instrument=instrument, invert=invert)
        for d in (20, 25)
    ]
    return bars


def _gold_convention():
    return b2_bridge.symbol_convention("Gold")


def _jpy_anchor():
    return _anchor("USDJPY=X", invert=True, analysis=1.0 / JPY_QUOTE,
                   last=JPY_QUOTE, atr=JPY_ATR)


def _jpy_series(terminal_quote):
    return _series(terminal_quote, symbol="USDJPY=X", instrument="JPY",
                   invert=True, flat=JPY_QUOTE)


def _evaluate(record=None, bars=None, *, as_of=NOW, instrument="Gold",
              config=None, convention=..., malformed_row_count=None):
    rec = _record() if record is None else record
    supplied = _series(ANCHOR_PRICE) if bars is None else bars
    conv = (
        b2_bridge.symbol_convention(instrument) if convention is ... else convention
    )
    return evaluate_observation(
        record=rec, bars=supplied, as_of=as_of, convention=conv,
        config=config or DEFAULT_VALIDATION_CONFIG,
        malformed_row_count=malformed_row_count,
    )


def _identifiers(obj) -> set[str]:
    """Every NAME the module references -- never its prose.

    AST rather than raw source, for the reason the b2_bridge guard already
    documents: a module may legitimately NAME a thing in a docstring to
    explain how it relates to it. ``observation.py``'s own docstring says
    which aggregation concepts it deliberately does NOT implement, and a
    substring check over the source would read that disclaimer as a
    violation of itself.
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


# ===========================================================================
# A. SUCCESSFUL COMPOSITION   (9 tests)
# ===========================================================================
class TestSuccessfulComposition(unittest.TestCase):
    def test_confirmed_produces_an_evaluated_observation(self):
        result = _evaluate(bars=_series(ANCHOR_PRICE * 1.20))
        self.assertIsInstance(result, EvaluatedObservation)
        self.assertIs(result.envelope.outcome_axes.direction,
                      DirectionOutcome.CONFIRMED)
        self.assertFalse(result.is_defect)

    def test_failed_is_a_verdict_and_never_an_exception(self):
        """A wrong call is a legitimate market outcome, not an error."""
        result = _evaluate(bars=_series(ANCHOR_PRICE * 0.80))
        self.assertIsInstance(result, EvaluatedObservation)
        self.assertIs(result.envelope.outcome_axes.direction,
                      DirectionOutcome.FAILED)

    def test_failed_is_exactly_as_ready_as_confirmed(self):
        """Readiness grades the EVIDENCE, never the answer."""
        good = _evaluate(bars=_series(ANCHOR_PRICE * 1.20))
        bad = _evaluate(bars=_series(ANCHOR_PRICE * 0.80))
        self.assertIs(good.readiness, ReadinessTier.CALIBRATION_ELIGIBLE)
        self.assertIs(bad.readiness, ReadinessTier.CALIBRATION_ELIGIBLE)

    def test_bearish_claim_confirmed_by_a_fall(self):
        result = _evaluate(record=_record("bearish"),
                           bars=_series(ANCHOR_PRICE * 0.80))
        self.assertIs(result.envelope.outcome_axes.direction,
                      DirectionOutcome.CONFIRMED)

    def test_neutral_within_band_is_resolved_evidence_not_a_verdict(self):
        result = _evaluate(bars=_series(ANCHOR_PRICE))
        self.assertIsInstance(result, EvaluatedObservation)
        self.assertIs(result.envelope.outcome_axes.direction,
                      DirectionOutcome.NEUTRAL_WITHIN_BAND)
        self.assertIs(result.envelope.outcome_axes.data_resolution,
                      DataResolution.RESOLVED)

    def test_neutral_is_never_promoted_to_calibration_eligibility(self):
        """Upstream readiness is reported, never overridden here."""
        result = _evaluate(bars=_series(ANCHOR_PRICE))
        self.assertIs(result.readiness, ReadinessTier.EXCLUDED)
        self.assertFalse(result.envelope.outcome_axes.is_calibration_eligible)

    def test_abstention_is_a_feature_and_still_composes(self):
        result = _evaluate(record=_record("flat"))
        self.assertIsInstance(result, EvaluatedObservation)
        self.assertIs(result.envelope.outcome_axes.direction,
                      DirectionOutcome.ABSTAINED)
        self.assertIs(result.claim_direction, Direction.FLAT)

    def test_claim_direction_matches_the_canonical_helper(self):
        record = _record("bearish")
        result = _evaluate(record=record, bars=_series(ANCHOR_PRICE * 0.80))
        self.assertIs(result.claim_direction, claim_direction(record))

    def test_as_record_delegates_the_envelope_rather_than_copying_it(self):
        result = _evaluate(bars=_series(ANCHOR_PRICE * 1.20))
        record = result.as_record()
        self.assertEqual(record["stage"], "d2d0")
        self.assertEqual(record["evaluation_schema_version"],
                         EVALUATION_SCHEMA_VERSION)
        self.assertFalse(record["defect"])
        self.assertEqual(record["envelope"], result.envelope.as_record())


# ===========================================================================
# B. DETERMINISM   (5 tests)
# ===========================================================================
class TestDeterminism(unittest.TestCase):
    def test_repeated_calls_are_semantically_identical(self):
        results = [_evaluate(bars=_series(ANCHOR_PRICE * 1.20)) for _ in range(4)]
        records = {repr(r.as_record()) for r in results}
        self.assertEqual(len(records), 1)

    def test_all_three_hashes_are_stable_across_repeated_calls(self):
        results = [_evaluate(bars=_series(ANCHOR_PRICE * 1.20)) for _ in range(4)]
        self.assertEqual(len({r.envelope.validation_id for r in results}), 1)
        self.assertEqual(len({r.envelope.input_hash for r in results}), 1)
        self.assertEqual(len({r.envelope.outcome_hash for r in results}), 1)

    def test_a_different_as_of_is_a_different_result_not_nondeterminism(self):
        """outcome_hash commits to maturity_state, so it MOVES as the window
        matures -- by D-2C4's deliberate design. validation_id identifies the
        JOB and does not move. Both facts are pinned here so a later reader
        cannot mistake one for a bug in the other."""
        early = _evaluate(bars=_series(ANCHOR_PRICE * 1.20),
                          as_of=datetime(2026, 9, 2, tzinfo=UTC))
        late = _evaluate(bars=_series(ANCHOR_PRICE * 1.20), as_of=NOW)
        self.assertEqual(early.envelope.validation_id, late.envelope.validation_id)
        self.assertNotEqual(early.envelope.outcome_hash, late.envelope.outcome_hash)

    def test_malformed_row_count_reaches_the_input_hash(self):
        without = _evaluate(bars=_series(ANCHOR_PRICE * 1.20))
        with_count = _evaluate(bars=_series(ANCHOR_PRICE * 1.20),
                               malformed_row_count=3)
        self.assertIsNone(without.envelope.context.malformed_row_count)
        self.assertEqual(with_count.envelope.context.malformed_row_count, 3)
        self.assertNotEqual(without.envelope.input_hash,
                            with_count.envelope.input_hash)

    def test_defects_are_deterministic_too(self):
        defects = [_evaluate(record=_record(horizon="not_a_horizon"))
                   for _ in range(3)]
        self.assertEqual(len({repr(d.as_record()) for d in defects}), 1)


# ===========================================================================
# C. COMPOSITION PATH   (6 tests)
# ===========================================================================
class TestCompositionPath(unittest.TestCase):
    """Verified against the primitives themselves, never against a copy of
    their arithmetic."""

    def _independently(self, record, bars, *, instrument="Gold", as_of=NOW):
        config = DEFAULT_VALIDATION_CONFIG
        path = resolve_direction_and_path(
            record=record, bars=bars, now=as_of,
            convention=b2_bridge.symbol_convention(instrument), config=config,
        )
        d2c3 = resolve_setup_and_execution(record=record, path_resolution=path)
        envelope = build_validation_envelope(
            record=record, path_resolution=path, d2c3_resolution=d2c3,
            validation_config=config,
        )
        readiness = classify_readiness(path_resolution=path, d2c3_resolution=d2c3)
        return path, d2c3, envelope, readiness

    def test_envelope_equals_the_primitives_run_in_the_same_order(self):
        record, bars = _record(), _series(ANCHOR_PRICE * 1.20)
        _, _, envelope, _ = self._independently(record, bars)
        result = _evaluate(record=record, bars=bars)
        self.assertEqual(result.envelope.validation_id, envelope.validation_id)
        self.assertEqual(result.envelope.input_hash, envelope.input_hash)
        self.assertEqual(result.envelope.outcome_hash, envelope.outcome_hash)
        self.assertEqual(result.envelope.as_record(), envelope.as_record())

    def test_readiness_equals_the_d2c5_classifier(self):
        record, bars = _record(), _series(ANCHOR_PRICE * 1.20)
        _, _, _, readiness = self._independently(record, bars)
        self.assertIs(_evaluate(record=record, bars=bars).readiness, readiness)

    def test_the_d2c3_axes_survive_onto_the_envelope(self):
        record, bars = _record(), _series(ANCHOR_PRICE * 1.20)
        _, d2c3, _, _ = self._independently(record, bars)
        axes = _evaluate(record=record, bars=bars).envelope.outcome_axes
        self.assertIs(axes.setup_invalidation, d2c3.setup.state)
        self.assertIs(axes.execution, d2c3.execution.state)
        self.assertIs(axes.thesis_invalidation, d2c3.thesis)

    def test_verification_is_not_bypassed(self):
        """``build_verified_envelope`` is imported, ``build_validation_envelope``
        is not. Calling D-2C4 directly would rebuild the exact exposure D-2C5
        exists to close."""
        names = _identifiers(observation_mod)
        self.assertIn("build_verified_envelope", names)
        self.assertNotIn("build_validation_envelope", names)

    def test_all_four_stages_are_actually_invoked(self):
        names = _identifiers(observation_mod)
        for entry_point in ("resolve_direction_and_path",
                            "resolve_setup_and_execution",
                            "build_verified_envelope",
                            "classify_readiness"):
            self.assertIn(entry_point, names, entry_point)

    def test_no_validation_logic_is_reimplemented(self):
        """No band, excursion, invalidation, readiness or hash arithmetic."""
        names = {n.lower() for n in _identifiers(observation_mod)}
        for forbidden in ("neutral_band", "sha256", "hashlib", "canonical_json",
                          "_excursions", "mfe", "mae", "assess_maturity",
                          "bind_series", "canonicalize_bars", "_pool_for",
                          "_direction_outcome", "_scan_for_touch"):
            self.assertNotIn(forbidden, names, forbidden)


# ===========================================================================
# D. MATURITY STATES   (4 tests)
# ===========================================================================
class TestMaturity(unittest.TestCase):
    def test_not_matured_composes_and_is_provisional(self):
        result = _evaluate(bars=_series(ANCHOR_PRICE * 1.20),
                           as_of=datetime(2026, 9, 2, tzinfo=UTC))
        self.assertIsInstance(result, EvaluatedObservation)
        self.assertEqual(result.envelope.context.maturity_state, "not_matured")
        self.assertIs(result.readiness, ReadinessTier.PROVISIONAL)

    def test_not_matured_is_never_a_directional_verdict(self):
        result = _evaluate(bars=_series(ANCHOR_PRICE * 1.20),
                           as_of=datetime(2026, 9, 2, tzinfo=UTC))
        self.assertIs(result.envelope.outcome_axes.direction,
                      DirectionOutcome.UNRESOLVED)
        self.assertFalse(result.envelope.outcome_axes.direction.is_verdict)

    def test_matured_partial_composes_and_is_provisional(self):
        gappy = [_bar(d, ANCHOR_PRICE) for d in (1, 2, 3)]
        gappy += [_bar(d, ANCHOR_PRICE) for d in (20, 25)]
        result = _evaluate(bars=gappy)
        self.assertIsInstance(result, EvaluatedObservation)
        self.assertEqual(result.envelope.context.maturity_state,
                         "matured_partial")
        self.assertIs(result.readiness, ReadinessTier.PROVISIONAL)

    def test_matured_partial_is_never_calibration_eligible(self):
        gappy = [_bar(d, ANCHOR_PRICE) for d in (1, 2, 3)]
        gappy += [_bar(d, ANCHOR_PRICE) for d in (20, 25)]
        result = _evaluate(bars=gappy)
        self.assertIsNot(result.readiness, ReadinessTier.CALIBRATION_ELIGIBLE)


# ===========================================================================
# E. MISSING AND CONTRADICTORY DATA   (6 tests)
# ===========================================================================
class TestAbsentData(unittest.TestCase):
    def test_no_bars_never_becomes_failed(self):
        result = _evaluate(bars=[])
        self.assertIsInstance(result, EvaluatedObservation)
        self.assertIs(result.envelope.outcome_axes.direction,
                      DirectionOutcome.UNRESOLVED)

    def test_missing_series_never_becomes_failed(self):
        other = [_bar(1, 1.10, symbol="EURUSD=X", instrument="EUR")]
        result = _evaluate(bars=other)
        self.assertIsInstance(result, EvaluatedObservation)
        self.assertIs(result.envelope.outcome_axes.direction,
                      DirectionOutcome.UNRESOLVED)
        self.assertIs(result.envelope.outcome_axes.data_resolution,
                      DataResolution.UNAVAILABLE)

    def test_missing_series_is_excluded_with_a_stated_reason(self):
        other = [_bar(1, 1.10, symbol="EURUSD=X", instrument="EUR")]
        axes = _evaluate(bars=other).envelope.outcome_axes
        self.assertIs(axes.eligibility_pool, EligibilityPool.EXCLUDED)
        self.assertIsNotNone(axes.exclusion_reason)

    def test_conflicted_bars_never_become_failed(self):
        a = _bar(3, ANCHOR_PRICE)
        b = _bar(3, ANCHOR_PRICE * 2)
        self.assertEqual(a.observation_id, b.observation_id)
        self.assertNotEqual(a.content_hash, b.content_hash)
        result = _evaluate(bars=[_bar(1, ANCHOR_PRICE), a, b,
                                 _bar(20, ANCHOR_PRICE), _bar(25, ANCHOR_PRICE)])
        self.assertIsInstance(result, EvaluatedObservation)
        self.assertIs(result.envelope.outcome_axes.direction,
                      DirectionOutcome.UNRESOLVED)

    def test_conflicted_bars_are_reported_never_arbitrated(self):
        a = _bar(3, ANCHOR_PRICE)
        b = _bar(3, ANCHOR_PRICE * 2)
        result = _evaluate(bars=[_bar(1, ANCHOR_PRICE), a, b,
                                 _bar(20, ANCHOR_PRICE), _bar(25, ANCHOR_PRICE)])
        self.assertIn(ExclusionReason.BAR_CONTENT_CONFLICT.value,
                      [r for r in result.envelope.outcome_hash_basis["reasons"]])
        self.assertIn(a.observation_id, result.envelope.context.conflict_ids)

    def test_conflict_order_does_not_change_the_result(self):
        a = _bar(3, ANCHOR_PRICE)
        b = _bar(3, ANCHOR_PRICE * 2)
        head, tail = [_bar(1, ANCHOR_PRICE)], [_bar(20, ANCHOR_PRICE),
                                               _bar(25, ANCHOR_PRICE)]
        forward = _evaluate(bars=head + [a, b] + tail)
        backward = _evaluate(bars=head + [b, a] + tail)
        self.assertEqual(forward.as_record(), backward.as_record())


# ===========================================================================
# F. INVERTED FX PARITY   (4 tests)
# ===========================================================================
class TestInversionParity(unittest.TestCase):
    def test_jpy_convention_is_inverted(self):
        self.assertTrue(b2_bridge.symbol_convention("JPY").invert)

    def test_quote_down_confirms_a_bullish_jpy_claim(self):
        result = _evaluate(
            record=_record("bullish", instrument="JPY", anchor=_jpy_anchor()),
            bars=_jpy_series(85.0), instrument="JPY",
        )
        self.assertIs(result.envelope.outcome_axes.direction,
                      DirectionOutcome.CONFIRMED)

    def test_quote_up_fails_a_bullish_jpy_claim(self):
        result = _evaluate(
            record=_record("bullish", instrument="JPY", anchor=_jpy_anchor()),
            bars=_jpy_series(118.0), instrument="JPY",
        )
        self.assertIs(result.envelope.outcome_axes.direction,
                      DirectionOutcome.FAILED)

    def test_inverted_and_uninverted_strength_moves_agree(self):
        """Gold rising and USDJPY falling are the same event in strength
        terms, and must produce the same directional outcome and the same
        readiness."""
        gold = _evaluate(bars=_series(ANCHOR_PRICE * 1.20))
        jpy = _evaluate(
            record=_record("bullish", instrument="JPY", anchor=_jpy_anchor()),
            bars=_jpy_series(85.0), instrument="JPY",
        )
        self.assertIs(gold.envelope.outcome_axes.direction,
                      jpy.envelope.outcome_axes.direction)
        self.assertIs(gold.readiness, jpy.readiness)
        self.assertIs(gold.provenance_grade, jpy.provenance_grade)


# ===========================================================================
# G. PROVENANCE GRADE   (12 tests)
# ===========================================================================
class TestProvenanceGrade(unittest.TestCase):
    def _gc_series(self, terminal=ANCHOR_PRICE):
        return _series(terminal, symbol="GC=F")

    def test_captured_anchor_on_its_own_series_is_ideal(self):
        result = _evaluate(bars=_series(ANCHOR_PRICE))
        self.assertIs(result.provenance_grade, ProvenanceGrade.IDEAL)
        self.assertEqual(result.envelope.context.anchor_status, "anchor_captured")
        self.assertEqual(result.envelope.context.series_binding_quality,
                         "series_exact")

    def test_gold_xauusd_anchor_against_gcf_bars_is_substituted(self):
        """The live Gold case: the 5m anchor is XAUUSD=X and the daily capture
        only ever lands under GC=F."""
        result = _evaluate(bars=self._gc_series())
        self.assertIs(result.provenance_grade, ProvenanceGrade.SUBSTITUTED_SERIES)
        self.assertEqual(result.envelope.context.bound_symbol, "GC=F")
        self.assertTrue(result.envelope.context.cross_source)

    def test_gold_fallback_anchor_against_gcf_bars_is_exact(self):
        """When production's own 5m fetch fell back, the anchor IS GC=F and the
        binding is exact -- which is a fact about a data outage, not about the
        forecast. D-2D0 reports it and changes nothing."""
        result = _evaluate(
            record=_record(anchor=_anchor("GC=F", fallback_used=True)),
            bars=self._gc_series(),
        )
        self.assertIs(result.provenance_grade, ProvenanceGrade.IDEAL)
        self.assertEqual(result.envelope.context.series_binding_quality,
                         "series_exact")
        self.assertFalse(result.envelope.context.cross_source)

    def test_reconstructed_anchor_on_an_exact_series(self):
        recon = _anchor(analysis=None, last=ANCHOR_PRICE)
        result = _evaluate(record=_record(anchor=recon), bars=_series(ANCHOR_PRICE))
        self.assertIs(result.provenance_grade, ProvenanceGrade.RECONSTRUCTED_ANCHOR)
        self.assertEqual(result.envelope.context.anchor_status,
                         "anchor_reconstructed")

    def test_reconstructed_anchor_on_a_substituted_series_is_degraded(self):
        recon = _anchor(analysis=None, last=ANCHOR_PRICE)
        result = _evaluate(record=_record(anchor=recon), bars=self._gc_series())
        self.assertIs(result.provenance_grade, ProvenanceGrade.DEGRADED)

    def test_no_bindable_series_has_no_provenance_to_grade(self):
        result = _evaluate(bars=[])
        self.assertIs(result.provenance_grade, ProvenanceGrade.UNAVAILABLE)

    def test_missing_anchor_has_no_provenance_to_grade(self):
        record = _record()
        record["record"]["market_anchor"] = None
        result = _evaluate(record=record, bars=_series(ANCHOR_PRICE))
        self.assertIs(result.provenance_grade, ProvenanceGrade.UNAVAILABLE)

    def test_provenance_never_overrides_readiness(self):
        """A substituted series is graded here AND tiered upstream. D-2D0 must
        report both, and must not have moved either."""
        result = _evaluate(bars=self._gc_series(ANCHOR_PRICE * 1.20))
        self.assertIs(result.provenance_grade, ProvenanceGrade.SUBSTITUTED_SERIES)
        self.assertIs(result.readiness, ReadinessTier.RESEARCH_ONLY)
        self.assertIs(result.envelope.outcome_axes.eligibility_pool,
                      EligibilityPool.RECONSTRUCTED_RESEARCH)

    def test_substituted_gold_never_reaches_the_captured_pool(self):
        result = _evaluate(bars=self._gc_series(ANCHOR_PRICE * 1.20))
        self.assertFalse(result.envelope.outcome_axes.is_calibration_eligible)

    def test_classification_is_total_over_every_combination(self):
        for status in AnchorStatus:
            for quality in SeriesBindingQuality:
                for cross in (True, False):
                    grade = classify_provenance(
                        anchor_status=status, binding_quality=quality,
                        cross_source=cross,
                    )
                    self.assertIsInstance(grade, ProvenanceGrade)

    def test_cross_source_is_disqualifying_even_on_an_exact_label(self):
        grade = classify_provenance(
            anchor_status=AnchorStatus.CAPTURED,
            binding_quality=SeriesBindingQuality.SERIES_EXACT,
            cross_source=True,
        )
        self.assertIs(grade, ProvenanceGrade.SUBSTITUTED_SERIES)

    def test_the_documented_two_by_two_mapping_holds(self):
        cases = {
            (AnchorStatus.CAPTURED, SeriesBindingQuality.SERIES_EXACT):
                ProvenanceGrade.IDEAL,
            (AnchorStatus.CAPTURED, SeriesBindingQuality.SERIES_SUBSTITUTED):
                ProvenanceGrade.SUBSTITUTED_SERIES,
            (AnchorStatus.RECONSTRUCTED, SeriesBindingQuality.SERIES_EXACT):
                ProvenanceGrade.RECONSTRUCTED_ANCHOR,
            (AnchorStatus.RECONSTRUCTED, SeriesBindingQuality.SERIES_SUBSTITUTED):
                ProvenanceGrade.DEGRADED,
        }
        for (status, quality), expected in cases.items():
            actual = classify_provenance(
                anchor_status=status, binding_quality=quality,
                cross_source=quality is SeriesBindingQuality.SERIES_SUBSTITUTED,
            )
            self.assertIs(actual, expected, f"{status}/{quality}")


# ===========================================================================
# H. ASSET CLASS   (4 tests)
# ===========================================================================
class TestAssetClass(unittest.TestCase):
    def test_asset_class_comes_from_the_module_registry(self):
        result = _evaluate(bars=_series(ANCHOR_PRICE))
        self.assertEqual(result.asset_class, module_for("Gold").MODULE_KEY)

    def test_every_registered_instrument_resolves_a_module_key(self):
        for instrument in registered_instruments():
            self.assertTrue(str(module_for(instrument).MODULE_KEY))

    def test_one_fx_module_serves_every_currency(self):
        keys = {module_for(i).MODULE_KEY
                for i in ("USD", "EUR", "GBP", "CAD", "JPY", "CHF", "AUD", "NZD")}
        self.assertEqual(len(keys), 1)

    def test_no_hardcoded_instrument_to_asset_class_map_exists(self):
        source = inspect.getsource(observation_mod)
        for literal in ("gold_module_v1", "fx_module_v1", "oil_module_v1",
                        "nasdaq_module_v1"):
            self.assertNotIn(literal, source, literal)


# ===========================================================================
# I. COMPOSITION DEFECTS   (10 tests)
# ===========================================================================
class TestLineageDefect(unittest.TestCase):
    def test_unknown_horizon_returns_a_defect_and_does_not_raise(self):
        """The R7 edge case, reproduced exactly.

        D-2C2 correctly answers UNKNOWN_HORIZON with a stub maturity anchored
        on ``as_of``; D-2C5 correctly reports that this cannot be the record's
        own evaluated_at. Both stages are behaving as specified and neither is
        touched. The orchestrator's job is to survive it.
        """
        result = _evaluate(record=_record(horizon="not_a_horizon"))
        self.assertIsInstance(result, LineageDefect)
        self.assertIs(result.reason, DefectReason.LINEAGE_VERIFICATION_FAILED)

    def test_the_r7_defect_is_not_a_directional_outcome(self):
        """Checked against the DirectionOutcome vocabulary itself, not by
        substring: ``lineage_verification_failed`` legitimately contains the
        word "failed", and a naive containment check would read the defect's
        own reason code as the verdict it exists to avoid producing."""
        result = _evaluate(record=_record(horizon="not_a_horizon"))
        self.assertFalse(hasattr(result, "envelope"))
        self.assertFalse(hasattr(result, "outcome_axes"))
        outcomes = {o.value for o in DirectionOutcome}
        record = result.as_record()
        self.assertFalse(outcomes.intersection(record.keys()))
        for value in record.values():
            self.assertNotIn(value, outcomes, value)
        self.assertNotIn(result.reason.value, outcomes)

    def test_a_config_missing_the_horizon_window_also_defects(self):
        narrow = ValidationConfig(
            horizon_windows={"execution": timedelta(days=3)}
        )
        result = _evaluate(bars=_series(ANCHOR_PRICE * 1.20), config=narrow)
        self.assertIsInstance(result, LineageDefect)
        self.assertIs(result.reason, DefectReason.LINEAGE_VERIFICATION_FAILED)

    def test_an_unregistered_instrument_is_reported_never_guessed(self):
        result = _evaluate(record=_record(instrument="NOT_AN_INSTRUMENT"),
                           convention=None)
        self.assertIsInstance(result, LineageDefect)
        self.assertIs(result.reason, DefectReason.UNREGISTERED_INSTRUMENT)

    def test_a_defect_carries_the_same_storage_id_a_success_would(self):
        good = _evaluate(bars=_series(ANCHOR_PRICE * 1.20))
        bad = _evaluate(record=_record(horizon="not_a_horizon"))
        self.assertNotEqual(good.envelope.context.shadow_storage_id, "")
        self.assertNotEqual(bad.shadow_storage_id, "")
        self.assertEqual(len(bad.shadow_storage_id),
                         len(good.envelope.context.shadow_storage_id))

    def test_defect_identity_is_derived_only_from_the_record(self):
        result = _evaluate(record=_record(horizon="not_a_horizon"))
        self.assertEqual(result.shadow_record_id, "r1")
        self.assertEqual(result.instrument, "Gold")
        self.assertEqual(result.horizon, "not_a_horizon")
        self.assertEqual(result.evaluated_at, EVAL_AT.isoformat())

    def test_the_message_is_diagnostic_text_and_never_identity(self):
        result = _evaluate(record=_record(horizon="not_a_horizon"))
        self.assertTrue(result.message)
        self.assertNotIn(result.message, result.shadow_storage_id)

    def test_a_defect_record_says_it_is_a_defect(self):
        record = _evaluate(record=_record(horizon="not_a_horizon")).as_record()
        self.assertTrue(record["defect"])
        self.assertEqual(record["stage"], "d2d0")

    def test_the_is_defect_flag_discriminates_the_union(self):
        good = _evaluate(bars=_series(ANCHOR_PRICE * 1.20))
        bad = _evaluate(record=_record(horizon="not_a_horizon"))
        self.assertFalse(good.is_defect)
        self.assertTrue(bad.is_defect)

    def test_defect_reasons_are_stage_owned_not_borrowed_from_d2c2(self):
        """``ExclusionReason`` describes the EVIDENCE; a defect describes the
        COMPOSITION. Sharing a vocabulary would blur two different failures."""
        exclusion = {r.value for r in ExclusionReason}
        for reason in DefectReason:
            self.assertNotIn(reason.value, exclusion, reason.value)


# ===========================================================================
# J. IMMUTABILITY   (5 tests)
# ===========================================================================
class TestImmutability(unittest.TestCase):
    def test_evaluated_observation_is_frozen(self):
        self.assertTrue(dataclasses.fields(EvaluatedObservation))
        result = _evaluate(bars=_series(ANCHOR_PRICE * 1.20))
        with self.assertRaises(dataclasses.FrozenInstanceError):
            result.readiness = ReadinessTier.EXCLUDED

    def test_lineage_defect_is_frozen(self):
        result = _evaluate(record=_record(horizon="not_a_horizon"))
        with self.assertRaises(dataclasses.FrozenInstanceError):
            result.reason = DefectReason.UNREGISTERED_INSTRUMENT

    def test_evaluated_observation_stays_at_five_fields(self):
        """It is not a second envelope and must never become one."""
        names = tuple(f.name for f in dataclasses.fields(EvaluatedObservation))
        self.assertEqual(
            names,
            ("envelope", "readiness", "claim_direction", "asset_class",
             "provenance_grade"),
        )

    def test_lineage_defect_carries_only_stable_input_derived_fields(self):
        names = tuple(f.name for f in dataclasses.fields(LineageDefect))
        self.assertEqual(
            names,
            ("shadow_storage_id", "shadow_record_id", "instrument", "horizon",
             "evaluated_at", "reason", "message"),
        )

    def test_the_input_record_is_never_mutated(self):
        record = _record()
        before = repr(record)
        _evaluate(record=record, bars=_series(ANCHOR_PRICE * 1.20))
        self.assertEqual(repr(record), before)


# ===========================================================================
# K. PURITY   (8 tests)
# ===========================================================================
class TestPurity(unittest.TestCase):
    def test_no_forbidden_imports(self):
        for module in (observation_mod,):
            tree = ast.parse(inspect.getsource(module))
            for node in ast.walk(tree):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    names = [a.name for a in node.names] + [
                        getattr(node, "module", "") or ""]
                    for name in names:
                        for forbidden in (
                            "requests", "urllib", "socket", "http", "httpx",
                            "aiohttp", "streamlit", "threading", "asyncio",
                            "subprocess", "multiprocessing", "sqlite",
                            "psycopg", "supabase", "random", "os", "pathlib",
                            "production_core", "b2_bridge",
                            "b2_validation_bridge",
                        ):
                            self.assertNotIn(forbidden, name, name)

    def test_no_clock_is_read(self):
        """Checked as a CALL on an attribute, not as a substring: the module
        legitimately passes ``now=as_of`` as a keyword to D-2C2, and a
        substring check would read that injection as a clock read."""
        tree = ast.parse(inspect.getsource(observation_mod))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                self.assertNotIn(node.func.attr,
                                 ("now", "utcnow", "today", "time", "monotonic"),
                                 node.func.attr)

    def test_no_file_or_process_io(self):
        tree = ast.parse(inspect.getsource(observation_mod))
        called = {
            node.func.id for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        for builtin in ("open", "exec", "eval", "compile", "__import__", "input",
                        "print"):
            self.assertNotIn(builtin, called, builtin)

    def test_no_ai_telegram_scheduler_or_daemon(self):
        names = {n.lower() for n in _identifiers(observation_mod)}
        for forbidden in ("telegram", "sendmessage", "openai", "anthropic",
                          "gemini", "groq", "completions", "thread", "timer",
                          "sleep", "crontab", "scheduler", "daemon"):
            self.assertFalse(any(forbidden in n for n in names), forbidden)

    def test_no_ddl_dml_or_persistence(self):
        upper = inspect.getsource(observation_mod).upper()
        for verb in ("CREATE TABLE", "ALTER TABLE", "DROP TABLE", "TRUNCATE",
                     "INSERT INTO", "DELETE FROM", "CREATE INDEX"):
            self.assertNotIn(verb, upper, verb)
        names = _identifiers(observation_mod)
        for forbidden in ("_save_persistent_state", "_load_persistent_state",
                          "_PERSISTENCE_LOCK", "insert_rows", "query_bars",
                          "query_records", "capture_daily_bars"):
            self.assertNotIn(forbidden, names, forbidden)

    def test_the_package_imports_without_production_core(self):
        """The evaluation package is pure: it must not drag production in."""
        tree = ast.parse(inspect.getsource(observation_mod))
        modules = {getattr(n, "module", "") or "" for n in ast.walk(tree)
                   if isinstance(n, ast.ImportFrom)}
        self.assertTrue(modules)
        for module in modules:
            self.assertFalse(module.startswith("apex.production_core"), module)

    def test_no_module_level_mutable_state(self):
        """No module-level cache a second call could observe.

        ``__all__`` is exempt: it is the export declaration every module in
        this package carries, is never read by the module's own logic, and
        is not state in any sense a caller could depend on.
        """
        tree = ast.parse(inspect.getsource(observation_mod))
        for node in tree.body:
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names = {t.id for t in targets if isinstance(t, ast.Name)}
            if names == {"__all__"}:
                continue
            self.assertNotIsInstance(node.value, (ast.List, ast.Dict, ast.Set),
                                     sorted(names))

    def test_evaluate_observation_never_raises_on_market_data(self):
        """Every absent/contradictory-data fixture returns a result."""
        cases = (
            [],
            [_bar(1, 1.10, symbol="EURUSD=X", instrument="EUR")],
            [_bar(3, ANCHOR_PRICE), _bar(3, ANCHOR_PRICE * 2)],
            _series(ANCHOR_PRICE),
        )
        for bars in cases:
            result = _evaluate(bars=bars)
            self.assertIsInstance(result, (EvaluatedObservation, LineageDefect))


# ===========================================================================
# L. SCOPE -- D-2D0 ONLY   (7 tests)
# ===========================================================================
class TestScope(unittest.TestCase):
    def _unchanged(self, path):
        result = subprocess.run(["git", "diff", "--exit-code", "--", path],
                                cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0,
                         f"{path} changed:\n{result.stdout[:800]}")

    def test_no_aggregation_concept_has_arrived_early(self):
        names = {n.lower() for n in _identifiers(observation_mod)}
        for forbidden in ("cohortdefinition", "cohort_id", "membership_hash",
                          "result_hash", "independent_n", "raw_n",
                          "effective_sample_size", "confirmation_rate",
                          "neutral_rate", "resolution_rate", "hit_rate",
                          "accuracy", "win_rate", "calibrate", "wilson",
                          "p_value", "significance", "promotion",
                          "confidence_interval", "overlap_context",
                          "deduplicate", "sample_floor"):
            self.assertFalse(any(forbidden in n for n in names), forbidden)

    def test_only_the_authorized_evaluation_modules_exist(self):
        """The evaluation surface may only grow by approved stage.

        D-2D0 authorized exactly one module beside the package init.
        Stage D-2D1 authorized exactly one more, ``cohort.py``, for
        deterministic cohort construction and its three narrow ratios. The
        check itself is unchanged and still fails on any UNapproved module.
        ``metrics.py`` stays forbidden: D-2D1 is deliberately one module, and
        a second one would have to publish the cohort's internal member
        sequence across a file boundary purely to hand it over."""
        present = {f for f in os.listdir(
            os.path.join(ROOT, "apex", "b2", "evaluation")) if f.endswith(".py")}
        self.assertEqual(present, {"__init__.py", "observation.py", "cohort.py"})
        self.assertNotIn("metrics.py", present)

    def test_the_validation_surface_is_untouched(self):
        present = {f for f in os.listdir(
            os.path.join(ROOT, "apex", "b2", "validation")) if f.endswith(".py")}
        self.assertEqual(present, {"__init__.py", "anchor.py", "bars.py",
                                   "config.py", "maturity.py", "outcome.py",
                                   "resolve.py", "series.py", "invalidation.py",
                                   "envelope.py", "readiness.py"})
        self.assertNotIn("metrics.py", present)

    def test_no_new_validation_or_envelope_hash_is_defined(self):
        names = _identifiers(observation_mod)
        for forbidden in ("VALIDATION_SCHEMA_VERSION", "sha256_hex",
                          "canonical_json", "canonical_content_hash"):
            self.assertNotIn(forbidden, names, forbidden)

    def test_cross_asset_remains_withheld(self):
        with open(os.path.join(ROOT, "apex", "b2", "shadow.py"),
                  encoding="utf-8") as handle:
            self.assertIn('CROSS_ASSET_STATUS = "withheld"', handle.read())

    def test_no_d3_entry_states_are_introduced(self):
        source = inspect.getsource(observation_mod)
        for forbidden in ("ENTRY_JUSTIFIED", "ENTRY_PREMATURE", "ENTRY_LATE"):
            self.assertNotIn(forbidden, source, forbidden)

    def test_no_production_wiring(self):
        names = _identifiers(observation_mod)
        for forbidden in ("b2_bridge", "b2_validation_bridge", "production_core",
                          "observe_instrument", "run_shadow_observation",
                          "resolve_range", "resolve_observation"):
            self.assertNotIn(forbidden, names, forbidden)


# ===========================================================================
# M. EXISTING B2 PROTECTION   (5 tests)
# ===========================================================================
class TestProductionSafety(unittest.TestCase):
    def _unchanged(self, path):
        result = subprocess.run(["git", "diff", "--exit-code", "--", path],
                                cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0,
                         f"{path} changed:\n{result.stdout[:800]}")

    def test_production_core_sha_unchanged(self):
        with open(os.path.join(ROOT, "apex", "production_core.py"), "rb") as handle:
            digest = hashlib.sha256(handle.read()).hexdigest()
        self.assertEqual(
            digest, "5935f807a8584007fc053ae7bb64d62017a7e2f804258d492fdd8a4c2cb4da69")

    def test_production_core_is_byte_for_byte_unchanged(self):
        self._unchanged("apex/production_core.py")

    def test_both_bridges_are_unchanged(self):
        self._unchanged("apex/b2_bridge.py")
        self._unchanged("apex/b2_validation_bridge.py")

    def test_every_validation_module_is_unchanged(self):
        for name in ("__init__", "anchor", "bars", "config", "maturity",
                     "outcome", "resolve", "series", "invalidation", "envelope",
                     "readiness"):
            self._unchanged(f"apex/b2/validation/{name}.py")

    def test_production_signal_thresholds_are_unchanged(self):
        self.assertEqual(core.bias_from_score(0.40)[0], "\U0001f680 Strong Bullish")
        self.assertEqual(core._broad_regime("\U0001f680 Strong Bullish"), "Bullish")


# ===========================================================================
# N. PRIOR-STAGE REGRESSION -- rerun D-2C2/D-2C3/D-2C4/D-2C5 internally.
# ===========================================================================
class TestPriorSuitesUnaffected(unittest.TestCase):
    def _run_suite(self, module_name):
        module = __import__(module_name, fromlist=["*"])
        loader = unittest.TestLoader()
        suite = loader.loadTestsFromModule(module)
        buffer = io.StringIO()
        runner = unittest.TextTestRunner(stream=buffer, verbosity=0)
        result = runner.run(suite)
        self.assertTrue(result.wasSuccessful(),
                        f"{module_name} regressed: {len(result.failures)} failures, "
                        f"{len(result.errors)} errors:\n{buffer.getvalue()}")

    def test_d2c2_suite_still_passes(self):
        self._run_suite("tests.test_b2_stage_d2c_resolution")

    def test_d2c3_suite_still_passes(self):
        self._run_suite("tests.test_b2_stage_d2c3_invalidation")

    def test_d2c4_suite_still_passes(self):
        self._run_suite("tests.test_b2_stage_d2c4_envelope")

    def test_d2c5_suite_still_passes(self):
        self._run_suite("tests.test_b2_stage_d2c5_readiness")


if __name__ == "__main__":
    unittest.main()
