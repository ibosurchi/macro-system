"""Stage D -- the I/O half of B2 validation.

This module is the ONLY place that touches ``b2_market_observations``, and the
only place in Stage D that performs I/O at all. Everything under
``apex.b2.validation`` stays pure, exactly as ``apex.b2_bridge`` keeps
``apex.b2`` pure for the live observation path.

**Nothing in the production system calls this module.** No page, score, alert,
scheduler, daemon or Telegram path imports it. The daemon loop is not touched:
capture is operator-invoked and offline, which is what keeps the approved "no
new expensive runtime work" guarantee true rather than merely intended.

What it does
------------
Captures **closed daily bars** for the instruments B2 observes, using the same
Yahoo chart endpoint and the same symbol conventions production already uses,
with ``range=1mo&interval=1d``. Each bar becomes one immutable row keyed to the
market series, shared by every shadow observation whose forward window contains
it.

Three properties are enforced here rather than assumed:

    Append-only.  Inserts use ON CONFLICT DO NOTHING against the
                  observation_id primary key. An existing row is never
                  updated, never re-timestamped, never overwritten.
    Closed only.  An in-progress bar is refused before it is ever sent. Yahoo
                  revises it continuously, and DO NOTHING would then preserve
                  the partial version permanently.
    Lock-free.    This path talks to PostgREST directly and NEVER calls
                  _save_persistent_state, so it cannot hold the global
                  production _PERSISTENCE_LOCK that VIP login, payment
                  verification, Smart Shift state and the Forecaster share.

B2 remains SHADOW / NON-PRODUCTION / UNCALIBRATED. Nothing here calibrates
anything, promotes anything, or feeds any production decision.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Sequence
from urllib.parse import quote

import requests

from . import production_core as core
from .b2_bridge import InsertOutcome, symbol_convention
from .b2.horizons import HORIZON_EVALUATION_WINDOW
from .b2.enums import Horizon
from .b2.modules import registered_instruments
from .b2.validation.anchor import AnchorStatus, classify_anchor
from .b2.validation.bars import (
    BAR_PRICE_SOURCE,
    GRANULARITY_1D,
    MarketBar,
    MarketObservationError,
    bar_is_final,
    canonical_bar_time_iso,
    coverage,
    forward_bars,
    row_to_bar,
)

#: The pre-created append-only table. Overridable only for testing against a
#: scratch table; this application never creates, alters or drops it.
MARKET_OBSERVATIONS_TABLE = (
    core.get_secret("B2_MARKET_OBSERVATIONS_TABLE", "b2_market_observations")
    or "b2_market_observations"
)

#: Own timeout, deliberately not production's REQUEST_TIMEOUT: this is an
#: offline research path and must never share a budget tuned for the live loop.
MARKET_OBS_TIMEOUT = 20

#: Local append-only mirror used when Supabase is not configured (local dev and
#: tests). One JSON object per line, appended, never rewritten.
LOCAL_MARKET_OBSERVATIONS_FILE = str(
    core.PROJECT_ROOT / "b2_market_observations_local.jsonl"
)

#: The approved fetch shape. Same endpoint and same symbols production already
#: uses for the tactical series; only the range and interval differ, because a
#: 14-day tactical window cannot be resolved from a 5-day intraday history.
DAILY_RANGE = "1mo"
DAILY_INTERVAL = "1d"

#: Bars per response below which the payload is treated as unusable rather than
#: partially trusted.
MIN_DAILY_BARS = 5

#: Version stamped into every row's ``meta`` so a later change to this capture
#: path is visible in the data rather than only in repository history.
RESOLVER_VERSION = "b2-validation-resolver-v1"


# ===========================================================================
# STORAGE CLIENTS
# ===========================================================================


class SupabaseMarketObservationStore:
    """Append-only row store over the pre-created ``b2_market_observations``.

    Uses the backend service-role credential already configured for production
    persistence, read-only with respect to production: no production
    persistence function is called and no credential is copied, logged or
    widened. RLS stays enabled -- the service-role key is precisely the
    credential intended to operate under it, and it is only ever used
    server-side.
    """

    def __init__(self, table: str | None = None, timeout: int | None = None) -> None:
        self.table = table or MARKET_OBSERVATIONS_TABLE
        self.timeout = timeout or MARKET_OBS_TIMEOUT

    @property
    def available(self) -> bool:
        return core._supabase_enabled()

    def _url(self) -> str:
        return f"{core.SUPABASE_URL}/rest/v1/{self.table}"

    def insert_rows(self, rows: list[dict[str, Any]]) -> InsertOutcome:
        """Insert bars, ignoring any whose observation_id already exists.

        ``resolution=ignore-duplicates`` makes this an ON CONFLICT DO NOTHING:
        an existing row is left exactly as it was. ``return=representation``
        with ``select=observation_id`` returns only the ids actually inserted,
        so inserted and duplicate can be told apart without a second query.

        On a batch failure it retries each row individually, so one rejected bar
        cannot cost the rest of the batch its persistence.
        """
        if not rows:
            return InsertOutcome(backend="supabase", durable=True)
        if not self.available:
            return InsertOutcome(
                backend="unavailable",
                durable=False,
                failed=tuple(str(r.get("observation_id", "")) for r in rows),
                error="Supabase is not configured",
            )

        sent = [str(r.get("observation_id", "")) for r in rows]
        try:
            response = requests.post(
                self._url(),
                headers=core._supabase_headers(
                    "resolution=ignore-duplicates,return=representation"
                ),
                params={
                    "on_conflict": "observation_id",
                    "select": "observation_id",
                },
                json=rows,
                timeout=self.timeout,
            )
            response.raise_for_status()
            body = response.json()
            inserted = (
                tuple(
                    str(item.get("observation_id", ""))
                    for item in body
                    if isinstance(item, dict) and item.get("observation_id")
                )
                if isinstance(body, list)
                else ()
            )
            not_inserted = [r for r in sent if r not in set(inserted)]
            duplicate, conflicted = self._classify_duplicates(rows, not_inserted)
            return InsertOutcome(
                backend="supabase",
                durable=True,
                inserted=inserted,
                duplicate=tuple(duplicate),
                conflicted=tuple(conflicted),
            )
        except Exception as exc:
            if len(rows) == 1:
                return InsertOutcome(
                    backend="supabase",
                    durable=False,
                    failed=tuple(sent),
                    error=str(exc)[:200],
                )
            inserted_ids: list[str] = []
            duplicate_ids: list[str] = []
            conflicted_ids: list[str] = []
            failed_ids: list[str] = []
            last_error = str(exc)[:200]
            for row in rows:
                one = self.insert_rows([row])
                inserted_ids.extend(one.inserted)
                duplicate_ids.extend(one.duplicate)
                conflicted_ids.extend(one.conflicted)
                failed_ids.extend(one.failed)
                if one.error:
                    last_error = one.error
            return InsertOutcome(
                backend="supabase",
                durable=not failed_ids,
                inserted=tuple(inserted_ids),
                duplicate=tuple(duplicate_ids),
                conflicted=tuple(conflicted_ids),
                failed=tuple(failed_ids),
                error=last_error if failed_ids else "",
            )

    def _classify_duplicates(
        self, rows: list[dict[str, Any]], not_inserted: list[str]
    ) -> tuple[list[str], list[str]]:
        """Split refused rows into benign duplicates and genuine conflicts.

        A refused bar is benign when the stored payload hashes identically --
        that is the normal case, because every run re-fetches the same 30-day
        window. When the hashes differ, two different sets of values claim one
        bar: a vendor revision, or a bug. Neither row is overwritten and the
        conflict is reported so a human can look.
        """
        if not not_inserted:
            return [], []
        by_id = {str(r.get("observation_id", "")): r for r in rows}
        duplicate: list[str] = []
        conflicted: list[str] = []
        for observation_id in not_inserted:
            expected = str(by_id.get(observation_id, {}).get("content_hash", ""))
            stored = self.stored_content_hash(observation_id)
            if expected and stored and stored != expected:
                conflicted.append(observation_id)
            else:
                duplicate.append(observation_id)
        return duplicate, conflicted

    def stored_content_hash(self, observation_id: str) -> str | None:
        """Content hash of a stored bar, or None when it cannot be read."""
        if not self.available:
            return None
        try:
            response = requests.get(
                self._url(),
                headers=core._supabase_headers(),
                params={
                    "observation_id": f"eq.{observation_id}",
                    "select": "content_hash",
                    "limit": 1,
                },
                timeout=self.timeout,
            )
            response.raise_for_status()
            body = response.json()
            if isinstance(body, list) and body and isinstance(body[0], dict):
                value = body[0].get("content_hash")
                return str(value) if value else None
            return None
        except Exception:
            return None

    def row_count(self) -> int | None:
        """Total rows, or None when it cannot be determined."""
        if not self.available:
            return None
        try:
            response = requests.get(
                self._url(),
                headers=core._supabase_headers("count=exact"),
                params={"select": "observation_id", "limit": 1},
                timeout=self.timeout,
            )
            response.raise_for_status()
            content_range = response.headers.get("content-range", "")
            total = content_range.rsplit("/", 1)[-1] if "/" in content_range else ""
            return int(total) if total.isdigit() else None
        except Exception:
            return None

    def query_bars(
        self,
        *,
        symbols: Sequence[str],
        start: datetime,
        end: datetime,
        granularity: str = GRANULARITY_1D,
        limit: int = 10000,
    ) -> list[dict[str, Any]]:
        """Every stored bar for these symbols in one request.

        Deliberately takes a LIST of symbols and one time span so a whole
        validation run costs a single query, never one per observation. Rides
        the natural-key unique index via its (symbol, granularity, bar_time)
        prefix.
        """
        if not symbols or not self.available:
            return []
        try:
            quoted = ",".join(f'"{s}"' for s in symbols)
            response = requests.get(
                self._url(),
                headers=core._supabase_headers(),
                params={
                    "symbol": f"in.({quoted})",
                    "granularity": f"eq.{granularity}",
                    "bar_time": f"gt.{canonical_bar_time_iso(start)}",
                    "and": f"(bar_time.lte.{canonical_bar_time_iso(end)})",
                    "select": (
                        "observation_id,symbol,instrument,granularity,price_source,"
                        "bar_time,open,high,low,close,volume,invert,content_hash"
                    ),
                    "order": "symbol.asc,bar_time.asc",
                    "limit": int(limit),
                },
                timeout=self.timeout,
            )
            response.raise_for_status()
            body = response.json()
            return list(body) if isinstance(body, list) else []
        except Exception:
            return []


class LocalMarketObservationStore:
    """Append-only JSONL mirror for local development and tests.

    Deliberately a SEPARATE backend identity. A local write is never reported as
    durable: a redeploy discards the container filesystem, so treating it as
    equivalent to a cloud write would silently misrepresent what was preserved.
    """

    def __init__(self, path: str | None = None) -> None:
        self.path = path or LOCAL_MARKET_OBSERVATIONS_FILE

    @property
    def available(self) -> bool:
        return True

    def _existing(self) -> dict[str, str]:
        """observation_id -> content_hash for everything already appended."""
        known: dict[str, str] = {}
        try:
            if not os.path.exists(self.path):
                return known
            with open(self.path, "r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except Exception:
                        continue
                    observation_id = str(row.get("observation_id", ""))
                    if observation_id:
                        known[observation_id] = str(row.get("content_hash", ""))
        except Exception:
            return known
        return known

    def insert_rows(self, rows: list[dict[str, Any]]) -> InsertOutcome:
        if not rows:
            return InsertOutcome(backend="local", durable=False)
        known = self._existing()
        inserted: list[str] = []
        duplicate: list[str] = []
        conflicted: list[str] = []
        failed: list[str] = []
        try:
            with open(self.path, "a", encoding="utf-8") as handle:
                for row in rows:
                    observation_id = str(row.get("observation_id", ""))
                    if not observation_id:
                        failed.append(observation_id)
                        continue
                    if observation_id in known:
                        expected = str(row.get("content_hash", ""))
                        stored = known[observation_id]
                        if expected and stored and stored != expected:
                            conflicted.append(observation_id)
                        else:
                            duplicate.append(observation_id)
                        continue
                    handle.write(json.dumps(row, separators=(",", ":")) + "\n")
                    known[observation_id] = str(row.get("content_hash", ""))
                    inserted.append(observation_id)
        except Exception as exc:
            settled = len(inserted) + len(duplicate) + len(conflicted)
            return InsertOutcome(
                backend="local",
                durable=False,
                inserted=tuple(inserted),
                duplicate=tuple(duplicate),
                conflicted=tuple(conflicted),
                failed=tuple(
                    str(r.get("observation_id", "")) for r in rows
                )[settled:],
                error=str(exc)[:200],
            )
        return InsertOutcome(
            backend="local",
            durable=False,
            inserted=tuple(inserted),
            duplicate=tuple(duplicate),
            conflicted=tuple(conflicted),
            failed=tuple(failed),
        )

    def stored_content_hash(self, observation_id: str) -> str | None:
        return self._existing().get(observation_id) or None

    def row_count(self) -> int | None:
        return len(self._existing())

    def query_bars(
        self,
        *,
        symbols: Sequence[str],
        start: datetime,
        end: datetime,
        granularity: str = GRANULARITY_1D,
        limit: int = 10000,
    ) -> list[dict[str, Any]]:
        wanted = set(symbols)
        low = canonical_bar_time_iso(start)
        high = canonical_bar_time_iso(end)
        rows: list[dict[str, Any]] = []
        try:
            if not os.path.exists(self.path):
                return rows
            with open(self.path, "r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except Exception:
                        continue
                    if row.get("symbol") not in wanted:
                        continue
                    if str(row.get("granularity")) != granularity:
                        continue
                    stamp = str(row.get("bar_time", ""))
                    if not (low < stamp <= high):
                        continue
                    rows.append(row)
        except Exception:
            return rows
        rows.sort(key=lambda r: (str(r.get("symbol")), str(r.get("bar_time"))))
        return rows[: int(limit)]


def resolve_market_store() -> Any:
    """The append-only store to use: Supabase when configured, else local."""
    supabase = SupabaseMarketObservationStore()
    return supabase if supabase.available else LocalMarketObservationStore()


# ===========================================================================
# DAILY BAR FETCH -- OFFLINE, OPERATOR-INVOKED
#
# Same endpoint and same symbols production already uses for the tactical
# series; only the range and interval differ. A 14-day tactical window cannot
# be resolved from a five-day intraday history, which is the entire reason this
# shape exists.
#
# This function is NEVER called from the production daemon. It is reached only
# through capture_daily_bars(), which an operator invokes.
# ===========================================================================


def fetch_daily_bars(
    symbol: str,
    *,
    instrument: str,
    invert: bool,
    now: datetime | None = None,
    timeout: int | None = None,
) -> list[MarketBar]:
    """Closed daily bars for one symbol. Never invents a bar.

    Any failure -- unreachable endpoint, malformed payload, too few bars --
    yields an empty list. A capture that did not happen is a gap to be retried,
    which is a different and far safer thing than a fabricated price.
    """
    reference = now or datetime.now(timezone.utc)
    if not symbol:
        return []
    try:
        url = (
            "https://query1.finance.yahoo.com/v8/finance/chart/"
            f"{quote(symbol, safe='')}"
        )
        response = requests.get(
            url,
            params={
                "range": DAILY_RANGE,
                "interval": DAILY_INTERVAL,
                "includePrePost": "false",
                "events": "div,splits",
            },
            headers={"User-Agent": "Mozilla/5.0 ApexMacro B2 Validation/1.0"},
            timeout=timeout or MARKET_OBS_TIMEOUT,
        )
        response.raise_for_status()
        payload = response.json()
    except Exception:
        return []

    result = (((payload or {}).get("chart") or {}).get("result") or [None])[0]
    if not isinstance(result, dict):
        return []
    timestamps = result.get("timestamp") or []
    quote_data = (((result.get("indicators") or {}).get("quote")) or [{}])[0]
    opens = quote_data.get("open") or []
    highs = quote_data.get("high") or []
    lows = quote_data.get("low") or []
    closes = quote_data.get("close") or []
    volumes = quote_data.get("volume") or []
    if not timestamps or not closes:
        return []

    exchange_tz = str(
        ((result.get("meta") or {}) if isinstance(result.get("meta"), dict) else {})
        .get("exchangeTimezoneName", "")
    )

    bars: list[MarketBar] = []
    for index, stamp in enumerate(timestamps):
        try:
            close = closes[index] if index < len(closes) else None
            if close is None:
                continue
            close_v = float(close)
            open_v = (
                float(opens[index])
                if index < len(opens) and opens[index] is not None
                else close_v
            )
            high_v = (
                float(highs[index])
                if index < len(highs) and highs[index] is not None
                else max(open_v, close_v)
            )
            low_v = (
                float(lows[index])
                if index < len(lows) and lows[index] is not None
                else min(open_v, close_v)
            )
            volume_v = (
                float(volumes[index])
                if index < len(volumes) and volumes[index] is not None
                else None
            )
            bar_time = datetime.fromtimestamp(int(stamp), tz=timezone.utc)

            # Closed bars only. An in-progress daily bar is revised for the rest
            # of the session, and ON CONFLICT DO NOTHING would freeze the
            # partial version into an append-only store permanently.
            if not bar_is_final(bar_time, GRANULARITY_1D, reference):
                continue

            bars.append(
                MarketBar(
                    symbol=symbol,
                    instrument=instrument,
                    granularity=GRANULARITY_1D,
                    bar_time=bar_time,
                    open=open_v,
                    # Same defensive clamp production applies when building the
                    # tactical series: a vendor high below the open is bad data,
                    # not a tradable price.
                    high=max(high_v, open_v, close_v),
                    low=min(low_v, open_v, close_v),
                    close=close_v,
                    volume=volume_v,
                    invert=invert,
                    meta={
                        "resolver_version": RESOLVER_VERSION,
                        "request": {
                            "endpoint": "query1.finance.yahoo.com/v8/finance/chart",
                            "range": DAILY_RANGE,
                            "interval": DAILY_INTERVAL,
                            "includePrePost": False,
                        },
                        "raw_epoch_seconds": int(stamp),
                        "bar_index_in_response": index,
                        "exchange_timezone": exchange_tz,
                        "bar_time_normalisation": (
                            "Yahoo epoch seconds interpreted as bar OPEN, "
                            "converted to UTC."
                        ),
                        "capture_rule": (
                            "Only bars whose period had fully closed at capture "
                            "time were inserted."
                        ),
                    },
                )
            )
        except (MarketObservationError, TypeError, ValueError, OSError, OverflowError):
            # One malformed bar is skipped; the rest of the response stands.
            continue

    if len(bars) < MIN_DAILY_BARS:
        return []
    return bars


def capture_daily_bars(
    instruments: Iterable[str] | None = None,
    *,
    store: Any | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Offline entry point: capture closed daily bars for B2's instruments.

    **Operator-invoked.** Nothing schedules this, no thread runs it, and the
    production daemon does not know it exists. It issues no AI request and
    sends no message.

    Never raises: a per-symbol failure is contained and reported so one bad
    instrument cannot cost the others their capture.
    """
    reference = now or datetime.now(timezone.utc)
    backend = store if store is not None else resolve_market_store()
    wanted = tuple(instruments) if instruments is not None else registered_instruments()

    rows: list[dict[str, Any]] = []
    per_instrument: dict[str, str] = {}
    symbols_seen: dict[str, str] = {}

    for instrument in wanted:
        convention = symbol_convention(instrument)
        if convention is None:
            per_instrument[instrument] = "unknown_instrument"
            continue
        try:
            bars = fetch_daily_bars(
                convention.symbol,
                instrument=instrument,
                invert=convention.invert,
                now=reference,
            )
        except Exception:
            per_instrument[instrument] = "fetch_failed"
            continue
        if not bars:
            per_instrument[instrument] = "no_bars"
            continue
        symbols_seen[instrument] = convention.symbol
        rows.extend(bar.to_row() for bar in bars)
        per_instrument[instrument] = "fetched"

    if not rows:
        return {
            "captured_at": reference.isoformat(),
            "resolver_version": RESOLVER_VERSION,
            "granularity": GRANULARITY_1D,
            "instruments": per_instrument,
            "symbols": symbols_seen,
            "backend": "none",
            "durable": False,
            "inserted": 0,
            "duplicate": 0,
            "conflicted": [],
            "failed": 0,
        }

    try:
        outcome = backend.insert_rows(rows)
    except Exception as exc:
        outcome = InsertOutcome(
            backend="unavailable",
            durable=False,
            failed=tuple(str(r.get("observation_id", "")) for r in rows),
            error=str(exc)[:200],
        )

    return {
        "captured_at": reference.isoformat(),
        "resolver_version": RESOLVER_VERSION,
        "granularity": GRANULARITY_1D,
        "instruments": per_instrument,
        "symbols": symbols_seen,
        "backend": outcome.backend,
        # True only for a real cloud write. A local mirror is never durable.
        "durable": outcome.durable,
        "inserted": len(outcome.inserted),
        "duplicate": len(outcome.duplicate),
        # Surfaced, never resolved silently: two payloads claim one bar.
        "conflicted": list(outcome.conflicted),
        "failed": len(outcome.failed),
        "error": outcome.error,
    }


