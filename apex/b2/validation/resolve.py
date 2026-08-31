"""Architecture B2 -- Stage D-2C2: direction and path resolution.

Answers one narrow question about ONE immutable shadow observation:

    Given what B2 claimed, and the immutable bars that fell inside the claimed
    horizon, which way did the market actually go, and what did the path look
    like on the way there?

**Scope is deliberately narrow.** This module resolves the directional claim,
the terminal return, and the excursion measurements. It does NOT resolve setup
invalidation, thesis invalidation or execution quality (D-2C3), and it does not
compute identity hashes, copy context, or emit overlap metadata (D-2C4). Those
axes are absent here rather than stubbed, so nothing can mistake an unwritten
answer for a computed one.

Four rules are structural rather than advisory:

*   **Missing data is never a wrong prediction.** Every path that cannot reach
    a verdict returns ``UNRESOLVED`` with a reason code drawn from the existing
    vocabulary. No branch produces ``FAILED`` from absence.
*   **Immaturity is not evidence.** A window that has not elapsed yields
    ``NOT_MATURED`` / ``UNRESOLVED``. ``now`` is injected and influences
    maturity eligibility only -- never a computed value, so a matured, fully
    covered observation resolves identically at any later moment.
*   **Everything is in the analysis (strength) convention.** A positive return
    means "stronger" for every instrument, including the inverted USD-quoted
    pairs, because ``MarketBar`` exposes ``analysis_high``/``analysis_low``
    through production's own parity-tested transformation.
*   **Contradictory evidence resolves to nothing.** A bar-content conflict
    withholds the verdict entirely rather than picking a version.

Pure: stdlib plus ``apex.b2.validation`` submodules only. No I/O, no clock, no
randomness, no network, no storage, no production import. Nothing here is read
by any production path, and B2 remains SHADOW / NON-PRODUCTION / UNCALIBRATED.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

from ..enums import Direction
from .anchor import AnchorResolution, AnchorStatus, SymbolConvention, classify_anchor
from .bars import (
    GRANULARITY_SECONDS,
    BarCanonicalization,
    MarketBar,
    canonicalize_bars,
    coverage,
    terminal_bar,
)
from .config import (
    DEFAULT_VALIDATION_CONFIG,
    BandMode,
    NeutralBand,
    ValidationConfig,
    neutral_band,
)
from .maturity import COVERAGE_RESOLVABLE, MaturityAssessment, MaturityState, assess_maturity
from .outcome import (
    DataResolution,
    DirectionOutcome,
    EligibilityPool,
    ExclusionReason,
    ExcursionMeasures,
)
from .series import InversionAgreement, SeriesBinding, SeriesBindingQuality, bind_series, bound_bars


def _utc(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _payload(record: Mapping[str, Any]) -> Mapping[str, Any]:
    """The shadow record payload, whether wrapped in a storage row or not."""
    inner = record.get("record") if isinstance(record, Mapping) else None
    return inner if isinstance(inner, Mapping) else record


def claim_direction(record: Mapping[str, Any]) -> Direction:
    """The PRIMARY registered directional prediction.

    ``claim.direction`` and nothing else. It is the only directional field
    carrying its own ``evaluate_at`` -- the deadline was stamped at evaluation
    time, so it is the claim that was actually registered.
    ``decision.direction``, ``macro_direction`` and ``technical_direction`` are
    secondary claims with different semantics and are deliberately NOT resolved
    here; substituting one would score a prediction B2 never registered.

    A record with no claim reads UNAVAILABLE: no claim is not a flat claim.
    """
    claim = _payload(record).get("claim")
    if not isinstance(claim, Mapping):
        return Direction.UNAVAILABLE
    try:
        return Direction(str(claim.get("direction")))
    except ValueError:
        return Direction.UNAVAILABLE


@dataclass(frozen=True)
class DirectionPathResolution:
    """What D-2C2 could establish about one observation. Nothing more.

    Deliberately not the final persisted outcome shape: identity hashes,
    context copy and overlap metadata belong to D-2C4, and the invalidation and
    execution axes to D-2C3. Their absence here is the point.
    """

    # -- claim -------------------------------------------------------------
    claim_direction: Direction
    direction: DirectionOutcome
    data_resolution: DataResolution
    reasons: tuple[ExclusionReason, ...]

    # -- measurement -------------------------------------------------------
    excursion: ExcursionMeasures
    band: NeutralBand
    terminal_bar_time: str | None
    path_complete: bool
    #: True when the excursion was measured over an incomplete path, so MFE and
    #: MAE are LOWER BOUNDS: a missing bar can hide an excursion but never
    #: invent one.
    excursion_is_lower_bound: bool

    # -- provenance --------------------------------------------------------
    anchor: AnchorResolution
    binding: SeriesBinding
    maturity: MaturityAssessment
    canonicalization: BarCanonicalization
    eligibility_pool: EligibilityPool
    coverage_status: str | None
    anchor_price: float | None

    @property
    def is_verdict(self) -> bool:
        return self.direction.is_verdict

    def as_record(self) -> dict[str, Any]:
        return {
            "stage": "d2c2",
            "claim_direction": self.claim_direction.value,
            "direction_outcome": self.direction.value,
            "data_resolution": self.data_resolution.value,
            "reasons": [r.value for r in self.reasons],
            "terminal_bar_time": self.terminal_bar_time,
            "path_complete": self.path_complete,
            "excursion_is_lower_bound": self.excursion_is_lower_bound,
            "anchor_price": self.anchor_price,
            "coverage_status": self.coverage_status,
            "eligibility_pool": self.eligibility_pool.value,
            "anchor_status": self.anchor.status.value,
            "point_in_time": self.anchor.status.is_point_in_time,
            "binding_quality": self.binding.quality.value,
            "inversion_agreement": self.binding.inversion.value,
            "bound_symbol": self.binding.bound_symbol,
            "maturity_state": self.maturity.state.value,
            "duplicates_collapsed": self.canonicalization.duplicates_collapsed,
            "bar_conflicts": len(self.canonicalization.conflicts),
            **self.excursion.as_record(),
            **self.band.as_record(),
            # D-2C2 resolves the directional and path axes only. The remaining
            # axes are NOT computed here and must not be inferred from absence.
            "not_resolved_in_this_stage": [
                "setup_invalidation",
                "thesis_invalidation",
                "execution_outcome",
                "validation_id",
                "input_hash",
                "outcome_hash",
                "context",
                "overlap_metadata",
            ],
        }


_EMPTY_CANONICALIZATION = BarCanonicalization(bars=(), duplicates_collapsed=0, conflicts=())


def _pool_for(
    anchor: AnchorResolution, binding: SeriesBinding, excluded: bool
) -> EligibilityPool:
    """Which evidence pool this observation may ever belong to.

    The captured pool is conjunctive and strict: a point-in-time anchor AND the
    very series that anchor names AND an agreed inversion convention. Anything
    that resolved by a weaker route is research evidence, stamped rather than
    filtered out -- silent inclusion and silent exclusion are both dishonest.
    """
    if excluded:
        return EligibilityPool.EXCLUDED
    if (
        anchor.status is AnchorStatus.CAPTURED
        and binding.quality is SeriesBindingQuality.SERIES_EXACT
        and binding.inversion is InversionAgreement.AGREED
        and not binding.cross_source
    ):
        return EligibilityPool.CAPTURED
    return EligibilityPool.RECONSTRUCTED_RESEARCH


def _unresolved(
    *,
    claim: Direction,
    data_resolution: DataResolution,
    reasons: tuple[ExclusionReason, ...],
    band: NeutralBand,
    anchor: AnchorResolution,
    binding: SeriesBinding,
    maturity: MaturityAssessment,
    canonicalization: BarCanonicalization = _EMPTY_CANONICALIZATION,
    coverage_status: str | None = None,
    anchor_price: float | None = None,
    excluded: bool = True,
) -> DirectionPathResolution:
    """The single shape every non-judgeable path returns.

    One shape rather than each branch inventing its own, so a validation run
    can count its exclusions -- which it can only do if they look alike.
    """
    return DirectionPathResolution(
        claim_direction=claim,
        direction=DirectionOutcome.UNRESOLVED,
        data_resolution=data_resolution,
        reasons=reasons,
        excursion=ExcursionMeasures(),
        band=band,
        terminal_bar_time=None,
        path_complete=False,
        excursion_is_lower_bound=False,
        anchor=anchor,
        binding=binding,
        maturity=maturity,
        canonicalization=canonicalization,
        eligibility_pool=_pool_for(anchor, binding, excluded),
        coverage_status=coverage_status,
        anchor_price=anchor_price,
    )


def _excursions(
    path: Sequence[MarketBar],
    *,
    claim: Direction,
    anchor_price: float,
) -> tuple[float | None, float | None, int | None, int | None]:
    """MFE and MAE as NON-NEGATIVE magnitudes relative to the claim direction.

    Favourable and adverse are defined by the claim, so both series are
    directly comparable across bullish and bearish observations without a sign
    convention a reader has to remember:

        bullish  MFE = max(analysis_high / anchor - 1, 0)
                 MAE = max(1 - analysis_low  / anchor, 0)
        bearish  MFE = max(1 - analysis_low  / anchor, 0)
                 MAE = max(analysis_high / anchor - 1, 0)

    A claim with no direction defines neither, so both are None: fabricating a
    "favourable" side for an abstention would invent a prediction that was
    never made.

    Indices are 0-based into the canonical path and record the FIRST bar
    achieving the extreme, so a plateau reports when it was first reached
    rather than when it happened to end.
    """
    if not claim.is_directional or not path:
        return None, None, None, None

    best = worst = None
    best_index = worst_index = None

    for index, bar in enumerate(path):
        upside = (bar.analysis_high / anchor_price) - 1.0
        downside = 1.0 - (bar.analysis_low / anchor_price)
        favourable, adverse = (
            (upside, downside) if claim is Direction.BULLISH else (downside, upside)
        )
        # Strict > keeps the FIRST occurrence of a repeated extreme.
        if best is None or favourable > best:
            best, best_index = favourable, index
        if worst is None or adverse > worst:
            worst, worst_index = adverse, index

    return max(best or 0.0, 0.0), max(worst or 0.0, 0.0), best_index, worst_index


def _direction_outcome(
    *, claim: Direction, terminal_return: float | None, band: float | None
) -> DirectionOutcome:
    """Map the claim and the realised return onto a verdict.

    Boundary semantics are explicit: the verdicts require ``> +band`` and
    ``< -band``, so a return landing EXACTLY on either edge is
    ``NEUTRAL_WITHIN_BAND``. The band is the width of "no material move", and a
    move exactly that size has not exceeded it.
    """
    if claim is Direction.FLAT:
        # B2 declined to make a directional claim. A feature, never a miss.
        return DirectionOutcome.ABSTAINED
    if claim is not Direction.BULLISH and claim is not Direction.BEARISH:
        return DirectionOutcome.NOT_APPLICABLE
    if terminal_return is None or band is None:
        return DirectionOutcome.UNRESOLVED

    if abs(terminal_return) <= band:
        return DirectionOutcome.NEUTRAL_WITHIN_BAND
    moved_up = terminal_return > band
    if claim is Direction.BULLISH:
        return DirectionOutcome.CONFIRMED if moved_up else DirectionOutcome.FAILED
    return DirectionOutcome.FAILED if moved_up else DirectionOutcome.CONFIRMED


def resolve_direction_and_path(
    *,
    record: Mapping[str, Any],
    bars: Sequence[MarketBar],
    now: datetime,
    convention: SymbolConvention | None = None,
    config: ValidationConfig | None = None,
) -> DirectionPathResolution:
    """Resolve the directional claim and path of ONE shadow observation.

    Every input is supplied. Nothing is fetched, queried, or read from a clock:
    ``now`` is injected precisely so maturity is deterministic under test.

    ``convention`` is the production symbol mapping for this instrument, needed
    to reconstruct a legacy anchor's series and to enumerate fallback symbols.
    Omitting it is legal and simply narrows what can be bound.

    Never raises. An observation that cannot be judged comes back UNRESOLVED
    with reasons attached.
    """
    settings = config or DEFAULT_VALIDATION_CONFIG
    payload = _payload(record)
    claim = claim_direction(record)
    reference = _utc(now)

    horizon = str(payload.get("horizon") or "")
    resolution = classify_anchor(payload, convention)
    anchor = resolution.anchor
    granularity = anchor.granularity if anchor is not None else ""

    window = settings.window_for(horizon)
    band = neutral_band(
        horizon=horizon,
        anchor_granularity=granularity,
        atr=anchor.atr if anchor is not None else None,
        volatility_scale=anchor.volatility_scale if anchor is not None else None,
        analysis_price=anchor.strength_price if anchor is not None else None,
        config=settings,
    )

    empty_binding = SeriesBinding(
        anchor_symbol=resolution.symbol,
        candidates=(),
        bound_symbol=None,
        quality=SeriesBindingQuality.SERIES_UNAVAILABLE,
        inversion=InversionAgreement.UNKNOWN,
        anchor_invert=resolution.invert,
        bound_invert=None,
        granularity=settings.resolution_granularity,
        cross_source=False,
        cross_granularity=False,
        bar_count=0,
    )

    def stub_maturity(state_window: timedelta) -> MaturityAssessment:
        return assess_maturity(
            evaluated_at=reference, window=state_window, now=reference
        )

    fallback_window = window or timedelta(0)

    # -- timestamp ---------------------------------------------------------
    try:
        evaluated_at = _utc(
            datetime.fromisoformat(
                str(payload.get("evaluated_at") or "").replace("Z", "+00:00")
            )
        )
    except ValueError:
        return _unresolved(
            claim=claim,
            data_resolution=DataResolution.UNAVAILABLE,
            reasons=(ExclusionReason.BAD_TIMESTAMP,),
            band=band,
            anchor=resolution,
            binding=empty_binding,
            maturity=stub_maturity(fallback_window),
        )

    # -- horizon -----------------------------------------------------------
    if window is None:
        return _unresolved(
            claim=claim,
            data_resolution=DataResolution.UNAVAILABLE,
            reasons=(ExclusionReason.UNKNOWN_HORIZON,),
            band=band,
            anchor=resolution,
            binding=empty_binding,
            maturity=stub_maturity(fallback_window),
        )

    maturity = assess_maturity(
        evaluated_at=evaluated_at, window=window, now=reference
    )

    # -- clock skew --------------------------------------------------------
    # The window has not started, which is not the same as being over-mature.
    if evaluated_at > reference:
        return _unresolved(
            claim=claim,
            data_resolution=DataResolution.NOT_MATURED,
            reasons=(ExclusionReason.CLOCK_SKEW,),
            band=band,
            anchor=resolution,
            binding=empty_binding,
            maturity=maturity,
            excluded=False,
        )

    # -- anchor ------------------------------------------------------------
    if resolution.status is AnchorStatus.MISSING or anchor is None:
        return _unresolved(
            claim=claim,
            data_resolution=DataResolution.UNAVAILABLE,
            reasons=(ExclusionReason.ANCHOR_MISSING,),
            band=band,
            anchor=resolution,
            binding=empty_binding,
            maturity=maturity,
        )

    anchor_price = anchor.strength_price
    if anchor_price is None or not (anchor_price > 0.0):
        return _unresolved(
            claim=claim,
            data_resolution=DataResolution.UNAVAILABLE,
            reasons=(ExclusionReason.ANCHOR_PRICE_UNUSABLE,),
            band=band,
            anchor=resolution,
            binding=empty_binding,
            maturity=maturity,
        )

    # -- series ------------------------------------------------------------
    binding = bind_series(
        anchor=anchor,
        convention=convention,
        bars=bars,
        granularity=settings.resolution_granularity,
        allow_substitution=settings.allow_series_substitution,
    )

    if binding.inversion is InversionAgreement.MISMATCH:
        # Bars exist but their direction convention cannot be trusted, and
        # there is no honest way to choose which convention was in force.
        return _unresolved(
            claim=claim,
            data_resolution=DataResolution.UNAVAILABLE,
            reasons=(ExclusionReason.INVERSION_MISMATCH,),
            band=band,
            anchor=resolution,
            binding=binding,
            maturity=maturity,
            anchor_price=anchor_price,
        )

    if not binding.is_bound:
        # Distinguish "this instrument has no bars" from "its bars are at a
        # granularity this configuration does not resolve against".
        candidates = set(binding.candidates)
        wrong_granularity = any(
            bar.symbol in candidates
            and bar.granularity != settings.resolution_granularity
            for bar in bars
        )
        reason = (
            ExclusionReason.GRANULARITY_MISMATCH
            if wrong_granularity
            else ExclusionReason.SERIES_UNAVAILABLE
        )
        return _unresolved(
            claim=claim,
            data_resolution=DataResolution.UNAVAILABLE,
            reasons=(reason,),
            band=band,
            anchor=resolution,
            binding=binding,
            maturity=maturity,
            anchor_price=anchor_price,
        )

    series_bars = bound_bars(binding, bars)
    window_end = evaluated_at + window

    # Conflict detection is scoped to the bars that would actually be USED as
    # evidence, so a contradiction outside the horizon cannot veto an
    # observation it never touched.
    eligible = [
        bar
        for bar in series_bars
        if evaluated_at < _utc(bar.bar_time) and bar.closes_within(window_end)
    ]
    canonicalization = canonicalize_bars(eligible)
    cover = coverage(
        series_bars,
        evaluated_at=evaluated_at,
        window=window,
        now=reference,
        max_gap_multiple=settings.max_gap_multiple,
        min_bars_for_cadence=settings.min_bars_for_cadence,
    )
    coverage_status = str(cover["status"])
    latest_bar = max((_utc(b.bar_time) for b in series_bars), default=None)
    maturity = assess_maturity(
        evaluated_at=evaluated_at,
        window=window,
        now=reference,
        coverage_status=coverage_status,
        latest_captured_bar=latest_bar,
    )

    if canonicalization.has_conflict:
        return _unresolved(
            claim=claim,
            data_resolution=DataResolution.UNAVAILABLE,
            reasons=(ExclusionReason.BAR_CONTENT_CONFLICT,),
            band=band,
            anchor=resolution,
            binding=binding,
            maturity=maturity,
            canonicalization=canonicalization,
            coverage_status=coverage_status,
            anchor_price=anchor_price,
        )

    # -- maturity ----------------------------------------------------------
    if maturity.state is MaturityState.NOT_MATURED:
        return _unresolved(
            claim=claim,
            data_resolution=DataResolution.NOT_MATURED,
            reasons=(ExclusionReason.WINDOW_OPEN,),
            band=band,
            anchor=resolution,
            binding=binding,
            maturity=maturity,
            canonicalization=canonicalization,
            coverage_status=coverage_status,
            anchor_price=anchor_price,
            excluded=False,
        )

    path = canonicalization.bars
    if not path or not maturity.permits_verdict:
        return _unresolved(
            claim=claim,
            data_resolution=maturity.state.to_data_resolution(),
            reasons=(ExclusionReason.NO_BARS_AFTER_MATURITY,),
            band=band,
            anchor=resolution,
            binding=binding,
            maturity=maturity,
            canonicalization=canonicalization,
            coverage_status=coverage_status,
            anchor_price=anchor_price,
            excluded=False,
        )

    # -- measurement -------------------------------------------------------
    last = terminal_bar(path, evaluated_at=evaluated_at, window=window)
    terminal_return = (
        (last.analysis_close / anchor_price) - 1.0 if last is not None else None
    )

    mfe, mae, bars_to_mfe, bars_to_mae = _excursions(
        path, claim=claim, anchor_price=anchor_price
    )

    # ATR is a PRICE distance, so the fractional excursions are converted back
    # to price distance before dividing. Only a point-in-time anchor ATR is
    # admissible: a reconstructed or future volatility would be lookahead.
    anchor_atr = band.atr
    mfe_atr = mae_atr = None
    if anchor_atr is not None and anchor_atr > 0.0:
        if mfe is not None:
            mfe_atr = (mfe * anchor_price) / anchor_atr
        if mae is not None:
            mae_atr = (mae * anchor_price) / anchor_atr

    path_complete = (
        maturity.state is MaturityState.MATURED
        and coverage_status == COVERAGE_RESOLVABLE
    )
    data_resolution = maturity.state.to_data_resolution()

    reasons: list[ExclusionReason] = []
    if not path_complete:
        reasons.append(ExclusionReason.COVERAGE_GAP)

    return DirectionPathResolution(
        claim_direction=claim,
        direction=_direction_outcome(
            claim=claim, terminal_return=terminal_return, band=band.band
        ),
        data_resolution=data_resolution,
        reasons=tuple(reasons),
        excursion=ExcursionMeasures(
            terminal_return=terminal_return,
            mfe=mfe,
            mae=mae,
            mfe_atr=mfe_atr,
            mae_atr=mae_atr,
            bars_to_mfe=bars_to_mfe,
            bars_to_mae=bars_to_mae,
            path_bars=len(path),
        ),
        band=band,
        terminal_bar_time=last.bar_time_iso if last is not None else None,
        path_complete=path_complete,
        excursion_is_lower_bound=not path_complete,
        anchor=resolution,
        binding=binding,
        maturity=maturity,
        canonicalization=canonicalization,
        eligibility_pool=_pool_for(resolution, binding, excluded=False),
        coverage_status=coverage_status,
        anchor_price=anchor_price,
    )


__all__ = [
    "DirectionPathResolution",
    "claim_direction",
    "resolve_direction_and_path",
]
