"""Real owner purge, shared object leases and deletion-history restore fences."""

from __future__ import annotations

import hashlib
import os
import secrets
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import SecretStr
from test_k05_legacy_import_postgres import _source
from test_k05_native_results_postgres import _case, _initial, _run
from test_k06_runtime_privileges_postgres import _role_url

from aletheia_lab.hosted.authorization import AuthorizationDenied
from aletheia_lab.hosted.deletion_checkpoint import DeletionCheckpointPage, DeletionFence
from aletheia_lab.hosted.durable_exports import DurableExports
from aletheia_lab.hosted.legacy_import import ImportTarget, LegacyImporter
from aletheia_lab.hosted.postgres_catalog import PostgresCatalog
from aletheia_lab.hosted.postgres_jobs import PostgresJobs
from aletheia_lab.hosted.postgres_objects import PostgresObjects
from aletheia_lab.hosted.project_mutations import PostgresProjectMutations
from aletheia_lab.hosted.recovery_points import BACKUP_ADVISORY_KEY
from aletheia_lab.hosted.request_boundary import RequestRejected
from aletheia_lab.hosted.retention import PostgresRetention, RetentionRejected
from aletheia_lab.hosted.review_migration import migrate as migrate_reviews
from aletheia_lab.hosted.review_migration import verify as verify_reviews
from aletheia_lab.hosted.review_repository import PostgresReviews
from aletheia_lab.hosted.runtime_privileges import grant_runtime
from aletheia_lab.product.lifecycle import build_product_lifecycle_record
from aletheia_lab.product.reports import render_product_report
from aletheia_lab.project.persistence import _json_bytes

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("K13_TEST_DATABASE_URL"),
        reason="Explicit private K13 PostgreSQL required",
    ),
]


@pytest.fixture(autouse=True)
def configured_database(monkeypatch):
    monkeypatch.setenv("K05_TEST_DATABASE_URL", os.environ["K13_TEST_DATABASE_URL"])


async def _review_ready(case):
    async with case.pool.connection() as conn:
        schema = (await (await conn.execute("SELECT current_schema()")).fetchone())[0]
        await migrate_reviews(conn, schema)
        assert await verify_reviews(conn, schema)
    return schema


async def _revision(case, project):
    async with case.pool.connection() as conn:
        return (
            await (
                await conn.execute(
                    "SELECT revision FROM k05_projects WHERE workspace_id=%s AND project_id=%s",
                    ("a", project),
                )
            ).fetchone()
        )[0]


async def _delete(case, project):
    return await PostgresProjectMutations(case.pool, case.catalog).delete(
        credential_digest=case.a,
        workspace_id="a",
        project_id=project,
        expected_revision=await _revision(case, project),
    )


def test_delete_receipt_is_durable_retained_and_eligible_time_never_claims_purge(tmp_path):
    async def scenario():
        async with _case(tmp_path) as case:
            _, initial = await _initial(case)
            retention = PostgresRetention(case.pool, case.catalog)
            assert await retention.is_ready()
            receipt = await _delete(case, initial.project_id)
            assert receipt["status"] == "tombstoned" and receipt["retention"] == "retained"
            status = await retention.status(
                credential_digest=case.a, workspace_id="a", project_id=initial.project_id
            )
            assert status.status == "retained" and status.project_retention_days == 30
            assert (
                status.eligible_at - status.requested_at == timedelta(days=30)
                and status.purged_at is None
            )
            outcome = await retention.purge(
                workspace_id="a", project_id=initial.project_id, limit=1
            )
            assert outcome.action == "retention_wait" and outcome.changed == 0
            # An operator policy update affects future deletions only.
            await retention.configure(project_days=0, terminal_job_days=0, object_grace_seconds=60)
            assert (
                await retention.status(
                    credential_digest=case.a, workspace_id="a", project_id=initial.project_id
                )
            ).eligible_at == status.eligible_at
            with pytest.raises(AuthorizationDenied):
                await retention.status(
                    credential_digest=case.a, workspace_id="b", project_id=initial.project_id
                )
            assert (
                await case.sources.read(
                    credential_digest=case.a, workspace_id="a", source_id=case.receipt.source_id
                )
                == case.payload
            )

    _run(scenario())


