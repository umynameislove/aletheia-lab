"""Upload preview and explicit mapping contracts shared by producer and consumer."""
from __future__ import annotations

import json
from datetime import datetime
from typing import Annotated, Literal, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_validator,
    model_validator,
)

from .session_capabilities import Identifier

Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
PreviewId = Annotated[str, Field(pattern=r"^prev_[0-9a-f]{32}$")]


class ImportModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class PreviewRequest(ImportModel):
    schema_version: Literal["product-api/v1-draft"] = "product-api/v1-draft"
    source_id: Identifier


class ConfirmImport(ImportModel):
    schema_version: Literal["product-api/v1-draft"] = "product-api/v1-draft"
    mapping: dict[str, JsonValue]

    @field_validator("mapping")
    @classmethod
    def _server_binding(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        if any(key.startswith("_") for key in value):
            raise ValueError("Server mapping bindings cannot be supplied by a client.")
        return value


class ExcludedMetric(ImportModel):
    project_item_id: str = Field(min_length=1, max_length=128)
    metric_name: str = Field(min_length=1, max_length=1000)
    reason: Literal["unsupported_metric_identifier"]
    source_record_index: int = Field(ge=0)


class PreviewImport(ImportModel):
    schema_version: Literal["product-api/v1-draft"]
    preview_id: PreviewId
    workspace_id: Identifier
    source_id: Identifier
    source_sha256: Sha256
    source_state_sha256: Sha256
    mapping_candidates: dict[str, JsonValue]
    excluded_metrics: list[ExcludedMetric] = Field(max_length=1000)
    excluded_metrics_total: int | None = Field(default=None, ge=0)
    expires_at: AwareDatetime

    @model_validator(mode="after")
    def _bounded(self) -> Self:
        if self.excluded_metrics_total is not None and self.excluded_metrics_total < len(self.excluded_metrics):
            raise ValueError("Excluded metric total cannot be smaller than the returned entries.")
        encoded = json.dumps(self.model_dump(mode="json"), ensure_ascii=True, allow_nan=False,
                             separators=(",", ":"))
        if len(encoded.encode("utf-8")) > 1024 * 1024:
            raise ValueError("Preview exceeds the response budget.")
        return self


def preview_fixture() -> PreviewImport:
    """Synthetic serializer fixture, with no private source path or contents."""
    return PreviewImport(schema_version="product-api/v1-draft", preview_id="prev_" + "1" * 32,
        workspace_id="ws_synthetic", source_id="src_synthetic", source_sha256="a" * 64,
        source_state_sha256="b" * 64,
        mapping_candidates={"runs": [], "metric_sources": [], "unresolved": ["target_run"]},
        excluded_metrics=[ExcludedMetric(project_item_id="item_synthetic", metric_name="độ chính xác",
                                        reason="unsupported_metric_identifier", source_record_index=0)],
        excluded_metrics_total=1,
        expires_at=datetime.fromisoformat("2030-01-01T00:00:00+00:00"))


class MappingIssue(ImportModel):
    code: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    field: str = Field(pattern=r"^[A-Za-z0-9_.\[\]-]{1,128}$")
    candidate_id: Identifier | None = None
    record_index: int | None = Field(default=None, ge=0)


class MappingFailure(ImportModel):
    schema_version: Literal["hosted-upload-error/v1"]
    code: Literal["mapping_invalid"]
    issues: list[MappingIssue] = Field(min_length=1, max_length=128)
