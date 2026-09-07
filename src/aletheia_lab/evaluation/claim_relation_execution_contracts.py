"""Immutable contracts for blind claim-relation execution."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256
from aletheia_lab.project.identity import SHA256_PATTERN

PLAN_SCHEMA_VERSION: Final = "claim-relation-execution-plan/v1"
AUTHORIZATION_SCHEMA_VERSION: Final = "claim-relation-execution-authorization/v1"
LEASE_SCHEMA_VERSION: Final = "claim-relation-execution-lease/v1"
PREFLIGHT_SCHEMA_VERSION: Final = "claim-relation-execution-preflight/v1"
REHEARSAL_SCHEMA_VERSION: Final = "claim-relation-execution-rehearsal/v1"
RECEIPT_SCHEMA_VERSION: Final = "claim-relation-execution-receipt/v1"
EXPECTED_RELATION_REQUEST_COUNT: Final = 962
MINIMUM_PROVIDER_INTERVAL_MS: Final = 1000
RESPONSE_FORMAT_TOKEN_ALLOWANCE: Final = 1024
INPUT_USD_PER_MILLION: Final = 2.0
OUTPUT_USD_PER_MILLION: Final = 8.0

Sha256 = Annotated[str, Field(pattern=SHA256_PATTERN)]


class ClaimRelationExecutionError(ValueError):
    """Raised when relation execution cannot preserve its frozen boundary."""


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, revalidate_instances="always", allow_inf_nan=False
    )


class ClaimRelationExecutionPlan(_StrictFrozenModel):
    schema_version: Literal["claim-relation-execution-plan/v1"] = PLAN_SCHEMA_VERSION
    source_commit_ref: str = Field(pattern=r"^[0-9a-f]{40}$")
    preparation_sha256: Sha256
    recovery_closeout_sha256: Sha256
    evidence_semantics_policy_sha256: Sha256
    request_census_sha256: Sha256
    request_count: Literal[962]
    model: Literal["gpt-4.1"]
    model_snapshot: Literal["gpt-4.1-2025-04-14"]
    maximum_output_tokens_per_request: Literal[600]
    maximum_provider_attempts_per_request: Literal[2]
    minimum_provider_interval_ms: Literal[1000]
    tokenizer_name: Literal["tiktoken"]
    tokenizer_version: Literal["0.14.0"]
    tokenizer_encoding: Literal["o200k_base"]
    response_format_token_allowance_per_request: Literal[1024]
    input_usd_per_million_tokens: float = Field(gt=0)
    output_usd_per_million_tokens: float = Field(gt=0)
    exact_message_input_token_count: int = Field(gt=0)
    conservative_input_token_ceiling: int = Field(gt=0)
    output_token_ceiling: int = Field(gt=0)
    estimated_upper_cost_usd: float = Field(gt=0)
    assignment_request_sha256s: tuple[Sha256, ...] = Field(min_length=962, max_length=962)
    provider_calls_executed: Literal[False] = False
    claims_materialized: Literal[False] = False
    automatic_labels_generated: Literal[False] = False
    blind_packets_generated: Literal[False] = False
    human_annotations_collected: Literal[False] = False
    main_or_sealed_outcomes_opened: Literal[False] = False
    plan_sha256: Sha256

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"plan_sha256"})

    @model_validator(mode="after")
    def _reconciles(self) -> Self:
        expected_output = self.request_count * self.maximum_output_tokens_per_request * 2
        expected_input = self.exact_message_input_token_count * 2 + (
            self.request_count * self.response_format_token_allowance_per_request * 2
        )
        expected_cost = round(
            expected_input * self.input_usd_per_million_tokens / 1_000_000
            + expected_output * self.output_usd_per_million_tokens / 1_000_000,
            6,
        )
        if (
            len(set(self.assignment_request_sha256s)) != self.request_count
            or self.output_token_ceiling != expected_output
            or self.conservative_input_token_ceiling != expected_input
            or self.input_usd_per_million_tokens != INPUT_USD_PER_MILLION
            or self.output_usd_per_million_tokens != OUTPUT_USD_PER_MILLION
            or self.estimated_upper_cost_usd != expected_cost
            or self.plan_sha256 != canonical_execution_sha256(self.identity_payload())
        ):
            raise ValueError("relation execution plan accounting or identity differs")
        return self


class ClaimRelationExecutionAuthorization(_StrictFrozenModel):
    schema_version: Literal["claim-relation-execution-authorization/v1"] = (
        AUTHORIZATION_SCHEMA_VERSION
    )
    authorization_ref: str = Field(pattern=r"^ev-[0-9a-f]{64}$")
    authorized_at: str
    source_commit_ref: str = Field(pattern=r"^[0-9a-f]{40}$")
    plan_sha256: Sha256
    rehearsal_sha256: Sha256
    preparation_sha256: Sha256
    request_census_sha256: Sha256
    request_count: Literal[962]
    estimated_upper_cost_usd: float = Field(gt=0)
    operator_cost_ceiling_usd: float = Field(gt=0)
    destination_sha256: Sha256
    registered_attempts: Literal[1] = 1
    credential_stored: Literal[False] = False
    claims_materialized: Literal[False] = False
    blind_packets_generated: Literal[False] = False
    human_annotations_collected: Literal[False] = False
    main_or_sealed_outcomes_opened: Literal[False] = False
    authorization_sha256: Sha256

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"authorization_ref", "authorization_sha256"})

    @model_validator(mode="after")
    def _reconciles(self) -> Self:
        try:
            parsed = datetime.fromisoformat(self.authorized_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("relation authorization timestamp is invalid") from exc
        digest = canonical_execution_sha256(self.identity_payload())
        if (
            not self.authorized_at.endswith("Z")
            or parsed.utcoffset() is None
            or self.operator_cost_ceiling_usd < self.estimated_upper_cost_usd
            or self.authorization_sha256 != digest
            or self.authorization_ref != f"ev-{digest}"
        ):
            raise ValueError("relation execution authorization does not reconcile")
        return self


class ClaimRelationExecutionLease(_StrictFrozenModel):
    schema_version: Literal["claim-relation-execution-lease/v1"] = LEASE_SCHEMA_VERSION
    authorization_sha256: Sha256
    plan_sha256: Sha256
    destination_sha256: Sha256
    registered_attempts: Literal[1] = 1
    lease_sha256: Sha256

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"lease_sha256"})

    @model_validator(mode="after")
    def _reconciles(self) -> Self:
        if self.lease_sha256 != canonical_execution_sha256(self.identity_payload()):
            raise ValueError("relation execution lease identity differs")
        return self


class ClaimRelationExecutionRehearsal(_StrictFrozenModel):
    schema_version: Literal["claim-relation-execution-rehearsal/v1"] = REHEARSAL_SCHEMA_VERSION
    status: Literal["claim_relation_execution_rehearsal_passed"]
    plan_sha256: Sha256
    preparation_sha256: Sha256
    policy_sha256: Sha256
    request_count: Literal[962]
    maximum_visible_evidence_items_observed: int = Field(ge=1, le=32)
    provider_input_fields: tuple[Literal["claim_text", "claim_type", "visible_evidence"], ...]
    valid_response_sha256: Sha256
    incoherent_relation_rejected: Literal[True]
    unknown_evidence_rejected: Literal[True]
    provider_calls_executed: Literal[False] = False
    claims_materialized: Literal[False] = False
    blind_packets_generated: Literal[False] = False
    human_annotations_collected: Literal[False] = False
    main_or_sealed_outcomes_opened: Literal[False] = False
    rehearsal_sha256: Sha256

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"rehearsal_sha256"})

    @model_validator(mode="after")
    def _reconciles(self) -> Self:
        if self.provider_input_fields != (
            "claim_text",
            "claim_type",
            "visible_evidence",
        ) or self.rehearsal_sha256 != canonical_execution_sha256(self.identity_payload()):
            raise ValueError("relation execution rehearsal identity differs")
        return self


class ClaimRelationExecutionPreflight(_StrictFrozenModel):
    schema_version: Literal["claim-relation-execution-preflight/v1"] = PREFLIGHT_SCHEMA_VERSION
    status: Literal["claim_relation_execution_live_blocked", "claim_relation_execution_live_ready"]
    plan_sha256: Sha256
    preparation_sha256: Sha256
    source_commit_ref: str = Field(pattern=r"^[0-9a-f]{40}$")
    clean_synchronized_main: bool
    credential_present: bool
    request_count: Literal[962]
    exact_message_input_token_count: int = Field(gt=0)
    estimated_upper_cost_usd: float = Field(gt=0)
    operator_cost_ceiling_usd: float | None = Field(default=None, gt=0)
    minimum_provider_interval_ms: Literal[1000]
    live_blockers: tuple[
        Literal[
            "authorization_pending", "credential_missing", "repository_not_clean_synchronized_main"
        ],
        ...,
    ]
    provider_calls_executed: Literal[False] = False
    claims_materialized: Literal[False] = False
    blind_packets_generated: Literal[False] = False
    human_annotations_collected: Literal[False] = False
    main_or_sealed_outcomes_opened: Literal[False] = False
    preflight_sha256: Sha256

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"preflight_sha256"})

    @model_validator(mode="after")
    def _reconciles(self) -> Self:
        if (not self.live_blockers) != (
            self.status == "claim_relation_execution_live_ready"
        ) or self.preflight_sha256 != canonical_execution_sha256(self.identity_payload()):
            raise ValueError("relation preflight status or identity differs")
        return self


class RelationRequestAuthority(_StrictFrozenModel):
    assignment_request_sha256: Sha256
    preparation_sha256: Sha256
    policy_sha256: Sha256
    provider_payload_sha256: Sha256
    authority_sha256: Sha256

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"authority_sha256"})

    @model_validator(mode="after")
    def _reconciles(self) -> Self:
        if self.authority_sha256 != canonical_execution_sha256(self.identity_payload()):
            raise ValueError("relation request authority identity differs")
        return self


class ClaimRelationExecutionReceipt(_StrictFrozenModel):
    schema_version: Literal["claim-relation-execution-receipt/v1"] = RECEIPT_SCHEMA_VERSION
    status: Literal[
        "claim_relation_execution_complete",
        "claim_relation_execution_complete_with_technical_failures",
    ]
    authorization_sha256: Sha256
    plan_sha256: Sha256
    preparation_sha256: Sha256
    policy_sha256: Sha256
    source_commit_ref: str = Field(pattern=r"^[0-9a-f]{40}$")
    terminal_store_sha256: Sha256
    relation_result_bundle_sha256: Sha256
    terminal_request_count: Literal[962]
    parsed_count: int = Field(ge=0, le=962)
    technical_failure_count: int = Field(ge=0, le=962)
    provider_terminal_failure_count: int = Field(ge=0, le=962)
    semantic_validation_failure_count: int = Field(ge=0, le=962)
    provider_attempt_count: int = Field(ge=962, le=1924)
    gateway_status_counts: dict[str, int]
    claims_materialized: Literal[False] = False
    automatic_labels_generated: Literal[False] = False
    blind_packets_generated: Literal[False] = False
    human_annotations_collected: Literal[False] = False
    main_or_sealed_outcomes_opened: Literal[False] = False
    receipt_sha256: Sha256

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"receipt_sha256"})

    @model_validator(mode="after")
    def _reconciles(self) -> Self:
        if (
            self.parsed_count + self.technical_failure_count != self.terminal_request_count
            or self.provider_terminal_failure_count + self.semantic_validation_failure_count
            != self.technical_failure_count
            or sum(self.gateway_status_counts.values()) != self.terminal_request_count
            or (self.technical_failure_count == 0)
            != (self.status == "claim_relation_execution_complete")
            or self.receipt_sha256 != canonical_execution_sha256(self.identity_payload())
        ):
            raise ValueError("relation execution receipt counts or identity differ")
        return self


__all__ = [
    "EXPECTED_RELATION_REQUEST_COUNT",
    "INPUT_USD_PER_MILLION",
    "LEASE_SCHEMA_VERSION",
    "MINIMUM_PROVIDER_INTERVAL_MS",
    "OUTPUT_USD_PER_MILLION",
    "PLAN_SCHEMA_VERSION",
    "PREFLIGHT_SCHEMA_VERSION",
    "REHEARSAL_SCHEMA_VERSION",
    "RECEIPT_SCHEMA_VERSION",
    "RESPONSE_FORMAT_TOKEN_ALLOWANCE",
    "ClaimRelationExecutionAuthorization",
    "ClaimRelationExecutionError",
    "ClaimRelationExecutionLease",
    "ClaimRelationExecutionPlan",
    "ClaimRelationExecutionPreflight",
    "ClaimRelationExecutionRehearsal",
    "ClaimRelationExecutionReceipt",
    "RelationRequestAuthority",
]
