"""Native K15.03/K15.04 evidence over real HTTPS, two APIs and owned PostgreSQL."""

from __future__ import annotations

import asyncio
import json
import os
import random
import secrets
import ssl
import subprocess
import sys
import time
from contextlib import AsyncExitStack
from pathlib import Path
from uuid import uuid4

import httpx2
import psycopg
from k15_tls_edge import Edge
from psycopg import sql
from pydantic import SecretStr
from test_k02_https_edge import _certificate
from test_k06_api_shutdown_postgres import ORIGIN, _application_url, _port
from test_k06_runtime_privileges_postgres import _role_url
from test_k07_previews_postgres import _bundle
from test_k14_missing_demand import diagnostic_child, process_snapshot
from test_k17_capacity_postgres import _identity
from test_k17_native_seed_postgres import _target

from aletheia_lab.hosted.action_audit_migration import migrate as migrate_audit
from aletheia_lab.hosted.capacity_seed import _session
from aletheia_lab.hosted.capacity_seed_models import SeedPlan
from aletheia_lab.hosted.job_polling import reconnect_delay_ms
from aletheia_lab.hosted.postgres_sources import PostgresSources
from aletheia_lab.hosted.preview_migration import migrate as migrate_preview
from aletheia_lab.hosted.preview_runtime_privileges import grant_preview_runtime
from aletheia_lab.hosted.upload_staging_migration import migrate as migrate_upload
from aletheia_lab.hosted.workspace_migration import migrate as migrate_workspace
from aletheia_lab.hosted_auth.csrf import CsrfTokens

HOST = "app.example.invalid"
OUT = Path("/evidence")


def headers(actor):
    return {
        "host": HOST,
        "origin": ORIGIN,
        "cookie": actor["cookie"],
        "x-csrf-token": actor["proof"],
    }


def client(url, tls):
    return httpx2.AsyncClient(
        base_url=url,
        verify=tls,
        trust_env=False,
        timeout=8,
        limits=httpx2.Limits(max_connections=1, max_keepalive_connections=1, keepalive_expiry=20),
    )


async def seed(pool, schema, secret):
    from importlib.resources import files

    actors = []
    plan = SeedPlan(namespace="k17_k15scope", idle_seconds=900)
    tokens = CsrfTokens(SecretStr(secret))
    sources = PostgresSources(
        pool, maximum_upload_bytes=1024**2, maximum_expanded_bytes=2 * 1024**2, maximum_entries=16
    )
    async with pool.connection() as conn:
        await migrate_workspace(conn, schema)
        await migrate_upload(conn, schema)
        await migrate_preview(conn, schema)
        await migrate_audit(conn, schema)
        await conn.execute(
            files("aletheia_lab.hosted_auth").joinpath("admission_schema.sql").read_text()
        )
        for i in range(106):
            workspace = f"k15_ws_{i // 5:02d}" if i < 100 else "k15_scope"
            user = f"k15_user_{i:03d}"
            await conn.execute(
                "INSERT INTO j01_workspaces(workspace_id,name) VALUES(%s,'K15 synthetic') ON CONFLICT DO NOTHING",
                (workspace,),
            )
            await conn.execute(
                "INSERT INTO hosted_identity_bindings VALUES('https://issuer.example.invalid',%s,%s,'Synthetic',%s,true,true,0,'ready',%s,NULL)",
                (user, user, user + "@aletheia.invalid", workspace),
            )
            await conn.execute(
                "INSERT INTO j01_memberships(workspace_id,user_id,role,active,revision) VALUES(%s,%s,'owner',true,1)",
                (workspace, user),
            )
            digest, context, raw, proof, _expiry = await _session(conn, user, tokens, plan)
            actors.append(
                dict(
                    user=user,
                    workspace=workspace,
                    context=context,
                    digest=digest,
                    proof=proof,
                    cookie=f"__Host-aletheia_session={raw}; __Host-aletheia_csrf={proof}",
                )
            )
    for a in actors:
        source = await sources.put(
            credential_digest=a["digest"],
            workspace_id=a["workspace"],
            request_key=SecretStr("k15-source-" + a["user"]),
            payload=_bundle(),
        )
        a.update(source=source.source_id, sha=source.sha256)
    return actors


