"""Fault injectors for the Aletheia benchmark.

Design contract for every injector:

1. Deterministic given a seed, so cases reproduce exactly.
2. One-factor: change a single controllable cause, leave everything else fixed,
   so the case has one dominant ground-truth cause.
3. Separation of concerns:
   - ``ground_truth`` holds the hidden answer key (cause label + mechanism) and
     must never be shown to the diagnosis model.
   - ``signals`` holds only observable, evidence-safe facts (distributions,
     PSI). They describe what the data looks like; they do not name the cause.
   The diagnosis model must *infer* the cause from ``signals`` alone.

P1 implements ``data_drift`` (categorical distribution shift). Other fault types
are added when their phase starts (see 02_TASKS.csv), each following this
contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import numpy as np
import pandas as pd

from aletheia_lab.benchmark.manifest import GroundTruth
from aletheia_lab.benchmark.signals import (
    categorical_distribution,
    population_stability_index,
)


@dataclass(frozen=True)
class InjectionResult:
    """Output of a fault injector.

    ``injected`` is the transformed dataset. ``ground_truth`` is hidden from the
    diagnoser. ``signals`` is the evidence-safe view (no answer key).
    """

    injected: pd.DataFrame
    ground_truth: GroundTruth
    signals: dict[str, object]


class FaultInjector(Protocol):
    """Protocol for deterministic fault injectors."""

    fault_type: str

    def inject(self, source: pd.DataFrame) -> InjectionResult:
        """Create one injected dataset from a fixed base dataset."""
        ...


@dataclass(frozen=True)
class DriftSpec:
    """Configuration for one categorical data-drift injection.

    ``target_distribution`` maps a category of ``feature`` to its desired
    proportion in the injected batch. Proportions are normalized internally.
    """

    injection_id: str
    feature: str
    target_distribution: dict[str, float]
    output_size: int | None = None
    seed: int = 0


class CategoricalDriftInjector:
    """Shift the marginal distribution of one categorical feature.

    The injector resamples rows within each category of ``feature`` so that
    ``feature`` follows ``spec.target_distribution``. The controlled cause is a
    single one: the marginal of ``feature``. Rows are drawn from their own
    category, so the within-category conditional structure of the other columns
    is preserved; however, because the category mix changes, the *marginal*
    distribution of any column correlated with ``feature`` will shift as a
    downstream consequence of the resampling. This is a property of conditional
    resampling, not a second injected cause, and the guarantee is deliberately
    scoped to the one controlled feature rather than to "all other columns".
    """

    fault_type = "data_drift"

    def __init__(self, spec: DriftSpec) -> None:
        self.spec = spec

    def inject(self, source: pd.DataFrame) -> InjectionResult:
        spec = self.spec
        if spec.feature not in source.columns:
            msg = f"feature {spec.feature!r} not in source columns"
            raise ValueError(msg)

        feature_values = source[spec.feature].astype(str)
        source_dist = categorical_distribution(feature_values.tolist())
        target_dist = _normalize(spec.target_distribution)

        n_out = spec.output_size if spec.output_size is not None else len(source)
        rng = np.random.default_rng(spec.seed)

        # Largest-remainder apportionment: counts sum to exactly n_out, so the
        # injected batch size matches the request instead of drifting with
        # per-category rounding. Deterministic given the (sorted) categories.
        counts = _apportion(target_dist, n_out)

        parts: list[pd.DataFrame] = []
        for category in sorted(target_dist):
            pool = source.loc[feature_values == category]
            if pool.empty:
                msg = f"category {category!r} absent from source; cannot inject drift"
                raise ValueError(msg)
            count = counts[category]
            if count == 0:
                continue
            picks = rng.integers(0, len(pool), size=count)
            parts.append(pool.iloc[picks])

        injected = pd.concat(parts, ignore_index=True)
        order = rng.permutation(len(injected))
        injected = injected.iloc[order].reset_index(drop=True)

        achieved_dist = categorical_distribution(injected[spec.feature].astype(str).tolist())
        psi = population_stability_index(source_dist, achieved_dist)

        ground_truth = GroundTruth(
            cause_label=self.fault_type,
            causal_mechanism="categorical_distribution_shift",
            injected_change=f"{spec.feature}: {source_dist} -> {achieved_dist}",
            affected_components=[spec.feature],
            expected_symptoms=["metric_regression", f"distribution_shift:{spec.feature}"],
        )

        # Evidence-safe: describes the data, never names the cause.
        signals: dict[str, object] = {
            "feature": spec.feature,
            "distribution_before": source_dist,
            "distribution_after": achieved_dist,
            "psi": psi,
            "sample_size": n_out,
        }

        return InjectionResult(injected=injected, ground_truth=ground_truth, signals=signals)


def _apportion(target_distribution: dict[str, float], n_out: int) -> dict[str, int]:
    """Split ``n_out`` across categories by proportion, summing to exactly n_out.

    Uses the largest-remainder method over the normalized proportions; ties are
    broken by category name so the result is deterministic.
    """

    normalized = _normalize(target_distribution)
    raw = {category: proportion * n_out for category, proportion in normalized.items()}
    floors = {category: int(value) for category, value in raw.items()}
    assigned = sum(floors.values())
    remainder = n_out - assigned
    # Hand out the remaining units to the largest fractional parts (name tiebreak).
    order = sorted(raw, key=lambda c: (raw[c] - floors[c], c), reverse=True)
    for category in order[:remainder]:
        floors[category] += 1
    return floors


def _normalize(distribution: dict[str, float]) -> dict[str, float]:
    """Normalize a proportion map to sum to 1.0."""

    total = sum(distribution.values())
    if total <= 0:
        msg = "target_distribution must have a positive total"
        raise ValueError(msg)
    return {category: weight / total for category, weight in distribution.items()}
