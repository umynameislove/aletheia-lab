"""One-call synthetic compatibility gate for the main diagnosis response schema."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
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
from aletheia_lab.model_gateway.contracts import GatewayContractError
from aletheia_lab.model_gateway.openai import (
    OpenAIGatewayConfigurationError,
    _openai_response_format,
    _provider_attempt_ref,
)
from aletheia_lab.model_gateway.openai_recovery import OpenAIRecoveryAdapter
from aletheia_lab.model_gateway.schema import validate_response_schema
from aletheia_lab.project.identity import canonical_project_json, content_sha256

SMOKE_PLAN_SCHEMA_VERSION = "diagnosis-main-schema-smoke-plan/v2"
SMOKE_RECEIPT_SCHEMA_VERSION = "diagnosis-main-schema-smoke-receipt/v2"
SMOKE_DESTINATION = "https://api.openai.com/v1/chat/completions"
SYNTHETIC_EVIDENCE_ID = "ev-synthetic-compatibility"
EXPECTED_FAILED_MAIN_COUNTS = {"deterministic_completed": 128, "technical_failure": 896}
EXPECTED_PRIOR_SMOKE_RECEIPT_SHA256 = (
    "4a0c2f722cad5116c18aebbf52bd625f5831a84b838df011973cdc46f3d51825"
)
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
MAIN_RECOVERY_TRANSPORT_V2_CONTRACT: dict[str, object] = {
    **MAIN_RECOVERY_TRANSPORT_CONTRACT,
    "schema_version": "diagnosis-main-recovery-transport/v2",
    "provider_schema_projection": "remove_string_patterns_preserve_claim_id_enum",
}
MAIN_RECOVERY_TRANSPORT_V2_SHA256 = canonical_execution_sha256(MAIN_RECOVERY_TRANSPORT_V2_CONTRACT)
MAIN_RECOVERY_TRANSPORT_V3_CONTRACT: dict[str, object] = {
    **MAIN_RECOVERY_TRANSPORT_V2_CONTRACT,
    "schema_version": "diagnosis-main-recovery-transport/v3",
    "citation_free_final_arm_wire_constraint": "visible_evidence_ids.maxItems=0 for A1,B1,B2",
}
MAIN_RECOVERY_TRANSPORT_V3_SHA256 = canonical_execution_sha256(MAIN_RECOVERY_TRANSPORT_V3_CONTRACT)
FORMAT_ONLY_REPAIR_POLICY: dict[str, object] = {
    "schema_version": "diagnosis-main-format-repair/v1",
    "operation": "strip_boundary_whitespace_only",
    "fields": [
        "atomic_claims[].claim_text",
        "atomic_claims[].material_parts[].part_id",
        "atomic_claims[].material_parts[].text",
        "atomic_claims[].visible_evidence_ids[]",
        "abstention_reason",
    ],
    "must_pass_original_schema_after_repair": True,
    "must_pass_variant_semantics_after_repair": True,
    "only_original_pattern_mismatch_fields_modified": True,
    "internal_whitespace_and_nontext_fields_unchanged": True,
}
FORMAT_ONLY_REPAIR_SHA256 = canonical_execution_sha256(FORMAT_ONLY_REPAIR_POLICY)

_RESPONSE_CONTRACT = Path("configs/evaluation/diagnosis_main_response_contract.json")
_FAIRNESS_FREEZE = Path("configs/evaluation/diagnosis_variant_fairness_freeze.json")


@dataclass(frozen=True)
class SmokeResponseAssessment:
    """In-memory accepted payload; the receipt stores only its digest and repair paths."""

    payload: dict[str, object]
    accepted_payload_sha256: str
    format_repaired_fields: tuple[str, ...]


class SmokeResponseValidationError(ValueError):
    """Allowlisted diagnostics that never contain generated text or evidence IDs."""

    def __init__(self, code: str, fields: tuple[str, ...] = ()) -> None:
        super().__init__(code)
        self.code = code
        self.fields = fields


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


def _claim_id_schema(schema: dict[str, object]) -> dict[str, object]:
    node: object = schema
    for key in ("properties", "atomic_claims", "items", "properties", "claim_local_id"):
        if not isinstance(node, dict):
            raise ValueError("main claim ID schema is unavailable")
        node = node.get(key)
    if not isinstance(node, dict):
        raise ValueError("main claim ID schema is unavailable")
    return cast(dict[str, object], node)


def main_provider_wire_schema(schema_json: str) -> dict[str, object]:
    """Replace the simple claim-ID regex with an equivalent provider enum."""

    schema = json.loads(schema_json)
    if not isinstance(schema, dict):
        raise ValueError("main recovery schema must be an object")
    validate_response_schema(schema)
    properties = schema.get("properties")
    schema_version = properties.get("schema_version") if isinstance(properties, dict) else None
    if (
        not isinstance(schema_version, dict)
        or schema_version.get("const") != MAIN_RECOVERY_SCHEMA_VERSION
        or _claim_id_schema(schema) != {"type": "string", "pattern": "^claim-[1-5]$"}
    ):
        raise ValueError("wire projection requires the registered main schema")
    projected = cast(dict[str, object], _remove_patterns(schema))
    _claim_id_schema(projected)["enum"] = [f"claim-{index}" for index in range(1, 6)]
    validate_response_schema(projected)
    return projected


def main_wire_schema_json(schema_json: str) -> str:
    return canonical_project_json(main_provider_wire_schema(schema_json))


class OpenAIMainRecoveryAdapter(OpenAIRecoveryAdapter):
    """Project only final diagnoses; retrieval selections retain their frozen schema."""

    def _prepare_payload(
        self,
        call: ProviderCall,
        payload: dict[str, object],
    ) -> dict[str, object]:
        if '"diagnosis-main-selection-output/v1"' in call.response_schema_json:
            return super()._prepare_payload(call, payload)
        try:
            wire = main_provider_wire_schema(call.response_schema_json)
            if call.runtime_policy.resource_policy_ref in _CITATION_FREE_FINAL_RESOURCE_REFS:
                citation_schema: object = wire
                for key in (
                    "properties",
                    "atomic_claims",
                    "items",
                    "properties",
                    "visible_evidence_ids",
                ):
                    if not isinstance(citation_schema, dict):
                        raise ValueError("citation-free wire schema is unavailable")
                    citation_schema = citation_schema.get(key)
                if not isinstance(citation_schema, dict) or citation_schema.get("type") != "array":
                    raise ValueError("citation-free wire schema is unavailable")
                citation_schema["maxItems"] = 0
            response_format = _openai_response_format(canonical_project_json(wire))
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


_CITATION_FREE_FINAL_RESOURCE_REFS = frozenset(
    f"ev-{canonical_execution_sha256({'route': variant, 'turn': turn, 'kind': 'final'})}"
    for variant, turn in (("A1", 1), ("B1", 1), ("B2", 2))
)


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
    prior_smoke_receipt_sha256: str,
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
        "transport_sha256": MAIN_RECOVERY_TRANSPORT_V2_SHA256,
        "format_only_repair_policy_sha256": FORMAT_ONLY_REPAIR_SHA256,
        "outbound_payload": outbound,
        "outbound_payload_sha256": canonical_execution_sha256(outbound),
        "predecessor_receipt_sha256": predecessor_receipt_sha256,
        "predecessor_tree_sha256": predecessor_tree_sha256,
        "prior_smoke_receipt_sha256": prior_smoke_receipt_sha256,
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


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise SmokeResponseValidationError("duplicate_json_key")
        result[key] = value
    return result


def _trim_allowed(
    container: dict[str, object] | list[object],
    key: str | int,
    path: str,
    allowed: frozenset[str],
    fields: list[str],
) -> None:
    if path not in allowed:
        return
    if isinstance(container, dict):
        if not isinstance(key, str):
            raise SmokeResponseValidationError("wire_schema_invalid")
        value = container[key]
    else:
        if not isinstance(key, int):
            raise SmokeResponseValidationError("wire_schema_invalid")
        value = container[key]
    if isinstance(value, str) and (stripped := value.strip()) != value:
        if isinstance(container, dict):
            container[cast(str, key)] = stripped
        else:
            container[cast(int, key)] = stripped
        fields.append(path)


def _trim_claim_fields(
    claim: dict[str, object], prefix: str, allowed: frozenset[str], fields: list[str]
) -> None:
    for key in ("claim_text",):
        if key in claim:
            _trim_allowed(claim, key, f"{prefix}.{key}", allowed, fields)
    parts = claim.get("material_parts")
    if isinstance(parts, list):
        for part_index, part in enumerate(parts):
            if isinstance(part, dict):
                for key in ("part_id", "text"):
                    if key in part:
                        _trim_allowed(
                            part,
                            key,
                            f"{prefix}.material_parts[{part_index}].{key}",
                            allowed,
                            fields,
                        )
    evidence_ids = claim.get("visible_evidence_ids")
    if isinstance(evidence_ids, list):
        for evidence_index in range(len(evidence_ids)):
            _trim_allowed(
                evidence_ids,
                evidence_index,
                f"{prefix}.visible_evidence_ids[{evidence_index}]",
                allowed,
                fields,
            )


def _strip_boundary_whitespace(
    payload: dict[str, object], mismatch_paths: tuple[str, ...]
) -> tuple[dict[str, object], tuple[str, ...]]:
    """Copy and trim only the six allowlisted fields; never rewrite claim meaning."""

    copied = deepcopy(payload)
    allowed = frozenset(mismatch_paths)
    fields: list[str] = []
    if "abstention_reason" in copied:
        _trim_allowed(copied, "abstention_reason", "abstention_reason", allowed, fields)
    claims = copied.get("atomic_claims")
    if isinstance(claims, list):
        for claim_index, claim in enumerate(claims):
            if isinstance(claim, dict):
                _trim_claim_fields(claim, f"atomic_claims[{claim_index}]", allowed, fields)
    return copied, tuple(fields)


def _pattern_mismatch_fields(
    value: object, schema: dict[str, object], path: str = ""
) -> tuple[str, ...]:
    """Report schema paths only; never include generated values in a receipt."""

    alternatives = schema.get("anyOf")
    if isinstance(alternatives, list):
        for alternative in alternatives:
            if isinstance(alternative, dict) and (
                (alternative.get("type") == "string" and isinstance(value, str))
                or (alternative.get("type") == "null" and value is None)
            ):
                return _pattern_mismatch_fields(value, alternative, path)
        return ()
    if schema.get("type") == "string" and isinstance(value, str):
        pattern = schema.get("pattern")
        return (path,) if isinstance(pattern, str) and re.search(pattern, value) is None else ()
    if schema.get("type") == "object" and isinstance(value, dict):
        properties = schema.get("properties")
        if isinstance(properties, dict):
            return tuple(
                child_path
                for key, child_schema in properties.items()
                if key in value and isinstance(child_schema, dict)
                for child_path in _pattern_mismatch_fields(
                    value[key], child_schema, f"{path}.{key}" if path else key
                )
            )
    if schema.get("type") == "array" and isinstance(value, list):
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            return tuple(
                child_path
                for index, item in enumerate(value)
                for child_path in _pattern_mismatch_fields(item, item_schema, f"{path}[{index}]")
            )
    return ()


def _validated_wire_response(
    raw: bytes, request: GatewayRequest
) -> tuple[dict[str, object], dict[str, object]]:
    try:
        text = raw.decode("utf-8", errors="strict")
        parsed = json.loads(text, object_pairs_hook=_reject_duplicate_keys)
    except SmokeResponseValidationError:
        raise
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise SmokeResponseValidationError("invalid_json_or_encoding") from exc
    if not isinstance(parsed, dict):
        raise SmokeResponseValidationError("wire_schema_invalid")
    original_schema = json.loads(request.response_schema_json)
    if not isinstance(original_schema, dict):
        raise SmokeResponseValidationError("original_schema_unavailable")
    try:
        validate_response_payload(parsed, main_provider_wire_schema(request.response_schema_json))
    except GatewayContractError as exc:
        raise SmokeResponseValidationError("wire_schema_invalid") from exc
    return parsed, original_schema


def _original_schema_or_boundary_repair(
    parsed: dict[str, object], original_schema: dict[str, object]
) -> tuple[dict[str, object], tuple[str, ...]]:
    try:
        validate_response_payload(parsed, original_schema)
    except GatewayContractError:
        mismatches = _pattern_mismatch_fields(parsed, original_schema)
        if not mismatches:
            raise SmokeResponseValidationError("local_schema_invalid") from None
        accepted, repaired_fields = _strip_boundary_whitespace(parsed, mismatches)
        if not repaired_fields:
            raise SmokeResponseValidationError("unrepaired_pattern_mismatch", mismatches) from None
        try:
            validate_response_payload(accepted, original_schema)
        except GatewayContractError as exc:
            raise SmokeResponseValidationError(
                "unrepaired_pattern_mismatch", _pattern_mismatch_fields(accepted, original_schema)
            ) from exc
        return accepted, repaired_fields
    return parsed, ()


def _validate_synthetic_semantics(accepted: dict[str, object]) -> None:
    try:
        output = validate_main_provider_output(
            canonical_project_json(accepted),
            variant="A2",
            visible_evidence_ids={SYNTHETIC_EVIDENCE_ID},
        )
    except (ValueError, TypeError) as exc:
        raise SmokeResponseValidationError("semantic_contract_invalid") from exc
    if not any(
        claim.claim_type in {"cause_assertion", "evidence_statement"}
        and SYNTHETIC_EVIDENCE_ID in claim.visible_evidence_ids
        for claim in output.atomic_claims
    ):
        raise SmokeResponseValidationError("synthetic_coverage_invalid")


def assess_smoke_response(raw: bytes, *, request: GatewayRequest) -> SmokeResponseAssessment:
    """Accept original-valid output or boundary-only repair, otherwise fail closed."""

    parsed, original_schema = _validated_wire_response(raw, request)
    accepted, repaired_fields = _original_schema_or_boundary_repair(parsed, original_schema)
    _validate_synthetic_semantics(accepted)
    return SmokeResponseAssessment(
        payload=accepted,
        accepted_payload_sha256=canonical_execution_sha256(accepted),
        format_repaired_fields=repaired_fields,
    )


def validate_self_hash(payload: Mapping[str, object], field: str) -> None:
    declared = payload.get(field)
    identity = {key: value for key, value in payload.items() if key != field}
    if declared != canonical_execution_sha256(identity):
        raise ValueError(f"{field} does not match canonical content")


__all__ = [
    "EXPECTED_FAILED_MAIN_COUNTS",
    "EXPECTED_PRIOR_SMOKE_RECEIPT_SHA256",
    "FORMAT_ONLY_REPAIR_POLICY",
    "FORMAT_ONLY_REPAIR_SHA256",
    "MAIN_RECOVERY_SCHEMA_VERSION",
    "MAIN_RECOVERY_TRANSPORT_CONTRACT",
    "MAIN_RECOVERY_TRANSPORT_SHA256",
    "MAIN_RECOVERY_TRANSPORT_V2_CONTRACT",
    "MAIN_RECOVERY_TRANSPORT_V2_SHA256",
    "OpenAIMainRecoveryAdapter",
    "SMOKE_DESTINATION",
    "SMOKE_PLAN_SCHEMA_VERSION",
    "SMOKE_RECEIPT_SCHEMA_VERSION",
    "SmokeResponseAssessment",
    "SmokeResponseValidationError",
    "assess_smoke_response",
    "build_smoke_plan",
    "build_synthetic_main_schema_request",
    "exact_outbound_payload",
    "main_provider_wire_schema",
    "main_wire_schema_json",
    "provider_call",
    "validate_self_hash",
    "validate_smoke_response",
]
