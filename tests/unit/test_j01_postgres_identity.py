"""Identity lookup stays exact and fails closed on invalid database facts."""

import asyncio
from contextlib import asynccontextmanager

import pytest
from pydantic import SecretStr

from aletheia_lab.hosted_auth.postgres_identity import PostgresIdentityDirectory
from aletheia_lab.hosted_auth.repositories import RepositoryDataRejected


class FakePool:
    def __init__(self, row):
        self.row = row
        self.params = None

    @asynccontextmanager
    async def connection(self):
        yield self

    async def execute(self, query, params):
        self.params = params
        assert "issuer = %s AND subject = %s" in query
        return self

    async def fetchone(self):
        return self.row


def test_exact_identity_lookup_and_no_provisioning():
    row = ("https://issuer", "subject", "user1", "User", "user@example.invalid", True,
           True, 2, "needs_workspace", None)
    pool = FakePool(row)
    result = asyncio.run(PostgresIdentityDirectory(pool).find_identity(
        issuer="https://issuer", subject=SecretStr("subject"),
    ))
    assert pool.params == ("https://issuer", "subject")
    assert result is not None and result.actor.user_id == "user1" and result.account_epoch == 2
    pool.row = None
    assert asyncio.run(PostgresIdentityDirectory(pool).find_identity(
        issuer="https://issuer", subject=SecretStr("unknown"),
    )) is None


def test_invalid_account_data_fails_closed():
    pool = FakePool(("https://issuer", "subject", "user1", "User", "user@example.invalid",
                     True, True, -1, "needs_workspace", None))
    with pytest.raises(RepositoryDataRejected):
        asyncio.run(PostgresIdentityDirectory(pool).find_identity(
            issuer="https://issuer", subject=SecretStr("subject"),
        ))
