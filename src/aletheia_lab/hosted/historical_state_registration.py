"""Operator-pinned historical state inputs scoped to an authorized imported source."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Literal, Self, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, model_validator

from aletheia_lab.hosted_contract.session_capabilities import Identifier
from aletheia_lab.project.identity import canonical_project_sha256

from .content_hashing import content_hasher
from .historical_state_evidence import HistoricalStateInput
from .request_boundary import RequestRejected, decode_json_model

HistoricalStateInputs: TypeAlias = Mapping[tuple[str, str, str], HistoricalStateInput]
_SHA = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
_MAX_BYTES = 1024 * 1024


class HistoricalStateRegistration(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    workspace_id: Identifier
    project_id: Annotated[str, Field(pattern=r"^p3-project-[0-9a-f]{64}$")]
    source_sha256: _SHA
    receipt_sha256: _SHA
    evidence: HistoricalStateInput

    @model_validator(mode="after")
    def _verify_receipt(self) -> Self:
        if canonical_project_sha256(self.evidence.receipt.model_dump(mode="json")) != self.receipt_sha256:
            raise ValueError("Historical state receipt does not match the registered digest.")
        return self


class _Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    schema_version: Literal["historical-state-registrations/v1"]
    entries: Annotated[tuple[HistoricalStateRegistration, ...], Field(min_length=1, max_length=128)]


def load_historical_state_inputs(path: Path, expected_sha256: str) -> HistoricalStateInputs:
    """Read the reviewed manifest pinned by deployment configuration, never by upload."""
    if not path.is_absolute():
        raise ValueError("Historical state registration must have an absolute operator path.")
    with path.open("rb") as stream:
        raw = stream.read(_MAX_BYTES + 1)
    if len(raw) > _MAX_BYTES or content_hasher(raw).hexdigest() != expected_sha256:
        raise ValueError("Historical state registration does not match the configured digest.")
    try:
        manifest = decode_json_model(raw, _Manifest, maximum_bytes=_MAX_BYTES)
    except RequestRejected:
        raise ValueError("Invalid historical state registration.") from None
    entries = {(row.workspace_id, row.project_id, row.source_sha256): row.evidence for row in manifest.entries}
    if len(entries) != len(manifest.entries):
        raise ValueError("Duplicate historical state registration scope.")
    return entries
