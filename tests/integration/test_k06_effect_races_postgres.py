"""Committed HTTP effects versus concurrent native-PG authority/tombstone changes."""
from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager

import httpx
import pytest
from test_k04_native_catalog_http_postgres import _native_application
from test_k04_runtime_principal_postgres import _run
from test_k06_follow_up_http_postgres import _problem

from aletheia_lab.hosted.action_audit_migration import migrate as migrate_audit
from aletheia_lab.hosted.project_access import PostgresProjectAccess
from aletheia_lab.hosted.project_mutations import PostgresProjectMutations
from aletheia_lab.hosted.review_repository import PostgresReviews
from aletheia_lab.hosted.runtime_privileges import grant_runtime
from aletheia_lab.hosted.workspace_management import WorkspaceManagement
from aletheia_lab.hosted_contract.workspaces import ExportDescriptor

pytestmark = [pytest.mark.integration, pytest.mark.skipif(
    not os.environ.get("K06_TEST_DATABASE_URL"), reason="Explicit K06 private PostgreSQL required")]
ORIGIN = "https://app.example.invalid"


@pytest.fixture(autouse=True)
def configured_database(monkeypatch):
    monkeypatch.setenv("K04_TEST_DATABASE_URL", os.environ["K06_TEST_DATABASE_URL"])


class Gate:
    def __init__(self):
        self.entered, self.release = asyncio.Event(), asyncio.Event()

    async def hold(self):
        self.entered.set()
        await asyncio.wait_for(self.release.wait(), 10)


@asynccontextmanager
async def _application(tmp_path):
    async with _native_application(tmp_path) as case:
        await case.api.resize(min_size=2, max_size=2)
        await case.api.wait(timeout=10)
        async with case.api.connection() as conn:
            schema, role = await (await conn.execute("SELECT current_schema(),current_user")).fetchone()
        async with case.owner.connection() as conn:
            await migrate_audit(conn, schema)
            await grant_runtime(conn, schema, role, profile="api", audit=True)
        case.app._mutations = PostgresProjectMutations(case.api, case.app._access.catalog, audit=True)
        case.app._reviews = PostgresReviews(case.api, case.app._access.catalog, audit=True)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=case.app), base_url=ORIGIN) as client:
            yield case, client


async def _row_counts(case):
    async with case.owner.connection() as conn:
        exports = (await (await conn.execute("SELECT count(*) FROM k02_exports")).fetchone())[0]
        refs = (await (await conn.execute("SELECT count(*) FROM k05_object_references r JOIN k02_exports e "
            "ON e.workspace_id=r.workspace_id AND e.result_id=r.record_id AND e.object_sha256=r.sha256")).fetchone())[0]
        project = await (await conn.execute("SELECT revision,deletion_tombstone_id FROM k05_projects "
            "WHERE workspace_id=%s AND project_id=%s", (case.workspace, case.initial.project_id))).fetchone()
        immutable = await (await conn.execute("SELECT canonical_sha256,object_sha256 FROM k05_records "
            "WHERE workspace_id=%s AND record_id=%s", (case.workspace, case.child.record_id))).fetchone()
    return exports, refs, project, immutable


async def _lose_access(case, client, loss):
    owner = case.digests["owner_a"]
    if loss == "delete":
        response = await client.request("DELETE", f"/v1/workspaces/{case.workspace}/projects/{case.initial.project_id}",
            headers={**case.headers["owner_a"], "if-match": '"3"'}, json={})
        assert response.status_code == 200, response.text
        assert response.json()["revision"] == 4 and response.json()["retention"] == "retained"
    elif loss == "project":
        await PostgresProjectAccess(case.api).set_grant(credential_digest=owner, workspace_id=case.workspace,
            project_id=case.initial.project_id, user_id="analyst", actions=["read"], expected_revision=1)
    elif loss == "source":
        await WorkspaceManagement(case.api).set_source_grant(credential_digest=owner, workspace_id=case.workspace,
            source_id=case.source.source_id, user_id="analyst", actions=["read"], expected_revision=1)
    else:
        assert loss == "membership"
        async with case.owner.connection() as conn:
            revision = (await (await conn.execute("SELECT revision FROM j01_memberships "
                "WHERE workspace_id=%s AND user_id='analyst'", (case.workspace,))).fetchone())[0]
        await WorkspaceManagement(case.api).mutate_member(credential_digest=owner, workspace_id=case.workspace,
            user_id="analyst", active=False, expected_revision=revision)


