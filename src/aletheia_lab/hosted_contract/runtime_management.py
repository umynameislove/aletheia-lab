"""Compact project metadata and effective workspace upload policy."""
from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from aletheia_lab.product.questions import checked_product_question

from .session_capabilities import Identifier, Version
from .uploads import MAX_UPLOAD_BYTES

MAX_WORKSPACE_BYTES = MAX_UPLOAD_BYTES * 8
ProjectId = Annotated[str, Field(pattern=r"^p3-project-[0-9a-f]{64}$")]
SnapshotId = Annotated[str, Field(pattern=r"^(?:p3-snapshot|upload-snapshot)-[0-9a-f]{64}$")]
ResultId = Annotated[str, Field(pattern=r"^p6-result-[0-9a-f]{64}$")]
DisplayName = Annotated[str, Field(min_length=1, max_length=160)]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)


class ChangeProject(_Model):
    schema_version: Version
    display_name: DisplayName

    @field_validator("display_name")
    @classmethod
    def _name(cls, value: str) -> str:
        return checked_product_question(value)


class ProjectDetails(_Model):
    schema_version: Version
    workspace_id: Identifier
    project_id: ProjectId
    revision: int = Field(ge=0, le=9223372036854775807)
    display_name: DisplayName | None
    state: Literal["active", "deleting"]
    current_snapshot_id: SnapshotId | None
    current_result_id: ResultId | None

    @field_validator("display_name")
    @classmethod
    def _name(cls, value: str | None) -> str | None:
        return None if value is None else checked_product_question(value)


class WorkspacePolicyLimits(_Model):
    maximum_upload_bytes: int = Field(default=MAX_UPLOAD_BYTES, ge=1, le=MAX_UPLOAD_BYTES)
    maximum_workspace_bytes: int = Field(default=MAX_WORKSPACE_BYTES, ge=1, le=MAX_WORKSPACE_BYTES)

    @model_validator(mode="after")
    def _bounds(self) -> Self:
        if self.maximum_upload_bytes > self.maximum_workspace_bytes:
            raise ValueError("The upload limit must fit the workspace limit.")
        return self


class ChangeWorkspaceSettings(_Model):
    schema_version: Version
    maximum_upload_bytes: int | None = Field(default=None, ge=1, le=MAX_UPLOAD_BYTES)
    maximum_workspace_bytes: int | None = Field(default=None, ge=1, le=MAX_WORKSPACE_BYTES)

    @model_validator(mode="after")
    def _change(self) -> Self:
        if (not self.model_fields_set - {"schema_version"}
                or any(getattr(self, field) is None for field in self.model_fields_set - {"schema_version"})):
            raise ValueError("A positive workspace policy change is required.")
        return self


class WorkspaceSettings(WorkspacePolicyLimits):
    schema_version: Version
    workspace_id: Identifier
    revision: int = Field(ge=0, le=9223372036854775807)
    provenance: Literal["platform_default", "workspace_override"]
    source_policy: Literal["zip_bundle_only"] = "zip_bundle_only"
    retention_policy: Literal["retained_no_purge_schedule"] = "retained_no_purge_schedule"
