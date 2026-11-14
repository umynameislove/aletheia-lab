"""Real admission commit/audit, rollback authorization, and process loss before HTTP ACK."""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr
from test_k06_idempotency_postgres import _bundle, _database, _digest, _run

from aletheia_lab.hosted.action_audit_migration import migrate
from aletheia_lab.hosted.authorization import AuthorizationDenied
from aletheia_lab.hosted.job_http import JobApplication
from aletheia_lab.hosted.postgres_jobs import PostgresJobs
from aletheia_lab.hosted.postgres_sources import PostgresSources
from aletheia_lab.hosted.request_boundary import BodyLimits
from aletheia_lab.hosted.settings import RuntimeSettings
from aletheia_lab.hosted.transactions import TransactionOutcomeUnknown
from aletheia_lab.hosted_auth.csrf import CsrfTokens
from aletheia_lab.hosted_auth.postgres_sessions import PostgresSessionStore
from aletheia_lab.hosted_auth.sessions import ServerSessions, credential_digest
from aletheia_lab.hosted_auth.settings import AuthPolicy
from aletheia_lab.hosted_contract.job_page import JobReceipt

pytestmark = [pytest.mark.integration, pytest.mark.skipif(
    not os.environ.get("K06_TEST_DATABASE_URL"), reason="Explicit PostgreSQL test database required")]
ORIGIN = "https://app.example.invalid"
TOKEN = SecretStr("a" * 43)
SIGNING_KEY = SecretStr("synthetic-k06-process-" + "s" * 43)


def _sources(pool):
    return PostgresSources(pool, maximum_upload_bytes=10000, maximum_expanded_bytes=20000,
                           maximum_entries=10, audit=True)


async def _audit(pool):
    async with pool.connection() as conn:
        schema = (await (await conn.execute("SELECT current_schema()")).fetchone())[0]
        assert await migrate(conn, schema) == "created"


def test_admission_replay_and_terminal_effect_each_have_one_committed_audit():
    async def scenario():
        async with _database() as pool:
            await _audit(pool)
            sources = _sources(pool)
            args = dict(credential_digest=_digest("analyst"), workspace_id="wa",
                        request_key=SecretStr("source-commit-audit-key"), payload=_bundle())
            source = await sources.put(**args)
            assert await sources.put(**args) == source
            jobs = PostgresJobs(pool, sources, audit=True)
            args = dict(credential_digest=_digest("analyst"), workspace_id="wa", operation="preview",
                        request_key=SecretStr("job-commit-audit-key"), request={"source_id": source.source_id})
            receipt = await jobs.enqueue(**args)
            assert await jobs.enqueue(**args) == receipt
            lease = SecretStr("1" * 64)
            claim = await jobs.claim(lease_digest=lease, lease_seconds=30)
            assert claim.job_id == receipt.job_id
            assert await jobs.complete(claim=claim, lease_digest=lease, result={"synthetic": True})
            assert not await jobs.complete(claim=claim, lease_digest=lease, result={"synthetic": True})
            assert await jobs.enqueue(**args) == receipt
            async with pool.connection() as conn:
                cursor = await conn.execute("SELECT action,count(*) FROM k06_action_audit GROUP BY action ORDER BY action")
                rows = []
                while (row := await cursor.fetchone()) is not None:
                    rows.append(row)
                assert rows == [("job.admit", 1), ("job.complete", 1), ("source.put", 1)]
                assert await (await conn.execute("SELECT count(*) FROM k06_action_audit WHERE job_id=%s "
                    "AND operation_id=%s AND actor_id='analyst' AND request_id=%s",
                    (receipt.job_id, receipt.job_id, receipt.request_id))).fetchone() == (2,)
    _run(scenario())