@pytest.mark.parametrize("loss", ["membership", "project", "source", "delete"])
@pytest.mark.parametrize("committed_first", [False, True], ids=["authority-first", "export-first"])
def test_http_export_loss_order_keeps_committed_truth_and_blocks_new_delivery(tmp_path, monkeypatch, loss, committed_first):
    async def scenario():
        async with _application(tmp_path) as (case, client):
            before = await _row_counts(case)
            gate = Gate()
            access = case.app._access
            if committed_first:
                original = access.create_export

                async def held_create(*args, **kwargs):
                    item = await original(*args, **kwargs)
                    await gate.hold()
                    return item

                monkeypatch.setattr(access, "create_export", held_create)
            else:
                renderer = access.renderer
                original_render = renderer.render

                async def held_render(*args, **kwargs):
                    payload = await original_render(*args, **kwargs)
                    await gate.hold()
                    return payload

                monkeypatch.setattr(renderer, "render", held_render)
            endpoint = f"/v1/workspaces/{case.workspace}/results/{case.child.record_id}/exports"
            headers = {**case.headers["analyst"], "idempotency-key": "k06-native-race-export-0001"}
            task = asyncio.create_task(client.post(endpoint, headers=headers, json={"format": "json"}))
            try:
                await asyncio.wait_for(gate.entered.wait(), 10)
                # The awaited callback has released its transaction: another real API connection can commit.
                intermediate = await _row_counts(case)
                assert intermediate[:2] == ((1, 1) if committed_first else (0, 0))
                await _lose_access(case, client, loss)
            finally:
                gate.release.set()
            response = await asyncio.wait_for(task, 10)
            _problem(response, 404, "not_found")
            assert "download_url" not in response.text and "etag" not in response.headers
            after = await _row_counts(case)
            assert after[:2] == ((1, 1) if committed_first else (0, 0))
            assert after[3] == before[3]
            assert after[2][0] == (4 if loss == "delete" else 3)
            assert bool(after[2][1]) == (loss == "delete")
            _problem(await client.post(endpoint, headers=headers, json={"format": "json"}), 404, "not_found")
            if committed_first:
                async with case.owner.connection() as conn:
                    export_id = (await (await conn.execute("SELECT export_id FROM k02_exports")).fetchone())[0]
                _problem(await client.get(f"/v1/workspaces/{case.workspace}/exports/{export_id}/download",
                    headers=case.headers["analyst"]), 404, "not_found")
            assert (await _row_counts(case))[:2] == after[:2]
    _run(scenario())


@pytest.mark.parametrize("loss", ["membership", "project", "source", "delete"])
def test_http_download_rechecks_loss_after_verified_object_read_before_any_bytes(tmp_path, monkeypatch, loss):
    async def scenario():
        async with _application(tmp_path) as (case, client):
            endpoint = f"/v1/workspaces/{case.workspace}/results/{case.child.record_id}/exports"
            created = await client.post(endpoint, headers={**case.headers["analyst"],
                "idempotency-key": "k06-native-download-race-001"}, json={"format": "json"})
            assert created.status_code == 201, created.text
            item = ExportDescriptor.model_validate_json(created.content)
            objects = case.app._access.catalog.objects
            read, gate = objects.read, Gate()

            async def held_read(workspace, descriptor, **kwargs):
                payload = await read(workspace, descriptor, **kwargs)
                if descriptor.sha256 == item.sha256:
                    await gate.hold()
                return payload

            monkeypatch.setattr(objects, "read", held_read)
            task = asyncio.create_task(client.get(item.download_url,
                headers={**case.headers["analyst"], "range": "bytes=0-31"}))
            try:
                await asyncio.wait_for(gate.entered.wait(), 10)
                await _lose_access(case, client, loss)
            finally:
                gate.release.set()
            response = await asyncio.wait_for(task, 10)
            _problem(response, 404, "not_found")
            assert "etag" not in response.headers and "content-range" not in response.headers
            assert all(secret not in response.text for secret in (item.sha256, item.export_id, case.child.record_id))
            assert (await _row_counts(case))[:2] == (1, 1)
    _run(scenario())


