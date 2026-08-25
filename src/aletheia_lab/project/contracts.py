"""Strict ProjectBundle and ProjectItem contracts for local-project ingestion.

These models define the immutable representation boundary used by later P3
collectors, permission checks, persistence and evidence projection.  They do
not scan the filesystem, authorize a path, parse project code or declare that
an import is semantically valid.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated, Final, Literal, Self, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from aletheia_lab.project.identity import (
    ARTIFACT_ID_PATTERN,
    PROJECT_BUNDLE_ID_PATTERN,
    PROJECT_ID_PATTERN,
    PROJECT_ITEM_ID_PATTERN,
    SHA256_PATTERN,
    SNAPSHOT_ID_PATTERN,
    canonical_project_sha256,
    content_sha256,
    normalize_relative_project_path,
    normalize_text,
    project_id_for_root,
)

PROJECT_ITEM_SCHEMA_VERSION: Final[Literal["project-item/v1"]] = "project-item/v1"
PROJECT_MANIFEST_SCHEMA_VERSION: Final[Literal["project-manifest/v1"]] = (
    "project-manifest/v1"
)
PROJECT_BUNDLE_SCHEMA_VERSION: Final[Literal["project-bundle/v1"]] = "project-bundle/v1"
PROJECT_COLLECTOR_SCHEMA_VERSION: Final[Literal["project-collector/v1"]] = (
    "project-collector/v1"
)
PROJECT_WARNING_SCHEMA_VERSION: Final[Literal["project-parse-warning/v1"]] = (
    "project-parse-warning/v1"
)
PROJECT_ARTIFACT_SCHEMA_VERSION: Final[Literal["project-artifact-reference/v1"]] = (
    "project-artifact-reference/v1"
)

PROJECT_ITEM_IDENTITY_SCHEMA_VERSION: Final[Literal["project-item-identity/v1"]] = (
    "project-item-identity/v1"
)
PROJECT_MANIFEST_IDENTITY_SCHEMA_VERSION: Final[Literal["project-manifest-identity/v1"]] = (
    "project-manifest-identity/v1"
)
PROJECT_BUNDLE_IDENTITY_SCHEMA_VERSION: Final[Literal["project-bundle-identity/v1"]] = (
    "project-bundle-identity/v1"
)

_IDENTIFIER_PATTERN: Final[str] = r"^[A-Za-z0-9][A-Za-z0-9._:@+-]{0,127}$"
_CODE_PATTERN: Final[str] = r"^[a-z][a-z0-9_.-]{0,63}$"
_MEDIA_TYPE_PATTERN: Final[str] = (
    r"^[a-z0-9][a-z0-9!#$&^_.+-]{0,126}/[a-z0-9][a-z0-9!#$&^_.+-]{0,126}$"
)
_UTC_TIMESTAMP_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$"
)
_MAX_ITEM_BYTES: Final[int] = 1 << 50
_MAX_ITEMS: Final[int] = 1_000_000

Sha256 = Annotated[str, Field(pattern=SHA256_PATTERN)]
ProjectId = Annotated[str, Field(pattern=PROJECT_ID_PATTERN)]
ProjectItemId = Annotated[str, Field(pattern=PROJECT_ITEM_ID_PATTERN)]
ProjectBundleId = Annotated[str, Field(pattern=PROJECT_BUNDLE_ID_PATTERN)]

ProjectSourceType = Literal[
    "dataset",
    "metrics",
    "log",
    "config",
    "git",
    "artifact",
    "other",
]
ProjectVisibility = Literal["local_only", "diagnosis", "outbound"]
ProjectRedactionState = Literal["none", "redacted", "withheld"]
ProjectParseStatus = Literal["parsed", "unparsed", "failed"]

_ModelT = TypeVar("_ModelT", bound=BaseModel)


class ProjectContractError(ValueError):
    """Raised when independently supplied project artifacts disagree."""


class _StrictFrozenModel(BaseModel):
    """Deeply immutable contract node without coercion or unknown fields."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        revalidate_instances="always",
    )


