"""Architecture B2 -- the null benchmark for the Directional evidence layer.

This module answers one question, and deliberately only one:

    Does the Directional family behave differently from a noise generator?

It exists because the defect it is designed to catch survived 1,533 tests. The
Directional family was classifying volatility-standardised returns against a
neutral band of 0.05 -- a constant chosen for a bounded [-1, 1] score, where it
means five percent of full scale, applied to a z-score, where it means five
hundredths of a standard deviation. On a driftless random walk containing no
signal at all, the family emitted a direction 68.5% of the time and emitted it
at STRONG strength -- full aggregation weight -- 44.6% of the time.

No unit test caught that, and no reasonable unit test would have. Every
component behaved exactly as specified; the specification was wrong about what
its own numbers meant. What catches an error of that shape is a control: feed
the pipeline evidence that is known to contain nothing, and check that it says
so.

WHAT THIS IS NOT
----------------
**It is not a calibration engine, and it must never become one.** It reports
how the evidence layer responds to known-null input. It does not search for a
threshold, it does not read the shadow corpus, and it does not return a
recommended parameter. Tuning ``STANDARDISED_SIGMA_FLAT_THRESHOLD`` until this
benchmark produced a pleasing number would be calibration performed against
synthetic data and then applied to real decisions -- worse than no control at
all, because it would carry the authority of having been "validated".

The separation is structural, not a convention: nothing here returns a
parameter, and every entry point takes the threshold as an input rather than
choosing one. A caller may vary it to see the response curve; the registry constant
still has to be justified on its own terms.

**It is not an accuracy measurement.** It says nothing about whether B2 is
right. A family that stays silent on noise has passed this control and may
still have no predictive value whatsoever. This is a necessary condition, not
evidence of skill.

DETERMINISM
-----------
Pure, seeded, and reproducible: same seed and same parameters give the same
numbers on every machine and every run. It performs no I/O, reads no clock, and
touches no stored record.

The generator is written out below rather than taken from ``random``. That is
deliberate. ``random`` is stdlib and would be admissible, but it carries a
module-level global generator, and a single future edit calling ``random.gauss``
instead of ``rng.gauss`` would silently make this control non-reproducible while
still appearing to work -- and a control whose numbers move between runs is
worse than none, because two runs can no longer be compared. A self-contained
generator removes that failure mode by construction and keeps the package's
import surface as narrow as it already was.

B2 remains SHADOW / NON-PRODUCTION / UNCALIBRATED. Nothing here votes,
calibrates, promotes, or informs a decision.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Sequence

from ..adapters import (
    LONG_HORIZON_BARS,
    MEDIUM_HORIZON_BARS,
    SHORT_HORIZON_BARS,
    directional_signals,
)
from ..enums import Direction, FamilyStrength, Horizon
from ..families import FamilyReading, evaluate_family
from ..registry import DIRECTIONAL, STANDARDISED_SIGMA_FLAT_THRESHOLD, FamilyDefinition

#: Bumped when the MEANING of a benchmark result changes -- a new baseline, a
#: different generator, a different reported statistic. A stored result without
#: it cannot be compared against a later one.
NULL_BENCHMARK_VERSION = "b2-null-benchmark-v1"

#: Per-bar return volatility used to generate synthetic series. The absolute
#: value is irrelevant to the result -- the pipeline standardises by exactly
#: this quantity, so the ratio is what matters and any positive value gives the
#: same answer. Fixed here only so runs are comparable.
REFERENCE_VOLATILITY = 0.0009

#: Bars generated per sample. LONG_HORIZON_BARS is the deepest lookback any
#: member needs, so this is the shortest series that can produce all three.
BARS_PER_SAMPLE = LONG_HORIZON_BARS


class Baseline(Enum):
    """What the Directional family is being compared against.

    ``PURE_NOISE`` is the control that matters. The others exist so a reader can
    see what the reported statistics look like at their extremes, rather than
    having to hold a mental model of what "good" would be.
    """

    #: Driftless random walk. Contains no directional signal by construction, so
    #: any directional reading is a false positive.
    PURE_NOISE = "pure_noise"
    #: Emits FLAT always. The floor: zero false positives, zero information.
    ALWAYS_FLAT = "always_flat"
    #: Direction chosen by coin flip, independent of the series. The comparison
    #: point for "is this better than guessing".
    RANDOM_DIRECTION = "random_direction"
    #: Repeats the previous sample's direction. Included because a trend-reading
    #: family SHOULD resemble persistence on trending input and should not
    #: resemble it on noise; the gap between the two is the informative part.
    PERSISTENCE = "persistence"
    #: Random walk plus a constant drift. NOT a baseline and not a skill claim:
    #: a contrast case showing the family does respond when a signal is present,
    #: so that a family which is merely silent about everything is distinguishable
    #: from one that is silent about noise specifically.
    DRIFTING = "drifting"


@dataclass(frozen=True)
class BenchmarkResult:
    """How one baseline responded, in counts and rates.

    Rates are reported alongside their numerator and denominator because a rate
    without its sample size invites exactly the over-reading this module exists
    to prevent.
    """

    baseline: Baseline
    samples: int
    directional_n: int
    strong_n: int
    flat_n: int
    unavailable_n: int
    threshold: float

    @property
    def directional_rate(self) -> float:
        return self.directional_n / self.samples if self.samples else 0.0

    @property
    def strong_rate(self) -> float:
        return self.strong_n / self.samples if self.samples else 0.0

    @property
    def flat_rate(self) -> float:
        return self.flat_n / self.samples if self.samples else 0.0

    def as_record(self) -> dict[str, object]:
        return {
            "baseline": self.baseline.value,
            "samples": self.samples,
            "threshold_sigma": self.threshold,
            "directional_n": self.directional_n,
            "directional_rate": round(self.directional_rate, 4),
            "strong_n": self.strong_n,
            "strong_rate": round(self.strong_rate, 4),
            "flat_n": self.flat_n,
            "flat_rate": round(self.flat_rate, 4),
            "unavailable_n": self.unavailable_n,
        }


@dataclass(frozen=True)
class NullBenchmark:
    """A complete benchmark run: every baseline under one configuration."""

    version: str
    seed: int
    samples: int
    threshold: float
    volatility: float
    results: tuple[BenchmarkResult, ...]

    def result(self, baseline: Baseline) -> BenchmarkResult | None:
        for item in self.results:
            if item.baseline is baseline:
                return item
        return None

    @property
    def noise_strong_rate(self) -> float:
        """The headline number: STRONG readings on input containing no signal.

        STRONG is the one that matters rather than "directional", because
        strength is what the concave aggregator turns into weight -- WEAK
        contributes 0.35 and STRONG contributes 1.00. A family that occasionally
        reads WEAK on noise is behaving reasonably; one that reads STRONG on
        noise is manufacturing full-weight evidence out of nothing.
        """
        noise = self.result(Baseline.PURE_NOISE)
        return noise.strong_rate if noise else 0.0

    def as_record(self) -> dict[str, object]:
        return {
            "version": self.version,
            "seed": self.seed,
            "samples": self.samples,
            "threshold_sigma": self.threshold,
            "reference_volatility": self.volatility,
            "noise_strong_rate": round(self.noise_strong_rate, 4),
            "results": [r.as_record() for r in self.results],
            "disclaimer": (
                "A validation control, not a calibration. Passing means the "
                "family is not a noise generator; it is not evidence of skill "
                "and no threshold may be selected from these numbers."
            ),
        }


class _Rng:
    """A small, explicit, fully deterministic generator.

    A 64-bit linear congruential core (the constants are Knuth's MMIX) feeding a
    Box-Muller transform for normal draws. Adequate for generating a null
    control and chosen for reproducibility rather than statistical pedigree: no
    conclusion here depends on the fine structure of the generator, only on the
    draws containing no directional signal.
    """

    __slots__ = ("_state", "_spare")

    _MULTIPLIER = 6364136223846793005
    _INCREMENT = 1442695040888963407
    _MODULUS = 1 << 64

    def __init__(self, seed: int) -> None:
        self._state = int(seed) % self._MODULUS
        self._spare: float | None = None

    def _next_u64(self) -> int:
        self._state = (self._state * self._MULTIPLIER + self._INCREMENT) % self._MODULUS
        return self._state

    def uniform(self) -> float:
        """A float in (0, 1). Zero is excluded so log() below is always defined."""
        return (self._next_u64() >> 11) / float(1 << 53) or 1e-12

    def gauss(self, mu: float, sigma: float) -> float:
        if self._spare is not None:
            value, self._spare = self._spare, None
            return mu + sigma * value
        u1, u2 = self.uniform(), self.uniform()
        radius = math.sqrt(-2.0 * math.log(u1))
        angle = 2.0 * math.pi * u2
        self._spare = radius * math.sin(angle)
        return mu + sigma * (radius * math.cos(angle))

    def choice(self, options: Sequence[object]) -> object:
        return options[self._next_u64() % len(options)]


def _walk(rng: "_Rng", volatility: float, drift: float = 0.0) -> list[float]:
    """One synthetic per-bar return series."""
    return [rng.gauss(drift, volatility) for _ in range(BARS_PER_SAMPLE)]


def _tactical_from_walk(steps: Sequence[float]) -> dict[str, float]:
    """The three tactical returns production would export from this series.

    Cumulative sums over the same bar counts ``compute_tactical_move`` uses, so
    the synthetic input enters the real adapter in exactly the shape a live
    observation would.
    """
    return {
        "ret_15m": sum(steps[-SHORT_HORIZON_BARS:]),
        "ret_1h": sum(steps[-MEDIUM_HORIZON_BARS:]),
        "ret_4h": sum(steps[-LONG_HORIZON_BARS:]),
    }


def _with_threshold(
    definition: FamilyDefinition, threshold: float
) -> FamilyDefinition:
    """The family definition with its sigma-scaled bands set to ``threshold``.

    Used only so a caller can plot the response curve. It builds a modified copy
    and never mutates the registry: the live definition is frozen and stays
    frozen.
    """
    from dataclasses import replace

    from ..registry import MemberScale

    specs = tuple(
        replace(spec, flat_threshold=threshold)
        if spec.scale is MemberScale.STANDARDISED_SIGMA
        else spec
        for spec in definition.member_specs
    )
    return replace(definition, member_specs=specs)


def _read(
    definition: FamilyDefinition,
    steps: Sequence[float],
    volatility: float,
    threshold: float,
) -> FamilyReading:
    # The same band reaches the adapter's alignment gate and the family's member
    # classification, so a response curve varies one quantity rather than
    # holding one of them fixed and reporting the result as if both had moved.
    signals = directional_signals(
        tactical=_tactical_from_walk(steps),
        volatility_scale=volatility,
        neutral_band=threshold,
    )
    return evaluate_family(definition, signals, Horizon.EXECUTION)


def run_baseline(
    baseline: Baseline,
    *,
    samples: int = 20_000,
    seed: int = 20260903,
    threshold: float = STANDARDISED_SIGMA_FLAT_THRESHOLD,
    volatility: float = REFERENCE_VOLATILITY,
    drift_sigma: float = 0.35,
) -> BenchmarkResult:
    """Run one baseline. Deterministic in ``seed``.

    ``drift_sigma`` applies only to ``DRIFTING`` and is expressed in units of the
    per-bar volatility, so the contrast case is defined relative to noise rather
    than to an absolute price move.
    """
    rng = _Rng(seed)
    definition = _with_threshold(DIRECTIONAL, threshold)

    directional_n = strong_n = flat_n = unavailable_n = 0
    previous = Direction.FLAT

    for _ in range(max(int(samples), 0)):
        if baseline is Baseline.ALWAYS_FLAT:
            direction, strength = Direction.FLAT, FamilyStrength.NONE
        elif baseline is Baseline.RANDOM_DIRECTION:
            direction = rng.choice((Direction.BULLISH, Direction.BEARISH))
            strength = FamilyStrength.STRONG
        elif baseline is Baseline.PERSISTENCE:
            # Consume a draw regardless so the generator advances identically
            # across baselines at the same seed.
            _walk(rng, volatility)
            direction = previous if previous.is_directional else Direction.BULLISH
            strength = FamilyStrength.MODERATE
            previous = direction
        else:
            drift = volatility * drift_sigma if baseline is Baseline.DRIFTING else 0.0
            reading = _read(
                definition, _walk(rng, volatility, drift), volatility, threshold
            )
            direction, strength = reading.direction, reading.strength

        if direction is Direction.UNAVAILABLE:
            unavailable_n += 1
        elif direction.is_directional:
            directional_n += 1
            if strength is FamilyStrength.STRONG:
                strong_n += 1
        else:
            flat_n += 1

    return BenchmarkResult(
        baseline=baseline,
        samples=max(int(samples), 0),
        directional_n=directional_n,
        strong_n=strong_n,
        flat_n=flat_n,
        unavailable_n=unavailable_n,
        threshold=float(threshold),
    )


def run_null_benchmark(
    *,
    samples: int = 20_000,
    seed: int = 20260903,
    threshold: float = STANDARDISED_SIGMA_FLAT_THRESHOLD,
    volatility: float = REFERENCE_VOLATILITY,
    baselines: Sequence[Baseline] | None = None,
) -> NullBenchmark:
    """Run every baseline under one configuration.

    Every baseline uses the SAME seed, so differences between them are
    differences in the pipeline rather than in the draws.
    """
    chosen = tuple(baselines) if baselines is not None else tuple(Baseline)
    return NullBenchmark(
        version=NULL_BENCHMARK_VERSION,
        seed=int(seed),
        samples=int(samples),
        threshold=float(threshold),
        volatility=float(volatility),
        results=tuple(
            run_baseline(
                baseline,
                samples=samples,
                seed=seed,
                threshold=threshold,
                volatility=volatility,
            )
            for baseline in chosen
        ),
    )


def threshold_response(
    thresholds: Sequence[float],
    *,
    samples: int = 5_000,
    seed: int = 20260903,
    volatility: float = REFERENCE_VOLATILITY,
) -> tuple[BenchmarkResult, ...]:
    """How the noise response varies with the neutral band. RESEARCH ONLY.

    Provided so the sensitivity of the evidence layer to its own band is
    inspectable rather than assumed. It returns measurements and nothing else:
    it does not rank them, does not identify a best value, and no caller may
    select the live threshold from its output. The registry constant is
    justified by its correspondence with the validation layer's own neutral
    band, and a number chosen because it looked good on synthetic data would be
    a fitted parameter wearing a control's clothes.
    """
    return tuple(
        run_baseline(
            Baseline.PURE_NOISE,
            samples=samples,
            seed=seed,
            threshold=float(threshold),
            volatility=volatility,
        )
        for threshold in thresholds
    )


__all__ = [
    "BARS_PER_SAMPLE",
    "Baseline",
    "BenchmarkResult",
    "NULL_BENCHMARK_VERSION",
    "NullBenchmark",
    "REFERENCE_VOLATILITY",
    "run_baseline",
    "run_null_benchmark",
    "threshold_response",
]
