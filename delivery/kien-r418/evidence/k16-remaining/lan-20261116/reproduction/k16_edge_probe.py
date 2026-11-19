"""Check only deployment-edge and bounded abuse controls on the private fleet."""

import asyncio
import hashlib
import json
import socket
import ssl
import sys
import time
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import httpx2
from stage_burst import login_smoke
from verify_remaining import fresh_actor
from stage_load import CONFIG, DNS, credentials, stats

from aletheia_lab.hosted.capacity_models import Actor
from aletheia_lab.hosted_contract.job_page import Job


async def probe(output):
    result = {"status": "failed", "checks": {}, "auth_burst": {}}
    checks = result["checks"]
    tls = ssl.create_default_context(cafile="/run/config/ca.pem")
    origin = CONFIG["origin"]
    actors = [
        Actor.model_validate_json(line)
        for line in Path("/run/load/actors.jsonl").read_text().splitlines()
    ]
    hot, quiet = next(
        (a, b)
        for a in actors
        for b in actors
        if a.role == b.role == "owner" and a.values["workspace_id"] != b.values["workspace_id"]
    )
    hot, quiet = [(await fresh_actor(tls, actor))[0] for actor in (hot, quiet)]
    quiet_path = (
        f"/v1/workspaces/{quiet.values['workspace_id']}/results/{quiet.values['result_id']}/header"
    )
    try:
        async with httpx2.AsyncClient(
            base_url=origin, verify=tls, trust_env=False, timeout=10
        ) as client:
            for _ in range(25):
                try:
                    if (await client.get("/health/ready")).status_code == 200:
                        break
                except httpx2.TransportError:
                    pass
                await asyncio.sleep(1)
            else:
                raise AssertionError("staging API unavailable")
            result["oidc"] = await login_smoke(tls)
            port = urlsplit(origin).port
            for name, context, sni in (
                ("trusted_ca_and_hostname", tls, "localhost"),
                ("untrusted_ca", ssl.create_default_context(), "localhost"),
                ("hostname_mismatch", tls, "wrong.invalid"),
            ):
                try:
                    _, writer = await asyncio.open_connection(
                        "ingress", port, ssl=context, server_hostname=sni
                    )
                except ssl.SSLCertVerificationError:
                    assert name != "trusted_ca_and_hostname"
                    checks[name] = "rejected"
                else:
                    assert name == "trusted_ca_and_hostname"
                    checks[name] = writer.get_extra_info("ssl_object").version()
                    assert checks[name] in ("TLSv1.2", "TLSv1.3")
                    writer.close()
                    await writer.wait_closed()

            requests = (
                ("wrong_host", "GET", "/health/ready", {"host": "wrong.invalid"}, 400),
                ("anonymous_session", "GET", "/v1/session", {}, 401),
                ("foreign_result", "GET", quiet_path, credentials(hot), 404),
                (
                    "csrf_cross_site",
                    "POST",
                    f"/v1/workspaces/{hot.values['workspace_id']}/jobs",
                    {**credentials(hot), "origin": "https://wrong.invalid"},
                    403,
                ),
            )
            for name, method, path, headers, expected in requests:
                response = await client.request(method, path, headers=headers)
                checks[name] = {
                    "status": response.status_code,
                    "cache_control": response.headers.get("cache-control"),
                    "nosniff": response.headers.get("x-content-type-options"),
                }
                assert checks[name] == {
                    "status": expected,
                    "cache_control": "no-store",
                    "nosniff": "nosniff",
                }, name

            response = await client.get("/v1/auth/start", headers={"sec-fetch-site": "same-origin"})
            assert response.status_code == 303
            fields = response.headers.get_list("set-cookie")
            assert fields and all(
                all(flag in field.lower() for flag in ("secure", "httponly", "samesite=lax"))
                and "domain=" not in field.lower()
                for field in fields
            )
            checks["oidc_start_cookie"] = "Secure, HttpOnly, SameSite=Lax, host-only"

            asset = await client.get("/")
            checks["static_asset"] = {"status": asset.status_code}
            if asset.status_code == 200:
                csp = asset.headers["content-security-policy"]
                assert all(v in csp for v in ("default-src 'self'", "frame-ancestors 'none'"))
                assert asset.headers["referrer-policy"] == "no-referrer"
                assert asset.headers["x-content-type-options"] == "nosniff"
                checks["static_asset"].update(csp=csp, referrer_policy="no-referrer")
            checks["hsts_seen"] = "strict-transport-security" in asset.headers

            async def auth_abuse(index):
                start = time.monotonic()
                r = await client.get(
                    "/v1/auth/start",
                    headers={
                        "sec-fetch-site": "same-origin",
                        "x-forwarded-for": f"198.51.100.{index + 1}",
                    },
                )
                assert r.status_code in (303, 429)
                if r.status_code == 429:
                    assert r.json()["code"] == "rate_limited"
                    assert r.headers["cache-control"] == "no-store"
                    assert int(r.headers["retry-after"]) >= 1
                return (r.status_code, r.headers.get("retry-after"), time.monotonic() - start)

            async def quiet_read():
                start = time.monotonic()
                r = await client.get(quiet_path, headers=credentials(quiet))
                assert r.status_code == 200
                assert r.json()["workspace_id"] == quiet.values["workspace_id"]
                return time.monotonic() - start

            async def burst_lane(first):
                return [await auth_abuse(i) for i in range(first, 60, 6)]

            async def background_lane():
                values = []
                for _ in range(12):
                    values.append(await quiet_read())
                    await asyncio.sleep(0.05)
                return values

            started = time.monotonic()
            *lanes, controls = await asyncio.gather(
                *(burst_lane(i) for i in range(6)), background_lane()
            )
            attempts = [sample for lane in lanes for sample in lane]
            counts = Counter(item[0] for item in attempts)
            assert counts[429] > 0
            result["auth_burst"] = {
                "offered": 60,
                "lanes": 6,
                "elapsed_seconds": time.monotonic() - started,
                "statuses": counts,
                "latency": stats([item[2] for item in attempts]),
                "quiet_workspace_reads": stats(controls),
                "retry_after_seconds": sorted({int(item[1]) for item in attempts if item[1]}),
                "distinct_spoofed_forwarded_ips": 60,
            }
            assert (await client.get("/health/ready")).status_code == 200
            checks["final_readiness"] = 200
            result["status"] = "passed"
    finally:
        output.write_text(json.dumps(result, indent=2), encoding="utf-8")


