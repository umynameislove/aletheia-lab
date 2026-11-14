"""Versioned, project-atomic import of an explicitly mapped, read-only local copy.

Local IDs and payloads are retained. This operator tool does not provision users,
workspaces or grants and never opens ProductStore, whose constructor migrates.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import stat
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from pydantic import SecretStr

from aletheia_lab.hosted.content_hashing import content_hasher
from aletheia_lab.product.lifecycle import (
    ProductLifecycleRecord,
    _canonical_utc_timestamp,
    _preview_lease_sha256,
)
from aletheia_lab.product.lifecycle_schema import _MIGRATIONS as PRODUCT_MIGRATIONS
from aletheia_lab.product.results import _view_from_record
from aletheia_lab.project.contracts import ProjectBundle
from aletheia_lab.project.identity import (
    PROJECT_ID_PATTERN,
    canonical_project_sha256,
)
from aletheia_lab.project.lineage import ProjectLineageGraph
from aletheia_lab.project.persistence import (
    _MIGRATIONS as PROJECT_MIGRATIONS,
)
from aletheia_lab.project.persistence import (
    _MODEL_BY_TYPE,
    StoredProjectModel,
    _json_bytes,
    _model_identity,
    parse_stored_project_model,
)
from aletheia_lab.project.regression import (
    ProjectEvidenceBundle,
    ProjectRegressionEvent,
    ProjectSnapshotComparison,
    ProjectSnapshotComparisonV2,
)
from aletheia_lab.project.snapshots import ProjectSnapshot, ProjectSnapshotV2

from .authorization import AuthorizationDenied, PostgresAuthorization
from .object_store import ObjectDescriptor
from .postgres_objects import PostgresObjects

IMPORT_VERSION = 1
Model = StoredProjectModel | ProductLifecycleRecord


class LegacyImportError(ValueError):
    """A safe failure; source paths, SQL parameters and payloads are not included."""


class LegacyImportConflict(LegacyImportError):
    """An existing checkpoint, identity or current pointer has different content."""


def _sha(payload: bytes) -> str:
    return content_hasher(payload).hexdigest()


def _file_sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _real_path(path: Path, *, directory: bool) -> Path:
    absolute = path.absolute()
    for current in (absolute, *absolute.parents):
        info = current.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 1024:
            raise LegacyImportError("Legacy source must not contain linked paths")
    if not (absolute.is_dir() if directory else absolute.is_file()):
        raise LegacyImportError("Legacy source copy is unavailable")
    return absolute


def model_metadata(model: Model) -> tuple[str, str, str | None, str, str]:
    if isinstance(model, ProductLifecycleRecord):
        checked = ProductLifecycleRecord.model_validate(model.model_dump(mode="python"))
        if checked.record_kind == "result":
            _view_from_record(checked)
        return (checked.record_kind, checked.record_id, checked.project_id,
                checked.schema_version, checked.canonical_sha256())
    kind, record_id, project_id, version = _model_identity(model)
    checked_project = type(model).model_validate(model.model_dump(mode="python"))
    return kind, record_id, project_id, version, canonical_project_sha256(checked_project.model_dump(mode="json"))


def record_parents(model: Model) -> tuple[tuple[str, str], ...]:
    """Canonical parent records; projections and metric item IDs are not records."""
    parents: list[tuple[str, str]] = []
    if isinstance(model, ProjectBundle):
        parents.extend(("item_parent_snapshot", item.parent_snapshot_id) for item in model.items if item.parent_snapshot_id is not None)
    elif isinstance(model, (ProjectSnapshot, ProjectSnapshotV2)):
        parents.append(("source_bundle", model.source_bundle_id))
    elif isinstance(model, (ProjectSnapshotComparison, ProjectSnapshotComparisonV2)):
        parents.extend((("before_snapshot", model.before_snapshot_id),
                        ("after_snapshot", model.after_snapshot_id)))
    elif isinstance(model, ProjectRegressionEvent):
        parents.extend((("comparison", model.comparison_id),
                        ("before_snapshot", model.before_snapshot_id),
                        ("after_snapshot", model.after_snapshot_id)))
    elif isinstance(model, ProjectEvidenceBundle):
        parents.append(("event", model.event_id))
        parents.extend((item.role, item.source_id) for item in model.items
                       if item.role in {"before_snapshot", "after_snapshot", "comparison"})
    elif isinstance(model, ProjectLineageGraph):
        parents.extend(("lineage_" + node.kind, node.source_id) for node in model.nodes
                       if node.kind in {"snapshot", "snapshot_comparison", "regression_candidate", "evidence_bundle"})
    elif isinstance(model, ProductLifecycleRecord):
        parents.extend(_lifecycle_parents(model))
    return tuple(sorted(set(parents)))


def validate_project_models(models: tuple[Model, ...], project_id: str) -> None:
    ids = {model_metadata(model)[1]: model for model in models}
    if len(ids) != len(models):
        raise LegacyImportError("Duplicate legacy record identity")
    if any(isinstance(model, ProductLifecycleRecord) and model.record_kind == "deletion" for model in models) and any(model_metadata(model)[0] not in {"deletion", "preview"} for model in models):
        raise LegacyImportError("Legacy deleted project still contains active canonical records")
    for model in models:
        if model_metadata(model)[2] not in (project_id, None):
            raise LegacyImportError("Legacy record is outside project")
        for _, parent_id in record_parents(model):
            if parent_id not in ids or model_metadata(ids[parent_id])[2] != project_id:
                raise LegacyImportError("Legacy canonical parent is missing or outside project")
        _validate_linked_model(model, ids)


@dataclass(frozen=True)
class ImportTarget:
    project_id: str
    workspace_id: str
    user_id: str

    def __post_init__(self) -> None:
        if re.fullmatch(PROJECT_ID_PATTERN, self.project_id) is None:
            raise LegacyImportError("Explicit canonical project mapping required")
        if any(not isinstance(value, str) or not value or len(value) > 128
               or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]*", value) is None
               for value in (self.workspace_id, self.user_id)):
            raise LegacyImportError("Explicit provisioned workspace and user mapping required")


@dataclass(frozen=True)
class LegacyProject:
    project_id: str
    models: tuple[Model, ...]
    artifacts: dict[str, tuple[str, bytes]]
    current_pointers: tuple[tuple[str, str], ...]
    confirmations: tuple[tuple[str, str], ...]
    preview_leases: tuple[tuple[str, str, str], ...]

    def semantic_export(self) -> bytes:
        return _json_bytes({
            "format": "k05-legacy-parity/v1", "project_id": self.project_id,
            "records": [model.model_dump(mode="json") for model in self.models],
            "artifacts": [(digest, media, len(payload)) for digest, (media, payload)
                          in sorted(self.artifacts.items())],
            "current_pointers": self.current_pointers,
            "confirmations": self.confirmations, "preview_leases": self.preview_leases,
        })

    @property
    def fingerprint(self) -> str:
        return _sha(self.semantic_export())


class LegacySource:
    """Read only a checkpointed SQLite copy and its content-addressed objects.

    Explicit preview mapping is required only for unconfirmed previews. Confirmed
    previews inherit their recorded project relation, never a guessed tenant.
    """
    def __init__(self, root: Path, *, preview_projects: dict[str, str] | None = None) -> None:
        self.root = _real_path(root, directory=True)
        database = _real_path(self.root / "project-store.sqlite3", directory=False)
        wal = Path(str(database) + "-wal")
        if wal.exists() and wal.stat().st_size:
            raise LegacyImportError("Checkpointed SQLite backup copy required; WAL is present")
        self._database = database
        self._bytes_read = 0
        self._source_digest = _file_sha(database)
        self.connection = sqlite3.connect(database.as_uri() + "?mode=ro&immutable=1", uri=True)
        self.connection.row_factory = sqlite3.Row
        try:
            self.connection.execute("PRAGMA query_only=ON")
            self.connection.execute("BEGIN")
            if self.connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise LegacyImportError("Legacy SQLite integrity check failed")
            if self.connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise LegacyImportError("Legacy foreign key integrity check failed")
            self._check_migrations("migration_history", PROJECT_MIGRATIONS)
            if self.connection.execute("PRAGMA user_version").fetchone()[0] != len(PROJECT_MIGRATIONS):
                raise LegacyImportError("Unsupported legacy project schema")
            self._has_product = self.connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='product_records'"
            ).fetchone() is not None
            self.preview_projects = dict(preview_projects or {})
            self._bind_confirmed_previews()
            for project in self.preview_projects.values():
                if re.fullmatch(PROJECT_ID_PATTERN, project) is None:
                    raise LegacyImportError("Canonical preview project mapping required")
        except BaseException:
            self.connection.close()
            raise

    def _bind_confirmed_previews(self) -> None:
        if self._has_product:
            self._check_migrations("product_migration_history", PRODUCT_MIGRATIONS)
            for row in self.connection.execute(
                "SELECT c.preview_id,r.project_id FROM product_confirmations c "
                "JOIN product_records r ON r.record_id=c.project_state_record_id"
            ):
                previous = self.preview_projects.setdefault(str(row[0]), str(row[1]))
                if previous != row[1]:
                    raise LegacyImportConflict("Preview mapping conflicts with confirmed relation")
            previews = {str(row[0]) for row in self.connection.execute(
                "SELECT record_id FROM product_records WHERE record_kind='preview'")}
            if previews != set(self.preview_projects):
                raise LegacyImportError("Every unconfirmed preview requires an explicit project mapping")

    def _check_migrations(self, table: str, migrations: tuple[str, ...]) -> None:
        queries = {
            "migration_history": "SELECT version,migration_sha256 FROM migration_history ORDER BY version",
            "product_migration_history": "SELECT version,migration_sha256 FROM product_migration_history ORDER BY version",
        }
        actual = [tuple(row) for row in self.connection.execute(queries[table])]
        expected = [(index, _sha(sql.strip().encode("utf-8")))
                    for index, sql in enumerate(migrations, 1)]
        if actual != expected:
            raise LegacyImportError("Unsupported or altered legacy migration history")

    def __enter__(self) -> LegacySource:
        return self

    def close(self) -> None:
        self.connection.close()
        if _file_sha(self._database) != self._source_digest:
            raise LegacyImportError("Legacy copy changed during import; checkpoint rejected")

    def __exit__(self, *_: object) -> None:
        self.close()

    def project_ids(self) -> tuple[str, ...]:
        projects = {str(row[0]) for row in self.connection.execute("SELECT DISTINCT project_id FROM records")}
        if self._has_product:
            projects.update(str(row[0]) for row in self.connection.execute(
                "SELECT DISTINCT project_id FROM product_records WHERE project_id IS NOT NULL"))
            projects.update(self.preview_projects.values())
        return tuple(sorted(projects))

    def _read_object(self, digest: str) -> tuple[str, bytes]:
        if re.fullmatch(r"[0-9a-f]{64}", digest) is None:
            raise LegacyImportError("Invalid legacy object digest")
        row = self.connection.execute(
            "SELECT byte_size,media_type,relative_path FROM objects WHERE sha256=?", (digest,)
        ).fetchone()
        expected_path = f"objects/sha256/{digest[:2]}/{digest[2:]}"
        if row is None or row[2] != expected_path:
            raise LegacyImportError("Legacy object reference does not reconcile")
        path = _real_path(self.root / expected_path, directory=False)
        # shortcut: offline importer bounds one object at 64 MiB; stream larger artifacts before raising this limit.
        if path.stat().st_size > 64 * 1024 * 1024:
            raise LegacyImportError("Legacy object exceeds importer memory budget")
        self._bytes_read += path.stat().st_size
        # shortcut: each atomic project import is limited to 256 MiB; add streamed checkpoints for larger projects.
        if self._bytes_read > 256 * 1024 * 1024:
            raise LegacyImportError("Legacy project exceeds importer memory budget")
        payload = path.read_bytes()
        if len(payload) != row[0] or _sha(payload) != digest:
            raise LegacyImportError("Legacy object hash or length differs")
        return str(row[1]), payload

    def _export_lifecycle(self, project_id: str, models: list[Model]) -> tuple[
        tuple[tuple[str, str], ...], tuple[tuple[str, str], ...], list[tuple[str, str, str]]]:
        leases: list[tuple[str, str, str]] = []
        previews = {key for key, value in self.preview_projects.items() if value == project_id}
        for row in self.connection.execute("SELECT * FROM product_records WHERE project_id=? "
                "OR record_id IN (SELECT value FROM json_each(?)) ORDER BY record_id",
                (project_id, json.dumps(sorted(previews)))):
            self._bytes_read += len(row["payload_json"].encode("utf-8"))
            if self._bytes_read > 256 * 1024 * 1024:
                raise LegacyImportError("Legacy project exceeds importer memory budget")
            record = ProductLifecycleRecord.model_validate({key: row[key] for key in (
                "record_id", "schema_version", "record_kind", "project_id", "snapshot_id", "parent_result_id", "payload_json")})
            if model_metadata(record)[-1] != row["canonical_sha256"]:
                raise LegacyImportError("Legacy lifecycle canonical hash differs")
            models.append(record)
        pointers = tuple(("project_state", str(row[0])) for row in self.connection.execute(
            "SELECT project_state_record_id FROM product_projects WHERE project_id=?", (project_id,)))
        confirmations = tuple((str(row[0]), str(row[1])) for row in self.connection.execute(
            "SELECT c.preview_id,c.project_state_record_id FROM product_confirmations c "
            "JOIN product_records r ON r.record_id=c.project_state_record_id WHERE r.project_id=? ORDER BY c.preview_id", (project_id,)))
        for preview in sorted(previews):
            row = self.connection.execute("SELECT leased_at,canonical_sha256 FROM product_preview_leases WHERE preview_id=?", (preview,)).fetchone()
            if row is not None:
                _canonical_utc_timestamp(str(row[0]))
                if _preview_lease_sha256(preview, row[0]) != row[1]:
                    raise LegacyImportError("Legacy preview lease hash differs")
                leases.append((preview, str(row[0]), str(row[1])))
        return pointers, confirmations, leases

    def _export_records(self, project_id: str) -> tuple[list[Model], dict[str, tuple[str, bytes]]]:
        models: list[Model] = []
        artifacts: dict[str, tuple[str, bytes]] = {}
        for row in self.connection.execute("SELECT * FROM records WHERE project_id=? ORDER BY record_id", (project_id,)):
            kind = row["record_type"]
            if kind not in _MODEL_BY_TYPE:
                raise LegacyImportError("Unsupported legacy record type")
            media, payload = self._read_object(row["object_sha256"])
            model = parse_stored_project_model(kind, payload)
            metadata = model_metadata(model)
            if metadata != (kind, row["record_id"], project_id, row["schema_version"], row["canonical_sha256"]) or media != "application/json":
                raise LegacyImportError("Legacy record identity or canonical hash differs")
            if payload != _json_bytes(model.model_dump(mode="json")):
                raise LegacyImportError("Legacy record does not have canonical bytes")
            models.append(model)
            if isinstance(model, ProjectBundle):
                for item in model.items:
                    artifact = self._read_object(item.artifact.sha256)
                    if (len(artifact[1]), artifact[0]) != (item.artifact.byte_size, item.artifact.media_type):
                        raise LegacyImportError("Legacy artifact metadata differs")
                    artifacts[item.artifact.sha256] = artifact
            if isinstance(model, ProjectLineageGraph):
                self._verify_lineage_index(model)
        return models, artifacts

    def export_project(self, project_id: str) -> LegacyProject:
        self._bytes_read = 0
        if project_id not in self.project_ids():
            raise LegacyImportError("Explicit legacy project is unavailable")
        models: list[Model] = []
        artifacts: dict[str, tuple[str, bytes]] = {}
        models, artifacts = self._export_records(project_id)
        pointers: tuple[tuple[str, str], ...] = ()
        confirmations: tuple[tuple[str, str], ...] = ()
        leases: list[tuple[str, str, str]] = []
        if self._has_product:
            pointers, confirmations, leases = self._export_lifecycle(project_id, models)
        ordered = tuple(sorted(models, key=lambda value: model_metadata(value)[1]))
        ids = {model_metadata(value)[1]: value for value in ordered}
        validate_project_models(ordered, project_id)
        for preview_id, state_id in confirmations:
            preview_record, state = ids.get(preview_id), ids.get(state_id)
            if not isinstance(preview_record, ProductLifecycleRecord) or preview_record.record_kind != "preview" or not isinstance(state, ProductLifecycleRecord) or state.record_kind != "project_state" or state.project_id != project_id or state.payload().get("preview_id") != preview_id:
                raise LegacyImportError("Legacy confirmation does not match canonical preview and state")
        for _, pointer in pointers:
            if pointer not in ids or model_metadata(ids[pointer])[0] != "project_state":
                raise LegacyImportError("Legacy current pointer differs from canonical record")
        if _file_sha(self._database) != self._source_digest:
            raise LegacyImportError("Legacy copy changed during import")
        return LegacyProject(project_id, ordered, artifacts, pointers, confirmations, tuple(leases))

    def _verify_lineage_index(self, graph: ProjectLineageGraph) -> None:
        nodes = [tuple(row) for row in self.connection.execute(
            "SELECT node_id,project_id,kind,source_id,source_sha256,visibility FROM lineage_nodes WHERE graph_id=? ORDER BY node_id", (graph.graph_id,))]
        edges = [tuple(row) for row in self.connection.execute(
            "SELECT edge_id,project_id,source_node_id,target_node_id,relationship,visibility FROM lineage_edges WHERE graph_id=? ORDER BY edge_id", (graph.graph_id,))]
        if nodes != [(node.node_id, node.project_id, node.kind, node.source_id, node.source_sha256, node.visibility) for node in graph.nodes] or edges != [(edge.edge_id, edge.project_id, edge.source_node_id, edge.target_node_id, edge.relationship, edge.visibility) for edge in graph.edges]:
            raise LegacyImportError("Legacy rebuildable lineage index differs from canonical graph")


@dataclass(frozen=True)
class ImportReport:
    import_id: str
    source_fingerprint: str
    mapping_fingerprint: str
    records: int
    artifacts: int
    disposition: str


class LegacyImporter:
    def __init__(self, pool: Any, objects: PostgresObjects) -> None:
        self._pool, self._objects = pool, objects
        self._authorization = PostgresAuthorization()

    async def _authorize(self, conn: Any, target: ImportTarget, credential_digest: SecretStr) -> None:
        if not isinstance(credential_digest, SecretStr):
            raise ValueError("Current authenticated session digest required")
        auth = await self._authorization.authorize(conn, credential_digest.get_secret_value(), target.workspace_id, "import")
        if auth.user_id != target.user_id:
            raise AuthorizationDenied()

    async def _stage_objects(self, source: LegacyProject, target: ImportTarget) -> tuple[dict[str, Any], dict[str, str]]:
        staged: dict[str, Any] = {}
        model_objects: dict[str, str] = {}
        for model in source.models:
            kind, record_id, _, schema_version, _ = model_metadata(model)
            obj = await self._objects.stage(target.workspace_id, _json_bytes(model.model_dump(mode="json")), media_type="application/json", schema_version=schema_version)
            staged[obj.descriptor.sha256] = obj
            model_objects[record_id] = obj.descriptor.sha256
        for digest, (media_type, payload) in sorted(source.artifacts.items()):
            if digest in staged:
                descriptor = staged[digest].descriptor
                if (descriptor.byte_size, descriptor.media_type) != (len(payload), media_type) or _sha(payload) != digest:
                    raise LegacyImportConflict("Shared canonical artifact descriptor differs")
                continue
            obj = await self._objects.stage(target.workspace_id, payload, media_type=media_type)
            if obj.descriptor.sha256 != digest:
                raise LegacyImportError("Legacy artifact hash changed before publication")
            staged[digest] = obj
        return staged, model_objects

    async def _enlist_records(self, conn: Any, source: LegacyProject, target: ImportTarget,
                              model_objects: dict[str, str], projections: dict[str, Any]) -> None:
        from .result_views import publish_projection

        for model in source.models:
            kind, record_id, project_id, schema_version, canonical_sha = model_metadata(model)
            snapshot_id = model.snapshot_id if isinstance(model, ProductLifecycleRecord) else None
            parent_result_id = model.parent_result_id if isinstance(model, ProductLifecycleRecord) else None
            values = (target.workspace_id, record_id, project_id, kind, schema_version, canonical_sha, model_objects[record_id], snapshot_id, parent_result_id)
            await conn.execute("INSERT INTO k05_records(workspace_id,record_id,project_id,record_type,schema_version,canonical_sha256,object_sha256,snapshot_id,parent_result_id) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING", values)
            actual = await (await conn.execute("SELECT workspace_id,record_id,project_id,record_type,schema_version,canonical_sha256,object_sha256,snapshot_id,parent_result_id FROM k05_records WHERE workspace_id=%s AND record_id=%s", (target.workspace_id, record_id))).fetchone()
            if actual != values:
                raise LegacyImportConflict("Immutable record conflicts with existing hosted identity")
            if isinstance(model, ProjectBundle):
                for item in model.items:
                    await conn.execute("INSERT INTO k05_object_references(workspace_id,record_id,sha256) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING", (target.workspace_id, record_id, item.artifact.sha256))
            for relation, parent in record_parents(model):
                await conn.execute("INSERT INTO k05_record_parents(workspace_id,project_id,record_id,relation,parent_id) VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING", (target.workspace_id, target.project_id, record_id, relation, parent))
            if record_id in projections:
                await publish_projection(conn, target.workspace_id, projections[record_id])

    async def _enlist_links(self, conn: Any, source: LegacyProject, target: ImportTarget) -> None:
        for name, record_id in source.current_pointers:
            await conn.execute("INSERT INTO k05_current_pointers(workspace_id,project_id,pointer_name,record_id) VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING", (target.workspace_id, target.project_id, name, record_id))
            actual = await (await conn.execute("SELECT record_id FROM k05_current_pointers WHERE workspace_id=%s AND project_id=%s AND pointer_name=%s", (target.workspace_id, target.project_id, name))).fetchone()
            if actual != (record_id,):
                raise LegacyImportConflict("Hosted current pointer advanced; explicit recovery required")
        for preview_id, record_id in source.confirmations:
            await conn.execute("INSERT INTO k05_confirmations(workspace_id,preview_id,project_id,project_state_record_id) VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING", (target.workspace_id, preview_id, target.project_id, record_id))
            actual = await (await conn.execute("SELECT project_id,project_state_record_id FROM k05_confirmations WHERE workspace_id=%s AND preview_id=%s", (target.workspace_id, preview_id))).fetchone()
            if actual != (target.project_id, record_id):
                raise LegacyImportConflict("Hosted confirmation differs")
        for preview_id, leased_at, digest in source.preview_leases:
            await conn.execute("INSERT INTO k05_preview_leases(workspace_id,preview_id,leased_at,canonical_sha256) VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING", (target.workspace_id, preview_id, leased_at, digest))
            actual = await (await conn.execute("SELECT leased_at,canonical_sha256 FROM k05_preview_leases WHERE workspace_id=%s AND preview_id=%s", (target.workspace_id, preview_id))).fetchone()
            if actual != (leased_at, digest):
                raise LegacyImportConflict("Hosted preview lease differs")

    async def import_project(self, source: LegacyProject, target: ImportTarget, *,
                             credential_digest: SecretStr, batch_id: str) -> ImportReport:
        if source.project_id != target.project_id or not isinstance(batch_id, str) or not 1 <= len(batch_id) <= 128:
            raise LegacyImportError("Explicit project and bounded batch identity required")
        validate_project_models(source.models, target.project_id)
        fingerprint = source.fingerprint
        mapping_sha = canonical_project_sha256(asdict(target))
        import_id = "k05-import-" + canonical_project_sha256({"batch": batch_id, "project": target.project_id})
        async with self._pool.connection() as conn:
            await self._authorize(conn, target, credential_digest)
            provenance = await (await conn.execute("SELECT provenance_kind FROM k05_projects WHERE workspace_id=%s AND project_id=%s", (target.workspace_id, target.project_id))).fetchone()
            if provenance is not None and provenance != ("local",):
                raise LegacyImportConflict("Legacy import requires local project provenance")
            await self._check_tombstone(conn, source, target)
            existing = await self._checkpoint(conn, target.workspace_id, import_id, fingerprint, mapping_sha)
        if existing:
            await self.verify_project(source, target, credential_digest=credential_digest)
            return ImportReport(import_id, fingerprint, mapping_sha, len(source.models), len(source.artifacts), "identical")
        staged, model_objects = await self._stage_objects(source, target)
        # Build only trusted result models before entering the publication transaction.
        # Local import avoids a module cycle with the canonical catalog decoder.
        from .result_views import prepare_projection

        projections = {model.record_id: prepare_projection(_view_from_record(model), model.canonical_sha256())
                       for model in source.models
                       if isinstance(model, ProductLifecycleRecord) and model.record_kind == "result"}
        async with self._pool.connection() as conn:
            await self._authorize(conn, target, credential_digest)
            await conn.execute("SET CONSTRAINTS ALL DEFERRED")
            await conn.execute("INSERT INTO k05_imports(workspace_id,import_id,source_fingerprint,mapping_fingerprint,version,state) VALUES (%s,%s,%s,%s,1,'running') ON CONFLICT DO NOTHING", (target.workspace_id, import_id, fingerprint, mapping_sha))
            row = await (await conn.execute("SELECT source_fingerprint,mapping_fingerprint,state FROM k05_imports WHERE workspace_id=%s AND import_id=%s FOR UPDATE", (target.workspace_id, import_id))).fetchone()
            if row is None or tuple(row[:2]) != (fingerprint, mapping_sha):
                raise LegacyImportConflict("Import checkpoint conflicts with source or mapping")
            disposition = "identical" if row[2] == "complete" else "created"
            await conn.execute("INSERT INTO k05_projects(workspace_id,project_id,creator_id) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING", (target.workspace_id, target.project_id, target.user_id))
            owner = await (await conn.execute("SELECT creator_id,provenance_kind FROM k05_projects WHERE workspace_id=%s AND project_id=%s FOR UPDATE", (target.workspace_id, target.project_id))).fetchone()
            if owner is None or owner[0] != target.user_id:
                raise LegacyImportConflict("Project already exists under another explicit owner")
            if owner[1] != "local":
                raise LegacyImportConflict("Legacy import requires local project provenance")
            await self._check_tombstone(conn, source, target)
            for obj in sorted(staged.values(), key=lambda value: value.descriptor.sha256):
                await self._objects.ready(conn, target.workspace_id, obj)
            await self._enlist_records(conn, source, target, model_objects, projections)
            await self._enlist_links(conn, source, target)
            for obj in staged.values():
                await self._objects.finish(conn, target.workspace_id, obj)
            await self._authorize(conn, target, credential_digest)
            await conn.execute("UPDATE k05_imports SET state='complete',completed_at=clock_timestamp() WHERE workspace_id=%s AND import_id=%s", (target.workspace_id, import_id))
        await self.verify_project(source, target, credential_digest=credential_digest)
        return ImportReport(import_id, fingerprint, mapping_sha, len(source.models), len(source.artifacts), disposition)

    async def _check_tombstone(self, conn: Any, source: LegacyProject, target: ImportTarget) -> None:
        cursor = await conn.execute("SELECT record_id FROM k05_records WHERE workspace_id=%s AND project_id=%s AND record_type='deletion'", (target.workspace_id, target.project_id))
        deleted = []
        while (row := await cursor.fetchone()) is not None:
            deleted.append(row[0])
        source_deleted = [model.record_id for model in source.models if isinstance(model, ProductLifecycleRecord) and model.record_kind == "deletion"]
        if deleted and sorted(deleted) != sorted(source_deleted):
            raise LegacyImportConflict("Hosted tombstone rejects older legacy content")

    async def _checkpoint(self, conn: Any, workspace_id: str, import_id: str, source_sha: str, mapping_sha: str) -> bool:
        row = await (await conn.execute("SELECT source_fingerprint,mapping_fingerprint,state FROM k05_imports WHERE workspace_id=%s AND import_id=%s", (workspace_id, import_id))).fetchone()
        if row is not None and tuple(row[:2]) != (source_sha, mapping_sha):
            raise LegacyImportConflict("Import checkpoint conflicts with source or mapping")
        return row is not None and row[2] == "complete"

    async def _verify_relations(self, conn: Any, source: LegacyProject, target: ImportTarget) -> None:
        cursor = await conn.execute("SELECT pointer_name,record_id FROM k05_current_pointers WHERE workspace_id=%s AND project_id=%s ORDER BY pointer_name", (target.workspace_id, target.project_id))
        pointers = []
        while (row := await cursor.fetchone()) is not None:
            pointers.append(row)
        if tuple(pointers) != tuple(sorted(source.current_pointers)):
            raise LegacyImportConflict("Hosted current pointers differ from import parity")
        cursor = await conn.execute("SELECT record_id,relation,parent_id FROM k05_record_parents WHERE workspace_id=%s AND project_id=%s ORDER BY record_id,relation,parent_id", (target.workspace_id, target.project_id))
        parents = []
        while (row := await cursor.fetchone()) is not None:
            parents.append(row)
        expected = sorted((model_metadata(model)[1], relation, parent) for model in source.models for relation, parent in record_parents(model))
        if parents != expected:
            raise LegacyImportConflict("Hosted parent projection differs from import parity")
        cursor = await conn.execute("SELECT preview_id,project_state_record_id FROM k05_confirmations WHERE workspace_id=%s AND project_id=%s ORDER BY preview_id", (target.workspace_id, target.project_id))
        confirmations = []
        while (row := await cursor.fetchone()) is not None:
            confirmations.append(row)
        if tuple(confirmations) != source.confirmations:
            raise LegacyImportConflict("Hosted confirmations differ from import parity")
        for preview, leased_at, digest in source.preview_leases:
            row = await (await conn.execute("SELECT leased_at,canonical_sha256 FROM k05_preview_leases WHERE workspace_id=%s AND preview_id=%s", (target.workspace_id, preview))).fetchone()
            if row != (leased_at, digest):
                raise LegacyImportConflict("Hosted preview lease differs from import parity")

    async def verify_project(self, source: LegacyProject, target: ImportTarget, *,
                             credential_digest: SecretStr) -> str:
        """Recheck exact installed IDs, parents, pointers and every object after commit.

        A completed checkpoint alone is not evidence of present-day parity.
        """
        objects: dict[str, tuple[ObjectDescriptor, bytes]] = {}
        async with self._pool.connection() as conn:
            await self._authorize(conn, target, credential_digest)
            count = await (await conn.execute("SELECT count(*) FROM k05_records WHERE workspace_id=%s AND project_id=%s", (target.workspace_id, target.project_id))).fetchone()
            if count != (sum(model_metadata(model)[2] is not None for model in source.models),):
                raise LegacyImportConflict("Hosted record count differs from import parity")
            for model in source.models:
                kind, record_id, project_id, schema_version, canonical_sha = model_metadata(model)
                payload = _json_bytes(model.model_dump(mode="json"))
                digest = _sha(payload)
                row = await (await conn.execute("SELECT r.project_id,r.record_type,r.schema_version,r.canonical_sha256,r.object_sha256,r.snapshot_id,r.parent_result_id,o.byte_size,o.media_type,o.schema_version FROM k05_records r JOIN k05_objects o ON o.workspace_id=r.workspace_id AND o.sha256=r.object_sha256 WHERE r.workspace_id=%s AND r.record_id=%s", (target.workspace_id, record_id))).fetchone()
                snapshot = model.snapshot_id if isinstance(model, ProductLifecycleRecord) else None
                parent_result = model.parent_result_id if isinstance(model, ProductLifecycleRecord) else None
                if row is None or tuple(row[:9]) != (project_id, kind, schema_version, canonical_sha, digest, snapshot, parent_result, len(payload), "application/json") or row[9] not in (None, schema_version):
                    raise LegacyImportConflict("Hosted immutable metadata differs from import parity")
                objects[digest] = (ObjectDescriptor(digest, len(payload), "application/json", schema_version), payload)
            await self._verify_relations(conn, source, target)
            cursor = await conn.execute("SELECT ref.record_id,ref.sha256 FROM k05_object_references ref JOIN k05_records r ON r.workspace_id=ref.workspace_id AND r.record_id=ref.record_id WHERE ref.workspace_id=%s AND r.project_id=%s ORDER BY ref.record_id,ref.sha256", (target.workspace_id, target.project_id))
            references = []
            while (row := await cursor.fetchone()) is not None:
                references.append(row)
            expected_refs = sorted(set((model.project_bundle_id, item.artifact.sha256) for model in source.models if isinstance(model, ProjectBundle) for item in model.items))
            if references != expected_refs:
                raise LegacyImportConflict("Hosted artifact references differ from import parity")
            for digest, (media_type, payload) in source.artifacts.items():
                row = await (await conn.execute("SELECT byte_size,media_type,schema_version FROM k05_objects WHERE workspace_id=%s AND sha256=%s", (target.workspace_id, digest))).fetchone()
                if row is None or tuple(row[:2]) != (len(payload), media_type):
                    raise LegacyImportConflict("Hosted artifact metadata differs from import parity")
                objects[digest] = (ObjectDescriptor(digest, len(payload), media_type), payload)
        for descriptor, expected_bytes in objects.values():
            if await self._objects.read(target.workspace_id, descriptor) != expected_bytes:
                raise LegacyImportConflict("Hosted canonical bytes differ from import parity")
        return source.fingerprint
def _lifecycle_parents(model: ProductLifecycleRecord) -> tuple[tuple[str, str], ...]:
    parents: list[tuple[str, str]] = []
    if model.snapshot_id is not None:
        parents.append(("snapshot", model.snapshot_id))
    if model.parent_result_id is not None:
        parents.append(("parent_result", model.parent_result_id))
    if model.record_kind == "project_state":
        parents.extend((name, value) for name, value in model.payload().items()
                       if name in {"project_bundle_id", "snapshot_id", "previous_snapshot_id",
                                   "comparison_id", "event_id", "evidence_bundle_id", "lineage_graph_id"}
                       and isinstance(value, str))
    return tuple(parents)

def _validate_linked_model(model: Model, ids: dict[str, Model]) -> None:
    if isinstance(model, (ProjectSnapshot, ProjectSnapshotV2)):
        source_bundle = ids[model.source_bundle_id]
        if not isinstance(source_bundle, ProjectBundle) or source_bundle.canonical_sha256() != model.source_bundle_sha256:
            raise LegacyImportError("Legacy snapshot source bundle hash differs")
    elif isinstance(model, (ProjectSnapshotComparison, ProjectSnapshotComparisonV2)):
        before, after = ids[model.before_snapshot_id], ids[model.after_snapshot_id]
        if not isinstance(before, (ProjectSnapshot, ProjectSnapshotV2)) or not isinstance(after, (ProjectSnapshot, ProjectSnapshotV2)) or (before.record_sha256, after.record_sha256) != (model.before_record_sha256, model.after_record_sha256):
            raise LegacyImportError("Legacy comparison snapshot hashes differ")
    elif isinstance(model, ProjectRegressionEvent):
        comparison = ids[model.comparison_id]
        if not isinstance(comparison, (ProjectSnapshotComparison, ProjectSnapshotComparisonV2)) or (comparison.comparison_sha256, comparison.before_snapshot_id, comparison.after_snapshot_id) != (model.comparison_sha256, model.before_snapshot_id, model.after_snapshot_id):
            raise LegacyImportError("Legacy event comparison differs")
    elif isinstance(model, ProjectEvidenceBundle):
        _validate_evidence_parents(model, ids)
    elif isinstance(model, ProjectLineageGraph):
        _validate_lineage_parents(model, ids)
    elif isinstance(model, ProductLifecycleRecord) and model.record_kind == "project_state":
        _validate_project_state(model, ids)


def _validate_lineage_parents(model: ProjectLineageGraph, ids: dict[str, Model]) -> None:
    for node in model.nodes:
        lineage_parent = ids.get(node.source_id)
        if node.kind == "snapshot":
            digest = lineage_parent.record_sha256 if isinstance(lineage_parent, (ProjectSnapshot, ProjectSnapshotV2)) else None
        elif node.kind == "snapshot_comparison":
            digest = lineage_parent.comparison_sha256 if isinstance(lineage_parent, (ProjectSnapshotComparison, ProjectSnapshotComparisonV2)) else None
        elif node.kind == "regression_candidate":
            digest = lineage_parent.event_sha256 if isinstance(lineage_parent, ProjectRegressionEvent) else None
        elif node.kind == "evidence_bundle":
            digest = lineage_parent.bundle_sha256 if isinstance(lineage_parent, ProjectEvidenceBundle) else None
        else:
            continue
        if digest is None or digest != node.source_sha256:
            raise LegacyImportError("Legacy lineage canonical parent hash differs")


def _validate_project_state(model: ProductLifecycleRecord, ids: dict[str, Model]) -> None:
    payload = model.payload()
    state_snapshot_id, state_bundle_id = payload.get("snapshot_id"), payload.get("project_bundle_id")
    if ("snapshot_id" in payload and not isinstance(state_snapshot_id, str)) or ("project_bundle_id" in payload and not isinstance(state_bundle_id, str)):
        raise LegacyImportError("Legacy project state reference is invalid")
    snapshot = ids.get(state_snapshot_id) if isinstance(state_snapshot_id, str) else None
    bundle = ids.get(state_bundle_id) if isinstance(state_bundle_id, str) else None
    if snapshot is not None and (not isinstance(snapshot, (ProjectSnapshot, ProjectSnapshotV2)) or bundle is not None and snapshot.source_bundle_id != model_metadata(bundle)[1]):
        raise LegacyImportError("Legacy project state snapshot differs")

def _validate_evidence_parents(model: ProjectEvidenceBundle, ids: dict[str, Model]) -> None:
    for item in model.items:
        evidence_parent = ids.get(item.source_id)
        digest = evidence_parent.state_sha256 if isinstance(evidence_parent, (ProjectSnapshot, ProjectSnapshotV2)) else evidence_parent.comparison_sha256 if isinstance(evidence_parent, (ProjectSnapshotComparison, ProjectSnapshotComparisonV2)) else evidence_parent.event_sha256 if isinstance(evidence_parent, ProjectRegressionEvent) else None
        if digest is not None and digest != item.source_sha256:
            raise LegacyImportError("Legacy evidence parent hash differs")

