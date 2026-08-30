"""Architecture B2 -- asset-specific transmission modules.

Modules are ACTIVE NON-VOTING diagnostics. They restate, in one instrument's
transmission terms, evidence that has already voted in the universal core, and
they report which dormant evidence is missing. They never add a vote and are
never seen by the aggregator.

Registered instruments are resolved through ``module_for``; an instrument with
no module simply produces a shadow record without an asset-module section,
rather than a fabricated one.
"""
from __future__ import annotations

from types import ModuleType

from . import fx, gold, nasdaq, oil
from .base import (
    AssetModuleReading,
    DriverDefinition,
    DriverEvidenceClass,
    DriverReading,
    TransmissionState,
    build_module_reading,
    evaluate_driver,
    validate_definitions,
)

#: instrument key (as production names it) -> module implementation.
MODULES: dict[str, ModuleType] = {
    gold.INSTRUMENT: gold,
    oil.INSTRUMENT: oil,
    nasdaq.INSTRUMENT: nasdaq,
    # One FX module serves every configured currency; the currency is supplied
    # per evaluation rather than baked into a separate module each time.
    **{currency: fx for currency in fx.INSTRUMENTS},
}


def module_for(instrument: str) -> ModuleType | None:
    """The asset module for this instrument, or None when none is registered."""
    return MODULES.get(str(instrument or "").strip())


def registered_instruments() -> tuple[str, ...]:
    return tuple(sorted(MODULES))


def module_keys() -> tuple[str, ...]:
    return tuple(sorted(m.MODULE_KEY for m in MODULES.values()))


__all__ = [
    "AssetModuleReading",
    "DriverDefinition",
    "DriverEvidenceClass",
    "DriverReading",
    "MODULES",
    "TransmissionState",
    "build_module_reading",
    "evaluate_driver",
    "module_for",
    "module_keys",
    "registered_instruments",
    "validate_definitions",
    "fx",
    "gold",
    "nasdaq",
    "oil",
]
