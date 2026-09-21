from __future__ import annotations

import hashlib
import json
import urllib.request
from pathlib import Path

import pytest

from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256
from aletheia_lab.evaluation.qwen_local_calibration import (
    QwenCalibrationError,
    _loopback_opener,
    _RejectRedirects,
    _request_json,
    build_calibration_receipt,
    build_calibration_requests,
    frozen_server_flags,
    load_and_verify_candidate,
    require_loopback_base_url,
    sha256_file,
    validate_calibration_receipt,
)

ROOT = Path(__file__).resolve().parents[2]


def test_candidate_and_synthetic_calibration_matrix_are_exact() -> None:
    candidate = load_and_verify_candidate(
        ROOT / "configs/evaluation/diagnosis_qwen_local_sensitivity_candidate_v2.json"
    )
    plan = json.loads(
        (ROOT / "configs/evaluation/diagnosis_development_pilot_plan.json").read_text()
    )
    contract = json.loads(
        (ROOT / "configs/evaluation/diagnosis_main_response_contract.json").read_text()
    )
    requests = build_calibration_requests(plan, contract)

    assert candidate["local_model_executed"] is False
    assert len(requests) == 6
    assert {(item.case_id, item.variant) for item in requests} == {
        (case["case_id"], variant)
        for case in plan["cases"]
        for variant in ("B1", "A3")
    }
    assert len({item.request_sha256 for item in requests}) == 6
    assert all(item.calibration_id == f"qcal-{item.request_sha256}" for item in requests)


@pytest.mark.parametrize(
    "url",
    (
        "https://127.0.0.1:18080",
        "http://0.0.0.0:18080",
        "http://example.com:18080",
        "http://localhost:18080",
        "http://user:secret@localhost:18080",
        "http://localhost:18080/v1",
    ),
)
def test_only_plain_loopback_http_is_accepted(url: str) -> None:
    with pytest.raises(QwenCalibrationError, match="loopback"):
        require_loopback_base_url(url)
    assert require_loopback_base_url("http://127.0.0.1:18080/") == (
        "http://127.0.0.1:18080"
    )


def test_loopback_request_rejects_non_loopback_url_before_io() -> None:
    with pytest.raises(QwenCalibrationError, match="escaped"):
        _request_json("http://example.com/health", None, 1)


def test_redirect_handler_refuses_every_redirect_target() -> None:
    handler = _RejectRedirects()
    request = urllib.request.Request("http://127.0.0.1:18080/health")
    assert (
        handler.redirect_request(
            request,
            None,
            302,
            "Found",
            {},
            "http://example.com/collect",
        )
        is None
    )


def test_loopback_opener_disables_environment_proxy_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        urllib.request,
        "getproxies",
        lambda: {"http": "http://proxy.example:8080"},
    )
    opener = _loopback_opener()
    assert not any(
        isinstance(handler, urllib.request.ProxyHandler)
        for handler in opener.handlers
    )


def test_streaming_file_hash_does_not_modify_input(tmp_path: Path) -> None:
    target = tmp_path / "model.gguf"
    target.write_bytes(b"test-model-bytes")
    assert sha256_file(target) == hashlib.sha256(b"test-model-bytes").hexdigest()
    assert target.read_bytes() == b"test-model-bytes"


def test_receipt_is_self_hashed_and_discloses_repeatability_without_claiming_determinism() -> None:
    candidate = {
        "candidate_sha256": "a" * 64,
    }
    plan = {"plan_sha256": "b" * 64}
    contract = {"contract_sha256": "c" * 64}
    records = tuple(
        {
            "request_sha256": "d" * 64 if index in {0, 6} else f"{index:064x}",
            "raw_response_sha256": "e" * 64 if index in {0, 6} else f"{index + 7:064x}",
            "schema_and_semantic_conformance": True,
        }
        for index in range(7)
    )
    receipt = build_calibration_receipt(
        candidate=candidate,
        development_plan=plan,
        response_contract=contract,
        artifacts={"model_sha256": "f" * 64},
        records=records,
        server_flags=("--host=127.0.0.1",),
    )
    declared = receipt.pop("receipt_sha256")

    assert canonical_execution_sha256(receipt) == declared
    assert receipt["scientific_quality_selection_performed"] is False
    assert receipt["repeatability_pair"] == {
        "request_sha256": "d" * 64,
        "byte_identical": True,
        "determinism_claimed": False,
    }


