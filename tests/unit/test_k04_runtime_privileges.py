"""Unsafe grant inputs fail before any operator SQL is issued."""
import asyncio

import pytest

from aletheia_lab.hosted.runtime_privileges import RuntimePrivilegesRejected, grant_runtime


class NoSqlConnection:
    autocommit = False

    async def execute(self, *args):
        raise AssertionError("Input validation must precede SQL")


@pytest.mark.parametrize("schema,role,profile,autocommit", [
    ("public", "runtime_api", "api", False),
    ("app", "pg_admin", "api", False),
    ("app;DROP", "runtime_api", "api", False),
    ("app", "runtime api", "worker", False),
    ("app", "runtime_api", "owner", False),
    ("app", "runtime_api", "api", True),
])
def test_invalid_runtime_grants_never_execute_sql(schema, role, profile, autocommit):
    conn = NoSqlConnection()
    conn.autocommit = autocommit
    with pytest.raises(RuntimePrivilegesRejected):
        asyncio.run(grant_runtime(conn, schema, role, profile=profile))
