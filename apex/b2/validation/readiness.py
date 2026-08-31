"""Architecture B2 -- Stage D-2C5: lineage verification and per-observation readiness.

D-2C2, D-2C3 and D-2C4 are each individually correct and individually
frozen. The gap this module closes sits BETWEEN them: nothing today verifies
that a ``DirectionPathResolution``, a ``D2C3Resolution`` and a
``ValidationConfig`` handed to ``build_validation_envelope`` together actually
belong to the same observation. That is currently a theoretical exposure --
no real orchestrator wires D-2C2 through D-2C4 together yet -- but the
guarantee has to exist BEFORE that wiring is written, not after.

This module adds exactly two things, and nothing else:

*   ``verify_lineage`` -- a cheap, deterministic set of consistency checks
    using ONLY signals that already exist on the current frozen dataclasses.
    No upstream file is modified, and no mutual fingerprint is invented and
    threaded backward into D-2C2/D-2C3. A mismatch is a PROGRAMMER /
    ARTIFACT-COMPOSITION error -- it says nothing about the market -- so it
    is signalled by raising ``LineageError``, never by returning a
    data-shaped result (``UNRESOLVED``/``EXCLUDED``/etc. remain exclusively
    D-2C2/D-2C3's vocabulary for describing the MARKET's evidence, not this
    module's vocabulary for describing a caller's mistake).

*   ``classify_readiness`` -- a pure, additive, orthogonal classification
    answering "can this observation safely enter a particular future D-2D
    evaluation tier", never "was the prediction good". It reads only
    maturity, eligibility and the coarse shape of the direction axis; it
    never reads ``setup_invalidation``, ``execution`` outcome state or
    ``thesis_invalidation`` to DECIDE a tier (Section 12 of the approved
    design is explicit that those axes must not be collapsed into
    readiness), and a ``FAILED`` direction is exactly as eligible as a
    ``CONFIRMED`` one once maturity/provenance/exclusion are satisfied --
    ``FAILED`` is a legitimate market outcome, not a data defect.

``build_verified_envelope`` is a thin convenience: it calls
``verify_lineage`` and then the existing, unmodified
``build_validation_envelope``. It reimplements no D-2C4 hashing logic.

This module does not modify ``OutcomeAxes``, does not add a new envelope
field, does not persist anything, and does not touch any frozen D-2C0-D-2C4
file. It is pure: no I/O, no wall clock, no production import.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping

from apex.b2.validation.config import ValidationConfig
from apex.b2.validation.envelope import (
    OverlapContext,
    ValidationEnvelope,
    build_validation_envelope,
)
from apex.b2.validation.invalidation import D2C3Resolution
from apex.b2.validation.maturity import MaturityState
from apex.b2.validation.outcome import (
    DirectionOutcome,
    EligibilityPool,
    ExecutionOutcome,
    SetupInvalidation,
    ThesisInvalidation,
)
from apex.b2.validation.resolve import DirectionPathResolution, claim_direction


class LineageError(ValueError):
    """Supplied artifacts cannot belong to the same validation lineage.

    Always a programmer/composition error, never market evidence. Must
    never be caught and translated into a data state anywhere in this
    package -- an integration layer above this module decides what to do
    with it (e.g. skip the observation and log the defect).
    """


class ReadinessTier(Enum):
    """Locked precedence: EXCLUDED > PROVISIONAL > RESEARCH_ONLY > CALIBRATION_ELIGIBLE.

    Orthogonal to direction/setup/execution/thesis outcome. Answers only
    "is the EVIDENCE trustworthy and complete enough", never "was the claim
    right". A ``FAILED`` direction is exactly as eligible as ``CONFIRMED``
    once maturity, provenance and exclusion are accounted for.
    """

    CALIBRATION_ELIGIBLE = "calibration_eligible"
    RESEARCH_ONLY = "research_only"
    PROVISIONAL = "provisional"
    EXCLUDED = "excluded"




def _utc(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _payload(record: Mapping[str, Any]) -> Mapping[str, Any]:
    """The shadow record payload, whether wrapped in a storage row or not.

    Duplicated in miniature from ``resolve.py``/``invalidation.py``/
    ``envelope.py`` rather than imported from any of them -- each already
    carries its own copy of this three-line helper, and importing a
    private, unexported name across a module boundary would gain nothing.
    """
    inner = record.get("record") if isinstance(record, Mapping) else None
    return inner if isinstance(inner, Mapping) else record


def _parse_evaluated_at(raw: Any) -> datetime | None:
    """Parse a record's own ``evaluated_at`` exactly as resolve.py does.

    Returns ``None`` on failure rather than raising: an unparsable
    timestamp is a DATA problem D-2C2 already classifies as
    ``BAD_TIMESTAMP``/``UNRESOLVED`` -- it is not this module's job to
    escalate an ordinary data defect into a lineage error.
    """
    try:
        return _utc(datetime.fromisoformat(str(raw or "").replace("Z", "+00:00")))
    except (TypeError, ValueError):
        return None


# ===========================================================================
# A. LINEAGE VERIFICATION
# ===========================================================================

def verify_lineage(
    *,
    record: Mapping[str, Any],
    path_resolution: DirectionPathResolution,
    d2c3_resolution: D2C3Resolution,
    validation_config: ValidationConfig,
) -> None:
    """Raise ``LineageError`` if the supplied artifacts cannot belong together.

    Every check below uses ONLY fields that already exist on the current
    frozen ``DirectionPathResolution``/``D2C3Resolution``/``ValidationConfig``
    -- nothing is added to any of them, and no new fingerprint is invented.
    A check that depends on data the RECORD itself has made unparsable (a
    bad timestamp, an unrecognised horizon) is skipped rather than raised:
    that is a data problem D-2C2 already surfaces on its own terms, not a
    lineage problem.

    Never returns a data-shaped result. Never raises anything but
    ``LineageError`` for a genuine mismatch; a well-formed, mutually
    consistent set of artifacts returns ``None`` with no side effect.
    """
    payload = _payload(record)

    # -- A. Claim direction --------------------------------------------------
    # The primary prediction is, and remains, claim.direction -- never
    # decision.direction/macro_direction/technical_direction.
    recomputed_claim = claim_direction(record)
    if recomputed_claim is not path_resolution.claim_direction:
        raise LineageError(
            "claim_direction mismatch: record implies "
            f"{recomputed_claim.value!r} but path_resolution.claim_direction is "
            f"{path_resolution.claim_direction.value!r}. These cannot be the "
            "same observation."
        )

    # -- B. evaluated_at -------------------------------------------------------
    # Compared as normalized, timezone-aware instants -- never as raw
    # strings, so equivalent ISO representations of the same instant never
    # falsely disagree.
    record_evaluated_at = _parse_evaluated_at(payload.get("evaluated_at"))
    if record_evaluated_at is not None:
        if record_evaluated_at != _utc(path_resolution.maturity.evaluated_at):
            raise LineageError(
                "evaluated_at mismatch: record implies "
                f"{record_evaluated_at.isoformat()!r} but "
                "path_resolution.maturity.evaluated_at is "
                f"{_utc(path_resolution.maturity.evaluated_at).isoformat()!r}."
            )

    # -- C. Validation config hash ----------------------------------------------
    # config_hash, never config_version -- version is a human-readable
    # sibling tag and does not uniquely identify config CONTENT (two
    # ValidationConfig instances can share a version string and differ in
    # config_hash).
    if validation_config.config_hash != path_resolution.band.config_hash:
        raise LineageError(
            "validation_config mismatch: supplied validation_config.config_hash="
            f"{validation_config.config_hash!r} but path_resolution.band.config_hash="
            f"{path_resolution.band.config_hash!r}. path_resolution was resolved "
            "under a DIFFERENT configuration than the one supplied here."
        )

    # -- D. Horizon / window --------------------------------------------------
    record_horizon = str(payload.get("horizon") or "").strip()
    expected_window = validation_config.window_for(record_horizon)
    if expected_window is not None and expected_window != path_resolution.maturity.window:
        raise LineageError(
            f"horizon/window mismatch: record horizon {record_horizon!r} implies a "
            f"{expected_window} window under the supplied config, but "
            f"path_resolution.maturity.window is {path_resolution.maturity.window}."
        )

    # -- E. D-2C3 structural self-consistency ------------------------------------
    # Cheap checks of implications the frozen D-2C3 contract ALREADY
    # guarantees -- never a re-implementation of its business logic (no
    # touch detection, no invalidation-level comparison is performed here).
    setup = d2c3_resolution.setup
    execution = d2c3_resolution.execution

    if d2c3_resolution.thesis is not ThesisInvalidation.NOT_ASSESSABLE:
        raise LineageError(
            "thesis_invalidation is not NOT_ASSESSABLE "
            f"({d2c3_resolution.thesis.value!r}); the current frozen D-2C3 "
            "contract requires it unconditionally."
        )

    if path_resolution.direction is DirectionOutcome.UNRESOLVED and setup.state is not SetupInvalidation.UNKNOWN:
        raise LineageError(
            "setup_invalidation is inconsistent with path_resolution: direction "
            f"is UNRESOLVED but setup.state is {setup.state.value!r}, not UNKNOWN."
        )

    if path_resolution.direction in (DirectionOutcome.ABSTAINED, DirectionOutcome.NOT_APPLICABLE) and (
        setup.state is not SetupInvalidation.NOT_APPLICABLE
    ):
        raise LineageError(
            "setup_invalidation is inconsistent with path_resolution: direction "
            f"is {path_resolution.direction.value!r} (not directional) but "
            f"setup.state is {setup.state.value!r}, not NOT_APPLICABLE."
        )

    if setup.reasons and tuple(setup.reasons) != tuple(path_resolution.reasons):
        raise LineageError(
            "setup.reasons does not match path_resolution.reasons: "
            f"{tuple(r.value for r in setup.reasons)} vs "
            f"{tuple(r.value for r in path_resolution.reasons)}. A D-2C3 result "
            "may only ever echo D-2C2's own reasons verbatim or carry none."
        )

    agreement = setup.measures.direction_agreement
    if agreement is True and setup.state is SetupInvalidation.NOT_APPLICABLE:
        raise LineageError(
            "setup.measures.direction_agreement is True but setup.state is "
            "NOT_APPLICABLE -- agreement can only be True on the verdict-reached "
            "path, which never resolves to NOT_APPLICABLE."
        )
    if agreement is False and setup.state is not SetupInvalidation.NOT_APPLICABLE:
        raise LineageError(
            "setup.measures.direction_agreement is False but setup.state is "
            f"{setup.state.value!r}, not NOT_APPLICABLE -- a direction "
            "disagreement always resolves to NOT_APPLICABLE."
        )

    if setup.state is SetupInvalidation.INVALIDATED:
        path = path_resolution.canonicalization.bars
        if not setup.measures.touched:
            raise LineageError(
                "setup.state is INVALIDATED but setup.measures.touched is not True."
            )
        index = setup.measures.bars_to_touch
        if index is None or not (0 <= index < len(path)):
            raise LineageError(
                f"setup.measures.bars_to_touch={index!r} is not a valid index into "
                f"the supplied path_resolution.canonicalization.bars (length "
                f"{len(path)}). The D2C3 result does not correspond to this path."
            )

    if execution.depends_on:
        if not execution.was_blocked:
            raise LineageError(
                "execution.depends_on is non-empty but execution.was_blocked is "
                "False -- a dependency is only ever recorded on the genuine "
                "new-entry-deferral path, which requires was_blocked=True."
            )
        if execution.state is ExecutionOutcome.NOT_APPLICABLE:
            raise LineageError(
                "execution.depends_on is non-empty but execution.state is "
                "NOT_APPLICABLE -- a dependency is never recorded on that path."
            )


# ===========================================================================
# B. READINESS CLASSIFICATION
# ===========================================================================

def classify_readiness(
    *,
    path_resolution: DirectionPathResolution,
    d2c3_resolution: D2C3Resolution,
) -> ReadinessTier:
    """Classify one observation's readiness for a future D-2D evaluation tier.

    Reads only ``path_resolution``'s maturity, eligibility and the coarse
    shape of its direction axis. Deliberately does NOT read
    ``d2c3_resolution.setup``/``.execution``/`.thesis`` to DECIDE a tier --
    Section 12 of the approved design is explicit that setup invalidation,
    execution deferral quality and thesis invalidation must not be
    collapsed into readiness; those remain independent axes a D-2D
    consumer reads separately. ``d2c3_resolution`` is accepted for API
    symmetry with ``verify_lineage``/``build_verified_envelope`` and for
    future extensibility; it is not consulted by the classification logic
    below.

    A "usable directional verdict" for ``CALIBRATION_ELIGIBLE`` and
    ``RESEARCH_ONLY`` means exactly the existing D-2C2 contract:
    ``direction.is_verdict`` (``CONFIRMED``/``FAILED`` only) -- reused
    directly rather than restated as a second enum list, exactly as
    ``OutcomeAxes.is_calibration_eligible`` already defines "verdict" for
    the same purpose. ``NEUTRAL_WITHIN_BAND`` is genuine, resolved,
    non-missing evidence (an honest "no material move", not an absence --
    see resolve.py's own docstring for the same distinction) but it is NOT
    a directional calibration verdict, so a final ``NEUTRAL_WITHIN_BAND``
    observation -- captured or reconstructed alike -- falls into the same
    EXCLUDED path as ``UNRESOLVED``/``ABSTAINED``/``NOT_APPLICABLE``: there
    is nothing directional here for a calibration or research-only pool to
    grade, even though the evidence itself is perfectly sound.

    Never raises. Total over every state D-2C2/D-2C3 can currently produce.
    """
    del d2c3_resolution  # accepted for API symmetry; not used in tier logic (Section 12)

    eligibility = path_resolution.eligibility_pool
    is_final = path_resolution.maturity.state is MaturityState.MATURED
    usable_direction = path_resolution.direction.is_verdict

    # -- 1. EXCLUDED: fundamentally unusable, independent of maturity. --------
    if eligibility is EligibilityPool.EXCLUDED:
        return ReadinessTier.EXCLUDED
    if is_final and not usable_direction:
        # The window closed and there is STILL no directional VERDICT --
        # covers UNRESOLVED/ABSTAINED/NOT_APPLICABLE (no reportable reading
        # at all) and NEUTRAL_WITHIN_BAND (a real reading, but not a
        # CONFIRMED/FAILED verdict). None of these can become calibration
        # or research evidence no matter how many more bars arrive, so this
        # is EXCLUDED now rather than PROVISIONAL forever.
        return ReadinessTier.EXCLUDED

    # -- 2. PROVISIONAL: window not yet closed (locked precedence: this ------
    # outranks RESEARCH_ONLY, per the approved design). Covers NOT_MATURED,
    # MATURED_AWAITING_BARS and MATURED_PARTIAL alike -- including the case
    # where direction is still UNRESOLVED simply because the window has not
    # been looked at yet: that is "not yet known", not "excluded".
    if not is_final:
        return ReadinessTier.PROVISIONAL

    # From here: is_final is True and usable_direction is True.

    # -- 3. RESEARCH_ONLY: final, usable, but not captured-grade provenance. --
    if eligibility is not EligibilityPool.CAPTURED:
        return ReadinessTier.RESEARCH_ONLY

    # -- 4. CALIBRATION_ELIGIBLE: every condition holds. ----------------------
    return ReadinessTier.CALIBRATION_ELIGIBLE


# ===========================================================================
# C. VERIFIED ENVELOPE CONSTRUCTION
# ===========================================================================

def build_verified_envelope(
    *,
    record: Mapping[str, Any],
    path_resolution: DirectionPathResolution,
    d2c3_resolution: D2C3Resolution,
    validation_config: ValidationConfig,
    overlap_context: OverlapContext | None = None,
    malformed_row_count: int | None = None,
) -> ValidationEnvelope:
    """Verify lineage, then build the envelope exactly as D-2C4 already does.

    Reimplements no D-2C4 hashing: ``validation_id``/``input_hash``/
    ``outcome_hash`` are produced by the unmodified
    ``build_validation_envelope``. ``LineageError`` propagates uncaught on a
    mismatch -- the envelope is never constructed from artifacts that
    cannot belong together.
    """
    verify_lineage(
        record=record,
        path_resolution=path_resolution,
        d2c3_resolution=d2c3_resolution,
        validation_config=validation_config,
    )
    return build_validation_envelope(
        record=record,
        path_resolution=path_resolution,
        d2c3_resolution=d2c3_resolution,
        validation_config=validation_config,
        overlap_context=overlap_context,
        malformed_row_count=malformed_row_count,
    )


__all__ = [
    "LineageError",
    "ReadinessTier",
    "build_verified_envelope",
    "classify_readiness",
    "verify_lineage",
]
