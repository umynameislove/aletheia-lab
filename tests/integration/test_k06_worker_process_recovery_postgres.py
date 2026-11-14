"""Independent workers recover real durable snapshot work across process loss."""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import secrets
import shutil
import signal
import subprocess
import sys
import zipfile
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import SecretStr
from test_k05_native_results_postgres import _case, _run
from test_k06_canonical_snapshot_postgres import _setup

from aletheia_lab.hosted.postgres_jobs import JobClaim

pytestmark = [pytest.mark.integration, pytest.mark.skipif(
    not os.environ.get("K06_TEST_DATABASE_URL"), reason="Explicit K-06 PostgreSQL test URL required")]

@pytest.fixture(autouse=True)
def configured_database(monkeypatch):
    monkeypatch.setenv("K05_TEST_DATABASE_URL", os.environ["K06_TEST_DATABASE_URL"])


_WORKER = r'''
import asyncio,json,os,sys
from pathlib import Path
from pydantic import SecretStr
from psycopg import sql
from psycopg_pool import AsyncConnectionPool
from aletheia_lab.hosted.postgres_jobs import PostgresJobs
from aletheia_lab.hosted.postgres_sources import PostgresSources
from aletheia_lab.hosted.postgres_catalog import PostgresCatalog
from aletheia_lab.hosted.postgres_objects import PostgresObjects
from aletheia_lab.hosted.object_store import FilesystemObjectStore
from aletheia_lab.hosted.upload_snapshot import UploadSnapshot

async def run():
    mode,schema,objects,snapshot_path,claim_path=sys.argv[1:]
    async def configure(conn):
        await conn.execute(sql.SQL('SET search_path TO {}, public').format(sql.Identifier(schema)))
        await conn.commit()
    async with AsyncConnectionPool(os.environ['K06_TEST_DATABASE_URL'],min_size=1,max_size=2,
                                   configure=configure,open=False) as pool:
        sources=PostgresSources(pool,maximum_upload_bytes=10000,maximum_expanded_bytes=20000,maximum_entries=10)
        catalog=PostgresCatalog(pool,PostgresObjects(pool,FilesystemObjectStore(Path(objects))))
        jobs=PostgresJobs(pool,sources,audit=True,catalog=catalog)
        lease=SecretStr(('1' if mode=='claim-loss' else '2')*64)
        claim=await jobs.claim(lease_digest=lease,lease_seconds=1 if mode=='claim-loss' else 120,
                               operations=('confirm',))
        assert claim is not None
        if mode=='claim-loss':
            body={'job_id':claim.job_id,'workspace_id':claim.workspace_id,'operation':claim.operation,
                  'request':claim.request,'credential_digest':claim.credential_digest.get_secret_value()}
            path=Path(claim_path)
            path.write_text(json.dumps(body));path.chmod(0o600)
            os._exit(73)
        ready,release=Path(claim_path).with_suffix('.ready'),Path(claim_path).with_suffix('.release')
        pending=ready.with_suffix('.ready.pending')
        pending.write_text(claim.job_id)
        pending.chmod(0o600)
        pending.replace(ready)
        for _ in range(1000):
            if release.exists():break
            await asyncio.sleep(0.01)
        assert release.exists()
        snapshot=UploadSnapshot.model_validate_json(Path(snapshot_path).read_bytes())
        assert await jobs.complete_snapshot(claim=claim,lease_digest=lease,snapshot=snapshot)
        # Terminate after the durable business effect, before any worker acknowledgement.
        os._exit(74)
with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop if sys.platform=='win32' else None) as runner:
    runner.run(run())
'''


async def _child(mode, schema, tmp_path, snapshot_path, claim_path):
    command = [sys.executable, "-B", "-c", _WORKER, mode, schema,
               str(tmp_path / "native-objects"), str(snapshot_path), str(claim_path)]
    return await asyncio.to_thread(subprocess.run, command, capture_output=True, timeout=45)


