"""Private native-cache development comparison; only execute may invoke a caller."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import tiktoken

from aletheia_lab.content_hashing import file_sha256
from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256 as sha256
from aletheia_lab.evaluation.native_cache_extraction import (
    PROMPT,
    RESPONSE_SCHEMA,
    assess_native_proposal,
    parser_facts,
    provider_payload,
    resolve_facts,
)
from aletheia_lab.evaluation.native_cache_source import CONTROLS, cached_value, producer_identity
from aletheia_lab.evaluation.source_evidence_admission import checked_json
from aletheia_lab.evaluation.warrant_development import DevelopmentCall, DevelopmentCaller
from aletheia_lab.evaluation.warrant_development_io import (
    DESTINATION,
    MAX_CALL_USD,
    MAX_INPUT_TOKENS,
    MAX_OUTPUT_TOKENS,
    MODEL,
    _private_dir,
    _write,
)
from aletheia_lab.evaluation.warrant_development_live import OpenAIDevelopmentCaller
from aletheia_lab.model_gateway.openai import _openai_response_format
from aletheia_lab.project.identity import content_sha256

COST_CEILING_USD = 0.25
CODE_FILES = (
    "evaluation/native_cache_source.py",
    "evaluation/native_cache_extraction.py",
    "evaluation/native_cache_experiment.py",
    "evaluation/source_evidence_extraction.py",
    "evaluation/source_evidence_admission.py",
    "evaluation/compositional_lineage.py",
    "evaluation/warrant_development.py",
    "evaluation/warrant_development_io.py",
    "evaluation/warrant_development_live.py",
    "model_gateway/openai.py",
    "model_gateway/schema.py",
    "model_gateway/runtime.py",
    "project/identity.py",
    "content_hashing.py",
    "filesystem.py",
)


def _read(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 1_000_000:
        raise ValueError("experiment input must be a bounded regular file")
    return checked_json(path.read_bytes())


def _source_files(directory: Path) -> dict[str, str]:
    paths = [directory / "source.json", directory / "frozen-a.pkl", directory / "frozen-b.pkl"]
    if any(
        path.is_symlink() or not path.is_file() or path.stat().st_size > 1_000_000 for path in paths
    ):
        raise ValueError("native source snapshot is absent or unsafe")
    return {path.name: file_sha256(path) for path in paths}


def _code_identity() -> dict[str, str]:
    base = Path(__file__).resolve().parents[1]
    return {name: file_sha256(base / name) for name in CODE_FILES}


def _check_source(directory: Path) -> list[dict[str, Any]]:
    source = _read(directory / "source.json")
    if (
        source["schema_version"] != "native-cache-source/v1"
        or source["producer"] != producer_identity()
    ):
        raise ValueError("native producer version/source identity changed")
    cases = source["cases"]
    if len(cases) != len(CONTROLS):
        raise ValueError("controlled native census differs")
    digests = {variant: file_sha256(directory / f"frozen-{variant}.pkl") for variant in ("a", "b")}
    if digests["a"] == digests["b"]:
        raise ValueError("controlled artifacts must be distinct")
    for index, (case, schedule) in enumerate(zip(cases, CONTROLS, strict=True)):
        control, requested, installed = schedule
        reference, observation = case["reference"], case["consumer_observation"]
        expected_return = list(cached_value(0, installed))
        expected = {
            "requested_sha256": digests[requested],
            "consumed_sha256": digests[installed],
            "status": "no_binding_fault" if requested == installed else "binding_fault",
            "expected_return": expected_return,
        }
        if (
            case["case_id"] != f"case-{index:02d}"
            or case["context"] != 0
            or case["control"] != control
            or reference != expected
        ):
            raise ValueError("reference differs from the fixed independent control schedule")
        fingerprint = content_sha256(
            json.dumps(expected_return, sort_keys=True, separators=(",", ":")).encode()
        )
        if (
            observation["consumed_sha256"] != digests[installed]
            or observation["returned"] != expected_return
            or observation["return_fingerprint"] != fingerprint
            or observation["byte_count"] != (directory / f"frozen-{installed}.pkl").stat().st_size
        ):
            raise ValueError("same-buffer native consumer observation differs from reference")
        facts = parser_facts(case["documents"])
        roles = {fact["kind"]: fact["digest"] for fact in facts}
        if roles != {
            "requested_endpoint": digests[requested],
            "loaded_endpoint": digests[installed],
        }:
            raise ValueError("producer adapter failed the independent endpoint reference")
        if resolve_facts(facts) != {"state": "identified", "compatible": [expected["status"]]}:
            raise ValueError("resolver failed the controlled reference")
    return list(cases)


def request_frame(directory: Path) -> list[dict[str, Any]]:
    requests = []
    for case in _check_source(directory):
        for view in ("log_only", "with_consumer"):
            documents = [
                doc
                for doc in case["documents"]
                if view == "with_consumer" or doc["kind"] != "consumer-witness"
            ]
            payload = provider_payload(documents)
            expected = resolve_facts(parser_facts(documents))
            if view == "log_only" and expected != {
                "state": "ambiguous",
                "compatible": ["binding_fault", "no_binding_fault"],
            }:
                raise ValueError("log-only source accidentally discloses a loaded endpoint")
            requests.append(
                {
                    "request_id": sha256([case["case_id"], view, payload]),
                    "case_id": case["case_id"],
                    "view": view,
                    "payload": payload,
                }
            )
    return requests


def _wire(request: dict[str, Any]) -> dict[str, Any]:
    return {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": PROMPT},
            {
                "role": "user",
                "content": json.dumps(request["payload"], sort_keys=True, ensure_ascii=False),
            },
        ],
        "response_format": _openai_response_format(json.dumps(RESPONSE_SCHEMA)),
        "temperature": 0.0,
        "seed": 731,
        "max_tokens": MAX_OUTPUT_TOKENS,
        "store": False,
    }


def _audit_wires(requests: list[dict[str, Any]]) -> dict[str, Any]:
    captured: list[dict[str, Any]] = []

    class Completions:
        def create(self, **kwargs: Any) -> object:
            captured.append(deepcopy(kwargs))
            # Synthetic contract response, not a measured language-model result.
            return SimpleNamespace(
                usage=SimpleNamespace(prompt_tokens=100, completion_tokens=10),
                choices=[
                    SimpleNamespace(
                        finish_reason="stop",
                        message=SimpleNamespace(
                            refusal=None,
                            content='{"schema_version":"source-evidence-proposal/v1","facts":[]}',
                        ),
                    )
                ],
            )

    caller = OpenAIDevelopmentCaller(
        maximum_calls=len(requests),
        client=SimpleNamespace(chat=SimpleNamespace(completions=Completions())),
    )
    encoding = tiktoken.get_encoding("o200k_base")
    for request in requests:
        wire = _wire(request)
        bound = (
            len(
                encoding.encode(
                    PROMPT + wire["messages"][1]["content"] + json.dumps(wire["response_format"]),
                    disallowed_special=(),
                )
            )
            + 512
        )
        if bound > MAX_INPUT_TOKENS:
            raise ValueError("native input exceeds the approved token reservation")
        caller.invoke(prompt=PROMPT, payload=request["payload"], schema=RESPONSE_SCHEMA)
        if captured[-1] != wire:
            raise ValueError("actual SDK interface differs from the minimized planned wire")
    return {
        "sdk_capture_count": len(captured),
        "wire_sha256": sha256(captured),
        "provider_calls_executed": 0,
    }


def _expected_plan(directory: Path, requests: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": "native-cache-extraction-plan/v1",
        "source_sha256": _source_files(directory),
        "code_sha256": _code_identity(),
        "producer": producer_identity(),
        "request_frame_sha256": sha256(requests),
        "prompt_sha256": sha256(PROMPT),
        "response_schema_sha256": sha256(RESPONSE_SCHEMA),
        "model_snapshot": MODEL,
        "destination": DESTINATION,
        "maximum_provider_calls": len(requests),
        "maximum_reserved_cost_usd_at_frozen_rates": round(len(requests) * MAX_CALL_USD, 6),
        "cost_ceiling_usd": COST_CEILING_USD,
        "max_input_tokens": MAX_INPUT_TOKENS,
        "max_output_tokens": MAX_OUTPUT_TOKENS,
        "sdk_retries": 0,
        "store": False,
        "provider_fields": [
            "native stdout/query text",
            "caller intent SHA256",
            "consumer SHA256 in full view only",
            "document identities, producer semantics and caller-bound scope",
        ],
        "withheld_fields": [
            "control schedule",
            "reference status",
            "returned values",
            "pickle bytes",
            "absolute paths",
            "historical experiments",
        ],
        "metrics": [
            "exact visible fact frame",
            "grounding",
            "role/provenance",
            "omissions",
            "raw unwarranted singleton",
            "guarded resolution",
            "cost and latency",
        ],
        "source_cluster_count": 1,
        "scientific_use": "exposed controlled development; paired descriptive census, not natural incidents or independent final validation",
        "failure_policy": "one attempt per slot, no automatic rerun; failures retained in the full denominator",
        "wire_audit": _audit_wires(requests),
    }


def prepare(*, root: Path, directory: Path) -> dict[str, Any]:
    root = root.resolve()
    if root / "src/aletheia_lab" != Path(__file__).resolve().parents[1]:
        raise ValueError("root differs from the executing code checkout")
    directory = _private_dir(directory, new=True).resolve()
    environment = dict(os.environ, PYTHONPATH=str(root / "src"))
    environment.pop("OPENAI_API_KEY", None)
    subprocess.run(
        [
            sys.executable,
            "-m",
            "aletheia_lab.evaluation.native_cache_source",
            "--output",
            str(directory),
        ],
        cwd=directory,
        env=environment,
        capture_output=True,
        check=True,
        timeout=90,
    )
    requests = request_frame(directory)
    plan = _expected_plan(directory, requests)
    if plan["maximum_reserved_cost_usd_at_frozen_rates"] > COST_CEILING_USD:
        raise ValueError("planned cost reservation exceeds ceiling")
    _write(directory / "plan.json", {**plan, "plan_sha256": sha256(plan)})
    return preflight(directory=directory)


def checked_plan(
    *, directory: Path, confirm_sha256: str | None = None
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    _private_dir(directory)
    saved = _read(directory / "plan.json")
    requests = request_frame(directory)
    expected = _expected_plan(directory, requests)
    if saved != {**expected, "plan_sha256": sha256(expected)} or (
        confirm_sha256 is not None and confirm_sha256 != sha256(expected)
    ):
        raise ValueError("source, code, runtime, plan or confirmation identity changed")
    return saved, requests


def preflight(*, directory: Path) -> dict[str, Any]:
    plan, requests = checked_plan(directory=directory)
    return {
        "status": "offline_native_cache_extraction_preflight_pass",
        "plan_sha256": plan["plan_sha256"],
        "native_load_count": len(CONTROLS),
        "case_view_count": len(requests),
        "source_cluster_count": 1,
        "parser_exact_reference_count": len(requests),
        "maximum_provider_calls": plan["maximum_provider_calls"],
        "maximum_reserved_cost_usd_at_frozen_rates": plan[
            "maximum_reserved_cost_usd_at_frozen_rates"
        ],
        "cost_ceiling_usd": COST_CEILING_USD,
        "model_snapshot": MODEL,
        "destination": DESTINATION,
        "wire_audit": plan["wire_audit"],
        "llm_efficacy_measured": False,
    }


def _assess(request: dict[str, Any], call: DevelopmentCall) -> dict[str, Any]:
    if call.status != "completed":
        return {
            "status": "technical_failure",
            "exact_fact_frame": False,
            "unsafe_raw_commit": False,
            "guarded_resolution": None,
        }
    return assess_native_proposal(
        request["payload"]["documents"], (call.payload_json or "").encode()
    )


def _analysis(rows: list[dict[str, Any]]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for row in rows:
        status = row["assessment"]["status"]
        counts[status] = counts.get(status, 0) + 1
    return {
        "denominator": len(rows),
        "source_cluster_count": 1,
        "status_counts": counts,
        "exact_fact_frame_count": sum(row["assessment"]["exact_fact_frame"] for row in rows),
        "raw_unwarranted_singleton_count": sum(
            row["assessment"]["unsafe_raw_commit"] for row in rows
        ),
        "guarded_resolution_count": sum(
            row["assessment"]["guarded_resolution"] is not None for row in rows
        ),
        "provider_attempt_count": sum(row["call"]["provider_attempted"] for row in rows),
        "cost_usd_at_frozen_rates": round(
            sum(row["call"]["estimated_cost_usd"] for row in rows), 6
        ),
        "provider_latency_seconds": sum(row["call"]["latency_seconds"] for row in rows),
        "parser_provider_calls": 0,
        "parser_exact_fact_frame_count": len(rows),
        "paired_case_results": [
            {
                "case_id": case_id,
                "exact_in_both_views": all(
                    row["assessment"]["exact_fact_frame"]
                    for row in rows
                    if row["case_id"] == case_id
                ),
                "raw_unwarranted_singletons": sum(
                    row["assessment"]["unsafe_raw_commit"]
                    for row in rows
                    if row["case_id"] == case_id
                ),
            }
            for case_id in sorted({row["case_id"] for row in rows})
        ],
        "views": {
            view: {
                "denominator": sum(row["view"] == view for row in rows),
                "exact_count": sum(
                    row["view"] == view and row["assessment"]["exact_fact_frame"] for row in rows
                ),
            }
            for view in ("log_only", "with_consumer")
        },
        "limitations": "one controlled producer; paired views; conservative guard; not a population estimate, semantic superiority or natural incident study",
    }


def execute(
    *,
    directory: Path,
    confirm_sha256: str,
    caller: DevelopmentCaller,
    progress: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    plan, requests = checked_plan(directory=directory, confirm_sha256=confirm_sha256)
    if (directory / "lease.json").exists() or (directory / "results").exists():
        raise ValueError("attempt exists; no automatic provider replay")
    _write(
        directory / "lease.json",
        {"plan_sha256": plan["plan_sha256"], "maximum_provider_calls": len(requests)},
    )
    (directory / "results").mkdir(mode=0o700)
    rows = []
    for index, request in enumerate(requests):
        try:
            call = caller.invoke(prompt=PROMPT, payload=request["payload"], schema=RESPONSE_SCHEMA)
        except Exception:
            call = DevelopmentCall(
                status="technical_failure",
                payload_json=None,
                input_tokens=MAX_INPUT_TOKENS,
                output_tokens=MAX_OUTPUT_TOKENS,
                estimated_cost_usd=MAX_CALL_USD,
                latency_seconds=0.0,
                usage_observed=False,
            )
        if (
            call.estimated_cost_usd > MAX_CALL_USD
            or call.input_tokens > MAX_INPUT_TOKENS
            or call.output_tokens > MAX_OUTPUT_TOKENS
        ):
            raise ValueError("caller exceeded the frozen resource contract")
        row = {
            "request_id": request["request_id"],
            "case_id": request["case_id"],
            "view": request["view"],
            "call": call.model_dump(mode="json"),
            "assessment": _assess(request, call),
        }
        _write(directory / "results" / f"{index:02d}.json", row)
        rows.append(row)
        if progress is not None:
            progress(
                {
                    "status": "native_cache_extraction_progress",
                    "completed_requests": len(rows),
                    "maximum_requests": len(requests),
                }
            )
    receipt = {
        "status": "native_cache_extraction_development_complete",
        "plan_sha256": plan["plan_sha256"],
        "results_sha256": sha256(rows),
        "analysis": _analysis(rows),
        "source_mutated": _source_files(directory) != plan["source_sha256"],
    }
    if receipt["source_mutated"]:
        raise ValueError("source snapshot mutated during execution")
    _write(directory / "receipt.json", receipt)
    return verify(directory=directory)


def verify(*, directory: Path) -> dict[str, Any]:
    plan, requests = checked_plan(directory=directory)
    if _read(directory / "lease.json") != {
        "plan_sha256": plan["plan_sha256"],
        "maximum_provider_calls": len(requests),
    }:
        raise ValueError("execution lease differs")
    result_dir = directory / "results"
    if result_dir.is_symlink() or {path.name for path in result_dir.iterdir()} != {
        f"{index:02d}.json" for index in range(len(requests))
    }:
        raise ValueError("result census is incomplete or has extra slots")
    rows = []
    for index, request in enumerate(requests):
        saved = _read(result_dir / f"{index:02d}.json")
        call = DevelopmentCall.model_validate(saved["call"])
        expected = {
            "request_id": request["request_id"],
            "case_id": request["case_id"],
            "view": request["view"],
            "call": call.model_dump(mode="json"),
            "assessment": _assess(request, call),
        }
        if (
            saved != expected
            or call.estimated_cost_usd > MAX_CALL_USD
            or call.input_tokens > MAX_INPUT_TOKENS
            or call.output_tokens > MAX_OUTPUT_TOKENS
        ):
            raise ValueError("stored response, assessment, resources or request binding changed")
        rows.append(expected)
    expected_receipt = {
        "status": "native_cache_extraction_development_complete",
        "plan_sha256": plan["plan_sha256"],
        "results_sha256": sha256(rows),
        "analysis": _analysis(rows),
        "source_mutated": False,
    }
    if _read(directory / "receipt.json") != expected_receipt:
        raise ValueError("receipt does not reproduce from exact retained responses")
    return expected_receipt
