"""Named operator/research runner for Stage D-5 forward outcome persistence.

**Explicit and independently invocable.** Run it by hand:

    python scripts/validate_matured_observations.py --start 2026-08-30
    python scripts/validate_matured_observations.py --start 2026-08-30 --instrument Gold
    python scripts/validate_matured_observations.py --start 2026-08-30 --persist
    python scripts/validate_matured_observations.py --start 2026-08-30 --json

Nothing imports this module and nothing schedules it. It is not part of the
production daemon, the Streamlit render cycle, the Telegram loop, the AI loop,
Smart Shift, Tactical Move, Macro Entry Zone, or any production decision path.
With ``scripts/capture_daily_bars.py`` it is one of exactly TWO approved
importers of ``apex.b2_validation_bridge`` outside the test suite -- see
``tests/test_b2_bridge.py`` for the architectural guard that enforces this and
fails if a third importer ever appears.

**DRY RUN BY DEFAULT.** Without ``--persist`` nothing is written anywhere: the
maturity assessment runs, the persistence gate runs, and the census is printed
so an operator can see exactly what WOULD be recorded. Persisting requires
saying so.

This runner performs no fetch, evaluation, maturity, resolution, cohort or
storage logic of its own. It calls ``validate_stored_range`` -- the same
function Stage D-2E's tests exercise -- and only formats what that call already
reported. That keeps point-in-time admission, the tactical-only gate, outcome
identity and append-only persistence entirely inside the modules that own them,
so this runner cannot drift from, weaken, or duplicate D-5 semantics.

It never fabricates success. **Zero eligible outcomes is a normal, successful
result** -- most of the time it simply means nothing has matured yet -- and is
reported as such rather than as an error.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from apex.b2_validation_bridge import validate_stored_range


def _utc(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _parse_moment(raw: str) -> datetime:
    """Parse an ISO date or datetime as UTC. Never guesses a local zone."""
    text = str(raw).strip().replace("Z", "+00:00")
    return _utc(datetime.fromisoformat(text))


def run_validation(
    *,
    start: datetime,
    as_of: datetime,
    end: datetime | None = None,
    instrument: str | None = None,
    persist: bool = False,
    record_store=None,
    market_store=None,
    outcome_store=None,
) -> dict:
    """Validate stored observations and return an operator-facing report.

    The three ``*_store`` arguments exist for tests to inject fakes; a real
    invocation leaves all three ``None`` so the bridge resolves the real
    backends. ``as_of`` is always explicit -- it is both the reproducibility
    instant and the capture bound (R4), and a runner that read a wall clock
    would make every run unreproducible by construction.
    """
    result = validate_stored_range(
        start=start,
        as_of=as_of,
        end=end,
        instrument=instrument,
        record_store=record_store,
        market_store=market_store,
        outcome_store=outcome_store,
        persist=bool(persist),
    )

    status = str(result.get("status") or "")
    evaluated = tuple(result.get("evaluated") or ())
    persistence = dict(result.get("persistence") or {})
    census = dict(persistence.get("gate_census") or {})

    # Maturity states across every evaluated (non-defect) observation. Read
    # from each envelope's own context rather than recomputed, so this report
    # can never disagree with what the evaluation actually decided.
    maturity_states: dict[str, int] = {}
    for item in evaluated:
        if getattr(item, "is_defect", True):
            continue
        context = getattr(getattr(item, "envelope", None), "context", None)
        state = str(getattr(context, "maturity_state", "") or "unknown")
        maturity_states[state] = maturity_states.get(state, 0) + 1

    return {
        "status": status,
        "as_of": _utc(as_of).isoformat(),
        "range_start": _utc(start).isoformat(),
        "range_end": _utc(end).isoformat() if end is not None else _utc(as_of).isoformat(),
        "instrument": instrument or "(all)",
        "persist_requested": bool(persist),
        "shadow_rows": int(result.get("shadow_rows") or 0),
        "observations_considered": len(evaluated),
        "bar_rows": int(result.get("bar_rows") or 0),
        "malformed_rows": int(result.get("malformed_rows") or 0),
        "symbols": list(result.get("symbols") or ()),
        "maturity_states": maturity_states,
        # The gate census. Every considered observation lands in exactly one
        # bucket, so these totals reconcile against observations_considered.
        "gate_census": census,
        "not_matured": int(census.get("withheld_not_matured", 0)),
        "awaiting_bars": int(census.get("withheld_no_verdict_permitted", 0)),
        "withheld_execution": int(census.get("withheld_execution_granularity", 0)),
        "withheld_horizon": int(census.get("withheld_horizon_not_activated", 0)),
        "lineage_defects": int(census.get("withheld_lineage_defect", 0)),
        "eligible_outcomes": int(persistence.get("outcomes_eligible", 0)),
        "final_outcomes": int(persistence.get("outcomes_final", 0)),
        "provisional_outcomes": int(persistence.get("outcomes_provisional", 0)),
        "rows_written": int(persistence.get("outcomes_written", 0)),
        "rows_already_known": int(persistence.get("outcomes_duplicate", 0)),
        "rows_conflicted": list(persistence.get("outcomes_conflicted") or ()),
        "rows_failed": int(persistence.get("outcomes_failed", 0)),
        "outcome_backend": str(persistence.get("outcome_backend") or "none"),
        "persistence_error": str(persistence.get("persistence_error") or ""),
    }


def format_report(report: dict) -> str:
    mode = "PERSIST" if report.get("persist_requested") else "DRY RUN (nothing written)"
    lines = [
        f"Stage D-5 forward outcome validation -- as_of {report['as_of']}  [{mode}]",
        f"Status: {report['status']}",
        f"Range: {report['range_start']} .. {report['range_end']}  instrument={report['instrument']}",
        f"Shadow rows: {report['shadow_rows']}  observations considered: {report['observations_considered']}",
        f"Bar rows: {report['bar_rows']}  malformed: {report['malformed_rows']}  symbols: {', '.join(report['symbols']) or '(none)'}",
        "Maturity: "
        + (
            "  ".join(f"{k}={v}" for k, v in sorted(report["maturity_states"].items()))
            or "(none)"
        ),
        (
            f"Withheld -- not matured: {report['not_matured']}  "
            f"awaiting bars: {report['awaiting_bars']}  "
            f"execution (granularity): {report['withheld_execution']}  "
            f"horizon not activated: {report['withheld_horizon']}  "
            f"lineage defects: {report['lineage_defects']}"
        ),
        (
            f"Eligible tactical outcomes: {report['eligible_outcomes']}  "
            f"(final: {report['final_outcomes']}  provisional: {report['provisional_outcomes']})"
        ),
    ]
    if report.get("persist_requested"):
        lines.append(
            f"Written: {report['rows_written']}  already known: {report['rows_already_known']}  "
            f"failed: {report['rows_failed']}  backend: {report['outcome_backend']}"
        )
    else:
        lines.append(
            "Nothing was written. Re-run with --persist to record the eligible outcomes above."
        )
    if report["eligible_outcomes"] == 0:
        lines.append(
            "No eligible outcomes. This is a NORMAL result -- most often it means "
            "no forward window has matured yet, which is not a failure."
        )
    if report["rows_conflicted"]:
        lines.append(
            "CONFLICTED -- one job over one set of evidence produced two different "
            f"verdicts. This is a determinism defect, not a market event: {report['rows_conflicted']}"
        )
    if report["persistence_error"]:
        lines.append(
            "Persistence error (the validation above is unaffected): "
            f"{report['persistence_error']}"
        )
    return "\n".join(lines)


#: Exit codes, matching the H8 contract in ``apex/ops/__init__.py``. Declared
#: here as plain integers rather than imported, so this script keeps working
#: standalone for a human with no dependency on the operational package.
EXIT_SUCCESS = 0
EXIT_JOB_FAILURE = 1
EXIT_NON_DURABLE = 4


def exit_code_for(report: dict) -> int:
    """Map a validation report onto the H8 exit-code contract.

    This runner previously always returned 0, including on a persistence error
    and on a determinism conflict. Under a scheduler that made a real defect
    indistinguishable from a clean run.

    **Zero eligible outcomes stays a SUCCESS.** Most of the time it simply means
    no forward window has matured yet, which the script's own report already
    says in words. Treating it as a failure would alert continuously until the
    first tactical maturity and train an operator to ignore the signal.

    A determinism conflict is a FAILURE, not a market event: one job over one
    set of evidence reached two different verdicts, and that must never pass
    quietly.
    """
    if report.get("persistence_error"):
        return EXIT_JOB_FAILURE
    if report.get("rows_conflicted"):
        return EXIT_JOB_FAILURE
    if int(report.get("rows_failed", 0) or 0) > 0:
        return EXIT_JOB_FAILURE
    # Durability only matters when this run actually tried to write something.
    # A dry run, or a run with nothing eligible, has no evidence to lose.
    if report.get("persist_requested") and int(report.get("rows_written", 0) or 0) > 0:
        if str(report.get("outcome_backend") or "") != "supabase":
            return EXIT_NON_DURABLE
    return EXIT_SUCCESS


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="validate_matured_observations",
        description=(
            "Operator-invoked Stage D-5 validation of matured TACTICAL shadow "
            "observations against captured daily bars. Dry run by default. "
            "Not scheduled and not reachable from any production path."
        ),
    )
    parser.add_argument(
        "--start",
        required=True,
        metavar="ISO",
        help="Earliest shadow observation evaluated_at to consider (ISO date or datetime, UTC).",
    )
    parser.add_argument(
        "--end",
        metavar="ISO",
        help="Latest observation to consider. Defaults to --as-of, and is clamped to it.",
    )
    parser.add_argument(
        "--as-of",
        dest="as_of",
        metavar="ISO",
        help=(
            "The instant this run speaks for. Bounds both the observations read "
            "and the capture time of admitted bars (rule R4). Defaults to now."
        ),
    )
    parser.add_argument(
        "--instrument",
        metavar="NAME",
        help="Restrict to one instrument. Default: every instrument.",
    )
    parser.add_argument(
        "--persist",
        action="store_true",
        help=(
            "Actually record gate-approved tactical outcomes. Without this flag "
            "the run is a dry run and writes nothing."
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the machine-readable report as JSON instead of a summary.",
    )
    args = parser.parse_args(argv)

    as_of = _parse_moment(args.as_of) if args.as_of else datetime.now(timezone.utc)
    report = run_validation(
        start=_parse_moment(args.start),
        as_of=as_of,
        end=_parse_moment(args.end) if args.end else None,
        instrument=args.instrument,
        persist=args.persist,
    )

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(format_report(report))

    return exit_code_for(report)


if __name__ == "__main__":
    sys.exit(main())
