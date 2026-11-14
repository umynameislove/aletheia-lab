"""Preview leases on real PostgreSQL; each check owns its random k07 schema."""
from __future__ import annotations

import io
import os
import re
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import SecretStr
from test_k04_workspaces_postgres import _identity, _run, _scoped

from aletheia_lab.hosted.postgres_jobs import PostgresJobs
from aletheia_lab.hosted.postgres_sources import PostgresSources, SourceNotFound
from aletheia_lab.hosted.preview_leases import PreviewChanged, PreviewExpired, PreviewLeases
from aletheia_lab.hosted.preview_migration import PreviewMigrationRejected, migrate, verify
from aletheia_lab.hosted.upload_project import UploadProject
from aletheia_lab.hosted.workspace_management import WorkspaceManagement

pytestmark = [pytest.mark.integration, pytest.mark.skipif(not os.environ.get("K07_TEST_DATABASE_URL"),
    reason="Explicit K-07 PostgreSQL test URL required")]


@asynccontextmanager
async def _database():
    import psycopg
    from psycopg import sql
    from psycopg_pool import AsyncConnectionPool

    from aletheia_lab.hosted.persistence_migration import migrate as base_migrate
    from aletheia_lab.hosted.workspace_migration import migrate as workspace_migrate

    schema = "k07_preview_" + uuid4().hex
    url = os.environ["K07_TEST_DATABASE_URL"]
    async with await psycopg.AsyncConnection.connect(url, autocommit=True) as admin:
        await admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            async with AsyncConnectionPool(_scoped(url, schema), min_size=1, max_size=4, open=False) as pool:
                await pool.wait(timeout=10)
                async with pool.connection() as conn:
                    await base_migrate(conn, schema)
                    await workspace_migrate(conn, schema)
                    assert await migrate(conn, schema) == "adopted"
                yield pool, schema
        finally:
            assert re.fullmatch(r"k07_preview_[0-9a-f]{32}", schema)
            await admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def _bundle():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("dataset.csv", "id,target\n1,0\n2,1\n")
        archive.writestr("metrics.csv", "run,name,value\nbaseline,loss,0.5\ncandidate,loss,0.6\n")
        archive.writestr("config.json", '{"seed":42}\n')
    return buffer.getvalue()


async def _seed(pool, tmp_path):
    owner, other = await _identity(pool, "owner"), await _identity(pool, "other")
    manager = WorkspaceManagement(pool)
    workspace = (await manager.create(credential_digest=owner, name="Synthetic K07"))["workspace_id"]
    outsider_workspace = (await manager.create(credential_digest=other, name="Other K07"))["workspace_id"]
    sources = PostgresSources(pool, maximum_upload_bytes=10000, maximum_expanded_bytes=20000, maximum_entries=10)
    payload = _bundle()
    source = await sources.put(credential_digest=owner, workspace_id=workspace,
                               request_key=SecretStr("synthetic-source-k07"), payload=payload)
    project = UploadProject(tmp_path, maximum_upload_bytes=10000, maximum_expanded_bytes=20000, maximum_entries=10)
    preview = project.preview(payload, captured_at="2026-11-10T00:00:00Z")
    return owner, other, workspace, outsider_workspace, sources, source, project, preview, payload


async def _expire(pool, preview_id):
    async with pool.connection() as conn:
        await conn.execute("WITH expiry AS (SELECT clock_timestamp()-interval '10 seconds' AS at) "
            "UPDATE k07_import_previews SET expires_at=expiry.at,"
            "payload=jsonb_set(payload,'{expires_at}',to_jsonb(expiry.at)) FROM expiry "
            "WHERE preview_id=%s", (preview_id,))


