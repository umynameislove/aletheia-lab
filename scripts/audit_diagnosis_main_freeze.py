#!/usr/bin/env python3
"""Verify the main diagnosis freeze candidate without authorizing execution."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Any

from aletheia_lab.benchmark.p2.canonical import canonical_sha256

DEFAULT_MANIFEST = Path("configs/evaluation/diagnosis_main_freeze_candidate.json")


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
            path_value = PurePosixPath(str(relative))
            target = root.joinpath(*path_value.parts)
            valid_path = not path_value.is_absolute() and ".." not in path_value.parts
            actual = (
                _sha256(target)
                if valid_path and target.is_file() and not target.is_symlink()
                else "missing"
            )
            findings.append(
                {
                    "code": f"artifact.{relative}",
                    "status": "pass" if actual == expected else "fail",
                    "evidence": actual,
                }
            )

    fairness = _load_object(root / "configs/evaluation/diagnosis_variant_fairness_freeze.json")
    prompt_hashes = {
        key: value["prompt_content_sha256"] for key, value in fairness["prompt_policies"].items()
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
            {"code": code, "status": "pass" if passed else "fail", "evidence": str(passed).lower()}
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
            ("assumption_limited_abstention_track", "assumption_limited_abstention_track"),
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
        and all(
            isinstance(value, str)
            and len(value) == 64
            and all(character in "0123456789abcdef" for character in value)
            for value in external_bindings.values()
        )
    )
    findings.append(
        {
            "code": "external_evidence_content_identities",
            "status": "pass" if external_safe else "fail",
            "evidence": "content hashes only; private source paths are not embedded",
        }
    )

    blockers = manifest.get("unresolved_contract_requirements")
    blocker_codes = (
        [item.get("code") for item in blockers if isinstance(item, dict)]
        if isinstance(blockers, list)
        else []
    )
    fail_closed = (
        bool(blocker_codes)
        and manifest.get("execution_authorized") is False
        and manifest.get("main_outcomes_opened") is False
        and manifest.get("status") == "candidate_integrity_locked_main_execution_blocked"
    )
    findings.append(
        {
            "code": "blocked_candidate_fails_closed",
            "status": "pass" if fail_closed else "fail",
            "evidence": ",".join(str(code) for code in blocker_codes),
        }
    )

    integrity_passed = all(item["status"] == "pass" for item in findings)
    return {
        "schema_version": "diagnosis-main-freeze-audit/v1",
        "integrity_status": "pass" if integrity_passed else "fail",
        "readiness_status": "blocked" if blocker_codes else "ready_for_independent_review",
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
    if args.require_ready and report["readiness_status"] != "ready_for_independent_review":
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
