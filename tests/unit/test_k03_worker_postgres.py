"""Manual worker-continuity negatives on loopback PostgreSQL; pytest checks guards.

Direct execution needs K03_DB_PASSWORD. No IdP/browser/service is started, and
all database artifacts belong to one disposable UUID schema.
"""

import asyncio
import hashlib
import os
import re
import sys
import tempfile
import traceback
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path

import pytest
from test_k03_postgres_environment import database_port

ROOT = Path(__file__).resolve().parents[2]
CASES = ("disabled", "unverified", "epoch", "idle", "absolute", "revoke", "logout_all",
         "new_context", "membership", "grant")


def _schema(value):
    if not isinstance(value, str) or re.fullmatch(r"k03_worker_[0-9a-f]{32}", value) is None:
        raise ValueError("Owned worker test schema required")
    return value


def _digest():
    from pydantic import SecretStr

    return SecretStr(hashlib.sha256(uuid.uuid4().bytes).hexdigest())


@asynccontextmanager
async def _database(password, payload):
    import psycopg
    from psycopg import sql
    from psycopg_pool import AsyncConnectionPool

    from aletheia_lab.hosted.workspace_migration import migrate

    schema = _schema("k03_worker_" + uuid.uuid4().hex)
    settings = dict(host="127.0.0.1", port=database_port(), dbname="aletheia", user="aletheia", password=password,
                    connect_timeout=5, options="-c statement_timeout=5000 -c lock_timeout=3000")
    created = False
    async with await psycopg.AsyncConnection.connect(**settings, autocommit=True) as admin:
        try:
            await admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
            created = True
            await admin.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
            for name in ("session", "source", "jobs"):
                await admin.execute((ROOT / "docs" / f"j01-experimental-{name}-schema.sql").read_text(encoding="utf-8"))
            for user in ("owner", "analyst"):
                await admin.execute("INSERT INTO hosted_identity_bindings VALUES "
                    "('issuer',%s,%s,'Synthetic',%s,true,true,0,'ready','wa',null)",
                    (user, user, user + "@example.invalid"))
            await admin.execute("INSERT INTO j01_workspaces VALUES ('wa','Synthetic worker test')")
            await admin.execute("INSERT INTO j01_memberships VALUES ('wa','owner','owner',true,1),"
                                "('wa','analyst','analyst',true,1)")
            await admin.execute("INSERT INTO j01_sources(source_id,workspace_id,creator_id,request_key_hash,sha256,payload,manifest) "
                "VALUES ('source','wa','owner','synthetic-source-key',%s,%s,'[]')",
                (hashlib.sha256(payload).hexdigest(), payload))
            scoped = {**settings, "options": settings["options"] + " -c search_path=" + schema}
            async with await psycopg.AsyncConnection.connect(**scoped) as conn:
                assert await migrate(conn, schema) == "adopted"
                await conn.execute("INSERT INTO j01_source_grants VALUES ('wa','source','analyst',ARRAY['read','write'],0)")
            async with AsyncConnectionPool(conninfo="", kwargs=scoped, min_size=1, max_size=3, open=False) as pool:
                await pool.wait(timeout=10)
                yield pool
        finally:
            if created:
                await admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(_schema(schema))))
                print("K03_WORKER_OWN_SCHEMA_REMOVED=PASS", flush=True)


async def _session(pool):
    digest, context = _digest(), "ctx_" + uuid.uuid4().hex
    async with pool.connection() as conn:
        await conn.execute("INSERT INTO j01_sessions VALUES (%s,'analyst','synthetic-csrf','active',"
            "statement_timestamp()-interval '60 seconds',statement_timestamp()-interval '60 seconds',"
            "statement_timestamp()-interval '60 seconds',statement_timestamp()+interval '900 seconds',"
            "statement_timestamp()+interval '28000 seconds',0,0)", (context,))
        await conn.execute("INSERT INTO j01_session_credentials VALUES (%s,%s,'active',0)",
                           (digest.get_secret_value(), context))
    return digest, context


