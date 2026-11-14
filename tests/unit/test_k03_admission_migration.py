"""Migration boundaries without a database; real rollout is an explicit smoke CLI."""

import asyncio
import sys
from types import SimpleNamespace

import pytest

from aletheia_lab.hosted_auth import admission_migrate_cli
from aletheia_lab.hosted_auth.abuse import RatePolicy
from aletheia_lab.hosted_auth.admission_migration import (
    MigrationRejected,
    _create,
    change_policy,
    migrate,
    migration_schema,
)


class Connection:
    autocommit = False

    def __init__(self):
        self.calls = []

    async def execute(self, query, params=()):
        self.calls.append((query, params))
        raise AssertionError("rejected input must not reach SQL")


@pytest.mark.parametrize("schema", [None, "", "public", "pg_catalog", "pg_temp_1", "information_schema",
    "schema;DROP", "schema.other", 'schema"', "with space", "a" * 64, "UpperCase"])
def test_migration_refuses_implicit_system_or_ambiguous_schema(schema):
    async def scenario():
        connection = Connection()
        with pytest.raises(MigrationRejected):
            await migrate(connection, schema)
        assert connection.calls == []
    asyncio.run(scenario())


def test_explicit_lowercase_schema_is_accepted_without_quoting_ambiguity():
    assert migration_schema("k03_local_123") == "k03_local_123"


def test_autocommit_cannot_leave_half_applied_migration():
    async def scenario():
        connection = Connection()
        connection.autocommit = True
        with pytest.raises(MigrationRejected):
            await migrate(connection, "k03_local_123")
        assert connection.calls == []
    asyncio.run(scenario())


@pytest.mark.parametrize("namespace", ["", "other/schema", "a" * 129])
def test_policy_change_refuses_untrusted_namespace_before_migration(namespace):
    async def scenario():
        connection = Connection()
        with pytest.raises(MigrationRejected):
            await change_policy(connection, "k03_local_123", namespace,
                expected=RatePolicy(9, 3, 60), replacement=RatePolicy(12, 4, 60))
        assert connection.calls == []
    asyncio.run(scenario())


def test_migration_materialization_changes_only_the_known_table_destination():
    ddl = "CREATE TABLE k03_auth_admission (attempts integer);"
    assert _create(ddl, "k03_auth_admission", '"k03_local_123"."k03_auth_admission"') == 'CREATE TABLE "k03_local_123"."k03_auth_admission" (attempts integer);'
    assert _create(ddl, "k03_auth_admission", "k03_expected", temporary=True).endswith("ON COMMIT DROP;")
    with pytest.raises(MigrationRejected):
        _create("SELECT 1;", "k03_auth_admission", "k03_expected")


def test_cli_rejects_bad_schema_without_echoing_connection_or_secret(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["migrate", "--schema", "public", "migrate"])
    monkeypatch.setenv("ALETHEIA_HOSTED_MIGRATION_DATABASE_URL", "postgresql://synthetic:private-secret@localhost/db")
    assert admission_migrate_cli.main() == 1
    result = capsys.readouterr()
    assert "private-secret" not in result.err + result.out
    assert result.err.strip() == "K03_ADMISSION_MIGRATION=FAILED_OR_UNKNOWN"


def test_cli_never_reports_applied_before_commit_or_echoes_driver_details(monkeypatch, capsys):
    class FailingCommit:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            raise RuntimeError("synthetic-private-commit-detail")

    async def connect(*args, **kwargs):
        return FailingCommit()

    async def migration(*args):
        return "created"

    monkeypatch.setitem(sys.modules, "psycopg", SimpleNamespace(AsyncConnection=SimpleNamespace(connect=connect)))
    monkeypatch.setattr(admission_migrate_cli, "migrate", migration)
    monkeypatch.setattr(admission_migrate_cli.asyncio, "set_event_loop_policy", lambda policy: None)
    monkeypatch.setattr(sys, "argv", ["migrate", "--schema", "k03_local_123", "migrate"])
    monkeypatch.setenv("ALETHEIA_HOSTED_MIGRATION_DATABASE_URL", "postgresql://synthetic:private-secret@localhost/db")
    assert admission_migrate_cli.main() == 1
    result = capsys.readouterr()
    assert result.out == "" and result.err.strip() == "K03_ADMISSION_MIGRATION=FAILED_OR_UNKNOWN"
