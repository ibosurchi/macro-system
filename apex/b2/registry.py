"""Architecture B2 -- the single, frozen registry of voting families.

This module is the ONE place where the voting budget lives. If a family is not
declared here it cannot vote, because every aggregation path in this package
takes its family definitions from ``VOTING_FAMILIES``.

Three registries are maintained, and they are deliberately disjoint:

``VOTING_FAMILIES``
    The fixed voting core. Adding a family here is a budget event and requires
    written justification in the definition itself.

``DORMANT_COMPONENTS``
    Documented, not computed, because the data to support them does not exist
    in this project. They are declared rather than omitted so that their
    absence is *visible* -- an unavailable family reduces Data Confidence
    instead of silently vanishing.

``WITHHELD_COMPONENTS``
    Code for these exists in the project but must NOT be activated yet, for a
    recorded safety reason. This encodes the operator's Stage A safety
    requirements structurally rather than as a comment someone can forget.

The budget of ~6 is an architectural complexity constraint, not a proven
number. Its purpose is to stop factor proliferation: adding an indicator to an
existing family is routine, adding a family is not.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from .enums import Horizon, Role

#: Maximum number of independent voting families across the entire architecture.
VOTING_BUDGET = 6


@dataclass(frozen=True)
class FamilyDefinition:
    """A voting family. Membership is frozen and must not be tuned against results."""

    key: str
    label: str
    role: Role
    horizon: Horizon
    members: tuple[str, ...]
    justification: str
    data_sources: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.role is not Role.ACTIVE_VOTING:
            raise ValueError(f"{self.key}: a voting family must have role ACTIVE_VOTING")
        if not self.members:
            raise ValueError(f"{self.key}: a voting family must declare at least one member")
        if len(set(self.members)) != len(self.members):
            raise ValueError(f"{self.key}: duplicate member keys")


@dataclass(frozen=True)
class InactiveComponent:
    """A component that is declared but not computed."""

    key: str
    label: str
    role: Role
    reason: str
    blocking_requirement: str = ""


@dataclass(frozen=True)
class AssetModuleComponent:
    """An asset-specific transmission module.

    Registered so its role is explicit and enforceable. An asset module is
    ACTIVE NON-VOTING: it computes and conditions interpretation but never
    contributes independent directional evidence, and it does not receive
    voting power until incremental value over the universal core alone has been
    demonstrated -- which no validation has yet done.
    """

    key: str
    label: str
    instrument: str
    role: Role
    rationale: str

    def __post_init__(self) -> None:
        if self.role is not Role.ASSET_SPECIFIC_MODULE:
            raise ValueError(
                f"{self.key}: an asset module must have role ASSET_SPECIFIC_MODULE"
            )


# ---------------------------------------------------------------------------
# THE VOTING CORE -- 5 families, within the budget of ~6.
#
# Only Policy / Real Rates maps onto the canonical B2 macro family list; the
# other three canonical macro families (Liquidity / Funding, Positioning /
# Crowding, Fiscal / Issuance) have no data in this project and are declared
# dormant below rather than faked. Macro Activity and News / Geopolitical are
# declared here because they are the evidence that actually drives every live
# score in this system -- excluding them would produce a voting core that does
# not describe the running platform.
# ---------------------------------------------------------------------------

POLICY_REAL_RATES = FamilyDefinition(
    key="policy_real_rates",
    label="Policy / Real Rates",
    role=Role.ACTIVE_VOTING,
    horizon=Horizon.TACTICAL,
    members=(
        "policy_rate_momentum",
        "real_yield_momentum",
        "nominal_yield_momentum",
        "inflation_expectations_momentum",
    ),
    justification=(
        "Canonical B2 macro family. Central-bank reaction function, policy "
        "expectations and the real-rate environment. These four members are "
        "one causal source read four ways, so they raise family strength and "
        "never the contribution count."
    ),
    data_sources=(
        "CURRENCY_SERIES indicators with cat='rate' (FEDFUNDS, ECBDFR, BOERUKM, ...)",
        "GOLD_SERIES real_yield (DFII10)",
        "GOLD_SERIES yield (DGS10)",
        "GOLD_SERIES inflation_exp (T10YIE)",
    ),
)

MACRO_ACTIVITY = FamilyDefinition(
    key="macro_activity",
    label="Macro Activity (Inflation / Labour / Growth)",
    role=Role.ACTIVE_VOTING,
    horizon=Horizon.TACTICAL,
    members=(
        "inflation_momentum",
        "labor_momentum",
        "growth_momentum",
    ),
    justification=(
        "BUDGET EVENT -- justified in writing, approved before implementation. "
        "This is not a new invention: it is the dominant content of the "
        "existing per-currency macro_score. It is economically distinct from "
        "Policy / Real Rates, which prices the reaction function, whereas this "
        "family measures the data flow that function responds to. Splitting "
        "inflation, labour and growth into three families would be the double "
        "count; holding them as one family whose internal agreement raises "
        "strength only is the anti-double-counting treatment."
    ),
    data_sources=(
        "CURRENCY_SERIES indicators with cat in {'inflation','labor_pos','labor_neg','growth'}",
    ),
)

NEWS_GEOPOLITICAL = FamilyDefinition(
    key="news_geopolitical",
    label="News / Geopolitical",
    role=Role.ACTIVE_VOTING,
    horizon=Horizon.TACTICAL,
    members=(
        "rule_based_news",
        "ai_news",
    ),
    justification=(
        "BUDGET EVENT -- justified in writing, approved before implementation. "
        "News sentiment is 50% of every currency score, 50% of Gold's base, "
        "40% of Oil's and 25% of Nasdaq's in the live system, and it is "
        "independently sourced (scraped wires / Telegram / RSS) rather than "
        "derived from any FRED series. Its two members read the SAME articles, "
        "so they are correlated by construction -- which is precisely why they "
        "belong in one family rather than voting separately."
    ),
    data_sources=(
        "analyze_news_rule_based -> per-asset rule points",
        "shared background AI batch -> per-asset score x confidence",
    ),
)

DIRECTIONAL = FamilyDefinition(
    key="directional",
    label="Directional (Trend / Momentum / Multi-Timeframe)",
    role=Role.ACTIVE_VOTING,
    horizon=Horizon.EXECUTION,
    members=(
        "short_horizon_return",
        "medium_horizon_return",
        "multi_timeframe_alignment",
    ),
    justification=(
        "Canonical B2 technical family. Trend, momentum and multi-timeframe "
        "alignment agreeing is ONE strong confirmation, not three votes. The "
        "alignment member is derived from the same returns as the other two, "
        "which is exactly the correlation this family structure exists to "
        "absorb."
    ),
    data_sources=("compute_tactical_move -> ret_15m / ret_1h / ret_4h",),
)

STRUCTURE = FamilyDefinition(
    key="structure",
    label="Structure (Price Structure / Breakout / Retest)",
    role=Role.ACTIVE_VOTING,
    horizon=Horizon.EXECUTION,
    members=(
        "breakout_quality",
        "price_structure_zone",
        "retest_behaviour",
    ),
    justification=(
        "Canonical B2 technical family. Retest is modelled as a property of a "
        "breakout, not a standalone rule. Two of the three members are "
        "currently Unavailable in this project (see adapters.structure_signals) "
        "and are reported as such rather than defaulted to flat."
    ),
    data_sources=("compute_tactical_move -> structure (breakout/breakdown classification)",),
)

VOTING_FAMILIES: tuple[FamilyDefinition, ...] = (
    POLICY_REAL_RATES,
    MACRO_ACTIVITY,
    NEWS_GEOPOLITICAL,
    DIRECTIONAL,
    STRUCTURE,
)

FAMILIES_BY_KEY: Mapping[str, FamilyDefinition] = {f.key: f for f in VOTING_FAMILIES}

#: Families whose absence should cap total confidence rather than pass quietly.
#: A thesis built with no policy read and no activity read is not a weak thesis,
#: it is an uninformed one.
CRITICAL_FAMILY_KEYS: frozenset[str] = frozenset({"policy_real_rates", "macro_activity"})

MACRO_FAMILY_KEYS: frozenset[str] = frozenset(
    f.key for f in VOTING_FAMILIES if f.horizon is Horizon.TACTICAL
)
TECHNICAL_FAMILY_KEYS: frozenset[str] = frozenset(
    f.key for f in VOTING_FAMILIES if f.horizon is Horizon.EXECUTION
)


# ---------------------------------------------------------------------------
# DORMANT -- documented, not computed, because the data does not exist here.
# Verified by exhaustive search of the project during Phase 1-3 inspection.
# ---------------------------------------------------------------------------

DORMANT_COMPONENTS: tuple[InactiveComponent, ...] = (
    InactiveComponent(
        key="liquidity_funding",
        label="Liquidity / Funding",
        role=Role.DORMANT,
        reason=(
            "No liquidity or funding data exists in this project. Searched and "
            "found nothing for SOFR, repo, OIS, TED, WALCL, RRPONTSYD, NFCI, "
            "STLFSI or credit spreads."
        ),
        blocking_requirement="A funding/liquidity data source must be added and verified.",
    ),
    InactiveComponent(
        key="positioning_crowding",
        label="Positioning / Crowding",
        role=Role.DORMANT,
        reason="No COT, open interest or positioning feed exists in this project.",
        blocking_requirement="A legitimate positioning data source must be added.",
    ),
    InactiveComponent(
        key="fiscal_issuance",
        label="Fiscal / Issuance",
        role=Role.DORMANT,
        reason="No issuance, refunding or auction data exists in this project.",
        blocking_requirement="A sovereign issuance/refunding data source must be added.",
    ),
    InactiveComponent(
        key="financial_cycle",
        label="Financial Cycle",
        role=Role.DORMANT,
        reason=(
            "No credit-to-GDP, system leverage or property-price series exists. "
            "Note that even with data this would be a structural prior that is "
            "unlikely to be validated in any realistic shadow window -- and "
            "'never disproved' must not later be read as 'validated'."
        ),
        blocking_requirement="Quarterly structural leverage data with publication-lag handling.",
    ),
    InactiveComponent(
        key="sovereign_refinancing",
        label="Sovereign Debt / Refinancing",
        role=Role.DORMANT,
        reason=(
            "No sovereign maturity schedule data exists. It also fails the "
            "'what decision does it change?' test in this project as it stands, "
            "so it is not built."
        ),
        blocking_requirement="Sovereign maturity/refunding calendar; and it belongs INSIDE "
                             "Fiscal / Issuance, never as a parallel family.",
    ),
    InactiveComponent(
        key="corporate_maturity_wall",
        label="Corporate Maturity Wall",
        role=Role.DORMANT,
        reason="No corporate refinancing data exists and none may be invented.",
        blocking_requirement="A reliable corporate maturity dataset.",
    ),
    InactiveComponent(
        key="systemic_risk_buildup",
        label="Systemic Risk Build-up",
        role=Role.DORMANT,
        reason=(
            "A derived output whose inputs are themselves dormant. Four of its "
            "five vulnerability inputs (structural vulnerability, funding "
            "stress, refinancing pressure, leverage excess) have no data, so "
            "it cannot be honestly derived from co-occurrence."
        ),
        blocking_requirement="At least the funding and leverage inputs must become available.",
    ),
    InactiveComponent(
        key="order_flow_market_depth",
        label="Order Flow / Market Depth",
        role=Role.DORMANT,
        reason=(
            "No institutional order flow or depth data exists. Spot FX/XAUUSD "
            "has no consolidated order book. The project does fetch a 'volume' "
            "column from the Yahoo chart endpoint but never uses it in any "
            "calculation, and it must never be labelled institutional flow."
        ),
        blocking_requirement="Genuine futures volume/open interest or exchange depth data.",
    ),
    InactiveComponent(
        key="execution_cost_spread",
        label="Spread / Execution Cost",
        role=Role.DORMANT,
        reason=(
            "The 5-minute Yahoo bars carry no bid/ask, so no real spread or "
            "execution cost is observable. Honestly-labelled proxies (realized "
            "volatility, session window) remain permitted and are not this."
        ),
        blocking_requirement="A broker/venue quote feed with recorded historical spreads.",
    ),
    InactiveComponent(
        key="gold_official_sector_demand",
        label="Gold Official-Sector Demand",
        role=Role.DORMANT,
        reason="Only news keyword proxies exist ('central bank buying'); that is not data.",
        blocking_requirement="An official-sector reserves dataset.",
    ),
    InactiveComponent(
        key="gold_etf_flows",
        label="Gold ETF Flows",
        role=Role.DORMANT,
        reason="Only news keyword proxies exist ('gold etf inflow'); that is not data.",
        blocking_requirement="An ETF holdings/flows dataset.",
    ),
    InactiveComponent(
        key="oil_inventories",
        label="Oil Inventories",
        role=Role.DORMANT,
        reason="Only news keyword proxies exist ('inventory draw'); that is not data.",
        blocking_requirement="An EIA/API inventory series.",
    ),
    InactiveComponent(
        key="opec_supply",
        label="OPEC / Supply Conditions",
        role=Role.DORMANT,
        reason="Only news keyword proxies exist ('supply cut'); that is not data.",
        blocking_requirement="A production/quota dataset.",
    ),
    InactiveComponent(
        key="crude_term_structure",
        label="Crude Term Structure",
        role=Role.DORMANT,
        reason=(
            "The project holds spot crude series only (DCOILWTICO/DCOILBRENTEU). "
            "There is no futures curve, so backwardation and contango -- the "
            "market's own read on physical tightness -- cannot be observed."
        ),
        blocking_requirement="A futures curve or at least a front/second-month spread.",
    ),
    InactiveComponent(
        key="refinery_and_shipping",
        label="Refinery Runs / Shipping Flows",
        role=Role.DORMANT,
        reason="No refinery utilisation, crack spread or freight data exists.",
        blocking_requirement="A refinery/freight dataset.",
    ),
    InactiveComponent(
        key="nasdaq_earnings_revisions",
        label="Nasdaq Earnings / Estimate Revisions",
        role=Role.DORMANT,
        reason=(
            "No earnings, estimate or revision data exists. News coverage of "
            "results is sentiment about earnings, not an earnings dataset, and "
            "is not promoted into this role."
        ),
        blocking_requirement="An estimates/revisions dataset.",
    ),
    InactiveComponent(
        key="equity_liquidity_flows",
        label="Equity Liquidity / Fund Flows",
        role=Role.DORMANT,
        reason="No fund-flow or equity-liquidity series exists in this project.",
        blocking_requirement="A flows dataset.",
    ),
    InactiveComponent(
        key="index_breadth_concentration",
        label="Index Breadth / Concentration",
        role=Role.DORMANT,
        reason=(
            "Only the index level (NASDAQ100) is available. Breadth and megacap "
            "concentration need constituent data, which this project has none of."
        ),
        blocking_requirement="Constituent-level index data.",
    ),
    InactiveComponent(
        key="central_bank_intervention",
        label="FX Intervention",
        role=Role.DORMANT,
        reason="No intervention or reserve-operation data exists.",
        blocking_requirement="An official intervention/reserves dataset.",
    ),
    InactiveComponent(
        key="cross_currency_basis",
        label="Cross-Currency Basis",
        role=Role.DORMANT,
        reason=(
            "No basis-swap data exists. This is funding-market plumbing, which "
            "remains unsupported across the whole architecture."
        ),
        blocking_requirement="A cross-currency basis series.",
    ),
    InactiveComponent(
        key="vintage_macro_data",
        label="Vintage / Revision-Aware Macro Data",
        role=Role.DORMANT,
        reason=(
            "fetch_fred reads the current-vintage FRED endpoint, not ALFRED. "
            "Current values are not what was known historically, so any "
            "historical test built on them carries a revision-leakage bias. "
            "This limitation is declared here so it is visible in output rather "
            "than assumed away."
        ),
        blocking_requirement="ALFRED-style vintage retrieval.",
    ),
)


# ---------------------------------------------------------------------------
# WITHHELD -- code exists in the project, activation is blocked for a reason.
# ---------------------------------------------------------------------------

WITHHELD_COMPONENTS: tuple[InactiveComponent, ...] = (
    InactiveComponent(
        key="cross_asset_bridge",
        label="Cross-Asset Bridge",
        role=Role.ACTIVE_NON_VOTING,
        reason=(
            "compute_cross_asset_confirmation exists but all six of its "
            "relationships confirm a thesis using one of that thesis's own "
            "inputs (real yields are 30% of Gold's score, USD macro is 20% of "
            "Gold's and Oil's and 15% of Nasdaq's, oil momentum is 15% of "
            "CAD's, the JPY rate leg is inside JPY's own composite). "
            "Activating it would manufacture confirmation out of re-measured "
            "inputs."
        ),
        blocking_requirement=(
            "A thesis-input registry that programmatically excludes any variable "
            "used to build the thesis from its confirmation set, plus "
            "Stable/Weakening/Broken/Divergence relationship stability with "
            "Broken mapping to Unavailable rather than 'no confirmation'."
        ),
    ),
    InactiveComponent(
        key="relative_value_layer",
        label="FX Relative Value",
        role=Role.ASSET_SPECIFIC_MODULE,
        reason=(
            "compute_relative_value is well-formed and correctly non-voting, "
            "but nothing calls it and nothing logs it, so it is not an "
            "implemented feature. It must not be counted as one."
        ),
        blocking_requirement="Shadow-mode logging (Stage B) plus forward validation.",
    ),
    InactiveComponent(
        key="macro_regime_context",
        label="Macro Regime Context",
        role=Role.META_STATE,
        reason=(
            "compute_macro_regime_context exists but is called by nothing, and "
            "its internal vote counting double-counts real-yield momentum "
            "(directly, and again inside the gold score it also counts). Its "
            "risk_on/risk_off states are also not the Trending/Range/Stress "
            "meta-state B2 specifies."
        ),
        blocking_requirement="Regime State + separate Regime Confidence, without double counting.",
    ),
    InactiveComponent(
        key="recent_macro_surprise",
        label="Recent Macro Surprise Bridge",
        role=Role.DIAGNOSTIC,
        reason="compute_recent_macro_surprise exists but is called by nothing and logged nowhere.",
        blocking_requirement="Shadow-mode logging (Stage B) plus forward validation.",
    ),
    InactiveComponent(
        key="macro_thesis_invalidation",
        label="Macro Thesis Invalidation + Escalation",
        role=Role.ACTIVE_NON_VOTING,
        reason="Not implemented. Stage A carries the ThesisState enum but computes no transitions.",
        blocking_requirement=(
            "Stage B: the repeated / broad / unexplained escalation rule, which "
            "needs the cross-asset bridge and a failure history to exist first."
        ),
    ),
    InactiveComponent(
        key="false_signal_whipsaw_detection",
        label="False Signal / Whipsaw Detection",
        role=Role.ACTIVE_NON_VOTING,
        reason=(
            "Not implemented anywhere in this project. It is a pre-entry setup "
            "credibility test ('is this apparent setup likely to be noise?') and "
            "must stay separate from Technical Invalidation, which is a "
            "post-setup failure condition ('what proves this setup is dead?'). "
            "They are different functions at different points in the trade "
            "lifecycle and are not merged here."
        ),
        blocking_requirement="A noise/whipsaw model with its own validation.",
    ),
    InactiveComponent(
        key="regime_confidence",
        label="Regime Confidence",
        role=Role.META_STATE,
        reason="Not implemented. No regime confidence value exists to cap against.",
        blocking_requirement="Stage B regime meta-state.",
    ),
    InactiveComponent(
        key="gold_pricing_matrix_display_rows",
        label="Gold Pricing Matrix display rows",
        role=Role.DIAGNOSTIC,
        reason=(
            "The Gold page's pricing matrix contains hard-coded display values "
            "for the DGS10 and T10YIE rows. They are presentation placeholders, "
            "not measurements, and must never be read as evidence by any "
            "scoring logic."
        ),
        blocking_requirement="Never. These are display artefacts; B2 must read the source series.",
    ),
)


# ---------------------------------------------------------------------------
# ASSET-SPECIFIC MODULES -- active, non-voting transmission diagnostics.
#
# Every driver in every one of these modules is a TRANSFORMATION of evidence
# that already votes in the universal core, or is dormant for want of data. The
# modules therefore add zero votes and the voting budget is untouched. That is
# the intended outcome: the evidence that would be genuinely independent for
# these instruments (official-sector demand, inventories, positioning,
# earnings) is exactly the data this project does not have.
# ---------------------------------------------------------------------------

ASSET_MODULES: tuple[AssetModuleComponent, ...] = (
    AssetModuleComponent(
        key="gold_module_v1",
        label="Gold transmission module",
        instrument="Gold",
        role=Role.ASSET_SPECIFIC_MODULE,
        rationale=(
            "Three channels -- real-rate, USD and safe-haven/news -- each a "
            "restatement of evidence already voting in Policy / Real Rates, "
            "Macro Activity and News / Geopolitical respectively. Diagnostic "
            "only: it reports whether each channel is transmitting the thesis, "
            "and never adds a vote."
        ),
    ),
    AssetModuleComponent(
        key="oil_module_v1",
        label="Oil transmission module",
        instrument="Oil",
        role=Role.ASSET_SPECIFIC_MODULE,
        rationale=(
            "Three channels -- price/trend, USD and supply narrative -- "
            "restating Directional, Macro Activity and News / Geopolitical "
            "respectively. The supply channel is explicitly a narrative read: "
            "physical inventories, OPEC output, refinery/shipping and term "
            "structure have no data here and stay dormant rather than proxied."
        ),
    ),
    AssetModuleComponent(
        key="fx_module_v1",
        label="FX transmission module",
        instrument="USD,EUR,GBP,CAD,JPY,CHF,AUD,NZD",
        role=Role.ASSET_SPECIFIC_MODULE,
        rationale=(
            "Supplies the relative read the universal core lacks, since "
            "production scores each currency in isolation. Relative macro and "
            "relative policy are differences of quantities that already vote, "
            "and news is the domestic leg only. Exactly one counter currency "
            "per instrument, so one body of evidence cannot surface repeatedly "
            "across several comparisons."
        ),
    ),
    AssetModuleComponent(
        key="nasdaq_module_v1",
        label="Nasdaq transmission module",
        instrument="NDX",
        role=Role.ASSET_SPECIFIC_MODULE,
        rationale=(
            "Three channels -- real-yield duration sensitivity, USD financial "
            "conditions and growth-risk news -- restating Policy / Real Rates, "
            "Macro Activity and News / Geopolitical. Price trend is "
            "deliberately excluded because it already votes through the "
            "Directional family; earnings revisions, liquidity flows and index "
            "breadth are dormant."
        ),
    ),
)


def _validate_registry() -> None:
    """Enforce the budget and the anti-double-counting invariants at import time."""
    if len(VOTING_FAMILIES) > VOTING_BUDGET:
        raise ValueError(
            f"Voting budget exceeded: {len(VOTING_FAMILIES)} families declared, "
            f"budget is {VOTING_BUDGET}. Adding a family must displace one."
        )

    keys = [f.key for f in VOTING_FAMILIES]
    if len(set(keys)) != len(keys):
        raise ValueError("Duplicate voting family keys")

    # A member may belong to exactly one family. This is the structural guard
    # against the same evidence being counted twice under two labels.
    seen: dict[str, str] = {}
    for family in VOTING_FAMILIES:
        for member in family.members:
            if member in seen:
                raise ValueError(
                    f"Member {member!r} appears in both {seen[member]!r} and "
                    f"{family.key!r}; a member may belong to exactly one family."
                )
            seen[member] = family.key

    inactive_keys = [c.key for c in DORMANT_COMPONENTS + WITHHELD_COMPONENTS]
    if len(set(inactive_keys)) != len(inactive_keys):
        raise ValueError("Duplicate inactive component keys")

    overlap = set(inactive_keys) & set(keys)
    if overlap:
        raise ValueError(f"Components cannot be both voting and inactive: {sorted(overlap)}")

    for component in DORMANT_COMPONENTS:
        if component.role is not Role.DORMANT:
            raise ValueError(f"{component.key}: dormant components must have role DORMANT")

    if not CRITICAL_FAMILY_KEYS <= set(keys):
        raise ValueError("CRITICAL_FAMILY_KEYS references an undeclared family")

    module_keys = [m.key for m in ASSET_MODULES]
    if len(set(module_keys)) != len(module_keys):
        raise ValueError("Duplicate asset module keys")
    # An asset module must never share an identity with a voting family or with
    # an inactive component: they are different roles with different rules.
    clash = set(module_keys) & (set(keys) | set(inactive_keys))
    if clash:
        raise ValueError(f"Asset modules clash with existing components: {sorted(clash)}")


_validate_registry()


def voting_family_keys() -> tuple[str, ...]:
    return tuple(f.key for f in VOTING_FAMILIES)


def dormant_keys() -> tuple[str, ...]:
    return tuple(c.key for c in DORMANT_COMPONENTS)


def asset_module_keys() -> tuple[str, ...]:
    return tuple(m.key for m in ASSET_MODULES)


def withheld_keys() -> tuple[str, ...]:
    return tuple(c.key for c in WITHHELD_COMPONENTS)


def describe_budget() -> dict[str, object]:
    """Auditable summary of the voting budget and what is deliberately excluded."""
    return {
        "budget": VOTING_BUDGET,
        "declared": len(VOTING_FAMILIES),
        "remaining": VOTING_BUDGET - len(VOTING_FAMILIES),
        "voting": list(voting_family_keys()),
        "macro": sorted(MACRO_FAMILY_KEYS),
        "technical": sorted(TECHNICAL_FAMILY_KEYS),
        "critical": sorted(CRITICAL_FAMILY_KEYS),
        "dormant": list(dormant_keys()),
        "withheld": list(withheld_keys()),
        # Asset modules are listed separately from the voting core precisely
        # because they add no votes to it.
        "asset_modules": list(asset_module_keys()),
    }
