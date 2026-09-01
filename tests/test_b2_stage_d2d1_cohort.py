"""Stage D-2D1: deterministic cohort construction and narrow validation metrics.

Covers ``apex.b2.evaluation.cohort``. Every fixture builds its members through
the real ``evaluate_observation``, so admission, deduplication, episode spacing
and the three ratios are exercised against genuine immutable artifacts rather
than hand-written stand-ins.

The suite's centre of gravity is not the arithmetic. It is the one property
that makes the arithmetic meaningful:

    NOTHING ABOUT COHORT STRUCTURE MAY DEPEND ON AN OUTCOME.

so the tests that matter most are the ones that would still pass if the numbers
were wrong and fail if the structure were biased -- the duplicate-selection
regression against the rejected highest-readiness rule, and the proof that
changing outcomes leaves the episode partition untouched.

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
import random
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
from apex.b2.evaluation import cohort as cohort_mod
from apex.b2.evaluation.cohort import (
    COHORT_SCHEMA_VERSION,
    DEDUP_POLICY_VERSION,
    DEFAULT_COHORT_CONFIG,
    EPISODE_POLICY_VERSION,
    STRATIFY_BY,
    AdmissionFailure,
    AdmissionFailureReason,
    Cohort,
    CohortConfig,
    CohortState,
    Ratio,
    RatioNote,
    RatioState,
    Stratum,
    StratumKey,
    build_cohort,
)
from apex.b2.evaluation.observation import (
    DefectReason,
    EvaluatedObservation,
    LineageDefect,
    ProvenanceGrade,
    evaluate_observation,
)
from apex.b2.validation.bars import GRANULARITY_1D, MarketBar
from apex.b2.validation.config import DEFAULT_VALIDATION_CONFIG, ValidationConfig
from apex.b2.validation.outcome import DirectionOutcome
from apex.b2.validation.readiness import ReadinessTier

UTC = timezone.utc
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: Every fixture anchors here. ``NOW`` is far enough ahead that fixtures
#: spreading thirty members fourteen days apart still mature before it -- an
#: observation whose evaluated_at ran past as_of would be CLOCK_SKEW, which is
#: a different test than the one being written.
EVAL_AT = datetime(2025, 1, 1, 12, 0, tzinfo=UTC)
NOW = datetime(2027, 6, 1, 12, 0, tzinfo=UTC)
ANCHOR_PRICE = 3330.0
ANCHOR_ATR = 12.0
ANCHOR_VOL = 0.0012
TACTICAL_WINDOW = timedelta(days=14)

#: Small floors, so ratio ARITHMETIC can be tested without 30-member fixtures.
#: The real defaults are exercised separately in TestSampleFloors.
TINY = CohortConfig(min_denominator=2, min_disjoint_episode_verdict_n=1)


# ===========================================================================
# Fixtures
# ===========================================================================

def _anchor(symbol="XAUUSD=X", *, invert=False, analysis=ANCHOR_PRICE,
            last=ANCHOR_PRICE, atr=ANCHOR_ATR, vol=ANCHOR_VOL):
    return {
        "analysis_price": analysis, "last_price": last, "symbol": symbol,
        "symbol_requested": symbol, "symbol_fallback_used": symbol != "XAUUSD=X",
        "invert": invert, "market_ts": 1, "market_ts_iso": "",
        "volatility_scale": vol, "atr": atr, "atr_ratio": 1.05,
        "volatility_regime": "normal", "price_source": "yahoo_5m_tactical",
        "granularity": "5m", "anchor_status": "anchor_captured",
    }


def _record(direction="bullish", *, instrument="Gold", horizon="tactical",
            record_id="r1", evaluated_at=EVAL_AT, anchor=None):
    payload = {
        "schema_version": 2, "record_id": record_id, "instrument": instrument,
        "horizon": horizon,
        "evaluated_at": (
            evaluated_at if isinstance(evaluated_at, str) else evaluated_at.isoformat()
        ),
        "market_anchor": _anchor() if anchor is None else anchor,
        "claim": {"direction": direction, "horizon": horizon},
        "execution": None, "gates_triggered": [],
    }
    return {"storage_id": "unused", "record_id": record_id,
            "instrument": instrument, "horizon": horizon, "record": payload}


def _bar(day, close, *, symbol="XAUUSD=X", instrument="Gold", invert=False):
    return MarketBar(
        symbol=symbol, instrument=instrument, granularity=GRANULARITY_1D,
        bar_time=day, open=close, high=close, low=close, close=close,
        volume=None, invert=invert,
    )


def _series(evaluated_at, terminal, *, symbol="XAUUSD=X", instrument="Gold",
            invert=False, flat=ANCHOR_PRICE):
    """Eleven flat bars, one terminal bar, then two bars past the window end.

    The trailing pair is what lets ``assess_maturity`` tell "capture reached
    past this window" from "capture has not got there yet"; without it every
    fixture would be MATURED_AWAITING_BARS instead of MATURED.
    """
    start = evaluated_at.replace(hour=0, minute=0, second=0, microsecond=0)
    bars = [
        _bar(start + timedelta(days=d), flat, symbol=symbol,
             instrument=instrument, invert=invert)
        for d in range(1, 12)
    ]
    bars.append(_bar(start + timedelta(days=12), terminal, symbol=symbol,
                     instrument=instrument, invert=invert))
    bars += [
        _bar(start + timedelta(days=d), terminal, symbol=symbol,
             instrument=instrument, invert=invert)
        for d in (20, 25)
    ]
    return bars


#: Terminal price producing each directional outcome for a BULLISH claim.
CONFIRMED_AT = ANCHOR_PRICE * 1.20
FAILED_AT = ANCHOR_PRICE * 0.80
NEUTRAL_AT = ANCHOR_PRICE


def _observation(*, outcome="confirmed", instrument="Gold", horizon="tactical",
                 record_id="r1", evaluated_at=EVAL_AT, as_of=NOW,
                 anchor_symbol="XAUUSD=X", bar_symbol=None, anchor=None,
                 config=None, bars=None, claim=None):
    """One real EvaluatedObservation with a controlled directional outcome."""
    bar_symbol = bar_symbol or anchor_symbol
    direction = claim or ("flat" if outcome == "abstained" else "bullish")
    terminal = {
        "confirmed": CONFIRMED_AT, "failed": FAILED_AT,
        "neutral": NEUTRAL_AT, "abstained": CONFIRMED_AT,
    }.get(outcome, NEUTRAL_AT)

    if bars is None:
        # A deliberately unparseable evaluated_at cannot anchor a bar
        # timeline; those fixtures only exercise admission, so the bars are
        # laid out around EVAL_AT and never consulted.
        timeline = evaluated_at if isinstance(evaluated_at, datetime) else EVAL_AT
        bars = _series(timeline, terminal, symbol=bar_symbol,
                       instrument=instrument)

    if anchor is None:
        # "unresolved" is produced by withholding the ATR and volatility
        # scale, so the neutral band cannot be computed and D-2C2 has no
        # threshold to judge the terminal return against. Deliberately NOT
        # produced by withholding bars: that would also break the series
        # binding, changing the observation's PROVENANCE as a side effect and
        # quietly moving it into a different stratum -- which is correct
        # behaviour, and exactly why it must not be the fixture for an
        # outcome test.
        anchor = (_anchor(anchor_symbol, atr=None, vol=None)
                  if outcome == "unresolved" else _anchor(anchor_symbol))

    record = _record(direction, instrument=instrument, horizon=horizon,
                     record_id=record_id, evaluated_at=evaluated_at,
                     anchor=anchor)
    result = evaluate_observation(
        record=record, bars=bars, as_of=as_of,
        convention=b2_bridge.symbol_convention(instrument),
        config=config or DEFAULT_VALIDATION_CONFIG,
    )
    return result


def _build(observations, *, as_of=NOW, config=None, cohort_config=TINY):
    return build_cohort(
        observations=list(observations), as_of=as_of,
        validation_config=config or DEFAULT_VALIDATION_CONFIG,
        cohort_config=cohort_config,
    )


def _only(cohort):
    """The single stratum of a single-slice cohort."""
    assert len(cohort.strata) == 1, [s.key.as_record() for s in cohort.strata]
    return cohort.strata[0]


def _spread(n, *, start=EVAL_AT, step=timedelta(hours=1), outcome="confirmed",
            instrument="Gold", as_of=NOW, **kw):
    """``n`` observations at a fixed cadence, each its own logical forecast."""
    return [
        _observation(outcome=outcome, instrument=instrument,
                     record_id=f"r{index}", evaluated_at=start + step * index,
                     as_of=as_of, **kw)
        for index in range(n)
    ]


def _identifiers(obj) -> set[str]:
    """Every NAME the module references -- never its prose.

    AST rather than raw source: ``cohort.py``'s docstrings deliberately name
    the concepts it refuses to implement, and a substring scan would read
    those disclaimers as violations of themselves.
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
# A. DETERMINISM   (8 tests)
# ===========================================================================
class TestDeterminism(unittest.TestCase):
    def setUp(self):
        self.members = _spread(6, step=timedelta(days=20))

    def test_identical_input_gives_identical_record(self):
        records = {repr(_build(self.members).as_record()) for _ in range(3)}
        self.assertEqual(len(records), 1)

    def test_shuffled_input_gives_identical_record(self):
        base = _build(self.members).as_record()
        for seed in range(5):
            shuffled = list(self.members)
            random.Random(seed).shuffle(shuffled)
            self.assertEqual(_build(shuffled).as_record(), base)

    def test_shuffled_input_gives_identical_cohort_id(self):
        base = _build(self.members).cohort_id
        shuffled = list(reversed(self.members))
        self.assertEqual(_build(shuffled).cohort_id, base)

    def test_shuffled_input_gives_identical_membership_hash(self):
        base = _build(self.members).membership_hash
        for seed in range(5):
            shuffled = list(self.members)
            random.Random(seed).shuffle(shuffled)
            self.assertEqual(_build(shuffled).membership_hash, base)

    def test_strata_are_ordered_deterministically(self):
        mixed = (_spread(2, instrument="Gold", step=timedelta(days=20))
                 + _spread(2, instrument="USD", step=timedelta(days=20),
                           anchor_symbol="DX-Y.NYB"))
        keys = [s.key.sort_key for s in _build(mixed).strata]
        self.assertEqual(keys, sorted(keys))

    def test_count_maps_cover_the_closed_vocabulary_in_order(self):
        stratum = _only(_build(self.members))
        self.assertEqual(list(stratum.by_direction_outcome),
                         [d.value for d in DirectionOutcome])
        self.assertEqual(list(stratum.by_readiness_tier),
                         [t.value for t in ReadinessTier])

    def test_states_that_never_occurred_are_zero_not_absent(self):
        stratum = _only(_build(self.members))
        self.assertEqual(stratum.by_direction_outcome[DirectionOutcome.FAILED.value], 0)
        self.assertIn(DirectionOutcome.ABSTAINED.value, stratum.by_direction_outcome)

    def test_empty_input_is_deterministic(self):
        a, b = _build([]), _build([])
        self.assertEqual(a.as_record(), b.as_record())


