"""Real TLS workload against the separately installed staging API/worker fleet."""

from __future__ import annotations

import asyncio
import hashlib
import json
import socket
import ssl
import sys
import time
from pathlib import Path

import httpx2
import psycopg

from aletheia_lab.hosted.capacity import CapacityHarness
from aletheia_lab.hosted.capacity_models import Actor
from aletheia_lab.hosted.capacity_profiles import calibration_profile, expanded_profile
from aletheia_lab.hosted_contract.session_capabilities import Session

CONFIG = json.loads(Path("/run/config/stage.json").read_text())
DNS = socket.getaddrinfo


def private_dns(host, *args, **kwargs):
    # Preserve the public HTTPS origin/cookie scope while routing inside the isolated bridge.
    return DNS("ingress" if host in ("localhost", b"localhost") else host, *args, **kwargs)


socket.getaddrinfo = private_dns


def stats(values):
    ordered = sorted(values)
    return {
        "count": len(values),
        "mean_ms": sum(values) * 1000 / len(values),
        "p95_observed_ms": ordered[min(len(values) - 1, int(0.95 * len(values)))] * 1000,
        "max_ms": max(values) * 1000,
    }


class Measured(CapacityHarness):
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
                "seconds": elapsed,
                "deadline_seconds": item.journey.deadline(item.actor.size),
            }
        )


def fleet():
    result = {}
    for name in ("api0", "api1", "worker0", "worker1", "ingress"):
        p = Path("/run/metrics", name + ".jsonl")
        with p.open("rb") as f:
            f.seek(max(0, p.stat().st_size - 65536))
            lines = f.read().splitlines()
        records = []
        for line in lines:
            try:
                records.append(json.loads(line))
            except ValueError:
                continue
        assert records, (name, "no measured process samples")
        result[name] = records[-1]
    return result


def credentials(actor):
    return {
        "host": "localhost:" + str(CONFIG["app_port"]),
        "origin": CONFIG["origin"],
        "cookie": actor.cookie.get_secret_value(),
        "x-csrf-token": actor.csrf.get_secret_value(),
    }


async def observe(conn):
    rows = await (
        await conn.execute(
            "SELECT usename,application_name,state,count(*) FROM pg_stat_activity WHERE datname=current_database() GROUP BY 1,2,3"
        )
    ).fetchall()
    return {
        "epoch": time.time(),
        "connections": [dict(role=r[0], application=r[1], state=r[2], count=r[3]) for r in rows],
    }