def _valid_receipt_inputs() -> tuple[
    dict[str, object], dict[str, object], dict[str, object], dict[str, object]
]:
    candidate = load_and_verify_candidate(
        ROOT / "configs/evaluation/diagnosis_qwen_local_sensitivity_candidate_v2.json"
    )
    plan = json.loads(
        (ROOT / "configs/evaluation/diagnosis_development_pilot_plan.json").read_text()
    )
    contract = json.loads(
        (ROOT / "configs/evaluation/diagnosis_main_response_contract.json").read_text()
    )
    requests = build_calibration_requests(plan, contract)
    records = tuple(
        {
            "calibration_id": request.calibration_id,
            "case_id": request.case_id,
            "variant": request.variant,
            "replicate": 2 if index == 6 else 1,
            "request_sha256": request.request_sha256,
            "raw_response_sha256": f"{index + 1:064x}",
            "schema_and_semantic_conformance": True,
        }
        for index, request in enumerate((*requests, requests[0]))
    )
    primary = candidate["primary_artifact"]
    runner = candidate["runner"]
    base_model = candidate["base_model"]
    host = candidate["host_envelope"]
    artifacts = {
        "model_byte_count": primary["byte_count"],
        "model_sha256": primary["sha256"],
        "llama_cpp_commit_sha": runner["dereferenced_commit_sha"],
        "llama_cpp_tag_object_sha": runner["annotated_tag_object_sha"],
        "cmake_build_type": runner["build_type"],
        "ggml_metal_enabled": True,
        "source_tokenizer_config_file_sha256": base_model[
            "tokenizer_config_file_sha256"
        ],
        "source_chat_template_utf8_sha256": base_model[
            "source_chat_template_utf8_sha256"
        ],
        "embedded_chat_template_utf8_sha256": base_model[
            "source_chat_template_utf8_sha256"
        ],
        "embedded_template_matches_source": True,
        "observed_process_rss_peak_gib": 30.0,
        "maximum_observed_peak_memory_gib": host[
            "maximum_observed_peak_memory_gib"
        ],
    }
    receipt = build_calibration_receipt(
        candidate=candidate,
        development_plan=plan,
        response_contract=contract,
        artifacts=artifacts,
        records=records,
        server_flags=frozen_server_flags(candidate, 18080),
    )
    return candidate, plan, contract, receipt


def test_calibration_receipt_validator_accepts_exact_frozen_sequence() -> None:
    candidate, plan, contract, receipt = _valid_receipt_inputs()
    report = validate_calibration_receipt(
        receipt=receipt,
        candidate=candidate,
        development_plan=plan,
        response_contract=contract,
    )
    assert report["status"] == "pass"
    assert report["receipt_sha256"] == receipt["receipt_sha256"]
    assert report["raw_outputs_retained_in_receipt"] is False


def test_calibration_receipt_validator_rejects_resigned_request_substitution() -> None:
    candidate, plan, contract, receipt = _valid_receipt_inputs()
    records = receipt["records"]
    assert isinstance(records, (list, tuple))
    records[0]["request_sha256"] = "0" * 64
    unsigned = {key: value for key, value in receipt.items() if key != "receipt_sha256"}
    receipt["receipt_sha256"] = canonical_execution_sha256(unsigned)

    with pytest.raises(QwenCalibrationError, match="identity differs"):
        validate_calibration_receipt(
            receipt=receipt,
            candidate=candidate,
            development_plan=plan,
            response_contract=contract,
        )