async def admit(c, actor):
    r = await c.post(
        f"/v1/workspaces/{actor['workspace']}/jobs",
        headers={**headers(actor), "idempotency-key": "k15-poll-" + uuid4().hex},
        json={"operation": "preview", "source_id": actor["source"]},
    )
    assert r.status_code == 202, r.status_code
    value = r.json()
    assert value["workspace_id"] == actor["workspace"]
    return value["status_url"]


async def get(c, a, path, cursor=None):
    return await c.get(path, headers=headers(a), params=None if not cursor else {"cursor": cursor})


def check_update(r, a, job):
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store", r.status_code
    value = r.json()
    assert value["workspace_id"] == a["workspace"] and value["job_id"] == job.rsplit("/", 1)[-1]
    assert 0 < len(value["cursor"]) <= 4096 and value["authorization_valid_for_ms"] == 4000
    assert not {"result", "request", "credential_digest", "source_sha256"} & set(value)
    return value


def denial(r, job, cursor=None, expected=None):
    assert r.status_code in (401, 403, 404) and r.headers["cache-control"] == "no-store", (
        r.status_code
    )
    assert expected is None or r.status_code == expected
    body = r.json()
    assert body["code"] in (
        "not_found",
        "session_expired",
        "session_revoked",
        "forbidden",
        "workspace_unavailable",
    )
    assert job.rsplit("/", 1)[-1] not in r.text and (not cursor or cursor not in r.text)
    assert not {
        "cursor",
        "job",
        "result",
        "source_id",
        "workspace_id",
        "job_id",
        "revision",
        "progress",
        "output_url",
    } & set(body)
    return {
        "status": r.status_code,
        "code": body["code"],
        "bytes": len(r.content),
        "no_store": True,
        "no_private_fields": True,
    }


async def change(conn, actor, kind):
    if kind in ("membership", "role"):
        stmt = (
            "UPDATE j01_memberships SET "
            + ("active=false" if kind == "membership" else "role='viewer'")
            + ",revision=revision+1 WHERE workspace_id=%s AND user_id=%s"
        )
        r = await conn.execute(stmt, (actor["workspace"], actor["user"]))
    elif kind == "account":
        r = await conn.execute(
            "UPDATE hosted_identity_bindings SET enabled=false,account_epoch=account_epoch+1 WHERE user_id=%s",
            (actor["user"],),
        )
    else:
        assert kind == "session"
        r = await conn.execute(
            "UPDATE j01_sessions SET state='revoked' WHERE context_id=%s", (actor["context"],)
        )
    assert r.rowcount == 1


