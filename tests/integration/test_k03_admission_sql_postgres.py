"""Real admission migration/policy SQL preserves counters and rejects corrupt data."""
from __future__ import annotations

import os

import pytest
from pydantic import SecretStr
from test_k05_migrations_postgres import _database, _run

from aletheia_lab.hosted_auth.abuse import RatePolicy
from aletheia_lab.hosted_auth.admission_migration import MigrationRejected, change_policy, migrate
from aletheia_lab.hosted_auth.postgres_admission import PostgresAuthAdmission

pytestmark = [pytest.mark.integration, pytest.mark.skipif(not os.environ.get('K05_TEST_DATABASE_URL'),
    reason='Explicit private native PostgreSQL required')]


def test_composed_admission_migration_and_policy_preserve_ledger_and_existing_counter():
    async def run():
        async with _database() as (pool, schema):
            async with pool.connection() as conn:
                assert await migrate(conn, schema) == 'created'
                before = await (await conn.execute('SELECT * FROM k03_auth_admission_migrations')).fetchall()
            old, new = RatePolicy(9, 3, 60), RatePolicy(12, 4, 60)
            store = PostgresAuthAdmission(pool, namespace='synthetic-sql-composition')
            await store.prepare(old)
            async with pool.connection() as conn:
                assert await change_policy(conn, schema, 'synthetic-sql-composition', expected=old, replacement=new)
                assert not await change_policy(conn, schema, 'synthetic-sql-composition', expected=new, replacement=new)
            assert (await store.consume('renew', SecretStr('a' * 64), new)).allowed
            async with pool.connection() as conn:
                row = await (await conn.execute("SELECT * FROM k03_auth_admission WHERE operation='renew'")).fetchone()
                assert await migrate(conn, schema) == 'unchanged'
                assert await (await conn.execute("SELECT * FROM k03_auth_admission WHERE operation='renew'")).fetchone() == row
                assert await (await conn.execute('SELECT * FROM k03_auth_admission_migrations')).fetchall() == before
                await conn.execute("UPDATE k03_auth_admission SET subjects='{}'::jsonb WHERE operation='renew'")
            with pytest.raises(MigrationRejected):
                async with pool.connection() as conn:
                    await migrate(conn, schema)
            async with pool.connection() as conn:
                assert await (await conn.execute("SELECT attempts FROM k03_auth_admission WHERE operation='renew'")).fetchone() == (1,)
                assert await (await conn.execute('SELECT * FROM k03_auth_admission_migrations')).fetchall() == before
    _run(run())
