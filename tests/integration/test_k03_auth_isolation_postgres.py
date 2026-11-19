"""Real independent auth backends, browser CAS and account revocation fences."""
from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from psycopg_pool import AsyncConnectionPool
from pydantic import SecretBytes, SecretStr
from test_k04_runtime_principal_postgres import _credential, _principals, _run

from aletheia_lab.hosted_auth.login import BeginLogin, CommitLogin
from aletheia_lab.hosted_auth.login_proof_cipher import LoginProofCipher
from aletheia_lab.hosted_auth.postgres_fresh_login import PostgresFreshLogin
from aletheia_lab.hosted_auth.postgres_identity import PostgresIdentityDirectory
from aletheia_lab.hosted_auth.postgres_sessions import PostgresSessionStore
from aletheia_lab.hosted_auth.repositories import RotateSession, SessionRules

pytestmark = [pytest.mark.integration, pytest.mark.skipif(
    not os.environ.get("K03_TEST_DATABASE_URL"), reason="Explicit K03 private native PostgreSQL required")]
_RULES = SessionRules(900, 28800, 300)


@pytest.fixture(autouse=True)
def configured_database(monkeypatch):
    monkeypatch.setenv("K04_TEST_DATABASE_URL", os.environ["K03_TEST_DATABASE_URL"])


@asynccontextmanager
async def _system():
    async with _principals() as (owner, api, _, schema, _, _, admin), \
            AsyncConnectionPool(api.conninfo, min_size=1, max_size=1, open=False) as other:
        await other.wait(timeout=10)
        async with owner.connection() as conn:
            await conn.execute("UPDATE j01_sessions SET issued_at=statement_timestamp()-interval '600 seconds',"
                "last_seen_at=statement_timestamp()-interval '600 seconds',"
                "rotated_at=statement_timestamp()-interval '600 seconds'")
        yield SimpleNamespace(owner=owner, pools=(api, other), admin=admin, schema=schema)


def _login(pool):
    return PostgresFreshLogin(pool, LoginProofCipher(SecretBytes(b"k" * 32)))


async def _begin(login, *, browser=None, previous=None):
    now = datetime.now(UTC)
    command = BeginLogin("intent_" + uuid4().hex, browser or SecretStr(uuid4().hex * 2),
        SecretStr(uuid4().hex * 2), "issuer", "synthetic-client", "https://app.example.invalid/callback",
        SecretStr("n" * 43), SecretStr("v" * 43), now - timedelta(seconds=1), now + timedelta(seconds=120),
        "/app", False, previous)
    intent = await login.begin_intent(command)
    assert intent is not None
    return command, intent


async def _prepared(pool, user, *, previous=None):
    login = _login(pool)
    begin, intent = await _begin(login, previous=previous)
    claim = SecretStr(uuid4().hex * 2)
    assert await login.claim_intent(browser_digest=begin.browser_digest,
        state_digest=begin.state_digest, claim_digest=claim) is not None
    account = await PostgresIdentityDirectory(pool).find_identity(issuer="issuer", subject=SecretStr(user))
    assert account is not None
    return login, CommitLogin(intent.intent_id, begin.browser_digest, begin.state_digest, claim,
        intent.browser_epoch, account, SecretStr(uuid4().hex * 2), "ctx_" + uuid4().hex,
        SecretStr("synthetic-new-csrf"), _RULES, True)


async def _wait_blocked(admin, pid):
    async with asyncio.timeout(10):
        while (await (await admin.execute("SELECT cardinality(pg_blocking_pids(%s))>0", (pid,))).fetchone()) != (True,):
            await asyncio.sleep(0.01)


