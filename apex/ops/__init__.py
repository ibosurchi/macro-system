"""H8 -- durable execution layer for B2 operational jobs.

WHY THIS PACKAGE EXISTS
-----------------------
B2 evidence capture previously ran inside a ``daemon=True`` thread owned by the
Streamlit server process. That thread dies with the process -- on redeploy, on
restart, and on Streamlit Community Cloud hibernation -- and its only health
signal lived in a module-level dict that died with it. Correct analytical code
therefore did not prove continuous evidence capture.

This package is the external half: small, orchestration-only entry points that
an outside scheduler can invoke, that write durable health state, and that
report meaningful exit codes.

THREE RULES THIS PACKAGE KEEPS
------------------------------
1.  **It orchestrates; it does not compute.** Every analytical decision stays in
    ``apex.b2`` / ``apex.b2_bridge`` / ``apex.b2_validation_bridge``. Nothing
    here re-implements capture, evaluation, storage identity or point-in-time
    admission. A bug fixed in a bridge is fixed here for free, and this layer
    cannot drift away from the semantics it invokes.

2.  **The support layers import no Streamlit.** ``config``, ``logging``,
    ``heartbeat`` and ``lease`` read configuration from the environment or from
    a TOML file using the standard library, so they run in a bare process with
    no Streamlit runtime, session or server. Only ``runner`` reaches the
    existing bridges -- and it does so through deferred, function-local imports
    -- because those legitimately depend on the production module.

3.  **It executes no DDL.** ``sql/003_b2_ops_job_health.sql`` is run by hand by
    an operator, exactly like every other file in ``sql/``. The guard in
    ``tests/test_b2_stage_d_storage.py`` covers this package for that reason.
"""
from __future__ import annotations

#: Bumped when the MEANING of an operational outcome changes -- a new exit code,
#: a changed durability rule, a changed job boundary. Recorded on every
#: heartbeat row so a stored health record stays interpretable later.
OPS_VERSION = "b2-ops-h8-v1"

#: Job keys. These are the primary keys of ``b2_ops_job_health`` and the values
#: an operator queries by, so they are declared once here rather than spelled as
#: literals at each call site.
JOB_CAPTURE_SHADOW = "capture_shadow"
JOB_CAPTURE_MARKET_BARS = "capture_market_bars"
JOB_EVALUATE_OUTCOMES = "evaluate_outcomes"

JOB_KEYS: tuple[str, ...] = (
    JOB_CAPTURE_SHADOW,
    JOB_CAPTURE_MARKET_BARS,
    JOB_EVALUATE_OUTCOMES,
)


class ExitCode:
    """The H8 exit-code contract. Every runner path maps to exactly one.

    A scheduler's only channel back to an operator is the exit code, so each one
    means a genuinely different thing and none of them overlap:

    ``0  SUCCESS``
        The job ran and its evidence reached durable storage.

    ``1  JOB_FAILURE``
        The job ran and failed. Evidence may be partial; the next scheduled run
        retries. This is the ordinary failure.

    ``2  CONFIG_UNAVAILABLE``
        Required configuration or the durable backend is missing, so the job was
        NOT ATTEMPTED. Distinct from 1 because nothing was tried: there is no
        partial state to reason about, and retrying without fixing config cannot
        help.

    ``3  LEASE_NOT_ACQUIRED``
        Another legitimate run owns this job. Not an error and not evidence
        corruption -- but this process must not execute the job. Kept separate
        from 0 so a scheduler cannot read "someone else did it" as "I did it".

    ``4  NON_DURABLE``
        The job completed, but its evidence reached only non-durable local
        storage. On an ephemeral host that evidence disappears at the next
        redeploy, so it must NEVER be reported as clean corpus capture. This is
        the code that exists specifically to stop a silent evidence-loss path
        from being logged as success.
    """

    SUCCESS = 0
    JOB_FAILURE = 1
    CONFIG_UNAVAILABLE = 2
    LEASE_NOT_ACQUIRED = 3
    NON_DURABLE = 4


#: Status strings recorded on the heartbeat, one per terminal exit code.
STATUS_BY_EXIT: dict[int, str] = {
    ExitCode.SUCCESS: "success",
    ExitCode.JOB_FAILURE: "failure",
    ExitCode.CONFIG_UNAVAILABLE: "config_unavailable",
    ExitCode.LEASE_NOT_ACQUIRED: "lease_not_acquired",
    ExitCode.NON_DURABLE: "non_durable",
}

__all__ = [
    "OPS_VERSION",
    "JOB_CAPTURE_SHADOW",
    "JOB_CAPTURE_MARKET_BARS",
    "JOB_EVALUATE_OUTCOMES",
    "JOB_KEYS",
    "ExitCode",
    "STATUS_BY_EXIT",
]