def test_server_aborted_admission_rereads_authority_and_persists_neither_effect_nor_audit():
    import psycopg

    async def scenario():
        async with _database() as pool:
            await _audit(pool)

            class AbortThenRevoke:
                injected = False

                @asynccontextmanager
                async def connection(self):
                    try:
                        async with pool.connection() as conn:
                            outer = self

                            class Connection:
                                autocommit = False

                                async def execute(self, query, params=()):
                                    if query.startswith("INSERT INTO k06_action_audit") and not outer.injected:
                                        outer.injected = True
                                        await conn.execute("DO $$BEGIN RAISE EXCEPTION 'synthetic abort' "
                                                           "USING ERRCODE='40001'; END$$")
                                    return await conn.execute(query, params)

                            yield Connection()
                    except psycopg.errors.SerializationFailure:
                        # The failed connection context has rolled back and released its locks.
                        async with pool.connection() as conn:
                            await conn.execute("UPDATE j01_memberships SET active=false,revision=revision+1 "
                                               "WHERE workspace_id='wa' AND user_id='analyst'")
                        raise

            sources = _sources(AbortThenRevoke())
            with pytest.raises(AuthorizationDenied):
                await sources.put(credential_digest=_digest("analyst"), workspace_id="wa",
                                  request_key=SecretStr("revoked-rollback-key"), payload=_bundle())
            async with pool.connection() as conn:
                assert await (await conn.execute("SELECT count(*) FROM j01_sources WHERE source_id NOT IN ('sa','sb')")).fetchone() == (0,)
                assert await (await conn.execute("SELECT count(*) FROM k06_action_audit")).fetchone() == (0,)
                assert await (await conn.execute("SELECT 1")).fetchone() == (1,)
    _run(scenario())


def test_real_job_commit_with_lost_commit_ack_reconciles_original_key_without_duplicate_audit():
    import psycopg

    async def scenario():
        async with _database() as pool:
            await _audit(pool)

            class LostCommitAck:
                lost = False

                @asynccontextmanager
                async def connection(self):
                    async with pool.connection() as conn:
                        yield conn
                    if not self.lost:
                        self.lost = True
                        raise psycopg.OperationalError("synthetic commit acknowledgement lost")

            jobs = PostgresJobs(LostCommitAck(), _sources(pool), audit=True)
            args = dict(credential_digest=_digest("analyst"), workspace_id="wa", operation="preview",
                        request_key=SecretStr("commit-ack-original-key"), request={"source_id": "sa"})
            with pytest.raises(TransactionOutcomeUnknown):
                await jobs.enqueue(**args)
            receipt = await PostgresJobs(pool, _sources(pool), audit=True).enqueue(**args)
            async with pool.connection() as conn:
                assert await (await conn.execute("SELECT count(*) FROM j01_jobs")).fetchone() == (1,)
                assert await (await conn.execute("SELECT count(*) FROM k06_action_audit WHERE operation_id=%s",
                                                (receipt.job_id,))).fetchone() == (1,)
    _run(scenario())


def _http_app(pool, root, proof=None):
    tokens = CsrfTokens(SIGNING_KEY)
    proof = proof or tokens.issue("ctx_analyst").get_secret_value()
    runtime = RuntimeSettings.model_validate({
        "environment": "local", "public_origin": ORIGIN,
        "identity": {"issuer": "https://issuer.example.invalid", "client_id": "synthetic",
                     "redirect_uri": ORIGIN + "/callback", "client_kind": "public"},
        "storage": {"database_url_env": "ALETHEIA_HOSTED_UNUSED_DATABASE_URL", "object_root": str(root)},
    })
    sessions = ServerSessions(PostgresSessionStore(pool), AuthPolicy(default_return_to="/", return_to_paths=("/",)),
                              timeout_seconds=3, maximum_cookie_bytes=runtime.api.maximum_cookie_bytes)

    async def fallback(scope, receive, send):
        raise AssertionError("Unexpected fallback")

    async def clock():
        return True

    app = JobApplication(fallback, PostgresJobs(pool, _sources(pool), audit=True), sessions, tokens, runtime,
                         body_limits=BodyLimits(20000, 1, 3, 10), clock_check=clock)
    headers = {"Cookie": "__Host-aletheia_session=" + TOKEN.get_secret_value() + "; __Host-aletheia_csrf=" + proof,
               "X-CSRF-Token": proof, "Origin": ORIGIN, "Idempotency-Key": "process-lost-http-response-key"}
    return app, headers


