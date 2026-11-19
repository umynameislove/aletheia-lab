"""Opt-in J-02 API/DB checks; default pytest runs only isolation guards.

Uses the real composed ASGI runtime, PostgreSQL, CSRF and shared admission.
The IdP and repository-publication results are synthetic; the rotation case
also runs a bounded real compute child. No hosted browser, RLS, export,
invitation, deployment or joint production acceptance is claimed. Direct CLI
execution requires K03_DB_PASSWORD and supports the K03_DB_PORT override;
the private fixture runner uses its own ephemeral PostgreSQL instead.
"""

import asyncio
import os
import re
import sys
import tempfile
import traceback
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import monotonic
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from pydantic import SecretBytes, SecretStr
from test_j01_source_bundle import bundle
from test_k03_login_coordinator import (
    ISSUER,
    POLICY,
    SyntheticOidc,
    _cookie,
    _identity,
    _query,
    _runtime,
)
from test_k03_postgres_environment import database_port

from aletheia_lab.hosted.application import AccessDenied
from aletheia_lab.hosted.postgres_jobs import PostgresJobs
from aletheia_lab.hosted.postgres_sources import PostgresSources, SourceNotFound
from aletheia_lab.hosted.postgres_workspaces import PostgresWorkspaceAccess
from aletheia_lab.hosted.upload_runtime import compose_upload_runtime
from aletheia_lab.hosted_auth.abuse import AuthAdmission, RatePolicy
from aletheia_lab.hosted_auth.boundary import SESSION_COOKIE
from aletheia_lab.hosted_auth.clock_guard import probe_clock
from aletheia_lab.hosted_auth.csrf import CsrfTokens
from aletheia_lab.hosted_auth.identity import AccountResolver
from aletheia_lab.hosted_auth.login import LoginCoordinator, LoginRejected
from aletheia_lab.hosted_auth.login_proof_cipher import LoginProofCipher
from aletheia_lab.hosted_auth.postgres_admission import PostgresAuthAdmission
from aletheia_lab.hosted_auth.postgres_fresh_login import PostgresFreshLogin
from aletheia_lab.hosted_auth.postgres_identity import PostgresIdentityDirectory
from aletheia_lab.hosted_auth.postgres_sessions import PostgresSessionStore
from aletheia_lab.hosted_auth.sessions import RenewalConflict, ServerSessions, credential_digest
from aletheia_lab.hosted_auth.worker_sessions import WorkerPostgresSources
from aletheia_lab.hosted_contract.session_capabilities import Limits

_PAYLOAD = bundle([("dataset.csv", b"id,target\n1,0\n2,1\n"),
                   ("metrics.csv", b"run,name,value\nbaseline,loss,0.5\ncandidate,loss,0.6\n"),
                   ("config.json", b'{"seed":42}\n')])
_ROOT = Path(__file__).resolve().parents[2]


def _schema_name(name):
    if not isinstance(name, str) or not re.fullmatch(r"k03_j02_[0-9a-f]{32}", name):
        raise ValueError("Only the exact isolated K-03 J-02 schema is allowed.")
    return name


@asynccontextmanager
async def _running(app):
    incoming, outgoing = asyncio.Queue(), asyncio.Queue()
    await incoming.put({"type": "lifespan.startup"})
    task = asyncio.create_task(app({"type": "lifespan"}, incoming.get, outgoing.put))
    try:
        async with asyncio.timeout(15):
            assert (await outgoing.get())["type"] == "lifespan.startup.complete"
        yield
    finally:
        if not task.done():
            await incoming.put({"type": "lifespan.shutdown"})
            async with asyncio.timeout(15):
                assert (await outgoing.get())["type"] == "lifespan.shutdown.complete"
                await task
        else:
            await task


async def _system(pool, cipher, tokens):
    runtime, provider = _runtime(_ROOT), SyntheticOidc()
    sessions = ServerSessions(PostgresSessionStore(pool), POLICY, timeout_seconds=5.0, maximum_cookie_bytes=4096)
    sources = PostgresSources(pool, maximum_upload_bytes=4096, maximum_expanded_bytes=4096, maximum_entries=10)
    jobs = PostgresJobs(pool, sources)
    worker_sources = WorkerPostgresSources(pool, maximum_upload_bytes=4096, maximum_expanded_bytes=4096, maximum_entries=10)
    worker_jobs = PostgresJobs(pool, worker_sources)

    async def clock():
        return await probe_clock(pool, max_skew_seconds=30, timeout_seconds=2)

    flow = LoginCoordinator(PostgresFreshLogin(pool, cipher, clock_skew_seconds=30), provider,
        AccountResolver(PostgresIdentityDirectory(pool), POLICY, expected_issuer=ISSUER, timeout_seconds=2),
        runtime, POLICY, csrf_factory=tokens.issue, timeout_seconds=5.0)
    rate = RatePolicy(1000, 1000, 60)
    admission_store = PostgresAuthAdmission(pool, namespace="k03-j02-test")
    await admission_store.prepare(rate)
    app = compose_upload_runtime(settings=runtime, sessions=sessions,
        workspaces=PostgresWorkspaceAccess(pool, sessions), sources=sources, jobs=jobs, tokens=tokens,
        coordinator=flow, auth_policy=POLICY,
        limits=Limits(question_chars=2000, upload_bundle_bytes=4096, page_max=200, job_poll_min_ms=2000),
        clock_check=clock, admission=AuthAdmission(admission_store, rate, timeout_seconds=2.0),
        admission_subject_key=SecretStr("synthetic-j02-admission-" + "r" * 43))
    return SimpleNamespace(app=app, provider=provider, sessions=sessions, sources=sources, jobs=jobs,
                           worker_sources=worker_sources, worker_jobs=worker_jobs, runtime=runtime)


