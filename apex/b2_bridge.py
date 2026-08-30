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

from typing import Any, Mapping, Sequence

from . import production_core as core
from .b2 import adapters
from .b2.enums import Horizon
from .b2.evaluate import ShadowEvaluation, run_shadow_evaluation, thesis_input_keys
from .b2.predictions import PredictionLog
from .b2.shadow import ShadowLog

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


__all__ = [
    "PREDICTION_LOG_FILE",
    "PREDICTION_LOG_STATE_ID",
    "ProductionShadowStore",
    "SHADOW_LOG_FILE",
    "SHADOW_LOG_STATE_ID",
    "evaluate_from_production",
    "load_prediction_log",
    "load_shadow_log",
    "record_evaluation",
    "save_prediction_log",
    "save_shadow_log",
    "signals_from_production",
]
