"""The same review CAS/history/audit works for typed native uploaded results."""
from __future__ import annotations

import os

import pytest
from test_k05_migrations_postgres import _run
from test_k05_native_results_postgres import _case, _initial

from aletheia_lab.hosted.action_audit_migration import migrate as migrate_audit
from aletheia_lab.hosted.authorization import AuthorizationDenied
from aletheia_lab.hosted.review_migration import migrate as migrate_reviews
from aletheia_lab.hosted.review_repository import PostgresReviews
from aletheia_lab.hosted.workspace_management import WorkspaceConflict

pytestmark = [pytest.mark.integration, pytest.mark.skipif(
    not os.environ.get("K05_TEST_DATABASE_URL"), reason="Explicit PostgreSQL test database required")]


def test_native_result_review_keeps_cas_history_exact_audit_and_rejects_snapshot_parent(tmp_path):
    import psycopg

    async def scenario():
        async with _case(tmp_path) as case:
            snapshot, initial = await _initial(case)
            async with case.pool.connection() as conn:
                schema = (await (await conn.execute("SELECT current_schema()")).fetchone())[0]
                await migrate_reviews(conn, schema)
                await migrate_audit(conn, schema)
            reviews = PostgresReviews(case.pool, case.catalog, audit=True)
            args = dict(credential_digest=case.a, workspace_id="a", result_id=initial.record_id, annotation_id="human-review")
            first = await reviews.put(**args, body="Observed native evidence.", status="comment", expected_revision=0)
            assert first["revision"] == 1 and first["result_id"] == initial.record_id
            with pytest.raises(WorkspaceConflict):
                await reviews.put(**args, body="Stale draft retained by caller.", status="approved", expected_revision=0)
            second = await reviews.put(**args, body="Human review completed.", status="approved", expected_revision=1)
            assert second["revision"] == 2
            history = await reviews.list_history(**args)
            assert [item["revision"] for item in history["items"]] == [1, 2]
            with pytest.raises(AuthorizationDenied):
                await reviews.put(**{**args, "result_id": snapshot.snapshot_id}, body="Forbidden snapshot review",
                                  status="comment", expected_revision=0)
            with pytest.raises(AuthorizationDenied):
                await reviews.put(**{**args, "credential_digest": case.b}, body="Other tenant",
                                  status="comment", expected_revision=2)
            async with case.pool.connection() as conn:
                cursor = await conn.execute("SELECT parent_id,effect_id,revision FROM k06_action_audit "
                    "WHERE action='review.put' ORDER BY revision")
                assert await cursor.fetchone() == (initial.record_id, "human-review", 1)
                assert await cursor.fetchone() == (initial.record_id, "human-review", 2)
                assert await cursor.fetchone() is None
            with pytest.raises(psycopg.errors.CheckViolation):
                async with case.pool.connection() as conn:
                    await conn.execute("INSERT INTO j01_annotations(workspace_id,project_id,result_id,annotation_id,"
                        "revision,author_id,body,status) VALUES('a',%s,%s,'wrong-parent',1,%s,'No','comment')",
                        (initial.project_id, snapshot.snapshot_id, first["author_id"]))
            assert await case.catalog.read_record(credential_digest=case.a, workspace_id="a",
                project_id=initial.project_id, record_id=initial.record_id) == case.initial
    _run(scenario())
