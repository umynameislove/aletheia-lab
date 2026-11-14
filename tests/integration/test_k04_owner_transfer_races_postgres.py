"""A29: current API LOGIN authority survives owner-transfer/member/invite races."""
from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager

import pytest
from pydantic import SecretStr
from test_k04_runtime_principal_postgres import _credential, _principals, _run

from aletheia_lab.hosted.workspace_management import (
    WorkspaceConflict,
    WorkspaceDenied,
    WorkspaceInvitationError,
    WorkspaceManagement,
)

pytestmark = [pytest.mark.integration, pytest.mark.skipif(not os.environ.get("K04_TEST_DATABASE_URL"),
    reason="Explicit K-04 PostgreSQL test URL required")]


class _HoldingManagement(WorkspaceManagement):
    def __init__(self, pool):
        super().__init__(pool)
        self.locked, self.release = asyncio.Event(), asyncio.Event()
        self.pid = None

    async def _workspace(self, conn, workspace):
        name = await super()._workspace(conn, workspace)
        self.pid = (await (await conn.execute("SELECT pg_backend_pid()")).fetchone())[0]
        self.locked.set()
        await self.release.wait()
        return name


class _ObservedPool:
    def __init__(self, pool):
        self.pool, self.entered = pool, asyncio.Queue()

    @asynccontextmanager
    async def connection(self):
        async with self.pool.connection() as conn:
            pid = (await (await conn.execute("SELECT pg_backend_pid()")).fetchone())[0]
            self.entered.put_nowait(pid)
            yield conn


async def _outcome(call):
    try:
        return "committed", await call()
    except (WorkspaceConflict, WorkspaceDenied) as error:
        return "blocked", type(error).__name__


async def _race(pool, observer, first_call, second_call):
    holder = _HoldingManagement(pool)
    observed = _ObservedPool(pool)
    contender = WorkspaceManagement(observed)
    tasks = [asyncio.create_task(_outcome(lambda: first_call(holder)))]
    try:
        async with asyncio.timeout(8):
            await holder.locked.wait()
            tasks.append(asyncio.create_task(_outcome(lambda: second_call(contender))))
            contender_pid = await observed.entered.get()
            assert holder.pid != contender_pid
            # Observe the real lock wait; task scheduling alone is not a race barrier.
            async with asyncio.timeout(3):
                async with observer.connection() as conn:
                    while await (await conn.execute("SELECT %s=ANY(pg_blocking_pids(%s))",
                                                    (holder.pid, contender_pid))).fetchone() != (True,):
                        pass
            holder.release.set()
            return await asyncio.gather(*tasks)
    finally:
        holder.release.set()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def _join(manager, workspace, user, role):
    invitation = await manager.invite(credential_digest=_credential("owner_a"), workspace_id=workspace,
        user_id=user, role=role, expires_in_seconds=600)
    accepted = await manager.accept_invite(credential_digest=_credential(user), token=SecretStr(invitation["token"]))
    assert accepted["membership"] == {"role": role, "revision": 1}


async def _members(pool, workspace):
    async with pool.connection() as conn:
        cursor = await conn.execute("SELECT user_id,role,active,revision FROM j01_memberships "
                                    "WHERE workspace_id=%s ORDER BY user_id", (workspace,))
        rows = await cursor.fetchall()
        usable = await (await conn.execute("SELECT count(*) FROM j01_memberships m "
            "JOIN hosted_identity_bindings a ON a.user_id=m.user_id "
            "WHERE m.workspace_id=%s AND m.active AND m.role='owner' AND a.enabled AND a.email_verified",
            (workspace,))).fetchone()
        assert usable == (1,)
        return {row[0]: row[1:] for row in rows}


