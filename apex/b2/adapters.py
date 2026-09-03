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

import math
from typing import Any, Iterable, Mapping, Sequence

from .enums import Direction
from .registry import STANDARDISED_SIGMA_FLAT_THRESHOLD

#: Bumped when the MEANING of an adapter output changes -- a new normalisation,
#: a new availability rule, a new member derivation. Stored on every record so a
#: historical reading stays interpretable if these rules are ever revised.
ADAPTER_VERSION = "b2-adapters-v2"

#: The live news layers express points on a +/-0.50 scale. B2 member signals
#: use a +/-1.0 scale, so news points are rescaled by this divisor. This is the
#: same normalisation the existing score formulas already apply (`news / 0.50`).
NEWS_POINTS_SCALE = 0.50

#: Bar counts behind each exported tactical return, read off
#: ``compute_tactical_move``: it computes ``ret(3)``, ``ret(12)`` and ``ret(48)``
#: on the 5-minute series and exports them as ret_15m / ret_1h / ret_4h.
#:
#: These are needed because a multi-bar return does NOT have the same dispersion
#: as a single-bar one. Production itself scales by ``vol5 * sqrt(bars)`` inside
#: ``normalized_move``; dividing every return by a bare per-bar sigma instead
#: inflates the longer horizons by sqrt(bars) -- about 3.5x at one hour and 6.9x
#: at four -- and makes the stored member magnitudes wrong.
SHORT_HORIZON_BARS = 3
MEDIUM_HORIZON_BARS = 12
LONG_HORIZON_BARS = 48

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
    "directional_returns_without_volatility_scale": (
        "The tactical returns are raw fractional moves and carry no scale of "
        "their own. Without the per-bar return volatility production already "
        "computed, there is no way to say whether a move is large or small, and "
        "the previous fallback -- dividing each return by the largest of the "
        "three -- invented a scale rather than measuring one: it guaranteed that "
        "one member always read at full magnitude no matter how quiet the market "
        "was. The members are Unavailable instead."
    ),
    "technical_invalidated": (
        "B2 cannot compute a technical invalidation independently. The only "
        "invalidation in this project comes from the macro entry plan, whose "
        "level AND whose comparison side are both chosen from production's macro "
        "regime -- so a flag derived from it is a macro verdict wearing a "
        "technical label, and letting it pre-empt a B2 decision state would be "
        "macro evidence voting a second time outside the family framework. It is "
        "reported Unavailable until B2 derives its own level in its own direction."
    ),
}


