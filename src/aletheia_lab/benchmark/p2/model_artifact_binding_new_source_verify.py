"""Independent new-source refit/metric replay; never load retained pickles.

Reader projections share the consumer contract, not the numerical oracle.
The trace establishes instrumented lineage, not hostile-process attestation.
"""

from __future__ import annotations

import json
import math
import warnings
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier  # type: ignore[import-untyped]
from sklearn.exceptions import ConvergenceWarning  # type: ignore[import-untyped]
from sklearn.preprocessing import StandardScaler  # type: ignore[import-untyped]

from aletheia_lab.benchmark.p2.canonical import canonical_sha256
from aletheia_lab.benchmark.p2.confirmatory_v3_runtime import (
    apply_logit_calibration,
    fit_logit_calibration,
)
from aletheia_lab.benchmark.p2.model_artifact_binding_forward import ADAPTER_ID, CASES, SCORER_ID
from aletheia_lab.benchmark.p2.model_artifact_binding_forward_verify import (
    _metrics_match,
    independent_losses,
)
from aletheia_lab.benchmark.p2.model_artifact_binding_new_source_cells import reader_audit
from aletheia_lab.benchmark.p2.model_artifact_binding_new_source_protocol import ArtifactSourceSpec
from aletheia_lab.benchmark.p2.target_binding_prospective_sources import (
    ParsedSource,
    ProspectiveBindingError,
    json_bytes,
)
from aletheia_lab.content_hashing import file_sha256


def read_object(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ProspectiveBindingError("retained artifact must be a regular nonsymlink file")
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict):
        raise ProspectiveBindingError("retained evidence must be a JSON object")
    return value


def require_equal(observed: Any, expected: Any) -> None:
    if json_bytes(observed) != json_bytes(expected):
        raise ProspectiveBindingError("retained evidence differs from independent replay")


def _refit(data: ParsedSource, protocol: Any) -> tuple[Any, Any, Any, Any]:
    scaler = StandardScaler()
    scaler.fit(data.features[list(data.partitions["train"])])
    matrices = {
        key: scaler.transform(data.features[list(rows)]) for key, rows in data.partitions.items()
    }
    state = {
        "mean": scaler.mean_.tolist(),
        "scale": scaler.scale_.tolist(),
        "var": scaler.var_.tolist(),
        "n_samples_seen": int(scaler.n_samples_seen_),
        "feature_count": int(scaler.n_features_in_),
        "fit_partition": "train",
    }
    params = protocol["model"]
    fixed = {
        key: value
        for key, value in params.items()
        if key not in ("kind", "A_iterations", "B_iterations", "B_role")
    }
    models = {}
    train_targets = [data.targets[i] for i in data.partitions["train"]]
    for name in ("A", "B"):
        model = HistGradientBoostingClassifier(**fixed, max_iter=params[f"{name}_iterations"])
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always", ConvergenceWarning)
            model.fit(matrices["train"], np.asarray(train_targets, dtype=np.int64))
        if (
            any(issubclass(w.category, ConvergenceWarning) for w in caught)
            or tuple(model.classes_) != (0, 1)
            or model.n_iter_ != params[f"{name}_iterations"]
        ):
            raise ProspectiveBindingError("independent fit differs from its frozen recipe")
        models[name] = model
    calibration_spec = protocol["calibration"]
    calibration = fit_logit_calibration(
        models["A"].predict_proba(matrices["calibration"])[:, 1].tolist(),
        [data.targets[i] for i in data.partitions["calibration"]],
        probability_clip=calibration_spec["probability_clip"],
        max_iter=calibration_spec["max_iter"],
        tolerance=calibration_spec["tolerance"],
    )
    return models, matrices, state, calibration


def _manifest(
    data: Any, directory: Path, models: Any, matrices: Any, scaler: Any, calibration: Any
) -> Any:
    retained = read_object(directory / "manifest.json")
    artifacts = retained["artifacts"]
    if artifacts.keys() != {"A", "B"} or artifacts["A"] == artifacts["B"]:
        raise ProspectiveBindingError("intended and alternate artifact identities differ")
    for name in ("A", "B"):
        path = directory / f"artifact_{name}.joblib"
        if path.is_symlink() or not path.is_file() or file_sha256(path) != artifacts[name]:
            raise ProspectiveBindingError("retained artifact bytes changed")
    indices = data.partitions["final"]
    rows = [data.record_ids[i] for i in indices]
    targets = [data.targets[i] for i in indices]
    expected = {
        "recipes": {name: model.get_params() for name, model in models.items()},
        "artifacts": artifacts,
        "preprocessor": scaler,
        "calibration": calibration.model_dump(mode="json"),
        "invariants": {
            "preprocessor_sha256": canonical_sha256(scaler),
            "features_sha256": canonical_sha256({"rows": rows, "x": matrices["final"].tolist()}),
            "row_target_sha256": canonical_sha256({"rows": list(zip(rows, targets, strict=True))}),
            "calibration_sha256": calibration.canonical_sha256(),
            "adapter": ADAPTER_ID,
            "scorer": SCORER_ID,
        },
    }
    require_equal(retained, expected)
    return expected


