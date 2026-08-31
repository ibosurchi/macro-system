"""Architecture B2 -- Stage D-2 series binding.

This module exists because of a live defect. Stage D-1's read path joined a
shadow observation to its forward bars on exact symbol equality, and that quietly
failed for Gold:

*   The ANCHOR records the symbol of the 5-minute tactical series, which is
    ``XAUUSD=X``.
*   The DAILY capture stored Gold under ``GC=F``, because ``XAUUSD=X`` does not
    return enough daily bars.

Exact-equality joining therefore found zero bars for every Gold observation and
reported ``unresolved_no_bars`` -- a *capture failure* -- while a complete bar
history sat in the table. Silent, total, and specific to one instrument.

The fix is not to loosen the join. It is to make the binding EXPLICIT and to
stamp what it did:

``SERIES_EXACT``
    Bars were found under the very symbol the anchor recorded. The only quality
    admitted to the captured calibration pool.

``SERIES_SUBSTITUTED``
    Bars were found under a declared alternative for the same instrument. This
    is real evidence and resolves normally, but it is a different series from
    the one the evaluation observed -- for Gold, spot bullion versus a COMEX
    futures contract, which carry a genuine basis. It is therefore tiered into
    the reconstructed research pool and can never reach a calibration claim.

``SERIES_UNAVAILABLE``
    No candidate produced bars. Reported with a reason; never a failed
    prediction.

The second job of this module is the inversion check. ``invert`` is recorded on
BOTH the anchor and every stored bar, and nothing previously compared them. A
disagreement would silently flip the sign of every return for that observation
-- which for CAD, CHF and JPY is the difference between a confirmed thesis and a
failed one. Disagreement is therefore an explicit ``INVERSION_MISMATCH`` that
excludes the observation. It is never reconciled, because there is no honest way
to choose which of two contradictory conventions was in force.

This module is pure. It performs no I/O and reads no configuration file.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, Sequence

from .anchor import MarketAnchor, SymbolConvention
from .bars import MarketBar


class SeriesBindingQuality(Enum):
    """Which series actually resolved an observation, relative to its anchor."""

    SERIES_EXACT = "series_exact"
    SERIES_SUBSTITUTED = "series_substituted"
    SERIES_UNAVAILABLE = "series_unavailable"

    @property
    def permits_capture_pool(self) -> bool:
        """Only an exact series binding may enter the captured calibration pool."""
        return self is SeriesBindingQuality.SERIES_EXACT


class InversionAgreement(Enum):
    """Whether the anchor and the bars agree on the direction convention."""

    AGREED = "agreed"
    MISMATCH = "inversion_mismatch"
    UNKNOWN = "unknown"          # nothing to compare against

    @property
    def is_usable(self) -> bool:
        """A mismatch is never usable. UNKNOWN is, because there is no conflict."""
        return self is not InversionAgreement.MISMATCH


def candidate_symbols(
    anchor: MarketAnchor | None,
    convention: SymbolConvention | None,
) -> tuple[str, ...]:
    """Every symbol that could legitimately serve this instrument, in priority order.

    Order matters and is deliberate:

    1.  The anchor's own symbol first. It is what the evaluation actually
        observed, so a match against it is the only exact binding.
    2.  The anchor's requested symbol, which differs from the served one when
        the 5-minute fetch itself fell back.
    3.  The production convention's primary symbol.
    4.  The convention's declared fallbacks, in their configured order.

    De-duplicated while preserving that order, so a symbol appearing in several
    roles is tried once and the priority of its FIRST appearance is what counts.
    Empty entries are dropped rather than becoming an empty-string candidate
    that would match nothing and waste a pass.
    """
    ordered: list[str] = []
    if anchor is not None:
        ordered.append(anchor.symbol)
        ordered.append(anchor.symbol_requested)
    if convention is not None:
        ordered.append(convention.symbol)
        ordered.extend(convention.fallback_symbols)

    seen: set[str] = set()
    unique: list[str] = []
    for symbol in ordered:
        cleaned = str(symbol or "").strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        unique.append(cleaned)
    return tuple(unique)


@dataclass(frozen=True)
class SeriesBinding:
    """Which market series was bound to one observation, and how honestly."""

    anchor_symbol: str
    candidates: tuple[str, ...]
    bound_symbol: str | None
    quality: SeriesBindingQuality
    inversion: InversionAgreement
    anchor_invert: bool | None
    bound_invert: bool | None
    granularity: str
    #: True when the bars come from a DIFFERENT MARKET SERIES than the anchor --
    #: for Gold, spot bullion against a COMEX futures contract. Those carry a
    #: real basis, so this bars the captured pool.
    cross_source: bool
    #: True when the anchor and the bars are the same series sampled
    #: differently -- in practice always, since the anchor is a 5-minute
    #: tactical close and the bars are daily. Recorded, but NOT disqualifying:
    #: it is one instrument at two sampling rates, not two instruments.
    #:
    #: Note that ``price_source`` is deliberately NOT compared across the two.
    #: Production's 5-minute fetch and this daily fetch use the SAME Yahoo chart
    #: endpoint; the labels differ only because one describes granularity and
    #: role while the other describes the endpoint. Comparing them would
    #: manufacture a mismatch out of a naming convention and would leave the
    #: captured pool permanently empty.
    cross_granularity: bool
    bar_count: int
    notes: tuple[str, ...] = ()

    @property
    def is_bound(self) -> bool:
        return (
            self.bound_symbol is not None
            and self.quality is not SeriesBindingQuality.SERIES_UNAVAILABLE
        )

    @property
    def is_usable(self) -> bool:
        """Bound AND free of an inversion conflict."""
        return self.is_bound and self.inversion.is_usable

    @property
    def permits_capture_pool(self) -> bool:
        """Whether this binding alone would allow the captured pool.

        Anchor provenance is judged separately: a reconstructed anchor on an
        exact series is still research evidence. Both conditions must hold, and
        each is decided by the layer that owns it.
        """
        return (
            self.quality.permits_capture_pool
            and self.inversion is InversionAgreement.AGREED
            and not self.cross_source
        )

    def as_record(self) -> dict[str, Any]:
        return {
            "anchor_symbol": self.anchor_symbol,
            "candidates": list(self.candidates),
            "bound_symbol": self.bound_symbol,
            "binding_quality": self.quality.value,
            "inversion_agreement": self.inversion.value,
            "anchor_invert": self.anchor_invert,
            "bound_invert": self.bound_invert,
            "granularity": self.granularity,
            "cross_source": self.cross_source,
            "cross_granularity": self.cross_granularity,
            "bar_count": self.bar_count,
            "permits_capture_pool": self.permits_capture_pool,
            "notes": list(self.notes),
        }


def _bars_for(
    bars: Iterable[MarketBar], symbol: str, granularity: str
) -> list[MarketBar]:
    return [
        bar
        for bar in bars
        if bar.symbol == symbol and bar.granularity == granularity
    ]


def bind_series(
    *,
    anchor: MarketAnchor | None,
    convention: SymbolConvention | None,
    bars: Sequence[MarketBar],
    granularity: str,
    allow_substitution: bool = True,
) -> SeriesBinding:
    """Bind one observation to the market series that can resolve it.

    Walks the ordered candidates and takes the FIRST that has bars at the
    requested granularity. The first candidate is the anchor's own symbol, so an
    exact binding is always preferred and substitution only ever happens when
    the exact series genuinely has nothing.

    ``allow_substitution=False`` refuses anything but an exact match, which is
    how a caller asks for the captured pool only.

    Never raises. An unbindable observation returns a binding that says so.
    """
    anchor_symbol = anchor.symbol if anchor is not None else ""
    anchor_invert = bool(anchor.invert) if anchor is not None else None
    candidates = candidate_symbols(anchor, convention)
    notes: list[str] = []

    if not candidates:
        return SeriesBinding(
            anchor_symbol=anchor_symbol,
            candidates=(),
            bound_symbol=None,
            quality=SeriesBindingQuality.SERIES_UNAVAILABLE,
            inversion=InversionAgreement.UNKNOWN,
            anchor_invert=anchor_invert,
            bound_invert=None,
            granularity=granularity,
            cross_source=False,
            cross_granularity=False,
            bar_count=0,
            notes=("no_candidate_symbols",),
        )

    for index, symbol in enumerate(candidates):
        matched = _bars_for(bars, symbol, granularity)
        if not matched:
            continue

        exact = bool(anchor_symbol) and symbol == anchor_symbol
        if not exact and not allow_substitution:
            notes.append(f"substitution_disallowed:{symbol}")
            continue

        quality = (
            SeriesBindingQuality.SERIES_EXACT
            if exact
            else SeriesBindingQuality.SERIES_SUBSTITUTED
        )

        bound_invert = bool(matched[0].invert)
        inverts = {bool(bar.invert) for bar in matched}
        if len(inverts) > 1:
            # The stored series contradicts itself. Nothing to reconcile.
            inversion = InversionAgreement.MISMATCH
            notes.append("bars_disagree_on_invert")
        elif anchor_invert is None:
            inversion = InversionAgreement.UNKNOWN
            notes.append("anchor_invert_unknown")
        elif bound_invert == anchor_invert:
            inversion = InversionAgreement.AGREED
        else:
            inversion = InversionAgreement.MISMATCH
            notes.append(
                f"anchor_invert={anchor_invert} bars_invert={bound_invert}"
            )

        anchor_granularity = anchor.granularity if anchor is not None else ""
        cross_source = not exact
        cross_granularity = bool(
            anchor_granularity and anchor_granularity != granularity
        )
        if not exact:
            notes.append(f"substituted:{anchor_symbol or '?'}->{symbol}")
        if anchor_granularity and anchor_granularity != granularity:
            notes.append(
                f"cross_granularity:anchor={anchor_granularity} bars={granularity}"
            )
        if index > 0 and exact:
            notes.append("anchor_symbol_matched_at_lower_priority")

        return SeriesBinding(
            anchor_symbol=anchor_symbol,
            candidates=candidates,
            bound_symbol=symbol,
            quality=quality,
            inversion=inversion,
            anchor_invert=anchor_invert,
            bound_invert=bound_invert,
            granularity=granularity,
            cross_source=cross_source,
            cross_granularity=cross_granularity,
            bar_count=len(matched),
            notes=tuple(notes),
        )

    notes.append("no_candidate_symbol_had_bars")
    return SeriesBinding(
        anchor_symbol=anchor_symbol,
        candidates=candidates,
        bound_symbol=None,
        quality=SeriesBindingQuality.SERIES_UNAVAILABLE,
        inversion=InversionAgreement.UNKNOWN,
        anchor_invert=anchor_invert,
        bound_invert=None,
        granularity=granularity,
        cross_source=False,
        cross_granularity=False,
        bar_count=0,
        notes=tuple(notes),
    )


def bound_bars(
    binding: SeriesBinding, bars: Sequence[MarketBar]
) -> tuple[MarketBar, ...]:
    """The bars belonging to the bound series, in time order.

    Returns nothing for an unusable binding -- including an inversion mismatch,
    where bars exist but their direction convention cannot be trusted. Handing
    those back would invite a caller to use them anyway.
    """
    if not binding.is_usable or binding.bound_symbol is None:
        return ()
    matched = _bars_for(bars, binding.bound_symbol, binding.granularity)
    matched.sort(key=lambda bar: bar.bar_time)
    return tuple(matched)


__all__ = [
    "InversionAgreement",
    "SeriesBinding",
    "SeriesBindingQuality",
    "bind_series",
    "bound_bars",
    "candidate_symbols",
]
