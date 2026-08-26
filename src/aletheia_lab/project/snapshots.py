"""Immutable, content-addressed snapshots of validated project state.

A snapshot is minted only from a fully reconciled import, collection, Git and
mapping boundary.  It contains identifiers and bounded observations, never raw
project payloads.  State identity deliberately excludes ``captured_at`` so an
unchanged refresh is idempotent; a second record digest protects that audit
timestamp from mutation.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Final, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from aletheia_lab.project.collectors import ProjectFileCollection, ProjectGitState
from aletheia_lab.project.contracts import ProjectBundle, build_project_bundle
from aletheia_lab.project.identity import (
    PROJECT_BUNDLE_ID_PATTERN,
    PROJECT_ID_PATTERN,
    PROJECT_ITEM_ID_PATTERN,
    SHA256_PATTERN,
    SNAPSHOT_ID_PATTERN,
    canonical_project_sha256,
    normalize_relative_project_path,
    normalize_text,
)
from aletheia_lab.project.mapping import (
    MetricObservation,
    ProjectMappingConfiguration,
    ProjectMappingResult,
)

PROJECT_SNAPSHOT_SCHEMA_VERSION: Final[Literal["project-snapshot/v1"]] = (
    "project-snapshot/v1"
)
PROJECT_SNAPSHOT_STATE_SCHEMA_VERSION: Final[
    Literal["project-snapshot-state/v1"]
] = "project-snapshot-state/v1"
PROJECT_SNAPSHOT_RECORD_SCHEMA_VERSION: Final[
    Literal["project-snapshot-record/v1"]
] = "project-snapshot-record/v1"

Sha256 = Annotated[str, Field(pattern=SHA256_PATTERN)]
_ModelT = TypeVar("_ModelT", bound=BaseModel)
_COLLECTOR_NAME_PATTERN: Final[str] = r"^[a-z][a-z0-9_.-]{0,63}$"


class ProjectSnapshotError(ValueError):
    """Raised when sources cannot mint one auditable project snapshot."""


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, revalidate_instances="always"
    )


def _checked(model: _ModelT) -> _ModelT:
    return type(model).model_validate(model.model_dump(mode="python", warnings=False))


def _canonical_timestamp(value: str) -> str:
    normalize_text(value, label="snapshot captured_at", max_length=32)
    if not value.endswith("Z"):
        raise ValueError("snapshot captured_at must be canonical UTC ending in Z")
    try:
        datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ValueError("snapshot captured_at must be a valid timestamp") from exc
    return value


class SnapshotCollectorBinding(_StrictFrozenModel):
    """One exact collector implementation and its bounded output digest."""

    role: Literal["import", "file_catalog", "git_state"]
    name: str = Field(pattern=_COLLECTOR_NAME_PATTERN, max_length=64)
    version: str = Field(min_length=1, max_length=128)
    output_sha256: Sha256

    @field_validator("version")
    @classmethod
    def _canonical_version(cls, value: str) -> str:
        return normalize_text(value, label="snapshot collector version", max_length=128)


class ProjectSnapshotItem(_StrictFrozenModel):
    """Payload-free item state retained in a snapshot."""

    project_item_id: str = Field(pattern=PROJECT_ITEM_ID_PATTERN)
    relative_path: str
    source_type: Literal[
        "dataset", "metrics", "log", "config", "git", "artifact", "other"
    ]
    source_sha256: Sha256
    artifact_sha256: Sha256
    observation_sha256: Sha256
    visibility: Literal["local_only", "diagnosis", "outbound"]
    redaction_state: Literal["none", "redacted", "withheld"]

    @field_validator("relative_path")
    @classmethod
    def _canonical_path(cls, value: str) -> str:
        return normalize_relative_project_path(value)


def _state_payload(
    *,
    project_id: str,
    source_bundle_id: str,
    source_bundle_sha256: str,
    project_manifest_sha256: str,
    file_collection_sha256: str,
    git_state_sha256: str,
    mapping_configuration_sha256: str,
    mapping_result_sha256: str,
    baseline_run_id: str,
    collectors: tuple[SnapshotCollectorBinding, ...],
    items: tuple[ProjectSnapshotItem, ...],
    metric_observations: tuple[MetricObservation, ...],
) -> dict[str, object]:
    return {
        "schema_version": PROJECT_SNAPSHOT_STATE_SCHEMA_VERSION,
        "project_id": project_id,
        "source_bundle_id": source_bundle_id,
        "source_bundle_sha256": source_bundle_sha256,
        "project_manifest_sha256": project_manifest_sha256,
        "file_collection_sha256": file_collection_sha256,
        "git_state_sha256": git_state_sha256,
        "mapping_configuration_sha256": mapping_configuration_sha256,
        "mapping_result_sha256": mapping_result_sha256,
        "baseline_run_id": baseline_run_id,
        "collectors": [value.model_dump(mode="json") for value in collectors],
        "items": [value.model_dump(mode="json") for value in items],
        "metric_observations": [
            value.model_dump(mode="json") for value in metric_observations
        ],
    }


def _record_digest(*, state_sha256: str, captured_at: str) -> str:
    return canonical_project_sha256(
        {
            "schema_version": PROJECT_SNAPSHOT_RECORD_SCHEMA_VERSION,
            "state_sha256": state_sha256,
            "captured_at": captured_at,
        }
    )


class ProjectSnapshot(_StrictFrozenModel):
    """One immutable and recursively self-validating project state."""

    schema_version: Literal["project-snapshot/v1"] = PROJECT_SNAPSHOT_SCHEMA_VERSION
    snapshot_id: str = Field(pattern=SNAPSHOT_ID_PATTERN)
    project_id: str = Field(pattern=PROJECT_ID_PATTERN)
    source_bundle_id: str = Field(pattern=PROJECT_BUNDLE_ID_PATTERN)
    source_bundle_sha256: Sha256
    project_manifest_sha256: Sha256
    file_collection_sha256: Sha256
    git_state_sha256: Sha256
    mapping_configuration_sha256: Sha256
    mapping_result_sha256: Sha256
    baseline_run_id: str = Field(min_length=1, max_length=128)
    collectors: tuple[SnapshotCollectorBinding, ...]
    items: tuple[ProjectSnapshotItem, ...]
    metric_observations: tuple[MetricObservation, ...]
    captured_at: str
    state_sha256: Sha256
    record_sha256: Sha256

    @field_validator("baseline_run_id")
    @classmethod
    def _canonical_baseline(cls, value: str) -> str:
        return normalize_text(value, label="snapshot baseline run ID", max_length=128)

    @field_validator("captured_at")
    @classmethod
    def _valid_time(cls, value: str) -> str:
        return _canonical_timestamp(value)

    @field_validator("collectors")
    @classmethod
    def _canonical_collectors(
        cls, values: tuple[SnapshotCollectorBinding, ...]
    ) -> tuple[SnapshotCollectorBinding, ...]:
        keys = tuple((value.role, value.name, value.version) for value in values)
        if not values or len(keys) != len(set(keys)):
            raise ValueError("snapshot collector bindings must be non-empty and unique")
        roles = {value.role for value in values}
        if not {"import", "file_catalog", "git_state"}.issubset(roles):
            raise ValueError("snapshot must bind import, file-catalog and Git collectors")
        return tuple(sorted(values, key=lambda value: (value.role, value.name, value.version)))

    @field_validator("items")
    @classmethod
    def _canonical_items(
        cls, values: tuple[ProjectSnapshotItem, ...]
    ) -> tuple[ProjectSnapshotItem, ...]:
        ids = tuple(value.project_item_id for value in values)
        paths = tuple(value.relative_path for value in values)
        if not values or len(ids) != len(set(ids)) or len(paths) != len(set(paths)):
            raise ValueError("snapshot items must be non-empty with unique IDs and paths")
        return tuple(sorted(values, key=lambda value: value.relative_path))

    @field_validator("metric_observations")
    @classmethod
    def _canonical_metrics(
        cls, values: tuple[MetricObservation, ...]
    ) -> tuple[MetricObservation, ...]:
        ids = tuple(value.observation_id for value in values)
        identities = tuple(
            (value.run_id, value.metric_name, value.step) for value in values
        )
        if not values or len(ids) != len(set(ids)) or len(identities) != len(set(identities)):
            raise ValueError("snapshot metrics must be non-empty with unique identities")
        return tuple(
            sorted(
                values,
                key=lambda value: (
                    value.run_id,
                    value.metric_name,
                    -1 if value.step is None else value.step,
                ),
            )
        )

    @model_validator(mode="after")
    def _identity_reconciles(self) -> ProjectSnapshot:
        item_ids = {value.project_item_id for value in self.items}
        if any(value.project_item_id not in item_ids for value in self.metric_observations):
            raise ValueError("snapshot metrics reference an item outside the snapshot")
        if not any(value.run_id == self.baseline_run_id for value in self.metric_observations):
            raise ValueError("snapshot baseline has no metric observation")
        state = _state_payload(
            project_id=self.project_id,
            source_bundle_id=self.source_bundle_id,
            source_bundle_sha256=self.source_bundle_sha256,
            project_manifest_sha256=self.project_manifest_sha256,
            file_collection_sha256=self.file_collection_sha256,
            git_state_sha256=self.git_state_sha256,
            mapping_configuration_sha256=self.mapping_configuration_sha256,
            mapping_result_sha256=self.mapping_result_sha256,
            baseline_run_id=self.baseline_run_id,
            collectors=self.collectors,
            items=self.items,
            metric_observations=self.metric_observations,
        )
        expected_state = canonical_project_sha256(state)
        if self.state_sha256 != expected_state:
            raise ValueError("state_sha256 does not match snapshot state")
        if self.snapshot_id != f"p3-snapshot-{expected_state}":
            raise ValueError("snapshot_id does not match snapshot state")
        if self.record_sha256 != _record_digest(
            state_sha256=expected_state, captured_at=self.captured_at
        ):
            raise ValueError("record_sha256 does not match snapshot audit record")
        return self


def _reconcile_sources(
    bundle: ProjectBundle,
    collection: ProjectFileCollection,
    git_state: ProjectGitState,
    configuration: ProjectMappingConfiguration,
    result: ProjectMappingResult,
) -> None:
    if collection.project_id != bundle.project_id or git_state.project_id != bundle.project_id:
        raise ProjectSnapshotError("snapshot sources belong to different projects")
    if (
        configuration.project_id != bundle.project_id
        or configuration.project_bundle_id != collection.project_bundle_id
    ):
        raise ProjectSnapshotError("mapping configuration belongs to a different project state")
    if configuration.file_collection_sha256 != collection.collection_sha256:
        raise ProjectSnapshotError("mapping configuration does not bind the file collection")
    if bundle.mapping_configuration_sha256 != configuration.mapping_sha256:
        raise ProjectSnapshotError("bundle is not bound to this mapping configuration")
    precursor = build_project_bundle(
        project_id=bundle.project_id,
        display_name=bundle.display_name,
        granted_root_fingerprint=bundle.granted_root_fingerprint,
        created_at=bundle.created_at,
        updated_at=bundle.updated_at,
        items=bundle.items,
        permission_policy_sha256=bundle.permission_policy_sha256,
        provider_policy_sha256=bundle.provider_policy_sha256,
        validation_summary_sha256=bundle.validation_summary_sha256,
        ingestion_report_sha256=bundle.ingestion_report_sha256,
        deletion_policy_sha256=bundle.deletion_policy_sha256,
        retention_policy_sha256=bundle.retention_policy_sha256,
        snapshot_refs=bundle.snapshot_refs,
        evidence_bundle_refs=bundle.evidence_bundle_refs,
    )
    if precursor.project_bundle_id != collection.project_bundle_id:
        raise ProjectSnapshotError("bound bundle does not derive from the collected import state")
    if (
        result.status != "valid"
        or result.project_id != bundle.project_id
        or result.project_bundle_id != collection.project_bundle_id
        or result.mapping_sha256 != configuration.mapping_sha256
    ):
        raise ProjectSnapshotError("only the matching valid mapping result can be snapshotted")

    items = {value.project_item_id: value for value in bundle.items}
    observations = {value.project_item_id: value for value in collection.observations}
    if set(items) != set(observations):
        raise ProjectSnapshotError("snapshot item and collection census do not reconcile")
    for item_id, item in items.items():
        observed = observations[item_id]
        if (
            observed.relative_path != item.relative_path
            or observed.source_type != item.source_type
            or observed.source_sha256 != item.content_sha256
            or observed.artifact_sha256 != item.artifact.sha256
            or observed.visibility != item.visibility
            or observed.redaction_state != item.redaction_state
        ):
            raise ProjectSnapshotError("snapshot item does not match its file observation")


def build_project_snapshot(
    bundle: ProjectBundle,
    collection: ProjectFileCollection,
    git_state: ProjectGitState,
    configuration: ProjectMappingConfiguration,
    result: ProjectMappingResult,
    *,
    captured_at: str,
) -> ProjectSnapshot:
    """Mint a deterministic snapshot from independently revalidated sources."""

    checked_bundle = _checked(bundle)
    checked_collection = _checked(collection)
    checked_git = _checked(git_state)
    checked_configuration = _checked(configuration)
    checked_result = _checked(result)
    captured_at = _canonical_timestamp(captured_at)
    _reconcile_sources(
        checked_bundle,
        checked_collection,
        checked_git,
        checked_configuration,
        checked_result,
    )

    observed = {value.project_item_id: value for value in checked_collection.observations}
    items = tuple(
        ProjectSnapshotItem(
            project_item_id=item.project_item_id,
            relative_path=item.relative_path,
            source_type=item.source_type,
            source_sha256=item.content_sha256,
            artifact_sha256=item.artifact.sha256,
            observation_sha256=observed[item.project_item_id].observation_sha256,
            visibility=item.visibility,
            redaction_state=item.redaction_state,
        )
        for item in checked_bundle.items
    )
    collectors = tuple(sorted(
        [
            SnapshotCollectorBinding(
                role="import",
                name=value.name,
                version=value.version,
                output_sha256=checked_bundle.project_manifest.manifest_sha256,
            )
            for value in checked_bundle.collector_versions
        ]
        + [
            SnapshotCollectorBinding(
                role="file_catalog",
                name="project-file-catalog",
                version=checked_collection.collector_version,
                output_sha256=checked_collection.collection_sha256,
            ),
            SnapshotCollectorBinding(
                role="git_state",
                name="project-git-state",
                version=checked_git.collector_version,
                output_sha256=checked_git.state_sha256,
            ),
        ],
        key=lambda value: (value.role, value.name, value.version),
    ))
    state = _state_payload(
        project_id=checked_bundle.project_id,
        source_bundle_id=checked_bundle.project_bundle_id,
        source_bundle_sha256=checked_bundle.canonical_sha256(),
        project_manifest_sha256=checked_bundle.project_manifest.manifest_sha256,
        file_collection_sha256=checked_collection.collection_sha256,
        git_state_sha256=checked_git.state_sha256,
        mapping_configuration_sha256=checked_configuration.mapping_sha256,
        mapping_result_sha256=checked_result.result_sha256,
        baseline_run_id=checked_configuration.baseline_run_id,
        collectors=collectors,
        items=items,
        metric_observations=checked_result.metric_observations,
    )
    state_sha256 = canonical_project_sha256(state)
    return ProjectSnapshot(
        snapshot_id=f"p3-snapshot-{state_sha256}",
        project_id=checked_bundle.project_id,
        source_bundle_id=checked_bundle.project_bundle_id,
        source_bundle_sha256=checked_bundle.canonical_sha256(),
        project_manifest_sha256=checked_bundle.project_manifest.manifest_sha256,
        file_collection_sha256=checked_collection.collection_sha256,
        git_state_sha256=checked_git.state_sha256,
        mapping_configuration_sha256=checked_configuration.mapping_sha256,
        mapping_result_sha256=checked_result.result_sha256,
        baseline_run_id=checked_configuration.baseline_run_id,
        collectors=collectors,
        items=items,
        metric_observations=checked_result.metric_observations,
        captured_at=captured_at,
        state_sha256=state_sha256,
        record_sha256=_record_digest(state_sha256=state_sha256, captured_at=captured_at),
    )
