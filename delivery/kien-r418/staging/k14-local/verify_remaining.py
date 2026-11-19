"""Additional K14 measurements on the existing isolated staging deployment."""

import asyncio
import hashlib
import html
import json
import re
import ssl
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from uuid import uuid4

import httpx2
import psycopg
from stage_load import CONFIG, Measured, credentials, fleet, observe, stats
from stage_node import sample

from aletheia_lab.hosted.capacity_models import Actor, Journey
from aletheia_lab.hosted.capacity_profiles import calibration_profile, expanded_profile
from aletheia_lab.hosted_contract.session_capabilities import Session


def client(tls, **kw):
    return httpx2.AsyncClient(
        base_url=CONFIG["origin"], verify=tls, trust_env=False, timeout=10, **kw
    )


async def fresh_actor(tls, actor):
    async with client(tls) as c:
        start = datetime.now(UTC)
        origin, issuer = CONFIG["origin"], CONFIG["issuer"]
        r = await c.get("/v1/auth/start", headers={"sec-fetch-site": "same-origin"})
        assert r.status_code == 303
        location = r.headers["location"]
        assert location.startswith(issuer + "/protocol/openid-connect/auth?")
        r = await c.get(location)
        form = re.search(r'<form\b[^>]*\bid="kc-form-login"[^>]*>', r.text)
        assert r.status_code == 200 and form
        action = re.search(r'\baction="([^"]+)"', form[0])
        assert action
        target = urljoin(str(r.url), html.unescape(action[1]))
        assert target.startswith(issuer + "/login-actions/")
        user = "u" + str(int(actor.actor_id.rsplit("_", 1)[1]))
        r = await c.post(
            target, data={"username": user, "password": Path("/run/load/password").read_text()}
        )
        assert r.status_code in (302, 303), r.status_code
        callback = r.headers["location"]
        assert callback.startswith(origin + "/v1/auth/callback?")
        callback_response = await c.get(callback)
        response = await c.get("/v1/session")
        assert callback_response.status_code == 303 and response.status_code == 200
        auth = Session.model_validate_json(response.content)
        assert auth.actor.user_id == actor.actor_id
        cookie_names = ("__Host-aletheia_session", "__Host-aletheia_csrf")
        values = [c.cookies.get(name) for name in cookie_names]
        assert all(values)
        exported = actor.model_dump(mode="python")
        exported["cookie"] = "; ".join(
            f"{k}={v}" for k, v in zip(cookie_names, values, strict=True)
        )
        exported["csrf"] = values[1]
        exported["values"].update(
            context_id=auth.session.context_id,
            absolute_expires_at=(start + timedelta(hours=8)).isoformat(),
        )
        return Actor.model_validate(exported), auth


def snapshot(directory):
    result = fleet()
    result["generator"] = sample()
    for role in ("pg", "idp"):
        p = directory / (role + "-resource.jsonl")
        if p.exists():
            with p.open("rb") as stream:
                stream.seek(max(0, p.stat().st_size - 32768))
                for line in reversed(stream.read().splitlines()):
                    try:
                        result[role] = json.loads(line)
                        break
                    except ValueError:
                        continue
        assert role in result, f"missing resource sample for {role}"
    return result


def result_path(actor):
    return (
        f"/v1/workspaces/{actor.values['workspace_id']}/results/{actor.values['result_id']}/header"
    )


async def read_header(c, actor, path=None):
    t = time.monotonic()
    r = await c.get(path or result_path(actor), headers=credentials(actor))
    seconds = time.monotonic() - t
    assert r.status_code == 200
    value = r.json()
    assert value["workspace_id"] == actor.values["workspace_id"]
    assert value["result_id"] == actor.values["result_id"]
    return {"seconds": seconds, "bytes": len(r.content)}


