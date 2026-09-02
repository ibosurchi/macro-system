"""B2 Stage D-4 -- the safe revision store.

What these tests pin, in order of how much it would cost to lose:

1.  The original observation is NEVER touched. Recording a revision must leave
    ``b2_market_observations`` byte-identical.
2.  Conflict classification is unchanged and upstream. Revisions are recorded
    from ids the store ALREADY classified as conflicted, and nothing about the
    revision path can change that classification.
3.  Revision recording FAILS OPEN. A missing table, an unreachable endpoint or a
    refused row must leave inserted/duplicate/conflicted intact and surface the
    failure separately.
4.  Identity deduplicates the same revision and appends a different one.
5.  Classification never treats a PostgREST read-back float as an authority.

The float-truncation regression (``TestFloatReadBackSafety``) is the one that
would silently rot: nothing fails today if the classifier starts comparing
read-back floats, it just starts calling every FX row a price revision.
"""
from __future__ import annotations

import ast
import inspect
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from state_isolation import isolate_durable_state

isolate_durable_state()

from apex import b2_bridge
from apex import b2_validation_bridge as vb
from apex.b2.validation import revisions as rev_mod
from apex.b2.validation.bars import (
    GRANULARITY_1D,
    MarketBar,
    MarketObservationError,
    canonical_bar_content_hash,
    canonical_observation_id,
)
from apex.b2.validation.revisions import (
    RevisionKind,
    build_revision,
    canonical_revision_id,
    classify_revision,
    transport_precision,
)

NOW = datetime(2026, 9, 15, 12, 0, 0, tzinfo=timezone.utc)

# The real GC=F 2026-08-28 conflict, from the Supabase row and the fresh Yahoo
# payload that produced it. Open/high/low/close were bit-identical across both
# captures; only volume moved, 5558 -> 1758.
GOLD_BAR_TIME = datetime(2026, 8, 28, 4, 0, 0, tzinfo=timezone.utc)
GOLD_OHLC = dict(o=4599.2998046875, h=4625.5, l=4451.7998046875, c=4478.10009765625)
GOLD_STORED_VOLUME = 5558.0
GOLD_REVISED_VOLUME = 1758.0
GOLD_OBSERVATION_ID = "2139b73fe861cde6d05d1cfb5e62a8bc"


def _bar(volume=GOLD_REVISED_VOLUME, symbol="GC=F", **overrides):
    values = dict(GOLD_OHLC)
    values.update(overrides)
    return MarketBar(
        symbol=symbol,
        instrument="Gold",
        granularity=GRANULARITY_1D,
        bar_time=GOLD_BAR_TIME,
        open=values["o"],
        high=values["h"],
        low=values["l"],
        close=values["c"],
        volume=volume,
        invert=False,
    )


def _stored_row(bar, *, truncate=False):
    """The row that bar would read back as, optionally with PostgREST's
    15-significant-digit truncation applied to the numerics."""
    row = bar.to_row()
    if truncate:
        for field in ("open", "high", "low", "close", "volume"):
            if row[field] is not None:
                row[field] = float(f"{float(row[field]):.15g}")
    return row


class FakeMarketTable:
    """Append-only stand-in for b2_market_observations, with stored_row()."""

    def __init__(self):
        self.rows: dict[str, dict] = {}
        self.available = True
        self.stored_row_calls: list[str] = []

    def insert_rows(self, rows):
        inserted, duplicate, conflicted = [], [], []
        for row in rows:
            oid = row["observation_id"]
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

    def stored_row(self, observation_id):
        self.stored_row_calls.append(observation_id)
        row = self.rows.get(observation_id)
        return dict(row) if row else None

    def row_count(self):
        return len(self.rows)


