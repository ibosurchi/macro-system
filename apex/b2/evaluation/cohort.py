"""Architecture B2 -- Stage D-2D1: deterministic cohort construction and narrow metrics.

D-2D0 answers "what happened to ONE observation". This module answers the only
question a SET of them can honestly support: *how much trustworthy evidence is
there, of what kind, and is it enough to say anything at all.* It is a research
accounting layer, not a scoreboard.

One rule dominates every design decision here, and every helper below exists to
enforce it:

    NOTHING ABOUT COHORT STRUCTURE MAY DEPEND ON AN OUTCOME.

Duplicate selection, episode anchoring, canonical ordering, admission and
denominator membership are all decided from point-in-time facts alone --
timestamps, storage identity, capture provenance and maturity. None of them may
read a direction, a readiness tier or a provenance grade, because each of those
is downstream of what the market did. The audit that authorised this stage
demonstrated the failure concretely: ``classify_readiness`` reads
``direction.is_verdict``, so a "keep the highest-readiness duplicate" rule
prefers observations that escaped the neutral band -- and since the band scales
with the anchor's ATR, that is selection on low recorded volatility. It would
bias the confirmation rate through a volatility channel, not merely deflate the
neutral count. That rule is therefore rejected outright and
:func:`_representative` is written so it cannot be reintroduced.

The order of operations is load-bearing and fixed:

    1. separate D-2D0 lineage defects   (never members, never denominators)
    2. admission checks                 (config lineage, timestamp, window, as_of)
    3. canonical ordering               (parsed evaluated_at, then storage id)
    4. logical deduplication            (point-in-time representative only)
    5. disjoint episode partition       (per instrument+horizon, filter-blind)
    6. ONLY THEN stratify, classify pools and count
    7. ratios, with sample gating
    8. identity hashes

Steps 4 and 5 run **before** step 6 deliberately. If readiness or eligibility
filtering ran first, dropping an observation would promote its successor to
episode anchor -- and since readiness depends on the outcome, the episode
partition would become a function of the results. That is look-ahead, and the
ordering above is what prevents it.

Two further separations are structural rather than stylistic:

*   **Readiness is reporting, never a denominator filter.**
    ``ReadinessTier.EXCLUDED`` is heterogeneous -- it holds both "the data was
    unusable" and "the window closed on a resolved NEUTRAL". Filtering a
    denominator by it would both smuggle the outcome into the denominator and
    silently drop final neutral evidence the neutral diagnostic needs. Metric
    bases are built from ``maturity_state`` and ``eligibility_pool``, which are
    a time/coverage fact and a capture-provenance fact respectively. Readiness
    is still counted and reported.

*   **Captured and reconstructed-research evidence never merge**, and neither do
    instruments, horizons or provenance grades. There is no pooled ratio and no
    overall score, because producing one would require a cross-instrument
    dependence model this architecture has deliberately withheld.

This module is pure: no clock, no I/O, no network, no persistence, no
randomness, no threads, and no import of ``production_core`` or either bridge.
``as_of`` is required and injected, because maturity -- and therefore
``outcome_hash`` and readiness -- move with it, so a cohort result is meaningful
only relative to a pinned instant.

B2 remains SHADOW / NON-PRODUCTION / UNCALIBRATED. Nothing here calibrates,
promotes, votes, or informs a decision.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Iterable, Mapping, Sequence

from ..enums import Direction
from ..validation.config import ValidationConfig
from ..validation.maturity import MaturityState
from ..validation.outcome import (
    DataResolution,
    DirectionOutcome,
    EligibilityPool,
    ExclusionReason,
)
from .observation import (
    EVALUATION_SCHEMA_VERSION,
    DefectReason,
    EvaluatedObservation,
    LineageDefect,
    ProvenanceGrade,
)

# Absolute, not relative, imports for the two guarded validation modules.
# Same reasoning envelope.py and observation.py already document: the
# "nothing else imports me" guards detect an importer by scanning an
# ImportFrom's module name for the substrings "validation.envelope" and
# "validation.readiness", which a same-package relative import would
# silently NOT match -- defeating each guard rather than satisfying it.
# Written in absolute form the guards keep seeing this module and keep
# verifying that it is the one further importer authorized by Stage D-2D1.
#
# The surface taken from each is deliberately minimal: ONE canonical-JSON
# helper and ONE hash helper from D-2C4 (so cohort identities normalise
# floats and enums byte-identically to the envelope hashes, instead of a
# fourth private copy drifting), and ONE enum from D-2C5 (so a future tier
# cannot be silently miscounted by a hardcoded key list). Neither D-2C2's
# ``resolve`` nor D-2C3's ``invalidation`` is imported: this stage never
# re-resolves anything.
from apex.b2.validation.envelope import (
    VALIDATION_SCHEMA_VERSION,
    canonical_json,
    sha256_hex,
)
from apex.b2.validation.readiness import ReadinessTier

#: Versions the cohort SHAPE and the rules that build its identities. Bumped
#: only when those rules change; a floor or config value change is already
#: covered by the relevant config hash, exactly as ``ValidationConfig.version``
#: and ``.config_hash`` are already kept orthogonal.
COHORT_SCHEMA_VERSION = "b2-evaluation-cohort-v1"

#: Versions the duplicate policy in :func:`_representative`. A change here
#: changes which observation answers a question and MUST invalidate a cohort id.
DEDUP_POLICY_VERSION = "b2-dedup-logical-earliest-v1"

#: Versions the episode policy in :func:`_partition_episodes`.
EPISODE_POLICY_VERSION = "b2-episode-disjoint-window-v1"

#: Versions the :class:`CohortConfig` shape.
COHORT_CONFIG_VERSION = "b2-cohortcfg-v1"

#: The stratification key, declared as data so a future change to it is visible
#: in ``cohort_id`` rather than only in repository history. Not configurable:
#: every one of these must stay separate (see the module docstring).
STRATIFY_BY: tuple[str, ...] = (
    "instrument",
    "horizon",
    "eligibility_pool",
    "provenance_grade",
)

_HASH_LENGTH = 32


# ===========================================================================
# Small pure helpers
# ===========================================================================

def _utc(moment: datetime) -> datetime:
    """UTC-aware, matching the convention every module in this package uses."""
    aware = moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)
    return aware.astimezone(timezone.utc)


def _canonical_iso(moment: datetime) -> str:
    return _utc(moment).isoformat()


def _parse_iso(raw: Any) -> datetime | None:
    """Parse a stored ``evaluated_at``, or return None. Never raises.

    ``ValidationContext.evaluated_at`` is the RAW string carried inside the
    shadow payload, because that exact string is what ``storage_id`` commits
    to. It is therefore not guaranteed parseable: D-2C2 resolves an
    unparseable timestamp to UNRESOLVED/BAD_TIMESTAMP and D-2C5's lineage
    check skips the comparison it cannot make, so a genuinely unparseable
    value reaches this module attached to an otherwise well-formed
    observation. Returning None lets that become an explicit admission
    failure instead of an exception raised from inside a sort.
    """
    try:
        return _utc(datetime.fromisoformat(str(raw or "").replace("Z", "+00:00")))
    except (TypeError, ValueError):
        return None


def _counts(members: Iterable[Any], vocabulary: Iterable[Any]) -> dict[str, int]:
    """A count over a CLOSED vocabulary, in the vocabulary's declared order.

    Every member of the vocabulary is present, including zeros: a state that
    never occurred and a state that was never counted must not look alike.
    Iterating the enum rather than the data also fixes key order, so the
    result is byte-stable regardless of input order.
    """
    tally = {str(item.value): 0 for item in vocabulary}
    for member in members:
        key = str(getattr(member, "value", member))
        if key in tally:
            tally[key] += 1
    return tally


# ===========================================================================
# A. COHORT CONFIGURATION
# ===========================================================================

@dataclass(frozen=True)
class CohortConfig:
    """Versioned research floors below which no ratio VALUE is published.

    These are guardrails against displaying a confident-looking percentage
    over a handful of overlapping observations. They are **not** significance
    thresholds, they are not fitted, and they carry no statistical claim
    whatsoever -- exactly the status ``ValidationConfig`` already declares for
    its own research defaults, and stamped the same way in
    :meth:`as_provenance`.

    Injectable so a future research exercise can vary a floor deliberately and
    have the change appear in ``cohort_id`` -- rather than by accident, where
    it would appear nowhere.
    """

    version: str = COHORT_CONFIG_VERSION

    #: Minimum size of a ratio's own denominator.
    min_denominator: int = 30

    #: Minimum number of DISJOINT EPISODES backing that denominator. In
    #: practice this is the binding constraint: at a 14-day tactical window,
    #: ten disjoint episodes is roughly 140 days of continuous per-instrument
    #: capture before any tactical rate may be printed.
    min_disjoint_episode_verdict_n: int = 10

    def __post_init__(self) -> None:
        if not str(self.version).strip():
            raise ValueError("A cohort config must carry a version.")
        if int(self.min_denominator) < 1:
            raise ValueError("min_denominator must be at least 1.")
        if int(self.min_disjoint_episode_verdict_n) < 1:
            raise ValueError("min_disjoint_episode_verdict_n must be at least 1.")

    @property
    def chosen(self) -> dict[str, Any]:
        """Exactly the values that were chosen, for hashing and for the record."""
        return {
            "min_denominator": int(self.min_denominator),
            "min_disjoint_episode_verdict_n": int(self.min_disjoint_episode_verdict_n),
        }

    @property
    def config_hash(self) -> str:
        return sha256_hex(canonical_json(self.chosen), 16)

    def as_provenance(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "chosen": self.chosen,
            "config_hash": self.config_hash,
            # Stated on the record itself so no reader mistakes these for
            # calibrated, fitted or statistically derived values.
            "status": "VERSIONED RESEARCH DEFAULTS -- NOT CALIBRATED",
        }


#: The configuration D-2D1 uses unless a caller supplies another.
DEFAULT_COHORT_CONFIG = CohortConfig()


# ===========================================================================
# B. ADMISSION
# ===========================================================================

class AdmissionFailureReason(Enum):
    """Why one evaluated observation could not enter this cohort.

    Deliberately distinct from D-2D0's ``DefectReason`` (which describes a
    failed COMPOSITION) and from D-2C2's ``ExclusionReason`` (which describes
    unusable MARKET EVIDENCE). These three vocabularies answer three different
    questions with three different owners, and merging any of them would make
    "this cohort was assembled wrongly" indistinguishable from "the market data
    was missing".
    """

    #: The member was validated under a DIFFERENT configuration than the one
    #: supplied here. A mixed-config cohort is prohibited: its members'
    #: neutral bands and forward windows would not be comparable.
    CONFIG_HASH_MISMATCH = "config_hash_mismatch"
    #: ``context.evaluated_at`` cannot be parsed, so the member can take no
    #: place in a canonical time ordering and no part in episode spacing.
    UNPARSEABLE_EVALUATED_AT = "unparseable_evaluated_at"
    #: The supplied configuration declares no forward window for this
    #: horizon. Defence in depth: with a matching config hash D-2C2 would
    #: already have refused the observation, but a window is never guessed.
    UNKNOWN_HORIZON_WINDOW = "unknown_horizon_window"
    #: The member's maturity contradicts the declared ``as_of``. Catches a
    #: cohort assembled from observations evaluated at a different instant.
    AS_OF_INCONSISTENT = "as_of_inconsistent"
    #: Two artifacts claim ONE physical point-in-time identity and carry
    #: different validation results. Reported, never arbitrated -- exactly as
    #: ``canonicalize_bars`` withholds a conflicting bar identity rather than
    #: picking one. Choosing between them would have to break the tie on
    #: something, and the only thing left to break it on is the outcome.
    PHYSICAL_IDENTITY_CONFLICT = "physical_identity_conflict"


@dataclass(frozen=True)
class AdmissionFailure:
    """One member refused entry, with a stable machine-readable reason.

    Built only from immutable, input-derived facts and keyed by the same
    ``shadow_storage_id`` a successful member carries, so a refusal is exactly
    as traceable as an admission. ``detail`` is diagnostic text: it never
    enters an identity and never decides anything.
    """

    shadow_storage_id: str
    shadow_record_id: str
    instrument: str
    horizon: str
    evaluated_at: str
    reason: AdmissionFailureReason
    detail: str

    def as_record(self) -> dict[str, Any]:
        return {
            "shadow_storage_id": self.shadow_storage_id,
            "shadow_record_id": self.shadow_record_id,
            "instrument": self.instrument,
            "horizon": self.horizon,
            "evaluated_at": self.evaluated_at,
            "reason": self.reason.value,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class _Member:
    """One admitted observation plus the point-in-time facts D-2D1 derived.

    Private. ``evaluated_at`` is the PARSED instant used for ordering and
    spacing; the raw string stays on ``observation.envelope.context`` and is
    what every identity commits to.
    """

    observation: EvaluatedObservation
    evaluated_at: datetime
    window: timedelta

    @property
    def context(self) -> Any:
        return self.observation.envelope.context

    @property
    def storage_id(self) -> str:
        return self.context.shadow_storage_id

    @property
    def logical_key(self) -> tuple[str, str, str]:
        """Logical forecast identity: one INTENDED observation.

        ``shadow_record_id`` is sha256 over instrument, horizon and the UTC
        HOUR BUCKET, so by construction every observation taken within one
        hour for one instrument shares it. Two point-in-time observations in
        that hour are two ``storage_id``s under one ``record_id`` -- one
        intended forecast recorded twice, which is what
        :func:`_deduplicate` collapses.
        """
        return (self.context.instrument, self.context.horizon,
                self.context.shadow_record_id)

    @property
    def group_key(self) -> tuple[str, str]:
        return (self.context.instrument, self.context.horizon)

    @property
    def result_identity(self) -> tuple[str, str, str]:
        """The validation RESULT identity, distinct from both other layers."""
        envelope = self.observation.envelope
        return (envelope.validation_id, envelope.input_hash, envelope.outcome_hash)

    @property
    def order_key(self) -> tuple[datetime, str]:
        """The ONLY ordering this module uses. Point-in-time facts only.

        Total over any set this module actually orders: two members sharing a
        ``storage_id`` are the same physical observation, and
        :func:`_collapse_physical` has already either collapsed them (same
        result) or refused them (different results) before any sort runs. That
        step is what keeps this key total WITHOUT adding a
        result-derived tiebreak, which would be outcome-derived ordering.
        """
        return (self.evaluated_at, self.storage_id)

    @property
    def is_final(self) -> bool:
        """Whether the forward window closed with usable coverage.

        ``MaturityState.MATURED`` alone. ``MATURED_PARTIAL`` and
        ``MATURED_AWAITING_BARS`` are elapsed but not final, matching how
        ``classify_readiness`` already draws the same line.
        """
        return self.context.maturity_state == MaturityState.MATURED.value

    @property
    def direction(self) -> DirectionOutcome:
        return self.observation.envelope.outcome_axes.direction


# ===========================================================================
# C. RATIOS
# ===========================================================================

class RatioState(Enum):
    """Whether a ratio VALUE may be published, and if not, why not.

    Precedence, highest first: ``NO_DENOMINATOR`` (nothing to divide),
    ``WITHHELD`` (the sample is structurally unrepresentative, so more data
    will not help), ``INSUFFICIENT_SAMPLE`` (too little evidence, more data
    will help), ``SUFFICIENT``.
    """

    SUFFICIENT = "sufficient"
    INSUFFICIENT_SAMPLE = "insufficient_sample"
    NO_DENOMINATOR = "no_denominator"
    #: Deliberately distinct from INSUFFICIENT_SAMPLE. Reporting a selection
    #: problem as a size problem would tell a reader to wait for more data,
    #: when more data cannot fix a sample selected by an outage.
    WITHHELD = "withheld"


class RatioNote(Enum):
    """Stable machine-readable reasons. Never prose, never hash-bearing."""

    #: This stratum's exact-binding captured subset coexists, in the SAME
    #: instrument and horizon, with substituted-binding observations. The
    #: exact subset is therefore selected by the absence of substitution --
    #: for a fallback-configured instrument, by an upstream data outage --
    #: and is not representative of the instrument. Derived generically from
    #: binding facts present in the cohort; no instrument is named in code.
    PROVENANCE_OUTAGE_SELECTED = "provenance_outage_selected"


@dataclass(frozen=True)
class Ratio:
    """One narrow ratio, with everything needed to audit it.

    Never a bare float. A ratio that may not be published carries
    ``value=None`` and says why in ``state``/``notes`` -- never a value plus a
    warning, because a warning is the part that gets dropped downstream.
    """

    name: str
    value: float | None
    state: RatioState
    numerator: int
    denominator: int
    denominator_name: str
    #: The episode count this ratio's gate actually applied, and its name.
    #: Each ratio gates on the episode count matching its OWN denominator:
    #: gating the neutral diagnostic on verdict episodes would silence it in
    #: exactly the case it exists to detect -- a band so wide that nothing
    #: ever produces a verdict.
    episode_denominator: int
    episode_denominator_name: str
    disjoint_episode_n: int
    disjoint_episode_verdict_n: int
    min_denominator: int
    min_disjoint_episode_verdict_n: int
    notes: tuple[RatioNote, ...] = ()

    def as_record(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "value": self.value,
            "state": self.state.value,
            "numerator": self.numerator,
            "denominator": self.denominator,
            "denominator_name": self.denominator_name,
            "episode_denominator": self.episode_denominator,
            "episode_denominator_name": self.episode_denominator_name,
            "disjoint_episode_n": self.disjoint_episode_n,
            "disjoint_episode_verdict_n": self.disjoint_episode_verdict_n,
            "floor_applied": {
                "min_denominator": self.min_denominator,
                "min_disjoint_episode_verdict_n": self.min_disjoint_episode_verdict_n,
            },
            "notes": [note.value for note in self.notes],
        }


def _build_ratio(
    *,
    name: str,
    numerator: int,
    denominator: int,
    denominator_name: str,
    episode_denominator: int,
    episode_denominator_name: str,
    disjoint_episode_n: int,
    disjoint_episode_verdict_n: int,
    cohort_config: CohortConfig,
    withheld: bool,
) -> Ratio:
    """Apply the dual floor. Counts are emitted whatever the outcome."""
    notes: tuple[RatioNote, ...] = ()
    if denominator <= 0:
        state, value = RatioState.NO_DENOMINATOR, None
    elif withheld:
        state, value = RatioState.WITHHELD, None
        notes = (RatioNote.PROVENANCE_OUTAGE_SELECTED,)
    elif (
        denominator < cohort_config.min_denominator
        or episode_denominator < cohort_config.min_disjoint_episode_verdict_n
    ):
        state, value = RatioState.INSUFFICIENT_SAMPLE, None
    else:
        state, value = RatioState.SUFFICIENT, numerator / denominator

    return Ratio(
        name=name,
        value=value,
        state=state,
        numerator=numerator,
        denominator=denominator,
        denominator_name=denominator_name,
        episode_denominator=episode_denominator,
        episode_denominator_name=episode_denominator_name,
        disjoint_episode_n=disjoint_episode_n,
        disjoint_episode_verdict_n=disjoint_episode_verdict_n,
        min_denominator=cohort_config.min_denominator,
        min_disjoint_episode_verdict_n=cohort_config.min_disjoint_episode_verdict_n,
        notes=notes,
    )


# ===========================================================================
# D. STRATA
# ===========================================================================

@dataclass(frozen=True)
class StratumKey:
    """The four dimensions that must never be merged.

    Instruments and horizons stay apart because pooling them would require a
    cross-instrument dependence model this architecture has withheld. Pools
    stay apart because captured evidence is the only kind a calibration claim
    may ever draw on. Provenance grades stay apart because an exact binding
    and a substituted one are not observations of the same series.
    """

    instrument: str
    horizon: str
    eligibility_pool: str
    provenance_grade: str

    @property
    def sort_key(self) -> tuple[str, str, str, str]:
        return (self.instrument, self.horizon, self.eligibility_pool,
                self.provenance_grade)

    def as_record(self) -> dict[str, Any]:
        return {
            "instrument": self.instrument,
            "horizon": self.horizon,
            "eligibility_pool": self.eligibility_pool,
            "provenance_grade": self.provenance_grade,
        }


@dataclass(frozen=True)
class Stratum:
    """Counts and the three ratios for one un-mergeable slice of evidence."""

    key: StratumKey
    #: A LABEL carried from the Stage C module registry, never a grouping
    #: key. Pooling the eight currencies that share a dollar leg is exactly
    #: the cross-instrument dependence problem, so asset class never merges
    #: instruments here.
    asset_class: str

    deduplicated_n: int
    finalized_n: int

    disjoint_episode_n: int
    disjoint_episode_verdict_n: int
    group_disjoint_episode_n: int

    by_readiness_tier: Mapping[str, int]
    provisional_not_matured_n: int
    provisional_matured_partial_n: int

    by_direction_outcome: Mapping[str, int]
    by_data_resolution: Mapping[str, int]
    by_maturity_state: Mapping[str, int]
    by_claim_direction: Mapping[str, int]
    by_exclusion_reason: Mapping[str, int]

    bar_conflict_n: int
    malformed_row_total: int | None
    bar_duplicates_collapsed_total: int
    excursion_is_lower_bound_n: int

    market_symbols: tuple[str, ...]
    bound_symbols: tuple[str, ...]
    cross_source_n: int
    cross_granularity_n: int
    provenance_exact_binding_n: int
    provenance_outage_selected: bool

    verdict_confirmation_rate: Ratio
    neutral_rate: Ratio
    resolution_rate: Ratio

    def as_record(self) -> dict[str, Any]:
        return {
            "key": self.key.as_record(),
            "asset_class": self.asset_class,
            "deduplicated_n": self.deduplicated_n,
            "finalized_n": self.finalized_n,
            "disjoint_episode_n": self.disjoint_episode_n,
            "disjoint_episode_verdict_n": self.disjoint_episode_verdict_n,
            "group_disjoint_episode_n": self.group_disjoint_episode_n,
            "by_readiness_tier": dict(self.by_readiness_tier),
            "provisional_not_matured_n": self.provisional_not_matured_n,
            "provisional_matured_partial_n": self.provisional_matured_partial_n,
            "by_direction_outcome": dict(self.by_direction_outcome),
            "by_data_resolution": dict(self.by_data_resolution),
            "by_maturity_state": dict(self.by_maturity_state),
            "by_claim_direction": dict(self.by_claim_direction),
            "by_exclusion_reason": dict(self.by_exclusion_reason),
            "bar_conflict_n": self.bar_conflict_n,
            "malformed_row_total": self.malformed_row_total,
            "bar_duplicates_collapsed_total": self.bar_duplicates_collapsed_total,
            "excursion_is_lower_bound_n": self.excursion_is_lower_bound_n,
            "market_symbols": list(self.market_symbols),
            "bound_symbols": list(self.bound_symbols),
            "cross_source_n": self.cross_source_n,
            "cross_granularity_n": self.cross_granularity_n,
            "provenance_exact_binding_n": self.provenance_exact_binding_n,
            "provenance_outage_selected": self.provenance_outage_selected,
            "verdict_confirmation_rate": self.verdict_confirmation_rate.as_record(),
            "neutral_rate": self.neutral_rate.as_record(),
            "resolution_rate": self.resolution_rate.as_record(),
        }


# ===========================================================================
# E. THE COHORT
# ===========================================================================

class CohortState(Enum):
    """Whether every admitted member's forward window has actually closed."""

    #: Every admitted, deduplicated member is final.
    FINALIZED = "finalized"
    #: At least one admitted member is still maturing, so these numbers will
    #: change. Stated as a field rather than left for a reader to infer.
    PROVISIONAL = "provisional"
    #: Nothing was admitted. Distinct from FINALIZED: "all members are final"
    #: and "there are no members" are different claims.
    EMPTY = "empty"


