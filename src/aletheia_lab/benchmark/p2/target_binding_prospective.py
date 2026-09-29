"""Single-use prospective target-binding controls on separately calibrated sources.

Only execution reads final model outcomes. Preparation is source/membership
metadata only. The frozen matcher constructs adversarial cases; failed matches
remain in the complete four-cell denominator, never trigger a new search policy.
"""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import Any

import joblib  # type: ignore[import-untyped]
from sklearn.preprocessing import StandardScaler  # type: ignore[import-untyped]
from threadpoolctl import threadpool_limits  # type: ignore[import-untyped]

from aletheia_lab.benchmark.p2.confirmatory_v3_runtime import (
    CalibrationAbstentionSignal,
    fit_logit_calibration,
)
from aletheia_lab.benchmark.p2.score_mapping_development import _fit_reference_model
from aletheia_lab.benchmark.p2.score_mapping_evidence import (
    mapping_observation,
    target_binding_rival_observation,
    zero_dose_observation,
)
from aletheia_lab.benchmark.p2.score_mapping_intervention import (
    apply_evaluator_mapping_fault,
    capture_evaluator_score_source,
)
from aletheia_lab.benchmark.p2.score_mapping_symptom_matching import (
    match_target_swaps,
    verify_target_swap_match,
)
from aletheia_lab.benchmark.p2.score_mapping_verification import (
    SourceArtifactPaths,
    capture_independent_score_witness,
    verify_evaluator_mapping,
)
from aletheia_lab.benchmark.p2.target_binding_intervention import (
    TargetBindingSource,
    apply_paired_target_binding_fault,
    apply_target_binding_fault,
    restore_target_bindings,
)
from aletheia_lab.benchmark.p2.target_binding_prospective_sources import (
    ParsedSource,
    ProspectiveBindingError,
    build_plan,
    json_bytes,
    publish_json,
)
from aletheia_lab.benchmark.p2.target_binding_verification import (
    verify_paired_target_binding,
    verify_target_binding,
)
from aletheia_lab.content_hashing import file_sha256
from aletheia_lab.evaluation.score_mapping_reader import (
    build_score_mapping_reader_context,
    validate_score_mapping_reader_context,
)
from aletheia_lab.filesystem import publish_immutable_file


def _fit_cell(data: ParsedSource, kind: Any, cell_dir: Path) -> dict[str, Any]:
    indices = data.partitions
    matrices = {name: data.features[list(rows)] for name, rows in indices.items()}
    train_targets = tuple(data.targets[index] for index in indices["train"])
    scaler = StandardScaler().fit(matrices["train"])
    transformed = {name: scaler.transform(matrix) for name, matrix in matrices.items()}
    for matrix in transformed.values():
        matrix[matrix == 0] = 0.0  # Normalize signed zeros in the canonical feature digest.
    publish_json(cell_dir / "split.json", data.audit())
    publish_json(
        cell_dir / "preprocessor.json",
        {"mean": scaler.mean_.tolist(), "scale": scaler.scale_.tolist(), "fit_partition": "train"},
    )
    model = _fit_reference_model(kind, transformed["train"], train_targets)
    calibration = fit_logit_calibration(
        model.predict_proba(transformed["calibration"])[:, 1].tolist(),
        tuple(data.targets[index] for index in indices["calibration"]),
        probability_clip=1e-12,
        max_iter=200,
        tolerance=1e-9,
    )
    publish_json(cell_dir / "calibration.json", calibration.model_dump(mode="json"))
    # This is a freshly fitted artifact, never deserialize an arbitrary supplied pickle.
    joblib.dump(model, cell_dir / "fitted_model.joblib")
    ids = tuple(data.record_ids[index] for index in indices["final"])
    target_rows = tuple((data.record_ids[index], data.targets[index]) for index in indices["final"])
    artifacts = SourceArtifactPaths(
        cell_dir.parent.parent / "sources" / data.spec.archive_filename,
        cell_dir / "split.json",
        cell_dir / "preprocessor.json",
        cell_dir / "fitted_model.joblib",
    )
    witness = capture_independent_score_witness(
        dataset_id=data.spec.dataset_id,
        record_ids=ids,
        target_rows=target_rows,
        evaluation_matrix=transformed["final"],
        model=model,
        calibration=calibration,
        artifacts=artifacts,
    )
    score_source = capture_evaluator_score_source(
        dataset_id=data.spec.dataset_id,
        record_ids=ids,
        evaluation_matrix=transformed["final"],
        model=model,
        calibration=calibration,
    )
    publish_json(cell_dir / "source-witness.json", asdict(witness))
    return {
        "witness": witness,
        "score_source": score_source,
        "target_source": TargetBindingSource(data.spec.dataset_id, target_rows),
        "reference_model": model,
        "evaluation_matrix": transformed["final"],
        "reference_features": transformed["train"],
        "reference_calibration": calibration,
        "artifacts": artifacts,
    }


