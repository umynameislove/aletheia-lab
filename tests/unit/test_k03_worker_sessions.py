"""Worker-only digest resolution; no database or services are opened."""

import asyncio

import pytest

from aletheia_lab.hosted import managed_runtime, managed_worker
from aletheia_lab.hosted.authorization import Authorization, PostgresAuthorization
from aletheia_lab.hosted.postgres_catalog import PostgresCatalog
from aletheia_lab.hosted.postgres_sources import PostgresSources, SourceNotFound
from aletheia_lab.hosted_auth.repositories import RepositoryDataRejected
from aletheia_lab.hosted_auth.worker_sessions import WorkerPostgresSources

_CAPTURED, _CURRENT = "a" * 64, "b" * 64
_USER, _CONTEXT = "usr_synthetic_a", "ctx_original"
_AUTH = Authorization(user_id=_USER, role="analyst", revision=3, workspace_id="ws_a")


class Cursor:
    def __init__(self, row):
        self.row = row

    async def fetchone(self):
        return self.row


class Connection:
    def __init__(self, rows=None, *, autocommit=False):
        self.autocommit = autocommit
        self.rows = iter(rows if rows is not None else [(_USER, _CONTEXT), (_USER,), (_CURRENT,)])
        self.calls = []

    async def execute(self, query, params=()):
        self.calls.append((query, params))
        return Cursor(next(self.rows))


def _source():
    return WorkerPostgresSources(object(), maximum_upload_bytes=1024, maximum_expanded_bytes=1024, maximum_entries=10)


@pytest.mark.parametrize("audit", [False, True])
def test_audit_forwarding_keeps_the_worker_authorization_guard(audit):
    pool = object()
    budgets = dict(maximum_upload_bytes=1024, maximum_expanded_bytes=1024, maximum_entries=10)
    worker = WorkerPostgresSources(pool, **budgets, audit=audit)
    canonical = PostgresSources(pool, **budgets, audit=audit)
    assert worker._pool is canonical._pool is pool
    assert worker._audit is canonical._audit is audit
    assert type(canonical._authorization) is PostgresAuthorization
    assert isinstance(worker._authorization, PostgresAuthorization)
    assert type(worker._authorization) is not PostgresAuthorization


@pytest.mark.parametrize("audit", [False, True])
@pytest.mark.parametrize("with_catalog", [False, True])
def test_catalog_and_audit_forward_once_without_replacing_worker_authorization(monkeypatch, audit, with_catalog):
    pool, calls = object(), []
    catalog = PostgresCatalog(pool, object()) if with_catalog else None
    budgets = dict(maximum_upload_bytes=1024, maximum_expanded_bytes=1024, maximum_entries=10)
    original = PostgresSources.__init__

    def initialize(self, *args, **kwargs):
        calls.append((args, kwargs))
        original(self, *args, **kwargs)

    monkeypatch.setattr(PostgresSources, "__init__", initialize)
    worker = WorkerPostgresSources(pool, **budgets, audit=audit, catalog=catalog)
    assert calls == [((pool,), {**budgets, "audit": audit, "catalog": catalog})]
    assert calls[0][1]["catalog"] is catalog and worker._audit is audit
    assert worker._pool is pool and worker._budgets == budgets
    assert isinstance(worker._authorization, PostgresAuthorization)
    assert type(worker._authorization) is not PostgresAuthorization


def _strict_parent(monkeypatch):
    calls = []

    async def strict(self, connection, digest, workspace_id, *, action):
        calls.append((connection, digest, workspace_id, action, len(connection.calls)))
        return _AUTH

    monkeypatch.setattr(PostgresSources, "_access", strict)
    return calls


@pytest.mark.parametrize("action,expected", [("read", "preview"), ("preview", "preview"),
                                           ("confirm", "confirm"), ("analyze", "analyze")])
def test_worker_resolves_exact_context_under_account_share_in_same_transaction(monkeypatch, action, expected):
    calls, connection = _strict_parent(monkeypatch), Connection()
    result = asyncio.run(_source()._access(connection, _CAPTURED, "ws_a", action=action))
    assert result is _AUTH
    assert calls == [(connection, _CURRENT, "ws_a", expected, 3)]
    anchor, params = connection.calls[0]
    assert "s.context_id=captured.context_id" in anchor and params == (_CAPTURED,)
    assert connection.calls[1] == (
        "SELECT user_id FROM hosted_identity_bindings WHERE user_id=%s FOR SHARE", (_USER,),
    )
    resolver, params = connection.calls[2]
    assert params == (_CAPTURED, _USER, _CONTEXT)
    assert "s.context_id=captured.context_id" in resolver
    assert "current_credential.context_id=s.context_id" in resolver
    assert "s.user_id=%s AND s.context_id=%s" in resolver
    assert "captured.generation<s.generation" in resolver
    assert "current_credential.state='active'" in resolver
    assert "current_credential.generation=s.generation" in resolver
    assert not any("pg_advisory" in query or "FOR UPDATE" in query for query, _ in connection.calls)


@pytest.mark.parametrize("digest", [None, True, 2, "", "synthetic-private-value", "A" * 64, "a" * 64 + "\n"])
def test_invalid_captured_digest_denies_before_database(monkeypatch, digest):
    calls, connection = _strict_parent(monkeypatch), Connection()
    with pytest.raises(SourceNotFound) as caught:
        asyncio.run(_source()._access(connection, digest, "ws_a", action="preview"))
    assert "synthetic-private-value" not in str(caught.value)
    assert connection.calls == calls == []


