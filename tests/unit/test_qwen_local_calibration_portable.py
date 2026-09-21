from __future__ import annotations

import copy
import json
import stat
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from aletheia_lab.evaluation import qwen_local_calibration as calibration
from aletheia_lab.project.identity import content_sha256

ROOT = Path(__file__).resolve().parents[2]


def test_local_artifact_verification_is_exercised_without_the_large_model(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_path = tmp_path / "portable-model.gguf"
    model_path.write_bytes(b"portable-qwen-fixture")

    checkout = tmp_path / "llama.cpp"
    binary = checkout / "build/bin/llama-server"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"portable-server")
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    (checkout / "build/CMakeCache.txt").write_text(
        "GGML_METAL:BOOL=ON\nCMAKE_BUILD_TYPE:STRING=Release\n",
        encoding="utf-8",
    )

    chat_template = "{% for message in messages %}{{ message.content }}{% endfor %}"
    tokenizer_config = tmp_path / "tokenizer_config.json"
    tokenizer_config.write_text(
        json.dumps({"chat_template": chat_template}),
        encoding="utf-8",
    )
    commit_sha = "1" * 40
    tag_sha = "2" * 40
    candidate: dict[str, object] = {
        "primary_artifact": {
            "byte_count": model_path.stat().st_size,
            "sha256": calibration.sha256_file(model_path),
        },
        "runner": {
            "release": "b-portable",
            "dereferenced_commit_sha": commit_sha,
            "annotated_tag_object_sha": tag_sha,
            "build_type": "Release",
        },
        "base_model": {
            "tokenizer_config_file_sha256": calibration.sha256_file(tokenizer_config),
            "source_chat_template_utf8_sha256": content_sha256(
                chat_template.encode("utf-8")
            ),
        },
        "host_envelope": {
            "cpu_cores": 8,
            "unified_memory_gib": 32,
        },
    }

    def fake_run_text(command: list[str], *, cwd: Path | None = None) -> str:
        del cwd
        if command[:2] == ["git", "rev-parse"]:
            return commit_sha if command[2] == "HEAD^{commit}" else tag_sha
        if command[:2] == ["git", "status"]:
            return ""
        if command == ["sysctl", "-n", "hw.ncpu"]:
            return "8"
        if command == ["sysctl", "-n", "hw.memsize"]:
            return str(64 * 2**30)
        if command == ["cmake", "--version"]:
            return "cmake portable"
        if command == ["c++", "--version"]:
            return "compiler portable"
        if command == ["sw_vers"]:
            return "macOS portable"
        if command == [str(binary), "--version"]:
            return "llama-server portable"
        raise AssertionError(f"unexpected command: {command}")

    def fake_subprocess_run(*args: Any, **kwargs: Any) -> SimpleNamespace:
        del args, kwargs
        metadata = {"metadata": {"tokenizer.chat_template": {"value": chat_template}}}
        return SimpleNamespace(stdout=json.dumps(metadata), stderr="", returncode=0)

    monkeypatch.setattr(calibration, "_run_text", fake_run_text)
    monkeypatch.setattr(calibration.subprocess, "run", fake_subprocess_run)

    observed = calibration.verify_local_artifacts(
        candidate=candidate,
        model_path=model_path,
        llama_checkout=checkout,
        source_tokenizer_config=tokenizer_config,
    )

    assert observed["model_sha256"] == calibration.sha256_file(model_path)
    assert observed["llama_cpp_commit_sha"] == commit_sha
    assert observed["llama_cpp_tag_object_sha"] == tag_sha
    assert observed["embedded_template_matches_source"] is True
    assert observed["ggml_metal_enabled"] is True
    assert observed["host_memory_bytes"] == 64 * 2**30


