"""Canonical project policy: current role, parent, tombstone and explicit grants."""
import asyncio
from contextlib import asynccontextmanager

import pytest
from pydantic import SecretStr

from aletheia_lab.hosted.authorization import (
    Authorization,
    AuthorizationDenied,
    PostgresAuthorization,
)
from aletheia_lab.hosted.project_access import PostgresProjectAccess
from aletheia_lab.hosted.project_policy_migration import ProjectPolicyMigrationRejected, migrate
from aletheia_lab.hosted.workspace_management import WorkspaceConflict


class Connection:
    autocommit = False

    def __init__(self, rows):
        self.rows = iter(rows)
        self.queries = []

    async def execute(self, query, params=()):
        self.queries.append((query, params))
        self.row = next(self.rows)
        return self

    async def fetchone(self):
        return self.row


@pytest.mark.parametrize("role,action,creator,grant,allowed", [
    ("owner", "read", "other", None, True),
    ("admin", "delete", "other", None, True),
    ("analyst", "analyze", "user", None, True),
    ("analyst", "delete", "user", None, False),
    ("analyst", "delete", "user", (["read", "delete"],), True),
    ("reviewer", "review", "other", (["read", "review"],), True),
    ("reviewer", "analyze", "user", (["read", "write"],), False),
    ("viewer", "export", "other", (["read", "export"],), True),
    ("viewer", "export", "other", (["export"],), False),
    ("viewer", "read", "other", None, False),
    ("analyst", "analyze", "other", (["read", "write"],), True),
    ("analyst", "refresh", "other", (["write"],), False),
    ("owner", "unknown", "user", None, False),
])
def test_project_role_and_grants(role, action, creator, grant, allowed):
    conn = Connection([(role, 1, True), (creator, 0), None, grant])
    call = PostgresAuthorization().authorize_project(conn, Authorization("user", role, 1, "wa"), "wa", "project", action)
    if allowed:
        asyncio.run(call)
    else:
        with pytest.raises(AuthorizationDenied):
            asyncio.run(call)


def test_owner_authorization_is_bound_to_workspace():
    conn = Connection([])
    with pytest.raises(AuthorizationDenied):
        asyncio.run(PostgresAuthorization().authorize_project(
            conn, Authorization("user", "owner", 1, "wa"), "wb", "project", "read"))
    assert not conn.queries


@pytest.mark.parametrize("member", [None, ("owner", 2, True), ("owner", 1, False), ("viewer", 1, True)])
def test_stale_project_authorization_does_not_reuse_member_grant(member):
    conn = Connection([member])
    with pytest.raises(AuthorizationDenied):
        asyncio.run(PostgresAuthorization().authorize_project(
            conn, Authorization("user", "owner", 1, "wa"), "wa", "project", "read"))
    assert len(conn.queries) == 1


def test_tombstone_denies_even_owner_after_project_lock():
    conn = Connection([("owner", 1, True), ("user", 0), (1,)])
    with pytest.raises(AuthorizationDenied):
        asyncio.run(PostgresAuthorization().authorize_project(
            conn, Authorization("user", "owner", 1, "wa"), "wa", "project", "read"))
    assert "FOR SHARE" in conn.queries[1][0]
    assert "record_type='deletion'" in conn.queries[2][0]


class Pool:
    def __init__(self, conn):
        self.conn = conn

    @asynccontextmanager
    async def connection(self):
        yield self.conn


class WorkspaceFacts:
    async def _accounts(self, conn, digest, user):
        return "manager"

    async def _workspace(self, conn, workspace):
        return "Synthetic"

    async def _manager(self, conn, workspace, actor):
        return ("owner", True, 1)

    async def _membership(self, conn, workspace, user):
        return ("viewer", True, 1)


@pytest.mark.parametrize("revision,expected,allowed", [(None, 0, True), ((2,), 2, True), ((2,), 1, False)])
def test_project_grant_compare_and_swap_does_not_write_on_conflict(revision, expected, allowed):
    conn = Connection([("creator", 0), None, revision, None, (False,)])
    manager = PostgresProjectAccess(Pool(conn))
    manager._workspaces = WorkspaceFacts()
    call = manager.set_grant(credential_digest=SecretStr("a" * 64), workspace_id="wa", project_id="project",
                             user_id="viewer", actions=["read", "export"], expected_revision=expected)
    if allowed:
        value = asyncio.run(call)
        assert value["revision"] == expected + 1 and value["project_id"] == "project"
        assert [query for query, _ in conn.queries if query.startswith('INSERT')][-1].startswith("INSERT INTO j01_project_grants")
    else:
        with pytest.raises(WorkspaceConflict):
            asyncio.run(call)
        assert all(not query.startswith("INSERT") for query, _ in conn.queries)


@pytest.mark.parametrize("actions,revision", [(["owner"], 0), (["export"], 0), (["read", "read"], 0),
                                             ([None], 0), (["read"], True), (["read"], -1)])
def test_project_grant_invalid_input_never_opens_transaction(actions, revision):
    conn = Connection([])
    with pytest.raises(ValueError):
        asyncio.run(PostgresProjectAccess(Pool(conn)).set_grant(credential_digest=SecretStr("a" * 64),
            workspace_id="wa", project_id="project", user_id="viewer", actions=actions, expected_revision=revision))
    assert not conn.queries


@pytest.mark.parametrize("schema", ["public", "pg_private", "information_schema", "a;DROP", "UPPER", ""])
def test_extension_rejects_unsafe_schema_before_any_sql(schema):
    conn = Connection([])
    with pytest.raises(ProjectPolicyMigrationRejected):
        asyncio.run(migrate(conn, schema))
    assert not conn.queries
