"""Durable exports through actual API LOGIN, native catalog, CSRF and report worker."""
from __future__ import annotations

import hashlib
import os
from uuid import uuid4

import httpx
import pytest
from test_k04_native_catalog_http_postgres import _native_application
from test_k04_runtime_principal_postgres import _run
from test_k06_follow_up_http_postgres import _problem

from aletheia_lab.hosted.catalog_access import CatalogAccess
from aletheia_lab.hosted.project_access import PostgresProjectAccess
from aletheia_lab.hosted.report_renderer import ReportRenderer
from aletheia_lab.hosted_contract.workspaces import ExportDescriptor

pytestmark = [pytest.mark.integration, pytest.mark.skipif(not os.environ.get("K02_TEST_DATABASE_URL"),
    reason="Explicit K02 owned-schema PostgreSQL test URL required")]
ORIGIN = "https://app.example.invalid"
PRIVATE = "synthetic-private-export-corruption"


@pytest.fixture(autouse=True)
def configured_database(monkeypatch):
    # This existing native fixture owns random schema/roles and authenticates as the real restricted API LOGIN.
    monkeypatch.setenv("K04_TEST_DATABASE_URL", os.environ["K02_TEST_DATABASE_URL"])


async def _create(client, case, *, actor="owner_a", key="durable-export-key-0001"):
    path = f"/v1/workspaces/{case.workspace}/results/{case.child.record_id}/exports"
    response = await client.post(path, json={"format": "json"},
        headers={**case.headers[actor], "idempotency-key": key})
    assert response.status_code == 201, response.text
    assert response.headers["cache-control"] == "no-store"
    item = ExportDescriptor.model_validate_json(response.content)
    assert item.expires_at is not None and item.result_id == case.child.record_id
    return item


async def _expired_copy(case, item, *, key):
    export_id = f"ex_{item.format}_{item.result_id}_{uuid4().hex}"
    async with case.owner.connection() as conn:
        await conn.execute("INSERT INTO k02_exports(workspace_id,export_id,project_id,creator_id,result_id,"
            "format,object_sha256,request_key_hash,created_at,expires_at) "
            "SELECT workspace_id,%s,project_id,creator_id,result_id,format,object_sha256,%s,"
            "clock_timestamp()-interval '2 minutes',clock_timestamp()-interval '1 minute' "
            "FROM k02_exports WHERE workspace_id=%s AND export_id=%s",
            (export_id, hashlib.sha256(key.encode()).hexdigest(), case.workspace, item.export_id))
    return f"/v1/workspaces/{case.workspace}/exports/{export_id}/download"