def test_loopback_json_transport_sends_direct_provider_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened: list[tuple[str, str, int, bytes | None]] = []

    class Response:
        def __enter__(self) -> Response:
            return self

        def __exit__(self, *args: object) -> None:
            del args

        @staticmethod
        def read() -> bytes:
            return b'{"status":"ok"}'

    class Opener:
        @staticmethod
        def open(request: object, *, timeout: int) -> Response:
            assert isinstance(request, calibration.urllib.request.Request)
            opened.append(
                (request.full_url, request.get_method(), timeout, request.data)
            )
            return Response()

    monkeypatch.setattr(calibration, "_loopback_opener", lambda: Opener())

    assert calibration._request_json("http://127.0.0.1:18080/health", None, 7) == {
        "status": "ok"
    }
    payload = {
        "messages": ({"role": "user", "content": "chào"},),
        "temperature": 0.7,
    }
    assert calibration._request_json(
        "http://127.0.0.1:18080/v1/chat/completions", payload, 11
    ) == {"status": "ok"}
    assert opened == [
        ("http://127.0.0.1:18080/health", "GET", 7, None),
        (
            "http://127.0.0.1:18080/v1/chat/completions",
            "POST",
            11,
            b'{"messages":[{"content":"ch\xc3\xa0o","role":"user"}],"temperature":0.7}',
        ),
    ]
    body = opened[1][3]
    assert body is not None
    decoded_body = json.loads(body.decode("utf-8"))
    assert decoded_body == {
        "messages": [{"role": "user", "content": "chào"}],
        "temperature": 0.7,
    }
    assert "payload" not in decoded_body
    assert "schema_version" not in decoded_body


def test_completion_and_six_cell_runner_record_only_auditable_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests = tuple(
        calibration.QwenCalibrationRequest(
            calibration_id=f"qcal-{index}",
            case_id=f"case-{index}",
            variant="B1" if index % 2 == 0 else "A3",
            messages=({"role": "user", "content": f"case {index}"},),
            visible_evidence_ids=frozenset({f"evidence-{index}"}),
            request_sha256=f"{index + 1:064x}",
        )
        for index in range(6)
    )
    raw = '{"portable":"response"}'
    monkeypatch.setattr(
        calibration,
        "_request_json",
        lambda url, payload, timeout: {
            "choices": [{"message": {"content": raw}}],
            "usage": {"total_tokens": 10},
            "timings": {"predicted_ms": 1},
        },
    )
    monkeypatch.setattr(calibration, "validate_main_provider_output", lambda *args, **kwargs: None)
    ticks = iter((10.0, 10.125))
    monkeypatch.setattr(calibration.time, "monotonic", lambda: next(ticks))
    generation_policy: dict[str, object] = {
        "maximum_output_tokens": 600,
        "temperature": 0.7,
        "top_p": 0.8,
        "top_k": 20,
        "min_p": 0.0,
        "presence_penalty": 1.5,
        "repetition_penalty": 1.0,
        "seed": 17,
        "chat_template_kwargs": {
            "enable_thinking": False,
            "preserve_thinking": False,
        },
    }

    record = calibration._completion_record(
        base_url="http://127.0.0.1:18080",
        request=requests[0],
        response_schema={"type": "object"},
        generation_policy=generation_policy,
        model_alias="qwen38-27b-q8_0-frozen",
        timeout=5,
        replicate=1,
    )

    assert record["raw_response_sha256"] == content_sha256(raw.encode("utf-8"))
    assert record["schema_and_semantic_conformance"] is True
    assert record["elapsed_seconds"] == 0.125
    assert not any(key in record for key in ("raw", "raw_response", "content", "output"))

    monkeypatch.setattr(
        calibration,
        "_completion_record",
        lambda *, request, replicate, **kwargs: {
            "calibration_id": request.calibration_id,
            "replicate": replicate,
        },
    )
    records = calibration.run_development_calibration(
        base_url="http://127.0.0.1:18080",
        requests=requests,
        response_schema={"type": "object"},
        generation_policy=generation_policy,
        model_alias="qwen38-27b-q8_0-frozen",
        timeout=5,
    )

    assert len(records) == 7
    assert [item["replicate"] for item in records] == [1, 1, 1, 1, 1, 1, 2]
    assert records[-1]["calibration_id"] == requests[0].calibration_id


