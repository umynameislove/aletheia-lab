"""Logical canonical project deletion; retained history is not physically purged."""
from __future__ import annotations

import re

from pydantic import SecretStr, ValidationError

from aletheia_lab.hosted_auth.postgres_sessions import Connection, Pool, _digest
from aletheia_lab.hosted_contract.workspaces import DeletedProject
from aletheia_lab.product.deletion import _DELETION_SCHEMA_VERSION
from aletheia_lab.product.lifecycle import build_product_lifecycle_record
from aletheia_lab.project.persistence import _json_bytes

from .action_audit import record_action
from .application import VERSION
from .authorization import AuthorizationDenied, PostgresAuthorization
from .legacy_import import model_metadata, record_parents
from .postgres_catalog import CatalogIntegrityError, PostgresCatalog
from .transactions import run_transaction
from .workspace_management import WorkspaceConflict, _revision


class PostgresProjectMutations:
    def __init__(self, pool: Pool, catalog: PostgresCatalog, *, audit: bool = False) -> None:
        self._pool, self._objects = pool, catalog.objects
        self._audit = audit
        self._policy = PostgresAuthorization()

    @staticmethod
    async def _state(conn: Connection, workspace: str, project: str, *, lock: bool = False) -> tuple[object, ...]:
        if lock:
            locked = await (await conn.execute("SELECT project_id FROM k05_projects WHERE workspace_id=%s "
                "AND project_id=%s FOR UPDATE", (workspace, project))).fetchone()
            if locked != (project,):
                raise AuthorizationDenied()
        # A fresh statement observes tombstones committed while the project row lock waited.
        row = await (await conn.execute(
            "SELECT p.revision,p.deletion_revision,p.deletion_tombstone_id,p.deleted_by,"
            "EXISTS(SELECT 1 FROM k05_records r WHERE r.workspace_id=p.workspace_id AND r.project_id=p.project_id "
            "AND r.record_type='deletion'),EXISTS(SELECT 1 FROM k05_records r WHERE r.workspace_id=p.workspace_id "
            "AND r.project_id=p.project_id AND r.record_id=p.deletion_tombstone_id AND r.record_type='deletion') "
            "FROM k05_projects p WHERE p.workspace_id=%s AND p.project_id=%s",
            (workspace, project))).fetchone()
        if row is None:
            raise AuthorizationDenied()
        return row

    @staticmethod
    def _replay(row: tuple[object, ...], workspace: str, project: str, expected: int) -> dict[str, object] | None:
        if (len(row) != 6 or type(row[0]) is not int or not 0 <= row[0] <= 9223372036854775807
                or type(row[4]) is not bool or type(row[5]) is not bool):
            raise CatalogIntegrityError()
        if row[1:4] == (None, None, None):
            if row[4] or row[0] != expected:
                raise WorkspaceConflict()
            return None
        if (any(value is None for value in row[1:4]) or row[4:6] != (True, True)
                or not isinstance(row[3], str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", row[3]) is None):
            raise CatalogIntegrityError()
        try:
            receipt = DeletedProject.model_validate({"schema_version": VERSION, "workspace_id": workspace,
                "project_id": project, "revision": row[1], "tombstone_id": row[2],
                "status": "tombstoned", "retention": "retained"})
        except ValidationError:
            raise CatalogIntegrityError() from None
        if row[0] != receipt.revision or expected + 1 != receipt.revision:
            raise WorkspaceConflict()
        return receipt.model_dump(mode="json")

    async def delete(self, *, credential_digest: SecretStr, workspace_id: str,
                     project_id: str, expected_revision: int) -> dict[str, object]:
        _revision(expected_revision)
        digest = _digest(credential_digest)
        async with self._pool.connection() as conn:
            auth = await self._policy.authorize(conn, digest, workspace_id, "delete")
            await self._policy.authorize_project(conn, auth, workspace_id, project_id, "delete", allow_deleted=True)
            receipt = self._replay(await self._state(conn, workspace_id, project_id), workspace_id, project_id, expected_revision)
            if receipt is not None:
                return receipt
            await self._policy.authorize_project(conn, auth, workspace_id, project_id, "delete")
        tombstone = build_product_lifecycle_record(record_kind="deletion", project_id=project_id, payload={
            "schema_version": _DELETION_SCHEMA_VERSION, "status": "pending", "deleted_count": 0,
            "retained_shared_count": 0, "purge_object_sha256s": [],
        })
        staged = await self._objects.stage(workspace_id, _json_bytes(tombstone.model_dump(mode="json")),
                                           media_type="application/json", schema_version=tombstone.schema_version)
        async def commit(conn: Connection) -> dict[str, object]:
            auth = await self._policy.authorize(conn, digest, workspace_id, "delete")
            # Take the write lock before project policy's SHARE read to avoid lock upgrades.
            current = await self._state(conn, workspace_id, project_id, lock=True)
            await self._policy.authorize_project(conn, auth, workspace_id, project_id, "delete", allow_deleted=True)
            receipt = self._replay(current, workspace_id, project_id, expected_revision)
            if receipt is not None:
                await self._objects.finish(conn, workspace_id, staged)
                return receipt
            await self._policy.authorize_project(conn, auth, workspace_id, project_id, "delete")
            kind, record_id, project, version, canonical = model_metadata(tombstone)
            values = (workspace_id, record_id, project, kind, version, canonical,
                      staged.descriptor.sha256, tombstone.snapshot_id, tombstone.parent_result_id)
            await self._objects.ready(conn, workspace_id, staged)
            await conn.execute("INSERT INTO k05_records(workspace_id,record_id,project_id,record_type,schema_version,"
                "canonical_sha256,object_sha256,snapshot_id,parent_result_id) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (workspace_id,record_id) DO NOTHING", values)
            saved = await (await conn.execute("SELECT workspace_id,record_id,project_id,record_type,schema_version,"
                "canonical_sha256,object_sha256,snapshot_id,parent_result_id FROM k05_records "
                "WHERE workspace_id=%s AND record_id=%s", (workspace_id, record_id))).fetchone()
            if saved != values:
                raise CatalogIntegrityError()
            for relation, parent in record_parents(tombstone):
                await conn.execute("INSERT INTO k05_record_parents(workspace_id,project_id,record_id,relation,parent_id) "
                    "VALUES(%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING", (workspace_id, project_id, record_id, relation, parent))
            changed = await (await conn.execute("UPDATE k05_projects SET revision=revision+1,deletion_revision=revision+1,"
                "deletion_tombstone_id=%s,deleted_by=%s WHERE workspace_id=%s AND project_id=%s AND revision=%s "
                "AND deletion_revision IS NULL RETURNING revision",
                (record_id, auth.user_id, workspace_id, project_id, expected_revision))).fetchone()
            if changed != (expected_revision + 1,):
                raise WorkspaceConflict()
            await self._objects.finish(conn, workspace_id, staged)
            if self._audit:
                await record_action(conn, actor_id=auth.user_id, workspace_id=workspace_id, action="project.delete",
                    effect_id=record_id, revision=expected_revision + 1, parent_id=project_id)
            return {"schema_version": VERSION, "workspace_id": workspace_id, "project_id": project_id,
                    "revision": expected_revision + 1, "tombstone_id": record_id,
                    "status": "tombstoned", "retention": "retained"}

        return await run_transaction(self._pool, commit)