def test_blocked_account_does_not_block_other_login_renewal_or_logout():
    async def scenario():
        async with _system() as case:
            api, other = case.pools
            async with api.connection() as conn:
                pid = (await (await conn.execute("SELECT pg_backend_pid()")).fetchone())[0]
            a = RotateSession(_credential("owner_a"), SecretStr(uuid4().hex * 2),
                "owner_a", "ctx_owner_a", 0, 0, _RULES)
            async with case.owner.connection() as held:
                await held.execute("SELECT user_id FROM hosted_identity_bindings WHERE user_id='owner_a' FOR UPDATE")
                blocked = asyncio.create_task(PostgresSessionStore(api).rotate_if_current(a))
                try:
                    await _wait_blocked(case.admin, pid)
                    b = RotateSession(_credential("owner_b"), SecretStr(uuid4().hex * 2),
                        "owner_b", "ctx_owner_b", 0, 0, _RULES)
                    async with asyncio.timeout(5):
                        assert await PostgresSessionStore(other).rotate_if_current(b) is not None
                        assert await PostgresSessionStore(other).revoke_presented(b.replacement_digest, all_sessions=True)
                        login, command = await _prepared(other, "owner_b")
                        assert (await login.commit_login(command)).actor.user_id == "owner_b"
                    assert not blocked.done()
                finally:
                    await held.rollback()
                    if not blocked.done():
                        await asyncio.wait_for(blocked, 10)
            assert blocked.result() is not None
    _run(scenario())


def test_same_browser_latest_intent_has_one_claim_without_session_creation():
    async def scenario():
        async with _system() as case:
            one, two = map(_login, case.pools)
            browser = SecretStr("b" * 64)
            begins = await asyncio.gather(_begin(one, browser=browser), _begin(two, browser=browser))
            claims = await asyncio.gather(*(login.claim_intent(browser_digest=command.browser_digest,
                state_digest=command.state_digest, claim_digest=SecretStr(uuid4().hex * 2))
                for login, (command, _) in zip((one, two), begins, strict=True)))
            assert sum(value is not None for value in claims) == 1
            winner = next(value for value in claims if value is not None)
            assert winner.browser_epoch == 2
            assert await one.claim_intent(browser_digest=winner.browser_digest,
                state_digest=winner.state_digest, claim_digest=SecretStr(uuid4().hex * 2)) is None
            async with case.owner.connection() as conn:
                assert await (await conn.execute("SELECT count(*) FROM j01_fresh_login_intents WHERE phase='claimed'")).fetchone() == (1,)
                assert await (await conn.execute("SELECT count(*) FROM j01_sessions")).fetchone() == (3,)
    _run(scenario())


def test_pending_login_cannot_resurrect_account_after_logout_all_or_operator_revoke():
    async def scenario():
        async with _system() as case:
            api, other = case.pools
            login, pending = await _prepared(api, "owner_a")
            assert await PostgresSessionStore(other).revoke_presented(_credential("owner_a"), all_sessions=True)
            current = await PostgresIdentityDirectory(other).find_identity(issuer="issuer", subject=SecretStr("owner_a"))
            assert current.account_epoch == 1
            assert await login.commit_login(replace(pending, account=current)) is None
            fresh_login, fresh = await _prepared(api, "owner_a")
            assert await fresh_login.commit_login(fresh) is not None
            pending_login, pending = await _prepared(api, "owner_a")
            async with case.owner.connection() as conn:
                assert await PostgresSessionStore.revoke_identity(conn, schema=case.schema,
                    issuer="issuer", subject=SecretStr("owner_a"), expected_issuer="issuer")
            current = await PostgresIdentityDirectory(other).find_identity(issuer="issuer", subject=SecretStr("owner_a"))
            assert current.account_epoch == 2
            assert await pending_login.commit_login(replace(pending, account=current)) is None
            assert (await PostgresSessionStore(other).inspect(fresh.credential_digest)).state == "revoked"
            async with case.owner.connection() as conn:
                assert await (await conn.execute("SELECT count(*) FROM j01_sessions WHERE user_id='owner_a' AND state='active'")).fetchone() == (0,)
    _run(scenario())


def test_opposite_account_switches_use_ordered_accounts_without_deadlock():
    async def scenario():
        async with _system() as case:
            api, other = case.pools
            a_to_b, b_to_a = await asyncio.gather(
                _prepared(api, "owner_b", previous=_credential("owner_a")),
                _prepared(other, "owner_a", previous=_credential("owner_b")))
            async with asyncio.timeout(10):
                results = await asyncio.gather(*(login.commit_login(command) for login, command in (a_to_b, b_to_a)))
            assert [result.actor.user_id for result in results] == ["owner_b", "owner_a"]
            for pool, (_, command) in zip(case.pools, (a_to_b, b_to_a), strict=True):
                assert (await PostgresSessionStore(pool).inspect(command.credential_digest)).state == "active"
            async with case.owner.connection() as conn:
                assert await (await conn.execute("SELECT state FROM j01_sessions WHERE context_id IN ('ctx_owner_a','ctx_owner_b') ORDER BY context_id")).fetchall() == [("revoked",), ("revoked",)]
    _run(scenario())


