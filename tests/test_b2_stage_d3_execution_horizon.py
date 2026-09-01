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

from apex import b2_bridge
from apex.b2.enums import Horizon
from apex.b2 import shadow
from test_b2_storage_v2 import NOW, FakeRow, _PatchProduction, _reset


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


if __name__ == "__main__":
    unittest.main()
