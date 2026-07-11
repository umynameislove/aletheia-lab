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

    The injector resamples rows per category so that ``feature`` follows
    ``spec.target_distribution`` while every other column, and the within-category
    conditional structure, is preserved. This keeps a single dominant cause:
    a distribution shift on exactly one feature.
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

        parts: list[pd.DataFrame] = []
        for category, proportion in sorted(target_dist.items()):
            pool = source.loc[feature_values == category]
            if pool.empty:
                msg = f"category {category!r} absent from source; cannot inject drift"
                raise ValueError(msg)
            count = round(proportion * n_out)
            if count == 0:
                continue
            picks = rng.integers(0, len(pool), size=count)
            parts.append(pool.iloc[picks])

        injected = pd.concat(parts, ignore_index=True)
        order = rng.permutation(len(injected))
        injected = injected.iloc[order].reset_index(drop=True)

        achieved_dist = categorical_distribution(
            injected[spec.feature].astype(str).tolist()
        )
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


def _normalize(distribution: dict[str, float]) -> dict[str, float]:
    """Normalize a proportion map to sum to 1.0."""

    total = sum(distribution.values())
    if total <= 0:
        msg = "target_distribution must have a positive total"
        raise ValueError(msg)
    return {category: weight / total for category, weight in distribution.items()}
