#!/usr/bin/env python3
"""Verify the main diagnosis freeze candidate without authorizing execution."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Any

from aletheia_lab.benchmark.p2.canonical import canonical_sha256
from aletheia_lab.evaluation.qwen_calibration_failure import (
    load_and_validate_failure_closeout,
)

DEFAULT_MANIFEST = Path("configs/evaluation/diagnosis_main_freeze_candidate_v6.json")

_SCHEMA_V1 = "diagnosis-main-freeze-candidate/v1"
_SCHEMA_V2 = "diagnosis-main-freeze-candidate/v2"
_SCHEMA_V3 = "diagnosis-main-freeze-candidate/v3"
_SCHEMA_V4 = "diagnosis-main-freeze-candidate/v4"
_SCHEMA_V5 = "diagnosis-main-freeze-candidate/v5"
_SCHEMA_V6 = "diagnosis-main-freeze-candidate/v6"
_BLOCKED_STATUS = {
    _SCHEMA_V1: "candidate_integrity_locked_main_execution_blocked",
    _SCHEMA_V2: "candidate_forward_integrity_locked_main_execution_blocked",
    _SCHEMA_V3: "candidate_forward_integrity_locked_main_execution_blocked",
    _SCHEMA_V4: "final_forward_integrity_locked_main_execution_blocked",
    _SCHEMA_V5: "final_forward_integrity_locked_main_execution_blocked",
    _SCHEMA_V6: "final_forward_integrity_locked_main_execution_blocked",
}
_READY_STATUS = "final_forward_integrity_locked_ready_for_execution_authorization"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_object(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _canonical_manifest_hash(payload: dict[str, Any]) -> str:
    return canonical_sha256(
        {key: value for key, value in payload.items() if key != "manifest_sha256"}
    )


def _safe_repo_target(root: Path, relative: object) -> Path | None:
    path_value = PurePosixPath(str(relative))
    if path_value.is_absolute() or ".." in path_value.parts:
        return None
    target = root.joinpath(*path_value.parts)
    if not target.is_file() or target.is_symlink():
        return None
    return target


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def audit_candidate(root: Path, manifest_path: Path) -> dict[str, object]:
    root = root.resolve()
    manifest_path = manifest_path if manifest_path.is_absolute() else root / manifest_path
    manifest = _load_object(manifest_path)
    findings: list[dict[str, str]] = []

    declared_hash = manifest.get("manifest_sha256")
    computed_hash = _canonical_manifest_hash(manifest)
    findings.append(
        {
            "code": "manifest_identity",
            "status": "pass" if declared_hash == computed_hash else "fail",
            "evidence": computed_hash,
        }
    )

    bindings = manifest.get("repo_artifact_bindings")
    if not isinstance(bindings, dict) or not bindings:
        findings.append({"code": "repo_artifact_bindings", "status": "fail", "evidence": "missing"})
    else:
        for relative, expected in sorted(bindings.items()):
            target = _safe_repo_target(root, relative)
            actual = _sha256(target) if target is not None else "missing"
            findings.append(
                {
                    "code": f"artifact.{relative}",
                    "status": "pass" if actual == expected else "fail",
                    "evidence": actual,
                }
            )

    schema_version = manifest.get("schema_version")
    if schema_version == _SCHEMA_V1:
        fairness = _load_object(root / "configs/evaluation/diagnosis_variant_fairness_freeze.json")
        prompt_hashes = {
            key: value["prompt_content_sha256"]
            for key, value in fairness["prompt_policies"].items()
        }
        parity_checks = {
            "model_policy_parity": fairness["model_policies"]["main_llm_v1"]
            == manifest.get("frozen_model_policy"),
            "information_budget_parity": fairness["information_budgets"]["main_matched_v1"]
            == manifest.get("frozen_information_budget"),
            "prompt_hash_parity": prompt_hashes == manifest.get("frozen_prompt_sha256"),
        }
        for code, passed in parity_checks.items():
            findings.append(
                {
                    "code": code,
                    "status": "pass" if passed else "fail",
                    "evidence": str(passed).lower(),
                }
            )

        mechanism_filter = _load_object(
            root / "configs/benchmark/provenance/p4_p5_mechanism_filter_manifest.json"
        )
        denominator_policy = manifest.get("denominator_policy")
        denominator_passed = isinstance(denominator_policy, dict) and all(
            denominator_policy.get(key) == mechanism_filter.get(source_key)
            for key, source_key in (
                ("mechanism_inventory", "mechanism_inventory"),
                ("primary_causal_diagnosis_track", "primary_causal_diagnosis_track"),
                (
                    "assumption_limited_abstention_track",
                    "assumption_limited_abstention_track",
                ),
                ("instrument_rejection_track", "instrument_rejection_track"),
                ("evidence_accountability_track", "evidence_accountability_track"),
            )
        )
        findings.append(
            {
                "code": "mechanism_denominator_parity",
                "status": "pass" if denominator_passed else "fail",
                "evidence": str(denominator_passed).lower(),
            }
        )

        external_bindings = manifest.get("external_evidence_bindings")
        external_safe = (
            isinstance(external_bindings, dict)
            and bool(external_bindings)
            and all(_is_sha256(value) for value in external_bindings.values())
        )
        findings.append(
            {
                "code": "external_evidence_content_identities",
                "status": "pass" if external_safe else "fail",
                "evidence": "content hashes only; private source paths are not embedded",
            }
        )
    elif schema_version in {
        _SCHEMA_V2,
        _SCHEMA_V3,
        _SCHEMA_V4,
        _SCHEMA_V5,
        _SCHEMA_V6,
    }:
        predecessor = manifest.get("predecessor")
        predecessor_passed = False
        predecessor_evidence = "missing"
        old_blocker_codes: set[str] = set()
        if isinstance(predecessor, dict):
            predecessor_target = _safe_repo_target(root, predecessor.get("path"))
            if predecessor_target is not None:
                predecessor_payload = _load_object(predecessor_target)
                predecessor_file_sha = _sha256(predecessor_target)
                predecessor_manifest_sha = predecessor_payload.get("manifest_sha256")
                predecessor_blockers = predecessor_payload.get("unresolved_contract_requirements")
                if isinstance(predecessor_blockers, list):
                    old_blocker_codes = {
                        str(item.get("code"))
                        for item in predecessor_blockers
                        if isinstance(item, dict)
                    }
                predecessor_identity_passed = predecessor_manifest_sha == _canonical_manifest_hash(
                    predecessor_payload
                )
                expected_predecessor_schema = {
                    _SCHEMA_V2: _SCHEMA_V1,
                    _SCHEMA_V3: _SCHEMA_V2,
                    _SCHEMA_V4: _SCHEMA_V3,
                    _SCHEMA_V5: _SCHEMA_V4,
                    _SCHEMA_V6: _SCHEMA_V5,
                }[str(schema_version)]
                if schema_version == _SCHEMA_V2:
                    predecessor_report = audit_candidate(root, predecessor_target)
                    predecessor_scope_passed = (
                        predecessor_report["integrity_status"] == "pass"
                        and predecessor_payload.get("schema_version") == expected_predecessor_schema
                    )
                else:
                    # Forward candidates bind the predecessor manifest bytes and
                    # canonical identity. They bind the current artifact bytes
                    # directly rather than requiring superseded mutable targets
                    # from an earlier local candidate to remain at old hashes.
                    predecessor_scope_passed = (
                        predecessor_payload.get("schema_version") == expected_predecessor_schema
                    )
                predecessor_passed = (
                    predecessor_file_sha == predecessor.get("file_sha256")
                    and predecessor_manifest_sha == predecessor.get("manifest_sha256")
                    and predecessor_identity_passed
                    and predecessor.get("history_mutated") is False
                    and predecessor_scope_passed
                )
                predecessor_evidence = predecessor_file_sha
        findings.append(
            {
                "code": "predecessor_integrity_and_forward_link",
                "status": "pass" if predecessor_passed else "fail",
                "evidence": predecessor_evidence,
            }
        )

        dispositions = manifest.get("requirements_disposition")
        disposition_codes = set(dispositions) if isinstance(dispositions, dict) else set()
        findings.append(
            {
                "code": "predecessor_requirement_reconciliation",
                "status": "pass" if disposition_codes == old_blocker_codes else "fail",
                "evidence": ",".join(sorted(disposition_codes)),
            }
        )

        blockers = manifest.get("unresolved_contract_requirements")
        blocker_count = len(blockers) if isinstance(blockers, list) else -1
        counts = manifest.get("requirement_counts")
        if isinstance(dispositions, dict):
            closed_count = sum(
                str(item.get("status", "")).startswith("closed")
                for item in dispositions.values()
                if isinstance(item, dict)
            )
            carried_count = sum(
                str(item.get("status", "")).startswith("carried")
                for item in dispositions.values()
                if isinstance(item, dict)
            )
        else:
            closed_count = -1
            carried_count = -1
        counts_passed = isinstance(counts, dict) and counts == {
            "predecessor_requirements": len(old_blocker_codes),
            "closed_forward": closed_count,
            "carried_forward": carried_count,
            "remaining_blockers_after_consolidation": blocker_count,
        }
        findings.append(
            {
                "code": "forward_requirement_counts",
                "status": "pass" if counts_passed else "fail",
                "evidence": str(counts_passed).lower(),
            }
        )

        locked = manifest.get("forward_locked_contracts")
        contract_hashes_safe = (
            isinstance(locked, dict)
            and bool(locked)
            and all(_is_sha256(value) for value in locked.values())
        )
        findings.append(
            {
                "code": "forward_contract_content_identities",
                "status": "pass" if contract_hashes_safe else "fail",
                "evidence": "self-hash identities only; no private paths or item-level outcomes",
            }
        )

        if schema_version == _SCHEMA_V6:
            qwen = manifest.get("secondary_qwen_disposition")
            qwen_passed = False
            qwen_evidence = "missing"
            if isinstance(qwen, dict):
                closeout_target = _safe_repo_target(root, qwen.get("closeout_path"))
                if closeout_target is not None:
                    closeout = load_and_validate_failure_closeout(closeout_target)
                    closeout_file_sha = _sha256(closeout_target)
                    qwen_passed = (
                        qwen.get("status") == "operationally_infeasible"
                        and qwen.get("closeout_file_sha256") == closeout_file_sha
                        and qwen.get("closeout_sha256") == closeout.get("closeout_sha256")
                        and qwen.get("calibration_observed_server_tasks") == 4
                        and qwen.get("calibration_planned_inference_calls") == 7
                        and qwen.get("sensitivity_executed") is False
                        and qwen.get("sensitivity_inference_calls") == 0
                        and qwen.get("primary_gpt_main_contract_changed") is False
                        and qwen.get("rerun_permitted") is False
                    )
                    qwen_evidence = str(closeout.get("closeout_sha256"))
            findings.append(
                {
                    "code": "secondary_qwen_terminal_disposition",
                    "status": "pass" if qwen_passed else "fail",
                    "evidence": qwen_evidence,
                }
            )

            expected_census = {
                "family_count": 32,
                "superfamily_count": 6,
                "conditions_per_family": 4,
                "context_count": 128,
                "controlled_variant_count": 8,
                "controlled_logical_request_count": 1024,
                "provider_backed_logical_request_count": 896,
                "deterministic_logical_request_count": 128,
                "provider_turn_count": 1408,
                "qwen_family_count": 12,
                "qwen_planned_request_count": 72,
                "qwen_executed_request_count": 0,
                "logdx_external_case_count": 35,
                "rq6b_case_count": 0,
            }
            expected_primary_invariance = {
                "model": "gpt-4.1-2025-04-14",
                "model_changed": False,
                "prompt_policy_changed": False,
                "response_contract_changed": False,
                "evidence_context_ceiling_changed": False,
                "sealed_census_changed": False,
                "metrics_changed": False,
                "aggregation_changed": False,
                "multiplicity_changed": False,
                "missingness_policy_changed": False,
                "precision_interpretation_changed": False,
                "retry_or_terminal_failure_policy_changed": False,
                "analysis_entrypoint_changed": False,
            }
            plan_v2 = _load_object(root / "configs/evaluation/diagnosis_main_analysis_plan.json")
            plan_v3 = _load_object(root / "configs/evaluation/diagnosis_main_analysis_plan_v3.json")
            qwen_secondary_fields = {
                "qwen_sensitivity_status",
                "qwen_sensitivity_model",
                "qwen_sensitivity_estimand",
                "qwen_sensitivity_family_count",
                "qwen_sensitivity_request_count",
                "qwen_cross_model_superiority_claim_permitted",
            }
            primary_v2 = {
                key: value
                for key, value in plan_v2.items()
                if key not in {"schema_version", "plan_sha256"}
            }
            primary_v3 = {
                key: value
                for key, value in plan_v3.items()
                if key not in {"schema_version", "plan_sha256", *qwen_secondary_fields}
            }
            expected_plan_lineage = {
                "runtime_authority_primary_plan_schema": ("diagnosis-main-analysis-plan/v2"),
                "runtime_authority_primary_plan_sha256": (
                    "4dd5908cadcbb249042741329ddbdceaf4f1a4b4088724d0c1e152cc0310a94a"
                ),
                "forward_analysis_plan_schema": "diagnosis-main-analysis-plan/v3",
                "forward_analysis_plan_sha256": (
                    "09f1a1293d9fc0ac93a9b25fec1456a591d48be444d3b479c389a6e255874249"
                ),
                "primary_fields_changed": False,
                "v3_added_fields_are_qwen_secondary_only": True,
                "main_analysis_entrypoint_uses_v3": True,
                "semantic_conflict": False,
            }
            plan_lineage_passed = (
                plan_v2.get("schema_version")
                == expected_plan_lineage["runtime_authority_primary_plan_schema"]
                and plan_v2.get("plan_sha256")
                == expected_plan_lineage["runtime_authority_primary_plan_sha256"]
                and plan_v3.get("schema_version")
                == expected_plan_lineage["forward_analysis_plan_schema"]
                and plan_v3.get("plan_sha256")
                == expected_plan_lineage["forward_analysis_plan_sha256"]
                and set(plan_v3) - set(plan_v2) == qwen_secondary_fields
                and primary_v2 == primary_v3
                and manifest.get("analysis_plan_lineage") == expected_plan_lineage
            )
            expected_primary_contracts = {
                "analysis_plan_sha256": (
                    "09f1a1293d9fc0ac93a9b25fec1456a591d48be444d3b479c389a6e255874249"
                ),
                "response_contract_sha256": (
                    "d9cbbdda6e29e0e2717591124804c8c5d5a90d4f5a9cd81de67fdf36ee5bd810"
                ),
                "information_path_fairness_audit_sha256": (
                    "a14a98b310a3c59c273842bcfe3985c8606dc0f8a94bca3e87add52f66eaa9d4"
                ),
                "main_census_seal_sha256": (
                    "e010cd36a37ce100c2033e4bd9652de4842635ef79e0ba7fb9fee79c4a420b8b"
                ),
                "main_runtime_contract_sha256": (
                    "eca4170c7d27fafd393bcc9d9ddca5b25a611e5793640259b3919ebe9536fbc7"
                ),
                "logdx_cached_replay_receipt_sha256": (
                    "74ba4458a783fb685a9d08f3cac138a88437647ad497c993aaf44fa2483b4038"
                ),
                "rq6b_scope_decision_sha256": (
                    "f4a2287a81fd624f9602b003f2c67bc657126e6db97722c3ec7869a84b8a8ed9"
                ),
                "engineering_preflight_sha256": (
                    "355bbf8069033b6f633390b92a44fa34f1dd3799bba660d7626c62530abcfa73"
                ),
            }
            primary_contracts_passed = isinstance(locked, dict) and all(
                locked.get(key) == value for key, value in expected_primary_contracts.items()
            )
            primary_unchanged = (
                manifest.get("frozen_census_counts") == expected_census
                and manifest.get("primary_study_invariance") == expected_primary_invariance
                and plan_lineage_passed
                and primary_contracts_passed
            )
            findings.append(
                {
                    "code": "primary_study_unchanged_after_qwen_failure",
                    "status": "pass" if primary_unchanged else "fail",
                    "evidence": (
                        "GPT-4.1; 32 families; 128 contexts; 1024 logical "
                        "requests; frozen analysis contracts"
                    ),
                }
            )
    else:
        findings.append(
            {
                "code": "supported_manifest_schema",
                "status": "fail",
                "evidence": str(schema_version),
            }
        )

    blockers = manifest.get("unresolved_contract_requirements")
    blocker_codes = (
        [item.get("code") for item in blockers if isinstance(item, dict)]
        if isinstance(blockers, list)
        else []
    )
    lifecycle_evidence: str
    if blocker_codes:
        lifecycle_passed = (
            manifest.get("execution_authorized") is False
            and manifest.get("main_outcomes_opened") is False
            and manifest.get("main_registered_attempts_consumed") == 0
            and manifest.get("status") == _BLOCKED_STATUS.get(str(schema_version))
        )
        readiness_status = "blocked"
        lifecycle_evidence = ",".join(str(code) for code in blocker_codes)
    else:
        readiness_evidence = manifest.get("readiness_evidence")
        if schema_version == _SCHEMA_V6:
            evidence_passed = (
                isinstance(readiness_evidence, dict)
                and readiness_evidence.get("qwen_disposition") == "operationally_infeasible"
                and readiness_evidence.get("qwen_failure_closeout_sha256")
                == (
                    manifest.get("secondary_qwen_disposition", {}).get("closeout_sha256")
                    if isinstance(manifest.get("secondary_qwen_disposition"), dict)
                    else None
                )
                and readiness_evidence.get("qwen_sensitivity_executed") is False
                and readiness_evidence.get("primary_gpt_main_contract_unchanged") is True
                and readiness_evidence.get("frozen_census_verified") is True
                and readiness_evidence.get("fairness_audit_bound") is True
                and readiness_evidence.get("logdx_replay_bound") is True
                and readiness_evidence.get("engineering_preflight_bound") is True
                and readiness_evidence.get("protected_main_outcomes_opened") is False
                and readiness_evidence.get("main_registered_attempts_consumed") == 0
            )
        else:
            evidence_passed = (
                isinstance(readiness_evidence, dict)
                and _is_sha256(readiness_evidence.get("qwen_calibration_receipt_sha256"))
                and _is_sha256(readiness_evidence.get("independent_review_receipt_sha256"))
                and readiness_evidence.get("engineering_preflight_decision") == "pass"
                and readiness_evidence.get("main_freeze_decision") == "pass"
            )
        lifecycle_passed = (
            schema_version in {_SCHEMA_V4, _SCHEMA_V5, _SCHEMA_V6}
            and manifest.get("execution_authorized") is False
            and manifest.get("main_outcomes_opened") is False
            and manifest.get("main_registered_attempts_consumed") == 0
            and manifest.get("status") == _READY_STATUS
            and evidence_passed
        )
        readiness_status = "ready_for_execution_authorization"
        lifecycle_evidence = (
            "Qwen disposition and objective frozen-contract evidence present"
            if evidence_passed
            else "missing or invalid readiness evidence"
        )
    findings.append(
        {
            "code": "candidate_lifecycle_state",
            "status": "pass" if lifecycle_passed else "fail",
            "evidence": lifecycle_evidence,
        }
    )

    integrity_passed = all(item["status"] == "pass" for item in findings)
    return {
        "schema_version": "diagnosis-main-freeze-audit/v1",
        "integrity_status": "pass" if integrity_passed else "fail",
        "readiness_status": readiness_status if integrity_passed else "invalid",
        "execution_authorized": False,
        "manifest_sha256": computed_hash,
        "blocker_codes": blocker_codes,
        "findings": findings,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--require-ready", action="store_true")
    args = parser.parse_args()
    try:
        report = audit_candidate(args.root, args.manifest)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(json.dumps({"integrity_status": "fail", "error_type": type(exc).__name__}))
        return 1
    print(json.dumps(report, indent=2, sort_keys=True))
    if report["integrity_status"] != "pass":
        return 1
    if args.require_ready and report["readiness_status"] != "ready_for_execution_authorization":
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
