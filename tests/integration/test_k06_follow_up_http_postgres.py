"""P19 real HTTP/PG admission, canonical publication, replay and current policy."""
from __future__ import annotations

import json
import os

import httpx
import pytest
from pydantic import SecretStr
from test_k05_migrations_postgres import _run
from test_k05_native_results_postgres import _case, _initial

from aletheia_lab.hosted.action_audit_migration import migrate as migrate_audit
from aletheia_lab.hosted.job_http import JobApplication
from aletheia_lab.hosted.native_upload_records import build_upload_follow_up_result
from aletheia_lab.hosted.postgres_jobs import PostgresJobs
from aletheia_lab.hosted.postgres_sources import PostgresSources
from aletheia_lab.hosted.project_access import PostgresProjectAccess
from aletheia_lab.hosted.project_http import ProjectOperationsApplication
from aletheia_lab.hosted.project_mutations import PostgresProjectMutations
from aletheia_lab.hosted.request_boundary import BodyLimits
from aletheia_lab.hosted.settings import RuntimeSettings
from aletheia_lab.hosted.workspace_management import WorkspaceManagement
from aletheia_lab.hosted_auth.csrf import CsrfTokens
from aletheia_lab.hosted_auth.postgres_sessions import PostgresSessionStore
from aletheia_lab.hosted_auth.sessions import ServerSessions, credential_digest
from aletheia_lab.hosted_auth.settings import AuthPolicy
from aletheia_lab.hosted_contract.job_page import Job, JobReceipt
from aletheia_lab.hosted_contract.project_operations import FollowUpJobOutput
from aletheia_lab.hosted_contract.session_capabilities import Problem
from aletheia_lab.product.follow_up import (
    build_product_follow_up_view,
    resolve_product_follow_up_selection,
)
from aletheia_lab.product.view import ProductView

pytestmark = [pytest.mark.integration, pytest.mark.skipif(
    not os.environ.get("K05_TEST_DATABASE_URL"), reason="Explicit owned-schema PostgreSQL test URL required")]
ORIGIN = "https://app.example.invalid"


async def _identity(case, scope, opaque, tokens):
    digest = credential_digest(SecretStr(opaque))
    previous = case.a if scope == "a" else case.b
    csrf = tokens.issue("ctx_" + scope).get_secret_value()
    async with case.pool.connection() as conn:
        await conn.execute("UPDATE j01_session_credentials SET digest=%s WHERE digest=%s AND context_id=%s",
                           (digest.get_secret_value(), previous.get_secret_value(), "ctx_" + scope))
        await conn.execute("UPDATE j01_sessions SET csrf_token=%s WHERE context_id=%s", (csrf, "ctx_" + scope))
    if scope == "a":
        case.a = digest
    else:
        case.b = digest
    return {"cookie": f"__Host-aletheia_session={opaque}; __Host-aletheia_csrf={csrf}",
            "x-csrf-token": csrf, "origin": ORIGIN}


def _problem(response, status, code):
    assert response.status_code == status, response.text
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["content-type"].startswith("application/problem+json")
    parsed = Problem.model_validate_json(response.content)
    assert (parsed.status, parsed.code) == (status, code)
    return parsed.model_dump(exclude={"request_id"})


