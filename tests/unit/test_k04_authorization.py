"""Deny-default role matrix and the current-session/source trust boundaries."""
import asyncio

import pytest

from aletheia_lab.hosted.authorization import (
    Authorization,
    AuthorizationDenied,
    PostgresAuthorization,
    current_session,
    role_allows,
)

ROLES = ("owner", "admin", "analyst", "reviewer", "viewer")
EXPECTED = {
    "read": ROLES,
    "import": ROLES[:3],
    "preview": ROLES[:3],
    "confirm": ROLES[:3],
    "analyze": ROLES[:3],
    "refresh": ROLES[:3],
    "review": ROLES[:4],
    "export": ROLES,
    "delete": ROLES[:3],
    "manage_members": ROLES[:2],
    "transfer_owner": ROLES[:1],
    "unknown": (),
}


@pytest.mark.parametrize("role", ROLES + ("root",))
@pytest.mark.parametrize("action", EXPECTED)
def test_role_action_matrix_denies_unknown_and_escalation(role, action):
    assert role_allows(role, action) is (role in EXPECTED[action])


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


def test_current_session_locks_account_then_fresh_session_without_global_gate():
    conn = Connection([("user",), ("user",), ("user",)])
    assert asyncio.run(current_session(conn, "a" * 64)) == "user"
    assert "FOR SHARE" in conn.queries[1][0]
    assert "FOR SHARE OF s,c" in conn.queries[2][0]
    assert all("advisory" not in query for query, _ in conn.queries)
    assert "c.generation=s.generation" in conn.queries[2][0]
    assert "s.session_epoch=a.account_epoch" in conn.queries[2][0]


@pytest.mark.parametrize("rows", [[None], [("user",), None], [("user",), ("user",), None]])
def test_current_session_missing_or_revoked_facts_deny(rows):
    with pytest.raises(AuthorizationDenied):
        asyncio.run(current_session(Connection(rows), "a" * 64))


def test_current_session_refuses_autocommit_before_any_query():
    conn = Connection([])
    conn.autocommit = True
    with pytest.raises(RuntimeError, match="transactional"):
        asyncio.run(current_session(conn, "a" * 64))
    assert conn.queries == []


@pytest.mark.parametrize("role,action,creator,grant,allowed", [
    ("owner", "read", "other", None, True),
    ("admin", "delete", "other", None, True),
    ("analyst", "analyze", "user", None, True),
    ("analyst", "delete", "user", None, False),
    ("analyst", "delete", "user", (["read", "delete"],), True),
    ("viewer", "read", "other", None, False),
    ("viewer", "read", "other", (["read"],), True),
    ("viewer", "export", "other", (["export"],), False),
    ("viewer", "export", "other", (["read", "export"],), True),
    ("reviewer", "review", "other", (["read", "review"],), True),
    ("reviewer", "analyze", "user", (["read", "write"],), False),
    ("analyst", "analyze", "other", (["write"],), False),
    ("analyst", "analyze", "other", (["read", "write"],), True),
])
def test_source_grants_cannot_exceed_role_or_skip_parent_read(role, action, creator, grant, allowed):
    conn = Connection([(role, 1, True), (creator,), grant])
    call = PostgresAuthorization().authorize_source(conn, Authorization("user", role, 1, "ws"), "ws", "src", action)
    if allowed:
        asyncio.run(call)
    else:
        with pytest.raises(AuthorizationDenied):
            asyncio.run(call)


def test_mismatched_source_parent_is_unavailable_even_for_owner():
    with pytest.raises(AuthorizationDenied):
        asyncio.run(PostgresAuthorization().authorize_source(
            Connection([("owner", 1, True), None]), Authorization("user", "owner", 1, "ws_b"), "ws_b", "src_a", "read"))


def test_owner_authorization_cannot_be_reused_in_another_workspace():
    conn = Connection([])
    with pytest.raises(AuthorizationDenied):
        asyncio.run(PostgresAuthorization().authorize_source(
            conn, Authorization("user", "owner", 1, "ws_a"), "ws_b", "src_b", "read"))
    assert conn.queries == []


@pytest.mark.parametrize("member", [None, ("owner", 2, True), ("owner", 1, False), ("viewer", 1, True)])
def test_captured_authorization_cannot_outlive_membership_revision_or_revoke(member):
    conn = Connection([member])
    with pytest.raises(AuthorizationDenied):
        asyncio.run(PostgresAuthorization().authorize_source(
            conn, Authorization("user", "owner", 1, "ws"), "ws", "src", "read"))
    assert len(conn.queries) == 1
