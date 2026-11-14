"""Real graceful/crash restart of an explicitly owned disposable PG16 container."""
from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4

import pytest
from pydantic import SecretStr
from test_k06_idempotency_postgres import _bundle, _digest

from aletheia_lab.hosted.action_audit_migration import migrate as migrate_audit
from aletheia_lab.hosted.action_audit_migration import verify as verify_audit
from aletheia_lab.hosted.postgres_jobs import PostgresJobs
from aletheia_lab.hosted.postgres_sources import PostgresSources
from aletheia_lab.hosted.workspace_migration import migrate as migrate_workspace

ROOT = Path(__file__).resolve().parents[2]
pytestmark = [pytest.mark.integration, pytest.mark.skipif(
    os.environ.get("K06_RECOVERY_ALLOW") != "owned-container-only",
    reason="Explicit permission for an owned disposable PostgreSQL recovery container required")]


def _docker(*arguments):
    outcome = subprocess.run(["docker", *arguments], capture_output=True, text=True, timeout=45, check=False)
    if outcome.returncode != 0:
        raise AssertionError("Owned recovery container command failed; no database details logged.")
    return outcome.stdout.strip()


def _inspect():
    container = os.environ["K06_RECOVERY_CONTAINER_ID"]
    if re.fullmatch(r"[0-9a-f]{64}", container) is None:
        raise AssertionError("An exact Docker container ID is required.")
    expected_name = os.environ["K06_RECOVERY_CONTAINER_NAME"]
    assert re.fullmatch(r"k06_recovery_[0-9a-f]{32}", expected_name)
    # Never inspect Config.Env, which contains the synthetic database password.
    value = _docker("inspect", "--format", '{{json .Id}}|{{json .Name}}|{{json .Config.Labels}}|'
        '{{json .Image}}|{{json .HostConfig.PortBindings}}|{{json .NetworkSettings.Ports}}|'
        '{{json .State}}|{{json .Mounts}}', container)
    identifier, name, labels, image, configured, mapped, state, mounts = map(json.loads, value.split("|"))
    suffix = expected_name.removeprefix("k06_recovery_")
    database = urlsplit(os.environ["K06_RECOVERY_DATABASE_URL"])
    expected_port = int(os.environ["K06_RECOVERY_PORT"])
    assert re.fullmatch(r"[0-9a-f]{32}", suffix)
    assert identifier == container and name == "/" + expected_name
    assert labels.get("aletheia.owner") == "k06" and labels.get("aletheia.run_id") == suffix
    assert image == os.environ["K06_RECOVERY_IMAGE_ID"]
    assert database.scheme == "postgresql" and database.hostname == "127.0.0.1"
    assert database.username == "k06" and database.path == "/k06_recovery"
    assert database.port == expected_port and 1024 < expected_port < 65536 and expected_port != 15432
    binding = [{"HostIp": "127.0.0.1", "HostPort": str(expected_port)}]
    assert configured.get("5432/tcp") == binding
    if state["Running"]:
        assert mapped.get("5432/tcp") == binding
    volume = [mount for mount in mounts if mount["Destination"] == "/var/lib/postgresql/data"]
    assert len(volume) == 1 and volume[0]["Type"] == "volume" and volume[0]["RW"] is True
    assert re.fullmatch(r"[0-9a-f]{64}", volume[0]["Name"])
    return {"id": identifier, "state": state, "volume": volume[0]["Name"], "port": expected_port}


def _ready():
    import psycopg

    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        state = _inspect()
        if state["state"]["Running"]:
            try:
                with psycopg.connect(os.environ["K06_RECOVERY_DATABASE_URL"], connect_timeout=1, autocommit=True) as conn:
                    assert conn.execute("SELECT 1").fetchone() == (1,)
                    return
            except psycopg.OperationalError:
                pass
        time.sleep(0.1)  # Poll readiness, never a race schedule or a durability witness.
    raise AssertionError("Owned PostgreSQL did not become ready within its recovery deadline.")


