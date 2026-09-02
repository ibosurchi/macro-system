"""B2 Stage D-5 -- forward outcome persistence (Tactical only).

What these tests pin, in order of how much it would cost to lose:

1.  The prediction fact and the market fact are NEVER touched. Persisting a
    conclusion must leave ``b2_shadow_records`` and ``b2_market_observations``
    byte-identical, and there is no update/delete/merge path to either.
2.  Persistence FAILS OPEN. A missing table, an unreachable endpoint or a
    refused row must leave the evaluation and the cohort intact and surface the
    failure separately.
3.  The gate withholds for NAMED reasons and never manufactures a verdict.
    NOT_MATURED writes nothing; Execution writes nothing; a lineage defect
    writes nothing; and each is counted rather than dropped.
4.  Identity is the JOB and the EVIDENCE, never the verdict. Re-running
    deduplicates; new evidence appends; the same evidence resolving two ways is
    caught as a determinism defect instead of looking like supersession.
5.  FINAL is earned by all three conditions, never asserted.

``test_same_evidence_different_verdict_is_a_conflict_not_a_second_row`` is the
one that would rot silently: nothing fails today if the identity quietly starts
including ``outcome_hash``, it just stops being able to detect non-determinism.
"""
from __future__ import annotations

import ast
import inspect
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from state_isolation import isolate_durable_state

isolate_durable_state()

from apex import b2_bridge
from apex import b2_validation_bridge as vb
from apex.b2.validation import outcomes as outcomes_mod
from apex.b2.validation.bars import canonical_observation_id
from apex.b2.validation.outcomes import (
    EXECUTION_HORIZON,
    PERSISTABLE_HORIZONS,
    GateDecision,
    OutcomeFinality,
    OutcomeRowError,
    build_outcome_row,
    build_outcome_rows,
    canonical_outcome_row_id,
    outcome_finality,
    persistence_gate,
)

AS_OF = datetime(2026, 9, 20, 12, 0, 0, tzinfo=timezone.utc)
AS_OF_ISO = AS_OF.isoformat()


# ---------------------------------------------------------------------------
# Fakes shaped like the real D-2D0 result, accepted structurally by outcomes.py
# ---------------------------------------------------------------------------
class _Ctx:
    def __init__(self, **kw):
        defaults = dict(
            validation_schema_version="b2-validation-envelope-v1",
            validation_config_version="b2-valcfg-v1",
            validation_config_hash="c" * 32,
            shadow_storage_id="storage-1",
            shadow_record_id="record-1",
            shadow_content_hash="s" * 32,
            shadow_schema_version=2,
            instrument="Gold",
            horizon="tactical",
            evaluated_at="2026-08-30T22:04:43+00:00",
            anchor_status="anchor_captured",
            market_symbol="GC=F",
            bound_symbol="GC=F",
            series_binding_quality="exact",
            inversion_agreement="agrees",
            eligibility_pool="captured",
            maturity_state="matured",
            coverage_status="resolvable",
            finalization_status="final",
            used_observation_ids=("obs-a", "obs-b"),
            used_bar_content_hashes=("hash-a", "hash-b"),
            terminal_observation_id="obs-b",
            terminal_bar_time="2026-09-12T04:00:00+00:00",
            used_bar_count=2,
            conflict_ids=(),
            malformed_row_count=0,
            duplicates_collapsed=0,
        )
        defaults.update(kw)
        for key, value in defaults.items():
            setattr(self, key, value)


class _Axes:
    def __init__(self, **kw):
        self.record = dict(
            data_resolution="resolved",
            direction_outcome="confirmed",
            setup_invalidation="not_invalidated",
            thesis_invalidation="not_assessable",
            execution_outcome="unresolved",
            eligibility_pool="captured",
            exclusion_reason=None,
            calibration_eligible=True,
            terminal_return=0.021,
            mfe=0.031,
            mae=0.004,
            mfe_atr=1.4,
            mae_atr=0.2,
            bars_to_mfe=6,
            bars_to_mae=1,
            path_bars=9,
        )
        self.record.update(kw)

    def as_record(self):
        return dict(self.record)