def _start_observation(response, elapsed):
    media = response.headers.get("content-type", "").partition(";")[0].lower().strip()
    media = media if media in ("application/problem+json", "application/json", "text/plain", "text/html", "") else "other"
    code = "none"
    if media in ("application/problem+json", "application/json") and len(response.content) <= 4096:
        try:
            value = response.json()
        except ValueError:
            value = None
        if isinstance(value, dict):
            candidate = value.get("code")
            allowed = ("rate_limited", "service_unavailable", "service_failure", "invalid_request",
                       "authentication_required", "access_denied")
            code = candidate if isinstance(candidate, str) and candidate in allowed else "other"
    return {"STATUS": response.status_code, "MEDIA": media or "missing", "CODE": code,
            "ELAPSED_MS": max(0, int(elapsed * 1000))}


async def _begin(client, *, return_to="/app"):
    started = monotonic()
    response = await client.get("/v1/auth/start", params={"return_to": return_to},
                                headers={"sec-fetch-site": "same-origin"})
    if response.status_code != 303:
        for field, value in _start_observation(response, monotonic() - started).items():
            print("K03_J02_START_UNEXPECTED_" + field + "=" + str(value), flush=True)
    assert response.status_code == 303
    return parse_qs(urlsplit(response.headers["location"]).query)["state"][0]


async def _callback(client, state):
    return await client.get("/v1/auth/callback", params={"state": state, "iss": ISSUER, "code": "synthetic-code"})


async def _login(client, system, suffix):
    system.provider.identity = _identity("subject-" + suffix)
    completed = await _callback(client, await _begin(client))
    assert completed.status_code == 303 and completed.headers["location"] == "/app"
    response = await client.get("/v1/session")
    assert response.status_code == 200
    assert response.json()["actor"]["user_id"] == "usr_synthetic_" + suffix
    return response.json()


def _headers(system, session):
    return {"origin": system.runtime.public_origin, "sec-fetch-site": "same-origin",
            "x-csrf-token": session["csrf_token"], "idempotency-key": uuid.uuid4().hex}


async def _upload(client, system, session, workspace, *, status=201, extra=None):
    headers = {**_headers(system, session), "content-type": "application/zip", **(extra or {})}
    response = await client.post(f"/v1/workspaces/{workspace}/sources", content=_PAYLOAD, headers=headers)
    assert response.status_code == status
    assert response.headers["cache-control"] == "no-store"
    return response


async def _enqueue(client, system, session, workspace, source, *, status=202, extra=None):
    response = await client.post(f"/v1/workspaces/{workspace}/jobs",
        json={"operation": "preview", "source_id": source, **(extra or {})}, headers=_headers(system, session))
    if response.status_code != status:
        print("K03_J02_ENQUEUE_EXPECTED_ACTUAL=" + str(status) + ":" + str(response.status_code), flush=True)
    assert response.status_code == status
    assert response.headers["cache-control"] == "no-store"
    return response


async def _count(pool, table):
    assert table in ("j01_sources", "j01_jobs", "j01_sessions", "j01_upload_snapshots")
    async with pool.connection() as connection:
        return (await (await connection.execute("SELECT count(*) FROM " + table)).fetchone())[0]


async def _membership(pool, *, role="owner", active=True, revision):
    async with pool.connection() as connection:
        await connection.execute("SELECT user_id FROM hosted_identity_bindings WHERE user_id='usr_synthetic_a' FOR UPDATE")
        await connection.execute("SELECT workspace_id FROM j01_workspaces WHERE workspace_id='ws_a' FOR UPDATE")
        await connection.execute("UPDATE j01_memberships SET role=%s,active=%s,revision=%s "
                                 "WHERE workspace_id='ws_a' AND user_id='usr_synthetic_a'", (role, active, revision))


async def _seed_read_job(client, system, session, workspace):
    source = (await _upload(client, system, session, workspace)).json()["source_id"]
    receipt = (await _enqueue(client, system, session, workspace, source)).json()
    lease = SecretStr("a" * 64)
    claim = await system.worker_jobs.claim(lease_digest=lease, lease_seconds=60)
    assert claim.job_id == receipt["job_id"]
    assert await system.worker_jobs.complete(claim=claim, lease_digest=lease, result={"synthetic_private": workspace})
    return source, receipt["job_id"]


