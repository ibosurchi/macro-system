"""Architecture B2 -- Stage D-2C3: setup invalidation and execution quality.

Answers three further, narrow questions about ONE already-resolved shadow
observation, on top of what D-2C2 already established:

*   **Setup invalidation** -- did the TECHNICAL setup (the production Macro
    Entry Plan's invalidation level) get touched by price after the
    observation was taken? Price-derived, never evidence-derived.
*   **Execution quality** -- was a point-in-time execution veto (a gate
    deferring a NEW entry) later proven correct or costly by what the setup
    and the direction actually did? Judged only against DOCUMENTED deferral
    behaviour; ApexMacro records no real fill, so no entry-timing verdict is
    ever produced here.
*   **Macro thesis invalidation** -- always ``NOT_ASSESSABLE`` in this stage.
    No live shadow record carries a populated thesis lifecycle (see the
    module-level test suite), so there is nothing here to resolve it from,
    and price movement must never be allowed to manufacture one.

**Scope is deliberately narrow**, mirroring D-2C2's own discipline. This
module does not compute identity hashes, context copy or overlap metadata
(D-2C4), and it does not touch Direction or the excursion measurements --
those remain exactly what D-2C2 already resolved.

Four rules are structural rather than advisory:

*   **The invalidation level must describe the claim being judged.** The
    stored invalidation/entry-zone came from production's own Macro Entry
    Plan, built against production's own macro-regime read -- a SEPARATE,
    independently-computed direction from B2's own voting-core claim. Before
    either axis is resolved, the direction implied by
    ``invalidation_level`` vs. ``current_price`` must agree with the claim.
    A disagreement is not evidence of anything about the claim, so both axes
    resolve ``NOT_APPLICABLE`` rather than being computed from a setup built
    for the wrong side.
*   **Missing data is never a wrong or negative verdict.** No path here
    reaches ``INVALIDATED`` or ``DEFERRAL_COSTLY`` from an absence; every
    non-judgeable path returns ``UNKNOWN``/``UNRESOLVED``/``NOT_APPLICABLE``
    as the data actually warrants.
*   **A proven touch cannot be undone by a missing later bar.** Setup
    invalidation the observed path already touched is final even under
    partial coverage; the LOWER BOUND is enough to prove a touch, exactly as
    D-2C2 already treats MFE/MAE as lower bounds under partial coverage.
*   **No real entry is pretended.** ApexMacro records no fill price, no
    entry timestamp and no executed position anywhere in this pipeline.
    ``ENTRY_JUSTIFIED``/``ENTRY_PREMATURE``/``ENTRY_LATE`` already exist in
    the enum vocabulary but are never emitted here -- only the documented
    deferral states (``DEFERRAL_CORRECT``/``DEFERRAL_COSTLY``) are, because
    only those are judgeable from a captured gate veto rather than an
    invented entry event.

This module deliberately does NOT import ``apex.b2.validation.resolve``:
that module is separately guarded by a test asserting nothing else in the
repository imports it, and this module's input is accepted structurally
(duck-typed against the shape of ``resolve.DirectionPathResolution``)
rather than by nominal type, which keeps that guarantee intact while still
consuming exactly the D-2C2 result the approved design calls for.

Pure: stdlib plus ``apex.b2.outcome``/``apex.b2.enums`` only. No I/O, no
clock, no randomness, no network, no storage, no production import.
Nothing here is read by any production path, and B2 remains
SHADOW / NON-PRODUCTION / UNCALIBRATED.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol, Sequence

from ..enums import Direction, Horizon
from .outcome import (
    DataResolution,
    DirectionOutcome,
    ExclusionReason,
    ExecutionOutcome,
    SetupInvalidation,
    ThesisInvalidation,
)


def _payload(record: Mapping[str, Any]) -> Mapping[str, Any]:
    """The shadow record payload, whether wrapped in a storage row or not.

    Duplicated in miniature from ``resolve.py`` rather than imported from it
    -- see the module docstring for why this module never imports
    ``validation.resolve``.
    """
    inner = record.get("record") if isinstance(record, Mapping) else None
    return inner if isinstance(inner, Mapping) else record


def _numeric(value: Any) -> float | None:
    """Coerce to float, or None. NaN and infinities are unusable, not zero."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


class PathResolutionLike(Protocol):
    """The structural shape this module consumes from D-2C2's result.

    Any object exposing these attributes works -- in practice always a real
    ``apex.b2.validation.resolve.DirectionPathResolution`` -- without this
    module ever importing that class by name.
    """

    claim_direction: Direction
    direction: DirectionOutcome
    data_resolution: DataResolution
    reasons: tuple[ExclusionReason, ...]
    path_complete: bool
    anchor_price: float | None
    excursion: Any  # .mfe / .mae: float | None
    canonicalization: Any  # .bars: Sequence[bar-like, each with .analysis_low/.analysis_high]


