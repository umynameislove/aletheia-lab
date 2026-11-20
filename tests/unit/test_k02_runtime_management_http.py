"""Metadata/policy routes use real session/CSRF guards and synthetic storage."""
from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import SecretStr

from aletheia_lab.hosted.action_audit import current_request_id
from aletheia_lab.hosted.authorization import AuthorizationDenied
from aletheia_lab.hosted.read_backend import SessionRecord
from aletheia_lab.hosted.request_boundary import BodyLimits
from aletheia_lab.hosted.runtime_management import PolicyRejected
from aletheia_lab.hosted.runtime_management_http import RuntimeManagementApplication
from aletheia_lab.hosted.settings import RuntimeSettings
from aletheia_lab.hosted.workspace_management import WorkspaceConflict, WorkspaceDenied
from aletheia_lab.hosted_auth.csrf import CsrfTokens
from aletheia_lab.hosted_auth.sessions import credential_digest
from aletheia_lab.hosted_contract.runtime_management import ProjectDetails, WorkspaceSettings
from aletheia_lab.hosted_contract.session_capabilities import Actor

VERSION = "product-api/v1-draft"
PROJECT = "p3-project-" + "a" * 64
BASE = "/v1/workspaces/ws1"
DETAIL = BASE + "/projects/" + PROJECT
PRIVATE = "private_marker_do_not_disclose"


class Management:
    def __init__(self, calls, *, error, foreign):
        self.calls, self.error, self.foreign = calls, error, foreign

    async def call(self, name, **kwargs):
        self.calls.append((name, kwargs, current_request_id()))
        if self.error is not None:
            raise self.error
        workspace = "other" if self.foreign else kwargs["workspace_id"]
        if "project" in name:
            return ProjectDetails(schema_version=VERSION, workspace_id=workspace, project_id=PROJECT,
                revision=4 if name.startswith("update") else 3, display_name="Synthetic project", state="active",
                current_snapshot_id="upload-snapshot-" + "b" * 64, current_result_id=None)
        return WorkspaceSettings(schema_version=VERSION, workspace_id=workspace,
            revision=4 if name.startswith("update") else 0, provenance="platform_default")

    async def project(self, **kwargs): return await self.call("project", **kwargs)
    async def update_project(self, **kwargs): return await self.call("update_project", **kwargs)
    async def settings(self, **kwargs): return await self.call("settings", **kwargs)
    async def update_settings(self, **kwargs): return await self.call("update_settings", **kwargs)


def scenario(tmp_path, *, path=DETAIL, method="GET", body=None, extra=(), remove=(), error=None,
             foreign=False, authenticated=True, clock=True, raw=None):
    async def run():
        calls, reads, messages = [], [], []
        tokens = CsrfTokens(SecretStr("synthetic-signing-key-" + "s" * 43))
        proof = tokens.issue("ctx1")
        record = SessionRecord(Actor(user_id="u1", display_name="Synthetic", email="u1@example.invalid",
            email_verified=True), "ctx1", datetime.now(UTC) + timedelta(minutes=5), proof, "ready", "ws1", "active", True)

        class Sessions:
            async def resolve_cookie(self, cookie):
                return record if authenticated else None

        async def fallback(scope, receive, send):
            messages.extend([{"type": "http.response.start", "status": 204, "headers": []},
                             {"type": "http.response.body", "body": b""}])

        async def ready(): return clock

        runtime = RuntimeSettings.model_validate({"environment": "local", "public_origin": "https://app.example.invalid",
            "identity": {"issuer": "https://id.example.invalid", "client_id": "synthetic", "client_kind": "public",
                         "redirect_uri": "https://app.example.invalid/callback"},
            "storage": {"database_url_env": "ALETHEIA_HOSTED_DATABASE_URL", "object_root": str(tmp_path)}})
        app = RuntimeManagementApplication(fallback, Management(calls, error=error, foreign=foreign), Sessions(), tokens, runtime,
            body_limits=BodyLimits(12000, 1, 2, 10), clock_check=ready)
        payload = raw if raw is not None else json.dumps({} if body is None else body).encode()
        headers = [(b"host", b"app.example.invalid"), (b"origin", b"https://app.example.invalid"),
            (b"content-type", b"application/json"), (b"content-length", str(len(payload)).encode()),
            (b"cookie", f"__Host-aletheia_session={'a' * 43}; __Host-aletheia_csrf={proof.get_secret_value()}".encode()),
            (b"x-csrf-token", proof.get_secret_value().encode()), (b"if-match", b'"3"')]
        headers = [(name, value) for name, value in headers if name not in remove] + list(extra)

        async def receive():
            reads.append(True)
            return {"type": "http.request", "body": payload, "more_body": False}

        async def send(message): messages.append(message)

        await app({"type": "http", "scheme": "https", "method": method, "path": path,
                   "headers": headers, "query_string": b""}, receive, send)
        assert current_request_id() is None
        return messages, calls, reads
    return asyncio.run(run())