class _Value:
    def __init__(self, value):
        self.value = value


class _Envelope:
    def __init__(self, *, validation_id="a" * 32, input_hash="b" * 32,
                 outcome_hash="d" * 32, path_complete=True, context=None, axes=None):
        self.validation_id = validation_id
        self.input_hash = input_hash
        self.outcome_hash = outcome_hash
        self.context = context if context is not None else _Ctx()
        self.outcome_axes = axes if axes is not None else _Axes()
        self.outcome_hash_basis = {"path_complete": path_complete}


class _Evaluated:
    """Shaped like apex.b2.evaluation.observation.EvaluatedObservation."""

    is_defect = False

    def __init__(self, envelope=None, readiness="calibration_eligible",
                 claim_direction="bullish", asset_class="metal",
                 provenance_grade="captured_exact"):
        self.envelope = envelope if envelope is not None else _Envelope()
        self.readiness = _Value(readiness)
        self.claim_direction = _Value(claim_direction)
        self.asset_class = asset_class
        self.provenance_grade = _Value(provenance_grade)


class _Defect:
    """Shaped like apex.b2.evaluation.observation.LineageDefect."""

    is_defect = True

    def __init__(self, horizon="tactical"):
        self.horizon = horizon
        self.shadow_storage_id = "storage-defect"
        self.reason = _Value("lineage_verification_failed")


def _evaluated(**kw):
    ctx_kw = {k: v for k, v in kw.items() if k in ("horizon", "maturity_state",
                                                   "coverage_status", "finalization_status",
                                                   "used_observation_ids",
                                                   "used_bar_content_hashes",
                                                   "eligibility_pool")}
    env_kw = {k: v for k, v in kw.items() if k in ("validation_id", "input_hash",
                                                   "outcome_hash", "path_complete")}
    axes_kw = {k: v for k, v in kw.items() if k in ("data_resolution", "direction_outcome",
                                                    "setup_invalidation", "exclusion_reason",
                                                    "calibration_eligible")}
    return _Evaluated(
        envelope=_Envelope(context=_Ctx(**ctx_kw), axes=_Axes(**axes_kw), **env_kw)
    )


class FakeOutcomeLog:
    """Append-only stand-in for b2_validation_outcomes."""

    def __init__(self, raise_always=False):
        self.rows: dict[str, dict] = {}
        self.raise_always = raise_always
        self.insert_calls = 0

    def insert_rows(self, rows):
        self.insert_calls += 1
        if self.raise_always:
            raise RuntimeError("outcome log unreachable")
        inserted, duplicate, conflicted = [], [], []
        for row in rows:
            rid = row["outcome_row_id"]
            if rid in self.rows:
                if self.rows[rid]["outcome_hash"] != row["outcome_hash"]:
                    conflicted.append(rid)          # never overwritten
                else:
                    duplicate.append(rid)
                continue
            stamped = dict(row)
            stamped["first_seen_at"] = f"seen-{len(self.rows)}"   # DB default
            self.rows[rid] = stamped
            inserted.append(rid)
        return b2_bridge.InsertOutcome(
            backend="supabase", durable=True, inserted=tuple(inserted),
            duplicate=tuple(duplicate), conflicted=tuple(conflicted),
        )


