"""Versioned upload snapshot envelope over validated P3 mapping facts.

This model does not authorize uploads or prove that mappings came from the
named source. A trusted worker must re-read source bytes and compute those facts
before the repository atomically publishes this envelope in the workspace.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from typing import Annotated, Generic, Literal, Self, TypeAlias, TypeVar

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationInfo,
    field_validator,
    model_validator,
)

from aletheia_lab.hosted_contract.session_capabilities import Identifier
from aletheia_lab.project.identity import canonical_project_json
from aletheia_lab.project.mapping import (
    AnyMappingConfiguration,
    AnyMappingResult,
    ProjectMappingConfiguration,
    ProjectMappingConfigurationV2,
    ProjectMappingResult,
    ProjectMappingResultV2,
    metric_identity,
    validate_mapping_dimensions,
)

from .historical_state_evidence import HistoricalStateInput

_MappingT = TypeVar('_MappingT', bound=AnyMappingConfiguration)
_ResultT = TypeVar('_ResultT', bound=AnyMappingResult)
_CAPTURE_FIELDS = frozenset({"snapshot_id", "captured_at"})


@dataclass
class _CatalogDigest:
    sha256: str = ""


def _canonical_fields(payload: dict[str, object]) -> dict[str, bytes]:
    # The envelope has fixed ASCII field names. Reuse the same P3 encoder for
    # each member, including normalization inside mapping facts and evidence.
    return {key: canonical_project_json({key: value})[1:-1].encode("utf-8")
            for key, value in payload.items()}


def _digest_fields(fields: dict[str, bytes], *, exclude: frozenset[str] = frozenset()) -> str:
    hashed = sha256(b"{")
    separator = b""
    for key in sorted(fields):
        if key in exclude:
            continue
        hashed.update(separator)
        hashed.update(fields[key])
        separator = b","
    hashed.update(b"}")
    return hashed.hexdigest()


class _UploadSnapshotBase(BaseModel, Generic[_MappingT, _ResultT]):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, revalidate_instances="always")

    schema_version: str
    snapshot_id: str = Field(pattern=r"^upload-snapshot-[0-9a-f]{64}$")
    workspace_id: Identifier
    source_id: Identifier
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    git_provenance: Literal["not_supplied"] = "not_supplied"
    mapping: _MappingT
    mapping_result: _ResultT
    captured_at: datetime
    record_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("captured_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("timezone required")
        return value

    def state_payload(self) -> dict[str, object]:
        return self.model_dump(mode="json", exclude={"snapshot_id", "captured_at", "record_sha256"})

    @model_validator(mode="after")
    def _reconciles(self, info: ValidationInfo) -> Self:
        mapping, result = self.mapping, self.mapping_result
        if (isinstance(mapping, ProjectMappingConfigurationV2) != isinstance(result, ProjectMappingResultV2)
                or result.status != "valid" or result.project_id != mapping.project_id
                or result.project_bundle_id != mapping.project_bundle_id
                or result.mapping_sha256 != mapping.mapping_sha256):
            raise ValueError("mapping facts do not reconcile")
        runs = {run.run_id for run in mapping.runs}
        sources = {source.mapping_id: source.project_item_id for source in mapping.metric_sources}
        identities = set()
        for observation in result.metric_observations:
            key = metric_identity(observation)
            if (observation.run_id not in runs
                    or sources.get(observation.source_mapping_id) != observation.project_item_id
                    or key in identities):
                raise ValueError("observation scope does not reconcile")
            identities.add(key)
        if isinstance(mapping, ProjectMappingConfigurationV2) and isinstance(result, ProjectMappingResultV2):
            validate_mapping_dimensions(mapping, result)
        if mapping.baseline_run_id not in {row.run_id for row in result.metric_observations}:
            raise ValueError("baseline has no observation")
        fields = _canonical_fields(self.model_dump(mode="json", exclude={"record_sha256"}))
        if self.snapshot_id != "upload-snapshot-" + _digest_fields(fields, exclude=_CAPTURE_FIELDS):
            raise ValueError("snapshot identity mismatch")
        if self.record_sha256 != _digest_fields(fields):
            raise ValueError("snapshot record hash mismatch")
        if isinstance(info.context, _CatalogDigest):
            fields.update(_canonical_fields({"record_sha256": self.record_sha256}))
            info.context.sha256 = _digest_fields(fields)
        return self


class UploadSnapshot(_UploadSnapshotBase[ProjectMappingConfiguration, ProjectMappingResult]):
    schema_version: Literal['hosted-upload-snapshot/v1'] = 'hosted-upload-snapshot/v1'


class UploadSnapshotV2(_UploadSnapshotBase[ProjectMappingConfigurationV2, ProjectMappingResultV2]):
    schema_version: Literal['hosted-upload-snapshot/v2'] = 'hosted-upload-snapshot/v2'


class HistoricalStateUploadSnapshot(_UploadSnapshotBase[AnyMappingConfiguration, AnyMappingResult]):
    """An upload snapshot with evidence independently registered by the server."""

    schema_version: Literal['hosted-upload-snapshot/v3'] = 'hosted-upload-snapshot/v3'
    mapping: Annotated[AnyMappingConfiguration, Field(discriminator='schema_version')]
    mapping_result: Annotated[AnyMappingResult, Field(discriminator='schema_version')]
    historical_state: HistoricalStateInput


AnyUploadSnapshot: TypeAlias = UploadSnapshot | UploadSnapshotV2 | HistoricalStateUploadSnapshot
UPLOAD_SNAPSHOT_TYPES = (UploadSnapshot, UploadSnapshotV2, HistoricalStateUploadSnapshot)
_SNAPSHOT_ADAPTER = TypeAdapter(Annotated[AnyUploadSnapshot, Field(discriminator='schema_version')])


def parse_upload_snapshot(value: object) -> AnyUploadSnapshot:
    return (_SNAPSHOT_ADAPTER.validate_json(value) if isinstance(value, str | bytes)
            else _SNAPSHOT_ADAPTER.validate_python(value))


def validated_upload_snapshot(value: object) -> tuple[AnyUploadSnapshot, str]:
    """Return the catalog digest from the same validation of the snapshot."""
    digest = _CatalogDigest()
    snapshot = (_SNAPSHOT_ADAPTER.validate_json(value, context=digest) if isinstance(value, str | bytes)
                else _SNAPSHOT_ADAPTER.validate_python(value, context=digest))
    return snapshot, digest.sha256


def build_upload_snapshot(*, workspace_id: str, source_id: str, source_sha256: str,
                          mapping: AnyMappingConfiguration, mapping_result: AnyMappingResult,
                          captured_at: datetime, historical_state: HistoricalStateInput | None = None) -> AnyUploadSnapshot:
    model: type[AnyUploadSnapshot]
    extra: dict[str, HistoricalStateInput] = {}
    if historical_state is not None:
        model = HistoricalStateUploadSnapshot
        extra["historical_state"] = historical_state
    else:
        model = UploadSnapshotV2 if isinstance(mapping, ProjectMappingConfigurationV2) else UploadSnapshot
    state = {"schema_version": model.model_fields['schema_version'].default, "workspace_id": workspace_id,
             "source_id": source_id, "source_sha256": source_sha256, "git_provenance": "not_supplied",
             "mapping": mapping.model_dump(mode="json"), "mapping_result": mapping_result.model_dump(mode="json")}
    if historical_state is not None:
        state["historical_state"] = historical_state.model_dump(mode="json")
    fields = _canonical_fields(state)
    snapshot_id = "upload-snapshot-" + _digest_fields(fields)
    fields.update(_canonical_fields({"snapshot_id": snapshot_id,
        "captured_at": TypeAdapter(datetime).dump_python(captured_at, mode="json")}))
    record_sha256 = _digest_fields(fields)
    del fields
    return parse_upload_snapshot({**state, "snapshot_id": snapshot_id, "mapping": mapping,
        "mapping_result": mapping_result, "captured_at": captured_at, "record_sha256": record_sha256, **extra})
