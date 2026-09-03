"""Deterministic claim-corpus materialization from already persisted outputs."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from pydantic import ValidationError

from aletheia_lab.evaluation.claim_corpus_adapters import normalize_variant_output
from aletheia_lab.evaluation.claim_corpus_contracts import (
    ClaimCorpusContractError,
    ClaimCorpusRequest,
    ClaimSupportCorpusEntry,
)
from aletheia_lab.evaluation.claim_evidence_semantics import (
    ClaimEvidenceBinding,
    ClaimRelationAssignmentResponse,
    build_relation_assignment_request,
    validate_request_evidence_binding,
    visible_relations_from_assignment,
)
from aletheia_lab.evaluation.claim_support_instrument import classify_visible_support
from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256


def materialize_request_claims(
    request: ClaimCorpusRequest,
    output_payload: Mapping[str, object],
    evidence_binding: ClaimEvidenceBinding,
    relation_assignments: Mapping[str, ClaimRelationAssignmentResponse],
    *,
    reserve_activated_before_execution: bool = False,
) -> tuple[ClaimSupportCorpusEntry, ...]:
    """Normalize one completed output and materialize its schema-native claims.

    The caller supplies an already persisted output.  This function has no
    provider or model dependency and cannot authorize execution.
    """

    checked_request = ClaimCorpusRequest.model_validate(request.model_dump(mode="python"))
    if checked_request.family_role == "reserve" and not reserve_activated_before_execution:
        raise ClaimCorpusContractError("reserve family was not activated before execution")
    output = normalize_variant_output(checked_request.variant, output_payload)
    if output.output_status != "completed":
        if relation_assignments:
            raise ClaimCorpusContractError(
                "terminal output without claims cannot carry relation assignments"
            )
        return ()
    binding = validate_request_evidence_binding(checked_request, evidence_binding)
    expected_claim_ids = tuple(claim.claim_local_id for claim in output.atomic_claims)
    if set(relation_assignments) != set(expected_claim_ids):
        raise ClaimCorpusContractError(
            "relation assignments must exactly cover completed output claims"
        )

    entries: list[ClaimSupportCorpusEntry] = []
    for claim in output.atomic_claims:
        assignment_request = build_relation_assignment_request(
            source_output_sha256=output.output_sha256,
            claim_local_id=claim.claim_local_id,
            claim_text=claim.claim_text,
            claim_type=claim.claim_type,
            cited_evidence_ids=claim.visible_evidence_ids,
            evidence_binding=binding,
        )
        selected = visible_relations_from_assignment(
            assignment_request,
            relation_assignments[claim.claim_local_id],
        )
        label = classify_visible_support(
            claim_text=claim.claim_text,
            claim_type=claim.claim_type,
            visible_evidence=selected,
        )
        payload = {
            "schema_version": "claim-support-corpus-entry/v1",
            "source_partition": "development",
            "request_sha256": checked_request.request_sha256,
            "source_record_sha256": output.source_record_sha256,
            "output_sha256": output.output_sha256,
            "family_id": checked_request.family_id,
            "mechanism": checked_request.mechanism,
            "evidence_condition": checked_request.evidence_condition,
            "variant": checked_request.variant,
            "claim_local_id": claim.claim_local_id,
            "claim_type": claim.claim_type,
            "claim_text": claim.claim_text,
            "material_parts": tuple(item.model_dump(mode="json") for item in claim.material_parts),
            "visible_evidence": tuple(item.model_dump(mode="json") for item in selected),
            "automatic_label": label,
            "hidden_ground_truth_present": False,
            "human_judgment_present": False,
            "main_outcome_present": False,
        }
        try:
            entries.append(
                ClaimSupportCorpusEntry.model_validate(
                    {**payload, "entry_sha256": canonical_execution_sha256(payload)}
                )
            )
        except ValidationError as exc:
            raise ClaimCorpusContractError("materialized claim entry is invalid") from exc
    return tuple(entries)


def reconcile_materialized_entries(
    entries: Sequence[ClaimSupportCorpusEntry],
) -> tuple[ClaimSupportCorpusEntry, ...]:
    """Validate a deterministic, duplicate-free entry sequence."""

    checked = tuple(
        ClaimSupportCorpusEntry.model_validate(item.model_dump(mode="python")) for item in entries
    )
    identities = tuple(item.entry_sha256 for item in checked)
    source_claims = tuple((item.source_record_sha256, item.claim_local_id) for item in checked)
    if len(identities) != len(set(identities)):
        raise ClaimCorpusContractError("materialized entries contain duplicate identities")
    if len(source_claims) != len(set(source_claims)):
        raise ClaimCorpusContractError("one source claim was materialized more than once")
    return tuple(sorted(checked, key=lambda item: item.entry_sha256))


__all__ = ["materialize_request_claims", "reconcile_materialized_entries"]
