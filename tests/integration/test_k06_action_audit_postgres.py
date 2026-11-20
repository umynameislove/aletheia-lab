"""Actual commit, rollback, cancellation and immutable safe action metadata."""
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

from aletheia_lab.hosted.action_audit import record_action, request_context
from aletheia_lab.hosted.action_audit_migration import ActionAuditMigrationRejected, migrate, verify
from aletheia_lab.hosted.action_audit_migration import _signature as audit_signature
from aletheia_lab.hosted.persistence_migration import migrate as migrate_canonical
from aletheia_lab.hosted.persistence_migration import verify as verify_canonical

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

    schema = "k06_audit_" + uuid4().hex
    database = os.environ["K06_TEST_DATABASE_URL"]
    parsed = urlsplit(database)
    assert parsed.scheme == "postgresql"
    query = [(key, value) for key, value in parse_qsl(parsed.query) if key != "options"]
    query.append(("options", "-csearch_path=" + schema))
    scoped = urlunsplit(parsed._replace(query=urlencode(query)))
    async with await psycopg.AsyncConnection.connect(database, autocommit=True) as admin:
        await admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            async with AsyncConnectionPool(scoped, min_size=1, max_size=3, open=False) as pool:
                await pool.wait(timeout=10)
                async with pool.connection() as conn:
                    assert await migrate_canonical(conn, schema) == "created"
                    for name in ("a", "b"):
                        await conn.execute("INSERT INTO hosted_identity_bindings VALUES('synthetic-issuer',%s,%s,"
                            "'Synthetic',%s,true,true,0,'ready',NULL,NULL)",
                            (name, "user_" + name, name + "@example.invalid"))
                        await conn.execute("INSERT INTO j01_workspaces VALUES(%s,'Synthetic')", ("w" + name,))
                        await conn.execute("INSERT INTO j01_memberships VALUES(%s,%s,'owner',true,1)",
                                           ("w" + name, "user_" + name))
                    await conn.execute("CREATE TABLE k06_probe_effects(effect_id text PRIMARY KEY)")
                yield pool, schema
        finally:
            assert schema.startswith("k06_audit_") and len(schema) == 42
            await admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


async def _record(conn, *, effect="effect_a", actor="user_a", workspace="wa", operation="op_a"):
    await conn.execute("INSERT INTO k06_probe_effects VALUES(%s)", (effect,))
    return await record_action(conn, actor_id=actor, workspace_id=workspace, action="source.accept",
                               effect_id=effect, operation_id=operation, revision=1)


async def _counts(pool):
    async with pool.connection() as conn:
        return await (await conn.execute("SELECT (SELECT count(*) FROM k06_probe_effects),"
            "(SELECT count(*) FROM k06_action_audit)")).fetchone()


def test_committed_audit_and_effect_are_visible_together_and_ack_loss_can_be_investigated():
    async def scenario():
        async with _database() as (pool, schema):
            async with pool.connection() as conn:
                assert not await verify(conn, schema)
                assert await migrate(conn, schema) == "created"
            with request_context("req_synthetic_lost_ack"):
                async with pool.connection() as conn:
                    audit = await _record(conn)
                    assert await _counts(pool) == (0, 0)
            # The originating response is discarded; a separate connection sees committed truth.
            assert await _counts(pool) == (1, 1)
            async with pool.connection() as conn:
                row = await (await conn.execute("SELECT audit_id,request_id,operation_id,actor_id,workspace_id,"
                    "action,effect_id,revision,job_id FROM k06_action_audit WHERE operation_id='op_a'")).fetchone()
                assert row == (audit, "req_synthetic_lost_ack", "op_a", "user_a", "wa", "source.accept", "effect_a", 1, None)
                assert await migrate(conn, schema) == "unchanged" and await verify(conn, schema)
                assert await verify_canonical(conn, schema)
    _run(scenario())


