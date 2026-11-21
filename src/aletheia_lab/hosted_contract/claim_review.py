"""Scoped claim review requests and immutable local check receipts."""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from aletheia_lab.project.identity import PROJECT_ID_PATTERN

from .imports import Sha256
from .job_page import JobOutput
from .project_operations import ResultId
from .session_capabilities import Identifier, Version

AssertedVerdict = Literal["allowed", "denied"]
ClaimReviewDecision = Literal["approve_in_scope", "revise", "unknown", "unsupported", "tool_error"]
ClaimReviewPolicy = Literal["historical-state-authorization/v1"]


class _ClaimReviewModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)


class ClaimReviewRequest(_ClaimReviewModel):
    schema_version: Version
    asserted_verdict: AssertedVerdict


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


class ClaimReviewJobOutput(JobOutput[ClaimReviewReceipt]):
    model_config = ConfigDict(hide_input_in_errors=True)

    operation: Literal["review_claim"]

    @model_validator(mode="after")
    def _review_scope(self) -> Self:
        if self.result.job_id != self.job_id or self.result.binding.workspace_id != self.workspace_id:
            raise ValueError("Claim review job output scope mismatch.")
        return self
