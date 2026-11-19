"""Verify real OIDC and queue fairness on the already running staging fleet."""

import asyncio
import html
import json
import re
import ssl
import sys
import time
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from uuid import uuid4

import httpx2
import psycopg
from stage_load import CONFIG, credentials, observe, stats

from aletheia_lab.hosted.capacity_models import Actor


async def login_smoke(tls):
    async with httpx2.AsyncClient(verify=tls, trust_env=False, timeout=10) as c:
        origin = CONFIG["origin"]
        issuer = CONFIG["issuer"]
        start = await c.get(origin + "/v1/auth/start", headers={"sec-fetch-site": "same-origin"})
        assert start.status_code == 303, start.status_code
        auth = start.headers["location"]
        assert auth.startswith(issuer + "/protocol/openid-connect/auth?")
        page = await c.get(auth)
        assert page.status_code == 200, page.status_code
        form = re.search(r'<form\b[^>]*\bid="kc-form-login"[^>]*>', page.text)
        assert form is not None, "login form unavailable"
        action = re.search(r'\baction="([^"]+)"', form[0])
        assert action is not None
        destination = urljoin(str(page.url), html.unescape(action[1]))
        assert destination.startswith(issuer + "/login-actions/")
        posted = await c.post(
            destination, data={"username": "u0", "password": Path("/run/load/password").read_text()}
        )
        assert posted.status_code in (302, 303), posted.status_code
        callback = posted.headers["location"]
        assert callback.startswith(origin + "/v1/auth/callback?")
        proof = await c.get(callback)
        me = await c.get(origin + "/v1/session")
        assert proof.status_code == 303 and me.status_code == 200, (
            proof.status_code,
            me.status_code,
        )
        return {
            "start": start.status_code,
            "provider_form": page.status_code,
            "provider_submission": posted.status_code,
            "callback": proof.status_code,
            "session": me.status_code,
            "issuer": urlsplit(issuer).netloc,
            "flow": "real Keycloak OIDC authorization code via HTTPS",
        }


