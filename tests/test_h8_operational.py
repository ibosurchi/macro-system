"""H8 -- durable execution layer.

The defect H8 closes:

    B2 evidence capture ran in a ``daemon=True`` thread owned by the Streamlit
    process. It died with that process -- silently -- and its only health signal
    was a module-level dict that died with it. Correct analytical code did not
    prove continuous evidence capture.

These tests are organised around the properties that make the replacement safe
to schedule: the exit-code contract, durability detection, lease exclusivity,
the absence of any way to fabricate a missed bucket, and the guarantee that the
operational layer needs no Streamlit runtime at all.

Nothing here touches the network, Supabase, or production state. Every backend
is an in-memory fake that models the real PostgREST semantics.
"""
from __future__ import annotations

import ast
import hashlib
import io
import json
import os
import subprocess
import sys
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from state_isolation import isolate_durable_state

isolate_durable_state()

from apex import ops
from apex.ops import config as ops_config
from apex.ops import heartbeat as ops_heartbeat
from apex.ops import lease as ops_lease
from apex.ops import logging as ops_logging
from apex.ops import runner as ops_runner
from apex.ops.__main__ import main as ops_main

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OPS_DIR = os.path.join(ROOT, "apex", "ops")
WORKFLOW_DIR = os.path.join(ROOT, ".github", "workflows")

NOW = datetime(2026, 9, 3, 14, 30, tzinfo=timezone.utc)


def _read(*parts: str) -> str:
    """Read a repository file. Closes the handle -- an unclosed one emits a
    ResourceWarning that would be noise in every future run of this suite."""
    with open(os.path.join(*parts), encoding="utf-8") as handle:
        return handle.read()


CONFIGURED = ops_config.OpsSettings(
    supabase_url="https://example.supabase.co",
    supabase_key="test-service-role-key-value",
    fred_key="test-fred-key",
    telegram_channel="Test_Channel",
)
UNCONFIGURED = ops_config.OpsSettings(
    supabase_url="", supabase_key="", fred_key="", telegram_channel=""
)


# ---------------------------------------------------------------------------
# In-memory PostgREST fake
# ---------------------------------------------------------------------------
class _Response:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakePostgrest:
    """Models the exact PostgREST semantics the ops layer relies on.

    Specifically: upsert with ``on_conflict``, ``ignore-duplicates`` leaving an
    existing row untouched, and -- the one that matters -- a conditional PATCH
    that updates zero rows when its filter does not match, which is how lease
    exclusivity is decided.
    """

    def __init__(self):
        self.rows: dict[str, dict] = {}
        self.fail_next = False

    # -- helpers ---------------------------------------------------------
    def _matches_lease_filter(self, row, or_filter, owner_filter):
        if owner_filter is not None:
            return row.get("lease_owner") == owner_filter
        if or_filter is None:
            return True
        # or=(lease_expires_at.is.null,lease_expires_at.lt.<iso>)
        expires = row.get("lease_expires_at")
        if expires is None:
            return True
        cutoff = or_filter.split("lease_expires_at.lt.")[1].rstrip(")")
        return str(expires) < str(cutoff)

    # -- HTTP verbs ------------------------------------------------------
    def post(self, url, headers=None, params=None, json=None, timeout=None):
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("simulated backend outage")
        prefer = (headers or {}).get("Prefer", "")
        for payload in json or []:
            key = payload["job_key"]
            if key in self.rows:
                if "ignore-duplicates" in prefer:
                    continue  # existing row untouched -- cannot steal a lease
                self.rows[key].update(payload)
            else:
                self.rows[key] = dict(payload)
        return _Response([], 201)

    def patch(self, url, headers=None, params=None, json=None, timeout=None):
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("simulated backend outage")
        params = params or {}
        key = str(params.get("job_key", "")).replace("eq.", "")
        owner_filter = params.get("lease_owner")
        if owner_filter is not None:
            owner_filter = str(owner_filter).replace("eq.", "")
        row = self.rows.get(key)
        if row is None:
            return _Response([], 200)
        if not self._matches_lease_filter(row, params.get("or"), owner_filter):
            return _Response([], 200)  # zero rows updated
        row.update(json or {})
        return _Response([dict(row)], 200)

    def get(self, url, headers=None, params=None, timeout=None):
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("simulated backend outage")
        params = params or {}
        key = params.get("job_key")
        if key:
            key = str(key).replace("eq.", "")
            rows = [dict(self.rows[key])] if key in self.rows else []
        else:
            rows = [dict(v) for v in self.rows.values()]
        return _Response(rows, 200)


def _stores(fake):
    """Health and lease stores wired to one shared fake backend."""
    health = ops_heartbeat.HealthStore(CONFIGURED)
    lease = ops_lease.LeaseStore(CONFIGURED)
    return health, lease


