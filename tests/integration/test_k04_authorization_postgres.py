"""Opt-in current authorization races in an owned disposable PostgreSQL schema."""
from __future__ import annotations

import asyncio
import hashlib
import io
import os
import sys
import time
import uuid
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = [pytest.mark.integration, pytest.mark.skipif(
    not os.environ.get("K04_TEST_DATABASE_URL"), reason="Explicit PostgreSQL test database required")]


def _run(coroutine):
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else None) as runner:
        return runner.run(coroutine)


def _scoped_url(url, schema):
    value = urlsplit(url)
    if value.scheme != "postgresql":
        raise ValueError("PostgreSQL test URL required")
    query = [(key, item) for key, item in parse_qsl(value.query) if key != "options"]
    query.append(("options", "-csearch_path=" + schema))
    return urlunsplit(value._replace(query=urlencode(query)))


def _digest(user):
    from pydantic import SecretStr

    return SecretStr(hashlib.sha256(("synthetic-k04-" + user).encode()).hexdigest())


@asynccontextmanager
async def _database(*, pool_size=3):
    import psycopg
    from psycopg import sql
    from psycopg_pool import AsyncConnectionPool

    from aletheia_lab.hosted.workspace_migration import migrate_workspace_schema

    database = os.environ["K04_TEST_DATABASE_URL"]
    schema = "k04_authz_" + uuid.uuid4().hex
    scoped = _scoped_url(database, schema)
    created = False
    async with await psycopg.AsyncConnection.connect(database, autocommit=True) as admin:
        try:
            await admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
            created = True
            await admin.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
            for name in ("session", "source", "jobs"):
                await admin.execute((ROOT / "docs" / f"j01-experimental-{name}-schema.sql").read_text(encoding="utf-8"))
            users = (("owner", "owner"), ("admin", "admin"), ("analyst", "analyst"),
                     ("reviewer", "reviewer"), ("viewer", "viewer"), ("other", "owner"))
            for user, _ in users:
                await admin.execute("INSERT INTO hosted_identity_bindings VALUES "
                    "('issuer',%s,%s,'Synthetic',%s,true,true,0,'ready',%s,null)",
                    (user, user, user + "@example.invalid", "wb" if user == "other" else "wa"))
                await admin.execute("INSERT INTO j01_sessions VALUES (%s,%s,'synthetic-csrf','active',"
                    "statement_timestamp(),statement_timestamp(),statement_timestamp(),statement_timestamp()+interval '900 seconds',"
                    "statement_timestamp()+interval '28000 seconds',0,0)", ("ctx_" + user, user))
                await admin.execute("INSERT INTO j01_session_credentials VALUES (%s,%s,'active',0)",
                                    (_digest(user).get_secret_value(), "ctx_" + user))
            await admin.execute("INSERT INTO j01_workspaces VALUES ('wa','Tenant A'),('wb','Tenant B')")
            for user, role in users:
                await admin.execute("INSERT INTO j01_memberships VALUES (%s,%s,%s,true,1)",
                                    ("wb" if user == "other" else "wa", user, role))
            for source, workspace, user in (("sa", "wa", "analyst"), ("sb", "wb", "other")):
                payload = ("synthetic bytes " + source).encode()
                await admin.execute("INSERT INTO j01_sources(source_id,workspace_id,creator_id,request_key_hash,"
                    "sha256,payload,manifest) VALUES (%s,%s,%s,%s,%s,%s,'[]')",
                    (source, workspace, user, "fixture-key-" + source, hashlib.sha256(payload).hexdigest(), payload))
            async with await psycopg.AsyncConnection.connect(scoped) as conn:
                assert await migrate_workspace_schema(conn, schema) == "adopted"
            async with AsyncConnectionPool(scoped, min_size=1, max_size=pool_size, open=False) as pool:
                await pool.wait(timeout=10)
                yield pool
        finally:
            if created:
                await admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def _repositories(pool):
    from aletheia_lab.hosted.postgres_jobs import PostgresJobs
    from aletheia_lab.hosted.postgres_sources import PostgresSources

    sources = PostgresSources(pool, maximum_upload_bytes=10000, maximum_expanded_bytes=20000, maximum_entries=10)
    return sources, PostgresJobs(pool, sources)