async def exercise(output, prior=None):
    actors = [
        Actor.model_validate_json(line)
        for line in Path("/run/load/actors.jsonl").read_text().splitlines()
    ]
    owners = [a for a in actors if a.role == "owner"]
    assert len(owners) == 6
    profile = expanded_profile(
        calibration_profile(
            target=CONFIG["origin"],
            origin=CONFIG["origin"],
            host="localhost:" + str(CONFIG["app_port"]),
            release_sha256=CONFIG["release_identity"],
            dataset_sha256=hashlib.sha256(
                Path("/evidence/deployment-dataset.json").read_bytes()
            ).hexdigest(),
            run_label="stage" + str(int(time.time())),
            tls_ca_file="/run/config/ca.pem",
        )
    )
    harness = Measured(profile, owners)
    noisy = [a for a in owners if a.values["workspace_id"].endswith("0000")]
    quiet = [a for a in owners if a not in noisy]
    positive = {j.name: j for j in profile.journeys if j.expected == "positive"}
    report = {
        "status": "running",
        "source_identity": CONFIG["release_identity"],
        "profile": profile.model_dump(mode="json"),
        "phases": [],
        "all_ops_primary_qualified": False,
        "full_k14_deployment_pass": False,
        "scope": "Separately deployed local staging, native S/M/L pilot; not primary or 50k",
    }
    tasks = []
    passed = set()
    if prior:
        previous = json.loads(prior.read_text())
        assert previous["profile"]["dataset_sha256"] == profile.dataset_sha256
        assert previous["source_identity"] == CONFIG["release_identity"]
        passed = {
            (p["journey"], p["size"])
            for p in previous["phases"]
            if p["outcomes"].get("completed") == p["count"]
        }
        report["preserved_phase_count"] = len(passed)
    try:
        async with (
            await psycopg.AsyncConnection.connect(
                Path("/run/load/observer_dsn").read_text(), autocommit=True
            ) as conn,
            httpx2.AsyncClient(
                base_url=CONFIG["origin"],
                verify=ssl.create_default_context(cafile="/run/config/ca.pem"),
                trust_env=False,
                timeout=15,
                limits=httpx2.Limits(max_connections=24, max_keepalive_connections=8),
            ) as client,
        ):
            for actor in owners:
                response = await client.get("/v1/session", headers=credentials(actor))
                assert response.status_code == 200
                harness.sessions.observe_session(
                    actor, Session.model_validate_json(response.content)
                )
            for journey in positive.values():
                for actor in noisy:
                    if (journey.name, actor.size) in passed:
                        continue
                    count = (
                        2
                        if any(s.follow_job or s.download_descriptor for s in journey.steps)
                        else 20
                    )
                    start = time.monotonic()
                    before = fleet()
                    counts = dict(harness.counts)
                    for _ in range(count):
                        await harness._execute(
                            client, harness._offer(actor, journey, time.monotonic())
                        )
                    phase = {
                        "journey": journey.name,
                        "size": actor.size,
                        "count": count,
                        "seconds": time.monotonic() - start,
                        "before": before,
                        "after": fleet(),
                        "outcomes": {
                            k: v - counts.get(k, 0)
                            for k, v in harness.counts.items()
                            if v != counts.get(k, 0)
                        },
                    }
                    report["phases"].append(phase)
                    output.write_text(json.dumps(report, indent=2))
                    assert harness._fatal is None, harness._fatal
            latency = {name: [] for name in ("background", "health", "admin")}
            db_samples = []
            before_counts = dict(harness.counts)
            start = time.monotonic()
            start_epoch = time.time()

            async def noise(actor, name):
                while time.monotonic() - start < 90:
                    await harness._execute(
                        client, harness._offer(actor, positive[name], time.monotonic())
                    )
                    await asyncio.sleep(0.05)

            tasks = [
                asyncio.create_task(noise(noisy[0], "analysis_terminal")),
                asyncio.create_task(noise(noisy[1], "preview_lease")),
                asyncio.create_task(noise(noisy[2], "export_whole_file")),
            ]
            while time.monotonic() - start < 90:
                actor = quiet[len(latency["background"]) % 3]
                for name, path in (
                    (
                        "background",
                        f"/v1/workspaces/{actor.values['workspace_id']}/results/{actor.values['result_id']}/header",
                    ),
                    ("health", "/health/ready"),
                ):
                    t = time.monotonic()
                    response = await client.get(path, headers=credentials(actor))
                    latency[name].append(time.monotonic() - t)
                    assert response.status_code == 200
                    if name == "background":
                        assert response.json()["workspace_id"] == actor.values["workspace_id"]
                t = time.monotonic()
                assert (await (await conn.execute("SELECT 1")).fetchone()) == (1,)
                latency["admin"].append(time.monotonic() - t)
                db_samples.append(await observe(conn))
                await asyncio.sleep(0.05)
            await asyncio.gather(*tasks)
            tasks.clear()
            report["mixed"] = {
                "start_epoch": start_epoch,
                "end_epoch": time.time(),
                "seconds": time.monotonic() - start,
                "latency": {k: stats(v) for k, v in latency.items()},
                "latency_samples": latency,
                "database_samples": db_samples,
                "outcomes": {
                    k: v - before_counts.get(k, 0)
                    for k, v in harness.counts.items()
                    if v != before_counts.get(k, 0)
                },
            }
            for name, limit in (("background", 500), ("health", 250), ("admin", 250)):
                assert report["mixed"]["latency"][name]["p95_observed_ms"] <= limit
            assert not any(
                harness.counts.get(k, 0)
                for k in (
                    "timeout",
                    "failed",
                    "rejected",
                    "conflict",
                    "outcome_unknown",
                    "transport_or_harness_failure",
                )
            ), dict(harness.counts)
            rows = await (
                await conn.execute("SELECT state,count(*) FROM j01_jobs GROUP BY state")
            ).fetchall()
            report["job_states"] = dict(rows)
            assert all(state == "succeeded" for state, _ in rows), rows
            report["status"] = "passed_staging_native_pilot"
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        report.update(
            actions=harness.actions,
            outcomes=dict(harness.counts),
            http=dict(harness.http),
            operation_counts=dict(harness.actual_operations),
            harness_failure=harness._fatal,
        )
        if report["status"] == "running":
            report["status"] = "failed"
        output.write_text(json.dumps(report, indent=2))


if __name__ == "__main__":
    asyncio.run(exercise(Path(sys.argv[1]), Path(sys.argv[2]) if len(sys.argv) == 3 else None))
