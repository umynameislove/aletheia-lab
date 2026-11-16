"""Exercise real OIDC sessions, accepted jobs and downloads during both rollouts."""

import asyncio
import hashlib
import json
import secrets
import socket
import ssl
import time
from pathlib import Path
from uuid import uuid4

import psycopg
from aletheia_lab.hosted.capacity_models import Actor
from aletheia_lab.hosted.postgres_jobs import PostgresJobs
from aletheia_lab.hosted.postgres_sources import PostgresSources
from coordination import read_json, write_json
from psycopg_pool import AsyncConnectionPool
from pydantic import SecretStr
from stage_load import CONFIG, DNS, credentials, observe, stats
from verify_remaining import client, fresh_actor, read_header

ROOT = Path("/evidence")
TLS = ssl.create_default_context(cafile="/run/config/ca.pem")
assert CONFIG["run_id"] != "da2093353a73"


def rollout_dns(host, *args, **kwargs):
    return DNS(
        "k18-edge" if host in ("localhost", b"localhost") else host, *args, **kwargs
    )


socket.getaddrinfo = rollout_dns


def record(name, value):
    write_json(ROOT / name, value)


async def wait(name, timeout=150):
    async with asyncio.timeout(timeout):
        while not (ROOT / name).exists():
            await asyncio.sleep(0.05)
    return read_json(ROOT / name)


async def create_export(connection, actor):
    ws, result = (actor.values[k] for k in ("workspace_id", "result_id"))
    reply = await connection.post(
        f"/v1/workspaces/{ws}/results/{result}/exports",
        headers={**credentials(actor), "idempotency-key": "rollout-" + uuid4().hex},
        json={"format": "json"},
    )
    assert reply.status_code == 201, reply.status_code
    d = reply.json()
    assert d["workspace_id"] == ws and d["result_id"] == result
    assert d["download_url"].startswith("/v1/") and ".." not in d["download_url"]
    assert d["byte_size"] > 8192 and len(d["sha256"]) == 64
    return d


async def download(connection, actor, descriptor, phase, started):
    url = f"https://localhost:{CONFIG['app_port'] + 1000}" + descriptor["download_url"]
    digest = hashlib.sha256()
    count = 0
    async with connection.stream("GET", url, headers=credentials(actor)) as response:
        assert response.status_code == 200, response.status_code
        async for chunk in response.aiter_bytes(chunk_size=4096):
            digest.update(chunk)
            count += len(chunk)
            if not started.is_set():
                record(
                    phase + "-download-first.json",
                    {
                        "epoch": time.time(),
                        "partial_bytes": count,
                        "total_bytes": descriptor["byte_size"],
                    },
                )
                started.set()
                await wait(phase + "-api-signal.json", timeout=30)
    assert (
        count == descriptor["byte_size"] and digest.hexdigest() == descriptor["sha256"]
    )
    return {
        "bytes": count,
        "sha256": digest.hexdigest(),
        "completed_epoch": time.time(),
    }


async def submit(connection, actor, key):
    ws = actor.values["workspace_id"]
    reply = await connection.post(
        f"/v1/workspaces/{ws}/jobs",
        headers={**credentials(actor), "idempotency-key": key},
        json={"operation": "preview", "source_id": actor.values["source_id"]},
    )
    assert reply.status_code == 202, reply.status_code
    receipt = reply.json()
    assert receipt["workspace_id"] == ws
    assert receipt["status_url"] == f"/v1/workspaces/{ws}/jobs/{receipt['job_id']}"
    return actor, key, receipt


async def job_rows(db, accepted):
    ids = [receipt["job_id"] for _, _, receipt in accepted]
    rows = await (
        await db.execute(
            "SELECT job_id,state,attempt_count,extract(epoch FROM terminal_at) FROM j01_jobs WHERE job_id=ANY(%s)",
            (ids,),
        )
    ).fetchall()
    assert len(rows) == len(ids) == len(set(ids))
    return {
        j: {
            "state": state,
            "attempts": attempts,
            "terminal_epoch": float(t) if t else None,
        }
        for j, state, attempts, t in rows
    }


async def finish(connection, db, accepted, count):
    async with asyncio.timeout(75):
        while True:
            rows = await job_rows(db, accepted)
            assert all(
                row["state"] in ("queued", "running", "succeeded")
                for row in rows.values()
            ), rows
            if all(row["state"] == "succeeded" for row in rows.values()):
                break
            await asyncio.sleep(0.20)
    for actor, key, receipt in accepted:
        assert rows[receipt["job_id"]]["attempts"] == count
        reply = await connection.get(
            receipt["status_url"] + "/output", headers=credentials(actor)
        )
        assert reply.status_code == 200, reply.status_code
        value = reply.json()
        assert (
            value["workspace_id"] == actor.values["workspace_id"]
            and value["job_id"] == receipt["job_id"]
        )
        assert value["result"]["source_sha256"] == actor.values["source_sha256"]
        _, _, repeated = await submit(connection, actor, key)
        assert repeated["job_id"] == receipt["job_id"]
    return rows


