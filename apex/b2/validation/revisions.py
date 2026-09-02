"""Architecture B2 -- vendor revisions of an already-stored market bar.

``b2_market_observations`` is append-only: an existing row is never updated and
never overwritten. That is the right guarantee, and it leaves one question
unanswered. When a vendor re-reports a bar we already stored with DIFFERENT
values, the store correctly refuses the write and reports a conflict -- but the
knowledge that the vendor changed its mind then exists only in a capture report
that nobody keeps.

The live case is settled and understood. Yahoo publishes a provisional volume
for the newest closed daily bar and corrects it later; on the 2026-08-31
capture, GC=F, CL=F and NQ=F each carried a provisional volume for their
2026-08-28 bar, and each was subsequently revised. Open, high, low and close
were bit-identical across both captures for every one of them.

A revision is therefore a SEPARATE point-in-time fact, not a correction of the
original observation. The original stays exactly as captured -- it is what we
knew when a prediction over that window would have been resolved -- and the
revision records, immutably, that a different payload later claimed the same
bar. Neither replaces the other.

Classifying a revision without a float-comparison bug
----------------------------------------------------
PostgREST returns ``double precision`` columns at 15 significant digits, so a
stored ``4033.699951171875`` reads back as ``4033.69995117188``. Measured across
all eleven captured symbols, that truncation accounts for 100% of apparent OHLC
differences and there are no real ones. A classifier that asked
``float(stored) != float(fresh)`` would therefore report a spurious price
revision on almost every FX row.

So this module never treats a read-back float as an authority:

*   ``volume_only`` is proven by HASH PROBE, not by comparison. Substituting the
    stored volume into the fresh OHLC and reproducing the stored content hash
    is bit-exact proof that the stored OHLC equalled the fresh OHLC and that
    volume is the only field that moved.
*   Only if that probe fails is OHLC compared at all, and then at the
    transport's own 15-significant-digit precision on BOTH sides -- like with
    like. Fifteen significant digits is many orders of magnitude finer than any
    real price revision, so that comparison can only fire on a genuine change.
*   Anything the hash cannot attribute is ``other``. It escalates rather than
    being quietly labelled benign.

This module is pure. It performs no I/O, holds no clock and reads no record;
the client that writes revisions lives in ``apex.b2_validation_bridge``.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from .bars import (
    MarketBar,
    MarketObservationError,
    canonical_bar_content_hash,
)

#: Domain tag in the revision identity basis. It makes the revision and
#: observation hash domains provably disjoint rather than merely unlikely to
#: collide, at the cost of three characters.
_REVISION_DOMAIN = "rev"

#: Same separator the observation identity uses. Cannot occur in a hex hash or
#: a 32-hex identity.
_IDENTITY_SEPARATOR = "|"

#: The measured fields a revision can move. Volume is handled separately
#: because it is the only nullable one.
OHLC_FIELDS = ("open", "high", "low", "close")
VALUE_FIELDS = OHLC_FIELDS + ("volume",)

#: Recorded when a payload differs but the difference cannot be attributed to a
#: specific field. The table requires at least one named field, and an honest
#: "we could not tell" is better than a guess that names the wrong one.
UNATTRIBUTED = "unattributed"


class RevisionKind(str, Enum):
    """What a vendor actually changed.

    ``VOLUME_ONLY``
        Proven bit-exactly by hash probe. The expected, already-diagnosed
        behaviour for a futures symbol's most recently closed bar.
    ``PRICE``
        At least one of open/high/low/close differs at the transport's own
        precision. Not expected, and materially different from a volume
        revision: it changes what the market is recorded as having done.
    ``OTHER``
        A real difference that could not be attributed. Escalated, never
        downgraded.
    """

    VOLUME_ONLY = "volume_only"
    PRICE = "price"
    OTHER = "other"


def canonical_revision_id(observation_id: str, revised_content_hash: str) -> str:
    """Deterministic durable identity for one revision of one bar.

    Deliberately a function of the observation and the REVISED CONTENT ONLY.

    *   No clock. Including ``first_seen_at`` would give every re-capture of the
        same revision a fresh identity, and the same vendor correction would be
        re-recorded on every run forever.
    *   No ``revision_kind``. The kind is DERIVED from the payload, so including
        it would let a change to the classifier silently fork identity.
    *   No ordinal. A monotonic counter cannot be assigned idempotently without
        a read-modify-write transaction, which would cost both the append-only
        and the fail-safe guarantee. Revisions are ordered by ``first_seen_at``
        on read instead.

    The consequence is exactly the required semantics: re-seeing a revision
    deduplicates onto the existing row and preserves its original
    ``first_seen_at``, while a LATER, DIFFERENT revision hashes differently and
    appends as a new row beside it.

    Same construction and same 32-hex width as ``canonical_observation_id`` and
    ``canonical_bar_content_hash``, so all three identities read alike in a
    database and in a log.
    """
    basis = _IDENTITY_SEPARATOR.join(
        [_REVISION_DOMAIN, str(observation_id), str(revised_content_hash)]
    )
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:32]


def transport_precision(value: Any) -> float | None:
    """A float at the precision the PostgREST JSON transport actually preserves.

    ``double precision`` columns come back as 15 significant digits, so this is
    the ONLY precision at which a stored value and a freshly fetched value may
    be compared. Applying it to an already-shortened value is a no-op, which is
    what makes it safe to apply to both sides.

    Returns None for anything unreadable rather than guessing a number.
    """
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return float(f"{number:.15g}")


@dataclass(frozen=True)
class RevisionClassification:
    """What changed, and how confidently we know it."""

    kind: RevisionKind
    changed_fields: tuple[str, ...]

    @property
    def is_volume_only(self) -> bool:
        return self.kind is RevisionKind.VOLUME_ONLY


def classify_revision(
    *,
    original_content_hash: str,
    stored_row: Mapping[str, Any] | None,
    bar: MarketBar,
) -> RevisionClassification:
    """Attribute a conflict to the fields that actually moved.

    ``original_content_hash`` MUST be the stored ``content_hash`` COLUMN -- the
    authoritative integrity witness, computed from full-precision values at
    insert time. It is never recomputed here from ``stored_row``'s numerics,
    which have already lost precision in transport.

    ``bar`` is the freshly fetched bar, at full precision, straight from the
    vendor payload.
    """
    stored = stored_row if isinstance(stored_row, Mapping) else {}

    # STEP 1 -- the bit-exact proof. If the fresh OHLC combined with the STORED
    # volume reproduce the stored content hash, then the stored OHLC were
    # bit-identical to the fresh OHLC and volume is the only field that moved.
    # No float is compared to reach this verdict.
    stored_volume = stored.get("volume")
    probe_volume = transport_precision(stored_volume) if stored_volume is not None else None
    if stored_volume is None or probe_volume is not None:
        probe = canonical_bar_content_hash(
            bar.open, bar.high, bar.low, bar.close, probe_volume
        )
        if probe == str(original_content_hash):
            return RevisionClassification(RevisionKind.VOLUME_ONLY, ("volume",))

    # STEP 2 -- the probe did not reconcile. A field-level verdict now requires
    # reading the stored numerics, so refuse to give one if they cannot be read.
    if any(stored.get(field) is None for field in OHLC_FIELDS):
        return RevisionClassification(RevisionKind.OTHER, (UNATTRIBUTED,))

    changed: list[str] = []
    for field in OHLC_FIELDS:
        fresh = transport_precision(getattr(bar, field))
        held = transport_precision(stored.get(field))
        if fresh is None or held is None or fresh != held:
            changed.append(field)

    fresh_volume = transport_precision(bar.volume) if bar.volume is not None else None
    held_volume = probe_volume if stored_volume is not None else None
    if (bar.volume is None) != (stored_volume is None) or fresh_volume != held_volume:
        changed.append("volume")

    if any(field in changed for field in OHLC_FIELDS):
        return RevisionClassification(RevisionKind.PRICE, tuple(changed))

    # A real hash difference that survives every attribution attempt. Reported
    # as unexplained rather than filed under a kind it was not proven to be.
    return RevisionClassification(
        RevisionKind.OTHER, tuple(changed) if changed else (UNATTRIBUTED,)
    )


@dataclass(frozen=True)
class MarketBarRevision:
    """One immutable revision of one already-stored market bar."""

    observation_id: str
    original_content_hash: str
    #: The REVISED bar, as the vendor reported it at first sight of this payload.
    bar: MarketBar
    kind: RevisionKind
    changed_fields: tuple[str, ...]
    #: The capture run's own reference clock, ISO-8601. Distinct from
    #: ``first_seen_at``, which the DATABASE stamps and the client never sends,
    #: so a clock disagreement stays visible instead of being averaged away.
    captured_at: str
    resolver_version: str
    meta: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if not str(self.observation_id).strip():
            raise MarketObservationError("A revision must name the observation it revises.")
        if not str(self.original_content_hash).strip():
            raise MarketObservationError(
                "A revision must record the original content hash it differs from."
            )
        if self.bar.observation_id != str(self.observation_id):
            raise MarketObservationError(
                "A revision must carry the SAME physical identity as the "
                "observation it revises; a revision of a different bar is not a "
                "revision, it is a second bar."
            )
        if self.bar.content_hash == str(self.original_content_hash):
            raise MarketObservationError(
                "A revision must DIFFER from the observation it revises. An "
                "identical payload is a duplicate, and the database refuses it "
                "too (b2_mor_hash_differs_ck)."
            )
        if not tuple(self.changed_fields):
            raise MarketObservationError("A revision must name at least one changed field.")

    @property
    def revised_content_hash(self) -> str:
        return self.bar.content_hash

    @property
    def revision_id(self) -> str:
        return canonical_revision_id(self.observation_id, self.revised_content_hash)

    def to_row(self) -> dict[str, Any]:
        """Map onto one ``b2_market_observation_revisions`` row.

        ``first_seen_at`` is deliberately ABSENT. The database defaults it to
        its own ``now()``, so the moment a payload was first observed cannot be
        backdated by a client, and ON CONFLICT DO NOTHING means a re-capture
        never moves it.

        The ORIGINAL values are not copied either -- only
        ``original_content_hash``. ``b2_market_observations`` is append-only, so
        a join to it can never go stale, and a copy could only ever disagree.
        """
        return {
            "revision_id": self.revision_id,
            "observation_id": self.observation_id,
            "original_content_hash": str(self.original_content_hash),
            "revised_content_hash": self.revised_content_hash,
            "revision_kind": self.kind.value,
            "symbol": self.bar.symbol,
            "granularity": self.bar.granularity,
            "bar_time": self.bar.bar_time_iso,
            "price_source": self.bar.price_source,
            "open": float(self.bar.open),
            "high": float(self.bar.high),
            "low": float(self.bar.low),
            "close": float(self.bar.close),
            "volume": None if self.bar.volume is None else float(self.bar.volume),
            "changed_fields": list(self.changed_fields),
            "captured_at": self.captured_at,
            "resolver_version": self.resolver_version,
            "meta": dict(self.meta) if self.meta else {},
        }


def build_revision(
    *,
    observation_id: str,
    original_content_hash: str,
    stored_row: Mapping[str, Any] | None,
    bar: MarketBar,
    captured_at: str,
    resolver_version: str,
    meta: Mapping[str, Any] | None = None,
) -> MarketBarRevision:
    """Classify a conflict and build the revision row it earns.

    Raises ``MarketObservationError`` when the inputs do not describe a genuine
    revision -- a mismatched identity, or a payload identical to the stored one.
    """
    classification = classify_revision(
        original_content_hash=original_content_hash, stored_row=stored_row, bar=bar
    )
    return MarketBarRevision(
        observation_id=str(observation_id),
        original_content_hash=str(original_content_hash),
        bar=bar,
        kind=classification.kind,
        changed_fields=classification.changed_fields,
        captured_at=captured_at,
        resolver_version=resolver_version,
        meta=meta,
    )


__all__ = [
    "OHLC_FIELDS",
    "UNATTRIBUTED",
    "VALUE_FIELDS",
    "MarketBarRevision",
    "RevisionClassification",
    "RevisionKind",
    "build_revision",
    "canonical_revision_id",
    "classify_revision",
    "transport_precision",
]
