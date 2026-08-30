"""Bridge between Architecture B2 and the live ApexMacro production core.

This module deliberately lives OUTSIDE ``apex.b2``. Everything under that
package is pure and performs no I/O; this file is the single place allowed to
touch ``production_core``, and keeping it separate is what preserves that
guarantee.

**Nothing in the production system calls this module.** B2 remains in shadow
mode: no page, score, alert, scheduler or Telegram path imports it, so importing
it changes no production behaviour. Wiring an actual call site is a separate,
explicitly approved step.

Persistence reuses the existing Supabase-first / atomic-local-mirror layer under
two NEW state ids. The backing table is a generic key/value store keyed by id,
so adding ids requires no schema change and no migration of existing rows.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

import requests

from . import production_core as core
from .b2 import adapters
from .b2 import event_timing as event_timing_mod
from .b2.enums import Direction, Horizon
from .b2.horizons import HORIZON_EVALUATION_WINDOW
from .b2.modules import fx as fx_module
from .b2.modules import module_for, registered_instruments
from .b2.evaluate import ShadowEvaluation, run_shadow_evaluation, thesis_input_keys
from .b2.predictions import (
    PredictionLog,
    PredictionLogError,
    TransmissionStep,
    build_prediction,
)
from .b2.shadow import ShadowLog, ShadowLogError, record_to_row

#: New state ids. Existing ids and payload shapes are untouched.
SHADOW_LOG_STATE_ID = "b2_shadow_log_v1"
PREDICTION_LOG_STATE_ID = "b2_prediction_log_v1"

SHADOW_LOG_FILE = str(core.PROJECT_ROOT / "b2_shadow_log_v1.json")
PREDICTION_LOG_FILE = str(core.PROJECT_ROOT / "b2_prediction_log_v1.json")


class ProductionShadowStore:
    """ShadowStore backed by the existing persistence layer.

    Satisfies ``apex.b2.shadow.ShadowStore`` structurally. Uses the same
    Supabase-first read and atomic local mirror every other durable state in the
    project uses, so shadow records survive a redeploy exactly as the VIP
    registry and forecaster history do.
    """

    _PATHS = {
        SHADOW_LOG_STATE_ID: SHADOW_LOG_FILE,
        PREDICTION_LOG_STATE_ID: PREDICTION_LOG_FILE,
    }

    def _path_for(self, state_id: str) -> str:
        try:
            return self._PATHS[state_id]
        except KeyError:
            raise ValueError(f"Unknown B2 state id {state_id!r}") from None

    def load(self, state_id: str, default: object) -> object:
        return core._load_persistent_state(state_id, self._path_for(state_id), default)

    def save(self, state_id: str, payload: object) -> None:
        core._save_persistent_state(state_id, self._path_for(state_id), payload)


def load_shadow_log(store: Any) -> ShadowLog:
    return ShadowLog.from_record(store.load(SHADOW_LOG_STATE_ID, {"records": []}))


def save_shadow_log(store: Any, log: ShadowLog) -> None:
    store.save(SHADOW_LOG_STATE_ID, log.as_record())


def load_prediction_log(store: Any) -> PredictionLog:
    return PredictionLog.from_record(
        store.load(PREDICTION_LOG_STATE_ID, {"predictions": [], "outcomes": []})
    )


def save_prediction_log(store: Any, log: PredictionLog) -> None:
    store.save(PREDICTION_LOG_STATE_ID, log.as_record())


def signals_from_production(
    *,
    composite: Mapping[str, Any] | None = None,
    real_yield_mtf: Mapping[str, Any] | None = None,
    nominal_yield_mtf: Mapping[str, Any] | None = None,
    inflation_expectations_mtf: Mapping[str, Any] | None = None,
    rule_points: float | None = None,
    ai_points: float | None = None,
    tactical: Mapping[str, Any] | None = None,
) -> dict[str, dict[str, float | None]]:
    """Translate live production outputs into B2 member signals.

    ``composite`` is a ``compute_composite(...)`` result and ``tactical`` a
    ``compute_tactical_move(...)`` result. The volatility scale is taken from
    the tactical result's own ``volatility_scale`` export, so the returns are
    normalised on exactly the scale that function already used internally
    rather than on a second, independently invented definition.
    """
    rows: Sequence[Mapping[str, Any]] | None = None
    if isinstance(composite, Mapping):
        candidate = composite.get("rows")
        if isinstance(candidate, Sequence):
            rows = candidate

    volatility_scale = None
    if isinstance(tactical, Mapping):
        volatility_scale = tactical.get("volatility_scale")

    return adapters.build_signals(
        composite_rows=rows,
        real_yield_mtf=real_yield_mtf,
        nominal_yield_mtf=nominal_yield_mtf,
        inflation_expectations_mtf=inflation_expectations_mtf,
        rule_points=rule_points,
        ai_points=ai_points,
        tactical=tactical,
        volatility_scale=volatility_scale,
    )


def evaluate_from_production(
    *,
    instrument: str,
    decision_horizon: Horizon = Horizon.TACTICAL,
    composite: Mapping[str, Any] | None = None,
    tactical: Mapping[str, Any] | None = None,
    real_yield_mtf: Mapping[str, Any] | None = None,
    nominal_yield_mtf: Mapping[str, Any] | None = None,
    inflation_expectations_mtf: Mapping[str, Any] | None = None,
    rule_points: float | None = None,
    ai_points: float | None = None,
    prediction_log: PredictionLog | None = None,
    **kwargs: Any,
) -> ShadowEvaluation:
    """Run one shadow evaluation from live production dictionaries.

    Read-only with respect to production: it consumes values the caller already
    computed and mutates nothing. It issues no AI request, opens no thread and
    sends no message.
    """
    entry_plan = None
    if isinstance(tactical, Mapping):
        candidate = tactical.get("entry_plan")
        if isinstance(candidate, Mapping):
            entry_plan = candidate

    execution_inputs = adapters.execution_inputs(entry_plan=entry_plan)
    signals = signals_from_production(
        composite=composite,
        real_yield_mtf=real_yield_mtf,
        nominal_yield_mtf=nominal_yield_mtf,
        inflation_expectations_mtf=inflation_expectations_mtf,
        rule_points=rule_points,
        ai_points=ai_points,
        tactical=tactical,
    )

    merged: dict[str, Any] = {
        "invalidation_level": execution_inputs["invalidation_level"],
        "entry_zone": execution_inputs["entry_zone"],
        "current_price": execution_inputs["current_price"],
        "atr": execution_inputs["atr"],
        "atr_ratio": execution_inputs["atr_ratio"],
        "room_to_opposing_atr": execution_inputs["room_to_opposing_atr"],
        "asymmetry_ratio": execution_inputs["asymmetry_ratio"],
        "volatility_regime": execution_inputs["volatility_regime"],
        "technical_invalidated": execution_inputs["technical_invalidated"],
    }
    merged.update(kwargs)

    return run_shadow_evaluation(
        instrument=instrument,
        decision_horizon=decision_horizon,
        signals_by_family=signals,
        prediction_log=prediction_log,
        **merged,
    )


def record_evaluation(store: Any, evaluation: ShadowEvaluation) -> ShadowLog:
    """Append one evaluation to the durable shadow log and persist it.

    Append-only: an existing record id is never overwritten.
    """
    log = load_shadow_log(store)
    log.append(evaluation.record)
    save_shadow_log(store, log)
    return log


# ===========================================================================
# SHADOW ACTIVATION
#
# The observation driver invoked once per iteration by the existing 60-second
# production daemon loop. It is observational only: production never reads its
# result, it starts no thread, issues no AI request and sends no message.
#
# Cost control and duplicate suppression are the same mechanism. One
# observation is taken per instrument per UTC hour, identified deterministically
# so that a Streamlit rerun, a process restart or a second loop owner within the
# same hour recomputes the same id and is rejected by the append-only log. The
# bucket is checked BEFORE any data is gathered, so on 59 of every 60 iterations
# this does no work at all.
# ===========================================================================

#: One observation per instrument per hour.
OBSERVATION_BUCKET_SECONDS = 3600

#: Counter names, all of which are required observability for the hook.
HOOK_COUNTERS = (
    "attempted",
    "written",
    "duplicate_skipped",
    "insufficient_data_skipped",
    "exception_swallowed",
    "disabled",
    "unknown_instrument",
    "prediction_registered",
    "prediction_duplicate",
    # Storage V2
    "v2_inserted",
    "v2_duplicate",
    "v2_failed",
    "v2_local_fallback",
    "v2_identity_conflict",
    "v2_logical_duplicate",
    "v2_dedup_check_unavailable",
    "migration_pending",
    "migration_complete",
    "backfill_inserted",
    "backfill_duplicate",
    "backfill_failed",
)

#: In-process counters for the running daemon. Mirrored into the shadow log's
#: own payload so they survive a restart. Never surfaced as a Telegram message
#: and never rendered as user-facing UI.
HOOK_STATS: dict[str, int] = {name: 0 for name in HOOK_COUNTERS}

#: Buckets already handled in this process, so the common case costs no store
#: read at all. Cleared naturally by a restart, after which one read re-syncs it.
_HANDLED_BUCKETS: dict[str, int] = {}


def _bump(counter: str) -> None:
    HOOK_STATS[counter] = HOOK_STATS.get(counter, 0) + 1


# ===========================================================================
# STORAGE V2 -- APPEND-ONLY SHADOW RECORDS
#
# Each observation becomes ONE immutable row in the dedicated
# ``b2_shadow_records`` table, instead of being appended into a single JSON blob
# that is rewritten in full every cycle.
#
# Three properties matter and are enforced here rather than assumed:
#
#   Append-only.   Inserts use ON CONFLICT DO NOTHING against the record_id
#                  primary key. An existing row is never updated, never
#                  re-timestamped, never overwritten.
#   O(new).        Writing one observation costs one row, not the whole
#                  history. Nothing loads past records to append a new one.
#   Lock-free.     This path talks to PostgREST directly and NEVER calls
#                  _save_persistent_state, so it cannot hold the global
#                  production _PERSISTENCE_LOCK that VIP login, payment
#                  verification, Smart Shift state and the Forecaster share.
#
# The database primary key is the final durable duplicate authority, so
# suppression survives a process restart without loading any history.
# ===========================================================================

#: The pre-created append-only table. Overridable only for testing against a
#: scratch table; the application never creates or alters it.
SHADOW_RECORDS_TABLE = (
    core.get_secret("B2_SHADOW_RECORDS_TABLE", "b2_shadow_records") or "b2_shadow_records"
)

#: Own timeout, deliberately not production's REQUEST_TIMEOUT: a batch of 11
#: records is ~145 KB, and this path must never be the thing that stalls.
SHADOW_V2_TIMEOUT = 15

#: Local append-only mirror used when Supabase is not configured (local dev and
#: tests). One JSON object per line, appended, never rewritten.
LOCAL_RECORDS_FILE = str(core.PROJECT_ROOT / "b2_shadow_records_local.jsonl")

#: Storage modes. "v2" writes append-only rows; "legacy" keeps the old blob.
STORAGE_MODE_V2 = "v2"
STORAGE_MODE_LEGACY = "legacy"


def shadow_store_mode() -> str:
    """Which storage path new observations use.

    Defaults to V2. ``B2_SHADOW_STORE=legacy`` restores the previous behaviour
    exactly, which is the rollback switch: no code change, no data change.
    """
    raw = str(core.get_secret("B2_SHADOW_STORE", STORAGE_MODE_V2) or STORAGE_MODE_V2)
    mode = raw.strip().lower()
    return STORAGE_MODE_LEGACY if mode == STORAGE_MODE_LEGACY else STORAGE_MODE_V2


@dataclass(frozen=True)
class InsertOutcome:
    """Result of persisting a batch of rows. Never claims more than happened."""

    backend: str                      # "supabase" | "local" | "unavailable"
    durable: bool                     # True only for a real cloud write
    inserted: tuple[str, ...] = ()
    duplicate: tuple[str, ...] = ()
    failed: tuple[str, ...] = ()
    #: Same physical identity, DIFFERENT payload. Never resolved silently.
    conflicted: tuple[str, ...] = ()
    error: str = ""

    @property
    def settled(self) -> tuple[str, ...]:
        """Storage ids that need no retry: they are stored, or already were."""
        return self.inserted + self.duplicate

    def as_record(self) -> dict[str, object]:
        return {
            "backend": self.backend,
            "durable": self.durable,
            "inserted": len(self.inserted),
            "duplicate": len(self.duplicate),
            "failed": len(self.failed),
            "identity_conflict": len(self.conflicted),
            "error": self.error[:200],
        }


class SupabaseShadowRecordStore:
    """Append-only row store over the pre-created ``b2_shadow_records`` table.

    Uses the backend service-role credential already configured for production
    persistence, read-only: no production persistence function is called and no
    credential is copied, logged or widened. RLS stays enabled -- the
    service-role key is precisely the credential intended to operate under it,
    and it is only ever used server-side.
    """

    def __init__(self, table: str | None = None, timeout: int | None = None) -> None:
        self.table = table or SHADOW_RECORDS_TABLE
        self.timeout = timeout or SHADOW_V2_TIMEOUT

    @property
    def available(self) -> bool:
        return core._supabase_enabled()

    def _url(self) -> str:
        return f"{core.SUPABASE_URL}/rest/v1/{self.table}"

    def insert_rows(self, rows: list[dict[str, Any]]) -> InsertOutcome:
        """Insert rows, ignoring any whose record_id already exists.

        ``resolution=ignore-duplicates`` makes this an ON CONFLICT DO NOTHING:
        an existing row is left exactly as it was. ``return=representation``
        with ``select=record_id`` returns only the ids actually inserted, so
        inserted and duplicate can be told apart without a second query and
        without transferring the payload back.

        On a batch failure it retries each row individually, so one malformed or
        rejected record cannot cost the other ten their persistence.
        """
        if not rows:
            return InsertOutcome(backend="supabase", durable=True)
        if not self.available:
            return InsertOutcome(
                backend="unavailable",
                durable=False,
                failed=tuple(str(r.get("record_id", "")) for r in rows),
                error="Supabase is not configured",
            )

        sent = [str(r.get("storage_id", "")) for r in rows]
        try:
            response = requests.post(
                self._url(),
                headers=core._supabase_headers(
                    "resolution=ignore-duplicates,return=representation"
                ),
                # Conflict is now resolved on the PHYSICAL point-in-time key, so
                # two observations sharing a logical record_id no longer collide.
                params={"on_conflict": "storage_id", "select": "storage_id"},
                json=rows,
                timeout=self.timeout,
            )
            response.raise_for_status()
            body = response.json()
            inserted = tuple(
                str(item.get("storage_id", ""))
                for item in body
                if isinstance(item, dict) and item.get("storage_id")
            ) if isinstance(body, list) else ()
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
            # Per-record fault isolation: correctness before request count.
            inserted: list[str] = []
            duplicate: list[str] = []
            failed: list[str] = []
            last_error = str(exc)[:200]
            for row in rows:
                one = self.insert_rows([row])
                inserted.extend(one.inserted)
                duplicate.extend(one.duplicate)
                failed.extend(one.failed)
                if one.error:
                    last_error = one.error
            return InsertOutcome(
                backend="supabase",
                durable=not failed,
                inserted=tuple(inserted),
                duplicate=tuple(duplicate),
                failed=tuple(failed),
                error=last_error if failed else "",
            )

    def _classify_duplicates(
        self, rows: list[dict[str, Any]], not_inserted: list[str]
    ) -> tuple[list[str], list[str]]:
        """Split rows the database refused into benign duplicates vs conflicts.

        A refused row is benign when the stored payload hashes identically: that
        is an exact retry. When the hashes differ, two different payloads claim
        one point-in-time identity, which is an integrity problem. Neither row
        is overwritten; the conflict is reported so a human can look.

        Only runs on the rare refused-row path, so it costs nothing in the
        normal case. If the check cannot be made, the row is treated as a plain
        duplicate rather than being asserted to be a conflict.
        """
        if not not_inserted:
            return [], []
        by_id = {str(r.get("storage_id", "")): r for r in rows}
        duplicate: list[str] = []
        conflicted: list[str] = []
        for storage_id in not_inserted:
            expected = str(by_id.get(storage_id, {}).get("content_hash", ""))
            stored = self.stored_content_hash(storage_id)
            if expected and stored and stored != expected:
                conflicted.append(storage_id)
            else:
                duplicate.append(storage_id)
        return duplicate, conflicted

    def stored_content_hash(self, storage_id: str) -> str | None:
        """Content hash of a stored row, or None when it cannot be read."""
        if not self.available:
            return None
        try:
            response = requests.get(
                self._url(),
                headers=core._supabase_headers(),
                params={
                    "storage_id": f"eq.{storage_id}",
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

    def existing_storage_ids(self, storage_ids: list[str]) -> set[str] | None:
        """Which of these physical identities are already stored. One request.

        Returns None when the answer could not be obtained, which callers must
        treat as "unknown" rather than "none".
        """
        if not storage_ids:
            return set()
        if not self.available:
            return None
        try:
            quoted = ",".join(f'"{sid}"' for sid in storage_ids)
            response = requests.get(
                self._url(),
                headers=core._supabase_headers(),
                params={
                    "storage_id": f"in.({quoted})",
                    "select": "storage_id",
                    "limit": len(storage_ids),
                },
                timeout=self.timeout,
            )
            response.raise_for_status()
            body = response.json()
            if not isinstance(body, list):
                return None
            return {
                str(item.get("storage_id"))
                for item in body
                if isinstance(item, dict) and item.get("storage_id")
            }
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
                params={"select": "storage_id", "limit": 1},
                timeout=self.timeout,
            )
            response.raise_for_status()
            content_range = response.headers.get("content-range", "")
            total = content_range.rsplit("/", 1)[-1] if "/" in content_range else ""
            return int(total) if total.isdigit() else None
        except Exception:
            return None

    def record_exists(self, storage_id: str) -> bool | None:
        """Physical point-in-time existence. None when it cannot be determined."""
        if not self.available:
            return None
        try:
            response = requests.get(
                self._url(),
                headers=core._supabase_headers(),
                params={
                    "storage_id": f"eq.{storage_id}",
                    "select": "storage_id",
                    "limit": 1,
                },
                timeout=self.timeout,
            )
            response.raise_for_status()
            body = response.json()
            return bool(isinstance(body, list) and body)
        except Exception:
            return None

    def logical_record_exists(self, record_id: str) -> bool | None:
        """Has ANY observation already been stored for this hour bucket?

        This is the durable, restart-safe half of the cadence policy. The
        physical key can no longer enforce one-per-hour -- that is exactly the
        conflation the collision exposed -- so the live path asks this question
        explicitly. It is a single indexed lookup per due instrument, so the
        write path stays O(new records) and never loads history.

        Backfill deliberately does NOT consult this: restoring a distinct
        historical observation must succeed even when a newer observation
        already occupies the same hour bucket.
        """
        if not self.available:
            return None
        try:
            response = requests.get(
                self._url(),
                headers=core._supabase_headers(),
                params={
                    "record_id": f"eq.{record_id}",
                    "select": "record_id",
                    "limit": 1,
                },
                timeout=self.timeout,
            )
            response.raise_for_status()
            body = response.json()
            return bool(isinstance(body, list) and body)
        except Exception:
            return None

    def query_records(
        self,
        *,
        instrument: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 1000,
        select: str = "record_id,instrument,horizon,evaluated_at,schema_version,record",
    ) -> list[dict[str, Any]]:
        """Point-in-time retrieval. Indexed on (instrument, evaluated_at)."""
        if not self.available:
            return []
        params: dict[str, Any] = {
            "select": select,
            "order": "evaluated_at.desc",
            "limit": int(limit),
        }
        if instrument:
            params["instrument"] = f"eq.{instrument}"
        if start is not None:
            params["evaluated_at"] = f"gte.{start.isoformat()}"
        if end is not None:
            # PostgREST takes repeated filters on one column via a list value.
            params["and"] = f"(evaluated_at.lte.{end.isoformat()})"
        try:
            response = requests.get(
                self._url(),
                headers=core._supabase_headers(),
                params=params,
                timeout=self.timeout,
            )
            response.raise_for_status()
            body = response.json()
            return list(body) if isinstance(body, list) else []
        except Exception:
            return []


class LocalShadowRecordStore:
    """Append-only JSONL mirror for local development and tests.

    Deliberately a SEPARATE backend identity. A local write is never reported as
    durable: a Streamlit redeploy discards the container filesystem, so treating
    it as equivalent to a cloud write would silently misrepresent what was
    preserved.
    """

    def __init__(self, path: str | None = None) -> None:
        self.path = path or LOCAL_RECORDS_FILE

    @property
    def available(self) -> bool:
        return True

    def _existing(self) -> dict[str, str]:
        """storage_id -> content_hash for everything already appended."""
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
                    storage_id = str(row.get("storage_id", ""))
                    if storage_id:
                        known[storage_id] = str(row.get("content_hash", ""))
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
                    storage_id = str(row.get("storage_id", ""))
                    if not storage_id:
                        failed.append(storage_id)
                        continue
                    if storage_id in known:
                        expected = str(row.get("content_hash", ""))
                        stored = known[storage_id]
                        if expected and stored and stored != expected:
                            conflicted.append(storage_id)
                        else:
                            duplicate.append(storage_id)
                        continue
                    handle.write(json.dumps(row, separators=(",", ":")) + "\n")
                    known[storage_id] = str(row.get("content_hash", ""))
                    inserted.append(storage_id)
        except Exception as exc:
            settled = len(inserted) + len(duplicate) + len(conflicted)
            return InsertOutcome(
                backend="local",
                durable=False,
                inserted=tuple(inserted),
                duplicate=tuple(duplicate),
                conflicted=tuple(conflicted),
                failed=tuple(str(r.get("storage_id", "")) for r in rows)[settled:],
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

    def record_exists(self, storage_id: str) -> bool | None:
        return storage_id in self._existing()

    def stored_content_hash(self, storage_id: str) -> str | None:
        return self._existing().get(storage_id) or None

    def existing_storage_ids(self, storage_ids: list[str]) -> set[str] | None:
        known = set(self._existing())
        return {sid for sid in storage_ids if sid in known}

    def row_count(self) -> int | None:
        return len(self._existing())

    def logical_record_exists(self, record_id: str) -> bool | None:
        try:
            if not os.path.exists(self.path):
                return False
            with open(self.path, "r", encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        if str(json.loads(line).get("record_id", "")) == record_id:
                            return True
                    except Exception:
                        continue
        except Exception:
            return None
        return False

    def query_records(
        self,
        *,
        instrument: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = 1000,
        select: str = "",
    ) -> list[dict[str, Any]]:
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
                    if instrument and row.get("instrument") != instrument:
                        continue
                    stamp = str(row.get("evaluated_at", ""))
                    if start is not None and stamp < start.isoformat():
                        continue
                    if end is not None and stamp > end.isoformat():
                        continue
                    rows.append(row)
        except Exception:
            return rows
        rows.sort(key=lambda r: str(r.get("evaluated_at", "")), reverse=True)
        return rows[: int(limit)]


def resolve_record_store() -> Any:
    """The append-only store to use: Supabase when configured, else local."""
    supabase = SupabaseShadowRecordStore()
    return supabase if supabase.available else LocalShadowRecordStore()


# ===========================================================================
# LEGACY FREEZE + BACKFILL
#
# b2_shadow_log_v1 holds real point-in-time history. It is never deleted,
# truncated, transformed in place, regenerated or re-timestamped. The migration
# only READS it, and after cutover nothing writes to it again.
#
# Both steps run in bounded units inside the EXISTING daemon cadence: no new
# thread, scheduler or daemon. Every step is fail-open and retryable, and a
# failure can never stop production.
# ===========================================================================

MIGRATION_STATE_ID = "b2_storage_migration_v1"
MIGRATION_STATE_FILE = str(core.PROJECT_ROOT / "b2_storage_migration_v1.json")

#: Directory for the one-time legacy backup's local mirror. A module-level
#: constant rather than an inline PROJECT_ROOT expression so the test-isolation
#: helper can redirect it and a test run can never write a backup into the repo.
SHADOW_BACKUP_DIR = str(core.PROJECT_ROOT)


def _frozen_backup_path(frozen_id: str) -> str:
    return os.path.join(SHADOW_BACKUP_DIR, f"{frozen_id}.json")

#: Legacy records backfilled per daemon tick. Bounded so migration can never
#: turn one loop iteration into a long blocking operation.
BACKFILL_BATCH_SIZE = 100

#: Bumped when the STORAGE IDENTITY MODEL changes. A backfill completed under an
#: older model must run again, because "complete" then meant something weaker.
#:
#: The first backfill ran while record_id was the primary key. Eleven legacy
#: records collided with newer rows, ON CONFLICT DO NOTHING refused them, and the
#: run recorded them as duplicates and declared itself complete -- so those
#: observations were silently absent. Under storage-id-v1 they are distinct rows
#: and must be re-attempted. Re-running is safe: the backfill is idempotent by
#: storage_id, so records already present return as duplicates.
IDENTITY_MODEL_VERSION = "storage-id-v1"


def _migration_state() -> dict[str, Any]:
    state = core._load_persistent_state(MIGRATION_STATE_ID, MIGRATION_STATE_FILE, {})
    return dict(state) if isinstance(state, dict) else {}


def _save_migration_state(state: Mapping[str, Any]) -> None:
    # Small, infrequent, and written only while migration is in progress -- the
    # one place the KV path is still appropriate. Recurring row persistence
    # never comes through here.
    core._save_persistent_state(MIGRATION_STATE_ID, MIGRATION_STATE_FILE, dict(state))


def canonical_payload_hash(payload: object) -> str:
    """Deterministic hash of a payload, independent of key ordering."""
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


def legacy_frozen_state_id(moment: datetime | None = None) -> str:
    stamp = (moment or datetime.now(timezone.utc)).strftime("%Y%m%d")
    return f"b2_shadow_log_v1_frozen_{stamp}"


def freeze_legacy_shadow_log(
    store: Any, *, now: datetime | None = None
) -> dict[str, Any]:
    """One-time byte-semantic backup of the legacy blob, verified before use.

    Refuses to overwrite an existing valid backup. Returns a status dict; never
    raises.
    """
    state = _migration_state()
    if state.get("freeze_verified"):
        return {"status": "already_frozen", **{k: state.get(k) for k in
                ("frozen_state_id", "legacy_record_count", "legacy_hash")}}

    try:
        payload = store.load(SHADOW_LOG_STATE_ID, {"records": []})
    except Exception as exc:
        return {"status": "failed", "reason": f"legacy read failed: {str(exc)[:150]}"}

    if not isinstance(payload, Mapping):
        return {"status": "failed", "reason": "legacy payload is not a mapping"}

    records = payload.get("records")
    records = list(records) if isinstance(records, list) else []
    source_hash = canonical_payload_hash(payload)
    frozen_id = state.get("frozen_state_id") or legacy_frozen_state_id(now)

    existing = core._load_persistent_state(
        frozen_id, _frozen_backup_path(frozen_id), None
    )
    if isinstance(existing, Mapping) and canonical_payload_hash(existing) == source_hash:
        verified = True
    else:
        if isinstance(existing, Mapping) and existing:
            # A different backup already occupies this id. Never overwrite it.
            return {
                "status": "failed",
                "reason": f"a different backup already exists at {frozen_id}",
            }
        try:
            core._save_persistent_state(
                frozen_id, _frozen_backup_path(frozen_id), dict(payload)
            )
        except Exception as exc:
            return {"status": "failed", "reason": f"backup write failed: {str(exc)[:150]}"}
        readback = core._load_persistent_state(
            frozen_id, _frozen_backup_path(frozen_id), None
        )
        verified = (
            isinstance(readback, Mapping)
            and canonical_payload_hash(readback) == source_hash
            and len(list(readback.get("records") or [])) == len(records)
        )

    if not verified:
        return {"status": "failed", "reason": "backup verification failed"}

    state.update(
        {
            "freeze_verified": True,
            "frozen_state_id": frozen_id,
            "frozen_at": (now or datetime.now(timezone.utc)).isoformat(),
            "legacy_record_count": len(records),
            "legacy_hash": source_hash,
        }
    )
    _save_migration_state(state)
    return {
        "status": "frozen",
        "frozen_state_id": frozen_id,
        "legacy_record_count": len(records),
        "legacy_hash": source_hash,
    }


def backfill_legacy_records(
    store: Any,
    record_store: Any,
    *,
    batch_size: int = BACKFILL_BATCH_SIZE,
) -> dict[str, Any]:
    """Copy legacy records into the append-only store, in one bounded batch.

    Idempotent by construction: rows carry their ORIGINAL record_id, and the
    store inserts only if absent. Records are copied verbatim -- never
    re-evaluated, never re-timestamped, and never given a fabricated
    aggregation_config, so legacy history stays historically truthful.

    Progress is a cursor into the legacy list, so an interrupted run resumes
    from where it stopped rather than restarting.
    """
    state = _migration_state()
    if state.get("backfill_complete"):
        return {"status": "complete", "cursor": state.get("cursor", 0)}
    if not state.get("freeze_verified"):
        return {"status": "blocked", "reason": "legacy freeze not verified yet"}

    try:
        payload = store.load(SHADOW_LOG_STATE_ID, {"records": []})
    except Exception as exc:
        return {"status": "failed", "reason": f"legacy read failed: {str(exc)[:150]}"}

    records = payload.get("records") if isinstance(payload, Mapping) else None
    records = list(records) if isinstance(records, list) else []
    cursor = int(state.get("cursor", 0) or 0)

    if cursor >= len(records):
        state.update({"backfill_complete": True, "cursor": len(records)})
        _save_migration_state(state)
        _bump("migration_complete")
        return {"status": "complete", "cursor": len(records), "total": len(records)}

    chunk = records[cursor : cursor + max(1, int(batch_size))]
    rows = [row for row in (record_to_row(r) for r in chunk) if row]
    skipped = len(chunk) - len(rows)

    outcome = record_store.insert_rows(rows) if rows else InsertOutcome(
        backend="supabase", durable=True
    )

    if outcome.failed or outcome.conflicted:
        _bump("backfill_failed")
        # Do NOT advance the cursor past records that did not land. A conflict
        # here means a row with this exact point-in-time identity already holds
        # a DIFFERENT payload, which must be looked at rather than skipped.
        state["last_error"] = outcome.error or (
            f"identity conflict on {len(outcome.conflicted)} legacy record(s)"
            if outcome.conflicted else ""
        )
        _save_migration_state(state)
        return {
            "status": "partial",
            "cursor": cursor,
            "inserted": len(outcome.inserted),
            "failed": len(outcome.failed),
            "identity_conflict": len(outcome.conflicted),
            "backend": outcome.backend,
        }

    for _ in outcome.inserted:
        _bump("backfill_inserted")
    for _ in outcome.duplicate:
        _bump("backfill_duplicate")

    cursor += len(chunk)
    state.update(
        {
            "cursor": cursor,
            "total": len(records),
            "identity_model": IDENTITY_MODEL_VERSION,
            "backfill_complete": cursor >= len(records),
            "skipped_malformed": int(state.get("skipped_malformed", 0)) + skipped,
            "last_error": "",
        }
    )
    _save_migration_state(state)
    if state["backfill_complete"]:
        _bump("migration_complete")
    else:
        _bump("migration_pending")
    return {
        "status": "complete" if state["backfill_complete"] else "in_progress",
        "cursor": cursor,
        "total": len(records),
        "inserted": len(outcome.inserted),
        "duplicate": len(outcome.duplicate),
        "skipped_malformed": skipped,
        "backend": outcome.backend,
    }


def verify_storage_identity_parity(
    store: Any, record_store: Any
) -> dict[str, Any]:
    """Prove the application computes the same storage_id the database holds.

    The schema migration populated ``storage_id`` with a SQL expression. If that
    expression and ``canonical_storage_id`` disagree by even one character, a
    re-run backfill would not recognise the rows already present and would
    insert a duplicate copy of every legacy observation.

    So before any re-backfill, recompute the storage ids for the frozen legacy
    records and ask the table which exist. If the table holds rows but NONE of
    them match, that is a parity failure and the backfill is blocked rather than
    allowed to duplicate history.
    """
    try:
        payload = store.load(SHADOW_LOG_STATE_ID, {"records": []})
    except Exception as exc:
        return {"status": "unknown", "reason": f"legacy read failed: {str(exc)[:150]}"}

    records = payload.get("records") if isinstance(payload, Mapping) else None
    records = list(records) if isinstance(records, list) else []
    rows = [row for row in (record_to_row(r) for r in records) if row]
    if not rows:
        return {"status": "not_applicable", "reason": "no legacy records to check"}

    ids = [row["storage_id"] for row in rows]
    found = record_store.existing_storage_ids(ids)
    if found is None:
        return {"status": "unknown", "reason": "existence lookup unavailable"}

    total = record_store.row_count()
    if not found and total:
        return {
            "status": "mismatch",
            "reason": (
                f"none of {len(ids)} recomputed legacy storage ids exist, but the "
                f"table holds {total} rows. The SQL and application hashes "
                "disagree; backfill is blocked to avoid duplicating history."
            ),
            "checked": len(ids),
            "found": 0,
            "table_rows": total,
        }
    return {
        "status": "ok",
        "checked": len(ids),
        "found": len(found),
        "table_rows": total,
    }


def advance_migration(store: Any, record_store: Any) -> dict[str, Any]:
    """One bounded migration step per daemon tick. Never raises."""
    try:
        state = _migration_state()

        # A backfill completed under an older identity model is not complete
        # under this one. Reset the cursor -- never the freeze -- and re-run.
        if (
            state.get("backfill_complete")
            and state.get("identity_model") != IDENTITY_MODEL_VERSION
        ):
            parity = verify_storage_identity_parity(store, record_store)
            if parity.get("status") == "mismatch":
                state["last_error"] = parity.get("reason", "identity parity mismatch")
                state["parity"] = parity
                _save_migration_state(state)
                _bump("migration_pending")
                # Spread FIRST so the outer status is not overwritten by the
                # parity result's own "status" key.
                return {**parity, "status": "blocked_parity_mismatch"}
            if parity.get("status") == "unknown":
                _bump("migration_pending")
                return {**parity, "status": "deferred"}
            state.update({
                "backfill_complete": False,
                "cursor": 0,
                "identity_model": IDENTITY_MODEL_VERSION,
                "reran_for_identity_model_at": datetime.now(timezone.utc).isoformat(),
                "parity": parity,
                "last_error": "",
            })
            _save_migration_state(state)

        if state.get("backfill_complete"):
            return {"status": "complete"}
        if not state.get("freeze_verified"):
            frozen = freeze_legacy_shadow_log(store)
            if frozen.get("status") not in {"frozen", "already_frozen"}:
                _bump("migration_pending")
                return {"status": "freeze_failed", **frozen}
        return backfill_legacy_records(store, record_store)
    except Exception as exc:
        _bump("migration_pending")
        return {"status": "failed", "reason": str(exc)[:200]}


def migration_status() -> dict[str, Any]:
    """Explicit, auditable migration state for an operator to inspect."""
    state = _migration_state()
    return {
        "freeze_verified": bool(state.get("freeze_verified")),
        "frozen_state_id": state.get("frozen_state_id"),
        "legacy_record_count": state.get("legacy_record_count"),
        "legacy_hash": state.get("legacy_hash"),
        "cursor": state.get("cursor", 0),
        "total": state.get("total"),
        "backfill_complete": bool(state.get("backfill_complete")),
        "skipped_malformed": state.get("skipped_malformed", 0),
        "last_error": state.get("last_error", ""),
        "storage_mode": shadow_store_mode(),
        "identity_model": state.get("identity_model"),
        "identity_model_current": IDENTITY_MODEL_VERSION,
        "identity_model_up_to_date": state.get("identity_model") == IDENTITY_MODEL_VERSION,
        "reran_for_identity_model_at": state.get("reran_for_identity_model_at"),
        "parity": state.get("parity"),
    }


def shadow_enabled() -> bool:
    """Operator switch. Defaults on; set B2_SHADOW_ENABLED=0 to disable."""
    return str(core.get_secret("B2_SHADOW_ENABLED", "1")).strip().lower() not in {
        "0",
        "false",
        "off",
        "no",
    }


def default_shadow_instruments() -> tuple[str, ...]:
    """Every instrument with a registered Stage C asset module.

    Derived from the module registry rather than listed here, so the activated
    set cannot drift away from the modules that actually exist.
    """
    return registered_instruments()


def shadow_instruments() -> tuple[str, ...]:
    """Instruments to observe. Defaults to every registered asset module.

    Multi-asset observation is close to free in this system: the production
    daemon already computes tactical moves for every alertable asset and macro
    scores for every configured currency in the SAME loop iteration, moments
    before the hook runs, so B2 reads warm caches rather than issuing fresh
    requests. Persistence is batched into a single write per tick (see
    ``run_shadow_observation``), so the number of instruments does not multiply
    the upload volume either.

    ``B2_SHADOW_INSTRUMENTS`` still overrides, so an operator can narrow the set.
    """
    raw = str(core.get_secret("B2_SHADOW_INSTRUMENTS", "")).strip()
    if not raw:
        return default_shadow_instruments()
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def observation_key(instrument: str, horizon: Horizon, moment: datetime) -> str:
    """Deterministic identity for one instrument-hour observation."""
    bucket = int(moment.timestamp()) // OBSERVATION_BUCKET_SECONDS
    return f"b2obs|{instrument}|{horizon.value}|{bucket}"


def observation_record_id(key: str) -> str:
    """The record id ``build_shadow_record`` will derive from this key."""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:20]


def _event_timing_for(instrument: str, now: datetime) -> event_timing_mod.EventTiming:
    """True minutes to the nearest relevant high-impact event.

    Reads the SAME cached rolling calendar production already fetches every
    daemon iteration, with the same arguments, so no extra request is made and
    no production calendar logic is touched. Relevance uses production's own
    ``_get_asset_relevant_currencies``.

    Any failure -- no calendar, an unparseable timestamp -- yields an explicit
    unavailable result. Timing is never invented.
    """
    try:
        events = core.fetch_forex_factory_calendar_rolling(3, 0)
    except Exception:
        return event_timing_mod.UNAVAILABLE_NO_CALENDAR
    try:
        currencies = core._get_asset_relevant_currencies(instrument)
    except Exception:
        currencies = set()
    return event_timing_mod.minutes_to_nearest_event(events, currencies, now)


def _rate_leg(composite: Mapping[str, Any] | None) -> float | None:
    """The rate-category reading from a composite, or None.

    Reads ``compute_composite``'s own rows; it does not recompute anything.
    """
    if not isinstance(composite, Mapping):
        return None
    rows = composite.get("rows")
    if not isinstance(rows, Sequence):
        return None
    total = 0.0
    weight_sum = 0.0
    for row in rows:
        if not isinstance(row, Mapping) or str(row.get("cat", "")) != "rate":
            continue
        try:
            score = float(row.get("score"))
            weight = float(row.get("weight") or 1.0)
        except (TypeError, ValueError):
            continue
        if score != score or weight <= 0:
            continue
        total += score * weight
        weight_sum += weight
    return total / weight_sum if weight_sum > 0 else None


def _fx_relative_inputs(
    currency: str,
    composite: Mapping[str, Any] | None,
    fred_key: str,
    channel_name: str,
) -> dict[str, Any]:
    """Counter-currency legs for the FX module.

    Exactly one counter per currency, so the same domestic evidence cannot
    appear across several comparisons. Every leg that fails to arrive is left
    as None -- Unavailable -- rather than substituted.
    """
    counter = fx_module.counter_currency_for(currency)
    result: dict[str, Any] = {
        "fx_currency": currency,
        "fx_counter_currency": counter,
        "domestic_macro_score": (
            composite.get("macro_score") if isinstance(composite, Mapping) else None
        ),
        "domestic_rate_score": _rate_leg(composite),
        "counter_macro_score": None,
        "counter_rate_score": None,
        "counter_rate_substitution": "",
    }
    if counter is None:
        return result

    counter_composite = core.compute_composite(counter, fred_key, channel_name)
    result["counter_macro_score"] = (
        counter_composite.get("macro_score")
        if isinstance(counter_composite, Mapping)
        else None
    )

    if currency == "JPY":
        # JPY's configured rate is a short policy rate and no JPY long bond is
        # available here, so the US 10-year stands in on the counter side. This
        # is the relationship the data actually supports, and it is recorded on
        # the reading rather than presented as a matched-tenor differential.
        us10y = core.fetch_fred(core.GOLD_SERIES["yield"], fred_key, limit=60)
        if us10y is not None and not us10y.empty:
            mtf = core.calc_mtf(us10y["value"].tail(36).tolist(), "rate")
            if mtf:
                result["counter_rate_score"] = mtf.get("score")
                result["counter_rate_substitution"] = (
                    "Counter rate leg uses US 10Y yield momentum (DGS10), not a "
                    "matched-tenor JPY bond: no JPY long-bond series exists in "
                    "this project."
                )
    else:
        result["counter_rate_score"] = _rate_leg(counter_composite)

    return result


def _gather_production_inputs(
    instrument: str, fred_key: str, channel_name: str
) -> dict[str, Any] | None:
    """Read the production values for one instrument. Mutates nothing.

    Every function called here is already cached and already invoked elsewhere
    in the same daemon iteration, so this is a cache read rather than new load.
    Returns None when the instrument is not one production can price.
    """
    if core._tactical_symbol_config(instrument) is None:
        return None

    news = core.analyze_news_rule_based(core.fetch_all_instant_news(channel_name))
    scores = news.get("scores", {}) if isinstance(news, dict) else {}

    rule_points: float | None = None
    ai_points: float | None = None
    #: Instrument-specific extras the asset modules need, gathered only for the
    #: instrument being observed so no unrelated work is done.
    extra: dict[str, Any] = {}

    if instrument in core.CURRENCY_SERIES:
        macro_score = core._calc_currency_score_only(instrument, fred_key, channel_name)
        composite = core.compute_composite(instrument, fred_key, channel_name)
        rule_points = scores.get(instrument)
        extra.update(
            _fx_relative_inputs(instrument, composite, fred_key, channel_name)
        )
    elif instrument == "Gold":
        macro_score, _, _ = core._calc_gold_score_only(fred_key, channel_name)
        composite = core.compute_composite("USD", fred_key, channel_name)
        # Gold is the one asset where production separates the two news legs, so
        # both members of the News family can be read. Elsewhere only the blended
        # figure exists and the AI member stays Unavailable rather than invented.
        rule_points = news.get("gold_rule_points")
        ai_points = news.get("gold_ai_points")
    elif instrument == "Oil":
        macro_score, _ = core._calc_oil_score_only(fred_key, channel_name)
        composite = core.compute_composite("USD", fred_key, channel_name)
        rule_points = scores.get("Oil")
        # The pure price-momentum leg, read from production's own cached
        # function rather than recomputed from a second definition.
        extra["oil_price_momentum"] = core._oil_price_momentum_score(fred_key)
    elif instrument == "NDX":
        macro_score, _ = core._calc_ndx_score_only(fred_key, channel_name)
        composite = core.compute_composite("USD", fred_key, channel_name)
        rule_points = scores.get("Nasdaq")
    else:
        return None

    def _mtf(series_id: str, category: str, tail: int | None = None):
        frame = core.fetch_fred(series_id, fred_key, limit=60)
        if frame is None or frame.empty:
            return None
        values = frame["value"]
        return core.calc_mtf((values.tail(tail) if tail else values).tolist(), category)

    tactical = core.compute_tactical_move(instrument, macro_score)
    real_yield_mtf = _mtf(core.GOLD_SERIES["real_yield"], "rate", 36)
    usd_macro_score = None
    if instrument not in core.CURRENCY_SERIES and isinstance(composite, Mapping):
        # For Gold/Oil/NDX the composite IS the USD composite, so its macro_score
        # is the USD transmission input the asset modules need.
        usd_macro_score = composite.get("macro_score")

    return {
        "composite": composite,
        "tactical": tactical,
        "rule_points": rule_points,
        "ai_points": ai_points,
        "real_yield_mtf": real_yield_mtf,
        "nominal_yield_mtf": _mtf(core.GOLD_SERIES["yield"], "rate", 36),
        "inflation_expectations_mtf": _mtf(core.GOLD_SERIES["inflation_exp"], "inflation", 36),
        "usd_macro_score": usd_macro_score,
        "news": news,
        **extra,
    }


def _asset_module_inputs(instrument: str, inputs: Mapping[str, Any]) -> dict[str, Any] | None:
    """Per-instrument keyword inputs for the registered asset module.

    Returns None when no module is registered for this instrument, in which case
    the record simply carries no asset-module section rather than a fabricated one.
    """
    module = module_for(instrument)
    if module is None:
        return None

    news = inputs.get("news")
    news = news if isinstance(news, Mapping) else {}

    scores = news.get("scores")
    scores = scores if isinstance(scores, Mapping) else {}

    if instrument == "Gold":
        return {
            "real_yield_mtf": inputs.get("real_yield_mtf"),
            "usd_macro_score": inputs.get("usd_macro_score"),
            "gold_rule_points": news.get("gold_rule_points"),
            "gold_ai_points": news.get("gold_ai_points"),
        }
    if instrument == "Oil":
        return {
            "oil_price_momentum": inputs.get("oil_price_momentum"),
            "usd_macro_score": inputs.get("usd_macro_score"),
            "oil_news_points": scores.get("Oil"),
        }
    if instrument == "NDX":
        return {
            "real_yield_mtf": inputs.get("real_yield_mtf"),
            "usd_macro_score": inputs.get("usd_macro_score"),
            "nasdaq_news_points": scores.get("Nasdaq"),
        }
    if instrument in fx_module.INSTRUMENTS:
        return {
            "currency": instrument,
            "domestic_macro_score": inputs.get("domestic_macro_score"),
            "counter_macro_score": inputs.get("counter_macro_score"),
            "domestic_rate_score": inputs.get("domestic_rate_score"),
            "counter_rate_score": inputs.get("counter_rate_score"),
            "domestic_news_points": scores.get(instrument),
            "counter_rate_substitution": inputs.get("counter_rate_substitution", ""),
        }
    return None


def _build_observation(
    instrument: str,
    fred_key: str,
    channel_name: str,
    *,
    moment: datetime,
    horizon: Horizon,
    observation_identity: str,
) -> tuple[str, ShadowEvaluation | None]:
    """Gather production inputs and evaluate one observation.

    Shared by BOTH storage paths so the legacy blob and the append-only row
    store can never drift apart in what they record. Returns
    ``(status, evaluation)`` where status is ``ok`` / ``unknown_instrument`` /
    ``insufficient_data_skipped``.
    """
    inputs = _gather_production_inputs(instrument, fred_key, channel_name)
    if inputs is None:
        return "unknown_instrument", None

    if inputs["composite"] is None and inputs["tactical"] is None:
        # No production evidence of any kind arrived. There is nothing to
        # observe, which is different from observing that evidence is missing.
        return "insufficient_data_skipped", None

    # True minutes-to-event from the production calendar's own timestamps.
    timing = _event_timing_for(instrument, moment)

    evaluation = evaluate_from_production(
        instrument=instrument,
        decision_horizon=horizon,
        composite=inputs["composite"],
        tactical=inputs["tactical"],
        real_yield_mtf=inputs["real_yield_mtf"],
        nominal_yield_mtf=inputs["nominal_yield_mtf"],
        inflation_expectations_mtf=inputs["inflation_expectations_mtf"],
        rule_points=inputs["rule_points"],
        ai_points=inputs["ai_points"],
        minutes_to_event=timing.minutes,
        is_top_tier_event=event_timing_mod.is_top_tier(timing),
        event_label=timing.title or "scheduled event",
        asset_module_inputs=_asset_module_inputs(instrument, inputs),
        event_timing=timing.as_record(),
        evaluated_at=moment,
        observation_key=observation_identity,
    )
    return "ok", evaluation


def observe_instrument(
    instrument: str,
    fred_key: str,
    channel_name: str,
    *,
    store: Any,
    now: datetime | None = None,
    horizon: Horizon = Horizon.TACTICAL,
    shadow_log: ShadowLog | None = None,
) -> str:
    """Take at most one observation for this instrument in the current hour.

    Returns the outcome: written / duplicate_skipped / insufficient_data_skipped
    / unknown_instrument.

    ``shadow_log`` lets a caller batch several instruments into one load and one
    save. This matters a great deal: the whole log payload is rewritten on every
    save, so saving per instrument would multiply the upload volume by the number
    of instruments observed. When a log is supplied this function mutates it and
    leaves persistence to the caller; when it is not, the original
    load-append-save behaviour is preserved exactly.
    """
    moment = now or datetime.now(timezone.utc)
    key = observation_key(instrument, horizon, moment)
    bucket = int(moment.timestamp()) // OBSERVATION_BUCKET_SECONDS
    record_id = observation_record_id(key)
    batched = shadow_log is not None

    def _persist(log_to_save: ShadowLog) -> None:
        if not batched:
            save_shadow_log(store, log_to_save)

    # Cheapest possible duplicate check first: this process already did it.
    if _HANDLED_BUCKETS.get(instrument) == bucket:
        _bump("duplicate_skipped")
        return "duplicate_skipped"

    log = shadow_log if shadow_log is not None else load_shadow_log(store)
    if log.contains(record_id):
        _HANDLED_BUCKETS[instrument] = bucket
        log.bump("duplicate_skipped")
        _persist(log)
        _bump("duplicate_skipped")
        return "duplicate_skipped"

    status, evaluation = _build_observation(
        instrument,
        fred_key,
        channel_name,
        moment=moment,
        horizon=horizon,
        observation_identity=key,
    )
    if status == "unknown_instrument":
        _bump("unknown_instrument")
        return "unknown_instrument"
    if status != "ok" or evaluation is None:
        _HANDLED_BUCKETS[instrument] = bucket
        log.bump("insufficient_data_skipped")
        _persist(log)
        _bump("insufficient_data_skipped")
        return "insufficient_data_skipped"

    try:
        log.append(evaluation.record)
    except ShadowLogError:
        _HANDLED_BUCKETS[instrument] = bucket
        log.bump("duplicate_skipped")
        _persist(log)
        _bump("duplicate_skipped")
        return "duplicate_skipped"

    _HANDLED_BUCKETS[instrument] = bucket
    log.bump("written")
    _persist(log)
    _bump("written")

    # Pre-register the asset module's transmission chain so the claim can be
    # tested later. Deliberately AFTER the shadow record is safely persisted and
    # separately guarded: a prediction-log failure must never cost us the
    # observation we already have. Validation infrastructure only -- outcomes
    # never feed back into production, and resolving them stays manual.
    try:
        register_transmission_prediction(
            store,
            instrument=instrument,
            direction=evaluation.direction,
            horizon=horizon,
            now=moment,
        )
    except Exception:
        _bump("exception_swallowed")
    return "written"


def _run_v2_observation(
    fred_key: str,
    channel_name: str,
    *,
    backend: Any,
    moment: datetime,
    bucket: int,
    due: list[str],
    record_store: Any | None = None,
) -> dict[str, str]:
    """Storage V2 path: one immutable row per observation.

    No history is loaded to append. Nothing rewrites a growing blob. The
    database primary key is the durable duplicate authority, so suppression
    survives a restart without reading any past record.

    ``_HANDLED_BUCKETS`` is only marked for instruments whose row actually
    settled (inserted, or already present). A record that FAILED to persist is
    deliberately left unmarked so the next tick retries it, rather than being
    silently lost for the hour.
    """
    rows: list[dict[str, Any]] = []
    evaluations: dict[str, Any] = {}
    outcomes: dict[str, str] = {}
    store_for_rows = record_store if record_store is not None else resolve_record_store()

    for instrument in due:
        try:
            key = observation_key(instrument, Horizon.TACTICAL, moment)

            # Durable cadence check BEFORE any work. The physical key can no
            # longer enforce one-observation-per-hour, so the live path asks
            # explicitly. One indexed lookup, never a history load. Unknown is
            # not treated as "already stored": losing evidence is worse than an
            # extra legitimate observation, and the row would be distinct anyway.
            already = store_for_rows.logical_record_exists(
                observation_record_id(key)
            )
            if already is True:
                _HANDLED_BUCKETS[instrument] = bucket
                _bump("v2_logical_duplicate")
                _bump("duplicate_skipped")
                outcomes[instrument] = "duplicate_skipped"
                continue
            if already is None:
                _bump("v2_dedup_check_unavailable")

            status, evaluation = _build_observation(
                instrument,
                fred_key,
                channel_name,
                moment=moment,
                horizon=Horizon.TACTICAL,
                observation_identity=key,
            )
        except Exception:
            _bump("exception_swallowed")
            outcomes[instrument] = "exception_swallowed"
            continue

        if status == "unknown_instrument":
            _bump("unknown_instrument")
            outcomes[instrument] = "unknown_instrument"
            continue
        if status != "ok" or evaluation is None:
            _HANDLED_BUCKETS[instrument] = bucket
            _bump("insufficient_data_skipped")
            outcomes[instrument] = "insufficient_data_skipped"
            continue

        row = record_to_row(evaluation.record.as_record())
        if not row:
            _bump("v2_failed")
            outcomes[instrument] = "failed"
            continue
        rows.append(row)
        evaluations[row["storage_id"]] = (instrument, evaluation)

    if not rows:
        return outcomes

    try:
        result = store_for_rows.insert_rows(rows)
    except Exception as exc:
        _bump("exception_swallowed")
        result = InsertOutcome(
            backend="unavailable",
            durable=False,
            failed=tuple(r["storage_id"] for r in rows),
            error=str(exc)[:200],
        )

    if result.backend == "local":
        _bump("v2_local_fallback")

    settled = set(result.settled)
    for storage_id, (instrument, evaluation) in evaluations.items():
        if storage_id in result.inserted:
            _HANDLED_BUCKETS[instrument] = bucket
            _bump("v2_inserted")
            _bump("written")
            outcomes[instrument] = "written"
        elif storage_id in result.duplicate:
            _HANDLED_BUCKETS[instrument] = bucket
            _bump("v2_duplicate")
            _bump("duplicate_skipped")
            outcomes[instrument] = "duplicate_skipped"
        elif storage_id in result.conflicted:
            # Two payloads claim one point-in-time identity. Nothing is
            # overwritten and the bucket stays unmarked; this needs a human.
            _bump("v2_identity_conflict")
            outcomes[instrument] = "identity_conflict"
        else:
            # Not settled: leave the bucket unmarked so the next tick retries.
            _bump("v2_failed")
            outcomes[instrument] = "failed"

    # Predictions only for observations that actually persisted, and only after
    # the record is safe. A prediction-log failure never costs the observation.
    for storage_id, (instrument, evaluation) in evaluations.items():
        if storage_id not in settled:
            continue
        try:
            register_transmission_prediction(
                backend,
                instrument=instrument,
                direction=evaluation.direction,
                horizon=Horizon.TACTICAL,
                now=moment,
            )
        except Exception:
            _bump("exception_swallowed")

    # One bounded migration step per tick, after the live observation is safe.
    try:
        advance_migration(backend, store_for_rows)
    except Exception:
        _bump("exception_swallowed")

    return outcomes


def run_shadow_observation(
    fred_key: str,
    channel_name: str,
    *,
    store: Any | None = None,
    now: datetime | None = None,
) -> dict[str, str]:
    """Entry point for the production daemon hook.

    Never raises: every per-instrument failure is caught and counted, so one
    broken instrument cannot stop the others and nothing can propagate back into
    the production loop.

    Persistence is batched. The shadow log is loaded once, every instrument
    appends into that one in-memory log, and it is saved once at the end. This
    is what keeps multi-asset observation write-neutral: the whole payload is
    rewritten on each save, so saving per instrument would multiply upload
    volume by the instrument count. A load or save failure is contained here and
    costs at most one tick of observations, never the production loop.
    """
    _bump("attempted")
    if not shadow_enabled():
        _bump("disabled")
        return {}

    backend = store if store is not None else ProductionShadowStore()
    moment = now or datetime.now(timezone.utc)
    bucket = int(moment.timestamp()) // OBSERVATION_BUCKET_SECONDS
    instruments = shadow_instruments()

    # Fast path first, before touching the store at all. On 59 of every 60 ticks
    # nothing is due, and loading the log to discover that would mean reading the
    # entire payload once a minute.
    due = [i for i in instruments if _HANDLED_BUCKETS.get(i) != bucket]
    outcomes: dict[str, str] = {
        i: "duplicate_skipped" for i in instruments if i not in due
    }
    for _ in outcomes:
        _bump("duplicate_skipped")
    if not due:
        return outcomes

    if shadow_store_mode() == STORAGE_MODE_V2:
        return {**outcomes, **_run_v2_observation(
            fred_key, channel_name, backend=backend, moment=moment,
            bucket=bucket, due=due,
        )}

    try:
        batch = load_shadow_log(backend)
    except Exception:
        _bump("exception_swallowed")
        return {**outcomes, **{i: "exception_swallowed" for i in due}}

    before = len(batch)

    for instrument in due:
        try:
            outcome = observe_instrument(
                instrument,
                fred_key,
                channel_name,
                store=backend,
                now=moment,
                shadow_log=batch,
            )
        except Exception:
            # One instrument failing must never stop the rest, and must never
            # discard the observations already collected in this batch.
            _bump("exception_swallowed")
            outcome = "exception_swallowed"
        outcomes[instrument] = outcome

    if len(batch) != before:
        try:
            save_shadow_log(backend, batch)
        except Exception:
            _bump("exception_swallowed")

    return outcomes


def get_shadow_hook_stats() -> dict[str, int]:
    """In-process hook counters, for an operator or an admin panel to read."""
    return dict(HOOK_STATS)


# ===========================================================================
# TRANSMISSION PREDICTION REGISTRATION
#
# Asset modules declare the chain they claim their thesis should travel along.
# Registering it in advance is what makes the claim testable later. This is
# validation infrastructure only: prediction outcomes never feed back into any
# production behaviour, and outcome attachment stays manual -- no scheduler is
# created to resolve them.
# ===========================================================================

#: One prediction per instrument per UTC day. A transmission claim is a
#: statement about a thesis, not about a particular hour, so re-registering it
#: hourly would bloat the log without adding a testable claim.
PREDICTION_BUCKET_SECONDS = 24 * 3600

#: Expected windows per horizon, taken from the horizon's own evaluation window
#: rather than invented: a step should show up well inside the horizon it
#: belongs to, so each step is given a fraction of that window.
_STEP_WINDOW_FRACTION = (0.25, 0.5)


def prediction_identity(instrument: str, horizon: Horizon, moment: datetime) -> str:
    bucket = int(moment.timestamp()) // PREDICTION_BUCKET_SECONDS
    return f"b2pred|{instrument}|{horizon.value}|{bucket}"


def register_transmission_prediction(
    store: Any,
    *,
    instrument: str,
    direction: Direction,
    horizon: Horizon = Horizon.TACTICAL,
    now: datetime | None = None,
) -> str:
    """Pre-register the asset module's transmission chain for this thesis.

    Returns registered / duplicate_skipped / no_module / no_direction.
    """
    module = module_for(instrument)
    chain = getattr(module, "TRANSMISSION_CHAIN", ()) if module else ()
    if not chain:
        return "no_module"
    if not direction.is_directional:
        # There is no claim to test without a directional thesis.
        return "no_direction"

    moment = now or datetime.now(timezone.utc)
    identity = prediction_identity(instrument, horizon, moment)
    window = HORIZON_EVALUATION_WINDOW[horizon]

    steps = tuple(
        TransmissionStep(
            index=index,
            source=source,
            target=target,
            expected_direction=direction,
            expects_within=window
            * _STEP_WINDOW_FRACTION[min(index, len(_STEP_WINDOW_FRACTION) - 1)],
            rationale=rationale,
        )
        for index, (source, target, rationale) in enumerate(chain)
    )

    record = build_prediction(
        horizon=horizon,
        thesis_direction=direction,
        instrument=instrument,
        steps=steps,
        created_at=moment,
        identity_key=identity,
    )

    log = load_prediction_log(store)
    try:
        log.append(record)
    except PredictionLogError:
        _bump("prediction_duplicate")
        return "duplicate_skipped"

    save_prediction_log(store, log)
    _bump("prediction_registered")
    return "registered"


__all__ = [
    "HOOK_COUNTERS",
    "HOOK_STATS",
    "OBSERVATION_BUCKET_SECONDS",
    "default_shadow_instruments",
    "PREDICTION_LOG_FILE",
    "PREDICTION_LOG_STATE_ID",
    "ProductionShadowStore",
    "SHADOW_LOG_FILE",
    "SHADOW_LOG_STATE_ID",
    "evaluate_from_production",
    "get_shadow_hook_stats",
    "load_prediction_log",
    "load_shadow_log",
    "BACKFILL_BATCH_SIZE",
    "IDENTITY_MODEL_VERSION",
    "InsertOutcome",
    "verify_storage_identity_parity",
    "LOCAL_RECORDS_FILE",
    "LocalShadowRecordStore",
    "MIGRATION_STATE_ID",
    "PREDICTION_BUCKET_SECONDS",
    "SHADOW_RECORDS_TABLE",
    "STORAGE_MODE_LEGACY",
    "STORAGE_MODE_V2",
    "SupabaseShadowRecordStore",
    "advance_migration",
    "backfill_legacy_records",
    "canonical_payload_hash",
    "freeze_legacy_shadow_log",
    "legacy_frozen_state_id",
    "migration_status",
    "resolve_record_store",
    "shadow_store_mode",
    "observation_key",
    "observation_record_id",
    "observe_instrument",
    "prediction_identity",
    "record_evaluation",
    "register_transmission_prediction",
    "run_shadow_observation",
    "save_prediction_log",
    "save_shadow_log",
    "shadow_enabled",
    "shadow_instruments",
    "signals_from_production",
]