def _revalidated(model: _ModelT) -> _ModelT:
    """Re-run validation after unsafe ``model_copy`` or ``model_construct`` use."""

    return type(model).model_validate(model.model_dump(mode="python", warnings=False))


def _canonical_timestamp(value: str, *, label: str) -> str:
    normalize_text(value, label=label, max_length=32)
    if _UTC_TIMESTAMP_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{label} must be a canonical UTC timestamp ending in Z")
    try:
        datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ValueError(f"{label} must be a valid calendar timestamp") from exc
    return value


def _optional_identifier(value: str | None, *, label: str) -> str | None:
    if value is None:
        return None
    normalize_text(value, label=label, max_length=128)
    if re.fullmatch(_IDENTIFIER_PATTERN, value) is None:
        raise ValueError(f"{label} is not a portable identifier")
    return value


def _artifact_id_for(sha256: str) -> str:
    return f"p3-artifact-{sha256}"


class ProjectCollector(_StrictFrozenModel):
    """Exact collector implementation identity for one imported item."""

    schema_version: Literal["project-collector/v1"] = PROJECT_COLLECTOR_SCHEMA_VERSION
    name: str = Field(pattern=_CODE_PATTERN, max_length=64)
    version: str = Field(min_length=1, max_length=128)

    @field_validator("version")
    @classmethod
    def _canonical_version(cls, value: str) -> str:
        return normalize_text(value, label="collector version", max_length=128)


class ProjectParseWarning(_StrictFrozenModel):
    """Structured, deterministic warning emitted while parsing one item."""

    schema_version: Literal["project-parse-warning/v1"] = PROJECT_WARNING_SCHEMA_VERSION
    code: str = Field(pattern=_CODE_PATTERN, max_length=64)
    message: str = Field(min_length=1, max_length=1024)

    @field_validator("message")
    @classmethod
    def _canonical_message(cls, value: str) -> str:
        return normalize_text(value, label="parse warning message", max_length=1024)


class ImmutableArtifactReference(_StrictFrozenModel):
    """Content address for immutable bytes produced or retained by a collector."""

    schema_version: Literal["project-artifact-reference/v1"] = PROJECT_ARTIFACT_SCHEMA_VERSION
    artifact_id: str = Field(pattern=ARTIFACT_ID_PATTERN)
    sha256: Sha256
    byte_size: int = Field(ge=0, le=_MAX_ITEM_BYTES)
    media_type: str = Field(pattern=_MEDIA_TYPE_PATTERN, max_length=255)

    @model_validator(mode="after")
    def _content_address_matches(self) -> ImmutableArtifactReference:
        if self.artifact_id != _artifact_id_for(self.sha256):
            raise ValueError("artifact_id does not match artifact SHA-256")
        return self

    @classmethod
    def from_bytes(cls, content: bytes, *, media_type: str) -> Self:
        """Create a content-addressed immutable artifact reference."""

        digest = content_sha256(content)
        return cls(
            artifact_id=_artifact_id_for(digest),
            sha256=digest,
            byte_size=len(content),
            media_type=media_type,
        )


def _item_identity_payload(
    *,
    project_id: str,
    relative_path: str,
    source_type: ProjectSourceType,
    media_type: str,
    source_schema: str | None,
    content_sha256_value: str,
    artifact: ImmutableArtifactReference,
    collector: ProjectCollector,
) -> dict[str, object]:
    return {
        "schema_version": PROJECT_ITEM_IDENTITY_SCHEMA_VERSION,
        "project_id": project_id,
        "relative_path": relative_path,
        "source_type": source_type,
        "media_type": media_type,
        "source_schema": source_schema,
        "content_sha256": content_sha256_value,
        "artifact_id": artifact.artifact_id,
        "artifact_sha256": artifact.sha256,
        "collector": collector.model_dump(mode="json"),
    }


def _project_item_id(**identity: object) -> str:
    return f"p3-item-{canonical_project_sha256(identity)}"