@dataclass(frozen=True)
class Cohort:
    """One immutable, deterministic research cohort. Never persisted here."""

    cohort_id: str
    membership_hash: str
    cohort_state: CohortState
    as_of: str

    cohort_schema_version: str
    evaluation_schema_version: str
    validation_schema_version: str
    dedup_policy_version: str
    episode_policy_version: str
    validation_config_version: str
    validation_config_hash: str
    cohort_config: Mapping[str, Any]
    stratify_by: tuple[str, ...]

    input_n: int
    evaluated_n: int
    admitted_n: int
    deduplicated_n: int

    lineage_defect_n: int
    lineage_defect_by_reason: Mapping[str, int]
    lineage_defects: tuple[LineageDefect, ...]

    admission_failure_n: int
    admission_failure_by_reason: Mapping[str, int]
    admission_failures: tuple[AdmissionFailure, ...]

    duplicate_group_n: int
    duplicates_collapsed_n: int
    duplicate_max_group_size: int
    duplicate_outcome_disagreement_n: int

    evaluated_at_min: str | None
    evaluated_at_max: str | None

    strata: tuple[Stratum, ...]

    def as_record(self) -> dict[str, Any]:
        return {
            "stage": "d2d1",
            "cohort_id": self.cohort_id,
            "membership_hash": self.membership_hash,
            "cohort_state": self.cohort_state.value,
            "as_of": self.as_of,
            "cohort_schema_version": self.cohort_schema_version,
            "evaluation_schema_version": self.evaluation_schema_version,
            "validation_schema_version": self.validation_schema_version,
            "dedup_policy_version": self.dedup_policy_version,
            "episode_policy_version": self.episode_policy_version,
            "validation_config_version": self.validation_config_version,
            "validation_config_hash": self.validation_config_hash,
            "cohort_config": dict(self.cohort_config),
            "stratify_by": list(self.stratify_by),
            "input_n": self.input_n,
            "evaluated_n": self.evaluated_n,
            "admitted_n": self.admitted_n,
            "deduplicated_n": self.deduplicated_n,
            "lineage_defect_n": self.lineage_defect_n,
            "lineage_defect_by_reason": dict(self.lineage_defect_by_reason),
            "lineage_defects": [d.as_record() for d in self.lineage_defects],
            "admission_failure_n": self.admission_failure_n,
            "admission_failure_by_reason": dict(self.admission_failure_by_reason),
            "admission_failures": [f.as_record() for f in self.admission_failures],
            "duplicate_group_n": self.duplicate_group_n,
            "duplicates_collapsed_n": self.duplicates_collapsed_n,
            "duplicate_max_group_size": self.duplicate_max_group_size,
            "duplicate_outcome_disagreement_n": self.duplicate_outcome_disagreement_n,
            "evaluated_at_min": self.evaluated_at_min,
            "evaluated_at_max": self.evaluated_at_max,
            "strata": [s.as_record() for s in self.strata],
            # Restated on the artifact so no reader has to consult this
            # module to know what the episode count is and is not.
            "disjoint_episode_note": (
                "disjoint_episode_n is a deterministic count of non-overlapping "
                "forward windows; NOT a statistical effective sample size."
            ),
            "status": "SHADOW / NON-PRODUCTION / UNCALIBRATED",
        }


