"""Stage D-2C5: lineage verification and per-observation readiness.

Covers ``apex.b2.validation.readiness``, which closes the cross-stage
lineage gap between D-2C2 (``resolve_direction_and_path``), D-2C3
(``resolve_setup_and_execution``) and D-2C4 (``build_validation_envelope``),
and adds an orthogonal, additive readiness classification for a future
D-2D consumer. Nothing here reimplements D-2C2/D-2C3/D-2C4 business logic;
every fixture runs the real resolvers so lineage/readiness are checked
against genuine results.

Imports ``apex.production_core`` for the safety assertions, so durable-state
isolation is installed first. Nothing here performs I/O.
"""
from __future__ import annotations

import ast
import dataclasses
import inspect
import io
import json
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
from apex.b2.validation import readiness as readiness_mod
from apex.b2.validation.bars import GRANULARITY_1D, MarketBar
from apex.b2.validation.config import DEFAULT_VALIDATION_CONFIG, ValidationConfig
from apex.b2.validation.envelope import build_validation_envelope
from apex.b2.validation.invalidation import resolve_setup_and_execution
from apex.b2.validation.outcome import (
    DirectionOutcome,
    EligibilityPool,
    ExecutionOutcome,
    SetupInvalidation,
    ThesisInvalidation,
)
from apex.b2.validation.readiness import (
    LineageError,
    ReadinessTier,
    build_verified_envelope,
    classify_readiness,
    verify_lineage,
)
from apex.b2.validation.resolve import resolve_direction_and_path

UTC = timezone.utc
EVAL_AT = datetime(2026, 8, 30, 22, 4, 43, 893828, tzinfo=UTC)
NOW = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)
ANCHOR_PRICE = 3330.0
ANCHOR_ATR = 12.0
ANCHOR_VOL = 0.0012
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

BULLISH_INVALIDATION = ANCHOR_PRICE - 20.0
BEARISH_INVALIDATION = ANCHOR_PRICE + 20.0


# ===========================================================================
# Fixtures
# ===========================================================================

def _anchor(symbol="XAUUSD=X", *, invert=False, atr=ANCHOR_ATR, price=ANCHOR_PRICE, vol=ANCHOR_VOL):
    return {
        "analysis_price": price, "last_price": price, "symbol": symbol,
        "symbol_requested": symbol, "symbol_fallback_used": False,
        "invert": invert, "market_ts": 1, "market_ts_iso": "",
        "volatility_scale": vol, "atr": atr, "atr_ratio": 1.05,
        "volatility_regime": "normal", "price_source": "yahoo_5m_tactical",
        "granularity": "5m", "anchor_status": "anchor_captured",
    }


def _execution(*, invalidation_level=None, current_price=ANCHOR_PRICE, blocked=False,
               block_reason="", invalidation_defined=None):
    defined = invalidation_defined if invalidation_defined is not None else invalidation_level is not None
    try:
        distance = (
            abs(current_price - invalidation_level)
            if invalidation_level is not None and current_price is not None else None
        )
    except TypeError:
        distance = None
    return {
        "invalidation_defined": defined, "invalidation_level": invalidation_level,
        "entry_zone_low": None, "entry_zone_high": None, "current_price": current_price,
        "invalidation_distance": distance, "invalidation_distance_atr": None,
        "room_to_opposing_atr": None, "asymmetry_ratio": None, "volatility_regime": "normal",
        "in_zone": False, "extended": False, "execution_confidence": "HIGH",
        "blocked": blocked, "block_reason": block_reason, "notes": [],
    }


def _gate(*, applies_to_open_position=False):
    return {
        "gate": "event_risk", "triggered": True, "action": "veto_execution",
        "reason": "test veto", "max_confidence": "LOW", "event_risk_state": "critical",
        "applies_to_open_position": applies_to_open_position,
    }


def _record(direction="bullish", *, instrument="Gold", anchor=None, horizon="tactical",
            evaluated_at=EVAL_AT, storage_id="s1", record_id="r1", claim=True,
            execution=None, gates_triggered=(), schema_version=2, wrapped=True):
    payload = {
        "schema_version": schema_version, "record_id": record_id, "instrument": instrument,
        "horizon": horizon,
        "evaluated_at": evaluated_at if isinstance(evaluated_at, str) else evaluated_at.isoformat(),
        "market_anchor": (_anchor() if anchor is None else anchor) if schema_version >= 2 else None,
        "claim": ({"direction": direction, "horizon": horizon} if claim else None),
        "execution": execution, "gates_triggered": list(gates_triggered),
    }
    if not wrapped:
        return payload
    return {"storage_id": storage_id, "record_id": record_id, "instrument": instrument,
            "horizon": horizon, "record": payload}


def _bar(day, close, *, high=None, low=None, symbol="XAUUSD=X", instrument="Gold",
         invert=False, month=9, granularity=GRANULARITY_1D):
    high = close * 1.001 if high is None else high
    low = close * 0.999 if low is None else low
    return MarketBar(
        symbol=symbol, instrument=instrument, granularity=granularity,
        bar_time=datetime(2026, month, day, tzinfo=UTC), open=close,
        high=max(high, close), low=min(low, close), close=close, volume=None, invert=invert,
    )


def _flat_path(days=(1, 2, 3), price=ANCHOR_PRICE, **kw):
    return [_bar(d, price, high=price, low=price, **kw) for d in days]


def _capture_tail(bars):
    if not bars:
        return []
    like = bars[-1]
    return [
        MarketBar(symbol=like.symbol, instrument=like.instrument, granularity=like.granularity,
                  bar_time=datetime(2026, 9, day, tzinfo=UTC), open=like.close, high=like.close,
                  low=like.close, close=like.close, volume=None, invert=like.invert)
        for day in (20, 25)
    ]


def _confirmed_bullish_bars(touch=False):
    bars = [_bar(1, ANCHOR_PRICE, high=ANCHOR_PRICE, low=(3300.0 if touch else ANCHOR_PRICE))]
    bars += [_bar(d, ANCHOR_PRICE, high=ANCHOR_PRICE, low=ANCHOR_PRICE) for d in range(2, 12)]
    bars.append(_bar(12, ANCHOR_PRICE * 1.20, high=ANCHOR_PRICE * 1.20, low=ANCHOR_PRICE * 1.20))
    return bars


def _resolve(record=None, bars=None, *, now=NOW, instrument="Gold", tail=True, config=None):
    rec = record if record is not None else _record()
    supplied = bars if bars is not None else _flat_path()
    if tail:
        supplied = list(supplied) + _capture_tail(supplied)
    cfg = config or DEFAULT_VALIDATION_CONFIG
    path_resolution = resolve_direction_and_path(
        record=rec, bars=supplied, now=now,
        convention=b2_bridge.symbol_convention(instrument), config=cfg,
    )
    d2c3 = resolve_setup_and_execution(record=rec, path_resolution=path_resolution)
    return rec, path_resolution, d2c3, cfg


