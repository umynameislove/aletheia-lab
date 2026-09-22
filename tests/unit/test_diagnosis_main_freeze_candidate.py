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
MANIFEST_V6 = ROOT / "configs" / "evaluation" / "diagnosis_main_freeze_candidate_v6.json"
MANIFEST_V7 = ROOT / "configs" / "evaluation" / "diagnosis_main_freeze_candidate_v7.json"


def _run(*extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(ROOT), *extra],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )


def test_forward_candidate_integrity_is_ready_without_authorizing_execution() -> None:
    completed = _run()
    assert completed.returncode == 0
    report = json.loads(completed.stdout)
    assert report["integrity_status"] == "pass"
    assert report["readiness_status"] == "ready_for_execution_authorization"
    assert report["execution_authorized"] is False
    assert "analysis_plan_contains_unresolved_tbd_fields" not in report["blocker_codes"]
    assert "sealed_main_case_split_and_request_census_missing" not in report["blocker_codes"]
    assert "outcome_eligible_nine_path_runtime_missing" not in report["blocker_codes"]
    assert report["blocker_codes"] == []

    ready_gate = _run("--require-ready")
    assert ready_gate.returncode == 0


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
    predecessor = json.loads(MANIFEST_V5.read_text(encoding="utf-8"))
    forward = json.loads(MANIFEST_V6.read_text(encoding="utf-8"))

    old_codes = {item["code"] for item in predecessor["unresolved_contract_requirements"]}
    assert set(forward["requirements_disposition"]) == old_codes
    assert forward["requirement_counts"] == {
        "predecessor_requirements": 2,
        "closed_forward": 2,
        "carried_forward": 0,
        "remaining_blockers_after_consolidation": 0,
    }
    assert forward["execution_authorized"] is False
    assert forward["main_outcomes_opened"] is False


def test_v7_binds_the_authorized_pipeline_without_consuming_the_attempt() -> None:
    predecessor = json.loads(MANIFEST_V6.read_text(encoding="utf-8"))
    forward = json.loads(MANIFEST_V7.read_text(encoding="utf-8"))

    assert predecessor["unresolved_contract_requirements"] == []
    assert forward["requirements_disposition"] == {}
    assert forward["requirement_counts"] == {
        "predecessor_requirements": 0,
        "closed_forward": 0,
        "carried_forward": 0,
        "remaining_blockers_after_consolidation": 0,
    }
    assert forward["execution_pipeline"] == {
        "status": "outcome_blind_authorized_pipeline_locked",
        "logical_request_count": 1024,
        "provider_backed_logical_request_count": 896,
        "deterministic_logical_request_count": 128,
        "maximum_main_provider_turn_count": 1408,
        "maximum_relation_request_count": 4480,
        "offline_end_to_end_preflight_tested": True,
        "offline_preflight_is_scientific_result": False,
        "private_packet_preflight_required_before_authorization": True,
        "registered_attempts_consumed": 0,
        "provider_calls_executed": False,
        "terminal_only_resume": True,
        "incomplete_request_replay_permitted": False,
        "shared_cost_guard_required": True,
        "raw_artifacts_private": True,
        "action_time_authorization_confirmation_required": True,
    }
    assert forward["execution_authorized"] is False
    assert forward["main_outcomes_opened"] is False
    assert forward["main_registered_attempts_consumed"] == 0


def test_v7_rejects_resealed_pipeline_policy_drift(tmp_path: Path) -> None:
    payload = json.loads(MANIFEST_V7.read_text(encoding="utf-8"))
    payload["execution_pipeline"]["incomplete_request_replay_permitted"] = True
    unsigned = {key: value for key, value in payload.items() if key != "manifest_sha256"}
    payload["manifest_sha256"] = canonical_sha256(unsigned)
    changed = tmp_path / "candidate-v7-pipeline-drift.json"
    changed.write_text(json.dumps(payload), encoding="utf-8")

    completed = _run("--manifest", str(changed))
    assert completed.returncode == 1
    report = json.loads(completed.stdout)
    finding = next(
        item for item in report["findings"] if item["code"] == "authorized_pipeline_forward_lock"
    )
    assert finding["status"] == "fail"


