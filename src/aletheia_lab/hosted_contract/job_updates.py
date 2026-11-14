"""Coalesced current job snapshots, with explicit resync rather than event replay."""
from __future__ import annotations

from typing import Literal, Self

from pydantic import Field, model_validator

from .job_page import DraftModel, Job
from .session_capabilities import Identifier, Version


class JobUpdate(DraftModel):
    schema_version: Version
    workspace_id: Identifier
    job_id: Identifier
    revision: int = Field(ge=1, le=9223372036854775807)
    disposition: Literal['snapshot', 'unchanged', 'resync']
    job: Job | None
    cursor: str = Field(min_length=1, max_length=4096)
    poll_after_ms: int | None = Field(ge=2000, le=3500)
    authorization_valid_for_ms: Literal[4000] = 4000

    @model_validator(mode='after')
    def _scope(self) -> Self:
        if (self.disposition == 'unchanged') != (self.job is None):
            raise ValueError('Only unchanged snapshots omit the current job.')
        if self.job is not None:
            if (self.job.workspace_id, self.job.job_id) != (self.workspace_id, self.job_id):
                raise ValueError('Job update scope mismatch.')
            if self.job.retryable is not None:
                raise ValueError('Control authorization is read separately from status polling.')
            terminal = self.job.status in ('succeeded', 'failed', 'cancelled', 'expired')
            if terminal != (self.poll_after_ms is None):
                raise ValueError('Only terminal snapshots stop status polling.')
        return self
