"""Trusted manual revoke uses existing operator/transaction boundaries; offline only."""

import asyncio
import importlib.util
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import SecretStr, ValidationError

from aletheia_lab.hosted_auth.postgres_sessions import PostgresSessionStore
from aletheia_lab.hosted_auth.repositories import RepositoryDataRejected

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "k04_workspace_admin.py"
_SPEC = importlib.util.spec_from_file_location("k03_operator_admin_test", _SCRIPT)
admin = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = admin
_SPEC.loader.exec_module(admin)
_ISSUER, _SUBJECT, _USER = "https://issuer.example.invalid/realm", "synthetic-private-subject", "user_synthetic_a"
_NOW = datetime(2026, 11, 10, tzinfo=UTC)


class Connection:
    autocommit = False

    def __init__(self, *, account=(_USER,), owner=("operator", "operator", "operator"), relations=None,
                 clock=(_NOW,), fail=None):
        self.account, self.owner, self.clock = account, owner, clock
        self.relations = relations or {"hosted_identity_bindings": ("r", "operator"),
                                      "j01_sessions": ("r", "operator")}
        self.fail, self.calls = fail, []
        self.pending, self.committed, self.exits = [], [], []

    async def execute(self, query, params=()):
        if not isinstance(query, str):
            query = query.as_string()
        self.calls.append((query, params))
        if self.fail is not None and self.fail in query:
            raise RuntimeError("synthetic-private-database-detail")
        if query.startswith("UPDATE "):
            self.pending.append((query, params))
        row = None
        if "SELECT current_user,session_user" in query:
            row = self.owner
        elif "FROM pg_catalog.pg_class" in query:
            row = self.relations.get(params[1])
        elif "SELECT user_id FROM " in query:
            row = self.account
        elif "SELECT pg_catalog.clock_timestamp()" in query:
            row = self.clock

        async def fetchone():
            return row

        return SimpleNamespace(fetchone=fetchone)

    async def __aenter__(self):
        return self

    async def __aexit__(self, kind, value, traceback):
        self.exits.append(kind)
        if kind is None:
            self.committed.extend(self.pending)
        self.pending.clear()


def _revoke(connection, **changes):
    options = dict(schema="synthetic", issuer=_ISSUER, subject=SecretStr(_SUBJECT), expected_issuer=_ISSUER)
    options.update(changes)
    return PostgresSessionStore.revoke_identity(connection, **options)


def test_operator_locks_exact_account_before_clock_and_enlists_only_its_sessions():
    connection = Connection()
    assert asyncio.run(_revoke(connection)) is True
    queries = [query for query, _ in connection.calls]
    assert queries[3] == ('SELECT user_id FROM "synthetic".hosted_identity_bindings '
                          'WHERE issuer=%s AND subject=%s FOR UPDATE')
    assert connection.calls[3][1] == (_ISSUER, _SUBJECT)
    assert queries[4] == "SELECT pg_catalog.clock_timestamp()"
    assert connection.calls[5] == (
        'UPDATE "synthetic".hosted_identity_bindings SET account_epoch=account_epoch+1, '
        'logout_all_at=GREATEST(logout_all_at,%s) WHERE user_id=%s', (_NOW, _USER))
    assert connection.calls[6] == (
        'UPDATE "synthetic".j01_sessions SET state=\'revoked\' WHERE user_id=%s AND state=\'active\'', (_USER,))
    assert connection.exits == connection.committed == []  # Caller owns commit/rollback.
    assert all(word not in " ".join(queries) for word in ("SET enabled", "SET email_verified", "j01_memberships", "INSERT"))


def test_unknown_exact_identity_has_no_clock_or_mutations_or_account_linking():
    connection = Connection(account=None)
    assert asyncio.run(_revoke(connection)) is False
    assert len(connection.calls) == 4 and connection.pending == []
    assert connection.calls[-1][1] == (_ISSUER, _SUBJECT)


@pytest.mark.parametrize("schema", ["public", "pg_catalog", "information_schema", "", "a;drop schema x", "Upper"])
def test_operator_refuses_unsafe_namespace_before_database(schema):
    connection = Connection()
    with pytest.raises(ValueError):
        asyncio.run(_revoke(connection, schema=schema))
    assert connection.calls == []


@pytest.mark.parametrize("changes", [
    {"issuer": "https://foreign.example.invalid"}, {"issuer": ""}, {"issuer": "x" * 2049},
    {"issuer": _ISSUER + "\n"}, {"subject": _SUBJECT}, {"subject": SecretStr("")},
    {"subject": SecretStr("x" * 256)}, {"subject": SecretStr(_SUBJECT + "\x7f")},
])
def test_operator_rejects_bad_identity_without_query_or_secret_message(changes):
    connection = Connection()
    with pytest.raises(ValueError) as caught:
        asyncio.run(_revoke(connection, **changes))
    assert _SUBJECT not in str(caught.value) + repr(caught.value)
    assert connection.calls == []