async def _roles(client, system, pool, session, source, job):
    digest = credential_digest(SecretStr(client.cookies.get(SESSION_COOKIE)))
    for revision, role in enumerate(("owner", "admin", "analyst", "reviewer", "viewer"), 1):
        await _membership(pool, role=role, revision=revision)
        response = await client.get("/v1/workspaces/ws_a/capabilities", headers={"x-role": "owner"})
        assert response.status_code == 200
        caps = response.json()
        assert caps["membership"] == {"role": role, "revision": revision}
        writes = role in ("owner", "admin", "analyst")
        assert caps["allowed_actions"] == (["import", "analyze"] if writes else [])
        assert caps["supported_export_formats"] == []
        assert (await client.get(f"/v1/workspaces/ws_a/jobs/{job}")).status_code == 200
        output = await client.get(f"/v1/workspaces/ws_a/jobs/{job}/output")
        assert output.status_code == 200 and output.json()["result"] == {"synthetic_private": "ws_a"}
        assert await system.sources.read(credential_digest=digest, workspace_id="ws_a", source_id=source) == _PAYLOAD
        before = await _count(pool, "j01_sources")
        await _upload(client, system, session, "ws_a", status=201 if writes else 404, extra={"x-role": "owner"})
        assert await _count(pool, "j01_sources") == before + int(writes)
        if not writes:
            before = await _count(pool, "j01_jobs")
            await _enqueue(client, system, session, "ws_a", source, status=404)
            assert await _count(pool, "j01_jobs") == before
    print("K03_J02_FIVE_ROLES_CURRENT_CAPS_READ_WRITE_AND_NO_CLIENT_ROLE=PASS", flush=True)


async def _scopes(client, other, system, pool, session, source_a, source_b, job_b):
    await _membership(pool, revision=10)
    for workspace in ("ws_b", "ws_missing"):
        response = await client.get(f"/v1/workspaces/{workspace}/capabilities")
        assert response.status_code == 403 and response.json()["code"] == "permission_denied"
        assert "Private workspace B" not in response.text
    before_sources, before_jobs = await _count(pool, "j01_sources"), await _count(pool, "j01_jobs")
    await _upload(client, system, session, "ws_b", status=404)
    await _enqueue(client, system, session, "ws_a", source_b, status=404)
    await _enqueue(client, system, session, "ws_a", source_a, status=422, extra={"role": "owner"})
    for workspace in ("ws_a", "ws_b"):
        for end in ("", "/output"):
            response = await client.get(f"/v1/workspaces/{workspace}/jobs/{job_b}{end}")
            assert response.status_code == 404 and "synthetic_private" not in response.text
    digest = credential_digest(SecretStr(client.cookies.get(SESSION_COOKIE)))
    with pytest.raises(SourceNotFound):
        await system.sources.read(credential_digest=digest, workspace_id="ws_a", source_id=source_b)
    for headers in ({"x-p6-bridge": "demo", "x-role": "owner", "x-user-id": "usr_synthetic_b"},
                    {"cookie": "session=demo", "authorization": "Bearer demo"}):
        response = await other.get("/v1/session", headers=headers)
        assert response.status_code == 401
    assert await _count(pool, "j01_sources") == before_sources
    assert await _count(pool, "j01_jobs") == before_jobs
    print("K03_J02_FOREIGN_WORKSPACE_SOURCE_JOB_AND_FORGED_IDENTITY_NO_EFFECT=PASS", flush=True)


async def _publication(client, other, system, pool, session, source):
    receipt = (await _enqueue(client, system, session, "ws_a", source)).json()
    lease = SecretStr("b" * 64)
    claim = await system.worker_jobs.claim(lease_digest=lease, lease_seconds=60)
    assert claim.job_id == receipt["job_id"]
    started = monotonic()
    await _membership(pool, active=False, revision=11)
    with pytest.raises(SourceNotFound):
        await system.worker_jobs.complete(claim=claim, lease_digest=lease, result={"synthetic_private": "must-not-publish"})
    for end in ("", "/output"):
        response = await client.get(receipt["status_url"] + end)
        assert response.status_code == 404 and "must-not-publish" not in response.text
    caps = await client.get("/v1/workspaces/ws_a/capabilities")
    assert caps.status_code == 403 and caps.json()["code"] == "membership_revoked"
    elapsed = monotonic() - started
    assert elapsed <= 5
    async with pool.connection() as connection:
        row = await (await connection.execute("SELECT state,result FROM j01_jobs WHERE job_id=%s",
                                              (receipt["job_id"],))).fetchone()
    assert row == ("running", None)
    assert await system.worker_jobs.fail(claim=claim, lease_digest=lease)
    assert (await other.get("/v1/workspaces/ws_b/capabilities")).status_code == 200
    assert (await client.get("/v1/session")).status_code == 200
    await _membership(pool, revision=12)
    print("K03_J02_REVOKE_BEFORE_PUBLICATION_AND_POLL_DENIAL=PASS", flush=True)
    print("K03_J02_LOCAL_MEMBERSHIP_REVOKE_SECONDS=" + format(elapsed, ".3f"), flush=True)


