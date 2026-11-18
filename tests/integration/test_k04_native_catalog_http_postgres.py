"""Production ZIP/native publication reaches catalog HTTP through an actual API LOGIN."""
from __future__ import annotations

import hashlib
import io
import json
import os
import zipfile
from contextlib import asynccontextmanager
from types import SimpleNamespace

import httpx
import pytest
from pydantic import SecretStr
from test_k04_runtime_principal_postgres import _credential, _principals, _run

from aletheia_lab.hosted.authorization import PostgresAuthorization
from aletheia_lab.hosted.canonical_results import CanonicalResults
from aletheia_lab.hosted.catalog_access import CatalogAccess
from aletheia_lab.hosted.catalog_http import CatalogApplication
from aletheia_lab.hosted.cursors import ScopedCursors
from aletheia_lab.hosted.native_upload_records import (
    UploadResultRecord,
    build_initial_upload_result,
    build_upload_follow_up_result,
)
from aletheia_lab.hosted.object_store import FilesystemObjectStore
from aletheia_lab.hosted.postgres_catalog import PostgresCatalog
from aletheia_lab.hosted.postgres_objects import PostgresObjects
from aletheia_lab.hosted.postgres_sources import PostgresSources
from aletheia_lab.hosted.project_access import PostgresProjectAccess
from aletheia_lab.hosted.report_renderer import ReportRenderer
from aletheia_lab.hosted.request_boundary import BodyLimits
from aletheia_lab.hosted.review_migration import verify as verify_reviews
from aletheia_lab.hosted.review_repository import PostgresReviews
from aletheia_lab.hosted.runtime_binding import verify_runtime_binding
from aletheia_lab.hosted.settings import RuntimeSettings
from aletheia_lab.hosted.upload_analysis import analyze_initial_upload
from aletheia_lab.hosted.upload_project import UploadProject as ProductionUploadProject
from aletheia_lab.hosted.upload_publication import UploadPublication
from aletheia_lab.hosted.upload_snapshot import UploadSnapshot
from aletheia_lab.hosted.workspace_management import WorkspaceManagement
from aletheia_lab.hosted_auth.boundary import SESSION_COOKIE
from aletheia_lab.hosted_auth.csrf import CSRF_COOKIE, CsrfTokens
from aletheia_lab.hosted_auth.postgres_sessions import PostgresSessionStore
from aletheia_lab.hosted_auth.sessions import ServerSessions, credential_digest
from aletheia_lab.hosted_auth.settings import AuthPolicy
from aletheia_lab.product.demo_flow import _demo_mapping
from aletheia_lab.product.follow_up import (
    build_product_follow_up_view,
    resolve_product_follow_up_selection,
)
from aletheia_lab.product.reports import render_product_report

pytestmark = [pytest.mark.integration, pytest.mark.skipif(not os.environ.get("K04_TEST_DATABASE_URL"),
    reason="Explicit K04 PostgreSQL test URL required")]


def _stable_zip():
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, body in sorted({"dataset.csv": "id,target\n1,0\n2,1\n",
                "metrics.csv": "run,name,value\nbaseline,loss,0.5\ncandidate,loss,0.6\n",
                "config.json": '{"seed":42}\n'}.items()):
            entry = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
            entry.external_attr = 0o600 << 16
            archive.writestr(entry, body)
    return output.getvalue()


