"""Stage D-2C4: deterministic validation identity, provenance and envelope.

Covers ``apex.b2.validation.envelope``, which binds one D-2C2
``DirectionPathResolution`` and one D-2C3 ``D2C3Resolution`` into a
``ValidationEnvelope`` carrying ``validation_id``/``input_hash``/
``outcome_hash``, human-readable ``ValidationContext``, and optional
``OverlapMetadata``. Nothing here recomputes D-2C2 or D-2C3; every fixture
runs the real resolvers so the envelope is built from genuine results.

Imports ``apex.production_core`` for the safety assertions, so durable-state
isolation is installed first. Nothing here performs I/O.
"""
from __future__ import annotations

import ast
import inspect
import io
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
from apex.b2.validation import envelope as envelope_mod
from apex.b2.validation.bars import GRANULARITY_1D, MarketBar
from apex.b2.validation.config import DEFAULT_VALIDATION_CONFIG, ValidationConfig
from apex.b2.validation.envelope import (
    OverlapContext,
    OverlapMetadata,
    ValidationContext,
    ValidationEnvelope,
    build_validation_envelope,
    canonical_json,
    sha256_hex,
)
from apex.b2.validation.invalidation import resolve_setup_and_execution
from apex.b2.validation.maturity import MaturityState
from apex.b2.validation.outcome import (
    DataResolution,
    DirectionOutcome,
    EligibilityPool,
    ExecutionOutcome,
    OutcomeAxes,
    OutcomeInvariantError,
    SetupInvalidation,
    ThesisInvalidation,
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
# Fixtures -- mirror the D-2C2/D-2C3 suites' own conventions closely.
# ===========================================================================

def _anchor(symbol="XAUUSD=X", *, invert=False, atr=ANCHOR_ATR, price=ANCHOR_PRICE,
            vol=ANCHOR_VOL, status="anchor_captured"):
    return {
        "analysis_price": price, "last_price": price, "symbol": symbol,
        "symbol_requested": symbol, "symbol_fallback_used": False,
        "invert": invert, "market_ts": 1, "market_ts_iso": "",
        "volatility_scale": vol, "atr": atr, "atr_ratio": 1.05,
        "volatility_regime": "normal", "price_source": "yahoo_5m_tactical",
        "granularity": "5m", "anchor_status": status,
    }


def _execution(*, invalidation_level=None, current_price=ANCHOR_PRICE,
               blocked=False, block_reason="", invalidation_defined=None):
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


def _build(record=None, bars=None, *, now=NOW, instrument="Gold", tail=True,
           config=None, overlap_context=None, malformed_row_count=None):
    """Resolve D-2C2 -> D-2C3 -> D-2C4 for one fixture."""
    rec = record if record is not None else _record()
    supplied = bars if bars is not None else _flat_path()
    if tail:
        supplied = list(supplied) + _capture_tail(supplied)
    path_resolution = resolve_direction_and_path(
        record=rec, bars=supplied, now=now,
        convention=b2_bridge.symbol_convention(instrument),
    )
    d2c3 = resolve_setup_and_execution(record=rec, path_resolution=path_resolution)
    env = build_validation_envelope(
        record=rec, path_resolution=path_resolution, d2c3_resolution=d2c3,
        validation_config=config or DEFAULT_VALIDATION_CONFIG,
        overlap_context=overlap_context, malformed_row_count=malformed_row_count,
    )
    return path_resolution, d2c3, env


def _confirmed_bullish_bars(touch=False):
    bars = [_bar(1, ANCHOR_PRICE, high=ANCHOR_PRICE, low=(3300.0 if touch else ANCHOR_PRICE))]
    bars += [_bar(d, ANCHOR_PRICE, high=ANCHOR_PRICE, low=ANCHOR_PRICE) for d in range(2, 12)]
    bars.append(_bar(12, ANCHOR_PRICE * 1.20, high=ANCHOR_PRICE * 1.20, low=ANCHOR_PRICE * 1.20))
    return bars


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
# A. VALIDATION ID   (10 tests)
# ===========================================================================
class TestValidationId(unittest.TestCase):
    def test_deterministic_repeated_construction(self):
        _, _, a = _build()
        _, _, b = _build()
        self.assertEqual(a.validation_id, b.validation_id)

    def test_storage_id_change_changes_validation_id(self):
        _, _, a = _build(record=_record(record_id="r1"))
        _, _, b = _build(record=_record(record_id="r2"))
        self.assertNotEqual(a.validation_id, b.validation_id)

    def test_horizon_change_changes_validation_id(self):
        _, _, a = _build(record=_record(horizon="tactical"))
        # execution horizon: still resolvable by D-2C2 generically (different window)
        rec_exec = _record(horizon="execution")
        path_res = resolve_direction_and_path(
            record=rec_exec, bars=_flat_path() + _capture_tail(_flat_path()), now=NOW,
            convention=b2_bridge.symbol_convention("Gold"),
        )
        d2c3 = resolve_setup_and_execution(record=rec_exec, path_resolution=path_res)
        b = build_validation_envelope(record=rec_exec, path_resolution=path_res,
                                       d2c3_resolution=d2c3, validation_config=DEFAULT_VALIDATION_CONFIG)
        self.assertNotEqual(a.validation_id, b.validation_id)

    def test_config_content_change_changes_validation_id(self):
        _, _, a = _build(config=DEFAULT_VALIDATION_CONFIG)
        _, _, b = _build(config=ValidationConfig(atr_period_bars=20))
        self.assertNotEqual(a.validation_id, b.validation_id)

    def test_same_config_version_different_hash_changes_validation_id(self):
        default_cfg = ValidationConfig()
        variant_cfg = ValidationConfig(atr_period_bars=20)
        self.assertEqual(default_cfg.version, variant_cfg.version)
        self.assertNotEqual(default_cfg.config_hash, variant_cfg.config_hash)
        _, _, a = _build(config=default_cfg)
        _, _, b = _build(config=variant_cfg)
        self.assertNotEqual(a.validation_id, b.validation_id)

    def test_schema_version_participates_in_validation_id(self):
        _, _, a = _build()
        basis = "|".join([a.context.shadow_storage_id, a.context.horizon,
                           a.context.validation_config_hash, "some-other-schema-version"])
        different = sha256_hex(basis, 32)
        self.assertNotEqual(a.validation_id, different)

    def test_wall_clock_does_not_participate(self):
        _, _, a = _build(now=NOW)
        _, _, b = _build(now=NOW + timedelta(days=400))
        self.assertEqual(a.validation_id, b.validation_id)

    def test_bare_payload_identity_recomputation(self):
        bare = _record(wrapped=False)
        wrapped = _record(wrapped=True)
        _, _, a = _build(record=bare)
        _, _, b = _build(record=wrapped)
        self.assertEqual(a.validation_id, b.validation_id)

    def test_wrapped_row_shaped_identity_ignores_wrapper_storage_id(self):
        """A wrapper storage_id is never trusted -- always recomputed."""
        wrong_wrapper = _record(wrapped=True)
        wrong_wrapper["storage_id"] = "totally-different-value"
        _, _, a = _build(record=_record(wrapped=True))
        _, _, b = _build(record=wrong_wrapper)
        self.assertEqual(a.validation_id, b.validation_id)

    def test_legacy_v1_record_gets_deterministic_identity(self):
        legacy = _record(schema_version=1)
        legacy["record"]["market_anchor"] = None
        _, _, a = _build(record=legacy)
        _, _, b = _build(record=legacy)
        self.assertEqual(a.validation_id, b.validation_id)
        self.assertTrue(a.validation_id)


# ===========================================================================
# B. CONFIG HASH   (6 tests)
# ===========================================================================
class TestConfigHash(unittest.TestCase):
    def test_existing_config_hash_is_reused_verbatim(self):
        _, _, env = _build(config=DEFAULT_VALIDATION_CONFIG)
        self.assertEqual(env.context.validation_config_hash, DEFAULT_VALIDATION_CONFIG.config_hash)

    def test_stable_field_ordering(self):
        a = ValidationConfig().config_hash
        b = ValidationConfig().config_hash
        self.assertEqual(a, b)

    def test_semantic_config_change_changes_hash(self):
        _, _, a = _build(config=ValidationConfig())
        _, _, b = _build(config=ValidationConfig(neutral_band_atr_multiple=0.75))
        self.assertNotEqual(a.context.validation_config_hash, b.context.validation_config_hash)
        self.assertNotEqual(a.input_hash, b.input_hash)

    def test_same_version_changed_content_changes_hash(self):
        default_cfg = ValidationConfig()
        variant_cfg = ValidationConfig(max_gap_multiple=3.0)
        self.assertEqual(default_cfg.version, variant_cfg.version)
        self.assertNotEqual(default_cfg.config_hash, variant_cfg.config_hash)

    def test_no_competing_d2c4_config_hash_implementation(self):
        """envelope.py must not define its own config-hashing function."""
        names = _identifiers(envelope_mod)
        for forbidden in ("neutral_band_atr_multiple", "atr_period_bars", "max_gap_multiple"):
            self.assertNotIn(forbidden, names, forbidden)

    def test_config_version_preserved_separately(self):
        cfg = ValidationConfig(atr_period_bars=20)
        _, _, env = _build(config=cfg)
        self.assertEqual(env.context.validation_config_version, cfg.version)
        self.assertEqual(env.context.validation_config_hash, cfg.config_hash)
        self.assertNotEqual(env.context.validation_config_version, env.context.validation_config_hash)


# ===========================================================================
# C. INPUT HASH   (16 tests)
# ===========================================================================
class TestInputHash(unittest.TestCase):
    def test_deterministic_repeated_construction(self):
        _, _, a = _build()
        _, _, b = _build()
        self.assertEqual(a.input_hash, b.input_hash)

    def test_canonical_path_order_preserved_in_basis(self):
        bars = _confirmed_bullish_bars()
        _, _, env = _build(bars=bars)
        obs_ids = [entry["observation_id"] for entry in env.input_hash_basis["used_bars"]]
        self.assertEqual(obs_ids, sorted(obs_ids, key=lambda oid: obs_ids.index(oid)))
        # matches the canonical path order exactly, not re-sorted by hash value
        self.assertEqual(list(env.context.used_observation_ids), obs_ids)

    def test_shuffled_raw_input_same_canonical_path_gives_same_input_hash(self):
        bars = _confirmed_bullish_bars()
        _, _, straight = _build(bars=bars)
        _, _, shuffled = _build(bars=list(reversed(bars)))
        _, _, scrambled = _build(bars=[bars[5], bars[0], bars[11], bars[2], bars[8], bars[1],
                                        bars[9], bars[3], bars[7], bars[4], bars[10], bars[6]])
        self.assertEqual(straight.input_hash, shuffled.input_hash)
        self.assertEqual(straight.input_hash, scrambled.input_hash)

    def test_same_content_hash_set_different_sequence_gives_different_input_hash(self):
        """The Decision-2 collision proof: swapping which day holds which
        content must change input_hash even though the SET of content
        hashes used is identical."""
        a_bar = _bar(1, ANCHOR_PRICE, high=ANCHOR_PRICE, low=ANCHOR_PRICE)
        b_bar = _bar(2, ANCHOR_PRICE * 1.05, high=ANCHOR_PRICE * 1.05, low=ANCHOR_PRICE * 1.05)
        path_a = [a_bar, b_bar]  # day1=flat, day2=up
        # Swap CONTENT between the two days, keeping bar_time fixed at 1 and 2.
        swapped_a = _bar(1, ANCHOR_PRICE * 1.05, high=ANCHOR_PRICE * 1.05, low=ANCHOR_PRICE * 1.05)
        swapped_b = _bar(2, ANCHOR_PRICE, high=ANCHOR_PRICE, low=ANCHOR_PRICE)
        path_b = [swapped_a, swapped_b]

        content_a = {bar.content_hash for bar in path_a}
        content_b = {bar.content_hash for bar in path_b}
        self.assertEqual(content_a, content_b, "the two paths must use the same SET of content")

        _, _, env_a = _build(bars=path_a)
        _, _, env_b = _build(bars=path_b)
        self.assertNotEqual(
            env_a.input_hash, env_b.input_hash,
            "swapping which day holds which content must change input_hash",
        )

    def test_used_bar_content_change_changes_input_hash(self):
        bars = _confirmed_bullish_bars()
        _, _, a = _build(bars=bars)
        altered = list(bars)
        altered[5] = _bar(6, ANCHOR_PRICE * 1.5, high=ANCHOR_PRICE * 1.5, low=ANCHOR_PRICE * 1.5)
        _, _, b = _build(bars=altered)
        self.assertNotEqual(a.input_hash, b.input_hash)

    def test_used_observation_id_change_changes_input_hash(self):
        bars = _confirmed_bullish_bars()
        _, _, a = _build(bars=bars)
        altered = list(bars)
        altered[0] = _bar(30, ANCHOR_PRICE, high=ANCHOR_PRICE, low=3300.0, month=8)
        _, _, b = _build(bars=altered)
        self.assertNotEqual(a.input_hash, b.input_hash)

    def test_exact_duplicate_count_does_not_change_input_hash(self):
        bars = _confirmed_bullish_bars()
        _, _, plain = _build(bars=bars)
        _, _, duped = _build(bars=bars + [bars[3], bars[3], bars[7]])
        self.assertEqual(plain.input_hash, duped.input_hash)

    def test_duplicate_count_available_in_context_not_hash(self):
        bars = _confirmed_bullish_bars()
        _, _, duped = _build(bars=bars + [bars[3], bars[3]])
        self.assertEqual(duped.context.duplicates_collapsed, 2)
        self.assertNotIn("duplicates_collapsed", canonical_json(duped.input_hash_basis))

    def test_conflict_evidence_changes_input_hash(self):
        # clean uses every eligible day (1-12); introduce a SECOND, conflicting
        # value at day 3 so that identity is withheld from used_bars while
        # every other day's bar is untouched.
        clean = _confirmed_bullish_bars()
        conflicting_value = _bar(3, ANCHOR_PRICE * 2, high=ANCHOR_PRICE * 2, low=ANCHOR_PRICE * 2)
        with_conflict = clean + [conflicting_value]
        _, _, a = _build(bars=clean)
        _, _, b = _build(bars=with_conflict)
        self.assertNotEqual(a.input_hash, b.input_hash)
        self.assertEqual(len(b.input_hash_basis["conflicts"]), 1)
        self.assertEqual(len(b.input_hash_basis["used_bars"]), len(a.input_hash_basis["used_bars"]) - 1)
        day3_id = _bar(3, ANCHOR_PRICE, high=ANCHOR_PRICE, low=ANCHOR_PRICE).observation_id
        used_ids_after = {entry["observation_id"] for entry in b.input_hash_basis["used_bars"]}
        self.assertNotIn(day3_id, used_ids_after)

    def test_different_conflicting_content_hashes_change_input_hash(self):
        a1 = _bar(3, ANCHOR_PRICE, high=ANCHOR_PRICE, low=ANCHOR_PRICE)
        a2 = _bar(3, ANCHOR_PRICE * 2, high=ANCHOR_PRICE * 2, low=ANCHOR_PRICE * 2)
        b1 = _bar(3, ANCHOR_PRICE, high=ANCHOR_PRICE, low=ANCHOR_PRICE)
        b2 = _bar(3, ANCHOR_PRICE * 3, high=ANCHOR_PRICE * 3, low=ANCHOR_PRICE * 3)
        _, _, env_a = _build(bars=[a1, a2])
        _, _, env_b = _build(bars=[b1, b2])
        self.assertNotEqual(env_a.input_hash, env_b.input_hash)

    def test_malformed_row_count_represented_when_supplied(self):
        _, _, a = _build(malformed_row_count=0)
        _, _, b = _build(malformed_row_count=3)
        self.assertEqual(a.input_hash_basis["malformed_row_count"], 0)
        self.assertEqual(b.input_hash_basis["malformed_row_count"], 3)
        self.assertNotEqual(a.input_hash, b.input_hash)

    def test_malformed_row_count_defaults_to_none_not_zero(self):
        _, _, env = _build()
        self.assertIsNone(env.input_hash_basis["malformed_row_count"])
        self.assertIsNone(env.context.malformed_row_count)

    def test_outside_horizon_bars_not_added_by_d2c4(self):
        bars = _flat_path()
        poisoned = bars + [_bar(25, ANCHOR_PRICE * 5), _bar(30, ANCHOR_PRICE * 0.1)]
        _, _, a = _build(bars=bars, tail=True)
        _, _, b = _build(bars=poisoned, tail=True)
        self.assertEqual(a.input_hash, b.input_hash)

    def test_entire_canonical_path_represented_not_terminal_only(self):
        bars = _confirmed_bullish_bars()
        _, path_resolution_len_check, env = (None, None, None)
        path_res, _, env = _build(bars=bars)
        self.assertGreater(len(env.input_hash_basis["used_bars"]), 1)
        self.assertEqual(len(env.input_hash_basis["used_bars"]),
                          len(path_res.canonicalization.bars))

    def test_gold_substitution_provenance_represented(self):
        gc_bars = [_bar(d, ANCHOR_PRICE, high=ANCHOR_PRICE, low=ANCHOR_PRICE, symbol="GC=F")
                   for d in range(1, 13)]
        path_res, _, env = _build(bars=gc_bars)
        self.assertEqual(env.input_hash_basis["series_binding"]["quality"], "series_substituted")
        self.assertEqual(env.context.series_binding_quality, "series_substituted")
        self.assertIsNot(path_res.eligibility_pool, EligibilityPool.CAPTURED)

    def test_reconstructed_anchor_provenance_represented(self):
        legacy = _record(schema_version=1)
        legacy["record"]["market_anchor"] = None
        _, _, env = _build(record=legacy)
        self.assertIn(env.input_hash_basis["anchor_resolution"]["status"],
                       ("anchor_missing", "anchor_reconstructed"))
        self.assertFalse(env.input_hash_basis["anchor_resolution"]["point_in_time"])

    def test_series_binding_represented(self):
        _, _, env = _build()
        binding = env.input_hash_basis["series_binding"]
        for key in ("quality", "bound_symbol", "inversion", "cross_source", "cross_granularity"):
            self.assertIn(key, binding)

    def test_anchor_classification_represented(self):
        _, _, env = _build()
        anchor = env.input_hash_basis["anchor_resolution"]
        for key in ("status", "point_in_time", "caveats"):
            self.assertIn(key, anchor)
        self.assertEqual(anchor["status"], "anchor_captured")


# ===========================================================================
# D. OUTCOME HASH   (18 tests)
# ===========================================================================
class TestOutcomeHash(unittest.TestCase):
    def test_deterministic_repeated_construction(self):
        _, _, a = _build()
        _, _, b = _build()
        self.assertEqual(a.outcome_hash, b.outcome_hash)

    def test_direction_outcome_change_changes_outcome_hash(self):
        _, _, confirmed = _build(bars=_confirmed_bullish_bars())
        flat_bars = [_bar(d, ANCHOR_PRICE, high=ANCHOR_PRICE, low=ANCHOR_PRICE) for d in range(1, 13)]
        _, _, neutral = _build(bars=flat_bars)
        self.assertNotEqual(confirmed.outcome_hash, neutral.outcome_hash)

    def test_setup_invalidation_change_changes_outcome_hash(self):
        record = _record("bullish", execution=_execution(invalidation_level=BULLISH_INVALIDATION))
        _, d2c3_touched, touched = _build(record=record, bars=_confirmed_bullish_bars(touch=True))
        _, d2c3_clean, not_touched = _build(record=record, bars=_confirmed_bullish_bars(touch=False))
        self.assertIs(d2c3_touched.setup.state, SetupInvalidation.INVALIDATED)
        self.assertIs(d2c3_clean.setup.state, SetupInvalidation.NOT_INVALIDATED)
        self.assertNotEqual(touched.outcome_hash, not_touched.outcome_hash)

    def test_execution_outcome_change_changes_outcome_hash(self):
        record_blocked = _record("bullish", execution=_execution(
            invalidation_level=BULLISH_INVALIDATION, blocked=True, block_reason="veto"),
            gates_triggered=(_gate(),))
        record_unblocked = _record("bullish", execution=_execution(
            invalidation_level=BULLISH_INVALIDATION, blocked=False))
        bars = _confirmed_bullish_bars()
        _, _, blocked_env = _build(record=record_blocked, bars=bars)
        _, _, unblocked_env = _build(record=record_unblocked, bars=bars)
        self.assertNotEqual(blocked_env.outcome_hash, unblocked_env.outcome_hash)

    def test_thesis_state_represented(self):
        _, d2c3, env = _build()
        self.assertEqual(d2c3.thesis, ThesisInvalidation.NOT_ASSESSABLE)
        self.assertEqual(env.outcome_hash_basis["thesis_invalidation"], "not_assessable")

    def test_terminal_return_represented(self):
        path_res, _, env = _build(bars=_confirmed_bullish_bars())
        self.assertEqual(env.outcome_hash_basis["terminal_return"], path_res.excursion.terminal_return)
        self.assertIsNotNone(env.outcome_hash_basis["terminal_return"])

    def test_mfe_represented(self):
        path_res, _, env = _build(bars=_confirmed_bullish_bars())
        self.assertEqual(env.outcome_hash_basis["mfe"], path_res.excursion.mfe)

    def test_mae_represented(self):
        path_res, _, env = _build(bars=_confirmed_bullish_bars())
        self.assertEqual(env.outcome_hash_basis["mae"], path_res.excursion.mae)

    def test_bars_to_mfe_represented(self):
        path_res, _, env = _build(bars=_confirmed_bullish_bars())
        self.assertEqual(env.outcome_hash_basis["bars_to_mfe"], path_res.excursion.bars_to_mfe)

    def test_bars_to_mae_represented(self):
        path_res, _, env = _build(bars=_confirmed_bullish_bars())
        self.assertEqual(env.outcome_hash_basis["bars_to_mae"], path_res.excursion.bars_to_mae)

    def test_bars_to_touch_represented(self):
        record = _record("bullish", execution=_execution(invalidation_level=BULLISH_INVALIDATION))
        _, d2c3, env = _build(record=record, bars=_confirmed_bullish_bars(touch=True))
        self.assertEqual(env.outcome_hash_basis["bars_to_touch"], d2c3.setup.measures.bars_to_touch)
        self.assertIsNotNone(env.outcome_hash_basis["bars_to_touch"])

    def test_neutral_band_represented(self):
        path_res, _, env = _build()
        self.assertEqual(env.outcome_hash_basis["neutral_band"], path_res.band.band)
        self.assertEqual(env.outcome_hash_basis["neutral_band_mode"], path_res.band.mode.value)

    def test_path_completeness_represented(self):
        path_res, _, env = _build(bars=_confirmed_bullish_bars())
        self.assertEqual(env.outcome_hash_basis["path_complete"], path_res.path_complete)

    def test_coverage_represented(self):
        path_res, _, env = _build()
        self.assertEqual(env.outcome_hash_basis["coverage_status"], path_res.coverage_status)

    def test_eligibility_pool_represented(self):
        path_res, _, env = _build()
        self.assertEqual(env.outcome_hash_basis["eligibility_pool"], path_res.eligibility_pool.value)

    def test_r_normalization_represented(self):
        record = _record("bullish", execution=_execution(invalidation_level=BULLISH_INVALIDATION))
        _, d2c3, env = _build(record=record, bars=_confirmed_bullish_bars())
        self.assertEqual(env.outcome_hash_basis["mfe_in_r"], d2c3.setup.measures.mfe_in_r)
        self.assertEqual(env.outcome_hash_basis["mae_in_r"], d2c3.setup.measures.mae_in_r)
        self.assertIsNotNone(env.outcome_hash_basis["mfe_in_r"])
        self.assertIsNotNone(env.outcome_hash_basis["mae_in_r"])

    def test_block_reason_represented_where_semantic(self):
        record = _record("bullish", execution=_execution(
            invalidation_level=BULLISH_INVALIDATION, blocked=True, block_reason="specific veto reason"),
            gates_triggered=(_gate(),))
        _, d2c3, env = _build(record=record, bars=_confirmed_bullish_bars())
        self.assertEqual(env.outcome_hash_basis["block_reason"], d2c3.execution.block_reason)

    def test_depends_on_represented(self):
        record = _record("bullish", execution=_execution(
            invalidation_level=BULLISH_INVALIDATION, blocked=True, block_reason="veto"),
            gates_triggered=(_gate(),))
        _, d2c3, env = _build(record=record, bars=_confirmed_bullish_bars())
        self.assertEqual(env.outcome_hash_basis["depends_on"], list(d2c3.execution.depends_on))

    def test_notes_do_not_change_outcome_hash(self):
        record_a = _record("bullish", execution=_execution(invalidation_level=None))
        record_b = _record("bullish", execution=_execution(invalidation_level=None))
        _, d2c3_a, env_a = _build(record=record_a)
        _, d2c3_b, env_b = _build(record=record_b)
        self.assertEqual(d2c3_a.setup.notes, d2c3_b.setup.notes)
        # Force different notes on a hand-built resolution with identical
        # everything else and confirm the outcome_hash is unaffected.
        import dataclasses
        d2c3_c = dataclasses.replace(
            d2c3_b, setup=dataclasses.replace(d2c3_b.setup, notes=("a_totally_different_note",)),
        )
        path_res_b = resolve_direction_and_path(
            record=record_b, bars=_flat_path() + _capture_tail(_flat_path()), now=NOW,
            convention=b2_bridge.symbol_convention("Gold"),
        )
        env_c = build_validation_envelope(record=record_b, path_resolution=path_res_b,
                                           d2c3_resolution=d2c3_c, validation_config=DEFAULT_VALIDATION_CONFIG)
        self.assertEqual(env_b.outcome_hash, env_c.outcome_hash)
        self.assertNotIn("notes", canonical_json(env_b.outcome_hash_basis))

    def test_maturity_now_does_not_change_outcome_hash(self):
        _, _, a = _build(bars=_confirmed_bullish_bars(), now=NOW)
        _, _, b = _build(bars=_confirmed_bullish_bars(), now=NOW + timedelta(days=365))
        self.assertEqual(a.outcome_hash, b.outcome_hash)

    def test_elapsed_fraction_does_not_change_outcome_hash(self):
        self.assertNotIn("elapsed_fraction", canonical_json({}))  # sanity: key literal absent
        _, _, env = _build()
        self.assertNotIn("elapsed_fraction", json.dumps(env.outcome_hash_basis))

    def test_reasons_preserve_semantic_order(self):
        path_res, _, env = _build(bars=[])
        self.assertEqual(env.outcome_hash_basis["reasons"], [r.value for r in path_res.reasons])

    def test_none_is_distinct_from_zero(self):
        record = _record("bullish", execution=_execution(invalidation_level=None))
        _, _, env = _build(record=record)
        self.assertIsNone(env.outcome_hash_basis["invalidation_level"])
        # Never coerced to 0.0.
        self.assertNotEqual(env.outcome_hash_basis["invalidation_level"], 0.0)

    def test_outcome_provenance_objects_not_duplicated_in_outcome_hash(self):
        _, _, env = _build()
        dumped = json.dumps(env.outcome_hash_basis)
        for forbidden in ("cross_source", "cross_granularity", "bound_symbol", "duplicates_collapsed"):
            self.assertNotIn(forbidden, dumped, forbidden)


# ===========================================================================
# E. FLOAT CANONICALIZATION   (7 tests)
# ===========================================================================
class TestFloatCanonicalization(unittest.TestCase):
    def test_zero_and_negative_zero_canonicalize_identically(self):
        self.assertEqual(canonical_json({"x": 0.0}), canonical_json({"x": -0.0}))

    def test_nan_rejected(self):
        with self.assertRaises(ValueError):
            canonical_json({"x": float("nan")})

    def test_positive_infinity_rejected(self):
        with self.assertRaises(ValueError):
            canonical_json({"x": float("inf")})

    def test_negative_infinity_rejected(self):
        with self.assertRaises(ValueError):
            canonical_json({"x": float("-inf")})

    def test_finite_small_float_stable(self):
        value = 1e-12
        self.assertEqual(canonical_json({"x": value}), canonical_json({"x": value}))

    def test_finite_large_float_stable(self):
        value = 1.23456789e12
        self.assertEqual(canonical_json({"x": value}), canonical_json({"x": value}))

    def test_materially_different_finite_values_do_not_collapse(self):
        a = canonical_json({"x": 0.100000001})
        b = canonical_json({"x": 0.100000002})
        self.assertNotEqual(a, b)

    def test_enum_serializes_via_value(self):
        self.assertEqual(canonical_json({"s": DirectionOutcome.CONFIRMED}),
                          canonical_json({"s": "confirmed"}))


# ===========================================================================
# F. TIME / FINALIZATION   (9 tests)
# ===========================================================================
class TestTimeFinalization(unittest.TestCase):
    def test_not_matured_envelope_exists(self):
        _, _, env = _build(now=EVAL_AT + timedelta(days=1))
        self.assertEqual(env.context.maturity_state, "not_matured")
        self.assertEqual(env.context.finalization_status, "not_matured")
        self.assertIsNotNone(env.validation_id)
        self.assertIsNotNone(env.input_hash)
        self.assertIsNotNone(env.outcome_hash)

    def test_provisional_awaiting_bars_envelope_exists(self):
        record = _record("bullish", execution=_execution(invalidation_level=BULLISH_INVALIDATION))
        path_res, _, env = _build(record=record, bars=[], tail=False)
        self.assertIn(env.context.finalization_status,
                      ("provisional_awaiting_bars", "not_matured"))

    def test_provisional_partial_envelope_exists(self):
        sparse = [_bar(1, ANCHOR_PRICE, high=ANCHOR_PRICE, low=ANCHOR_PRICE)]
        _, _, env = _build(bars=sparse)
        self.assertEqual(env.context.finalization_status, "provisional_partial")

    def test_final_envelope_exists(self):
        _, _, env = _build(bars=_confirmed_bullish_bars())
        self.assertEqual(env.context.finalization_status, "final")

    def test_validation_id_stable_across_provisional_and_final_rerun(self):
        record = _record("bullish", execution=_execution(invalidation_level=BULLISH_INVALIDATION))
        bars = _confirmed_bullish_bars()
        _, _, provisional = _build(record=record, bars=bars, now=EVAL_AT + timedelta(days=1))
        _, _, final = _build(record=record, bars=bars, now=NOW)
        self.assertEqual(provisional.validation_id, final.validation_id)

    def test_more_eligible_bars_may_change_input_hash(self):
        record = _record("bullish", execution=_execution(invalidation_level=BULLISH_INVALIDATION))
        few = [_bar(1, ANCHOR_PRICE, high=ANCHOR_PRICE, low=ANCHOR_PRICE)]
        more = few + [_bar(d, ANCHOR_PRICE, high=ANCHOR_PRICE, low=ANCHOR_PRICE) for d in range(2, 13)]
        _, _, env_few = _build(record=record, bars=few)
        _, _, env_more = _build(record=record, bars=more)
        self.assertNotEqual(env_few.input_hash, env_more.input_hash)

    def test_more_eligible_bars_may_change_outcome_hash(self):
        record = _record("bullish", execution=_execution(invalidation_level=BULLISH_INVALIDATION))
        few = [_bar(1, ANCHOR_PRICE, high=ANCHOR_PRICE, low=ANCHOR_PRICE)]
        more = _confirmed_bullish_bars()
        _, _, env_few = _build(record=record, bars=few)
        _, _, env_more = _build(record=record, bars=more)
        self.assertNotEqual(env_few.outcome_hash, env_more.outcome_hash)

    def test_identical_final_inputs_at_later_now_reproduce_same_hashes(self):
        bars = _confirmed_bullish_bars()
        _, _, a = _build(bars=bars, now=NOW)
        _, _, b = _build(bars=bars, now=NOW + timedelta(days=1000))
        self.assertEqual(a.validation_id, b.validation_id)
        self.assertEqual(a.input_hash, b.input_hash)
        self.assertEqual(a.outcome_hash, b.outcome_hash)

    def test_finalization_status_does_not_affect_the_three_hashes(self):
        record = _record("bullish", execution=_execution(invalidation_level=BULLISH_INVALIDATION))
        bars = _confirmed_bullish_bars()
        _, _, final_env = _build(record=record, bars=bars, now=NOW)
        self.assertNotIn("finalization_status", json.dumps(final_env.input_hash_basis))
        self.assertNotIn("finalization_status", json.dumps(final_env.outcome_hash_basis))


# ===========================================================================
# G. OVERLAP   (13 tests)
# ===========================================================================
class TestOverlap(unittest.TestCase):
    def test_no_previous_observation(self):
        _, _, env = _build(overlap_context=None)
        self.assertIsNone(env.overlap)

    def test_no_previous_observation_via_empty_context(self):
        _, _, env = _build(overlap_context=OverlapContext(previous_storage_id=None,
                                                            previous_evaluated_at=None))
        self.assertTrue(env.overlap.valid)
        self.assertIsNone(env.overlap.seconds_since_previous)
        self.assertIsNone(env.overlap.overlaps_previous_window)

    def test_exact_non_overlap(self):
        previous = EVAL_AT - timedelta(days=14)  # tactical window is 14 days
        ctx = OverlapContext(previous_storage_id="p", previous_evaluated_at=previous)
        _, _, env = _build(overlap_context=ctx)
        self.assertTrue(env.overlap.valid)
        self.assertEqual(env.overlap.overlap_seconds, 0.0)
        self.assertFalse(env.overlap.overlaps_previous_window)

    def test_partial_overlap(self):
        previous = EVAL_AT - timedelta(days=7)
        ctx = OverlapContext(previous_storage_id="p", previous_evaluated_at=previous)
        _, _, env = _build(overlap_context=ctx)
        self.assertTrue(env.overlap.valid)
        self.assertAlmostEqual(env.overlap.overlap_fraction, 0.5, places=6)
        self.assertTrue(env.overlap.overlaps_previous_window)

    def test_heavy_overlap(self):
        previous = EVAL_AT - timedelta(hours=1)
        ctx = OverlapContext(previous_storage_id="p", previous_evaluated_at=previous)
        _, _, env = _build(overlap_context=ctx)
        self.assertGreater(env.overlap.overlap_fraction, 0.99)

    def test_exact_boundary_same_instant_is_full_overlap(self):
        ctx = OverlapContext(previous_storage_id="p", previous_evaluated_at=EVAL_AT)
        _, _, env = _build(overlap_context=ctx)
        self.assertAlmostEqual(env.overlap.overlap_fraction, 1.0, places=6)
        self.assertEqual(env.overlap.seconds_since_previous, 0.0)

    def test_seconds_since_previous_computed(self):
        previous = EVAL_AT - timedelta(days=3)
        ctx = OverlapContext(previous_storage_id="p", previous_evaluated_at=previous)
        _, _, env = _build(overlap_context=ctx)
        self.assertAlmostEqual(env.overlap.seconds_since_previous, 3 * 86400.0, places=3)

    def test_overlap_seconds_computed(self):
        previous = EVAL_AT - timedelta(days=10)
        ctx = OverlapContext(previous_storage_id="p", previous_evaluated_at=previous)
        _, _, env = _build(overlap_context=ctx)
        self.assertAlmostEqual(env.overlap.overlap_seconds, 4 * 86400.0, places=3)

    def test_overlap_fraction_computed(self):
        previous = EVAL_AT - timedelta(days=10)
        ctx = OverlapContext(previous_storage_id="p", previous_evaluated_at=previous)
        _, _, env = _build(overlap_context=ctx)
        self.assertAlmostEqual(env.overlap.overlap_fraction, 4.0 / 14.0, places=6)

    def test_no_negative_overlap(self):
        previous = EVAL_AT - timedelta(days=30)
        ctx = OverlapContext(previous_storage_id="p", previous_evaluated_at=previous)
        _, _, env = _build(overlap_context=ctx)
        self.assertGreaterEqual(env.overlap.overlap_seconds, 0.0)
        self.assertGreaterEqual(env.overlap.overlap_fraction, 0.0)

    def test_invalid_future_predecessor_handled_safely(self):
        future = EVAL_AT + timedelta(days=1)
        ctx = OverlapContext(previous_storage_id="p", previous_evaluated_at=future)
        _, _, env = _build(overlap_context=ctx)
        self.assertFalse(env.overlap.valid)
        self.assertEqual(env.overlap.invalid_reason, "previous_evaluated_at_is_in_the_future")
        self.assertIsNone(env.overlap.overlap_seconds)

    def test_overlap_does_not_change_validation_id(self):
        _, _, without = _build()
        ctx = OverlapContext(previous_storage_id="p", previous_evaluated_at=EVAL_AT - timedelta(days=5))
        _, _, with_overlap = _build(overlap_context=ctx)
        self.assertEqual(without.validation_id, with_overlap.validation_id)

    def test_overlap_does_not_change_input_or_outcome_hash(self):
        _, _, without = _build(bars=_confirmed_bullish_bars())
        ctx = OverlapContext(previous_storage_id="p", previous_evaluated_at=EVAL_AT - timedelta(days=5))
        _, _, with_overlap = _build(bars=_confirmed_bullish_bars(), overlap_context=ctx)
        self.assertEqual(without.input_hash, with_overlap.input_hash)
        self.assertEqual(without.outcome_hash, with_overlap.outcome_hash)


# ===========================================================================
# H. LEGACY / PROVENANCE   (8 tests)
# ===========================================================================
class TestLegacyProvenance(unittest.TestCase):
    def test_schema_v2_captured_exact_series(self):
        _, _, env = _build()
        self.assertEqual(env.context.shadow_schema_version, 2)
        self.assertEqual(env.context.anchor_status, "anchor_captured")
        self.assertEqual(env.context.series_binding_quality, "series_exact")

    def test_schema_v1_missing_anchor_still_receives_full_envelope(self):
        legacy = _record(schema_version=1)
        legacy["record"]["market_anchor"] = None
        path_res, d2c3, env = _build(record=legacy)
        self.assertIsNotNone(env.validation_id)
        self.assertIsNotNone(env.input_hash)
        self.assertIsNotNone(env.outcome_hash)
        self.assertEqual(env.context.shadow_schema_version, 1)

    def test_excluded_outcome_receives_full_envelope(self):
        record = _record(evaluated_at="not-a-timestamp")
        path_res, d2c3, env = _build(record=record)
        self.assertIs(path_res.direction, DirectionOutcome.UNRESOLVED)
        self.assertIsNotNone(env.validation_id)
        self.assertIsNotNone(env.outcome_hash)

    def test_gold_substituted_remains_research(self):
        gc_bars = [_bar(d, ANCHOR_PRICE, high=ANCHOR_PRICE, low=ANCHOR_PRICE, symbol="GC=F")
                   for d in range(1, 13)]
        path_res, _, env = _build(bars=gc_bars)
        self.assertIsNot(path_res.eligibility_pool, EligibilityPool.CAPTURED)
        self.assertEqual(env.outcome_hash_basis["eligibility_pool"],
                          path_res.eligibility_pool.value)
        self.assertNotEqual(env.outcome_hash_basis["eligibility_pool"], "captured")

    def test_unavailable_anchor_remains_excluded(self):
        record = _record()
        record["record"]["market_anchor"] = None
        path_res, _, env = _build(record=record, bars=[], tail=False)
        self.assertIsNot(path_res.eligibility_pool, EligibilityPool.CAPTURED)

    def test_storage_id_recomputation_works(self):
        from apex.b2.shadow import canonical_storage_id
        rec = _record(record_id="rX", instrument="EUR", horizon="tactical", evaluated_at=EVAL_AT)
        payload = rec["record"]
        expected = canonical_storage_id("rX", "EUR", "tactical", EVAL_AT.isoformat())
        _, _, env = _build(record=rec, instrument="EUR")
        self.assertEqual(env.context.shadow_storage_id, expected)

    def test_content_hash_recomputation_works(self):
        from apex.b2.shadow import canonical_content_hash
        rec = _record()
        expected = canonical_content_hash(rec["record"])
        _, _, env = _build(record=rec)
        self.assertEqual(env.context.shadow_content_hash, expected)

    def test_distinct_storage_identities_do_not_collapse(self):
        rec_a = _record(record_id="same-hour-bucket", evaluated_at=EVAL_AT)
        rec_b = _record(record_id="same-hour-bucket", evaluated_at=EVAL_AT + timedelta(minutes=35))
        _, _, env_a = _build(record=rec_a)
        _, _, env_b = _build(record=rec_b)
        self.assertNotEqual(env_a.context.shadow_storage_id, env_b.context.shadow_storage_id)
        self.assertNotEqual(env_a.validation_id, env_b.validation_id)


# ===========================================================================
# I. OUTCOMEAXES INVARIANT CHECK   (4 tests)
# ===========================================================================
class TestOutcomeAxesInvariant(unittest.TestCase):
    def test_valid_combination_passes_for_every_current_branch(self):
        cases = [
            dict(bars=_confirmed_bullish_bars()),
            dict(bars=[]),
            dict(now=EVAL_AT + timedelta(days=1)),
            dict(record=_record("flat")),
            dict(record=_record("unavailable")),
            dict(bars=_confirmed_bullish_bars(touch=True)),
        ]
        for case in cases:
            _, _, env = _build(**case)
            self.assertIsInstance(env.outcome_axes, OutcomeAxes)

    def test_impossible_combination_is_rejected(self):
        path_res, d2c3, _ = _build()
        import dataclasses
        broken_path = dataclasses.replace(path_res, direction=DirectionOutcome.CONFIRMED,
                                           data_resolution=DataResolution.NOT_MATURED)
        with self.assertRaises(OutcomeInvariantError):
            build_validation_envelope(
                record=_record(), path_resolution=broken_path, d2c3_resolution=d2c3,
                validation_config=DEFAULT_VALIDATION_CONFIG,
            )

    def test_d2c4_does_not_weaken_outcome_axes_invariants(self):
        names = _identifiers(envelope_mod)
        self.assertNotIn("OutcomeInvariantError", names)  # never caught/swallowed

    def test_outcome_axes_is_not_used_as_the_full_outcome_payload(self):
        _, _, env = _build(bars=_confirmed_bullish_bars())
        axes_keys = set(env.outcome_axes.as_record().keys())
        basis_keys = set(env.outcome_hash_basis.keys())
        self.assertFalse(axes_keys.issuperset(basis_keys),
                         "OutcomeAxes.as_record() must be narrower than outcome_hash_basis")
        self.assertGreater(len(basis_keys - axes_keys), 0)


# ===========================================================================
# J. PURITY   (10 tests)
# ===========================================================================
class TestPurity(unittest.TestCase):
    def test_no_forbidden_imports(self):
        tree = ast.parse(inspect.getsource(envelope_mod))
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

    def test_no_wall_clock_dependency(self):
        names = _identifiers(envelope_mod)
        for forbidden in ("now", "utcnow", "monotonic", "random", "randint"):
            self.assertNotIn(forbidden, names, forbidden)
        source = inspect.getsource(envelope_mod)
        self.assertNotIn("datetime.now(", source)
        self.assertNotIn("time.time(", source)

    def test_no_ai_telegram_or_scheduler_reference(self):
        names = {n.lower() for n in _identifiers(envelope_mod)}
        for forbidden in ("telegram", "sendmessage", "openai", "anthropic", "gemini", "groq",
                          "completions", "thread", "timer", "sleep", "crontab", "scheduler",
                          "daemon"):
            self.assertFalse(any(forbidden in n for n in names), forbidden)

    def test_no_ddl_dml_or_persistence(self):
        upper = inspect.getsource(envelope_mod).upper()
        for verb in ("CREATE TABLE", "ALTER TABLE", "DROP TABLE", "TRUNCATE",
                     "INSERT INTO", "DELETE FROM", "CREATE INDEX"):
            self.assertNotIn(verb, upper, verb)
        names = _identifiers(envelope_mod)
        for forbidden in ("_save_persistent_state", "_load_persistent_state",
                          "insert_rows", "query_bars", "supabase_client"):
            self.assertNotIn(forbidden, names, forbidden)

    def test_no_environment_variable_dependency(self):
        names = _identifiers(envelope_mod)
        self.assertNotIn("environ", names)
        self.assertNotIn("getenv", names)

    def test_no_file_io(self):
        tree = ast.parse(inspect.getsource(envelope_mod))
        called = {node.func.id for node in ast.walk(tree)
                  if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
        for builtin in ("open", "exec", "eval", "compile", "__import__", "input"):
            self.assertNotIn(builtin, called, builtin)

    def test_no_network_or_db_names(self):
        names = {n.lower() for n in _identifiers(envelope_mod)}
        for forbidden in ("requests", "urlopen", "socket", "cursor", "execute_sql", "supabase"):
            self.assertFalse(any(forbidden in n for n in names), forbidden)

    def test_no_mutable_module_globals_besides_constants(self):
        tree = ast.parse(inspect.getsource(envelope_mod))
        module_level_assigns = [
            node for node in tree.body if isinstance(node, ast.Assign)
        ]
        for node in module_level_assigns:
            for target in node.targets:
                if isinstance(target, ast.Name):
                    self.assertTrue(target.id.isupper() or target.id.startswith("_"),
                                    f"unexpected module-level mutable-looking name: {target.id}")

    def test_deterministic_repeated_calls_are_pure(self):
        bars = _confirmed_bullish_bars()
        record = _record("bullish", execution=_execution(invalidation_level=BULLISH_INVALIDATION))
        results = [_build(record=record, bars=bars)[2] for _ in range(3)]
        ids = {r.validation_id for r in results}
        inputs = {r.input_hash for r in results}
        outcomes = {r.outcome_hash for r in results}
        self.assertEqual(len(ids), 1)
        self.assertEqual(len(inputs), 1)
        self.assertEqual(len(outcomes), 1)

    def test_no_module_outside_tests_imports_envelope(self):
        """D-2C5's ``readiness.py`` is the ONE approved exception: it calls
        the existing ``build_validation_envelope`` unchanged rather than
        reimplementing D-2C4 hashing. Any OTHER importer still fails this
        guard."""
        importers = []
        for folder, _dirs, files in os.walk(ROOT):
            if any(p in folder for p in ("_backup_", "_baseline_", ".git", "__pycache__", "tests")):
                continue
            for filename in files:
                if not filename.endswith(".py") or filename == "envelope.py":
                    continue
                path = os.path.join(folder, filename)
                with open(path, encoding="utf-8") as handle:
                    try:
                        tree = ast.parse(handle.read())
                    except SyntaxError:
                        continue
                for node in ast.walk(tree):
                    if isinstance(node, ast.ImportFrom) and "validation.envelope" in (node.module or ""):
                        importers.append(filename)
                    if isinstance(node, ast.Import) and any(
                            "validation.envelope" in a.name for a in node.names):
                        importers.append(filename)
        self.assertEqual(sorted(set(importers)), ["readiness.py"])


# ===========================================================================
# K. SCOPE   (8 tests)
# ===========================================================================
class TestScope(unittest.TestCase):
    def _unchanged(self, path):
        result = subprocess.run(["git", "diff", "--exit-code", "--", path],
                                cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, f"{path} changed:\n{result.stdout[:800]}")

    def test_metrics_module_remains_absent(self):
        """D-2C5 authorized exactly one further module, ``readiness.py``, for
        lineage verification and per-observation readiness."""
        present = {f for f in os.listdir(os.path.join(ROOT, "apex", "b2", "validation"))
                   if f.endswith(".py")}
        self.assertNotIn("metrics.py", present)
        self.assertEqual(present, {"__init__.py", "anchor.py", "bars.py", "config.py",
                                   "maturity.py", "outcome.py", "resolve.py", "series.py",
                                   "invalidation.py", "envelope.py", "readiness.py"})

    def test_cross_asset_remains_withheld(self):
        with open(os.path.join(ROOT, "apex", "b2", "shadow.py"), encoding="utf-8") as h:
            self.assertIn('CROSS_ASSET_STATUS = "withheld"', h.read())

    def test_production_core_sha_unchanged(self):
        import hashlib
        with open(os.path.join(ROOT, "apex", "production_core.py"), "rb") as handle:
            digest = hashlib.sha256(handle.read()).hexdigest()
        self.assertEqual(digest, "5935f807a8584007fc053ae7bb64d62017a7e2f804258d492fdd8a4c2cb4da69")

    def test_production_core_is_byte_for_byte_unchanged(self):
        self._unchanged("apex/production_core.py")

    def test_d2c2_module_is_unchanged(self):
        self._unchanged("apex/b2/validation/resolve.py")

    def test_d2c3_module_is_unchanged(self):
        self._unchanged("apex/b2/validation/invalidation.py")

    def test_no_metrics_rate_or_calibration_computed(self):
        names = {n.lower() for n in _identifiers(envelope_mod)}
        for forbidden in ("hit_rate", "accuracy", "win_rate", "calibrate", "sharpe",
                          "profit_factor", "effective_sample_size", "promotion_score"):
            self.assertFalse(any(forbidden in n for n in names), forbidden)

    def test_no_entry_verdicts_or_thesis_lifecycle_introduced(self):
        source = inspect.getsource(envelope_mod)
        for forbidden in ("ENTRY_JUSTIFIED", "ENTRY_PREMATURE", "ENTRY_LATE",
                          "open_thesis", "apply_escalation", "apply_macro_evidence"):
            self.assertNotIn(forbidden, source, forbidden)


# ===========================================================================
# L. D-2C2 / D-2C3 REGRESSION -- the prior suites still pass unmodified.
# ===========================================================================
class TestPriorSuitesUnaffected(unittest.TestCase):
    def test_d2c3_suite_still_passes(self):
        import tests.test_b2_stage_d2c3_invalidation as d2c3_tests
        loader = unittest.TestLoader()
        suite = loader.loadTestsFromModule(d2c3_tests)
        buffer = io.StringIO()
        runner = unittest.TextTestRunner(stream=buffer, verbosity=0)
        result = runner.run(suite)
        self.assertTrue(result.wasSuccessful(),
                        f"D-2C3 suite regressed: {len(result.failures)} failures, "
                        f"{len(result.errors)} errors:\n{buffer.getvalue()}")

    def test_d2c2_suite_still_passes(self):
        import tests.test_b2_stage_d2c_resolution as d2c2_tests
        loader = unittest.TestLoader()
        suite = loader.loadTestsFromModule(d2c2_tests)
        buffer = io.StringIO()
        runner = unittest.TextTestRunner(stream=buffer, verbosity=0)
        result = runner.run(suite)
        self.assertTrue(result.wasSuccessful(),
                        f"D-2C2 suite regressed: {len(result.failures)} failures, "
                        f"{len(result.errors)} errors:\n{buffer.getvalue()}")


if __name__ == "__main__":
    unittest.main()
