"""Workspace directory journeys with real canonical PostgreSQL and HTTP ASGI."""
from __future__ import annotations

import hashlib
import os

import httpx
import pytest
from pydantic import SecretStr
from test_k04_http_postgres import ORIGIN, _http_identity
from test_k04_member_pages_postgres import SECRET, _canonical
from test_k04_workspaces_postgres import _run

from aletheia_lab.hosted.cursors import ScopedCursors
from aletheia_lab.hosted.request_boundary import BodyLimits
from aletheia_lab.hosted.settings import RuntimeSettings
from aletheia_lab.hosted.workspace_http import WorkspaceApplication
from aletheia_lab.hosted.workspace_management import WorkspaceManagement
from aletheia_lab.hosted_auth.csrf import CsrfTokens
from aletheia_lab.hosted_auth.postgres_sessions import PostgresSessionStore
from aletheia_lab.hosted_auth.sessions import ServerSessions
from aletheia_lab.hosted_auth.settings import AuthPolicy

pytestmark = [pytest.mark.integration, pytest.mark.skipif(not os.environ.get("K04_TEST_DATABASE_URL"),
    reason="Explicit K-04 PostgreSQL test URL required")]


def test_real_workspace_directory_scope_pending_invites_targets_names_and_grant_reads(tmp_path):
    async def run():
        async with _canonical() as pool:
            csrf = CsrfTokens(SECRET)
            owner, other, viewer, target = [await _http_identity(pool, user, char * 43, csrf)
                for user, char in (("owner", "a"), ("other", "b"), ("viewer", "c"), ("target", "d"))]
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
                raise AssertionError("Unexpected directory fallback")

            app = WorkspaceApplication(fallback, manager, sessions, csrf, runtime,
                                        body_limits=BodyLimits(8192, 1, 3, 10), clock_check=clock)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
                workspace = (await client.post("/v1/workspaces", json={"name": "A"}, headers=owner[1])).json()["workspace_id"]
                foreign = (await client.post("/v1/workspaces", json={"name": "B"}, headers=other[1])).json()["workspace_id"]
                base = "/v1/workspaces/" + workspace
                own = await client.get("/v1/workspaces", headers=owner[1])
                assert own.status_code == 200 and [item["workspace_id"] for item in own.json()["items"]] == [workspace]
                assert foreign not in str(own.json()) and own.json()["total"] is None
                assert (await client.get(base, headers=owner[1])).json()["workspace_name"] == "A"
                for unavailable in (foreign, "missing"):
                    denied = await client.get("/v1/workspaces/" + unavailable, headers=owner[1])
                    assert denied.status_code == 404 and denied.json()["code"] == "not_found"
                invited = await manager.invite(credential_digest=owner[0], workspace_id=workspace,
                    user_id="viewer", role="viewer", expires_in_seconds=600)
                await manager.accept_invite(credential_digest=viewer[0], token=SecretStr(invited["token"]))
                pending = await manager.invite(credential_digest=owner[0], workspace_id=workspace,
                    user_id="target", role="analyst", expires_in_seconds=600)
                pending_ids = {pending["invitation_id"]}
                for role in ("admin", "reviewer"):
                    extra = await manager.invite(credential_digest=owner[0], workspace_id=workspace,
                        user_id="target", role=role, expires_in_seconds=600)
                    pending_ids.add(extra["invitation_id"])
                revoked = await manager.invite(credential_digest=owner[0], workspace_id=workspace,
                    user_id="target", role="viewer", expires_in_seconds=600)
                await manager.revoke_invite(credential_digest=owner[0], workspace_id=workspace,
                                             invitation_id=revoked["invitation_id"])
                expired = await manager.invite(credential_digest=owner[0], workspace_id=workspace,
                    user_id="target", role="reviewer", expires_in_seconds=600)
                async with pool.connection() as conn:
                    await conn.execute("UPDATE j01_invitations SET expires_at=clock_timestamp()-interval '1 second' "
                                       "WHERE invitation_id=%s", (expired["invitation_id"],))
                pending_page = await client.get(base + "/invitations?limit=1", headers=owner[1])
                assert pending_page.status_code == 200 and len(pending_page.json()["items"]) == 1
                item = pending_page.json()["items"][0]
                assert item["invitation_id"] in pending_ids and item["display_name"] == "Synthetic"
                assert not {"token", "token_digest", "email", "subject"} & set(item)
                seen = {item["invitation_id"]}
                cursor = pending_page.json()["next_cursor"]
                while cursor:
                    page = await client.get(base + "/invitations", params={"limit": 1, "cursor": cursor}, headers=owner[1])
                    assert page.status_code == 200 and len(page.json()["items"]) == 1
                    seen.add(page.json()["items"][0]["invitation_id"])
                    cursor = page.json()["next_cursor"]
                assert seen == pending_ids
                member_cursor = (await client.get(base + "/members?limit=1", headers=owner[1])).json()["next_cursor"]
                substituted = await client.get(base + "/invitations", params={"limit": 1, "cursor": member_cursor}, headers=owner[1])
                assert substituted.status_code == 400 and substituted.json()["code"] == "cursor_invalid"
                names = await client.get(base + "/members", headers=owner[1])
                assert names.status_code == 200 and all(item["display_name"] == "Synthetic" for item in names.json()["items"])
                async with pool.connection() as conn:
                    await conn.execute("INSERT INTO hosted_identity_bindings(issuer,subject,user_id,display_name,email,email_verified,"
                        "enabled,account_epoch,onboarding) SELECT 'https://issuer.example.invalid','ambiguous-'||n,"
                        "'ambiguous_'||lpad(n::text,3,'0'),'Candidate '||n,'same@example.invalid',true,true,0,'needs_workspace' "
                        "FROM generate_series(1,23) n")
                    await conn.execute("INSERT INTO hosted_identity_bindings VALUES "
                        "('https://issuer.example.invalid','disabled','disabled','Disabled','same@example.invalid',true,false,0,'needs_workspace',NULL,NULL),"
                        "('https://issuer.example.invalid','unverified','unverified','Unverified','same@example.invalid',false,true,0,'needs_workspace',NULL,NULL)")
                found = await client.post(base + "/invitation-targets", json={"email": "same@example.invalid"}, headers=owner[1])
                assert found.status_code == 200 and len(found.json()["items"]) == 20 and found.json()["next_cursor"]
                continuation = found.json()["next_cursor"]
                last = await client.post(base + "/invitation-targets", json={"email": "same@example.invalid", "cursor": continuation}, headers=owner[1])
                assert last.status_code == 200 and len(last.json()["items"]) == 3 and last.json()["next_cursor"] is None
                identifiers = {item["user_id"] for item in found.json()["items"] + last.json()["items"]}
                assert len(identifiers) == 23 and not {"disabled", "unverified"} & identifiers
                changed_query = await client.post(base + "/invitation-targets", json={"email": "other@example.invalid", "cursor": continuation}, headers=owner[1])
                assert changed_query.status_code == 400 and changed_query.json()["code"] == "cursor_invalid"
                async with pool.connection() as conn:
                    for source, scope, creator in (("source", workspace, "owner"), ("foreign", foreign, "other")):
                        await conn.execute("INSERT INTO j01_sources(source_id,workspace_id,creator_id,request_key_hash,sha256,payload,manifest) "
                            "VALUES (%s,%s,%s,%s,%s,%s,'[]')", (source, scope, creator, source, hashlib.sha256(b"synthetic").hexdigest(), b"synthetic"))
                grant_path = base + "/sources/source/grants/viewer"
                grant = await client.get(grant_path, headers=owner[1])
                assert grant.status_code == 200 and grant.json()["actions"] == [] and grant.json()["revision"] == 0
                await manager.set_source_grant(credential_digest=owner[0], workspace_id=workspace,
                    source_id="source", user_id="viewer", actions=["read", "export"], expected_revision=0)
                grant = await client.get(grant_path, headers=owner[1])
                assert grant.status_code == 200 and grant.json()["revision"] == 1 and grant.json()["actions"] == ["read", "export"]
                for source in ("foreign", "missing"):
                    denied = await client.get(base + "/sources/" + source + "/grants/viewer", headers=owner[1])
                    assert denied.status_code == 404 and denied.json()["code"] == "not_found"
                assert (await client.get(base, headers=viewer[1])).status_code == 200
                for path in (base + "/members", base + "/invitations", grant_path):
                    assert (await client.get(path, headers=viewer[1])).status_code == 403
                assert (await client.post(base + "/invitation-targets", json={"email": "same@example.invalid"}, headers=viewer[1])).status_code == 403
                async with pool.connection() as conn:
                    await conn.execute("INSERT INTO j01_workspaces VALUES ('zz_directory','Additional')")
                    await conn.execute("INSERT INTO j01_memberships VALUES ('zz_directory','other','owner',true,1),"
                                       "('zz_directory','owner','admin',true,1)")
                changed_workspace = await client.post("/v1/workspaces/zz_directory/invitation-targets",
                    json={"email": "same@example.invalid", "cursor": found.json()["next_cursor"]}, headers=owner[1])
                assert changed_workspace.status_code == 400 and changed_workspace.json()["code"] == "cursor_invalid"
                first = await client.get("/v1/workspaces?limit=1", headers=owner[1])
                assert first.status_code == 200 and first.json()["items"][0]["workspace_id"] == workspace
                continuation = first.json()["next_cursor"]
                substituted = await client.get("/v1/workspaces", params={"limit": 1, "cursor": continuation}, headers=other[1])
                assert substituted.status_code == 400 and substituted.json()["code"] == "cursor_invalid"
                await manager.mutate_member(credential_digest=other[0], workspace_id="zz_directory",
                                             user_id="owner", expected_revision=1, active=False)
                second = await client.get("/v1/workspaces", params={"limit": 1, "cursor": continuation}, headers=owner[1])
                assert second.status_code == 200 and second.json()["items"] == []

    _run(run())
