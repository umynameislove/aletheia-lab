"""Explicit audit opt-in for real unprivileged API/worker database logins."""
from __future__ import annotations

import os
import secrets
from contextlib import asynccontextmanager
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit
from uuid import uuid4

import pytest
from test_k06_action_audit_postgres import _database, _run

from aletheia_lab.hosted.action_audit import is_ready, record_action
from aletheia_lab.hosted.action_audit_migration import migrate, verify
from aletheia_lab.hosted.project_policy_migration import migrate as migrate_projects
from aletheia_lab.hosted.review_migration import migrate as migrate_reviews
from aletheia_lab.hosted.runtime_privileges import RuntimePrivilegesRejected, grant_runtime

pytestmark = [pytest.mark.integration, pytest.mark.skipif(not os.environ.get("K06_TEST_DATABASE_URL"),
    reason="Explicit K-06 PostgreSQL test URL required")]


def _role_url(database, schema, role, password):
    parsed = urlsplit(database)
    assert parsed.hostname is not None
    host = "[" + parsed.hostname + "]" if ":" in parsed.hostname else parsed.hostname
    if parsed.port is not None:
        host += ":" + str(parsed.port)
    query = [(key, value) for key, value in parse_qsl(parsed.query) if key != "options"]
    query.append(("options", "-csearch_path=" + schema + ",pg_catalog"))
    return urlunsplit(parsed._replace(netloc=quote(role, safe="") + ":" + quote(password, safe="") + "@" + host,
                                     query=urlencode(query)))


def test_audit_grants_are_opt_in_verify_schema_and_allow_only_safe_insert_and_read():
    import psycopg
    from psycopg import sql

    class RolePool:
        def __init__(self, url):
            self.url = url

        @asynccontextmanager
        async def connection(self):
            async with await psycopg.AsyncConnection.connect(self.url) as conn:
                yield conn

    async def scenario():
        created = []
        suffix = uuid4().hex
        try:
            async with _database() as (owner, schema):
                async with owner.connection() as conn:
                    await migrate_projects(conn, schema)
                    await migrate_reviews(conn, schema)
                    for profile in ("api", "worker"):
                        role, password = "k06_audit_" + profile + "_" + suffix, secrets.token_urlsafe(32)
                        await conn.execute(sql.SQL("CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
                            "NOINHERIT NOBYPASSRLS PASSWORD {}").format(sql.Identifier(role), sql.Literal(password)))
                        created.append((profile, role, password))
                with pytest.raises(RuntimePrivilegesRejected):
                    async with owner.connection() as conn:
                        await grant_runtime(conn, schema, created[0][1], profile="api", audit=True)
                async with owner.connection() as conn:
                    await migrate(conn, schema)
                    for profile, role, _password in created:
                        await grant_runtime(conn, schema, role, profile=profile)
                        assert await (await conn.execute("SELECT has_table_privilege(%s,%s,'SELECT'),"
                            "has_any_column_privilege(%s,%s,'INSERT')", (role, schema + ".k06_action_audit",
                            role, schema + ".k06_action_audit"))).fetchone() == (False, False)
                        await grant_runtime(conn, schema, role, profile=profile, audit=True)
                for profile, role, password in created:
                    role_pool = RolePool(_role_url(os.environ["K06_TEST_DATABASE_URL"], schema, role, password))
                    assert await is_ready(role_pool)
                    async with owner.connection() as conn:
                        await conn.execute(sql.SQL("REVOKE INSERT(actor_id) ON {}.k06_action_audit FROM {}").format(
                            sql.Identifier(schema), sql.Identifier(role)))
                    assert not await is_ready(role_pool)
                    async with owner.connection() as conn:
                        await grant_runtime(conn, schema, role, profile=profile, audit=True)
                    assert await is_ready(role_pool)
                    async with await psycopg.AsyncConnection.connect(
                            _role_url(os.environ["K06_TEST_DATABASE_URL"], schema, role, password)) as conn:
                        assert await verify(conn, schema)
                        assert await (await conn.execute("SELECT has_table_privilege(current_user,'k06_action_audit','SELECT'),"
                            "has_any_column_privilege(current_user,'k06_action_audit','INSERT'),"
                            "has_any_column_privilege(current_user,'k06_action_audit','UPDATE'),"
                            "has_table_privilege(current_user,'k06_action_audit','DELETE'),"
                            "has_table_privilege(current_user,'k06_action_audit','TRUNCATE')")).fetchone() == (True, True, False, False, False)
                        await record_action(conn, actor_id="user_a", workspace_id="wa", action="probe." + profile,
                                            effect_id="effect_" + profile, operation_id="operation_" + profile)
                    for statement in ("UPDATE k06_action_audit SET revision=0", "DELETE FROM k06_action_audit",
                                      "TRUNCATE k06_action_audit", "UPDATE k06_action_audit_migrations SET version=version"):
                        with pytest.raises(psycopg.errors.InsufficientPrivilege):
                            async with await psycopg.AsyncConnection.connect(
                                    _role_url(os.environ["K06_TEST_DATABASE_URL"], schema, role, password)) as conn:
                                await conn.execute(statement)
                async with owner.connection() as conn:
                    assert await (await conn.execute("SELECT count(*) FROM k06_action_audit")).fetchone() == (2,)
                    await conn.execute("DROP TABLE k06_action_audit")
                assert not await is_ready(owner)
        finally:
            async with await psycopg.AsyncConnection.connect(os.environ["K06_TEST_DATABASE_URL"], autocommit=True) as admin:
                for profile, role, _password in created:
                    assert role == "k06_audit_" + profile + "_" + suffix
                    await admin.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))
    _run(scenario())
