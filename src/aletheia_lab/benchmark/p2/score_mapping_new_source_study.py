"""One-shot execution and independent replay of the frozen new-source M5 census.

Preparation never fits a model. Execution requires separate explicit U3
authorization, consumes one immutable lease, and retains all four cells.
There is no provider, outcome-dependent dose selection, or retry path.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from threadpoolctl import threadpool_limits  # type: ignore[import-untyped]

from aletheia_lab.benchmark.p2.confirmatory_v3_runtime import CalibrationAbstentionSignal
from aletheia_lab.benchmark.p2.score_mapping_new_source_cells import run_cell
from aletheia_lab.benchmark.p2.score_mapping_new_source_plan import (
    LEASE_FILE,
    PLAN_FILE,
    RECEIPT_FILE,
    bound_plan,
)
from aletheia_lab.benchmark.p2.score_mapping_new_source_verify import verify_cell
from aletheia_lab.benchmark.p2.target_binding_prospective_sources import (
    ParsedSource,
    ProspectiveBindingError,
    json_bytes,
    publish_json,
)
from aletheia_lab.content_hashing import file_sha256
from aletheia_lab.filesystem import publish_immutable_file


def scientific_summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    verified = [item for item in results if item["status"] == "verified"]
    g1, g2 = (sum(item.get(key) is True for item in results) for key in ("g1_pass", "g2_pass"))
    return {
        "structurally_verified_cells": len(verified),
        "g1_pass_count": g1,
        "g2_pass_count": g2,
        "g1_denominator": 4,
        "g2_denominator": 4,
        "disposition": "incomplete"
        if len(verified) != 4
        else "finite_controls_pass"
        if g1 == g2 == 4
        else "assumption_limited",
        "effect_direction_counts": dict(Counter(item["effect_direction"] for item in verified)),
        "source_cluster_count": 2,
        "estimator_family_count": 2,
        "cell_measurements": [
            {
                "cell_id": item["cell_id"],
                "status": item["status"],
                "absolute_effect": item.get("absolute_effect"),
                "g1_pass": item.get("g1_pass", False),
                "g2_pass": item.get("g2_pass", False),
                "match_status": item.get("match_status", "not_evaluated"),
            }
            for item in results
        ],
        "uncertainty": "finite-two-source-descriptive-no-row-level-ci",
        "llm_efficacy_tested": False,
        "global_zero_leakage_claim": False,
        "mechanism_admitted": False,
    }


def _file_inventory(directory: Path) -> dict[str, str]:
    paths = sorted((directory / "cells").rglob("*"))
    if any(path.is_symlink() for path in paths):
        raise ProspectiveBindingError("result inventory must not traverse symlinks")
    return {
        path.relative_to(directory).as_posix(): file_sha256(path)
        for path in paths
        if path.is_file()
    }


def _receipt(plan: Any, directory: Path, results: Any, failure: Any) -> dict[str, Any]:
    summary = scientific_summary(results)
    if failure is not None:
        summary["disposition"] = "incomplete"
    return {
        "schema_version": "score-mapping-new-source-execution-receipt/v1",
        "status": "new_source_census_terminalized"
        if failure is None
        else "new_source_failed_closed",
        "failure": failure,
        "plan_sha256": file_sha256(directory / PLAN_FILE),
        "lease_sha256": file_sha256(directory / LEASE_FILE),
        "files_sha256": _file_inventory(directory),
        "cell_census": plan["cell_census"],
        "cell_count": 4,
        "source_cluster_count": 2,
        "census_dispositions": [
            {"cell_id": item["cell_id"], "status": item["status"]} for item in results
        ],
        "cell_status_counts": dict(Counter(item["status"] for item in results)),
        "match_status_counts": dict(
            Counter(item.get("match_status", "not_evaluated") for item in results)
        ),
        "scientific_summary": summary,
        "provider_calls": 0,
        "existing_results_mutated": False,
        "mechanism_admitted": False,
        "claim_scope": "finite-frozen-new-source-control-not-LLM-efficacy",
    }


def _lease(plan_hash: str) -> dict[str, Any]:
    return {
        "schema_version": "score-mapping-new-source-execution-lease/v1",
        "plan_sha256": plan_hash,
        "explicit_u3_owner_authorization": True,
        "provider_calls": 0,
    }


def _execute_cells(
    root: Path, plan: Any, sources: list[ParsedSource], directory: Path, results: Any
) -> None:
    (directory / "cells").mkdir()
    position = 0
    with threadpool_limits(limits=1):
        for data in sources:
            for kind in plan["protocol"]["models"]:
                cell_dir = directory / "cells" / f"{data.spec.dataset_id}-{kind}"
                cell_dir.mkdir()
                results[position]["status"] = "not_terminalized"
                try:
                    result = run_cell(root, data, kind, cell_dir)
                except Exception as exc:
                    result = {
                        "cell_id": f"{data.spec.dataset_id}/{kind}",
                        "dataset_id": data.spec.dataset_id,
                        "model_kind": kind,
                        "status": "runtime_failure",
                        "error_type": type(exc).__name__,
                        "failure_stage": "cell_computation",
                        "g1_pass": False,
                        "g2_pass": False,
                    }
                    if isinstance(exc, CalibrationAbstentionSignal):
                        result["failure_stage"] = "calibration"
                        result["calibration_reason"] = exc.abstention.reason_code
                publish_json(cell_dir / "result.json", result)
                results[position] = result
                position += 1


def execute_study(
    root: Path,
    directory: Path,
    *,
    confirm_plan_sha256: str,
    authorize_final_execution: bool = False,
) -> dict[str, Any]:
    if authorize_final_execution is not True:
        raise ProspectiveBindingError(
            "final outcomes require separate explicit U3 owner authorization"
        )
    plan, sources = bound_plan(root, directory)
    plan_hash = file_sha256(directory / PLAN_FILE)
    if confirm_plan_sha256 != plan_hash:
        raise ProspectiveBindingError("exact prepared plan confirmation is required")
    if any((directory / name).exists() for name in (LEASE_FILE, RECEIPT_FILE, "cells")):
        raise ProspectiveBindingError(
            "study already leased; no retries or overwrites are permitted"
        )
    if publish_immutable_file(directory / LEASE_FILE, json_bytes(_lease(plan_hash))) != "created":
        raise ProspectiveBindingError("another process already acquired this execution lease")
    results = [{"cell_id": cell_id, "status": "not_executed"} for cell_id in plan["cell_census"]]
    failure = None
    stage = "execute_census"
    try:
        _execute_cells(root, plan, sources, directory, results)
        stage = "post_execution_binding"
        bound_plan(root, directory)
    except (Exception, KeyboardInterrupt) as exc:
        failure = {"stage": stage, "error_type": type(exc).__name__}
    receipt = _receipt(plan, directory, results, failure)
    publish_json(directory / RECEIPT_FILE, receipt)
    return receipt


def _read_regular(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ProspectiveBindingError("execution artifact must be a regular nonsymlink file")
    payload = json.loads(path.read_bytes())
    if not isinstance(payload, dict):
        raise ProspectiveBindingError("execution artifact must be a JSON object")
    return payload


def _verify_results(
    root: Path, plan: Any, sources: list[ParsedSource], directory: Path, receipt: Any
) -> list[dict[str, Any]]:
    dispositions = {item["cell_id"]: item["status"] for item in receipt["census_dispositions"]}
    results = []
    with threadpool_limits(limits=1):
        for data in sources:
            for kind in plan["protocol"]["models"]:
                cell_id = f"{data.spec.dataset_id}/{kind}"
                cell_dir = directory / "cells" / f"{data.spec.dataset_id}-{kind}"
                path = cell_dir / "result.json"
                if (
                    not path.exists()
                    and receipt["failure"] is not None
                    and dispositions.get(cell_id) in ("not_executed", "not_terminalized")
                ):
                    results.append({"cell_id": cell_id, "status": dispositions[cell_id]})
                    continue
                result = _read_regular(path)
                verify_cell(root, data, kind, cell_dir, result)
                results.append(result)
    return results


def verify_study(root: Path, directory: Path) -> dict[str, Any]:
    plan, sources = bound_plan(root, directory)
    receipt = _read_regular(directory / RECEIPT_FILE)
    if _read_regular(directory / LEASE_FILE) != _lease(file_sha256(directory / PLAN_FILE)):
        raise ProspectiveBindingError("execution lease differs from its exact authorized plan")
    if receipt["files_sha256"] != _file_inventory(directory):
        raise ProspectiveBindingError("retained cell artifact hashes differ")
    failure = receipt["failure"]
    if failure is not None and (
        set(failure) != {"stage", "error_type"}
        or failure["stage"] not in ("execute_census", "post_execution_binding")
    ):
        raise ProspectiveBindingError("unknown execution failure disposition")
    results = _verify_results(root, plan, sources, directory, receipt)
    if (directory / RECEIPT_FILE).read_bytes() != json_bytes(
        _receipt(plan, directory, results, failure)
    ):
        raise ProspectiveBindingError("receipt claims, fixed census or recomputed metrics differ")
    return {
        "status": "new_source_verification_pass" if failure is None else "failure_receipt_verified",
        "receipt_sha256": file_sha256(directory / RECEIPT_FILE),
        "cell_count": 4,
        "source_cluster_count": 2,
        "provider_calls": 0,
        "scientific_summary": receipt["scientific_summary"],
        "mechanism_admitted": False,
    }