# ===========================================================================
# F. PIPELINE STEPS
# ===========================================================================

def _admit(
    observation: EvaluatedObservation,
    *,
    as_of: datetime,
    validation_config: ValidationConfig,
) -> tuple[_Member | None, AdmissionFailure | None]:
    """Decide whether one evaluated observation may enter this cohort.

    Checks run in dependency order -- config lineage, then timestamp, then
    window, then the as_of implication -- so a member never fails on a check
    that a prior failure already made meaningless. Exactly one failure reason
    is reported: the first one that applies.

    Nothing here reads a direction, a readiness tier or a provenance grade.
    Admission is decided from configuration lineage and point-in-time facts
    alone, because a cohort whose membership depended on results would be
    answering a question it had already peeked at.
    """
    context = observation.envelope.context

    def _failure(reason: AdmissionFailureReason, detail: str) -> AdmissionFailure:
        return AdmissionFailure(
            shadow_storage_id=context.shadow_storage_id,
            shadow_record_id=context.shadow_record_id,
            instrument=context.instrument,
            horizon=context.horizon,
            evaluated_at=context.evaluated_at,
            reason=reason,
            detail=detail,
        )

    if context.validation_config_hash != validation_config.config_hash:
        return None, _failure(
            AdmissionFailureReason.CONFIG_HASH_MISMATCH,
            f"member validation_config_hash={context.validation_config_hash!r} "
            f"but the supplied config hashes to {validation_config.config_hash!r}; "
            "a cohort mixing configurations would compare incomparable windows "
            "and bands.",
        )

    evaluated_at = _parse_iso(context.evaluated_at)
    if evaluated_at is None:
        return None, _failure(
            AdmissionFailureReason.UNPARSEABLE_EVALUATED_AT,
            f"context.evaluated_at={context.evaluated_at!r} is not an ISO "
            "instant, so this member can take no place in a canonical ordering.",
        )

    window = validation_config.window_for(context.horizon)
    if window is None:
        return None, _failure(
            AdmissionFailureReason.UNKNOWN_HORIZON_WINDOW,
            f"the supplied configuration declares no forward window for horizon "
            f"{context.horizon!r}; a window is never guessed.",
        )

    # The as_of implication. assess_maturity returns NOT_MATURED exactly when
    # the reference instant precedes the window end, so this is an equivalence
    # rather than a heuristic, and it is the only check available that can
    # detect a cohort assembled at a different instant -- maturity.now is
    # deliberately absent from the envelope and from outcome_hash.
    window_end = evaluated_at + window
    not_matured = context.maturity_state == MaturityState.NOT_MATURED.value
    if not_matured and not (as_of < window_end):
        return None, _failure(
            AdmissionFailureReason.AS_OF_INCONSISTENT,
            f"member is NOT_MATURED but as_of={_canonical_iso(as_of)} is not "
            f"before window_end={_canonical_iso(window_end)}.",
        )
    if not not_matured and as_of < window_end:
        return None, _failure(
            AdmissionFailureReason.AS_OF_INCONSISTENT,
            f"member maturity is {context.maturity_state!r} but "
            f"as_of={_canonical_iso(as_of)} precedes "
            f"window_end={_canonical_iso(window_end)}.",
        )

    return _Member(observation=observation, evaluated_at=evaluated_at,
                   window=window), None