@pytest.mark.parametrize("revocation", ["membership", "tombstone"])
def test_follow_up_http_actual_pg_receipt_revision_publication_and_revoked_replay(tmp_path, revocation):
    async def scenario():
        async with _case(tmp_path) as case:
            _, initial = await _initial(case)
            assert initial.revision == 2
            async with case.pool.connection() as conn:
                schema = (await (await conn.execute("SELECT current_schema()")).fetchone())[0]
                assert await migrate_audit(conn, schema) == "created"
            tokens = CsrfTokens(SecretStr("synthetic-k06-follow-up-http-" + "x" * 43))
            owner = await _identity(case, "a", "a" * 43, tokens)
            other = await _identity(case, "b", "b" * 43, tokens)
            runtime = RuntimeSettings.model_validate({"environment": "staging", "public_origin": ORIGIN,
                "identity": {"issuer": "https://issuer.example.invalid", "client_id": "synthetic-p19",
                    "redirect_uri": ORIGIN + "/v1/auth/callback", "client_kind": "public"},
                "storage": {"database_url_env": "ALETHEIA_HOSTED_DATABASE_URL", "object_root": str(tmp_path)}})
            sessions = ServerSessions(PostgresSessionStore(case.pool),
                AuthPolicy(default_return_to="/", return_to_paths=("/",)), timeout_seconds=3,
                maximum_cookie_bytes=runtime.api.maximum_cookie_bytes)
            case.sources = PostgresSources(case.pool, maximum_upload_bytes=10000, maximum_expanded_bytes=20000,
                                           maximum_entries=10, catalog=case.catalog)
            jobs = PostgresJobs(case.pool, case.sources, audit=True, catalog=case.catalog)
            assert await jobs.follow_up_ready()

            async def fallback(scope, receive, send):
                raise AssertionError("Unexpected P19 integration route")

            async def clock():
                return True

            app = JobApplication(fallback, jobs, sessions, tokens, runtime,
                body_limits=BodyLimits(20000, 1, 3, 20), clock_check=clock)
            app = ProjectOperationsApplication(app, jobs, sessions, tokens, runtime,
                body_limits=BodyLimits(20000, 1, 3, 20), clock_check=clock)
            path = f"/v1/workspaces/a/results/{initial.record_id}/follow-ups"
            body = {"schema_version": "product-api/v1-draft", "selection_kind": "claim",
                    "selection_id": case.view.claims[0].id, "question": "Which observed evidence remains uncertain?"}
            key_headers = {**owner, "idempotency-key": "synthetic-p19-http-key-0001"}
            headers = {**key_headers, "if-match": '"2"'}
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
                _problem(await client.post(path, json=body, headers=key_headers), 428, "precondition_required")
                _problem(await client.post(path, json=body, headers={**headers, "if-match": '"1"'}),
                         412, "revision_conflict")
                admitted = await client.post(path, json=body, headers=headers)
                assert admitted.status_code == 202, admitted.text
                receipt = JobReceipt.model_validate_json(admitted.content)
                assert receipt.workspace_id == "a" and receipt.status == "queued"
                assert receipt.request_id == admitted.headers["x-request-id"]
                replay = await client.post(path, json=body, headers=headers)
                assert replay.status_code == 202 and JobReceipt.model_validate_json(replay.content) == receipt
                _problem(await client.post(path, json={**body, "question": "A different synthetic question?"}, headers=headers),
                         409, "idempotency_conflict")
                _problem(await client.post(path, json=body, headers={**headers, "if-match": '"3"'}),
                         409, "idempotency_conflict")
                lease = SecretStr("a" * 64)
                claim = await jobs.claim(lease_digest=lease, lease_seconds=120, operations=("follow_up",))
                assert claim and claim.job_id == receipt.job_id and claim.request["expected_revision"] == 2
                parent = await jobs.follow_up_parent(claim=claim)
                selection = resolve_product_follow_up_selection(parent, selection_kind=body["selection_kind"],
                    selection_id=body["selection_id"], question=body["question"])
                child = build_upload_follow_up_result("a", parent, build_product_follow_up_view(parent, selection))
                assert await jobs.complete_result(claim=claim, lease_digest=lease, record=child)
                status = await client.get(receipt.status_url, headers=owner)
                assert status.status_code == 200 and Job.model_validate_json(status.content).status == "succeeded"
                output_response = await client.get(receipt.status_url + "/output", headers=owner)
                assert output_response.status_code == 200, output_response.text
                output = FollowUpJobOutput.model_validate_json(output_response.content)
                assert output.job_id == receipt.job_id and output.workspace_id == "a"
                assert (output.result.project_id, output.result.parent_result_id, output.result.result_id,
                        output.result.snapshot_id, output.result.revision) == (
                    initial.project_id, initial.record_id, child.record_id, case.snapshot.snapshot_id, 3)
                replay_after_publication = await client.post(path, json=body, headers=headers)
                assert replay_after_publication.status_code == 202
                assert JobReceipt.model_validate_json(replay_after_publication.content) == receipt
                _problem(await client.post(path, json=body, headers={**headers,
                    "idempotency-key": "synthetic-p19-fresh-key-0002"}), 412, "revision_conflict")
                assert await case.catalog.read_record(credential_digest=case.a, workspace_id="a",
                    project_id=initial.project_id, record_id=initial.record_id) == case.initial
                assert await case.catalog.read_record(credential_digest=case.a, workspace_id="a",
                    project_id=initial.project_id, record_id=child.record_id) == child
                _problem(await client.get(receipt.status_url.replace("/workspaces/a/", "/workspaces/b/") + "/output",
                                           headers=other), 404, "not_found")
                async with case.pool.connection() as conn:
                    assert await (await conn.execute("SELECT revision FROM k05_projects WHERE workspace_id='a' "
                        "AND project_id=%s", (initial.project_id,))).fetchone() == (3,)
                    assert await (await conn.execute("SELECT count(*) FROM j01_jobs")).fetchone() == (1,)
                    audit = await (await conn.execute("SELECT action,effect_id,parent_id,request_id FROM k06_action_audit "
                        "WHERE job_id=%s ORDER BY action", (receipt.job_id,))).fetchall()
                    assert len(audit) == 2
                    assert audit[0] == ("job.admit", receipt.job_id, None, receipt.request_id)
                    assert audit[1][0:3] == ("job.publish", child.record_id, initial.project_id)
                    assert body["question"] not in json.dumps(audit)
                if revocation == "membership":
                    await WorkspaceManagement(case.pool, audit=True).mutate_member(credential_digest=case.b,
                        workspace_id="a", user_id="owner_a", expected_revision=1, active=False)
                else:
                    deleted = await PostgresProjectMutations(case.pool, case.catalog, audit=True).delete(
                        credential_digest=case.a, workspace_id="a", project_id=initial.project_id, expected_revision=3)
                    assert deleted["status"] == "tombstoned" and deleted["revision"] == 4
                denied = _problem(await client.post(path, json=body, headers=headers), 404, "not_found")
                _problem(await client.get(receipt.status_url, headers=owner), 404, "not_found")
                _problem(await client.get(receipt.status_url + "/output", headers=owner), 404, "not_found")
                absent_path = path.replace(initial.record_id, "p6-result-" + "0" * 64)
                absent = _problem(await client.post(absent_path, json=body, headers=headers), 404, "not_found")
                assert denied == absent
                async with case.pool.connection() as conn:
                    assert await (await conn.execute("SELECT count(*) FROM j01_jobs")).fetchone() == (1,)
                    assert await (await conn.execute("SELECT count(*) FROM k06_action_audit WHERE job_id=%s",
                                                    (receipt.job_id,))).fetchone() == (2,)
                    assert await (await conn.execute("SELECT count(*) FROM k05_records WHERE workspace_id='a' "
                        "AND project_id=%s AND record_type='upload_result'", (initial.project_id,))).fetchone() == (2,)
                print("K06 P19 actualHTTP+PG: receipt survives revision2→3; current " + revocation + " blocks replay/output")
    _run(scenario())