def _direction_from_plan_label(label: Any) -> Direction:
    """Production's entry-plan side, as a B2 direction.

    ``BUY``/``SELL`` are the only two directional values ``_build_macro_entry_plan``
    emits; ``WAIT`` accompanies its neutral return. Anything else is Unavailable
    rather than guessed.
    """
    text = str(label or "").strip().upper()
    if text == "BUY":
        return Direction.BULLISH
    if text == "SELL":
        return Direction.BEARISH
    return Direction.UNAVAILABLE


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
    article_count: int | None = None,
    ai_active: bool | None = None,
) -> dict[str, float | None]:
    """Signals for the News / Geopolitical family, rescaled from +/-0.50 to +/-1.0.

    ``article_count`` and ``ai_active`` exist because the production news layer
    cannot express "unavailable" in its return value. ``analyze_news_rule_based``
    initialises every asset score to ``0.0`` and returns that same all-zero map
    when no articles were fetched at all, and ``gold_ai_points`` is ``0.0``
    whenever the shared AI batch is inactive. Both are indistinguishable, in the
    number alone, from a genuinely balanced news flow.

    A zero that means "nothing arrived" must not enter B2 as FLAT. Flat asserts
    that the evidence was read and showed no direction, which is precisely the
    claim a dead feed is not entitled to make -- and because News is half of
    every currency score, letting a broken feed read as a calm market is the most
    consequential form this failure could take.

    Both parameters are optional and default to None, meaning "the caller did not
    say". In that case the points are trusted as given, which preserves the
    behaviour of callers that genuinely hold a measured value. A caller reading
    production output should pass them.
    """
    rule = _numeric(rule_points)
    ai = _numeric(ai_points)

    if article_count is not None and int(article_count) <= 0:
        # No article reached the scorer, so there is no news reading of any
        # kind -- neither leg is entitled to a value.
        return {"rule_based_news": None, "ai_news": None}

    if ai_active is not None and not ai_active:
        ai = None

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
    long_horizon_bars: int | None = None,
    neutral_band: float = STANDARDISED_SIGMA_FLAT_THRESHOLD,
) -> dict[str, float | None]:
    """Signals for the Directional family, from ``compute_tactical_move`` output.

    The two return members are emitted as **z-scores on the STANDARDISED_SIGMA
    scale**, not as bounded [-1, 1] scores::

        z = ret / (volatility_scale * sqrt(bars))

    Three things about that formula are deliberate.

    *   ``sqrt(bars)`` is production's own term. ``normalized_move`` divides by
        ``vol5 * sqrt(bars)``; the previous version of this adapter divided by a
        bare ``vol5`` while claiming to use "exactly the scale that function
        already used internally". It did not, and the longer horizons were
        inflated by up to 6.9x as a result.
    *   ``volatility_scale`` is REQUIRED. When it is absent both return members
        are Unavailable. The old fallback -- dividing by the largest of the three
        returns -- guaranteed that one member always read at full magnitude
        however quiet the market was, which is a manufactured scale rather than a
        measured one. See ``UNAVAILABLE_REASONS``.
    *   The result is NOT clamped. Clamping to +/-1 was a consequence of assuming
        a bounded scale; on a sigma scale it would discard exactly the
        information that distinguishes a 1-sigma move from a 4-sigma one, and it
        would corrupt the stored member value that Stage-3 records keep for
        re-analysis.

    ``multi_timeframe_alignment`` is a CONFIRMATION member and now carries a
    magnitude requirement as well as a sign requirement. It reads +/-1.0 only
    when all three timeframes share a sign AND both measurable members
    independently clear the neutral band. Sign agreement alone is worth roughly a
    coin flip cubed on a random walk, so a member that went to full magnitude on
    sign agreement alone was manufacturing a third agreeing vote out of noise.

    The four-hour leg contributes its SIGN only, never its magnitude. Production
    silently substitutes a shorter lookback for ``ret_4h`` when history is short
    (``ret(48)`` becomes ``ret(max(6, len//3))``) without relabelling it, so its
    bar count is not knowable from the exported dictionary. ``long_horizon_bars``
    lets a caller who does know assert it; absent that assertion the magnitude is
    not used, rather than being standardised against a bar count that may be wrong.
    """
    unavailable = {
        "short_horizon_return": None,
        "medium_horizon_return": None,
        "multi_timeframe_alignment": None,
    }
    if not isinstance(tactical, Mapping):
        return dict(unavailable)

    short = _numeric(tactical.get("ret_15m"))
    medium = _numeric(tactical.get("ret_1h"))
    long = _numeric(tactical.get("ret_4h"))

    scale = _numeric(volatility_scale)
    if scale is None or scale <= 0:
        # No measurable volatility scale: the returns cannot be placed on any
        # scale at all. Unavailable, never flat.
        return dict(unavailable)

    def standardised(value: float | None, bars: int) -> float | None:
        if value is None:
            return None
        return value / (scale * math.sqrt(max(int(bars), 1)))

    short_z = standardised(short, SHORT_HORIZON_BARS)
    medium_z = standardised(medium, MEDIUM_HORIZON_BARS)

    # The alignment gate MUST use the same band the members are classified
    # against. If it used a fixed constant while the members used a declared
    # one, the two could drift apart and alignment could confirm a move the
    # family had already judged flat. It is injectable for the same reason the
    # member band is: so the null benchmark can vary one number, not two.
    band = abs(float(neutral_band))

    def clears(value: float | None, sign: int) -> bool:
        return value is not None and (
            value > band if sign > 0 else value < -band
        )

    alignment: float | None
    if short is None or medium is None or long is None:
        # Alignment is a statement about three timeframes. With fewer than three
        # it is unknown, not zero.
        alignment = None
    elif short > 0 and medium > 0 and long > 0 and clears(short_z, 1) and clears(medium_z, 1):
        alignment = 1.0
    elif short < 0 and medium < 0 and long < 0 and clears(short_z, -1) and clears(medium_z, -1):
        alignment = -1.0
    else:
        # All three timeframes were read and they do not jointly confirm.
        # Present, and carrying no confirmation: flat, not unavailable.
        alignment = 0.0

    return {
        "short_horizon_return": short_z,
        "medium_horizon_return": medium_z,
        "multi_timeframe_alignment": alignment,
    }


