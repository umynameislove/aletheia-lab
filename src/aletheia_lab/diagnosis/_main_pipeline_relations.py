"""Resumable blind relation-scoring stage for the diagnosis main pipeline."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from aletheia_lab.diagnosis._main_pipeline_budget import SharedProviderBudget
from aletheia_lab.diagnosis._main_pipeline_contracts import DiagnosisMainPipelineError
from aletheia_lab.evaluation.claim_corpus_live import NeverCancelled
from aletheia_lab.evaluation.claim_corpus_live_store import ClaimCorpusAttemptStore
from aletheia_lab.evaluation.claim_corpus_terminal_reader import ClaimCorpusTerminalReader
from aletheia_lab.evaluation.claim_evidence_semantics import (
    ClaimRelationAssignmentRequest,
    ClaimRelationAssignmentResponse,
    parse_relation_assignment,
)
from aletheia_lab.evaluation.claim_relation_provider import (
    PreparedRelationRequest,
    RelationAuthorizationBinding,
    build_relation_gateway_requests_for_assignments,
)
from aletheia_lab.evaluation.diagnosis_main_materialization import (
    DiagnosisMainRelationResult,
    DiagnosisMainRelationResults,
    DiagnosisMainScoringPreparation,
)
from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256
from aletheia_lab.model_gateway import (
    Clock,
    ProviderAdapter,
    ProviderBinding,
    ProviderCall,
    ProviderEnvelope,
    RawResponseArtifact,
    UsageMetadata,
    execute_gateway_request,
)


@dataclass(frozen=True)
class RehearsalRelationAuthorization:
    authorization_ref: str
    authorized_at: str = "1970-01-01T00:00:00Z"


class DeterministicRelationAdapter:
    """Network-incapable relation adapter used only by the offline preflight."""

    def __init__(self, binding: ProviderBinding) -> None:
        self._binding = binding

    @property
    def binding(self) -> ProviderBinding:
        return self._binding

    def invoke(self, call: ProviderCall) -> ProviderEnvelope:
        payload = json.loads(call.context_json)["payload"]
        evidence = payload["visible_evidence"]
        decisions = [
            {
                "evidence_id": item["evidence_id"],
                "relation_polarity": "supports" if index == 0 else "neutral",
                "relation_scope": "entire" if index == 0 else "none",
            }
            for index, item in enumerate(evidence)
        ]
        raw = json.dumps({"decisions": decisions}, separators=(",", ":"), sort_keys=True).encode(
            "utf-8"
        )
        return ProviderEnvelope(
            request_identity_sha256=call.request_identity_sha256,
            binding=self.binding,
            provider_attempt_ref=(
                f"ev-{canonical_execution_sha256({'offline_relation': call.attempt_identity_sha256})}"
            ),
            response_mode="structured",
            raw_response=RawResponseArtifact.from_bytes(raw),
            usage=UsageMetadata(
                input_tokens=1,
                output_tokens=1,
                total_tokens=2,
                cost_amount=None,
                cost_currency_ref=None,
            ),
        )


def _assignments(
    preparation: DiagnosisMainScoringPreparation,
) -> tuple[ClaimRelationAssignmentRequest, ...]:
    return tuple(
        claim.relation_request for record in preparation.records for claim in record.claims
    )


def build_pipeline_relation_requests(
    root: Path,
    preparation: DiagnosisMainScoringPreparation,
    *,
    source_commit_ref: str,
    authorization: RelationAuthorizationBinding,
    plan_sha256: str,
) -> tuple[PreparedRelationRequest, ...]:
    assignments = _assignments(preparation)
    if not assignments:
        return ()
    return build_relation_gateway_requests_for_assignments(
        root,
        assignments,
        preparation_sha256=preparation.preparation_sha256,
        plan_sha256=plan_sha256,
        source_commit_ref=source_commit_ref,
        authorization=authorization,
        expected_request_count=len(assignments),
        execution_boundary="diagnosis-main-relation-scoring/v1",
        dataset_scope="diagnosis-main-controlled-census",
    )


def offline_relation_adapter(
    prepared: tuple[PreparedRelationRequest, ...],
) -> DeterministicRelationAdapter | None:
    if not prepared:
        return None
    return DeterministicRelationAdapter(
        ProviderBinding.from_model_policy(prepared[0].request.initial_attempt.model_policy)
    )


def _reader(store_root: Path, identity: str) -> ClaimCorpusTerminalReader:
    shard = store_root / "requests" / identity
    return ClaimCorpusTerminalReader(
        root=shard,
        object_root=shard / "objects" / "sha256",
        request_root=shard / "requests",
        terminal_root=shard / "terminal",
        failure_root=shard / "failures",
    )


def _read_result(
    item: PreparedRelationRequest,
    store_root: Path,
) -> DiagnosisMainRelationResult:
    identity = item.request.initial_attempt.request_identity_sha256
    reader = _reader(store_root, identity)
    inventory = reader.terminal_inventory(identity)
    response: ClaimRelationAssignmentResponse | None = None
    if inventory.gateway_status == "parsed":
        parsed = reader.terminal_parsed_payload(identity)
        if parsed is None:
            raise DiagnosisMainPipelineError("parsed relation terminal has no payload")
        try:
            response = parse_relation_assignment(item.assignment, parsed)
        except ValueError:
            response = None
    return DiagnosisMainRelationResult(
        assignment_request_sha256=item.assignment.assignment_request_sha256,
        terminal_status="parsed" if response is not None else "technical_failure",
        response=response,
        issue_code=(
            None
            if response is not None
            else (
                "relation_semantic_validation_failed"
                if inventory.gateway_status == "parsed"
                else f"gateway_{inventory.gateway_status}"
            )
        ),
    )


def execute_pipeline_relation_stage(
    *,
    prepared: tuple[PreparedRelationRequest, ...],
    preparation: DiagnosisMainScoringPreparation,
    store_root: Path,
    adapter: ProviderAdapter | None,
    clock: Clock,
) -> tuple[DiagnosisMainRelationResults, dict[str, int]]:
    """Execute only absent requests; terminal requests replay and partial ones stop."""

    if len(prepared) != preparation.relation_request_count:
        raise DiagnosisMainPipelineError("relation request census differs from preparation")
    if not prepared:
        payload: dict[str, object] = {
            "schema_version": "diagnosis-main-relation-results/v1",
            "execution_mode": preparation.execution_mode,
            "preparation_sha256": preparation.preparation_sha256,
            "provider_calls_executed": False,
            "registered_relation_attempts_consumed": 0,
            "results": (),
        }
        results = DiagnosisMainRelationResults.model_validate(
            {**payload, "results_sha256": canonical_execution_sha256(payload)}
        )
        return results, {}
    if adapter is None:
        raise DiagnosisMainPipelineError("relation stage requires an explicit adapter")
    store = ClaimCorpusAttemptStore(store_root, clock=clock)
    shards = store.shards(prepared)  # type: ignore[arg-type]
    states = {identity: shard.current_state(identity) for identity, shard in shards.items()}
    if any(state not in {None, "terminal_published"} for state in states.values()):
        raise DiagnosisMainPipelineError(
            "incomplete relation request forbids automatic provider replay"
        )
    for item in prepared:
        request = item.request
        identity = request.initial_attempt.request_identity_sha256
        shard = shards[identity]
        state = shard.current_state(identity)
        if state == "terminal_published":
            continue
        if state is not None:
            raise AssertionError("pre-scanned relation state changed unexpectedly")
        shard.prepare(request)
        shard.start(request)
        result = execute_gateway_request(
            request,
            adapter=adapter,
            clock=clock,
            cancellation=NeverCancelled(),
        )
        for attempt in result.attempts:
            shard.record_attempt(request, attempt)
        if result.raw_response is not None:
            shard.record_response(request, result)
        shard.record_parsed_or_failed(request, result)
        shard.mark_closeout_pending(request, result)
        shard.publish_terminal(request, result)
    inventories = store.terminal_inventories(shards)
    counts: dict[str, int] = {
        str(status): count
        for status, count in sorted(Counter(item.gateway_status for item in inventories).items())
    }
    ordered = tuple(
        sorted(
            (_read_result(item, store_root) for item in prepared),
            key=lambda item: item.assignment_request_sha256,
        )
    )
    authorized = preparation.execution_mode == "authorized_execution"
    payload = {
        "schema_version": "diagnosis-main-relation-results/v1",
        "execution_mode": preparation.execution_mode,
        "preparation_sha256": preparation.preparation_sha256,
        "provider_calls_executed": authorized,
        "registered_relation_attempts_consumed": int(authorized),
        "results": tuple(item.model_dump(mode="json") for item in ordered),
    }
    results = DiagnosisMainRelationResults.model_validate(
        {
            **payload,
            "results": ordered,
            "results_sha256": canonical_execution_sha256(payload),
        }
    )
    return results, counts


def restore_relation_budget(
    prepared: tuple[PreparedRelationRequest, ...],
    store_root: Path,
    budget: SharedProviderBudget,
) -> None:
    """Restore cost from sealed relation requests before dispatching a resume."""

    if not store_root.exists():
        return
    for item in prepared:
        identity = item.request.initial_attempt.request_identity_sha256
        terminal = store_root / "requests" / identity / "terminal" / f"{identity}.json"
        if terminal.is_file() and not terminal.is_symlink():
            records = _reader(store_root, identity).terminal_attempt_records(identity)
            budget.restore(item.request, records)


__all__ = [
    "DeterministicRelationAdapter",
    "RehearsalRelationAuthorization",
    "build_pipeline_relation_requests",
    "execute_pipeline_relation_stage",
    "offline_relation_adapter",
    "restore_relation_budget",
]
