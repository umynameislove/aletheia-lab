"""Opt-in K-04 checks; every test owns and removes only its random schema."""

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
from pydantic import SecretStr

ROOT = Path(__file__).resolve().parents[2]
pytestmark = [pytest.mark.integration, pytest.mark.skipif(not os.environ.get("K04_TEST_DATABASE_URL"),
                                                          reason="Explicit K-04 PostgreSQL test URL required")]


def _run(coro):
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else None) as runner:
        return runner.run(coro)


def _scoped(url, schema):
    parsed = urlsplit(url)
    if parsed.scheme != "postgresql":
        raise ValueError("PostgreSQL test URL required")
    query = [(key, value) for key, value in parse_qsl(parsed.query) if key != "options"]
    query.append(("options", "-csearch_path=" + schema))
    return urlunsplit(parsed._replace(query=urlencode(query)))


@asynccontextmanager
async def _database(*, migrate_schema=True, maximum=4):
    import psycopg
    from psycopg import sql
    from psycopg_pool import AsyncConnectionPool

    from aletheia_lab.hosted.workspace_migration import migrate

    schema = "k04_workspaces_" + uuid4().hex
    database = os.environ["K04_TEST_DATABASE_URL"]
    async with await psycopg.AsyncConnection.connect(database, autocommit=True) as admin:
        await admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            await admin.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
            for name in ("session", "source", "jobs"):
                await admin.execute((ROOT / "docs" / f"j01-experimental-{name}-schema.sql").read_text(encoding="utf-8"))
            async with AsyncConnectionPool(_scoped(database, schema), min_size=1, max_size=maximum, open=False) as pool:
                await pool.wait(timeout=10)
                if migrate_schema:
                    async with pool.connection() as conn:
                        assert await migrate(conn, schema) == "adopted"
                yield pool, schema
        finally:
            await admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))


async def _identity(pool, user):
    digest = hashlib.sha256(("synthetic-session-" + user).encode()).hexdigest()
    async with pool.connection() as conn:
        await conn.execute("INSERT INTO hosted_identity_bindings VALUES "
            "('https://issuer.example.invalid',%s,%s,'Synthetic',%s,true,true,0,'needs_workspace',NULL,NULL)",
            ("subject-" + user, user, user + "@example.invalid"))
        await conn.execute("INSERT INTO j01_sessions VALUES (%s,%s,'synthetic-csrf','active',"
            "statement_timestamp()-interval '60 seconds',statement_timestamp()-interval '30 seconds',"
            "statement_timestamp()-interval '30 seconds',statement_timestamp()+interval '900 seconds',"
            "statement_timestamp()+interval '28000 seconds',0,0)", ("ctx-" + user, user))
        await conn.execute("INSERT INTO j01_session_credentials VALUES (%s,%s,'active',0)", (digest, "ctx-" + user))
    return SecretStr(digest)


async def _member(manager, owner, workspace, user, role):
    from aletheia_lab.hosted.workspace_management import WorkspaceManagement

    assert isinstance(manager, WorkspaceManagement)
    invitation = await manager.invite(credential_digest=owner, workspace_id=workspace, user_id=user,
                                      role=role, expires_in_seconds=600)
    return invitation


