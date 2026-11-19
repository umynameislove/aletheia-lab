"""HTTP job responses must respect producer schema, request scope and budgets."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import SecretStr

from aletheia_lab.hosted.job_http import JobApplication
from aletheia_lab.hosted.read_backend import SessionRecord
from aletheia_lab.hosted.request_boundary import BodyLimits
from aletheia_lab.hosted.settings import RuntimeSettings
from aletheia_lab.hosted_auth.boundary import SESSION_COOKIE
from aletheia_lab.hosted_auth.csrf import CSRF_COOKIE, CsrfTokens
from aletheia_lab.hosted_contract.job_page import Job, JobReceipt
from aletheia_lab.hosted_contract.session_capabilities import Actor

VERSION = "product-api/v1-draft"
PATH = "/v1/workspaces/ws_boundary/jobs"
PRIVATE = "private_marker_do_not_disclose"


@dataclass
class _Jobs:
    receipt: JobReceipt
    job: Job
    result: dict
    enqueue_calls: int = 0

    async def enqueue(self, **kwargs):
        self.enqueue_calls += 1
        return self.receipt

    async def status(self, **kwargs):
        return self.job

    async def output(self, **kwargs):
        return self.result


def _system(tmp_path, *, maximum_depth=64):
    tokens = CsrfTokens(SecretStr("synthetic-signing-key-" + "s" * 43))
    proof = tokens.issue("ctx_boundary")
    record = SessionRecord(
        actor=Actor(user_id="user_boundary", display_name="Synthetic", email="synthetic@example.invalid",
                    email_verified=True), context_id="ctx_boundary", csrf_token=proof,
        expires_at=datetime.now(UTC) + timedelta(hours=1), onboarding="ready",
        last_workspace_id="ws_boundary", state="active", actor_enabled=True,
    )

    class Sessions:
        async def resolve_cookie(self, cookie):
            return record

    runtime = RuntimeSettings.model_validate({
        "environment": "local", "public_origin": "https://app.example.invalid",
        "identity": {"issuer": "https://identity.example.invalid", "client_id": "synthetic",
                     "redirect_uri": "https://app.example.invalid/callback", "client_kind": "public"},
        "storage": {"database_url_env": "ALETHEIA_HOSTED_DATABASE_URL", "object_root": str(tmp_path)},
        "api": {"maximum_json_depth": maximum_depth, "maximum_response_bytes": 4096},
    })
    jobs = _Jobs(
        JobReceipt(schema_version=VERSION, workspace_id="ws_boundary", job_id="job_boundary", status="queued",
                   request_id="req_boundary", status_url=PATH + "/job_boundary"),
        Job(schema_version=VERSION, workspace_id="ws_boundary", job_id="job_boundary", status="succeeded", progress=1.0),
        {"schema_version": VERSION, "workspace_id": "ws_boundary", "job_id": "job_boundary",
         "operation": "preview", "result": {"source_sha256": "a" * 64}},
    )

    async def fallback(scope, receive, send):
        raise AssertionError("Unexpected route")

    async def ready():
        return True

    app = JobApplication(fallback, jobs, Sessions(), tokens, runtime,
                         body_limits=BodyLimits(10000, 1, 2, 10), clock_check=ready)
    cookie = (f"{SESSION_COOKIE}={'x' * 43}; {CSRF_COOKIE}={proof.get_secret_value()}").encode("ascii")
    headers = [(b"host", b"app.example.invalid"), (b"cookie", cookie),
               (b"origin", b"https://app.example.invalid"), (b"content-type", b"application/json"),
               (b"x-csrf-token", proof.get_secret_value().encode("ascii")),
               (b"idempotency-key", b"synthetic-request-key-0001")]
    return app, jobs, headers


def _request(app, headers, *, method="GET", path=PATH + "/job_boundary", body=b""):
    async def run():
        messages = []
        sent = False

        async def receive():
            nonlocal sent
            assert not sent, "Unexpected repeated body read"
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}

        async def send(message):
            messages.append(message)

        await app({"type": "http", "scheme": "https", "path": path, "method": method,
                   "headers": headers}, receive, send)
        return messages[0]["status"], dict(messages[0]["headers"]), messages[1]["body"]

    return asyncio.run(run())


@pytest.mark.parametrize("kind,status", [("receipt", 202), ("job", 200), ("result", 200)])
def test_valid_job_responses_keep_existing_contract(tmp_path, kind, status):
    app, jobs, headers = _system(tmp_path)
    kwargs = {"method": "POST", "path": PATH, "body": b'{"operation":"preview","source_id":"src1"}'} if kind == "receipt" else {}
    if kind == "result":
        kwargs["path"] = PATH + "/job_boundary/output"
    observed, response_headers, body = _request(app, headers, **kwargs)
    assert observed == status
    assert json.loads(body)["workspace_id"] == "ws_boundary"
    assert response_headers[b"cache-control"] == b"no-store"
    assert int(response_headers[b"content-length"]) == len(body)
    assert jobs.enqueue_calls == (kind == "receipt")


@pytest.mark.parametrize("change", [
    {"workspace_id": PRIVATE, "status_url": f"/v1/workspaces/{PRIVATE}/jobs/job_boundary"},
    {"job_id": PRIVATE}, {"schema_version": PRIVATE}, {"status": "succeeded"},
    {"status_url": "/" + PRIVATE}, {"request_id": PRIVATE * 300},
])
def test_corrupt_receipt_is_rejected_without_disclosing_fields(tmp_path, change):
    app, jobs, headers = _system(tmp_path)
    jobs.receipt = jobs.receipt.model_copy(update=change)
    status, _, body = _request(app, headers, method="POST", path=PATH,
                               body=b'{"operation":"preview","source_id":"src1"}')
    assert status == 500 and PRIVATE.encode() not in body
    assert json.loads(body)["code"] in ("service_failure", "response_invalid")


@pytest.mark.parametrize("change", [
    {"workspace_id": PRIVATE}, {"job_id": PRIVATE}, {"schema_version": PRIVATE},
    {"progress": float("nan")}, {"status": PRIVATE}, {"workspace_id": PRIVATE * 300},
])
def test_corrupt_job_status_is_rejected_without_disclosing_fields(tmp_path, change):
    app, jobs, headers = _system(tmp_path)
    jobs.job = jobs.job.model_copy(update=change)
    status, _, body = _request(app, headers)
    assert status == 500 and PRIVATE.encode() not in body
    assert json.loads(body)["code"] in ("service_failure", "response_invalid")


@pytest.mark.parametrize("change", [
    {"schema_version": PRIVATE}, {"workspace_id": PRIVATE}, {"job_id": PRIVATE},
    {"operation": PRIVATE}, {"result": PRIVATE}, {PRIVATE: "unexpected"},
    {"result": {"oversized": PRIVATE * 300}},
])
def test_invalid_job_output_envelope_or_size_is_rejected(tmp_path, change):
    app, jobs, headers = _system(tmp_path)
    jobs.result.update(change)
    status, _, body = _request(app, headers, path=PATH + "/job_boundary/output")
    assert status == 500 and PRIVATE.encode() not in body
    assert json.loads(body)["code"] in ("service_failure", "response_invalid")


def test_missing_job_output_field_is_rejected(tmp_path):
    app, jobs, headers = _system(tmp_path)
    del jobs.result["result"]
    status, _, body = _request(app, headers, path=PATH + "/job_boundary/output")
    assert status == 500
    assert json.loads(body)["code"] == "service_failure"


def test_configured_json_depth_rejects_submission_before_enqueue(tmp_path):
    app, jobs, headers = _system(tmp_path, maximum_depth=2)
    status, _, body = _request(app, headers, method="POST", path=PATH,
        body=b'{"operation":"confirm","source_id":"src1","mapping":{"nested":{"value":"secret"}}}')
    assert status == 400 and json.loads(body)["code"] == "json_too_deep"
    assert jobs.enqueue_calls == 0 and b"secret" not in body