@dataclass(frozen=True)
class InvalidationMeasures:
    """Deterministic, point-in-time-safe measurements. MEASUREMENTS, not scores.

    Recorded even when the categorical state is ``UNKNOWN`` or
    ``NOT_APPLICABLE`` wherever they are honestly computable, so a later
    calibration pass has raw numbers to work from rather than only a label.
    """

    invalidation_level: float | None = None
    invalidation_distance: float | None = None  # price units, |current - invalidation| at evaluated_at
    invalidation_distance_pct: float | None = None  # invalidation_distance / anchor_price
    #: None only when there was not enough data to even attempt the check
    #: (e.g. invalidation undefined). True/False once both prices are known.
    direction_agreement: bool | None = None
    #: Whether a touch was OBSERVED within the available bars. When the
    #: category is UNKNOWN (partial coverage, no touch yet) this is False --
    #: a fact about the evidence seen so far, not a claim that no later touch
    #: can occur.
    touched: bool | None = None
    bars_to_touch: int | None = None
    #: Research-only R-multiples. See module docstring: 1R here is the
    #: captured invalidation distance from the point-in-time reference price,
    #: NOT a real trade's entry-to-stop distance.
    mfe_in_r: float | None = None
    mae_in_r: float | None = None

    def as_record(self) -> dict[str, Any]:
        return {
            "invalidation_level": self.invalidation_level,
            "invalidation_distance": self.invalidation_distance,
            "invalidation_distance_pct": self.invalidation_distance_pct,
            "direction_agreement": self.direction_agreement,
            "touched": self.touched,
            "bars_to_touch": self.bars_to_touch,
            "mfe_in_r": self.mfe_in_r,
            "mae_in_r": self.mae_in_r,
        }


@dataclass(frozen=True)
class SetupInvalidationResolution:
    """Whether the captured technical setup was invalidated by price."""

    state: SetupInvalidation
    measures: InvalidationMeasures
    #: Reused verbatim from D-2C2 when the reason IS a D-2C2 data-quality
    #: exclusion. Empty when the state instead follows from claim shape,
    #: horizon scope, or invalidation-availability -- none of which is a
    #: D-2C2 ``ExclusionReason``.
    reasons: tuple[ExclusionReason, ...] = ()
    #: Free-text notes for reasons outside the D-2C2 vocabulary (e.g. horizon
    #: out of scope, direction mismatch, degenerate invalidation).
    notes: tuple[str, ...] = ()

    def as_record(self) -> dict[str, Any]:
        return {
            "setup_invalidation": self.state.value,
            "reasons": [r.value for r in self.reasons],
            "notes": list(self.notes),
            **self.measures.as_record(),
        }


@dataclass(frozen=True)
class ExecutionQualityResolution:
    """Whether a documented execution deferral was later proven correct or costly.

    Never an entry-timing verdict: ApexMacro records no real fill, so
    ``ENTRY_JUSTIFIED``/``ENTRY_PREMATURE``/``ENTRY_LATE`` are never produced
    by this module even though they remain defined on ``ExecutionOutcome``.
    """

    state: ExecutionOutcome
    #: The captured ``execution.blocked`` value, recorded regardless of which
    #: branch produced ``state`` -- true point-in-time fact, not a verdict.
    was_blocked: bool
    block_reason: str | None
    #: Which other axes this verdict is a function of. Empty when the state
    #: was decided before those axes were ever consulted.
    depends_on: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    def as_record(self) -> dict[str, Any]:
        return {
            "execution_outcome": self.state.value,
            "was_blocked": self.was_blocked,
            "block_reason": self.block_reason,
            "depends_on": list(self.depends_on),
            "notes": list(self.notes),
        }


@dataclass(frozen=True)
class D2C3Resolution:
    """What D-2C3 could establish about one observation. Nothing more.

    Deliberately a separate, additive result rather than an expansion of
    D-2C2's frozen ``DirectionPathResolution`` -- see the module docstring.
    Identity hashes, context copy and overlap metadata remain D-2C4's job and
    are absent here rather than stubbed.
    """

    setup: SetupInvalidationResolution
    execution: ExecutionQualityResolution
    #: Always ``ThesisInvalidation.NOT_ASSESSABLE`` in this stage. Carried as
    #: a field (rather than a bare module constant call site) so a reader of
    #: the record sees it as a declared axis, not a silent omission.
    thesis: ThesisInvalidation

    def as_record(self) -> dict[str, Any]:
        return {
            "stage": "d2c3",
            **self.setup.as_record(),
            **self.execution.as_record(),
            "thesis_invalidation": self.thesis.value,
            "not_resolved_in_this_stage": [
                "validation_id",
                "input_hash",
                "outcome_hash",
                "context",
                "overlap_metadata",
            ],
        }


