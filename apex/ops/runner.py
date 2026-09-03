"""H8 -- job orchestration for unattended execution.

THIS MODULE ORCHESTRATES. IT DOES NOT COMPUTE.
----------------------------------------------
Every analytical decision -- what a shadow observation contains, which bars are
closed, which outcomes have matured, what identity a row gets, what may be
admitted point-in-time -- stays exactly where it already lives. This module
decides only: may this run proceed, did it reach durable storage, what should the
exit code be, and what should be recorded about it.

That boundary is why H8 cannot drift from B2 semantics. A change to capture
behaviour lands in the bridges and is picked up here for free.

HOW EACH JOB REACHES ITS EXISTING IMPLEMENTATION
------------------------------------------------
*   **Job A** calls ``apex.b2_bridge.run_shadow_observation`` directly. There is
    no script wrapper for shadow capture -- capture has only ever run inside the
    production daemon -- so this module is a legitimate new importer of that
    bridge, and the importer guard in ``tests/test_b2_bridge.py`` is updated to
    name exactly this file.

*   **Jobs B and C** load the two EXISTING approved runner scripts and call the
    report functions they already expose. This module does not import
    ``apex.b2_validation_bridge`` at all: ``scripts/capture_daily_bars.py`` and
    ``scripts/validate_matured_observations.py`` remain its only importers, and
    their allowlist is unchanged. Report shaping, durability reporting and
    dry-run defaults stay owned by the scripts, so there is exactly one
    definition of each and no chance of the two disagreeing.

DRY RUN WRITES NOTHING. ANYWHERE.
---------------------------------
A dry run validates configuration, resolves the logical bucket and reports what
would happen. It does not capture, does not persist evidence, does not take the
lease and does not write a heartbeat. The safer default was chosen deliberately:
a dry run that wrote health rows would make "did this job really run?" ambiguous
in exactly the table an operator consults to answer it.
"""
from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from . import (
    JOB_CAPTURE_MARKET_BARS,
    JOB_CAPTURE_SHADOW,
    JOB_EVALUATE_OUTCOMES,
    STATUS_BY_EXIT,
    ExitCode,
)
from .config import (
    DURABILITY_REQUIRED,
    ENV_FRED_KEY,
    OpsSettings,
    code_version,
    ops_settings,
    project_root,
)
from .heartbeat import HealthStore, JobHealth
from .lease import LEASE_CAPTURE_SHADOW, LeaseStore, new_owner_token
from .logging import emit, error_class, error_summary, render_run_record, utcnow

#: Default window for scheduled outcome evaluation. The tactical horizon is
#: fourteen days, so thirty covers full maturity plus generous slack for missed
#: runs. Re-evaluating an already-final outcome is free: its natural key
#: collides and the insert is ignored.
DEFAULT_LOOKBACK_DAYS = 30


@dataclass
class JobResult:
    """What one orchestrated job produced. ``exit_code`` is the contract."""

    job_key: str
    run_id: str
    exit_code: int = ExitCode.SUCCESS
    durable: bool = False
    records_written: int = 0
    logical_bucket: str = ""
    error_class: str = ""
    error_summary: str = ""
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def status(self) -> str:
        return STATUS_BY_EXIT.get(self.exit_code, "unknown")

    @property
    def ok(self) -> bool:
        return self.exit_code == ExitCode.SUCCESS


