"""Immutable contracts for the seven-request V2 live qualification."""

from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Annotated, Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256
from aletheia_lab.project.identity import SHA256_PATTERN

PLAN_SCHEMA_VERSION: Final = "claim-support-validation-v2-qualification-plan/v1"
REHEARSAL_SCHEMA_VERSION: Final = "claim-support-validation-v2-qualification-rehearsal/v1"
AUTHORIZATION_SCHEMA_VERSION: Final = "claim-support-validation-v2-qualification-authorization/v1"
PREFLIGHT_SCHEMA_VERSION: Final = "claim-support-validation-v2-qualification-preflight/v1"
LEASE_SCHEMA_VERSION: Final = "claim-support-validation-v2-qualification-lease/v1"
RECEIPT_SCHEMA_VERSION: Final = "claim-support-validation-v2-qualification-receipt/v1"
QUALIFICATION_REQUEST_COUNT: Final = 7
PROVIDER_VARIANTS: Final = ("A1", "A2", "A3", "B1", "B2", "CodeGraph", "FULL")
INPUT_USD_PER_MILLION: Final = 2.0
OUTPUT_USD_PER_MILLION: Final = 8.0
RESPONSE_FORMAT_TOKEN_ALLOWANCE: Final = 1024

Sha256 = Annotated[str, Field(pattern=SHA256_PATTERN)]
ProviderVariant = Literal["A1", "A2", "A3", "B1", "B2", "CodeGraph", "FULL"]
GatewayStatus = Literal[
    "parsed",
    "provider_failed",
    "timed_out",
    "retry_exhausted",
    "cancelled",
    "identity_rejected",
    "oversized_response",
    "parse_failed",
]


class ClaimValidationV2QualificationError(ValueError):
    """Raised when qualification cannot preserve its prospective boundary."""


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        revalidate_instances="always",
        allow_inf_nan=False,
    )


class V2QualificationExecutionPlan(_StrictFrozenModel):
    schema_version: Literal["claim-support-validation-v2-qualification-plan/v1"] = (
        PLAN_SCHEMA_VERSION
    )
    source_commit_ref: str = Field(pattern=r"^[0-9a-f]{40}$")
    amendment_sha256: Sha256
    expressiveness_review_sha256: Sha256
    parent_protocol_sha256: Sha256
    parent_runtime_manifest_sha256: Sha256
    request_census_sha256: Sha256
    qualification_request_sha256s: tuple[Sha256, ...] = Field(
        min_length=QUALIFICATION_REQUEST_COUNT,
        max_length=QUALIFICATION_REQUEST_COUNT,
    )
    variants: tuple[ProviderVariant, ...] = Field(
        min_length=QUALIFICATION_REQUEST_COUNT,
        max_length=QUALIFICATION_REQUEST_COUNT,
    )
    request_count: Literal[7]
    model: Literal["gpt-4.1"]
    model_snapshot: Literal["gpt-4.1-2025-04-14"]
    maximum_output_tokens_per_request: Literal[2048]
    maximum_provider_attempts_per_request: Literal[2]
    minimum_provider_interval_ms: Literal[1000]
    retry_initial_backoff_ms: Literal[5000]
    retry_backoff_multiplier: Literal[2]
    retry_backoff_ceiling_ms: Literal[60000]
    retry_after_ceiling_ms: Literal[60000]
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
    transport_sha256: Sha256
    synthetic_only: Literal[True] = True
    admitted_to_corpus: Literal[False] = False
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
    def _census_accounting_and_identity_reconcile(self) -> Self:
        expected_input = 2 * (
            self.exact_message_input_token_count
            + self.request_count * self.response_format_token_allowance_per_request
        )
        expected_output = (
            self.request_count
            * self.maximum_output_tokens_per_request
            * self.maximum_provider_attempts_per_request
        )
        expected_cost = round(
            expected_input * self.input_usd_per_million_tokens / 1_000_000
            + expected_output * self.output_usd_per_million_tokens / 1_000_000,
            6,
        )
        if (
            self.variants != PROVIDER_VARIANTS
            or len(set(self.qualification_request_sha256s)) != self.request_count
            or self.request_census_sha256
            != canonical_execution_sha256(self.qualification_request_sha256s)
            or self.input_usd_per_million_tokens != INPUT_USD_PER_MILLION
            or self.output_usd_per_million_tokens != OUTPUT_USD_PER_MILLION
            or self.conservative_input_token_ceiling != expected_input
            or self.output_token_ceiling != expected_output
            or self.estimated_upper_cost_usd != expected_cost
            or self.plan_sha256 != canonical_execution_sha256(self.identity_payload())
        ):
            raise ValueError("qualification plan census, accounting or identity differs")
        return self


