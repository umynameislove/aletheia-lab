"""Generative invariants for v3 corruption and nuisance controls."""

from __future__ import annotations

import math

from hypothesis import given
from hypothesis import strategies as st

from aletheia_lab.benchmark.p2.confirmatory_v3_runtime import (
    apply_directional_corruption,
    build_prior_environment,
    prior_match_sample_weights,
    reciprocal_control_targets,
)


@st.composite
def binary_population(draw: st.DrawFn) -> tuple[tuple[str, ...], tuple[int, ...]]:
    negative = draw(st.integers(min_value=10, max_value=80))
    positive = draw(st.integers(min_value=10, max_value=80))
    labels = (0,) * negative + (1,) * positive
    ids = tuple(f"record-{index:03d}" for index in range(len(labels)))
    return ids, labels


@given(
    population=binary_population(),
    direction=st.sampled_from(("yes_to_no", "no_to_yes")),
    rate=st.sampled_from((0.1, 0.2, 0.3)),
    seed=st.integers(min_value=1, max_value=100000),
)
def test_directional_corruption_is_exact_deterministic_and_source_only(
    population: tuple[tuple[str, ...], tuple[int, ...]],
    direction: str,
    rate: float,
    seed: int,
) -> None:
    record_ids, labels = population
    first, receipt = apply_directional_corruption(
        dataset_id="synthetic",
        record_ids=record_ids,
        clean_targets=labels,
        direction=direction,
        conditional_rate=rate,
        seed=seed,
    )
    second, repeated = apply_directional_corruption(
        dataset_id="synthetic",
        record_ids=record_ids,
        clean_targets=labels,
        direction=direction,
        conditional_rate=rate,
        seed=seed,
    )
    source = 1 if direction == "yes_to_no" else 0
    changed = tuple(index for index, pair in enumerate(zip(labels, first, strict=True)) if pair[0] != pair[1])
    assert first == second
    assert receipt == repeated
    assert len(changed) == math.floor(rate * labels.count(source))
    assert all(labels[index] == source for index in changed)
    assert receipt.achieved_conditional_rate == len(changed) / labels.count(source)


@given(
    negative=st.integers(min_value=2, max_value=200),
    positive=st.integers(min_value=2, max_value=200),
    target=st.floats(
        min_value=0.01,
        max_value=0.99,
        allow_nan=False,
        allow_infinity=False,
    ),
)
def test_prior_match_weights_reconcile_exact_effective_prior(
    negative: int, positive: int, target: float
) -> None:
    labels = (0,) * negative + (1,) * positive
    weights = prior_match_sample_weights(labels, target_positive_prior=target)
    effective = sum(weight * label for weight, label in zip(weights, labels, strict=True)) / sum(
        weights
    )
    assert math.isclose(effective, target, abs_tol=1e-12)
    assert math.isclose(sum(weights) / len(weights), 1.0, abs_tol=1e-12)
    assert all(weight > 0.0 for weight in weights)


@given(
    population=binary_population(),
    direction=st.sampled_from(("yes_to_no", "no_to_yes")),
    rate=st.sampled_from((0.1, 0.2, 0.3)),
    seed=st.integers(min_value=1, max_value=100000),
)
def test_reciprocal_control_preserves_prevalence_and_pair_cardinality(
    population: tuple[tuple[str, ...], tuple[int, ...]],
    direction: str,
    rate: float,
    seed: int,
) -> None:
    record_ids, labels = population
    controlled, source_ids, opposite_ids = reciprocal_control_targets(
        dataset_id="synthetic",
        record_ids=record_ids,
        clean_targets=labels,
        direction=direction,
        conditional_rate=rate,
        seed=seed,
    )
    assert len(source_ids) == len(opposite_ids)
    assert len(set((*source_ids, *opposite_ids))) == len(source_ids) + len(opposite_ids)
    assert sum(controlled) == sum(labels)
    assert sum(left != right for left, right in zip(controlled, labels, strict=True)) == 2 * len(
        source_ids
    )


@given(
    population=binary_population(),
    multiplier=st.sampled_from((0.25, 1.0, 4.0)),
    seed=st.integers(min_value=1, max_value=100000),
)
def test_prior_environment_is_deterministic_and_count_preserving(
    population: tuple[tuple[str, ...], tuple[int, ...]],
    multiplier: float,
    seed: int,
) -> None:
    record_ids, labels = population
    indices, environment = build_prior_environment(
        dataset_id="synthetic",
        record_ids=record_ids,
        labels=labels,
        odds_multiplier=multiplier,
        environment_seed=seed,
    )
    repeated_indices, repeated = build_prior_environment(
        dataset_id="synthetic",
        record_ids=record_ids,
        labels=labels,
        odds_multiplier=multiplier,
        environment_seed=seed,
    )
    assert indices == repeated_indices
    assert environment == repeated
    assert len(indices) == len(labels)
    assert environment.target_negative_count + environment.target_positive_count == len(labels)
    if multiplier == 1.0:
        assert indices == tuple(range(len(labels)))
        assert environment.neutral_identity
