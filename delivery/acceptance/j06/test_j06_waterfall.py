"""Measure serial synthetic journeys on the existing native TLS/PG harness."""
import asyncio
import hashlib
import json
import os
import ssl
import time
from contextlib import asynccontextmanager
from functools import partial
from pathlib import Path
from uuid import uuid4

import httpx2
import pytest
import test_k17_window_pipeline_postgres as harness
from test_k17_capacity_postgres import ORIGIN, _exercise

from aletheia_lab.hosted.capacity_seed_domain import confirm_mapping, source_payload
from aletheia_lab.hosted.capacity_seed_models import SeedPlan
from aletheia_lab.hosted.upload_analysis import WINDOW_OBSERVATION_MODEL

VERSION = "product-api/v1-draft"
ROLES = ("api", "worker")
COUNTERS = ("statements", "sql_exec_ms", "sql_rows", "wal_bytes", "cpu_seconds")


def processes():
    """Sample native parent CPU plus reaped-child CPU, instantaneous RSS and FDs."""
    found = {}
    for directory in Path("/proc").iterdir():
        if not directory.name.isdecimal():
            continue
        try:
            parts = (directory / "cmdline").read_bytes().rstrip(b"\0").split(b"\0")
            if len(parts) < 7 or parts[1:3] != [b"-B", b"-c"] or parts[-4] not in (b"api", b"worker"):
                continue
            role = parts[-4].decode()
            fields = (directory / "stat").read_text().rsplit(") ", 1)[1].split()
            found[role] = {
                "cpu_seconds": sum(map(int, fields[11:15])) / os.sysconf("SC_CLK_TCK"),
                "rss_bytes": int(fields[21]) * os.sysconf("SC_PAGE_SIZE"),
                "fds": sum(1 for _ in (directory / "fd").iterdir()),
            }
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            continue
    assert set(found) == set(ROLES), sorted(found)
    return found


async def sample(pool):
    async with pool.connection() as conn:
        rows = await (await conn.execute("""
            WITH roles AS (
                SELECT oid, rolname, split_part(rolname, '_', 2) AS role
                FROM pg_roles WHERE rolname ~ '^k17_(api|worker)_[a-f0-9]+$'
            ), stmts AS (
                SELECT userid, sum(calls) AS calls, sum(total_exec_time) AS ms,
                       sum(rows) AS rows, sum(wal_bytes) AS wal
                FROM public.pg_stat_statements(false)
                WHERE dbid=(SELECT oid FROM pg_database WHERE datname=current_database())
                GROUP BY userid
            )
            SELECT role, coalesce(calls,0)::bigint, coalesce(ms,0)::float8,
                   coalesce(rows,0)::bigint, coalesce(wal,0)::bigint,
                   (SELECT count(*) FROM pg_stat_activity
                    WHERE datname=current_database() AND usename=roles.rolname)
            FROM roles LEFT JOIN stmts ON userid=roles.oid
        """)).fetchall()
    metrics = processes()
    for role, calls, milliseconds, queried_rows, wal, connections in rows:
        metrics[role].update(statements=calls, sql_exec_ms=milliseconds, sql_rows=queried_rows,
                             wal_bytes=wal, open_connections=connections)
    assert all("statements" in entry for entry in metrics.values())
    return metrics


def delta(before, after, role):
    changes = {key: round(after[role][key] - before[role][key], 6) for key in COUNTERS}
    assert all(value >= 0 for value in changes.values()), changes
    return changes


