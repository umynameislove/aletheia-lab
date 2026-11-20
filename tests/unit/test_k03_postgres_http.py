"""Manual isolated-PostgreSQL HTTP smoke; pytest checks only the cleanup guard.

The direct CLI needs K03_DB_PASSWORD and the existing local PostgreSQL sandbox.
It creates and drops only its UUID-named schema. OIDC is explicitly synthetic.
"""

import asyncio
import os
import re
import sys
import traceback
import uuid
from pathlib import Path
from time import monotonic

import httpx
import pytest
from pydantic import SecretBytes, SecretStr
from test_k03_postgres_environment import database_port

from aletheia_lab.hosted.application import AdapterLimits, HostedApplication
from aletheia_lab.hosted.read_backend import ReadOnlyHostedBackend
from aletheia_lab.hosted.request_boundary import BodyLimits
from aletheia_lab.hosted_auth.boundary import LOGIN_COOKIE, SESSION_COOKIE
from aletheia_lab.hosted_auth.callback_http import CallbackApplication
from aletheia_lab.hosted_auth.clock_guard import probe_clock
from aletheia_lab.hosted_auth.csrf import CSRF_COOKIE, CsrfTokens
from aletheia_lab.hosted_auth.identity import AccountResolver
from aletheia_lab.hosted_auth.login import LoginCoordinator
from aletheia_lab.hosted_auth.login_proof_cipher import LoginProofCipher
from aletheia_lab.hosted_auth.postgres_fresh_login import PostgresFreshLogin
from aletheia_lab.hosted_auth.postgres_identity import PostgresIdentityDirectory
from aletheia_lab.hosted_auth.postgres_sessions import PostgresSessionStore
from aletheia_lab.hosted_auth.session_http import (
    LOGOUT_ALL_PATH,
    RENEW_PATH,
    SessionMutationApplication,
)
from aletheia_lab.hosted_auth.sessions import ServerSessions
from aletheia_lab.hosted_auth.start_http import LoginStartApplication
from aletheia_lab.hosted_contract.session_capabilities import Limits


def _schema_name(name):
    if not isinstance(name, str) or not re.fullmatch(r"k03_http_test_[0-9a-f]{32}", name):
        raise ValueError("Only the exact isolated K-03 test schema is allowed.")
    return name


