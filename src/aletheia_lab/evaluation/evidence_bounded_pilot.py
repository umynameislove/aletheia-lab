"""Private bounded policy pilot: offline prepare, explicit paid execution, replay.

Uses the existing no-retry development caller. Only visible context reaches
the provider; cause labels, pair/source IDs and references stay evaluator-side.
"""

from __future__ import annotations

import json
import platform
from collections.abc import Callable
from importlib.metadata import version
from pathlib import Path
from typing import Any

import tiktoken

from aletheia_lab.content_hashing import file_sha256
from aletheia_lab.evaluation.evidence_bounded_policy import (
    POLICIES,
    PROMPTS,
    PolicyDecision,
    analyze_policy_rows,
    assess_decision,
    compatibility_reference,
    decision_schema,
    deterministic_decision,
)
from aletheia_lab.evaluation.evidence_bounded_sources import replay_development_cases
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
from aletheia_lab.model_gateway.openai import _openai_response_format

COST_CEILING_USD = 4.25


def _code_identity() -> dict[str, str]:
    directory = Path(__file__).parent
    return {
        name: file_sha256(directory / name)
        for name in (
            "evidence_bounded_policy.py",
            "evidence_bounded_sources.py",
            "evidence_bounded_pilot.py",
            "score_mapping_reader.py",
            "warrant_development_live.py",
            "warrant_development_io.py",
            "warrant_development.py",
            "claim_evidence_semantics.py",
            "execution_contracts.py",
            "../model_gateway/openai.py",
            "../filesystem.py",
            "../project/identity.py",
        )
    }


