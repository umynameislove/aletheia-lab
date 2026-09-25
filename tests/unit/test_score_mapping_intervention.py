"""Development-only tests for the evaluator-boundary mapping fault."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

import numpy as np
import pytest
from numpy.typing import NDArray
from sklearn.linear_model import LogisticRegression

from aletheia_lab.benchmark.p2.confirmatory_v3_runtime import CalibrationResult
from aletheia_lab.benchmark.p2.confirmatory_v3_shift import (
    reference_prior_standardized_log_loss,
)
from aletheia_lab.benchmark.p2.score_column_mapping import decode_positive_class_scores
from aletheia_lab.benchmark.p2.score_mapping_intervention import (
    DEVELOPMENT_SELECTOR_SEED,
    EvaluatorScoreSource,
    ScoreMappingInterventionError,
    apply_evaluator_mapping_fault,
    capture_evaluator_score_source,
)


@dataclass
class _SyntheticModel:
    classes_: NDArray[np.int64]
    probabilities: NDArray[np.float64]
    calls: int = 0

    def predict_proba(self, features: NDArray[np.float64]) -> NDArray[np.float64]:
        self.calls += 1
        return self.probabilities


def _identity_calibration() -> CalibrationResult:
    return CalibrationResult(
        intercept=0.0,
        slope=1.0,
        iterations=0,
        converged=True,
        gradient_infinity_norm=0.0,
        development_record_count=2,
    )


def _capture(
    ids: tuple[str, ...],
    rows: NDArray[np.float64],
    *,
    dataset_id: str = "synthetic-dataset",
    classes: tuple[int, int] = (0, 1),
) -> tuple[EvaluatorScoreSource, _SyntheticModel]:
    model = _SyntheticModel(np.asarray(classes, dtype=np.int64), rows)
    source = capture_evaluator_score_source(
        dataset_id=dataset_id,
        record_ids=ids,
        evaluation_matrix=np.arange(len(ids), dtype=np.float64).reshape(-1, 1),
        model=model,
        calibration=_identity_calibration(),
    )
    return source, model


def _expected_shard(dataset_id: str, record_id: str) -> int:
    payload = f"{DEVELOPMENT_SELECTOR_SEED}\x00{dataset_id}\x00{record_id}".encode()
    return int.from_bytes(hashlib.sha256(payload).digest(), "big") % 20


def test_captures_real_model_columns_before_intervention_without_changing_model() -> None:
    training_x = np.array([[-3.0], [-2.0], [-1.0], [1.0], [2.0], [3.0]])
    training_y = np.array([0, 0, 0, 1, 1, 1])
    evaluation_x = np.array([[-1.5], [0.5], [2.5]])
    model = LogisticRegression(random_state=42).fit(training_x, training_y)
    raw_before = model.predict_proba(evaluation_x).copy()
    source = capture_evaluator_score_source(
        dataset_id="synthetic-dataset",
        record_ids=("r0", "r1", "r2"),
        evaluation_matrix=evaluation_x,
        model=model,
        calibration=_identity_calibration(),
    )
    assert source.model_classes == tuple(model.classes_)
    assert np.asarray(source.raw_score_rows) == pytest.approx(raw_before)
    assert np.asarray(source.calibrated_score_rows)[:, 1] == pytest.approx(raw_before[:, 1])
    assert apply_evaluator_mapping_fault(source, selected_shard_count=0).positive_probabilities == (
        decode_positive_class_scores(
            record_ids=source.record_ids,
            expected_record_ids=source.record_ids,
            score_rows=source.calibrated_score_rows,
            column_classes=source.model_classes,
        )
    )
    assert model.predict_proba(evaluation_x) == pytest.approx(raw_before)


def test_reversed_model_classes_are_captured_before_positive_calibration() -> None:
    ids = ("r0", "r1")
    source, model = _capture(ids, np.array([[0.8, 0.2], [0.3, 0.7]]), classes=(1, 0))
    assert model.calls == 1
    assert source.model_classes == (1, 0)
    assert source.raw_score_rows == ((0.8, 0.2), (0.3, 0.7))
    assert source.calibrated_score_rows[0] == pytest.approx((0.8, 0.2))
    assert apply_evaluator_mapping_fault(
        source, selected_shard_count=0
    ).positive_probabilities == pytest.approx((0.8, 0.3))


def test_nonidentity_calibration_is_bound_to_the_original_class_order() -> None:
    model = _SyntheticModel(np.array([1, 0]), np.array([[0.8, 0.2], [0.3, 0.7]]))
    calibration = CalibrationResult(
        intercept=-0.3,
        slope=0.5,
        iterations=2,
        converged=True,
        gradient_infinity_norm=0.0,
        development_record_count=2,
    )
    source = capture_evaluator_score_source(
        dataset_id="synthetic-dataset",
        record_ids=("r0", "r1"),
        evaluation_matrix=np.array([[1.0], [2.0]]),
        model=model,
        calibration=calibration,
    )
    assert source.calibration == calibration
    assert source.calibrated_score_rows[0][0] != source.raw_score_rows[0][0]
    assert source.calibrated_score_rows[0][0] + source.calibrated_score_rows[0][1] == 1.0
    assert apply_evaluator_mapping_fault(
        source, selected_shard_count=0
    ).positive_probabilities == tuple(pair[0] for pair in source.calibrated_score_rows)


def test_selector_is_reproducible_nested_and_independent_of_scores_or_row_order() -> None:
    ids = tuple(f"r{index}" for index in range(160))
    rows = np.asarray([(0.8, 0.2) if index % 2 else (0.7, 0.3) for index in range(160)])
    source, model = _capture(ids, rows)
    source_snapshot = (source.raw_score_rows, source.calibrated_score_rows)
    expected_shards = tuple(_expected_shard(source.dataset_id, record_id) for record_id in ids)
    healthy = apply_evaluator_mapping_fault(source, selected_shard_count=0)
    results = {
        count: apply_evaluator_mapping_fault(source, selected_shard_count=count)
        for count in (0, 1, 2, 4)
    }

    assert model.calls == 1
    assert results[0] == healthy
    assert healthy.affected_record_ids == healthy.changed_score_record_ids == ()
    assert source.raw_score_rows == source_snapshot[0]
    assert source.calibrated_score_rows == source_snapshot[1]
    assert results[1].row_shards == expected_shards
    assert len(results[1].affected_record_ids) > 0
    for count, result in results.items():
        expected_affected = tuple(
            record_id
            for record_id, shard in zip(ids, expected_shards, strict=True)
            if shard < count
        )
        assert result.record_ids == ids
        assert result.selected_shard_count == count
        assert result.faulty_column_classes == (1, 0)
        assert result.affected_record_ids == expected_affected
        assert result.changed_score_record_ids == expected_affected
        assert len(result.positive_probabilities) == len(ids)
        for index, shard in enumerate(expected_shards):
            expected = (
                source.calibrated_score_rows[index][0]
                if shard < count
                else source.calibrated_score_rows[index][1]
            )
            assert result.positive_probabilities[index] == expected
    assert set(results[1].affected_record_ids) <= set(results[2].affected_record_ids)
    assert set(results[2].affected_record_ids) <= set(results[4].affected_record_ids)

    reverse_ids = tuple(reversed(ids))
    reverse_source, _ = _capture(reverse_ids, rows[::-1].copy())
    reverse_result = apply_evaluator_mapping_fault(reverse_source, selected_shard_count=2)
    assert dict(zip(reverse_result.record_ids, reverse_result.row_shards, strict=True)) == dict(
        zip(results[2].record_ids, results[2].row_shards, strict=True)
    )
    assert dict(
        zip(reverse_result.record_ids, reverse_result.positive_probabilities, strict=True)
    ) == dict(zip(results[2].record_ids, results[2].positive_probabilities, strict=True))

    changed_rows = np.asarray([(0.95, 0.05)] * len(ids))
    changed_source, _ = _capture(ids, changed_rows)
    changed_result = apply_evaluator_mapping_fault(changed_source, selected_shard_count=2)
    assert changed_result.affected_record_ids == results[2].affected_record_ids
    assert changed_result.row_shards == results[2].row_shards


def test_selected_tie_is_counted_as_affected_but_not_score_changed() -> None:
    record_id = next(
        f"r{index}"
        for index in range(1000)
        if _expected_shard("synthetic-dataset", f"r{index}") == 0
    )
    source, _ = _capture((record_id,), np.array([[0.5, 0.5]]))
    result = apply_evaluator_mapping_fault(source, selected_shard_count=1)
    assert result.affected_record_ids == (record_id,)
    assert result.changed_score_record_ids == ()
    assert result.positive_probabilities == (0.5,)


def test_faulty_and_corrected_probabilities_use_the_same_runtime_scorer() -> None:
    ids = tuple(f"r{index}" for index in range(100))
    targets = tuple(index % 2 for index in range(100))
    rows = np.asarray([(0.9, 0.1) if target == 0 else (0.1, 0.9) for target in targets])
    source, _ = _capture(ids, rows)
    source_snapshot = (source.raw_score_rows, source.calibrated_score_rows)
    healthy = apply_evaluator_mapping_fault(source, selected_shard_count=0)
    faulty = apply_evaluator_mapping_fault(source, selected_shard_count=4)
    corrected = decode_positive_class_scores(
        record_ids=ids,
        expected_record_ids=ids,
        score_rows=source.calibrated_score_rows,
        column_classes=source.model_classes,
    )
    assert faulty.affected_record_ids
    assert corrected == healthy.positive_probabilities
    assert reference_prior_standardized_log_loss(
        true_labels=targets, probabilities=faulty.positive_probabilities
    ) > reference_prior_standardized_log_loss(
        true_labels=targets, probabilities=healthy.positive_probabilities
    )
    assert reference_prior_standardized_log_loss(
        true_labels=targets, probabilities=corrected
    ) == reference_prior_standardized_log_loss(
        true_labels=targets, probabilities=healthy.positive_probabilities
    )
    assert (source.raw_score_rows, source.calibrated_score_rows) == source_snapshot


def test_source_snapshot_is_deeply_immutable_after_model_output_changes() -> None:
    rows = np.array([[0.8, 0.2], [0.3, 0.7]])
    source, model = _capture(("r0", "r1"), rows)
    before = (source.raw_score_rows, source.calibrated_score_rows)
    model.probabilities[0, :] = (0.4, 0.6)
    assert (source.raw_score_rows, source.calibrated_score_rows) == before
    assert apply_evaluator_mapping_fault(
        source, selected_shard_count=0
    ).positive_probabilities == pytest.approx((0.2, 0.7))


@pytest.mark.parametrize("count", [-1, 3, 20, True, 1.0, "1"])
def test_unregistered_development_shard_counts_fail_closed(count: object) -> None:
    source, _ = _capture(("r0",), np.array([[0.8, 0.2]]))
    with pytest.raises(ScoreMappingInterventionError):
        apply_evaluator_mapping_fault(source, selected_shard_count=count)  # type: ignore[arg-type]


@pytest.mark.parametrize("dataset_id", ["", " bad", "bad ", "a\x00b", "e\u0301"])
def test_noncanonical_dataset_id_fails_before_prediction(dataset_id: str) -> None:
    model = _SyntheticModel(np.array([0, 1]), np.array([[0.8, 0.2]]))
    with pytest.raises(ScoreMappingInterventionError):
        capture_evaluator_score_source(
            dataset_id=dataset_id,
            record_ids=("r0",),
            evaluation_matrix=np.array([[1.0]]),
            model=model,
            calibration=_identity_calibration(),
        )
    assert model.calls == 0


@pytest.mark.parametrize("record_ids", [("r0", "r0"), ("r0", "e\u0301"), ("r0", "r\x001")])
def test_invalid_record_ids_fail_closed(record_ids: tuple[str, ...]) -> None:
    model = _SyntheticModel(np.asarray((0, 1), dtype=np.int64), np.array([[0.8, 0.2], [0.3, 0.7]]))
    with pytest.raises(ScoreMappingInterventionError):
        capture_evaluator_score_source(
            dataset_id="synthetic-dataset",
            record_ids=record_ids,
            evaluation_matrix=np.array([[1.0], [2.0]]),
            model=model,
            calibration=_identity_calibration(),
        )
    assert model.calls == 0


def test_record_id_string_is_not_split_into_characters() -> None:
    model = _SyntheticModel(np.array([0, 1]), np.array([[0.8, 0.2]]))
    with pytest.raises(ScoreMappingInterventionError, match="canonical and unique"):
        capture_evaluator_score_source(
            dataset_id="synthetic-dataset",
            record_ids="r0",  # type: ignore[arg-type]
            evaluation_matrix=np.array([[1.0]]),
            model=model,
            calibration=_identity_calibration(),
        )
    assert model.calls == 0


@pytest.mark.parametrize(
    "rows",
    [
        np.array([[0.8]]),
        np.array([[0.8, 0.2, 0.0]]),
        np.array([[0.8, 0.1]]),
        np.array([[np.nan, 0.2]]),
        np.array([[0.8, 0.2], [0.3, 0.7]]),
    ],
)
def test_malformed_model_scores_fail_closed(rows: NDArray[np.float64]) -> None:
    with pytest.raises(ScoreMappingInterventionError):
        _capture(("r0",), rows)


def test_source_rejects_misaligned_calibrated_rows() -> None:
    with pytest.raises(ScoreMappingInterventionError):
        EvaluatorScoreSource(
            dataset_id="synthetic-dataset",
            record_ids=("r0", "r1"),
            model_classes=(0, 1),
            raw_score_rows=((0.8, 0.2), (0.3, 0.7)),
            calibration=_identity_calibration(),
            calibrated_score_rows=((0.8, 0.2),),
        )


def test_source_rejects_calibrated_scores_not_derived_from_raw_columns() -> None:
    with pytest.raises(ScoreMappingInterventionError, match="calibrated columns"):
        EvaluatorScoreSource(
            dataset_id="synthetic-dataset",
            record_ids=("r0",),
            model_classes=(0, 1),
            raw_score_rows=((0.8, 0.2),),
            calibration=_identity_calibration(),
            calibrated_score_rows=((0.7, 0.3),),
        )


@pytest.mark.parametrize("classes", [(0, 0), (0, 2), (0,), (0, 1, 2)])
def test_capture_rejects_invalid_model_class_order(classes: tuple[int, ...]) -> None:
    model = _SyntheticModel(np.asarray(classes), np.array([[0.8, 0.2]]))
    with pytest.raises(ScoreMappingInterventionError):
        capture_evaluator_score_source(
            dataset_id="synthetic-dataset",
            record_ids=("r0",),
            evaluation_matrix=np.array([[1.0]]),
            model=model,
            calibration=_identity_calibration(),
        )


@pytest.mark.parametrize(
    "features", [np.array([[np.nan]]), np.array([[1.0, np.inf]]), np.array([1.0])]
)
def test_invalid_feature_matrix_fails_before_prediction(features: NDArray[np.float64]) -> None:
    model = _SyntheticModel(np.array([0, 1]), np.array([[0.8, 0.2]]))
    with pytest.raises(ScoreMappingInterventionError):
        capture_evaluator_score_source(
            dataset_id="synthetic-dataset",
            record_ids=("r0",),
            evaluation_matrix=features,
            model=model,
            calibration=_identity_calibration(),
        )
    assert model.calls == 0