async def _age_context(pool, session):
    async with pool.connection() as connection:
        await connection.execute("UPDATE j01_sessions SET issued_at=statement_timestamp()-interval '600 seconds',"
            "last_seen_at=statement_timestamp()-interval '600 seconds',rotated_at=statement_timestamp()-interval '600 seconds' "
            "WHERE context_id=%s", (session["session"]["context_id"],))


async def _rotate(client, system, pool, session):
    await _age_context(pool, session)
    old = client.cookies.get(SESSION_COOKIE)
    renewal = await system.sessions.renew((SESSION_COOKIE + "=" + old).encode())
    assert renewal.credential is not None
    assert renewal.record.context_id == session["session"]["context_id"]
    client.cookies.set(SESSION_COOKIE, renewal.credential.get_secret_value(), domain="app.example.invalid", path="/")
    assert (await client.get("/v1/session", headers={"cookie": SESSION_COOKIE + "=" + old})).status_code == 401


async def _renew_job_boundary(client, system, pool, session, source):
    receipt = (await _enqueue(client, system, session, "ws_a", source)).json()
    anchor = credential_digest(SecretStr(client.cookies.get(SESSION_COOKIE)))
    for _ in range(2):
        await _rotate(client, system, pool, session)
    lease = SecretStr("c" * 64)
    claim = await system.worker_jobs.claim(lease_digest=lease, lease_seconds=60)
    assert claim.job_id == receipt["job_id"] and claim.credential_digest == anchor
    assert await system.worker_sources.read(credential_digest=anchor, workspace_id="ws_a", source_id=source) == _PAYLOAD
    await _rotate(client, system, pool, session)
    assert await system.worker_sources.read(credential_digest=anchor, workspace_id="ws_a", source_id=source) == _PAYLOAD
    with pytest.raises(SourceNotFound):
        await system.sources.read(credential_digest=anchor, workspace_id="ws_a", source_id=source)
    assert not await system.worker_jobs.complete(claim=claim, lease_digest=SecretStr("f" * 64), result={"must": "not-publish"})
    async with pool.connection() as connection:
        assert await (await connection.execute("SELECT state,result FROM j01_jobs WHERE job_id=%s",
                                               (claim.job_id,))).fetchone() == ("running", None)
    assert await system.worker_jobs.complete(claim=claim, lease_digest=lease, result={"synthetic_private": "after-renew"})
    async with pool.connection() as connection:
        assert await (await connection.execute("SELECT credential_digest,state FROM j01_jobs WHERE job_id=%s",
            (claim.job_id,))).fetchone() == (anchor.get_secret_value(), "succeeded")
    assert (await client.get(receipt["status_url"])).json()["status"] == "succeeded"
    print("K03_J02_WORKER_MULTI_ROTATION_BEFORE_AND_AFTER_CLAIM_WITH_STRICT_API=PASS", flush=True)


def _mapping_and_snapshot(directory, source):
    from aletheia_lab.hosted.upload_project import UploadProject

    project = UploadProject(directory, maximum_upload_bytes=4096, maximum_expanded_bytes=4096, maximum_entries=10)
    captured = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    candidates = project.preview(_PAYLOAD, captured_at=captured)["mapping_candidates"]
    target, metric, config = (candidates[key][0] for key in ("targets", "metric_sources", "configs"))
    mapping = {
        "target": {"mapping_id": target["mapping_id"], "project_item_id": target["project_item_id"],
                   "target_field": "target", "identifier_field": "id"},
        "metric_sources": [{"mapping_id": metric["mapping_id"], "project_item_id": metric["project_item_id"],
                           "format": "csv", "records_path": [], "metric_name_field": "name", "metric_value_field": "value",
                           "run_id_field": "run", "step_field": None}],
        "runs": [{"run_id": "baseline", "config_item_ids": []},
                 {"run_id": "candidate", "config_item_ids": [config["project_item_id"]]}],
        "baseline_run_id": "baseline",
        "metric_definitions": [{"metric_name": "loss", "direction": "lower_is_better", "regression_threshold": 0.08}],
    }
    return mapping, project.confirm(_PAYLOAD, workspace_id="ws_a", source_id=source,
                                    mapping=mapping, captured_at=captured)