def test_expired_worker_takeover_after_effect_process_loss_and_redelivery_keep_one_effect(tmp_path: Path):
    async def scenario():
        async with _case(tmp_path) as case:
            jobs = await _setup(case)
            args = dict(credential_digest=case.a, workspace_id="a", operation="confirm",
                        request_key=SecretStr("k06-separate-worker-recovery"),
                        request={"source_id": case.receipt.source_id,
                                 "mapping": case.snapshot.mapping.model_dump(mode="json")})
            receipt = await jobs.enqueue(**args)
            # No worker was available at admission; reconstructing a worker later sees the job.
            async with case.pool.connection() as conn:
                schema = (await (await conn.execute("SELECT current_schema()")).fetchone())[0]
                assert await (await conn.execute("SELECT state FROM j01_jobs WHERE job_id=%s", (receipt.job_id,))).fetchone() == ("queued",)
            snapshot_path, claim_path = tmp_path / "snapshot.json", tmp_path / "claim.json"
            snapshot_path.write_text(case.snapshot.model_dump_json())
            snapshot_path.chmod(0o600)
            first = await _child("claim-loss", schema, tmp_path, snapshot_path, claim_path)
            assert first.returncode == 73, first.stderr.decode(errors="replace")[-2000:]
            payload = json.loads(claim_path.read_text())
            payload["credential_digest"] = SecretStr(payload["credential_digest"])
            old_claim = JobClaim(**payload)
            assert old_claim.job_id == receipt.job_id
            # Wait for the actual database lease clock, never rewrite the lease to make the test pass.
            for _ in range(40):
                async with case.pool.connection() as conn:
                    expired = (await (await conn.execute("SELECT lease_until<clock_timestamp() FROM j01_jobs WHERE job_id=%s", (receipt.job_id,))).fetchone())[0]
                if expired:
                    break
                await asyncio.sleep(0.05)
            assert expired
            takeover = asyncio.create_task(_child("publish-loss", schema, tmp_path, snapshot_path, claim_path))
            ready, release = claim_path.with_suffix(".ready"), claim_path.with_suffix(".release")
            try:
                for _ in range(500):
                    if ready.exists():
                        break
                    await asyncio.sleep(0.01)
                assert ready.read_text() == receipt.job_id
                # Worker B owns a live lease but has not published. A's stale lease must fail here.
                assert not await jobs.complete_snapshot(claim=old_claim, lease_digest=SecretStr("1" * 64), snapshot=case.snapshot)
                async with case.pool.connection() as conn:
                    assert await (await conn.execute("SELECT state FROM j01_jobs WHERE job_id=%s", (receipt.job_id,))).fetchone() == ("running",)
                    assert await (await conn.execute("SELECT count(*) FROM k05_records WHERE record_type='upload_snapshot'")).fetchone() == (0,)
            finally:
                release.touch(mode=0o600)
                second = await takeover
            assert second.returncode == 74, second.stderr.decode(errors="replace")[-2000:]
            # Redelivered completion from the worker that committed also cannot append an effect.
            assert not await jobs.complete_snapshot(claim=old_claim, lease_digest=SecretStr("2" * 64), snapshot=case.snapshot)
            assert await jobs.claim(lease_digest=SecretStr("3" * 64), lease_seconds=30, operations=("confirm",)) is None
            assert await jobs.enqueue(**args) == receipt
            assert await case.catalog.read_record(credential_digest=case.a, workspace_id="a",
                project_id=case.snapshot.mapping.project_id, record_id=case.snapshot.snapshot_id) == case.snapshot
            async with case.pool.connection() as conn:
                assert await (await conn.execute("SELECT state,result->>'snapshot_id' FROM j01_jobs WHERE job_id=%s", (receipt.job_id,))).fetchone() == ("succeeded", case.snapshot.snapshot_id)
                assert await (await conn.execute("SELECT count(*) FROM k05_records WHERE record_type='upload_snapshot'")).fetchone() == (1,)
                assert await (await conn.execute("SELECT revision FROM k05_projects")).fetchone() == (1,)
                assert await (await conn.execute("SELECT count(*) FROM k06_action_audit WHERE action='job.publish' AND job_id=%s", (receipt.job_id,))).fetchone() == (1,)
                assert await (await conn.execute("SELECT count(*) FROM k06_action_audit WHERE action='job.admit' AND job_id=%s", (receipt.job_id,))).fetchone() == (1,)
    _run(scenario())