def test_operator_requires_caller_transaction_before_queries():
    connection = Connection()
    connection.autocommit = True
    with pytest.raises(RuntimeError, match="transactional connection required"):
        asyncio.run(_revoke(connection))
    assert connection.calls == []


@pytest.mark.parametrize("owner", [None, ("api", "api", "operator"), ("operator", "login", "operator"),
                                  ("operator", "operator"), (True, True, True)])
def test_operator_requires_actual_login_schema_owner(owner):
    connection = Connection(owner=owner)
    with pytest.raises(PermissionError):
        asyncio.run(_revoke(connection))
    assert len(connection.calls) == 1 and connection.pending == []


@pytest.mark.parametrize("relation", [None, ("v", "operator"), ("p", "operator"), ("r", "other")])
def test_exact_schema_owned_normal_relations_required_without_public_fallback(relation):
    connection = Connection(relations={"hosted_identity_bindings": relation, "j01_sessions": ("r", "operator")})
    with pytest.raises(PermissionError):
        asyncio.run(_revoke(connection))
    assert connection.pending == []
    assert connection.calls[1][1] == ("synthetic", "hosted_identity_bindings")
    assert all("to_regclass" not in query and "public." not in query for query, _ in connection.calls)


@pytest.mark.parametrize("account,clock", [((True,), (_NOW,)), ((), (_NOW,)), ((_USER,), None),
                                         ((_USER,), (_NOW.replace(tzinfo=None),)), ((_USER,), ("private-clock",))])
def test_invalid_locked_account_or_database_clock_cannot_write(account, clock):
    connection = Connection(account=account, clock=clock)
    with pytest.raises(RepositoryDataRejected):
        asyncio.run(_revoke(connection))
    assert connection.pending == []


def _driver(monkeypatch, connection):
    from psycopg import sql

    async def connect(database, **kwargs):
        assert database == "synthetic-private-database-url" and kwargs == {"connect_timeout": 10}
        return connection

    monkeypatch.setitem(sys.modules, "psycopg", SimpleNamespace(AsyncConnection=SimpleNamespace(connect=connect), sql=sql))
    monkeypatch.setenv("K03_OPERATOR_TEST_DATABASE", "synthetic-private-database-url")


def _options(tmp_path, *, payload=None, operation="revoke-sessions"):
    document = tmp_path / "identity.json"
    document.write_text(json.dumps(payload if payload is not None else {"issuer": _ISSUER, "subject": _SUBJECT}),
                        encoding="utf-8")
    return admin.arguments([operation, "--schema", "synthetic", "--identity-file", str(document),
                            "--expected-issuer", _ISSUER, "--database-url-env", "K03_OPERATOR_TEST_DATABASE"])


def test_cli_revocation_uses_existing_transaction_and_reports_safe_receipt_after_commit(monkeypatch, tmp_path):
    connection = Connection()
    _driver(monkeypatch, connection)
    receipt = asyncio.run(admin.execute(_options(tmp_path)))
    assert receipt == {"sessions_revoked": True}
    assert connection.exits == [None] and len(connection.committed) == 2
    assert [query for query, _ in connection.calls[:3]] == ["SET LOCAL lock_timeout='5s'",
        "SET LOCAL statement_timeout='30s'", 'SET LOCAL search_path TO "synthetic",pg_catalog']
    assert _SUBJECT not in json.dumps(receipt) and "schema" not in receipt


def test_failure_of_session_revoke_rolls_back_both_effects_and_emits_only_fixed_failure(monkeypatch, tmp_path, capsys):
    connection = Connection(fail='UPDATE "synthetic".j01_sessions')
    _driver(monkeypatch, connection)
    options = _options(tmp_path)
    assert admin.main(["revoke-sessions", "--schema", options.schema, "--identity-file", str(options.identity_file),
                       "--expected-issuer", _ISSUER, "--database-url-env", options.database_url_env]) == 1
    assert connection.exits == [RuntimeError] and connection.pending == connection.committed == []
    output = capsys.readouterr()
    assert output.out == "" and output.err == "Workspace administration failed; no credentials or identity details were logged.\n"


@pytest.mark.parametrize("arguments", [
    ["--subject", _SUBJECT], ["--token", "synthetic-private-token"],
    ["--database-url-env", "postgresql://synthetic-private-password"],
])
def test_cli_never_accepts_secret_arguments_or_echoes_their_value(arguments, capsys):
    with pytest.raises(SystemExit):
        admin.arguments(["revoke-sessions", "--schema", "synthetic", "--identity-file", "identity.json",
                         "--expected-issuer", _ISSUER, *arguments])
    output = capsys.readouterr()
    assert "synthetic-private" not in output.out + output.err


