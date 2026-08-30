"""Regression proof that a full test run never rewrites production state.

The Phase 1 inspection found that running the suite performed a live calendar
fetch and rewrote ``forex_factory_schedule_state.json`` at the repository root.
This module proves that is fixed, end to end: it hashes the durable production
files, runs the entire rest of the suite in a clean subprocess, and asserts the
files come back byte-identical.

It runs the other modules in a subprocess rather than in-process so the check is
honest: an in-process run would already be inside this process's own isolation.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: The durable production files that must never change during a test run.
PROTECTED_FILES = (
    "forecaster_history.json",
    "forecaster_evidence_archive.json",
    "forex_factory_schedule_state.json",
)

#: Every test module except this one, to avoid recursing into itself.
SUITE_MODULES = (
    "tests.test_forecaster_core",
    "tests.test_strategy_layer",
    "tests.test_b2_stage_a",
    "tests.test_b2_stage_b",
    "tests.test_b2_bridge",
)


def _digest(path: str) -> str | None:
    if not os.path.exists(path):
        return None
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


class TestIsolationMechanism(unittest.TestCase):
    def test_paths_are_redirected_away_from_the_repository_root(self):
        from state_isolation import isolate_durable_state, redirected_paths

        tmp = isolate_durable_state()
        redirected = redirected_paths()
        self.assertTrue(redirected, "no state paths were redirected")
        for name, target in redirected.items():
            self.assertTrue(
                os.path.abspath(target).startswith(os.path.abspath(tmp)),
                f"{name} still points outside the temp dir: {target}",
            )

    def test_the_known_state_files_are_all_covered(self):
        from state_isolation import isolate_durable_state, redirected_paths

        isolate_durable_state()
        basenames = {os.path.basename(p) for p in redirected_paths().values()}
        for expected in PROTECTED_FILES:
            self.assertIn(expected, basenames, expected)

    def test_isolation_is_idempotent(self):
        from state_isolation import isolate_durable_state

        self.assertEqual(isolate_durable_state(), isolate_durable_state())

    def test_a_write_through_the_real_api_lands_in_the_temp_directory(self):
        from apex import production_core as core
        from state_isolation import isolate_durable_state

        tmp = isolate_durable_state()
        before = {name: _digest(os.path.join(ROOT, name)) for name in PROTECTED_FILES}

        core._save_persistent_state(
            "forex_factory_schedule_state", core.FF_SCHEDULE_FILE, [{"probe": True}]
        )

        self.assertTrue(os.path.abspath(core.FF_SCHEDULE_FILE).startswith(os.path.abspath(tmp)))
        after = {name: _digest(os.path.join(ROOT, name)) for name in PROTECTED_FILES}
        self.assertEqual(before, after)


class TestFullSuiteLeavesProductionStateUntouched(unittest.TestCase):
    def test_durable_files_are_byte_identical_after_the_whole_suite(self):
        before = {name: _digest(os.path.join(ROOT, name)) for name in PROTECTED_FILES}
        self.assertTrue(
            any(v is not None for v in before.values()),
            "no production state files present to protect",
        )

        completed = subprocess.run(
            [sys.executable, "-m", "unittest", *SUITE_MODULES],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=900,
        )
        self.assertEqual(
            completed.returncode, 0, completed.stderr[-4000:] or completed.stdout[-4000:]
        )

        after = {name: _digest(os.path.join(ROOT, name)) for name in PROTECTED_FILES}
        for name in PROTECTED_FILES:
            self.assertEqual(
                before[name], after[name], f"{name} was modified by the test suite"
            )

    def test_no_b2_state_files_leak_into_the_repository_root(self):
        for name in ("b2_shadow_log_v1.json", "b2_prediction_log_v1.json"):
            self.assertFalse(
                os.path.exists(os.path.join(ROOT, name)),
                f"{name} was written to the repository root by a test run",
            )


if __name__ == "__main__":
    unittest.main()
