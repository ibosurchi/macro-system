"""B2 Stage D-5 -- point-in-time bar admission and the four clocks.

Four clocks are involved in resolving one observation, and conflating any two
of them is a different bug with a different consequence:

    EVENT TIME       bar_time / bar_close_time   when the market printed it
    CAPTURE TIME     captured_at                 when we fetched and stored it
    EVALUATION TIME  evaluated_at / market_ts    when B2 made the claim
    RUN TIME         as_of                       what instant a run speaks for

The admission rules, and what each is FOR:

    R1  bar_time       >  evaluated_at            anti-look-ahead
    R2  bar_close_time <= evaluated_at + window   horizon not silently extended
    R3  evaluated_at   <= as_of                   no future observations read
    R4  captured_at    <= as_of                   as-of REPRODUCIBILITY

R1 and R2 are the anti-look-ahead rules and were already enforced by
``path_bars``; this module re-pins them because D-5 must not weaken them.

**R4 is not an anti-look-ahead rule, and the tests below say so explicitly.**
Forward evidence is captured AFTER the prediction by definition -- a bar that
prints inside the forward window cannot have been stored before that window
opened. ``captured_at > evaluated_at`` is therefore required, not suspect. What
R4 buys is that re-running a historical ``as_of`` cannot silently absorb bars
captured since, which would change ``input_hash`` for reasons that have nothing
to do with the market.

``test_a_bar_captured_after_the_prediction_is_admitted`` is the guard against
over-applying R4: if someone ever "tightens" it to ``captured_at <=
evaluated_at``, forward validation stops being possible at all, and that test
is what says so.
"""
from __future__ import annotations

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
from apex.b2.validation.bars import (
    GRANULARITY_1D,
    MarketBar,
    canonical_bar_time_iso,
    path_bars,
)

EVAL_AT = datetime(2026, 8, 30, 22, 4, 43, tzinfo=timezone.utc)
WINDOW = timedelta(days=14)
WINDOW_END = EVAL_AT + WINDOW

EARLY_AS_OF = datetime(2026, 9, 14, 0, 0, 0, tzinfo=timezone.utc)
LATER_AS_OF = datetime(2026, 9, 20, 0, 0, 0, tzinfo=timezone.utc)


def _bar(day, *, month=9, hour=4, close=3400.0, symbol="GC=F"):
    return MarketBar(
        symbol=symbol, instrument="Gold", granularity=GRANULARITY_1D,
        bar_time=datetime(2026, month, day, hour, tzinfo=timezone.utc),
        open=close, high=close * 1.01, low=close * 0.99, close=close,
        volume=None, invert=False,
    )


def _row(bar, captured_at):
    """A stored row as PostgREST returns it -- bar plus its capture time."""
    row = bar.to_row()
    row["captured_at"] = canonical_bar_time_iso(captured_at)
    return row


class RecordingResponse:
    def __init__(self, payload):
        self._payload = payload
        self.headers = {}

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


# ---------------------------------------------------------------------------
# 1. R1 / R2 -- the anti-look-ahead rules, unchanged by D-5
# ---------------------------------------------------------------------------
class TestAntiLookahead(unittest.TestCase):
    def test_a_bar_opening_exactly_at_evaluated_at_is_excluded(self):
        """R1 is STRICT. A bar opening at the evaluation moment contains price
        action from before the prediction was made."""
        exact = MarketBar(
            symbol="GC=F", instrument="Gold", granularity=GRANULARITY_1D,
            bar_time=EVAL_AT, open=1.0, high=1.0, low=1.0, close=1.0,
            volume=None, invert=False,
        )
        self.assertEqual(
            path_bars([exact], evaluated_at=EVAL_AT, window=WINDOW), ()
        )

    def test_a_bar_straddling_the_evaluation_moment_is_excluded(self):
        straddling = _bar(30, month=8, hour=4)      # opens 2026-08-30T04:00
        self.assertLess(straddling.bar_time, EVAL_AT)
        self.assertEqual(
            path_bars([straddling], evaluated_at=EVAL_AT, window=WINDOW), ()
        )

    def test_a_bar_closing_after_window_end_is_excluded(self):
        """R2. Without it a daily bar opening just inside the window would
        contribute a close taken up to a full session beyond the horizon."""
        late = _bar(13, hour=4)                     # opens 09-13T04, closes 09-14T04
        self.assertLess(late.bar_time, WINDOW_END)
        self.assertGreater(late.bar_close_time, WINDOW_END)
        self.assertEqual(path_bars([late], evaluated_at=EVAL_AT, window=WINDOW), ())

    def test_a_bar_wholly_inside_the_window_is_admitted(self):
        inside = _bar(5)
        self.assertEqual(
            path_bars([inside], evaluated_at=EVAL_AT, window=WINDOW), (inside,)
        )

    def test_d5_did_not_touch_the_evidence_selection_rule(self):
        """path_bars is the anti-look-ahead rule and lives in a protected
        module. Its source must still say exactly what it said."""
        source = inspect.getsource(path_bars)
        self.assertIn("start < _utc(bar.bar_time)", source)
        self.assertIn("bar.closes_within(end)", source)


