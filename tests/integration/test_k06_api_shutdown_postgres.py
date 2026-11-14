"""Native composed API processes drain/reconcile against one owned PostgreSQL schema.

This exercises the existing seeded-session HTTP composition and real Uvicorn
SIGTERM handling. Full ManagedRuntime/OIDC startup is deliberately not exercised.
"""
from __future__ import annotations

import asyncio
import json
import os
import secrets
import signal
import socket
import ssl
import subprocess
import sys
from importlib.resources import files
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr
from test_k02_https_edge import _certificate
from test_k05_native_results_postgres import _case, _run
from test_k06_canonical_snapshot_postgres import _setup
from test_k06_follow_up_http_postgres import _identity
from test_k06_runtime_privileges_postgres import _role_url

from aletheia_lab.hosted.runtime_privileges import grant_runtime
from aletheia_lab.hosted_auth.csrf import CsrfTokens
from aletheia_lab.hosted_contract.job_page import JobReceipt

pytestmark = [pytest.mark.integration, pytest.mark.skipif(
    not os.environ.get("K06_TEST_DATABASE_URL") or os.name != "posix",
    reason="Explicit native PostgreSQL and POSIX signal opt-in required")]
ORIGIN = "https://app.example.invalid"
_API = r'''
import asyncio,json,os,sys
from pathlib import Path
import uvicorn
from pydantic import SecretStr
from psycopg_pool import AsyncConnectionPool
from aletheia_lab.hosted.object_store import FilesystemObjectStore
from aletheia_lab.hosted.postgres_catalog import PostgresCatalog
from aletheia_lab.hosted.postgres_objects import PostgresObjects
from aletheia_lab.hosted.postgres_jobs import PostgresJobs
from aletheia_lab.hosted.postgres_sources import PostgresSources
from aletheia_lab.hosted.postgres_workspaces import PostgresWorkspaceAccess
from aletheia_lab.hosted.runtime import api_server_options
from aletheia_lab.hosted.runtime_binding import verify_runtime_binding
from aletheia_lab.hosted.settings import RuntimeSettings
from aletheia_lab.hosted.upload_runtime import compose_upload_runtime
from aletheia_lab.hosted_auth.clock_guard import probe_clock
from aletheia_lab.hosted_auth.csrf import CsrfTokens
from aletheia_lab.hosted_auth.postgres_sessions import PostgresSessionStore
from aletheia_lab.hosted_auth.abuse import AuthAdmission,RatePolicy
from aletheia_lab.hosted_auth.postgres_admission import PostgresAuthAdmission
from aletheia_lab.hosted_auth.sessions import ServerSessions
from aletheia_lab.hosted_auth.settings import AuthPolicy
from aletheia_lab.hosted_contract.session_capabilities import Limits

async def main():
    config,cert,key,marker=sys.argv[1:]
    settings=RuntimeSettings.model_validate_json(Path(config).read_bytes())
    async with AsyncConnectionPool(os.environ['ALETHEIA_HOSTED_DATABASE_URL'],min_size=0,max_size=2,open=False) as pool:
        await pool.wait(timeout=10)
        assert await verify_runtime_binding(pool,settings.storage)
        policy=AuthPolicy(default_return_to='/',return_to_paths=('/',))
        sessions=ServerSessions(PostgresSessionStore(pool),policy,timeout_seconds=5,maximum_cookie_bytes=4096)
        objects=PostgresObjects(pool,FilesystemObjectStore(Path(settings.storage.object_root)/'catalog'))
        catalog=PostgresCatalog(pool,objects)
        sources=PostgresSources(pool,maximum_upload_bytes=10000,maximum_expanded_bytes=20000,
                               maximum_entries=10,audit=True,catalog=catalog)
        jobs=PostgresJobs(pool,sources,audit=True,catalog=catalog)
        async def clock():
            return await jobs.is_ready() and await probe_clock(pool,max_skew_seconds=30,timeout_seconds=2)
        assert await clock()
        rate_policy=RatePolicy(100,50,60)
        rate_store=PostgresAuthAdmission(pool,namespace='native-api-lifecycle')
        await rate_store.prepare(rate_policy)
        # Login routes are not called: no IdP discovery/JWKS service or provider is started.
        composed=compose_upload_runtime(settings=settings,sessions=sessions,
            workspaces=PostgresWorkspaceAccess(pool,sessions),sources=sources,jobs=jobs,
            tokens=CsrfTokens(SecretStr(os.environ['ALETHEIA_HOSTED_CSRF_SECRET'])),
            coordinator=object(),auth_policy=policy,
            limits=Limits(question_chars=2000,upload_bundle_bytes=10000,page_max=200,job_poll_min_ms=2000),
            clock_check=clock,admission=AuthAdmission(rate_store,rate_policy,timeout_seconds=5),
            admission_subject_key=SecretStr(os.environ['ALETHEIA_HOSTED_CSRF_SECRET']))
        async def app(scope,receive,send):
            async def tracked(event):
                if event.get('type')=='lifespan.shutdown.complete':
                    await pool.close(timeout=5)
                    Path(marker).write_text(json.dumps({'pool_close_completed':True,
                        'shutdown_event':'lifespan.shutdown.complete','pid':os.getpid()}))
                await send(event)
            await composed(scope,receive,tracked if scope.get('type')=='lifespan' else send)
        options=api_server_options(settings)
        options.update(ssl_certfile=cert,ssl_keyfile=key,log_config=None,log_level='warning')
        server=uvicorn.Server(uvicorn.Config(app,**options))
        await server.serve()
        assert server.started
asyncio.run(main())
'''