def _scores(
    data: Any,
    protocol: Any,
    directory: Path,
    models: Any,
    matrices: Any,
    calibration: Any,
    manifest: Any,
) -> Any:
    scores = read_object(directory / "scores.json")
    indices = data.partitions["final"]
    rows, labels = [data.record_ids[i] for i in indices], [data.targets[i] for i in indices]
    expected_raw = {
        name: model.predict_proba(matrices["final"]).tolist() for name, model in models.items()
    }
    require_equal(scores["row_ids"], rows)
    require_equal(scores["targets"], labels)
    require_equal(sorted(scores["paths"]), sorted(case for case, *_ in CASES))
    events = []
    for case, declared, loaded, reported in CASES:
        raw = expected_raw[loaded]
        positive = [p[1] for p in raw]
        calibrated = list(
            apply_logit_calibration(
                positive, calibration, clip=protocol["calibration"]["application_clip"]
            )
        )
        observed = scores["paths"][case]
        exact = {
            "raw": raw,
            "calibrated_positive": calibrated,
            "raw_scores_sha256": canonical_sha256({"rows": rows, "scores": raw}),
            "calibrated_scores_sha256": canonical_sha256({"rows": rows, "scores": calibrated}),
        }
        require_equal(
            {k: v for k, v in observed.items() if k not in ("raw_metrics", "calibrated_metrics")},
            exact,
        )
        _metrics_match(observed["raw_metrics"], independent_losses(labels, positive))
        _metrics_match(observed["calibrated_metrics"], independent_losses(labels, calibrated))
        events.append(
            {
                "case": case,
                "declared_artifact_sha256": manifest["artifacts"][declared],
                "actual_loaded_sha256": manifest["artifacts"][loaded],
                "reported_manifest_sha256": manifest["artifacts"][reported],
                "actual_iterations": models[loaded].n_iter_,
                "actual_recipe_sha256": canonical_sha256(models[loaded].get_params()),
                "classes": [0, 1],
                "feature_count": data.features.shape[1],
                **manifest["invariants"],
            }
        )
    require_equal(scores["events"], events)
    stage = bool(
        np.array_equal(next(models["A"].staged_predict_proba(matrices["final"])), expected_raw["B"])
    )
    require_equal(scores["B_matches_A_first_stage"], stage)
    train_labels = [data.targets[i] for i in data.partitions["train"]]
    prior = sum(train_labels) / len(train_labels)
    _metrics_match(
        scores["training_prior_metrics"], independent_losses(labels, [prior] * len(labels))
    )
    _metrics_match(scores["uniform_metrics"], independent_losses(labels, [0.5] * len(labels)))
    _rivals(scores["rivals"], rows, labels, expected_raw["A"], manifest["artifacts"]["A"])
    if scores.keys() != {
        "row_ids",
        "targets",
        "paths",
        "events",
        "rivals",
        "B_matches_A_first_stage",
        "training_prior_metrics",
        "uniform_metrics",
    }:
        raise ProspectiveBindingError("retained score census differs")
    return scores


def _rivals(observed: Any, rows: Any, labels: Any, raw: Any, digest: str) -> None:
    swapped = list(labels)
    left, right = labels.index(0), labels.index(1)
    swapped[left], swapped[right] = swapped[right], swapped[left]
    expected: dict[str, Any] = {"matched_model_visible_inputs_proven": False}
    for name, targets, column, adapter in (
        ("adapter_column_reversal", labels, 0, "binary-column-reversal"),
        ("two_row_target_swap", swapped, 1, ADAPTER_ID),
    ):
        actual = observed[name]
        positive = [p[column] for p in raw]
        expected[name] = {
            "declared_artifact_sha256": digest,
            "actual_loaded_sha256": digest,
            "pre_adapter_scores_sha256": canonical_sha256({"rows": rows, "scores": raw}),
            "adapter": adapter,
            "targets": targets,
            "scored_positive": positive,
            "metrics": actual["metrics"],
        }
        _metrics_match(actual["metrics"], independent_losses(targets, positive))
    require_equal(observed, expected)