@asynccontextmanager
async def _native_application(tmp_path):
    # The shared helper creates UUID-owned schema + actual API/worker LOGIN pools.
    # It installs canonical/project/review migrations only; no K07/audit migration.
    async with _principals() as (owner, api, _, schema, api_role, _, _):
        memberships = WorkspaceManagement(api)
        workspace = (await memberships.create(credential_digest=_credential("owner_a"), name="Native A"))["workspace_id"]
        other = (await memberships.create(credential_digest=_credential("owner_b"), name="Native B"))["workspace_id"]
        invitation = await memberships.invite(credential_digest=_credential("owner_a"), workspace_id=workspace,
            user_id="analyst", role="analyst", expires_in_seconds=600)
        await memberships.accept_invite(credential_digest=_credential("analyst"), token=SecretStr(invitation["token"]))
        payload = _stable_zip()
        assert payload == _stable_zip()
        source = await PostgresSources(api, maximum_upload_bytes=10000, maximum_expanded_bytes=20000,
            maximum_entries=10).put(credential_digest=_credential("owner_a"), workspace_id=workspace,
                request_key=SecretStr("synthetic-native-http-source"), payload=payload)
        private = tmp_path / "native-materialization"
        private.mkdir()
        project = ProductionUploadProject(private, maximum_upload_bytes=10000,
            maximum_expanded_bytes=20000, maximum_entries=10)
        captured = "2026-11-10T00:00:00Z"
        preview = project.preview(payload, captured_at=captured)
        snapshot = project.confirm(payload, workspace_id=workspace, source_id=source.source_id,
            mapping=_demo_mapping(preview), expected_source_state_sha256=preview["source_state_sha256"], captured_at=captured)
        assert isinstance(snapshot, UploadSnapshot) and snapshot.git_provenance == "not_supplied"
        question = "What is observed in this exact uploaded source?"
        initial = build_initial_upload_result(snapshot, question, analyze_initial_upload(snapshot, question))
        selection = resolve_product_follow_up_selection(initial.view, selection_kind="claim",
            selection_id=initial.view.claims[0].id, question="What source evidence remains uncertain?")
        child = build_upload_follow_up_result(workspace, initial.view, build_product_follow_up_view(initial.view, selection))
        assert isinstance(initial, UploadResultRecord) and child.parent_result_id == initial.record_id
        store = FilesystemObjectStore(tmp_path / "native-canonical-objects")
        objects = PostgresObjects(owner, store)
        uploads, results, policy = UploadPublication(objects), CanonicalResults(objects), PostgresAuthorization()
        # Operator-owned enlistment is explicit: prepare/stage is outside each transaction.
        # These assertions do not claim the API job-completion hook has been wired.
        prepared_snapshot = await uploads.prepare_snapshot(snapshot)
        prepared_initial = await results.prepare_initial_result(workspace, snapshot=snapshot, record=initial)
        async with owner.connection() as conn:
            auth = await policy.authorize(conn, _credential("owner_a").get_secret_value(), workspace, "confirm")
            published = await uploads.enlist_snapshot(conn, authorization=auth, prepared=prepared_snapshot)
            auth = await policy.authorize(conn, _credential("owner_a").get_secret_value(), workspace, "analyze")
            published_initial = await results.enlist_result(conn, authorization=auth, prepared=prepared_initial,
                expected_revision=published.revision)
        prepared_child = await results.prepare_result(workspace, parent=initial, record=child)
        async with owner.connection() as conn:
            auth = await policy.authorize(conn, _credential("owner_a").get_secret_value(), workspace, "analyze")
            await results.enlist_result(conn, authorization=auth, prepared=prepared_child,
                expected_revision=published_initial.revision)
        grants = PostgresProjectAccess(api)
        await grants.set_grant(credential_digest=_credential("owner_a"), workspace_id=workspace,
            project_id=initial.project_id, user_id="analyst", actions=["read", "review", "export"], expected_revision=0)
        await memberships.set_source_grant(credential_digest=_credential("owner_a"), workspace_id=workspace,
            source_id=source.source_id, user_id="analyst", actions=["read", "review", "export"], expected_revision=0)
        tokens = CsrfTokens(SecretStr("synthetic-native-http-csrf-key-" + "x" * 43))
        headers, digests = {}, {}
        async with owner.connection() as conn:
            for user in ("owner_a", "owner_b", "analyst"):
                raw = SecretStr(("synthetic-native-http-" + user).ljust(43, "x"))
                proof = tokens.issue("ctx_" + user)
                digests[user] = credential_digest(raw)
                await conn.execute("UPDATE j01_session_credentials SET digest=%s WHERE context_id=%s",
                    (digests[user].get_secret_value(), "ctx_" + user))
                await conn.execute("UPDATE j01_sessions SET csrf_token=%s WHERE context_id=%s",
                    (proof.get_secret_value(), "ctx_" + user))
                headers[user] = {"origin": "https://app.example.invalid", "x-csrf-token": proof.get_secret_value(),
                    "cookie": f"{SESSION_COOKIE}={raw.get_secret_value()}; {CSRF_COOKIE}={proof.get_secret_value()}"}
        settings = RuntimeSettings.model_validate({"environment": "local", "public_origin": "https://app.example.invalid",
            "identity": {"issuer": "https://identity.example.invalid", "client_id": "synthetic",
                "redirect_uri": "https://app.example.invalid/v1/auth/callback", "client_kind": "public"},
            "storage": {"database_url_env": "ALETHEIA_HOSTED_DATABASE_URL", "object_root": str(tmp_path),
                "expected_schema": schema, "expected_database_role": api_role},
            "api": {"backend_timeout_seconds": 20, "request_timeout_seconds": 30}})
        assert await verify_runtime_binding(api, settings.storage, strict=True)
        async with api.connection() as conn:
            assert await (await conn.execute("SELECT current_user=session_user,current_user=%s", (api_role,))).fetchone() == (True, True)
            assert await verify_reviews(conn, schema)
            assert await (await conn.execute("SELECT version FROM k04_review_migrations ORDER BY version")).fetchall() == [(1,), (2,), (3,)]
        sessions = ServerSessions(PostgresSessionStore(api), AuthPolicy(default_return_to="/app", return_to_paths=("/app",)),
            timeout_seconds=5, maximum_cookie_bytes=4096)
        renderer = ReportRenderer(timeout_seconds=20)
        catalog = PostgresCatalog(api, PostgresObjects(api, store))
        access = CatalogAccess(catalog, ScopedCursors(SecretStr("synthetic-native-http-cursor-key-" + "x" * 43)), renderer)

        async def clock():
            return True

        async def fallback(scope, receive, send):
            raise AssertionError("Unexpected native catalog route")

        app = CatalogApplication(fallback, access, grants, PostgresReviews(api, catalog), sessions, tokens, settings,
            body_limits=BodyLimits(10000, 2, 5, 100), clock_check=clock)
        try:
            yield SimpleNamespace(app=app, owner=owner, api=api, headers=headers, digests=digests, workspace=workspace,
                other=other, snapshot=snapshot, initial=initial, child=child, source=source, store=store)
        finally:
            await renderer.aclose()