# ---------------------------------------------------------------------------
# 1. Identity
# ---------------------------------------------------------------------------
class TestOutcomeIdentity(unittest.TestCase):
    def test_outcome_row_id_is_deterministic_32_hex(self):
        first = canonical_outcome_row_id("job", "evidence")
        self.assertEqual(first, canonical_outcome_row_id("job", "evidence"))
        self.assertEqual(len(first), 32)
        self.assertTrue(all(c in "0123456789abcdef" for c in first))

    def test_identity_changes_with_the_evidence(self):
        self.assertNotEqual(
            canonical_outcome_row_id("job", "evidence_a"),
            canonical_outcome_row_id("job", "evidence_b"),
        )

    def test_identity_changes_with_the_job(self):
        self.assertNotEqual(
            canonical_outcome_row_id("job_a", "evidence"),
            canonical_outcome_row_id("job_b", "evidence"),
        )

    def test_the_outcome_domain_is_disjoint_from_the_others(self):
        self.assertNotEqual(
            canonical_outcome_row_id("a", "b"),
            canonical_observation_id("a", "b", "c", "d"),
        )

    def test_identity_carries_neither_a_clock_nor_the_verdict(self):
        """The whole detection guarantee rests on this. If outcome_hash ever
        enters the basis, a non-deterministic verdict stops colliding and
        starts looking like ordinary supersession."""
        source = inspect.getsource(outcomes_mod.canonical_outcome_row_id)
        body = source.split('"""')[-1]
        for forbidden in ("outcome_hash", "first_seen_at", "now", "datetime"):
            self.assertNotIn(forbidden, body, forbidden)

    def test_row_never_carries_first_seen_at(self):
        row = build_outcome_row(evaluated=_evaluated(), as_of=AS_OF_ISO).to_row()
        self.assertNotIn("first_seen_at", row)
        self.assertEqual(row["as_of"], AS_OF_ISO)

    def test_row_id_matches_its_own_declared_derivation(self):
        row = build_outcome_row(evaluated=_evaluated(), as_of=AS_OF_ISO).to_row()
        self.assertEqual(
            row["outcome_row_id"],
            canonical_outcome_row_id(row["validation_id"], row["input_hash"]),
        )


# ---------------------------------------------------------------------------
# 2. Idempotency and supersession
# ---------------------------------------------------------------------------
class TestIdempotency(unittest.TestCase):
    def test_the_same_evidence_is_recorded_once_however_often_it_is_run(self):
        log = FakeOutcomeLog()
        rows = [build_outcome_row(evaluated=_evaluated(), as_of=AS_OF_ISO).to_row()]
        first = log.insert_rows(rows)
        self.assertEqual((len(first.inserted), len(first.duplicate)), (1, 0))
        for _ in range(4):
            again = log.insert_rows(rows)
            self.assertEqual(len(again.inserted), 0)
            self.assertEqual(len(again.duplicate), 1)
        self.assertEqual(len(log.rows), 1)

    def test_first_seen_at_is_preserved_across_reruns(self):
        log = FakeOutcomeLog()
        row = build_outcome_row(evaluated=_evaluated(), as_of=AS_OF_ISO).to_row()
        log.insert_rows([row])
        before = dict(next(iter(log.rows.values())))
        log.insert_rows([row])
        log.insert_rows([row])
        self.assertEqual(next(iter(log.rows.values())), before)

    def test_partial_then_complete_appends_a_superseding_row(self):
        """New bars legitimately change the answer. The first row is never
        rewritten; the second stands beside it."""
        log = FakeOutcomeLog()
        partial = build_outcome_row(
            evaluated=_evaluated(
                input_hash="1" * 32, outcome_hash="1" * 32,
                maturity_state="matured_partial", data_resolution="partial",
                path_complete=False,
            ),
            as_of=AS_OF_ISO,
        )
        complete = build_outcome_row(
            evaluated=_evaluated(input_hash="2" * 32, outcome_hash="2" * 32),
            as_of=AS_OF_ISO,
        )
        self.assertEqual(partial.validation_id, complete.validation_id)
        self.assertNotEqual(partial.outcome_row_id, complete.outcome_row_id)

        log.insert_rows([partial.to_row()])
        before = dict(log.rows[partial.outcome_row_id])
        outcome = log.insert_rows([complete.to_row()])

        self.assertEqual(len(outcome.inserted), 1)
        self.assertEqual(len(log.rows), 2)
        self.assertEqual(log.rows[partial.outcome_row_id], before)
        self.assertEqual(partial.finality, OutcomeFinality.PROVISIONAL)
        self.assertEqual(complete.finality, OutcomeFinality.FINAL)

    def test_same_evidence_different_verdict_is_a_conflict_not_a_second_row(self):
        """THE detection test. One job, one set of evidence, two verdicts is a
        determinism defect in this codebase -- never a market event."""
        log = FakeOutcomeLog()
        first = build_outcome_row(
            evaluated=_evaluated(outcome_hash="a" * 32), as_of=AS_OF_ISO
        )
        second = build_outcome_row(
            evaluated=_evaluated(outcome_hash="f" * 32), as_of=AS_OF_ISO
        )
        self.assertEqual(first.outcome_row_id, second.outcome_row_id)

        log.insert_rows([first.to_row()])
        stored = dict(log.rows[first.outcome_row_id])
        outcome = log.insert_rows([second.to_row()])

        self.assertEqual(len(outcome.conflicted), 1)
        self.assertEqual(len(outcome.inserted), 0)
        self.assertEqual(len(outcome.duplicate), 0)
        self.assertEqual(len(log.rows), 1)
        self.assertEqual(log.rows[first.outcome_row_id], stored, "never overwritten")


