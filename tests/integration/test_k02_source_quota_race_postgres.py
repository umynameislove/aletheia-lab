"""Source put cannot double spend a reservation while upload completion converts it."""
from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager

import pytest
from pydantic import SecretStr
from test_k05_migrations_postgres import _run
from test_k05_native_results_postgres import _case
from test_k07_upload_staging_postgres import _manifest

from aletheia_lab.hosted.postgres_sources import PostgresSources, SourceReceipt
from aletheia_lab.hosted.upload_staging import UploadRejected, UploadStatus
from aletheia_lab.hosted.upload_staging_migration import migrate as migrate_uploads

pytestmark = [pytest.mark.integration, pytest.mark.skipif(not os.environ.get("K02_TEST_DATABASE_URL"),
    reason="Explicit K02 owned-schema PostgreSQL test URL required")]


@pytest.fixture(autouse=True)
def configured_database(monkeypatch):
    monkeypatch.setenv("K05_TEST_DATABASE_URL", os.environ["K02_TEST_DATABASE_URL"])


class _Row:
    def __init__(self, value): self.value = value

    async def fetchone(self):
        value, self.value = self.value, None
        return value


class _Connection:
    def __init__(self, connection, pool, transaction):
        self.connection, self.pool, self.transaction = connection, pool, transaction

    @property
    def autocommit(self): return self.connection.autocommit

    async def execute(self, query, params=(), **kwargs):
        pool = self.pool
        if pool.label == "complete" and "pg_advisory_xact_lock" in query and params == ("aletheia-k07-quota:b",):
            pool.trace.append((self.transaction, "quota_attempt"))
            pool.quota_attempted.set()
        if pool.label == "complete" and "FROM k07_uploads" in query and "FOR UPDATE" in query:
            pool.trace.append((self.transaction, "upload_row"))
        cursor = await self.connection.execute(query, params, **kwargs)
        if (pool.label == "put" and query.startswith("SELECT COALESCE(sum(octet_length(payload)),0)")
                and "FROM j01_sources" in query):
            row = await cursor.fetchone()
            assert row == (0,), "The isolated workspace starts with reserved bytes only."
            pool.stored_read.set()
            await pool.release_put.wait()
            return _Row(row)
        return cursor


class _Pool:
    def __init__(self, pool, label, *, stored_read, release_put, quota_attempted):
        self.pool, self.label, self.trace, self.transactions = pool, label, [], 0
        self.stored_read, self.release_put, self.quota_attempted = stored_read, release_put, quota_attempted

    @asynccontextmanager
    async def connection(self):
        async with self.pool.connection() as conn:
            self.transactions += 1
            yield _Connection(conn, self, self.transactions)


class _CompletionSources(PostgresSources):
    def __init__(self, *args, parsed, allow_commit, **kwargs):
        super().__init__(*args, **kwargs)
        self.parsed, self.allow_commit = parsed, allow_commit

    async def _inspect(self, payload):
        entries = await super()._inspect(payload)
        self.parsed.set()
        await self.allow_commit.wait()
        return entries


async def _interleave(case, sources, upload, budgets):
    parsed, allow_commit = asyncio.Event(), asyncio.Event()
    stored_read, release_put, quota_attempted = asyncio.Event(), asyncio.Event(), asyncio.Event()
    arguments = dict(stored_read=stored_read, release_put=release_put, quota_attempted=quota_attempted)
    complete_pool, put_pool = (_Pool(case.pool, label, **arguments) for label in ("complete", "put"))
    completing = _CompletionSources(complete_pool, parsed=parsed, allow_commit=allow_commit, **budgets)
    putting = PostgresSources(put_pool, **budgets)
    tasks = []
    try:
        complete = asyncio.create_task(completing.complete_upload(credential_digest=case.b,
            workspace_id="b", upload_id=upload.upload_id))
        tasks.append(complete)
        await asyncio.wait_for(parsed.wait(), 15)
        put = asyncio.create_task(putting.put(credential_digest=case.b, workspace_id="b",
            request_key=SecretStr("quota-race-direct-put-b-002"), payload=case.payload))
        tasks.append(put)
        await asyncio.wait_for(stored_read.wait(), 5)
        # B owns the quota lock and has read stored=0. A is already parsed, ready to convert reservation→source.
        allow_commit.set()
        attempted = asyncio.create_task(quota_attempted.wait())
        tasks.append(attempted)
        await asyncio.wait_for(asyncio.wait((complete, attempted), return_when=asyncio.FIRST_COMPLETED), 5)
        # Correct A waits on the same quota lock. Before the fix A commits, allowing B to miss both forms of its bytes.
        release_put.set()
        completed, admitted = await asyncio.gather(complete, put, return_exceptions=True)
        return completed, admitted, complete_pool.trace
    finally:
        allow_commit.set()
        release_put.set()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def test_upload_conversion_and_direct_put_share_quota_fence_without_double_spend(tmp_path):
    async def scenario():
        async with _case(tmp_path) as case:
            async with case.pool.connection() as conn:
                schema = (await (await conn.execute("SELECT current_schema()")).fetchone())[0]
                await migrate_uploads(conn, schema)
            size = len(case.payload)
            quota = size + size // 2
            budgets = dict(maximum_upload_bytes=10000, maximum_expanded_bytes=20000,
                           maximum_entries=10, maximum_workspace_bytes=quota)
            sources = PostgresSources(case.pool, **budgets)
            manifest, parts = _manifest(case.payload)
            upload = await sources.create_upload(credential_digest=case.b, workspace_id="b",
                request_key=SecretStr("quota-race-reserved-a-001"), manifest=manifest)
            for number, part in enumerate(parts, 1):
                await sources.put_upload_part(credential_digest=case.b, workspace_id="b", upload_id=upload.upload_id,
                                               part_number=number, payload=part)
            completed, admitted, trace = await _interleave(case, sources, upload, budgets)
            assert isinstance(completed, UploadStatus) and completed.state == "ready", repr(completed)
            assert isinstance(admitted, UploadRejected) and admitted.code == "upload_quota_exceeded", repr(admitted)
            assert admitted.status == 413
            quota_transaction = next(transaction for transaction, kind in trace if kind == "quota_attempt")
            conversion_trace = [kind for transaction, kind in trace if transaction == quota_transaction]
            assert conversion_trace.index("quota_attempt") < conversion_trace.index("upload_row")
            async with case.pool.connection() as conn:
                assert await (await conn.execute("SELECT count(*),COALESCE(sum(octet_length(payload)),0) "
                    "FROM j01_sources WHERE workspace_id='b'")).fetchone() == (1, size)
                assert await (await conn.execute("SELECT state,cleaned_at IS NOT NULL FROM k07_uploads "
                    "WHERE workspace_id='b' AND upload_id=%s", (upload.upload_id,))).fetchone() == ("ready", True)
                assert await (await conn.execute("SELECT COALESCE(sum(byte_size),0) FROM k07_uploads "
                    "WHERE workspace_id='b' AND cleaned_at IS NULL")).fetchone() == (0,)
                assert size <= quota < 2 * size
    _run(scenario())


