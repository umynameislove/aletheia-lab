"""HTTP ASGI + real PostgreSQL K-04 journey; no IdP, browser or TLS claim."""
from __future__ import annotations

import io
import os
import time
import zipfile

import httpx
import pytest
from pydantic import SecretStr
from test_k04_workspaces_postgres import _database, _identity, _run

from aletheia_lab.hosted.application import AdapterLimits, HostedApplication
from aletheia_lab.hosted.job_http import JobApplication
from aletheia_lab.hosted.postgres_jobs import PostgresJobs
from aletheia_lab.hosted.postgres_sources import PostgresSources
from aletheia_lab.hosted.postgres_workspaces import PostgresWorkspaceAccess
from aletheia_lab.hosted.request_boundary import BodyLimits
from aletheia_lab.hosted.settings import RuntimeSettings
from aletheia_lab.hosted.source_http import SourceUploadApplication
from aletheia_lab.hosted.upload_runtime import UploadReadBackend
from aletheia_lab.hosted.workspace_http import WorkspaceApplication
from aletheia_lab.hosted.workspace_management import WorkspaceManagement
from aletheia_lab.hosted_auth.csrf import CsrfTokens
from aletheia_lab.hosted_auth.postgres_sessions import PostgresSessionStore
from aletheia_lab.hosted_auth.sessions import ServerSessions, credential_digest
from aletheia_lab.hosted_auth.settings import AuthPolicy
from aletheia_lab.hosted_contract.job_page import Job, JobReceipt
from aletheia_lab.hosted_contract.session_capabilities import Capabilities, Limits, Problem, Session
from aletheia_lab.hosted_contract.workspaces import (
    Invitation,
    Member,
    MemberPage,
    RevokedInvitation,
    SourceGrant,
    Workspace,
)

pytestmark = [pytest.mark.integration, pytest.mark.skipif(
    not os.environ.get("K04_TEST_DATABASE_URL"), reason="Explicit K-04 PostgreSQL test URL required")]
ORIGIN = "https://app.example.invalid"


async def _http_identity(pool, user, opaque, tokens):
    previous = await _identity(pool, user)
    digest = credential_digest(SecretStr(opaque))
    csrf = tokens.issue("ctx-" + user).get_secret_value()
    async with pool.connection() as conn:
        await conn.execute("UPDATE j01_session_credentials SET digest=%s WHERE digest=%s AND context_id=%s",
                           (digest.get_secret_value(), previous.get_secret_value(), "ctx-" + user))
        await conn.execute("UPDATE j01_sessions SET csrf_token=%s WHERE context_id=%s", (csrf, "ctx-" + user))
    return digest, {
        "Cookie": f"__Host-aletheia_session={opaque}; __Host-aletheia_csrf={csrf}",
        "X-CSRF-Token": csrf, "Origin": ORIGIN,
    }


def _body(response, status, model):
    assert response.status_code == status
    assert response.headers["cache-control"] == "no-store"
    return model.model_validate_json(response.content)


def _denied(response, status, code):
    value = _body(response, status, Problem)
    assert value.status == status and value.code == code
    return value.model_dump(exclude={"request_id"})