async def _membership(pool, user, *, active):
    async with pool.connection() as conn:
        await conn.execute("SELECT user_id FROM hosted_identity_bindings WHERE user_id=%s FOR UPDATE", (user,))
        await conn.execute("SELECT workspace_id FROM j01_workspaces WHERE workspace_id='wa' FOR UPDATE")
        await conn.execute("UPDATE j01_memberships SET active=%s,revision=revision+1 "
                           "WHERE workspace_id='wa' AND user_id=%s", (active, user))


class BarrierPool:
    def __init__(self, pool, prefix):
        self.pool, self.prefix = pool, prefix
        self.entered, self.release = asyncio.Event(), asyncio.Event()

    @asynccontextmanager
    async def connection(self):
        async with self.pool.connection() as conn:
            outer = self

            class Connection:
                autocommit = False

                async def execute(self, query, params=()):
                    if query.startswith(outer.prefix):
                        outer.entered.set()
                        await outer.release.wait()
                    return await conn.execute(query, params)

            yield Connection()


def test_current_roles_parent_grants_cross_tenant_and_job_visibility():
    from pydantic import SecretStr

    from aletheia_lab.hosted.authorization import AuthorizationDenied, PostgresAuthorization

    async def scenario():
        async with _database() as pool:
            sources, jobs = _repositories(pool)
            assert await jobs.is_ready()
            policy = PostgresAuthorization()
            for user, allowed in (("owner", True), ("admin", True), ("analyst", True),
                                  ("reviewer", False), ("viewer", False)):
                async with pool.connection() as conn:
                    if allowed:
                        assert (await policy.authorize(conn, _digest(user).get_secret_value(), "wa", "analyze")).role == user
                    else:
                        with pytest.raises(AuthorizationDenied):
                            await policy.authorize(conn, _digest(user).get_secret_value(), "wa", "analyze")
            assert await sources.read(credential_digest=_digest("analyst"), workspace_id="wa", source_id="sa")
            for user, workspace, source in (("viewer", "wa", "sa"), ("other", "wa", "sa"),
                                            ("owner", "wa", "sb"), ("owner", "wb", "sb")):
                with pytest.raises(AuthorizationDenied):
                    await sources.read(credential_digest=_digest(user), workspace_id=workspace, source_id=source)
            receipt = await jobs.enqueue(credential_digest=_digest("analyst"), workspace_id="wa", operation="preview",
                                         request_key=SecretStr("synthetic-preview-01"), request={"source_id": "sa"})
            assert (await jobs.status(credential_digest=_digest("owner"), workspace_id="wa", job_id=receipt.job_id)).status == "queued"
            with pytest.raises(AuthorizationDenied):
                await jobs.status(credential_digest=_digest("viewer"), workspace_id="wa", job_id=receipt.job_id)
            async with pool.connection() as conn:
                await conn.execute("INSERT INTO j01_source_grants(workspace_id,source_id,user_id,actions) "
                                   "VALUES ('wa','sa','viewer',ARRAY['read','export'])")
            assert (await jobs.status(credential_digest=_digest("viewer"), workspace_id="wa", job_id=receipt.job_id)).status == "queued"
            with pytest.raises(AuthorizationDenied):
                await jobs.enqueue(credential_digest=_digest("viewer"), workspace_id="wa", operation="preview",
                                   request_key=SecretStr("synthetic-denied-key"), request={"source_id": "sa"})
            lease = SecretStr("b" * 64)
            claim = await jobs.claim(lease_digest=lease, lease_seconds=30)
            assert claim.job_id == receipt.job_id
            assert await jobs.complete(claim=claim, lease_digest=lease, result={"synthetic": True})
            for user in ("owner", "analyst", "viewer"):
                output = await jobs.output(credential_digest=_digest(user), workspace_id="wa", job_id=receipt.job_id)
                assert output["result"] == {"synthetic": True}
            with pytest.raises(AuthorizationDenied):
                await jobs.output(credential_digest=_digest("reviewer"), workspace_id="wa", job_id=receipt.job_id)
            async with pool.connection() as conn:
                count = await (await conn.execute("SELECT count(*) FROM j01_jobs")).fetchone()
                assert count == (1,)
    _run(scenario())