def _collapse_physical(
    members: Sequence[_Member],
) -> tuple[list[_Member], list[AdmissionFailure]]:
    """Reduce members sharing ONE physical identity, or refuse them.

    ``shadow_storage_id`` is the physical point-in-time identity, so two
    members carrying it are two claims about the same immutable observation.
    Three situations, kept distinct exactly as ``canonicalize_bars`` keeps
    them distinct for market bars:

    *   **one member** -- the ordinary case, passed through.
    *   **identical repeats** -- the same artifact supplied more than once
        (an idempotent caller, a concatenated batch). Same result identity,
        so collapsing to one loses nothing and is not a choice.
    *   **conflicting claims** -- same physical identity, DIFFERENT validation
        results. There is no honest way to choose: every remaining
        discriminator is derived from the outcome. Both are refused and the
        conflict is reported.

    Running before any sort is what keeps ``_Member.order_key`` a total order
    without a result-derived tiebreak.
    """
    groups: dict[str, list[_Member]] = {}
    for member in members:
        groups.setdefault(member.storage_id, []).append(member)

    kept: list[_Member] = []
    failures: list[AdmissionFailure] = []
    for storage_id in sorted(groups):
        group = groups[storage_id]
        identities = {member.result_identity for member in group}
        if len(identities) == 1:
            kept.append(group[0])
            continue
        context = group[0].context
        failures.append(AdmissionFailure(
            shadow_storage_id=storage_id,
            shadow_record_id=context.shadow_record_id,
            instrument=context.instrument,
            horizon=context.horizon,
            evaluated_at=context.evaluated_at,
            reason=AdmissionFailureReason.PHYSICAL_IDENTITY_CONFLICT,
            detail=(
                f"{len(group)} artifacts share shadow_storage_id={storage_id!r} "
                f"and carry {len(identities)} different validation results; "
                "contradictory claims about one immutable observation are "
                "reported, never arbitrated."
            ),
        ))
    return kept, failures


