"""Strict contracts for the one-shot diagnosis main-study pipeline."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from aletheia_lab.diagnosis._main_pipeline_budget import (
    INPUT_USD_PER_MILLION_TOKENS,
    OUTPUT_USD_PER_MILLION_TOKENS,
)
from aletheia_lab.evaluation.execution_contracts import canonical_execution_sha256
from aletheia_lab.project.identity import SHA256_PATTERN

PIPELINE_AUTHORIZATION_SCHEMA_VERSION: Final = "diagnosis-main-pipeline-authorization/v1"
PIPELINE_LEASE_SCHEMA_VERSION: Final = "diagnosis-main-pipeline-lease/v1"
PIPELINE_PREFLIGHT_SCHEMA_VERSION: Final = "diagnosis-main-pipeline-preflight/v1"
PIPELINE_RECEIPT_SCHEMA_VERSION: Final = "diagnosis-main-pipeline-receipt/v1"

Sha256 = Annotated[str, Field(pattern=SHA256_PATTERN)]


class DiagnosisMainPipelineError(ValueError):
    """Raised when the main pipeline cannot preserve its frozen boundary."""


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        revalidate_instances="always",
        allow_inf_nan=False,
    )


class DiagnosisMainPipelinePreflight(_StrictFrozenModel):
    """Sanitized proof that the complete local pipeline was rehearsed offline."""

    schema_version: Literal["diagnosis-main-pipeline-preflight/v1"] = (
        PIPELINE_PREFLIGHT_SCHEMA_VERSION
    )
    status: Literal["offline_end_to_end_preflight_pass"]
    scientific_result_eligible: Literal[False]
    protected_main_outcomes_opened: Literal[False]
    provider_calls_executed: Literal[False]
    registered_main_attempts_consumed: Literal[0]
    registered_relation_attempts_consumed: Literal[0]
    source_commit_ref: str = Field(pattern=r"^[0-9a-f]{40}$")
    private_packet_sha256: Sha256
    analysis_census_sha256: Sha256
    runtime_contract_sha256: Sha256
    response_contract_sha256: Sha256
    scoring_contract_sha256: Sha256
    analysis_plan_sha256: Sha256
    logical_request_count: Literal[1024]
    provider_backed_logical_request_count: Literal[896]
    deterministic_logical_request_count: Literal[128]
    expected_provider_turn_count: Literal[1408]
    completed_provider_turn_count: Literal[1408]
    emitted_claim_count: int = Field(ge=0, le=4480)
    relation_request_count: int = Field(ge=0, le=4480)
    provider_input_fields: tuple[
        Literal["claim_text"], Literal["claim_type"], Literal["visible_evidence"]
    ]
    main_resume_is_terminal_only: Literal[True]
    relation_resume_is_terminal_only: Literal[True]
    incomplete_request_replay_permitted: Literal[False]
    batch_result_sha256: Sha256
    scoring_preparation_sha256: Sha256
    relation_results_sha256: Sha256
    analysis_input_sha256: Sha256
    analysis_report_sha256: Sha256
    preflight_sha256: Sha256

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"preflight_sha256"})

    @model_validator(mode="after")
    def _reconciles(self) -> Self:
        if (
            self.provider_input_fields != ("claim_text", "claim_type", "visible_evidence")
            or self.emitted_claim_count != self.relation_request_count
            or self.preflight_sha256 != canonical_execution_sha256(self.identity_payload())
        ):
            raise ValueError("main pipeline preflight counts or identity changed")
        return self


class DiagnosisMainPipelineAuthorization(_StrictFrozenModel):
    """Explicit operator authority for one private end-to-end registered run."""

    schema_version: Literal["diagnosis-main-pipeline-authorization/v1"] = (
        PIPELINE_AUTHORIZATION_SCHEMA_VERSION
    )
    execution_authorized: Literal[True]
    authorized_at: str
    source_commit_ref: str = Field(pattern=r"^[0-9a-f]{40}$")
    preflight_sha256: Sha256
    private_packet_sha256: Sha256
    analysis_census_sha256: Sha256
    runtime_contract_sha256: Sha256
    response_contract_sha256: Sha256
    scoring_contract_sha256: Sha256
    analysis_plan_sha256: Sha256
    destination_sha256: Sha256
    logical_request_count: Literal[1024]
    maximum_main_provider_turn_count: Literal[1408]
    maximum_relation_request_count: Literal[4480]
    maximum_provider_attempts_per_request: Literal[2]
    provider: Literal["openai"]
    model: Literal["gpt-4.1"]
    model_snapshot: Literal["gpt-4.1-2025-04-14"]
    input_usd_per_million_tokens: float = Field(ge=2.0, le=2.0)
    output_usd_per_million_tokens: float = Field(ge=8.0, le=8.0)
    pricing_source_url: Literal["https://developers.openai.com/api/docs/models/gpt-4.1"]
    pricing_checked_on: Literal["2026-09-22"]
    operator_cost_ceiling_usd: float = Field(gt=0)
    registered_main_attempts: Literal[1]
    registered_relation_attempts: Literal[1]
    raw_artifacts_private: Literal[True]
    credential_stored: Literal[False]
    authorization_ref: str = Field(pattern=r"^ev-[0-9a-f]{64}$")
    authorization_sha256: Sha256

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"authorization_ref", "authorization_sha256"})

    @model_validator(mode="after")
    def _reconciles(self) -> Self:
        try:
            timestamp = datetime.fromisoformat(self.authorized_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("pipeline authorization timestamp is invalid") from exc
        digest = canonical_execution_sha256(self.identity_payload())
        if (
            not self.authorized_at.endswith("Z")
            or timestamp.utcoffset() is None
            or self.input_usd_per_million_tokens != INPUT_USD_PER_MILLION_TOKENS
            or self.output_usd_per_million_tokens != OUTPUT_USD_PER_MILLION_TOKENS
            or self.authorization_sha256 != digest
            or self.authorization_ref != f"ev-{digest}"
        ):
            raise ValueError("pipeline authorization identity changed")
        return self


class DiagnosisMainPipelineLease(_StrictFrozenModel):
    """Create-only marker consuming the single registered main attempt."""

    schema_version: Literal["diagnosis-main-pipeline-lease/v1"] = PIPELINE_LEASE_SCHEMA_VERSION
    authorization_sha256: Sha256
    destination_sha256: Sha256
    registered_main_attempts_consumed: Literal[1]
    registered_relation_attempts_reserved: Literal[1]
    lease_sha256: Sha256

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"lease_sha256"})

    @model_validator(mode="after")
    def _reconciles(self) -> Self:
        if self.lease_sha256 != canonical_execution_sha256(self.identity_payload()):
            raise ValueError("pipeline lease identity changed")
        return self


class DiagnosisMainPipelineReceipt(_StrictFrozenModel):
    """Sanitized terminal summary; private item-level artifacts stay outside git."""

    schema_version: Literal["diagnosis-main-pipeline-receipt/v1"] = PIPELINE_RECEIPT_SCHEMA_VERSION
    status: Literal["registered_main_analysis_complete"]
    authorization_sha256: Sha256
    lease_sha256: Sha256
    source_commit_ref: str = Field(pattern=r"^[0-9a-f]{40}$")
    logical_request_count: Literal[1024]
    relation_request_count: int = Field(ge=0, le=4480)
    main_terminal_status_counts: dict[str, int]
    relation_terminal_status_counts: dict[str, int]
    registered_main_attempts_consumed: Literal[1]
    registered_relation_attempts_consumed: int = Field(ge=0, le=1)
    provider_calls_executed: Literal[True]
    operator_cost_ceiling_usd: float = Field(gt=0)
    provider_cost_committed_usd: float = Field(ge=0)
    provider_budget_exhausted: Literal[False]
    raw_artifacts_private: Literal[True]
    private_paths_embedded: Literal[False]
    batch_result_sha256: Sha256
    scoring_preparation_sha256: Sha256
    relation_results_sha256: Sha256
    analysis_input_sha256: Sha256
    analysis_report_sha256: Sha256
    receipt_sha256: Sha256

    def identity_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"receipt_sha256"})

    @model_validator(mode="after")
    def _reconciles(self) -> Self:
        if (
            sum(self.main_terminal_status_counts.values()) != self.logical_request_count
            or sum(self.relation_terminal_status_counts.values()) != self.relation_request_count
            or self.registered_relation_attempts_consumed != int(self.relation_request_count > 0)
            or any(value < 0 for value in self.main_terminal_status_counts.values())
            or any(value < 0 for value in self.relation_terminal_status_counts.values())
            or self.provider_cost_committed_usd > self.operator_cost_ceiling_usd
            or self.receipt_sha256 != canonical_execution_sha256(self.identity_payload())
        ):
            raise ValueError("pipeline receipt counts or identity changed")
        return self


__all__ = [
    "PIPELINE_AUTHORIZATION_SCHEMA_VERSION",
    "PIPELINE_LEASE_SCHEMA_VERSION",
    "PIPELINE_PREFLIGHT_SCHEMA_VERSION",
    "PIPELINE_RECEIPT_SCHEMA_VERSION",
    "DiagnosisMainPipelineAuthorization",
    "DiagnosisMainPipelineError",
    "DiagnosisMainPipelineLease",
    "DiagnosisMainPipelinePreflight",
    "DiagnosisMainPipelineReceipt",
]