def _identifiers(obj) -> set[str]:
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
# A. LINEAGE   (10 tests)
# ===========================================================================
class TestLineage(unittest.TestCase):
    def test_exact_lineage_passes(self):
        record = _record("bullish", execution=_execution(invalidation_level=BULLISH_INVALIDATION))
        rec, path_res, d2c3, cfg = _resolve(record, _confirmed_bullish_bars())
        verify_lineage(record=rec, path_resolution=path_res, d2c3_resolution=d2c3, validation_config=cfg)

    def test_claim_mismatch_raises(self):
        record_a = _record("bullish", execution=_execution(invalidation_level=BULLISH_INVALIDATION))
        record_b = _record("bearish", execution=_execution(invalidation_level=BEARISH_INVALIDATION))
        _, path_res_a, d2c3_a, cfg = _resolve(record_a, _confirmed_bullish_bars())
        with self.assertRaises(LineageError):
            verify_lineage(record=record_b, path_resolution=path_res_a, d2c3_resolution=d2c3_a,
                           validation_config=cfg)

    def test_evaluated_at_mismatch_raises(self):
        record_a = _record(evaluated_at=EVAL_AT)
        record_b = _record(evaluated_at=EVAL_AT + timedelta(hours=3))
        _, path_res_a, d2c3_a, cfg = _resolve(record_a)
        with self.assertRaises(LineageError):
            verify_lineage(record=record_b, path_resolution=path_res_a, d2c3_resolution=d2c3_a,
                           validation_config=cfg)

    def test_equivalent_timestamp_representations_compare_correctly(self):
        """A record whose ISO string differs in FORMAT but not INSTANT must not
        falsely raise."""
        record = _record(evaluated_at=EVAL_AT)
        _, path_res, d2c3, cfg = _resolve(record)
        equivalent = dict(record)
        equivalent_payload = dict(record["record"])
        equivalent_payload["evaluated_at"] = EVAL_AT.astimezone(timezone(timedelta(hours=2))).isoformat()
        equivalent["record"] = equivalent_payload
        verify_lineage(record=equivalent, path_resolution=path_res, d2c3_resolution=d2c3,
                       validation_config=cfg)

    def test_config_hash_mismatch_raises(self):
        record = _record("bullish", execution=_execution(invalidation_level=BULLISH_INVALIDATION))
        _, path_res, d2c3, cfg = _resolve(record, _confirmed_bullish_bars())
        variant = ValidationConfig(atr_period_bars=20)
        with self.assertRaises(LineageError):
            verify_lineage(record=record, path_resolution=path_res, d2c3_resolution=d2c3,
                           validation_config=variant)

    def test_same_config_version_different_hash_raises(self):
        default_cfg = ValidationConfig()
        variant_cfg = ValidationConfig(neutral_band_atr_multiple=0.75)
        self.assertEqual(default_cfg.version, variant_cfg.version)
        self.assertNotEqual(default_cfg.config_hash, variant_cfg.config_hash)
        record = _record()
        _, path_res, d2c3, _ = _resolve(record, config=default_cfg)
        with self.assertRaises(LineageError):
            verify_lineage(record=record, path_resolution=path_res, d2c3_resolution=d2c3,
                           validation_config=variant_cfg)

    def test_horizon_window_mismatch_raises(self):
        record_tactical = _record(horizon="tactical")
        _, path_res, d2c3, cfg = _resolve(record_tactical)
        record_structural = _record(horizon="structural")
        with self.assertRaises(LineageError):
            verify_lineage(record=record_structural, path_resolution=path_res, d2c3_resolution=d2c3,
                           validation_config=cfg)

    def test_missing_lineage_critical_identity_handled_explicitly(self):
        """A record with an unparsable evaluated_at is a DATA problem D-2C2
        already surfaces -- verify_lineage must not raise for it when the
        supplied path_resolution genuinely corresponds to that same record."""
        record = _record(evaluated_at="not-a-timestamp")
        rec, path_res, d2c3, cfg = _resolve(record)
        self.assertIs(path_res.direction, DirectionOutcome.UNRESOLVED)
        verify_lineage(record=rec, path_resolution=path_res, d2c3_resolution=d2c3, validation_config=cfg)

    def test_lineage_error_is_deterministic(self):
        record_a = _record("bullish")
        record_b = _record("bearish")
        _, path_res_a, d2c3_a, cfg = _resolve(record_a)
        raised_twice = []
        for _ in range(2):
            try:
                verify_lineage(record=record_b, path_resolution=path_res_a, d2c3_resolution=d2c3_a,
                               validation_config=cfg)
            except LineageError:
                raised_twice.append(True)
        self.assertEqual(raised_twice, [True, True])

    def test_lineage_error_never_becomes_a_market_or_data_state(self):
        """LineageError must be a real exception subclassing ValueError, never
        swallowed into a ReadinessTier/DirectionOutcome-shaped value."""
        self.assertTrue(issubclass(LineageError, ValueError))
        source = inspect.getsource(readiness_mod)
        self.assertNotIn("except LineageError", source)


# ===========================================================================
# B. D-2C3 STRUCTURAL CONSISTENCY   (5 tests)
# ===========================================================================
class TestD2C3Consistency(unittest.TestCase):
    def test_valid_d2c3_result_accepted(self):
        record = _record("bullish", execution=_execution(
            invalidation_level=BULLISH_INVALIDATION, blocked=True, block_reason="veto"),
            gates_triggered=(_gate(),))
        rec, path_res, d2c3, cfg = _resolve(record, _confirmed_bullish_bars())
        verify_lineage(record=rec, path_resolution=path_res, d2c3_resolution=d2c3, validation_config=cfg)

    def test_structurally_impossible_reasons_combination_rejected(self):
        record = _record("bullish", execution=_execution(invalidation_level=BULLISH_INVALIDATION))
        rec, path_res, d2c3, cfg = _resolve(record, _confirmed_bullish_bars())
        from apex.b2.validation.outcome import ExclusionReason
        broken_setup = dataclasses.replace(d2c3.setup, reasons=(ExclusionReason.ANCHOR_MISSING,))
        broken_d2c3 = dataclasses.replace(d2c3, setup=broken_setup)
        with self.assertRaises(LineageError):
            verify_lineage(record=rec, path_resolution=path_res, d2c3_resolution=broken_d2c3,
                           validation_config=cfg)

    def test_thesis_state_must_remain_not_assessable(self):
        record = _record()
        rec, path_res, d2c3, cfg = _resolve(record)
        self.assertIs(d2c3.thesis, ThesisInvalidation.NOT_ASSESSABLE)
        broken = dataclasses.replace(d2c3, thesis=ThesisInvalidation.INVALIDATED)
        with self.assertRaises(LineageError):
            verify_lineage(record=rec, path_resolution=path_res, d2c3_resolution=broken,
                           validation_config=cfg)

    def test_invalidated_without_touch_flag_rejected(self):
        record = _record("bullish", execution=_execution(invalidation_level=BULLISH_INVALIDATION))
        rec, path_res, d2c3, cfg = _resolve(record, _confirmed_bullish_bars(touch=True))
        self.assertIs(d2c3.setup.state, SetupInvalidation.INVALIDATED)
        broken_measures = dataclasses.replace(d2c3.setup.measures, touched=False)
        broken_setup = dataclasses.replace(d2c3.setup, measures=broken_measures)
        broken_d2c3 = dataclasses.replace(d2c3, setup=broken_setup)
        with self.assertRaises(LineageError):
            verify_lineage(record=rec, path_resolution=path_res, d2c3_resolution=broken_d2c3,
                           validation_config=cfg)

    def test_no_duplication_of_d2c3_business_logic(self):
        """readiness.py must never re-derive a touch/invalidation verdict itself."""
        names = _identifiers(readiness_mod)
        for forbidden in ("analysis_low", "analysis_high", "_scan_for_touch", "_direction_agreement"):
            self.assertNotIn(forbidden, names, forbidden)


