"""Opt-in durable idempotency checks using owned PostgreSQL schemas and real rows."""
from __future__ import annotations

import asyncio
import hashlib
import io
import os
import sys
import uuid
import zipfile
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = [pytest.mark.integration, pytest.mark.skipif(
    not os.environ.get("K06_TEST_DATABASE_URL"), reason="Explicit PostgreSQL test database required")]


def _run(coroutine):
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else None) as runner:
        return runner.run(coroutine)


def _digest(user):
    from pydantic import SecretStr

    return SecretStr(hashlib.sha256(("synthetic-k06-" + user).encode()).hexdigest())


def _bundle(marker="one"):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, payload in (("dataset.csv", "id,target\n1,0\n2,1\n"),
                              ("metrics.csv", "run,name,value\nbaseline,loss,0.5\ncandidate,loss,0.6\n"),
                              ("config.json", '{"synthetic":"' + marker + '"}\n')):
            archive.writestr(zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0)), payload)
    return buffer.getvalue()


@asynccontextmanager
async def _database(*, pool_size=3):
    import psycopg
    from psycopg import sql
    from psycopg_pool import AsyncConnectionPool

    from aletheia_lab.hosted.workspace_migration import migrate_workspace_schema

    database = os.environ["K06_TEST_DATABASE_URL"]
    schema = "k06_idempotency_" + uuid.uuid4().hex
    parsed = urlsplit(database)
    if parsed.scheme != "postgresql":
        raise ValueError("PostgreSQL test URL required")
    query = [(key, value) for key, value in parse_qsl(parsed.query) if key != "options"]
    query.append(("options", "-csearch_path=" + schema))
    scoped = urlunsplit(parsed._replace(query=urlencode(query)))
    created = False
    async with await psycopg.AsyncConnection.connect(database, autocommit=True) as admin:
        try:
            await admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
            created = True
            await admin.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
            for name in ("session", "source", "jobs"):
                await admin.execute((ROOT / "docs" / f"j01-experimental-{name}-schema.sql").read_text(encoding="utf-8"))
            for user in ("owner", "analyst"):
                await admin.execute("INSERT INTO hosted_identity_bindings VALUES "
                    "('issuer',%s,%s,'Synthetic',%s,true,true,0,'ready','wa',null)",
                    (user, user, user + "@example.invalid"))
                await admin.execute("INSERT INTO j01_sessions VALUES (%s,%s,'synthetic-csrf','active',"
                    "statement_timestamp(),statement_timestamp(),statement_timestamp(),statement_timestamp()+interval '900 seconds',"
                    "statement_timestamp()+interval '28000 seconds',0,0)", ("ctx_" + user, user))
                await admin.execute("INSERT INTO j01_session_credentials VALUES (%s,%s,'active',0)",
                                    (_digest(user).get_secret_value(), "ctx_" + user))
            await admin.execute("INSERT INTO j01_workspaces VALUES ('wa','Tenant A'),('wb','Tenant B')")
            for workspace in ("wa", "wb"):
                for user in ("owner", "analyst"):
                    await admin.execute("INSERT INTO j01_memberships VALUES (%s,%s,%s,true,1)",
                                        (workspace, user, user))
            for source, workspace in (("sa", "wa"), ("sb", "wb")):
                payload = ("synthetic bytes " + source).encode()
                await admin.execute("INSERT INTO j01_sources(source_id,workspace_id,creator_id,request_key_hash,"
                    "sha256,payload,manifest) VALUES (%s,%s,'analyst',%s,%s,%s,'[]')",
                    (source, workspace, "fixture-key-" + source, hashlib.sha256(payload).hexdigest(), payload))
            async with await psycopg.AsyncConnection.connect(scoped) as conn:
                assert await migrate_workspace_schema(conn, schema) == "adopted"
            async with AsyncConnectionPool(scoped, min_size=pool_size, max_size=pool_size, open=False) as pool:
                await pool.wait(timeout=15)
                yield pool
        finally:
            if created:
                await admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def _repositories(pool):
    from aletheia_lab.hosted.postgres_jobs import PostgresJobs
    from aletheia_lab.hosted.postgres_sources import PostgresSources

    sources = PostgresSources(pool, maximum_upload_bytes=10000, maximum_expanded_bytes=20000, maximum_entries=10)
    return sources, PostgresJobs(pool, sources)


