"""Real session/CSRF routes and two native PG pools; no OIDC/provider or browser."""
from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from types import SimpleNamespace

import httpx
import pytest
from psycopg_pool import AsyncConnectionPool
from pydantic import SecretStr
from test_k03_csrf_boundary import ORIGIN, POLICY, SIGNING_SECRET, _runtime
from test_k04_runtime_principal_postgres import _principals, _run

from aletheia_lab.hosted.application import AccessDenied
from aletheia_lab.hosted.request_boundary import BodyLimits
from aletheia_lab.hosted_auth.boundary import SESSION_COOKIE
from aletheia_lab.hosted_auth.csrf import CSRF_COOKIE, CsrfTokens
from aletheia_lab.hosted_auth.postgres_sessions import PostgresSessionStore
from aletheia_lab.hosted_auth.session_http import (
    LOGOUT_ALL_PATH,
    RENEW_PATH,
    SessionMutationApplication,
)
from aletheia_lab.hosted_auth.sessions import ServerSessions, credential_digest

pytestmark = [pytest.mark.integration, pytest.mark.skipif(
    not os.environ.get("K03_TEST_DATABASE_URL"), reason="Explicit K03 private native PostgreSQL required")]


@pytest.fixture(autouse=True)
def configured_database(monkeypatch):
    monkeypatch.setenv("K04_TEST_DATABASE_URL", os.environ["K03_TEST_DATABASE_URL"])


@asynccontextmanager
async def _session_system(tmp_path):
    async with _principals() as (owner, api, _, _, _, _, _), \
            AsyncConnectionPool(api.conninfo, min_size=1, max_size=1, open=False) as second:
        await second.wait(timeout=10)
        async with api.connection() as a, second.connection() as b:
            assert await (await a.execute("SELECT pg_backend_pid()")).fetchone() != await (
                await b.execute("SELECT pg_backend_pid()")).fetchone()
        tokens = CsrfTokens(SecretStr(SIGNING_SECRET))
        headers = {}
        async with owner.connection() as conn:
            await conn.execute("INSERT INTO j01_sessions SELECT 'ctx_owner_a_other',user_id,csrf_token,state,"
                "issued_at,last_seen_at,rotated_at,idle_expires_at,absolute_expires_at,generation,session_epoch "
                "FROM j01_sessions WHERE context_id='ctx_owner_a'")
            for name, context in (("a", "ctx_owner_a"), ("a_other", "ctx_owner_a_other"), ("b", "ctx_owner_b")):
                raw = SecretStr(("synthetic-k03-native-session-" + name).ljust(43, "x"))
                proof = tokens.issue(context).get_secret_value()
                await conn.execute("DELETE FROM j01_session_credentials WHERE context_id=%s", (context,))
                await conn.execute("INSERT INTO j01_session_credentials VALUES(%s,%s,'active',0)",
                    (credential_digest(raw).get_secret_value(), context))
                await conn.execute("UPDATE j01_sessions SET csrf_token=%s,"
                    "issued_at=statement_timestamp()-interval '600 seconds',"
                    "rotated_at=statement_timestamp()-interval '600 seconds',"
                    "last_seen_at=statement_timestamp()-interval '600 seconds',"
                    "idle_expires_at=statement_timestamp()+interval '300 seconds',"
                    "absolute_expires_at=statement_timestamp()+interval '28000 seconds' WHERE context_id=%s",
                    (proof, context))
                headers[name] = {"origin": ORIGIN, "x-csrf-token": proof,
                    "cookie": f"{SESSION_COOKIE}={raw.get_secret_value()}; {CSRF_COOKIE}={proof}"}

        async def fallback(scope, receive, send):
            raise AssertionError("Unexpected session route")

        async def clock():
            return True

        sessions = [ServerSessions(PostgresSessionStore(pool), POLICY,
            timeout_seconds=10, maximum_cookie_bytes=4096) for pool in (api, second)]
        apps = [SessionMutationApplication(fallback, item, tokens, _runtime(tmp_path),
            body_limits=BodyLimits(1024, 2, 10, 16), clock_check=clock) for item in sessions]
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=apps[0]), base_url=ORIGIN) as a, \
                httpx.AsyncClient(transport=httpx.ASGITransport(app=apps[1]), base_url=ORIGIN) as b:
            yield SimpleNamespace(owner=owner, clients=(a, b), sessions=sessions, headers=headers)

def _replacement_headers(response, old):
    values = response.cookies
    return {**old, "cookie": f"{SESSION_COOKIE}={values[SESSION_COOKIE]}; {CSRF_COOKIE}={values[CSRF_COOKIE]}"}