async def cold_and_warm(directory, actors, tls):
    # The controller restarts only the recorded, owned API replica after logins.
    (directory / "sessions-ready").touch(exist_ok=False)
    async with asyncio.timeout(80):
        while not (directory / "api0-restarted.json").exists():
            await asyncio.sleep(0.05)
    host = urlsplit(CONFIG["origin"]).netloc
    async with client(tls) as c:
        async with asyncio.timeout(20):
            while True:
                try:
                    r = await c.get("https://api0:18766/health/ready", headers={"host": host})
                    if r.status_code == 200:
                        break
                except httpx2.TransportError:
                    pass
                await asyncio.sleep(0.1)
        result = []
        for actor in actors:
            path = "https://api0:18766" + result_path(actor)
            first = await read_header(c, actor, path)
            warm = [await read_header(c, actor, path) for _ in range(10)]
            result.append(
                {"size": actor.size, "after_restart_first_read_of_result": first, "repeat": warm}
            )
        return result


class Requests(Measured):
    def __init__(self, *args):
        super().__init__(*args)
        self.requests = []

    async def _request(self, c, item, **kw):
        t = time.monotonic()
        before = self.http["response_body_bytes"]
        result = None
        try:
            result = await super()._request(c, item, **kw)
            return result
        finally:
            self.requests.append(
                {
                    "code": kw["operation"],
                    "method": kw.get("method", "GET"),
                    "size": item.actor.size,
                    "status": result[0] if result else None,
                    "seconds": time.monotonic() - t,
                    "response_bytes": self.http["response_body_bytes"] - before,
                }
            )


async def measure(c, directory, harness, actor, recipe, operation, count, report):
    old = len(harness.actions)
    start = time.monotonic()
    pre = snapshot(directory)
    for _ in range(count):
        await harness._execute(c, harness._offer(actor, recipe, time.monotonic()))
    # Wait for the next 250 ms fleet sample to cover the last response.
    await asyncio.sleep(0.28)
    after = snapshot(directory)
    actions = harness.actions[old:]
    report["operation_batches"].append(
        {
            "operation": operation,
            "size": actor.size,
            "recipe": recipe.name,
            "offered": len(actions),
            "completed": sum(a["outcome"] == "completed" for a in actions),
            "window_seconds": time.monotonic() - start,
            "latency": stats([a["seconds"] for a in actions]),
            "before": pre,
            "after": after,
        }
    )
    assert harness._fatal is None, harness._fatal


async def operations(c, directory, harness, actors, profile, report):
    reads = {}
    for j in profile.journeys:
        if j.expected == "positive":
            for step in j.steps:
                if (
                    step.method == "GET"
                    and step.assertions
                    and not any(x in step.path for x in ("{preview_id}", "{action_id}"))
                ):
                    reads.setdefault((step.operation_id, step.path), (j, step))
    covered = {
        (p["operation"], p["size"], p["recipe"]) for p in report.get("preserved_batches", [])
    }
    for base, step in reads.values():
        # Keep the existing business oracle and per-size deadline for each isolated request.
        recipe = Journey.model_validate({**base.model_dump(), "steps": (step,)})
        for actor in actors:
            if (step.operation_id, actor.size, base.name) in covered:
                continue
            if step.operation_id == "P17":
                r = await c.get(
                    result_path(actor).removesuffix("/header") + "/claims?limit=50",
                    headers=credentials(actor),
                )
                assert (
                    r.status_code == 200
                    and r.json()["workspace_id"] == actor.values["workspace_id"]
                )
                item = r.json()["items"][0]
                actor = actor.model_copy(
                    update={
                        "values": {
                            **actor.values,
                            "selected_claim": item["id"],
                            "selected_evidence": item["citation_ids"][0],
                        }
                    }
                )
            await measure(c, directory, harness, actor, recipe, step.operation_id, 12, report)


async def transaction_groups(c, directory, harness, actors, profile, report):
    before = snapshot(directory)
    await asyncio.sleep(2)
    report["idle_control"] = {"before": before, "after": snapshot(directory)}
    names = {
        "analysis_terminal",
        "followup_terminal",
        "preview_lease",
        "export_whole_file",
        "own_review_comment",
    }
    for j in profile.journeys:
        if j.name in names:
            operation = next(step.operation_id for step in j.steps if step.method != "GET")
            for actor in actors:
                await measure(c, directory, harness, actor, j, operation, 2, report)