class V2QualificationRehearsal(_StrictFrozenModel):
    schema_version: Literal["claim-support-validation-v2-qualification-rehearsal/v1"] = (
        REHEARSAL_SCHEMA_VERSION
    )
    status: Literal["claim_support_validation_v2_qualification_rehearsal_passed"]
    plan_sha256: Sha256
    amendment_sha256: Sha256
    request_census_sha256: Sha256
    request_count: Literal[7]
    variants: tuple[ProviderVariant, ...]
    all_requests_synthetic_and_excluded: Literal[True]
    all_response_schemas_validated: Literal[True]
    exact_first_witness_accepted: Literal[True]
    changed_witness_rejected: Literal[True]
    abstention_rejected_for_qualification: Literal[True]
    unknown_evidence_rejected: Literal[True]
    provider_calls_executed: Literal[False] = False
    claims_materialized: Literal[False] = False
    automatic_labels_generated: Literal[False] = False
    blind_packets_generated: Literal[False] = False
    human_annotations_collected: Literal[False] = False
    main_or_sealed_outcomes_opened: Literal[False] = False
    rehearsal_sha256: Sha256

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"rehearsal_sha256"})

    @model_validator(mode="after")
    def _identity_reconciles(self) -> Self:
        if (
            self.variants != PROVIDER_VARIANTS
            or self.rehearsal_sha256 != canonical_execution_sha256(self.identity_payload())
        ):
            raise ValueError("qualification rehearsal identity differs")
        return self


