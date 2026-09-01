"""Stage D-2E bridge/I-O safety tests.

These tests cover the two storage defects D-2E closes before live cohort work:
pagination beyond one PostgREST page and explicit failure-vs-empty semantics.
"""
from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import Mock, patch

from apex.b2_bridge import QueryOutcome, SupabaseShadowRecordStore
from apex.b2_validation_bridge import SupabaseMarketObservationStore


class _Response:
    def __init__(self, body):
        self._body = body
    def raise_for_status(self):
        return None
    def json(self):
        return self._body


class D2EQueryOutcomeTests(unittest.TestCase):
    def test_empty_success_is_not_failure(self):
        result = QueryOutcome(backend="test", ok=True, rows=())
        self.assertTrue(result.ok)
        self.assertEqual(result.rows, ())
        self.assertEqual(result.error, "")

    def test_failure_is_not_empty_success(self):
        result = QueryOutcome(backend="test", ok=False, error="boom")
        self.assertFalse(result.ok)
        self.assertEqual(result.rows, ())
        self.assertEqual(result.error, "boom")


class D2EShadowPaginationTests(unittest.TestCase):
    @patch("apex.b2_bridge.core._supabase_enabled", return_value=True)
    @patch("apex.b2_bridge.core._supabase_headers", return_value={})
    @patch("apex.b2_bridge.requests.get")
    def test_shadow_read_paginates_past_1000(self, get, _headers, _enabled):
        first = [{"storage_id": f"a{i}", "evaluated_at": "2026-01-01T00:00:00+00:00"} for i in range(1000)]
        second = [{"storage_id": f"b{i}", "evaluated_at": "2026-01-02T00:00:00+00:00"} for i in range(7)]
        get.side_effect = [_Response(first), _Response(second)]
        result = SupabaseShadowRecordStore().query_records_result(page_size=1000, max_rows=None)
        self.assertTrue(result.ok)
        self.assertEqual(len(result.rows), 1007)
        self.assertEqual(result.pages, 2)
        self.assertEqual(get.call_args_list[0].kwargs["params"]["offset"], 0)
        self.assertEqual(get.call_args_list[1].kwargs["params"]["offset"], 1000)

    @patch("apex.b2_bridge.core._supabase_enabled", return_value=True)
    @patch("apex.b2_bridge.core._supabase_headers", return_value={})
    @patch("apex.b2_bridge.requests.get", side_effect=RuntimeError("network down"))
    def test_shadow_read_reports_failure(self, _get, _headers, _enabled):
        result = SupabaseShadowRecordStore().query_records_result()
        self.assertFalse(result.ok)
        self.assertIn("network down", result.error)


class D2EMarketPaginationTests(unittest.TestCase):
    @patch("apex.b2_validation_bridge.core._supabase_enabled", return_value=True)
    @patch("apex.b2_validation_bridge.core._supabase_headers", return_value={})
    @patch("apex.b2_validation_bridge.requests.get")
    def test_market_read_paginates(self, get, _headers, _enabled):
        first = [{"observation_id": f"a{i}"} for i in range(3)]
        second = [{"observation_id": "b0"}]
        get.side_effect = [_Response(first), _Response(second)]
        result = SupabaseMarketObservationStore().query_bars_result(
            symbols=["EURUSD=X"],
            start=datetime(2026, 1, 1, tzinfo=timezone.utc),
            end=datetime(2026, 2, 1, tzinfo=timezone.utc),
            page_size=3,
        )
        self.assertTrue(result.ok)
        self.assertEqual(len(result.rows), 4)
        self.assertEqual(result.pages, 2)
        self.assertEqual(get.call_args_list[1].kwargs["params"]["offset"], 3)

    @patch("apex.b2_validation_bridge.core._supabase_enabled", return_value=True)
    @patch("apex.b2_validation_bridge.core._supabase_headers", return_value={})
    @patch("apex.b2_validation_bridge.requests.get", side_effect=RuntimeError("timeout"))
    def test_market_read_reports_failure(self, _get, _headers, _enabled):
        result = SupabaseMarketObservationStore().query_bars_result(
            symbols=["EURUSD=X"],
            start=datetime(2026, 1, 1, tzinfo=timezone.utc),
            end=datetime(2026, 2, 1, tzinfo=timezone.utc),
        )
        self.assertFalse(result.ok)
        self.assertIn("timeout", result.error)


if __name__ == "__main__":
    unittest.main()
