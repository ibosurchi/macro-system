"""Architecture B2 -- FX asset module (transmission diagnostic).

FX is relative. "Is EUR improving" is a different question from "is EUR
improving against USD", and the production engine scores each currency in
isolation, so this module supplies the relative read the universal core cannot.

Three channels:

    A. Relative macro pressure   (domestic macro minus counter macro)
    B. Relative policy pressure  (domestic policy rate minus counter policy rate)
    C. Domestic news transmission

All three are TRANSFORMATIONS. A difference of two quantities that already vote
is still those same quantities: differencing changes the framing, not the
independence. The module therefore adds no voting power.

**One counter-currency per instrument, deliberately.** The same domestic macro
evidence appears in every comparison a currency could take part in, so running
several comparisons would surface one body of evidence repeatedly under
different names. Each currency is therefore compared against exactly one
counter, and that counter is recorded on the reading.

**USD is the base.** It has no counter here, so its relative channels report
Unavailable rather than being compared against an arbitrary substitute.

**JPY** uses the US 10-year yield in place of a matching long bond, because
that is the data this project actually has: JPY's own configured rate is a
short policy rate, no JPY long bond series is available, and USD/JPY tracks US
long-yield pressure largely independently of the BOJ's near-zero short rate.
The substitution is recorded in the reading's notes rather than hidden.
"""
from __future__ import annotations

from typing import Any

from ..enums import Direction, Horizon
from .base import (
    AssetModuleReading,
    DriverDefinition,
    DriverEvidenceClass,
    build_module_reading,
    validate_definitions,
)

MODULE_KEY = "fx_module_v1"

NEWS_POINTS_SCALE = 0.50

#: Currencies this module serves. Kept in step with the production
#: CURRENCY_SERIES configuration; a bridge test asserts the two agree so this
#: list cannot silently drift.
INSTRUMENTS: tuple[str, ...] = (
    "USD",
    "EUR",
    "GBP",
    "CAD",
    "JPY",
    "CHF",
    "AUD",
    "NZD",
)

#: Exactly one counter per currency. USD is the base and has none.
COUNTER_CURRENCY: dict[str, str | None] = {
    "USD": None,
    "EUR": "USD",
    "GBP": "USD",
    "CAD": "USD",
    "JPY": "USD",
    "CHF": "USD",
    "AUD": "USD",
    "NZD": "USD",
}


def counter_currency_for(currency: str) -> str | None:
    return COUNTER_CURRENCY.get(str(currency or "").strip().upper())


RELATIVE_MACRO = DriverDefinition(
    key="relative_macro_pressure",
    label="Relative macro pressure",
    evidence_class=DriverEvidenceClass.TRANSFORMATION,
    horizon=Horizon.TACTICAL,
    universal_family_overlap="macro_activity",
    rationale=(
        "An exchange rate prices one economy against another, so the relevant "
        "quantity is the difference in macro momentum, not either level. Both "
        "legs are composite macro scores that already vote through Macro "
        "Activity, and differencing two voting inputs does not create an "
        "independent third one -- it reframes them."
    ),
    data_sources=(
        "compute_composite(<currency>).macro_score",
        "compute_composite(<counter>).macro_score",
    ),
)

RELATIVE_POLICY = DriverDefinition(
    key="relative_policy_pressure",
    label="Relative policy / rate pressure",
    evidence_class=DriverEvidenceClass.TRANSFORMATION,
    horizon=Horizon.TACTICAL,
    universal_family_overlap="policy_real_rates",
    rationale=(
        "Rate differentials, not absolute rates, drive carry and capital flow. "
        "Both legs are the rate-category readings that already vote through "
        "Policy / Real Rates, so this is a restatement of existing evidence "
        "rather than a new source."
    ),
    data_sources=(
        "compute_composite(<currency>).rows[cat='rate']",
        "compute_composite(<counter>).rows[cat='rate']",
        "GOLD_SERIES yield (DGS10) for the JPY long-yield substitution",
    ),
)

