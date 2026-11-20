"""Current workspace membership and session facts from one PostgreSQL read."""
from __future__ import annotations

from aletheia_lab.hosted_auth.postgres_sessions import _READ, Pool, _snapshot
from aletheia_lab.hosted_auth.sessions import ServerSessions
from aletheia_lab.hosted_contract.session_capabilities import Membership

from .read_backend import WorkspaceAccessRecord


class PostgresWorkspaceAccess:
    def __init__(self, pool: Pool, sessions: ServerSessions) -> None:
        self._pool, self._sessions = pool, sessions

    async def is_ready(self) -> bool:
        async with self._pool.connection() as conn:
            row = await (await conn.execute("SELECT to_regclass('j01_workspaces'), "
                                           "to_regclass('j01_memberships')")).fetchone()
            return row is not None and len(row) == 2 and all(value is not None for value in row)

    async def access_for_context(self, *, user_id: str, context_id: str,
                                 workspace_id: str) -> WorkspaceAccessRecord | None:
        query = _READ.replace("SELECT c.digest,", "SELECT w.name, m.role, m.revision, m.active, c.digest,")
        query = query.replace("WHERE c.digest = %s", "JOIN j01_memberships m ON m.user_id=a.user_id "
            "JOIN j01_workspaces w ON w.workspace_id=m.workspace_id "
            "WHERE a.user_id=%s AND s.context_id=%s AND w.workspace_id=%s "
            "AND c.state='active' AND c.generation=s.generation")
        async with self._pool.connection() as conn:
            row = await (await conn.execute(query, (user_id, context_id, workspace_id))).fetchone()
        if row is None:
            return None
        snapshot = _snapshot(row[4:])
        if snapshot is None or not isinstance(row[0], str) or type(row[3]) is not bool:
            raise ValueError("invalid workspace record")
        checked = self._sessions._checked(snapshot, snapshot.credential_digest)
        membership = Membership.model_validate({"role": row[1], "revision": row[2]}, strict=True)
        return WorkspaceAccessRecord(session=self._sessions._record(checked), workspace_id=workspace_id,
                                     workspace_name=row[0], membership=membership, membership_active=row[3])