def test_bounded_purge_removes_private_annotations_but_respects_shared_objects_reader_lease_and_backup(
    tmp_path,
):
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    source, original, _ = _source(legacy)
    before = {
        p.relative_to(legacy).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in legacy.rglob("*")
        if p.is_file()
    }

    async def scenario():
        async with _case(tmp_path) as case:
            await _review_ready(case)
            importer = LegacyImporter(case.pool, case.objects)
            for workspace, credential, creator in [
                ("a", case.a, "owner_a"),
                ("b", case.b, "owner_b"),
            ]:
                await importer.import_project(
                    source,
                    ImportTarget(source.project_id, workspace, creator),
                    credential_digest=credential,
                    batch_id="k13-shared",
                )
            retention = PostgresRetention(case.pool, case.catalog)
            await retention.configure(project_days=0, terminal_job_days=0, object_grace_seconds=60)
            async with case.pool.connection() as conn:
                await conn.execute(
                    "UPDATE k05_projects SET display_name=%s WHERE project_id=%s",
                    ("Synthetic private title", source.project_id),
                )
            result = original["result"]["id"]
            reviews = PostgresReviews(case.pool, case.catalog)
            await reviews.put(
                credential_digest=case.a,
                workspace_id="a",
                result_id=result,
                annotation_id="private-note",
                body="Private comment must be physically removed after retention.",
                status="comment",
                expected_revision=0,
            )
            unique = await case.objects.stage(
                "a", b"synthetic private export attachment", media_type="text/plain"
            )
            async with case.pool.connection() as conn:
                await case.objects.ready(conn, "a", unique)
                await conn.execute(
                    "INSERT INTO k05_object_references VALUES(%s,%s,%s)",
                    ("a", result, unique.descriptor.sha256),
                )
                await case.objects.finish(conn, "a", unique)
            held = await case.objects._lease("a", unique.descriptor, 120, require_ready=True)
            for f in case.objects.store.object_root.glob("*/*"):
                if f.is_file():
                    os.utime(f, (time.time() - 120, time.time() - 120))
            await _delete(case, source.project_id)
            due = await retention.due(limit=1)
            assert len(due) == 1 and due[0][1:] == ("a", source.project_id)
            assert await retention.due(limit=1, after=due[-1]) == ()
            actions = []
            for _ in range(300):
                batch = await retention.purge(
                    workspace_id="a", project_id=source.project_id, limit=1
                )
                actions.append(batch.action)
                assert batch.changed <= 1
                if batch.action == "objects_or_lease_wait":
                    break
            else:
                pytest.fail("Bounded metadata cleanup did not reach the held object.")
            assert case.objects.store.path(unique.descriptor.sha256).is_file()
            async with case.pool.connection() as conn:
                assert await (
                    await conn.execute(
                        "SELECT count(*) FROM j01_annotations WHERE workspace_id='a'"
                    )
                ).fetchone() == (0,)
                assert await (
                    await conn.execute(
                        "SELECT count(*) FROM k05_records WHERE workspace_id='a' AND project_id=%s AND record_type<>'deletion'",
                        (source.project_id,),
                    )
                ).fetchone() == (0,)
                await case.objects.finish(conn, "a", held)
            async with case.pool.connection() as backup:
                await backup.execute("SELECT pg_advisory_lock(%s)", (BACKUP_ADVISORY_KEY,))
                try:
                    blocked = await retention.purge(
                        workspace_id="a", project_id=source.project_id, limit=1
                    )
                    assert (
                        blocked.action == "backup_wait"
                        and case.objects.store.path(unique.descriptor.sha256).is_file()
                    )
                finally:
                    await backup.execute("SELECT pg_advisory_unlock(%s)", (BACKUP_ADVISORY_KEY,))
            for _ in range(100):
                batch = await retention.purge(
                    workspace_id="a", project_id=source.project_id, limit=1
                )
                if batch.status == "purged":
                    break
            else:
                pytest.fail("Physical object cleanup did not finish.")
            assert not case.objects.store.path(unique.descriptor.sha256).exists()
            assert (
                await case.catalog.read_record(
                    credential_digest=case.b,
                    workspace_id="b",
                    project_id=source.project_id,
                    record_id=result,
                )
            ).record_id == result
            status = await retention.status(
                credential_digest=case.a, workspace_id="a", project_id=source.project_id
            )
            assert (
                status.status == "purged"
                and status.pending_objects == 0
                and status.shared_objects_retained > 0
            )
            async with case.pool.connection() as conn:
                assert await (
                    await conn.execute(
                        "SELECT workspace_id,display_name FROM k05_projects WHERE project_id=%s ORDER BY workspace_id",
                        (source.project_id,),
                    )
                ).fetchall() == [("a", None), ("b", "Synthetic private title")]
            assert (
                await case.sources.read(
                    credential_digest=case.a, workspace_id="a", source_id=case.receipt.source_id
                )
                == case.payload
            )
            assert "annotations" in actions and "artifact_references" in actions

    _run(scenario())
    assert {
        p.relative_to(legacy).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in legacy.rglob("*")
        if p.is_file()
    } == before


