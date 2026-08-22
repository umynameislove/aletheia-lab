"""Registered label-shift estimators, diagnostics, and proper-score kernels."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Annotated, Final, Literal, NoReturn

import numpy as np
from pydantic import BaseModel, ConfigDict, Field, model_validator

from aletheia_lab.benchmark.p2.canonical import canonical_sha256
from aletheia_lab.benchmark.p2.confirmatory_v3_runtime import (
    PROTOCOL_SHA256,
    V3RuntimeError,
    stabilize_numeric_evidence,
)
from aletheia_lab.benchmark.p2.identity import SHA256_PATTERN

SHIFT_SCHEMA_VERSION: Final[Literal["p2-v3-label-shift-estimate/1"]] = (
    "p2-v3-label-shift-estimate/1"
)
MMD_SCHEMA_VERSION: Final[Literal["p2-v3-classwise-linear-mmd/1"]] = (
    "p2-v3-classwise-linear-mmd/1"
)
SIMPLEX_TOLERANCE: Final[float] = 1e-6
RBF_MIN_BANDWIDTH_SQUARED: Final[float] = 1e-12

Sha256 = Annotated[str, Field(pattern=SHA256_PATTERN)]
EstimatorName = Literal["unadjusted_v2", "oracle_prior_ratio", "bbse", "mlls_em", "rlls"]
EstimateStatus = Literal["ok", "abstain"]


def _fail(message: str) -> NoReturn:
    raise V3RuntimeError(message)


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


def _probabilities(values: Sequence[float], *, label: str) -> np.ndarray:
    result = np.asarray(tuple(values), dtype=np.float64)
    if result.ndim != 1 or result.size < 2 or not np.isfinite(result).all():
        _fail(f"{label} must be a finite probability vector")
    if np.any(result <= 0.0) or np.any(result >= 1.0):
        _fail(f"{label} probabilities must be strictly inside (0, 1)")
    return result


def _binary_targets(values: Sequence[int], *, label: str) -> np.ndarray:
    result = np.asarray(tuple(values), dtype=np.int64)
    if result.ndim != 1 or result.size < 2 or set(result.tolist()) != {0, 1}:
        _fail(f"{label} must contain both binary classes")
    return result


def _source_prior(targets: np.ndarray) -> np.ndarray:
    positive = float(np.mean(targets))
    return np.asarray((1.0 - positive, positive), dtype=np.float64)


def _posterior_matrix(probabilities: np.ndarray) -> np.ndarray:
    return np.column_stack((1.0 - probabilities, probabilities)).astype(np.float64)


def _soft_confusion(probabilities: np.ndarray, targets: np.ndarray) -> np.ndarray:
    posterior = _posterior_matrix(probabilities)
    # Columns are true source classes; rows are mean predicted class probabilities.
    return np.column_stack(
        tuple(np.mean(posterior[targets == label], axis=0) for label in (0, 1))
    )


def _simplex_or_none(values: np.ndarray) -> np.ndarray | None:
    if values.shape != (2,) or not np.isfinite(values).all():
        return None
    total = float(np.sum(values))
    if abs(total - 1.0) > SIMPLEX_TOLERANCE or np.any(values < -SIMPLEX_TOLERANCE) or np.any(
        values > 1.0 + SIMPLEX_TOLERANCE
    ):
        return None
    normalized = values / total
    if np.any(normalized <= 0.0) or np.any(normalized >= 1.0):
        return None
    return normalized


def adjust_probabilities_for_prior(
    probabilities: Sequence[float],
    *,
    source_positive_prior: float,
    target_positive_prior: float,
) -> tuple[float, ...]:
    """Apply exact binary prior-ratio posterior reweighting without clipping."""

    values = _probabilities(probabilities, label="prior adjustment")
    if not 0.0 < source_positive_prior < 1.0 or not 0.0 < target_positive_prior < 1.0:
        _fail("source and target priors must be strictly inside (0, 1)")
    ratios = np.asarray(
        (
            (1.0 - target_positive_prior) / (1.0 - source_positive_prior),
            target_positive_prior / source_positive_prior,
        ),
        dtype=np.float64,
    )
    weighted = _posterior_matrix(values) * ratios
    denominators = np.sum(weighted, axis=1)
    if not np.isfinite(weighted).all() or np.any(denominators <= 0.0):
        _fail("prior adjustment produced invalid posterior weights")
    adjusted = weighted[:, 1] / denominators
    if np.any(adjusted <= 0.0) or np.any(adjusted >= 1.0):
        _fail("prior adjustment produced boundary probabilities")
    return tuple(float(value) for value in adjusted)


class ShiftEstimate(_StrictFrozenModel):
    schema_version: Literal["p2-v3-label-shift-estimate/1"] = SHIFT_SCHEMA_VERSION
    protocol_sha256: Sha256 = PROTOCOL_SHA256
    estimator: EstimatorName
    status: EstimateStatus
    source_positive_prior: float
    target_positive_prior: float | None
    condition_number: float | None
    iterations: int | None
    reason: str | None
    adjusted_probabilities: tuple[float, ...]

    @model_validator(mode="after")
    def _status_is_fail_closed(self) -> ShiftEstimate:
        if not 0.0 < self.source_positive_prior < 1.0:
            raise ValueError("shift source prior must be strictly interior")
        if self.status == "ok":
            if (
                self.target_positive_prior is None
                or not 0.0 < self.target_positive_prior < 1.0
                or self.reason is not None
                or not self.adjusted_probabilities
            ):
                raise ValueError("successful shift estimate is incomplete")
            if any(
                not math.isfinite(value) or not 0.0 < value < 1.0
                for value in self.adjusted_probabilities
            ):
                raise ValueError("successful adjusted probabilities are invalid")
        elif (
            self.target_positive_prior is not None
            or self.reason is None
            or self.adjusted_probabilities
        ):
            raise ValueError("abstained shift estimate must not expose a partial result")
        return self

    def canonical_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json"))


def _abstain(
    estimator: EstimatorName,
    source_positive_prior: float,
    reason: str,
    *,
    condition_number: float | None = None,
    iterations: int | None = None,
) -> ShiftEstimate:
    return ShiftEstimate(
        estimator=estimator,
        status="abstain",
        source_positive_prior=stabilize_numeric_evidence(source_positive_prior),
        target_positive_prior=None,
        condition_number=(
            None
            if condition_number is None
            else stabilize_numeric_evidence(condition_number)
        ),
        iterations=iterations,
        reason=reason,
        adjusted_probabilities=(),
    )


def estimate_label_shift(
    *,
    estimator: EstimatorName,
    development_probabilities: Sequence[float],
    development_targets: Sequence[int],
    target_probabilities: Sequence[float],
    oracle_target_positive_prior: float | None = None,
    bbse_condition_number_max: float = 1e8,
    mlls_max_iter: int = 1000,
    mlls_tolerance: float = 1e-8,
    rlls_l2_regularization: float = 0.01,
) -> ShiftEstimate:
    """Estimate the target prior using one of the five frozen estimators."""

    development = _probabilities(development_probabilities, label="development")
    targets = _binary_targets(development_targets, label="development targets")
    target = _probabilities(target_probabilities, label="target")
    if development.shape != targets.shape:
        _fail("development probabilities and targets must align")
    source = _source_prior(targets)
    source_positive = float(source[1])
    target_prior: np.ndarray | None = None
    condition_number: float | None = None
    iterations: int | None = None

    if estimator == "unadjusted_v2":
        target_prior = source
    elif estimator == "oracle_prior_ratio":
        if oracle_target_positive_prior is None or not 0.0 < oracle_target_positive_prior < 1.0:
            return _abstain(estimator, source_positive, "oracle target prior is unavailable")
        target_prior = np.asarray(
            (1.0 - oracle_target_positive_prior, oracle_target_positive_prior),
            dtype=np.float64,
        )
    elif estimator in {"bbse", "rlls"}:
        confusion = _soft_confusion(development, targets)
        target_moment = np.mean(_posterior_matrix(target), axis=0)
        condition_number = float(np.linalg.cond(confusion))
        if not math.isfinite(condition_number) or condition_number > bbse_condition_number_max:
            return _abstain(
                estimator,
                source_positive,
                "soft confusion matrix is ill-conditioned",
                condition_number=None,
            )
        try:
            if estimator == "bbse":
                candidate = np.linalg.solve(confusion, target_moment)
            else:
                if not math.isfinite(rlls_l2_regularization) or rlls_l2_regularization <= 0.0:
                    _fail("RLLS regularization must be finite and positive")
                quadratic = confusion.T @ confusion + rlls_l2_regularization * np.eye(2)
                linear = confusion.T @ target_moment + rlls_l2_regularization * source
                # Equality-constrained ridge: min ||Cq-m||^2 + lambda||q-p_s||^2,
                # subject to 1^T q = 1.  Positivity is validated after the solve.
                system = np.block(
                    [
                        [quadratic, np.ones((2, 1), dtype=np.float64)],
                        [np.ones((1, 2), dtype=np.float64), np.zeros((1, 1))],
                    ]
                )
                rhs = np.concatenate((linear, np.asarray((1.0,), dtype=np.float64)))
                candidate = np.linalg.solve(system, rhs)[:2]
        except np.linalg.LinAlgError:
            return _abstain(
                estimator,
                source_positive,
                "shift linear system is singular",
                condition_number=condition_number,
            )
        target_prior = _simplex_or_none(candidate)
        if target_prior is None:
            return _abstain(
                estimator,
                source_positive,
                "unconstrained shift estimate lies outside the simplex",
                condition_number=condition_number,
            )
    else:
        posterior = _posterior_matrix(target)
        current = source.copy()
        for iteration in range(1, mlls_max_iter + 1):
            ratios = current / source
            weighted = posterior * ratios
            denominators = np.sum(weighted, axis=1)
            if not np.isfinite(weighted).all() or np.any(denominators <= 0.0):
                return _abstain(
                    estimator,
                    source_positive,
                    "MLLS produced invalid posterior weights",
                    iterations=iteration,
                )
            updated = np.mean(weighted / denominators[:, None], axis=0)
            normalized = _simplex_or_none(updated)
            if normalized is None:
                return _abstain(
                    estimator,
                    source_positive,
                    "MLLS iterate left the simplex",
                    iterations=iteration,
                )
            if float(np.max(np.abs(normalized - current))) <= mlls_tolerance:
                target_prior = normalized
                iterations = iteration
                break
            current = normalized
        if target_prior is None:
            return _abstain(
                estimator,
                source_positive,
                "MLLS did not converge",
                iterations=mlls_max_iter,
            )

    if target_prior is None:
        _fail("shift estimator reached an impossible incomplete state")
    stable_target_positive = stabilize_numeric_evidence(float(target_prior[1]))
    adjusted = (
        tuple(stabilize_numeric_evidence(float(value)) for value in target)
        if estimator == "unadjusted_v2"
        else adjust_probabilities_for_prior(
            target.tolist(),
            source_positive_prior=source_positive,
            target_positive_prior=stable_target_positive,
        )
    )
    return ShiftEstimate(
        estimator=estimator,
        status="ok",
        source_positive_prior=stabilize_numeric_evidence(source_positive),
        target_positive_prior=stable_target_positive,
        condition_number=(
            None
            if condition_number is None
            else stabilize_numeric_evidence(condition_number)
        ),
        iterations=iterations,
        reason=None,
        adjusted_probabilities=tuple(
            stabilize_numeric_evidence(value) for value in adjusted
        ),
    )


def reference_prior_standardized_losses(
    *, true_labels: Sequence[int], probabilities: Sequence[float]
) -> tuple[float, ...]:
    """Return record contributions whose mean is 50/50 standardized log loss."""

    labels = _binary_targets(true_labels, label="reference-prior targets")
    values = _probabilities(probabilities, label="reference-prior score")
    if labels.shape != values.shape:
        _fail("reference-prior labels and probabilities must align")
    losses = -(labels * np.log(values) + (1 - labels) * np.log1p(-values))
    count = labels.size
    counts = np.bincount(labels, minlength=2).astype(np.float64)
    weights = np.where(labels == 1, count / (2.0 * counts[1]), count / (2.0 * counts[0]))
    contributions = losses * weights
    if not np.isfinite(contributions).all() or np.any(contributions < 0.0):
        _fail("reference-prior score produced invalid contributions")
    return tuple(float(value) for value in contributions)


def reference_prior_standardized_log_loss(
    *, true_labels: Sequence[int], probabilities: Sequence[float]
) -> float:
    return float(
        np.mean(
            np.asarray(
                reference_prior_standardized_losses(
                    true_labels=true_labels,
                    probabilities=probabilities,
                ),
                dtype=np.float64,
            )
        )
    )


def _rank_rows(record_ids: Sequence[str], *, seed: int, label: int, role: str) -> tuple[int, ...]:
    return tuple(
        sorted(
            range(len(record_ids)),
            key=lambda index: (
                canonical_sha256(
                    {
                        "schema_version": MMD_SCHEMA_VERSION,
                        "domain": "aletheia-lab/v3.1/mmd-row-rank",
                        "seed": seed,
                        "class_label": label,
                        "role": role,
                        "record_id": record_ids[index],
                    }
                ),
                record_ids[index],
            ),
        )
    )


def _rbf(x: np.ndarray, y: np.ndarray, bandwidth_squared: float) -> np.ndarray:
    squared = np.sum((x - y) ** 2, axis=1)
    return np.exp(-squared / (2.0 * bandwidth_squared))


def _linear_mmd_statistic(x: np.ndarray, y: np.ndarray, bandwidth_squared: float) -> float:
    pair_count = min(len(x), len(y)) // 2
    if pair_count < 2:
        _fail("linear MMD requires at least four observations per sample")
    x = x[: pair_count * 2]
    y = y[: pair_count * 2]
    statistic = (
        _rbf(x[0::2], x[1::2], bandwidth_squared)
        + _rbf(y[0::2], y[1::2], bandwidth_squared)
        - _rbf(x[0::2], y[1::2], bandwidth_squared)
        - _rbf(x[1::2], y[0::2], bandwidth_squared)
    )
    return float(np.mean(statistic))


class MmdClassResult(_StrictFrozenModel):
    class_label: Literal[0, 1]
    source_count: int
    target_count: int
    balanced_count: int
    bandwidth_squared: float
    statistic: float
    permutation_p_value: float = Field(ge=0.0, le=1.0)
    resamples: int
    seed: int


class MmdDiagnostic(_StrictFrozenModel):
    schema_version: Literal["p2-v3-classwise-linear-mmd/1"] = MMD_SCHEMA_VERSION
    protocol_sha256: Sha256 = PROTOCOL_SHA256
    dataset_id: str
    representation: Literal["registered_preprocessed_model_inputs"]
    statistic: Literal["balanced_linear_time_unbiased_rbf_mmd_squared"]
    bandwidth: Literal["median_nonzero_deterministic_paired_squared_distance"]
    permutation_p_value: Literal["plus_one_greater_or_equal"]
    classes: tuple[MmdClassResult, MmdClassResult]

    @model_validator(mode="after")
    def _classes_are_complete(self) -> MmdDiagnostic:
        if tuple(item.class_label for item in self.classes) != (0, 1):
            raise ValueError("MMD diagnostic requires both classes in canonical order")
        return self

    def canonical_sha256(self) -> str:
        return canonical_sha256(self.model_dump(mode="json"))


def classwise_mmd_diagnostic(
    *,
    dataset_id: str,
    source_matrix: np.ndarray,
    source_record_ids: Sequence[str],
    source_labels: Sequence[int],
    target_matrix: np.ndarray,
    target_record_ids: Sequence[str],
    target_labels: Sequence[int],
    resamples: int,
    seed: int,
) -> MmdDiagnostic:
    """Run deterministic balanced linear-time classwise RBF-MMD permutations."""

    source = np.asarray(source_matrix, dtype=np.float64)
    target = np.asarray(target_matrix, dtype=np.float64)
    y_source = _binary_targets(source_labels, label="MMD source targets")
    y_target = _binary_targets(target_labels, label="MMD target targets")
    if (
        source.ndim != 2
        or target.ndim != 2
        or source.shape[1] != target.shape[1]
        or source.shape[0] != len(source_record_ids)
        or source.shape[0] != y_source.size
        or target.shape[0] != len(target_record_ids)
        or target.shape[0] != y_target.size
        or not np.isfinite(source).all()
        or not np.isfinite(target).all()
    ):
        _fail("MMD matrices, record identities, and targets must align")
    if resamples < 100:
        _fail("MMD diagnostic requires at least 100 permutations")
    results: list[MmdClassResult] = []
    for class_label in (0, 1):
        source_positions = np.flatnonzero(y_source == class_label)
        target_positions = np.flatnonzero(y_target == class_label)
        source_ids = tuple(source_record_ids[int(index)] for index in source_positions)
        target_ids = tuple(target_record_ids[int(index)] for index in target_positions)
        source_order = _rank_rows(source_ids, seed=seed, label=class_label, role="source")
        target_order = _rank_rows(target_ids, seed=seed, label=class_label, role="target")
        balanced = min(len(source_order), len(target_order))
        balanced -= balanced % 2
        if balanced < 4:
            _fail("MMD class sample is too small")
        x = source[source_positions[np.asarray(source_order[:balanced], dtype=int)]]
        y = target[target_positions[np.asarray(target_order[:balanced], dtype=int)]]
        pooled = np.concatenate((x, y), axis=0)
        paired_squared = np.sum((pooled[0::2] - pooled[1::2]) ** 2, axis=1)
        nonzero = paired_squared[paired_squared > 0.0]
        bandwidth_squared = (
            float(np.median(nonzero)) if nonzero.size else RBF_MIN_BANDWIDTH_SQUARED
        )
        bandwidth_squared = max(bandwidth_squared, RBF_MIN_BANDWIDTH_SQUARED)
        observed = _linear_mmd_statistic(x, y, bandwidth_squared)
        generator = np.random.default_rng(seed + class_label)
        exceedances = 0
        for _ in range(resamples):
            permutation = generator.permutation(len(pooled))
            permuted_x = pooled[permutation[:balanced]]
            permuted_y = pooled[permutation[balanced:]]
            statistic = _linear_mmd_statistic(permuted_x, permuted_y, bandwidth_squared)
            exceedances += int(statistic >= observed)
        p_value = (exceedances + 1.0) / (resamples + 1.0)
        results.append(
            MmdClassResult(
                class_label=class_label,
                source_count=len(source_positions),
                target_count=len(target_positions),
                balanced_count=balanced,
                bandwidth_squared=bandwidth_squared,
                statistic=observed,
                permutation_p_value=p_value,
                resamples=resamples,
                seed=seed + class_label,
            )
        )
    return MmdDiagnostic(
        dataset_id=dataset_id,
        representation="registered_preprocessed_model_inputs",
        statistic="balanced_linear_time_unbiased_rbf_mmd_squared",
        bandwidth="median_nonzero_deterministic_paired_squared_distance",
        permutation_p_value="plus_one_greater_or_equal",
        classes=tuple(results),  # type: ignore[arg-type]
    )


def holm_adjust_all(p_values: Mapping[str, float]) -> dict[str, float]:
    """Holm-adjust an explicitly named deterministic hypothesis family."""

    if not p_values or any(
        not math.isfinite(value) or not 0.0 <= value <= 1.0 for value in p_values.values()
    ):
        _fail("Holm family requires finite p-values in [0, 1]")
    ordered = sorted(p_values.items(), key=lambda item: (item[1], item[0]))
    adjusted: dict[str, float] = {}
    running = 0.0
    count = len(ordered)
    for index, (name, value) in enumerate(ordered):
        running = max(running, min(1.0, (count - index) * value))
        adjusted[name] = running
    return adjusted