# ===========================================================================
# READ SIDE -- anchoring stored shadow observations against stored bars
# ===========================================================================


def anchor_census(records: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """How many stored observations can be anchored, and how.

    Pure bookkeeping over records the caller already holds. Produces the count
    that matters most today: how much of the existing evidence carries a real
    point-in-time anchor rather than one that would have to be reconstructed.
    """
    counts = {status.value: 0 for status in AnchorStatus}
    by_instrument: dict[str, dict[str, int]] = {}
    caveats: dict[str, int] = {}

    for record in records:
        payload = record.get("record") if isinstance(record, Mapping) else None
        payload = payload if isinstance(payload, Mapping) else record
        instrument = str(payload.get("instrument") or "")
        resolution = classify_anchor(payload, symbol_convention(instrument))
        counts[resolution.status.value] += 1
        bucket = by_instrument.setdefault(
            instrument, {status.value: 0 for status in AnchorStatus}
        )
        bucket[resolution.status.value] += 1
        for caveat in resolution.caveats:
            caveats[caveat] = caveats.get(caveat, 0) + 1

    return {
        "total": sum(counts.values()),
        "by_status": counts,
        "by_instrument": by_instrument,
        "caveats": caveats,
    }


def forward_window_for(horizon: str) -> timedelta:
    """The architecture's own evaluation window for a horizon string."""
    try:
        return HORIZON_EVALUATION_WINDOW[Horizon(str(horizon))]
    except (KeyError, ValueError):
        return HORIZON_EVALUATION_WINDOW[Horizon.TACTICAL]


def resolve_observation(
    record: Mapping[str, Any],
    bars: Sequence[MarketBar],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Everything known about one observation's forward window. No labelling.

    Stage D-1 deliberately stops at the market facts: which anchor is available,
    which forward bars exist, and whether the window is covered. Outcome
    labelling and metrics are a later, separately approved step, and inventing
    them here would be exactly the "optimise before the measurement
    infrastructure is correct" mistake Stage D exists to avoid.
    """
    reference = now or datetime.now(timezone.utc)
    payload = record.get("record") if isinstance(record, Mapping) else None
    payload = payload if isinstance(payload, Mapping) else record

    instrument = str(payload.get("instrument") or "")
    horizon = str(payload.get("horizon") or Horizon.TACTICAL.value)
    window = forward_window_for(horizon)
    resolution = classify_anchor(payload, symbol_convention(instrument))

    try:
        evaluated_at = datetime.fromisoformat(
            str(payload.get("evaluated_at") or "").replace("Z", "+00:00")
        )
    except ValueError:
        return {
            "storage_id": str(record.get("storage_id") or ""),
            "instrument": instrument,
            "horizon": horizon,
            "anchor": resolution.as_record(),
            "status": "unvalidatable_bad_timestamp",
            "coverage": None,
            "forward_bars": 0,
        }

    relevant = [b for b in bars if b.symbol == resolution.symbol]
    selected = forward_bars(relevant, evaluated_at=evaluated_at, window=window)
    cover = coverage(
        relevant, evaluated_at=evaluated_at, window=window, now=reference
    )

    if resolution.status is AnchorStatus.MISSING:
        status = "unvalidatable_no_anchor"
    else:
        status = cover["status"]

    return {
        "storage_id": str(record.get("storage_id") or ""),
        "record_id": str(payload.get("record_id") or ""),
        "instrument": instrument,
        "horizon": horizon,
        "evaluated_at": evaluated_at.isoformat(),
        "anchor": resolution.as_record(),
        "status": status,
        "coverage": cover,
        "forward_bars": len(selected),
        "first_forward_bar": selected[0].bar_time_iso if selected else None,
        "last_forward_bar": selected[-1].bar_time_iso if selected else None,
    }


def resolve_range(
    records: Sequence[Mapping[str, Any]],
    *,
    store: Any | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Resolve many observations with ONE bar query, never one per record.

    The whole point of keying bars to the market series is that a single query
    covering the union of symbols and the widest window serves every
    observation. A per-observation fetch would be an N+1 pattern against a
    store whose rows are shared by construction.
    """
    reference = now or datetime.now(timezone.utc)
    backend = store if store is not None else resolve_market_store()
    if not records:
        return {"resolved": [], "bar_rows": 0, "symbols": [], "queries": 0}

    symbols: set[str] = set()
    stamps: list[datetime] = []
    widest = timedelta(0)
    for record in records:
        payload = record.get("record") if isinstance(record, Mapping) else None
        payload = payload if isinstance(payload, Mapping) else record
        instrument = str(payload.get("instrument") or "")
        resolution = classify_anchor(payload, symbol_convention(instrument))
        if resolution.symbol:
            symbols.add(resolution.symbol)
        window = forward_window_for(str(payload.get("horizon") or ""))
        widest = max(widest, window)
        try:
            stamps.append(
                datetime.fromisoformat(
                    str(payload.get("evaluated_at") or "").replace("Z", "+00:00")
                )
            )
        except ValueError:
            continue

    if not symbols or not stamps:
        # Every record is still resolved and reported, with the honest status
        # its own state earns. Returning an empty list here would DROP records
        # that cannot be anchored -- and an unvalidatable observation has to be
        # counted, not made to disappear from the denominator.
        return {
            "resolved": [
                resolve_observation(record, [], now=reference) for record in records
            ],
            "bar_rows": 0,
            "symbols": sorted(symbols),
            "queries": 0,
        }

    rows = backend.query_bars(
        symbols=sorted(symbols),
        start=min(stamps),
        end=max(stamps) + widest,
    )
    bars = [bar for bar in (row_to_bar(r) for r in rows) if bar is not None]

    return {
        "resolved": [
            resolve_observation(record, bars, now=reference) for record in records
        ],
        "bar_rows": len(bars),
        "symbols": sorted(symbols),
        # One query for the whole run, regardless of record count.
        "queries": 1,
    }


__all__ = [
    "DAILY_INTERVAL",
    "DAILY_RANGE",
    "LOCAL_MARKET_OBSERVATIONS_FILE",
    "MARKET_OBSERVATIONS_TABLE",
    "MARKET_OBS_TIMEOUT",
    "MIN_DAILY_BARS",
    "RESOLVER_VERSION",
    "LocalMarketObservationStore",
    "SupabaseMarketObservationStore",
    "anchor_census",
    "capture_daily_bars",
    "fetch_daily_bars",
    "forward_window_for",
    "resolve_market_store",
    "resolve_observation",
    "resolve_range",
]
