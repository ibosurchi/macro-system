"""Tests for scripts/validate_matured_observations.py -- the D-5 runner.

Two properties matter more than the reporting detail:

*   **Dry run is the default.** A run that was not asked to persist must issue
    no write of any kind, and must still report exactly what it would have
    written. Persistence is opt-in through ``--persist`` and nothing else.
*   **The runner is a formatter, not a second implementation.** It must not
    contain fetch, maturity, resolution, cohort, gate or storage logic. If it
    ever grows any, it can drift from what the bridge actually did -- and a
    report that disagrees with the run it describes is worse than no report.

``test_zero_eligible_outcomes_is_a_successful_run`` pins the thing most likely
to be "fixed" into a bug later: for the next eleven days every real run will
find nothing matured, and that is the system working, not failing.
"""
from __future__ import annotations

import ast
import io
import json
import os
import sys
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
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
RUNNER_PATH = os.path.join(SCRIPTS_DIR, "validate_matured_observations.py")

import validate_matured_observations as runner

START = datetime(2026, 8, 30, tzinfo=timezone.utc)
AS_OF = datetime(2026, 9, 20, 12, 0, 0, tzinfo=timezone.utc)
EVAL_AT = datetime(2026, 8, 30, 22, 4, 43, tzinfo=timezone.utc)


class _Value:
    def __init__(self, value):
        self.value = value


class _Ctx:
    def __init__(self, maturity_state="matured", horizon="tactical"):
        self.maturity_state = maturity_state
        self.horizon = horizon


class _Envelope:
    def __init__(self, maturity_state="matured", horizon="tactical"):
        self.context = _Ctx(maturity_state, horizon)


class _Evaluated:
    is_defect = False

    def __init__(self, maturity_state="matured", horizon="tactical"):
        self.envelope = _Envelope(maturity_state, horizon)


class FakeShadowStore:
    def __init__(self, rows=(), ok=True):
        self.rows = tuple(rows)
        self.ok = ok

    def query_records_result(self, *, instrument, start, end, max_rows=None):
        return b2_bridge.QueryOutcome(
            backend="fake", ok=self.ok, rows=self.rows, pages=1 if self.ok else 0
        )


class FakeBarStore:
    available = True

    def __init__(self):
        self.captured_at_max_seen = "unset"

    def query_bars_result(self, *, symbols, start, end, granularity="1d",
                          page_size=1000, max_rows=None, captured_at_max=None):
        self.captured_at_max_seen = captured_at_max
        return b2_bridge.QueryOutcome(backend="fake", ok=True, rows=(), pages=0)


class FakeOutcomeLog:
    def __init__(self):
        self.rows = []
        self.insert_calls = 0

    def insert_rows(self, rows):
        self.insert_calls += 1
        self.rows.extend(rows)
        return b2_bridge.InsertOutcome(
            backend="local", durable=False,
            inserted=tuple(r["outcome_row_id"] for r in rows),
        )


def _shadow_row(storage_id="s1", instrument="Gold", horizon="tactical"):
    record = {
        "schema_version": 2, "record_id": f"r-{storage_id}",
        "instrument": instrument, "horizon": horizon,
        "evaluated_at": EVAL_AT.isoformat(),
    }
    return {
        "storage_id": storage_id, "record_id": f"r-{storage_id}",
        "instrument": instrument, "horizon": horizon,
        "evaluated_at": EVAL_AT.isoformat(), "schema_version": 2,
        "content_hash": "h", "record": record,
    }


# ---------------------------------------------------------------------------
# 1. The reporting contract
# ---------------------------------------------------------------------------
class TestRunValidationReport(unittest.TestCase):
    def _report(self, **kw):
        return runner.run_validation(
            start=START, as_of=AS_OF,
            record_store=FakeShadowStore([_shadow_row()]),
            market_store=FakeBarStore(),
            outcome_store=FakeOutcomeLog(),
            **kw,
        )

    def test_every_field_an_operator_needs_is_present(self):
        report = self._report()
        for key in (
            "status", "as_of", "range_start", "range_end", "instrument",
            "persist_requested", "shadow_rows", "observations_considered",
            "bar_rows", "malformed_rows", "symbols", "maturity_states",
            "gate_census", "not_matured", "awaiting_bars", "withheld_execution",
            "withheld_horizon", "lineage_defects", "eligible_outcomes",
            "final_outcomes", "provisional_outcomes", "rows_written",
            "rows_already_known", "rows_conflicted", "rows_failed",
            "outcome_backend", "persistence_error",
        ):
            self.assertIn(key, report, key)

    def test_the_gate_census_reconciles_against_what_was_considered(self):
        report = self._report()
        self.assertEqual(
            sum(report["gate_census"].values()), report["observations_considered"]
        )

    def test_dry_run_is_the_default(self):
        report = self._report()
        self.assertFalse(report["persist_requested"])

    def test_as_of_is_reported_verbatim_and_never_a_wall_clock(self):
        report = self._report()
        self.assertEqual(report["as_of"], AS_OF.isoformat())

    def test_range_end_defaults_to_as_of(self):
        report = self._report()
        self.assertEqual(report["range_end"], AS_OF.isoformat())

    def test_a_failed_shadow_query_is_reported_not_hidden(self):
        report = runner.run_validation(
            start=START, as_of=AS_OF,
            record_store=FakeShadowStore(ok=False),
            market_store=FakeBarStore(), outcome_store=FakeOutcomeLog(),
        )
        self.assertEqual(report["status"], "shadow_query_failed")
        self.assertEqual(report["eligible_outcomes"], 0)
        self.assertEqual(report["rows_written"], 0)

    def test_zero_eligible_outcomes_is_a_successful_run(self):
        """For the next eleven days every real run finds nothing matured. That
        is the system working, and must never be reported as a failure."""
        report = self._report()
        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["eligible_outcomes"], 0)
        self.assertEqual(report["rows_written"], 0)
        self.assertEqual(report["persistence_error"], "")
        text = runner.format_report(report)
        self.assertIn("NORMAL result", text)


