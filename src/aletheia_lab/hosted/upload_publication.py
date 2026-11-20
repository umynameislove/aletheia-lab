"""Typed upload snapshot enlistment in the caller's job-publication transaction.

The caller supplies current Authorization obtained on this same connection and
must commit/rollback canonical metadata with its projection/job terminal/audit.
All filesystem work happens in prepare_snapshot before acquiring that connection.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, cast

from aletheia_lab.hosted_auth.postgres_sessions import Connection
from aletheia_lab.project.identity import canonical_project_sha256
from aletheia_lab.project.persistence import _json_bytes

from .async_work import run_blocking
from .authorization import Authorization, AuthorizationDenied, PostgresAuthorization
from .postgres_catalog import CatalogIntegrityError
from .postgres_objects import PostgresObjects, StagedObject
from .upload_comparison import (
    UploadGenerationComparison,
    _compare_validated,
    comparison_metadata,
    comparison_parents,
)
from .upload_snapshot import AnyUploadSnapshot, parse_upload_snapshot
from .workspace_management import WorkspaceConflict, _revision


class SnapshotAlreadyPublished(ValueError):
    """Rollback; verify/read the existing canonical snapshot outside the transaction."""
    def __init__(self, snapshot_id: str) -> None:
        self.snapshot_id = snapshot_id
        super().__init__("The snapshot already has an immutable canonical record.")


@dataclass(frozen=True)
class PreparedUploadSnapshot:
    snapshot: AnyUploadSnapshot
    staged: StagedObject
    metadata: tuple[str, str, str, str, str]
    comparison: UploadGenerationComparison | None = None
    staged_comparison: StagedObject | None = None
    parent_canonical_sha256: str | None = None


@dataclass(frozen=True)
class PublishedUploadSnapshot:
    snapshot_id: str
    project_id: str
    revision: int
    disposition: Literal["created", "identical"]


class UploadPublication:
    def __init__(self, objects: PostgresObjects) -> None:
        self.objects = objects
        self.policy = PostgresAuthorization()

    @staticmethod
    def _payloads(snapshot: AnyUploadSnapshot, existing: AnyUploadSnapshot | None,
                  parent: AnyUploadSnapshot | None) -> tuple[AnyUploadSnapshot, tuple[str, str, str, str, str], bytes,
                      UploadGenerationComparison | None, bytes | None, str | None]:
        snapshot = parse_upload_snapshot(snapshot.model_dump(mode="python"))
        if parent is not None:
            parent = parse_upload_snapshot(parent.model_dump(mode='python'))
        if parent is not None and (parent.workspace_id != snapshot.workspace_id
                                   or parent.mapping.project_id != snapshot.mapping.project_id):
            raise ValueError('Comparison parent belongs to another project.')
        if existing is not None:
            existing = parse_upload_snapshot(existing.model_dump(mode="python"))
            if existing.snapshot_id != snapshot.snapshot_id or existing.state_payload() != snapshot.state_payload():
                raise CatalogIntegrityError()
            # State identity excludes capture time: retain the first canonical audit bytes.
            snapshot = existing
        comparison = (_compare_validated(parent, snapshot)
                      if parent is not None and parent.snapshot_id != snapshot.snapshot_id else None)
        payload = snapshot.model_dump(mode='json')
        metadata = ('upload_snapshot', snapshot.snapshot_id, snapshot.mapping.project_id,
                    snapshot.schema_version, canonical_project_sha256(payload))
        return (snapshot, metadata, _json_bytes(payload), comparison,
            None if comparison is None else _json_bytes(comparison.model_dump(mode='json')),
            None if parent is None else canonical_project_sha256(parent.model_dump(mode='json')))

    async def prepare_snapshot(self, snapshot: AnyUploadSnapshot, *,
                               existing: AnyUploadSnapshot | None = None,
                               parent: AnyUploadSnapshot | None = None) -> PreparedUploadSnapshot:
        snapshot, metadata, payload, comparison, compared, parent_hash = await run_blocking(
            self._payloads, snapshot, existing, parent)
        staged = await self.objects.stage(snapshot.workspace_id, payload,
            media_type='application/json', schema_version=snapshot.schema_version)
        staged_comparison = None
        if comparison is not None and compared is not None:
            staged_comparison = await self.objects.stage(snapshot.workspace_id, compared,
                media_type='application/json', schema_version=comparison.schema_version)
        return PreparedUploadSnapshot(snapshot, staged, metadata, comparison, staged_comparison, parent_hash)

    async def _lock_project(self, conn: Connection, authorization: Authorization,
                            snapshot: AnyUploadSnapshot, expected_revision: int | None) -> tuple[int, tuple[object, ...] | None]:
        workspace, project = snapshot.workspace_id, snapshot.mapping.project_id
        await self.policy.authorize_source(conn, authorization, workspace, snapshot.source_id, "confirm")
        source = await (await conn.execute("SELECT sha256 FROM j01_sources WHERE workspace_id=%s AND source_id=%s FOR SHARE",
                                          (workspace, snapshot.source_id))).fetchone()
        if source != (snapshot.source_sha256,):
            raise CatalogIntegrityError()
        await conn.execute("INSERT INTO k05_projects(workspace_id,project_id,creator_id,provenance_kind) "
                           "VALUES(%s,%s,%s,'upload') ON CONFLICT DO NOTHING",
                           (workspace, project, authorization.user_id))
        current = await (await conn.execute("SELECT revision,provenance_kind FROM k05_projects "
            "WHERE workspace_id=%s AND project_id=%s FOR UPDATE", (workspace, project))).fetchone()
        if current is None or current[1] != "upload":
            raise AuthorizationDenied()
        await self.policy.authorize_project(conn, authorization, workspace, project, "confirm")
        revision = cast(int, current[0])
        if expected_revision is not None and revision != expected_revision:
            raise WorkspaceConflict()
        pointer = await (await conn.execute("SELECT record_id FROM k05_current_pointers "
            "WHERE workspace_id=%s AND project_id=%s AND pointer_name='snapshot' FOR UPDATE", (workspace, project))).fetchone()
        if (pointer is None and revision != 0) or (pointer is not None and pointer[0] != snapshot.snapshot_id
                                                and expected_revision is None):
            raise WorkspaceConflict()
        return revision, pointer

    async def _verify_parent(self, conn: Connection, authorization: Authorization,
                             prepared: PreparedUploadSnapshot, workspace: str, project: str,
                             pointer: tuple[object, ...] | None) -> None:
        comparison = prepared.comparison
        if comparison is None:
            raise CatalogIntegrityError()
        if pointer != (comparison.before_snapshot_id,):
            raise WorkspaceConflict()
        await self.policy.authorize_record(conn, authorization, workspace, project,
            comparison.before_snapshot_id, 'refresh')
        parent = await (await conn.execute('SELECT canonical_sha256 FROM k05_records WHERE workspace_id=%s '
            'AND project_id=%s AND record_id=%s AND record_type=\'upload_snapshot\' FOR SHARE',
            (workspace, project, comparison.before_snapshot_id))).fetchone()
        # Canonical hash includes the record's own integrity field.
        # The builder used verified canonical bytes outside this transaction;
        # immutable metadata and the locked current pointer fence its identity.
        if parent != (prepared.parent_canonical_sha256,):
            raise CatalogIntegrityError()

    @staticmethod
    def _validate_transaction(conn: Connection, expected_revision: int | None) -> None:
        if conn.autocommit:
            raise RuntimeError("Caller-owned transactional connection required")
        if expected_revision is not None:
            _revision(expected_revision)

    async def enlist_snapshot(self, conn: Connection, *, authorization: Authorization,
                              prepared: PreparedUploadSnapshot,
                              expected_revision: int | None = None) -> PublishedUploadSnapshot:
        self._validate_transaction(conn, expected_revision)
        snapshot, staged = prepared.snapshot, prepared.staged
        workspace, project = snapshot.workspace_id, snapshot.mapping.project_id
        if authorization.workspace_id != workspace:
            raise AuthorizationDenied()
        revision, pointer = await self._lock_project(conn, authorization, snapshot, expected_revision)
        if prepared.comparison is not None:
            await self._verify_parent(conn, authorization, prepared, workspace, project, pointer)
        kind, identity, scope, version, canonical = prepared.metadata
        values = (workspace, identity, scope, kind, version, canonical, staged.descriptor.sha256,
                  None, None, snapshot.source_id)
        prior = await (await conn.execute("SELECT workspace_id,record_id,project_id,record_type,schema_version,"
            "canonical_sha256,object_sha256,snapshot_id,parent_result_id,source_id FROM k05_records "
            "WHERE workspace_id=%s AND record_id=%s", (workspace, identity))).fetchone()
        if prior is not None and prior != values:
            # The caller re-reads verified first-capture bytes after rolling back.
            if tuple(prior[:5]) == values[:5] and prior[7:] == values[7:]:
                raise SnapshotAlreadyPublished(identity)
            raise CatalogIntegrityError()
        await self.objects.ready(conn, workspace, staged)
        if prior is None:
            await conn.execute("INSERT INTO k05_records(workspace_id,record_id,project_id,record_type,schema_version,"
                "canonical_sha256,object_sha256,snapshot_id,parent_result_id,source_id) "
                "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", values)
        elif pointer is None or (pointer[0] != identity and expected_revision is None):
            raise CatalogIntegrityError()
        if prior is not None and pointer == (identity,):
            await self.objects.finish(conn, workspace, staged)
            return PublishedUploadSnapshot(identity, project, revision, "identical")
        if prepared.comparison is not None:
            await self._enlist_comparison(conn, workspace, project, prepared)
        await conn.execute("INSERT INTO k05_current_pointers(workspace_id,project_id,pointer_name,record_id,revision) "
            "VALUES(%s,%s,'snapshot',%s,0) ON CONFLICT(workspace_id,project_id,pointer_name) DO UPDATE "
            "SET record_id=EXCLUDED.record_id,revision=k05_current_pointers.revision+1", (workspace, project, identity))
        changed = await (await conn.execute("UPDATE k05_projects SET revision=revision+1 WHERE workspace_id=%s "
            "AND project_id=%s AND revision=%s RETURNING revision", (workspace, project, revision))).fetchone()
        if changed != (revision + 1,):
            raise WorkspaceConflict()
        await self.objects.finish(conn, workspace, staged)
        return PublishedUploadSnapshot(identity, project, revision + 1, "created")

    async def _enlist_comparison(self, conn: Connection, workspace: str, project: str,
                                 prepared: PreparedUploadSnapshot) -> None:
        comparison, staged = prepared.comparison, prepared.staged_comparison
        if comparison is None or staged is None:
            raise CatalogIntegrityError()
        kind, identity, scope, version, canonical = comparison_metadata(comparison)
        values = (workspace, identity, scope, kind, version, canonical, staged.descriptor.sha256)
        prior = await (await conn.execute('SELECT workspace_id,record_id,project_id,record_type,schema_version,'
            'canonical_sha256,object_sha256 FROM k05_records WHERE workspace_id=%s AND record_id=%s',
            (workspace, identity))).fetchone()
        if prior is not None and prior != values:
            raise CatalogIntegrityError()
        await self.objects.ready(conn, workspace, staged)
        if prior is None:
            await conn.execute('INSERT INTO k05_records(workspace_id,record_id,project_id,record_type,schema_version,'
                'canonical_sha256,object_sha256) VALUES(%s,%s,%s,%s,%s,%s,%s)', values)
            for role, parent_id in comparison_parents(comparison):
                await conn.execute('INSERT INTO k05_record_parents(workspace_id,project_id,record_id,relation,parent_id) '
                    'VALUES(%s,%s,%s,%s,%s)', (workspace, project, identity, role, parent_id))
        cursor = await conn.execute('SELECT relation,parent_id FROM k05_record_parents '
            'WHERE workspace_id=%s AND project_id=%s AND record_id=%s ORDER BY relation,parent_id LIMIT 3',
            (workspace, project, identity))
        relations = []
        for _ in range(3):
            row = await cursor.fetchone()
            if row is None:
                break
            relations.append(row)
        if tuple(relations) != comparison_parents(comparison):
            raise CatalogIntegrityError()
        await self.objects.finish(conn, workspace, staged)
