"""Architecture B2 -- Stage D-5: forward outcome persistence (Tactical only).

D-2C2 through D-2E resolve one shadow observation into a
``ValidationEnvelope`` -- direction, path, excursions, setup invalidation,
execution quality, lineage and readiness -- and the whole chain terminates in
memory. Nothing keeps the answer. This module decides which of those answers
are worth keeping, gives each one a durable identity, and shapes it into a row.

It adds NO evaluation logic. Every value written comes from an envelope that
was already built, verified and hashed by the frozen D-2C stages; this module
selects, labels and reshapes, and computes exactly one thing of its own -- the
row identity.

Three separations are load-bearing:

**A prediction fact, a market fact and an evaluation fact are different things.**
``b2_shadow_records`` holds what B2 claimed. ``b2_market_observations`` holds
what the market printed. What an evaluation concluded from both is a THIRD
kind of fact, and writing it into either of the first two would destroy the
separation the architecture rests on. Hence a separate table, and hence this
module never touches the other two.

**Evidence is part of identity; the verdict is not.**
``outcome_row_id = sha256("val" | validation_id | input_hash)`` -- see
:func:`canonical_outcome_row_id`. Re-running against the same evidence
deduplicates. Re-running after a coverage gap fills produces a different
``input_hash`` and APPENDS a superseding row beside the first, which is never
touched. ``outcome_hash`` is carried as a field rather than folded into the
identity precisely so that "same evidence, different verdict" stays
DETECTABLE -- it is a determinism defect in this codebase, not a market event,
and it must never be silently absorbed.

**Withholding is not failure.**
Every observation the gate declines is declined for a NAMED reason and
counted. An immature window, a horizon this stage cannot honestly judge, and a
composition defect are three different things, and none of them is a wrong
prediction. There is no path in this module that turns an absence into a
verdict.

Tactical only. Execution is withheld on measured grounds: a three-day window
yields at most two daily bars, 45% of observations get fewer than two, and the
16% that get none are weekend-correlated -- a systematic sampling bias, not
random loss. Judging execution from that would be false precision, so this
stage records nothing for it. Structural is withheld because it is not an
activated horizon at all.

This module is pure. It performs no I/O, holds no clock, opens no file, and
imports neither bridge. It also deliberately does NOT import ``envelope.py``,
``readiness.py``, ``resolve.py`` or ``invalidation.py``: each is guarded by a
test asserting exactly which modules may import it, and this stage is not on
any of those lists. Inputs are accepted STRUCTURALLY, against the shape of
``apex.b2.evaluation.observation.EvaluatedObservation`` -- the same
duck-typing ``invalidation.py`` already uses for its own upstream input.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

from .maturity import MaturityState
from .outcome import DataResolution

#: Bumped when the SHAPE of a persisted outcome row, or the rule that builds
#: its identity, changes. A change to the evaluation itself is already covered
#: by ``validation_schema_version`` and ``validation_config_hash``, which are
#: carried on every row -- exactly as ``ValidationConfig.version`` and
#: ``.config_hash`` are kept orthogonal upstream.
OUTCOME_SCHEMA_VERSION = "b2-validation-outcome-v1"

#: Domain tag in the outcome identity basis, making this hash domain provably
#: disjoint from the observation, content and revision domains.
_OUTCOME_DOMAIN = "val"

#: Matches the separator every other identity basis in this package uses.
#: Cannot occur in a hex hash or an ISO timestamp.
_IDENTITY_SEPARATOR = "|"

_HASH_LENGTH = 32

#: The ONLY horizon D-5 may persist an outcome for.
#:
#: Execution is absent on MEASURED grounds, not stylistic ones -- see the
#: module docstring and ``GateDecision.WITHHELD_EXECUTION_GRANULARITY``.
#: Structural is absent because it is not an activated shadow horizon.
#: Widening this set is a separate, separately-approved stage.
PERSISTABLE_HORIZONS = frozenset({"tactical"})

#: The horizon whose withholding is a statement about BAR GRANULARITY rather
#: than about activation. Named so the report can tell the two apart.
EXECUTION_HORIZON = "execution"


class OutcomeFinality(Enum):
    """Whether later evidence could still change this result.

    ``FINAL``
        The window matured, the data resolved, and the path is complete. No
        bar that has not yet arrived can belong to this window, so nothing
        later can change the answer.

    ``PROVISIONAL``
        Everything else that is nonetheless worth recording -- in practice a
        matured window with a genuine coverage gap. The excursion measures are
        LOWER BOUNDS (``excursion_is_lower_bound`` upstream), and a later
        capture that fills the gap will produce a different ``input_hash`` and
        append a superseding row.

    A provisional row is never rewritten when it is superseded. Both states of
    the evidence are kept, because "what we concluded, from what evidence,
    when" is the question this table exists to answer.
    """

    FINAL = "final"
    PROVISIONAL = "provisional"


class GateDecision(Enum):
    """Why one evaluated observation was or was not persisted.

    Every value except ``PERSIST`` is a withholding, and every withholding is
    counted and reported. None of them is a verdict about the claim.
    """

    PERSIST = "persist"

    #: The composition failed (D-2D0 ``LineageDefect``). A defect says a
    #: caller assembled artifacts that cannot belong together; it says nothing
    #: whatever about the market, so it is never written as an outcome.
    WITHHELD_LINEAGE_DEFECT = "withheld_lineage_defect"

    #: Execution horizon. Daily bars cannot honestly resolve a three-day
    #: window -- at most two bars, 45% with fewer than two, and the zero-bar
    #: cases concentrated on weekends. Recording a verdict from that would
    #: manufacture precision the evidence does not contain.
    WITHHELD_EXECUTION_GRANULARITY = "withheld_execution_granularity"

    #: Any other non-persistable horizon, Structural included. Distinct from
    #: the execution case because the REASON is different: not activated,
    #: rather than not resolvable at this granularity.
    WITHHELD_HORIZON_NOT_ACTIVATED = "withheld_horizon_not_activated"

    #: The forward window has not elapsed. There is nothing to record but the
    #: passage of time, and recording it would be the single most damaging
    #: error this stage could make.
    WITHHELD_NOT_MATURED = "withheld_not_matured"

    #: The window elapsed but capture has not reached past it
    #: (``MATURED_AWAITING_BARS``). That is OUR backlog, not the market's
    #: absence, and a row written from it would be a statement about the
    #: capture schedule. It becomes persistable on its own once bars arrive.
    WITHHELD_NO_VERDICT_PERMITTED = "withheld_no_verdict_permitted"

    @property
    def persists(self) -> bool:
        return self is GateDecision.PERSIST


@runtime_checkable
class EvaluatedLike(Protocol):
    """The shape of a D-2D0 result, accepted structurally rather than by name.

    Mirrors ``apex.b2.evaluation.observation.EvaluatedObservation``. Declared
    as a Protocol for the same reason ``invalidation.py`` declares
    ``PathResolutionLike``: importing the nominal type would make this module
    an importer of a guarded module, defeating a standing architectural test
    rather than satisfying it.
    """

    @property
    def is_defect(self) -> bool: ...


def canonical_outcome_row_id(validation_id: str, input_hash: str) -> str:
    """Deterministic durable identity for one persisted evaluation result.

    Deliberately a function of the validation JOB and the EVIDENCE, and of
    nothing else.

    *   No clock. ``first_seen_at`` is stamped by the database and is not an
        input here; including it would give every re-run a fresh identity and
        the same conclusion would be re-recorded forever.
    *   No ``outcome_hash``. This is the load-bearing choice. If the verdict
        were part of the identity, a run that produced a DIFFERENT verdict
        from IDENTICAL evidence would quietly append a second row and look
        like ordinary supersession. Keeping it out makes that collide on the
        primary key instead, where it is caught and reported as the
        determinism defect it is.
    *   No ordinal. A counter cannot be assigned idempotently without a
        read-modify-write, which would cost both the append-only and the
        fail-open guarantee. Rows are ordered by ``first_seen_at`` on read.

    Same construction and same 32-hex width as ``canonical_observation_id``,
    ``canonical_bar_content_hash`` and ``canonical_revision_id``, so every
    identity in this system reads alike in a database and in a log.
    """
    basis = _IDENTITY_SEPARATOR.join(
        [_OUTCOME_DOMAIN, str(validation_id), str(input_hash)]
    )
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:_HASH_LENGTH]


def outcome_finality(
    *,
    maturity_state: str,
    data_resolution: str,
    path_complete: Any,
) -> OutcomeFinality:
    """FINAL only when no later bar could change the answer.

    All three conditions are required, and each rules out a different way for
    evidence to still be incomplete:

        maturity_state   == MATURED    -- the window elapsed AND is covered
        data_resolution  == RESOLVED   -- the evidence is usable, not partial
        path_complete    is True       -- no gap inside the window

    ``path_complete`` is read as an identity check against ``True`` rather
    than for truthiness, so a missing key, a ``None`` or a stray string can
    never be promoted into a claim of completeness.
    """
    if (
        str(maturity_state) == MaturityState.MATURED.value
        and str(data_resolution) == DataResolution.RESOLVED.value
        and path_complete is True
    ):
        return OutcomeFinality.FINAL
    return OutcomeFinality.PROVISIONAL


@dataclass(frozen=True)
class GateResult:
    """One gate decision, with the horizon it was made about."""

    decision: GateDecision
    horizon: str
    #: Present only for a withholding, and only when the maturity state is
    #: known. Diagnostic; never an identity.
    maturity_state: str | None = None

    @property
    def persists(self) -> bool:
        return self.decision.persists


def _envelope_of(evaluated: Any) -> Any:
    return getattr(evaluated, "envelope", None)


def _context_of(evaluated: Any) -> Any:
    envelope = _envelope_of(evaluated)
    return getattr(envelope, "context", None) if envelope is not None else None


def persistence_gate(evaluated: Any) -> GateResult:
    """Decide whether one evaluated observation may be persisted, and why not.

    The order of checks is fixed, and each precedes the next because its
    answer is more fundamental:

        1. a composition defect is not an observation at all
        2. a horizon this stage cannot judge is refused before any maturity
           question is asked -- an immature execution observation must not be
           reported as merely immature, because it would still be withheld
           after it matured
        3. an unelapsed window has nothing to record
        4. an elapsed window nobody has looked past has nothing to record

    Never raises. An input this function cannot read at all is withheld as a
    lineage defect, which is the conservative reading: something is wrong with
    the composition, so nothing is written.
    """
    if evaluated is None or bool(getattr(evaluated, "is_defect", True)):
        return GateResult(
            decision=GateDecision.WITHHELD_LINEAGE_DEFECT,
            horizon=str(getattr(evaluated, "horizon", "") or ""),
        )

    context = _context_of(evaluated)
    if context is None:
        return GateResult(decision=GateDecision.WITHHELD_LINEAGE_DEFECT, horizon="")

    horizon = str(getattr(context, "horizon", "") or "").strip()
    maturity_state = str(getattr(context, "maturity_state", "") or "")

    if horizon not in PERSISTABLE_HORIZONS:
        decision = (
            GateDecision.WITHHELD_EXECUTION_GRANULARITY
            if horizon == EXECUTION_HORIZON
            else GateDecision.WITHHELD_HORIZON_NOT_ACTIVATED
        )
        return GateResult(
            decision=decision, horizon=horizon, maturity_state=maturity_state
        )

    if maturity_state == MaturityState.NOT_MATURED.value:
        return GateResult(
            decision=GateDecision.WITHHELD_NOT_MATURED,
            horizon=horizon,
            maturity_state=maturity_state,
        )

    try:
        permits = MaturityState(maturity_state).permits_verdict
    except ValueError:
        permits = False
    if not permits:
        return GateResult(
            decision=GateDecision.WITHHELD_NO_VERDICT_PERMITTED,
            horizon=horizon,
            maturity_state=maturity_state,
        )

    return GateResult(
        decision=GateDecision.PERSIST,
        horizon=horizon,
        maturity_state=maturity_state,
    )


class OutcomeRowError(ValueError):
    """Raised when a row cannot be built honestly from what was supplied."""


@dataclass(frozen=True)
class ValidationOutcomeRow:
    """One immutable persisted evaluation result.

    Every field is copied from an envelope that already computed and hashed
    it. Nothing here is recomputed, and nothing is derived that the envelope
    does not already assert -- a second derivation is a second definition, and
    the two would eventually disagree without anything failing.
    """

    outcome_row_id: str
    validation_id: str
    input_hash: str
    outcome_hash: str
    finality: OutcomeFinality
    as_of: str
    #: The envelope's own wire form, reshaped column-wise. Held as a mapping
    #: so this dataclass does not become a second, drifting copy of
    #: ``ValidationContext``.
    columns: Mapping[str, Any]

    def to_row(self) -> dict[str, Any]:
        """Map onto one ``b2_validation_outcomes`` row.

        ``first_seen_at`` is deliberately ABSENT. The database defaults it to
        its own ``now()``, so the moment a conclusion was first recorded
        cannot be backdated by a client, and ON CONFLICT DO NOTHING means a
        re-run never moves it.
        """
        row = dict(self.columns)
        row.update(
            {
                "outcome_row_id": self.outcome_row_id,
                "validation_id": self.validation_id,
                "input_hash": self.input_hash,
                "outcome_hash": self.outcome_hash,
                "finality": self.finality.value,
                "as_of": self.as_of,
                "outcome_schema_version": OUTCOME_SCHEMA_VERSION,
            }
        )
        return row


def _bar_evidence(context: Any) -> list[dict[str, str]]:
    """The ordered ``input_hash`` preimage, as stored.

    Committed in the order D-2C2's own ``canonicalize_bars`` produced and
    never re-sorted: a sorted bag of content hashes is blind to which bar
    holds which content, and two paths that swap them would look identical
    while resolving to different returns. Pairing each id with its content
    hash, in order, is what makes the evidence reproducible from the row.
    """
    ids = tuple(getattr(context, "used_observation_ids", ()) or ())
    hashes = tuple(getattr(context, "used_bar_content_hashes", ()) or ())
    return [
        {"observation_id": str(observation_id), "content_hash": str(content_hash)}
        for observation_id, content_hash in zip(ids, hashes)
    ]


def build_outcome_row(
    *,
    evaluated: Any,
    as_of: str,
    gate: GateResult | None = None,
) -> ValidationOutcomeRow:
    """Shape one PERSISTABLE evaluated observation into its row.

    Raises ``OutcomeRowError`` when the gate did not authorise persistence, or
    when the envelope lacks the identity it must carry. Both are programmer
    errors rather than market facts, so they raise rather than returning a
    data-shaped result -- the same separation ``LineageError`` already keeps
    upstream.
    """
    decision = gate if gate is not None else persistence_gate(evaluated)
    if not decision.persists:
        raise OutcomeRowError(
            "build_outcome_row was called for an observation the gate withheld "
            f"({decision.decision.value}). Only a gate-approved observation may "
            "become a row."
        )

    envelope = _envelope_of(evaluated)
    context = _context_of(evaluated)
    validation_id = str(getattr(envelope, "validation_id", "") or "")
    input_hash = str(getattr(envelope, "input_hash", "") or "")
    outcome_hash = str(getattr(envelope, "outcome_hash", "") or "")
    if not validation_id or not input_hash or not outcome_hash:
        raise OutcomeRowError(
            "An outcome row requires validation_id, input_hash and outcome_hash. "
            "An envelope missing any of the three cannot be persisted."
        )

    axes = getattr(envelope, "outcome_axes", None)
    axes_record: Mapping[str, Any] = (
        axes.as_record() if axes is not None and hasattr(axes, "as_record") else {}
    )
    basis = getattr(envelope, "outcome_hash_basis", None)
    basis = basis if isinstance(basis, Mapping) else {}
    path_complete = basis.get("path_complete")

    maturity_state = str(getattr(context, "maturity_state", "") or "")
    data_resolution = str(axes_record.get("data_resolution") or "")

    columns: dict[str, Any] = {
        # -- lineage of the evaluation itself ------------------------------
        "validation_schema_version": str(
            getattr(context, "validation_schema_version", "") or ""
        ),
        "validation_config_version": str(
            getattr(context, "validation_config_version", "") or ""
        ),
        "validation_config_hash": str(
            getattr(context, "validation_config_hash", "") or ""
        ),
        # -- the prediction fact this is ABOUT (never a copy of its payload)
        "shadow_storage_id": str(getattr(context, "shadow_storage_id", "") or ""),
        "shadow_record_id": str(getattr(context, "shadow_record_id", "") or ""),
        "shadow_content_hash": str(getattr(context, "shadow_content_hash", "") or ""),
        "shadow_schema_version": getattr(context, "shadow_schema_version", None),
        "instrument": str(getattr(context, "instrument", "") or ""),
        "horizon": str(getattr(context, "horizon", "") or ""),
        "asset_class": str(getattr(evaluated, "asset_class", "") or ""),
        "evaluated_at": str(getattr(context, "evaluated_at", "") or ""),
        "claim_direction": str(
            getattr(getattr(evaluated, "claim_direction", None), "value", "") or ""
        ),
        # -- the six axes, verbatim from the envelope ----------------------
        "data_resolution": data_resolution,
        "direction_outcome": str(axes_record.get("direction_outcome") or ""),
        "setup_invalidation": str(axes_record.get("setup_invalidation") or ""),
        "thesis_invalidation": str(axes_record.get("thesis_invalidation") or ""),
        "execution_outcome": str(axes_record.get("execution_outcome") or ""),
        "eligibility_pool": str(axes_record.get("eligibility_pool") or ""),
        "exclusion_reason": axes_record.get("exclusion_reason"),
        "calibration_eligible": bool(axes_record.get("calibration_eligible")),
        # -- evidence quality ----------------------------------------------
        "readiness_tier": str(
            getattr(getattr(evaluated, "readiness", None), "value", "") or ""
        ),
        "provenance_grade": str(
            getattr(getattr(evaluated, "provenance_grade", None), "value", "") or ""
        ),
        "maturity_state": maturity_state,
        "coverage_status": getattr(context, "coverage_status", None),
        "finalization_status": str(getattr(context, "finalization_status", "") or ""),
        "path_complete": path_complete is True,
        # -- the measurements. MEASUREMENTS, never scores. -----------------
        "terminal_return": axes_record.get("terminal_return"),
        "mfe": axes_record.get("mfe"),
        "mae": axes_record.get("mae"),
        "mfe_atr": axes_record.get("mfe_atr"),
        "mae_atr": axes_record.get("mae_atr"),
        "bars_to_mfe": axes_record.get("bars_to_mfe"),
        "bars_to_mae": axes_record.get("bars_to_mae"),
        "path_bars": axes_record.get("path_bars"),
        # -- which market series actually answered -------------------------
        "anchor_status": str(getattr(context, "anchor_status", "") or ""),
        "market_symbol": getattr(context, "market_symbol", None),
        "bound_symbol": getattr(context, "bound_symbol", None),
        "series_binding_quality": str(
            getattr(context, "series_binding_quality", "") or ""
        ),
        "terminal_observation_id": getattr(context, "terminal_observation_id", None),
        "terminal_bar_time": getattr(context, "terminal_bar_time", None),
        "used_bar_count": getattr(context, "used_bar_count", None),
        "duplicates_collapsed": getattr(context, "duplicates_collapsed", None),
        "malformed_row_count": getattr(context, "malformed_row_count", None),
        # -- the input_hash preimage, reproducible from the row ------------
        "bar_evidence": _bar_evidence(context),
        "conflict_ids": list(getattr(context, "conflict_ids", ()) or ()),
    }

    return ValidationOutcomeRow(
        outcome_row_id=canonical_outcome_row_id(validation_id, input_hash),
        validation_id=validation_id,
        input_hash=input_hash,
        outcome_hash=outcome_hash,
        finality=outcome_finality(
            maturity_state=maturity_state,
            data_resolution=data_resolution,
            path_complete=path_complete,
        ),
        as_of=str(as_of),
        columns=columns,
    )


def build_outcome_rows(
    *,
    evaluated: Sequence[Any],
    as_of: str,
) -> tuple[tuple[ValidationOutcomeRow, ...], dict[str, int]]:
    """Gate a batch and shape what survives, with a census of what did not.

    The census counts EVERY input under exactly one gate decision, so the
    totals always reconcile against the batch size. An observation that is
    withheld is never dropped silently -- a denominator that shrinks without
    anyone noticing is the failure mode this whole stage is built against.
    """
    rows: list[ValidationOutcomeRow] = []
    census: dict[str, int] = {decision.value: 0 for decision in GateDecision}
    for item in evaluated:
        gate = persistence_gate(item)
        census[gate.decision.value] += 1
        if not gate.persists:
            continue
        rows.append(build_outcome_row(evaluated=item, as_of=as_of, gate=gate))
    return tuple(rows), census


__all__ = [
    "EXECUTION_HORIZON",
    "OUTCOME_SCHEMA_VERSION",
    "PERSISTABLE_HORIZONS",
    "EvaluatedLike",
    "GateDecision",
    "GateResult",
    "OutcomeFinality",
    "OutcomeRowError",
    "ValidationOutcomeRow",
    "build_outcome_row",
    "build_outcome_rows",
    "canonical_outcome_row_id",
    "outcome_finality",
    "persistence_gate",
]
