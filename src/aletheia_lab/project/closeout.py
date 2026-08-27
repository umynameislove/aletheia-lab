"""Fail-closed reconciliation for one persisted P3 regression generation."""

from __future__ import annotations

from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from aletheia_lab.project.contracts import ProjectBundle
from aletheia_lab.project.identity import (
    PROJECT_ID_PATTERN,
    SHA256_PATTERN,
    canonical_project_sha256,
    content_sha256,
)
from aletheia_lab.project.lineage import (
    ProjectLineageGraph,
    project_lineage,
    project_lineage_table,
)
from aletheia_lab.project.persistence import (
    PROJECT_STORE_SCHEMA_VERSION,
    ProjectStore,
    RecordType,
    StoredRecord,
)
from aletheia_lab.project.regression import (
    ProjectEvidenceBundle,
    ProjectRegressionEvent,
    ProjectSnapshotComparison,
)
from aletheia_lab.project.snapshots import ProjectSnapshot

P3_CLOSEOUT_SCHEMA_VERSION: Final[Literal["p3-closeout/v1"]] = "p3-closeout/v1"
_CLOSEOUT_ID_PATTERN: Final[str] = r"^p3-closeout-[0-9a-f]{64}$"
_REQUIRED_TYPES: Final[tuple[RecordType, ...]] = (
    "project_bundle",
    "project_bundle",
    "snapshot",
    "snapshot",
    "snapshot_comparison",
    "regression_event",
    "evidence_bundle",
    "lineage_graph",
)
CloseoutRecordRole = Literal[
    "before_bundle",
    "after_bundle",
    "before_snapshot",
    "after_snapshot",
    "comparison",
    "event",
    "evidence_bundle",
    "lineage_graph",
]
_REQUIRED_ROLES: Final[tuple[CloseoutRecordRole, ...]] = (
    "before_bundle",
    "after_bundle",
    "before_snapshot",
    "after_snapshot",
    "comparison",
    "event",
    "evidence_bundle",
    "lineage_graph",
)
_TYPE_BY_ROLE: Final[dict[CloseoutRecordRole, RecordType]] = dict(
    zip(_REQUIRED_ROLES, _REQUIRED_TYPES, strict=True)
)


class ProjectCloseoutError(ValueError):
    """Raised when a persisted P3 generation cannot be closed consistently."""


class _StrictFrozenModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, revalidate_instances="always"
    )


class ProjectCloseoutRecord(_StrictFrozenModel):
    """Payload-free binding to one immutable record selected for closeout."""

    role: CloseoutRecordRole
    record_id: str = Field(min_length=1, max_length=128)
    record_type: RecordType
    schema_version: str = Field(min_length=1, max_length=128)
    canonical_sha256: str = Field(pattern=SHA256_PATTERN)
    object_sha256: str = Field(pattern=SHA256_PATTERN)


class ProjectCloseoutMigration(_StrictFrozenModel):
    """One applied, implementation-bound project-store migration."""

    version: int = Field(ge=1)
    migration_sha256: str = Field(pattern=SHA256_PATTERN)


class ProjectCloseoutProjection(_StrictFrozenModel):
    """Deterministic visibility projection used by dashboard consumers."""

    visibility: Literal["public", "diagnosis", "evaluator"]
    graph_id: str = Field(pattern=r"^p3-lineage-graph-[0-9a-f]{64}$")
    graph_sha256: str = Field(pattern=SHA256_PATTERN)
    table_sha256: str = Field(pattern=SHA256_PATTERN)
    node_count: int = Field(ge=1)
    edge_count: int = Field(ge=0)


def _closeout_payload(
    *,
    project_id: str,
    store_schema_version: int,
    records: tuple[ProjectCloseoutRecord, ...],
    migrations: tuple[ProjectCloseoutMigration, ...],
    export_index_sha256: str,
    projections: tuple[ProjectCloseoutProjection, ...],
) -> dict[str, object]:
    return {
        "schema_version": P3_CLOSEOUT_SCHEMA_VERSION,
        "project_id": project_id,
        "store_schema_version": store_schema_version,
        "records": [value.model_dump(mode="json") for value in records],
        "migrations": [value.model_dump(mode="json") for value in migrations],
        "export_index_sha256": export_index_sha256,
        "projections": [value.model_dump(mode="json") for value in projections],
        "causal_status": "unverified",
        "status": "p3_closeout_pass",
    }