CHILD = r'''
import asyncio, json, os, sys
from pathlib import Path
from psycopg_pool import AsyncConnectionPool
sys.path.insert(0, os.environ['K06_CHILD_TEST_ROOT'])
from test_k06_admission_audit_postgres import _http_app
async def run():
    async with AsyncConnectionPool(os.environ['K06_CHILD_SCOPED_URL'],min_size=1,max_size=2,open=False) as pool:
        await pool.wait(timeout=10)
        app, headers = _http_app(pool, Path(os.environ['K06_CHILD_ROOT']), os.environ['K06_CHILD_CSRF'])
        body = json.dumps({'operation':'preview','source_id':'sa'}).encode()
        sent = False
        async def receive():
            nonlocal sent
            if sent:
                await asyncio.Future()
            sent = True
            return {'type':'http.request','body':body,'more_body':False}
        async def send(message):
            if message['type']=='http.response.start':
                # The actual HTTP serializer reached 202 after durable admission; ACK never leaves this process.
                os._exit(73 if message['status']==202 else 74)
        await app({'type':'http','method':'POST','scheme':'https','path':'/v1/workspaces/wa/jobs',
            'query_string':b'','headers':[(key.lower().encode(),value.encode()) for key,value in headers.items()]
            +[(b'host',b'app.example.invalid'),(b'content-type',b'application/json'),(b'content-length',str(len(body)).encode())]},receive,send)
with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop if sys.platform=='win32' else None) as runner:
    runner.run(run())
'''


def test_process_dies_after_real_http_admission_before_response_and_replay_recovers_one_receipt(tmp_path):
    async def scenario():
        async with _database() as pool:
            await _audit(pool)
            proof = CsrfTokens(SIGNING_KEY).issue("ctx_analyst").get_secret_value()
            digest = credential_digest(TOKEN)
            async with pool.connection() as conn:
                await conn.execute("UPDATE j01_session_credentials SET digest=%s WHERE context_id='ctx_analyst'",
                                   (digest.get_secret_value(),))
                await conn.execute("UPDATE j01_sessions SET csrf_token=%s WHERE context_id='ctx_analyst'", (proof,))
            child_env = dict(os.environ, K06_CHILD_SCOPED_URL=pool.conninfo, K06_CHILD_ROOT=str(tmp_path),
                             K06_CHILD_TEST_ROOT=str(Path(__file__).parent), K06_CHILD_CSRF=proof)
            result = await asyncio.to_thread(subprocess.run, [sys.executable, "-B", "-c", CHILD],
                                             env=child_env, capture_output=True, timeout=30, check=False)
            assert result.returncode == 73, "Child must die at the committed 202 response boundary"
            app, headers = _http_app(pool, tmp_path, proof)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=ORIGIN) as client:
                first = await client.post("/v1/workspaces/wa/jobs", json={"operation": "preview", "source_id": "sa"}, headers=headers)
                second = await client.post("/v1/workspaces/wa/jobs", json={"operation": "preview", "source_id": "sa"}, headers=headers)
                assert first.status_code == second.status_code == 202
                receipt = JobReceipt.model_validate_json(first.content)
                assert second.json() == first.json()
                status = await client.get(receipt.status_url, headers=headers)
                assert status.status_code == 200 and status.json()["job_id"] == receipt.job_id
            async with pool.connection() as conn:
                assert await (await conn.execute("SELECT count(*) FROM j01_jobs")).fetchone() == (1,)
                assert await (await conn.execute("SELECT count(*) FROM k06_action_audit WHERE operation_id=%s",
                                                (receipt.job_id,))).fetchone() == (1,)
    _run(scenario())


def test_mutated_claim_cannot_complete_another_persisted_parent_request():
    async def scenario():
        async with _database() as pool:
            await _audit(pool)
            sources = _sources(pool)
            other = await sources.put(credential_digest=_digest("analyst"), workspace_id="wa",
                request_key=SecretStr("another-source-claim-key"), payload=_bundle())
            jobs = PostgresJobs(pool, sources, audit=True)
            receipt = await jobs.enqueue(credential_digest=_digest("analyst"), workspace_id="wa", operation="preview",
                request_key=SecretStr("persisted-parent-claim-key"), request={"source_id": "sa"})
            lease = SecretStr("2" * 64)
            claim = await jobs.claim(lease_digest=lease, lease_seconds=30)
            claim.request["source_id"] = other.source_id
            assert not await jobs.complete(claim=claim, lease_digest=lease, result={"synthetic": True})
            assert (await jobs.status(credential_digest=_digest("analyst"), workspace_id="wa", job_id=receipt.job_id)).status == "running"
            async with pool.connection() as conn:
                assert await (await conn.execute("SELECT count(*) FROM k06_action_audit WHERE action='job.complete'")).fetchone() == (0,)
            claim.request["source_id"] = "sa"
            assert await jobs.complete(claim=claim, lease_digest=lease, result={"synthetic": True})
    _run(scenario())


