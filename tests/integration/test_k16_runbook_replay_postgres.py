"""Separate operator replay: real SQL lock/TLS failure, alert, action and recovery."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import secrets
import ssl
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx2
import psycopg
import pytest
import uvicorn
from pydantic import SecretStr
from test_k02_https_edge import _certificate
from test_k05_native_results_postgres import _case, _run
from test_k06_api_shutdown_postgres import ORIGIN, _port
from test_k06_follow_up_http_postgres import _identity

from aletheia_lab.hosted.application import AdapterLimits, HostedApplication
from aletheia_lab.hosted.clock_guarded_backend import ClockGuardedBackend
from aletheia_lab.hosted.edge import EdgeGuard
from aletheia_lab.hosted.observability import (
    ObservedApplication,
    RuntimeTelemetry,
    SafeJsonFormatter,
)
from aletheia_lab.hosted.operational_alerts import AlertEvaluator
from aletheia_lab.hosted.operator_observation import operator_snapshot
from aletheia_lab.hosted.postgres_workspaces import PostgresWorkspaceAccess
from aletheia_lab.hosted.read_backend import ReadOnlyHostedBackend
from aletheia_lab.hosted_auth.csrf import CsrfTokens
from aletheia_lab.hosted_auth.postgres_sessions import PostgresSessionStore
from aletheia_lab.hosted_auth.sessions import ServerSessions
from aletheia_lab.hosted_auth.settings import AuthPolicy
from aletheia_lab.hosted_contract.session_capabilities import Limits

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("K16_TEST_DATABASE_URL"), reason="Explicit owned K16 PostgreSQL required"
    ),
]


def test_operator_follows_local_runbook_from_actual_dependency_lock_to_authorized_recovery(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("K05_TEST_DATABASE_URL", os.environ["K16_TEST_DATABASE_URL"])

    async def scenario():
        observation = {
            "scope": "Local runbook replay: actual TLS sockets, PostgreSQL dependency lock, production read/session/observation boundaries and operator queries. Synthetic preauthenticated identity; no IdP, deployed paging, full ManagedRuntime or load/availability claim."
        }
        observation["started_at"] = datetime.now(UTC).isoformat()
        epoch = time.monotonic()
        telemetry, alerts, records = RuntimeTelemetry(), AlertEvaluator(), []
        formatter = SafeJsonFormatter(telemetry)

        class Capture(logging.Handler):
            def emit(self, record):
                records.append(json.loads(formatter.format(record)))

        logger, handler = logging.getLogger("aletheia.telemetry"), Capture()
        old_level = logger.level
        logger.setLevel(logging.INFO)
        logger.addHandler(handler)
        server, serving = None, None
        try:
            async with _case(tmp_path) as case:
                async with case.pool.connection() as conn:
                    schema = (await (await conn.execute("SELECT current_schema()")).fetchone())[0]
                tokens = CsrfTokens(SecretStr(secrets.token_urlsafe(48)))
                owner = await _identity(case, "a", secrets.token_urlsafe(32), tokens)
                sessions = ServerSessions(
                    PostgresSessionStore(case.pool),
                    AuthPolicy(default_return_to="/app", return_to_paths=("/app",)),
                    timeout_seconds=1,
                    maximum_cookie_bytes=4096,
                )
                backend = ReadOnlyHostedBackend(
                    sessions,
                    PostgresWorkspaceAccess(case.pool, sessions),
                    limits=Limits(
                        question_chars=2000,
                        upload_bundle_bytes=10000,
                        page_max=200,
                        job_poll_min_ms=2000,
                    ),
                )

                async def dependency_ready():
                    try:
                        async with case.pool.connection() as conn:
                            await conn.execute("SET LOCAL lock_timeout='100ms'")
                            await conn.execute("SELECT job_id FROM j01_jobs LIMIT 0")
                        return True
                    except psycopg.errors.LockNotAvailable:
                        return False

                app = EdgeGuard(
                    ORIGIN,
                    ObservedApplication(
                        HostedApplication(
                            ClockGuardedBackend(backend, dependency_ready),
                            limits=AdapterLimits(0.5, 100000, 4096),
                        ),
                        telemetry=telemetry,
                    ),
                )
                cert, key = _certificate(tmp_path)
                key.chmod(0o600)
                port = _port()
                server = uvicorn.Server(
                    uvicorn.Config(
                        app,
                        host="127.0.0.1",
                        port=port,
                        ssl_certfile=str(cert),
                        ssl_keyfile=str(key),
                        lifespan="off",
                        access_log=False,
                        log_config=None,
                        log_level="warning",
                    )
                )
                serving = asyncio.create_task(server.serve())
                async with asyncio.timeout(10):
                    while not server.started:
                        assert not serving.done()
                        await asyncio.sleep(0.01)
                async with httpx2.AsyncClient(
                    base_url=f"https://127.0.0.1:{port}",
                    verify=ssl.create_default_context(cafile=str(cert)),
                    trust_env=False,
                    timeout=2,
                    headers={"host": "app.example.invalid"},
                ) as client:
                    assert (await client.get("/health/ready")).status_code == 200
                    alerts.evaluate(telemetry.snapshot(), observed_at=time.monotonic() - epoch)
                    async with case.pool.connection() as holder:
                        await holder.execute("LOCK TABLE j01_jobs IN ACCESS EXCLUSIVE MODE")
                        failed = [
                            (await client.get("/health/ready")).status_code for _ in range(20)
                        ]
                        assert failed == [503] * 20
                        firing = alerts.evaluate(
                            telemetry.snapshot(), observed_at=time.monotonic() - epoch
                        )
                        assert any(r["code"] == "http_burn" for r in firing["active"])
                        started = time.monotonic()
                        with pytest.raises(psycopg.errors.LockNotAvailable):
                            async with case.pool.connection() as observer:
                                await observer.execute("SET TRANSACTION READ ONLY")
                                await operator_snapshot(observer, schema)
                        observation["operator_sampler_failure"] = {
                            "code": "lock_timeout_unavailable",
                            "reported_as_zero": False,
                            "elapsed_seconds": time.monotonic() - started,
                        }
                        # The operator action is an actual transaction rollback,
                        # releasing the fault, not a fabricated success flag.
                        await holder.rollback()
                    recovered = [(await client.get("/health/ready")).status_code for _ in range(20)]
                    assert recovered == [200] * 20
                    session = await client.get("/v1/session", headers={"cookie": owner["cookie"]})
                    capability = await client.get(
                        "/v1/workspaces/a/capabilities", headers={"cookie": owner["cookie"]}
                    )
                    assert session.status_code == capability.status_code == 200
                    assert session.json()["actor"]["user_id"] == "owner_a"
                    assert (
                        capability.json()["workspace_id"] == "a"
                        and capability.json()["membership"]["role"] == "owner"
                    )
                    resolved = alerts.evaluate(
                        telemetry.snapshot(), observed_at=time.monotonic() - epoch
                    )
                    assert {"code": "http_burn", "state": "resolved"} in resolved["transitions"]
                    receipt = await case.sources.put(
                        credential_digest=case.a,
                        workspace_id="a",
                        request_key=SecretStr("synthetic-k16-runbook-recovery-0001"),
                        payload=case.payload,
                    )
                    actual = await case.sources.read(
                        credential_digest=case.a, workspace_id="a", source_id=receipt.source_id
                    )
                    assert hashlib.sha256(actual).hexdigest() == receipt.sha256
                    async with case.pool.connection() as observer:
                        await observer.execute("SET TRANSACTION READ ONLY")
                        restored_snapshot = await operator_snapshot(observer, schema)
                    observation.update(
                        initial_ready=200,
                        injected_ready_status_counts={"503": 20},
                        operator_action="ROLLBACK of the owned lock-holding transaction",
                        recovered_ready_status_counts={"200": 20},
                        authorized_session_and_capability_verified=True,
                        subsequent_authorized_source_commit_and_whole_hash_verified=True,
                        alert_firing=firing,
                        alert_recovery=resolved,
                        operator_snapshot_after_recovery=restored_snapshot,
                        safe_logs=records,
                    )
                if serving is not None:
                    server.should_exit = True
                    await asyncio.wait_for(serving, 10)
                    serving = None
            async with await psycopg.AsyncConnection.connect(
                os.environ["K16_TEST_DATABASE_URL"]
            ) as check:
                assert await (
                    await check.execute("SELECT to_regnamespace(%s)", (schema,))
                ).fetchone() == (None,)
            observation["owned_schema_absence_verified_after_cleanup"] = True
            observation["finished_at"] = datetime.now(UTC).isoformat()
            rendered = json.dumps(observation, default=str)
            assert owner["cookie"] not in rendered and owner["x-csrf-token"] not in rendered
            evidence = os.environ.get("K16_FOLLOWUP_EVIDENCE_DIR")
            if evidence:
                path = Path(evidence) / "runbook-replay.json"
                with path.open("x") as stream:
                    stream.write(json.dumps(observation, indent=2, default=str) + "\n")
        finally:
            if server is not None:
                server.should_exit = True
            if serving is not None:
                await asyncio.wait_for(serving, 10)
            logger.removeHandler(handler)
            logger.setLevel(old_level)

    _run(scenario())
