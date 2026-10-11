"""Independently refit and recompute the forward artifact-binding control.

No injector, runner scoring/decision helper or supplied pickle is executed.
The saved boundary trace supports instrumentation provenance, not attestation
against a malicious process that can rewrite every local record.
"""

from __future__ import annotations

import math
import warnings
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier  # type: ignore[import-untyped]
from sklearn.exceptions import ConvergenceWarning  # type: ignore[import-untyped]
from threadpoolctl import threadpool_limits  # type: ignore[import-untyped]

from aletheia_lab.benchmark.p2.canonical import canonical_sha256
from aletheia_lab.benchmark.p2.confirmatory_v3_runtime import (
    apply_logit_calibration,
    fit_logit_calibration,
)
from aletheia_lab.benchmark.p2.model_artifact_binding_development import ModelArtifactBindingError
from aletheia_lab.benchmark.p2.model_artifact_binding_forward import (
    ADAPTER_ID,
    CASES,
    MIN_RAW_LOSS_DELTA,
    SCORER_ID,
    json_file,
    partition_indices,
    validate_plan,
)
from aletheia_lab.content_hashing import file_sha256


def independent_losses(targets: list[int], positive: list[float]) -> dict[str, float]:
    """Direct class-conditional calculation; raw probabilities are never clipped."""

    if (
        len(targets) != len(positive)
        or set(targets) != {0, 1}
        or any(not math.isfinite(p) or not 0 < p < 1 for p in positive)
    ):
        raise ModelArtifactBindingError("invalid independent scoring inputs")
    losses = [-math.log(p if y else 1.0 - p) for y, p in zip(targets, positive, strict=True)]
    class_means = [
        math.fsum(loss for loss, y in zip(losses, targets, strict=True) if y == label)
        / targets.count(label)
        for label in (0, 1)
    ]
    return {
        "reference_prior_log_loss": math.fsum(class_means) / 2,
        "empirical_log_loss": math.fsum(losses) / len(losses),
    }


def _metrics_match(observed: Any, expected: dict[str, float]) -> None:
    if not isinstance(observed, dict) or observed.keys() != expected.keys():
        raise ModelArtifactBindingError("metric endpoint differs")
    if any(
        isinstance(observed[key], bool)
        or not math.isclose(observed[key], value, rel_tol=0, abs_tol=1e-12)
        for key, value in expected.items()
    ):
        raise ModelArtifactBindingError("metric differs from independent recomputation")


def _independent_refit(source: Any, plan: dict[str, Any]) -> tuple[dict[str, Any], Any]:
    models = {}
    for name in ("A", "B"):
        model = HistGradientBoostingClassifier(**plan["recipes"][name])
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always", ConvergenceWarning)
            model.fit(source.train, np.asarray(source.train_targets, dtype=np.int64))
        if any(issubclass(item.category, ConvergenceWarning) for item in caught):
            raise ModelArtifactBindingError("independent model fit failed")
        if tuple(model.classes_) != (0, 1) or model.n_iter_ != plan["recipes"][name]["max_iter"]:
            raise ModelArtifactBindingError("independent fitted attributes differ")
        models[name] = model
    calibration_indices, _ = partition_indices(source)
    clip, max_iter, tolerance = source.calibration_parameters
    calibration = fit_logit_calibration(
        models["A"].predict_proba(source.development[list(calibration_indices)])[:, 1].tolist(),
        [source.development_targets[i] for i in calibration_indices],
        probability_clip=clip,
        max_iter=max_iter,
        tolerance=tolerance,
    )
    return models, calibration


def _verify_artifact_bytes(output: Path, manifest: dict[str, Any]) -> None:
    if manifest["artifacts"].keys() != {"A", "B"}:
        raise ModelArtifactBindingError("artifact identities differ")
    if manifest["artifacts"]["A"] == manifest["artifacts"]["B"]:
        raise ModelArtifactBindingError("the fitted artifacts have identical identities")
    for name in ("A", "B"):
        path = output / f"artifact_{name}.joblib"
        if (
            path.is_symlink()
            or not path.is_file()
            or file_sha256(path) != manifest["artifacts"][name]
        ):
            raise ModelArtifactBindingError("retained fitted artifact changed")
        # Identity of retained bytes is distinct from functional replay of a
        # fresh model: serialization need not be byte-stable across refits.
        # Behavior is checked from boundary scores, without loading a pickle.