async def authority(pool, ports, tls, actors, log):
    a, b, c, d, e, peer = actors
    direct = [f"https://127.0.0.1:{p}" for p in ports]
    async with AsyncExitStack() as stack:
        first, second = [await stack.enter_async_context(client(u, tls)) for u in direct]
        paths = [await admit(first, p) for p in (a, b, c, d, e)]
        # A request waiting on the membership transaction must observe its committed revocation.
        path = paths[0]
        old = check_update(await get(first, a, path + "/updates"), a, path)
        async with pool.connection() as owner:
            # Match workspace management's lock order before the deferred owner guard.
            await owner.execute(
                "SELECT workspace_id FROM j01_workspaces WHERE workspace_id=%s FOR UPDATE",
                (a["workspace"],),
            )
            await change(owner, a, "membership")
            blocker = (await (await owner.execute("SELECT pg_backend_pid()")).fetchone())[0]
            waiting = asyncio.create_task(get(second, a, path + "/updates", old["cursor"]))
            async with asyncio.timeout(3):
                while True:
                    await owner.execute("SELECT pg_stat_clear_snapshot()")
                    row = await (
                        await owner.execute(
                            "SELECT count(*) FROM pg_stat_activity WHERE application_name='k15-api1' AND wait_event_type='Lock' AND %s=ANY(pg_blocking_pids(pid))",
                            (blocker,),
                        )
                    ).fetchone()
                    if row[0] != 0:
                        break
                    await asyncio.sleep(0.025)
            await owner.commit()
            t = time.monotonic()
        response = await waiting
        log.append(
            dict(
                case="membership_committed_before_blocked_replica_authorization",
                blocked_on_real_pg_lock=True,
                after_commit_ms=(time.monotonic() - t) * 1000,
                **denial(response, path, old["cursor"]),
            )
        )
        assert log[-1]["after_commit_ms"] <= 5000
        for api in (first, second):
            denial(await get(api, a, path + "/updates", "invalid-cursor"), path)
            denial(await get(api, a, path + "/output"), path)

        for actor, path, kind in ((b, paths[1], "account"), (c, paths[2], "session")):
            old = check_update(await get(first, actor, path + "/updates"), actor, path)
            t = time.monotonic()
            async with pool.connection() as owner:
                await change(owner, actor, kind)
            outcomes = []
            for api in (first, second):
                outcomes.append(
                    denial(
                        await get(api, actor, path + "/updates", old["cursor"]), path, old["cursor"]
                    )
                )
                denial(await get(api, actor, path + "/output"), path)
            elapsed = (time.monotonic() - t) * 1000
            assert elapsed <= 5000
            log.append(
                dict(case=kind + "_revoke_both_replicas", outcomes=outcomes, elapsed_ms=elapsed)
            )

        path = paths[3]
        old = check_update(await get(first, d, path + "/updates"), d, path)
        cross = await get(second, peer, path + "/updates", old["cursor"])
        assert cross.status_code in (400, 403, 404), cross.status_code
        assert old["cursor"] not in cross.text and path.rsplit("/", 1)[-1] not in cross.text
        assert not {"cursor", "job", "result", "revision"} & set(cross.json())
        log.append(
            dict(case="other_account_replay", status=cross.status_code, no_private_fields=True)
        )
        async with pool.connection() as owner:
            await change(owner, d, "role")
        invalid = await get(second, d, path + "/updates", old["cursor"])
        assert invalid.status_code == 400 and invalid.json()["code"] == "cursor_invalid"
        assert "cursor" not in invalid.json() and "job" not in invalid.json()
        clean = check_update(await get(first, d, path + "/updates"), d, path)
        log.append(
            dict(
                case="role_change_invalidates_old_cursor_preserves_read_if_allowed",
                replay=invalid.status_code,
                fresh=200,
                revision=clean["revision"],
            )
        )

        path = paths[4]
        edge = await stack.enter_async_context(Edge([ports[0]]))
        buffered = await stack.enter_async_context(client(edge.url, tls))
        current = check_update(await get(buffered, e, path + "/updates"), e, path)
        edge.hold.set()
        start = time.monotonic()
        delayed = asyncio.create_task(get(buffered, e, path + "/updates", current["cursor"]))
        await asyncio.wait_for(edge.held.wait(), 3)
        async with pool.connection() as owner:
            await change(owner, e, "membership")
        t = time.monotonic()
        post = denial(
            await get(second, e, path + "/updates", current["cursor"]), path, current["cursor"]
        )
        elapsed = (time.monotonic() - t) * 1000
        assert elapsed <= 5000
        await asyncio.sleep(max(0, 4.25 - (time.monotonic() - start)))
        edge.release.set()
        held_response = await delayed
        age = (time.monotonic() - start) * 1000
        assert (
            held_response.status_code == 200
            and held_response.headers["cache-control"] == "no-store"
        )
        applied = age < current["authorization_valid_for_ms"]
        assert not applied
        log.append(
            dict(
                case="pre_authorized_tls_response_held_across_revoke",
                buffered_wire_status=held_response.status_code,
                consumer_applied=applied,
                request_start_age_ms=age,
                lease_ms=4000,
                held_tls_bytes=edge.counts["held_tls_bytes"],
                next_replica_denial=post,
                next_replica_denial_ms=elapsed,
            )
        )


