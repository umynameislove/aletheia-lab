"""Outcome-blind operational calibration for the pinned local Qwen path."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Literal
from urllib.parse import urlparse

from aletheia_lab.content_hashing import file_sha256
from aletheia_lab.diagnosis.main_response import (
    DiagnosisMainResponseError,
    validate_main_provider_output,
)
from aletheia_lab.evaluation.execution_contracts import (
    canonical_execution_json,
    canonical_execution_sha256,
)
from aletheia_lab.evaluation.qwen_calibration_transport import provider_wire_json
from aletheia_lab.project.identity import content_sha256

CALIBRATION_SCHEMA_VERSION: Final = "diagnosis-qwen-local-calibration/v2"
_CALIBRATION_VARIANTS: Final[tuple[Literal["B1", "A3"], ...]] = ("B1", "A3")


class QwenCalibrationError(ValueError):
    """Raised when local calibration would violate or fail the frozen contract."""


@dataclass(frozen=True)
class QwenCalibrationRequest:
    calibration_id: str
    case_id: str
    variant: Literal["B1", "A3"]
    messages: tuple[dict[str, str], ...]
    visible_evidence_ids: frozenset[str]
    request_sha256: str


sha256_file = file_sha256


def _load_json_object(path: Path) -> dict[str, object]:
    if path.is_symlink() or not path.is_file():
        raise QwenCalibrationError(f"required regular file is unavailable: {path.name}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise QwenCalibrationError(f"invalid JSON artifact: {path.name}") from exc
    if not isinstance(payload, dict):
        raise QwenCalibrationError(f"JSON artifact must be an object: {path.name}")
    return payload


def load_and_verify_candidate(path: Path) -> dict[str, object]:
    payload = _load_json_object(path)
    declared = payload.get("candidate_sha256")
    unsigned = {key: value for key, value in payload.items() if key != "candidate_sha256"}
    if declared != canonical_execution_sha256(unsigned):
        raise QwenCalibrationError("Qwen candidate self-hash mismatch")
    if payload.get("execution_authorized") is not False:
        raise QwenCalibrationError("Qwen candidate unexpectedly authorizes execution")
    return payload


def _render_evidence(case: dict[str, object], *, structured: bool) -> str:
    evidence = case.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        raise QwenCalibrationError("development case has no evidence")
    records: list[dict[str, str]] = []
    for item in evidence:
        if not isinstance(item, dict):
            raise QwenCalibrationError("development evidence is malformed")
        record = {
            "evidence_id": str(item.get("evidence_id", "")),
            "kind": str(item.get("kind", "")),
            "title": str(item.get("title", "")),
            "content": str(item.get("content", "")),
        }
        if not all(record.values()):
            raise QwenCalibrationError("development evidence has a blank field")
        records.append(record)
    if structured:
        return canonical_execution_json(records)
    return " | ".join(f"{item['title']}: {item['content']}" for item in records)


def build_calibration_requests(
    development_plan: dict[str, object],
    response_contract: dict[str, object],
) -> tuple[QwenCalibrationRequest, ...]:
    """Build the six frozen synthetic B1/A3 cells without outcome labels."""

    if development_plan.get("mode") != "development_synthetic":
        raise QwenCalibrationError("Qwen calibration accepts synthetic development cases only")
    if development_plan.get("protected_outcomes_opened") is not False:
        raise QwenCalibrationError("development plan reports protected outcomes opened")
    cases = development_plan.get("cases")
    prompts = response_contract.get("prompt_contracts")
    if not isinstance(cases, list) or len(cases) != 3 or not isinstance(prompts, dict):
        raise QwenCalibrationError("development cases or prompt contracts differ from freeze")

    requests: list[QwenCalibrationRequest] = []
    for case in cases:
        if not isinstance(case, dict):
            raise QwenCalibrationError("development case is malformed")
        case_id = str(case.get("case_id", ""))
        evidence = case.get("evidence")
        if not case_id or not isinstance(evidence, list):
            raise QwenCalibrationError("development case identity is malformed")
        visible_ids = frozenset(
            str(item.get("evidence_id", "")) for item in evidence if isinstance(item, dict)
        )
        if len(visible_ids) != len(evidence) or "" in visible_ids:
            raise QwenCalibrationError("development evidence IDs are invalid or duplicated")
        for variant in _CALIBRATION_VARIANTS:
            prompt = prompts.get(variant)
            if not isinstance(prompt, str) or not prompt:
                raise QwenCalibrationError(f"missing prompt contract for {variant}")
            messages = (
                {
                    "role": "system",
                    "content": (
                        f"{prompt} Return only one JSON object matching the supplied schema."
                    ),
                },
                {
                    "role": "user",
                    "content": _render_evidence(case, structured=variant == "A3"),
                },
            )
            identity = {
                "schema_version": "diagnosis-qwen-calibration-request/v1",
                "case_id": case_id,
                "variant": variant,
                "messages": messages,
                "visible_evidence_ids": tuple(sorted(visible_ids)),
            }
            digest = canonical_execution_sha256(identity)
            requests.append(
                QwenCalibrationRequest(
                    calibration_id=f"qcal-{digest}",
                    case_id=case_id,
                    variant=variant,
                    messages=messages,
                    visible_evidence_ids=visible_ids,
                    request_sha256=digest,
                )
            )
    return tuple(requests)


def require_loopback_base_url(base_url: str) -> str:
    parsed = urlparse(base_url)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "::1"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise QwenCalibrationError("Qwen calibration endpoint must be loopback HTTP")
    return base_url.rstrip("/")


def frozen_server_flags(candidate: dict[str, object], port: int) -> tuple[str, ...]:
    """Render the auditable llama-server envelope from the frozen candidate."""

    if not 1 <= port <= 65535:
        raise QwenCalibrationError("loopback port must be between 1 and 65535")
    runner = candidate.get("runner")
    policy = candidate.get("generation_policy")
    if not isinstance(runner, dict) or not isinstance(policy, dict):
        raise QwenCalibrationError("candidate runtime policy is malformed")
    if runner.get("server_bind") != "127.0.0.1":
        raise QwenCalibrationError("candidate does not freeze a loopback server bind")
    return (
        "--host=127.0.0.1",
        f"--port={port}",
        f"--ctx-size={policy['server_context_tokens']}",
        "--parallel=1",
        f"--n-predict={policy['maximum_output_tokens']}",
        f"--n-gpu-layers={runner['n_gpu_layers']}",
        "--jinja",
        "--no-context-shift",
        f"--reasoning={policy['reasoning_mode']}",
        f"--temp={policy['temperature']}",
        f"--top-p={policy['top_p']}",
        f"--top-k={policy['top_k']}",
        f"--min-p={policy['min_p']:g}",
        f"--presence-penalty={policy['presence_penalty']}",
        f"--repeat-penalty={policy['repetition_penalty']}",
        f"--seed={policy['seed']}",
    )


def server_command(
    binary: Path,
    model_path: Path,
    server_flags: tuple[str, ...],
) -> list[str]:
    """Expand normalized receipt flags into the exact local server command."""

    command = [str(binary), "--model", str(model_path)]
    for flag in server_flags:
        if "=" not in flag:
            command.append(flag)
            continue
        option, value = flag.split("=", 1)
        command.extend((option, value))
    return command


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    """Stop redirects before a loopback request can reach another origin."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        del req, fp, code, msg, headers, newurl
        return None