def test_revoke_barriers_deny_enqueue_and_publish_keep_receipts_and_measure_current_output():
    from pydantic import SecretStr

    from aletheia_lab.hosted.authorization import AuthorizationDenied

    async def scenario():
        async with _database() as pool:
            _, jobs = _repositories(pool)
            receipt = await jobs.enqueue(credential_digest=_digest("analyst"), workspace_id="wa", operation="preview",
                                         request_key=SecretStr("synthetic-running-01"), request={"source_id": "sa"})
            lease = SecretStr("c" * 64)
            claim = await jobs.claim(lease_digest=lease, lease_seconds=30)
            assert claim.job_id == receipt.job_id
            barrier = BarrierPool(pool, "SELECT user_id FROM hosted_identity_bindings")
            _, blocked_jobs = _repositories(barrier)
            publication = asyncio.create_task(blocked_jobs.complete(claim=claim, lease_digest=lease, result={"safe": True}))
            enqueue_barrier = BarrierPool(pool, "SELECT user_id FROM hosted_identity_bindings")
            _, blocked_enqueue = _repositories(enqueue_barrier)
            submission = asyncio.create_task(blocked_enqueue.enqueue(credential_digest=_digest("analyst"),
                workspace_id="wa", operation="preview", request_key=SecretStr("synthetic-raced-enqueue"),
                request={"source_id": "sa"}))
            await asyncio.wait_for(asyncio.gather(barrier.entered.wait(), enqueue_barrier.entered.wait()), 5)
            await _membership(pool, "analyst", active=False)
            committed = time.perf_counter()
            barrier.release.set()
            enqueue_barrier.release.set()
            with pytest.raises(AuthorizationDenied):
                await publication
            with pytest.raises(AuthorizationDenied):
                await submission
            assert await jobs.fail(claim=claim, lease_digest=lease)
            for call in (jobs.output(credential_digest=_digest("analyst"), workspace_id="wa", job_id=receipt.job_id),
                         jobs.enqueue(credential_digest=_digest("analyst"), workspace_id="wa", operation="preview",
                                      request_key=SecretStr("synthetic-after-revoke"), request={"source_id": "sa"})):
                with pytest.raises(AuthorizationDenied):
                    await call
            latency = time.perf_counter() - committed
            assert latency <= 5
            async with pool.connection() as conn:
                row = await (await conn.execute("SELECT state,result,request_id FROM j01_jobs WHERE job_id=%s",
                                                (receipt.job_id,))).fetchone()
                assert row == ("failed", None, receipt.request_id)
                assert await (await conn.execute("SELECT count(*) FROM j01_jobs")).fetchone() == (1,)
            print(f"K04 revoke commit-to-denied publication/output/enqueue: {latency:.6f}s; local PostgreSQL")
    _run(scenario())


def test_revoked_queued_dispatch_is_terminal_and_parallel_idempotency_preserves_one_receipt():
    from pydantic import SecretStr

    async def scenario():
        async with _database() as pool:
            _, jobs = _repositories(pool)
            args = dict(credential_digest=_digest("analyst"), workspace_id="wa", operation="preview",
                        request_key=SecretStr("synthetic-parallel-01"), request={"source_id": "sa"})
            receipts = await asyncio.gather(jobs.enqueue(**args), jobs.enqueue(**args))
            assert receipts[0] == receipts[1]
            await _membership(pool, "analyst", active=False)
            assert await jobs.claim(lease_digest=SecretStr("e" * 64), lease_seconds=30) is None
            async with pool.connection() as conn:
                row = await (await conn.execute("SELECT state,result,request_id FROM j01_jobs WHERE job_id=%s",
                                                (receipts[0].job_id,))).fetchone()
                assert row == ("failed", None, receipts[0].request_id)
    _run(scenario())


def test_same_pooled_connection_reuses_explicit_tenant_after_success_rollback_and_cancellation():
    from aletheia_lab.hosted.authorization import AuthorizationDenied

    async def scenario():
        async with _database(pool_size=1) as pool:
            sources, _ = _repositories(pool)
            async with pool.connection() as conn:
                original_pid = await (await conn.execute("SELECT pg_backend_pid()")).fetchone()
            assert await sources.read(credential_digest=_digest("analyst"), workspace_id="wa", source_id="sa")
            with pytest.raises(AuthorizationDenied):
                await sources.read(credential_digest=_digest("analyst"), workspace_id="wb", source_id="sb")
            with pytest.raises(RuntimeError, match="synthetic"):
                async with pool.connection() as conn:
                    await conn.execute("SELECT source_id FROM j01_sources WHERE workspace_id='wa'")
                    raise RuntimeError("synthetic rollback")
            entered, release = asyncio.Event(), asyncio.Event()

            async def cancelled_transaction():
                async with pool.connection() as conn:
                    await conn.execute("SELECT source_id FROM j01_sources WHERE workspace_id='wa'")
                    entered.set()
                    await release.wait()

            pending = asyncio.create_task(cancelled_transaction())
            await asyncio.wait_for(entered.wait(), 5)
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
            assert await sources.read(credential_digest=_digest("other"), workspace_id="wb", source_id="sb")
            with pytest.raises(AuthorizationDenied):
                await sources.read(credential_digest=_digest("other"), workspace_id="wa", source_id="sa")
            async with pool.connection() as conn:
                assert await (await conn.execute("SELECT pg_backend_pid()")).fetchone() == original_pid
                assert await (await conn.execute("SELECT current_setting('app.workspace_id',true)")).fetchone() in ((None,), ("",))
    _run(scenario())