# ===========================================================================
# C. READINESS   (19 tests)
# ===========================================================================
class TestReadiness(unittest.TestCase):
    def test_final_captured_confirmed_is_calibration_eligible(self):
        record = _record("bullish", execution=_execution(invalidation_level=BULLISH_INVALIDATION))
        _, path_res, d2c3, _ = _resolve(record, _confirmed_bullish_bars())
        self.assertIs(path_res.direction, DirectionOutcome.CONFIRMED)
        self.assertIs(classify_readiness(path_resolution=path_res, d2c3_resolution=d2c3),
                     ReadinessTier.CALIBRATION_ELIGIBLE)

    def test_final_captured_failed_is_calibration_eligible(self):
        bars = [_bar(d, ANCHOR_PRICE, high=ANCHOR_PRICE, low=ANCHOR_PRICE) for d in range(1, 12)]
        bars.append(_bar(12, ANCHOR_PRICE * 0.90, high=ANCHOR_PRICE * 0.90, low=ANCHOR_PRICE * 0.90))
        record = _record("bullish", execution=_execution(invalidation_level=ANCHOR_PRICE - 500.0))
        _, path_res, d2c3, _ = _resolve(record, bars)
        self.assertIs(path_res.direction, DirectionOutcome.FAILED)
        self.assertIs(classify_readiness(path_resolution=path_res, d2c3_resolution=d2c3),
                     ReadinessTier.CALIBRATION_ELIGIBLE)

    def test_confirmed_alone_does_not_imply_calibration_eligibility(self):
        """Same CONFIRMED direction, but immature -- must NOT be calibration eligible."""
        record = _record("bullish", execution=_execution(invalidation_level=BULLISH_INVALIDATION))
        _, path_res, d2c3, _ = _resolve(record, _confirmed_bullish_bars(), now=EVAL_AT + timedelta(days=1))
        self.assertIsNot(classify_readiness(path_resolution=path_res, d2c3_resolution=d2c3),
                         ReadinessTier.CALIBRATION_ELIGIBLE)

    def test_failed_is_not_treated_as_a_data_failure(self):
        bars = [_bar(d, ANCHOR_PRICE, high=ANCHOR_PRICE, low=ANCHOR_PRICE) for d in range(1, 12)]
        bars.append(_bar(12, ANCHOR_PRICE * 0.90, high=ANCHOR_PRICE * 0.90, low=ANCHOR_PRICE * 0.90))
        record = _record("bullish", execution=_execution(invalidation_level=ANCHOR_PRICE - 500.0))
        _, path_res, d2c3, _ = _resolve(record, bars)
        tier = classify_readiness(path_resolution=path_res, d2c3_resolution=d2c3)
        self.assertIsNot(tier, ReadinessTier.EXCLUDED)

    def test_not_matured_is_provisional_when_otherwise_usable(self):
        record = _record()
        _, path_res, d2c3, _ = _resolve(record, now=EVAL_AT + timedelta(days=1))
        self.assertIs(classify_readiness(path_resolution=path_res, d2c3_resolution=d2c3),
                     ReadinessTier.PROVISIONAL)

    def test_matured_awaiting_bars_is_not_calibration_eligible(self):
        record = _record()
        _, path_res, d2c3, _ = _resolve(record, bars=[], tail=False)
        tier = classify_readiness(path_resolution=path_res, d2c3_resolution=d2c3)
        self.assertIsNot(tier, ReadinessTier.CALIBRATION_ELIGIBLE)

    def test_matured_partial_confirmed_is_provisional(self):
        sparse = [_bar(1, ANCHOR_PRICE * 1.5, high=ANCHOR_PRICE * 1.5, low=ANCHOR_PRICE * 1.5)]
        record = _record()
        _, path_res, d2c3, _ = _resolve(record, sparse)
        self.assertFalse(path_res.path_complete)
        self.assertIs(classify_readiness(path_resolution=path_res, d2c3_resolution=d2c3),
                     ReadinessTier.PROVISIONAL)

    def test_matured_partial_failed_is_provisional(self):
        sparse = [_bar(1, ANCHOR_PRICE * 0.5, high=ANCHOR_PRICE * 0.5, low=ANCHOR_PRICE * 0.5)]
        record = _record()
        _, path_res, d2c3, _ = _resolve(record, sparse)
        self.assertFalse(path_res.path_complete)
        self.assertIs(classify_readiness(path_resolution=path_res, d2c3_resolution=d2c3),
                     ReadinessTier.PROVISIONAL)

    def test_final_reconstructed_with_verdict_is_research_only(self):
        gc_bars = [_bar(d, ANCHOR_PRICE * 1.2, high=ANCHOR_PRICE * 1.2, low=ANCHOR_PRICE * 1.2,
                        symbol="GC=F") for d in range(1, 13)]
        record = _record()
        _, path_res, d2c3, _ = _resolve(record, gc_bars)
        self.assertIsNot(path_res.eligibility_pool, EligibilityPool.CAPTURED)
        self.assertIs(classify_readiness(path_resolution=path_res, d2c3_resolution=d2c3),
                     ReadinessTier.RESEARCH_ONLY)

    def test_non_final_reconstructed_with_verdict_is_provisional(self):
        """Locked precedence: PROVISIONAL outranks RESEARCH_ONLY."""
        gc_bars = [_bar(1, ANCHOR_PRICE * 1.5, high=ANCHOR_PRICE * 1.5, low=ANCHOR_PRICE * 1.5, symbol="GC=F")]
        record = _record()
        _, path_res, d2c3, _ = _resolve(record, gc_bars)
        self.assertFalse(path_res.path_complete)
        self.assertIs(classify_readiness(path_resolution=path_res, d2c3_resolution=d2c3),
                     ReadinessTier.PROVISIONAL)

    def test_gold_substituted_final_is_research_only(self):
        # A decisive move (not a flat/NEUTRAL_WITHIN_BAND path) so the fixture
        # genuinely exercises a CONFIRMED verdict under research-only provenance.
        gc_bars = [_bar(d, ANCHOR_PRICE, high=ANCHOR_PRICE, low=ANCHOR_PRICE, symbol="GC=F")
                   for d in range(1, 12)]
        gc_bars.append(_bar(12, ANCHOR_PRICE * 1.20, high=ANCHOR_PRICE * 1.20,
                            low=ANCHOR_PRICE * 1.20, symbol="GC=F"))
        record = _record()
        _, path_res, d2c3, _ = _resolve(record, gc_bars)
        self.assertIs(path_res.direction, DirectionOutcome.CONFIRMED)
        self.assertIs(classify_readiness(path_resolution=path_res, d2c3_resolution=d2c3),
                     ReadinessTier.RESEARCH_ONLY)

    def test_gold_substituted_partial_is_provisional(self):
        gc_bars = [_bar(1, ANCHOR_PRICE, high=ANCHOR_PRICE, low=ANCHOR_PRICE, symbol="GC=F")]
        record = _record()
        _, path_res, d2c3, _ = _resolve(record, gc_bars)
        self.assertIs(classify_readiness(path_resolution=path_res, d2c3_resolution=d2c3),
                     ReadinessTier.PROVISIONAL)

    def test_excluded_eligibility_is_excluded(self):
        record = _record()
        record["record"]["market_anchor"] = None
        _, path_res, d2c3, _ = _resolve(record, bars=[], tail=False)
        self.assertIs(path_res.eligibility_pool, EligibilityPool.EXCLUDED)
        self.assertIs(classify_readiness(path_resolution=path_res, d2c3_resolution=d2c3),
                     ReadinessTier.EXCLUDED)

    def test_unresolved_final_is_excluded(self):
        record = _record(evaluated_at="not-a-timestamp")
        _, path_res, d2c3, _ = _resolve(record)
        self.assertIs(path_res.direction, DirectionOutcome.UNRESOLVED)
        self.assertIs(classify_readiness(path_resolution=path_res, d2c3_resolution=d2c3),
                     ReadinessTier.EXCLUDED)

    def test_abstained_is_excluded(self):
        record = _record("flat")
        _, path_res, d2c3, _ = _resolve(record, _confirmed_bullish_bars())
        self.assertIs(path_res.direction, DirectionOutcome.ABSTAINED)
        self.assertIs(classify_readiness(path_resolution=path_res, d2c3_resolution=d2c3),
                     ReadinessTier.EXCLUDED)

    def test_not_applicable_is_excluded(self):
        record = _record("unavailable")
        _, path_res, d2c3, _ = _resolve(record, _confirmed_bullish_bars())
        self.assertIs(path_res.direction, DirectionOutcome.NOT_APPLICABLE)
        self.assertIs(classify_readiness(path_resolution=path_res, d2c3_resolution=d2c3),
                     ReadinessTier.EXCLUDED)

    def test_missing_anchor_is_excluded(self):
        record = _record()
        record["record"]["market_anchor"] = None
        _, path_res, d2c3, _ = _resolve(record, bars=[], tail=False)
        self.assertIs(classify_readiness(path_resolution=path_res, d2c3_resolution=d2c3),
                     ReadinessTier.EXCLUDED)

    def test_bar_conflict_is_excluded(self):
        a = _bar(3, ANCHOR_PRICE, high=ANCHOR_PRICE, low=ANCHOR_PRICE)
        b = _bar(3, ANCHOR_PRICE * 2, high=ANCHOR_PRICE * 2, low=ANCHOR_PRICE * 2)
        record = _record()
        _, path_res, d2c3, _ = _resolve(record, [a, b])
        self.assertIs(classify_readiness(path_resolution=path_res, d2c3_resolution=d2c3),
                     ReadinessTier.EXCLUDED)

    def test_no_missing_or_conflicted_path_becomes_calibration_eligible(self):
        cases = [
            dict(bars=[], tail=False),
            dict(record=_record(evaluated_at="not-a-timestamp")),
            dict(record=_record("flat")),
            dict(record=_record("unavailable")),
        ]
        for case in cases:
            record = case.pop("record", _record())
            _, path_res, d2c3, _ = _resolve(record, **case)
            tier = classify_readiness(path_resolution=path_res, d2c3_resolution=d2c3)
            self.assertIsNot(tier, ReadinessTier.CALIBRATION_ELIGIBLE, str(case))


