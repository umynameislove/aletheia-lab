"""A separately planned, loader-only early-budget artifact development control.

The original 100/25 cell is immutable. This cell fixes 100/1 before fitting,
splits existing development rows for calibration/measurement, and makes raw
reference-prior loss primary. Neither a positive effect nor replay admits M4.
"""

from __future__ import annotations

import io
import json
import math
import platform
import warnings
from importlib.metadata import version
from pathlib import Path
from typing import Any

import joblib  # type: ignore[import-untyped]
import numpy as np
from sklearn.exceptions import ConvergenceWarning  # type: ignore[import-untyped]
from threadpoolctl import threadpool_limits  # type: ignore[import-untyped]

from aletheia_lab.benchmark.p2.canonical import canonical_sha256
from aletheia_lab.benchmark.p2.confirmatory_v3_runtime import (
    CalibrationResult,
    apply_logit_calibration,
    fit_logit_calibration,
)
from aletheia_lab.benchmark.p2.confirmatory_v3_shift import (
    reference_prior_standardized_log_loss,
)
from aletheia_lab.benchmark.p2.model_artifact_binding_development import (
    DevelopmentSource,
    ModelArtifactBindingError,
    _trusted_load,
    load_development_source,
)
from aletheia_lab.benchmark.p2.score_mapping_development import _model_for
from aletheia_lab.content_hashing import file_sha256
from aletheia_lab.filesystem import write_new_file

A_ITERATIONS = 100
B_ITERATIONS = 1
PARTITION_SEED = 20260930
MIN_RAW_LOSS_DELTA = 0.01
SCORER_ID = "raw-reference-prior-standardized-log-loss/v1"
ADAPTER_ID = "binary-classes-0-1/identity"
CASES = (
    ("healthy", "A", "A", "A"),
    ("faulty", "A", "B", "A"),
    ("sham", "A", "A", "A"),
    ("corrected", "A", "A", "A"),
    ("legitimate_B", "B", "B", "B"),
    ("manifest_text_only", "A", "A", "B"),
)
CODE_PATHS = (
    "src/aletheia_lab/benchmark/p2/model_artifact_binding_forward.py",
    "src/aletheia_lab/benchmark/p2/model_artifact_binding_forward_verify.py",
    "src/aletheia_lab/benchmark/p2/model_artifact_binding_development.py",
    "src/aletheia_lab/benchmark/p2/score_mapping_development.py",
    "src/aletheia_lab/benchmark/p2/confirmatory_v3_datasets.py",
    "src/aletheia_lab/benchmark/p2/confirmatory_v3_protocol.py",
    "src/aletheia_lab/benchmark/p2/confirmatory_v3_runtime.py",
    "src/aletheia_lab/benchmark/p2/confirmatory_v3_shift.py",
    "src/aletheia_lab/benchmark/p2/canonical.py",
    "src/aletheia_lab/content_hashing.py",
    "src/aletheia_lab/project/identity.py",
    "src/aletheia_lab/filesystem.py",
    "scripts/model_artifact_binding_development.py",
    "pyproject.toml",
)


