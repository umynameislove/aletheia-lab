"""P19 follow-up boundary uses current session, CSRF and durable-job ports."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from aletheia_lab.hosted.application import VERSION
from aletheia_lab.hosted.authorization import AuthorizationDenied
from aletheia_lab.hosted.postgres_sources import SourceConflict, SourceNotFound
from aletheia_lab.hosted.project_http import ProjectOperationsApplication
from aletheia_lab.hosted.read_backend import SessionRecord
from aletheia_lab.hosted.request_boundary import BodyLimits
from aletheia_lab.hosted.settings import RuntimeSettings
from aletheia_lab.hosted.workspace_management import WorkspaceConflict
from aletheia_lab.hosted_auth.boundary import SESSION_COOKIE
from aletheia_lab.hosted_auth.csrf import CSRF_COOKIE, CsrfTokens
from aletheia_lab.hosted_auth.sessions import credential_digest
from aletheia_lab.hosted_contract.job_page import JobReceipt
from aletheia_lab.hosted_contract.session_capabilities import Actor, Problem

RESULT = "p6-result-" + "a" * 64
PATH = "/v1/workspaces/ws1/results/" + RESULT + "/follow-ups"
BODY = {"schema_version": VERSION, "selection_kind": "claim", "selection_id": "claim1",
        "question": "Why did the reported loss increase?"}
KEY = "synthetic-follow-up-request-0001"


@pytest.fixture
def system(tmp_path):
    state = SimpleNamespace(calls=[], reads=[], fallback=[], authenticated=True, ready=True, error=None)
    tokens = CsrfTokens(SecretStr("synthetic-signing-key-" + "s" * 43))
    proof = tokens.issue("ctx1")
    record = SessionRecord(Actor(user_id="u1", display_name="Synthetic", email="u1@example.invalid",
        email_verified=True), "ctx1", datetime.now(UTC) + timedelta(minutes=5), proof,
        "ready", "ws1", "active", True)
    state.receipt = JobReceipt(schema_version=VERSION, workspace_id="ws1", job_id="job1", status="queued",
                               request_id="req1", status_url="/v1/workspaces/ws1/jobs/job1")

    class Sessions:
        async def resolve_cookie(self, cookie):
            return record if state.authenticated else None

    class Jobs:
        async def enqueue_follow_up(self, **kwargs):
            state.calls.append(kwargs)
            if state.error is not None:
                raise state.error
            return state.receipt

    runtime = RuntimeSettings.model_validate({
        "environment": "local", "public_origin": "https://app.example.invalid",
        "identity": {"issuer": "https://id.example.invalid", "client_id": "synthetic",
                     "redirect_uri": "https://app.example.invalid/callback", "client_kind": "public"},
        "storage": {"database_url_env": "ALETHEIA_HOSTED_DATABASE_URL", "object_root": str(tmp_path)},
    })

    async def clock():
        return state.ready

    async def fallback(scope, receive, send):
        state.fallback.append(scope["path"])
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    state.app = ProjectOperationsApplication(fallback, Jobs(), Sessions(), tokens, runtime,
                                             body_limits=BodyLimits(12000, 1, 3, 10), clock_check=clock)
    cookie = f"{SESSION_COOKIE}={'a' * 43}; {CSRF_COOKIE}={proof.get_secret_value()}".encode()
    state.headers = [(b"host", b"app.example.invalid"), (b"origin", b"https://app.example.invalid"),
                     (b"cookie", cookie), (b"content-type", b"application/json"),
                     (b"x-csrf-token", proof.get_secret_value().encode()),
                     (b"if-match", b'"3"'), (b"idempotency-key", KEY.encode())]
    return state


def request(system, *, body=None, method="POST", path=PATH, remove=(), extra=()):
    async def run():
        output = []
        payload = json.dumps(BODY if body is None else body).encode()

        async def receive():
            system.reads.append(True)
            return {"type": "http.request", "body": payload, "more_body": False}

        async def send(message):
            output.append(message)

        headers = [(key, value) for key, value in system.headers if key not in remove] + list(extra)
        await system.app({"type": "http", "scheme": "https", "method": method, "path": path,
                          "headers": headers, "query_string": b""}, receive, send)
        return output

    return asyncio.run(run())


@pytest.mark.parametrize("selection_kind", ["claim", "node"])
def test_follow_up_uses_only_current_cookie_and_exact_parent_and_revision(system, selection_kind):
    body = {**BODY, "selection_kind": selection_kind}
    start, end = request(system, body=body)
    assert start["status"] == 202
    assert JobReceipt.model_validate_json(end["body"]) == system.receipt
    assert (b"cache-control", b"no-store") in start["headers"]
    assert len(system.calls) == 1
    values = system.calls[0]
    assert set(values) == {"credential_digest", "workspace_id", "parent_result_id",
                           "expected_revision", "request_key", "selection_kind", "selection_id", "question"}
    assert values["credential_digest"] == credential_digest(SecretStr("a" * 43))
    assert values["workspace_id"] == "ws1" and values["parent_result_id"] == RESULT
    assert values["expected_revision"] == 3 and values["request_key"] == SecretStr(KEY)
    assert {key: values[key] for key in ("selection_kind", "selection_id", "question")} == {
        key: body[key] for key in ("selection_kind", "selection_id", "question")}


@pytest.mark.parametrize("question", ["x" * 2001, "é" * 4000])
def test_follow_up_preserves_the_full_question_within_the_product_limit(system, question):
    # Escaped Unicode JSON needs its own byte budget independently of character count.
    system.app._limits = BodyLimits(30000, 1, 3, 10)
    start, _ = request(system, body={**BODY, "question": question})
    assert start["status"] == 202 and len(system.calls) == 1
    assert system.calls[0]["question"] == question


@pytest.mark.parametrize("field,value", [
    ("project_id", "untrusted_parent"), ("parent_result_id", "untrusted_parent"),
    ("actor_id", "untrusted_actor"), ("workspace_id", "untrusted_workspace"),
    ("schema_version", "product-api/v99"), ("selection_kind", "project"),
    ("selection_id", "../outside"), ("question", ""), ("question", "x" * 4001),
    ("question", "password=private_marker_do_not_disclose"),
])
def test_invalid_follow_up_never_reaches_job_repository_or_reflects_input(system, field, value):
    start, end = request(system, body={**BODY, field: value})
    assert start["status"] == 422 and not system.calls
    assert Problem.model_validate_json(end["body"]).code == "schema_invalid"
    assert b"private_marker_do_not_disclose" not in end["body"]


def test_schema_version_is_required(system):
    body = {key: value for key, value in BODY.items() if key != "schema_version"}
    assert request(system, body=body)[0]["status"] == 422 and not system.calls


@pytest.mark.parametrize("name,value", [
    (b"x-csrf-token", b"wrong"), (b"origin", b"https://outside.example.invalid"),
])
def test_csrf_or_origin_denial_has_zero_body_reads_or_effects(system, name, value):
    output = request(system, remove=(name,), extra=((name, value),))
    assert output[0]["status"] == 403
    assert not system.calls and not system.reads


def test_unknown_session_has_no_effect(system):
    system.authenticated = False
    assert request(system)[0]["status"] == 401
    assert not system.calls and not system.reads


@pytest.mark.parametrize("values,status", [
    ((), 428), ((b"3",), 400), ((b'W/"3"',), 400),
    ((b'"3"', b'"3"'), 403), ((b'"-1"',), 400),
])
def test_current_strong_revision_is_required_before_body_or_effect(system, values, status):
    output = request(system, remove=(b"if-match",), extra=tuple((b"if-match", value) for value in values))
    assert output[0]["status"] == status and not system.calls and not system.reads


@pytest.mark.parametrize("values", [(), (b"short",), (KEY.encode(), KEY.encode())])
def test_idempotency_key_must_be_single_and_valid(system, values):
    output = request(system, remove=(b"idempotency-key",),
                     extra=tuple((b"idempotency-key", value) for value in values))
    assert output[0]["status"] == 400 and not system.calls


@pytest.mark.parametrize("error,status,code", [
    (AuthorizationDenied(), 404, "not_found"), (SourceNotFound(), 404, "not_found"),
    (WorkspaceConflict(), 412, "revision_conflict"), (SourceConflict(), 409, "idempotency_conflict"),
    (RuntimeError("private_marker_do_not_disclose"), 500, "service_failure"),
])
def test_job_errors_keep_shared_safe_problem_contract(system, error, status, code):
    system.error = error
    start, end = request(system)
    assert start["status"] == status and Problem.model_validate_json(end["body"]).code == code
    assert b"private_marker_do_not_disclose" not in end["body"]


def test_bad_clock_prevents_enqueue(system):
    system.ready = False
    assert request(system)[0]["status"] == 503 and not system.calls


def test_receipt_for_another_workspace_is_never_returned(system):
    system.receipt = system.receipt.model_copy(update={
        "workspace_id": "ws_foreign", "status_url": "/v1/workspaces/ws_foreign/jobs/job1"})
    start, end = request(system)
    assert start["status"] == 500 and b"ws_foreign" not in end["body"]


def test_unrelated_route_falls_through_without_auth_or_body_work(system):
    assert request(system, method="GET", path="/other")[0]["status"] == 204
    assert system.fallback == ["/other"] and not system.calls and not system.reads

