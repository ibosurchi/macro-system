"""Architecture B2 -- Nasdaq asset module (transmission diagnostic).

Three transmission channels:

    A. Real-yield / duration sensitivity   (the discount rate on future earnings)
    B. USD / financial-conditions          (the dollar leg of financial conditions)
    C. Growth-risk news transmission       (the Nasdaq-specific narrative)

All three are TRANSFORMATIONS of evidence that already votes, so the module adds
no voting power. Sign conventions come from ``_calc_ndx_score_only``, which
prices ``0.40 * momentum + 0.20 * (-real_yield) + 0.15 * (-usd_macro) + 0.25 *
(news / 0.50)``.

**Price trend is deliberately NOT a channel here.** Nasdaq price momentum
already votes through the Directional technical family, and the module is capped
at three drivers, so including it would spend a slot restating something the
core already reads while adding nothing the Directional family does not already
say. Oil's module does carry a price channel because there the price IS the
market's read on physical balance and no separate balance data exists; for an
equity index the earnings and liquidity data that would play that role is simply
absent, and is declared dormant rather than substituted by the chart.
"""
from __future__ import annotations

from typing import Any, Mapping

from ..enums import Direction, Horizon
from .base import (
    AssetModuleReading,
    DriverDefinition,
    DriverEvidenceClass,
    build_module_reading,
    validate_definitions,
)

MODULE_KEY = "nasdaq_module_v1"
INSTRUMENT = "NDX"

NEWS_POINTS_SCALE = 0.50

REAL_YIELD_SENSITIVITY = DriverDefinition(
    key="real_yield_sensitivity",
    label="Real-yield / duration sensitivity",
    evidence_class=DriverEvidenceClass.TRANSFORMATION,
    horizon=Horizon.TACTICAL,
    universal_family_overlap="policy_real_rates",
    rationale=(
        "A long-duration growth index is valued on distant cash flows, so the "
        "real discount rate moves it more than it moves a short-duration index. "
        "DFII10 already votes inside Policy / Real Rates, so this channel "
        "restates that evidence in duration terms and must not vote again."
    ),
    data_sources=("GOLD_SERIES real_yield (DFII10), via calc_mtf",),
)

USD_FINANCIAL_CONDITIONS = DriverDefinition(
    key="usd_financial_conditions",
    label="USD / financial-conditions transmission",
    evidence_class=DriverEvidenceClass.TRANSFORMATION,
    horizon=Horizon.TACTICAL,
    universal_family_overlap="macro_activity",
    rationale=(
        "The dollar is one leg of global financial conditions, and a tightening "
        "dollar tightens conditions for the globally-exposed megacaps that "
        "dominate the index. The USD composite is what the universal families "
        "read for this instrument, so this restates an existing vote."
    ),
    data_sources=("compute_composite('USD').macro_score",),
)

GROWTH_RISK_NEWS = DriverDefinition(
    key="growth_risk_news_transmission",
    label="Growth-risk news transmission",
    evidence_class=DriverEvidenceClass.TRANSFORMATION,
    horizon=Horizon.TACTICAL,
    universal_family_overlap="news_geopolitical",
    rationale=(
        "Sector narratives -- AI demand, chip supply and restrictions, megacap "
        "results as they are reported in the wires -- reach the index through "
        "sentiment, which the News / Geopolitical family already scores. This "
        "is coverage of earnings, not an earnings dataset: actual revisions and "
        "estimates remain dormant."
    ),
    data_sources=("analyze_news_rule_based -> scores['Nasdaq']",),
)

DRIVERS: tuple[DriverDefinition, ...] = (
    REAL_YIELD_SENSITIVITY,
    USD_FINANCIAL_CONDITIONS,
    GROWTH_RISK_NEWS,
)

#: Documented, not computed. Price trend is listed here as covered elsewhere,
#: not as missing: it votes through the Directional technical family and is
#: deliberately not restated as a fourth channel.
DORMANT_DRIVERS: tuple[str, ...] = (
    "earnings_revisions",
    "equity_liquidity_flows",
    "index_breadth_concentration",
)

#: Recorded so a reader knows the omission is a decision, not an oversight.
COVERED_ELSEWHERE: dict[str, str] = {
    "price_trend": (
        "Nasdaq price momentum votes through the Directional technical family. "
        "It is not restated here, so it cannot be counted twice."
    ),
}

validate_definitions(MODULE_KEY, DRIVERS)


def _numeric(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def driver_values(
    *,
    real_yield_mtf: Mapping[str, Any] | None = None,
    usd_macro_score: float | None = None,
    nasdaq_news_points: float | None = None,
) -> dict[str, float | None]:
    """Map production readings onto the three Nasdaq channels."""
    real_yield_score = None
    if isinstance(real_yield_mtf, Mapping):
        real_yield_score = _numeric(real_yield_mtf.get("score"))

    usd = _numeric(usd_macro_score)
    news = _numeric(nasdaq_news_points)

    return {
        "real_yield_sensitivity": (
            None if real_yield_score is None else -real_yield_score
        ),
        "usd_financial_conditions": None if usd is None else -usd,
        "growth_risk_news_transmission": (
            None if news is None else max(-1.0, min(1.0, news / NEWS_POINTS_SCALE))
        ),
    }


def evaluate(
    *,
    thesis_direction: Direction,
    horizon: Horizon = Horizon.TACTICAL,
    real_yield_mtf: Mapping[str, Any] | None = None,
    usd_macro_score: float | None = None,
    nasdaq_news_points: float | None = None,
) -> AssetModuleReading:
    """Evaluate the Nasdaq transmission module. Adds no evidence to the core."""
    return build_module_reading(
        module=MODULE_KEY,
        instrument=INSTRUMENT,
        horizon=horizon,
        thesis_direction=thesis_direction,
        definitions=DRIVERS,
        values=driver_values(
            real_yield_mtf=real_yield_mtf,
            usd_macro_score=usd_macro_score,
            nasdaq_news_points=nasdaq_news_points,
        ),
        dormant_drivers=DORMANT_DRIVERS,
        notes=tuple(f"{key}: {reason}" for key, reason in COVERED_ELSEWHERE.items()),
    )


TRANSMISSION_CHAIN: tuple[tuple[str, str, str], ...] = (
    ("real_yields", "nasdaq", "The real discount rate reprices long-duration earnings"),
    ("usd", "nasdaq", "Dollar tightening tightens conditions for global megacaps"),
)
