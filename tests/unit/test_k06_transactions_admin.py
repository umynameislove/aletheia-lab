"""Operator boundaries, read-only verify and fixed safe administration failures."""
import argparse
import asyncio
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "k06_transactions_admin.py"
spec = importlib.util.spec_from_file_location("k06_transactions_admin", SCRIPT)
admin = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = admin
spec.loader.exec_module(admin)


@pytest.mark.parametrize("schema", ["public", "pg_catalog", "information_schema", "x;DROP", "UPPER", ""])
def test_admin_requires_explicit_application_schema(schema):
    with pytest.raises(SystemExit):
        admin.arguments(["migrate", "--schema", schema])


def test_admin_accepts_only_environment_reference_for_database_secret():
    with pytest.raises(SystemExit):
        admin.arguments(["verify", "--schema", "synthetic", "--database-url-env", "postgresql://private"])
    options = admin.arguments(["verify", "--schema", "synthetic", "--database-url-env", "K06_SYNTHETIC_DATABASE"])
    assert options.operation == "verify" and options.database_url_env == "K06_SYNTHETIC_DATABASE"


@pytest.mark.parametrize("operation", ["migrate", "verify"])
def test_admin_verifies_inside_bounded_transaction_and_reports_after_commit(monkeypatch, operation):
    events = []

    class Connection:
        async def __aenter__(self):
            events.append("enter")
            return self

        async def __aexit__(self, *args):
            events.append("commit" if args[0] is None else "rollback")

        async def execute(self, query):
            events.append(query)

        @staticmethod
        async def connect(database, *, connect_timeout):
            assert database == "synthetic-private-dsn" and connect_timeout == 10
            return Connection()

    async def migrate(conn, schema):
        assert schema == "synthetic"
        events.append("migrate")
        return "created"

    async def verify(conn, schema):
        assert schema == "synthetic"
        events.append("verify")
        return True

    monkeypatch.setenv("K06_SYNTHETIC_DATABASE", "synthetic-private-dsn")
    monkeypatch.setitem(sys.modules, "psycopg", SimpleNamespace(AsyncConnection=Connection))
    monkeypatch.setattr(admin, "migrate", migrate)
    monkeypatch.setattr(admin, "verify", verify)
    result = asyncio.run(admin.execute(argparse.Namespace(operation=operation, schema="synthetic",
                                                          database_url_env="K06_SYNTHETIC_DATABASE")))
    assert events == ["enter", "SET LOCAL lock_timeout='5s'", "SET LOCAL statement_timeout='30s'",
                      *(["migrate"] if operation == "migrate" else []), "verify", "commit"]
    assert result["audit_schema"] == "verified"
    assert ("audit_migration" in result) == (operation == "migrate")


def test_admin_logs_no_database_diagnostics_on_failure(monkeypatch, capsys):
    async def failing(options):
        raise RuntimeError("postgresql://synthetic-password@private-host/private-database")

    monkeypatch.setattr(admin, "execute", failing)
    assert admin.main(["verify", "--schema", "synthetic"]) == 1
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == "Transaction administration failed; database details were not logged.\n"
