"""Owned PostgreSQL canonical-project grants, migration drift and revoke races."""
from __future__ import annotations

import asyncio
import hashlib
import os
import sys
from contextlib import asynccontextmanager
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4

import pytest
from pydantic import SecretStr

from aletheia_lab.hosted.authorization import AuthorizationDenied, PostgresAuthorization
from aletheia_lab.hosted.persistence_migration import migrate as migrate_canonical
from aletheia_lab.hosted.persistence_migration import verify as verify_canonical
from aletheia_lab.hosted.project_access import PostgresProjectAccess
from aletheia_lab.hosted.project_policy_migration import migrate, verify
from aletheia_lab.hosted.workspace_management import WorkspaceConflict, WorkspaceDenied

pytestmark = [pytest.mark.integration, pytest.mark.skipif(not os.environ.get("K04_TEST_DATABASE_URL"),
    reason="Explicit K-04 PostgreSQL test URL required")]
PROJECT = "p3-project-" + "1" * 64
SECOND = "p3-project-" + "2" * 64


def _run(coro):
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else None) as runner:
        return runner.run(coro)


def _credential(user):
    return SecretStr(hashlib.sha256(("synthetic-k04-project-" + user).encode()).hexdigest())


@asynccontextmanager
async def _database():
    import psycopg
    from psycopg import sql
    from psycopg_pool import AsyncConnectionPool

    database = os.environ["K04_TEST_DATABASE_URL"]
    schema = "k04_projects_" + uuid4().hex
    parsed = urlsplit(database)
    if parsed.scheme != "postgresql":
        raise ValueError("PostgreSQL test URL required")
    query = [(key, value) for key, value in parse_qsl(parsed.query) if key != "options"]
    query.append(("options", "-csearch_path=" + schema))
    scoped = urlunsplit(parsed._replace(query=urlencode(query)))
    async with await psycopg.AsyncConnection.connect(database, autocommit=True) as admin:
        await admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            async with AsyncConnectionPool(scoped, min_size=1, max_size=4, open=False) as pool:
                await pool.wait(timeout=10)
                async with pool.connection() as conn:
                    assert await migrate_canonical(conn, schema) == "created"
                    assert await migrate(conn, schema) == "created"
                async with pool.connection() as conn:
                    for user, _role in (("owner", "owner"), ("admin", "admin"), ("analyst", "analyst"),
                                       ("reviewer", "reviewer"), ("viewer", "viewer"), ("other", "owner")):
                        await conn.execute("INSERT INTO hosted_identity_bindings VALUES('issuer',%s,%s,'Synthetic',%s,"
                            "true,true,0,'ready',NULL,NULL)", (user, user, user + "@example.invalid"))
                        await conn.execute("INSERT INTO j01_sessions VALUES(%s,%s,'synthetic-csrf','active',"
                            "statement_timestamp(),statement_timestamp(),statement_timestamp(),"
                            "statement_timestamp()+interval '900 seconds',statement_timestamp()+interval '28000 seconds',0,0)",
                            ("ctx_" + user, user))
                        await conn.execute("INSERT INTO j01_session_credentials VALUES(%s,%s,'active',0)",
                                           (_credential(user).get_secret_value(), "ctx_" + user))
                    await conn.execute("INSERT INTO j01_workspaces VALUES('wa','Synthetic A'),('wb','Synthetic B')")
                    for user, role in (("owner", "owner"), ("admin", "admin"), ("analyst", "analyst"),
                                       ("reviewer", "reviewer"), ("viewer", "viewer"), ("other", "owner")):
                        await conn.execute("INSERT INTO j01_memberships VALUES(%s,%s,%s,true,1)",
                                           ("wb" if user == "other" else "wa", user, role))
                    for workspace, project, creator in (("wa", PROJECT, "analyst"), ("wa", SECOND, "owner"),
                                                         ("wb", PROJECT, "other")):
                        await conn.execute("INSERT INTO k05_projects(workspace_id,project_id,creator_id) VALUES(%s,%s,%s)",
                                           (workspace, project, creator))
                yield pool, schema
        finally:
            await admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


async def _authorize(pool, user, workspace, project, action):
    policy = PostgresAuthorization()
    async with pool.connection() as conn:
        auth = await policy.authorize(conn, _credential(user).get_secret_value(), workspace, action)
        await policy.authorize_project(conn, auth, workspace, project, action)
        return auth


