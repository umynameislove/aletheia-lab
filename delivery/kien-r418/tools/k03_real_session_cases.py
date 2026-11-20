"""Natural-time rotation and bounded auth admission through real HTTPS."""

from __future__ import annotations

import asyncio
import time
from collections import Counter

import httpx

from aletheia_lab.hosted_auth.boundary import SESSION_COOKIE
from aletheia_lab.hosted_auth.csrf import CSRF_COOKIE


async def run(ctx):
    started = time.monotonic()
    client, session, _, _ = await ctx.login("recovery-user", ctx.password, ctx.seed, ctx.last_a)
    try:
        cookie = "; ".join(f"{c.name}={c.value}" for c in client.cookies.jar)
        proof = client.cookies.get(CSRF_COOKIE)
        headers = {"cookie": cookie, "origin": ctx.origin, "x-csrf-token": proof}
        context_id = session["session"]["context_id"]
        early = await client.post("/v1/auth/renew", json={}, headers=headers)
        assert early.status_code == 200 and not early.headers.get_list("set-cookie")
        ctx.record("real_early_renew_does_not_rotate", started, http_status=200)

        started = time.monotonic()
        row = await (
            await ctx.owner.execute(
                "SELECT EXTRACT(EPOCH FROM (rotated_at + interval '301 seconds' - clock_timestamp())), generation "
                "FROM j01_sessions WHERE context_id=%s",
                (context_id,),
            )
        ).fetchone()
        wait_seconds = max(0.0, float(row[0]))
        assert 0 < wait_seconds <= 302
        print("K03_NATURAL_ROTATION_WAIT_SECONDS=" + str(round(wait_seconds, 3)), flush=True)
        await asyncio.sleep(wait_seconds)
        async with httpx.AsyncClient(
            base_url=ctx.origin,
            verify=ctx.tls,
            trust_env=False,
            timeout=20,
        ) as storm:
            gate = asyncio.Event()

            async def renew():
                await gate.wait()
                return await storm.post("/v1/auth/renew", json={}, headers=headers)

            tasks = [asyncio.create_task(renew()) for _ in range(12)]
            gate.set()
            responses = await asyncio.gather(*tasks)
            statuses = Counter(r.status_code for r in responses)
            assert statuses[200] == 1 and sum(statuses.values()) == 12
            assert set(statuses) <= {200, 401, 409}
            winner = next(r for r in responses if r.status_code == 200)
            new_credential = winner.cookies.get(SESSION_COOKIE)
            assert new_credential and new_credential != client.cookies.get(SESSION_COOKIE)
            ctx.secrets.extend(c.value for c in winner.cookies.jar)
            denied = await storm.get("/v1/session", headers={"cookie": cookie})
            assert denied.status_code == 401
            live = await storm.get(
                "/v1/session",
                headers={
                    "cookie": SESSION_COOKIE + "=" + new_credential,
                },
            )
            assert live.status_code == 200 and live.json()["actor"]["user_id"] == "k03_a"
            state = await (
                await ctx.owner.execute(
                    "SELECT generation FROM j01_sessions WHERE context_id=%s",
                    (context_id,),
                )
            ).fetchone()
            assert state[0] == row[1] + 1
        ctx.record(
            "real_natural_300s_rotation_12_parallel_renewals_one_winner",
            started,
            natural_wait_seconds=wait_seconds,
            requests=12,
            statuses=dict(statuses),
            rotations=1,
            old_cookie_status=401,
            new_cookie_status=200,
            idle_seconds=900,
            absolute_seconds=28800,
            clock_mutation=False,
        )
    finally:
        await client.aclose()

    await abuse(ctx)


async def abuse(ctx):
    # A private loopback source only; no redirect following or external IdP traffic.
    started = time.monotonic()
    async with httpx.AsyncClient(
        base_url=ctx.origin,
        verify=ctx.tls,
        trust_env=False,
        timeout=20,
    ) as abuse:
        row = await (
            await ctx.owner.execute(
                "SELECT EXTRACT(EPOCH FROM clock_timestamp())",
            )
        ).fetchone()
        until_boundary = 60 - float(row[0]) % 60
        if until_boundary < 15:
            await asyncio.sleep(until_boundary + 0.1)
        statuses = Counter()
        retry_after = []

        async def request():
            return await abuse.get("/v1/auth/start", headers={"sec-fetch-site": "same-origin"})

        for offset in range(0, 35, 1):
            responses = await asyncio.gather(*(request() for _ in range(min(1, 35 - offset))))
            for response in responses:
                statuses[response.status_code] += 1
                assert response.status_code in (303, 429)
                if response.status_code == 429:
                    retry_after.append(int(response.headers["retry-after"]))
                    assert response.headers["cache-control"] == "no-store"
        assert statuses[429] >= 5 and retry_after and min(retry_after) > 0
        assert SESSION_COOKIE not in abuse.cookies
    ctx.record(
        "real_bounded_auth_start_abuse_subject_limit",
        started,
        requests=35,
        maximum_parallel=1,
        statuses=dict(statuses),
        retries=0,
        followed_redirects=0,
        retry_after_min=min(retry_after),
        retry_after_max=max(retry_after),
        subject_limit=30,
        window_seconds=60,
        public_idp_requests=0,
    )
