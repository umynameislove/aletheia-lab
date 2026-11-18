"""Regression candidate: native canonical routes must inherit their source scope."""
from __future__ import annotations

import os
import time

import httpx
import pytest
from pydantic import SecretStr
from test_k04_native_catalog_http_postgres import _native_application, _stable_zip
from test_k04_runtime_principal_postgres import _run

from aletheia_lab.hosted import catalog_http
from aletheia_lab.hosted.authorization import AuthorizationDenied, PostgresAuthorization
from aletheia_lab.hosted.canonical_results import CanonicalResults
from aletheia_lab.hosted.native_upload_records import build_initial_upload_result
from aletheia_lab.hosted.postgres_catalog import PostgresCatalog
from aletheia_lab.hosted.postgres_objects import PostgresObjects
from aletheia_lab.hosted.postgres_sources import PostgresSources
from aletheia_lab.hosted.upload_analysis import analyze_initial_upload
from aletheia_lab.hosted.upload_publication import UploadPublication
from aletheia_lab.hosted.upload_snapshot import build_upload_snapshot
from aletheia_lab.hosted.workspace_management import WorkspaceManagement

pytestmark = [pytest.mark.integration, pytest.mark.skipif(not os.environ.get("K04_TEST_DATABASE_URL"),
    reason="Explicit private K04 test database required")]


def test_native_canonical_routes_do_not_bypass_revoked_source_read(tmp_path):
    async def scenario():
        async with _native_application(tmp_path) as case:
            manager = WorkspaceManagement(case.api)
            actor, admin = case.digests["analyst"], case.digests["owner_a"]
            previous = await manager.read_source_grant(credential_digest=admin, workspace_id=case.workspace,
                source_id=case.source.source_id, user_id="analyst")
            grant = await manager.set_source_grant(credential_digest=admin, workspace_id=case.workspace,
                source_id=case.source.source_id, user_id="analyst", actions=["read", "export"],
                expected_revision=previous["revision"])
            # The canonical fixture is operator-enlisted; install its exact legacy linkage
            # only to compare the existing Source.snapshot authority path to catalog reads.
            async with case.owner.connection() as conn:
                await conn.execute("INSERT INTO j01_upload_snapshots(snapshot_id,workspace_id,source_id,payload,"
                    "canonical_record_id) VALUES(%s,%s,%s,%s::jsonb,%s) ON CONFLICT DO NOTHING",
                    (case.snapshot.snapshot_id, case.workspace, case.source.source_id,
                     case.snapshot.model_dump_json(), case.snapshot.snapshot_id))
            catalog = PostgresCatalog(case.api, PostgresObjects(case.api, case.store))
            sources = PostgresSources(case.api, maximum_upload_bytes=10000, maximum_expanded_bytes=20000,
                maximum_entries=10, catalog=catalog)
            assert await sources.snapshot(credential_digest=actor, workspace_id=case.workspace,
                                          snapshot_id=case.snapshot.snapshot_id) == case.snapshot
            base = f"/v1/workspaces/{case.workspace}"
            result = base + "/results/" + case.initial.record_id
            snapshot = base + "/projects/" + case.initial.project_id + "/snapshots/" + case.snapshot.snapshot_id
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=case.app),
                    base_url="https://app.example.invalid", headers=case.headers["analyst"]) as client:
                assert (await client.get(result)).status_code == 200
                export = await client.post(result + "/exports", json={"format": "json"})
                assert export.status_code == 201
                download = export.json()["download_url"]
                assert (await client.get(download)).status_code == 200
                await manager.set_source_grant(credential_digest=admin, workspace_id=case.workspace,
                    source_id=case.source.source_id, user_id="analyst", actions=[], expected_revision=grant["revision"])
                with pytest.raises(AuthorizationDenied):
                    await sources.snapshot(credential_digest=actor, workspace_id=case.workspace,
                                           snapshot_id=case.snapshot.snapshot_id)
                statuses = {}
                for name, path in (("native-snapshot", snapshot), ("result", result),
                                   ("claims", result + "/claims"), ("graph", result + "/graph"),
                                   ("existing-download", download)):
                    statuses[name] = (await client.get(path)).status_code
                statuses["new-export"] = (await client.post(result + "/exports", json={"format": "json"})).status_code
                assert statuses == dict.fromkeys(statuses, 404), statuses
                page = await client.get(base + "/results")
                assert page.status_code == 200 and page.json()["items"] == []

    _run(scenario())


