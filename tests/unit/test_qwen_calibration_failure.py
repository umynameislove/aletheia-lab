from __future__ import annotations

import json
from pathlib import Path

import pytest

from aletheia_lab.content_hashing import file_sha256
from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256
from aletheia_lab.evaluation.qwen_calibration_failure import (
    QwenCalibrationFailureError,
    audit_private_server_log,
    load_and_validate_failure_closeout,
    validate_failure_closeout,
)

ROOT = Path(__file__).resolve().parents[2]
CLOSEOUT = (
    ROOT
    / "configs"
    / "evaluation"
    / "diagnosis_qwen38_calibration_failure_closeout.json"
)


def _tracked_payload() -> dict[str, object]:
    payload = json.loads(CLOSEOUT.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def _reseal(payload: dict[str, object]) -> None:
    unsigned = {key: value for key, value in payload.items() if key != "closeout_sha256"}
    payload["closeout_sha256"] = canonical_execution_sha256(unsigned)


def test_tracked_failure_closeout_is_sanitized_and_fail_closed() -> None:
    payload = load_and_validate_failure_closeout(CLOSEOUT)
    report = validate_failure_closeout(payload)

    assert report["status"] == "pass"
    assert report["disposition"] == "operationally_infeasible"
    assert report["observed_server_tasks"] == 4
    assert report["planned_inference_calls"] == 7
    assert report["qwen_sensitivity_inference_calls"] == 0
    assert report["protected_main_outcomes_opened"] is False
    assert report["main_registered_attempts_consumed"] == 0

    serialized = json.dumps(payload, ensure_ascii=False)
    assert "/Users/" not in serialized
    assert "tranbao" not in serialized.casefold()
    assert '"raw_response":' not in serialized
    assert "assistant content" not in serialized.casefold()


def test_private_log_can_be_verified_without_copying_it_into_closeout(
    tmp_path: Path,
) -> None:
    log = tmp_path / "server.log"
    lines = [
        "model loaded",
        "listening on http://127.0.0.1:18080",
        *("processing task, is_child = 0" for _ in range(4)),
        *("stop processing: n_tokens = 1, truncated = 0" for _ in range(4)),
        "cleaning up before exit",
    ]
    log.write_text("\n".join(lines) + "\n", encoding="utf-8")
    payload = _tracked_payload()
    identities = payload["private_failure_evidence_content_identities"]
    counts = payload["private_failure_evidence_byte_counts"]
    assert isinstance(identities, dict)
    assert isinstance(counts, dict)
    identities["v2_server_log_byte_sha256"] = file_sha256(log)
    counts["v2_server_log"] = log.stat().st_size
    _reseal(payload)

    report = audit_private_server_log(payload, log)

    assert report["status"] == "pass"
    assert report["processing_task_count"] == 4
    assert report["completed_untruncated_task_count"] == 4
    assert report["raw_model_response_content_emitted"] is False


def test_failure_closeout_rejects_replay_or_disposition_relaxation() -> None:
    for key, value in (
        ("rerun_permitted_under_current_registration", True),
        ("qwen_sensitivity_executed", True),
        ("pool_with_primary_permitted", True),
        ("calibration_observed_server_tasks", 7),
    ):
        payload = _tracked_payload()
        payload[key] = value
        _reseal(payload)
        with pytest.raises(QwenCalibrationFailureError, match="fixed fields differ"):
            validate_failure_closeout(payload)


def test_failure_closeout_rejects_changed_private_evidence_identity() -> None:
    payload = _tracked_payload()
    identities = payload["private_failure_evidence_content_identities"]
    assert isinstance(identities, dict)
    identities["v2_server_log_byte_sha256"] = "0" * 64
    _reseal(payload)

    with pytest.raises(QwenCalibrationFailureError, match="identities are malformed"):
        validate_failure_closeout(payload)


def test_failure_closeout_rejects_changed_scientific_disposition() -> None:
    payload = _tracked_payload()
    disposition = payload["scientific_disposition"]
    assert isinstance(disposition, dict)
    disposition["cross_model_claim_permitted"] = True
    _reseal(payload)

    with pytest.raises(QwenCalibrationFailureError, match="disposition differs"):
        validate_failure_closeout(payload)
