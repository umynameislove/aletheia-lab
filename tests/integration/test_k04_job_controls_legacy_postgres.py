"""Official legacy fixture remains usable for normal jobs but gates new controls."""
import io
import os
import zipfile

import httpx
import pytest
from pydantic import SecretStr
from test_k04_http_postgres import ORIGIN, _http_identity
from test_k04_job_controls_postgres import _setup
from test_k04_member_pages_postgres import SECRET, _canonical
from test_k04_workspaces_postgres import _database, _run

from aletheia_lab.hosted.job_http import JobApplication
from aletheia_lab.hosted.postgres_jobs import PostgresJobs
from aletheia_lab.hosted.postgres_sources import PostgresSources
from aletheia_lab.hosted.request_boundary import BodyLimits
from aletheia_lab.hosted.settings import RuntimeSettings
from aletheia_lab.hosted.workspace_management import WorkspaceManagement
from aletheia_lab.hosted_auth.csrf import CsrfTokens
from aletheia_lab.hosted_auth.postgres_sessions import PostgresSessionStore
from aletheia_lab.hosted_auth.sessions import ServerSessions
from aletheia_lab.hosted_auth.settings import AuthPolicy

pytestmark = [pytest.mark.integration, pytest.mark.skipif(not os.environ.get("K04_TEST_DATABASE_URL"),
    reason="Explicit K-04 PostgreSQL test URL required")]


def test_real_legacy_schema_controls_return_503_preserve_normal_job_and_receipt(tmp_path):
    async def run():
        async with _database() as (pool, _):
            tokens = CsrfTokens(SECRET)
            digest, headers = await _http_identity(pool, "owner", "a" * 43, tokens)
            workspace = (await WorkspaceManagement(pool).create(credential_digest=digest, name="Legacy"))["workspace_id"]
            source_data = io.BytesIO()
            with zipfile.ZipFile(source_data, "w") as archive:
                archive.writestr("dataset.csv", "id,target\n1,0\n2,1\n")
            sources = PostgresSources(pool, maximum_upload_bytes=10000, maximum_expanded_bytes=20000, maximum_entries=10)
            source = await sources.put(credential_digest=digest, workspace_id=workspace,
                request_key=SecretStr("synthetic-legacy-source"), payload=source_data.getvalue())
            jobs = PostgresJobs(pool, sources)
            assert await jobs.is_ready() and not await jobs.controls_ready()
            receipt = await jobs.enqueue(credential_digest=digest, workspace_id=workspace, operation="preview",
                request_key=SecretStr("synthetic-legacy-job"), request={"source_id": source.source_id})
            runtime = RuntimeSettings.model_validate({"environment": "local", "public_origin": ORIGIN,
                "identity": {"issuer": "https://issuer.example.invalid", "client_id": "synthetic",
                    "redirect_uri": ORIGIN + "/v1/auth/callback", "client_kind": "public"},
                "storage": {"database_url_env": "ALETHEIA_HOSTED_DATABASE_URL", "object_root": str(tmp_path)}})
            sessions = ServerSessions(PostgresSessionStore(pool), AuthPolicy(default_return_to="/", return_to_paths=("/",)),
                                       timeout_seconds=3, maximum_cookie_bytes=4096)

            async def clock():
                return True

            async def fallback(scope, receive, send):
                raise AssertionError("Unexpected legacy route")

            app = JobApplication(fallback, jobs, sessions, tokens, runtime,
                                  body_limits=BodyLimits(8192, 1, 3, 10), clock_check=clock)
            base = "/v1/workspaces/" + workspace + "/jobs/" + receipt.job_id
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
                for action in ("cancel", "retry"):
                    response = await client.post(base + "/" + action, json={},
                        headers={**headers, "Idempotency-Key": "synthetic-legacy-retry"})
                    assert response.status_code == 503 and response.json()["code"] == "service_unavailable"
                current = await client.get(base, headers=headers)
                assert current.status_code == 200 and current.json()["status"] == "queued"
            async with pool.connection() as conn:
                assert await (await conn.execute("SELECT state,request_id FROM j01_jobs WHERE job_id=%s", (receipt.job_id,))).fetchone() == ("queued", receipt.request_id)
                assert await (await conn.execute("SELECT count(*) FROM j01_jobs")).fetchone() == (1,)

    _run(run())


def test_real_canonical_control_schema_probe_accepts_approved_migration(tmp_path):
    async def run():
        async with _canonical() as pool:
            env = await _setup(pool, tmp_path)
            assert await env.jobs.controls_ready()

    _run(run())
