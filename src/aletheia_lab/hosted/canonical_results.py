"""Connection-enlisted canonical P6 derived-result publication.

Prepare outside every database transaction. Enlist requires current session
Authorization obtained on the SAME caller connection, before taking a project
SHARE lock. The caller owns job fencing, receipt/audit/terminal writes and commit;
any later failure must roll back this transaction. This is not an HTTP or queue
adapter, and PreparedResult/Authorization must never be built from client fields.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from aletheia_lab.hosted_auth.postgres_sessions import Connection
from aletheia_lab.product.lifecycle import ProductLifecycleRecord
from aletheia_lab.product.results import _validate_derived_result, _view_from_record
from aletheia_lab.project.persistence import _json_bytes

from .async_work import run_blocking
from .authorization import Authorization, AuthorizationDenied, PostgresAuthorization
from .legacy_import import record_parents
from .native_upload_records import (
    AnyUploadResultRecord,
    NativeUploadModel,
    UploadResultRecord,
    UploadResultRecordV2,
    build_initial_upload_result,
    native_metadata,
    native_parents,
    parse_upload_result_record,
)
from .object_store import ObjectDescriptor
from .postgres_catalog import CatalogIntegrityError, RecordIndex, _all, _index
from .postgres_objects import PostgresObjects, StagedObject
from .result_views import PreparedProjection, prepare_projection, publish_projection
from .upload_snapshot import (
    AnyUploadSnapshot,
    UploadSnapshot,
    UploadSnapshotV2,
    parse_upload_snapshot,
)
from .workspace_management import WorkspaceConflict, _revision

ResultModel = ProductLifecycleRecord | AnyUploadResultRecord
RecordModel = ProductLifecycleRecord | NativeUploadModel


class CanonicalResultRejected(ValueError):
    def __init__(self) -> None:
        super().__init__("The canonical result does not match its parent scope.")


@dataclass(frozen=True)
class PreparedResult:
    workspace_id: str
    parent: RecordIndex
    result: RecordIndex
    parent_object: ObjectDescriptor
    staged: StagedObject
    parent_relations: tuple[tuple[str, str], ...]
    result_relations: tuple[tuple[str, str], ...]
    projection: PreparedProjection
    provenance_kind: Literal["local", "upload"] = "local"


@dataclass(frozen=True)
class PublishedResult:
    record_id: str
    project_id: str
    revision: int
    disposition: Literal["created", "identical"]


@dataclass(frozen=True)
class _PreparedContent:
    parent: RecordIndex
    result: RecordIndex
    parent_object: ObjectDescriptor
    result_object: ObjectDescriptor
    payload: bytes = field(repr=False)
    parent_relations: tuple[tuple[str, str], ...]
    result_relations: tuple[tuple[str, str], ...]
    projection: PreparedProjection
    provenance_kind: Literal['local', 'upload']


def _record_index(record: RecordModel, descriptor: ObjectDescriptor) -> RecordIndex:
    if isinstance(record, ProductLifecycleRecord):
        return RecordIndex(record.record_id, record.project_id, "result", record.schema_version,
                           record.canonical_sha256(), descriptor.sha256,
                           record.snapshot_id, record.parent_result_id)
    kind, identity, project, schema, canonical = native_metadata(record)
    return RecordIndex(identity, project, kind, schema, canonical, descriptor.sha256,
                       record.snapshot_id if isinstance(record, (UploadResultRecord, UploadResultRecordV2)) else None,
                       record.parent_result_id if isinstance(record, (UploadResultRecord, UploadResultRecordV2)) else None,
                       source_id=record.source_id if isinstance(record, (UploadSnapshot, UploadSnapshotV2)) else None)


async def _stored(conn: Connection, workspace: str, record_id: str) -> tuple[object, ...] | None:
    return await (await conn.execute(
        "SELECT r.record_id,r.project_id,r.record_type,r.schema_version,r.canonical_sha256,"
        "r.object_sha256,r.snapshot_id,r.parent_result_id,r.source_id,o.byte_size,o.media_type,o.schema_version "
        "FROM k05_records r JOIN k05_objects o ON o.workspace_id=r.workspace_id AND o.sha256=r.object_sha256 "
        "WHERE r.workspace_id=%s AND r.record_id=%s FOR SHARE OF r,o", (workspace, record_id),
    )).fetchone()


def _check_stored(row: tuple[object, ...] | None, index: RecordIndex,
                  descriptor: ObjectDescriptor) -> None:
    if (row is None or len(row) != 12 or _index(row[:9]) != index
            or row[9:11] != (descriptor.byte_size, descriptor.media_type)
            or row[11] not in (None, descriptor.schema_version)):
        raise CatalogIntegrityError()


async def _check_relations(conn: Connection, workspace: str, project: str,
                           record: str, expected: tuple[tuple[str, str], ...]) -> None:
    rows = await _all(await conn.execute(
        "SELECT relation,parent_id FROM k05_record_parents WHERE workspace_id=%s "
        "AND project_id=%s AND record_id=%s ORDER BY relation,parent_id LIMIT %s",
        (workspace, project, record, len(expected) + 1),
    ))
    if tuple(rows) != expected:
        raise CatalogIntegrityError()


class CanonicalResults:
    def __init__(self, objects: PostgresObjects) -> None:
        self.objects = objects
        self.policy = PostgresAuthorization()

    def _content(self, parent: RecordModel, record: ResultModel) -> _PreparedContent:
        parent_object = self.objects.store.describe(_json_bytes(parent.model_dump(mode="json")),
                                                    media_type="application/json", schema_version=parent.schema_version)
        payload = _json_bytes(record.model_dump(mode='json'))
        result_object = self.objects.store.describe(payload, media_type='application/json', schema_version=record.schema_version)
        result_index = _record_index(record, result_object)
        return _PreparedContent(
            _record_index(parent, parent_object), result_index, parent_object, result_object, payload,
            record_parents(parent) if isinstance(parent, ProductLifecycleRecord) else native_parents(parent),
            record_parents(record) if isinstance(record, ProductLifecycleRecord) else native_parents(record),
            prepare_projection(_view_from_record(record) if isinstance(record, ProductLifecycleRecord)
                               else record.view, result_index.canonical_sha256),
            "local" if isinstance(record, ProductLifecycleRecord) else "upload",
        )

    async def _prepare(self, workspace: str, parent: RecordModel, record: ResultModel) -> PreparedResult:
        content = await run_blocking(self._content, parent, record)
        staged = await self.objects.stage(workspace, content.payload,
                                          media_type='application/json', schema_version=record.schema_version)
        if staged.descriptor != content.result_object:
            raise CatalogIntegrityError()
        return PreparedResult(workspace, content.parent, content.result, content.parent_object, staged,
            content.parent_relations, content.result_relations, content.projection, content.provenance_kind)

    @staticmethod
    def _validated_result(workspace_id: str, parent: ResultModel, record: ResultModel) -> tuple[ResultModel, ResultModel]:
        try:
            if not isinstance(workspace_id, str) or not workspace_id:
                raise ValueError
            if isinstance(parent, ProductLifecycleRecord) and isinstance(record, ProductLifecycleRecord):
                parent = ProductLifecycleRecord.model_validate(parent.model_dump(mode="python"))
                record = ProductLifecycleRecord.model_validate(record.model_dump(mode="python"))
                parent_view, view = _view_from_record(parent), _view_from_record(record)
                if parent.record_kind != "result" or record.record_kind != "result":
                    raise ValueError
            elif isinstance(parent, (UploadResultRecord, UploadResultRecordV2)) and isinstance(record, (UploadResultRecord, UploadResultRecordV2)):
                parent = parse_upload_result_record(parent.model_dump(mode="python"))
                record = parse_upload_result_record(record.model_dump(mode="python"))
                parent_view, view = parent.view, record.view
                if parent.workspace_id != workspace_id or record.workspace_id != workspace_id:
                    raise ValueError
            else:
                raise ValueError
            if (parent.project_id is None or parent.snapshot_id is None
                    or (record.project_id, record.snapshot_id, record.parent_result_id)
                    != (parent.project_id, parent.snapshot_id, parent.record_id)
                    or record.record_id == parent.record_id):
                raise ValueError
            _validate_derived_result(parent_view, view)
        except (ValueError, TypeError, AttributeError):
            raise CanonicalResultRejected() from None
        return parent, record

    async def prepare_result(self, workspace_id: str, *, parent: ResultModel,
                             record: ResultModel) -> PreparedResult:
        """Validate/hash/stage a same-domain follow-up before its commit connection."""
        parent, record = await run_blocking(self._validated_result, workspace_id, parent, record)
        return await self._prepare(workspace_id, parent, record)

    @staticmethod
    def _validated_initial(workspace_id: str, snapshot: AnyUploadSnapshot,
                           record: AnyUploadResultRecord) -> tuple[AnyUploadSnapshot, AnyUploadResultRecord]:
        try:
            if not isinstance(snapshot, (UploadSnapshot, UploadSnapshotV2)) or not isinstance(record, (UploadResultRecord, UploadResultRecordV2)):
                raise ValueError
            snapshot = parse_upload_snapshot(snapshot.model_dump(mode="python"))
            record = parse_upload_result_record(record.model_dump(mode="python"))
            if (workspace_id != snapshot.workspace_id or record.workspace_id != workspace_id
                    or record.parent_result_id is not None
                    or not record.view.conversation.turns
                    or record != build_initial_upload_result(snapshot, record.view.conversation.turns[0].question, record.view)):
                raise ValueError
        except (ValueError, TypeError, AttributeError):
            raise CanonicalResultRejected() from None
        return snapshot, record

    async def prepare_initial_result(self, workspace_id: str, *, snapshot: AnyUploadSnapshot,
                                     record: AnyUploadResultRecord) -> PreparedResult:
        """Validate the exact native initial analyzer output without minting IDs."""
        snapshot, record = await run_blocking(self._validated_initial, workspace_id, snapshot, record)
        return await self._prepare(workspace_id, snapshot, record)

    @staticmethod
    def _validate_publication(conn: Connection, authorization: Authorization,
                              prepared: PreparedResult, expected_revision: int) -> None:
        if conn.autocommit:
            raise RuntimeError("Transactional connection required")
        _revision(expected_revision)
        if (not isinstance(prepared, PreparedResult) or not isinstance(authorization, Authorization)
                or authorization.workspace_id != prepared.workspace_id):
            raise AuthorizationDenied()

    async def enlist_result(self, conn: Connection, *, authorization: Authorization,
                            prepared: PreparedResult, expected_revision: int) -> PublishedResult:
        """Append a result/history row in the caller's existing all-or-nothing transaction."""
        self._validate_publication(conn, authorization, prepared, expected_revision)
        workspace, result = prepared.workspace_id, prepared.result
        project = result.project_id
        if project is None or result.snapshot_id is None:
            raise CanonicalResultRejected()
        # A writer takes UPDATE immediately; two prepared writers never upgrade
        # a shared project lock and deadlock each other.
        row = await (await conn.execute(
            "SELECT revision,provenance_kind FROM k05_projects WHERE workspace_id=%s AND project_id=%s FOR UPDATE",
            (workspace, project),
        )).fetchone()
        if row is None:
            raise AuthorizationDenied()
        await self.policy.authorize_record(conn, authorization, workspace, project, prepared.parent.record_id, "analyze")
        if (len(row) != 2 or type(row[0]) is not int or row[0] < 0
                or row[1] != prepared.provenance_kind):
            raise CatalogIntegrityError()
        revision = row[0]
        if revision != expected_revision:
            raise WorkspaceConflict()
        _check_stored(await _stored(conn, workspace, prepared.parent.record_id),
                      prepared.parent, prepared.parent_object)
        await _check_relations(conn, workspace, project, prepared.parent.record_id,
                               prepared.parent_relations)
        snapshot = await (await conn.execute(
            "SELECT record_type FROM k05_records WHERE workspace_id=%s AND project_id=%s "
            "AND record_id=%s FOR SHARE", (workspace, project, result.snapshot_id),
        )).fetchone()
        snapshot_kind = "upload_snapshot" if prepared.provenance_kind == "upload" else "snapshot"
        if snapshot != (snapshot_kind,):
            raise CatalogIntegrityError()
        existing = await _stored(conn, workspace, result.record_id)
        if existing is not None:
            _check_stored(existing, result, prepared.staged.descriptor)
            await _check_relations(conn, workspace, project, result.record_id,
                                   prepared.result_relations)
            await publish_projection(conn, workspace, prepared.projection)
            await self.objects.finish(conn, workspace, prepared.staged)
            return PublishedResult(result.record_id, project, revision, "identical")
        await self.objects.ready(conn, workspace, prepared.staged)
        await conn.execute(
            "INSERT INTO k05_records(workspace_id,record_id,project_id,record_type,schema_version,"
            "canonical_sha256,object_sha256,snapshot_id,parent_result_id,source_id) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (workspace, result.record_id, project, result.record_type, result.schema_version, result.canonical_sha256,
             result.object_sha256, result.snapshot_id, result.parent_result_id, result.source_id),
        )
        for relation, parent_id in prepared.result_relations:
            await conn.execute("INSERT INTO k05_record_parents(workspace_id,project_id,record_id,relation,parent_id) "
                               "VALUES (%s,%s,%s,%s,%s)", (workspace, project, result.record_id, relation, parent_id))
        await publish_projection(conn, workspace, prepared.projection)
        changed = await (await conn.execute(
            "UPDATE k05_projects SET revision=revision+1 WHERE workspace_id=%s AND project_id=%s "
            "AND revision=%s RETURNING revision", (workspace, project, expected_revision),
        )).fetchone()
        if changed != (revision + 1,):
            raise WorkspaceConflict()
        await conn.execute("INSERT INTO k05_current_pointers(workspace_id,project_id,pointer_name,record_id,revision) "
            "VALUES(%s,%s,'result',%s,0) ON CONFLICT(workspace_id,project_id,pointer_name) DO UPDATE "
            "SET record_id=EXCLUDED.record_id,revision=k05_current_pointers.revision+1", (workspace, project, result.record_id))
        await self.objects.finish(conn, workspace, prepared.staged)
        return PublishedResult(result.record_id, project, revision + 1, "created")