@pytest.mark.parametrize("path,method,body,operation", [
    (DETAIL, "GET", None, "project"), (DETAIL, "HEAD", None, "project"),
    (DETAIL, "PATCH", {"schema_version": VERSION, "display_name": "New project"}, "update_project"),
    (BASE + "/settings", "GET", None, "settings"), (BASE + "/settings", "HEAD", None, "settings"),
    (BASE + "/settings", "PATCH", {"schema_version": VERSION, "maximum_upload_bytes": 1024}, "update_settings"),
])
def test_routes_copy_only_current_authority_and_exact_resource(tmp_path, path, method, body, operation):
    (start, end), calls, reads = scenario(tmp_path, path=path, method=method, body=body)
    assert start["status"] == 200 and len(calls) == 1 and calls[0][0] == operation
    headers = dict(start["headers"])
    assert headers[b"cache-control"] == b"no-store" and headers[b"etag"] in (b'"0"', b'"1"', b'"3"', b'"4"')
    assert calls[0][1]["credential_digest"] == credential_digest(SecretStr("a" * 43))
    assert calls[0][1]["workspace_id"] == "ws1" and calls[0][2] == headers[b"x-request-id"].decode()
    if method == "PATCH":
        assert calls[0][1]["expected_revision"] == 3 and reads
    else:
        assert not reads
    assert bool(end["body"]) == (method != "HEAD")
    if method != "HEAD":
        assert json.loads(end["body"])["workspace_id"] == "ws1"


@pytest.mark.parametrize("path,body", [
    (DETAIL, {"display_name": "secret=" + PRIVATE}), (DETAIL, {"display_name": "C:/private"}),
    (DETAIL, {"display_name": "x\nprivate"}), (DETAIL, {"display_name": "x" * 161}),
    (DETAIL, {"display_name": "valid", "project_id": PRIVATE}),
    (BASE + "/settings", {}), (BASE + "/settings", {"maximum_upload_bytes": True}),
    (BASE + "/settings", {"maximum_upload_bytes": None}),
    (BASE + "/settings", {"maximum_upload_bytes": 200 * 1024 * 1024 + 1}),
    (BASE + "/settings", {"maximum_workspace_bytes": -1}),
    (BASE + "/settings", {"retention_days": 0}), (BASE + "/settings", {"source_policy": PRIVATE}),
])
def test_untrusted_metadata_policy_or_identity_never_reaches_storage(tmp_path, path, body):
    (start, end), calls, _ = scenario(tmp_path, path=path, method="PATCH", body={"schema_version": VERSION, **body})
    assert start["status"] == 422 and not calls and PRIVATE.encode() not in end["body"]


@pytest.mark.parametrize("remove,extra,status", [
    ((b"if-match",), (), 428), ((), ((b"if-match", b'"3"'),), 403),
    ((b"if-match",), ((b"if-match", b'W/"3"'),), 400),
    ((b"x-csrf-token",), (), 403), ((b"origin",), ((b"origin", b"https://foreign.invalid"),), 403),
])
def test_precondition_and_csrf_fail_before_body_or_effect(tmp_path, remove, extra, status):
    (start, _), calls, reads = scenario(tmp_path, method="PATCH", body={"schema_version": VERSION,
        "display_name": "New project"}, remove=remove, extra=extra)
    assert start["status"] == status and not calls and not reads


@pytest.mark.parametrize("error,status,code", [
    (AuthorizationDenied(), 404, "not_found"), (WorkspaceDenied(), 403, "permission_denied"),
    (WorkspaceConflict(), 412, "revision_conflict"), (PolicyRejected(), 422, "schema_invalid"),
    (TimeoutError(PRIVATE), 503, "service_unavailable"), (RuntimeError(PRIVATE), 500, "service_failure"),
])
def test_safe_repository_errors_do_not_leak_values(tmp_path, error, status, code):
    (start, end), _, _ = scenario(tmp_path, error=error)
    assert start["status"] == status and json.loads(end["body"])["code"] == code
    assert PRIVATE.encode() not in end["body"]


@pytest.mark.parametrize("options,status", [({"foreign": True}, 500), ({"authenticated": False}, 401), ({"clock": False}, 503)])
def test_foreign_output_or_unavailable_identity_never_returns_metadata(tmp_path, options, status):
    (start, end), _, _ = scenario(tmp_path, **options)
    assert start["status"] == status and PROJECT.encode() not in end["body"]


@pytest.mark.parametrize("path,body", [(DETAIL, {"display_name": "New project"}),
    (BASE + "/settings", {"maximum_upload_bytes": 1024})])
def test_mutation_response_must_reference_the_expected_successor_revision(tmp_path, monkeypatch, path, body):
    original = Management.call

    async def corrupt(self, name, **kwargs):
        value = await original(self, name, **kwargs)
        return value.model_copy(update={"revision": 999})

    monkeypatch.setattr(Management, "call", corrupt)
    (start, end), _, _ = scenario(tmp_path, path=path, method="PATCH", body={"schema_version": VERSION, **body})
    assert start["status"] == 500 and b'"revision":999' not in end["body"]


@pytest.mark.parametrize("path,method", [(DETAIL, "DELETE"), (DETAIL + "/records", "GET"), ("/unrelated", "GET")])
def test_delete_and_existing_record_routes_fall_through(tmp_path, path, method):
    (start, _), calls, reads = scenario(tmp_path, path=path, method=method)
    assert start["status"] == 204 and not calls and not reads