class FakeRevisionLog:
    """Append-only stand-in for b2_market_observation_revisions."""

    def __init__(self, raise_always=False):
        self.rows: dict[str, dict] = {}
        self.raise_always = raise_always
        self.available = True
        self.insert_calls = 0

    def insert_rows(self, rows):
        self.insert_calls += 1
        if self.raise_always:
            raise RuntimeError("revision log unreachable")
        inserted, duplicate = [], []
        for row in rows:
            rid = row["revision_id"]
            if rid in self.rows:
                duplicate.append(rid)       # ON CONFLICT DO NOTHING
                continue
            stamped = dict(row)
            # The database's own default, which the client never sends.
            stamped["first_seen_at"] = f"seen-{len(self.rows)}"
            self.rows[rid] = stamped
            inserted.append(rid)
        return b2_bridge.InsertOutcome(
            backend="supabase", durable=True,
            inserted=tuple(inserted), duplicate=tuple(duplicate),
        )


# ---------------------------------------------------------------------------
# 1. Revision identity and idempotency
# ---------------------------------------------------------------------------
class TestRevisionIdentity(unittest.TestCase):
    def test_revision_id_is_deterministic_and_32_hex(self):
        first = canonical_revision_id("obs", "hash")
        self.assertEqual(first, canonical_revision_id("obs", "hash"))
        self.assertEqual(len(first), 32)
        self.assertTrue(all(c in "0123456789abcdef" for c in first))

    def test_revision_id_changes_with_the_revised_content(self):
        self.assertNotEqual(
            canonical_revision_id("obs", "hash_a"),
            canonical_revision_id("obs", "hash_b"),
        )

    def test_revision_id_changes_with_the_observation(self):
        self.assertNotEqual(
            canonical_revision_id("obs_a", "hash"),
            canonical_revision_id("obs_b", "hash"),
        )

    def test_the_revision_domain_is_disjoint_from_the_observation_domain(self):
        """A revision id can never collide with an observation id by sharing a
        basis: the 'rev' domain tag makes the two hash inputs disjoint."""
        observation = canonical_observation_id("a", "b", "c", "d")
        self.assertNotEqual(canonical_revision_id("a", "b"), observation)

    def test_identity_carries_no_clock(self):
        """The whole dedup guarantee rests on this. If a clock ever enters the
        basis, every re-capture re-records the same vendor correction."""
        source = inspect.getsource(rev_mod.canonical_revision_id)
        for forbidden in ("first_seen_at", "captured_at", "now", "datetime"):
            self.assertNotIn(forbidden, source.split('"""')[-1], forbidden)

    def test_the_same_revision_is_recorded_once_however_often_it_is_seen(self):
        log = FakeRevisionLog()
        revision = build_revision(
            observation_id=_bar().observation_id,
            original_content_hash=_bar(volume=GOLD_STORED_VOLUME).content_hash,
            stored_row=_stored_row(_bar(volume=GOLD_STORED_VOLUME)),
            bar=_bar(),
            captured_at=NOW.isoformat(),
            resolver_version="v1",
        )
        first = log.insert_rows([revision.to_row()])
        self.assertEqual((len(first.inserted), len(first.duplicate)), (1, 0))
        for _ in range(4):
            again = log.insert_rows([revision.to_row()])
            self.assertEqual(len(again.inserted), 0)
            self.assertEqual(len(again.duplicate), 1)
        self.assertEqual(len(log.rows), 1)

    def test_a_later_different_revision_appends_beside_the_first(self):
        log = FakeRevisionLog()
        original = _bar(volume=GOLD_STORED_VOLUME)
        kwargs = dict(
            observation_id=original.observation_id,
            original_content_hash=original.content_hash,
            stored_row=_stored_row(original),
            captured_at=NOW.isoformat(),
            resolver_version="v1",
        )
        first = build_revision(bar=_bar(volume=1758.0), **kwargs)
        second = build_revision(bar=_bar(volume=1799.0), **kwargs)
        self.assertNotEqual(first.revision_id, second.revision_id)

        log.insert_rows([first.to_row()])
        before = dict(log.rows[first.revision_id])
        outcome = log.insert_rows([second.to_row()])

        self.assertEqual(len(outcome.inserted), 1)
        self.assertEqual(len(log.rows), 2)
        self.assertEqual(log.rows[first.revision_id], before)
        self.assertEqual(before["first_seen_at"], log.rows[first.revision_id]["first_seen_at"])

    def test_a_row_never_carries_first_seen_at(self):
        """The database stamps it, so a client cannot backdate it."""
        revision = build_revision(
            observation_id=_bar().observation_id,
            original_content_hash=_bar(volume=GOLD_STORED_VOLUME).content_hash,
            stored_row=_stored_row(_bar(volume=GOLD_STORED_VOLUME)),
            bar=_bar(),
            captured_at=NOW.isoformat(),
            resolver_version="v1",
        )
        self.assertNotIn("first_seen_at", revision.to_row())
        self.assertEqual(revision.to_row()["captured_at"], NOW.isoformat())

    def test_a_revision_must_differ_from_what_it_revises(self):
        same = _bar()
        with self.assertRaises(MarketObservationError):
            build_revision(
                observation_id=same.observation_id,
                original_content_hash=same.content_hash,
                stored_row=_stored_row(same),
                bar=same,
                captured_at=NOW.isoformat(),
                resolver_version="v1",
            )

    def test_a_revision_cannot_be_attached_to_a_different_bar(self):
        other = _bar(symbol="CL=F", volume=1.0)
        with self.assertRaises(MarketObservationError):
            build_revision(
                observation_id=_bar().observation_id,
                original_content_hash="0" * 32,
                stored_row=_stored_row(_bar(volume=GOLD_STORED_VOLUME)),
                bar=other,
                captured_at=NOW.isoformat(),
                resolver_version="v1",
            )


