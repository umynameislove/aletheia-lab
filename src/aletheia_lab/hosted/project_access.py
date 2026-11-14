"""Manager-controlled grants on the existing canonical PostgreSQL projects."""
from __future__ import annotations

from pydantic import SecretStr

from aletheia_lab.hosted_auth.postgres_sessions import Connection, Pool, _digest

from .action_audit import record_action
from .application import VERSION
from .authorization import AuthorizationDenied
from .read_projection_migration import stamp_catalog_revision
from .transactions import run_transaction
from .workspace_management import (
    GRANT_ACTIONS,
    WorkspaceConflict,
    WorkspaceDenied,
    WorkspaceManagement,
    _revision,
)


class PostgresProjectAccess:
    def __init__(self, pool: Pool, *, audit: bool = False) -> None:
        self._pool = pool
        self._audit = audit
        self._workspaces = WorkspaceManagement(pool)

    async def read_grant(self, *, credential_digest: SecretStr, workspace_id: str, project_id: str,
                         user_id: str) -> dict[str, object]:
        async with self._pool.connection() as conn:
            auth = await self._workspaces._authorization.authorize(
                conn, _digest(credential_digest), workspace_id, "manage_members")
            await self._workspaces._authorization.authorize_project(conn, auth, workspace_id, project_id, "read")
            target = await (await conn.execute("SELECT 1 FROM j01_memberships WHERE workspace_id=%s AND user_id=%s",
                                               (workspace_id, user_id))).fetchone()
            if target is None:
                raise AuthorizationDenied()
            row = await (await conn.execute("SELECT actions,revision FROM j01_project_grants "
                "WHERE workspace_id=%s AND project_id=%s AND user_id=%s", (workspace_id, project_id, user_id))).fetchone()
            return {"schema_version": VERSION, "workspace_id": workspace_id, "project_id": project_id,
                    "user_id": user_id, "actions": [] if row is None else row[0], "revision": 0 if row is None else row[1]}

    async def set_grant(self, *, credential_digest: SecretStr, workspace_id: str, project_id: str,
                        user_id: str, actions: list[str], expected_revision: int) -> dict[str, object]:
        _revision(expected_revision)
        if (not isinstance(actions, list) or any(not isinstance(action, str) or action not in GRANT_ACTIONS for action in actions)
                or (actions and "read" not in actions) or len(set(actions)) != len(actions)):
            raise ValueError("Explicit grant actions required.")
        actions = list(actions)
        async def commit(conn: Connection) -> dict[str, object]:
            actor = await self._workspaces._accounts(conn, _digest(credential_digest), user_id)
            await self._workspaces._workspace(conn, workspace_id)
            await self._workspaces._manager(conn, workspace_id, actor)
            target = await self._workspaces._membership(conn, workspace_id, user_id)
            if target[1] is not True:
                raise WorkspaceDenied()
            project = await (await conn.execute(
                "SELECT creator_id,revision FROM k05_projects WHERE workspace_id=%s AND project_id=%s FOR UPDATE",
                (workspace_id, project_id),
            )).fetchone()
            if project is None:
                raise AuthorizationDenied()
            deleted = await (await conn.execute(
                "SELECT 1 FROM k05_records WHERE workspace_id=%s AND project_id=%s AND record_type='deletion' LIMIT 1",
                (workspace_id, project_id),
            )).fetchone()
            if deleted is not None:
                raise AuthorizationDenied()
            row = await (await conn.execute(
                "SELECT revision FROM j01_project_grants WHERE workspace_id=%s AND project_id=%s AND user_id=%s FOR UPDATE",
                (workspace_id, project_id, user_id),
            )).fetchone()
            if (0 if row is None else row[0]) != expected_revision:
                raise WorkspaceConflict()
            await conn.execute(
                "INSERT INTO j01_project_grants(workspace_id,project_id,user_id,actions,revision) VALUES (%s,%s,%s,%s,%s) "
                "ON CONFLICT(workspace_id,project_id,user_id) DO UPDATE SET actions=EXCLUDED.actions,revision=EXCLUDED.revision",
                (workspace_id, project_id, user_id, actions, expected_revision + 1),
            )
            await stamp_catalog_revision(conn, workspace_id)
            if self._audit:
                await record_action(conn, actor_id=actor, workspace_id=workspace_id, action="project_grant.set",
                    effect_id=project_id, revision=expected_revision + 1, parent_id=project_id, subject_id=user_id)
            return {"schema_version": VERSION, "workspace_id": workspace_id, "project_id": project_id,
                    "user_id": user_id, "actions": list(actions), "revision": expected_revision + 1}

        return await run_transaction(self._pool, commit)
