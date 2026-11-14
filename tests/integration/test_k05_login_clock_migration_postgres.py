"""The DB login-intent clock upgrade expires legacy claims before backfill."""
from __future__ import annotations

import hashlib
import os

import pytest
from test_k05_migrations_postgres import _database, _run
from test_k05_workspace_index_postgres import _v3

from aletheia_lab.hosted.persistence_migration import (
    BASE_DDL,
    MIGRATIONS,
    PersistenceMigrationRejected,
    _context,
    _signature,
    migrate,
    verify,
)

pytestmark = [pytest.mark.integration, pytest.mark.skipif(not os.environ.get('K05_TEST_DATABASE_URL'),
    reason='Explicit K-05 PostgreSQL test URL required')]


async def _v6(pool, schema):
    await _v3(pool, schema)
    async with pool.connection() as conn:
        for version, ddl in enumerate(MIGRATIONS[3:6], 4):
            await conn.execute(ddl)
            await conn.execute('INSERT INTO k05_persistence_migrations(version,checksum,signature) VALUES(%s,%s,%s)',
                (version, hashlib.sha256(ddl.encode()).hexdigest(), await _signature(conn, schema, version)))


async def _intent(conn, name, phase='pending'):
    digest = hashlib.sha256(name.encode()).hexdigest()
    await conn.execute('INSERT INTO j01_fresh_login_browsers(browser_digest,epoch,latest_intent) VALUES(%s,1,%s)', (digest, name))
    await conn.execute('INSERT INTO j01_fresh_login_intents(intent_id,browser_digest,state_digest,issuer,client_id,'
        'redirect_uri,sealed_proof,created_at,expires_at,browser_epoch,return_to,reauthenticate,phase,claim_digest) '
        "VALUES(%s,%s,%s,'https://clock.example.invalid','synthetic','https://app.example.invalid/callback',%s,"
        "clock_timestamp()+interval '10 seconds',clock_timestamp()+interval '600 seconds',1,'/',false,%s,%s)",
        (name, digest, digest, b'synthetic-sealed-proof-for-clock-test', phase, None if phase == 'pending' else digest))


def test_genuine_v6_upgrade_expires_pending_before_stamp_preserves_history_and_reruns():
    async def run():
        async with _database() as (pool, schema):
            await _v6(pool, schema)
            async with pool.connection() as conn:
                for phase in ('pending', 'claimed', 'committed'):
                    await _intent(conn, phase, phase)
                before = dict((row[0], row[1:]) for row in await (await conn.execute(
                    'SELECT intent_id,created_at,expires_at,phase FROM j01_fresh_login_intents ORDER BY intent_id')).fetchall())
                assert not await verify(conn, schema)
                assert await migrate(conn, schema) == 'upgraded'
                assert await verify(conn, schema)
                after = await (await conn.execute('SELECT intent_id,created_at,expires_at,phase,database_created_at '
                    'FROM j01_fresh_login_intents ORDER BY intent_id')).fetchall()
                for name, created, expires, phase, database_created in after:
                    assert created == before[name][0] and phase == before[name][2]
                    if phase in ('pending', 'claimed'):
                        assert expires <= database_created < created
                    else:
                        assert expires == before[name][1]
                await _intent(conn, 'fresh')
                fresh = await (await conn.execute("SELECT created_at,expires_at,database_created_at FROM j01_fresh_login_intents WHERE intent_id='fresh'")).fetchone()
                assert await migrate(conn, schema) == 'unchanged'
                assert await (await conn.execute("SELECT created_at,expires_at,database_created_at FROM j01_fresh_login_intents WHERE intent_id='fresh'")).fetchone() == fresh
                assert fresh[1] > fresh[2]
    _run(run())


def test_v7_rollback_retains_v6_pending_expiry_host_time_and_ledger():
    async def run():
        async with _database() as (pool, schema):
            await _v6(pool, schema)
            async with pool.connection() as conn:
                await _intent(conn, 'pending')
                before = await (await conn.execute('SELECT created_at,expires_at,phase FROM j01_fresh_login_intents')).fetchone()
            with pytest.raises(RuntimeError, match='injected v7 rollback'):
                async with pool.connection() as conn:
                    assert await migrate(conn, schema) == 'upgraded'
                    raise RuntimeError('injected v7 rollback')
            async with pool.connection() as conn:
                assert await (await conn.execute('SELECT max(version) FROM k05_persistence_migrations')).fetchone() == (6,)
                assert await (await conn.execute("SELECT count(*) FROM pg_attribute WHERE attrelid='j01_fresh_login_intents'::regclass "
                    "AND attname='database_created_at' AND NOT attisdropped")).fetchone() == (0,)
                assert await (await conn.execute('SELECT created_at,expires_at,phase FROM j01_fresh_login_intents')).fetchone() == before
                assert await migrate(conn, schema) == 'upgraded'
                assert await verify(conn, schema)
    _run(run())


def test_exact_modern_experimental_clock_adoption_still_expires_existing_claims():
    async def run():
        async with _database() as (pool, schema), pool.connection() as conn:
            await _context(conn, schema)
            await conn.execute(BASE_DDL)
            await conn.execute('ALTER TABLE j01_fresh_login_intents ADD COLUMN database_created_at timestamptz NOT NULL DEFAULT clock_timestamp()')
            await _intent(conn, 'pending')
            stamp = await (await conn.execute('SELECT database_created_at FROM j01_fresh_login_intents')).fetchone()
            assert await migrate(conn, schema) == 'adopted'
            assert await verify(conn, schema)
            row = await (await conn.execute('SELECT database_created_at,expires_at<=clock_timestamp() FROM j01_fresh_login_intents')).fetchone()
            assert row == (stamp[0], True)
    _run(run())

@pytest.mark.parametrize('column_ddl', [
    'database_created_at timestamptz DEFAULT clock_timestamp()',
    'database_created_at timestamptz NOT NULL DEFAULT statement_timestamp()',
    'database_created_at text NOT NULL DEFAULT \'invalid\'',
    'unrecognized_extra text',
    'database_created_at timestamptz NOT NULL DEFAULT clock_timestamp(), ADD COLUMN unrecognized_extra text',
])
def test_modern_adoption_rejects_nullable_wrong_default_type_or_extra_columns(column_ddl):
    async def run():
        async with _database() as (pool, schema):
            async with pool.connection() as conn:
                await _context(conn, schema)
                await conn.execute(BASE_DDL)
                await conn.execute('ALTER TABLE j01_fresh_login_intents ADD COLUMN ' + column_ddl)
            with pytest.raises(PersistenceMigrationRejected):
                async with pool.connection() as conn:
                    await migrate(conn, schema)
            async with pool.connection() as conn:
                assert await (await conn.execute("SELECT to_regclass('k05_persistence_migrations')")).fetchone() == (None,)
    _run(run())
