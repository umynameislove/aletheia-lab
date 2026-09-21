#!/usr/bin/env python3
"""Verify the main diagnosis freeze candidate without authorizing execution."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Any

from aletheia_lab.benchmark.p2.canonical import canonical_sha256

DEFAULT_MANIFEST = Path("configs/evaluation/diagnosis_main_freeze_candidate_v3.json")

_SCHEMA_V1 = "diagnosis-main-freeze-candidate/v1"
_SCHEMA_V2 = "diagnosis-main-freeze-candidate/v2"
_SCHEMA_V3 = "diagnosis-main-freeze-candidate/v3"
_SCHEMA_V4 = "diagnosis-main-freeze-candidate/v4"
_BLOCKED_STATUS = {
    _SCHEMA_V1: "candidate_integrity_locked_main_execution_blocked",
    _SCHEMA_V2: "candidate_forward_integrity_locked_main_execution_blocked",
    _SCHEMA_V3: "candidate_forward_integrity_locked_main_execution_blocked",
    _SCHEMA_V4: "final_forward_integrity_locked_main_execution_blocked",
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
        fairness = _load_object(
            root / "configs/evaluation/diagnosis_variant_fairness_freeze.json"
        )
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
    elif schema_version in {_SCHEMA_V2, _SCHEMA_V3, _SCHEMA_V4}:
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
                predecessor_blockers = predecessor_payload.get(
                    "unresolved_contract_requirements"
                )
                if isinstance(predecessor_blockers, list):
                    old_blocker_codes = {
                        str(item.get("code"))
                        for item in predecessor_blockers
                        if isinstance(item, dict)
                    }
                predecessor_identity_passed = (
                    predecessor_manifest_sha
                    == _canonical_manifest_hash(predecessor_payload)
                )
                expected_predecessor_schema = {
                    _SCHEMA_V2: _SCHEMA_V1,
                    _SCHEMA_V3: _SCHEMA_V2,
                    _SCHEMA_V4: _SCHEMA_V3,
                }[str(schema_version)]
                if schema_version == _SCHEMA_V2:
                    predecessor_report = audit_candidate(root, predecessor_target)
                    predecessor_scope_passed = (
                        predecessor_report["integrity_status"] == "pass"
                        and predecessor_payload.get("schema_version")
                        == expected_predecessor_schema
                    )
                else:
                    # Forward candidates bind the predecessor manifest bytes and
                    # canonical identity. They bind the current artifact bytes
                    # directly rather than requiring superseded mutable targets
                    # from an earlier local candidate to remain at old hashes.
                    predecessor_scope_passed = (
                        predecessor_payload.get("schema_version")
                        == expected_predecessor_schema
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
        evidence_passed = (
            isinstance(readiness_evidence, dict)
            and _is_sha256(readiness_evidence.get("qwen_calibration_receipt_sha256"))
            and _is_sha256(readiness_evidence.get("independent_review_receipt_sha256"))
            and readiness_evidence.get("engineering_preflight_decision") == "pass"
            and readiness_evidence.get("main_freeze_decision") == "pass"
        )
        lifecycle_passed = (
            schema_version == _SCHEMA_V4
            and manifest.get("execution_authorized") is False
            and manifest.get("main_outcomes_opened") is False
            and manifest.get("main_registered_attempts_consumed") == 0
            and manifest.get("status") == _READY_STATUS
            and evidence_passed
        )
        readiness_status = "ready_for_execution_authorization"
        lifecycle_evidence = (
            "qwen calibration and independent review content identities present"
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
    if (
        args.require_ready
        and report["readiness_status"] != "ready_for_execution_authorization"
    ):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
