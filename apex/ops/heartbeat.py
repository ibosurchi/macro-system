"""H8 -- durable operational health state.

One mutable row per job in ``b2_ops_job_health``. This is the answer to the
question the previous architecture could not answer at all: *when did each job
last actually succeed?* -- readable with the Streamlit application stopped,
surviving process death, redeploy and hibernation.

WHY A DATABASE AND NOT A FILE OR A LOG
--------------------------------------
The failure being fixed is that health lived in process memory. A file fails the
same way on an ephemeral host -- that is exactly the non-durable fallback this
project already has to detect elsewhere -- and a log needs an aggregator that
does not exist here. The database is the only store that survives the process,
is reachable when the application is down, and already has a credential, an RLS
posture and an append-only convention this table can follow.

THE ONE DELIBERATELY MUTABLE TABLE
----------------------------------
Every other B2 table revokes UPDATE from ``service_role``, because history must
not be rewritable. This one keeps it: it holds CURRENT state, not history, and
one row per job is the whole point. That distinction is documented in the
migration header as well, because it is the first exception in the system and an
unexplained exception is how a convention quietly dies.

HEALTH WRITING NEVER CHANGES AN EVIDENCE RESULT
-----------------------------------------------
Every function here returns an outcome instead of raising. A heartbeat failure
is an OPERATIONAL failure and is surfaced as one -- never swallowed -- but it
must not turn a successful capture into a failed one, or the observability layer
becomes a new way to lose evidence.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping

import requests

from .config import OpsSettings, ops_settings
from .logging import error_class, error_summary, utcnow

#: The operational health table. Created BY HAND from sql/003; this module never
#: emits schema statements, and the guard test asserts that.
HEALTH_TABLE = "b2_ops_job_health"

#: Own timeout. Health must never be the thing that stalls a capture.
HEALTH_TIMEOUT = 10


@dataclass(frozen=True)
class HealthOutcome:
    """Result of a health read or write. Never claims more than happened."""

    ok: bool
    backend: str  # "supabase" | "unconfigured" | "error"
    rows: tuple[Mapping[str, Any], ...] = ()
    error: str = ""

    def as_record(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "backend": self.backend,
            "rows": len(self.rows),
            "error": self.error,
        }


@dataclass
class JobHealth:
    """One job's health, as this run observed it.

    Built incrementally during a run and written once at the end, so a crash
    mid-job leaves the previous row intact rather than a half-updated one.
    """

    job_key: str
    run_id: str
    started_at: datetime = field(default_factory=utcnow)
    status: str = "unknown"
    logical_bucket: str = ""
    records_written: int = 0
    durable: bool = False
    error_class: str = ""
    error_summary: str = ""
    code_version: str = ""
    schema_version: int | None = None

    def mark(self, exc: BaseException | None) -> None:
        """Record a failure's class and redacted summary. Never a traceback."""
        self.error_class = error_class(exc)
        self.error_summary = error_summary(exc)

    def to_row(self, *, now: datetime | None = None) -> dict[str, Any]:
        """The row body. ``last_success_at`` moves ONLY on a real success.

        A non-durable completion is deliberately not a success: on an ephemeral
        host that evidence is gone at the next redeploy, so advancing
        ``last_success_at`` for it would make a stale corpus look fresh.
        """
        moment = (now or utcnow()).isoformat()
        succeeded = self.status == "success" and self.durable
        row: dict[str, Any] = {
            "job_key": self.job_key,
            "last_attempt_at": self.started_at.isoformat(),
            "last_status": self.status,
            "last_run_id": self.run_id,
            "last_records_written": int(self.records_written),
            "last_durable": bool(self.durable),
            "code_version": self.code_version,
            "updated_at": moment,
        }
        if self.logical_bucket:
            row["last_logical_bucket"] = self.logical_bucket
        if self.schema_version is not None:
            row["schema_version"] = int(self.schema_version)
        if succeeded:
            row["last_success_at"] = moment
        else:
            row["last_failure_at"] = moment
            row["last_error_class"] = self.error_class
            row["last_error_summary"] = self.error_summary
        return row


class HealthStore:
    """Supabase-backed health state. Imports no Streamlit and holds no session."""

    def __init__(
        self,
        settings: OpsSettings | None = None,
        *,
        table: str | None = None,
        timeout: int | None = None,
    ) -> None:
        self.settings = settings if settings is not None else ops_settings()
        self.table = table or HEALTH_TABLE
        self.timeout = timeout or HEALTH_TIMEOUT

    @property
    def available(self) -> bool:
        return self.settings.supabase_available

    def _url(self) -> str:
        return f"{self.settings.supabase_url}/rest/v1/{self.table}"

    def _headers(self, prefer: str = "") -> dict[str, str]:
        """Request headers.

        Built here rather than borrowed from ``production_core._supabase_headers``
        so this module stays free of the production import -- and therefore of
        Streamlit -- which is what lets health be read in a bare process.
        """
        headers = {
            "apikey": self.settings.supabase_key,
            "Authorization": f"Bearer {self.settings.supabase_key}",
            "Content-Type": "application/json",
        }
        if prefer:
            headers["Prefer"] = prefer
        return headers

    def write(self, health: JobHealth, *, now: datetime | None = None) -> HealthOutcome:
        """Upsert one job's health row.

        ``resolution=merge-duplicates`` on ``job_key`` makes this an upsert, so
        the first run for a job creates its row and every later run updates it.
        Only the keys present in the body are written, which is what lets the
        lease own its own columns on the same row without either overwriting the
        other.
        """
        if not self.available:
            return HealthOutcome(
                ok=False, backend="unconfigured", error="Supabase is not configured"
            )
        try:
            response = requests.post(
                self._url(),
                headers=self._headers("resolution=merge-duplicates,return=minimal"),
                params={"on_conflict": "job_key"},
                json=[health.to_row(now=now)],
                timeout=self.timeout,
            )
            response.raise_for_status()
        except Exception as exc:
            return HealthOutcome(ok=False, backend="error", error=error_summary(exc))
        return HealthOutcome(ok=True, backend="supabase")

    def read(self, job_key: str = "") -> HealthOutcome:
        """Current health for one job, or for every job when key is empty."""
        if not self.available:
            return HealthOutcome(
                ok=False, backend="unconfigured", error="Supabase is not configured"
            )
        params: dict[str, str] = {"select": "*", "order": "job_key.asc"}
        if job_key:
            params["job_key"] = f"eq.{job_key}"
        try:
            response = requests.get(
                self._url(), headers=self._headers(), params=params, timeout=self.timeout
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            return HealthOutcome(ok=False, backend="error", error=error_summary(exc))
        rows = tuple(payload) if isinstance(payload, list) else ()
        return HealthOutcome(ok=True, backend="supabase", rows=rows)


__all__ = [
    "HEALTH_TABLE",
    "HEALTH_TIMEOUT",
    "HealthOutcome",
    "HealthStore",
    "JobHealth",
]