async def exercise_worker_outage(output, mode):
    tls = ssl.create_default_context(cafile="/run/config/ca.pem")
    actors = [
        Actor.model_validate_json(line)
        for line in Path("/run/load/actors.jsonl").read_text().splitlines()
    ]
    hot, quiet = next(
        (a, b)
        for a in actors
        for b in actors
        if a.role == b.role == "owner" and a.values["workspace_id"] != b.values["workspace_id"]
    )
    hot, quiet = [(await fresh_actor(tls, actor))[0] for actor in (hot, quiet)]
    async with httpx2.AsyncClient(
        base_url=CONFIG["origin"],
        verify=tls,
        trust_env=False,
        timeout=10,
        limits=httpx2.Limits(max_connections=12, max_keepalive_connections=0),
    ) as client:

        async def ready():
            response = await client.get("/health/ready")
            assert response.status_code == 200, response.status_code

        async def quiet_read():
            start = time.monotonic()
            path = f"/v1/workspaces/{quiet.values['workspace_id']}/results/{quiet.values['result_id']}/header"
            response = await client.get(path, headers=credentials(quiet))
            assert response.status_code == 200
            assert response.json()["workspace_id"] == quiet.values["workspace_id"]
            return time.monotonic() - start

        async def submit():
            start = time.monotonic()
            path = f"/v1/workspaces/{hot.values['workspace_id']}/jobs"
            response = await client.post(
                path,
                headers={**credentials(hot), "idempotency-key": "k16-" + uuid4().hex},
                json={"operation": "preview", "source_id": hot.values["source_id"]},
            )
            status = response.status_code
            assert status in (202, 503), status
            item = {"status": status, "seconds": time.monotonic() - start}
            if status == 202:
                item["status_url"] = response.json()["status_url"]
            else:
                assert response.json()["code"] == "service_unavailable"
                assert int(response.headers["retry-after"]) >= 1
                assert response.headers["cache-control"] == "no-store"
                item["retry_after"] = int(response.headers["retry-after"])
            return item

        if mode == "preflight":
            await ready()
            quiet_ms = await quiet_read() * 1000
            action = await submit()
            assert action["status"] == 202
            url = action["status_url"]
            async with asyncio.timeout(30):
                while True:
                    r = await client.get(url, headers=credentials(hot))
                    assert r.status_code == 200
                    job = Job.model_validate_json(r.content)
                    assert job.workspace_id == hot.values["workspace_id"] and job.job_id == url.rsplit("/", 1)[-1]
                    assert job.status not in {"failed", "expired", "cancelled"}
                    if job.status == "succeeded":
                        break
                    await asyncio.sleep(1)
            result = await client.get(url + "/output", headers=credentials(hot))
            assert result.status_code == 200 and job.attempt == 1
            assert result.json()["result"]["source_sha256"] == hot.values["source_sha256"]
            payload = {"verified_job": 1, "source_hash_verified": True, "submissions": 1,
                       "api_ready": 200, "tenant_background_ms": quiet_ms}
        elif mode == "cardinality":
            count = 120
            durations = []
            for _ in range(count):
                missing = "k16-miss-" + uuid4().hex
                path = f"/v1/workspaces/{quiet.values['workspace_id']}/results/{missing}/header"
                start = time.monotonic()
                response = await client.get(path, headers=credentials(quiet))
                assert response.status_code == 404
                durations.append(time.monotonic() - start)
            payload = {
                "unique_missing_result_ids": count,
                "http_404": count,
                "latency": stats(durations),
            }
        elif mode == "overload":

            async def lane(count, action):
                return [await action() for _ in range(count)]

            *traffic, quiet_times, health = await asyncio.gather(
                *(lane(12, submit) for _ in range(6)), lane(12, quiet_read), lane(6, ready)
            )
            attempts = [sample for lane_items in traffic for sample in lane_items]
            statuses = Counter(item["status"] for item in attempts)
            assert statuses[202] > 0 and statuses[503] > 0 and len(health) == 6
            payload = {
                "attempts": attempts,
                "statuses": statuses,
                "quiet_reads": stats(quiet_times),
                "health_ready_http_200": len(health),
                "generator_lanes": 6,
            }
        elif mode == "recovery":
            attempts = json.loads(output.with_name("overload.json").read_text())["attempts"]
            urls = [item["status_url"] for item in attempts if item["status"] == 202]
            assert len(urls) == len(set(urls)) > 0
            resource_headers = credentials(hot)
            ok = set()
            start = time.monotonic()
            async with asyncio.timeout(75):
                while len(ok) != len(urls):
                    for url in set(urls) - ok:
                        response = await client.get(url, headers=resource_headers)
                        assert response.status_code == 200
                        job = Job.model_validate_json(response.content)
                        assert job.workspace_id == hot.values["workspace_id"]
                        assert job.job_id == url.rsplit("/", 1)[-1]
                        assert job.status not in ("failed", "expired", "cancelled"), job.status
                        if job.status == "succeeded":
                            assert job.attempt == 1
                            result = await client.get(url + "/output", headers=resource_headers)
                            assert result.status_code == 200
                            assert (
                                result.json()["result"]["source_sha256"]
                                == hot.values["source_sha256"]
                            )
                            ok.add(url)
                    if len(ok) != len(urls):
                        await asyncio.sleep(2)
            # The deployment reserves one concurrent health slot per API.
            # Probe in sequence to observe recovery without overloading that slot.
            for _ in range(120):
                await ready()
            payload = {
                "accepted": len(urls),
                "unique_jobs_with_verified_hash": len(ok),
                "seconds_to_drained": time.monotonic() - start,
                "health_ready_http_200": 120,
                "quiet_read_ms": await quiet_read() * 1000,
            }
        else:
            raise AssertionError("explicit worker outage phase required")
    output.write_text(json.dumps(payload, indent=2), encoding="utf-8")


