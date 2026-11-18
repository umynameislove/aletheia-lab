"""Workspace readiness checks real write/lock privileges without mutating state."""
from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack, asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from test_k04_followup_runtime import _managed

from aletheia_lab.hosted import managed_runtime, workspace_migration
from aletheia_lab.hosted.workspace_management import WorkspaceManagement


@pytest.mark.parametrize("writable", [True, False, None])
def test_workspace_readiness_checks_column_and_row_lock_permissions_without_effects(monkeypatch, writable):
    seen = []

    class Connection:
        async def execute(self, query, params=None):
            seen.append((query, params))
            return SimpleNamespace(fetchone=AsyncMock(return_value=(writable,)))

    @asynccontextmanager
    async def connection():
        yield Connection()

    verify = AsyncMock(return_value=True)
    monkeypatch.setattr(workspace_migration, "verify", verify)
    manager = WorkspaceManagement(SimpleNamespace(connection=connection))
    assert asyncio.run(manager.is_ready()) is (writable is True)
    assert all(query.startswith("SELECT ") for query, _ in seen)
    checks = [(query, params) for query, params in seen if "has_column_privilege" in query]
    assert len(checks) == 1
    query, (relations, columns, permissions) = checks[0]
    required = set(zip(relations, columns, permissions, strict=True))
    assert ("j01_invitations", "role", "INSERT") in required
    assert ("j01_memberships", "active", "UPDATE") in required
    assert ("j01_workspaces", "name", "UPDATE") in required
    assert ("j01_source_grants", "actions", "UPDATE") in required
    assert ("hosted_identity_bindings", "last_workspace_id", "UPDATE") in required
    assert not ({"enabled", "email_verified", "issuer", "subject", "payload"} & set(columns))
    for table in ("j01_sessions", "j01_session_credentials", "j01_sources"):
        assert f"has_any_column_privilege(current_user,'{table}','UPDATE')" in query
    assert "k06_action_audit" not in query
    assert verify.await_count == (writable is True)


@pytest.mark.parametrize("failure", [PermissionError("private storage detail"), RuntimeError("private SQL detail")])
def test_workspace_readiness_does_not_leak_database_errors(monkeypatch, failure):
    class Connection:
        async def execute(self, query, params=None):
            raise failure

    @asynccontextmanager
    async def connection():
        yield Connection()

    manager = WorkspaceManagement(SimpleNamespace(connection=connection))
    assert asyncio.run(manager.is_ready()) is False


def test_workspace_readiness_preserves_external_cancellation():
    @asynccontextmanager
    async def connection():
        raise asyncio.CancelledError()
        yield

    manager = WorkspaceManagement(SimpleNamespace(connection=connection))
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(manager.is_ready())


@pytest.mark.parametrize("unavailable", ["denied", "error", "timeout"])
def test_managed_clock_rechecks_workspace_dependency_before_protected_routes(tmp_path, monkeypatch, unavailable):
    runtime, captured, probes = _managed(tmp_path, monkeypatch)
    readiness = AsyncMock(return_value=True)
    monkeypatch.setattr(managed_runtime, "PostgresWorkspaceManagement", lambda *args, **kwargs:
                        SimpleNamespace(is_ready=readiness))

    async def slow():
        await asyncio.Event().wait()

    async def scenario():
        async with AsyncExitStack() as stack:
            await runtime._compose(stack)
            assert await captured["clock_check"]() is True
            if unavailable == "denied":
                readiness.return_value = False
            elif unavailable == "error":
                readiness.side_effect = PermissionError("private database detail")
            else:
                readiness.side_effect = slow
            before = probes.catalog.await_count
            assert await captured["clock_check"]() is False
            assert probes.catalog.await_count == before
    asyncio.run(scenario())


def test_managed_workspace_readiness_keeps_external_cancellation(tmp_path, monkeypatch):
    runtime, captured, _ = _managed(tmp_path, monkeypatch)
    readiness = AsyncMock(side_effect=asyncio.CancelledError())
    monkeypatch.setattr(managed_runtime, "PostgresWorkspaceManagement", lambda *args, **kwargs:
                        SimpleNamespace(is_ready=readiness))

    async def scenario():
        async with AsyncExitStack() as stack:
            await runtime._compose(stack)
            with pytest.raises(asyncio.CancelledError):
                await captured["clock_check"]()
    asyncio.run(scenario())