def test_snapshot_publication_and_reads_inherit_current_source_parent_grant(tmp_path):
    from pydantic import SecretStr

    from aletheia_lab.hosted.authorization import AuthorizationDenied
    from aletheia_lab.hosted.postgres_catalog import CatalogIntegrityError
    from aletheia_lab.hosted.upload_project import UploadProject

    async def scenario():
        async with _database() as pool:
            sources, jobs = _repositories(pool)
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w") as archive:
                archive.writestr("dataset.csv", "id,target\n1,0\n2,1\n")
                archive.writestr("metrics.csv", "run,name,value\nbaseline,loss,0.5\ncandidate,loss,0.6\n")
                archive.writestr("config.json", '{"seed":42}\n')
            payload, captured = buffer.getvalue(), "2026-11-10T00:00:00Z"
            source = await sources.put(credential_digest=_digest("analyst"), workspace_id="wa",
                                       request_key=SecretStr("synthetic-snapshot-source"), payload=payload)
            project = UploadProject(tmp_path, maximum_upload_bytes=10000,
                                    maximum_expanded_bytes=20000, maximum_entries=10)
            candidates = project.preview(payload, captured_at=captured)["mapping_candidates"]
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
            snapshot = project.confirm(payload, workspace_id="wa", source_id=source.source_id,
                                       mapping=mapping, captured_at=captured)
            receipt = await jobs.enqueue(credential_digest=_digest("analyst"), workspace_id="wa", operation="confirm",
                request_key=SecretStr("synthetic-confirm-snapshot"), request={"source_id": source.source_id, "mapping": mapping})
            lease = SecretStr("f" * 64)
            claim = await jobs.claim(lease_digest=lease, lease_seconds=30)
            assert claim.job_id == receipt.job_id
            assert await jobs.complete_snapshot(claim=claim, lease_digest=lease, snapshot=snapshot)
            assert await sources.snapshot(credential_digest=_digest("analyst"), workspace_id="wa",
                                          snapshot_id=snapshot.snapshot_id) == snapshot
            with pytest.raises(AuthorizationDenied):
                await sources.snapshot(credential_digest=_digest("viewer"), workspace_id="wa", snapshot_id=snapshot.snapshot_id)
            with pytest.raises(AuthorizationDenied):
                await sources.snapshot(credential_digest=_digest("other"), workspace_id="wb", snapshot_id=snapshot.snapshot_id)
            async with pool.connection() as conn:
                await conn.execute("INSERT INTO j01_source_grants(workspace_id,source_id,user_id,actions) "
                    "VALUES ('wa',%s,'viewer',ARRAY['read'])", (source.source_id,))
            assert await sources.snapshot(credential_digest=_digest("viewer"), workspace_id="wa",
                                          snapshot_id=snapshot.snapshot_id) == snapshot
            with pytest.raises(AuthorizationDenied):
                await sources.publish_snapshot(credential_digest=_digest("viewer"), snapshot=snapshot)
            async with pool.connection() as conn:
                await conn.execute("UPDATE j01_source_grants SET actions=ARRAY[]::text[],revision=revision+1 "
                                   "WHERE workspace_id='wa' AND source_id=%s AND user_id='viewer'", (source.source_id,))
            with pytest.raises(AuthorizationDenied):
                await sources.snapshot(credential_digest=_digest("viewer"), workspace_id="wa", snapshot_id=snapshot.snapshot_id)
            async with pool.connection() as conn:
                await conn.execute("UPDATE j01_upload_snapshots SET source_id='sa' WHERE snapshot_id=%s", (snapshot.snapshot_id,))
            with pytest.raises(CatalogIntegrityError):
                await sources.snapshot(credential_digest=_digest("analyst"), workspace_id="wa", snapshot_id=snapshot.snapshot_id)
    _run(scenario())

