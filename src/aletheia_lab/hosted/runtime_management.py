"""Metadata and policy transactions on the existing canonical/workspace tables."""
from __future__ import annotations

from pydantic import SecretStr, ValidationError

from aletheia_lab.hosted_auth.postgres_sessions import Connection, Pool, _digest
from aletheia_lab.hosted_contract.runtime_management import (
    ChangeProject,
    ChangeWorkspaceSettings,
    ProjectDetails,
    WorkspacePolicyLimits,
    WorkspaceSettings,
)

from .action_audit import record_action
from .application import VERSION
from .authorization import (
    Authorization,
    AuthorizationDenied,
    PostgresAuthorization,
    current_session,
)
from .transactions import run_transaction
from .workspace_management import WorkspaceConflict, WorkspaceDenied, _revision


class PolicyRejected(ValueError):
    """A requested policy exceeds the current trusted platform limits."""


class RuntimeManagement:
    def __init__(self, pool: Pool, *, platform_limits: WorkspacePolicyLimits | None = None,
                 audit: bool = False) -> None:
        self._pool, self._audit = pool, audit
        self._limits = WorkspacePolicyLimits.model_validate((platform_limits or WorkspacePolicyLimits()).model_dump(mode="python"))
        self._policy = PostgresAuthorization()

    async def is_ready(self) -> bool:
        try:
            async with self._pool.connection() as conn:
                await conn.execute("SELECT display_name FROM k05_projects LIMIT 0")
                await conn.execute("SELECT settings_revision,maximum_upload_bytes,maximum_workspace_bytes "
                                   "FROM j01_workspaces LIMIT 0")
            return True
        except Exception:
            return False

    async def _project(self, conn: Connection, auth: Authorization, workspace: str, project: str) -> ProjectDetails:
        await self._policy.authorize_project(conn, auth, workspace, project, "read", allow_deleted=True)
        row = await (await conn.execute(
            "SELECT p.revision,p.display_name,EXISTS(SELECT 1 FROM k05_records r WHERE r.workspace_id=p.workspace_id "
            "AND r.project_id=p.project_id AND r.record_type='deletion'),"
            "(SELECT record_id FROM k05_current_pointers c WHERE c.workspace_id=p.workspace_id "
            "AND c.project_id=p.project_id AND c.pointer_name='snapshot'),"
            "(SELECT c.record_id FROM k05_current_pointers c JOIN k05_records r ON r.workspace_id=c.workspace_id "
            "AND r.project_id=c.project_id AND r.record_id=c.record_id WHERE c.workspace_id=p.workspace_id "
            "AND c.project_id=p.project_id AND c.pointer_name='result' AND r.snapshot_id=(SELECT s.record_id "
            "FROM k05_current_pointers s WHERE s.workspace_id=p.workspace_id AND s.project_id=p.project_id "
            "AND s.pointer_name='snapshot')) "
            "FROM k05_projects p WHERE p.workspace_id=%s AND p.project_id=%s", (workspace, project))).fetchone()
        if row is None or len(row) != 5 or type(row[2]) is not bool:
            raise AuthorizationDenied()
        return ProjectDetails.model_validate({"schema_version": VERSION, "workspace_id": workspace, "project_id": project,
            "revision": row[0], "display_name": row[1], "state": "deleting" if row[2] else "active",
            "current_snapshot_id": row[3], "current_result_id": row[4]})

    async def project(self, *, credential_digest: SecretStr, workspace_id: str, project_id: str) -> ProjectDetails:
        async with self._pool.connection() as conn:
            auth = await self._policy.authorize(conn, _digest(credential_digest), workspace_id, "read")
            return await self._project(conn, auth, workspace_id, project_id)

    async def update_project(self, *, credential_digest: SecretStr, workspace_id: str, project_id: str,
                             expected_revision: int, request: ChangeProject) -> ProjectDetails:
        _revision(expected_revision)
        request = ChangeProject.model_validate(request.model_dump(mode="python"))
        digest = _digest(credential_digest)

        async def update(conn: Connection) -> ProjectDetails:
            auth = await self._policy.authorize(conn, digest, workspace_id, "update")
            row = await (await conn.execute("SELECT revision FROM k05_projects WHERE workspace_id=%s "
                "AND project_id=%s FOR UPDATE", (workspace_id, project_id))).fetchone()
            await self._policy.authorize_project(conn, auth, workspace_id, project_id, "update")
            if row != (expected_revision,):
                raise WorkspaceConflict()
            changed = await (await conn.execute("UPDATE k05_projects SET display_name=%s,revision=revision+1 "
                "WHERE workspace_id=%s AND project_id=%s AND revision=%s RETURNING revision",
                (request.display_name, workspace_id, project_id, expected_revision))).fetchone()
            if changed != (expected_revision + 1,):
                raise WorkspaceConflict()
            if self._audit:
                await record_action(conn, actor_id=auth.user_id, workspace_id=workspace_id,
                    action="project.update", effect_id=project_id, revision=expected_revision + 1)
            return await self._project(conn, auth, workspace_id, project_id)

        return await run_transaction(self._pool, update)

    async def _settings_auth(self, conn: Connection, digest: str, workspace: str) -> Authorization:
        auth = await self._policy.authorize(conn, digest, workspace, "read")
        if auth.role not in ("owner", "admin"):
            raise WorkspaceDenied()
        return auth

    async def _settings(self, conn: Connection, workspace: str) -> WorkspaceSettings:
        row = await (await conn.execute("SELECT settings_revision,maximum_upload_bytes,maximum_workspace_bytes "
                                       "FROM j01_workspaces WHERE workspace_id=%s", (workspace,))).fetchone()
        if row is None or len(row) != 3:
            raise AuthorizationDenied()
        overrides = WorkspacePolicyLimits.model_validate({
            "maximum_upload_bytes": self._limits.maximum_upload_bytes if row[1] is None else row[1],
            "maximum_workspace_bytes": self._limits.maximum_workspace_bytes if row[2] is None else row[2]})
        workspace_bytes = min(overrides.maximum_workspace_bytes, self._limits.maximum_workspace_bytes)
        return WorkspaceSettings.model_validate({"schema_version": VERSION, "workspace_id": workspace, "revision": row[0],
            "maximum_upload_bytes": min(overrides.maximum_upload_bytes, self._limits.maximum_upload_bytes, workspace_bytes),
            "maximum_workspace_bytes": workspace_bytes,
            "provenance": "platform_default" if row[1:] == (None, None) else "workspace_override"})

    async def settings(self, *, credential_digest: SecretStr, workspace_id: str) -> WorkspaceSettings:
        async with self._pool.connection() as conn:
            await self._settings_auth(conn, _digest(credential_digest), workspace_id)
            return await self._settings(conn, workspace_id)

    async def update_settings(self, *, credential_digest: SecretStr, workspace_id: str,
                              expected_revision: int, request: ChangeWorkspaceSettings) -> WorkspaceSettings:
        _revision(expected_revision)
        request = ChangeWorkspaceSettings.model_validate(request.model_dump(mode="python", exclude_unset=True))
        digest = _digest(credential_digest)

        async def update(conn: Connection) -> WorkspaceSettings:
            await current_session(conn, digest)
            await conn.execute("SELECT workspace_id FROM j01_workspaces WHERE workspace_id=%s FOR UPDATE", (workspace_id,))
            auth = await self._settings_auth(conn, digest, workspace_id)
            current = await self._settings(conn, workspace_id)
            if current.revision != expected_revision:
                raise WorkspaceConflict()
            try:
                values = WorkspacePolicyLimits.model_validate({field: getattr(request, field) if field in request.model_fields_set
                    else getattr(current, field) for field in WorkspacePolicyLimits.model_fields})
                if (values.maximum_upload_bytes > self._limits.maximum_upload_bytes
                        or values.maximum_workspace_bytes > self._limits.maximum_workspace_bytes):
                    raise PolicyRejected()
            except ValidationError:
                raise PolicyRejected() from None
            changed = await (await conn.execute("UPDATE j01_workspaces SET settings_revision=settings_revision+1,"
                "maximum_upload_bytes=%s,maximum_workspace_bytes=%s WHERE workspace_id=%s AND settings_revision=%s "
                "RETURNING settings_revision", (values.maximum_upload_bytes, values.maximum_workspace_bytes,
                                               workspace_id, expected_revision))).fetchone()
            if changed != (expected_revision + 1,):
                raise WorkspaceConflict()
            if self._audit:
                await record_action(conn, actor_id=auth.user_id, workspace_id=workspace_id,
                    action="workspace.settings", effect_id=workspace_id, revision=expected_revision + 1)
            return await self._settings(conn, workspace_id)

        return await run_transaction(self._pool, update)
