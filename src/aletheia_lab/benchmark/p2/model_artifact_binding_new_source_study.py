"""One-lease, fixed two-source M4 census and independent offline verification."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from threadpoolctl import threadpool_limits  # type: ignore[import-untyped]

from aletheia_lab.benchmark.p2.confirmatory_v3_runtime import CalibrationAbstentionSignal
from aletheia_lab.benchmark.p2.model_artifact_binding_new_source_cells import run_cell
from aletheia_lab.benchmark.p2.model_artifact_binding_new_source_plan import (
    LEASE_FILE,
    PLAN_FILE,
    RECEIPT_FILE,
    bound_plan,
)
from aletheia_lab.benchmark.p2.model_artifact_binding_new_source_verify import (
    read_object,
    require_equal,
    verify_cell,
)
from aletheia_lab.benchmark.p2.target_binding_prospective_sources import (
    ProspectiveBindingError,
    json_bytes,
    publish_json,
)
from aletheia_lab.content_hashing import file_sha256
from aletheia_lab.filesystem import publish_immutable_file


def scientific_summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    verified = [item for item in results if item["status"] == "verified"]
    g1 = sum(item["summary"]["g1_pass"] is True for item in verified)
    g2 = sum(item["g2_pass"] is True for item in verified)
    return {
        "structurally_verified_cells": len(verified),
        "g1_pass_count": g1,
        "g2_pass_count": g2,
        "g1_denominator": 2,
        "g2_denominator": 2,
        "source_cluster_count": 2,
        "completed_sdk_capture_count": 32 * len(verified),
        "disposition": "incomplete"
        if len(verified) != 2
        else "finite_controls_pass"
        if g1 == g2 == 2
        else "assumption_limited",
        "cell_measurements": [
            {
                "cell_id": item["cell_id"],
                "status": item["status"],
                "raw_reference_prior_loss_delta": item.get("summary", {}).get(
                    "raw_reference_prior_loss_delta"
                ),
                "g1_pass": item.get("summary", {}).get("g1_pass", False),
                "g2_pass": item.get("g2_pass", False),
            }
            for item in results
        ],
        "uncertainty": "finite-two-source-descriptive-no-population-CI",
        "legitimate_B_is_second_fault": False,
        "llm_efficacy_tested": False,
        "mechanism_admitted": False,
    }


def _inventory(directory: Path) -> dict[str, str]:
    cells = directory / "cells"
    if cells.is_symlink():
        raise ProspectiveBindingError("cell inventory must not traverse symlinks")
    paths = sorted(cells.rglob("*"))
    if any(p.is_symlink() for p in paths):
        raise ProspectiveBindingError("retained artifacts must not traverse symlinks")
    return {p.relative_to(directory).as_posix(): file_sha256(p) for p in paths if p.is_file()}


def _receipt(plan: Any, directory: Path, results: Any, failure: Any) -> dict[str, Any]:
    summary = scientific_summary(results)
    if failure is not None:
        summary["disposition"] = "incomplete"
    return {
        "schema_version": "model-artifact-binding-new-source-execution-receipt/v1",
        "status": "new_source_census_terminalized"
        if failure is None
        else "new_source_failed_closed",
        "failure": failure,
        "plan_sha256": file_sha256(directory / PLAN_FILE),
        "lease_sha256": file_sha256(directory / LEASE_FILE),
        "files_sha256": _inventory(directory),
        "cell_census": plan["cell_census"],
        "cell_count": 2,
        "source_cluster_count": 2,
        "census_dispositions": [
            {"cell_id": item["cell_id"], "status": item["status"]} for item in results
        ],
        "cell_status_counts": dict(Counter(item["status"] for item in results)),
        "scientific_summary": summary,
        "provider_calls": 0,
        "existing_results_mutated": False,
        "mechanism_admitted": False,
        "claim_scope": plan["protocol"]["claim_scope"],
    }


def _lease(plan_hash: str) -> dict[str, Any]:
    return {
        "schema_version": "model-artifact-binding-new-source-execution-lease/v1",
        "plan_sha256": plan_hash,
        "explicit_u4_owner_authorization": True,
        "provider_calls": 0,
    }


def _execute_cells(root: Path, plan: Any, sources: Any, directory: Path, results: Any) -> None:
    (directory / "cells").mkdir(mode=0o700)
    with threadpool_limits(limits=1):
        for position, data in enumerate(sources):
            cell_dir = directory / "cells" / data.spec.dataset_id
            cell_dir.mkdir(mode=0o700)
            results[position]["status"] = "not_terminalized"
            try:
                result = run_cell(root, data, plan["protocol"], cell_dir, plan["inventory_sha256"])
            except Exception as exc:
                result = {
                    "cell_id": plan["cell_census"][position],
                    "dataset_id": data.spec.dataset_id,
                    "source_family": data.spec.source_family,
                    "status": "runtime_failure",
                    "error_type": type(exc).__name__,
                    "failure_stage": "calibration"
                    if isinstance(exc, CalibrationAbstentionSignal)
                    else "cell_computation",
                    "g1_pass": False,
                    "g2_pass": False,
                }
            publish_json(cell_dir / "result.json", result)
            results[position] = result


def execute_study(
    root: Path,
    directory: Path,
    *,
    confirm_plan_sha256: str,
    authorize_final_execution: bool = False,
) -> dict[str, Any]:
    if authorize_final_execution is not True:
        raise ProspectiveBindingError(
            "final outcomes require separate explicit U4 owner authorization"
        )
    plan, sources = bound_plan(root, directory)
    digest = file_sha256(directory / PLAN_FILE)
    if confirm_plan_sha256 != digest:
        raise ProspectiveBindingError("exact prepared plan confirmation is required")
    if any(
        (directory / name).exists() or (directory / name).is_symlink()
        for name in (LEASE_FILE, RECEIPT_FILE, "cells")
    ):
        raise ProspectiveBindingError(
            "execution already began; retries and overwrites are prohibited"
        )
    if publish_immutable_file(directory / LEASE_FILE, json_bytes(_lease(digest))) != "created":
        raise ProspectiveBindingError("another process acquired this immutable execution lease")
    results = [{"cell_id": cell_id, "status": "not_executed"} for cell_id in plan["cell_census"]]
    failure, stage = None, "execute_census"
    try:
        _execute_cells(root, plan, sources, directory, results)
        stage = "post_execution_binding"
        bound_plan(root, directory)
    except (Exception, KeyboardInterrupt) as exc:
        failure = {"stage": stage, "error_type": type(exc).__name__}
    receipt = _receipt(plan, directory, results, failure)
    publish_json(directory / RECEIPT_FILE, receipt)
    return receipt


def _verify_results(
    root: Path, plan: Any, sources: Any, directory: Path, receipt: Any
) -> list[dict[str, Any]]:
    expected = plan["cell_census"]
    dispositions = receipt["census_dispositions"]
    if [item["cell_id"] for item in dispositions] != expected:
        raise ProspectiveBindingError("the fixed source census or order differs")
    results = []
    with threadpool_limits(limits=1):
        for position, data in enumerate(sources):
            cell_dir = directory / "cells" / data.spec.dataset_id
            path = cell_dir / "result.json"
            disposition = dispositions[position]
            if (
                not path.exists()
                and receipt["failure"] is not None
                and disposition["status"] in ("not_executed", "not_terminalized")
            ):
                results.append(disposition)
                continue
            result = read_object(path)
            verify_cell(root, data, plan["protocol"], cell_dir, result, plan["inventory_sha256"])
            results.append(result)
    return results


def verify_study(root: Path, directory: Path) -> dict[str, Any]:
    plan, sources = bound_plan(root, directory)
    receipt = read_object(directory / RECEIPT_FILE)
    require_equal(read_object(directory / LEASE_FILE), _lease(file_sha256(directory / PLAN_FILE)))
    require_equal(receipt["files_sha256"], _inventory(directory))
    failure = receipt["failure"]
    if failure is not None and (
        set(failure) != {"stage", "error_type"}
        or failure["stage"] not in ("execute_census", "post_execution_binding")
        or not isinstance(failure["error_type"], str)
    ):
        raise ProspectiveBindingError("unknown execution failure disposition")
    results = _verify_results(root, plan, sources, directory, receipt)
    require_equal(receipt, _receipt(plan, directory, results, failure))
    complete = failure is None and all(item["status"] == "verified" for item in results)
    return {
        "status": "new_source_verification_pass" if complete else "failure_receipt_verified",
        "receipt_sha256": file_sha256(directory / RECEIPT_FILE),
        "cell_count": 2,
        "source_cluster_count": 2,
        "provider_calls": 0,
        "scientific_summary": receipt["scientific_summary"],
        "mechanism_admitted": False,
        "verification_scope": "independent-refit-and-loss-replay-for-completed-cells; integrity-only-for-failures; no-pickle-load",
    }
