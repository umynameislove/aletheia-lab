"""Operator-approved scope and one-send budget for hosted claim review."""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from aletheia_lab.hosted_contract.claim_review import ClaimReviewBinding
from aletheia_lab.hosted_contract.imports import Sha256
from aletheia_lab.hosted_contract.session_capabilities import Identifier
from aletheia_lab.project.identity import canonical_project_sha256

from .content_hashing import content_hasher
from .request_boundary import decode_json_model


class ClaimProviderPlan(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)

    schema_version: Literal["claim-provider-plan/v1"]
    workspace_id: Identifier
    actor_id: Identifier
    candidate_sha256: Sha256
    request_sha256: Sha256
    model: Identifier
    endpoint: Literal["https://api.openai.com/v1/responses"]
    max_calls: Literal[1]
    maximum_output_tokens: Literal[128]
    budget_microusd: int = Field(gt=0)
    expires_at: AwareDatetime

    @property
    def sha256(self) -> str:
        return canonical_project_sha256(self.model_dump(mode="json"))

    def available(self, workspace: str) -> bool:
        return self.workspace_id == workspace and self.expires_at > datetime.now(UTC)

    def matches(self, binding: ClaimReviewBinding, actor: str, request_sha256: str) -> bool:
        from .claim_review_provider import (
            CLAIM_PROVIDER_ENDPOINT,
            CLAIM_PROVIDER_HOLD_MICROUSD,
            CLAIM_PROVIDER_MODEL,
        )

        return self.available(binding.workspace_id) and (
            self.actor_id, self.candidate_sha256, self.request_sha256, self.model, self.endpoint
        ) == (
            actor, binding.candidate_sha256, request_sha256, CLAIM_PROVIDER_MODEL, CLAIM_PROVIDER_ENDPOINT
        ) and self.budget_microusd >= CLAIM_PROVIDER_HOLD_MICROUSD


def load_claim_provider_plan(manifest_path: str, expected_sha256: str) -> ClaimProviderPlan:
    path = Path(manifest_path)
    if not path.is_absolute() or not path.is_file():
        raise ValueError("A pinned operator plan file is required.")
    with path.open("rb") as stream:
        raw = stream.read(8193)
    if len(raw) > 8192 or content_hasher(raw).hexdigest() != expected_sha256:
        raise ValueError("Claim provider plan does not match the configured digest.")
    return decode_json_model(raw, ClaimProviderPlan, maximum_bytes=8192)