# ---------------------------------------------------------------------------
# 3. The persistence gate
# ---------------------------------------------------------------------------
class TestPersistenceGate(unittest.TestCase):
    def test_a_matured_tactical_observation_persists(self):
        gate = persistence_gate(_evaluated())
        self.assertIs(gate.decision, GateDecision.PERSIST)
        self.assertTrue(gate.persists)

    def test_not_matured_is_never_persisted(self):
        """The single most damaging error this stage could make: reporting an
        observation as anything because time has passed."""
        gate = persistence_gate(_evaluated(maturity_state="not_matured"))
        self.assertIs(gate.decision, GateDecision.WITHHELD_NOT_MATURED)
        with self.assertRaises(OutcomeRowError):
            build_outcome_row(
                evaluated=_evaluated(maturity_state="not_matured"), as_of=AS_OF_ISO
            )

    def test_matured_awaiting_bars_is_withheld_not_judged(self):
        """The window elapsed but nobody looked past it. That is our backlog,
        not the market's absence."""
        gate = persistence_gate(_evaluated(maturity_state="matured_awaiting_bars"))
        self.assertIs(gate.decision, GateDecision.WITHHELD_NO_VERDICT_PERMITTED)

    def test_matured_partial_is_eligible_as_provisional(self):
        gate = persistence_gate(_evaluated(maturity_state="matured_partial"))
        self.assertIs(gate.decision, GateDecision.PERSIST)

    def test_execution_is_withheld_on_granularity_grounds(self):
        gate = persistence_gate(_evaluated(horizon="execution"))
        self.assertIs(gate.decision, GateDecision.WITHHELD_EXECUTION_GRANULARITY)

    def test_a_matured_execution_observation_is_still_withheld(self):
        """Withheld for granularity, NOT for immaturity -- the reason must not
        change when it matures, because the answer does not."""
        gate = persistence_gate(
            _evaluated(horizon="execution", maturity_state="matured")
        )
        self.assertIs(gate.decision, GateDecision.WITHHELD_EXECUTION_GRANULARITY)

    def test_structural_is_withheld_as_not_activated(self):
        gate = persistence_gate(_evaluated(horizon="structural"))
        self.assertIs(gate.decision, GateDecision.WITHHELD_HORIZON_NOT_ACTIVATED)

    def test_a_lineage_defect_never_becomes_an_outcome(self):
        gate = persistence_gate(_Defect())
        self.assertIs(gate.decision, GateDecision.WITHHELD_LINEAGE_DEFECT)
        with self.assertRaises(OutcomeRowError):
            build_outcome_row(evaluated=_Defect(), as_of=AS_OF_ISO)

    def test_an_unreadable_input_is_withheld_rather_than_guessed(self):
        for bad in (None, object()):
            self.assertIs(
                persistence_gate(bad).decision, GateDecision.WITHHELD_LINEAGE_DEFECT
            )

    def test_only_tactical_is_persistable(self):
        self.assertEqual(PERSISTABLE_HORIZONS, frozenset({"tactical"}))
        self.assertEqual(EXECUTION_HORIZON, "execution")

    def test_the_census_accounts_for_every_input(self):
        batch = [
            _evaluated(),
            _evaluated(maturity_state="not_matured"),
            _evaluated(horizon="execution"),
            _evaluated(horizon="structural"),
            _Defect(),
        ]
        rows, census = build_outcome_rows(evaluated=batch, as_of=AS_OF_ISO)
        self.assertEqual(len(rows), 1)
        self.assertEqual(sum(census.values()), len(batch), "totals must reconcile")
        self.assertEqual(census["persist"], 1)
        self.assertEqual(census["withheld_not_matured"], 1)
        self.assertEqual(census["withheld_execution_granularity"], 1)
        self.assertEqual(census["withheld_horizon_not_activated"], 1)
        self.assertEqual(census["withheld_lineage_defect"], 1)


