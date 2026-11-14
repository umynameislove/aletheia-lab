"""A native page must recheck its exact records after the query and before headers."""
from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import SecretStr

from aletheia_lab.hosted.authorization import Authorization, AuthorizationDenied
from aletheia_lab.hosted.catalog_access import CatalogAccess
from aletheia_lab.hosted.catalog_http import CatalogApplication
from aletheia_lab.hosted.postgres_catalog import PostgresCatalog

DIGEST = SecretStr("a" * 64)
WORKSPACE, PROJECT, RECORD = "workspace-own", "project-own", "native-record-private"


@pytest.mark.parametrize("path,query,kind", [
    (f"/v1/workspaces/{WORKSPACE}/results", b"", "upload_result"),
    (f"/v1/workspaces/{WORKSPACE}/results", f"project_id={PROJECT}".encode(), "upload_result"),
    (f"/v1/workspaces/{WORKSPACE}/projects/{PROJECT}/records", b"kind=result", "upload_result"),
    (f"/v1/workspaces/{WORKSPACE}/projects/{PROJECT}/snapshots", b"", "upload_snapshot"),
])
@pytest.mark.parametrize("revoke", [False, True])
def test_native_page_denies_source_revoke_after_query_before_delivery(path, query, kind, revoke):
    async def scenario():
        queried, release = asyncio.Event(), asyncio.Event()
        messages, checks = [], []
        allowed = True

        @asynccontextmanager
        async def connection():
            yield object()

        class Catalog:
            pool = SimpleNamespace(connection=connection)
            policy = SimpleNamespace(authorize=AsyncMock(return_value=Authorization("actor", "analyst", 1, WORKSPACE)),
                authorize_project=AsyncMock())

            async def check_records(self, **kwargs):
                checks.append(kwargs)
                if not allowed:
                    raise AuthorizationDenied()

        class Access(CatalogAccess):
            async def records(self, *args, **kwargs):
                # The bounded page was selected while source access was still valid.
                value = {"schema_version": "product-api/v1-draft", "workspace_id": WORKSPACE,
                    "items": [{"record_id": RECORD, "project_id": PROJECT, "record_type": kind,
                        "schema_version": "native/v1", "canonical_sha256": "b" * 64,
                        "snapshot_id": None, "parent_result_id": None}],
                    "next_cursor": None, "total": None, "total_kind": "unknown"}
                queried.set()
                await release.wait()
                return value

            workspace_records = records

        app = object.__new__(CatalogApplication)
        app._access = Access(Catalog(), None, None)
        app._runtime = SimpleNamespace(api=SimpleNamespace(backend_timeout_seconds=2,
            request_timeout_seconds=5, maximum_response_bytes=1024 * 1024))
        app._clock = AsyncMock(return_value=True)
        app._session = AsyncMock(return_value=(DIGEST, SimpleNamespace(context_id="context-own")))

        async def receive():
            raise AssertionError("read route must not consume a body")

        async def send(message):
            messages.append(message)

        task = asyncio.create_task(app._route_with_context({"type": "http", "method": "GET", "path": path,
            "query_string": query, "headers": []}, receive, send, "request-own"))
        try:
            async with asyncio.timeout(5):
                await queried.wait()
                assert not messages
                allowed = not revoke
                release.set()
                await task
        finally:
            release.set()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        assert messages[0]["status"] == (404 if revoke else 200)
        body = b"".join(message.get("body", b"") for message in messages)
        assert (RECORD.encode() in body) is not revoke
        assert len(checks) == 1
        assert checks[0]["record_ids"] == (RECORD,)
        if revoke:
            assert json.loads(body)["code"] == "not_found"

    asyncio.run(scenario())


@pytest.mark.parametrize("size,count", [(0, 0), (1, 1), (100, 100), (100, 99), (1, None)])
def test_record_page_checkpoint_checks_one_bounded_current_source_and_project_query(size, count):
    queries = []

    class Connection:
        async def execute(self, query, params):
            queries.append((query, params))
            return SimpleNamespace(fetchone=AsyncMock(return_value=None if count is None else (count,)))

    @asynccontextmanager
    async def connection():
        yield Connection()

    policy = SimpleNamespace(authorize=AsyncMock(return_value=Authorization("actor", "analyst", 2, WORKSPACE)),
        authorize_project=AsyncMock(), authorize_record=AsyncMock())
    catalog = PostgresCatalog(SimpleNamespace(connection=connection), None, policy=policy)
    ids = tuple(f"record-{index}" for index in range(size))
    call = catalog.check_records(credential_digest=DIGEST, workspace_id=WORKSPACE,
        record_ids=ids, project_id=PROJECT)
    if count == size:
        asyncio.run(call)
    else:
        with pytest.raises(AuthorizationDenied):
            asyncio.run(call)
    assert len(queries) == 1
    query, params = queries[0]
    assert "r.record_id=ANY(%s)" in query and params[:2] == (WORKSPACE, list(ids))
    assert "j01_source_grants" in query and "j01_project_grants" in query and "record_type='deletion'" in query
    assert "n.record_type='upload_snapshot'" in query and "n.project_id=r.project_id" in query
    assert "LIMIT" not in query
    policy.authorize_record.assert_not_awaited()
    policy.authorize_project.assert_awaited_once()