async def _exercise(pool):
    from test_k03_login_coordinator import ISSUER, POLICY, SyntheticOidc, _runtime

    runtime, provider = _runtime(Path.cwd()), SyntheticOidc()
    tokens = CsrfTokens(SecretStr("synthetic-postgres-smoke-key-" + "k" * 43))
    store = PostgresSessionStore(pool)
    sessions = ServerSessions(store, POLICY, timeout_seconds=5.0, maximum_cookie_bytes=4096)

    async def clock():
        return await probe_clock(pool, max_skew_seconds=30, timeout_seconds=2)

    assert await clock(), "Host/database clock is outside test budget"
    flow = LoginCoordinator(PostgresFreshLogin(pool, LoginProofCipher(SecretBytes(b"k" * 32)), clock_skew_seconds=30),
        provider, AccountResolver(PostgresIdentityDirectory(pool), POLICY, expected_issuer=ISSUER, timeout_seconds=2),
        runtime, POLICY, csrf_factory=tokens.issue, timeout_seconds=5.0)

    class Workspaces:
        async def is_ready(self):
            return True

    backend = ReadOnlyHostedBackend(sessions, Workspaces(),
        limits=Limits(question_chars=2000, upload_bundle_bytes=1024, page_max=200, job_poll_min_ms=2000))
    app = CallbackApplication(HostedApplication(backend, limits=AdapterLimits(5.0, 65536, 4096)),
        flow, POLICY, maximum_cookie_bytes=4096, timeout_seconds=5.0, clock_check=clock)
    app = LoginStartApplication(app, flow, maximum_cookie_bytes=4096, timeout_seconds=5.0, clock_check=clock)
    app = SessionMutationApplication(app, sessions, tokens, runtime,
        body_limits=BodyLimits(1024, 1.0, 2.0, 16), clock_check=clock)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=runtime.public_origin,
                                 follow_redirects=False) as client:
        await _login(client)
        before = (await client.get("/v1/session")).json()
        initial = client.cookies.get(SESSION_COOKIE)
        attempted_rebind = await client.get("/v1/auth/start", headers={"sec-fetch-site": "same-origin",
            "cookie": LOGIN_COOKIE + "=" + client.cookies.get(LOGIN_COOKIE)})
        assert attempted_rebind.status_code == 400 and not attempted_rebind.headers.get_list("set-cookie")
        stale = await client.get("/v1/auth/start", headers={"sec-fetch-site": "same-origin",
            "cookie": LOGIN_COOKIE + "=" + client.cookies.get(LOGIN_COOKIE) + "; " + SESSION_COOKIE + "=" + "z" * 43})
        assert stale.status_code == 400 and not stale.headers.get_list("set-cookie")
        assert (await client.get("/v1/session")).json() == before
        assert client.cookies.get(SESSION_COOKIE) == initial
        headers = {"origin": runtime.public_origin, "sec-fetch-site": "same-origin",
                   "x-csrf-token": before["csrf_token"]}
        async with pool.connection() as connection:
            await connection.execute("UPDATE j01_sessions SET issued_at=statement_timestamp()-interval '600 seconds', "
                "last_seen_at=statement_timestamp()-interval '600 seconds', rotated_at=statement_timestamp()-interval '600 seconds' "
                "WHERE context_id=%s", (before["session"]["context_id"],))
        rotated = await client.post(RENEW_PATH, json={}, headers=headers)
        assert rotated.status_code == 200
        after = rotated.json()
        assert before["session"]["context_id"] == after["session"]["context_id"]
        assert before["csrf_token"] == after["csrf_token"]
        assert client.cookies.get(SESSION_COOKIE) != initial
        assert (await client.get("/v1/session", headers={"cookie": SESSION_COOKIE + "=" + initial})).status_code == 401
        early = await client.post(RENEW_PATH, json={}, headers=headers)
        assert early.status_code == 200 and not early.headers.get_list("set-cookie")
        denied = await client.post(LOGOUT_ALL_PATH, json={}, headers={**headers, "x-csrf-token": "wrong"})
        assert denied.status_code == 403 and (await client.get("/v1/session")).status_code == 200
        current = client.cookies.get(SESSION_COOKIE)
        extra_cookie, other_cookie = await _seed_families(pool, tokens)
        started = monotonic()
        revoked = await client.post(LOGOUT_ALL_PATH, json={}, headers=headers)
        assert revoked.status_code == 204
        assert client.cookies.get(SESSION_COOKIE) is None and client.cookies.get(CSRF_COOKIE) is None
        for credential in (initial, current, extra_cookie):
            response = await client.get("/v1/session", headers={"cookie": SESSION_COOKIE + "=" + credential})
            assert response.status_code == 401
        elapsed = monotonic() - started
        assert elapsed <= 5.0
        other = await client.get("/v1/session", headers={"cookie": SESSION_COOKIE + "=" + other_cookie})
        assert other.status_code == 200 and other.json()["actor"]["user_id"] == "usr_synthetic_b"
        print("K03_DB_HTTP_RENEW_AND_EARLY_RENEW=PASS", flush=True)
        print("K03_DB_HTTP_LOGOUT_ALL_ACCOUNT_ISOLATION=PASS", flush=True)
        print("K03_DB_HTTP_LOCAL_REVOKE_SECONDS=" + format(elapsed, ".3f"), flush=True)
        print("K03_DB_CLOCK=REAL_HOST_AND_DATABASE_NO_OVERRIDE", flush=True)
        await _login(client)
        recovered = await client.get("/v1/session")
        assert recovered.status_code == 200 and recovered.json()["actor"]["user_id"] == "usr_synthetic_a"
        print("K03_DB_FRESH_LOGIN_AFTER_LOGOUT_ALL=PASS", flush=True)
        async with pool.connection() as connection:
            await connection.execute("UPDATE j01_sessions SET issued_at=statement_timestamp()-interval '600 seconds', "
                "last_seen_at=statement_timestamp()-interval '600 seconds', rotated_at=statement_timestamp()-interval '600 seconds', "
                "idle_expires_at=statement_timestamp()-interval '1 second' WHERE context_id=%s",
                (recovered.json()["session"]["context_id"],))
        assert client.cookies.get(SESSION_COOKIE) is not None
        await _login(client)
        assert (await client.get("/v1/session")).status_code == 200
        print("K03_DB_FRESH_LOGIN_WITH_EXPIRED_COOKIE=PASS", flush=True)
        print("K03_DB_LIVE_CONTEXT_REBIND_STILL_REJECTED=PASS", flush=True)
        await _account_policy_checks(client, pool, provider)