async def _confirm_after_rotation(client, system, pool, session):
    source = (await _upload(client, system, session, "ws_a")).json()["source_id"]
    with tempfile.TemporaryDirectory(prefix="k03-j02-mapping-") as directory:
        mapping, snapshot = await asyncio.to_thread(_mapping_and_snapshot, Path(directory), source)
    receipt = (await _enqueue(client, system, session, "ws_a", source,
                             extra={"operation": "confirm", "mapping": mapping})).json()
    lease = SecretStr("d" * 64)
    claim = await system.worker_jobs.claim(lease_digest=lease, lease_seconds=60)
    assert claim.job_id == receipt["job_id"]
    await _rotate(client, system, pool, session)
    before = await _count(pool, "j01_upload_snapshots")
    assert not await system.worker_jobs.complete_snapshot(claim=claim, lease_digest=SecretStr("f" * 64), snapshot=snapshot)
    assert await _count(pool, "j01_upload_snapshots") == before
    assert await system.worker_jobs.complete_snapshot(claim=claim, lease_digest=lease, snapshot=snapshot)
    assert await _count(pool, "j01_upload_snapshots") == before + 1
    current = credential_digest(SecretStr(client.cookies.get(SESSION_COOKIE)))
    assert await system.sources.snapshot(credential_digest=current, workspace_id="ws_a", snapshot_id=snapshot.snapshot_id) == snapshot
    assert (await client.get(receipt["status_url"])).json()["status"] == "succeeded"
    print("K03_J02_CONFIRM_SNAPSHOT_AFTER_ROTATION_AND_WRONG_LEASE_NO_EFFECT=PASS", flush=True)


async def _compute_process_after_rotation(client, system, pool, session, source):
    from aletheia_lab.hosted.upload_job_worker import run_one_upload_job
    from aletheia_lab.hosted.upload_operations import UploadOperations
    from aletheia_lab.hosted.windows_worker import WindowsUploadWorker
    from aletheia_lab.hosted.worker_supervisor import UploadWorkerSupervisor

    receipt = (await _enqueue(client, system, session, "ws_a", source)).json()
    with tempfile.TemporaryDirectory(prefix="k03-j02-compute-") as directory:
        worker_type = WindowsUploadWorker if sys.platform == "win32" else UploadWorkerSupervisor
        worker = worker_type(python=Path(sys.executable).absolute(), source_root=_ROOT / "src",
                             private_root=Path(directory), slots=1, timeout_seconds=30)
        class RotateAfterCompute:
            async def run_upload(self, payload):
                result = await worker.run_upload(payload)
                await _rotate(client, system, pool, session)
                return result
        try:
            operations = UploadOperations(system.worker_sources, RotateAfterCompute())
            assert await run_one_upload_job(system.worker_jobs, operations, lease_seconds=60)
        finally:
            await worker.aclose()
    assert (await client.get(receipt["status_url"])).json()["status"] == "succeeded"
    output = (await client.get(receipt["status_url"] + "/output")).json()
    import hashlib
    assert output["result"]["source_sha256"] == hashlib.sha256(_PAYLOAD).hexdigest()
    print("K03_J02_REAL_COMPUTE_CHILD_ROTATION_BEFORE_POSTREAD_AND_PUBLICATION=PASS", flush=True)


async def _independent_accounts(system, pool, client, other, source_a, source_b):
    digest_a = credential_digest(SecretStr(client.cookies.get(SESSION_COOKIE)))
    digest_b = credential_digest(SecretStr(other.cookies.get(SESSION_COOKIE)))
    async with pool.connection() as blocker:
        await blocker.execute("SELECT user_id FROM hosted_identity_bindings WHERE user_id='usr_synthetic_a' FOR UPDATE")
        pid = (await (await blocker.execute("SELECT pg_backend_pid()")).fetchone())[0]
        pending = asyncio.create_task(system.worker_sources.read(credential_digest=digest_a, workspace_id="ws_a", source_id=source_a))
        try:
            async with asyncio.timeout(3):
                while True:
                    async with pool.connection() as observer:
                        row = await (await observer.execute("SELECT 1 FROM pg_stat_activity WHERE %s=ANY(pg_blocking_pids(pid))", (pid,))).fetchone()
                    if row is not None:
                        break
                    await asyncio.sleep(0.01)
            async with asyncio.timeout(2):
                assert await system.worker_sources.read(credential_digest=digest_b, workspace_id="ws_b", source_id=source_b) == _PAYLOAD
        except BaseException:
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
            raise
    async with asyncio.timeout(3):
        assert await pending == _PAYLOAD
    print("K03_J02_ACCOUNT_A_LOCK_DOES_NOT_SERIALIZE_WORKER_B=PASS", flush=True)


async def _operator_revoke_a(pool):
    async with pool.connection() as connection:
        schema = (await (await connection.execute("SELECT current_schema()")).fetchone())[0]
        assert await PostgresSessionStore.revoke_identity(connection, schema=_schema_name(schema),
            issuer=ISSUER, subject=SecretStr("subject-a"), expected_issuer=ISSUER)
    return monotonic()


async def _account_session_count(pool, suffix):
    async with pool.connection() as connection:
        return (await (await connection.execute("SELECT count(*) FROM j01_sessions WHERE user_id=%s",
            ("usr_synthetic_" + suffix,))).fetchone())[0]


async def _claimed_operator_ordering(pool, flow, provider):
    start = await flow.begin(None)
    provider.identity = _identity("subject-a")
    provider.entered.clear()
    provider.release.clear()
    completion = asyncio.create_task(flow.complete(_cookie(start), _query(start)))
    try:
        await asyncio.wait_for(provider.entered.wait(), 2)
        await _operator_revoke_a(pool)
        provider.release.set()
        with pytest.raises(LoginRejected):
            await completion
        with pytest.raises(LoginRejected):
            await flow.complete(_cookie(start), _query(start))
    finally:
        provider.release.set()
        if not completion.done():
            completion.cancel()
        await asyncio.gather(completion, return_exceptions=True)