# ---------------------------------------------------------------------------
# 4. Finality
# ---------------------------------------------------------------------------
class TestFinality(unittest.TestCase):
    def test_final_requires_all_three_conditions(self):
        self.assertIs(
            outcome_finality(
                maturity_state="matured", data_resolution="resolved", path_complete=True
            ),
            OutcomeFinality.FINAL,
        )

    def test_any_missing_condition_is_provisional(self):
        for kwargs in (
            dict(maturity_state="matured_partial", data_resolution="resolved", path_complete=True),
            dict(maturity_state="matured", data_resolution="partial", path_complete=True),
            dict(maturity_state="matured", data_resolution="resolved", path_complete=False),
            dict(maturity_state="matured", data_resolution="resolved", path_complete=None),
        ):
            self.assertIs(outcome_finality(**kwargs), OutcomeFinality.PROVISIONAL, kwargs)

    def test_path_complete_is_identity_checked_never_truthy_checked(self):
        """A stray non-empty string must never be promoted into completeness."""
        self.assertIs(
            outcome_finality(
                maturity_state="matured", data_resolution="resolved", path_complete="yes"
            ),
            OutcomeFinality.PROVISIONAL,
        )

    def test_a_proven_invalidation_cannot_silently_reverse(self):
        """D-2C3 makes a touch final under partial coverage. When better
        coverage arrives the new row must still carry it -- and because the row
        is appended rather than updated, the proof survives either way."""
        touched = _evaluated(
            input_hash="1" * 32, outcome_hash="1" * 32,
            maturity_state="matured_partial", data_resolution="partial",
            path_complete=False, setup_invalidation="invalidated",
        )
        later = _evaluated(
            input_hash="2" * 32, outcome_hash="2" * 32,
            setup_invalidation="invalidated",
        )
        log = FakeOutcomeLog()
        first = build_outcome_row(evaluated=touched, as_of=AS_OF_ISO)
        log.insert_rows([first.to_row()])
        before = dict(log.rows[first.outcome_row_id])

        second = build_outcome_row(evaluated=later, as_of=AS_OF_ISO)
        log.insert_rows([second.to_row()])

        self.assertEqual(log.rows[first.outcome_row_id], before)
        self.assertEqual(before["setup_invalidation"], "invalidated")
        self.assertEqual(
            log.rows[second.outcome_row_id]["setup_invalidation"], "invalidated"
        )


