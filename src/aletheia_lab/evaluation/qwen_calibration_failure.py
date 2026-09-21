"""Validate the sanitized terminal disposition for failed Qwen calibration."""

from __future__ import annotations

import json
import re
from pathlib import Path

from aletheia_lab.content_hashing import file_sha256
from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256

FAILURE_SCHEMA_VERSION = "diagnosis-qwen38-calibration-failure-closeout/v1"
FAILURE_STATUS = "operationally_infeasible"
_SERVER_LOG_SHA256 = (
    "02cb252349b462c9d3dd6b5c8c21536f5ea47545f66c1e3b0a84418ac6694812"
)
_TERMINAL_TRANSCRIPT_SHA256 = (
    "871f0933b549d85c88447b7a59e9b5064e397e1b15d4d245fcd5b1ea45d8a1cf"
)


class QwenCalibrationFailureError(ValueError):
    """Raised when a calibration-failure closeout cannot be verified."""


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _is_git_sha1(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 40
        and all(character in "0123456789abcdef" for character in value)
    )


def _load_object(path: Path) -> dict[str, object]:
    if path.is_symlink() or not path.is_file():
        raise QwenCalibrationFailureError("failure closeout must be a regular file")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise QwenCalibrationFailureError("failure closeout is invalid JSON") from exc
    if not isinstance(payload, dict):
        raise QwenCalibrationFailureError("failure closeout must be a JSON object")
    return payload


def _require_exact_fixed_fields(payload: dict[str, object]) -> None:
    fixed = {
        "schema_version": FAILURE_SCHEMA_VERSION,
        "status": FAILURE_STATUS,
        "protected_main_outcomes_opened": False,
        "main_registered_attempts_consumed": 0,
        "qwen_sensitivity_executed": False,
        "qwen_sensitivity_inference_calls": 0,
        "calibration_planned_inference_calls": 7,
        "calibration_observed_server_tasks": 4,
        "calibration_confirmed_conforming_records_before_failure": 3,
        "calibration_receipt_created": False,
        "raw_model_responses_retained": False,
        "private_paths_embedded": False,
        "rerun_permitted_under_current_registration": False,
        "fallback_model_or_quantization_permitted": False,
        "pool_with_primary_permitted": False,
        "primary_gpt_main_contract_changed": False,
        "sealed_main_census_changed": False,
        "analysis_plan_changed": False,
    }
    if any(payload.get(key) != value for key, value in fixed.items()):
        raise QwenCalibrationFailureError("failure closeout fixed fields differ")

    if payload.get("created_on") != "2026-09-21":
        raise QwenCalibrationFailureError("failure closeout date differs")
    if payload.get("inference_basis") != {
        "frozen_request_order_used": True,
        "server_task_count_used": True,
        "raw_response_inspected": False,
        "claim_limit": (
            "The log and deterministic control flow locate the terminal boundary "
            "but do not identify the exact inner validation exception."
        ),
    }:
        raise QwenCalibrationFailureError("failure inference basis differs")
    if payload.get("scientific_disposition") != {
        "rule_source": "prespecified_q8_failure_branch",
        "qwen_role": "secondary_robustness_study_not_executed",
        "reporting_label": FAILURE_STATUS,
        "main_gpt_study_impact": "none",
        "qwen_72_request_census_status": "frozen_but_not_executed",
        "cross_model_claim_permitted": False,
        "post_failure_prompt_schema_sampling_or_model_selection_permitted": False,
        "future_qwen_work_requires_new_registration": True,
    }:
        raise QwenCalibrationFailureError("failure scientific disposition differs")
    if payload.get("privacy") != {
        "server_log_retained_outside_git": True,
        "terminal_transcript_retained_outside_git": True,
        "model_output_text_in_closeout": False,
        "absolute_local_path_in_closeout": False,
        "operator_identity_in_closeout": False,
    }:
        raise QwenCalibrationFailureError("failure privacy disposition differs")


def _validate_failure_boundary(payload: dict[str, object]) -> None:
    boundary = payload.get("failure_boundary")
    expected = {
        "server_started": True,
        "model_loaded": True,
        "loopback_only": True,
        "serial_slot_count": 1,
        "completed_server_task_count": 4,
        "completed_without_truncation_count": 4,
        "failed_request_index_in_frozen_sequence": 4,
        "failed_calibration_id": (
            "qcal-00e5b549b7cdbeb685754ca723d65083c48279c9064ce7a614622150fbbbcac9"
        ),
        "failed_case_id": "devcase-conflicting-signals",
        "failed_variant": "A3",
        "cli_error_type": "QwenCalibrationError",
        "specific_inner_validation_cause_observed": False,
        "terminal_stage": "fourth_inference_response_handling_or_post_generation_validation",
    }
    if boundary != expected:
        raise QwenCalibrationFailureError("failure boundary differs")


def _validate_evidence_identities(payload: dict[str, object]) -> None:
    identities = payload.get("private_failure_evidence_content_identities")
    if not isinstance(identities, dict):
        raise QwenCalibrationFailureError("private evidence identities are missing")
    expected_keys = {
        "v2_server_log_byte_sha256",
        "operator_terminal_transcript_byte_sha256",
    }
    if set(identities) != expected_keys or identities != {
        "v2_server_log_byte_sha256": _SERVER_LOG_SHA256,
        "operator_terminal_transcript_byte_sha256": _TERMINAL_TRANSCRIPT_SHA256,
    }:
        raise QwenCalibrationFailureError("private evidence identities are malformed")
    byte_counts = payload.get("private_failure_evidence_byte_counts")
    if byte_counts != {
        "v2_server_log": 14235,
        "operator_terminal_transcript": 5064,
    }:
        raise QwenCalibrationFailureError("private evidence byte counts differ")