def test_rollback_and_external_cancellation_leave_neither_effect_nor_false_success():
    async def scenario():
        async with _database() as (pool, schema):
            async with pool.connection() as conn:
                await migrate(conn, schema)
            with pytest.raises(ValueError, match="synthetic rollback"):
                async with pool.connection() as conn:
                    await _record(conn)
                    raise ValueError("synthetic rollback")
            assert await _counts(pool) == (0, 0)
            entered, release = asyncio.Event(), asyncio.Event()

            async def writer():
                async with pool.connection() as conn:
                    await _record(conn)
                    entered.set()
                    await release.wait()

            task = asyncio.create_task(writer())
            await asyncio.wait_for(entered.wait(), 10)
            assert await _counts(pool) == (0, 0)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert await _counts(pool) == (0, 0)
            async with pool.connection() as conn:
                await _record(conn)
            assert await _counts(pool) == (1, 1)
    _run(scenario())


def test_duplicate_audit_scope_and_foreign_tenant_cannot_create_extra_effect():
    import psycopg

    async def scenario():
        async with _database() as (pool, schema):
            async with pool.connection() as conn:
                await migrate(conn, schema)
                await _record(conn)
            with pytest.raises(psycopg.errors.UniqueViolation):
                async with pool.connection() as conn:
                    await record_action(conn, actor_id="user_a", workspace_id="wa", action="source.accept",
                        effect_id="effect_a", operation_id="op_a", revision=1)
            with pytest.raises(psycopg.errors.ForeignKeyViolation):
                async with pool.connection() as conn:
                    await _record(conn, effect="foreign_effect", actor="user_b", operation="foreign_op")
            assert await _counts(pool) == (1, 1)
            async with pool.connection() as conn:
                cursor = await conn.execute("SELECT column_name FROM information_schema.columns WHERE table_schema=%s "
                    "AND table_name='k06_action_audit' ORDER BY ordinal_position", (schema,))
                columns = []
                while (row := await cursor.fetchone()) is not None:
                    columns.append(row[0])
                assert columns == ["audit_id", "request_id", "operation_id", "actor_id", "workspace_id", "action",
                                   "effect_id", "revision", "job_id", "recorded_at", "parent_id", "subject_id"]
    _run(scenario())


@pytest.mark.parametrize("query", ["UPDATE k06_action_audit SET revision=99", "DELETE FROM k06_action_audit",
                                  "TRUNCATE k06_action_audit"])
def test_committed_audit_is_immutable_and_failed_write_releases_connection(query):
    import psycopg

    async def scenario():
        async with _database() as (pool, schema):
            async with pool.connection() as conn:
                await migrate(conn, schema)
                await _record(conn)
            with pytest.raises(psycopg.errors.CheckViolation):
                async with pool.connection() as conn:
                    await conn.execute(query)
            assert await _counts(pool) == (1, 1)
            async with pool.connection() as conn:
                assert await verify(conn, schema)
    _run(scenario())


@pytest.mark.parametrize("query", [
    "ALTER TABLE k06_action_audit ADD COLUMN payload text",
    "ALTER TABLE k06_action_audit ALTER COLUMN action DROP NOT NULL",
    "ALTER TABLE k06_action_audit ALTER COLUMN recorded_at DROP DEFAULT",
    "ALTER TABLE k06_action_audit DROP CONSTRAINT k06_action_audit_revision_check",
    "DROP INDEX k06_action_audit_operation",
    "ALTER TABLE k06_action_audit DISABLE TRIGGER k06_action_audit_immutable",
    "CREATE OR REPLACE FUNCTION k06_action_audit_immutable() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END $$",
    "ALTER TABLE k06_action_audit DROP CONSTRAINT k06_audit_parent_id_v2",
    "ALTER TABLE k06_action_audit ALTER COLUMN subject_id TYPE varchar(128)",
])
def test_schema_function_trigger_index_constraint_drift_fails_closed_without_repair(query):
    async def scenario():
        async with _database() as (pool, schema):
            async with pool.connection() as conn:
                await migrate(conn, schema)
            async with pool.connection() as conn:
                await conn.execute(query)
            async with pool.connection() as conn:
                assert not await verify(conn, schema)
            with pytest.raises(ActionAuditMigrationRejected):
                async with pool.connection() as conn:
                    await migrate(conn, schema)
    _run(scenario())


