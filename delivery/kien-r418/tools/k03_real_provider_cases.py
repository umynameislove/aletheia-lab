"""Real provider failures/recovery over the deployed K03 HTTP boundary."""

from __future__ import annotations

import asyncio
import json
import subprocess
import time
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import httpx
import psycopg
import test_k03_keycloak_recovery as recovery
import test_k03_recovery_environment as infrastructure
from pydantic import SecretStr

from aletheia_lab.hosted_auth.boundary import SESSION_COOKIE
from aletheia_lab.hosted_auth.postgres_sessions import PostgresSessionStore


def identity_container(environment):
    identities = subprocess.check_output(
        ["docker", "ps", "--filter", "label=com.aletheia.k03.recovery", "--format", "{{.ID}}"],
        text=True,
    ).splitlines()
    port = str(urlsplit(environment.keycloak_origin).port)
    matches = []
    for identity in identities:
        row = json.loads(subprocess.check_output(["docker", "inspect", identity]))[0]
        if row["NetworkSettings"]["Ports"].get("8443/tcp") != [
            {"HostIp": "127.0.0.1", "HostPort": port}
        ]:
            continue
        name = row["Name"].lstrip("/")
        infrastructure._owned_name(name, "keycloak")
        owner = row["Config"]["Labels"]["com.aletheia.k03.recovery"]
        assert name == "aletheia-k03-recovery-" + owner + "-keycloak"
        assert (
            row["Image"]
            == infrastructure._image(infrastructure.KEYCLOAK_IMAGE, allow_pull=False)[0]
        )
        matches.append(row["Id"])
    assert len(matches) == 1
    return matches[0]


async def revoke_account(owner_dsn, schema, issuer, subject):
    async with await psycopg.AsyncConnection.connect(owner_dsn) as operator:
        assert await PostgresSessionStore.revoke_identity(
            operator,
            schema=schema,
            issuer=issuer,
            subject=SecretStr(subject),
            expected_issuer=issuer,
        )


