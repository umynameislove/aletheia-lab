"""Bounded compositional lineage development, preserving the saturated pilot.

Prepare/preflight/verify are offline. Only the explicit execute caller can make
paid requests, once per exact visible context/policy, with a checked finite plan.
"""

from __future__ import annotations

import json
import math
import platform
from collections.abc import Callable
from copy import deepcopy
from importlib.metadata import version
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import tiktoken

from aletheia_lab.content_hashing import file_sha256
from aletheia_lab.evaluation.artifact_lineage_pilot import verify_artifact_lineage_pilot
from aletheia_lab.evaluation.compositional_lineage import (
    POLICIES,
    PROMPTS,
    Decision,
    assess_decision,
    baseline_decision,
    decision_schema,
    parse_decision,
)
from aletheia_lab.evaluation.compositional_lineage_cases import (
    assessment_row,
    baseline_rows,
    oracle_audit,
    source_bound_cases,
    summarize_rows,
)
from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256
from aletheia_lab.evaluation.warrant_development import DevelopmentCall, DevelopmentCaller
from aletheia_lab.evaluation.warrant_development_io import (
    DESTINATION,
    MAX_CALL_USD,
    MAX_INPUT_TOKENS,
    MAX_OUTPUT_TOKENS,
    MODEL,
    _private_dir,
    _read,
    _write,
)
from aletheia_lab.evaluation.warrant_development_live import OpenAIDevelopmentCaller
from aletheia_lab.model_gateway.openai import _openai_response_format

COST_CEILING_USD = 2.5
PREDECESSOR_PLAN_SHA256 = "ffeedb966c84f1bdfc8c06fb33109529e2a66661a018f276c6f261e7319129bc"
BASE_FILES = {
    "plan.json",
    "cases.json",
    "input-audit.json",
    "oracle-audit.json",
    "offline-baselines.json",
}


def request_frame(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    contexts = {canonical_execution_sha256(case["context"]): case["context"] for case in cases}
    encoding = tiktoken.get_encoding("o200k_base")
    response_format = _openai_response_format(json.dumps(decision_schema()))
    requests = []
    for index, digest in enumerate(sorted(contexts)):
        for policy in POLICIES if index % 2 == 0 else tuple(reversed(POLICIES)):
            payload = {"visible_context": contexts[digest]}
            text = (
                PROMPTS[policy]
                + json.dumps(payload, sort_keys=True, ensure_ascii=False)
                + json.dumps(response_format)
            )
            tokens = len(encoding.encode(text, disallowed_special=())) + 512
            if tokens > MAX_INPUT_TOKENS:
                raise ValueError("request exceeds common input reservation")
            requests.append(
                {
                    "request_id": canonical_execution_sha256([policy, digest]),
                    "policy": policy,
                    "context_sha256": digest,
                    "payload": payload,
                    "input_token_upper_bound": tokens,
                }
            )
    return requests


def _parse(call: DevelopmentCall) -> Decision | None:
    if call.status != "completed" or call.payload_json is None:
        return None
    try:
        return parse_decision(call.payload_json)
    except (ValueError, TypeError):
        return None


def _wire(request: dict[str, Any]) -> dict[str, Any]:
    return {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": PROMPTS[request["policy"]]},
            {
                "role": "user",
                "content": json.dumps(request["payload"], sort_keys=True, ensure_ascii=False),
            },
        ],
        "response_format": _openai_response_format(json.dumps(decision_schema())),
        "temperature": 0.0,
        "seed": 731,
        "max_tokens": MAX_OUTPUT_TOKENS,
        "store": False,
    }