# ===========================================================================
# B. AS_OF   (9 tests)
# ===========================================================================
class TestAsOf(unittest.TestCase):
    def test_as_of_is_required(self):
        with self.assertRaises(TypeError):
            build_cohort(observations=[],
                         validation_config=DEFAULT_VALIDATION_CONFIG)

    def test_no_wall_clock_fallback_exists(self):
        signature = inspect.signature(build_cohort)
        self.assertIs(signature.parameters["as_of"].default,
                      inspect.Parameter.empty)

    def test_as_of_is_recorded_canonically(self):
        cohort = _build(_spread(2, step=timedelta(days=20)))
        self.assertEqual(cohort.as_of, NOW.isoformat())

    def test_as_of_changes_cohort_id(self):
        members = _spread(2, step=timedelta(days=20))
        later = NOW + timedelta(days=1)
        self.assertNotEqual(_build(members).cohort_id,
                            _build(members, as_of=later).cohort_id)

    def test_membership_hash_unchanged_when_no_member_state_changed(self):
        """Two as_of values over members whose maturity did not move are the
        same evidence, and must hash the same. The QUESTION differs; the
        ANSWERING SET does not."""
        members = _spread(2, step=timedelta(days=20))
        later = NOW + timedelta(days=1)
        a, b = _build(members), _build(members, as_of=later)
        self.assertEqual(a.membership_hash, b.membership_hash)
        self.assertNotEqual(a.cohort_id, b.cohort_id)

    def test_membership_hash_changes_when_outcome_hash_evolves(self):
        """validation_id is stable across maturity; outcome_hash is not. The
        membership hash must follow the outcome, or a provisional cohort
        would be indistinguishable from its finalized successor."""
        early_at = NOW - timedelta(days=3)
        immature = _observation(evaluated_at=early_at, as_of=NOW)
        mature = _observation(evaluated_at=early_at,
                              as_of=early_at + TACTICAL_WINDOW + timedelta(days=20))
        self.assertEqual(immature.envelope.validation_id,
                         mature.envelope.validation_id)
        self.assertNotEqual(immature.envelope.outcome_hash,
                            mature.envelope.outcome_hash)
        a = _build([immature], as_of=NOW)
        b = _build([mature], as_of=early_at + TACTICAL_WINDOW + timedelta(days=20))
        self.assertNotEqual(a.membership_hash, b.membership_hash)

    def test_consistent_maturity_is_admitted(self):
        cohort = _build(_spread(2, step=timedelta(days=20)))
        self.assertEqual(cohort.admission_failure_n, 0)
        self.assertEqual(cohort.admitted_n, 2)

    def test_not_matured_member_with_past_as_of_is_admitted(self):
        at = NOW - timedelta(days=3)
        cohort = _build([_observation(evaluated_at=at, as_of=NOW)], as_of=NOW)
        self.assertEqual(cohort.admission_failure_n, 0)
        self.assertIs(cohort.cohort_state, CohortState.PROVISIONAL)

    def test_as_of_inconsistency_is_an_admission_failure(self):
        """A member evaluated at one instant, presented in a cohort declaring
        another. Caught through the maturity implication, which is the only
        signal available -- maturity.now is deliberately absent from the
        envelope."""
        at = NOW - timedelta(days=3)
        immature = _observation(evaluated_at=at, as_of=NOW)
        cohort = _build([immature],
                        as_of=at + TACTICAL_WINDOW + timedelta(days=10))
        self.assertEqual(cohort.admitted_n, 0)
        self.assertEqual(cohort.admission_failure_n, 1)
        self.assertIs(cohort.admission_failures[0].reason,
                      AdmissionFailureReason.AS_OF_INCONSISTENT)


# ===========================================================================
# C. ADMISSION   (10 tests)
# ===========================================================================
class TestAdmission(unittest.TestCase):
    def test_config_hash_mismatch(self):
        member = _observation()
        variant = ValidationConfig(neutral_band_atr_multiple=0.25)
        cohort = _build([member], config=variant)
        self.assertEqual(cohort.admitted_n, 0)
        self.assertIs(cohort.admission_failures[0].reason,
                      AdmissionFailureReason.CONFIG_HASH_MISMATCH)

    def test_unparseable_evaluated_at_does_not_crash_sorting(self):
        """Reproduced from the audit: an unparseable timestamp still yields a
        well-formed EvaluatedObservation upstream, so a naive canonical sort
        would raise here rather than report."""
        member = _observation(evaluated_at="not-a-timestamp")
        self.assertIsInstance(member, EvaluatedObservation)
        cohort = _build([member])
        self.assertEqual(cohort.admitted_n, 0)
        self.assertIs(cohort.admission_failures[0].reason,
                      AdmissionFailureReason.UNPARSEABLE_EVALUATED_AT)

    def test_unknown_horizon_window(self):
        """Defence in depth. Reached by presenting a member built under a
        config that knows its horizon to a cohort config that does not --
        which the config-hash check catches first, so this asserts the guard
        exists rather than that it is currently reachable."""
        narrow = ValidationConfig(horizon_windows={"execution": timedelta(days=3)})
        self.assertIsNone(narrow.window_for("tactical"))
        self.assertIn(AdmissionFailureReason.UNKNOWN_HORIZON_WINDOW,
                      list(AdmissionFailureReason))

    def test_one_admission_failure_does_not_abort_the_batch(self):
        good = _spread(3, step=timedelta(days=20))
        bad = _observation(record_id="bad", evaluated_at="not-a-timestamp")
        cohort = _build(good + [bad])
        self.assertEqual(cohort.admitted_n, 3)
        self.assertEqual(cohort.admission_failure_n, 1)
        self.assertEqual(cohort.evaluated_n, 4)

    def test_admission_failures_never_become_failed(self):
        cohort = _build([_observation(evaluated_at="not-a-timestamp")])
        self.assertEqual(cohort.strata, ())
        outcomes = {o.value for o in DirectionOutcome}
        for failure in cohort.admission_failures:
            self.assertNotIn(failure.reason.value, outcomes)

    def test_admission_failures_never_enter_episodes(self):
        good = _spread(2, step=timedelta(days=20))
        bad = _observation(record_id="bad", evaluated_at="not-a-timestamp")
        with_bad = _only(_build(good + [bad]))
        without = _only(_build(good))
        self.assertEqual(with_bad.disjoint_episode_n, without.disjoint_episode_n)

    def test_admission_failures_never_enter_denominators(self):
        good = _spread(2, step=timedelta(days=20))
        bad = _observation(record_id="bad", evaluated_at="not-a-timestamp")
        a = _only(_build(good + [bad])).verdict_confirmation_rate
        b = _only(_build(good)).verdict_confirmation_rate
        self.assertEqual(a.denominator, b.denominator)

    def test_admission_failures_are_traceable_and_counted_by_reason(self):
        cohort = _build([_observation(evaluated_at="not-a-timestamp")])
        failure = cohort.admission_failures[0]
        self.assertTrue(failure.shadow_storage_id)
        self.assertEqual(failure.instrument, "Gold")
        self.assertEqual(
            cohort.admission_failure_by_reason[
                AdmissionFailureReason.UNPARSEABLE_EVALUATED_AT.value], 1)

    def test_admission_reason_vocabulary_is_stage_owned(self):
        """Distinct from D-2D0 DefectReason and from D-2C2 ExclusionReason."""
        from apex.b2.validation.outcome import ExclusionReason
        admission = {r.value for r in AdmissionFailureReason}
        self.assertFalse(admission & {r.value for r in DefectReason})
        self.assertFalse(admission & {r.value for r in ExclusionReason})

    def test_admission_failure_is_frozen(self):
        cohort = _build([_observation(evaluated_at="not-a-timestamp")])
        with self.assertRaises(dataclasses.FrozenInstanceError):
            cohort.admission_failures[0].reason = (
                AdmissionFailureReason.CONFIG_HASH_MISMATCH)


