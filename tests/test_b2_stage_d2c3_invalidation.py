"""Stage D-2C3: setup invalidation and execution quality.

Covers the two new outcome axes ``apex.b2.validation.invalidation`` adds on
top of D-2C2's direction/path resolution: whether the captured technical
setup was touched (``SetupInvalidation``) and whether a documented execution
deferral was later proven correct or costly (``ExecutionOutcome``). Macro
thesis invalidation is asserted to be unconditionally ``NOT_ASSESSABLE`` --
this stage does not and must not resolve it from anything.

Imports ``apex.production_core`` for the safety assertions, so durable-state
isolation is installed first. Nothing here performs I/O.
"""
from __future__ import annotations

import ast
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
from apex.b2.validation import invalidation as invalidation_mod
from apex.b2.validation.bars import GRANULARITY_1D, MarketBar
from apex.b2.validation.invalidation import (
    D2C3Resolution,
    ExecutionQualityResolution,
    InvalidationMeasures,
    SetupInvalidationResolution,
    resolve_setup_and_execution,
)
from apex.b2.validation.outcome import (
    DirectionOutcome,
    ExecutionOutcome,
    SetupInvalidation,
    ThesisInvalidation,
)
from apex.b2.validation.resolve import resolve_direction_and_path

import tests.test_b2_stage_d2c_resolution as d2c2_tests

UTC = timezone.utc
EVAL_AT = datetime(2026, 8, 30, 22, 4, 43, 893828, tzinfo=UTC)
NOW = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)
ANCHOR_PRICE = 3330.0
ANCHOR_ATR = 12.0
ANCHOR_VOL = 0.0012
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: Symmetric invalidation levels around ANCHOR_PRICE, used across most fixtures.
BULLISH_INVALIDATION = ANCHOR_PRICE - 20.0   # below current -- implies long
BEARISH_INVALIDATION = ANCHOR_PRICE + 20.0   # above current -- implies short


# ===========================================================================
# Fixtures
# ===========================================================================

def _anchor(symbol="XAUUSD=X", *, invert=False, atr=ANCHOR_ATR,
            price=ANCHOR_PRICE, vol=ANCHOR_VOL, requested=None):
    return {
        "analysis_price": price, "last_price": price, "symbol": symbol,
        "symbol_requested": requested or symbol, "symbol_fallback_used": False,
        "invert": invert, "market_ts": 1, "market_ts_iso": "",
        "volatility_scale": vol, "atr": atr, "atr_ratio": 1.05,
        "volatility_regime": "normal", "price_source": "yahoo_5m_tactical",
        "granularity": "5m", "anchor_status": "anchor_captured",
    }


def _execution(*, invalidation_level=None, entry_zone=None, current_price=ANCHOR_PRICE,
               blocked=False, block_reason="", invalidation_defined=None):
    defined = (
        invalidation_defined if invalidation_defined is not None
        else invalidation_level is not None
    )
    try:
        distance = (
            abs(current_price - invalidation_level)
            if invalidation_level is not None and current_price is not None else None
        )
    except TypeError:
        distance = None  # malformed invalidation_level (e.g. a string) -- fixture mirrors reality
    return {
        "invalidation_defined": defined,
        "invalidation_level": invalidation_level,
        "entry_zone_low": entry_zone[0] if entry_zone else None,
        "entry_zone_high": entry_zone[1] if entry_zone else None,
        "current_price": current_price,
        "invalidation_distance": distance,
        "invalidation_distance_atr": None,
        "room_to_opposing_atr": None,
        "asymmetry_ratio": None,
        "volatility_regime": "normal",
        "in_zone": False,
        "extended": False,
        "execution_confidence": "HIGH",
        "blocked": blocked,
        "block_reason": block_reason,
        "notes": [],
    }


def _gate(*, gate="event_risk", triggered=True, action="veto_execution",
          reason="test veto", applies_to_open_position=False):
    return {
        "gate": gate, "triggered": triggered, "action": action, "reason": reason,
        "max_confidence": "LOW", "event_risk_state": "critical",
        "applies_to_open_position": applies_to_open_position,
    }