def validate_failure_closeout(payload: dict[str, object]) -> dict[str, object]:
    """Validate a sanitized failure disposition without reading private evidence."""

    unsigned = {key: value for key, value in payload.items() if key != "closeout_sha256"}
    closeout_sha = canonical_execution_sha256(unsigned)
    if payload.get("closeout_sha256") != closeout_sha:
        raise QwenCalibrationFailureError("failure closeout self-hash mismatch")
    _require_exact_fixed_fields(payload)
    _validate_failure_boundary(payload)
    _validate_evidence_identities(payload)

    lineage = payload.get("lineage")
    if (
        not isinstance(lineage, dict)
        or not all(
            _is_sha256(lineage.get(key))
            for key in (
                "qwen_candidate_sha256",
                "transport_correction_sha256",
                "model_sha256",
                "response_contract_sha256",
            )
        )
        or not _is_git_sha1(lineage.get("llama_cpp_commit_sha"))
    ):
        raise QwenCalibrationFailureError("failure closeout lineage is malformed")
    if lineage.get("qwen_candidate_sha256") != (
        "db61a2ec899e546dc0ece77120624c45296cbe3c547b52d04b31b71c34b3388d"
    ):
        raise QwenCalibrationFailureError("failure closeout candidate identity differs")
    if lineage.get("transport_correction_sha256") != (
        "bc158c27f8881e1ad0995793adceaef52c6e7f7fbe3f6fc21c8a80e4de077ed4"
    ):
        raise QwenCalibrationFailureError("failure closeout correction identity differs")
    if lineage.get("model_sha256") != (
        "aab65c67ef0dad127960efef9247f1832bca105faa1c7a052cc039b223cf86a1"
    ):
        raise QwenCalibrationFailureError("failure closeout model identity differs")
    if lineage.get("llama_cpp_commit_sha") != (
        "b29c606e28a01b1bc8c1351026a0fa6e616bf6c4"
    ):
        raise QwenCalibrationFailureError("failure closeout runtime identity differs")
    if lineage.get("response_contract_sha256") != (
        "d9cbbdda6e29e0e2717591124804c8c5d5a90d4f5a9cd81de67fdf36ee5bd810"
    ):
        raise QwenCalibrationFailureError("failure closeout response contract differs")

    return {
        "schema_version": "diagnosis-qwen38-calibration-failure-audit/v1",
        "status": "pass",
        "disposition": FAILURE_STATUS,
        "closeout_sha256": closeout_sha,
        "observed_server_tasks": payload["calibration_observed_server_tasks"],
        "planned_inference_calls": payload["calibration_planned_inference_calls"],
        "qwen_sensitivity_inference_calls": payload["qwen_sensitivity_inference_calls"],
        "protected_main_outcomes_opened": payload["protected_main_outcomes_opened"],
        "main_registered_attempts_consumed": payload["main_registered_attempts_consumed"],
    }


def load_and_validate_failure_closeout(path: Path) -> dict[str, object]:
    """Load and validate a tracked sanitized Qwen failure closeout."""

    payload = _load_object(path)
    validate_failure_closeout(payload)
    return payload


def audit_private_server_log(
    payload: dict[str, object], server_log: Path
) -> dict[str, object]:
    """Verify the private server log while emitting only aggregate evidence."""

    if server_log.is_symlink() or not server_log.is_file():
        raise QwenCalibrationFailureError("private server log must be a regular file")
    identities = payload["private_failure_evidence_content_identities"]
    counts = payload["private_failure_evidence_byte_counts"]
    if not isinstance(identities, dict) or not isinstance(counts, dict):
        raise QwenCalibrationFailureError("private evidence bindings are malformed")
    if file_sha256(server_log) != identities["v2_server_log_byte_sha256"]:
        raise QwenCalibrationFailureError("private server log SHA-256 mismatch")
    if server_log.stat().st_size != counts["v2_server_log"]:
        raise QwenCalibrationFailureError("private server log byte count mismatch")
    try:
        log_text = server_log.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise QwenCalibrationFailureError("private server log is unreadable") from exc

    observations = {
        "model_loaded_count": log_text.count("model loaded"),
        "loopback_listener_count": log_text.count(
            "listening on http://127.0.0.1:18080"
        ),
        "processing_task_count": len(
            re.findall(r"processing task, is_child = 0", log_text)
        ),
        "completed_untruncated_task_count": len(
            re.findall(r"stop processing:.*truncated = 0", log_text)
        ),
        "cleanup_count": log_text.count("cleaning up before exit"),
    }
    expected = {
        "model_loaded_count": 1,
        "loopback_listener_count": 1,
        "processing_task_count": 4,
        "completed_untruncated_task_count": 4,
        "cleanup_count": 1,
    }
    if observations != expected or "truncated = 1" in log_text:
        raise QwenCalibrationFailureError("private server log observations differ")
    return {
        "status": "pass",
        "server_log_sha256": identities["v2_server_log_byte_sha256"],
        "server_log_byte_count": counts["v2_server_log"],
        **observations,
        "raw_model_response_content_emitted": False,
        "private_path_emitted": False,
    }
