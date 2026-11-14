"""Follow-up outputs and action correlation use the existing guarded HTTP boundary."""

from __future__ import annotations

import json
import logging

import pytest
from test_k02_job_boundary import PATH, PRIVATE, _request, _system
from test_k02_project_operations import BODY as FOLLOW_UP_BODY
from test_k02_project_operations import request as project_request
from test_k02_project_operations import system as system

from aletheia_lab.hosted.action_audit import current_request_id
from aletheia_lab.hosted.authorization import AuthorizationDenied

CHILD = "p6-result-" + "a" * 64
PARENT = "p6-result-" + "b" * 64
PROJECT = "p3-project-" + "c" * 64
OUTPUT = PATH + "/job_boundary/output"
PREVIEW = b'{"operation":"preview","source_id":"src1"}'


def _follow_up(jobs, prefix="p3-snapshot"):
    jobs.result.update(operation="follow_up", result={
        "project_id": PROJECT, "result_id": CHILD, "parent_result_id": PARENT,
        "snapshot_id": prefix + "-" + "d" * 64, "revision": 0,
    })


@pytest.mark.parametrize("prefix", ["p3-snapshot", "upload-snapshot"])
def test_follow_up_output_returns_only_validated_canonical_references(tmp_path, prefix):
    app, jobs, headers = _system(tmp_path)
    _follow_up(jobs, prefix)
    status, response_headers, body = _request(app, headers, path=OUTPUT)
    assert status == 200 and json.loads(body) == jobs.result
    assert response_headers[b"cache-control"] == b"no-store"
    assert int(response_headers[b"content-length"]) == len(body)
    assert PRIVATE.encode() not in body


@pytest.mark.parametrize("field,value", [
    ("private_metadata", PRIVATE), ("parent_result_id", CHILD),
    ("project_id", PRIVATE), ("result_id", PRIVATE), ("parent_result_id", PRIVATE),
    ("snapshot_id", "p6-result-" + "d" * 64), ("snapshot_id", PRIVATE),
    ("revision", -1), ("revision", True), ("revision", 1.0), ("revision", "1"),
])
def test_invalid_follow_up_metadata_is_safe_failure(tmp_path, field, value):
    app, jobs, headers = _system(tmp_path)
    _follow_up(jobs)
    jobs.result["result"][field] = value
    status, response_headers, body = _request(app, headers, path=OUTPUT)
    assert status == 500 and json.loads(body)["code"] == "service_failure"
    assert PRIVATE.encode() not in body and CHILD.encode() not in body
    assert response_headers[b"cache-control"] == b"no-store"


def test_follow_up_output_current_authorization_denial_is_safe_not_found(tmp_path):
    app, jobs, headers = _system(tmp_path)
    _follow_up(jobs)

    async def denied(**kwargs):
        raise AuthorizationDenied()

    jobs.output = denied
    status, response_headers, body = _request(app, headers, path=OUTPUT)
    assert status == 404 and json.loads(body)["code"] == "not_found"
    assert CHILD.encode() not in body and PRIVATE.encode() not in body
    assert response_headers[b"cache-control"] == b"no-store"


@pytest.mark.parametrize("route", ["job", "project"])
def test_enqueue_audit_context_matches_http_request_and_is_reset(tmp_path, system, route):
    seen = []
    if route == "job":
        app, jobs, headers = _system(tmp_path)
        original = jobs.enqueue
    else:
        jobs = system.app._jobs
        original = jobs.enqueue_follow_up

    async def enqueue(**kwargs):
        seen.append(current_request_id())
        return await original(**kwargs)

    assert current_request_id() is None
    if route == "job":
        jobs.enqueue = enqueue
        status, response_headers, _ = _request(app, headers, method="POST", path=PATH, body=PREVIEW)
    else:
        jobs.enqueue_follow_up = enqueue
        start, _ = project_request(system)
        status, response_headers = start["status"], dict(start["headers"])
    assert status == 202 and seen == [response_headers[b"x-request-id"].decode()]
    assert seen[0].startswith("req_") and current_request_id() is None


@pytest.mark.parametrize("route", ["job", "project"])
def test_schema_rejection_logs_fixed_correlated_outcome_without_private_input(tmp_path, system, caplog, route):
    with caplog.at_level(logging.INFO, logger="aletheia.action"):
        if route == "job":
            app, jobs, headers = _system(tmp_path)
            payload = json.dumps({"operation": "preview", "source_id": "src1", "private": PRIVATE}).encode()
            status, response_headers, body = _request(app, headers, method="POST", path=PATH, body=payload)
            assert jobs.enqueue_calls == 0
        else:
            start, end = project_request(system, body={**FOLLOW_UP_BODY, "private": PRIVATE})
            status, response_headers, body = start["status"], dict(start["headers"]), end["body"]
            assert not system.calls
    assert status == 422 and PRIVATE.encode() not in body
    records = [record for record in caplog.records if record.name == "aletheia.action"]
    assert len(records) == 1
    record = records[0]
    assert record.getMessage() == "action_rejected" and record.action_outcome == "rejected"
    assert record.request_id == response_headers[b"x-request-id"].decode()
    assert record.args == () and record.exc_info is None and PRIVATE not in caplog.text
    assert current_request_id() is None
