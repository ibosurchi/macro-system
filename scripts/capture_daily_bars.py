"""Named operator/research runner for Stage D-1 daily bar capture.

**Explicit and independently invocable.** Run it by hand:

    python scripts/capture_daily_bars.py
    python scripts/capture_daily_bars.py --instrument Gold --instrument Oil
    python scripts/capture_daily_bars.py --json

Nothing imports this module and nothing schedules it. It is not part of the
production daemon, the Streamlit render cycle, the Telegram loop, the AI loop,
Smart Shift, Tactical Move, Macro Entry Zone, or any production decision path.
It is the ONLY approved importer of ``apex.b2_validation_bridge`` outside the
test suite -- see ``tests/test_b2_bridge.py`` for the architectural guard that
enforces this and fails if a second importer ever appears.

This runner performs no fetch or storage logic of its own. It calls
``apex.b2_validation_bridge.capture_daily_bars`` -- the same function Stage
D-1's tests exercise -- and only formats what that call already reported.
That keeps append-only inserts, closed-bar filtering, symbol/fallback
resolution and deduplication entirely inside the one module that owns them,
so this runner cannot drift from, weaken, or duplicate Stage D-1 semantics.
It never fabricates success: an instrument that produced no bars is reported
as such, not silently dropped.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from apex.b2_validation_bridge import (
    capture_daily_bars,
    registered_instruments,
    symbol_convention,
)


def run_capture(instruments=None, *, store=None, revision_store=None, now=None) -> dict:
    """Capture closed daily bars and return an operator-facing report.

    ``store``, ``revision_store`` and ``now`` exist for tests to inject a fake
    table, a fake revision log and a fixed clock; a real invocation leaves all
    three ``None`` so ``capture_daily_bars`` resolves the real backends
    (Supabase if configured, else the local append-only mirrors) and uses the
    real wall clock.
    """
    requested = tuple(instruments) if instruments is not None else registered_instruments()
    result = capture_daily_bars(
        requested, store=store, revision_store=revision_store, now=now
    )

    symbols_used = dict(result["symbols"])
    pinned = dict(result.get("pinned") or {})
    fallback_used = {}
    for instrument, symbol in symbols_used.items():
        if instrument in pinned:
            # A pinned instrument has no fallback to have used. Reporting its
            # pinned symbol as a "fallback" because it differs from production's
            # primary would describe a switch that cannot happen.
            fallback_used[instrument] = False
            continue
        convention = symbol_convention(instrument)
        fallback_used[instrument] = bool(convention is not None and symbol != convention.symbol)

    # Every fetched row lands in exactly one of these buckets (see
    # capture_daily_bars / *MarketObservationStore.insert_rows), so their sum
    # is the true count of closed bars this run pulled from the network --
    # capture_daily_bars does not return that count directly.
    closed_bars_fetched = (
        result["inserted"] + result["duplicate"] + len(result["conflicted"]) + result["failed"]
    )

    successful = sorted(
        instrument for instrument, status in result["instruments"].items() if status == "fetched"
    )
    failed = sorted(
        instrument for instrument, status in result["instruments"].items() if status != "fetched"
    )

    return {
        "captured_at": result["captured_at"],
        "requested_instruments": list(requested),
        "successful_instruments": successful,
        "failed_instruments": failed,
        "instrument_status": dict(result["instruments"]),
        "closed_bars_fetched": closed_bars_fetched,
        "inserted": result["inserted"],
        "duplicate": result["duplicate"],
        "conflicted": list(result["conflicted"]),
        "failed_rows": result["failed"],
        "symbols_used": symbols_used,
        "fallback_used": fallback_used,
        "pinned_series": pinned,
        "series_pin_version": result.get("series_pin_version", ""),
        "backend": result["backend"],
        "durable": result["durable"],
        "error": result.get("error", ""),
        # Reported alongside, never folded into, the capture counts above. A
        # revision that failed to record does not make a stored bar less stored.
        "revision_backend": result.get("revision_backend", "none"),
        "revisions_recorded": result.get("revisions_recorded", 0),
        "revisions_duplicate": result.get("revisions_duplicate", 0),
        "revisions_failed": result.get("revisions_failed", 0),
        "revisions_skipped": result.get("revisions_skipped", 0),
        "revisions_by_kind": dict(result.get("revisions_by_kind") or {}),
        "revisions_error": result.get("revisions_error", ""),
    }


def format_report(report: dict) -> str:
    lines = [
        f"Stage D-1 daily bar capture -- {report['captured_at']}",
        f"Backend: {report['backend']} (durable={report['durable']})",
        f"Requested instruments: {', '.join(report['requested_instruments']) or '(none)'}",
        f"Successful: {', '.join(report['successful_instruments']) or '(none)'}",
        f"Failed/unavailable: {', '.join(report['failed_instruments']) or '(none)'}",
        f"Closed bars fetched: {report['closed_bars_fetched']}",
        (
            f"Inserted: {report['inserted']}  Duplicate: {report['duplicate']}  "
            f"Conflicted: {len(report['conflicted'])}  Failed rows: {report['failed_rows']}"
        ),
    ]
    for instrument in report["requested_instruments"]:
        status = report["instrument_status"].get(instrument, "unknown")
        symbol = report["symbols_used"].get(instrument, "-")
        if instrument in (report.get("pinned_series") or {}):
            note = " (pinned series)"
        elif report["fallback_used"].get(instrument):
            note = " (fallback symbol)"
        else:
            note = ""
        lines.append(f"  {instrument}: {status} symbol={symbol}{note}")
    if report["conflicted"]:
        lines.append(f"CONFLICTED -- two payloads claim one bar, not auto-resolved: {report['conflicted']}")
        by_kind = report.get("revisions_by_kind") or {}
        kinds = ", ".join(f"{kind}={count}" for kind, count in sorted(by_kind.items())) or "(none)"
        lines.append(
            f"Revisions -- recorded: {report.get('revisions_recorded', 0)}  "
            f"already known: {report.get('revisions_duplicate', 0)}  "
            f"skipped: {report.get('revisions_skipped', 0)}  "
            f"failed: {report.get('revisions_failed', 0)}  "
            f"backend: {report.get('revision_backend', 'none')}  kinds: {kinds}"
        )
    if report.get("revisions_error"):
        lines.append(
            "Revision log error (the capture above is unaffected): "
            f"{report['revisions_error']}"
        )
    if report["error"]:
        lines.append(f"Error: {report['error']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="capture_daily_bars",
        description=(
            "Operator-invoked Stage D-1 capture of closed daily market bars. "
            "Not scheduled and not reachable from any production path."
        ),
    )
    parser.add_argument(
        "--instrument",
        action="append",
        dest="instruments",
        metavar="NAME",
        help="Instrument to capture (repeatable). Default: every registered instrument.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the machine-readable report as JSON instead of a summary.",
    )
    args = parser.parse_args(argv)

    report = run_capture(args.instruments)

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(format_report(report))

    return 0


if __name__ == "__main__":
    sys.exit(main())
