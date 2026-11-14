"""Actual canonical result reviews: role, ownership, CAS, history and revoke."""
from __future__ import annotations

import asyncio
import os

import pytest
from test_k04_project_policy_postgres import _credential, _database, _run
from test_k05_legacy_import_postgres import _source

from aletheia_lab.hosted.authorization import AuthorizationDenied
from aletheia_lab.hosted.legacy_import import ImportTarget, LegacyImporter
from aletheia_lab.hosted.object_store import FilesystemObjectStore
from aletheia_lab.hosted.persistence_migration import verify as verify_canonical
from aletheia_lab.hosted.postgres_catalog import PostgresCatalog
from aletheia_lab.hosted.postgres_objects import PostgresObjects
from aletheia_lab.hosted.project_access import PostgresProjectAccess
from aletheia_lab.hosted.review_migration import migrate, verify
from aletheia_lab.hosted.review_repository import PostgresReviews
from aletheia_lab.hosted.workspace_management import WorkspaceConflict
from aletheia_lab.project.snapshots import ProjectSnapshot

pytestmark = [pytest.mark.integration, pytest.mark.skipif(not os.environ.get("K04_TEST_DATABASE_URL"),
    reason="Explicit K-04 PostgreSQL test URL required")]


async def _setup(pool, schema, tmp_path):
    source, original, _ = _source(tmp_path)
    objects = PostgresObjects(pool, FilesystemObjectStore(tmp_path / "review-objects"))
    await LegacyImporter(pool, objects).import_project(source, ImportTarget(source.project_id, "wa", "analyst"),
        credential_digest=_credential("analyst"), batch_id="synthetic-review")
    async with pool.connection() as conn:
        assert await migrate(conn, schema) == "created"
        assert await verify(conn, schema) and await verify_canonical(conn, schema)
    manager = PostgresProjectAccess(pool)
    for user, actions in (("reviewer", ["read", "review"]), ("viewer", ["read"])):
        await manager.set_grant(credential_digest=_credential("owner"), workspace_id="wa", project_id=source.project_id,
                               user_id=user, actions=actions, expected_revision=0)
    catalog = PostgresCatalog(pool, objects)
    return PostgresReviews(pool, catalog), manager, catalog, source.project_id, original["result"]["id"], source


def test_reviewer_actual_result_history_author_only_edit_and_viewer_read_policy(tmp_path):
    async def scenario():
        async with _database() as (pool, schema):
            reviews, _, _, _, result, _ = await _setup(pool, schema, tmp_path)
            args = dict(workspace_id="wa", result_id=result, annotation_id="human-review")
            first = await reviews.put(credential_digest=_credential("reviewer"), **args,
                body="Observed evidence needs human follow-up.", status="comment", expected_revision=0)
            assert first["revision"] == 1 and first["author_id"] == "reviewer"
            second = await reviews.put(credential_digest=_credential("reviewer"), **args,
                body="Review approved; causal status is unchanged.", status="approved", expected_revision=1)
            assert second["revision"] == 2
            with pytest.raises(WorkspaceConflict):
                await reviews.put(credential_digest=_credential("reviewer"), **args,
                    body="Stale revision", status="rejected", expected_revision=1)
            with pytest.raises(AuthorizationDenied):
                await reviews.put(credential_digest=_credential("owner"), **args,
                    body="Cannot overwrite another author", status="approved", expected_revision=2)
            for actor in ("analyst", "admin", "owner"):
                value = await reviews.put(credential_digest=_credential(actor), workspace_id="wa", result_id=result,
                    annotation_id=actor + "-review", body="Scoped reviewer comment", status="comment", expected_revision=0)
                assert value["author_id"] == actor
            with pytest.raises(AuthorizationDenied):
                await reviews.put(credential_digest=_credential("viewer"), workspace_id="wa", result_id=result,
                    annotation_id="viewer-forbidden", body="Forbidden", status="comment", expected_revision=0)
            with pytest.raises(AuthorizationDenied):
                await reviews.list_history(credential_digest=_credential("other"), **args)
            first_page = await reviews.list_history(credential_digest=_credential("viewer"), **args, limit=1)
            assert [item["revision"] for item in first_page["items"]] == [1] and first_page["next_after_revision"] == 1
            next_page = await reviews.list_history(credential_digest=_credential("viewer"), **args,
                                                  after_revision=first_page["next_after_revision"], limit=1)
            assert [item["revision"] for item in next_page["items"]] == [2] and next_page["next_after_revision"] is None
            assert next_page["items"][0]["status"] == "approved"
    _run(scenario())


def test_two_connections_append_one_cas_winner_and_database_keeps_immutable_result_scope(tmp_path):
    import psycopg

    async def scenario():
        async with _database() as (pool, schema):
            reviews, _, _, project, result, source = await _setup(pool, schema, tmp_path)
            start = asyncio.Event()

            async def append(body):
                await start.wait()
                try:
                    return await reviews.put(credential_digest=_credential("reviewer"), workspace_id="wa", result_id=result,
                        annotation_id="race-review", body=body, status="comment", expected_revision=0)
                except WorkspaceConflict:
                    return None

            first, second = asyncio.create_task(append("First")), asyncio.create_task(append("Second"))
            start.set()
            outcomes = await asyncio.gather(first, second)
            assert sum(value is not None for value in outcomes) == 1
            for statement in ("UPDATE j01_annotations SET body='tampered'", "DELETE FROM j01_annotations"):
                with pytest.raises(psycopg.errors.CheckViolation):
                    async with pool.connection() as conn:
                        await conn.execute(statement)
            snapshot = next(model.snapshot_id for model in source.models if isinstance(model, ProjectSnapshot))
            with pytest.raises(psycopg.errors.CheckViolation):
                async with pool.connection() as conn:
                    await conn.execute("INSERT INTO j01_annotations(workspace_id,project_id,result_id,annotation_id,revision,"
                        "author_id,body,status) VALUES('wa',%s,%s,'wrong-parent',1,'reviewer','No','comment')", (project, snapshot))
            async with pool.connection() as conn:
                assert await (await conn.execute("SELECT count(*) FROM j01_annotations")).fetchone() == (1,)
                assert await migrate(conn, schema) == "unchanged" and await verify(conn, schema)
    _run(scenario())


def test_revoke_committed_after_canonical_read_prevents_review_insert(tmp_path):
    async def scenario():
        async with _database() as (pool, schema):
            _, manager, catalog, project, result, _ = await _setup(pool, schema, tmp_path)
            entered, release = asyncio.Event(), asyncio.Event()

            class BarrierCatalog:
                async def read_record(self, **kwargs):
                    value = await catalog.read_record(**kwargs)
                    entered.set()
                    await release.wait()
                    return value

            reviews = PostgresReviews(pool, BarrierCatalog())
            task = asyncio.create_task(reviews.put(credential_digest=_credential("reviewer"), workspace_id="wa", result_id=result,
                annotation_id="revoke-review", body="Must not publish", status="comment", expected_revision=0))
            await asyncio.wait_for(entered.wait(), 10)
            await manager.set_grant(credential_digest=_credential("owner"), workspace_id="wa", project_id=project,
                                   user_id="reviewer", actions=[], expected_revision=1)
            release.set()
            with pytest.raises(AuthorizationDenied):
                await task
            async with pool.connection() as conn:
                assert await (await conn.execute("SELECT count(*) FROM j01_annotations")).fetchone() == (0,)
    _run(scenario())
