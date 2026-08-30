"""Architecture B2 -- asset-specific module foundations.

Asset modules are **transmission diagnostics**, not evidence. They answer a
different question from the voting core:

    Voting core:  what is the evidence, and which way does it point?
    Asset module: is the channel that evidence must travel through actually
                  carrying it right now, for this instrument?

Because of that, an asset module NEVER contributes a vote. ``AssetModuleReading``
exposes no evidence value, the aggregator never sees one, and
``DriverReading.contributes_vote`` is a constant ``False``.

The rule that makes this safe is enforced at import time rather than by
convention: a driver that declares an overlap with a universal voting family
cannot also claim to be independent asset-specific evidence. In this project
every driver overlaps something -- which is the honest result, since the data
that would be genuinely independent (official-sector demand, inventories,
positioning, earnings) does not exist here and is declared dormant instead.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping

from ..enums import Direction, FamilyStrength, Horizon, Role
from ..families import FLAT_THRESHOLD, classify_signal

#: Strength bands. These are the existing project thresholds, reused rather than
#: invented: bias_from_score already treats 0.15 as the moderate boundary and
#: 0.35 as the strong boundary on the same [-1, 1] scale.
MODERATE_THRESHOLD = 0.15
STRONG_THRESHOLD = 0.35


class DriverEvidenceClass(Enum):
    """How a driver relates to the universal voting core."""

    #: Genuinely new evidence, not present in any voting family. Requires that
    #: the driver declares NO universal-family overlap.
    INDEPENDENT_ASSET_SPECIFIC = "independent_asset_specific"
    #: A restatement of evidence that already votes, expressed in this asset's
    #: transmission terms. Diagnostic only -- it must never vote again.
    TRANSFORMATION = "transformation"
    #: Computed from other components' states. Never feeds its own inputs.
    DERIVED_DIAGNOSTIC = "derived_diagnostic"
    #: The data exists in principle but did not arrive this evaluation.
    UNAVAILABLE = "unavailable"
    #: Documented, not computed: the project has no data for it.
    DORMANT = "dormant"


class TransmissionState(Enum):
    """A driver's state relative to the thesis direction under test."""

    SUPPORTING = "supporting"
    NEUTRAL = "neutral"
    CONFLICTING = "conflicting"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class DriverDefinition:
    """One transmission channel for one asset. Membership is frozen."""

    key: str
    label: str
    evidence_class: DriverEvidenceClass
    horizon: Horizon
    rationale: str
    #: The voting family whose evidence this driver restates, or None when the
    #: driver genuinely stands alone.
    universal_family_overlap: str | None
    data_sources: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if (
            self.universal_family_overlap
            and self.evidence_class is DriverEvidenceClass.INDEPENDENT_ASSET_SPECIFIC
        ):
            raise ValueError(
                f"{self.key}: a driver overlapping the "
                f"{self.universal_family_overlap!r} voting family cannot be "
                "INDEPENDENT_ASSET_SPECIFIC. Evidence that already votes must "
                "not vote a second time under an asset-specific name."
            )
        if self.evidence_class is DriverEvidenceClass.DORMANT and self.data_sources:
            raise ValueError(
                f"{self.key}: a dormant driver must declare no data sources."
            )
        if not self.rationale.strip():
            raise ValueError(f"{self.key}: a driver must carry a written rationale.")


@dataclass(frozen=True)
class DriverReading:
    """One driver's reading. Carries no evidence value and never votes."""

    key: str
    label: str
    evidence_class: DriverEvidenceClass
    horizon: Horizon
    universal_family_overlap: str | None
    direction: Direction
    strength: FamilyStrength
    state: TransmissionState
    value: float | None
    rationale: str

    @property
    def contributes_vote(self) -> bool:
        """Asset modules never vote. This is not configurable."""
        return False

    @property
    def is_available(self) -> bool:
        return self.direction is not Direction.UNAVAILABLE

    def as_record(self) -> dict[str, object]:
        return {
            "driver": self.key,
            "label": self.label,
            "evidence_class": self.evidence_class.value,
            "horizon": self.horizon.value,
            "universal_family_overlap": self.universal_family_overlap,
            "direction": self.direction.value,
            "strength": self.strength.name,
            "transmission_state": self.state.value,
            "value": self.value,
            "contributes_vote": self.contributes_vote,
            "rationale": self.rationale,
        }