def audit_request_inputs(cases: list[dict[str, Any]]) -> dict[str, Any]:
    captured: list[dict[str, Any]] = []

    class Completions:
        def create(self, **kwargs: Any) -> object:
            captured.append(deepcopy(kwargs))
            context = json.loads(kwargs["messages"][1]["content"])["visible_context"]
            answer = baseline_decision(context).model_dump_json()
            return SimpleNamespace(
                usage=SimpleNamespace(prompt_tokens=400, completion_tokens=40),
                choices=[
                    SimpleNamespace(
                        finish_reason="stop", message=SimpleNamespace(refusal=None, content=answer)
                    )
                ],
            )

    requests = request_frame(cases)
    caller = OpenAIDevelopmentCaller(
        maximum_calls=len(requests),
        client=SimpleNamespace(chat=SimpleNamespace(completions=Completions())),
    )
    wires: list[dict[str, str]] = []
    for request in requests:
        call = caller.invoke(
            prompt=PROMPTS[request["policy"]], payload=request["payload"], schema=decision_schema()
        )
        if (
            len(captured) != len(wires) + 1
            or captured[-1] != _wire(request)
            or _parse(call) is None
        ):
            raise ValueError("complete SDK wire or grammar differs")
        wires.append(
            {
                "request_id": request["request_id"],
                "wire_sha256": canonical_execution_sha256(captured[-1]),
            }
        )
    return {
        "status": "complete_sdk_interface_capture_pass",
        "sdk_capture_count": len(wires),
        "wire_frame_sha256": canonical_execution_sha256(wires),
        "provider_calls": 0,
        "http_transport_measured": False,
        "private_metadata_in_messages": False,
        "schema_and_sampling_shared": True,
        "excluded_wire_fields": [],
    }


def _source(root: Path, memory_root: Path) -> tuple[list[dict[str, Any]], dict[str, str]]:
    cases, hashes = source_bound_cases(root=root, memory_root=memory_root)
    predecessor = memory_root / "artifact-lineage-policy-development-v1"
    receipt = verify_artifact_lineage_pilot(
        root=root, memory_root=memory_root, directory=predecessor
    )
    if (
        receipt["plan_sha256"] != PREDECESSOR_PLAN_SHA256
        or receipt["completed_unique_requests"] != 72
        or receipt["status"] != "development_pilot_terminalized"
    ):
        raise ValueError("predecessor is not the completed retained lineage pilot")
    bindings = {f"anchor/{name}": digest for name, digest in hashes.items()}
    bindings.update(
        {
            f"predecessor/{path.relative_to(predecessor).as_posix()}": file_sha256(path)
            for path in sorted(predecessor.rglob("*.json"))
        }
    )
    return cases, bindings


def _identity(root: Path) -> dict[str, str]:
    paths = (
        "src/aletheia_lab/evaluation/compositional_lineage.py",
        "src/aletheia_lab/evaluation/compositional_lineage_cases.py",
        "src/aletheia_lab/evaluation/compositional_lineage_pilot.py",
        "scripts/compositional_lineage_pilot.py",
        "src/aletheia_lab/model_gateway/runtime.py",
        "src/aletheia_lab/model_gateway/schema.py",
    )
    return {name: file_sha256(root / name) for name in paths}


