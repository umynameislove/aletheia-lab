"""Offline score-mapping feasibility on V3 train/development partitions only.

This is not a registered attempt. The historical registered execution and sealed
partition are never scored by this module.
"""

from __future__ import annotations

import json
import warnings
from pathlib import Path
from typing import Any

import joblib  # type: ignore[import-untyped]
import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier  # type: ignore[import-untyped]
from sklearn.exceptions import ConvergenceWarning  # type: ignore[import-untyped]
from sklearn.linear_model import LogisticRegression  # type: ignore[import-untyped]

from aletheia_lab.benchmark.p2.canonical import canonical_sha256
from aletheia_lab.benchmark.p2.confirmatory_v3_datasets import (
    load_v3_dataset_snapshot_for_registration,
)
from aletheia_lab.benchmark.p2.confirmatory_v3_protocol import (
    load_v3_confirmatory_protocol,
    verify_v3_protocol_artifacts,
)
from aletheia_lab.benchmark.p2.confirmatory_v3_runtime import (
    ModelKind,
    PreprocessorState,
    fit_logit_calibration,
    fit_preprocessor,
    fit_registered_model,
    reconstruct_runtime_split,
    transform_features,
)
from aletheia_lab.benchmark.p2.score_mapping_evidence import (
    build_development_evidence_views,
    mapping_observation,
    target_binding_rival_observation,
)
from aletheia_lab.benchmark.p2.score_mapping_intervention import (
    DevelopmentSelectedShards,
    apply_evaluator_mapping_fault,
    capture_evaluator_score_source,
)
from aletheia_lab.benchmark.p2.score_mapping_verification import (
    SourceArtifactPaths,
    capture_independent_score_witness,
    verify_evaluator_mapping,
)

MODEL_KINDS: tuple[ModelKind, ModelKind] = (
    "logistic_regression",
    "hist_gradient_boosting",
)
DOSES: tuple[
    DevelopmentSelectedShards,
    DevelopmentSelectedShards,
    DevelopmentSelectedShards,
    DevelopmentSelectedShards,
] = (0, 1, 2, 4)


class ScoreMappingDevelopmentError(ValueError):
    """An offline development source or measurement failed closed."""


def _model_for(kind: ModelKind) -> LogisticRegression | HistGradientBoostingClassifier:
    if kind == "logistic_regression":
        return LogisticRegression(C=1.0, solver="lbfgs", max_iter=1000, random_state=42)
    if kind == "hist_gradient_boosting":
        return HistGradientBoostingClassifier(
            learning_rate=0.1,
            max_iter=100,
            max_leaf_nodes=31,
            l2_regularization=0.0,
            early_stopping=False,
            random_state=43,
        )
    raise ScoreMappingDevelopmentError("model kind is outside the fixed development matrix")


def _fit_reference_model(
    kind: ModelKind, train: np.ndarray, targets: tuple[int, ...]
) -> LogisticRegression | HistGradientBoostingClassifier:
    model = _model_for(kind)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", ConvergenceWarning)
        model.fit(train, np.asarray(targets, dtype=np.int64))
    if any(issubclass(item.category, ConvergenceWarning) for item in caught):
        raise ScoreMappingDevelopmentError("registered model emitted a convergence warning")
    if tuple(int(value) for value in model.classes_) != (0, 1):
        raise ScoreMappingDevelopmentError("model class order differs from expected binary classes")
    return model