# ---------------------------------------------------------------------------
# 5. Row content -- delegated, never re-derived
# ---------------------------------------------------------------------------
class TestRowContent(unittest.TestCase):
    def test_the_row_carries_the_ordered_input_hash_preimage(self):
        row = build_outcome_row(evaluated=_evaluated(), as_of=AS_OF_ISO).to_row()
        self.assertEqual(
            row["bar_evidence"],
            [
                {"observation_id": "obs-a", "content_hash": "hash-a"},
                {"observation_id": "obs-b", "content_hash": "hash-b"},
            ],
        )

    def test_bar_evidence_preserves_order_and_is_never_sorted(self):
        row = build_outcome_row(
            evaluated=_evaluated(
                used_observation_ids=("z", "a"), used_bar_content_hashes=("hz", "ha")
            ),
            as_of=AS_OF_ISO,
        ).to_row()
        self.assertEqual([e["observation_id"] for e in row["bar_evidence"]], ["z", "a"])

    def test_the_six_axes_are_copied_verbatim_from_the_envelope(self):
        row = build_outcome_row(evaluated=_evaluated(), as_of=AS_OF_ISO).to_row()
        for key, expected in (
            ("data_resolution", "resolved"),
            ("direction_outcome", "confirmed"),
            ("setup_invalidation", "not_invalidated"),
            ("thesis_invalidation", "not_assessable"),
            ("execution_outcome", "unresolved"),
            ("eligibility_pool", "captured"),
        ):
            self.assertEqual(row[key], expected, key)

    def test_the_row_carries_no_shadow_payload_copy(self):
        """Identity and integrity only. b2_shadow_records is append-only, so a
        join can never go stale and a copy could only ever disagree."""
        row = build_outcome_row(evaluated=_evaluated(), as_of=AS_OF_ISO).to_row()
        self.assertIn("shadow_storage_id", row)
        self.assertIn("shadow_content_hash", row)
        for forbidden in ("record", "payload", "claim", "market_anchor", "decision"):
            self.assertNotIn(forbidden, row, forbidden)

    def test_an_envelope_missing_its_identity_cannot_become_a_row(self):
        broken = _evaluated()
        broken.envelope.input_hash = ""
        with self.assertRaises(OutcomeRowError):
            build_outcome_row(evaluated=broken, as_of=AS_OF_ISO)


# ---------------------------------------------------------------------------
# 6. persist_validation_outcomes -- dry run, fail-open, no hidden writes
# ---------------------------------------------------------------------------
class TestPersistEntryPoint(unittest.TestCase):
    def test_dry_run_is_the_default_and_issues_no_request(self):
        log = FakeOutcomeLog()
        report = vb.persist_validation_outcomes(
            [_evaluated()], as_of=AS_OF, outcome_store=log
        )
        self.assertFalse(report["persist_attempted"])
        self.assertEqual(report["outcomes_eligible"], 1)
        self.assertEqual(report["outcomes_written"], 0)
        self.assertEqual(log.insert_calls, 0, "a dry run must write nothing")

    def test_persist_records_the_eligible_outcome(self):
        log = FakeOutcomeLog()
        report = vb.persist_validation_outcomes(
            [_evaluated()], as_of=AS_OF, outcome_store=log, persist=True
        )
        self.assertEqual(report["outcomes_written"], 1)
        self.assertEqual(report["outcomes_final"], 1)
        self.assertEqual(report["outcomes_provisional"], 0)
        self.assertEqual(report["persistence_error"], "")

    def test_zero_eligible_is_a_normal_result_with_no_request(self):
        log = FakeOutcomeLog()
        report = vb.persist_validation_outcomes(
            [_evaluated(maturity_state="not_matured")],
            as_of=AS_OF, outcome_store=log, persist=True,
        )
        self.assertEqual(report["outcomes_eligible"], 0)
        self.assertEqual(report["outcomes_written"], 0)
        self.assertEqual(report["persistence_error"], "")
        self.assertEqual(log.insert_calls, 0)

    def test_an_unreachable_log_fails_open(self):
        report = vb.persist_validation_outcomes(
            [_evaluated()], as_of=AS_OF,
            outcome_store=FakeOutcomeLog(raise_always=True), persist=True,
        )
        self.assertEqual(report["outcomes_eligible"], 1)
        self.assertEqual(report["outcomes_written"], 0)
        self.assertEqual(report["outcomes_failed"], 1)
        self.assertIn("unreachable", report["persistence_error"])

    def test_no_outcome_store_never_reaches_for_the_real_cloud_log(self):
        with mock.patch.object(vb, "resolve_outcome_store") as resolver:
            report = vb.persist_validation_outcomes(
                [_evaluated()], as_of=AS_OF, outcome_store=None, persist=True
            )
        resolver.assert_not_called()
        self.assertEqual(report["outcomes_written"], 0)
        self.assertIn("no outcome store", report["persistence_error"])

    def test_a_determinism_conflict_is_surfaced_not_absorbed(self):
        log = FakeOutcomeLog()
        vb.persist_validation_outcomes(
            [_evaluated(outcome_hash="a" * 32)], as_of=AS_OF,
            outcome_store=log, persist=True,
        )
        report = vb.persist_validation_outcomes(
            [_evaluated(outcome_hash="f" * 32)], as_of=AS_OF,
            outcome_store=log, persist=True,
        )
        self.assertEqual(len(report["outcomes_conflicted"]), 1)
        self.assertEqual(report["outcomes_written"], 0)


