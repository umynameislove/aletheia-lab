"""Owned staging helpers; the API and worker remain the installed production entrypoints."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import resource
import signal
import subprocess
import sys
import threading
import time
from contextlib import suppress
from pathlib import Path


def metadata():
    return json.loads(Path("/run/config/stage.json").read_text())


def sample():
    rows = []
    for p in Path("/proc").glob("[0-9]*"):
        try:
            fields = (p / "stat").read_text().rsplit(") ", 1)[1].split()
            fds = len(list((p / "fd").iterdir()))
            rows.append(
                {
                    "pid": int(p.name),
                    "parent": int(fields[1]),
                    "start_tick": int(fields[19]),
                    "rss": int(fields[21]) * os.sysconf("SC_PAGE_SIZE"),
                    "fds": fds,
                }
            )
        except (OSError, ValueError, IndexError):
            continue
    cg = Path("/sys/fs/cgroup")
    unified = (cg / "cpu.stat").exists()
    cpu_stats = cg / ("cpu.stat" if unified else "cpu/cpu.stat")
    counters = {k: int(v) for k, v in (line.split() for line in cpu_stats.read_text().splitlines())}
    if not unified:
        counters["usage_usec"] = int((cg / "cpuacct/cpuacct.usage").read_text()) // 1000
    paths = (
        ("memory.current", "memory.peak", "memory.max", "pids.current")
        if unified
        else (
            "memory/memory.usage_in_bytes",
            "memory/memory.max_usage_in_bytes",
            "memory/memory.limit_in_bytes",
            "pids/pids.current",
        )
    )
    current, peak, memory_max, pids = ((cg / p).read_text().strip() for p in paths)
    cpu_max = (
        (cg / "cpu.max").read_text().strip()
        if unified
        else " ".join(
            (cg / "cpu" / name).read_text().strip()
            for name in ("cpu.cfs_quota_us", "cpu.cfs_period_us")
        )
    )
    net = {}
    for line in Path("/proc/net/dev").read_text().splitlines()[2:]:
        name, values = line.split(":")
        v = values.split()
        net[name.strip()] = {"rx_bytes": int(v[0]), "tx_bytes": int(v[8])}
    return {
        "monotonic": time.monotonic(),
        "epoch": time.time(),
        "cpu": counters,
        "cgroup_version": 2 if unified else 1,
        "memory_current": int(current),
        "memory_peak": int(peak),
        "cpu_max": cpu_max,
        "memory_max": memory_max,
        "pids": int(pids),
        "fd_soft_limit": resource.getrlimit(resource.RLIMIT_NOFILE)[0],
        "processes": rows,
        "network": net,
        "ingress": dict(globals().get("TRANSPORT", {})),
    }


def monitor(stop):
    label = os.environ["K14_COMPONENT"]
    with Path("/run/metrics", label + ".jsonl").open("a", buffering=1) as f:
        while not stop.is_set():
            f.write(json.dumps(sample()) + "\n")
            stop.wait(0.25)


def watch(role):
    stop = threading.Event()
    thread = threading.Thread(target=monitor, args=(stop,))
    thread.start()
    child = subprocess.Popen(
        [sys.executable, "-B", "-m", "aletheia_lab.hosted.container_entrypoint", role]
    )

    def terminate(*_):
        if child.poll() is None:
            child.terminate()

    signal.signal(signal.SIGTERM, terminate)
    signal.signal(signal.SIGINT, terminate)
    try:
        raise SystemExit(child.wait())
    finally:
        stop.set()
        thread.join(5)


TRANSPORT = dict(accepted=0, active=0, peak_active=0, rejected=0, to_api_bytes=0, to_client_bytes=0)


async def ingress():
    config = metadata()
    stop = asyncio.Event()
    tasks = set()
    asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, stop.set)

    async def connection(reader, writer):
        if TRANSPORT["active"] >= 32:
            TRANSPORT["rejected"] += 1
            writer.close()
            await writer.wait_closed()
            return
        ordinal = TRANSPORT["accepted"]
        TRANSPORT["accepted"] += 1
        TRANSPORT["active"] += 1
        TRANSPORT["peak_active"] = max(TRANSPORT["peak_active"], TRANSPORT["active"])
        upstream = None
        try:
            remote, upstream = await asyncio.open_connection("api" + str(ordinal % 2), 18766)

            async def pipe(source, target, key):
                while data := await source.read(65536):
                    TRANSPORT[key] += len(data)
                    target.write(data)
                    await target.drain()

            pumps = [
                asyncio.create_task(pipe(reader, upstream, "to_api_bytes")),
                asyncio.create_task(pipe(remote, writer, "to_client_bytes")),
            ]
            try:
                await asyncio.wait(pumps, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for task in pumps:
                    task.cancel()
                await asyncio.gather(*pumps, return_exceptions=True)
        finally:
            TRANSPORT["active"] -= 1
            for stream in (upstream, writer):
                if stream is not None:
                    stream.close()
                    with suppress(OSError):
                        await stream.wait_closed()

    def begin(reader, writer):
        task = asyncio.create_task(connection(reader, writer))
        tasks.add(task)
        task.add_done_callback(tasks.discard)

    server = await asyncio.start_server(begin, "0.0.0.0", config["app_port"], backlog=64)
    async with server:
        await stop.wait()
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


def provision():
    """Copy private input into separately mounted role volumes, never into the image."""
    private = Path("/private")
    setup = json.loads((private / "provision.json").read_text())
    for directory, owner in setup["volumes"].items():
        target = Path("/volumes", directory)
        target.mkdir(exist_ok=True)
        os.chown(target, *owner)
        target.chmod(0o700)
    for item in setup["files"]:
        source, target = private / item["source"], Path("/volumes") / item["destination"]
        assert target.resolve().is_relative_to(Path("/volumes"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
        os.chown(target, *item["owner"])
        target.chmod(item["mode"])
    print("K14_STAGING_PRIVATE_PROVISION=PASS", flush=True)


async def initialize():
    import secrets
    from importlib.resources import files
    from urllib.parse import quote

    import psycopg
    from psycopg import sql
    from psycopg_pool import AsyncConnectionPool
    from pydantic import SecretStr

    from aletheia_lab.hosted.action_audit_migration import migrate as migrate_audit
    from aletheia_lab.hosted.capacity_seed import SeedLimits, seed_native
    from aletheia_lab.hosted.capacity_seed_models import SeedPlan
    from aletheia_lab.hosted.persistence_migration import migrate
    from aletheia_lab.hosted.preview_migration import migrate as migrate_preview
    from aletheia_lab.hosted.preview_runtime_privileges import grant_preview_runtime
    from aletheia_lab.hosted.project_policy_migration import migrate as migrate_policy
    from aletheia_lab.hosted.review_migration import migrate as migrate_review
    from aletheia_lab.hosted.upload_staging_migration import migrate as migrate_upload
    from aletheia_lab.hosted.workspace_migration import migrate as migrate_workspace

    config = metadata()
    secret = json.loads(Path("/run/admin/bootstrap.json").read_text())
    schema = config["schema"]
    observer = "observer_" + config["run_id"] + secret.get("observer_suffix", "")
    observer_password = secrets.token_urlsafe(36)
    async with await psycopg.AsyncConnection.connect(secret["owner_dsn"], autocommit=True) as conn:
        await conn.execute(
            sql.SQL("CREATE SCHEMA {} AUTHORIZATION CURRENT_USER").format(sql.Identifier(schema))
        )
        await conn.execute(
            sql.SQL("REVOKE CREATE ON DATABASE {} FROM PUBLIC").format(sql.Identifier("aletheia"))
        )
        for role in ("api", "worker"):
            await conn.execute(
                sql.SQL(
                    "CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS PASSWORD {}"
                ).format(
                    sql.Identifier(config[role + "_role"]), sql.Literal(secret[role + "_password"])
                )
            )
        await conn.execute(
            sql.SQL(
                "CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE INHERIT NOBYPASSRLS PASSWORD {}"
            ).format(sql.Identifier(observer), sql.Literal(observer_password))
        )
        await conn.execute(
            sql.SQL("GRANT pg_read_all_stats TO {}").format(sql.Identifier(observer))
        )
    scoped = secret["owner_dsn"] + "&options=-csearch_path%3D" + schema
    async with AsyncConnectionPool(scoped, min_size=1, max_size=2, open=False) as pool:
        await pool.wait(10)
        async with pool.connection() as conn:
            await migrate(conn, schema)
            await migrate_policy(conn, schema)
            await migrate_review(conn, schema)
        plan = SeedPlan(
            namespace="k17_stg" + config["run_id"],
            generations=1,
            results_per_generation=1,
            source_rows={"S": 1000, "M": 10000, "L": 100000},
        )
        output = Path("/var/lib/aletheia/seed")
        manifest = await seed_native(
            pool,
            schema=schema,
            output=output,
            plan=plan,
            csrf_secret=SecretStr(secret["csrf_secret"]),
            limits=SeedLimits(500 * 1024**2, 300000, 100, maximum_seconds=300),
        )
        (output / "objects").rename("/var/lib/aletheia/catalog")
        actors = (output / "actors.jsonl").read_bytes()
        Path("/run/load/actors.jsonl").write_bytes(actors)
        Path("/run/load/actors.jsonl").chmod(0o400)
        Path("/run/load/password").write_text(secret["user_password"])
        Path("/run/load/password").chmod(0o400)
        (output / "actors.jsonl").unlink()
        async with pool.connection() as conn:
            await migrate_workspace(conn, schema)
            await migrate_upload(conn, schema)
            await migrate_preview(conn, schema)
            await migrate_audit(conn, schema)
            await conn.execute(
                files("aletheia_lab.hosted_auth").joinpath("admission_schema.sql").read_text()
            )
            for index in range(plan.principals):
                await conn.execute(
                    "UPDATE hosted_identity_bindings SET issuer=%s,subject=%s,email=%s WHERE user_id=%s",
                    (
                        config["issuer"],
                        config["subjects"][index],
                        f"u{index}@aletheia.invalid",
                        plan.user(index),
                    ),
                )
            for role in ("api", "worker"):
                await grant_preview_runtime(
                    conn, schema, config[role + "_role"], profile=role, audit=True
                )
            await conn.execute(
                sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(
                    sql.Identifier(schema), sql.Identifier(observer)
                )
            )
            for table, columns in {
                "j01_jobs": "workspace_id,job_id,operation,state,created_at,terminal_at,attempt_count",
                "k09_job_attempts": "workspace_id,job_id,epoch,started_at,finished_at,outcome,failure_kind",
            }.items():
                await conn.execute(
                    sql.SQL("GRANT SELECT ({}) ON {} TO {}").format(
                        sql.SQL(columns), sql.Identifier(table), sql.Identifier(observer)
                    )
                )
        observer_dsn = secret["owner_dsn"].replace(
            "postgres:" + quote(secret["pg_password"], safe=""), observer + ":" + observer_password
        )
        observer_dsn += "&options=-csearch_path%3D" + schema
        Path("/run/load/observer_dsn").write_text(observer_dsn)
        Path("/run/load/observer_dsn").chmod(0o400)
        recorded = {
            "seed_manifest": manifest,
            "catalog_root": "/var/lib/aletheia/catalog",
            "actor_file_sha256": hashlib.sha256(actors).hexdigest(),
            "actors_stored_in_private_client_volume": True,
            "schema": schema,
        }
        Path("/evidence/deployment-dataset.json").write_text(json.dumps(recorded, indent=2))
    print("K14_STAGING_NATIVE_DATA_READY=PASS", flush=True)


if __name__ == "__main__":
    mode = sys.argv[1]
    if mode == "provision":
        provision()
    elif mode == "initialize":
        asyncio.run(initialize())
    elif mode in ("api", "worker"):
        watch(mode)
    elif mode == "ingress":
        stop = threading.Event()
        thread = threading.Thread(target=monitor, args=(stop,))
        thread.start()
        try:
            asyncio.run(ingress())
        finally:
            stop.set()
            thread.join(5)
    else:
        raise ValueError("Explicit staging helper mode required")