def _private_output_guard(root: Path, output: Path) -> None:
    root = root.resolve(strict=True)
    output = output.absolute()
    if output.is_symlink() or output.exists() or output.resolve().is_relative_to(root):
        raise ScoreMappingDevelopmentError(
            "output must be a new private directory outside the repository"
        )
    if output.parent.is_symlink() or not output.parent.is_dir():
        raise ScoreMappingDevelopmentError("private output parent must exist and not be a symlink")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _cell(
    *,
    dataset: Any,
    archive: Path,
    split: Any,
    protocol: Any,
    frame: Any,
    train_indices: tuple[int, ...],
    development_indices: tuple[int, ...],
    kind: ModelKind,
    cell_dir: Path,
    split_path: Path,
) -> dict[str, Any]:
    features = list(dataset.analysis_features)
    train_frame = frame.iloc[list(train_indices)].loc[:, features]
    development_frame = frame.iloc[list(development_indices)].loc[:, features]
    state = fit_preprocessor(dataset, train_frame)
    train = transform_features(dataset=dataset, state=state, frame=train_frame)
    development = transform_features(dataset=dataset, state=state, frame=development_frame)
    train_ids = tuple(split.record_ids[index] for index in train_indices)
    train_targets = tuple(split.labels[index] for index in train_indices)
    development_ids = tuple(split.record_ids[index] for index in development_indices)
    development_targets = tuple(split.labels[index] for index in development_indices)
    target_rows = tuple(zip(development_ids, development_targets, strict=True))
    cell_dir.mkdir()
    preprocessor_path = cell_dir / "preprocessor.json"
    preprocessor_path.write_text(state.model_dump_json(indent=2) + "\n", encoding="utf-8")
    stored_state = PreprocessorState.model_validate_json(preprocessor_path.read_text())
    if stored_state != state or not np.array_equal(
        transform_features(dataset=dataset, state=stored_state, frame=development_frame),
        development,
    ):
        raise ScoreMappingDevelopmentError(
            "stored train-only preprocessor cannot reproduce development"
        )

    model = _fit_reference_model(kind, train, train_targets)
    raw_scores = np.asarray(model.predict_proba(development), dtype=np.float64)
    if raw_scores.shape != (len(development_ids), 2) or not np.isfinite(raw_scores).all():
        raise ScoreMappingDevelopmentError("fresh two-column model scores are invalid")
    calibration = fit_logit_calibration(
        raw_scores[:, 1].tolist(),
        development_targets,
        probability_clip=protocol.models.calibration_probability_clip,
        max_iter=protocol.models.calibration_max_iter,
        tolerance=protocol.models.calibration_tolerance,
    )
    model_path = cell_dir / "fitted_model.joblib"
    joblib.dump(model, model_path)
    # Only deserialize the just-created private artifact, never an arbitrary input.
    stored_model = joblib.load(model_path)
    if not np.array_equal(stored_model.predict_proba(development), raw_scores):
        raise ScoreMappingDevelopmentError("stored fitted model cannot reproduce its score columns")

    # Independent runtime refit checks that the fresh source follows the
    # historical estimator/calibration contract without touching sealed rows.
    runtime = fit_registered_model(
        protocol=protocol,
        dataset=dataset,
        model_kind=kind,
        training_role="clean",
        state=state,
        training_matrix=train,
        training_record_ids=train_ids,
        training_targets=train_targets,
        development_matrix=development,
        development_record_ids=development_ids,
        development_targets=development_targets,
        evaluation_matrix=development,
        evaluation_record_ids=development_ids,
    )
    if runtime.calibration != calibration or runtime.development_record_ids != development_ids:
        raise ScoreMappingDevelopmentError(
            "fresh model and registered runtime calibration disagree"
        )
    artifacts = SourceArtifactPaths(archive, split_path, preprocessor_path, model_path)
    witness = capture_independent_score_witness(
        dataset_id=dataset.dataset_id,
        record_ids=development_ids,
        target_rows=target_rows,
        evaluation_matrix=development,
        model=stored_model,
        calibration=calibration,
        artifacts=artifacts,
    )
    source = capture_evaluator_score_source(
        dataset_id=dataset.dataset_id,
        record_ids=development_ids,
        evaluation_matrix=development,
        model=stored_model,
        calibration=calibration,
    )
    healthy = tuple(row[witness.model_classes.index(1)] for row in witness.calibrated_score_rows)
    if not np.allclose(runtime.development_probabilities, healthy, atol=1e-12, rtol=0.0):
        raise ScoreMappingDevelopmentError(
            "two-column source differs from independently refitted runtime"
        )
    measurements: list[dict[str, Any]] = []
    for dose in DOSES:
        intervention = apply_evaluator_mapping_fault(source, selected_shard_count=dose)
        checked = verify_evaluator_mapping(
            witness=witness,
            source=source,
            intervention=intervention,
            scoring_target_rows=target_rows,
            reference_model=stored_model,
            evaluation_matrix=development,
            reference_calibration=calibration,
            artifacts=artifacts,
        )
        item: dict[str, Any] = {
            "selected_shards": dose,
            "nominal_fraction": checked.nominal_shard_fraction,
            "achieved_fraction": checked.achieved_affected_fraction,
            "affected_rows": len(checked.affected_record_ids),
            "changed_score_rows": len(checked.changed_score_record_ids),
            "healthy_log_loss": checked.healthy_log_loss,
            "faulty_log_loss": checked.faulty_log_loss,
            "corrected_log_loss": checked.corrected_log_loss,
            "delta_log_loss": checked.faulty_log_loss - checked.healthy_log_loss,
        }
        if dose:
            observation = mapping_observation(
                witness=witness,
                source=source,
                intervention=intervention,
                scoring_target_rows=target_rows,
                reference_model=stored_model,
                reference_calibration=calibration,
                artifacts=artifacts,
                reference_features=train,
                evaluation_features=development,
            )
            views = build_development_evidence_views(observation)
            item["evidence_view_sha256"] = {
                name: canonical_sha256(view) for name, view in views.items()
            }
            affected = set(checked.affected_record_ids)
            rival_rows = tuple(
                (record_id, 1 - target if record_id in affected else target)
                for record_id, target in target_rows
            )
            rival = target_binding_rival_observation(
                witness=witness,
                source=source,
                scoring_target_rows=rival_rows,
                reference_features=train,
                evaluation_features=development,
            )
            rival_views = build_development_evidence_views(rival)
            item["target_flip_rival"] = {
                "log_loss": rival.observed_log_loss,
                "delta_from_mapping_loss": rival.observed_log_loss - checked.faulty_log_loss,
                "missing_key_payload_matches": views["missing_key"] == rival_views["missing_key"],
                "full_payload_differs": views["full"] != rival_views["full"],
            }
        measurements.append(item)
    if measurements[0]["affected_rows"] != 0 or measurements[0]["delta_log_loss"] != 0.0:
        raise ScoreMappingDevelopmentError("zero-dose control did not reproduce healthy scoring")
    return {
        "dataset_id": dataset.dataset_id,
        "model_kind": kind,
        "train_count": len(train_ids),
        "development_count": len(development_ids),
        "split_membership_sha256": split.membership_sha256,
        "train_target_binding_sha256": canonical_sha256(
            {"rows": tuple(zip(train_ids, train_targets, strict=True))}
        ),
        "source_artifact_sha256": {
            "dataset": witness.artifact_hashes.dataset,
            "split": witness.artifact_hashes.split,
            "preprocessor": witness.artifact_hashes.preprocessor,
            "fitted_model": witness.artifact_hashes.fitted_model,
        },
        "source_score_sha256": witness.raw_scores_sha256,
        "target_binding_sha256": witness.target_binding_sha256,
        "calibration_sha256": witness.calibration_sha256,
        "runtime_refit_agreed": True,
        "measurements": measurements,
    }


