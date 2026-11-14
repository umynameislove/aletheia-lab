"""Current real source ACL is rechecked after native list SQL and before response."""
from __future__ import annotations

import asyncio
import os

import httpx
import pytest
from test_k04_native_catalog_http_postgres import _native_application
from test_k04_runtime_principal_postgres import _run

from aletheia_lab.hosted.workspace_management import WorkspaceManagement

pytestmark = [pytest.mark.integration, pytest.mark.skipif(not os.environ.get("K04_TEST_DATABASE_URL"),
    reason="Explicit private K04 test database required")]


@pytest.mark.parametrize("project_scoped", [False, True])
def test_native_page_source_revoke_commits_after_page_query_before_headers(tmp_path, monkeypatch, project_scoped):
    async def scenario():
        async with _native_application(tmp_path) as case:
            manager = WorkspaceManagement(case.api)
            current = await manager.read_source_grant(credential_digest=case.digests["owner_a"],
                workspace_id=case.workspace, source_id=case.source.source_id, user_id="analyst")
            grant = await manager.set_source_grant(credential_digest=case.digests["owner_a"],
                workspace_id=case.workspace, source_id=case.source.source_id, user_id="analyst",
                actions=["read"], expected_revision=current["revision"])
            queried, release = asyncio.Event(), asyncio.Event()
            access = case.app._access
            method = "records" if project_scoped else "workspace_records"
            original = getattr(access, method)

            async def captured_page(*args, **kwargs):
                page = await original(*args, **kwargs)
                assert case.initial.record_id in [item["record_id"] for item in page["items"]]
                queried.set()
                await release.wait()
                return page

            monkeypatch.setattr(access, method, captured_page)
            path = f"/v1/workspaces/{case.workspace}/results"
            params = {"project_id": case.initial.project_id} if project_scoped else {}
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=case.app),
                    base_url="https://app.example.invalid", headers=case.headers["analyst"]) as client:
                task = asyncio.create_task(client.get(path, params=params))
                try:
                    async with asyncio.timeout(8):
                        await queried.wait()
                        assert not task.done()
                        await manager.set_source_grant(credential_digest=case.digests["owner_a"],
                            workspace_id=case.workspace, source_id=case.source.source_id, user_id="analyst",
                            actions=[], expected_revision=grant["revision"])
                        release.set()
                        response = await task
                    assert response.status_code == 404 and response.json()["code"] == "not_found"
                    assert case.initial.record_id.encode() not in response.content
                    assert case.snapshot.snapshot_id.encode() not in response.content
                finally:
                    release.set()
                    if not task.done():
                        task.cancel()
                    await asyncio.gather(task, return_exceptions=True)

    _run(scenario())