async def _begin(client):
    from urllib.parse import parse_qs, urlsplit

    response = await client.get("/v1/auth/start", headers={"sec-fetch-site": "same-origin"})
    assert response.status_code == 303
    # Synthetic IdP only: state comes from the real persisted begin flow.
    return parse_qs(urlsplit(response.headers["location"]).query)["state"][0]


async def _callback(client, state):
    from test_k03_login_coordinator import ISSUER

    return await client.get("/v1/auth/callback", params={"state": state, "iss": ISSUER, "code": "synthetic-code"})


async def _login(client):
    response = await _callback(client, await _begin(client))
    assert response.status_code == 303 and response.headers["location"] == "/app"
    return response


async def _account_policy_checks(client, pool, provider):
    from aletheia_lab.hosted_auth.login import _digest

    async def count_sessions():
        async with pool.connection() as connection:
            return (await (await connection.execute("SELECT count(*) FROM j01_sessions")).fetchone())[0]

    async def writer(*, enabled, verified):
        # Fixture for the trusted writer contract, not an IdP event implementation.
        async with pool.connection() as connection:
            await connection.execute("UPDATE hosted_identity_bindings SET enabled=%s,email_verified=%s,"
                "account_epoch=account_epoch+1,logout_all_at=clock_timestamp() WHERE user_id=%s",
                (enabled, verified, "usr_synthetic_a"))

    def no_credentials(response, reason="login_failed"):
        assert response.status_code == 303
        assert response.headers["location"] == "/#/signed-out?reason=" + reason
        assert not response.headers.get_list("set-cookie") and response.content == b""
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["referrer-policy"] == "no-referrer"

    for enabled, verified in ((False, True), (True, False)):
        old = client.cookies.get(SESSION_COOKIE)
        before = await count_sessions()
        await writer(enabled=enabled, verified=verified)
        assert (await client.get("/v1/session")).status_code == 401
        switch = await client.get("/v1/auth/start?mode=switch", headers={"sec-fetch-site": "same-origin"})
        assert switch.status_code == 400 and not switch.headers.get_list("set-cookie")
        assert "location" not in switch.headers and switch.content == b"Authentication request could not be completed."
        denied = await _callback(client, await _begin(client))
        no_credentials(denied, "account_disabled" if not enabled else "login_failed")
        no_credentials(await client.get(denied.request.url))
        assert await count_sessions() == before
        await writer(enabled=True, verified=True)
        # Re-enabling cannot restore a credential from an earlier account epoch.
        assert (await client.get("/v1/session", headers={"cookie": SESSION_COOKIE + "=" + old})).status_code == 401
        await _login(client)
        assert (await client.get("/v1/session")).status_code == 200
    print("K03_DB_STALE_COOKIE_FRESH_LOGIN_ALLOWED_REAUTH_REJECTED=PASS", flush=True)
    print("K03_DB_DISABLED_AND_EMAIL_POLICY_HTTP_DENIAL=PASS", flush=True)
    print("K03_DB_REENABLE_WITH_EPOCH_NEVER_RESURRECTS_OLD_SESSION=PASS", flush=True)

    original = provider.identity
    before = await count_sessions()
    provider.identity = original.model_copy(update={"subject": SecretStr("recovered-new-subject"),
                                                    "email": SecretStr("a@example.invalid")})
    try:
        denied = await _callback(client, await _begin(client))
        no_credentials(denied)
        no_credentials(await client.get(denied.request.url))
        assert await count_sessions() == before
    finally:
        provider.identity = original
    print("K03_DB_NEW_SUBJECT_SAME_EMAIL_NO_ACCOUNT_LINK=PASS", flush=True)

    client.cookies.delete(SESSION_COOKIE)
    client.cookies.delete(CSRF_COOKIE)
    client.cookies.delete(LOGIN_COOKIE)
    state = await _begin(client)  # No previous context, so only the watermark fences this login.
    state_hash = _digest(SecretStr(state), "login-state").get_secret_value()
    async with pool.connection() as connection:
        await connection.execute("UPDATE hosted_identity_bindings SET account_epoch=account_epoch+1,"
            "logout_all_at=(SELECT database_created_at FROM j01_fresh_login_intents WHERE state_digest=%s) "
            "WHERE user_id=%s", (state_hash, "usr_synthetic_a"))
    no_credentials(await _callback(client, state))
    assert await count_sessions() == before
    no_credentials(await _callback(client, state))
    await _login(client)
    assert (await client.get("/v1/session")).status_code == 200
    assert await count_sessions() == before + 1
    print("K03_DB_EQUAL_RECOVERY_WATERMARK_DENIES_AND_FRESH_LOGIN_RECOVERS=PASS", flush=True)


