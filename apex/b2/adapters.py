"""Architecture B2 -- read-only adapters from existing production outputs.

These functions translate the return values of the live engine into B2 member
signals. They are deliberately **pure**: this module imports nothing from
``apex.production_core``, nothing from Streamlit, and nothing that performs
I/O. That is a structural guarantee, not a convention -- it means no B2 code
path can start a thread, issue an AI request, send a Telegram message, hit a
network endpoint or touch durable state.

Callers pass the dictionaries the existing functions already return; the
adapters never call those functions themselves.

Two safety rules are enforced by construction here:

*   **Missing is not flat.** Every adapter returns ``None`` for a signal it
    cannot measure. It never substitutes ``0.0``, which downstream would read
    as a genuine flat market.

*   **No macro contamination of technical evidence.** The live entry-plan layer
    takes the macro regime as an input and uses it to choose the trade
    direction, the candidate levels and the cluster it selects, so nothing in
    its output is macro-independent. Using it as technical confirmation would
    let macro evidence vote a second time. Structure evidence is therefore
    sourced only from the price-derived breakout classification, and the
    zone-based structure member is reported Unavailable with a recorded reason.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

#: The live news layers express points on a +/-0.50 scale. B2 member signals
#: use a +/-1.0 scale, so news points are rescaled by this divisor. This is the
#: same normalisation the existing score formulas already apply (`news / 0.50`).
NEWS_POINTS_SCALE = 0.50

#: Recorded reasons for members that are structurally unavailable in this project.
UNAVAILABLE_REASONS: Mapping[str, str] = {
    "price_structure_zone": (
        "The entry-zone layer is macro-conditioned: it takes the macro regime as "
        "an input and selects direction, candidate levels and the winning cluster "
        "from it. Using its output as technical confirmation would let macro "
        "evidence vote twice, so this member is Unavailable rather than flat."
    ),
    "retest_behaviour": (
        "No retest signal is computed anywhere in this project. Retest is a "
        "property of a breakout and is not fabricated here."
    ),
}


def _numeric(value: Any) -> float | None:
    """Coerce to float, or None. NaN and infinities are treated as unusable."""
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _weighted_category_score(
    rows: Iterable[Mapping[str, Any]] | None,
    categories: frozenset[str],
) -> float | None:
    """Weighted mean of ``score`` over rows whose ``cat`` is in ``categories``.

    Returns None when no row in those categories produced a usable score --
    which is the honest reading for "this evidence did not arrive", as opposed
    to a zero that would look like a balanced market.
    """
    if not rows:
        return None
    total = 0.0
    weight_sum = 0.0
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        if str(row.get("cat", "")) not in categories:
            continue
        score = _numeric(row.get("score"))
        if score is None:
            continue
        weight = _numeric(row.get("weight"))
        if weight is None or weight <= 0:
            weight = 1.0
        total += score * weight
        weight_sum += weight
    if weight_sum <= 0:
        return None
    return total / weight_sum


def _mtf_score(mtf: Mapping[str, Any] | None) -> float | None:
    """Read ``score`` out of a calc_mtf-shaped dict."""
    if not isinstance(mtf, Mapping):
        return None
    return _numeric(mtf.get("score"))


# ---------------------------------------------------------------------------
# Macro families
# ---------------------------------------------------------------------------

def policy_real_rates_signals(
    *,
    composite_rows: Sequence[Mapping[str, Any]] | None = None,
    real_yield_mtf: Mapping[str, Any] | None = None,
    nominal_yield_mtf: Mapping[str, Any] | None = None,
    inflation_expectations_mtf: Mapping[str, Any] | None = None,
) -> dict[str, float | None]:
    """Signals for the Policy / Real Rates family.

    ``composite_rows`` is ``compute_composite(...)["rows"]``; the policy member
    is the weighted mean of its ``cat == "rate"`` rows. The yield members come
    from calc_mtf readings of DFII10 / DGS10 / T10YIE.
    """
    return {
        "policy_rate_momentum": _weighted_category_score(composite_rows, frozenset({"rate"})),
        "real_yield_momentum": _mtf_score(real_yield_mtf),
        "nominal_yield_momentum": _mtf_score(nominal_yield_mtf),
        "inflation_expectations_momentum": _mtf_score(inflation_expectations_mtf),
    }


def macro_activity_signals(
    *,
    composite_rows: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, float | None]:
    """Signals for the Macro Activity family.

    Labour combines ``labor_pos`` and ``labor_neg``; calc_mtf already flips the
    sign of ``labor_neg`` series, so both are directly comparable without any
    further correction here.
    """
    return {
        "inflation_momentum": _weighted_category_score(
            composite_rows, frozenset({"inflation"})
        ),
        "labor_momentum": _weighted_category_score(
            composite_rows, frozenset({"labor_pos", "labor_neg"})
        ),
        "growth_momentum": _weighted_category_score(composite_rows, frozenset({"growth"})),
    }


def news_geopolitical_signals(
    *,
    rule_points: float | None = None,
    ai_points: float | None = None,
) -> dict[str, float | None]:
    """Signals for the News / Geopolitical family, rescaled from +/-0.50 to +/-1.0."""
    rule = _numeric(rule_points)
    ai = _numeric(ai_points)
    return {
        "rule_based_news": None if rule is None else rule / NEWS_POINTS_SCALE,
        "ai_news": None if ai is None else ai / NEWS_POINTS_SCALE,
    }


# ---------------------------------------------------------------------------
# Technical families
# ---------------------------------------------------------------------------

def directional_signals(
    *,
    tactical: Mapping[str, Any] | None = None,
    volatility_scale: float | None = None,
) -> dict[str, float | None]:
    """Signals for the Directional family, from ``compute_tactical_move`` output.

    Raw returns are tiny fractions and have to be put on the +/-1.0 scale the
    family evaluator uses.

    *   If ``volatility_scale`` (a per-bar return standard deviation) is
        supplied, each return is scaled by it, which is the correct
        volatility-relative normalisation.
    *   Otherwise a shape-preserving relative normalisation is used: each
        return is divided by the largest absolute return among those available.
        This preserves sign and relative magnitude without inventing a
        volatility constant, but it cannot distinguish a genuinely large move
        from a uniformly tiny one. That limitation is recorded rather than
        papered over.

    The multi-timeframe member is derived from the same three returns, which is
    exactly the within-family correlation this family structure exists to
    absorb into strength rather than into extra votes.
    """
    if not isinstance(tactical, Mapping):
        return {
            "short_horizon_return": None,
            "medium_horizon_return": None,
            "multi_timeframe_alignment": None,
        }

    short = _numeric(tactical.get("ret_15m"))
    medium = _numeric(tactical.get("ret_1h"))
    long = _numeric(tactical.get("ret_4h"))
    available = [v for v in (short, medium, long) if v is not None]

    scale = _numeric(volatility_scale)
    if scale is not None and scale > 0:
        divisor: float | None = scale
    else:
        magnitudes = [abs(v) for v in available]
        largest = max(magnitudes) if magnitudes else 0.0
        divisor = largest if largest > 0 else None

    def scaled(value: float | None) -> float | None:
        if value is None:
            return None
        if divisor is None:
            # Every available return is exactly zero: present, and flat.
            return 0.0
        return max(-1.0, min(1.0, value / divisor))

    if len(available) < 3:
        alignment: float | None = None
    elif short > 0 and medium > 0 and long > 0:
        alignment = 1.0
    elif short < 0 and medium < 0 and long < 0:
        alignment = -1.0
    else:
        alignment = 0.0

    return {
        "short_horizon_return": scaled(short),
        "medium_horizon_return": scaled(medium),
        "multi_timeframe_alignment": alignment,
    }


#: Mapping from the live ``structure`` label to a breakout reading.
#: Trend labels map to 0.0 -- data is present and shows no breakout -- rather
#: than to a directional value, because trend belongs to the Directional family
#: and must not be counted again here.
_BREAKOUT_BY_LABEL: Mapping[str, float] = {
    "Upside Breakout": 1.0,
    "Downside Breakdown": -1.0,
    "Higher Short-Term Trend": 0.0,
    "Lower Short-Term Trend": 0.0,
    "Range / Mean-Reversion": 0.0,
}


def structure_signals(
    *,
    tactical: Mapping[str, Any] | None = None,
) -> dict[str, float | None]:
    """Signals for the Structure family.

    Sourced only from the price-derived breakout classification, which is
    computed from closes, EMAs and prior highs/lows with no macro input. The
    other two members are Unavailable for the reasons recorded in
    ``UNAVAILABLE_REASONS`` and are never defaulted to flat.
    """
    breakout: float | None = None
    if isinstance(tactical, Mapping):
        label = str(tactical.get("structure", "")).strip()
        if label in _BREAKOUT_BY_LABEL:
            breakout = _BREAKOUT_BY_LABEL[label]

    return {
        "breakout_quality": breakout,
        "price_structure_zone": None,
        "retest_behaviour": None,
    }


# ---------------------------------------------------------------------------
# Execution inputs (not evidence -- they never change the thesis)
# ---------------------------------------------------------------------------

def execution_inputs(
    *,
    entry_plan: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Extract execution-layer inputs from ``_build_macro_entry_plan`` output.

    Using the entry plan here is legitimate precisely because execution is not
    evidence: these values affect entry quality and cost only, and never feed a
    voting family. The macro conditioning that disqualifies the plan as
    technical confirmation is harmless for execution, which is downstream of
    the thesis by design.
    """
    if not isinstance(entry_plan, Mapping):
        return {
            "invalidation_level": None,
            "entry_zone": None,
            "current_price": None,
            "atr": None,
            "atr_ratio": None,
            "room_to_opposing_atr": None,
            "asymmetry_ratio": None,
            "volatility_regime": "unavailable",
            "technical_invalidated": False,
        }

    zone_low = _numeric(entry_plan.get("zone_low"))
    zone_high = _numeric(entry_plan.get("zone_high"))
    zone = (zone_low, zone_high) if zone_low is not None and zone_high is not None else None

    quality = entry_plan.get("opportunity_quality")
    room = None
    asymmetry = None
    if isinstance(quality, Mapping) and not quality.get("unavailable"):
        room = _numeric(quality.get("room_to_opposing_structure_atr"))
        asymmetry = _numeric(quality.get("asymmetry_ratio"))

    regime = str(entry_plan.get("volatility_regime", "unavailable") or "unavailable")

    return {
        "invalidation_level": _numeric(entry_plan.get("invalidation")),
        "entry_zone": zone,
        "current_price": _numeric(entry_plan.get("current_analysis_price")),
        "atr": _numeric(entry_plan.get("atr")),
        # Current ATR over its own longer-window average, exported by the entry
        # plan. None when it could not be measured -- never defaulted to 1.0,
        # which would read as "volatility is normal" instead of "unknown".
        "atr_ratio": _numeric(entry_plan.get("atr_ratio")),
        "room_to_opposing_atr": room,
        "asymmetry_ratio": asymmetry,
        "volatility_regime": regime,
        "technical_invalidated": str(entry_plan.get("status", "")) == "INVALIDATED",
    }


def build_signals(
    *,
    composite_rows: Sequence[Mapping[str, Any]] | None = None,
    real_yield_mtf: Mapping[str, Any] | None = None,
    nominal_yield_mtf: Mapping[str, Any] | None = None,
    inflation_expectations_mtf: Mapping[str, Any] | None = None,
    rule_points: float | None = None,
    ai_points: float | None = None,
    tactical: Mapping[str, Any] | None = None,
    volatility_scale: float | None = None,
) -> dict[str, dict[str, float | None]]:
    """Assemble the full ``signals_by_family`` mapping for the voting core."""
    return {
        "policy_real_rates": policy_real_rates_signals(
            composite_rows=composite_rows,
            real_yield_mtf=real_yield_mtf,
            nominal_yield_mtf=nominal_yield_mtf,
            inflation_expectations_mtf=inflation_expectations_mtf,
        ),
        "macro_activity": macro_activity_signals(composite_rows=composite_rows),
        "news_geopolitical": news_geopolitical_signals(
            rule_points=rule_points, ai_points=ai_points
        ),
        "directional": directional_signals(
            tactical=tactical, volatility_scale=volatility_scale
        ),
        "structure": structure_signals(tactical=tactical),
    }
