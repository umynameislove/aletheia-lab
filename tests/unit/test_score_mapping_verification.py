"""Independent development checks of the evaluator mapping intervention."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
import pytest
from numpy.typing import NDArray
from sklearn.linear_model import LogisticRegression

from aletheia_lab.benchmark.p2.confirmatory_v3_runtime import CalibrationResult
from aletheia_lab.benchmark.p2.confirmatory_v3_shift import (
    reference_prior_standardized_log_loss,
)
from aletheia_lab.benchmark.p2.score_mapping_intervention import (
    DEVELOPMENT_SELECTOR_SEED,
    EvaluatorScoreSource,
    MappingInterventionResult,
    apply_evaluator_mapping_fault,
    capture_evaluator_score_source,
)
from aletheia_lab.benchmark.p2.score_mapping_verification import (
    IndependentScoreWitness,
    MappingVerification,
    ScoreMappingVerificationError,
    SourceArtifactPaths,
    capture_independent_score_witness,
    verify_evaluator_mapping,
)


@dataclass
class _FixedModel:
    classes_: NDArray[np.int64]
    probabilities: NDArray[np.float64]

    def predict_proba(self, features: NDArray[np.float64]) -> NDArray[np.float64]:
        return self.probabilities.copy()


@dataclass(frozen=True)
class _Case:
    ids: tuple[str, ...]
    targets: tuple[tuple[str, int], ...]
    features: NDArray[np.float64]
    model: _FixedModel
    calibration: CalibrationResult
    artifacts: SourceArtifactPaths
    witness: IndependentScoreWitness
    source: EvaluatorScoreSource
    intervention: MappingInterventionResult


def _calibration() -> CalibrationResult:
    return CalibrationResult(
        intercept=0.0,
        slope=1.0,
        iterations=0,
        converged=True,
        gradient_infinity_norm=0.0,
        development_record_count=2,
    )


def _selected(record_id: str) -> bool:
    key = f"{DEVELOPMENT_SELECTOR_SEED}\x00synthetic-dataset\x00{record_id}".encode()
    return int.from_bytes(hashlib.sha256(key).digest(), "big") % 20 < 4


def _case(tmp_path: Path, *, tie_selected_row: bool = False) -> _Case:
    ids = tuple(f"r{index}" for index in range(80))
    targets = tuple((record_id, index % 2) for index, record_id in enumerate(ids))
    rows = np.asarray(
        [(0.9, 0.1) if label == 0 else (0.1, 0.9) for _, label in targets],
        dtype=np.float64,
    )
    if tie_selected_row:
        selected_index = next(index for index, record_id in enumerate(ids) if _selected(record_id))
        rows[selected_index, :] = (0.5, 0.5)
    model = _FixedModel(np.array([0, 1]), rows)
    features = np.arange(len(ids), dtype=np.float64).reshape(-1, 1)
    calibration = _calibration()
    artifacts = SourceArtifactPaths(
        dataset=tmp_path / "dataset.bin",
        split=tmp_path / "split.bin",
        preprocessor=tmp_path / "preprocessor.bin",
        fitted_model=tmp_path / "model.bin",
    )
    for path, payload in (
        (artifacts.dataset, b"independent development dataset"),
        (artifacts.split, b"development split and ordered IDs"),
        (artifacts.preprocessor, b"fitted preprocessing state"),
        (artifacts.fitted_model, b"fitted model artifact"),
    ):
        path.write_bytes(payload)
    witness = capture_independent_score_witness(
        dataset_id="synthetic-dataset",
        record_ids=ids,
        target_rows=targets,
        evaluation_matrix=features,
        model=model,
        calibration=calibration,
        artifacts=artifacts,
    )
    source = capture_evaluator_score_source(
        dataset_id="synthetic-dataset",
        record_ids=ids,
        evaluation_matrix=features,
        model=model,
        calibration=calibration,
    )
    intervention = apply_evaluator_mapping_fault(source, selected_shard_count=4)
    return _Case(
        ids, targets, features, model, calibration, artifacts, witness, source, intervention
    )


def _verify(
    case: _Case,
    *,
    witness: IndependentScoreWitness | None = None,
    source: EvaluatorScoreSource | None = None,
    intervention: MappingInterventionResult | None = None,
    scoring_target_rows: tuple[tuple[str, int], ...] | None = None,
    evaluation_matrix: NDArray[np.float64] | None = None,
    calibration: CalibrationResult | None = None,
) -> MappingVerification:
    return verify_evaluator_mapping(
        witness=case.witness if witness is None else witness,
        source=case.source if source is None else source,
        intervention=case.intervention if intervention is None else intervention,
        scoring_target_rows=case.targets if scoring_target_rows is None else scoring_target_rows,
        reference_model=case.model,
        evaluation_matrix=case.features if evaluation_matrix is None else evaluation_matrix,
        reference_calibration=case.calibration if calibration is None else calibration,
        artifacts=case.artifacts,
    )


def test_independent_witness_recomputes_scores_dose_and_runtime_metric(tmp_path: Path) -> None:
    case = _case(tmp_path)
    result = verify_evaluator_mapping(
        witness=case.witness,
        source=case.source,
        intervention=case.intervention,
        scoring_target_rows=case.targets,
        reference_model=case.model,
        evaluation_matrix=case.features,
        reference_calibration=case.calibration,
        artifacts=case.artifacts,
    )
    assert case.witness.raw_score_rows == case.source.raw_score_rows
    assert result.artifact_hashes == case.witness.artifact_hashes
    assert result.raw_scores_sha256 == case.witness.raw_scores_sha256
    assert result.calibrated_scores_sha256 == case.witness.calibrated_scores_sha256
    assert result.target_binding_sha256 == case.witness.target_binding_sha256
    assert result.affected_record_ids == case.intervention.affected_record_ids
    assert result.changed_score_record_ids == case.intervention.changed_score_record_ids
    assert result.nominal_shard_fraction == 0.2
    assert result.achieved_affected_fraction == len(result.affected_record_ids) / len(case.ids)
    assert result.faulty_log_loss > result.healthy_log_loss
    assert result.corrected_log_loss == result.healthy_log_loss


def test_zero_dose_and_selected_tie_keep_affected_separate_from_changed(tmp_path: Path) -> None:
    case = _case(tmp_path, tie_selected_row=True)
    result = _verify(case)
    assert len(result.affected_record_ids) > len(result.changed_score_record_ids)
    zero = apply_evaluator_mapping_fault(case.source, selected_shard_count=0)
    zero_result = _verify(case, intervention=zero)
    assert zero_result.affected_record_ids == ()
    assert zero_result.changed_score_record_ids == ()
    assert zero_result.achieved_affected_fraction == 0.0
    assert zero_result.faulty_log_loss == zero_result.healthy_log_loss


@pytest.mark.parametrize("role", ["dataset", "split", "preprocessor", "fitted_model"])
def test_upstream_artifact_change_is_rejected(tmp_path: Path, role: str) -> None:
    case = _case(tmp_path)
    getattr(case.artifacts, role).write_bytes(b"changed after independent capture")
    with pytest.raises(ScoreMappingVerificationError, match="upstream source changed"):
        _verify(case)


def test_changed_reference_model_or_features_is_rejected(tmp_path: Path) -> None:
    case = _case(tmp_path)
    case.model.probabilities[0, :] = (0.8, 0.2)
    with pytest.raises(ScoreMappingVerificationError, match="upstream source changed"):
        _verify(case)


@pytest.mark.parametrize("changed_source", ["artifact", "features"])
def test_source_mutation_during_score_capture_fails_closed(
    tmp_path: Path, changed_source: str
) -> None:
    case = _case(tmp_path)

    class MutatingModel(_FixedModel):
        def predict_proba(self, features: NDArray[np.float64]) -> NDArray[np.float64]:
            if changed_source == "artifact":
                case.artifacts.fitted_model.write_bytes(b"model changed while scoring")
            else:
                features[0, 0] = 999.0
            return super().predict_proba(features)

    model = MutatingModel(case.model.classes_, case.model.probabilities)
    with pytest.raises(ScoreMappingVerificationError, match="during score capture"):
        capture_independent_score_witness(
            dataset_id="synthetic-dataset",
            record_ids=case.ids,
            target_rows=case.targets,
            evaluation_matrix=case.features,
            model=model,
            calibration=case.calibration,
            artifacts=case.artifacts,
        )


def test_real_fitted_model_scores_are_replayed_without_adapter_metric(tmp_path: Path) -> None:
    training_x = np.array([[-3.0], [-2.0], [-1.0], [1.0], [2.0], [3.0]])
    training_y = np.array([0, 0, 0, 1, 1, 1])
    evaluation_x = np.array([[-1.5], [-0.5], [0.5], [2.5]])
    targets = (("r0", 0), ("r1", 0), ("r2", 1), ("r3", 1))
    model = LogisticRegression(random_state=42).fit(training_x, training_y)
    case = _case(tmp_path)
    witness = capture_independent_score_witness(
        dataset_id="synthetic-dataset",
        record_ids=tuple(row_id for row_id, _ in targets),
        target_rows=targets,
        evaluation_matrix=evaluation_x,
        model=model,
        calibration=case.calibration,
        artifacts=case.artifacts,
    )
    source = capture_evaluator_score_source(
        dataset_id="synthetic-dataset",
        record_ids=witness.record_ids,
        evaluation_matrix=evaluation_x,
        model=model,
        calibration=case.calibration,
    )
    intervention = apply_evaluator_mapping_fault(source, selected_shard_count=4)
    result = verify_evaluator_mapping(
        witness=witness,
        source=source,
        intervention=intervention,
        scoring_target_rows=targets,
        reference_model=model,
        evaluation_matrix=evaluation_x,
        reference_calibration=case.calibration,
        artifacts=case.artifacts,
    )
    assert np.asarray(witness.raw_score_rows) == pytest.approx(model.predict_proba(evaluation_x))
    assert result.corrected_log_loss == result.healthy_log_loss


def test_reversed_class_order_and_nonidentity_calibration_are_replayed(tmp_path: Path) -> None:
    case = _case(tmp_path)
    ids = ("r0", "r1")
    targets = (("r0", 0), ("r1", 1))
    features = np.array([[0.0], [1.0]])
    model = _FixedModel(np.array([1, 0]), np.array([[0.2, 0.8], [0.8, 0.2]]))
    calibration = case.calibration.model_copy(update={"intercept": 0.2, "slope": 0.8})
    witness = capture_independent_score_witness(
        dataset_id="synthetic-dataset",
        record_ids=ids,
        target_rows=targets,
        evaluation_matrix=features,
        model=model,
        calibration=calibration,
        artifacts=case.artifacts,
    )
    source = capture_evaluator_score_source(
        dataset_id="synthetic-dataset",
        record_ids=ids,
        evaluation_matrix=features,
        model=model,
        calibration=calibration,
    )
    intervention = apply_evaluator_mapping_fault(source, selected_shard_count=4)
    result = verify_evaluator_mapping(
        witness=witness,
        source=source,
        intervention=intervention,
        scoring_target_rows=targets,
        reference_model=model,
        evaluation_matrix=features,
        reference_calibration=calibration,
        artifacts=case.artifacts,
    )
    assert witness.model_classes == (1, 0)
    assert witness.calibrated_score_rows != witness.raw_score_rows
    assert result.corrected_log_loss == result.healthy_log_loss
    case.model.probabilities[0, :] = (0.9, 0.1)
    altered_features = case.features.copy()
    altered_features[0, 0] = 999.0
    with pytest.raises(ScoreMappingVerificationError, match="upstream source changed"):
        _verify(case, evaluation_matrix=altered_features)
    case.model.classes_ = np.array([1, 0])
    with pytest.raises(ScoreMappingVerificationError, match="upstream source changed"):
        _verify(case)


def test_changed_calibration_or_witness_hash_is_rejected(tmp_path: Path) -> None:
    case = _case(tmp_path)
    altered_calibration = case.calibration.model_copy(update={"intercept": 0.1})
    with pytest.raises(ScoreMappingVerificationError, match="upstream source changed"):
        _verify(case, calibration=altered_calibration)
    forged_witness = replace(case.witness, raw_scores_sha256="0" * 64)
    with pytest.raises(ScoreMappingVerificationError, match="upstream source changed"):
        _verify(case, witness=forged_witness)


def test_target_flip_and_row_target_misalignment_fail_before_metric(tmp_path: Path) -> None:
    case = _case(tmp_path)
    flipped = list(case.targets)
    flipped[0] = (case.ids[0], 1)
    flipped[1] = (case.ids[1], 0)
    with pytest.raises(ScoreMappingVerificationError, match="scoring targets differ"):
        _verify(case, scoring_target_rows=tuple(flipped))
    misaligned = list(case.targets)
    misaligned[0], misaligned[1] = misaligned[1], misaligned[0]
    with pytest.raises(ScoreMappingVerificationError, match="exact row IDs"):
        _verify(case, scoring_target_rows=tuple(misaligned))


def test_equal_aggregate_loss_from_target_flip_does_not_identify_mapping(
    tmp_path: Path,
) -> None:
    case = _case(tmp_path)
    selected_even = next(
        record_id for record_id in case.ids if _selected(record_id) and int(record_id[1:]) % 2 == 0
    )
    selected_odd = next(
        record_id for record_id in case.ids if _selected(record_id) and int(record_id[1:]) % 2 == 1
    )
    ids = (selected_even, selected_odd)
    targets = ((selected_even, 0), (selected_odd, 1))
    flipped_targets = ((selected_even, 1), (selected_odd, 0))
    features = np.array([[0.0], [1.0]])
    model = _FixedModel(np.array([0, 1]), np.array([[0.9, 0.1], [0.1, 0.9]]))
    witness = capture_independent_score_witness(
        dataset_id="synthetic-dataset",
        record_ids=ids,
        target_rows=targets,
        evaluation_matrix=features,
        model=model,
        calibration=case.calibration,
        artifacts=case.artifacts,
    )
    source = capture_evaluator_score_source(
        dataset_id="synthetic-dataset",
        record_ids=ids,
        evaluation_matrix=features,
        model=model,
        calibration=case.calibration,
    )
    intervention = apply_evaluator_mapping_fault(source, selected_shard_count=4)
    faulty_loss = reference_prior_standardized_log_loss(
        true_labels=(0, 1), probabilities=intervention.positive_probabilities
    )
    flipped_target_loss = reference_prior_standardized_log_loss(
        true_labels=(1, 0), probabilities=(0.1, 0.9)
    )
    assert faulty_loss == pytest.approx(flipped_target_loss)
    with pytest.raises(ScoreMappingVerificationError, match="scoring targets differ"):
        verify_evaluator_mapping(
            witness=witness,
            source=source,
            intervention=intervention,
            scoring_target_rows=flipped_targets,
            reference_model=model,
            evaluation_matrix=features,
            reference_calibration=case.calibration,
            artifacts=case.artifacts,
        )


@pytest.mark.parametrize(
    "field",
    [
        "row_shards",
        "affected_record_ids",
        "changed_score_record_ids",
        "positive_probabilities",
        "faulty_column_classes",
        "selected_shard_count",
    ],
)
def test_intervention_metadata_or_scores_cannot_self_certify(tmp_path: Path, field: str) -> None:
    case = _case(tmp_path)
    tampered_by_field = {
        "row_shards": replace(
            case.intervention, row_shards=tuple(reversed(case.intervention.row_shards))
        ),
        "affected_record_ids": replace(case.intervention, affected_record_ids=()),
        "changed_score_record_ids": replace(case.intervention, changed_score_record_ids=()),
        "positive_probabilities": replace(
            case.intervention, positive_probabilities=(0.5,) * len(case.ids)
        ),
        "faulty_column_classes": replace(case.intervention, faulty_column_classes=(0, 1)),
        "selected_shard_count": replace(case.intervention, selected_shard_count=2),
    }
    with pytest.raises(ScoreMappingVerificationError, match="independent reconstruction"):
        _verify(case, intervention=tampered_by_field[field])


def test_adapter_source_must_match_independent_model_replay(tmp_path: Path) -> None:
    case = _case(tmp_path)
    other_model = _FixedModel(np.array([0, 1]), np.asarray([(0.7, 0.3)] * len(case.ids)))
    other_source = capture_evaluator_score_source(
        dataset_id="synthetic-dataset",
        record_ids=case.ids,
        evaluation_matrix=case.features,
        model=other_model,
        calibration=case.calibration,
    )
    with pytest.raises(ScoreMappingVerificationError, match="adapter source differs"):
        _verify(case, source=other_source)


@pytest.mark.parametrize("bad_targets", [(("r0", True), ("r1", 0)), (("r1", 0), ("r0", 1))])
def test_reference_ledger_rejects_nonbinary_or_reordered_rows(
    tmp_path: Path, bad_targets: tuple[tuple[str, object], ...]
) -> None:
    case = _case(tmp_path)
    with pytest.raises(ScoreMappingVerificationError, match="source target values"):
        capture_independent_score_witness(
            dataset_id="synthetic-dataset",
            record_ids=("r0", "r1"),
            target_rows=bad_targets,  # type: ignore[arg-type]
            evaluation_matrix=np.array([[1.0], [2.0]]),
            model=_FixedModel(np.array([0, 1]), np.array([[0.9, 0.1], [0.1, 0.9]])),
            calibration=case.calibration,
            artifacts=case.artifacts,
        )


def test_source_artifact_symlink_is_rejected(tmp_path: Path) -> None:
    case = _case(tmp_path)
    linked = tmp_path / "dataset-link.bin"
    linked.symlink_to(case.artifacts.dataset)
    artifacts = replace(case.artifacts, dataset=linked)
    with pytest.raises(ScoreMappingVerificationError, match="regular file"):
        capture_independent_score_witness(
            dataset_id="synthetic-dataset",
            record_ids=case.ids,
            target_rows=case.targets,
            evaluation_matrix=case.features,
            model=case.model,
            calibration=case.calibration,
            artifacts=artifacts,
        )
