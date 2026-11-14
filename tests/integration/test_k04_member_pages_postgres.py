"""Signed member pages over canonically migrated, UUID-owned PostgreSQL schemas."""
from __future__ import annotations

import hashlib
import os
from contextlib import asynccontextmanager

import httpx
import pytest
from itsdangerous.timed import TimestampSigner
from pydantic import SecretStr
from test_k04_http_postgres import ORIGIN, _http_identity
from test_k04_workspaces_postgres import _database, _identity, _run

from aletheia_lab.hosted.cursors import CursorExpired, CursorInvalid, ScopedCursors
from aletheia_lab.hosted.persistence_migration import migrate
from aletheia_lab.hosted.request_boundary import BodyLimits
from aletheia_lab.hosted.settings import RuntimeSettings
from aletheia_lab.hosted.workspace_http import WorkspaceApplication
from aletheia_lab.hosted.workspace_management import WorkspaceDenied, WorkspaceManagement
from aletheia_lab.hosted_auth.csrf import CsrfTokens
from aletheia_lab.hosted_auth.postgres_sessions import PostgresSessionStore
from aletheia_lab.hosted_auth.sessions import ServerSessions
from aletheia_lab.hosted_auth.settings import AuthPolicy
from aletheia_lab.hosted_contract.workspaces import MemberPage

pytestmark = [pytest.mark.integration, pytest.mark.skipif(not os.environ.get("K04_TEST_DATABASE_URL"),
    reason="Explicit K-04 PostgreSQL test URL required")]
SECRET = SecretStr("synthetic-member-pages-" + "s" * 43)


@asynccontextmanager
async def _canonical():
    async with _database(migrate_schema=False) as (pool, schema):
        async with pool.connection() as conn:
            await migrate(conn, schema)
        yield pool


async def _bulk(pool, workspace):
    async with pool.connection() as conn:
        await conn.execute("INSERT INTO hosted_identity_bindings(issuer,subject,user_id,display_name,email,email_verified,"
            "enabled,account_epoch,onboarding,last_workspace_id) SELECT 'https://issuer.example.invalid',"
            "'member_'||lpad(n::text,4,'0'),'member_'||lpad(n::text,4,'0'),'Synthetic member '||n,"
            "'member'||n||'@example.invalid',true,true,0,'ready',%s FROM generate_series(1,225) n", (workspace,))
        await conn.execute("INSERT INTO j01_memberships(workspace_id,user_id,role,active,revision) "
            "SELECT %s,user_id,'viewer',true,1 FROM hosted_identity_bindings WHERE user_id LIKE 'member_%%'", (workspace,))


async def _invite(manager, owner, workspace, user, credential):
    invitation = await manager.invite(credential_digest=owner, workspace_id=workspace, user_id=user,
                                      role="admin", expires_in_seconds=600)
    await manager.accept_invite(credential_digest=credential, token=SecretStr(invitation["token"]))