async def phase(connection, db, first, second, descriptor, label):
    await wait(label + "-arm.json")
    started = asyncio.Event()
    async with asyncio.TaskGroup() as tasks:
        transfer = tasks.create_task(
            download(connection, first, descriptor, label, started)
        )
        async with asyncio.timeout(15):
            await started.wait()
        choices = (first, second) * 3
        accepted = await asyncio.gather(
            *(submit(connection, a, label + "-" + uuid4().hex) for a in choices)
        )
        async with asyncio.timeout(12):
            while True:
                counts = await job_rows(db, accepted)
                running = [
                    key for key, value in counts.items() if value["state"] == "running"
                ]
                queued = sum(value["state"] == "queued" for value in counts.values())
                if len(running) == 2 and queued:
                    break
                await asyncio.sleep(0.04)
        record(
            label + "-cutover.json",
            {"epoch": time.time(), "running": running, "queued": queued},
        )
        done = await finish(connection, db, accepted, 1)
        control = await wait(label + "-complete.json")
    signal = json.loads((ROOT / (label + "-api-signal.json")).read_text())
    assert transfer.result()["completed_epoch"] >= signal["epoch"]
    assert all(
        done[key]["terminal_epoch"] > control["worker_signal_epoch"] for key in running
    ), (running, done, control)
    record(
        label + "-result.json",
        {
            "acknowledged": len(accepted),
            "successful": done,
            "download": transfer.result(),
            "running_at_cutover": running,
            "queued_at_cutover": queued,
            "controller": control,
        },
    )


async def fencing(connection, db, actor):
    await wait("fence-arm.json")
    accepted = [await submit(connection, actor, "fence-" + uuid4().hex)]
    digest = SecretStr(secrets.token_hex(32))
    dsn = Path("/run/worker/database_worker").read_text()
    async with AsyncConnectionPool(dsn, min_size=1, max_size=1, open=False) as pool:
        sources = PostgresSources(
            pool,
            maximum_upload_bytes=200 * 1024**2,
            maximum_expanded_bytes=400 * 1024**2,
            maximum_entries=100,
        )
        jobs = PostgresJobs(pool, sources, execution=True)
        claim = await jobs.claim(
            lease_digest=digest, lease_seconds=1, operations=("preview",)
        )
        assert claim is not None and claim.job_id == accepted[0][2]["job_id"]
        record("fence-claimed.json", {"epoch": time.time(), "claim": claim.job_id})
        await wait("fence-resume.json")
        async with asyncio.timeout(20):
            while True:
                rows = await job_rows(db, accepted)
                if rows[claim.job_id]["attempts"] == 2:
                    break
                await asyncio.sleep(0.02)
        assert rows[claim.job_id]["state"] == "running", rows
        assert not await jobs.heartbeat(
            claim=claim, lease_digest=digest, lease_seconds=30
        )
        assert not await jobs.complete(
            claim=claim, lease_digest=digest, result={"stale_marker": True}
        )
    done = await finish(connection, db, accepted, 2)
    attempts = await (
        await db.execute(
            "SELECT epoch,outcome FROM k09_job_attempts WHERE job_id=%s ORDER BY epoch",
            (claim.job_id,),
        )
    ).fetchall()
    assert (
        len(attempts) == 2 and attempts[0][0] == 1 and attempts[1] == (2, "succeeded")
    ), attempts
    record(
        "fence-result.json",
        {
            "jobs": done,
            "attempts": attempts,
            "stale_heartbeat_rejected": True,
            "stale_publish_rejected": True,
        },
    )


async def steady(connection, db, actor, stop, samples):
    while not stop.is_set():
        header = await read_header(connection, actor)
        ready = read_json(ROOT / "edge-state.json")["ready"]
        assert ready
        begin = time.monotonic()
        assert await (await db.execute("SELECT 1")).fetchone() == (1,)
        admin = time.monotonic() - begin
        connections = sum(r["count"] for r in (await observe(db))["connections"])
        samples.append(
            {
                "epoch": time.time(),
                "header": header["seconds"],
                "ready_replica_count": len(ready),
                "admin": admin,
                "db_connections": connections,
            }
        )
        await asyncio.sleep(0.10)


async def main():
    actors = [
        Actor.model_validate_json(line)
        for line in Path("/run/load/actors.jsonl").read_text().splitlines()
    ]
    large = [actor for actor in actors if actor.role == "owner" and actor.size == "L"]
    assert (
        len(large) == 2
        and large[0].values["workspace_id"] != large[1].values["workspace_id"]
    )
    first, second = [(await fresh_actor(TLS, actor))[0] for actor in large]
    dsn = Path("/run/load/observer_dsn").read_text()
    samples = []
    stop = asyncio.Event()
    async with (
        client(TLS, headers={"Connection": "close"}) as c,
        await psycopg.AsyncConnection.connect(dsn, autocommit=True) as db,
        await psycopg.AsyncConnection.connect(dsn, autocommit=True) as observer,
    ):
        descriptor = await create_export(c, first)
        async with asyncio.TaskGroup() as tasks:
            tasks.create_task(steady(c, observer, second, stop, samples))
            try:
                record(
                    "driver-ready.json",
                    {"epoch": time.time(), "download_bytes": descriptor["byte_size"]},
                )
                await phase(c, db, first, second, descriptor, "upgrade")
                await phase(c, db, first, second, descriptor, "rollback")
                await fencing(c, db, first)
            finally:
                stop.set()
                record("probe-samples.json", samples)
    summary = {
        name: stats([sample[name] for sample in samples])
        for name in ("header", "admin")
    }
    assert len(samples) >= 20
    assert max(sample["db_connections"] for sample in samples) <= 20
    record(
        "traffic-result.json",
        {
            "status": "passed",
            "probes": summary,
            "pg_peak_connections": max(sample["db_connections"] for sample in samples),
        },
    )


asyncio.run(main())