class _InsertBarrierPool:
    """Hold real connections at the insert boundary; every query still runs in PG."""

    def __init__(self, pool, prefix, participants=20):
        self.pool, self.prefix = pool, prefix
        self.barrier = asyncio.Barrier(participants)
        self.backend_pids = set()

    @asynccontextmanager
    async def connection(self):
        async with self.pool.connection() as conn:
            outer = self

            class Connection:
                autocommit = False
                synchronized = False

                async def execute(self, query, params=()):
                    if not self.synchronized and query.startswith(outer.prefix):
                        self.synchronized = True
                        pid = await (await conn.execute("SELECT pg_backend_pid()")).fetchone()
                        outer.backend_pids.add(pid[0])
                        await outer.barrier.wait()
                    return await conn.execute(query, params)

            yield Connection()


def test_twenty_real_connections_same_key_create_one_source_and_one_job_receipt():
    from pydantic import SecretStr

    async def scenario():
        async with _database(pool_size=20) as pool:
            # Each API process owns its bounded parser; synchronize before the
            # workspace quota lock, which must serialize source publication.
            source_barrier = _InsertBarrierPool(pool, "SELECT pg_advisory_xact_lock")
            source_args = dict(credential_digest=_digest("analyst"), workspace_id="wa",
                               request_key=SecretStr("same-source-key-0001"), payload=_bundle())
            receipts = await asyncio.wait_for(asyncio.gather(*(
                _repositories(source_barrier)[0].put(**source_args) for _ in range(20))), 30)
            assert receipts == [receipts[0]] * 20
            assert len(source_barrier.backend_pids) == 20
            job_barrier = _InsertBarrierPool(pool, "INSERT INTO j01_jobs")
            _, jobs = _repositories(job_barrier)
            job_args = dict(credential_digest=_digest("analyst"), workspace_id="wa", operation="preview",
                            request_key=SecretStr("same-job-key-000001"), request={"source_id": receipts[0].source_id})
            jobs_receipts = await asyncio.wait_for(asyncio.gather(*(jobs.enqueue(**job_args) for _ in range(20))), 30)
            assert jobs_receipts == [jobs_receipts[0]] * 20
            assert len(job_barrier.backend_pids) == 20
            async with pool.connection() as conn:
                assert await (await conn.execute("SELECT count(*) FROM j01_sources WHERE source_id NOT IN ('sa','sb')")).fetchone() == (1,)
                assert await (await conn.execute("SELECT count(*),count(DISTINCT request_id) FROM j01_jobs")).fetchone() == (1, 1)
            print("K06 DB14: 20 real backend connections, one source row and one job admission/receipt")
    _run(scenario())


def test_twenty_concurrent_requests_with_different_bodies_conflict_without_second_admission():
    from pydantic import SecretStr

    from aletheia_lab.hosted.postgres_sources import SourceConflict

    async def scenario():
        async with _database(pool_size=20) as pool:
            barrier = _InsertBarrierPool(pool, "INSERT INTO j01_jobs")
            _, jobs = _repositories(barrier)
            calls = [jobs.enqueue(credential_digest=_digest("analyst"), workspace_id="wa", operation="confirm",
                request_key=SecretStr("different-body-key-01"), request={"source_id": "sa", "mapping": {"target": index % 2}})
                for index in range(20)]
            outcomes = await asyncio.wait_for(asyncio.gather(*calls, return_exceptions=True), 30)
            conflicts = [value for value in outcomes if isinstance(value, SourceConflict)]
            receipts = [value for value in outcomes if not isinstance(value, BaseException)]
            assert len(conflicts) == len(receipts) == 10
            assert receipts == [receipts[0]] * 10
            assert len(barrier.backend_pids) == 20
            async with pool.connection() as conn:
                assert await (await conn.execute("SELECT count(*) FROM j01_jobs")).fetchone() == (1,)
            print("K06 DB15: 20 real backend connections, 10 shared receipts, 10 conflicts, one job row")
    _run(scenario())