# ---------------------------------------------------------------------------
# 2. R4 -- as-of capture admission, Supabase store
# ---------------------------------------------------------------------------
class TestSupabaseCaptureAdmission(unittest.TestCase):
    def _params(self, captured_at_max):
        seen = {}

        def fake_get(url, **kwargs):
            seen.update(kwargs.get("params") or {})
            return RecordingResponse([])

        store = vb.SupabaseMarketObservationStore()
        with mock.patch.object(vb.core, "_supabase_enabled", return_value=True):
            with mock.patch.object(vb.requests, "get", side_effect=fake_get):
                store.query_bars_result(
                    symbols=["GC=F"], start=EVAL_AT, end=WINDOW_END,
                    captured_at_max=captured_at_max,
                )
        return seen

    def test_captured_at_is_selected_so_a_caller_can_audit_it(self):
        params = self._params(None)
        self.assertIn("captured_at", params["select"])

    def test_no_bound_means_no_capture_filter_at_all(self):
        """The default must preserve pre-D-5 behaviour exactly."""
        params = self._params(None)
        self.assertNotIn("captured_at", {k for k in params if k != "select"})

    def test_a_bound_becomes_a_lte_filter_on_captured_at(self):
        params = self._params(LATER_AS_OF)
        self.assertEqual(
            params["captured_at"], f"lte.{canonical_bar_time_iso(LATER_AS_OF)}"
        )

    def test_the_bound_never_touches_the_bar_time_filters(self):
        """R4 is orthogonal to R1/R2. Conflating them is the bug this catches."""
        without = self._params(None)
        with_bound = self._params(LATER_AS_OF)
        self.assertEqual(without["bar_time"], with_bound["bar_time"])
        self.assertEqual(without["and"], with_bound["and"])


# ---------------------------------------------------------------------------
# 3. R4 -- as-of capture admission, end to end
# ---------------------------------------------------------------------------
class FakeBarStore:
    """A store that honours captured_at_max, like the real Supabase one."""

    def __init__(self, rows):
        self.rows = list(rows)
        self.available = True
        self.last_captured_at_max = "unset"

    def query_bars_result(self, *, symbols, start, end, granularity=GRANULARITY_1D,
                          page_size=1000, max_rows=None, captured_at_max=None):
        self.last_captured_at_max = captured_at_max
        wanted = set(symbols)
        low, high = canonical_bar_time_iso(start), canonical_bar_time_iso(end)
        bound = (
            canonical_bar_time_iso(captured_at_max)
            if captured_at_max is not None
            else None
        )
        out = []
        for row in self.rows:
            if row["symbol"] not in wanted or row["granularity"] != granularity:
                continue
            if not (low < row["bar_time"] <= high):
                continue
            if bound is not None and str(row.get("captured_at", "")) > bound:
                continue
            out.append(row)
        return b2_bridge.QueryOutcome(
            backend="fake", ok=True, rows=tuple(out), pages=1
        )

    def query_bars(self, *, symbols, start, end, granularity=GRANULARITY_1D,
                   limit=10000, captured_at_max=None):
        return list(
            self.query_bars_result(
                symbols=symbols, start=start, end=end, granularity=granularity,
                captured_at_max=captured_at_max,
            ).rows
        )


class LegacyBarStore:
    """A store predating R4: query_bars_result rejects captured_at_max."""

    available = True

    def query_bars_result(self, *, symbols, start, end, granularity=GRANULARITY_1D,
                          page_size=1000, max_rows=None):
        return b2_bridge.QueryOutcome(backend="legacy", ok=True, rows=(), pages=0)


