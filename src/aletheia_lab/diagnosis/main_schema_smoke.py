"""One-call synthetic compatibility gate for the main diagnosis response schema."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import cast

from aletheia_lab.diagnosis.main_response import validate_main_provider_output
from aletheia_lab.evaluation.claim_evidence_semantics import (
    ModelVisibleEvidenceContext,
    build_visible_evidence_item,
)
from aletheia_lab.evaluation.execution_contracts import (
    EvaluationCaseReference,
    EvaluationManifestReference,
    ModelPolicyReference,
    canonical_execution_json,
    canonical_execution_sha256,
)
from aletheia_lab.evaluation.variant_fairness import load_diagnosis_variant_freeze
from aletheia_lab.model_gateway import (
    AdapterInvocationError,
    GatewayRequest,
    OpenAIGatewayPolicy,
    ProviderCall,
    RuntimePolicyReference,
    prepare_gateway_request,
    validate_response_payload,
)
from aletheia_lab.model_gateway.openai import (
    OpenAIGatewayConfigurationError,
    _openai_response_format,
    _provider_attempt_ref,
)
from aletheia_lab.model_gateway.openai_recovery import OpenAIRecoveryAdapter
from aletheia_lab.model_gateway.schema import validate_response_schema
from aletheia_lab.project.identity import canonical_project_json, content_sha256

SMOKE_PLAN_SCHEMA_VERSION = "diagnosis-main-schema-smoke-plan/v1"
SMOKE_RECEIPT_SCHEMA_VERSION = "diagnosis-main-schema-smoke-receipt/v1"
SMOKE_DESTINATION = "https://api.openai.com/v1/chat/completions"
SYNTHETIC_EVIDENCE_ID = "ev-synthetic-compatibility"
EXPECTED_FAILED_MAIN_COUNTS = {"deterministic_completed": 128, "technical_failure": 896}
MAIN_RECOVERY_SCHEMA_VERSION = "diagnosis-main-provider-output/1"
MAIN_RECOVERY_TRANSPORT_CONTRACT: dict[str, object] = {
    "schema_version": "diagnosis-main-recovery-transport/v1",
    "provider_schema_projection": "remove_string_patterns_only",
    "local_schema_validation": "original_full_schema_required",
    "prompt_changed": False,
    "scientific_design_changed": False,
    "maximum_output_tokens": 600,
    "sdk_retries": 0,
    "failed_response_content_persisted": False,
}
MAIN_RECOVERY_TRANSPORT_SHA256 = canonical_execution_sha256(MAIN_RECOVERY_TRANSPORT_CONTRACT)

_RESPONSE_CONTRACT = Path("configs/evaluation/diagnosis_main_response_contract.json")
_FAIRNESS_FREEZE = Path("configs/evaluation/diagnosis_variant_fairness_freeze.json")


def _remove_patterns(value: object) -> object:
    if isinstance(value, list):
        return [_remove_patterns(item) for item in value]
    if isinstance(value, dict):
        return {
            key: _remove_patterns(item)
            for key, item in value.items()
            if not (key == "pattern" and isinstance(item, str))
        }
    return value


def main_provider_wire_schema(schema_json: str) -> dict[str, object]:
    """Remove regex decoding constraints while retaining the original local gate."""

    schema = json.loads(schema_json)
    if not isinstance(schema, dict):
        raise ValueError("main recovery schema must be an object")
    validate_response_schema(schema)
    properties = schema.get("properties")
    schema_version = properties.get("schema_version") if isinstance(properties, dict) else None
    if (
        not isinstance(schema_version, dict)
        or schema_version.get("const") != MAIN_RECOVERY_SCHEMA_VERSION
    ):
        raise ValueError("wire projection requires the registered main schema")
    projected = cast(dict[str, object], _remove_patterns(schema))
    validate_response_schema(projected)
    return projected


def main_wire_schema_json(schema_json: str) -> str:
    return canonical_project_json(main_provider_wire_schema(schema_json))


class OpenAIMainRecoveryAdapter(OpenAIRecoveryAdapter):
    """Use the main-only projection without mutating the historical adapters."""

    def _prepare_payload(
        self,
        call: ProviderCall,
        payload: dict[str, object],
    ) -> dict[str, object]:
        try:
            response_format = _openai_response_format(
                main_wire_schema_json(call.response_schema_json)
            )
        except (OpenAIGatewayConfigurationError, ValueError):
            raise AdapterInvocationError(
                code="provider_schema_incompatible",
                retryable=False,
                provider_attempt_ref=_provider_attempt_ref(
                    call,
                    provider_request_id=None,
                    outcome="response_schema_incompatible",
                ),
            ) from None
        return {**payload, "response_format": response_format}


def _opaque(payload: object) -> str:
    return f"ev-{canonical_execution_sha256(payload)}"


def _load_object(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path.name}")
    return value


def _response_contract(root: Path) -> dict[str, object]:
    contract = _load_object(root / _RESPONSE_CONTRACT)
    declared = contract.get("contract_sha256")
    observed = canonical_execution_sha256(
        {key: value for key, value in contract.items() if key != "contract_sha256"}
    )
    if declared != observed:
        raise ValueError("main response contract identity changed")
    return contract


def build_synthetic_main_schema_request(
    root: Path,
    *,
    source_commit_ref: str,
) -> tuple[GatewayRequest, OpenAIGatewayPolicy]:
    """Bind synthetic evidence to the frozen model, prompt and original schema."""

    contract = _response_contract(root)
    schema = contract.get("json_schema")
    prompts = contract.get("prompt_contracts")
    if not isinstance(schema, dict) or not isinstance(prompts, dict):
        raise ValueError("main response contract is incomplete")
    base_prompt = prompts.get("A2")
    if not isinstance(base_prompt, str):
        raise ValueError("main A2 prompt is unavailable")
    prompt = (
        f"{base_prompt}\n\nFinal diagnosis turn. Use only the supplied final "
        "projection and return exactly one registered diagnosis JSON object."
    )

    source_sha = content_sha256(b"synthetic compatibility counter: 7")
    evidence = build_visible_evidence_item(
        evidence_id=SYNTHETIC_EVIDENCE_ID,
        kind="metric",
        title="Synthetic compatibility counter",
        content="The synthetic counter is 7.",
        source_content_sha256=source_sha,
    )
    context_payload = {
        "schema_version": "claim-visible-evidence-context/v1",
        "items": (evidence.model_dump(mode="json"),),
    }
    context_sha = canonical_execution_sha256(context_payload)
    context = ModelVisibleEvidenceContext(
        context_id=f"ccctx-{context_sha}",
        items=(evidence,),
        context_sha256=context_sha,
    )

    schema_json = canonical_project_json(schema)
    seed = canonical_execution_sha256(
        {
            "purpose": "diagnosis-main-schema-transport-smoke",
            "source_commit_ref": source_commit_ref,
            "schema_sha256": content_sha256(schema_json.encode("utf-8")),
            "context_sha256": context_sha,
        }
    )
    manifest = EvaluationManifestReference.build(
        project_id=f"p3-project-{seed}",
        snapshot_id=f"p3-snapshot-{seed}",
        manifest_content_sha256=seed,
        source_commit_ref=source_commit_ref,
        authorization_state="authorized",
        authorization_ref=f"ev-{seed}",
        provenance_sha256=seed,
        created_at="2026-09-23T00:00:00Z",
        frozen_at="2026-09-23T00:00:00Z",
        visibility="diagnosis",
    )
    case = EvaluationCaseReference.build(
        manifest=manifest,
        case_id=_opaque({"smoke": seed, "field": "case"}),
        family_id=_opaque({"smoke": seed, "field": "family"}),
        mechanism_id=_opaque({"smoke": seed, "field": "mechanism"}),
        dataset_id=_opaque({"smoke": seed, "field": "dataset"}),
        variant_id=_opaque({"smoke": seed, "variant": "A2"}),
        variant_content_sha256=canonical_execution_sha256({"variant": "A2"}),
        case_content_sha256=seed,
        evidence_bundle_id=f"p3-evidence-bundle-{context_sha}",
        evidence_content_sha256=context_sha,
        lineage_graph_id=f"p3-lineage-graph-{source_sha}",
        lineage_sha256=source_sha,
        visibility_projection_sha256=context_sha,
        provenance_sha256=seed,
        visibility="diagnosis",
    )
    freeze = load_diagnosis_variant_freeze(root / _FAIRNESS_FREEZE)
    policy = OpenAIGatewayPolicy.from_fairness_policy(freeze.model_policies["main_llm_v1"])
    model_policy = ModelPolicyReference.build(
        manifest=manifest,
        policy_content_sha256=policy.model_policy_sha256(),
        provider_ref=_opaque({"provider": "openai"}),
        model_ref=_opaque({"model": policy.model}),
        model_version_ref=_opaque({"model_version": policy.model_version}),
        resource_policy_ref=_opaque({"boundary": "main-schema-smoke"}),
        prompt_policy_ref=_opaque({"prompt_sha256": content_sha256(prompt.encode())}),
        response_schema_sha256=content_sha256(schema_json.encode()),
        provenance_sha256=seed,
        visibility="diagnosis",
    )
    runtime = RuntimePolicyReference.build(
        manifest=manifest,
        model_policy=model_policy,
        retry_policy_ref=_opaque({"adapter_invocations": 1, "sdk_retries": 0}),
        timeout_ns=60_000_000_000,
        max_attempts=2,
        max_response_bytes=32_768,
        provenance_sha256=seed,
    )
    return (
        prepare_gateway_request(
            manifest=manifest,
            case=case,
            model_policy=model_policy,
            context=context,
            prompt_text=prompt,
            response_schema=schema,
            runtime_policy=runtime,
        ),
        policy,
    )


def provider_call(request: GatewayRequest) -> ProviderCall:
    """Create exactly the first adapter call; no gateway retry loop is used."""

    attempt = request.initial_attempt
    context = request.context
    if not isinstance(context, ModelVisibleEvidenceContext):
        raise ValueError("smoke request must contain synthetic visible evidence")
    return ProviderCall(
        request_identity_sha256=attempt.request_identity_sha256,
        attempt_id=attempt.attempt_id,
        attempt_identity_sha256=attempt.attempt_identity_sha256,
        attempt_ordinal=1,
        context_sha256=attempt.context_sha256,
        prompt_sha256=attempt.prompt_sha256,
        response_schema_sha256=attempt.response_schema_sha256,
        context_json=canonical_execution_json(context.model_payload()),
        prompt_text=request.prompt_text,
        response_schema_json=request.response_schema_json,
        runtime_policy=request.runtime_policy,
    )


def exact_outbound_payload(
    request: GatewayRequest,
    policy: OpenAIGatewayPolicy,
) -> dict[str, object]:
    """Return the exact synthetic SDK payload, excluding the credential header."""

    call = provider_call(request)
    return {
        "model": policy.model_version,
        "messages": [
            {"role": "system", "content": call.prompt_text},
            {"role": "user", "content": call.context_json},
        ],
        "response_format": _openai_response_format(
            main_wire_schema_json(call.response_schema_json)
        ),
        "temperature": policy.temperature,
        "top_p": policy.top_p,
        "seed": policy.seed,
        "max_tokens": policy.max_output_tokens,
        "n": 1,
        "store": False,
        "stream": False,
        "timeout": call.runtime_policy.timeout_ns / 1_000_000_000,
        "extra_headers": {"X-Client-Request-Id": call.attempt_id},
    }


def build_smoke_plan(
    root: Path,
    *,
    source_commit_ref: str,
    predecessor_receipt_sha256: str,
    predecessor_tree_sha256: str,
) -> dict[str, object]:
    request, policy = build_synthetic_main_schema_request(root, source_commit_ref=source_commit_ref)
    outbound = exact_outbound_payload(request, policy)
    original_schema_sha = content_sha256(request.response_schema_json.encode())
    wire_schema = main_wire_schema_json(request.response_schema_json)
    payload: dict[str, object] = {
        "schema_version": SMOKE_PLAN_SCHEMA_VERSION,
        "purpose": "synthetic_transport_compatibility_only",
        "source_commit_ref": source_commit_ref,
        "destination": SMOKE_DESTINATION,
        "provider_call_count": 1,
        "sdk_retries": 0,
        "synthetic_payload_only": True,
        "production_prompt_and_scientific_design_changed": False,
        "model_snapshot": policy.model_version,
        "maximum_output_tokens": policy.max_output_tokens,
        "request_identity_sha256": request.initial_attempt.request_identity_sha256,
        "original_schema_sha256": original_schema_sha,
        "wire_schema_sha256": content_sha256(wire_schema.encode()),
        "transport_sha256": MAIN_RECOVERY_TRANSPORT_SHA256,
        "outbound_payload": outbound,
        "outbound_payload_sha256": canonical_execution_sha256(outbound),
        "predecessor_receipt_sha256": predecessor_receipt_sha256,
        "predecessor_tree_sha256": predecessor_tree_sha256,
        "recovery_authorized": False,
    }
    return {**payload, "plan_sha256": canonical_execution_sha256(payload)}


def validate_smoke_response(raw: bytes, *, request: GatewayRequest) -> dict[str, object]:
    """Apply the original full schema and A2 semantics after wire decoding."""

    text = raw.decode("utf-8", errors="strict")
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError("smoke response root is not an object")
    original_schema = json.loads(request.response_schema_json)
    if not isinstance(original_schema, dict):
        raise ValueError("original response schema is unavailable")
    validate_response_payload(parsed, original_schema)
    validate_main_provider_output(
        text,
        variant="A2",
        visible_evidence_ids={SYNTHETIC_EVIDENCE_ID},
    )
    return parsed


def validate_self_hash(payload: Mapping[str, object], field: str) -> None:
    declared = payload.get(field)
    identity = {key: value for key, value in payload.items() if key != field}
    if declared != canonical_execution_sha256(identity):
        raise ValueError(f"{field} does not match canonical content")


__all__ = [
    "EXPECTED_FAILED_MAIN_COUNTS",
    "MAIN_RECOVERY_SCHEMA_VERSION",
    "MAIN_RECOVERY_TRANSPORT_CONTRACT",
    "MAIN_RECOVERY_TRANSPORT_SHA256",
    "OpenAIMainRecoveryAdapter",
    "SMOKE_DESTINATION",
    "SMOKE_PLAN_SCHEMA_VERSION",
    "SMOKE_RECEIPT_SCHEMA_VERSION",
    "build_smoke_plan",
    "build_synthetic_main_schema_request",
    "exact_outbound_payload",
    "main_provider_wire_schema",
    "main_wire_schema_json",
    "provider_call",
    "validate_self_hash",
    "validate_smoke_response",
]
