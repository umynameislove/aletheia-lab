"""Fixed A100/B1 loader controls and the unchanged artifact reader contract."""

from __future__ import annotations

import io
import math
import warnings
from pathlib import Path
from typing import Any

import joblib  # type: ignore[import-untyped]
import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier  # type: ignore[import-untyped]
from sklearn.exceptions import ConvergenceWarning  # type: ignore[import-untyped]
from sklearn.preprocessing import StandardScaler  # type: ignore[import-untyped]

from aletheia_lab.benchmark.p2.canonical import canonical_sha256
from aletheia_lab.benchmark.p2.confirmatory_v3_runtime import fit_logit_calibration
from aletheia_lab.benchmark.p2.model_artifact_binding_forward import (
    ADAPTER_ID,
    CASES,
    SCORER_ID,
    load_event,
    other_locus_controls,
    probability_rows,
    score_bundle,
    score_metrics,
)
from aletheia_lab.benchmark.p2.model_artifact_binding_new_source_protocol import ArtifactSourceSpec
from aletheia_lab.benchmark.p2.target_binding_prospective_sources import (
    ParsedSource,
    ProspectiveBindingError,
    publish_json,
)
from aletheia_lab.content_hashing import file_sha256
from aletheia_lab.evaluation.artifact_binding_reader import (
    ArtifactBindingObservation,
    audit_artifact_binding_observations,
)
from aletheia_lab.filesystem import write_new_file


def recipes(protocol: dict[str, Any]) -> dict[str, dict[str, Any]]:
    model = protocol["model"]
    common = {k: v for k, v in model.items() if k not in ("A_iterations", "B_iterations", "B_role")}
    common.pop("kind")
    return {name: {**common, "max_iter": model[f"{name}_iterations"]} for name in ("A", "B")}


def source_matrices(data: ParsedSource) -> tuple[dict[str, Any], dict[str, Any]]:
    scaler = StandardScaler().fit(data.features[list(data.partitions["train"])])
    matrices = {
        name: scaler.transform(data.features[list(indices)])
        for name, indices in data.partitions.items()
    }
    state = {
        "mean": scaler.mean_.tolist(),
        "scale": scaler.scale_.tolist(),
        "var": scaler.var_.tolist(),
        "n_samples_seen": int(scaler.n_samples_seen_),
        "feature_count": int(scaler.n_features_in_),
        "fit_partition": "train",
    }
    return matrices, state


def _fit_models(matrices: Any, targets: list[int], fixed: Any) -> dict[str, Any]:
    models = {}
    for name, recipe in fixed.items():
        model = HistGradientBoostingClassifier(**recipe)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always", ConvergenceWarning)
            model.fit(matrices["train"], np.asarray(targets, dtype=np.int64))
        if (
            any(issubclass(w.category, ConvergenceWarning) for w in caught)
            or tuple(model.classes_) != (0, 1)
            or model.n_iter_ != recipe["max_iter"]
            or model.get_params() != recipe
        ):
            raise ProspectiveBindingError("fitted artifact differs from its frozen recipe")
        models[name] = model
    return models


def reader_observations(
    *, targets: list[int], paths: Any, events: Any, counts: Any, provenance: str, rivals: Any
) -> dict[str, ArtifactBindingObservation]:
    probes = (targets.index(0), targets.index(1))
    aliases = {
        events[0]["actual_loaded_sha256"]: "artifact-0",
        events[1]["actual_loaded_sha256"]: "artifact-1",
    }

    def observed(
        event: Any, raw: Any, positive: Any, labels: Any, loss: float, consumed: Any
    ) -> Any:
        return ArtifactBindingObservation(
            record_count=len(targets),
            reference_log_loss=paths["healthy"]["raw_metrics"]["reference_prior_log_loss"],
            observed_log_loss=loss,
            intended_artifact=aliases[event["declared_artifact_sha256"]],
            loaded_artifact=aliases[event["actual_loaded_sha256"]],
            reported_artifact=aliases[event["reported_manifest_sha256"]],
            model_classes=(0, 1),
            consumed_classes=consumed,
            source_targets=(0, 1),
            scoring_targets=tuple(labels[i] for i in probes),
            raw_probe_scores=(
                (raw[probes[0]][0], raw[probes[0]][1]),
                (raw[probes[1]][0], raw[probes[1]][1]),
            ),
            scored_probe_positive=tuple(positive[i] for i in probes),
            feature_count=events[0]["feature_count"],
            training_count=counts["train"],
            calibration_count=counts["calibration"],
            private_source_sha256=provenance,
        )

    observations = {}
    for event in events:
        path = paths[event["case"]]
        observations[event["case"]] = observed(
            event,
            path["raw"],
            [p[1] for p in path["raw"]],
            targets,
            path["raw_metrics"]["reference_prior_log_loss"],
            (0, 1),
        )
    for name, consumed in (("adapter_column_reversal", (1, 0)), ("two_row_target_swap", (0, 1))):
        rival = rivals[name]
        observations[name] = observed(
            events[0],
            paths["healthy"]["raw"],
            rival["scored_positive"],
            rival["targets"],
            rival["metrics"]["reference_prior_log_loss"],
            consumed,
        )
    return observations