async def churn(pool, ports, tls, actors, roots, report):
    records = []
    snapshots = []
    db_samples = []
    rng = random.Random(1504)
    stop = asyncio.Event()
    baseline = process_snapshot(roots)

    async def sample():
        async with pool.connection() as conn:
            while not stop.is_set():
                snapshots.append(process_snapshot(roots))
                row = await (
                    await conn.execute(
                        "SELECT application_name,count(*) FROM pg_stat_activity WHERE datname=current_database() GROUP BY 1"
                    )
                ).fetchall()
                db_samples.append(dict(epoch=time.time(), counts=dict(row)))
                await asyncio.sleep(0.15)

    monitor = asyncio.create_task(sample())
    try:
        async with AsyncExitStack() as stack:
            main = await stack.enter_async_context(Edge(ports))
            reset = await stack.enter_async_context(Edge(ports))
            idle = await stack.enter_async_context(Edge(ports, idle=2.0))
            c = [
                await stack.enter_async_context(client(edge.url, tls))
                for edge in [reset] * 15 + [idle] * 15 + [main] * 70
            ]
            gate = asyncio.Semaphore(12)

            async def warm(i):
                async with gate:
                    path = await admit(c[i], actors[i])
                    r = await get(c[i], actors[i], path + "/updates")
                    value = check_update(r, actors[i], path)
                    assert value["job"]["status"] == "queued"
                    return path, value

            steady = await asyncio.gather(*(warm(i) for i in range(30, 100)))
            disturbed = await asyncio.gather(*(warm(i) for i in range(30)))
            warmed = disturbed + steady
            assert len({path for path, _ in warmed}) == 100
            assert len(reset.active) == 15
            reset.offline = idle.offline = True
            reset.abort()
            async with asyncio.timeout(4):
                while idle.active:
                    await asyncio.sleep(0.025)
            assert reset.counts["forced_abort"] == 15 and idle.counts["idle_timeout"] == 15
            delays = [reconnect_delay_ms(0, rng.random()) for _ in range(30)]

            async def fault(i):
                path, state = warmed[i]
                try:
                    await get(c[i], actors[i], path + "/updates", state["cursor"])
                except httpx2.TransportError:
                    return True
                raise AssertionError("fault was not observable to client")

            assert all(await asyncio.gather(*(fault(i) for i in range(30))))
            assert reset.counts["fault_refused"] <= 15 and idle.counts["fault_refused"] <= 15
            report["injected_faults"] = {
                "forced_abort": reset.counts["forced_abort"],
                "proxy_idle": idle.counts["idle_timeout"],
                "observable_transport_errors": 30,
            }
            reset.offline = idle.offline = False
            idle.idle = 20.0
            for i in range(30):
                await c[i].aclose()
                c[i] = await stack.enter_async_context(client((reset if i < 15 else idle).url, tls))
            start = time.monotonic()

            async def resume(i):
                path, old = warmed[i]
                actor = actors[i]
                initial = delays[i] if i < 30 else old["poll_after_ms"]
                await asyncio.sleep(initial / 1000)
                scheduled = (time.monotonic() - start) * 1000
                retries = []
                for attempt in range(4):
                    tick = time.monotonic()
                    try:
                        r = await get(c[i], actor, path + "/updates", old["cursor"])
                    except httpx2.TransportError as failure:
                        wait = reconnect_delay_ms(attempt, rng.random())
                        retries.append(
                            dict(
                                status=None,
                                transport_error=type(failure).__name__,
                                latency_ms=(time.monotonic() - tick) * 1000,
                                wait_ms=wait,
                            )
                        )
                        await asyncio.sleep(wait / 1000)
                        continue
                    wall = (time.monotonic() - tick) * 1000
                    retries.append(
                        dict(status=r.status_code, latency_ms=wall, bytes=len(r.content))
                    )
                    if r.status_code == 200:
                        value = check_update(r, actor, path)
                        assert (
                            value["revision"] == old["revision"]
                            and value["disposition"] == "unchanged"
                        )
                        return dict(
                            index=i,
                            job=path.rsplit("/", 1)[-1],
                            fault="reset" if i < 15 else "idle" if i < 30 else "none",
                            scheduled_ms=initial,
                            actual_first_request_ms=scheduled,
                            observed_unchanged=True,
                            requests=retries,
                            revision=value["revision"],
                        )
                    assert (
                        r.status_code in (429, 503) and r.headers["cache-control"] == "no-store"
                    ), r.status_code
                    wait = reconnect_delay_ms(
                        attempt, rng.random(), retry_after_ms=1000 * int(r.headers["retry-after"])
                    )
                    retries[-1]["wait_ms"] = wait
                    await asyncio.sleep(wait / 1000)
                raise AssertionError("bounded reconnect attempts exhausted")

            records = await asyncio.gather(*(resume(i) for i in range(100)))
            async with pool.connection() as conn:
                ids = [x["job"] for x in records]
                actual = await (
                    await conn.execute(
                        "SELECT job_id,state,status_revision,attempt_count FROM j01_jobs WHERE job_id=ANY(%s)",
                        (ids,),
                    )
                ).fetchall()
            byid = {j: (state, rev, attempt) for j, state, rev, attempt in actual}
            assert len(byid) == 100 and all(
                byid[x["job"]] == ("queued", x["revision"], 0) for x in records
            )
            assert len({int(r["actual_first_request_ms"] // 100) for r in records[:30]}) >= 3
            report.update(
                pending_jobs=100,
                principals=len({a["user"] for a in actors}),
                transport_failure_count=30,
                reconnect_records=records,
                queued_ledger_verified=100,
                server_ms=(time.monotonic() - start) * 1000,
                edge_stats={
                    "steady": dict(main.counts),
                    "reset": dict(reset.counts),
                    "idle": dict(idle.counts),
                },
            )
        await asyncio.sleep(0.35)
        final = process_snapshot(roots)
        assert all(
            final["roles"][r]["open_fds"] <= baseline["roles"][r]["open_fds"] + 4
            for r in ("api0", "api1", "generator")
        )
        report["resource_before"] = baseline
        report["resource_after"] = final
        assert all(len(edge.active) == 0 for edge in (main, reset, idle))
        report["ingress_connections_after_drain"] = 0
        return [(a, path) for a, (path, _) in zip(actors, warmed, strict=True)]
    finally:
        stop.set()
        await monitor
        report["process_samples"] = snapshots
        report["db_samples"] = db_samples


async def settle(pool, url, tls, admitted):
    ids = [p.rsplit("/", 1)[-1] for _, p in admitted]
    async with pool.connection() as conn, asyncio.timeout(180):
        while True:
            rows = await (
                await conn.execute(
                    "SELECT job_id,state,status_revision,attempt_count FROM j01_jobs WHERE job_id=ANY(%s)",
                    (ids,),
                )
            ).fetchall()
            assert len(rows) == 100 and all(
                r[1] not in ("failed", "expired", "cancelled") for r in rows
            )
            if all(r[1] == "succeeded" for r in rows):
                break
            await asyncio.sleep(0.4)
    ledger = {r[0]: r[1:] for r in rows}
    checked = 0
    async with client(url, tls) as c:
        for a, path in admitted:
            updated = check_update(await get(c, a, path + "/updates"), a, path)
            assert (
                updated["job"]["status"],
                updated["revision"],
                updated["job"]["attempt"],
            ) == ledger[updated["job_id"]]
            assert updated["poll_after_ms"] is None and ledger[updated["job_id"]][-1] == 1
            output = await get(c, a, path + "/output")
            assert (
                output.status_code == 200 and output.json()["result"]["source_sha256"] == a["sha"]
            )
            checked += 1
    return dict(
        terminal_ledger_verified=checked,
        source_hash_verified=checked,
        unique_job_ids=len(ledger),
        all_succeeded_one_attempt=True,
    )


async def exercise(tmp):
    repository = Path(__file__).resolve().parents[3]
    database = os.environ["K15_TEST_DATABASE_URL"]
    os.environ["K17_TEST_DATABASE_URL"] = database
    identity = _identity(repository)
    secret = secrets.token_urlsafe(48)
    password = secrets.token_urlsafe(36)
    evidence = {
        "status": "failed",
        "runtime_identity": identity,
        "K15_03": [],
        "K15_04": {},
        "scope": "Backend of two native API processes behind verified HTTPS/TCP proxies. Synthetic sessions; no browser or external IdP.",
    }
    processes = {}
    actors = []
    try:
        async with _target() as (pool, schema), AsyncExitStack() as stack:
            actors = await seed(pool, schema, secret)
            roles = {k: "k15_" + k + "_" + uuid4().hex for k in ("api", "worker")}
            async with await psycopg.AsyncConnection.connect(database, autocommit=True) as conn:
                for role in roles.values():
                    await conn.execute(
                        sql.SQL(
                            "CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS PASSWORD {}"
                        ).format(sql.Identifier(role), sql.Literal(password))
                    )
            async with pool.connection() as conn:
                for kind, role in roles.items():
                    await grant_preview_runtime(conn, schema, role, profile=kind, audit=True)
            cert, key = _certificate(tmp)
            tls = ssl.create_default_context(cafile=str(cert))
            (tmp / "objects").mkdir()
            (tmp / "worker-private").mkdir(mode=0o700)

            async def close_child(p):
                if p.poll() is None:
                    p.terminate()
                try:
                    await asyncio.to_thread(p.wait, 10)
                except subprocess.TimeoutExpired:
                    p.kill()
                    await asyncio.to_thread(p.wait, 5)

            def launch(kind, index):
                name = kind + str(index)
                port = _port()
                config = tmp / (name + ".json")
                config.write_text(
                    json.dumps(
                        {
                            "environment": "local",
                            "public_origin": ORIGIN,
                            "identity": {
                                "issuer": "https://issuer.example.invalid",
                                "client_id": "synthetic-k15",
                                "redirect_uri": ORIGIN + "/v1/auth/callback",
                                "client_kind": "public",
                            },
                            "storage": {
                                "database_url_env": "ALETHEIA_HOSTED_DATABASE_URL",
                                "object_root": str(tmp),
                                "expected_schema": schema,
                                "expected_database_role": roles[kind],
                            },
                            "api": {
                                "port": port,
                                "database_pool_size": 4,
                                "graceful_shutdown_seconds": 10,
                            },
                            "worker": {"database_pool_size": 2, "job_slots_per_process": 2},
                        }
                    )
                )
                env = dict(
                    os.environ,
                    PYTHONPATH=str(repository / "src"),
                    ALETHEIA_HOSTED_CSRF_SECRET=secret,
                    ALETHEIA_HOSTED_DATABASE_URL=_application_url(
                        _role_url(database, schema, roles[kind], password), "k15-" + name
                    ),
                    ALETHEIA_RELEASE_ID=identity,
                    OMP_NUM_THREADS="1",
                    OPENBLAS_NUM_THREADS="1",
                    MKL_NUM_THREADS="1",
                    K14_RESOURCE_SAMPLE_FILE=str(tmp / (name + ".resources.jsonl")),
                )
                log = stack.enter_context((tmp / (name + ".safe.log")).open("wb"))
                p = subprocess.Popen(
                    [
                        sys.executable,
                        "-B",
                        "-c",
                        diagnostic_child(),
                        kind,
                        str(config),
                        str(cert),
                        str(key),
                    ],
                    cwd=repository,
                    env=env,
                    stdout=subprocess.DEVNULL,
                    stderr=log,
                )
                processes[name] = p
                stack.push_async_callback(close_child, p)
                return port

            ports = [launch("api", i) for i in range(2)]
            for port in ports:
                async with client(f"https://127.0.0.1:{port}", tls) as c, asyncio.timeout(30):
                    while True:
                        try:
                            if (
                                await c.get("/health/ready", headers={"host": HOST})
                            ).status_code == 200:
                                break
                        except httpx2.TransportError:
                            pass
                        await asyncio.sleep(0.1)
            roots = {
                "postgres": 1,
                "generator": os.getpid(),
                **{name: p.pid for name, p in processes.items()},
            }
            if os.environ.get("K15_RECONNECT_ONLY") != "1":
                await authority(pool, ports, tls, actors[100:], evidence["K15_03"])
                print("K15_03_NATIVE_CASES=" + str(len(evidence["K15_03"])), flush=True)
            admitted = await churn(pool, ports, tls, actors[:100], roots, evidence["K15_04"])
            (OUT / "scenario-progress.json").write_text(json.dumps(evidence, indent=2))
            for i in range(2):
                launch("worker", i)
            started = time.monotonic()
            async with Edge(ports) as edge:
                evidence["K15_04"].update(await settle(pool, edge.url, tls, admitted))
            evidence["K15_04"]["worker_drain_and_verify_seconds"] = time.monotonic() - started
            evidence["status"] = "passed_native_https"
    finally:
        for path in tmp.glob("*.safe.log"):
            data = path.read_bytes()
            known = [
                secret,
                password,
                database,
                *[a["proof"] for a in actors],
                *[a["cookie"].split(";", 1)[0].split("=", 1)[1] for a in actors],
            ]
            assert not any(x.encode() in data for x in known)
            (OUT / path.name).write_bytes(data)
        evidence["source_unchanged"] = _identity(repository) == identity
        (OUT / "k15-result.json").write_text(json.dumps(evidence, indent=2))
        assert evidence["source_unchanged"]


def test_pending_k15_cases(tmp_path):
    assert os.environ.get("K15_TEST_DATABASE_URL")
    asyncio.run(exercise(tmp_path))
