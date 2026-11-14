"""Compact project/CAS policy routes and source quotas in UUID-owned schemas."""
from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from types import SimpleNamespace

import httpx
import pytest
from pydantic import SecretStr
from test_k05_migrations_postgres import _run
from test_k05_native_results_postgres import _case, _initial
from test_k06_follow_up_http_postgres import _identity, _problem
from test_k07_upload_staging_postgres import _manifest

from aletheia_lab.hosted.action_audit_migration import migrate as migrate_audit
from aletheia_lab.hosted.authorization import AuthorizationDenied
from aletheia_lab.hosted.postgres_sources import PostgresSources
from aletheia_lab.hosted.project_access import PostgresProjectAccess
from aletheia_lab.hosted.project_mutations import PostgresProjectMutations
from aletheia_lab.hosted.request_boundary import BodyLimits
from aletheia_lab.hosted.runtime_management import RuntimeManagement
from aletheia_lab.hosted.runtime_management_http import RuntimeManagementApplication
from aletheia_lab.hosted.settings import RuntimeSettings
from aletheia_lab.hosted.upload_staging import UploadRejected
from aletheia_lab.hosted.upload_staging_migration import migrate as migrate_uploads
from aletheia_lab.hosted.workspace_management import WorkspaceConflict
from aletheia_lab.hosted_auth.csrf import CsrfTokens
from aletheia_lab.hosted_auth.postgres_sessions import PostgresSessionStore
from aletheia_lab.hosted_auth.sessions import ServerSessions
from aletheia_lab.hosted_auth.settings import AuthPolicy
from aletheia_lab.hosted_contract.runtime_management import (
    ChangeWorkspaceSettings,
    ProjectDetails,
    WorkspacePolicyLimits,
    WorkspaceSettings,
)

pytestmark = [pytest.mark.integration, pytest.mark.skipif(not os.environ.get("K02_TEST_DATABASE_URL"),
    reason="Explicit K02 owned-schema PostgreSQL test URL required")]
ORIGIN = "https://app.example.invalid"
VERSION = "product-api/v1-draft"


@pytest.fixture(autouse=True)
def configured_database(monkeypatch):
    # Reuse the existing UUID schema/native publication fixture without copying its identity SQL.
    monkeypatch.setenv("K05_TEST_DATABASE_URL", os.environ["K02_TEST_DATABASE_URL"])


@asynccontextmanager
async def _application(tmp_path):
    async with _case(tmp_path) as case:
        snapshot, initial = await _initial(case)
        async with case.pool.connection() as conn:
            schema = (await (await conn.execute("SELECT current_schema()")).fetchone())[0]
            await migrate_audit(conn, schema)
            await migrate_uploads(conn, schema)
        tokens = CsrfTokens(SecretStr("synthetic-k02-metadata-policy-" + "x" * 43))
        owner = await _identity(case, "a", "a" * 43, tokens)
        other = await _identity(case, "b", "b" * 43, tokens)
        sources = PostgresSources(case.pool, maximum_upload_bytes=10000, maximum_expanded_bytes=20000,
            maximum_entries=10, maximum_workspace_bytes=100000, catalog=case.catalog)
        repo = RuntimeManagement(case.pool, platform_limits=WorkspacePolicyLimits(
            maximum_upload_bytes=10000, maximum_workspace_bytes=100000), audit=True)
        assert await repo.is_ready()
        runtime = RuntimeSettings.model_validate({"environment": "local", "public_origin": ORIGIN,
            "identity": {"issuer": "https://issuer.example.invalid", "client_id": "synthetic-metadata",
                "redirect_uri": ORIGIN + "/v1/auth/callback", "client_kind": "public"},
            "storage": {"database_url_env": "ALETHEIA_HOSTED_DATABASE_URL", "object_root": str(tmp_path)}})
        sessions = ServerSessions(PostgresSessionStore(case.pool),
            AuthPolicy(default_return_to="/", return_to_paths=("/",)), timeout_seconds=3, maximum_cookie_bytes=4096)

        async def fallback(scope, receive, send):
            raise AssertionError("Unexpected metadata/policy integration route")

        async def clock(): return True

        app = RuntimeManagementApplication(fallback, repo, sessions, tokens, runtime,
            body_limits=BodyLimits(10000, 1, 3, 20), clock_check=clock)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
            yield SimpleNamespace(case=case, repo=repo, sources=sources, client=client, owner=owner,
                other=other, snapshot=snapshot, initial=initial, project=initial.project_id)


