"""Separate finite-measure change, sampling evidence and diagnosis eligibility.

These checks do not supersede any registered mechanism admission policy.
Distances describe realised measures; tests have explicit sampling contracts.
Eligibility is conditional on supplied measurements and a finite rival catalogue,
not a certificate of a unique cause in an unrestricted deployment.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

import numpy as np


def _matrix(values: np.ndarray) -> np.ndarray:
    array = np.asarray(values, dtype=np.float64)
    if array.ndim != 2 or min(array.shape) == 0 or not np.isfinite(array).all():
        raise ValueError("measure requires a non-empty finite matrix")
    return array


def _weights(count: int, weights: np.ndarray | None) -> np.ndarray:
    value = np.ones(count) if weights is None else np.asarray(weights, dtype=np.float64)
    if (
        value.shape != (count,)
        or not np.isfinite(value).all()
        or (value < 0).any()
        or not 0 < float(value.sum()) < float("inf")
    ):
        raise ValueError("weights must be aligned, finite, nonnegative and nonzero")
    result: np.ndarray = value / value.sum()
    return result


def empirical_total_variation(
    source: np.ndarray,
    target: np.ndarray,
    *,
    source_weights: np.ndarray | None = None,
    target_weights: np.ndarray | None = None,
) -> float:
    """TV of discrete feature measures, aggregating equal rows and multiplicity.

    Exact numeric row equality is the support relation. In particular content
    duplicates do not create extra independent observations. No p-value or
    continuous-population distance is implied by this finite-support quantity.
    """
    x, y = _matrix(source), _matrix(target)
    if x.shape[1] != y.shape[1]:
        raise ValueError("feature dimensions differ")
    mass: dict[tuple[float, ...], float] = defaultdict(float)
    for sign, values, weights in (
        (1, x, _weights(len(x), source_weights)),
        (-1, y, _weights(len(y), target_weights)),
    ):
        for row, weight in zip(values, weights, strict=True):
            mass[tuple(float(value) for value in row)] += sign * float(weight)
    return float(sum(abs(value) for value in mass.values()) / 2)


def _kernel(values: np.ndarray, bandwidth_squared: float) -> np.ndarray:
    if not np.isfinite(bandwidth_squared) or bandwidth_squared <= 0:
        raise ValueError("bandwidth squared must be positive and finite")
    differences = values[:, None, :] - values[None, :, :]
    result: np.ndarray = np.exp(-np.sum(differences**2, axis=2) / (2 * bandwidth_squared))
    return result


def empirical_mmd_squared(
    source: np.ndarray,
    target: np.ndarray,
    *,
    bandwidth_squared: float = 1.0,
    source_weights: np.ndarray | None = None,
    target_weights: np.ndarray | None = None,
) -> float:
    """Full biased RBF distance of weighted empirical measures, not a test."""
    x, y = _matrix(source), _matrix(target)
    if x.shape[1] != y.shape[1]:
        raise ValueError("feature dimensions differ")
    difference = np.concatenate(
        (_weights(len(x), source_weights), -_weights(len(y), target_weights))
    )
    gram = _kernel(np.concatenate((x, y)), bandwidth_squared)
    return max(0.0, float(difference @ gram @ difference))


@dataclass(frozen=True)
class SamplingTest:
    status: Literal["tested", "unidentified", "outside_declared_support"]
    statistic: float | None
    p_value: float | None
    reason: str
    resamples: int


def iid_mmd_permutation(
    source: np.ndarray,
    target: np.ndarray,
    *,
    source_origins: Sequence[str],
    target_origins: Sequence[str],
    independent_draws_declared: bool,
    resamples: int = 199,
    seed: int = 0,
    bandwidth_squared: float = 1.0,
) -> SamplingTest:
    """Two-sample permutation under declared independent, exchangeable draws.

    Origin IDs identify draws, not feature values. The guard detects known
    repeated/shared ancestry; disjoint IDs alone cannot establish independence.
    The bandwidth is supplied before labels/permutations, never arm-selected.
    """
    if type(independent_draws_declared) is not bool:
        raise ValueError("independent-draw declaration must be a boolean")
    x, y = _matrix(source), _matrix(target)
    if x.shape[1] != y.shape[1] or len(source_origins) != len(x) or len(target_origins) != len(y):
        raise ValueError("sampling origins and feature matrices must align")
    if resamples < 99:
        raise ValueError("at least 99 resamples required")
    origins = tuple(source_origins) + tuple(target_origins)
    if not all(isinstance(value, str) and value for value in origins):
        raise ValueError("origin IDs must be non-empty strings")
    if not independent_draws_declared or len(set(origins)) != len(origins):
        return SamplingTest(
            "unidentified", None, None, "independent_draw_contract_not_established", 0
        )
    gram = _kernel(np.concatenate((x, y)), bandwidth_squared)
    weights = np.concatenate((np.full(len(x), 1 / len(x)), np.full(len(y), -1 / len(y))))
    observed = float(weights @ gram @ weights)
    rng = np.random.default_rng(seed)
    exceedances = 0
    for _ in range(resamples):
        permuted = weights[rng.permutation(len(weights))]
        exceedances += int(float(permuted @ gram @ permuted) >= observed - 1e-12)
    return SamplingTest(
        "tested",
        max(0.0, observed),
        (exceedances + 1) / (resamples + 1),
        "iid_exchangeability_assumed_not_inferred_from_nonrejection",
        resamples,
    )


def fixed_pool_resampling_test(
    pool: np.ndarray,
    target: np.ndarray,
    *,
    ancestor_indices: Sequence[int],
    resamples: int = 199,
    seed: int = 0,
    bandwidth_squared: float = 1.0,
) -> SamplingTest:
    """Conditional GOF under uniform iid draws from this fixed empirical pool.

    The pool is conditioned on, not a second independent sample. The null is
    this declared generator, not unknown population conditional invariance.
    Ancestry must match actual target content; a changed feature leaves support.
    """
    x, y = _matrix(pool), _matrix(target)
    if x.shape[1] != y.shape[1] or len(ancestor_indices) != len(y):
        raise ValueError("ancestry and target matrix must align")
    if any(
        isinstance(value, bool) or not isinstance(value, (int, np.integer))
        for value in ancestor_indices
    ):
        raise ValueError("ancestor indices must be integers")
    indices = np.asarray(ancestor_indices, dtype=int)
    if (indices < 0).any() or (indices >= len(x)).any() or resamples < 99:
        raise ValueError("invalid ancestry or resampling budget")
    if not np.array_equal(x[indices], y):
        return SamplingTest("outside_declared_support", None, None, "ancestor_content_differs", 0)
    gram = _kernel(x, bandwidth_squared)
    weights = np.bincount(indices, minlength=len(x)) / len(y) - 1 / len(x)
    observed = float(weights @ gram @ weights)
    rng = np.random.default_rng(seed)
    counts = rng.multinomial(len(y), np.full(len(x), 1 / len(x)), size=resamples)
    differences = counts / len(y) - 1 / len(x)
    statistics = np.einsum("bi,ij,bj->b", differences, gram, differences)
    exceedances = int(np.count_nonzero(statistics >= observed - 1e-12))
    return SamplingTest(
        "tested",
        max(0.0, observed),
        (exceedances + 1) / (resamples + 1),
        "conditional_uniform_fixed_pool_null_only",
        resamples,
    )


@dataclass(frozen=True)
class GroundTruthDecision:
    status: str
    verified_consequence: bool
    identifiable_in_catalogue: bool
    reason: str


def assess_ground_truth(
    *,
    fidelity_verified: bool,
    harmful_loss_delta: float,
    repair_residual: float,
    sham_residual: float,
    compatible_mechanisms: Sequence[str],
    minimum_harm: float = 0.01,
    control_tolerance: float = 1e-10,
) -> GroundTruthDecision:
    """Route supplied evidence without conflating injected label with cause.

    Singletons are sufficient only within a complete/truthful supplied catalogue.
    A repair effect is a controlled contrast, not unrestricted causal discovery.
    """
    if type(fidelity_verified) is not bool:
        raise ValueError("fidelity verification must be a boolean")
    numbers = (harmful_loss_delta, repair_residual, sham_residual, minimum_harm, control_tolerance)
    if (
        not all(np.isfinite(value) for value in numbers)
        or minimum_harm <= 0
        or control_tolerance < 0
    ):
        raise ValueError("effect measurements and policy must be finite and valid")
    if not all(isinstance(value, str) and value for value in compatible_mechanisms):
        raise ValueError("compatible mechanisms must be non-empty strings")
    mechanisms = set(compatible_mechanisms)
    if not fidelity_verified:
        return GroundTruthDecision("invalid_intervention", False, False, "fidelity_not_verified")
    if harmful_loss_delta < minimum_harm:
        return GroundTruthDecision("not_harmful", False, len(mechanisms) == 1, "harm_gate_not_met")
    if abs(repair_residual) > control_tolerance or abs(sham_residual) > control_tolerance:
        return GroundTruthDecision(
            "unverified_effect", False, len(mechanisms) == 1, "repair_or_sham_failed"
        )
    if not mechanisms:
        return GroundTruthDecision("unidentified", True, False, "catalogue_or_evidence_incomplete")
    if len(mechanisms) > 1:
        return GroundTruthDecision("ambiguous", True, False, "multiple_compatible_mechanisms")
    return GroundTruthDecision(
        "supported_in_catalogue", True, True, "bounded_controlled_contrast_and_witness"
    )