async def static_policy(output):
    def static_dns(host, *args, **kwargs):
        return DNS("k16-static" if host in ("localhost", b"localhost") else host, *args, **kwargs)

    socket.getaddrinfo = static_dns
    tls = ssl.create_default_context(cafile="/run/config/ca.pem")
    headers = {"host": urlsplit(CONFIG["origin"]).netloc}
    async with httpx2.AsyncClient(
        base_url="https://localhost:18766", headers=headers, verify=tls, trust_env=False, timeout=5
    ) as client:
        response = None
        for _ in range(20):
            try:
                response = await client.get("/")
                break
            except httpx2.TransportError:
                await asyncio.sleep(1)
        assert response is not None and response.status_code == 200
        csp = response.headers["content-security-policy"]
        assert all(
            v in csp for v in ("default-src 'self'", "frame-ancestors 'none'", "base-uri 'none'")
        )
        assert response.headers["referrer-policy"] == "no-referrer"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["cache-control"] == "no-store"
        statuses = {}
        for path in ("/.env", "/assets/../../api.toml", "/%2e%2e/run/secrets/database_api"):
            denied = await client.get(path)
            assert denied.status_code == 404 and denied.text == "Not Found"
            statuses[path] = denied.status_code
        output.write_text(
            json.dumps(
                {
                    "status": response.status_code,
                    "index_sha256": hashlib.sha256(response.content).hexdigest(),
                    "csp": csp,
                    "referrer_policy": response.headers["referrer-policy"],
                    "nosniff": response.headers["x-content-type-options"],
                    "cache_control": response.headers["cache-control"],
                    "hsts_seen": "strict-transport-security" in response.headers,
                    "path_denials": statuses,
                },
                indent=2,
            ),
            encoding="utf-8",
        )


if __name__ == "__main__":
    output = Path(sys.argv[1])
    if len(sys.argv) == 2:
        asyncio.run(probe(output))
    elif sys.argv[2] == "static":
        asyncio.run(static_policy(output))
    else:
        asyncio.run(exercise_worker_outage(output, sys.argv[2]))