def test_real_compact_project_http_cas_current_grants_and_deleting_state(tmp_path):
    async def scenario():
        async with _application(tmp_path) as app:
            case, client = app.case, app.client
            path = f"/v1/workspaces/a/projects/{app.project}"
            response = await client.get(path, headers=app.owner)
            assert response.status_code == 200 and response.headers["etag"] == '"2"'
            before = ProjectDetails.model_validate_json(response.content)
            assert before.state == "active" and before.current_snapshot_id == app.snapshot.snapshot_id
            assert before.current_result_id == app.initial.record_id and "data" not in response.json()
            updated = await client.patch(path, headers={**app.owner, "if-match": '"2"'},
                json={"schema_version": VERSION, "display_name": "Dự án Kiên"})
            assert updated.status_code == 200, updated.text
            details = ProjectDetails.model_validate_json(updated.content)
            assert details.revision == 3 and details.display_name == "Dự án Kiên"
            _problem(await client.patch(path, headers={**app.owner, "if-match": '"2"'},
                json={"schema_version": VERSION, "display_name": "Stale writer"}), 412, "revision_conflict")
            _problem(await client.get(path.replace("/workspaces/a/", "/workspaces/b/"), headers=app.other), 404, "not_found")
            assert await case.catalog.read_record(credential_digest=case.a, workspace_id="a",
                project_id=app.project, record_id=app.snapshot.snapshot_id) == case.snapshot
            assert await case.catalog.read_record(credential_digest=case.a, workspace_id="a",
                project_id=app.project, record_id=app.initial.record_id) == case.initial
            await PostgresProjectAccess(case.pool).set_grant(credential_digest=case.a, workspace_id="a",
                project_id=app.project, user_id="owner_b", actions=["read"], expected_revision=0)
            async with case.pool.connection() as conn:
                await conn.execute("UPDATE j01_memberships SET role='viewer',revision=revision+1 "
                                   "WHERE workspace_id='a' AND user_id='owner_b'")
                audit = await (await conn.execute("SELECT action,effect_id,revision,request_id FROM k06_action_audit "
                    "WHERE action='project.update'")).fetchall()
                assert audit == [("project.update", app.project, 3, updated.headers["x-request-id"])]
            assert (await client.get(path, headers=app.other)).status_code == 200
            _problem(await client.patch(path, headers={**app.other, "if-match": '"3"'},
                json={"schema_version": VERSION, "display_name": "Forbidden writer"}), 404, "not_found")
            _problem(await client.get("/v1/workspaces/a/settings", headers=app.other), 403, "permission_denied")
            deleted = await PostgresProjectMutations(case.pool, case.catalog).delete(credential_digest=case.a,
                workspace_id="a", project_id=app.project, expected_revision=3)
            assert deleted["status"] == "tombstoned"
            response = await client.get(path, headers=app.owner)
            assert response.status_code == 200, response.text
            tombstone = ProjectDetails.model_validate_json(response.content)
            assert (tombstone.state, tombstone.revision, tombstone.display_name) == ("deleting", 4, "Dự án Kiên")
            assert tombstone.current_snapshot_id == before.current_snapshot_id
    _run(scenario())


def test_real_settings_policy_controls_new_upload_admission_and_preserves_authorized_replay(tmp_path):
    async def scenario():
        async with _application(tmp_path) as app:
            case, client = app.case, app.client
            path = "/v1/workspaces/a/settings"
            initial = await client.get(path, headers=app.owner)
            assert initial.status_code == 200
            assert WorkspaceSettings.model_validate_json(initial.content).provenance == "platform_default"
            manifest, _ = _manifest(case.payload)
            accepted = await app.sources.create_upload(credential_digest=case.a, workspace_id="a",
                request_key=SecretStr("metadata-policy-before-001"), manifest=manifest)
            changed = await client.patch(path, headers={**app.owner, "if-match": '"0"'},
                json={"schema_version": VERSION, "maximum_upload_bytes": len(case.payload) - 1})
            assert changed.status_code == 200, changed.text
            policy = WorkspaceSettings.model_validate_json(changed.content)
            assert policy.revision == 1 and policy.maximum_upload_bytes == len(case.payload) - 1
            with pytest.raises(UploadRejected) as denied:
                await app.sources.create_upload(credential_digest=case.a, workspace_id="a",
                    request_key=SecretStr("metadata-policy-too-big-002"), manifest=manifest)
            assert denied.value.status == 413
            replay = await app.sources.create_upload(credential_digest=case.a, workspace_id="a",
                request_key=SecretStr("metadata-policy-before-001"), manifest=manifest)
            assert replay.upload_id == accepted.upload_id
            changed = await client.patch(path, headers={**app.owner, "if-match": '"1"'}, json={
                "schema_version": VERSION, "maximum_upload_bytes": len(case.payload),
                "maximum_workspace_bytes": 2 * len(case.payload)})
            assert changed.status_code == 200, changed.text
            with pytest.raises(UploadRejected) as denied:
                await app.sources.create_upload(credential_digest=case.a, workspace_id="a",
                    request_key=SecretStr("metadata-policy-full-003"), manifest=manifest)
            assert denied.value.status == 413
            other = await app.sources.create_upload(credential_digest=case.b, workspace_id="b",
                request_key=SecretStr("metadata-policy-other-004"), manifest=manifest)
            assert other.workspace_id == "b"
            with pytest.raises(AuthorizationDenied):
                await app.repo.settings(credential_digest=case.a, workspace_id="b")
            async with case.pool.connection() as conn:
                assert await (await conn.execute("SELECT count(*) FROM k07_uploads WHERE workspace_id='a'")).fetchone() == (1,)
                assert await (await conn.execute("SELECT count(*) FROM j01_sources WHERE workspace_id='a'")).fetchone() == (1,)
    _run(scenario())


def test_real_concurrent_settings_writers_have_one_cas_successor(tmp_path):
    async def scenario():
        async with _application(tmp_path) as app:
            results = await asyncio.gather(*(app.repo.update_settings(credential_digest=app.case.a,
                workspace_id="a", expected_revision=0, request=ChangeWorkspaceSettings(schema_version=VERSION,
                    maximum_upload_bytes=value)) for value in (100, 200)), return_exceptions=True)
            assert sum(isinstance(value, WorkspaceSettings) for value in results) == 1
            assert sum(isinstance(value, WorkspaceConflict) for value in results) == 1
            current = await app.repo.settings(credential_digest=app.case.a, workspace_id="a")
            assert current.revision == 1 and current.maximum_upload_bytes in (100, 200)
            async with app.case.pool.connection() as conn:
                assert await (await conn.execute("SELECT count(*) FROM k06_action_audit "
                    "WHERE action='workspace.settings'")).fetchone() == (1,)
    _run(scenario())
