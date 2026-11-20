"""Measure previously grouped HTTP reads on the isolated K14 Docker fixture."""

import asyncio
import hashlib
import json
import ssl
import time
from pathlib import Path
from urllib.parse import urlsplit

import psycopg
from aletheia_lab.hosted.capacity_models import Actor
from aletheia_lab.hosted.capacity_profiles import calibration_profile, expanded_profile
from aletheia_lab.hosted.resource_budget import resource_budget
from aletheia_lab.hosted.settings import load_runtime_settings
from stage_load import CONFIG, fleet, observe, stats
from stage_node import sample
from verify_remaining import Requests, client, fresh_actor


class CapturedReads(Requests):
    def __init__(self, profile, actors):
        super().__init__(profile, actors)
        self.resources = {}

    async def _request(self, connection, item, **args):
        status, data = await super()._request(connection, item, **args)
        if args.get("method", "GET") == "GET" and status == 200:
            code = args["operation"]
            path = args["path"]
            if code == "P20" and path.endswith("/output"):
                self.resources[item.journey.name, "P20.output"] = item, args, data
                status_args = {**args, "path": path.removesuffix("/output")}
                self.resources[item.journey.name, "P20.status"] = (
                    item,
                    status_args,
                    data,
                )
            elif code in {"P08", "P24", "P26"}:
                self.resources[item.journey.name, code] = item, args, data
        return status, data

    async def probe(self, connection, key, target, count):
        item, args, expected = target
        timings = []
        before = snapshot()
        started = time.monotonic()
        for _ in range(count):
            begin = time.monotonic()
            status, data = await super()._request(connection, item, **args)
            timings.append(time.monotonic() - begin)
            assert status == 200, (key, status)
            if key[1] == "P20.status":
                assert data["job_id"] == expected["job_id"]
                assert data["workspace_id"] == expected["workspace_id"]
                assert data["status"] == "succeeded"
            else:
                assert data == expected, key
        seconds = time.monotonic() - started
        await asyncio.sleep(0.30)
        return {
            "journey": key[0],
            "operation": key[1],
            "size": item.actor.size,
            "count": count,
            "window_seconds": seconds,
            "latency": stats(timings),
            "before": before,
            "after": snapshot(),
        }


def snapshot():
    readings = {**fleet(), "generator": sample()}
    assert all(abs(time.time() - item["epoch"]) < 3 for item in readings.values())
    return readings


async def run(result, dataset_hash):
    assert CONFIG["run_id"] != "da2093353a73", "Use the disposable fleet"
    result["plan"] = resource_budget(
        load_runtime_settings(Path("/run/config/api.toml")),
        api_replicas=2,
        worker_replicas=2,
    )
    assert result["plan"]["within_database_budget"]
    tls = ssl.create_default_context(cafile="/run/config/ca.pem")
    actors = [
        Actor.model_validate_json(row)
        for row in Path("/run/load/actors.jsonl").read_text().splitlines()
    ]
    owners = [
        actor
        for actor in actors
        if actor.role == "owner" and actor.values["workspace_id"].endswith("0000")
    ]
    assert {actor.size for actor in owners} == {"S", "M", "L"} and len(owners) == 3
    profile = expanded_profile(
        calibration_profile(
            target=CONFIG["origin"],
            origin=CONFIG["origin"],
            host=urlsplit(CONFIG["origin"]).netloc,
            release_sha256=CONFIG["release_identity"],
            dataset_sha256=dataset_hash,
            run_label="k1401" + CONFIG["run_id"],
            tls_ca_file="/run/config/ca.pem",
        )
    )
    sessions = [await fresh_actor(tls, actor) for actor in owners]
    harness = CapturedReads(profile, [actor for actor, _ in sessions])
    for actor, session in sessions:
        harness.sessions.observe_session(actor, session)

    wanted = {
        "analysis_terminal",
        "followup_terminal",
        "preview_lease",
        "export_whole_file",
        "own_review_comment",
    }
    async with (
        client(tls) as connection,
        await psycopg.AsyncConnection.connect(
            Path("/run/load/observer_dsn").read_text(), autocommit=True
        ) as db,
    ):
        for actor, _ in sessions:
            harness.resources.clear()
            for journey in profile.journeys:
                if journey.name in wanted:
                    await harness._execute(
                        connection, harness._offer(actor, journey, time.monotonic())
                    )
                    action = harness.actions[-1]
                    assert action["outcome"] == "completed", (
                        action,
                        harness._unexpected_http,
                    )
            assert len(harness.resources) == 9, sorted(harness.resources)
            result["idle_before"] = snapshot()
            await asyncio.sleep(2)
            result["idle_after"] = snapshot()
            for key, target in sorted(harness.resources.items()):
                result["batches"].append(
                    await harness.probe(connection, key, target, 12)
                )

        result["fixture_actions"] = harness.actions
        await verify_reserve(result, connection, db)
        assert len(result["batches"]) == 27


async def verify_reserve(result, connection, db):
    paths = {
        "ingress": "/health/ready",
        "api0": "https://api0:18766/health/ready",
        "api1": "https://api1:18766/health/ready",
    }
    times = {**{role: [] for role in paths}, "admin": []}
    observations = []
    addresses = {role: {"host": urlsplit(CONFIG["origin"]).netloc} for role in paths}
    result["pg_limits"] = dict(
        await (
            await db.execute(
                "SELECT name,setting::int FROM pg_settings WHERE name IN "
                "('max_connections','superuser_reserved_connections','reserved_connections')"
            )
        ).fetchall()
    )
    for _ in range(16):
        for role, path in paths.items():
            started = time.monotonic()
            response = await connection.get(path, headers=addresses[role])
            times[role].append(time.monotonic() - started)
            assert response.status_code == 200 and response.json()["status"] == "ready"
        started = time.monotonic()
        assert await (await db.execute("SELECT 1")).fetchone() == (1,)
        times["admin"].append(time.monotonic() - started)
        observations.append(await observe(db))
    result["reserve_probes"] = {role: stats(values) for role, values in times.items()}
    result["pg_observations"] = observations
    assert all(
        item["p95_observed_ms"] <= 250 for item in result["reserve_probes"].values()
    )
    assert (
        max(sum(row["count"] for row in item["connections"]) for item in observations)
        <= result["plan"]["total_database_connections"]
    )


if __name__ == "__main__":
    import sys

    assert len(sys.argv) == 2 and len(sys.argv[1]) == hashlib.sha256().digest_size * 2
    report = {"status": "failed", "runtime": CONFIG["release_identity"], "batches": []}
    try:
        asyncio.run(run(report, sys.argv[1]))
        report["status"] = "passed_isolated_steps"
    finally:
        print(json.dumps(report, indent=2))