class ProjectItem(_StrictFrozenModel):
    """One source-bound, content-addressed item in a local project import."""

    schema_version: Literal["project-item/v1"] = PROJECT_ITEM_SCHEMA_VERSION
    project_item_id: ProjectItemId
    project_id: ProjectId
    relative_path: str
    source_type: ProjectSourceType
    media_type: str = Field(pattern=_MEDIA_TYPE_PATTERN, max_length=255)
    source_schema: str | None = Field(default=None, max_length=256)
    content_sha256: Sha256
    byte_size: int = Field(ge=0, le=_MAX_ITEM_BYTES)
    source_modified_at: str
    ingested_at: str
    collector: ProjectCollector
    visibility: ProjectVisibility
    redaction_state: ProjectRedactionState = "none"
    redaction_reasons: tuple[str, ...] = ()
    parse_status: ProjectParseStatus
    parse_warnings: tuple[ProjectParseWarning, ...] = ()
    parent_snapshot_id: str | None = Field(default=None, pattern=SNAPSHOT_ID_PATTERN)
    parent_run_id: str | None = Field(default=None, max_length=128)
    artifact: ImmutableArtifactReference

    @field_validator("relative_path")
    @classmethod
    def _canonical_relative_path(cls, value: str) -> str:
        return normalize_relative_project_path(value)

    @field_validator("source_schema")
    @classmethod
    def _canonical_source_schema(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return normalize_text(value, label="source schema", max_length=256)

    @field_validator("source_modified_at", "ingested_at")
    @classmethod
    def _canonical_time(cls, value: str, info: object) -> str:
        field_name = getattr(info, "field_name", "timestamp")
        return _canonical_timestamp(value, label=field_name)

    @field_validator("parent_run_id")
    @classmethod
    def _canonical_parent_run(cls, value: str | None) -> str | None:
        return _optional_identifier(value, label="parent_run_id")

    @field_validator("redaction_reasons")
    @classmethod
    def _canonical_redaction_reasons(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(
            normalize_text(reason, label="redaction reason", max_length=64) for reason in value
        )
        if any(re.fullmatch(_CODE_PATTERN, reason) is None for reason in normalized):
            raise ValueError("redaction reasons must be stable reason codes")
        if len(normalized) != len(set(normalized)):
            raise ValueError("redaction reasons must be unique")
        return tuple(sorted(normalized))

    @field_validator("parse_warnings")
    @classmethod
    def _canonical_warning_order(
        cls, value: tuple[ProjectParseWarning, ...]
    ) -> tuple[ProjectParseWarning, ...]:
        keys = tuple((warning.code, warning.message) for warning in value)
        if len(keys) != len(set(keys)):
            raise ValueError("parse warnings must be unique")
        return tuple(sorted(value, key=lambda warning: (warning.code, warning.message)))

    @model_validator(mode="after")
    def _identity_and_state_are_consistent(self) -> ProjectItem:
        identity = _item_identity_payload(
            project_id=self.project_id,
            relative_path=self.relative_path,
            source_type=self.source_type,
            media_type=self.media_type,
            source_schema=self.source_schema,
            content_sha256_value=self.content_sha256,
            artifact=self.artifact,
            collector=self.collector,
        )
        if self.project_item_id != _project_item_id(**identity):
            raise ValueError("project_item_id does not match canonical item identity")
        if self.redaction_state == "none" and self.redaction_reasons:
            raise ValueError("unredacted items must not carry redaction reasons")
        if self.redaction_state != "none" and not self.redaction_reasons:
            raise ValueError("redacted or withheld items require a redaction reason")
        if self.redaction_state == "withheld" and self.visibility != "local_only":
            raise ValueError("withheld items must remain local-only")
        if self.parse_status in {"unparsed", "failed"} and not self.parse_warnings:
            raise ValueError("unparsed or failed items require a structured parse warning")
        if self.parse_status == "failed" and self.visibility != "local_only":
            raise ValueError("failed items must remain local-only")
        return self

    def identity_payload(self) -> dict[str, object]:
        """Return the exact identity payload, excluding mutable audit metadata."""

        checked = _revalidated(self)
        return _item_identity_payload(
            project_id=checked.project_id,
            relative_path=checked.relative_path,
            source_type=checked.source_type,
            media_type=checked.media_type,
            source_schema=checked.source_schema,
            content_sha256_value=checked.content_sha256,
            artifact=checked.artifact,
            collector=checked.collector,
        )

    def canonical_sha256(self) -> str:
        """Hash the complete validated item, including audit metadata."""

        checked = _revalidated(self)
        return canonical_project_sha256(checked.model_dump(mode="json"))


def build_project_item(
    *,
    project_id: str,
    relative_path: str,
    source_type: ProjectSourceType,
    media_type: str,
    source_schema: str | None,
    source_bytes: bytes,
    source_modified_at: str,
    ingested_at: str,
    collector: ProjectCollector,
    visibility: ProjectVisibility,
    redaction_state: ProjectRedactionState = "none",
    redaction_reasons: tuple[str, ...] = (),
    parse_status: ProjectParseStatus = "parsed",
    parse_warnings: tuple[ProjectParseWarning, ...] = (),
    parent_snapshot_id: str | None = None,
    parent_run_id: str | None = None,
    artifact_bytes: bytes | None = None,
    artifact_media_type: str | None = None,
) -> ProjectItem:
    """Build an item while deriving every content and identity digest."""

    normalized_path = normalize_relative_project_path(relative_path)
    raw_sha256 = content_sha256(source_bytes)
    artifact_content = source_bytes if artifact_bytes is None else artifact_bytes
    artifact = ImmutableArtifactReference.from_bytes(
        artifact_content,
        media_type=media_type if artifact_media_type is None else artifact_media_type,
    )
    identity = _item_identity_payload(
        project_id=project_id,
        relative_path=normalized_path,
        source_type=source_type,
        media_type=media_type,
        source_schema=source_schema,
        content_sha256_value=raw_sha256,
        artifact=artifact,
        collector=collector,
    )
    return ProjectItem(
        project_item_id=_project_item_id(**identity),
        project_id=project_id,
        relative_path=normalized_path,
        source_type=source_type,
        media_type=media_type,
        source_schema=source_schema,
        content_sha256=raw_sha256,
        byte_size=len(source_bytes),
        source_modified_at=source_modified_at,
        ingested_at=ingested_at,
        collector=collector,
        visibility=visibility,
        redaction_state=redaction_state,
        redaction_reasons=redaction_reasons,
        parse_status=parse_status,
        parse_warnings=parse_warnings,
        parent_snapshot_id=parent_snapshot_id,
        parent_run_id=parent_run_id,
        artifact=artifact,
    )


def verify_project_item_source(item: ProjectItem, source_bytes: bytes) -> None:
    """Revalidate an item and bind its declared source checksum and size to bytes."""

    checked = _revalidated(item)
    if checked.content_sha256 != content_sha256(source_bytes):
        raise ProjectContractError("project item source bytes do not match content_sha256")
    if checked.byte_size != len(source_bytes):
        raise ProjectContractError("project item source bytes do not match byte_size")


def verify_project_item_artifact(item: ProjectItem, artifact_bytes: bytes) -> None:
    """Revalidate an item and bind its artifact reference to immutable bytes."""

    checked = _revalidated(item)
    if checked.artifact.sha256 != content_sha256(artifact_bytes):
        raise ProjectContractError("project item artifact bytes do not match artifact SHA-256")
    if checked.artifact.byte_size != len(artifact_bytes):
        raise ProjectContractError("project item artifact bytes do not match artifact byte_size")


class ProjectManifestEntry(_StrictFrozenModel):
    """Identity-bearing inventory entry for one project item."""

    project_item_id: ProjectItemId
    relative_path: str
    source_type: ProjectSourceType
    content_sha256: Sha256
    source_byte_size: int = Field(ge=0, le=_MAX_ITEM_BYTES)
    artifact_id: str = Field(pattern=ARTIFACT_ID_PATTERN)
    artifact_sha256: Sha256
    artifact_byte_size: int = Field(ge=0, le=_MAX_ITEM_BYTES)

    @field_validator("relative_path")
    @classmethod
    def _canonical_path(cls, value: str) -> str:
        return normalize_relative_project_path(value)


def _manifest_digest(
    project_id: str, entries: tuple[ProjectManifestEntry, ...]
) -> str:
    return canonical_project_sha256(
        {
            "schema_version": PROJECT_MANIFEST_IDENTITY_SCHEMA_VERSION,
            "project_id": project_id,
            "entries": [entry.model_dump(mode="json") for entry in entries],
        }
    )


class ProjectManifest(_StrictFrozenModel):
    """Self-validating canonical inventory of every item in one bundle."""

    schema_version: Literal["project-manifest/v1"] = PROJECT_MANIFEST_SCHEMA_VERSION
    project_id: ProjectId
    item_count: int = Field(gt=0, le=_MAX_ITEMS)
    source_byte_count: int = Field(ge=0)
    artifact_byte_count: int = Field(ge=0)
    entries: tuple[ProjectManifestEntry, ...]
    manifest_sha256: Sha256

    @field_validator("entries")
    @classmethod
    def _canonical_entries(
        cls, value: tuple[ProjectManifestEntry, ...]
    ) -> tuple[ProjectManifestEntry, ...]:
        if not value:
            raise ValueError("project manifest must contain at least one entry")
        ids = tuple(entry.project_item_id for entry in value)
        paths = tuple(entry.relative_path for entry in value)
        if len(ids) != len(set(ids)):
            raise ValueError("project manifest contains duplicate project_item_id")
        if len(paths) != len(set(paths)):
            raise ValueError("project manifest contains duplicate relative_path")
        return tuple(sorted(value, key=lambda entry: entry.relative_path))

    @model_validator(mode="after")
    def _inventory_is_bound(self) -> ProjectManifest:
        if self.item_count != len(self.entries):
            raise ValueError("project manifest item_count does not match entries")
        if self.source_byte_count != sum(entry.source_byte_size for entry in self.entries):
            raise ValueError("project manifest source_byte_count does not match entries")
        if self.artifact_byte_count != sum(entry.artifact_byte_size for entry in self.entries):
            raise ValueError("project manifest artifact_byte_count does not match entries")
        if self.manifest_sha256 != _manifest_digest(self.project_id, self.entries):
            raise ValueError("manifest_sha256 does not match the canonical item inventory")
        return self

    def canonical_sha256(self) -> str:
        checked = _revalidated(self)
        return canonical_project_sha256(checked.model_dump(mode="json"))


def _manifest_entry(item: ProjectItem) -> ProjectManifestEntry:
    checked = _revalidated(item)
    return ProjectManifestEntry(
        project_item_id=checked.project_item_id,
        relative_path=checked.relative_path,
        source_type=checked.source_type,
        content_sha256=checked.content_sha256,
        source_byte_size=checked.byte_size,
        artifact_id=checked.artifact.artifact_id,
        artifact_sha256=checked.artifact.sha256,
        artifact_byte_size=checked.artifact.byte_size,
    )


def build_project_manifest(
    project_id: str, items: tuple[ProjectItem, ...]
) -> ProjectManifest:
    """Build a deterministic manifest from independently revalidated items."""

    if not items:
        raise ValueError("cannot build an empty project manifest")
    if any(item.project_id != project_id for item in items):
        raise ValueError("cannot build a project manifest from foreign project items")
    entries = tuple(
        sorted((_manifest_entry(item) for item in items), key=lambda item: item.relative_path)
    )
    return ProjectManifest(
        project_id=project_id,
        item_count=len(entries),
        source_byte_count=sum(entry.source_byte_size for entry in entries),
        artifact_byte_count=sum(entry.artifact_byte_size for entry in entries),
        entries=entries,
        manifest_sha256=_manifest_digest(project_id, entries),
    )


def _unique_sorted_strings(
    value: tuple[str, ...], *, label: str, pattern: str | None = None
) -> tuple[str, ...]:
    normalized = tuple(normalize_text(item, label=label, max_length=256) for item in value)
    if pattern is not None and any(re.fullmatch(pattern, item) is None for item in normalized):
        raise ValueError(f"{label} contains an invalid identifier")
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{label} must contain unique values")
    return tuple(sorted(normalized))


def _bundle_identity_payload(
    *,
    project_id: str,
    display_name: str,
    granted_root_fingerprint: str,
    created_at: str,
    updated_at: str,
    collector_versions: tuple[ProjectCollector, ...],
    project_manifest: ProjectManifest,
    permission_policy_sha256: str | None,
    provider_policy_sha256: str | None,
    mapping_configuration_sha256: str | None,
    validation_summary_sha256: str | None,
    ingestion_report_sha256: str | None,
    deletion_policy_sha256: str | None,
    retention_policy_sha256: str | None,
    snapshot_refs: tuple[str, ...],
    evidence_bundle_refs: tuple[str, ...],
) -> dict[str, object]:
    return {
        "schema_version": PROJECT_BUNDLE_IDENTITY_SCHEMA_VERSION,
        "project_id": project_id,
        "display_name": display_name,
        "granted_root_fingerprint": granted_root_fingerprint,
        "created_at": created_at,
        "updated_at": updated_at,
        "collector_versions": [item.model_dump(mode="json") for item in collector_versions],
        "project_manifest_sha256": project_manifest.manifest_sha256,
        "permission_policy_sha256": permission_policy_sha256,
        "provider_policy_sha256": provider_policy_sha256,
        "mapping_configuration_sha256": mapping_configuration_sha256,
        "validation_summary_sha256": validation_summary_sha256,
        "ingestion_report_sha256": ingestion_report_sha256,
        "deletion_policy_sha256": deletion_policy_sha256,
        "retention_policy_sha256": retention_policy_sha256,
        "snapshot_refs": list(snapshot_refs),
        "evidence_bundle_refs": list(evidence_bundle_refs),
    }


class ProjectBundle(_StrictFrozenModel):
    """Immutable import record for one exact local-project state.

    ``contract_state`` means only that the representation is schema-valid.  It
    is not a permission, semantic-validation or scientific-admission verdict.
    Optional policy/report bindings become required at later P3 gates.
    """

    schema_version: Literal["project-bundle/v1"] = PROJECT_BUNDLE_SCHEMA_VERSION
    contract_state: Literal["schema_validated"] = "schema_validated"
    project_bundle_id: ProjectBundleId
    project_id: ProjectId
    display_name: str = Field(min_length=1, max_length=128)
    granted_root_fingerprint: Sha256
    created_at: str
    updated_at: str
    collector_versions: tuple[ProjectCollector, ...]
    project_manifest: ProjectManifest
    items: tuple[ProjectItem, ...]
    permission_policy_sha256: Sha256 | None = None
    provider_policy_sha256: Sha256 | None = None
    mapping_configuration_sha256: Sha256 | None = None
    validation_summary_sha256: Sha256 | None = None
    ingestion_report_sha256: Sha256 | None = None
    deletion_policy_sha256: Sha256 | None = None
    retention_policy_sha256: Sha256 | None = None
    snapshot_refs: tuple[str, ...] = ()
    evidence_bundle_refs: tuple[str, ...] = ()

    @field_validator("display_name")
    @classmethod
    def _canonical_display_name(cls, value: str) -> str:
        return normalize_text(value, label="project display name", max_length=128)

    @field_validator("created_at", "updated_at")
    @classmethod
    def _canonical_bundle_time(cls, value: str, info: object) -> str:
        field_name = getattr(info, "field_name", "timestamp")
        return _canonical_timestamp(value, label=field_name)

    @field_validator("collector_versions")
    @classmethod
    def _canonical_collectors(
        cls, value: tuple[ProjectCollector, ...]
    ) -> tuple[ProjectCollector, ...]:
        if not value:
            raise ValueError("project bundle must declare at least one collector")
        keys = tuple((collector.name, collector.version) for collector in value)
        if len(keys) != len(set(keys)):
            raise ValueError("collector versions must be unique")
        return tuple(sorted(value, key=lambda collector: (collector.name, collector.version)))

    @field_validator("items")
    @classmethod
    def _canonical_items(cls, value: tuple[ProjectItem, ...]) -> tuple[ProjectItem, ...]:
        if not value:
            raise ValueError("project bundle must contain at least one item")
        if len(value) > _MAX_ITEMS:
            raise ValueError("project bundle exceeds the maximum item count")
        ids = tuple(item.project_item_id for item in value)
        paths = tuple(item.relative_path for item in value)
        if len(ids) != len(set(ids)):
            raise ValueError("project bundle contains duplicate project_item_id")
        if len(paths) != len(set(paths)):
            raise ValueError("project bundle contains duplicate relative_path")
        return tuple(sorted(value, key=lambda item: item.relative_path))

    @field_validator("snapshot_refs")
    @classmethod
    def _canonical_snapshot_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _unique_sorted_strings(
            value, label="snapshot references", pattern=SNAPSHOT_ID_PATTERN
        )

    @field_validator("evidence_bundle_refs")
    @classmethod
    def _canonical_evidence_refs(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        return _unique_sorted_strings(
            value, label="evidence bundle references", pattern=_IDENTIFIER_PATTERN
        )

    @model_validator(mode="after")
    def _bundle_is_reconciled(self) -> ProjectBundle:
        if self.project_id != project_id_for_root(self.granted_root_fingerprint):
            raise ValueError("project_id does not match granted_root_fingerprint")
        if datetime.fromisoformat(self.updated_at[:-1] + "+00:00") < datetime.fromisoformat(
            self.created_at[:-1] + "+00:00"
        ):
            raise ValueError("updated_at must not precede created_at")
        if self.project_manifest.project_id != self.project_id:
            raise ValueError("project manifest belongs to a different project")
        if any(item.project_id != self.project_id for item in self.items):
            raise ValueError("every project item must belong to the bundle project")

        rebuilt_manifest = build_project_manifest(self.project_id, self.items)
        if rebuilt_manifest.model_dump(mode="json") != self.project_manifest.model_dump(
            mode="json"
        ):
            raise ValueError("project manifest does not exactly reconcile with bundle items")

        declared_collectors = {
            (collector.name, collector.version) for collector in self.collector_versions
        }
        used_collectors = {(item.collector.name, item.collector.version) for item in self.items}
        if declared_collectors != used_collectors:
            raise ValueError("collector_versions must exactly match collectors used by items")

        identity = _bundle_identity_payload(
            project_id=self.project_id,
            display_name=self.display_name,
            granted_root_fingerprint=self.granted_root_fingerprint,
            created_at=self.created_at,
            updated_at=self.updated_at,
            collector_versions=self.collector_versions,
            project_manifest=self.project_manifest,
            permission_policy_sha256=self.permission_policy_sha256,
            provider_policy_sha256=self.provider_policy_sha256,
            mapping_configuration_sha256=self.mapping_configuration_sha256,
            validation_summary_sha256=self.validation_summary_sha256,
            ingestion_report_sha256=self.ingestion_report_sha256,
            deletion_policy_sha256=self.deletion_policy_sha256,
            retention_policy_sha256=self.retention_policy_sha256,
            snapshot_refs=self.snapshot_refs,
            evidence_bundle_refs=self.evidence_bundle_refs,
        )
        expected_id = f"p3-bundle-{canonical_project_sha256(identity)}"
        if self.project_bundle_id != expected_id:
            raise ValueError("project_bundle_id does not match canonical bundle identity")
        return self

    def identity_payload(self) -> dict[str, object]:
        """Return the exact payload used to derive ``project_bundle_id``."""

        checked = _revalidated(self)
        return _bundle_identity_payload(
            project_id=checked.project_id,
            display_name=checked.display_name,
            granted_root_fingerprint=checked.granted_root_fingerprint,
            created_at=checked.created_at,
            updated_at=checked.updated_at,
            collector_versions=checked.collector_versions,
            project_manifest=checked.project_manifest,
            permission_policy_sha256=checked.permission_policy_sha256,
            provider_policy_sha256=checked.provider_policy_sha256,
            mapping_configuration_sha256=checked.mapping_configuration_sha256,
            validation_summary_sha256=checked.validation_summary_sha256,
            ingestion_report_sha256=checked.ingestion_report_sha256,
            deletion_policy_sha256=checked.deletion_policy_sha256,
            retention_policy_sha256=checked.retention_policy_sha256,
            snapshot_refs=checked.snapshot_refs,
            evidence_bundle_refs=checked.evidence_bundle_refs,
        )

    def canonical_sha256(self) -> str:
        """Hash the complete, recursively revalidated project bundle."""

        checked = _revalidated(self)
        return canonical_project_sha256(checked.model_dump(mode="json"))


def build_project_bundle(
    *,
    project_id: str,
    display_name: str,
    granted_root_fingerprint: str,
    created_at: str,
    updated_at: str,
    items: tuple[ProjectItem, ...],
    permission_policy_sha256: str | None = None,
    provider_policy_sha256: str | None = None,
    mapping_configuration_sha256: str | None = None,
    validation_summary_sha256: str | None = None,
    ingestion_report_sha256: str | None = None,
    deletion_policy_sha256: str | None = None,
    retention_policy_sha256: str | None = None,
    snapshot_refs: tuple[str, ...] = (),
    evidence_bundle_refs: tuple[str, ...] = (),
) -> ProjectBundle:
    """Construct a reconciled bundle and derive its manifest and stable ID."""

    checked_items = tuple(_revalidated(item) for item in items)
    collectors = tuple(
        sorted(
            {item.collector for item in checked_items},
            key=lambda collector: (collector.name, collector.version),
        )
    )
    manifest = build_project_manifest(project_id, checked_items)
    sorted_snapshots = _unique_sorted_strings(
        snapshot_refs, label="snapshot references", pattern=SNAPSHOT_ID_PATTERN
    )
    sorted_evidence = _unique_sorted_strings(
        evidence_bundle_refs, label="evidence bundle references", pattern=_IDENTIFIER_PATTERN
    )
    identity = _bundle_identity_payload(
        project_id=project_id,
        display_name=display_name,
        granted_root_fingerprint=granted_root_fingerprint,
        created_at=created_at,
        updated_at=updated_at,
        collector_versions=collectors,
        project_manifest=manifest,
        permission_policy_sha256=permission_policy_sha256,
        provider_policy_sha256=provider_policy_sha256,
        mapping_configuration_sha256=mapping_configuration_sha256,
        validation_summary_sha256=validation_summary_sha256,
        ingestion_report_sha256=ingestion_report_sha256,
        deletion_policy_sha256=deletion_policy_sha256,
        retention_policy_sha256=retention_policy_sha256,
        snapshot_refs=sorted_snapshots,
        evidence_bundle_refs=sorted_evidence,
    )
    return ProjectBundle(
        project_bundle_id=f"p3-bundle-{canonical_project_sha256(identity)}",
        project_id=project_id,
        display_name=display_name,
        granted_root_fingerprint=granted_root_fingerprint,
        created_at=created_at,
        updated_at=updated_at,
        collector_versions=collectors,
        project_manifest=manifest,
        items=checked_items,
        permission_policy_sha256=permission_policy_sha256,
        provider_policy_sha256=provider_policy_sha256,
        mapping_configuration_sha256=mapping_configuration_sha256,
        validation_summary_sha256=validation_summary_sha256,
        ingestion_report_sha256=ingestion_report_sha256,
        deletion_policy_sha256=deletion_policy_sha256,
        retention_policy_sha256=retention_policy_sha256,
        snapshot_refs=sorted_snapshots,
        evidence_bundle_refs=sorted_evidence,
    )