def _verify_scores(
    *, source: Any, scores: dict[str, Any], models: dict[str, Any], calibration: Any
) -> dict[str, Any]:
    _, indices = partition_indices(source)
    rows = [source.development_ids[i] for i in indices]
    targets = [source.development_targets[i] for i in indices]
    if (
        scores.keys() != {"row_ids", "targets", "paths", "training_prior_metrics"}
        or scores["row_ids"] != rows
        or scores["targets"] != targets
    ):
        raise ModelArtifactBindingError("measurement row-target binding differs")
    if scores["paths"].keys() != {case for case, *_ in CASES}:
        raise ModelArtifactBindingError("measurement paths differ")
    raw_by_identity = {
        name: np.asarray(
            model.predict_proba(source.development[list(indices)]), dtype=np.float64
        ).tolist()
        for name, model in models.items()
    }
    if not np.array_equal(
        next(models["A"].staged_predict_proba(source.development[list(indices)])),
        raw_by_identity["B"],
    ):
        raise ModelArtifactBindingError("independent early-stage witness differs")
    for case, _, loaded, _ in CASES:
        expected_raw = raw_by_identity[loaded]
        calibrated = list(
            apply_logit_calibration(
                [p[1] for p in expected_raw], calibration, clip=source.calibration_parameters[0]
            )
        )
        observed = scores["paths"][case]
        if observed.keys() != {
            "raw",
            "calibrated_positive",
            "raw_scores_sha256",
            "calibrated_scores_sha256",
            "raw_metrics",
            "calibrated_metrics",
        }:
            raise ModelArtifactBindingError("score fields differ")
        expected_fields = {
            "raw": expected_raw,
            "calibrated_positive": calibrated,
            "raw_scores_sha256": canonical_sha256({"rows": rows, "scores": expected_raw}),
            "calibrated_scores_sha256": canonical_sha256({"rows": rows, "scores": calibrated}),
        }
        if any(observed[key] != value for key, value in expected_fields.items()):
            raise ModelArtifactBindingError(
                "row-aligned saved scores differ from independent refit"
            )
        _metrics_match(
            observed["raw_metrics"], independent_losses(targets, [p[1] for p in expected_raw])
        )
        _metrics_match(observed["calibrated_metrics"], independent_losses(targets, calibrated))
    prior = sum(source.train_targets) / len(source.train_targets)
    _metrics_match(
        scores["training_prior_metrics"], independent_losses(targets, [prior] * len(targets))
    )
    return raw_by_identity


def _verify_trace(
    *, trace: dict[str, Any], manifest: dict[str, Any], plan: dict[str, Any], feature_count: int
) -> None:
    measurement = plan["partitions"]["measurement"]
    invariants = {
        "preprocessor_sha256": plan["source"]["preprocessor_sha256"],
        "features_sha256": measurement["features_sha256"],
        "row_target_sha256": measurement["row_target_sha256"],
        "calibration_sha256": manifest["calibration_sha256"],
        "adapter": ADAPTER_ID,
        "scorer": SCORER_ID,
    }
    expected = []
    for case, declared, loaded, reported in CASES:
        expected.append(
            {
                "case": case,
                "declared_artifact_sha256": manifest["artifacts"][declared],
                "actual_loaded_sha256": manifest["artifacts"][loaded],
                "reported_manifest_sha256": manifest["artifacts"][reported],
                "actual_iterations": plan["recipes"][loaded]["max_iter"],
                "actual_recipe_sha256": canonical_sha256(plan["recipes"][loaded]),
                "classes": [0, 1],
                "feature_count": feature_count,
                **invariants,
            }
        )
    if trace != {"events": expected}:
        raise ModelArtifactBindingError("actual load trace or stable execution locus differs")


def _verify_rivals(rivals: dict[str, Any], scores: dict[str, Any], artifact_sha256: str) -> None:
    if (
        rivals.keys()
        != {"adapter_column_reversal", "two_row_target_swap", "matched_model_visible_inputs_proven"}
        or rivals["matched_model_visible_inputs_proven"] is not False
    ):
        raise ModelArtifactBindingError("rival scope differs")
    rows, targets = scores["row_ids"], scores["targets"]
    raw = scores["paths"]["healthy"]["raw"]
    raw_hash = canonical_sha256({"rows": rows, "scores": raw})
    swapped = list(targets)
    zero, one = targets.index(0), targets.index(1)
    swapped[zero], swapped[one] = swapped[one], swapped[zero]
    for name, expected_targets, positive, adapter in (
        ("adapter_column_reversal", targets, [p[0] for p in raw], "binary-column-reversal"),
        ("two_row_target_swap", swapped, [p[1] for p in raw], ADAPTER_ID),
    ):
        expected = {
            "declared_artifact_sha256": artifact_sha256,
            "actual_loaded_sha256": artifact_sha256,
            "pre_adapter_scores_sha256": raw_hash,
            "adapter": adapter,
            "targets": expected_targets,
            "scored_positive": positive,
        }
        observed = rivals[name]
        if observed.keys() != {*expected.keys(), "metrics"} or any(
            observed[key] != value for key, value in expected.items()
        ):
            raise ModelArtifactBindingError("other-locus control differs")
        _metrics_match(observed["metrics"], independent_losses(expected_targets, positive))