def _loopback_opener() -> urllib.request.OpenerDirector:
    """Build an opener with redirects and environment-configured proxies disabled."""

    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _RejectRedirects,
    )


def _run_text(command: list[str], *, cwd: Path | None = None) -> str:
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise QwenCalibrationError(f"local command failed: {command[0]}") from exc
    return (completed.stdout + completed.stderr).strip()


def _candidate_artifact_sections(
    candidate: dict[str, object],
) -> tuple[dict[str, object], dict[str, object], dict[str, object], dict[str, object]]:
    primary = candidate.get("primary_artifact")
    runner = candidate.get("runner")
    base_model = candidate.get("base_model")
    host = candidate.get("host_envelope")
    if not all(isinstance(item, dict) for item in (primary, runner, base_model, host)):
        raise QwenCalibrationError("candidate artifact bindings are malformed")
    assert isinstance(primary, dict)
    assert isinstance(runner, dict)
    assert isinstance(base_model, dict)
    assert isinstance(host, dict)
    return primary, runner, base_model, host


def _numeric_contract_value(payload: dict[str, object], key: str) -> int | float:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise QwenCalibrationError("candidate numeric artifact binding is malformed")
    return value


def _verify_model_artifact(model_path: Path, primary: dict[str, object]) -> str:
    if model_path.is_symlink() or not model_path.is_file():
        raise QwenCalibrationError("Q8 model must be a regular non-symlink file")
    if model_path.stat().st_size != primary.get("byte_count"):
        raise QwenCalibrationError("Q8 byte count mismatch")
    observed_model_sha = sha256_file(model_path)
    if observed_model_sha != primary.get("sha256"):
        raise QwenCalibrationError("Q8 SHA-256 mismatch")
    return observed_model_sha