def test_real_onboarding_single_winner_owner_transfer_and_stale_revision():
    from aletheia_lab.hosted.workspace_management import (
        WorkspaceConflict,
        WorkspaceDenied,
        WorkspaceManagement,
    )
    from aletheia_lab.hosted_auth.postgres_sessions import PostgresSessionStore

    async def run():
        async with _database() as (pool, _):
            a, b = await _identity(pool, "owner"), await _identity(pool, "member")
            manager = WorkspaceManagement(pool)
            start = asyncio.Event()

            async def create():
                await start.wait()
                try:
                    return await manager.create(credential_digest=a, name="Synthetic workspace")
                except WorkspaceDenied:
                    return None

            tasks = [asyncio.create_task(create()), asyncio.create_task(create())]
            start.set()
            results = await asyncio.gather(*tasks)
            assert sum(value is not None for value in results) == 1
            created = next(value for value in results if value is not None)
            workspace = created["workspace_id"]
            snapshot = await PostgresSessionStore(pool).inspect(a)
            assert snapshot.onboarding == "ready" and snapshot.last_workspace_id == workspace
            with pytest.raises(WorkspaceDenied):
                await manager.mutate_member(credential_digest=a, workspace_id=workspace, user_id="owner",
                                             expected_revision=1, active=False)
            invitation = await _member(manager, a, workspace, "member", "admin")
            accepted = await manager.accept_invite(credential_digest=b, token=SecretStr(invitation["token"]))
            assert accepted["membership"] == {"role": "admin", "revision": 1}
            with pytest.raises(WorkspaceDenied):
                await manager.mutate_member(credential_digest=b, workspace_id=workspace, user_id="owner",
                                             expected_revision=1, active=False)
            with pytest.raises(WorkspaceDenied):
                await manager.mutate_member(credential_digest=b, workspace_id=workspace, user_id="member",
                                             expected_revision=1, role="owner")
            with pytest.raises(WorkspaceConflict):
                await manager.transfer_owner(credential_digest=a, workspace_id=workspace, user_id="member",
                                              expected_revision=0)
            transferred = await manager.transfer_owner(credential_digest=a, workspace_id=workspace,
                                                        user_id="member", expected_revision=1)
            assert transferred["role"] == "owner" and transferred["revision"] == 2
            revoked = await manager.mutate_member(credential_digest=b, workspace_id=workspace,
                                                  user_id="owner", expected_revision=2, active=False)
            assert revoked["revision"] == 3 and revoked["active"] is False
            snapshot = await PostgresSessionStore(pool).inspect(a)
            assert snapshot.onboarding == "needs_workspace" and snapshot.last_workspace_id is None
            with pytest.raises(WorkspaceDenied):
                await manager.list_members(credential_digest=a, workspace_id=workspace)
            with pytest.raises(WorkspaceConflict):
                await manager.mutate_member(credential_digest=b, workspace_id=workspace, user_id="owner",
                                             expected_revision=2, active=True)
            async with pool.connection() as conn:
                row = await (await conn.execute("SELECT active,revision FROM j01_memberships "
                                               "WHERE workspace_id=%s AND user_id='owner'", (workspace,))).fetchone()
                assert row == (False, 3)

    _run(run())


def test_real_invitation_binding_expiry_revoke_replay_and_current_issuer():
    from aletheia_lab.hosted.workspace_management import WorkspaceDenied, WorkspaceManagement

    async def run():
        async with _database() as (pool, _):
            owner, target, other = [await _identity(pool, user) for user in ("owner", "target", "other")]
            manager = WorkspaceManagement(pool)
            workspace = (await manager.create(credential_digest=owner, name="Synthetic"))["workspace_id"]
            invite = await _member(manager, owner, workspace, "target", "reviewer")
            with pytest.raises(WorkspaceDenied):
                await manager.accept_invite(credential_digest=other, token=SecretStr(invite["token"]))
            outcomes = await asyncio.gather(*(manager.accept_invite(credential_digest=target,
                token=SecretStr(invite["token"])) for _ in range(2)), return_exceptions=True)
            assert sum(isinstance(value, dict) for value in outcomes) == 1
            assert sum(isinstance(value, WorkspaceDenied) for value in outcomes) == 1
            invite = await _member(manager, owner, workspace, "other", "viewer")
            await manager.revoke_invite(credential_digest=owner, workspace_id=workspace,
                                         invitation_id=invite["invitation_id"])
            with pytest.raises(WorkspaceDenied):
                await manager.accept_invite(credential_digest=other, token=SecretStr(invite["token"]))
            invite = await _member(manager, owner, workspace, "other", "viewer")
            async with pool.connection() as conn:
                await conn.execute("UPDATE j01_invitations SET expires_at=clock_timestamp()-interval '1 second' "
                                   "WHERE invitation_id=%s", (invite["invitation_id"],))
            with pytest.raises(WorkspaceDenied):
                await manager.accept_invite(credential_digest=other, token=SecretStr(invite["token"]))
            invite = await _member(manager, owner, workspace, "other", "viewer")
            await manager.transfer_owner(credential_digest=owner, workspace_id=workspace,
                                          user_id="target", expected_revision=1)
            with pytest.raises(WorkspaceDenied):
                await manager.accept_invite(credential_digest=other, token=SecretStr(invite["token"]))
            with pytest.raises(WorkspaceDenied):
                await manager.list_members(credential_digest=other, workspace_id=workspace)

    _run(run())


