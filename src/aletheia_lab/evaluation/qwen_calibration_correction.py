"""Verification for the prospective Qwen calibration transport correction."""

from __future__ import annotations

import json
from pathlib import Path, PurePosixPath

from aletheia_lab.content_hashing import file_sha256
from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256
from aletheia_lab.evaluation.qwen_local_calibration import QwenCalibrationError

CORRECTION_SCHEMA_VERSION = "diagnosis-qwen38-calibration-technical-correction/v1"
_REQUIRED_IMPLEMENTATION_BINDINGS = frozenset(
    {
        "scripts/audit_qwen_local_calibration.py",
        "scripts/calibrate_qwen_local_sensitivity.py",
        "src/aletheia_lab/evaluation/qwen_calibration_correction.py",
        "src/aletheia_lab/evaluation/qwen_calibration_transport.py",
        "src/aletheia_lab/evaluation/qwen_local_calibration.py",
        "tests/unit/test_qwen_local_calibration.py",
        "tests/unit/test_qwen_local_calibration_portable.py",
    }
)
_REQUIRED_SUPERSEDED_BINDINGS = frozenset(
    {
        "docs/qwen38-local-sensitivity-preparation.md",
        "scripts/audit_diagnosis_main_freeze.py",
        "scripts/audit_qwen_local_calibration.py",
        "scripts/calibrate_qwen_local_sensitivity.py",
        "src/aletheia_lab/evaluation/qwen_local_calibration.py",
    }
)


def _load_object(path: Path) -> dict[str, object]:
    if path.is_symlink() or not path.is_file():
        raise QwenCalibrationError("technical correction must be a regular file")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise QwenCalibrationError("technical correction is invalid JSON") from exc
    if not isinstance(payload, dict):
        raise QwenCalibrationError("technical correction must be a JSON object")
    return payload


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _verify_repo_bindings(root: Path, bindings: object) -> None:
    if not isinstance(bindings, dict) or set(bindings) != _REQUIRED_IMPLEMENTATION_BINDINGS:
        raise QwenCalibrationError("technical correction implementation bindings differ")
    resolved_root = root.resolve()
    for relative, expected in sorted(bindings.items()):
        relative_path = PurePosixPath(str(relative))
        if relative_path.is_absolute() or ".." in relative_path.parts or not _is_sha256(expected):
            raise QwenCalibrationError("technical correction contains an unsafe binding")
        target = resolved_root.joinpath(*relative_path.parts)
        if target.is_symlink() or not target.is_file() or not target.resolve().is_relative_to(
            resolved_root
        ):
            raise QwenCalibrationError("technical correction bound file is unavailable")
        if file_sha256(target) != expected:
            raise QwenCalibrationError("technical correction implementation hash mismatch")


def load_and_verify_technical_correction(
    path: Path,
    *,
    candidate: dict[str, object],
    repository_root: Path,
) -> dict[str, object]:
    """Verify correction identity, chronology, candidate lineage, and source bytes."""

    payload = _load_object(path)
    unsigned = {key: value for key, value in payload.items() if key != "correction_sha256"}
    if payload.get("correction_sha256") != canonical_execution_sha256(unsigned):
        raise QwenCalibrationError("technical correction self-hash mismatch")
    fixed = {
        "schema_version": CORRECTION_SCHEMA_VERSION,
        "status": "prospective_transport_correction_frozen_before_any_model_generation",
        "protected_main_outcomes_opened": False,
        "main_registered_attempts_consumed": 0,
        "accepted_calibration_requests": 0,
        "model_generation_calls": 0,
        "scientific_contract_changed": False,
        "development_calibration_rerun_permitted_after_merge_green_ci": True,
    }
    if any(payload.get(key) != value for key, value in fixed.items()):
        raise QwenCalibrationError("technical correction fixed fields differ")
    if payload.get("qwen_candidate_sha256") != candidate.get("candidate_sha256"):
        raise QwenCalibrationError("technical correction candidate identity differs")

    predecessor = payload.get("superseded_candidate_bindings")
    contract = candidate.get("development_calibration_contract")
    if not isinstance(predecessor, dict) or not isinstance(contract, dict):
        raise QwenCalibrationError("technical correction predecessor bindings are malformed")
    if any(contract.get(key) != value for key, value in predecessor.items()):
        raise QwenCalibrationError("technical correction predecessor binding differs")
    superseded = payload.get("superseded_repo_bindings")
    if (
        not isinstance(superseded, dict)
        or set(superseded) != _REQUIRED_SUPERSEDED_BINDINGS
        or not all(_is_sha256(value) for value in superseded.values())
    ):
        raise QwenCalibrationError("technical correction superseded bindings differ")

    failure = payload.get("failure_history_summary")
    if not isinstance(failure, dict) or failure != {
        "failed_operator_invocations": 2,
        "server_starts": 1,
        "accepted_calibration_requests": 0,
        "model_generation_calls": 0,
        "synthetic_response_content_observed": False,
    }:
        raise QwenCalibrationError("technical correction failure history differs")
    identities = payload.get("private_failure_evidence_identities")
    if not isinstance(identities, dict) or not identities or not all(
        _is_sha256(value) for value in identities.values()
    ):
        raise QwenCalibrationError("technical correction failure identities are malformed")
    _verify_repo_bindings(repository_root, payload.get("implementation_bindings"))
    return payload