def test_json_candidate_and_development_contracts_fail_closed(tmp_path: Path) -> None:
    with pytest.raises(calibration.QwenCalibrationError, match="unavailable"):
        calibration._load_json_object(tmp_path / "missing.json")

    invalid_json = tmp_path / "invalid.json"
    invalid_json.write_text("{", encoding="utf-8")
    with pytest.raises(calibration.QwenCalibrationError, match="invalid JSON"):
        calibration._load_json_object(invalid_json)

    json_array = tmp_path / "array.json"
    json_array.write_text("[]", encoding="utf-8")
    with pytest.raises(calibration.QwenCalibrationError, match="must be an object"):
        calibration._load_json_object(json_array)

    candidate_path = tmp_path / "candidate.json"
    candidate_path.write_text(
        json.dumps({"candidate_sha256": "0" * 64, "execution_authorized": False}),
        encoding="utf-8",
    )
    with pytest.raises(calibration.QwenCalibrationError, match="self-hash"):
        calibration.load_and_verify_candidate(candidate_path)

    candidate: dict[str, object] = {"execution_authorized": True}
    candidate["candidate_sha256"] = calibration.canonical_execution_sha256(candidate)
    candidate_path.write_text(json.dumps(candidate), encoding="utf-8")
    with pytest.raises(calibration.QwenCalibrationError, match="authorizes execution"):
        calibration.load_and_verify_candidate(candidate_path)

    plan = json.loads(
        (ROOT / "configs/evaluation/diagnosis_development_pilot_plan.json").read_text(
            encoding="utf-8"
        )
    )
    response_contract = json.loads(
        (ROOT / "configs/evaluation/diagnosis_main_response_contract.json").read_text(
            encoding="utf-8"
        )
    )

    def rejected(
        changed_plan: dict[str, object],
        changed_contract: dict[str, object],
        message: str,
    ) -> None:
        with pytest.raises(calibration.QwenCalibrationError, match=message):
            calibration.build_calibration_requests(changed_plan, changed_contract)

    changed = copy.deepcopy(plan)
    changed["mode"] = "main"
    rejected(changed, response_contract, "synthetic development")

    changed = copy.deepcopy(plan)
    changed["protected_outcomes_opened"] = True
    rejected(changed, response_contract, "protected outcomes")

    changed = copy.deepcopy(plan)
    changed["cases"] = []
    rejected(changed, response_contract, "differ from freeze")

    changed = copy.deepcopy(plan)
    cases = changed["cases"]
    assert isinstance(cases, list)
    cases[0] = "malformed"
    rejected(changed, response_contract, "case is malformed")

    changed = copy.deepcopy(plan)
    cases = changed["cases"]
    assert isinstance(cases, list) and isinstance(cases[0], dict)
    cases[0]["case_id"] = ""
    rejected(changed, response_contract, "identity is malformed")

    changed = copy.deepcopy(plan)
    cases = changed["cases"]
    assert isinstance(cases, list) and isinstance(cases[0], dict)
    evidence = cases[0]["evidence"]
    assert isinstance(evidence, list)
    evidence.append(copy.deepcopy(evidence[0]))
    rejected(changed, response_contract, "invalid or duplicated")

    changed = copy.deepcopy(plan)
    cases = changed["cases"]
    assert isinstance(cases, list) and isinstance(cases[0], dict)
    cases[0]["evidence"] = []
    rejected(changed, response_contract, "has no evidence")

    changed_contract = copy.deepcopy(response_contract)
    prompts = changed_contract["prompt_contracts"]
    assert isinstance(prompts, dict)
    prompts["B1"] = ""
    rejected(plan, changed_contract, "missing prompt contract")

    with pytest.raises(calibration.QwenCalibrationError, match="evidence is malformed"):
        calibration._render_evidence({"evidence": ["bad"]}, structured=True)
    with pytest.raises(calibration.QwenCalibrationError, match="blank field"):
        calibration._render_evidence(
            {
                "evidence": [
                    {
                        "evidence_id": "",
                        "kind": "metric",
                        "title": "title",
                        "content": "content",
                    }
                ]
            },
            structured=True,
        )


