from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from aletheia_lab.evaluation.claim_corpus_construction import (
    ClaimNormalizationRecord,
    ClaimPoolConstructionError,
    ClaimPoolPreparation,
    ClaimRelationResult,
    build_relation_result_bundle,
    normalize_provider_output,
    publish_claim_pool,
)
from aletheia_lab.evaluation.claim_corpus_contracts import (
    ClaimCorpusContractError,
    ClaimCorpusRequest,
    ClaimCorpusRequestCensus,
)
from aletheia_lab.evaluation.claim_corpus_readiness import REQUEST_CENSUS_PATH
from aletheia_lab.evaluation.claim_evidence_census import ObservedEvidenceCensus
from aletheia_lab.evaluation.claim_evidence_semantics import (
    ClaimRelationAssignmentRequest,
    build_relation_assignment_request,
    load_evidence_semantics_policy,
    parse_relation_assignment,
)
from aletheia_lab.evaluation.execution_contracts import (
    ATTEMPT_IDENTITY_SCHEMA_VERSION,
    canonical_execution_json,
    canonical_execution_sha256,
)
from aletheia_lab.model_gateway import (
    ClaimRelationProviderContext,
    ProviderCall,
    RuntimePolicyReference,
)
from aletheia_lab.project.identity import content_sha256

ROOT = Path(__file__).resolve().parents[2]


def _inputs() -> tuple[ClaimCorpusRequestCensus, ObservedEvidenceCensus]:
    census = ClaimCorpusRequestCensus.model_validate_json(
        (ROOT / REQUEST_CENSUS_PATH).read_bytes()
    )
    evidence = ObservedEvidenceCensus.model_validate_json(
        (
            ROOT
            / "configs/evaluation/claim_support_observed_evidence_census.json"
        ).read_bytes()
    )
    return census, evidence


def _provider_payload(request: ClaimCorpusRequest, evidence_id: str) -> dict[str, object]:
    return {
        "schema_version": "diagnosis-provider-output/1",
        "output_status": "completed",
        "atomic_claims": (
            {
                "claim_local_id": "claim-1",
                "claim_type": "evidence_statement",
                "claim_text": "The measured evidence contains the stated observation.",
                "material_parts": (
                    {
                        "part_id": "part-observation",
                        "text": "The measured evidence contains the observation.",
                    },
                ),
                "visible_evidence_ids": (evidence_id,),
            },
        ),
        "abstention_reason": "",
    }


def _record(fields: dict[str, object]) -> ClaimNormalizationRecord:
    hash_fields = dict(fields)
    output = hash_fields.get("normalized_output")
    if hasattr(output, "model_dump"):
        hash_fields["normalized_output"] = output.model_dump(mode="json")
    return ClaimNormalizationRecord.model_validate(
        {**fields, "record_sha256": canonical_execution_sha256(hash_fields)}
    )


