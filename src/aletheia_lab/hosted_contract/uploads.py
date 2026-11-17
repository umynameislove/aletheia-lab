"""Bounded resumable-upload producer models and safe staging outcomes."""
from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from aletheia_lab.hosted_contract.session_capabilities import Identifier, Version

MAX_UPLOAD_BYTES = 200 * 1024 * 1024
MAX_PART_BYTES = 4 * 1024 * 1024
MAX_PARTS = 512
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
PartNumber = Annotated[int, Field(ge=1, le=MAX_PARTS)]


class _UploadModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)


class UploadPart(_UploadModel):
    part_number: PartNumber
    byte_size: int = Field(ge=1, le=MAX_PART_BYTES)
    sha256: Digest


class UploadManifest(_UploadModel):
    schema_version: Version
    source_type: Literal["zip_bundle"]
    byte_size: int = Field(ge=1, le=MAX_UPLOAD_BYTES)
    sha256: Digest
    parts: list[UploadPart] = Field(min_length=1, max_length=MAX_PARTS)
    parent_source_id: Identifier | None = None

    @model_validator(mode="after")
    def _manifest(self) -> Self:
        if ([part.part_number for part in self.parts] != list(range(1, len(self.parts) + 1))
                or sum(part.byte_size for part in self.parts) != self.byte_size):
            raise ValueError("A complete ordered part manifest matching the total length is required.")
        return self


class UploadStatus(_UploadModel):
    schema_version: Version = "product-api/v1-draft"
    workspace_id: Identifier
    upload_id: Identifier
    state: Literal["active", "ready", "cancelled", "expired"]
    byte_size: int = Field(ge=1, le=MAX_UPLOAD_BYTES)
    sha256: Digest
    expires_at: datetime
    received_bytes: int = Field(ge=0, le=MAX_UPLOAD_BYTES)
    received_parts: list[PartNumber] = Field(max_length=MAX_PARTS)
    missing_parts: list[PartNumber] = Field(max_length=MAX_PARTS)
    source_id: Identifier | None = None

    @model_validator(mode="after")
    def _progress(self) -> Self:
        parts = set(self.received_parts) | set(self.missing_parts)
        if (not parts or parts != set(range(1, len(parts) + 1))
                or self.expires_at.utcoffset() is None or self.received_bytes > self.byte_size
                or set(self.received_parts) & set(self.missing_parts)
                or len(set(self.received_parts)) != len(self.received_parts)
                or len(set(self.missing_parts)) != len(self.missing_parts)
                or (self.state == "ready") != (self.source_id is not None)
                or (self.state == "ready" and (self.missing_parts or self.received_bytes != self.byte_size))):
            raise ValueError("Invalid upload scope or confirmed progress.")
        return self