def _port():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def _application_url(database, name):
    parsed = urlsplit(database)
    pairs = [(key, value) for key, value in parse_qsl(parsed.query) if key != "application_name"]
    return urlunsplit(parsed._replace(query=urlencode([*pairs, ("application_name", name)])))


@pytest.fixture(autouse=True)
def configured_database(monkeypatch):
    monkeypatch.setenv("K05_TEST_DATABASE_URL", os.environ["K06_TEST_DATABASE_URL"])


@pytest.mark.parametrize("lose_response", [False, True], ids=["graceful-ack", "client-disconnect"])
def test_native_api_sigterm_drain_two_process_replay_and_restart(tmp_path: Path, lose_response):
    import psycopg
    from psycopg import sql

    from aletheia_lab.hosted.object_store import FilesystemObjectStore
    from aletheia_lab.hosted.postgres_catalog import PostgresCatalog
    from aletheia_lab.hosted.postgres_objects import PostgresObjects

    async def scenario():
        database = os.environ["K06_TEST_DATABASE_URL"]
        role = "k06_api_" + uuid4().hex
        password, csrf_secret = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
        created_role = False
        processes = []
        key = None
        observation = {"client_disconnect": lose_response, "scope": "Seeded-session composed API; no ManagedRuntime/IdP startup"}
        try:
            async with _case(tmp_path) as case:
                private = tmp_path / "runtime"
                private.mkdir()
                case.catalog = PostgresCatalog(case.pool, PostgresObjects(case.pool, FilesystemObjectStore(private / "catalog")))
                jobs = await _setup(case)
                async with case.pool.connection() as conn:
                    schema = (await (await conn.execute("SELECT current_schema()")).fetchone())[0]
                async with await psycopg.AsyncConnection.connect(database, autocommit=True) as admin:
                    await admin.execute(sql.SQL("CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
                        "NOINHERIT NOBYPASSRLS PASSWORD {}").format(sql.Identifier(role), sql.Literal(password)))
                    created_role = True
                async with case.pool.connection() as conn:
                    await conn.execute(files('aletheia_lab.hosted_auth').joinpath('admission_schema.sql').read_text())
                    await grant_runtime(conn, schema, role, profile="api", audit=True)
                headers = await _identity(case, "a", secrets.token_urlsafe(32), CsrfTokens(SecretStr(csrf_secret)))
                headers["host"] = "app.example.invalid"
                cert, key = _certificate(tmp_path)
                key.chmod(0o600)
                tls = ssl.create_default_context(cafile=str(cert))
                ports = [_port(), _port()]
                names = ["k06-api-a-" + uuid4().hex, "k06-api-b-" + uuid4().hex,
                         "k06-api-restart-" + uuid4().hex]
                markers = [tmp_path / (name + ".closed.json") for name in names]
                root = Path(__file__).resolve().parents[2]

                def start(index, port):
                    config = tmp_path / (names[index] + ".json")
                    config.write_text(json.dumps({"environment": "local", "public_origin": ORIGIN,
                        "identity": {"issuer": "https://issuer.example.invalid", "client_id": "synthetic-no-idp",
                            "redirect_uri": ORIGIN + "/v1/auth/callback", "client_kind": "public"},
                        "storage": {"database_url_env": "ALETHEIA_HOSTED_DATABASE_URL", "object_root": str(private),
                            "expected_schema": schema, "expected_database_role": role},
                        "api": {"port": port, "database_pool_size": 2, "graceful_shutdown_seconds": 10}}), encoding="utf-8")
                    config.chmod(0o600)
                    environment = {key: os.environ[key] for key in ("SYSTEMROOT", "WINDIR", "TEMP", "TMP") if key in os.environ}
                    environment.update(PYTHONPATH=str(root / "src"), ALETHEIA_HOSTED_CSRF_SECRET=csrf_secret,
                        ALETHEIA_HOSTED_DATABASE_URL=_application_url(_role_url(database, schema, role, password), names[index]))
                    child = subprocess.Popen([sys.executable, "-B", "-c", _API, str(config), str(cert), str(key), str(markers[index])],
                        cwd=root, env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    processes.append(child)
                    return child

                async def wait_ready(child, client):
                    async with asyncio.timeout(15):
                        while True:
                            if child.poll() is not None:
                                raise AssertionError(child.communicate()[1].decode(errors="replace")[-1500:])
                            try:
                                if (await client.get("/health/ready")).status_code == 200:
                                    return
                            except httpx.HTTPError:
                                pass
                            await asyncio.sleep(0.05)

                async def finish(child, index):
                    output = await asyncio.wait_for(asyncio.to_thread(child.communicate), 15)
                    # Uvicorn restores/reraises POSIX SIGTERM after lifespan shutdown.
                    assert child.returncode in (0, -signal.SIGTERM), output[1].decode(errors="replace")[-1500:]
                    marker = json.loads(markers[index].read_text())
                    assert marker == {"pool_close_completed": True, "shutdown_event": "lifespan.shutdown.complete", "pid": child.pid}
                    async with case.pool.connection() as conn:
                        assert await (await conn.execute("SELECT count(*) FROM pg_stat_activity WHERE application_name=%s", (names[index],))).fetchone() == (0,)
                    return child.returncode

                first = start(0, ports[0])
                second = start(1, ports[1])
                async with httpx.AsyncClient(base_url=f"https://127.0.0.1:{ports[0]}", headers=headers, verify=tls, trust_env=False, timeout=15) as a, \
                        httpx.AsyncClient(base_url=f"https://127.0.0.1:{ports[1]}", headers=headers, verify=tls, trust_env=False, timeout=15) as b:
                    await asyncio.gather(wait_ready(first, a), wait_ready(second, b))
                    body = {"operation": "confirm", "source_id": case.receipt.source_id,
                            "mapping": case.snapshot.mapping.model_dump(mode="json")}
                    request_key = "k06-native-api-drain-replay"
                    mutation_headers = {"idempotency-key": request_key}
                    async with case.pool.connection() as held:
                        await held.execute("SELECT workspace_id FROM j01_workspaces WHERE workspace_id='a' FOR UPDATE")
                        pending = asyncio.create_task(a.post("/v1/workspaces/a/jobs", headers=mutation_headers, json=body))
                        async with asyncio.timeout(10):
                            while True:
                                async with case.pool.connection() as conn:
                                    waiting = (await (await conn.execute("SELECT count(*) FROM pg_stat_activity WHERE application_name=%s AND wait_event_type='Lock'", (names[0],))).fetchone())[0]
                                if waiting:
                                    break
                                assert not pending.done()
                                await asyncio.sleep(0.01)
                        assert (await b.get("/health/ready")).status_code == 200
                        if lose_response:
                            pending.cancel()
                            await asyncio.gather(pending, return_exceptions=True)
                        first.send_signal(signal.SIGTERM)
                        await asyncio.sleep(0.2)
                        assert first.poll() is None
                        await held.commit()
                    accepted = None if lose_response else await pending
                    if accepted is not None:
                        assert accepted.status_code == 202, accepted.text
                    exit_a = await finish(first, 0)
                    async with case.pool.connection() as conn:
                        durable = await (await conn.execute("SELECT job_id,state FROM j01_jobs")).fetchall()
                        assert len(durable) == 1 and durable[0][1] == "queued"
                        assert await (await conn.execute("SELECT count(*) FROM k06_action_audit WHERE action='job.admit'")).fetchone() == (1,)
                    replay = await b.post("/v1/workspaces/a/jobs", headers=mutation_headers, json=body)
                    assert replay.status_code == 202, replay.text
                    receipt = JobReceipt.model_validate_json(replay.content)
                    assert receipt.job_id == durable[0][0]
                    if accepted is not None:
                        assert JobReceipt.model_validate_json(accepted.content) == receipt
                    async with case.pool.connection() as conn:
                        assert await (await conn.execute("SELECT state,count(*) FROM j01_jobs GROUP BY state")).fetchall() == [("queued", 1)]
                        assert await (await conn.execute("SELECT count(*) FROM k06_action_audit WHERE action='job.admit'")).fetchone() == (1,)
                    lease = SecretStr("7" * 64)
                    claim = await jobs.claim(lease_digest=lease, lease_seconds=120, operations=("confirm",))
                    assert claim is not None and claim.job_id == receipt.job_id
                    assert await jobs.complete_snapshot(claim=claim, lease_digest=lease, snapshot=case.snapshot)
                    assert not await jobs.complete_snapshot(claim=claim, lease_digest=lease, snapshot=case.snapshot)
                    terminal = await b.get(f"/v1/workspaces/a/jobs/{receipt.job_id}/output")
                    assert terminal.status_code == 200 and terminal.json()["result"]["snapshot_id"] == case.snapshot.snapshot_id
                    restarted = start(2, ports[0])
                    async with httpx.AsyncClient(base_url=f"https://127.0.0.1:{ports[0]}", headers=headers, verify=tls, trust_env=False, timeout=15) as again:
                        await wait_ready(restarted, again)
                        recovered = await again.post("/v1/workspaces/a/jobs", headers=mutation_headers, json=body)
                        assert recovered.status_code == 202 and JobReceipt.model_validate_json(recovered.content) == receipt
                        assert (await again.get(f"/v1/workspaces/a/jobs/{receipt.job_id}/output")).json() == terminal.json()
                        different = {**body, "mapping": {**body["mapping"], "baseline_run_id": "candidate"}}
                        conflict = await again.post("/v1/workspaces/a/jobs", headers=mutation_headers, json=different)
                        assert conflict.status_code == 409 and conflict.json()["code"] == "idempotency_conflict"
                    restarted.send_signal(signal.SIGTERM)
                    exit_restart = await finish(restarted, 2)
                    second.send_signal(signal.SIGTERM)
                    exit_b = await finish(second, 1)
                    async with case.pool.connection() as conn:
                        assert await (await conn.execute("SELECT state,count(*) FROM j01_jobs GROUP BY state")).fetchall() == [("succeeded", 1)]
                        assert await (await conn.execute("SELECT count(*) FROM k05_records WHERE record_type='upload_snapshot'")).fetchone() == (1,)
                        assert await (await conn.execute("SELECT revision FROM k05_projects")).fetchone() == (1,)
                        assert await (await conn.execute("SELECT action,count(*) FROM k06_action_audit GROUP BY action ORDER BY action")).fetchall() == [("job.admit", 1), ("job.publish", 1)]
                    observation.update(status="passed", api_pids=[first.pid, second.pid, restarted.pid],
                        signal_exit_codes=[exit_a, exit_b, exit_restart], all_lifespans_closed_pools=True,
                        all_process_db_sessions_removed=True, job_id=receipt.job_id,
                        admission_committed_before_replay=True,
                        receipt_reconciled_across_api_and_restart=True, source_id=case.receipt.source_id,
                        snapshot_id=case.snapshot.snapshot_id, canonical_snapshots=1, current_revision=1,
                        admission_audits=1, publication_audits=1, logical_origin=ORIGIN)
        finally:
            try:
                for child in processes:
                    if child.poll() is None:
                        child.kill()
                    await asyncio.to_thread(child.communicate)
                if created_role:
                    async with await psycopg.AsyncConnection.connect(database, autocommit=True) as admin:
                        assert role.startswith("k06_api_") and len(role) == 40
                        await admin.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))
                        assert await (await admin.execute("SELECT to_regnamespace(%s),EXISTS(SELECT 1 FROM pg_roles WHERE rolname=%s)", (schema, role))).fetchone() == (None, False)
                        observation.update(owned_schema_removed=True, owned_role_removed=True)
            finally:
                if key is not None:
                    key.unlink(missing_ok=True)
        observation["private_tls_key_removed"] = True
        (tmp_path / "api-lifecycle-observation.json").write_text(json.dumps(observation, indent=2) + "\n", encoding="utf-8")
    _run(scenario())