class ProjectCloseoutReceipt(_StrictFrozenModel):
    """Machine-readable proof that one P3 generation reconciles end to end."""

    schema_version: Literal["p3-closeout/v1"] = P3_CLOSEOUT_SCHEMA_VERSION
    closeout_id: str = Field(pattern=_CLOSEOUT_ID_PATTERN)
    project_id: str = Field(pattern=PROJECT_ID_PATTERN)
    store_schema_version: int = Field(ge=1)
    records: tuple[ProjectCloseoutRecord, ...]
    migrations: tuple[ProjectCloseoutMigration, ...]
    export_index_sha256: str = Field(pattern=SHA256_PATTERN)
    projections: tuple[ProjectCloseoutProjection, ...]
    causal_status: Literal["unverified"] = "unverified"
    status: Literal["p3_closeout_pass"] = "p3_closeout_pass"
    closeout_sha256: str = Field(pattern=SHA256_PATTERN)

    @field_validator("records")
    @classmethod
    def _canonical_records(
        cls, values: tuple[ProjectCloseoutRecord, ...]
    ) -> tuple[ProjectCloseoutRecord, ...]:
        if len(values) != 8 or {value.role for value in values} != set(_REQUIRED_ROLES):
            raise ValueError("P3 closeout requires eight unique generation roles")
        ordered = tuple(sorted(values, key=lambda value: _REQUIRED_ROLES.index(value.role)))
        if any(value.record_type != _TYPE_BY_ROLE[value.role] for value in ordered):
            raise ValueError("P3 closeout record census is incomplete")
        return ordered

    @field_validator("migrations")
    @classmethod
    def _canonical_migrations(
        cls, values: tuple[ProjectCloseoutMigration, ...]
    ) -> tuple[ProjectCloseoutMigration, ...]:
        ordered = tuple(sorted(values, key=lambda value: value.version))
        versions = tuple(value.version for value in ordered)
        if not versions or versions != tuple(range(1, max(versions) + 1)):
            raise ValueError("P3 closeout migration census is incomplete")
        return ordered

    @field_validator("projections")
    @classmethod
    def _canonical_projections(
        cls, values: tuple[ProjectCloseoutProjection, ...]
    ) -> tuple[ProjectCloseoutProjection, ...]:
        order = {"public": 0, "diagnosis": 1, "evaluator": 2}
        ordered = tuple(sorted(values, key=lambda value: order[value.visibility]))
        if tuple(value.visibility for value in ordered) != (
            "public",
            "diagnosis",
            "evaluator",
        ):
            raise ValueError("P3 closeout requires all visibility projections")
        if any(
            old.node_count > new.node_count or old.edge_count > new.edge_count
            for old, new in zip(ordered[:-1], ordered[1:], strict=True)
        ):
            raise ValueError("P3 closeout visibility projections are not monotonic")
        return ordered

    @model_validator(mode="after")
    def _identity_reconciles(self) -> ProjectCloseoutReceipt:
        if tuple(value.version for value in self.migrations) != tuple(
            range(1, self.store_schema_version + 1)
        ):
            raise ValueError("P3 closeout migrations do not match its store schema")
        payload = _closeout_payload(
            project_id=self.project_id,
            store_schema_version=self.store_schema_version,
            records=self.records,
            migrations=self.migrations,
            export_index_sha256=self.export_index_sha256,
            projections=self.projections,
        )
        digest = canonical_project_sha256(payload)
        if self.closeout_sha256 != digest or self.closeout_id != f"p3-closeout-{digest}":
            raise ValueError("P3 closeout identity does not reconcile")
        return self


def _record(store: ProjectStore, project_id: str, record_id: str) -> StoredRecord:
    matches = tuple(
        value for value in store.list_records(project_id) if value.record_id == record_id
    )
    if len(matches) != 1:
        raise ProjectCloseoutError(f"closeout record is missing or ambiguous: {record_id}")
    return matches[0]


def _bound_record(value: StoredRecord, role: CloseoutRecordRole) -> ProjectCloseoutRecord:
    return ProjectCloseoutRecord(
        role=role,
        record_id=value.record_id,
        record_type=value.record_type,
        schema_version=value.schema_version,
        canonical_sha256=value.canonical_sha256,
        object_sha256=value.object_sha256,
    )