# ---------------------------------------------------------------------------
# 2. Dry run vs --persist
# ---------------------------------------------------------------------------
class TestPersistIsOptIn(unittest.TestCase):
    def test_a_dry_run_issues_no_write(self):
        log = FakeOutcomeLog()
        runner.run_validation(
            start=START, as_of=AS_OF,
            record_store=FakeShadowStore([_shadow_row()]),
            market_store=FakeBarStore(), outcome_store=log,
        )
        self.assertEqual(log.insert_calls, 0)

    def test_persist_must_be_asked_for_explicitly(self):
        log = FakeOutcomeLog()
        report = runner.run_validation(
            start=START, as_of=AS_OF, persist=True,
            record_store=FakeShadowStore([_shadow_row()]),
            market_store=FakeBarStore(), outcome_store=log,
        )
        self.assertTrue(report["persist_requested"])

    def test_the_cli_defaults_to_a_dry_run(self):
        with mock.patch.object(runner, "run_validation") as fake:
            fake.return_value = _blank_report()
            with redirect_stdout(io.StringIO()):
                runner.main(["--start", "2026-08-30", "--as-of", AS_OF.isoformat()])
        self.assertFalse(fake.call_args.kwargs["persist"])

    def test_the_cli_persist_flag_reaches_the_run(self):
        with mock.patch.object(runner, "run_validation") as fake:
            fake.return_value = _blank_report()
            with redirect_stdout(io.StringIO()):
                runner.main(
                    ["--start", "2026-08-30", "--as-of", AS_OF.isoformat(), "--persist"]
                )
        self.assertTrue(fake.call_args.kwargs["persist"])

    def test_the_cli_passes_instrument_and_range_through(self):
        with mock.patch.object(runner, "run_validation") as fake:
            fake.return_value = _blank_report()
            with redirect_stdout(io.StringIO()):
                runner.main([
                    "--start", "2026-08-30", "--end", "2026-09-10",
                    "--as-of", AS_OF.isoformat(), "--instrument", "Gold",
                ])
        kwargs = fake.call_args.kwargs
        self.assertEqual(kwargs["instrument"], "Gold")
        self.assertEqual(kwargs["start"], datetime(2026, 8, 30, tzinfo=timezone.utc))
        self.assertEqual(kwargs["end"], datetime(2026, 9, 10, tzinfo=timezone.utc))
        self.assertEqual(kwargs["as_of"], AS_OF)

    def test_a_naive_timestamp_is_read_as_utc_never_as_local(self):
        self.assertEqual(
            runner._parse_moment("2026-09-20T12:00:00"),
            datetime(2026, 9, 20, 12, tzinfo=timezone.utc),
        )
        self.assertEqual(
            runner._parse_moment("2026-09-20T12:00:00Z"),
            datetime(2026, 9, 20, 12, tzinfo=timezone.utc),
        )


def _blank_report():
    return {
        "status": "ok", "as_of": AS_OF.isoformat(),
        "range_start": START.isoformat(), "range_end": AS_OF.isoformat(),
        "instrument": "(all)", "persist_requested": False, "shadow_rows": 0,
        "observations_considered": 0, "bar_rows": 0, "malformed_rows": 0,
        "symbols": [], "maturity_states": {}, "gate_census": {},
        "not_matured": 0, "awaiting_bars": 0, "withheld_execution": 0,
        "withheld_horizon": 0, "lineage_defects": 0, "eligible_outcomes": 0,
        "final_outcomes": 0, "provisional_outcomes": 0, "rows_written": 0,
        "rows_already_known": 0, "rows_conflicted": [], "rows_failed": 0,
        "outcome_backend": "none", "persistence_error": "",
    }