def test_expired_export_receipt_cannot_renew_idempotency_while_terminal_private_job_is_removed(
    tmp_path,
):
    async def scenario():
        async with _case(tmp_path) as case:
            _, initial = await _initial(case)
            retention = PostgresRetention(case.pool, case.catalog)
            await retention.configure(terminal_job_days=0)
            exports = DurableExports(case.catalog)
            current = await exports.publish(
                case.a,
                "a",
                initial.project_id,
                initial.record_id,
                "json",
                render_product_report(case.view, "json"),
                "e" * 64,
            )
            expired_id = "ex_json_" + initial.record_id + "_" + uuid4().hex
            async with case.pool.connection() as conn:
                await conn.execute(
                    "INSERT INTO k02_exports(workspace_id,export_id,project_id,creator_id,result_id,format,object_sha256,request_key_hash,created_at,expires_at) "
                    "VALUES('a',%s,%s,'owner_a',%s,'json',%s,%s,clock_timestamp()-interval '1 hour',clock_timestamp()-interval '30 minutes')",
                    (
                        expired_id,
                        initial.project_id,
                        initial.record_id,
                        current.descriptor.sha256,
                        "f" * 64,
                    ),
                )
            jobs = PostgresJobs(case.pool, case.sources, catalog=case.catalog, execution=True)
            receipt = await jobs.enqueue(
                credential_digest=case.a,
                workspace_id="a",
                operation="preview",
                request_key=SecretStr("k13-private-question"),
                request={"source_id": case.receipt.source_id},
            )
            await jobs.cancel(credential_digest=case.a, workspace_id="a", job_id=receipt.job_id)
            outcome = await retention.expire(limit=1)
            assert (
                outcome["terminal_jobs"] == 1 and outcome["expired_export_receipts_retained"] == 1
            )
            async with case.pool.connection() as conn:
                assert (
                    await (
                        await conn.execute(
                            "SELECT 1 FROM j01_jobs WHERE job_id=%s", (receipt.job_id,)
                        )
                    ).fetchone()
                    is None
                )
                assert await (
                    await conn.execute(
                        "SELECT 1 FROM k02_exports WHERE export_id=%s", (expired_id,)
                    )
                ).fetchone() == (1,)
            with pytest.raises(RequestRejected) as error:
                await exports.publish(
                    case.a, "a", initial.project_id, initial.record_id, "json", b"{}", "f" * 64
                )
            assert error.value.status == 410
            assert (
                await exports.find(
                    case.a, "a", initial.project_id, initial.record_id, "json", "e" * 64
                )
            ).expires_at == current.expires_at

    _run(scenario())


