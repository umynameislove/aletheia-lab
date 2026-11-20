"""Verified v2 upgrade retains old receipts and adds scoped retry lineage."""
from __future__ import annotations

import hashlib
import os

import pytest
from test_k05_migrations_postgres import _database, _run, _seed

from aletheia_lab.hosted.persistence_migration import (
    _LEDGER_DDL,
    BASE_DDL,
    CATALOG_DDL,
    MIGRATIONS,
    _baseline,
    _context,
    _signature,
    migrate,
    verify,
)
from aletheia_lab.hosted.workspace_migration import migrate as migrate_workspace

pytestmark = [pytest.mark.integration, pytest.mark.skipif(not os.environ.get('K05_TEST_DATABASE_URL'),
    reason='Explicit K-05 PostgreSQL test URL required')]


async def _v2(pool, schema):
    async with pool.connection() as conn:
        await _context(conn, schema)
        await _baseline(conn, schema)
        await conn.execute(_LEDGER_DDL)
        await conn.execute('INSERT INTO k05_persistence_migrations(version,checksum,signature) VALUES(1,%s,%s)',
            (hashlib.sha256(BASE_DDL.encode()).hexdigest(), await _signature(conn, schema, 1)))
        await migrate_workspace(conn, schema)
        await conn.execute(CATALOG_DDL)
        await conn.execute('INSERT INTO k05_persistence_migrations(version,checksum,signature) VALUES(2,%s,%s)',
            (hashlib.sha256(CATALOG_DDL.encode()).hexdigest(), await _signature(conn, schema, 2)))
    await _seed(pool)
    async with pool.connection() as conn:
        for scope in ('a', 'b'):
            await conn.execute('INSERT INTO j01_sources(source_id,workspace_id,creator_id,request_key_hash,sha256,payload,manifest) '
                'VALUES(%s,%s,%s,%s,%s,%s,\'{}\')', ('source_' + scope, scope, 'user_' + scope,
                    '0' * 64, hashlib.sha256(b'{}').hexdigest(), b'{}'))
            await conn.execute('INSERT INTO j01_jobs(job_id,workspace_id,creator_id,credential_digest,operation,key_hash,'
                'request_hash,request,request_id,state) VALUES(%s,%s,%s,%s,\'preview\',%s,%s,%s::jsonb,%s,\'queued\')',
                ('job_' + scope, scope, 'user_' + scope, '1' * 64, '2' * 64, '3' * 64,
                 '{"source_id":"source_' + scope + '"}', 'request_' + scope))


def test_v2_upgrade_preserves_legacy_receipt_and_nullable_retry_then_reruns():
    async def run():
        async with _database() as (pool, schema):
            await _v2(pool, schema)
            async with pool.connection() as conn:
                assert not await verify(conn, schema)
                before = await (await conn.execute('SELECT job_id,workspace_id,creator_id,operation,key_hash,request_hash,request,request_id,state '
                    'FROM j01_jobs ORDER BY job_id')).fetchall()
                assert await migrate(conn, schema) == 'upgraded'
                assert await verify(conn, schema)
                after = await (await conn.execute('SELECT job_id,workspace_id,creator_id,operation,key_hash,request_hash,request,request_id,state '
                    'FROM j01_jobs ORDER BY job_id')).fetchall()
                assert before == after
                assert await (await conn.execute('SELECT count(*) FROM j01_jobs WHERE retry_of_job_id IS NULL')).fetchone() == (2,)
                assert await migrate(conn, schema) == 'unchanged'
                assert await (await conn.execute('SELECT version FROM k05_persistence_migrations ORDER BY version')).fetchall() == [(version,) for version in range(1, len(MIGRATIONS) + 1)]
                duplicate = await (await conn.execute('INSERT INTO j01_jobs(job_id,workspace_id,creator_id,credential_digest,operation,key_hash,'
                    'request_hash,request,request_id,state) VALUES(\'duplicate\',\'a\',\'user_a\',%s,\'preview\',%s,%s,'
                    '\'{"source_id":"source_a"}\',\'duplicate_request\',\'queued\') '
                    'ON CONFLICT(workspace_id,creator_id,operation,key_hash) DO NOTHING RETURNING job_id',
                    ('1' * 64, '2' * 64, '3' * 64))).fetchone()
                assert duplicate is None
                assert await (await conn.execute("SELECT job_id,request_id FROM j01_jobs WHERE workspace_id='a'")).fetchone() == ('job_a', 'request_a')
    _run(run())


def test_v3_retry_cannot_cross_tenant_and_cancelled_is_a_terminal_value():
    import psycopg

    async def run():
        async with _database() as (pool, schema):
            await _v2(pool, schema)
            async with pool.connection() as conn:
                await migrate(conn, schema)
                await conn.execute("UPDATE j01_jobs SET state='cancelled' WHERE job_id='job_a'")
                await conn.execute('INSERT INTO j01_jobs(job_id,workspace_id,creator_id,credential_digest,operation,key_hash,'
                    'request_hash,request,request_id,state,retry_of_job_id) VALUES(\'job_retry\',\'a\',\'user_a\',%s,\'preview\',%s,%s,'
                    '\'{"source_id":"source_a"}\',\'retry_request\',\'queued\',\'job_a\')', ('1' * 64, '4' * 64, '3' * 64))
            with pytest.raises(psycopg.errors.ForeignKeyViolation):
                async with pool.connection() as conn:
                    await conn.execute("UPDATE j01_jobs SET retry_of_job_id='job_b' WHERE job_id='job_retry'")
            with pytest.raises(psycopg.errors.RaiseException, match="terminal job cannot reopen"):
                async with pool.connection() as conn:
                    await conn.execute("UPDATE j01_jobs SET state='cancel_requested' WHERE job_id='job_a'")
            async with pool.connection() as conn:
                assert await (await conn.execute("SELECT state,retry_of_job_id FROM j01_jobs WHERE job_id='job_retry'")).fetchone() == ('queued', 'job_a')
                assert await (await conn.execute("SELECT state FROM j01_jobs WHERE job_id='job_a'")).fetchone() == ('cancelled',)
    _run(run())


def test_v3_transaction_rollback_restores_v2_schema_rows_and_upgrade_can_resume():
    async def run():
        async with _database() as (pool, schema):
            await _v2(pool, schema)
            with pytest.raises(RuntimeError, match='injected rollback'):
                async with pool.connection() as conn:
                    assert await migrate(conn, schema) == 'upgraded'
                    raise RuntimeError('injected rollback')
            async with pool.connection() as conn:
                assert await (await conn.execute('SELECT max(version) FROM k05_persistence_migrations')).fetchone() == (2,)
                assert await (await conn.execute("SELECT count(*) FROM pg_attribute WHERE attrelid='j01_jobs'::regclass "
                    "AND attname='retry_of_job_id' AND NOT attisdropped")).fetchone() == (0,)
                assert await (await conn.execute('SELECT count(*) FROM j01_jobs')).fetchone() == (2,)
                assert await migrate(conn, schema) == 'upgraded'
                assert await verify(conn, schema)
    _run(run())
