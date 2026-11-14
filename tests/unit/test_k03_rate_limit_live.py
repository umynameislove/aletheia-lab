"""Opt-in real DB/process smoke; pytest only checks the isolated-schema guard."""

import asyncio
import json
import os
import re
import subprocess
import sys
import traceback
import uuid
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr
from test_k03_postgres_environment import database_port

from aletheia_lab.hosted.edge import EdgeGuard
from aletheia_lab.hosted_auth.abuse import AuthAdmission, AuthRateLimited, RatePolicy
from aletheia_lab.hosted_auth.admission_http import AuthAdmissionApplication
from aletheia_lab.hosted_auth.postgres_admission import PostgresAuthAdmission
from aletheia_lab.hosted_auth.repositories import RepositoryDataRejected


def _schema(value):
    if not isinstance(value, str) or not re.fullmatch(r"k03_rate_test_[0-9a-f]{32}", value):
        raise ValueError("Only an isolated UUID K-03 rate test schema is allowed.")
    return value


def _db(password, schema=None):
    values = {"host": "127.0.0.1", "port": database_port(), "dbname": "aletheia", "user": "aletheia", "password": password}
    if schema is not None:
        values["options"] = "-c search_path=" + _schema(schema)
    return values


async def _worker():
    from psycopg_pool import AsyncConnectionPool

    settings = json.loads(os.environ["K03_RATE_WORKER"])
    policy = RatePolicy(9, settings["subject_limit"], settings["window"])
    async with AsyncConnectionPool(conninfo="", kwargs=_db(os.environ["K03_DB_PASSWORD"], settings["schema"]), min_size=1, max_size=2) as pool:
        gate = AuthAdmission(PostgresAuthAdmission(pool, namespace=settings["namespace"]), policy, timeout_seconds=5)
        allowed = denied = 0
        for index in range(12):
            subject = 1 if settings["subject_limit"] == 3 else 1000 * settings["worker"] + index
            try:
                await gate.check("callback", SecretStr(format(subject, "064x")))
            except AuthRateLimited:
                denied += 1
            else:
                allowed += 1
        return {"allowed": allowed, "denied": denied}


async def _processes(schema, namespace, subject_limit, window):
    def run(worker):
        environment = dict(os.environ)
        environment["K03_RATE_WORKER"] = json.dumps({"schema": schema, "namespace": namespace,
            "subject_limit": subject_limit, "window": window, "worker": worker})
        result = subprocess.run([sys.executable, "-B", str(Path(__file__).resolve()), "--worker"],
            env=environment, capture_output=True, text=True, timeout=60,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=False)
        if result.returncode != 0:
            raise RuntimeError("Isolated rate worker failed.")
        return json.loads(result.stdout)

    values = await asyncio.gather(*(asyncio.to_thread(run, worker) for worker in range(4)))
    assert sum(item["allowed"] for item in values) == (3 if subject_limit == 3 else 9)
    assert sum(item["allowed"] + item["denied"] for item in values) == 48


async def _http(pool, window):
    calls, reads = [], []

    async def business(scope, receive, send):
        calls.append(1)
        await receive()
        reads.append(1)
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    policy = RatePolicy(4, 2, window)
    store = PostgresAuthAdmission(pool, namespace="http")
    await store.prepare(policy)
    assert await store.is_ready(policy)
    gate = AuthAdmission(store, policy, timeout_seconds=2)
    key = SecretStr("synthetic-shared-hmac-key-" + "k" * 43)
    app = EdgeGuard("https://app.example.invalid", AuthAdmissionApplication(business, gate,
        subject_key=key, timeout_seconds=3))
    transports = [httpx.ASGITransport(app=app, client=("192.0.2." + str(index), 12345)) for index in (1, 2)]
    for transport in transports:
        async with httpx.AsyncClient(transport=transport, base_url="https://app.example.invalid") as client:
            for _ in range(2):
                assert (await client.post("/v1/auth/renew", json={})).status_code == 204
            denied = await client.post("/v1/auth/renew", json={}, headers={"x-forwarded-for": "198.51.100.2"})
            assert denied.status_code == 429 and 1 <= int(denied.headers["retry-after"]) <= window
            assert denied.headers["cache-control"] == "no-store" and "set-cookie" not in denied.headers
            assert denied.json()["code"] == "rate_limited"
    assert len(calls) == len(reads) == 4
    print("K03_RATE_DB_HTTP_DENIAL_ZERO_EFFECT=PASS", flush=True)
    async with pool.connection() as connection:
        await connection.execute("ALTER TABLE k03_auth_admission RENAME TO k03_auth_admission_unavailable")
    try:
        assert not await store.is_ready(policy)
        async with httpx.AsyncClient(transport=transports[0], base_url="https://app.example.invalid") as client:
            outage = await client.post("/v1/auth/renew", json={})
            assert outage.status_code == 503 and outage.json()["code"] == "service_unavailable"
            assert len(calls) == len(reads) == 4
        print("K03_RATE_DB_HTTP_MISSING_TABLE_FAILS_CLOSED=PASS", flush=True)
    finally:
        async with pool.connection() as connection:
            await connection.execute("ALTER TABLE k03_auth_admission_unavailable RENAME TO k03_auth_admission")
    assert await store.is_ready(policy)


