"""Expanded recipes execute scoped reads, a real polling flow and an own-record review."""
import asyncio
import json
import time

import httpx2
from test_k17_capacity import _actor

from aletheia_lab.hosted.capacity import CapacityHarness
from aletheia_lab.hosted.capacity_profiles import calibration_profile, expanded_profile


def test_expanded_recipes_keep_conditional_choices_and_mutations_honest():
    base = calibration_profile(target="http://127.0.0.1:32109", origin="https://app.example.invalid",
        host="app.example.invalid", release_sha256="a" * 64, dataset_sha256="b" * 64)
    profile = expanded_profile(base)
    assert base.operation_dispositions["P06"] == profile.operation_dispositions["P06"] == "not_run"
    assert {p for p, disposition in profile.operation_dispositions.items() if disposition == "unsupported"} == {
        "P32", "P33", "P34"}
    assert profile.operation_dispositions["P22"] == "dedicated" and profile.job_polling == "updates"
    analysis = next(j for j in profile.journeys if j.name == "analysis_terminal").steps[0]
    assert analysis.path.endswith("/jobs") and analysis.body["operation"] == "analyze"
    assert "backend" not in analysis.body


def test_expanded_reads_preview_and_review_use_actor_scope_and_complete_job():
    base = calibration_profile(target="http://127.0.0.1:32109", origin="https://app.example.invalid",
        host="app.example.invalid", release_sha256="a" * 64, dataset_sha256="b" * 64)
    profile = expanded_profile(base)
    actor = _actor(role="owner")
    actor = actor.model_copy(update={"values": {**actor.values, "context_id": "ctx0", "snapshot_id": "snap0",
        "source_id": "src0", "source_sha256": "c" * 64}})
    workspace, project, result = (actor.values[k] for k in ("workspace_id", "project_id", "result_id"))
    scope = {"workspace_id": workspace, "project_id": project, "result_id": result}
    preview = {"workspace_id": workspace, "preview_id": "prev0", "source_id": "src0", "source_sha256": "c" * 64}
    requests = []
    review = {}

    def handler(request):
        requests.append((request.method, request.url.path))
        assert request.headers["cookie"] == actor.cookie.get_secret_value()
        path = request.url.path
        if request.method not in ("GET", "HEAD"):
            assert request.headers["x-csrf-token"] == actor.csrf.get_secret_value()
            assert request.headers["idempotency-key"]
        if path == "/v1/session":
            data = {"actor": {"user_id": actor.actor_id}, "session": {"context_id": "ctx0"}}
        elif path == "/v1/workspaces":
            data = {"items": [{"workspace_id": workspace}]}
        elif path.endswith("/capabilities"):
            data = {"workspace_id": workspace, "deployment_mode": "hosted"}
        elif path.endswith("/settings"):
            data = {"workspace_id": workspace, "source_policy": "zip_bundle_only"}
        elif path.endswith("/imports/preview"):
            assert json.loads(request.content) == {"schema_version": "product-api/v1-draft", "source_id": "src0"}
            return httpx2.Response(202, json={"workspace_id": workspace, "job_id": "job0"})
        elif path.endswith("/jobs/job0/updates"):
            data = {"workspace_id": workspace, "job_id": "job0", "revision": 1, "cursor": "opaque",
                "disposition": "snapshot", "poll_after_ms": None,
                "job": {"workspace_id": workspace, "job_id": "job0", "status": "succeeded"}}
        elif path.endswith("/jobs/job0/output"):
            data = {"workspace_id": workspace, "job_id": "job0", "operation": "preview", "result": preview}
        elif path.endswith("/imports/prev0"):
            data = preview
        elif path.endswith("/history"):
            data = {**scope, "annotation_id": path.split("/")[-2], "items": [review]}
        elif "/annotations/" in path:
            body = json.loads(request.content)
            assert body["status"] == "comment" and request.headers["if-match"] == '"0"'
            data = {**scope, "annotation_id": path.rsplit("/", 1)[1], "author_id": actor.actor_id,
                "revision": 1, "status": "comment", "body": body["body"]}
            review.update(data)
        else:
            data = {**scope, "items": [{"record_id": "record0"}]}
        return httpx2.Response(200, json=data)

    async def run():
        harness = CapacityHarness(profile, [actor], transport=httpx2.MockTransport(handler), verify_principals=False)
        harness._started = time.monotonic()
        async with httpx2.AsyncClient(base_url=profile.target, transport=harness.transport) as client:
            for journey in profile.journeys[len(base.journeys):]:
                await harness._execute(client, harness._offer(actor, journey, time.monotonic()))
        assert harness.counts["completed"] == 10
        assert harness.counts["jobs_admitted"] == harness.counts["jobs_terminal"] == 1
        assert not harness._fatal
        assert len(requests) == 14

    asyncio.run(run())