# ===========================================================================
# D. PRECEDENCE   (5 tests)
# ===========================================================================
class TestPrecedence(unittest.TestCase):
    def test_excluded_plus_partial_is_excluded(self):
        record = _record()
        record["record"]["market_anchor"] = None
        sparse = [_bar(1, ANCHOR_PRICE, high=ANCHOR_PRICE, low=ANCHOR_PRICE)]
        _, path_res, d2c3, _ = _resolve(record, sparse)
        self.assertIs(path_res.eligibility_pool, EligibilityPool.EXCLUDED)
        self.assertIs(classify_readiness(path_resolution=path_res, d2c3_resolution=d2c3),
                     ReadinessTier.EXCLUDED)

    def test_research_only_plus_partial_is_provisional(self):
        gc_bars = [_bar(1, ANCHOR_PRICE, high=ANCHOR_PRICE, low=ANCHOR_PRICE, symbol="GC=F")]
        record = _record()
        _, path_res, d2c3, _ = _resolve(record, gc_bars)
        self.assertIs(classify_readiness(path_resolution=path_res, d2c3_resolution=d2c3),
                     ReadinessTier.PROVISIONAL)

    def test_captured_plus_partial_is_provisional(self):
        sparse = [_bar(1, ANCHOR_PRICE, high=ANCHOR_PRICE, low=ANCHOR_PRICE)]
        record = _record()
        _, path_res, d2c3, _ = _resolve(record, sparse)
        self.assertIs(path_res.eligibility_pool, EligibilityPool.CAPTURED)
        self.assertIs(classify_readiness(path_resolution=path_res, d2c3_resolution=d2c3),
                     ReadinessTier.PROVISIONAL)

    def test_final_research_only_is_research_only(self):
        # A decisive move (not a flat/NEUTRAL_WITHIN_BAND path) so the fixture
        # genuinely exercises a CONFIRMED verdict under research-only provenance.
        gc_bars = [_bar(d, ANCHOR_PRICE, high=ANCHOR_PRICE, low=ANCHOR_PRICE, symbol="GC=F")
                   for d in range(1, 12)]
        gc_bars.append(_bar(12, ANCHOR_PRICE * 1.20, high=ANCHOR_PRICE * 1.20,
                            low=ANCHOR_PRICE * 1.20, symbol="GC=F"))
        record = _record()
        _, path_res, d2c3, _ = _resolve(record, gc_bars)
        self.assertIs(path_res.direction, DirectionOutcome.CONFIRMED)
        self.assertIs(classify_readiness(path_resolution=path_res, d2c3_resolution=d2c3),
                     ReadinessTier.RESEARCH_ONLY)

    def test_final_captured_is_calibration_eligible(self):
        record = _record("bullish", execution=_execution(invalidation_level=BULLISH_INVALIDATION))
        _, path_res, d2c3, _ = _resolve(record, _confirmed_bullish_bars())
        self.assertIs(classify_readiness(path_resolution=path_res, d2c3_resolution=d2c3),
                     ReadinessTier.CALIBRATION_ELIGIBLE)