def _not_applicable(
    note: str, *, was_blocked: bool = False, block_reason: str | None = None
) -> D2C3Resolution:
    setup = SetupInvalidationResolution(
        state=SetupInvalidation.NOT_APPLICABLE,
        measures=InvalidationMeasures(),
        notes=(note,),
    )
    execution = ExecutionQualityResolution(
        state=ExecutionOutcome.NOT_APPLICABLE,
        was_blocked=was_blocked,
        block_reason=block_reason,
        notes=(note,),
    )
    return D2C3Resolution(setup=setup, execution=execution, thesis=ThesisInvalidation.NOT_ASSESSABLE)


def _unresolved_from_data(
    reasons: tuple[ExclusionReason, ...], *, was_blocked: bool = False, block_reason: str | None = None
) -> D2C3Resolution:
    setup = SetupInvalidationResolution(
        state=SetupInvalidation.UNKNOWN,
        measures=InvalidationMeasures(),
        reasons=reasons,
    )
    execution = ExecutionQualityResolution(
        state=ExecutionOutcome.UNRESOLVED,
        was_blocked=was_blocked,
        block_reason=block_reason,
    )
    return D2C3Resolution(setup=setup, execution=execution, thesis=ThesisInvalidation.NOT_ASSESSABLE)


def _direction_agreement(
    *, invalidation_level: float, current_price: float, claim: Direction
) -> bool:
    """Does the entry-plan-implied direction agree with the claim being judged?

    ``invalidation_level < current_price`` implies the stored Macro Entry
    Plan was built long; ``>`` implies short. Purely a comparison of two
    already-captured, same-instant, same-convention scalars -- no
    re-inversion, no lookahead.
    """
    implied_bullish = invalidation_level < current_price
    implied_bearish = invalidation_level > current_price
    if claim is Direction.BULLISH:
        return implied_bullish
    if claim is Direction.BEARISH:
        return implied_bearish
    return False


def _scan_for_touch(
    path: Sequence[Any], *, claim: Direction, invalidation_level: float
) -> tuple[bool, int | None]:
    """First canonical-path bar that touches the invalidation level, if any.

    Consumes the SAME canonical, conflict-free path D-2C2 already resolved
    (``path_resolution.canonicalization.bars``) -- no second bar
    interpretation, no re-sorting, no re-fetch. A touch is a boundary cross
    (``<=``/``>=``), not a close-only or terminal-only reading: production's
    own live status already treats a level cross as invalidated, and a
    close-only rule here would be silently more lenient than that.
    """
    for index, bar in enumerate(path):
        if claim is Direction.BULLISH:
            crossed = bar.analysis_low <= invalidation_level
        else:
            crossed = bar.analysis_high >= invalidation_level
        if crossed:
            return True, index
    return False, None


def _price_distance(fraction: float | None, anchor_price: float | None) -> float | None:
    if fraction is None or anchor_price is None:
        return None
    return fraction * anchor_price


def _in_r(price_distance: float | None, invalidation_distance: float | None) -> float | None:
    if price_distance is None or invalidation_distance is None:
        return None
    if not (invalidation_distance > 0.0):
        return None
    return price_distance / invalidation_distance