def _verify_llama_checkout(
    llama_checkout: Path,
    runner: dict[str, object],
) -> tuple[str, str, Path]:
    observed_commit = _run_text(
        ["git", "rev-parse", "HEAD^{commit}"], cwd=llama_checkout
    ).splitlines()[-1]
    observed_tag = _run_text(
        ["git", "rev-parse", f"{runner.get('release')}^{{tag}}"], cwd=llama_checkout
    ).splitlines()[-1]
    if observed_commit != runner.get("dereferenced_commit_sha"):
        raise QwenCalibrationError("llama.cpp commit mismatch")
    if observed_tag != runner.get("annotated_tag_object_sha"):
        raise QwenCalibrationError("llama.cpp annotated tag mismatch")
    binary = llama_checkout / "build/bin/llama-server"
    if binary.is_symlink() or not binary.is_file() or not os.access(binary, os.X_OK):
        raise QwenCalibrationError("pinned llama-server binary is unavailable")
    source_diff = _run_text(
        ["git", "status", "--porcelain", "--untracked-files=no"], cwd=llama_checkout
    )
    if source_diff:
        raise QwenCalibrationError("llama.cpp tracked source checkout is dirty")
    return observed_commit, observed_tag, binary


def _verify_host_and_build(
    llama_checkout: Path,
    host: dict[str, object],
) -> dict[str, object]:
    cpu_count = int(_run_text(["sysctl", "-n", "hw.ncpu"]))
    memory_bytes = int(_run_text(["sysctl", "-n", "hw.memsize"]))
    if cpu_count != host.get("cpu_cores"):
        raise QwenCalibrationError("host CPU-core count differs from frozen envelope")
    if memory_bytes < int(_numeric_contract_value(host, "unified_memory_gib")) * 2**30:
        raise QwenCalibrationError("host unified memory is below the frozen envelope")
    cmake_cache = llama_checkout / "build/CMakeCache.txt"
    if cmake_cache.is_symlink() or not cmake_cache.is_file():
        raise QwenCalibrationError("llama.cpp CMake cache is unavailable")
    cache_text = cmake_cache.read_text(encoding="utf-8")
    if "GGML_METAL:BOOL=ON" not in cache_text:
        raise QwenCalibrationError("llama.cpp build does not declare GGML_METAL=ON")
    if "CMAKE_BUILD_TYPE:STRING=Release" not in cache_text:
        raise QwenCalibrationError("llama.cpp build is not Release")
    return {
        "cmake_version_output": _run_text(["cmake", "--version"]),
        "compiler_version_output": _run_text(["c++", "--version"]),
        "operating_system_version_output": _run_text(["sw_vers"]),
        "host_cpu_count": cpu_count,
        "host_memory_bytes": memory_bytes,
        "cmake_cache_sha256": content_sha256(cache_text.encode("utf-8")),
        "cmake_build_type": "Release",
        "ggml_metal_enabled": True,
    }