def test_persisted_export_replay_and_range_survive_renderer_change_and_outage(tmp_path, monkeypatch):
    async def scenario():
        async with _native_application(tmp_path) as case:
            assert isinstance(case.app._access, CatalogAccess)
            renderer = case.app._access.renderer
            assert isinstance(renderer, ReportRenderer)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=case.app), base_url=ORIGIN) as client:
                original = await _create(client, case)
                headers = {**case.headers["owner_a"], "idempotency-key": "durable-export-key-0001"}
                endpoint = f"/v1/workspaces/{case.workspace}/results/{case.child.record_id}/exports"
                _problem(await client.post(endpoint, json={"format": "markdown"}, headers=headers),
                         409, "idempotency_conflict")
                authorized_other_target = endpoint.replace(case.child.record_id, case.initial.record_id)
                _problem(await client.post(authorized_other_target, json={"format": "json"}, headers=headers),
                         409, "idempotency_conflict")
                response = await client.get(original.download_url, headers=case.headers["owner_a"])
                assert response.status_code == 200
                first_bytes = response.content
                assert len(first_bytes) == original.byte_size and hashlib.sha256(first_bytes).hexdigest() == original.sha256
                actual_render, calls = renderer.render, []

                async def changed_render(report_format, payload):
                    calls.append(report_format)
                    return await actual_render(report_format, payload) + b"\n"

                monkeypatch.setattr(renderer, "render", changed_render)
                assert await _create(client, case) == original
                assert not calls, "An authorized same-key replay must not render or renew the artifact."
                changed = await _create(client, case, key="durable-export-key-0002")
                assert calls == ["json"] and changed.export_id != original.export_id
                assert changed.sha256 != original.sha256 and changed.expires_at >= original.expires_at

                async def unavailable(*args):
                    raise TimeoutError("Synthetic report worker outage")

                monkeypatch.setattr(renderer, "render", unavailable)
                old = await client.get(original.download_url, headers=case.headers["owner_a"])
                new = await client.get(changed.download_url, headers=case.headers["owner_a"])
                assert old.status_code == new.status_code == 200
                assert old.content == first_bytes and new.content == first_bytes + b"\n"
                partial = await client.get(original.download_url,
                    headers={**case.headers["owner_a"], "range": "bytes=3-17"})
                assert partial.status_code == 206 and partial.content == first_bytes[3:18]
                assert partial.headers["content-range"] == f"bytes 3-17/{len(first_bytes)}"
                head = await client.head(original.download_url, headers=case.headers["owner_a"])
                assert head.status_code == 200 and not head.content and int(head.headers["content-length"]) == len(first_bytes)
                assert await _create(client, case) == original
                path = f"/v1/workspaces/{case.workspace}/results/{case.child.record_id}/exports"
                _problem(await client.post(path, json={"format": "json"}, headers={**case.headers["owner_a"],
                    "idempotency-key": "durable-export-worker-down-003"}), 503, "service_unavailable")
                async with case.api.connection() as conn:
                    assert await (await conn.execute("SELECT count(*) FROM k02_exports")).fetchone() == (2,)
                    assert await (await conn.execute("SELECT expires_at FROM k02_exports WHERE workspace_id=%s "
                        "AND export_id=%s", (case.workspace, original.export_id))).fetchone() == (original.expires_at,)
                    assert await (await conn.execute("SELECT count(*) FROM k05_object_references WHERE workspace_id=%s "
                        "AND record_id=%s AND sha256=%s", (case.workspace, original.result_id, original.sha256))).fetchone() == (1,)
    _run(scenario())


def test_export_expiry_is_revealed_only_after_current_scope_creator_and_grant_authorization(tmp_path):
    async def scenario():
        async with (_native_application(tmp_path) as case,
                    httpx.AsyncClient(transport=httpx.ASGITransport(app=case.app), base_url=ORIGIN) as client):
            original = await _create(client, case, actor="analyst")
            expired_key = "durable-export-expired-key-004"
            expired = await _expired_copy(case, original, key=expired_key)
            response = await client.get(expired, headers={**case.headers["analyst"], "range": "bytes=0-0"})
            _problem(response, 410, "export_expired")
            assert "content-range" not in response.headers and "etag" not in response.headers
            endpoint = f"/v1/workspaces/{case.workspace}/results/{case.child.record_id}/exports"
            _problem(await client.post(endpoint, json={"format": "json"},
                headers={**case.headers["analyst"], "idempotency-key": expired_key}), 410, "export_expired")
            fresh = await _create(client, case, actor="analyst", key="durable-export-fresh-key-005")
            assert fresh.export_id not in (original.export_id, expired.split("/")[-2])
            assert (await client.get(fresh.download_url, headers=case.headers["analyst"])).status_code == 200
            async with case.owner.connection() as conn:
                assert await (await conn.execute("SELECT expires_at<=clock_timestamp() FROM k02_exports "
                    "WHERE workspace_id=%s AND export_id=%s", (case.workspace, expired.split("/")[-2]))).fetchone() == (True,)
            _problem(await client.get(expired, headers=case.headers["owner_a"]), 404, "not_found")
            other_scope = expired.replace("/workspaces/" + case.workspace + "/", "/workspaces/" + case.other + "/")
            _problem(await client.get(other_scope, headers=case.headers["owner_b"]), 404, "not_found")
            await PostgresProjectAccess(case.api).set_grant(credential_digest=case.digests["owner_a"],
                workspace_id=case.workspace, project_id=case.initial.project_id, user_id="analyst",
                actions=["read"], expected_revision=1)
            denied = _problem(await client.get(expired, headers=case.headers["analyst"]), 404, "not_found")
            absent = expired.replace(expired.split("/")[-2], f"ex_json_{original.result_id}_{'0' * 32}")
            assert _problem(await client.get(absent, headers=case.headers["analyst"]), 404, "not_found") == denied
            _problem(await client.get(original.download_url, headers=case.headers["analyst"]), 404, "not_found")
            async with case.owner.connection() as conn:
                assert await (await conn.execute("SELECT expires_at FROM k02_exports WHERE workspace_id=%s "
                    "AND export_id=%s", (case.workspace, original.export_id))).fetchone() == (original.expires_at,)
    _run(scenario())


