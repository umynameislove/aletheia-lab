"""Opt-in API-to-worker check in a disposable PostgreSQL schema.

K02_TEST_DATABASE_URL must name an explicitly permitted test database. The test
creates and removes only its own randomly named schema, never existing tables.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import subprocess
import sys
import uuid
import zipfile
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import pytest

ROOT = Path(__file__).resolve().parents[2]
CHILD = r"""
import asyncio
import logging
import os
import sys
import threading
from aletheia_lab.hosted.managed_worker import run_worker

logging.disable(logging.CRITICAL)

async def main():
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    def control():
        sys.stdin.readline()
        loop.call_soon_threadsafe(stop.set)
    threading.Thread(target=control, daemon=True).start()
    print(os.getpid(), flush=True)
    await run_worker(os.environ, stop)

try:
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop if sys.platform == 'win32' else None) as runner:
        runner.run(main())
except Exception:
    print('Worker test failed.', file=sys.stderr)
    raise SystemExit(1)
"""


def _scoped_url(url: str, schema: str) -> str:
    value = urlsplit(url)
    if value.scheme != "postgresql":
        raise ValueError("PostgreSQL test URL required")
    query = [(key, item) for key, item in parse_qsl(value.query) if key != "options"]
    query.append(("options", "-csearch_path=" + schema))
    return urlunsplit(value._replace(query=urlencode(query)))


def _stop_child(child: subprocess.Popen[str]) -> None:
    try:
        child.communicate("stop\n", timeout=15)
    except subprocess.TimeoutExpired:
        child.kill()
        child.communicate(timeout=10)
        raise AssertionError("Worker did not stop within its test deadline") from None
    assert child.returncode == 0, "Worker process failed"


async def _assert_stale_cookies_denied(client, job_id, stale_cookies):
    for stale in stale_cookies:
        denied = await client.get(f"/v1/workspaces/ws_split/jobs/{job_id}", headers={"cookie": stale})
        assert denied.status_code == 401


@pytest.mark.integration
@pytest.mark.skipif(not os.environ.get("K02_TEST_DATABASE_URL"), reason="Explicit PostgreSQL test URL required")
def test_api_jobs_are_completed_by_separate_worker_process(tmp_path):
    import httpx
    import psycopg
    from psycopg import sql
    from psycopg_pool import AsyncConnectionPool
    from pydantic import SecretStr

    from aletheia_lab.hosted.job_http import JobApplication
    from aletheia_lab.hosted.persistence_migration import migrate as migrate_current
    from aletheia_lab.hosted.postgres_jobs import PostgresJobs
    from aletheia_lab.hosted.postgres_sources import PostgresSources
    from aletheia_lab.hosted.request_boundary import BodyLimits
    from aletheia_lab.hosted.settings import load_runtime_settings
    from aletheia_lab.hosted.workspace_migration import migrate_workspace_schema
    from aletheia_lab.hosted_auth.boundary import SESSION_COOKIE
    from aletheia_lab.hosted_auth.csrf import CSRF_COOKIE, CsrfTokens
    from aletheia_lab.hosted_auth.postgres_sessions import PostgresSessionStore
    from aletheia_lab.hosted_auth.session_http import RENEW_PATH, SessionMutationApplication
    from aletheia_lab.hosted_auth.sessions import ServerSessions, credential_digest
    from aletheia_lab.hosted_auth.settings import load_auth_policy

    async def run():
        database = os.environ["K02_TEST_DATABASE_URL"]
        schema = "k02_split_" + uuid.uuid4().hex
        scoped = _scoped_url(database, schema)
        objects = tmp_path / "objects"
        objects.mkdir()
        runtime_file, policy_file = tmp_path / "runtime.toml", tmp_path / "auth.toml"
        runtime_file.write_text(
            'environment = "local"\npublic_origin = "https://app.example.invalid"\n'
            '[identity]\nissuer = "https://identity.example.invalid"\nclient_id = "synthetic"\n'
            'redirect_uri = "https://app.example.invalid/v1/auth/callback"\nclient_kind = "public"\n'
            '[storage]\ndatabase_url_env = "ALETHEIA_HOSTED_DATABASE_URL"\n'
            f'object_root = {json.dumps(str(objects))}\n', encoding="utf-8")
        policy_file.write_text('default_return_to = "/app"\nreturn_to_paths = ["/app"]\n', encoding="utf-8")
        runtime, policy = load_runtime_settings(runtime_file), load_auth_policy(policy_file)
        token = SecretStr("x" * 43)
        digest = credential_digest(token)
        tokens = CsrfTokens(SecretStr("synthetic-signing-key-" + "s" * 43))
        proof = tokens.issue("ctx_split")
        created, child = False, None
        async with await psycopg.AsyncConnection.connect(database, autocommit=True) as admin:
            try:
                await admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
                created = True
                await admin.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
                # Managed workers require the maintained execution profile, never an
                # implicit fallback to the three historical experimental SQL files.
                async with await psycopg.AsyncConnection.connect(scoped) as migration:
                    assert await migrate_current(migration, schema) == "created"
                # The maintained owner invariant is deferred to this seed transaction.
                async with admin.transaction():
                    await admin.execute(
                        "INSERT INTO hosted_identity_bindings VALUES "
                        "('issuer','subject','user_split','Synthetic','u@example.invalid',true,true,0,'ready','ws_split',null)")
                    await admin.execute("INSERT INTO j01_workspaces VALUES ('ws_split','Synthetic workspace')")
                    await admin.execute("INSERT INTO j01_memberships VALUES ('ws_split','user_split','analyst',true,1)")
                    await admin.execute("INSERT INTO hosted_identity_bindings VALUES "
                        "('issuer','subject-owner','owner_split','Synthetic owner','owner@example.invalid',true,true,0,'ready','ws_split',null)")
                    await admin.execute("INSERT INTO j01_memberships VALUES ('ws_split','owner_split','owner',true,1)")
                    await admin.execute(
                        "INSERT INTO j01_sessions VALUES ('ctx_split','user_split',%s,'active',"
                        "statement_timestamp()-interval '500 seconds',statement_timestamp()-interval '400 seconds',"
                        "statement_timestamp()-interval '400 seconds',statement_timestamp()+interval '900 seconds',"
                        "statement_timestamp()+interval '28000 seconds',0,0)", (proof.get_secret_value(),))
                    await admin.execute("INSERT INTO j01_session_credentials VALUES (%s,'ctx_split','active',0)",
                                        (digest.get_secret_value(),))
                async with await psycopg.AsyncConnection.connect(scoped) as migration:
                    assert await migrate_workspace_schema(migration, schema) == "unchanged"
                async with AsyncConnectionPool(scoped, min_size=1, max_size=2, open=False) as pool:
                    await pool.wait(timeout=10)
                    sources = PostgresSources(pool, maximum_upload_bytes=10000,
                                              maximum_expanded_bytes=10000, maximum_entries=10)
                    jobs = PostgresJobs(pool, sources, execution=True)
                    assert await jobs.execution_ready()
                    sessions = ServerSessions(PostgresSessionStore(pool), policy, timeout_seconds=3,
                                              maximum_cookie_bytes=4096)
                    buffer = io.BytesIO()
                    with zipfile.ZipFile(buffer, "w") as archive:
                        archive.writestr("dataset.csv", "id,target\n1,0\n2,1\n")
                        archive.writestr("metrics.csv", "run,name,value\nbaseline,loss,0.5\ncandidate,loss,0.6\n")
                        archive.writestr("config.json", '{"seed":42}\n')
                    payload = buffer.getvalue()
                    source = await sources.put(credential_digest=digest, workspace_id="ws_split",
                                               request_key=SecretStr("source-split-key-0001"), payload=payload)

                    async def fallback(scope, receive, send):
                        raise AssertionError("Unexpected test route")

                    async def clock():
                        return True

                    app = JobApplication(fallback, jobs, sessions, tokens, runtime,
                                         body_limits=BodyLimits(10000, 2, 3, 20), clock_check=clock)
                    app = SessionMutationApplication(app, sessions, tokens, runtime,
                        body_limits=BodyLimits(10000, 2, 3, 20), clock_check=clock)
                    headers = {"origin": runtime.public_origin, "x-csrf-token": proof.get_secret_value(),
                               "cookie": f"{SESSION_COOKIE}={token.get_secret_value()}; {CSRF_COOKIE}={proof.get_secret_value()}"}
                    child_env = {key: os.environ[key] for key in ("SYSTEMROOT", "WINDIR", "TEMP", "TMP", "PYTHONPATH")
                                 if key in os.environ}
                    child_env.update(ALETHEIA_HOSTED_CONFIG=str(runtime_file),
                                     ALETHEIA_HOSTED_AUTH_POLICY=str(policy_file), ALETHEIA_HOSTED_DATABASE_URL=scoped)
                    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                                 base_url=runtime.public_origin, headers=headers) as client:
                        stale_cookies = []
                        for attempt in range(2):
                            receipt = await client.post("/v1/workspaces/ws_split/jobs",
                                headers={"idempotency-key": f"preview-split-key-{attempt:04d}"},
                                json={"operation": "preview", "source_id": source.source_id})
                            assert receipt.status_code == 202
                            job_id = receipt.json()["job_id"]
                            assert (await jobs.status(credential_digest=digest, workspace_id="ws_split",
                                                      job_id=job_id)).status == "queued"
                            # Age only this synthetic session to enter its renewal window.
                            await admin.execute("UPDATE j01_sessions SET rotated_at=statement_timestamp()-interval '400 seconds', "
                                "last_seen_at=statement_timestamp()-interval '400 seconds' WHERE context_id='ctx_split'")
                            stale_cookies.append(client.headers["cookie"])
                            renewed = await client.post(RENEW_PATH, json={})
                            assert renewed.status_code == 200
                            assert renewed.json()["session"]["context_id"] == "ctx_split"
                            current = renewed.cookies.get(SESSION_COOKIE)
                            assert current and current != token.get_secret_value()
                            token = SecretStr(current)
                            digest = credential_digest(token)
                            client.headers["cookie"] = f"{SESSION_COOKIE}={current}; {CSRF_COOKIE}={proof.get_secret_value()}"
                            await _assert_stale_cookies_denied(client, job_id, stale_cookies)
                            child = subprocess.Popen([sys.executable, "-B", "-c", CHILD], cwd=ROOT,
                                env=child_env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True)
                            pid = await asyncio.wait_for(asyncio.to_thread(child.stdout.readline), 10)
                            # Windows venv launchers can start a second interpreter PID.
                            assert int(pid.strip()) > 0 and int(pid.strip()) != os.getpid()
                            async with asyncio.timeout(30):
                                while True:
                                    assert child.poll() is None, "Worker stopped before completing the job"
                                    status = await client.get(f"/v1/workspaces/ws_split/jobs/{job_id}")
                                    assert status.status_code == 200
                                    if status.json()["status"] in ("succeeded", "failed"):
                                        assert status.json()["status"] == "succeeded", "Worker failed the preview"
                                        break
                                    await asyncio.sleep(0.1)
                            output = await client.get(f"/v1/workspaces/ws_split/jobs/{job_id}/output")
                            assert output.status_code == 200
                            assert output.json()["result"]["source_sha256"] == hashlib.sha256(payload).hexdigest()
                            assert output.json()["result"]["mapping_candidates"]
                            await asyncio.to_thread(_stop_child, child)
                            child = None
            finally:
                try:
                    if child is not None:
                        await asyncio.to_thread(_stop_child, child)
                finally:
                    if created:
                        await admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))

    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else None) as runner:
        runner.run(run())
