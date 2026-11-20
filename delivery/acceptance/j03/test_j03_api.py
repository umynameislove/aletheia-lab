"""J03 Vietnamese fixture and negative reads through the existing native HTTPS harness."""
import asyncio
import hashlib
import io
import json
import ssl
import zipfile
from copy import copy, deepcopy
from pathlib import Path
from uuid import uuid4

import httpx2
import test_k17_window_pipeline_postgres as harness
from test_k17_capacity_postgres import ORIGIN, _exercise


def fixture_variant(payload, *, timestamp_only=False, replacement=None):
    stream = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(payload)) as before, zipfile.ZipFile(stream, "w") as after:
        for entry in before.infolist():
            info = copy(entry)
            data = before.read(entry)
            if timestamp_only:
                info.date_time = (2026, 11, 17, 8, 0, 0)
            if replacement and info.filename == "metrics.csv":
                old, new = (item.encode() for item in replacement)
                assert data.count(old) == 1
                data = data.replace(old, new, 1)
            after.writestr(info, data)
    assert stream.getvalue() != payload
    return stream.getvalue()


def mapping_from(preview):
    candidates = preview["mapping_candidates"]
    target = next(row for row in candidates["targets"] if row["available"])
    metric = next(row for row in candidates["metric_sources"] if row["available"])
    return {
        "schema_version": "project-mapping-request/v2",
        "target": {"mapping_id": target["mapping_id"], "project_item_id": target["project_item_id"],
                   "target_field": "target", "identifier_field": "id"},
        "metric_sources": [{"mapping_id": metric["mapping_id"], "project_item_id": metric["project_item_id"],
                            "format": "csv", "records_path": [], "metric_name_field": "name",
                            "metric_value_field": "value", "run_id_field": "run", "step_field": None,
                            "class_field": "class", "unit_field": "unit"}],
        "runs": [{"run_id": f"lần-{i}", "config_item_ids": []} for i in range(5)],
        "baseline_run_id": "lần-0",
        "metric_definitions": [{"metric_name": "độ_chính_xác", "direction": "higher_is_better",
                                "regression_threshold": .08, "unit": "%"}],
    }