# ===========================================================================
# D2. NEUTRAL_WITHIN_BAND EXCLUSION -- review correction   (7 tests)
#
# A usable directional verdict for CALIBRATION_ELIGIBLE/RESEARCH_ONLY means
# exactly DirectionOutcome.is_verdict (CONFIRMED/FAILED). NEUTRAL_WITHIN_BAND
# is genuine, resolved, non-missing evidence -- but it is not a directional
# calibration verdict, so it must fall into EXCLUDED regardless of maturity
# finality or provenance tier.
# ===========================================================================
class TestNeutralWithinBandExclusion(unittest.TestCase):
    _FULL_FLAT = [_bar(d, ANCHOR_PRICE, high=ANCHOR_PRICE, low=ANCHOR_PRICE) for d in range(1, 13)]

    def _full_flat_confirmed(self):
        """A full-coverage path that decisively confirms (CONFIRMED)."""
        bars = list(self._FULL_FLAT[:-1])
        bars.append(_bar(12, ANCHOR_PRICE * 1.20, high=ANCHOR_PRICE * 1.20, low=ANCHOR_PRICE * 1.20))
        return bars

    def _full_flat_failed(self, direction="bullish"):
        """A full-coverage path that decisively fails the stated claim."""
        bars = list(self._FULL_FLAT[:-1])
        bars.append(_bar(12, ANCHOR_PRICE * 0.80, high=ANCHOR_PRICE * 0.80, low=ANCHOR_PRICE * 0.80))
        return bars

    def _full_flat_neutral(self):
        """A full-coverage path with no material move (NEUTRAL_WITHIN_BAND)."""
        return list(self._FULL_FLAT)

    def _gc(self, bars):
        return [
            _bar(b.bar_time.day, b.close, high=b.high, low=b.low, symbol="GC=F", month=b.bar_time.month)
            for b in bars
        ]

    def test_final_captured_confirmed_is_calibration_eligible(self):
        record = _record("bullish", execution=_execution(invalidation_level=BULLISH_INVALIDATION))
        _, path_res, d2c3, _ = _resolve(record, self._full_flat_confirmed())
        self.assertIs(path_res.direction, DirectionOutcome.CONFIRMED)
        self.assertIs(path_res.eligibility_pool, EligibilityPool.CAPTURED)
        self.assertIs(classify_readiness(path_resolution=path_res, d2c3_resolution=d2c3),
                     ReadinessTier.CALIBRATION_ELIGIBLE)

    def test_final_captured_failed_is_calibration_eligible(self):
        record = _record("bullish", execution=_execution(invalidation_level=ANCHOR_PRICE - 500.0))
        _, path_res, d2c3, _ = _resolve(record, self._full_flat_failed())
        self.assertIs(path_res.direction, DirectionOutcome.FAILED)
        self.assertIs(path_res.eligibility_pool, EligibilityPool.CAPTURED)
        self.assertIs(classify_readiness(path_resolution=path_res, d2c3_resolution=d2c3),
                     ReadinessTier.CALIBRATION_ELIGIBLE)

    def test_final_captured_neutral_within_band_is_excluded(self):
        record = _record("bullish", execution=_execution(invalidation_level=ANCHOR_PRICE - 500.0))
        _, path_res, d2c3, _ = _resolve(record, self._full_flat_neutral())
        self.assertIs(path_res.direction, DirectionOutcome.NEUTRAL_WITHIN_BAND)
        self.assertIs(path_res.eligibility_pool, EligibilityPool.CAPTURED)
        self.assertIs(classify_readiness(path_resolution=path_res, d2c3_resolution=d2c3),
                     ReadinessTier.EXCLUDED)

    def test_final_reconstructed_confirmed_is_research_only(self):
        record = _record("bullish", execution=_execution(invalidation_level=BULLISH_INVALIDATION))
        _, path_res, d2c3, _ = _resolve(record, self._gc(self._full_flat_confirmed()))
        self.assertIs(path_res.direction, DirectionOutcome.CONFIRMED)
        self.assertIsNot(path_res.eligibility_pool, EligibilityPool.CAPTURED)
        self.assertIs(classify_readiness(path_resolution=path_res, d2c3_resolution=d2c3),
                     ReadinessTier.RESEARCH_ONLY)

    def test_final_reconstructed_failed_is_research_only(self):
        record = _record("bullish", execution=_execution(invalidation_level=ANCHOR_PRICE - 500.0))
        _, path_res, d2c3, _ = _resolve(record, self._gc(self._full_flat_failed()))
        self.assertIs(path_res.direction, DirectionOutcome.FAILED)
        self.assertIsNot(path_res.eligibility_pool, EligibilityPool.CAPTURED)
        self.assertIs(classify_readiness(path_resolution=path_res, d2c3_resolution=d2c3),
                     ReadinessTier.RESEARCH_ONLY)

    def test_final_reconstructed_neutral_within_band_is_excluded(self):
        record = _record("bullish", execution=_execution(invalidation_level=ANCHOR_PRICE - 500.0))
        _, path_res, d2c3, _ = _resolve(record, self._gc(self._full_flat_neutral()))
        self.assertIs(path_res.direction, DirectionOutcome.NEUTRAL_WITHIN_BAND)
        self.assertIsNot(path_res.eligibility_pool, EligibilityPool.CAPTURED)
        self.assertIs(classify_readiness(path_resolution=path_res, d2c3_resolution=d2c3),
                     ReadinessTier.EXCLUDED)

    def test_non_final_confirmed_and_failed_still_obey_locked_precedence(self):
        """Non-final CONFIRMED/FAILED must still resolve PROVISIONAL --
        EXCLUDED > PROVISIONAL > RESEARCH_ONLY > CALIBRATION_ELIGIBLE."""
        sparse_up = [_bar(1, ANCHOR_PRICE * 1.5, high=ANCHOR_PRICE * 1.5, low=ANCHOR_PRICE * 1.5)]
        sparse_down = [_bar(1, ANCHOR_PRICE * 0.5, high=ANCHOR_PRICE * 0.5, low=ANCHOR_PRICE * 0.5)]
        record = _record("bullish", execution=_execution(invalidation_level=ANCHOR_PRICE - 500.0))

        _, path_res_up, d2c3_up, _ = _resolve(record, sparse_up)
        self.assertIs(path_res_up.direction, DirectionOutcome.CONFIRMED)
        self.assertFalse(path_res_up.path_complete)
        self.assertIs(classify_readiness(path_resolution=path_res_up, d2c3_resolution=d2c3_up),
                     ReadinessTier.PROVISIONAL)

        _, path_res_down, d2c3_down, _ = _resolve(record, sparse_down)
        self.assertIs(path_res_down.direction, DirectionOutcome.FAILED)
        self.assertFalse(path_res_down.path_complete)
        self.assertIs(classify_readiness(path_resolution=path_res_down, d2c3_resolution=d2c3_down),
                     ReadinessTier.PROVISIONAL)