def _representative(group: Sequence[_Member]) -> _Member:
    """The canonical member of one logical duplicate group.

    **Point-in-time facts only, and this is the whole point of the function.**

    1. earliest parsed ``evaluated_at`` -- reconstructs the cadence the live
       hook intended, whose durable gate deliberately fails OPEN and so lets
       duplicates reach storage in the first place;
    2. lexicographically smallest ``shadow_storage_id`` on an exact tie --
       immutable, deterministic and total.

    It must never consult readiness, direction, provenance grade, eligibility
    pool or any later data-quality observation. The rejected alternative --
    "keep the highest-readiness duplicate" -- was shown to prefer members that
    escaped the neutral band, and because that band scales with the anchor's
    ATR, to select on low recorded volatility. That is outcome-derived
    selection through a volatility channel, not a tie-break.

    A group whose members DISAGREE about the outcome is not excluded either.
    Excluding on disagreement would itself be outcome-conditioned exclusion,
    and it would remove precisely the ambiguous cases -- strictly worse than a
    deterministic, outcome-blind choice. The disagreement is counted as a
    diagnostic instead.
    """
    return min(group, key=lambda member: member.order_key)


def _deduplicate(
    members: Sequence[_Member],
) -> tuple[tuple[_Member, ...], dict[str, int]]:
    """Collapse each logical forecast to one deterministic representative."""
    groups: dict[tuple[str, str, str], list[_Member]] = {}
    for member in members:
        groups.setdefault(member.logical_key, []).append(member)

    kept: list[_Member] = []
    group_n = collapsed = max_size = disagreements = 0
    for key in sorted(groups):
        group = groups[key]
        kept.append(_representative(group))
        if len(group) > 1:
            group_n += 1
            collapsed += len(group) - 1
            max_size = max(max_size, len(group))
            if len({member.direction for member in group}) > 1:
                disagreements += 1

    kept.sort(key=lambda member: member.order_key)
    return tuple(kept), {
        "duplicate_group_n": group_n,
        "duplicates_collapsed_n": collapsed,
        "duplicate_max_group_size": max_size,
        "duplicate_outcome_disagreement_n": disagreements,
    }