async def _unbound_operator_ordering(pool, cipher, tokens, system, client, baseline):
    async with pool.connection() as connection:
        database_now = (await (await connection.execute("SELECT clock_timestamp()")).fetchone())[0]
    # Controlled application clock only; database time and commit order are real.
    offset = database_now - datetime.now(UTC) + timedelta(seconds=10)
    provider = SyntheticOidc()
    flow = LoginCoordinator(PostgresFreshLogin(pool, cipher, clock_skew_seconds=30), provider,
        AccountResolver(PostgresIdentityDirectory(pool), POLICY, expected_issuer=ISSUER, timeout_seconds=2),
        system.runtime, POLICY, csrf_factory=tokens.issue, timeout_seconds=5,
        clock=lambda: datetime.now(UTC) + offset)
    pending_a, pending_b = await flow.begin(None), await flow.begin(None)
    async with pool.connection() as connection:
        rows = await (await connection.execute("SELECT previous_context_id,created_at>clock_timestamp() "
            "FROM j01_fresh_login_intents WHERE phase='pending'")).fetchall()
    assert len(rows) == 2 and all(row == (None, True) for row in rows)
    before = await _account_session_count(pool, "a")
    await _operator_revoke_a(pool)
    provider.identity = _identity("subject-a")
    with pytest.raises(LoginRejected):
        await flow.complete(_cookie(pending_a), _query(pending_a))
    with pytest.raises(LoginRejected):
        await flow.complete(_cookie(pending_a), _query(pending_a))
    assert await _account_session_count(pool, "a") == before
    assert (await client.get("/v1/session")).status_code == 401
    provider.identity = _identity("subject-b")
    completed_b = await flow.complete(_cookie(pending_b), _query(pending_b))
    assert completed_b.session.actor.user_id == "usr_synthetic_b"
    provider.identity = _identity("subject-a")
    fresh = await flow.begin(None)
    completed_a = await flow.complete(_cookie(fresh), _query(fresh))
    assert completed_a.session.actor.user_id == "usr_synthetic_a"
    assert completed_a.session.context_id != baseline["session"]["context_id"]
    before_claimed = await _account_session_count(pool, "a")
    await _claimed_operator_ordering(pool, flow, provider)
    assert await _account_session_count(pool, "a") == before_claimed
    print("K03_J02_UNBOUND_AND_CLAIMED_OPERATOR_ORDER_REAL_DB_APP_CLOCK_PLUS10S_SYNTHETIC_OIDC=PASS", flush=True)


async def _targeted_operator(client, other, system, pool, session, source):
    system.provider.identity = _identity("subject-a")
    before_b = (await other.get("/v1/session")).json()
    receipt = (await _enqueue(client, system, session, "ws_a", source)).json()
    lease = SecretStr("e" * 64)
    claim = await system.worker_jobs.claim(lease_digest=lease, lease_seconds=60)
    assert claim is not None and claim.job_id == receipt["job_id"]
    pending = await _begin(client)
    sessions_before = await _count(pool, "j01_sessions")
    snapshots_before = await _count(pool, "j01_upload_snapshots")
    committed = await _operator_revoke_a(pool)
    assert (await client.get("/v1/session")).status_code == 401
    renewal = await client.post("/v1/auth/renew", json={}, headers=_headers(system, session))
    assert renewal.status_code == 401 and not renewal.headers.get_list("set-cookie")
    for _ in range(2):
        rejected = await _callback(client, pending)
        assert rejected.status_code == 303 and rejected.headers["location"] == "/#/signed-out?reason=login_failed"
        assert rejected.content == b"" and not rejected.headers.get_list("set-cookie")
    with pytest.raises(SourceNotFound):
        await system.worker_sources.read(credential_digest=claim.credential_digest, workspace_id="ws_a", source_id=source)
    with pytest.raises(SourceNotFound):
        await system.worker_jobs.complete(claim=claim, lease_digest=lease, result={"must_not_publish": True})
    async with pool.connection() as connection:
        assert await (await connection.execute("SELECT state,result FROM j01_jobs WHERE job_id=%s",
            (claim.job_id,))).fetchone() == ("running", None)
    assert await _count(pool, "j01_sessions") == sessions_before
    assert await _count(pool, "j01_upload_snapshots") == snapshots_before
    after_b = await other.get("/v1/session")
    assert after_b.status_code == 200 and after_b.json()["session"]["context_id"] == before_b["session"]["context_id"]
    assert after_b.json()["actor"]["user_id"] == before_b["actor"]["user_id"]
    elapsed = monotonic() - committed
    assert elapsed <= 5
    assert await system.worker_jobs.fail(claim=claim, lease_digest=lease)
    fresh = await _login(client, system, "a")
    assert fresh["session"]["context_id"] != session["session"]["context_id"]
    print("K03_J02_TARGETED_OPERATOR_REVOKES_SESSION_PENDING_RENEW_AND_JOB_PRESERVES_B=PASS", flush=True)
    print("K03_J02_LOCAL_TARGETED_OPERATOR_SECONDS=" + format(elapsed, ".3f"), flush=True)
    return fresh