def test_native_canonical_http_lists_aliases_result_children_and_real_child_exports(tmp_path):
    async def scenario():
        async with _native_application(tmp_path) as case:
            base = f"/v1/workspaces/{case.workspace}"
            project = base + "/projects/" + case.initial.project_id
            result = base + "/results/" + case.initial.record_id
            headers = case.headers["analyst"]
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=case.app), base_url="https://app.example.invalid") as client:
                first = await client.get(base + "/results", params={"limit": 1}, headers=headers)
                assert first.status_code == 200 and first.json()["next_cursor"]
                second = await client.get(base + "/results", params={"limit": 1, "cursor": first.json()["next_cursor"]}, headers=headers)
                assert second.status_code == 200 and second.json()["next_cursor"] is None
                items = first.json()["items"] + second.json()["items"]
                assert {item["record_id"] for item in items} == {case.initial.record_id, case.child.record_id}
                assert all(item["record_type"] == "upload_result" and "object_sha256" not in item and "source_id" not in item for item in items)
                grouped = await client.get(project + "/records", params={"kind": "result", "snapshot_id": case.snapshot.snapshot_id}, headers=headers)
                assert grouped.status_code == 200 and {item["record_id"] for item in grouped.json()["items"]} == {case.initial.record_id, case.child.record_id}
                aliases = await client.get(project + "/snapshots", headers=headers)
                assert aliases.status_code == 200 and [(item["record_id"], item["record_type"]) for item in aliases.json()["items"]] == [(case.snapshot.snapshot_id, "upload_snapshot")]
                detail = await client.get(project + "/snapshots/" + case.snapshot.snapshot_id, headers=headers)
                assert detail.status_code == 200 and detail.json()["record_kind"] == "upload_snapshot"
                assert detail.json()["data"] == case.snapshot.model_dump(mode="json")
                native = await client.get(project + "/records/" + case.initial.record_id, headers=headers)
                assert native.status_code == 200 and native.json()["record_kind"] == "upload_result"
                view = await client.get(result, headers=headers)
                assert view.status_code == 200 and view.json()["view"] == case.initial.view.model_dump(mode="json")
                claims = await client.get(result + "/claims", headers=headers)
                assert claims.status_code == 200 and {item["id"] for item in claims.json()["items"]} == {item.id for item in case.initial.view.claims}
                graph = await client.get(result + "/graph", headers=headers)
                assert graph.status_code == 200 and graph.json()["graph"] == case.initial.view.graph.model_dump(mode="json")
                for report_format in ("json", "markdown"):
                    export = await client.post(result + "/exports", headers=headers, json={"format": report_format})
                    assert export.status_code == 201
                    descriptor = export.json()
                    downloaded = await client.get(descriptor["download_url"], headers=headers)
                    expected = render_product_report(case.initial.view, report_format)
                    assert downloaded.status_code == 200 and downloaded.content == expected
                    assert len(expected) == descriptor["byte_size"] and hashlib.sha256(expected).hexdigest() == descriptor["sha256"]
                for path, actor in ((result, "owner_b"), (f"/v1/workspaces/{case.other}/results/{case.initial.record_id}", "owner_b"),
                        (project + "/snapshots/" + case.initial.record_id, "owner_a")):
                    denied = await client.get(path, headers=case.headers[actor])
                    assert denied.status_code == 404 and case.initial.record_id not in denied.text
                wrong_claim = next(item.id for item in case.child.view.claims if item.id not in {claim.id for claim in case.initial.view.claims})
                denied_child = await client.get(result + "/evidence/" + case.initial.view.evidence[0].id,
                    params={"claim_id": wrong_claim}, headers=headers)
                assert denied_child.status_code == 404
                empty = await client.get(f"/v1/workspaces/{case.other}/results", headers=case.headers["owner_b"])
                assert empty.status_code == 200 and empty.json()["items"] == []
    _run(scenario())