def _embedded_chat_template(model_path: Path, llama_checkout: Path) -> tuple[str, str]:
    dump_script = llama_checkout / "gguf-py/gguf/scripts/gguf_dump.py"
    try:
        metadata_process = subprocess.run(
            [
                sys.executable,
                str(dump_script),
                str(model_path),
                "--json",
                "--no-tensors",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise QwenCalibrationError("local command failed: gguf_dump.py") from exc
    metadata_text = metadata_process.stdout.strip()
    try:
        metadata = json.loads(metadata_text)
        embedded_template = metadata["metadata"]["tokenizer.chat_template"]["value"]
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise QwenCalibrationError("cannot extract embedded GGUF chat template") from exc
    if not isinstance(embedded_template, str):
        raise QwenCalibrationError("embedded GGUF chat template is not a string")
    return embedded_template, metadata_text


def _verify_chat_templates(
    *,
    model_path: Path,
    llama_checkout: Path,
    source_tokenizer_config: Path,
    base_model: dict[str, object],
) -> dict[str, object]:
    tokenizer_config = _load_json_object(source_tokenizer_config)
    observed_tokenizer_config_sha = sha256_file(source_tokenizer_config)
    if observed_tokenizer_config_sha != base_model.get("tokenizer_config_file_sha256"):
        raise QwenCalibrationError("source tokenizer-config SHA-256 mismatch")
    source_template = tokenizer_config.get("chat_template")
    if not isinstance(source_template, str) or not source_template:
        raise QwenCalibrationError("source tokenizer config has no string chat template")
    source_template_sha = content_sha256(source_template.encode("utf-8"))
    if source_template_sha != base_model.get("source_chat_template_utf8_sha256"):
        raise QwenCalibrationError("source chat-template SHA-256 mismatch")
    embedded_template, metadata_text = _embedded_chat_template(model_path, llama_checkout)
    embedded_template_sha = content_sha256(embedded_template.encode("utf-8"))
    if embedded_template_sha != source_template_sha:
        raise QwenCalibrationError("embedded GGUF chat template differs from pinned source")
    return {
        "source_chat_template_utf8_sha256": source_template_sha,
        "source_tokenizer_config_file_sha256": observed_tokenizer_config_sha,
        "embedded_chat_template_utf8_sha256": embedded_template_sha,
        "embedded_template_matches_source": True,
        "metadata_dump_sha256": content_sha256(metadata_text.encode("utf-8")),
    }


def verify_local_artifacts(
    *,
    candidate: dict[str, object],
    model_path: Path,
    llama_checkout: Path,
    source_tokenizer_config: Path,
) -> dict[str, object]:
    primary, runner, base_model, host = _candidate_artifact_sections(candidate)
    observed_model_sha = _verify_model_artifact(model_path, primary)
    observed_commit, observed_tag, binary = _verify_llama_checkout(llama_checkout, runner)
    host_and_build = _verify_host_and_build(llama_checkout, host)
    templates = _verify_chat_templates(
        model_path=model_path,
        llama_checkout=llama_checkout,
        source_tokenizer_config=source_tokenizer_config,
        base_model=base_model,
    )

    return {
        "model_byte_count": model_path.stat().st_size,
        "model_sha256": observed_model_sha,
        "llama_cpp_commit_sha": observed_commit,
        "llama_cpp_tag_object_sha": observed_tag,
        "llama_server_sha256": sha256_file(binary),
        "llama_server_version_output": _run_text([str(binary), "--version"]),
        **host_and_build,
        **templates,
    }


def _request_json(url: str, payload: dict[str, object] | None, timeout: int) -> dict[str, object]:
    parsed = urlparse(url)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "::1"}
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise QwenCalibrationError("loopback request URL escaped the local endpoint")
    body = None if payload is None else provider_wire_json(payload)
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="GET" if body is None else "POST",
    )
    opener = _loopback_opener()
    try:
        with opener.open(request, timeout=timeout) as response:  # noqa: S310
            decoded = json.loads(response.read().decode())
    except (OSError, UnicodeError, json.JSONDecodeError, urllib.error.URLError) as exc:
        raise QwenCalibrationError(f"loopback request failed: {urlparse(url).path}") from exc
    if not isinstance(decoded, dict):
        raise QwenCalibrationError("loopback response must be a JSON object")
    return decoded


def wait_until_healthy(base_url: str, process: subprocess.Popen[str], timeout: int) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise QwenCalibrationError("llama-server exited while loading the model")
        try:
            if _request_json(f"{base_url}/health", None, 5).get("status") == "ok":
                return
        except QwenCalibrationError:
            pass
        time.sleep(2)
    raise QwenCalibrationError("llama-server did not become healthy before timeout")