def _verify_receipt(
    *,
    output: Path,
    receipt: dict[str, Any],
    plan: dict[str, Any],
    scores: dict[str, Any],
    source: Any,
) -> None:
    a, b = scores["paths"]["healthy"], scores["paths"]["faulty"]
    targets = scores["targets"]
    losses_a = independent_losses(targets, [p[1] for p in a["raw"]])
    losses_b = independent_losses(targets, [p[1] for p in b["raw"]])
    prior = sum(source.train_targets) / len(source.train_targets)
    prior_loss = independent_losses(targets, [prior] * len(targets))["reference_prior_log_loss"]
    changed = sum(before != after for before, after in zip(a["raw"], b["raw"], strict=True))
    delta = losses_b["reference_prior_log_loss"] - losses_a["reference_prior_log_loss"]
    adequate = losses_a["reference_prior_log_loss"] < prior_loss
    status = (
        "development_positive_control_observed"
        if changed > 0 and delta >= MIN_RAW_LOSS_DELTA and adequate
        else "development_effect_insufficient"
    )
    expected = {
        "schema_version": "model-artifact-binding-forward-receipt/v1",
        "status": status,
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
        "measurement_count": len(targets),
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
        "changed_raw_score_rows": changed,
        "changed_calibrated_score_rows": sum(
            before != after
            for before, after in zip(
                a["calibrated_positive"], b["calibrated_positive"], strict=True
            )
        ),
        "healthy_better_than_training_prior": adequate,
        "scientific_admission": False,
        "U4_authorized": False,
        "provider_calls": 0,
        "registered_attempt": False,
        "protected_predictions_or_metrics_computed": False,
    }
    secondary_delta = (
        independent_losses(targets, b["calibrated_positive"])["reference_prior_log_loss"]
        - independent_losses(targets, a["calibrated_positive"])["reference_prior_log_loss"]
    )
    expected_metrics = {
        "raw_reference_prior_loss_delta": delta,
        "calibrated_reference_prior_loss_delta": secondary_delta,
        "raw_empirical_loss_delta": losses_b["empirical_log_loss"] - losses_a["empirical_log_loss"],
    }
    if receipt.keys() != {*expected.keys(), *expected_metrics.keys()} or any(
        receipt[key] != value for key, value in expected.items()
    ):
        raise ModelArtifactBindingError("development decision or receipt bindings differ")
    _metrics_match({key: receipt[key] for key in expected_metrics}, expected_metrics)


def verify_forward_cell(
    *, root: Path, archive: Path, output: Path, predecessor: Path
) -> dict[str, Any]:
    """Replay source models/metrics without trusting the injector or deserializing its files."""

    try:
        source, plan = validate_plan(
            root=root, archive=archive, output=output, predecessor=predecessor
        )
        expected_files = {
            "plan.json",
            "lease.json",
            "manifest.json",
            "load-trace.json",
            "scores.json",
            "rivals.json",
            "receipt.json",
            "artifact_A.joblib",
            "artifact_B.joblib",
        }
        if {path.name for path in output.iterdir()} != expected_files:
            raise ModelArtifactBindingError(
                "retained forward cell is incomplete or has extra files"
            )
        lease, manifest, trace, scores, rivals, receipt = (
            json_file(output / name)
            for name in (
                "lease.json",
                "manifest.json",
                "load-trace.json",
                "scores.json",
                "rivals.json",
                "receipt.json",
            )
        )
        if lease != {
            "schema_version": "model-artifact-binding-forward-lease/v1",
            "plan_sha256": file_sha256(output / "plan.json"),
        }:
            raise ModelArtifactBindingError("lease binding differs")
        if manifest.keys() != {
            "schema_version",
            "plan_sha256",
            "artifacts",
            "recipes",
            "source",
            "partitions",
            "calibration",
            "calibration_sha256",
        }:
            raise ModelArtifactBindingError("manifest fields differ")
        expected_manifest = {
            "schema_version": "model-artifact-binding-forward-manifest/v1",
            "plan_sha256": file_sha256(output / "plan.json"),
            "recipes": plan["recipes"],
            "source": plan["source"],
            "partitions": plan["partitions"],
        }
        if any(manifest[key] != value for key, value in expected_manifest.items()):
            raise ModelArtifactBindingError("manifest source or recipe differs")
        _verify_artifact_bytes(output, manifest)
        with threadpool_limits(limits=1):
            models, calibration = _independent_refit(source, plan)
            if (
                manifest["calibration"] != calibration.model_dump(mode="json")
                or manifest["calibration_sha256"] != calibration.canonical_sha256()
            ):
                raise ModelArtifactBindingError("independent calibration differs")
            _verify_scores(source=source, scores=scores, models=models, calibration=calibration)
        _verify_trace(
            trace=trace, manifest=manifest, plan=plan, feature_count=source.development.shape[1]
        )
        _verify_rivals(rivals, scores, manifest["artifacts"]["A"])
        _verify_receipt(output=output, receipt=receipt, plan=plan, scores=scores, source=source)
        return {
            **receipt,
            "receipt_sha256": file_sha256(output / "receipt.json"),
            "verification": "independent_refit_and_metric_replay",
        }
    except (KeyError, TypeError, OverflowError) as exc:
        raise ModelArtifactBindingError("malformed retained development evidence") from exc