def json_file(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ModelArtifactBindingError("retained development file is unavailable")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ModelArtifactBindingError("retained development file must be an object")
    return value


def publish_json(path: Path, value: dict[str, Any]) -> None:
    write_new_file(
        path, (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()
    )


def private_directory(root: Path, output: Path, *, existing: bool) -> Path:
    output = output.absolute()
    if any(path.is_symlink() for path in (output, *output.parents)):
        raise ModelArtifactBindingError("private output must not traverse a symlink")
    if output.resolve().is_relative_to(root.resolve(strict=True)):
        raise ModelArtifactBindingError("raw artifacts must remain outside the repository")
    if (
        not output.parent.is_dir()
        or (output.is_dir() if existing else not output.exists()) is False
    ):
        raise ModelArtifactBindingError("private output has the wrong creation state")
    return output


def predecessor_bindings(root: Path, predecessor: Path) -> dict[str, str]:
    predecessor = private_directory(root, predecessor, existing=True)
    receipt = json_file(predecessor / "receipt.json")
    if receipt.get("status") != "development_effect_insufficient":
        raise ModelArtifactBindingError("the historical insufficient cell is required")
    files = sorted(predecessor.iterdir())
    if not files or any(path.is_symlink() or not path.is_file() for path in files):
        raise ModelArtifactBindingError("historical cell contains invalid artifacts")
    return {path.name: file_sha256(path) for path in files}


def partition_indices(source: DevelopmentSource) -> tuple[tuple[int, ...], tuple[int, ...]]:
    ids, labels = source.development_ids, source.development_targets
    if (
        len(ids) != len(labels)
        or len(set(ids)) != len(ids)
        or len(set(source.train_ids)) != len(source.train_ids)
        or set(ids).intersection(source.train_ids)
        or len(source.train_ids) != len(source.train_targets)
        or set(source.train_targets) != {0, 1}
        or set(labels) != {0, 1}
        or source.train.ndim != 2
        or source.development.ndim != 2
        or source.train.shape != (len(source.train_ids), source.development.shape[1])
        or source.development.shape[0] != len(ids)
        or not np.isfinite(source.train).all()
        or not np.isfinite(source.development).all()
    ):
        raise ModelArtifactBindingError("source rows, classes or matrices are inconsistent")
    calibration: set[int] = set()
    for label in (0, 1):
        ranked = sorted(
            (index for index, target in enumerate(labels) if target == label),
            key=lambda index: (
                canonical_sha256({"seed": PARTITION_SEED, "label": label, "row": ids[index]}),
                ids[index],
            ),
        )
        if len(ranked) < 4:
            raise ModelArtifactBindingError("each development class needs at least four rows")
        calibration.update(ranked[: len(ranked) // 2])
    return (
        tuple(index for index in range(len(ids)) if index in calibration),
        tuple(index for index in range(len(ids)) if index not in calibration),
    )


def partition_bindings(source: DevelopmentSource) -> dict[str, Any]:
    calibration, measurement = partition_indices(source)
    result: dict[str, Any] = {"seed": PARTITION_SEED, "rule": "per-class-half-hash-ranked"}
    for role, indices in (("calibration", calibration), ("measurement", measurement)):
        rows = [(source.development_ids[i], source.development_targets[i]) for i in indices]
        features = [[0.0 if v == 0 else float(v) for v in source.development[i]] for i in indices]
        result[role] = {
            "count": len(indices),
            "class_counts": [sum(y == label for _, y in rows) for label in (0, 1)],
            "row_target_sha256": canonical_sha256({"rows": rows}),
            "features_sha256": canonical_sha256({"rows": [row for row, _ in rows], "x": features}),
        }
    return result


def runtime_bindings() -> dict[str, Any]:
    return {
        "python": platform.python_version(),
        "platform": platform.platform(),
        **{
            name: version(name)
            for name in ("numpy", "scipy", "scikit-learn", "joblib", "threadpoolctl")
        },
        "thread_limit": 1,
    }


def model_recipe(iterations: int) -> dict[str, Any]:
    return dict(_model_for("hist_gradient_boosting").set_params(max_iter=iterations).get_params())


def expected_plan(*, root: Path, source: DevelopmentSource, predecessor: Path) -> dict[str, Any]:
    return {
        "schema_version": "model-artifact-binding-forward-plan/v1",
        "scope": "development-only; prior development exposure retained",
        "source": source.bindings(),
        "partitions": partition_bindings(source),
        "preprocessing_fit": "original_training_only",
        "recipes": {"A": model_recipe(A_ITERATIONS), "B": model_recipe(B_ITERATIONS)},
        "B_role": "separately-fitted-early-budget-artifact",
        "calibration_fit": "calibration-subset-A-only",
        "calibration_parameters": list(source.calibration_parameters),
        "primary_endpoint": SCORER_ID,
        "minimum_raw_loss_delta": MIN_RAW_LOSS_DELTA,
        "healthy_better_than_training_prior_required": True,
        "secondary_endpoints": [
            "raw-empirical-log-loss",
            "fixed-A-calibrated-reference-and-empirical",
        ],
        "comparison_tolerance": 1e-12,
        "cases": [list(case) for case in CASES],
        "rivals": ["adapter-column-reversal", "two-row-target-swap"],
        "failure_rule": "retain-insufficient-or-failed-cell; no replacement search",
        "runtime": runtime_bindings(),
        "code_sha256": {path: file_sha256(root / path) for path in CODE_PATHS},
        "predecessor_sha256": predecessor_bindings(root, predecessor),
        "provider_calls": 0,
        "scientific_admission": False,
        "protected_execution_permitted": False,
    }


def prepare_forward_cell(
    *, root: Path, archive: Path, output: Path, predecessor: Path
) -> dict[str, Any]:
    """Fix recipe, endpoint and membership before any new fitted-model predictions."""

    root = root.resolve(strict=True)
    output = private_directory(root, output, existing=False)
    source = load_development_source(root=root, archive=archive)
    plan = expected_plan(root=root, source=source, predecessor=predecessor)
    output.mkdir(mode=0o700)
    publish_json(output / "plan.json", plan)
    return {
        "status": "development_prepared",
        "plan_sha256": file_sha256(output / "plan.json"),
        "partitions": plan["partitions"],
        "provider_calls": 0,
    }


def validate_plan(
    *, root: Path, archive: Path, output: Path, predecessor: Path
) -> tuple[DevelopmentSource, dict[str, Any]]:
    output = private_directory(root, output, existing=True)
    source = load_development_source(root=root, archive=archive)
    plan = json_file(output / "plan.json")
    if plan != expected_plan(root=root, source=source, predecessor=predecessor):
        raise ModelArtifactBindingError(
            "forward source, recipe, code, runtime or predecessor drift"
        )
    return source, plan


def fit_forward_models(source: DevelopmentSource) -> tuple[Any, Any]:
    models = []
    for iterations in (A_ITERATIONS, B_ITERATIONS):
        model = _model_for("hist_gradient_boosting").set_params(max_iter=iterations)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always", ConvergenceWarning)
            model.fit(source.train, np.asarray(source.train_targets, dtype=np.int64))
        if any(issubclass(item.category, ConvergenceWarning) for item in caught):
            raise ModelArtifactBindingError("fit emitted a convergence warning")
        if tuple(model.classes_) != (0, 1) or model.n_iter_ != iterations:
            raise ModelArtifactBindingError("fitted model recipe did not execute as declared")
        models.append(model)
    return models[0], models[1]


def calibration_for(source: DevelopmentSource, model: Any) -> CalibrationResult:
    indices, _ = partition_indices(source)
    clip, max_iter, tolerance = source.calibration_parameters
    return fit_logit_calibration(
        model.predict_proba(source.development[list(indices)])[:, 1].tolist(),
        [source.development_targets[i] for i in indices],
        probability_clip=clip,
        max_iter=max_iter,
        tolerance=tolerance,
    )


def probability_rows(model: Any, matrix: Any) -> list[list[float]]:
    raw = np.asarray(model.predict_proba(matrix), dtype=np.float64)
    if (
        raw.shape != (len(matrix), 2)
        or not np.isfinite(raw).all()
        or np.any((raw <= 0) | (raw >= 1))
    ):
        raise ModelArtifactBindingError("binary model probabilities must be strictly inside (0,1)")
    if not np.allclose(raw.sum(axis=1), 1.0, rtol=0.0, atol=1e-12):
        raise ModelArtifactBindingError("model probability columns do not sum to one")
    return [[float(a), float(b)] for a, b in raw]


def score_metrics(targets: list[int], positive: list[float]) -> dict[str, float]:
    reference = reference_prior_standardized_log_loss(true_labels=targets, probabilities=positive)
    empirical = math.fsum(
        -math.log(p if y else 1.0 - p) for y, p in zip(targets, positive, strict=True)
    ) / len(targets)
    return {"reference_prior_log_loss": reference, "empirical_log_loss": empirical}


def score_bundle(
    *,
    row_ids: list[str],
    targets: list[int],
    raw: list[list[float]],
    calibration: CalibrationResult,
    clip: float,
) -> dict[str, Any]:
    positive = [row[1] for row in raw]
    calibrated = list(apply_logit_calibration(positive, calibration, clip=clip))
    return {
        "raw": raw,
        "calibrated_positive": calibrated,
        "raw_scores_sha256": canonical_sha256({"rows": row_ids, "scores": raw}),
        "calibrated_scores_sha256": canonical_sha256({"rows": row_ids, "scores": calibrated}),
        "raw_metrics": score_metrics(targets, positive),
        "calibrated_metrics": score_metrics(targets, calibrated),
    }


def load_event(
    *,
    case: str,
    path: Path,
    expected_sha256: str,
    declared_sha256: str,
    reported_sha256: str,
    feature_count: int,
    invariants: dict[str, str],
) -> tuple[Any, dict[str, Any]]:
    # This boundary reads/hashes the actual buffer passed to deserialization.
    # The declared identity never supplies the observed digest.
    model, actual_sha256 = _trusted_load(
        path, expected_sha256, expected_feature_count=feature_count
    )
    actual_recipe = model.get_params()
    if actual_recipe != model_recipe(int(model.n_iter_)):
        raise ModelArtifactBindingError("loaded model recipe differs from the fixed family")
    return model, {
        "case": case,
        "declared_artifact_sha256": declared_sha256,
        "actual_loaded_sha256": actual_sha256,
        "reported_manifest_sha256": reported_sha256,
        "actual_iterations": model.n_iter_,
        "actual_recipe_sha256": canonical_sha256(actual_recipe),
        "classes": [int(v) for v in model.classes_],
        "feature_count": int(model.n_features_in_),
        **invariants,
    }


def other_locus_controls(
    *, row_ids: list[str], targets: list[int], raw: list[list[float]], artifact_sha256: str
) -> dict[str, Any]:
    swapped = list(targets)
    left, right = targets.index(0), targets.index(1)
    swapped[left], swapped[right] = swapped[right], swapped[left]
    raw_hash = canonical_sha256({"rows": row_ids, "scores": raw})
    return {
        "adapter_column_reversal": {
            "declared_artifact_sha256": artifact_sha256,
            "actual_loaded_sha256": artifact_sha256,
            "pre_adapter_scores_sha256": raw_hash,
            "adapter": "binary-column-reversal",
            "targets": targets,
            "scored_positive": [p[0] for p in raw],
            "metrics": score_metrics(targets, [p[0] for p in raw]),
        },
        "two_row_target_swap": {
            "declared_artifact_sha256": artifact_sha256,
            "actual_loaded_sha256": artifact_sha256,
            "pre_adapter_scores_sha256": raw_hash,
            "adapter": ADAPTER_ID,
            "targets": swapped,
            "scored_positive": [p[1] for p in raw],
            "metrics": score_metrics(swapped, [p[1] for p in raw]),
        },
        "matched_model_visible_inputs_proven": False,
    }


def development_decision(
    *, score_a: dict[str, Any], score_b: dict[str, Any], prior_loss: float
) -> dict[str, Any]:
    changed = sum(a != b for a, b in zip(score_a["raw"], score_b["raw"], strict=True))
    changed_calibrated = sum(
        a != b
        for a, b in zip(score_a["calibrated_positive"], score_b["calibrated_positive"], strict=True)
    )
    delta = (
        score_b["raw_metrics"]["reference_prior_log_loss"]
        - score_a["raw_metrics"]["reference_prior_log_loss"]
    )
    healthy_adequate = score_a["raw_metrics"]["reference_prior_log_loss"] < prior_loss
    observed = changed > 0 and delta >= MIN_RAW_LOSS_DELTA and healthy_adequate
    return {
        "status": "development_positive_control_observed"
        if observed
        else "development_effect_insufficient",
        "changed_raw_score_rows": changed,
        "changed_calibrated_score_rows": changed_calibrated,
        "raw_reference_prior_loss_delta": delta,
        "calibrated_reference_prior_loss_delta": score_b["calibrated_metrics"][
            "reference_prior_log_loss"
        ]
        - score_a["calibrated_metrics"]["reference_prior_log_loss"],
        "raw_empirical_loss_delta": score_b["raw_metrics"]["empirical_log_loss"]
        - score_a["raw_metrics"]["empirical_log_loss"],
        "healthy_better_than_training_prior": healthy_adequate,
        "scientific_admission": False,
        "U4_authorized": False,
        "provider_calls": 0,
        "registered_attempt": False,
        "protected_predictions_or_metrics_computed": False,
    }


def _execute_prepared(
    *, source: DevelopmentSource, plan: dict[str, Any], output: Path
) -> dict[str, Any]:
    _, indices = partition_indices(source)
    matrix = source.development[list(indices)]
    row_ids = [source.development_ids[i] for i in indices]
    targets = [source.development_targets[i] for i in indices]
    with threadpool_limits(limits=1):
        model_a, model_b = fit_forward_models(source)
        calibration = calibration_for(source, model_a)
        artifacts: dict[str, str] = {}
        for name, model in (("A", model_a), ("B", model_b)):
            buffer = io.BytesIO()
            joblib.dump(model, buffer)
            write_new_file(output / f"artifact_{name}.joblib", buffer.getvalue())
            artifacts[name] = file_sha256(output / f"artifact_{name}.joblib")
        if artifacts["A"] == artifacts["B"]:
            raise ModelArtifactBindingError("the alternate fitted artifact has identical bytes")
        manifest = {
            "schema_version": "model-artifact-binding-forward-manifest/v1",
            "plan_sha256": file_sha256(output / "plan.json"),
            "artifacts": artifacts,
            "recipes": plan["recipes"],
            "source": plan["source"],
            "partitions": plan["partitions"],
            "calibration": calibration.model_dump(mode="json"),
            "calibration_sha256": calibration.canonical_sha256(),
        }
        publish_json(output / "manifest.json", manifest)
        invariants = {
            "preprocessor_sha256": plan["source"]["preprocessor_sha256"],
            "features_sha256": plan["partitions"]["measurement"]["features_sha256"],
            "row_target_sha256": plan["partitions"]["measurement"]["row_target_sha256"],
            "calibration_sha256": calibration.canonical_sha256(),
            "adapter": ADAPTER_ID,
            "scorer": SCORER_ID,
        }
        events: list[dict[str, Any]] = []
        paths: dict[str, dict[str, Any]] = {}
        for case, declared, loaded, reported in CASES:
            model, event = load_event(
                case=case,
                path=output / f"artifact_{loaded}.joblib",
                expected_sha256=artifacts[loaded],
                declared_sha256=artifacts[declared],
                reported_sha256=artifacts[reported],
                feature_count=matrix.shape[1],
                invariants=invariants,
            )
            paths[case] = score_bundle(
                row_ids=row_ids,
                targets=targets,
                raw=probability_rows(model, matrix),
                calibration=calibration,
                clip=source.calibration_parameters[0],
            )
            events.append(event)
        # Same public staged API, not a claim that B was literally saved during A's fit.
        if not np.array_equal(next(model_a.staged_predict_proba(matrix)), paths["faulty"]["raw"]):
            raise ModelArtifactBindingError("early-budget artifact differs from A's first stage")
    if not (paths["healthy"] == paths["sham"] == paths["corrected"] == paths["manifest_text_only"]):
        raise ModelArtifactBindingError("sham, correction or manifest-only control changed scores")
    if paths["faulty"] != paths["legitimate_B"]:
        raise ModelArtifactBindingError("legitimate B control did not reproduce faulty scores")
    prior = sum(source.train_targets) / len(source.train_targets)
    prior_metrics = score_metrics(targets, [prior] * len(targets))
    scores = {
        "row_ids": row_ids,
        "targets": targets,
        "paths": paths,
        "training_prior_metrics": prior_metrics,
    }
    rivals = other_locus_controls(
        row_ids=row_ids,
        targets=targets,
        raw=paths["healthy"]["raw"],
        artifact_sha256=artifacts["A"],
    )
    publish_json(output / "load-trace.json", {"events": events})
    publish_json(output / "scores.json", scores)
    publish_json(output / "rivals.json", rivals)
    receipt = {
        "schema_version": "model-artifact-binding-forward-receipt/v1",
        "plan_sha256": file_sha256(output / "plan.json"),
        "retained_sha256": {
            name: file_sha256(output / name)
            for name in (
                "lease.json",
                "manifest.json",
                "load-trace.json",
                "scores.json",
                "rivals.json",
            )
        },
        "measurement_count": len(indices),
        "calibration_count": plan["partitions"]["calibration"]["count"],
        "train_count": len(source.train_ids),
        "primary_endpoint": SCORER_ID,
        "minimum_raw_loss_delta": MIN_RAW_LOSS_DELTA,
        "checks": {
            "sham_exact": True,
            "correction_exact": True,
            "manifest_text_only_exact": True,
            "legitimate_B_same_scores": True,
            "B_matches_A_first_stage": True,
            "calibration_measurement_disjoint": True,
        },
        **development_decision(
            score_a=paths["healthy"],
            score_b=paths["faulty"],
            prior_loss=prior_metrics["reference_prior_log_loss"],
        ),
    }
    publish_json(output / "receipt.json", receipt)
    return receipt


def execute_forward_cell(
    *, root: Path, archive: Path, output: Path, predecessor: Path, confirm_plan_sha256: str
) -> dict[str, Any]:
    source, plan = validate_plan(root=root, archive=archive, output=output, predecessor=predecessor)
    if confirm_plan_sha256 != file_sha256(output / "plan.json"):
        raise ModelArtifactBindingError("forward plan confirmation differs")
    if set(path.name for path in output.iterdir()) != {"plan.json"}:
        raise ModelArtifactBindingError(
            "prepared cell has already begun; use a separate development cell"
        )
    publish_json(
        output / "lease.json",
        {
            "schema_version": "model-artifact-binding-forward-lease/v1",
            "plan_sha256": confirm_plan_sha256,
        },
    )
    try:
        receipt = _execute_prepared(source=source, plan=plan, output=output)
        if predecessor_bindings(root, predecessor) != plan["predecessor_sha256"]:
            raise ModelArtifactBindingError("historical cell changed during execution")
        return receipt
    except (ValueError, OSError) as exc:
        publish_json(
            output / "failure.json",
            {
                "status": "development_failed_closed",
                "error_type": type(exc).__name__,
                "provider_calls": 0,
                "scientific_admission": False,
            },
        )
        raise
