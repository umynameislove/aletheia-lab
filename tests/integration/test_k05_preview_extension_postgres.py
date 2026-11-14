"""A pinned independently verified preview extension never refreshes core facts."""
from __future__ import annotations

import hashlib
import os

import pytest
from test_k05_login_clock_migration_postgres import _v6
from test_k05_migrations_postgres import _database, _run

from aletheia_lab.hosted.persistence_migration import (
    MIGRATIONS,
    PersistenceMigrationRejected,
    migrate,
    verify,
)
from aletheia_lab.hosted.preview_migration import migrate as migrate_preview
from aletheia_lab.hosted.preview_migration import verify as verify_preview
from aletheia_lab.hosted.upload_staging_migration import DDL as STAGING_DDL
from aletheia_lab.hosted.upload_staging_migration import _signature as staging_signature
from aletheia_lab.hosted.upload_staging_migration import verify as verify_staging

pytestmark = [pytest.mark.integration, pytest.mark.skipif(not os.environ.get('K05_TEST_DATABASE_URL'),
    reason='Explicit K-05 PostgreSQL test URL required')]


async def _core_ledger(conn):
    return await (await conn.execute('SELECT version,checksum,signature,applied_at FROM k05_persistence_migrations ORDER BY version')).fetchall()


async def _known_v6_extensions(pool, schema):
    await _v6(pool, schema)
    async with pool.connection() as conn:
        before = await _core_ledger(conn)
        # Genuine old-release staging fixture: its frozen DDL/signature, without
        # invoking the current staging admission check that now requires core v7.
        await conn.execute(STAGING_DDL)
        await conn.execute('INSERT INTO k07_upload_migrations VALUES(1,%s,%s)',
            (hashlib.sha256(STAGING_DDL.encode()).hexdigest(), await staging_signature(conn, schema)))
        assert await migrate_preview(conn, schema) == 'adopted'
        assert await verify_preview(conn) and await verify_staging(conn, schema)
        assert await _core_ledger(conn) == before
        return before


def test_verified_v6_plus_installed_known_extension_upgrades_without_refreshing_ledger():
    async def run():
        async with _database() as (pool, schema):
            before = await _known_v6_extensions(pool, schema)
            async with pool.connection() as conn:
                assert not await verify(conn, schema)
                assert await migrate(conn, schema) == 'upgraded'
                assert await verify(conn, schema)
                assert await verify_preview(conn) and await verify_staging(conn, schema)
                after = await _core_ledger(conn)
                assert after[:6] == before and len(after) == len(MIGRATIONS)
                assert await verify(conn, schema)
                assert await _core_ledger(conn) == after
                assert await migrate(conn, schema) == 'unchanged'
                assert await _core_ledger(conn) == after
    _run(run())


@pytest.mark.parametrize('tamper', ['disable_guard', 'drop_index', 'wrong_index', 'wrong_checksum', 'missing_ledger'])
def test_preview_partial_or_tampered_objects_fail_closed_without_ledger_refresh(tamper):
    async def run():
        async with _database() as (pool, schema):
            await _known_v6_extensions(pool, schema)
            async with pool.connection() as conn:
                await migrate(conn, schema)
                before = await _core_ledger(conn)
                if tamper == 'disable_guard':
                    await conn.execute('ALTER TABLE j01_jobs DISABLE TRIGGER k07_confirm_preview_guard')
                elif tamper == 'drop_index':
                    await conn.execute('DROP INDEX k07_jobs_preview_ref')
                elif tamper == 'wrong_index':
                    await conn.execute('DROP INDEX k07_jobs_preview_ref')
                    await conn.execute('CREATE INDEX k07_jobs_preview_ref ON j01_jobs(workspace_id,request_id)')
                elif tamper == 'wrong_checksum':
                    await conn.execute('UPDATE k07_preview_migrations SET checksum=%s', ('0' * 64,))
                else:
                    await conn.execute('DROP TABLE k07_preview_migrations')
                assert not await verify(conn, schema)
                assert await _core_ledger(conn) == before
            with pytest.raises(PersistenceMigrationRejected):
                async with pool.connection() as conn:
                    await migrate(conn, schema)
            async with pool.connection() as conn:
                assert await _core_ledger(conn) == before
    _run(run())


@pytest.mark.parametrize('extra', ['trigger', 'index'])
def test_unknown_base_trigger_or_index_remains_in_core_fingerprint(extra):
    async def run():
        async with _database() as (pool, schema):
            await _known_v6_extensions(pool, schema)
            async with pool.connection() as conn:
                await migrate(conn, schema)
                before = await _core_ledger(conn)
                if extra == 'trigger':
                    await conn.execute("CREATE FUNCTION unexpected_job_guard() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END; $$")
                    await conn.execute('CREATE TRIGGER unexpected_job_guard BEFORE UPDATE ON j01_jobs FOR EACH ROW EXECUTE FUNCTION unexpected_job_guard()')
                else:
                    await conn.execute('CREATE INDEX unexpected_job_index ON j01_jobs(request_id)')
                assert await verify_preview(conn)
                assert not await verify(conn, schema)
                assert await _core_ledger(conn) == before
    _run(run())


@pytest.mark.parametrize('orphan', ['table', 'function'])
def test_orphan_known_preview_object_is_rejected_even_without_a_core_table_change(orphan):
    async def run():
        async with _database() as (pool, schema), pool.connection() as conn:
            await migrate(conn, schema)
            before = await _core_ledger(conn)
            if orphan == 'table':
                await conn.execute('CREATE TABLE k07_import_previews(unexpected text)')
            else:
                await conn.execute("CREATE FUNCTION k07_confirm_preview_guard() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END; $$")
            assert not await verify(conn, schema)
            assert await _core_ledger(conn) == before
    _run(run())
