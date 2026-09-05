"""Prospective recovery contract for claim-corpus output normalization.

The first live diagnosis attempt remains immutable and terminal.  This module
registers a new response boundary that removes provider-authored structural
identifiers, constrains every array at the gateway, and binds citations to the
evidence IDs visible in that request.  It does not authorize or execute a
provider call and it cannot reuse output from the retired attempt.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Annotated, Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from aletheia_lab.evaluation.claim_corpus_adapters import normalize_variant_output
from aletheia_lab.evaluation.claim_corpus_contracts import (
    SUPPORT_LABELS,
    AtomicClaimV2,
    ClaimCorpusContractError,
    ClaimCorpusRequest,
    ClaimCorpusRequestCensus,
    DiagnosisOutputV2,
    MaterialClaimPart,
)
from aletheia_lab.evaluation.claim_corpus_protocol import (
    load_claim_support_corpus_protocol,
)
from aletheia_lab.evaluation.claim_corpus_readiness import FAIRNESS_PATH, REQUEST_CENSUS_PATH
from aletheia_lab.evaluation.claim_evidence_census import ObservedEvidenceCensus
from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256
from aletheia_lab.evaluation.variant_fairness import load_diagnosis_variant_freeze
from aletheia_lab.model_gateway import validate_response_payload, validate_response_schema
from aletheia_lab.project.identity import (
    SHA256_PATTERN,
    canonical_project_json,
    content_sha256,
    normalize_text,
)

RECOVERY_PROTOCOL_SCHEMA_VERSION: Final = "claim-corpus-normalization-recovery/v1"
PROVIDER_OUTPUT_SCHEMA_VERSION_V2: Final = "diagnosis-provider-output/2"
RECOVERY_PROTOCOL_PATH: Final = "configs/evaluation/claim_support_normalization_recovery_v2.json"
OBSERVED_EVIDENCE_PATH: Final = "configs/evaluation/claim_support_observed_evidence_census.json"
CORPUS_PROTOCOL_PATH: Final = "configs/evaluation/claim_support_corpus_protocol.json"

PREDECESSOR_SOURCE_COMMIT: Final = "ef2f31a653c00ab4cb02b517ae68be8c69188ba6"
PREDECESSOR_AUTHORIZATION_SHA256: Final = (
    "5a0a7b32996bfebb16f47dcb01ff980546a43b1f5ec6a00f6e9e4febd83ad6e0"
)
PREDECESSOR_EXECUTION_PLAN_SHA256: Final = (
    "ae377cf6f8dff56e1e6b717d44c47174d9319e8be1d8730a3ca252d8f89a0621"
)
PREDECESSOR_LIVE_RECEIPT_SHA256: Final = (
    "79169a92a380bfdc85f3163a453f687a5533312c5c3f59fbfc81bf90c72a79b3"
)
PREDECESSOR_TERMINAL_STORE_SHA256: Final = (
    "0e3e6e64e99b8436680c425de968c5e3f2369d57e06cc59002999d381f28a470"
)
PREDECESSOR_RECONCILIATION_SHA256: Final = (
    "b12f262f9b6a5b485a5ab8b60a555d23f925c817381443dc3a8ed001e868ac15"
)
PREDECESSOR_PREPARATION_SHA256: Final = (
    "6cedbf39fc15fb091f24b5d4f96b4e18dfeeebafe28951568257d5149fab168a"
)

RECOVERY_SEMANTIC_INSTRUCTION: Final = (
    "Serialize the diagnosis required by the variant instructions using the "
    "provided response schema. Do not emit claim or part identifiers; the "
    "consumer assigns them by array order. Evidence identifiers must be drawn "
    "from the supplied context. This serialization instruction does not change "
    "the variant's diagnostic, reasoning, or abstention policy."
)

Sha256 = Annotated[str, Field(pattern=SHA256_PATTERN)]
ClaimType = Literal[
    "cause_assertion",
    "evidence_statement",
    "uncertainty_statement",
    "recommended_action",
    "other",
]


class ClaimCorpusNormalizationRecoveryError(ValueError):
    """Raised when the recovery registration or response boundary diverges."""


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        revalidate_instances="always",
        allow_inf_nan=False,
    )


class ProviderMaterialPartV2(_StrictFrozenModel):
    """Semantic part without a provider-authored structural identifier."""

    text: str

    @field_validator("text")
    @classmethod
    def _text_is_bounded(cls, value: str) -> str:
        return normalize_text(value, label="provider material claim part", max_length=1024)


class ProviderAtomicClaimV2(_StrictFrozenModel):
    """Provider claim whose local IDs are assigned deterministically downstream."""

    claim_type: ClaimType
    claim_text: str
    material_parts: tuple[ProviderMaterialPartV2, ...] = Field(min_length=1, max_length=8)
    visible_evidence_ids: tuple[str, ...] = Field(min_length=1, max_length=32)

    @field_validator("claim_text")
    @classmethod
    def _claim_is_bounded(cls, value: str) -> str:
        return normalize_text(value, label="provider atomic claim", max_length=2048)


class ProviderCompletedResultV2(_StrictFrozenModel):
    output_status: Literal["completed"]
    atomic_claims: tuple[ProviderAtomicClaimV2, ...] = Field(min_length=1, max_length=5)


class ProviderAbstainedResultV2(_StrictFrozenModel):
    output_status: Literal["abstained"]
    abstention_reason: str

    @field_validator("abstention_reason")
    @classmethod
    def _reason_is_bounded(cls, value: str) -> str:
        return normalize_text(value, label="provider abstention reason", max_length=2048)


ProviderResultV2 = Annotated[
    ProviderCompletedResultV2 | ProviderAbstainedResultV2,
    Field(discriminator="output_status"),
]


class ProviderDiagnosisOutputV2(_StrictFrozenModel):
    """Exact provider envelope for the prospective recovery attempt."""

    schema_version: Literal["diagnosis-provider-output/2"]
    result: ProviderResultV2


class ClaimCorpusNormalizationRecoveryProtocol(_StrictFrozenModel):
    """Hash-bound, outcome-blind registration for one clean recovery attempt."""

    schema_version: Literal["claim-corpus-normalization-recovery/v1"] = (
        RECOVERY_PROTOCOL_SCHEMA_VERSION
    )
    status: Literal["normalization_recovery_contract_frozen_authorization_pending"]
    recovery_id: Literal["claim-support-normalization-contract-v2"]
    predecessor_source_commit_ref: str = Field(pattern=r"^[0-9a-f]{40}$")
    predecessor_authorization_sha256: Sha256
    predecessor_execution_plan_sha256: Sha256
    predecessor_live_receipt_sha256: Sha256
    predecessor_terminal_store_sha256: Sha256
    predecessor_reconciliation_sha256: Sha256
    predecessor_preparation_sha256: Sha256
    predecessor_terminal_request_count: Literal[360]
    predecessor_parsed_terminal_count: Literal[258]
    predecessor_technical_failure_count: Literal[102]
    predecessor_normalized_output_count: Literal[49]
    predecessor_schema_rejection_count: Literal[209]
    predecessor_claim_candidate_count: Literal[152]
    target_claim_count: Literal[200]
    corpus_protocol_sha256: Sha256
    request_census_sha256: Sha256
    fairness_freeze_sha256: Sha256
    observed_evidence_census_sha256: Sha256
    provider_output_schema_version: Literal["diagnosis-provider-output/2"]
    response_schema_set_sha256: Sha256
    semantic_instruction_sha256: Sha256
    model: Literal["gpt-4.1"]
    model_snapshot: Literal["gpt-4.1-2025-04-14"]
    primary_request_count: Literal[360]
    model_request_count: Literal[315]
    deterministic_request_count: Literal[45]
    registered_recovery_attempts: Literal[1]
    correction_scope: Literal["provider_response_contract_only"]
    request_census_unchanged: Literal[True]
    evidence_census_unchanged: Literal[True]
    variant_matrix_unchanged: Literal[True]
    model_policy_unchanged: Literal[True]
    resource_budgets_unchanged: Literal[True]
    selection_policy_unchanged: Literal[True]
    predecessor_attempt_retired: Literal[True]
    predecessor_outputs_reused: Literal[False]
    predecessor_failures_preserved: Literal[True]
    new_authorization_required: Literal[True]
    provider_calls_executed: Literal[False]
    outputs_generated: Literal[False]
    claims_materialized: Literal[False]
    automatic_labels_generated: Literal[False]
    blind_packets_generated: Literal[False]
    human_annotations_collected: Literal[False]
    main_or_sealed_outcomes_opened: Literal[False]
    protocol_sha256: Sha256

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"protocol_sha256"})

    @model_validator(mode="after")
    def _counts_and_identity_reconcile(self) -> Self:
        if (
            self.predecessor_parsed_terminal_count + self.predecessor_technical_failure_count
            != self.predecessor_terminal_request_count
            or self.predecessor_normalized_output_count + self.predecessor_schema_rejection_count
            != self.predecessor_parsed_terminal_count
            or self.predecessor_claim_candidate_count >= self.target_claim_count
            or self.model_request_count + self.deterministic_request_count
            != self.primary_request_count
        ):
            raise ValueError("recovery protocol census does not reconcile")
        if self.protocol_sha256 != canonical_execution_sha256(self.identity_payload()):
            raise ValueError("recovery protocol identity does not match content")
        return self


def _bounded_nonblank_pattern(maximum: int) -> str:
    # Match the downstream trimmed/control-free text contract. A negative
    # end assertion is deliberate: `$` also matches before a final newline.
    edge = r"[^\s\x00-\x1f\x7f\ud800-\udfff]"
    middle = r"[^\x00-\x1f\x7f\ud800-\udfff]"
    return rf"^{edge}(?:{middle}{{0,{maximum - 2}}}{edge})?(?![\s\S])"


def provider_response_schema_v2(
    visible_evidence_ids: Sequence[str],
) -> dict[str, object]:
    """Build the exact per-context schema accepted by the recovery gateway."""

    evidence_ids = tuple(visible_evidence_ids)
    if (
        not 1 <= len(evidence_ids) <= 32
        or len(set(evidence_ids)) != len(evidence_ids)
        or any(not isinstance(value, str) or not value for value in evidence_ids)
    ):
        raise ClaimCorpusNormalizationRecoveryError(
            "visible evidence IDs must be one to 32 unique non-empty strings"
        )
    part = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "text": {
                "type": "string",
                "pattern": _bounded_nonblank_pattern(1024),
            }
        },
        "required": ["text"],
    }
    claim = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "claim_type": {
                "type": "string",
                "enum": [
                    "cause_assertion",
                    "evidence_statement",
                    "uncertainty_statement",
                    "recommended_action",
                    "other",
                ],
            },
            "claim_text": {
                "type": "string",
                "pattern": _bounded_nonblank_pattern(2048),
            },
            "material_parts": {
                "type": "array",
                "items": part,
                "minItems": 1,
                "maxItems": 8,
            },
            "visible_evidence_ids": {
                "type": "array",
                "items": {"type": "string", "enum": list(evidence_ids)},
                "minItems": 1,
                "maxItems": 32,
            },
        },
        "required": [
            "claim_type",
            "claim_text",
            "material_parts",
            "visible_evidence_ids",
        ],
    }
    completed = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "output_status": {"type": "string", "const": "completed"},
            "atomic_claims": {
                "type": "array",
                "items": claim,
                "minItems": 1,
                "maxItems": 5,
            },
        },
        "required": ["output_status", "atomic_claims"],
    }
    abstained = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "output_status": {"type": "string", "const": "abstained"},
            "abstention_reason": {
                "type": "string",
                "pattern": _bounded_nonblank_pattern(2048),
            },
        },
        "required": ["output_status", "abstention_reason"],
    }
    schema: dict[str, object] = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "schema_version": {
                "type": "string",
                "const": PROVIDER_OUTPUT_SCHEMA_VERSION_V2,
            },
            "result": {"anyOf": [completed, abstained]},
        },
        "required": ["schema_version", "result"],
    }
    validate_response_schema(schema)
    return schema


def normalize_provider_output_v2(
    request: ClaimCorpusRequest,
    payload: Mapping[str, object],
    *,
    source_record_sha256: str,
    visible_evidence_ids: Sequence[str],
) -> DiagnosisOutputV2:
    """Normalize only a schema-valid v2 envelope using ordinal structural IDs."""

    checked_payload = dict(payload)
    try:
        validate_response_payload(
            checked_payload,
            provider_response_schema_v2(visible_evidence_ids),
        )
        source = ProviderDiagnosisOutputV2.model_validate_json(
            canonical_project_json(checked_payload)
        )
    except (ValidationError, ValueError) as exc:
        raise ClaimCorpusContractError(
            "stored provider output is incompatible with the recovery schema"
        ) from exc

    if isinstance(source.result, ProviderCompletedResultV2):
        claims = tuple(
            AtomicClaimV2(
                claim_local_id=f"claim-{claim_index}",
                claim_type=claim.claim_type,
                claim_text=claim.claim_text,
                material_parts=tuple(
                    MaterialClaimPart(part_id=f"part-{part_index}", text=part.text)
                    for part_index, part in enumerate(claim.material_parts, start=1)
                ),
                visible_evidence_ids=tuple(dict.fromkeys(claim.visible_evidence_ids)),
            )
            for claim_index, claim in enumerate(source.result.atomic_claims, start=1)
        )
        target_payload: dict[str, object] = {
            "schema_version": "diagnosis-output/2",
            "output_status": "completed",
            "atomic_claims": tuple(item.model_dump(mode="python") for item in claims),
            "abstention_reason": None,
            "parse_failure_code": None,
            "source_record_sha256": source_record_sha256,
        }
    else:
        target_payload = {
            "schema_version": "diagnosis-output/2",
            "output_status": "abstained",
            "atomic_claims": (),
            "abstention_reason": source.result.abstention_reason,
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


def _response_schema_set_sha256(evidence: ObservedEvidenceCensus) -> str:
    entries = []
    for binding in evidence.bindings:
        schema = provider_response_schema_v2(
            tuple(item.evidence_id for item in binding.visible_context.items)
        )
        entries.append(
            {
                "family_id": binding.family_id,
                "evidence_condition": binding.evidence_condition,
                "visible_context_sha256": binding.visible_context.context_sha256,
                "response_schema_sha256": content_sha256(
                    canonical_project_json(schema).encode("utf-8")
                ),
            }
        )
    return canonical_execution_sha256(
        {
            "provider_output_schema_version": PROVIDER_OUTPUT_SCHEMA_VERSION_V2,
            "context_schemas": entries,
        }
    )


def build_recovery_protocol(root: Path) -> ClaimCorpusNormalizationRecoveryProtocol:
    """Rebuild the registration from frozen public inputs without outcomes."""

    census = ClaimCorpusRequestCensus.model_validate_json((root / REQUEST_CENSUS_PATH).read_bytes())
    freeze = load_diagnosis_variant_freeze(root / FAIRNESS_PATH)
    evidence = ObservedEvidenceCensus.model_validate_json(
        (root / OBSERVED_EVIDENCE_PATH).read_bytes()
    )
    corpus_protocol = load_claim_support_corpus_protocol(root / CORPUS_PROTOCOL_PATH)
    model_policy = freeze.model_policies["main_llm_v1"]
    primary_request_count = len(census.primary_requests)
    deterministic_request_count = sum(
        request.variant == "B0" for request in census.primary_requests
    )
    model_request_count = primary_request_count - deterministic_request_count
    target_claim_count = (
        len(SUPPORT_LABELS) * corpus_protocol.family_census.target_claims_per_automatic_label
    )
    payload: dict[str, object] = {
        "schema_version": RECOVERY_PROTOCOL_SCHEMA_VERSION,
        "status": "normalization_recovery_contract_frozen_authorization_pending",
        "recovery_id": "claim-support-normalization-contract-v2",
        "predecessor_source_commit_ref": PREDECESSOR_SOURCE_COMMIT,
        "predecessor_authorization_sha256": PREDECESSOR_AUTHORIZATION_SHA256,
        "predecessor_execution_plan_sha256": PREDECESSOR_EXECUTION_PLAN_SHA256,
        "predecessor_live_receipt_sha256": PREDECESSOR_LIVE_RECEIPT_SHA256,
        "predecessor_terminal_store_sha256": PREDECESSOR_TERMINAL_STORE_SHA256,
        "predecessor_reconciliation_sha256": PREDECESSOR_RECONCILIATION_SHA256,
        "predecessor_preparation_sha256": PREDECESSOR_PREPARATION_SHA256,
        "predecessor_terminal_request_count": 360,
        "predecessor_parsed_terminal_count": 258,
        "predecessor_technical_failure_count": 102,
        "predecessor_normalized_output_count": 49,
        "predecessor_schema_rejection_count": 209,
        "predecessor_claim_candidate_count": 152,
        "target_claim_count": target_claim_count,
        "corpus_protocol_sha256": corpus_protocol.protocol_sha256,
        "request_census_sha256": census.census_sha256,
        "fairness_freeze_sha256": canonical_execution_sha256(freeze.model_dump(mode="json")),
        "observed_evidence_census_sha256": evidence.census_sha256,
        "provider_output_schema_version": PROVIDER_OUTPUT_SCHEMA_VERSION_V2,
        "response_schema_set_sha256": _response_schema_set_sha256(evidence),
        "semantic_instruction_sha256": content_sha256(
            RECOVERY_SEMANTIC_INSTRUCTION.encode("utf-8")
        ),
        "model": model_policy.model,
        "model_snapshot": model_policy.model_version,
        "primary_request_count": primary_request_count,
        "model_request_count": model_request_count,
        "deterministic_request_count": deterministic_request_count,
        "registered_recovery_attempts": 1,
        "correction_scope": "provider_response_contract_only",
        "request_census_unchanged": True,
        "evidence_census_unchanged": True,
        "variant_matrix_unchanged": True,
        "model_policy_unchanged": True,
        "resource_budgets_unchanged": True,
        "selection_policy_unchanged": True,
        "predecessor_attempt_retired": True,
        "predecessor_outputs_reused": False,
        "predecessor_failures_preserved": True,
        "new_authorization_required": True,
        "provider_calls_executed": False,
        "outputs_generated": False,
        "claims_materialized": False,
        "automatic_labels_generated": False,
        "blind_packets_generated": False,
        "human_annotations_collected": False,
        "main_or_sealed_outcomes_opened": False,
    }
    return ClaimCorpusNormalizationRecoveryProtocol.model_validate(
        {**payload, "protocol_sha256": canonical_execution_sha256(payload)}
    )


def load_recovery_protocol(root: Path) -> ClaimCorpusNormalizationRecoveryProtocol:
    """Load and independently reproduce the tracked recovery registration."""

    try:
        registered = ClaimCorpusNormalizationRecoveryProtocol.model_validate_json(
            (root / RECOVERY_PROTOCOL_PATH).read_bytes()
        )
    except (OSError, ValidationError) as exc:
        raise ClaimCorpusNormalizationRecoveryError(
            "normalization recovery registration is unavailable or invalid"
        ) from exc
    expected = build_recovery_protocol(root)
    if registered != expected:
        raise ClaimCorpusNormalizationRecoveryError(
            "normalization recovery registration differs from frozen inputs"
        )
    return registered


__all__ = [
    "PROVIDER_OUTPUT_SCHEMA_VERSION_V2",
    "RECOVERY_PROTOCOL_PATH",
    "RECOVERY_SEMANTIC_INSTRUCTION",
    "ClaimCorpusNormalizationRecoveryError",
    "ClaimCorpusNormalizationRecoveryProtocol",
    "ProviderDiagnosisOutputV2",
    "build_recovery_protocol",
    "load_recovery_protocol",
    "normalize_provider_output_v2",
    "provider_response_schema_v2",
]