def test_native_analysis_http_output_reads_canonical_view_checks_viewer_grants_and_fails_closed_on_corruption(tmp_path):
    async def scenario():
        async with _case(tmp_path) as case:
            async with case.pool.connection() as conn:
                schema = (await (await conn.execute("SELECT current_schema()")).fetchone())[0]
                await migrate_audit(conn, schema)
            tokens = CsrfTokens(SecretStr("synthetic-k06-analysis-http-" + "x" * 43))
            owner = await _identity(case, "a", "a" * 43, tokens)
            runtime = RuntimeSettings.model_validate({"environment": "staging", "public_origin": ORIGIN,
                "identity": {"issuer": "https://issuer.example.invalid", "client_id": "synthetic-analysis",
                    "redirect_uri": ORIGIN + "/v1/auth/callback", "client_kind": "public"},
                "storage": {"database_url_env": "ALETHEIA_HOSTED_DATABASE_URL", "object_root": str(tmp_path)}})
            sessions = ServerSessions(PostgresSessionStore(case.pool),
                AuthPolicy(default_return_to="/", return_to_paths=("/",)), timeout_seconds=3,
                maximum_cookie_bytes=runtime.api.maximum_cookie_bytes)
            case.sources = PostgresSources(case.pool, maximum_upload_bytes=10000, maximum_expanded_bytes=20000,
                                           maximum_entries=10, catalog=case.catalog)
            jobs = PostgresJobs(case.pool, case.sources, audit=True, catalog=case.catalog)
            confirmed = await jobs.enqueue(credential_digest=case.a, workspace_id="a", operation="confirm",
                request_key=SecretStr("http-native-confirm-key-001"), request={"source_id": case.receipt.source_id,
                    "mapping": case.snapshot.mapping.model_dump(mode="json")})
            confirm_lease = SecretStr("c" * 64)
            confirming = await jobs.claim(lease_digest=confirm_lease, lease_seconds=120, operations=("confirm",))
            assert confirming and confirming.job_id == confirmed.job_id
            assert await jobs.complete_snapshot(claim=confirming, lease_digest=confirm_lease, snapshot=case.snapshot)
            receipt = await jobs.enqueue(credential_digest=case.a, workspace_id="a", operation="analyze",
                request_key=SecretStr("http-native-analysis-key-001"), request={"snapshot_id": case.snapshot.snapshot_id,
                    "question": case.view.conversation.turns[0].question})
            lease = SecretStr("d" * 64)
            claim = await jobs.claim(lease_digest=lease, lease_seconds=120, operations=("analyze",))
            assert claim and claim.job_id == receipt.job_id
            assert await jobs.complete(claim=claim, lease_digest=lease, result=case.view.model_dump(mode="json"))
            opaque = "v" * 43
            digest = credential_digest(SecretStr(opaque))
            csrf = tokens.issue("ctx_viewer").get_secret_value()
            async with case.pool.connection() as conn:
                await conn.execute("INSERT INTO hosted_identity_bindings(issuer,subject,user_id,display_name,email,"
                    "email_verified,enabled,account_epoch,onboarding) VALUES('https://issuer.example.invalid','viewer',"
                    "'viewer','Synthetic viewer','viewer@example.invalid',true,true,0,'ready')")
                await conn.execute("INSERT INTO j01_memberships VALUES('a','viewer','viewer',true,1)")
                await conn.execute("INSERT INTO j01_sessions VALUES('ctx_viewer','viewer',%s,'active',"
                    "statement_timestamp(),statement_timestamp(),statement_timestamp(),statement_timestamp()+interval '900 seconds',"
                    "statement_timestamp()+interval '28000 seconds',0,0)", (csrf,))
                await conn.execute("INSERT INTO j01_session_credentials VALUES(%s,'ctx_viewer','active',0)",
                                   (digest.get_secret_value(),))
            viewer = {"cookie": f"__Host-aletheia_session={opaque}; __Host-aletheia_csrf={csrf}",
                      "x-csrf-token": csrf, "origin": ORIGIN}
            manager, grants = WorkspaceManagement(case.pool, audit=True), PostgresProjectAccess(case.pool, audit=True)
            await manager.set_source_grant(credential_digest=case.a, workspace_id="a", source_id=case.receipt.source_id,
                user_id="viewer", actions=["read"], expected_revision=0)
            await grants.set_grant(credential_digest=case.a, workspace_id="a", project_id=case.initial.project_id,
                user_id="viewer", actions=["read"], expected_revision=0)

            async def fallback(scope, receive, send):
                raise AssertionError("Unexpected native analysis HTTP route")

            async def clock():
                return True

            app = JobApplication(fallback, jobs, sessions, tokens, runtime,
                body_limits=BodyLimits(20000, 1, 3, 20), clock_check=clock)
            path = receipt.status_url + "/output"
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
                for actor in (owner, viewer):
                    output = await client.get(path, headers=actor)
                    assert output.status_code == 200, output.text
                    assert output.json()["operation"] == "analyze"
                    assert ProductView.model_validate_json(json.dumps(output.json()["result"])) == case.view
                await grants.set_grant(credential_digest=case.a, workspace_id="a", project_id=case.initial.project_id,
                    user_id="viewer", actions=[], expected_revision=1)
                _problem(await client.get(path, headers=viewer), 404, "not_found")
                await grants.set_grant(credential_digest=case.a, workspace_id="a", project_id=case.initial.project_id,
                    user_id="viewer", actions=["read"], expected_revision=2)
                assert (await client.get(path, headers=viewer)).status_code == 200
                await manager.set_source_grant(credential_digest=case.a, workspace_id="a", source_id=case.receipt.source_id,
                    user_id="viewer", actions=[], expected_revision=1)
                _problem(await client.get(path, headers=viewer), 404, "not_found")
                async with case.pool.connection() as conn:
                    object_sha = (await (await conn.execute("SELECT object_sha256 FROM k05_records WHERE workspace_id='a' "
                        "AND record_id=%s", (case.initial.record_id,))).fetchone())[0]
                object_path = case.objects.store.path(object_sha)
                assert object_path.resolve().is_relative_to(tmp_path.resolve())
                original_bytes = object_path.read_bytes()
                try:
                    object_path.write_bytes(b"synthetic corrupt canonical result")
                    rejected = await client.get(path, headers=owner)
                    _problem(rejected, 500, "service_failure")
                    assert case.view.conversation.turns[0].question not in rejected.text
                    assert str(object_path) not in rejected.text
                finally:
                    object_path.write_bytes(original_bytes)
                restored = await client.get(path, headers=owner)
                assert restored.status_code == 200
                assert ProductView.model_validate_json(json.dumps(restored.json()["result"])) == case.view
                async with case.pool.connection() as conn:
                    assert await (await conn.execute("SELECT count(*) FROM j01_jobs")).fetchone() == (2,)
                    assert await (await conn.execute("SELECT count(*) FROM k06_action_audit WHERE action='job.publish'")).fetchone() == (2,)
                print("K06 nativeANALYZE actualHTTP: canonical ProductView; explicit viewer read grants; revoke and corruption fail closed")
    _run(scenario())