async def _seed_families(pool, tokens):
    from aletheia_lab.hosted_auth.boundary import new_opaque_secret
    from aletheia_lab.hosted_auth.sessions import credential_digest

    credentials = []
    async with pool.connection() as connection:
        for suffix in ("a", "b"):
            credential = new_opaque_secret()
            context = "ctx_test_extra_" + suffix
            await connection.execute("INSERT INTO j01_sessions VALUES (%s,%s,%s,'active', "
                "statement_timestamp(),statement_timestamp(),statement_timestamp(), "
                "statement_timestamp()+interval '900 seconds',statement_timestamp()+interval '28800 seconds',0,0)",
                (context, "usr_synthetic_" + suffix, tokens.issue(context).get_secret_value()))
            await connection.execute("INSERT INTO j01_session_credentials VALUES (%s,%s,'active',0)",
                (credential_digest(credential).get_secret_value(), context))
            credentials.append(credential.get_secret_value())
    return credentials


async def _live(password):
    import psycopg
    from psycopg import sql
    from psycopg_pool import AsyncConnectionPool
    from test_k03_login_coordinator import ISSUER

    from aletheia_lab.hosted.persistence_migration import LOGIN_CLOCK_DDL

    schema = _schema_name("k03_http_test_" + uuid.uuid4().hex)
    db = {"host": "127.0.0.1", "port": database_port(), "dbname": "aletheia", "user": "aletheia", "password": password}
    created = False
    async with await psycopg.AsyncConnection.connect(**db, autocommit=True) as admin:
        try:
            await admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
            created = True
            await admin.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
            for name in ("j01-experimental-session-schema.sql", "j01-experimental-fresh-login-schema.sql"):
                await admin.execute((Path.cwd() / "docs" / name).read_text(encoding="utf-8"))
            for suffix in ("a", "b"):
                await admin.execute("INSERT INTO hosted_identity_bindings VALUES (%s,%s,%s,%s,%s,true,true,0,'needs_workspace',NULL,NULL)",
                    (ISSUER, "subject-" + suffix, "usr_synthetic_" + suffix, "Synthetic", suffix + "@example.invalid"))
            await admin.execute(LOGIN_CLOCK_DDL)
            async with AsyncConnectionPool(conninfo="", kwargs={**db, "options": "-c search_path=" + schema}, min_size=1, max_size=5) as pool:
                await _exercise(pool)
        finally:
            if created:
                await admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(_schema_name(schema))))
                print("K03_DB_OWN_SCHEMA_REMOVED=PASS", flush=True)


@pytest.mark.parametrize("name", ["public", "j01_sessions", "k03_http_test_x", "k03_http_test_" + "a" * 32 + ";DROP"])
def test_schema_cleanup_cannot_target_another_namespace(name):
    with pytest.raises(ValueError):
        _schema_name(name)


def test_schema_guard_accepts_only_generated_names():
    name = "k03_http_test_" + uuid.uuid4().hex
    assert _schema_name(name) == name


if __name__ == "__main__":
    try:
        if sys.platform == "win32":
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        asyncio.run(_live(os.environ.pop("K03_DB_PASSWORD")))
    except Exception as error:
        print("K03_DB_HTTP_CHECK=FAIL:" + type(error).__name__, flush=True)
        frames = traceback.extract_tb(error.__traceback__)
        if frames:
            print("K03_DB_FAILURE_LINE=" + str(frames[-1].lineno), flush=True)
        sys.exit(1)