def test_real_member_pages_walk_more_than_200_and_reject_every_scope_substitution():
    async def run():
        async with _canonical() as pool:
            owner, other, admin = [await _identity(pool, user) for user in ("owner", "other", "admin")]
            manager = WorkspaceManagement(pool, cursors=ScopedCursors(SECRET))
            workspace = (await manager.create(credential_digest=owner, name="A"))["workspace_id"]
            foreign = (await manager.create(credential_digest=other, name="B"))["workspace_id"]
            await _bulk(pool, workspace)
            users, cursor = [], None
            while True:
                page = MemberPage.model_validate(await manager.list_members(credential_digest=owner,
                    workspace_id=workspace, limit=50, cursor=cursor))
                assert len(page.items) <= 50 and page.total is None and page.total_kind == "unknown"
                users.extend(item.user_id for item in page.items)
                cursor = page.next_cursor
                if cursor is None:
                    break
            assert len(users) == 226 and users == sorted(set(users)) and "other" not in users
            first = await manager.list_members(credential_digest=owner, workspace_id=workspace, limit=50)
            token = first["next_cursor"]
            assert isinstance(token, str)
            with pytest.raises(CursorInvalid):
                await manager.list_members(credential_digest=owner, workspace_id=workspace, limit=100, cursor=token)
            with pytest.raises(CursorInvalid):
                await manager.list_members(credential_digest=owner, workspace_id=workspace, cursor="x" + token[1:])
            await _invite(manager, other, foreign, "owner", owner)
            with pytest.raises(CursorInvalid):
                await manager.list_members(credential_digest=owner, workspace_id=foreign, cursor=token)
            await _invite(manager, owner, workspace, "admin", admin)
            with pytest.raises(CursorInvalid):
                await manager.list_members(credential_digest=admin, workspace_id=workspace, cursor=token)
            replacement = hashlib.sha256(b"synthetic-second-owner-session").hexdigest()
            async with pool.connection() as conn:
                await conn.execute("INSERT INTO j01_sessions SELECT 'ctx-second-owner',user_id,csrf_token,state,"
                    "issued_at,last_seen_at,rotated_at,idle_expires_at,absolute_expires_at,generation,session_epoch "
                    "FROM j01_sessions WHERE context_id='ctx-owner'")
                await conn.execute("INSERT INTO j01_session_credentials VALUES (%s,'ctx-second-owner','active',0)", (replacement,))
            with pytest.raises(CursorInvalid):
                await manager.list_members(credential_digest=SecretStr(replacement), workspace_id=workspace, cursor=token)
            await manager.mutate_member(credential_digest=owner, workspace_id=workspace, user_id="owner",
                                         expected_revision=1, active=True)
            with pytest.raises(CursorInvalid):
                await manager.list_members(credential_digest=owner, workspace_id=workspace, cursor=token)
            admin_page = await manager.list_members(credential_digest=admin, workspace_id=workspace)
            await manager.mutate_member(credential_digest=owner, workspace_id=workspace, user_id="admin",
                                         expected_revision=1, role="viewer")
            with pytest.raises(WorkspaceDenied):
                await manager.list_members(credential_digest=admin, workspace_id=workspace, cursor=admin_page["next_cursor"])
            await manager.mutate_member(credential_digest=owner, workspace_id=workspace, user_id="admin",
                                         expected_revision=2, role="admin")
            admin_page = await manager.list_members(credential_digest=admin, workspace_id=workspace)
            await manager.mutate_member(credential_digest=owner, workspace_id=workspace, user_id="admin",
                                         expected_revision=3, active=False)
            with pytest.raises(WorkspaceDenied):
                await manager.list_members(credential_digest=admin, workspace_id=workspace, cursor=admin_page["next_cursor"])

    _run(run())


def test_real_member_cursor_expiry_and_missing_signer_fail_closed(monkeypatch):
    clock = [1000]
    monkeypatch.setattr(TimestampSigner, "get_timestamp", lambda self: clock[0])

    async def run():
        async with _canonical() as pool:
            owner = await _identity(pool, "owner")
            manager = WorkspaceManagement(pool, cursors=ScopedCursors(SECRET))
            workspace = (await manager.create(credential_digest=owner, name="Synthetic"))["workspace_id"]
            await _bulk(pool, workspace)
            page = await manager.list_members(credential_digest=owner, workspace_id=workspace)
            clock[0] = 1301
            with pytest.raises(CursorExpired):
                await manager.list_members(credential_digest=owner, workspace_id=workspace, cursor=page["next_cursor"])
            with pytest.raises(TimeoutError):
                await WorkspaceManagement(pool).list_members(credential_digest=owner, workspace_id=workspace)

    _run(run())