def test_process_helpers_and_server_guards_are_portable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert calibration._run_text([sys.executable, "-c", "print('portable')"]) == "portable"
    with pytest.raises(calibration.QwenCalibrationError, match="local command failed"):
        calibration._run_text(["/definitely/missing/portable-command"])

    candidate = calibration.load_and_verify_candidate(
        ROOT / "configs/evaluation/diagnosis_qwen_local_sensitivity_candidate_v2.json"
    )
    server_flags = calibration.frozen_server_flags(candidate, 18080)
    assert "--reasoning=off" in server_flags
    assert "--parallel=1" in server_flags
    assert "--presence-penalty=1.5" in server_flags
    command = calibration.server_command(
        Path("/tmp/llama-server"), Path("/tmp/model.gguf"), server_flags
    )
    assert command[:4] == [
        "/tmp/llama-server",
        "--model",
        "/tmp/model.gguf",
        "--host",
    ]
    assert command[command.index("--reasoning") + 1] == "off"
    with pytest.raises(calibration.QwenCalibrationError, match="port"):
        calibration.frozen_server_flags(candidate, 0)
    with pytest.raises(calibration.QwenCalibrationError, match="runtime policy"):
        calibration.frozen_server_flags({}, 18080)
    non_loopback = copy.deepcopy(candidate)
    runner = non_loopback["runner"]
    assert isinstance(runner, dict)
    runner["server_bind"] = "0.0.0.0"
    with pytest.raises(calibration.QwenCalibrationError, match="loopback server bind"):
        calibration.frozen_server_flags(non_loopback, 18080)

    class Process:
        def __init__(self, return_code: int | None) -> None:
            self.return_code = return_code

        def poll(self) -> int | None:
            return self.return_code

    monkeypatch.setattr(calibration, "_request_json", lambda *args, **kwargs: {"status": "ok"})
    calibration.wait_until_healthy(
        "http://127.0.0.1:18080",
        Process(None),  # type: ignore[arg-type]
        1,
    )

    with pytest.raises(calibration.QwenCalibrationError, match="exited"):
        calibration.wait_until_healthy(
            "http://127.0.0.1:18080",
            Process(1),  # type: ignore[arg-type]
            1,
        )

    ticks = iter((0.0, 0.1, 1.0))
    monkeypatch.setattr(calibration.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(calibration.time, "sleep", lambda _: None)

    def unavailable(*args: object, **kwargs: object) -> dict[str, object]:
        del args, kwargs
        raise calibration.QwenCalibrationError("not ready")

    monkeypatch.setattr(calibration, "_request_json", unavailable)
    with pytest.raises(calibration.QwenCalibrationError, match="did not become healthy"):
        calibration.wait_until_healthy(
            "http://127.0.0.1:18080",
            Process(None),  # type: ignore[arg-type]
            1,
        )

    monkeypatch.setattr(calibration, "_run_text", lambda *args, **kwargs: "1024")
    assert calibration.sample_process_rss_kib(42) == 1024
    monkeypatch.setattr(calibration, "_run_text", lambda *args, **kwargs: "not-an-int")
    with pytest.raises(calibration.QwenCalibrationError, match="cannot parse"):
        calibration.sample_process_rss_kib(42)
    monkeypatch.setattr(calibration, "_run_text", lambda *args, **kwargs: "0")
    with pytest.raises(calibration.QwenCalibrationError, match="must be positive"):
        calibration.sample_process_rss_kib(42)


def _receipt_inputs() -> tuple[
    dict[str, object],
    dict[str, object],
    dict[str, object],
    dict[str, object],
    dict[str, object],
]:
    candidate = calibration.load_and_verify_candidate(
        ROOT / "configs/evaluation/diagnosis_qwen_local_sensitivity_candidate_v2.json"
    )
    correction = {
        "correction_sha256": "9" * 64,
    }
    plan = json.loads(
        (ROOT / "configs/evaluation/diagnosis_development_pilot_plan.json").read_text(
            encoding="utf-8"
        )
    )
    response_contract = json.loads(
        (ROOT / "configs/evaluation/diagnosis_main_response_contract.json").read_text(
            encoding="utf-8"
        )
    )
    requests = calibration.build_calibration_requests(plan, response_contract)
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
    assert all(isinstance(item, dict) for item in (primary, runner, base_model, host))
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
    receipt = calibration.build_calibration_receipt(
        candidate=candidate,
        technical_correction=correction,
        development_plan=plan,
        response_contract=response_contract,
        artifacts=artifacts,
        records=records,
        server_flags=calibration.frozen_server_flags(candidate, 18080),
    )
    return candidate, correction, plan, response_contract, receipt


def _resign(receipt: dict[str, object]) -> None:
    unsigned = {key: value for key, value in receipt.items() if key != "receipt_sha256"}
    receipt["receipt_sha256"] = calibration.canonical_execution_sha256(unsigned)


def test_completion_transport_and_receipt_tampering_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate, correction, plan, response_contract, receipt = _receipt_inputs()
    requests = calibration.build_calibration_requests(plan, response_contract)
    generation_policy = candidate["generation_policy"]
    assert isinstance(generation_policy, dict)
    model_alias = generation_policy["request_model_alias"]
    assert isinstance(model_alias, str)

    with pytest.raises(calibration.QwenCalibrationError, match="six cells"):
        calibration.run_development_calibration(
            base_url="http://127.0.0.1:18080",
            requests=requests[:-1],
            response_schema={"type": "object"},
            generation_policy=generation_policy,
            model_alias=model_alias,
            timeout=1,
        )

    def completion_with(response: object) -> None:
        monkeypatch.setattr(calibration, "_request_json", lambda *args, **kwargs: response)
        calibration._completion_record(
            base_url="http://127.0.0.1:18080",
            request=requests[0],
            response_schema={"type": "object"},
            generation_policy=generation_policy,
            model_alias=model_alias,
            timeout=1,
            replicate=1,
        )

    with pytest.raises(calibration.QwenCalibrationError, match="no assistant content"):
        completion_with({})
    with pytest.raises(calibration.QwenCalibrationError, match="content is not text"):
        completion_with({"choices": [{"message": {"content": 7}}]})

    monkeypatch.setattr(
        calibration,
        "validate_main_provider_output",
        lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("invalid")),
    )
    with pytest.raises(calibration.QwenCalibrationError, match="semantic calibration failed"):
        completion_with({"choices": [{"message": {"content": "{}"}}]})

    base_records = receipt["records"]
    assert isinstance(base_records, (list, tuple))
    with pytest.raises(calibration.QwenCalibrationError, match="seven conforming"):
        calibration.build_calibration_receipt(
            candidate=candidate,
            technical_correction=correction,
            development_plan=plan,
            response_contract=response_contract,
            artifacts={},
            records=tuple(base_records[:-1]),
            server_flags=(),
        )
    mismatched_records = copy.deepcopy(list(base_records))
    assert isinstance(mismatched_records[-1], dict)
    mismatched_records[-1]["request_sha256"] = "f" * 64
    with pytest.raises(calibration.QwenCalibrationError, match="repeatability pair"):
        calibration.build_calibration_receipt(
            candidate=candidate,
            technical_correction=correction,
            development_plan=plan,
            response_contract=response_contract,
            artifacts={},
            records=tuple(mismatched_records),
            server_flags=(),
        )

    def rejected(changed: dict[str, object], message: str, *, resign: bool = True) -> None:
        if resign:
            _resign(changed)
        with pytest.raises(calibration.QwenCalibrationError, match=message):
            calibration.validate_calibration_receipt(
                receipt=changed,
                candidate=candidate,
                technical_correction=correction,
                development_plan=plan,
                response_contract=response_contract,
            )

    changed = copy.deepcopy(receipt)
    changed["receipt_sha256"] = "0" * 64
    rejected(changed, "self-hash mismatch", resign=False)

    changed = copy.deepcopy(receipt)
    changed["status"] = "tampered"
    rejected(changed, "fixed contract fields")

    changed = copy.deepcopy(receipt)
    changed["records"] = []
    rejected(changed, "request count differs")

    changed = copy.deepcopy(receipt)
    records = list(changed["records"])
    records[0] = "malformed"
    changed["records"] = records
    rejected(changed, "record is malformed")

    changed = copy.deepcopy(receipt)
    records = list(changed["records"])
    assert isinstance(records[0], dict)
    records[0]["raw_response_sha256"] = "bad"
    changed["records"] = records
    rejected(changed, "response hash is malformed")

    changed = copy.deepcopy(receipt)
    records = list(changed["records"])
    assert isinstance(records[0], dict)
    records[0]["raw"] = "private-output"
    changed["records"] = records
    rejected(changed, "contains raw model output")

    changed = copy.deepcopy(receipt)
    changed["repeatability_pair"] = {}
    rejected(changed, "repeatability record differs")

    changed = copy.deepcopy(receipt)
    changed["server_flags"] = "not-a-sequence"
    rejected(changed, "server flags are malformed")

    changed = copy.deepcopy(receipt)
    changed["server_flags"] = []
    rejected(changed, "one loopback port")

    changed = copy.deepcopy(receipt)
    changed["server_flags"] = ["--port=not-an-int"]
    rejected(changed, "loopback port is malformed")

    changed = copy.deepcopy(receipt)
    flags = list(changed["server_flags"])
    flags.append("--unexpected")
    changed["server_flags"] = flags
    rejected(changed, "server flags differ")

    changed = copy.deepcopy(receipt)
    changed["artifact_verification"] = []
    rejected(changed, "artifact verification is malformed")

    changed = copy.deepcopy(receipt)
    artifacts = changed["artifact_verification"]
    assert isinstance(artifacts, dict)
    artifacts["model_sha256"] = "0" * 64
    rejected(changed, "artifact identity differs")

    changed = copy.deepcopy(receipt)
    artifacts = changed["artifact_verification"]
    assert isinstance(artifacts, dict)
    maximum = artifacts["maximum_observed_peak_memory_gib"]
    assert isinstance(maximum, (int, float))
    artifacts["observed_process_rss_peak_gib"] = maximum + 1
    rejected(changed, "memory observation exceeds")


def test_loopback_transport_rejects_io_failure_and_non_object_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingOpener:
        @staticmethod
        def open(*args: object, **kwargs: object) -> None:
            del args, kwargs
            raise OSError("offline")

    monkeypatch.setattr(calibration, "_loopback_opener", lambda: FailingOpener())
    with pytest.raises(calibration.QwenCalibrationError, match="loopback request failed"):
        calibration._request_json("http://127.0.0.1:18080/health", None, 1)

    class Response:
        def __enter__(self) -> Response:
            return self

        def __exit__(self, *args: object) -> None:
            del args

        @staticmethod
        def read() -> bytes:
            return b"[]"

    class ArrayOpener:
        @staticmethod
        def open(*args: object, **kwargs: object) -> Response:
            del args, kwargs
            return Response()

    monkeypatch.setattr(calibration, "_loopback_opener", lambda: ArrayOpener())
    with pytest.raises(calibration.QwenCalibrationError, match="must be a JSON object"):
        calibration._request_json("http://127.0.0.1:18080/health", None, 1)