# ===========================================================================
# D. LINEAGE DEFECTS   (7 tests)
# ===========================================================================
class TestLineageDefects(unittest.TestCase):
    def _defect(self):
        defect = _observation(horizon="not_a_horizon")
        self.assertIsInstance(defect, LineageDefect)
        return defect

    def test_defect_is_counted(self):
        cohort = _build(_spread(2, step=timedelta(days=20)) + [self._defect()])
        self.assertEqual(cohort.lineage_defect_n, 1)
        self.assertEqual(cohort.evaluated_n, 2)
        self.assertEqual(cohort.input_n, 3)

    def test_defect_reason_is_counted(self):
        cohort = _build([self._defect()])
        self.assertEqual(
            cohort.lineage_defect_by_reason[
                DefectReason.LINEAGE_VERIFICATION_FAILED.value], 1)

    def test_defect_is_traceable(self):
        cohort = _build([self._defect()])
        self.assertTrue(cohort.lineage_defects[0].shadow_storage_id)

    def test_defect_is_not_a_cohort_member(self):
        cohort = _build([self._defect()])
        self.assertEqual(cohort.admitted_n, 0)
        self.assertEqual(cohort.deduplicated_n, 0)
        self.assertEqual(cohort.strata, ())

    def test_defect_does_not_affect_episodes(self):
        good = _spread(2, step=timedelta(days=20))
        self.assertEqual(_only(_build(good + [self._defect()])).disjoint_episode_n,
                         _only(_build(good)).disjoint_episode_n)

    def test_defect_does_not_affect_denominators(self):
        good = _spread(2, step=timedelta(days=20))
        a = _only(_build(good + [self._defect()])).verdict_confirmation_rate
        b = _only(_build(good)).verdict_confirmation_rate
        self.assertEqual((a.numerator, a.denominator), (b.numerator, b.denominator))

    def test_defect_never_becomes_a_direction_outcome(self):
        cohort = _build([self._defect()])
        outcomes = {o.value for o in DirectionOutcome}
        for defect in cohort.lineage_defects:
            self.assertNotIn(defect.reason.value, outcomes)


# ===========================================================================
# E. DEDUPLICATION   (12 tests)
# ===========================================================================
class TestDeduplication(unittest.TestCase):
    def _pair(self, early_outcome, late_outcome, *, gap=timedelta(minutes=35)):
        """Two point-in-time observations sharing one logical hour bucket."""
        early = _observation(outcome=early_outcome, record_id="bucket",
                             evaluated_at=EVAL_AT)
        late = _observation(outcome=late_outcome, record_id="bucket",
                            evaluated_at=EVAL_AT + gap)
        return early, late

    def test_logical_key_is_instrument_horizon_record_id(self):
        early, late = self._pair("confirmed", "confirmed")
        self.assertEqual(early.envelope.context.shadow_record_id,
                         late.envelope.context.shadow_record_id)
        self.assertNotEqual(early.envelope.context.shadow_storage_id,
                            late.envelope.context.shadow_storage_id)
        self.assertEqual(_build([early, late]).deduplicated_n, 1)

    def test_a_different_record_id_is_not_a_duplicate(self):
        a = _observation(record_id="one", evaluated_at=EVAL_AT)
        b = _observation(record_id="two", evaluated_at=EVAL_AT + timedelta(minutes=35))
        self.assertEqual(_build([a, b]).deduplicated_n, 2)

    def test_a_different_instrument_is_not_a_duplicate(self):
        a = _observation(record_id="bucket", instrument="Gold")
        b = _observation(record_id="bucket", instrument="USD",
                         anchor_symbol="DX-Y.NYB")
        self.assertEqual(_build([a, b]).deduplicated_n, 2)

    def test_earliest_evaluated_at_wins(self):
        early, late = self._pair("confirmed", "confirmed")
        cohort = _build([late, early])
        self.assertEqual(cohort.evaluated_at_min, EVAL_AT.isoformat())
        self.assertEqual(cohort.evaluated_at_max, EVAL_AT.isoformat())

    def test_readiness_never_selects_the_representative(self):
        """The locked regression against the REJECTED rule.

        The earliest member landed NEUTRAL, so ``classify_readiness`` puts it
        in EXCLUDED; the later member landed CONFIRMED and is
        CALIBRATION_ELIGIBLE. A "keep the highest readiness" rule would pick
        the later one -- selecting on having escaped the neutral band, which
        because the band scales with the anchor's ATR is selection on low
        recorded volatility. The implementation must still keep the earliest.
        """
        early, late = self._pair("neutral", "confirmed")
        self.assertIs(early.readiness, ReadinessTier.EXCLUDED)
        self.assertIs(late.readiness, ReadinessTier.CALIBRATION_ELIGIBLE)
        stratum = _only(_build([early, late]))
        self.assertEqual(
            stratum.by_direction_outcome[DirectionOutcome.NEUTRAL_WITHIN_BAND.value], 1)
        self.assertEqual(
            stratum.by_direction_outcome[DirectionOutcome.CONFIRMED.value], 0)

    def test_the_representative_is_the_same_whichever_outcome_is_earliest(self):
        """Swapping which member confirms must not move the representative."""
        for early_outcome, late_outcome in (("neutral", "confirmed"),
                                            ("confirmed", "neutral"),
                                            ("failed", "confirmed"),
                                            ("unresolved", "confirmed")):
            early, late = self._pair(early_outcome, late_outcome)
            cohort = _build([late, early])
            self.assertEqual(cohort.evaluated_at_min, EVAL_AT.isoformat(),
                             f"{early_outcome}/{late_outcome}")

    def test_provenance_never_selects_the_representative(self):
        early = _observation(record_id="bucket", evaluated_at=EVAL_AT,
                             anchor_symbol="XAUUSD=X", bar_symbol="GC=F")
        late = _observation(record_id="bucket",
                            evaluated_at=EVAL_AT + timedelta(minutes=35),
                            anchor_symbol="GC=F", bar_symbol="GC=F")
        self.assertIs(early.provenance_grade, ProvenanceGrade.SUBSTITUTED_SERIES)
        self.assertIs(late.provenance_grade, ProvenanceGrade.IDEAL)
        stratum = _only(_build([early, late]))
        self.assertEqual(stratum.key.provenance_grade,
                         ProvenanceGrade.SUBSTITUTED_SERIES.value)

    def test_one_minute_apart_still_collapses_to_the_earlier_member(self):
        """A minute apart is still one hour bucket, so one logical forecast.
        The earlier member's OUTCOME is what survives, whichever order the
        two arrive in."""
        early = _observation(record_id="bucket", outcome="failed",
                             evaluated_at=EVAL_AT)
        late = _observation(record_id="bucket", outcome="confirmed",
                            evaluated_at=EVAL_AT + timedelta(minutes=1))
        forward, backward = _build([early, late]), _build([late, early])
        self.assertEqual(forward.as_record(), backward.as_record())
        self.assertEqual(forward.deduplicated_n, 1)
        stratum = _only(forward)
        self.assertEqual(
            stratum.by_direction_outcome[DirectionOutcome.FAILED.value], 1)
        self.assertEqual(
            stratum.by_direction_outcome[DirectionOutcome.CONFIRMED.value], 0)

    def test_contradictory_claims_on_one_physical_identity_are_refused(self):
        """Same record id AND same instant is the SAME physical observation --
        ``storage_id`` hashes exactly those fields. Two such artifacts
        carrying different validation results are contradictory claims about
        one immutable observation, and are reported rather than arbitrated:
        every discriminator left to choose between them is derived from the
        outcome."""
        a = _observation(record_id="bucket", evaluated_at=EVAL_AT,
                         outcome="confirmed")
        b = _observation(record_id="bucket", evaluated_at=EVAL_AT,
                         outcome="failed")
        self.assertEqual(a.envelope.context.shadow_storage_id,
                         b.envelope.context.shadow_storage_id)
        forward, backward = _build([a, b]), _build([b, a])
        self.assertEqual(forward.as_record(), backward.as_record())
        self.assertEqual(forward.admitted_n, 0)
        self.assertIs(forward.admission_failures[0].reason,
                      AdmissionFailureReason.PHYSICAL_IDENTITY_CONFLICT)

    def test_an_identical_artifact_supplied_twice_collapses_silently(self):
        member = _observation(record_id="bucket", evaluated_at=EVAL_AT)
        cohort = _build([member, member, member])
        self.assertEqual(cohort.admission_failure_n, 0)
        self.assertEqual(cohort.admitted_n, 1)
        self.assertEqual(cohort.deduplicated_n, 1)

    def test_disagreeing_outcomes_do_not_exclude_the_group(self):
        """Excluding on disagreement would itself be outcome-conditioned
        exclusion, and would remove exactly the ambiguous cases."""
        early, late = self._pair("neutral", "confirmed")
        cohort = _build([early, late])
        self.assertEqual(cohort.deduplicated_n, 1)
        self.assertEqual(cohort.duplicate_outcome_disagreement_n, 1)

    def test_agreeing_outcomes_do_not_increment_the_disagreement_counter(self):
        early, late = self._pair("confirmed", "confirmed")
        self.assertEqual(_build([early, late]).duplicate_outcome_disagreement_n, 0)

    def test_duplicate_diagnostics(self):
        a = _observation(record_id="bucket", evaluated_at=EVAL_AT)
        b = _observation(record_id="bucket", evaluated_at=EVAL_AT + timedelta(minutes=10))
        c = _observation(record_id="bucket", evaluated_at=EVAL_AT + timedelta(minutes=20))
        solo = _observation(record_id="solo", evaluated_at=EVAL_AT + timedelta(days=40))
        cohort = _build([a, b, c, solo])
        self.assertEqual(cohort.duplicate_group_n, 1)
        self.assertEqual(cohort.duplicates_collapsed_n, 2)
        self.assertEqual(cohort.duplicate_max_group_size, 3)
        self.assertEqual(cohort.deduplicated_n, 2)

    def test_dedup_runs_before_episode_spacing(self):
        """Three members in one hour bucket must count as ONE episode anchor,
        not three -- which is only true if dedup ran first."""
        members = [
            _observation(record_id="bucket", evaluated_at=EVAL_AT + timedelta(minutes=m))
            for m in (0, 10, 20)
        ]
        self.assertEqual(_only(_build(members)).disjoint_episode_n, 1)