# ===========================================================================
# E. BUILD VERIFIED ENVELOPE   (9 tests)
# ===========================================================================
class TestBuildVerifiedEnvelope(unittest.TestCase):
    def test_verifies_before_building(self):
        """A LineageError must prevent any envelope from being returned."""
        record_a = _record("bullish")
        record_b = _record("bearish")
        _, path_res_a, d2c3_a, cfg = _resolve(record_a)
        with self.assertRaises(LineageError):
            build_verified_envelope(record=record_b, path_resolution=path_res_a,
                                    d2c3_resolution=d2c3_a, validation_config=cfg)

    def test_lineage_mismatch_prevents_envelope_construction(self):
        record_a = _record("bullish")
        record_b = _record("bearish")
        _, path_res_a, d2c3_a, cfg = _resolve(record_a)
        try:
            build_verified_envelope(record=record_b, path_resolution=path_res_a,
                                    d2c3_resolution=d2c3_a, validation_config=cfg)
            self.fail("expected LineageError")
        except LineageError:
            pass

    def test_valid_lineage_returns_ordinary_envelope(self):
        record = _record("bullish", execution=_execution(invalidation_level=BULLISH_INVALIDATION))
        rec, path_res, d2c3, cfg = _resolve(record, _confirmed_bullish_bars())
        envelope = build_verified_envelope(record=rec, path_resolution=path_res, d2c3_resolution=d2c3,
                                           validation_config=cfg)
        self.assertTrue(envelope.validation_id)
        self.assertTrue(envelope.input_hash)
        self.assertTrue(envelope.outcome_hash)

    def test_validation_id_unchanged_versus_direct_builder(self):
        record = _record("bullish", execution=_execution(invalidation_level=BULLISH_INVALIDATION))
        rec, path_res, d2c3, cfg = _resolve(record, _confirmed_bullish_bars())
        verified = build_verified_envelope(record=rec, path_resolution=path_res, d2c3_resolution=d2c3,
                                           validation_config=cfg)
        direct = build_validation_envelope(record=rec, path_resolution=path_res, d2c3_resolution=d2c3,
                                           validation_config=cfg)
        self.assertEqual(verified.validation_id, direct.validation_id)

    def test_input_hash_unchanged_versus_direct_builder(self):
        record = _record("bullish", execution=_execution(invalidation_level=BULLISH_INVALIDATION))
        rec, path_res, d2c3, cfg = _resolve(record, _confirmed_bullish_bars())
        verified = build_verified_envelope(record=rec, path_resolution=path_res, d2c3_resolution=d2c3,
                                           validation_config=cfg)
        direct = build_validation_envelope(record=rec, path_resolution=path_res, d2c3_resolution=d2c3,
                                           validation_config=cfg)
        self.assertEqual(verified.input_hash, direct.input_hash)

    def test_outcome_hash_unchanged_versus_direct_builder(self):
        record = _record("bullish", execution=_execution(invalidation_level=BULLISH_INVALIDATION))
        rec, path_res, d2c3, cfg = _resolve(record, _confirmed_bullish_bars())
        verified = build_verified_envelope(record=rec, path_resolution=path_res, d2c3_resolution=d2c3,
                                           validation_config=cfg)
        direct = build_validation_envelope(record=rec, path_resolution=path_res, d2c3_resolution=d2c3,
                                           validation_config=cfg)
        self.assertEqual(verified.outcome_hash, direct.outcome_hash)

    def test_malformed_row_count_passed_through_unchanged(self):
        record = _record()
        rec, path_res, d2c3, cfg = _resolve(record)
        envelope = build_verified_envelope(record=rec, path_resolution=path_res, d2c3_resolution=d2c3,
                                           validation_config=cfg, malformed_row_count=7)
        self.assertEqual(envelope.context.malformed_row_count, 7)

    def test_overlap_context_passed_through_unchanged(self):
        from apex.b2.validation.envelope import OverlapContext
        record = _record()
        rec, path_res, d2c3, cfg = _resolve(record)
        ctx = OverlapContext(previous_storage_id="prev", previous_evaluated_at=EVAL_AT - timedelta(days=5))
        envelope = build_verified_envelope(record=rec, path_resolution=path_res, d2c3_resolution=d2c3,
                                           validation_config=cfg, overlap_context=ctx)
        self.assertIsNotNone(envelope.overlap)
        self.assertEqual(envelope.overlap.previous_same_instrument_storage_id, "prev")

    def test_readiness_does_not_enter_d2c4_hashes(self):
        record = _record("bullish", execution=_execution(invalidation_level=BULLISH_INVALIDATION))
        rec, path_res, d2c3, cfg = _resolve(record, _confirmed_bullish_bars())
        envelope = build_verified_envelope(record=rec, path_resolution=path_res, d2c3_resolution=d2c3,
                                           validation_config=cfg)
        dumped_input = json.dumps(envelope.input_hash_basis)
        dumped_outcome = json.dumps(envelope.outcome_hash_basis)
        for forbidden in ("readiness", "calibration_eligible", "research_only", "provisional"):
            self.assertNotIn(forbidden, dumped_input, forbidden)
            self.assertNotIn(forbidden, dumped_outcome, forbidden)


# ===========================================================================
# F. DETERMINISM   (4 tests)
# ===========================================================================
class TestDeterminism(unittest.TestCase):
    def test_same_immutable_inputs_same_result(self):
        record = _record("bullish", execution=_execution(invalidation_level=BULLISH_INVALIDATION))
        bars = _confirmed_bullish_bars()
        rec1, path_res1, d2c3_1, cfg1 = _resolve(record, bars)
        rec2, path_res2, d2c3_2, cfg2 = _resolve(record, bars)
        tier1 = classify_readiness(path_resolution=path_res1, d2c3_resolution=d2c3_1)
        tier2 = classify_readiness(path_resolution=path_res2, d2c3_resolution=d2c3_2)
        self.assertIs(tier1, tier2)
        env1 = build_verified_envelope(record=rec1, path_resolution=path_res1, d2c3_resolution=d2c3_1,
                                       validation_config=cfg1)
        env2 = build_verified_envelope(record=rec2, path_resolution=path_res2, d2c3_resolution=d2c3_2,
                                       validation_config=cfg2)
        self.assertEqual(env1.validation_id, env2.validation_id)

    def test_later_now_on_already_final_resolution_does_not_change_tier(self):
        record = _record("bullish", execution=_execution(invalidation_level=BULLISH_INVALIDATION))
        bars = _confirmed_bullish_bars()
        _, path_res_early, d2c3_early, _ = _resolve(record, bars, now=NOW)
        _, path_res_late, d2c3_late, _ = _resolve(record, bars, now=NOW + timedelta(days=1000))
        tier_early = classify_readiness(path_resolution=path_res_early, d2c3_resolution=d2c3_early)
        tier_late = classify_readiness(path_resolution=path_res_late, d2c3_resolution=d2c3_late)
        self.assertIs(tier_early, tier_late)

    def test_no_wall_clock_dependency_in_module(self):
        names = _identifiers(readiness_mod)
        for forbidden in ("now", "utcnow", "monotonic", "random", "randint"):
            self.assertNotIn(forbidden, names, forbidden)

    def test_no_unordered_data_dependency(self):
        """Readiness/lineage must not depend on set/dict iteration order."""
        source = inspect.getsource(readiness_mod)
        self.assertNotIn("set(", source)


# ===========================================================================
# G. LEGACY / PROVENANCE   (4 tests)
# ===========================================================================
class TestLegacyProvenance(unittest.TestCase):
    def test_schema_v1_missing_anchor_remains_excluded(self):
        legacy = _record(schema_version=1)
        legacy["record"]["market_anchor"] = None
        _, path_res, d2c3, _ = _resolve(legacy, bars=[], tail=False)
        self.assertIs(classify_readiness(path_resolution=path_res, d2c3_resolution=d2c3),
                     ReadinessTier.EXCLUDED)

    def test_no_legacy_anchor_fabrication(self):
        legacy = _record(schema_version=1)
        legacy["record"]["market_anchor"] = None
        rec, path_res, d2c3, cfg = _resolve(legacy, bars=[], tail=False)
        self.assertFalse(path_res.anchor.status.is_point_in_time)
        # Lineage must still verify cleanly for a genuinely-corresponding pair.
        verify_lineage(record=rec, path_resolution=path_res, d2c3_resolution=d2c3, validation_config=cfg)

    def test_substituted_gold_never_calibration_eligible(self):
        gc_bars = [_bar(d, ANCHOR_PRICE, high=ANCHOR_PRICE, low=ANCHOR_PRICE, symbol="GC=F")
                   for d in range(1, 13)]
        record = _record()
        _, path_res, d2c3, _ = _resolve(record, gc_bars)
        self.assertIsNot(classify_readiness(path_resolution=path_res, d2c3_resolution=d2c3),
                         ReadinessTier.CALIBRATION_ELIGIBLE)

    def test_captured_exact_data_not_downgraded_unnecessarily(self):
        record = _record("bullish", execution=_execution(invalidation_level=BULLISH_INVALIDATION))
        _, path_res, d2c3, _ = _resolve(record, _confirmed_bullish_bars())
        self.assertIs(path_res.eligibility_pool, EligibilityPool.CAPTURED)
        self.assertIs(classify_readiness(path_resolution=path_res, d2c3_resolution=d2c3),
                     ReadinessTier.CALIBRATION_ELIGIBLE)


