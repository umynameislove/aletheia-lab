"""Transactional SQLite metadata and immutable project-artifact persistence."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal, TypeAlias, cast

from pydantic import BaseModel

from aletheia_lab.project.contracts import ProjectBundle
from aletheia_lab.project.identity import canonical_project_sha256, content_sha256
from aletheia_lab.project.lineage import ProjectLineageGraph
from aletheia_lab.project.regression import (
    ProjectEvidenceBundle,
    ProjectRegressionEvent,
    ProjectSnapshotComparison,
)
from aletheia_lab.project.snapshots import ProjectSnapshot

PROJECT_STORE_SCHEMA_VERSION: Final[int] = 2
PROJECT_STORE_FORMAT: Final[Literal["project-store/v1"]] = "project-store/v1"

StoredProjectModel: TypeAlias = (
    ProjectBundle
    | ProjectSnapshot
    | ProjectSnapshotComparison
    | ProjectRegressionEvent
    | ProjectEvidenceBundle
    | ProjectLineageGraph
)
RecordType = Literal[
    "project_bundle",
    "snapshot",
    "snapshot_comparison",
    "regression_event",
    "evidence_bundle",
    "lineage_graph",
]

_MIGRATIONS: Final[tuple[str, ...]] = (
    """
    CREATE TABLE IF NOT EXISTS migration_history (
        version INTEGER PRIMARY KEY,
        migration_sha256 TEXT NOT NULL CHECK(length(migration_sha256) = 64)
    );
    CREATE TABLE IF NOT EXISTS objects (
        sha256 TEXT PRIMARY KEY CHECK(length(sha256) = 64),
        byte_size INTEGER NOT NULL CHECK(byte_size >= 0),
        media_type TEXT NOT NULL,
        relative_path TEXT NOT NULL UNIQUE
    );
    CREATE TABLE IF NOT EXISTS records (
        record_id TEXT PRIMARY KEY,
        project_id TEXT NOT NULL,
        record_type TEXT NOT NULL,
        schema_version TEXT NOT NULL,
        canonical_sha256 TEXT NOT NULL CHECK(length(canonical_sha256) = 64),
        object_sha256 TEXT NOT NULL REFERENCES objects(sha256),
        UNIQUE(project_id, record_type, canonical_sha256)
    );
    CREATE INDEX IF NOT EXISTS records_project_type
        ON records(project_id, record_type, record_id);
    """,
    """
    CREATE TABLE IF NOT EXISTS lineage_nodes (
        graph_id TEXT NOT NULL,
        node_id TEXT NOT NULL,
        project_id TEXT NOT NULL,
        kind TEXT NOT NULL,
        source_id TEXT NOT NULL,
        source_sha256 TEXT NOT NULL CHECK(length(source_sha256) = 64),
        visibility TEXT NOT NULL,
        PRIMARY KEY(graph_id, node_id),
        FOREIGN KEY(graph_id) REFERENCES records(record_id) ON DELETE RESTRICT
    );
    CREATE TABLE IF NOT EXISTS lineage_edges (
        graph_id TEXT NOT NULL,
        edge_id TEXT NOT NULL,
        project_id TEXT NOT NULL,
        source_node_id TEXT NOT NULL,
        target_node_id TEXT NOT NULL,
        relationship TEXT NOT NULL,
        visibility TEXT NOT NULL,
        PRIMARY KEY(graph_id, edge_id),
        FOREIGN KEY(graph_id, source_node_id)
            REFERENCES lineage_nodes(graph_id, node_id) ON DELETE RESTRICT,
        FOREIGN KEY(graph_id, target_node_id)
            REFERENCES lineage_nodes(graph_id, node_id) ON DELETE RESTRICT
    );
    CREATE INDEX IF NOT EXISTS lineage_nodes_source
        ON lineage_nodes(project_id, source_id, kind);
    CREATE INDEX IF NOT EXISTS lineage_edges_endpoints
        ON lineage_edges(graph_id, source_node_id, target_node_id);
    """,
)


class ProjectStoreError(ValueError):
    """Raised when persistence cannot preserve integrity or atomic visibility."""


@dataclass(frozen=True)
class StoredRecord:
    record_id: str
    project_id: str
    record_type: RecordType
    schema_version: str
    canonical_sha256: str
    object_sha256: str


@dataclass(frozen=True)
class ImmutableObject:
    sha256: str
    byte_size: int
    media_type: str
    payload: bytes


def _json_bytes(payload: object) -> bytes:
    return (
        json.dumps(
            payload,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _migration_sha256(sql: str) -> str:
    return hashlib.sha256(sql.strip().encode("utf-8")).hexdigest()


def _model_identity(model: StoredProjectModel) -> tuple[RecordType, str, str, str]:
    if isinstance(model, ProjectBundle):
        return "project_bundle", model.project_bundle_id, model.project_id, model.schema_version
    if isinstance(model, ProjectSnapshot):
        return "snapshot", model.snapshot_id, model.project_id, model.schema_version
    if isinstance(model, ProjectSnapshotComparison):
        return "snapshot_comparison", model.comparison_id, model.project_id, model.schema_version
    if isinstance(model, ProjectRegressionEvent):
        return "regression_event", model.event_id, model.project_id, model.schema_version
    if isinstance(model, ProjectEvidenceBundle):
        return "evidence_bundle", model.evidence_bundle_id, model.project_id, model.schema_version
    if isinstance(model, ProjectLineageGraph):
        return "lineage_graph", model.graph_id, model.project_id, model.schema_version
    raise TypeError(f"unsupported project record model: {type(model).__name__}")


_MODEL_BY_TYPE: Final[dict[RecordType, type[BaseModel]]] = {
    "project_bundle": ProjectBundle,
    "snapshot": ProjectSnapshot,
    "snapshot_comparison": ProjectSnapshotComparison,
    "regression_event": ProjectRegressionEvent,
    "evidence_bundle": ProjectEvidenceBundle,
    "lineage_graph": ProjectLineageGraph,
}


class ProjectStore:
    """One local project store with explicit migrations and fail-closed reads."""

    def __init__(self, root: str | Path) -> None:
        supplied = Path(root)
        supplied.mkdir(parents=True, exist_ok=True)
        if supplied.is_symlink() or not supplied.is_dir():
            raise ProjectStoreError("project store root must be a real directory")
        self.root = supplied.resolve()
        self.object_root = self.root / "objects" / "sha256"
        self.object_root.mkdir(parents=True, exist_ok=True)
        if self.object_root.is_symlink() or not self.object_root.is_dir():
            raise ProjectStoreError("project object root must be a real directory")
        self.database_path = self.root / "project-store.sqlite3"
        if self.database_path.is_symlink():
            raise ProjectStoreError("project store database must not be a symlink")
        self._connection = sqlite3.connect(self.database_path)
        try:
            self._connection.row_factory = sqlite3.Row
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._connection.execute("PRAGMA journal_mode = WAL")
            self._connection.execute("PRAGMA synchronous = FULL")
            self._migrate()
            self.verify_integrity()
        except BaseException:
            self._connection.close()
            raise

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> ProjectStore:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @contextmanager
    def transaction(self) -> Iterator[None]:
        if self._connection.in_transaction:
            raise ProjectStoreError("nested project-store transactions are forbidden")
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self._connection.rollback()
            raise
        else:
            self._connection.commit()

    def _migrate(self) -> None:
        version = int(self._connection.execute("PRAGMA user_version").fetchone()[0])
        original_version = version
        if version > PROJECT_STORE_SCHEMA_VERSION:
            raise ProjectStoreError(
                f"project store schema {version} is newer than supported "
                f"{PROJECT_STORE_SCHEMA_VERSION}"
            )
        for target_version in range(version + 1, PROJECT_STORE_SCHEMA_VERSION + 1):
            sql = _MIGRATIONS[target_version - 1]
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                for statement in sql.split(";"):
                    if statement.strip():
                        self._connection.execute(statement)
                self._connection.execute(
                    "INSERT INTO migration_history(version, migration_sha256) VALUES (?, ?)",
                    (target_version, _migration_sha256(sql)),
                )
                self._connection.execute(f"PRAGMA user_version = {target_version}")
                self._connection.commit()
            except BaseException:
                self._connection.rollback()
                raise
        rows = self._connection.execute(
            "SELECT version, migration_sha256 FROM migration_history ORDER BY version"
        ).fetchall()
        expected = [
            (index, _migration_sha256(sql)) for index, sql in enumerate(_MIGRATIONS, start=1)
        ]
        if [(int(row[0]), str(row[1])) for row in rows] != expected:
            raise ProjectStoreError("project store migration history does not reconcile")
        if original_version < 2:
            graph_ids = [
                str(row[0])
                for row in self._connection.execute(
                    "SELECT record_id FROM records WHERE record_type = 'lineage_graph' "
                    "ORDER BY record_id"
                )
            ]
            if graph_ids:
                with self.transaction():
                    for graph_id in graph_ids:
                        graph = self.load(graph_id)
                        if not isinstance(graph, ProjectLineageGraph):
                            raise ProjectStoreError("lineage record did not load as a graph")
                        self._index_lineage(graph)

    def _object_path(self, digest: str) -> Path:
        if len(digest) != 64 or any(value not in "0123456789abcdef" for value in digest):
            raise ProjectStoreError("object digest must be lowercase SHA-256")
        return self.object_root / digest[:2] / digest[2:]

    def _write_object(self, payload: bytes, *, media_type: str) -> ImmutableObject:
        digest = content_sha256(payload)
        destination = self._object_path(digest)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.parent.is_symlink() or not destination.parent.is_dir():
            raise ProjectStoreError("content-addressed object bucket must be a real directory")
        if destination.exists():
            if destination.is_symlink() or destination.read_bytes() != payload:
                raise ProjectStoreError("content-addressed object conflicts with stored bytes")
        else:
            fd, temporary = tempfile.mkstemp(dir=destination.parent, prefix=".object-", suffix=".part")
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(payload)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, destination)
            except BaseException:
                Path(temporary).unlink(missing_ok=True)
                raise
        return ImmutableObject(
            sha256=digest,
            byte_size=len(payload),
            media_type=media_type,
            payload=payload,
        )

    def _index_object(self, value: ImmutableObject) -> None:
        relative = self._object_path(value.sha256).relative_to(self.root).as_posix()
        existing = self._connection.execute(
            "SELECT byte_size, media_type, relative_path FROM objects WHERE sha256 = ?",
            (value.sha256,),
        ).fetchone()
        expected = (value.byte_size, value.media_type, relative)
        if existing is not None:
            actual = (int(existing[0]), str(existing[1]), str(existing[2]))
            if actual != expected:
                raise ProjectStoreError("immutable object metadata conflicts with existing index")
            return
        self._connection.execute(
            "INSERT INTO objects(sha256, byte_size, media_type, relative_path) VALUES (?, ?, ?, ?)",
            (value.sha256, value.byte_size, value.media_type, relative),
        )

    def _insert_record(self, model: StoredProjectModel, value: ImmutableObject) -> StoredRecord:
        record_type, record_id, project_id, schema_version = _model_identity(model)
        canonical_sha = canonical_project_sha256(model.model_dump(mode="json"))
        existing = self._connection.execute(
            "SELECT project_id, record_type, schema_version, canonical_sha256, object_sha256 "
            "FROM records WHERE record_id = ?",
            (record_id,),
        ).fetchone()
        expected = (project_id, record_type, schema_version, canonical_sha, value.sha256)
        if existing is not None:
            actual = tuple(str(existing[index]) for index in range(5))
            if actual != expected:
                raise ProjectStoreError("immutable record ID conflicts with existing content")
        else:
            self._connection.execute(
                "INSERT INTO records(record_id, project_id, record_type, schema_version, "
                "canonical_sha256, object_sha256) VALUES (?, ?, ?, ?, ?, ?)",
                (record_id, project_id, record_type, schema_version, canonical_sha, value.sha256),
            )
        return StoredRecord(record_id, project_id, record_type, schema_version, canonical_sha, value.sha256)

    def _index_lineage(self, graph: ProjectLineageGraph) -> None:
        existing_nodes = self._connection.execute(
            "SELECT COUNT(*) FROM lineage_nodes WHERE graph_id = ?", (graph.graph_id,)
        ).fetchone()[0]
        existing_edges = self._connection.execute(
            "SELECT COUNT(*) FROM lineage_edges WHERE graph_id = ?", (graph.graph_id,)
        ).fetchone()[0]
        if existing_nodes or existing_edges:
            if existing_nodes != len(graph.nodes) or existing_edges != len(graph.edges):
                raise ProjectStoreError("lineage index conflicts with immutable graph")
            return
        self._connection.executemany(
            "INSERT INTO lineage_nodes(graph_id, node_id, project_id, kind, source_id, "
            "source_sha256, visibility) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    graph.graph_id,
                    node.node_id,
                    node.project_id,
                    node.kind,
                    node.source_id,
                    node.source_sha256,
                    node.visibility,
                )
                for node in graph.nodes
            ],
        )
        self._connection.executemany(
            "INSERT INTO lineage_edges(graph_id, edge_id, project_id, source_node_id, "
            "target_node_id, relationship, visibility) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    graph.graph_id,
                    edge.edge_id,
                    edge.project_id,
                    edge.source_node_id,
                    edge.target_node_id,
                    edge.relationship,
                    edge.visibility,
                )
                for edge in graph.edges
            ],
        )

    def persist(
        self,
        models: Iterable[StoredProjectModel],
        *,
        artifacts: Mapping[str, tuple[str, bytes]] | None = None,
    ) -> tuple[StoredRecord, ...]:
        """Atomically expose a generation after writing and verifying its objects.

        ``artifacts`` maps the declared SHA-256 to ``(media_type, bytes)``.  A
        mismatch is rejected before the SQLite transaction begins.
        """

        checked_models: list[StoredProjectModel] = []
        record_objects: list[ImmutableObject] = []
        for model in models:
            checked = type(model).model_validate(model.model_dump(mode="python", warnings=False))
            checked_models.append(checked)
            record_objects.append(
                self._write_object(
                    _json_bytes(checked.model_dump(mode="json")),
                    media_type="application/json",
                )
            )
        extra_objects: list[ImmutableObject] = []
        for declared_sha, (media_type, payload) in sorted((artifacts or {}).items()):
            if content_sha256(payload) != declared_sha:
                raise ProjectStoreError("artifact bytes do not match declared SHA-256")
            extra_objects.append(self._write_object(payload, media_type=media_type))
        records: list[StoredRecord] = []
        with self.transaction():
            for value in (*record_objects, *extra_objects):
                self._index_object(value)
            for model, value in zip(checked_models, record_objects, strict=True):
                records.append(self._insert_record(model, value))
            for model in checked_models:
                if isinstance(model, ProjectLineageGraph):
                    self._index_lineage(model)
        self.verify_integrity()
        return tuple(records)

    def load(self, record_id: str) -> StoredProjectModel:
        row = self._connection.execute(
            "SELECT record_type, canonical_sha256, object_sha256 FROM records WHERE record_id = ?",
            (record_id,),
        ).fetchone()
        if row is None:
            raise KeyError(record_id)
        record_type = str(row[0])
        if record_type not in _MODEL_BY_TYPE:
            raise ProjectStoreError("stored record type is unsupported")
        payload = self.read_object(str(row[2]))
        model = _MODEL_BY_TYPE[record_type].model_validate_json(payload)
        if canonical_project_sha256(model.model_dump(mode="json")) != str(row[1]):
            raise ProjectStoreError("stored record canonical hash mismatch")
        loaded_type, loaded_id, _, _ = _model_identity(model)  # type: ignore[arg-type]
        if loaded_type != record_type or loaded_id != record_id:
            raise ProjectStoreError("stored record identity differs from index")
        return model  # type: ignore[return-value]

    def read_object(self, digest: str) -> bytes:
        row = self._connection.execute(
            "SELECT byte_size, relative_path FROM objects WHERE sha256 = ?", (digest,)
        ).fetchone()
        if row is None:
            raise KeyError(digest)
        candidate = self.root / str(row[1])
        if candidate.is_symlink() or candidate.parent.is_symlink():
            raise ProjectStoreError("indexed object must not be a symlink")
        try:
            candidate.resolve().relative_to(self.root)
        except ValueError as exc:
            raise ProjectStoreError("indexed object path escapes project store") from exc
        payload = candidate.read_bytes()
        if len(payload) != int(row[0]) or content_sha256(payload) != digest:
            raise ProjectStoreError("stored object bytes fail integrity verification")
        return payload

    def list_records(
        self, project_id: str, record_type: RecordType | None = None
    ) -> tuple[StoredRecord, ...]:
        query = (
            "SELECT record_id, project_id, record_type, schema_version, canonical_sha256, "
            "object_sha256 FROM records WHERE project_id = ?"
        )
        parameters: tuple[object, ...] = (project_id,)
        if record_type is not None:
            query += " AND record_type = ?"
            parameters = (project_id, record_type)
        query += " ORDER BY record_type, record_id"
        records: list[StoredRecord] = []
        for row in self._connection.execute(query, parameters):
            records.append(
                StoredRecord(
                    record_id=str(row[0]),
                    project_id=str(row[1]),
                    record_type=cast(RecordType, str(row[2])),
                    schema_version=str(row[3]),
                    canonical_sha256=str(row[4]),
                    object_sha256=str(row[5]),
                )
            )
        return tuple(records)

    def export_index(self, project_id: str) -> bytes:
        """Export a byte-stable, path-free index suitable for publication tooling."""

        records = self.list_records(project_id)
        payload = {
            "schema_version": PROJECT_STORE_FORMAT,
            "project_id": project_id,
            "records": [value.__dict__ for value in records],
        }
        return _json_bytes(payload)

    def verify_integrity(self) -> None:
        if self._connection.execute("PRAGMA foreign_key_check").fetchall():
            raise ProjectStoreError("project store contains a foreign-key violation")
        result = self._connection.execute("PRAGMA integrity_check").fetchone()[0]
        if result != "ok":
            raise ProjectStoreError(f"SQLite integrity check failed: {result}")
        for row in self._connection.execute("SELECT sha256 FROM objects ORDER BY sha256"):
            self.read_object(str(row[0]))
        record_ids = [
            str(row[0])
            for row in self._connection.execute("SELECT record_id FROM records ORDER BY record_id")
        ]
        for record_id in record_ids:
            model = self.load(record_id)
            if not isinstance(model, ProjectLineageGraph):
                continue
            expected_nodes = [
                (
                    node.node_id,
                    node.project_id,
                    node.kind,
                    node.source_id,
                    node.source_sha256,
                    node.visibility,
                )
                for node in model.nodes
            ]
            actual_nodes = [
                tuple(map(str, row))
                for row in self._connection.execute(
                    "SELECT node_id, project_id, kind, source_id, source_sha256, visibility "
                    "FROM lineage_nodes WHERE graph_id = ? ORDER BY node_id",
                    (model.graph_id,),
                )
            ]
            expected_edges = [
                (
                    edge.edge_id,
                    edge.project_id,
                    edge.source_node_id,
                    edge.target_node_id,
                    edge.relationship,
                    edge.visibility,
                )
                for edge in model.edges
            ]
            actual_edges = [
                tuple(map(str, row))
                for row in self._connection.execute(
                    "SELECT edge_id, project_id, source_node_id, target_node_id, relationship, "
                    "visibility FROM lineage_edges WHERE graph_id = ? ORDER BY edge_id",
                    (model.graph_id,),
                )
            ]
            if actual_nodes != expected_nodes or actual_edges != expected_edges:
                raise ProjectStoreError("lineage index differs from immutable graph")
