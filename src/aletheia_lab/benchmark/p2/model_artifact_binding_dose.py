"""A fixed, development-only wrong-artifact budget sweep.

The intended A bytes and calibration/measurement membership are retained from
the verified forward cell. B's fitting budget describes its provenance; the
evaluation intervention still changes only which artifact the loader consumes.
"""

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
from threadpoolctl import threadpool_limits  # type: ignore[import-untyped]

from aletheia_lab.benchmark.p2.confirmatory_v3_runtime import CalibrationResult
from aletheia_lab.benchmark.p2.model_artifact_binding_development import (
    DevelopmentSource,
    ModelArtifactBindingError,
    load_development_source,
)
from aletheia_lab.benchmark.p2.model_artifact_binding_forward import (
    ADAPTER_ID,
    CASES,
    CODE_PATHS,
    MIN_RAW_LOSS_DELTA,
    SCORER_ID,
    json_file,
    load_event,
    model_recipe,
    other_locus_controls,
    partition_bindings,
    partition_indices,
    private_directory,
    probability_rows,
    publish_json,
    runtime_bindings,
    score_bundle,
)
from aletheia_lab.benchmark.p2.model_artifact_binding_observation import _retained_chain
from aletheia_lab.benchmark.p2.target_binding_prospective_sources import validate_loaded_code_root
from aletheia_lab.content_hashing import file_sha256
from aletheia_lab.evaluation.artifact_binding_reader import (
    ArtifactBindingObservation,
    audit_artifact_binding_observations,
)
from aletheia_lab.filesystem import write_new_file

DOSES = (1, 5, 10, 25, 50)
EXTRA_CODE_PATHS = (
    "src/aletheia_lab/benchmark/p2/model_artifact_binding_dose.py",
    "src/aletheia_lab/benchmark/p2/model_artifact_binding_dose_verify.py",
    "src/aletheia_lab/benchmark/p2/model_artifact_binding_observation.py",
    "src/aletheia_lab/evaluation/artifact_binding_reader.py",
    "scripts/model_artifact_binding_dose.py",
)


def _upstream(root: Path, predecessor: Path, digest: str) -> dict[str, dict[str, Any]]:
    return _retained_chain(root, predecessor, digest)


def build_dose_plan(
    *, root: Path, archive: Path, predecessor: Path, expected_receipt_sha256: str
) -> tuple[DevelopmentSource, dict[str, Any]]:
    root = root.resolve(strict=True)
    validate_loaded_code_root(root)
    data = _upstream(root, predecessor, expected_receipt_sha256)
    source = load_development_source(root=root, archive=archive)
    legacy = data["plan.json"]
    if (
        source.bindings() != legacy["source"]
        or partition_bindings(source) != legacy["partitions"]
        or runtime_bindings() != legacy["runtime"]
    ):
        raise ModelArtifactBindingError("development source, membership or runtime changed")
    plan = {
        "schema_version": "model-artifact-binding-dose-plan/v1",
        "scope": "exposed-development-only-not-registered",
        "predecessor_receipt_sha256": expected_receipt_sha256,
        "predecessor_files_sha256": {p.name: file_sha256(p) for p in predecessor.iterdir()},
        "source": source.bindings(),
        "partitions": legacy["partitions"],
        "train_count": len(source.train_ids),
        "runtime": legacy["runtime"],
        "intended_A_sha256": data["manifest.json"]["artifacts"]["A"],
        "calibration": data["manifest.json"]["calibration"],
        "calibration_sha256": data["manifest.json"]["calibration_sha256"],
        "recipes": {"A": model_recipe(100), **{f"B{n}": model_recipe(n) for n in DOSES}},
        "doses": list(DOSES),
        "zero_dose": "reload-the-same-retained-A-bytes-not-a-refitted-B100",
        "primary_endpoint": SCORER_ID,
        "minimum_raw_loss_delta": MIN_RAW_LOSS_DELTA,
        "prospective_healthy_adequacy": "raw-reference-prior-loss-strictly-below-log(2)",
        "selection": "none-report-all-doses-new-source-B1-fixed-not-best-loss",
        "case_census": [case for case, *_ in CASES],
        "input_audit": "unchanged-common-A2-SDK-interface-four-views-six-decimals",
        "code_sha256": {
            name: file_sha256(root / name) for name in (*CODE_PATHS, *EXTRA_CODE_PATHS)
        },
        "provider_calls": 0,
        "protected_predictions_or_metrics_computed": False,
        "scientific_admission": False,
    }
    return source, plan


def prepare_dose_study(*, output: Path, **options: Any) -> dict[str, Any]:
    output = private_directory(options["root"], output, existing=False)
    _, plan = build_dose_plan(**options)
    output.mkdir()
    publish_json(output / "plan.json", plan)
    return {
        "status": "development_dose_plan_fixed",
        "plan_sha256": file_sha256(output / "plan.json"),
    }


def bound_dose_plan(*, output: Path, **options: Any) -> tuple[DevelopmentSource, dict[str, Any]]:
    private_directory(options["root"], output, existing=True)
    source, expected = build_dose_plan(**options)
    if json_file(output / "plan.json") != expected:
        raise ModelArtifactBindingError("dose plan source, recipe or code drift")
    return source, expected


