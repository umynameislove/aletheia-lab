"""Unit tests for the P1 data_drift injector.

Covers the injector design contract: determinism, measurable drift, one-factor
change, ground-truth isolation from evidence, and a reversibility check.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

from aletheia_lab.benchmark.injectors import CategoricalDriftInjector, DriftSpec
from aletheia_lab.benchmark.signals import (
    categorical_distribution,
    population_stability_index,
)

FORBIDDEN_ANSWER_KEY_TERMS = ["data_drift", "injected", "ground_truth", "answer_key"]


def make_source(n: int = 1000) -> pd.DataFrame:
    """Synthetic source: 'contract' drifts; 'region' is independent of it."""

    rng = np.random.default_rng(0)
    contract = rng.choice(["month_to_month", "one_year", "two_year"], size=n, p=[0.55, 0.25, 0.20])
    region = rng.choice(["north", "south"], size=n, p=[0.5, 0.5])
    return pd.DataFrame({"contract": contract, "region": region})


def make_spec(seed: int = 7) -> DriftSpec:
    return DriftSpec(
        injection_id="ml_drift_001",
        feature="contract",
        target_distribution={"month_to_month": 0.80, "one_year": 0.12, "two_year": 0.08},
        seed=seed,
    )


def test_injection_is_deterministic() -> None:
    source = make_source()
    a = CategoricalDriftInjector(make_spec()).inject(source)
    b = CategoricalDriftInjector(make_spec()).inject(source)
    pd.testing.assert_frame_equal(a.injected, b.injected)
    assert a.signals["psi"] == b.signals["psi"]


def test_drift_is_measurable_and_matches_target() -> None:
    source = make_source()
    result = CategoricalDriftInjector(make_spec()).inject(source)

    # Injected distribution moves toward the requested target.
    after = categorical_distribution(result.injected["contract"].astype(str).tolist())
    assert after["month_to_month"] > 0.75

    # PSI on the drifted feature is a significant shift (> 0.25 rule of thumb).
    assert isinstance(result.signals["psi"], float)
    assert result.signals["psi"] > 0.25


def test_change_is_one_factor() -> None:
    source = make_source()
    result = CategoricalDriftInjector(make_spec()).inject(source)

    source_region = categorical_distribution(source["region"].astype(str).tolist())
    after_region = categorical_distribution(result.injected["region"].astype(str).tolist())
    region_psi = population_stability_index(source_region, after_region)

    # The independent feature stays far more stable than the drifted one.
    assert region_psi < 0.1
    assert result.signals["psi"] > region_psi * 5


def test_injector_records_intervention_without_claiming_failure() -> None:
    source = make_source()
    result = CategoricalDriftInjector(make_spec()).inject(source)

    # The raw injector describes what it changed, before any model outcome is
    # measured; it cannot yet assert a failure or hidden failure cause.
    assert result.injected_change.feature == "contract"
    assert result.injected_change.intervention_type == "categorical_distribution_shift"
    assert not hasattr(result, "ground_truth")

    # ...but the evidence-safe signals never leak answer-key terms.
    signals_text = json.dumps(result.signals).lower()
    for term in FORBIDDEN_ANSWER_KEY_TERMS:
        assert term not in signals_text


def test_reversibility_check() -> None:
    source = make_source()
    result = CategoricalDriftInjector(make_spec()).inject(source)

    source_dist = categorical_distribution(source["contract"].astype(str).tolist())
    # Comparing the source to itself yields ~0 PSI: the spike is caused only by
    # the injection, not by the metric itself.
    assert population_stability_index(source_dist, source_dist) < 1e-9
    assert result.signals["psi"] > 0.25
