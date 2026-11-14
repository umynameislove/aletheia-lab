"""K-05 schema checks using an explicit URL and UUID-owned schemas only."""
from __future__ import annotations

import asyncio
import hashlib
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4

import pytest

from aletheia_lab.hosted.persistence_migration import (
    PersistenceMigrationRejected,
    migrate,
    verify,
)

ROOT = Path(__file__).resolve().parents[2]
pytestmark = [pytest.mark.integration, pytest.mark.skipif(not os.environ.get("K05_TEST_DATABASE_URL"),
    reason="Explicit K-05 PostgreSQL test URL required")]


def _run(coro):
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else None) as runner:
        return runner.run(coro)


def _scoped(database, schema):
    value = urlsplit(database)
    assert value.scheme == "postgresql"
    pairs = [(k, v) for k, v in parse_qsl(value.query) if k != "options"]
    pairs.append(("options", "-csearch_path=" + schema))
    return urlunsplit(value._replace(query=urlencode(pairs)))


@asynccontextmanager
async def _database(*, legacy=False):
    import psycopg
    from psycopg import sql
    from psycopg_pool import AsyncConnectionPool

    schema = "k05_schema_" + uuid4().hex
    database = os.environ["K05_TEST_DATABASE_URL"]
    async with await psycopg.AsyncConnection.connect(database, autocommit=True) as admin:
        await admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            await admin.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
            if legacy:
                for name in ("session", "source", "jobs", "login-claims", "fresh-login"):
                    suffix = "j01-experimental-" + name + (".sql" if name == "login-claims" else "-schema.sql")
                    await admin.execute((ROOT / "docs" / suffix).read_text(encoding="utf-8"))
            async with AsyncConnectionPool(_scoped(database, schema), min_size=1, max_size=4, open=False) as pool:
                await pool.wait(timeout=10)
                yield pool, schema
        finally:
            assert schema.startswith("k05_schema_") and len(schema) == 43
            await admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


async def _seed(pool):
    async with pool.connection() as conn:
        for scope in ("a", "b"):
            await conn.execute("INSERT INTO hosted_identity_bindings VALUES(%s,%s,%s,'Synthetic',%s,true,true,0,'ready',NULL,NULL)",
                ("https://issuer.example.invalid", scope, "user_" + scope, scope + "@example.invalid"))
            await conn.execute("INSERT INTO j01_workspaces VALUES(%s,'Synthetic')", (scope,))
            await conn.execute("INSERT INTO j01_memberships VALUES(%s,%s,'owner',true,0)", (scope, "user_" + scope))
            await conn.execute("INSERT INTO k05_projects(workspace_id,project_id,creator_id) VALUES(%s,%s,%s)",
                (scope, "p3-project-" + scope * 64, "user_" + scope))
            await conn.execute("INSERT INTO k05_objects(workspace_id,sha256,byte_size,media_type) VALUES(%s,%s,2,'application/json')",
                (scope, "e" * 64))


async def _record(conn, scope, *, record_id=None, project=None):
    await conn.execute("INSERT INTO k05_records(workspace_id,record_id,project_id,record_type,schema_version,"
        "canonical_sha256,object_sha256) VALUES(%s,%s,%s,'project_bundle','p3-project-bundle/v1',%s,%s)",
        (scope, record_id or "p3-bundle-" + "c" * 64, project or "p3-project-" + scope * 64, "d" * 64, "e" * 64))


def test_create_rerun_and_exact_legacy_adoption():
    async def run():
        for legacy in (False, True):
            async with _database(legacy=legacy) as (pool, schema):
                async with pool.connection() as conn:
                    assert await migrate(conn, schema) == ("adopted" if legacy else "created")
                    assert await verify(conn, schema)
                await _seed(pool)
                async with pool.connection() as conn:
                    assert await migrate(conn, schema) == "unchanged"
                    assert await verify(conn, schema)
                    assert await (await conn.execute("SELECT count(*) FROM hosted_identity_bindings")).fetchone() == (2,)
    _run(run())


