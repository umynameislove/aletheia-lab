"""Known-answer tests: measures, acquisition law and effect != identification."""

from __future__ import annotations

import math

import numpy as np
import pytest

from aletheia_lab.benchmark.p2.ground_truth_validity import (
    assess_ground_truth,
    empirical_mmd_squared,
    empirical_total_variation,
    fixed_pool_resampling_test,
    iid_mmd_permutation,
)


def test_uniform_duplication_is_measure_identity_not_new_information() -> None:
    pool = np.array([[0.0], [1.0], [2.0]])
    repeated = np.repeat(pool, 4, axis=0)
    assert empirical_total_variation(pool, repeated) == pytest.approx(0, abs=1e-15)
    assert empirical_mmd_squared(pool, repeated) == pytest.approx(0, abs=1e-15)
    assert empirical_mmd_squared(pool, repeated[::-1]) == pytest.approx(0, abs=1e-15)


def test_unequal_multiplicities_change_measure_and_weights_restore_it() -> None:
    pool = np.array([[0.0], [1.0]])
    target = np.array([[0.0], [0.0], [1.0]])
    assert empirical_total_variation(pool, target) == pytest.approx(1 / 6)
    assert empirical_mmd_squared(pool, target) > 0
    weights = np.array([0.5, 0.5, 1.0])
    assert empirical_total_variation(pool, target, target_weights=weights) == pytest.approx(0)
    assert empirical_mmd_squared(pool, target, target_weights=weights) == pytest.approx(
        0, abs=1e-15
    )


def test_signed_zero_and_repeated_feature_atoms_are_numeric_equality() -> None:
    assert empirical_total_variation(np.array([[0.0]]), np.array([[-0.0], [0.0]])) == 0


@pytest.mark.parametrize("invalid", [np.array([[math.nan]]), np.array([]), np.ones((2, 2, 2))])
def test_invalid_measures_are_not_reported_as_equal(invalid: np.ndarray) -> None:
    with pytest.raises(ValueError):
        empirical_total_variation(invalid, np.array([[0.0]]))


@pytest.mark.parametrize(
    "weights", [np.array([0.0, 0.0]), np.array([-1.0, 2.0]), np.array([math.inf, 1.0]), np.ones(1)]
)
def test_invalid_weights_fail_closed(weights: np.ndarray) -> None:
    with pytest.raises(ValueError):
        empirical_mmd_squared(np.zeros((2, 1)), np.ones((2, 1)), source_weights=weights)


def test_generator_compatibility_does_not_assert_realized_equality() -> None:
    pool = np.array([[0.0], [1.0]])
    target = np.array([[0.0], [0.0]])
    assert empirical_total_variation(pool, target) == 0.5
    result = fixed_pool_resampling_test(pool, target, ancestor_indices=(0, 0), seed=11)
    assert result.status == "tested"
    assert result.p_value is not None and result.p_value > 0.05
    assert result.reason == "conditional_uniform_fixed_pool_null_only"


def test_changed_ancestor_content_is_structural_failure_not_small_p() -> None:
    result = fixed_pool_resampling_test(
        np.array([[0.0], [1.0]]), np.array([[2.0]]), ancestor_indices=(0,)
    )
    assert result.status == "outside_declared_support" and result.p_value is None


def test_shared_ancestry_cannot_enter_iid_test_despite_distinct_occurrences() -> None:
    pool = np.array([[0.0], [1.0]])
    result = iid_mmd_permutation(
        pool,
        pool,
        source_origins=("a", "b"),
        target_origins=("a", "b"),
        independent_draws_declared=True,
    )
    assert result.status == "unidentified" and result.p_value is None
    distinct = iid_mmd_permutation(
        pool,
        pool,
        source_origins=("a", "b"),
        target_origins=("c", "d"),
        independent_draws_declared=False,
    )
    assert distinct.status == "unidentified"


def test_equal_values_are_allowed_for_independent_discrete_draws() -> None:
    result = iid_mmd_permutation(
        np.zeros((4, 1)),
        np.zeros((4, 1)),
        source_origins=("a", "b", "c", "d"),
        target_origins=("e", "f", "g", "h"),
        independent_draws_declared=True,
    )
    assert result.status == "tested" and result.p_value == 1.0


def test_continuous_iid_draws_have_disjoint_empirical_support_even_under_null() -> None:
    rng = np.random.default_rng(82)
    x, y = rng.normal(size=(10, 2)), rng.normal(size=(10, 2))
    assert empirical_total_variation(x, y) == pytest.approx(1.0)
    # TV=1 of these finite measures is not a population shift verdict.


@pytest.mark.parametrize("kind", ["indices", "budget", "bandwidth", "dimensions"])
def test_sampling_invalid_inputs_fail_closed(kind: str) -> None:
    with pytest.raises(ValueError):
        fixed_pool_resampling_test(
            np.array([[0.0], [1.0]]),
            np.array([[0.0, 1.0]]) if kind == "dimensions" else np.array([[0.0]]),
            ancestor_indices=(True,) if kind == "indices" else (0,),
            resamples=1 if kind == "budget" else 199,
            bandwidth_squared=-1 if kind == "bandwidth" else 1,
        )


def _decision(**kwargs: object):
    parameters = dict(
        fidelity_verified=True,
        harmful_loss_delta=0.2,
        repair_residual=0.0,
        sham_residual=0.0,
        compatible_mechanisms=("input_transform",),
    )
    parameters.update(kwargs)
    return assess_ground_truth(**parameters)


def test_fidelity_harm_and_identifiability_are_distinct_gates() -> None:
    assert _decision(fidelity_verified=False).status == "invalid_intervention"
    assert _decision(harmful_loss_delta=0.0).status == "not_harmful"
    assert _decision(repair_residual=0.2).status == "unverified_effect"
    assert _decision(sham_residual=0.02).status == "unverified_effect"
    assert _decision(compatible_mechanisms=()).status == "unidentified"
    ambiguous = _decision(compatible_mechanisms=("input_transform", "model_weights"))
    assert ambiguous.status == "ambiguous" and ambiguous.verified_consequence
    assert not ambiguous.identifiable_in_catalogue
    assert _decision().status == "supported_in_catalogue"


def test_nonfinite_effect_cannot_be_admitted() -> None:
    with pytest.raises(ValueError):
        _decision(harmful_loss_delta=math.nan)


@pytest.mark.parametrize("flag", ["false", 0, 1, None])
def test_nonboolean_trust_flags_cannot_bypass_gates(flag: object) -> None:
    with pytest.raises(ValueError):
        _decision(fidelity_verified=flag)
    with pytest.raises(ValueError):
        iid_mmd_permutation(
            np.zeros((2, 1)),
            np.ones((2, 1)),
            source_origins=("a", "b"),
            target_origins=("c", "d"),
            independent_draws_declared=flag,
        )


def test_label_shift_and_conditional_change_have_same_visible_unlabeled_law() -> None:
    source_zero, source_one = 0.2, 0.8
    world_prior = (1 - 2 / 3) * source_zero + (2 / 3) * source_one
    world_conditional = 0.5 * 0.4 + 0.5 * 0.8
    assert world_prior == pytest.approx(world_conditional)
    estimate = (world_conditional - source_zero) / (source_one - source_zero)
    assert estimate == pytest.approx(2 / 3)
    # Identical unlabeled target observation; estimator 'success' is conditional.
