"""K-04 HTTP trust boundary using real CSRF and synthetic management facts."""
import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import SecretStr

from aletheia_lab.hosted.authorization import AuthorizationDenied
from aletheia_lab.hosted.postgres_sources import SourceNotFound
from aletheia_lab.hosted.read_backend import SessionRecord
from aletheia_lab.hosted.request_boundary import BodyLimits
from aletheia_lab.hosted.settings import RuntimeSettings
from aletheia_lab.hosted.workspace_http import WorkspaceApplication
from aletheia_lab.hosted.workspace_management import WorkspaceConflict, WorkspaceDenied
from aletheia_lab.hosted_auth.csrf import CsrfTokens
from aletheia_lab.hosted_auth.sessions import credential_digest
from aletheia_lab.hosted_contract.session_capabilities import Actor, Problem
from aletheia_lab.hosted_contract.workspaces import (
    Invitation,
    Member,
    MemberPage,
    RevokedInvitation,
    SourceGrant,
    Workspace,
)

VERSION = "product-api/v1-draft"
TOKEN = "synthetic-invitation-" + "i" * 43
BASE = "/v1/workspaces/ws1"
WORKSPACE = {"schema_version": VERSION, "workspace_id": "ws1", "workspace_name": "Synthetic",
             "membership": {"role": "owner", "revision": 1}}
MEMBER = {"schema_version": VERSION, "workspace_id": "ws1", "user_id": "u2",
          "role": "analyst", "active": True, "revision": 2}
GRANT = {"schema_version": VERSION, "workspace_id": "ws1", "source_id": "src1",
         "user_id": "u2", "actions": ["read"], "revision": 2}


