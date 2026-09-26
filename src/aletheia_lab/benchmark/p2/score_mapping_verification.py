"""Independent development witness for evaluator score-column mapping.

The witness is captured from source artifacts, a reference model, and a
separately retained row-target ledger before the adapter is applied. Hashes
bind those inputs at capture and verification; they do not certify that the
original artifacts or labels were semantically correct.
"""

from __future__ import annotations

import math
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import numpy as np
from numpy.typing import NDArray

from aletheia_lab.benchmark.p2.canonical import canonical_sha256
from aletheia_lab.benchmark.p2.confirmatory_v3_runtime import (
    CalibrationResult,
    apply_logit_calibration,
)
from aletheia_lab.benchmark.p2.confirmatory_v3_shift import (
    reference_prior_standardized_log_loss,
)
from aletheia_lab.benchmark.p2.score_column_mapping import (
    ScoreColumnMappingError,
    validate_score_record_ids,
)
from aletheia_lab.benchmark.p2.score_mapping_intervention import (
    DEVELOPMENT_SELECTOR_SEED,
    DEVELOPMENT_SHARD_COUNT,
    BinaryProbabilityModel,
    EvaluatorScoreSource,
    MappingInterventionResult,
)
from aletheia_lab.content_hashing import file_sha256
from aletheia_lab.evidence.schema import sha256_text


class ScoreMappingVerificationError(ValueError):
    """The independent source, binding, or intervention output disagrees."""


@dataclass(frozen=True, slots=True)
class SourceArtifactPaths:
    """Read-only source artifacts; the caller owns their provenance."""

    dataset: Path
    split: Path
    preprocessor: Path
    fitted_model: Path


@dataclass(frozen=True, slots=True)
class SourceArtifactHashes:
    dataset: str
    split: str
    preprocessor: str
    fitted_model: str


@dataclass(frozen=True, slots=True)
class IndependentScoreWitness:
    """Pre-intervention snapshot made without reading the adapter output."""

    dataset_id: str
    record_ids: tuple[str, ...]
    target_rows: tuple[tuple[str, int], ...]
    model_classes: tuple[int, int]
    raw_score_rows: tuple[tuple[float, float], ...]
    calibrated_score_rows: tuple[tuple[float, float], ...]
    artifact_hashes: SourceArtifactHashes
    feature_matrix_sha256: str
    calibration_sha256: str
    raw_scores_sha256: str
    calibrated_scores_sha256: str
    target_binding_sha256: str


@dataclass(frozen=True, slots=True)
class MappingVerification:
    """Recomputed measurements, not a mechanism-admission decision."""

    artifact_hashes: SourceArtifactHashes
    raw_scores_sha256: str
    calibrated_scores_sha256: str
    target_binding_sha256: str
    affected_record_ids: tuple[str, ...]
    changed_score_record_ids: tuple[str, ...]
    nominal_shard_fraction: float
    achieved_affected_fraction: float
    healthy_log_loss: float
    faulty_log_loss: float
    corrected_log_loss: float


def _artifact_hashes(paths: SourceArtifactPaths) -> SourceArtifactHashes:
    if not isinstance(paths, SourceArtifactPaths):
        raise ScoreMappingVerificationError("four source artifact paths are required")
    digests: list[str] = []
    for path in (paths.dataset, paths.split, paths.preprocessor, paths.fitted_model):
        if not isinstance(path, Path) or path.is_symlink() or not path.is_file():
            raise ScoreMappingVerificationError("source artifact must be a regular file")
        try:
            digests.append(file_sha256(path))
        except OSError as exc:
            raise ScoreMappingVerificationError("source artifact cannot be read") from exc
    return SourceArtifactHashes(*digests)


def _checked_target_rows(
    record_ids: tuple[str, ...], target_rows: Sequence[tuple[str, int]]
) -> tuple[tuple[str, int], ...]:
    if isinstance(target_rows, (str, bytes)):
        raise ScoreMappingVerificationError("source targets need ordered row-ID bindings")
    try:
        rows = tuple(tuple(row) for row in target_rows)
    except TypeError as exc:
        raise ScoreMappingVerificationError("source targets need ordered row-ID bindings") from exc
    if (
        len(rows) != len(record_ids)
        or any(len(row) != 2 for row in rows)
        or tuple(row[0] for row in rows) != record_ids
        or any(type(row[1]) is not int or row[1] not in (0, 1) for row in rows)
        or {row[1] for row in rows} != {0, 1}
    ):
        raise ScoreMappingVerificationError("source target values must bind to exact row IDs")
    return tuple((record_ids[index], cast(int, row[1])) for index, row in enumerate(rows))