def test_mutated_confirm_mapping_cannot_publish_snapshot_under_original_receipt(tmp_path):
    from copy import deepcopy

    from test_k04_job_controls_postgres import _setup
    from test_k04_member_pages_postgres import _canonical

    async def scenario():
        async with _canonical() as pool:
            env = await _setup(pool, tmp_path)
            await _audit(pool)
            jobs = PostgresJobs(pool, env.sources, audit=True)
            receipt = await jobs.enqueue(credential_digest=env.accounts["analyst"], workspace_id=env.workspace,
                operation="confirm", request_key=SecretStr("confirm-request-binding-key"),
                request={"source_id": env.source.source_id, "mapping": env.mapping})
            lease = SecretStr("3" * 64)
            claim = await jobs.claim(lease_digest=lease, lease_seconds=30)
            original = deepcopy(claim.request)
            claim.request["mapping"]["baseline_run_id"] = "different-run"
            assert not await jobs.complete_snapshot(claim=claim, lease_digest=lease, snapshot=env.snapshot)
            async with pool.connection() as conn:
                assert await (await conn.execute("SELECT count(*) FROM j01_upload_snapshots")).fetchone() == (0,)
                assert await (await conn.execute("SELECT count(*) FROM k06_action_audit WHERE action='job.publish'")).fetchone() == (0,)
            claim.request.clear()
            claim.request.update(original)
            assert await jobs.complete_snapshot(claim=claim, lease_digest=lease, snapshot=env.snapshot)
            assert (await jobs.status(credential_digest=env.accounts["analyst"], workspace_id=env.workspace,
                                      job_id=receipt.job_id)).status == "succeeded"
    _run(scenario())


def test_retry_replays_receipt_before_original_state_eligibility_for_new_effect():
    import psycopg

    from aletheia_lab.hosted.postgres_jobs import JobStateConflict
    from aletheia_lab.hosted.postgres_sources import SourceConflict

    async def scenario():
        async with _database() as pool:
            # Job controls require the canonical V4 shape; migrate the owned experimental schema.
            from aletheia_lab.hosted.persistence_migration import migrate as migrate_canonical
            async with pool.connection() as conn:
                schema = (await (await conn.execute("SELECT current_schema()")).fetchone())[0]
                await migrate_canonical(conn, schema)
            await _audit(pool)
            jobs = PostgresJobs(pool, _sources(pool), audit=True, execution=True)
            original = await jobs.enqueue(credential_digest=_digest("analyst"), workspace_id="wa", operation="preview",
                request_key=SecretStr("parent-business-operation-key"), request={"source_id": "sa"})
            await jobs.cancel(credential_digest=_digest("analyst"), workspace_id="wa", job_id=original.job_id)
            args = dict(credential_digest=_digest("analyst"), workspace_id="wa", job_id=original.job_id,
                        request_key=SecretStr("child-business-operation-key"))
            child = await jobs.retry(**args)
            # A terminal parent cannot be reopened to manufacture a different eligibility state.
            with pytest.raises(psycopg.errors.RaiseException, match='terminal job cannot reopen'):
                async with pool.connection() as conn:
                    await conn.execute("UPDATE j01_jobs SET state='succeeded' WHERE job_id=%s", (original.job_id,))
            parent = await jobs.status(credential_digest=_digest('analyst'), workspace_id='wa', job_id=original.job_id)
            assert parent.status == 'cancelled' and parent.retryable is False
            # The accepted child consumes this parent's only successor slot. Same-key replay
            # must precede that fresh-admission guard, while a new key remains a conflict.
            assert await jobs.retry(**args) == child
            with pytest.raises(JobStateConflict):
                await jobs.retry(**{**args, "request_key": SecretStr("fresh-child-business-key")})
            with pytest.raises(SourceConflict):
                await jobs.retry(**{**args, "request_key": SecretStr("parent-business-operation-key")})
            async with pool.connection() as conn:
                assert await (await conn.execute("SELECT count(*) FROM j01_jobs")).fetchone() == (2,)
                assert await (await conn.execute("SELECT count(*) FROM k06_action_audit WHERE action='job.admit'")).fetchone() == (2,)
    _run(scenario())