@pytest.mark.parametrize("first_action", ["transfer", "remove"])
def test_real_api_owner_transfer_racing_admin_remove_has_one_safe_linearized_effect(first_action):
    from psycopg_pool import AsyncConnectionPool

    async def scenario():
        async with (
            _principals() as (owner, api, _worker, _schema, api_role, _worker_role, _admin),
            AsyncConnectionPool(api.conninfo, min_size=2, max_size=2, open=False) as racing,
        ):
            await racing.wait(timeout=10)
            async with racing.connection() as conn:
                assert await (await conn.execute("SELECT current_user,session_user")).fetchone() == (api_role, api_role)
            manager = WorkspaceManagement(racing)
            workspace = (await manager.create(credential_digest=_credential("owner_a"), name="A29 synthetic"))["workspace_id"]
            await _join(manager, workspace, "analyst", "analyst")
            await _join(manager, workspace, "owner_b", "admin")

            def transfer(service):
                return service.transfer_owner(credential_digest=_credential("owner_a"), workspace_id=workspace,
                                              user_id="analyst", expected_revision=1)

            def remove(service):
                return service.mutate_member(credential_digest=_credential("owner_b"), workspace_id=workspace,
                                             user_id="analyst", expected_revision=1, active=False)

            first, second = (transfer, remove) if first_action == "transfer" else (remove, transfer)
            outcomes = await _race(racing, owner, first, second)
            committed = [value for status, value in outcomes if status == "committed"]
            assert len(committed) == 1 and sum(status == "blocked" for status, _ in outcomes) == 1
            assert committed[0]["workspace_id"] == workspace and committed[0]["user_id"] == "analyst"
            assert committed[0]["revision"] == 2
            state = await _members(racing, workspace)
            assert state in (
                {"owner_a": ("admin", True, 2), "analyst": ("owner", True, 2), "owner_b": ("admin", True, 1)},
                {"owner_a": ("owner", True, 1), "analyst": ("analyst", False, 2), "owner_b": ("admin", True, 1)},
            )
            assert state["analyst"] == (committed[0]["role"], committed[0]["active"], committed[0]["revision"])
            async with racing.connection() as conn:
                account = await (await conn.execute("SELECT onboarding,last_workspace_id "
                    "FROM hosted_identity_bindings WHERE user_id='analyst'")).fetchone()
                assert account == (("ready", workspace) if state["analyst"][1] else ("needs_workspace", None))

    _run(scenario())


def test_real_api_transfer_racing_invitation_preserves_creator_revision_and_explicit_acceptance():
    from psycopg_pool import AsyncConnectionPool

    async def scenario():
        async with (
            _principals() as (owner, api, _worker, _schema, api_role, _worker_role, _admin),
            AsyncConnectionPool(api.conninfo, min_size=2, max_size=2, open=False) as racing,
        ):
            await racing.wait(timeout=10)
            async with racing.connection() as conn:
                assert await (await conn.execute("SELECT current_user,session_user")).fetchone() == (api_role, api_role)
            manager = WorkspaceManagement(racing)
            workspace = (await manager.create(credential_digest=_credential("owner_a"), name="A29 invitation"))["workspace_id"]
            await _join(manager, workspace, "analyst", "analyst")

            def invite(service):
                return service.invite(credential_digest=_credential("owner_a"), workspace_id=workspace,
                                      user_id="owner_b", role="viewer", expires_in_seconds=600)

            def transfer(service):
                return service.transfer_owner(credential_digest=_credential("owner_a"), workspace_id=workspace,
                                              user_id="analyst", expected_revision=1)

            issued, transferred = await _race(racing, owner, invite, transfer)
            assert issued[0] == transferred[0] == "committed"
            invitation = issued[1]
            with pytest.raises(WorkspaceInvitationError) as unavailable:
                await manager.accept_invite(credential_digest=_credential("owner_b"),
                                            token=SecretStr(invitation["token"]))
            assert (unavailable.value.status, unavailable.value.code) == (404, "invitation_unavailable")
            assert await _members(racing, workspace) == {
                "owner_a": ("admin", True, 2), "analyst": ("owner", True, 2)}
            async with racing.connection() as conn:
                saved = await (await conn.execute("SELECT creator_id,creator_revision,revoked,accepted_at "
                    "FROM j01_invitations WHERE invitation_id=%s", (invitation["invitation_id"],))).fetchone()
                assert saved == ("owner_a", 1, False, None)
                assert await (await conn.execute("SELECT onboarding,last_workspace_id FROM hosted_identity_bindings "
                    "WHERE user_id='owner_b'")).fetchone() == ("needs_workspace", None)
            # The former owner remains an authorized admin; a fresh invite is a new decision.
            fresh = await invite(manager)
            accepted = await manager.accept_invite(credential_digest=_credential("owner_b"), token=SecretStr(fresh["token"]))
            assert accepted["membership"] == {"role": "viewer", "revision": 1}
            assert (await _members(racing, workspace))["owner_b"] == ("viewer", True, 1)

    _run(scenario())