def _summary(scores: Any, protocol: Any) -> dict[str, Any]:
    paths, labels = scores["paths"], scores["targets"]
    a, b = paths["healthy"], paths["faulty"]
    loss_a = independent_losses(labels, [p[1] for p in a["raw"]])
    loss_b = independent_losses(labels, [p[1] for p in b["raw"]])
    delta = loss_b["reference_prior_log_loss"] - loss_a["reference_prior_log_loss"]
    empirical = loss_b["empirical_log_loss"] - loss_a["empirical_log_loss"]
    changed = sum(x != y for x, y in zip(a["raw"], b["raw"], strict=True))
    controls = {
        "sham_exact": a == paths["sham"],
        "correction_exact": a == paths["corrected"],
        "manifest_only_exact": a == paths["manifest_text_only"],
        "legitimate_B_exact": b == paths["legitimate_B"],
    }
    adequate = loss_a["reference_prior_log_loss"] < math.log(2)
    effect = changed > 0 and delta >= protocol["analysis"]["minimum_raw_loss_delta"]
    return {
        **controls,
        "changed_raw_score_rows": changed,
        "raw_reference_prior_loss_delta": delta,
        "raw_empirical_loss_delta": empirical,
        "fixed_A_calibrated_reference_prior_loss_delta": independent_losses(
            labels, b["calibrated_positive"]
        )["reference_prior_log_loss"]
        - independent_losses(labels, a["calibrated_positive"])["reference_prior_log_loss"],
        "healthy_better_than_uniform": adequate,
        "effect_threshold_met": effect,
        "raw_empirical_delta_positive": empirical > 0,
        "g1_pass": bool(effect and adequate and empirical > 0 and all(controls.values())),
    }


def verify_cell(
    root: Path, data: ParsedSource, protocol: Any, directory: Path, result: Any, provenance: str
) -> None:
    base = {
        "cell_id": f"{data.spec.dataset_id}/hist_gradient_boosting",
        "dataset_id": data.spec.dataset_id,
        "source_family": ArtifactSourceSpec.model_validate(data.spec.model_dump()).source_family,
    }
    for key, value in base.items():
        require_equal(result[key], value)
    if result["status"] == "runtime_failure":
        if (
            result.keys() != {*base, "status", "error_type", "failure_stage", "g1_pass", "g2_pass"}
            or not isinstance(result["error_type"], str)
            or result["failure_stage"] not in ("cell_computation", "calibration")
        ):
            raise ProspectiveBindingError("unknown runtime failure disposition")
        require_equal(result["g1_pass"], False)
        require_equal(result["g2_pass"], False)
        return  # Failure record integrity is checked, not claimed as a numerical replay pass.
    if result["status"] != "verified":
        raise ProspectiveBindingError("unknown cell disposition")
    if {path.name for path in directory.iterdir()} != {
        "artifact_A.joblib",
        "artifact_B.joblib",
        "manifest.json",
        "scores.json",
        "input-audit.json",
        "result.json",
    }:
        raise ProspectiveBindingError("completed cell file census differs")
    models, matrices, scaler, calibration = _refit(data, protocol)
    manifest = _manifest(data, directory, models, matrices, scaler, calibration)
    scores = _scores(data, protocol, directory, models, matrices, calibration, manifest)
    audit = reader_audit(
        root,
        targets=scores["targets"],
        paths=scores["paths"],
        events=scores["events"],
        counts={name: len(values) for name, values in data.partitions.items()},
        provenance=provenance,
        rivals=scores["rivals"],
    )
    require_equal(read_object(directory / "input-audit.json"), audit)
    cross = audit["pairwise"]
    expected = {
        **base,
        "status": "verified",
        "final_count": len(data.partitions["final"]),
        "summary": _summary(scores, protocol),
        "cross_fault_missing_key_equal": {
            name: cross[name]["missing_key"]["equal"] for name in protocol["rivals"]
        },
        "g2_pass": any(
            cross[name]["missing_key"]["equal"] and not cross[name]["full"]["equal"]
            for name in protocol["rivals"]
        ),
        "B_matches_A_first_stage": scores["B_matches_A_first_stage"],
        "literal_checkpoint_provenance": False,
        "provider_calls": 0,
        "mechanism_admitted": False,
    }
    # Floating-point metrics may differ within the frozen tolerance, not decisions or input bytes.
    _metrics_match(
        {k: v for k, v in result["summary"].items() if type(v) is float},
        {k: v for k, v in expected["summary"].items() if type(v) is float},
    )
    expected["summary"].update({k: v for k, v in result["summary"].items() if type(v) is float})
    require_equal(result, expected)