# ---------------------------------------------------------------------------
# 7. Store posture, purity and immutability
# ---------------------------------------------------------------------------
class TestStorePosture(unittest.TestCase):
    def test_the_outcome_insert_is_do_nothing_never_merge(self):
        source = inspect.getsource(vb.SupabaseValidationOutcomeStore.insert_rows)
        self.assertIn("resolution=ignore-duplicates", source)
        self.assertIn('"on_conflict": "outcome_row_id"', source)
        self.assertNotIn("merge-duplicates", source)

    def test_no_mutating_verb_reaches_the_outcome_log(self):
        source = inspect.getsource(vb.SupabaseValidationOutcomeStore)
        for forbidden in ("requests.patch", "requests.delete", "requests.put"):
            self.assertNotIn(forbidden, source, forbidden)

    def test_conflict_classification_reads_the_stored_hash_column(self):
        source = inspect.getsource(
            vb.SupabaseValidationOutcomeStore._classify_outcome_duplicates
        )
        self.assertIn("stored_outcome_hash", source)
        reader = inspect.getsource(vb.SupabaseValidationOutcomeStore.stored_outcome_hash)
        self.assertIn('"select": "outcome_hash"', reader)

    def test_conflict_is_never_asserted_when_it_cannot_be_checked(self):
        store = vb.SupabaseValidationOutcomeStore()
        row = build_outcome_row(evaluated=_evaluated(), as_of=AS_OF_ISO).to_row()
        with mock.patch.object(store, "stored_outcome_hash", return_value=None):
            duplicate, conflicted = store._classify_outcome_duplicates(
                [row], [row["outcome_row_id"]]
            )
        self.assertEqual(len(duplicate), 1)
        self.assertEqual(conflicted, [])

    def test_the_local_mirror_is_append_only_and_never_durable(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "outcomes.jsonl")
            store = vb.LocalValidationOutcomeStore(path=path)
            row = build_outcome_row(evaluated=_evaluated(), as_of=AS_OF_ISO).to_row()

            first = store.insert_rows([row])
            self.assertEqual(len(first.inserted), 1)
            self.assertFalse(first.durable)

            again = store.insert_rows([row])
            self.assertEqual(len(again.inserted), 0)
            self.assertEqual(len(again.duplicate), 1)

            with open(path, encoding="utf-8") as handle:
                lines = [json.loads(l) for l in handle if l.strip()]
            self.assertEqual(len(lines), 1)
            self.assertEqual(store.row_count(), 1)

    def test_the_outcomes_module_is_pure_and_guard_safe(self):
        tree = ast.parse(inspect.getsource(outcomes_mod))
        modules: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                modules.add(node.module or "")
            if isinstance(node, ast.Import):
                modules.update(a.name for a in node.names)
        for forbidden in ("requests", "streamlit", "threading", "production_core",
                          "socket", "urllib", "os", "json"):
            self.assertFalse(
                any(forbidden == m or m.endswith("." + forbidden) for m in modules),
                forbidden,
            )
        # The four guarded modules. Importing any of them nominally would
        # defeat a standing architectural test rather than satisfy it.
        for guarded in ("resolve", "envelope", "readiness", "invalidation"):
            self.assertNotIn(guarded, modules, guarded)

    def test_no_ddl_verb_appears_in_the_outcome_modules(self):
        for module in (outcomes_mod, vb):
            source = inspect.getsource(module).upper()
            for verb in ("CREATE TABLE", "ALTER TABLE", "DROP TABLE",
                         "TRUNCATE", "CREATE INDEX"):
                self.assertNotIn(verb, source, f"{module.__name__}:{verb}")

    def test_no_calibration_or_accuracy_logic_is_introduced(self):
        """D-5 accumulates evidence. It computes no rate of any kind."""
        for module in (outcomes_mod, vb):
            source = inspect.getsource(module).lower()
            for forbidden in ("hit_rate", "win_rate", "accuracy", "calibrate(",
                              "def calibrate"):
                self.assertNotIn(forbidden, source, f"{module.__name__}:{forbidden}")

    def test_the_migration_file_exists_and_is_never_executed_by_the_app(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(root, "sql", "002_b2_validation_outcomes.sql")
        self.assertTrue(os.path.isfile(path), path)
        with open(path, encoding="utf-8") as handle:
            ddl = handle.read()
        for expected in ("b2_validation_outcomes", "outcome_row_id",
                         "b2_vo_natural_key_uq", "b2_vo_final_earned_ck",
                         "b2_vo_horizon_ck", "enable row level security"):
            self.assertIn(expected, ddl, expected)
        # Tactical-only is asserted by the DATABASE, not merely by the gate.
        self.assertIn("check (horizon = 'tactical')", ddl)
        # The bridge may NAME the migration in prose but must never open it.
        tree = ast.parse(inspect.getsource(vb))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "open":
                for arg in node.args:
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        self.assertNotIn(".sql", arg.value)


# ---------------------------------------------------------------------------
# 8. Vendor revision isolation
# ---------------------------------------------------------------------------
class TestRevisionIsolation(unittest.TestCase):
    """The D-4 revision store must remain AUDIT ONLY.

    A revision is data that did not exist when the evaluation was made. Letting
    it reach resolution would mean an already-evaluated outcome could change
    because of it -- which IS look-ahead, and the worst kind, because it would
    be invisible.
    """

    def _validation_modules(self):
        from apex.b2.validation import resolve, invalidation, envelope, readiness, series
        from apex.b2.evaluation import observation, cohort
        return (resolve, invalidation, envelope, readiness, series, observation,
                cohort, outcomes_mod)

    def test_no_evaluation_module_names_the_revision_table_or_store(self):
        for module in self._validation_modules():
            source = inspect.getsource(module)
            for forbidden in (
                "b2_market_observation_revisions",
                "MARKET_OBSERVATION_REVISIONS_TABLE",
                "SupabaseMarketObservationRevisionStore",
                "LocalMarketObservationRevisionStore",
                "resolve_revision_store",
            ):
                self.assertNotIn(forbidden, source, f"{module.__name__}:{forbidden}")

    def test_no_evaluation_module_imports_the_revisions_module(self):
        for module in self._validation_modules():
            tree = ast.parse(inspect.getsource(module))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    self.assertNotIn("revisions", node.module or "", module.__name__)
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertNotIn("revisions", alias.name, module.__name__)

    def test_the_revision_stores_expose_no_read_path_at_all(self):
        """Structural, not conventional: the application literally cannot read
        the revision log, so no future caller can accidentally consume it."""
        for store in (vb.SupabaseMarketObservationRevisionStore,
                      vb.LocalMarketObservationRevisionStore):
            for reader in ("query_bars", "query_bars_result", "get", "select",
                           "read", "fetch", "stored_row"):
                self.assertFalse(hasattr(store, reader), f"{store.__name__}.{reader}")

    def test_the_bar_read_path_queries_only_the_observations_table(self):
        source = inspect.getsource(vb.SupabaseMarketObservationStore.query_bars_result)
        self.assertNotIn("REVISION", source.upper())

    def test_a_recorded_revision_cannot_alter_a_persisted_outcome(self):
        """A revision changes only volume, and volume enters no resolution
        arithmetic. The outcome row is byte-identical either way."""
        log = FakeOutcomeLog()
        row = build_outcome_row(evaluated=_evaluated(), as_of=AS_OF_ISO).to_row()
        log.insert_rows([row])
        before = dict(next(iter(log.rows.values())))

        revision_store = vb.LocalMarketObservationRevisionStore(path=os.devnull)
        self.assertFalse(hasattr(revision_store, "query_bars"))

        log.insert_rows([row])
        self.assertEqual(next(iter(log.rows.values())), before)


if __name__ == "__main__":
    unittest.main()