async def _grant(case, actions, source_id=None):
    source_id = case.source.source_id if source_id is None else source_id
    manager = WorkspaceManagement(case.api)
    current = await manager.read_source_grant(credential_digest=case.digests["owner_a"],
        workspace_id=case.workspace, source_id=source_id, user_id="analyst")
    return await manager.set_source_grant(credential_digest=case.digests["owner_a"],
        workspace_id=case.workspace, source_id=source_id, user_id="analyst", actions=actions,
        expected_revision=current["revision"])


def test_native_review_and_export_require_both_parents_corresponding_action(tmp_path):
    async def scenario():
        async with _native_application(tmp_path) as case:
            result = f"/v1/workspaces/{case.workspace}/results/{case.initial.record_id}"
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=case.app),
                    base_url="https://app.example.invalid", headers=case.headers["analyst"]) as client:
                for index, (actions, review, export) in enumerate([
                    (["read"], 404, 404), (["read", "review"], 200, 404), (["read", "export"], 404, 201),
                ]):
                    await _grant(case, actions)
                    assert (await client.get(result)).status_code == 200
                    response = await client.put(result + f"/annotations/native-scope-{index}",
                        headers={"if-match": '"0"'}, json={"body": "Exact parent scope", "status": "comment"})
                    assert response.status_code == review
                    assert (await client.post(result + "/exports", json={"format": "json"})).status_code == export
                await case.app._projects.set_grant(credential_digest=case.digests["owner_a"],
                    workspace_id=case.workspace, project_id=case.initial.project_id, user_id="analyst",
                    actions=[], expected_revision=1)
                assert (await client.get(result)).status_code == 404
            async with case.owner.connection() as conn:
                assert await (await conn.execute("SELECT count(*) FROM j01_annotations")).fetchone() == (1,)

    _run(scenario())


