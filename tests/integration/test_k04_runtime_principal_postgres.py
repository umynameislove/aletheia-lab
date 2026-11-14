"""Actual LOGIN principals, scoped repositories and pooled reuse without owner rights."""
from __future__ import annotations

import asyncio
import hashlib
import io
import os
import secrets
import sys
import zipfile
from contextlib import asynccontextmanager
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit
from uuid import uuid4

import pytest
from pydantic import SecretStr

from aletheia_lab.hosted.authorization import AuthorizationDenied
from aletheia_lab.hosted.persistence_migration import migrate as migrate_canonical
from aletheia_lab.hosted.persistence_migration import verify as verify_canonical
from aletheia_lab.hosted.postgres_jobs import PostgresJobs
from aletheia_lab.hosted.postgres_sources import PostgresSources
from aletheia_lab.hosted.project_policy_migration import migrate as migrate_projects
from aletheia_lab.hosted.project_policy_migration import verify as verify_projects
from aletheia_lab.hosted.review_migration import migrate as migrate_reviews
from aletheia_lab.hosted.review_migration import verify as verify_reviews
from aletheia_lab.hosted.runtime_binding import verify_runtime_binding
from aletheia_lab.hosted.runtime_privileges import RuntimePrivilegesRejected, grant_runtime
from aletheia_lab.hosted.settings import StorageSettings
from aletheia_lab.hosted.workspace_management import WorkspaceManagement
from aletheia_lab.hosted_auth.abuse import RatePolicy
from aletheia_lab.hosted_auth.admission_migration import migrate as migrate_admission
from aletheia_lab.hosted_auth.postgres_admission import PostgresAuthAdmission
from aletheia_lab.hosted_auth.worker_sessions import WorkerPostgresSources

pytestmark = [pytest.mark.integration, pytest.mark.skipif(not os.environ.get("K04_TEST_DATABASE_URL"),
    reason="Explicit K-04 PostgreSQL test URL required")]


def _run(coro):
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else None) as runner:
        return runner.run(coro)


def _url(database, schema, *, role=None, password=None):
    value = urlsplit(database)
    if value.scheme != "postgresql" or value.hostname is None:
        raise ValueError("Explicit PostgreSQL test URL required")
    query = [(key, item) for key, item in parse_qsl(value.query) if key != "options"]
    query.append(("options", "-csearch_path=" + schema + ",pg_catalog"))
    if role is not None:
        host = "[" + value.hostname + "]" if ":" in value.hostname else value.hostname
        if value.port is not None:
            host += ":" + str(value.port)
        value = value._replace(netloc=quote(role, safe="") + ":" + quote(password, safe="") + "@" + host)
    return urlunsplit(value._replace(query=urlencode(query)))


def _credential(user):
    return SecretStr(hashlib.sha256(("synthetic-principal-" + user).encode()).hexdigest())


@asynccontextmanager
async def _principals():
    import psycopg
    from psycopg import sql
    from psycopg_pool import AsyncConnectionPool

    database = os.environ["K04_TEST_DATABASE_URL"]
    suffix = uuid4().hex
    schema, api_role, worker_role = "k04_runtime_" + suffix, "k04_api_" + suffix, "k04_worker_" + suffix
    api_password, worker_password = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    created_roles, created_schema = [], False
    async with await psycopg.AsyncConnection.connect(database, autocommit=True) as admin:
        try:
            for role, password in ((api_role, api_password), (worker_role, worker_password)):
                await admin.execute(sql.SQL("CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
                    "NOINHERIT NOBYPASSRLS PASSWORD {}").format(sql.Identifier(role), sql.Literal(password)))
                created_roles.append(role)
            await admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
            created_schema = True
            async with AsyncConnectionPool(_url(database, schema), min_size=1, max_size=1, open=False) as owner:
                await owner.wait(timeout=10)
                async with owner.connection() as conn:
                    await migrate_canonical(conn, schema)
                    await migrate_projects(conn, schema)
                    await migrate_reviews(conn, schema)
                    await migrate_admission(conn, schema)
                    for user in ("owner_a", "owner_b", "analyst"):
                        await conn.execute("INSERT INTO hosted_identity_bindings VALUES('issuer',%s,%s,'Synthetic',%s,"
                            "true,true,0,'needs_workspace',NULL,NULL)", (user, user, user + "@example.invalid"))
                        await conn.execute("INSERT INTO j01_sessions VALUES(%s,%s,'synthetic-csrf','active',"
                            "statement_timestamp(),statement_timestamp(),statement_timestamp(),"
                            "statement_timestamp()+interval '900 seconds',statement_timestamp()+interval '28000 seconds',0,0)",
                            ("ctx_" + user, user))
                        await conn.execute("INSERT INTO j01_session_credentials VALUES(%s,%s,'active',0)",
                                           (_credential(user).get_secret_value(), "ctx_" + user))
                    await grant_runtime(conn, schema, api_role, profile="api")
                    await grant_runtime(conn, schema, worker_role, profile="worker")
                async with AsyncConnectionPool(_url(database, schema, role=api_role, password=api_password),
                        min_size=1, max_size=1, open=False) as api, AsyncConnectionPool(
                        _url(database, schema, role=worker_role, password=worker_password),
                        min_size=1, max_size=1, open=False) as worker:
                    await api.wait(timeout=10)
                    await worker.wait(timeout=10)
                    yield owner, api, worker, schema, api_role, worker_role, admin
        finally:
            # Drop only this test's namespace and exact roles; no DROP OWNED or shared ACL change.
            if created_schema:
                assert schema == "k04_runtime_" + suffix
                await admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
            for role in created_roles:
                assert role in ("k04_api_" + suffix, "k04_worker_" + suffix)
                await admin.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))