def test_scheduler_drains_inflight_real_child_and_leaves_next_durable_job_for_restart(tmp_path: Path):
    from aletheia_lab.hosted.managed_worker import _supervisor_type
    from aletheia_lab.hosted.upload_job_worker import run_one_upload_job, run_upload_scheduler
    from aletheia_lab.hosted.upload_operations import UploadOperations

    async def scenario():
        async with _case(tmp_path) as case:
            jobs = await _setup(case)
            args = dict(credential_digest=case.a, workspace_id="a", operation="preview",
                        request={"source_id": case.receipt.source_id})
            first = await jobs.enqueue(**args, request_key=SecretStr("k06-drain-preview-first"))
            private = tmp_path / "draining-worker"
            private.mkdir()
            supervisor = _supervisor_type()(python=Path(sys.executable).absolute(),
                source_root=Path(__file__).resolve().parents[2] / "src", private_root=private,
                slots=1, timeout_seconds=20)
            operations = UploadOperations(case.sources, supervisor, private_root=private)
            stop, computed, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
            checks = 0

            async def clock():
                nonlocal checks
                checks += 1
                if checks == 2:
                    computed.set()
                    await release.wait()
                return True

            scheduler = asyncio.create_task(run_upload_scheduler(jobs, operations, stop=stop,
                slots=1, lease_seconds=30, idle_seconds=0.05, clock_check=clock))
            try:
                await asyncio.wait_for(computed.wait(), 20)
                stats = case.pool.get_stats()
                assert stats["pool_available"] == stats["pool_size"]
                stop.set()
                second = await jobs.enqueue(**args, request_key=SecretStr("k06-drain-preview-next"))
                release.set()
                await asyncio.wait_for(scheduler, 10)
                async with case.pool.connection() as conn:
                    assert await (await conn.execute("SELECT state FROM j01_jobs WHERE job_id=%s", (first.job_id,))).fetchone() == ("succeeded",)
                    assert await (await conn.execute("SELECT state FROM j01_jobs WHERE job_id=%s", (second.job_id,))).fetchone() == ("queued",)
                assert await run_one_upload_job(jobs, operations, lease_seconds=30)
                async with case.pool.connection() as conn:
                    assert await (await conn.execute("SELECT count(*) FROM j01_jobs WHERE state='succeeded'")).fetchone() == (2,)
                    assert await (await conn.execute("SELECT count(*) FROM k06_action_audit WHERE action='job.complete'")).fetchone() == (2,)
            finally:
                stop.set()
                release.set()
                scheduler.cancel()
                await asyncio.gather(scheduler, return_exceptions=True)
                await supervisor.aclose()
    _run(scenario())


@pytest.mark.skipif(os.name != "posix" or shutil.which("pgrep") is None,
                    reason="Native POSIX SIGTERM and child PID observation required")
