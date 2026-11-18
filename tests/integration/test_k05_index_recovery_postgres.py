"""Controlled interruption leaves an invalid index; verification rejects it."""
from __future__ import annotations

import asyncio
import os

import pytest
from test_k05_migrations_postgres import _database, _run, _scoped

from aletheia_lab.hosted.persistence_migration import migrate, verify

pytestmark = [pytest.mark.integration, pytest.mark.skipif(not os.environ.get('K05_TEST_DATABASE_URL'),
    reason='Explicit K-05 PostgreSQL test URL required')]


def test_interrupted_concurrent_index_is_detected_and_repaired_before_adoption():
    import psycopg

    async def run():
        async with _database() as (pool, schema):
            async with pool.connection() as conn:
                await migrate(conn, schema)
                await conn.execute('DROP INDEX k05_records_page')
            async with pool.connection() as writer:
                # A real writer lock fences concurrent index validation even on an empty table.
                await writer.execute('UPDATE k05_records SET record_id=record_id WHERE false')
                async with await psycopg.AsyncConnection.connect(
                        _scoped(os.environ['K05_TEST_DATABASE_URL'], schema), autocommit=True) as indexer:
                    task = asyncio.create_task(indexer.execute('CREATE INDEX CONCURRENTLY k05_records_page '
                        'ON k05_records(workspace_id,project_id,record_type,record_id)'))
                    async with pool.connection() as observer:
                        async with asyncio.timeout(10):
                            while True:
                                row = await (await observer.execute("SELECT i.indisvalid FROM pg_index i "
                                    "JOIN pg_class c ON c.oid=i.indexrelid JOIN pg_namespace n ON n.oid=c.relnamespace "
                                    "WHERE n.nspname=%s AND c.relname='k05_records_page'", (schema,))).fetchone()
                                if row == (False,):
                                    break
                                if task.done():
                                    await task
                                    raise AssertionError('Index must remain fenced by the controlled writer')
                        task.cancel()
                        with pytest.raises(asyncio.CancelledError):
                            await task
            async with pool.connection() as conn:
                row = await (await conn.execute("SELECT indisvalid FROM pg_index WHERE indexrelid='k05_records_page'::regclass")).fetchone()
                assert row == (False,)
                assert not await verify(conn, schema)
                await conn.execute('DROP INDEX k05_records_page')
                await conn.execute('CREATE INDEX k05_records_page ON k05_records(workspace_id,project_id,record_type,record_id)')
                assert await verify(conn, schema)
    _run(run())
