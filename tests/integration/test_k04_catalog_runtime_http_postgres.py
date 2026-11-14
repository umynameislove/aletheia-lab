"""Real API LOGIN, canonical objects, server sessions/CSRF and catalog HTTP policy."""
from __future__ import annotations

import hashlib
import os
import time
from contextlib import asynccontextmanager

import httpx
import pytest
from pydantic import SecretStr
from test_k04_runtime_principal_postgres import _credential, _principals, _run
from test_k05_legacy_import_postgres import _source

from aletheia_lab.hosted import catalog_http
from aletheia_lab.hosted.authorization import AuthorizationDenied
from aletheia_lab.hosted.catalog_access import CatalogAccess
from aletheia_lab.hosted.catalog_http import CatalogApplication
from aletheia_lab.hosted.cursors import ScopedCursors
from aletheia_lab.hosted.legacy_import import ImportTarget, LegacyImporter
from aletheia_lab.hosted.object_store import FilesystemObjectStore
from aletheia_lab.hosted.postgres_catalog import PostgresCatalog
from aletheia_lab.hosted.postgres_objects import PostgresObjects
from aletheia_lab.hosted.project_access import PostgresProjectAccess
from aletheia_lab.hosted.project_mutations import PostgresProjectMutations
from aletheia_lab.hosted.report_renderer import ReportRenderer
from aletheia_lab.hosted.request_boundary import BodyLimits
from aletheia_lab.hosted.review_repository import PostgresReviews
from aletheia_lab.hosted.runtime_binding import verify_runtime_binding
from aletheia_lab.hosted.settings import RuntimeSettings
from aletheia_lab.hosted.workspace_management import WorkspaceManagement
from aletheia_lab.hosted_auth.boundary import SESSION_COOKIE
from aletheia_lab.hosted_auth.csrf import CSRF_COOKIE, CsrfTokens
from aletheia_lab.hosted_auth.postgres_sessions import PostgresSessionStore
from aletheia_lab.hosted_auth.sessions import ServerSessions, credential_digest
from aletheia_lab.hosted_auth.settings import AuthPolicy

pytestmark = [pytest.mark.integration, pytest.mark.skipif(not os.environ.get("K04_TEST_DATABASE_URL"),
    reason="Explicit K-04 PostgreSQL test URL required")]
ACTORS = {"owner": "owner_a", "admin": "admin", "analyst": "analyst", "reviewer": "reviewer", "viewer": "viewer"}