# ---------------------------------------------------------------------------
# 1. Exit-code contract
# ---------------------------------------------------------------------------
class TestExitCodeContract(unittest.TestCase):
    def test_the_five_codes_are_distinct_and_stable(self):
        codes = [
            ops.ExitCode.SUCCESS, ops.ExitCode.JOB_FAILURE,
            ops.ExitCode.CONFIG_UNAVAILABLE, ops.ExitCode.LEASE_NOT_ACQUIRED,
            ops.ExitCode.NON_DURABLE,
        ]
        self.assertEqual(codes, [0, 1, 2, 3, 4])
        self.assertEqual(len(set(codes)), 5)

    def test_every_code_maps_to_a_status(self):
        for code in (0, 1, 2, 3, 4):
            self.assertIn(code, ops.STATUS_BY_EXIT)

    def test_non_durable_is_never_success(self):
        """The whole point of code 4: it must not be readable as clean capture."""
        self.assertNotEqual(ops.ExitCode.NON_DURABLE, ops.ExitCode.SUCCESS)
        self.assertEqual(ops.STATUS_BY_EXIT[ops.ExitCode.NON_DURABLE], "non_durable")

    def test_success_status_requires_durability_on_the_heartbeat_row(self):
        """A non-durable completion must not advance last_success_at."""
        health = ops_heartbeat.JobHealth(
            job_key="capture_shadow", run_id="r", status="non_durable", durable=False
        )
        row = health.to_row(now=NOW)
        self.assertNotIn("last_success_at", row)
        self.assertIn("last_failure_at", row)
        self.assertFalse(row["last_durable"])

    def test_durable_success_advances_last_success_at(self):
        health = ops_heartbeat.JobHealth(
            job_key="capture_shadow", run_id="r", status="success", durable=True
        )
        row = health.to_row(now=NOW)
        self.assertEqual(row["last_success_at"], NOW.isoformat())
        self.assertTrue(row["last_durable"])


