"""Additive bounded attempt history; frozen Job/JobReceipt schemas stay unchanged."""
from typing import Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from .session_capabilities import Identifier, Version


class JobConflict(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)
    project_id: str = Field(pattern=r'^p3-project-[0-9a-f]{64}$')
    current_revision: int = Field(ge=0, le=9223372036854775807)


class JobAttempt(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)
    epoch: int = Field(ge=1, le=3)
    started_at: AwareDatetime | None
    heartbeat_at: AwareDatetime | None
    lease_until: AwareDatetime | None
    finished_at: AwareDatetime | None
    outcome: Literal['running', 'cancel_requested', 'succeeded', 'failed', 'cancelled', 'expired', 'lease_lost', 'retrying']
    failure_kind: Literal['transient', 'permanent', 'authorization', 'attempts_exhausted', 'deadline', 'cancelled', 'conflict'] | None


    @model_validator(mode="after")
    def _times(self) -> Self:
        if self.failure_kind == 'conflict' and self.outcome != 'failed':
            raise ValueError("A revision conflict must be a failed attempt.")
        if (self.outcome in ("running", "cancel_requested")) != (self.finished_at is None):
            raise ValueError("Attempt outcome and terminal time differ.")
        if self.started_at is not None and (
                self.heartbeat_at is not None and self.heartbeat_at < self.started_at
                or self.finished_at is not None and self.finished_at < self.started_at):
            raise ValueError("Attempt time precedes its start.")
        return self


class JobAttemptPage(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)
    schema_version: Version
    workspace_id: Identifier
    job_id: Identifier
    retry_of_job_id: Identifier | None
    items: list[JobAttempt] = Field(max_length=3)
    conflict: JobConflict | None = None


    @model_validator(mode="after")
    def _epochs(self) -> Self:
        epochs = [item.epoch for item in self.items]
        if epochs != sorted(set(epochs)):
            raise ValueError("Attempt epochs must be unique and increasing.")
        if any(item.finished_at is None for item in self.items[:-1]):
            raise ValueError("Only the latest attempt may be active.")
        if (self.conflict is not None) != bool(self.items and self.items[-1].failure_kind == 'conflict'):
            raise ValueError("Conflict metadata requires a terminal conflict attempt.")
        return self
