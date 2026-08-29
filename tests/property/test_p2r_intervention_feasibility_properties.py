"""Generative invariants for P2R intervention positivity and capacity."""

from __future__ import annotations

import math

from hypothesis import given
from hypothesis import strategies as st

from aletheia_lab.benchmark.p2.p2r_replication_failure import (
    CAPACITY_RESERVE,
    DECLARED_MAGNITUDE,
    P2RFeatureCapacity,
    assess_feature_capacity,
)


@given(
    sealed_count=st.integers(min_value=20, max_value=500),
    mode_count=st.integers(min_value=0, max_value=500),
)
def test_directional_capacity_is_exactly_the_susceptible_population(
    sealed_count: int, mode_count: int
) -> None:
    bounded_mode = min(mode_count, sealed_count)
    sealed = ("mode",) * bounded_mode + ("other",) * (sealed_count - bounded_mode)
    capacity = assess_feature_capacity(
        feature="feature",
        training_values=("mode",) * 7 + ("other",) * 3,
        sealed_values=sealed,
    )
    target = math.floor(DECLARED_MAGNITUDE * sealed_count)
    reserve = math.ceil(CAPACITY_RESERVE * sealed_count)

    assert capacity.data_drift_capacity_count == sealed_count - bounded_mode
    assert capacity.preprocessing_capacity_count == bounded_mode
    assert capacity.data_drift_feasible == (sealed_count - bounded_mode >= target)
    assert capacity.preprocessing_feasible == (bounded_mode >= target)
    assert capacity.jointly_feasible_with_reserve == (
        min(bounded_mode, sealed_count - bounded_mode) >= target + reserve
    )


@given(
    first=st.integers(min_value=3, max_value=100),
    second=st.integers(min_value=3, max_value=100),
)
def test_capacity_receipt_is_deterministic_and_content_addressed(
    first: int, second: int
) -> None:
    one = assess_feature_capacity(
        feature="feature",
        training_values=("a",) * 6 + ("b",) * 4,
        sealed_values=("a",) * first + ("b",) * second,
    )
    two = assess_feature_capacity(
        feature="feature",
        training_values=("a",) * 6 + ("b",) * 4,
        sealed_values=("a",) * first + ("b",) * second,
    )

    assert one == two
    assert one.canonical_sha256() == two.canonical_sha256()
    assert P2RFeatureCapacity.model_validate(one.model_dump()) == one
