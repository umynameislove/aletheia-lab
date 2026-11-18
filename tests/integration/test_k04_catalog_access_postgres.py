"""Canonical catalog facade paging/search/export with current project grants."""
from __future__ import annotations

import asyncio
import hashlib
import os

import pytest
from pydantic import SecretStr
from test_k04_project_policy_postgres import _credential, _database, _run
from test_k04_reviews_postgres import _setup

from aletheia_lab.hosted.authorization import AuthorizationDenied
from aletheia_lab.hosted.catalog_access import CatalogAccess
from aletheia_lab.hosted.cursors import CursorInvalid, ScopedCursors
from aletheia_lab.hosted.report_renderer import ReportRenderer
from aletheia_lab.hosted.workspace_management import WorkspaceManagement
from aletheia_lab.product.reports import render_product_report

pytestmark = [pytest.mark.integration, pytest.mark.skipif(not os.environ.get("K04_TEST_DATABASE_URL"),
    reason="Explicit K-04 PostgreSQL test URL required")]


def _cursors():
    return ScopedCursors(SecretStr("synthetic-catalog-cursor-key-" + "x" * 43))


def test_signed_project_record_and_workspace_pages_bind_actor_context_query_and_revision(tmp_path):
    async def scenario():
        async with _database() as (pool, schema):
            _, _, catalog, project, result, _ = await _setup(pool, schema, tmp_path)
            renderer = ReportRenderer()
            try:
                access = CatalogAccess(catalog, _cursors(), renderer)
                projects = await access.projects(_credential("owner"), "wa", "ctx_owner", limit=1)
                cursor = projects["next_cursor"]
                assert cursor and projects["total"] is None and projects["total_kind"] == "unknown"
                more = await access.projects(_credential("owner"), "wa", "ctx_owner", limit=1, cursor=cursor)
                assert more["items"] and more["items"][0] != projects["items"][0]
                for user, workspace, context, kwargs in (
                    ("viewer", "wa", "ctx_viewer", {"limit": 1}),
                    ("other", "wb", "ctx_other", {"limit": 1}),
                    ("owner", "wa", "ctx_owner", {"limit": 2}),
                    ("owner", "wa", "ctx_owner", {"limit": 1, "search": project}),
                ):
                    with pytest.raises(CursorInvalid):
                        await access.projects(_credential(user), workspace, context, cursor=cursor, **kwargs)
                with pytest.raises(AuthorizationDenied):
                    await access.projects(_credential("owner"), "wa", "ctx_viewer", limit=1, cursor=cursor)
                with pytest.raises(CursorInvalid):
                    await access.workspace_records(_credential("owner"), "wa", "ctx_owner", kind="result", limit=1, cursor=cursor)
                selected = await access.projects(_credential("owner"), "wa", "ctx_owner", search=project)
                assert [item["project_id"] for item in selected["items"]] == [project]
                literal = await access.projects(_credential("owner"), "wa", "ctx_owner", search="_%")
                assert literal["items"] == []
                records = await access.records(_credential("viewer"), "wa", project, "ctx_viewer", kind="result", limit=1)
                record_cursor = records["next_cursor"]
                assert record_cursor and "object_sha256" not in records["items"][0]
                remaining = await access.records(_credential("viewer"), "wa", project, "ctx_viewer", kind="result", limit=1,
                                                 cursor=record_cursor)
                assert len(remaining["items"]) == 1 and remaining["next_cursor"] is None
                assert remaining["items"][0]["record_id"] != records["items"][0]["record_id"]
                with pytest.raises(CursorInvalid):
                    await access.records(_credential("viewer"), "wa", project, "ctx_viewer", kind="snapshot", limit=1,
                                         cursor=record_cursor)
                searched = await access.records(_credential("owner"), "wa", project, "ctx_owner", kind="result", search=result)
                assert [item["record_id"] for item in searched["items"]] == [result]
                global_page = await access.workspace_records(_credential("viewer"), "wa", "ctx_viewer", kind="result", limit=1)
                assert global_page["next_cursor"] and "object_sha256" not in global_page["items"][0]
                global_next = await access.workspace_records(_credential("viewer"), "wa", "ctx_viewer", kind="result", limit=1,
                                                              cursor=global_page["next_cursor"])
                assert len(global_next["items"]) == 1 and global_next["next_cursor"] is None
                await WorkspaceManagement(pool).mutate_member(credential_digest=_credential("owner"), workspace_id="wa",
                    user_id="viewer", expected_revision=1, role="reviewer")
                with pytest.raises(CursorInvalid):
                    await access.workspace_records(_credential("viewer"), "wa", "ctx_viewer", kind="result", limit=1,
                                                   cursor=global_page["next_cursor"])
            finally:
                await renderer.aclose()
    _run(scenario())


def test_result_and_export_require_current_read_and_export_grant_across_real_render(tmp_path):
    async def scenario():
        async with _database() as (pool, schema):
            _, manager, catalog, project, result, _ = await _setup(pool, schema, tmp_path)
            renderer = ReportRenderer(timeout_seconds=20)
            try:
                access = CatalogAccess(catalog, _cursors(), renderer)
                restored_project, view = await access.result(_credential("viewer"), "wa", result)
                assert restored_project == project and view.result.id == result
                with pytest.raises(AuthorizationDenied):
                    await access.result(_credential("other"), "wb", result)
                with pytest.raises(AuthorizationDenied):
                    await access.export(_credential("viewer"), "wa", result, "json")
                await manager.set_grant(credential_digest=_credential("owner"), workspace_id="wa", project_id=project,
                    user_id="viewer", actions=["read", "export"], expected_revision=1)
                actual_project, payload, digest = await access.export(_credential("viewer"), "wa", result, "json")
                assert actual_project == project and payload == render_product_report(view, "json")
                assert digest == hashlib.sha256(payload).hexdigest()
                checkpoint = access.checkpoint(_credential("viewer"), "wa", project, "export")
                await checkpoint()
                entered, release = asyncio.Event(), asyncio.Event()

                class BarrierRenderer:
                    async def render(self, report_format, data):
                        output = await renderer.render(report_format, data)
                        entered.set()
                        await release.wait()
                        return output

                waiting = CatalogAccess(catalog, _cursors(), BarrierRenderer())
                task = asyncio.create_task(waiting.export(_credential("viewer"), "wa", result, "markdown"))
                await asyncio.wait_for(entered.wait(), 20)
                await manager.set_grant(credential_digest=_credential("owner"), workspace_id="wa", project_id=project,
                    user_id="viewer", actions=[], expected_revision=2)
                release.set()
                with pytest.raises(AuthorizationDenied):
                    await task
                with pytest.raises(AuthorizationDenied):
                    await checkpoint()
                assert (await access.workspace_records(_credential("viewer"), "wa", "ctx_viewer", kind="result"))["items"] == []
            finally:
                await renderer.aclose()
    _run(scenario())