DOMESTIC_NEWS = DriverDefinition(
    key="domestic_news_transmission",
    label="Domestic news transmission",
    evidence_class=DriverEvidenceClass.TRANSFORMATION,
    horizon=Horizon.TACTICAL,
    universal_family_overlap="news_geopolitical",
    rationale=(
        "Currency-specific headlines reach the rate through sentiment, which "
        "the News / Geopolitical family already scores for this currency. It "
        "is deliberately the domestic leg only: differencing sentiment across "
        "two currencies would double-count the same global stories, which are "
        "frequently relevant to both."
    ),
    data_sources=("analyze_news_rule_based -> scores[<currency>]",),
)

DRIVERS: tuple[DriverDefinition, ...] = (
    RELATIVE_MACRO,
    RELATIVE_POLICY,
    DOMESTIC_NEWS,
)

#: Documented, not computed.
DORMANT_DRIVERS: tuple[str, ...] = (
    "fx_positioning_crowding",
    "central_bank_intervention",
    "cross_currency_basis",
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


def _difference(own: Any, counter: Any) -> float | None:
    """Clamped difference, or None when either leg is missing."""
    a = _numeric(own)
    b = _numeric(counter)
    if a is None or b is None:
        return None
    return max(-1.0, min(1.0, a - b))


def driver_values(
    *,
    currency: str,
    domestic_macro_score: float | None = None,
    counter_macro_score: float | None = None,
    domestic_rate_score: float | None = None,
    counter_rate_score: float | None = None,
    domestic_news_points: float | None = None,
) -> dict[str, float | None]:
    """Map production readings onto the three FX channels.

    A missing counter leg -- USD, or a counter composite that failed -- yields
    None for the relative channels. They are Unavailable, not neutral: an
    uncompared currency is not a balanced one.
    """
    news = _numeric(domestic_news_points)
    return {
        "relative_macro_pressure": _difference(domestic_macro_score, counter_macro_score),
        "relative_policy_pressure": _difference(domestic_rate_score, counter_rate_score),
        "domestic_news_transmission": (
            None if news is None else max(-1.0, min(1.0, news / NEWS_POINTS_SCALE))
        ),
    }


def evaluate(
    *,
    thesis_direction: Direction,
    currency: str,
    horizon: Horizon = Horizon.TACTICAL,
    domestic_macro_score: float | None = None,
    counter_macro_score: float | None = None,
    domestic_rate_score: float | None = None,
    counter_rate_score: float | None = None,
    domestic_news_points: float | None = None,
    counter_rate_substitution: str = "",
) -> AssetModuleReading:
    """Evaluate the FX transmission module for one currency."""
    code = str(currency or "").strip().upper()
    counter = counter_currency_for(code)

    notes: list[str] = []
    if counter is None:
        notes.append(
            f"{code} is the base currency of this comparison set and has no "
            "counter here, so the relative channels are Unavailable rather than "
            "compared against a substitute."
        )
    else:
        notes.append(f"Compared against exactly one counter currency: {counter}.")
        notes.append(
            "One comparison only, by design: the same domestic evidence would "
            "otherwise appear in several comparisons under different names."
        )
    if counter_rate_substitution:
        notes.append(counter_rate_substitution)

    return build_module_reading(
        module=MODULE_KEY,
        instrument=code,
        horizon=horizon,
        thesis_direction=thesis_direction,
        definitions=DRIVERS,
        values=driver_values(
            currency=code,
            domestic_macro_score=domestic_macro_score,
            counter_macro_score=counter_macro_score,
            domestic_rate_score=domestic_rate_score,
            counter_rate_score=counter_rate_score,
            domestic_news_points=domestic_news_points,
        ),
        dormant_drivers=DORMANT_DRIVERS,
        notes=tuple(notes),
    )


TRANSMISSION_CHAIN: tuple[tuple[str, str, str], ...] = (
    ("relative_policy", "fx_rate", "Rate differentials drive carry and flow"),
    ("relative_macro", "fx_rate", "Relative growth and inflation reprice the pair"),
)