async def measure(target, cert, actors, pool, saved, *, sizes):
    rows, requests = [], []
    # The harness seeds identities and composes the normal HTTPS boundaries.
    plan = SeedPlan(namespace="k17_j06waterfall", analysis_model=WINDOW_OBSERVATION_MODEL,
                    source_rows={"S": 1000, "M": 10000, "L": 100000})
    tls = ssl.create_default_context(cafile=str(cert))
    idle_before = await sample(pool)
    idle_start = time.monotonic()
    await asyncio.sleep(2)
    idle_after = await sample(pool)
    idle = {"seconds": time.monotonic() - idle_start,
            **{role: delta(idle_before, idle_after, role) for role in ROLES}}
    progress = saved / "waterfall.partial.json"

    def checkpoint():
        progress.write_text(json.dumps({
            "scope": "Serial probe over TLS, one API pool cap 4, one worker pool cap 2 and two worker slots. "
                     "Synthetic authenticated sessions and local evidence model. Body bytes exclude headers/TLS.",
            "sizes": sizes, "idle_control": idle, "actions": rows, "requests": requests,
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    async with httpx2.AsyncClient(base_url=target, verify=tls, trust_env=False,
                                 timeout=120 if sizes == ("L",) else 60) as client:
        for size in sizes:
            index = ("S", "M", "L").index(size)
            actor = next(a for a in actors if a.role == "owner" and a.size == size)
            workspace = actor.values["workspace_id"]
            base = f"/v1/workspaces/{workspace}"
            auth = {"host": "app.example.invalid", "cookie": actor.cookie.get_secret_value(),
                    "origin": ORIGIN, "x-csrf-token": actor.csrf.get_secret_value()}
            payload = source_payload(plan, index, 0)
            label = ""
            action_number = 0

            @asynccontextmanager
            async def action(name):
                nonlocal label, action_number
                label, action_number = name, len(rows) + 1
                start_index = len(requests)
                before = await sample(pool)
                started = time.monotonic()
                info = {"jobs": 0}
                try:
                    yield info
                except Exception:
                    checkpoint()
                    raise
                duration = time.monotonic() - started
                after = await sample(pool)
                served = requests[start_index:]
                rows.append({"sample": action_number, "action": name, "size": size,
                    "seconds": duration, "http_requests": len(served),
                    "http_wall_seconds_sum": sum(row["seconds"] for row in served),
                    "request_body_bytes": sum(row["request_body_bytes"] for row in served),
                    "response_body_bytes": sum(row["response_body_bytes"] for row in served),
                    "status_polls": sum(row["operation"] == "P20.status" for row in served),
                    "api": delta(before, after, "api"), "worker_gross": delta(before, after, "worker"),
                    "sampled_resources": {
                        role: {key: max(before[role][key], after[role][key])
                               for key in ("rss_bytes", "fds", "open_connections")} for role in ROLES
                    }, **info})
                if info["jobs"]:
                    print(f"{size} {name}: {len(served)} HTTP in {duration:.2f}s", flush=True)
                    checkpoint()

            async def call(code, method, path, status=200, **options):
                headers = {**auth, **options.pop("headers", {})}
                if method != "GET":
                    headers["idempotency-key"] = "j0602-" + uuid4().hex
                before = await sample(pool)
                t = time.monotonic()
                try:
                    response = await client.request(method, path, headers=headers, **options)
                except httpx2.TransportError as exc:
                    exc.add_note(f"{size} {label} {code}")
                    raise
                seconds = time.monotonic() - t
                after = await sample(pool)
                assert response.status_code == status, (code, response.status_code, response.text[:300])
                requests.append({"sample": action_number, "action": label, "size": size, "operation": code,
                    "method": method, "status": response.status_code, "seconds": seconds,
                    "request_body_bytes": len(response.request.content), "response_body_bytes": len(response.content),
                    "api": delta(before, after, "api")})
                return response

            async def terminal(info, receipt):
                info["jobs"] += 1
                job_path = base + "/jobs/" + receipt["job_id"]
                async with asyncio.timeout(600 if size == "L" else 240):
                    while True:
                        state = (await call("P20.status", "GET", job_path)).json()
                        if state["status"] in ("succeeded", "failed", "cancelled", "expired"):
                            assert state["status"] == "succeeded", state
                            info["terminal"] = "succeeded"
                            break
                        await asyncio.sleep(2)
                return (await call("P20.output", "GET", job_path + "/output")).json()["result"]

            async with action("upload_preview") as info:
                source = (await call("direct_zip_source", "POST", base + "/sources", 201, content=payload,
                                     headers={"content-type": "application/zip"})).json()
                rec = (await call("P08.preview", "POST", base + "/jobs", 202,
                                  json={"operation": "preview", "source_id": source["source_id"]})).json()
                preview = await terminal(info, rec)
                assert source["sha256"] == preview["source_sha256"] == hashlib.sha256(payload).hexdigest()

            async with action("confirm_snapshot") as info:
                rec = (await call("P09.confirm", "POST", base + "/jobs", 202,
                    json={"operation": "confirm", "source_id": source["source_id"],
                          "mapping": confirm_mapping(preview, 1, windowed=True)})).json()
                snap = (await terminal(info, rec))["snapshot_id"]

            async with action("analysis_to_result") as info:
                rec = (await call("P13.analyze", "POST", base + "/jobs", 202,
                                  json={"operation": "analyze", "snapshot_id": snap,
                                        "question": "Dữ liệu ghi nhận quan sát nào?"})).json()
                result = await terminal(info, rec)
                assert result["kind"] == "result_reference" and result["snapshot_id"] == snap
                result_id = result["result_id"]

            endpoint = base + "/results/" + result_id
            for _ in range(6):
                async with action("session"):
                    await call("P01.session", "GET", "/v1/session")
                async with action("capabilities"):
                    await call("P04.capabilities", "GET", base + "/capabilities")
                async with action("result_header"):
                    header = (await call("P15.header", "GET", endpoint + "/header")).json()
                    assert header["result_id"] == result_id and header["claim_count"] == plan.expected_claims(size)
                async with action("claim_and_citation"):
                    claims = (await call("P16.claims", "GET", endpoint + "/claims",
                                         params={"limit": 50})).json()
                    claim = claims["items"][0]
                    citation = (await call("P17.evidence", "GET", endpoint + "/evidence/" + claim["citation_ids"][0],
                                           params={"claim_id": claim["id"]})).json()
                    assert citation["relation"] == "citation" and citation["evidence"]["source_sha256"] == source["sha256"]
                async with action("graph_page"):
                    graph = (await call("P18.graph", "GET", endpoint + "/graph", params={"limit": 100})).json()
                    assert 0 < len(graph["graph"]["nodes"]) <= 100 and graph["next_cursor"]

            if size != "L":
                async with action("follow_up_to_result") as info:
                    project = (await call("P11.project", "GET", base + "/projects/" + header["project_id"])).json()
                    rec = (await call("P19.follow_up", "POST", endpoint + "/follow-ups", 202,
                        headers={"if-match": f'"{project["revision"]}"'},
                        json={"schema_version": VERSION, "selection_kind": "claim", "selection_id": claim["id"],
                              "question": "Quan sát này dựa trên bằng chứng nào?"})).json()
                    followed = await terminal(info, rec)
                    assert followed["parent_result_id"] == result_id and followed["snapshot_id"] == snap

            for format in ("json", "markdown", "pdf") if size == "S" else ("json",):
                async with action("export_" + format) as info:
                    desc = (await call("P23.export_" + format, "POST", endpoint + "/exports", 201,
                                       json={"format": format})).json()
                    downloaded = await call("P24.download_" + format, "GET", desc["download_url"])
                    assert hashlib.sha256(downloaded.content).hexdigest() == desc["sha256"]
                    assert len(downloaded.content) == desc["byte_size"] and desc["result_id"] == result_id
                    info["download_hash_verified"] = True

    assert len(rows) == sum({"S": 37, "M": 35, "L": 34}[size] for size in sizes)
    assert sum(row["http_requests"] for row in rows) == len(requests)
    assert sum(row["jobs"] for row in rows) == sum(3 if size == "L" else 4 for size in sizes)
    assert all(row["api"]["statements"] >= 1 for row in requests)
    checkpoint()
    progress.replace(saved / "waterfall.json")


@pytest.mark.parametrize("sizes", [("S", "M"), ("L",)], ids=["SM", "L"])
def test_j06_tls_waterfall(tmp_path, monkeypatch, sizes):
    monkeypatch.setattr(harness, "exercise_window_pipeline", partial(measure, sizes=sizes))
    _exercise(tmp_path, windowed=True)
