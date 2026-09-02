"""Tests for scripts/capture_daily_bars.py -- the named Stage D-1 capture runner.

This module imports ``apex.production_core`` (transitively, through the
validation bridge), so it installs the durable-state isolation first, exactly
like the other B2 test modules. Every network call is faked; nothing here
reaches Yahoo or a real Supabase table.
"""
from __future__ import annotations

import ast
import io
import json
import os
import sys
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from state_isolation import isolate_durable_state

isolate_durable_state()

from apex import b2_bridge
from apex import b2_validation_bridge as vb

SCRIPTS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"
)
sys.path.insert(0, SCRIPTS_DIR)
RUNNER_PATH = os.path.join(SCRIPTS_DIR, "capture_daily_bars.py")

import capture_daily_bars as runner

NOW = datetime(2026, 9, 15, 12, 0, 0, tzinfo=timezone.utc)


def _epochs(start_day, count, month=8):
    from datetime import timedelta

    base = datetime(2026, month, start_day, tzinfo=timezone.utc)
    return [int((base + timedelta(days=i)).timestamp()) for i in range(count)]


def _yahoo_payload(timestamps, closes, *, tz="UTC"):
    n = len(timestamps)
    return {"chart": {"result": [{
        "meta": {"exchangeTimezoneName": tz},
        "timestamp": list(timestamps),
        "indicators": {"quote": [{
            "open": list(closes),
            "high": [c * 1.01 for c in closes],
            "low": [c * 0.99 for c in closes],
            "close": list(closes),
            "volume": [None] * n,
        }]},
    }]}}


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class FakeMarketTable:
    """Minimal append-only stand-in, enough to exercise the runner's reporting."""

    def __init__(self):
        self.rows: dict[str, dict] = {}
        self.available = True

    def insert_rows(self, rows):
        inserted, duplicate, conflicted = [], [], []
        for row in rows:
            oid = row["observation_id"]
            if oid in self.rows:
                if self.rows[oid]["content_hash"] != row["content_hash"]:
                    conflicted.append(oid)
                else:
                    duplicate.append(oid)
                continue
            self.rows[oid] = dict(row)
            inserted.append(oid)
        return b2_bridge.InsertOutcome(
            backend="local", durable=False, inserted=tuple(inserted),
            duplicate=tuple(duplicate), conflicted=tuple(conflicted),
        )