def _bundle():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("dataset.csv", "id,target\n1,0\n2,1\n")
        archive.writestr("metrics.csv", "run,name,value\nbaseline,loss,0.5\ncandidate,loss,0.6\n")
        archive.writestr("config.json", '{"seed":42}\n')
    return buffer.getvalue()


def test_actual_runtime_logins_scope_mutations_binding_privileges_and_pool_reuse(tmp_path):
    import psycopg
    from psycopg import sql

    async def scenario():
        async with _principals() as (owner, api, worker, schema, api_role, worker_role, admin):
            settings = StorageSettings(database_url_env="ALETHEIA_HOSTED_DATABASE_URL", object_root=str(tmp_path),
                                       expected_schema=schema, expected_database_role=api_role)
            assert await verify_runtime_binding(api, settings, strict=True)
            worker_settings = settings.model_copy(update={"expected_database_role": worker_role})
            assert await verify_runtime_binding(worker, worker_settings, strict=True)
            for pool in (api, worker):
                async with pool.connection() as conn:
                    assert await verify_canonical(conn, schema)
                    assert await verify_projects(conn, schema)
                    assert await verify_reviews(conn, schema)
            assert not await verify_runtime_binding(api, worker_settings, strict=True)
            assert not await verify_runtime_binding(api, settings.model_copy(update={"expected_schema": "k04_absent_" + uuid4().hex}), strict=True)
            async with owner.connection() as conn:
                owner_name = (await (await conn.execute("SELECT current_user")).fetchone())[0]
                await grant_runtime(conn, schema, api_role, profile="api")
                await grant_runtime(conn, schema, worker_role, profile="worker")
                with pytest.raises(RuntimePrivilegesRejected):
                    await grant_runtime(conn, schema, owner_name, profile="api")
                with pytest.raises(RuntimePrivilegesRejected):
                    await grant_runtime(conn, schema, api_role, profile="worker")
            assert not await verify_runtime_binding(owner, settings.model_copy(update={"expected_database_role": owner_name}), strict=True)
            async with api.connection() as conn:
                api_pid = await (await conn.execute("SELECT pg_backend_pid()")).fetchone()
                assert await (await conn.execute("SELECT current_user,session_user")).fetchone() == (api_role, api_role)
                await conn.execute("CREATE TEMP TABLE hosted_identity_bindings(shadow integer)")
            async with worker.connection() as conn:
                worker_pid = await (await conn.execute("SELECT pg_backend_pid()")).fetchone()
                assert await (await conn.execute("SELECT rolsuper,rolbypassrls,rolcreatedb,rolcreaterole,rolinherit "
                    "FROM pg_roles WHERE rolname=current_user")).fetchone() == (False, False, False, False, False)
            assert not await verify_runtime_binding(api, settings, strict=True)
            async with api.connection() as conn:
                await conn.execute("DROP TABLE pg_temp.hosted_identity_bindings")
            assert await verify_runtime_binding(api, settings, strict=True)
            for flag, reset in (("BYPASSRLS", "NOBYPASSRLS"), ("CREATEDB", "NOCREATEDB")):
                await admin.execute(sql.SQL("ALTER ROLE {} " + flag).format(sql.Identifier(api_role)))
                try:
                    assert not await verify_runtime_binding(api, settings, strict=True)
                finally:
                    await admin.execute(sql.SQL("ALTER ROLE {} " + reset).format(sql.Identifier(api_role)))
            assert await verify_runtime_binding(api, settings, strict=True)
            management = WorkspaceManagement(api)
            wa = (await management.create(credential_digest=_credential("owner_a"), name="Synthetic A"))["workspace_id"]
            wb = (await management.create(credential_digest=_credential("owner_b"), name="Synthetic B"))["workspace_id"]
            invitation = await management.invite(credential_digest=_credential("owner_a"), workspace_id=wa,
                user_id="analyst", role="analyst", expires_in_seconds=600)
            member = await management.accept_invite(credential_digest=_credential("analyst"), token=SecretStr(invitation["token"]))
            assert member["membership"]["role"] == "analyst"
            rate = PostgresAuthAdmission(api, namespace="synthetic-principal")
            policy = RatePolicy(20, 5, 60)
            await rate.prepare(policy)
            assert await rate.is_ready(policy)
            sources = PostgresSources(api, maximum_upload_bytes=10000, maximum_expanded_bytes=20000, maximum_entries=10)
            worker_sources = WorkerPostgresSources(worker, maximum_upload_bytes=10000, maximum_expanded_bytes=20000, maximum_entries=10)
            jobs, worker_jobs = PostgresJobs(api, sources), PostgresJobs(worker, worker_sources)
            payload = _bundle()
            a = await sources.put(credential_digest=_credential("analyst"), workspace_id=wa,
                                  request_key=SecretStr("synthetic-principal-source-a"), payload=payload)
            b = await sources.put(credential_digest=_credential("owner_b"), workspace_id=wb,
                                  request_key=SecretStr("synthetic-principal-source-b"), payload=payload)
            receipt = await jobs.enqueue(credential_digest=_credential("analyst"), workspace_id=wa, operation="preview",
                request_key=SecretStr("synthetic-principal-job"), request={"source_id": a.source_id})
            lease = SecretStr("c" * 64)
            claim = await worker_jobs.claim(lease_digest=lease, lease_seconds=30)
            assert claim.job_id == receipt.job_id
            assert await worker_sources.read(credential_digest=_credential("analyst"), workspace_id=wa, source_id=a.source_id) == payload
            assert await worker_jobs.complete(claim=claim, lease_digest=lease, result={"synthetic": True})
            assert (await jobs.output(credential_digest=_credential("analyst"), workspace_id=wa, job_id=receipt.job_id))["result"] == {"synthetic": True}
            with pytest.raises(AuthorizationDenied):
                await sources.read(credential_digest=_credential("owner_a"), workspace_id=wb, source_id=b.source_id)
            assert await sources.read(credential_digest=_credential("owner_b"), workspace_id=wb, source_id=b.source_id) == payload
            await management.mutate_member(credential_digest=_credential("owner_a"), workspace_id=wa,
                                           user_id="analyst", expected_revision=1, active=False)
            with pytest.raises(AuthorizationDenied):
                await jobs.output(credential_digest=_credential("analyst"), workspace_id=wa, job_id=receipt.job_id)
            with pytest.raises(AuthorizationDenied):
                await worker_sources.read(credential_digest=_credential("analyst"), workspace_id=wa, source_id=a.source_id)
            for pool in (api, worker):
                for statement in (
                    'CREATE TABLE "' + schema + '".forbidden_ddl(id integer)',
                    'ALTER TABLE "' + schema + '".j01_memberships DISABLE TRIGGER ALL',
                    'DROP TABLE "' + schema + '".j01_workspaces',
                    "UPDATE hosted_identity_bindings SET enabled=false WHERE user_id='owner_a'",
                    "UPDATE hosted_identity_bindings SET issuer='invalid-issuer' WHERE user_id='owner_a'",
                    "UPDATE k04_workspace_migrations SET checksum=repeat('0',64)",
                ):
                    with pytest.raises(psycopg.errors.InsufficientPrivilege):
                        async with pool.connection() as conn:
                            await conn.execute(statement)
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                async with worker.connection() as conn:
                    await conn.execute("UPDATE j01_memberships SET role='owner',revision=revision+1 WHERE user_id='analyst'")
            entered, release = asyncio.Event(), asyncio.Event()

            async def interrupted():
                async with api.connection() as conn:
                    await conn.execute("SELECT workspace_id FROM j01_workspaces WHERE workspace_id=%s", (wa,))
                    entered.set()
                    await release.wait()

            task = asyncio.create_task(interrupted())
            await asyncio.wait_for(entered.wait(), 5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert await sources.read(credential_digest=_credential("owner_b"), workspace_id=wb, source_id=b.source_id) == payload
            assert await worker_sources.read(credential_digest=_credential("owner_b"), workspace_id=wb, source_id=b.source_id) == payload
            async with api.connection() as conn:
                assert await (await conn.execute("SELECT pg_backend_pid()")).fetchone() == api_pid
                assert await (await conn.execute("SELECT current_setting('app.workspace_id',true)")).fetchone() in ((None,), ("",))
            async with worker.connection() as conn:
                assert await (await conn.execute("SELECT pg_backend_pid()")).fetchone() == worker_pid
    _run(scenario())
