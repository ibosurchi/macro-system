"""Architecture B2 -- shared vocabulary.

Every value here is a *distinct enumerated state*. The single most important
rule encoded in this module is that ``FLAT``/``NEUTRAL`` and ``UNAVAILABLE``
are different things and can never be spelled the same way:

* ``FLAT`` / ``NEUTRAL`` -- the data exists and shows no directional evidence.
* ``UNAVAILABLE``        -- the data is missing or unusable.

Collapsing those two is what lets a broken feed masquerade as a calm market,
so they are separate members of separate enums and are never coerced to a
shared falsy value such as ``0.0`` anywhere in this package.
"""
from __future__ import annotations

from enum import Enum


class Role(Enum):
    """Component role taxonomy. A component has exactly one role."""

    ACTIVE_VOTING = "active_voting"
    ACTIVE_NON_VOTING = "active_non_voting"
    DERIVED_OUTPUT = "derived_output"
    META_STATE = "meta_state"
    GATE = "gate"
    EXECUTION_INPUT = "execution_input"
    DIAGNOSTIC = "diagnostic"
    ASSET_SPECIFIC_MODULE = "asset_specific_module"
    DORMANT = "dormant"


#: The ONLY role permitted to contribute independent directional evidence.
#: Anything checking "may this component influence direction?" must consult
#: this set rather than re-deriving the rule locally.
DIRECTION_INFLUENCING_ROLES = frozenset({Role.ACTIVE_VOTING})


class Horizon(Enum):
    """The three explicit horizons. Every claim must record which it belongs to."""

    STRUCTURAL = "structural"   # weeks to months
    TACTICAL = "tactical"       # days to weeks -- primary macro thesis horizon
    EXECUTION = "execution"     # hours to days


class Direction(Enum):
    """Directional reading of a signal, member or family."""

    BULLISH = "bullish"
    BEARISH = "bearish"
    FLAT = "flat"               # data exists, no directional evidence
    UNAVAILABLE = "unavailable"  # data missing/unusable -- NOT the same as FLAT

    def opposite(self) -> "Direction":
        if self is Direction.BULLISH:
            return Direction.BEARISH
        if self is Direction.BEARISH:
            return Direction.BULLISH
        return self

    @property
    def is_directional(self) -> bool:
        return self in (Direction.BULLISH, Direction.BEARISH)


class FamilyState(Enum):
    """A family's state *relative to a candidate direction under test*."""

    SUPPORTS = "supports"
    NEUTRAL = "neutral"
    CONFLICTS = "conflicts"
    UNAVAILABLE = "unavailable"


class FamilyStrength(Enum):
    """Internal strength of a family. Raised by within-family agreement ONLY.

    Strength never increases the *number* of contributions a family makes --
    that is fixed at exactly one (see ``families.FamilyReading.contribution_count``).
    """

    NONE = 0
    WEAK = 1
    MODERATE = 2
    STRONG = 3


class ConfidenceLevel(Enum):
    """Categorical confidence. Deliberately not a percentage.

    Until empirical calibration exists there is no basis for emitting a number
    like ``87.4%``, so user-facing confidence stays Low/Moderate/High.
    """

    LOW = 1
    MODERATE = 2
    HIGH = 3

    def capped_at(self, ceiling: "ConfidenceLevel | None") -> "ConfidenceLevel":
        """Return this level, lowered to ``ceiling`` if the ceiling is stricter.

        Caps *lower* confidence; they never raise it and never flip direction.
        """
        if ceiling is None:
            return self
        return self if self.value <= ceiling.value else ceiling


class EventRiskState(Enum):
    """Event risk is a gate with three states -- never a score to average in."""

    NORMAL = "normal"
    ELEVATED = "elevated"
    CRITICAL = "critical"


class GateAction(Enum):
    """What a gate is entitled to do. Note that none of these change direction."""

    NONE = "none"
    WARN = "warn"
    REDUCE_EXECUTION_CONFIDENCE = "reduce_execution_confidence"
    CAP_CONFIDENCE = "cap_confidence"
    VETO_EXECUTION = "veto_execution"


class DecisionState(Enum):
    """Final decision states. Semantic distinctions are preserved on purpose;
    nothing here collapses to Buy / Sell / Neutral."""

    CONFIRMED_THESIS = "confirmed_thesis"
    THESIS_VALID_WAIT_FOR_ENTRY = "thesis_valid_wait_for_entry"
    TECHNICAL_SETUP_WEAK_MACRO_SUPPORT = "technical_setup_weak_macro_support"
    MIXED_NO_EDGE = "mixed_no_edge"
    EXECUTION_BLOCKED = "execution_blocked"
    HIGH_EVENT_RISK = "high_event_risk"
    THESIS_UNDER_REVIEW = "thesis_under_review"
    TECHNICAL_SETUP_INVALIDATED = "technical_setup_invalidated"
    MACRO_THESIS_INVALIDATED = "macro_thesis_invalidated"
    INSUFFICIENT_DATA_SYSTEM_DEGRADED = "insufficient_data_system_degraded"
    POSITION_OPEN_THESIS_INTACT = "position_open_thesis_intact"
    POSITION_OPEN_UNDER_REVIEW = "position_open_under_review"
    THESIS_CONFIRMED_LATE_EXTENDED = "thesis_confirmed_late_extended"


class ThesisState(Enum):
    """Macro thesis lifecycle. Distinct from technical invalidation.

    Stage A only *carries* this value; nothing in Stage A computes it. The
    evaluator that transitions between these states (including the repeated /
    broad / unexplained escalation rule) is Stage B work and is registered as
    withheld until then.
    """

    INTACT = "intact"
    WEAKENING = "weakening"
    UNDER_REVIEW = "under_review"
    INVALIDATED = "invalidated"
