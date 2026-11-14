"""Native record policy follows its own source without changing P3 scope."""
from __future__ import annotations

import asyncio

import pytest

from aletheia_lab.hosted.authorization import (
    Authorization,
    AuthorizationDenied,
    PostgresAuthorization,
)


class _Connection:
    def __init__(self, rows):
        self.rows = iter(rows)
        self.queries = []

    async def execute(self, query, params):
        self.queries.append((query, params))
        self.row = next(self.rows)
        return self

    async def fetchone(self):
        return self.row


class _Policy(PostgresAuthorization):
    def __init__(self, *, project=True, source=True):
        self.project, self.source, self.calls = project, source, []

    async def authorize_project(self, conn, auth, workspace, project, action):
        self.calls.append(("project", workspace, project, action))
        if not self.project:
            raise AuthorizationDenied()

    async def authorize_source(self, conn, auth, workspace, source, action):
        self.calls.append(("source", workspace, source, action))
        if not self.source:
            raise AuthorizationDenied()


@pytest.mark.parametrize("kind,rows,source", [
    ("upload_snapshot", [("upload_snapshot", "source-own", None)], "source-own"),
    ("upload_result", [("upload_result", None, "snapshot-own"), ("source-own",)], "source-own"),
    ("result", [("result", None, "p3-snapshot")], None),
])
@pytest.mark.parametrize("action", ["read", "review", "export"])
def test_each_native_record_requires_its_exact_source_action(kind, rows, source, action):
    conn, policy = _Connection(rows), _Policy()
    result = asyncio.run(policy.authorize_record(conn, Authorization("actor", "analyst", 1, "wa"),
        "wa", "project-own", "record-own", action, for_update=action == "review"))
    assert result == kind
    expected = [("project", "wa", "project-own", action)]
    if source:
        expected.append(("source", "wa", source, action))
    assert policy.calls == expected
    assert conn.queries[0][1] == ("wa", "project-own", "record-own")
    assert conn.queries[0][0].endswith(" FOR UPDATE" if action == "review" else " FOR SHARE")
    if kind == "upload_result":
        assert conn.queries[1][1] == ("wa", "project-own", "snapshot-own")
        assert "record_type='upload_snapshot'" in conn.queries[1][0]


@pytest.mark.parametrize("rows", [
    [None], [("upload_snapshot", None, None)], [("upload_result", None, "foreign-snapshot"), None],
])
def test_missing_native_record_or_same_scope_snapshot_is_denied(rows):
    with pytest.raises(AuthorizationDenied):
        asyncio.run(_Policy().authorize_record(_Connection(rows), Authorization("actor", "analyst", 1, "wa"),
            "wa", "project-own", "record-own", "read"))


@pytest.mark.parametrize("project,source", [(False, True), (True, False)])
def test_neither_parent_permission_substitutes_for_the_other(project, source):
    conn, policy = _Connection([("upload_snapshot", "source-own", None)]), _Policy(project=project, source=source)
    with pytest.raises(AuthorizationDenied):
        asyncio.run(policy.authorize_record(conn, Authorization("actor", "analyst", 1, "wa"),
            "wa", "project-own", "record-own", "read"))
    if not project:
        assert not conn.queries