@pytest.mark.parametrize("extra", [{"email": "private@example.invalid"}, {"role": "owner"}, {"token": "private"}])
def test_revocation_document_accepts_only_exact_issuer_subject(extra):
    with pytest.raises(ValidationError):
        admin.RevocationInput.model_validate({"issuer": _ISSUER, "subject": _SUBJECT, **extra})
    assert _SUBJECT not in repr(admin.RevocationInput(issuer=_ISSUER, subject=_SUBJECT))


@pytest.mark.parametrize("flags", [[], ["--identity-file", "identity.json"], ["--expected-issuer", _ISSUER]])
def test_revocation_requires_bounded_document_and_explicit_issuer(flags):
    with pytest.raises(SystemExit):
        admin.arguments(["revoke-sessions", "--schema", "synthetic", *flags])


@pytest.mark.parametrize("changes", [{"issuer": "https://issuer\nprivate"}, {"subject": ""},
                                     {"subject": "x" * 256}, {"subject": "private\x00"}, {"subject": True}])
def test_revocation_document_rejects_unbounded_or_control_input(changes):
    with pytest.raises(ValidationError) as caught:
        admin.RevocationInput.model_validate({"issuer": _ISSUER, "subject": _SUBJECT, **changes})
    assert _SUBJECT not in str(caught.value) + repr(caught.value)


@pytest.mark.parametrize("payload", [b"x" * 8193, b"[broken", b'{"issuer":"https://foreign","subject":"private"}',
                                     b'{"issuer":"https://issuer","subject":"a","subject":"b"}'])
def test_bad_or_oversized_identity_document_fails_before_opening_database(monkeypatch, tmp_path, payload):
    options = _options(tmp_path)
    options.identity_file.write_bytes(payload)

    async def forbidden(*args, **kwargs):
        raise AssertionError("invalid input must not connect")

    monkeypatch.setitem(sys.modules, "psycopg", SimpleNamespace(AsyncConnection=SimpleNamespace(connect=forbidden), sql=object()))
    monkeypatch.setenv("K03_OPERATOR_TEST_DATABASE", "synthetic-private-database-url")
    with pytest.raises(ValueError):
        asyncio.run(admin.execute(options))


@pytest.mark.parametrize("operation", ["migrate", "provision"])
def test_existing_operator_operations_keep_their_canonical_callers(monkeypatch, tmp_path, operation):
    connection, calls = Connection(), []
    _driver(monkeypatch, connection)
    payload = {"identity": {"issuer": _ISSUER, "subject": _SUBJECT, "email": "synthetic@example.invalid",
                             "email_verified": True}, "display_name": "Synthetic"}

    async def migrate(conn, schema):
        calls.append(("migrate", conn, schema))
        return "unchanged"

    async def verify(conn):
        calls.append(("verify", conn))
        return True

    async def provision(conn, identity, **options):
        calls.append(("provision", conn, identity.subject.get_secret_value(), options))
        return {"user_id": _USER, "onboarding": "needs_workspace"}

    monkeypatch.setattr(admin, "migrate", migrate)
    monkeypatch.setattr(admin, "verify", verify)
    monkeypatch.setattr(admin, "provision_identity", provision)
    receipt = asyncio.run(admin.execute(_options(tmp_path, payload=payload, operation=operation)))
    assert connection.exits == [None] and connection.pending == []
    assert [call[0] for call in calls] == (["migrate"] if operation == "migrate" else ["verify", "provision"])
    assert receipt == ({"migration": "unchanged"} if operation == "migrate" else
                       {"user_id": _USER, "onboarding": "needs_workspace"})


def test_cookie_logout_all_reuses_monotonic_account_revoke_helper(monkeypatch):
    connection = Connection()

    async def locked(conn, digest):
        assert conn is connection
        return ("context", _USER, "active", 0, 0, True, 0, _NOW + timedelta(seconds=1),
                _NOW + timedelta(seconds=1), _NOW, _NOW, "active", 0, _NOW)

    monkeypatch.setattr(PostgresSessionStore, "_lock", staticmethod(locked))
    pool = SimpleNamespace(connection=lambda: connection)
    assert asyncio.run(PostgresSessionStore(pool).revoke_presented(SecretStr("a" * 64), all_sessions=True))
    assert len(connection.committed) == 2
    assert "logout_all_at=GREATEST(logout_all_at,%s)" in connection.committed[0][0]
    assert connection.committed[0][1] == (_NOW, _USER)
    assert connection.committed[1][1] == (_USER,)