def test_switch_rechecks_previous_account_after_waiting_for_its_lock():
    async def scenario():
        async with _system() as case:
            api, other = case.pools
            login, command = await _prepared(api, "owner_b", previous=_credential("owner_a"))
            async with api.connection() as conn:
                pid = (await (await conn.execute("SELECT pg_backend_pid()")).fetchone())[0]
            async with case.owner.connection() as held:
                await held.execute("SELECT user_id FROM hosted_identity_bindings WHERE user_id='owner_a' FOR UPDATE")
                switching = asyncio.create_task(login.commit_login(command))
                try:
                    await _wait_blocked(case.admin, pid)
                    assert await PostgresSessionStore.revoke_identity(held, schema=case.schema,
                        issuer="issuer", subject=SecretStr("owner_a"), expected_issuer="issuer")
                    await held.commit()
                finally:
                    await held.rollback()
                    if not switching.done():
                        await asyncio.wait_for(switching, 10)
            assert switching.result() is None
            assert await PostgresSessionStore(other).inspect(command.credential_digest) is None
            assert (await PostgresSessionStore(other).inspect(_credential("owner_b"))).state == "active"
    _run(scenario())


def test_new_begin_cannot_supersede_same_browser_commit_while_account_is_locked():
    async def scenario():
        async with _system() as case:
            api, other = case.pools
            login, command = await _prepared(api, "owner_a")
            pids = []
            for pool in case.pools:
                async with pool.connection() as conn:
                    pids.append((await (await conn.execute("SELECT pg_backend_pid()")).fetchone())[0])
            now = datetime.now(UTC)
            newer = BeginLogin("intent_" + uuid4().hex, command.browser_digest, SecretStr(uuid4().hex * 2),
                "issuer", "synthetic-client", "https://app.example.invalid/callback", SecretStr("n" * 43),
                SecretStr("v" * 43), now - timedelta(seconds=1), now + timedelta(seconds=120), "/app", False, None)
            async with case.owner.connection() as held:
                await held.execute("SELECT user_id FROM hosted_identity_bindings WHERE user_id='owner_a' FOR UPDATE")
                committing = asyncio.create_task(login.commit_login(command))
                beginning = None
                try:
                    await _wait_blocked(case.admin, pids[0])
                    beginning = asyncio.create_task(_login(other).begin_intent(newer))
                    await _wait_blocked(case.admin, pids[1])
                    assert not committing.done() and not beginning.done()
                finally:
                    await held.rollback()
                    tasks = [committing] + ([beginning] if beginning is not None else [])
                    await asyncio.wait_for(asyncio.gather(*tasks), 10)
            assert committing.result() is not None
            assert beginning.result() is None
            assert (await PostgresSessionStore(other).inspect(command.credential_digest)).state == "active"
    _run(scenario())


def test_claim_expiring_during_account_lock_wait_cannot_publish_a_session():
    async def scenario():
        async with _system() as case:
            api, other = case.pools
            login, command = await _prepared(api, "owner_a")
            async with api.connection() as conn:
                pid = (await (await conn.execute("SELECT pg_backend_pid()")).fetchone())[0]
            async with case.owner.connection() as held:
                await held.execute("SELECT user_id FROM hosted_identity_bindings WHERE user_id='owner_a' FOR UPDATE")
                committing = asyncio.create_task(login.commit_login(command))
                try:
                    await _wait_blocked(case.admin, pid)
                    await held.execute("UPDATE j01_fresh_login_intents SET expires_at=clock_timestamp() WHERE intent_id=%s",
                                       (command.intent_id,))
                    await held.commit()
                finally:
                    await held.rollback()
                    if not committing.done():
                        await asyncio.wait_for(committing, 10)
            assert committing.result() is None
            assert await PostgresSessionStore(other).inspect(command.credential_digest) is None
            async with case.owner.connection() as conn:
                assert await (await conn.execute("SELECT phase FROM j01_fresh_login_intents WHERE intent_id=%s",
                                                (command.intent_id,))).fetchone() == ("claimed",)
    _run(scenario())
