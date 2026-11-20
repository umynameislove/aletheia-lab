"""Boundary tests plus an opt-in real PostgreSQL/multiple-process smoke CLI."""

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import SecretStr

from aletheia_lab.hosted_auth.abuse import AuthAdmission, RatePolicy
from aletheia_lab.hosted_auth.postgres_admission import PostgresAuthAdmission
from aletheia_lab.hosted_auth.repositories import RepositoryDataRejected, RepositoryUnavailable
from aletheia_lab.hosted_auth.settings import AuthConfigurationRejected

NOW = datetime(2030, 1, 1, tzinfo=UTC)
POLICY = RatePolicy(9, 3, 60)
ROW = (9, 3, 60, -1, NOW - timedelta(seconds=60), 0, 0)


class Cursor:
    def __init__(self, row):
        self.row = row

    async def fetchone(self):
        return self.row


class Connection:
    def __init__(self):
        self.autocommit = False
        self.row, self.observed = ROW, (NOW,)
        self.statements = []

    async def execute(self, query, params=()):
        self.statements.append(query)
        if query.startswith("SELECT global_limit"):
            return Cursor(self.row)
        return Cursor(self.observed)


class Pool:
    def __init__(self):
        self.connection_value = Connection()
        self.commit_error = None
        self.commits = 0

    @asynccontextmanager
    async def connection(self):
        yield self.connection_value
        if self.commit_error is not None:
            raise self.commit_error
        self.commits += 1


@pytest.mark.parametrize("namespace", ["", "other/schema", "a" * 129, "x;DROP TABLE", None])
def test_namespace_cannot_select_tables_or_create_unbounded_keys(namespace):
    with pytest.raises(AuthConfigurationRejected):
        PostgresAuthAdmission(Pool(), namespace=namespace)


@pytest.mark.parametrize("row", [
    None, (), (9, 3, 60, -1, NOW, 0),
    (10, 3, 60, -1, NOW, 0, 0),
    (9, 3, 60, -2, NOW, 0, 0),
    (9, 3, 60, -1, NOW.replace(tzinfo=None), 0, 0),
    (9, 3, 60, -1, NOW, True, 0),
    (9, 3, 60, -1, NOW, 10, 0),
    (9, 3, 60, -1, NOW, 2, 3),
    (9, 3, 60, -1, NOW, 5, 4),
    (9, 3, 60, -1, NOW, 0, -1),
])
def test_corrupt_counter_or_policy_never_yields_a_grant(row):
    async def scenario():
        pool = Pool()
        pool.connection_value.row = row
        with pytest.raises(RepositoryDataRejected):
            await PostgresAuthAdmission(pool, namespace="test").consume("callback", SecretStr("a" * 64), POLICY)
        assert not any(query.startswith("UPDATE") for query in pool.connection_value.statements)
        assert pool.commits == 0
    asyncio.run(scenario())


def test_database_time_is_checked_after_row_lock_and_backwards_time_is_denied():
    async def scenario():
        pool = Pool()
        pool.connection_value.row = (*ROW[:4], NOW + timedelta(seconds=1), 0, 0)
        with pytest.raises(RepositoryDataRejected):
            await PostgresAuthAdmission(pool, namespace="test").consume("callback", SecretStr("a" * 64), POLICY)
        statements = pool.connection_value.statements
        assert "FOR UPDATE" in statements[-2] and statements[-1] == "SELECT clock_timestamp()"
        assert pool.commits == 0
    asyncio.run(scenario())


def test_no_allowance_escapes_failed_commit_and_driver_text_is_redacted():
    async def scenario():
        pool = Pool()
        pool.commit_error = RuntimeError("synthetic-private-driver-value")
        admission = AuthAdmission(PostgresAuthAdmission(pool, namespace="test"), POLICY, timeout_seconds=1)
        with pytest.raises(RepositoryUnavailable) as caught:
            await admission.check("callback", SecretStr("a" * 64))
        assert "synthetic-private-driver-value" not in repr(caught.value)
        assert caught.value.__suppress_context__ is True
        assert pool.commits == 0
    asyncio.run(scenario())


def test_autocommit_pool_is_rejected_before_any_counter_change():
    async def scenario():
        pool = Pool()
        pool.connection_value.autocommit = True
        with pytest.raises(AuthConfigurationRejected):
            await PostgresAuthAdmission(pool, namespace="test").consume("callback", SecretStr("a" * 64), POLICY)
        assert pool.connection_value.statements == []
    asyncio.run(scenario())


def test_startup_verifies_every_operation_without_consuming_a_slot():
    async def scenario():
        pool = Pool()
        await PostgresAuthAdmission(pool, namespace="test").prepare(POLICY)
        assert pool.commits == 1
        assert sum("FOR UPDATE" in query for query in pool.connection_value.statements) == 5
        assert not any(query.startswith("UPDATE") for query in pool.connection_value.statements)
    asyncio.run(scenario())


@pytest.mark.parametrize("operation,subject,policy", [
    ("other", "a" * 64, POLICY), ("callback", "not-a-digest", POLICY),
    ("callback", "A" * 64, POLICY), ("callback", "a" * 64, RatePolicy(10001, 1, 60)),
])
def test_untrusted_inputs_are_rejected_before_repository_access(operation, subject, policy):
    async def scenario():
        pool = Pool()
        with pytest.raises(AuthConfigurationRejected):
            await PostgresAuthAdmission(pool, namespace="test").consume(operation, SecretStr(subject), policy)
        assert pool.connection_value.statements == []
    asyncio.run(scenario())


def test_external_cancellation_never_becomes_an_allowance():
    async def scenario():
        pool = Pool()
        pool.commit_error = asyncio.CancelledError()
        with pytest.raises(asyncio.CancelledError):
            await AuthAdmission(PostgresAuthAdmission(pool, namespace="test"), POLICY, timeout_seconds=1).check("callback", SecretStr("a" * 64))
        assert pool.commits == 0
    asyncio.run(scenario())


@pytest.mark.parametrize("row,expected", [((5, True), True), ((4, True), False), ((5, False), False),
    ((True, True), False), (None, False), ((5,), False)])
def test_readiness_requires_all_five_matching_current_policies_without_quota_writes(row, expected):
    async def scenario():
        pool = Pool()
        pool.connection_value.observed = row
        assert await PostgresAuthAdmission(pool, namespace="test").is_ready(POLICY) is expected
        assert all(query.startswith("SELECT") for query in pool.connection_value.statements)
    asyncio.run(scenario())