def sample_process_rss_kib(process_id: int) -> int:
    """Read the server process RSS as an auditable host-memory proxy."""

    output = _run_text(["ps", "-o", "rss=", "-p", str(process_id)])
    try:
        value = int(output.strip())
    except ValueError as exc:
        raise QwenCalibrationError("cannot parse llama-server process RSS") from exc
    if value <= 0:
        raise QwenCalibrationError("llama-server process RSS must be positive")
    return value


def _completion_record(
    *,
    base_url: str,
    request: QwenCalibrationRequest,
    response_schema: dict[str, object],
    generation_policy: dict[str, object],
    model_alias: str,
    timeout: int,
    replicate: int,
) -> dict[str, object]:
    payload = {
        "model": model_alias,
        "messages": request.messages,
        "response_format": {"type": "json_object", "schema": response_schema},
        "max_tokens": generation_policy["maximum_output_tokens"],
        "temperature": generation_policy["temperature"],
        "top_p": generation_policy["top_p"],
        "top_k": generation_policy["top_k"],
        "min_p": generation_policy["min_p"],
        "presence_penalty": generation_policy["presence_penalty"],
        "repeat_penalty": generation_policy["repetition_penalty"],
        "seed": generation_policy["seed"],
        "chat_template_kwargs": generation_policy["chat_template_kwargs"],
        "stream": False,
    }
    started = time.monotonic()
    response = _request_json(f"{base_url}/v1/chat/completions", payload, timeout)
    elapsed = time.monotonic() - started
    try:
        raw = response["choices"][0]["message"]["content"]  # type: ignore[index]
    except (KeyError, IndexError, TypeError) as exc:
        raise QwenCalibrationError("llama-server returned no assistant content") from exc
    if not isinstance(raw, str):
        raise QwenCalibrationError("llama-server assistant content is not text")
    try:
        validate_main_provider_output(
            raw,
            variant=request.variant,
            visible_evidence_ids=set(request.visible_evidence_ids),
        )
    except (DiagnosisMainResponseError, ValueError) as exc:
        raise QwenCalibrationError(
            f"schema or semantic calibration failed for {request.calibration_id}"
        ) from exc
    return {
        "calibration_id": request.calibration_id,
        "case_id": request.case_id,
        "variant": request.variant,
        "replicate": replicate,
        "request_sha256": request.request_sha256,
        "raw_response_sha256": content_sha256(raw.encode("utf-8")),
        "schema_and_semantic_conformance": True,
        "elapsed_seconds": round(elapsed, 3),
        "usage": response.get("usage"),
        "timings": response.get("timings"),
    }


def run_development_calibration(
    *,
    base_url: str,
    requests: tuple[QwenCalibrationRequest, ...],
    response_schema: dict[str, object],
    generation_policy: dict[str, object],
    model_alias: str,
    timeout: int,
) -> tuple[dict[str, object], ...]:
    if len(requests) != 6:
        raise QwenCalibrationError("Qwen development calibration must contain six cells")
    records = [
        _completion_record(
            base_url=base_url,
            request=request,
            response_schema=response_schema,
            generation_policy=generation_policy,
            model_alias=model_alias,
            timeout=timeout,
            replicate=1,
        )
        for request in requests
    ]
    records.append(
        _completion_record(
            base_url=base_url,
            request=requests[0],
            response_schema=response_schema,
            generation_policy=generation_policy,
            model_alias=model_alias,
            timeout=timeout,
            replicate=2,
        )
    )
    return tuple(records)


