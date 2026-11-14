"""V3-to-V4 storage adoption and the workspace-page index access path."""
from __future__ import annotations

import hashlib
import os

import pytest
from test_k05_job_migration_postgres import _v2
from test_k05_migrations_postgres import _database, _run

from aletheia_lab.hosted.persistence_migration import (
    JOB_DDL,
    MIGRATIONS,
    _signature,
    migrate,
    verify,
)

pytestmark = [pytest.mark.integration, pytest.mark.skipif(not os.environ.get('K05_TEST_DATABASE_URL'),
    reason='Explicit K-05 PostgreSQL test URL required')]


async def _v3(pool, schema):
    await _v2(pool, schema)
    async with pool.connection() as conn:
        await conn.execute(JOB_DDL)
        await conn.execute('INSERT INTO k05_persistence_migrations(version,checksum,signature) VALUES(3,%s,%s)',
            (hashlib.sha256(JOB_DDL.encode()).hexdigest(), await _signature(conn, schema, 3)))


def test_genuine_v3_upgrade_adds_workspace_index_and_preserves_rows_receipts():
    async def run():
        async with _database() as (pool, schema):
            await _v3(pool, schema)
            async with pool.connection() as conn:
                assert not await verify(conn, schema)
                before = await (await conn.execute('SELECT job_id,request_id,state,retry_of_job_id FROM j01_jobs ORDER BY job_id')).fetchall()
                assert await migrate(conn, schema) == 'upgraded'
                assert await verify(conn, schema)
                assert await (await conn.execute('SELECT job_id,request_id,state,retry_of_job_id FROM j01_jobs ORDER BY job_id')).fetchall() == before
                assert await (await conn.execute('SELECT version FROM k05_persistence_migrations ORDER BY version')).fetchall() == [(version,) for version in range(1, len(MIGRATIONS) + 1)]
                assert await migrate(conn, schema) == 'unchanged'
                index = await (await conn.execute("SELECT indisvalid,indisready FROM pg_index WHERE indexrelid='k05_records_workspace_page'::regclass")).fetchone()
                assert index == (True, True)
    _run(run())


def test_v4_rollback_leaves_genuine_v3_index_ledger_and_data_untouched():
    async def run():
        async with _database() as (pool, schema):
            await _v3(pool, schema)
            with pytest.raises(RuntimeError, match='injected v4 rollback'):
                async with pool.connection() as conn:
                    assert await migrate(conn, schema) == 'upgraded'
                    raise RuntimeError('injected v4 rollback')
            async with pool.connection() as conn:
                assert await (await conn.execute('SELECT max(version) FROM k05_persistence_migrations')).fetchone() == (3,)
                assert await (await conn.execute("SELECT to_regclass('k05_records_workspace_page')")).fetchone() == (None,)
                assert await (await conn.execute('SELECT count(*) FROM j01_jobs')).fetchone() == (2,)
                assert await migrate(conn, schema) == 'upgraded'
                assert await verify(conn, schema)
    _run(run())


def test_workspace_page_plan_uses_type_order_key_without_project_filter():
    async def run():
        async with _database() as (pool, schema):
            await _v3(pool, schema)
            async with pool.connection() as conn:
                await migrate(conn, schema)
                # SQL-structure fixture only: generated indexes are not a domain/scale benchmark.
                await conn.execute("INSERT INTO k05_records(workspace_id,record_id,project_id,record_type,schema_version,canonical_sha256,object_sha256) "
                    "SELECT 'a','p3-bundle-'||lpad(to_hex(n),64,'0'),%s,'project_bundle','v1',%s,%s FROM generate_series(1,4096) n",
                    ('p3-project-' + 'a' * 64, 'd' * 64, 'e' * 64))
                snapshot = 'p3-snapshot-' + '7' * 64
                await conn.execute("INSERT INTO k05_records(workspace_id,record_id,project_id,record_type,schema_version,canonical_sha256,object_sha256) "
                    "VALUES('a',%s,%s,'snapshot','v1',%s,%s)", (snapshot, 'p3-project-' + 'a' * 64, 'd' * 64, 'e' * 64))
                await conn.execute("INSERT INTO k05_records(workspace_id,record_id,project_id,record_type,schema_version,canonical_sha256,object_sha256,snapshot_id) "
                    "SELECT 'a','p6-result-'||lpad(to_hex(n),64,'0'),%s,'result','v1',lpad(to_hex(n),64,'0'),%s,%s FROM generate_series(1,128) n",
                    ('p3-project-' + 'a' * 64, 'e' * 64, snapshot))
                await conn.execute('ANALYZE k05_records')
                facts = (await (await conn.execute("EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) SELECT record_id,project_id,record_type,"
                    "schema_version,canonical_sha256,object_sha256,snapshot_id,parent_result_id FROM k05_records "
                    "WHERE workspace_id='a' AND record_type='result' AND record_id>'' ORDER BY record_id LIMIT 51")).fetchone())[0][0]
                stack = [facts['Plan']]
                nodes = []
                while stack:
                    node = stack.pop()
                    nodes.append(node)
                    stack.extend(node.get('Plans', ()))
                assert any(node.get('Index Name') == 'k05_records_workspace_page' for node in nodes)
                assert facts['Plan']['Actual Rows'] == 51
                assert all(node.get('Rows Removed by Filter', 0) == 0 for node in nodes)
    _run(run())