def test_concurrent_http_renewal_has_one_rotation_across_two_real_backends(tmp_path, monkeypatch):
    async def scenario():
        async with _session_system(tmp_path) as case:
            ready, release = asyncio.Event(), asyncio.Event()
            original, entered = PostgresSessionStore.rotate_if_current, 0

            async def held(store, command):
                nonlocal entered
                entered += 1
                if entered == 12:
                    ready.set()
                await asyncio.wait_for(release.wait(), 10)
                return await original(store, command)

            monkeypatch.setattr(PostgresSessionStore, "rotate_if_current", held)
            tasks = [asyncio.create_task(case.clients[i % 2].post(RENEW_PATH,
                headers=case.headers["a"], json={})) for i in range(12)]
            try:
                await asyncio.wait_for(ready.wait(), 10)
            finally:
                release.set()
            responses = await asyncio.gather(*tasks)
            assert sorted(r.status_code for r in responses) == [200] + [409] * 11
            winner = next(r for r in responses if r.status_code == 200)
            assert all(r.json()["code"] == "renewal_conflict" and not r.cookies
                for r in responses if r.status_code == 409)
            for value in winner.headers.get_list("set-cookie"):
                assert "Secure" in value and "HttpOnly" in value and "SameSite=Lax" in value and "Domain=" not in value
            fresh = _replacement_headers(winner, case.headers["a"])
            early = await case.clients[1].post(RENEW_PATH, headers=fresh, json={})
            assert early.status_code == 200 and not early.cookies
            for session in case.sessions:
                with pytest.raises(AccessDenied):
                    await session.resolve_cookie(case.headers["a"]["cookie"].encode())
                assert (await session.resolve_cookie(fresh["cookie"].encode())).actor.user_id == "owner_a"
                assert (await session.resolve_cookie(case.headers["b"]["cookie"].encode())).actor.user_id == "owner_b"
            async with case.owner.connection() as conn:
                assert await (await conn.execute("SELECT generation FROM j01_sessions WHERE context_id='ctx_owner_a'")).fetchone() == (1,)
                assert await (await conn.execute("SELECT state,count(*) FROM j01_session_credentials "
                    "WHERE context_id='ctx_owner_a' GROUP BY state ORDER BY state")).fetchall() == [("active", 1), ("rotated", 1)]
    _run(scenario())


@pytest.mark.parametrize("rotation_first", [False, True], ids=["logout-first", "rotation-first"])
def test_http_renewal_logout_all_orders_never_resurrect_other_sessions(tmp_path, monkeypatch, rotation_first):
    async def scenario():
        async with _session_system(tmp_path) as case:
            entered, release = asyncio.Event(), asyncio.Event()
            original = PostgresSessionStore.rotate_if_current

            async def held(store, command):
                result = await original(store, command) if rotation_first else None
                entered.set()
                await asyncio.wait_for(release.wait(), 10)
                return result if rotation_first else await original(store, command)

            monkeypatch.setattr(PostgresSessionStore, "rotate_if_current", held)
            task = asyncio.create_task(case.clients[0].post(RENEW_PATH, headers=case.headers["a"], json={}))
            try:
                await asyncio.wait_for(entered.wait(), 10)
                logout = await case.clients[1].post(LOGOUT_ALL_PATH, headers=case.headers["a_other"], json={})
                assert logout.status_code == 204
                assert all("Max-Age=0" in item for item in logout.headers.get_list("set-cookie"))
            finally:
                release.set()
            answer = await task
            assert answer.status_code == (200 if rotation_first else 409)
            denied = [case.headers["a"], case.headers["a_other"]]
            if rotation_first:
                denied.append(_replacement_headers(answer, case.headers["a"]))
            for session in case.sessions:
                for headers in denied:
                    with pytest.raises(AccessDenied):
                        await session.resolve_cookie(headers["cookie"].encode())
                assert (await session.resolve_cookie(case.headers["b"]["cookie"].encode())).actor.user_id == "owner_b"
            async with case.owner.connection() as conn:
                assert await (await conn.execute("SELECT state,generation FROM j01_sessions WHERE context_id='ctx_owner_a'")).fetchone() == (
                    "revoked", int(rotation_first))
                assert await (await conn.execute("SELECT user_id,account_epoch FROM hosted_identity_bindings "
                    "WHERE user_id IN ('owner_a','owner_b') ORDER BY user_id")).fetchall() == [("owner_a", 1), ("owner_b", 0)]
    _run(scenario())