def _checked_model_scores(
    model: BinaryProbabilityModel,
    features: NDArray[np.float64],
    row_count: int,
) -> tuple[tuple[int, int], tuple[tuple[float, float], ...]]:
    try:
        classes = tuple(model.classes_)
        scores = np.asarray(model.predict_proba(features), dtype=np.float64)
    except (AttributeError, TypeError, ValueError, FloatingPointError) as exc:
        raise ScoreMappingVerificationError("reference model cannot reproduce its scores") from exc
    if (
        len(classes) != 2
        or any(
            isinstance(value, (bool, np.bool_)) or not isinstance(value, (int, np.integer))
            for value in classes
        )
        or set(classes) != {0, 1}
        or scores.shape != (row_count, 2)
        or not np.isfinite(scores).all()
        or np.any(scores < 0.0)
        or np.any(scores > 1.0)
        or not np.allclose(scores.sum(axis=1), 1.0, rtol=0.0, atol=1e-12)
    ):
        raise ScoreMappingVerificationError("reference model has invalid classes or scores")
    return (
        (int(classes[0]), int(classes[1])),
        tuple((float(row[0]), float(row[1])) for row in scores),
    )


def _feature_matrix_sha256(record_ids: tuple[str, ...], features: NDArray[np.float64]) -> str:
    # Signed zeros have identical numeric meaning for the model and the P2
    # canonical serializer rejects negative zero.
    rows = tuple(tuple(0.0 if value == 0.0 else float(value) for value in row) for row in features)
    return canonical_sha256({"record_ids": record_ids, "features": rows})


def capture_independent_score_witness(
    *,
    dataset_id: str,
    record_ids: Sequence[str],
    target_rows: Sequence[tuple[str, int]],
    evaluation_matrix: NDArray[np.float64],
    model: BinaryProbabilityModel,
    calibration: CalibrationResult,
    artifacts: SourceArtifactPaths,
) -> IndependentScoreWitness:
    """Bind reference inputs before intervention, without reading its source/result.

    The caller must obtain the model, row-target ledger, and artifact paths
    from the actual upstream source. This function cannot authenticate their
    origin merely by hashing them.
    """

    if (
        not isinstance(dataset_id, str)
        or not dataset_id
        or dataset_id != dataset_id.strip()
        or "\x00" in dataset_id
        or dataset_id != unicodedata.normalize("NFC", dataset_id)
    ):
        raise ScoreMappingVerificationError("dataset ID must be canonical")
    try:
        ids = validate_score_record_ids(record_ids)
    except ScoreColumnMappingError as exc:
        raise ScoreMappingVerificationError("source record IDs are invalid") from exc
    targets = _checked_target_rows(ids, target_rows)
    try:
        features = np.asarray(evaluation_matrix, dtype=np.float64)
    except (TypeError, ValueError) as exc:
        raise ScoreMappingVerificationError("reference features must be numeric") from exc
    if (
        features.ndim != 2
        or features.shape[0] != len(ids)
        or features.shape[1] == 0
        or not np.isfinite(features).all()
    ):
        raise ScoreMappingVerificationError("reference features must align with finite rows")
    if not isinstance(calibration, CalibrationResult):
        raise ScoreMappingVerificationError("reference calibration is required")
    artifact_hashes = _artifact_hashes(artifacts)
    feature_matrix_sha256 = _feature_matrix_sha256(ids, features)
    classes, raw_rows = _checked_model_scores(model, features, len(ids))
    if (
        _artifact_hashes(artifacts) != artifact_hashes
        or _feature_matrix_sha256(ids, features) != feature_matrix_sha256
    ):
        raise ScoreMappingVerificationError("upstream source changed during score capture")
    positive_column = classes.index(1)
    calibrated_positive = apply_logit_calibration(
        tuple(row[positive_column] for row in raw_rows), calibration
    )
    calibrated_rows = tuple(
        (1.0 - value, value) if classes == (0, 1) else (value, 1.0 - value)
        for value in calibrated_positive
    )
    return IndependentScoreWitness(
        dataset_id=dataset_id,
        record_ids=ids,
        target_rows=targets,
        model_classes=classes,
        raw_score_rows=raw_rows,
        calibrated_score_rows=calibrated_rows,
        artifact_hashes=artifact_hashes,
        feature_matrix_sha256=feature_matrix_sha256,
        calibration_sha256=calibration.canonical_sha256(),
        raw_scores_sha256=canonical_sha256({"record_ids": ids, "scores": raw_rows}),
        calibrated_scores_sha256=canonical_sha256({"record_ids": ids, "scores": calibrated_rows}),
        target_binding_sha256=canonical_sha256({"target_rows": targets}),
    )