def _load_script(name: str) -> Any:
    """Load one of the two approved runner scripts by path.

    Loaded rather than imported because ``scripts/`` is not a package, and
    because reaching the validation bridge THROUGH its approved runner is the
    point: those scripts stay the only importers of that bridge, and they keep
    ownership of report shaping and dry-run defaults.
    """
    path = project_root() / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"apex_ops_script_{name}", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load approved runner script: {name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _preflight(
    settings: OpsSettings, *, required: tuple[str, ...]
) -> tuple[str, ...]:
    """Configuration names missing for this job. Returns NAMES, never values."""
    return settings.missing(required)


def _shadow_bucket(moment: datetime) -> str:
    """The UTC hour bucket label for a shadow run.

    Presentation only -- the AUTHORITATIVE bucket identity is computed inside
    ``apex.b2_bridge.observation_key`` from the same instant. This must never
    become a second definition of that identity.
    """
    return moment.strftime("%Y-%m-%dT%H")


# ---------------------------------------------------------------------------
# JOB A -- shadow capture
# ---------------------------------------------------------------------------
def run_capture_shadow(
    *,
    settings: OpsSettings | None = None,
    dry_run: bool = False,
    health_store: HealthStore | None = None,
    lease_store: LeaseStore | None = None,
    now: datetime | None = None,
) -> JobResult:
    """Capture the CURRENT hour's Tactical and Execution shadow observations.

    There is deliberately no way to ask for a past bucket. A shadow evaluation
    is built from live production values -- news, composites, tactical moves --
    that cannot be reconstructed for an earlier hour, so backdating
    ``evaluated_at`` onto a present-day snapshot would fabricate a prediction
    that was never made. A run delayed past the hour boundary therefore captures
    the CURRENT legitimate bucket, and the missed hour stays a documented gap.
    Point-in-time integrity outranks corpus completeness.
    """
    resolved = settings if settings is not None else ops_settings()
    moment = now or utcnow()
    run_id = new_owner_token()
    result = JobResult(
        job_key=JOB_CAPTURE_SHADOW, run_id=run_id, logical_bucket=_shadow_bucket(moment)
    )

    # --- Layer 1: durable storage must be configured BEFORE any capture. ----
    # Not attempted rather than attempted-and-degraded: a run that cannot reach
    # durable storage would write to an ephemeral local file that disappears at
    # the next redeploy, and that must never look like evidence capture.
    missing = _preflight(resolved, required=DURABILITY_REQUIRED + (ENV_FRED_KEY,))
    if missing:
        result.exit_code = ExitCode.CONFIG_UNAVAILABLE
        result.error_class = "ConfigurationError"
        result.error_summary = "missing required configuration: " + ", ".join(missing)
        result.detail["missing_config"] = list(missing)
        result.detail["capture_attempted"] = False
        return result

    if dry_run:
        result.detail.update(
            {"dry_run": True, "capture_attempted": False, "would_capture_bucket": result.logical_bucket}
        )
        return result

    leases = lease_store if lease_store is not None else LeaseStore(resolved)
    lease = leases.acquire(LEASE_CAPTURE_SHADOW, owner=run_id, now=moment)
    result.detail.update(lease.as_record())
    if not lease.acquired:
        # A live holder is exit 3 and not a failure. An INDETERMINATE result --
        # the lease could not be evaluated at all -- is a real failure: assuming
        # "someone else has it" would silently skip capture forever if the
        # backend were down.
        if lease.indeterminate:
            result.exit_code = ExitCode.JOB_FAILURE
            result.error_class = "LeaseUnavailable"
            result.error_summary = lease.error or "lease state could not be determined"
        else:
            result.exit_code = ExitCode.LEASE_NOT_ACQUIRED
        result.detail["capture_attempted"] = False
        return result

    try:
        from ..b2_bridge import get_shadow_hook_stats, run_shadow_observation

        outcomes = run_shadow_observation(resolved.fred_key, resolved.telegram_channel)
        stats = get_shadow_hook_stats()
        result.detail["outcomes"] = dict(outcomes)
        result.detail["capture_attempted"] = True
        result.records_written = int(stats.get("written", 0))

        # --- Layer 2: did anything land in NON-DURABLE local storage? -------
        # This process is short-lived and freshly started, so the counters begin
        # at zero and any non-zero value belongs to this run alone.
        fallback = int(stats.get("v2_local_fallback", 0))
        result.detail["v2_local_fallback"] = fallback
        result.detail["hook_stats"] = {
            key: int(value) for key, value in stats.items() if value
        }
        if fallback > 0:
            result.exit_code = ExitCode.NON_DURABLE
            result.durable = False
            result.error_class = "NonDurableStorage"
            result.error_summary = (
                "shadow evidence reached local fallback storage only; "
                "it is not clean corpus evidence"
            )
        else:
            result.durable = True
    except Exception as exc:
        result.exit_code = ExitCode.JOB_FAILURE
        result.error_class = error_class(exc)
        result.error_summary = error_summary(exc)
    finally:
        leases.release(LEASE_CAPTURE_SHADOW, owner=run_id, now=utcnow())

    return result


# ---------------------------------------------------------------------------
# JOB B -- market bars
# ---------------------------------------------------------------------------
def run_capture_market_bars(
    *,
    settings: OpsSettings | None = None,
    dry_run: bool = False,
    now: datetime | None = None,
    capture: Callable[..., dict[str, Any]] | None = None,
) -> JobResult:
    """Capture closed daily bars through the existing approved runner."""
    resolved = settings if settings is not None else ops_settings()
    moment = now or utcnow()
    result = JobResult(
        job_key=JOB_CAPTURE_MARKET_BARS,
        run_id=new_owner_token(),
        logical_bucket=moment.strftime("%Y-%m-%d"),
    )

    missing = _preflight(resolved, required=DURABILITY_REQUIRED)
    if missing:
        result.exit_code = ExitCode.CONFIG_UNAVAILABLE
        result.error_class = "ConfigurationError"
        result.error_summary = "missing required configuration: " + ", ".join(missing)
        result.detail["missing_config"] = list(missing)
        result.detail["capture_attempted"] = False
        return result

    if dry_run:
        result.detail.update({"dry_run": True, "capture_attempted": False})
        return result

    try:
        run_capture = capture or _load_script("capture_daily_bars").run_capture
        report = run_capture()
        result.detail["capture_attempted"] = True
        result.records_written = int(report.get("inserted", 0))
        result.detail["report"] = {
            key: report.get(key)
            for key in (
                "backend", "durable", "successful_instruments", "failed_instruments",
                "inserted", "duplicate", "failed_rows", "conflicted",
                "revisions_recorded", "revisions_failed", "error",
            )
        }
        # Durability is REPORTED by the existing runner. Reusing its answer
        # rather than deriving a parallel one keeps a single definition.
        result.durable = bool(report.get("durable"))
        if report.get("error"):
            result.exit_code = ExitCode.JOB_FAILURE
            result.error_class = "CaptureError"
            result.error_summary = str(report.get("error"))
        elif report.get("failed_instruments"):
            result.exit_code = ExitCode.JOB_FAILURE
            result.error_class = "InstrumentCaptureFailure"
            result.error_summary = "instruments failed: " + ", ".join(
                str(i) for i in report.get("failed_instruments") or ()
            )
        elif not result.durable:
            result.exit_code = ExitCode.NON_DURABLE
            result.error_class = "NonDurableStorage"
            result.error_summary = (
                "market observations reached local fallback storage only"
            )
    except Exception as exc:
        result.exit_code = ExitCode.JOB_FAILURE
        result.error_class = error_class(exc)
        result.error_summary = error_summary(exc)

    return result


# ---------------------------------------------------------------------------
# JOB C -- matured outcomes
# ---------------------------------------------------------------------------
def run_evaluate_outcomes(
    *,
    settings: OpsSettings | None = None,
    dry_run: bool = False,
    persist: bool = False,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    now: datetime | None = None,
    validate: Callable[..., dict[str, Any]] | None = None,
) -> JobResult:
    """Evaluate matured TACTICAL outcomes through the existing approved runner.

    The window is explicit and derived from the run instant, so a delayed or
    repeated run evaluates the same claims against the same rule. Anti-look-ahead
    lives in ``path_bars`` -- a pure function of each observation's own
    ``evaluated_at`` and horizon -- so running late can only admit MORE matured
    bars, never future information about an earlier claim.
    """
    resolved = settings if settings is not None else ops_settings()
    moment = now or utcnow()
    result = JobResult(
        job_key=JOB_EVALUATE_OUTCOMES,
        run_id=new_owner_token(),
        logical_bucket=moment.strftime("%Y-%m-%d"),
    )

    missing = _preflight(resolved, required=DURABILITY_REQUIRED)
    if missing:
        result.exit_code = ExitCode.CONFIG_UNAVAILABLE
        result.error_class = "ConfigurationError"
        result.error_summary = "missing required configuration: " + ", ".join(missing)
        result.detail["missing_config"] = list(missing)
        result.detail["evaluation_attempted"] = False
        return result

    start = moment - timedelta(days=int(lookback_days))
    result.detail["window"] = {
        "start": start.isoformat(), "as_of": moment.isoformat(),
        "lookback_days": int(lookback_days),
    }

    if dry_run:
        # The script is dry-run by default; ``persist`` is forced off here as
        # well so a dry run cannot write even if a caller passed --persist.
        result.detail.update({"dry_run": True, "evaluation_attempted": False})
        return result

    try:
        run_validation = validate or _load_script(
            "validate_matured_observations"
        ).run_validation
        report = run_validation(
            start=start, as_of=moment, end=None, instrument=None, persist=bool(persist)
        )
        result.detail["evaluation_attempted"] = True
        result.records_written = int(report.get("rows_written", 0))
        result.detail["report"] = {
            key: report.get(key)
            for key in (
                "eligible_outcomes", "final_outcomes", "provisional_outcomes",
                "rows_written", "rows_already_known", "rows_failed",
                "rows_conflicted", "outcome_backend", "persistence_error",
            )
        }
        backend = str(report.get("outcome_backend") or "none")
        # "none" is the honest backend when nothing was eligible to persist --
        # a normal, successful result, not a durability failure.
        wrote_anything = bool(persist) and result.records_written > 0
        result.durable = (backend == "supabase") or not wrote_anything

        if report.get("persistence_error"):
            result.exit_code = ExitCode.JOB_FAILURE
            result.error_class = "PersistenceError"
            result.error_summary = str(report.get("persistence_error"))
        elif report.get("rows_conflicted"):
            # One job, one evidence set, two different verdicts. A determinism
            # defect, never a market event, and never silently tolerated.
            result.exit_code = ExitCode.JOB_FAILURE
            result.error_class = "DeterminismConflict"
            result.error_summary = "conflicting outcome rows: " + ", ".join(
                str(x) for x in report.get("rows_conflicted") or ()
            )
        elif wrote_anything and backend != "supabase":
            result.exit_code = ExitCode.NON_DURABLE
            result.error_class = "NonDurableStorage"
            result.error_summary = "outcomes reached local fallback storage only"
    except Exception as exc:
        result.exit_code = ExitCode.JOB_FAILURE
        result.error_class = error_class(exc)
        result.error_summary = error_summary(exc)

    return result


# ---------------------------------------------------------------------------
# Heartbeat + dispatch
# ---------------------------------------------------------------------------
def record_health(
    result: JobResult,
    *,
    store: HealthStore | None = None,
    settings: OpsSettings | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Persist one job's health. NEVER changes the evidence result.

    A heartbeat failure is an operational failure and is reported as one -- it
    is emitted as its own structured line and returned to the caller -- but it
    does not alter ``result.exit_code``. Letting observability turn a successful
    capture into a failure would make the health layer a new way to lose
    evidence, which is the opposite of its purpose.
    """
    resolved = settings if settings is not None else ops_settings()
    health_store = store if store is not None else HealthStore(resolved)
    health = JobHealth(
        job_key=result.job_key,
        run_id=result.run_id,
        status=result.status,
        logical_bucket=result.logical_bucket,
        records_written=result.records_written,
        durable=result.durable,
        error_class=result.error_class,
        error_summary=result.error_summary,
        code_version=code_version(),
    )
    outcome = health_store.write(health, now=now)
    if not outcome.ok:
        emit(
            "heartbeat_write_failed",
            job_key=result.job_key,
            run_id=result.run_id,
            backend=outcome.backend,
            error_summary=outcome.error,
            evidence_result_preserved=True,
        )
    return outcome.as_record()


JOB_DISPATCH: dict[str, Callable[..., JobResult]] = {
    JOB_CAPTURE_SHADOW: run_capture_shadow,
    JOB_CAPTURE_MARKET_BARS: run_capture_market_bars,
    JOB_EVALUATE_OUTCOMES: run_evaluate_outcomes,
}


def execute(
    job_key: str,
    *,
    dry_run: bool = False,
    settings: OpsSettings | None = None,
    write_health: bool = True,
    **kwargs: Any,
) -> JobResult:
    """Run one job, emit its structured record, and persist its health."""
    resolved = settings if settings is not None else ops_settings()
    job = JOB_DISPATCH[job_key]
    emit("job_started", job_key=job_key, dry_run=bool(dry_run), code_version=code_version())

    result = job(settings=resolved, dry_run=dry_run, **kwargs)

    # A dry run writes NOTHING -- no evidence and no health. See module docstring.
    health_record: dict[str, Any] | None = None
    if write_health and not dry_run:
        health_record = record_health(result, settings=resolved)

    emit(
        "job_finished",
        **render_run_record(
            job_key=result.job_key,
            run_id=result.run_id,
            status=result.status,
            exit_code=result.exit_code,
            logical_bucket=result.logical_bucket,
            records_written=result.records_written,
            durable=result.durable,
            error_class_name=result.error_class,
            error_text=result.error_summary,
            code_version=code_version(),
            extra={"heartbeat": health_record} if health_record else None,
        ),
    )
    return result


def check_health(
    *, settings: OpsSettings | None = None, store: HealthStore | None = None
) -> tuple[int, dict[str, Any]]:
    """Read durable health for every job. Read-only; writes nothing."""
    resolved = settings if settings is not None else ops_settings()
    health_store = store if store is not None else HealthStore(resolved)
    if not health_store.available:
        report = {
            "ok": False,
            "backend": "unconfigured",
            "config": resolved.describe(),
            "jobs": [],
        }
        emit("health_unavailable", **report)
        return ExitCode.CONFIG_UNAVAILABLE, report

    outcome = health_store.read()
    report = {
        "ok": outcome.ok,
        "backend": outcome.backend,
        "config": resolved.describe(),
        "jobs": [dict(row) for row in outcome.rows],
        "error": outcome.error,
    }
    emit("health_report", **report)
    return (ExitCode.SUCCESS if outcome.ok else ExitCode.JOB_FAILURE), report


__all__ = [
    "DEFAULT_LOOKBACK_DAYS",
    "JOB_DISPATCH",
    "JobResult",
    "check_health",
    "execute",
    "record_health",
    "run_capture_market_bars",
    "run_capture_shadow",
    "run_evaluate_outcomes",
]