class _ReplayConnection:
    def __init__(self, connection, pool, label, transaction):
        self.connection, self.pool, self.label, self.transaction = connection, pool, label, transaction

    @property
    def autocommit(self):
        return self.connection.autocommit

    async def execute(self, query, params=(), **kwargs):
        pool = self.pool
        if "pg_advisory_xact_lock" in query and params == ("aletheia-k07-quota:b",):
            pool.trace.append((self.label, self.transaction, "quota"))
            if self.label == "quota-replay-second":
                pool.second_attempted.set()
        cursor = await self.connection.execute(query, params, **kwargs)
        if query.startswith("SELECT source_id FROM j01_sources"):
            row = await cursor.fetchone()
            pool.trace.append((self.label, self.transaction, "previous"))
            if self.label == "quota-replay-first" and row is None:
                pool.first_previous.set()
                await pool.release_first.wait()
            return _Row(row)
        return cursor


class _ReplayPool:
    def __init__(self, pool):
        self.pool, self.trace, self.transactions = pool, [], 0
        self.first_previous, self.release_first, self.second_attempted = (asyncio.Event() for _ in range(3))

    @asynccontextmanager
    async def connection(self):
        async with self.pool.connection() as conn:
            self.transactions += 1
            label = asyncio.current_task().get_name()
            yield _ReplayConnection(conn, self, label, self.transactions)


def test_identical_parallel_put_replays_at_exact_workspace_quota_across_parser_owners(tmp_path):
    async def scenario():
        async with _case(tmp_path) as case:
            size = len(case.payload)
            pool = _ReplayPool(case.pool)
            budgets = dict(maximum_upload_bytes=size, maximum_expanded_bytes=20000,
                           maximum_entries=10, maximum_workspace_bytes=size)
            first, second = PostgresSources(pool, **budgets), PostgresSources(pool, **budgets)
            assert first is not second and first._pool is second._pool
            request = dict(credential_digest=case.b, workspace_id="b", payload=case.payload,
                           request_key=SecretStr("quota-identical-replay-race-001"))
            tasks = []
            try:
                tasks.append(asyncio.create_task(first.put(**request), name="quota-replay-first"))
                await asyncio.wait_for(pool.first_previous.wait(), 5)
                tasks.append(asyncio.create_task(second.put(**request), name="quota-replay-second"))
                # Signal BEFORE awaiting the DB lock: waiting for both lookups would deadlock the correct fence.
                await asyncio.wait_for(pool.second_attempted.wait(), 5)
                pool.release_first.set()
                receipts = await asyncio.gather(*tasks, return_exceptions=True)
                assert all(isinstance(receipt, SourceReceipt) for receipt in receipts), receipts
                assert receipts[0] == receipts[1]
                for label, transaction, kind in pool.trace:
                    if kind == "previous":
                        facts = [event for owner, number, event in pool.trace
                                 if (owner, number) == (label, transaction)]
                        assert facts.index("quota") < facts.index("previous")
            finally:
                pool.release_first.set()
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
            async with case.pool.connection() as conn:
                assert await (await conn.execute("SELECT count(*),COALESCE(sum(octet_length(payload)),0) "
                    "FROM j01_sources WHERE workspace_id='b'")).fetchone() == (1, size)
    _run(scenario())