async def run(ctx):
    last_step = ctx.last_a
    password = ctx.password
    base = "/admin/realms/" + ctx.realm
    async with (
        httpx.AsyncClient(base_url=ctx.origin, verify=ctx.tls, trust_env=False, timeout=20) as app,
        httpx.AsyncClient(verify=ctx.idp.tls_context, trust_env=False, timeout=20) as provider,
        httpx.AsyncClient(base_url=ctx.idp.mailbox_origin, trust_env=False, timeout=20) as mailbox,
    ):
        started = time.monotonic()
        _, callback, last_step = await recovery._mfa_callback(
            SimpleNamespace(flow=ctx.flow(app)),
            provider,
            password,
            ctx.seed,
            ctx.idp,
            ctx.realm,
            last_step,
        )
        ctx.secrets.extend(parse_qs(urlsplit(callback).query).get("code", []))
        before = (await (await ctx.owner.execute("SELECT count(*) FROM j01_sessions")).fetchone())[
            0
        ]
        identity = identity_container(ctx.idp)
        paused = False
        try:
            subprocess.run(
                ["docker", "pause", identity], check=True, capture_output=True, timeout=15
            )
            paused = True
            failure_started = time.monotonic()
            failed = await app.get(callback)
            failed_seconds = time.monotonic() - failure_started
            assert failed.status_code == 303 and "idp_unavailable" in failed.headers["location"]
            assert failed.headers["cache-control"] == "no-store"
            assert SESSION_COOKIE not in app.cookies
            assert (await app.get("/health/live")).status_code == 200
            after = (
                await (await ctx.owner.execute("SELECT count(*) FROM j01_sessions")).fetchone()
            )[0]
            assert after == before
        finally:
            if paused:
                subprocess.run(
                    ["docker", "unpause", identity], check=True, capture_output=True, timeout=15
                )
        ctx.record(
            "real_idp_process_outage_no_session_or_retry_loop",
            started,
            callback_seconds=failed_seconds,
            callbacks_sent=1,
            created_sessions=0,
        )
        await asyncio.to_thread(infrastructure._wait_ready, ctx.idp)
        await asyncio.sleep(1.1)
        started = time.monotonic()
        fresh, _, last_step, _ = await ctx.login("recovery-user", password, ctx.seed, last_step)
        try:
            assert (await fresh.get("/v1/session")).status_code == 200
        finally:
            await fresh.aclose()
        ctx.record("real_idp_resume_fresh_login_recovers", started)

        await recovery._admin_login(ctx.idp, ctx.admin)
        factors = await recovery._otp_ids(ctx.admin, base, ctx.subject)
        assert len(factors) == 1
        started = time.monotonic()
        await recovery._json(ctx.admin, "PUT", base, json={"actionTokenGeneratedByUserLifespan": 2})
        expired = await recovery._request_reset(
            SimpleNamespace(flow=ctx.flow(app)), provider, mailbox, ctx.idp, ctx.realm
        )
        assert (await recovery._json(ctx.admin, "GET", base))[
            "actionTokenGeneratedByUserLifespan"
        ] == 2
        await asyncio.sleep(5)
        async with httpx.AsyncClient(
            verify=ctx.idp.tls_context, trust_env=False, timeout=20
        ) as fresh_browser:
            page = await recovery._navigate(fresh_browser, expired, ctx.idp, ctx.realm)
            recovery._recovery_denied(page)
        assert await recovery._otp_ids(ctx.admin, base, ctx.subject) == factors
        ctx.record(
            "real_smtp_expired_reset_link_denied", started, token_ttl_seconds=2, wait_seconds=5
        )

        started = time.monotonic()
        await recovery._json(
            ctx.admin, "PUT", base, json={"actionTokenGeneratedByUserLifespan": 120}
        )
        link = await recovery._request_reset(
            SimpleNamespace(flow=ctx.flow(app)), provider, mailbox, ctx.idp, ctx.realm
        )
        page = await recovery._navigate(provider, link, ctx.idp, ctx.realm)
        new_password = ctx.new_password
        ctx.secrets.append(new_password)
        page = await recovery._submit(
            provider,
            page,
            "password-new",
            {"password-new": new_password, "password-confirm": new_password, "login": "Submit"},
            ctx.idp,
            ctx.realm,
        )
        assert (
            not isinstance(page, str)
            and page.status_code == 200
            and not recovery._document(page).has("totpSecret")
        )
        assert await recovery._otp_ids(ctx.admin, base, ctx.subject) == factors
        assert (await app.get("/v1/session")).status_code == 401
        ctx.record(
            "real_smtp_reset_retains_factor_no_auto_session", started, original_factor_retained=True
        )

        started = time.monotonic()
        for client in (provider,):
            replay = await recovery._navigate(client, link, ctx.idp, ctx.realm)
            recovery._recovery_denied(replay)
        async with httpx.AsyncClient(
            verify=ctx.idp.tls_context, trust_env=False, timeout=20
        ) as fresh_browser:
            replay = await recovery._navigate(fresh_browser, link, ctx.idp, ctx.realm)
            recovery._recovery_denied(replay)
        _, denied = await recovery._password_page(
            SimpleNamespace(flow=ctx.flow(app)), provider, password, ctx.idp, ctx.realm
        )
        recovery._password_denied(denied)
        ctx.record("real_reset_replay_and_old_password_denied", started, replay_contexts=2)

        started = time.monotonic()
        reset_session, _, last_step, _ = await ctx.login(
            "recovery-user", new_password, ctx.seed, last_step
        )
        try:
            before = (await reset_session.get("/v1/session")).json()
            assert before["actor"]["user_id"] == "k03_a"
            ctx.record("real_new_password_still_requires_original_otp", started)
            started = time.monotonic()
            await revoke_account(ctx.owner_dsn, ctx.schema, ctx.issuer, ctx.subject)
            denied = await reset_session.get("/v1/session")
            assert denied.status_code == 401
            assert time.monotonic() - started <= 5
            other = await app.get("/v1/session", headers={"cookie": ctx.other_cookie})
            assert other.status_code == 200 and other.json()["actor"]["user_id"] == "k03_b"
            ctx.record(
                "real_operator_revoke_after_reset_preserves_b",
                started,
                denied_status=401,
                other_status=200,
            )
        finally:
            await reset_session.aclose()

        started = time.monotonic()
        await recovery._json(
            ctx.admin, "PUT", base + "/users/" + ctx.subject, json={"enabled": False}
        )
        try:
            _, page = await recovery._password_page(
                SimpleNamespace(flow=ctx.flow(app)), provider, new_password, ctx.idp, ctx.realm
            )
            assert not isinstance(page, str) and not recovery._document(page).has("otp")
            assert (await app.get("/v1/session")).status_code == 401
            ctx.record("real_disabled_provider_account_cannot_login", started)
        finally:
            await recovery._json(
                ctx.admin, "PUT", base + "/users/" + ctx.subject, json={"enabled": True}
            )
    return new_password, last_step