def scenario(tmp_path, *, method="POST", path="/v1/workspaces", body=None, raw=None,
             revision=None, valid_csrf=True, origin=b"https://app.example.invalid",
             authenticated=True, query=b"", error=None, output=None, omit_media=False,
             body_events=None, declared_length=None, extra_headers=()):
    async def run():
        tokens = CsrfTokens(SecretStr("synthetic-key-" + "s" * 43))
        csrf = tokens.issue("ctx1")
        record = SessionRecord(
            Actor(user_id="u1", display_name="Synthetic", email="u1@example.invalid", email_verified=True),
            "ctx1", datetime.now(UTC) + timedelta(minutes=5), csrf, "ready", "ws1", "active", True,
        )
        calls, reads, messages = [], [], []

        class Sessions:
            async def is_ready(self):
                return True

            async def resolve_cookie(self, cookie):
                return record if authenticated else None

        class Management:
            async def call(self, operation, values, response):
                calls.append((operation, values))
                if error is not None:
                    raise error
                return response if output is None else output

            async def list_workspaces(self, **values):
                return await self.call("list_workspaces", values, {"schema_version": VERSION, "items": [WORKSPACE],
                    "next_cursor": None, "total": None, "total_kind": "unknown"})

            async def read_workspace(self, **values):
                return await self.call("read_workspace", values, WORKSPACE)

            async def list_invitations(self, **values):
                return await self.call("list_invitations", values, {"schema_version": VERSION, "workspace_id": "ws1",
                    "items": [{"schema_version": VERSION, "workspace_id": "ws1", "invitation_id": "inv1",
                        "user_id": "u2", "display_name": "Second", "role": "viewer", "expires_at": datetime.now(UTC) + timedelta(hours=1)}],
                    "next_cursor": None, "total": None, "total_kind": "unknown"})

            async def lookup_invitation_targets(self, **values):
                return await self.call("lookup_invitation_targets", values, {"schema_version": VERSION, "workspace_id": "ws1",
                    "items": [{"user_id": "u2", "display_name": "Second"}], "next_cursor": None, "total": None, "total_kind": "unknown"})

            async def read_source_grant(self, **values):
                return await self.call("read_source_grant", values, GRANT)

            async def create(self, **values):
                return await self.call("create", values, WORKSPACE)

            async def list_members(self, **values):
                return await self.call("list_members", values,
                    {"schema_version": VERSION, "workspace_id": "ws1", "items": [MEMBER]})

            async def mutate_member(self, **values):
                return await self.call("mutate_member", values, MEMBER)

            async def transfer_owner(self, **values):
                return await self.call("transfer_owner", values, {**MEMBER, "role": "owner"})

            async def invite(self, **values):
                return await self.call("invite", values, {
                    "schema_version": VERSION, "workspace_id": "ws1", "invitation_id": "inv1",
                    "user_id": "u2", "role": "analyst", "expires_at": datetime.now(UTC) + timedelta(hours=1),
                    "token": TOKEN,
                })

            async def revoke_invite(self, **values):
                return await self.call("revoke_invite", values, {
                    "schema_version": VERSION, "workspace_id": "ws1", "invitation_id": "inv1", "revoked": True,
                })

            async def accept_invite(self, **values):
                return await self.call("accept_invite", values, WORKSPACE)

            async def set_source_grant(self, **values):
                return await self.call("set_source_grant", values, GRANT)

        runtime = RuntimeSettings.model_validate({
            "environment": "staging", "public_origin": "https://app.example.invalid",
            "identity": {"issuer": "https://id.example.invalid", "client_id": "test",
                         "redirect_uri": "https://app.example.invalid/v1/auth/callback", "client_kind": "public"},
            "storage": {"database_url_env": "ALETHEIA_HOSTED_DATABASE_URL", "object_root": str(tmp_path)},
        })

        async def clock():
            return True

        async def fallback(scope, receive, send):
            raise AssertionError("unexpected fallback")

        app = WorkspaceApplication(fallback, Management(), Sessions(), tokens, runtime,
            body_limits=BodyLimits(4096, 1, 3, 10), clock_check=clock)
        payload = raw if raw is not None else json.dumps({"name": "Synthetic"} if body is None else body).encode()
        headers = [
            (b"host", b"app.example.invalid"), (b"origin", origin),
            (b"content-type", b"application/json"), (b"content-length", str(len(payload)).encode()),
            (b"cookie", f'__Host-aletheia_session={"a" * 43}; __Host-aletheia_csrf={csrf.get_secret_value()}'.encode()),
            (b"x-csrf-token", csrf.get_secret_value().encode() if valid_csrf else b"wrong"),
        ]
        if omit_media:
            headers = [(name, value) for name, value in headers if name not in (b"content-type", b"content-length")]
        if declared_length is not None:
            headers = [(name, value) for name, value in headers if name != b"content-length"]
            headers.append((b"content-length", declared_length))
        headers.extend(extra_headers)
        event_source = iter(body_events) if body_events is not None else None
        if revision is not None:
            headers.append((b"if-match", revision))

        async def receive():
            reads.append(True)
            return (next(event_source) if event_source is not None else
                    {"type": "http.request", "body": payload, "more_body": False})

        async def send(message):
            messages.append(message)

        await app({"type": "http", "method": method, "scheme": "https", "path": path,
                   "headers": headers, "query_string": query}, receive, send)
        return messages, calls, reads

    return asyncio.run(run())


@pytest.mark.parametrize(("operation", "method", "path", "body", "revision", "status", "model"), [
    ("create", "POST", "/v1/workspaces", {"name": "Synthetic"}, None, 201, Workspace),
    ("list_members", "GET", BASE + "/members", {}, None, 200, MemberPage),
    ("mutate_member", "PATCH", BASE + "/members/u2", {"role": "reviewer"}, b'"1"', 200, Member),
    ("transfer_owner", "POST", BASE + "/transfer-owner", {"user_id": "u2"}, b'"1"', 200, Member),
    ("invite", "POST", BASE + "/invitations", {"user_id": "u2", "role": "analyst"}, None, 201, Invitation),
    ("revoke_invite", "DELETE", BASE + "/invitations/inv1", {}, None, 200, RevokedInvitation),
    ("accept_invite", "POST", "/v1/invitations/accept", {"token": TOKEN}, None, 200, Workspace),
    ("set_source_grant", "PUT", BASE + "/sources/src1/grants/u2", {"actions": ["read"]}, b'"1"', 200, SourceGrant),
])
def test_routes_use_cookie_digest_and_validate_producer_response(tmp_path, operation, method, path,
                                                               body, revision, status, model):
    messages, calls, reads = scenario(tmp_path, method=method, path=path, body=body, revision=revision)
    assert messages[0]["status"] == status
    value = model.model_validate_json(messages[1]["body"])
    if hasattr(value, "revision"):
        assert dict(messages[0]["headers"])[b"etag"] == ('"' + str(value.revision) + '"').encode()
    assert len(calls) == 1 and calls[0][0] == operation
    values = calls[0][1]
    assert values["credential_digest"] == credential_digest(SecretStr("a" * 43))
    assert "actor" not in values and "actor_id" not in values
    if operation not in ("mutate_member", "invite"):
        assert "role" not in values
    if operation not in ("create", "accept_invite"):
        assert values["workspace_id"] == "ws1"
    if revision is not None:
        assert values["expected_revision"] == 1
    assert len(reads) == (method != "GET")
    assert (b"cache-control", b"no-store") in messages[0]["headers"]
    assert (b"x-content-type-options", b"nosniff") in messages[0]["headers"]