def _plan(
    root: Path, cases: list[dict[str, Any]], hashes: dict[str, str], audit: dict[str, Any]
) -> dict[str, Any]:
    requests = request_frame(cases)
    maximum = round(len(requests) * MAX_CALL_USD, 6)
    if len(requests) > 96 or maximum > COST_CEILING_USD:
        raise ValueError("fixed development cohort exceeds call/cost ceiling")
    return {
        "schema_version": "compositional-lineage-plan/v1",
        "task_id": "V6-A4-DEV-02",
        "scientific_scope": "exposed development after saturated direct-witness pilot; not confirmatory or historical P5 A3",
        "target": "pinned requested byte identity versus actual consumed-buffer byte identity",
        "trust": "seven externally admitted primitive kinds; all target-scope attested constraints jointly consistent; reports never constraints",
        "world_model": "8 independent typed binary variables; 256 worlds; requested and loaded dependencies disjoint; missing records unknown; global admitted-record integrity",
        "query_policy": "all minimum-cost endpoint queries guaranteeing status resolution in every feasible outcome; no prior; conflict means reconciliation not endpoint-query repair",
        "case_frame_sha256": canonical_execution_sha256(cases),
        "request_frame_sha256": canonical_execution_sha256(requests),
        "input_audit_sha256": canonical_execution_sha256(audit),
        "oracle_audit_sha256": canonical_execution_sha256(oracle_audit(cases)),
        "source_hashes": hashes,
        "code_sha256": _identity(root),
        "runtime": {
            "python": platform.python_version(),
            "packages": {
                name: version(name) for name in ("openai", "pydantic", "tiktoken", "httpx")
            },
        },
        "prompt_sha256": {
            policy: canonical_execution_sha256(PROMPTS[policy]) for policy in POLICIES
        },
        "schema_sha256": canonical_execution_sha256(decision_schema()),
        "case_count": len(cases),
        "authored_motif_count": 12,
        "source_cluster_count": 1,
        "aliases_and_orders_independent": False,
        "event_envelopes_observed": False,
        "maximum_provider_calls": len(requests),
        "model_snapshot": MODEL,
        "destination": DESTINATION,
        "maximum_input_tokens": MAX_INPUT_TOKENS,
        "maximum_output_tokens": MAX_OUTPUT_TOKENS,
        "temperature": 0.0,
        "seed": 731,
        "sdk_retries": 0,
        "timeout_seconds": 90.0,
        "concurrency": 1,
        "store": False,
        "cost_ceiling_usd": COST_CEILING_USD,
        "maximum_reserved_cost_usd_at_frozen_rates": maximum,
        "frozen_rates_usd_per_million": {"input": 2, "output": 8},
        "provider_fields": [
            "shared task semantics and policy instructions",
            "visible_context",
            "flat decision schema",
        ],
        "withheld": [
            "case/motif/order/source IDs",
            "private source/receipt hashes and paths",
            "oracle worlds/references",
            "human labels",
            "raw source rows",
        ],
        "primary_development_endpoint": "all-planned justified action success; resolve identified views, minimum-guaranteed check on ambiguous views, cite and flag conflicts",
        "secondary_endpoints": [
            "resolution",
            "bounded abstention",
            "unwarranted commitment",
            "conflict handling",
            "citation proof",
            "cost",
            "latency",
        ],
        "comparison": "paired A4 minus A3-derived motif success; same inputs/model/schema/sampling/budget; dependent alias/order controls",
        "uncertainty": "finite constructed census from one exposed source; no population CI, power claim, independent-source count inflation or generalization guarantee",
        "failure_policy": "no retry; stop three consecutive technical/schema failures; semantic errors retained, all missing requests in planned denominator",
        "continuation_policy": "no automatic expansion seeking A4 wins; tied ceiling closes headroom for this frame",
        "protected_predictions_used": False,
        "mechanism_admitted": False,
    }


def _report(plan: dict[str, Any], audit: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": "offline_compositional_lineage_preflight_pass",
        "plan_sha256": canonical_execution_sha256(plan),
        **{
            key: plan[key]
            for key in (
                "case_count",
                "authored_motif_count",
                "source_cluster_count",
                "maximum_provider_calls",
                "model_snapshot",
                "destination",
                "cost_ceiling_usd",
                "maximum_reserved_cost_usd_at_frozen_rates",
            )
        },
        "sdk_capture_count": audit["sdk_capture_count"],
        "provider_calls_executed": 0,
        "live_policy_efficacy_measured": False,
        "event_envelopes_observed": False,
    }


def prepare(*, root: Path, memory_root: Path, directory: Path) -> dict[str, Any]:
    cases, hashes = _source(root, memory_root)
    audit = audit_request_inputs(cases)
    plan = _plan(root, cases, hashes, audit)
    _private_dir(directory, new=True)
    for name, value in (
        ("cases.json", cases),
        ("plan.json", plan),
        ("input-audit.json", audit),
        ("oracle-audit.json", oracle_audit(cases)),
        ("offline-baselines.json", summarize_rows(baseline_rows(cases))),
    ):
        _write(directory / name, value)
    return _report(plan, audit)


