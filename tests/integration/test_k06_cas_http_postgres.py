"""Real PostgreSQL CAS races and the actual HTTP producer/consumer boundary."""
from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager

import pytest
from pydantic import SecretStr
from test_k04_project_policy_postgres import PROJECT, _credential, _database, _run
from test_k04_reviews_postgres import _setup

from aletheia_lab.hosted.application import VERSION
from aletheia_lab.hosted.project_access import PostgresProjectAccess
from aletheia_lab.hosted.review_repository import PostgresReviews
from aletheia_lab.hosted.workspace_management import WorkspaceConflict, WorkspaceManagement

pytestmark = [pytest.mark.integration, pytest.mark.skipif(
    not os.environ.get("K04_TEST_DATABASE_URL"), reason="Explicit PostgreSQL test database required")]


class _EarlyBarrierPool:
    """Synchronize once per transaction before acquiring authorization locks."""

    def __init__(self, pool):
        self.pool = pool
        self.barrier = asyncio.Barrier(20)
        self.backend_pids = set()

    @asynccontextmanager
    async def connection(self):
        async with self.pool.connection() as conn:
            outer = self

            class Connection:
                autocommit = False
                synchronized = False

                async def execute(self, query, params=()):
                    if not self.synchronized and query.startswith("SELECT s.user_id FROM j01_session_credentials"):
                        self.synchronized = True
                        pid = await (await conn.execute("SELECT pg_backend_pid()")).fetchone()
                        outer.backend_pids.add(pid[0])
                        await outer.barrier.wait()
                    return await conn.execute(query, params)

            yield Connection()


async def _twenty(pool, make_call):
    await pool.resize(min_size=20, max_size=20)
    await pool.wait(timeout=15)
    barrier = _EarlyBarrierPool(pool)
    outcomes = await asyncio.wait_for(asyncio.gather(
        *(make_call(barrier, index) for index in range(20)), return_exceptions=True), 45)
    winners = [value for value in outcomes if isinstance(value, dict)]
    assert len(winners) == 1, [type(value).__name__ for value in outcomes]
    assert sum(isinstance(value, WorkspaceConflict) for value in outcomes) == 19
    assert len(barrier.backend_pids) == 20
    return winners[0]


def test_twenty_review_writers_same_revision_have_one_append_and_keep_canonical_result(tmp_path):
    async def scenario():
        async with _database() as (pool, schema):
            _, _, catalog, project, result, _ = await _setup(pool, schema, tmp_path)
            original = await catalog.read_record(credential_digest=_credential("owner"), workspace_id="wa",
                                                 project_id=project, record_id=result)

            async def write(barrier, index):
                return await PostgresReviews(barrier, catalog).put(credential_digest=_credential("reviewer"),
                    workspace_id="wa", result_id=result, annotation_id="twenty-writers", body=f"Synthetic draft {index}",
                    status="comment", expected_revision=0)

            winner = await _twenty(pool, write)
            assert winner["revision"] == 1
            reviews = PostgresReviews(pool, catalog)
            history = await reviews.list_history(credential_digest=_credential("viewer"), workspace_id="wa",
                                                 result_id=result, annotation_id="twenty-writers")
            assert history["items"] == [winner]
            assert await catalog.read_record(credential_digest=_credential("owner"), workspace_id="wa",
                project_id=project, record_id=result) == original
            async with pool.connection() as conn:
                assert await (await conn.execute("SELECT count(*),max(revision) FROM j01_annotations")).fetchone() == (1, 1)
            print("K06 DB08 review: 20 real backend connections, one revision-1 append, 19 conflicts; immutable result unchanged")
    _run(scenario())


def test_twenty_membership_writers_same_revision_have_one_winner():
    async def scenario():
        async with _database() as (pool, _):
            async def write(barrier, index):
                return await WorkspaceManagement(barrier).mutate_member(credential_digest=_credential("owner"),
                    workspace_id="wa", user_id="viewer", expected_revision=1,
                    role="analyst" if index % 2 else "reviewer")

            winner = await _twenty(pool, write)
            assert winner["revision"] == 2
            async with pool.connection() as conn:
                assert await (await conn.execute("SELECT role,active,revision FROM j01_memberships "
                    "WHERE workspace_id='wa' AND user_id='viewer'")).fetchone() == (winner["role"], True, 2)
            print("K06 DB08 membership: 20 real backend connections, one revision-2 winner, 19 conflicts")
    _run(scenario())


