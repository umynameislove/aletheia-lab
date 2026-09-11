"""One-use synthetic V3 qualification with independent read-only replay."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from aletheia_lab.evaluation.claim_corpus_live import NeverCancelled, SystemMonotonicClock
from aletheia_lab.evaluation.claim_corpus_live_store import (
    ClaimCorpusAttemptStore,
    verified_complete_claim_corpus_store_sha256,
)
from aletheia_lab.evaluation.claim_corpus_terminal_reader import ClaimCorpusTerminalReader
from aletheia_lab.evaluation.claim_evidence_semantics import ModelVisibleEvidenceContext
from aletheia_lab.evaluation.claim_validation_v2_qualification import _openai_policy
from aletheia_lab.evaluation.claim_validation_v3_design import (
    build_probes,
    reduce_relations,
)
from aletheia_lab.evaluation.claim_validation_v3_qualification import (
    FALSE_FLAGS,
    prepare_requests,
    publish,
    read_document,
    seal,
    source_roundtrip,
    validate_authority,
)
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
        model_policy=prepared[0].request.initial_attempt.model_policy, policy=_openai_policy(root)
    )
    return GloballyPacedProviderAdapter(base, minimum_interval_ms=1000)


def _reader(store: Path, identity: str) -> ClaimCorpusTerminalReader:
    shard = store / "requests" / identity
    return ClaimCorpusTerminalReader(
        root=shard,
        object_root=shard / "objects/sha256",
        request_root=shard / "requests",
        terminal_root=shard / "terminal",
        failure_root=shard / "failures",
    )


def _accepted(root: Path, probe: dict[str, Any], payload: dict[str, Any] | None) -> bool:
    if payload is None:
        return False
    if probe["kind"] == "source":
        return source_roundtrip(root, probe, payload)
    context = ModelVisibleEvidenceContext.model_validate_json(json.dumps(probe["context"]))
    try:
        reduce_relations(payload, 2, [i.evidence_id for i in context.items])
    except ValueError:
        return False

    def key(row: dict[str, Any]) -> tuple[int, str]:
        return row["part"], row["evidence_id"]

    return sorted(payload["relations"], key=key) == sorted(probe["expected"]["relations"], key=key)


def rebuild_receipt(
    root: Path, run: Path, plan: dict[str, Any], auth: dict[str, Any]
) -> dict[str, Any]:
    validate_authority(root, run, plan, auth, completed=True)
    lease = read_document(run / "lease.json", "lease_sha256")
    expected_lease = seal(
        {"authorization_sha256": auth["authorization_sha256"], "plan_sha256": plan["plan_sha256"]},
        "lease_sha256",
    )
    if lease != expected_lease:
        raise ValueError("qualification lease binding mismatch")
    prepared = prepare_requests(root, plan, auth)
    store_hash = verified_complete_claim_corpus_store_sha256(run / "attempt-store", prepared)
    rows = []
    for request, probe in zip(prepared, build_probes(root), strict=True):
        identity = request.request.initial_attempt.request_identity_sha256
        reader = _reader(run / "attempt-store", identity)
        inventory = reader.terminal_inventory(identity)
        payload = reader.terminal_parsed_payload(identity)
        records = reader.terminal_attempt_records(identity)
        diagnostics = [record.model_dump(mode="json") for record in records]
        # Full diagnostics remain in the store; summaries contain only categories.
        categories = Counter(
            str(d.get("provider_failure_category"))
            for d in diagnostics
            if d.get("provider_failure_category") is not None
        )
        rows.append(
            {
                "probe_sha256": probe["probe_sha256"],
                "kind": probe["kind"],
                "gateway_request_identity_sha256": identity,
                "gateway_status": inventory.gateway_status,
                "accepted": inventory.gateway_status == "parsed"
                and _accepted(root, probe, payload),
                "attempt_count": len(records),
                "failure_categories": dict(categories),
            }
        )
    parsed = sum(row["gateway_status"] == "parsed" for row in rows)
    accepted = sum(row["accepted"] for row in rows)
    passed = parsed == accepted == 33
    return seal(
        {
            "schema_version": "claim-support-v3-qualification-receipt/1",
            "status": "v3_qualification_passed" if passed else "v3_qualification_failed",
            "plan_sha256": plan["plan_sha256"],
            "authorization_sha256": auth["authorization_sha256"],
            "terminal_store_sha256": store_hash,
            "terminal_request_count": len(rows),
            "parsed_count": parsed,
            "accepted_count": accepted,
            "technical_failure_count": len(rows) - parsed,
            "semantic_failure_count": parsed - accepted,
            "provider_attempt_count": sum(row["attempt_count"] for row in rows),
            "outcomes": rows,
            "source_commit_ref": plan["source_commit_ref"],
            "protocol_sha256": plan["protocol_sha256"],
            "synthetic_only": True,
            "provider_calls_executed": True,
            "rerun_forbidden": True,
            "cohort_planning_unlocked": passed,
            **FALSE_FLAGS,
        },
        "receipt_sha256",
    )


def execute(
    root: Path,
    run: Path,
    plan: dict[str, Any],
    auth: dict[str, Any],
    *,
    confirmation: str,
    adapter: ProviderAdapter,
    clock: Clock | None = None,
    retry: V2RetryController | None = None,
) -> dict[str, Any]:
    validate_authority(root, run, plan, auth)
    if confirmation != auth["authorization_sha256"]:
        raise ValueError("authorization confirmation differs")
    if any((run / name).exists() for name in ("lease.json", "attempt-store", "receipt.json")):
        raise ValueError("attempt already registered; verify, never restart")
    prepared = prepare_requests(root, plan, auth)
    lease = seal(
        {"authorization_sha256": auth["authorization_sha256"], "plan_sha256": plan["plan_sha256"]},
        "lease_sha256",
    )
    # O_EXCL prevents two simultaneous executors from spending the same authority.
    write_new_file(run / "lease.json", (canonical_project_json(lease) + "\n").encode())
    active_clock = clock or SystemMonotonicClock()
    store = ClaimCorpusAttemptStore(run / "attempt-store", clock=active_clock)
    shards = store.shards(prepared)
    controller = retry or V2RetryController()
    for item in prepared:
        shard = shards[item.request.initial_attempt.request_identity_sha256]
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
    receipt = rebuild_receipt(root, run, plan, auth)
    publish(run / "receipt.json", receipt)
    return receipt


def verify(root: Path, run: Path, plan: dict[str, Any], auth: dict[str, Any]) -> dict[str, Any]:
    actual = read_document(run / "receipt.json", "receipt_sha256")
    if actual != rebuild_receipt(root, run, plan, auth):
        raise ValueError("receipt differs from independent terminal replay")
    return actual