def test_two_native_http_notes_same_revision_commit_one_history_row_and_audit(tmp_path):
    async def scenario():
        async with _application(tmp_path) as (case, client):
            class ConcurrentReviews(PostgresReviews):
                pids = set()
                barrier = asyncio.Barrier(2)

                async def _current(self, conn, *args):
                    self.pids.add((await (await conn.execute("SELECT pg_backend_pid()")).fetchone())[0])
                    await asyncio.wait_for(self.barrier.wait(), 10)
                    return await super()._current(conn, *args)

            reviews = ConcurrentReviews(case.api, case.app._access.catalog, audit=True)
            case.app._reviews = reviews
            path = f"/v1/workspaces/{case.workspace}/results/{case.child.record_id}/annotations/racing-note"
            before = await _row_counts(case)
            responses = await asyncio.wait_for(asyncio.gather(*(client.put(path,
                headers={**case.headers["analyst"], "if-match": '"0"'},
                json={"body": body, "status": "comment"}) for body in ("First draft", "Second draft"))), 15)
            assert sorted(r.status_code for r in responses) == [200, 412]
            winner = next(r.json() for r in responses if r.status_code == 200)
            assert len(reviews.pids) == 2 and winner["revision"] == 1
            case.app._reviews = PostgresReviews(case.api, case.app._access.catalog, audit=True)
            history = await client.get(path + "/history", headers=case.headers["analyst"])
            assert history.status_code == 200 and history.json()["items"] == [winner]
            async with case.owner.connection() as conn:
                assert await (await conn.execute("SELECT count(*),max(revision) FROM j01_annotations")).fetchone() == (1, 1)
                assert await (await conn.execute("SELECT count(*) FROM k06_action_audit WHERE action='review.put'")).fetchone() == (1,)
            assert (await _row_counts(case))[3] == before[3]
    _run(scenario())


@pytest.mark.parametrize("committed_first", [False, True], ids=["role-first", "note-first"])
def test_native_http_role_downgrade_vs_note_preserves_linearized_effect(tmp_path, monkeypatch, committed_first):
    async def scenario():
        async with _application(tmp_path) as (case, client):
            reviews, gate = case.app._reviews, Gate()
            if committed_first:
                original = reviews.put

                async def held_put(**kwargs):
                    value = await original(**kwargs)
                    await gate.hold()
                    return value

                monkeypatch.setattr(reviews, "put", held_put)
            else:
                original_load = reviews._load_result

                async def held_load(*args):
                    project = await original_load(*args)
                    await gate.hold()
                    return project

                monkeypatch.setattr(reviews, "_load_result", held_load)
            path = f"/v1/workspaces/{case.workspace}/results/{case.child.record_id}/annotations/role-note"
            task = asyncio.create_task(client.put(path, headers={**case.headers["analyst"], "if-match": '"0"'},
                json={"body": "The authorized draft", "status": "comment"}))
            try:
                await asyncio.wait_for(gate.entered.wait(), 10)
                async with case.owner.connection() as conn:
                    expected = (await (await conn.execute("SELECT revision FROM j01_memberships "
                        "WHERE workspace_id=%s AND user_id='analyst'", (case.workspace,))).fetchone())[0]
                await WorkspaceManagement(case.api, audit=True).mutate_member(credential_digest=case.digests["owner_a"],
                    workspace_id=case.workspace, user_id="analyst", role="viewer", expected_revision=expected)
            finally:
                gate.release.set()
            response = await asyncio.wait_for(task, 10)
            if committed_first:
                assert response.status_code == 200 and response.json()["revision"] == 1
            else:
                _problem(response, 404, "not_found")
            async with case.owner.connection() as conn:
                assert await (await conn.execute("SELECT count(*) FROM j01_annotations")).fetchone() == (int(committed_first),)
                assert await (await conn.execute("SELECT count(*) FROM k06_action_audit WHERE action='review.put'")).fetchone() == (int(committed_first),)
                assert await (await conn.execute("SELECT role,active,revision FROM j01_memberships "
                    "WHERE workspace_id=%s AND user_id='analyst'", (case.workspace,))).fetchone() == ("viewer", True, expected + 1)
            _problem(await client.put(path, headers={**case.headers["analyst"], "if-match": '"1"'},
                json={"body": "Forbidden new draft", "status": "approved"}), 404, "not_found")
            history = await client.get(path + "/history", headers=case.headers["analyst"])
            assert history.status_code == 200 and len(history.json()["items"]) == int(committed_first)
    _run(scenario())
