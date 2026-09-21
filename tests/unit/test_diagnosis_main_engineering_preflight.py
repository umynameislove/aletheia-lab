from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath

from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256

ROOT = Path(__file__).resolve().parents[2]
PREFLIGHT = ROOT / "configs/evaluation/diagnosis_main_engineering_preflight_v2.json"
CORRECTION = ROOT / "configs/evaluation/diagnosis_qwen38_calibration_technical_correction.json"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_engineering_preflight_is_self_hashed_and_source_bound() -> None:
    payload = json.loads(PREFLIGHT.read_text(encoding="utf-8"))
    correction = json.loads(CORRECTION.read_text(encoding="utf-8"))
    declared = payload.pop("candidate_sha256")

    assert canonical_execution_sha256(payload) == declared
    observed_superseded: dict[str, str] = {}
    for relative, expected in payload["repo_artifact_bindings"].items():
        parts = PurePosixPath(relative)
        assert not parts.is_absolute() and ".." not in parts.parts
        if _sha(ROOT.joinpath(*parts.parts)) != expected:
            observed_superseded[relative] = expected
    assert observed_superseded == correction["superseded_repo_bindings"]


def test_engineering_preflight_does_not_promote_development_checks() -> None:
    payload = json.loads(PREFLIGHT.read_text(encoding="utf-8"))
    checks = {item["check"]: item["status"] for item in payload["acceptance_checks"]}
    blockers = {item["code"] for item in payload["blocking_requirements"]}

    assert payload["protected_outcomes_opened"] is False
    assert payload["main_execution_authorized"] is False
    assert checks["external_mock_path"] == "pass_development_only"
    assert checks["deterministic_local_path"] == "pass_development_only"
    assert checks["main_controlled_runtime"] == "pass_implementation"
    assert checks["sealed_main_census"] == "pass"
    assert checks["qwen_72_request_sensitivity_census"] == "pass"
    assert checks["pinned_qwen_local_path"] == "blocked"
    assert "main_nine_variant_request_builder_and_executor_missing" not in blockers
    assert "sealed_main_census_and_qwen_request_ids_missing" not in blockers
    assert "qwen_local_artifact_build_and_calibration_receipt_missing" in blockers
    assert "independent_methods_approval_missing" in blockers


def test_engineering_preflight_local_verification_is_bounded_and_offline() -> None:
    payload = json.loads(PREFLIGHT.read_text(encoding="utf-8"))
    verification = payload["local_verification"]

    assert verification["selected_test_count"] == 33
    assert verification["scope"] == (
        "qwen_candidate_calibration_and_main_analysis_contract_focused_tests"
    )
    assert verification["result"] == "pass"
    assert verification["real_provider_calls"] == 0
    assert verification["local_qwen_inferences"] == 0
