"""One offline development cell for a wrong fitted-model artifact load.

Only the frozen V3 train/development members are predicted. The intervention
changes the loader-selected model for the whole call, never rows, targets,
preprocessing, calibration, adapter, or scoring. This is not M4 admission.
"""

from __future__ import annotations

import io
import json
import platform
import warnings
from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path
from typing import Any, Literal

import joblib  # type: ignore[import-untyped]
import numpy as np
from numpy.typing import NDArray
from sklearn.ensemble import HistGradientBoostingClassifier  # type: ignore[import-untyped]
from sklearn.exceptions import ConvergenceWarning  # type: ignore[import-untyped]
from threadpoolctl import threadpool_limits  # type: ignore[import-untyped]

from aletheia_lab.benchmark.p2.canonical import canonical_sha256
from aletheia_lab.benchmark.p2.confirmatory_v3_datasets import (
    load_v3_dataset_snapshot_for_registration,
)
from aletheia_lab.benchmark.p2.confirmatory_v3_protocol import (
    load_v3_confirmatory_protocol,
    verify_v3_protocol_artifacts,
)
from aletheia_lab.benchmark.p2.confirmatory_v3_runtime import (
    CalibrationResult,
    PreprocessorState,
    apply_logit_calibration,
    fit_logit_calibration,
    fit_preprocessor,
    reconstruct_runtime_split,
    transform_features,
)
from aletheia_lab.benchmark.p2.confirmatory_v3_shift import (
    reference_prior_standardized_log_loss,
)
from aletheia_lab.benchmark.p2.score_mapping_development import _model_for, _private_output_guard
from aletheia_lab.content_hashing import bytes_sha256, file_sha256

DATASET_ID = "uci_online_shoppers_purchasing_intention"
MODEL_KIND: Literal["hist_gradient_boosting"] = "hist_gradient_boosting"
A_ITERATIONS = 100
B_ITERATIONS = 25
MIN_CALIBRATED_LOG_LOSS_DELTA = 0.01
PROTOCOL_RELATIVE_PATH = "configs/benchmark/p2_label_noise_shift_v3_protocol.json"


class ModelArtifactBindingError(ValueError):
    """The source, trusted artifact, or development control is inconsistent."""


@dataclass(frozen=True)
class DevelopmentSource:
    archive_sha256: str
    split_sha256: str
    membership_sha256: str
    preprocessor: PreprocessorState
    train: NDArray[np.float64]
    development: NDArray[np.float64]
    train_ids: tuple[str, ...]
    train_targets: tuple[int, ...]
    development_ids: tuple[str, ...]
    development_targets: tuple[int, ...]
    calibration_parameters: tuple[float, int, float]

    def bindings(self) -> dict[str, Any]:
        return {
            "dataset_id": DATASET_ID,
            "archive_sha256": self.archive_sha256,
            "split_sha256": self.split_sha256,
            "membership_sha256": self.membership_sha256,
            "preprocessor_sha256": canonical_sha256(self.preprocessor.model_dump(mode="json")),
            "train_target_binding_sha256": canonical_sha256(
                {"rows": tuple(zip(self.train_ids, self.train_targets, strict=True))}
            ),
            "development_target_binding_sha256": canonical_sha256(
                {"rows": tuple(zip(self.development_ids, self.development_targets, strict=True))}
            ),
            "development_matrix_sha256": canonical_sha256(
                {
                    "record_ids": self.development_ids,
                    "features": tuple(
                        tuple(0.0 if value == 0 else float(value) for value in row)
                        for row in self.development
                    ),
                }
            ),
        }


def load_development_source(*, root: Path, archive: Path) -> DevelopmentSource:
    """Resolve only the existing train/development split; never score sealed rows."""

    protocol_path = root / PROTOCOL_RELATIVE_PATH
    protocol = load_v3_confirmatory_protocol(protocol_path)
    _, manifest, _ = verify_v3_protocol_artifacts(protocol, root=root)
    candidates = [item for item in manifest.datasets if item.dataset_id == DATASET_ID]
    receipts = [item for item in protocol.dataset_splits if item.dataset_id == DATASET_ID]
    if len(candidates) != 1 or len(receipts) != 1:
        raise ModelArtifactBindingError("the fixed development source is unavailable")
    dataset, receipt = candidates[0], receipts[0]
    if archive.is_symlink() or not archive.is_file() or archive.name != dataset.archive.file_name:
        raise ModelArtifactBindingError("the bound source archive is unavailable")
    _, frame = load_v3_dataset_snapshot_for_registration(dataset=dataset, archive_path=archive)
    split = reconstruct_runtime_split(
        protocol=protocol, dataset=dataset, frame=frame, receipt=receipt
    )
    train_indices, development_indices = split.indices("train"), split.indices("development")
    if not train_indices or not development_indices:
        raise ModelArtifactBindingError("the development split is empty")
    columns = list(dataset.analysis_features)
    train_frame = frame.iloc[list(train_indices)].loc[:, columns]
    development_frame = frame.iloc[list(development_indices)].loc[:, columns]
    state = fit_preprocessor(dataset, train_frame)
    train = transform_features(dataset=dataset, state=state, frame=train_frame)
    development = transform_features(dataset=dataset, state=state, frame=development_frame)
    return DevelopmentSource(
        archive_sha256=file_sha256(archive),
        split_sha256=file_sha256(protocol_path),
        membership_sha256=split.membership_sha256,
        preprocessor=state,
        train=train,
        development=development,
        train_ids=tuple(split.record_ids[index] for index in train_indices),
        train_targets=tuple(split.labels[index] for index in train_indices),
        development_ids=tuple(split.record_ids[index] for index in development_indices),
        development_targets=tuple(split.labels[index] for index in development_indices),
        calibration_parameters=(
            protocol.models.calibration_probability_clip,
            protocol.models.calibration_max_iter,
            protocol.models.calibration_tolerance,
        ),
    )