def test_key_scope_separates_actor_workspace_operation_and_source_upload():
    from pydantic import SecretStr

    async def scenario():
        async with _database() as pool:
            sources, jobs = _repositories(pool)
            key = SecretStr("shared-scoped-key-0001")
            source_receipts = [await sources.put(credential_digest=_digest(user), workspace_id=workspace,
                request_key=key, payload=_bundle()) for user, workspace in
                (("analyst", "wa"), ("owner", "wa"), ("analyst", "wb"))]
            assert len({receipt.source_id for receipt in source_receipts}) == 3
            receipts = []
            for user, workspace, operation, request in (
                ("analyst", "wa", "preview", {"source_id": "sa"}),
                ("owner", "wa", "preview", {"source_id": "sa"}),
                ("analyst", "wb", "preview", {"source_id": "sb"}),
                ("analyst", "wa", "confirm", {"source_id": "sa", "mapping": {}}),
            ):
                receipts.append(await jobs.enqueue(credential_digest=_digest(user), workspace_id=workspace,
                    operation=operation, request_key=key, request=request))
            assert len({receipt.job_id for receipt in receipts}) == 4
            async with pool.connection() as conn:
                assert await (await conn.execute("SELECT count(*) FROM j01_jobs")).fetchone() == (4,)
    _run(scenario())


def test_source_content_conflict_and_job_fingerprint_preserve_numeric_and_unicode_meaning():
    from pydantic import SecretStr

    from aletheia_lab.hosted.postgres_sources import SourceConflict

    async def scenario():
        async with _database() as pool:
            sources, jobs = _repositories(pool)
            source_args = dict(credential_digest=_digest("analyst"), workspace_id="wa",
                               request_key=SecretStr("source-conflict-key-01"))
            original = await sources.put(**source_args, payload=_bundle())
            with pytest.raises(SourceConflict):
                await sources.put(**source_args, payload=_bundle("two"))
            assert await sources.put(**source_args, payload=_bundle()) == original
            args = dict(credential_digest=_digest("analyst"), workspace_id="wa", operation="confirm",
                        request_key=SecretStr("fingerprint-key-00001"))
            receipt = await jobs.enqueue(**args, request={"source_id": "sa", "mapping": {"label": "é", "value": 1}})
            assert await jobs.enqueue(**args, request={"mapping": {"value": 1, "label": "é"}, "source_id": "sa"}) == receipt
            for mapping in ({"label": "e\u0301", "value": 1}, {"label": "é", "value": 1.0},
                            {"label": "é", "value": True}):
                with pytest.raises(SourceConflict):
                    await jobs.enqueue(**args, request={"source_id": "sa", "mapping": mapping})
            async with pool.connection() as conn:
                assert await (await conn.execute("SELECT count(*) FROM j01_jobs")).fetchone() == (1,)
                assert await (await conn.execute("SELECT count(*) FROM j01_sources WHERE source_id NOT IN ('sa','sb')")).fetchone() == (1,)
    _run(scenario())


