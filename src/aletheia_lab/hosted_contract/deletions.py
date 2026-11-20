"""Truthful deletion progress; the existing tombstone receipt stays unchanged."""

from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from .runtime_management import ProjectId
from .session_capabilities import Identifier, Version

TombstoneId = Annotated[str, Field(pattern=r"^p6-deletion-[0-9a-f]{64}$")]


class DeletionStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)

    schema_version: Version
    workspace_id: Identifier
    project_id: ProjectId
    tombstone_id: TombstoneId
    revision: int = Field(ge=0, le=9223372036854775807)
    status: Literal["retained", "purging", "purged"]
    requested_at: AwareDatetime
    eligible_at: AwareDatetime
    project_retention_days: int = Field(ge=0, le=3650)
    time_basis: Literal["delete_commit", "migration_first_seen", "restore_checkpoint"]
    purged_at: AwareDatetime | None
    records_removed: int = Field(ge=0)
    pending_objects: int = Field(ge=0)
    shared_objects_retained: int = Field(ge=0)
    raw_source_policy: Literal["workspace_sources_retained"] = "workspace_sources_retained"
    backup_policy: Literal["operator_managed_not_erased_here"] = "operator_managed_not_erased_here"

    @model_validator(mode="after")
    def _time(self) -> Self:
        if self.eligible_at < self.requested_at or (self.status == "purged") != (
            self.purged_at is not None
        ):
            raise ValueError("Deletion progress times do not match its state.")
        if self.purged_at is not None and self.purged_at < self.requested_at:
            raise ValueError("Deletion progress cannot precede its request.")
        return self
