"""Versioned large model through real TLS API, durable jobs and bounded reads."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import ssl
import time
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import httpx2
import pytest
from test_k17_capacity_postgres import ORIGIN, _exercise
from test_k17_native_seed_postgres import _target

from aletheia_lab.hosted import persistence_migration as core
from aletheia_lab.hosted.capacity_seed_domain import confirm_mapping, source_payload
from aletheia_lab.hosted.capacity_seed_models import SeedPlan
from aletheia_lab.hosted.upload_analysis import WINDOW_OBSERVATION_MODEL

pytestmark = [pytest.mark.integration, pytest.mark.skipif(
    not os.environ.get("K17_TEST_DATABASE_URL") or os.name != "posix",
    reason="Owned native PostgreSQL and POSIX process fixture required")]


async def exercise_window_pipeline(target, cert, actors, pool, saved):
    rows, requests = [], []
    plan = SeedPlan(namespace="k17_windowproof", analysis_model=WINDOW_OBSERVATION_MODEL,
                    source_rows={"S": 1000, "M": 10000, "L": 100000})
    async with httpx2.AsyncClient(base_url=target, verify=ssl.create_default_context(cafile=str(cert)),
            trust_env=False, timeout=65, headers={"host": "app.example.invalid"}) as client:
        for index, size in enumerate(("S", "M", "L")):
            actor = next(a for a in actors if a.role == "owner" and a.size == size)
            base = "/v1/workspaces/" + actor.values["workspace_id"]
            auth = {"cookie": actor.cookie.get_secret_value(), "origin": ORIGIN,
                    "x-csrf-token": actor.csrf.get_secret_value()}

            async def request(method, path, expected=200, *, auth=auth, size=size, **kwargs):
                headers = dict(auth)
                if method != "GET":
                    headers["idempotency-key"] = "window-" + uuid4().hex
                headers.update(kwargs.pop("headers", {}))
                started = time.monotonic()
                response = await client.request(method, path, headers=headers, **kwargs)
                requests.append({"size": size, "method": method, "path": path, "status": response.status_code,
                                 "bytes": len(response.content), "seconds": time.monotonic() - started})
                assert response.status_code == expected, (method, path, response.status_code, response.text[:500])
                return response

            async def finish(receipt, *, base=base, request=request):
                started = time.monotonic()
                async with asyncio.timeout(300):
                    while True:
                        job = (await request("GET", base + "/jobs/" + receipt["job_id"])).json()
                        if job["status"] in ("succeeded", "failed", "cancelled", "expired"):
                            assert job["status"] == "succeeded", job
                            break
                        await asyncio.sleep(1)
                output = await request("GET", base + "/jobs/" + receipt["job_id"] + "/output")
                assert len(output.content) <= 1048576
                return output.json()["result"], time.monotonic() - started

            payload = source_payload(plan, index, 0)
            source = (await request("POST", base + "/sources", 201, content=payload,
                                   headers={"content-type": "application/zip"})).json()
            preview, _ = await finish((await request("POST", base + "/jobs", 202,
                json={"operation": "preview", "source_id": source["source_id"]})).json())
            assert preview["source_sha256"] == hashlib.sha256(payload).hexdigest()
            confirmed, confirm_seconds = await finish((await request("POST", base + "/jobs", 202,
                json={"operation": "confirm", "source_id": source["source_id"],
                      "mapping": confirm_mapping(preview, 1, windowed=True)})).json())
            snapshot_id = confirmed["snapshot_id"]
            reference, analysis_seconds = await finish((await request("POST", base + "/jobs", 202,
                json={"operation": "analyze", "snapshot_id": snapshot_id, "question": "What is observed?"})).json())
            assert reference["kind"] == "result_reference" and reference["snapshot_id"] == snapshot_id
            result_id = reference["result_id"]
            path = base + "/results/" + result_id
            header = (await request("GET", path + "/header")).json()
            claims = plan.expected_claims(size)
            assert header["claim_count"] == header["evidence_count"] == claims
            assert header["node_count"] == claims * 2 + 2 and header["edge_count"] == claims * 3
            assert header["runtime"]["model"] == WINDOW_OBSERVATION_MODEL
            claim_ids, cursor = set(), None
            pages = 0
            while True:
                page = (await request("GET", path + "/claims", params={"limit": 50, **({"cursor": cursor} if cursor else {})})).json()
                assert len(page["items"]) <= 50 and page["total"] == claims
                for claim in page["items"]:
                    assert claim["id"] not in claim_ids
                    claim_ids.add(claim["id"])
                pages += 1
                cursor = page["next_cursor"]
                if cursor is None:
                    break
            assert len(claim_ids) == claims
            nodes, edges, cursor, graph_pages = set(), set(), None, 0
            while True:
                graph = (await request("GET", path + "/graph",
                    params={"limit": 100, **({"cursor": cursor} if cursor else {})})).json()
                page_nodes = {node["id"] for node in graph["graph"]["nodes"]}
                assert len(page_nodes) <= 100
                for edge in graph["graph"]["edges"]:
                    assert edge["id"] not in edges and {edge["source"], edge["target"]} <= page_nodes
                    edges.add(edge["id"])
                nodes.update(page_nodes)
                graph_pages += 1
                cursor = graph["next_cursor"]
                if not cursor:
                    break
            assert len(nodes) == claims * 2 + 2 and len(edges) == claims * 3
            claim = (await request("GET", path + "/claims", params={"limit": 1})).json()["items"][0]
            evidence = (await request("GET", path + "/evidence/" + claim["citation_ids"][0],
                                      params={"claim_id": claim["id"]})).json()
            assert evidence["relation"] == "citation" and evidence["evidence"]["source_sha256"] == source["sha256"]
            assert evidence["evidence"]["reproduction_ref"]["record_id"] == snapshot_id
            exports = {}
            for fmt in ("json", "markdown", "pdf"):
                started = time.monotonic()
                descriptor = (await request("POST", path + "/exports", 201, json={"format": fmt})).json()
                download = await request("GET", descriptor["download_url"])
                digest = hashlib.sha256(download.content).hexdigest()
                assert digest == descriptor["sha256"] and len(download.content) == descriptor["byte_size"]
                if fmt == "json":
                    report = download.json()
                    assert len(report["claims"]) == len(report["evidence"]) == claims
                    assert report["result"]["id"] == result_id
                elif fmt == "pdf":
                    assert download.content.startswith(b"%PDF-")
                exports[fmt] = {"bytes": len(download.content), "sha256": digest,
                                "seconds": time.monotonic() - started}
            other = next(a for a in actors if a.values["workspace_id"] != actor.values["workspace_id"])
            denied = await client.get(path + "/header", headers={"cookie": other.cookie.get_secret_value()})
            assert denied.status_code in (403, 404)
            rows.append({"size": size, "observations": claims * 10, "claims": claims, "evidence": claims,
                         "nodes": claims * 2 + 2, "edges": claims * 3, "source_bytes": len(payload),
                         "confirm_seconds": confirm_seconds, "analysis_seconds": analysis_seconds,
                         "claim_pages": pages, "graph_pages": graph_pages,
                         "exports": exports, "cross_workspace_status": denied.status_code})
            (saved / "window-pipeline-progress.json").write_text(json.dumps(rows, indent=2) + "\n")
    async with pool.connection() as conn:
        versions = await (await conn.execute("SELECT version,checksum FROM k05_persistence_migrations ORDER BY version")).fetchall()
    (saved / "window-pipeline.json").write_text(json.dumps({"status": "passed", "primary_pass": False,
        "rows": rows, "requests": requests, "schema_versions": versions,
        "topology": "Real TLS API + separate durable worker + PostgreSQL; synthetic authenticated sessions; no 50k load",
        "api_backend_timeout_seconds": 60, "api_request_timeout_seconds": 90}, indent=2) + "\n")


def test_real_tls_window_model_jobs_paging_and_whole_exports(tmp_path):
    _exercise(tmp_path, windowed=True)


def test_window_model_schema_upgrade_retains_installed_checksums():
    async def scenario():
        previous = core.MIGRATIONS[:-1]
        assert len(previous) == 17
        with patch.object(core, "MIGRATIONS", previous):
            manager = _target()
            pool, schema = await manager.__aenter__()
        try:
            async with pool.connection() as conn:
                ledger_sql = "SELECT version,checksum,signature FROM k05_persistence_migrations ORDER BY version"
                before = await (await conn.execute(ledger_sql)).fetchall()
                assert len(before) == 17 and not await core.verify(conn, schema)
                assert await core.migrate(conn, schema) == "upgraded"
                assert await core.verify(conn, schema)
                assert await core.migrate(conn, schema) == "unchanged"
                after = await (await conn.execute(ledger_sql)).fetchall()
                assert after[:17] == before and after[-1][0] == 18
        finally:
            await manager.__aexit__(None, None, None)
        if evidence := os.environ.get("K17_EVIDENCE_DIR"):
            (Path(evidence) / "migration-17-to-18.json").write_text(json.dumps({
                "from": 17, "to": 18, "unchanged_prefix": True, "migration_replay": "unchanged", "ledger": after,
            }, indent=2) + "\n")

    asyncio.run(scenario())