# ===========================================================================
# F. DISJOINT EPISODES   (12 tests)
# ===========================================================================
class TestEpisodes(unittest.TestCase):
    def test_hourly_observations_inside_one_window_are_one_episode(self):
        """336 hourly tactical observations span 13.96 days -- less than one
        14-day forward window -- so they are ONE disjoint episode, not 336
        independent forecasts. This is the whole point of the stage."""
        members = _spread(336, step=timedelta(hours=1), outcome="unresolved")
        cohort = _build(members)
        self.assertEqual(cohort.deduplicated_n, 336)
        self.assertEqual(_only(cohort).disjoint_episode_n, 1)

    def test_exact_window_boundary_is_accepted(self):
        a = _observation(record_id="a", evaluated_at=EVAL_AT)
        b = _observation(record_id="b", evaluated_at=EVAL_AT + TACTICAL_WINDOW)
        self.assertEqual(_only(_build([a, b])).disjoint_episode_n, 2)

    def test_one_second_before_the_boundary_is_rejected(self):
        a = _observation(record_id="a", evaluated_at=EVAL_AT)
        b = _observation(record_id="b",
                         evaluated_at=EVAL_AT + TACTICAL_WINDOW - timedelta(seconds=1))
        self.assertEqual(_only(_build([a, b])).disjoint_episode_n, 1)

    def test_instruments_partition_separately(self):
        members = (_spread(2, instrument="Gold", step=timedelta(hours=1))
                   + _spread(2, instrument="USD", step=timedelta(hours=1),
                             anchor_symbol="DX-Y.NYB"))
        cohort = _build(members)
        self.assertEqual(len(cohort.strata), 2)
        for stratum in cohort.strata:
            self.assertEqual(stratum.disjoint_episode_n, 1)

    def test_horizons_partition_separately(self):
        tactical = _observation(record_id="t", horizon="tactical",
                                evaluated_at=EVAL_AT)
        execution = _observation(record_id="e", horizon="execution",
                                 evaluated_at=EVAL_AT + timedelta(hours=1))
        cohort = _build([tactical, execution])
        self.assertEqual({s.key.horizon for s in cohort.strata},
                         {"tactical", "execution"})
        for stratum in cohort.strata:
            self.assertEqual(stratum.disjoint_episode_n, 1)

    def test_each_horizon_spaces_on_its_own_window(self):
        """A 3-day execution window and a 14-day tactical window must not
        share a constant: two observations 4 days apart are two execution
        episodes but one tactical episode."""
        gap = timedelta(days=4)
        tactical = [_observation(record_id=f"t{i}", horizon="tactical",
                                 evaluated_at=EVAL_AT + gap * i) for i in range(2)]
        execution = [_observation(record_id=f"e{i}", horizon="execution",
                                  evaluated_at=EVAL_AT + gap * i) for i in range(2)]
        by_horizon = {s.key.horizon: s for s in _build(tactical + execution).strata}
        self.assertEqual(by_horizon["tactical"].disjoint_episode_n, 1)
        self.assertEqual(by_horizon["execution"].disjoint_episode_n, 2)

    def test_no_later_observation_replaces_an_accepted_representative(self):
        """Greedy forward only. The anchor is the earliest, even when a later
        member in the same window carries a verdict and the anchor does not."""
        anchor = _observation(record_id="a", evaluated_at=EVAL_AT,
                              outcome="unresolved")
        later = _observation(record_id="b", evaluated_at=EVAL_AT + timedelta(days=1),
                             outcome="confirmed")
        stratum = _only(_build([anchor, later]))
        self.assertEqual(stratum.disjoint_episode_n, 1)
        self.assertEqual(stratum.disjoint_episode_verdict_n, 0)

    def test_neutral_participates_in_structural_spacing(self):
        members = [_observation(record_id=f"n{i}", outcome="neutral",
                                evaluated_at=EVAL_AT + timedelta(hours=i))
                   for i in range(3)]
        self.assertEqual(_only(_build(members)).disjoint_episode_n, 1)

    def test_abstained_participates_in_structural_spacing(self):
        members = [_observation(record_id=f"a{i}", outcome="abstained",
                                evaluated_at=EVAL_AT + TACTICAL_WINDOW * i)
                   for i in range(3)]
        self.assertEqual(_only(_build(members)).disjoint_episode_n, 3)

    def test_unresolved_participates_in_structural_spacing(self):
        members = [_observation(record_id=f"u{i}", outcome="unresolved",
                                evaluated_at=EVAL_AT + TACTICAL_WINDOW * i)
                   for i in range(3)]
        self.assertEqual(_only(_build(members)).disjoint_episode_n, 3)

    def test_changing_outcomes_does_not_change_the_partition(self):
        """The look-ahead test. Same timestamps, different results: the
        episode structure must be byte-identical, because spacing runs before
        any readiness or eligibility filtering."""
        stamps = [EVAL_AT + TACTICAL_WINDOW * i for i in range(4)]
        counts = set()
        for outcome in ("confirmed", "failed", "neutral", "unresolved", "abstained"):
            members = [_observation(record_id=f"x{i}", outcome=outcome,
                                    evaluated_at=at)
                       for i, at in enumerate(stamps)]
            cohort = _build(members)
            counts.add(tuple(s.group_disjoint_episode_n for s in cohort.strata))
        self.assertEqual(counts, {(4,)})

    def test_episode_verdict_count_never_exceeds_episode_count(self):
        members = _spread(5, step=TACTICAL_WINDOW, outcome="confirmed")
        stratum = _only(_build(members))
        self.assertLessEqual(stratum.disjoint_episode_verdict_n,
                             stratum.disjoint_episode_n)