@pytest.mark.parametrize("options", [
    {"valid_csrf": False}, {"origin": b"https://attacker.example.invalid"}, {"authenticated": False},
])
def test_guard_rejects_before_body_and_management(tmp_path, options):
    messages, calls, reads = scenario(tmp_path, **options)
    assert messages[0]["status"] in (401, 403)
    Problem.model_validate_json(messages[1]["body"])
    assert not calls and not reads


@pytest.mark.parametrize("field,value", [("user_id", "u2"), ("role", "owner"), ("workspace_id", "ws2")])
def test_client_identity_fields_cannot_provision_authority(tmp_path, field, value):
    messages, calls, _ = scenario(tmp_path, body={"name": "Synthetic", field: value})
    assert messages[0]["status"] == 422 and not calls


@pytest.mark.parametrize("revision,status", [(None, 428), (b"1", 400), (b'W/"1"', 400),
                                          (b'"-1"', 400), (b'"9223372036854775807"', 400)])
def test_revision_precondition_blocks_effect_before_body(tmp_path, revision, status):
    messages, calls, reads = scenario(tmp_path, method="PATCH", path=BASE + "/members/u2",
                                    body={"active": False}, revision=revision)
    assert messages[0]["status"] == status and not calls and not reads


def test_stale_repository_revision_preserves_problem_contract(tmp_path):
    messages, calls, _ = scenario(tmp_path, method="PATCH", path=BASE + "/members/u2",
        body={"active": False}, revision=b'"1"', error=WorkspaceConflict("revision_conflict"))
    problem = Problem.model_validate_json(messages[1]["body"])
    assert messages[0]["status"] == problem.status == 412
    assert problem.code == "revision_conflict" and len(calls) == 1


def test_management_denial_is_safe_permission_problem(tmp_path):
    messages, calls, _ = scenario(tmp_path, method="GET", path=BASE + "/members", error=WorkspaceDenied())
    problem = Problem.model_validate_json(messages[1]["body"])
    assert messages[0]["status"] == 403 and problem.code == "permission_denied"
    assert len(calls) == 1


def test_outside_scope_and_missing_object_have_identical_public_problem(tmp_path):
    problems = []
    for path in (BASE + "/invitations/missing", "/v1/workspaces/ws2/invitations/private"):
        messages, _, _ = scenario(tmp_path, method="DELETE", path=path, body={}, error=SourceNotFound())
        value = Problem.model_validate_json(messages[1]["body"])
        assert messages[0]["status"] == value.status == 404 and value.code == "not_found"
        problems.append(value.model_dump(exclude={"request_id"}))
    assert problems[0] == problems[1]


def test_source_grant_authorization_denial_is_not_found(tmp_path):
    messages, calls, _ = scenario(tmp_path, method="PUT", path=BASE + "/sources/src1/grants/u2",
        body={"actions": ["read"]}, revision=b'"1"', error=AuthorizationDenied())
    problem = Problem.model_validate_json(messages[1]["body"])
    assert messages[0]["status"] == problem.status == 404 and problem.code == "not_found"
    assert len(calls) == 1


