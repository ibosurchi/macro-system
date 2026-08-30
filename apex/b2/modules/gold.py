"""Architecture B2 -- Gold asset module (transmission diagnostic).

Three transmission channels, matching the ones the production Gold formula
already prices:

    A. Real-rate / policy transmission   (falling real yields -> supportive)
    B. USD transmission                  (weaker USD -> supportive)
    C. Safe-haven / news transmission    (risk and gold-specific flow)

Every one of the three is a TRANSFORMATION of evidence that already votes in
the universal core -- real yields sit inside Policy / Real Rates, the USD
composite feeds Macro Activity for this instrument, and gold news is the News /
Geopolitical family's input. **None of them earns Gold a second vote.** What the
module adds is the question the voting core cannot answer: whether each channel
is presently carrying the thesis, conflicting with it, or silent.

Genuinely independent Gold evidence -- official-sector demand and ETF flows --
does not exist in this project and is declared dormant rather than proxied. The
news layer contains keyword phrases such as "central bank buying"; those are
sentiment about the topic, not the underlying series, and are not promoted here.

Sign convention is taken from production, not invented: ``_calc_gold_score_only``
already computes ``gold_ry = -real_yield_score`` and ``gold_usd = -usd_macro``,
so a positive driver value means supportive for gold in exactly the sense the
live engine already uses.
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

MODULE_KEY = "gold_module_v1"
INSTRUMENT = "Gold"

#: The +/-0.50 convention the production news layers use, rescaled to +/-1.0.
NEWS_POINTS_SCALE = 0.50

REAL_RATE_TRANSMISSION = DriverDefinition(
    key="real_rate_transmission",
    label="Real-rate / policy transmission",
    evidence_class=DriverEvidenceClass.TRANSFORMATION,
    horizon=Horizon.TACTICAL,
    universal_family_overlap="policy_real_rates",
    rationale=(
        "Gold pays no coupon, so the real yield is the opportunity cost of "
        "holding it: falling real rates ease that cost. DFII10 is already a "
        "member of the Policy / Real Rates family, so this channel restates "
        "evidence that has already voted and must not vote again. Its value "
        "here is diagnostic -- whether the rate channel is actually transmitting."
    ),
    data_sources=("GOLD_SERIES real_yield (DFII10), via calc_mtf",),
)

USD_TRANSMISSION = DriverDefinition(
    key="usd_transmission",
    label="USD transmission",
    evidence_class=DriverEvidenceClass.TRANSFORMATION,
    horizon=Horizon.TACTICAL,
    universal_family_overlap="macro_activity",
    rationale=(
        "Gold is USD-denominated, so broad dollar strength mechanically "
        "pressures the USD price. The USD composite rows are what the universal "
        "families read for this instrument, making this a restatement of an "
        "existing vote rather than new evidence."
    ),
    data_sources=("compute_composite('USD').macro_score",),
)

SAFE_HAVEN_TRANSMISSION = DriverDefinition(
    key="safe_haven_news_transmission",
    label="Safe-haven / news transmission",
    evidence_class=DriverEvidenceClass.TRANSFORMATION,
    horizon=Horizon.TACTICAL,
    universal_family_overlap="news_geopolitical",
    rationale=(
        "Geopolitical stress and gold-specific flow narratives reach price "
        "through sentiment. This is the same gold-relevant news the News / "
        "Geopolitical family already scores, so it carries no additional vote. "
        "Note that it is sentiment about flows, not a flows dataset."
    ),
    data_sources=(
        "analyze_news_rule_based -> gold_rule_points",
        "shared background AI batch -> gold_ai_points",
    ),
)

DRIVERS: tuple[DriverDefinition, ...] = (
    REAL_RATE_TRANSMISSION,
    USD_TRANSMISSION,
    SAFE_HAVEN_TRANSMISSION,
)

#: Documented, not computed. No data for these exists in this project and no
#: proxy is substituted.
DORMANT_DRIVERS: tuple[str, ...] = (
    "official_sector_demand",
    "gold_etf_flows",
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
    real_yield_mtf: Mapping[str, Any] | None = None,
    usd_macro_score: float | None = None,
    gold_rule_points: float | None = None,
    gold_ai_points: float | None = None,
) -> dict[str, float | None]:
    """Map production readings onto the three Gold channels.

    Each returns None -- Unavailable -- when its source did not arrive. Nothing
    is defaulted to zero, which downstream would read as a genuinely quiet
    channel rather than an unmeasured one.
    """
    real_yield_score = None
    if isinstance(real_yield_mtf, Mapping):
        real_yield_score = _numeric(real_yield_mtf.get("score"))
    real_rate = None if real_yield_score is None else -real_yield_score

    usd = _numeric(usd_macro_score)
    usd_channel = None if usd is None else -usd

    rule = _numeric(gold_rule_points)
    ai = _numeric(gold_ai_points)
    news_parts = [p for p in (rule, ai) if p is not None]
    news_channel = (
        sum(news_parts) / len(news_parts) / NEWS_POINTS_SCALE if news_parts else None
    )
    if news_channel is not None:
        news_channel = max(-1.0, min(1.0, news_channel))

    return {
        "real_rate_transmission": real_rate,
        "usd_transmission": usd_channel,
        "safe_haven_news_transmission": news_channel,
    }


def evaluate(
    *,
    thesis_direction: Direction,
    horizon: Horizon = Horizon.TACTICAL,
    real_yield_mtf: Mapping[str, Any] | None = None,
    usd_macro_score: float | None = None,
    gold_rule_points: float | None = None,
    gold_ai_points: float | None = None,
) -> AssetModuleReading:
    """Evaluate the Gold transmission module. Adds no evidence to the core."""
    return build_module_reading(
        module=MODULE_KEY,
        instrument=INSTRUMENT,
        horizon=horizon,
        thesis_direction=thesis_direction,
        definitions=DRIVERS,
        values=driver_values(
            real_yield_mtf=real_yield_mtf,
            usd_macro_score=usd_macro_score,
            gold_rule_points=gold_rule_points,
            gold_ai_points=gold_ai_points,
        ),
        dormant_drivers=DORMANT_DRIVERS,
    )


#: The transmission chain this module claims, for pre-registered predictions.
TRANSMISSION_CHAIN: tuple[tuple[str, str, str], ...] = (
    ("real_yields", "usd", "Real-rate moves reprice the dollar"),
    ("usd", "gold", "The dollar leg reprices USD-denominated gold"),
)
