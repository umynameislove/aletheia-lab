"""One-use execution and independent verification for V2 qualification."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from pydantic import ValidationError

from aletheia_lab.evaluation.claim_corpus_execution import RepositoryExecutionState
from aletheia_lab.evaluation.claim_corpus_live import (
    NeverCancelled,
    PreparedClaimCorpusRequest,
    SystemMonotonicClock,
)
from aletheia_lab.evaluation.claim_corpus_live_store import ClaimCorpusAttemptStore
from aletheia_lab.evaluation.claim_corpus_terminal_reader import ClaimCorpusTerminalReader
from aletheia_lab.evaluation.claim_validation_v2_qualification import (
    _first_witness_accepted,
    _load_frozen_inputs,
    _openai_policy,
    _source_requests,
    build_qualification_gateway_requests,
    build_qualification_plan,
    load_qualification_authorization,
    publish_qualification_result,
    rehearse_qualification,
    validate_qualification_authorization,
)
from aletheia_lab.evaluation.claim_validation_v2_qualification_contracts import (
    LEASE_SCHEMA_VERSION,
    QUALIFICATION_REQUEST_COUNT,
    RECEIPT_SCHEMA_VERSION,
    ClaimValidationV2QualificationError,
    V2QualificationAuthorization,
    V2QualificationExecutionPlan,
    V2QualificationLease,
    V2QualificationOutcome,
    V2QualificationReceipt,
    V2QualificationRehearsal,
)
from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256
from aletheia_lab.model_gateway import (
    Clock,
    GloballyPacedProviderAdapter,
    OpenAIValidationV2Adapter,
    ProviderAdapter,
    V2RetryController,
    execute_gateway_request,
)


def build_openai_qualification_adapter(
    root: Path, prepared: tuple[PreparedClaimCorpusRequest, ...]
) -> ProviderAdapter:
    """Build the frozen V2 adapter behind a process-wide one-second pace."""

    if len(prepared) != QUALIFICATION_REQUEST_COUNT:
        raise ClaimValidationV2QualificationError(
            "qualification adapter requires exactly seven requests"
        )
    base = OpenAIValidationV2Adapter.from_environment(
        model_policy=prepared[0].request.initial_attempt.model_policy,
        policy=_openai_policy(root),
    )
    return GloballyPacedProviderAdapter(base, minimum_interval_ms=1000)


def _reader(store_root: Path, identity: str) -> ClaimCorpusTerminalReader:
    shard = store_root / "requests" / identity
    return ClaimCorpusTerminalReader(
        root=shard,
        object_root=shard / "objects" / "sha256",
        request_root=shard / "requests",
        terminal_root=shard / "terminal",
        failure_root=shard / "failures",
    )


def _build_receipt(
    root: Path,
    plan: V2QualificationExecutionPlan,
    rehearsal: V2QualificationRehearsal,
    authorization: V2QualificationAuthorization,
    prepared: tuple[PreparedClaimCorpusRequest, ...],
    store: ClaimCorpusAttemptStore,
) -> V2QualificationReceipt:
    amendment, review = _load_frozen_inputs(root)
    probes = {item.qualification_request_sha256: item for item in amendment.qualification_probes}
    sources = _source_requests(root, amendment)
    shards = store.shards(prepared)
    inventories = store.terminal_inventories(shards)
    by_identity = {item.request.initial_attempt.request_identity_sha256: item for item in prepared}
    inventory_by_identity = {item.request_identity_sha256: item for item in inventories}
    if set(by_identity) != set(inventory_by_identity):
        raise ClaimValidationV2QualificationError(
            "qualification terminal store does not match the request census"
        )
    outcomes = []
    for item in prepared:
        identity = item.request.initial_attempt.request_identity_sha256
        inventory = inventory_by_identity[identity]
        probe = probes[item.request_sha256]
        accepted = False
        issue_sha = inventory.issue_sha256
        if inventory.gateway_status == "parsed":
            parsed_payload = _reader(store.root, identity).terminal_parsed_payload(identity)
            if parsed_payload is None or inventory.parsed_response_sha256 is None:
                raise ClaimValidationV2QualificationError(
                    "parsed qualification terminal has no authenticated payload"
                )
            accepted = _first_witness_accepted(
                probe,
                sources[item.request_sha256],
                parsed_payload,
                source_record_sha256=inventory.parsed_response_sha256,
            )
            if not accepted:
                issue_sha = canonical_execution_sha256(
                    {
                        "code": "qualification_first_witness_rejected",
                        "qualification_request_sha256": item.request_sha256,
                        "parsed_response_sha256": inventory.parsed_response_sha256,
                    }
                )
        outcome_payload: dict[str, object] = {
            "variant": probe.variant,
            "qualification_request_sha256": item.request_sha256,
            "gateway_request_identity_sha256": identity,
            "gateway_status": inventory.gateway_status,
            "attempt_count": len(inventory.attempt_outcomes),
            "first_witness_accepted": accepted,
            "issue_sha256": issue_sha,
        }
        outcomes.append(
            V2QualificationOutcome.model_validate(
                {
                    **outcome_payload,
                    "outcome_sha256": canonical_execution_sha256(outcome_payload),
                }
            )
        )
    parsed = sum(item.gateway_status == "parsed" for item in outcomes)
    accepted_count = sum(item.first_witness_accepted for item in outcomes)
    technical = QUALIFICATION_REQUEST_COUNT - parsed
    semantic = parsed - accepted_count
    passed = accepted_count == QUALIFICATION_REQUEST_COUNT and technical == 0 and semantic == 0
    status_counts = dict(sorted(Counter(item.gateway_status for item in outcomes).items()))
    receipt_payload: dict[str, object] = {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "status": (
            "claim_support_validation_v2_qualification_passed"
            if passed
            else "claim_support_validation_v2_qualification_failed"
        ),
        "authorization_sha256": authorization.authorization_sha256,
        "plan_sha256": plan.plan_sha256,
        "rehearsal_sha256": rehearsal.rehearsal_sha256,
        "amendment_sha256": amendment.amendment_sha256,
        "expressiveness_review_sha256": review.review_sha256,
        "source_commit_ref": authorization.source_commit_ref,
        "terminal_store_sha256": store.store_sha256(shards),
        "terminal_request_count": QUALIFICATION_REQUEST_COUNT,
        "parsed_count": parsed,
        "first_witness_accepted_count": accepted_count,
        "technical_failure_count": technical,
        "semantic_validation_failure_count": semantic,
        "provider_attempt_count": sum(len(item.attempt_outcomes) for item in inventories),
        "gateway_status_counts": status_counts,
        "outcomes": tuple(item.model_dump(mode="json") for item in outcomes),
        "synthetic_only": True,
        "admitted_to_corpus": False,
        "provider_calls_executed": True,
        "rerun_forbidden": True,
        "full_cohort_authorization_unlocked": passed,
        "claims_materialized": False,
        "automatic_labels_generated": False,
        "blind_packets_generated": False,
        "human_annotations_collected": False,
        "main_or_sealed_outcomes_opened": False,
    }
    return V2QualificationReceipt.model_validate(
        {
            **receipt_payload,
            "receipt_sha256": canonical_execution_sha256(receipt_payload),
        }
    )


def acquire_qualification_lease(
    run_dir: Path,
    authorization: V2QualificationAuthorization,
) -> V2QualificationLease:
    """Irreversibly register the single authorized qualification attempt."""

    payload: dict[str, object] = {
        "schema_version": LEASE_SCHEMA_VERSION,
        "authorization_sha256": authorization.authorization_sha256,
        "plan_sha256": authorization.plan_sha256,
        "destination_sha256": authorization.destination_sha256,
        "registered_attempts": 1,
    }
    lease = V2QualificationLease.model_validate(
        {**payload, "lease_sha256": canonical_execution_sha256(payload)}
    )
    publish_qualification_result(run_dir / "lease.json", lease)
    return lease


def execute_qualification(
    root: Path,
    *,
    repository_state: RepositoryExecutionState,
    run_dir: Path,
    confirm_authorization_sha256: str,
    adapter: ProviderAdapter,
    clock: Clock | None = None,
    retry_controller: V2RetryController | None = None,
) -> V2QualificationReceipt:
    """Consume the one-use authority and seal all seven terminal outcomes."""

    plan = build_qualification_plan(root, source_commit_ref=repository_state.head_commit)
    rehearsal = rehearse_qualification(root, plan)
    authorization = load_qualification_authorization(run_dir / "authorization.json")
    validate_qualification_authorization(
        authorization,
        plan,
        rehearsal,
        repository_state=repository_state,
        run_dir=run_dir,
    )
    if confirm_authorization_sha256 != authorization.authorization_sha256:
        raise ClaimValidationV2QualificationError(
            "qualification authorization confirmation differs"
        )
    if any((run_dir / name).exists() for name in ("lease.json", "attempt-store", "receipt.json")):
        raise ClaimValidationV2QualificationError(
            "qualification attempt already started; verify it and never restart"
        )
    prepared = build_qualification_gateway_requests(root, plan, authorization)
    acquire_qualification_lease(run_dir, authorization)
    active_clock = clock or SystemMonotonicClock()
    controller = retry_controller or V2RetryController()
    store = ClaimCorpusAttemptStore(run_dir / "attempt-store", clock=active_clock)
    shards = store.shards(prepared)
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
    receipt = _build_receipt(root, plan, rehearsal, authorization, prepared, store)
    publish_qualification_result(run_dir / "receipt.json", receipt)
    return receipt


def verify_completed_qualification(
    root: Path,
    *,
    repository_state: RepositoryExecutionState,
    run_dir: Path,
) -> V2QualificationReceipt:
    """Rebuild the receipt from authenticated immutable request shards."""

    authorization = load_qualification_authorization(run_dir / "authorization.json")
    plan = build_qualification_plan(root, source_commit_ref=repository_state.head_commit)
    rehearsal = rehearse_qualification(root, plan)
    validate_qualification_authorization(
        authorization,
        plan,
        rehearsal,
        repository_state=repository_state,
        run_dir=run_dir,
    )
    prepared = build_qualification_gateway_requests(root, plan, authorization)
    expected_lease_payload: dict[str, object] = {
        "schema_version": LEASE_SCHEMA_VERSION,
        "authorization_sha256": authorization.authorization_sha256,
        "plan_sha256": authorization.plan_sha256,
        "destination_sha256": authorization.destination_sha256,
        "registered_attempts": 1,
    }
    expected_lease = V2QualificationLease.model_validate(
        {
            **expected_lease_payload,
            "lease_sha256": canonical_execution_sha256(expected_lease_payload),
        }
    )
    try:
        actual_lease = V2QualificationLease.model_validate_json(
            (run_dir / "lease.json").read_bytes()
        )
    except (OSError, ValidationError) as exc:
        raise ClaimValidationV2QualificationError(
            "qualification lease is unavailable or invalid"
        ) from exc
    if actual_lease != expected_lease:
        raise ClaimValidationV2QualificationError("qualification lease differs from authorization")
    store = ClaimCorpusAttemptStore(run_dir / "attempt-store", clock=SystemMonotonicClock())
    expected = _build_receipt(root, plan, rehearsal, authorization, prepared, store)
    try:
        actual = V2QualificationReceipt.model_validate_json((run_dir / "receipt.json").read_bytes())
    except (OSError, ValidationError) as exc:
        raise ClaimValidationV2QualificationError(
            "qualification receipt is unavailable or invalid"
        ) from exc
    if actual != expected:
        raise ClaimValidationV2QualificationError(
            "qualification receipt differs from authenticated terminal state"
        )
    return expected


__all__ = [
    "acquire_qualification_lease",
    "build_openai_qualification_adapter",
    "execute_qualification",
    "verify_completed_qualification",
]
