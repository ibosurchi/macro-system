"""H8 -- distributed lease for Job A (shadow capture).

WHY JOB A NEEDS A LEASE AND THE OTHER JOBS DO NOT
-------------------------------------------------
Every B2 write path is already idempotent at the database: duplicate logical
evidence collapses on a primary key or a natural-key unique with ON CONFLICT DO
NOTHING. So a second concurrent run cannot CORRUPT anything.

What it can do is duplicate WORK. Shadow capture in an external runner starts
cold -- none of the production caches the in-daemon hook rode are warm -- so
each run issues a full set of upstream fetches for every instrument. Two
overlapping runs double that load on the data providers for no evidence gain.
The lease exists to prevent wasted upstream work, not to protect correctness.

Market bars and outcome evaluation are cheap and converge, so they rely on
idempotency alone. Adding leases there would be ceremony.

THE AI PROVIDER LEASE IS NOT REUSED
-----------------------------------
A distributed lease already exists elsewhere in this application, for the shared
AI batch supervisor. Its scope is that supervisor; nothing in the B2 path
acquires it, and borrowing it would couple B2 capture to AI provider lifecycle
for no reason. This is a separate lease with its own key.

ATOMICITY -- AND THE ONE ASSUMPTION IT MAKES
--------------------------------------------
Acquisition is a SINGLE conditional update::

    UPDATE b2_ops_job_health
       SET lease_owner = :owner, lease_expires_at = :expiry, ...
     WHERE job_key = :job
       AND (lease_expires_at IS NULL OR lease_expires_at < :as_of)
 RETURNING *

PostgREST issues exactly that as one statement, so the database does the work:
concurrent updates serialise on the row lock, and under READ COMMITTED the loser
re-evaluates its WHERE clause against the winner's committed row, no longer
matches, and updates zero rows. Returning the representation is what lets the
caller tell "I acquired it" from "someone else holds it" without a second query
and without a read-then-write race. **This is not a client-side check-then-act.**

The one assumption: ``:as_of`` is the RUNNER's UTC clock, not the database's.
PostgREST filter values are literals, so having the database evaluate ``now()``
would need a stored function -- a schema object beyond the single table this
stage authorises. The exposure is bounded and small: expiry is compared against
a clock that is NTP-synced on any scheduler runner, and the TTL is fifteen
minutes, which is orders of magnitude larger than any plausible skew. The
consequence of skew is only that a lease is considered expired slightly early or
late, never that two owners are granted at once -- that is prevented by the
database, not by the clock.

If strict database-clock authority is ever required, the minimum addition is one
``rpc`` function performing the same conditional update with ``now()`` inline.
That is deliberately NOT introduced here, because it would add a schema object
outside this stage's authorised surface.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

import requests

from .config import OpsSettings, ops_settings
from .heartbeat import HEALTH_TABLE, HEALTH_TIMEOUT
from .logging import error_summary, utcnow

#: The lease key for shadow capture. Named for the JOB, not the table, so the
#: operational vocabulary matches the runbook.
LEASE_CAPTURE_SHADOW = "b2_capture_shadow"

#: Which health row each lease key lives on. The lease columns share the job's
#: health row rather than occupying a second table: it is the same subject, one
#: row per job either way, and a second table would be a second thing to create,
#: grant and keep in step for no additional guarantee.
LEASE_JOB_KEY: dict[str, str] = {LEASE_CAPTURE_SHADOW: "capture_shadow"}

#: How long a lease survives without renewal. Generously longer than any
#: measured run, so a healthy job never races its own expiry, and short enough
#: that a crashed run does not block the next scheduled hour.
LEASE_TTL_SECONDS = 15 * 60


@dataclass(frozen=True)
class LeaseOutcome:
    """Result of a lease operation. ``acquired`` is never a guess."""

    acquired: bool
    owner: str
    key: str
    expires_at: datetime | None = None
    backend: str = "supabase"
    error: str = ""
    #: True when the attempt could not be evaluated at all -- a network failure
    #: rather than a live holder. Kept separate so a caller never reports "held
    #: by another run" for what was actually an outage.
    indeterminate: bool = False

    def as_record(self) -> dict[str, Any]:
        return {
            "lease_key": self.key,
            "lease_acquired": self.acquired,
            "lease_owner": self.owner,
            "lease_expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "lease_backend": self.backend,
            "lease_indeterminate": self.indeterminate,
            "lease_error": self.error,
        }


def new_owner_token() -> str:
    """A fresh owner identity per run. Never reused, never derived from a host."""
    return str(uuid.uuid4())


class LeaseStore:
    """Conditional-update lease over the operational health row."""

    def __init__(
        self,
        settings: OpsSettings | None = None,
        *,
        table: str | None = None,
        timeout: int | None = None,
        ttl_seconds: int = LEASE_TTL_SECONDS,
    ) -> None:
        self.settings = settings if settings is not None else ops_settings()
        self.table = table or HEALTH_TABLE
        self.timeout = timeout or HEALTH_TIMEOUT
        self.ttl_seconds = int(ttl_seconds)

    @property
    def available(self) -> bool:
        return self.settings.supabase_available

    def _url(self) -> str:
        return f"{self.settings.supabase_url}/rest/v1/{self.table}"

    def _headers(self, prefer: str = "") -> dict[str, str]:
        headers = {
            "apikey": self.settings.supabase_key,
            "Authorization": f"Bearer {self.settings.supabase_key}",
            "Content-Type": "application/json",
        }
        if prefer:
            headers["Prefer"] = prefer
        return headers

    def _ensure_row(self, job_key: str) -> bool:
        """Make sure the job's row exists, without disturbing an existing one.

        ``resolution=ignore-duplicates`` means an existing row is left exactly as
        it is -- including a live lease -- so this can never be a way to steal
        one by racing the row into existence.
        """
        try:
            response = requests.post(
                self._url(),
                headers=self._headers(
                    "resolution=ignore-duplicates,return=minimal"
                ),
                params={"on_conflict": "job_key"},
                json=[{"job_key": job_key}],
                timeout=self.timeout,
            )
            response.raise_for_status()
            return True
        except Exception:
            return False

    def acquire(
        self, key: str = LEASE_CAPTURE_SHADOW, *, owner: str = "", now: datetime | None = None
    ) -> LeaseOutcome:
        """Take the lease if it is free or expired. One atomic statement."""
        token = owner or new_owner_token()
        job_key = LEASE_JOB_KEY.get(key, key)
        if not self.available:
            return LeaseOutcome(
                acquired=False,
                owner=token,
                key=key,
                backend="unconfigured",
                error="Supabase is not configured",
                indeterminate=True,
            )

        moment = now or utcnow()
        expiry = moment + timedelta(seconds=self.ttl_seconds)
        if not self._ensure_row(job_key):
            # The row could not be created or confirmed, so the conditional
            # update below would match nothing and look exactly like "another
            # run holds it". Reporting that would silently skip capture forever
            # while the backend was down, so this is INDETERMINATE instead.
            return LeaseOutcome(
                acquired=False,
                owner=token,
                key=key,
                backend="error",
                error="lease row could not be created or confirmed",
                indeterminate=True,
            )

        try:
            response = requests.patch(
                self._url(),
                headers=self._headers("return=representation"),
                params={
                    "job_key": f"eq.{job_key}",
                    # Free, or expired against this run's clock. The database
                    # evaluates this predicate while holding the row lock.
                    "or": f"(lease_expires_at.is.null,lease_expires_at.lt.{moment.isoformat()})",
                    "select": "job_key,lease_owner,lease_expires_at",
                },
                json={
                    "lease_owner": token,
                    "lease_acquired_at": moment.isoformat(),
                    "lease_expires_at": expiry.isoformat(),
                },
                timeout=self.timeout,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            return LeaseOutcome(
                acquired=False,
                owner=token,
                key=key,
                backend="error",
                error=error_summary(exc),
                indeterminate=True,
            )

        rows = payload if isinstance(payload, list) else []
        if not rows:
            # Zero rows updated: the predicate did not match, so a live,
            # unexpired lease is held by another run. Not an error.
            return LeaseOutcome(acquired=False, owner=token, key=key)
        return LeaseOutcome(acquired=True, owner=token, key=key, expires_at=expiry)

    def release(
        self, key: str = LEASE_CAPTURE_SHADOW, *, owner: str, now: datetime | None = None
    ) -> LeaseOutcome:
        """Release the lease, but ONLY if this owner still holds it.

        The ``lease_owner`` filter is what stops a run whose lease already
        expired -- and was taken over by a newer run -- from releasing the new
        owner's lease on its way out.
        """
        job_key = LEASE_JOB_KEY.get(key, key)
        if not self.available:
            return LeaseOutcome(
                acquired=False, owner=owner, key=key, backend="unconfigured",
                error="Supabase is not configured", indeterminate=True,
            )
        try:
            response = requests.patch(
                self._url(),
                headers=self._headers("return=minimal"),
                params={"job_key": f"eq.{job_key}", "lease_owner": f"eq.{owner}"},
                json={"lease_owner": None, "lease_expires_at": None},
                timeout=self.timeout,
            )
            response.raise_for_status()
        except Exception as exc:
            return LeaseOutcome(
                acquired=False, owner=owner, key=key, backend="error",
                error=error_summary(exc), indeterminate=True,
            )
        return LeaseOutcome(acquired=False, owner=owner, key=key)


__all__ = [
    "LEASE_CAPTURE_SHADOW",
    "LEASE_JOB_KEY",
    "LEASE_TTL_SECONDS",
    "LeaseOutcome",
    "LeaseStore",
    "new_owner_token",
]