def _restart(mode):
    before = _inspect()
    assert before["state"]["Running"]
    previous_logs = subprocess.run(["docker", "logs", before["id"]],
        capture_output=True, text=True, timeout=10, check=False)
    assert previous_logs.returncode == 0
    previous_output = previous_logs.stdout + previous_logs.stderr
    if mode == "graceful":
        _docker("restart", "--time", "10", before["id"])
    else:
        assert mode == "crash"
        _docker("kill", "--signal", "KILL", before["id"])
        killed = _inspect()
        assert not killed["state"]["Running"] and killed["state"]["ExitCode"] == 137
        _docker("start", before["id"])
    _ready()
    after = _inspect()
    assert (after["id"], after["volume"], after["port"]) == (before["id"], before["volume"], before["port"])
    logs = subprocess.run(["docker", "logs", before["id"]],
        capture_output=True, text=True, timeout=10, check=False)
    assert logs.returncode == 0
    output = logs.stdout + logs.stderr
    markers = ("database system was interrupted", "automatic recovery in progress", "redo starts at") if mode == "crash" else (
        "database system is shut down",)
    observed = {marker: output.count(marker) - previous_output.count(marker) for marker in markers}
    assert all(count >= 1 for count in observed.values())
    return {"mode": mode, "container_id": after["id"], "same_anonymous_volume": True,
            "port": after["port"], "actual_wal_recovery": mode == "crash", "new_postgres_log_markers": observed}


def _scoped(schema):
    database = urlsplit(os.environ["K06_RECOVERY_DATABASE_URL"])
    query = [(key, value) for key, value in parse_qsl(database.query) if key != "options"]
    query.append(("options", "-csearch_path=" + schema))
    return urlunsplit(database._replace(query=urlencode(query)))


def _repositories(pool):
    sources = PostgresSources(pool, maximum_upload_bytes=10000, maximum_expanded_bytes=20000,
                              maximum_entries=10, audit=True)
    return sources, PostgresJobs(pool, sources, audit=True)