def build_calibration_receipt(
    *,
    candidate: dict[str, object],
    technical_correction: dict[str, object],
    development_plan: dict[str, object],
    response_contract: dict[str, object],
    artifacts: dict[str, object],
    records: tuple[dict[str, object], ...],
    server_flags: tuple[str, ...],
) -> dict[str, object]:
    if len(records) != 7 or not all(
        record.get("schema_and_semantic_conformance") is True for record in records
    ):
        raise QwenCalibrationError("calibration receipt requires seven conforming calls")
    first = records[0]
    repeat = records[-1]
    if first.get("request_sha256") != repeat.get("request_sha256"):
        raise QwenCalibrationError("repeatability pair does not share one request identity")
    payload: dict[str, object] = {
        "schema_version": CALIBRATION_SCHEMA_VERSION,
        "status": "development_operational_calibration_pass",
        "protected_main_outcomes_opened": False,
        "main_registered_attempts_consumed": 0,
        "scientific_quality_selection_performed": False,
        "local_loopback_only": True,
        "candidate_sha256": candidate["candidate_sha256"],
        "technical_correction_sha256": technical_correction["correction_sha256"],
        "development_plan_sha256": development_plan["plan_sha256"],
        "response_contract_sha256": response_contract["contract_sha256"],
        "artifact_verification": artifacts,
        "server_flags": server_flags,
        "calibration_cell_count": 6,
        "inference_call_count": 7,
        "repeatability_pair": {
            "request_sha256": first["request_sha256"],
            "byte_identical": first["raw_response_sha256"] == repeat["raw_response_sha256"],
            "determinism_claimed": False,
        },
        "records": records,
        "raw_outputs_retained_in_receipt": False,
    }
    return {**payload, "receipt_sha256": canonical_execution_sha256(payload)}


def _validate_receipt_contract_fields(
    receipt: dict[str, object],
    candidate: dict[str, object],
    technical_correction: dict[str, object],
    development_plan: dict[str, object],
    response_contract: dict[str, object],
) -> str:
    unsigned = {key: value for key, value in receipt.items() if key != "receipt_sha256"}
    receipt_sha = canonical_execution_sha256(unsigned)
    fixed_fields = {
        "schema_version": CALIBRATION_SCHEMA_VERSION,
        "status": "development_operational_calibration_pass",
        "protected_main_outcomes_opened": False,
        "main_registered_attempts_consumed": 0,
        "scientific_quality_selection_performed": False,
        "local_loopback_only": True,
        "candidate_sha256": candidate.get("candidate_sha256"),
        "technical_correction_sha256": technical_correction.get("correction_sha256"),
        "development_plan_sha256": development_plan.get("plan_sha256"),
        "response_contract_sha256": response_contract.get("contract_sha256"),
        "calibration_cell_count": 6,
        "inference_call_count": 7,
        "raw_outputs_retained_in_receipt": False,
    }
    if receipt.get("receipt_sha256") != receipt_sha:
        raise QwenCalibrationError("calibration receipt self-hash mismatch")
    if any(receipt.get(key) != value for key, value in fixed_fields.items()):
        raise QwenCalibrationError("calibration receipt fixed contract fields differ")
    return receipt_sha


def _validate_receipt_records(
    receipt: dict[str, object],
    expected_requests: tuple[QwenCalibrationRequest, ...],
) -> tuple[dict[str, object], ...]:
    expected_sequence = (*expected_requests, expected_requests[0])
    records = receipt.get("records")
    if not isinstance(records, (list, tuple)) or len(records) != len(expected_sequence):
        raise QwenCalibrationError("calibration receipt request count differs")
    for index, (record, expected) in enumerate(zip(records, expected_sequence, strict=True)):
        if not isinstance(record, dict):
            raise QwenCalibrationError("calibration receipt record is malformed")
        expected_identity = {
            "calibration_id": expected.calibration_id,
            "case_id": expected.case_id,
            "variant": expected.variant,
            "replicate": 2 if index == 6 else 1,
            "request_sha256": expected.request_sha256,
            "schema_and_semantic_conformance": True,
        }
        if any(record.get(key) != value for key, value in expected_identity.items()):
            raise QwenCalibrationError("calibration receipt request identity differs")
        if not _is_sha256_text(record.get("raw_response_sha256")):
            raise QwenCalibrationError("calibration response hash is malformed")
        if any(key in record for key in ("raw", "raw_response", "content", "output")):
            raise QwenCalibrationError("calibration receipt contains raw model output")
    return tuple(records)