# ===========================================================================
# G. ELIGIBILITY AND POOLS   (10 tests)
# ===========================================================================
class TestEligibility(unittest.TestCase):
    def test_captured_and_research_are_separate_strata(self):
        captured = _observation(record_id="c", anchor_symbol="XAUUSD=X",
                                bar_symbol="XAUUSD=X")
        research = _observation(record_id="r", anchor_symbol="XAUUSD=X",
                                bar_symbol="GC=F",
                                evaluated_at=EVAL_AT + timedelta(days=30))
        cohort = _build([captured, research])
        pools = {s.key.eligibility_pool for s in cohort.strata}
        self.assertEqual(pools, {"captured", "reconstructed_research"})

    def test_pools_never_merge_into_one_ratio(self):
        captured = _spread(3, step=TACTICAL_WINDOW, bar_symbol="XAUUSD=X")
        research = [_observation(record_id=f"g{i}", bar_symbol="GC=F",
                                 evaluated_at=EVAL_AT + timedelta(days=200)
                                 + TACTICAL_WINDOW * i) for i in range(3)]
        cohort = _build(captured + research)
        for stratum in cohort.strata:
            self.assertEqual(stratum.verdict_confirmation_rate.denominator, 3)

    def test_provisional_members_are_not_in_finalized_denominators(self):
        at = NOW - timedelta(days=3)
        cohort = _build([_observation(record_id="p", evaluated_at=at, as_of=NOW)],
                        as_of=NOW)
        stratum = _only(cohort)
        self.assertEqual(stratum.finalized_n, 0)
        self.assertEqual(stratum.verdict_confirmation_rate.denominator, 0)
        self.assertIs(stratum.verdict_confirmation_rate.state,
                      RatioState.NO_DENOMINATOR)

    def test_provisional_split_reports_not_matured(self):
        at = NOW - timedelta(days=3)
        stratum = _only(_build([_observation(record_id="p", evaluated_at=at,
                                             as_of=NOW)], as_of=NOW))
        self.assertEqual(stratum.provisional_not_matured_n, 1)
        self.assertEqual(stratum.provisional_matured_partial_n, 0)

    def test_provisional_split_reports_matured_partial(self):
        start = EVAL_AT.replace(hour=0, minute=0, second=0, microsecond=0)
        gappy = [_bar(start + timedelta(days=d), ANCHOR_PRICE) for d in (1, 2, 3)]
        gappy += [_bar(start + timedelta(days=d), ANCHOR_PRICE) for d in (20, 25)]
        member = _observation(record_id="g", bars=gappy)
        stratum = _only(_build([member]))
        self.assertIs(member.readiness, ReadinessTier.PROVISIONAL)
        self.assertEqual(stratum.provisional_matured_partial_n, 1)
        self.assertEqual(stratum.provisional_not_matured_n, 0)

    def test_unusable_data_is_excluded_from_verdict_ratios(self):
        member = _observation(record_id="u", outcome="unresolved")
        stratum = _only(_build([member]))
        self.assertEqual(stratum.verdict_confirmation_rate.denominator, 0)

    def test_final_neutral_reaches_the_neutral_denominator_despite_readiness(self):
        """The reason readiness must NOT be the denominator filter: a final
        NEUTRAL is ReadinessTier.EXCLUDED, yet it is exactly the evidence the
        neutral diagnostic exists to count."""
        members = [_observation(record_id=f"n{i}", outcome="neutral",
                                evaluated_at=EVAL_AT + TACTICAL_WINDOW * i)
                   for i in range(3)]
        stratum = _only(_build(members))
        self.assertEqual(stratum.by_readiness_tier[ReadinessTier.EXCLUDED.value], 3)
        self.assertEqual(stratum.neutral_rate.denominator, 3)
        self.assertEqual(stratum.neutral_rate.numerator, 3)

    def test_readiness_is_not_used_as_a_denominator_filter(self):
        """Structural: the metric base is built from maturity and eligibility
        pool. ReadinessTier appears in the module only for counting."""
        source = inspect.getsource(cohort_mod._build_stratum)
        self.assertIn("is_final", source)
        self.assertIn("eligibility_pool", source)
        self.assertNotIn("ReadinessTier.CALIBRATION_ELIGIBLE", source)
        self.assertNotIn("ReadinessTier.RESEARCH_ONLY", source)

    def test_readiness_tiers_are_still_reported(self):
        stratum = _only(_build(_spread(2, step=TACTICAL_WINDOW)))
        self.assertEqual(
            stratum.by_readiness_tier[ReadinessTier.CALIBRATION_ELIGIBLE.value], 2)

    def test_finalized_count_uses_matured_only(self):
        final = _observation(record_id="f", evaluated_at=EVAL_AT)
        immature = _observation(record_id="i", evaluated_at=NOW - timedelta(days=3),
                                as_of=NOW)
        stratum = _only(_build([final, immature], as_of=NOW))
        self.assertEqual(stratum.deduplicated_n, 2)
        self.assertEqual(stratum.finalized_n, 1)


# ===========================================================================
# H. OUTCOME SEMANTICS   (9 tests)
# ===========================================================================
class TestOutcomeSemantics(unittest.TestCase):
    def _stratum(self, outcomes):
        members = [_observation(record_id=f"o{i}", outcome=outcome,
                                evaluated_at=EVAL_AT + TACTICAL_WINDOW * i)
                   for i, outcome in enumerate(outcomes)]
        return _only(_build(members))

    def test_confirmed_is_the_numerator(self):
        stratum = self._stratum(["confirmed", "confirmed", "failed"])
        self.assertEqual(stratum.verdict_confirmation_rate.numerator, 2)

    def test_failed_is_in_the_denominator_only(self):
        stratum = self._stratum(["confirmed", "failed"])
        self.assertEqual(stratum.verdict_confirmation_rate.numerator, 1)
        self.assertEqual(stratum.verdict_confirmation_rate.denominator, 2)

    def test_neutral_is_excluded_from_the_verdict_denominator(self):
        stratum = self._stratum(["confirmed", "failed", "neutral"])
        self.assertEqual(stratum.verdict_confirmation_rate.denominator, 2)

    def test_neutral_is_never_counted_as_failed(self):
        stratum = self._stratum(["neutral", "neutral"])
        self.assertEqual(stratum.by_direction_outcome[DirectionOutcome.FAILED.value], 0)
        self.assertEqual(
            stratum.by_direction_outcome[DirectionOutcome.NEUTRAL_WITHIN_BAND.value], 2)

    def test_neutral_is_in_the_neutral_denominator(self):
        stratum = self._stratum(["confirmed", "failed", "neutral"])
        self.assertEqual(stratum.neutral_rate.numerator, 1)
        self.assertEqual(stratum.neutral_rate.denominator, 3)

    def test_abstained_has_its_own_count_and_is_never_a_miss(self):
        stratum = self._stratum(["confirmed", "abstained"])
        self.assertEqual(
            stratum.by_direction_outcome[DirectionOutcome.ABSTAINED.value], 1)
        self.assertEqual(stratum.verdict_confirmation_rate.denominator, 1)
        self.assertEqual(stratum.neutral_rate.denominator, 1)

    def test_abstained_is_outside_the_resolution_denominator(self):
        """A FLAT claim was never going to resolve a direction; counting it
        would make resolution_rate measure abstention rather than coverage."""
        stratum = self._stratum(["confirmed", "abstained"])
        self.assertEqual(stratum.resolution_rate.denominator, 1)
        self.assertEqual(stratum.by_claim_direction[Direction.FLAT.value], 1)

    def test_unresolved_has_its_own_count(self):
        stratum = self._stratum(["confirmed", "unresolved"])
        self.assertEqual(
            stratum.by_direction_outcome[DirectionOutcome.UNRESOLVED.value], 1)
        self.assertEqual(
            stratum.by_direction_outcome[DirectionOutcome.FAILED.value], 0)

    def test_exclusion_reasons_are_reported_for_excluded_evidence(self):
        """An observation with no bindable series is EXCLUDED and must say
        why. It also lands in its own stratum, because unavailable provenance
        never merges with an ideal one."""
        member = _observation(record_id="x", bars=[])
        stratum = _only(_build([member]))
        self.assertEqual(stratum.key.eligibility_pool, "excluded")
        self.assertEqual(stratum.key.provenance_grade,
                         ProvenanceGrade.UNAVAILABLE.value)
        self.assertEqual(sum(stratum.by_exclusion_reason.values()), 1)

    def test_unresolved_is_in_the_resolution_denominator_but_not_its_numerator(self):
        stratum = self._stratum(["confirmed", "unresolved"])
        self.assertEqual(stratum.resolution_rate.numerator, 1)
        self.assertEqual(stratum.resolution_rate.denominator, 2)


# ===========================================================================
# I. THE THREE RATIOS   (8 tests)
# ===========================================================================
class TestRatios(unittest.TestCase):
    def _stratum(self, outcomes, **kw):
        members = [_observation(record_id=f"o{i}", outcome=outcome,
                                evaluated_at=EVAL_AT + TACTICAL_WINDOW * i)
                   for i, outcome in enumerate(outcomes)]
        return _only(_build(members, **kw))

    def test_verdict_confirmation_rate_arithmetic(self):
        ratio = self._stratum(["confirmed", "confirmed", "confirmed",
                               "failed"]).verdict_confirmation_rate
        self.assertIs(ratio.state, RatioState.SUFFICIENT)
        self.assertAlmostEqual(ratio.value, 0.75)
        self.assertEqual(ratio.denominator_name, "directional_verdicts")

    def test_neutral_rate_arithmetic(self):
        ratio = self._stratum(["confirmed", "neutral",
                               "neutral", "failed"]).neutral_rate
        self.assertAlmostEqual(ratio.value, 0.5)
        self.assertEqual(ratio.denominator_name, "resolved_directional_claims")

    def test_resolution_rate_arithmetic(self):
        ratio = self._stratum(["confirmed", "neutral", "unresolved",
                               "unresolved"]).resolution_rate
        self.assertAlmostEqual(ratio.value, 0.5)
        self.assertEqual(ratio.denominator_name, "finalized_directional_claims")

    def test_exactly_three_named_ratios_exist(self):
        stratum_fields = {f.name for f in dataclasses.fields(Stratum)}
        ratio_fields = {name for name in stratum_fields if name.endswith("_rate")}
        self.assertEqual(ratio_fields,
                         {"verdict_confirmation_rate", "neutral_rate",
                          "resolution_rate"})

    def test_every_ratio_exposes_its_numerator_and_named_denominator(self):
        stratum = self._stratum(["confirmed", "failed"])
        for ratio in (stratum.verdict_confirmation_rate, stratum.neutral_rate,
                      stratum.resolution_rate):
            self.assertIsInstance(ratio, Ratio)
            self.assertTrue(ratio.denominator_name)
            self.assertTrue(ratio.episode_denominator_name)
            self.assertIsInstance(ratio.numerator, int)
            self.assertIsInstance(ratio.denominator, int)

    def test_no_generic_accuracy_metric_is_exposed(self):
        names = {f.name for f in dataclasses.fields(Stratum)}
        names |= {f.name for f in dataclasses.fields(Cohort)}
        for forbidden in ("accuracy", "hit_rate", "win_rate", "score", "overall",
                          "sharpe", "pnl", "brier", "confidence_interval"):
            self.assertFalse(any(forbidden in n for n in names), forbidden)

    def test_no_pooled_ratio_exists_at_cohort_level(self):
        cohort_fields = {f.name for f in dataclasses.fields(Cohort)}
        self.assertFalse(any(n.endswith("_rate") for n in cohort_fields))

    def test_ratio_is_frozen(self):
        ratio = self._stratum(["confirmed", "failed"]).verdict_confirmation_rate
        with self.assertRaises(dataclasses.FrozenInstanceError):
            ratio.value = 1.0