async def _reset(pool):
    async with pool.connection() as conn:
        await conn.execute("UPDATE hosted_identity_bindings SET enabled=true,email_verified=true,account_epoch=0 WHERE user_id='analyst'")
        await conn.execute("UPDATE j01_memberships SET role='analyst',active=true,revision=revision+1 WHERE user_id='analyst'")
        await conn.execute("UPDATE j01_source_grants SET actions=ARRAY['read','write'],revision=revision+1 WHERE user_id='analyst'")


async def _rotate(pool, digest, context):
    from aletheia_lab.hosted_auth.postgres_sessions import PostgresSessionStore
    from aletheia_lab.hosted_auth.repositories import RotateSession, SessionRules

    replacement = _digest()
    command = RotateSession(digest, replacement, "analyst", context, 0, 0, SessionRules(900, 28000, 30))
    assert await PostgresSessionStore(pool).rotate_if_current(command) is not None
    return replacement


async def _change(pool, case, context, current):
    from aletheia_lab.hosted_auth.postgres_sessions import PostgresSessionStore

    if case in ("revoke", "logout_all", "new_context"):
        assert await PostgresSessionStore(pool).revoke_presented(current, all_sessions=case == "logout_all")
        if case == "new_context":
            await _session(pool)
        return
    statements = {
        "disabled": "UPDATE hosted_identity_bindings SET enabled=false WHERE user_id='analyst'",
        "unverified": "UPDATE hosted_identity_bindings SET email_verified=false WHERE user_id='analyst'",
        "epoch": "UPDATE hosted_identity_bindings SET account_epoch=account_epoch+1 WHERE user_id='analyst'",
        "membership": "UPDATE j01_memberships SET role='viewer',revision=revision+1 WHERE user_id='analyst'",
        "grant": "UPDATE j01_source_grants SET actions=ARRAY['read'],revision=revision+1 WHERE user_id='analyst'",
    }
    async with pool.connection() as conn:
        if case in ("idle", "absolute"):
            absolute = "statement_timestamp()-interval '1 second'" if case == "absolute" else "absolute_expires_at"
            await conn.execute("UPDATE j01_sessions SET issued_at=statement_timestamp()-interval '2 hours',"
                "rotated_at=statement_timestamp()-interval '2 hours',last_seen_at=statement_timestamp()-interval '2 hours',"
                "idle_expires_at=statement_timestamp()-interval '2 seconds',absolute_expires_at=" + absolute +
                " WHERE context_id=%s", (context,))
        else:
            await conn.execute(statements[case])


def _repositories(pool):
    from aletheia_lab.hosted.postgres_jobs import PostgresJobs
    from aletheia_lab.hosted.postgres_sources import PostgresSources
    from aletheia_lab.hosted_auth.worker_sessions import WorkerPostgresSources

    budgets = dict(maximum_upload_bytes=10000, maximum_expanded_bytes=20000, maximum_entries=10)
    sources = WorkerPostgresSources(pool, **budgets)
    return PostgresJobs(pool, PostgresSources(pool, **budgets)), PostgresJobs(pool, sources), sources


async def _enqueue(jobs, digest, *, confirm=False):
    return await jobs.enqueue(credential_digest=digest, workspace_id="wa", operation="confirm" if confirm else "preview",
        request_key=_digest(), request={"source_id": "source", **({"mapping": {}} if confirm else {})})


async def _unpublished(pool, job_id, *, state):
    async with pool.connection() as conn:
        assert await (await conn.execute("SELECT state,result FROM j01_jobs WHERE job_id=%s", (job_id,))).fetchone() == (state, None)
        assert await (await conn.execute("SELECT count(*) FROM j01_upload_snapshots")).fetchone() == (0,)


async def _negatives(pool, snapshot):
    from aletheia_lab.hosted.postgres_sources import SourceNotFound

    api, worker, sources = _repositories(pool)
    for before_claim in (True, False):
        for case in CASES:
            await _reset(pool)
            digest, context = await _session(pool)
            receipt = await _enqueue(api, digest, confirm=not before_claim)
            current = await _rotate(pool, digest, context)
            lease = _digest()
            if not before_claim:
                claim = await worker.claim(lease_digest=lease, lease_seconds=60)
                assert claim is not None and claim.job_id == receipt.job_id
            await _change(pool, case, context, current)
            with pytest.raises(SourceNotFound):
                await sources.read(credential_digest=digest, workspace_id="wa", source_id="source")
            if before_claim:
                assert await worker.claim(lease_digest=lease, lease_seconds=60) is None
            else:
                with pytest.raises(SourceNotFound):
                    await worker.complete(claim=claim, lease_digest=lease, result={"must_not_publish": True})
                with pytest.raises(SourceNotFound):
                    await worker.complete_snapshot(claim=claim, lease_digest=lease, snapshot=snapshot)
                await _unpublished(pool, receipt.job_id, state="running")
                assert await worker.fail(claim=claim, lease_digest=lease)
            await _unpublished(pool, receipt.job_id, state="failed")
    print("K03_WORKER_TEN_CURRENT_DENIALS_BEFORE_CLAIM_AND_PUBLICATION=PASS", flush=True)