def test_same_canonical_id_across_tenants_and_parent_scope_is_enforced():
    import psycopg

    async def run():
        async with _database() as (pool, schema):
            async with pool.connection() as conn:
                await migrate(conn, schema)
            await _seed(pool)
            async with pool.connection() as conn:
                await _record(conn, "a")
                await _record(conn, "b")
            with pytest.raises(psycopg.errors.ForeignKeyViolation):
                async with pool.connection() as conn:
                    await _record(conn, "a", record_id="p3-bundle-" + "f" * 64, project="p3-project-" + "b" * 64)
            with pytest.raises(psycopg.errors.ForeignKeyViolation):
                async with pool.connection() as conn:
                    await conn.execute("INSERT INTO k05_record_parents VALUES('a',%s,%s,'source',%s)",
                        ("p3-project-" + "a" * 64, "p3-bundle-" + "c" * 64, "p3-bundle-" + "f" * 64))
            async with pool.connection() as conn:
                assert await (await conn.execute("SELECT count(*) FROM k05_records")).fetchone() == (2,)
    _run(run())


def test_ready_reference_and_immutable_canonical_rows():
    import psycopg

    async def run():
        async with _database() as (pool, schema):
            async with pool.connection() as conn:
                await migrate(conn, schema)
            await _seed(pool)
            with pytest.raises(psycopg.errors.ForeignKeyViolation):
                async with pool.connection() as conn:
                    await conn.execute("INSERT INTO k05_records(workspace_id,record_id,project_id,record_type,schema_version,"
                        "canonical_sha256,object_sha256) VALUES('a',%s,%s,'project_bundle','v1',%s,%s)",
                        ("p3-bundle-" + "c" * 64, "p3-project-" + "a" * 64, "d" * 64, "0" * 64))
            async with pool.connection() as conn:
                await _record(conn, "a")
            with pytest.raises(psycopg.errors.CheckViolation):
                async with pool.connection() as conn:
                    await conn.execute("UPDATE k05_records SET canonical_sha256=%s WHERE workspace_id='a'", ("f" * 64,))
    _run(run())


def test_legacy_shape_drift_and_unknown_ledger_reject_without_side_effect():
    async def run():
        async with _database(legacy=True) as (pool, schema):
            async with pool.connection() as conn:
                await conn.execute("ALTER TABLE j01_sessions ALTER COLUMN generation DROP NOT NULL")
            with pytest.raises(PersistenceMigrationRejected):
                async with pool.connection() as conn:
                    await migrate(conn, schema)
            async with pool.connection() as conn:
                assert await (await conn.execute("SELECT to_regclass('k05_projects')")).fetchone() == (None,)
        async with _database() as (pool, schema):
            async with pool.connection() as conn:
                await migrate(conn, schema)
                await conn.execute("UPDATE k05_persistence_migrations SET checksum=%s WHERE version=2", ("0" * 64,))
            with pytest.raises(PersistenceMigrationRejected):
                async with pool.connection() as conn:
                    await migrate(conn, schema)
    _run(run())


def test_installed_constraint_or_index_drift_fails_verification():
    async def run():
        async with _database() as (pool, schema):
            async with pool.connection() as conn:
                await migrate(conn, schema)
                await conn.execute("DROP INDEX k05_records_page")
            async with pool.connection() as conn:
                assert not await verify(conn, schema)
            with pytest.raises(PersistenceMigrationRejected):
                async with pool.connection() as conn:
                    await migrate(conn, schema)
    _run(run())


def test_migration_rollback_and_cancel_preserve_existing_legacy_rows():
    async def run():
        async with _database(legacy=True) as (pool, schema):
            async with pool.connection() as conn:
                await conn.execute("INSERT INTO hosted_identity_bindings VALUES('https://issuer.example.invalid','subject',"
                    "'user','Synthetic','x@example.invalid',true,true,0,'needs_workspace',NULL,NULL)")
            with pytest.raises(asyncio.CancelledError):
                async with pool.connection() as conn:
                    await migrate(conn, schema)
                    raise asyncio.CancelledError()
            async with pool.connection() as conn:
                assert await (await conn.execute("SELECT to_regclass('k05_projects')")).fetchone() == (None,)
                assert await (await conn.execute("SELECT count(*) FROM hosted_identity_bindings")).fetchone() == (1,)
                assert await migrate(conn, schema) == "adopted"
    _run(run())