# ===========================================================================
# J. SAMPLE FLOORS   (9 tests)
# ===========================================================================
class TestSampleFloors(unittest.TestCase):
    def _members(self, n, outcome="confirmed"):
        return [_observation(record_id=f"s{i}", outcome=outcome,
                             evaluated_at=EVAL_AT + TACTICAL_WINDOW * i)
                for i in range(n)]

    def test_zero_denominator_is_no_denominator(self):
        stratum = _only(_build(self._members(2, "unresolved")))
        ratio = stratum.verdict_confirmation_rate
        self.assertIs(ratio.state, RatioState.NO_DENOMINATOR)
        self.assertIsNone(ratio.value)

    def test_small_denominator_is_insufficient_sample(self):
        stratum = _only(_build(self._members(5), cohort_config=DEFAULT_COHORT_CONFIG))
        ratio = stratum.verdict_confirmation_rate
        self.assertIs(ratio.state, RatioState.INSUFFICIENT_SAMPLE)
        self.assertIsNone(ratio.value)
        self.assertEqual(ratio.denominator, 5)

    def test_enough_denominator_but_too_few_episodes_is_insufficient(self):
        """Thirty hourly observations inside one window: the denominator floor
        passes and the episode floor does not."""
        members = [_observation(record_id=f"h{i}", outcome="confirmed",
                                evaluated_at=EVAL_AT + timedelta(hours=i))
                   for i in range(30)]
        config = CohortConfig(min_denominator=30, min_disjoint_episode_verdict_n=10)
        ratio = _only(_build(members, cohort_config=config)).verdict_confirmation_rate
        self.assertEqual(ratio.denominator, 30)
        self.assertEqual(ratio.episode_denominator, 1)
        self.assertIs(ratio.state, RatioState.INSUFFICIENT_SAMPLE)
        self.assertIsNone(ratio.value)

    def test_both_floors_passing_publishes_a_value(self):
        ratio = _only(_build(self._members(3),
                             cohort_config=TINY)).verdict_confirmation_rate
        self.assertIs(ratio.state, RatioState.SUFFICIENT)
        self.assertAlmostEqual(ratio.value, 1.0)

    def test_exact_floor_equality_passes(self):
        config = CohortConfig(min_denominator=4, min_disjoint_episode_verdict_n=4)
        ratio = _only(_build(self._members(4),
                             cohort_config=config)).verdict_confirmation_rate
        self.assertEqual(ratio.denominator, 4)
        self.assertEqual(ratio.episode_denominator, 4)
        self.assertIs(ratio.state, RatioState.SUFFICIENT)

    def test_one_below_the_floor_fails(self):
        config = CohortConfig(min_denominator=5, min_disjoint_episode_verdict_n=1)
        ratio = _only(_build(self._members(4),
                             cohort_config=config)).verdict_confirmation_rate
        self.assertIs(ratio.state, RatioState.INSUFFICIENT_SAMPLE)

    def test_counts_are_emitted_even_when_no_value_is(self):
        stratum = _only(_build(self._members(5), cohort_config=DEFAULT_COHORT_CONFIG))
        ratio = stratum.verdict_confirmation_rate
        self.assertIsNone(ratio.value)
        self.assertEqual(ratio.numerator, 5)
        self.assertEqual(ratio.denominator, 5)
        self.assertEqual(stratum.deduplicated_n, 5)

    def test_a_value_is_never_paired_with_a_small_sample_warning(self):
        for n in (1, 5, 29):
            ratio = _only(_build(self._members(n),
                                 cohort_config=DEFAULT_COHORT_CONFIG)
                          ).verdict_confirmation_rate
            if ratio.state is not RatioState.SUFFICIENT:
                self.assertIsNone(ratio.value, n)

    def test_floors_are_versioned_research_defaults(self):
        provenance = DEFAULT_COHORT_CONFIG.as_provenance()
        self.assertEqual(provenance["status"],
                         "VERSIONED RESEARCH DEFAULTS -- NOT CALIBRATED")
        self.assertEqual(DEFAULT_COHORT_CONFIG.min_denominator, 30)
        self.assertEqual(DEFAULT_COHORT_CONFIG.min_disjoint_episode_verdict_n, 10)


# ===========================================================================
# K. PROVENANCE AND THE FALLBACK HAZARD   (10 tests)
# ===========================================================================
class TestProvenance(unittest.TestCase):
    def _gold_mixed(self, exact_n=3, substituted_n=3):
        exact = [_observation(record_id=f"e{i}", anchor_symbol="GC=F",
                              bar_symbol="GC=F",
                              evaluated_at=EVAL_AT + TACTICAL_WINDOW * i)
                 for i in range(exact_n)]
        substituted = [_observation(record_id=f"s{i}", anchor_symbol="XAUUSD=X",
                                    bar_symbol="GC=F",
                                    evaluated_at=EVAL_AT + timedelta(days=300)
                                    + TACTICAL_WINDOW * i)
                       for i in range(substituted_n)]
        return exact, substituted

    def test_normal_gold_stays_substituted_and_research(self):
        member = _observation(anchor_symbol="XAUUSD=X", bar_symbol="GC=F")
        self.assertIs(member.provenance_grade, ProvenanceGrade.SUBSTITUTED_SERIES)
        stratum = _only(_build([member]))
        self.assertEqual(stratum.key.eligibility_pool, "reconstructed_research")

    def test_fallback_anchor_gold_is_exact_and_captured_upstream(self):
        member = _observation(anchor_symbol="GC=F", bar_symbol="GC=F")
        self.assertIs(member.provenance_grade, ProvenanceGrade.IDEAL)
        stratum = _only(_build([member]))
        self.assertEqual(stratum.key.eligibility_pool, "captured")

    def test_provenance_grades_never_merge(self):
        exact, substituted = self._gold_mixed()
        cohort = _build(exact + substituted)
        grades = {s.key.provenance_grade for s in cohort.strata}
        self.assertEqual(grades, {ProvenanceGrade.IDEAL.value,
                                  ProvenanceGrade.SUBSTITUTED_SERIES.value})
        for stratum in cohort.strata:
            self.assertEqual(stratum.deduplicated_n, 3)

    def test_exact_binding_count_is_emitted(self):
        exact, substituted = self._gold_mixed()
        by_grade = {s.key.provenance_grade: s for s in _build(exact + substituted).strata}
        self.assertEqual(
            by_grade[ProvenanceGrade.IDEAL.value].provenance_exact_binding_n, 3)
        self.assertEqual(
            by_grade[ProvenanceGrade.SUBSTITUTED_SERIES.value].cross_source_n, 3)

    def test_outage_selected_captured_ratio_is_withheld(self):
        """The exact-binding captured subset coexists with substituted members
        of the same instrument and horizon, so it is selected by the absence
        of substitution -- an outage, not a property of the forecast."""
        exact, substituted = self._gold_mixed()
        by_grade = {s.key.provenance_grade: s for s in _build(exact + substituted).strata}
        ratio = by_grade[ProvenanceGrade.IDEAL.value].verdict_confirmation_rate
        self.assertIs(ratio.state, RatioState.WITHHELD)
        self.assertIsNone(ratio.value)
        self.assertIn(RatioNote.PROVENANCE_OUTAGE_SELECTED, ratio.notes)

    def test_withholding_beats_numeric_sufficiency(self):
        exact, substituted = self._gold_mixed(exact_n=5, substituted_n=2)
        by_grade = {s.key.provenance_grade: s for s in _build(exact + substituted).strata}
        stratum = by_grade[ProvenanceGrade.IDEAL.value]
        self.assertEqual(stratum.verdict_confirmation_rate.denominator, 5)
        self.assertIs(stratum.verdict_confirmation_rate.state, RatioState.WITHHELD)

    def test_all_three_ratios_are_withheld_for_an_outage_selected_stratum(self):
        exact, substituted = self._gold_mixed()
        by_grade = {s.key.provenance_grade: s for s in _build(exact + substituted).strata}
        stratum = by_grade[ProvenanceGrade.IDEAL.value]
        for ratio in (stratum.verdict_confirmation_rate, stratum.neutral_rate,
                      stratum.resolution_rate):
            self.assertIs(ratio.state, RatioState.WITHHELD)

    def test_a_stable_binding_instrument_is_not_withheld(self):
        members = [_observation(record_id=f"u{i}", instrument="USD",
                                anchor_symbol="DX-Y.NYB", bar_symbol="DX-Y.NYB",
                                evaluated_at=EVAL_AT + TACTICAL_WINDOW * i)
                   for i in range(3)]
        stratum = _only(_build(members))
        self.assertFalse(stratum.provenance_outage_selected)
        self.assertIs(stratum.verdict_confirmation_rate.state, RatioState.SUFFICIENT)

    def test_no_instrument_is_named_in_the_implementation(self):
        """The hazard is derived generically from binding facts present in the
        cohort, never from a hardcoded market rule."""
        source = inspect.getsource(cohort_mod)
        for literal in ('"Gold"', "'Gold'", '"XAUUSD=X"', "'XAUUSD=X'",
                        '"GC=F"', "'GC=F'"):
            self.assertNotIn(literal, source, literal)

    def test_symbols_are_exposed_and_cross_granularity_is_not_a_defect(self):
        member = _observation(anchor_symbol="XAUUSD=X", bar_symbol="GC=F")
        stratum = _only(_build([member]))
        self.assertEqual(stratum.market_symbols, ("XAUUSD=X",))
        self.assertEqual(stratum.bound_symbols, ("GC=F",))
        self.assertEqual(stratum.cross_granularity_n, 1)
        self.assertFalse(stratum.provenance_outage_selected)