def test_real_preview_scope_source_expiry_and_current_revoke(tmp_path):
    async def run():
        async with _database() as (pool, _):
            owner, other, workspace, other_workspace, sources, source, _, raw, _ = await _seed(pool, tmp_path)
            leases = PreviewLeases(sources)
            preview = await leases.create(credential_digest=owner, workspace_id=workspace,
                                           source_id=source.source_id, preview_payload=raw)
            args = dict(credential_digest=owner, workspace_id=workspace, preview_id=preview.preview_id)
            assert await leases.is_ready()
            assert await leases.read(**args) == preview
            assert await leases.validate(**args, source_id=source.source_id) == preview
            for changed in ({"credential_digest": other}, {"workspace_id": other_workspace},
                            {"preview_id": "prev_" + "0" * 32}):
                with pytest.raises(SourceNotFound):
                    await leases.read(**{**args, **changed})
            with pytest.raises(SourceNotFound):
                await leases.validate(**args, source_id="src_different")
            with pytest.raises(PreviewChanged):
                await leases.create(credential_digest=owner, workspace_id=workspace, source_id=source.source_id,
                                    preview_payload={**raw, "source_sha256": "a" * 64})
            async with pool.connection() as conn:
                await conn.execute("UPDATE j01_sources SET sha256=%s WHERE source_id=%s", ("b" * 64, source.source_id))
            with pytest.raises(PreviewChanged):
                await leases.validate(**args, source_id=source.source_id)
            assert (await leases.for_submission(**args)).preview_id == preview.preview_id
            async with pool.connection() as conn:
                await conn.execute("UPDATE j01_sources SET sha256=%s WHERE source_id=%s", (source.sha256, source.source_id))
                await conn.execute("INSERT INTO j01_memberships VALUES(%s,'other','analyst',true,1)", (workspace,))
                await conn.execute("INSERT INTO j01_source_grants(workspace_id,source_id,user_id,actions,revision) "
                    "VALUES(%s,%s,'other',ARRAY['read','write'],1)", (workspace, source.source_id))
            shared = await leases.create(credential_digest=other, workspace_id=workspace,
                                         source_id=source.source_id, preview_payload=raw)
            async with pool.connection() as conn:
                await conn.execute("DELETE FROM j01_source_grants WHERE workspace_id=%s AND user_id='other'", (workspace,))
            with pytest.raises(SourceNotFound):
                await leases.read(credential_digest=other, workspace_id=workspace, preview_id=shared.preview_id)
            with pytest.raises(SourceNotFound):
                await leases.for_submission(credential_digest=other, workspace_id=workspace, preview_id=shared.preview_id)
            await _expire(pool, preview.preview_id)
            with pytest.raises(PreviewExpired):
                await leases.read(**args)
            assert (await leases.for_submission(**args)).preview_id == preview.preview_id
            fresh = await leases.create(credential_digest=owner, workspace_id=workspace,
                                         source_id=source.source_id, preview_payload=raw)
            async with pool.connection() as conn:
                await conn.execute("UPDATE j01_sessions SET state='revoked' WHERE user_id='owner'")
            with pytest.raises(SourceNotFound):
                await leases.validate(credential_digest=owner, workspace_id=workspace,
                                      preview_id=fresh.preview_id, source_id=source.source_id)
            with pytest.raises(SourceNotFound):
                await leases.for_submission(credential_digest=owner, workspace_id=workspace, preview_id=fresh.preview_id)

    _run(run())