@pytest.mark.parametrize("actions", [["write"], ["read", "read"]])
def test_invalid_grant_actions_are_schema_errors_before_repository(tmp_path, actions):
    messages, calls, _ = scenario(tmp_path, method="PUT", path=BASE + "/sources/src1/grants/u2",
                                 body={"actions": actions}, revision=b'"0"')
    problem = Problem.model_validate_json(messages[1]["body"])
    assert messages[0]["status"] == 422 and problem.code == "schema_invalid" and not calls


def test_repository_response_outside_workspace_is_not_returned(tmp_path):
    messages, _, _ = scenario(tmp_path, method="GET", path=BASE + "/members",
        output={"schema_version": VERSION, "workspace_id": "ws2", "items": []})
    assert messages[0]["status"] == 500
    problem = Problem.model_validate_json(messages[1]["body"])
    assert problem.code == "service_failure" and "ws2" not in problem.detail


@pytest.mark.parametrize("method,path,body,revision,output", [
    ("PATCH", BASE + "/members/u2", {"role": "reviewer"}, b'"1"', {**MEMBER, "user_id": "u3"}),
    ("POST", BASE + "/transfer-owner", {"user_id": "u2"}, b'"1"', {**MEMBER, "user_id": "u3", "role": "owner"}),
    ("POST", BASE + "/invitations", {"user_id": "u2", "role": "analyst"}, None, {
        "schema_version": VERSION, "workspace_id": "ws1", "invitation_id": "inv1", "user_id": "u3",
        "role": "analyst", "expires_at": datetime(2026, 11, 11, tzinfo=UTC), "token": TOKEN,
    }),
    ("DELETE", BASE + "/invitations/inv1", {}, None, {
        "schema_version": VERSION, "workspace_id": "ws1", "invitation_id": "inv2", "revoked": True,
    }),
    ("PUT", BASE + "/sources/src1/grants/u2", {"actions": ["read"]}, b'"1"', {**GRANT, "source_id": "src2"}),
    ("PUT", BASE + "/sources/src1/grants/u2", {"actions": ["read"]}, b'"1"', {**GRANT, "user_id": "u3"}),
])
def test_repository_response_must_match_exact_requested_object(tmp_path, method, path, body, revision, output):
    messages, _, _ = scenario(tmp_path, method=method, path=path, body=body, revision=revision, output=output)
    problem = Problem.model_validate_json(messages[1]["body"])
    assert messages[0]["status"] == problem.status == 500 and problem.code == "service_failure"


@pytest.mark.parametrize("path,allow", [("/v1/workspaces", b"GET, HEAD, POST"), (BASE + "/members", b"GET, HEAD"),
    (BASE + "/members/u2", b"PATCH"), (BASE + "/invitations/inv1", b"DELETE"),
    (BASE + "/sources/src1/grants/u2", b"GET, HEAD, PUT")])
def test_method_rejection_lists_allowed_method_without_effect(tmp_path, path, allow):
    messages, calls, reads = scenario(tmp_path, method="OPTIONS", path=path)
    assert messages[0]["status"] == 405 and (b"allow", allow) in messages[0]["headers"]
    assert not calls and not reads


def test_head_member_list_retains_headers_without_body(tmp_path):
    messages, calls, reads = scenario(tmp_path, method="HEAD", path=BASE + "/members")
    assert messages[0]["status"] == 200 and messages[1]["body"] == b""
    assert int(dict(messages[0]["headers"])[b"content-length"]) > 0
    assert len(calls) == 1 and not reads


@pytest.mark.parametrize("query", [b"workspace_id=ws2", b"user_id=u2&role=owner"])
def test_foreign_cursor_or_scope_query_is_rejected_before_repository(tmp_path, query):
    messages, calls, reads = scenario(tmp_path, method="GET", path=BASE + "/members", query=query)
    assert messages[0]["status"] == 400 and not calls and not reads


@pytest.mark.parametrize("raw,status", [(b'{"name":"one","name":"two"}', 400),
    (b'{"name":"Synthetic","role":"owner"}', 422), (b"[]", 400)])
def test_strict_request_parser_rejects_ambiguous_or_unauthorized_shape(tmp_path, raw, status):
    messages, calls, _ = scenario(tmp_path, raw=raw)
    assert messages[0]["status"] == status and not calls
