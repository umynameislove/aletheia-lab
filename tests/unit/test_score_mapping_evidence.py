"""Synthetic paired-world checks for the new M5 evidence conditions."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, cast

import numpy as np
import pytest
from numpy.typing import NDArray

from aletheia_lab.benchmark.p2.confirmatory_v3_runtime import CalibrationResult
from aletheia_lab.benchmark.p2.score_mapping_evidence import (
    DevelopmentObservation,
    ScoreMappingEvidenceError,
    audit_matched_target_rival,
    build_development_evidence_views,
    mapping_observation,
    target_binding_rival_observation,
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
    ScoreMappingVerificationError,
    SourceArtifactPaths,
    capture_independent_score_witness,
)


@dataclass
class _FixedModel:
    classes_: NDArray[np.int64]
    score_rows: NDArray[np.float64]

    def predict_proba(self, features: NDArray[np.float64]) -> NDArray[np.float64]:
        return self.score_rows.copy()


@dataclass(frozen=True)
class _Case:
    ids: tuple[str, str]
    targets: tuple[tuple[str, int], tuple[str, int]]
    model: _FixedModel
    calibration: CalibrationResult
    witness: IndependentScoreWitness
    source: EvaluatorScoreSource
    intervention: MappingInterventionResult
    reference_features: NDArray[np.float64]
    evaluation_features: NDArray[np.float64]
    artifacts: SourceArtifactPaths


def _selected_id(parity: int) -> str:
    for index in range(parity, 1000, 2):
        record_id = f"r{index}"
        payload = f"{DEVELOPMENT_SELECTOR_SEED}\x00synthetic-dataset\x00{record_id}".encode()
        if int.from_bytes(hashlib.sha256(payload).digest(), "big") % 20 < 4:
            return record_id
    raise AssertionError("the fixed synthetic shard selector has no selected row")


def _case(tmp_path: Path, *, reversed_classes: bool = False) -> _Case:
    ids = (_selected_id(0), _selected_id(1))
    targets = ((ids[0], 0), (ids[1], 1))
    model = (
        _FixedModel(np.array([1, 0]), np.array([[0.1, 0.9], [0.9, 0.1]]))
        if reversed_classes
        else _FixedModel(np.array([0, 1]), np.array([[0.9, 0.1], [0.1, 0.9]]))
    )
    reference_features = np.array([[-2.0], [-1.0], [0.0], [1.0]])
    evaluation_features = np.array([[0.0], [1.0]])
    calibration = CalibrationResult(
        intercept=0.0,
        slope=1.0,
        iterations=0,
        converged=True,
        gradient_infinity_norm=0.0,
        development_record_count=2,
    )
    artifacts = SourceArtifactPaths(
        dataset=tmp_path / "dataset.bin",
        split=tmp_path / "split.bin",
        preprocessor=tmp_path / "preprocessor.bin",
        fitted_model=tmp_path / "model.bin",
    )
    for path, payload in (
        (artifacts.dataset, b"synthetic source data"),
        (artifacts.split, b"synthetic source split"),
        (artifacts.preprocessor, b"synthetic preprocessor"),
        (artifacts.fitted_model, b"synthetic model"),
    ):
        path.write_bytes(payload)
    witness = capture_independent_score_witness(
        dataset_id="synthetic-dataset",
        record_ids=ids,
        target_rows=targets,
        evaluation_matrix=evaluation_features,
        model=model,
        calibration=calibration,
        artifacts=artifacts,
    )
    source = capture_evaluator_score_source(
        dataset_id="synthetic-dataset",
        record_ids=ids,
        evaluation_matrix=evaluation_features,
        model=model,
        calibration=calibration,
    )
    intervention = apply_evaluator_mapping_fault(source, selected_shard_count=4)
    return _Case(
        ids,
        targets,
        model,
        calibration,
        witness,
        source,
        intervention,
        reference_features,
        evaluation_features,
        artifacts,
    )


def _mapping(case: _Case) -> DevelopmentObservation:
    return mapping_observation(
        witness=case.witness,
        source=case.source,
        intervention=case.intervention,
        scoring_target_rows=case.targets,
        reference_model=case.model,
        reference_calibration=case.calibration,
        artifacts=case.artifacts,
        reference_features=case.reference_features,
        evaluation_features=case.evaluation_features,
    )


def _rival(case: _Case) -> DevelopmentObservation:
    return target_binding_rival_observation(
        witness=case.witness,
        source=case.source,
        scoring_target_rows=((case.ids[0], 1), (case.ids[1], 0)),
        reference_features=case.reference_features,
        evaluation_features=case.evaluation_features,
    )


def _items(view: dict[str, object]) -> list[dict[str, Any]]:
    items = view["items"]
    assert isinstance(items, list)
    return cast(list[dict[str, Any]], items)


@pytest.mark.parametrize("reversed_classes", [False, True])
def test_full_distinguishes_equal_loss_mapping_and_target_rival(
    tmp_path: Path, reversed_classes: bool
) -> None:
    case = _case(tmp_path, reversed_classes=reversed_classes)
    mapping = _mapping(case)
    rival = _rival(case)
    audit = audit_matched_target_rival(mapping, rival)
    mapping_views = build_development_evidence_views(mapping)
    rival_views = build_development_evidence_views(rival)

    assert audit.construct_loci_differ
    assert audit.full_witness_distinguishes
    assert audit.missing_payloads_identical
    assert audit.symptom_only_balanced_accuracy_ceiling == 0.5
    assert mapping.reference_log_loss == pytest.approx(rival.reference_log_loss)
    assert mapping.observed_log_loss == pytest.approx(rival.observed_log_loss)
    assert mapping_views["missing_key"] == rival_views["missing_key"]
    assert mapping_views["full"] != rival_views["full"]
    assert _items(mapping_views["full"])[2]["payload"]["model_column_classes"] == (
        (1, 0) if reversed_classes else (0, 1)
    )
    assert _items(mapping_views["full"])[2]["payload"]["evaluator_column_classes"] == (
        (0, 1) if reversed_classes else (1, 0)
    )
    assert _items(mapping_views["full"])[3]["payload"] == {
        "scoring_targets_match_source_rows": True
    }
    assert _items(rival_views["full"])[3]["payload"] == {"scoring_targets_match_source_rows": False}
    assert mapping.corrected_log_loss == mapping.reference_log_loss
    assert rival.corrected_log_loss == rival.observed_log_loss


def test_sibling_views_preserve_common_evidence_without_answer_metadata(tmp_path: Path) -> None:
    case = _case(tmp_path)
    views = build_development_evidence_views(_mapping(case))
    assert set(views) == {"full", "missing_key", "noisy", "misleading"}
    full = _items(views["full"])
    assert [item["id"] for item in full] == [
        "performance-comparison",
        "score-source-controls",
        "column-interpretation",
        "target-binding-check",
        "independent-recomputation",
    ]
    assert _items(views["missing_key"]) == full[:2]
    assert [item["id"] for item in _items(views["missing_key"])] == [
        "performance-comparison",
        "score-source-controls",
    ]
    assert _items(views["noisy"])[:-1] == full
    assert _items(views["misleading"])[:-1] == full
    assert _items(views["noisy"])[-1]["id"] == "feature-shape-observation"
    clue = _items(views["misleading"])[-1]
    assert clue["id"] == "cohort-feature-comparison"
    assert clue["payload"]["mean_vector_distance"] > 0
    assert clue["payload"]["provenance"] == "unmodified_reference_and_evaluation_feature_rows"
    for view in views.values():
        encoded = json.dumps(view, sort_keys=True).lower()
        for forbidden in (
            "synthetic-dataset",
            case.ids[0],
            case.ids[1],
            "oracle",
            "injector",
            "selected_shard",
            "selector_seed",
            "dose",
            "mechanism_label",
            "sha256",
            "condition",
        ):
            assert forbidden not in encoded
    _items(views["full"])[0]["payload"]["record_count"] = 999
    assert _items(views["missing_key"])[0]["payload"]["record_count"] == 2


def test_pairwise_audit_rejects_symptom_and_source_shortcuts(tmp_path: Path) -> None:
    case = _case(tmp_path)
    mapping = _mapping(case)
    rival = _rival(case)
    with pytest.raises(ScoreMappingEvidenceError, match="underlying performance"):
        audit_matched_target_rival(
            mapping,
            replace(
                rival,
                observed_log_loss=rival.observed_log_loss + 0.01,
                corrected_log_loss=rival.corrected_log_loss + 0.01,
            ),
        )
    with pytest.raises(ScoreMappingEvidenceError, match="distinct loci"):
        audit_matched_target_rival(mapping, replace(rival, source_identity_sha256="0" * 64))
    with pytest.raises(ScoreMappingEvidenceError, match="added evidence"):
        audit_matched_target_rival(mapping, replace(rival, feature_mean_distance=0.1))


def test_zero_dose_and_scoring_target_flip_do_not_create_mapping_evidence(tmp_path: Path) -> None:
    case = _case(tmp_path)
    zero = apply_evaluator_mapping_fault(case.source, selected_shard_count=0)
    with pytest.raises(ScoreMappingEvidenceError, match="no changed score"):
        mapping_observation(
            witness=case.witness,
            source=case.source,
            intervention=zero,
            scoring_target_rows=case.targets,
            reference_model=case.model,
            reference_calibration=case.calibration,
            artifacts=case.artifacts,
            reference_features=case.reference_features,
            evaluation_features=case.evaluation_features,
        )
    with pytest.raises(ScoreMappingVerificationError, match="scoring targets differ"):
        mapping_observation(
            witness=case.witness,
            source=case.source,
            intervention=case.intervention,
            scoring_target_rows=((case.ids[0], 1), (case.ids[1], 0)),
            reference_model=case.model,
            reference_calibration=case.calibration,
            artifacts=case.artifacts,
            reference_features=case.reference_features,
            evaluation_features=case.evaluation_features,
        )


def test_changed_source_and_feature_inputs_fail_before_projection(tmp_path: Path) -> None:
    case = _case(tmp_path)
    case.artifacts.dataset.write_bytes(b"changed source")
    with pytest.raises(ScoreMappingVerificationError, match="upstream source changed"):
        _mapping(case)
    case.artifacts.dataset.write_bytes(b"synthetic source data")
    changed_features = case.evaluation_features.copy()
    changed_features[0, 0] = 99.0
    with pytest.raises(ScoreMappingVerificationError, match="upstream source changed"):
        mapping_observation(
            witness=case.witness,
            source=case.source,
            intervention=case.intervention,
            scoring_target_rows=case.targets,
            reference_model=case.model,
            reference_calibration=case.calibration,
            artifacts=case.artifacts,
            reference_features=case.reference_features,
            evaluation_features=changed_features,
        )


def test_changed_score_matrix_or_model_class_metadata_fails_before_projection(
    tmp_path: Path,
) -> None:
    case = _case(tmp_path)
    case.model.score_rows[0, :] = (0.8, 0.2)
    with pytest.raises(ScoreMappingVerificationError, match="upstream source changed"):
        _mapping(case)
    case.model.score_rows[0, :] = (0.9, 0.1)
    case.model.classes_ = np.array([1, 0])
    with pytest.raises(ScoreMappingVerificationError, match="upstream source changed"):
        _mapping(case)


@pytest.mark.parametrize(
    "rival_rows",
    [(("different-row", 1), ("r1", 0)), (("r0", True), ("r1", 0))],
)
def test_rival_requires_exact_row_ids_and_binary_targets(
    tmp_path: Path, rival_rows: tuple[tuple[str, object], ...]
) -> None:
    case = _case(tmp_path)
    with pytest.raises(ScoreMappingEvidenceError, match="rival targets"):
        target_binding_rival_observation(
            witness=case.witness,
            source=case.source,
            scoring_target_rows=rival_rows,  # type: ignore[arg-type]
            reference_features=case.reference_features,
            evaluation_features=case.evaluation_features,
        )


def test_row_permutation_is_not_a_valid_target_rival_binding(tmp_path: Path) -> None:
    case = _case(tmp_path)
    with pytest.raises(ScoreMappingEvidenceError, match="rival targets"):
        target_binding_rival_observation(
            witness=case.witness,
            source=case.source,
            scoring_target_rows=(case.targets[1], case.targets[0]),
            reference_features=case.reference_features,
            evaluation_features=case.evaluation_features,
        )


def test_misleading_signal_cannot_be_fabricated_from_identical_features(tmp_path: Path) -> None:
    case = _case(tmp_path)
    observation = mapping_observation(
        witness=case.witness,
        source=case.source,
        intervention=case.intervention,
        scoring_target_rows=case.targets,
        reference_model=case.model,
        reference_calibration=case.calibration,
        artifacts=case.artifacts,
        reference_features=case.evaluation_features.copy(),
        evaluation_features=case.evaluation_features,
    )
    with pytest.raises(ScoreMappingEvidenceError, match="real nonzero competing clue"):
        build_development_evidence_views(observation)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("observed_log_loss", float("nan")),
        ("feature_mean_distance", float("inf")),
        ("example_observed_positive", 0.123),
    ],
)
def test_forged_nonfinite_or_inconsistent_observation_is_rejected(
    tmp_path: Path, field: str, value: float
) -> None:
    observation = _mapping(_case(tmp_path))
    with pytest.raises(ScoreMappingEvidenceError, match="malformed"):
        build_development_evidence_views(replace(observation, **cast(Any, {field: value})))