def _requests(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    contexts = {canonical_execution_sha256(c["context"]): c["context"] for c in cases}
    result = []
    encoding = tiktoken.get_encoding("o200k_base")
    schema = decision_schema()
    wire_schema = _openai_response_format(json.dumps(schema))
    for index, digest in enumerate(sorted(contexts)):
        # Alternate order within each identical-context block, not by hidden cause.
        policies = POLICIES if index % 2 == 0 else tuple(reversed(POLICIES))
        for policy in policies:
            payload = {"visible_context": contexts[digest]}
            bound = (
                len(
                    encoding.encode(
                        PROMPTS[policy]
                        + json.dumps(payload, ensure_ascii=False, sort_keys=True)
                        + json.dumps(wire_schema),
                        disallowed_special=(),
                    )
                )
                + 512
            )
            if bound > MAX_INPUT_TOKENS:
                raise ValueError("a matched request exceeds the common input ceiling")
            result.append(
                {
                    "request_id": canonical_execution_sha256([policy, digest]),
                    "policy": policy,
                    "context_sha256": digest,
                    "payload": payload,
                    "input_token_upper_bound": bound,
                }
            )
    return result


def _plan(cases: list[dict[str, Any]], source_hashes: dict[str, str]) -> dict[str, Any]:
    requests = _requests(cases)
    return {
        "schema_version": "evidence-bounded-policy-plan/v1",
        "case_frame_sha256": canonical_execution_sha256(cases),
        "request_frame_sha256": canonical_execution_sha256(requests),
        "source_hashes": source_hashes,
        "code_sha256": _code_identity(),
        "runtime": {
            "python": platform.python_version(),
            "packages": {
                name: version(name) for name in ("openai", "pydantic", "tiktoken", "httpx")
            },
        },
        "prompt_sha256": {p: canonical_execution_sha256(PROMPTS[p]) for p in POLICIES},
        "schema_sha256": canonical_execution_sha256(decision_schema()),
        "case_count": len(cases),
        "pair_count": len({c["pair_id"] for c in cases}),
        "source_cluster_count": len({c["source_cluster"] for c in cases}),
        "maximum_provider_calls": len(requests),
        "deduplication": "one completion per policy and exact visible context; copied to observationally identical worlds",
        "model_snapshot": MODEL,
        "destination": DESTINATION,
        "temperature": 0.0,
        "seed": 731,
        "top_p": "shared API default; omitted",
        "maximum_input_tokens": MAX_INPUT_TOKENS,
        "maximum_output_tokens": MAX_OUTPUT_TOKENS,
        "timeout_seconds": 90.0,
        "sdk_retries": 0,
        "concurrency": 1,
        "store": False,
        "cost_ceiling_usd": COST_CEILING_USD,
        "maximum_reserved_cost_usd_at_frozen_rates": round(len(requests) * MAX_CALL_USD, 6),
        "frozen_rates_usd_per_million": {"input": 2, "output": 8},
        "provider_fields": [
            "shared policy instructions",
            "visible_context",
            "shared decision schema",
        ],
        "withheld": [
            "hidden cause",
            "source/pair/condition/dose IDs",
            "reference",
            "paths",
            "raw rows",
            "human judgments",
        ],
        "scientific_scope": "controlled-decision development pilot; not historical P5 A3 or free-prose efficacy",
        "contradictory_evidence_cohort": False,
        "failure_policy": "retain all failures; stop after three consecutive invalid/provider responses; never rerun silently",
    }


def prepare_policy_pilot(
    *, root: Path, memory_root: Path, directory: Path, source_root: Path | None = None
) -> dict[str, Any]:
    cases, hashes = replay_development_cases(
        root=root, memory_root=memory_root, source_root=source_root
    )
    return prepare_case_frame(cases=cases, source_hashes=hashes, directory=directory)


def prepare_case_frame(
    *, cases: list[dict[str, Any]], source_hashes: dict[str, str], directory: Path
) -> dict[str, Any]:
    """Separate preparation from source replay for genuinely offline lifecycle tests."""
    if not cases or len({(c["pair_id"], c["truth"], c["condition"]) for c in cases}) != len(cases):
        raise ValueError("empty or duplicated case frame")
    for case in cases:
        if case["reference"] != compatibility_reference(case["context"]):
            raise ValueError("reference differs from visible witness")
    for pair_id in {c["pair_id"] for c in cases}:
        pair = [c for c in cases if c["pair_id"] == pair_id]
        if (
            len(pair) != 8
            or {(c["truth"], c["condition"]) for c in pair}
            != {
                (truth, condition)
                for truth in ("score_mapping", "target_binding")
                for condition in ("full", "missing_key", "noisy", "misleading")
            }
            or len({c["source_cluster"] for c in pair}) != 1
        ):
            raise ValueError("each complete pair must retain both worlds and four views")
        missing = [c["context"] for c in pair if c["condition"] == "missing_key"]
        if missing[0] != missing[1]:
            raise ValueError("missing-key pair has a visible shortcut")
    plan = _plan(cases, source_hashes)
    if plan["maximum_reserved_cost_usd_at_frozen_rates"] > COST_CEILING_USD:
        raise ValueError("pilot exceeds the fixed cost ceiling")
    _private_dir(directory, new=True)
    _write(directory / "cases.json", cases)
    _write(directory / "plan.json", plan)
    rows = _baseline_rows(cases)
    _write(directory / "offline-baselines.json", analyze_policy_rows(rows))
    return {
        "status": "offline_policy_preflight_pass",
        "provider_calls_executed": 0,
        "plan_sha256": canonical_execution_sha256(plan),
        **{
            k: plan[k]
            for k in (
                "case_count",
                "pair_count",
                "source_cluster_count",
                "maximum_provider_calls",
                "maximum_reserved_cost_usd_at_frozen_rates",
                "model_snapshot",
                "destination",
            )
        },
        "live_policy_efficacy_measured": False,
    }


def checked_policy_plan(
    directory: Path, *, confirm_sha256: str | None = None
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    _private_dir(directory)
    plan = _read(directory / "plan.json")
    cases = _read(directory / "cases.json")
    if plan != _plan(cases, plan["source_hashes"]):
        raise ValueError("plan, case frame, prompts, schema or runtime code changed")
    if confirm_sha256 is not None and canonical_execution_sha256(plan) != confirm_sha256:
        raise ValueError("approved plan digest differs")
    return plan, cases


def execute_policy_pilot(
    *,
    directory: Path,
    confirm_sha256: str,
    caller: DevelopmentCaller,
    progress: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    plan, cases = checked_policy_plan(directory, confirm_sha256=confirm_sha256)
    if (directory / "lease.json").exists() or (directory / "results").exists():
        raise ValueError("paid attempt already exists; verify it instead of replaying")
    _write(
        directory / "lease.json",
        {"plan_sha256": confirm_sha256, "maximum_calls": plan["maximum_provider_calls"]},
    )
    result_dir = directory / "results"
    result_dir.mkdir(mode=0o700)
    consecutive_failures = 0
    for request in _requests(cases):
        call = caller.invoke(
            prompt=PROMPTS[request["policy"]], payload=request["payload"], schema=decision_schema()
        )
        _check_call_resources(call)
        decision = _parse(call)
        status = "parsed" if decision is not None else "invalid_or_provider_failure"
        record = {
            "request_id": request["request_id"],
            "policy": request["policy"],
            "context_sha256": request["context_sha256"],
            "status": status,
            "call": call.model_dump(mode="json"),
        }
        _write(result_dir / f"{request['request_id']}.json", record)
        consecutive_failures = consecutive_failures + 1 if decision is None else 0
        if progress is not None:
            progress(
                {
                    "status": "policy_pilot_progress",
                    "completed_unique_requests": len(list(result_dir.glob("*.json"))),
                    "maximum_unique_requests": plan["maximum_provider_calls"],
                }
            )
        if consecutive_failures == 3:
            break
    analysis, receipt = _rebuild(directory, plan, cases)
    _write(directory / "analysis.json", analysis)
    _write(directory / "receipt.json", receipt)
    return receipt


def _parse(call: DevelopmentCall) -> PolicyDecision | None:
    if call.status != "completed" or call.payload_json is None:
        return None
    try:
        return PolicyDecision.model_validate_json(call.payload_json)
    except ValueError:
        return None


def _check_call_resources(call: DevelopmentCall) -> None:
    if call.input_tokens > MAX_INPUT_TOKENS or call.output_tokens > MAX_OUTPUT_TOKENS:
        raise ValueError("caller exceeded the common token reservation")
    expected = (
        (
            (call.input_tokens * 2 + call.output_tokens * 8) / 1_000_000
            if call.usage_observed
            else MAX_CALL_USD
        )
        if call.provider_attempted
        else 0.0
    )
    if call.estimated_cost_usd != expected or expected > MAX_CALL_USD:
        raise ValueError("caller cost differs from frozen rates or unknown-usage reservation")


def _baseline_rows(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            **{k: c[k] for k in ("pair_id", "source_cluster", "truth", "condition")},
            "policy": arm,
            "execution_status": "deterministic",
            "provider_attempted": False,
            "assessment": assess_decision(
                c["context"],
                deterministic_decision(c["context"], always_abstain=arm == "always_abstain"),
            ),
        }
        for c in cases
        for arm in ("always_abstain", "visible_rule")
    ]


def _rebuild(
    directory: Path, plan: dict[str, Any], cases: list[dict[str, Any]]
) -> tuple[dict[str, Any], dict[str, Any]]:
    request_by_id = {r["request_id"]: r for r in _requests(cases)}
    records = {}
    for path in sorted((directory / "results").glob("*.json")):
        record = _read(path)
        request = request_by_id.get(path.stem)
        if request is None or any(
            record[k] != request[k] for k in ("request_id", "policy", "context_sha256")
        ):
            raise ValueError("result does not bind an approved request")
        call = DevelopmentCall.model_validate_json(json.dumps(record["call"]))
        _check_call_resources(call)
        if call.estimated_cost_usd > MAX_CALL_USD or record["status"] != (
            "parsed" if _parse(call) is not None else "invalid_or_provider_failure"
        ):
            raise ValueError("persisted result status or cost is inconsistent")
        records[path.stem] = record
    rows = _baseline_rows(cases)
    for case in cases:
        digest = canonical_execution_sha256(case["context"])
        for policy in POLICIES:
            record = records.get(canonical_execution_sha256([policy, digest]))
            decision = (
                _parse(DevelopmentCall.model_validate_json(json.dumps(record["call"])))
                if record
                else None
            )
            rows.append(
                {
                    **{k: case[k] for k in ("pair_id", "source_cluster", "truth", "condition")},
                    "policy": policy,
                    "execution_status": record["status"] if record else "not_executed",
                    "provider_attempted": record["call"]["provider_attempted"] if record else False,
                    "assessment": assess_decision(case["context"], decision),
                }
            )
    analysis = analyze_policy_rows(rows)
    analysis["census"] = {k: plan[k] for k in ("case_count", "pair_count", "source_cluster_count")}
    analysis["completed_unique_requests"] = len(records)
    analysis["not_executed_unique_requests"] = len(request_by_id) - len(records)
    analysis["incomplete_execution"] = len(records) != len(request_by_id)
    analysis["paired_effects"] = _paired_effects(rows)
    analysis["paid_arm_resources"] = {
        policy: {
            "unique_completed_requests": len(
                selected := [r for r in records.values() if r["policy"] == policy]
            ),
            "provider_attempts": sum(r["call"]["provider_attempted"] for r in selected),
            "input_tokens": sum(r["call"]["input_tokens"] for r in selected),
            "output_tokens": sum(r["call"]["output_tokens"] for r in selected),
            "committed_cost_usd_at_frozen_rates": round(
                sum(r["call"]["estimated_cost_usd"] for r in selected), 6
            ),
            "total_latency_seconds": sum(r["call"]["latency_seconds"] for r in selected),
            "unknown_usage_requests": sum(not r["call"]["usage_observed"] for r in selected),
        }
        for policy in POLICIES
    }
    receipt = {
        "schema_version": "evidence-bounded-policy-receipt/v1",
        "status": "pilot_terminalized"
        if len(records) == len(request_by_id)
        else "pilot_stopped_technical",
        "plan_sha256": canonical_execution_sha256(plan),
        "lease_sha256": file_sha256(directory / "lease.json"),
        "results_sha256": canonical_execution_sha256(records),
        "analysis_sha256": canonical_execution_sha256(analysis),
        "completed_unique_requests": len(records),
        "not_executed_unique_requests": len(request_by_id) - len(records),
        "provider_attempt_count": sum(r["call"]["provider_attempted"] for r in records.values()),
        "committed_cost_usd_at_frozen_rates": round(
            sum(r["call"]["estimated_cost_usd"] for r in records.values()), 6
        ),
        "parsed_unique_requests": sum(r["status"] == "parsed" for r in records.values()),
        "protected_predictions_used": False,
        "mechanism_admitted": False,
        "raw_artifacts_private": True,
    }
    return analysis, receipt


def _paired_effects(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Average within constructed pairs first; zero failure is never safe success."""
    effects = []
    for pair_id in sorted({r["pair_id"] for r in rows}):
        pair = [r for r in rows if r["pair_id"] == pair_id]
        effects.append(
            {
                "pair_id": pair_id,
                "source_cluster": pair[0]["source_cluster"],
                "a4_minus_a3": {
                    name: (
                        sum(
                            bool(r["assessment"].get(field))
                            for r in pair
                            if r["policy"] == "a4_bounded" and r["condition"] == condition
                        )
                        - sum(
                            bool(r["assessment"].get(field))
                            for r in pair
                            if r["policy"] == "a3_matched" and r["condition"] == condition
                        )
                    )
                    / 2
                    for name, field, condition in (
                        ("full_all_planned_resolution", "valid_resolution", "full"),
                        (
                            "ambiguous_all_planned_bounded_success",
                            "bounded_ambiguous_response",
                            "missing_key",
                        ),
                    )
                },
                "ambiguous_both_policies_assessable": all(
                    r["assessment"]["assessable"]
                    for r in pair
                    if r["policy"] in POLICIES and r["condition"] == "missing_key"
                ),
                "full_both_policies_assessable": all(
                    r["assessment"]["assessable"]
                    for r in pair
                    if r["policy"] in POLICIES and r["condition"] == "full"
                ),
            }
        )
    return effects


def verify_policy_pilot(*, directory: Path) -> dict[str, Any]:
    plan, cases = checked_policy_plan(directory)
    expected_lease = {
        "plan_sha256": canonical_execution_sha256(plan),
        "maximum_calls": plan["maximum_provider_calls"],
    }
    if _read(directory / "lease.json") != expected_lease:
        raise ValueError("lease differs from the prepared plan")
    analysis, receipt = _rebuild(directory, plan, cases)
    if (
        _read(directory / "analysis.json") != analysis
        or _read(directory / "receipt.json") != receipt
    ):
        raise ValueError("independent persisted replay differs")
    return {"verification": "pass", **receipt}
