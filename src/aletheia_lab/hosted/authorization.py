"""Current session, membership and source-parent policy in caller transactions."""
from __future__ import annotations

import re
from dataclasses import dataclass

from aletheia_lab.hosted_auth.postgres_sessions import Connection

_ROLES = frozenset({"owner", "admin", "analyst", "reviewer", "viewer"})
_WRITERS = frozenset({"owner", "admin", "analyst"})
_ROLE_ACTIONS = {
    "read": _ROLES,
    "import": _WRITERS,
    "preview": _WRITERS,
    "confirm": _WRITERS,
    "analyze": _WRITERS,
    "refresh": _WRITERS,
    "update": _WRITERS,
    "review": frozenset({"owner", "admin", "analyst", "reviewer"}),
    "export": _ROLES,
    "delete": _WRITERS,
    "manage_members": frozenset({"owner", "admin"}),
    "transfer_owner": frozenset({"owner"}),
}
_SOURCE_ACTIONS = {"read": "read", "import": "write", "preview": "write", "confirm": "write",
                   "analyze": "write", "refresh": "write", "update": "write", "review": "review", "export": "export", "delete": "delete"}


class AuthorizationDenied(PermissionError):
    def __init__(self) -> None:
        super().__init__("The requested resource is unavailable.")


@dataclass(frozen=True)
class Authorization:
    user_id: str
    role: str
    revision: int
    workspace_id: str


def role_allows(role: str, action: str) -> bool:
    return role in _ROLE_ACTIONS.get(action, frozenset())


def record_source_filter(auth: Authorization) -> tuple[str, tuple[object, ...]]:
    """Filter the fixed catalog alias r by its native source before pagination."""
    if not role_allows(auth.role, "read"):
        raise AuthorizationDenied()
    native = (
        "(r.record_type NOT IN ('upload_snapshot','upload_result') OR EXISTS ("
        "SELECT 1 FROM k05_records n JOIN j01_sources s "
        "ON s.workspace_id=n.workspace_id AND s.source_id=n.source_id "
        "WHERE n.workspace_id=r.workspace_id AND n.project_id=r.project_id "
        "AND n.record_type='upload_snapshot' AND n.record_id=CASE "
        "WHEN r.record_type='upload_snapshot' THEN r.record_id ELSE r.snapshot_id END "
        "AND (%s OR s.creator_id=%s OR EXISTS (SELECT 1 FROM j01_source_grants g "
        "WHERE g.workspace_id=s.workspace_id AND g.source_id=s.source_id AND g.user_id=%s "
        "AND 'read'=ANY(g.actions)))))"
    )
    comparison = (
        "(r.schema_version<>'hosted-upload-generation-comparison/v1' OR ("
        "(SELECT count(*) FROM k05_record_parents p JOIN k05_records n ON n.workspace_id=p.workspace_id "
        "AND n.project_id=p.project_id AND n.record_id=p.parent_id JOIN j01_sources s "
        "ON s.workspace_id=n.workspace_id AND s.source_id=n.source_id "
        "WHERE p.workspace_id=r.workspace_id AND p.project_id=r.project_id AND p.record_id=r.record_id "
        "AND p.relation IN ('before_snapshot','after_snapshot') AND n.record_type='upload_snapshot' "
        "AND (%s OR s.creator_id=%s OR EXISTS (SELECT 1 FROM j01_source_grants g "
        "WHERE g.workspace_id=s.workspace_id AND g.source_id=s.source_id AND g.user_id=%s "
        "AND 'read'=ANY(g.actions))))=2))"
    )
    arguments = (auth.role in ('owner', 'admin'), auth.user_id, auth.user_id)
    return native + ' AND ' + comparison, arguments + arguments