# ---------------------------------------------------------------------------
# run_capture -- the reporting contract
# ---------------------------------------------------------------------------
class TestRunCaptureReport(unittest.TestCase):
    def test_reports_all_fields_required_by_the_capture_activation(self):
        """Every field an operator needs is present and honest on a clean run."""
        payload = _yahoo_payload(_epochs(1, 20), [3400.0] * 20)
        with mock.patch.object(vb.requests, "get", return_value=_FakeResponse(payload)):
            report = runner.run_capture(["Gold"], store=FakeMarketTable(), now=NOW)

        self.assertEqual(report["captured_at"], NOW.isoformat())
        self.assertEqual(report["requested_instruments"], ["Gold"])
        self.assertEqual(report["successful_instruments"], ["Gold"])
        self.assertEqual(report["failed_instruments"], [])
        self.assertGreater(report["closed_bars_fetched"], 0)
        self.assertGreater(report["inserted"], 0)
        self.assertEqual(report["duplicate"], 0)
        self.assertEqual(report["conflicted"], [])
        self.assertEqual(report["failed_rows"], 0)
        self.assertIn("Gold", report["symbols_used"])
        self.assertIn("Gold", report["fallback_used"])
        self.assertFalse(report["fallback_used"]["Gold"])
        self.assertIn("backend", report)
        self.assertIn("durable", report)

    def test_does_not_fabricate_success_for_an_unknown_instrument(self):
        report = runner.run_capture(
            ["NOT_A_REAL_ASSET"], store=FakeMarketTable(), now=NOW
        )
        self.assertEqual(report["successful_instruments"], [])
        self.assertEqual(report["failed_instruments"], ["NOT_A_REAL_ASSET"])
        self.assertEqual(report["instrument_status"]["NOT_A_REAL_ASSET"], "unknown_instrument")
        self.assertEqual(report["inserted"], 0)
        self.assertEqual(report["closed_bars_fetched"], 0)
        self.assertNotIn("NOT_A_REAL_ASSET", report["symbols_used"])

    def test_one_failing_instrument_does_not_block_the_others(self):
        good_payload = _yahoo_payload(_epochs(1, 20), [3400.0] * 20)

        def fake_get(url, **kwargs):
            if "CL" in url:
                raise RuntimeError("simulated network failure")
            return _FakeResponse(good_payload)

        with mock.patch.object(vb.requests, "get", side_effect=fake_get):
            report = runner.run_capture(
                ["Gold", "Oil"], store=FakeMarketTable(), now=NOW
            )

        self.assertIn("Oil", report["failed_instruments"])
        self.assertIn("Gold", report["successful_instruments"])
        self.assertGreater(report["inserted"], 0)

    def test_fallback_symbol_use_is_reported(self):
        """When the primary symbol has no bars and a fallback does, say so."""
        primary_payload = _yahoo_payload(_epochs(1, 3), [3400.0] * 3)
        fallback_payload = _yahoo_payload(_epochs(1, 20), [3400.0] * 20)

        def fake_get(url, **kwargs):
            if "XAUUSD" in url:
                return _FakeResponse(primary_payload)
            if "GC%3DF" in url or "GC=F" in url:
                return _FakeResponse(fallback_payload)
            raise AssertionError(f"unexpected symbol request: {url}")

        with mock.patch.object(vb.requests, "get", side_effect=fake_get):
            report = runner.run_capture(["Gold"], store=FakeMarketTable(), now=NOW)

        self.assertEqual(report["symbols_used"]["Gold"], "GC=F")
        self.assertTrue(report["fallback_used"]["Gold"])

    def test_defaults_to_every_registered_instrument(self):
        with mock.patch.object(runner, "capture_daily_bars") as fake_capture:
            fake_capture.return_value = {
                "captured_at": NOW.isoformat(), "instruments": {}, "symbols": {},
                "backend": "none", "durable": False, "inserted": 0, "duplicate": 0,
                "conflicted": [], "failed": 0,
            }
            runner.run_capture(None, store=FakeMarketTable(), now=NOW)
        requested = fake_capture.call_args[0][0]
        self.assertEqual(tuple(requested), runner.registered_instruments())

    def test_never_stores_a_bar_capture_daily_bars_did_not_produce(self):
        """The runner has no independent fetch/store path to fabricate data with.

        It is structurally incapable of writing a synthetic bar: it never
        imports the network client or the bar model, so every row that ever
        reaches a store is one ``capture_daily_bars`` itself produced.
        """
        with open(RUNNER_PATH, "r", encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                imported.update(a.name for a in node.names)
            if isinstance(node, ast.Import):
                imported.update(a.name for a in node.names)
        for forbidden in ("requests", "MarketBar", "SupabaseMarketObservationStore",
                          "LocalMarketObservationStore"):
            self.assertNotIn(forbidden, imported, forbidden)


# ---------------------------------------------------------------------------
# format_report / CLI
# ---------------------------------------------------------------------------
class TestFormatReportAndCli(unittest.TestCase):
    def _sample_report(self):
        return {
            "captured_at": NOW.isoformat(),
            "requested_instruments": ["Gold", "Oil"],
            "successful_instruments": ["Gold"],
            "failed_instruments": ["Oil"],
            "instrument_status": {"Gold": "fetched", "Oil": "no_bars"},
            "closed_bars_fetched": 20,
            "inserted": 20,
            "duplicate": 0,
            "conflicted": [],
            "failed_rows": 0,
            "symbols_used": {"Gold": "XAUUSD=X"},
            "fallback_used": {"Gold": False},
            "backend": "local",
            "durable": False,
            "error": "",
        }

    def test_format_report_surfaces_the_required_facts(self):
        text = runner.format_report(self._sample_report())
        for expected in (
            NOW.isoformat(), "Gold", "Oil", "fetched", "no_bars",
            "XAUUSD=X", "Inserted: 20",
        ):
            self.assertIn(expected, text)

    def test_main_json_mode_emits_a_valid_report_and_exits_zero(self):
        with mock.patch.object(runner, "run_capture", return_value=self._sample_report()):
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                exit_code = runner.main(["--json"])
        self.assertEqual(exit_code, 0)
        parsed = json.loads(buffer.getvalue())
        self.assertEqual(parsed["successful_instruments"], ["Gold"])

    def test_main_passes_repeated_instrument_flags_through(self):
        with mock.patch.object(
            runner, "run_capture", return_value=self._sample_report()
        ) as fake_run:
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                runner.main(["--instrument", "Gold", "--instrument", "Oil"])
        fake_run.assert_called_once_with(["Gold", "Oil"])


if __name__ == "__main__":
    unittest.main()
