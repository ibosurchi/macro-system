"""B2 Stage D-4 -- Gold series pinning.

The hazard this closes: ``symbol`` is part of ``canonical_observation_id``.
Gold's production convention is ``XAUUSD=X`` with ``GC=F`` as a declared
fallback, ``XAUUSD=X`` returned HTTP 404 throughout Stage D-1 activation, and so
every stored Gold bar is ``GC=F``. If ``XAUUSD=X`` ever starts answering again,
an unpinned capture would begin appending a SECOND Gold series -- spot rather
than futures, a different scale -- under the same instrument. No identity would
collide. No conflict would be raised. Nothing would fail. The two series would
simply coexist and be read together.

``test_xauusd_is_never_requested_even_when_it_would_succeed`` is the load-bearing
test here: it is the only one that fails if the pin is bypassed while the vendor
is healthy, which is precisely the situation the pin exists for.

These tests also pin what must NOT change: production's own symbol config, the
anchor classification legacy Gold records depend on, and the identity semantics
that make a fallback symbol a distinct row.
"""
from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from state_isolation import isolate_durable_state

isolate_durable_state()

from apex import production_core as core
from apex import b2_bridge
from apex import b2_validation_bridge as vb
from apex.b2.validation import series_pins as pins_mod
from apex.b2.validation.anchor import SymbolConvention, classify_anchor
from apex.b2.validation.series_pins import (
    PINNED_CAPTURE_SYMBOLS,
    SERIES_PIN_VERSION,
    pinned_capture_symbol,
    pinned_instruments,
)

SCRIPTS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"
)
sys.path.insert(0, SCRIPTS_DIR)
import capture_daily_bars as runner

NOW = datetime(2026, 9, 15, 12, 0, 0, tzinfo=timezone.utc)


def _epochs(count, start_day=1, month=8):
    base = datetime(2026, month, start_day, tzinfo=timezone.utc)
    return [int((base + timedelta(days=i)).timestamp()) for i in range(count)]


