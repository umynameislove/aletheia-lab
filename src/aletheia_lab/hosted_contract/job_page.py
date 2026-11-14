"""Producer models shared by hosted job and paging boundaries."""

from __future__ import annotations

from typing import Generic, Literal, Self, TypeVar

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from .session_capabilities import Identifier, Version

Item = TypeVar("Item")
JobOperation = Literal["preview", "confirm", "analyze", "follow_up", "refresh"]
JobState = Literal["queued", "running", "cancel_requested", "succeeded", "failed", "cancelled", "expired"]


class DraftModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class JobReceipt(DraftModel):
    schema_version: Version
    workspace_id: Identifier
    job_id: Identifier
    status: Literal["queued"]
    request_id: Identifier
    status_url: str = Field(
        min_length=1,
        max_length=1024,
        pattern=r"^/v1/workspaces/[A-Za-z0-9][A-Za-z0-9._:-]{0,127}/jobs/[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$",
    )

    @model_validator(mode="after")
    def _bound_status_url(self) -> Self:
        expected = f"/v1/workspaces/{self.workspace_id}/jobs/{self.job_id}"
        if self.status_url != expected:
            raise ValueError("status_url must reference the receipt's workspace and job")
        return self


class Job(DraftModel):
    schema_version: Version
    workspace_id: Identifier
    job_id: Identifier
    status: JobState
    progress: float | None = Field(ge=0, le=1, allow_inf_nan=False)
    # Legacy records did not persist every metadata field: None means unknown.
    operation: JobOperation | None = None
    created_at: AwareDatetime | None = None
    deadline_at: AwareDatetime | None = None
    terminal_at: AwareDatetime | None = None
    attempt: int | None = Field(default=None, ge=1)
    max_attempts: int | None = Field(default=None, ge=1)
    retryable: bool | None = None
    output_url: str | None = Field(default=None, min_length=1, max_length=1024,
        pattern=r"^/v1/workspaces/[A-Za-z0-9][A-Za-z0-9._:-]{0,127}/jobs/[A-Za-z0-9][A-Za-z0-9._:-]{0,127}/output$")

    @model_validator(mode="after")
    def _metadata(self) -> Self:
        if self.created_at is not None:
            if self.deadline_at is not None and self.deadline_at < self.created_at:
                raise ValueError("job deadline precedes creation")
            if self.terminal_at is not None and self.terminal_at < self.created_at:
                raise ValueError("job terminal time precedes creation")
        if self.terminal_at is not None and self.status not in ("succeeded", "failed", "cancelled", "expired"):
            raise ValueError("nonterminal job has a terminal time")
        if self.attempt is not None and self.max_attempts is not None and self.attempt > self.max_attempts:
            raise ValueError("job attempt exceeds the recorded maximum")
        if self.output_url is not None and self.output_url != f"/v1/workspaces/{self.workspace_id}/jobs/{self.job_id}/output":
            raise ValueError("job output URL scope mismatch")
        return self


class JobOutput(DraftModel, Generic[Item]):
    """Common envelope; the operation adapter validates its concrete result."""

    schema_version: Version
    workspace_id: Identifier
    job_id: Identifier
    operation: JobOperation
    result: Item


class Page(DraftModel, Generic[Item]):
    model_config = ConfigDict(json_schema_extra={
        "allOf": [{
            "if": {"properties": {"total_kind": {"const": "unknown"}}, "required": ["total_kind"]},
            "then": {"properties": {"total": {"type": "null"}}},
            "else": {"properties": {"total": {"type": "integer", "minimum": 0}}},
        }]
    })

    schema_version: Version
    workspace_id: Identifier
    items: list[Item] = Field(max_length=200)
    next_cursor: str | None = Field(min_length=1, max_length=4096)
    total: int | None = Field(ge=0)
    total_kind: Literal["exact", "estimated", "unknown"]

    @model_validator(mode="after")
    def _total_kind_consistent(self) -> Self:
        if (self.total is None) != (self.total_kind == "unknown"):
            raise ValueError("unknown total must be null; known totals must be provided")
        if self.next_cursor == "":
            raise ValueError("empty cursor is not valid")
        return self