@pytest.mark.parametrize("mode", ["graceful", "crash"])
def test_committed_source_receipt_terminal_job_and_audit_survive_real_postgres_restart(mode):
    import psycopg
    from psycopg import sql
    from psycopg_pool import AsyncConnectionPool

    async def scenario():
        _inspect()
        _ready()
        schema = "k06_recovery_" + uuid4().hex
        scoped = _scoped(schema)
        created = False
        try:
            async with await psycopg.AsyncConnection.connect(os.environ["K06_RECOVERY_DATABASE_URL"], autocommit=True,
                                                            connect_timeout=5) as admin:
                await admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
                created = True
                await admin.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
                for name in ("session", "source", "jobs"):
                    await admin.execute((ROOT / "docs" / f"j01-experimental-{name}-schema.sql").read_text(encoding="utf-8"))
                await admin.execute("INSERT INTO hosted_identity_bindings VALUES "
                    "('synthetic-issuer','analyst','analyst','Synthetic','k06@example.invalid',true,true,0,'ready','wa',null)")
                await admin.execute("INSERT INTO j01_sessions VALUES ('ctx_analyst','analyst','synthetic-csrf','active',"
                    "statement_timestamp(),statement_timestamp(),statement_timestamp(),statement_timestamp()+interval '900seconds',"
                    "statement_timestamp()+interval '28000seconds',0,0)")
                await admin.execute("INSERT INTO j01_session_credentials VALUES (%s,'ctx_analyst','active',0)",
                                    (_digest("analyst").get_secret_value(),))
                await admin.execute("INSERT INTO j01_workspaces VALUES ('wa','Synthetic recovery')")
                await admin.execute("INSERT INTO j01_memberships VALUES ('wa','analyst','owner',true,1)")
            async with await psycopg.AsyncConnection.connect(scoped, connect_timeout=5) as migration:
                assert await migrate_workspace(migration, schema) == "adopted"
                assert await migrate_audit(migration, schema) == "created" and await verify_audit(migration, schema)
            async with AsyncConnectionPool(scoped, min_size=1, max_size=2, open=False) as pool:
                await pool.wait(timeout=10)
                async with pool.connection() as conn:
                    for setting in ("fsync", "synchronous_commit", "full_page_writes"):
                        assert await (await conn.execute("SHOW " + setting)).fetchone() == ("on",)
                    started_before = (await (await conn.execute("SELECT pg_postmaster_start_time()")).fetchone())[0]
                sources, jobs = _repositories(pool)
                source_args = dict(credential_digest=_digest("analyst"), workspace_id="wa",
                                   request_key=SecretStr("recovery-source-key-0001"), payload=_bundle())
                source = await sources.put(**source_args)
                job_args = dict(credential_digest=_digest("analyst"), workspace_id="wa", operation="preview",
                    request_key=SecretStr("recovery-job-key-000001"), request={"source_id": source.source_id})
                receipt = await jobs.enqueue(**job_args)
                lease = SecretStr("6" * 64)
                claim = await jobs.claim(lease_digest=lease, lease_seconds=30)
                assert claim and claim.job_id == receipt.job_id
                assert await jobs.complete(claim=claim, lease_digest=lease, result={"synthetic_effect": "committed-once"})
                async with pool.connection() as conn:
                    assert await (await conn.execute("SELECT count(*) FROM k06_action_audit")).fetchone() == (3,)
                    lsn_before = str((await (await conn.execute("SELECT pg_current_wal_lsn()")).fetchone())[0])
            witness = await asyncio.to_thread(_restart, mode)
            async with AsyncConnectionPool(scoped, min_size=1, max_size=2, open=False) as pool:
                await pool.wait(timeout=10)
                sources, jobs = _repositories(pool)
                assert await sources.put(**source_args) == source
                assert await jobs.enqueue(**job_args) == receipt and receipt.status == "queued"
                assert (await jobs.status(credential_digest=_digest("analyst"), workspace_id="wa",
                                          job_id=receipt.job_id)).status == "succeeded"
                assert (await jobs.output(credential_digest=_digest("analyst"), workspace_id="wa",
                                          job_id=receipt.job_id))["result"] == {"synthetic_effect": "committed-once"}
                async with pool.connection() as conn:
                    assert await (await conn.execute("SELECT count(*) FROM j01_sources")).fetchone() == (1,)
                    assert await (await conn.execute("SELECT count(*) FROM j01_jobs")).fetchone() == (1,)
                    actions = await (await conn.execute("SELECT action,count(*) FROM k06_action_audit GROUP BY action ORDER BY action")).fetchall()
                    assert actions == [("job.admit", 1), ("job.complete", 1), ("source.put", 1)]
                    assert await verify_audit(conn, schema)
                    started_after, recovering, durable_lsn = await (await conn.execute(
                        "SELECT pg_postmaster_start_time(),pg_is_in_recovery(),pg_current_wal_lsn()>=%s::pg_lsn",
                        (lsn_before,))).fetchone()
                    assert started_after > started_before and recovering is False and durable_lsn is True
                    for setting in ("fsync", "synchronous_commit", "full_page_writes"):
                        assert await (await conn.execute("SHOW " + setting)).fetchone() == ("on",)
            witness.update(schema=schema, source_count=1, job_count=1, committed_audit_count=3,
                           same_original_receipt=True, durable_settings_on=True)
            evidence = Path(os.environ["K06_RECOVERY_EVIDENCE_DIR"])
            (evidence / (mode + "-witness.json")).write_text(json.dumps(witness, indent=2) + "\n", encoding="utf-8")
            print("K06 real PostgreSQL " + mode + ": original source/job receipt, terminal output and 3 audit actions survived")
        finally:
            if created and _inspect()["state"]["Running"]:
                async with await psycopg.AsyncConnection.connect(os.environ["K06_RECOVERY_DATABASE_URL"], autocommit=True,
                                                                connect_timeout=5) as cleanup:
                    await cleanup.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop if sys.platform == "win32" else None) as runner:
        runner.run(scenario())
