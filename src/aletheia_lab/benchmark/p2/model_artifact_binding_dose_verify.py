"""Refit the development dose grid without loading supplied model artifacts.

The verifier does not invoke the fault injector, runner scorer or decision
helper. The shared reader projection is the consumer contract, not the oracle
for numerical source/model correctness or adversarial-process attestation.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier  # type: ignore[import-untyped]
from threadpoolctl import threadpool_limits  # type: ignore[import-untyped]

from aletheia_lab.benchmark.p2.canonical import canonical_sha256
from aletheia_lab.benchmark.p2.confirmatory_v3_runtime import (
    apply_logit_calibration,
    fit_logit_calibration,
)
from aletheia_lab.benchmark.p2.model_artifact_binding_development import ModelArtifactBindingError
from aletheia_lab.benchmark.p2.model_artifact_binding_dose import (
    DOSES,
    bound_dose_plan,
    dose_observations,
)
from aletheia_lab.benchmark.p2.model_artifact_binding_forward import (
    ADAPTER_ID,
    CASES,
    MIN_RAW_LOSS_DELTA,
    SCORER_ID,
    json_file,
    partition_indices,
)
from aletheia_lab.benchmark.p2.model_artifact_binding_forward_verify import (
    _metrics_match,
    independent_losses,
)
from aletheia_lab.content_hashing import file_sha256
from aletheia_lab.evaluation.artifact_binding_reader import audit_artifact_binding_observations


def _refit(source: Any, plan: dict[str, Any]) -> tuple[dict[str, Any], Any]:
    models = {}
    for name, recipe in plan["recipes"].items():
        model = HistGradientBoostingClassifier(**recipe)
        model.fit(source.train, np.asarray(source.train_targets, dtype=np.int64))
        if tuple(model.classes_) != (0, 1) or model.n_iter_ != recipe["max_iter"]:
            raise ModelArtifactBindingError("independent fitted dose recipe differs")
        models[name] = model
    indices, _ = partition_indices(source)
    clip, max_iter, tolerance = source.calibration_parameters
    calibration = fit_logit_calibration(
        models["A"].predict_proba(source.development[list(indices)])[:, 1].tolist(),
        [source.development_targets[i] for i in indices],
        probability_clip=clip,
        max_iter=max_iter,
        tolerance=tolerance,
    )
    if (
        calibration.model_dump(mode="json") != plan["calibration"]
        or calibration.canonical_sha256() != plan["calibration_sha256"]
    ):
        raise ModelArtifactBindingError("independent A-calibrator differs")
    return models, calibration


def _verify_paths(
    source: Any, plan: Any, output: Path, scores: Any, models: Any, calibration: Any
) -> None:
    _, indices = partition_indices(source)
    rows = [source.development_ids[i] for i in indices]
    targets = [source.development_targets[i] for i in indices]
    if (
        scores.keys() != {"row_ids", "targets", "doses"}
        or scores["row_ids"] != rows
        or scores["targets"] != targets
        or scores["doses"].keys() != {str(n) for n in DOSES}
    ):
        raise ModelArtifactBindingError("retained dose census or row-target binding differs")
    raw = {
        name: model.predict_proba(source.development[list(indices)]).tolist()
        for name, model in models.items()
    }
    stages = {
        step: values
        for step, values in enumerate(
            models["A"].staged_predict_proba(source.development[list(indices)]), 1
        )
        if step in DOSES
    }
    for dose in DOSES:
        cell = scores["doses"][str(dose)]
        if cell.keys() != {"artifact_B_sha256", "paths", "events", "B_matches_A_stage"} or cell[
            "paths"
        ].keys() != {case for case, *_ in CASES}:
            raise ModelArtifactBindingError("dose cell fields or controls differ")
        if (
            cell["artifact_B_sha256"] != file_sha256(output / f"artifact_B{dose}.joblib")
            or cell["artifact_B_sha256"] == plan["intended_A_sha256"]
        ):
            raise ModelArtifactBindingError("alternate artifact identity differs")
        _verify_events(plan, cell, dose, source.train.shape[1])
        if cell["B_matches_A_stage"] is not bool(np.array_equal(stages[dose], raw[f"B{dose}"])):
            raise ModelArtifactBindingError("independent staged-budget relationship differs")
        for case, _, loaded, _ in CASES:
            expected_raw = raw["A" if loaded == "A" else f"B{dose}"]
            positive = [p[1] for p in expected_raw]
            calibrated = list(
                apply_logit_calibration(
                    positive, calibration, clip=source.calibration_parameters[0]
                )
            )
            expected = {
                "raw": expected_raw,
                "calibrated_positive": calibrated,
                "raw_scores_sha256": canonical_sha256({"rows": rows, "scores": expected_raw}),
                "calibrated_scores_sha256": canonical_sha256({"rows": rows, "scores": calibrated}),
            }
            path = cell["paths"][case]
            if path.keys() != {*expected, "raw_metrics", "calibrated_metrics"} or any(
                path[key] != value for key, value in expected.items()
            ):
                raise ModelArtifactBindingError(
                    "retained scores differ from independent dose refit"
                )
            _metrics_match(path["raw_metrics"], independent_losses(targets, positive))
            _metrics_match(path["calibrated_metrics"], independent_losses(targets, calibrated))


def _verify_events(plan: Any, cell: Any, dose: int, feature_count: int) -> None:
    aliases = {"A": plan["intended_A_sha256"], "B": cell["artifact_B_sha256"]}
    measurement = plan["partitions"]["measurement"]
    expected = []
    for case, declared, loaded, reported in CASES:
        recipe = plan["recipes"]["A" if loaded == "A" else f"B{dose}"]
        expected.append(
            {
                "case": case,
                "declared_artifact_sha256": aliases[declared],
                "actual_loaded_sha256": aliases[loaded],
                "reported_manifest_sha256": aliases[reported],
                "actual_iterations": recipe["max_iter"],
                "actual_recipe_sha256": canonical_sha256(recipe),
                "classes": [0, 1],
                "feature_count": feature_count,
                "preprocessor_sha256": plan["source"]["preprocessor_sha256"],
                "features_sha256": measurement["features_sha256"],
                "row_target_sha256": measurement["row_target_sha256"],
                "calibration_sha256": plan["calibration_sha256"],
                "adapter": ADAPTER_ID,
                "scorer": SCORER_ID,
            }
        )
    if cell["events"] != expected:
        raise ModelArtifactBindingError("actual load trace or unchanged-locus evidence differs")


def _verify_receipt(receipt: Any, scores: Any) -> None:
    if (
        receipt.get("selected_dose") is not None
        or receipt.get("all_doses_retained") is not True
        or receipt.get("predecessor_unchanged") is not True
        or receipt.get("sdk_capture_count") != 32 * len(DOSES)
    ):
        raise ModelArtifactBindingError("receipt changed sweep or selection semantics")
    if (
        receipt["doses"].keys() != {str(n) for n in DOSES}
        or receipt.get("uniform_reference_loss") != math.log(2)
        or receipt.get("measurement_count") != len(scores["targets"])
    ):
        raise ModelArtifactBindingError("receipt census or uniform baseline differs")
    for key, cell in scores["doses"].items():
        a, b = cell["paths"]["healthy"], cell["paths"]["faulty"]
        loss_a = independent_losses(scores["targets"], [p[1] for p in a["raw"]])
        loss_b = independent_losses(scores["targets"], [p[1] for p in b["raw"]])
        changed = sum(x != y for x, y in zip(a["raw"], b["raw"], strict=True))
        delta = loss_b["reference_prior_log_loss"] - loss_a["reference_prior_log_loss"]
        effect, adequate = (
            changed > 0 and delta >= MIN_RAW_LOSS_DELTA,
            loss_a["reference_prior_log_loss"] < math.log(2),
        )
        empirical_delta = loss_b["empirical_log_loss"] - loss_a["empirical_log_loss"]
        expected = {
            "changed_raw_score_rows": changed,
            "effect_threshold_met": effect,
            "healthy_better_than_uniform": adequate,
            "raw_empirical_delta_positive": empirical_delta > 0,
            "prospective_joint_gate_met": effect and adequate and empirical_delta > 0,
            "sham_exact": a == cell["paths"]["sham"],
            "correction_exact": a == cell["paths"]["corrected"],
            "manifest_only_exact": a == cell["paths"]["manifest_text_only"],
            "legitimate_B_exact": b == cell["paths"]["legitimate_B"],
            "B_matches_A_stage": cell["B_matches_A_stage"],
        }
        observed = receipt["doses"][key]
        numeric = {
            "raw_reference_prior_loss_delta": delta,
            "raw_empirical_loss_delta": empirical_delta,
            "fixed_A_calibrated_reference_prior_loss_delta": independent_losses(
                scores["targets"], b["calibrated_positive"]
            )["reference_prior_log_loss"]
            - independent_losses(scores["targets"], a["calibrated_positive"])[
                "reference_prior_log_loss"
            ],
        }
        if observed.keys() != {*expected, *numeric} or any(
            observed[k] != v for k, v in expected.items()
        ):
            raise ModelArtifactBindingError("dose decision differs from independent recomputation")
        _metrics_match({k: observed[k] for k in numeric}, numeric)


def verify_dose_study(*, output: Path, **options: Any) -> dict[str, Any]:
    try:
        source, plan = bound_dose_plan(output=output, **options)
        retained = {
            "lease.json",
            "scores.json",
            "input-audit.json",
            "artifact_A.joblib",
            *(f"artifact_B{n}.joblib" for n in DOSES),
        }
        if {p.name for p in output.iterdir()} != {*retained, "plan.json", "receipt.json"}:
            raise ModelArtifactBindingError("retained dose file census differs")
        receipt = json_file(output / "receipt.json")
        if (
            receipt.get("schema_version") != "model-artifact-binding-dose-receipt/v1"
            or receipt.get("status") != "development_dose_study_completed"
            or receipt.get("plan_sha256") != file_sha256(output / "plan.json")
        ):
            raise ModelArtifactBindingError("dose receipt identity differs")
        if receipt["retained_sha256"] != {name: file_sha256(output / name) for name in retained}:
            raise ModelArtifactBindingError("retained dose bytes changed")
        if (
            any(
                receipt.get(key) is not False
                for key in (
                    "scientific_admission",
                    "U4_authorized",
                    "protected_predictions_or_metrics_computed",
                )
            )
            or receipt.get("provider_calls") != 0
        ):
            raise ModelArtifactBindingError("development-only boundary differs")
        if file_sha256(output / "artifact_A.joblib") != plan["intended_A_sha256"] or json_file(
            output / "lease.json"
        ) != {"plan_sha256": receipt["plan_sha256"], "registered_attempt": False}:
            raise ModelArtifactBindingError("intended A bytes or lease differs")
        scores = json_file(output / "scores.json")
        with threadpool_limits(limits=1):
            models, calibration = _refit(source, plan)
            _verify_paths(source, plan, output, scores, models, calibration)
        _verify_receipt(receipt, scores)
        expected_audit = {
            key: audit_artifact_binding_observations(
                options["root"],
                dose_observations(
                    plan=plan, targets=scores["targets"], paths=cell["paths"], events=cell["events"]
                ),
            )
            for key, cell in scores["doses"].items()
        }
        if json_file(output / "input-audit.json") != {"doses": expected_audit}:
            raise ModelArtifactBindingError("complete reader input audit differs")
        return {
            **receipt,
            "receipt_sha256": file_sha256(output / "receipt.json"),
            "verification": "independent_refit_and_metric_replay_no_pickle_load",
        }
    except (KeyError, TypeError, IndexError, OverflowError) as exc:
        raise ModelArtifactBindingError("malformed retained dose evidence") from exc