def fit_models(
    source: DevelopmentSource,
) -> tuple[HistGradientBoostingClassifier, HistGradientBoostingClassifier]:
    """Fit the predeclared A/B runs; do not select B by evaluation loss."""

    models = []
    for iterations in (A_ITERATIONS, B_ITERATIONS):
        model = _model_for(MODEL_KIND)
        model.set_params(max_iter=iterations)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always", ConvergenceWarning)
            model.fit(source.train, np.asarray(source.train_targets, dtype=np.int64))
        if any(issubclass(item.category, ConvergenceWarning) for item in caught):
            raise ModelArtifactBindingError("a model emitted a convergence warning")
        if tuple(int(value) for value in model.classes_) != (0, 1):
            raise ModelArtifactBindingError("model class order changed")
        models.append(model)
    return models[0], models[1]


def fit_healthy_calibration(
    source: DevelopmentSource, model: HistGradientBoostingClassifier
) -> CalibrationResult:
    clip, max_iter, tolerance = source.calibration_parameters
    return fit_logit_calibration(
        model.predict_proba(source.development)[:, 1].tolist(),
        source.development_targets,
        probability_clip=clip,
        max_iter=max_iter,
        tolerance=tolerance,
    )


def score_model(
    source: DevelopmentSource,
    model: HistGradientBoostingClassifier,
    calibration: CalibrationResult,
) -> dict[str, Any]:
    raw = np.asarray(model.predict_proba(source.development), dtype=np.float64)
    if (
        raw.shape != (len(source.development_ids), 2)
        or not np.isfinite(raw).all()
        or not np.allclose(raw.sum(axis=1), 1.0, rtol=0.0, atol=1e-12)
    ):
        raise ModelArtifactBindingError("loaded model returned invalid binary scores")
    raw_positive = tuple(float(value) for value in raw[:, 1])
    calibrated_positive = apply_logit_calibration(raw_positive, calibration)
    raw_rows = tuple((float(row[0]), float(row[1])) for row in raw)
    calibrated_rows = tuple((1.0 - value, value) for value in calibrated_positive)
    return {
        "raw_scores_sha256": canonical_sha256(
            {"record_ids": source.development_ids, "scores": raw_rows}
        ),
        "calibrated_scores_sha256": canonical_sha256(
            {"record_ids": source.development_ids, "scores": calibrated_rows}
        ),
        "raw_log_loss": reference_prior_standardized_log_loss(
            true_labels=source.development_targets, probabilities=raw_positive
        ),
        "calibrated_log_loss": reference_prior_standardized_log_loss(
            true_labels=source.development_targets, probabilities=calibrated_positive
        ),
        "raw_positive": raw_positive,
        "calibrated_positive": calibrated_positive,
    }


def _trusted_load(
    path: Path, expected_sha256: str, *, expected_feature_count: int
) -> tuple[HistGradientBoostingClassifier, str]:
    """Deserialize only bytes just produced inside the new private run directory."""

    if path.is_symlink() or not path.is_file() or path.stat().st_size > 100_000_000:
        raise ModelArtifactBindingError("trusted model artifact is unavailable")
    payload = path.read_bytes()
    actual_sha256 = bytes_sha256(payload)
    if actual_sha256 != expected_sha256:
        raise ModelArtifactBindingError("model bytes differ from the pre-load manifest")
    model = joblib.load(io.BytesIO(payload))
    if (
        not isinstance(model, HistGradientBoostingClassifier)
        or tuple(int(value) for value in model.classes_) != (0, 1)
        or model.n_features_in_ != expected_feature_count
    ):
        raise ModelArtifactBindingError("loaded object is not the declared model type")
    return model, actual_sha256


def _publish_json(path: Path, value: dict[str, Any]) -> None:
    if path.exists() or path.is_symlink():
        raise ModelArtifactBindingError("development artifact must not be overwritten")
    path.write_text(
        json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )


def evidence_views(receipt: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Candidate observations only; these are not validated LLM messages."""

    paths = receipt["paths"]
    base = {
        "schema_version": "model-artifact-binding-development-view/v1",
        "source": DATASET_ID,
        "healthy_log_loss": round(paths["healthy"]["calibrated_log_loss"], 6),
        "observed_log_loss": round(paths["faulty"]["calibrated_log_loss"], 6),
        "calibration_unchanged": True,
        "target_binding_unchanged": True,
    }
    full = {
        **base,
        "declared_artifact_sha256": paths["faulty"]["declared_artifact_sha256"],
        "loaded_artifact_sha256": paths["faulty"]["loaded_artifact_sha256"],
        "healthy_raw_scores_sha256": paths["healthy"]["raw_scores_sha256"],
        "observed_raw_scores_sha256": paths["faulty"]["raw_scores_sha256"],
    }
    return {
        "full": full,
        "missing_key": base,
        "noisy": {**full, "runtime": receipt["runtime"]},
        "misleading": {
            **full,
            "compatible_alternative_artifact_sha256": receipt["manifest"]["artifact_B_sha256"],
        },
    }


def run_development_cell(*, root: Path, archive: Path, output: Path) -> dict[str, Any]:
    """Execute exactly one prespecified development cell in a new private directory."""

    _private_output_guard(root, output)
    source = load_development_source(root=root.resolve(strict=True), archive=archive)
    with threadpool_limits(limits=1):
        model_a, model_b = fit_models(source)
        calibration = fit_healthy_calibration(source, model_a)
        output.mkdir(mode=0o700)
        path_a, path_b = output / "artifact_A.joblib", output / "artifact_B.joblib"
        joblib.dump(model_a, path_a)
        joblib.dump(model_b, path_b)
        manifest = {
            "schema_version": "model-artifact-binding-development-manifest/v1",
            "source": source.bindings(),
            "model_family": MODEL_KIND,
            "artifact_A_iterations": A_ITERATIONS,
            "artifact_B_iterations": B_ITERATIONS,
            "artifact_A_sha256": file_sha256(path_a),
            "artifact_B_sha256": file_sha256(path_b),
            "calibration_sha256": calibration.canonical_sha256(),
            "calibration_fit_partition": "development",
            "metric_partition": "development",
            "minimum_calibrated_log_loss_delta": MIN_CALIBRATED_LOG_LOSS_DELTA,
        }
        _publish_json(output / "manifest.json", manifest)
        paths: dict[str, dict[str, Any]] = {}
        for name, declared, loaded in (
            ("healthy", "A", "A"),
            ("faulty", "A", "B"),
            ("sham", "A", "A"),
            ("corrected", "A", "A"),
            ("promoted_B", "B", "B"),
        ):
            expected = manifest[f"artifact_{loaded}_sha256"]
            if not isinstance(expected, str):
                raise ModelArtifactBindingError("pre-load artifact digest is not a string")
            selected, loaded_sha = _trusted_load(
                output / f"artifact_{loaded}.joblib",
                expected,
                expected_feature_count=source.development.shape[1],
            )
            if selected.max_iter != (A_ITERATIONS if loaded == "A" else B_ITERATIONS):
                raise ModelArtifactBindingError("loaded model recipe differs from the manifest")
            measured = score_model(source, selected, calibration)
            paths[name] = {
                "declared_artifact_sha256": manifest[f"artifact_{declared}_sha256"],
                "loaded_artifact_sha256": loaded_sha,
                **{key: value for key, value in measured.items() if not key.endswith("positive")},
            }
        healthy = score_model(source, model_a, calibration)
        faulty = score_model(source, model_b, calibration)
    changed_raw = sum(
        before != after
        for before, after in zip(healthy["raw_positive"], faulty["raw_positive"], strict=True)
    )
    changed_calibrated = sum(
        before != after
        for before, after in zip(
            healthy["calibrated_positive"], faulty["calibrated_positive"], strict=True
        )
    )
    delta = paths["faulty"]["calibrated_log_loss"] - paths["healthy"]["calibrated_log_loss"]
    status = (
        "development_positive_control_observed"
        if changed_raw > 0 and changed_calibrated > 0 and delta >= MIN_CALIBRATED_LOG_LOSS_DELTA
        else "development_effect_insufficient"
    )
    receipt = {
        "schema_version": "model-artifact-binding-development-receipt/v1",
        "status": status,
        "manifest_sha256": file_sha256(output / "manifest.json"),
        "manifest": manifest,
        "runtime": {
            "python": platform.python_version(),
            **{
                name: version(name) for name in ("numpy", "scikit-learn", "joblib", "threadpoolctl")
            },
            "thread_limit": 1,
        },
        "train_count": len(source.train_ids),
        "development_count": len(source.development_ids),
        "changed_raw_score_rows": changed_raw,
        "changed_calibrated_score_rows": changed_calibrated,
        "calibrated_log_loss_delta": delta,
        "paths": paths,
        "registered_attempt": False,
        "provider_calls": 0,
        "sealed_predictions_or_metrics_computed": False,
        "scientific_admission": False,
    }
    if (
        paths["healthy"] != paths["sham"]
        or paths["healthy"] != paths["corrected"]
        or paths["faulty"]["raw_scores_sha256"] != paths["promoted_B"]["raw_scores_sha256"]
    ):
        raise ModelArtifactBindingError("sham, correction or legitimate B control failed")
    _publish_json(output / "receipt.json", receipt)
    _publish_json(output / "candidate-evidence-views.json", evidence_views(receipt))
    return receipt