def test_real_expired_preview_rolls_back_snapshot_and_terminal_together(tmp_path):
    import psycopg

    async def run():
        async with _database() as (pool, _):
            owner, _, workspace, _, sources, source, project, raw, payload = await _seed(pool, tmp_path)
            leases = PreviewLeases(sources)
            preview = await leases.create(credential_digest=owner, workspace_id=workspace,
                                           source_id=source.source_id, preview_payload=raw)
            candidates = raw["mapping_candidates"]
            target, metric, config = (candidates[key][0] for key in ("targets", "metric_sources", "configs"))
            mapping = {
                "target": {"mapping_id": target["mapping_id"], "project_item_id": target["project_item_id"],
                           "target_field": "target", "identifier_field": "id"},
                "metric_sources": [{"mapping_id": metric["mapping_id"], "project_item_id": metric["project_item_id"],
                    "format": "csv", "records_path": [], "metric_name_field": "name", "metric_value_field": "value",
                    "run_id_field": "run", "step_field": None}],
                "runs": [{"run_id": "baseline", "config_item_ids": []},
                         {"run_id": "candidate", "config_item_ids": [config["project_item_id"]]}],
                "baseline_run_id": "baseline",
                "metric_definitions": [{"metric_name": "loss", "direction": "lower_is_better", "regression_threshold": 0.08}],
            }
            snapshot = project.confirm(payload, workspace_id=workspace, source_id=source.source_id,
                                       mapping=mapping, captured_at="2026-11-10T00:00:00Z")
            jobs, lease = PostgresJobs(pool, sources), SecretStr("e" * 64)
            receipt = await jobs.enqueue(credential_digest=owner, workspace_id=workspace, operation="confirm",
                request_key=SecretStr("synthetic-confirm-k07"),
                request={"source_id": source.source_id, "mapping": {**mapping, "_preview_id": preview.preview_id}})
            claim = await jobs.claim(lease_digest=lease, lease_seconds=60)
            assert claim is not None and claim.job_id == receipt.job_id
            await _expire(pool, preview.preview_id)
            with pytest.raises(PreviewExpired):
                await leases.validate(credential_digest=owner, workspace_id=workspace,
                                      preview_id=preview.preview_id, source_id=source.source_id)
            with pytest.raises(psycopg.errors.CheckViolation):
                await jobs.complete_snapshot(claim=claim, lease_digest=lease, snapshot=snapshot)
            async with pool.connection() as conn:
                assert await (await conn.execute("SELECT count(*) FROM j01_upload_snapshots")).fetchone() == (0,)
            assert (await jobs.status(credential_digest=owner, workspace_id=workspace, job_id=receipt.job_id)).status == "running"
            fresh = await leases.create(credential_digest=owner, workspace_id=workspace,
                                         source_id=source.source_id, preview_payload=raw)
            second = await jobs.enqueue(credential_digest=owner, workspace_id=workspace, operation="confirm",
                request_key=SecretStr("synthetic-confirm-fresh-k07"),
                request={"source_id": source.source_id, "mapping": {**mapping, "_preview_id": fresh.preview_id}})
            current = await jobs.claim(lease_digest=lease, lease_seconds=60)
            assert current is not None and current.job_id == second.job_id
            assert await jobs.complete_snapshot(claim=current, lease_digest=lease, snapshot=snapshot)
            assert (await jobs.output(credential_digest=owner, workspace_id=workspace,
                                     job_id=second.job_id))["result"] == {"snapshot_id": snapshot.snapshot_id}
            assert (await sources.snapshot(credential_digest=owner, workspace_id=workspace,
                                            snapshot_id=snapshot.snapshot_id)).source_sha256 == source.sha256

    _run(run())


def test_real_preview_migration_checksum_and_trigger_drift(tmp_path):
    async def run():
        async with _database() as (pool, schema):
            async with pool.connection() as conn:
                assert await migrate(conn, schema) == "unchanged"
                assert await verify(conn)
            with pytest.raises(PreviewMigrationRejected):
                async with pool.connection() as conn:
                    await migrate(conn, "public")
            async with pool.connection() as conn:
                await conn.execute("ALTER TABLE j01_jobs DISABLE TRIGGER k07_confirm_preview_guard")
            async with pool.connection() as conn:
                assert not await verify(conn)
            with pytest.raises(PreviewMigrationRejected):
                async with pool.connection() as conn:
                    await migrate(conn, schema)
            async with pool.connection() as conn:
                await conn.execute("ALTER TABLE j01_jobs ENABLE TRIGGER k07_confirm_preview_guard")
                await conn.execute("DROP INDEX k07_jobs_preview_ref")
            async with pool.connection() as conn:
                assert not await verify(conn)
            with pytest.raises(PreviewMigrationRejected):
                async with pool.connection() as conn:
                    await migrate(conn, schema)

    _run(run())