# ---------------------------------------------------------------------------
# 2. Configuration pre-flight (exit 2)
# ---------------------------------------------------------------------------
class TestConfigPreflight(unittest.TestCase):
    def test_missing_supabase_gives_exit_2_and_does_not_attempt_capture(self):
        result = ops_runner.run_capture_shadow(settings=UNCONFIGURED, now=NOW)
        self.assertEqual(result.exit_code, ops.ExitCode.CONFIG_UNAVAILABLE)
        self.assertFalse(result.detail["capture_attempted"])
        self.assertIn("SUPABASE_URL", result.detail["missing_config"])

    def test_missing_config_reports_names_never_values(self):
        result = ops_runner.run_capture_shadow(settings=UNCONFIGURED, now=NOW)
        self.assertEqual(
            set(result.detail["missing_config"]),
            {"SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "FRED_API_KEY"},
        )

    def test_market_bars_needs_no_fred_key(self):
        settings = ops_config.OpsSettings(
            supabase_url="https://x", supabase_key="k", fred_key="", telegram_channel=""
        )
        result = ops_runner.run_capture_market_bars(
            settings=settings, dry_run=True, now=NOW
        )
        self.assertEqual(result.exit_code, ops.ExitCode.SUCCESS)

    def test_check_health_without_config_exits_2(self):
        code, report = ops_runner.check_health(settings=UNCONFIGURED)
        self.assertEqual(code, ops.ExitCode.CONFIG_UNAVAILABLE)
        self.assertFalse(report["ok"])


# ---------------------------------------------------------------------------
# 3. Dry run writes nothing, anywhere
# ---------------------------------------------------------------------------
class TestDryRunWritesNothing(unittest.TestCase):
    def test_all_three_jobs_dry_run_without_attempting_work(self):
        for job, kwargs in (
            (ops_runner.run_capture_shadow, {}),
            (ops_runner.run_capture_market_bars, {}),
            (ops_runner.run_evaluate_outcomes, {"persist": True}),
        ):
            with self.subTest(job=job.__name__):
                result = job(settings=CONFIGURED, dry_run=True, now=NOW, **kwargs)
                self.assertEqual(result.exit_code, ops.ExitCode.SUCCESS)
                self.assertTrue(result.detail["dry_run"])
                self.assertEqual(result.records_written, 0)

    def test_dry_run_writes_no_heartbeat(self):
        fake = FakePostgrest()
        with mock.patch.object(ops_heartbeat, "requests", fake):
            with redirect_stdout(io.StringIO()):
                ops_runner.execute(
                    ops.JOB_CAPTURE_MARKET_BARS, dry_run=True, settings=CONFIGURED
                )
        self.assertEqual(fake.rows, {})

    def test_dry_run_takes_no_lease(self):
        fake = FakePostgrest()
        with mock.patch.object(ops_lease, "requests", fake):
            ops_runner.run_capture_shadow(settings=CONFIGURED, dry_run=True, now=NOW)
        self.assertEqual(fake.rows, {})

    def test_dry_run_evaluate_outcomes_never_persists_even_with_persist(self):
        called = {}

        def _should_not_run(**kwargs):
            called["ran"] = True
            return {}

        ops_runner.run_evaluate_outcomes(
            settings=CONFIGURED, dry_run=True, persist=True,
            now=NOW, validate=_should_not_run,
        )
        self.assertNotIn("ran", called)


# ---------------------------------------------------------------------------
# 4. NON_DURABLE detection (exit 4)
# ---------------------------------------------------------------------------
class TestNonDurableDetection(unittest.TestCase):
    def _run_shadow_with_stats(self, stats):
        fake = FakePostgrest()
        fake_bridge = mock.MagicMock()
        fake_bridge.run_shadow_observation.return_value = {"Gold": "written"}
        fake_bridge.get_shadow_hook_stats.return_value = stats
        with mock.patch.object(ops_lease, "requests", fake), mock.patch.dict(
            sys.modules, {"apex.b2_bridge": fake_bridge}
        ):
            return ops_runner.run_capture_shadow(settings=CONFIGURED, now=NOW)

    def test_local_fallback_gives_exit_4_and_last_durable_false(self):
        result = self._run_shadow_with_stats({"written": 11, "v2_local_fallback": 1})
        self.assertEqual(result.exit_code, ops.ExitCode.NON_DURABLE)
        self.assertFalse(result.durable)
        self.assertEqual(result.status, "non_durable")

    def test_clean_supabase_write_is_durable_success(self):
        result = self._run_shadow_with_stats({"written": 11, "v2_local_fallback": 0})
        self.assertEqual(result.exit_code, ops.ExitCode.SUCCESS)
        self.assertTrue(result.durable)
        self.assertEqual(result.records_written, 11)

    def test_market_bars_non_durable_report_gives_exit_4(self):
        report = {
            "durable": False, "backend": "local", "inserted": 4,
            "failed_instruments": [], "failed_rows": 0, "error": "",
        }
        result = ops_runner.run_capture_market_bars(
            settings=CONFIGURED, now=NOW, capture=lambda **k: report
        )
        self.assertEqual(result.exit_code, ops.ExitCode.NON_DURABLE)

    def test_market_bars_durable_report_is_success(self):
        report = {
            "durable": True, "backend": "supabase", "inserted": 4,
            "failed_instruments": [], "failed_rows": 0, "error": "",
        }
        result = ops_runner.run_capture_market_bars(
            settings=CONFIGURED, now=NOW, capture=lambda **k: report
        )
        self.assertEqual(result.exit_code, ops.ExitCode.SUCCESS)
        self.assertTrue(result.durable)

    def test_outcomes_with_nothing_eligible_is_success_not_non_durable(self):
        """Zero matured outcomes is the NORMAL result and must not alert."""
        report = {
            "rows_written": 0, "rows_failed": 0, "rows_conflicted": [],
            "outcome_backend": "none", "persistence_error": "",
            "eligible_outcomes": 0,
        }
        result = ops_runner.run_evaluate_outcomes(
            settings=CONFIGURED, now=NOW, persist=True, validate=lambda **k: report
        )
        self.assertEqual(result.exit_code, ops.ExitCode.SUCCESS)

    def test_outcomes_written_locally_gives_exit_4(self):
        report = {
            "rows_written": 3, "rows_failed": 0, "rows_conflicted": [],
            "outcome_backend": "local", "persistence_error": "",
        }
        result = ops_runner.run_evaluate_outcomes(
            settings=CONFIGURED, now=NOW, persist=True, validate=lambda **k: report
        )
        self.assertEqual(result.exit_code, ops.ExitCode.NON_DURABLE)


# ---------------------------------------------------------------------------
# 5. Job failure (exit 1)
# ---------------------------------------------------------------------------
class TestJobFailure(unittest.TestCase):
    def test_shadow_capture_exception_gives_exit_1(self):
        fake = FakePostgrest()
        fake_bridge = mock.MagicMock()
        fake_bridge.run_shadow_observation.side_effect = RuntimeError("provider down")
        with mock.patch.object(ops_lease, "requests", fake), mock.patch.dict(
            sys.modules, {"apex.b2_bridge": fake_bridge}
        ):
            result = ops_runner.run_capture_shadow(settings=CONFIGURED, now=NOW)
        self.assertEqual(result.exit_code, ops.ExitCode.JOB_FAILURE)
        self.assertEqual(result.error_class, "RuntimeError")

    def test_market_bars_failed_instrument_gives_exit_1(self):
        report = {
            "durable": True, "backend": "supabase", "inserted": 2,
            "failed_instruments": ["Oil"], "failed_rows": 0, "error": "",
        }
        result = ops_runner.run_capture_market_bars(
            settings=CONFIGURED, now=NOW, capture=lambda **k: report
        )
        self.assertEqual(result.exit_code, ops.ExitCode.JOB_FAILURE)

    def test_determinism_conflict_gives_exit_1(self):
        """One job, one evidence set, two verdicts is a defect, not an event."""
        report = {
            "rows_written": 0, "rows_failed": 0, "rows_conflicted": ["abc123"],
            "outcome_backend": "supabase", "persistence_error": "",
        }
        result = ops_runner.run_evaluate_outcomes(
            settings=CONFIGURED, now=NOW, persist=True, validate=lambda **k: report
        )
        self.assertEqual(result.exit_code, ops.ExitCode.JOB_FAILURE)
        self.assertEqual(result.error_class, "DeterminismConflict")

    def test_indeterminate_lease_is_failure_not_lease_not_acquired(self):
        """A backend outage must not masquerade as 'another run owns it'.

        Reporting exit 3 for an unreachable backend would silently skip capture
        forever while looking healthy.
        """
        fake = FakePostgrest()
        fake.fail_next = True
        with mock.patch.object(ops_lease, "requests", fake):
            result = ops_runner.run_capture_shadow(settings=CONFIGURED, now=NOW)
        self.assertEqual(result.exit_code, ops.ExitCode.JOB_FAILURE)


# ---------------------------------------------------------------------------
# 6. Lease exclusivity (exit 3)
# ---------------------------------------------------------------------------
class TestLease(unittest.TestCase):
    def test_first_owner_acquires_and_second_does_not(self):
        fake = FakePostgrest()
        with mock.patch.object(ops_lease, "requests", fake):
            store = ops_lease.LeaseStore(CONFIGURED)
            first = store.acquire(owner="owner-one", now=NOW)
            second = store.acquire(owner="owner-two", now=NOW)
        self.assertTrue(first.acquired)
        self.assertFalse(second.acquired)
        self.assertFalse(second.indeterminate)

    def test_second_concurrent_run_exits_3(self):
        fake = FakePostgrest()
        with mock.patch.object(ops_lease, "requests", fake):
            ops_lease.LeaseStore(CONFIGURED).acquire(owner="holder", now=NOW)
            result = ops_runner.run_capture_shadow(settings=CONFIGURED, now=NOW)
        self.assertEqual(result.exit_code, ops.ExitCode.LEASE_NOT_ACQUIRED)
        self.assertFalse(result.detail["capture_attempted"])

    def test_expired_lease_is_recoverable_after_a_crash(self):
        fake = FakePostgrest()
        with mock.patch.object(ops_lease, "requests", fake):
            store = ops_lease.LeaseStore(CONFIGURED)
            crashed = store.acquire(owner="crashed", now=NOW)
            self.assertTrue(crashed.acquired)
            later = NOW + timedelta(seconds=ops_lease.LEASE_TTL_SECONDS + 60)
            recovered = store.acquire(owner="next-run", now=later)
        self.assertTrue(recovered.acquired)

    def test_release_only_affects_the_current_owner(self):
        fake = FakePostgrest()
        with mock.patch.object(ops_lease, "requests", fake):
            store = ops_lease.LeaseStore(CONFIGURED)
            store.acquire(owner="owner-one", now=NOW)
            store.release(owner="someone-else", now=NOW)
            blocked = store.acquire(owner="owner-two", now=NOW)
        self.assertFalse(blocked.acquired)

    def test_released_lease_can_be_retaken_immediately(self):
        fake = FakePostgrest()
        with mock.patch.object(ops_lease, "requests", fake):
            store = ops_lease.LeaseStore(CONFIGURED)
            store.acquire(owner="owner-one", now=NOW)
            store.release(owner="owner-one", now=NOW)
            again = store.acquire(owner="owner-two", now=NOW)
        self.assertTrue(again.acquired)

    def test_ensure_row_cannot_steal_a_live_lease(self):
        fake = FakePostgrest()
        with mock.patch.object(ops_lease, "requests", fake):
            store = ops_lease.LeaseStore(CONFIGURED)
            store.acquire(owner="holder", now=NOW)
            store._ensure_row("capture_shadow")
            stolen = store.acquire(owner="thief", now=NOW)
        self.assertFalse(stolen.acquired)

    def test_owner_tokens_are_unique_per_run(self):
        tokens = {ops_lease.new_owner_token() for _ in range(200)}
        self.assertEqual(len(tokens), 200)

    def test_the_ai_provider_lease_is_not_reused(self):
        source = _read(OPS_DIR, "lease.py")
        for foreign in ("provider_lease", "ai_lease", "_acquire_provider"):
            self.assertNotIn(foreign, source)


# ---------------------------------------------------------------------------
# 7. Heartbeat durability
# ---------------------------------------------------------------------------
class TestHeartbeat(unittest.TestCase):
    def test_health_survives_the_object_that_wrote_it(self):
        """A new store object -- standing in for a new process -- still reads it."""
        fake = FakePostgrest()
        with mock.patch.object(ops_heartbeat, "requests", fake):
            writer = ops_heartbeat.HealthStore(CONFIGURED)
            writer.write(
                ops_heartbeat.JobHealth(
                    job_key="capture_shadow", run_id="run-1",
                    status="success", durable=True, records_written=11,
                ),
                now=NOW,
            )
            del writer
            reader = ops_heartbeat.HealthStore(CONFIGURED)
            outcome = reader.read("capture_shadow")
        self.assertTrue(outcome.ok)
        self.assertEqual(outcome.rows[0]["last_success_at"], NOW.isoformat())
        self.assertTrue(outcome.rows[0]["last_durable"])

    def test_heartbeat_failure_does_not_change_the_evidence_result(self):
        fake = FakePostgrest()
        fake.fail_next = True
        result = ops_runner.JobResult(
            job_key="capture_shadow", run_id="r",
            exit_code=ops.ExitCode.SUCCESS, durable=True, records_written=11,
        )
        with mock.patch.object(ops_heartbeat, "requests", fake):
            with redirect_stdout(io.StringIO()) as buffer:
                record = ops_runner.record_health(result, settings=CONFIGURED)
        self.assertFalse(record["ok"])
        # The evidence verdict is untouched...
        self.assertEqual(result.exit_code, ops.ExitCode.SUCCESS)
        # ...and the operational failure is surfaced, never swallowed.
        self.assertIn("heartbeat_write_failed", buffer.getvalue())

    def test_health_read_reports_every_job(self):
        fake = FakePostgrest()
        with mock.patch.object(ops_heartbeat, "requests", fake):
            store = ops_heartbeat.HealthStore(CONFIGURED)
            for key in ops.JOB_KEYS:
                store.write(
                    ops_heartbeat.JobHealth(job_key=key, run_id="r", status="success",
                                            durable=True),
                    now=NOW,
                )
            code, report = ops_runner.check_health(settings=CONFIGURED, store=store)
        self.assertEqual(code, ops.ExitCode.SUCCESS)
        self.assertEqual(len(report["jobs"]), len(ops.JOB_KEYS))

    def test_attempt_and_success_are_independent_axes(self):
        """A job attempting hourly and failing hourly must look attempted, not fresh."""
        health = ops_heartbeat.JobHealth(
            job_key="capture_shadow", run_id="r", status="failure", durable=False
        )
        row = health.to_row(now=NOW)
        self.assertIn("last_attempt_at", row)
        self.assertNotIn("last_success_at", row)


# ---------------------------------------------------------------------------
# 8. Idempotency of repeated invocation
# ---------------------------------------------------------------------------
class TestRepeatedInvocation(unittest.TestCase):
    def test_same_bucket_twice_does_not_duplicate_logical_evidence(self):
        """The bridge's durable dedup owns this; the runner must not defeat it."""
        from apex import b2_bridge
        from apex.b2.enums import Horizon

        first = b2_bridge.observation_key("Gold", Horizon.TACTICAL, NOW)
        again = b2_bridge.observation_key(
            "Gold", Horizon.TACTICAL, NOW + timedelta(minutes=25)
        )
        self.assertEqual(first, again)
        self.assertEqual(
            b2_bridge.observation_record_id(first),
            b2_bridge.observation_record_id(again),
        )

    def test_a_later_hour_is_a_different_bucket(self):
        from apex import b2_bridge
        from apex.b2.enums import Horizon

        self.assertNotEqual(
            b2_bridge.observation_key("Gold", Horizon.TACTICAL, NOW),
            b2_bridge.observation_key(
                "Gold", Horizon.TACTICAL, NOW + timedelta(hours=1)
            ),
        )

    def test_runner_reports_the_bucket_it_intended(self):
        result = ops_runner.run_capture_shadow(
            settings=CONFIGURED, dry_run=True, now=NOW
        )
        self.assertEqual(result.logical_bucket, "2026-09-03T14")


# ---------------------------------------------------------------------------
# 9. No fabricated buckets -- the point-in-time guarantee
# ---------------------------------------------------------------------------
class TestNoBackfill(unittest.TestCase):
    def test_no_cli_option_can_backdate_a_shadow_capture(self):
        """A shadow evaluation cannot be reconstructed for a past hour.

        It is built from live production values that no longer exist, so any
        flag that set an earlier ``evaluated_at`` would stamp today's evidence
        with yesterday's time -- fabricating a prediction. The absence of the
        option IS the safeguard, so it is asserted rather than assumed.
        """
        # Asserted against the REGISTERED options, not the source text: the
        # module docstring deliberately names the flags that must not exist, and
        # a text scan would fire on the very sentence explaining the rule.
        from apex.ops.__main__ import build_parser

        registered = set()
        for action in build_parser()._subparsers._group_actions[0].choices[  # noqa: SLF001
            "run"
        ]._actions:
            registered.update(action.option_strings)
        for forbidden in ("--backfill", "--bucket", "--at", "--since", "--evaluated-at"):
            self.assertNotIn(forbidden, registered, forbidden)

    def test_shadow_capture_accepts_no_backfill_argument(self):
        import inspect

        params = inspect.signature(ops_runner.run_capture_shadow).parameters
        for forbidden in ("backfill", "bucket", "evaluated_at", "since"):
            self.assertNotIn(forbidden, params)

    def test_the_cli_rejects_a_backfill_flag(self):
        with self.assertRaises(SystemExit):
            with redirect_stdout(io.StringIO()):
                ops_main(["run", "capture-shadow", "--backfill", "5"])


# ---------------------------------------------------------------------------
# 10. No Streamlit requirement in the operational layer
# ---------------------------------------------------------------------------
class TestNoStreamlitDependency(unittest.TestCase):
    #: ``runner`` is excluded deliberately: it reaches the existing bridges,
    #: which legitimately depend on the production module. Its imports are
    #: function-local so importing the package costs nothing.
    SUPPORT_MODULES = ("__init__.py", "config.py", "heartbeat.py", "lease.py", "logging.py")

    def test_support_modules_never_import_streamlit(self):
        for name in self.SUPPORT_MODULES:
            with self.subTest(module=name):
                tree = ast.parse(_read(OPS_DIR, name))
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            self.assertNotIn("streamlit", alias.name)
                    if isinstance(node, ast.ImportFrom):
                        self.assertNotIn("streamlit", node.module or "")

    def test_support_layer_imports_in_a_process_with_no_streamlit_loaded(self):
        """The strongest form of the claim: prove it in a fresh interpreter."""
        code = (
            "import sys;"
            "import apex.ops, apex.ops.config, apex.ops.heartbeat,"
            "apex.ops.lease, apex.ops.logging;"
            "print('streamlit' in sys.modules)"
        )
        completed = subprocess.run(
            [sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True,
            timeout=120,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr[-2000:])
        self.assertIn("False", completed.stdout)

    def test_health_check_runs_with_no_streamlit_runtime(self):
        completed = subprocess.run(
            [sys.executable, "-m", "apex.ops", "check", "health"],
            cwd=ROOT, capture_output=True, text=True, timeout=120,
        )
        self.assertIn(completed.returncode, (0, 1, 2))
        self.assertNotIn("Traceback", completed.stderr)


# ---------------------------------------------------------------------------
# 11. UTC-only time handling
# ---------------------------------------------------------------------------
class TestTimeSafety(unittest.TestCase):
    def test_ops_uses_no_naive_clock(self):
        for name in os.listdir(OPS_DIR):
            if not name.endswith(".py"):
                continue
            source = _read(OPS_DIR, name)
            with self.subTest(module=name):
                # ``datetime.utcnow()`` returns a NAIVE datetime and is the
                # specific trap here. The package's own ``utcnow()`` helper is
                # timezone-aware and is the only clock it may use.
                self.assertNotIn("datetime.utcnow", source)
                self.assertNotIn("datetime.now()", source)
                self.assertNotIn("get_current_time", source)
                self.assertNotIn("st.session_state", source)
                self.assertNotIn("time.time()", source)

    def test_the_ops_clock_is_timezone_aware_utc(self):
        moment = ops_logging.utcnow()
        self.assertIsNotNone(moment.tzinfo)
        self.assertEqual(moment.utcoffset(), timedelta(0))

    def test_every_emitted_timestamp_is_offset_aware(self):
        with redirect_stdout(io.StringIO()) as buffer:
            ops_logging.emit("probe")
        stamp = json.loads(buffer.getvalue())["timestamp_utc"]
        self.assertTrue(stamp.endswith("+00:00"))


# ---------------------------------------------------------------------------
# 12. Secret handling and redaction
# ---------------------------------------------------------------------------
class TestSecretSafety(unittest.TestCase):
    FAKE = "sk-test-abcdefghijklmnopqrstuvwxyz-0123456789"

    def test_a_seeded_secret_never_reaches_a_log_line(self):
        with mock.patch.dict(os.environ, {"SUPABASE_SERVICE_ROLE_KEY": self.FAKE}):
            with redirect_stdout(io.StringIO()) as buffer:
                ops_logging.emit("probe", detail=f"failed using {self.FAKE}")
        self.assertNotIn(self.FAKE, buffer.getvalue())
        self.assertIn(ops_logging.REDACTED, buffer.getvalue())

    def test_a_seeded_secret_never_reaches_a_heartbeat_error_summary(self):
        with mock.patch.dict(os.environ, {"SUPABASE_SERVICE_ROLE_KEY": self.FAKE}):
            health = ops_heartbeat.JobHealth(job_key="capture_shadow", run_id="r")
            health.mark(RuntimeError(f"connect failed for key {self.FAKE}"))
            row = health.to_row(now=NOW)
        self.assertNotIn(self.FAKE, json.dumps(row))

    def test_error_summary_is_length_capped(self):
        summary = ops_logging.error_summary("x" * 5000)
        self.assertLessEqual(len(summary), ops_logging.ERROR_SUMMARY_LIMIT)

    def test_error_summary_is_never_a_traceback(self):
        try:
            raise ValueError("boom")
        except ValueError as exc:
            summary = ops_logging.error_summary(exc)
        self.assertNotIn("Traceback", summary)
        self.assertNotIn("File \"", summary)

    def test_secrets_file_is_written_by_python_and_gitignored(self):
        gitignore = _read(ROOT, ".gitignore")
        self.assertIn(".streamlit/secrets.toml", gitignore)

    def test_no_bot_token_is_requested_anywhere(self):
        """H8 sends no message, so it must not ask for a messaging credential."""
        self.assertNotIn("TELEGRAM_BOT_TOKEN", ops_config.SUPPORTED_SECRET_NAMES)
        for name in os.listdir(OPS_DIR):
            if name.endswith(".py"):
                source = _read(OPS_DIR, name)
                self.assertNotIn("sendMessage", source)

    def test_shadow_enable_switch_is_never_written_by_the_runner(self):
        """The cutover depends on the two runtimes reading independent stores."""
        for name in os.listdir(OPS_DIR):
            if name.endswith(".py"):
                source = _read(OPS_DIR, name)
                self.assertNotIn(
                    'B2_SHADOW_ENABLED"', source.replace("# ", "")
                )

    def test_materialize_writes_only_names_it_was_given(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(
                os.environ, {"SUPABASE_URL": "https://x", "FRED_API_KEY": "abc"},
                clear=False,
            ):
                path, written = ops_config.materialize_streamlit_secrets(
                    root=Path(tmp), overwrite=True
                )
            self.assertIn("SUPABASE_URL", written)
            body = path.read_text(encoding="utf-8")
            self.assertIn("SUPABASE_URL", body)
            self.assertNotIn("B2_SHADOW_ENABLED", body)

    def test_settings_describe_reports_presence_not_values(self):
        described = CONFIGURED.describe()
        self.assertNotIn(CONFIGURED.supabase_key, json.dumps(described))
        self.assertTrue(described["supabase_key_present"])


# ---------------------------------------------------------------------------
# 13. Workflow specification
# ---------------------------------------------------------------------------
def _uncommented(text: str) -> str:
    """Workflow text with comment lines removed.

    Comments legitimately DESCRIBE the future schedule; only a real trigger
    counts as activation, so the assertions below read the active YAML only.
    """
    return "\n".join(
        line for line in text.splitlines() if not line.strip().startswith("#")
    )


class TestWorkflows(unittest.TestCase):
    FILES = ("b2-capture-shadow.yml", "b2-daily.yml")

    def _text(self, name):
        return _read(WORKFLOW_DIR, name)

    def test_both_workflows_exist(self):
        for name in self.FILES:
            self.assertTrue(os.path.exists(os.path.join(WORKFLOW_DIR, name)), name)

    def test_manual_dispatch_only_and_no_active_schedule(self):
        """This phase prepares infrastructure; activation is a separate step."""
        for name in self.FILES:
            active = _uncommented(self._text(name))
            with self.subTest(workflow=name):
                self.assertIn("workflow_dispatch:", active)
                self.assertNotIn("schedule:", active)
                self.assertNotIn("cron:", active)

    def test_least_privilege_read_only_token(self):
        for name in self.FILES:
            active = _uncommented(self._text(name))
            with self.subTest(workflow=name):
                self.assertIn("permissions:", active)
                self.assertIn("contents: read", active)
                self.assertNotIn("contents: write", active)
                self.assertNotIn("packages: write", active)
                self.assertNotIn("id-token:", active)

    def test_never_cancels_an_in_flight_run(self):
        for name in self.FILES:
            active = _uncommented(self._text(name))
            with self.subTest(workflow=name):
                self.assertIn("cancel-in-progress: false", active)
                self.assertNotIn("cancel-in-progress: true", active)

    def test_no_push_commit_tag_or_artifact_upload(self):
        for name in self.FILES:
            active = _uncommented(self._text(name))
            with self.subTest(workflow=name):
                for forbidden in ("git push", "git commit", "git tag",
                                  "upload-artifact", "actions/cache/save"):
                    self.assertNotIn(forbidden, active, forbidden)

    def test_python_311_and_pinned_actions(self):
        for name in self.FILES:
            active = _uncommented(self._text(name))
            with self.subTest(workflow=name):
                self.assertIn('python-version: "3.11"', active)
                self.assertIn("cache: pip", active)
                # Pinned to a 40-character commit SHA, never a moving tag. The
                # trailing "# vX.Y.Z" comment names the version the pin refers
                # to so an operator can verify it.
                for line in active.splitlines():
                    if "uses:" in line:
                        ref = line.split("@")[-1].split("#")[0].strip()
                        self.assertEqual(len(ref), 40, line)
                        int(ref, 16)  # hexadecimal or this raises

    def test_secrets_are_passed_as_env_never_echoed(self):
        for name in self.FILES:
            active = _uncommented(self._text(name))
            with self.subTest(workflow=name):
                self.assertNotIn("echo ${{ secrets", active)
                self.assertNotIn("TELEGRAM_BOT_TOKEN", active)
                self.assertIn("rm -f .streamlit/secrets.toml", active)

    def test_shadow_workflow_does_not_set_the_cutover_switch(self):
        active = _uncommented(self._text("b2-capture-shadow.yml"))
        self.assertNotIn("B2_SHADOW_ENABLED", active)

    def test_job_c_runs_only_after_job_b_succeeds(self):
        active = _uncommented(self._text("b2-daily.yml"))
        self.assertIn("needs: market-bars", active)
        # No JOB-LEVEL condition may override the dependency. A job-level key
        # sits at four spaces; the ``if: always()`` on the secrets-cleanup STEP
        # is at eight and is both legitimate and required, so the indent is what
        # distinguishes them.
        for line in active.splitlines():
            if line.startswith("    if:"):
                self.fail(f"job-level condition overrides the B->C gate: {line}")

    def test_the_daily_workflow_uses_the_agreed_lookback(self):
        active = _uncommented(self._text("b2-daily.yml"))
        self.assertIn("--lookback-days 30", active)


# ---------------------------------------------------------------------------
# 14. Architectural guards still hold
# ---------------------------------------------------------------------------
class TestArchitecturalGuards(unittest.TestCase):
    PROTECTED_SHA = (
        "5935f807a8584007fc053ae7bb64d62017a7e2f804258d492fdd8a4c2cb4da69"
    )

    def test_production_core_is_byte_for_byte_unchanged(self):
        with open(os.path.join(ROOT, "apex", "production_core.py"), "rb") as handle:
            digest = hashlib.sha256(handle.read()).hexdigest()
        self.assertEqual(digest, self.PROTECTED_SHA)

    def test_ops_never_imports_production_core_outside_the_runner(self):
        # AST, not text: several modules legitimately NAME production_core in
        # prose to explain how they relate to it -- the same reason the existing
        # bridge guard parses rather than greps.
        for name in os.listdir(OPS_DIR):
            if not name.endswith(".py") or name == "runner.py":
                continue
            tree = ast.parse(_read(OPS_DIR, name))
            with self.subTest(module=name):
                for node in ast.walk(tree):
                    if isinstance(node, ast.ImportFrom):
                        self.assertNotIn("production_core", node.module or "")
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            self.assertNotIn("production_core", alias.name)

    def test_ops_does_not_import_the_validation_bridge(self):
        """Its two approved script importers stay the only ones."""
        for name in os.listdir(OPS_DIR):
            if not name.endswith(".py"):
                continue
            tree = ast.parse(_read(OPS_DIR, name))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    self.assertNotIn("b2_validation_bridge", node.module or "")
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertNotIn("b2_validation_bridge", alias.name)

    def test_ops_executes_no_schema_statements(self):
        for name in os.listdir(OPS_DIR):
            if not name.endswith(".py"):
                continue
            source = _read(OPS_DIR, name).upper()
            with self.subTest(module=name):
                for verb in ("CREATE TABLE", "ALTER TABLE", "DROP TABLE",
                             "TRUNCATE", "CREATE INDEX"):
                    self.assertNotIn(verb, source, verb)

    def test_the_operator_run_migration_exists_and_is_not_executed(self):
        path = os.path.join(ROOT, "sql", "003_b2_ops_job_health.sql")
        self.assertTrue(os.path.exists(path))
        body = _read(path)
        self.assertIn("b2_ops_job_health", body)
        self.assertIn("enable row level security", body)
        # The deliberate exception to the append-only convention, documented.
        self.assertIn("revoke delete, truncate", body)
        # It must NOT revoke update: this table is intentionally mutable.
        self.assertNotIn("revoke update, delete, truncate", body)

    def test_no_existing_evidence_table_is_referenced_for_writing(self):
        for name in os.listdir(OPS_DIR):
            if not name.endswith(".py"):
                continue
            source = _read(OPS_DIR, name)
            with self.subTest(module=name):
                for table in ("b2_shadow_records", "b2_market_observations",
                              "b2_validation_outcomes",
                              "b2_market_observation_revisions"):
                    self.assertNotIn(f'"{table}"', source)


# ---------------------------------------------------------------------------
# 15. Dispatcher CLI
# ---------------------------------------------------------------------------
class TestDispatcherCli(unittest.TestCase):
    def test_all_three_jobs_are_dispatchable(self):
        from apex.ops.__main__ import JOB_NAMES

        self.assertEqual(
            sorted(JOB_NAMES), ["capture-market-bars", "capture-shadow", "evaluate-outcomes"]
        )
        self.assertEqual(sorted(JOB_NAMES.values()), sorted(ops.JOB_KEYS))

    def test_dispatcher_returns_the_job_exit_code(self):
        import apex.ops.__main__ as entry

        with mock.patch.object(
            entry, "execute",
            return_value=ops_runner.JobResult(
                job_key="capture_shadow", run_id="r", exit_code=ops.ExitCode.NON_DURABLE
            ),
        ):
            with redirect_stdout(io.StringIO()):
                code = ops_main(["run", "capture-shadow", "--no-secret-bootstrap"])
        self.assertEqual(code, ops.ExitCode.NON_DURABLE)

    def test_an_unknown_job_is_rejected(self):
        with self.assertRaises(SystemExit):
            with redirect_stdout(io.StringIO()):
                ops_main(["run", "not-a-job"])

    def test_structured_output_is_json_lines(self):
        with redirect_stdout(io.StringIO()) as buffer:
            ops_runner.execute(
                ops.JOB_CAPTURE_MARKET_BARS, dry_run=True, settings=CONFIGURED
            )
        for line in buffer.getvalue().strip().splitlines():
            parsed = json.loads(line)
            self.assertIn("timestamp_utc", parsed)
            self.assertIn("event", parsed)


if __name__ == "__main__":
    unittest.main()