class TestEndToEndCaptureAdmission(unittest.TestCase):
    def setUp(self):
        # A realistic dense daily series across the forward window, so the
        # observation genuinely MATURES rather than sitting in
        # MATURED_AWAITING_BARS. Every bar is captured the day after it
        # printed -- which is what forward capture actually looks like --
        # EXCEPT one, deliberately captured after EARLY_AS_OF so the two
        # as-of runs see different evidence.
        self.late_bar = _bar(5)
        self.rows = []
        for day in range(1, 14):
            bar = _bar(day)
            captured = (
                datetime(2026, 9, 18, tzinfo=timezone.utc)
                if day == 5
                else bar.bar_time + timedelta(days=1)
            )
            self.rows.append(_row(bar, captured))
        anchor = {
            "analysis_price": 3400.0, "last_price": 3400.0, "symbol": "GC=F",
            "symbol_requested": "GC=F", "symbol_fallback_used": False,
            "invert": False, "market_ts": int(EVAL_AT.timestamp()),
            "market_ts_iso": EVAL_AT.isoformat(),
            "volatility_scale": 0.0012, "atr": 12.0, "atr_ratio": 1.05,
            "volatility_regime": "normal", "price_source": "yahoo_5m_tactical",
            "granularity": "5m", "anchor_status": "anchor_captured",
        }
        self.record = {
            "storage_id": "s1", "record_id": "r1", "instrument": "Gold",
            "horizon": "tactical", "evaluated_at": EVAL_AT.isoformat(),
            "schema_version": 2, "content_hash": "h",
            "record": {
                "schema_version": 2, "record_id": "r1", "instrument": "Gold",
                "horizon": "tactical", "evaluated_at": EVAL_AT.isoformat(),
                "market_anchor": anchor,
            },
        }

    def _run(self, as_of):
        store = FakeBarStore(self.rows)
        result = vb.validate_range(
            [self.record], store=store, as_of=as_of, captured_at_max=as_of
        )
        return store, result

    def _admissible(self, as_of):
        """How many stored rows rule R4 alone admits at this as_of."""
        bound = canonical_bar_time_iso(as_of)
        return sum(1 for row in self.rows if row["captured_at"] <= bound)

    def test_a_bar_captured_after_as_of_is_excluded(self):
        store, result = self._run(EARLY_AS_OF)
        self.assertEqual(store.last_captured_at_max, EARLY_AS_OF)
        admitted = self._admissible(EARLY_AS_OF)
        self.assertLess(admitted, len(self.rows), "the fixture must exclude something")
        self.assertEqual(
            result["bar_rows"], admitted,
            "exactly the rows captured at or before as_of may be admitted",
        )

    def test_a_later_as_of_admits_that_same_bar(self):
        _, early = self._run(EARLY_AS_OF)
        _, later = self._run(LATER_AS_OF)
        self.assertEqual(early["bar_rows"], self._admissible(EARLY_AS_OF))
        self.assertEqual(later["bar_rows"], self._admissible(LATER_AS_OF))
        self.assertGreater(
            later["bar_rows"], early["bar_rows"],
            "a later as_of must admit strictly more of the same evidence",
        )

    def test_admitting_more_evidence_changes_the_input_hash(self):
        """The point of R4: a run's fingerprint must move only when the
        evidence it used actually moved."""
        _, early = self._run(EARLY_AS_OF)
        _, later = self._run(LATER_AS_OF)

        def hashes(result):
            out = []
            for item in result["evaluated"]:
                if getattr(item, "is_defect", True):
                    continue
                out.append((item.envelope.validation_id, item.envelope.input_hash))
            return out

        early_hashes, later_hashes = hashes(early), hashes(later)
        self.assertTrue(early_hashes and later_hashes)
        self.assertEqual(early_hashes[0][0], later_hashes[0][0], "same job")
        self.assertNotEqual(early_hashes[0][1], later_hashes[0][1], "different evidence")

    def test_a_bar_captured_after_the_prediction_is_admitted(self):
        """THE over-application guard.

        Every one of these bars was captured AFTER evaluated_at -- that is what
        forward evidence IS. If R4 is ever 'tightened' to
        ``captured_at <= evaluated_at``, forward validation becomes impossible
        and this test is what says so.
        """
        for row in self.rows:
            self.assertGreater(row["captured_at"], EVAL_AT.isoformat())
        _, result = self._run(LATER_AS_OF)
        self.assertEqual(result["bar_rows"], len(self.rows))
        self.assertEqual(self._admissible(LATER_AS_OF), len(self.rows))

    def test_the_four_clocks_stay_distinct(self):
        _, result = self._run(LATER_AS_OF)
        evaluated = [
            item for item in result["evaluated"] if not getattr(item, "is_defect", True)
        ]
        self.assertTrue(evaluated)
        context = evaluated[0].envelope.context
        # EVALUATION TIME is the record's own, never the run's.
        self.assertEqual(context.evaluated_at, EVAL_AT.isoformat())
        self.assertNotEqual(context.evaluated_at, LATER_AS_OF.isoformat())
        # The observation really matured -- otherwise the clock separation
        # below would be vacuous.
        self.assertEqual(context.maturity_state, "matured")
        # EVENT TIME is a bar's OPEN time, never a capture time and never the
        # run's instant.
        self.assertIsNotNone(context.terminal_bar_time)
        self.assertNotEqual(context.terminal_bar_time, context.evaluated_at)
        self.assertNotEqual(context.terminal_bar_time, LATER_AS_OF.isoformat())
        # And the terminal bar closes no later than the horizon it resolves.
        self.assertLessEqual(
            datetime.fromisoformat(context.terminal_bar_time), WINDOW_END
        )

    def test_no_bound_is_still_passed_when_none(self):
        store = FakeBarStore(self.rows)
        vb.validate_range([self.record], store=store, as_of=LATER_AS_OF)
        self.assertIsNone(store.last_captured_at_max)

    def test_a_store_that_cannot_bound_capture_fails_the_query_honestly(self):
        """An as-of run that cannot bound capture time is not an as-of run.
        Refusing beats quietly dropping the bound."""
        result = vb.validate_range(
            [self.record], store=LegacyBarStore(), as_of=LATER_AS_OF,
            captured_at_max=LATER_AS_OF,
        )
        self.assertEqual(result["status"], "query_failed")
        self.assertIn("captured_at_max", result["bar_query"].error)

    def test_the_same_store_succeeds_when_no_bound_is_asked_for(self):
        result = vb.validate_range([self.record], store=LegacyBarStore(), as_of=LATER_AS_OF)
        self.assertEqual(result["status"], "ok")