async def disconnect(tls, actor, path):
    host = urlsplit(CONFIG["origin"]).netloc
    _, writer = await asyncio.open_connection(
        "ingress", CONFIG["app_port"], ssl=tls, server_hostname="localhost"
    )
    try:
        lines = [
            f"GET {path} HTTP/1.1",
            f"Host: {host}",
            f"Cookie: {actor.cookie.get_secret_value()}",
            "Connection: keep-alive",
            "",
            "",
        ]
        writer.write("\r\n".join(lines).encode())
        await writer.drain()
    finally:
        writer.transport.abort()


async def fair_round(c, tls, db, noisy, quiet, wave):
    async def job(actor):
        ws = actor.values["workspace_id"]
        payload = {"operation": "preview", "source_id": actor.values["source_id"]}
        headers = {**credentials(actor), "idempotency-key": "k14-staging-closeout-" + uuid4().hex}
        acknowledgements = await asyncio.gather(
            *(c.post(f"/v1/workspaces/{ws}/jobs", headers=headers, json=payload) for _ in range(3))
        )
        assert all(r.status_code == 202 for r in acknowledgements), [
            r.status_code for r in acknowledgements
        ]
        ack = acknowledgements[0].json()
        assert all(r.json() == ack for r in acknowledgements)
        path = ack["status_url"]
        assert path == f"/v1/workspaces/{ws}/jobs/{ack['job_id']}"
        t = time.monotonic()
        first = await c.get(path + "/updates", headers=credentials(actor))
        assert first.status_code == 200
        state = first.json()
        assert (state["workspace_id"], state["job_id"]) == (ws, ack["job_id"])
        await disconnect(tls, actor, path + "/updates")
        polls = 0
        async with client(tls) as fresh, asyncio.timeout(80):
            while True:
                if state["poll_after_ms"] is not None:
                    await asyncio.sleep(state["poll_after_ms"] / 1000)
                r = await fresh.get(
                    path + "/updates",
                    headers=credentials(actor),
                    params={"cursor": state["cursor"]},
                )
                assert r.status_code == 200, r.status_code
                prior = state
                state = r.json()
                polls += 1
                assert state["revision"] >= prior["revision"] and (
                    state["workspace_id"],
                    state["job_id"],
                ) == (ws, ack["job_id"])
                job_status = state["job"] or prior["job"]
                if job_status and job_status["status"] in (
                    "succeeded",
                    "failed",
                    "cancelled",
                    "expired",
                ):
                    assert job_status["status"] == "succeeded"
                    break
            output = await fresh.get(path + "/output", headers=credentials(actor))
            assert (
                output.status_code == 200
                and output.json()["result"]["source_sha256"] == actor.values["source_sha256"]
            )
        return {
            "job_id": ack["job_id"],
            "size": actor.size,
            "workspace": ws,
            "acknowledgements": 3,
            "resume_polls": polls,
            "seconds": time.monotonic() - t,
            "source_sha_verified": True,
        }

    pending = asyncio.gather(*(job(a) for a in [*noisy, quiet[wave % len(quiet)]]))
    reads = {"background": [], "health": [], "admin": []}
    try:
        while not pending.done():
            reads["background"].append((await read_header(c, quiet[-1]))["seconds"])
            t = time.monotonic()
            assert (await c.get("/health/ready")).status_code == 200
            reads["health"].append(time.monotonic() - t)
            t = time.monotonic()
            assert (await (await db.execute("SELECT 1")).fetchone()) == (1,)
            reads["admin"].append(time.monotonic() - t)
            await asyncio.sleep(0.05)
        jobs = await pending
    finally:
        if not pending.done():
            pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
    ids = [j["job_id"] for j in jobs]
    rows = await (
        await db.execute(
            "SELECT job_id,state,attempt_count,terminal_at FROM j01_jobs WHERE job_id=ANY(%s)",
            (ids,),
        )
    ).fetchall()
    assert len(set(ids)) == len(rows) == 4 and all(r[1:3] == ("succeeded", 1) for r in rows)
    return {
        "wave": wave + 1,
        "jobs": jobs,
        "ledger": [
            {"job_id": j, "state": state, "attempts": n, "terminal": t.isoformat()}
            for j, state, n, t in rows
        ],
        "samples_seconds": reads,
    }