def _preparation() -> tuple[ClaimPoolPreparation, ClaimRelationAssignmentRequest]:
    census, evidence = _inputs()
    request = census.primary_requests[0]
    binding = next(
        item
        for item in evidence.bindings
        if item.family_id == request.family_id
        and item.evidence_condition == request.evidence_condition
    )
    output = normalize_provider_output(
        request,
        _provider_payload(request, binding.visible_context.items[0].evidence_id),
        source_record_sha256="1" * 64,
    )
    claim = output.atomic_claims[0]
    relation = build_relation_assignment_request(
        source_output_sha256=output.output_sha256,
        claim_local_id=claim.claim_local_id,
        claim_text=claim.claim_text,
        claim_type=claim.claim_type,
        cited_evidence_ids=claim.visible_evidence_ids,
        evidence_binding=binding,
    )
    records = [
        _record(
            {
                "request_sha256": request.request_sha256,
                "request_identity_sha256": canonical_execution_sha256(
                    {"request": request.request_sha256}
                ),
                "variant": request.variant,
                "gateway_status": "parsed",
                "normalization_status": "normalized",
                "source_record_sha256": output.source_record_sha256,
                "issue_sha256": None,
                "normalized_output": output,
                "relation_request_sha256s": (relation.assignment_request_sha256,),
                "blocker_code": None,
            }
        )
    ]
    for frozen in census.primary_requests[1:]:
        records.append(
            _record(
                {
                    "request_sha256": frozen.request_sha256,
                    "request_identity_sha256": canonical_execution_sha256(
                        {"request": frozen.request_sha256}
                    ),
                    "variant": frozen.variant,
                    "gateway_status": "provider_failed",
                    "normalization_status": "technical_failure",
                    "source_record_sha256": None,
                    "issue_sha256": "2" * 64,
                    "normalized_output": None,
                    "relation_request_sha256s": (),
                    "blocker_code": "technical_terminal",
                }
            )
        )
    policy = load_evidence_semantics_policy(ROOT)
    payload: dict[str, object] = {
        "schema_version": "claim-pool-preparation/v1",
        "source_commit_ref": "3" * 40,
        "authorization_sha256": "4" * 64,
        "execution_plan_sha256": "5" * 64,
        "live_receipt_sha256": "6" * 64,
        "reconciliation_receipt_sha256": "7" * 64,
        "reserve_receipt_sha256": "8" * 64,
        "evidence_census_sha256": evidence.census_sha256,
        "evidence_semantics_policy_sha256": policy.policy_sha256,
        "terminal_request_count": 360,
        "parsed_terminal_count": 1,
        "technical_failure_terminal_count": 359,
        "normalized_output_count": 1,
        "normalization_rejection_count": 0,
        "completed_output_count": 1,
        "abstained_output_count": 0,
        "claim_candidate_count": 1,
        "relation_request_count": 1,
        "records": tuple(item.model_dump(mode="json") for item in records),
        "relation_requests": (relation.model_dump(mode="json"),),
        "failures_preserved_in_denominator": True,
        "free_text_recovery_performed": False,
        "automatic_labels_generated": False,
        "corpus_entries_materialized": False,
        "blind_packets_generated": False,
        "human_annotations_collected": False,
        "main_or_sealed_outcomes_opened": False,
    }
    preparation = ClaimPoolPreparation.model_validate(
        {
            **payload,
            "records": tuple(records),
            "relation_requests": (relation,),
            "preparation_sha256": canonical_execution_sha256(payload),
        }
    )
    return preparation, relation


def _parsed_result(
    relation: ClaimRelationAssignmentRequest,
) -> ClaimRelationResult:
    response = parse_relation_assignment(
        relation,
        {
            "decisions": (
                {
                    "evidence_id": relation.visible_evidence[0].evidence_id,
                    "relation_polarity": "supports",
                    "relation_scope": "entire",
                },
            )
        },
    )
    payload: dict[str, object] = {
        "assignment_request_sha256": relation.assignment_request_sha256,
        "terminal_status": "parsed",
        "attempt_count": 1,
        "response": response.model_dump(mode="json"),
        "issue_sha256": None,
    }
    return ClaimRelationResult.model_validate(
        {
            **payload,
            "response": response,
            "result_sha256": canonical_execution_sha256(payload),
        }
    )


@pytest.mark.parametrize("variant", ("A1", "B0"))
def test_structured_provider_envelope_normalizes_without_prose_recovery(
    variant: str,
) -> None:
    census, evidence = _inputs()
    request = next(item for item in census.primary_requests if item.variant == variant)
    binding = next(
        item
        for item in evidence.bindings
        if item.family_id == request.family_id
        and item.evidence_condition == request.evidence_condition
    )

    output = normalize_provider_output(
        request,
        _provider_payload(request, binding.visible_context.items[0].evidence_id),
        source_record_sha256="a" * 64,
    )

    assert output.schema_version == "diagnosis-output/2"
    assert output.output_status == "completed"
    assert output.source_record_sha256 == "a" * 64
    assert len(output.atomic_claims) == 1


def test_normalizer_rejects_free_text_and_noncanonical_terminal_shape() -> None:
    census, _ = _inputs()
    request = census.primary_requests[0]

    with pytest.raises(ClaimCorpusContractError, match="frozen schema"):
        normalize_provider_output(
            request,
            {"free_text": "Split this prose after observing it."},
            source_record_sha256="a" * 64,
        )
    with pytest.raises(ClaimCorpusContractError, match="frozen schema"):
        normalize_provider_output(
            request,
            {
                "schema_version": "diagnosis-provider-output/1",
                "output_status": "completed",
                "atomic_claims": (),
                "abstention_reason": "",
            },
            source_record_sha256="a" * 64,
        )


def test_relation_context_exposes_only_three_frozen_provider_fields() -> None:
    _, relation = _preparation()
    fields = relation.provider_payload()
    serialized = json.loads(json.dumps(fields))
    context = ClaimRelationProviderContext.from_provider_payload(serialized)

    assert context.context_sha256 == canonical_execution_sha256(fields)
    assert context.model_payload() == fields
    assert set(context.model_payload()) == {
        "claim_text",
        "claim_type",
        "visible_evidence",
    }
    with pytest.raises(ValidationError, match="identity"):
        ClaimRelationProviderContext.model_validate(
            {**context.model_dump(mode="python"), "context_sha256": "f" * 64}
        )