def test_twenty_project_grant_writers_same_revision_have_one_winner():
    async def scenario():
        async with _database() as (pool, _):
            manager = PostgresProjectAccess(pool)
            await manager.set_grant(credential_digest=_credential("owner"), workspace_id="wa", project_id=PROJECT,
                                    user_id="viewer", actions=["read"], expected_revision=0)

            async def write(barrier, index):
                return await PostgresProjectAccess(barrier).set_grant(credential_digest=_credential("owner"),
                    workspace_id="wa", project_id=PROJECT, user_id="viewer", expected_revision=1,
                    actions=["read", "export"] if index % 2 else ["read"])

            winner = await _twenty(pool, write)
            assert winner["revision"] == 2
            async with pool.connection() as conn:
                assert await (await conn.execute("SELECT actions,revision FROM j01_project_grants "
                    "WHERE workspace_id='wa' AND project_id=%s AND user_id='viewer'", (PROJECT,))).fetchone() == (
                    winner["actions"], 2)
                assert await (await conn.execute("SELECT count(*) FROM j01_project_grants")).fetchone() == (1,)
            print("K06 DB08 project grant: 20 real backend connections, one revision-2 winner, 19 conflicts")
    _run(scenario())


@pytest.mark.parametrize("resource", ["project", "settings"])
def test_twenty_metadata_writers_keep_one_revision_and_one_audit(tmp_path, monkeypatch, resource):
    from test_k02_runtime_management_postgres import _application

    from aletheia_lab.hosted.runtime_management import RuntimeManagement
    from aletheia_lab.hosted_contract.runtime_management import (
        ChangeProject,
        ChangeWorkspaceSettings,
        WorkspacePolicyLimits,
    )

    monkeypatch.setenv("K05_TEST_DATABASE_URL", os.environ["K06_TEST_DATABASE_URL"])

    async def scenario():
        async with _application(tmp_path) as app:
            async def write(barrier, index):
                repo = RuntimeManagement(barrier, audit=True, platform_limits=WorkspacePolicyLimits(
                    maximum_upload_bytes=10000, maximum_workspace_bytes=100000))
                if resource == "project":
                    value = await repo.update_project(credential_digest=app.case.a, workspace_id="a",
                        project_id=app.project, expected_revision=2, request=ChangeProject(
                            schema_version=VERSION, display_name=f"Dự án đồng thời {index}"))
                else:
                    value = await repo.update_settings(credential_digest=app.case.a, workspace_id="a",
                        expected_revision=0, request=ChangeWorkspaceSettings(
                            schema_version=VERSION, maximum_upload_bytes=100 + index))
                return value.model_dump(mode="python")

            winner = await _twenty(app.case.pool, write)
            current = (await app.repo.project(credential_digest=app.case.a, workspace_id="a", project_id=app.project)
                       if resource == "project" else
                       await app.repo.settings(credential_digest=app.case.a, workspace_id="a"))
            assert current.model_dump(mode="python") == winner
            assert winner["revision"] == (3 if resource == "project" else 1)
            action = "project.update" if resource == "project" else "workspace.settings"
            async with app.case.pool.connection() as conn:
                assert await (await conn.execute("SELECT count(*) FROM k06_action_audit WHERE action=%s",
                                                (action,))).fetchone() == (1,)
            assert await app.case.catalog.read_record(credential_digest=app.case.a, workspace_id="a",
                project_id=app.project, record_id=app.initial.record_id) == app.case.initial
    _run(scenario())


async def _http_headers(pool, user, opaque, tokens):
    from aletheia_lab.hosted_auth.sessions import credential_digest

    digest = credential_digest(SecretStr(opaque))
    csrf = tokens.issue("ctx_" + user).get_secret_value()
    async with pool.connection() as conn:
        await conn.execute("UPDATE j01_session_credentials SET digest=%s WHERE digest=%s AND context_id=%s",
            (digest.get_secret_value(), _credential(user).get_secret_value(), "ctx_" + user))
        await conn.execute("UPDATE j01_sessions SET csrf_token=%s WHERE context_id=%s", (csrf, "ctx_" + user))
    return {"cookie": f"__Host-aletheia_session={opaque}; __Host-aletheia_csrf={csrf}",
            "x-csrf-token": csrf, "origin": "https://app.example.invalid"}


def _problem(response, status, code):
    from aletheia_lab.hosted_contract.session_capabilities import Problem

    assert response.status_code == status, response.text
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.headers["cache-control"] == "no-store"
    problem = Problem.model_validate_json(response.content)
    assert (problem.status, problem.code) == (status, code)
    return problem.model_dump(exclude={"request_id"})


