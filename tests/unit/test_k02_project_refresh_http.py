"""Refresh HTTP scope, current grant, revision and CSRF checks precede effects."""
from __future__ import annotations

import json

import pytest
from pydantic import SecretStr
from test_k02_job_boundary import PATH, PRIVATE, VERSION, _request, _system

from aletheia_lab.hosted.authorization import AuthorizationDenied
from aletheia_lab.hosted.project_refresh_http import ProjectRefreshApplication
from aletheia_lab.hosted.workspace_management import WorkspaceConflict
from aletheia_lab.hosted_auth.csrf import CsrfTokens

ROUTE = "/v1/workspaces/ws_boundary/projects/p3-project-" + "a" * 64 + "/refresh"
BODY = {"schema_version": VERSION, "source_id": "src_new"}


def _case(tmp_path, *, error=None):
    app, jobs, headers = _system(tmp_path)
    calls = []

    async def enqueue_refresh(**values):
        calls.append(values)
        if error is not None:
            raise error
        return jobs.receipt

    jobs.enqueue_refresh = enqueue_refresh
    tokens = CsrfTokens(SecretStr("synthetic-signing-key-" + "s" * 43))
    refresh = ProjectRefreshApplication(app._fallback, jobs, app._sessions, tokens, app._runtime,
                                       body_limits=app._limits, clock_check=app._clock)
    return refresh, headers + [(b"if-match", b'"3"')], calls


def test_refresh_dispatches_exact_cookie_actor_project_source_revision_and_receipt(tmp_path):
    app, headers, calls = _case(tmp_path)
    status, response_headers, body = _request(app, headers, method="POST", path=ROUTE, body=json.dumps(BODY).encode())
    assert status == 202 and len(calls) == 1
    assert set(calls[0]) == {"credential_digest", "workspace_id", "project_id", "expected_revision", "request_key", "source_id"}
    assert calls[0]["project_id"] == "p3-project-" + "a" * 64 and calls[0]["expected_revision"] == 3
    assert calls[0]["source_id"] == "src_new" and calls[0]["workspace_id"] == "ws_boundary"
    assert json.loads(body)["status_url"] == PATH + "/job_boundary" and response_headers[b"cache-control"] == b"no-store"


@pytest.mark.parametrize("patch", [{"schema_version": "wrong"}, {"source_id": "../../private"},
    {"parent_snapshot_id": "untrusted"}, {"origin_source_sha256": "a" * 64}, {"role": "owner"}])
def test_refresh_request_rejects_client_authority_parent_or_identity_without_effect(tmp_path, patch):
    app, headers, calls = _case(tmp_path)
    status, _, body = _request(app, headers, method="POST", path=ROUTE, body=json.dumps({**BODY, **patch}).encode())
    assert status == 422 and not calls and PRIVATE.encode() not in body


@pytest.mark.parametrize("header,status", [(b"if-match", 428), (b"x-csrf-token", 403), (b"origin", 403),
                                         (b"idempotency-key", 400), (b"cookie", 403)])
def test_refresh_requires_current_credential_origin_csrf_revision_and_request_key(tmp_path, header, status):
    app, headers, calls = _case(tmp_path)
    observed, _, _ = _request(app, [(key, value) for key, value in headers if key != header],
                              method="POST", path=ROUTE, body=json.dumps(BODY).encode())
    assert observed == status and not calls


@pytest.mark.parametrize("error,status,code", [(AuthorizationDenied(), 404, "not_found"),
    (WorkspaceConflict(), 412, "revision_conflict"), (RuntimeError(PRIVATE), 500, "service_failure")])
def test_refresh_denial_conflict_and_private_failure_stay_safe(tmp_path, error, status, code):
    app, headers, _ = _case(tmp_path, error=error)
    observed, response_headers, body = _request(app, headers, method="POST", path=ROUTE, body=json.dumps(BODY).encode())
    assert observed == status and json.loads(body)["code"] == code
    assert PRIVATE.encode() not in body and response_headers[b"cache-control"] == b"no-store"


def test_refresh_rejects_expired_session_even_with_complete_csrf_cookie(tmp_path):
    app, headers, calls = _case(tmp_path)

    async def expired(cookie):
        return None

    app._sessions.resolve_cookie = expired
    observed, _, _ = _request(app, headers, method="POST", path=ROUTE, body=json.dumps(BODY).encode())
    assert observed == 401 and not calls
