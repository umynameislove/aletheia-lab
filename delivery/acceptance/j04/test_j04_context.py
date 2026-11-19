"""Exercise held responses from the native HTTPS API with two authenticated contexts."""
import asyncio
import hashlib
import json
import ssl
import time
from uuid import uuid4

import httpx2
import test_k17_window_pipeline_postgres as harness
from test_k17_capacity_postgres import ORIGIN, _exercise

from aletheia_lab.hosted.capacity_seed_domain import confirm_mapping, source_payload
from aletheia_lab.hosted.capacity_seed_models import SeedPlan
from aletheia_lab.hosted.upload_analysis import WINDOW_OBSERVATION_MODEL

VERSION = "product-api/v1-draft"


def headers(actor):
    return {"cookie": actor.cookie.get_secret_value(), "origin": ORIGIN,
            "x-csrf-token": actor.csrf.get_secret_value()}


async def verify_context(target, cert, actors, pool, saved):
    a = next(actor for actor in actors if actor.role == "owner" and actor.size == "S")
    b = next(actor for actor in actors if actor.role == "owner" and
             actor.values["workspace_id"] != a.values["workspace_id"])
    wa, wb = a.values["workspace_id"], b.values["workspace_id"]
    scope_a, scope_b = f"/v1/workspaces/{wa}", f"/v1/workspaces/{wb}"
    b_header_path = scope_b + "/results/" + b.values["result_id"] + "/header"
    plan = SeedPlan(namespace="k17_j04context", analysis_model=WINDOW_OBSERVATION_MODEL,
                    source_rows={"S": 100, "M": 200, "L": 400})
    fixtures = {
        "initial.zip": source_payload(plan, 0, 0),
        "import-second.zip": source_payload(plan, 1, 0),
        "refresh-changed.zip": source_payload(plan, 0, 1),
    }
    trace = []
    checks = {}
    started = time.monotonic()

    def mark(event, **values):
        trace.append({"elapsed_seconds": round(time.monotonic() - started, 6), "event": event, **values})

    async def b_state():
        async with pool.connection() as conn:
            projects = await (await conn.execute(
                "SELECT project_id,revision,deletion_tombstone_id FROM k05_projects "
                "WHERE workspace_id=%s ORDER BY project_id", (wb,))).fetchall()
            counts = await (await conn.execute(
                "SELECT (SELECT count(*) FROM j01_jobs WHERE workspace_id=%s),"
                "(SELECT count(*) FROM k05_records WHERE workspace_id=%s),"
                "(SELECT count(*) FROM k02_exports WHERE workspace_id=%s)", (wb, wb, wb))).fetchone()
            return projects, counts

    verify = ssl.create_default_context(cafile=str(cert))
    async with (
        httpx2.AsyncClient(base_url=target, verify=verify, trust_env=False, timeout=25) as client_a,
        httpx2.AsyncClient(base_url=target, verify=verify, trust_env=False, timeout=25) as client_b,
    ):
        async def request(method, path, status, *, as_b=False, key=None, **kwargs):
            client, actor = (client_b, b) if as_b else (client_a, a)
            signed = {"host": "app.example.invalid", **headers(actor), **kwargs.pop("headers", {})}
            if method != "GET":
                signed["idempotency-key"] = key or "j04-" + uuid4().hex
            response = await client.request(method, path, headers=signed, **kwargs)
            mark("http_response", context="B" if as_b else "A", method=method, path=path,
                 status=response.status_code)
            assert response.status_code == status, (method, path, response.status_code, response.text[:300])
            return response

        async def upload(name):
            data = fixtures[name]
            response = await request("POST", scope_a + "/sources", 201, content=data,
                                     headers={"content-type": "application/zip"})
            assert response.json()["sha256"] == hashlib.sha256(data).hexdigest()
            return response.json()["source_id"]

        async def finish(receipt):
            assert receipt["workspace_id"] == wa and receipt["status_url"].startswith(scope_a + "/jobs/")
            path = receipt["status_url"]
            async with asyncio.timeout(90):
                while True:
                    state = (await request("GET", path, 200)).json()
                    if state["status"] in ("succeeded", "failed", "cancelled", "expired"):
                        assert state["status"] == "succeeded", state
                        break
                    await asyncio.sleep(.35)
            result = (await request("GET", path + "/output", 200)).json()
            assert result["workspace_id"] == wa and result["job_id"] == receipt["job_id"]
            assert (await request("GET", path + "/output", 404, as_b=True)).json()["code"] == "not_found"
            return result["result"]

        async def submit(operation, **kwargs):
            submission = await request("POST", scope_a + "/jobs", 202, json={"operation": operation, **kwargs})
            return await finish(submission.json())

        async def prepare_import(name):
            source = await upload(name)
            preview = await submit("preview", source_id=source)
            assert preview["source_sha256"] == hashlib.sha256(fixtures[name]).hexdigest()
            return {"operation": "confirm", "source_id": source,
                    "mapping": confirm_mapping(preview, 1, windowed=True)}

        initial = await prepare_import("initial.zip")
        snapshot = (await submit("confirm", source_id=initial["source_id"], mapping=initial["mapping"]))["snapshot_id"]
        result_id = (await submit("analyze", snapshot_id=snapshot,
                                  question="Số liệu nào được ghi nhận?"))["result_id"]
        result_path = scope_a + "/results/" + result_id
        a_header = (await request("GET", result_path + "/header", 200)).json()
        project = a_header["project_id"]
        project_path = scope_a + "/projects/" + project
        claim = (await request("GET", result_path + "/claims", 200, params={"limit": 1})).json()["items"][0]
        b_header = (await request("GET", b_header_path, 200, as_b=True)).json()
        assert (a_header["workspace_id"], b_header["workspace_id"]) == (wa, wb)
        before_b = await b_state()

        async def revision():
            return (await request("GET", project_path, 200)).json()["revision"]

        async def held(operation, method, path, status, **kwargs):
            reached, deliver = asyncio.Event(), asyncio.Event()

            async def sent_by_a():
                response = await request(method, path, status, **kwargs)
                mark("response_held", operation=operation, context="A")
                reached.set()
                await deliver.wait()
                mark("response_delivered", operation=operation, context="A")
                return response

            async with asyncio.TaskGroup() as group:
                pending = group.create_task(sent_by_a())
                await asyncio.wait_for(reached.wait(), timeout=30)
                current_b = (await request("GET", b_header_path, 200, as_b=True)).json()
                assert current_b == b_header
                assert (await request("GET", result_path + "/header", 404, as_b=True)).json()["code"] == "not_found"
                mark("b_confirmed_before_release", operation=operation, context="B")
                deliver.set()
            response = pending.result()
            checks[operation] = {"status": response.status_code, "b_header_unchanged": True}
            return response.json()

        version = await revision()
        body = {"schema_version": VERSION, "selection_kind": "claim", "selection_id": claim["id"],
                "question": "Quan sát này dựa trên bằng chứng nào?"}
        follow_path = result_path + "/follow-ups"
        match = {"if-match": f'"{version}"'}
        key = "j04-held-follow-up-" + uuid4().hex
        follow = await held("follow_up", "POST", follow_path, 202, json=body, headers=match, key=key)
        assert (await request("POST", follow_path, 202, json=body, headers=match, key=key)).json() == follow
        conflict = await request("POST", follow_path, 409, json={**body, "question": "Một câu hỏi khác?"},
                                 headers=match, key=key)
        assert conflict.json()["code"] == "idempotency_conflict"
        output = await finish(follow)
        assert output["parent_result_id"] == result_id and output["project_id"] == project
        assert (await request("POST", follow_path, 202, json=body, headers=match, key=key)).json() == follow
        async with pool.connection() as conn:
            audits = await (await conn.execute(
                "SELECT action,count(*) FROM k06_action_audit WHERE job_id=%s GROUP BY action ORDER BY action",
                (follow["job_id"],))).fetchall()
            assert audits == [("job.admit", 1), ("job.publish", 1)]
        checks["follow_up"].update(job_id=follow["job_id"], audit=audits, modified_body_http=409)

        second = await prepare_import("import-second.zip")
        confirm = await held("import", "POST", scope_a + "/jobs", 202, json=second)
        confirmed = await finish(confirm)
        assert confirmed["snapshot_id"] != snapshot
        checks["import"].update(job_id=confirm["job_id"], snapshot_id=confirmed["snapshot_id"])

        version = await revision()
        refreshed_source = await upload("refresh-changed.zip")
        refresh = await held("refresh", "POST", project_path + "/refresh", 202,
                             json={"schema_version": VERSION, "source_id": refreshed_source},
                             headers={"if-match": f'"{version}"'})
        result = await finish(refresh)
        assert (result["status"], result["previous_snapshot_id"], result["revision"]) == ("new_snapshot", snapshot, version + 1)
        checks["refresh"].update(job_id=refresh["job_id"], snapshot_id=result["snapshot_id"])

        export = await held("export", "POST", result_path + "/exports", 201, json={"format": "json"})
        assert export["result_id"] == result_id and export["download_url"].startswith(scope_a + "/")
        data = (await request("GET", export["download_url"], 200)).content
        assert hashlib.sha256(data).hexdigest() == export["sha256"]
        assert (await request("GET", export["download_url"], 404, as_b=True)).json()["code"] == "not_found"
        checks["export"].update(result_id=result_id, sha256=export["sha256"], bytes=len(data))

        version = await revision()
        tombstone = await held("delete", "DELETE", project_path, 200, json={},
                               headers={"if-match": f'"{version}"'})
        assert tombstone["project_id"] == project and tombstone["revision"] == version + 1
        assert tombstone["status"] == "tombstoned"
        await request("GET", result_path + "/header", 404)
        await request("GET", export["download_url"], 404)
        checks["delete"].update(project_id=project, revision=tombstone["revision"],
                                result_after_delete_http=404, download_after_delete_http=404)
        assert (await request("GET", b_header_path, 200, as_b=True)).json() == b_header
        assert await b_state() == before_b

    for name, data in fixtures.items():
        (saved / name).write_bytes(data)
    (saved / "j04-context.json").write_text(json.dumps({
        "workspace_a": wa, "workspace_b": wb, "result_b": b.values["result_id"], "b_database_unchanged": True,
        "fixtures": {name: hashlib.sha256(data).hexdigest() for name, data in fixtures.items()},
        "checks": checks, "trace": trace,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def test_j04_real_https_context_held_responses(tmp_path, monkeypatch):
    monkeypatch.setattr(harness, "exercise_window_pipeline", verify_context)
    _exercise(tmp_path, windowed=True)