async def _transaction_checks(pool, window):
    policy = RatePolicy(9, 3, window)
    store = PostgresAuthAdmission(pool, namespace="transaction")
    await store.prepare(policy)
    gate = AuthAdmission(store, policy, timeout_seconds=0.03)
    async with pool.connection() as holder:
        await holder.execute("SELECT namespace FROM k03_auth_admission WHERE namespace='transaction' AND operation='callback' FOR UPDATE")
        with pytest.raises(TimeoutError):
            await gate.check("callback", SecretStr("a" * 64))
    assert (await store.consume("callback", SecretStr("a" * 64), policy)).allowed
    async with pool.connection() as connection:
        row = await (await connection.execute("SELECT attempts FROM k03_auth_admission WHERE namespace='transaction' AND operation='callback'")).fetchone()
        assert row == (1,)
    with pytest.raises(RepositoryDataRejected):
        await store.prepare(RatePolicy(10, 3, window))
    async with pool.connection() as connection:
        await connection.execute("UPDATE k03_auth_admission SET last_seen=clock_timestamp()+interval '60 seconds' "
                                 "WHERE namespace='transaction' AND operation='callback'")
    with pytest.raises(RepositoryDataRejected):
        await store.consume("callback", SecretStr("a" * 64), policy)
    async with pool.connection() as connection:
        row = await (await connection.execute("SELECT attempts FROM k03_auth_admission WHERE namespace='transaction' AND operation='callback'")).fetchone()
        assert row == (1,)
        await connection.execute("UPDATE k03_auth_admission SET last_seen=to_timestamp(0),window_number=-1 "
                                 "WHERE namespace='transaction' AND operation='callback'")
    assert (await store.consume("callback", SecretStr("b" * 64), policy)).allowed
    async with pool.connection() as connection:
        row = await (await connection.execute("SELECT attempts,(SELECT count(*) FROM jsonb_object_keys(subjects)) "
            "FROM k03_auth_admission WHERE namespace='transaction' AND operation='callback'")).fetchone()
        assert row == (1, 1)
    print("K03_RATE_DB_CANCEL_POLICY_CLOCK_AND_WINDOW_RESET=PASS", flush=True)


async def _after_workers(pool, namespace, subject_limit, window):
    policy = RatePolicy(9, subject_limit, window)
    store = PostgresAuthAdmission(pool, namespace=namespace)
    await store.prepare(policy)
    subject = 1 if subject_limit == 3 else 99999
    result = await store.consume("callback", SecretStr(format(subject, "064x")), policy)
    assert result.allowed is False
    async with pool.connection() as connection:
        row = await (await connection.execute("SELECT attempts,(SELECT count(*) FROM jsonb_object_keys(subjects)) "
            "FROM k03_auth_admission WHERE namespace=%s AND operation='callback'", (namespace,))).fetchone()
        assert row == ((3, 1) if subject_limit == 3 else (9, 9))
    assert (await store.consume("renew", SecretStr(format(subject, "064x")), policy)).allowed