# ---------------------------------------------------------------------------
# 2. Classification
# ---------------------------------------------------------------------------
class TestRevisionClassification(unittest.TestCase):
    def _classify(self, stored_bar, fresh_bar, *, truncate=False):
        return classify_revision(
            original_content_hash=stored_bar.content_hash,
            stored_row=_stored_row(stored_bar, truncate=truncate),
            bar=fresh_bar,
        )

    def test_the_real_gold_conflict_is_volume_only(self):
        """GC=F 2026-08-28: OHLC bit-identical, volume 5558 -> 1758."""
        result = self._classify(_bar(volume=GOLD_STORED_VOLUME), _bar())
        self.assertIs(result.kind, RevisionKind.VOLUME_ONLY)
        self.assertEqual(result.changed_fields, ("volume",))

    def test_a_changed_close_is_a_price_revision(self):
        stored = _bar(volume=GOLD_STORED_VOLUME)
        fresh = _bar(volume=GOLD_STORED_VOLUME, c=4479.0)
        result = self._classify(stored, fresh)
        self.assertIs(result.kind, RevisionKind.PRICE)
        self.assertIn("close", result.changed_fields)

    def test_a_price_and_volume_revision_is_price_and_names_both(self):
        stored = _bar(volume=GOLD_STORED_VOLUME)
        fresh = _bar(volume=1758.0, c=4479.0)
        result = self._classify(stored, fresh)
        self.assertIs(result.kind, RevisionKind.PRICE)
        self.assertIn("close", result.changed_fields)
        self.assertIn("volume", result.changed_fields)

    def test_an_unreadable_stored_row_is_other_not_a_guess(self):
        result = classify_revision(
            original_content_hash="0" * 32, stored_row=None, bar=_bar()
        )
        self.assertIs(result.kind, RevisionKind.OTHER)
        self.assertEqual(result.changed_fields, (rev_mod.UNATTRIBUTED,))

    def test_a_hash_difference_nothing_explains_is_other(self):
        """Values agree at every readable precision, yet the authoritative hash
        disagrees. Escalated, never filed as benign."""
        stored = _stored_row(_bar())
        result = classify_revision(
            original_content_hash="f" * 32, stored_row=stored, bar=_bar()
        )
        self.assertIs(result.kind, RevisionKind.OTHER)
        self.assertEqual(result.changed_fields, (rev_mod.UNATTRIBUTED,))

    def test_a_volume_that_cannot_be_read_is_other_never_volume_only(self):
        stored = _stored_row(_bar(volume=GOLD_STORED_VOLUME))
        stored["volume"] = "not-a-number"
        result = classify_revision(
            original_content_hash=_bar(volume=GOLD_STORED_VOLUME).content_hash,
            stored_row=stored,
            bar=_bar(),
        )
        self.assertIs(result.kind, RevisionKind.OTHER)

    def test_volume_appearing_where_there_was_none_is_volume_only(self):
        stored = _bar(volume=None)
        result = self._classify(stored, _bar(volume=1758.0))
        self.assertIs(result.kind, RevisionKind.VOLUME_ONLY)


