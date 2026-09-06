"""Synthetic transport probes for every distinct frozen recovery response schema."""

import json
from dataclasses import replace

from aletheia_lab.diagnosis.variant_registry import DiagnosisVariantRequestBinding
from aletheia_lab.evaluation.claim_corpus_live import (
    ClaimCorpusRequestAuthority,
    ClaimCorpusToolLedger,
    PreparedClaimCorpusRequest,
)
from aletheia_lab.evaluation.claim_evidence_semantics import (
    ModelVisibleEvidenceContext,
    build_visible_evidence_item,
)
from aletheia_lab.evaluation.execution_contracts import (
    EvaluationCaseReference,
    ModelPolicyReference,
    canonical_execution_sha256,
)
from aletheia_lab.model_gateway import RuntimePolicyReference, prepare_gateway_request
from aletheia_lab.project.identity import content_sha256

PROBE_PROMPT = (
    "This is a synthetic serialization compatibility test, not a diagnosis. "
    "Return one short completed observation claim that the synthetic counter is 7, "
    "with one material part and a citation to the first supplied evidence item. "
    "Use the supplied response schema exactly."
)


def build_compatibility_requests(
    prepared: tuple[PreparedClaimCorpusRequest, ...],
) -> tuple[PreparedClaimCorpusRequest, ...]:
    """Keep exact schema bytes and transport policy, but replace ALL scientific content."""
    templates = {
        item.request.response_schema_json: item
        for item in reversed(prepared)
        if item.route == "model_gateway"
    }
    return tuple(_synthetic_request(templates[key]) for key in sorted(templates))


def _synthetic_request(template: PreparedClaimCorpusRequest) -> PreparedClaimCorpusRequest:
    original = template.request
    if not isinstance(original.context, ModelVisibleEvidenceContext):
        raise ValueError("compatibility requires a visible evidence schema")
    source = content_sha256(b"synthetic compatibility counter: 7")
    items = tuple(
        build_visible_evidence_item(
            evidence_id=item.evidence_id,
            kind=item.kind,
            title="Synthetic counter",
            content="The synthetic counter is 7.",
            source_content_sha256=source,
        )
        for item in original.context.items
    )
    context_hash = canonical_execution_sha256(
        {"schema_version": "claim-visible-evidence-context/v1", "items": tuple(
            item.model_dump(mode="json") for item in items
        )}
    )
    context = ModelVisibleEvidenceContext(
        context_id=f"ccctx-{context_hash}", items=items, context_sha256=context_hash
    )
    probe_hash = canonical_execution_sha256(
        {"purpose": "synthetic-schema-compatibility", "schema": original.response_schema_json}
    )
    opaque = f"ev-{probe_hash}"
    attempt = original.initial_attempt
    case = EvaluationCaseReference.build(
        manifest=attempt.manifest,
        case_id=opaque, family_id=opaque, mechanism_id=opaque, dataset_id=opaque,
        variant_id=opaque, variant_content_sha256=probe_hash,
        case_content_sha256=probe_hash,
        evidence_bundle_id=f"p3-evidence-bundle-{context_hash}",
        evidence_content_sha256=context_hash,
        lineage_graph_id=f"p3-lineage-graph-{source}", lineage_sha256=source,
        visibility_projection_sha256=context_hash, provenance_sha256=probe_hash,
        visibility="diagnosis",
    )
    previous_policy = attempt.model_policy
    model_policy = ModelPolicyReference.build(
        manifest=attempt.manifest,
        policy_content_sha256=previous_policy.policy_content_sha256,
        provider_ref=previous_policy.provider_ref,
        model_ref=previous_policy.model_ref,
        model_version_ref=previous_policy.model_version_ref,
        resource_policy_ref=previous_policy.resource_policy_ref,
        prompt_policy_ref=opaque,
        response_schema_sha256=previous_policy.response_schema_sha256,
        provenance_sha256=probe_hash,
        visibility="diagnosis",
    )
    previous_runtime = original.runtime_policy
    runtime = RuntimePolicyReference.build(
        manifest=attempt.manifest,
        model_policy=model_policy,
        retry_policy_ref=previous_runtime.retry_policy_ref,
        timeout_ns=previous_runtime.timeout_ns,
        max_attempts=previous_runtime.max_attempts,
        max_response_bytes=previous_runtime.max_response_bytes,
        provenance_sha256=probe_hash,
    )
    request = prepare_gateway_request(
        manifest=attempt.manifest, case=case, model_policy=model_policy,
        context=context, prompt_text=PROBE_PROMPT,
        response_schema=json.loads(original.response_schema_json),
        runtime_policy=runtime,
    )
    authority = _synthetic_authority(template, probe_hash, context)
    # Keep the in-memory source request key solely so the verifier can exercise
    # the exact downstream variant normalizer. Persisted authority and gateway
    # identities are synthetic and do not reuse the study request identity.
    return replace(template, authority=authority, request=request)


def _synthetic_authority(
    template: PreparedClaimCorpusRequest,
    probe_hash: str,
    context: ModelVisibleEvidenceContext,
) -> ClaimCorpusRequestAuthority:
    previous = template.authority
    ledger = None
    if previous.tool_ledger is not None:
        fields = previous.tool_ledger.model_dump(mode="json", exclude={"ledger_sha256"})
        fields.update(
            request_sha256=probe_hash,
            query_sha256s=tuple(
                canonical_execution_sha256(
                    {"synthetic_probe": probe_hash, "operation": op, "ordinal": ordinal}
                )
                for ordinal, op in enumerate(previous.tool_ledger.operations, start=1)
            ),
            selected_evidence_ids=tuple(item.evidence_id for item in context.items),
            visible_context_sha256=context.context_sha256,
        )
        ledger = ClaimCorpusToolLedger.model_validate(
            {**fields, "ledger_sha256": canonical_execution_sha256(fields)}
        )
    binding_fields = previous.variant_binding.model_dump(
        mode="json", exclude={"binding_sha256"}
    )
    binding_fields.update(
        context_sha256=context.context_sha256,
        evidence_content_sha256=context.context_sha256,
        tool_ledger_sha256=ledger.ledger_sha256 if ledger else None,
    )
    binding = DiagnosisVariantRequestBinding.model_validate(
        {**binding_fields, "binding_sha256": canonical_execution_sha256(binding_fields)}
    )
    authority_payload = {
        "schema_version": "claim-corpus-request-authority/v1",
        "request_sha256": probe_hash,
        "observed_evidence_binding_sha256": canonical_execution_sha256(
            {"synthetic_probe": probe_hash, "context": context.context_sha256}
        ),
        "variant_binding": binding.model_dump(mode="json"),
        "tool_ledger": ledger.model_dump(mode="json") if ledger else None,
    }
    return ClaimCorpusRequestAuthority.model_validate(
        {
            **authority_payload,
            "variant_binding": binding,
            "tool_ledger": ledger,
            "authority_sha256": canonical_execution_sha256(authority_payload),
        }
    )
