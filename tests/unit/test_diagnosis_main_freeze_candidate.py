from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from aletheia_lab.benchmark.p2.canonical import canonical_sha256

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "audit_diagnosis_main_freeze.py"
MANIFEST_V1 = ROOT / "configs" / "evaluation" / "diagnosis_main_freeze_candidate.json"
MANIFEST_V2 = ROOT / "configs" / "evaluation" / "diagnosis_main_freeze_candidate_v2.json"
MANIFEST_V3 = ROOT / "configs" / "evaluation" / "diagnosis_main_freeze_candidate_v3.json"
MANIFEST_V4 = ROOT / "configs" / "evaluation" / "diagnosis_main_freeze_candidate_v4.json"
MANIFEST_V5 = ROOT / "configs" / "evaluation" / "diagnosis_main_freeze_candidate_v5.json"


def _run(*extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(ROOT), *extra],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def test_forward_candidate_integrity_passes_but_main_execution_remains_blocked() -> None:
    completed = _run()
    assert completed.returncode == 0
    report = json.loads(completed.stdout)
    assert report["integrity_status"] == "pass"
    assert report["readiness_status"] == "blocked"
    assert report["execution_authorized"] is False
    assert "analysis_plan_contains_unresolved_tbd_fields" not in report["blocker_codes"]
    assert "sealed_main_case_split_and_request_census_missing" not in report["blocker_codes"]
    assert "outcome_eligible_nine_path_runtime_missing" not in report["blocker_codes"]
    assert report["blocker_codes"] == [
        "qwen_local_artifact_build_and_calibration_receipt_missing",
        "independent_methods_approval_missing",
    ]

    ready_gate = _run("--require-ready")
    assert ready_gate.returncode == 2


def test_resigned_policy_drift_fails_cross_artifact_reconciliation(tmp_path: Path) -> None:
    payload = json.loads(MANIFEST_V1.read_text(encoding="utf-8"))
    payload["frozen_model_policy"]["max_output_tokens"] = 601
    unsigned = {key: value for key, value in payload.items() if key != "manifest_sha256"}
    payload["manifest_sha256"] = canonical_sha256(unsigned)
    changed = tmp_path / "candidate.json"
    changed.write_text(json.dumps(payload), encoding="utf-8")

    completed = _run("--manifest", str(changed))
    assert completed.returncode == 1
    report = json.loads(completed.stdout)
    assert report["integrity_status"] == "fail"
    finding = next(item for item in report["findings"] if item["code"] == "model_policy_parity")
    assert finding["status"] == "fail"


def test_forward_candidate_reconciles_every_predecessor_blocker_once() -> None:
    predecessor = json.loads(MANIFEST_V4.read_text(encoding="utf-8"))
    forward = json.loads(MANIFEST_V5.read_text(encoding="utf-8"))

    old_codes = {
        item["code"] for item in predecessor["unresolved_contract_requirements"]
    }
    assert set(forward["requirements_disposition"]) == old_codes
    assert forward["requirement_counts"] == {
        "predecessor_requirements": 2,
        "closed_forward": 0,
        "carried_forward": 2,
        "remaining_blockers_after_consolidation": 2,
    }
    assert forward["execution_authorized"] is False
    assert forward["main_outcomes_opened"] is False


def test_forward_candidate_artifact_tampering_fails_integrity(tmp_path: Path) -> None:
    payload = json.loads(MANIFEST_V5.read_text(encoding="utf-8"))
    payload["repo_artifact_bindings"][
        "configs/evaluation/diagnosis_main_analysis_plan_v3.json"
    ] = "0" * 64
    unsigned = {key: value for key, value in payload.items() if key != "manifest_sha256"}
    payload["manifest_sha256"] = canonical_sha256(unsigned)
    changed = tmp_path / "candidate-v4.json"
    changed.write_text(json.dumps(payload), encoding="utf-8")

    completed = _run("--manifest", str(changed))
    assert completed.returncode == 1
    report = json.loads(completed.stdout)
    finding = next(
        item
        for item in report["findings"]
        if item["code"]
        == "artifact.configs/evaluation/diagnosis_main_analysis_plan_v3.json"
    )
    assert finding["status"] == "fail"


def _ready_v6_payload() -> dict[str, object]:
    predecessor = json.loads(MANIFEST_V5.read_text(encoding="utf-8"))
    dispositions = {
        item["code"]: {
            "status": "closed_forward",
            "evidence": "Bound by a content-addressed post-calibration review receipt.",
        }
        for item in predecessor["unresolved_contract_requirements"]
    }
    payload = {
        **predecessor,
        "schema_version": "diagnosis-main-freeze-candidate/v6",
        "status": "final_forward_integrity_locked_ready_for_execution_authorization",
        "predecessor": {
            "path": "configs/evaluation/diagnosis_main_freeze_candidate_v5.json",
            "file_sha256": __import__("hashlib").sha256(MANIFEST_V5.read_bytes()).hexdigest(),
            "manifest_sha256": predecessor["manifest_sha256"],
            "history_mutated": False,
        },
        "requirements_disposition": dispositions,
        "requirement_counts": {
            "predecessor_requirements": 2,
            "closed_forward": 2,
            "carried_forward": 0,
            "remaining_blockers_after_consolidation": 0,
        },
        "unresolved_contract_requirements": [],
        "readiness_evidence": {
            "qwen_calibration_receipt_sha256": "a" * 64,
            "independent_review_receipt_sha256": "b" * 64,
            "engineering_preflight_decision": "pass",
            "main_freeze_decision": "pass",
        },
    }
    unsigned = {key: value for key, value in payload.items() if key != "manifest_sha256"}
    payload["manifest_sha256"] = canonical_sha256(unsigned)
    return payload


def test_final_v6_can_pass_require_ready_only_with_closed_receipted_gates(
    tmp_path: Path,
) -> None:
    payload = _ready_v6_payload()
    ready = tmp_path / "candidate-v6.json"
    ready.write_text(json.dumps(payload), encoding="utf-8")

    completed = _run("--manifest", str(ready), "--require-ready")
    assert completed.returncode == 0
    report = json.loads(completed.stdout)
    assert report["integrity_status"] == "pass"
    assert report["readiness_status"] == "ready_for_execution_authorization"
    assert report["execution_authorized"] is False


def test_final_v6_without_valid_readiness_evidence_fails_closed(tmp_path: Path) -> None:
    payload = _ready_v6_payload()
    payload.pop("readiness_evidence")
    unsigned = {key: value for key, value in payload.items() if key != "manifest_sha256"}
    payload["manifest_sha256"] = canonical_sha256(unsigned)
    invalid = tmp_path / "candidate-v6-invalid.json"
    invalid.write_text(json.dumps(payload), encoding="utf-8")

    completed = _run("--manifest", str(invalid), "--require-ready")
    assert completed.returncode == 1
    report = json.loads(completed.stdout)
    finding = next(
        item for item in report["findings"] if item["code"] == "candidate_lifecycle_state"
    )
    assert finding["status"] == "fail"