@asynccontextmanager
async def _application(tmp_path):
    async with _principals() as (owner, api, _, schema, api_role, _, _):
        async with owner.connection() as conn:
            for user in ("admin", "reviewer", "viewer"):
                await conn.execute("INSERT INTO hosted_identity_bindings VALUES('issuer',%s,%s,'Synthetic',%s,"
                    "true,true,0,'needs_workspace',NULL,NULL)", (user, user, user + "@example.invalid"))
                await conn.execute("INSERT INTO j01_sessions VALUES(%s,%s,'synthetic-csrf','active',"
                    "statement_timestamp(),statement_timestamp(),statement_timestamp(),"
                    "statement_timestamp()+interval '900 seconds',statement_timestamp()+interval '28000 seconds',0,0)",
                    ("ctx_" + user, user))
                await conn.execute("INSERT INTO j01_session_credentials VALUES(%s,%s,'active',0)",
                                   (_credential(user).get_secret_value(), "ctx_" + user))
        memberships = WorkspaceManagement(api)
        workspace = (await memberships.create(credential_digest=_credential("owner_a"), name="Synthetic canonical A"))["workspace_id"]
        other_workspace = (await memberships.create(credential_digest=_credential("owner_b"), name="Synthetic canonical B"))["workspace_id"]
        for role, user in ACTORS.items():
            if role == "owner":
                continue
            invitation = await memberships.invite(credential_digest=_credential("owner_a"), workspace_id=workspace,
                user_id=user, role=role, expires_in_seconds=600)
            await memberships.accept_invite(credential_digest=_credential(user), token=SecretStr(invitation["token"]))
        source, original, followed = _source(tmp_path)
        store = FilesystemObjectStore(tmp_path / "http-objects")
        await LegacyImporter(owner, PostgresObjects(owner, store)).import_project(source,
            ImportTarget(source.project_id, workspace, "analyst"), credential_digest=_credential("analyst"), batch_id="http-canonical")
        projects = PostgresProjectAccess(api)
        for user, actions in (("reviewer", ["read", "review", "export"]), ("viewer", ["read", "export"])):
            await projects.set_grant(credential_digest=_credential("owner_a"), workspace_id=workspace,
                project_id=source.project_id, user_id=user, actions=actions, expected_revision=0)
        tokens = CsrfTokens(SecretStr("synthetic-canonical-http-signing-key-" + "x" * 43))
        headers, digests = {}, {}
        async with owner.connection() as conn:
            for user in (*ACTORS.values(), "owner_b"):
                raw = SecretStr(("synthetic-canonical-http-" + user).ljust(43, "x"))
                proof = tokens.issue("ctx_" + user)
                digests[user] = credential_digest(raw)
                await conn.execute("UPDATE j01_session_credentials SET digest=%s WHERE context_id=%s",
                                   (digests[user].get_secret_value(), "ctx_" + user))
                await conn.execute("UPDATE j01_sessions SET csrf_token=%s WHERE context_id=%s",
                                   (proof.get_secret_value(), "ctx_" + user))
                headers[user] = {"origin": "https://app.example.invalid", "x-csrf-token": proof.get_secret_value(),
                    "cookie": f"{SESSION_COOKIE}={raw.get_secret_value()}; {CSRF_COOKIE}={proof.get_secret_value()}"}
        settings = RuntimeSettings.model_validate({"environment": "local", "public_origin": "https://app.example.invalid",
            "identity": {"issuer": "https://identity.example.invalid", "client_id": "synthetic",
                "redirect_uri": "https://app.example.invalid/v1/auth/callback", "client_kind": "public"},
            "storage": {"database_url_env": "ALETHEIA_HOSTED_DATABASE_URL", "object_root": str(tmp_path),
                "expected_schema": schema, "expected_database_role": api_role},
            "api": {"backend_timeout_seconds": 20, "request_timeout_seconds": 30}})
        assert await verify_runtime_binding(api, settings.storage, strict=True)
        sessions = ServerSessions(PostgresSessionStore(api), AuthPolicy(default_return_to="/app", return_to_paths=("/app",)),
                                  timeout_seconds=5, maximum_cookie_bytes=4096)
        renderer = ReportRenderer(timeout_seconds=20)
        catalog = PostgresCatalog(api, PostgresObjects(api, store))
        access = CatalogAccess(catalog, ScopedCursors(SecretStr("synthetic-canonical-cursor-key-" + "x" * 43)), renderer)

        async def clock():
            return True

        async def fallback(scope, receive, send):
            raise AssertionError("Unexpected route in canonical HTTP test")

        app = CatalogApplication(fallback, access, projects, PostgresReviews(api, catalog), sessions, tokens, settings,
            body_limits=BodyLimits(10000, 2, 5, 100), clock_check=clock,
            project_mutations=PostgresProjectMutations(api, catalog))
        try:
            yield app, projects, headers, digests, workspace, other_workspace, source.project_id, original, followed, api
        finally:
            await renderer.aclose()


