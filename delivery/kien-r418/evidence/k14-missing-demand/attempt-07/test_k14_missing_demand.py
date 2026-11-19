"""Only the missing K14 demand attribution and mixed-operation fairness probes."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
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
import pytest
from psycopg import sql
from pydantic import SecretStr
from test_k02_https_edge import _certificate
from test_k05_native_results_postgres import _run
from test_k06_api_shutdown_postgres import ORIGIN, _application_url, _port
from test_k06_runtime_privileges_postgres import _role_url
from test_k14_process_envelope_postgres import _INGRESS, _MONITOR, _monitored_child, _summary
from test_k17_capacity_postgres import _identity
from test_k17_native_seed_postgres import _target

from aletheia_lab.hosted.action_audit_migration import migrate as migrate_audit
from aletheia_lab.hosted.capacity import CapacityHarness
from aletheia_lab.hosted.capacity_models import Actor
from aletheia_lab.hosted.capacity_profiles import calibration_profile, expanded_profile
from aletheia_lab.hosted.capacity_seed import SeedLimits, seed_native
from aletheia_lab.hosted.capacity_seed_models import SeedPlan
from aletheia_lab.hosted.preview_migration import migrate as migrate_preview
from aletheia_lab.hosted.preview_runtime_privileges import grant_preview_runtime
from aletheia_lab.hosted.upload_staging_migration import migrate as migrate_upload
from aletheia_lab.hosted.workspace_migration import migrate as migrate_workspace
from aletheia_lab.hosted_contract.session_capabilities import Session

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.environ.get("K14_RUN_MISSING") != "1" or sys.platform != "linux",
        reason="Explicit owned Linux K14 demand fixture required",
    ),
]


class MeasuredHarness(CapacityHarness):
    def __init__(self, *args):
        super().__init__(*args)
        self.actions = []

    def _finish(self, item, outcome, elapsed, *, response):
        super()._finish(item, outcome, elapsed, response=response)
        self.actions.append(
            {
                "sequence": item.sequence,
                "journey": item.journey.name,
                "size": item.actor.size,
                "outcome": outcome,
                "elapsed_seconds": elapsed,
                "deadline_seconds": item.journey.deadline(item.actor.size),
            }
        )


def process_snapshot(roots):
    """Include reaped child CPU; identify every live PID by its start tick."""
    ticks, page = os.sysconf("SC_CLK_TCK"), os.sysconf("SC_PAGE_SIZE")
    rows = {}
    for p in Path("/proc").iterdir():
        if not p.name.isdigit():
            continue
        try:
            v = (p / "stat").read_text().rsplit(") ", 1)[1].split()
            rows[int(p.name)] = {
                "parent": int(v[1]),
                "cpu_self_seconds": (int(v[11]) + int(v[12])) / ticks,
                "start_tick": int(v[19]),
                "cpu_including_reaped_seconds": sum(int(v[i]) for i in (11, 12, 13, 14)) / ticks,
                "rss_bytes": int(v[21]) * page,
            }
        except (OSError, ValueError, IndexError):
            continue
        try:
            rows[int(p.name)]["open_fds"] = len(list((p / "fd").iterdir()))
        except OSError:
            rows[int(p.name)]["open_fds"] = None
    result = {"monotonic": time.monotonic(), "roles": {}}
    for role, pid in roots.items():
        assert pid in rows, (role, "process missing")
        children = {pid}
        while role != "generator":
            extra = {n for n, v in rows.items() if v["parent"] in children}
            if extra <= children:
                break
            children |= extra
        selected = {str(n): rows[n] for n in children if n in rows}
        result["roles"][role] = {
            "root_pid": pid,
            "root_start_tick": rows[pid]["start_tick"],
            "cpu_seconds": sum(
                v["cpu_self_seconds"] if role == "generator" else v["cpu_including_reaped_seconds"]
                for v in selected.values()
            ),
            "rss_sum_bytes": sum(v["rss_bytes"] for v in selected.values()),
            "open_fds": (
                sum(v["open_fds"] for v in selected.values())
                if all(v["open_fds"] is not None for v in selected.values())
                else None
            ),
            "processes": selected,
        }
    seen = set()
    for values in result["roles"].values():
        current = set(values["processes"])
        assert not seen & current, "role CPU/RSS process sets overlap"
        seen |= current
    return result


def cpu_delta(before, after):
    result = {}
    for role, b in before["roles"].items():
        a = after["roles"][role]
        assert (a["root_pid"], a["root_start_tick"]) == (b["root_pid"], b["root_start_tick"])
        value = a["cpu_seconds"] - b["cpu_seconds"]
        assert value >= -1e-8, (role, "CPU counter regressed")
        result[role] = max(0.0, value)
    return result


def diagnostic_child():
    child = _monitored_child()
    original = "return await jobs.is_ready() and await jobs.execution_ready() and await jobs.follow_up_ready() and await probe_clock(pool,max_skew_seconds=30,timeout_seconds=2)"
    replacement = """async def checked(stage, operation):
                value=await operation
                if value is not True: print('K14_CLOCK_GUARD_FALSE='+stage,file=sys.stderr,flush=True)
                return value
            return await checked('jobs',jobs.is_ready()) and await checked('execution',jobs.execution_ready()) and await checked('follow_up',jobs.follow_up_ready()) and await checked('clock',probe_clock(pool,max_skew_seconds=30,timeout_seconds=2))"""
    assert child.count(original) == 1
    child = child.replace(original, replacement)
    needle = "membership_management=WorkspaceManagement(pool,cursors=cursors,audit=True),"
    child = "from aletheia_lab.hosted.workspace_management import WorkspaceManagement\n" + child
    marker = "catalog_access=access,project_access="
    assert child.count(marker) == 1
    child = child.replace(marker, needle + "resumable_uploads=True," + marker)
    child = "from aletheia_lab.hosted.preview_leases import PreviewLeases\n" + child
    marker = "CanonicalProjectOperations(sources,supervisor,jobs,private_root="
    assert child.count(marker) == 1
    return child.replace(
        marker,
        "CanonicalProjectOperations(sources,supervisor,jobs,leases=PreviewLeases(sources),private_root=",
    )


def test_missing_operation_demand_and_mixed_job_fairness(tmp_path, monkeypatch):
    monkeypatch.setenv("K17_TEST_DATABASE_URL", os.environ["K14_TEST_DATABASE_URL"])
    _run(exercise(tmp_path))


async def exercise(private):
    root = Path(__file__).resolve().parents[3]
    output = Path(os.environ["K14_EVIDENCE_DIR"])
    identity = _identity(root)
    report = {
        "scope": "Supplemental local K14 only; per-journey gross CPU and mixed-operation fairness. Not declared team deployment or primary dataset.",
        "runtime_identity": identity,
        "phases": [],
        "full_k14_deployment_pass": False,
        "primary50k_pass": False,
        "status": "running",
    }
    children, handles, roles, tasks = [], [], {}, []
    stop = asyncio.Event()
    database = os.environ["K14_TEST_DATABASE_URL"]
    password = secrets.token_urlsafe(32)
    secret = secrets.token_urlsafe(48)
    samples = []

    async def stop_runtime():
        stop.set()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        tasks.clear()
        for child in children:
            if child.poll() is None:
                child.terminate()
        for child in children:
            try:
                await asyncio.to_thread(child.wait, 15)
            except subprocess.TimeoutExpired:
                child.kill()
                await asyncio.to_thread(child.wait, 5)

    try:
        # An actual reaped compute child must remain in the cumulative CPU measurement.
        b = process_snapshot({"self": os.getpid()})
        subprocess.run(
            [
                sys.executable,
                "-c",
                "import time; s=time.process_time();\nwhile time.process_time()-s<.12: pass",
            ],
            check=True,
        )
        a = process_snapshot({"self": os.getpid()})
        assert cpu_delta(b, a)["self"] >= 0.1
        report["reaped_child_cpu_self_check"] = "pass"
        async with AsyncExitStack() as stack:
            pool, schema = await stack.enter_async_context(_target())
            stack.push_async_callback(stop_runtime)
            seed = private / "seed"
            plan = SeedPlan(
                namespace="k17_k14demand",
                generations=1,
                results_per_generation=1,
                source_rows={"S": 1000, "M": 10000, "L": 100000},
            )
            manifest = await seed_native(
                pool,
                schema=schema,
                output=seed,
                plan=plan,
                csrf_secret=SecretStr(secret),
                limits=SeedLimits(500 * 1024**2, 300000, 100, maximum_seconds=300),
            )
            owners = [
                Actor.model_validate_json(line)
                for line in (seed / "actors.jsonl").read_text().splitlines()
                if json.loads(line)["role"] == "owner"
            ]
            assert len(owners) == 6
            (seed / "worker-private").mkdir(mode=0o700)
            async with pool.connection() as conn:
                await migrate_workspace(conn, schema)
                await migrate_upload(conn, schema)
                await migrate_preview(conn, schema)
                await migrate_audit(conn, schema)
                from importlib.resources import files

                await conn.execute(
                    files("aletheia_lab.hosted_auth").joinpath("admission_schema.sql").read_text()
                )
                backend = (await (await conn.execute("SELECT pg_backend_pid()")).fetchone())[0]
                postgres = int(
                    Path(f"/proc/{backend}/stat").read_text().rsplit(") ", 1)[1].split()[1]
                )
                assert Path(f"/proc/{postgres}/comm").read_text().strip() == "postgres"
            async with await psycopg.AsyncConnection.connect(database, autocommit=True) as admin:
                for mode in ("api", "worker"):
                    role = "k14_demand_" + mode + "_" + uuid4().hex
                    await admin.execute(
                        sql.SQL(
                            "CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS PASSWORD {}"
                        ).format(sql.Identifier(role), sql.Literal(password))
                    )
                    roles[mode] = role
            async with pool.connection() as conn:
                for mode, role in roles.items():
                    await grant_preview_runtime(conn, schema, role, profile=mode, audit=True)
            cert, key = _certificate(private)
            tls = ssl.create_default_context(cafile=str(cert))
            roots = {"postgres": postgres, "generator": os.getpid()}
            api_ports = []
            for mode in ("api", "worker"):
                for ordinal in range(2):
                    label, port = f"{mode}{ordinal}", _port()
                    config = private / (label + ".json")
                    config.write_text(
                        json.dumps(
                            {
                                "environment": "local",
                                "public_origin": ORIGIN,
                                "identity": {
                                    "issuer": "https://issuer.example.invalid",
                                    "client_id": "synthetic-preauthenticated",
                                    "redirect_uri": ORIGIN + "/v1/auth/callback",
                                    "client_kind": "public",
                                },
                                "storage": {
                                    "database_url_env": "ALETHEIA_HOSTED_DATABASE_URL",
                                    "object_root": str(seed),
                                    "expected_schema": schema,
                                    "expected_database_role": roles[mode],
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
                        PYTHONPATH=str(root / "src"),
                        ALETHEIA_HOSTED_CSRF_SECRET=secret,
                        ALETHEIA_HOSTED_DATABASE_URL=_application_url(
                            _role_url(database, schema, roles[mode], password),
                            "k14-demand-" + label,
                        ),
                        ALETHEIA_RELEASE_ID=identity,
                        K14_RESOURCE_SAMPLE_FILE=str(private / (label + ".resources.jsonl")),
                        OMP_NUM_THREADS="1",
                        OPENBLAS_NUM_THREADS="1",
                        MKL_NUM_THREADS="1",
                    )
                    log = (private / (label + ".safe.log")).open("xb")
                    handles.append(log)
                    child = subprocess.Popen(
                        [
                            sys.executable,
                            "-B",
                            "-c",
                            diagnostic_child(),
                            mode,
                            str(config),
                            str(cert),
                            str(key),
                        ],
                        cwd=root,
                        env=env,
                        stdout=subprocess.DEVNULL,
                        stderr=log,
                    )
                    children.append(child)
                    roots[label] = child.pid
                    if mode == "api":
                        api_ports.append(port)
            port = _port()
            ingress_code = (
                "import asyncio,json,os,signal,sys\nfrom aletheia_lab.hosted.observability import process_telemetry\n"
                + _MONITOR
                + _INGRESS
            )
            ingress = subprocess.Popen(
                [sys.executable, "-B", "-c", ingress_code, str(port), json.dumps(api_ports)],
                env=dict(
                    os.environ, K14_RESOURCE_SAMPLE_FILE=str(private / "ingress.resources.jsonl")
                ),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            children.append(ingress)
            roots["ingress"] = ingress.pid
            profile = expanded_profile(
                calibration_profile(
                    target=f"https://127.0.0.1:{port}",
                    origin=ORIGIN,
                    host="app.example.invalid",
                    release_sha256=identity,
                    dataset_sha256=hashlib.sha256(
                        json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
                    ).hexdigest(),
                    run_label="k14demand",
                    tls_ca_file=str(cert),
                )
            )
            report["profile"] = profile.model_dump(mode="json")
            (output / "seed-manifest.json").write_text(
                json.dumps(manifest, sort_keys=True, separators=(",", ":"))
            )
            assert (
                hashlib.sha256((output / "seed-manifest.json").read_bytes()).hexdigest()
                == profile.dataset_sha256
            )
            harness = MeasuredHarness(profile, owners)
            phase_mode = os.environ.get("K14_MEASURE_PHASES", "resume-missing")
            assert phase_mode in ("resume-missing", "mixed-only")
            report["phase_mode"] = phase_mode
            async with httpx2.AsyncClient(
                base_url=profile.target,
                verify=tls,
                trust_env=False,
                timeout=15,
                limits=httpx2.Limits(max_connections=16, max_keepalive_connections=8),
            ) as client:
                async with asyncio.timeout(30):
                    while True:
                        assert all(c.poll() is None for c in children), "fixture child exited"
                        try:
                            if (
                                await client.get("/health/ready", headers={"host": profile.host})
                            ).status_code == 200:
                                break
                        except httpx2.HTTPError:
                            pass
                        await asyncio.sleep(0.1)
                for actor in owners:
                    r = await client.get(
                        "/v1/session",
                        headers={"host": profile.host, "cookie": actor.cookie.get_secret_value()},
                    )
                    assert r.status_code == 200
                    harness.sessions.observe_session(actor, Session.model_validate_json(r.content))
                positive = [j for j in profile.journeys if j.expected == "positive"]
                noisy = [a for a in owners if a.values["workspace_id"] == plan.workspace(0)]
                quiet = [a for a in owners if a.values["workspace_id"] == plan.workspace(1)]

                async def action(actor, journey):
                    before = dict(harness.counts)
                    await harness._execute(client, harness._offer(actor, journey, time.monotonic()))
                    return {
                        k: v - before.get(k, 0)
                        for k, v in harness.counts.items()
                        if v != before.get(k, 0)
                    }

                # No foreground workload overlaps a calibration phase. Background runtime/DB CPU remains included.
                b = process_snapshot(roots)
                await asyncio.sleep(2)
                a = process_snapshot(roots)
                report["idle_control"] = {
                    "before": b,
                    "after": a,
                    "gross_cpu_seconds": cpu_delta(b, a),
                }
                for journey in positive:
                    for actor in noisy:
                        # Resume only missing phases; prior phase evidence stays unchanged.
                        if phase_mode == "mixed-only":
                            continue
                        if journey.name not in ("preview_lease", "own_review_comment") and not (
                            journey.name == "analysis_terminal" and actor.size == "S"
                        ):
                            continue
                        count = (
                            2
                            if any(s.follow_job or s.download_descriptor for s in journey.steps)
                            else 20
                        )
                        before = process_snapshot(roots)
                        counters = dict(harness.counts)
                        http = dict(harness.http)
                        operation = dict(harness.actual_operations)
                        wall = []
                        started = time.monotonic()
                        for _ in range(count):
                            tick = time.monotonic()
                            await action(actor, journey)
                            wall.append(time.monotonic() - tick)
                        after = process_snapshot(roots)
                        elapsed = time.monotonic() - started
                        delta = {
                            k: v - counters.get(k, 0)
                            for k, v in harness.counts.items()
                            if v != counters.get(k, 0)
                        }
                        cpu = cpu_delta(before, after)
                        phase = {
                            "journey": journey.name,
                            "size": actor.size,
                            "offered": count,
                            "outcomes": delta,
                            "elapsed_seconds": elapsed,
                            "latency": _summary(wall),
                            "before": before,
                            "after": after,
                            "gross_cpu_seconds": cpu,
                            "mean_cpu_seconds_per_offered_journey": {
                                k: v / count for k, v in cpu.items()
                            },
                            "observed_arrival_rate": count / elapsed,
                            "observed_mean_cpu_demand_cores": {
                                k: v / elapsed for k, v in cpu.items()
                            },
                            "http": {
                                k: v - http.get(k, 0)
                                for k, v in harness.http.items()
                                if v != http.get(k, 0)
                            },
                            "actual_operations": {
                                k: v - operation.get(k, 0)
                                for k, v in harness.actual_operations.items()
                                if v != operation.get(k, 0)
                            },
                        }
                        report["phases"].append(phase)
                        (output / "phases.json").write_text(json.dumps(report["phases"], indent=2))
                        print(
                            "K14_PHASE="
                            + json.dumps(
                                {
                                    "journey": journey.name,
                                    "size": actor.size,
                                    "count": count,
                                    "outcomes": delta,
                                }
                            ),
                            flush=True,
                        )
                        phase["all_offered_completed"] = delta.get("completed", 0) == count
                        assert harness._fatal is None, harness._fatal

                latency = {k: [] for k in ("background", "health", "admin")}
                mixed_start = time.monotonic()
                noise_counts = dict(harness.counts)
                selected = {j.name: j for j in positive}
                gate = asyncio.Event()

                async def noise(index, name):
                    await gate.wait()
                    while time.monotonic() - mixed_start < 60:
                        await action(noisy[index], selected[name])
                        await asyncio.sleep(0.05)

                tasks = [
                    asyncio.create_task(noise(0, "analysis_terminal")),
                    asyncio.create_task(noise(1, "preview_lease")),
                    asyncio.create_task(noise(2, "export_whole_file")),
                ]
                gate.set()
                while time.monotonic() - mixed_start < 60:
                    actor = quiet[len(latency["background"]) % 3]
                    h = {
                        "host": profile.host,
                        "cookie": harness.sessions.current(actor).cookie.get_secret_value(),
                    }
                    t = time.monotonic()
                    r = await client.get(
                        f"/v1/workspaces/{actor.values['workspace_id']}/results/{actor.values['result_id']}/header",
                        headers=h,
                    )
                    latency["background"].append(time.monotonic() - t)
                    assert (
                        r.status_code == 200
                        and r.json()["workspace_id"] == actor.values["workspace_id"]
                    )
                    t = time.monotonic()
                    r = await client.get("/health/ready", headers={"host": profile.host})
                    latency["health"].append(time.monotonic() - t)
                    assert r.status_code == 200
                    t = time.monotonic()
                    async with pool.connection() as conn:
                        assert (await (await conn.execute("SELECT 1")).fetchone()) == (1,)
                    latency["admin"].append(time.monotonic() - t)
                    samples.append(process_snapshot(roots))
                    await asyncio.sleep(0.05)
                await asyncio.gather(*tasks)
                tasks.clear()
                report["mixed"] = {
                    "seconds": time.monotonic() - mixed_start,
                    "samples": samples,
                    "latency_samples_seconds": latency,
                    "latency": {k: _summary(v) for k, v in latency.items()},
                    "outcomes": {
                        k: v - noise_counts.get(k, 0)
                        for k, v in harness.counts.items()
                        if v != noise_counts.get(k, 0)
                    },
                    "noise": ["analysis_terminal", "preview_lease", "export_whole_file"],
                }
                for name, bound in (("background", 500), ("health", 250), ("admin", 250)):
                    assert (
                        len(latency[name]) >= 30
                        and _summary(latency[name])["p95_observed_ms"] <= bound
                    )
                report["harness_failure_code"] = harness._fatal
                report["unexpected_http"] = harness._unexpected_http
                drain_start = time.monotonic()
                async with asyncio.timeout(120):
                    while True:
                        async with pool.connection() as conn:
                            states = dict(
                                await (
                                    await conn.execute(
                                        "SELECT state,count(*) FROM j01_jobs GROUP BY state"
                                    )
                                ).fetchall()
                            )
                        if not any(
                            n
                            for state, n in states.items()
                            if state not in ("succeeded", "failed", "cancelled", "expired")
                        ):
                            break
                        await asyncio.sleep(0.2)
                report["post_load_drain"] = {
                    "seconds": time.monotonic() - drain_start,
                    "job_states": states,
                    "note": "Drain after action timeout does not turn the action into success.",
                }
                assert all(p["all_offered_completed"] for p in report["phases"])
                assert not any(
                    harness.counts.get(k, 0)
                    for k in (
                        "failed",
                        "timeout",
                        "rejected",
                        "conflict",
                        "outcome_unknown",
                        "transport_or_harness_failure",
                    )
                ), dict(harness.counts)
                async with pool.connection() as conn:
                    assert (
                        await (
                            await conn.execute(
                                "SELECT count(*) FROM j01_jobs WHERE state NOT IN ('succeeded','failed','cancelled','expired')"
                            )
                        ).fetchone()
                    ) == (0,)
            report.update(
                status="passed_local_supplement",
                physical_strata=manifest["strata"],
                physical=manifest["physical"],
                primary_dataset_qualified=manifest["primary_dataset_qualified"],
                executed_owners=6,
                http=dict(harness.http),
                outcomes=dict(harness.counts),
                operation_counts=dict(harness.actual_operations),
                cpu_tick_seconds=1 / os.sysconf("SC_CLK_TCK"),
                limitations=[
                    "Isolated per-journey gross CPU includes runtime heartbeat, DB maintenance, reaped children and request completion protocol; not exact per-SQL or concurrent per-request CPU.",
                    "RSS sums can double-count shared pages. PID/FD sampling can miss short children; FD may be unavailable by permission.",
                    "Shared 4CPU/3GiB application+generator cgroup; separate PG 2CPU/1GiB. This is not independent production component quotas or off-host NIC capacity.",
                    "Native result sizes are 2/16/64 claims, not primary100/1000/10000. No production/50k gate is closed.",
                ],
            )
    finally:
        if "harness" in locals():
            report["actions"] = harness.actions
            report["harness_failure_code"] = harness._fatal
            report["unexpected_http"] = harness._unexpected_http
            report["http"] = dict(harness.http)
            report["outcomes"] = dict(harness.counts)
            report["operation_counts"] = dict(harness.actual_operations)
        await stop_runtime()
        for h in handles:
            h.close()
        async with await psycopg.AsyncConnection.connect(database, autocommit=True) as admin:
            for role in roles.values():
                await admin.execute(
                    sql.SQL("DROP OWNED BY {}; DROP ROLE {}").format(
                        sql.Identifier(role), sql.Identifier(role)
                    )
                )
        sensitive = [
            secret,
            password,
            database,
            *[a.cookie.get_secret_value() for a in locals().get("owners", [])],
            *[a.csrf.get_secret_value() for a in locals().get("owners", [])],
        ]
        for p in [*private.glob("*.safe.log"), *private.glob("*.resources.jsonl")]:
            raw = p.read_text(errors="replace")
            assert not any(v in raw for v in sensitive if len(v) > 12), (
                "sensitive fixture value in log"
            )
            (output / p.name).write_text(raw)
        if report["status"] == "running":
            report["status"] = "failed"
        report["source_stable"] = _identity(root) == identity
        (output / "demand-result.json").write_text(json.dumps(report, indent=2))
        assert report["source_stable"]