def verify_evaluator_mapping(
    *,
    witness: IndependentScoreWitness,
    source: EvaluatorScoreSource,
    intervention: MappingInterventionResult,
    scoring_target_rows: Sequence[tuple[str, int]],
    reference_model: BinaryProbabilityModel,
    evaluation_matrix: NDArray[np.float64],
    reference_calibration: CalibrationResult,
    artifacts: SourceArtifactPaths,
) -> MappingVerification:
    """Recompute source, selection and metrics without calling the injector."""

    if not isinstance(witness, IndependentScoreWitness):
        raise ScoreMappingVerificationError("a prior independent witness is required")
    if not isinstance(source, EvaluatorScoreSource) or not isinstance(
        intervention, MappingInterventionResult
    ):
        raise ScoreMappingVerificationError("typed source and intervention are required")
    replay = capture_independent_score_witness(
        dataset_id=witness.dataset_id,
        record_ids=witness.record_ids,
        target_rows=witness.target_rows,
        evaluation_matrix=evaluation_matrix,
        model=reference_model,
        calibration=reference_calibration,
        artifacts=artifacts,
    )
    if replay != witness:
        raise ScoreMappingVerificationError("upstream source changed since witness capture")
    if (
        source.dataset_id != witness.dataset_id
        or source.record_ids != witness.record_ids
        or source.model_classes != witness.model_classes
        or source.calibration.canonical_sha256() != witness.calibration_sha256
        or source.raw_score_rows != witness.raw_score_rows
        or source.calibrated_score_rows != witness.calibrated_score_rows
    ):
        raise ScoreMappingVerificationError("adapter source differs from independent witness")
    scoring_targets = _checked_target_rows(witness.record_ids, scoring_target_rows)
    if scoring_targets != witness.target_rows:
        raise ScoreMappingVerificationError("scoring targets differ from source row-target ledger")
    count = intervention.selected_shard_count
    if type(count) is not int or count not in (0, 1, 2, 4):
        raise ScoreMappingVerificationError("intervention uses an unregistered development dose")
    shards = tuple(
        int(sha256_text(f"{DEVELOPMENT_SELECTOR_SEED}\x00{witness.dataset_id}\x00{record_id}"), 16)
        % DEVELOPMENT_SHARD_COUNT
        for record_id in witness.record_ids
    )
    affected = tuple(
        record_id
        for record_id, shard in zip(witness.record_ids, shards, strict=True)
        if shard < count
    )
    positive_index = witness.model_classes.index(1)
    healthy = tuple(row[positive_index] for row in witness.calibrated_score_rows)
    faulty = tuple(
        row[1 - positive_index] if shard < count else row[positive_index]
        for row, shard in zip(witness.calibrated_score_rows, shards, strict=True)
    )
    changed = tuple(
        record_id
        for record_id, before, after in zip(witness.record_ids, healthy, faulty, strict=True)
        if before != after
    )
    if (
        intervention.record_ids != witness.record_ids
        or intervention.faulty_column_classes != tuple(reversed(witness.model_classes))
        or intervention.row_shards != shards
        or intervention.affected_record_ids != affected
        or intervention.changed_score_record_ids != changed
        or intervention.positive_probabilities != faulty
    ):
        raise ScoreMappingVerificationError("intervention output failed independent reconstruction")
    targets = tuple(value for _, value in scoring_targets)
    healthy_loss = reference_prior_standardized_log_loss(true_labels=targets, probabilities=healthy)
    faulty_loss = reference_prior_standardized_log_loss(true_labels=targets, probabilities=faulty)
    corrected = tuple(row[witness.model_classes.index(1)] for row in replay.calibrated_score_rows)
    corrected_loss = reference_prior_standardized_log_loss(
        true_labels=targets, probabilities=corrected
    )
    if corrected != healthy or corrected_loss != healthy_loss:
        raise ScoreMappingVerificationError("corrected decode did not restore healthy output")
    if not all(math.isfinite(value) for value in (healthy_loss, faulty_loss, corrected_loss)):
        raise ScoreMappingVerificationError("recomputed metric is non-finite")
    return MappingVerification(
        artifact_hashes=witness.artifact_hashes,
        raw_scores_sha256=witness.raw_scores_sha256,
        calibrated_scores_sha256=witness.calibrated_scores_sha256,
        target_binding_sha256=witness.target_binding_sha256,
        affected_record_ids=affected,
        changed_score_record_ids=changed,
        nominal_shard_fraction=count / DEVELOPMENT_SHARD_COUNT,
        achieved_affected_fraction=len(affected) / len(witness.record_ids),
        healthy_log_loss=healthy_loss,
        faulty_log_loss=faulty_loss,
        corrected_log_loss=corrected_loss,
    )