def _yahoo_payload(timestamps, closes):
    return {"chart": {"result": [{
        "meta": {"exchangeTimezoneName": "UTC"},
        "timestamp": list(timestamps),
        "indicators": {"quote": [{
            "open": list(closes),
            "high": [c * 1.01 for c in closes],
            "low": [c * 0.99 for c in closes],
            "close": list(closes),
            "volume": [None] * len(timestamps),
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

    def stored_content_hash(self, observation_id):
        row = self.rows.get(observation_id)
        return row.get("content_hash") if row else None

    def stored_row(self, observation_id):
        row = self.rows.get(observation_id)
        return dict(row) if row else None


class FakeRevisionLog:
    def __init__(self):
        self.rows: dict[str, dict] = {}

    def insert_rows(self, rows):
        for row in rows:
            self.rows.setdefault(row["revision_id"], dict(row))
        return b2_bridge.InsertOutcome(
            backend="local", durable=False,
            inserted=tuple(r["revision_id"] for r in rows),
        )


# ---------------------------------------------------------------------------
# 1. The pin table itself -- a versioned research decision
# ---------------------------------------------------------------------------
class TestPinTable(unittest.TestCase):
    def test_gold_is_pinned_to_gc_f(self):
        pin = pinned_capture_symbol("Gold")
        self.assertIsNotNone(pin)
        self.assertEqual(pin.symbol, "GC=F")
        self.assertFalse(pin.invert)

    def test_gold_is_the_only_pinned_instrument(self):
        self.assertEqual(pinned_instruments(), ("Gold",))

    def test_an_unpinned_instrument_returns_none_rather_than_raising(self):
        for instrument in ("Oil", "NDX", "EUR", "NOT_A_REAL_ASSET", "", None):
            self.assertIsNone(pinned_capture_symbol(instrument))

    def test_the_pin_table_is_a_versioned_decision(self):
        """GOLDEN TEST. Changing a pin or the version without deliberately
        updating this test fails CI -- which is the entire enforcement
        mechanism behind 'a pin change is an explicit research decision'."""
        self.assertEqual(SERIES_PIN_VERSION, "b2-series-pin-v1")
        self.assertEqual(
            {name: (pin.symbol, pin.invert) for name, pin in PINNED_CAPTURE_SYMBOLS.items()},
            {"Gold": ("GC=F", False)},
        )
        self.assertEqual(PINNED_CAPTURE_SYMBOLS["Gold"].pinned_on, "2026-09-02")
        self.assertIn("XAUUSD=X", PINNED_CAPTURE_SYMBOLS["Gold"].reason)

    def test_a_pin_only_ever_narrows_production_never_invents(self):
        """A pin must name a symbol production already declares -- primary or
        fallback -- with production's own inversion. It may forbid a switch; it
        may not introduce a series production never sanctioned."""
        for instrument, pin in PINNED_CAPTURE_SYMBOLS.items():
            convention = b2_bridge.symbol_convention(instrument)
            self.assertIsNotNone(convention, instrument)
            approved = {convention.symbol, *convention.fallback_symbols}
            self.assertIn(pin.symbol, approved, instrument)
            self.assertEqual(pin.invert, convention.invert, instrument)

    def test_the_pin_module_is_pure(self):
        import ast
        import inspect

        tree = ast.parse(inspect.getsource(pins_mod))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = [a.name for a in node.names] + [getattr(node, "module", "") or ""]
                for name in names:
                    for forbidden in ("requests", "streamlit", "threading",
                                      "production_core", "socket", "urllib"):
                        self.assertNotIn(forbidden, name, name)


# ---------------------------------------------------------------------------
# 2. Capture obeys the pin
# ---------------------------------------------------------------------------
class TestCaptureIsPinned(unittest.TestCase):
    def test_xauusd_is_never_requested_even_when_it_would_succeed(self):
        """THE test. A healthy XAUUSD=X is exactly the situation the pin exists
        for: without it, capture would silently start a second Gold series."""
        requested: list[str] = []
        payload = _yahoo_payload(_epochs(20), [3400.0] * 20)

        def fake_get(url, **kwargs):
            requested.append(url)
            return _FakeResponse(payload)

        with mock.patch.object(vb.requests, "get", side_effect=fake_get):
            result = vb.capture_daily_bars(
                ("Gold",), store=FakeMarketTable(),
                revision_store=FakeRevisionLog(), now=NOW,
            )

        self.assertTrue(requested, "no request was made at all")
        for url in requested:
            self.assertNotIn("XAUUSD", url)
        self.assertTrue(any("GC" in url for url in requested))
        self.assertEqual(result["symbols"]["Gold"], "GC=F")
        self.assertEqual(result["pinned"], {"Gold": "GC=F"})

    def test_gold_reports_no_bars_rather_than_switching_series(self):
        """If GC=F is unavailable, Gold is unavailable. Availability may cost a
        capture; it may never change which series the history is made of."""
        payload = _yahoo_payload(_epochs(20), [3400.0] * 20)

        def fake_get(url, **kwargs):
            if "GC" in url:
                raise RuntimeError("GC=F unavailable")
            return _FakeResponse(payload)      # XAUUSD=X would succeed

        with mock.patch.object(vb.requests, "get", side_effect=fake_get):
            result = vb.capture_daily_bars(
                ("Gold",), store=FakeMarketTable(),
                revision_store=FakeRevisionLog(), now=NOW,
            )

        self.assertEqual(result["instruments"]["Gold"], "no_bars")
        self.assertNotIn("Gold", result["symbols"])
        self.assertEqual(result["inserted"], 0)

    def test_an_unpinned_instrument_still_uses_its_primary_symbol(self):
        payload = _yahoo_payload(_epochs(20), [70.0] * 20)
        requested: list[str] = []

        def fake_get(url, **kwargs):
            requested.append(url)
            return _FakeResponse(payload)

        with mock.patch.object(vb.requests, "get", side_effect=fake_get):
            result = vb.capture_daily_bars(
                ("Oil",), store=FakeMarketTable(),
                revision_store=FakeRevisionLog(), now=NOW,
            )
        self.assertEqual(result["symbols"]["Oil"], "CL=F")
        self.assertEqual(result["pinned"], {})

    def test_the_generic_fallback_path_still_works_for_an_unpinned_instrument(self):
        """Gold is the only instrument production declares a fallback for, so
        pinning it would otherwise leave the fallback loop untested and free to
        rot. Exercised here with an injected convention instead."""
        thin = _yahoo_payload(_epochs(3), [1.0] * 3)          # below MIN_DAILY_BARS
        full = _yahoo_payload(_epochs(20), [1.0] * 20)
        requested: list[str] = []

        def fake_get(url, **kwargs):
            requested.append(url)
            return _FakeResponse(thin if "PRIMARY" in url else full)

        convention = SymbolConvention(
            instrument="Oil", symbol="PRIMARY=X", invert=False,
            fallback_symbols=("BACKUP=X",),
        )
        with mock.patch.object(vb, "symbol_convention", return_value=convention):
            with mock.patch.object(vb.requests, "get", side_effect=fake_get):
                result = vb.capture_daily_bars(
                    ("Oil",), store=FakeMarketTable(),
                    revision_store=FakeRevisionLog(), now=NOW,
                )

        self.assertEqual(result["symbols"]["Oil"], "BACKUP=X")
        self.assertTrue(any("PRIMARY" in url for url in requested), "primary was skipped")
        self.assertEqual(result["pinned"], {})

    def test_the_pin_version_is_reported_on_every_capture(self):
        payload = _yahoo_payload(_epochs(20), [3400.0] * 20)
        with mock.patch.object(vb.requests, "get", return_value=_FakeResponse(payload)):
            result = vb.capture_daily_bars(
                ("Gold",), store=FakeMarketTable(),
                revision_store=FakeRevisionLog(), now=NOW,
            )
        self.assertEqual(result["series_pin_version"], SERIES_PIN_VERSION)

    def test_a_capture_that_fetched_nothing_still_reports_the_pin_version(self):
        result = vb.capture_daily_bars(
            ("NOT_A_REAL_ASSET",), store=FakeMarketTable(),
            revision_store=FakeRevisionLog(), now=NOW,
        )
        self.assertEqual(result["series_pin_version"], SERIES_PIN_VERSION)
        self.assertEqual(result["pinned"], {})


# ---------------------------------------------------------------------------
# 3. What the pin must NOT touch
# ---------------------------------------------------------------------------
class TestProductionAndHistoryAreUnaffected(unittest.TestCase):
    def test_production_symbol_config_for_gold_is_unchanged(self):
        config = core._tactical_symbol_config("Gold")
        self.assertEqual(config["symbol"], "XAUUSD=X")
        self.assertEqual(list(config["fallback_symbols"]), ["GC=F"])
        self.assertFalse(config["invert"])

    def test_the_symbol_convention_still_reports_the_production_primary(self):
        convention = b2_bridge.symbol_convention("Gold")
        self.assertEqual(convention.symbol, "XAUUSD=X")
        self.assertEqual(convention.fallback_symbols, ("GC=F",))

    def test_legacy_gold_records_are_not_reinterpreted(self):
        """A legacy Gold record still resolves against production's primary and
        still carries its fallback-uncertainty caveat. The pin governs capture,
        not the meaning of an observation already stored."""
        record = {"instrument": "Gold", "horizon": "1d", "evaluated_at": NOW.isoformat()}
        resolution = classify_anchor(record, b2_bridge.symbol_convention("Gold"))
        self.assertEqual(resolution.symbol, "XAUUSD=X")
        self.assertIn("symbol_uncertain_fallback_configured", resolution.caveats)

    def test_identity_semantics_are_untouched_by_pinning(self):
        """A fallback symbol is still a DISTINCT row for the same instrument --
        the property the pin protects, not one it changes."""
        from apex.b2.validation.bars import canonical_observation_id

        primary = canonical_observation_id("XAUUSD=X", "1d", "t", "yahoo_chart_v8")
        fallback = canonical_observation_id("GC=F", "1d", "t", "yahoo_chart_v8")
        self.assertNotEqual(primary, fallback)


# ---------------------------------------------------------------------------
# 4. The operator report
# ---------------------------------------------------------------------------
class TestRunnerReportsThePin(unittest.TestCase):
    def _report(self):
        payload = _yahoo_payload(_epochs(20), [3400.0] * 20)
        with mock.patch.object(vb.requests, "get", return_value=_FakeResponse(payload)):
            return runner.run_capture(
                ["Gold"], store=FakeMarketTable(),
                revision_store=FakeRevisionLog(), now=NOW,
            )

    def test_a_pinned_symbol_is_reported_as_pinned_not_as_a_fallback(self):
        report = self._report()
        self.assertEqual(report["symbols_used"]["Gold"], "GC=F")
        self.assertEqual(report["pinned_series"], {"Gold": "GC=F"})
        self.assertFalse(
            report["fallback_used"]["Gold"],
            "a pinned instrument has no fallback to have used",
        )
        self.assertEqual(report["series_pin_version"], SERIES_PIN_VERSION)

    def test_the_formatted_report_says_pinned_series(self):
        text = runner.format_report(self._report())
        self.assertIn("pinned series", text)
        self.assertNotIn("fallback symbol", text)

    def test_the_runner_reads_the_pin_from_the_result_never_recomputes_it(self):
        """The runner must stay a formatter. If it decided pinning itself it
        could disagree with what capture actually did."""
        import ast

        with open(runner.__file__, encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                imported.update(a.name for a in node.names)
                imported.add((node.module or ""))
            if isinstance(node, ast.Import):
                imported.update(a.name for a in node.names)
        for forbidden in ("series_pins", "pinned_capture_symbol",
                          "PINNED_CAPTURE_SYMBOLS"):
            self.assertNotIn(forbidden, imported, forbidden)


if __name__ == "__main__":
    unittest.main()
