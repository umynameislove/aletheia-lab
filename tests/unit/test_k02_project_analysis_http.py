"""Project analysis binds an immutable snapshot to the route's current grant."""
from __future__ import annotations

import json

import pytest
from jsonschema import Draft202012Validator
from pydantic import SecretStr
from test_k02_job_boundary import PRIVATE, VERSION, _request, _system

from aletheia_lab.hosted.authorization import AuthorizationDenied
from aletheia_lab.hosted.postgres_sources import SourceNotFound
from aletheia_lab.hosted.project_analysis_http import ProjectAnalysisApplication
from aletheia_lab.hosted.request_boundary import BodyLimits
from aletheia_lab.hosted_auth.csrf import CsrfTokens
from aletheia_lab.hosted_contract.project_analysis import AnalysisRequest

ROUTE = "/v1/workspaces/ws_boundary/projects/p3-project-" + "a" * 64 + "/analyses"
BODY = {"schema_version": VERSION, "snapshot_id": "upload-snapshot-" + "b" * 64,
        "question": "What does this immutable snapshot show?", "backend": "local_evidence"}


def _case(tmp_path, *, error=None):
    app, jobs, headers = _system(tmp_path)
    calls = []

    async def enqueue_analysis(**values):
        calls.append(values)
        if error is not None:
            raise error
        return jobs.receipt

    jobs.enqueue_analysis = enqueue_analysis
    tokens = CsrfTokens(SecretStr("synthetic-signing-key-" + "s" * 43))
    analysis = ProjectAnalysisApplication(app._fallback, jobs, app._sessions, tokens, app._runtime,
                                         body_limits=app._limits, clock_check=app._clock)
    return analysis, headers, calls


@pytest.mark.parametrize("explicit_backend", [False, True])
def test_analysis_dispatches_only_cookie_actor_route_project_snapshot_and_question(tmp_path, explicit_backend):
    app, headers, calls = _case(tmp_path)
    body = BODY if explicit_backend else {key: value for key, value in BODY.items() if key != "backend"}
    status, response_headers, raw = _request(app, headers, method="POST", path=ROUTE, body=json.dumps(body).encode())
    assert status == 202 and len(calls) == 1
    assert set(calls[0]) == {"credential_digest", "workspace_id", "project_id", "snapshot_id", "question", "request_key"}
    assert calls[0]["project_id"] == "p3-project-" + "a" * 64 and calls[0]["workspace_id"] == "ws_boundary"
    assert calls[0]["snapshot_id"] == BODY["snapshot_id"] and calls[0]["question"] == BODY["question"]
    assert json.loads(raw)["status"] == "queued" and response_headers[b"cache-control"] == b"no-store"


@pytest.mark.parametrize("patch", [{"schema_version": "wrong"}, {"snapshot_id": "../../private"},
    {"project_id": "untrusted"}, {"workspace_id": "other"}, {"backend": "provider"},
    {"question": "x" * 4001}, {"question": "password=private_marker_do_not_disclose"}])
def test_analysis_rejects_untrusted_scope_backend_or_invalid_question_before_repository(tmp_path, patch):
    app, headers, calls = _case(tmp_path)
    observed, _, raw = _request(app, headers, method="POST", path=ROUTE, body=json.dumps({**BODY, **patch}).encode())
    assert observed == 422 and not calls and PRIVATE.encode() not in raw


@pytest.mark.parametrize("length,status", [(4000, 202), (4001, 422)])
def test_analysis_http_and_independent_schema_keep_full_original_question_limit(tmp_path, length, status):
    app, headers, calls = _case(tmp_path)
    app._limits = BodyLimits(30000, 1, 3, 10)
    body = {**BODY, "question": "é" * length}
    assert Draft202012Validator(AnalysisRequest.model_json_schema()).is_valid(body) is (status == 202)
    observed, _, _ = _request(app, headers, method="POST", path=ROUTE, body=json.dumps(body).encode())
    assert observed == status
    if status == 202:
        assert calls[0]["question"] == body["question"]
    else:
        assert not calls


@pytest.mark.parametrize("error,status", [(AuthorizationDenied(), 404), (SourceNotFound(), 404),
                                        (RuntimeError(PRIVATE), 500)])
def test_analysis_current_grant_or_parent_mismatch_is_safe_failure(tmp_path, error, status):
    app, headers, _ = _case(tmp_path, error=error)
    observed, _, raw = _request(app, headers, method="POST", path=ROUTE, body=json.dumps(BODY).encode())
    assert observed == status and PRIVATE.encode() not in raw


@pytest.mark.parametrize("header,status", [(b"x-csrf-token", 403), (b"origin", 403), (b"idempotency-key", 400)])
def test_analysis_requires_origin_csrf_and_idempotency_before_effect(tmp_path, header, status):
    app, headers, calls = _case(tmp_path)
    observed, _, _ = _request(app, [(key, value) for key, value in headers if key != header],
                              method="POST", path=ROUTE, body=json.dumps(BODY).encode())
    assert observed == status and not calls