def test_job_parent_tenant_scope_rejects_direct_sql_bypass():
    import psycopg

    async def run():
        async with _database() as (pool, schema):
            async with pool.connection() as conn:
                await migrate(conn, schema)
            await _seed(pool)
            async with pool.connection() as conn:
                await conn.execute("INSERT INTO j01_sources(source_id,workspace_id,creator_id,request_key_hash,sha256,payload,manifest) "
                    "VALUES('source_b','b','user_b',%s,%s,%s,'{}')", ("0" * 64, hashlib.sha256(b"{}").hexdigest(), b"{}"))
            with pytest.raises(psycopg.errors.ForeignKeyViolation):
                async with pool.connection() as conn:
                    await conn.execute("INSERT INTO j01_jobs(job_id,workspace_id,creator_id,credential_digest,operation,key_hash,"
                        "request_hash,request,request_id,state) VALUES('job','a','user_a',%s,'preview',%s,%s,"
                        "'{\"source_id\":\"source_b\"}','req','queued')", ("1" * 64, "2" * 64, "3" * 64))
            async with pool.connection() as conn:
                assert await (await conn.execute("SELECT count(*) FROM j01_jobs")).fetchone() == (0,)
    _run(run())


def test_busy_legacy_writer_bounds_ddl_wait_and_rolls_back_adoption():
    import psycopg

    async def run():
        async with _database(legacy=True) as (pool, schema):
            async with pool.connection() as writer:
                await writer.execute('LOCK TABLE hosted_identity_bindings IN ROW EXCLUSIVE MODE')
                with pytest.raises(psycopg.errors.LockNotAvailable):
                    async with pool.connection() as migration:
                        await migration.execute("SET LOCAL lock_timeout='100ms'")
                        await migrate(migration, schema)
                async with pool.connection() as reader:
                    assert await (await reader.execute('SELECT count(*) FROM hosted_identity_bindings')).fetchone() == (0,)
                    assert await (await reader.execute("SELECT to_regclass('k05_projects')")).fetchone() == (None,)
            async with pool.connection() as conn:
                assert await migrate(conn, schema) == 'adopted'
                assert await verify(conn, schema)
    _run(run())


def test_artifact_reference_retention_fences_gc_until_canonical_record_is_removed():
    import psycopg

    async def run():
        async with _database() as (pool, schema):
            async with pool.connection() as conn:
                await migrate(conn, schema)
            await _seed(pool)
            identity = 'p3-bundle-' + 'c' * 64
            async with pool.connection() as conn:
                await _record(conn, 'a')
                for digest in ('f' * 64, '0' * 64):
                    await conn.execute("INSERT INTO k05_objects(workspace_id,sha256,byte_size,media_type) VALUES('a',%s,2,'text/plain')", (digest,))
                await conn.execute("INSERT INTO k05_object_references VALUES('a',%s,%s)", (identity, 'f' * 64))
            with pytest.raises(psycopg.errors.CheckViolation):
                async with pool.connection() as conn:
                    await conn.execute("DELETE FROM k05_object_references WHERE workspace_id='a'")
            with pytest.raises(psycopg.errors.CheckViolation):
                async with pool.connection() as conn:
                    await conn.execute("UPDATE k05_object_references SET sha256=%s WHERE workspace_id='a'", ('0' * 64,))
            async with pool.connection() as conn:
                assert await (await conn.execute("SELECT sha256 FROM k05_object_references WHERE workspace_id='a'")).fetchone() == ('f' * 64,)
                await conn.execute("DELETE FROM k05_object_references WHERE workspace_id='a'")
                await conn.execute("INSERT INTO k05_object_references VALUES('a',%s,%s)", (identity, 'f' * 64))
            async with pool.connection() as conn:
                await conn.execute("DELETE FROM k05_object_references WHERE workspace_id='a'")
                await conn.execute("DELETE FROM k05_records WHERE workspace_id='a'")
            async with pool.connection() as conn:
                assert await (await conn.execute("SELECT count(*) FROM k05_object_references WHERE workspace_id='a'")).fetchone() == (0,)
    _run(run())