@pytest.mark.parametrize("isolation", ["READ COMMITTED", "REPEATABLE READ", "SERIALIZABLE"])
def test_real_raw_sql_concurrent_last_owner_writes_cannot_commit_both(isolation):
    import psycopg

    from aletheia_lab.hosted.workspace_management import WorkspaceManagement

    async def run():
        async with _database() as (pool, _):
            owner, second = await _identity(pool, "owner"), await _identity(pool, "second")
            manager = WorkspaceManagement(pool)
            workspace = (await manager.create(credential_digest=owner, name="Synthetic"))["workspace_id"]
            invite = await _member(manager, owner, workspace, "second", "admin")
            await manager.accept_invite(credential_digest=second, token=SecretStr(invite["token"]))
            async with pool.connection() as conn:
                await conn.execute("UPDATE j01_memberships SET role='owner',revision=revision+1 "
                                   "WHERE workspace_id=%s AND user_id='second'", (workspace,))
            barrier = asyncio.Barrier(2)

            async def remove(user):
                try:
                    async with pool.connection() as conn:
                        await conn.execute("SET TRANSACTION ISOLATION LEVEL " + isolation)
                        await conn.execute("UPDATE j01_memberships SET active=false,revision=revision+1 "
                                           "WHERE workspace_id=%s AND user_id=%s", (workspace, user))
                        await asyncio.wait_for(barrier.wait(), 5)
                    return True
                except psycopg.Error as error:
                    assert error.sqlstate in ("23514", "40P01", "40001")
                    return False

            outcomes = await asyncio.wait_for(asyncio.gather(remove("owner"), remove("second")), 15)
            assert sorted(outcomes) == [False, True]
            async with pool.connection() as conn:
                owners = await (await conn.execute("SELECT count(*) FROM j01_memberships WHERE workspace_id=%s "
                    "AND active AND role='owner'", (workspace,))).fetchone()
                assert owners == (1,)

    _run(run())


def test_real_migration_adopts_populated_schema_replays_and_preserves_rows():
    import psycopg

    from aletheia_lab.hosted.workspace_migration import WorkspaceMigrationRejected, migrate

    async def run():
        async with _database(migrate_schema=False) as (pool, schema):
            await _identity(pool, "owner")
            async with pool.connection() as conn:
                await conn.execute("INSERT INTO j01_workspaces VALUES ('legacy','Legacy')")
                await conn.execute("INSERT INTO j01_memberships VALUES ('legacy','owner','owner',true,1)")
                await conn.execute("INSERT INTO j01_sources(source_id,workspace_id,creator_id,request_key_hash,"
                    "sha256,payload,manifest) VALUES ('source','legacy','owner','synthetic',%s,%s,'[]')",
                    (hashlib.sha256(b"original").hexdigest(), b"original"))
            async with pool.connection() as conn:
                assert await migrate(conn, schema) == "adopted"
            async with pool.connection() as conn:
                assert await migrate(conn, schema) == "unchanged"
                assert await (await conn.execute("SELECT payload FROM j01_sources WHERE source_id='source'")).fetchone() == (b"original",)
            with pytest.raises(WorkspaceMigrationRejected):
                async with pool.connection() as conn:
                    await conn.execute("ALTER TABLE j01_upload_snapshots DROP CONSTRAINT j01_snapshots_source_scope")
                    await conn.execute("ALTER TABLE j01_upload_snapshots ADD CONSTRAINT j01_snapshots_source_scope CHECK(true)")
                    await migrate(conn, schema)
            with pytest.raises(WorkspaceMigrationRejected):
                async with pool.connection() as conn:
                    await conn.execute("CREATE OR REPLACE FUNCTION j01_workspace_owner_guard() RETURNS trigger "
                                       "LANGUAGE plpgsql AS $$ BEGIN RETURN NULL; END; $$")
                    await migrate(conn, schema)
            with pytest.raises(psycopg.errors.CheckViolation):
                async with pool.connection() as conn:
                    await conn.execute("UPDATE j01_memberships SET active=false,revision=revision+1 WHERE user_id='owner'")
            with pytest.raises(psycopg.errors.ForeignKeyViolation):
                async with pool.connection() as conn:
                    await conn.execute("INSERT INTO j01_workspaces VALUES ('other','Other')")
                    await conn.execute("INSERT INTO j01_memberships VALUES ('other','owner','owner',true,1)")
                    await conn.execute("INSERT INTO j01_upload_snapshots VALUES ('bad','other','source','{}')")
            async with pool.connection() as conn:
                await conn.execute("DROP TRIGGER j01_memberships_owner_guard ON j01_memberships")
            with pytest.raises(WorkspaceMigrationRejected):
                async with pool.connection() as conn:
                    await migrate(conn, schema)

    _run(run())