def _partition_episodes(members: Sequence[_Member]) -> set[str]:
    """Storage ids of the disjoint-episode representatives, per instrument+horizon.

    Greedy forward scan over the canonical ordering: accept the first member,
    then accept the next only once its ``evaluated_at`` has reached the
    previous acceptance plus that horizon's own forward window. Exact boundary
    equality is accepted -- two windows meeting at a single instant share a
    measure-zero overlap, which against daily bars is not a shared observation.

    **Filter-blind by construction.** Every admitted, deduplicated member takes
    part regardless of readiness, direction or provenance, and no accepted
    representative is ever replaced by a later one. Both properties matter: if
    filtering ran first, dropping a member would promote its successor to
    anchor, and since readiness depends on the outcome the partition would
    become a function of the results. Replacement would require reading what
    came afterwards, which is the same defect wearing a different hat.

    The window is taken per member from the injected configuration, so the
    three horizons partition on their own scales and never on a shared
    constant.
    """
    groups: dict[tuple[str, str], list[_Member]] = {}
    for member in members:
        groups.setdefault(member.group_key, []).append(member)

    accepted: set[str] = set()
    for key in sorted(groups):
        ordered = sorted(groups[key], key=lambda member: member.order_key)
        last_accepted: datetime | None = None
        for member in ordered:
            if last_accepted is None or member.evaluated_at >= last_accepted + member.window:
                accepted.add(member.storage_id)
                last_accepted = member.evaluated_at
    return accepted


# ===========================================================================
# G. THE PUBLIC ENTRY POINT
# ===========================================================================