def test_http_onboarding_membership_parent_grant_and_revoke_with_real_postgres(tmp_path):
    async def run():
        async with _database() as (pool, _):
            tokens = CsrfTokens(SecretStr("synthetic-http-k04-" + "k" * 43))
            owner_digest, owner = await _http_identity(pool, "owner", "a" * 43, tokens)
            _, member = await _http_identity(pool, "member", "b" * 43, tokens)
            _, outsider = await _http_identity(pool, "outsider", "c" * 43, tokens)
            runtime = RuntimeSettings.model_validate({
                "environment": "staging", "public_origin": ORIGIN,
                "identity": {"issuer": "https://issuer.example.invalid", "client_id": "synthetic-k04",
                             "redirect_uri": ORIGIN + "/v1/auth/callback", "client_kind": "public"},
                "storage": {"database_url_env": "ALETHEIA_HOSTED_DATABASE_URL", "object_root": str(tmp_path)},
            })
            sessions = ServerSessions(PostgresSessionStore(pool),
                AuthPolicy(default_return_to="/", return_to_paths=("/",)),
                timeout_seconds=3, maximum_cookie_bytes=runtime.api.maximum_cookie_bytes)
            sources = PostgresSources(pool, maximum_upload_bytes=10000, maximum_expanded_bytes=20000,
                                      maximum_entries=10)
            jobs = PostgresJobs(pool, sources)
            access = PostgresWorkspaceAccess(pool, sessions)
            backend = UploadReadBackend(sessions, access, limits=Limits(
                question_chars=2000, upload_bundle_bytes=10000, page_max=200, job_poll_min_ms=1000))
            application = HostedApplication(backend, limits=AdapterLimits(
                runtime.api.backend_timeout_seconds, runtime.api.maximum_response_bytes, runtime.api.maximum_cookie_bytes))
            body_limits = BodyLimits(20000, 1, 3, 10)

            async def clock():
                return True

            application = SourceUploadApplication(application, sources, sessions, tokens, runtime,
                                                  body_limits=body_limits, clock_check=clock)
            application = JobApplication(application, jobs, sessions, tokens, runtime,
                                         body_limits=body_limits, clock_check=clock)
            application = WorkspaceApplication(application, WorkspaceManagement(pool), sessions, tokens, runtime,
                                               body_limits=body_limits, clock_check=clock)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url=ORIGIN) as client:
                before = _body(await client.get("/v1/session", headers=owner), 200, Session)
                assert before.onboarding == "needs_workspace" and before.last_workspace_id is None
                created = _body(await client.post("/v1/workspaces", json={"name": "Synthetic A"}, headers=owner),
                                201, Workspace)
                workspace = created.workspace_id
                base = "/v1/workspaces/" + workspace
                assert created.membership.role == "owner" and created.membership.revision == 1
                after = _body(await client.get("/v1/session", headers=owner), 200, Session)
                assert after.onboarding == "ready" and after.last_workspace_id == workspace
                _denied(await client.post("/v1/workspaces", json={"name": "Duplicate"}, headers=owner),
                        403, "permission_denied")
                caps = _body(await client.get(base + "/capabilities", headers=owner), 200, Capabilities)
                assert caps.membership.role == "owner" and {"import", "analyze"} <= set(caps.allowed_actions)
                _denied(await client.patch(base + "/members/owner", json={"active": False},
                    headers={**owner, "If-Match": '"1"'}), 403, "permission_denied")
                invitation = _body(await client.post(base + "/invitations",
                    json={"user_id": "member", "role": "admin", "expires_in_seconds": 600}, headers=owner),
                    201, Invitation)
                _denied(await client.post("/v1/invitations/accept", json={"token": invitation.token},
                                         headers=outsider), 403, "invitation_wrong_account")
                accepted = _body(await client.post("/v1/invitations/accept", json={"token": invitation.token},
                                                 headers=member), 200, Workspace)
                assert accepted.workspace_id == workspace and accepted.membership.role == "admin"
                members = _body(await client.get(base + "/members", headers=owner), 200, MemberPage)
                assert {value.user_id for value in members.items} == {"owner", "member"}
                _denied(await client.get(base + "/members", headers=outsider), 403, "permission_denied")
                _denied(await client.patch(base + "/members/member", json={"role": "owner"},
                    headers={**member, "If-Match": '"1"'}), 403, "permission_denied")
                transferred = _body(await client.post(base + "/transfer-owner", json={"user_id": "member"},
                    headers={**owner, "If-Match": '"1"'}), 200, Member)
                assert transferred.user_id == "member" and transferred.role == "owner" and transferred.revision == 2
                _denied(await client.patch(base + "/members/member", json={"active": False},
                    headers={**owner, "If-Match": '"2"'}), 403, "permission_denied")
                _denied(await client.patch(base + "/members/member", json={"active": False},
                    headers={**member, "If-Match": '"2"'}), 403, "permission_denied")

                other = _body(await client.post("/v1/workspaces", json={"name": "Synthetic B"}, headers=outsider),
                              201, Workspace)
                bundle = io.BytesIO()
                with zipfile.ZipFile(bundle, "w") as archive:
                    archive.writestr("data/metrics.csv", "metric,value\nloss,0.5\n")
                uploaded = await client.post(base + "/sources", content=bundle.getvalue(), headers={
                    **owner, "Content-Type": "application/zip", "Idempotency-Key": "synthetic-http-source-001"})
                assert uploaded.status_code == 201
                source_id = uploaded.json()["source_id"]
                assert uploaded.json()["workspace_id"] == workspace
                receipt = _body(await client.post(base + "/jobs", json={"operation": "preview", "source_id": source_id},
                    headers={**owner, "Idempotency-Key": "synthetic-http-preview-001"}), 202, JobReceipt)
                assert receipt.workspace_id == workspace
                lease = SecretStr("d" * 64)
                claim = await jobs.claim(lease_digest=lease, lease_seconds=30)
                assert claim is not None and claim.job_id == receipt.job_id and claim.credential_digest == owner_digest
                # This check targets authorization and durability; analysis computation is outside this test.
                result = {"synthetic": "authorized-output"}
                assert await jobs.complete(claim=claim, lease_digest=lease, result=result)
                job_path = base + "/jobs/" + receipt.job_id
                status = _body(await client.get(job_path, headers=member), 200, Job)
                assert status.status == "succeeded"
                output = await client.get(job_path + "/output", headers=member)
                assert output.status_code == 200 and output.json()["result"] == result
                assert output.json()["workspace_id"] == workspace and output.json()["job_id"] == receipt.job_id
                outside = _denied(await client.get(job_path + "/output", headers=outsider), 404, "not_found")
                swapped = _denied(await client.get("/v1/workspaces/" + other.workspace_id + "/jobs/" + receipt.job_id + "/output",
                                                  headers=outsider), 404, "not_found")
                assert outside == swapped

                withdrawn = _body(await client.post(base + "/invitations", json={"user_id": "outsider", "role": "viewer"},
                                                    headers=member), 201, Invitation)
                _body(await client.request("DELETE", base + "/invitations/" + withdrawn.invitation_id,
                                           json={}, headers=member), 200, RevokedInvitation)
                _denied(await client.post("/v1/invitations/accept", json={"token": withdrawn.token}, headers=outsider),
                        404, "invitation_unavailable")
                fresh = _body(await client.post(base + "/invitations", json={"user_id": "outsider", "role": "viewer"},
                                                headers=member), 201, Invitation)
                _body(await client.post("/v1/invitations/accept", json={"token": fresh.token}, headers=outsider), 200, Workspace)
                _denied(await client.get(base + "/members", headers=outsider), 403, "permission_denied")
                _denied(await client.post(base + "/invitations", json={"user_id": "member", "role": "admin"},
                                          headers=outsider), 403, "permission_denied")
                _denied(await client.post(base + "/transfer-owner", json={"user_id": "outsider"},
                    headers={**outsider, "If-Match": '"1"'}), 403, "permission_denied")
                _denied(await client.patch(base + "/members/member", json={"active": False},
                    headers={**outsider, "If-Match": '"2"'}), 403, "permission_denied")
                _denied(await client.request("DELETE", base + "/invitations/" + fresh.invitation_id,
                                             json={}, headers=outsider), 403, "permission_denied")
                _denied(await client.post(base + "/sources", content=bundle.getvalue(), headers={
                    **outsider, "Content-Type": "application/zip", "Idempotency-Key": "synthetic-denied-source-001"}),
                    404, "not_found")
                _denied(await client.post(base + "/jobs", json={"operation": "preview", "source_id": source_id},
                    headers={**outsider, "Idempotency-Key": "synthetic-denied-preview-001"}), 404, "not_found")
                grant_path = base + "/sources/" + source_id + "/grants/outsider"
                _denied(await client.put(grant_path, json={"actions": ["read", "write"]},
                    headers={**outsider, "If-Match": '"0"'}), 403, "permission_denied")
                granted = _body(await client.put(grant_path, json={"actions": ["read"]},
                    headers={**member, "If-Match": '"0"'}), 200, SourceGrant)
                assert granted.revision == 1 and granted.source_id == source_id
                assert (await client.get(job_path + "/output", headers=outsider)).status_code == 200
                _body(await client.put(grant_path, json={"actions": []},
                    headers={**member, "If-Match": '"1"'}), 200, SourceGrant)
                _denied(await client.get(job_path + "/output", headers=outsider), 404, "not_found")

                mutation_started = time.perf_counter()
                revoked = _body(await client.patch(base + "/members/owner", json={"active": False},
                    headers={**member, "If-Match": '"2"'}), 200, Member)
                mutation_response = time.perf_counter()
                assert revoked.active is False and revoked.revision == 3
                _denied(await client.get(job_path + "/output", headers=owner), 404, "not_found")
                denied_at = time.perf_counter()
                response_to_denial = denied_at - mutation_response
                upper_bound = denied_at - mutation_started
                assert 0 <= response_to_denial <= upper_bound <= 5
                print(f"K04_HTTP_PG_REVOKE_RESPONSE_TO_OUTPUT_DENIAL_SECONDS={response_to_denial:.6f}")
                print(f"K04_HTTP_PG_REVOKE_REQUEST_TO_OUTPUT_DENIAL_UPPER_BOUND_SECONDS={upper_bound:.6f}")
                _denied(await client.get(job_path, headers=owner), 404, "not_found")
                _denied(await client.get(base + "/capabilities", headers=owner), 403, "membership_revoked")
                _denied(await client.post(base + "/jobs", json={"operation": "preview", "source_id": source_id},
                    headers={**owner, "Idempotency-Key": "synthetic-http-revoked-001"}), 404, "not_found")
                current = _body(await client.get("/v1/session", headers=owner), 200, Session)
                assert current.onboarding == "needs_workspace" and current.last_workspace_id is None
                assert (await client.get(job_path + "/output", headers=member)).json()["result"] == result
                async with pool.connection() as conn:
                    row = await (await conn.execute("SELECT state,result FROM j01_jobs WHERE job_id=%s AND workspace_id=%s",
                                                   (receipt.job_id, workspace))).fetchone()
                    assert row == ("succeeded", result)
                    assert await (await conn.execute("SELECT count(*) FROM j01_memberships WHERE workspace_id=%s "
                        "AND active AND role='owner'", (workspace,))).fetchone() == (1,)

    _run(run())
