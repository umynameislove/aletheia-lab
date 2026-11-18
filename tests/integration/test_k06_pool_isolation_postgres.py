"""Real per-tenant lock isolation and bounded pool-exhaustion recovery."""
from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager

import pytest
from pydantic import SecretStr
from test_k06_idempotency_postgres import _database, _digest, _repositories, _run

from aletheia_lab.hosted.transactions import TransactionUnavailable, run_transaction

pytestmark = [pytest.mark.integration, pytest.mark.skipif(
    not os.environ.get("K06_TEST_DATABASE_URL"), reason="Explicit PostgreSQL test database required")]


def test_blocked_workspace_does_not_serialize_another_tenant():
    async def scenario():
        async with _database(pool_size=3) as pool:
            waiting = asyncio.Event()
            blocked_pid = None

            class ScheduledPool:
                @asynccontextmanager
                async def connection(self):
                    async with pool.connection() as conn:
                        class Connection:
                            autocommit = False

                            async def execute(self, query, params=()):
                                nonlocal blocked_pid
                                if query.startswith("SELECT workspace_id FROM j01_workspaces") and params == ("wa",):
                                    blocked_pid = (await (await conn.execute("SELECT pg_backend_pid()")).fetchone())[0]
                                    waiting.set()
                                return await conn.execute(query, params)

                        yield Connection()

            _, jobs = _repositories(ScheduledPool())
            async with pool.connection() as blocker:
                await blocker.execute("SELECT workspace_id FROM j01_workspaces WHERE workspace_id='wa' FOR UPDATE")
                blocked = asyncio.create_task(jobs.enqueue(credential_digest=_digest("analyst"), workspace_id="wa",
                    operation="preview", request_key=SecretStr("independent-tenant-key-a"), request={"source_id": "sa"}))
                await asyncio.wait_for(waiting.wait(), 2)
                receipt = await asyncio.wait_for(jobs.enqueue(credential_digest=_digest("analyst"), workspace_id="wb",
                    operation="preview", request_key=SecretStr("independent-tenant-key-b"), request={"source_id": "sb"}), 2)
                assert receipt.workspace_id == "wb" and not blocked.done()
                assert await (await blocker.execute("SELECT wait_event_type FROM pg_stat_activity WHERE pid=%s",
                                                    (blocked_pid,))).fetchone() == ("Lock",)
                assert await (await blocker.execute("SELECT count(*) FROM j01_jobs WHERE workspace_id='wb'")).fetchone() == (1,)
                assert await (await blocker.execute("SELECT count(*) FROM j01_jobs WHERE workspace_id='wa'")).fetchone() == (0,)
            assert (await asyncio.wait_for(blocked, 2)).workspace_id == "wa"
            async with pool.connection() as conn:
                assert await (await conn.execute("SELECT count(*) FROM j01_jobs")).fetchone() == (2,)
    _run(scenario())


def test_full_pool_wait_has_one_deadline_then_releases_and_reuses_every_slot():
    async def scenario():
        async with _database(pool_size=2) as pool:
            called = False

            async def effect(conn):
                nonlocal called
                called = True
                return await (await conn.execute("SELECT 1")).fetchone()

            async with pool.connection() as first, pool.connection() as second:
                assert await (await first.execute("SELECT pg_backend_pid()")).fetchone() != await (await second.execute("SELECT pg_backend_pid()")).fetchone()
                started = asyncio.get_running_loop().time()
                with pytest.raises(TransactionUnavailable):
                    await run_transaction(pool, effect, timeout_seconds=0.2)
                assert asyncio.get_running_loop().time() - started < 1
                assert not called
            assert await run_transaction(pool, effect, timeout_seconds=1) == (1,)
            async with pool.connection() as first, pool.connection() as second:
                assert await (await first.execute("SELECT 1")).fetchone() == (1,)
                assert await (await second.execute("SELECT 1")).fetchone() == (1,)
    _run(scenario())