def build_cohort(
    *,
    observations: Sequence[EvaluatedObservation | LineageDefect],
    as_of: datetime,
    validation_config: ValidationConfig,
    cohort_config: CohortConfig = DEFAULT_COHORT_CONFIG,
) -> Cohort:
    """Assemble one deterministic research cohort from evaluated observations.

    Every input is supplied. Nothing is fetched, queried, persisted or read
    from a clock: ``as_of`` is the single reference instant, required because
    maturity -- and therefore ``outcome_hash`` and readiness -- move with it.

    ``validation_config`` is injected and verified: every member must carry the
    same ``validation_config_hash``, and its ``window_for`` is the only source
    of a forward window. A horizon window is never assumed from a constant.

    **Never raises for anything about the evidence.** A lineage defect, an
    inadmissible member or an empty input all produce a fully formed cohort
    that says so. One bad member never costs the batch the rest of its
    records.

    The result is a pure value. It is not written anywhere, and it informs no
    production decision.
    """
    inputs = tuple(observations)

    # -- 1. lineage defects: counted, never members, never denominators. ----
    defects = tuple(item for item in inputs if isinstance(item, LineageDefect))
    candidates = tuple(item for item in inputs if isinstance(item, EvaluatedObservation))

    ordered_defects = tuple(
        sorted(defects, key=lambda d: (d.shadow_storage_id, d.reason.value))
    )

    # -- 2. admission ------------------------------------------------------
    admitted: list[_Member] = []
    failures: list[AdmissionFailure] = []
    for candidate in candidates:
        member, failure = _admit(
            candidate, as_of=_utc(as_of), validation_config=validation_config
        )
        if member is not None:
            admitted.append(member)
        elif failure is not None:
            failures.append(failure)

    # -- 3. physical identity: collapse exact repeats, refuse contradictions.
    # Runs before any sort, so the canonical ordering below is total without
    # needing a result-derived tiebreak.
    admitted, conflicts = _collapse_physical(admitted)
    failures.extend(conflicts)

    ordered_failures = tuple(
        sorted(failures, key=lambda f: (f.shadow_storage_id, f.reason.value))
    )

    # -- 4. canonical order, then logical deduplication. -------------------
    admitted.sort(key=lambda member: member.order_key)
    members, duplicate_counts = _deduplicate(admitted)

    # -- 5. disjoint episodes, BEFORE any readiness/eligibility filtering. --
    episode_ids = _partition_episodes(members)

    # -- 6. stratify and count. --------------------------------------------
    strata = _build_strata(
        members, episode_ids=episode_ids, cohort_config=cohort_config
    )

    # -- 7./8. state and identities. ---------------------------------------
    if not members:
        state = CohortState.EMPTY
    elif all(member.is_final for member in members):
        state = CohortState.FINALIZED
    else:
        state = CohortState.PROVISIONAL

    as_of_iso = _canonical_iso(as_of)
    cohort_id = sha256_hex(
        canonical_json({
            "cohort_schema_version": COHORT_SCHEMA_VERSION,
            "evaluation_schema_version": EVALUATION_SCHEMA_VERSION,
            "validation_schema_version": VALIDATION_SCHEMA_VERSION,
            "dedup_policy_version": DEDUP_POLICY_VERSION,
            "episode_policy_version": EPISODE_POLICY_VERSION,
            "validation_config_version": validation_config.version,
            "validation_config_hash": validation_config.config_hash,
            "cohort_config_version": cohort_config.version,
            "cohort_config_hash": cohort_config.config_hash,
            "cohort_config_chosen": cohort_config.chosen,
            "as_of": as_of_iso,
            "stratify_by": list(STRATIFY_BY),
        }),
        _HASH_LENGTH,
    )

    # NOT sorted independently. This commits to the canonical member ORDER
    # already established above, exactly as D-2C4 commits to its canonical bar
    # path rather than to a sorted bag of content hashes: a sorted bag is
    # blind to which observation held which result, so two cohorts that swap
    # who confirmed and who failed would hash identically.
    membership_hash = sha256_hex(
        canonical_json([
            {
                "validation_id": member.observation.envelope.validation_id,
                "input_hash": member.observation.envelope.input_hash,
                "outcome_hash": member.observation.envelope.outcome_hash,
                "shadow_storage_id": member.storage_id,
            }
            for member in members
        ]),
        _HASH_LENGTH,
    )

    return Cohort(
        cohort_id=cohort_id,
        membership_hash=membership_hash,
        cohort_state=state,
        as_of=as_of_iso,
        cohort_schema_version=COHORT_SCHEMA_VERSION,
        evaluation_schema_version=EVALUATION_SCHEMA_VERSION,
        validation_schema_version=VALIDATION_SCHEMA_VERSION,
        dedup_policy_version=DEDUP_POLICY_VERSION,
        episode_policy_version=EPISODE_POLICY_VERSION,
        validation_config_version=validation_config.version,
        validation_config_hash=validation_config.config_hash,
        cohort_config=cohort_config.as_provenance(),
        stratify_by=STRATIFY_BY,
        input_n=len(inputs),
        evaluated_n=len(candidates),
        admitted_n=len(admitted),
        deduplicated_n=len(members),
        lineage_defect_n=len(ordered_defects),
        lineage_defect_by_reason=_counts(
            (d.reason for d in ordered_defects), DefectReason
        ),
        lineage_defects=ordered_defects,
        admission_failure_n=len(ordered_failures),
        admission_failure_by_reason=_counts(
            (f.reason for f in ordered_failures), AdmissionFailureReason
        ),
        admission_failures=ordered_failures,
        duplicate_group_n=duplicate_counts["duplicate_group_n"],
        duplicates_collapsed_n=duplicate_counts["duplicates_collapsed_n"],
        duplicate_max_group_size=duplicate_counts["duplicate_max_group_size"],
        duplicate_outcome_disagreement_n=duplicate_counts[
            "duplicate_outcome_disagreement_n"
        ],
        evaluated_at_min=(
            _canonical_iso(members[0].evaluated_at) if members else None
        ),
        evaluated_at_max=(
            _canonical_iso(members[-1].evaluated_at) if members else None
        ),
        strata=strata,
    )


# ===========================================================================
# H. STRATIFICATION AND METRICS
# ===========================================================================

_RESOLVED_DIRECTIONS = (
    DirectionOutcome.CONFIRMED,
    DirectionOutcome.FAILED,
    DirectionOutcome.NEUTRAL_WITHIN_BAND,
)


def _build_strata(
    members: Sequence[_Member],
    *,
    episode_ids: set[str],
    cohort_config: CohortConfig,
) -> tuple[Stratum, ...]:
    """Count and rate each un-mergeable slice. Runs AFTER dedup and spacing."""
    # Group-level binding instability, computed over the whole instrument +
    # horizon group BEFORE slicing into strata. This is what makes the
    # outage gate generic: an instrument whose binding is stable produces no
    # substituted members and never trips it, while one that sometimes
    # substitutes reveals that its exact-binding subset is a minority
    # selected by the absence of substitution. No instrument is named.
    group_substituted: dict[tuple[str, str], int] = {}
    group_episodes: dict[tuple[str, str], int] = {}
    for member in members:
        key = member.group_key
        group_substituted.setdefault(key, 0)
        group_episodes.setdefault(key, 0)
        if bool(member.context.cross_source):
            group_substituted[key] += 1
        if member.storage_id in episode_ids:
            group_episodes[key] += 1

    buckets: dict[StratumKey, list[_Member]] = {}
    for member in members:
        key = StratumKey(
            instrument=member.context.instrument,
            horizon=member.context.horizon,
            eligibility_pool=member.context.eligibility_pool,
            provenance_grade=member.observation.provenance_grade.value,
        )
        buckets.setdefault(key, []).append(member)

    strata: list[Stratum] = []
    for key in sorted(buckets, key=lambda k: k.sort_key):
        strata.append(
            _build_stratum(
                key,
                buckets[key],
                episode_ids=episode_ids,
                group_substituted_n=group_substituted[(key.instrument, key.horizon)],
                group_disjoint_episode_n=group_episodes[(key.instrument, key.horizon)],
                cohort_config=cohort_config,
            )
        )
    return tuple(strata)