async def _renew_logout(client, other, system, pool, session):
    await _age_context(pool, session)
    cookie = (SESSION_COOKIE + "=" + client.cookies.get(SESSION_COOKIE)).encode()
    independent = ServerSessions(PostgresSessionStore(pool), POLICY, timeout_seconds=5.0, maximum_cookie_bytes=4096)
    renewal, logout = await asyncio.gather(system.sessions.renew(cookie), independent.logout_all(cookie), return_exceptions=True)
    assert logout is None
    if isinstance(renewal, BaseException):
        assert isinstance(renewal, (AccessDenied, RenewalConflict))
    else:
        assert renewal.credential is not None
        replacement = SESSION_COOKIE + "=" + renewal.credential.get_secret_value()
        assert (await client.get("/v1/session", headers={"cookie": replacement})).status_code == 401
    assert (await client.get("/v1/session")).status_code == 401
    assert (await other.get("/v1/session")).status_code == 200
    print("K03_J02_TWO_SESSION_STORES_CONCURRENT_RENEW_LOGOUT_ALL_NO_RESURRECTION=PASS", flush=True)


async def _exercise(pool):
    cipher = LoginProofCipher(SecretBytes(os.urandom(32)))
    tokens = CsrfTokens(SecretStr("synthetic-j02-csrf-" + "k" * 43))
    system = await _system(pool, cipher, tokens)
    transport = httpx.ASGITransport(app=system.app, client=("127.0.0.1", 12345))
    async with (
        _running(system.app),
        httpx.AsyncClient(transport=transport, base_url=system.runtime.public_origin) as client,
        httpx.AsyncClient(transport=transport, base_url=system.runtime.public_origin) as other,
        httpx.AsyncClient(transport=transport, base_url=system.runtime.public_origin) as anonymous,
    ):
        session = await _login(client, system, "a")
        await _unbound_operator_ordering(pool, cipher, tokens, system, client, session)
        session = await _login(client, system, "a")
        source_a, job_a = await _seed_read_job(client, system, session, "ws_a")
        session_b = await _login(other, system, "b")
        source_b, job_b = await _seed_read_job(other, system, session_b, "ws_b")
        await _independent_accounts(system, pool, client, other, source_a, source_b)
        await _roles(client, system, pool, session, source_a, job_a)
        await _scopes(client, anonymous, system, pool, session, source_a, source_b, job_b)
        await _publication(client, other, system, pool, session, source_a)
        await _renew_job_boundary(client, system, pool, session, source_a)
        await _confirm_after_rotation(client, system, pool, session)
        await _compute_process_after_rotation(client, system, pool, session, source_a)
        session = await _targeted_operator(client, other, system, pool, session, source_a)
        await _renew_logout(client, other, system, pool, session)
        system.provider.identity = _identity()
        state = await _begin(client, return_to="/account")
        cookies = httpx.Cookies(client.cookies)
    rebuilt = await _system(pool, cipher, tokens)
    async with _running(rebuilt.app), httpx.AsyncClient(transport=httpx.ASGITransport(app=rebuilt.app),
            base_url=rebuilt.runtime.public_origin, cookies=cookies) as client:
        response = await _callback(client, state)
        assert response.status_code == 303 and response.headers["location"] == "/account"
        assert (await client.get("/v1/session")).status_code == 200
        replay = await _callback(client, state)
        assert replay.status_code == 303 and replay.headers["location"] == "/#/signed-out?reason=login_failed"
        assert replay.content == b"" and not replay.headers.get_list("set-cookie")
    print("K03_J02_APP_RECONSTRUCTION_PENDING_CALLBACK_RETURN_TO_AND_REPLAY=PASS", flush=True)