@dataclass(frozen=True)
class AssetModuleReading:
    """One asset module's complete diagnostic output."""

    module: str
    instrument: str
    horizon: Horizon
    thesis_direction: Direction
    drivers: tuple[DriverReading, ...]
    dormant_drivers: tuple[str, ...]
    transmission_summary: TransmissionState
    conflicts_with_macro_thesis: bool
    rationale: str
    role: Role = Role.ASSET_SPECIFIC_MODULE
    #: Module-specific context a reader needs to interpret the drivers, such as
    #: which counter-currency an FX comparison used.
    notes: tuple[str, ...] = ()

    @property
    def contributes_evidence(self) -> bool:
        """An asset module is a diagnostic. It adds no evidence to the core."""
        return False

    @property
    def available_drivers(self) -> tuple[DriverReading, ...]:
        return tuple(d for d in self.drivers if d.is_available)

    @property
    def unavailable_drivers(self) -> tuple[str, ...]:
        return tuple(d.key for d in self.drivers if not d.is_available)

    def driver(self, key: str) -> DriverReading | None:
        for candidate in self.drivers:
            if candidate.key == key:
                return candidate
        return None

    def as_record(self) -> dict[str, object]:
        return {
            "asset_module": self.module,
            "instrument": self.instrument,
            "role": self.role.value,
            "horizon": self.horizon.value,
            "thesis_direction": self.thesis_direction.value,
            "transmission_summary": self.transmission_summary.value,
            "conflicts_with_macro_thesis": self.conflicts_with_macro_thesis,
            "contributes_evidence": self.contributes_evidence,
            "drivers": [d.as_record() for d in self.drivers],
            "available_drivers": [d.key for d in self.available_drivers],
            "unavailable_drivers": list(self.unavailable_drivers),
            "dormant_drivers": list(self.dormant_drivers),
            "rationale": self.rationale,
            "notes": list(self.notes),
        }


def _strength_for(value: float) -> FamilyStrength:
    magnitude = abs(value)
    if magnitude <= FLAT_THRESHOLD:
        return FamilyStrength.NONE
    if magnitude < MODERATE_THRESHOLD:
        return FamilyStrength.WEAK
    if magnitude < STRONG_THRESHOLD:
        return FamilyStrength.MODERATE
    return FamilyStrength.STRONG


def evaluate_driver(
    definition: DriverDefinition,
    value: float | None,
    thesis_direction: Direction,
) -> DriverReading:
    """Read one driver against the thesis direction.

    ``value`` must already be expressed in the instrument's own direction (positive
    means bullish for this instrument), using the sign convention the production
    formula itself uses. ``None`` means the channel could not be measured, which
    is Unavailable and never a flat reading.
    """
    direction = classify_signal(value)

    if direction is Direction.UNAVAILABLE:
        return DriverReading(
            key=definition.key,
            label=definition.label,
            evidence_class=DriverEvidenceClass.UNAVAILABLE,
            horizon=definition.horizon,
            universal_family_overlap=definition.universal_family_overlap,
            direction=Direction.UNAVAILABLE,
            strength=FamilyStrength.NONE,
            state=TransmissionState.UNAVAILABLE,
            value=None,
            rationale=(
                f"{definition.label} could not be measured this evaluation. "
                "Unavailable, not neutral: the channel's state is unknown."
            ),
        )

    numeric = float(value)  # classify_signal already rejected NaN/inf
    strength = _strength_for(numeric)

    if direction is Direction.FLAT:
        state = TransmissionState.NEUTRAL
        rationale = (
            f"{definition.label} is present but below the {FLAT_THRESHOLD} "
            "directional threshold: the channel is open and carrying nothing."
        )
    elif not thesis_direction.is_directional:
        state = TransmissionState.NEUTRAL
        rationale = (
            f"{definition.label} reads {direction.value}, but there is no "
            "directional thesis for it to support or conflict with."
        )
    elif direction is thesis_direction:
        state = TransmissionState.SUPPORTING
        rationale = (
            f"{definition.label} is transmitting {direction.value} at "
            f"{strength.name.lower()} strength, consistent with the thesis."
        )
    else:
        state = TransmissionState.CONFLICTING
        rationale = (
            f"{definition.label} is transmitting {direction.value} at "
            f"{strength.name.lower()} strength, against the "
            f"{thesis_direction.value} thesis."
        )

    return DriverReading(
        key=definition.key,
        label=definition.label,
        evidence_class=definition.evidence_class,
        horizon=definition.horizon,
        universal_family_overlap=definition.universal_family_overlap,
        direction=direction,
        strength=strength,
        state=state,
        value=numeric,
        rationale=rationale,
    )