# ---------------------------------------------------------------------------
# 3. Float read-back safety -- the regression that would rot silently
# ---------------------------------------------------------------------------
class TestFloatReadBackSafety(unittest.TestCase):
    #: The measured truncation: PostgREST serialises double precision at 15
    #: significant digits, so this stored value returns as 4033.69995117188.
    FULL = 4033.699951171875
    TRUNCATED = 4033.69995117188

    def test_the_truncation_this_all_guards_against_is_real(self):
        self.assertNotEqual(float(f"{self.FULL:.15g}"), self.FULL)
        self.assertEqual(float(f"{self.FULL:.15g}"), self.TRUNCATED)
        self.assertNotEqual(
            canonical_bar_content_hash(1.0, 2.0, 0.5, self.FULL, 1.0),
            canonical_bar_content_hash(1.0, 2.0, 0.5, self.TRUNCATED, 1.0),
        )

    def test_transport_precision_is_idempotent(self):
        """Safe to apply to both sides precisely because re-applying is a no-op."""
        once = transport_precision(self.FULL)
        self.assertEqual(transport_precision(once), once)
        self.assertEqual(once, self.TRUNCATED)

    def _lossy_bar(self, volume):
        """A bar whose close needs 16 significant digits, so it cannot survive
        the PostgREST round trip intact."""
        return _bar(volume=volume, o=4000.0, h=4100.0, l=3990.0, c=self.FULL)

    def test_a_truncated_read_back_with_a_changed_volume_is_volume_only(self):
        """THE regression. A classifier comparing read-back floats directly
        would call this a price revision, on ~95% of FX rows."""
        stored = self._lossy_bar(GOLD_STORED_VOLUME)
        fresh = self._lossy_bar(GOLD_REVISED_VOLUME)
        stored_row = _stored_row(stored, truncate=True)
        # The premise: the row really did lose precision in transport.
        self.assertNotEqual(stored_row["close"], stored.close)

        result = classify_revision(
            original_content_hash=stored.content_hash,
            stored_row=stored_row,
            bar=fresh,
        )
        self.assertIs(result.kind, RevisionKind.VOLUME_ONLY)
        self.assertEqual(result.changed_fields, ("volume",))

    def test_truncation_alone_is_never_a_conflict(self):
        """_classify_duplicates reads the content_hash COLUMN, so a value that
        only lost precision in transport stays a duplicate."""
        bar = self._lossy_bar(GOLD_STORED_VOLUME)
        store = vb.SupabaseMarketObservationStore()
        with mock.patch.object(
            store, "stored_content_hash", return_value=bar.content_hash
        ):
            duplicate, conflicted = store._classify_duplicates(
                [bar.to_row()], [bar.observation_id]
            )
        self.assertEqual(duplicate, [bar.observation_id])
        self.assertEqual(conflicted, [])

    def test_classify_duplicates_never_recomputes_a_stored_hash(self):
        """Source guard. The stored content_hash column is the only integrity
        authority; recomputing from returned floats is the defect."""
        source = inspect.getsource(vb.SupabaseMarketObservationStore._classify_duplicates)
        self.assertNotIn("canonical_bar_content_hash", source)
        self.assertIn("stored_content_hash", source)

    def test_stored_content_hash_selects_only_the_hash_column(self):
        source = inspect.getsource(vb.SupabaseMarketObservationStore.stored_content_hash)
        self.assertIn('"select": "content_hash"', source)

    def test_the_revision_path_reads_the_hash_column_never_rehashes_the_row(self):
        source = inspect.getsource(vb._record_revisions)
        self.assertIn('stored.get("content_hash")', source)
        self.assertNotIn("canonical_bar_content_hash", source)