def _target_controls(inputs: dict[str, Any]) -> dict[str, Any]:
    source = inputs["target_source"]
    verification_args = {key: value for key, value in inputs.items() if key != "reference_features"}
    measurements = []
    for dose in (0, 1):
        intervention = apply_target_binding_fault(source, selected_shard_count=dose)
        checked = verify_target_binding(
            **verification_args,
            intervention=intervention,
            corrected_target_rows=restore_target_bindings(source, intervention),
        )
        if checked.corrected_log_loss != checked.healthy_log_loss:
            raise ProspectiveBindingError("target correction did not restore the healthy metric")
        measurements.append(
            {
                "dose": dose,
                "verification": asdict(checked),
                "donor_record_ids": intervention.donor_record_ids,
                "selected_record_ids": intervention.selected_record_ids,
                "changed_record_ids": intervention.changed_record_ids,
                "effect_direction": (
                    "positive"
                    if checked.faulty_log_loss > checked.healthy_log_loss
                    else "negative"
                    if checked.faulty_log_loss < checked.healthy_log_loss
                    else "flat"
                ),
            }
        )
    return {"cyclic_controls": measurements}


def _mapping_contrast(inputs: dict[str, Any]) -> dict[str, Any]:
    witness, source = inputs["witness"], inputs["score_source"]
    mapping_args = {
        "witness": witness,
        "source": source,
        "scoring_target_rows": witness.target_rows,
        "reference_model": inputs["reference_model"],
        "evaluation_matrix": inputs["evaluation_matrix"],
        "reference_calibration": inputs["reference_calibration"],
        "artifacts": inputs["artifacts"],
    }
    intervention = apply_evaluator_mapping_fault(source, selected_shard_count=1)
    checked = verify_evaluator_mapping(**mapping_args, intervention=intervention)
    result: dict[str, Any] = {"mapping_verification": asdict(checked)}
    if checked.faulty_log_loss <= checked.healthy_log_loss:
        return {**result, "match_status": "nonpositive_mapping_effect"}
    if len(checked.affected_record_ids) < 2 or not checked.changed_score_record_ids:
        return {**result, "match_status": "insufficient_mapping_footprint"}
    positive = tuple(row[witness.model_classes.index(1)] for row in witness.calibrated_score_rows)
    match_args = {
        "record_ids": witness.record_ids,
        "targets": tuple(label for _, label in witness.target_rows),
        "probabilities": positive,
        "mapping_log_loss": checked.faulty_log_loss,
        "max_changed_targets": len(checked.affected_record_ids),
    }
    matched = match_target_swaps(**match_args)
    verify_target_swap_match(**match_args, result=matched)
    paired = apply_paired_target_binding_fault(
        inputs["target_source"], swapped_pairs=matched.swapped_pairs
    )
    verification_args = {key: value for key, value in inputs.items() if key != "reference_features"}
    target_checked = verify_paired_target_binding(
        **verification_args,
        intervention=paired,
        corrected_target_rows=restore_target_bindings(inputs["target_source"], paired),
    )
    if paired.observed_target_rows != matched.scoring_target_rows:
        raise ProspectiveBindingError("independent donor injector differs from matcher ledger")
    result.update(
        {
            "paired_target_verification": asdict(target_checked),
            "swapped_pairs": matched.swapped_pairs,
            "absolute_loss_gap": matched.absolute_loss_gap,
            "match_status": "unmatched",
        }
    )
    if not matched.resolution_matched:
        return result
    observation_args = {
        "witness": witness,
        "source": source,
        "reference_features": inputs["reference_features"],
        "evaluation_features": inputs["evaluation_matrix"],
    }
    observation = mapping_observation(
        **observation_args,
        intervention=intervention,
        scoring_target_rows=witness.target_rows,
        reference_model=inputs["reference_model"],
        reference_calibration=inputs["reference_calibration"],
        artifacts=inputs["artifacts"],
    )
    rival = target_binding_rival_observation(
        **observation_args, scoring_target_rows=paired.observed_target_rows
    )
    equality = {}
    payloads = {}
    for condition in ("full", "missing_key", "noisy", "misleading"):
        contexts = [
            build_score_mapping_reader_context(item, condition=condition)
            for item in (observation, rival)
        ]
        for context, item in zip(contexts, (observation, rival), strict=True):
            validate_score_mapping_reader_context(context, observation=item, condition=condition)
        payloads[condition] = [context.model_payload() for context in contexts]
        equality[condition] = payloads[condition][0] == payloads[condition][1]
    if not equality["missing_key"] or equality["full"]:
        raise ProspectiveBindingError("matched metrics do not meet the complete reader boundary")
    result.update(
        {
            "match_status": "resolution_matched",
            "reader_equality": equality,
            "reader_payloads": payloads,
        }
    )
    return result


