"""Architecture B2 -- Oil asset module (transmission diagnostic).

Three transmission channels, matching what the production Oil formula prices:

    A. Oil price / trend transmission   (the market's own read on balance)
    B. USD transmission                 (oil is USD-denominated)
    C. Supply / geopolitical narrative  (sentiment about supply, not supply)

As with Gold, all three are TRANSFORMATIONS of evidence that already votes, so
the module adds no voting power. Sign conventions come from
``_calc_oil_score_only``, which prices ``0.40 * price_momentum + 0.20 * (-usd_macro)
+ 0.40 * (news / 0.50)``.

A word on channel C, because it is the easiest place in this project to fool
oneself. The news layer matches phrases like "inventory draw", "supply cut" and
"output increase". Those are *sentiment about* physical balance, not a physical
balance dataset. The channel is labelled a narrative channel for that reason,
and the actual physical series -- inventories, OPEC production and quotas,
refinery runs, shipping, term structure -- are declared dormant. No weak proxy
is substituted for them.
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

MODULE_KEY = "oil_module_v1"
INSTRUMENT = "Oil"

NEWS_POINTS_SCALE = 0.50

PRICE_TREND_TRANSMISSION = DriverDefinition(
    key="price_trend_transmission",
    label="Oil price / trend transmission",
    evidence_class=DriverEvidenceClass.TRANSFORMATION,
    horizon=Horizon.TACTICAL,
    universal_family_overlap="directional",
    rationale=(
        "Crude's own price path is the market's aggregate read on physical "
        "balance, and it is the single largest term in the production Oil "
        "score. Price trend already votes through the Directional technical "
        "family, so this channel restates it in transmission terms and must "
        "not be counted a second time."
    ),
    data_sources=("OIL_SERIES wti/brent via _oil_price_momentum_score",),
)

USD_TRANSMISSION = DriverDefinition(
    key="usd_transmission",
    label="USD transmission",
    evidence_class=DriverEvidenceClass.TRANSFORMATION,
    horizon=Horizon.TACTICAL,
    universal_family_overlap="macro_activity",
    rationale=(
        "Oil is invoiced in dollars, so broad dollar strength mechanically "
        "pressures the dollar price for a given physical balance. The USD "
        "composite is what the universal families read for this instrument, "
        "making this a restatement of an existing vote."
    ),
    data_sources=("compute_composite('USD').macro_score",),
)

SUPPLY_NARRATIVE_TRANSMISSION = DriverDefinition(
    key="supply_narrative_transmission",
    label="Supply / geopolitical narrative transmission",
    evidence_class=DriverEvidenceClass.TRANSFORMATION,
    horizon=Horizon.TACTICAL,
    universal_family_overlap="news_geopolitical",
    rationale=(
        "Supply disruption and demand narratives reach price through "
        "sentiment, which the News / Geopolitical family already scores. This "
        "is explicitly a NARRATIVE channel: it reads what is being said about "
        "physical balance, not the balance itself. Inventories, OPEC output, "
        "refinery runs, shipping and term structure remain dormant because "
        "this project holds no such series."
    ),
    data_sources=("analyze_news_rule_based -> scores['Oil']",),
)

DRIVERS: tuple[DriverDefinition, ...] = (
    PRICE_TREND_TRANSMISSION,
    USD_TRANSMISSION,
    SUPPLY_NARRATIVE_TRANSMISSION,
)

#: Documented, not computed. No physical-market data exists in this project.
DORMANT_DRIVERS: tuple[str, ...] = (
    "crude_inventories",
    "opec_production_quotas",
    "refinery_and_shipping",
    "crude_term_structure",
)

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
    oil_price_momentum: float | None = None,
    usd_macro_score: float | None = None,
    oil_news_points: float | None = None,
) -> dict[str, float | None]:
    """Map production readings onto the three Oil channels."""
    usd = _numeric(usd_macro_score)
    news = _numeric(oil_news_points)
    return {
        "price_trend_transmission": _numeric(oil_price_momentum),
        "usd_transmission": None if usd is None else -usd,
        "supply_narrative_transmission": (
            None if news is None else max(-1.0, min(1.0, news / NEWS_POINTS_SCALE))
        ),
    }


def evaluate(
    *,
    thesis_direction: Direction,
    horizon: Horizon = Horizon.TACTICAL,
    oil_price_momentum: float | None = None,
    usd_macro_score: float | None = None,
    oil_news_points: float | None = None,
) -> AssetModuleReading:
    """Evaluate the Oil transmission module. Adds no evidence to the core."""
    return build_module_reading(
        module=MODULE_KEY,
        instrument=INSTRUMENT,
        horizon=horizon,
        thesis_direction=thesis_direction,
        definitions=DRIVERS,
        values=driver_values(
            oil_price_momentum=oil_price_momentum,
            usd_macro_score=usd_macro_score,
            oil_news_points=oil_news_points,
        ),
        dormant_drivers=DORMANT_DRIVERS,
    )


TRANSMISSION_CHAIN: tuple[tuple[str, str, str], ...] = (
    ("usd", "oil", "Dollar moves reprice USD-invoiced crude"),
    ("supply_narrative", "oil", "Supply narrative shifts reprice the risk premium"),
)
