"""Job control requests preserve the existing authenticated HTTP boundary."""
import json

import pytest
from test_k02_job_boundary import PATH, VERSION, _request, _system

from aletheia_lab.hosted.postgres_jobs import JobStateConflict
from aletheia_lab.hosted.postgres_sources import SourceConflict, SourceNotFound
from aletheia_lab.hosted_contract.job_page import JobReceipt


def _controls(tmp_path, *, error=None, result=None):
    app, jobs, headers = _system(tmp_path)
    calls = []

    async def cancel(**values):
        calls.append(("cancel", values))
        if error is not None:
            raise error
        return jobs.job.model_copy(update={"status": "cancelled", "progress": None}) if result is None else result

    async def retry(**values):
        calls.append(("retry", values))
        if error is not None:
            raise error
        return JobReceipt(schema_version=VERSION, workspace_id="ws_boundary", job_id="job_retry", status="queued",
            request_id="req_retry", status_url=PATH + "/job_retry") if result is None else result

    jobs.cancel, jobs.retry = cancel, retry
    return app, jobs, headers, calls


@pytest.mark.parametrize("action,status", [("cancel", 200), ("retry", 202)])
def test_control_actions_use_only_cookie_actor_and_requested_job(tmp_path, action, status):
    app, _, headers, calls = _controls(tmp_path)
    if action == "cancel":
        headers = [(key, value) for key, value in headers if key != b"idempotency-key"]
    observed, response_headers, body = _request(app, headers, method="POST", path=PATH + "/job_boundary/" + action, body=b"{}")
    assert observed == status and calls[0][0] == action
    assert calls[0][1]["workspace_id"] == "ws_boundary" and calls[0][1]["job_id"] == "job_boundary"
    assert not {"user_id", "role", "source_id", "request"} & set(calls[0][1])
    assert response_headers[b"cache-control"] == b"no-store"
    value = json.loads(body)
    assert value["job_id"] == ("job_boundary" if action == "cancel" else "job_retry")


@pytest.mark.parametrize("action", ["cancel", "retry"])
@pytest.mark.parametrize("body,status", [(b'{"role":"owner"}', 422), (b'{"source_id":"other"}', 422),
    (b'{"request":{}}', 422), (b"[]", 400), (b"", 400)])
def test_control_bodies_cannot_override_server_copied_authority_or_request(tmp_path, action, body, status):
    app, _, headers, calls = _controls(tmp_path)
    observed, _, _ = _request(app, headers, method="POST", path=PATH + "/job_boundary/" + action, body=body)
    assert observed == status and not calls


@pytest.mark.parametrize("action", ["cancel", "retry"])
@pytest.mark.parametrize("change", ["csrf", "origin"])
def test_control_csrf_or_origin_denial_has_no_repository_effect(tmp_path, action, change):
    app, _, headers, calls = _controls(tmp_path)
    headers = [(key, b"wrong" if key == b"x-csrf-token" and change == "csrf" else
                b"https://foreign.example.invalid" if key == b"origin" and change == "origin" else value)
               for key, value in headers]
    observed, _, _ = _request(app, headers, method="POST", path=PATH + "/job_boundary/" + action, body=b"{}")
    assert observed == 403 and not calls


@pytest.mark.parametrize("key", [None, b"short", b"invalid.request.key.value"])
def test_retry_requires_valid_idempotency_key_before_effect(tmp_path, key):
    app, _, headers, calls = _controls(tmp_path)
    headers = [(name, value) for name, value in headers if name != b"idempotency-key"]
    if key is not None:
        headers.append((b"idempotency-key", key))
    observed, _, _ = _request(app, headers, method="POST", path=PATH + "/job_boundary/retry", body=b"{}")
    assert observed == 400 and not calls


@pytest.mark.parametrize("action", ["cancel", "retry"])
def test_control_method_denial_has_exact_allow_header(tmp_path, action):
    app, _, headers, calls = _controls(tmp_path)
    observed, response_headers, _ = _request(app, headers, method="GET", path=PATH + "/job_boundary/" + action)
    assert observed == 405 and response_headers[b"allow"] == b"POST" and not calls


@pytest.mark.parametrize("error,status,code", [(SourceNotFound(), 404, "not_found"),
    (SourceConflict(), 409, "idempotency_conflict"), (JobStateConflict(), 409, "job_state_conflict")])
def test_control_errors_keep_existing_problem_envelope_without_job_metadata(tmp_path, error, status, code):
    app, _, headers, _ = _controls(tmp_path, error=error)
    observed, _, body = _request(app, headers, method="POST", path=PATH + "/job_boundary/retry", body=b"{}")
    value = json.loads(body)
    assert observed == status and value["code"] == code
    assert not {"job_id", "workspace_id", "creator_id", "request", "credential_digest"} & set(value)


def test_cancel_preserves_already_succeeded_terminal_truth(tmp_path):
    app, jobs, headers, _ = _controls(tmp_path)

    async def terminal(**values):
        return jobs.job

    jobs.cancel = terminal
    observed, _, body = _request(app, headers, method="POST", path=PATH + "/job_boundary/cancel", body=b"{}")
    assert observed == 200 and json.loads(body)["status"] == "succeeded"


def test_retry_cannot_return_original_job_as_new_receipt(tmp_path):
    app, jobs, headers, _ = _controls(tmp_path)

    async def wrong(**values):
        return jobs.receipt

    jobs.retry = wrong
    observed, _, body = _request(app, headers, method="POST", path=PATH + "/job_boundary/retry", body=b"{}")
    assert observed == 500 and json.loads(body)["code"] == "service_failure"