def test_v7_rejects_missing_pipeline_implementation_binding(tmp_path: Path) -> None:
    payload = json.loads(MANIFEST_V7.read_text(encoding="utf-8"))
    del payload["repo_artifact_bindings"]["src/aletheia_lab/diagnosis/main_pipeline.py"]
    unsigned = {key: value for key, value in payload.items() if key != "manifest_sha256"}
    payload["manifest_sha256"] = canonical_sha256(unsigned)
    changed = tmp_path / "candidate-v7-missing-implementation.json"
    changed.write_text(json.dumps(payload), encoding="utf-8")

    completed = _run("--manifest", str(changed))
    assert completed.returncode == 1
    report = json.loads(completed.stdout)
    finding = next(
        item
        for item in report["findings"]
        if item["code"] == "authorized_pipeline_implementation_identity"
    )
    assert finding["status"] == "fail"


def test_forward_candidate_artifact_tampering_fails_integrity(tmp_path: Path) -> None:
    payload = json.loads(MANIFEST_V6.read_text(encoding="utf-8"))
    payload["repo_artifact_bindings"]["configs/evaluation/diagnosis_main_analysis_plan_v3.json"] = (
        "0" * 64
    )
    unsigned = {key: value for key, value in payload.items() if key != "manifest_sha256"}
    payload["manifest_sha256"] = canonical_sha256(unsigned)
    changed = tmp_path / "candidate-v6.json"
    changed.write_text(json.dumps(payload), encoding="utf-8")

    completed = _run("--manifest", str(changed))
    assert completed.returncode == 1
    report = json.loads(completed.stdout)
    finding = next(
        item
        for item in report["findings"]
        if item["code"] == "artifact.configs/evaluation/diagnosis_main_analysis_plan_v3.json"
    )
    assert finding["status"] == "fail"


def test_final_v6_without_valid_readiness_evidence_fails_closed(tmp_path: Path) -> None:
    payload = json.loads(MANIFEST_V6.read_text(encoding="utf-8"))
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


def test_v6_preserves_primary_census_and_records_zero_qwen_study_calls() -> None:
    payload = json.loads(MANIFEST_V6.read_text(encoding="utf-8"))
    counts = payload["frozen_census_counts"]
    assert counts == {
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
    assert payload["primary_study_invariance"]["model"] == "gpt-4.1-2025-04-14"
    assert payload["analysis_plan_lineage"] == {
        "runtime_authority_primary_plan_schema": "diagnosis-main-analysis-plan/v2",
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
    assert payload["secondary_qwen_disposition"]["status"] == ("operationally_infeasible")
    assert payload["main_registered_attempts_consumed"] == 0
    assert payload["main_outcomes_opened"] is False


def test_v6_rejects_resealed_primary_census_drift(tmp_path: Path) -> None:
    payload = json.loads(MANIFEST_V6.read_text(encoding="utf-8"))
    payload["frozen_census_counts"]["controlled_logical_request_count"] = 1023
    unsigned = {key: value for key, value in payload.items() if key != "manifest_sha256"}
    payload["manifest_sha256"] = canonical_sha256(unsigned)
    changed = tmp_path / "candidate-v6-primary-drift.json"
    changed.write_text(json.dumps(payload), encoding="utf-8")

    completed = _run("--manifest", str(changed))
    assert completed.returncode == 1
    report = json.loads(completed.stdout)
    finding = next(
        item
        for item in report["findings"]
        if item["code"] == "primary_study_unchanged_after_qwen_failure"
    )
    assert finding["status"] == "fail"


def test_v6_rejects_unbound_qwen_closeout_identity(tmp_path: Path) -> None:
    payload = json.loads(MANIFEST_V6.read_text(encoding="utf-8"))
    payload["readiness_evidence"]["qwen_failure_closeout_sha256"] = "a" * 64
    unsigned = {key: value for key, value in payload.items() if key != "manifest_sha256"}
    payload["manifest_sha256"] = canonical_sha256(unsigned)
    invalid = tmp_path / "candidate-v6-wrong-closeout.json"
    invalid.write_text(json.dumps(payload), encoding="utf-8")

    completed = _run("--manifest", str(invalid), "--require-ready")
    assert completed.returncode == 1
    report = json.loads(completed.stdout)
    finding = next(
        item for item in report["findings"] if item["code"] == "candidate_lifecycle_state"
    )
    assert finding["status"] == "fail"