def test_preview_v1_upgrade_preserves_historical_ledger_and_core_facts():
    from aletheia_lab.hosted import preview_migration
    from aletheia_lab.hosted.persistence_migration import verify as verify_core

    async def run():
        async with _database() as (pool, schema), pool.connection() as conn:
            original = Path(preview_migration.__file__).with_name('preview_schema.sql').read_text(encoding='utf-8')
            function = 'CREATE OR REPLACE FUNCTION' + original.split('CREATE FUNCTION', 1)[1].split('CREATE TRIGGER', 1)[0]
            await conn.execute(function)
            await conn.execute('DELETE FROM k07_preview_migrations WHERE version=2')
            before = await (await conn.execute('SELECT * FROM k07_preview_migrations ORDER BY version')).fetchall()
            core_before = await (await conn.execute('SELECT * FROM k05_persistence_migrations ORDER BY version')).fetchall()
            assert not await verify(conn) and await verify(conn, allow_previous=True)
            assert await verify_core(conn, schema)
            assert await migrate(conn, schema) == 'upgraded'
            assert await verify(conn) and await verify_core(conn, schema)
            after = await (await conn.execute('SELECT * FROM k07_preview_migrations ORDER BY version')).fetchall()
            assert len(after) == 2 and after[:1] == before
            assert await (await conn.execute('SELECT * FROM k05_persistence_migrations ORDER BY version')).fetchall() == core_before
            assert await migrate(conn, schema) == 'unchanged'
    _run(run())


def test_real_preview_cleanup_is_bounded_and_retains_durable_references(tmp_path):
    async def run():
        async with _database() as (pool, _):
            owner, _, workspace, _, sources, source, _, raw, payload = await _seed(pool, tmp_path)
            leases = PreviewLeases(sources)
            previews = [await leases.create(credential_digest=owner, workspace_id=workspace,
                source_id=source.source_id, preview_payload=raw) for _ in range(4)]
            await PostgresJobs(pool, sources).enqueue(credential_digest=owner, workspace_id=workspace,
                operation="confirm", request_key=SecretStr("synthetic-retained-preview-k07"),
                request={"source_id": source.source_id, "mapping": {"_preview_id": previews[0].preview_id}})
            for preview in previews[:3]:
                await _expire(pool, preview.preview_id)
            assert await leases.cleanup_expired(grace_seconds=60, limit=1) == 0
            assert await leases.cleanup_expired(grace_seconds=1, limit=1) == 1
            assert await leases.cleanup_expired(grace_seconds=1, limit=1) == 1
            assert await leases.cleanup_expired(grace_seconds=1, limit=1) == 0
            assert (await leases.for_submission(credential_digest=owner, workspace_id=workspace,
                preview_id=previews[0].preview_id)).preview_id == previews[0].preview_id
            assert await leases.read(credential_digest=owner, workspace_id=workspace,
                                     preview_id=previews[3].preview_id) == previews[3]
            assert await sources.read(credential_digest=owner, workspace_id=workspace, source_id=source.source_id) == payload
            with pytest.raises(ValueError):
                await leases.cleanup_expired(grace_seconds=0)
            with pytest.raises(ValueError):
                await leases.cleanup_expired(limit=1001)

    _run(run())