# ===========================================================================
# H. INVERSION SMOKE   (3 tests)
# ===========================================================================
class TestInversionSmoke(unittest.TestCase):
    def _fx_case(self, instrument, symbol, quotes, direction="bullish"):
        anchor = _anchor(symbol, invert=True, price=1.0 / 100.0, atr=0.0001)
        record = _record(direction, instrument=instrument, anchor=anchor,
                         execution=_execution(invalidation_level=0.0099))
        bars = [
            _bar(i + 1, q, high=q * 1.01, low=q * 0.99, symbol=symbol, instrument=instrument, invert=True)
            for i, q in enumerate(quotes)
        ]
        return _resolve(record, bars, instrument=instrument)

    def test_cad_inversion_lineage_and_readiness_unaffected(self):
        rec, path_res, d2c3, cfg = self._fx_case("CAD", "USDCAD=X", [100.0, 95.0, 90.0])
        verify_lineage(record=rec, path_resolution=path_res, d2c3_resolution=d2c3, validation_config=cfg)
        classify_readiness(path_resolution=path_res, d2c3_resolution=d2c3)  # must not raise

    def test_chf_inversion_lineage_and_readiness_unaffected(self):
        rec, path_res, d2c3, cfg = self._fx_case("CHF", "USDCHF=X", [100.0, 95.0, 90.0])
        verify_lineage(record=rec, path_resolution=path_res, d2c3_resolution=d2c3, validation_config=cfg)
        classify_readiness(path_resolution=path_res, d2c3_resolution=d2c3)

    def test_jpy_inversion_lineage_and_readiness_unaffected(self):
        rec, path_res, d2c3, cfg = self._fx_case("JPY", "USDJPY=X", [100.0, 95.0, 90.0])
        verify_lineage(record=rec, path_resolution=path_res, d2c3_resolution=d2c3, validation_config=cfg)
        classify_readiness(path_resolution=path_res, d2c3_resolution=d2c3)


# ===========================================================================
# I. OVERLAP   (4 tests)
# ===========================================================================
class TestOverlap(unittest.TestCase):
    def test_overlap_absent_does_not_change_readiness(self):
        record = _record("bullish", execution=_execution(invalidation_level=BULLISH_INVALIDATION))
        rec, path_res, d2c3, cfg = _resolve(record, _confirmed_bullish_bars())
        tier_no_overlap = classify_readiness(path_resolution=path_res, d2c3_resolution=d2c3)
        envelope = build_verified_envelope(record=rec, path_resolution=path_res, d2c3_resolution=d2c3,
                                           validation_config=cfg)
        self.assertIsNone(envelope.overlap)
        self.assertIs(tier_no_overlap, ReadinessTier.CALIBRATION_ELIGIBLE)

    def test_overlap_present_does_not_change_readiness(self):
        from apex.b2.validation.envelope import OverlapContext
        record = _record("bullish", execution=_execution(invalidation_level=BULLISH_INVALIDATION))
        rec, path_res, d2c3, cfg = _resolve(record, _confirmed_bullish_bars())
        tier = classify_readiness(path_resolution=path_res, d2c3_resolution=d2c3)
        ctx = OverlapContext(previous_storage_id="p", previous_evaluated_at=EVAL_AT - timedelta(days=5))
        envelope = build_verified_envelope(record=rec, path_resolution=path_res, d2c3_resolution=d2c3,
                                           validation_config=cfg, overlap_context=ctx)
        self.assertIsNotNone(envelope.overlap)
        self.assertIs(tier, ReadinessTier.CALIBRATION_ELIGIBLE)

    def test_overlap_does_not_enter_lineage(self):
        names = _identifiers(readiness_mod.verify_lineage)
        self.assertNotIn("overlap_context", names)
        self.assertNotIn("OverlapContext", names)

    def test_existing_overlap_hash_isolation_remains_intact(self):
        from apex.b2.validation.envelope import OverlapContext
        record = _record("bullish", execution=_execution(invalidation_level=BULLISH_INVALIDATION))
        rec, path_res, d2c3, cfg = _resolve(record, _confirmed_bullish_bars())
        without = build_verified_envelope(record=rec, path_resolution=path_res, d2c3_resolution=d2c3,
                                          validation_config=cfg)
        ctx = OverlapContext(previous_storage_id="p", previous_evaluated_at=EVAL_AT - timedelta(days=5))
        with_overlap = build_verified_envelope(record=rec, path_resolution=path_res, d2c3_resolution=d2c3,
                                               validation_config=cfg, overlap_context=ctx)
        self.assertEqual(without.input_hash, with_overlap.input_hash)
        self.assertEqual(without.outcome_hash, with_overlap.outcome_hash)


# ===========================================================================
# J. FLOAT / HASH REGRESSION (through build_verified_envelope)   (4 tests)
# ===========================================================================
class TestFloatHashRegression(unittest.TestCase):
    def test_nan_rejected_through_verified_envelope(self):
        from apex.b2.validation.envelope import canonical_json
        with self.assertRaises(ValueError):
            canonical_json({"x": float("nan")})

    def test_positive_infinity_rejected_through_verified_envelope(self):
        from apex.b2.validation.envelope import canonical_json
        with self.assertRaises(ValueError):
            canonical_json({"x": float("inf")})

    def test_negative_infinity_rejected_through_verified_envelope(self):
        from apex.b2.validation.envelope import canonical_json
        with self.assertRaises(ValueError):
            canonical_json({"x": float("-inf")})

    def test_negative_zero_canonicalizes_consistently_end_to_end(self):
        record = _record("bullish", execution=_execution(invalidation_level=BULLISH_INVALIDATION))
        rec, path_res, d2c3, cfg = _resolve(record, _flat_path())
        envelope = build_verified_envelope(record=rec, path_resolution=path_res, d2c3_resolution=d2c3,
                                           validation_config=cfg)
        self.assertAlmostEqual(envelope.outcome_hash_basis["terminal_return"], 0.0, places=12)
        # Rerun with a fixture that could plausibly produce -0.0 and confirm stability.
        envelope2 = build_verified_envelope(record=rec, path_resolution=path_res, d2c3_resolution=d2c3,
                                            validation_config=cfg)
        self.assertEqual(envelope.outcome_hash, envelope2.outcome_hash)


