"""One small source-bound artifact-lineage development pilot.

Prepare and replay have no network client. Execute rechecks the retained source,
code, wire, budget and exact approved plan before consuming a local lease. It
reuses the existing bounded no-retry caller rather than another provider stack.
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
from aletheia_lab.evaluation.artifact_lineage_policy import (
    POLICIES,
    PROMPTS,
    LineageDecision,
    assess_decision,
    baseline_decision,
    decision_schema,
    parse_decision,
    summarize_rows,
)
from aletheia_lab.evaluation.artifact_lineage_sources import (
    retained_development_cases,
    validate_cases,
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

COST_CEILING_USD = 2.0
BASELINES = ("visible_rule", "always_abstain", "metric_only")
_BASE_FILES = {"plan.json", "cases.json", "offline-baselines.json", "input-audit.json"}


def request_frame(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    contexts = {canonical_execution_sha256(case["context"]): case["context"] for case in cases}
    requests = []
    encoding = tiktoken.get_encoding("o200k_base")
    response_format = _openai_response_format(json.dumps(decision_schema()))
    for index, digest in enumerate(sorted(contexts)):
        policies = POLICIES if index % 2 == 0 else tuple(reversed(POLICIES))
        for policy in policies:
            payload = {"visible_context": contexts[digest]}
            tokens = (
                len(
                    encoding.encode(
                        PROMPTS[policy]
                        + json.dumps(payload, sort_keys=True, ensure_ascii=False)
                        + json.dumps(response_format),
                        disallowed_special=(),
                    )
                )
                + 512
            )
            if tokens > MAX_INPUT_TOKENS:
                raise ValueError("visible request exceeds the common input reservation")
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


def _expected_wire(request: dict[str, Any]) -> dict[str, Any]:
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
    """Capture ALL real SDK-interface arguments using a non-network substitute."""
    captured: list[dict[str, Any]] = []

    class Completions:
        def create(self, **kwargs: Any) -> object:
            captured.append(deepcopy(kwargs))
            context = json.loads(kwargs["messages"][1]["content"])["visible_context"]
            answer = baseline_decision(context).model_dump_json()
            return SimpleNamespace(
                usage=SimpleNamespace(prompt_tokens=400, completion_tokens=30),
                choices=[
                    SimpleNamespace(
                        finish_reason="stop", message=SimpleNamespace(refusal=None, content=answer)
                    )
                ],
            )

    requests = request_frame(cases)
    client = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
    caller = OpenAIDevelopmentCaller(maximum_calls=len(requests), client=client)
    wires: list[dict[str, Any]] = []
    for request in requests:
        call = caller.invoke(
            prompt=PROMPTS[request["policy"]], payload=request["payload"], schema=decision_schema()
        )
        if (
            len(captured) != len(wires) + 1
            or captured[-1] != _expected_wire(request)
            or _parse(call) is None
        ):
            raise ValueError(
                "SDK input or shared response contract differs from the authorized channel"
            )
        wires.append(
            {
                "request_id": request["request_id"],
                "wire_sha256": canonical_execution_sha256(captured[-1]),
            }
        )
    return {
        "schema_version": "artifact-lineage-policy-input-audit/v1",
        "status": "complete_sdk_interface_capture_pass",
        "sdk_capture_count": len(wires),
        "provider_calls": 0,
        "wire_frame_sha256": canonical_execution_sha256(wires),
        "excluded_wire_fields": [],
        "http_transport_measured": False,
        "case_metadata_in_messages": False,
        "schema_and_sampling_shared": True,
    }


def _code_identity(root: Path) -> dict[str, str]:
    names = (
        "evaluation/artifact_lineage_policy.py",
        "evaluation/artifact_lineage_sources.py",
        "evaluation/artifact_lineage_pilot.py",
        "evaluation/artifact_binding_reader.py",
        "evaluation/warrant_development_live.py",
        "evaluation/warrant_development_io.py",
        "evaluation/warrant_development.py",
        "evaluation/execution_contracts.py",
        "evaluation/claim_evidence_semantics.py",
        "model_gateway/openai.py",
        "benchmark/p2/model_artifact_binding_observation.py",
        "content_hashing.py",
        "filesystem.py",
        "project/identity.py",
    )
    paths = [f"src/aletheia_lab/{name}" for name in names] + [
        "scripts/artifact_lineage_policy_pilot.py"
    ]
    return {name: file_sha256(root / name) for name in paths}


def _plan(
    root: Path, cases: list[dict[str, Any]], hashes: dict[str, str], audit: dict[str, Any]
) -> dict[str, Any]:
    requests = request_frame(cases)
    maximum = round(len(requests) * MAX_CALL_USD, 6)
    if maximum > COST_CEILING_USD:
        raise ValueError("source-bound pilot exceeds the fixed cost ceiling")
    return {
        "schema_version": "artifact-lineage-policy-plan/v1",
        "scientific_question": "warranted fitted-model loader binding status under evidence removal",
        "scientific_scope": "development only; not historical P5 A3, loss-cause attribution or two-fault S2",
        "case_frame_sha256": canonical_execution_sha256(cases),
        "request_frame_sha256": canonical_execution_sha256(requests),
        "input_audit_sha256": canonical_execution_sha256(audit),
        "source_hashes": hashes,
        "code_sha256": _code_identity(root),
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
        "retained_observation_count": 8,
        "alias_relabeling_count": 2,
        "source_cluster_count": 1,
        "fault_legitimate_pair_count": 2,
        "independent_fault_legitimate_source_pair_count": 1,
        "maximum_provider_calls": len(requests),
        "model_snapshot": MODEL,
        "destination": DESTINATION,
        "temperature": 0.0,
        "seed": 731,
        "top_p": "shared default; omitted",
        "maximum_input_tokens": MAX_INPUT_TOKENS,
        "maximum_output_tokens": MAX_OUTPUT_TOKENS,
        "timeout_seconds": 90.0,
        "sdk_retries": 0,
        "concurrency": 1,
        "store": False,
        "cost_ceiling_usd": COST_CEILING_USD,
        "maximum_reserved_cost_usd_at_frozen_rates": maximum,
        "frozen_rates_usd_per_million": {"input": 2, "output": 8},
        "deduplication": "one completion per policy and exact visible context; joined to every compatible retained world/control",
        "provider_fields": [
            "policy instructions",
            "visible_context",
            "shared flat decision schema",
        ],
        "withheld": [
            "private truth/reference",
            "source/case/pair/condition/alias-order IDs",
            "receipt/artifact hashes",
            "paths",
            "raw rows",
            "human judgments",
        ],
        "primary_development_endpoint": "both faulty and legitimate worlds full resolved AND missing-lineage bounded; all planned denominator",
        "comparison": "A4 minus A3-derived descriptive paired transitions; deterministic and abstention ceilings reported",
        "uncertainty": "one exposed source cluster; no population interval or AURC",
        "failure_policy": "retain failures and unexecuted requests; stop after three consecutive invalid/provider responses; no automatic paid replay",
        "protected_predictions_used": False,
        "mechanism_admitted": False,
    }


def _row(
    case: dict[str, Any], policy: str, status: str, decision: LineageDecision | None
) -> dict[str, Any]:
    return {
        **{
            key: case[key]
            for key in (
                "case_id",
                "case_kind",
                "alias_order",
                "pair_id",
                "source_cluster",
                "truth",
                "condition",
            )
        },
        "policy": policy,
        "execution_status": status,
        "assessment": assess_decision(case["context"], decision),
    }


def _baseline_rows(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        _row(case, baseline, "deterministic", baseline_decision(case["context"], baseline=baseline))
        for case in cases
        for baseline in BASELINES
    ]


def prepare_artifact_lineage_pilot(
    *, root: Path, memory_root: Path, directory: Path
) -> dict[str, Any]:
    cases, hashes = retained_development_cases(root=root, memory_root=memory_root)
    validate_cases(cases)
    audit = audit_request_inputs(cases)
    plan = _plan(root, cases, hashes, audit)
    baselines = summarize_rows(_baseline_rows(cases))
    _private_dir(directory, new=True)
    for name, value in (
        ("cases.json", cases),
        ("plan.json", plan),
        ("input-audit.json", audit),
        ("offline-baselines.json", baselines),
    ):
        _write(directory / name, value)
    return _preflight_report(plan, audit)


def _preflight_report(plan: dict[str, Any], audit: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": "offline_artifact_lineage_preflight_pass",
        "plan_sha256": canonical_execution_sha256(plan),
        **{
            key: plan[key]
            for key in (
                "case_count",
                "source_cluster_count",
                "retained_observation_count",
                "maximum_provider_calls",
                "cost_ceiling_usd",
                "maximum_reserved_cost_usd_at_frozen_rates",
                "model_snapshot",
                "destination",
            )
        },
        "sdk_capture_count": audit["sdk_capture_count"],
        "provider_calls_executed": 0,
        "live_policy_efficacy_measured": False,
        "protected_predictions_used": False,
    }


def checked_plan(
    *, root: Path, memory_root: Path, directory: Path, confirm_sha256: str | None = None
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    _private_dir(directory)
    cases, hashes = retained_development_cases(root=root, memory_root=memory_root)
    validate_cases(cases)
    audit = audit_request_inputs(cases)
    plan = _read(directory / "plan.json")
    if (
        plan != _plan(root, cases, hashes, audit)
        or _read(directory / "cases.json") != cases
        or _read(directory / "input-audit.json") != audit
        or _read(directory / "offline-baselines.json") != summarize_rows(_baseline_rows(cases))
    ):
        raise ValueError("source, case frame, input audit, code, prompt, runtime or plan changed")
    if confirm_sha256 is not None and canonical_execution_sha256(plan) != confirm_sha256:
        raise ValueError("approved plan digest differs")
    return plan, cases


def verify_artifact_lineage_preflight(
    *, root: Path, memory_root: Path, directory: Path
) -> dict[str, Any]:
    plan, _ = checked_plan(root=root, memory_root=memory_root, directory=directory)
    if {path.name for path in directory.iterdir()} != _BASE_FILES:
        raise ValueError("preflight directory has execution or unclassified artifacts")
    return {
        "verification": "pass",
        **_preflight_report(plan, _read(directory / "input-audit.json")),
    }


def _parse(call: DevelopmentCall) -> LineageDecision | None:
    if call.status != "completed" or call.payload_json is None:
        return None
    try:
        return parse_decision(call.payload_json)
    except (ValueError, TypeError):
        return None


def _check_resources(call: DevelopmentCall) -> None:
    if (
        call.input_tokens > MAX_INPUT_TOKENS
        or call.output_tokens > MAX_OUTPUT_TOKENS
        or not math.isfinite(call.estimated_cost_usd)
        or not math.isfinite(call.latency_seconds)
    ):
        raise ValueError("caller exceeded the finite common resource reservation")
    cost = (
        (
            (call.input_tokens * 2 + call.output_tokens * 8) / 1_000_000
            if call.usage_observed
            else MAX_CALL_USD
        )
        if call.provider_attempted
        else 0.0
    )
    if cost != call.estimated_cost_usd or cost > MAX_CALL_USD:
        raise ValueError("caller cost differs from frozen rates or unknown-usage reservation")


def execute_artifact_lineage_pilot(
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
    if {path.name for path in directory.iterdir()} != _BASE_FILES:
        raise ValueError("attempt already exists or directory changed; verify rather than replay")
    _write(
        directory / "lease.json",
        {"plan_sha256": confirm_sha256, "maximum_calls": plan["maximum_provider_calls"]},
    )
    results = directory / "results"
    results.mkdir(mode=0o700)
    failures = 0
    for index, request in enumerate(request_frame(cases)):
        try:
            call = caller.invoke(
                prompt=PROMPTS[request["policy"]],
                payload=request["payload"],
                schema=decision_schema(),
            )
        except Exception:
            # A caller exception can be post-charge. Never persist its text/key,
            # treat unknown usage as the maximum, or repeat this request.
            call = DevelopmentCall(
                status="technical_failure",
                payload_json=None,
                input_tokens=MAX_INPUT_TOKENS,
                output_tokens=MAX_OUTPUT_TOKENS,
                estimated_cost_usd=MAX_CALL_USD,
                latency_seconds=0,
                usage_observed=False,
            )
        _check_resources(call)
        valid = _parse(call) is not None
        record = {
            **{key: request[key] for key in ("request_id", "policy", "context_sha256")},
            "status": "parsed" if valid else "invalid_or_provider_failure",
            "call": call.model_dump(mode="json"),
        }
        _write(results / f"{request['request_id']}.json", record)
        failures = 0 if valid else failures + 1
        if progress is not None:
            progress(
                {
                    "status": "artifact_lineage_pilot_progress",
                    "completed_unique_requests": index + 1,
                    "maximum_unique_requests": plan["maximum_provider_calls"],
                }
            )
        if failures == 3:
            break
    # Check retained source/code again before publishing scientific aggregates.
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
        raise ValueError("results must be a regular private directory")
    records = {}
    for path in sorted(results.iterdir()):
        if path.suffix != ".json" or path.stem not in allowed:
            raise ValueError("unclassified or extra result artifact")
        record = _read(path)
        request = allowed[path.stem]
        if set(record) != {"request_id", "policy", "context_sha256", "status", "call"} or any(
            record[key] != request[key] for key in ("request_id", "policy", "context_sha256")
        ):
            raise ValueError("result is not bound to an approved visible request")
        call = DevelopmentCall.model_validate(record["call"])
        _check_resources(call)
        if record["status"] != (
            "parsed" if _parse(call) is not None else "invalid_or_provider_failure"
        ):
            raise ValueError("result status differs from retained response parsing")
        records[path.stem] = record
    if set(records) != {request["request_id"] for request in requests[: len(records)]}:
        raise ValueError("result set is not the executed prefix of the fixed order")
    _check_stop_policy(records, requests)
    return records


def _check_stop_policy(records: dict[str, dict[str, Any]], requests: list[dict[str, Any]]) -> None:
    failures = 0
    for index, request in enumerate(requests[: len(records)]):
        status = records[request["request_id"]]["status"]
        failures = failures + 1 if status == "invalid_or_provider_failure" else 0
        if failures == 3 and index + 1 != len(records):
            raise ValueError("results continued beyond the three-failure stop")
    if len(records) < len(requests) and failures != 3:
        raise ValueError("incomplete terminal results lack the three-failure stop")


def _resources(records: dict[str, dict[str, Any]], policy: str) -> dict[str, Any]:
    calls = [record["call"] for record in records.values() if record["policy"] == policy]
    return {
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


def _rebuild(
    directory: Path, plan: dict[str, Any], cases: list[dict[str, Any]]
) -> tuple[dict[str, Any], dict[str, Any]]:
    records = _records(directory, cases)
    rows = _baseline_rows(cases)
    for case in cases:
        digest = canonical_execution_sha256(case["context"])
        for policy in POLICIES:
            record = records.get(canonical_execution_sha256([policy, digest]))
            decision = _parse(DevelopmentCall.model_validate(record["call"])) if record else None
            rows.append(
                _row(case, policy, record["status"] if record else "not_executed", decision)
            )
    analysis = summarize_rows(rows)
    analysis.update(
        completed_unique_requests=len(records),
        not_executed_unique_requests=plan["maximum_provider_calls"] - len(records),
        incomplete_execution=len(records) != plan["maximum_provider_calls"],
        case_frame_sha256=plan["case_frame_sha256"],
        source_cluster_count=1,
        paid_arm_resources={policy: _resources(records, policy) for policy in POLICIES},
    )
    committed = round(sum(record["call"]["estimated_cost_usd"] for record in records.values()), 6)
    if committed > plan["cost_ceiling_usd"]:
        raise ValueError("retained costs exceed the authorized ceiling")
    receipt = {
        "schema_version": "artifact-lineage-policy-receipt/v1",
        "status": "development_pilot_terminalized"
        if not analysis["incomplete_execution"]
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


def verify_artifact_lineage_pilot(
    *, root: Path, memory_root: Path, directory: Path
) -> dict[str, Any]:
    plan, cases = checked_plan(root=root, memory_root=memory_root, directory=directory)
    if {path.name for path in directory.iterdir()} != _BASE_FILES | {
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
        raise ValueError("lease differs from the source-bound plan")
    analysis, receipt = _rebuild(directory, plan, cases)
    if (
        _read(directory / "analysis.json") != analysis
        or _read(directory / "receipt.json") != receipt
    ):
        raise ValueError("persisted analysis or receipt differs from offline replay")
    return {"verification": "pass", **receipt}