# ===========================================================================
# L. HASHING   (11 tests)
# ===========================================================================
class TestHashing(unittest.TestCase):
    def setUp(self):
        self.members = _spread(3, step=TACTICAL_WINDOW)

    def test_cohort_id_is_deterministic(self):
        self.assertEqual(len({_build(self.members).cohort_id for _ in range(3)}), 1)

    def test_membership_hash_is_deterministic(self):
        self.assertEqual(
            len({_build(self.members).membership_hash for _ in range(3)}), 1)

    def test_membership_hash_commits_to_member_identity(self):
        base = _build(self.members).membership_hash
        fewer = _build(self.members[:2]).membership_hash
        self.assertNotEqual(base, fewer)

    def test_swapping_outcome_ownership_changes_membership_hash(self):
        """Order-committing: the hash pairs each storage id with its own
        result, so who confirmed and who failed is part of the identity."""
        stamps = [EVAL_AT, EVAL_AT + TACTICAL_WINDOW]
        first = [_observation(record_id="a", outcome="confirmed", evaluated_at=stamps[0]),
                 _observation(record_id="b", outcome="failed", evaluated_at=stamps[1])]
        second = [_observation(record_id="a", outcome="failed", evaluated_at=stamps[0]),
                  _observation(record_id="b", outcome="confirmed", evaluated_at=stamps[1])]
        self.assertNotEqual(_build(first).membership_hash,
                            _build(second).membership_hash)

    def test_cohort_id_includes_as_of(self):
        self.assertNotEqual(
            _build(self.members).cohort_id,
            _build(self.members, as_of=NOW + timedelta(days=1)).cohort_id)

    def test_cohort_id_includes_the_cohort_config_floors(self):
        a = _build(self.members, cohort_config=CohortConfig(min_denominator=5))
        b = _build(self.members, cohort_config=CohortConfig(min_denominator=6))
        self.assertNotEqual(a.cohort_id, b.cohort_id)

    def test_cohort_id_includes_the_validation_config_hash(self):
        variant = ValidationConfig(neutral_band_atr_multiple=0.25)
        a = _build(self.members)
        b = _build([], config=variant)
        self.assertNotEqual(a.cohort_id, b.cohort_id)

    def test_cohort_id_includes_policy_versions(self):
        record = _build(self.members).as_record()
        for key, expected in (("cohort_schema_version", COHORT_SCHEMA_VERSION),
                              ("dedup_policy_version", DEDUP_POLICY_VERSION),
                              ("episode_policy_version", EPISODE_POLICY_VERSION)):
            self.assertEqual(record[key], expected)
        source = inspect.getsource(cohort_mod.build_cohort)
        for token in ("dedup_policy_version", "episode_policy_version",
                      "cohort_schema_version", "as_of", "stratify_by"):
            self.assertIn(token, source, token)

    def test_membership_hash_does_not_commit_to_diagnostic_prose(self):
        source = inspect.getsource(cohort_mod.build_cohort)
        basis = source.split("membership_hash = ")[1]
        for forbidden in ("detail", "message", "notes", "setup_notes"):
            self.assertNotIn(forbidden, basis, forbidden)

    def test_no_result_hash_is_produced(self):
        names = {f.name for f in dataclasses.fields(Cohort)}
        self.assertNotIn("result_hash", names)
        self.assertNotIn("result_hash", _identifiers(cohort_mod))

    def test_hash_helpers_are_reused_not_reimplemented(self):
        names = _identifiers(cohort_mod)
        self.assertIn("canonical_json", names)
        self.assertIn("sha256_hex", names)
        self.assertNotIn("hashlib", names)
        self.assertNotIn("json", names)


# ===========================================================================
# M. STRATIFICATION   (7 tests)
# ===========================================================================
class TestStratification(unittest.TestCase):
    def test_stratify_by_is_the_declared_four_dimensions(self):
        self.assertEqual(STRATIFY_BY, ("instrument", "horizon",
                                       "eligibility_pool", "provenance_grade"))
        self.assertEqual({f.name for f in dataclasses.fields(StratumKey)},
                         set(STRATIFY_BY))

    def test_instruments_never_merge(self):
        members = (_spread(2, instrument="Gold", step=TACTICAL_WINDOW)
                   + _spread(2, instrument="USD", step=TACTICAL_WINDOW,
                             anchor_symbol="DX-Y.NYB"))
        cohort = _build(members)
        self.assertEqual({s.key.instrument for s in cohort.strata}, {"Gold", "USD"})
        for stratum in cohort.strata:
            self.assertEqual(stratum.verdict_confirmation_rate.denominator, 2)

    def test_horizons_never_merge(self):
        members = [_observation(record_id="t", horizon="tactical"),
                   _observation(record_id="e", horizon="execution")]
        self.assertEqual(len(_build(members).strata), 2)

    def test_asset_class_is_a_label_and_never_merges_instruments(self):
        members = (_spread(1, instrument="EUR", anchor_symbol="EURUSD=X")
                   + _spread(1, instrument="USD", anchor_symbol="DX-Y.NYB"))
        cohort = _build(members)
        self.assertEqual(len(cohort.strata), 2)
        self.assertEqual({s.asset_class for s in cohort.strata}, {"fx_module_v1"})
        self.assertNotIn("asset_class", STRATIFY_BY)

    def test_no_cohort_level_aggregate_across_strata_exists(self):
        fields = {f.name for f in dataclasses.fields(Cohort)}
        for forbidden in ("all_assets", "all_fx", "overall", "pooled", "total_rate"):
            self.assertFalse(any(forbidden in f for f in fields), forbidden)

    def test_every_stratum_names_its_full_key(self):
        stratum = _only(_build(_spread(2, step=TACTICAL_WINDOW)))
        record = stratum.as_record()["key"]
        self.assertEqual(set(record), set(STRATIFY_BY))

    def test_data_quality_counts_are_reported(self):
        stratum = _only(_build(_spread(2, step=TACTICAL_WINDOW)))
        self.assertEqual(stratum.bar_conflict_n, 0)
        self.assertIsNone(stratum.malformed_row_total)
        self.assertEqual(stratum.bar_duplicates_collapsed_total, 0)
        self.assertEqual(stratum.excursion_is_lower_bound_n, 0)


# ===========================================================================
# N. COHORT STATE   (5 tests)
# ===========================================================================
class TestCohortState(unittest.TestCase):
    def test_all_final_members_are_finalized(self):
        self.assertIs(_build(_spread(3, step=TACTICAL_WINDOW)).cohort_state,
                      CohortState.FINALIZED)

    def test_any_provisional_member_makes_the_cohort_provisional(self):
        final = _observation(record_id="f", evaluated_at=EVAL_AT)
        immature = _observation(record_id="i", evaluated_at=NOW - timedelta(days=3),
                                as_of=NOW)
        self.assertIs(_build([final, immature], as_of=NOW).cohort_state,
                      CohortState.PROVISIONAL)

    def test_empty_input_is_empty_not_finalized(self):
        self.assertIs(_build([]).cohort_state, CohortState.EMPTY)

    def test_a_defect_only_batch_is_empty(self):
        defect = _observation(horizon="not_a_horizon")
        cohort = _build([defect])
        self.assertIs(cohort.cohort_state, CohortState.EMPTY)
        self.assertEqual(cohort.lineage_defect_n, 1)

    def test_cohort_state_is_not_inferred_from_ratio_availability(self):
        """A finalized cohort whose ratios are all withheld for sample size is
        still FINALIZED: state describes maturity, not publishability."""
        cohort = _build(_spread(3, step=TACTICAL_WINDOW),
                        cohort_config=DEFAULT_COHORT_CONFIG)
        self.assertIs(cohort.cohort_state, CohortState.FINALIZED)
        self.assertIs(_only(cohort).verdict_confirmation_rate.state,
                      RatioState.INSUFFICIENT_SAMPLE)