def directional_provenance(
    *, volatility_scale: float | None = None, long_horizon_bars: int | None = None
) -> dict[str, object]:
    """How the Directional members were derived, for the record.

    Stored alongside the values so a later analyst can reconstruct the
    normalisation without consulting repository history.
    """
    scale = _numeric(volatility_scale)
    usable = scale is not None and scale > 0
    return {
        "normalisation": "z = ret / (volatility_scale * sqrt(bars))",
        "volatility_scale": scale if usable else None,
        "volatility_scale_available": bool(usable),
        "bars": {
            "short_horizon_return": SHORT_HORIZON_BARS,
            "medium_horizon_return": MEDIUM_HORIZON_BARS,
            "long_leg_sign_only": (
                int(long_horizon_bars) if long_horizon_bars is not None else None
            ),
        },
        "clamped": False,
        "alignment_rule": (
            "sign agreement across all three timeframes AND both standardised "
            f"members independently beyond {STANDARDISED_SIGMA_FLAT_THRESHOLD} sigma"
        ),
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
            "technical_invalidated": None,
            "entry_plan_direction": Direction.UNAVAILABLE,
            "entry_plan_status": "",
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
        # UNAVAILABLE, always. See UNAVAILABLE_REASONS["technical_invalidated"]:
        # the entry plan's invalidation level AND the side of its comparison are
        # both selected from production's macro regime, so its INVALIDATED status
        # is a macro verdict. It previously entered B2 as a boolean named
        # "technical" and short-circuited the decision state ahead of almost
        # every other branch. The status is still carried below as a diagnostic
        # so the mismatch rate stays measurable; it is no longer evidence.
        "technical_invalidated": None,
        # The side production built this plan for. Carried explicitly so the
        # execution layer can refuse to measure B2's thesis against a zone,
        # invalidation and ATR geometry that belong to the opposite trade.
        "entry_plan_direction": _direction_from_plan_label(entry_plan.get("direction")),
        "entry_plan_status": str(entry_plan.get("status", "") or ""),
    }


def build_signals(
    *,
    composite_rows: Sequence[Mapping[str, Any]] | None = None,
    real_yield_mtf: Mapping[str, Any] | None = None,
    nominal_yield_mtf: Mapping[str, Any] | None = None,
    inflation_expectations_mtf: Mapping[str, Any] | None = None,
    rule_points: float | None = None,
    ai_points: float | None = None,
    article_count: int | None = None,
    ai_active: bool | None = None,
    tactical: Mapping[str, Any] | None = None,
    volatility_scale: float | None = None,
    long_horizon_bars: int | None = None,
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
            rule_points=rule_points,
            ai_points=ai_points,
            article_count=article_count,
            ai_active=ai_active,
        ),
        "directional": directional_signals(
            tactical=tactical,
            volatility_scale=volatility_scale,
            long_horizon_bars=long_horizon_bars,
        ),
        "structure": structure_signals(tactical=tactical),
    }


def signal_provenance(
    *,
    volatility_scale: float | None = None,
    long_horizon_bars: int | None = None,
    article_count: int | None = None,
    ai_active: bool | None = None,
) -> dict[str, object]:
    """Adapter-side provenance for one evaluation, stored on the record.

    Together with ``registry.member_spec_provenance`` this is what makes a
    stored member value re-scorable: the value, the scale it is on, the band
    applied to it, and how it was derived from the production output.
    """
    return {
        "adapter_version": ADAPTER_VERSION,
        "news_points_scale": NEWS_POINTS_SCALE,
        "news_availability": {
            "article_count": (
                int(article_count) if article_count is not None else None
            ),
            "ai_active": None if ai_active is None else bool(ai_active),
            "rule": (
                "zero points with zero articles is Unavailable, not flat; "
                "an inactive AI batch makes the ai_news member Unavailable"
            ),
        },
        "directional": directional_provenance(
            volatility_scale=volatility_scale, long_horizon_bars=long_horizon_bars
        ),
        "unavailable_reasons": dict(UNAVAILABLE_REASONS),
    }
