"""Native queue publication through distinct real LOGIN API/worker principals."""
from __future__ import annotations

import hashlib
import io
import json
import os
import zipfile

import httpx
import pytest
from pydantic import SecretStr
from test_k04_http_postgres import ORIGIN, _http_identity
from test_k04_runtime_principal_postgres import _principals, _run

from aletheia_lab.hosted.authorization import AuthorizationDenied
from aletheia_lab.hosted.job_http import JobApplication
from aletheia_lab.hosted.native_upload_records import UploadResultRecord
from aletheia_lab.hosted.object_store import FilesystemObjectStore
from aletheia_lab.hosted.postgres_catalog import PostgresCatalog
from aletheia_lab.hosted.postgres_jobs import PostgresJobs
from aletheia_lab.hosted.postgres_objects import PostgresObjects
from aletheia_lab.hosted.postgres_sources import PostgresSources
from aletheia_lab.hosted.request_boundary import BodyLimits
from aletheia_lab.hosted.settings import RuntimeSettings
from aletheia_lab.hosted.upload_analysis import analyze_initial_upload
from aletheia_lab.hosted.upload_project import UploadProject as ProductionUploadProject
from aletheia_lab.hosted.upload_snapshot import UploadSnapshot
from aletheia_lab.hosted.workspace_management import WorkspaceManagement
from aletheia_lab.hosted_auth.csrf import CsrfTokens
from aletheia_lab.hosted_auth.postgres_sessions import PostgresSessionStore
from aletheia_lab.hosted_auth.sessions import ServerSessions, credential_digest
from aletheia_lab.hosted_auth.settings import AuthPolicy
from aletheia_lab.hosted_auth.worker_sessions import WorkerPostgresSources
from aletheia_lab.hosted_contract.job_page import Job, JobReceipt
from aletheia_lab.product.demo_flow import _demo_mapping
from aletheia_lab.product.view import ProductView

pytestmark = [pytest.mark.integration, pytest.mark.skipif(not os.environ.get("K04_TEST_DATABASE_URL"),
    reason="Explicit K-04 PostgreSQL test URL required")]


def _stable_bundle():
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        for name, content in (
            ("dataset.csv", "id,target\n1,0\n2,1\n"),
            ("metrics.csv", "run,name,value\nbaseline,loss,0.5\ncandidate,loss,0.6\n"),
            ("config.json", '{"seed":42}\n'),
        ):
            archive.writestr(zipfile.ZipInfo(name, date_time=(2026, 11, 10, 0, 0, 0)), content)
    return stream.getvalue()


