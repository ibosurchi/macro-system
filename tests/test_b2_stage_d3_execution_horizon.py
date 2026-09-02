"""Stage D-3: live Execution-horizon shadow activation.

The historical V2 suite remains Tactical-only so it continues to prove the
pre-D3 contract. These tests cover the new live dual-horizon orchestration.
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from collections import Counter
from datetime import timedelta

from apex import b2_bridge
from apex.b2.enums import Horizon
from apex.b2 import shadow
from apex.b2.validation.bars import GRANULARITY_1D, MarketBar
from apex.b2_bridge import QueryOutcome
from apex.b2_validation_bridge import validate_range
from test_b2_storage_v2 import NOW, _TACTICAL, FakeRow, _PatchProduction, _reset


class TestExecutionHorizonActivation(unittest.TestCase):
    def setUp(self):
        _reset()

    def _run(self, table, *, now=NOW):
        backend = shadow.InMemoryShadowStore()
        with _PatchProduction():
            with mock.patch.object(b2_bridge, "resolve_record_store", return_value=table), \
                 mock.patch.object(b2_bridge, "shadow_instruments", return_value=("Gold",)):
                out = b2_bridge.run_shadow_observation(
                    "FAKE_KEY", "chan", store=backend, now=now
                )
        return out, backend

    def test_live_v2_writes_distinct_tactical_and_execution_rows(self):
        table = FakeRow()
        out, _ = self._run(table)
        self.assertEqual(out["Gold"], "written")
        self.assertEqual(len(table.rows), 2)
        self.assertEqual({r["horizon"] for r in table.rows.values()}, {"tactical", "execution"})
        self.assertEqual(len({r["record_id"] for r in table.rows.values()}), 2)
        self.assertEqual(len({r["storage_id"] for r in table.rows.values()}), 2)

    def test_production_inputs_are_gathered_once_for_both_horizons(self):
        table = FakeRow()
        backend = shadow.InMemoryShadowStore()
        original = b2_bridge._gather_production_inputs
        with _PatchProduction():
            with mock.patch.object(b2_bridge, "resolve_record_store", return_value=table), \
                 mock.patch.object(b2_bridge, "shadow_instruments", return_value=("Gold",)), \
                 mock.patch.object(b2_bridge, "_gather_production_inputs", wraps=original) as gather:
                b2_bridge.run_shadow_observation("FAKE_KEY", "chan", store=backend, now=NOW)
        self.assertEqual(gather.call_count, 1)
        self.assertEqual(len(table.rows), 2)

    def test_in_process_duplicate_suppression_is_horizon_aware(self):
        table = FakeRow()
        self._run(table)
        bucket = int(NOW.timestamp()) // b2_bridge.OBSERVATION_BUCKET_SECONDS
        self.assertEqual(b2_bridge._HANDLED_BUCKETS["Gold"], bucket)
        self.assertEqual(b2_bridge._HANDLED_BUCKETS["Gold|execution"], bucket)
        self._run(table)
        self.assertEqual(len(table.rows), 2)

    def test_durable_dedup_is_independent_per_horizon(self):
        table = FakeRow()
        self._run(table)
        execution = next(r for r in table.rows.values() if r["horizon"] == "execution")
        # Simulate a restart and loss of only the execution row. Tactical must
        # remain a durable duplicate while Execution is independently rebuilt.
        del table.rows[execution["storage_id"]]
        b2_bridge._HANDLED_BUCKETS.clear()
        self._run(table)
        self.assertEqual(len(table.rows), 2)
        self.assertEqual({r["horizon"] for r in table.rows.values()}, {"tactical", "execution"})

    def test_predictions_register_with_the_actual_horizon(self):
        table = FakeRow()
        backend = shadow.InMemoryShadowStore()
        with _PatchProduction():
            with mock.patch.object(b2_bridge, "resolve_record_store", return_value=table), \
                 mock.patch.object(b2_bridge, "shadow_instruments", return_value=("Gold",)), \
                 mock.patch.object(b2_bridge, "register_transmission_prediction", return_value="registered") as reg:
                b2_bridge.run_shadow_observation("FAKE_KEY", "chan", store=backend, now=NOW)
        self.assertEqual({c.kwargs["horizon"] for c in reg.call_args_list}, {Horizon.TACTICAL, Horizon.EXECUTION})

    def test_execution_failure_does_not_cost_tactical_observation(self):
        table = FakeRow()
        backend = shadow.InMemoryShadowStore()
        original = b2_bridge._evaluate_gathered_observation

        def fail_execution(instrument, inputs, **kwargs):
            if kwargs["horizon"] is Horizon.EXECUTION:
                raise RuntimeError("execution-only failure")
            return original(instrument, inputs, **kwargs)

        with _PatchProduction():
            with mock.patch.object(b2_bridge, "resolve_record_store", return_value=table), \
                 mock.patch.object(b2_bridge, "shadow_instruments", return_value=("Gold",)), \
                 mock.patch.object(b2_bridge, "_evaluate_gathered_observation", side_effect=fail_execution):
                out = b2_bridge.run_shadow_observation("FAKE_KEY", "chan", store=backend, now=NOW)
        self.assertEqual(out["Gold"], "written")
        self.assertEqual(len(table.rows), 1)
        self.assertEqual(next(iter(table.rows.values()))["horizon"], "tactical")
        self.assertNotIn("Gold|execution", b2_bridge._HANDLED_BUCKETS)

    def test_structural_horizon_remains_withheld(self):
        self.assertEqual(b2_bridge.live_shadow_horizons(), (Horizon.TACTICAL, Horizon.EXECUTION))
        self.assertNotIn(Horizon.STRUCTURAL, b2_bridge.live_shadow_horizons())


class TestLiveCycleAtFullScale(unittest.TestCase):
    """The whole live cycle, at the size it actually runs at.

    Every other test here narrows to Gold so the assertions stay readable. This
    one runs the real instrument set once and pins the numbers D-3 is specified
    in: 11 instruments, 22 rows, ONE write.
    """

    def setUp(self):
        _reset()

    def _cycle(self):
        table = FakeRow()
        backend = shadow.InMemoryShadowStore()
        tactical = dict(
            _TACTICAL, symbol="XAUUSD=X", analysis_price=3330.0,
            last_price=3330.0, market_ts=int(NOW.timestamp()),
        )
        with _PatchProduction(tactical=tactical):
            with mock.patch.object(b2_bridge, "resolve_record_store", return_value=table):
                outcomes = b2_bridge.run_shadow_observation(
                    "FAKE_KEY", "chan", store=backend, now=NOW
                )
        return outcomes, table, backend

    def test_eleven_instruments_produce_twenty_two_rows_in_one_write(self):
        outcomes, table, _ = self._cycle()
        self.assertEqual(len(outcomes), 11)
        self.assertEqual(set(outcomes.values()), {"written"})
        self.assertEqual(len(table.rows), 22)
        # The batch is the point: 22 observations must not cost 22 round trips.
        self.assertEqual(table.insert_calls, 1)
        self.assertEqual(table.batch_sizes, [22])

    def test_every_instrument_horizon_pair_is_written_exactly_once(self):
        _, table, _ = self._cycle()
        pairs = [(r["instrument"], r["horizon"]) for r in table.rows.values()]
        self.assertEqual(len(set(pairs)), 22, "an instrument-horizon pair repeated")
        self.assertEqual(
            Counter(h for _, h in pairs),
            {Horizon.TACTICAL.value: 11, Horizon.EXECUTION.value: 11},
        )

    def test_identities_and_anchors_hold_across_the_whole_set(self):
        _, table, _ = self._cycle()
        rows = list(table.rows.values())
        self.assertEqual(len({r["record_id"] for r in rows}), 22)
        self.assertEqual(len({r["storage_id"] for r in rows}), 22)
        self.assertEqual(
            {r["record"]["market_anchor"]["anchor_status"] for r in rows},
            {"anchor_captured"},
        )

    def test_predictions_are_registered_per_instrument_and_horizon(self):
        _, _, backend = self._cycle()
        log = backend.load(b2_bridge.PREDICTION_LOG_STATE_ID, None)
        self.assertIsNotNone(log)
        self.assertEqual(len(log["predictions"]), 22)
        self.assertEqual(
            Counter(p["horizon"] for p in log["predictions"]),
            {Horizon.TACTICAL.value: 11, Horizon.EXECUTION.value: 11},
        )

    def test_structural_is_absent_from_everything_the_cycle_produced(self):
        _, table, backend = self._cycle()
        log = backend.load(b2_bridge.PREDICTION_LOG_STATE_ID, None)
        self.assertNotIn(
            Horizon.STRUCTURAL.value, {r["horizon"] for r in table.rows.values()}
        )
        self.assertNotIn(
            Horizon.STRUCTURAL.value, {p["horizon"] for p in log["predictions"]}
        )

    def test_cross_asset_stays_withheld_on_every_row(self):
        _, table, _ = self._cycle()
        for row in table.rows.values():
            self.assertEqual(row["record"]["cross_asset"]["status"], "withheld")


class TestExecutionRowsSurviveValidation(unittest.TestCase):
    """The rows D-3 activates must be VALIDATABLE, not merely writable.

    Activating a horizon on the write side is only half the change: the offline
    validation pipeline has to accept what that side now produces. Before this
    was covered, every live Execution observation whose forward window was
    still open came back from D-2D0 as a lineage DEFECT -- a
    programmer/composition error signal -- rather than as an honest unresolved
    observation. Defects are counted but are never cohort members and never
    denominators, so the entire Execution horizon would have been silently
    absent from research while inflating the defect metric.
    """

    def setUp(self):
        _reset()

    #: The shared storage fixture omits the four keys production's
    #: ``compute_tactical_move`` actually exports (symbol / analysis_price /
    #: last_price / market_ts), so its records carry ``anchor_missing`` and every
    #: observation built from them is unresolvable for that reason alone. Adding
    #: them here -- as an override, leaving the shared fixture untouched -- is
    #: what lets these tests exercise the path a LIVE record really takes.
    ANCHORED_TACTICAL = dict(
        _TACTICAL,
        symbol="XAUUSD=X",
        analysis_price=3330.0,
        last_price=3330.0,
        market_ts=int(NOW.timestamp()),
    )

    def _live_rows(self):
        table = FakeRow()
        backend = shadow.InMemoryShadowStore()
        with _PatchProduction(tactical=dict(self.ANCHORED_TACTICAL)):
            with mock.patch.object(b2_bridge, "resolve_record_store", return_value=table),                  mock.patch.object(b2_bridge, "shadow_instruments", return_value=("Gold",)):
                b2_bridge.run_shadow_observation(
                    "FAKE_KEY", "chan", store=backend, now=NOW
                )
        return list(table.rows.values())

    @staticmethod
    def _by_horizon(result):
        out = {}
        for item in result["evaluated"]:
            record = item.as_record()
            out[record["envelope"]["context"]["horizon"]] = record
        return out

    @staticmethod
    def _market_store(rows):
        class _Store:
            def query_bars_result(self, **kwargs):
                return QueryOutcome(backend="fake", ok=True, rows=tuple(rows), pages=1)
        return _Store()

    def _daily_bars(self, days=25):
        convention = b2_bridge.symbol_convention("Gold")
        symbol = convention.symbol if convention else "XAUUSD=X"
        start = NOW.replace(hour=0, minute=0, second=0, microsecond=0)
        rows, price = [], 3330.0
        for offset in range(days):
            bar_time = start + timedelta(days=offset)
            if bar_time.weekday() >= 5:      # a real series prints no weekend bar
                continue
            price *= 1.004
            rows.append(MarketBar(
                symbol=symbol, instrument="Gold", granularity=GRANULARITY_1D,
                bar_time=bar_time, open=price * 0.999, high=price * 1.006,
                low=price * 0.997, close=price, volume=1000.0, invert=False,
            ).to_row())
        return rows

    def _validate(self, *, as_of):
        rows = self._live_rows()
        self.assertEqual(len(rows), 2)
        return validate_range(
            rows, store=self._market_store(self._daily_bars()), as_of=as_of,
        )

    def _defects(self, result):
        return [
            item.as_record() for item in result["evaluated"]
            if getattr(item, "is_defect", False)
        ]

    def test_open_execution_window_is_not_a_lineage_defect(self):
        # One hour in: BOTH windows are open, which is the ordinary state for
        # most of any observation's life and must never look like a defect.
        result = self._validate(as_of=NOW + timedelta(hours=1))
        self.assertEqual(result["status"], "ok")
        self.assertEqual(self._defects(result), [])

    def test_matured_execution_window_is_not_a_lineage_defect(self):
        # Past the 3-day execution window but inside the 14-day tactical one,
        # so the two horizons are deliberately in DIFFERENT maturity states.
        result = self._validate(as_of=NOW + timedelta(days=4))
        self.assertEqual(result["status"], "ok")
        self.assertEqual(self._defects(result), [])

    def test_both_horizons_reach_evaluation_not_defect(self):
        result = self._validate(as_of=NOW + timedelta(days=20))
        self.assertEqual(self._defects(result), [])
        self.assertEqual(len(result["evaluated"]), 2)
        for item in result["evaluated"]:
            self.assertFalse(item.is_defect)

    def test_execution_rows_are_never_reinterpreted_as_tactical(self):
        """No verdict is invented for Execution -- it stays out of D-2C3 scope."""
        records = self._by_horizon(self._validate(as_of=NOW + timedelta(days=20)))
        axes = records[Horizon.EXECUTION.value]["envelope"]["outcome_hash_basis"]
        self.assertIn(axes["setup_invalidation"], ("unknown", "not_applicable"))

    def test_the_shared_anchor_is_identical_across_both_horizons(self):
        """One gathering pass means one anchor: the horizons cannot disagree
        about the state of the world they observed."""
        anchors = [row["record"]["market_anchor"] for row in self._live_rows()]
        self.assertEqual(len(anchors), 2)
        self.assertEqual(anchors[0], anchors[1])
        self.assertEqual(anchors[0]["anchor_status"], "anchor_captured")

    def test_each_horizon_matures_on_its_own_window(self):
        """The load-bearing property of D-3: same snapshot, separate windows.

        Four days on, Execution's 3-day window has closed and resolved while
        Tactical's 14-day window is still open. A single shared window -- or a
        horizon silently borrowing the other's -- could not produce this.
        """
        records = self._by_horizon(self._validate(as_of=NOW + timedelta(days=4)))
        self.assertEqual(set(records), {Horizon.TACTICAL.value, Horizon.EXECUTION.value})
        execution = records[Horizon.EXECUTION.value]["envelope"]["outcome_hash_basis"]
        tactical = records[Horizon.TACTICAL.value]["envelope"]["outcome_hash_basis"]
        self.assertEqual(execution["maturity_state"], "matured")
        self.assertEqual(execution["coverage_status"], "resolvable")
        self.assertEqual(tactical["maturity_state"], "not_matured")
        self.assertEqual(tactical["coverage_status"], "unresolved_window_open")

    def test_a_live_anchored_observation_reaches_a_real_verdict(self):
        """End-to-end proof the D-3 write path produces VALIDATABLE records.

        Guards the anchor contract: if the four production tactical exports the
        anchor is assembled from were ever dropped, every record would silently
        degrade to anchor_missing and nothing else in this suite would notice.
        """
        records = self._by_horizon(self._validate(as_of=NOW + timedelta(days=20)))
        for horizon, record in records.items():
            self.assertEqual(record["provenance_grade"], "ideal", horizon)
            axes = record["envelope"]["outcome_hash_basis"]
            self.assertEqual(axes["eligibility_pool"], "captured", horizon)
            self.assertEqual(axes["maturity_state"], "matured", horizon)
            self.assertIsNotNone(axes["terminal_return"], horizon)
        tactical = records[Horizon.TACTICAL.value]
        self.assertEqual(
            tactical["envelope"]["outcome_hash_basis"]["direction"], "confirmed"
        )
        self.assertEqual(tactical["readiness_tier"], "calibration_eligible")


if __name__ == "__main__":
    unittest.main()