def build_module_reading(
    *,
    module: str,
    instrument: str,
    horizon: Horizon,
    thesis_direction: Direction,
    definitions: tuple[DriverDefinition, ...],
    values: Mapping[str, float | None],
    dormant_drivers: tuple[str, ...] = (),
    notes: tuple[str, ...] = (),
) -> AssetModuleReading:
    """Evaluate every driver and summarise the module's transmission state.

    A value for a key that is not a declared driver is rejected: it would be a
    route for smuggling extra evidence into a frozen module.
    """
    unknown = set(values) - {d.key for d in definitions}
    if unknown:
        raise ValueError(
            f"{module}: {sorted(unknown)} are not declared drivers of this module."
        )

    drivers = tuple(
        evaluate_driver(definition, values.get(definition.key), thesis_direction)
        for definition in definitions
    )

    supporting = tuple(d for d in drivers if d.state is TransmissionState.SUPPORTING)
    conflicting = tuple(d for d in drivers if d.state is TransmissionState.CONFLICTING)
    available = tuple(d for d in drivers if d.is_available)

    if not available:
        summary = TransmissionState.UNAVAILABLE
        rationale = (
            f"No {instrument} transmission channel could be measured. The module "
            "is Unavailable, which is not the same as reading neutral."
        )
    elif len(supporting) > len(conflicting):
        summary = TransmissionState.SUPPORTING
        rationale = (
            f"{len(supporting)} of {len(available)} measurable channels are "
            f"carrying the {thesis_direction.value} thesis"
            + (f", {len(conflicting)} against it" if conflicting else "")
            + "."
        )
    elif len(conflicting) > len(supporting):
        summary = TransmissionState.CONFLICTING
        rationale = (
            f"{len(conflicting)} of {len(available)} measurable channels are "
            f"working against the {thesis_direction.value} thesis."
        )
    else:
        summary = TransmissionState.NEUTRAL
        rationale = (
            f"Measurable {instrument} channels are balanced "
            f"({len(supporting)} supporting, {len(conflicting)} conflicting)."
        )

    if dormant_drivers:
        rationale += (
            " Dormant for want of data: " + ", ".join(sorted(dormant_drivers)) + "."
        )

    return AssetModuleReading(
        module=module,
        instrument=instrument,
        horizon=horizon,
        thesis_direction=thesis_direction,
        drivers=drivers,
        dormant_drivers=tuple(sorted(dormant_drivers)),
        transmission_summary=summary,
        conflicts_with_macro_thesis=bool(conflicting),
        rationale=rationale,
        notes=tuple(notes),
    )


def validate_definitions(module: str, definitions: tuple[DriverDefinition, ...]) -> None:
    """Import-time guards for one module's driver set."""
    if not definitions:
        raise ValueError(f"{module}: a module must declare at least one driver.")
    keys = [d.key for d in definitions]
    if len(set(keys)) != len(keys):
        raise ValueError(f"{module}: duplicate driver keys.")
    if len(definitions) > 3:
        raise ValueError(
            f"{module}: {len(definitions)} drivers declared. Asset modules are "
            "capped at three strongly justified drivers -- each is validated on "
            "a single instrument and so faces the worst sample conditions in the "
            "system, which calls for stricter discipline than the core, not looser."
        )