def test_real_http_onboarding_pages_head_and_identity_bound_invitation_errors(tmp_path):
    async def run():
        async with _canonical() as pool:
            csrf = CsrfTokens(SECRET)
            owner_digest, owner = await _http_identity(pool, "owner", "a" * 43, csrf)
            _, target = await _http_identity(pool, "target", "b" * 43, csrf)
            _, wrong = await _http_identity(pool, "wrong", "c" * 43, csrf)
            manager = WorkspaceManagement(pool, cursors=ScopedCursors(SECRET))
            runtime = RuntimeSettings.model_validate({"environment": "local", "public_origin": ORIGIN,
                "identity": {"issuer": "https://issuer.example.invalid", "client_id": "synthetic",
                    "redirect_uri": ORIGIN + "/v1/auth/callback", "client_kind": "public"},
                "storage": {"database_url_env": "ALETHEIA_HOSTED_DATABASE_URL", "object_root": str(tmp_path)}})
            sessions = ServerSessions(PostgresSessionStore(pool), AuthPolicy(default_return_to="/", return_to_paths=("/",)),
                                       timeout_seconds=3, maximum_cookie_bytes=4096)

            async def clock():
                return True

            async def fallback(scope, receive, send):
                raise AssertionError("Unexpected fallback")

            app = WorkspaceApplication(fallback, manager, sessions, csrf, runtime,
                                        body_limits=BodyLimits(4096, 1, 3, 10), clock_check=clock)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
                created = await client.post("/v1/workspaces", json={"name": "Synthetic"}, headers=owner)
                assert created.status_code == 201
                workspace = created.json()["workspace_id"]
                assert (await client.post("/v1/workspaces", json={"name": "Again"}, headers=owner)).status_code == 403
                await _bulk(pool, workspace)
                base = "/v1/workspaces/" + workspace
                page = await client.get(base + "/members?limit=50", headers=owner)
                assert page.status_code == 200 and len(page.json()["items"]) == 50 and page.json()["next_cursor"]
                head = await client.head(base + "/members?limit=50", headers=owner)
                assert head.status_code == 200 and not head.content and head.headers["cache-control"] == "no-store"
                assert int(head.headers["content-length"]) > 0
                invitation = await manager.invite(credential_digest=owner_digest, workspace_id=workspace,
                    user_id="target", role="viewer", expires_in_seconds=600)
                async with pool.connection() as conn:
                    await conn.execute("UPDATE j01_invitations SET expires_at=clock_timestamp()-interval '1 second' "
                                       "WHERE invitation_id=%s", (invitation["invitation_id"],))
                for headers, status, code in ((wrong, 403, "invitation_wrong_account"), (target, 410, "invitation_expired")):
                    response = await client.post("/v1/invitations/accept", json={"token": invitation["token"]}, headers=headers)
                    assert response.status_code == status and response.json()["code"] == code
                    assert not {"user_id", "email", "workspace_id", "token"} & set(response.json())
                unknown = await client.post("/v1/invitations/accept", json={"token": "x" * 43}, headers=target)
                assert unknown.status_code == 404 and unknown.json()["code"] == "invitation_unavailable"
                invitation = await manager.invite(credential_digest=owner_digest, workspace_id=workspace,
                    user_id="target", role="viewer", expires_in_seconds=600)
                await manager.revoke_invite(credential_digest=owner_digest, workspace_id=workspace,
                                             invitation_id=invitation["invitation_id"])
                unavailable = await client.post("/v1/invitations/accept", json={"token": invitation["token"]}, headers=wrong)
                assert unavailable.status_code == 404 and unavailable.json()["code"] == "invitation_unavailable"
                invitation = await manager.invite(credential_digest=owner_digest, workspace_id=workspace,
                    user_id="target", role="viewer", expires_in_seconds=600)
                accepted = await client.post("/v1/invitations/accept", json={"token": invitation["token"]}, headers=target)
                assert accepted.status_code == 200
                replay = await client.post("/v1/invitations/accept", json={"token": invitation["token"]}, headers=target)
                assert replay.status_code == 404 and replay.json()["code"] == "invitation_unavailable"

    _run(run())