def resolve_setup_and_execution(
    *,
    record: Mapping[str, Any],
    path_resolution: PathResolutionLike,
) -> D2C3Resolution:
    """Resolve setup invalidation, execution quality and thesis invalidation.

    ``record`` is the immutable shadow record ``resolve_direction_and_path``
    was itself given -- this module reads ``record["execution"]`` and
    ``record["gates_triggered"]`` from it directly, exactly as captured, and
    never re-fetches or re-derives either. ``path_resolution`` is the D-2C2
    result already computed for the SAME record; its claim, direction,
    canonical path and anchor price are reused verbatim rather than
    recomputed.

    Thesis invalidation is unconditionally ``NOT_ASSESSABLE`` (see module
    docstring). Never raises; every non-judgeable input resolves to
    ``UNKNOWN``/``UNRESOLVED``/``NOT_APPLICABLE`` rather than a fabricated
    verdict.
    """
    thesis = ThesisInvalidation.NOT_ASSESSABLE
    payload = _payload(record)

    execution_payload = payload.get("execution") if isinstance(payload, Mapping) else None
    was_blocked = bool(execution_payload.get("blocked")) if isinstance(execution_payload, Mapping) else False
    block_reason = (
        execution_payload.get("block_reason") if isinstance(execution_payload, Mapping) else None
    )
    block_reason = str(block_reason) if block_reason else None

    # -- 0. Horizon scope guard. D-2C3 is approved for Tactical observations
    # only; a Structural/Execution record is rejected deterministically rather
    # than silently reinterpreted as Tactical. --------------------------------
    horizon = str(payload.get("horizon") or "").strip().lower() if isinstance(payload, Mapping) else ""
    if horizon != Horizon.TACTICAL.value:
        return _not_applicable(
            "non_tactical_horizon_out_of_scope_for_d2c3",
            was_blocked=was_blocked, block_reason=block_reason,
        )

    # -- 1. Hard D-2C2 data exclusion: reuse its verdict and its reasons,
    # never re-derive a second judgment from the same input. This covers
    # immaturity, missing/unusable anchor, bad timestamp, series unavailable,
    # inversion mismatch, unknown horizon and bar-content conflict alike --
    # every one of them already yields DirectionOutcome.UNRESOLVED. ----------
    if path_resolution.direction is DirectionOutcome.UNRESOLVED:
        return _unresolved_from_data(
            path_resolution.reasons, was_blocked=was_blocked, block_reason=block_reason,
        )

    # -- 2. The claim itself carries no directional content (FLAT/UNAVAILABLE).
    # Not a data problem -- there is simply no long/short setup to invalidate. -
    if path_resolution.direction in (DirectionOutcome.ABSTAINED, DirectionOutcome.NOT_APPLICABLE):
        return _not_applicable(
            "claim_not_directional", was_blocked=was_blocked, block_reason=block_reason,
        )

    # From here, claim_direction is BULLISH or BEARISH and D-2C2 reached a
    # real verdict (CONFIRMED / FAILED / NEUTRAL_WITHIN_BAND).
    claim = path_resolution.claim_direction

    if not isinstance(execution_payload, Mapping):
        return _not_applicable(
            "no_execution_assessment_captured", was_blocked=was_blocked, block_reason=block_reason,
        )

    invalidation_defined = bool(execution_payload.get("invalidation_defined"))
    invalidation_level = _numeric(execution_payload.get("invalidation_level"))
    current_price = _numeric(execution_payload.get("current_price"))

    # -- 3. No usable invalidation: undefined, missing or malformed. ---------
    if not invalidation_defined or invalidation_level is None or current_price is None:
        return _not_applicable(
            "invalidation_not_available", was_blocked=was_blocked, block_reason=block_reason,
        )

    # -- 4. Degenerate setup: invalidation coincides with the reference price. -
    if invalidation_level == current_price:
        measures = InvalidationMeasures(invalidation_level=invalidation_level, direction_agreement=None)
        setup = SetupInvalidationResolution(
            state=SetupInvalidation.NOT_APPLICABLE, measures=measures,
            notes=("degenerate_invalidation_equals_current_price",),
        )
        execution = ExecutionQualityResolution(
            state=ExecutionOutcome.NOT_APPLICABLE, was_blocked=was_blocked, block_reason=block_reason,
            notes=("degenerate_invalidation_equals_current_price",),
        )
        return D2C3Resolution(setup=setup, execution=execution, thesis=thesis)

    # -- 5. Direction-agreement guard (approved design decision #1). ---------
    agrees = _direction_agreement(
        invalidation_level=invalidation_level, current_price=current_price, claim=claim
    )
    if not agrees:
        measures = InvalidationMeasures(
            invalidation_level=invalidation_level, direction_agreement=False
        )
        setup = SetupInvalidationResolution(
            state=SetupInvalidation.NOT_APPLICABLE, measures=measures,
            notes=("entry_plan_direction_disagrees_with_claim_direction",),
        )
        execution = ExecutionQualityResolution(
            state=ExecutionOutcome.NOT_APPLICABLE, was_blocked=was_blocked, block_reason=block_reason,
            notes=("entry_plan_direction_disagrees_with_claim_direction",),
        )
        return D2C3Resolution(setup=setup, execution=execution, thesis=thesis)

    invalidation_distance = abs(current_price - invalidation_level)
    anchor_price = path_resolution.anchor_price
    invalidation_distance_pct = (
        invalidation_distance / anchor_price if anchor_price and anchor_price > 0.0 else None
    )

    # -- 6. Touch detection over the SAME canonical path D-2C2 already resolved.
    path = path_resolution.canonicalization.bars
    touched, bars_to_touch = _scan_for_touch(
        path, claim=claim, invalidation_level=invalidation_level
    )

    if touched:
        setup_state = SetupInvalidation.INVALIDATED
    elif path_resolution.path_complete:
        setup_state = SetupInvalidation.NOT_INVALIDATED
    else:
        # Partial coverage, no touch observed yet: a later bar could still
        # touch it, so this cannot be asserted NOT_INVALIDATED.
        setup_state = SetupInvalidation.UNKNOWN

    mfe_price_distance = _price_distance(path_resolution.excursion.mfe, anchor_price)
    mae_price_distance = _price_distance(path_resolution.excursion.mae, anchor_price)
    mfe_in_r = _in_r(mfe_price_distance, invalidation_distance)
    mae_in_r = _in_r(mae_price_distance, invalidation_distance)

    measures = InvalidationMeasures(
        invalidation_level=invalidation_level,
        invalidation_distance=invalidation_distance,
        invalidation_distance_pct=invalidation_distance_pct,
        direction_agreement=True,
        touched=touched,
        bars_to_touch=bars_to_touch,
        mfe_in_r=mfe_in_r,
        mae_in_r=mae_in_r,
    )
    setup = SetupInvalidationResolution(state=setup_state, measures=measures)

    # -- 7. Execution quality: DEFERRAL_* only, never an entry-timing verdict. -
    if not was_blocked:
        # Ordinary absence of a block is not a deferral of anything.
        execution = ExecutionQualityResolution(
            state=ExecutionOutcome.UNRESOLVED, was_blocked=False, block_reason=None,
        )
        return D2C3Resolution(setup=setup, execution=execution, thesis=thesis)

    veto_gate = _find_veto_gate(payload)
    if veto_gate is None:
        # Blocked, but this record shape does not let us find the gate that
        # caused it. Do not guess; do not reconstruct from mutable production
        # state -- report the safest available verdict.
        execution = ExecutionQualityResolution(
            state=ExecutionOutcome.UNRESOLVED, was_blocked=True, block_reason=block_reason,
            notes=("veto_gate_not_found_in_record",),
        )
        return D2C3Resolution(setup=setup, execution=execution, thesis=thesis)

    if bool(veto_gate.get("applies_to_open_position")):
        # A hold decision on an already-open position, not a new-entry
        # deferral -- a different risk question this module does not judge.
        execution = ExecutionQualityResolution(
            state=ExecutionOutcome.NOT_APPLICABLE, was_blocked=True, block_reason=block_reason,
            notes=("veto_applies_to_open_position_not_new_entry",),
        )
        return D2C3Resolution(setup=setup, execution=execution, thesis=thesis)

    # A genuine new-entry deferral. Judge it against what setup invalidation
    # and direction actually resolved to.
    depends_on = ("direction", "setup_invalidation")
    if setup_state is SetupInvalidation.UNKNOWN:
        execution_state = ExecutionOutcome.UNRESOLVED
    elif setup_state is SetupInvalidation.INVALIDATED:
        execution_state = ExecutionOutcome.DEFERRAL_CORRECT
    elif path_resolution.direction is DirectionOutcome.FAILED:
        execution_state = ExecutionOutcome.DEFERRAL_CORRECT
    elif path_resolution.direction is DirectionOutcome.CONFIRMED:
        execution_state = ExecutionOutcome.DEFERRAL_COSTLY
    else:
        # NEUTRAL_WITHIN_BAND: no material move either way. Neither avoiding
        # nor entering was demonstrably vindicated.
        execution_state = ExecutionOutcome.UNRESOLVED

    execution = ExecutionQualityResolution(
        state=execution_state, was_blocked=True, block_reason=block_reason, depends_on=depends_on,
    )
    return D2C3Resolution(setup=setup, execution=execution, thesis=thesis)


def _find_veto_gate(payload: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """The triggered gate that vetoed execution, from the captured record.

    Reads ``record["gates_triggered"]`` -- an immutable, point-in-time list
    already stored on the record -- never production state. At most one gate
    (``event_risk``) can ever carry ``action == "veto_execution"``.
    """
    gates = payload.get("gates_triggered")
    if not isinstance(gates, Sequence):
        return None
    for gate in gates:
        if isinstance(gate, Mapping) and gate.get("action") == "veto_execution":
            return gate
    return None


__all__ = [
    "D2C3Resolution",
    "ExecutionQualityResolution",
    "InvalidationMeasures",
    "PathResolutionLike",
    "SetupInvalidationResolution",
    "resolve_setup_and_execution",
]
