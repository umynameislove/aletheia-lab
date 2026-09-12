"""Single-use V3.2 relation cohort execution with independent replay."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from aletheia_lab.evaluation.claim_corpus_live import NeverCancelled, SystemMonotonicClock
from aletheia_lab.evaluation.claim_corpus_live_store import (
    ClaimCorpusAttemptStore,
    verified_complete_claim_corpus_store_sha256,
)
from aletheia_lab.evaluation.claim_corpus_terminal_reader import ClaimCorpusTerminalReader
from aletheia_lab.evaluation.claim_support_v3_2_qualification import (
    MINIMUM_PROVIDER_INTERVAL_MS,
)
from aletheia_lab.evaluation.claim_support_v3_2_relation_cohort import (
    PROTECTED_FALSE_FLAGS,
    REQUEST_COUNT,
    build_relation_tasks,
    prepare_requests,
    publish,
    read_document,
    relation_semantic_issue,
    seal,
    validate_authority,
)
from aletheia_lab.evaluation.claim_validation_v2_qualification import _openai_policy
from aletheia_lab.filesystem import write_new_file
from aletheia_lab.model_gateway import (
    Clock,
    GloballyPacedProviderAdapter,
    OpenAIValidationV2Adapter,
    ProviderAdapter,
    V2RetryController,
    execute_gateway_request,
)
from aletheia_lab.project.identity import canonical_project_json


def adapter_for(root: Path, plan: dict[str, Any], auth: dict[str, Any]) -> ProviderAdapter:
    prepared = prepare_requests(root, plan, auth)
    base = OpenAIValidationV2Adapter.from_environment(
        model_policy=prepared[0].request.initial_attempt.model_policy,
        policy=_openai_policy(root),
    )
    return GloballyPacedProviderAdapter(
        base, minimum_interval_ms=MINIMUM_PROVIDER_INTERVAL_MS
    )


def _reader(store: Path, identity: str) -> ClaimCorpusTerminalReader:
    shard = store / "requests" / identity
    return ClaimCorpusTerminalReader(
        root=shard,
        object_root=shard / "objects/sha256",
        request_root=shard / "requests",
        terminal_root=shard / "terminal",
        failure_root=shard / "failures",
    )


def rebuild_receipt(
    root: Path,
    run: Path,
    qualification_run: Path,
    plan: dict[str, Any],
    auth: dict[str, Any],
) -> dict[str, Any]:
    validate_authority(
        root, run, qualification_run, plan, auth, completed=True
    )
    lease = read_document(run / "lease.json", "lease_sha256")
    expected_lease = seal(
        {
            "authorization_sha256": auth["authorization_sha256"],
            "plan_sha256": plan["plan_sha256"],
        },
        "lease_sha256",
    )
    if lease != expected_lease:
        raise ValueError("relation cohort lease binding mismatch")
    prepared = prepare_requests(root, plan, auth)
    store_hash = verified_complete_claim_corpus_store_sha256(
        run / "attempt-store", prepared
    )
    rows: list[dict[str, Any]] = []
    for request, task in zip(prepared, build_relation_tasks(root), strict=True):
        identity = request.request.initial_attempt.request_identity_sha256
        reader = _reader(run / "attempt-store", identity)
        inventory = reader.terminal_inventory(identity)
        payload = reader.terminal_parsed_payload(identity)
        records = reader.terminal_attempt_records(identity)
        failure_categories = Counter(
            record.provider_failure_category
            for record in records
            if record.provider_failure_category is not None
        )
        semantic_issue = (
            relation_semantic_issue(task, payload)
            if inventory.gateway_status == "parsed"
            else None
        )
        rows.append(
            {
                "task_sha256": task["task_sha256"],
                "source_instance_sha256": task["source_instance_sha256"],
                "execution_ordinal": task["execution_ordinal"],
                "gateway_request_identity_sha256": identity,
                "gateway_status": inventory.gateway_status,
                "structurally_accepted": inventory.gateway_status == "parsed"
                and semantic_issue is None,
                "semantic_issue_code": semantic_issue,
                "attempt_count": len(records),
                "failure_categories": dict(failure_categories),
            }
        )
    parsed = sum(row["gateway_status"] == "parsed" for row in rows)
    accepted = sum(row["structurally_accepted"] for row in rows)
    semantic_issues = Counter(
        row["semantic_issue_code"]
        for row in rows
        if row["semantic_issue_code"] is not None
    )
    complete_without_failures = accepted == REQUEST_COUNT
    return seal(
        {
            "schema_version": "claim-support-v3.2-relation-cohort-receipt/1",
            "status": (
                "v3_2_relation_cohort_execution_complete"
                if complete_without_failures
                else "v3_2_relation_cohort_execution_complete_with_failures"
            ),
            "plan_sha256": plan["plan_sha256"],
            "protocol_sha256": plan["protocol_sha256"],
            "authorization_sha256": auth["authorization_sha256"],
            "qualification_receipt_sha256": plan["qualification_receipt_sha256"],
            "terminal_store_sha256": store_hash,
            "terminal_request_count": len(rows),
            "parsed_count": parsed,
            "structurally_accepted_count": accepted,
            "technical_failure_count": len(rows) - parsed,
            "semantic_failure_count": parsed - accepted,
            "semantic_issue_counts": dict(semantic_issues),
            "provider_attempt_count": sum(row["attempt_count"] for row in rows),
            "outcomes": rows,
            "source_commit_ref": plan["source_commit_ref"],
            "failures_preserved_in_denominator": True,
            "provider_calls_executed": True,
            "rerun_forbidden": True,
            "relation_execution_authorized": True,
            "relation_closeout_unlocked": True,
            **PROTECTED_FALSE_FLAGS,
        },
        "receipt_sha256",
    )


def execute(
    root: Path,
    run: Path,
    qualification_run: Path,
    plan: dict[str, Any],
    auth: dict[str, Any],
    *,
    confirmation: str,
    adapter: ProviderAdapter,
    clock: Clock | None = None,
    retry: V2RetryController | None = None,
) -> dict[str, Any]:
    validate_authority(root, run, qualification_run, plan, auth)
    if confirmation != auth["authorization_sha256"]:
        raise ValueError("authorization confirmation differs")
    if any(
        (run / name).exists() for name in ("lease.json", "attempt-store", "receipt.json")
    ):
        raise ValueError("attempt already registered; verify, never restart")
    prepared = prepare_requests(root, plan, auth)
    lease = seal(
        {
            "authorization_sha256": auth["authorization_sha256"],
            "plan_sha256": plan["plan_sha256"],
        },
        "lease_sha256",
    )
    write_new_file(run / "lease.json", (canonical_project_json(lease) + "\n").encode())
    active_clock = clock or SystemMonotonicClock()
    store = ClaimCorpusAttemptStore(run / "attempt-store", clock=active_clock)
    shards = store.shards(prepared)
    controller = retry or V2RetryController()
    for item in prepared:
        identity = item.request.initial_attempt.request_identity_sha256
        shard = shards[identity]
        shard.prepare(item.request)
        shard.start(item.request)
        result = execute_gateway_request(
            item.request,
            adapter=adapter,
            clock=active_clock,
            cancellation=NeverCancelled(),
            retry_controller=controller,
        )
        for attempt in result.attempts:
            shard.record_attempt(item.request, attempt)
        if result.raw_response is not None:
            shard.record_response(item.request, result)
        shard.record_parsed_or_failed(item.request, result)
        shard.mark_closeout_pending(item.request, result)
        shard.publish_terminal(item.request, result)
    receipt = rebuild_receipt(root, run, qualification_run, plan, auth)
    publish(run / "receipt.json", receipt)
    return receipt


def verify(
    root: Path,
    run: Path,
    qualification_run: Path,
    plan: dict[str, Any],
    auth: dict[str, Any],
) -> dict[str, Any]:
    actual = read_document(run / "receipt.json", "receipt_sha256")
    if actual != rebuild_receipt(root, run, qualification_run, plan, auth):
        raise ValueError("relation cohort receipt differs from independent terminal replay")
    return actual


__all__ = ["adapter_for", "execute", "rebuild_receipt", "verify"]