def _validate_receipt_repeatability(
    receipt: dict[str, object],
    records: tuple[dict[str, object], ...],
    expected_requests: tuple[QwenCalibrationRequest, ...],
) -> None:
    first = records[0]
    repeat = records[-1]
    expected_repeatability = {
        "request_sha256": expected_requests[0].request_sha256,
        "byte_identical": first.get("raw_response_sha256") == repeat.get("raw_response_sha256"),
        "determinism_claimed": False,
    }
    if receipt.get("repeatability_pair") != expected_repeatability:
        raise QwenCalibrationError("calibration repeatability record differs")


def _validate_receipt_server_flags(
    receipt: dict[str, object], candidate: dict[str, object]
) -> None:
    flags = receipt.get("server_flags")
    if not isinstance(flags, (list, tuple)):
        raise QwenCalibrationError("calibration server flags are malformed")
    port_flags = [item for item in flags if isinstance(item, str) and item.startswith("--port=")]
    if len(port_flags) != 1:
        raise QwenCalibrationError("calibration receipt must contain one loopback port")
    try:
        port = int(port_flags[0].split("=", 1)[1])
    except ValueError as exc:
        raise QwenCalibrationError("calibration loopback port is malformed") from exc
    if tuple(flags) != frozen_server_flags(candidate, port):
        raise QwenCalibrationError("calibration server flags differ from freeze")


def _validate_receipt_artifacts(receipt: dict[str, object], candidate: dict[str, object]) -> None:
    primary, runner, base_model, host = _candidate_artifact_sections(candidate)
    artifacts = receipt.get("artifact_verification")
    if not isinstance(artifacts, dict):
        raise QwenCalibrationError("calibration artifact verification is malformed")
    required_artifact_values = {
        "model_byte_count": primary.get("byte_count"),
        "model_sha256": primary.get("sha256"),
        "llama_cpp_commit_sha": runner.get("dereferenced_commit_sha"),
        "llama_cpp_tag_object_sha": runner.get("annotated_tag_object_sha"),
        "cmake_build_type": runner.get("build_type"),
        "ggml_metal_enabled": True,
        "source_tokenizer_config_file_sha256": base_model.get("tokenizer_config_file_sha256"),
        "source_chat_template_utf8_sha256": base_model.get("source_chat_template_utf8_sha256"),
        "embedded_chat_template_utf8_sha256": base_model.get("source_chat_template_utf8_sha256"),
        "embedded_template_matches_source": True,
        "maximum_observed_peak_memory_gib": host.get("maximum_observed_peak_memory_gib"),
    }
    if any(artifacts.get(key) != value for key, value in required_artifact_values.items()):
        raise QwenCalibrationError("calibration artifact identity differs from freeze")
    observed_peak = artifacts.get("observed_process_rss_peak_gib")
    if (
        not isinstance(observed_peak, (int, float))
        or observed_peak < 0
        or observed_peak > float(_numeric_contract_value(host, "maximum_observed_peak_memory_gib"))
    ):
        raise QwenCalibrationError("calibration memory observation exceeds freeze")


def validate_calibration_receipt(
    *,
    receipt: dict[str, object],
    candidate: dict[str, object],
    technical_correction: dict[str, object],
    development_plan: dict[str, object],
    response_contract: dict[str, object],
) -> dict[str, object]:
    """Validate a calibration receipt without exposing response content."""

    receipt_sha = _validate_receipt_contract_fields(
        receipt, candidate, technical_correction, development_plan, response_contract
    )
    expected_requests = build_calibration_requests(development_plan, response_contract)
    records = _validate_receipt_records(receipt, expected_requests)
    _validate_receipt_repeatability(receipt, records, expected_requests)
    _validate_receipt_server_flags(receipt, candidate)
    _validate_receipt_artifacts(receipt, candidate)
    return {
        "schema_version": "diagnosis-qwen-local-calibration-audit/v1",
        "status": "pass",
        "receipt_sha256": receipt_sha,
        "candidate_sha256": candidate["candidate_sha256"],
        "technical_correction_sha256": technical_correction["correction_sha256"],
        "calibration_cell_count": 6,
        "inference_call_count": 7,
        "protected_main_outcomes_opened": False,
        "main_registered_attempts_consumed": 0,
        "raw_outputs_retained_in_receipt": False,
    }


def _is_sha256_text(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )
