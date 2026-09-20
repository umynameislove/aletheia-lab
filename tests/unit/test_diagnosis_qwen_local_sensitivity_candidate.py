from __future__ import annotations

import hashlib
import json
from pathlib import Path

from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256

ROOT = Path(__file__).resolve().parents[2]
CANDIDATE = ROOT / "configs/evaluation/diagnosis_qwen_local_sensitivity_candidate.json"
CENSUS = ROOT / "configs/evaluation/diagnosis_qwen_sensitivity_census.json"


def _load_unsigned() -> tuple[dict[str, object], str]:
    payload = json.loads(CANDIDATE.read_text(encoding="utf-8"))
    declared = payload.pop("candidate_sha256")
    return payload, declared


def test_qwen_candidate_is_self_hashed_and_not_execution_authority() -> None:
    payload, declared = _load_unsigned()

    assert canonical_execution_sha256(payload) == declared
    assert payload["protected_main_outcomes_opened"] is False
    assert payload["local_model_executed"] is False
    assert payload["execution_authorized"] is False
    assert payload["scientific_role"] == (
        "secondary_model_family_sensitivity_reported_separately"
    )


def test_qwen_artifacts_runner_and_operational_fallback_are_exactly_bound() -> None:
    payload, _ = _load_unsigned()
    primary = payload["primary_artifact"]
    fallback = payload["operational_fallback"]
    runner = payload["runner"]

    assert primary["revision"] == "99d445b5fd000458cabc098da6a79c2967a472f1"
    assert primary["byte_count"] == 32_483_933_856
    assert primary["sha256"] == (
        "f22993e29318b5b9ec2026f6b65802a5ca99b38ab4844aab83aed8a26ce00ff6"
    )
    assert fallback["revision"] == "75c8d15549f5509d1cf941d8ae429f909e0f9bd9"
    assert fallback["scientific_quality_selection_forbidden"] is True
    assert fallback["different_converter_repository_disclosed"] is True
    assert runner["release"] == "v0.4.1"
    assert runner["dereferenced_commit_sha"] == (
        "b29c606e28a01b1bc8c1351026a0fa6e616bf6c4"
    )
    assert runner["cmake_flags"] == [
        "-DGGML_METAL=ON",
        "-DCMAKE_BUILD_TYPE=Release",
    ]
    assert runner["n_gpu_layers"] == 99
    assert runner["tools_supplied"] is False
    assert payload["host_envelope"]["serial_number_recorded"] is False


def test_qwen_uses_model_recommended_sampling_without_claiming_determinism() -> None:
    payload, _ = _load_unsigned()
    policy = payload["generation_policy"]

    assert policy["decoding"] == "qwen_recommended_sampling_with_fixed_seed"
    assert policy["temperature"] == 0.7
    assert policy["top_p"] == 0.8
    assert policy["top_k"] == 20
    assert policy["min_p"] == 0.0
    assert policy["repetition_penalty"] == 1.05
    assert policy["deterministic_output_claimed"] is False
    assert payload["model_family_comparison_role"].startswith("within_qwen")


def test_qwen_72_request_design_is_exactly_locked_but_execution_remains_closed() -> None:
    payload, _ = _load_unsigned()
    design = payload["sensitivity_design"]
    unresolved = set(payload["unresolved_requirements"])
    census = json.loads(CENSUS.read_text(encoding="utf-8"))

    assert design["expected_request_count"] == (
        design["family_count"]
        * len(design["evidence_conditions"])
        * len(design["variants"])
    )
    assert design["expected_request_count"] == 72
    assert design["exact_request_ids_locked"] is True
    assert design["request_ids_embedded_in_candidate"] is False
    assert design["request_census_sha256"] == census["census_sha256"]
    assert design["request_census_file_sha256"] == hashlib.sha256(
        CENSUS.read_bytes()
    ).hexdigest()
    assert census["request_count"] == len(census["request_ids"]) == 72
    assert census["family_count"] == len(census["family_ids"]) == 12
    assert design["main_model_pooling_forbidden"] is True
    assert all("main_census" not in requirement for requirement in unresolved)
    assert payload["status"].endswith("blocked_on_local_calibration")


def test_development_calibration_is_bounded_and_outcome_blind() -> None:
    payload, _ = _load_unsigned()
    contract = payload["development_calibration_contract"]

    assert contract["calibration_cell_count"] == 6
    assert contract["total_local_inference_calls"] == 7
    assert contract["scientific_quality_selection_permitted"] is False
    assert contract["main_outcomes_permitted"] is False
    for key, value in contract.items():
        if not key.endswith("_path"):
            continue
        hash_key = key.removesuffix("_path") + "_file_sha256"
        assert hashlib.sha256((ROOT / value).read_bytes()).hexdigest() == contract[hash_key]