def run_score_mapping_development_feasibility(*, root: Path, output: Path) -> dict[str, Any]:
    """Execute all four prespecified development cells, writing privately only."""

    _private_output_guard(root, output)
    root = root.resolve(strict=True)
    protocol_path = root / "configs/benchmark/p2_label_noise_shift_v3_protocol.json"
    protocol = load_v3_confirmatory_protocol(protocol_path)
    _, manifest, _ = verify_v3_protocol_artifacts(protocol, root=root)
    output.mkdir(mode=0o700)
    cells: list[dict[str, Any]] = []
    for dataset, receipt in zip(manifest.datasets, protocol.dataset_splits, strict=True):
        if (dataset.dataset_id, dataset.role) != (receipt.dataset_id, receipt.role):
            raise ScoreMappingDevelopmentError("manifest and protocol dataset order disagree")
        archive = root / "data/raw/p2-v3" / dataset.archive.file_name
        _, frame = load_v3_dataset_snapshot_for_registration(dataset=dataset, archive_path=archive)
        split = reconstruct_runtime_split(
            protocol=protocol, dataset=dataset, frame=frame, receipt=receipt
        )
        train_indices = split.indices("train")
        development_indices = split.indices("development")
        if not train_indices or not development_indices:
            raise ScoreMappingDevelopmentError("registered development partition is empty")
        for kind in MODEL_KINDS:
            cell_dir = output / f"{dataset.dataset_id}-{kind}"
            cell = _cell(
                dataset=dataset,
                archive=archive,
                split=split,
                protocol=protocol,
                frame=frame,
                train_indices=train_indices,
                development_indices=development_indices,
                kind=kind,
                cell_dir=cell_dir,
                split_path=protocol_path,
            )
            _write_json(cell_dir / "measurement.json", cell)
            cells.append(cell)
    summary = {
        "schema_version": "score-mapping-development-feasibility/v1",
        "status": "development_only",
        "registered_attempt": False,
        "provider_calls": 0,
        "sealed_predictions_or_metrics_computed": False,
        "protocol_sha256": protocol.canonical_sha256(),
        "cell_count": len(cells),
        "cells": cells,
    }
    _write_json(output / "summary.json", summary)
    return summary