# ---------------------------------------------------------------------------
# 4. The local mirror's honest limitation
# ---------------------------------------------------------------------------
class TestLocalMirrorAdmission(unittest.TestCase):
    def _store(self, tmp, rows):
        path = os.path.join(tmp, "bars.jsonl")
        with open(path, "w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row) + "\n")
        return vb.LocalMarketObservationStore(path=path)

    def test_a_row_with_a_capture_time_honours_the_bound(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(
                tmp,
                [
                    _row(_bar(2), datetime(2026, 9, 3, tzinfo=timezone.utc)),
                    _row(_bar(5), datetime(2026, 9, 18, tzinfo=timezone.utc)),
                ],
            )
            bounded = store.query_bars_result(
                symbols=["GC=F"], start=EVAL_AT, end=WINDOW_END,
                captured_at_max=EARLY_AS_OF,
            )
            self.assertEqual(len(bounded.rows), 1)

    def test_a_row_with_no_capture_time_is_admitted_and_documented(self):
        """MarketBar.to_row never sends captured_at -- it is a database
        default -- so a locally mirrored bar has no capture time to bound.
        Admitting it is stated in the docstring rather than hidden."""
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp, [_bar(5).to_row()])
            result = store.query_bars_result(
                symbols=["GC=F"], start=EVAL_AT, end=WINDOW_END,
                captured_at_max=EARLY_AS_OF,
            )
            self.assertEqual(len(result.rows), 1)
        doc = vb.LocalMarketObservationStore.query_bars_result.__doc__ or ""
        self.assertIn("cannot enforce as-of capture admission", doc)

    def test_the_local_mirror_is_never_durable(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = vb.LocalMarketObservationStore(
                path=os.path.join(tmp, "bars.jsonl")
            )
            outcome = store.insert_rows([_bar(5).to_row()])
            self.assertFalse(outcome.durable)


# ---------------------------------------------------------------------------
# 5. validate_stored_range wires as_of through as the capture bound
# ---------------------------------------------------------------------------
class TestStoredRangeWiring(unittest.TestCase):
    def test_as_of_is_passed_as_captured_at_max(self):
        shadow = mock.Mock()
        shadow.query_records_result.return_value = b2_bridge.QueryOutcome(
            backend="fake", ok=True, rows=(), pages=0
        )
        with mock.patch.object(
            vb, "validate_range", return_value={"status": "ok", "evaluated": ()}
        ) as validate:
            vb.validate_stored_range(
                start=EVAL_AT, as_of=LATER_AS_OF, record_store=shadow,
                market_store=FakeBarStore([]),
            )
        self.assertEqual(validate.call_args.kwargs["captured_at_max"], LATER_AS_OF)
        self.assertEqual(validate.call_args.kwargs["as_of"], LATER_AS_OF)

    def test_persist_defaults_to_false(self):
        shadow = mock.Mock()
        shadow.query_records_result.return_value = b2_bridge.QueryOutcome(
            backend="fake", ok=True, rows=(), pages=0
        )
        result = vb.validate_stored_range(
            start=EVAL_AT, as_of=LATER_AS_OF, record_store=shadow,
            market_store=FakeBarStore([]),
        )
        self.assertFalse(result["persistence"]["persist_attempted"])

    def test_an_injected_market_store_never_reaches_the_real_outcome_log(self):
        shadow = mock.Mock()
        shadow.query_records_result.return_value = b2_bridge.QueryOutcome(
            backend="fake", ok=True, rows=(), pages=0
        )
        with mock.patch.object(vb, "resolve_outcome_store") as resolver:
            vb.validate_stored_range(
                start=EVAL_AT, as_of=LATER_AS_OF, record_store=shadow,
                market_store=FakeBarStore([]), persist=True,
            )
        resolver.assert_not_called()


if __name__ == "__main__":
    unittest.main()