def test_http_real_reviews_membership_grants_and_delete_preserve_preconditions_scope_and_etags(tmp_path):
    import httpx
    from test_k06_idempotency_postgres import _bundle

    from aletheia_lab.hosted.catalog_access import CatalogAccess
    from aletheia_lab.hosted.catalog_http import CatalogApplication
    from aletheia_lab.hosted.cursors import ScopedCursors
    from aletheia_lab.hosted.job_http import JobApplication
    from aletheia_lab.hosted.postgres_jobs import PostgresJobs
    from aletheia_lab.hosted.postgres_sources import PostgresSources
    from aletheia_lab.hosted.project_mutations import PostgresProjectMutations
    from aletheia_lab.hosted.report_renderer import ReportRenderer
    from aletheia_lab.hosted.request_boundary import BodyLimits
    from aletheia_lab.hosted.settings import RuntimeSettings
    from aletheia_lab.hosted.source_http import SourceUploadApplication
    from aletheia_lab.hosted.workspace_http import WorkspaceApplication
    from aletheia_lab.hosted_auth.csrf import CsrfTokens
    from aletheia_lab.hosted_auth.postgres_sessions import PostgresSessionStore
    from aletheia_lab.hosted_auth.sessions import ServerSessions
    from aletheia_lab.hosted_auth.settings import AuthPolicy
    from aletheia_lab.hosted_contract.job_page import JobReceipt
    from aletheia_lab.hosted_contract.workspaces import (
        Annotation,
        AnnotationPage,
        DeletedProject,
        Member,
        ProjectGrant,
    )

    async def scenario():
        async with _database() as (pool, schema):
            reviews, grants, catalog, project, result, _ = await _setup(pool, schema, tmp_path)
            tokens = CsrfTokens(SecretStr("synthetic-k06-http-" + "x" * 43))
            headers = {user: await _http_headers(pool, user, character * 43, tokens) for user, character in
                       (("owner", "a"), ("reviewer", "b"), ("other", "c"))}
            runtime = RuntimeSettings.model_validate({"environment": "staging",
                "public_origin": "https://app.example.invalid", "identity": {"issuer": "https://issuer.example.invalid",
                    "client_id": "synthetic-k06", "redirect_uri": "https://app.example.invalid/v1/auth/callback",
                    "client_kind": "public"}, "storage": {"database_url_env": "ALETHEIA_HOSTED_DATABASE_URL",
                                                             "object_root": str(tmp_path)}})
            sessions = ServerSessions(PostgresSessionStore(pool), AuthPolicy(default_return_to="/", return_to_paths=("/",)),
                timeout_seconds=3, maximum_cookie_bytes=runtime.api.maximum_cookie_bytes)
            renderer = ReportRenderer()

            async def fallback(scope, receive, send):
                raise AssertionError("Unexpected HTTP test route")

            async def clock():
                return True

            try:
                access = CatalogAccess(catalog, ScopedCursors(SecretStr("synthetic-k06-cursor-" + "s" * 43)), renderer)
                sources = PostgresSources(pool, maximum_upload_bytes=10000, maximum_expanded_bytes=20000, maximum_entries=10)
                jobs = PostgresJobs(pool, sources)
                app = SourceUploadApplication(fallback, sources, sessions, tokens, runtime,
                    body_limits=BodyLimits(20000, 1, 3, 20), clock_check=clock)
                app = JobApplication(app, jobs, sessions, tokens, runtime,
                    body_limits=BodyLimits(20000, 1, 3, 20), clock_check=clock)
                app = CatalogApplication(app, access, grants, reviews, sessions, tokens, runtime,
                    body_limits=BodyLimits(20000, 1, 3, 20), clock_check=clock,
                    project_mutations=PostgresProjectMutations(pool, catalog))
                app = WorkspaceApplication(app, WorkspaceManagement(pool), sessions, tokens, runtime,
                    body_limits=BodyLimits(20000, 1, 3, 20), clock_check=clock)
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=runtime.public_origin) as client:
                    source_headers = {**headers["owner"], "content-type": "application/zip",
                                      "idempotency-key": "http-source-key-0001"}
                    uploaded = await client.post("/v1/workspaces/wa/sources", content=_bundle(), headers=source_headers)
                    assert uploaded.status_code == 201
                    replayed = await client.post("/v1/workspaces/wa/sources", content=_bundle(), headers=source_headers)
                    assert replayed.status_code == 201 and replayed.json() == uploaded.json()
                    _problem(await client.post("/v1/workspaces/wa/sources", content=_bundle("changed"),
                        headers=source_headers), 409, "idempotency_conflict")
                    another = await client.post("/v1/workspaces/wa/sources", content=_bundle("another"),
                        headers={**source_headers, "idempotency-key": "http-source-key-0002"})
                    assert another.status_code == 201
                    job_headers = {**headers["owner"], "idempotency-key": "http-preview-key-0001"}
                    job_request = {"operation": "preview", "source_id": uploaded.json()["source_id"]}
                    admitted = await client.post("/v1/workspaces/wa/jobs", json=job_request, headers=job_headers)
                    assert admitted.status_code == 202
                    receipt = JobReceipt.model_validate_json(admitted.content)
                    replayed = await client.post("/v1/workspaces/wa/jobs", json=job_request, headers=job_headers)
                    assert replayed.status_code == 202 and JobReceipt.model_validate_json(replayed.content) == receipt
                    _problem(await client.post("/v1/workspaces/wa/jobs", json={"operation": "preview",
                        "source_id": another.json()["source_id"]}, headers=job_headers), 409, "idempotency_conflict")
                    note = f"/v1/workspaces/wa/results/{result}/annotations/http-review"
                    draft = {"body": "Synthetic private review draft", "status": "comment"}
                    _problem(await client.put(note, json=draft, headers=headers["reviewer"]), 428, "precondition_required")
                    created = await client.put(note, json=draft, headers={**headers["reviewer"], "if-match": '"0"'})
                    assert created.status_code == 200, created.text
                    assert created.headers["etag"] == '"1"'
                    first = Annotation.model_validate_json(created.content)
                    assert (first.workspace_id, first.project_id, first.result_id, first.revision) == ("wa", project, result, 1)
                    stale = await client.put(note, json={**draft, "body": "Stale local draft"},
                        headers={**headers["reviewer"], "if-match": '"0"'})
                    _problem(stale, 412, "revision_conflict")
                    assert draft["body"] not in stale.text and "Stale local draft" not in stale.text
                    updated = await client.put(note, json={**draft, "body": "Committed second revision"},
                        headers={**headers["reviewer"], "if-match": created.headers["etag"]})
                    assert updated.status_code == 200 and updated.headers["etag"] == '"2"'
                    assert Annotation.model_validate_json(updated.content).revision == 2
                    history = await client.get(note + "/history", headers=headers["reviewer"])
                    assert history.status_code == 200
                    assert [item.revision for item in AnnotationPage.model_validate_json(history.content).items] == [1, 2]
                    denied = _problem(await client.get(note + "/history", headers=headers["other"]), 404, "not_found")
                    absent = _problem(await client.get(note.replace(result, "missing-result") + "/history",
                        headers=headers["other"]), 404, "not_found")
                    assert denied == absent
                    member = "/v1/workspaces/wa/members/viewer"
                    _problem(await client.patch(member, json={"role": "reviewer"}, headers=headers["owner"]),
                             428, "precondition_required")
                    changed = await client.patch(member, json={"role": "reviewer"},
                        headers={**headers["owner"], "if-match": '"1"'})
                    assert changed.status_code == 200 and changed.headers["etag"] == '"2"'
                    assert Member.model_validate_json(changed.content).revision == 2
                    _problem(await client.patch(member, json={"role": "analyst"},
                        headers={**headers["owner"], "if-match": '"1"'}), 412, "revision_conflict")
                    grant = f"/v1/workspaces/wa/projects/{project}/grants/viewer"
                    _problem(await client.put(grant, json={"actions": ["read"]}, headers=headers["owner"]),
                             428, "precondition_required")
                    granted = await client.put(grant, json={"actions": ["read", "review"]},
                        headers={**headers["owner"], "if-match": '"1"'})
                    assert granted.status_code == 200 and granted.headers["etag"] == '"2"'
                    assert ProjectGrant.model_validate_json(granted.content).revision == 2
                    _problem(await client.put(grant, json={"actions": []},
                        headers={**headers["owner"], "if-match": '"1"'}), 412, "revision_conflict")
                    project_path = f"/v1/workspaces/wa/projects/{project}"
                    _problem(await client.delete(project_path, headers=headers["owner"]), 428, "precondition_required")
                    _problem(await client.delete(project_path, headers={**headers["owner"], "if-match": '"1"'}),
                             412, "revision_conflict")
                    deleted = await client.delete(project_path, headers={**headers["owner"], "if-match": '"0"'})
                    assert deleted.status_code == 200, deleted.text
                    assert deleted.headers["etag"] == '"1"'
                    tombstone = DeletedProject.model_validate_json(deleted.content)
                    assert tombstone.revision == 1 and tombstone.status == "tombstoned"
                    _problem(await client.get(note + "/history", headers=headers["reviewer"]), 404, "not_found")
                    async with pool.connection() as conn:
                        assert await (await conn.execute("SELECT count(*),max(request_id) FROM j01_jobs")).fetchone() == (1, receipt.request_id)
                        assert await (await conn.execute("SELECT count(*),max(revision) FROM j01_annotations")).fetchone() == (2, 2)
                        assert await (await conn.execute("SELECT count(*) FROM k05_records "
                            "WHERE workspace_id='wa' AND project_id=%s AND record_type='deletion'", (project,))).fetchone() == (1,)
                    print("K06 HTTP+PG: 428/412/200 and ETag through real review/member/grant/delete serializers; scoped history retained")
            finally:
                await renderer.aclose()
    _run(scenario())
