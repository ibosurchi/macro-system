"""H8 -- the unattended dispatcher entry point.

    python -m apex.ops run capture-shadow      [--dry-run]
    python -m apex.ops run capture-market-bars [--dry-run]
    python -m apex.ops run evaluate-outcomes   [--dry-run] [--persist] [--lookback-days N]
    python -m apex.ops check health            [--json]

ONE DISPATCHER, NOT FOUR SCRIPTS
--------------------------------
The jobs share configuration resolution, secret bootstrap, lease handling,
heartbeat writing, structured logging and exit-code mapping. Four separate
scripts would either duplicate all of that or grow a shared module anyway, at
which point the dispatcher is the honest shape. It also means the architectural
importer guard has exactly ONE new name to allow rather than several.

NOTE ON JOB A's MISSING FLAGS
-----------------------------
There is deliberately no ``--bucket``, ``--at``, ``--backfill`` or ``--since``
for shadow capture. Those would all be ways to backdate ``evaluated_at``, and a
shadow evaluation cannot be honestly reconstructed for a past hour: it is built
from live production values that no longer exist. The absence of the option is
the safeguard.
"""
from __future__ import annotations

import argparse
import json
import sys

from . import (
    JOB_CAPTURE_MARKET_BARS,
    JOB_CAPTURE_SHADOW,
    JOB_EVALUATE_OUTCOMES,
    ExitCode,
)
from .config import materialize_streamlit_secrets
from .logging import emit
from .runner import DEFAULT_LOOKBACK_DAYS, check_health, execute

#: CLI job name -> internal job key. The CLI uses hyphens because that is the
#: convention a scheduler command line reads best; the job key uses underscores
#: because it is a database primary key.
JOB_NAMES: dict[str, str] = {
    "capture-shadow": JOB_CAPTURE_SHADOW,
    "capture-market-bars": JOB_CAPTURE_MARKET_BARS,
    "evaluate-outcomes": JOB_EVALUATE_OUTCOMES,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="apex.ops",
        description=(
            "ApexMacro B2 operational dispatcher. Orchestrates existing B2 "
            "capture and validation logic for unattended execution. Performs no "
            "analysis of its own and executes no schema statements."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="Run one operational job.")
    run.add_argument("job", choices=sorted(JOB_NAMES), help="Which job to run.")
    run.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Validate configuration and report what would happen. Writes "
            "nothing: no evidence, no heartbeat, no lease."
        ),
    )
    run.add_argument(
        "--persist",
        action="store_true",
        help=(
            "evaluate-outcomes only: actually record gate-approved tactical "
            "outcomes. Ignored under --dry-run."
        ),
    )
    run.add_argument(
        "--lookback-days",
        type=int,
        default=DEFAULT_LOOKBACK_DAYS,
        metavar="N",
        help=(
            "evaluate-outcomes only: how far back to consider observations. "
            f"Default {DEFAULT_LOOKBACK_DAYS}."
        ),
    )
    run.add_argument(
        "--no-secret-bootstrap",
        action="store_true",
        help=(
            "Do not materialise .streamlit/secrets.toml from the environment. "
            "Use when the file is already present and authoritative."
        ),
    )

    check = sub.add_parser("check", help="Read durable operational state.")
    check.add_argument("target", choices=["health"], help="What to check.")
    check.add_argument(
        "--json", action="store_true", help="Print the report as indented JSON."
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "check":
        exit_code, report = check_health()
        if getattr(args, "json", False):
            sys.stdout.write(json.dumps(report, indent=2, sort_keys=True, default=str) + "\n")
        return exit_code

    job_key = JOB_NAMES[args.job]

    # Materialise the secrets file the existing runtime expects BEFORE any job
    # runs, so the deferred bridge imports inside the job find their
    # configuration. Names only are ever reported; values never leave this call.
    if not args.no_secret_bootstrap:
        _, written = materialize_streamlit_secrets()
        if written:
            emit("secret_bootstrap", secret_names_written=sorted(written))

    kwargs: dict[str, object] = {}
    if job_key == JOB_EVALUATE_OUTCOMES:
        kwargs["persist"] = bool(args.persist)
        kwargs["lookback_days"] = int(args.lookback_days)

    try:
        result = execute(job_key, dry_run=bool(args.dry_run), **kwargs)
    except Exception as exc:  # pragma: no cover - defensive top-level guard
        # Nothing may escape the dispatcher unrecorded: an unhandled traceback
        # printed to a scheduler log is exactly the unredacted leak this layer
        # exists to prevent.
        from .logging import emit_error

        emit_error("dispatcher_failed", exc, job_key=job_key)
        return ExitCode.JOB_FAILURE

    return result.exit_code


if __name__ == "__main__":
    sys.exit(main())
