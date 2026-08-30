"""Bridge between Architecture B2 and the live ApexMacro production core.

This module deliberately lives OUTSIDE ``apex.b2``. Everything under that
package is pure and performs no I/O; this file is the single place allowed to
touch ``production_core``, and keeping it separate is what preserves that
guarantee.

**Nothing in the production system calls this module.** B2 remains in shadow
mode: no page, score, alert, scheduler or Telegram path imports it, so importing
it changes no production behaviour. Wiring an actual call site is a separate,
explicitly approved step.

Persistence reuses the existing Supabase-first / atomic-local-mirror layer under
two NEW state ids. The backing table is a generic key/value store keyed by id,
so adding ids requires no schema change and no migration of existing rows.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from . import production_core as core
from .b2 import adapters
from .b2.enums import Horizon
from .b2.evaluate import ShadowEvaluation, run_shadow_evaluation, thesis_input_keys
from .b2.predictions import PredictionLog
from .b2.shadow import ShadowLog, ShadowLogError

#: New state ids. Existing ids and payload shapes are untouched.
SHADOW_LOG_STATE_ID = "b2_shadow_log_v1"
PREDICTION_LOG_STATE_ID = "b2_prediction_log_v1"

SHADOW_LOG_FILE = str(core.PROJECT_ROOT / "b2_shadow_log_v1.json")
PREDICTION_LOG_FILE = str(core.PROJECT_ROOT / "b2_prediction_log_v1.json")


class ProductionShadowStore:
    """ShadowStore backed by the existing persistence layer.

    Satisfies ``apex.b2.shadow.ShadowStore`` structurally. Uses the same
    Supabase-first read and atomic local mirror every other durable state in the
    project uses, so shadow records survive a redeploy exactly as the VIP
    registry and forecaster history do.
    """

    _PATHS = {
        SHADOW_LOG_STATE_ID: SHADOW_LOG_FILE,
        PREDICTION_LOG_STATE_ID: PREDICTION_LOG_FILE,
    }

    def _path_for(self, state_id: str) -> str:
        try:
            return self._PATHS[state_id]
        except KeyError:
            raise ValueError(f"Unknown B2 state id {state_id!r}") from None

    def load(self, state_id: str, default: object) -> object:
        return core._load_persistent_state(state_id, self._path_for(state_id), default)

    def save(self, state_id: str, payload: object) -> None:
        core._save_persistent_state(state_id, self._path_for(state_id), payload)


def load_shadow_log(store: Any) -> ShadowLog:
    return ShadowLog.from_record(store.load(SHADOW_LOG_STATE_ID, {"records": []}))


def save_shadow_log(store: Any, log: ShadowLog) -> None:
    store.save(SHADOW_LOG_STATE_ID, log.as_record())


def load_prediction_log(store: Any) -> PredictionLog:
    return PredictionLog.from_record(
        store.load(PREDICTION_LOG_STATE_ID, {"predictions": [], "outcomes": []})
    )


def save_prediction_log(store: Any, log: PredictionLog) -> None:
    store.save(PREDICTION_LOG_STATE_ID, log.as_record())


def signals_from_production(
    *,
    composite: Mapping[str, Any] | None = None,
    real_yield_mtf: Mapping[str, Any] | None = None,
    nominal_yield_mtf: Mapping[str, Any] | None = None,
    inflation_expectations_mtf: Mapping[str, Any] | None = None,
    rule_points: float | None = None,
    ai_points: float | None = None,
    tactical: Mapping[str, Any] | None = None,
) -> dict[str, dict[str, float | None]]:
    """Translate live production outputs into B2 member signals.

    ``composite`` is a ``compute_composite(...)`` result and ``tactical`` a
    ``compute_tactical_move(...)`` result. The volatility scale is taken from
    the tactical result's own ``volatility_scale`` export, so the returns are
    normalised on exactly the scale that function already used internally
    rather than on a second, independently invented definition.
    """
    rows: Sequence[Mapping[str, Any]] | None = None
    if isinstance(composite, Mapping):
        candidate = composite.get("rows")
        if isinstance(candidate, Sequence):
            rows = candidate

    volatility_scale = None
    if isinstance(tactical, Mapping):
        volatility_scale = tactical.get("volatility_scale")

    return adapters.build_signals(
        composite_rows=rows,
        real_yield_mtf=real_yield_mtf,
        nominal_yield_mtf=nominal_yield_mtf,
        inflation_expectations_mtf=inflation_expectations_mtf,
        rule_points=rule_points,
        ai_points=ai_points,
        tactical=tactical,
        volatility_scale=volatility_scale,
    )


def evaluate_from_production(
    *,
    instrument: str,
    decision_horizon: Horizon = Horizon.TACTICAL,
    composite: Mapping[str, Any] | None = None,
    tactical: Mapping[str, Any] | None = None,
    real_yield_mtf: Mapping[str, Any] | None = None,
    nominal_yield_mtf: Mapping[str, Any] | None = None,
    inflation_expectations_mtf: Mapping[str, Any] | None = None,
    rule_points: float | None = None,
    ai_points: float | None = None,
    prediction_log: PredictionLog | None = None,
    **kwargs: Any,
) -> ShadowEvaluation:
    """Run one shadow evaluation from live production dictionaries.

    Read-only with respect to production: it consumes values the caller already
    computed and mutates nothing. It issues no AI request, opens no thread and
    sends no message.
    """
    entry_plan = None
    if isinstance(tactical, Mapping):
        candidate = tactical.get("entry_plan")
        if isinstance(candidate, Mapping):
            entry_plan = candidate

    execution_inputs = adapters.execution_inputs(entry_plan=entry_plan)
    signals = signals_from_production(
        composite=composite,
        real_yield_mtf=real_yield_mtf,
        nominal_yield_mtf=nominal_yield_mtf,
        inflation_expectations_mtf=inflation_expectations_mtf,
        rule_points=rule_points,
        ai_points=ai_points,
        tactical=tactical,
    )

    merged: dict[str, Any] = {
        "invalidation_level": execution_inputs["invalidation_level"],
        "entry_zone": execution_inputs["entry_zone"],
        "current_price": execution_inputs["current_price"],
        "atr": execution_inputs["atr"],
        "atr_ratio": execution_inputs["atr_ratio"],
        "room_to_opposing_atr": execution_inputs["room_to_opposing_atr"],
        "asymmetry_ratio": execution_inputs["asymmetry_ratio"],
        "volatility_regime": execution_inputs["volatility_regime"],
        "technical_invalidated": execution_inputs["technical_invalidated"],
    }
    merged.update(kwargs)

    return run_shadow_evaluation(
        instrument=instrument,
        decision_horizon=decision_horizon,
        signals_by_family=signals,
        prediction_log=prediction_log,
        **merged,
    )


def record_evaluation(store: Any, evaluation: ShadowEvaluation) -> ShadowLog:
    """Append one evaluation to the durable shadow log and persist it.

    Append-only: an existing record id is never overwritten.
    """
    log = load_shadow_log(store)
    log.append(evaluation.record)
    save_shadow_log(store, log)
    return log


# ===========================================================================
# SHADOW ACTIVATION
#
# The observation driver invoked once per iteration by the existing 60-second
# production daemon loop. It is observational only: production never reads its
# result, it starts no thread, issues no AI request and sends no message.
#
# Cost control and duplicate suppression are the same mechanism. One
# observation is taken per instrument per UTC hour, identified deterministically
# so that a Streamlit rerun, a process restart or a second loop owner within the
# same hour recomputes the same id and is rejected by the append-only log. The
# bucket is checked BEFORE any data is gathered, so on 59 of every 60 iterations
# this does no work at all.
# ===========================================================================

#: One observation per instrument per hour.
OBSERVATION_BUCKET_SECONDS = 3600

#: Counter names, all of which are required observability for the hook.
HOOK_COUNTERS = (
    "attempted",
    "written",
    "duplicate_skipped",
    "insufficient_data_skipped",
    "exception_swallowed",
    "disabled",
    "unknown_instrument",
)

#: In-process counters for the running daemon. Mirrored into the shadow log's
#: own payload so they survive a restart. Never surfaced as a Telegram message
#: and never rendered as user-facing UI.
HOOK_STATS: dict[str, int] = {name: 0 for name in HOOK_COUNTERS}

#: Buckets already handled in this process, so the common case costs no store
#: read at all. Cleared naturally by a restart, after which one read re-syncs it.
_HANDLED_BUCKETS: dict[str, int] = {}


def _bump(counter: str) -> None:
    HOOK_STATS[counter] = HOOK_STATS.get(counter, 0) + 1


def shadow_enabled() -> bool:
    """Operator switch. Defaults on; set B2_SHADOW_ENABLED=0 to disable."""
    return str(core.get_secret("B2_SHADOW_ENABLED", "1")).strip().lower() not in {
        "0",
        "false",
        "off",
        "no",
    }


def shadow_instruments() -> tuple[str, ...]:
    """Instruments to observe. Deliberately one by default.

    Widening this multiplies the per-hour cost, so it is an explicit operator
    decision rather than a default.
    """
    raw = str(core.get_secret("B2_SHADOW_INSTRUMENTS", "Gold"))
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def observation_key(instrument: str, horizon: Horizon, moment: datetime) -> str:
    """Deterministic identity for one instrument-hour observation."""
    bucket = int(moment.timestamp()) // OBSERVATION_BUCKET_SECONDS
    return f"b2obs|{instrument}|{horizon.value}|{bucket}"


def observation_record_id(key: str) -> str:
    """The record id ``build_shadow_record`` will derive from this key."""
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:20]


#: Representative window positions for the event-risk gate, derived from the
#: entry plan's OWN already-computed event tier (10 = clear, 6 = same session,
#: 3 = close proximity, 0 = release window). These are the tier midpoints, not
#: measured times: production exposes the tier, not the minutes, and inventing a
#: precise countdown here would be fabricating precision the system does not
#: have. Recorded in the shadow record so the coarseness is visible.
_EVENT_POINTS_TO_MINUTES: dict[int, float | None] = {
    10: None,
    6: 240.0,
    3: 60.0,
    0: 0.0,
}


def _gather_production_inputs(
    instrument: str, fred_key: str, channel_name: str
) -> dict[str, Any] | None:
    """Read the production values for one instrument. Mutates nothing.

    Every function called here is already cached and already invoked elsewhere
    in the same daemon iteration, so this is a cache read rather than new load.
    Returns None when the instrument is not one production can price.
    """
    if core._tactical_symbol_config(instrument) is None:
        return None

    news = core.analyze_news_rule_based(core.fetch_all_instant_news(channel_name))
    scores = news.get("scores", {}) if isinstance(news, dict) else {}

    rule_points: float | None = None
    ai_points: float | None = None

    if instrument in core.CURRENCY_SERIES:
        macro_score = core._calc_currency_score_only(instrument, fred_key, channel_name)
        composite = core.compute_composite(instrument, fred_key, channel_name)
        rule_points = scores.get(instrument)
    elif instrument == "Gold":
        macro_score, _, _ = core._calc_gold_score_only(fred_key, channel_name)
        composite = core.compute_composite("USD", fred_key, channel_name)
        # Gold is the one asset where production separates the two news legs, so
        # both members of the News family can be read. Elsewhere only the blended
        # figure exists and the AI member stays Unavailable rather than invented.
        rule_points = news.get("gold_rule_points")
        ai_points = news.get("gold_ai_points")
    elif instrument == "Oil":
        macro_score, _ = core._calc_oil_score_only(fred_key, channel_name)
        composite = core.compute_composite("USD", fred_key, channel_name)
        rule_points = scores.get("Oil")
    elif instrument == "NDX":
        macro_score, _ = core._calc_ndx_score_only(fred_key, channel_name)
        composite = core.compute_composite("USD", fred_key, channel_name)
        rule_points = scores.get("Nasdaq")
    else:
        return None

    def _mtf(series_id: str, category: str, tail: int | None = None):
        frame = core.fetch_fred(series_id, fred_key, limit=60)
        if frame is None or frame.empty:
            return None
        values = frame["value"]
        return core.calc_mtf((values.tail(tail) if tail else values).tolist(), category)

    tactical = core.compute_tactical_move(instrument, macro_score)

    return {
        "composite": composite,
        "tactical": tactical,
        "rule_points": rule_points,
        "ai_points": ai_points,
        "real_yield_mtf": _mtf(core.GOLD_SERIES["real_yield"], "rate", 36),
        "nominal_yield_mtf": _mtf(core.GOLD_SERIES["yield"], "rate", 36),
        "inflation_expectations_mtf": _mtf(core.GOLD_SERIES["inflation_exp"], "inflation", 36),
    }


def observe_instrument(
    instrument: str,
    fred_key: str,
    channel_name: str,
    *,
    store: Any,
    now: datetime | None = None,
    horizon: Horizon = Horizon.TACTICAL,
) -> str:
    """Take at most one observation for this instrument in the current hour.

    Returns the outcome: written / duplicate_skipped / insufficient_data_skipped
    / unknown_instrument.
    """
    moment = now or datetime.now(timezone.utc)
    key = observation_key(instrument, horizon, moment)
    bucket = int(moment.timestamp()) // OBSERVATION_BUCKET_SECONDS
    record_id = observation_record_id(key)

    # Cheapest possible duplicate check first: this process already did it.
    if _HANDLED_BUCKETS.get(instrument) == bucket:
        _bump("duplicate_skipped")
        return "duplicate_skipped"

    log = load_shadow_log(store)
    if log.contains(record_id):
        _HANDLED_BUCKETS[instrument] = bucket
        log.bump("duplicate_skipped")
        save_shadow_log(store, log)
        _bump("duplicate_skipped")
        return "duplicate_skipped"

    inputs = _gather_production_inputs(instrument, fred_key, channel_name)
    if inputs is None:
        _bump("unknown_instrument")
        return "unknown_instrument"

    if inputs["composite"] is None and inputs["tactical"] is None:
        # No production evidence of any kind arrived. There is nothing to
        # observe, which is different from observing that evidence is missing.
        _HANDLED_BUCKETS[instrument] = bucket
        log.bump("insufficient_data_skipped")
        save_shadow_log(store, log)
        _bump("insufficient_data_skipped")
        return "insufficient_data_skipped"

    entry_plan = None
    if isinstance(inputs["tactical"], Mapping):
        candidate = inputs["tactical"].get("entry_plan")
        if isinstance(candidate, Mapping):
            entry_plan = candidate

    event_points = None
    if isinstance(entry_plan, Mapping):
        try:
            event_points = int(entry_plan.get("event_points"))
        except (TypeError, ValueError):
            event_points = None
    minutes_to_event = _EVENT_POINTS_TO_MINUTES.get(event_points) if event_points is not None else None

    evaluation = evaluate_from_production(
        instrument=instrument,
        decision_horizon=horizon,
        composite=inputs["composite"],
        tactical=inputs["tactical"],
        real_yield_mtf=inputs["real_yield_mtf"],
        nominal_yield_mtf=inputs["nominal_yield_mtf"],
        inflation_expectations_mtf=inputs["inflation_expectations_mtf"],
        rule_points=inputs["rule_points"],
        ai_points=inputs["ai_points"],
        minutes_to_event=minutes_to_event,
        is_top_tier_event=(event_points == 0),
        evaluated_at=moment,
        observation_key=key,
    )

    try:
        log.append(evaluation.record)
    except ShadowLogError:
        _HANDLED_BUCKETS[instrument] = bucket
        log.bump("duplicate_skipped")
        save_shadow_log(store, log)
        _bump("duplicate_skipped")
        return "duplicate_skipped"

    _HANDLED_BUCKETS[instrument] = bucket
    log.bump("written")
    save_shadow_log(store, log)
    _bump("written")
    return "written"


def run_shadow_observation(
    fred_key: str,
    channel_name: str,
    *,
    store: Any | None = None,
    now: datetime | None = None,
) -> dict[str, str]:
    """Entry point for the production daemon hook.

    Never raises: every per-instrument failure is caught and counted, so one
    broken instrument cannot stop the others and nothing can propagate back into
    the production loop.
    """
    _bump("attempted")
    if not shadow_enabled():
        _bump("disabled")
        return {}

    backend = store if store is not None else ProductionShadowStore()
    outcomes: dict[str, str] = {}
    for instrument in shadow_instruments():
        try:
            outcomes[instrument] = observe_instrument(
                instrument, fred_key, channel_name, store=backend, now=now
            )
        except Exception:
            _bump("exception_swallowed")
            outcomes[instrument] = "exception_swallowed"
    return outcomes


def get_shadow_hook_stats() -> dict[str, int]:
    """In-process hook counters, for an operator or an admin panel to read."""
    return dict(HOOK_STATS)


__all__ = [
    "HOOK_COUNTERS",
    "HOOK_STATS",
    "OBSERVATION_BUCKET_SECONDS",
    "PREDICTION_LOG_FILE",
    "PREDICTION_LOG_STATE_ID",
    "ProductionShadowStore",
    "SHADOW_LOG_FILE",
    "SHADOW_LOG_STATE_ID",
    "evaluate_from_production",
    "get_shadow_hook_stats",
    "load_prediction_log",
    "load_shadow_log",
    "observation_key",
    "observation_record_id",
    "observe_instrument",
    "record_evaluation",
    "run_shadow_observation",
    "save_prediction_log",
    "save_shadow_log",
    "shadow_enabled",
    "shadow_instruments",
    "signals_from_production",
]