def _build_stratum(
    key: StratumKey,
    members: Sequence[_Member],
    *,
    episode_ids: set[str],
    group_substituted_n: int,
    group_disjoint_episode_n: int,
    cohort_config: CohortConfig,
) -> Stratum:
    """One stratum's counts and its three gated ratios."""
    ordered = sorted(members, key=lambda member: member.order_key)

    # -- The metric BASE. Built from maturity and eligibility pool only --
    # a time/coverage fact and a capture-provenance fact. ReadinessTier is
    # deliberately NOT used: it already folds direction.is_verdict into
    # itself, so filtering by it would put the answer inside the
    # denominator, and its EXCLUDED tier holds final NEUTRAL observations
    # the neutral diagnostic needs to count.
    finalized = tuple(member for member in ordered if member.is_final)
    directions = [member.direction for member in finalized]

    confirmed_n = directions.count(DirectionOutcome.CONFIRMED)
    failed_n = directions.count(DirectionOutcome.FAILED)
    neutral_n = directions.count(DirectionOutcome.NEUTRAL_WITHIN_BAND)
    verdict_n = confirmed_n + failed_n
    resolved_n = verdict_n + neutral_n
    directional_claim_n = sum(
        1 for member in finalized
        if member.observation.claim_direction.is_directional
    )

    # Episodes are counted, never chosen: the partition was fixed above from
    # timestamps alone, and these are classifications of representatives that
    # already landed here. A representative that is not a verdict is never
    # swapped for a later one that is, so these counts are conservative by
    # construction.
    episode_members = [m for m in ordered if m.storage_id in episode_ids]
    episode_final = [m for m in episode_members if m.is_final]
    episode_verdict_n = sum(1 for m in episode_final if m.direction.is_verdict)
    episode_resolved_n = sum(1 for m in episode_final
                             if m.direction in _RESOLVED_DIRECTIONS)
    episode_directional_n = sum(
        1 for m in episode_final if m.observation.claim_direction.is_directional
    )

    # -- The generic outage gate. -----------------------------------------
    outage_selected = (
        key.eligibility_pool == EligibilityPool.CAPTURED.value
        and key.provenance_grade == ProvenanceGrade.IDEAL.value
        and group_substituted_n > 0
    )

    def _ratio(name, numerator, denominator, denominator_name,
               episode_denominator, episode_denominator_name) -> Ratio:
        return _build_ratio(
            name=name,
            numerator=numerator,
            denominator=denominator,
            denominator_name=denominator_name,
            episode_denominator=episode_denominator,
            episode_denominator_name=episode_denominator_name,
            disjoint_episode_n=len(episode_members),
            disjoint_episode_verdict_n=episode_verdict_n,
            cohort_config=cohort_config,
            withheld=outage_selected,
        )

    readiness_counts = _counts(
        (member.observation.readiness for member in ordered), ReadinessTier
    )
    provisional = [
        member for member in ordered
        if member.observation.readiness is ReadinessTier.PROVISIONAL
    ]

    malformed = [
        member.context.malformed_row_count for member in ordered
        if member.context.malformed_row_count is not None
    ]

    return Stratum(
        key=key,
        asset_class=ordered[0].observation.asset_class,
        deduplicated_n=len(ordered),
        finalized_n=len(finalized),
        disjoint_episode_n=len(episode_members),
        disjoint_episode_verdict_n=episode_verdict_n,
        group_disjoint_episode_n=group_disjoint_episode_n,
        by_readiness_tier=readiness_counts,
        # PROVISIONAL conflates "the window has not closed" with "it closed
        # on a coverage gap". Those are different states with different
        # futures, so they are reported apart.
        provisional_not_matured_n=sum(
            1 for member in provisional
            if member.context.maturity_state == MaturityState.NOT_MATURED.value
        ),
        provisional_matured_partial_n=sum(
            1 for member in provisional
            if member.context.maturity_state == MaturityState.MATURED_PARTIAL.value
        ),
        by_direction_outcome=_counts(
            (member.direction for member in ordered), DirectionOutcome
        ),
        by_data_resolution=_counts(
            (member.observation.envelope.outcome_axes.data_resolution
             for member in ordered), DataResolution
        ),
        by_maturity_state=_counts(
            (member.context.maturity_state for member in ordered), MaturityState
        ),
        by_claim_direction=_counts(
            (member.observation.claim_direction for member in ordered), Direction
        ),
        by_exclusion_reason=_counts(
            (member.observation.envelope.outcome_axes.exclusion_reason
             for member in ordered
             if member.observation.envelope.outcome_axes.exclusion_reason is not None),
            ExclusionReason,
        ),
        bar_conflict_n=sum(
            1 for member in ordered if member.context.conflict_ids
        ),
        # None, not 0: "no rows were malformed" and "nobody counted" are
        # different claims, and only a caller that read the rows knows which.
        malformed_row_total=(sum(malformed) if malformed else None),
        bar_duplicates_collapsed_total=sum(
            int(member.context.duplicates_collapsed) for member in ordered
        ),
        excursion_is_lower_bound_n=sum(
            1 for member in ordered
            if member.observation.envelope.outcome_hash_basis.get(
                "excursion_is_lower_bound")
        ),
        market_symbols=tuple(sorted(
            {str(member.context.market_symbol) for member in ordered
             if member.context.market_symbol}
        )),
        bound_symbols=tuple(sorted(
            {str(member.context.bound_symbol) for member in ordered
             if member.context.bound_symbol}
        )),
        cross_source_n=sum(1 for member in ordered if member.context.cross_source),
        # Recorded, never disqualifying: the anchor is a 5-minute close and
        # the bars are daily, so this is one instrument at two sampling
        # rates rather than two instruments.
        cross_granularity_n=sum(
            1 for member in ordered if member.context.cross_granularity
        ),
        provenance_exact_binding_n=sum(
            1 for member in ordered if not member.context.cross_source
        ),
        provenance_outage_selected=outage_selected,
        verdict_confirmation_rate=_ratio(
            "verdict_confirmation_rate", confirmed_n, verdict_n,
            "directional_verdicts", episode_verdict_n,
            "disjoint_episode_verdict_n",
        ),
        neutral_rate=_ratio(
            "neutral_rate", neutral_n, resolved_n,
            "resolved_directional_claims", episode_resolved_n,
            "disjoint_episode_resolved_n",
        ),
        resolution_rate=_ratio(
            "resolution_rate", resolved_n, directional_claim_n,
            "finalized_directional_claims", episode_directional_n,
            "disjoint_episode_directional_n",
        ),
    )


__all__ = [
    "COHORT_CONFIG_VERSION",
    "COHORT_SCHEMA_VERSION",
    "DEDUP_POLICY_VERSION",
    "DEFAULT_COHORT_CONFIG",
    "EPISODE_POLICY_VERSION",
    "STRATIFY_BY",
    "AdmissionFailure",
    "AdmissionFailureReason",
    "Cohort",
    "CohortConfig",
    "CohortState",
    "Ratio",
    "RatioNote",
    "RatioState",
    "Stratum",
    "StratumKey",
    "build_cohort",
]