def test_real_confirm_retry_receipt_survives_expiry_and_new_intents_are_rejected(tmp_path):
    from aletheia_lab.hosted.imports_http import LeasedJobs
    from aletheia_lab.hosted.request_boundary import RequestRejected

    async def run():
        async with _database() as (pool, _):
            owner, _, workspace, _, sources, source, _, raw, _ = await _seed(pool, tmp_path)
            leases = PreviewLeases(sources)
            preview = await leases.create(credential_digest=owner, workspace_id=workspace,
                                           source_id=source.source_id, preview_payload=raw)
            raw_jobs = PostgresJobs(pool, sources)
            jobs = LeasedJobs(raw_jobs, leases, None)
            request = {"source_id": source.source_id, "mapping": {"_preview_id": preview.preview_id}}
            first = await jobs.enqueue(credential_digest=owner, workspace_id=workspace, operation="confirm",
                request_key=SecretStr("synthetic-original-retry-k07"), request=request)
            lease = SecretStr("e" * 64)
            claim = await raw_jobs.claim(lease_digest=lease, lease_seconds=60)
            assert claim is not None and claim.job_id == first.job_id
            assert await raw_jobs.fail(claim=claim, lease_digest=lease)
            args = dict(credential_digest=owner, workspace_id=workspace, job_id=first.job_id)
            retry_key = SecretStr("synthetic-accepted-retry-k07")
            second = await jobs.retry(**args, request_key=retry_key)
            await _expire(pool, preview.preview_id)
            with pytest.raises(PreviewExpired):
                await leases.validate(credential_digest=owner, workspace_id=workspace,
                                      preview_id=preview.preview_id, source_id=source.source_id)
            assert await jobs.retry(**args, request_key=retry_key) == second
            assert await jobs.enqueue(credential_digest=owner, workspace_id=workspace, operation="confirm",
                request_key=SecretStr("synthetic-original-retry-k07"), request=request) == first
            with pytest.raises(RequestRejected) as caught:
                await jobs.retry(**args, request_key=SecretStr("synthetic-new-retry-k07"))
            assert caught.value.status == 410
            with pytest.raises(RequestRejected) as caught:
                await jobs.enqueue(credential_digest=owner, workspace_id=workspace, operation="confirm",
                    request_key=SecretStr("synthetic-new-submit-k07"), request=request)
            assert caught.value.status == 410
            async with pool.connection() as conn:
                assert await (await conn.execute("SELECT count(*) FROM j01_jobs")).fetchone() == (2,)

    _run(run())


def test_real_mapping_diagnostics_require_live_lease_and_current_reader(tmp_path):
    from aletheia_lab.hosted.import_errors import MappingInvalid
    from aletheia_lab.hosted.import_failures import ImportFailures
    from aletheia_lab.hosted.imports_http import LeasedJobs

    async def run():
        async with _database() as (pool, _):
            owner, _, workspace, _, sources, source, _, raw, _ = await _seed(pool, tmp_path)
            leases, failures = PreviewLeases(sources), ImportFailures(pool)
            preview = await leases.create(credential_digest=owner, workspace_id=workspace,
                                           source_id=source.source_id, preview_payload=raw)
            raw_jobs = PostgresJobs(pool, sources)
            receipt = await raw_jobs.enqueue(credential_digest=owner, workspace_id=workspace, operation="confirm",
                request_key=SecretStr("synthetic-diagnostics-k07"),
                request={"source_id": source.source_id, "mapping": {"_preview_id": preview.preview_id}})
            lease = SecretStr("e" * 64)
            claim = await raw_jobs.claim(lease_digest=lease, lease_seconds=60)
            assert claim is not None
            failure = MappingInvalid({"schema_version": "hosted-upload-error/v1", "code": "mapping_invalid",
                "issues": [{"code": "metric_missing", "field": "metric_definitions[0].metric_name"}]})
            await failures.record(claim=claim, lease_digest=SecretStr("f" * 64), failure=failure)
            assert await failures.read(workspace_id=workspace, job_id=receipt.job_id) is None
            await failures.record(claim=claim, lease_digest=lease, failure=failure)
            assert await raw_jobs.fail(claim=claim, lease_digest=lease)
            jobs = LeasedJobs(raw_jobs, leases, failures)
            output = await jobs.output(credential_digest=owner, workspace_id=workspace, job_id=receipt.job_id)
            assert output["result"] == failure.failure.model_dump(mode="json", exclude_none=True)
            async with pool.connection() as conn:
                await conn.execute("UPDATE j01_sessions SET state='revoked' WHERE user_id='owner'")
            with pytest.raises(SourceNotFound):
                await jobs.output(credential_digest=owner, workspace_id=workspace, job_id=receipt.job_id)

    _run(run())