def test_failed_initial_migration_rolls_back_and_collision_is_not_overwritten():
    async def scenario():
        async with _database() as (pool, schema):
            with pytest.raises(ValueError, match="synthetic rollback"):
                async with pool.connection() as conn:
                    assert await migrate(conn, schema) == "created"
                    raise ValueError("synthetic rollback")
            async with pool.connection() as conn:
                assert not await verify(conn, schema)
                await conn.execute("CREATE TABLE k06_action_audit(synthetic text)")
            with pytest.raises(ActionAuditMigrationRejected):
                async with pool.connection() as conn:
                    await migrate(conn, schema)
            async with pool.connection() as conn:
                assert await (await conn.execute("SELECT synthetic FROM k06_action_audit LIMIT 0")).fetchone() is None
    _run(scenario())


def test_frozen_v1_upgrades_additively_and_upgrade_rollback_preserves_existing_audit():
    import aletheia_lab.hosted.action_audit_migration as migration

    async def scenario():
        async with _database() as (pool, schema):
            ddl = Path(migration.__file__).with_name("action_audit_schema.sql").read_bytes()
            checksum = hashlib.sha256(ddl).hexdigest()
            assert checksum == "3f89544e36d382c4e50333fb68b9741c83dae0c8e6198ee5deb21ec1e25d72cd"
            async with pool.connection() as conn:
                await conn.execute(ddl.decode("utf-8"))
                await conn.execute("CREATE TABLE k06_action_audit_migrations(version integer PRIMARY KEY CHECK(version>0),"
                    "checksum text NOT NULL CHECK(checksum~'^[0-9a-f]{64}$'),"
                    "signature text NOT NULL CHECK(signature~'^[0-9a-f]{64}$'),"
                    "applied_at timestamptz NOT NULL DEFAULT clock_timestamp())")
                original = (1, checksum, await audit_signature(conn, 1))
                await conn.execute("INSERT INTO k06_action_audit_migrations(version,checksum,signature) VALUES(%s,%s,%s)", original)
                await conn.execute("INSERT INTO k06_action_audit(audit_id,operation_id,actor_id,workspace_id,action,effect_id) "
                    "VALUES(%s,'legacy_op','user_a','wa','fixture.legacy','legacy_effect')", ("audit_" + "a" * 32,))
                assert not await verify(conn, schema)
            with pytest.raises(ValueError, match="synthetic rollback"):
                async with pool.connection() as conn:
                    assert await migrate(conn, schema) == "upgraded"
                    raise ValueError("synthetic rollback")
            async with pool.connection() as conn:
                assert await (await conn.execute("SELECT count(*) FROM k06_action_audit_migrations")).fetchone() == (1,)
                assert await migrate(conn, schema) == "upgraded"
            async with pool.connection() as conn:
                assert await verify(conn, schema) and await migrate(conn, schema) == "unchanged"
                assert await (await conn.execute("SELECT version,checksum,signature FROM k06_action_audit_migrations "
                    "WHERE version=1")).fetchone() == original
                assert await (await conn.execute("SELECT effect_id,parent_id,subject_id FROM k06_action_audit")).fetchone() == (
                    "legacy_effect", None, None)
                await record_action(conn, actor_id="user_a", workspace_id="wa", action="review.put", effect_id="annotation_a",
                    parent_id="result_a", subject_id="user_b", revision=2)
                assert await (await conn.execute("SELECT parent_id,subject_id FROM k06_action_audit "
                    "WHERE effect_id='annotation_a'")).fetchone() == ("result_a", "user_b")
    _run(scenario())
