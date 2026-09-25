"""Development-only score capture and evaluator mapping intervention.

The fitted model and calibration are upstream of this adapter. This module
never reads targets, chooses a dose from an outcome, or scores a metric.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from typing import Literal, Protocol

import numpy as np
from numpy.typing import NDArray

from aletheia_lab.benchmark.p2.confirmatory_v3_runtime import (
    CalibrationResult,
    apply_logit_calibration,
)
from aletheia_lab.benchmark.p2.score_column_mapping import (
    ScoreColumnMappingError,
    decode_positive_class_scores,
    validate_score_record_ids,
)
from aletheia_lab.evidence.schema import sha256_text

DEVELOPMENT_SELECTOR_SEED = "m5-dev-v1-2026-09-25"
DEVELOPMENT_SHARD_COUNT = 20
DevelopmentSelectedShards = Literal[0, 1, 2, 4]


class ScoreMappingInterventionError(ValueError):
    """A score source, selector, or mapping request is invalid."""


class BinaryProbabilityModel(Protocol):
    classes_: NDArray[np.int64]

    def predict_proba(self, features: NDArray[np.float64]) -> NDArray[np.float64]: ...


def _checked_dataset_id(value: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or "\x00" in value
        or value != unicodedata.normalize("NFC", value)
    ):
        raise ScoreMappingInterventionError("dataset ID must be a nonempty canonical string")
    return value


@dataclass(frozen=True, slots=True)
class EvaluatorScoreSource:
    """Immutable pre-intervention score snapshot, not independent attestation."""

    dataset_id: str
    record_ids: tuple[str, ...]
    model_classes: tuple[int, int]
    raw_score_rows: tuple[tuple[float, float], ...]
    calibration: CalibrationResult
    calibrated_score_rows: tuple[tuple[float, float], ...]

    def __post_init__(self) -> None:
        _checked_dataset_id(self.dataset_id)
        if isinstance(self.record_ids, (str, bytes)):
            raise ScoreMappingInterventionError("record IDs must be an ordered sequence")
        try:
            ids = tuple(self.record_ids)
            classes = tuple(self.model_classes)
            raw = tuple(tuple(row) for row in self.raw_score_rows)
            calibrated = tuple(tuple(row) for row in self.calibrated_score_rows)
            raw_positive = decode_positive_class_scores(
                record_ids=ids,
                expected_record_ids=ids,
                score_rows=raw,
                column_classes=classes,
            )
            decode_positive_class_scores(
                record_ids=ids,
                expected_record_ids=ids,
                score_rows=calibrated,
                column_classes=classes,
            )
        except (TypeError, ScoreColumnMappingError) as exc:
            raise ScoreMappingInterventionError(
                "evaluator score source is malformed or misaligned"
            ) from exc
        if not isinstance(self.calibration, CalibrationResult):
            raise ScoreMappingInterventionError(
                "evaluator score source needs its development calibration"
            )
        calibration = CalibrationResult.model_validate(self.calibration.model_dump())
        positive = apply_logit_calibration(raw_positive, calibration)
        expected_calibrated = tuple(
            (1.0 - value, value) if classes == (0, 1) else (value, 1.0 - value)
            for value in positive
        )
        if calibrated != expected_calibrated:
            raise ScoreMappingInterventionError(
                "calibrated columns do not follow the captured source"
            )
        object.__setattr__(self, "record_ids", ids)
        object.__setattr__(self, "model_classes", tuple(int(value) for value in classes))
        object.__setattr__(self, "calibration", calibration)
        object.__setattr__(
            self, "raw_score_rows", tuple((float(left), float(right)) for left, right in raw)
        )
        object.__setattr__(
            self,
            "calibrated_score_rows",
            tuple((float(left), float(right)) for left, right in calibrated),
        )


@dataclass(frozen=True, slots=True)
class MappingInterventionResult:
    """Adapter output and intervention bookkeeping, not a causal verdict."""

    record_ids: tuple[str, ...]
    positive_probabilities: tuple[float, ...]
    selected_shard_count: int
    faulty_column_classes: tuple[int, int]
    row_shards: tuple[int, ...]
    affected_record_ids: tuple[str, ...]
    changed_score_record_ids: tuple[str, ...]


def capture_evaluator_score_source(
    *,
    dataset_id: str,
    record_ids: tuple[str, ...],
    evaluation_matrix: NDArray[np.float64],
    model: BinaryProbabilityModel,
    calibration: CalibrationResult,
) -> EvaluatorScoreSource:
    """Capture both model columns and actual class order before evaluator mapping.

    This fresh development path does not read a historical one-column receipt.
    A later, independent provenance check is still required for causal claims.
    """

    _checked_dataset_id(dataset_id)
    try:
        ids = validate_score_record_ids(record_ids)
    except ScoreColumnMappingError as exc:
        raise ScoreMappingInterventionError("record IDs must be canonical and unique") from exc
    try:
        features = np.asarray(evaluation_matrix, dtype=np.float64)
    except (TypeError, ValueError) as exc:
        raise ScoreMappingInterventionError(
            "evaluation matrix must contain numeric values"
        ) from exc
    if (
        features.ndim != 2
        or features.shape[0] != len(ids)
        or features.shape[1] == 0
        or not np.isfinite(features).all()
    ):
        raise ScoreMappingInterventionError("evaluation matrix must align with finite feature rows")
    if not isinstance(calibration, CalibrationResult):
        raise ScoreMappingInterventionError("a completed development calibration is required")
    try:
        classes = tuple(model.classes_)
        raw_rows = tuple(tuple(row) for row in model.predict_proba(features))
        raw_positive = decode_positive_class_scores(
            record_ids=ids,
            expected_record_ids=ids,
            score_rows=raw_rows,
            column_classes=classes,
        )
    except (AttributeError, TypeError, ValueError, FloatingPointError) as exc:
        raise ScoreMappingInterventionError(
            "model score columns or class order are invalid"
        ) from exc
    calibrated_positive = apply_logit_calibration(raw_positive, calibration)
    calibrated_rows = tuple(
        (1.0 - value, value) if tuple(classes) == (0, 1) else (value, 1.0 - value)
        for value in calibrated_positive
    )
    return EvaluatorScoreSource(
        dataset_id=dataset_id,
        record_ids=ids,
        model_classes=classes,
        raw_score_rows=raw_rows,
        calibration=calibration,
        calibrated_score_rows=calibrated_rows,
    )


def _development_shard(dataset_id: str, record_id: str) -> int:
    key = f"{DEVELOPMENT_SELECTOR_SEED}\x00{dataset_id}\x00{record_id}"
    return int(sha256_text(key), 16) % DEVELOPMENT_SHARD_COUNT


def apply_evaluator_mapping_fault(
    source: EvaluatorScoreSource, *, selected_shard_count: DevelopmentSelectedShards
) -> MappingInterventionResult:
    """Misread the positive-class column only on prespecified row-ID shards."""

    if not isinstance(source, EvaluatorScoreSource):
        raise ScoreMappingInterventionError("a validated evaluator score source is required")
    if type(selected_shard_count) is not int or selected_shard_count not in (0, 1, 2, 4):
        raise ScoreMappingInterventionError("selected shard count must be a development grid value")
    actual_classes = source.model_classes
    healthy = decode_positive_class_scores(
        record_ids=source.record_ids,
        expected_record_ids=source.record_ids,
        score_rows=source.calibrated_score_rows,
        column_classes=actual_classes,
    )
    swapped = decode_positive_class_scores(
        record_ids=source.record_ids,
        expected_record_ids=source.record_ids,
        score_rows=source.calibrated_score_rows,
        column_classes=tuple(reversed(actual_classes)),
    )
    shards = tuple(
        _development_shard(source.dataset_id, record_id) for record_id in source.record_ids
    )
    affected = tuple(
        record_id
        for record_id, shard in zip(source.record_ids, shards, strict=True)
        if shard < selected_shard_count
    )
    probabilities = tuple(
        swapped[index] if shard < selected_shard_count else healthy[index]
        for index, shard in enumerate(shards)
    )
    changed = tuple(
        record_id
        for record_id, before, after in zip(source.record_ids, healthy, probabilities, strict=True)
        if before != after
    )
    return MappingInterventionResult(
        record_ids=source.record_ids,
        positive_probabilities=probabilities,
        selected_shard_count=selected_shard_count,
        faulty_column_classes=tuple(reversed(actual_classes)),
        row_shards=shards,
        affected_record_ids=affected,
        changed_score_record_ids=changed,
    )