# ---------------------------------------------------------------------------
# 4. Capture integration -- downstream, append-only, fail-open
# ---------------------------------------------------------------------------
def _yahoo_payload(timestamps, closes, volumes):
    return {"chart": {"result": [{
        "meta": {"exchangeTimezoneName": "America/New_York"},
        "timestamp": list(timestamps),
        "indicators": {"quote": [{
            "open": list(closes),
            "high": [c * 1.01 for c in closes],
            "low": [c * 0.99 for c in closes],
            "close": list(closes),
            "volume": list(volumes),
        }]},
    }]}}


def _epochs(count):
    from datetime import timedelta

    base = datetime(2026, 8, 3, 4, 0, 0, tzinfo=timezone.utc)
    return [int((base + timedelta(days=i)).timestamp()) for i in range(count)]


class TestCaptureRecordsRevisions(unittest.TestCase):
    """Drives the real capture_daily_bars against fake tables and a fake vendor."""

    def _capture(self, volumes, table, log, instruments=("Gold",)):
        payload = _yahoo_payload(_epochs(len(volumes)), [3400.0] * len(volumes), volumes)
        with mock.patch.object(
            vb.requests, "get", return_value=mock.Mock(
                raise_for_status=lambda: None, json=lambda: payload
            )
        ):
            return vb.capture_daily_bars(
                instruments, store=table, revision_store=log, now=NOW
            )

    def test_a_first_capture_records_no_revision(self):
        table, log = FakeMarketTable(), FakeRevisionLog()
        result = self._capture([100.0] * 8, table, log)
        self.assertEqual(result["inserted"], 8)
        self.assertEqual(result["conflicted"], [])
        self.assertEqual(result["revisions_recorded"], 0)
        self.assertEqual(log.insert_calls, 0, "no conflicts must mean no write at all")

    def test_an_identical_recapture_records_no_revision(self):
        table, log = FakeMarketTable(), FakeRevisionLog()
        self._capture([100.0] * 8, table, log)
        result = self._capture([100.0] * 8, table, log)
        self.assertEqual(result["duplicate"], 8)
        self.assertEqual(result["conflicted"], [])
        self.assertEqual(log.insert_calls, 0)

    def test_a_revised_volume_conflicts_and_is_recorded_as_volume_only(self):
        table, log = FakeMarketTable(), FakeRevisionLog()
        self._capture([100.0] * 8, table, log)
        result = self._capture([100.0] * 7 + [250.0], table, log)

        self.assertEqual(len(result["conflicted"]), 1)
        self.assertEqual(result["duplicate"], 7)
        self.assertEqual(result["inserted"], 0)
        self.assertEqual(result["revisions_recorded"], 1)
        self.assertEqual(result["revisions_by_kind"], {"volume_only": 1})
        self.assertEqual(result["revisions_error"], "")

        stored = next(iter(log.rows.values()))
        self.assertEqual(stored["revision_kind"], "volume_only")
        self.assertEqual(stored["changed_fields"], ["volume"])
        self.assertEqual(stored["volume"], 250.0)
        self.assertEqual(stored["observation_id"], result["conflicted"][0])

    def test_the_original_observation_row_is_never_touched(self):
        table, log = FakeMarketTable(), FakeRevisionLog()
        self._capture([100.0] * 8, table, log)
        before = {oid: dict(row) for oid, row in table.rows.items()}
        self._capture([100.0] * 7 + [250.0], table, log)
        self.assertEqual(table.rows, before)

    def test_re_capturing_the_same_revision_adds_no_second_row(self):
        table, log = FakeMarketTable(), FakeRevisionLog()
        self._capture([100.0] * 8, table, log)
        first = self._capture([100.0] * 7 + [250.0], table, log)
        second = self._capture([100.0] * 7 + [250.0], table, log)

        self.assertEqual(first["revisions_recorded"], 1)
        self.assertEqual(second["revisions_recorded"], 0)
        self.assertEqual(second["revisions_duplicate"], 1)
        self.assertEqual(len(log.rows), 1)
        self.assertEqual(len(second["conflicted"]), 1, "still reported as a conflict")

    def test_a_second_different_revision_appends(self):
        table, log = FakeMarketTable(), FakeRevisionLog()
        self._capture([100.0] * 8, table, log)
        self._capture([100.0] * 7 + [250.0], table, log)
        result = self._capture([100.0] * 7 + [275.0], table, log)
        self.assertEqual(result["revisions_recorded"], 1)
        self.assertEqual(len(log.rows), 2)
        self.assertEqual(
            sorted(r["volume"] for r in log.rows.values()), [250.0, 275.0]
        )

    def test_revisions_are_read_only_downstream_of_classification(self):
        """stored_row is consulted ONLY for ids already classified conflicted."""
        table, log = FakeMarketTable(), FakeRevisionLog()
        self._capture([100.0] * 8, table, log)
        table.stored_row_calls.clear()
        result = self._capture([100.0] * 7 + [250.0], table, log)
        self.assertEqual(table.stored_row_calls, list(result["conflicted"]))