async def verify(target, cert, actors, pool, saved):
    payload = (Path(__file__).parent / "vietnamese-5-run.zip").read_bytes()
    variants = {
        "vietnamese-5-run.zip": payload,
        "vietnamese-timestamp-only.zip": fixture_variant(payload, timestamp_only=True),
        "vietnamese-adverse.zip": fixture_variant(payload,
            replacement=("lần-1,độ_chính_xác,0.6,lớp B,%", "lần-1,độ_chính_xác,0.2,lớp B,%")),
        "vietnamese-missing-value.zip": fixture_variant(payload,
            replacement=("run,name,value,class,unit", "run,name,unexpected,class,unit")),
    }
    owner = next(a for a in actors if a.role == "owner" and a.size == "S")
    workspace = owner.values["workspace_id"]
    other = next(a for a in actors if a.values["workspace_id"] != workspace)
    base = f"/v1/workspaces/{workspace}"
    trace = []
    auth = {"cookie": owner.cookie.get_secret_value(), "origin": ORIGIN,
            "x-csrf-token": owner.csrf.get_secret_value()}

    async with httpx2.AsyncClient(base_url=target, verify=ssl.create_default_context(cafile=str(cert)),
            trust_env=False, timeout=20, headers={"host": "app.example.invalid"}) as client:
        async def request(method, path, expected, *, sign=auth, **kwargs):
            headers = {**sign, **kwargs.pop("headers", {})}
            if method != "GET":
                headers["idempotency-key"] = "j03-" + uuid4().hex
            response = await client.request(method, path, headers=headers, **kwargs)
            trace.append({"method": method, "path": path, "status": response.status_code})
            assert response.status_code == expected, (method, path, response.status_code, response.text[:300])
            return response.json()

        async def upload(data):
            receipt = await request("POST", base + "/sources", 201, content=data,
                                    headers={"content-type": "application/zip"})
            assert receipt["sha256"] == hashlib.sha256(data).hexdigest()
            return receipt

        async def finish(receipt, expected="succeeded"):
            path = base + "/jobs/" + receipt["job_id"]
            async with asyncio.timeout(90):
                while True:
                    state = await request("GET", path, 200)
                    if state["status"] in ("succeeded", "failed", "cancelled", "expired"):
                        assert state["status"] == expected, state
                        break
                    await asyncio.sleep(.35)
            if expected == "failed":
                problem = await request("GET", path + "/output", 404)
                assert problem["code"] == "not_found"
                return {"status": state["status"], "output_http": 404}
            return (await request("GET", path + "/output", 200))["result"]

        async def job(operation, expected="succeeded", **body):
            receipt = await request("POST", base + "/jobs", 202, json={"operation": operation, **body})
            return await finish(receipt, expected)

        async def snapshots():
            async with pool.connection() as conn:
                return (await (await conn.execute("SELECT count(*) FROM j01_upload_snapshots "
                                                  "WHERE workspace_id=%s", (workspace,))).fetchone())[0]

        source = await upload(payload)
        source_id = source["source_id"]
        preview = await job("preview", source_id=source_id)
        assert preview["source_sha256"] == source["sha256"]
        mapping = mapping_from(preview)
        before = await snapshots()
        invalid_mapping = deepcopy(mapping)
        invalid_mapping["target"]["target_field"] = "missing_field_j03"
        failed = await job("confirm", "failed", source_id=source_id, mapping=invalid_mapping)
        assert failed["status"] == "failed" and await snapshots() == before
        changed_after_preview = await upload(variants["vietnamese-adverse.zip"])
        stale_mapping = await job("confirm", "failed", source_id=changed_after_preview["source_id"], mapping=mapping)
        assert stale_mapping["status"] == "failed" and await snapshots() == before
        confirmed = await job("confirm", source_id=source_id, mapping=mapping)
        snapshot = confirmed["snapshot_id"]
        assert await snapshots() == before + 1
        analyzed = await job("analyze", snapshot_id=snapshot, question="Dữ liệu ghi nhận quan sát nào?")
        result = analyzed["result_id"]
        path = base + "/results/" + result
        header = await request("GET", path + "/header", 200)
        assert header["snapshot_id"] == snapshot and header["runtime"]["external_call"] is False
        assert header["result"]["causal_status"] == "unverified"
        claims = (await request("GET", path + "/claims", 200, params={"limit": 50}))["items"]
        assert len(claims) == header["claim_count"] == 10
        evidence = []
        for claim in claims:
            assert claim["epistemic_state"] == "observed" and claim["max_strength"] == "observation"
            assert len(claim["citation_ids"]) == 1 and not claim["counterevidence_ids"]
            view = await request("GET", path + "/evidence/" + claim["citation_ids"][0], 200,
                                 params={"claim_id": claim["id"]})
            assert view["relation"] == "citation"
            assert view["evidence"]["source_sha256"] == source["sha256"]
            assert view["evidence"]["reproduction_ref"]["record_id"] == snapshot
            evidence.append(view)

        await request("GET", path + "/evidence/" + claims[1]["citation_ids"][0], 404,
                      params={"claim_id": claims[0]["id"]})
        await request("GET", path + "/header", 404, sign={"cookie": other.cookie.get_secret_value()})
        cursor_path = path + "/claims"
        page = await request("GET", cursor_path, 200, params={"limit": 2})
        assert page["next_cursor"] and len(page["items"]) == 2
        cursor_error = await request("GET", cursor_path, 400, params={"limit": 2, "cursor": "j03-invalid-cursor"})
        assert cursor_error["code"] == "cursor_invalid"
        assert (await request("GET", cursor_path, 200, params={"limit": 2}))["items"] == page["items"]
        await request("GET", path + "/graph", 400, params={"limit": 101})

        project = header["project_id"]
        async with pool.connection() as conn:
            revision = (await (await conn.execute("SELECT revision FROM k05_projects WHERE "
                        "workspace_id=%s AND project_id=%s", (workspace, project))).fetchone())[0]
        refresh_path = base + "/projects/" + project + "/refresh"

        async def refresh(name, current_revision, terminal="succeeded"):
            new = await upload(variants[name])
            receipt = await request("POST", refresh_path, 202,
                                    headers={"if-match": f'"{current_revision}"'},
                                    json={"schema_version": "product-api/v1-draft", "source_id": new["source_id"]})
            return await finish(receipt, terminal), new["source_id"]

        same, _ = await refresh("vietnamese-timestamp-only.zip", revision)
        assert (same["status"], same["snapshot_id"], same["revision"]) == ("unchanged", snapshot, revision)
        missing, _ = await refresh("vietnamese-missing-value.zip", revision, "failed")
        assert missing["status"] == "failed" and await snapshots() == before + 1
        changed, new_source = await refresh("vietnamese-adverse.zip", revision)
        assert changed["status"] == "new_snapshot" and changed["revision"] == revision + 1
        stale = await request("POST", refresh_path, 412, headers={"if-match": f'"{revision}"'},
                              json={"schema_version": "product-api/v1-draft", "source_id": new_source})
        assert stale["code"] == "revision_conflict"
        assert changed["snapshot_id"] != snapshot and await snapshots() == before + 2
        assert await request("GET", path + "/header", 200) == header
        assert (await request("GET", path + "/claims", 200, params={"limit": 50}))["items"] == claims
        for item in evidence:
            assert await request("GET", path + "/evidence/" + item["evidence_id"], 200,
                                 params={"claim_id": item["claim_id"]}) == item

    for name, data in variants.items():
        (saved / name).write_bytes(data)
    (saved / "j03-vietnamese.json").write_text(json.dumps({
        "source_sha256": source["sha256"], "mapping": mapping, "result_header": header,
        "claims": claims, "evidence": evidence, "invalid_mapping": failed, "changed_after_preview": stale_mapping,
        "timestamp_only": same, "adverse": changed,
        "missing_value": missing, "stale_revision": stale["code"], "cursor": cursor_error["code"],
        "requests": trace,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def test_j03_vietnamese_api_refresh_and_read_boundaries(tmp_path, monkeypatch):
    monkeypatch.setattr(harness, "exercise_window_pipeline", verify)
    _exercise(tmp_path, windowed=True)