class V2QualificationAuthorization(_StrictFrozenModel):
    schema_version: Literal["claim-support-validation-v2-qualification-authorization/v1"] = (
        AUTHORIZATION_SCHEMA_VERSION
    )
    authorization_ref: str = Field(pattern=r"^ev-[0-9a-f]{64}$")
    authorized_at: str
    source_commit_ref: str = Field(pattern=r"^[0-9a-f]{40}$")
    plan_sha256: Sha256
    rehearsal_sha256: Sha256
    amendment_sha256: Sha256
    expressiveness_review_sha256: Sha256
    request_census_sha256: Sha256
    request_count: Literal[7]
    estimated_upper_cost_usd: float = Field(gt=0)
    operator_cost_ceiling_usd: float = Field(gt=0)
    destination_sha256: Sha256
    registered_attempts: Literal[1] = 1
    synthetic_only: Literal[True] = True
    admitted_to_corpus: Literal[False] = False
    credential_stored: Literal[False] = False
    claims_materialized: Literal[False] = False
    automatic_labels_generated: Literal[False] = False
    blind_packets_generated: Literal[False] = False
    human_annotations_collected: Literal[False] = False
    main_or_sealed_outcomes_opened: Literal[False] = False
    authorization_sha256: Sha256

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"authorization_ref", "authorization_sha256"})

    @model_validator(mode="after")
    def _identity_and_time_reconcile(self) -> Self:
        try:
            parsed = datetime.fromisoformat(self.authorized_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("qualification authorization timestamp is invalid") from exc
        digest = canonical_execution_sha256(self.identity_payload())
        if (
            not self.authorized_at.endswith("Z")
            or parsed.utcoffset() is None
            or self.operator_cost_ceiling_usd < self.estimated_upper_cost_usd
            or self.authorization_sha256 != digest
            or self.authorization_ref != f"ev-{digest}"
        ):
            raise ValueError("qualification authorization does not reconcile")
        return self


class V2QualificationPreflight(_StrictFrozenModel):
    schema_version: Literal["claim-support-validation-v2-qualification-preflight/v1"] = (
        PREFLIGHT_SCHEMA_VERSION
    )
    status: Literal[
        "claim_support_validation_v2_qualification_live_blocked",
        "claim_support_validation_v2_qualification_live_ready",
    ]
    plan_sha256: Sha256
    rehearsal_sha256: Sha256
    source_commit_ref: str = Field(pattern=r"^[0-9a-f]{40}$")
    clean_synchronized_main: bool
    credential_present: bool
    request_count: Literal[7]
    exact_message_input_token_count: int = Field(gt=0)
    estimated_upper_cost_usd: float = Field(gt=0)
    operator_cost_ceiling_usd: float | None = Field(default=None, gt=0)
    live_blockers: tuple[
        Literal[
            "authorization_pending",
            "credential_missing",
            "repository_not_clean_synchronized_main",
        ],
        ...,
    ]
    provider_calls_executed: Literal[False] = False
    claims_materialized: Literal[False] = False
    automatic_labels_generated: Literal[False] = False
    blind_packets_generated: Literal[False] = False
    human_annotations_collected: Literal[False] = False
    main_or_sealed_outcomes_opened: Literal[False] = False
    preflight_sha256: Sha256

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"preflight_sha256"})

    @model_validator(mode="after")
    def _status_and_identity_reconcile(self) -> Self:
        ready = self.status == "claim_support_validation_v2_qualification_live_ready"
        if (
            ready == bool(self.live_blockers)
            or tuple(sorted(set(self.live_blockers))) != self.live_blockers
            or self.preflight_sha256 != canonical_execution_sha256(self.identity_payload())
        ):
            raise ValueError("qualification preflight status or identity differs")
        return self


class V2QualificationLease(_StrictFrozenModel):
    schema_version: Literal["claim-support-validation-v2-qualification-lease/v1"] = (
        LEASE_SCHEMA_VERSION
    )
    authorization_sha256: Sha256
    plan_sha256: Sha256
    destination_sha256: Sha256
    registered_attempts: Literal[1] = 1
    lease_sha256: Sha256

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"lease_sha256"})

    @model_validator(mode="after")
    def _identity_reconciles(self) -> Self:
        if self.lease_sha256 != canonical_execution_sha256(self.identity_payload()):
            raise ValueError("qualification lease identity differs")
        return self


class V2QualificationOutcome(_StrictFrozenModel):
    variant: ProviderVariant
    qualification_request_sha256: Sha256
    gateway_request_identity_sha256: Sha256
    gateway_status: GatewayStatus
    attempt_count: int = Field(ge=1, le=2)
    first_witness_accepted: bool
    issue_sha256: Sha256 | None
    outcome_sha256: Sha256

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"outcome_sha256"})

    @model_validator(mode="after")
    def _status_and_identity_reconcile(self) -> Self:
        if (
            (self.first_witness_accepted and self.gateway_status != "parsed")
            or (not self.first_witness_accepted and self.issue_sha256 is None)
            or (self.first_witness_accepted and self.issue_sha256 is not None)
        ):
            raise ValueError("qualification outcome status and issue identity differ")
        if self.outcome_sha256 != canonical_execution_sha256(self.identity_payload()):
            raise ValueError("qualification outcome identity differs")
        return self