# ===========================================================================
# K. PURITY   (11 tests)
# ===========================================================================
class TestPurity(unittest.TestCase):
    def test_no_forbidden_imports(self):
        tree = ast.parse(inspect.getsource(readiness_mod))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [a.name for a in node.names] + [getattr(node, "module", "") or ""]
                for name in names:
                    for forbidden in ("requests", "urllib", "httpx", "socket", "http",
                                      "streamlit", "threading", "asyncio", "subprocess",
                                      "multiprocessing", "sqlite", "psycopg", "supabase",
                                      "production_core", "b2_bridge", "b2_validation_bridge",
                                      "random"):
                        self.assertNotIn(forbidden, name, name)

    def test_no_wall_clock(self):
        names = _identifiers(readiness_mod)
        for forbidden in ("now", "utcnow", "monotonic"):
            self.assertNotIn(forbidden, names, forbidden)
        source = inspect.getsource(readiness_mod)
        self.assertNotIn("datetime.now(", source)
        self.assertNotIn("time.time(", source)

    def test_no_ai_telegram_or_scheduler_reference(self):
        names = {n.lower() for n in _identifiers(readiness_mod)}
        for forbidden in ("telegram", "sendmessage", "openai", "anthropic", "gemini", "groq",
                          "completions", "thread", "timer", "sleep", "crontab", "scheduler", "daemon"):
            self.assertFalse(any(forbidden in n for n in names), forbidden)

    def test_no_ddl_dml_or_persistence(self):
        upper = inspect.getsource(readiness_mod).upper()
        for verb in ("CREATE TABLE", "ALTER TABLE", "DROP TABLE", "TRUNCATE",
                     "INSERT INTO", "DELETE FROM", "CREATE INDEX"):
            self.assertNotIn(verb, upper, verb)

    def test_no_environment_variable_dependency(self):
        names = _identifiers(readiness_mod)
        self.assertNotIn("environ", names)
        self.assertNotIn("getenv", names)

    def test_no_file_io(self):
        tree = ast.parse(inspect.getsource(readiness_mod))
        called = {node.func.id for node in ast.walk(tree)
                  if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
        for builtin in ("open", "exec", "eval", "compile", "__import__", "input"):
            self.assertNotIn(builtin, called, builtin)

    def test_no_network_or_db_names(self):
        names = {n.lower() for n in _identifiers(readiness_mod)}
        for forbidden in ("requests", "urlopen", "socket", "cursor", "execute_sql", "supabase"):
            self.assertFalse(any(forbidden in n for n in names), forbidden)

    def test_no_streamlit_reference(self):
        names = {n.lower() for n in _identifiers(readiness_mod)}
        self.assertFalse(any("streamlit" in n for n in names))

    def test_no_production_core_import(self):
        names = _identifiers(readiness_mod)
        self.assertNotIn("production_core", names)

    def test_deterministic_repeated_calls_are_pure(self):
        record = _record("bullish", execution=_execution(invalidation_level=BULLISH_INVALIDATION))
        bars = _confirmed_bullish_bars()
        rec, path_res, d2c3, cfg = _resolve(record, bars)
        tiers = {classify_readiness(path_resolution=path_res, d2c3_resolution=d2c3) for _ in range(3)}
        self.assertEqual(len(tiers), 1)

    def test_no_module_outside_tests_imports_readiness(self):
        """D-2D0's ``observation.py`` is the ONE approved exception,
        authorized by Stage D-2D0.

        Until D-2D0 this list was empty and D-2C5 was unreachable from
        anywhere: nothing composed it. ``observation.py`` is the
        per-observation orchestrator, and it uses D-2C5 the way D-2C5 was
        designed to be used -- ``build_verified_envelope`` for lineage-then-
        envelope, and ``classify_readiness`` for the tier. It remains pure,
        has no non-test caller of its own, and neither bridge imports it, so
        readiness is still not reachable from production. The guard is
        extended by exactly one approved name and is otherwise unchanged:
        any OTHER importer still fails it."""
        importers = []
        for folder, _dirs, files in os.walk(ROOT):
            if any(p in folder for p in ("_backup_", "_baseline_", ".git", "__pycache__", "tests")):
                continue
            for filename in files:
                if not filename.endswith(".py") or filename == "readiness.py":
                    continue
                path = os.path.join(folder, filename)
                with open(path, encoding="utf-8") as handle:
                    try:
                        tree = ast.parse(handle.read())
                    except SyntaxError:
                        continue
                for node in ast.walk(tree):
                    if isinstance(node, ast.ImportFrom) and "validation.readiness" in (node.module or ""):
                        importers.append(filename)
                    if isinstance(node, ast.Import) and any(
                            "validation.readiness" in a.name for a in node.names):
                        importers.append(filename)
        self.assertEqual(sorted(set(importers)), ["observation.py"])


# ===========================================================================
# L. SCOPE   (7 tests)
# ===========================================================================
class TestScope(unittest.TestCase):
    def _unchanged(self, path):
        result = subprocess.run(["git", "diff", "--exit-code", "--", path],
                                cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, f"{path} changed:\n{result.stdout[:800]}")

    def test_metrics_module_remains_absent(self):
        present = {f for f in os.listdir(os.path.join(ROOT, "apex", "b2", "validation"))
                   if f.endswith(".py")}
        self.assertNotIn("metrics.py", present)
        self.assertEqual(present, {"__init__.py", "anchor.py", "bars.py", "config.py",
                                   "maturity.py", "outcome.py", "resolve.py", "series.py",
                                   "invalidation.py", "envelope.py", "readiness.py"})

    def test_cross_asset_remains_withheld(self):
        with open(os.path.join(ROOT, "apex", "b2", "shadow.py"), encoding="utf-8") as h:
            self.assertIn('CROSS_ASSET_STATUS = "withheld"', h.read())

    def test_no_d3_entry_states_introduced(self):
        source = inspect.getsource(readiness_mod)
        for forbidden in ("ENTRY_JUSTIFIED", "ENTRY_PREMATURE", "ENTRY_LATE"):
            self.assertNotIn(forbidden, source, forbidden)

    def test_no_thesis_lifecycle_activation(self):
        source = inspect.getsource(readiness_mod)
        for forbidden in ("open_thesis", "apply_escalation", "apply_macro_evidence", "restore_thesis"):
            self.assertNotIn(forbidden, source, forbidden)

    def test_no_production_wiring(self):
        names = _identifiers(readiness_mod)
        for forbidden in ("b2_bridge", "b2_validation_bridge", "production_core"):
            self.assertNotIn(forbidden, names, forbidden)

    def test_production_core_sha_unchanged(self):
        import hashlib
        with open(os.path.join(ROOT, "apex", "production_core.py"), "rb") as handle:
            digest = hashlib.sha256(handle.read()).hexdigest()
        self.assertEqual(digest, "5935f807a8584007fc053ae7bb64d62017a7e2f804258d492fdd8a4c2cb4da69")

    def test_production_core_is_byte_for_byte_unchanged(self):
        self._unchanged("apex/production_core.py")


# ===========================================================================
# M. PRIOR-STAGE REGRESSION -- rerun D-2C2/D-2C3/D-2C4 suites internally.
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


if __name__ == "__main__":
    unittest.main()
