"""Architecture B2 -- Stage D-2D0: the per-observation orchestrator.

D-2C2, D-2C3, D-2C4 and D-2C5 are each frozen, tested and individually
correct, and until now NOTHING outside the test suite ran them together --
so no ``ValidationEnvelope`` had a producer at all. This module is that
producer. It is an ORCHESTRATION stage and nothing else: it calls the four
existing entry points in their approved order and binds their results, and
it contains no band arithmetic, no excursion measurement, no invalidation
scan, no readiness rule and no hash of its own.

The order is fixed and load-bearing:

    1. ``resolve_direction_and_path``  (D-2C2)  -- direction and path
    2. ``resolve_setup_and_execution`` (D-2C3)  -- setup and execution axes
    3. ``build_verified_envelope``     (D-2C5 -> D-2C4) -- lineage THEN envelope
    4. ``classify_readiness``          (D-2C5)  -- evidence-quality tier

Step 3 deliberately goes through D-2C5's ``build_verified_envelope`` rather
than calling D-2C4's ``build_validation_envelope`` directly. Bypassing the
verification would rebuild exactly the exposure D-2C5 was written to close:
an envelope assembled from artifacts that cannot belong to the same
observation. ``classify_readiness`` runs AFTER verification for the same
reason -- a tier assigned to an unverified composition would be a statement
about nothing.

**A composition defect is not a market outcome.** ``LineageError`` says a
caller handed this module artifacts that cannot belong together; an
``OutcomeInvariantError`` says D-2C2 and D-2C3 combined into a state the
architecture declared impossible. Neither says anything whatever about the
market, and neither may be allowed to abort a batch partway through or to
be rendered as a directional result. Both come back as an explicit,
immutable :class:`LineageDefect` carrying a stage-owned reason code. There
is no path in this module that turns a defect into ``FAILED``,
``CONFIRMED``, ``NEUTRAL_WITHIN_BAND`` or ``ABSTAINED``.

This module is pure. It reads no clock -- every moment comes from the
injected ``as_of``, exactly as ``resolve_direction_and_path`` already
requires -- opens no file, issues no request, starts nothing, persists
nothing, and imports neither ``production_core`` nor either bridge.

D-2D0 is NOT aggregation. Cohorts, deduplication, overlap structure,
effective sample size, ratios and sample floors are D-2D1 and are absent
here rather than stubbed. ``overlap_context`` is deliberately NOT exposed:
finding "the previous same-instrument observation" is a query across a SET
of observations, which is precisely the boundary this stage stops at.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Mapping, Sequence

from ..enums import Direction
from ..modules import module_for
from ..shadow import canonical_storage_id
from ..validation.anchor import AnchorStatus, SymbolConvention
from ..validation.bars import MarketBar
from ..validation.config import DEFAULT_VALIDATION_CONFIG, ValidationConfig
from ..validation.outcome import OutcomeInvariantError
from ..validation.series import SeriesBindingQuality

# Absolute, not relative, imports for the four nominal D-2C entry points.
# This is deliberate and mirrors the choice envelope.py already documents:
# the "nothing else imports me" guards on resolve.py / invalidation.py /
# envelope.py / readiness.py detect an importer by scanning an ImportFrom's
# module name for the substrings "validation.resolve",
# "validation.invalidation", "validation.envelope" and
# "validation.readiness", which a same-package relative import would
# silently NOT match -- defeating each guard rather than satisfying it.
# Written in absolute form, the guards continue to see this module and to
# verify that it is the ONE further approved importer authorized by Stage
# D-2D0, instead of going blind.
from apex.b2.validation.envelope import ValidationEnvelope
from apex.b2.validation.invalidation import resolve_setup_and_execution
from apex.b2.validation.readiness import (
    LineageError,
    ReadinessTier,
    build_verified_envelope,
    classify_readiness,
)
from apex.b2.validation.resolve import resolve_direction_and_path

#: Version of the D-2D0 composition CONTRACT -- the call order above and the
#: shape of what comes back from it. Deliberately separate from
#: ``VALIDATION_SCHEMA_VERSION`` (which versions the envelope and its three
#: hashes) and from ``ValidationConfig.version``/``.config_hash`` (which
#: version the research values), exactly as those two are already kept
#: orthogonal to each other. Nothing here is hashed: D-2D0 introduces no
#: identity of its own and reuses D-2C4's unchanged.
EVALUATION_SCHEMA_VERSION = "b2-evaluation-observation-v1"


def _payload(record: Mapping[str, Any]) -> Mapping[str, Any]:
    """The shadow record payload, whether wrapped in a storage row or not.

    Duplicated in miniature from ``resolve.py``/``invalidation.py``/
    ``envelope.py``/``readiness.py`` rather than imported from any of them --
    each already carries its own copy of this three-line helper by explicit
    design decision, and reaching across a module boundary for a private,
    unexported name would gain nothing.
    """
    inner = record.get("record") if isinstance(record, Mapping) else None
    return inner if isinstance(inner, Mapping) else record


# ===========================================================================
# A. PROVENANCE GRADE
#
# A label over provenance facts D-2C2 ALREADY established. It computes
# nothing about the market, and it deliberately does NOT influence
# eligibility or readiness: EligibilityPool is decided by resolve._pool_for
# and ReadinessTier by readiness.classify_readiness, both unchanged. This is
# a SEPARATE, reportable axis, so that a later reader can refuse to pool
# ideal and degraded evidence without that refusal having been silently
# pre-applied here.
# ===========================================================================

class ProvenanceGrade(Enum):
    """How trustworthy the point-in-time provenance behind one observation is.

    Orthogonal to outcome and to readiness. Answers only "which anchor and
    which market series actually resolved this", never "was the claim right"
    and never "may this be calibrated on".

    ``UNAVAILABLE`` is present because the classification must be TOTAL over
    every state D-2C2 can produce, and an observation with no anchor at all
    (or no bindable series at all) has no provenance to grade. Naming that
    explicitly is the only honest option: silently folding it into
    ``DEGRADED`` would assert a reconstructed anchor and a substituted
    series that were never established, which is exactly the invented
    provenance this architecture forbids -- the same reason
    ``Direction.UNAVAILABLE`` is not spelled ``FLAT`` and
    ``RegimeState.UNAVAILABLE`` is not spelled ``RANGE``.
    """

    IDEAL = "ideal"
    SUBSTITUTED_SERIES = "substituted_series"
    RECONSTRUCTED_ANCHOR = "reconstructed_anchor"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


def classify_provenance(
    *,
    anchor_status: AnchorStatus,
    binding_quality: SeriesBindingQuality,
    cross_source: bool,
) -> ProvenanceGrade:
    """Grade one observation's provenance from facts D-2C2 already resolved.

    Total and deterministic over every combination the current
    ``AnchorStatus`` x ``SeriesBindingQuality`` product admits. Reads no
    record, no bar and no clock, and makes no statistical judgment.

    ``cross_source`` is treated as disqualifying in exactly the way
    ``resolve._pool_for`` already treats it. Today it is redundant with
    ``SERIES_SUBSTITUTED`` by construction (``bind_series`` sets
    ``cross_source = not exact``), but it is checked rather than assumed: if
    the two ever disagreed, bars from a genuinely different market series
    must not be graded ``IDEAL`` on the strength of a binding label.

    Gold needs and gets no special case. An ``XAUUSD=X`` anchor resolved
    against ``GC=F`` daily bars is a substituted, cross-source binding and
    grades accordingly; a ``GC=F`` anchor -- which happens when production's
    own 5-minute fetch fell back -- is an exact binding and grades
    accordingly. Both readings fall straight out of the existing series
    logic rather than out of a rule about Gold.
    """
    if (
        anchor_status is AnchorStatus.MISSING
        or binding_quality is SeriesBindingQuality.SERIES_UNAVAILABLE
    ):
        return ProvenanceGrade.UNAVAILABLE

    substituted = (
        binding_quality is SeriesBindingQuality.SERIES_SUBSTITUTED or bool(cross_source)
    )

    if anchor_status is AnchorStatus.CAPTURED:
        return (
            ProvenanceGrade.SUBSTITUTED_SERIES if substituted else ProvenanceGrade.IDEAL
        )
    return (
        ProvenanceGrade.DEGRADED if substituted else ProvenanceGrade.RECONSTRUCTED_ANCHOR
    )


# ===========================================================================
# B. COMPOSITION DEFECT
# ===========================================================================

class DefectReason(Enum):
    """Why one observation could not be composed. Never why the market moved.

    Stage-owned rather than borrowed: ``ExclusionReason`` is D-2C2's
    vocabulary for describing the EVIDENCE, and reusing it here would blur
    "the market data was unusable" into "the caller assembled this wrong".
    Those are different failures with different owners and different fixes.
    """

    #: D-2C5's ``verify_lineage`` rejected the artifacts. ``LineageError``
    #: carries no structured reason -- only human-readable text -- so a
    #: deterministic stage-owned code is used for identity and the message
    #: is retained as diagnostic text only.
    LINEAGE_VERIFICATION_FAILED = "lineage_verification_failed"
    #: D-2C2 and D-2C3 combined into a state ``OutcomeAxes`` declares
    #: structurally impossible. Like a lineage mismatch this is a defect in
    #: the composition, not evidence about the claim, so it is reported the
    #: same way rather than being allowed to abort a batch.
    OUTCOME_INVARIANT_VIOLATED = "outcome_invariant_violated"
    #: The instrument has no registered Stage C asset module, so no asset
    #: class can be derived. Reported rather than guessed: an observation
    #: with an invented asset class would silently defeat the per-asset
    #: separation a later stage depends on.
    UNREGISTERED_INSTRUMENT = "unregistered_instrument"


@dataclass(frozen=True)
class LineageDefect:
    """One observation that could not be composed into an envelope.

    Deliberately minimal, and deliberately built only from stable,
    input-derived facts: the identity fields are read from the record's own
    payload and the storage id is produced by the SAME
    ``canonical_storage_id`` a real Storage-V2 row carries, so a defect and
    a successful result are keyed identically. No stack trace, no wall
    clock, no generated id, no path, nothing mutable.

    ``message`` is DIAGNOSTIC TEXT ONLY and is never an identity: two
    defects are the same defect when their storage id and ``reason`` agree,
    regardless of how an exception happened to phrase itself.
    """

    shadow_storage_id: str
    shadow_record_id: str
    instrument: str
    horizon: str
    evaluated_at: str
    reason: DefectReason
    message: str

    @property
    def is_defect(self) -> bool:
        """Always True. The cheap way for a caller to fork on the union type."""
        return True

    def as_record(self) -> dict[str, Any]:
        return {
            "stage": "d2d0",
            "evaluation_schema_version": EVALUATION_SCHEMA_VERSION,
            "defect": True,
            "reason": self.reason.value,
            "message": self.message,
            "shadow_storage_id": self.shadow_storage_id,
            "shadow_record_id": self.shadow_record_id,
            "instrument": self.instrument,
            "horizon": self.horizon,
            "evaluated_at": self.evaluated_at,
        }


# ===========================================================================
# C. THE EVALUATED OBSERVATION
# ===========================================================================

@dataclass(frozen=True)
class EvaluatedObservation:
    """One fully composed observation: the envelope plus what it lacks.

    Deliberately FIVE fields and no more. It is not a second envelope and
    must not become one: anything already on ``envelope.context`` or
    ``envelope.outcome_axes`` -- instrument, horizon, evaluated_at, the
    three hashes, anchor status, binding quality, eligibility pool,
    maturity, the six outcome axes, the excursion measures -- is read
    THROUGH the envelope and is never copied up to this level, where a copy
    could drift from the value that was actually hashed.

    The four additions each exist because the envelope genuinely does not
    carry them:

    *   ``readiness`` -- ``classify_readiness`` returns a free-standing tier
        bound to nothing. Binding it to the envelope it describes is the
        whole reason this type exists.
    *   ``claim_direction`` -- present inside ``outcome_hash_basis`` but not
        on ``ValidationContext`` or ``OutcomeAxes``.
    *   ``asset_class`` -- from the Stage C module registry, not the
        envelope.
    *   ``provenance_grade`` -- a derived label over three context fields,
        reported separately so provenance can be refused as a pooling
        dimension without that refusal being pre-applied to readiness.

    No aggregation field, no metric, no count, no rate, and no timestamp
    that was not already a point-in-time input.
    """

    envelope: ValidationEnvelope
    readiness: ReadinessTier
    claim_direction: Direction
    asset_class: str
    provenance_grade: ProvenanceGrade

    @property
    def is_defect(self) -> bool:
        """Always False. Mirrors ``LineageDefect.is_defect`` for the union."""
        return False

    def as_record(self) -> dict[str, Any]:
        """This observation's record. The envelope's half is DELEGATED.

        ``envelope.as_record()`` is called rather than reproduced, so there
        is exactly one definition of what an envelope looks like on the wire
        and this stage cannot drift from it.
        """
        return {
            "stage": "d2d0",
            "evaluation_schema_version": EVALUATION_SCHEMA_VERSION,
            "defect": False,
            "readiness_tier": self.readiness.value,
            "claim_direction": self.claim_direction.value,
            "asset_class": self.asset_class,
            "provenance_grade": self.provenance_grade.value,
            "envelope": self.envelope.as_record(),
        }


# ===========================================================================
# D. THE ORCHESTRATOR
# ===========================================================================

def evaluate_observation(
    *,
    record: Mapping[str, Any],
    bars: Sequence[MarketBar],
    as_of: datetime,
    convention: SymbolConvention | None = None,
    config: ValidationConfig | None = None,
    malformed_row_count: int | None = None,
) -> EvaluatedObservation | LineageDefect:
    """Compose one shadow observation into an evaluated result, or a defect.

    Every input is supplied. Nothing is fetched, queried, or read from a
    clock: ``as_of`` is the single reference moment, injected precisely so
    maturity -- and therefore ``outcome_hash``, which commits to
    ``maturity_state`` -- is deterministic and reproducible. The same
    ``record``, ``bars``, ``as_of``, ``convention``, ``config`` and
    ``malformed_row_count`` always produce the same result.

    ``convention`` is production's symbol mapping for this instrument,
    injected rather than imported for the same reason ``anchor.py`` gives:
    it lives in ``production_core``, which nothing under ``apex.b2`` may
    import. Omitting it is legal and simply narrows what can be bound.

    ``malformed_row_count`` is passed straight through to D-2C4, where it
    enters ``input_hash``. It defaults to ``None`` -- genuinely unavailable
    -- rather than to ``0``, because "no rows were malformed" and "nobody
    counted" are different claims and only the caller that read the rows
    knows which one is true.

    ``overlap_context`` is deliberately not a parameter. Locating the
    previous same-instrument observation is a query across a SET, which is
    D-2D1's boundary, not this one's.

    **Never raises for anything about the market.** An observation that
    cannot be judged comes back as a fully formed ``EvaluatedObservation``
    whose direction is ``UNRESOLVED``/``ABSTAINED``/``NEUTRAL_WITHIN_BAND``
    and whose readiness says so -- that is a result, not an error. Only a
    COMPOSITION defect returns ``LineageDefect``, and it returns rather than
    raises so that one bad observation cannot cost a future batch the rest
    of its records.
    """
    settings = config if config is not None else DEFAULT_VALIDATION_CONFIG
    payload = _payload(record)

    # -- identity: recomputed from the payload's own fields exactly as
    # build_validation_envelope does, never trusted from an optional
    # storage-row wrapper key, so a defect and a successful envelope are
    # keyed by the identical storage id. -----------------------------------
    record_id = str(payload.get("record_id") or "").strip()
    instrument = str(payload.get("instrument") or "").strip()
    horizon = str(payload.get("horizon") or "").strip()
    evaluated_at_raw = str(payload.get("evaluated_at") or "").strip()
    storage_id = canonical_storage_id(record_id, instrument, horizon, evaluated_at_raw)

    def _defect(reason: DefectReason, message: str) -> LineageDefect:
        return LineageDefect(
            shadow_storage_id=storage_id,
            shadow_record_id=record_id,
            instrument=instrument,
            horizon=horizon,
            evaluated_at=evaluated_at_raw,
            reason=reason,
            message=message,
        )

    # -- asset class: checked BEFORE any resolution work, because an
    # instrument with no registered module cannot produce a usable result no
    # matter what the market did, and failing fast keeps the defect's
    # identity derived purely from the record. -----------------------------
    module = module_for(instrument)
    if module is None:
        return _defect(
            DefectReason.UNREGISTERED_INSTRUMENT,
            f"instrument {instrument!r} has no registered Stage C asset module; "
            "asset class cannot be derived and is never guessed.",
        )
    asset_class = str(module.MODULE_KEY)

    # -- 1. D-2C2: direction and path --------------------------------------
    path_resolution = resolve_direction_and_path(
        record=record,
        bars=bars,
        now=as_of,
        convention=convention,
        config=settings,
    )

    # -- 2. D-2C3: setup invalidation and execution quality ----------------
    d2c3_resolution = resolve_setup_and_execution(
        record=record, path_resolution=path_resolution
    )

    # -- 3. D-2C5 -> D-2C4: verify lineage, THEN build the envelope. -------
    # build_verified_envelope runs verify_lineage first and only then calls
    # the unmodified build_validation_envelope, so no envelope is ever
    # constructed from artifacts that cannot belong together. Both failure
    # modes are composition defects, not market outcomes, and both are
    # returned rather than raised.
    try:
        envelope = build_verified_envelope(
            record=record,
            path_resolution=path_resolution,
            d2c3_resolution=d2c3_resolution,
            validation_config=settings,
            malformed_row_count=malformed_row_count,
        )
    except LineageError as exc:
        # Reached, among other ways, by an observation whose horizon this
        # configuration does not recognise: D-2C2 correctly returns
        # UNKNOWN_HORIZON with a stub maturity anchored on ``as_of``, and
        # D-2C5 correctly reports that this does not match the record's own
        # evaluated_at. Both stages are behaving as specified and neither is
        # touched here; the composition simply cannot be completed, which is
        # exactly what a defect says.
        return _defect(DefectReason.LINEAGE_VERIFICATION_FAILED, str(exc))
    except OutcomeInvariantError as exc:
        return _defect(DefectReason.OUTCOME_INVARIANT_VIOLATED, str(exc))

    # -- 4. D-2C5: readiness, AFTER verification ---------------------------
    readiness = classify_readiness(
        path_resolution=path_resolution, d2c3_resolution=d2c3_resolution
    )

    # -- 5. derived metadata the envelope does not carry -------------------
    provenance_grade = classify_provenance(
        anchor_status=path_resolution.anchor.status,
        binding_quality=path_resolution.binding.quality,
        cross_source=path_resolution.binding.cross_source,
    )

    return EvaluatedObservation(
        envelope=envelope,
        readiness=readiness,
        # claim.direction as D-2C2 itself resolved it, via the canonical
        # claim_direction helper. Read from the resolution rather than
        # re-derived: verify_lineage has, by this line, already proven the
        # two are the same value, so a second parse could only ever
        # introduce a way for them to disagree.
        claim_direction=path_resolution.claim_direction,
        asset_class=asset_class,
        provenance_grade=provenance_grade,
    )


__all__ = [
    "EVALUATION_SCHEMA_VERSION",
    "DefectReason",
    "EvaluatedObservation",
    "LineageDefect",
    "ProvenanceGrade",
    "classify_provenance",
    "evaluate_observation",
]