def test_corrupt_export_object_fails_closed_without_rerender_or_partial_success(tmp_path, monkeypatch):
    async def scenario():
        async with (_native_application(tmp_path) as case,
                    httpx.AsyncClient(transport=httpx.ASGITransport(app=case.app), base_url=ORIGIN) as client):
            item = await _create(client, case)
            case.store.path(item.sha256).write_bytes(PRIVATE.encode())

            async def unavailable(*args):
                raise AssertionError("A persisted export must never fall back to the renderer.")

            monkeypatch.setattr(case.app._access.renderer, "render", unavailable)
            broken = await client.get(item.download_url, headers={**case.headers["owner_a"], "range": "bytes=0-3"})
            _problem(broken, 500, "service_failure")
            assert "content-range" not in broken.headers and "etag" not in broken.headers
            assert all(value not in broken.text for value in (PRIVATE, item.sha256, item.export_id, str(tmp_path)))
    _run(scenario())


def test_restricted_api_login_can_publish_export_references_but_cannot_rewrite_artifact_or_identity(tmp_path):
    import psycopg

    async def scenario():
        async with _native_application(tmp_path) as case:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=case.app), base_url=ORIGIN) as client:
                item = await _create(client, case)
            async with case.api.connection() as conn:
                assert await (await conn.execute("SELECT current_user=session_user,"
                    "has_table_privilege(current_user,'k02_exports','SELECT'),"
                    "has_column_privilege(current_user,'k02_exports','export_id','INSERT'),"
                    "has_table_privilege(current_user,'k02_exports','UPDATE')")).fetchone() == (True, True, True, False)
                assert await (await conn.execute("SELECT object_sha256 FROM k02_exports WHERE workspace_id=%s "
                    "AND export_id=%s", (case.workspace, item.export_id))).fetchone() == (item.sha256,)
                assert await (await conn.execute("SELECT count(*) FROM k05_object_references WHERE workspace_id=%s "
                    "AND record_id=%s AND sha256=%s", (case.workspace, item.result_id, item.sha256))).fetchone() == (1,)
            for query, params in [
                ("UPDATE k02_exports SET expires_at=expires_at+interval '1 minute' WHERE export_id=%s", (item.export_id,)),
                ("DELETE FROM k02_exports WHERE export_id=%s", (item.export_id,)),
                ("UPDATE hosted_identity_bindings SET enabled=false WHERE user_id='owner_a'", ()),
                ("ALTER TABLE k02_exports DISABLE TRIGGER k02_export_immutable", ()),
            ]:
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    async with case.api.connection() as conn:
                        await conn.execute(query, params)
            async with case.api.connection() as conn:
                assert await (await conn.execute("SELECT expires_at FROM k02_exports WHERE export_id=%s",
                                                (item.export_id,))).fetchone() == (item.expires_at,)
    _run(scenario())