def test_actual_api_worker_login_native_queue_preserves_canonical_truth_and_denies_sensitive_writes(tmp_path):
    import psycopg

    async def scenario():
        async with _principals() as (owner, api, worker, schema, api_role, worker_role, _admin):
            async with owner.connection() as conn:
                owner_role = (await (await conn.execute("SELECT current_user")).fetchone())[0]
            for pool, role in ((api, api_role), (worker, worker_role)):
                async with pool.connection() as conn:
                    assert role != owner_role
                    assert await (await conn.execute("SELECT current_user,session_user")).fetchone() == (role, role)
                    assert await (await conn.execute("SELECT rolsuper,rolbypassrls,rolcreatedb,rolcreaterole,rolinherit "
                        "FROM pg_roles WHERE rolname=current_user")).fetchone() == (False, False, False, False, False)
                    # This baseline deliberately uses the supported audit=False composition.
                    assert await (await conn.execute("SELECT to_regclass('k06_action_audit')")).fetchone() == (None,)

            csrf = CsrfTokens(SecretStr("synthetic-native-principal-" + "k" * 43))
            captured_digest, original_headers = await _http_identity(owner, "native_actor", "n" * 43, csrf)
            workspace = (await WorkspaceManagement(api).create(
                credential_digest=captured_digest, name="Synthetic native queue"))["workspace_id"]
            shared_root = tmp_path / "shared-canonical"
            api_catalog = PostgresCatalog(api, PostgresObjects(api, FilesystemObjectStore(shared_root)))
            worker_catalog = PostgresCatalog(worker, PostgresObjects(worker, FilesystemObjectStore(shared_root)))
            budgets = dict(maximum_upload_bytes=10000, maximum_expanded_bytes=20000, maximum_entries=10)
            sources = PostgresSources(api, catalog=api_catalog, **budgets)
            worker_sources = WorkerPostgresSources(worker, catalog=worker_catalog, **budgets)
            jobs = PostgresJobs(api, sources, catalog=api_catalog)
            worker_jobs = PostgresJobs(worker, worker_sources, catalog=worker_catalog)
            payload = _stable_bundle()
            assert payload == _stable_bundle()
            source = await sources.put(credential_digest=captured_digest, workspace_id=workspace,
                request_key=SecretStr("synthetic-native-principal-source"), payload=payload)
            assert source.sha256 == hashlib.sha256(payload).hexdigest()
            private = tmp_path / "trusted-worker"
            private.mkdir()
            operations = ProductionUploadProject(private, **budgets)
            captured_at = "2026-11-10T00:00:00Z"
            mapping = _demo_mapping(operations.preview(payload, captured_at=captured_at))

            runtime = RuntimeSettings.model_validate({"environment": "local", "public_origin": ORIGIN,
                "identity": {"issuer": "https://issuer.example.invalid", "client_id": "synthetic-native-principal",
                    "redirect_uri": ORIGIN + "/v1/auth/callback", "client_kind": "public"},
                "storage": {"database_url_env": "ALETHEIA_HOSTED_DATABASE_URL", "object_root": str(tmp_path)}})
            sessions = ServerSessions(PostgresSessionStore(api), AuthPolicy(
                default_return_to="/", return_to_paths=("/",), rotation_seconds=1),
                timeout_seconds=3, maximum_cookie_bytes=4096)

            async def clock():
                return True

            async def fallback(scope, receive, send):
                raise AssertionError("Unexpected route outside the job application")

            app = JobApplication(fallback, jobs, sessions, csrf, runtime,
                                 body_limits=BodyLimits(65536, 1, 3, 10), clock_check=clock)
            base = "/v1/workspaces/" + workspace + "/jobs"
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
                confirmation = await client.post(base, json={"operation": "confirm", "source_id": source.source_id,
                    "mapping": mapping}, headers={**original_headers, "Idempotency-Key": "synthetic-native-confirm-key"})
                assert confirmation.status_code == 202
                confirm_receipt = JobReceipt.model_validate_json(confirmation.content)
                renewal = await sessions.renew(original_headers["Cookie"].encode("ascii"))
                assert renewal.credential is not None
                current_digest = credential_digest(renewal.credential)
                current_headers = {**original_headers, "Cookie": original_headers["Cookie"].replace(
                    "n" * 43, renewal.credential.get_secret_value(), 1)}
                with pytest.raises(AuthorizationDenied):
                    await sources.read(credential_digest=captured_digest, workspace_id=workspace, source_id=source.source_id)
                lease = SecretStr("a" * 64)
                claim = await worker_jobs.claim(lease_digest=lease, lease_seconds=300)
                assert claim is not None and claim.job_id == confirm_receipt.job_id
                assert claim.credential_digest == captured_digest
                trusted_payload = await worker_sources.read(credential_digest=claim.credential_digest,
                    workspace_id=workspace, source_id=source.source_id)
                assert trusted_payload == payload
                snapshot = operations.confirm(trusted_payload, workspace_id=workspace, source_id=source.source_id,
                    mapping=mapping, captured_at=captured_at)
                assert await worker_jobs.complete_snapshot(claim=claim, lease_digest=lease, snapshot=snapshot)
                confirmed = await client.get(confirm_receipt.status_url + "/output", headers=current_headers)
                assert confirmed.status_code == 200 and confirmed.json()["result"] == {"snapshot_id": snapshot.snapshot_id}
                denied = await client.get(confirm_receipt.status_url, headers=original_headers)
                assert denied.status_code == 401

                question = "What is observed in this exact uploaded source?"
                submitted = await client.post(base, json={"operation": "analyze", "snapshot_id": snapshot.snapshot_id,
                    "question": question}, headers={**current_headers, "Idempotency-Key": "synthetic-native-analysis-key"})
                assert submitted.status_code == 202
                analyze_receipt = JobReceipt.model_validate_json(submitted.content)
                analyze_lease = SecretStr("b" * 64)
                analysis_claim = await worker_jobs.claim(lease_digest=analyze_lease, lease_seconds=300)
                assert analysis_claim is not None and analysis_claim.job_id == analyze_receipt.job_id
                assert analysis_claim.credential_digest == current_digest
                stored_snapshot = await worker_sources.snapshot(credential_digest=analysis_claim.credential_digest,
                    workspace_id=workspace, snapshot_id=snapshot.snapshot_id)
                assert stored_snapshot == snapshot
                view = analyze_initial_upload(stored_snapshot, question)
                assert await worker_jobs.complete(claim=analysis_claim, lease_digest=analyze_lease,
                                                  result=view.model_dump(mode="json"))
                status = await client.get(analyze_receipt.status_url, headers=current_headers)
                assert status.status_code == 200 and Job.model_validate_json(status.content).status == "succeeded"
                response = await client.get(analyze_receipt.status_url + "/output", headers=current_headers)
                assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
                envelope = response.json()
                assert set(envelope) == {"schema_version", "workspace_id", "job_id", "operation", "result"}
                assert envelope["workspace_id"] == workspace and envelope["job_id"] == analyze_receipt.job_id
                assert envelope["operation"] == "analyze"
                returned_view = ProductView.model_validate_json(json.dumps(envelope["result"]))
                assert returned_view == view and returned_view.result.id == view.result.id
                assert returned_view.snapshot.id == snapshot.snapshot_id
                head = await client.head(analyze_receipt.status_url + "/output", headers=current_headers)
                assert head.status_code == 200 and head.content == b""
                assert head.headers["content-length"] == response.headers["content-length"]

            project, result = snapshot.mapping.project_id, view.result.id
            loaded_snapshot = await api_catalog.read_record(credential_digest=current_digest,
                workspace_id=workspace, project_id=project, record_id=snapshot.snapshot_id)
            loaded_result = await api_catalog.read_record(credential_digest=current_digest,
                workspace_id=workspace, project_id=project, record_id=result)
            assert loaded_snapshot == snapshot and isinstance(loaded_result, UploadResultRecord)
            assert loaded_result.view == view
            assert await worker_sources.snapshot(credential_digest=captured_digest,
                workspace_id=workspace, snapshot_id=snapshot.snapshot_id) == snapshot
            async with api.connection() as conn:
                assert await (await conn.execute("SELECT provenance_kind,revision FROM k05_projects "
                    "WHERE workspace_id=%s AND project_id=%s", (workspace, project))).fetchone() == ("upload", 2)
                assert await (await conn.execute("SELECT record_id,revision FROM k05_current_pointers "
                    "WHERE workspace_id=%s AND project_id=%s AND pointer_name='snapshot'", (workspace, project))).fetchone() == (snapshot.snapshot_id, 0)
                projection = await (await conn.execute("SELECT source_id,canonical_record_id,payload::text FROM j01_upload_snapshots "
                    "WHERE workspace_id=%s AND snapshot_id=%s", (workspace, snapshot.snapshot_id))).fetchone()
                assert projection[:2] == (source.source_id, snapshot.snapshot_id)
                assert UploadSnapshot.model_validate_json(projection[2]) == snapshot
                for receipt, expected in ((confirm_receipt, {"snapshot_id": snapshot.snapshot_id}),
                                          (analyze_receipt, {"result_id": result})):
                    row = await (await conn.execute("SELECT state,request_id,result,canonical_result_id FROM j01_jobs "
                        "WHERE workspace_id=%s AND job_id=%s", (workspace, receipt.job_id))).fetchone()
                    assert row == ("succeeded", receipt.request_id, expected, result if receipt == analyze_receipt else None)
                assert await (await conn.execute("SELECT record_type,source_id,snapshot_id,parent_result_id FROM k05_records "
                    "WHERE workspace_id=%s AND record_id=%s", (workspace, snapshot.snapshot_id))).fetchone() == ("upload_snapshot", source.source_id, None, None)
                assert await (await conn.execute("SELECT record_type,source_id,snapshot_id,parent_result_id FROM k05_records "
                    "WHERE workspace_id=%s AND record_id=%s", (workspace, result))).fetchone() == ("upload_result", None, snapshot.snapshot_id, None)
                assert await (await conn.execute("SELECT relation,parent_id FROM k05_record_parents WHERE workspace_id=%s "
                    "AND record_id=%s", (workspace, result))).fetchone() == ("snapshot", snapshot.snapshot_id)
                rows = await conn.execute("SELECT r.record_id,r.object_sha256,o.byte_size FROM k05_records r "
                    "JOIN k05_objects o ON o.workspace_id=r.workspace_id AND o.sha256=r.object_sha256 "
                    "WHERE r.workspace_id=%s ORDER BY r.record_id", (workspace,))
                verified_records = []
                while (row := await rows.fetchone()) is not None:
                    record_id, sha, byte_size = row
                    data = api_catalog.objects.store.path(sha).read_bytes()
                    assert hashlib.sha256(data).hexdigest() == sha and len(data) == byte_size
                    verified_records.append(record_id)
                assert set(verified_records) == {snapshot.snapshot_id, result}
                assert await (await conn.execute("SELECT count(*) FROM k05_object_leases")).fetchone() == (0,)

            # Grants constrain trusted runtime writers; actor isolation still comes from repositories, not RLS.
            probes = (
                'CREATE TABLE "' + schema + '".forbidden_native_ddl(id integer)',
                'ALTER TABLE "' + schema + '".k05_records DISABLE TRIGGER ALL',
                "UPDATE hosted_identity_bindings SET enabled=enabled WHERE user_id='native_actor'",
                "UPDATE j01_sources SET payload=payload",
                "UPDATE j01_upload_snapshots SET payload=payload",
                "UPDATE k05_records SET object_sha256=object_sha256",
                "UPDATE k05_objects SET byte_size=byte_size",
                "UPDATE j01_jobs SET request=request",
                "UPDATE k04_review_migrations SET checksum=checksum",
            )
            for pool in (api, worker):
                for statement in probes:
                    with pytest.raises(psycopg.errors.InsufficientPrivilege):
                        async with pool.connection() as conn:
                            await conn.execute(statement)
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                async with worker.connection() as conn:
                    await conn.execute("UPDATE j01_memberships SET role=role")

    _run(scenario())