def build_project_closeout(
    store: ProjectStore,
    *,
    project_id: str,
    before_bundle_id: str,
    after_bundle_id: str,
    before_snapshot_id: str,
    after_snapshot_id: str,
    comparison_id: str,
    event_id: str,
    evidence_bundle_id: str,
    lineage_graph_id: str,
) -> ProjectCloseoutReceipt:
    """Validate and close one exact persisted import-to-lineage generation."""

    store.verify_integrity()
    selected_ids = (
        before_bundle_id,
        after_bundle_id,
        before_snapshot_id,
        after_snapshot_id,
        comparison_id,
        event_id,
        evidence_bundle_id,
        lineage_graph_id,
    )
    stored = tuple(_record(store, project_id, value) for value in selected_ids)
    if tuple(value.record_type for value in stored) != _REQUIRED_TYPES:
        raise ProjectCloseoutError("closeout record types do not match their declared roles")

    before_bundle = store.load(before_bundle_id)
    after_bundle = store.load(after_bundle_id)
    before = store.load(before_snapshot_id)
    after = store.load(after_snapshot_id)
    comparison = store.load(comparison_id)
    event = store.load(event_id)
    evidence = store.load(evidence_bundle_id)
    graph = store.load(lineage_graph_id)
    if not (
        isinstance(before_bundle, ProjectBundle)
        and isinstance(after_bundle, ProjectBundle)
        and isinstance(before, ProjectSnapshot)
        and isinstance(after, ProjectSnapshot)
        and isinstance(comparison, ProjectSnapshotComparison)
        and isinstance(event, ProjectRegressionEvent)
        and isinstance(evidence, ProjectEvidenceBundle)
        and isinstance(graph, ProjectLineageGraph)
    ):
        raise ProjectCloseoutError("closeout generation contains an unexpected model type")
    if not (
        before_bundle.project_id
        == after_bundle.project_id
        == before.project_id
        == after.project_id
        == comparison.project_id
        == event.project_id
        == evidence.project_id
        == graph.project_id
        == project_id
    ):
        raise ProjectCloseoutError("closeout generation crosses project boundaries")
    if not (
        before.source_bundle_id == before_bundle.project_bundle_id
        and after.source_bundle_id == after_bundle.project_bundle_id
        and comparison.before_snapshot_id == before.snapshot_id
        and comparison.after_snapshot_id == after.snapshot_id
        and comparison.before_record_sha256 == before.record_sha256
        and comparison.after_record_sha256 == after.record_sha256
        and event.comparison_id == comparison.comparison_id
        and event.comparison_sha256 == comparison.comparison_sha256
        and event.before_snapshot_id == before.snapshot_id
        and event.after_snapshot_id == after.snapshot_id
        and evidence.event_id == event.event_id
    ):
        raise ProjectCloseoutError("closeout generation relations do not reconcile")
    if comparison.status != "changed" or event.causal_status != "unverified":
        raise ProjectCloseoutError("closeout requires a changed, causally unverified candidate")
    graph_sources = {value.source_id for value in graph.nodes}
    required_sources = {
        before.snapshot_id,
        after.snapshot_id,
        comparison.comparison_id,
        event.event_id,
        evidence.evidence_bundle_id,
    }
    if not required_sources.issubset(graph_sources):
        raise ProjectCloseoutError("lineage graph omits a selected generation record")

    projections: list[ProjectCloseoutProjection] = []
    for visibility in ("public", "diagnosis", "evaluator"):
        projected = project_lineage(graph, visibility)
        table = project_lineage_table(graph, visibility)
        node_ids = {value.node_id for value in projected.nodes}
        if any(
            value.source_node_id not in node_ids or value.target_node_id not in node_ids
            for value in projected.edges
        ):
            raise ProjectCloseoutError("lineage projection contains a dangling edge")
        projections.append(
            ProjectCloseoutProjection(
                visibility=visibility,
                graph_id=projected.graph_id,
                graph_sha256=projected.graph_sha256,
                table_sha256=table.table_sha256,
                node_count=len(projected.nodes),
                edge_count=len(projected.edges),
            )
        )

    migration_rows = store._connection.execute(
        "SELECT version, migration_sha256 FROM migration_history ORDER BY version"
    ).fetchall()
    migrations = tuple(
        ProjectCloseoutMigration(version=int(row[0]), migration_sha256=str(row[1]))
        for row in migration_rows
    )
    records = tuple(
        sorted(
            (
                _bound_record(value, role)
                for value, role in zip(stored, _REQUIRED_ROLES, strict=True)
            ),
            key=lambda value: _REQUIRED_ROLES.index(value.role),
        )
    )
    export_sha = content_sha256(store.export_index(project_id))
    ordered_projections = tuple(projections)
    payload = _closeout_payload(
        project_id=project_id,
        store_schema_version=PROJECT_STORE_SCHEMA_VERSION,
        records=records,
        migrations=migrations,
        export_index_sha256=export_sha,
        projections=ordered_projections,
    )
    digest = canonical_project_sha256(payload)
    return ProjectCloseoutReceipt(
        closeout_id=f"p3-closeout-{digest}",
        project_id=project_id,
        store_schema_version=PROJECT_STORE_SCHEMA_VERSION,
        records=records,
        migrations=migrations,
        export_index_sha256=export_sha,
        projections=ordered_projections,
        closeout_sha256=digest,
    )
