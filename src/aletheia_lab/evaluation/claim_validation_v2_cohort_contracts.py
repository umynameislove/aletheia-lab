"""Immutable contracts for the prospective V2 diagnosis-cohort authority."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from aletheia_lab.evaluation.claim_corpus_contracts import EligibleVariant
from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256
from aletheia_lab.project.identity import SHA256_PATTERN

PLAN_SCHEMA_VERSION: Final = "claim-support-validation-v2-cohort-plan/v1"
REHEARSAL_SCHEMA_VERSION: Final = "claim-support-validation-v2-cohort-rehearsal/v1"
AUTHORIZATION_SCHEMA_VERSION: Final = (
    "claim-support-validation-v2-cohort-authorization/v1"
)
PREFLIGHT_SCHEMA_VERSION: Final = "claim-support-validation-v2-cohort-preflight/v1"
DIAGNOSIS_REQUEST_COUNT: Final = 360
MODEL_REQUEST_COUNT: Final = 315
DETERMINISTIC_REQUEST_COUNT: Final = 45
INPUT_USD_PER_MILLION: Final = 2.0
OUTPUT_USD_PER_MILLION: Final = 8.0
RESPONSE_FORMAT_OVERHEAD_ALLOWANCE: Final = 1024
MAXIMUM_OUTPUT_TOKENS: Final = 2048
MAXIMUM_PROVIDER_ATTEMPTS: Final = 2

Sha256 = Annotated[str, Field(pattern=SHA256_PATTERN)]
ExecutionRoute = Literal["model_gateway", "deterministic_local"]
LiveBlocker = Literal[
    "authorization_pending",
    "credential_missing",
    "qualification_not_verified",
    "repository_not_clean_synchronized_main",
]


class ClaimValidationV2CohortError(ValueError):
    """Raised when the cohort authority would diverge from frozen inputs."""


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        revalidate_instances="always",
        allow_inf_nan=False,
    )


class V2CohortRequestProjection(_StrictFrozenModel):
    """Provider-independent identity and local token accounting for one cell."""

    sequence: int = Field(ge=1, le=DIAGNOSIS_REQUEST_COUNT)
    schedule_round: int = Field(ge=1, le=15)
    v2_request_sha256: Sha256
    source_request_sha256: Sha256
    variant: EligibleVariant
    execution_route: ExecutionRoute
    visible_context_sha256: Sha256
    prompt_sha256: Sha256 | None
    response_schema_sha256: Sha256 | None
    exact_message_input_token_count: int = Field(ge=0)
    exact_response_schema_token_count: int = Field(ge=0)
    maximum_output_tokens: int = Field(ge=0, le=MAXIMUM_OUTPUT_TOKENS)
    maximum_attempts: int = Field(ge=1, le=MAXIMUM_PROVIDER_ATTEMPTS)
    projection_sha256: Sha256

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"projection_sha256"})

    @model_validator(mode="after")
    def _route_and_identity_reconcile(self) -> Self:
        local = self.execution_route == "deterministic_local"
        if (
            local != (self.variant == "B0")
            or local != (self.prompt_sha256 is None)
            or local != (self.response_schema_sha256 is None)
            or (local and self.exact_message_input_token_count != 0)
            or (local and self.exact_response_schema_token_count != 0)
            or (local and self.maximum_output_tokens != 0)
            or (local and self.maximum_attempts != 1)
            or (not local and self.exact_message_input_token_count <= 0)
            or (not local and self.exact_response_schema_token_count <= 0)
            or (not local and self.maximum_output_tokens != MAXIMUM_OUTPUT_TOKENS)
            or (not local and self.maximum_attempts != MAXIMUM_PROVIDER_ATTEMPTS)
            or self.projection_sha256
            != canonical_execution_sha256(self.identity_payload())
        ):
            raise ValueError("V2 cohort request projection does not reconcile")
        return self


class V2CohortExecutionPlan(_StrictFrozenModel):
    schema_version: Literal["claim-support-validation-v2-cohort-plan/v1"] = (
        PLAN_SCHEMA_VERSION
    )
    source_commit_ref: str = Field(pattern=r"^[0-9a-f]{40}$")
    protocol_sha256: Sha256
    runtime_manifest_sha256: Sha256
    runtime_readiness_sha256: Sha256
    amendment_sha256: Sha256
    expressiveness_review_sha256: Sha256
    qualification_authorization_sha256: Sha256
    qualification_receipt_sha256: Sha256
    qualification_terminal_store_sha256: Sha256
    qualification_source_commit_ref: str = Field(pattern=r"^[0-9a-f]{40}$")
    qualification_request_count: Literal[7]
    qualification_parsed_count: Literal[7]
    qualification_first_witness_accepted_count: Literal[7]
    diagnosis_request_count: Literal[360]
    model_request_count: Literal[315]
    deterministic_request_count: Literal[45]
    request_census_sha256: Sha256
    request_projection_census_sha256: Sha256
    request_projection_sha256s: tuple[Sha256, ...] = Field(
        min_length=DIAGNOSIS_REQUEST_COUNT,
        max_length=DIAGNOSIS_REQUEST_COUNT,
    )
    model: Literal["gpt-4.1"]
    model_snapshot: Literal["gpt-4.1-2025-04-14"]
    maximum_output_tokens_per_model_request: Literal[2048]
    maximum_provider_attempts_per_request: Literal[2]
    minimum_provider_interval_ms: Literal[1000]
    retry_initial_backoff_ms: Literal[5000]
    retry_backoff_multiplier: Literal[2]
    retry_backoff_ceiling_ms: Literal[60000]
    retry_after_ceiling_ms: Literal[60000]
    tokenizer_name: Literal["tiktoken"]
    tokenizer_version: Literal["0.14.0"]
    tokenizer_encoding: Literal["o200k_base"]
    exact_message_input_token_count: int = Field(gt=0)
    exact_response_schema_token_count: int = Field(gt=0)
    provider_overhead_allowance_tokens_per_call: Literal[1024]
    conservative_input_token_ceiling: int = Field(gt=0)
    output_token_ceiling: int = Field(gt=0)
    provider_billed_input_tokens_known: Literal[False] = False
    input_usd_per_million_tokens: float = Field(gt=0)
    output_usd_per_million_tokens: float = Field(gt=0)
    estimated_upper_cost_usd: float = Field(gt=0)
    relation_execution_authorized: Literal[False] = False
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
    def _census_cost_and_identity_reconcile(self) -> Self:
        expected_input = MAXIMUM_PROVIDER_ATTEMPTS * (
            self.exact_message_input_token_count
            + self.exact_response_schema_token_count
            + self.model_request_count
            * self.provider_overhead_allowance_tokens_per_call
        )
        expected_output = (
            self.model_request_count
            * self.maximum_output_tokens_per_model_request
            * self.maximum_provider_attempts_per_request
        )
        expected_cost = round(
            expected_input * self.input_usd_per_million_tokens / 1_000_000
            + expected_output * self.output_usd_per_million_tokens / 1_000_000,
            6,
        )
        if (
            self.model_request_count + self.deterministic_request_count
            != self.diagnosis_request_count
            or len(set(self.request_projection_sha256s))
            != self.diagnosis_request_count
            or self.request_projection_census_sha256
            != canonical_execution_sha256(self.request_projection_sha256s)
            or self.input_usd_per_million_tokens != INPUT_USD_PER_MILLION
            or self.output_usd_per_million_tokens != OUTPUT_USD_PER_MILLION
            or self.conservative_input_token_ceiling != expected_input
            or self.output_token_ceiling != expected_output
            or self.estimated_upper_cost_usd != expected_cost
            or self.plan_sha256
            != canonical_execution_sha256(self.identity_payload())
        ):
            raise ValueError("V2 cohort plan census, cost or identity differs")
        return self


class V2CohortRehearsal(_StrictFrozenModel):
    schema_version: Literal["claim-support-validation-v2-cohort-rehearsal/v1"] = (
        REHEARSAL_SCHEMA_VERSION
    )
    status: Literal["claim_support_validation_v2_cohort_rehearsal_passed"]
    plan_sha256: Sha256
    qualification_receipt_sha256: Sha256
    request_census_sha256: Sha256
    request_projection_census_sha256: Sha256
    diagnosis_request_count: Literal[360]
    model_request_count: Literal[315]
    deterministic_request_count: Literal[45]
    balanced_round_count: Literal[15]
    exact_request_projections_rebuilt: Literal[True]
    qualification_pass_receipt_bound: Literal[True]
    all_model_requests_share_budget: Literal[True]
    all_model_prompts_bind_amendment: Literal[True]
    all_response_schemas_bind_visible_evidence: Literal[True]
    provider_calls_executed: Literal[False] = False
    authorization_created: Literal[False] = False
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
        if self.rehearsal_sha256 != canonical_execution_sha256(self.identity_payload()):
            raise ValueError("V2 cohort rehearsal identity differs")
        return self


class V2CohortAuthorization(_StrictFrozenModel):
    schema_version: Literal[
        "claim-support-validation-v2-cohort-authorization/v1"
    ] = AUTHORIZATION_SCHEMA_VERSION
    authorization_ref: str = Field(pattern=r"^ev-[0-9a-f]{64}$")
    authorized_at: str
    source_commit_ref: str = Field(pattern=r"^[0-9a-f]{40}$")
    plan_sha256: Sha256
    rehearsal_sha256: Sha256
    qualification_receipt_sha256: Sha256
    request_census_sha256: Sha256
    request_projection_census_sha256: Sha256
    diagnosis_request_count: Literal[360]
    model_request_count: Literal[315]
    deterministic_request_count: Literal[45]
    estimated_upper_cost_usd: float = Field(gt=0)
    operator_cost_ceiling_usd: float = Field(gt=0)
    destination_sha256: Sha256
    registered_attempts: Literal[1] = 1
    execution_phase: Literal["v2_diagnosis_cohort"]
    relation_execution_authorized: Literal[False] = False
    credential_stored: Literal[False] = False
    provider_calls_executed: Literal[False] = False
    claims_materialized: Literal[False] = False
    automatic_labels_generated: Literal[False] = False
    blind_packets_generated: Literal[False] = False
    human_annotations_collected: Literal[False] = False
    main_or_sealed_outcomes_opened: Literal[False] = False
    authorization_sha256: Sha256

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(
            mode="json", exclude={"authorization_ref", "authorization_sha256"}
        )

    @model_validator(mode="after")
    def _time_cost_and_identity_reconcile(self) -> Self:
        try:
            parsed = datetime.fromisoformat(self.authorized_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("V2 cohort authorization timestamp is invalid") from exc
        digest = canonical_execution_sha256(self.identity_payload())
        if (
            not self.authorized_at.endswith("Z")
            or parsed.utcoffset() is None
            or self.operator_cost_ceiling_usd < self.estimated_upper_cost_usd
            or self.authorization_sha256 != digest
            or self.authorization_ref != f"ev-{digest}"
        ):
            raise ValueError("V2 cohort authorization does not reconcile")
        return self


class V2CohortPreflight(_StrictFrozenModel):
    schema_version: Literal["claim-support-validation-v2-cohort-preflight/v1"] = (
        PREFLIGHT_SCHEMA_VERSION
    )
    status: Literal[
        "claim_support_validation_v2_cohort_live_blocked",
        "claim_support_validation_v2_cohort_live_ready",
    ]
    plan_sha256: Sha256
    rehearsal_sha256: Sha256
    qualification_receipt_sha256: Sha256
    source_commit_ref: str = Field(pattern=r"^[0-9a-f]{40}$")
    clean_synchronized_main: bool
    credential_present: bool
    diagnosis_request_count: Literal[360]
    model_request_count: Literal[315]
    deterministic_request_count: Literal[45]
    exact_message_input_token_count: int = Field(gt=0)
    exact_response_schema_token_count: int = Field(gt=0)
    estimated_upper_cost_usd: float = Field(gt=0)
    operator_cost_ceiling_usd: float | None = Field(default=None, gt=0)
    live_blockers: tuple[LiveBlocker, ...]
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
        ready = self.status == "claim_support_validation_v2_cohort_live_ready"
        if (
            ready == bool(self.live_blockers)
            or tuple(sorted(set(self.live_blockers))) != self.live_blockers
            or self.preflight_sha256
            != canonical_execution_sha256(self.identity_payload())
        ):
            raise ValueError("V2 cohort preflight status or identity differs")
        return self


__all__ = [
    "AUTHORIZATION_SCHEMA_VERSION",
    "ClaimValidationV2CohortError",
    "DETERMINISTIC_REQUEST_COUNT",
    "DIAGNOSIS_REQUEST_COUNT",
    "INPUT_USD_PER_MILLION",
    "MAXIMUM_OUTPUT_TOKENS",
    "MAXIMUM_PROVIDER_ATTEMPTS",
    "MODEL_REQUEST_COUNT",
    "OUTPUT_USD_PER_MILLION",
    "PLAN_SCHEMA_VERSION",
    "PREFLIGHT_SCHEMA_VERSION",
    "REHEARSAL_SCHEMA_VERSION",
    "RESPONSE_FORMAT_OVERHEAD_ALLOWANCE",
    "V2CohortAuthorization",
    "V2CohortExecutionPlan",
    "V2CohortPreflight",
    "V2CohortRehearsal",
    "V2CohortRequestProjection",
]
