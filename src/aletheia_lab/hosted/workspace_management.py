"""Workspace onboarding and membership transactions over the existing identity store."""

from __future__ import annotations

import re
import secrets
from uuid import uuid4

from pydantic import SecretStr

from aletheia_lab.hosted_auth.postgres_sessions import Connection, Pool, _digest
from aletheia_lab.hosted_auth.repositories import ValidatedIdentity
from aletheia_lab.project.identity import content_hasher

from .action_audit import record_action
from .authorization import AuthorizationDenied, PostgresAuthorization, current_session
from .cursors import CursorInvalid, ScopedCursors
from .read_projection_migration import stamp_catalog_revision
from .transactions import run_transaction

VERSION = "product-api/v1-draft"
ROLES = frozenset(("owner", "admin", "analyst", "reviewer", "viewer"))
GRANT_ACTIONS = frozenset(("read", "write", "review", "export", "delete"))

# Required by these transactions and the deferred last-owner guard; health never grants rights.
_WRITE_COLUMNS: dict[str, dict[str, tuple[str, ...]]] = {
    "INSERT": {
        "j01_workspaces": ("workspace_id", "name"),
        "j01_memberships": ("workspace_id", "user_id", "role", "active", "revision"),
        "j01_invitations": ("invitation_id", "workspace_id", "user_id", "role", "creator_id",
                            "creator_revision", "token_digest", "expires_at"),
        "j01_source_grants": ("workspace_id", "source_id", "user_id", "actions", "revision"),
    },
    "UPDATE": {
        "hosted_identity_bindings": ("onboarding", "last_workspace_id"),
        "j01_workspaces": ("name",),
        "j01_memberships": ("role", "active", "revision"),
        "j01_invitations": ("revoked", "accepted_at"),
        "j01_source_grants": ("actions", "revision"),
    },
}


class WorkspaceDenied(PermissionError):
    def __init__(self) -> None:
        super().__init__("The workspace operation is not permitted.")


class WorkspaceConflict(ValueError):
    def __init__(self, code: str = "revision_conflict") -> None:
        self.code = code
        super().__init__("The workspace state has changed.")


class WorkspaceInvitationError(WorkspaceDenied):
    def __init__(self, status: int, code: str) -> None:
        self.status, self.code = status, code
        super().__init__()


def _revision(value: object) -> int:
    if type(value) is not int or not 0 <= value <= 9223372036854775806:
        raise ValueError("Nonnegative revision required.")
    return value


def _member(workspace: str, user: str, row: tuple[object, ...], display_name: object = None) -> dict[str, object]:
    return {"schema_version": VERSION, "workspace_id": workspace, "user_id": user,
            "role": row[0], "active": row[1], "revision": row[2], "display_name": display_name}


async def provision_identity(connection: Connection, identity: ValidatedIdentity, *,
                             expected_issuer: str, display_name: str) -> dict[str, object]:
    """Trusted operator only, after OIDC validation; never exposed as an HTTP route."""
    identity = ValidatedIdentity.model_validate(identity.model_dump())
    if (connection.autocommit or not expected_issuer or identity.issuer != expected_issuer
            or not identity.email_verified or identity.email is None
            or not isinstance(display_name, str) or not 1 <= len(display_name.strip()) <= 256):
        raise WorkspaceDenied()
    user = "user_" + uuid4().hex
    await connection.execute(
        "INSERT INTO hosted_identity_bindings(issuer,subject,user_id,display_name,email,"
        "email_verified,enabled,account_epoch,onboarding,last_workspace_id) "
        "VALUES (%s,%s,%s,%s,%s,true,true,0,'needs_workspace',NULL) "
        "ON CONFLICT (issuer,subject) DO NOTHING",
        (identity.issuer, identity.subject.get_secret_value(), user, display_name.strip(),
         identity.email.get_secret_value()))
    row = await (await connection.execute(
        "SELECT user_id,onboarding FROM hosted_identity_bindings WHERE issuer=%s AND subject=%s",
        (identity.issuer, identity.subject.get_secret_value()))).fetchone()
    if row is None:
        raise WorkspaceDenied()
    return {"user_id": row[0], "onboarding": row[1]}