def test_managed_sigterm_during_real_compute_drains_and_restart_preserves_backlog(tmp_path: Path):
    import psycopg
    from psycopg import sql
    from test_k06_runtime_privileges_postgres import _role_url

    from aletheia_lab.hosted.object_store import FilesystemObjectStore
    from aletheia_lab.hosted.postgres_catalog import PostgresCatalog
    from aletheia_lab.hosted.postgres_objects import PostgresObjects
    from aletheia_lab.hosted.postgres_sources import PostgresSources
    from aletheia_lab.hosted.runtime_privileges import grant_runtime
    from aletheia_lab.hosted.upload_project import UploadProject
    from aletheia_lab.product.demo_flow import _demo_mapping

    async def scenario():
        database = os.environ["K06_TEST_DATABASE_URL"]
        role = "k06_signal_" + uuid4().hex
        password = secrets.token_urlsafe(32)
        created_role = False
        process = None
        compute_pid = None
        observation = {}
        try:
            async with _case(tmp_path) as case:
                private = tmp_path / "managed-worker"
                private.mkdir()
                case.catalog = PostgresCatalog(case.pool, PostgresObjects(
                    case.pool, FilesystemObjectStore(private / "catalog")))
                jobs = await _setup(case)
                async with case.pool.connection() as conn:
                    schema = (await (await conn.execute("SELECT current_schema()")).fetchone())[0]
                async with await psycopg.AsyncConnection.connect(database, autocommit=True) as admin:
                    await admin.execute(sql.SQL("CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
                        "NOINHERIT NOBYPASSRLS PASSWORD {}").format(sql.Identifier(role), sql.Literal(password)))
                    created_role = True
                async with case.pool.connection() as conn:
                    await grant_runtime(conn, schema, role, profile="worker", audit=True)

                buffer = io.BytesIO()
                with zipfile.ZipFile(buffer, "w") as archive:
                    archive.writestr("dataset.csv", "id,target\n1,0\n2,1\n")
                    archive.writestr("metrics.csv", "run,name,value,step\n" + "".join(
                        f"{'baseline' if index % 2 == 0 else 'candidate'},loss,0.5,{index // 2}\n"
                        for index in range(10000)))
                    archive.writestr("config.json", '{"seed":42}\n')
                payload = buffer.getvalue()
                assert len(payload) < 1024 * 1024
                sources = PostgresSources(case.pool, maximum_upload_bytes=1024 * 1024,
                    maximum_expanded_bytes=2 * 1024 * 1024, maximum_entries=10, catalog=case.catalog)
                source = await sources.put(credential_digest=case.a, workspace_id="a",
                    request_key=SecretStr("k06-managed-signal-source"), payload=payload)
                preparation = tmp_path / "mapping-preparation"
                preparation.mkdir()
                preview = UploadProject(preparation, maximum_upload_bytes=1024 * 1024,
                    maximum_expanded_bytes=2 * 1024 * 1024, maximum_entries=10).preview(
                        payload, captured_at="2026-11-11T00:00:00Z")
                mapping = _demo_mapping(preview)
                mapping["metric_sources"][0]["step_field"] = "step"
                args = dict(credential_digest=case.a, workspace_id="a", operation="confirm",
                            request={"source_id": source.source_id, "mapping": mapping})
                first_args = dict(args, request_key=SecretStr("k06-managed-signal-first"))
                second_args = dict(args, request_key=SecretStr("k06-managed-signal-backlog"))
                first = await jobs.enqueue(**first_args)

                runtime, policy = tmp_path / "runtime.toml", tmp_path / "auth.toml"
                runtime.write_text('environment = "local"\npublic_origin = "https://app.example.invalid"\n'
                    '[identity]\nissuer = "https://identity.example.invalid"\nclient_id = "synthetic"\n'
                    'redirect_uri = "https://app.example.invalid/v1/auth/callback"\nclient_kind = "public"\n'
                    '[storage]\ndatabase_url_env = "ALETHEIA_HOSTED_DATABASE_URL"\n'
                    f'object_root = {json.dumps(str(private))}\nexpected_schema = "{schema}"\n'
                    f'expected_database_role = "{role}"\n'
                    '[worker]\nprocesses = 1\ndatabase_pool_size = 2\njob_slots_per_process = 1\n'
                    'shutdown_grace_seconds = 30\n', encoding="utf-8")
                policy.write_text('default_return_to = "/app"\nreturn_to_paths = ["/app"]\n', encoding="utf-8")
                runtime.chmod(0o600)
                policy.chmod(0o600)
                root = Path(__file__).resolve().parents[2]
                environment = {key: os.environ[key] for key in ("SYSTEMROOT", "WINDIR", "TEMP", "TMP")
                               if key in os.environ}
                environment.update(ALETHEIA_HOSTED_CONFIG=str(runtime), ALETHEIA_HOSTED_AUTH_POLICY=str(policy),
                    ALETHEIA_HOSTED_DATABASE_URL=_role_url(database, schema, role, password),
                    ALETHEIA_HOSTED_ACTION_AUDIT="enabled", PYTHONPATH=str(root / "src"))

                def start():
                    return subprocess.Popen([sys.executable, "-B", "-m", "aletheia_lab.hosted.managed_worker"],
                        cwd=root, env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

                async def stop(child):
                    child.send_signal(signal.SIGTERM)
                    output = await asyncio.wait_for(asyncio.to_thread(child.communicate), 40)
                    assert child.returncode == 0, output[1].decode(errors="replace")[-2000:]

                try:
                    process = start()
                    first_worker_pid = process.pid
                    async with asyncio.timeout(20):
                        while True:
                            assert process.poll() is None, "Managed worker stopped before actual computation"
                            children = await asyncio.to_thread(subprocess.run,
                                [shutil.which("pgrep"), "-P", str(process.pid)], capture_output=True, check=False)
                            materialized = tuple((private / "materializations").rglob("metrics.csv"))
                            if children.returncode == 0 and materialized:
                                compute_pid = int(children.stdout.splitlines()[0])
                                os.kill(compute_pid, 0)
                                async with case.pool.connection() as conn:
                                    assert await (await conn.execute("SELECT state FROM j01_jobs WHERE job_id=%s",
                                        (first.job_id,))).fetchone() == ("running",)
                                break
                            await asyncio.sleep(0.01)
                    # This is the actual process entry point's OS handler, not a stop-event mock.
                    os.kill(compute_pid, 0)
                    process.send_signal(signal.SIGTERM)
                    second = await jobs.enqueue(**second_args)
                    output = await asyncio.wait_for(asyncio.to_thread(process.communicate), 40)
                    assert process.returncode == 0, output[1].decode(errors="replace")[-2000:]
                    observation["first_sigterm_exit_code"] = process.returncode
                    process = None
                    with pytest.raises(ProcessLookupError):
                        os.kill(compute_pid, 0)
                    async with case.pool.connection() as conn:
                        assert await (await conn.execute("SELECT state FROM j01_jobs WHERE job_id=%s",
                            (first.job_id,))).fetchone() == ("succeeded",)
                        assert await (await conn.execute("SELECT state FROM j01_jobs WHERE job_id=%s",
                            (second.job_id,))).fetchone() == ("queued",)

                    process = start()
                    restarted_worker_pid = process.pid
                    async with asyncio.timeout(25):
                        while True:
                            assert process.poll() is None, "Restarted managed worker stopped before backlog completion"
                            async with case.pool.connection() as conn:
                                row = await (await conn.execute("SELECT state FROM j01_jobs WHERE job_id=%s",
                                    (second.job_id,))).fetchone()
                            if row == ("succeeded",):
                                break
                            assert row in (("queued",), ("running",)), row
                            await asyncio.sleep(0.05)
                    await stop(process)
                    observation["restart_sigterm_exit_code"] = process.returncode
                    process = None
                    assert await jobs.enqueue(**first_args) == first
                    assert await jobs.enqueue(**second_args) == second
                    initial = await jobs.output(credential_digest=case.a, workspace_id="a", job_id=first.job_id)
                    repeated = await jobs.output(credential_digest=case.a, workspace_id="a", job_id=second.job_id)
                    snapshot_id = initial["result"]["snapshot_id"]
                    assert repeated["result"]["snapshot_id"] == snapshot_id
                    async with case.pool.connection() as conn:
                        project_id = (await (await conn.execute(
                            "SELECT project_id FROM k05_records WHERE record_id=%s", (snapshot_id,))).fetchone())[0]
                    snapshot = await case.catalog.read_record(credential_digest=case.a, workspace_id="a",
                        project_id=project_id, record_id=snapshot_id)
                    assert snapshot.source_sha256 == hashlib.sha256(payload).hexdigest()
                    assert len(snapshot.mapping_result.metric_observations) == 10000
                    async with case.pool.connection() as conn:
                        assert await (await conn.execute("SELECT revision FROM k05_projects")).fetchone() == (1,)
                        assert await (await conn.execute("SELECT count(*) FROM k05_records WHERE record_type='upload_snapshot'")).fetchone() == (1,)
                        assert await (await conn.execute("SELECT record_id FROM k05_current_pointers WHERE pointer_name='snapshot'")).fetchone() == (snapshot_id,)
                        assert await (await conn.execute("SELECT state,count(*) FROM j01_jobs GROUP BY state")).fetchall() == [("succeeded", 2)]
                        assert await (await conn.execute("SELECT action,count(*) FROM k06_action_audit "
                            "WHERE action IN ('job.admit','job.publish') GROUP BY action ORDER BY action")).fetchall() == [("job.admit", 2), ("job.publish", 2)]
                    observation.update(status="passed", schema=schema, worker_role=role,
                        first_worker_pid=first_worker_pid, compute_child_pid=compute_pid,
                        restarted_worker_pid=restarted_worker_pid, signal="SIGTERM",
                        materialized_metrics_observed_before_signal=True, compute_child_reaped=True,
                        backlog_after_first_shutdown="queued", terminal_states=["succeeded", "succeeded"],
                        job_ids=[first.job_id, second.job_id], same_key_receipts_preserved=True,
                        snapshot_id=snapshot_id, canonical_snapshot_count=1, current_revision=1,
                        admission_audits=2, publication_audits=2, source_sha256=snapshot.source_sha256,
                        source_bytes=len(payload), metric_rows=len(snapshot.mapping_result.metric_observations))
                    for name in ("inputs", "materializations"):
                        assert not (private / name).exists() or not tuple((private / name).iterdir())
                    observation["owned_inputs_and_materializations_cleaned"] = True
                finally:
                    if process is not None:
                        if process.poll() is None:
                            process.kill()
                        await asyncio.to_thread(process.communicate)
        finally:
            if created_role:
                async with await psycopg.AsyncConnection.connect(database, autocommit=True) as admin:
                    assert role.startswith("k06_signal_") and len(role) == 43
                    await admin.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))
                    assert await (await admin.execute("SELECT NOT EXISTS(SELECT 1 FROM pg_namespace WHERE nspname=%s),"
                        "NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname=%s)", (schema, role))).fetchone() == (True, True)
                    observation.update(owned_schema_removed=True, owned_role_removed=True)
        (tmp_path / "managed-sigterm-observation.json").write_text(
            json.dumps(observation, indent=2) + "\n", encoding="utf-8")
    _run(scenario())
