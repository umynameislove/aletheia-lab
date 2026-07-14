"""Injection correctness: exact size, determinism, fail-closed, measurable drift."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from aletheia_lab.benchmark.injectors import CategoricalDriftInjector, DriftSpec, _apportion


def _source(n=600, seed=0):
    rng = np.random.default_rng(seed)
    return pd.DataFrame(
        {
            "Contract": rng.choice(
                ["Month-to-month", "One year", "Two year"], size=n, p=[0.5, 0.3, 0.2]
            ),
            "other": rng.integers(0, 5, size=n),
        }
    )


def _spec(seed=1, size=600):
    return DriftSpec(
        injection_id="s",
        feature="Contract",
        target_distribution={"Month-to-month": 0.8, "One year": 0.12, "Two year": 0.08},
        output_size=size,
        seed=seed,
    )


@pytest.mark.parametrize("n", [7043, 600, 101, 7, 3])
def test_apportionment_sums_exactly(n):
    counts = _apportion({"a": 0.8, "b": 0.12, "c": 0.08}, n)
    assert sum(counts.values()) == n
    assert all(v >= 0 for v in counts.values())


def test_output_size_is_exact():
    result = CategoricalDriftInjector(_spec(size=600)).inject(_source())
    assert len(result.injected) == 600


def test_same_seed_same_output():
    a = CategoricalDriftInjector(_spec(seed=1)).inject(_source())
    b = CategoricalDriftInjector(_spec(seed=1)).inject(_source())
    pd.testing.assert_frame_equal(a.injected, b.injected)


def test_different_seed_changes_membership():
    a = CategoricalDriftInjector(_spec(seed=1)).inject(_source())
    b = CategoricalDriftInjector(_spec(seed=2)).inject(_source())
    assert not a.injected.equals(b.injected)


def test_absent_category_fails_closed():
    spec = DriftSpec(
        injection_id="s",
        feature="Contract",
        target_distribution={"Nonexistent": 1.0},
        output_size=100,
        seed=1,
    )
    with pytest.raises(ValueError):
        CategoricalDriftInjector(spec).inject(_source())


def test_missing_feature_fails_closed():
    spec = DriftSpec(
        injection_id="s", feature="NoSuchColumn", target_distribution={"x": 1.0}, seed=1
    )
    with pytest.raises(ValueError):
        CategoricalDriftInjector(spec).inject(_source())


def test_five_targets_produce_distinct_psi():
    targets = [
        {"Month-to-month": 0.8, "One year": 0.12, "Two year": 0.08},
        {"Month-to-month": 0.7, "One year": 0.18, "Two year": 0.12},
        {"Month-to-month": 0.9, "One year": 0.06, "Two year": 0.04},
        {"Month-to-month": 0.4, "One year": 0.30, "Two year": 0.30},
        {"Month-to-month": 0.6, "One year": 0.20, "Two year": 0.20},
    ]
    psis = []
    for i, target in enumerate(targets, start=1):
        spec = DriftSpec(
            injection_id=f"s{i}",
            feature="Contract",
            target_distribution=target,
            output_size=600,
            seed=i,
        )
        psis.append(
            round(float(CategoricalDriftInjector(spec).inject(_source()).signals["psi"]), 6)
        )
    assert len(set(psis)) == 5


def test_negative_weight_fails_closed():
    spec = DriftSpec(
        injection_id="s",
        feature="Contract",
        target_distribution={"Month-to-month": 1.2, "One year": -0.2},
        seed=1,
    )
    with pytest.raises(ValueError):
        CategoricalDriftInjector(spec).inject(_source())


def test_empty_distribution_fails_closed():
    spec = DriftSpec(injection_id="s", feature="Contract", target_distribution={}, seed=1)
    with pytest.raises(ValueError):
        CategoricalDriftInjector(spec).inject(_source())


def test_non_finite_weight_fails_closed():
    for bad in (float("inf"), float("nan")):
        spec = DriftSpec(
            injection_id="s",
            feature="Contract",
            target_distribution={"Month-to-month": bad},
            seed=1,
        )
        with pytest.raises(ValueError):
            CategoricalDriftInjector(spec).inject(_source())


def test_non_positive_output_size_fails_closed():
    spec = DriftSpec(
        injection_id="s",
        feature="Contract",
        target_distribution={"Month-to-month": 1.0},
        output_size=0,
        seed=1,
    )
    with pytest.raises(ValueError):
        CategoricalDriftInjector(spec).inject(_source())
