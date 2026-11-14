"""A real limited LOGIN loses workspace writes: readiness and routes fail closed."""
from __future__ import annotations

import os

import httpx
import pytest
from pydantic import SecretStr
from test_k04_runtime_principal_postgres import _credential, _principals, _run

from aletheia_lab.hosted.application import AdapterLimits, HostedApplication
from aletheia_lab.hosted.clock_guarded_backend import ClockGuardedBackend
from aletheia_lab.hosted.postgres_workspaces import PostgresWorkspaceAccess
from aletheia_lab.hosted.request_boundary import BodyLimits
from aletheia_lab.hosted.settings import RuntimeSettings
from aletheia_lab.hosted.upload_runtime import UploadReadBackend
from aletheia_lab.hosted.workspace_http import WorkspaceApplication
from aletheia_lab.hosted.workspace_management import WorkspaceManagement
from aletheia_lab.hosted_auth.boundary import SESSION_COOKIE
from aletheia_lab.hosted_auth.csrf import CSRF_COOKIE, CsrfTokens
from aletheia_lab.hosted_auth.postgres_sessions import PostgresSessionStore
from aletheia_lab.hosted_auth.sessions import ServerSessions, credential_digest
from aletheia_lab.hosted_auth.settings import AuthPolicy
from aletheia_lab.hosted_contract.session_capabilities import Limits

pytestmark = [pytest.mark.integration, pytest.mark.skipif(not os.environ.get("K04_TEST_DATABASE_URL"),
    reason="Explicit K-04 PostgreSQL test URL required")]


def test_limited_api_write_privilege_loss_blocks_readiness_capability_and_invite_until_restored(tmp_path):
    from psycopg import sql

    async def scenario():
        async with _principals() as (owner, api, _, schema, api_role, _, admin):
            manager = WorkspaceManagement(api)
            workspace = (await manager.create(credential_digest=_credential("owner_a"), name="Write health"))["workspace_id"]
            tokens = CsrfTokens(SecretStr("synthetic-workspace-health-key-" + "x" * 43))
            raw = SecretStr("w" * 43)
            proof = tokens.issue("ctx_owner_a")
            async with owner.connection() as conn:
                await conn.execute("UPDATE j01_session_credentials SET digest=%s WHERE context_id='ctx_owner_a'",
                                   (credential_digest(raw).get_secret_value(),))
                await conn.execute("UPDATE j01_sessions SET csrf_token=%s WHERE context_id='ctx_owner_a'",
                                   (proof.get_secret_value(),))
            sessions = ServerSessions(PostgresSessionStore(api),
                AuthPolicy(default_return_to="/app", return_to_paths=("/app",)), timeout_seconds=5, maximum_cookie_bytes=4096)
            limits = Limits(question_chars=2000, upload_bundle_bytes=10 * 1024 * 1024, page_max=200, job_poll_min_ms=2000)
            read = UploadReadBackend(sessions, PostgresWorkspaceAccess(api, sessions), limits=limits,
                                     membership_management=manager)
            backend = ClockGuardedBackend(read, manager.is_ready)
            settings = RuntimeSettings.model_validate({"environment": "local", "public_origin": "https://app.example.invalid",
                "identity": {"issuer": "https://identity.example.invalid", "client_id": "synthetic",
                    "redirect_uri": "https://app.example.invalid/v1/auth/callback", "client_kind": "public"},
                "storage": {"database_url_env": "ALETHEIA_HOSTED_DATABASE_URL", "object_root": str(tmp_path),
                    "expected_schema": schema, "expected_database_role": api_role}})
            app = WorkspaceApplication(HostedApplication(backend, limits=AdapterLimits(5, 1024 * 1024, 4096)), manager, sessions, tokens, settings,
                body_limits=BodyLimits(4096, 1, 5, 10), clock_check=manager.is_ready)
            headers = {"origin": "https://app.example.invalid", "x-csrf-token": proof.get_secret_value(),
                "cookie": f"{SESSION_COOKIE}={raw.get_secret_value()}; {CSRF_COOKIE}={proof.get_secret_value()}"}
            base = "/v1/workspaces/" + workspace

            async def counts():
                async with owner.connection() as conn:
                    return await (await conn.execute("SELECT (SELECT count(*) FROM j01_invitations),"
                        "(SELECT count(*) FROM j01_memberships),(SELECT count(*) FROM j01_source_grants)")).fetchone()

            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://app.example.invalid") as client:
                assert await manager.is_ready()
                assert (await client.get("/health/ready")).status_code == 200
                response = await client.get(base + "/capabilities", headers=headers)
                assert response.status_code == 200 and response.json()["features"]["members.app_invitation"] is True
                before = await counts()
                for table, column, privilege in (
                    ("j01_workspaces", "workspace_id", "INSERT"),
                    ("j01_memberships", "role", "INSERT"),
                    ("j01_invitations", "role", "INSERT"),
                    ("j01_source_grants", "actions", "INSERT"),
                    ("hosted_identity_bindings", "onboarding", "UPDATE"),
                    ("hosted_identity_bindings", "last_workspace_id", "UPDATE"),
                    ("j01_workspaces", "name", "UPDATE"),
                    ("j01_memberships", "role", "UPDATE"),
                    ("j01_memberships", "active", "UPDATE"),
                    ("j01_memberships", "revision", "UPDATE"),
                    ("j01_invitations", "revoked", "UPDATE"),
                    ("j01_invitations", "accepted_at", "UPDATE"),
                    ("j01_source_grants", "actions", "UPDATE"),
                    ("j01_source_grants", "revision", "UPDATE"),
                ):
                    statement = sql.SQL("{} {} ({}) ON {}.{} {} {}").format(
                        sql.SQL("REVOKE"), sql.SQL(privilege), sql.Identifier(column), sql.Identifier(schema),
                        sql.Identifier(table), sql.SQL("FROM"), sql.Identifier(api_role))
                    await admin.execute(statement)
                    try:
                        assert not await manager.is_ready(), (table, column, privilege)
                        for path in ("/health/ready", "/v1/session", base + "/capabilities"):
                            response = await client.get(path, headers=headers)
                            assert response.status_code == 503, (table, column, path)
                            assert response.json()["code"] == "service_unavailable"
                        response = await client.post(base + "/invitations", headers=headers,
                            json={"user_id": "owner_b", "role": "viewer", "expires_in_seconds": 600})
                        assert response.status_code == 503 and response.json()["code"] == "service_unavailable"
                        assert await counts() == before
                    finally:
                        await admin.execute(sql.SQL("GRANT {} ({}) ON {}.{} TO {}").format(sql.SQL(privilege),
                            sql.Identifier(column), sql.Identifier(schema), sql.Identifier(table), sql.Identifier(api_role)))
                    assert await manager.is_ready()
                assert (await client.get("/health/ready")).status_code == 200
                response = await client.get(base + "/capabilities", headers=headers)
                assert response.status_code == 200 and response.json()["features"]["members.app_invitation"] is True
                response = await client.post(base + "/invitations", headers=headers,
                    json={"user_id": "owner_b", "role": "viewer", "expires_in_seconds": 600})
                assert response.status_code == 201
    _run(scenario())