class TestRevisionFailureIsFailOpen(unittest.TestCase):
    """A revision that cannot be recorded must never cost the capture."""

    def _run(self, log):
        table = FakeMarketTable()
        payload_a = _yahoo_payload(_epochs(8), [3400.0] * 8, [100.0] * 8)
        payload_b = _yahoo_payload(_epochs(8), [3400.0] * 8, [100.0] * 7 + [250.0])
        for payload in (payload_a, payload_b):
            with mock.patch.object(
                vb.requests, "get", return_value=mock.Mock(
                    raise_for_status=lambda: None, json=lambda: payload
                )
            ):
                result = vb.capture_daily_bars(
                    ("Gold",), store=table, revision_store=log, now=NOW
                )
        return table, result

    def test_an_unreachable_revision_log_leaves_the_capture_intact(self):
        table, result = self._run(FakeRevisionLog(raise_always=True))
        self.assertEqual(len(result["conflicted"]), 1)
        self.assertEqual(result["duplicate"], 7)
        self.assertEqual(result["inserted"], 0)
        self.assertEqual(result["failed"], 0)
        self.assertTrue(result["durable"])
        self.assertEqual(result["revisions_recorded"], 0)
        self.assertEqual(result["revisions_failed"], 1)
        self.assertIn("unreachable", result["revisions_error"])
        self.assertEqual(len(table.rows), 8, "bars still stored")

    def test_a_revision_log_that_refuses_a_row_is_reported_not_raised(self):
        class Refusing:
            available = True

            def insert_rows(self, rows):
                return b2_bridge.InsertOutcome(
                    backend="supabase", durable=False,
                    failed=tuple(r["revision_id"] for r in rows),
                    error="b2_mor_kind_ck violated",
                )

        _, result = self._run(Refusing())
        self.assertEqual(len(result["conflicted"]), 1)
        self.assertEqual(result["revisions_failed"], 1)
        self.assertIn("b2_mor_kind_ck", result["revisions_error"])

    def test_a_store_that_cannot_read_a_row_back_skips_rather_than_guesses(self):
        class NoReadBack(FakeMarketTable):
            stored_row = None

        table = NoReadBack()
        log = FakeRevisionLog()
        payloads = [
            _yahoo_payload(_epochs(8), [3400.0] * 8, [100.0] * 8),
            _yahoo_payload(_epochs(8), [3400.0] * 8, [100.0] * 7 + [250.0]),
        ]
        for payload in payloads:
            with mock.patch.object(
                vb.requests, "get", return_value=mock.Mock(
                    raise_for_status=lambda: None, json=lambda: payload
                )
            ):
                result = vb.capture_daily_bars(
                    ("Gold",), store=table, revision_store=log, now=NOW
                )
        self.assertEqual(len(result["conflicted"]), 1)
        self.assertEqual(result["revisions_skipped"], 1)
        self.assertEqual(result["revisions_recorded"], 0)
        self.assertEqual(log.insert_calls, 0)

    def test_an_injected_store_never_silently_writes_to_the_real_cloud_log(self):
        """An injected market store with no injected revision store must NOT
        reach for the real Supabase revision log. That would be a hidden write
        against a backend the caller did not ask for."""
        table = FakeMarketTable()
        payloads = [
            _yahoo_payload(_epochs(8), [3400.0] * 8, [100.0] * 8),
            _yahoo_payload(_epochs(8), [3400.0] * 8, [100.0] * 7 + [250.0]),
        ]
        with mock.patch.object(vb, "resolve_revision_store") as resolver:
            for payload in payloads:
                with mock.patch.object(
                    vb.requests, "get", return_value=mock.Mock(
                        raise_for_status=lambda: None, json=lambda: payload
                    )
                ):
                    result = vb.capture_daily_bars(("Gold",), store=table, now=NOW)
        resolver.assert_not_called()
        self.assertEqual(len(result["conflicted"]), 1)
        self.assertEqual(result["revisions_skipped"], 1)
        self.assertIn("no revision store", result["revisions_error"])


