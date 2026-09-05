"""Fail-closed construction of the development claim-support pool.

The boundary consumes only the sealed diagnosis attempt store and its
independent reconciliation.  It normalizes schema-valid outputs, extracts
already-atomic claims, prepares blind relation requests, and publishes a pool
only after every relation result reconciles.  It never calls a provider,
repairs prose, selects the 200-claim sample, or exposes human/main outcomes.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path

from pydantic import ValidationError

from aletheia_lab.evaluation._attempt_store.contracts import TerminalExecutionInventory
from aletheia_lab.evaluation.claim_corpus_adapters import normalize_variant_output
from aletheia_lab.evaluation.claim_corpus_construction_contracts import (
    POOL_CLOSEOUT_SCHEMA_VERSION,
    PREPARATION_SCHEMA_VERSION,
    RELATION_RESULT_BUNDLE_SCHEMA_VERSION,
    ClaimNormalizationRecord,
    ClaimPoolConstructionError,
    ClaimPoolPreparation,
    ClaimPoolPublicationCloseout,
    ClaimRelationResult,
    ClaimRelationResultBundle,
    NormalizationStatus,
    ProviderDiagnosisOutput,
)
from aletheia_lab.evaluation.claim_corpus_contracts import (
    ClaimCorpusContractError,
    ClaimCorpusRequest,
    ClaimCorpusRequestCensus,
    ClaimSupportCorpusEntry,
    DiagnosisOutputV2,
)
from aletheia_lab.evaluation.claim_corpus_execution import (
    ClaimCorpusExecutionAuthorization,
    build_execution_plan,
)
from aletheia_lab.evaluation.claim_corpus_live import (
    ClaimCorpusExecutionLease,
    ClaimCorpusLiveReceipt,
    ClaimCorpusRequestAuthority,
)
from aletheia_lab.evaluation.claim_corpus_materializer import (
    materialize_request_claims,
    reconcile_materialized_entries,
)
from aletheia_lab.evaluation.claim_corpus_protocol import ClaimSupportCorpusProtocol
from aletheia_lab.evaluation.claim_corpus_readiness import REQUEST_CENSUS_PATH
from aletheia_lab.evaluation.claim_corpus_reconciliation import (
    ClaimCorpusRequestReconciliationReceipt,
    ClaimCorpusReserveDecisionReceipt,
    reconcile_claim_corpus_execution,
)
from aletheia_lab.evaluation.claim_corpus_store import ClaimCorpusArtifactStore
from aletheia_lab.evaluation.claim_corpus_terminal_reader import (
    ClaimCorpusTerminalReader,
)
from aletheia_lab.evaluation.claim_evidence_census import ObservedEvidenceCensus
from aletheia_lab.evaluation.claim_evidence_semantics import (
    ClaimRelationAssignmentRequest,
    ClaimRelationAssignmentResponse,
    build_relation_assignment_request,
    load_evidence_semantics_policy,
)
from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256
from aletheia_lab.model_gateway.contracts import TerminalStatus
from aletheia_lab.project.identity import canonical_project_json


def normalize_provider_output(
    request: ClaimCorpusRequest,
    payload: Mapping[str, object],
    *,
    source_record_sha256: str,
) -> DiagnosisOutputV2:
    """Convert only the registered structured envelope to diagnosis-output/2."""

    try:
        source = ProviderDiagnosisOutput.model_validate_json(
            canonical_project_json(dict(payload))
        )
    except ValidationError as exc:
        raise ClaimCorpusContractError(
            "stored provider output is incompatible with the frozen schema"
        ) from exc
    target_payload: dict[str, object] = {
        "schema_version": "diagnosis-output/2",
        "output_status": source.output_status,
        "atomic_claims": tuple(
            item.model_dump(mode="python") for item in source.atomic_claims
        ),
        "abstention_reason": (
            source.abstention_reason if source.output_status == "abstained" else None
        ),
        "parse_failure_code": None,
        "source_record_sha256": source_record_sha256,
    }
    target_payload["output_sha256"] = canonical_execution_sha256(target_payload)
    if request.variant == "B0":
        adapter_payload = {
            "schema_version": "deterministic-diagnosis/1",
            "output_status": target_payload["output_status"],
            "rule_claims": target_payload["atomic_claims"],
            "abstention_reason": target_payload["abstention_reason"],
            "parse_failure_code": target_payload["parse_failure_code"],
            "source_record_sha256": target_payload["source_record_sha256"],
            "output_sha256": target_payload["output_sha256"],
        }
    else:
        adapter_payload = target_payload
    return normalize_variant_output(request.variant, adapter_payload)


def verify_construction_inputs(
    root: Path,
    *,
    store_root: Path,
    authorization: ClaimCorpusExecutionAuthorization,
    lease: ClaimCorpusExecutionLease,
    live_receipt: ClaimCorpusLiveReceipt,
    reserve_receipt: ClaimCorpusReserveDecisionReceipt,
    reconciliation_receipt: ClaimCorpusRequestReconciliationReceipt,
    evidence_census: ObservedEvidenceCensus,
) -> None:
    """Reproduce CSV-19/20 before any output content is normalized."""

    fresh_reserve, fresh_reconciliation = reconcile_claim_corpus_execution(
        root,
        store_root=store_root,
        authorization=authorization,
        lease=lease,
        live_receipt=live_receipt,
        evidence_census=evidence_census,
    )
    if fresh_reserve != reserve_receipt or fresh_reconciliation != reconciliation_receipt:
        raise ClaimPoolConstructionError(
            "construction inputs differ from independent execution reconciliation"
        )
    if not fresh_reconciliation.ready_for_output_normalization:
        raise ClaimPoolConstructionError("execution is not ready for output normalization")


def build_claim_pool_preparation(
    root: Path,
    *,
    store_root: Path,
    authorization: ClaimCorpusExecutionAuthorization,
    lease: ClaimCorpusExecutionLease,
    live_receipt: ClaimCorpusLiveReceipt,
    reserve_receipt: ClaimCorpusReserveDecisionReceipt,
    reconciliation_receipt: ClaimCorpusRequestReconciliationReceipt,
    evidence_census: ObservedEvidenceCensus,
) -> ClaimPoolPreparation:
    """Run CSV-21/22 and freeze the exact blind relation-request census."""

    checked_root = root.resolve()
    verify_construction_inputs(
        checked_root,
        store_root=store_root,
        authorization=authorization,
        lease=lease,
        live_receipt=live_receipt,
        reserve_receipt=reserve_receipt,
        reconciliation_receipt=reconciliation_receipt,
        evidence_census=evidence_census,
    )
    census = ClaimCorpusRequestCensus.model_validate_json(
        (checked_root / REQUEST_CENSUS_PATH).read_bytes()
    )
    requests = census.primary_requests
    evidence_by_key = {
        (item.family_id, item.evidence_condition): item
        for item in evidence_census.bindings
    }
    identity_by_request = _load_authority_identities(store_root, requests)
    records: list[ClaimNormalizationRecord] = []
    relation_requests: list[ClaimRelationAssignmentRequest] = []
    for request in requests:
        identity = identity_by_request[request.request_sha256]
        inventory, parsed = _read_terminal(store_root, identity)
        if inventory.gateway_status != "parsed":
            record = _normalization_record(
                request=request,
                identity=identity,
                gateway_status=inventory.gateway_status,
                status="technical_failure",
                source_record_sha256=None,
                issue_sha256=inventory.issue_sha256,
                output=None,
                relation_requests=(),
                blocker_code="technical_terminal",
            )
            records.append(record)
            continue
        if parsed is None or inventory.parsed_response_sha256 is None:
            raise ClaimPoolConstructionError("parsed terminal lacks its canonical payload")
        try:
            output = normalize_provider_output(
                request,
                parsed,
                source_record_sha256=inventory.parsed_response_sha256,
            )
        except ClaimCorpusContractError:
            records.append(
                _normalization_record(
                    request=request,
                    identity=identity,
                    gateway_status=inventory.gateway_status,
                    status="schema_rejected",
                    source_record_sha256=inventory.parsed_response_sha256,
                    issue_sha256=None,
                    output=None,
                    relation_requests=(),
                    blocker_code="provider_schema_incompatible",
                )
            )
            continue
        output_requests: tuple[ClaimRelationAssignmentRequest, ...] = ()
        if output.output_status == "completed":
            binding = evidence_by_key[(request.family_id, request.evidence_condition)]
            try:
                output_requests = tuple(
                    build_relation_assignment_request(
                        source_output_sha256=output.output_sha256,
                        claim_local_id=claim.claim_local_id,
                        claim_text=claim.claim_text,
                        claim_type=claim.claim_type,
                        cited_evidence_ids=claim.visible_evidence_ids,
                        evidence_binding=binding,
                    )
                    for claim in output.atomic_claims
                )
            except ClaimCorpusContractError:
                records.append(
                    _normalization_record(
                        request=request,
                        identity=identity,
                        gateway_status=inventory.gateway_status,
                        status="claim_binding_rejected",
                        source_record_sha256=inventory.parsed_response_sha256,
                        issue_sha256=None,
                        output=output,
                        relation_requests=(),
                        blocker_code="claim_evidence_binding_invalid",
                    )
                )
                continue
        relation_requests.extend(output_requests)
        records.append(
            _normalization_record(
                request=request,
                identity=identity,
                gateway_status=inventory.gateway_status,
                status="normalized",
                source_record_sha256=inventory.parsed_response_sha256,
                issue_sha256=None,
                output=output,
                relation_requests=output_requests,
                blocker_code=None,
            )
        )
    policy = load_evidence_semantics_policy(checked_root)
    outputs = tuple(
        item.normalized_output
        for item in records
        if item.normalized_output is not None
    )
    statuses = Counter(item.normalization_status for item in records)
    payload: dict[str, object] = {
        "schema_version": PREPARATION_SCHEMA_VERSION,
        "source_commit_ref": authorization.source_commit_ref,
        "authorization_sha256": authorization.authorization_sha256,
        "execution_plan_sha256": build_execution_plan(checked_root).plan_sha256,
        "live_receipt_sha256": live_receipt.receipt_sha256,
        "reconciliation_receipt_sha256": reconciliation_receipt.receipt_sha256,
        "reserve_receipt_sha256": reserve_receipt.receipt_sha256,
        "evidence_census_sha256": evidence_census.census_sha256,
        "evidence_semantics_policy_sha256": policy.policy_sha256,
        "terminal_request_count": 360,
        "parsed_terminal_count": reconciliation_receipt.parsed_terminal_count,
        "technical_failure_terminal_count": (
            reconciliation_receipt.technical_failure_terminal_count
        ),
        "normalized_output_count": len(outputs),
        "normalization_rejection_count": (
            statuses["schema_rejected"] + statuses["claim_binding_rejected"]
        ),
        "completed_output_count": sum(
            item.output_status == "completed" for item in outputs
        ),
        "abstained_output_count": sum(
            item.output_status == "abstained" for item in outputs
        ),
        "claim_candidate_count": sum(len(item.atomic_claims) for item in outputs),
        "relation_request_count": len(relation_requests),
        "records": tuple(item.model_dump(mode="json") for item in records),
        "relation_requests": tuple(
            item.model_dump(mode="json") for item in relation_requests
        ),
        "failures_preserved_in_denominator": True,
        "free_text_recovery_performed": False,
        "automatic_labels_generated": False,
        "corpus_entries_materialized": False,
        "blind_packets_generated": False,
        "human_annotations_collected": False,
        "main_or_sealed_outcomes_opened": False,
    }
    return ClaimPoolPreparation.model_validate(
        {
            **payload,
            "records": tuple(records),
            "relation_requests": tuple(relation_requests),
            "preparation_sha256": canonical_execution_sha256(payload),
        }
    )


def build_relation_result_bundle(
    preparation: ClaimPoolPreparation,
    results: Sequence[ClaimRelationResult],
) -> ClaimRelationResultBundle:
    """Bind externally executed relation results to one exact preparation."""

    checked_preparation = ClaimPoolPreparation.model_validate(
        preparation.model_dump(mode="python")
    )
    checked_results = tuple(
        ClaimRelationResult.model_validate(item.model_dump(mode="python"))
        for item in results
    )
    expected = tuple(
        item.assignment_request_sha256
        for item in checked_preparation.relation_requests
    )
    actual = tuple(item.assignment_request_sha256 for item in checked_results)
    if actual != expected:
        raise ClaimPoolConstructionError(
            "relation results do not exactly cover the frozen request census"
        )
    payload: dict[str, object] = {
        "schema_version": RELATION_RESULT_BUNDLE_SCHEMA_VERSION,
        "preparation_sha256": checked_preparation.preparation_sha256,
        "policy_sha256": checked_preparation.evidence_semantics_policy_sha256,
        "results": tuple(item.model_dump(mode="json") for item in checked_results),
        "parsed_count": sum(item.terminal_status == "parsed" for item in checked_results),
        "technical_failure_count": sum(
            item.terminal_status == "technical_failure" for item in checked_results
        ),
        "registered_attempt_count": sum(item.attempt_count for item in checked_results),
        "provider_calls_executed": bool(checked_results),
        "labels_generated": False,
        "human_annotations_collected": False,
        "main_or_sealed_outcomes_opened": False,
    }
    return ClaimRelationResultBundle.model_validate(
        {
            **payload,
            "results": checked_results,
            "bundle_sha256": canonical_execution_sha256(payload),
        }
    )


def publish_claim_pool(
    root: Path,
    *,
    preparation: ClaimPoolPreparation,
    relation_results: ClaimRelationResultBundle,
    store_root: Path,
) -> ClaimPoolPublicationCloseout:
    """Apply CSV-23 and publish CSV-24 only from a complete terminal relation run."""

    checked_preparation = ClaimPoolPreparation.model_validate(
        preparation.model_dump(mode="python")
    )
    checked_results = ClaimRelationResultBundle.model_validate(
        relation_results.model_dump(mode="python")
    )
    policy = load_evidence_semantics_policy(root.resolve())
    if (
        checked_results.preparation_sha256 != checked_preparation.preparation_sha256
        or checked_results.policy_sha256 != policy.policy_sha256
        or checked_preparation.evidence_semantics_policy_sha256 != policy.policy_sha256
    ):
        raise ClaimPoolConstructionError("relation result policy or preparation differs")
    if checked_results.technical_failure_count:
        raise ClaimPoolConstructionError(
            "technical relation failures forbid full-pool publication"
        )
    expected_relation_identities = tuple(
        item.assignment_request_sha256
        for item in checked_preparation.relation_requests
    )
    actual_relation_identities = tuple(
        item.assignment_request_sha256 for item in checked_results.results
    )
    if actual_relation_identities != expected_relation_identities:
        raise ClaimPoolConstructionError(
            "relation result bundle differs from the frozen request census"
        )
    responses = {
        item.assignment_request_sha256: item.response
        for item in checked_results.results
    }
    request_by_output = {
        item.normalized_output.output_sha256: item
        for item in checked_preparation.records
        if item.normalization_status == "normalized"
        and item.normalized_output is not None
    }
    census = ClaimCorpusRequestCensus.model_validate_json(
        (root.resolve() / REQUEST_CENSUS_PATH).read_bytes()
    )
    frozen_requests = {item.request_sha256: item for item in census.primary_requests}
    evidence_census = ObservedEvidenceCensus.model_validate_json(
        (root.resolve() / "configs/evaluation/claim_support_observed_evidence_census.json").read_bytes()
    )
    evidence = {
        (item.family_id, item.evidence_condition): item
        for item in evidence_census.bindings
    }
    relation_by_output: dict[str, dict[str, ClaimRelationAssignmentResponse]] = {}
    for assignment in checked_preparation.relation_requests:
        response = responses.get(assignment.assignment_request_sha256)
        if response is None:
            raise ClaimPoolConstructionError("relation result census is incomplete")
        relation_by_output.setdefault(assignment.source_output_sha256, {})[
            assignment.claim_local_id
        ] = response
    entries: list[ClaimSupportCorpusEntry] = []
    for output_sha256, record in request_by_output.items():
        output = record.normalized_output
        if output is None or output.output_status != "completed":
            continue
        request = frozen_requests[record.request_sha256]
        entries.extend(
            materialize_request_claims(
                request,
                output.model_dump(mode="python"),
                evidence[(request.family_id, request.evidence_condition)],
                relation_by_output.get(output_sha256, {}),
            )
        )
    reconciled = reconcile_materialized_entries(entries)
    protocol = ClaimSupportCorpusProtocol.model_validate_json(
        (
            root.resolve()
            / "configs/evaluation/claim_support_corpus_protocol.json"
        ).read_bytes()
    )
    store_receipt = ClaimCorpusArtifactStore(store_root).publish(
        protocol_sha256=protocol.protocol_sha256,
        census_sha256=census.census_sha256,
        entries=reconciled,
        provider_calls_recorded=checked_results.registered_attempt_count,
    )
    payload: dict[str, object] = {
        "schema_version": POOL_CLOSEOUT_SCHEMA_VERSION,
        "preparation_sha256": checked_preparation.preparation_sha256,
        "relation_result_bundle_sha256": checked_results.bundle_sha256,
        "policy_sha256": policy.policy_sha256,
        "corpus_store_receipt": store_receipt.model_dump(mode="json"),
        "candidate_claim_count": checked_preparation.relation_request_count,
        "automatically_labeled_claim_count": len(reconciled),
        "relation_technical_failure_count": 0,
        "corpus_entry_count": len(reconciled),
        "failures_preserved_in_denominator": True,
        "labels_immutable_before_human_access": True,
        "blind_packets_generated": False,
        "human_annotations_collected": False,
        "main_or_sealed_outcomes_opened": False,
    }
    return ClaimPoolPublicationCloseout.model_validate(
        {**payload, "closeout_sha256": canonical_execution_sha256(payload)}
    )


def _load_authority_identities(
    store_root: Path,
    requests: tuple[ClaimCorpusRequest, ...],
) -> dict[str, str]:
    authority_root = store_root.resolve() / "authorities"
    expected_requests = {item.request_sha256 for item in requests}
    identities: dict[str, str] = {}
    paths = tuple(sorted(authority_root.glob("*.json")))
    if len(paths) != 360:
        raise ClaimPoolConstructionError("authority census is not exactly 360")
    for path in paths:
        if path.is_symlink() or not path.is_file():
            raise ClaimPoolConstructionError("authority path is unsafe")
        try:
            authority = ClaimCorpusRequestAuthority.model_validate_json(path.read_bytes())
        except (OSError, ValidationError) as exc:
            raise ClaimPoolConstructionError("authority record is invalid") from exc
        expected_bytes = (
            canonical_project_json(authority.model_dump(mode="json")) + "\n"
        ).encode()
        if (
            path.read_bytes() != expected_bytes
            or path.stem in identities.values()
            or authority.request_sha256 in identities
        ):
            raise ClaimPoolConstructionError("authority identity is duplicate or non-canonical")
        identities[authority.request_sha256] = path.stem
    if set(identities) != expected_requests:
        raise ClaimPoolConstructionError("authority records differ from request census")
    return identities


def _read_terminal(
    store_root: Path,
    identity: str,
) -> tuple[TerminalExecutionInventory, dict[str, object] | None]:
    shard = store_root.resolve() / "requests" / identity
    verifier = ClaimCorpusTerminalReader(
        root=shard,
        object_root=shard / "objects" / "sha256",
        request_root=shard / "requests",
        terminal_root=shard / "terminal",
        failure_root=shard / "failures",
    )
    inventory = verifier.terminal_inventory(identity)
    return inventory, verifier.terminal_parsed_payload(identity)


def _normalization_record(
    *,
    request: ClaimCorpusRequest,
    identity: str,
    gateway_status: TerminalStatus,
    status: NormalizationStatus,
    source_record_sha256: str | None,
    issue_sha256: str | None,
    output: DiagnosisOutputV2 | None,
    relation_requests: tuple[ClaimRelationAssignmentRequest, ...],
    blocker_code: str | None,
) -> ClaimNormalizationRecord:
    payload: dict[str, object] = {
        "request_sha256": request.request_sha256,
        "request_identity_sha256": identity,
        "variant": request.variant,
        "gateway_status": gateway_status,
        "normalization_status": status,
        "source_record_sha256": source_record_sha256,
        "issue_sha256": issue_sha256,
        "normalized_output": output.model_dump(mode="json") if output else None,
        "relation_request_sha256s": tuple(
            item.assignment_request_sha256 for item in relation_requests
        ),
        "blocker_code": blocker_code,
    }
    return ClaimNormalizationRecord.model_validate(
        {
            **payload,
            "normalized_output": output,
            "record_sha256": canonical_execution_sha256(payload),
        }
    )


__all__ = [
    "ClaimNormalizationRecord",
    "ClaimPoolConstructionError",
    "ClaimPoolPreparation",
    "ClaimPoolPublicationCloseout",
    "ClaimRelationResult",
    "ClaimRelationResultBundle",
    "build_claim_pool_preparation",
    "build_relation_result_bundle",
    "normalize_provider_output",
    "publish_claim_pool",
    "verify_construction_inputs",
]