def test_actual_runtime_http_all_five_roles_read_review_export_and_parent_scope(tmp_path):
    async def scenario():
        async with _application(tmp_path) as (app, projects, headers, digests, workspace, other, project, original, followed, api):
            result = original["result"]["id"]
            base = f"/v1/workspaces/{workspace}"
            path = base + "/results/" + result
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://app.example.invalid") as client:
                for role, user in ACTORS.items():
                    response = await client.get(path, headers=headers[user])
                    assert response.status_code == 200 and response.json()["view"]["result"]["id"] == result
                    assert response.headers["cache-control"] == "no-store"
                    comment = await client.put(path + "/annotations/review_" + role,
                        headers={**headers[user], "if-match": '"0"', "x-role": "owner", "x-user-id": "owner_a"},
                        json={"body": "Scoped human review", "status": "comment"})
                    if role == "viewer":
                        assert comment.status_code == 404
                    else:
                        assert comment.status_code == 200 and comment.json()["author_id"] == user
                        assert comment.headers["etag"] == '"1"'
                    export = await client.post(path + "/exports", headers=headers[user], json={"format": "json"})
                    assert export.status_code == 201
                    descriptor = export.json()
                    download = await client.get(descriptor["download_url"], headers=headers[user])
                    assert download.status_code == 200 and hashlib.sha256(download.content).hexdigest() == descriptor["sha256"]
                    assert len(download.content) == descriptor["byte_size"]
                denied = await client.get(path, headers=headers["owner_b"])
                missing = await client.get(base + "/results/p6-result-" + "f" * 64, headers=headers["owner_a"])
                assert denied.status_code == missing.status_code == 404
                assert denied.json()["detail"] == missing.json()["detail"]
                assert "Synthetic canonical A" not in denied.text and result not in denied.text
                assert (await client.get(f"/v1/workspaces/{other}/results/{result}", headers=headers["owner_b"])).status_code == 404
                evidence = original["evidence"][0]["id"]
                wrong_claim = followed["claims"][-1]["id"]
                assert (await client.get(path + "/evidence/" + evidence, params={"claim_id": wrong_claim},
                                         headers=headers["viewer"])).status_code == 404
                assert (await client.get(path + "/claims", headers=headers["reviewer"])).status_code == 200
                assert (await client.get(path + "/graph", headers=headers["viewer"])).status_code == 200
                assert (await client.get(base + "/events", params={"project_id": project}, headers=headers["viewer"])).status_code == 200
                grant = await client.get(base + f"/projects/{project}/grants/viewer", headers=headers["owner_a"])
                assert grant.status_code == 200 and grant.json()["revision"] == 1
                assert grant.headers["etag"] == '"1"'
                await projects.set_grant(credential_digest=digests["owner_a"], workspace_id=workspace, project_id=project,
                    user_id="viewer", actions=["read"], expected_revision=1)
                assert (await client.get(path, headers=headers["viewer"])).status_code == 200
                assert (await client.post(path + "/exports", headers=headers["viewer"], json={"format": "json"})).status_code == 404
            async with api.connection() as conn:
                assert await (await conn.execute("SELECT count(*) FROM j01_annotations")).fetchone() == (4,)
    _run(scenario())