async def current_session(conn: Connection, digest: str) -> str:
    """Lock account before session; the fresh statement observes concurrent logout."""
    if conn.autocommit:
        raise RuntimeError("transactional connection required")
    if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise ValueError("credential digest required")
    row = await (await conn.execute(
        "SELECT s.user_id FROM j01_session_credentials c JOIN j01_sessions s "
        "ON s.context_id=c.context_id WHERE c.digest=%s", (digest,),
    )).fetchone()
    if row is None or not isinstance(row[0], str):
        raise AuthorizationDenied()
    user_id = row[0]
    account = await (await conn.execute(
        "SELECT user_id FROM hosted_identity_bindings WHERE user_id=%s FOR SHARE", (user_id,),
    )).fetchone()
    if account is None:
        raise AuthorizationDenied()
    current = await (await conn.execute(
        "SELECT s.user_id FROM j01_session_credentials c JOIN j01_sessions s ON s.context_id=c.context_id "
        "JOIN hosted_identity_bindings a ON a.user_id=s.user_id "
        "WHERE c.digest=%s AND s.user_id=%s AND c.state='active' AND c.generation=s.generation "
        "AND s.state='active' AND s.session_epoch=a.account_epoch AND a.enabled AND a.email_verified "
        "AND s.idle_expires_at>clock_timestamp() AND s.absolute_expires_at>clock_timestamp() "
        "FOR SHARE OF s,c", (digest, user_id),
    )).fetchone()
    if current is None or current[0] != user_id:
        raise AuthorizationDenied()
    return user_id