def test_relation_context_rejects_evaluator_only_fields() -> None:
    _, relation = _preparation()
    fields = relation.provider_payload()

    with pytest.raises(ValueError, match="unauthorized fields"):
        ClaimRelationProviderContext.from_provider_payload(
            {**fields, "variant": "FULL"}
        )

    context = ClaimRelationProviderContext.model_validate(
        {
            "schema_version": "claim-relation-provider-context/v1",
            **fields,
            "context_sha256": canonical_execution_sha256(fields),
        }
    )

    assert not {
        "mechanism",
        "evidence_condition",
        "variant",
        "hidden_ground_truth",
        "human_judgment",
        "main_outcome",
    } & set(context.model_payload())


def test_provider_call_round_trips_exact_blind_relation_payload() -> None:
    _, relation = _preparation()
    context = ClaimRelationProviderContext.from_provider_payload(
        relation.provider_payload()
    )
    policy_fields: dict[str, object] = {
        "schema_version": "model-gateway-runtime-policy/v1",
        "manifest_reference_id": f"ev-{'1' * 64}",
        "manifest_content_sha256": "2" * 64,
        "model_policy_reference_id": f"ev-{'3' * 64}",
        "resource_policy_ref": f"ev-{'4' * 64}",
        "retry_policy_ref": f"ev-{'5' * 64}",
        "timeout_ns": 1_000_000,
        "max_attempts": 2,
        "max_response_bytes": 4096,
        "authorization_ref": f"ev-{'6' * 64}",
        "provenance_sha256": "7" * 64,
    }
    policy_sha = canonical_execution_sha256(policy_fields)
    policy = RuntimePolicyReference.model_validate(
        {
            **policy_fields,
            "reference_id": f"ev-{policy_sha}",
            "policy_sha256": policy_sha,
        }
    )
    request_sha = "8" * 64
    attempt_fields = {
        "schema_version": ATTEMPT_IDENTITY_SCHEMA_VERSION,
        "request_identity_sha256": request_sha,
        "attempt_ordinal": 1,
    }
    attempt_sha = canonical_execution_sha256(attempt_fields)
    prompt = "Judge the relation using only the visible evidence."
    response_schema = "{}"

    call = ProviderCall(
        request_identity_sha256=request_sha,
        attempt_id=f"ev-{attempt_sha}",
        attempt_identity_sha256=attempt_sha,
        attempt_ordinal=1,
        context_sha256=context.context_sha256,
        prompt_sha256=content_sha256(prompt.encode()),
        response_schema_sha256=content_sha256(response_schema.encode()),
        context_json=canonical_execution_json(context.model_payload()),
        prompt_text=prompt,
        response_schema_json=response_schema,
        runtime_policy=policy,
    )

    assert json.loads(call.context_json)["payload"] == json.loads(
        json.dumps(relation.provider_payload())
    )


def test_complete_relation_census_publishes_an_immutable_labeled_pool(
    tmp_path: Path,
) -> None:
    preparation, relation = _preparation()
    bundle = build_relation_result_bundle(preparation, (_parsed_result(relation),))

    first = publish_claim_pool(
        ROOT,
        preparation=preparation,
        relation_results=bundle,
        store_root=tmp_path / "pool",
    )
    second = publish_claim_pool(
        ROOT,
        preparation=preparation,
        relation_results=bundle,
        store_root=tmp_path / "pool",
    )

    assert first == second
    assert first.corpus_entry_count == 1
    assert first.automatically_labeled_claim_count == 1
    assert first.corpus_store_receipt.entry_count == 1
    assert not first.blind_packets_generated
    assert not first.human_annotations_collected
    assert not first.main_or_sealed_outcomes_opened


def test_relation_technical_failure_blocks_full_pool_publication(
    tmp_path: Path,
) -> None:
    preparation, relation = _preparation()
    fields: dict[str, object] = {
        "assignment_request_sha256": relation.assignment_request_sha256,
        "terminal_status": "technical_failure",
        "attempt_count": 2,
        "response": None,
        "issue_sha256": "d" * 64,
    }
    failed = ClaimRelationResult.model_validate(
        {**fields, "result_sha256": canonical_execution_sha256(fields)}
    )
    bundle = build_relation_result_bundle(preparation, (failed,))

    with pytest.raises(ClaimPoolConstructionError, match="technical relation failures"):
        publish_claim_pool(
            ROOT,
            preparation=preparation,
            relation_results=bundle,
            store_root=tmp_path / "pool",
        )
