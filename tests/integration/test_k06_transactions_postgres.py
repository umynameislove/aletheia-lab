"""Real PostgreSQL abort/retry/cancel with UUID-owned schema and barriers."""
from __future__ import annotations

import asyncio
import os
import sys
from contextlib import asynccontextmanager
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4

import pytest

from aletheia_lab.hosted.transactions import (
    TransactionOutcomeUnknown,
    TransactionUnavailable,
    run_transaction,
)

pytestmark = [pytest.mark.integration, pytest.mark.skipif(not os.environ.get("K06_TEST_DATABASE_URL"),
    reason="Explicit K-06 PostgreSQL test URL required")]


def _run(coro):
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else None) as runner:
        return runner.run(coro)


@asynccontextmanager
async def _database():
    import psycopg
    from psycopg import sql
    from psycopg_pool import AsyncConnectionPool

    schema = "k06_tx_" + uuid4().hex
    database = os.environ["K06_TEST_DATABASE_URL"]
    value = urlsplit(database)
    pairs = [(k, v) for k, v in parse_qsl(value.query) if k != "options"]
    pairs.append(("options", "-csearch_path=" + schema))
    scoped = urlunsplit(value._replace(query=urlencode(pairs)))
    async with await psycopg.AsyncConnection.connect(database, autocommit=True) as admin:
        await admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            async with AsyncConnectionPool(scoped, min_size=1, max_size=4, open=False) as pool:
                await pool.wait(timeout=10)
                async with pool.connection() as conn:
                    await conn.execute("CREATE TABLE counters(id integer PRIMARY KEY,value integer NOT NULL)")
                    await conn.execute("INSERT INTO counters VALUES(1,0),(2,0)")
                    await conn.execute("CREATE TABLE effects(operation text PRIMARY KEY)")
                yield pool
        finally:
            assert schema.startswith("k06_tx_") and len(schema) == 39
            await admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


def test_real_deadlock_retries_whole_transaction_without_duplicate_effect():
    async def run():
        async with _database() as pool:
            barrier = asyncio.Barrier(2)
            attempts = {1: 0, 2: 0}

            async def writer(first):
                async def operation(conn):
                    attempts[first] += 1
                    await conn.execute("UPDATE counters SET value=value+1 WHERE id=%s", (first,))
                    if attempts[first] == 1:
                        await barrier.wait()
                    await conn.execute("UPDATE counters SET value=value+1 WHERE id=%s", (3 - first,))
                    await conn.execute("INSERT INTO effects VALUES(%s)", (str(first),))
                    return first
                return await run_transaction(pool, operation)

            assert sorted(await asyncio.gather(writer(1), writer(2))) == [1, 2]
            assert sorted(attempts.values()) == [1, 2]
            async with pool.connection() as conn:
                assert await (await conn.execute("SELECT sum(value) FROM counters")).fetchone() == (4,)
                assert await (await conn.execute("SELECT count(*) FROM effects")).fetchone() == (2,)
    _run(run())


def test_real_serialization_failure_rereads_state_and_commits_once():
    async def run():
        async with _database() as pool:
            barrier = asyncio.Barrier(2)
            observed = {1: [], 2: []}

            async def writer(identity):
                async def operation(conn):
                    await conn.execute("SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
                    row = await (await conn.execute("SELECT value FROM counters WHERE id=1")).fetchone()
                    observed[identity].append(row[0])
                    if len(observed[identity]) == 1:
                        await barrier.wait()
                    await conn.execute("UPDATE counters SET value=%s WHERE id=1", (row[0] + 1,))
                    await conn.execute("INSERT INTO effects VALUES(%s)", (str(identity),))
                await run_transaction(pool, operation)

            await asyncio.gather(writer(1), writer(2))
            assert sorted(observed.values(), key=len) == [[0], [0, 1]]
            async with pool.connection() as conn:
                assert await (await conn.execute("SELECT value FROM counters WHERE id=1")).fetchone() == (2,)
                assert await (await conn.execute("SELECT count(*) FROM effects")).fetchone() == (2,)
    _run(run())


def test_real_lock_timeout_is_bounded_and_pool_remains_usable():
    async def run():
        async with _database() as pool:
            attempts = 0
            async with pool.connection() as blocker:
                await blocker.execute("SELECT id FROM counters WHERE id=1 FOR UPDATE")

                async def operation(conn):
                    nonlocal attempts
                    attempts += 1
                    await conn.execute("SET LOCAL lock_timeout='100ms'")
                    await conn.execute("UPDATE counters SET value=value+1 WHERE id=1")

                with pytest.raises(TransactionUnavailable):
                    await run_transaction(pool, operation, timeout_seconds=1)
            assert attempts == 1
            async with pool.connection() as conn:
                assert await (await conn.execute("UPDATE counters SET value=value+1 WHERE id=1 RETURNING value")).fetchone() == (1,)
    _run(run())


def test_real_cancellation_rolls_back_and_releases_row_lock():
    async def run():
        async with _database() as pool:
            entered = asyncio.Event()

            async def operation(conn):
                await conn.execute("UPDATE counters SET value=99 WHERE id=1")
                await conn.execute("INSERT INTO effects VALUES('cancelled')")
                entered.set()
                await asyncio.Event().wait()

            pending = asyncio.create_task(run_transaction(pool, operation))
            await entered.wait()
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await pending
            async with pool.connection() as conn:
                await conn.execute("SET LOCAL lock_timeout='200ms'")
                assert await (await conn.execute("UPDATE counters SET value=value+1 WHERE id=1 RETURNING value")).fetchone() == (1,)
                assert await (await conn.execute("SELECT count(*) FROM effects")).fetchone() == (0,)
    _run(run())


def test_real_commit_with_injected_ack_loss_is_not_replayed():
    import psycopg

    async def run():
        async with _database() as pool:
            class LostAck:
                entries = 0

                @asynccontextmanager
                async def connection(self):
                    self.entries += 1
                    async with pool.connection() as conn:
                        yield conn
                    # PostgreSQL committed; transport loss is injected at this boundary.
                    raise psycopg.OperationalError("synthetic lost commit acknowledgement")

            broken = LostAck()

            async def operation(conn):
                await conn.execute("UPDATE counters SET value=value+1 WHERE id=1")
                await conn.execute("INSERT INTO effects VALUES('durable-operation')")

            with pytest.raises(TransactionOutcomeUnknown):
                await run_transaction(broken, operation)
            assert broken.entries == 1
            async with pool.connection() as conn:
                assert await (await conn.execute("SELECT value FROM counters WHERE id=1")).fetchone() == (1,)
                assert await (await conn.execute("SELECT count(*) FROM effects")).fetchone() == (1,)
    _run(run())