def reader_audit(root: Path, **options: Any) -> dict[str, Any]:
    audit = audit_artifact_binding_observations(root, reader_observations(**options))
    return {
        **audit,
        "status": "offline_artifact_input_boundary_audited",
        "scope": "one-finite-source-cell; unchanged-common-A2-SDK-interface; not-LLM-efficacy",
    }


def synthetic_reader_preflight(root: Path) -> dict[str, Any]:
    targets = [0, 1]
    raw = {"A": [[0.8, 0.2], [0.2, 0.8]], "B": [[0.55, 0.45], [0.45, 0.55]]}
    identities = {"A": "a" * 64, "B": "b" * 64}
    paths = {
        case: {
            "raw": raw[loaded],
            "raw_metrics": {"reference_prior_log_loss": -math.log(raw[loaded][0][0])},
        }
        for case, _, loaded, _ in CASES
    }
    events = [
        {
            "case": case,
            "declared_artifact_sha256": identities[declared],
            "actual_loaded_sha256": identities[loaded],
            "reported_manifest_sha256": identities[reported],
            "feature_count": 2,
        }
        for case, declared, loaded, reported in CASES
    ]
    return {
        **reader_audit(
            root,
            targets=targets,
            paths=paths,
            events=events,
            counts={"train": 4, "calibration": 4},
            provenance="c" * 64,
            rivals={
                "adapter_column_reversal": {
                    "targets": targets,
                    "scored_positive": [0.8, 0.2],
                    "metrics": {"reference_prior_log_loss": -math.log(0.2)},
                },
                "two_row_target_swap": {
                    "targets": [1, 0],
                    "scored_positive": [0.2, 0.8],
                    "metrics": {"reference_prior_log_loss": -math.log(0.2)},
                },
            },
        ),
        "synthetic_only": True,
    }


def cell_summary(paths: Any, analysis: Any) -> dict[str, Any]:
    a, b = paths["healthy"], paths["faulty"]
    delta = (
        b["raw_metrics"]["reference_prior_log_loss"] - a["raw_metrics"]["reference_prior_log_loss"]
    )
    empirical = b["raw_metrics"]["empirical_log_loss"] - a["raw_metrics"]["empirical_log_loss"]
    changed = sum(x != y for x, y in zip(a["raw"], b["raw"], strict=True))
    controls = {
        "sham_exact": a == paths["sham"],
        "correction_exact": a == paths["corrected"],
        "manifest_only_exact": a == paths["manifest_text_only"],
        "legitimate_B_exact": b == paths["legitimate_B"],
    }
    adequate = a["raw_metrics"]["reference_prior_log_loss"] < math.log(2)
    effect = changed > 0 and delta >= analysis["minimum_raw_loss_delta"]
    return {
        **controls,
        "changed_raw_score_rows": changed,
        "raw_reference_prior_loss_delta": delta,
        "raw_empirical_loss_delta": empirical,
        "fixed_A_calibrated_reference_prior_loss_delta": b["calibrated_metrics"][
            "reference_prior_log_loss"
        ]
        - a["calibrated_metrics"]["reference_prior_log_loss"],
        "healthy_better_than_uniform": adequate,
        "effect_threshold_met": effect,
        "raw_empirical_delta_positive": empirical > 0,
        "g1_pass": bool(effect and adequate and empirical > 0 and all(controls.values())),
    }