async def _leases(pool, snapshot):
    await _reset(pool)
    api, worker, _ = _repositories(pool)
    digest, context = await _session(pool)
    receipt = await _enqueue(api, digest, confirm=True)
    lease, other = _digest(), _digest()
    claim = await worker.claim(lease_digest=lease, lease_seconds=60)
    assert claim is not None and claim.job_id == receipt.job_id
    await _rotate(pool, digest, context)
    for expired in (False, True):
        if expired:
            async with pool.connection() as conn:
                await conn.execute("UPDATE j01_jobs SET lease_until=clock_timestamp()-interval '1 second' WHERE job_id=%s", (receipt.job_id,))
        assert not await worker.complete(claim=claim, lease_digest=lease if expired else other, result={"must_not_publish": True})
        assert not await worker.complete_snapshot(claim=claim, lease_digest=lease if expired else other, snapshot=snapshot)
        await _unpublished(pool, receipt.job_id, state="running")
    reclaimed = await worker.claim(lease_digest=other, lease_seconds=60)
    assert reclaimed is not None and reclaimed.job_id == receipt.job_id
    assert not await worker.complete_snapshot(claim=claim, lease_digest=lease, snapshot=snapshot)
    await _unpublished(pool, receipt.job_id, state="running")
    assert await worker.fail(claim=reclaimed, lease_digest=other)
    print("K03_WORKER_WRONG_EXPIRED_RECLAIMED_LEASE_NO_PUBLICATION=PASS", flush=True)


async def _live(password):
    from test_project_mapping import _case, _configuration

    from aletheia_lab.hosted.upload_snapshot import build_upload_snapshot
    from aletheia_lab.project.mapping import validate_project_mapping

    with tempfile.TemporaryDirectory(prefix="aletheia-k03-worker-") as directory:
        case = _case(Path(directory))
        mapping = _configuration(case)
        payload = b"synthetic-source-for-worker-authorization-only"
        snapshot = build_upload_snapshot(workspace_id="wa", source_id="source", source_sha256=hashlib.sha256(payload).hexdigest(),
            mapping=mapping, mapping_result=validate_project_mapping(case.bundle, case.artifacts, case.collection, mapping),
            captured_at=datetime.now(UTC))
        async with _database(password, payload) as pool:
            async with asyncio.timeout(90):
                await _negatives(pool, snapshot)
                await _leases(pool, snapshot)


@pytest.mark.parametrize("value", ["public", "pg_catalog", "k03_worker_", "k03_worker_" + "a" * 31,
                                    "k03_worker_" + "a" * 32 + "; DROP SCHEMA public"])
def test_worker_schema_guard_rejects_nonowned_or_interpolated_names(value):
    with pytest.raises(ValueError, match="Owned worker"):
        _schema(value)


def test_worker_schema_guard_accepts_only_owned_uuid_shape():
    assert _schema("k03_worker_" + "a" * 32) == "k03_worker_" + "a" * 32


if __name__ == "__main__":
    try:
        password = os.environ.pop("K03_DB_PASSWORD")
        with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else None) as runner:
            runner.run(_live(password))
    except Exception as error:
        print("K03_WORKER_DB_CHECK=FAIL:" + type(error).__name__, flush=True)
        frames = traceback.extract_tb(error.__traceback__)
        own_frames = [frame for frame in frames if Path(frame.filename).resolve() == Path(__file__).resolve()]
        if own_frames:
            print("K03_WORKER_DB_FAILURE_LINE=" + str(own_frames[-1].lineno), flush=True)
        sys.exit(1)
