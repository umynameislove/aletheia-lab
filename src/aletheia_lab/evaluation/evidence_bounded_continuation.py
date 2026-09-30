"""Continue only uncalled policy requests, preserving the stopped pilot verbatim."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from aletheia_lab.content_hashing import file_sha256
from aletheia_lab.evaluation.evidence_bounded_decoding import DECODING_POLICY, decode_policy_call
from aletheia_lab.evaluation.evidence_bounded_pilot import (
    COST_CEILING_USD,
    _baseline_rows,
    _check_call_resources,
    _paired_effects,
    _parse,
    _requests,
    checked_policy_plan,
    verify_policy_pilot,
)
from aletheia_lab.evaluation.evidence_bounded_policy import (
    POLICIES,
    PROMPTS,
    analyze_policy_rows,
    assess_decision,
    decision_schema,
)
from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256
from aletheia_lab.evaluation.warrant_development import DevelopmentCall, DevelopmentCaller
from aletheia_lab.evaluation.warrant_development_io import MAX_CALL_USD, _private_dir, _read, _write


def _check_disjoint(predecessor: Path, directory: Path) -> None:
    if directory.resolve().is_relative_to(
        predecessor.resolve()
    ) or predecessor.resolve().is_relative_to(directory.resolve()):
        raise ValueError("continuation and predecessor directories must be disjoint")


def _manifest(directory: Path) -> dict[str, str]:
    """Bind all predecessor bytes, including raw responses and historical summaries."""
    if (directory / "results").is_symlink():
        raise ValueError("result directory cannot be symlinked")
    result = {}
    for path in sorted(directory.rglob("*")):
        if path.is_symlink():
            raise ValueError("private artifact tree cannot contain symlinks")
        if path.is_file():
            if path.stat().st_size > 16_000_000:
                raise ValueError("private file exceeds the bounded input size")
            result[path.relative_to(directory).as_posix()] = file_sha256(path)
    return result


def _load_records(directory: Path, requests: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result_dir = directory / "results"
    if result_dir.is_symlink() or not result_dir.is_dir():
        raise ValueError("regular result directory is required")
    paths = sorted(result_dir.iterdir())
    expected = {r["request_id"]: r for r in requests}
    records = {}
    for path in paths:
        request = expected.get(path.stem)
        if path.suffix != ".json" or request is None:
            raise ValueError("result does not belong to the approved remaining requests")
        record = _read(path)
        if set(record) != {"request_id", "policy", "context_sha256", "status", "call"} or any(
            record[k] != request[k] for k in ("request_id", "policy", "context_sha256")
        ):
            raise ValueError("result identity differs from the approved request")
        call = DevelopmentCall.model_validate_json(json.dumps(record["call"]))
        _check_call_resources(call)
        if record["status"] != (
            "parsed" if _parse(call) is not None else "invalid_or_provider_failure"
        ):
            raise ValueError("original strict result status differs")
        records[path.stem] = record
    prefix = {r["request_id"] for r in requests[: len(records)]}
    if set(records) != prefix:
        raise ValueError("execution ledger is not a prefix of the approved order")
    return records


def _predecessor(
    directory: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, dict[str, Any]], dict[str, str]]:
    verify_policy_pilot(directory=directory)
    plan, cases = checked_policy_plan(directory)
    records = _load_records(directory, _requests(cases))
    return plan, cases, records, _manifest(directory)


def _continuation_plan(
    predecessor: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, dict[str, Any]]]:
    original, cases, records, manifest = _predecessor(predecessor)
    remaining = [r for r in _requests(cases) if r["request_id"] not in records]
    spent = round(sum(r["call"]["estimated_cost_usd"] for r in records.values()), 6)
    if not remaining or spent + len(remaining) * MAX_CALL_USD > COST_CEILING_USD:
        raise ValueError("no remaining requests or combined cost exceeds the original ceiling")
    plan = {
        "schema_version": "evidence-bounded-policy-continuation-plan/v1",
        "predecessor_plan_sha256": canonical_execution_sha256(original),
        "predecessor_manifest": manifest,
        "predecessor_receipt_sha256": file_sha256(predecessor / "receipt.json"),
        "decoding_policy": DECODING_POLICY,
        "code_sha256": {
            name: file_sha256(Path(__file__).with_name(name))
            for name in ("evidence_bounded_continuation.py", "evidence_bounded_decoding.py")
        },
        "remaining_request_ids": [r["request_id"] for r in remaining],
        "remaining_request_frame_sha256": canonical_execution_sha256(remaining),
        "reused_unique_requests": len(records),
        "maximum_provider_calls": len(remaining),
        "predecessor_cost_usd_at_frozen_rates": spent,
        "maximum_additional_reserved_cost_usd": round(len(remaining) * MAX_CALL_USD, 6),
        "maximum_combined_reserved_cost_usd": round(spent + len(remaining) * MAX_CALL_USD, 6),
        "cost_ceiling_usd": COST_CEILING_USD,
        "unchanged_provider_contract": {
            k: original[k]
            for k in (
                "prompt_sha256",
                "schema_sha256",
                "model_snapshot",
                "destination",
                "runtime",
                "temperature",
                "seed",
                "top_p",
                "maximum_input_tokens",
                "maximum_output_tokens",
                "timeout_seconds",
                "sdk_retries",
                "concurrency",
                "store",
                "provider_fields",
                "withheld",
                "frozen_rates_usd_per_million",
            )
        },
        "failure_policy": "stop after three consecutive unassessable/provider responses; no retry",
        "scientific_scope": "supplementary semantic decoding; strict scores retained separately",
        "paid_request_replay_permitted": False,
    }
    return plan, cases, records


def _analysis(
    cases: list[dict[str, Any]], records: dict[str, dict[str, Any]]
) -> tuple[dict[str, Any], dict[str, Any]]:
    decoded = {}
    audits = {}
    for request_id, stored_record in records.items():
        stored_call = DevelopmentCall.model_validate_json(json.dumps(stored_record["call"]))
        stored_output = decode_policy_call(stored_call)
        decoded[request_id] = stored_output
        audits[request_id] = {"policy": stored_record["policy"], **stored_output.audit(stored_call)}
    strict_rows, semantic_rows = _baseline_rows(cases), _baseline_rows(cases)
    for case in cases:
        for policy in POLICIES:
            request_id = canonical_execution_sha256(
                [policy, canonical_execution_sha256(case["context"])]
            )
            record, output = records.get(request_id), decoded.get(request_id)
            common = {
                **{k: case[k] for k in ("pair_id", "source_cluster", "truth", "condition")},
                "policy": policy,
                "provider_attempted": record["call"]["provider_attempted"] if record else False,
            }
            call = (
                DevelopmentCall.model_validate_json(json.dumps(record["call"])) if record else None
            )
            strict_rows.append(
                {
                    **common,
                    "execution_status": record["status"] if record else "not_executed",
                    "assessment": assess_decision(case["context"], _parse(call) if call else None),
                }
            )
            semantic_rows.append(
                {
                    **common,
                    "execution_status": (
                        "parsed"
                        if output and output.decision is not None
                        else "invalid_or_provider_failure"
                        if record
                        else "not_executed"
                    ),
                    "assessment": output.assessment(case["context"])
                    if output
                    else assess_decision(case["context"], None),
                }
            )
    strict, semantic = analyze_policy_rows(strict_rows), analyze_policy_rows(semantic_rows)
    strict["paired_effects"] = _paired_effects(strict_rows)
    semantic["paired_effects"] = _paired_effects(semantic_rows)
    resources = {
        policy: {
            "unique_requests": len(
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
    return {
        "schema_version": "evidence-bounded-policy-continuation-analysis/v1",
        "status": "development_only",
        "strict_contract": strict,
        "supplementary_semantic_decoding": semantic,
        "paid_arm_resources": resources,
        "decoding_by_policy": {
            policy: {
                "strict_invalid_unique_requests": sum(
                    not a["strict_valid"] for a in audits.values() if a["policy"] == policy
                ),
                "decoded_noncanonical_unique_requests": sum(
                    bool(a["projected_fields"]) for a in audits.values() if a["policy"] == policy
                ),
                "unassessable_unique_requests": sum(
                    not a["semantic_assessable"] for a in audits.values() if a["policy"] == policy
                ),
                "proposed_measurement_unique_requests": sum(
                    a["original_measurement"] == "column_and_target_provenance"
                    for a in audits.values()
                    if a["policy"] == policy
                ),
            }
            for policy in POLICIES
        },
        "completed_unique_requests": len(records),
        "not_executed_unique_requests": len(_requests(cases)) - len(records),
        "strict_parsed_unique_requests": sum(a["strict_valid"] for a in audits.values()),
        "semantic_assessable_unique_requests": sum(
            a["semantic_assessable"] for a in audits.values()
        ),
        "decoded_noncanonical_unique_requests": sum(
            bool(a["projected_fields"]) for a in audits.values()
        ),
        "historical_results_replaced": False,
        "a4_superiority_established": False,
        "protected_study_authorized": False,
    }, audits


def prepare_policy_continuation(*, predecessor: Path, directory: Path) -> dict[str, Any]:
    _check_disjoint(predecessor, directory)
    plan, cases, records = _continuation_plan(predecessor)
    analysis, audits = _analysis(cases, records)
    _private_dir(directory, new=True)
    _write(directory / "plan.json", plan)
    _write(directory / "preflight-analysis.json", analysis)
    _write(directory / "preflight-decoding-audit.json", audits)
    if _manifest(predecessor) != plan["predecessor_manifest"]:
        raise ValueError("predecessor changed during preparation")
    return _preflight_summary(plan, analysis)


def _preflight_summary(plan: dict[str, Any], analysis: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": "offline_policy_continuation_preflight_pass",
        "plan_sha256": canonical_execution_sha256(plan),
        **{
            k: plan[k]
            for k in (
                "reused_unique_requests",
                "maximum_provider_calls",
                "maximum_additional_reserved_cost_usd",
                "maximum_combined_reserved_cost_usd",
                "cost_ceiling_usd",
            )
        },
        **{
            k: analysis[k]
            for k in (
                "strict_parsed_unique_requests",
                "semantic_assessable_unique_requests",
                "decoded_noncanonical_unique_requests",
            )
        },
        "provider_calls_executed": 0,
        "predecessor_mutated": False,
        "provider_contract_changed": False,
    }


def checked_policy_continuation(
    *, predecessor: Path, directory: Path, confirm_sha256: str | None = None
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, dict[str, Any]]]:
    _private_dir(directory)
    _check_disjoint(predecessor, directory)
    expected, cases, records = _continuation_plan(predecessor)
    plan = _read(directory / "plan.json")
    analysis, audits = _analysis(cases, records)
    if (
        plan != expected
        or _read(directory / "preflight-analysis.json") != analysis
        or _read(directory / "preflight-decoding-audit.json") != audits
    ):
        raise ValueError("continuation plan, predecessor, decoder or preflight changed")
    if confirm_sha256 is not None and canonical_execution_sha256(plan) != confirm_sha256:
        raise ValueError("approved continuation plan digest differs")
    return plan, cases, records


def execute_policy_continuation(
    *,
    predecessor: Path,
    directory: Path,
    confirm_sha256: str,
    caller: DevelopmentCaller,
    progress: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    plan, cases, reused = checked_policy_continuation(
        predecessor=predecessor, directory=directory, confirm_sha256=confirm_sha256
    )
    if (directory / "lease.json").exists() or (directory / "results").exists():
        raise ValueError("continuation attempt already exists; verify instead of replaying")
    _write(
        directory / "lease.json",
        {"plan_sha256": confirm_sha256, "maximum_calls": plan["maximum_provider_calls"]},
    )
    result_dir = directory / "results"
    result_dir.mkdir(mode=0o700)
    failures = 0
    remaining = [r for r in _requests(cases) if r["request_id"] not in reused]
    for index, request in enumerate(remaining, 1):
        call = caller.invoke(
            prompt=PROMPTS[request["policy"]], payload=request["payload"], schema=decision_schema()
        )
        _check_call_resources(call)
        _write(
            result_dir / f"{request['request_id']}.json",
            {
                **{k: request[k] for k in ("request_id", "policy", "context_sha256")},
                "status": "parsed" if _parse(call) is not None else "invalid_or_provider_failure",
                "call": call.model_dump(mode="json"),
            },
        )
        output = decode_policy_call(call)
        failures = failures + 1 if output.decision is None else 0
        if progress is not None:
            progress(
                {
                    "status": "policy_continuation_progress",
                    "new_completed_requests": index,
                    "maximum_new_requests": len(remaining),
                    "total_completed_requests": len(reused) + index,
                }
            )
        if failures == 3:
            break
    analysis, audits, receipt = _rebuild_continuation(predecessor, directory, plan, cases, reused)
    _write(directory / "analysis.json", analysis)
    _write(directory / "decoding-audit.json", audits)
    _write(directory / "receipt.json", receipt)
    return receipt


def _rebuild_continuation(
    predecessor: Path,
    directory: Path,
    plan: dict[str, Any],
    cases: list[dict[str, Any]],
    reused: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    remaining = [r for r in _requests(cases) if r["request_id"] not in reused]
    new = _load_records(directory, remaining)
    failures = 0
    for index, request in enumerate(remaining[: len(new)], 1):
        call = DevelopmentCall.model_validate_json(json.dumps(new[request["request_id"]]["call"]))
        failures = failures + 1 if decode_policy_call(call).decision is None else 0
        if failures == 3 and index != len(new):
            raise ValueError("continuation ledger bypassed the three-failure stop")
    if len(new) != len(remaining) and failures != 3:
        raise ValueError("incomplete continuation does not end at the failure stop")
    if _manifest(predecessor) != plan["predecessor_manifest"]:
        raise ValueError("predecessor changed during continuation")
    records = {**reused, **new}
    analysis, audits = _analysis(cases, records)
    spent = round(sum(r["call"]["estimated_cost_usd"] for r in records.values()), 6)
    if spent > COST_CEILING_USD:
        raise ValueError("combined provider cost exceeds the original ceiling")
    receipt = {
        "schema_version": "evidence-bounded-policy-continuation-receipt/v1",
        "status": "continuation_terminalized"
        if len(new) == len(remaining)
        else "continuation_stopped_technical",
        "plan_sha256": canonical_execution_sha256(plan),
        "predecessor_receipt_sha256": plan["predecessor_receipt_sha256"],
        "lease_sha256": file_sha256(directory / "lease.json"),
        "combined_results_sha256": canonical_execution_sha256(records),
        "analysis_sha256": canonical_execution_sha256(analysis),
        "decoding_audit_sha256": canonical_execution_sha256(audits),
        "reused_unique_requests": len(reused),
        "new_unique_requests": len(new),
        "completed_unique_requests": len(records),
        "not_executed_unique_requests": len(remaining) - len(new),
        "provider_attempt_count": sum(r["call"]["provider_attempted"] for r in records.values()),
        "combined_committed_cost_usd_at_frozen_rates": spent,
        **{
            k: analysis[k]
            for k in (
                "strict_parsed_unique_requests",
                "semantic_assessable_unique_requests",
                "decoded_noncanonical_unique_requests",
            )
        },
        "predecessor_mutated": False,
        "paid_request_replay_executed": False,
        "protected_predictions_used": False,
        "mechanism_admitted": False,
        "raw_artifacts_private": True,
    }
    return analysis, audits, receipt


def verify_policy_continuation(*, predecessor: Path, directory: Path) -> dict[str, Any]:
    plan, cases, reused = checked_policy_continuation(predecessor=predecessor, directory=directory)
    if not (directory / "lease.json").exists():
        if (directory / "results").exists():
            raise ValueError("continuation results exist without a lease")
        analysis, _ = _analysis(cases, reused)
        return {"verification": "pass", **_preflight_summary(plan, analysis)}
    if _read(directory / "lease.json") != {
        "plan_sha256": canonical_execution_sha256(plan),
        "maximum_calls": plan["maximum_provider_calls"],
    }:
        raise ValueError("continuation lease differs")
    analysis, audits, receipt = _rebuild_continuation(predecessor, directory, plan, cases, reused)
    if any(
        _read(directory / name) != value
        for name, value in (
            ("analysis.json", analysis),
            ("decoding-audit.json", audits),
            ("receipt.json", receipt),
        )
    ):
        raise ValueError("persisted continuation replay differs")
    return {"verification": "pass", **receipt}