def run_cell(
    root: Path, data: ParsedSource, protocol: Any, directory: Path, provenance: str
) -> dict[str, Any]:
    matrices, scaler = source_matrices(data)
    train_targets = [data.targets[i] for i in data.partitions["train"]]
    models = _fit_models(matrices, train_targets, recipes(protocol))
    calibration_targets = [data.targets[i] for i in data.partitions["calibration"]]
    params = protocol["calibration"]
    calibration = fit_logit_calibration(
        [p[1] for p in probability_rows(models["A"], matrices["calibration"])],
        calibration_targets,
        probability_clip=params["probability_clip"],
        max_iter=params["max_iter"],
        tolerance=params["tolerance"],
    )
    identities = {}
    for name, model in models.items():
        buffer = io.BytesIO()
        joblib.dump(model, buffer)
        path = directory / f"artifact_{name}.joblib"
        write_new_file(path, buffer.getvalue())
        identities[name] = file_sha256(path)
    if identities["A"] == identities["B"]:
        raise ProspectiveBindingError("alternate artifact bytes equal intended A")
    indices = data.partitions["final"]
    rows, targets = [data.record_ids[i] for i in indices], [data.targets[i] for i in indices]
    invariants = {
        "preprocessor_sha256": canonical_sha256(scaler),
        "features_sha256": canonical_sha256({"rows": rows, "x": matrices["final"].tolist()}),
        "row_target_sha256": canonical_sha256({"rows": list(zip(rows, targets, strict=True))}),
        "calibration_sha256": calibration.canonical_sha256(),
        "adapter": ADAPTER_ID,
        "scorer": SCORER_ID,
    }
    manifest = {
        "recipes": recipes(protocol),
        "artifacts": identities,
        "preprocessor": scaler,
        "calibration": calibration.model_dump(mode="json"),
        "invariants": invariants,
    }
    publish_json(directory / "manifest.json", manifest)
    paths, events = {}, []
    for case, declared, loaded, reported in CASES:
        model, event = load_event(
            case=case,
            path=directory / f"artifact_{loaded}.joblib",
            expected_sha256=identities[loaded],
            declared_sha256=identities[declared],
            reported_sha256=identities[reported],
            feature_count=data.features.shape[1],
            invariants=invariants,
        )
        paths[case] = score_bundle(
            row_ids=rows,
            targets=targets,
            raw=probability_rows(model, matrices["final"]),
            calibration=calibration,
            clip=params["application_clip"],
        )
        events.append(event)
    rivals = other_locus_controls(
        row_ids=rows, targets=targets, raw=paths["healthy"]["raw"], artifact_sha256=identities["A"]
    )
    prior = sum(train_targets) / len(train_targets)
    stage_equal = bool(
        np.array_equal(
            next(models["A"].staged_predict_proba(matrices["final"])), paths["faulty"]["raw"]
        )
    )
    scores = {
        "row_ids": rows,
        "targets": targets,
        "paths": paths,
        "events": events,
        "rivals": rivals,
        "training_prior_metrics": score_metrics(targets, [prior] * len(targets)),
        "uniform_metrics": score_metrics(targets, [0.5] * len(targets)),
        "B_matches_A_first_stage": stage_equal,
    }
    publish_json(directory / "scores.json", scores)
    audit = reader_audit(
        root,
        targets=targets,
        paths=paths,
        events=events,
        counts={name: len(values) for name, values in data.partitions.items()},
        provenance=provenance,
        rivals=rivals,
    )
    publish_json(directory / "input-audit.json", audit)
    cross = audit["pairwise"]
    summary = cell_summary(paths, protocol["analysis"])
    if not all(
        summary[key]
        for key in ("sham_exact", "correction_exact", "manifest_only_exact", "legitimate_B_exact")
    ):
        raise ProspectiveBindingError("a loader control changed its frozen meaning")
    return {
        "cell_id": f"{data.spec.dataset_id}/hist_gradient_boosting",
        "dataset_id": data.spec.dataset_id,
        "source_family": ArtifactSourceSpec.model_validate(data.spec.model_dump()).source_family,
        "status": "verified",
        "final_count": len(rows),
        "summary": summary,
        "cross_fault_missing_key_equal": {
            name: cross[name]["missing_key"]["equal"] for name in protocol["rivals"]
        },
        "g2_pass": any(
            cross[name]["missing_key"]["equal"] and not cross[name]["full"]["equal"]
            for name in protocol["rivals"]
        ),
        "B_matches_A_first_stage": stage_equal,
        "literal_checkpoint_provenance": False,
        "provider_calls": 0,
        "mechanism_admitted": False,
    }
