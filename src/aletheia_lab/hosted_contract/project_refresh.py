"""Hosted refresh accepts an uploaded source, with the parent pinned by the server."""
from __future__ import annotations

from typing import Annotated, Literal, Self, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    model_serializer,
    model_validator,
)

from aletheia_lab.project.identity import PROJECT_ID_PATTERN

from .session_capabilities import Identifier, Version

SnapshotId = Annotated[str, Field(pattern=r"^upload-snapshot-[0-9a-f]{64}$")]


class RefreshRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)

    schema_version: Version
    source_id: Identifier


class RefreshResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)

    project_id: Annotated[str, Field(pattern=PROJECT_ID_PATTERN)]
    status: Literal["unchanged", "new_snapshot"]
    previous_snapshot_id: SnapshotId
    snapshot_id: SnapshotId
    revision: int = Field(ge=0, le=9223372036854775807)
    historical_state_available: Literal[True] | None = None

    @model_serializer(mode="wrap")
    def _omit_absent_historical_state(self, serialize: SerializerFunctionWrapHandler) -> dict[str, object]:
        result = cast(dict[str, object], serialize(self))
        if self.historical_state_available is None:
            result.pop("historical_state_available", None)
        return result

    @model_validator(mode="after")
    def _snapshot_status(self) -> Self:
        if (self.status == "unchanged") != (self.previous_snapshot_id == self.snapshot_id):
            raise ValueError("Refresh disposition must reconcile with its snapshots.")
        return self