class WorkspaceManagement:
    def __init__(self, pool: Pool, *, cursors: ScopedCursors | None = None, audit: bool = False) -> None:
        self._pool, self._cursors = pool, cursors
        self._audit = audit
        self._authorization = PostgresAuthorization()

    async def is_ready(self) -> bool:
        try:
            from .workspace_migration import verify

            async with self._pool.connection() as conn:
                await conn.execute("SELECT invitation_id,workspace_id,user_id,role,creator_id,creator_revision,"
                                   "token_digest,expires_at,revoked,accepted_at FROM j01_invitations LIMIT 0")
                await conn.execute("SELECT workspace_id,source_id,user_id,actions,revision FROM j01_source_grants LIMIT 0")
                required = [(table, column, privilege) for privilege, tables in _WRITE_COLUMNS.items()
                            for table, columns in tables.items() for column in columns]
                writable = await (await conn.execute(
                    "SELECT COALESCE(bool_and(has_column_privilege(current_user,permission.relation,"
                    "permission.column_name,permission.privilege)),false) "
                    "AND has_any_column_privilege(current_user,'j01_sessions','UPDATE') "
                    "AND has_any_column_privilege(current_user,'j01_session_credentials','UPDATE') "
                    "AND has_any_column_privilege(current_user,'j01_sources','UPDATE') "
                    "FROM unnest(%s::text[],%s::text[],%s::text[]) "
                    "AS permission(relation,column_name,privilege)",
                    ([row[0] for row in required], [row[1] for row in required],
                     [row[2] for row in required]))).fetchone()
                return writable == (True,) and await verify(conn)
        except Exception:
            return False

    async def can_create_workspace(self, *, credential_digest: SecretStr) -> bool:
        """Session hint uses the exact same current server gate as create."""
        async with self._pool.connection() as conn:
            user = await self._accounts(conn, _digest(credential_digest))
            return await self._can_create(conn, user)

    async def _accounts(self, conn: Connection, digest: str, *targets: str) -> str:
        if conn.autocommit:
            raise RuntimeError("Transactional connection required.")
        row = await (await conn.execute(
            "SELECT s.user_id FROM j01_session_credentials c JOIN j01_sessions s "
            "ON s.context_id=c.context_id WHERE c.digest=%s", (digest,))).fetchone()
        if row is None or not isinstance(row[0], str):
            raise WorkspaceDenied()
        # Account rows precede workspace/member rows in every mutation.
        for user in sorted(set((row[0], *targets))):
            await conn.execute("SELECT user_id FROM hosted_identity_bindings WHERE user_id=%s FOR UPDATE", (user,))
        try:
            return await current_session(conn, digest)
        except AuthorizationDenied:
            raise WorkspaceDenied() from None

    async def _workspace(self, conn: Connection, workspace: str) -> str:
        row = await (await conn.execute(
            "SELECT name FROM j01_workspaces WHERE workspace_id=%s FOR UPDATE", (workspace,))).fetchone()
        if row is None or not isinstance(row[0], str):
            raise WorkspaceDenied()
        return row[0]

    async def _membership(self, conn: Connection, workspace: str, user: str) -> tuple[object, ...]:
        row = await (await conn.execute(
            "SELECT role,active,revision FROM j01_memberships WHERE workspace_id=%s AND user_id=%s FOR UPDATE",
            (workspace, user))).fetchone()
        if row is None:
            raise WorkspaceDenied()
        return row

    async def _manager(self, conn: Connection, workspace: str, user: str) -> tuple[object, ...]:
        row = await self._membership(conn, workspace, user)
        account = await (await conn.execute("SELECT enabled,email_verified FROM hosted_identity_bindings WHERE user_id=%s",
                                           (user,))).fetchone()
        if row[1] is not True or row[0] not in ("owner", "admin") or account != (True, True):
            raise WorkspaceDenied()
        return row

    async def _onboarding(self, conn: Connection, user: str) -> None:
        await conn.execute(
            "UPDATE hosted_identity_bindings SET onboarding=CASE WHEN EXISTS(SELECT 1 FROM j01_memberships "
            "WHERE user_id=%s AND active) THEN 'ready' ELSE 'needs_workspace' END, "
            "last_workspace_id=(SELECT workspace_id FROM j01_memberships WHERE user_id=%s AND active "
            "ORDER BY workspace_id LIMIT 1) WHERE user_id=%s", (user, user, user))

    async def _can_create(self, conn: Connection, user: str) -> bool:
        row = await (await conn.execute(
            "SELECT onboarding FROM hosted_identity_bindings WHERE user_id=%s", (user,))).fetchone()
        existing = await (await conn.execute(
            "SELECT 1 FROM j01_memberships WHERE user_id=%s AND active LIMIT 1", (user,))).fetchone()
        return row == ("needs_workspace",) and existing is None

    async def create(self, *, credential_digest: SecretStr, name: str) -> dict[str, object]:
        if not isinstance(name, str) or not 1 <= len(name.strip()) <= 256:
            raise ValueError("Bounded workspace name required.")
        async def commit(conn: Connection) -> dict[str, object]:
            user = await self._accounts(conn, _digest(credential_digest))
            if not await self._can_create(conn, user):
                raise WorkspaceDenied()
            workspace = "ws_" + uuid4().hex
            await conn.execute("INSERT INTO j01_workspaces(workspace_id,name) VALUES (%s,%s)", (workspace, name.strip()))
            await conn.execute("INSERT INTO j01_memberships VALUES (%s,%s,'owner',true,1)", (workspace, user))
            await conn.execute("UPDATE hosted_identity_bindings SET onboarding='ready',last_workspace_id=%s "
                               "WHERE user_id=%s", (workspace, user))
            if self._audit:
                await record_action(conn, actor_id=user, workspace_id=workspace, action="workspace.create",
                    effect_id=workspace, revision=1)
            return {"schema_version": VERSION, "workspace_id": workspace, "workspace_name": name.strip(),
                    "membership": {"role": "owner", "revision": 1}}

        return await run_transaction(self._pool, commit)

    async def _page_binding(self, conn: Connection, digest: str, user: str, query: dict[str, object], *,
                            workspace: str | None = None, revision: int | None = None) -> dict[str, object]:
        session = await (await conn.execute("SELECT s.context_id,a.account_epoch FROM j01_session_credentials c "
            "JOIN j01_sessions s ON s.context_id=c.context_id JOIN hosted_identity_bindings a ON a.user_id=s.user_id "
            "WHERE c.digest=%s", (digest,))).fetchone()
        if session is None or not isinstance(session[0], str) or type(session[1]) is not int:
            raise WorkspaceDenied()
        binding: dict[str, object] = {"user_id": user, "context_id": session[0], "account_epoch": session[1], "query": query}
        if workspace is not None:
            binding.update(workspace_id=workspace, membership_revision=revision)
        return binding

    def _page_after(self, purpose: str, binding: dict[str, object], cursor: str | None) -> str:
        if cursor is None:
            return ""
        if self._cursors is None:
            raise TimeoutError("Pagination is unavailable.")
        value = self._cursors.read(purpose=purpose, binding=binding, cursor=cursor)
        if (set(value) != {"after"} or not isinstance(value["after"], str)
                or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", value["after"]) is None):
            raise CursorInvalid()
        return value["after"]

    def _page_next(self, purpose: str, binding: dict[str, object], after: object | None) -> str | None:
        if after is None:
            return None
        if self._cursors is None:
            raise TimeoutError("Pagination is unavailable.")
        if not isinstance(after, str):
            raise ValueError("Invalid stored continuation identity.")
        return self._cursors.issue(purpose=purpose, binding=binding, value={"after": after})

    async def list_workspaces(self, *, credential_digest: SecretStr, limit: int = 50,
                              cursor: str | None = None) -> dict[str, object]:
        if type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError("A bounded workspace page limit is required.")
        async with self._pool.connection() as conn:
            digest = _digest(credential_digest)
            try:
                user = await current_session(conn, digest)
            except AuthorizationDenied:
                raise WorkspaceDenied() from None
            binding = await self._page_binding(conn, digest, user, {"limit": limit})
            after = self._page_after("workspaces", binding, cursor)
            result = await conn.execute("SELECT w.workspace_id,w.name,m.role,m.revision FROM j01_workspaces w "
                "JOIN j01_memberships m ON m.workspace_id=w.workspace_id WHERE m.user_id=%s AND m.active "
                "AND w.workspace_id>%s ORDER BY w.workspace_id LIMIT %s FOR SHARE OF w,m", (user, after, limit + 1))
            rows = []
            while (row := await result.fetchone()) is not None:
                rows.append(row)
            return {"schema_version": VERSION, "items": [{"schema_version": VERSION, "workspace_id": row[0],
                "workspace_name": row[1], "membership": {"role": row[2], "revision": row[3]}} for row in rows[:limit]],
                "next_cursor": self._page_next("workspaces", binding, rows[limit - 1][0] if len(rows) > limit else None),
                "total": None, "total_kind": "unknown"}

    async def read_workspace(self, *, credential_digest: SecretStr, workspace_id: str) -> dict[str, object]:
        async with self._pool.connection() as conn:
            auth = await self._authorization.authorize(conn, _digest(credential_digest), workspace_id, "read")
            row = await (await conn.execute("SELECT name FROM j01_workspaces WHERE workspace_id=%s", (workspace_id,))).fetchone()
            if row is None:
                raise AuthorizationDenied()
            return {"schema_version": VERSION, "workspace_id": workspace_id, "workspace_name": row[0],
                    "membership": {"role": auth.role, "revision": auth.revision}}

    async def list_members(self, *, credential_digest: SecretStr, workspace_id: str,
                           limit: int = 50, cursor: str | None = None) -> dict[str, object]:
        if type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError("A bounded member page limit is required.")
        async with self._pool.connection() as conn:
            digest = _digest(credential_digest)
            try:
                auth = await self._authorization.authorize(conn, digest, workspace_id, "manage_members")
            except AuthorizationDenied:
                raise WorkspaceDenied() from None
            binding = await self._page_binding(conn, digest, auth.user_id, {"limit": limit},
                                                workspace=workspace_id, revision=auth.revision)
            after = self._page_after("workspace_members", binding, cursor)
            result = await conn.execute("SELECT m.user_id,m.role,m.active,m.revision,a.display_name FROM j01_memberships m "
                "JOIN hosted_identity_bindings a ON a.user_id=m.user_id WHERE m.workspace_id=%s AND m.user_id>%s "
                "ORDER BY m.user_id LIMIT %s", (workspace_id, after, limit + 1))
            rows: list[tuple[str, object, object, object, object]] = []
            while (row := await result.fetchone()) is not None:
                if len(row) != 5 or not isinstance(row[0], str):
                    raise ValueError("Invalid stored member identity.")
                rows.append((row[0], row[1], row[2], row[3], row[4]))
            return {"schema_version": VERSION, "workspace_id": workspace_id,
                    "items": [_member(workspace_id, row[0], row[1:4], row[4]) for row in rows[:limit]],
                    "next_cursor": self._page_next("workspace_members", binding, rows[limit - 1][0] if len(rows) > limit else None),
                    "total": None, "total_kind": "unknown"}

    async def mutate_member(self, *, credential_digest: SecretStr, workspace_id: str, user_id: str,
                            expected_revision: int, role: str | None = None,
                            active: bool | None = None) -> dict[str, object]:
        _revision(expected_revision)
        if (role is not None and role not in ROLES) or (active is not None and type(active) is not bool) or (role is None and active is None):
            raise ValueError("Membership mutation required.")
        async def commit(conn: Connection) -> dict[str, object]:
            actor = await self._accounts(conn, _digest(credential_digest), user_id)
            await self._workspace(conn, workspace_id)
            acting = await self._membership(conn, workspace_id, actor)
            target = await self._membership(conn, workspace_id, user_id)
            leaving = actor == user_id and role is None and active is False
            if acting[1] is not True or (not leaving and acting[0] not in ("owner", "admin")):
                raise WorkspaceDenied()
            if role == "owner" or (acting[0] == "admin" and target[0] == "owner"):
                raise WorkspaceDenied()
            if target[2] != expected_revision:
                raise WorkspaceConflict()
            replacement = target[0] if role is None else role
            enabled = target[1] if active is None else active
            if target[0] == "owner" and target[1] is True and (replacement != "owner" or enabled is False):
                other = await (await conn.execute("SELECT 1 FROM j01_memberships m JOIN hosted_identity_bindings a "
                    "ON a.user_id=m.user_id WHERE m.workspace_id=%s AND m.active AND m.role='owner' "
                    "AND a.enabled AND a.email_verified AND m.user_id<>%s LIMIT 1", (workspace_id, user_id))).fetchone()
                if other is None:
                    raise WorkspaceDenied()
            if replacement == "owner" and target[1] is not True and enabled is True:
                raise WorkspaceDenied()
            await conn.execute("UPDATE j01_memberships SET role=%s,active=%s,revision=revision+1 "
                               "WHERE workspace_id=%s AND user_id=%s", (replacement, enabled, workspace_id, user_id))
            await self._onboarding(conn, user_id)
            account = await (await conn.execute("SELECT display_name FROM hosted_identity_bindings WHERE user_id=%s", (user_id,))).fetchone()
            if self._audit:
                await record_action(conn, actor_id=actor, workspace_id=workspace_id, action="member.update",
                    effect_id=user_id, revision=expected_revision + 1)
            return _member(workspace_id, user_id, (replacement, enabled, expected_revision + 1), None if account is None else account[0])

        return await run_transaction(self._pool, commit)

    async def transfer_owner(self, *, credential_digest: SecretStr, workspace_id: str, user_id: str,
                             expected_revision: int) -> dict[str, object]:
        _revision(expected_revision)
        async def commit(conn: Connection) -> dict[str, object]:
            actor = await self._accounts(conn, _digest(credential_digest), user_id)
            await self._workspace(conn, workspace_id)
            acting = await self._membership(conn, workspace_id, actor)
            target = await self._membership(conn, workspace_id, user_id)
            account = await (await conn.execute("SELECT enabled,email_verified FROM hosted_identity_bindings WHERE user_id=%s",
                                               (user_id,))).fetchone()
            if (actor == user_id or acting[0] != "owner" or acting[1] is not True
                    or target[1] is not True or account != (True, True)):
                raise WorkspaceDenied()
            _revision(acting[2])
            if target[2] != expected_revision:
                raise WorkspaceConflict()
            await conn.execute("UPDATE j01_memberships SET role='admin',revision=revision+1 "
                               "WHERE workspace_id=%s AND user_id=%s", (workspace_id, actor))
            await conn.execute("UPDATE j01_memberships SET role='owner',revision=revision+1 "
                               "WHERE workspace_id=%s AND user_id=%s", (workspace_id, user_id))
            account = await (await conn.execute("SELECT display_name FROM hosted_identity_bindings WHERE user_id=%s", (user_id,))).fetchone()
            if self._audit:
                await record_action(conn, actor_id=actor, workspace_id=workspace_id, action="owner.transfer",
                    effect_id=user_id, revision=expected_revision + 1)
            return _member(workspace_id, user_id, ("owner", True, expected_revision + 1), None if account is None else account[0])

        return await run_transaction(self._pool, commit)

    async def list_invitations(self, *, credential_digest: SecretStr, workspace_id: str,
                               limit: int = 50, cursor: str | None = None) -> dict[str, object]:
        if type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError("A bounded invitation page limit is required.")
        async with self._pool.connection() as conn:
            digest = _digest(credential_digest)
            try:
                auth = await self._authorization.authorize(conn, digest, workspace_id, "manage_members")
            except AuthorizationDenied:
                raise WorkspaceDenied() from None
            binding = await self._page_binding(conn, digest, auth.user_id, {"limit": limit},
                                                workspace=workspace_id, revision=auth.revision)
            after = self._page_after("workspace_invitations", binding, cursor)
            result = await conn.execute("SELECT i.invitation_id,i.user_id,i.role,i.expires_at,a.display_name "
                "FROM j01_invitations i JOIN hosted_identity_bindings a ON a.user_id=i.user_id WHERE i.workspace_id=%s "
                "AND NOT i.revoked AND i.accepted_at IS NULL AND i.expires_at>clock_timestamp() AND i.invitation_id>%s "
                "ORDER BY i.invitation_id LIMIT %s", (workspace_id, after, limit + 1))
            rows = []
            while (row := await result.fetchone()) is not None:
                rows.append(row)
            return {"schema_version": VERSION, "workspace_id": workspace_id, "items": [
                {"schema_version": VERSION, "workspace_id": workspace_id, "invitation_id": row[0], "user_id": row[1],
                 "role": row[2], "expires_at": row[3], "display_name": row[4]} for row in rows[:limit]],
                "next_cursor": self._page_next("workspace_invitations", binding, rows[limit - 1][0] if len(rows) > limit else None),
                "total": None, "total_kind": "unknown"}

    async def lookup_invitation_targets(self, *, credential_digest: SecretStr, workspace_id: str, email: str,
                                        limit: int = 20, cursor: str | None = None) -> dict[str, object]:
        if (not isinstance(email, str) or not 3 <= len(email) <= 254 or "@" not in email
                or any(ord(char) <= 32 or ord(char) == 127 for char in email)
                or type(limit) is not int or not 1 <= limit <= 20):
            raise ValueError("An exact email and bounded target page are required.")
        async with self._pool.connection() as conn:
            digest = _digest(credential_digest)
            try:
                auth = await self._authorization.authorize(conn, digest, workspace_id, "manage_members")
            except AuthorizationDenied:
                raise WorkspaceDenied() from None
            binding = await self._page_binding(conn, digest, auth.user_id,
                {"limit": limit, "email_sha256": content_hasher(email.encode()).hexdigest()},
                workspace=workspace_id, revision=auth.revision)
            after = self._page_after("invitation_targets", binding, cursor)
            result = await conn.execute("SELECT user_id,display_name FROM hosted_identity_bindings "
                "WHERE email=%s AND enabled AND email_verified AND user_id>%s ORDER BY user_id LIMIT %s",
                (email, after, limit + 1))
            rows = []
            while (row := await result.fetchone()) is not None:
                rows.append(row)
            return {"schema_version": VERSION, "workspace_id": workspace_id,
                "items": [{"user_id": row[0], "display_name": row[1]} for row in rows[:limit]],
                "next_cursor": self._page_next("invitation_targets", binding, rows[limit - 1][0] if len(rows) > limit else None),
                "total": None, "total_kind": "unknown"}

    async def read_source_grant(self, *, credential_digest: SecretStr, workspace_id: str,
                                source_id: str, user_id: str) -> dict[str, object]:
        async with self._pool.connection() as conn:
            digest = _digest(credential_digest)
            try:
                auth = await self._authorization.authorize(conn, digest, workspace_id, "manage_members")
            except AuthorizationDenied:
                raise WorkspaceDenied() from None
            await self._authorization.authorize_source(conn, auth, workspace_id, source_id, "read")
            member = await (await conn.execute("SELECT 1 FROM j01_memberships WHERE workspace_id=%s AND user_id=%s",
                                               (workspace_id, user_id))).fetchone()
            if member is None:
                raise AuthorizationDenied()
            row = await (await conn.execute("SELECT actions,revision FROM j01_source_grants WHERE workspace_id=%s "
                "AND source_id=%s AND user_id=%s", (workspace_id, source_id, user_id))).fetchone()
            return {"schema_version": VERSION, "workspace_id": workspace_id, "source_id": source_id,
                    "user_id": user_id, "actions": [] if row is None else row[0], "revision": 0 if row is None else row[1]}

    async def invite(self, *, credential_digest: SecretStr, workspace_id: str, user_id: str, role: str,
                     expires_in_seconds: int) -> dict[str, object]:
        if role not in ROLES - {"owner"} or type(expires_in_seconds) is not int or not 60 <= expires_in_seconds <= 604800:
            raise ValueError("Bounded non-owner invitation required.")
        async def commit(conn: Connection) -> dict[str, object]:
            actor = await self._accounts(conn, _digest(credential_digest), user_id)
            await self._workspace(conn, workspace_id)
            acting = await self._manager(conn, workspace_id, actor)
            target = await (await conn.execute("SELECT 1 FROM hosted_identity_bindings WHERE user_id=%s "
                "AND enabled AND email_verified", (user_id,))).fetchone()
            member = await (await conn.execute("SELECT 1 FROM j01_memberships WHERE workspace_id=%s "
                "AND user_id=%s AND active", (workspace_id, user_id))).fetchone()
            if target is None or member is not None:
                raise WorkspaceDenied()
            invitation, token = "inv_" + uuid4().hex, secrets.token_urlsafe(32)
            row = await (await conn.execute("INSERT INTO j01_invitations(invitation_id,workspace_id,user_id,role,"
                "creator_id,creator_revision,token_digest,expires_at) VALUES (%s,%s,%s,%s,%s,%s,%s,"
                "clock_timestamp()+%s*interval '1 second') RETURNING expires_at",
                (invitation, workspace_id, user_id, role, actor, acting[2], content_hasher(token.encode()).hexdigest(),
                 expires_in_seconds))).fetchone()
            if row is None:
                raise RuntimeError("Invitation creation failed.")
            if self._audit:
                await record_action(conn, actor_id=actor, workspace_id=workspace_id, action="invitation.create", effect_id=invitation)
            return {"schema_version": VERSION, "workspace_id": workspace_id, "invitation_id": invitation,
                    "user_id": user_id, "role": role, "expires_at": row[0], "token": token}

        return await run_transaction(self._pool, commit)

    async def _validate_invitation(self, conn: Connection, workspace: str, creator: str,
                                   target: str, actor: str, row: tuple[object, ...] | None) -> tuple[object, ...]:
        if row is None or row[2] is True or row[3] is not None:
            raise WorkspaceInvitationError(404, "invitation_unavailable")
        if actor != target:
            raise WorkspaceInvitationError(403, "invitation_wrong_account")
        if row[4] is True:
            raise WorkspaceInvitationError(410, "invitation_expired")
        try:
            issuer = await self._manager(conn, str(workspace), str(creator))
        except WorkspaceDenied:
            raise WorkspaceInvitationError(404, "invitation_unavailable") from None
        if issuer[2] != row[1]:
            raise WorkspaceInvitationError(404, "invitation_unavailable")
        return row

    async def accept_invite(self, *, credential_digest: SecretStr, token: SecretStr) -> dict[str, object]:
        if not isinstance(token, SecretStr) or not 32 <= len(token.get_secret_value()) <= 128:
            raise WorkspaceInvitationError(404, "invitation_unavailable")
        token_digest = content_hasher(token.get_secret_value().encode()).hexdigest()
        async def commit(conn: Connection) -> dict[str, object]:
            found = await (await conn.execute("SELECT workspace_id,user_id,creator_id FROM j01_invitations "
                "WHERE token_digest=%s", (token_digest,))).fetchone()
            if found is None:
                raise WorkspaceInvitationError(404, "invitation_unavailable")
            workspace, target, creator = found
            if not all(isinstance(value, str) for value in found):
                raise RuntimeError("Invalid invitation record.")
            actor = await self._accounts(conn, _digest(credential_digest), str(target), str(creator))
            name = await self._workspace(conn, str(workspace))
            row = await (await conn.execute("SELECT role,creator_revision,revoked,accepted_at,expires_at<=clock_timestamp() "
                "FROM j01_invitations WHERE token_digest=%s FOR UPDATE",
                (token_digest,))).fetchone()
            row = await self._validate_invitation(conn, str(workspace), str(creator), str(target), actor, row)
            existing = await (await conn.execute("SELECT role,active,revision FROM j01_memberships "
                "WHERE workspace_id=%s AND user_id=%s FOR UPDATE", (workspace, actor))).fetchone()
            if existing is not None and (existing[1] is True or existing[0] == "owner"):
                raise WorkspaceInvitationError(404, "invitation_unavailable")
            revision = 1 if existing is None else _revision(existing[2]) + 1
            await conn.execute("INSERT INTO j01_memberships VALUES (%s,%s,%s,true,%s) "
                "ON CONFLICT(workspace_id,user_id) DO UPDATE SET role=EXCLUDED.role,active=true,revision=EXCLUDED.revision",
                (workspace, actor, row[0], revision))
            await conn.execute("UPDATE j01_invitations SET accepted_at=clock_timestamp() WHERE token_digest=%s", (token_digest,))
            await self._onboarding(conn, actor)
            if self._audit:
                await record_action(conn, actor_id=actor, workspace_id=str(workspace), action="invitation.accept",
                    effect_id=actor, revision=revision)
            return {"schema_version": VERSION, "workspace_id": workspace, "workspace_name": name,
                    "membership": {"role": row[0], "revision": revision}}

        return await run_transaction(self._pool, commit)

    async def revoke_invite(self, *, credential_digest: SecretStr, workspace_id: str,
                            invitation_id: str) -> dict[str, object]:
        async def commit(conn: Connection) -> dict[str, object]:
            actor = await self._accounts(conn, _digest(credential_digest))
            await self._workspace(conn, workspace_id)
            await self._manager(conn, workspace_id, actor)
            row = await (await conn.execute("UPDATE j01_invitations SET revoked=true WHERE invitation_id=%s "
                "AND workspace_id=%s RETURNING invitation_id", (invitation_id, workspace_id))).fetchone()
            if row is None:
                raise WorkspaceInvitationError(404, "invitation_unavailable")
            if self._audit:
                await record_action(conn, actor_id=actor, workspace_id=workspace_id, action="invitation.revoke",
                    effect_id=invitation_id)
            return {"schema_version": VERSION, "workspace_id": workspace_id,
                    "invitation_id": invitation_id, "revoked": True}

        return await run_transaction(self._pool, commit)

    async def set_source_grant(self, *, credential_digest: SecretStr, workspace_id: str, source_id: str,
                               user_id: str, actions: list[str], expected_revision: int) -> dict[str, object]:
        _revision(expected_revision)
        if (not isinstance(actions, list) or any(action not in GRANT_ACTIONS for action in actions)
                or (actions and "read" not in actions)
                or len(set(actions)) != len(actions)):
            raise ValueError("Explicit grant actions required.")
        actions = list(actions)
        async def commit(conn: Connection) -> dict[str, object]:
            actor = await self._accounts(conn, _digest(credential_digest), user_id)
            await self._workspace(conn, workspace_id)
            await self._manager(conn, workspace_id, actor)
            target = await self._membership(conn, workspace_id, user_id)
            source = await (await conn.execute("SELECT source_id FROM j01_sources WHERE workspace_id=%s AND source_id=%s",
                                               (workspace_id, source_id))).fetchone()
            if target[1] is not True:
                raise WorkspaceDenied()
            if source is None:
                raise AuthorizationDenied()
            row = await (await conn.execute("SELECT revision FROM j01_source_grants WHERE workspace_id=%s "
                "AND source_id=%s AND user_id=%s FOR UPDATE", (workspace_id, source_id, user_id))).fetchone()
            if (0 if row is None else row[0]) != expected_revision:
                raise WorkspaceConflict()
            await conn.execute("INSERT INTO j01_source_grants(workspace_id,source_id,user_id,actions,revision) "
                "VALUES (%s,%s,%s,%s,%s) ON CONFLICT(workspace_id,source_id,user_id) "
                "DO UPDATE SET actions=EXCLUDED.actions,revision=EXCLUDED.revision",
                (workspace_id, source_id, user_id, actions, expected_revision + 1))
            await stamp_catalog_revision(conn, workspace_id)
            if self._audit:
                await record_action(conn, actor_id=actor, workspace_id=workspace_id, action="source_grant.set",
                    effect_id=source_id, revision=expected_revision + 1, parent_id=source_id, subject_id=user_id)
            return {"schema_version": VERSION, "workspace_id": workspace_id, "source_id": source_id,
                    "user_id": user_id, "actions": actions, "revision": expected_revision + 1}

        return await run_transaction(self._pool, commit)


PostgresWorkspaceManagement = WorkspaceManagement
