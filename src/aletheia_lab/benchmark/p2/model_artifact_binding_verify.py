"""Rebuild the M4 development cell without trusting its injected loader path."""

from __future__ import annotations

import json
import platform
from importlib.metadata import version
from pathlib import Path
from typing import Any

from threadpoolctl import threadpool_limits  # type: ignore[import-untyped]

from aletheia_lab.benchmark.p2.model_artifact_binding_development import (
    A_ITERATIONS,
    B_ITERATIONS,
    MIN_CALIBRATED_LOG_LOSS_DELTA,
    MODEL_KIND,
    ModelArtifactBindingError,
    _trusted_load,
    evidence_views,
    fit_healthy_calibration,
    fit_models,
    load_development_source,
    score_model,
)
from aletheia_lab.content_hashing import file_sha256


def _json_file(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ModelArtifactBindingError("private development evidence is missing")
    value: Any = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ModelArtifactBindingError("private development evidence must be an object")
    return value


def _verify_manifest(
    *, output: Path, manifest: dict[str, Any], receipt: dict[str, Any], source: Any
) -> None:
    if (
        manifest.get("schema_version") != "model-artifact-binding-development-manifest/v1"
        or receipt.get("schema_version") != "model-artifact-binding-development-receipt/v1"
        or receipt.get("manifest") != manifest
        or receipt.get("manifest_sha256") != file_sha256(output / "manifest.json")
    ):
        raise ModelArtifactBindingError("manifest/receipt binding differs")
    if (
        manifest.get("source") != source.bindings()
        or manifest.get("model_family") != MODEL_KIND
        or manifest.get("artifact_A_iterations") != A_ITERATIONS
        or manifest.get("artifact_B_iterations") != B_ITERATIONS
        or manifest.get("calibration_fit_partition") != "development"
        or manifest.get("metric_partition") != "development"
        or manifest.get("minimum_calibrated_log_loss_delta") != MIN_CALIBRATED_LOG_LOSS_DELTA
    ):
        raise ModelArtifactBindingError("source or declared recipe differs")


def _verify_saved_artifacts(output: Path, manifest: dict[str, Any]) -> None:
    for identity in ("A", "B"):
        path = output / f"artifact_{identity}.joblib"
        if (
            path.is_symlink()
            or not path.is_file()
            or file_sha256(path) != manifest.get(f"artifact_{identity}_sha256")
        ):
            raise ModelArtifactBindingError("saved artifact differs from the pre-load manifest")
    if manifest["artifact_A_sha256"] == manifest["artifact_B_sha256"]:
        raise ModelArtifactBindingError("the two saved artifacts have identical bytes")


def verify_development_cell(*, root: Path, archive: Path, output: Path) -> dict[str, Any]:
    """Verify source lineage, actual load trace and scores by fresh refitting."""

    root = root.resolve(strict=True)
    output = output.absolute()
    if output.is_symlink() or not output.is_dir() or output.resolve().is_relative_to(root):
        raise ModelArtifactBindingError("development evidence must remain outside the repo")
    manifest = _json_file(output / "manifest.json")
    receipt = _json_file(output / "receipt.json")
    views = _json_file(output / "candidate-evidence-views.json")
    source = load_development_source(root=root, archive=archive)
    _verify_manifest(output=output, manifest=manifest, receipt=receipt, source=source)
    _verify_saved_artifacts(output, manifest)
    expected_runtime = {
        "python": platform.python_version(),
        **{name: version(name) for name in ("numpy", "scikit-learn", "joblib", "threadpoolctl")},
        "thread_limit": 1,
    }
    if receipt.get("runtime") != expected_runtime:
        raise ModelArtifactBindingError("execution runtime differs from the recorded runtime")
    with threadpool_limits(limits=1):
        model_a, model_b = fit_models(source)
        calibration = fit_healthy_calibration(source, model_a)
        score_a = score_model(source, model_a, calibration)
        score_b = score_model(source, model_b, calibration)
        # Verify the serialized objects actually used by the loader against an
        # independently refitted witness, not merely their filenames/digests.
        for identity, expected in (("A", score_a), ("B", score_b)):
            saved, _ = _trusted_load(
                output / f"artifact_{identity}.joblib",
                manifest[f"artifact_{identity}_sha256"],
                expected_feature_count=source.development.shape[1],
            )
            if saved.max_iter != (A_ITERATIONS if identity == "A" else B_ITERATIONS):
                raise ModelArtifactBindingError("serialized model recipe differs")
            if score_model(source, saved, calibration) != expected:
                raise ModelArtifactBindingError(
                    "serialized model scores differ from independent refit"
                )
    if manifest.get("calibration_sha256") != calibration.canonical_sha256():
        raise ModelArtifactBindingError("calibration changed since the source run")
    scores = {"A": score_a, "B": score_b}
    expected_paths = {}
    for name, declared, loaded in (
        ("healthy", "A", "A"),
        ("faulty", "A", "B"),
        ("sham", "A", "A"),
        ("corrected", "A", "A"),
        ("promoted_B", "B", "B"),
    ):
        expected_paths[name] = {
            "declared_artifact_sha256": manifest[f"artifact_{declared}_sha256"],
            "loaded_artifact_sha256": manifest[f"artifact_{loaded}_sha256"],
            **{key: value for key, value in scores[loaded].items() if not key.endswith("positive")},
        }
    if receipt.get("paths") != expected_paths:
        raise ModelArtifactBindingError("loaded-path scores or artifact identity do not replay")
    changed_raw = sum(
        before != after
        for before, after in zip(score_a["raw_positive"], score_b["raw_positive"], strict=True)
    )
    changed_calibrated = sum(
        before != after
        for before, after in zip(
            score_a["calibrated_positive"], score_b["calibrated_positive"], strict=True
        )
    )
    delta = score_b["calibrated_log_loss"] - score_a["calibrated_log_loss"]
    status = (
        "development_positive_control_observed"
        if changed_raw > 0 and changed_calibrated > 0 and delta >= MIN_CALIBRATED_LOG_LOSS_DELTA
        else "development_effect_insufficient"
    )
    if (
        receipt.get("status") != status
        or receipt.get("train_count") != len(source.train_ids)
        or receipt.get("development_count") != len(source.development_ids)
        or receipt.get("changed_raw_score_rows") != changed_raw
        or receipt.get("changed_calibrated_score_rows") != changed_calibrated
        or receipt.get("calibrated_log_loss_delta") != delta
        or receipt.get("registered_attempt") is not False
        or receipt.get("provider_calls") != 0
        or receipt.get("sealed_predictions_or_metrics_computed") is not False
        or receipt.get("scientific_admission") is not False
        or views != evidence_views(receipt)
    ):
        raise ModelArtifactBindingError("development receipt or view does not independently replay")
    return {
        "status": status,
        "development_count": len(source.development_ids),
        "changed_raw_score_rows": changed_raw,
        "changed_calibrated_score_rows": changed_calibrated,
        "calibrated_log_loss_delta": delta,
        "manifest_sha256": receipt["manifest_sha256"],
        "receipt_sha256": file_sha256(output / "receipt.json"),
        "registered_attempt": False,
        "provider_calls": 0,
        "scientific_admission": False,
    }