def test_real_migration_rejects_ownerless_legacy_data_without_rewriting_it():
    from aletheia_lab.hosted.workspace_migration import WorkspaceMigrationRejected, migrate

    async def run():
        async with _database(migrate_schema=False) as (pool, schema):
            await _identity(pool, "analyst")
            async with pool.connection() as conn:
                await conn.execute("INSERT INTO j01_workspaces VALUES ('legacy','Legacy')")
                await conn.execute("INSERT INTO j01_memberships VALUES ('legacy','analyst','analyst',true,1)")
            with pytest.raises(WorkspaceMigrationRejected):
                async with pool.connection() as conn:
                    await migrate(conn, schema)
            async with pool.connection() as conn:
                assert await (await conn.execute("SELECT role,revision FROM j01_memberships")).fetchone() == ("analyst", 1)
                assert await (await conn.execute("SELECT to_regclass('k04_workspace_migrations')")).fetchone() == (None,)

    _run(run())


def test_real_pool_reuses_connection_across_tenants_after_success_error_rollback_and_cancellation():
    import psycopg

    from aletheia_lab.hosted.authorization import AuthorizationDenied, PostgresAuthorization
    from aletheia_lab.hosted.workspace_management import WorkspaceManagement

    async def run():
        async with _database(maximum=1) as (pool, schema):
            a, b = await _identity(pool, "a"), await _identity(pool, "b")
            manager, authorization = WorkspaceManagement(pool), PostgresAuthorization()
            wa = (await manager.create(credential_digest=a, name="A"))["workspace_id"]
            wb = (await manager.create(credential_digest=b, name="B"))["workspace_id"]
            pids = []

            async def read(digest, workspace):
                async with pool.connection() as conn:
                    pids.append((await (await conn.execute("SELECT pg_backend_pid()")).fetchone())[0])
                    auth = await authorization.authorize(conn, digest.get_secret_value(), workspace, "read")
                    return auth.user_id

            assert await read(a, wa) == "a"
            assert await read(b, wb) == "b"
            with pytest.raises(AuthorizationDenied):
                await read(a, wb)
            assert await read(b, wb) == "b"
            with pytest.raises(psycopg.errors.CheckViolation):
                async with pool.connection() as conn:
                    await authorization.authorize(conn, a.get_secret_value(), wa, "read")
                    await conn.execute("UPDATE j01_memberships SET revision=-1 WHERE workspace_id=%s", (wa,))
            assert await read(b, wb) == "b"
            entered = asyncio.Event()

            async def cancelled():
                async with pool.connection() as conn:
                    await authorization.authorize(conn, a.get_secret_value(), wa, "read")
                    await conn.execute("UPDATE j01_workspaces SET name='uncommitted' WHERE workspace_id=%s", (wa,))
                    entered.set()
                    await asyncio.Future()

            task = asyncio.create_task(cancelled())
            await asyncio.wait_for(entered.wait(), 5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert await read(b, wb) == "b"
            assert len(set(pids)) == 1
            async with pool.connection() as conn:
                assert await (await conn.execute("SELECT name FROM j01_workspaces WHERE workspace_id=%s", (wa,))).fetchone() == ("A",)
            query_entered = asyncio.Event()

            async def cancelled_query():
                async with pool.connection() as conn:
                    await authorization.authorize(conn, a.get_secret_value(), wa, "read")
                    await conn.execute("UPDATE j01_workspaces SET name='query-uncommitted' WHERE workspace_id=%s", (wa,))
                    query_entered.set()
                    await conn.execute("SELECT pg_sleep(30)")

            task = asyncio.create_task(cancelled_query())
            try:
                await asyncio.wait_for(query_entered.wait(), 5)
                async with await psycopg.AsyncConnection.connect(
                        _scoped(os.environ["K04_TEST_DATABASE_URL"], schema), autocommit=True) as observer:
                    async with asyncio.timeout(5):
                        while True:
                            state = await (await observer.execute(
                                "SELECT wait_event FROM pg_stat_activity WHERE pid=%s AND state='active'", (pids[0],))).fetchone()
                            if state == ("PgSleep",):
                                break
                            await asyncio.sleep(0)
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            finally:
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
            assert await read(b, wb) == "b"
            assert len(set(pids)) == 1
            async with pool.connection() as conn:
                assert await (await conn.execute("SELECT name FROM j01_workspaces WHERE workspace_id=%s", (wa,))).fetchone() == ("A",)

    _run(run())


def test_real_operator_provision_does_not_link_same_email_and_grants_are_scoped_and_current():
    from aletheia_lab.hosted.authorization import AuthorizationDenied, PostgresAuthorization
    from aletheia_lab.hosted.workspace_management import (
        WorkspaceConflict,
        WorkspaceDenied,
        WorkspaceManagement,
        provision_identity,
    )
    from aletheia_lab.hosted_auth.repositories import ValidatedIdentity

    async def run():
        async with _database() as (pool, _):
            provisioned = []
            for subject in ("new-subject-a", "new-subject-b", "new-subject-a"):
                identity = ValidatedIdentity(issuer="https://issuer.example.invalid", subject=SecretStr(subject),
                    email=SecretStr("same@example.invalid"), email_verified=True)
                async with pool.connection() as conn:
                    provisioned.append(await provision_identity(conn, identity,
                        expected_issuer="https://issuer.example.invalid", display_name="Synthetic"))
            assert provisioned[0] == provisioned[2] and provisioned[0]["user_id"] != provisioned[1]["user_id"]
            assert provisioned[0]["onboarding"] == "needs_workspace"
            owner, analyst, foreign = [await _identity(pool, user) for user in ("owner", "analyst", "foreign")]
            manager, authorization = WorkspaceManagement(pool), PostgresAuthorization()
            workspace = (await manager.create(credential_digest=owner, name="A"))["workspace_id"]
            other = (await manager.create(credential_digest=foreign, name="B"))["workspace_id"]
            invitation = await _member(manager, owner, workspace, "analyst", "analyst")
            await manager.accept_invite(credential_digest=analyst, token=SecretStr(invitation["token"]))
            async with pool.connection() as conn:
                for source, scope, creator in (("local-source", workspace, "owner"), ("foreign-source", other, "foreign")):
                    await conn.execute("INSERT INTO j01_sources(source_id,workspace_id,creator_id,request_key_hash,"
                        "sha256,payload,manifest) VALUES (%s,%s,%s,%s,%s,%s,'[]')",
                        (source, scope, creator, source, hashlib.sha256(b"synthetic").hexdigest(), b"synthetic"))

            async def allowed(action):
                async with pool.connection() as conn:
                    auth = await authorization.authorize(conn, analyst.get_secret_value(), workspace, action)
                    await authorization.authorize_source(conn, auth, workspace, "local-source", action)

            with pytest.raises(AuthorizationDenied):
                await allowed("read")
            granted = await manager.set_source_grant(credential_digest=owner, workspace_id=workspace,
                source_id="local-source", user_id="analyst", actions=["read", "delete"], expected_revision=0)
            assert granted["revision"] == 1
            await allowed("read")
            await allowed("delete")
            with pytest.raises(WorkspaceConflict):
                await manager.set_source_grant(credential_digest=owner, workspace_id=workspace,
                    source_id="local-source", user_id="analyst", actions=[], expected_revision=0)
            await allowed("delete")
            with pytest.raises(WorkspaceDenied):
                await manager.set_source_grant(credential_digest=analyst, workspace_id=workspace,
                    source_id="local-source", user_id="analyst", actions=["read", "write"], expected_revision=1)
            with pytest.raises(AuthorizationDenied):
                await manager.set_source_grant(credential_digest=owner, workspace_id=workspace,
                    source_id="foreign-source", user_id="analyst", actions=["read"], expected_revision=0)
            await manager.set_source_grant(credential_digest=owner, workspace_id=workspace,
                source_id="local-source", user_id="analyst", actions=[], expected_revision=1)
            with pytest.raises(AuthorizationDenied):
                await allowed("delete")
            assert await manager.is_ready()

    _run(run())


def test_real_membership_api_concurrent_owner_leave_has_one_winner():
    from aletheia_lab.hosted.workspace_management import WorkspaceDenied, WorkspaceManagement

    async def run():
        async with _database() as (pool, _):
            owner, second = await _identity(pool, "owner"), await _identity(pool, "second")
            manager = WorkspaceManagement(pool)
            workspace = (await manager.create(credential_digest=owner, name="Synthetic"))["workspace_id"]
            invitation = await _member(manager, owner, workspace, "second", "admin")
            await manager.accept_invite(credential_digest=second, token=SecretStr(invitation["token"]))
            async with pool.connection() as conn:
                await conn.execute("UPDATE j01_memberships SET role='owner',revision=revision+1 "
                                   "WHERE workspace_id=%s AND user_id='second'", (workspace,))
            barrier = asyncio.Barrier(2)

            async def leave(digest, user, revision):
                await asyncio.wait_for(barrier.wait(), 5)
                try:
                    return await manager.mutate_member(credential_digest=digest, workspace_id=workspace,
                                                       user_id=user, expected_revision=revision, active=False)
                except WorkspaceDenied:
                    return None

            results = await asyncio.wait_for(asyncio.gather(leave(owner, "owner", 1), leave(second, "second", 2)), 10)
            assert sum(value is not None for value in results) == 1
            async with pool.connection() as conn:
                assert await (await conn.execute("SELECT count(*) FROM j01_memberships WHERE workspace_id=%s "
                    "AND active AND role='owner'", (workspace,))).fetchone() == (1,)

    _run(run())


def test_real_owner_transfer_and_last_owner_require_enabled_verified_replacement():
    import psycopg

    from aletheia_lab.hosted.workspace_management import WorkspaceDenied, WorkspaceManagement

    async def run():
        async with _database() as (pool, _):
            owner, second = await _identity(pool, "owner"), await _identity(pool, "second")
            manager = WorkspaceManagement(pool)
            workspace = (await manager.create(credential_digest=owner, name="Synthetic"))["workspace_id"]
            invitation = await _member(manager, owner, workspace, "second", "admin")
            await manager.accept_invite(credential_digest=second, token=SecretStr(invitation["token"]))
            for enabled, verified in ((False, True), (True, False)):
                async with pool.connection() as conn:
                    await conn.execute("UPDATE hosted_identity_bindings SET enabled=%s,email_verified=%s WHERE user_id='second'",
                                       (enabled, verified))
                with pytest.raises(WorkspaceDenied):
                    await manager.transfer_owner(credential_digest=owner, workspace_id=workspace,
                                                  user_id="second", expected_revision=1)
                async with pool.connection() as conn:
                    assert await (await conn.execute("SELECT role,revision FROM j01_memberships "
                        "WHERE workspace_id=%s AND user_id='owner'", (workspace,))).fetchone() == ("owner", 1)
                    assert await (await conn.execute("SELECT role,revision FROM j01_memberships "
                        "WHERE workspace_id=%s AND user_id='second'", (workspace,))).fetchone() == ("admin", 1)
            async with pool.connection() as conn:
                await conn.execute("UPDATE hosted_identity_bindings SET enabled=true,email_verified=true WHERE user_id='second'")
                await conn.execute("UPDATE j01_memberships SET role='owner',revision=revision+1 "
                                   "WHERE workspace_id=%s AND user_id='second'", (workspace,))
            async with pool.connection() as conn:
                await conn.execute("UPDATE hosted_identity_bindings SET enabled=false WHERE user_id='second'")
            with pytest.raises(WorkspaceDenied):
                await manager.mutate_member(credential_digest=owner, workspace_id=workspace,
                                             user_id="owner", expected_revision=1, active=False)
            with pytest.raises(psycopg.errors.CheckViolation):
                async with pool.connection() as conn:
                    await conn.execute("UPDATE j01_memberships SET active=false,revision=revision+1 "
                                       "WHERE workspace_id=%s AND user_id='owner'", (workspace,))

    _run(run())