class V2QualificationReceipt(_StrictFrozenModel):
    schema_version: Literal["claim-support-validation-v2-qualification-receipt/v1"] = (
        RECEIPT_SCHEMA_VERSION
    )
    status: Literal[
        "claim_support_validation_v2_qualification_passed",
        "claim_support_validation_v2_qualification_failed",
    ]
    authorization_sha256: Sha256
    plan_sha256: Sha256
    rehearsal_sha256: Sha256
    amendment_sha256: Sha256
    expressiveness_review_sha256: Sha256
    source_commit_ref: str = Field(pattern=r"^[0-9a-f]{40}$")
    terminal_store_sha256: Sha256
    terminal_request_count: Literal[7]
    parsed_count: int = Field(ge=0, le=7)
    first_witness_accepted_count: int = Field(ge=0, le=7)
    technical_failure_count: int = Field(ge=0, le=7)
    semantic_validation_failure_count: int = Field(ge=0, le=7)
    provider_attempt_count: int = Field(ge=7, le=14)
    gateway_status_counts: dict[str, int]
    outcomes: tuple[V2QualificationOutcome, ...] = Field(min_length=7, max_length=7)
    synthetic_only: Literal[True] = True
    admitted_to_corpus: Literal[False] = False
    provider_calls_executed: Literal[True]
    rerun_forbidden: Literal[True]
    full_cohort_authorization_unlocked: bool
    claims_materialized: Literal[False] = False
    automatic_labels_generated: Literal[False] = False
    blind_packets_generated: Literal[False] = False
    human_annotations_collected: Literal[False] = False
    main_or_sealed_outcomes_opened: Literal[False] = False
    receipt_sha256: Sha256

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"receipt_sha256"})

    @model_validator(mode="after")
    def _counts_status_and_identity_reconcile(self) -> Self:
        expected_status_counts = dict(
            sorted(Counter(item.gateway_status for item in self.outcomes).items())
        )
        expected_parsed = sum(item.gateway_status == "parsed" for item in self.outcomes)
        expected_accepted = sum(item.first_witness_accepted for item in self.outcomes)
        passed = (
            self.parsed_count == self.terminal_request_count
            and self.first_witness_accepted_count == self.terminal_request_count
            and self.technical_failure_count == 0
            and self.semantic_validation_failure_count == 0
        )
        if (
            tuple(item.variant for item in self.outcomes) != PROVIDER_VARIANTS
            or len({item.gateway_request_identity_sha256 for item in self.outcomes}) != 7
            or len({item.qualification_request_sha256 for item in self.outcomes}) != 7
            or self.parsed_count != expected_parsed
            or self.first_witness_accepted_count != expected_accepted
            or self.parsed_count + self.technical_failure_count != self.terminal_request_count
            or self.first_witness_accepted_count + self.semantic_validation_failure_count
            != self.parsed_count
            or self.gateway_status_counts != expected_status_counts
            or self.provider_attempt_count != sum(item.attempt_count for item in self.outcomes)
            or passed != (self.status == "claim_support_validation_v2_qualification_passed")
            or self.full_cohort_authorization_unlocked != passed
            or self.receipt_sha256 != canonical_execution_sha256(self.identity_payload())
        ):
            raise ValueError("qualification receipt counts, status or identity differs")
        return self


__all__ = [
    "AUTHORIZATION_SCHEMA_VERSION",
    "INPUT_USD_PER_MILLION",
    "LEASE_SCHEMA_VERSION",
    "OUTPUT_USD_PER_MILLION",
    "PLAN_SCHEMA_VERSION",
    "PREFLIGHT_SCHEMA_VERSION",
    "PROVIDER_VARIANTS",
    "QUALIFICATION_REQUEST_COUNT",
    "RECEIPT_SCHEMA_VERSION",
    "REHEARSAL_SCHEMA_VERSION",
    "RESPONSE_FORMAT_TOKEN_ALLOWANCE",
    "ClaimValidationV2QualificationError",
    "V2QualificationAuthorization",
    "V2QualificationExecutionPlan",
    "V2QualificationLease",
    "V2QualificationOutcome",
    "V2QualificationPreflight",
    "V2QualificationReceipt",
    "V2QualificationRehearsal",
]