async def exercise(output):
    tls = ssl.create_default_context(cafile="/run/config/ca.pem")
    owners = [
        a
        for a in (
            Actor.model_validate_json(line)
            for line in Path("/run/load/actors.jsonl").read_text().splitlines()
        )
        if a.role == "owner"
    ]
    noisy = [a for a in owners if a.values["workspace_id"].endswith("0000")]
    quiet = [a for a in owners if not a.values["workspace_id"].endswith("0000")]
    report = {
        "status": "failed",
        "login": None,
        "waves": [],
        "mixed_background": {},
        "source_identity": CONFIG["release_identity"],
    }
    latency = {name: [] for name in ("background", "health", "admin")}
    samples = []
    try:
        report["login"] = await login_smoke(tls)
        async with (
            httpx2.AsyncClient(
                base_url=CONFIG["origin"],
                verify=tls,
                trust_env=False,
                timeout=10,
                limits=httpx2.Limits(max_connections=16, max_keepalive_connections=8),
            ) as c,
            await psycopg.AsyncConnection.connect(
                Path("/run/load/observer_dsn").read_text(), autocommit=True
            ) as db,
        ):
            stop = asyncio.Event()

            async def background():
                while not stop.is_set():
                    actor = quiet[len(latency["background"]) % 3]
                    endpoint = f"/v1/workspaces/{actor.values['workspace_id']}/results/{actor.values['result_id']}/header"
                    t = time.monotonic()
                    r = await c.get(endpoint, headers=credentials(actor))
                    latency["background"].append(time.monotonic() - t)
                    assert (
                        r.status_code == 200
                        and r.json()["workspace_id"] == actor.values["workspace_id"]
                    )
                    t = time.monotonic()
                    r = await c.get("/health/ready")
                    latency["health"].append(time.monotonic() - t)
                    assert r.status_code == 200
                    t = time.monotonic()
                    assert (await (await db.execute("SELECT 1")).fetchone()) == (1,)
                    latency["admin"].append(time.monotonic() - t)
                    samples.append(await observe(db))
                    await asyncio.sleep(0.05)

            async def submit(actor, key):
                start = time.monotonic()
                path = f"/v1/workspaces/{actor.values['workspace_id']}/jobs"
                r = await c.post(
                    path,
                    headers={**credentials(actor), "idempotency-key": key},
                    json={"operation": "preview", "source_id": actor.values["source_id"]},
                )
                value = {"status": r.status_code, "seconds": time.monotonic() - start}
                if r.status_code == 202:
                    value.update(job_id=r.json()["job_id"], status_url=r.json()["status_url"])
                else:
                    assert r.status_code == 503 and r.json()["code"] == "service_unavailable", (
                        r.status_code
                    )
                    value.update(
                        retry_after=int(r.headers["retry-after"]),
                        cache_control=r.headers["cache-control"],
                    )
                    assert 1 <= value["retry_after"] <= 5 and value["cache_control"] == "no-store"
                return value

            async def finish(items):
                ids = [x[1]["job_id"] for x in items]
                assert len(set(ids)) == len(ids)
                async with asyncio.timeout(100):
                    while True:
                        rows = await (
                            await db.execute(
                                "SELECT job_id,workspace_id,state,created_at,terminal_at,attempt_count FROM j01_jobs WHERE job_id=ANY(%s)",
                                (ids,),
                            )
                        ).fetchall()
                        assert len(rows) == len(ids)
                        assert not any(r[2] in ("failed", "expired", "cancelled") for r in rows)
                        if all(r[2] == "succeeded" for r in rows):
                            break
                        await asyncio.sleep(0.25)
                for actor, receipt in items:
                    r = await c.get(receipt["status_url"] + "/output", headers=credentials(actor))
                    assert (
                        r.status_code == 200
                        and r.json()["result"]["source_sha256"] == actor.values["source_sha256"]
                    )
                started = await (
                    await db.execute(
                        "SELECT job_id,epoch,started_at,finished_at,outcome FROM k09_job_attempts WHERE job_id=ANY(%s)",
                        (ids,),
                    )
                ).fetchall()
                assert len(started) == len(ids) and all(
                    r[1] == 1 and r[4] == "succeeded" for r in started
                )
                return [
                    {
                        "job_id": r[0],
                        "workspace": r[1],
                        "state": r[2],
                        "created": r[3].isoformat(),
                        "terminal": r[4].isoformat(),
                        "attempt": r[5],
                        "claimed": next(x[2].isoformat() for x in started if x[0] == r[0]),
                        "output_source_sha256_verified": True,
                    }
                    for r in rows
                ]

            task = asyncio.create_task(background())
            try:
                for wave in range(2):
                    start = time.monotonic()
                    prefix = "k14-stage-" + uuid4().hex
                    chosen = [noisy[i % 3] for i in range(12)] + [quiet[0], quiet[-1]]
                    keys = [prefix + "-" + str(i) for i in range(len(chosen))]
                    offered = await asyncio.gather(
                        *(submit(a, k) for a, k in zip(chosen, keys, strict=True))
                    )
                    accepted = [
                        (a, r) for a, r in zip(chosen, offered, strict=True) if r["status"] == 202
                    ]
                    rejected = [
                        (a, k, r)
                        for a, k, r in zip(chosen, keys, offered, strict=True)
                        if r["status"] == 503
                    ]
                    assert rejected and all(a in noisy for a, _, _ in rejected), (
                        "expected bounded queue saturation in noisy workspace"
                    )
                    first = next(i for i, r in enumerate(offered) if r["status"] == 202)
                    replay = await submit(chosen[first], keys[first])
                    assert replay["job_id"] == offered[first]["job_id"]
                    settled = await finish(accepted)
                    quiet_rows = [r for r in settled if r["workspace"].endswith("0001")]
                    hot_rows = [r for r in settled if r["workspace"].endswith("0000")]
                    assert len(quiet_rows) == 2 and min(r["terminal"] for r in quiet_rows) < max(
                        r["terminal"] for r in hot_rows
                    ), "quiet workspace starved"
                    retry = await submit(rejected[0][0], rejected[0][1])
                    assert retry["status"] == 202
                    repeat = await submit(rejected[0][0], rejected[0][1])
                    assert repeat["job_id"] == retry["job_id"]
                    assert retry["job_id"] not in {r["job_id"] for r in settled}
                    retried = await finish([(rejected[0][0], retry)])
                    report["waves"].append(
                        dict(
                            wave=wave + 1,
                            seconds=time.monotonic() - start,
                            offered=offered,
                            accepted=len(accepted),
                            rejected=len(rejected),
                            same_key_replay_same_job=True,
                            quiet_finished_before_noisy_queue_empty=True,
                            jobs=settled,
                            retried_jobs=retried,
                        )
                    )
                    output.write_text(json.dumps(report, indent=2))
            finally:
                stop.set()
                await task
            report["mixed_background"] = {name: stats(values) for name, values in latency.items()}
            for name, limit in (("background", 500), ("health", 250), ("admin", 250)):
                assert report["mixed_background"][name]["p95_observed_ms"] <= limit
            report["status"] = "passed_staging_burst_pilot"
    finally:
        report["samples"] = samples
        report["raw_latency_seconds"] = latency
        output.write_text(json.dumps(report, indent=2))


if __name__ == "__main__":
    asyncio.run(exercise(Path(sys.argv[1])))