def _run_cell(data: ParsedSource, kind: Any, cell_dir: Path) -> dict[str, Any]:
    cell_id = f"{data.spec.dataset_id}/{kind}"
    base = {"cell_id": cell_id, "dataset_id": data.spec.dataset_id, "model_kind": kind}
    if any({data.targets[index] for index in rows} != {0, 1} for rows in data.partitions.values()):
        return {**base, "status": "ineligible_class_partition"}
    inputs = _fit_cell(data, kind, cell_dir)
    common = {
        "witness": inputs["witness"],
        "source": inputs["score_source"],
        "reference_model": inputs["reference_model"],
        "reference_calibration": inputs["reference_calibration"],
        "artifacts": inputs["artifacts"],
        "reference_features": inputs["reference_features"],
        "evaluation_features": inputs["evaluation_matrix"],
    }
    sham = zero_dose_observation(**common)
    if (
        sham.reference_log_loss != sham.observed_log_loss
        or sham.evaluator_classes != sham.model_classes
    ):
        raise ProspectiveBindingError("healthy sham is not unchanged")
    return {
        **base,
        "status": "verified",
        "final_count": len(inputs["witness"].record_ids),
        "sham_verified": True,
        **_target_controls(inputs),
        **_mapping_contrast(inputs),
    }


def _bound_plan(root: Path, directory: Path) -> tuple[dict[str, Any], list[ParsedSource]]:
    plan, sources = build_plan(root, directory)
    path = directory / "plan.json"
    if path.is_symlink() or not path.is_file() or path.read_bytes() != json_bytes(plan):
        raise ProspectiveBindingError("prepared plan, code, runtime, sources or membership changed")
    return plan, sources


def _execute_cells(
    plan: dict[str, Any],
    sources: list[ParsedSource],
    directory: Path,
    results: list[dict[str, Any]],
) -> None:
    (directory / "cells").mkdir()
    position = 0
    with threadpool_limits(limits=1):
        for data in sources:
            for kind in plan["protocol"]["models"]:
                cell_dir = directory / "cells" / f"{data.spec.dataset_id}-{kind}"
                results[position]["status"] = "not_terminalized"
                cell_dir.mkdir()
                try:
                    result = _run_cell(data, kind, cell_dir)
                except Exception as exc:
                    # Retain the fixed cell; don't expose exception text or replace its source.
                    result = {
                        "cell_id": f"{data.spec.dataset_id}/{kind}",
                        "dataset_id": data.spec.dataset_id,
                        "model_kind": kind,
                        "status": "runtime_failure",
                        "error_type": type(exc).__name__,
                        "failure_stage": "cell_computation",
                    }
                    if isinstance(exc, CalibrationAbstentionSignal):
                        result["failure_stage"] = "calibration"
                        result["calibration_reason"] = exc.abstention.reason_code
                publish_json(cell_dir / "result.json", result)
                results[position] = result
                position += 1


def _scientific_summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    verified = [result for result in results if result["status"] == "verified"]
    observable = sum(
        result["cyclic_controls"][1]["verification"]["changed_target_count"] > 0
        and round(result["cyclic_controls"][1]["verification"]["faulty_log_loss"], 6)
        != round(result["cyclic_controls"][1]["verification"]["healthy_log_loss"], 6)
        for result in verified
    )
    return {
        "structurally_verified_cells": len(verified),
        "cyclic_observable_cells_at_declared_precision": observable,
        "cyclic_effect_direction_counts": dict(
            Counter(result["cyclic_controls"][1]["effect_direction"] for result in verified)
        ),
        "matched_cell_count": sum(
            result.get("match_status") == "resolution_matched" for result in results
        ),
        "matching_denominator": 4,
        "mechanism_disposition": "incomplete"
        if len(verified) != 4
        else "assumption_limited"
        if observable
        else "rejected_no_visible_cyclic_effect",
    }


def _receipt(
    plan: dict[str, Any],
    directory: Path,
    results: list[dict[str, Any]],
    failure: dict[str, str] | None,
) -> dict[str, Any]:
    files = sorted((directory / "cells").rglob("*"))
    if any(path.is_symlink() for path in files):
        raise ProspectiveBindingError("result inventory must not traverse symlinks")
    return {
        "schema_version": "target-binding-prospective-receipt/v1",
        "status": "prospective_census_terminalized"
        if failure is None
        else "prospective_execution_failed_closed",
        "failure": failure,
        "plan_sha256": file_sha256(directory / "plan.json"),
        "lease_sha256": file_sha256(directory / "lease.json"),
        "files_sha256": {
            path.relative_to(directory).as_posix(): file_sha256(path)
            for path in files
            if path.is_file()
        },
        "cell_status_counts": dict(Counter(result["status"] for result in results)),
        "match_status_counts": dict(
            Counter(result.get("match_status", "not_evaluated") for result in results)
        ),
        "cell_count": len(results),
        "census_dispositions": [
            {"cell_id": result["cell_id"], "status": result["status"]} for result in results
        ],
        "source_cluster_count": 2,
        "provider_calls": 0,
        "existing_results_mutated": False,
        "mechanism_admitted": False,
        "claim_scope": plan["protocol"]["claim_scope"],
        "scientific_summary": _scientific_summary(results),
    }


