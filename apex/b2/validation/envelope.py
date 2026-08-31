"""Architecture B2 -- Stage D-2C4: deterministic validation identity and provenance.

Lets a later reader say, precisely: *this exact immutable shadow observation,
evaluated under this exact validation configuration and this exact immutable
ordered market evidence, produced this exact deterministic validation
outcome.* D-2C4 does not compute anything new about the market or the claim --
it binds together what D-2C2 (:mod:`resolve`) and D-2C3 (:mod:`invalidation`)
already resolved into one auditable envelope with four strictly separate
things:

1.  **Logical validation-job identity** (``validation_id``) -- stable across a
    not-yet-matured evaluation and its eventual matured rerun of the SAME job.
2.  **Immutable input/evidence fingerprint** (``input_hash``) -- changes
    exactly when the evidence D-2C2 actually used changes.
3.  **Deterministic outcome fingerprint** (``outcome_hash``) -- changes
    exactly when the resolved verdict changes.
4.  **Human-readable provenance/context** (:class:`ValidationContext`) and
    **optional overlap metadata** (:class:`OverlapMetadata`) -- neither ever
    participates in any of the three hashes above.

Two defects in an earlier proposed identity model are deliberately NOT
reproduced here, because they are provable, not stylistic, objections:

*   ``SHA256(storage_id | config_version | horizon)`` is REJECTED.
    ``ValidationConfig.version`` does not uniquely identify config content --
    two configs can share a version string and differ in ``config_hash``
    (``ValidationConfig(atr_period_bars=20)`` keeps the default version). This
    module uses ``validation_config_hash`` instead.
*   ``sorted(used_market_bar_content_hashes)`` is REJECTED. Sorting a bag of
    content hashes is blind to which physical bar each hash belongs to: two
    canonical paths that swap which day holds which content produce the
    identical sorted list while resolving to different terminal returns,
    different ``bars_to_mfe``/``bars_to_mae``, and potentially different
    verdicts. This module commits to the bars D-2C2's own
    ``canonicalize_bars`` already ordered, unchanged, as an array of
    ``{"observation_id", "content_hash"}`` -- never re-sorted.

A third defect -- ``MaturityAssessment.now``/``.elapsed_fraction`` are live,
call-time-dependent fields that a naive "hash the whole resolution object"
implementation would silently fold into an identity that is supposed to be
wall-clock-free -- is avoided by hand-picking exactly which fields enter
``outcome_hash`` rather than serializing any dataclass wholesale. Only
``maturity.state.value`` (stable once matured) ever enters the hash.

This module is pure: no I/O, no network, no Supabase, no wall clock, no
production import. Nominal imports of :mod:`resolve` and :mod:`invalidation`
are deliberate (D-2C4's entire purpose is binding their concrete result
shapes together) and are the one narrow, approved exception to those two
modules' "nothing else imports me" guard tests. Nothing under
``apex.production_core`` or any capture path imports this module, and B2
remains SHADOW / NON-PRODUCTION / UNCALIBRATED.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping

from .config import ValidationConfig
from .maturity import MaturityState
from .outcome import OutcomeAxes
from ..shadow import canonical_content_hash, canonical_storage_id

# Absolute, not relative, imports for the two nominal D-2C2/D-2C3 result
# types (Decision 4). This is deliberate: the existing "nothing else
# imports me" guards on resolve.py/invalidation.py detect an importer by
# scanning for the substring "validation.resolve"/"validation.invalidation"
# in an ImportFrom's module name, which a same-package relative import
# (``from .resolve import ...``) would silently NOT match -- defeating the
# guard's purpose rather than satisfying it. Writing these two imports in
# absolute form keeps the guard meaningful: it continues to see and verify
# that envelope.py is the ONE approved exception, rather than going blind.
from apex.b2.validation.invalidation import D2C3Resolution
from apex.b2.validation.resolve import DirectionPathResolution

#: The single, centralized schema version for D-2C4's envelope shape and the
#: rules that build validation_id/input_hash/outcome_hash from it. Bumped
#: only when those RULES change; a config-content change is already covered
#: by validation_config_hash, exactly as ValidationConfig.version and
#: .config_hash are already kept orthogonal (see the module docstring).
VALIDATION_SCHEMA_VERSION = "b2-validation-envelope-v1"

#: Matches the separator already used for identity bases elsewhere in this
#: package (``shadow.py``, ``bars.py``). Cannot occur in an ISO timestamp, a
#: hex hash, or a horizon name.
_IDENTITY_SEPARATOR = "|"

_HASH_LENGTH = 32


def _utc(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _canonical_iso(moment: datetime) -> str:
    return _utc(moment).astimezone(timezone.utc).isoformat()


def _payload(record: Mapping[str, Any]) -> Mapping[str, Any]:
    """The shadow record payload, whether wrapped in a storage row or not.

    Duplicated in miniature from ``resolve.py``/``invalidation.py`` rather
    than imported from either -- each already carries its own copy of this
    three-line helper, and there is nothing to gain by reaching into a
    private, unexported name across a module boundary for this.
    """
    inner = record.get("record") if isinstance(record, Mapping) else None
    return inner if isinstance(inner, Mapping) else record


# ===========================================================================
# Canonical JSON / hashing primitive -- ONE definition, used for every hash
# this module produces. Deliberately local to this module: shadow.py,
# bars.py and config.py each already have their own near-identical private
# helper, and this stage does not touch any of those frozen files.
# ===========================================================================

def _canonical_float(value: float) -> float:
    """A hash-safe float: finite, and with signed zero normalised away.

    Non-finite values are REJECTED (raised), never silently emitted as
    JSON's non-standard ``NaN``/``Infinity`` tokens -- every float actually
    produced by D-2C2/D-2C3 is already guaranteed finite by their own input
    guards, so this is a defence-in-depth backstop, not a reachable path
    today. ``-0.0`` is normalised to ``0.0``: ``-0.0 == 0.0`` but
    ``repr(-0.0) != repr(0.0)``, and two mathematically identical results
    must never hash differently over an IEEE-754 sign-of-zero technicality.
    """
    if not math.isfinite(value):
        raise ValueError(f"non-finite float cannot enter a validation hash: {value!r}")
    return 0.0 if value == 0.0 else value


def _canonicalize(value: Any) -> Any:
    """Recursively normalise a value for canonical JSON serialisation.

    Enums become their stable ``.value``. Floats are hash-safe canonicalised.
    Tuples become lists (JSON has no tuple type, and key ordering already
    carries whatever order a tuple encoded). Mappings become plain dicts;
    ``json.dumps(sort_keys=True)`` sorts their keys, and does not reorder
    list/array elements -- so any ordering already committed to by the
    caller (canonical bar path order, reason construction order) survives
    untouched.
    """
    if value is None or isinstance(value, (str, int, bool)):
        return value
    if isinstance(value, float):
        return _canonical_float(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): _canonicalize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_canonicalize(item) for item in value]
    raise TypeError(f"cannot canonicalize value of type {type(value)!r}: {value!r}")


def canonical_json(payload: Any) -> str:
    """Deterministic, compact, UTF-8-safe JSON for hashing or comparison."""
    return json.dumps(
        _canonicalize(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=True
    )


def sha256_hex(basis: str, length: int | None = None) -> str:
    digest = hashlib.sha256(basis.encode("utf-8")).hexdigest()
    return digest[:length] if length else digest


# ===========================================================================
# Overlap metadata -- dataset-structure metadata, never hash-bearing.
# ===========================================================================

@dataclass(frozen=True)
class OverlapContext:
    """The one neighbouring fact D-2C4 needs, injected by the caller.

    D-2C4 never looks this up itself: no DB, no Supabase, no ShadowLog, no
    file. Finding "the previous same-instrument observation" is a query
    against already-existing storage and belongs to a batch/orchestration
    layer outside this pure module.
    """

    previous_storage_id: str | None
    previous_evaluated_at: datetime | None


@dataclass(frozen=True)
class OverlapMetadata:
    """Deterministic overlap arithmetic over one injected neighbour, if any.

    ``valid=False`` represents a genuinely malformed context (a "previous"
    observation strictly in the future) as an explicit, safe non-verdict --
    mirroring how every other axis in this architecture reports "cannot
    judge" rather than fabricating a negative number. It never raises: a bad
    optional annotation must not fail the envelope build that carries the
    hashes that actually matter.
    """

    valid: bool
    invalid_reason: str | None
    window_start: str | None
    window_end: str | None
    horizon_seconds: float | None
    previous_same_instrument_storage_id: str | None
    previous_same_instrument_evaluated_at: str | None
    seconds_since_previous: float | None
    overlaps_previous_window: bool | None
    overlap_seconds: float | None
    overlap_fraction: float | None

    def as_record(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "invalid_reason": self.invalid_reason,
            "window_start": self.window_start,
            "window_end": self.window_end,
            "horizon_seconds": self.horizon_seconds,
            "previous_same_instrument_storage_id": self.previous_same_instrument_storage_id,
            "previous_same_instrument_evaluated_at": self.previous_same_instrument_evaluated_at,
            "seconds_since_previous": self.seconds_since_previous,
            "overlaps_previous_window": self.overlaps_previous_window,
            "overlap_seconds": self.overlap_seconds,
            "overlap_fraction": self.overlap_fraction,
        }


def _resolve_overlap(
    context: OverlapContext | None,
    *,
    window_start: datetime,
    window_end: datetime,
    horizon_seconds: float,
) -> OverlapMetadata:
    ws_iso = _canonical_iso(window_start)
    we_iso = _canonical_iso(window_end)

    if context is None or context.previous_evaluated_at is None:
        return OverlapMetadata(
            valid=True,
            invalid_reason=None,
            window_start=ws_iso,
            window_end=we_iso,
            horizon_seconds=horizon_seconds,
            previous_same_instrument_storage_id=(
                context.previous_storage_id if context is not None else None
            ),
            previous_same_instrument_evaluated_at=None,
            seconds_since_previous=None,
            overlaps_previous_window=None,
            overlap_seconds=None,
            overlap_fraction=None,
        )

    previous = _utc(context.previous_evaluated_at)
    current = _utc(window_start)

    if previous > current:
        # A "previous" observation strictly in the future is not silently
        # reinterpreted -- reported as an explicit invalid context instead.
        return OverlapMetadata(
            valid=False,
            invalid_reason="previous_evaluated_at_is_in_the_future",
            window_start=ws_iso,
            window_end=we_iso,
            horizon_seconds=horizon_seconds,
            previous_same_instrument_storage_id=context.previous_storage_id,
            previous_same_instrument_evaluated_at=_canonical_iso(previous),
            seconds_since_previous=None,
            overlaps_previous_window=None,
            overlap_seconds=None,
            overlap_fraction=None,
        )

    seconds_since_previous = (current - previous).total_seconds()
    # Both windows share one horizon duration, so the intersection of
    # [previous, previous+horizon] and [current, current+horizon] is exactly
    # max(0, horizon - seconds_since_previous), clamped at the horizon itself
    # (reached only when seconds_since_previous <= 0, i.e. a shared instant).
    if horizon_seconds > 0:
        overlap_seconds = max(0.0, horizon_seconds - seconds_since_previous)
        overlap_seconds = min(overlap_seconds, horizon_seconds)
        overlap_fraction = max(0.0, min(1.0, overlap_seconds / horizon_seconds))
    else:
        overlap_seconds = 0.0
        overlap_fraction = 0.0

    return OverlapMetadata(
        valid=True,
        invalid_reason=None,
        window_start=ws_iso,
        window_end=we_iso,
        horizon_seconds=horizon_seconds,
        previous_same_instrument_storage_id=context.previous_storage_id,
        previous_same_instrument_evaluated_at=_canonical_iso(previous),
        seconds_since_previous=seconds_since_previous,
        overlaps_previous_window=overlap_seconds > 0.0,
        overlap_seconds=overlap_seconds,
        overlap_fraction=overlap_fraction,
    )


# ===========================================================================
# Finalization status -- derived, human-readable, never hash-bearing.
# ===========================================================================

def _finalization_status(state: MaturityState) -> str:
    if state is MaturityState.NOT_MATURED:
        return "not_matured"
    if state is MaturityState.MATURED_AWAITING_BARS:
        return "provisional_awaiting_bars"
    if state is MaturityState.MATURED_PARTIAL:
        return "provisional_partial"
    return "final"


# ===========================================================================
# Validation context -- human-readable provenance, never hash-bearing.
# ===========================================================================

@dataclass(frozen=True)
class ValidationContext:
    """Everything needed to audit the three hashes without reversing them."""

    validation_id: str
    validation_schema_version: str
    validation_config_version: str
    validation_config_hash: str
    input_hash: str
    outcome_hash: str

    shadow_storage_id: str
    shadow_record_id: str
    shadow_schema_version: int
    shadow_content_hash: str

    instrument: str
    horizon: str
    evaluated_at: str

    anchor_status: str
    anchor_source: str | None
    market_symbol: str | None

    series_binding_quality: str
    bound_symbol: str | None
    inversion_agreement: str
    cross_source: bool
    cross_granularity: bool

    eligibility_pool: str
    maturity_state: str
    coverage_status: str | None
    finalization_status: str

    used_observation_ids: tuple[str, ...]
    used_bar_content_hashes: tuple[str, ...]
    terminal_observation_id: str | None
    terminal_bar_time: str | None
    used_bar_count: int
    conflict_ids: tuple[str, ...]
    malformed_row_count: int | None
    duplicates_collapsed: int

    setup_notes: tuple[str, ...] = ()
    execution_notes: tuple[str, ...] = ()

    def as_record(self) -> dict[str, Any]:
        return {
            "validation_id": self.validation_id,
            "validation_schema_version": self.validation_schema_version,
            "validation_config_version": self.validation_config_version,
            "validation_config_hash": self.validation_config_hash,
            "input_hash": self.input_hash,
            "outcome_hash": self.outcome_hash,
            "shadow_storage_id": self.shadow_storage_id,
            "shadow_record_id": self.shadow_record_id,
            "shadow_schema_version": self.shadow_schema_version,
            "shadow_content_hash": self.shadow_content_hash,
            "instrument": self.instrument,
            "horizon": self.horizon,
            "evaluated_at": self.evaluated_at,
            "anchor_status": self.anchor_status,
            "anchor_source": self.anchor_source,
            "market_symbol": self.market_symbol,
            "series_binding_quality": self.series_binding_quality,
            "bound_symbol": self.bound_symbol,
            "inversion_agreement": self.inversion_agreement,
            "cross_source": self.cross_source,
            "cross_granularity": self.cross_granularity,
            "eligibility_pool": self.eligibility_pool,
            "maturity_state": self.maturity_state,
            "coverage_status": self.coverage_status,
            "finalization_status": self.finalization_status,
            "used_observation_ids": list(self.used_observation_ids),
            "used_bar_content_hashes": list(self.used_bar_content_hashes),
            "terminal_observation_id": self.terminal_observation_id,
            "terminal_bar_time": self.terminal_bar_time,
            "used_bar_count": self.used_bar_count,
            "conflict_ids": list(self.conflict_ids),
            "malformed_row_count": self.malformed_row_count,
            "duplicates_collapsed": self.duplicates_collapsed,
            "setup_notes": list(self.setup_notes),
            "execution_notes": list(self.execution_notes),
        }


# ===========================================================================
# The envelope itself.
# ===========================================================================

@dataclass(frozen=True)
class ValidationEnvelope:
    """The complete, immutable D-2C4 result for one observation.

    ``input_hash_basis``/``outcome_hash_basis`` are exposed verbatim so a
    reader can see exactly what was hashed without recomputing it --
    auditability was the entire point. ``outcome_axes`` is the six-axis
    ``OutcomeAxes`` instance constructed purely as an internal invariant
    check (see the module docstring and Decision 5); it is NEVER what
    ``outcome_hash`` is computed from, and is exposed only as a convenience
    byproduct of a check that already had to run.
    """

    validation_id: str
    input_hash: str
    outcome_hash: str
    input_hash_basis: Mapping[str, Any]
    outcome_hash_basis: Mapping[str, Any]
    context: ValidationContext
    overlap: OverlapMetadata | None
    outcome_axes: OutcomeAxes

    def as_record(self) -> dict[str, Any]:
        return {
            "stage": "d2c4",
            "validation_schema_version": VALIDATION_SCHEMA_VERSION,
            "validation_id": self.validation_id,
            "input_hash": self.input_hash,
            "outcome_hash": self.outcome_hash,
            "input_hash_basis": _canonicalize(self.input_hash_basis),
            "outcome_hash_basis": _canonicalize(self.outcome_hash_basis),
            "context": self.context.as_record(),
            "overlap": self.overlap.as_record() if self.overlap is not None else None,
            "outcome_axes": self.outcome_axes.as_record(),
        }


def build_validation_envelope(
    *,
    record: Mapping[str, Any],
    path_resolution: DirectionPathResolution,
    d2c3_resolution: D2C3Resolution,
    validation_config: ValidationConfig,
    overlap_context: OverlapContext | None = None,
    malformed_row_count: int | None = None,
) -> ValidationEnvelope:
    """Bind one D-2C2 result and one D-2C3 result into a validation envelope.

    ``record`` is the immutable shadow record both resolutions were computed
    from -- read here only for the identity fields
    (``record_id``/``instrument``/``horizon``/``evaluated_at``) and the raw
    payload used to recompute ``shadow_content_hash``, never re-interpreted.
    ``path_resolution`` and ``d2c3_resolution`` are consumed exactly as
    already resolved: nothing here reruns D-2C2 or D-2C3, fetches bars, or
    reads a clock. ``malformed_row_count`` is optional and defaults to
    ``None`` (genuinely unavailable) because ``DirectionPathResolution``
    does not carry it -- see the implementation report for why.

    Never mutates any argument. Raises ``OutcomeInvariantError`` (from
    ``apex.b2.validation.outcome``) only if the D-2C2 and D-2C3 results
    combine into a state the architecture has already declared structurally
    impossible -- a defensive, already-tested invariant check, not new logic.
    """
    payload = _payload(record)

    # -- shadow identity: ALWAYS recomputed from the payload's own fields,
    # never trusted from an optional storage-row wrapper key. Mirrors
    # shadow.record_to_row's own stripping exactly, so the recomputed value
    # is byte-identical to whatever a real Storage-V2 row would carry. -----
    record_id = str(payload.get("record_id") or "").strip()
    instrument = str(payload.get("instrument") or "").strip()
    horizon = str(payload.get("horizon") or "").strip()
    evaluated_at_raw = str(payload.get("evaluated_at") or "").strip()
    try:
        shadow_schema_version = int(payload.get("schema_version") or 1)
    except (TypeError, ValueError):
        shadow_schema_version = 1

    shadow_storage_id = canonical_storage_id(record_id, instrument, horizon, evaluated_at_raw)
    shadow_content_hash = canonical_content_hash(payload)

    validation_config_hash = validation_config.config_hash
    validation_config_version = validation_config.version

    # -- Decision 1: validation_id identifies the JOB, not the result. -----
    validation_id = sha256_hex(
        _IDENTITY_SEPARATOR.join(
            [shadow_storage_id, horizon, validation_config_hash, VALIDATION_SCHEMA_VERSION]
        ),
        _HASH_LENGTH,
    )

    # -- Decision 2: input_hash commits to the canonical path IN ORDER. ----
    anchor_resolution_basis = {
        "status": path_resolution.anchor.status.value,
        "point_in_time": path_resolution.anchor.status.is_point_in_time,
        "caveats": list(path_resolution.anchor.caveats),
    }
    series_binding_basis = {
        "quality": path_resolution.binding.quality.value,
        "bound_symbol": path_resolution.binding.bound_symbol,
        "inversion": path_resolution.binding.inversion.value,
        "cross_source": path_resolution.binding.cross_source,
        "cross_granularity": path_resolution.binding.cross_granularity,
    }
    # NOT sorted. This is exactly the canonical, deterministic order
    # canonicalize_bars() already established -- committing to it, not
    # re-deriving or re-ordering it.
    used_bars_basis = [
        {"observation_id": bar.observation_id, "content_hash": bar.content_hash}
        for bar in path_resolution.canonicalization.bars
    ]
    # Conflicting evidence is represented SEPARATELY from used evidence, so a
    # BAR_CONTENT_CONFLICT result never hashes as though the conflict never
    # existed, and never as though its bars were used.
    conflicts_basis = [
        {"observation_id": conflict.observation_id, "content_hashes": list(conflict.content_hashes)}
        for conflict in path_resolution.canonicalization.conflicts
    ]

    input_hash_basis: dict[str, Any] = {
        "shadow_content_hash": shadow_content_hash,
        "validation_config_hash": validation_config_hash,
        "anchor_resolution": anchor_resolution_basis,
        "series_binding": series_binding_basis,
        "used_bars": used_bars_basis,
        "conflicts": conflicts_basis,
        "malformed_row_count": malformed_row_count,
    }
    input_hash = sha256_hex(canonical_json(input_hash_basis), _HASH_LENGTH)

    # -- Decision 5: OutcomeAxes as an internal sanity check only. ----------
    # reasons is, in every current resolve.py branch, either empty or a
    # single element (verified directly against the source: every
    # _unresolved() call site passes a literal one-element tuple, and the
    # one list-built branch has exactly one possible append). This mapping
    # is therefore lossless today, not a guess.
    exclusion_reason = path_resolution.reasons[0] if path_resolution.reasons else None
    outcome_axes = OutcomeAxes(
        data_resolution=path_resolution.data_resolution,
        direction=path_resolution.direction,
        setup_invalidation=d2c3_resolution.setup.state,
        thesis_invalidation=d2c3_resolution.thesis,
        execution=d2c3_resolution.execution.state,
        excursion=path_resolution.excursion,
        eligibility_pool=path_resolution.eligibility_pool,
        exclusion_reason=exclusion_reason,
    )

    # -- outcome_hash: hand-picked stable fields only. Never a serialized
    # dataclass. maturity.now/.elapsed_fraction/.window_end/.evaluated_at
    # are deliberately absent -- only maturity.state.value (Decision 3).
    # setup.notes/execution.notes are deliberately absent (diagnostic text,
    # never authoritative). d2c3_resolution.setup.reasons is deliberately
    # absent: it is always either identical to path_resolution.reasons or
    # empty (verified against invalidation.py's source), so including it
    # would add no information while duplicating what "reasons" already
    # carries. -------------------------------------------------------------
    outcome_hash_basis: dict[str, Any] = {
        "claim_direction": path_resolution.claim_direction.value,
        "direction": path_resolution.direction.value,
        "data_resolution": path_resolution.data_resolution.value,
        "reasons": [reason.value for reason in path_resolution.reasons],
        "terminal_return": path_resolution.excursion.terminal_return,
        "mfe": path_resolution.excursion.mfe,
        "mae": path_resolution.excursion.mae,
        "mfe_atr": path_resolution.excursion.mfe_atr,
        "mae_atr": path_resolution.excursion.mae_atr,
        "bars_to_mfe": path_resolution.excursion.bars_to_mfe,
        "bars_to_mae": path_resolution.excursion.bars_to_mae,
        "path_bars": path_resolution.excursion.path_bars,
        "neutral_band": path_resolution.band.band,
        "neutral_band_mode": path_resolution.band.mode.value,
        "path_complete": path_resolution.path_complete,
        "excursion_is_lower_bound": path_resolution.excursion_is_lower_bound,
        "eligibility_pool": path_resolution.eligibility_pool.value,
        "terminal_bar_time": path_resolution.terminal_bar_time,
        "anchor_price": path_resolution.anchor_price,
        "coverage_status": path_resolution.coverage_status,
        "maturity_state": path_resolution.maturity.state.value,
        "setup_invalidation": d2c3_resolution.setup.state.value,
        "invalidation_level": d2c3_resolution.setup.measures.invalidation_level,
        "invalidation_distance": d2c3_resolution.setup.measures.invalidation_distance,
        "invalidation_distance_pct": d2c3_resolution.setup.measures.invalidation_distance_pct,
        "direction_agreement": d2c3_resolution.setup.measures.direction_agreement,
        "touched": d2c3_resolution.setup.measures.touched,
        "bars_to_touch": d2c3_resolution.setup.measures.bars_to_touch,
        "mfe_in_r": d2c3_resolution.setup.measures.mfe_in_r,
        "mae_in_r": d2c3_resolution.setup.measures.mae_in_r,
        "execution_outcome": d2c3_resolution.execution.state.value,
        "was_blocked": d2c3_resolution.execution.was_blocked,
        "block_reason": d2c3_resolution.execution.block_reason,
        "depends_on": list(d2c3_resolution.execution.depends_on),
        "thesis_invalidation": d2c3_resolution.thesis.value,
    }
    outcome_hash = sha256_hex(canonical_json(outcome_hash_basis), _HASH_LENGTH)

    finalization_status = _finalization_status(path_resolution.maturity.state)

    overlap = (
        _resolve_overlap(
            overlap_context,
            window_start=path_resolution.maturity.evaluated_at,
            window_end=path_resolution.maturity.window_end,
            horizon_seconds=path_resolution.maturity.window.total_seconds(),
        )
        if overlap_context is not None
        else None
    )

    used_path = path_resolution.canonicalization.bars
    context = ValidationContext(
        validation_id=validation_id,
        validation_schema_version=VALIDATION_SCHEMA_VERSION,
        validation_config_version=validation_config_version,
        validation_config_hash=validation_config_hash,
        input_hash=input_hash,
        outcome_hash=outcome_hash,
        shadow_storage_id=shadow_storage_id,
        shadow_record_id=record_id,
        shadow_schema_version=shadow_schema_version,
        shadow_content_hash=shadow_content_hash,
        instrument=instrument,
        horizon=horizon,
        evaluated_at=evaluated_at_raw,
        anchor_status=path_resolution.anchor.status.value,
        anchor_source=(
            path_resolution.anchor.anchor.price_source
            if path_resolution.anchor.anchor is not None
            else None
        ),
        market_symbol=path_resolution.anchor.symbol or None,
        series_binding_quality=path_resolution.binding.quality.value,
        bound_symbol=path_resolution.binding.bound_symbol,
        inversion_agreement=path_resolution.binding.inversion.value,
        cross_source=path_resolution.binding.cross_source,
        cross_granularity=path_resolution.binding.cross_granularity,
        eligibility_pool=path_resolution.eligibility_pool.value,
        maturity_state=path_resolution.maturity.state.value,
        coverage_status=path_resolution.coverage_status,
        finalization_status=finalization_status,
        used_observation_ids=tuple(bar.observation_id for bar in used_path),
        used_bar_content_hashes=tuple(bar.content_hash for bar in used_path),
        terminal_observation_id=(used_path[-1].observation_id if used_path else None),
        terminal_bar_time=path_resolution.terminal_bar_time,
        used_bar_count=len(used_path),
        conflict_ids=tuple(
            conflict.observation_id for conflict in path_resolution.canonicalization.conflicts
        ),
        malformed_row_count=malformed_row_count,
        duplicates_collapsed=path_resolution.canonicalization.duplicates_collapsed,
        setup_notes=d2c3_resolution.setup.notes,
        execution_notes=d2c3_resolution.execution.notes,
    )

    return ValidationEnvelope(
        validation_id=validation_id,
        input_hash=input_hash,
        outcome_hash=outcome_hash,
        input_hash_basis=input_hash_basis,
        outcome_hash_basis=outcome_hash_basis,
        context=context,
        overlap=overlap,
        outcome_axes=outcome_axes,
    )


__all__ = [
    "VALIDATION_SCHEMA_VERSION",
    "OverlapContext",
    "OverlapMetadata",
    "ValidationContext",
    "ValidationEnvelope",
    "build_validation_envelope",
    "canonical_json",
    "sha256_hex",
]