def _record(direction="bullish", *, instrument="Gold", anchor=None,
            horizon="tactical", evaluated_at=EVAL_AT, storage_id="s1",
            record_id="r1", claim=True, execution=None, gates_triggered=()):
    payload = {
        "schema_version": 2, "record_id": record_id, "instrument": instrument,
        "horizon": horizon,
        "evaluated_at": evaluated_at if isinstance(evaluated_at, str)
        else evaluated_at.isoformat(),
        "market_anchor": _anchor() if anchor is None else anchor,
        "claim": ({"direction": direction, "horizon": horizon} if claim else None),
        "execution": execution,
        "gates_triggered": list(gates_triggered),
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
    return [_bar(d, price, high=price, low=price, **kw) for d in days]


def _capture_tail(bars):
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


def _run(record=None, bars=None, *, now=NOW, instrument="Gold", tail=True):
    """Resolve D-2C2 then D-2C3 for one fixture. Returns (path_resolution, d2c3)."""
    rec = record if record is not None else _record()
    supplied = bars if bars is not None else _flat_path()
    if tail:
        supplied = list(supplied) + _capture_tail(supplied)
    path_resolution = resolve_direction_and_path(
        record=rec, bars=supplied, now=now,
        convention=b2_bridge.symbol_convention(instrument),
    )
    result = resolve_setup_and_execution(record=rec, path_resolution=path_resolution)
    return path_resolution, result


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
# A. SETUP INVALIDATION -- BULLISH / BEARISH / BOUNDARY   (tests 1-5)
# ===========================================================================
class TestSetupInvalidationDirectional(unittest.TestCase):
    def test_bullish_invalidation_touch(self):
        bars = [_bar(1, ANCHOR_PRICE, high=ANCHOR_PRICE, low=ANCHOR_PRICE),
                _bar(2, ANCHOR_PRICE, high=ANCHOR_PRICE, low=3300.0)]
        record = _record("bullish", execution=_execution(
            invalidation_level=BULLISH_INVALIDATION, current_price=ANCHOR_PRICE))
        _, result = _run(record, bars)
        self.assertIs(result.setup.state, SetupInvalidation.INVALIDATED)
        self.assertTrue(result.setup.measures.touched)
        self.assertEqual(result.setup.measures.bars_to_touch, 1)

    def test_bullish_no_touch_complete_path(self):
        bars = [_bar(d, ANCHOR_PRICE, high=ANCHOR_PRICE, low=ANCHOR_PRICE)
                for d in range(1, 13)]
        record = _record("bullish", execution=_execution(
            invalidation_level=BULLISH_INVALIDATION, current_price=ANCHOR_PRICE))
        path_resolution, result = _run(record, bars)
        self.assertTrue(path_resolution.path_complete)
        self.assertIs(result.setup.state, SetupInvalidation.NOT_INVALIDATED)
        self.assertFalse(result.setup.measures.touched)
        self.assertIsNone(result.setup.measures.bars_to_touch)

    def test_bearish_invalidation_touch(self):
        bars = [_bar(1, ANCHOR_PRICE, high=ANCHOR_PRICE, low=ANCHOR_PRICE),
                _bar(2, ANCHOR_PRICE, high=3360.0, low=ANCHOR_PRICE)]
        record = _record("bearish", execution=_execution(
            invalidation_level=BEARISH_INVALIDATION, current_price=ANCHOR_PRICE))
        _, result = _run(record, bars)
        self.assertIs(result.setup.state, SetupInvalidation.INVALIDATED)
        self.assertEqual(result.setup.measures.bars_to_touch, 1)

    def test_bearish_no_touch_complete_path(self):
        bars = [_bar(d, ANCHOR_PRICE, high=ANCHOR_PRICE, low=ANCHOR_PRICE)
                for d in range(1, 13)]
        record = _record("bearish", execution=_execution(
            invalidation_level=BEARISH_INVALIDATION, current_price=ANCHOR_PRICE))
        path_resolution, result = _run(record, bars)
        self.assertTrue(path_resolution.path_complete)
        self.assertIs(result.setup.state, SetupInvalidation.NOT_INVALIDATED)

    def test_exact_boundary_touch_counts_as_invalidated(self):
        """Touching the level EXACTLY (not beyond it) still counts."""
        bars = [_bar(1, ANCHOR_PRICE, high=ANCHOR_PRICE, low=BULLISH_INVALIDATION)]
        record = _record("bullish", execution=_execution(
            invalidation_level=BULLISH_INVALIDATION, current_price=ANCHOR_PRICE))
        _, result = _run(record, bars)
        self.assertIs(result.setup.state, SetupInvalidation.INVALIDATED)
        self.assertEqual(result.setup.measures.bars_to_touch, 0)


# ===========================================================================
# B. AXIS INDEPENDENCE   (tests 6-7)
# ===========================================================================
class TestAxisIndependence(unittest.TestCase):
    def test_invalidation_before_eventual_direction_confirmed(self):
        """Setup INVALIDATED and Direction CONFIRMED must coexist without contradiction."""
        bars = [_bar(1, ANCHOR_PRICE, high=ANCHOR_PRICE, low=3300.0)]  # touch first
        bars += [_bar(d, ANCHOR_PRICE, high=ANCHOR_PRICE, low=ANCHOR_PRICE)
                 for d in range(2, 12)]
        bars.append(_bar(12, ANCHOR_PRICE * 1.20, high=ANCHOR_PRICE * 1.20,
                          low=ANCHOR_PRICE * 1.20))  # ends decisively higher
        record = _record("bullish", execution=_execution(
            invalidation_level=BULLISH_INVALIDATION, current_price=ANCHOR_PRICE))
        path_resolution, result = _run(record, bars)
        self.assertIs(path_resolution.direction, DirectionOutcome.CONFIRMED)
        self.assertIs(result.setup.state, SetupInvalidation.INVALIDATED)

    def test_direction_failed_without_touch(self):
        bars = [_bar(d, ANCHOR_PRICE, high=ANCHOR_PRICE, low=ANCHOR_PRICE)
                for d in range(1, 12)]
        bars.append(_bar(12, ANCHOR_PRICE * 0.90, high=ANCHOR_PRICE * 0.90,
                          low=ANCHOR_PRICE * 0.90))  # decisive drop, but far above invalidation
        record = _record("bullish", execution=_execution(
            invalidation_level=ANCHOR_PRICE - 500.0, current_price=ANCHOR_PRICE))
        path_resolution, result = _run(record, bars)
        self.assertIs(path_resolution.direction, DirectionOutcome.FAILED)
        self.assertIs(result.setup.state, SetupInvalidation.NOT_INVALIDATED)


# ===========================================================================
# C. EXECUTION QUALITY -- DEFERRAL PRECEDENCE   (tests 8-12)
# ===========================================================================
class TestDeferralPrecedence(unittest.TestCase):
    def test_deferral_correct_from_invalidation(self):
        bars = [_bar(1, ANCHOR_PRICE, high=ANCHOR_PRICE, low=3300.0)]
        bars += [_bar(d, ANCHOR_PRICE, high=ANCHOR_PRICE, low=ANCHOR_PRICE)
                 for d in range(2, 12)]
        bars.append(_bar(12, ANCHOR_PRICE * 1.20, high=ANCHOR_PRICE * 1.20,
                          low=ANCHOR_PRICE * 1.20))
        record = _record("bullish", execution=_execution(
            invalidation_level=BULLISH_INVALIDATION, current_price=ANCHOR_PRICE,
            blocked=True, block_reason="event risk"),
            gates_triggered=(_gate(applies_to_open_position=False),))
        path_resolution, result = _run(record, bars)
        self.assertIs(path_resolution.direction, DirectionOutcome.CONFIRMED)
        self.assertIs(result.setup.state, SetupInvalidation.INVALIDATED)
        self.assertIs(result.execution.state, ExecutionOutcome.DEFERRAL_CORRECT)

    def test_deferral_correct_from_failed_direction(self):
        bars = [_bar(d, ANCHOR_PRICE, high=ANCHOR_PRICE, low=ANCHOR_PRICE)
                for d in range(1, 12)]
        bars.append(_bar(12, ANCHOR_PRICE * 0.90, high=ANCHOR_PRICE * 0.90,
                          low=ANCHOR_PRICE * 0.90))
        record = _record("bullish", execution=_execution(
            invalidation_level=ANCHOR_PRICE - 500.0, current_price=ANCHOR_PRICE,
            blocked=True, block_reason="event risk"),
            gates_triggered=(_gate(applies_to_open_position=False),))
        _, result = _run(record, bars)
        self.assertIs(result.execution.state, ExecutionOutcome.DEFERRAL_CORRECT)
        self.assertEqual(result.execution.depends_on, ("direction", "setup_invalidation"))

    def test_deferral_costly_from_confirmed_direction_and_surviving_setup(self):
        bars = [_bar(d, ANCHOR_PRICE, high=ANCHOR_PRICE, low=ANCHOR_PRICE)
                for d in range(1, 12)]
        bars.append(_bar(12, ANCHOR_PRICE * 1.20, high=ANCHOR_PRICE * 1.20,
                          low=ANCHOR_PRICE * 1.20))
        record = _record("bullish", execution=_execution(
            invalidation_level=BULLISH_INVALIDATION, current_price=ANCHOR_PRICE,
            blocked=True, block_reason="event risk"),
            gates_triggered=(_gate(applies_to_open_position=False),))
        path_resolution, result = _run(record, bars)
        self.assertIs(path_resolution.direction, DirectionOutcome.CONFIRMED)
        self.assertIs(result.setup.state, SetupInvalidation.NOT_INVALIDATED)
        self.assertIs(result.execution.state, ExecutionOutcome.DEFERRAL_COSTLY)

    def test_neutral_direction_does_not_become_deferral_costly(self):
        bars = [_bar(d, ANCHOR_PRICE, high=ANCHOR_PRICE, low=ANCHOR_PRICE)
                for d in range(1, 13)]  # flat -> NEUTRAL_WITHIN_BAND
        record = _record("bullish", execution=_execution(
            invalidation_level=BULLISH_INVALIDATION, current_price=ANCHOR_PRICE,
            blocked=True, block_reason="event risk"),
            gates_triggered=(_gate(applies_to_open_position=False),))
        path_resolution, result = _run(record, bars)
        self.assertIs(path_resolution.direction, DirectionOutcome.NEUTRAL_WITHIN_BAND)
        self.assertIsNot(result.execution.state, ExecutionOutcome.DEFERRAL_COSTLY)
        self.assertIs(result.execution.state, ExecutionOutcome.UNRESOLVED)

    def test_no_block_yields_unresolved(self):
        bars = [_bar(d, ANCHOR_PRICE, high=ANCHOR_PRICE, low=ANCHOR_PRICE)
                for d in range(1, 12)]
        bars.append(_bar(12, ANCHOR_PRICE * 1.20, high=ANCHOR_PRICE * 1.20,
                          low=ANCHOR_PRICE * 1.20))
        record = _record("bullish", execution=_execution(
            invalidation_level=BULLISH_INVALIDATION, current_price=ANCHOR_PRICE,
            blocked=False))
        _, result = _run(record, bars)
        self.assertIs(result.execution.state, ExecutionOutcome.UNRESOLVED)
        self.assertFalse(result.execution.was_blocked)


# ===========================================================================
# D. DEFERRAL SCOPE -- OPEN-POSITION HOLD VS. NEW-ENTRY VETO   (extra)
# ===========================================================================
class TestDeferralScope(unittest.TestCase):
    def test_open_position_veto_is_not_applicable_not_a_deferral(self):
        bars = [_bar(1, ANCHOR_PRICE, high=ANCHOR_PRICE, low=3300.0)]
        record = _record("bullish", execution=_execution(
            invalidation_level=BULLISH_INVALIDATION, current_price=ANCHOR_PRICE,
            blocked=True, block_reason="event risk"),
            gates_triggered=(_gate(applies_to_open_position=True),))
        _, result = _run(record, bars)
        self.assertIs(result.execution.state, ExecutionOutcome.NOT_APPLICABLE)

    def test_blocked_with_no_veto_gate_found_is_unresolved(self):
        """Blocked=True but the record does not expose which gate did it."""
        bars = [_bar(1, ANCHOR_PRICE, high=ANCHOR_PRICE, low=3300.0)]
        record = _record("bullish", execution=_execution(
            invalidation_level=BULLISH_INVALIDATION, current_price=ANCHOR_PRICE,
            blocked=True, block_reason="event risk"),
            gates_triggered=())  # no gate recorded -- safest, do not guess
        _, result = _run(record, bars)
        self.assertIs(result.execution.state, ExecutionOutcome.UNRESOLVED)
        self.assertTrue(result.execution.was_blocked)


# ===========================================================================
# E. NOT_APPLICABLE / DEGENERATE / MISMATCH   (tests 13-16)
# ===========================================================================
class TestNotApplicable(unittest.TestCase):
    def test_direction_mismatch_is_not_applicable(self):
        """Invalidation built for a SHORT setup, claim is bullish -- not usable evidence."""
        record = _record("bullish", execution=_execution(
            invalidation_level=BEARISH_INVALIDATION, current_price=ANCHOR_PRICE))
        _, result = _run(record)
        self.assertIs(result.setup.state, SetupInvalidation.NOT_APPLICABLE)
        self.assertIs(result.execution.state, ExecutionOutcome.NOT_APPLICABLE)
        self.assertFalse(result.setup.measures.direction_agreement)
        self.assertNotIn(SetupInvalidation.INVALIDATED, [result.setup.state])

    def test_equal_current_and_invalidation_price_is_not_applicable(self):
        record = _record("bullish", execution=_execution(
            invalidation_level=ANCHOR_PRICE, current_price=ANCHOR_PRICE))
        _, result = _run(record)
        self.assertIs(result.setup.state, SetupInvalidation.NOT_APPLICABLE)
        self.assertIs(result.execution.state, ExecutionOutcome.NOT_APPLICABLE)

    def test_missing_invalidation_is_not_applicable(self):
        record = _record("bullish", execution=_execution(invalidation_level=None))
        _, result = _run(record)
        self.assertIs(result.setup.state, SetupInvalidation.NOT_APPLICABLE)
        self.assertIs(result.execution.state, ExecutionOutcome.NOT_APPLICABLE)

    def test_malformed_invalidation_is_safe_non_verdict(self):
        record = _record("bullish", execution=_execution(
            invalidation_level="not-a-number", invalidation_defined=True))
        _, result = _run(record)
        self.assertIs(result.setup.state, SetupInvalidation.NOT_APPLICABLE)
        self.assertIs(result.execution.state, ExecutionOutcome.NOT_APPLICABLE)


# ===========================================================================
# F. DATA QUALITY -- UNKNOWN / UNRESOLVED   (tests 17-21)
# ===========================================================================
class TestDataQuality(unittest.TestCase):
    def test_missing_bars_is_unknown_and_unresolved(self):
        record = _record("bullish", execution=_execution(
            invalidation_level=BULLISH_INVALIDATION, current_price=ANCHOR_PRICE))
        _, result = _run(record, bars=[])
        self.assertIs(result.setup.state, SetupInvalidation.UNKNOWN)
        self.assertIs(result.execution.state, ExecutionOutcome.UNRESOLVED)

    def test_immature_is_unknown_and_unresolved(self):
        record = _record("bullish", execution=_execution(
            invalidation_level=BULLISH_INVALIDATION, current_price=ANCHOR_PRICE))
        _, result = _run(record, now=EVAL_AT + timedelta(days=1))
        self.assertIs(result.setup.state, SetupInvalidation.UNKNOWN)
        self.assertIs(result.execution.state, ExecutionOutcome.UNRESOLVED)

    def test_partial_path_no_touch_is_unknown(self):
        sparse = [_bar(1, ANCHOR_PRICE, high=ANCHOR_PRICE, low=ANCHOR_PRICE)]
        record = _record("bullish", execution=_execution(
            invalidation_level=ANCHOR_PRICE - 500.0, current_price=ANCHOR_PRICE))
        path_resolution, result = _run(record, sparse)
        self.assertFalse(path_resolution.path_complete)
        self.assertIs(result.setup.state, SetupInvalidation.UNKNOWN)
        self.assertFalse(result.setup.measures.touched)

    def test_partial_path_proven_touch_is_invalidated(self):
        sparse = [_bar(1, ANCHOR_PRICE, high=ANCHOR_PRICE, low=3300.0)]
        record = _record("bullish", execution=_execution(
            invalidation_level=BULLISH_INVALIDATION, current_price=ANCHOR_PRICE))
        path_resolution, result = _run(record, sparse)
        self.assertFalse(path_resolution.path_complete)
        self.assertIs(result.setup.state, SetupInvalidation.INVALIDATED)

    def test_bar_content_conflict_is_unknown_and_unresolved(self):
        a = _bar(3, ANCHOR_PRICE, high=ANCHOR_PRICE, low=ANCHOR_PRICE)
        b = _bar(3, ANCHOR_PRICE * 2, high=ANCHOR_PRICE * 2, low=ANCHOR_PRICE * 2)
        record = _record("bullish", execution=_execution(
            invalidation_level=BULLISH_INVALIDATION, current_price=ANCHOR_PRICE))
        path_resolution, result = _run(record, [a, b])
        self.assertIs(path_resolution.data_resolution.name, "UNAVAILABLE")
        self.assertIs(result.setup.state, SetupInvalidation.UNKNOWN)
        self.assertIs(result.execution.state, ExecutionOutcome.UNRESOLVED)
        from apex.b2.validation.outcome import ExclusionReason
        self.assertIn(ExclusionReason.BAR_CONTENT_CONFLICT, result.setup.reasons)


# ===========================================================================
# G. INVERSION / SUBSTITUTION / RECONSTRUCTION   (tests 22-24)
# ===========================================================================
class TestProvenanceParity(unittest.TestCase):
    def test_inverted_fx_parity_no_reinversion(self):
        """JPY strength UP means USDJPY quote DOWN. Invalidation is already
        expressed in the strength convention -- this module must not re-invert it."""
        anchor = _anchor("USDJPY=X", invert=True, price=1.0 / 100.0, atr=0.0001)
        bars = [_bar(1, 102.0, high=103.0, low=101.0, symbol="USDJPY=X",
                     instrument="JPY", invert=True)]
        record = _record("bullish", instrument="JPY", anchor=anchor, execution=_execution(
            invalidation_level=0.0099, current_price=1.0 / 100.0))
        _, result = _run(record, bars, instrument="JPY")
        # analysis_low = 1/103 ~= 0.009709 <= 0.0099 -> touch
        self.assertIs(result.setup.state, SetupInvalidation.INVALIDATED)
        self.assertTrue(result.setup.measures.direction_agreement)

    def test_gold_substituted_series_still_resolves(self):
        gc_bars = [_bar(d, ANCHOR_PRICE, high=ANCHOR_PRICE, low=ANCHOR_PRICE,
                        symbol="GC=F") for d in range(1, 13)]
        record = _record("bullish", execution=_execution(
            invalidation_level=BULLISH_INVALIDATION, current_price=ANCHOR_PRICE))
        path_resolution, result = _run(record, gc_bars)
        from apex.b2.validation.outcome import EligibilityPool
        self.assertIsNot(path_resolution.eligibility_pool, EligibilityPool.CAPTURED)
        self.assertIs(result.setup.state, SetupInvalidation.NOT_INVALIDATED)

    def test_reconstructed_anchor_never_fabricates_a_verdict(self):
        record = _record("bullish", execution=_execution(
            invalidation_level=BULLISH_INVALIDATION, current_price=ANCHOR_PRICE))
        record["record"]["market_anchor"] = None
        record["record"]["schema_version"] = 1
        path_resolution, result = _run(record)
        from apex.b2.validation.outcome import EligibilityPool
        self.assertIsNot(path_resolution.eligibility_pool, EligibilityPool.CAPTURED)
        self.assertIs(result.setup.state, SetupInvalidation.UNKNOWN)
        self.assertIs(result.execution.state, ExecutionOutcome.UNRESOLVED)


# ===========================================================================
# H. FLAT / UNAVAILABLE CLAIMS   (tests 25-26)
# ===========================================================================
class TestNonDirectionalClaims(unittest.TestCase):
    def test_flat_claim_is_not_applicable(self):
        record = _record("flat", execution=_execution(
            invalidation_level=BULLISH_INVALIDATION, current_price=ANCHOR_PRICE))
        path_resolution, result = _run(record)
        self.assertIs(path_resolution.direction, DirectionOutcome.ABSTAINED)
        self.assertIs(result.setup.state, SetupInvalidation.NOT_APPLICABLE)
        self.assertIs(result.execution.state, ExecutionOutcome.NOT_APPLICABLE)

    def test_unavailable_claim_is_not_applicable(self):
        record = _record("unavailable", execution=_execution(
            invalidation_level=BULLISH_INVALIDATION, current_price=ANCHOR_PRICE))
        path_resolution, result = _run(record)
        self.assertIs(path_resolution.direction, DirectionOutcome.NOT_APPLICABLE)
        self.assertIs(result.setup.state, SetupInvalidation.NOT_APPLICABLE)
        self.assertIs(result.execution.state, ExecutionOutcome.NOT_APPLICABLE)


# ===========================================================================
# I. NO FABRICATED ENTRY VERDICTS   (tests 27-29)
# ===========================================================================
class TestNoEntryVerdicts(unittest.TestCase):
    def test_entry_justified_never_emitted(self):
        self.assertNotIn("ENTRY_JUSTIFIED", _identifiers(invalidation_mod))

    def test_entry_premature_never_emitted(self):
        self.assertNotIn("ENTRY_PREMATURE", _identifiers(invalidation_mod))

    def test_entry_late_never_emitted(self):
        self.assertNotIn("ENTRY_LATE", _identifiers(invalidation_mod))


# ===========================================================================
# J. THESIS INVALIDATION   (tests 30-31)
# ===========================================================================
class TestThesisInvalidation(unittest.TestCase):
    FIXTURES = []

    def test_thesis_always_not_assessable(self):
        cases = [
            dict(record=_record("bullish", execution=_execution(
                invalidation_level=BULLISH_INVALIDATION, current_price=ANCHOR_PRICE))),
            dict(record=_record("bearish", execution=_execution(
                invalidation_level=BEARISH_INVALIDATION, current_price=ANCHOR_PRICE))),
            dict(record=_record("flat")),
            dict(record=_record("unavailable")),
            dict(bars=[]),
            dict(now=EVAL_AT + timedelta(days=1)),
        ]
        for case in cases:
            _, result = _run(**case)
            self.assertIs(result.thesis, ThesisInvalidation.NOT_ASSESSABLE, str(case)[:60])

    def test_price_movement_cannot_invalidate_thesis(self):
        """Even a dramatic, clearly-invalidating move must not touch the thesis axis."""
        bars = [_bar(1, ANCHOR_PRICE, high=ANCHOR_PRICE, low=1.0)]  # extreme crash
        record = _record("bullish", execution=_execution(
            invalidation_level=BULLISH_INVALIDATION, current_price=ANCHOR_PRICE))
        _, result = _run(record, bars)
        self.assertIs(result.setup.state, SetupInvalidation.INVALIDATED)
        self.assertIs(result.thesis, ThesisInvalidation.NOT_ASSESSABLE)


# ===========================================================================
# K. 1R NORMALIZATION   (tests 32-35)
# ===========================================================================
class TestRNormalization(unittest.TestCase):
    def test_mfe_in_r_unit_conversion(self):
        bars = [_bar(1, ANCHOR_PRICE, high=ANCHOR_PRICE * 1.02, low=ANCHOR_PRICE)]
        record = _record("bullish", execution=_execution(
            invalidation_level=BULLISH_INVALIDATION, current_price=ANCHOR_PRICE))
        path_resolution, result = _run(record, bars)
        mfe_fraction = path_resolution.excursion.mfe
        expected = (mfe_fraction * path_resolution.anchor_price) / 20.0
        self.assertAlmostEqual(result.setup.measures.mfe_in_r, expected, places=9)

    def test_mae_in_r_unit_conversion(self):
        bars = [_bar(1, ANCHOR_PRICE, high=ANCHOR_PRICE, low=ANCHOR_PRICE * 0.995)]
        record = _record("bullish", execution=_execution(
            invalidation_level=BULLISH_INVALIDATION, current_price=ANCHOR_PRICE))
        path_resolution, result = _run(record, bars)
        mae_fraction = path_resolution.excursion.mae
        expected = (mae_fraction * path_resolution.anchor_price) / 20.0
        self.assertAlmostEqual(result.setup.measures.mae_in_r, expected, places=9)

    def test_zero_invalidation_distance_does_not_divide(self):
        self.assertIsNone(invalidation_mod._in_r(5.0, 0.0))

    def test_negative_or_malformed_normalization_safely_withheld(self):
        self.assertIsNone(invalidation_mod._in_r(5.0, -3.0))
        self.assertIsNone(invalidation_mod._in_r(None, 10.0))
        self.assertIsNone(invalidation_mod._price_distance(None, 100.0))
        self.assertIsNone(invalidation_mod._price_distance(0.05, None))


# ===========================================================================
# L. DETERMINISM   (tests 36-39)
# ===========================================================================
class TestDeterminism(unittest.TestCase):
    def _touch_fixture(self):
        bars = [
            _bar(1, ANCHOR_PRICE, high=ANCHOR_PRICE, low=ANCHOR_PRICE),
            _bar(2, ANCHOR_PRICE, high=ANCHOR_PRICE, low=ANCHOR_PRICE),
            _bar(3, ANCHOR_PRICE, high=ANCHOR_PRICE, low=3300.0),
            _bar(4, ANCHOR_PRICE, high=ANCHOR_PRICE, low=ANCHOR_PRICE),
        ]
        record = _record("bullish", execution=_execution(
            invalidation_level=BULLISH_INVALIDATION, current_price=ANCHOR_PRICE))
        return record, bars

    def test_first_touch_index_deterministic(self):
        record, bars = self._touch_fixture()
        _, result = _run(record, bars)
        self.assertEqual(result.setup.measures.bars_to_touch, 2)
        _, result_again = _run(record, bars)
        self.assertEqual(result_again.setup.measures.bars_to_touch, 2)

    def test_shuffled_input_bars_produce_same_result(self):
        record, bars = self._touch_fixture()
        _, straight = _run(record, bars)
        _, reversed_ = _run(record, list(reversed(bars)))
        scrambled = [bars[2], bars[0], bars[3], bars[1]]
        _, scrambled_result = _run(record, scrambled)
        self.assertEqual(straight.as_record(), reversed_.as_record())
        self.assertEqual(straight.as_record(), scrambled_result.as_record())

    def test_duplicate_identical_bars_produce_same_result(self):
        record, bars = self._touch_fixture()
        _, plain = _run(record, bars)
        _, duped = _run(record, bars + [bars[2], bars[2]])
        self.assertEqual(plain.as_record(), duped.as_record())

    def test_conflicting_duplicates_never_selected(self):
        a = _bar(3, ANCHOR_PRICE, high=ANCHOR_PRICE, low=ANCHOR_PRICE)
        b = _bar(3, ANCHOR_PRICE * 3, high=ANCHOR_PRICE * 3, low=ANCHOR_PRICE * 3)
        record = _record("bullish", execution=_execution(
            invalidation_level=BULLISH_INVALIDATION, current_price=ANCHOR_PRICE))
        _, forward = _run(record, [a, b])
        _, backward = _run(record, [b, a])
        self.assertEqual(forward.as_record(), backward.as_record())
        self.assertIsNone(forward.setup.measures.touched)
        self.assertIsNone(forward.setup.measures.bars_to_touch)


# ===========================================================================
# M. MISSING-DATA SWEEP   (test 43)
# ===========================================================================
class TestMissingDataNeverNegative(unittest.TestCase):
    def test_no_missing_data_path_ever_returns_invalidated_or_deferral_costly(self):
        cases = [
            dict(bars=[]),
            dict(now=EVAL_AT + timedelta(days=1)),
            dict(record=_record("bullish", execution=_execution(invalidation_level=None))),
            dict(record=_record("bullish", execution=_execution(
                invalidation_level=BEARISH_INVALIDATION, current_price=ANCHOR_PRICE))),
            dict(record=_record("bullish", execution=_execution(
                invalidation_level=ANCHOR_PRICE, current_price=ANCHOR_PRICE))),
            dict(record=_record("flat")),
            dict(record=_record("unavailable")),
            dict(record=_record("bullish", horizon="structural", execution=_execution(
                invalidation_level=BULLISH_INVALIDATION, current_price=ANCHOR_PRICE))),
        ]
        for case in cases:
            _, result = _run(**case)
            self.assertIsNot(result.setup.state, SetupInvalidation.INVALIDATED, str(case)[:80])
            self.assertIsNot(result.execution.state, ExecutionOutcome.DEFERRAL_COSTLY, str(case)[:80])


# ===========================================================================
# N. HORIZON SAFETY   (extra)
# ===========================================================================
class TestHorizonSafety(unittest.TestCase):
    def test_non_tactical_horizon_is_rejected_not_reinterpreted(self):
        record = _record("bullish", horizon="structural", execution=_execution(
            invalidation_level=BULLISH_INVALIDATION, current_price=ANCHOR_PRICE))
        _, result = _run(record)
        self.assertIs(result.setup.state, SetupInvalidation.NOT_APPLICABLE)
        self.assertIs(result.execution.state, ExecutionOutcome.NOT_APPLICABLE)
        self.assertIn("non_tactical_horizon_out_of_scope_for_d2c3", result.setup.notes)


# ===========================================================================
# O. RECORD SERIALISATION   (extra)
# ===========================================================================
class TestRecordShape(unittest.TestCase):
    def test_as_record_is_json_serialisable(self):
        import json
        bars = [_bar(1, ANCHOR_PRICE, high=ANCHOR_PRICE, low=3300.0)]
        record = _record("bullish", execution=_execution(
            invalidation_level=BULLISH_INVALIDATION, current_price=ANCHOR_PRICE,
            blocked=True), gates_triggered=(_gate(),))
        _, result = _run(record, bars)
        json.dumps(result.as_record())

    def test_not_resolved_in_this_stage_declares_d2c4_axes(self):
        _, result = _run()
        declared = result.as_record()["not_resolved_in_this_stage"]
        for axis in ("validation_id", "input_hash", "outcome_hash", "context", "overlap_metadata"):
            self.assertIn(axis, declared, axis)


# ===========================================================================
# P. PURE / DETERMINISTIC / PRODUCTION SAFETY   (tests 40-46)
# ===========================================================================
class TestProductionSafety(unittest.TestCase):
    def _unchanged(self, path):
        result = subprocess.run(
            ["git", "diff", "--exit-code", "--", path],
            cwd=ROOT, capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, f"{path} changed:\n{result.stdout[:800]}")

    def test_production_core_is_byte_for_byte_unchanged(self):
        self._unchanged("apex/production_core.py")

    def test_production_core_sha256_matches_approved_baseline(self):
        with open(os.path.join(ROOT, "apex", "production_core.py"), "rb") as handle:
            digest = hashlib.sha256(handle.read()).hexdigest()
        self.assertEqual(
            digest, "5935f807a8584007fc053ae7bb64d62017a7e2f804258d492fdd8a4c2cb4da69"
        )

    def test_every_d2c2_protected_file_is_unchanged(self):
        for path in (
            "apex/b2_bridge.py", "apex/b2_validation_bridge.py",
            "apex/b2/validation/anchor.py", "apex/b2/validation/bars.py",
            "apex/b2/validation/config.py", "apex/b2/validation/maturity.py",
            "apex/b2/validation/outcome.py", "apex/b2/validation/resolve.py",
            "apex/b2/validation/series.py", "apex/b2/validation/__init__.py",
            "apex/b2/shadow.py", "apex/b2/evaluate.py", "apex/b2/registry.py",
            "apex/b2/aggregation.py", "apex/b2/horizons.py", "apex/b2/execution.py",
            "apex/b2/decision.py", "apex/b2/thesis.py", "apex/b2/scenarios.py",
            "apex/b2/gates.py", "apex/b2/enums.py",
        ):
            self._unchanged(path)

    def test_no_forbidden_imports(self):
        tree = ast.parse(inspect.getsource(invalidation_mod))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [a.name for a in node.names] + [getattr(node, "module", "") or ""]
                for name in names:
                    for forbidden in ("requests", "urllib", "socket", "http", "streamlit",
                                      "threading", "asyncio", "subprocess", "multiprocessing",
                                      "sqlite", "psycopg", "supabase", "production_core",
                                      "b2_bridge", "b2_validation_bridge", "random"):
                        self.assertNotIn(forbidden, name, name)

    def test_does_not_import_validation_resolve(self):
        """Deliberate design choice: duck-typed input, so the existing D-2C2
        guard (nothing else imports validation.resolve) needs no change."""
        names = set()
        tree = ast.parse(inspect.getsource(invalidation_mod))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                names.add(node.module or "")
        for module_name in names:
            self.assertNotIn("validation.resolve", module_name)

    def test_no_production_core_import(self):
        names = _identifiers(invalidation_mod)
        self.assertNotIn("production_core", names)
        self.assertNotIn("core", names)

    def test_no_wall_clock_dependency(self):
        names = _identifiers(invalidation_mod)
        for forbidden in ("now", "utcnow", "datetime", "time", "random", "monotonic"):
            self.assertNotIn(forbidden, names, forbidden)

    def test_no_ai_telegram_or_scheduler_reference(self):
        names = {n.lower() for n in _identifiers(invalidation_mod)}
        for forbidden in ("telegram", "sendmessage", "openai", "anthropic", "gemini",
                          "groq", "completions", "thread", "timer", "sleep",
                          "crontab", "scheduler", "daemon"):
            self.assertFalse(any(forbidden in n for n in names), forbidden)

    def test_no_ddl_dml_or_persistence(self):
        upper = inspect.getsource(invalidation_mod).upper()
        for verb in ("CREATE TABLE", "ALTER TABLE", "DROP TABLE", "TRUNCATE",
                     "INSERT INTO", "DELETE FROM"):
            self.assertNotIn(verb, upper, verb)

    def test_no_rate_or_calibration_is_computed(self):
        names = {n.lower() for n in _identifiers(invalidation_mod)}
        for forbidden in ("hit_rate", "accuracy", "win_rate", "calibrate",
                          "significance", "p_value", "wilson"):
            self.assertFalse(any(forbidden in n for n in names), forbidden)

    def test_no_module_outside_tests_imports_invalidation(self):
        """D-2C3 remains inert: nothing under production or B2 capture wires it in."""
        importers = []
        for folder, _dirs, files in os.walk(ROOT):
            if any(p in folder for p in ("_backup_", "_baseline_", ".git", "__pycache__", "tests")):
                continue
            for filename in files:
                if not filename.endswith(".py") or filename == "invalidation.py":
                    continue
                path = os.path.join(folder, filename)
                with open(path, encoding="utf-8") as handle:
                    try:
                        tree = ast.parse(handle.read())
                    except SyntaxError:
                        continue
                for node in ast.walk(tree):
                    if isinstance(node, ast.ImportFrom) and "validation.invalidation" in (node.module or ""):
                        importers.append(filename)
                    if isinstance(node, ast.Import) and any(
                            "validation.invalidation" in a.name for a in node.names):
                        importers.append(filename)
        self.assertEqual(sorted(set(importers)), [])

    def test_production_signal_thresholds_are_unchanged(self):
        self.assertEqual(core.bias_from_score(0.40)[0], "\U0001f680 Strong Bullish")
        self.assertEqual(core._broad_regime("\U0001f680 Strong Bullish"), "Bullish")


# ===========================================================================
# Q. D-2C2 REGRESSION -- run the D-2C2 suite from inside this suite   (test 45)
# ===========================================================================
class TestD2C2SuiteUnaffected(unittest.TestCase):
    def test_existing_d2c2_suite_still_passes(self):
        loader = unittest.TestLoader()
        suite = loader.loadTestsFromModule(d2c2_tests)
        buffer = io.StringIO()
        runner = unittest.TextTestRunner(stream=buffer, verbosity=0)
        result = runner.run(suite)
        self.assertTrue(
            result.wasSuccessful(),
            f"D-2C2 suite regressed: {len(result.failures)} failures, "
            f"{len(result.errors)} errors:\n{buffer.getvalue()}",
        )

    def test_existing_d2c2_output_remains_unchanged(self):
        """Spot-check: a known D-2C2 assertion still holds bit-for-bit."""
        bars = [_bar(d, ANCHOR_PRICE, high=ANCHOR_PRICE, low=ANCHOR_PRICE)
                for d in range(1, 12)]
        bars.append(_bar(12, ANCHOR_PRICE * 1.20, high=ANCHOR_PRICE * 1.20,
                          low=ANCHOR_PRICE * 1.20))
        path_resolution, _ = _run(_record("bullish"), bars)
        self.assertIs(path_resolution.direction, DirectionOutcome.CONFIRMED)
        self.assertAlmostEqual(path_resolution.band.band_atr * 100, 3.0578, places=3)


if __name__ == "__main__":
    unittest.main()