def test_multiple_native_sources_share_project_and_filter_before_limit(tmp_path):
    async def scenario():
        async with _native_application(tmp_path) as case:
            source = await PostgresSources(case.api, maximum_upload_bytes=10000, maximum_expanded_bytes=20000,
                maximum_entries=10).put(credential_digest=case.digests["owner_a"], workspace_id=case.workspace,
                    request_key=SecretStr("synthetic-native-second-source"), payload=_stable_zip())
            snapshot = build_upload_snapshot(workspace_id=case.workspace, source_id=source.source_id,
                source_sha256=source.sha256, mapping=case.snapshot.mapping, mapping_result=case.snapshot.mapping_result,
                captured_at=case.snapshot.captured_at)
            question = "What is observed in this second exact source?"
            initial = build_initial_upload_result(snapshot, question, analyze_initial_upload(snapshot, question))
            assert initial.project_id == case.initial.project_id and snapshot.snapshot_id != case.snapshot.snapshot_id
            objects = PostgresObjects(case.owner, case.store)
            uploads, results, policy = UploadPublication(objects), CanonicalResults(objects), PostgresAuthorization()
            prepared_snapshot = await uploads.prepare_snapshot(snapshot)
            prepared_result = await results.prepare_initial_result(case.workspace, snapshot=snapshot, record=initial)
            async with case.owner.connection() as conn:
                revision = (await (await conn.execute("SELECT revision FROM k05_projects WHERE workspace_id=%s "
                    "AND project_id=%s", (case.workspace, initial.project_id))).fetchone())[0]
                auth = await policy.authorize(conn, case.digests["owner_a"].get_secret_value(), case.workspace, "confirm")
                published = await uploads.enlist_snapshot(conn, authorization=auth, prepared=prepared_snapshot,
                    expected_revision=revision)
                auth = await policy.authorize(conn, case.digests["owner_a"].get_secret_value(), case.workspace, "analyze")
                await results.enlist_result(conn, authorization=auth, prepared=prepared_result,
                    expected_revision=published.revision)
            groups = [(case.source.source_id, case.snapshot.snapshot_id, [case.initial.record_id, case.child.record_id]),
                      (source.source_id, snapshot.snapshot_id, [initial.record_id])]
            # Put an unauthorized row first so post-LIMIT filtering cannot pass accidentally.
            allowed = max(groups, key=lambda group: min(group[2]))
            denied = next(group for group in groups if group != allowed)
            for source_id, _, _ in groups:
                await _grant(case, ["read", "review", "export"] if source_id == allowed[0] else [], source_id)
            base = f"/v1/workspaces/{case.workspace}"
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=case.app),
                    base_url="https://app.example.invalid", headers=case.headers["analyst"]) as client:
                for path in (base + "/results", base + "/projects/" + initial.project_id + "/records"):
                    items, cursor = [], None
                    while True:
                        params = {"limit": 1, **({"kind": "result"} if path.endswith("/records") else {})}
                        if cursor:
                            params["cursor"] = cursor
                        page = await client.get(path, params=params)
                        assert page.status_code == 200
                        items.extend(item["record_id"] for item in page.json()["items"])
                        cursor = page.json()["next_cursor"]
                        if cursor is None:
                            break
                        assert len(items) <= len(allowed[2])
                    assert items == sorted(allowed[2])
                aliases = await client.get(base + "/projects/" + initial.project_id + "/snapshots")
                assert [item["record_id"] for item in aliases.json()["items"]] == [allowed[1]]
                assert (await client.get(base + "/results/" + allowed[2][0])).status_code == 200
                assert (await client.get(base + "/results/" + denied[2][0])).status_code == 404
                projects = await client.get(base + "/projects")
                assert [item["project_id"] for item in projects.json()["items"]] == [initial.project_id]

    _run(scenario())


def test_source_revoke_between_native_download_chunks_sends_no_more_bytes(tmp_path, monkeypatch):
    async def scenario():
        async with _native_application(tmp_path) as case:
            await _grant(case, ["read", "export"])
            result = f"/v1/workspaces/{case.workspace}/results/{case.initial.record_id}"
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=case.app),
                    base_url="https://app.example.invalid", headers=case.headers["analyst"]) as client:
                export = await client.post(result + "/exports", json={"format": "json"})
                assert export.status_code == 201
                path = export.json()["download_url"]
                full = await client.get(path)
                assert full.status_code == 200 and len(full.content) > 32
            monkeypatch.setattr(catalog_http, "_CHUNK_BYTES", 32)
            messages, revoked_at = [], None

            async def send(message):
                nonlocal revoked_at
                messages.append(message)
                if message.get("type") == "http.response.body" and message.get("body") and revoked_at is None:
                    await _grant(case, [])
                    revoked_at = time.perf_counter()

            async def receive():
                return {"type": "http.request", "body": b"", "more_body": False}

            scope = {"type": "http", "method": "GET", "scheme": "https", "path": path, "query_string": b"",
                "headers": [(b"host", b"app.example.invalid"),
                    *[(name.encode(), value.encode()) for name, value in case.headers["analyst"].items()]]}
            with pytest.raises(AuthorizationDenied):
                await case.app(scope, receive, send)
            assert revoked_at is not None
            denial_seconds = time.perf_counter() - revoked_at
            assert denial_seconds <= 5
            assert [message["body"] for message in messages if message.get("type") == "http.response.body"] == [full.content[:32]]
            assert len([message for message in messages if message.get("type") == "http.response.start"]) == 1
            print(f"K04_NATIVE_SOURCE_REVOKE commit-return-to-denied={denial_seconds:.6f}s; "
                  "testchunk=32; bytes-before=32; bytes-after=0; ASGI+actual API LOGIN")

    _run(scenario())