# ---------------------------------------------------------------------------
# 3. Formatting and CLI plumbing
# ---------------------------------------------------------------------------
class TestFormatAndCli(unittest.TestCase):
    def test_the_summary_says_which_mode_it_ran_in(self):
        dry = runner.format_report(_blank_report())
        self.assertIn("DRY RUN", dry)
        self.assertIn("--persist", dry)

        wet = dict(_blank_report(), persist_requested=True, rows_written=3)
        text = runner.format_report(wet)
        self.assertIn("PERSIST", text)
        self.assertIn("Written: 3", text)

    def test_a_determinism_conflict_is_shouted_about(self):
        report = dict(_blank_report(), rows_conflicted=["abc"])
        text = runner.format_report(report)
        self.assertIn("CONFLICTED", text)
        self.assertIn("determinism defect", text)

    def test_a_persistence_error_says_the_validation_is_unaffected(self):
        report = dict(_blank_report(), persistence_error="table missing")
        text = runner.format_report(report)
        self.assertIn("unaffected", text)

    def test_json_mode_emits_valid_json_and_exits_zero(self):
        with mock.patch.object(runner, "run_validation", return_value=_blank_report()):
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                exit_code = runner.main(
                    ["--start", "2026-08-30", "--as-of", AS_OF.isoformat(), "--json"]
                )
        self.assertEqual(exit_code, 0)
        self.assertEqual(json.loads(buffer.getvalue())["status"], "ok")

    def test_start_is_required(self):
        with self.assertRaises(SystemExit):
            with redirect_stdout(io.StringIO()):
                runner.main([])


# ---------------------------------------------------------------------------
# 4. The runner is a formatter, not a second implementation
# ---------------------------------------------------------------------------
class TestRunnerIsThin(unittest.TestCase):
    def _imports(self):
        with open(RUNNER_PATH, encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                imported.update(a.name for a in node.names)
                imported.add(node.module or "")
            if isinstance(node, ast.Import):
                imported.update(a.name for a in node.names)
        return imported

    def test_it_cannot_fetch_evaluate_or_store_on_its_own(self):
        """Structurally incapable of duplicating the pipeline: it never imports
        the network client, the bar model, any store, or any evaluation entry
        point."""
        imported = self._imports()
        for forbidden in (
            "requests", "MarketBar", "SupabaseMarketObservationStore",
            "LocalMarketObservationStore", "SupabaseValidationOutcomeStore",
            "LocalValidationOutcomeStore", "resolve_outcome_store",
            "resolve_market_store", "evaluate_observation", "build_cohort",
            "build_outcome_rows", "persistence_gate", "persist_validation_outcomes",
            "assess_maturity", "resolve_direction_and_path", "capture_daily_bars",
        ):
            self.assertNotIn(forbidden, imported, forbidden)

    def test_it_calls_exactly_one_bridge_function(self):
        imported = self._imports()
        self.assertIn("validate_stored_range", imported)

    def _referenced_names(self):
        """Every NAME the runner's CODE touches -- imports, calls, attributes.

        AST rather than raw text, deliberately, and for the reason
        ``test_b2_stage_d_storage`` already documents: this file legitimately
        NAMES the Telegram loop and the scheduler in prose to say it is not
        part of either, and a text scan would fire on the documentation
        instead of the code.
        """
        with open(RUNNER_PATH, encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                names.add(node.attr)
            if isinstance(node, ast.Name):
                names.add(node.id)
            if isinstance(node, ast.ImportFrom):
                names.add(node.module or "")
                names.update(a.name for a in node.names)
            if isinstance(node, ast.Import):
                names.update(a.name for a in node.names)
        return {n.lower() for n in names}

    def test_it_starts_no_thread_scheduler_or_loop(self):
        names = self._referenced_names()
        for forbidden in ("threading", "sched", "asyncio", "multiprocessing",
                          "crontab", "thread", "timer", "sleep"):
            self.assertFalse(
                any(forbidden in name for name in names), forbidden
            )
        with open(RUNNER_PATH, encoding="utf-8") as handle:
            source = handle.read()
        for forbidden in ("Thread(", "Timer(", "time.sleep", "while True"):
            self.assertNotIn(forbidden, source, forbidden)

    def test_it_makes_no_ai_or_telegram_call(self):
        names = self._referenced_names()
        for forbidden in ("telegram", "openai", "anthropic", "gemini", "groq",
                          "completions", "send_message", "sendmessage"):
            self.assertFalse(
                any(forbidden in name for name in names), forbidden
            )

    def test_it_computes_no_rate_of_any_kind(self):
        names = self._referenced_names()
        for forbidden in ("hit_rate", "win_rate", "accuracy", "calibrat",
                          "confirmation_rate", "percent", "ratio"):
            self.assertFalse(
                any(forbidden in name for name in names), forbidden
            )
        with open(RUNNER_PATH, encoding="utf-8") as handle:
            source = handle.read()
        # No arithmetic that could become a rate.
        for forbidden in ("/ total", "/ len(", "* 100"):
            self.assertNotIn(forbidden, source, forbidden)

    def test_it_reads_maturity_from_the_envelope_never_recomputed(self):
        """A report that recomputed maturity could disagree with the run it
        describes."""
        with open(RUNNER_PATH, encoding="utf-8") as handle:
            source = handle.read()
        self.assertIn('getattr(context, "maturity_state"', source)
        self.assertNotIn("assess_maturity", source)


if __name__ == "__main__":
    unittest.main()