def test_native_review_v2_author_cas_history_and_corrupt_object_never_falls_back_to_job_json(tmp_path):
    async def scenario():
        async with _native_application(tmp_path) as case:
            result = f"/v1/workspaces/{case.workspace}/results/{case.initial.record_id}"
            annotation = result + "/annotations/native-review"
            headers = case.headers["analyst"]
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=case.app), base_url="https://app.example.invalid") as client:
                first = await client.put(annotation, headers={**headers, "if-match": '"0"', "x-user-id": "owner_a"},
                    json={"body": "Observed exact uploaded evidence.", "status": "comment"})
                assert first.status_code == 200 and first.json()["author_id"] == "analyst" and first.headers["etag"] == '"1"'
                stale = await client.put(annotation, headers={**headers, "if-match": '"0"'}, json={"body": "Stale", "status": "approved"})
                assert stale.status_code == 412
                other_author = await client.put(annotation, headers={**case.headers["owner_a"], "if-match": '"1"'},
                    json={"body": "Cannot replace another author.", "status": "approved"})
                assert other_author.status_code == 404
                second = await client.put(annotation, headers={**headers, "if-match": '"1"'},
                    json={"body": "Human review complete.", "status": "approved"})
                assert second.status_code == 200 and second.json()["revision"] == 2 and second.headers["etag"] == '"2"'
                history = await client.get(annotation + "/history", params={"limit": 1}, headers=headers)
                assert history.status_code == 200 and [item["revision"] for item in history.json()["items"]] == [1]
                following = await client.get(annotation + "/history", params={"limit": 1, "cursor": history.json()["next_cursor"]}, headers=headers)
                assert following.status_code == 200 and [item["revision"] for item in following.json()["items"]] == [2]
                assert following.json()["next_cursor"] is None
                snapshot_parent = f"/v1/workspaces/{case.workspace}/results/{case.snapshot.snapshot_id}/annotations/not-a-result"
                assert (await client.put(snapshot_parent, headers={**headers, "if-match": '"0"'},
                    json={"body": "Invalid snapshot parent", "status": "comment"})).status_code == 404
                async with case.owner.connection() as conn:
                    # A valid old job projection remains present. Full-record/export
                    # reads must verify object bytes; bounded SQL projections verify
                    # their own immutable payload checksums instead of opening it.
                    await conn.execute("INSERT INTO j01_upload_snapshots(snapshot_id,workspace_id,source_id,payload,canonical_record_id) "
                        "VALUES(%s,%s,%s,%s::jsonb,%s)", (case.snapshot.snapshot_id, case.workspace, case.source.source_id,
                        case.snapshot.model_dump_json(), case.snapshot.snapshot_id))
                    await conn.execute("INSERT INTO j01_jobs(job_id,workspace_id,creator_id,credential_digest,operation,"
                        "key_hash,request_hash,request,request_id,state,result,canonical_result_id) "
                        "VALUES('job_native_projection',%s,'owner_a',%s,'analyze',%s,%s,%s::jsonb,'req_native_projection','succeeded',%s::jsonb,%s)",
                        (case.workspace, case.digests["owner_a"].get_secret_value(), "a" * 64, "b" * 64,
                        json.dumps({"snapshot_id": case.snapshot.snapshot_id, "question": case.initial.view.conversation.turns[0].question}),
                        case.initial.view.model_dump_json(), case.initial.record_id))
                    sha = (await (await conn.execute("SELECT object_sha256 FROM k05_records WHERE workspace_id=%s AND record_id=%s",
                        (case.workspace, case.initial.record_id))).fetchone())[0]
                    assert await (await conn.execute("SELECT result->'result'->>'id' FROM j01_jobs WHERE job_id='job_native_projection'")).fetchone() == (case.initial.record_id,)
                    assert await (await conn.execute("SELECT count(*) FROM j01_annotations")).fetchone() == (2,)
                assert (await client.get(result, headers=headers)).status_code == 200
                projected = (result + '/header', result + '/claims', result + '/graph', result + '/evidence',
                             result + '/evidence/' + case.initial.view.evidence[0].id)
                baselines = {}
                for path in projected:
                    response = await client.get(path, headers=headers)
                    assert response.status_code == 200
                    baselines[path] = response.json()

                def assert_safe_failure(response):
                    assert response.status_code == 500 and response.json()['code'] == 'service_failure'
                    assert response.json()['detail'] == 'The service could not complete the request.'
                    assert all(value not in response.text for value in (str(tmp_path), sha, case.initial.record_id,
                        'synthetic-corrupt', 'Observed exact uploaded evidence'))

                case.store.path(sha).write_bytes(b"synthetic-corrupt-canonical-object")
                assert_safe_failure(await client.get(result, headers=headers))
                broken_export = await client.post(result + "/exports", headers=headers, json={"format": "json"})
                assert_safe_failure(broken_export)
                assert 'view' not in broken_export.json()
                for path in projected:
                    response = await client.get(path, headers=headers)
                    assert response.status_code == 200 and response.json() == baselines[path]
                # Trailing whitespace is valid JSON, so failure below specifically
                # requires checksum verification, not merely a JSON parse error.
                async with case.owner.connection() as conn:
                    await conn.execute("UPDATE k10_result_items SET payload=payload||' ' "
                        'WHERE workspace_id=%s AND result_id=%s', (case.workspace, case.initial.record_id))
                    await conn.execute("UPDATE k10_result_views SET header=header||' ' "
                        'WHERE workspace_id=%s AND result_id=%s', (case.workspace, case.initial.record_id))
                    assert await (await conn.execute("SELECT result->'result'->>'id' FROM j01_jobs "
                        "WHERE job_id='job_native_projection'")).fetchone() == (case.initial.record_id,)
                for path in projected:
                    assert_safe_failure(await client.get(path, headers=headers))
    _run(scenario())