# ---------------------------------------------------------------------------
# 5. Append-only posture of the revision client itself
# ---------------------------------------------------------------------------
class TestRevisionStorePosture(unittest.TestCase):
    def test_the_revision_insert_is_do_nothing_never_merge(self):
        source = inspect.getsource(
            vb.SupabaseMarketObservationRevisionStore.insert_rows
        )
        self.assertIn("resolution=ignore-duplicates", source)
        self.assertIn('"on_conflict": "revision_id"', source)
        self.assertNotIn("merge-duplicates", source)

    def test_no_mutating_verb_reaches_the_revision_log(self):
        source = inspect.getsource(vb.SupabaseMarketObservationRevisionStore)
        for forbidden in ("requests.patch", "requests.delete", "requests.put"):
            self.assertNotIn(forbidden, source, forbidden)

    def test_the_local_revision_mirror_is_append_only_and_never_durable(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "revisions.jsonl")
            store = vb.LocalMarketObservationRevisionStore(path=path)
            revision = build_revision(
                observation_id=_bar().observation_id,
                original_content_hash=_bar(volume=GOLD_STORED_VOLUME).content_hash,
                stored_row=_stored_row(_bar(volume=GOLD_STORED_VOLUME)),
                bar=_bar(),
                captured_at=NOW.isoformat(),
                resolver_version="v1",
            )
            first = store.insert_rows([revision.to_row()])
            self.assertEqual(len(first.inserted), 1)
            self.assertFalse(first.durable)

            again = store.insert_rows([revision.to_row()])
            self.assertEqual(len(again.inserted), 0)
            self.assertEqual(len(again.duplicate), 1)

            with open(path, encoding="utf-8") as handle:
                lines = [json.loads(l) for l in handle if l.strip()]
            self.assertEqual(len(lines), 1)
            self.assertEqual(store.row_count(), 1)

    def test_no_ddl_verb_appears_in_the_revision_modules(self):
        for module in (rev_mod, vb):
            source = inspect.getsource(module).upper()
            for verb in ("CREATE TABLE", "ALTER TABLE", "DROP TABLE",
                         "TRUNCATE", "CREATE INDEX"):
                self.assertNotIn(verb, source, f"{module.__name__}:{verb}")

    def test_the_migration_file_exists_and_is_not_executed_by_the_app(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(root, "sql", "001_b2_market_observation_revisions.sql")
        self.assertTrue(os.path.isfile(path), path)
        with open(path, encoding="utf-8") as handle:
            ddl = handle.read()
        for expected in ("b2_market_observation_revisions", "revision_id",
                         "b2_mor_natural_key_uq", "b2_mor_hash_differs_ck",
                         "enable row level security"):
            self.assertIn(expected, ddl, expected)

        # The bridge may NAME the migration in prose -- that is how a reader
        # finds it -- but must never open or execute it. Checked by AST so a
        # documentation reference cannot be mistaken for an execution path.
        tree = ast.parse(inspect.getsource(vb))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "open":
                for arg in node.args:
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        self.assertNotIn(".sql", arg.value)


if __name__ == "__main__":
    unittest.main()