def test_actual_runtime_http_range_and_revoke_between_download_chunks(tmp_path, monkeypatch):
    async def scenario():
        async with _application(tmp_path) as (app, projects, headers, digests, workspace, _, project, original, _, _):
            result = original["result"]["id"]
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://app.example.invalid") as client:
                export = await client.post(f"/v1/workspaces/{workspace}/results/{result}/exports",
                    headers=headers["viewer"], json={"format": "json"})
                assert export.status_code == 201
                path = export.json()["download_url"]
                full = await client.get(path, headers=headers["viewer"])
                assert full.status_code == 200 and len(full.content) > 32
                partial = await client.get(path, headers={**headers["viewer"], "range": "bytes=0-31"})
                assert partial.status_code == 206 and partial.content == full.content[:32]
                assert partial.headers["content-range"] == f"bytes 0-31/{len(full.content)}"
                head = await client.head(path, headers=headers["viewer"])
                assert head.status_code == 200 and head.content == b"" and int(head.headers["content-length"]) == len(full.content)
                assert (await client.get(path, headers={**headers["viewer"], "range": "bytes=99999999-"})).status_code == 416
            # Small chunks force a transport boundary without fabricating a large ProductView.
            monkeypatch.setattr(catalog_http, "_CHUNK_BYTES", 32)
            messages, revoked_at = [], None

            async def send(message):
                nonlocal revoked_at
                messages.append(message)
                if message.get("type") == "http.response.body" and message.get("body") and revoked_at is None:
                    await projects.set_grant(credential_digest=digests["owner_a"], workspace_id=workspace, project_id=project,
                        user_id="viewer", actions=[], expected_revision=1)
                    revoked_at = time.perf_counter()

            async def receive():
                return {"type": "http.request", "body": b"", "more_body": False}

            scope = {"type": "http", "method": "GET", "scheme": "https", "path": path, "query_string": b"",
                "headers": [(b"host", b"app.example.invalid"),
                    *[(name.encode(), value.encode()) for name, value in headers["viewer"].items()]]}
            with pytest.raises(AuthorizationDenied):
                await app(scope, receive, send)
            assert revoked_at is not None
            denial_seconds = time.perf_counter() - revoked_at
            assert denial_seconds <= 5
            bodies = [message["body"] for message in messages if message.get("type") == "http.response.body"]
            assert bodies == [full.content[:32]]
            starts = [message for message in messages if message.get("type") == "http.response.start"]
            assert len(starts) == 1 and starts[0]["status"] == 200
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://app.example.invalid") as client:
                denied = await client.get(path, headers={**headers["viewer"], "range": "bytes=99999999-"})
                assert denied.status_code == 404 and "content-range" not in denied.headers
            print(f"K04 ASGI+actual API LOGIN revoke commit-return to first denied checkpoint: {denial_seconds:.6f}s; "
                  "test chunk=32 bytes, bytes sent before revoke=32, bytes sent after revoke=0")
    _run(scenario())


def test_actual_runtime_http_global_search_and_explicit_analyst_logical_delete(tmp_path):
    async def scenario():
        async with _application(tmp_path) as (app, _, headers, _, workspace, other, project, original, _, api):
            base = f"/v1/workspaces/{workspace}"
            result = original["result"]["id"]
            project_path = base + "/projects/" + project
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://app.example.invalid") as client:
                page = await client.get(base + "/results", headers=headers["viewer"], params={"limit": 1})
                assert page.status_code == 200 and len(page.json()["items"]) == 1 and page.json()["next_cursor"]
                matching = await client.get(base + "/results", headers=headers["viewer"], params={"search": result})
                assert matching.status_code == 200 and [item["record_id"] for item in matching.json()["items"]] == [result]
                assert (await client.get(f"/v1/workspaces/{other}/results", headers=headers["owner_b"])).json()["items"] == []
                for user in ("viewer", "reviewer", "analyst", "owner_b"):
                    denied = await client.request("DELETE", project_path, headers={**headers[user], "if-match": '"0"'}, json={})
                    assert denied.status_code == 404
                stale = await client.request("DELETE", project_path, headers={**headers["owner_a"], "if-match": '"1"'}, json={})
                assert stale.status_code == 412
                assert (await client.get(base + "/results/" + result, headers=headers["owner_a"])).status_code == 200
                grant = await client.put(project_path + "/grants/analyst",
                    headers={**headers["owner_a"], "if-match": '"0"'}, json={"actions": ["read", "delete"]})
                assert grant.status_code == 200 and grant.headers["etag"] == '"1"'
                deleted = await client.request("DELETE", project_path, headers={**headers["analyst"], "if-match": '"0"'}, json={})
                assert deleted.status_code == 200 and deleted.headers["etag"] == '"1"'
                assert deleted.json()["status"] == "tombstoned" and deleted.json()["retention"] == "retained"
                assert "job_id" not in deleted.json()
                assert (await client.get(base + "/results/" + result, headers=headers["owner_a"])).status_code == 404
                assert (await client.get(base + "/results", headers=headers["owner_a"])).json()["items"] == []
            async with api.connection() as conn:
                assert await (await conn.execute("SELECT count(*) FROM k05_records WHERE record_type='result'")).fetchone() == (2,)
    _run(scenario())