def execute_study(root: Path, directory: Path, *, confirm_plan_sha256: str) -> dict[str, Any]:
    plan, sources = _bound_plan(root, directory)
    plan_hash = file_sha256(directory / "plan.json")
    if confirm_plan_sha256 != plan_hash:
        raise ProspectiveBindingError("exact prepared plan confirmation is required")
    if (directory / "lease.json").exists() or (directory / "cells").exists():
        raise ProspectiveBindingError(
            "study already leased; no retries or overwrites are permitted"
        )
    lease = {"schema_version": "target-binding-prospective-lease/v1", "plan_sha256": plan_hash}
    if publish_immutable_file(directory / "lease.json", json_bytes(lease)) != "created":
        raise ProspectiveBindingError("another process already acquired this execution lease")
    results = [{"cell_id": cell_id, "status": "not_executed"} for cell_id in plan["cell_census"]]
    failure = None
    stage = "execute_census"
    try:
        _execute_cells(plan, sources, directory, results)
        stage = "post_execution_binding"
        _bound_plan(root, directory)
    except (Exception, KeyboardInterrupt) as exc:
        failure = {"stage": stage, "error_type": type(exc).__name__}
    receipt = _receipt(plan, directory, results, failure)
    if failure is not None:
        receipt["scientific_summary"]["mechanism_disposition"] = "incomplete"
    publish_json(directory / "receipt.json", receipt)
    return receipt


def verify_study(root: Path, directory: Path) -> dict[str, Any]:
    from aletheia_lab.benchmark.p2.target_binding_prospective_verify import verify_cell

    plan, sources = _bound_plan(root, directory)
    receipt_path = directory / "receipt.json"
    receipt = json.loads(receipt_path.read_bytes())
    expected_lease = {
        "schema_version": "target-binding-prospective-lease/v1",
        "plan_sha256": file_sha256(directory / "plan.json"),
    }
    if (directory / "lease.json").read_bytes() != json_bytes(expected_lease):
        raise ProspectiveBindingError("lease does not bind the prepared plan")
    if receipt_path.is_symlink() or (directory / "lease.json").is_symlink():
        raise ProspectiveBindingError("receipt and lease must be regular files")
    failure = receipt["failure"]
    if failure is not None and (
        set(failure) != {"stage", "error_type"}
        or failure["stage"] not in ("execute_census", "post_execution_binding")
    ):
        raise ProspectiveBindingError("unknown execution failure disposition")
    results = []
    dispositions = {item["cell_id"]: item["status"] for item in receipt["census_dispositions"]}
    for data in sources:
        for kind in plan["protocol"]["models"]:
            cell_dir = directory / "cells" / f"{data.spec.dataset_id}-{kind}"
            cell_id = f"{data.spec.dataset_id}/{kind}"
            result_path = cell_dir / "result.json"
            if (
                not result_path.exists()
                and failure is not None
                and dispositions.get(cell_id) in ("not_executed", "not_terminalized")
            ):
                results.append({"cell_id": cell_id, "status": dispositions[cell_id]})
                continue
            result = json.loads(result_path.read_bytes())
            if result["cell_id"] != f"{data.spec.dataset_id}/{kind}":
                raise ProspectiveBindingError("fixed cell identity changed")
            verify_cell(data, cell_dir, result)
            results.append(result)
    expected_receipt = _receipt(plan, directory, results, failure)
    if failure is not None:
        expected_receipt["scientific_summary"]["mechanism_disposition"] = "incomplete"
    if receipt_path.read_bytes() != json_bytes(expected_receipt):
        raise ProspectiveBindingError("receipt claims, artifact inventory or census changed")
    return {
        "status": "prospective_verification_pass"
        if failure is None
        else "prospective_failure_receipt_verified",
        "receipt_sha256": file_sha256(receipt_path),
        "provider_calls": 0,
        "mechanism_admitted": False,
        "cell_status_counts": receipt["cell_status_counts"],
        "match_status_counts": receipt["match_status_counts"],
        "cell_count": 4,
        "source_cluster_count": 2,
        "scientific_summary": receipt["scientific_summary"],
    }