def dose_summary(paths: dict[str, Any]) -> dict[str, Any]:
    a, b = paths["healthy"], paths["faulty"]
    raw_delta = (
        b["raw_metrics"]["reference_prior_log_loss"] - a["raw_metrics"]["reference_prior_log_loss"]
    )
    changed = sum(x != y for x, y in zip(a["raw"], b["raw"], strict=True))
    adequate = a["raw_metrics"]["reference_prior_log_loss"] < math.log(2)
    effect = changed > 0 and raw_delta >= MIN_RAW_LOSS_DELTA
    empirical_delta = (
        b["raw_metrics"]["empirical_log_loss"] - a["raw_metrics"]["empirical_log_loss"]
    )
    return {
        "changed_raw_score_rows": changed,
        "raw_reference_prior_loss_delta": raw_delta,
        "raw_empirical_loss_delta": empirical_delta,
        "fixed_A_calibrated_reference_prior_loss_delta": b["calibrated_metrics"][
            "reference_prior_log_loss"
        ]
        - a["calibrated_metrics"]["reference_prior_log_loss"],
        "effect_threshold_met": effect,
        "healthy_better_than_uniform": adequate,
        "raw_empirical_delta_positive": empirical_delta > 0,
        "prospective_joint_gate_met": effect and adequate and empirical_delta > 0,
        "sham_exact": a == paths["sham"],
        "correction_exact": a == paths["corrected"],
        "manifest_only_exact": a == paths["manifest_text_only"],
        "legitimate_B_exact": b == paths["legitimate_B"],
    }


def _paths(
    source: DevelopmentSource, plan: dict[str, Any], output: Path, dose: int, digest_b: str
) -> tuple[dict[str, Any], list[dict[str, Any]], bool]:
    _, indices = partition_indices(source)
    rows = [source.development_ids[i] for i in indices]
    targets = [source.development_targets[i] for i in indices]
    identities = {"A": plan["intended_A_sha256"], "B": digest_b}
    filenames = {"A": "artifact_A.joblib", "B": f"artifact_B{dose}.joblib"}
    calibration = CalibrationResult.model_validate(plan["calibration"])
    invariants = {
        "preprocessor_sha256": plan["source"]["preprocessor_sha256"],
        "features_sha256": plan["partitions"]["measurement"]["features_sha256"],
        "row_target_sha256": plan["partitions"]["measurement"]["row_target_sha256"],
        "calibration_sha256": plan["calibration_sha256"],
        "adapter": ADAPTER_ID,
        "scorer": SCORER_ID,
    }
    paths, events, models = {}, [], {}
    for case, declared, loaded, reported in CASES:
        model, event = load_event(
            case=case,
            path=output / filenames[loaded],
            expected_sha256=identities[loaded],
            declared_sha256=identities[declared],
            reported_sha256=identities[reported],
            feature_count=source.train.shape[1],
            invariants=invariants,
        )
        events.append(event)
        models[loaded] = model
        paths[case] = score_bundle(
            row_ids=rows,
            targets=targets,
            raw=probability_rows(model, source.development[list(indices)]),
            calibration=calibration,
            clip=source.calibration_parameters[0],
        )
    stage = next(
        values
        for step, values in enumerate(
            models["A"].staged_predict_proba(source.development[list(indices)]), 1
        )
        if step == dose
    )
    return paths, events, bool(np.array_equal(stage, paths["faulty"]["raw"]))


def dose_observations(
    *, plan: dict[str, Any], targets: list[int], paths: dict[str, Any], events: list[dict[str, Any]]
) -> dict[str, ArtifactBindingObservation]:
    probes = (targets.index(0), targets.index(1))
    aliases = {
        events[0]["actual_loaded_sha256"]: "artifact-0",
        events[1]["actual_loaded_sha256"]: "artifact-1",
    }
    common = {
        "record_count": len(targets),
        "reference_log_loss": paths["healthy"]["raw_metrics"]["reference_prior_log_loss"],
        "source_targets": (0, 1),
        "feature_count": events[0]["feature_count"],
        "training_count": plan["train_count"],
        "calibration_count": plan["partitions"]["calibration"]["count"],
        "private_source_sha256": plan["predecessor_receipt_sha256"],
    }

    def observation(
        event: Any, raw: Any, positive: Any, labels: Any, loss: float, consumed: Any
    ) -> ArtifactBindingObservation:
        return ArtifactBindingObservation(
            **common,
            observed_log_loss=loss,
            intended_artifact=aliases[event["declared_artifact_sha256"]],
            loaded_artifact=aliases[event["actual_loaded_sha256"]],
            reported_artifact=aliases[event["reported_manifest_sha256"]],
            model_classes=(0, 1),
            consumed_classes=consumed,
            raw_probe_scores=(
                (raw[probes[0]][0], raw[probes[0]][1]),
                (raw[probes[1]][0], raw[probes[1]][1]),
            ),
            scored_probe_positive=tuple(positive[i] for i in probes),
            scoring_targets=tuple(labels[i] for i in probes),
        )

    observations = {
        event["case"]: observation(
            event,
            paths[event["case"]]["raw"],
            [p[1] for p in paths[event["case"]]["raw"]],
            targets,
            paths[event["case"]]["raw_metrics"]["reference_prior_log_loss"],
            (0, 1),
        )
        for event in events
    }
    rivals = other_locus_controls(
        row_ids=[str(i) for i in range(len(targets))],
        targets=targets,
        raw=paths["healthy"]["raw"],
        artifact_sha256=plan["intended_A_sha256"],
    )
    for name, consumed in (("adapter_column_reversal", (1, 0)), ("two_row_target_swap", (0, 1))):
        rival = rivals[name]
        observations[name] = observation(
            events[0],
            paths["healthy"]["raw"],
            rival["scored_positive"],
            rival["targets"],
            rival["metrics"]["reference_prior_log_loss"],
            consumed,
        )
    return observations