def test_lost_response_repository_reconstruction_and_completed_replay_keep_initial_receipt():
    from pydantic import SecretStr

    async def scenario():
        async with _database() as pool:
            sources, jobs = _repositories(pool)
            source_args = dict(credential_digest=_digest("analyst"), workspace_id="wa",
                               request_key=SecretStr("lost-source-key-0001"), payload=_bundle())
            initial_source = await sources.put(**source_args)
            args = dict(credential_digest=_digest("analyst"), workspace_id="wa", operation="preview",
                        request_key=SecretStr("lost-response-key-01"), request={"source_id": initial_source.source_id})
            await jobs.enqueue(**args)  # The caller discards the committed ACK.
            async with pool.connection() as conn:
                saved = await (await conn.execute("SELECT job_id,request_id FROM j01_jobs")).fetchone()
            sources, jobs = _repositories(pool)
            assert await sources.put(**source_args) == initial_source
            receipt = await jobs.enqueue(**args)
            assert (receipt.job_id, receipt.request_id) == saved
            lease = SecretStr("6" * 64)
            claim = await jobs.claim(lease_digest=lease, lease_seconds=30)
            assert claim.job_id == receipt.job_id
            assert await jobs.complete(claim=claim, lease_digest=lease, result={"synthetic_effect": "one"})
            assert not await jobs.complete(claim=claim, lease_digest=lease, result={"synthetic_effect": "two"})
            _, reconstructed = _repositories(pool)
            assert await reconstructed.enqueue(**args) == receipt
            assert receipt.status == "queued"
            assert (await reconstructed.status(credential_digest=_digest("analyst"), workspace_id="wa",
                                               job_id=receipt.job_id)).status == "succeeded"
            assert (await reconstructed.output(credential_digest=_digest("analyst"), workspace_id="wa",
                                               job_id=receipt.job_id))["result"] == {"synthetic_effect": "one"}
            async with pool.connection() as conn:
                assert await (await conn.execute("SELECT count(*) FROM j01_jobs")).fetchone() == (1,)
            print("K06 DB16: committed ACK discarded; fresh repository recovers original queued receipt after completion")
    _run(scenario())


def test_replay_requires_current_source_grant_and_membership_before_returning_receipt():
    from pydantic import SecretStr

    from aletheia_lab.hosted.authorization import AuthorizationDenied

    async def scenario():
        async with _database() as pool:
            sources, jobs = _repositories(pool)
            async with pool.connection() as conn:
                await conn.execute("UPDATE j01_sources SET creator_id='owner' WHERE source_id='sa'")
                await conn.execute("INSERT INTO j01_source_grants(workspace_id,source_id,user_id,actions) "
                    "VALUES ('wa','sa','analyst',ARRAY['read','write'])")
            args = dict(credential_digest=_digest("analyst"), workspace_id="wa", operation="preview",
                        request_key=SecretStr("revoke-replay-key-01"), request={"source_id": "sa"})
            receipt = await jobs.enqueue(**args)
            async with pool.connection() as conn:
                await conn.execute("UPDATE j01_source_grants SET actions=ARRAY['read'],revision=revision+1 "
                                   "WHERE workspace_id='wa' AND source_id='sa' AND user_id='analyst'")
            with pytest.raises(AuthorizationDenied):
                await jobs.enqueue(**args)
            source_args = dict(credential_digest=_digest("analyst"), workspace_id="wa",
                               request_key=SecretStr("membership-source-key-01"), payload=_bundle())
            await sources.put(**source_args)
            async with pool.connection() as conn:
                await conn.execute("SELECT user_id FROM hosted_identity_bindings WHERE user_id='analyst' FOR UPDATE")
                await conn.execute("SELECT workspace_id FROM j01_workspaces WHERE workspace_id='wa' FOR UPDATE")
                await conn.execute("UPDATE j01_memberships SET active=false,revision=revision+1 "
                                   "WHERE workspace_id='wa' AND user_id='analyst'")
            for call in (jobs.enqueue(**args), sources.put(**source_args), jobs.status(
                    credential_digest=_digest("analyst"), workspace_id="wa", job_id=receipt.job_id)):
                with pytest.raises(AuthorizationDenied):
                    await call
            async with pool.connection() as conn:
                assert await (await conn.execute("SELECT job_id,request_id,state FROM j01_jobs")).fetchone() == (
                    receipt.job_id, receipt.request_id, "queued")
    _run(scenario())
