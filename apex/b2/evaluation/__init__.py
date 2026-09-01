"""Architecture B2 -- Stage D-2D evaluation layer.

Where ``apex.b2.validation`` answers "what happened to ONE observation",
this package answers "what may be said about a SET of them". Stage D-2D0 --
everything currently in here -- is only the first half of that: the
per-observation ORCHESTRATOR that composes the four frozen validation
stages into one result. Aggregation, cohorts, deduplication, overlap and
metrics are D-2D1 and are deliberately absent rather than stubbed.

The gap D-2D0 closes is narrow and was found by direct inspection: D-2C2
(``resolve_direction_and_path``), D-2C3 (``resolve_setup_and_execution``),
D-2C5 (``verify_lineage``/``classify_readiness``) and D-2C4
(``build_validation_envelope``) are each implemented and tested, and NOTHING
outside the test suite composes them. There is, today, no producer of a
``ValidationEnvelope`` at all. This package is that producer, and nothing
more: it reimplements no band arithmetic, no excursion measurement, no
invalidation scan, no readiness rule and no hash.

Two guarantees hold structurally rather than by convention:

*   **Pure.** Nothing here imports ``apex.production_core``, ``b2_bridge``,
    ``b2_validation_bridge``, Streamlit, ``requests`` or ``threading``, and
    nothing reads a clock. Every moment comes from the injected ``as_of``
    and every record and bar comes from an argument, so the same inputs
    always produce the same result.
*   **A composition defect is never a market outcome.** A
    ``LineageError`` raised by D-2C5, or a structurally impossible outcome
    combination, comes back as an explicit :class:`LineageDefect` -- never
    as ``FAILED``, never as ``CONFIRMED``, never as ``NEUTRAL_WITHIN_BAND``
    or ``ABSTAINED``, and never as an exception that would abort a future
    batch partway through.

B2 remains SHADOW / NON-PRODUCTION / UNCALIBRATED. Nothing in this package
is wired into production, persists anything, or informs a decision.
"""
from __future__ import annotations

from .observation import (
    DefectReason,
    EvaluatedObservation,
    LineageDefect,
    ProvenanceGrade,
    classify_provenance,
    evaluate_observation,
)

# Stage D-2D1: the cohort layer. Additive -- nothing above changed, and
# ``cohort`` imports ``observation`` and never the reverse, so the one-way
# dependency validation -> observation -> cohort is preserved.
from .cohort import (
    DEFAULT_COHORT_CONFIG,
    AdmissionFailure,
    AdmissionFailureReason,
    Cohort,
    CohortConfig,
    CohortState,
    Ratio,
    RatioNote,
    RatioState,
    Stratum,
    StratumKey,
    build_cohort,
)

__all__ = [
    "DEFAULT_COHORT_CONFIG",
    "AdmissionFailure",
    "AdmissionFailureReason",
    "Cohort",
    "CohortConfig",
    "CohortState",
    "DefectReason",
    "EvaluatedObservation",
    "LineageDefect",
    "ProvenanceGrade",
    "Ratio",
    "RatioNote",
    "RatioState",
    "Stratum",
    "StratumKey",
    "build_cohort",
    "classify_provenance",
    "evaluate_observation",
]