def _fit_alternate(source: DevelopmentSource, iterations: int) -> Any:
    model = HistGradientBoostingClassifier(**model_recipe(iterations))
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", ConvergenceWarning)
        model.fit(source.train, np.asarray(source.train_targets, dtype=np.int64))
    if (
        any(issubclass(w.category, ConvergenceWarning) for w in caught)
        or model.n_iter_ != iterations
    ):
        raise ModelArtifactBindingError("alternate artifact fit failed its fixed recipe")
    return model


def execute_dose_study(*, output: Path, confirm_plan_sha256: str, **options: Any) -> dict[str, Any]:
    source, plan = bound_dose_plan(output=output, **options)
    if confirm_plan_sha256 != file_sha256(output / "plan.json") or {
        p.name for p in output.iterdir()
    } != {"plan.json"}:
        raise ModelArtifactBindingError("dose confirmation differs or execution already began")
    publish_json(
        output / "lease.json", {"plan_sha256": confirm_plan_sha256, "registered_attempt": False}
    )
    try:
        write_new_file(
            output / "artifact_A.joblib",
            (options["predecessor"] / "artifact_A.joblib").read_bytes(),
        )
        if file_sha256(output / "artifact_A.joblib") != plan["intended_A_sha256"]:
            raise ModelArtifactBindingError("retained A bytes changed before load")
        _, indices = partition_indices(source)
        targets = [source.development_targets[i] for i in indices]
        results, audits, summaries = {}, {}, {}
        with threadpool_limits(limits=1):
            for dose in DOSES:
                model = _fit_alternate(source, dose)
                buffer = io.BytesIO()
                joblib.dump(model, buffer)
                path = output / f"artifact_B{dose}.joblib"
                write_new_file(path, buffer.getvalue())
                digest = file_sha256(path)
                if digest == plan["intended_A_sha256"]:
                    raise ModelArtifactBindingError("alternate bytes equal the intended artifact")
                paths, events, stage_equal = _paths(source, plan, output, dose, digest)
                summary = dose_summary(paths)
                if not all(
                    summary[name]
                    for name in (
                        "sham_exact",
                        "correction_exact",
                        "manifest_only_exact",
                        "legitimate_B_exact",
                    )
                ):
                    raise ModelArtifactBindingError("a loader control changed its meaning")
                results[str(dose)] = {
                    "artifact_B_sha256": digest,
                    "paths": paths,
                    "events": events,
                    "B_matches_A_stage": stage_equal,
                }
                audits[str(dose)] = audit_artifact_binding_observations(
                    options["root"],
                    dose_observations(plan=plan, targets=targets, paths=paths, events=events),
                )
                summaries[str(dose)] = {**summary, "B_matches_A_stage": stage_equal}
        scores = {
            "row_ids": [source.development_ids[i] for i in indices],
            "targets": targets,
            "doses": results,
        }
        publish_json(output / "scores.json", scores)
        publish_json(output / "input-audit.json", {"doses": audits})
        _, after = build_dose_plan(**options)
        if after != plan:
            raise ModelArtifactBindingError("historical cell or source changed during development")
        receipt = {
            "schema_version": "model-artifact-binding-dose-receipt/v1",
            "status": "development_dose_study_completed",
            "plan_sha256": confirm_plan_sha256,
            "retained_sha256": {
                name: file_sha256(output / name)
                for name in (
                    "lease.json",
                    "artifact_A.joblib",
                    "scores.json",
                    "input-audit.json",
                    *(f"artifact_B{n}.joblib" for n in DOSES),
                )
            },
            "measurement_count": len(indices),
            "uniform_reference_loss": math.log(2),
            "doses": summaries,
            "sdk_capture_count": 32 * len(DOSES),
            "all_doses_retained": True,
            "selected_dose": None,
            "predecessor_unchanged": True,
            "provider_calls": 0,
            "protected_predictions_or_metrics_computed": False,
            "scientific_admission": False,
            "U4_authorized": False,
        }
        publish_json(output / "receipt.json", receipt)
        return receipt
    except (ValueError, OSError) as exc:
        publish_json(
            output / "failure.json",
            {
                "status": "development_failed_closed",
                "error_type": type(exc).__name__,
                "provider_calls": 0,
            },
        )
        raise
