"""Scoped claim review requests and immutable local check receipts."""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from aletheia_lab.project.identity import PROJECT_ID_PATTERN

from .imports import Sha256
from .job_page import JobOutput
from .project_operations import ResultId
from .session_capabilities import Identifier, Version

AssertedVerdict = Literal["allowed", "denied"]
ClaimReviewMode = Literal["local_check", "provider"]
ClaimReviewDecision = Literal["approve_in_scope", "revise", "unknown", "unsupported", "tool_error"]
ClaimReviewPolicy = Literal["historical-state-authorization/v1"]


class _ClaimReviewModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)


class ClaimReviewRequest(_ClaimReviewModel):
    schema_version: Version
    asserted_verdict: AssertedVerdict
    mode: ClaimReviewMode = "local_check"


class ClaimReviewFinding(_ClaimReviewModel):
    decision: ClaimReviewDecision
    reason: str = Field(min_length=1, max_length=500)
    evidence_ids: tuple[Identifier, ...] = Field(max_length=128)
    observed_state_sha256: Sha256 | None = None
    receipt_sha256: Sha256 | None = None
    contract_sha256: Sha256 | None = None
    execution_mode: Literal["fresh", "replay"] | None = None


class ClaimReviewBinding(_ClaimReviewModel):
    workspace_id: Identifier
    project_id: Annotated[str, Field(pattern=PROJECT_ID_PATTERN)]
    source_id: Identifier | None
    source_sha256: Sha256 | None
    snapshot_id: Annotated[str, Field(pattern=r"^(?:p3-snapshot|upload-snapshot)-[0-9a-f]{64}$")]
    result_id: ResultId
    claim_id: Identifier
    result_sha256: Sha256
    claim_sha256: Sha256
    evidence_sha256: Sha256
    receipt_sha256: Sha256 | None
    contract_sha256: Sha256 | None
    asserted_verdict: AssertedVerdict
    policy: ClaimReviewPolicy = "historical-state-authorization/v1"
    candidate_sha256: Sha256

    @model_validator(mode="after")
    def _source_binding(self) -> Self:
        if (self.source_id is None) != (self.source_sha256 is None):
            raise ValueError("Claim review source binding requires both its ID and its hash.")
        return self


class ClaimReviewReceipt(_ClaimReviewModel):
    schema_version: Literal["claim-evidence-review/v1"] = "claim-evidence-review/v1"
    job_id: Identifier
    binding: ClaimReviewBinding
    finding: ClaimReviewFinding
    mode: Literal["local_check"] = "local_check"
    usage: Literal["not_incurred"] = "not_incurred"


class ProviderClaimReviewBinding(ClaimReviewBinding):
    provider_plan_sha256: Sha256


CLAIM_REVIEW_BINDING_ADAPTER = TypeAdapter(ProviderClaimReviewBinding | ClaimReviewBinding)


class ClaimReviewProviderResult(_ClaimReviewModel):
    status: Literal["completed", "unavailable", "unresolved"]
    provider_request_id: Identifier | None
    input_tokens: int | None = Field(ge=0)
    output_tokens: int | None = Field(ge=0)
    provider_decision: ClaimReviewDecision | None
    agrees_with_host: bool | None

    @model_validator(mode="after")
    def _check_outcome(self) -> Self:
        if (self.input_tokens is None) != (self.output_tokens is None):
            raise ValueError("Usage counts must be reported together.")
        decision = self.status == "completed"
        if decision != (self.provider_decision is not None) or decision != (self.agrees_with_host is not None):
            raise ValueError("Provider decision does not match its response status.")
        return self


class ClaimReviewProviderAttempt(_ClaimReviewModel):
    attempt_id: Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")]
    request_sha256: Sha256
    model: Identifier
    dispatched_at: AwareDatetime
    deadline_at: AwareDatetime
    received_at: AwareDatetime | None
    held_microusd: int = Field(gt=0)
    outcome: ClaimReviewProviderResult | None

    @model_validator(mode="after")
    def _check_timestamps(self) -> Self:
        if (self.outcome is None) != (self.received_at is None):
            raise ValueError("Provider response time must be paired with its outcome.")
        if self.dispatched_at >= self.deadline_at or (
            self.received_at is not None and not self.dispatched_at <= self.received_at <= self.deadline_at
        ):
            raise ValueError("Provider receipt timestamps do not match the original job deadline.")
        return self


class ProviderClaimReviewReceipt(_ClaimReviewModel):
    schema_version: Literal["claim-evidence-review/v2"] = "claim-evidence-review/v2"
    job_id: Identifier
    binding: ProviderClaimReviewBinding
    finding: ClaimReviewFinding
    mode: Literal["provider"] = "provider"
    usage: Literal["settled", "unsettled"]
    attempt: ClaimReviewProviderAttempt

    @model_validator(mode="after")
    def _check_accounting(self) -> Self:
        outcome = self.attempt.outcome
        if (self.usage == "settled") != (outcome is not None and outcome.input_tokens is not None):
            raise ValueError("Provider receipt usage does not match recorded accounting.")
        if (outcome is not None and outcome.provider_decision is not None
                and outcome.agrees_with_host != (outcome.provider_decision == self.finding.decision)):
            raise ValueError("Provider agreement does not match the checked finding.")
        return self


AnyClaimReviewReceipt = Annotated[
    ClaimReviewReceipt | ProviderClaimReviewReceipt, Field(discriminator="schema_version")
]
CLAIM_REVIEW_RECEIPT_ADAPTER = TypeAdapter(AnyClaimReviewReceipt)


class ClaimReviewJobOutput(JobOutput[AnyClaimReviewReceipt]):
    model_config = ConfigDict(hide_input_in_errors=True)

    operation: Literal["review_claim"]

    @model_validator(mode="after")
    def _review_scope(self) -> Self:
        if self.result.job_id != self.job_id or self.result.binding.workspace_id != self.workspace_id:
            raise ValueError("Claim review job output scope mismatch.")
        return self