# ===========================================================================
# O. PURITY   (10 tests)
# ===========================================================================
class TestPurity(unittest.TestCase):
    def test_no_forbidden_imports(self):
        tree = ast.parse(inspect.getsource(cohort_mod))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [a.name for a in node.names] + [
                    getattr(node, "module", "") or ""]
                for name in names:
                    for forbidden in (
                        "requests", "urllib", "socket", "http", "httpx",
                        "aiohttp", "streamlit", "threading", "asyncio",
                        "subprocess", "multiprocessing", "sqlite", "psycopg",
                        "supabase", "random", "os", "pathlib", "production_core",
                        "b2_bridge", "b2_validation_bridge",
                    ):
                        self.assertNotIn(forbidden, name, name)

    def test_no_clock_is_read(self):
        """Checked as a CALL on an attribute, not as a substring: ``now`` is a
        legitimate parameter name elsewhere in this package."""
        tree = ast.parse(inspect.getsource(cohort_mod))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                self.assertNotIn(node.func.attr,
                                 ("now", "utcnow", "today", "time", "monotonic"),
                                 node.func.attr)

    def test_no_file_or_process_io(self):
        tree = ast.parse(inspect.getsource(cohort_mod))
        called = {node.func.id for node in ast.walk(tree)
                  if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)}
        for builtin in ("open", "exec", "eval", "compile", "__import__", "input",
                        "print"):
            self.assertNotIn(builtin, called, builtin)

    def test_no_randomness(self):
        names = _identifiers(cohort_mod)
        for forbidden in ("random", "shuffle", "sample", "choice", "seed"):
            self.assertNotIn(forbidden, names, forbidden)

    def test_no_environment_dependency(self):
        names = _identifiers(cohort_mod)
        for forbidden in ("environ", "getenv", "get_secret", "os"):
            self.assertNotIn(forbidden, names, forbidden)

    def test_no_ai_telegram_scheduler_or_daemon(self):
        names = {n.lower() for n in _identifiers(cohort_mod)}
        for forbidden in ("telegram", "sendmessage", "openai", "anthropic",
                          "gemini", "groq", "completions", "thread", "timer",
                          "sleep", "crontab", "scheduler", "daemon"):
            self.assertFalse(any(forbidden in n for n in names), forbidden)

    def test_no_ddl_dml_or_persistence(self):
        upper = inspect.getsource(cohort_mod).upper()
        for verb in ("CREATE TABLE", "ALTER TABLE", "DROP TABLE", "TRUNCATE",
                     "INSERT INTO", "DELETE FROM", "CREATE INDEX"):
            self.assertNotIn(verb, upper, verb)
        names = _identifiers(cohort_mod)
        for forbidden in ("_save_persistent_state", "_load_persistent_state",
                          "insert_rows", "query_bars", "query_records",
                          "capture_daily_bars", "resolve_market_store"):
            self.assertNotIn(forbidden, names, forbidden)

    def test_no_module_level_mutable_state(self):
        tree = ast.parse(inspect.getsource(cohort_mod))
        for node in tree.body:
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names = {t.id for t in targets if isinstance(t, ast.Name)}
            if names == {"__all__"}:
                continue
            self.assertNotIsInstance(node.value, (ast.List, ast.Dict, ast.Set),
                                     sorted(names))

    def test_does_not_re_resolve_observations(self):
        """D-2D1 consumes frozen D-2D0 artifacts; it never calls D-2C2/D-2C3."""
        names = _identifiers(cohort_mod)
        for forbidden in ("resolve_direction_and_path", "resolve_setup_and_execution",
                          "build_validation_envelope", "build_verified_envelope",
                          "evaluate_observation", "classify_readiness"):
            self.assertNotIn(forbidden, names, forbidden)

    def test_build_cohort_never_raises_on_evidence(self):
        defect = _observation(horizon="not_a_horizon")
        bad = _observation(record_id="bad", evaluated_at="not-a-timestamp")
        good = _observation(record_id="good", evaluated_at=EVAL_AT)
        for batch in ([], [defect], [bad], [defect, bad, good], [good]):
            self.assertIsInstance(_build(batch), Cohort)


# ===========================================================================
# P. SCOPE AND EXISTING B2 PROTECTION   (13 tests)
# ===========================================================================
class TestScope(unittest.TestCase):
    def _unchanged(self, path):
        result = subprocess.run(["git", "diff", "--exit-code", "--", path],
                                cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0,
                         f"{path} changed:\n{result.stdout[:800]}")

    def test_the_evaluation_package_holds_exactly_three_modules(self):
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

    def test_observation_py_is_unchanged(self):
        self._unchanged("apex/b2/evaluation/observation.py")

    def test_every_validation_module_is_unchanged(self):
        for name in ("__init__", "anchor", "bars", "config", "maturity",
                     "outcome", "resolve", "series", "invalidation", "envelope",
                     "readiness"):
            self._unchanged(f"apex/b2/validation/{name}.py")

    def test_both_bridges_are_unchanged(self):
        self._unchanged("apex/b2_bridge.py")
        self._unchanged("apex/b2_validation_bridge.py")

    def test_production_core_sha_unchanged(self):
        with open(os.path.join(ROOT, "apex", "production_core.py"), "rb") as handle:
            digest = hashlib.sha256(handle.read()).hexdigest()
        self.assertEqual(
            digest, "5935f807a8584007fc053ae7bb64d62017a7e2f804258d492fdd8a4c2cb4da69")

    def test_production_core_is_byte_for_byte_unchanged(self):
        self._unchanged("apex/production_core.py")

    def test_cross_asset_remains_withheld(self):
        with open(os.path.join(ROOT, "apex", "b2", "shadow.py"),
                  encoding="utf-8") as handle:
            self.assertIn('CROSS_ASSET_STATUS = "withheld"', handle.read())

    def test_no_deferred_metric_has_arrived_early(self):
        names = {n.lower() for n in _identifiers(cohort_mod)}
        for forbidden in ("mfe", "mae", "r_multiple", "invalidation_rate",
                          "deferral", "calibrate", "calibration", "brier",
                          "reliability", "wilson", "p_value", "significance",
                          "confidence_interval", "sharpe", "pnl", "portfolio",
                          "promotion", "ablation", "walk_forward", "cross_asset",
                          "accuracy", "hit_rate", "win_rate"):
            self.assertFalse(any(forbidden in n for n in names), forbidden)

    def test_no_d3_entry_states_are_introduced(self):
        source = inspect.getsource(cohort_mod)
        for forbidden in ("ENTRY_JUSTIFIED", "ENTRY_PREMATURE", "ENTRY_LATE"):
            self.assertNotIn(forbidden, source, forbidden)

    def test_no_production_wiring(self):
        names = _identifiers(cohort_mod)
        for forbidden in ("b2_bridge", "b2_validation_bridge", "production_core",
                          "observe_instrument", "run_shadow_observation"):
            self.assertNotIn(forbidden, names, forbidden)

    def test_the_episode_count_is_documented_honestly(self):
        record = _build(_spread(2, step=TACTICAL_WINDOW)).as_record()
        self.assertIn("NOT a statistical effective sample size",
                      record["disjoint_episode_note"])
        names = _identifiers(cohort_mod)
        for forbidden in ("effective_sample_size", "ess", "independent_n"):
            self.assertNotIn(forbidden, names, forbidden)

    def test_results_are_immutable(self):
        cohort = _build(_spread(2, step=TACTICAL_WINDOW))
        with self.assertRaises(dataclasses.FrozenInstanceError):
            cohort.cohort_id = "tampered"
        with self.assertRaises(dataclasses.FrozenInstanceError):
            _only(cohort).deduplicated_n = 99


# ===========================================================================
# Q. PRIOR-STAGE REGRESSION -- rerun D-2C2..D-2C5 and D-2D0 internally.
# ===========================================================================
class TestPriorSuitesUnaffected(unittest.TestCase):
    def _run_suite(self, module_name):
        module = __import__(module_name, fromlist=["*"])
        loader = unittest.TestLoader()
        suite = loader.loadTestsFromModule(module)
        buffer = io.StringIO()
        result = unittest.TextTestRunner(stream=buffer, verbosity=0).run(suite)
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

    def test_d2d0_suite_still_passes(self):
        self._run_suite("tests.test_b2_stage_d2d_evaluation")


if __name__ == "__main__":
    unittest.main()