def checked_plan(
    *, root: Path, memory_root: Path, directory: Path, confirm_sha256: str | None = None
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    _private_dir(directory)
    cases, hashes = _source(root, memory_root)
    audit = audit_request_inputs(cases)
    plan = _read(directory / "plan.json")
    if (
        plan != _plan(root, cases, hashes, audit)
        or _read(directory / "cases.json") != cases
        or _read(directory / "input-audit.json") != audit
        or _read(directory / "oracle-audit.json") != oracle_audit(cases)
        or _read(directory / "offline-baselines.json") != summarize_rows(baseline_rows(cases))
    ):
        raise ValueError("source/code/runtime/corpus/wire/plan changed")
    if confirm_sha256 is not None and canonical_execution_sha256(plan) != confirm_sha256:
        raise ValueError("approved plan digest differs")
    return plan, cases


def preflight(*, root: Path, memory_root: Path, directory: Path) -> dict[str, Any]:
    plan, _ = checked_plan(root=root, memory_root=memory_root, directory=directory)
    if {path.name for path in directory.iterdir()} != BASE_FILES:
        raise ValueError("preflight found execution or unclassified artifacts")
    return {"verification": "pass", **_report(plan, _read(directory / "input-audit.json"))}


def _resources(call: DevelopmentCall) -> None:
    if call.status == "completed" and (not call.provider_attempted or not call.usage_observed):
        raise ValueError("completed output lacks the frozen caller's provider/usage observations")
    expected = (
        (
            (call.input_tokens * 2 + call.output_tokens * 8) / 1_000_000
            if call.usage_observed
            else MAX_CALL_USD
        )
        if call.provider_attempted
        else 0.0
    )
    if (
        call.input_tokens > MAX_INPUT_TOKENS
        or call.output_tokens > MAX_OUTPUT_TOKENS
        or not math.isfinite(call.estimated_cost_usd)
        or not math.isfinite(call.latency_seconds)
        or call.estimated_cost_usd != expected
        or expected > MAX_CALL_USD
    ):
        raise ValueError("caller exceeded resource bounds or frozen cost accounting")


def _invoke(caller: DevelopmentCaller, request: dict[str, Any]) -> DevelopmentCall:
    try:
        call = caller.invoke(
            prompt=PROMPTS[request["policy"]], payload=request["payload"], schema=decision_schema()
        )
    except Exception:
        call = DevelopmentCall(
            status="technical_failure",
            payload_json=None,
            input_tokens=MAX_INPUT_TOKENS,
            output_tokens=MAX_OUTPUT_TOKENS,
            estimated_cost_usd=MAX_CALL_USD,
            latency_seconds=0,
            usage_observed=False,
        )
    _resources(call)
    return call


def execute(
    *,
    root: Path,
    memory_root: Path,
    directory: Path,
    confirm_sha256: str,
    caller: DevelopmentCaller,
    progress: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    plan, cases = checked_plan(
        root=root, memory_root=memory_root, directory=directory, confirm_sha256=confirm_sha256
    )
    if {path.name for path in directory.iterdir()} != BASE_FILES:
        raise ValueError("attempt already exists; verify rather than replay")
    _write(
        directory / "lease.json",
        {"plan_sha256": confirm_sha256, "maximum_calls": plan["maximum_provider_calls"]},
    )
    results = directory / "results"
    results.mkdir(mode=0o700)
    failures = 0
    for index, request in enumerate(request_frame(cases)):
        call = _invoke(caller, request)
        status = "parsed" if _parse(call) is not None else "invalid_or_provider_failure"
        record = {
            **{key: request[key] for key in ("request_id", "policy", "context_sha256")},
            "status": status,
            "call": call.model_dump(mode="json"),
        }
        _write(results / f"{request['request_id']}.json", record)
        failures = failures + 1 if status != "parsed" else 0
        if progress is not None:
            progress(
                {
                    "status": "compositional_lineage_pilot_progress",
                    "completed_unique_requests": index + 1,
                    "maximum_unique_requests": plan["maximum_provider_calls"],
                }
            )
        if failures == 3:
            break
    checked_plan(
        root=root, memory_root=memory_root, directory=directory, confirm_sha256=confirm_sha256
    )
    analysis, receipt = _rebuild(directory, plan, cases)
    _write(directory / "analysis.json", analysis)
    _write(directory / "receipt.json", receipt)
    return receipt


def _records(directory: Path, cases: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    requests = request_frame(cases)
    allowed = {request["request_id"]: request for request in requests}
    results = directory / "results"
    if results.is_symlink() or not results.is_dir():
        raise ValueError("results are not a regular directory")
    records = {}
    for path in sorted(results.iterdir()):
        if path.suffix != ".json" or path.stem not in allowed:
            raise ValueError("unclassified result artifact")
        record = _read(path)
        request = allowed[path.stem]
        if set(record) != {"request_id", "policy", "context_sha256", "status", "call"} or any(
            record[key] != request[key] for key in ("request_id", "policy", "context_sha256")
        ):
            raise ValueError("result binding differs from the approved request")
        call = DevelopmentCall.model_validate(record["call"])
        _resources(call)
        if record["status"] != (
            "parsed" if _parse(call) is not None else "invalid_or_provider_failure"
        ):
            raise ValueError("result status differs from parsing")
        records[path.stem] = record
    if set(records) != {request["request_id"] for request in requests[: len(records)]}:
        raise ValueError("results are not the executed fixed-order prefix")
    failures = 0
    for index, request in enumerate(requests[: len(records)]):
        failures = failures + 1 if records[request["request_id"]]["status"] != "parsed" else 0
        if failures == 3 and index + 1 != len(records):
            raise ValueError("execution continued beyond the failure stop")
    if len(records) < len(requests) and failures != 3:
        raise ValueError("incomplete results lack a recorded terminal stop")
    return records


def _rebuild(
    directory: Path, plan: dict[str, Any], cases: list[dict[str, Any]]
) -> tuple[dict[str, Any], dict[str, Any]]:
    records = _records(directory, cases)
    rows = baseline_rows(cases)
    for case in cases:
        for policy in POLICIES:
            request_id = canonical_execution_sha256(
                [policy, canonical_execution_sha256(case["context"])]
            )
            record = records.get(request_id)
            decision = _parse(DevelopmentCall.model_validate(record["call"])) if record else None
            rows.append(
                assessment_row(
                    case,
                    policy,
                    assess_decision(case["context"], decision),
                    record["status"] if record else "not_executed",
                )
            )
    analysis = summarize_rows(rows)
    resources = {}
    for policy in POLICIES:
        calls = [record["call"] for record in records.values() if record["policy"] == policy]
        resources[policy] = {
            "unique_executed_requests": len(calls),
            "provider_attempts": sum(call["provider_attempted"] for call in calls),
            "input_tokens": sum(call["input_tokens"] for call in calls),
            "output_tokens": sum(call["output_tokens"] for call in calls),
            "committed_cost_usd_at_frozen_rates": round(
                sum(call["estimated_cost_usd"] for call in calls), 6
            ),
            "total_latency_seconds": sum(call["latency_seconds"] for call in calls),
            "unknown_usage_requests": sum(not call["usage_observed"] for call in calls),
        }
    incomplete = len(records) != plan["maximum_provider_calls"]
    analysis.update(
        completed_unique_requests=len(records),
        not_executed_unique_requests=plan["maximum_provider_calls"] - len(records),
        incomplete_execution=incomplete,
        source_cluster_count=1,
        authored_motif_count=12,
        paid_arm_resources=resources,
    )
    committed = round(sum(record["call"]["estimated_cost_usd"] for record in records.values()), 6)
    if committed > plan["cost_ceiling_usd"]:
        raise ValueError("retained cost exceeds authorized ceiling")
    receipt = {
        "schema_version": "compositional-lineage-receipt/v1",
        "status": "development_pilot_terminalized"
        if not incomplete
        else "development_pilot_stopped_technical",
        "plan_sha256": canonical_execution_sha256(plan),
        "lease_sha256": file_sha256(directory / "lease.json"),
        "results_sha256": canonical_execution_sha256(records),
        "analysis_sha256": canonical_execution_sha256(analysis),
        "completed_unique_requests": len(records),
        "not_executed_unique_requests": plan["maximum_provider_calls"] - len(records),
        "provider_attempt_count": sum(
            record["call"]["provider_attempted"] for record in records.values()
        ),
        "parsed_unique_requests": sum(record["status"] == "parsed" for record in records.values()),
        "committed_cost_usd_at_frozen_rates": committed,
        "protected_predictions_used": False,
        "mechanism_admitted": False,
        "raw_artifacts_private": True,
    }
    return analysis, receipt


def verify(*, root: Path, memory_root: Path, directory: Path) -> dict[str, Any]:
    plan, cases = checked_plan(root=root, memory_root=memory_root, directory=directory)
    if {path.name for path in directory.iterdir()} != BASE_FILES | {
        "lease.json",
        "results",
        "analysis.json",
        "receipt.json",
    }:
        raise ValueError("pilot file census differs")
    if _read(directory / "lease.json") != {
        "plan_sha256": canonical_execution_sha256(plan),
        "maximum_calls": plan["maximum_provider_calls"],
    }:
        raise ValueError("execution lease differs")
    analysis, receipt = _rebuild(directory, plan, cases)
    if (
        _read(directory / "analysis.json") != analysis
        or _read(directory / "receipt.json") != receipt
    ):
        raise ValueError("persisted analysis/receipt differs from independent replay")
    return {"verification": "pass", **receipt}