async def main(directory, previous=None, transactions=False):
    report = {
        "status": "failed",
        "operation_batches": [],
        "reconnect_waves": [],
        "source_identity": CONFIG["release_identity"],
        "mode": "transaction_cpu" if transactions else "isolated_reads_and_reconnect",
    }
    try:
        tls = ssl.create_default_context(cafile="/run/config/ca.pem")
        owners = [
            a
            for a in (
                Actor.model_validate_json(line)
                for line in Path("/run/load/actors.jsonl").read_text().splitlines()
            )
            if a.role == "owner"
        ]
        sessions = [await fresh_actor(tls, a) for a in owners]
        actors = [a for a, _ in sessions]
        noisy = [a for a in actors if a.values["workspace_id"].endswith("0000")]
        quiet = [a for a in actors if a not in noisy]
        assert len(noisy) == len(quiet) == 3
        report["real_oidc_sessions"] = len(sessions)
        if transactions:
            pass
        elif previous:
            prior = json.loads(previous.read_text())
            assert prior["source_identity"] == CONFIG["release_identity"]
            report["preserved_batches"] = [
                p for p in prior["operation_batches"] if p["offered"] == p["completed"]
            ]
            report["cold_warm"] = prior["cold_warm"]
            report["cold_warm_evidence"] = previous.parent.name
        else:
            report["cold_warm"] = await cold_and_warm(directory, noisy, tls)
        dataset = hashlib.sha256(
            Path("/evidence/k14-staging/deployment-dataset.json").read_bytes()
        ).hexdigest()
        profile = expanded_profile(
            calibration_profile(
                target=CONFIG["origin"],
                origin=CONFIG["origin"],
                host=urlsplit(CONFIG["origin"]).netloc,
                release_sha256=CONFIG["release_identity"],
                dataset_sha256=dataset,
                run_label="close" + str(int(time.time())),
                tls_ca_file="/run/config/ca.pem",
            )
        )
        harness = Requests(profile, actors)
        for a, s in sessions:
            harness.sessions.observe_session(a, s)
        async with (
            client(tls) as c,
            await psycopg.AsyncConnection.connect(
                Path("/run/load/observer_dsn").read_text(), autocommit=True
            ) as db,
        ):
            try:
                if transactions:
                    await transaction_groups(c, directory, harness, noisy, profile, report)
                else:
                    await operations(c, directory, harness, noisy, profile, report)
            finally:
                report["operation_requests"] = harness.requests
                report["operation_actions"] = harness.actions
            if not transactions:
                for wave in range(3):
                    report["reconnect_waves"].append(
                        await fair_round(c, tls, db, noisy, quiet, wave)
                    )
            report["db_after"] = await observe(db)
        if not transactions:
            for name, limit in (("background", 500), ("health", 250), ("admin", 250)):
                report.setdefault("reconnect_latency", {})[name] = stats(
                    [s for w in report["reconnect_waves"] for s in w["samples_seconds"][name]]
                )
                assert report["reconnect_latency"][name]["p95_observed_ms"] <= limit
        assert all(p["offered"] == p["completed"] for p in report["operation_batches"])
        report["status"] = "passed_local_targeted"
    finally:
        (directory / "result.json").write_text(json.dumps(report, indent=2))


if __name__ == "__main__":
    option = sys.argv[2] if len(sys.argv) == 3 else None
    asyncio.run(
        main(
            Path(sys.argv[1]),
            Path(option) if option and option != "transactions" else None,
            option == "transactions",
        )
    )