class PostgresAuthorization:
    async def authorize(self, conn: Connection, digest: str, workspace_id: str, action: str) -> Authorization:
        user_id = await current_session(conn, digest)
        workspace = await (await conn.execute(
            "SELECT workspace_id FROM j01_workspaces WHERE workspace_id=%s FOR SHARE", (workspace_id,),
        )).fetchone()
        if workspace is None:
            raise AuthorizationDenied()
        row = await (await conn.execute(
            "SELECT role,revision,active FROM j01_memberships WHERE workspace_id=%s AND user_id=%s FOR SHARE",
            (workspace_id, user_id),
        )).fetchone()
        if (row is None or len(row) != 3 or row[2] is not True or not isinstance(row[0], str)
                or type(row[1]) is not int or row[1] < 0 or not role_allows(row[0], action)):
            raise AuthorizationDenied()
        return Authorization(user_id, row[0], row[1], workspace_id)

    async def authorize_source(self, conn: Connection, auth: Authorization, workspace_id: str,
                               source_id: str, action: str) -> None:
        grant_action = _SOURCE_ACTIONS.get(action)
        if auth.workspace_id != workspace_id or grant_action is None or not role_allows(auth.role, action):
            raise AuthorizationDenied()
        member = await (await conn.execute(
            "SELECT role,revision,active FROM j01_memberships WHERE workspace_id=%s AND user_id=%s FOR SHARE",
            (workspace_id, auth.user_id),
        )).fetchone()
        if member != (auth.role, auth.revision, True):
            raise AuthorizationDenied()
        row = await (await conn.execute(
            "SELECT creator_id FROM j01_sources WHERE source_id=%s AND workspace_id=%s FOR SHARE",
            (source_id, workspace_id),
        )).fetchone()
        if row is None:
            raise AuthorizationDenied()
        if auth.role in ("owner", "admin"):
            return
        # Analyst deletion always requires an explicit grant, including for its own source.
        if row[0] == auth.user_id and action != "delete":
            return
        grant = await (await conn.execute(
            "SELECT actions FROM j01_source_grants WHERE workspace_id=%s AND source_id=%s AND user_id=%s FOR SHARE",
            (workspace_id, source_id, auth.user_id),
        )).fetchone()
        if (grant is None or not isinstance(grant[0], list)
                or "read" not in grant[0] or grant_action not in grant[0]):
            raise AuthorizationDenied()

    async def authorize_record(self, conn: Connection, auth: Authorization, workspace_id: str,
                               project_id: str, record_id: str, action: str, *,
                               for_update: bool = False) -> str:
        """Require project and exact native parent source policy in one transaction."""
        from psycopg import sql

        await self.authorize_project(conn, auth, workspace_id, project_id, action)
        lock = " FOR UPDATE" if for_update else " FOR SHARE"
        row = await (await conn.execute(
            sql.SQL(
                'SELECT record_type,source_id,snapshot_id FROM k05_records WHERE workspace_id=%s AND project_id=%s AND '
                'record_id=%s{}'
            ).format(sql.SQL(lock)).as_string(),
            (workspace_id, project_id, record_id),
        )).fetchone()
        if row is None or len(row) != 3 or not isinstance(row[0], str):
            raise AuthorizationDenied()
        source = row[1]
        if row[0] == "upload_result":
            parent = await (await conn.execute(
                "SELECT source_id FROM k05_records WHERE workspace_id=%s AND project_id=%s "
                "AND record_id=%s AND record_type='upload_snapshot' FOR SHARE",
                (workspace_id, project_id, row[2]),
            )).fetchone()
            source = None if parent is None else parent[0]
        if row[0] in ("upload_snapshot", "upload_result"):
            if not isinstance(source, str):
                raise AuthorizationDenied()
            await self.authorize_source(conn, auth, workspace_id, source, action)
        elif row[0] == 'snapshot_comparison':
            await self._authorize_comparison_sources(conn, auth, workspace_id, project_id, record_id, action)
        return row[0]

    async def _authorize_comparison_sources(self, conn: Connection, auth: Authorization,
                                            workspace_id: str, project_id: str, record_id: str,
                                            action: str) -> None:
        version = await (await conn.execute('SELECT schema_version FROM k05_records '
            'WHERE workspace_id=%s AND record_id=%s', (workspace_id, record_id))).fetchone()
        if version == ('hosted-upload-generation-comparison/v1',):
            cursor = await conn.execute('SELECT p.relation,n.source_id FROM k05_record_parents p '
                'JOIN k05_records n ON n.workspace_id=p.workspace_id AND n.project_id=p.project_id '
                'AND n.record_id=p.parent_id WHERE p.workspace_id=%s AND p.project_id=%s AND p.record_id=%s '
                "AND n.record_type='upload_snapshot' LIMIT 3 FOR SHARE OF n", (workspace_id, project_id, record_id))
            parents = []
            for _ in range(3):
                parent = await cursor.fetchone()
                if parent is None:
                    break
                parents.append(parent)
            if (len(parents) != 2 or {p[0] for p in parents} != {'before_snapshot', 'after_snapshot'}
                    or any(not isinstance(p[1], str) for p in parents)):
                raise AuthorizationDenied()
            for _, parent_source in parents:
                if not isinstance(parent_source, str):
                    raise AuthorizationDenied()
                await self.authorize_source(conn, auth, workspace_id, parent_source, action)

    async def authorize_project(self, conn: Connection, auth: Authorization, workspace_id: str,
                                project_id: str, action: str, *, allow_deleted: bool = False) -> None:
        """Authorize the canonical project in the caller's current-auth transaction."""
        grant_action = _SOURCE_ACTIONS.get(action)
        if auth.workspace_id != workspace_id or grant_action is None or not role_allows(auth.role, action):
            raise AuthorizationDenied()
        member = await (await conn.execute(
            "SELECT role,revision,active FROM j01_memberships WHERE workspace_id=%s AND user_id=%s FOR SHARE",
            (workspace_id, auth.user_id),
        )).fetchone()
        if member != (auth.role, auth.revision, True):
            raise AuthorizationDenied()
        project = await (await conn.execute(
            "SELECT creator_id,revision FROM k05_projects WHERE workspace_id=%s AND project_id=%s FOR SHARE",
            (workspace_id, project_id),
        )).fetchone()
        if (project is None or len(project) != 2 or not isinstance(project[0], str)
                or type(project[1]) is not int or project[1] < 0):
            raise AuthorizationDenied()
        # A separate statement observes tombstones committed while the project lock waited.
        deleted = await (await conn.execute(
            "SELECT 1 FROM k05_records WHERE workspace_id=%s AND project_id=%s AND record_type='deletion' LIMIT 1",
            (workspace_id, project_id),
        )).fetchone()
        if deleted is not None and not allow_deleted:
            raise AuthorizationDenied()
        if auth.role in ("owner", "admin"):
            return
        if project[0] == auth.user_id and action != "delete":
            return
        grant = await (await conn.execute(
            "SELECT actions FROM j01_project_grants WHERE workspace_id=%s AND project_id=%s AND user_id=%s FOR SHARE",
            (workspace_id, project_id, auth.user_id),
        )).fetchone()
        if (grant is None or not isinstance(grant[0], list)
                or "read" not in grant[0] or grant_action not in grant[0]):
            raise AuthorizationDenied()