@pytest.mark.parametrize("rows", [[None], [(_USER, _CONTEXT), None],
                                 [(_USER, _CONTEXT), ("other-user",)],
                                 [(_USER, _CONTEXT), (_USER,), None]])
def test_missing_or_changed_anchor_account_and_current_credential_never_delegates(monkeypatch, rows):
    calls, connection = _strict_parent(monkeypatch), Connection(rows)
    with pytest.raises(SourceNotFound):
        asyncio.run(_source()._access(connection, _CAPTURED, "ws_a", action="preview"))
    assert calls == []


@pytest.mark.parametrize("row", [(), ("synthetic-private-value",), (True, _CONTEXT), (_USER, None), [_USER, _CONTEXT]])
def test_corrupt_captured_anchor_has_fixed_error(monkeypatch, row):
    calls, connection = _strict_parent(monkeypatch), Connection([row])
    with pytest.raises(RepositoryDataRejected) as caught:
        asyncio.run(_source()._access(connection, _CAPTURED, "ws_a", action="preview"))
    assert str(caught.value) == "Authentication storage returned invalid data."
    assert "synthetic-private-value" not in repr(caught.value)
    assert len(connection.calls) == 1 and calls == []


@pytest.mark.parametrize("row", [(), ("synthetic-private-value",), ("a" * 64 + "\n",),
                                 (True,), (None,), (_CURRENT, "extra"), [_CURRENT]])
def test_corrupt_current_digest_is_rejected_with_fixed_safe_error(monkeypatch, row):
    calls = _strict_parent(monkeypatch)
    connection = Connection([(_USER, _CONTEXT), (_USER,), row])
    with pytest.raises(RepositoryDataRejected) as caught:
        asyncio.run(_source()._access(connection, _CAPTURED, "ws_a", action="preview"))
    assert str(caught.value) == "Authentication storage returned invalid data."
    assert "synthetic-private-value" not in repr(caught.value)
    assert calls == []


def test_worker_access_requires_transaction_before_any_query(monkeypatch):
    calls, connection = _strict_parent(monkeypatch), Connection(autocommit=True)
    with pytest.raises(RuntimeError, match="transactional connection required"):
        asyncio.run(_source()._access(connection, _CAPTURED, "ws_a", action="preview"))
    assert connection.calls == calls == []


def test_every_worker_access_resolves_again_after_rotation(monkeypatch):
    calls = _strict_parent(monkeypatch)
    rows = [(_USER, _CONTEXT), (_USER,), (_CURRENT,), (_USER, _CONTEXT), (_USER,), ("c" * 64,)]
    connection, source = Connection(rows), _source()

    async def scenario():
        await source._access(connection, _CAPTURED, "ws_a", action="read")
        await source._access(connection, _CAPTURED, "ws_a", action="analyze")

    asyncio.run(scenario())
    assert [call[1] for call in calls] == [_CURRENT, "c" * 64]
    assert [params for query, params in connection.calls if "AND s.user_id=%s AND s.context_id=%s" in query] == [
        (_CAPTURED, _USER, _CONTEXT), (_CAPTURED, _USER, _CONTEXT),
    ]


def test_parent_current_account_membership_or_action_denial_propagates(monkeypatch):
    calls = []

    async def denied(self, connection, digest, workspace_id, *, action):
        calls.append((digest, workspace_id, action))
        raise SourceNotFound()

    monkeypatch.setattr(PostgresSources, "_access", denied)
    with pytest.raises(SourceNotFound):
        asyncio.run(_source()._access(Connection(), _CAPTURED, "ws_a", action="confirm"))
    assert calls == [(_CURRENT, "ws_a", "confirm")]


@pytest.mark.parametrize("action,expected", [("read", "preview"), ("preview", "preview"),
                                           ("confirm", "confirm"), ("analyze", "analyze")])
def test_worker_source_callback_always_delegates_exact_scope_and_writer_action(monkeypatch, action, expected):
    calls, connection = [], Connection()

    async def parent(self, conn, auth, workspace_id, source_id, checked_action):
        calls.append((conn, auth, workspace_id, source_id, checked_action))

    monkeypatch.setattr(PostgresAuthorization, "authorize_source", parent)
    asyncio.run(_source()._authorization.authorize_source(connection, _AUTH, "ws_a", "src_a", action))
    assert calls == [(connection, _AUTH, "ws_a", "src_a", expected)]


@pytest.mark.parametrize("grants,allowed", [(["read"], False), (["read", "write"], True)])
def test_worker_read_keeps_inherited_source_write_grant_enforcement(grants, allowed):
    def connection():
        return Connection([("analyst", 3, True), ("another-source-creator",), (grants,)])

    async def scenario():
        # API reads keep their existing read-only grant behavior.
        await PostgresAuthorization().authorize_source(connection(), _AUTH, "ws_a", "src_a", "read")
        if allowed:
            await _source()._authorization.authorize_source(connection(), _AUTH, "ws_a", "src_a", "read")
        else:
            with pytest.raises(SourceNotFound):
                await _source()._authorization.authorize_source(connection(), _AUTH, "ws_a", "src_a", "read")

    asyncio.run(scenario())


def test_external_cancellation_is_not_converted_into_authorization(monkeypatch):
    calls = _strict_parent(monkeypatch)

    class Cancelled(Connection):
        async def execute(self, query, params=()):
            raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(_source()._access(Cancelled(), _CAPTURED, "ws_a", action="preview"))
    assert calls == []


def test_only_managed_worker_wires_the_worker_adapter():
    assert managed_worker.PostgresSources is WorkerPostgresSources
    assert managed_runtime.PostgresSources is PostgresSources
    assert WorkerPostgresSources is not PostgresSources