def test_real_project_grants_roles_scope_cas_revoke_and_database_constraints():
    import psycopg

    async def scenario():
        async with _database() as (pool, _):
            manager = PostgresProjectAccess(pool)
            await _authorize(pool, "analyst", "wa", PROJECT, "analyze")
            with pytest.raises(AuthorizationDenied):
                await _authorize(pool, "analyst", "wa", PROJECT, "delete")
            for user, actions in (("analyst", ["read", "delete"]), ("reviewer", ["read", "review"]),
                                  ("viewer", ["read", "export"])):
                grant = await manager.set_grant(credential_digest=_credential("owner"), workspace_id="wa",
                    project_id=PROJECT, user_id=user, actions=actions, expected_revision=0)
                assert grant["revision"] == 1
            await _authorize(pool, "analyst", "wa", PROJECT, "delete")
            await _authorize(pool, "reviewer", "wa", PROJECT, "review")
            await _authorize(pool, "viewer", "wa", PROJECT, "export")
            for user, action in (("reviewer", "analyze"), ("viewer", "review")):
                with pytest.raises(AuthorizationDenied):
                    await _authorize(pool, user, "wa", PROJECT, action)
            for actor in ("analyst", "reviewer", "viewer", "other"):
                with pytest.raises(WorkspaceDenied):
                    await manager.set_grant(credential_digest=_credential(actor), workspace_id="wa", project_id=PROJECT,
                        user_id="viewer", actions=[], expected_revision=1)
            with pytest.raises(WorkspaceConflict):
                await manager.set_grant(credential_digest=_credential("admin"), workspace_id="wa", project_id=PROJECT,
                                       user_id="viewer", actions=[], expected_revision=0)
            assert (await manager.set_grant(credential_digest=_credential("admin"), workspace_id="wa", project_id=PROJECT,
                user_id="viewer", actions=[], expected_revision=1))["revision"] == 2
            with pytest.raises(AuthorizationDenied):
                await _authorize(pool, "viewer", "wa", PROJECT, "read")
            with pytest.raises(AuthorizationDenied):
                await _authorize(pool, "other", "wa", PROJECT, "read")
            await _authorize(pool, "other", "wb", PROJECT, "read")
            with pytest.raises(psycopg.errors.ForeignKeyViolation):
                async with pool.connection() as conn:
                    await conn.execute("INSERT INTO j01_project_grants VALUES('wa',%s,'other',ARRAY['read'],0)", (PROJECT,))
            with pytest.raises(psycopg.errors.CheckViolation):
                async with pool.connection() as conn:
                    await conn.execute("UPDATE j01_project_grants SET actions=ARRAY['read'] WHERE workspace_id='wa' "
                                       "AND project_id=%s AND user_id='viewer'", (PROJECT,))
    _run(scenario())


def test_project_grant_two_connections_have_one_cas_winner_and_captured_grant_is_revalidated():
    async def scenario():
        async with _database() as (pool, _):
            manager = PostgresProjectAccess(pool)
            start = asyncio.Event()

            async def change(actions):
                await start.wait()
                try:
                    return await manager.set_grant(credential_digest=_credential("owner"), workspace_id="wa",
                        project_id=PROJECT, user_id="viewer", actions=actions, expected_revision=0)
                except WorkspaceConflict:
                    return None

            first, second = asyncio.create_task(change(["read"])), asyncio.create_task(change(["read", "export"]))
            start.set()
            outcomes = await asyncio.gather(first, second)
            assert sum(value is not None for value in outcomes) == 1
            policy = PostgresAuthorization()
            captured = await _authorize(pool, "viewer", "wa", PROJECT, "read")
            await manager.set_grant(credential_digest=_credential("owner"), workspace_id="wa", project_id=PROJECT,
                                   user_id="viewer", actions=[], expected_revision=1)
            async with pool.connection() as conn:
                with pytest.raises(AuthorizationDenied):
                    await policy.authorize_project(conn, captured, "wa", PROJECT, "read")
            async with pool.connection() as conn:
                assert await (await conn.execute("SELECT revision,actions FROM j01_project_grants WHERE workspace_id='wa' "
                    "AND project_id=%s AND user_id='viewer'", (PROJECT,))).fetchone() == (2, [])
    _run(scenario())


def test_extension_keeps_canonical_signature_detects_drift_and_tombstone_denies_owner(tmp_path):
    from aletheia_lab.hosted.object_store import FilesystemObjectStore
    from aletheia_lab.hosted.postgres_objects import PostgresObjects
    from aletheia_lab.product.lifecycle import build_product_lifecycle_record
    from aletheia_lab.project.persistence import _json_bytes

    async def scenario():
        async with _database() as (pool, schema):
            async with pool.connection() as conn:
                assert await migrate(conn, schema) == "unchanged"
                assert await verify(conn, schema)
                assert await verify_canonical(conn, schema)
            with pytest.raises(RuntimeError, match="rollback synthetic drift"):
                async with pool.connection() as conn:
                    await conn.execute("ALTER TABLE j01_project_grants DISABLE TRIGGER j01_project_grants_revision_guard")
                    assert await verify(conn, schema) is False
                    raise RuntimeError("rollback synthetic drift")
            async with pool.connection() as conn:
                assert await verify(conn, schema)
            objects = PostgresObjects(pool, FilesystemObjectStore(tmp_path / "objects"))
            tombstone = build_product_lifecycle_record(record_kind="deletion", project_id=PROJECT,
                                                       payload={"status": "deleted"})
            staged = await objects.stage("wa", _json_bytes(tombstone.model_dump(mode="json")),
                                         media_type="application/json", schema_version=tombstone.schema_version)
            async with pool.connection() as conn:
                await conn.execute("SELECT project_id FROM k05_projects WHERE workspace_id='wa' AND project_id=%s FOR UPDATE", (PROJECT,))
                await objects.ready(conn, "wa", staged)
                await conn.execute("INSERT INTO k05_records(workspace_id,record_id,project_id,record_type,schema_version,"
                    "canonical_sha256,object_sha256) VALUES('wa',%s,%s,'deletion',%s,%s,%s)",
                    (tombstone.record_id, PROJECT, tombstone.schema_version, tombstone.canonical_sha256(), staged.descriptor.sha256))
                await objects.finish(conn, "wa", staged)
            with pytest.raises(AuthorizationDenied):
                await _authorize(pool, "owner", "wa", PROJECT, "read")
            with pytest.raises(AuthorizationDenied):
                await PostgresProjectAccess(pool).set_grant(credential_digest=_credential("owner"), workspace_id="wa",
                    project_id=PROJECT, user_id="viewer", actions=["read"], expected_revision=0)
            await _authorize(pool, "other", "wb", PROJECT, "read")
    _run(scenario())