def test_authenticated_checkpoint_replay_hides_older_data_and_blocks_a_project_absent_in_backup(
    tmp_path,
):
    import psycopg

    (tmp_path / "legacy").mkdir()
    (tmp_path / "live").mkdir()
    (tmp_path / "restore").mkdir()
    source, original, _ = _source(tmp_path / "legacy")

    async def scenario():
        async with _case(tmp_path / "live") as live, _case(tmp_path / "restore") as restored:
            for case in (live, restored):
                await LegacyImporter(case.pool, case.objects).import_project(
                    source,
                    ImportTarget(source.project_id, "a", "owner_a"),
                    credential_digest=case.a,
                    batch_id="k13-restore",
                )
            await _delete(live, source.project_id)
            retention = PostgresRetention(live.pool, live.catalog)
            async with live.pool.connection() as conn:
                await conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
                first = await retention.export_checkpoint_page(connection=conn, limit=1)
            assert len(first.items) == 1 and first.next_cursor is None
            target = PostgresRetention(restored.pool, restored.catalog)
            assert await target.apply_checkpoint_page(first) == 1
            assert await target.apply_checkpoint_page(first) == 1
            with pytest.raises(AuthorizationDenied):
                await restored.catalog.read_record(
                    credential_digest=restored.a,
                    workspace_id="a",
                    project_id=source.project_id,
                    record_id=original["result"]["id"],
                )
            missing_project = "p3-project-" + "c" * 64
            assert missing_project != source.project_id
            marker = build_product_lifecycle_record(
                record_kind="deletion",
                project_id=missing_project,
                payload={
                    "schema_version": "p6-project-deletion/v1",
                    "status": "pending",
                    "deleted_count": 0,
                    "retained_shared_count": 0,
                    "purge_object_sha256s": [],
                },
            )
            now = datetime.now(UTC)
            missing = DeletionFence(
                workspace_id="a",
                project_id=missing_project,
                tombstone_id=marker.record_id,
                revision=1,
                deleted_by="owner_a",
                requested_at=now,
                eligible_at=now + timedelta(days=30),
                project_days=30,
                time_basis="delete_commit",
                record_json=_json_bytes(marker.model_dump(mode="json")).decode(),
            )
            assert (
                await target.apply_checkpoint_page(
                    DeletionCheckpointPage(after=None, items=(missing,), next_cursor=None)
                )
                == 1
            )
            with pytest.raises(psycopg.errors.CheckViolation):
                async with restored.pool.connection() as conn:
                    await conn.execute(
                        "INSERT INTO k05_projects(workspace_id,project_id,creator_id) VALUES('a',%s,'owner_a')",
                        (missing_project,),
                    )

    _run(scenario())


def test_unprivileged_api_temp_shadow_and_counterfeit_trigger_cannot_redirect_deletion_history(
    tmp_path,
):
    import psycopg
    from psycopg import sql

    role = "k13_api_" + uuid4().hex
    password = secrets.token_urlsafe(32)

    async def scenario():
        try:
            async with _case(tmp_path) as case:
                _, initial = await _initial(case)
                schema = await _review_ready(case)
                async with case.pool.connection() as conn:
                    await conn.execute(
                        sql.SQL(
                            "CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS PASSWORD {}"
                        ).format(sql.Identifier(role), sql.Literal(password))
                    )
                    await grant_runtime(conn, schema, role, profile="api")
                async with await psycopg.AsyncConnection.connect(
                    _role_url(os.environ["K13_TEST_DATABASE_URL"], schema, role, password)
                ) as api:
                    await api.execute(
                        "CREATE TEMP TABLE k13_deletions(workspace_id text,project_id text)"
                    )
                    await api.execute("CREATE TEMP TABLE counterfeit(value integer)")
                    await api.commit()
                    for function in ("k13_capture_deletion", "k13_no_resurrection"):
                        with pytest.raises(psycopg.errors.InsufficientPrivilege):
                            async with api.transaction():
                                await api.execute(
                                    sql.SQL(
                                        "CREATE TRIGGER counterfeit BEFORE INSERT ON counterfeit FOR EACH ROW EXECUTE FUNCTION {}.{}()"
                                    ).format(sql.Identifier(schema), sql.Identifier(function))
                                )

                    class PinnedPool:
                        @asynccontextmanager
                        async def connection(self):
                            async with api.transaction():
                                yield api

                    pool = PinnedPool()
                    catalog = PostgresCatalog(pool, PostgresObjects(pool, case.objects.store))
                    with pytest.raises(RetentionRejected):
                        await PostgresRetention(pool, catalog).configure(project_days=0)
                    receipt = await PostgresProjectMutations(pool, catalog).delete(
                        credential_digest=case.a,
                        workspace_id="a",
                        project_id=initial.project_id,
                        expected_revision=2,
                    )
                    assert receipt["status"] == "tombstoned"
                    assert await (
                        await api.execute("SELECT count(*) FROM pg_temp.k13_deletions")
                    ).fetchone() == (0,)
                    assert await (
                        await api.execute(
                            sql.SQL("SELECT count(*) FROM {}.k13_deletions").format(
                                sql.Identifier(schema)
                            )
                        )
                    ).fetchone() == (1,)
        finally:
            async with await psycopg.AsyncConnection.connect(
                os.environ["K13_TEST_DATABASE_URL"], autocommit=True
            ) as admin:
                await admin.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role)))

    _run(scenario())