async def _natural_boundary(pool):
    policy = RatePolicy(2, 1, 2)
    store = PostgresAuthAdmission(pool, namespace="boundary")
    await store.prepare(policy)
    async with pool.connection() as connection:
        row = await (await connection.execute("SELECT extract(epoch FROM clock_timestamp())")).fetchone()
        wait = 2 - float(row[0]) % 2
    await asyncio.sleep(wait + 0.02)
    assert (await store.consume("callback", SecretStr("a" * 64), policy)).allowed
    assert not (await store.consume("callback", SecretStr("a" * 64), policy)).allowed
    async with pool.connection() as connection:
        first = await (await connection.execute("SELECT window_number FROM k03_auth_admission WHERE namespace='boundary' AND operation='callback'")).fetchone()
    for _ in range(50):
        await asyncio.sleep(0.1)
        async with pool.connection() as connection:
            now = await (await connection.execute("SELECT floor(extract(epoch FROM clock_timestamp())/2)::bigint")).fetchone()
        if now[0] > first[0]:
            break
    else:
        raise RuntimeError("Database window did not advance.")
    assert (await store.consume("callback", SecretStr("a" * 64), policy)).allowed
    print("K03_RATE_DB_NATURAL_WINDOW_ROLLOVER=PASS", flush=True)


async def _live():
    import psycopg
    from psycopg import sql
    from psycopg_pool import AsyncConnectionPool

    schema = _schema("k03_rate_test_" + uuid.uuid4().hex)
    created = False
    async with await psycopg.AsyncConnection.connect(**_db(os.environ["K03_DB_PASSWORD"]), autocommit=True) as admin:
        try:
            await admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
            created = True
            await admin.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
            ddl = Path(__file__).resolve().parents[2] / "src/aletheia_lab/hosted_auth/admission_schema.sql"
            await admin.execute(ddl.read_text(encoding="utf-8"))
            row = await (await admin.execute("SELECT extract(epoch FROM clock_timestamp())")).fetchone()
            epoch = int(row[0])
            window = next(value for value in range(3000, 3601) if value - epoch % value > 120)
            for namespace, subject_limit in (("global", 9), ("subject", 3)):
                await _processes(schema, namespace, subject_limit, window)
                # All worker processes have exited; a new pool must see their counts.
                async with AsyncConnectionPool(conninfo="", kwargs=_db(os.environ["K03_DB_PASSWORD"], schema), min_size=1, max_size=5) as pool:
                    await _after_workers(pool, namespace, subject_limit, window)
            print("K03_RATE_DB_FOUR_PROCESSES_GLOBAL_AND_SUBJECT=PASS", flush=True)
            print("K03_RATE_DB_NEW_PROCESS_POOL_PRESERVES_QUOTA=PASS", flush=True)
            async with AsyncConnectionPool(conninfo="", kwargs=_db(os.environ["K03_DB_PASSWORD"], schema), min_size=1, max_size=5) as pool:
                await _http(pool, window)
                await _transaction_checks(pool, window)
                await _natural_boundary(pool)
            count = await (await admin.execute("SELECT count(*) FROM k03_auth_admission")).fetchone()
            assert count == (25,)
            print("K03_RATE_DB_ROW_AND_SOURCE_CARDINALITY_BOUNDED=PASS", flush=True)
        finally:
            if created:
                await admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(_schema(schema))))
                print("K03_RATE_DB_OWN_SCHEMA_REMOVED=PASS", flush=True)


@pytest.mark.parametrize("name", ["public", "k03_rate_test_x", "x;DROP", "k03_rate_test_" + "a" * 33])
def test_cleanup_cannot_touch_a_non_test_schema(name):
    with pytest.raises(ValueError):
        _schema(name)


def test_cleanup_guard_accepts_only_own_uuid_format():
    name = "k03_rate_test_" + uuid.uuid4().hex
    assert _schema(name) == name


if __name__ == "__main__":
    try:
        if sys.platform == "win32":
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        if sys.argv[1:] == ["--worker"]:
            print(json.dumps(asyncio.run(_worker())), flush=True)
        elif not sys.argv[1:]:
            asyncio.run(_live())
        else:
            raise ValueError("Unknown smoke test mode.")
    except Exception as error:
        print("K03_RATE_DB_CHECK=FAIL:" + type(error).__name__, flush=True)
        frames = traceback.extract_tb(error.__traceback__)
        if frames:
            print("K03_RATE_DB_FAILURE_LINE=" + str(frames[-1].lineno), flush=True)
        sys.exit(1)