async def _live(password):
    import psycopg
    from psycopg import sql
    from psycopg_pool import AsyncConnectionPool

    from aletheia_lab.hosted.persistence_migration import LOGIN_CLOCK_DDL

    schema = _schema_name("k03_j02_" + uuid.uuid4().hex)
    db = {"host": "127.0.0.1", "port": database_port(), "dbname": "aletheia", "user": "aletheia",
          "password": password, "connect_timeout": 5}
    created = False
    async with await psycopg.AsyncConnection.connect(**db, autocommit=True) as admin:
        try:
            await admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
            created = True
            await admin.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
            for name in ("j01-experimental-session-schema.sql", "j01-experimental-fresh-login-schema.sql",
                         "j01-experimental-source-schema.sql", "j01-experimental-jobs-schema.sql"):
                await admin.execute((_ROOT / "docs" / name).read_text(encoding="utf-8"))
            await admin.execute(LOGIN_CLOCK_DDL)
            await admin.execute((_ROOT / "src/aletheia_lab/hosted_auth/admission_schema.sql").read_text(encoding="utf-8"))
            async with AsyncConnectionPool(conninfo="", kwargs={**db, "options": "-c search_path=" + schema},
                                           min_size=1, max_size=5, timeout=5) as pool:
                from aletheia_lab.hosted.workspace_migration import migrate

                async with pool.connection() as connection:
                    assert await migrate(connection, schema) == "adopted"
                    for suffix in ("a", "b", "fixture_owner"):
                        await connection.execute("INSERT INTO hosted_identity_bindings VALUES (%s,%s,%s,%s,%s,true,true,0,'ready',%s,NULL)",
                            (ISSUER, "subject-" + suffix, "usr_synthetic_" + suffix, "Synthetic",
                             suffix + "@example.invalid", "ws_b" if suffix == "b" else "ws_a"))
                    for suffix in ("a", "b"):
                        await connection.execute("INSERT INTO j01_workspaces VALUES (%s,%s)",
                                                 ("ws_" + suffix, "Private workspace " + suffix.upper()))
                        await connection.execute("INSERT INTO j01_memberships VALUES (%s,%s,'owner',true,0)",
                                                 ("ws_" + suffix, "usr_synthetic_" + suffix))
                    # A stable administrative fixture keeps the last-owner invariant while A cycles roles.
                    await connection.execute("INSERT INTO j01_memberships VALUES ('ws_a','usr_synthetic_fixture_owner','owner',true,0)")
                async with asyncio.timeout(60):
                    await _exercise(pool)
        finally:
            if created:
                await admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(_schema_name(schema))))
                print("K03_J02_OWN_SCHEMA_REMOVED=PASS", flush=True)


@pytest.mark.parametrize("name", [None, "public", "k03_j02_x", "k03_j02_" + "a" * 32 + ";DROP",
                                       "k03_j02_" + "a" * 31, "k03_j02_" + "A" * 32])
def test_j02_schema_cleanup_cannot_target_another_namespace(name):
    with pytest.raises(ValueError):
        _schema_name(name)


def test_j02_schema_guard_accepts_only_generated_names():
    name = "k03_j02_" + uuid.uuid4().hex
    assert _schema_name(name) == name


@pytest.mark.parametrize("code, expected", [("rate_limited", "rate_limited"),
    ("service_unavailable", "service_unavailable"), ("synthetic-private-code", "other"), ([], "other")])
def test_j02_start_observation_keeps_only_fixed_problem_fields(code, expected):
    response = httpx.Response(503, headers={"content-type": "application/problem+json; charset=utf-8"},
        json={"code": code, "detail": "synthetic-private-body", "request_id": "synthetic-private-id"})
    observed = _start_observation(response, 0.123)
    assert observed == {"STATUS": 503, "MEDIA": "application/problem+json", "CODE": expected, "ELAPSED_MS": 123}
    assert "synthetic-private" not in repr(observed)


@pytest.mark.parametrize("media,body,expected", [
    ("text/plain; private=value", b"synthetic-private-body", "text/plain"),
    ("synthetic-private-type", b"synthetic-private-body", "other"),
    ("application/json", b"not-json-synthetic-private", "application/json"),
    ("application/json", b"x" * 4097, "application/json"),
    ("", b"synthetic-private-body", "missing"),
])
def test_j02_start_observation_does_not_echo_headers_or_parse_unbounded_body(media, body, expected):
    observed = _start_observation(httpx.Response(400, headers={"content-type": media}, content=body), -1)
    assert observed == {"STATUS": 400, "MEDIA": expected, "CODE": "none", "ELAPSED_MS": 0}
    assert "synthetic-private" not in repr(observed)


@pytest.mark.parametrize("destination, accepted", [("/app", True), ("/#/signed-out?reason=login_failed", False)])
def test_j02_login_failure_redirect_cannot_reuse_an_old_session(monkeypatch, destination, accepted):
    reads = []

    async def begin(*args, **kwargs):
        return "synthetic-state"

    async def callback(*args):
        return httpx.Response(303, headers={"location": destination})

    class Client:
        async def get(self, path):
            reads.append(path)
            return httpx.Response(200, json={"actor": {"user_id": "usr_synthetic_a"}})

    monkeypatch.setattr(sys.modules[__name__], "_begin", begin)
    monkeypatch.setattr(sys.modules[__name__], "_callback", callback)
    system = SimpleNamespace(provider=SimpleNamespace())
    if accepted:
        assert asyncio.run(_login(Client(), system, "a"))["actor"]["user_id"] == "usr_synthetic_a"
        assert reads == ["/v1/session"]
    else:
        with pytest.raises(AssertionError):
            asyncio.run(_login(Client(), system, "a"))
        assert reads == []


if __name__ == "__main__":
    try:
        if sys.platform == "win32":
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        asyncio.run(_live(os.environ.pop("K03_DB_PASSWORD")))
    except Exception as error:
        print("K03_J02_BACKEND_CHECK=FAIL:" + type(error).__name__, flush=True)
        frames = traceback.extract_tb(error.__traceback__)
        if frames:
            print("K03_J02_FAILURE_LINE=" + str(frames[-1].lineno), flush=True)
        sys.exit(1)
